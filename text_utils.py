"""文本小工具：消息段提取、按句截断、去重、限频。"""

from __future__ import annotations

import re
import time
from typing import Any

# 句末标点（中英文混排都覆盖）
SENTENCE_ENDS = "。！？!?；;…\n"
# TTS 截断时优先找的停顿点
PAUSE_CHARS = "。！？!?；;，,、 "


def extract_text(message: Any) -> str:
    """从 OneBot11 array 消息里提取所有 text 段，其他段（图片/表情/at）直接忽略。"""
    if isinstance(message, str):
        # 兼容 string 格式上报
        return message
    if not isinstance(message, list):
        return ""
    parts: list[str] = []
    for segment in message:
        if not isinstance(segment, dict):
            continue
        if segment.get("type") != "text":
            continue
        data = segment.get("data") or {}
        piece = data.get("text")
        if isinstance(piece, str) and piece:
            parts.append(piece)
    return "".join(parts)


def truncate_by_sentence(text: str, limit: int) -> str:
    """回复限长：优先在句末标点处截断，找不到句末标点就硬截断并加省略号。"""
    text = (text or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    window = text[:limit]
    for index in range(len(window) - 1, -1, -1):
        if window[index] in SENTENCE_ENDS:
            head = window[: index + 1].strip()
            if head:
                return head
    return window.rstrip() + "…"


def clean_for_tts(text: str) -> str:
    """去掉朗读起来很奇怪的内容：URL、代码块、Markdown 符号、多余空白。"""
    cleaned = re.sub(r"```.*?```", "", text or "", flags=re.S)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"[*#`>_~\[\]|]", "", cleaned)
    cleaned = re.sub(r"[（(][^）)]{0,30}[）)]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def truncate_tts(text: str, limit: int) -> str:
    """TTS 文本限长：优先在句号处截断，其次是逗号/空格，最后才硬截断。"""
    cleaned = clean_for_tts(text)
    if limit <= 0 or len(cleaned) <= limit:
        return cleaned
    window = cleaned[:limit]
    for index in range(len(window) - 1, -1, -1):
        if window[index] in "。！？!?；;":
            head = window[: index + 1].strip()
            if head:
                return head
    for index in range(len(window) - 1, -1, -1):
        if window[index] in PAUSE_CHARS:
            head = window[:index].strip()
            if head:
                return head + "。"
    return window.strip()


class MessageDedup:
    """按 message_id 去重，防止 NapCat 重推导致重复回复。"""

    def __init__(self, ttl_sec: float = 600.0) -> None:
        self.ttl_sec = max(1.0, float(ttl_sec))
        self._seen: dict[str, float] = {}

    def is_duplicate(self, message_id: str) -> bool:
        """返回 True 表示这条消息已经处理过。"""
        now = time.monotonic()
        self._prune(now)
        if message_id in self._seen:
            return True
        self._seen[message_id] = now
        return False

    def _prune(self, now: float) -> None:
        """缓存过大时清理过期项，避免内存无限增长。"""
        if len(self._seen) < 512:
            return
        expired = [key for key, stamp in self._seen.items() if now - stamp > self.ttl_sec]
        for key in expired:
            self._seen.pop(key, None)


class RateLimiter:
    """单用户最小触发间隔，防止同一个人刷屏。"""

    def __init__(self, min_interval_sec: float = 3.0) -> None:
        self.min_interval_sec = max(0.0, float(min_interval_sec))
        self._last: dict[int, float] = {}

    def too_fast(self, user_id: int) -> bool:
        """返回 True 表示距离上次触发太近，应当丢弃这条消息。"""
        now = time.monotonic()
        last = self._last.get(user_id)
        if last is not None and now - last < self.min_interval_sec:
            return True
        self._last[user_id] = now
        return False
