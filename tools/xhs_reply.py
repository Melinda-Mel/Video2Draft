#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书口播稿：短链 → 展开 → 抓分享页 → 读 __INITIAL_STATE__.noteData → 下载 → 复用 pipeline 转写 → DeepSeek 整理。

为什么要单独一条路（2026-09-20 实测）：
  wx_video_download 的小红书适配器读的是老的 `__INITIAL_STATE__.note.noteDetailMap`，
  小红书已改版为 `__INITIAL_STATE__.noteData.data.noteData`，所以适配器必然报
  「小红书 INITIAL_STATE 中没有笔记详情」。本脚本绕开适配器自己解析页面结构。

用法:
    python3 xhs_reply.py "https://xhslink.cn/o/xxxx"      # 短链或完整链都可以
"""
import json
import os
import tempfile
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline  # noqa: E402
import format_doc  # noqa: E402

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
HEADS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*",
         "Accept-Language": "zh-CN,zh;q=0.9"}


TMP = tempfile.gettempdir()

def get(url, timeout=20):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADS), timeout=timeout)


def full_url(short):
    for _ in range(3):
        try:
            u = get(short, timeout=15).geturl()
            if u and u != short:
                return u
        except Exception:
            time.sleep(1)
    return short


def parse_note(html):
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not m:
        raise RuntimeError("页面里没有 __INITIAL_STATE__（多半被风控拦了，稍后原样重发）")
    d = json.loads(m.group(1).replace("undefined", "null"))
    nd = d.get("noteData") or {}
    note = ((nd.get("data") or {}).get("noteData")) or (nd.get("normalNotePreloadData") or {})
    if not note:
        raise RuntimeError("noteData 里没有笔记详情（小红书又改结构了，去看 parse_note）")
    return note


def pick_video(note):
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
        # 图文笔记没有视频
        raise RuntimeError("这条笔记里没有视频（可能是图文笔记），转不了口播")
    return cands


def dl(urls, dst):
    last = None
    for u in urls:
        for _ in range(2):
            try:
                with get(u, timeout=60) as r, open(dst, "wb") as f:
                    while True:
                        chunk = r.read(262144)
                        if not chunk:
                            break
                        f.write(chunk)
                if os.path.getsize(dst) > 10000:
                    return os.path.getsize(dst)
            except Exception as e:
                last = e
                time.sleep(1)
    raise RuntimeError(f"下载失败: {last}")


def clean_tags(desc):
    t = re.sub(r"\[话题\]#", " ", desc or "")
    seen, out = set(), []
    for x in re.findall(r"#([^#\s]+)", t):
        x = x.strip("#[] ")
        if x and x not in seen:
            seen.add(x)
            out.append("#" + x)
    return " ".join(out)


def grab(url):
    """只负责拿到 (title, author, duration, tags, video_urls)，不下载。"""
    u = full_url(url)
    with get(u, timeout=25) as r:
        html = r.read().decode("utf-8", "ignore")
    note = parse_note(html)
    user = note.get("user") or {}
    v = note.get("video") or {}
    dur = ((v.get("capa") or {}).get("duration")
           or ((v.get("media") or {}).get("video") or {}).get("duration") or 0)
    return {
        "url": u,
        "id": note.get("noteId") or "xhs",
        "title": (note.get("title") or "").strip(),
        "author": (user.get("nickName") or user.get("nickname") or "").strip(),
        "duration": dur,
        "tags": clean_tags(note.get("desc")),
        "video_urls": pick_video(note),
    }


def run(url):
    t0 = time.time()
    info = grab(url)
    log = lambda m: print(m, flush=True)  # noqa: E731
    log(f"① 展开 {info['url'].split('?')[0]}")
    log(f"   ✓ {info['title'][:40]} | {info['author']} | {info['duration']}s")
    mp4 = os.path.join(TMP, f"xhs_{info['id']}.mp4")
    sz = dl(info["video_urls"], mp4)
    log(f"② 下载 ✓ {sz / 1048576:.1f} MB")
    wav = os.path.join(TMP, f"xhs_{info['id']}.wav")
    pipeline.extract_audio(mp4, wav)
    segs = pipeline.transcribe(wav)
    text = "".join(s["text"] for s in segs).strip()
    log(f"③ 转写 ✓ {len(text)} 字")
    doc = format_doc.format_transcript(info["title"], info["author"], info["duration"],
                                       len(text), info["tags"], text)
    base = pipeline.safe_name(f"{info['title']} _{info['tags']}")
    md = os.path.join(pipeline.OUT, base + ".md")
    open(md, "w", encoding="utf-8").write(doc)
    open(os.path.join(pipeline.OUT, base + ".txt"), "w", encoding="utf-8").write(text)
    log(f"④ 出稿 {int(time.time() - t0)}s → {md}")
    try:
        os.remove(mp4)
    except Exception:
        pass
    return doc


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    try:
        print(run(sys.argv[1]))
    except Exception as e:
        print("❌ 这条没成功：", str(e)[-300:])


if __name__ == "__main__":
    main()
