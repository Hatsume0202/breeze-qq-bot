"""单独验证 BreezeBlue API Key 与音色是否可用：能出 mp3 就说明配置正确。

用法：
    python test_tts.py                 # 用默认测试文本
    python test_tts.py "你好呀，测试一下"  # 自定义文本
产物写到 data/voice/test_tts_<时间戳>.mp3，可用播放器直接听。
"""

from __future__ import annotations

import asyncio
import sys
import time

import httpx

from config import Config
from logger_setup import setup_logger
from silk import SilkError, mp3_to_silk
from tts_client import TTSError, TTSClient

DEFAULT_TEXT = "你好呀，我是用 BreezeBlue 合成的声音，听到这段话就说明配置成功啦。"


async def _run(text: str) -> int:
    cfg = Config.load()
    logger = setup_logger(cfg.log_dir, "DEBUG")
    if not cfg.breeze_api_key:
        logger.error("BREEZE_API_KEY 为空，请先在 .env 里填写")
        return 1
    if not cfg.breeze_voice_id:
        logger.error("BREEZE_VOICE_ID 为空，请先克隆音色拿到 voice_id")
        return 1

    logger.info("目标地址：%s/text-to-speech/%s", cfg.breeze_api_base, cfg.breeze_voice_id)
    logger.info("文本（%d 字）：%s", len(text), text)
    logger.info("output_format=%s model=%s language=%s", cfg.breeze_output_format, cfg.breeze_model_id, cfg.breeze_language)

    cfg.voice_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    mp3_path = cfg.voice_dir / f"test_tts_{stamp}.mp3"
    silk_path = mp3_path.with_suffix(".silk")

    async with httpx.AsyncClient() as http:
        client = TTSClient(cfg, logger, http)
        try:
            audio = await client.synthesize(text)
        except TTSError as exc:
            logger.error("合成失败：%s", exc)
            if exc.status == 402:
                logger.error("→ 余额不足，请到 BreezeBlue 控制台充值")
            elif exc.status == 422:
                logger.error("→ 音色 id 或克隆音频有问题，请重新克隆音色")
            elif exc.status in (401, 403):
                logger.error("→ API Key 无效或没有权限")
            return 1

    mp3_path.write_bytes(audio)
    logger.info("成功！mp3 已保存：%s（%d 字节）", mp3_path, len(audio))
    logger.info("可以用播放器打开听听效果。")

    try:
        await mp3_to_silk(mp3_path, silk_path)
        logger.info("pilk 转码也成功：%s（%d 字节），QQ 语音链路可用", silk_path, silk_path.stat().st_size)
    except SilkError as exc:
        logger.warning("pilk 转码未成功（不影响验证 API）：%s", exc)
        logger.warning("请按 README「安装 pilk」一节装好后再试")
    return 0


def main() -> int:
    """同步入口：解析命令行文本后跑一次异步验证。"""
    text = sys.argv[1].strip() if len(sys.argv) > 1 else DEFAULT_TEXT
    if not text:
        text = DEFAULT_TEXT
    return asyncio.run(_run(text))


if __name__ == "__main__":
    raise SystemExit(main())
