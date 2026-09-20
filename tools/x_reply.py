#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X（Twitter）口播稿：推文链接 → 拿视频直链 + 作者/正文 → 下载 → 复用 pipeline 转写 → DeepSeek 整理。

为什么不走 wxcd 适配器（2026-09-20 加）：
    解析服务（wx_video_download）支持的平台里根本没有 X/Twitter，链接识别器也不认 x.com，
    所以单独走这一条路。

走的是 X 公开的 syndication 接口（无需登录、无需 cookie）：
    https://cdn.syndication.twimg.com/tweet-result?id=<推文ID>&token=a
它返回 JSON，里面就有作者、正文、时长和 video_info.variants（多档 mp4 直链）。
比抓页面稳、也更省流量。拿不到（推文被删/私密/接口改动）时**如实报错**，不猜。

用法:
    python3 x_reply.py "https://x.com/<user>/status/<id>"
"""
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline  # noqa: E402
import format_doc  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADS = {"User-Agent": UA, "Accept": "application/json,text/html,*/*"}
API = "https://cdn.syndication.twimg.com/tweet-result?id={id}&token=a"

# 直链备用：万一 syndication 挂了，用 yt-dlp 兜底（本机 default 环境里有）
YTDLP = os.environ.get("YJCG_YTDLP", "yt-dlp")


def get(url, timeout=20):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADS), timeout=timeout)


def tweet_id(url):
    """从各种写法的 X 链接里挖出推文 ID（/status/<id>，后面带 /video/1 或 ?s=46 都不影响）。"""
    m = re.search(r"/(?:status|statuses)/(\d+)", url or "")
    if not m:
        raise RuntimeError("这条链接里没找到推文 ID（请发 x.com/<用户>/status/<数字> 这种形式）")
    return m.group(1)


def clean_text(text):
    """去掉正文里的 t.co 短链和多余空白。"""
    t = re.sub(r"https?://t\.co/\S+", "", text or "")
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def make_title(text):
    """X 没有标题字段，用正文第一行当标题（去掉话题标签、截断到 40 字）。"""
    for line in (text or "").split("\n"):
        s = re.sub(r"#\S+", "", line).strip(" 　·—-，,。！!？?、")
        if len(s) >= 4:
            return s[:40]
    return "X 视频"


def tags_of(text):
    seen, out = set(), []
    for t in re.findall(r"#([^\s#]+)", text or ""):
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append("#" + t)
    return " ".join(out[:8])


def pick_urls(media):
    """从 mediaDetails 里挑 mp4 直链，优先 720p 一档（够清楚又不拖下载）。"""
    var = {}
    for m in (media or []):
        for v in ((m.get("video_info") or {}).get("variants") or []):
            if v.get("content_type") == "video/mp4" and v.get("url"):
                var[int(v.get("bitrate") or 0)] = v["url"]
    if not var:
        return []
    rates = sorted(var)
    best = [r for r in rates if r <= 2176000] or rates[:1]
    order = sorted(best, reverse=True) + sorted(rates, reverse=True)   # 首选 720p，其余兜底
    out, seen = [], set()
    for r in order:
        if var[r] not in seen:
            seen.add(var[r])
            out.append(var[r])
    return out


def dl(urls, dst):
    last = None
    for u in urls:
        for _ in range(2):
            try:
                with get(u, timeout=90) as r, open(dst, "wb") as f:
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


def dl_ytdlp(url, dst):
    """兜底：syndication 拿不到直链时，用 yt-dlp 下（走它自己的解析）。"""
    import subprocess
    p = subprocess.run([YTDLP, "-f", "mp4/best", "--no-playlist", "-o", dst, url],
                       capture_output=True, text=True, timeout=300)
    if not os.path.exists(dst) or os.path.getsize(dst) < 10000:
        raise RuntimeError(f"yt-dlp 也没拿到：({(p.stdout + p.stderr)[-200:]})")
    return os.path.getsize(dst)


def grab(url):
    """拿到 (title, author, duration, tags, video_urls)，不下载。"""
    tid = tweet_id(url)
    canonical = f"https://x.com/i/status/{tid}"
    try:
        with get(API.format(id=tid), timeout=25) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"X 接口没返回数据（{type(e).__name__}）：推文可能已删/私密，"
                           f"或本机出网把 x.com 掐了，稍后原样重发即可")
    text = clean_text(d.get("text"))
    user = d.get("user") or {}
    urls = pick_urls(d.get("mediaDetails"))
    if not urls:
        # 没有直链不一定是没视频（接口字段改动），让调用方用 yt-dlp 兜底
        urls = [canonical]
    return {
        "url": canonical,
        "id": tid,
        "title": make_title(text),
        "author": (user.get("name") or user.get("screen_name") or "").strip(),
        "duration": round((d.get("video") or {}).get("durationMs", 0) / 1000),
        "tags": tags_of(text),
        "tweet": text,
        "video_urls": urls,
    }


def run(url):
    t0 = time.time()
    info = grab(url)
    log = lambda m: print(m, flush=True)  # noqa: E731
    log(f"① X {info['url']}")
    log(f"   ✓ {info['title'][:40]} | {info['author']} | {info['duration']}s")
    mp4 = f"/tmp/x_{info['id']}.mp4"
    if info["video_urls"][0].startswith("https://video.twimg.com"):
        sz = dl(info["video_urls"], mp4)
    else:
        sz = dl_ytdlp(info["url"], mp4)
    log(f"② 下载 ✓ {sz / 1048576:.1f} MB")
    wav = f"/tmp/x_{info['id']}.wav"
    pipeline.extract_audio(mp4, wav)
    segs = pipeline.transcribe(wav)
    text = "".join(s["text"] for s in segs).strip()
    log(f"③ 转写 ✓ {len(text)} 字")
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


if __name__ == "__main__":
    main()
