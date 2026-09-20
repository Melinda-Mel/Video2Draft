# -*- coding: utf-8 -*-
"""CI 端到端（真实 YouTube 链接）：clone 后只装仓库依赖，跑出 MD + PNG。

判定分三档，避免把环境问题误报成代码问题：
    PASS    链接下载 → 转写 → MD + PNG 全部成功
    BLOCKED 出口 IP 被 YouTube 反爬拦截（Sign in to confirm you're not a bot / 403 / 需要 cookies）
            —— 机房 IP 的已知限制，本步骤只告警、不判失败，并会重试几次
    FAIL    其它失败（代码/依赖问题），判失败

判定结果一律用 base64 annotation 回传（YTSAY），所以「这轮到底 PASS 还是 BLOCKED」
永远是可查的，不会出现「步骤是绿的但不知道有没有真跑通」的含糊状态。

用法：
    YJCG_OUTPUT_DIR=/tmp/out python tests/ci_e2e_youtube.py
    YJCG_E2E_URL="https://www.youtube.com/watch?v=..."      # 可换链接
    YJCG_E2E_ATTEMPTS=3                                     # 拦截时重试次数
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


def emit_b64(prefix: str, text: str, chunk: int = 300, maxn: int = 8, level: str = "error") -> None:
    """把文本以 base64 分片发成 annotation（非 ASCII/控制符直接塞会被 GitHub 吞掉）。"""
    b = base64.b64encode(text.encode("utf-8", "replace")).decode("ascii")
    parts = [b[i:i + chunk] for i in range(0, len(b), chunk)][-maxn:]
    for n, p in enumerate(parts, 1):
        print(f"::{level}::{prefix}[{n}/{len(parts)}]{p}")


def probe() -> str:
    """自检：这台 runner 上子进程捕获是否正常（历史上出现过 p.stdout 为 None）。"""
    out = {"python": sys.executable, "version": sys.version.split()[0], "platform": sys.platform}
    try:
        p = subprocess.run([sys.executable, "-c", "print('probe-ok')"],
                           capture_output=True, text=True, timeout=60)
        out["pipe_rc"] = p.returncode
        out["pipe_stdout_repr"] = repr(p.stdout)
        out["pipe_stdout_is_none"] = p.stdout is None
    except Exception as e:  # noqa: BLE001
        out["pipe_error"] = f"{type(e).__name__}: {e}"
    return json.dumps(out, ensure_ascii=False, indent=2)


def run_child(cmd: list[str], logfile: Path, env: dict) -> tuple[int, str]:
    """跑子进程并把输出写文件（不用管道：部分 Windows runner 上管道会拿到 None）。"""
    with open(logfile, "w", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                            text=True, timeout=5400, env=env).returncode
    return rc, logfile.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    url = os.environ.get("YJCG_E2E_URL", DEFAULT_URL)
    model = os.environ.get("YJCG_E2E_MODEL", "tiny")
    attempts = int(os.environ.get("YJCG_E2E_ATTEMPTS", "3"))
    out_dir = Path(os.environ.get("YJCG_OUTPUT_DIR", str(ROOT / "_out"))).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, str(ROOT / "tools" / "video2draft.py"), url,
           "--model", model, "--no-push"]
    logfile = out_dir / "youtube_e2e.log"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    print("$ " + " ".join(cmd), flush=True)

    rc, log, md_ok, png_ok, result, verdict, detail = 0, "", False, False, {}, "FAIL", ""
    for attempt in range(1, attempts + 1):
        t0 = time.time()
        rc, log = run_child(cmd, logfile, env)
        print(f"--- 第 {attempt} 次：退出码={rc} 日志={len(log)} 字符 用时={round(time.time() - t0)}s", flush=True)
        print(log[-6000:], flush=True)

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

        if rc == 0 and md_ok:
            verdict = "PASS"
            break
        if any(k in log.lower() for k in BLOCK_MARKERS):
            verdict = "BLOCKED"
            detail = log[-1200:]
            if attempt < attempts:
                wait = 15 * attempt
                print(f"   出口 IP 被反爬拦截（{sys.platform}），{wait}s 后重试…", flush=True)
                time.sleep(wait)
                continue
        else:
            verdict = "FAIL"
            detail = log[-1800:]
        break

    lines = [f"### 真实 YouTube 端到端（{sys.platform}）", "",
             f"- 链接：{url}", f"- 模型：{model}",
             f"- 退出码：{rc} ｜ 日志：{len(log)} 字符", ""]

    if verdict == "PASS":
        lines += [f"- **MD**：✓ `{Path(result['md_path']).name}`（{Path(result['md_path']).stat().st_size} 字节）",
                  f"- **PNG**：{'✓ 已生成（' + str(Path(result['png_path']).stat().st_size) + ' 字节）' if png_ok else '⚠ 未生成'}",
                  "", "**结论：PASS** —— 真实 YouTube 链接跑通并产出 MD" + ("+PNG" if png_ok else "（PNG 缺失）")]
        print(f"::notice::YouTube 端到端 PASS（MD=ok PNG={'ok' if png_ok else 'no'}）")
    elif verdict == "BLOCKED":
        lines += ["- **判定**：⛔ BLOCKED（YouTube 反爬拦截机房 IP，非代码问题）",
                  "", "**结论：环境拦截，未完成真实 YouTube 下载**，不能据此声称该系统已验证。",
                  "→ 需在有登录态的机器上跑：`YJCG_COOKIES_FROM_BROWSER=chrome` 或提供 `YJCG_COOKIE_FILE`。"]
        print("::warning::真实 YouTube 端到端被反爬拦截（机房 IP 限制），未完成真实下载")
    else:
        lines += ["- **判定**：✗ FAIL", "", "```", log[-1500:], "```"]
        print("::error::真实 YouTube 端到端失败（非反爬原因）")
        print(f"::notice::YT-META rc={rc} loglen={len(log)} platform={sys.platform}")
        emit_b64("YTMETA", json.dumps({"rc": rc, "log_len": len(log), "cmd": cmd,
                                       "cwd": os.getcwd(), "out_dir": str(out_dir),
                                       "log_tail_repr": log[-1200:],
                                       "out_files": sorted(p.name for p in out_dir.iterdir())},
                                      ensure_ascii=False, indent=2))
        if not log.strip():
            emit_b64("YTPROBE", probe())   # 子进程一个字都没吐：区分「捕获坏了」和「真没输出」

    # 无论哪种结果都回传可读判定（避免"步骤绿了但不知道真跑通没有"）
    emit_b64("YTSAY", json.dumps({
        "verdict": verdict, "platform": sys.platform, "rc": rc,
        "md_ok": md_ok, "png_ok": png_ok,
        "title": result.get("title"), "author": result.get("author"),
        "md_bytes": Path(result["md_path"]).stat().st_size if md_ok else None,
        "png_bytes": Path(result["png_path"]).stat().st_size if png_ok else None,
        "steps": result.get("steps"), "detail_tail": detail[-600:],
    }, ensure_ascii=False, indent=2), level=("notice" if verdict == "PASS" else "warning"))

    summary(lines)
    if verdict == "PASS":
        return 0 if png_ok else 1
    return 0 if verdict == "BLOCKED" else 1


if __name__ == "__main__":
    sys.exit(main())
