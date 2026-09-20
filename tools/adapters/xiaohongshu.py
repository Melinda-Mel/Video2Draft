# -*- coding: utf-8 -*-
"""小红书适配器：纯 Python 解析分享页，不依赖任何本地服务或第三方下载器。

为什么自己解析（2026-09-20 实测）：通用下载器读的是老结构
`__INITIAL_STATE__.note.noteDetailMap`，小红书已改版为
`__INITIAL_STATE__.noteData.data.noteData`，所以这里自己读页面。
跨平台：只用标准库（urllib + re + json），Mac / Windows / Linux 行为一致。
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
HEADS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*",
         "Accept-Language": "zh-CN,zh;q=0.9"}


def _get(url, timeout=20):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADS), timeout=timeout)


def _full_url(short: str) -> str:
    for _ in range(3):
        try:
            u = _get(short, timeout=15).geturl()
            if u and u != short:
                return u
        except Exception:
            time.sleep(1)
    return short


def parse_note(html: str) -> dict:
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not m:
        raise RuntimeError("页面里没有 __INITIAL_STATE__（多半被风控拦了，稍后原样重发）")
    d = json.loads(m.group(1).replace("undefined", "null"))
    nd = d.get("noteData") or {}
    note = ((nd.get("data") or {}).get("noteData")) or (nd.get("normalNotePreloadData") or {})
    if not note:
        raise RuntimeError("noteData 里没有笔记详情（小红书又改结构了，去看 parse_note）")
    return note


def pick_video(note: dict) -> list:
    med = ((note.get("video") or {}).get("media") or {}).get("stream") or {}
    cands = []
    for codec in ("h264", "h265", "av1"):
        for it in (med.get(codec) or []):
            if it.get("masterUrl"):
                cands.append(it["masterUrl"])
            for b in (it.get("backupUrls") or []):
                if b:
                    cands.append(b.replace("http://", "https://"))
    if not cands:
        key = ((note.get("video") or {}).get("consumer") or {}).get("originVideoKey")
        if key:
            cands.append("https://sns-video-bd.xhscdn.com/" + key)
    if not cands:
        raise RuntimeError("这条笔记里没有视频（可能是图文笔记），转不了口播")
    return cands


def clean_tags(desc: str) -> str:
    t = re.sub(r"\[话题\]#", " ", desc or "")
    seen, out = set(), []
    for x in re.findall(r"#([^#\s]+)", t):
        x = x.strip("#[] ")
        if x and x not in seen:
            seen.add(x)
            out.append("#" + x)
    return " ".join(out)


def _download(urls, dst: str) -> int:
    last = None
    for u in urls:
        for _ in range(2):
            try:
                with _get(u, timeout=60) as r, open(dst, "wb") as f:
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


def fetch(url: str, workdir: str, platform: str = "小红书") -> dict:
    u = _full_url(url)
    with _get(u, timeout=25) as r:
        html = r.read().decode("utf-8", "ignore")
    note = parse_note(html)
    user = note.get("user") or {}
    v = note.get("video") or {}
    dur = ((v.get("capa") or {}).get("duration")
           or ((v.get("media") or {}).get("video") or {}).get("duration") or 0)

    note_id = note.get("noteId") or "xhs"
    mp4 = str(Path(workdir) / f"xhs_{note_id}.mp4")
    size = _download(pick_video(note), mp4)
    print(f"   ✓ 小红书视频 {size / 1048576:.1f} MB", flush=True)

    return {
        "platform": "小红书",
        "title": (note.get("title") or "").strip() or "小红书笔记",
        "author": (user.get("nickName") or user.get("nickname") or "").strip(),
        "tags": clean_tags(note.get("desc")),
        "publish_time": 0,
        "video_path": mp4,
        "audio_path": None,
        "text": None,
        "duration": dur,
        "source_url": u,
    }
