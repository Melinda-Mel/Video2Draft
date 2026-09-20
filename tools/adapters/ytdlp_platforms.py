# -*- coding: utf-8 -*-
"""yt-dlp 适配器：抖音 / B站 / YouTube（以及 X 的兜底）。

要点（2026-09-20 重构）：
- 只用**仓库安装的** yt-dlp：以 `当前解释器 -m yt_dlp` 调用，不碰系统里的其它版本，
  也不再有 Node 中间层或本机解析服务。
- 只拉音频（bestaudio）：转写只需要声音，省掉视频体积与合并步骤。
- 短链自己先展开一次（纯 urllib，跨平台），失败了也照样交给 yt-dlp 试。
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from v2d import config

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
SHORT_HOSTS = ("b23.tv", "youtu.be", "v.douyin.com", "xhslink.com", "t.cn", "dwz.cn")


class YtdlpMissing(RuntimeError):
    pass


def _check_ytdlp() -> None:
    try:
        import yt_dlp  # noqa: F401
    except ImportError as e:  # pragma: no cover - 只在没装时触发
        raise YtdlpMissing(
            "未安装 yt-dlp（下载抖音/B站/YouTube 需要它）。\n"
            "   → 修复：pip install -r requirements.txt   或   pip install yt-dlp"
        ) from e


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def expand_short_url(url: str, max_hop: int = 6) -> str:
    """短链 → 完整链接；任何一步失败都原样返回，绝不阻断主流程。"""
    try:
        op = urllib.request.build_opener(_NoRedirect)
        cur = url
        for _ in range(max_hop):
            host = urllib.parse.urlparse(cur).netloc.lower()
            if not any(h in host for h in SHORT_HOSTS):
                break
            nxt = None
            for method in ("HEAD", "GET"):
                try:
                    req = urllib.request.Request(cur, method=method, headers={"User-Agent": UA})
                    try:
                        resp = op.open(req, timeout=8)
                    except urllib.error.HTTPError as e:
                        resp = e
                    nxt = resp.headers.get("Location")
                except Exception:
                    nxt = None
                if nxt:
                    break
            if not nxt:
                break
            cur = urllib.parse.urljoin(cur, nxt)
        m = re.match(r"https?://(?:www\.)?bilibili\.com/video/(BV[0-9A-Za-z]+)", cur)
        if m:
            cur = f"https://www.bilibili.com/video/{m.group(1)}"
        return cur
    except Exception:
        return url


def _clean_tags(text: str) -> str:
    if not text:
        return ""
    tags = re.findall(r"#([^\s#，,。]+)", text)
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append("#" + t)
    return " ".join(out[:8])


def _fmt_date(raw: str) -> int:
    """yt-dlp 的 upload_date 是 YYYYMMDD，转成时间戳（失败给 0）。"""
    try:
        import datetime
        return int(datetime.datetime.strptime(raw, "%Y%m%d").timestamp())
    except Exception:
        return 0


def fetch(url: str, workdir: str, platform: str = "") -> dict:
    _check_ytdlp()
    url = expand_short_url(url)
    workdir = str(workdir)
    tmpl = str(Path(workdir) / "%(id)s.%(ext)s")

    cookie_args = []
    cookie_file = os.environ.get("YJCG_COOKIE_FILE")
    cookie_browser = os.environ.get("YJCG_COOKIES_FROM_BROWSER")
    if cookie_file and Path(cookie_file).exists():
        cookie_args = ["--cookies", cookie_file]
    elif cookie_browser:
        cookie_args = ["--cookies-from-browser", cookie_browser]

    cmd = [
        config.python_exe(), "-m", "yt_dlp",
        "--no-playlist", "--no-warnings", "--no-progress",
        *cookie_args,
        "-f", "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio/best",
        "-o", tmpl,
        "--no-simulate",
        "--print", "after_move:filepath",
        "--print", "V2DMETA:%(title)s\t%(uploader)s\t%(upload_date)s\t%(description)s",
        url,
    ]
    import subprocess
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    out = p.stdout or ""
    if p.returncode != 0 and "V2DMETA:" not in out:
        tail = (p.stderr or out)[-500:]
        hint = ""
        low = tail.lower()
        if "cookies" in low or "login" in low or "sign in" in low or "not a bot" in low:
            hint = ("\n   → 该视频需要登录态（YouTube 在机房/VPN 出口常触发机器人校验）。"
                    "先在本机浏览器登录 YouTube，然后：\n"
                    "      export YJCG_COOKIES_FROM_BROWSER=chrome     # 或 edge / firefox / safari\n"
                    "      或 export YJCG_COOKIE_FILE=/path/cookies.txt（Netscape 格式）")
        elif "unable to download" in low or "timed out" in low or "connection" in low:
            hint = "\n   → 网络出口可能被拦，稍后重试或换网络。"
        raise RuntimeError(f"yt-dlp 下载失败：{tail}{hint}")

    video_path, meta = None, {}
    for line in out.splitlines():
        if line.startswith("V2DMETA:"):
            parts = line[len("V2DMETA:"):].split("\t")
            meta = {
                "title": (parts[0] if parts else "").strip(),
                "author": (parts[1] if len(parts) > 1 else "").strip(),
                "publish_time": _fmt_date(parts[2] if len(parts) > 2 else ""),
                "description": (parts[3] if len(parts) > 3 else "").strip(),
            }
        elif line.strip() and Path(line.strip()).exists():
            video_path = line.strip()

    if not video_path:
        files = [f for f in Path(workdir).iterdir() if f.is_file() and f.suffix != ".json"]
        video_path = str(files[0]) if files else None
    if not video_path:
        raise RuntimeError("yt-dlp 没有产出音频文件")

    return {
        "platform": platform or "yt-dlp",
        "title": meta.get("title") or Path(video_path).stem,
        "author": meta.get("author", ""),
        "tags": _clean_tags(meta.get("description", "")),
        "publish_time": meta.get("publish_time", 0),
        "video_path": video_path,
        "audio_path": None,
        "text": None,
    }
