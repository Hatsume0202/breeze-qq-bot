"""日志配置：控制台 + logs/ 下按天滚动的文件。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(log_dir: Path | str, level: str = "INFO") -> logging.Logger:
    """创建（或复用）主日志器，日志文件按天切分、保留 30 天。"""
    logger = logging.getLogger("breeze")
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False
    if logger.handlers:
        # 重复调用时不叠加 handler
        return logger

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    try:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            filename=str(directory / "bot.log"),
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError as exc:
        # 磁盘不可写时退回纯控制台，保证机器人还能跑
        logger.warning("日志文件初始化失败，仅输出到控制台：%s", exc)

    return logger
