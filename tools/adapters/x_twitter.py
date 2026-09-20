# -*- coding: utf-8 -*-
"""X（Twitter）适配器：公开 syndication 接口为主，yt-dlp 兜底。

公开接口：https://cdn.syndication.twimg.com/tweet-result?id=<推文ID>&token=a
- 不需要登录、不需要 API Key；
- 如果接口拿不到直链（字段改动 / 已删 / 私密），自动改用仓库安装的 yt-dlp 下载。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from v2d import config

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADS = {"User-Agent": UA, "Accept": "application/json,text/html,*/*"}
API = "https://cdn.syndication.twimg.com/tweet-result?id={id}&token=a"


def _get(url, timeout=20):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADS), timeout=timeout)


def tweet_id(url: str) -> str:
    m = re.search(r"/(?:status|statuses)/(\d+)", url or "")
    if not m:
        raise RuntimeError("这条链接里没找到推文 ID（请发 x.com/<用户>/status/<数字> 这种形式）")
    return m.group(1)


def clean_text(text: str) -> str:
    t = re.sub(r"https?://t\.co/\S+", "", text or "")
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def make_title(text: str) -> str:
    for line in (text or "").split("\n"):
        s = re.sub(r"#\S+", "", line).strip(" 　·—-，,。！!？?、")
        if len(s) >= 4:
            return s[:40]
    return "X 视频"


def tags_of(text: str) -> str:
    seen, out = set(), []
    for t in re.findall(r"#([^\s#]+)", text or ""):
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append("#" + t)
    return " ".join(out[:8])


def pick_urls(media) -> list:
    """挑 mp4 直链，优先 720p 一档（够清楚又不拖下载）。"""
    var = {}
    for m in (media or []):
        for v in ((m.get("video_info") or {}).get("variants") or []):
            if v.get("content_type") == "video/mp4" and v.get("url"):
                var[int(v.get("bitrate") or 0)] = v["url"]
    if not var:
        return []
    rates = sorted(var)
    best = [r for r in rates if r <= 2176000] or rates[:1]
    order = sorted(best, reverse=True) + sorted(rates, reverse=True)
    out, seen = [], set()
    for r in order:
        if var[r] not in seen:
            seen.add(var[r])
            out.append(var[r])
    return out


def _download(urls, dst: str) -> int:
    last = None
    for u in urls:
        for _ in range(2):
            try:
                with _get(u, timeout=90) as r, open(dst, "wb") as f:
                    while True:
                        chunk = r.read(262144)
                        if not chunk:
                            break
                        f.write(chunk)
                if Path(dst).stat().st_size > 10000:
                    return Path(dst).stat().st_size
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1)
    raise RuntimeError(f"下载失败: {last}")


def _download_ytdlp(url: str, dst: str) -> int:
    """兜底：用仓库安装的 yt-dlp 下载（同样是当前解释器的 -m yt_dlp）。"""
    p = subprocess.run(
        [config.python_exe(), "-m", "yt_dlp", "-f", "mp4/best", "--no-playlist",
         "--no-warnings", "-o", dst, url],
        capture_output=True, text=True, timeout=600,
    )
    if not Path(dst).exists() or Path(dst).stat().st_size < 10000:
        raise RuntimeError(f"yt-dlp 兜底也没拿到：({(p.stdout + p.stderr)[-200:]})")
    return Path(dst).stat().st_size


def fetch(url: str, workdir: str, platform: str = "X") -> dict:
    tid = tweet_id(url)
    canonical = f"https://x.com/i/status/{tid}"
    meta = {}
    try:
        with _get(API.format(id=tid), timeout=25) as r:
            meta = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"X 公开接口没返回数据（{type(e).__name__}）：推文可能已删/私密，"
            f"或本机出网把 x.com 掐了，稍后原样重发即可") from e

    text = clean_text(meta.get("text"))
    user = meta.get("user") or {}
    urls = pick_urls(meta.get("mediaDetails"))

    mp4 = str(Path(workdir) / f"x_{tid}.mp4")
    if urls and urls[0].startswith("https://video.twimg.com"):
        size = _download(urls, mp4)
    else:
        print("   （公开接口没给直链，改用 yt-dlp 兜底）", flush=True)
        size = _download_ytdlp(canonical, mp4)
    print(f"   ✓ X 视频 {size / 1048576:.1f} MB", flush=True)

    return {
        "platform": "X",
        "title": make_title(text) or "X 视频",
        "author": (user.get("name") or user.get("screen_name") or "").strip(),
        "tags": tags_of(text),
        "publish_time": 0,
        "video_path": mp4,
        "audio_path": None,
        "text": None,
        "duration": round((meta.get("video") or {}).get("durationMs", 0) / 1000),
        "source_url": canonical,
    }
