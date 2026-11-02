"""breeze-qq-bot 入口：收 NapCat 私聊消息 -> 模型回复 -> BreezeBlue TTS -> silk 语音。

启动：python main.py（先按 README 配好 .env，并让 NapCat 反向 WS 连到本程序）
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from pathlib import Path

import httpx

from chat_client import ChatClient, ChatError
from config import Config
from logger_setup import setup_logger
from onebot_ws import OneBotError, OneBotServer
from silk import SilkError, cleanup_voice_dir, mp3_to_silk
from text_utils import MessageDedup, RateLimiter, extract_text, truncate_by_sentence, truncate_tts
from tts_client import TTSError, TTSClient

# 对话彻底失败时的兜底回复
FALLBACK_REPLY = "……脑子突然卡了一下，等我缓过来再跟我说好不好？"


class QQBot:
    """把 OneBot 事件、对话、TTS、语音发送串起来的核心类。"""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.log = logging.getLogger("breeze")
        self.dedup = MessageDedup(cfg.dedup_ttl_sec)
        self.limiter = RateLimiter(cfg.min_interval_sec)

        self.http = httpx.AsyncClient()
        self.chat = ChatClient(cfg, self.log, self.http)
        self.tts = TTSClient(cfg, self.log, self.http)
        self.onebot = OneBotServer(cfg, self.log, self.on_event)

        # TTS 开关；配置不全时自动降级为纯文字
        self.tts_enabled = cfg.enable_tts
        if self.tts_enabled and (not cfg.breeze_api_key or not cfg.breeze_voice_id):
            self.log.error(
                "ENABLE_TTS=true 但 BREEZE_API_KEY / BREEZE_VOICE_ID 未填写，本次运行自动降级为纯文字回复"
            )
            self.tts_enabled = False

    async def run(self, stop_event: asyncio.Event) -> None:
        """准备目录、启动清理任务、开始监听，直到收到退出信号。"""
        self.cfg.voice_dir.mkdir(parents=True, exist_ok=True)
        self.log.info("breeze-qq-bot 启动，配置：%s", self.cfg.summary())
        self._check_allowed_users()

        removed = cleanup_voice_dir(self.cfg.voice_dir, self.cfg.voice_keep_hours)
        if removed:
            self.log.info("启动清理：删除 %d 个过期语音文件", removed)

        cleanup_task = asyncio.create_task(self._cleanup_loop(stop_event))
        try:
            await self.onebot.start(stop_event)
        finally:
            cleanup_task.cancel()
            await self.onebot.shutdown()
            await self.http.aclose()
            self.log.info("breeze-qq-bot 已退出")

    def _check_allowed_users(self) -> None:
        """白名单为空时大声提醒：此时不会回复任何人。"""
        if not self.cfg.allowed_users:
            self.log.warning(
                "!!! ALLOWED_USERS 为空：机器人在拒绝所有人的私聊。请把 QQ 号写进 .env 再重启 !!!"
            )
        else:
            self.log.info("白名单用户：%s", sorted(self.cfg.allowed_users))

    async def _cleanup_loop(self, stop_event: asyncio.Event) -> None:
        """周期性清理 data/voice 里的旧文件。"""
        interval = max(60.0, self.cfg.voice_clean_interval_sec)
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass
            removed = cleanup_voice_dir(self.cfg.voice_dir, self.cfg.voice_keep_hours)
            if removed:
                self.log.info("定时清理：删除 %d 个过期语音文件", removed)

    async def on_event(self, event: dict) -> None:
        """只处理私聊文字消息，其余一律忽略。"""
        if event.get("post_type") != "message":
            return
        if event.get("message_type") != "private":
            return
        try:
            user_id = int(event.get("user_id"))
        except (TypeError, ValueError):
            return

        if user_id not in self.cfg.allowed_users:
            self.log.info("忽略非白名单用户 %s 的消息", user_id)
            return

        message_id = str(event.get("message_id") or "")
        if message_id and self.dedup.is_duplicate(message_id):
            self.log.info("重复消息已忽略 message_id=%s", message_id)
            return

        text = extract_text(event.get("message")).strip()
        if not text:
            self.log.info("用户 %s 只发了非文字消息，忽略", user_id)
            return

        if self.limiter.too_fast(user_id):
            self.log.info("用户 %s 触发间隔不足 %.1fs，丢弃本条", user_id, self.cfg.min_interval_sec)
            return

        await self._handle_message(user_id, text)

    async def _handle_message(self, user_id: int, text: str) -> None:
        """走完一轮：模型回复 -> 截断 -> 语音 / 纯文字。"""
        self.log.info("<- user=%s %s", user_id, text[:120])
        try:
            reply = await self.chat.chat(user_id, text)
        except ChatError as exc:
            self.log.error("对话失败：%s", exc)
            await self._send_text(user_id, FALLBACK_REPLY)
            return

        reply = truncate_by_sentence(reply, self.cfg.max_reply_chars)
        if not reply:
            self.log.warning("回复截断后为空，跳过")
            return
        self.log.info("-> user=%s %s", user_id, reply[:120])

        if self.tts_enabled:
            voice = await self._make_voice(user_id, reply)
            if voice is not None:
                try:
                    await self.onebot.send_private_record(user_id, voice)
                    return
                except OneBotError as exc:
                    self.log.warning("语音发送失败，降级为纯文字：%s", exc)

        await self._send_text(user_id, reply)

    async def _make_voice(self, user_id: int, reply: str) -> Path | None:
        """回复 -> TTS 文本截断 -> mp3 -> silk；任一步失败都返回 None 走纯文字。"""
        tts_text = truncate_tts(reply, self.cfg.tts_max_chars)
        if not tts_text:
            self.log.info("TTS 文本为空，降级为纯文字")
            return None

        try:
            audio = await self.tts.synthesize(tts_text)
        except TTSError as exc:
            self.log.warning("TTS 合成失败，降级为纯文字：%s", exc)
            return None

        self.cfg.voice_dir.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time() * 1000)
        mp3_path = self.cfg.voice_dir / f"{user_id}_{stamp}.mp3"
        silk_path = mp3_path.with_suffix(".silk")
        try:
            mp3_path.write_bytes(audio)
        except OSError as exc:
            self.log.warning("保存 mp3 失败，降级为纯文字：%s", exc)
            return None

        try:
            await mp3_to_silk(mp3_path, silk_path)
        except SilkError as exc:
            self.log.warning("mp3 转 silk 失败，降级为纯文字：%s", exc)
            return None

        self.log.info("语音就绪：%s（%d 字节）", silk_path.name, silk_path.stat().st_size)
        return silk_path

    async def _send_text(self, user_id: int, text: str) -> None:
        """发纯文字，失败只记日志，不抛到事件循环。"""
        try:
            await self.onebot.send_private_text(user_id, text)
        except OneBotError as exc:
            self.log.error("发送文字失败 user=%s：%s", user_id, exc)


def _install_signal_handlers(stop_event: asyncio.Event, logger: logging.Logger) -> None:
    """SIGINT / SIGTERM 触发优雅退出；Windows 下退回 signal.signal。"""

    def _trigger() -> None:
        if not stop_event.is_set():
            logger.info("收到退出信号，准备关闭…")
            stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _trigger)
        except (NotImplementedError, RuntimeError):
            # Windows 事件循环不支持 add_signal_handler
            try:
                signal.signal(sig, lambda *_: _trigger())
            except (ValueError, OSError):
                pass


async def _amain() -> int:
    """异步主流程：建日志、建配置、跑机器人。"""
    cfg = Config.load()
    logger = setup_logger(cfg.log_dir, cfg.log_level)
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event, logger)

    bot = QQBot(cfg)
    try:
        await bot.run(stop_event)
    except Exception:
        logger.exception("主循环异常退出")
        return 1
    return 0


def main() -> int:
    """同步入口，兼容 Windows 的 ProactorEventLoop。"""
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        print("已手动中断。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
