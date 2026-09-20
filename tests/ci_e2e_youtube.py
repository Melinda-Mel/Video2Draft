# -*- coding: utf-8 -*-
"""CI 端到端（真实 YouTube 链接）：clone 后只装仓库依赖，跑出 MD + PNG。

判定分三档，避免把环境问题误报成代码问题：
    PASS    链接下载 → 转写 → MD + PNG 全部成功
    BLOCKED 出口 IP 被 YouTube 反爬拦截（Sign in to confirm you're not a bot / 403 / 需要 cookies）
            —— 这是机房 IP 的已知限制，本步骤只告警，不判失败
    FAIL    其它失败（代码/依赖问题），判失败

用法：
    YJCG_OUTPUT_DIR=/tmp/out python tests/ci_e2e_youtube.py
    YJCG_E2E_URL="https://www.youtube.com/watch?v=..."   # 可换链接
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # Me at the zoo（19 秒，公开）

BLOCK_MARKERS = [
    "not a bot",
    "sign in to confirm",
    "cookies",
    "login required",
    "confirm your age",
    "this video is unavailable",
    "video unavailable",
    "http error 403",
    "403 forbidden",
    "unable to extract",
    "failed to extract",
]


def summary(lines: list[str]) -> None:
    dst = os.environ.get("GITHUB_STEP_SUMMARY")
    if dst:
        with open(dst, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def esc(s: str, limit: int = 1200) -> str:
    s = s.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    return s[:limit]


def emit_b64(prefix: str, text: str, chunk: int = 300, maxn: int = 8) -> None:
    """把失败日志以 base64 分片发成 annotation。

    为什么不用原文：ci 日志里混着 \\r 进度条、ANSI 控制符和非 ASCII 字符，
    GitHub 解析 annotation 时会把这些整段吞掉（实测 message 直接变空），
    base64 是纯 ASCII，一定能读回来：
        python3 -c "import base64;print(base64.b64decode('...'))"
    """
    import base64

    b = base64.b64encode(text.encode("utf-8", "replace")).decode("ascii")
    parts = [b[i:i + chunk] for i in range(0, len(b), chunk)][-maxn:]
    for n, p in enumerate(parts, 1):
        print(f"::error::{prefix}[{n}/{len(parts)}]{p}")


def main() -> int:
    url = os.environ.get("YJCG_E2E_URL", DEFAULT_URL)
    model = os.environ.get("YJCG_E2E_MODEL", "tiny")
    out_dir = Path(os.environ.get("YJCG_OUTPUT_DIR", str(ROOT / "_out"))).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, str(ROOT / "tools" / "video2draft.py"), url,
           "--model", model, "--no-push"]
    print("$ " + " ".join(cmd), flush=True)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=5400)
    log = (p.stdout or "") + "\n" + (p.stderr or "")
    print(log[-6000:], flush=True)

    result: dict = {}
    m = re.search(r"@@RESULT@@(\{.*\})", p.stdout or "")
    if m:
        try:
            result = json.loads(m.group(1))
        except Exception:  # noqa: BLE001
            result = {}

    md_path = result.get("md_path")
    png_path = result.get("png_path")
    md_ok = bool(md_path and Path(md_path).exists())
    png_ok = bool(png_path and Path(png_path).exists())

    lines = [f"### 真实 YouTube 端到端（{sys.platform}）", "",
             f"- 链接：{url}", f"- 模型：{model}", ""]

    if p.returncode == 0 and md_ok:
        verdict = "PASS"
        lines += [f"- **MD**：✓ `{Path(md_path).name}`（{Path(md_path).stat().st_size} 字节）",
                  f"- **PNG**：{'✓ `' + Path(png_path).name + '`（' + str(Path(png_path).stat().st_size) + ' 字节）' if png_ok else '⚠ 未生成'}",
                  "", "**结论：PASS** —— 真实 YouTube 链接跑通并产出 MD" + ("+PNG" if png_ok else "（PNG 缺失）")]
        print(f"::notice::YouTube 端到端 PASS（MD={'ok' if md_ok else 'no'} PNG={'ok' if png_ok else 'no'}）")
        summary(lines)
        return 0 if png_ok else 1

    low = log.lower()
    if any(k in low for k in BLOCK_MARKERS):
        lines += ["- **判定**：⛔ BLOCKED（YouTube 反爬拦截数据中心 IP，非代码问题）",
                  "", "**结论：环境拦截，未完成真实 YouTube 下载。**",
                  "→ 需在有登录态的机器上跑：`YJCG_COOKIES_FROM_BROWSER=chrome` 或提供 `YJCG_COOKIE_FILE`。"]
        print("::warning::真实 YouTube 端到端被反爬拦截（机房 IP 限制），不算代码失败")
        summary(lines)
        return 0

    lines += ["- **判定**：✗ FAIL", "", "```", log[-1500:], "```"]
    # 失败详情同时发成 annotation（纯 ASCII 的 base64 分片，公共仓库免登录可读）
    print("::error::真实 YouTube 端到端失败（非反爬原因）")
    emit_b64("YTFAIL", log[-2500:])
    summary(lines)
    return 1


if __name__ == "__main__":
    sys.exit(main())
