#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站口播稿：视频链接 → 公开 API 拿标题/作者/cid → playurl 拿媒体流 → 下载音频。

为什么单独走一条路（2026-09-20 加，照 x_reply.py 的先例）：
    解析服务（wx_video_download）抓 B站 页面被风控 HTTP 412（curl/yt-dlp/带浏览器
    cookies 都一样，页面风控看 TLS 指纹），但 B站 的 **公开 API 和 CDN 不拦**：
      · GET api.bilibili.com/x/web-interface/view?bvid=…   → 标题/作者/时长/cid
      · GET api.bilibili.com/x/player/playurl?bvid=…&cid=… → 媒体流直链
      · upos-*.bilivideo.com 的 m4s/flv 直链带 UA+Referer 就能下
    所以完全不碰 www.bilibili.com 页面，绕开风控。

转写只需要音轨，所以优先只下音频流（dash.audio），省一半时间和流量。

用法:
    python3 bili_reply.py "https://b23.tv/xxxx" | python3 bili_reply.py "https://www.bilibili.com/video/BV…"
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tempfile

import pipeline  # noqa: E402

TMP = tempfile.gettempdir()

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADS = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}

# 本机环境变量里有代理（加速器），B站走代理出口反被风控/掐断 → B站相关请求全部直连
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(url, timeout=25):
    return OPENER.open(urllib.request.Request(url, headers=HEADS), timeout=timeout)


def bvid_of(url):
    """b23.tv 短链先跟一次跳转拿真身，再从各种写法里挖 BV 号。"""
    u = (url or "").strip()
    if "b23.tv/" in u:
        try:
            # 不能让 urllib 一路跟到 www.bilibili.com（那一步会 412），只跟 b23.tv 这一跳
            class _NoFollow(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *a, **k):
                    return None
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoFollow)
            req = urllib.request.Request(u, headers={"User-Agent": UA})
            try:
                with op.open(req, timeout=20) as r:
                    loc = r.headers.get("Location")
            except urllib.error.HTTPError as e:
                loc = e.headers.get("Location")
            if loc and "bilibili.com" in loc:
                u = loc                     # 拿到真身就停
        except Exception as e:
            raise RuntimeError(f"b23.tv 短链展开失败（{type(e).__name__}）：稍后原样重发即可")
    m = re.search(r"(BV[0-9A-Za-z]{10})", u) or re.search(r"av(\d+)", u)
    if not m:
        raise RuntimeError("这条链接里没找到 BV 号（请发完整分享链接）")
    return m.group(1)


def api_json(url):
    try:
        with get(url) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"B站接口没返回数据（{type(e).__name__}）：视频可能已删/私密，稍后原样重发")
    if d.get("code") != 0:
        raise RuntimeError(f"B站接口报错 code={d.get('code')}: {d.get('message', '')[-120:]}")
    return d["data"]


def grab(url):
    """拿到 (title, author, duration, tags, bvid, media_urls)，不下载。media_urls 首选音轨。"""
    bvid = bvid_of(url)
    v = api_json("https://api.bilibili.com/x/web-interface/view?"
                 + urllib.parse.urlencode({"bvid": bvid}))
    cid = v.get("cid")
    if not cid:
        raise RuntimeError("view 接口没给 cid（分P视频暂不支持）")
    tags = " ".join("#" + t for t in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]+",
                                                v.get("desc", "")[:0] or "") )  # desc 里的标签不可靠，留空
    p = api_json("https://api.bilibili.com/x/player/playurl?"
                 + urllib.parse.urlencode({"bvid": bvid, "cid": cid, "fnval": 16}))
    urls = []
    dash = p.get("dash") or {}
    for a in dash.get("audio") or []:           # 音轨优先：小、快，转写够用
        if a.get("baseUrl"):
            urls.append(a["baseUrl"])
    for seg in p.get("durl") or []:             # 兜底：flv/mp4 整段（含音轨）
        if seg.get("url"):
            urls.append(seg["url"])
    if not urls:
        raise RuntimeError("playurl 没给任何媒体直链（可能需要登录态，换个视频试试）")
    return {
        "url": f"https://www.bilibili.com/video/{bvid}",
        "id": bvid,
        "title": (v.get("title") or "B站视频").strip(),
        "author": (v.get("owner") or {}).get("name", ""),
        "duration": round((v.get("duration") or 0)),
        "tags": tags,
        "media_urls": urls,
    }


def dl(url, dst):
    last = None
    for _ in range(2):
        try:
            with get(url, timeout=120) as r, open(dst, "wb") as f:
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


def dl_first(urls, dst):
    """按顺序试直链，第一个能下的用。"""
    last = None
    for u in urls:
        try:
            return dl(u, dst)
        except Exception as e:
            last = e
    raise RuntimeError(f"所有媒体直链都下不动: {last}")


def run(url):
    t0 = time.time()
    info = grab(url)
    log = lambda m: print(m, flush=True)  # noqa: E731
    log(f"① B站 {info['url']}")
    log(f"   ✓ {info['title'][:40]} | {info['author']} | {info['duration']}s")
    ext = ".flv" if ".flv" in info["media_urls"][0] else ".m4s"
    mp4 = os.path.join(TMP, f"bili_{info['id']}{ext}")
    sz = dl_first(info["media_urls"], mp4)
    log(f"② 下载 ✓ {sz / 1048576:.1f} MB")
    wav = os.path.join(TMP, f"bili_{info['id']}.wav")
    pipeline.extract_audio(mp4, wav)
    segs = pipeline.transcribe(wav)
    text = "".join(s["text"] for s in segs).strip()
    log(f"③ 转写 ✓ {len(text)} 字")
    import format_doc  # noqa: E402
    doc = format_doc.format_transcript(info["title"], info["author"], info["duration"],
                                       len(text), info["tags"], text)
    base = pipeline.safe_name(f"{info['title']} _{info['tags']}")
    md = os.path.join(pipeline.OUT, base + ".md")
    open(md, "w", encoding="utf-8").write(doc)
    log(f"④ 出稿 {int(time.time() - t0)}s → {md}")
    for f in (mp4, wav):
        try:
            os.remove(f)
        except OSError:
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
        sys.exit(1)


if __name__ == "__main__":
    main()
