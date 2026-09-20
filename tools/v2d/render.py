# -*- coding: utf-8 -*-
"""出图：调用仓库自带的 md2pic（同一解释器），失败绝不拖垮主流程。

契约（重要）：
- 出图失败只返回 (False, None, 原因)，**已经生成的 MD 必须保留**，任务仍算成功；
- 不做任何推送，微信收发由外层（WorkBuddy）负责。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from v2d import config


def md2pic_path() -> Path:
    return config.CODE_DIR / "md2pic.py"


def render(md_path: str, out_png: str | None = None, timeout: int = 300) -> tuple[bool, str | None, str]:
    """MD → PNG。返回 (成功?, png路径, 说明)。"""
    md = Path(md_path)
    if not md.exists():
        return False, None, f"MD 不存在，跳过出图：{md}"
    png = Path(out_png) if out_png else md.with_suffix(".png")

    if not config.find_chrome():
        return False, None, ("没找到 Chrome/Chromium，已跳过出图（MD 已保留）。"
                             "装一个 Chrome，或用环境变量 YJCG_CHROME 指定路径。")

    cmd = [config.python_exe(), str(md2pic_path()), str(md), "-o", str(png)]
    try:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except Exception as e:  # noqa: BLE001
        return False, None, f"出图进程启动失败（{type(e).__name__}），MD 已保留"
    if p.returncode != 0 or not png.exists():
        tail = ((p.stdout or "") + (p.stderr or "")).strip()[-300:]
        return False, None, f"出图失败（退出码 {p.returncode}），MD 已保留：{tail}"
    return True, str(png), f"{png.stat().st_size // 1024} KB"


def available() -> tuple[bool, str]:
    if not md2pic_path().exists():
        return False, "仓库里没有 tools/md2pic.py"
    if not config.find_chrome():
        return False, "未检测到 Chrome/Chromium（出图需要）"
    return True, "就绪"
