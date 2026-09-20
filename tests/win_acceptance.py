# -*- coding: utf-8 -*-
"""Windows 真机验收：真实 YouTube 链接 → MD + PNG + JSON（一条命令跑完）。

设计目标（对应 2026-09-20 验收要求）：
1. 普通用户 clone 仓库后，按 docs/12 复制几条 PowerShell 就能跑完；
2. **直接复用** tools/video2draft.py，不另起一套实现（本脚本只是调用 + 断言 + 汇总）；
3. **只允许在 Windows 上执行**：非 Windows 立即中止（不下载、不读 cookies、不验收，退出码非 0）；
4. 本地保留完整日志（含路径）便于排查；**可复制的摘要是脱敏版**——
   不含本机路径、用户名、临时日志路径、cookies 路径、API Key 或账号信息。

用法（PowerShell，仅 Windows）：
    python tests\\win_acceptance.py
    python tests\\win_acceptance.py --cookies-from-browser chrome
    python tests\\win_acceptance.py --cookies-file C:\\path\\to\\cookies.txt
    python tests\\win_acceptance.py --model base --keep-video --out D:\\v2d-acceptance

默认**故意清空 DEEPSEEK_API_KEY**：这样一次运行同时验证
「真实 YouTube 出 MD/PNG/JSON」+「没有 Key 时保留原始转写稿」。

退出码：0=PASS，2=BLOCKED（被反爬/要登录态），1=FAIL（真失败），3=不是 Windows。
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
sys.path.insert(0, str(TOOLS))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

DEFAULT_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # Me at the zoo（最短的公开测试视频）
FALLBACK_MARKER = "（自动整理不可用，已退化为原始稿）"

# 「真失败」与「被拦截」要分开：被拦截不是代码问题，是出口 IP / 登录态问题。
BLOCK_MARKERS = (
    "not a bot",
    "sign in to confirm",
    "please sign in",
    "login required",
    "cookies for the authentication",
    "confirm you're not a bot",
)

# 一键成稿里「推送」相关的历史依赖；CLI 路径上出现任何一个都算越界。
PUSH_TOKENS = ("wx-send", "wx_send", "mcp_call", "127.0.0.1:2022", "127.0.0.1:2024")
SCAN_FILES = [
    "tools/video2draft.py",
    "tools/md2pic.py",
    "tools/format_doc.py",
] + [f"tools/v2d/{n}" for n in ("audio.py", "config.py", "render.py", "transcribe.py")]
SCAN_FILES += [f"tools/adapters/{n}" for n in ("ytdlp_platforms.py", "x_twitter.py", "xiaohongshu.py", "shipinhao.py")]

# 脱敏用：把各类绝对路径整体替换掉（顺序敏感，先长后短）
_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:\\[^\s\"'<>|]*"),                                    # C:\Users\xxx\...
    re.compile(r"\\\\[^\s\"'<>|]+"),                                           # \\server\share\...
    re.compile(r"/(?:Users|home|tmp|var|private|opt|mnt|Volumes|Applications)/[^\s\"'<>|]*"),  # /Users/xxx/...
)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _require_windows() -> int | None:
    """非 Windows 立即中止：不下载、不读 cookies、不验收。返回退出码，或 None 表示放行。"""
    if sys.platform == "win32":
        return None
    print("=" * 72)
    print("X 不是 Windows，不能作为 Windows 真机验收结果。")
    print("=" * 72)
    print(f"当前平台：{sys.platform}（本脚本只接受 Windows 10/11 真机）")
    print("已在任何验收动作之前中止：未下载视频、未读取浏览器 Cookies、未运行后续任何步骤。")
    print(r"请在 Windows 上重跑：python tests\win_acceptance.py --cookies-from-browser chrome")
    return 3


def _env_snapshot(extra: dict) -> dict:
    """给子进程的环境：清掉 Key（默认）、注入 UTF-8、带上输出目录与 cookie 设置。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.update(extra)
    return env


def _run_child(cmd: list, env: dict, log_file: Path, timeout: int = 1800) -> tuple:
    """子进程输出**重定向到文件**再读。

    原因：部分 Windows runner / 终端上 subprocess.run(capture_output=True) 会同时给出
    stdout=None、stderr=None，日志拿不到还会把成功误判成失败（CI 上踩过）。
    """
    with open(log_file, "w", encoding="utf-8", errors="replace") as f:
        p = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, env=env,
                           cwd=str(REPO), timeout=timeout)
    return p.returncode, log_file.read_text(encoding="utf-8", errors="replace")


def _parse_result(text: str) -> tuple:
    res, err = None, None
    for line in text.splitlines():
        if line.startswith("@@RESULT@@"):
            try:
                res = json.loads(line[len("@@RESULT@@"):])
            except Exception:  # noqa: BLE001
                pass
        elif line.startswith("@@ERROR@@"):
            try:
                err = json.loads(line[len("@@ERROR@@"):])
            except Exception:  # noqa: BLE001
                pass
    return res, err


def _detect_block(text: str) -> str | None:
    low = text.lower()
    for m in BLOCK_MARKERS:
        if m in low:
            return m
    return None


def _push_scan() -> tuple:
    """静态核验：CLI 链路上没有任何推送依赖（用来支撑「--no-push 天然成立」）。"""
    hits = []
    for rel in SCAN_FILES:
        p = REPO / rel
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8", errors="ignore")
        for tok in PUSH_TOKENS:
            if tok in txt:
                hits.append(f"{rel}: {tok}")
    return hits, SCAN_FILES


def _env_report() -> dict:
    rep = {
        "platform": sys.platform,
        "python": platform.python_version(),
        "ffmpeg": None,
        "chrome_ok": False,
        "yt_dlp": None,
    }
    try:
        from v2d import config
        rep["ffmpeg"] = config.find_ffmpeg()
        rep["chrome_ok"] = bool(config.find_chrome())
    except Exception:  # noqa: BLE001
        pass
    try:
        import yt_dlp
        rep["yt_dlp"] = yt_dlp.version.__version__
    except Exception:  # noqa: BLE001
        pass
    return rep


def _newest(out_dir: Path, suffix: str) -> Path | None:
    files = sorted(out_dir.glob(f"*{suffix}"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


# ---------------------------------------------------------------- 脱敏 --------

def _secrets(cookies_file: str | None) -> list:
    """需要从可外发摘要里抹掉的字符串（长串优先替换，避免子串误伤）。"""
    s = set()
    for k in ("USERNAME", "USER", "LOGNAME", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"):
        v = os.environ.get(k)
        if v:
            s.add(v)
    try:
        s.add(str(Path.home()))
        s.add(Path.home().name)
    except Exception:  # noqa: BLE001
        pass
    if cookies_file:
        s.add(cookies_file)
        s.add(str(Path(cookies_file).parent))
        s.add(Path(cookies_file).name)
    return sorted((x for x in s if x and len(x) >= 3), key=len, reverse=True)


def _scrub_text(text: str, secrets: list) -> str:
    for pat in _PATH_PATTERNS:
        text = pat.sub("<path>", text)
    for sec in secrets:
        text = text.replace(sec, "<redacted>")
    return text


def _scrub(obj, secrets: list):
    if isinstance(obj, dict):
        return {k: _scrub(v, secrets) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v, secrets) for v in obj]
    if isinstance(obj, str):
        return _scrub_text(obj, secrets)
    return obj


def _basename(p) -> str | None:
    """取文件名，兼容 Windows 与 POSIX 两种分隔符（本地跑/跨平台构造测试都要稳）。"""
    if not p:
        return None
    return Path(str(p).replace("\\", "/")).name


def build_share(full: dict, cookies_file: str | None) -> dict:
    """把完整结果压成**可复制发送**的脱敏摘要（不含路径 / 用户名 / cookies / Key）。"""
    share = copy.deepcopy(full)

    env = share.get("env") or {}
    env["ffmpeg"] = _basename(env.get("ffmpeg"))            # 只留文件名，不留目录与用户名
    share["env"] = env

    for key in ("main", "with_key"):
        run = share.get(key)
        if not isinstance(run, dict):
            continue
        # 直接把 log_path 整个删掉：临时目录/文件名都不外发，只留一个布尔标记
        run.pop("log_path", None)
        run.pop("log_file", None)
        run["log_saved_locally"] = True

    share["share_note"] = ("已脱敏：不含本机路径 / 用户名 / 日志绝对路径 / cookies 路径 / API Key / 账号信息。"
                           "含路径的完整版留在本机（见 win_acceptance_result.json），请勿外发。")
    return _scrub(share, _secrets(cookies_file))


# ------------------------------------------------------------ 单次端到端 ------

def run_once(url: str, out_dir: Path, model: str, with_key: bool, cookies_from_browser: str | None,
             cookies_file: str | None, keep_video: bool, tag: str) -> dict:
    """跑一遍真实 YouTube 端到端，返回结论（此处含本机路径，仅供本地排查）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(TOOLS / "video2draft.py"), url, "--model", model, "--no-push"]
    if keep_video:
        cmd.append("--keep-video")

    extra = {"YJCG_OUTPUT_DIR": str(out_dir)}
    if cookies_file:
        extra["YJCG_COOKIE_FILE"] = cookies_file
    elif cookies_from_browser:
        extra["YJCG_COOKIES_FROM_BROWSER"] = cookies_from_browser
    env = _env_snapshot(extra)
    if not with_key:
        env.pop("DEEPSEEK_API_KEY", None)  # 验证「没有 Key 保留原始转写稿」

    before = {p.stat().st_mtime for p in out_dir.glob("*") if p.is_file()}
    log_file = Path(tempfile.gettempdir()) / f"v2d_accept_{tag}.log"
    log(f"运行（{tag}）：{Path(cmd[1]).name} <url> --model {model} --no-push "
        f"{'[带 Key]' if with_key else '[已清空 Key]'}")
    t0 = time.time()
    try:
        rc, text = _run_child(cmd, env, log_file)
    except subprocess.TimeoutExpired:
        return {"tag": tag, "verdict": "FAIL", "rc": None, "reason": "运行超时（>30 分钟）",
                "log_path": str(log_file)}
    cost = round(time.time() - t0)

    res, err = _parse_result(text)
    blocked = _detect_block(text)

    # 只取本次新增/更新的产物，避免误认旧文件
    md = Path(res["md_path"]) if (res and res.get("md_path")) else None
    png = Path(res["png_path"]) if (res and res.get("png_path")) else None
    js = Path(res["json_path"]) if (res and res.get("json_path")) else None
    if md is None:
        cand = _newest(out_dir, ".md")
        md = cand if (cand and cand.stat().st_mtime not in before) else None
    if js is None:
        cand = _newest(out_dir, ".json")
        js = cand if (cand and cand.stat().st_mtime not in before) else None

    out = {
        "tag": tag,
        "rc": rc,
        "cost_s": cost,
        "model": model,
        "deepseek_key_used": bool(with_key),
        "pass_checks": {},
        "log_path": str(log_file),
    }

    if res and res.get("ok"):
        md_text = md.read_text(encoding="utf-8", errors="replace") if (md and md.exists()) else ""
        txt = None
        if res.get("txt_path"):
            p = Path(res["txt_path"])
            txt = p.read_text(encoding="utf-8", errors="replace") if p.exists() else None
        checks = {
            "fetch_ok": res.get("steps", {}).get("fetch") == "ok",
            "transcribe_ok": str(res.get("steps", {}).get("transcribe", "")).startswith(("ok", "skipped")),
            "md_ok": bool(md and md.exists() and md.stat().st_size > 0),
            "png_ok": bool(png and png.exists() and png.stat().st_size > 0),
            "json_ok": bool(js and js.exists() and js.stat().st_size > 0),
            "md_has_fallback_marker": FALLBACK_MARKER in md_text,
            "md_keeps_raw_transcript": bool(txt and txt.strip() and txt.strip() in md_text),
        }
        out.update({
            "verdict": "PASS" if all(checks.values()) else "FAIL",
            "title": res.get("title", ""),
            "author": res.get("author", ""),
            "duration_s": res.get("duration", 0),
            "chars": res.get("chars", 0),
            "md_bytes": md.stat().st_size if (md and md.exists()) else 0,
            "png_bytes": png.stat().st_size if (png and png.exists()) else 0,
            "json_bytes": js.stat().st_size if (js and js.exists()) else 0,
            "pass_checks": checks,
        })
        if out["verdict"] == "FAIL":
            out["reason"] = "以下检查未通过：" + ", ".join(k for k, v in checks.items() if not v)
    elif blocked:
        out.update({"verdict": "BLOCKED", "reason": f"YouTube 反爬/需登录态（命中 {blocked!r}）",
                    "pass_checks": {k: False for k in
                                    ("fetch_ok", "md_ok", "png_ok", "json_ok")}})
    else:
        out.update({"verdict": "FAIL",
                    "reason": (err or {}).get("error") or f"退出码 {rc}",
                    "pass_checks": {k: False for k in
                                    ("fetch_ok", "md_ok", "png_ok", "json_ok")}})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Windows 真机验收：真实 YouTube → MD + PNG + JSON")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"视频链接（默认 {DEFAULT_URL}）")
    ap.add_argument("--model", default="tiny", help="whisper 档位（默认 tiny，快）")
    ap.add_argument("--out", default=None, help=r"输出目录（默认 %%USERPROFILE%%\Video2Draft-acceptance）")
    ap.add_argument("--cookies-from-browser", default=None,
                    help="从本机浏览器读登录态：chrome / edge / firefox")
    ap.add_argument("--cookies-file", default=None,
                    help="Netscape 格式 cookies.txt 的本地路径（.gitignore 已屏蔽，切勿提交）")
    ap.add_argument("--keep-video", action="store_true", help="保留下载的音频文件，便于人工听检")
    ap.add_argument("--also-with-key", action="store_true",
                    help="若本机已配置 DEEPSEEK_API_KEY，再跑一遍「带 Key 整理」对照（可失败，不影响主判定）")
    args = ap.parse_args()

    # ---- 硬闸：不是 Windows 就地拒绝，绝不进入任何验收动作 ----
    guard = _require_windows()
    if guard is not None:
        return guard

    out_dir = Path(args.out) if args.out else (Path.home() / "Video2Draft-acceptance")

    print("=" * 72)
    print("Video2Draft · Windows 真机验收（真实 YouTube 链接 → MD + PNG + JSON）")
    print("=" * 72)
    print(f"仓库目录：{REPO}")
    print(f"输出目录：{out_dir}")

    env_rep = _env_report()
    log("环境：platform=%s python=%s ffmpeg=%s chrome=%s yt_dlp=%s"
        % (env_rep["platform"], env_rep["python"], bool(env_rep["ffmpeg"]),
           env_rep["chrome_ok"], env_rep["yt_dlp"]))
    if not env_rep["ffmpeg"]:
        print("X 没找到 ffmpeg。先跑 install.ps1 装好再来。")
        return 1
    if not env_rep["yt_dlp"]:
        print("X 没装 yt-dlp。先跑：python -m pip install -r requirements.txt")
        return 1

    hits, scanned = _push_scan()
    log(f"--no-push 静态核验：扫描 {len(scanned)} 个 CLI 链路文件，命中推送依赖 {len(hits)} 处")
    if hits:
        print("X CLI 链路上发现了推送依赖，--no-push 不能视为成立：")
        for h in hits:
            print("   " + h)
        return 1

    main_run = run_once(args.url, out_dir, args.model, False,
                        args.cookies_from_browser, args.cookies_file, args.keep_video, "nokey")

    with_key_run = None
    if args.also_with_key and os.environ.get("DEEPSEEK_API_KEY"):
        with_key_run = run_once(args.url, out_dir / "withkey", args.model, True,
                                args.cookies_from_browser, args.cookies_file, args.keep_video, "withkey")
    elif args.also_with_key:
        log("--also-with-key 但环境里没有 DEEPSEEK_API_KEY，跳过对照。")

    full = {
        "suite": "win_acceptance",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "is_windows_real_machine": True,
        "os": {"platform": sys.platform, "python": platform.python_version()},
        "env": env_rep,
        "url": args.url,
        "no_push": {"static_scan_files": len(scanned), "push_dep_hits": hits},
        "main": main_run,
        "with_key": with_key_run,
    }
    full["verdict"] = main_run["verdict"]

    # 本地完整版（含路径，仅供自己排查）
    js_path = out_dir / "win_acceptance_result.json"
    js_path.parent.mkdir(parents=True, exist_ok=True)
    js_path.write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")

    # 可外发脱敏版（JSON 与 base64 用**同一份**）
    share = build_share(full, args.cookies_file)
    share_path = out_dir / "win_acceptance_result_share.json"
    share_path.write_text(json.dumps(share, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("=" * 72)
    print(f"结论：{share['verdict']}")
    for k, v in share.get("main", {}).get("pass_checks", {}).items():
        print(f"  {'OK ' if v else 'X  '} {k}")
    if share.get("main", {}).get("reason"):
        print(f"  原因：{share['main']['reason']}")
    print(f"本机完整结果（含路径，请勿外发）：{js_path}")
    print(f"本机日志（含路径，请勿外发）：{main_run.get('log_path')}")
    print(f"可外发脱敏摘要：{share_path}")
    print("-" * 72)
    print("把下面这一行（脱敏，可直接贴回复核）发回来：")
    line = json.dumps(share, ensure_ascii=False, separators=(",", ":"))
    print(line)
    print("-" * 72)
    print("备用（同一份脱敏摘要的 base64，若上一行被聊天工具截断就用这个）：")
    print("V2DWIN[" + base64.b64encode(line.encode("utf-8")).decode("ascii") + "]")
    print("=" * 72)

    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}[share["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
