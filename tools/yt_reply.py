#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube 口播稿：yt-dlp 拿元数据 + 只下音轨 → 复用 pipeline 转写。

为什么单独一条路（2026-09-20）：
    make_card 的通用分支走 wxcd 适配器下高清全量视频，一条 shorts 就 64MB，
    机房出口只有 ~380KB/s，光下载就 170s+。转写只需要音频 —— 改用 yt-dlp
    只下 bestaudio（通常 <5MB），下载段从分钟级压到秒级。

用法:
    yt_reply.grab(url)        # 只拿元数据，不下载
    yt_reply.dl_audio(url, dst)  # 只下音轨
"""
import json
import os
import subprocess
import re

YTDLP = os.environ.get("YJCG_YTDLP", "yt-dlp")   # 默认走 PATH；本机装的路径用环境变量指
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _run(args, timeout=90):
    p = subprocess.run([YTDLP, "--no-playlist", "--no-warnings"] + args,
                       capture_output=True, text=True, timeout=timeout)
    return p


def grab(url):
    """yt-dlp --dump-json 拿元数据（一次网络往返，几秒）。"""
    p = _run(["--dump-json", url])
    if not p.stdout.strip():
        raise RuntimeError(f"yt-dlp 没解析到视频：{(p.stderr or '')[-200:]}")
    d = json.loads(p.stdout.strip().splitlines()[0])
    tags = " ".join("#" + re.sub(r"[^\w\u4e00-\u9fff]", "", t)
                    for t in (d.get("tags") or [])[:5] if t)
    return {
        "url": url,
        "id": re.sub(r"[^\w-]", "", d.get("id") or "yt"),
        "title": (d.get("title") or "YouTube 视频").strip(),
        "author": (d.get("uploader") or d.get("channel") or "").strip(),
        "duration": int(d.get("duration") or 0),
        "tags": tags,
    }


def dl_audio(url, dst):
    """只下音轨（m4a 优先），ffmpeg 提音频够用。"""
    if os.path.exists(dst):
        os.remove(dst)
    base = dst.rsplit(".", 1)[0]
    p = _run(["-f", "bestaudio[ext=m4a]/bestaudio",
              "-o", base + ".%(ext)s", url], timeout=300)
    # yt-dlp 可能落成 .webm/.mka 等后缀，找实际产物
    import glob
    got = sorted(glob.glob(base + ".*"))
    got = [g for g in got if not g.endswith((".part", ".ytdl"))]
    if not got or os.path.getsize(got[0]) < 10000:
        raise RuntimeError(f"yt-dlp 没下到音轨：{(p.stderr or '')[-200:]}")
    if got[0] != dst:
        os.replace(got[0], dst)
    return os.path.getsize(dst)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    info = grab(sys.argv[1])
    print(json.dumps(info, ensure_ascii=False, indent=2))
