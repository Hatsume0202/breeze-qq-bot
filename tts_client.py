"""BreezeBlue 官方 TTS 调用：并发限制、429 退避、402/422 不重试。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from config import Config


class TTSError(RuntimeError):
    """TTS 合成失败。status 为 HTTP 状态码（网络错误时为 None）。"""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class TTSClient:
    """把一段文本合成为 mp3 字节流。"""

    def __init__(self, cfg: Config, logger: logging.Logger, http: httpx.AsyncClient) -> None:
        self.cfg = cfg
        self.log = logger
        self.http = http
        self._semaphore = asyncio.Semaphore(max(1, cfg.tts_concurrency))

    async def synthesize(self, text: str) -> bytes:
        """合成语音，返回 mp3 字节；并发由信号量限制。"""
        text = (text or "").strip()
        if not text:
            raise TTSError("TTS 文本为空")
        async with self._semaphore:
            return await self._request_with_retry(text)

    async def _request_with_retry(self, text: str) -> bytes:
        """发请求；只对 429 按 Retry-After 退避重试。"""
        url = f"{self.cfg.breeze_api_base.rstrip('/')}/text-to-speech/{self.cfg.breeze_voice_id}"
        params = {"output_format": self.cfg.breeze_output_format}
        headers = {
            "xi-api-key": self.cfg.breeze_api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload: dict[str, Any] = {
            "text": text,
            "model_id": self.cfg.breeze_model_id,
            "language_code": self.cfg.breeze_language,
            "voice_settings": {
                "speed": self.cfg.tts_speed,
                "guidance_scale": self.cfg.tts_guidance,
                "use_speaker_boost": self.cfg.tts_speaker_boost,
            },
        }

        max_attempts = max(1, self.cfg.tts_max_retries)
        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            try:
                response = await self.http.post(
                    url,
                    params=params,
                    json=payload,
                    headers=headers,
                    timeout=self.cfg.tts_timeout_sec,
                )
            except httpx.HTTPError as exc:
                raise TTSError(f"TTS 网络错误：{exc}") from exc

            status = response.status_code
            if status == 200 and response.content:
                self.log.info("TTS 合成成功：%d 字，%d 字节", len(text), len(response.content))
                return response.content

            if status == 429:
                if attempt >= max_attempts:
                    raise TTSError(f"TTS 被限流（429），已重试 {max_attempts} 次", status=429)
                delay = self._retry_after(response, attempt)
                self.log.warning("TTS 触发限流 429，%.1f 秒后重试（%d/%d）", delay, attempt, max_attempts)
                await asyncio.sleep(delay)
                continue

            if status == 402:
                self.log.error(
                    "!!!!! BreezeBlue 余额不足（HTTP 402），语音功能已不可用，请充值。本条不重试。 !!!!!"
                )
                raise TTSError("BreezeBlue 余额不足（402）", status=402)

            if status == 422:
                snippet = self._snippet(response)
                self.log.error(
                    "TTS 参数/克隆音色异常（HTTP 422），请检查 BREEZE_VOICE_ID、文本内容或克隆音频：%s",
                    snippet,
                )
                raise TTSError(f"TTS 参数或音色异常（422）：{snippet}", status=422)

            if status in (401, 403):
                self.log.error("TTS 鉴权失败（HTTP %d），请检查 BREEZE_API_KEY 是否正确", status)
                raise TTSError(f"TTS 鉴权失败（{status}）", status=status)

            if status == 200:
                raise TTSError("TTS 返回 200 但内容为空", status=200)

            snippet = self._snippet(response)
            self.log.error("TTS 返回异常状态 HTTP %d：%s", status, snippet)
            raise TTSError(f"TTS 返回 HTTP {status}：{snippet}", status=status)

        raise TTSError("TTS 重试次数用尽")

    @staticmethod
    def _retry_after(response: httpx.Response, attempt: int) -> float:
        """优先按 Retry-After 头等待，没有该头就用指数退避。"""
        raw = response.headers.get("Retry-After")
        if raw:
            try:
                return min(max(float(raw.strip()), 0.5), 60.0)
            except ValueError:
                pass
        return min(2.0 ** attempt, 30.0)

    @staticmethod
    def _snippet(response: httpx.Response) -> str:
        """取响应体前 300 字符，避免日志爆掉。"""
        try:
            return response.text[:300]
        except Exception:
            return "<无法读取响应体>"
