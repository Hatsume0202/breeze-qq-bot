"""OneBot11 反向 WebSocket 服务端：接收 NapCat 事件、下发动作。

说明：NapCat 是「客户端」，本程序是「服务端」，监听 127.0.0.1:8080，
NapCat 里配置的反向 WS 地址要填 ws://127.0.0.1:8080/onebot/v11/ws。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

import websockets
from websockets.exceptions import ConnectionClosed


class OneBotError(RuntimeError):
    """调用 OneBot 动作失败。"""


class OneBotServer:
    """监听反向 WS，并把事件回调给上层；同时提供 send_private_* 动作。"""

    def __init__(
        self,
        cfg: Any,
        logger: logging.Logger,
        on_event: Callable[[dict], Awaitable[None]],
    ) -> None:
        self.cfg = cfg
        self.log = logger
        self.on_event = on_event
        self._conn: Any | None = None
        self._server: Any | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ 服务端
    async def start(self, stop_event: asyncio.Event) -> None:
        """启动监听；监听失败按指数退避重试，直到收到退出信号。"""
        delay = 1.0
        while not stop_event.is_set():
            try:
                async with websockets.serve(
                    self._handler,
                    self.cfg.ws_host,
                    self.cfg.ws_port,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=8 * 1024 * 1024,
                    close_timeout=5,
                ) as server:
                    self._server = server
                    self.log.info(
                        "反向 WebSocket 已监听 ws://%s:%d%s ，请在 NapCat 里填这个地址",
                        self.cfg.ws_host,
                        self.cfg.ws_port,
                        self.cfg.ws_path,
                    )
                    delay = 1.0
                    await stop_event.wait()
                    self.log.info("收到退出信号，正在关闭 WebSocket 服务…")
                    break
            except OSError as exc:
                self._server = None
                self.log.error(
                    "WebSocket 监听失败（端口可能被占用）：%s，%.0f 秒后重试", exc, delay
                )
                if await self._sleep_or_stop(stop_event, delay):
                    break
                delay = min(delay * 2, 60.0)

    @staticmethod
    async def _sleep_or_stop(stop_event: asyncio.Event, seconds: float) -> bool:
        """等待若干秒；若期间收到退出信号则提前返回 True。"""
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    def _resolve_path(self, websocket: Any, rest: tuple) -> str:
        """兼容新旧版 websockets：取出请求路径。"""
        if rest:
            return str(rest[0])
        request = getattr(websocket, "request", None)
        path = getattr(request, "path", None)
        if path:
            return str(path)
        return str(getattr(websocket, "path", "/"))

    async def _handler(self, websocket: Any, *rest: Any) -> None:
        """处理一条 NapCat 连接：校验路径、循环收事件、断线清理。"""
        path = self._resolve_path(websocket, rest)
        clean_path = path.split("?", 1)[0]
        if clean_path.rstrip("/") != self.cfg.ws_path.rstrip("/"):
            self.log.warning("拒绝连接：路径 %s 与配置 WS_PATH=%s 不一致", path, self.cfg.ws_path)
            await websocket.close(code=1008, reason="invalid path")
            return

        old = self._conn
        if old is not None and old is not websocket:
            self.log.info("检测到新的 NapCat 连接，关闭旧连接")
            await self._close_quietly(old)

        self._conn = websocket
        self.log.info("NapCat 已连接（%s）", path)
        try:
            async for raw in websocket:
                await self._dispatch(raw)
        except ConnectionClosed as exc:
            self.log.warning("NapCat 连接断开：%s", exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 单条连接异常不影响主进程
            self.log.exception("WebSocket 处理异常：%s", exc)
        finally:
            if self._conn is websocket:
                self._conn = None
            self._fail_pending("连接已断开")
            self.log.info("NapCat 连接已关闭，等待其自动重连…")

    async def _dispatch(self, raw: Any) -> None:
        """解析一条 JSON：echo 是动作响应，post_type 是事件。"""
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            self.log.warning("收到非 JSON 报文，已忽略")
            return
        if not isinstance(data, dict):
            return

        echo = data.get("echo")
        if echo and echo in self._pending:
            future = self._pending[echo]
            if not future.done():
                future.set_result(data)
            return

        if data.get("post_type") == "message":
            # 事件放到独立任务里处理，保证接收循环能继续读到动作响应
            task = asyncio.create_task(self._run_event(data))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_event(self, event: dict) -> None:
        """包装上层回调，异常只记录不抛出。"""
        try:
            await self.on_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.log.exception("处理事件失败：%s", exc)

    def _fail_pending(self, reason: str) -> None:
        """连接断开时让所有等待中的动作立即失败。"""
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(OneBotError(reason))
        self._pending.clear()

    async def _close_quietly(self, websocket: Any) -> None:
        """关闭连接且忽略关闭过程中的错误。"""
        try:
            await websocket.close(code=1001, reason="replaced by new connection")
        except Exception:
            pass

    # ------------------------------------------------------------------ 动作
    async def call_action(
        self,
        action: str,
        params: dict | None = None,
        timeout: float = 20.0,
    ) -> Any:
        """下发 OneBot 动作并等待响应。"""
        websocket = self._conn
        if websocket is None:
            raise OneBotError("NapCat 尚未连接")
        echo = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        payload = {"action": action, "params": params or {}, "echo": echo}
        try:
            await websocket.send(json.dumps(payload, ensure_ascii=False))
            response = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise OneBotError(f"动作 {action} 超时（{timeout:.0f}s）") from exc
        except ConnectionClosed as exc:
            raise OneBotError(f"动作 {action} 发送失败：连接已断开") from exc
        finally:
            self._pending.pop(echo, None)

        retcode = response.get("retcode")
        if response.get("status") == "failed" or (retcode not in (0, None)):
            raise OneBotError(f"动作 {action} 返回失败：retcode={retcode} {response.get('message') or response.get('wording') or ''}")
        return response.get("data")

    async def send_private_text(self, user_id: int, text: str) -> None:
        """发送私聊纯文字。"""
        await self.call_action(
            "send_private_msg",
            {
                "user_id": user_id,
                "message": [{"type": "text", "data": {"text": text}}],
            },
        )

    async def send_private_record(self, user_id: int, file_path: Path) -> None:
        """发送私聊语音（silk），file 用 file:/// 绝对路径。"""
        uri = Path(file_path).resolve().as_uri()
        self.log.info("发送语音 user=%s file=%s", user_id, uri)
        await self.call_action(
            "send_private_msg",
            {
                "user_id": user_id,
                "message": [{"type": "record", "data": {"file": uri}}],
            },
            timeout=30.0,
        )

    # ------------------------------------------------------------------ 退出
    async def shutdown(self) -> None:
        """优雅退出：关闭当前连接、取消事件任务。"""
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        if self._conn is not None:
            await self._close_quietly(self._conn)
            self._conn = None
        self._fail_pending("服务正在退出")
