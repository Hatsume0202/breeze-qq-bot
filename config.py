"""配置加载：所有可变项都来自 .env 或环境变量。

启动时读取一次，之后全项目只读 Config 实例，避免到处 os.getenv。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（本文件所在目录）
BASE_DIR = Path(__file__).resolve().parent
# 读取 .env；文件不存在时静默跳过，方便纯环境变量部署（如 systemd / 容器）
load_dotenv(BASE_DIR / ".env")


def _get_str(name: str, default: str = "") -> str:
    """取字符串配置，空值回退默认值。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw if raw else default


def _get_int(name: str, default: int) -> int:
    """取整数配置，非法值回退默认值并保持程序可用。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    """取浮点配置，非法值回退默认值。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    """取布尔配置，支持 true/false、1/0、yes/no、on/off。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_ids(name: str) -> frozenset[int]:
    """解析逗号分隔的 QQ 号列表，忽略空白与非法项。"""
    raw = _get_str(name)
    ids: set[int] = set()
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            continue
    return frozenset(ids)


@dataclass(frozen=True)
class Config:
    """全局配置快照。"""

    # ---- NapCat 反向 WebSocket（本程序是服务端）----
    ws_host: str
    ws_port: int
    ws_path: str

    # ---- 安全 ----
    allowed_users: frozenset[int]
    min_interval_sec: float
    dedup_ttl_sec: float

    # ---- 对话 ----
    chat_provider: str
    system_prompt: str
    max_reply_chars: int
    chat_history_turns: int
    chat_temperature: float
    chat_timeout_sec: float
    ollama_base_url: str
    ollama_model: str
    openai_base_url: str
    openai_api_key: str
    openai_model: str

    # ---- TTS ----
    enable_tts: bool
    breeze_api_base: str
    breeze_api_key: str
    breeze_voice_id: str
    breeze_model_id: str
    breeze_language: str
    breeze_output_format: str
    tts_speed: float
    tts_guidance: float
    tts_speaker_boost: bool
    tts_max_chars: int
    tts_concurrency: int
    tts_max_retries: int
    tts_timeout_sec: float

    # ---- 语音文件 ----
    voice_dir: Path
    voice_keep_hours: float
    voice_clean_interval_sec: float

    # ---- 日志 ----
    log_dir: Path
    log_level: str

    @classmethod
    def load(cls) -> "Config":
        """按 .env / 环境变量生成配置对象。"""
        voice_dir = Path(_get_str("VOICE_DIR", "data/voice"))
        if not voice_dir.is_absolute():
            voice_dir = BASE_DIR / voice_dir

        log_dir = Path(_get_str("LOG_DIR", "logs"))
        if not log_dir.is_absolute():
            log_dir = BASE_DIR / log_dir

        return cls(
            ws_host=_get_str("WS_HOST", "127.0.0.1"),
            ws_port=_get_int("WS_PORT", 8080),
            ws_path=_get_str("WS_PATH", "/onebot/v11/ws"),
            allowed_users=_get_ids("ALLOWED_USERS"),
            min_interval_sec=_get_float("MIN_INTERVAL_SEC", 3.0),
            dedup_ttl_sec=_get_float("DEDUP_TTL_SEC", 600.0),
            chat_provider=_get_str("CHAT_PROVIDER", "ollama").lower(),
            system_prompt=_get_str(
                "SYSTEM_PROMPT",
                "你是一个温柔、话不多的 QQ 私聊机器人，用口语化的中文聊天，"
                "回复尽量简短（不超过一百字），不要暴露自己是 AI。",
            ),
            max_reply_chars=_get_int("MAX_REPLY_CHARS", 500),
            chat_history_turns=_get_int("CHAT_HISTORY_TURNS", 10),
            chat_temperature=_get_float("CHAT_TEMPERATURE", 0.8),
            chat_timeout_sec=_get_float("CHAT_TIMEOUT_SEC", 90.0),
            ollama_base_url=_get_str("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            ollama_model=_get_str("OLLAMA_MODEL", "qwen2.5:7b"),
            openai_base_url=_get_str("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            openai_api_key=_get_str("OPENAI_API_KEY"),
            openai_model=_get_str("OPENAI_MODEL", "gpt-4o-mini"),
            enable_tts=_get_bool("ENABLE_TTS", True),
            breeze_api_base=_get_str("BREEZE_API_BASE", "https://api.breeze.blue/v1"),
            breeze_api_key=_get_str("BREEZE_API_KEY"),
            breeze_voice_id=_get_str("BREEZE_VOICE_ID"),
            breeze_model_id=_get_str("BREEZE_MODEL_ID", "breeze-tts-2"),
            breeze_language=_get_str("BREEZE_LANGUAGE", "zh"),
            breeze_output_format=_get_str("BREEZE_OUTPUT_FORMAT", "mp3_24000_64"),
            tts_speed=_get_float("TTS_SPEED", 1.0),
            tts_guidance=_get_float("TTS_GUIDANCE", 1.0),
            tts_speaker_boost=_get_bool("TTS_SPEAKER_BOOST", True),
            tts_max_chars=_get_int("TTS_MAX_CHARS", 80),
            tts_concurrency=max(1, _get_int("TTS_CONCURRENCY", 1)),
            tts_max_retries=max(1, _get_int("TTS_MAX_RETRIES", 3)),
            tts_timeout_sec=_get_float("TTS_TIMEOUT_SEC", 60.0),
            voice_dir=voice_dir,
            voice_keep_hours=_get_float("VOICE_KEEP_HOURS", 6.0),
            voice_clean_interval_sec=_get_float("VOICE_CLEAN_INTERVAL_SEC", 1800.0),
            log_dir=log_dir,
            log_level=_get_str("LOG_LEVEL", "INFO").upper(),
        )

    def summary(self) -> dict:
        """用于启动日志：只暴露非敏感信息，密钥一律打码。"""
        return {
            "ws": f"ws://{self.ws_host}:{self.ws_port}{self.ws_path}",
            "allowed_users": sorted(self.allowed_users),
            "chat_provider": self.chat_provider,
            "max_reply_chars": self.max_reply_chars,
            "tts_enabled": self.enable_tts,
            "voice_id": self.breeze_voice_id or "(未配置)",
            "api_key": "***" if self.breeze_api_key else "(未配置)",
        }
