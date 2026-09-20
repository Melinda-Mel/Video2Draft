# -*- coding: utf-8 -*-
"""路径与运行环境配置。

设计原则（2026-09-20 重构）：
- 代码目录只用来找代码：`CODE_DIR = Path(__file__).resolve().parent`，不写死任何个人路径。
- 输出目录单独由 `YJCG_OUTPUT_DIR` 决定，默认 `~/Video2Draft`，与代码目录彻底解耦。
- 所有外部可执行文件（ffmpeg / Chrome）都先查环境变量，再查 PATH，最后查常见安装位置。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# ---------- 代码目录（一律由 __file__ 推导，不写死）----------
V2D_DIR = Path(__file__).resolve().parent            # …/tools/v2d
CODE_DIR = V2D_DIR.parent                            # …/tools
REPO_DIR = CODE_DIR.parent                           # 仓库根

# ---------- 输出目录（与代码目录解耦）----------
OUTPUT_DIR = Path(os.environ.get("YJCG_OUTPUT_DIR") or (Path.home() / "Video2Draft")).expanduser()

# ---------- 临时目录 ----------
TMP_DIR = Path(os.environ.get("YJCG_TMP_DIR") or Path(__import__("tempfile").gettempdir()))


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)


# ---------- 可执行文件探测 ----------
def find_executable(env_var: str, names, extra_paths=()) -> str | None:
    """按「环境变量 → PATH → 常见安装位置」顺序找一个可执行文件。"""
    v = os.environ.get(env_var)
    if v and Path(v).exists():
        return v
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for p in extra_paths:
        if Path(p).exists():
            return str(p)
    return None


_FFMPEG_EXTRA = (
    "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg",
    r"C:\ffmpeg\bin\ffmpeg.exe", r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
)
_CHROME_EXTRA_MAC = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
_CHROME_EXTRA_WIN = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def find_ffmpeg() -> str | None:
    return find_executable("YJCG_FFMPEG", ("ffmpeg",), _FFMPEG_EXTRA)


def find_chrome() -> str | None:
    extra = _CHROME_EXTRA_WIN if os.name == "nt" else _CHROME_EXTRA_MAC
    names = ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge")
    return find_executable("YJCG_CHROME", names, extra)


def python_exe() -> str:
    """当前解释器。子进程一律用它，不再依赖任何特定虚拟环境。"""
    return os.environ.get("YJCG_PYTHON") or sys.executable


def whisper_model() -> str:
    return os.environ.get("YJCG_WHISPER_MODEL", os.environ.get("SPH_WHISPER_MODEL", "small"))
