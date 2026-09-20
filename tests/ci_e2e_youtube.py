# -*- coding: utf-8 -*-
"""CI 端到端（真实 YouTube 链接）：clone 后只装仓库依赖，跑出 MD + PNG。

判定分三档，避免把环境问题误报成代码问题：
    PASS    链接下载 → 转写 → MD + PNG 全部成功
    BLOCKED 出口 IP 被 YouTube 反爬拦截（Sign in to confirm you're not a bot / 403 / 需要 cookies）
            —— 这是机房 IP 的已知限制，本步骤只告警，不判失败
    FAIL    其它失败（代码/依赖问题），判失败

诊断设计（踩过的坑）：
    Windows runner 上 `subprocess.run(capture_output=True)` 出现 p.stdout/p.stderr
    同时为 None 的情况，日志完全拿不到。所以这里改成**把子进程输出重定向到文件**
    再读文件，并在日志为空时补一段探针，把“到底发生什么”用 base64 annotation
    带回来（纯 ASCII，不会被 GitHub 解析吞掉）。

用法：
    YJCG_OUTPUT_DIR=/tmp/out python tests/ci_e2e_youtube.py
    YJCG_E2E_URL="https://www.youtube.com/watch?v=..."   # 可换链接
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
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


def emit_b64(prefix: str, text: str, chunk: int = 300, maxn: int = 8) -> None:
    """把文本以 base64 分片发成 annotation（annotation 里塞非 ASCII/控制符会被吞）。"""
    b = base64.b64encode(text.encode("utf-8", "replace")).decode("ascii")
    parts = [b[i:i + chunk] for i in range(0, len(b), chunk)][-maxn:]
    for n, p in enumerate(parts, 1):
        print(f"::error::{prefix}[{n}/{len(parts)}]{p}")


def probe() -> str:
    """自检：这台 runner 上子进程捕获到底正常不正常。"""
    out = {"python": sys.executable, "version": sys.version.split()[0], "platform": sys.platform}
    try:
        p = subprocess.run([sys.executable, "-c", "print('probe-ok')"],
                           capture_output=True, text=True, timeout=60)
        out["pipe_rc"] = p.returncode
        out["pipe_stdout_repr"] = repr(p.stdout)
        out["pipe_stderr_repr"] = repr(p.stderr)
        out["pipe_stdout_is_none"] = p.stdout is None
    except Exception as e:  # noqa: BLE001
        out["pipe_error"] = f"{type(e).__name__}: {e}"
    try:
        tmp = Path(os.environ.get("YJCG_OUTPUT_DIR", ".")) / "_probe.txt"
        with open(tmp, "w", encoding="utf-8") as fh:
            p2 = subprocess.run([sys.executable, "-c", "print('probe-file-ok')"],
                                stdout=fh, stderr=subprocess.STDOUT, text=True, timeout=60)
        out["file_rc"] = p2.returncode
        out["file_content"] = tmp.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        out["file_error"] = f"{type(e).__name__}: {e}"
    return json.dumps(out, ensure_ascii=False, indent=2)


def main() -> int:
    url = os.environ.get("YJCG_E2E_URL", DEFAULT_URL)
    model = os.environ.get("YJCG_E2E_MODEL", "tiny")
    out_dir = Path(os.environ.get("YJCG_OUTPUT_DIR", str(ROOT / "_out"))).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, str(ROOT / "tools" / "video2draft.py"), url,
           "--model", model, "--no-push"]
    logfile = out_dir / "youtube_e2e.log"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

    print("$ " + " ".join(cmd), flush=True)
    t0 = time.time()
    # 关键：输出重定向到文件，不用管道（管道在部分 Windows runner 上会拿到 None）
    with open(logfile, "w", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                            text=True, timeout=5400, env=env).returncode
    log = logfile.read_text(encoding="utf-8", errors="replace")
    print(f"退出码={rc} 日志长度={len(log)} 用时={round(time.time() - t0)}s", flush=True)
    print(log[-6000:], flush=True)

    result: dict = {}
    m = re.search(r"@@RESULT@@(\{.*\})", log)
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
             f"- 链接：{url}", f"- 模型：{model}",
             f"- 退出码：{rc} ｜ 日志：{len(log)} 字符", ""]

    if rc == 0 and md_ok:
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
    print("::error::真实 YouTube 端到端失败（非反爬原因）")
    print(f"::notice::YT-META rc={rc} loglen={len(log)} platform={sys.platform}")
    emit_b64("YTMETA", json.dumps({"rc": rc, "log_len": len(log), "cmd": cmd,
                                   "cwd": os.getcwd(), "out_dir": str(out_dir),
                                   "log_tail_repr": log[-1200:],
                                   "out_files": sorted(p.name for p in out_dir.iterdir())},
                                  ensure_ascii=False, indent=2))
    if not log.strip():
        # 子进程一个字都没吐出来：把探针结果带回来，区分「捕获坏了」还是「子进程真没输出」
        emit_b64("YTPROBE", probe())
    summary(lines)
    return 1


if __name__ == "__main__":
    sys.exit(main())
