"""mp3 -> silk 转码（pilk）与语音文件清理。"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import subprocess
import sys
import time
from pathlib import Path

# 语音目录里会出现的后缀
VOICE_SUFFIXES = {".mp3", ".silk", ".amr"}


class SilkError(RuntimeError):
    """pilk 转码失败或未安装。"""


def _pilk_command(mp3_path: Path, silk_path: Path) -> list[str]:
    """按优先级找 pilk：命令行入口 -> python -m pilk。"""
    cli = shutil.which("pilk")
    if cli:
        return [cli, str(mp3_path), str(silk_path), "--tencent"]
    if importlib.util.find_spec("pilk") is not None:
        return [sys.executable, "-m", "pilk", str(mp3_path), str(silk_path), "--tencent"]
    raise SilkError("未找到 pilk，请先执行 pip install pilk，或参考 README「安装 pilk」手动编译")


def _run_pilk(mp3_path: Path, silk_path: Path) -> None:
    """同步执行 pilk（--tencent 出 QQ 可用的 silk）。"""
    cmd = _pilk_command(mp3_path, silk_path)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SilkError(f"调用 pilk 失败：{exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        raise SilkError(f"pilk 转码失败（返回码 {proc.returncode}）：{detail}")
    if not silk_path.exists() or silk_path.stat().st_size == 0:
        detail = (proc.stdout or proc.stderr or "").strip()[:300]
        raise SilkError(f"pilk 未生成有效 silk 文件：{detail}")


async def mp3_to_silk(mp3_path: Path | str, silk_path: Path | str) -> Path:
    """异步转码：把阻塞的 subprocess 放到线程池里跑。"""
    mp3_path = Path(mp3_path)
    silk_path = Path(silk_path)
    if not mp3_path.exists() or mp3_path.stat().st_size == 0:
        raise SilkError(f"mp3 文件不存在或为空：{mp3_path}")
    silk_path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_run_pilk, mp3_path, silk_path)
    return silk_path


def cleanup_voice_dir(directory: Path | str, keep_hours: float) -> int:
    """删除超过保留时间的语音文件，返回删除数量。"""
    directory = Path(directory)
    if not directory.exists():
        return 0
    deadline = time.time() - max(0.1, float(keep_hours)) * 3600
    removed = 0
    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() not in VOICE_SUFFIXES:
            continue
        try:
            if path.stat().st_mtime < deadline:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
