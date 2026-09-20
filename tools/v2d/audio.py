# -*- coding: utf-8 -*-
"""音频处理：ffmpeg 检测 + 抽取 16k 单声道 wav。

用户可见文案一律中文，并且明确告诉对方「装什么、怎么装」。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from v2d import config

FFMPEG_HINT = (
    "没有找到 ffmpeg（提取音频要用它）。\n"
    "   → macOS：brew install ffmpeg\n"
    "   → Windows：winget install Gyan.FFmpeg   或到 ffmpeg.org 下载后把 bin 目录加进 PATH\n"
    "   → Linux：sudo apt install ffmpeg\n"
    "   也可以用环境变量 YJCG_FFMPEG 直接指定 ffmpeg 的完整路径。"
)


def require_ffmpeg() -> str:
    exe = config.find_ffmpeg()
    if not exe:
        raise RuntimeError(FFMPEG_HINT)
    return exe


def extract_audio(video: str, wav: str, ffmpeg: str | None = None) -> str:
    exe = ffmpeg or require_ffmpeg()
    Path(wav).parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-y", "-i", video,
             "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav],
            check=True, timeout=1800,
        )
    except FileNotFoundError as e:  # ffmpeg 路径无效
        raise RuntimeError(FFMPEG_HINT) from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"ffmpeg 提取音频失败（退出码 {e.returncode}）。"
            "如果是视频文件损坏或格式特殊，可先手动确认文件能否播放。"
        ) from e
    return wav


def have_ffmpeg() -> bool:
    return bool(config.find_ffmpeg())


def ffmpeg_version() -> str:
    exe = config.find_ffmpeg()
    if not exe:
        return "未安装"
    try:
        p = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=20)
        return (p.stdout or "").splitlines()[0][:80] or "已安装"
    except Exception:  # noqa: BLE001
        return "已安装（版本读取失败）"
