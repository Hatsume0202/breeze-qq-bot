"""对话层：一个 chat 接口，两种 provider（ollama / OpenAI 兼容），按 .env 切换。"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any, Deque, Dict

import httpx

from config import Config


class ChatError(RuntimeError):
    """对话接口调用失败（网络、HTTP 状态、空回复等）。"""


class ChatClient:
    """维护每个用户的短上下文，并调用配置好的模型后端。"""

    def __init__(self, cfg: Config, logger: logging.Logger, http: httpx.AsyncClient) -> None:
        self.cfg = cfg
        self.log = logger
        self.http = http
        self.provider = cfg.chat_provider
        # 每用户保存 user/assistant 各 N 条，maxlen=0 表示不带上下文
        self._history: Dict[int, Deque[dict]] = {}

    async def chat(self, user_id: int, text: str) -> str:
        """输入用户一句话，返回模型回复（未截断，由上层限长）。"""
        messages = self._build_messages(user_id, text)
        if self.provider == "ollama":
            reply = await self._ask_ollama(messages)
        elif self.provider in {"openai", "openai-compatible", "compatible"}:
            reply = await self._ask_openai(messages)
        else:
            raise ChatError(f"未知 CHAT_PROVIDER：{self.provider}（只支持 ollama / openai）")

        reply = (reply or "").strip()
        if not reply:
            raise ChatError("模型返回了空内容")

        history = self._history_for(user_id)
        if history.maxlen:
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": reply})
        return reply

    def _history_for(self, user_id: int) -> Deque[dict]:
        """取用户的历史队列，首次访问时创建。"""
        if user_id not in self._history:
            turns = max(0, self.cfg.chat_history_turns)
            self._history[user_id] = deque(maxlen=turns * 2)
        return self._history[user_id]

    def _build_messages(self, user_id: int, text: str) -> list[dict]:
        """拼装 system + 历史 + 本轮用户消息。"""
        messages: list[dict] = [{"role": "system", "content": self.cfg.system_prompt}]
        messages.extend(list(self._history_for(user_id)))
        messages.append({"role": "user", "content": text})
        return messages

    async def _ask_ollama(self, messages: list[dict]) -> str:
        """调用本地 ollama 的 /api/chat。"""
        url = self.cfg.ollama_base_url.rstrip("/") + "/api/chat"
        payload = {
            "model": self.cfg.ollama_model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": self.cfg.chat_temperature},
        }
        try:
            response = await self.http.post(url, json=payload, timeout=self.cfg.chat_timeout_sec)
        except httpx.HTTPError as exc:
            raise ChatError(f"连接 ollama 失败：{exc}") from exc
        if response.status_code != 200:
            raise ChatError(f"ollama 返回 HTTP {response.status_code}：{response.text[:200]}")
        try:
            data: Any = response.json()
        except ValueError as exc:
            raise ChatError("ollama 返回了非 JSON 内容") from exc
        message = data.get("message") or {}
        return str(message.get("content") or "")

    async def _ask_openai(self, messages: list[dict]) -> str:
        """调用任意 OpenAI 兼容的 /chat/completions。"""
        if not self.cfg.openai_api_key:
            raise ChatError("CHAT_PROVIDER=openai 但未配置 OPENAI_API_KEY")
        url = self.cfg.openai_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.cfg.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.cfg.openai_model,
            "messages": messages,
            "temperature": self.cfg.chat_temperature,
        }
        try:
            response = await self.http.post(
                url, json=payload, headers=headers, timeout=self.cfg.chat_timeout_sec
            )
        except httpx.HTTPError as exc:
            raise ChatError(f"连接对话 API 失败：{exc}") from exc
        if response.status_code != 200:
            raise ChatError(f"对话 API 返回 HTTP {response.status_code}：{response.text[:200]}")
        try:
            data: Any = response.json()
        except ValueError as exc:
            raise ChatError("对话 API 返回了非 JSON 内容") from exc
        choices = data.get("choices") or []
        if not choices:
            return ""
        return str((choices[0].get("message") or {}).get("content") or "")

    def reset_history(self, user_id: int) -> None:
        """清空某个用户的上下文。"""
        self._history.pop(user_id, None)
