#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_card.py —— 一条命令端到端：链接 → 成品稿 → 归档 .md → 长图 .png → 推微信 → 更新 INDEX。

为什么要有它（2026-09-20 提速②）：
    以前「跑转写 → 写 md → md2pic 出图 → wx-send 推送 → 改 INDEX」是五轮调用，
    每轮都有往返延迟。打包成一条命令后只剩一轮，编排开销基本归零。

用法:
    python3 make_card.py "<链接>"
    python3 make_card.py "<链接>" --no-push      # 只归档出图，不推微信（自查/调试）
    python3 make_card.py "<链接>" --platform 小红书   # 平台识别失败时手动指定

产出（正常情况）:
    ~/Documents/资料库/文案素材/<平台>文案-<作者>-YYYYMMDD.md    ← 源文件
    同名 .png                                                     ← 长图视图
    微信收到一张长图 + 一句话说明
    末尾打印一行 @@CARD@@{json}，给调用方取结果用

耗时参考（170s 视频）：解析 <1s + 下载 3~4s + 提音频 <1s + 热转写 ~9s
    + DeepSeek 整理 ~4.5s + 出图 ~3s ≈ 20s
"""
import datetime
import json
import os
import tempfile
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import format_doc          # noqa: E402
import pipeline            # noqa: E402
import platforms           # noqa: E402

TMP = tempfile.gettempdir()
LIB = os.environ.get("YJCG_LIB", os.path.expanduser("~/一键成稿"))   # 归档目录，可用环境变量改
ARCHIVE = os.path.join(LIB, "文案素材")
INDEX = os.path.join(LIB, "INDEX.md")
MD2PIC = os.path.expanduser("~/.local/bin/md2pic")
WX_SEND = os.path.expanduser("~/.local/bin/wx-send")


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


# ---------- 平台识别 ----------
# 她明确不做的平台（2026-09-20）：认出链接后直接给一句人话说明，别丢进去跑半天。
UNSUPPORTED = {
    "快手": "你不玩快手",
    "微博": "你 2026-09-20 要求下线（适配器没验证过，不再维护）",
    "TikTok": "登录态不稳，你 2026-09-20 说先放弃",
}


def detect(raw, forced=None):
    if forced:
        return forced, raw.strip()
    name, url = platforms.extract(raw)
    if name:
        return name, url
    if raw.strip().startswith("http"):
        return "未知", raw.strip()
    raise RuntimeError("没从这段文字里认出视频链接（可用 --platform 手动指定平台）")


# ---------- 取内容（统一成 title/author/duration/tags/text + 临时文件）----------
def extract(platform, url):
    """返回 (info, tmp_files)。小红书与其它平台走两条不同的路（原因见 SOLUTIONS.md）。"""
    info = {"platform": platform, "url": url, "duration": 0, "tags": ""}
    tmp = []
    if platform == "小红书":
        import xhs_reply
        note = xhs_reply.grab(url)
        info.update(title=note["title"], author=note["author"],
                    duration=note["duration"] or 0, tags=note["tags"])
        mp4 = os.path.join(TMP, f"card_xhs_{note['id']}.mp4")
        tmp.append(mp4)
        size = xhs_reply.dl(note["video_urls"], mp4)
        log(f"② 下载 ✓ {size / 1048576:.1f} MB")
        info["url"] = note["url"]
    elif platform == "B站":
        # wxcd 适配器抓 B站 页面被风控 412（原因见 bili_reply.py 头注），单独走公开 API
        import bili_reply
        bi = bili_reply.grab(url)
        info.update(title=bi["title"], author=bi["author"],
                    duration=bi["duration"] or 0, tags=bi["tags"])
        ext = ".flv" if ".flv" in bi["media_urls"][0] else ".m4s"
        mp4 = os.path.join(TMP, f"bili_{bi['id']}{ext}")
        tmp.append(mp4)
        size = bili_reply.dl_first(bi["media_urls"], mp4)
        log(f"② 下载 ✓ {size / 1048576:.1f} MB（公开 API，只取音轨所需媒体流）")
        info["url"] = bi["url"]
    elif platform == "YouTube":
        # wxcd 适配器下高清全量（一条 shorts 64MB，机房出口 ~380KB/s 时下载 170s+），
        # 改走 yt-dlp 只下 bestaudio（2026-09-20 实测同一条视频 220s → 62s）
        import yt_reply
        yt = yt_reply.grab(url)
        info.update(title=yt["title"], author=yt["author"],
                    duration=yt["duration"] or 0, tags=yt["tags"])
        mp4 = os.path.join(TMP, f"card_yt_{yt['id']}.m4a")
        tmp.append(mp4)
        size = yt_reply.dl_audio(yt["url"], mp4)
        log(f"② 下载 ✓ {size / 1048576:.1f} MB（yt-dlp 只取音轨）")
        info["url"] = yt["url"]
    elif platform == "X":
        # X/Twitter 没有适配器（原因见 x_reply.py），单独走一条路
        import x_reply
        tw = x_reply.grab(url)
        info.update(title=tw["title"] or "X 视频", author=tw["author"],
                    duration=tw["duration"] or 0, tags=tw["tags"])
        mp4 = os.path.join(TMP, f"card_x_{tw['id']}.mp4")
        tmp.append(mp4)
        if tw["video_urls"][0].startswith("https://video.twimg.com"):
            size = x_reply.dl(tw["video_urls"], mp4)
        else:
            size = x_reply.dl_ytdlp(tw["url"], mp4)
        log(f"② 下载 ✓ {size / 1048576:.1f} MB")
        info["url"] = tw["url"]
    else:
        job, meta = pipeline.resolve(url)
        c = meta["content"]
        acc = meta.get("account") or {}
        parts = [p.strip() for p in (c.get("title") or "").split("\n") if p.strip()]
        info.update(title=parts[0] if parts else "未命名",
                    author=acc.get("nickname", ""),
                    tags=next((p for p in parts[1:] if p.startswith("#")), ""))
        mp4 = pipeline.download(job, pipeline.OUT)
        tmp.append(mp4)

    wav = os.path.join(TMP, f"card_{int(time.time())}.wav")
    tmp.append(wav)
    pipeline.extract_audio(mp4, wav)
    segs = pipeline.transcribe(wav)
    text = "".join(s["text"] for s in segs).strip()
    # 标题是封面上的大字，也要过一遍纠错表（实测平台返回的标题里就有「证券上裢」这种错字）
    import fix_terms
    info["title"], _ = fix_terms.fix_text(info.get("title") or "")
    if not info.get("duration") and segs:
        info["duration"] = round(segs[-1]["end"])
    info["text"] = text
    info["chars"] = len(text)
    return info, tmp


# ---------- 整理稿 → 归档 .md ----------
def doc_to_md(doc, title, platform, author, chars, date, url):
    """把 format_doc 的「✅《…》/【关键要点】/【完整文案】」结构转成资料库用的 md。"""
    lines = doc.split("\n")

    def find(mark):
        for i, l in enumerate(lines):
            if mark in l:
                return i
        return -1

    i_kp, i_full = find("【关键要点】"), find("【完整文案】")
    kp = "\n".join(lines[i_kp + 1:i_full]).strip() if 0 <= i_kp < i_full else ""
    body = "\n".join(lines[i_full + 1:]).strip() if i_full >= 0 else "\n".join(lines[1:]).strip()

    head = f"# {title}\n\n"
    head += f"**来源**：{platform} · {author} ｜ **归档**：{date} ｜ **字数**：{chars}\n\n"
    out = head
    if kp:
        out += "## 关键要点\n\n" + kp + "\n\n"
    out += "## 完整文案\n\n" + body + "\n"
    if url:
        # 用 md 链接写法：md2pic 会渲染成可点的蓝链（`<sub>` 这类裸 HTML 会被转义显示成字面文本）
        out += f"\n---\n\n[原始链接]({url})\n"
    return out


def safe(s, n=40):
    s = re.sub(r"[\\/:*?\"<>|#\n\r\t\s]", "", s or "").strip("_ ")
    return s[:n] or "未知"


def archive_path(platform, author, title, date):
    os.makedirs(ARCHIVE, exist_ok=True)
    base = f"{safe(platform, 10)}文案-{safe(author)}-{date}"
    p = os.path.join(ARCHIVE, base + ".md")
    # 同名不同视频（同作者同一天两条）：追加序号，别互相覆盖
    n = 2
    while os.path.exists(p):
        try:
            if open(p, encoding="utf-8").readline().strip() == f"# {title}":
                break                       # 同一条视频重跑 → 覆盖
        except Exception:
            pass
        p = os.path.join(ARCHIVE, f"{base}-{n}.md")
        n += 1
    return p


# ---------- 出图 / 推送 / 索引 ----------
def render(md):
    t0 = time.time()
    p = subprocess.run([MD2PIC, md], capture_output=True, text=True)
    png = os.path.splitext(md)[0] + ".png"
    if not os.path.exists(png):
        raise RuntimeError(f"md2pic 没出图：{(p.stdout + p.stderr)[-300:]}")
    log(f"⑤ 长图 ✓ {os.path.getsize(png) // 1024} KB / {time.time()-t0:.1f}s")
    return png


def push(png, text):
    if not os.path.exists(WX_SEND):
        log("⑥ 微信推送 –（未安装 wx-send，跳过；仅本地出稿）")
        return False
    p = subprocess.run([WX_SEND, png, "--text", text], capture_output=True, text=True)
    ok = "已发图片" in p.stdout
    log(f"⑥ 微信推送 {'✓' if ok else '✗ ' + (p.stdout + p.stderr)[-160:]}")
    return ok


def update_index(platform, author, title, md, date):
    rel = "资料库/" + os.path.relpath(md, LIB)
    if not os.path.exists(INDEX):
        return "没有 INDEX.md，跳过"
    txt = open(INDEX, encoding="utf-8").read()
    if rel in txt:
        return "已在索引里（不重复登记）"
    txt = re.sub(r"\*\*最后更新\*\*：[^\n]*",
                 f"**最后更新**：{date} ｜ 维护：一键成稿", txt, count=1)
    entry = (f"- {date} · {platform} · {author}《{title}》\n"
             f"  → `{rel}`（同名 `.png` 长图已有）\n")
    m = re.search(r"(\*\*各平台文案\*\*\n+)", txt)
    if m:
        txt = txt[:m.end()] + entry + txt[m.end():]
    else:                               # 结构变了就退回「已入库」段尾
        m2 = re.search(r"(###\s*一、已入库\n+)", txt)
        txt = (txt[:m2.end()] + entry + txt[m2.end():]) if m2 else txt.rstrip() + "\n" + entry
    open(INDEX, "w", encoding="utf-8").write(txt)
    return "已登记"


# ---------- 主流程 ----------
def run(raw, force_platform=None, do_push=True):
    t_all = time.time()
    stages = {}
    platform, url = detect(raw, force_platform)
    if platform in UNSUPPORTED:
        raise RuntimeError(f"{platform}这条链路已下线：{UNSUPPORTED[platform]}")
    log(f"① 平台 {platform} ｜ {url[:70]}")

    t0 = time.time()
    info, tmp = extract(platform, url)
    stages["下载+转写"] = round(time.time() - t0, 1)
    log(f"③ 转写 ✓ {info['chars']} 字 ｜ {info['title'][:36]}")

    date = datetime.date.today().strftime("%Y-%m-%d")
    stamp = datetime.date.today().strftime("%Y%m%d")
    t0 = time.time()
    doc = format_doc.format_transcript(info["title"], info["author"], info.get("duration", 0),
                                       info["chars"], info.get("tags", ""), info["text"])
    stages["整理"] = round(time.time() - t0, 1)
    if not doc.strip().startswith("✅《"):
        raise RuntimeError(f"整理结果格式异常：{doc[:120]}")

    md = archive_path(platform, info["author"], info["title"], stamp)
    open(md, "w", encoding="utf-8").write(
        doc_to_md(doc, info["title"], platform, info["author"], info["chars"], date, info["url"]))
    log(f"④ 归档 ✓ {os.path.relpath(md, LIB)}")

    png = render(md)
    t0 = time.time()
    # 推送文案只留一行（2026-09-20 她要求：微信只保留「图 + 一句总介绍」，不附存档路径）
    pushed = push(png, f"{platform} · {info['author']}《{info['title']}》") if do_push else False
    if do_push:
        stages["推送"] = round(time.time() - t0, 1)
    idx = update_index(platform, info["author"], info["title"], md, date)
    log(f"⑦ INDEX {idx}")

    for f in tmp:                        # 临时视频/音频不留在磁盘上（她问过占空间的事）
        try:
            os.remove(f)
        except OSError:
            pass

    total = round(time.time() - t_all, 1)
    stages["合计"] = total
    result = {"ok": True, "platform": platform, "title": info["title"], "author": info["author"],
              "chars": info["chars"], "md": md, "png": png, "pushed": pushed,
              "index": idx, "stages": stages}
    log(f"完成 ✓ {total}s ｜ {json.dumps(stages, ensure_ascii=False)}")
    return result


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return
    fp = sys.argv[sys.argv.index("--platform") + 1] if "--platform" in sys.argv else None
    try:
        r = run(args[0], force_platform=fp, do_push="--no-push" not in sys.argv)
        print("@@CARD@@" + json.dumps(r, ensure_ascii=False))
    except Exception as e:
        print(f"❌ {type(e).__name__}: {str(e)[-400:]}")
        sys.exit(2)


if __name__ == "__main__":
    main()
