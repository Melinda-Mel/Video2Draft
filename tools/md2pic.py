#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""md2pic —— 把 Markdown 渲染成「一张竖长图」，专治手机上表格要横划、行距太松的问题。

为什么要有它：
  .md 文件在微信里虽然能渲染，但排版是按桌面端设计的——行距大、表格宽，手机窄屏
  必须横向滑动才能看完。长图则是：宽度我定死、表格不许溢出、行距压紧，
  微信里直接看不用点开文件，转给别人也不用装任何 App。

用法：
  md2pic 输入.md [-o 输出.png] [--width 720] [--title "顶部大标题"] [--sub "副标题"]

产出：PNG。同目录会留一份中间 HTML，方便排查。
"""
import os, re, sys, html, subprocess, json
from pathlib import Path

# ---- Chrome 探测（跨平台）----
# 优先环境变量 YJCG_CHROME，其次 PATH，其次各系统常见安装位置。
def _find_chrome():
    import shutil
    v = os.environ.get("YJCG_CHROME")
    if v and os.path.exists(v):
        return v
    for n in ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge"):
        p = shutil.which(n)
        if p:
            return p
    cands = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return None

def _file_url(p):
    """跨平台 file:// URL。

    Windows 上直接拼 "file://" + "C:\\a\\b.html" 是无效地址
    （浏览器要的是 file:///C:/a/b.html），必须走 Path.as_uri()。
    """
    return Path(p).resolve().as_uri()


CHROME = _find_chrome()

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#fff;color:#24292f;
  font-family:-apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  font-size:30px;line-height:1.72;-webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility}
.topbar{height:10px;background:linear-gradient(90deg,#2f6df6 0%,#7b5cf0 52%,#f2606b 100%)}
.wrap{padding:0 36px 54px;counter-reset:kpn stn olc}
/* ---- 封面 ---- */
.cover{padding:48px 0 30px;margin-bottom:38px;border-bottom:2px solid #eef1f5}
.cover .pill{display:inline-block;font-size:23px;font-weight:600;letter-spacing:.5px;
  color:#2f6df6;background:#eaf1ff;border-radius:999px;padding:8px 20px;margin-bottom:22px}
.cover h1{font-size:50px;line-height:1.26;letter-spacing:-.6px;font-weight:700;color:#12161c}
.cover .sub{margin-top:18px;font-size:25px;color:#8a9099;letter-spacing:.2px}
.cover .rule{margin-top:26px;height:6px;width:92px;border-radius:3px;
  background:linear-gradient(90deg,#2f6df6,#7b5cf0)}
/* ---- 标题 ---- */
h1{font-size:42px;line-height:1.3;margin:40px 0 18px;letter-spacing:-.3px}
h2{font-size:36px;line-height:1.32;font-weight:700;color:#12161c;letter-spacing:-.2px;
  margin:52px 0 22px;padding:16px 22px;border-radius:16px;border-left:10px solid #2f6df6;
  background:linear-gradient(90deg,#f1f6ff,#fbfcff)}
h3{font-size:32px;font-weight:600;margin:34px 0 14px;color:#1b2027;
  padding-left:22px;position:relative}
h3::before{content:"";position:absolute;left:0;top:11px;width:10px;height:10px;
  border-radius:3px;background:#2f6df6;transform:rotate(45deg)}
h4{font-size:29px;font-weight:600;margin:26px 0 12px;color:#3a4048}
/* ---- 正文 ---- */
p{margin:18px 0;color:#2b3138}
strong{font-weight:700;color:#0b46c8}
em{font-style:normal;background:#fff6d6;padding:0 4px;border-radius:4px}
a{color:#2f6df6;text-decoration:none;border-bottom:2px solid #bcd0fb}
ul{margin:15px 0;padding-left:42px}
ol{margin:15px 0;padding-left:0;list-style:none}
ol li{counter-increment:olc;position:relative;padding-left:64px;margin:16px 0}
ol li::before{content:counter(olc);position:absolute;left:0;top:3px;
  width:44px;height:44px;border-radius:50%;background:#eef3fe;color:#0b46c8;
  font-size:26px;font-weight:700;text-align:center;line-height:44px}
li{margin:9px 0;padding-left:4px}
li::marker{color:#8a9099}
hr{border:0;height:2px;background:#eceef1;margin:34px 0}
blockquote{margin:20px 0;padding:18px 22px;background:#f5f7fa;
  border-left:8px solid #c8d3e0;border-radius:0 12px 12px 0;color:#3a4048}
blockquote p{margin:6px 0}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:26px;
  background:#f0f2f5;color:#c0392b;padding:3px 9px;border-radius:6px;
  word-break:break-all}
pre{margin:20px 0;padding:22px;background:#f6f7f9;border-radius:14px;
  border:2px solid #eceef1}
pre code{background:none;color:#24292f;padding:0;font-size:25px;
  line-height:1.55;white-space:pre-wrap;word-break:break-word}
table{width:100%;table-layout:fixed;border-collapse:collapse;margin:22px 0;
  font-size:26px;border:2px solid #e6e9ee;border-radius:12px;overflow:hidden}
th,td{padding:16px 14px;text-align:left;vertical-align:top;
  border-bottom:2px solid #eef0f3;word-break:break-word;line-height:1.5}
th{background:#f6f7f9;font-weight:600;font-size:25px}
tr:last-child td{border-bottom:0}
.cards{margin:22px 0}
.card{border:2px solid #e6e9ee;border-radius:14px;padding:20px 22px;margin:14px 0;
  background:#fcfcfd}
.card .ct{font-weight:600;font-size:29px;margin-bottom:10px;color:#1a1d21}
.card .cr{margin:7px 0;font-size:26px;line-height:1.5;color:#3a4048}
.card .ck{color:#8a9099;display:inline-block;min-width:0}
/* ---- 关键要点卡片 ---- */
.kps{margin:24px 0 10px}
.kp{counter-increment:kpn;position:relative;background:#fbfcfe;
  border:2px solid #e9eef6;border-radius:18px;padding:18px 22px 18px 68px;
  margin:14px 0;box-shadow:0 6px 18px rgba(47,109,246,.06)}
.kp::before{content:counter(kpn);position:absolute;left:20px;top:20px;
  width:34px;height:34px;border-radius:11px;line-height:34px;text-align:center;
  background:linear-gradient(135deg,#2f6df6,#7b5cf0);color:#fff;
  font-size:22px;font-weight:700}
.kp p{margin:0;font-size:29px;line-height:1.66;color:#2b3138}
/* ---- 正文分节（完整文案里的「第 N 条」小标题）---- */
.steps{margin:26px 0 10px}
.step{counter-increment:stn;position:relative;font-size:33px;font-weight:700;
  color:#12161c;margin:34px 0 8px;padding-left:54px;line-height:1.42}
.step::before{content:counter(stn);position:absolute;left:0;top:3px;width:38px;height:38px;
  border-radius:50%;line-height:38px;text-align:center;font-size:22px;font-weight:700;
  color:#2f6df6;background:#eaf1ff}
.foot{margin-top:56px;padding-top:26px;border-top:2px dashed #e6eaef;
  font-size:23px;color:#a8aeb6;text-align:center;letter-spacing:.4px;line-height:1.6}
"""

# ---- 风格变体（2026-09-20 她要三版挑）----
# refine  = 现版精修：压紧行距、卡片加重、封面加层次，改动最小最稳
# magazine= 杂志感：深色大封面 + 宋体标题 + 墨蓝/朱红双色，视觉起伏最强
# cards   = 卡片流：灰底白卡，每段一个气泡卡，结构感最强
STYLE_CSS = {
# Paper & Ink —— 编辑部纸感：米白纸底、宋体标题、朱红重点、首字下沉
"paper": """
.topbar{height:0}
body{background:#faf7f2;color:#221f1b;line-height:1.76;letter-spacing:.2px}
.wrap{padding:0 46px 66px}
.cover{padding:68px 0 38px;margin-bottom:42px}
.cover .pill{font-family:"Baskerville","Times New Roman",serif;font-size:21px;
  letter-spacing:3px;color:#c41e3a;border:1px solid #d8cfc2;border-radius:2px;
  padding:7px 17px;background:#fdfaf5}
.cover h1{font-family:"Songti SC","STSong","Baskerville",serif;font-size:56px;
  font-weight:700;line-height:1.26;letter-spacing:.5px;color:#161311}
.cover .sub{font-family:"Baskerville",serif;font-style:italic;font-size:24px;color:#8b8177}
.cover .rule{height:2px;width:100%;margin-top:38px;border-radius:0;
  background:linear-gradient(90deg,#161311 0 84px,#c41e3a 84px 134px,#161311 134px 100%)}
h2{font-family:"Songti SC","STSong",serif;font-size:35px;font-weight:700;color:#161311;
  margin:58px 0 26px;padding-bottom:16px;background:none;border-left:0;border-radius:0;
  border-bottom:1px solid #ddd5c8;position:relative}
h2::after{content:"";position:absolute;left:0;bottom:-2px;width:72px;height:3px;background:#c41e3a}
h3::before{background:#c41e3a;border-radius:2px}
.kp{background:none;border:0;border-top:1px solid #e8e0d3;border-radius:0;box-shadow:none;
  padding:20px 0 20px 80px;margin:0}
.kp::before{content:counter(kpn);font-family:"Baskerville","Times New Roman",serif;
  font-size:44px;font-weight:700;color:#c41e3a;background:none;width:auto;height:auto;
  line-height:1;left:22px;top:14px}
.kp p{font-size:29px;line-height:1.62}
h2+p{margin-top:26px}
h2+p::first-letter{font-family:"Songti SC","STSong",serif;font-size:88px;line-height:.86;
  float:left;padding:8px 14px 0 0;color:#c41e3a;font-weight:700}
p{margin:20px 0;color:#262220}
strong{color:#161311;background:linear-gradient(180deg,transparent 60%,#f4e6c9 60%);
  padding:0 2px;font-weight:700}
em{background:#f4e6c9}
a{color:#c41e3a;border-bottom-color:#e3c4c0}
hr{height:1px;background:#ddd5c8;margin:46px 0}
blockquote{border-left:3px solid #c41e3a;background:none;color:#4a443e}
ol li::before{background:#161311;color:#fff;border-radius:2px}
.step::before{background:#161311;color:#fff}
.foot{border-top:1px solid #ddd5c8;color:#9a9188;font-family:"Baskerville",serif;letter-spacing:3px}
""",
# Swiss Modern —— 纯白网格：黑块标题、朱红锐角、可见栅格、非对称
"swiss": """
.topbar{height:0}
body{background:#fff;color:#111;font-family:"Futura","Avenir Next","PingFang SC",sans-serif;
  line-height:1.6;letter-spacing:.1px}
.wrap{padding:0 48px 68px;
  background-image:linear-gradient(90deg,rgba(0,0,0,.07) 0 1px,transparent 1px);
  background-size:calc(100% / 6) 100%}
.cover{padding:78px 0 40px;border-bottom:8px solid #111;margin-bottom:0}
.cover .pill{background:#ff3300;color:#fff;font-weight:700;font-size:20px;letter-spacing:3px;
  padding:9px 16px;border-radius:0}
.cover h1{font-size:64px;font-weight:800;letter-spacing:-1.6px;line-height:1.1;color:#111}
.cover .sub{color:#8a8a8a;font-size:24px;letter-spacing:1px}
.cover .rule{background:#ff3300;width:84px;height:16px;margin-top:38px;border-radius:0}
h2{display:inline-block;background:#111;color:#fff;font-size:28px;font-weight:700;
  letter-spacing:4px;padding:13px 22px;margin:58px 0 28px;border-radius:0;
  border-left:0;font-family:"Futura","Avenir Next","PingFang SC",sans-serif}
.kps{margin:0}
.kp{background:transparent;border:0;border-top:2px solid #111;border-radius:0;box-shadow:none;
  padding:22px 0 22px 86px;margin:0}
.kp::before{content:counter(kpn);background:none;color:#ff3300;font-size:46px;font-weight:800;
  line-height:1;width:auto;height:auto;left:22px;top:14px}
.kp p{font-size:29px;line-height:1.58;color:#111}
p{color:#1a1a1a}
strong{color:#111;background:linear-gradient(180deg,transparent 62%,#ffd7cc 62%);padding:0 2px}
em{background:#ffd7cc}
hr{height:2px;background:#111;margin:42px 0}
blockquote{border-left:8px solid #ff3300;background:#fff;border-radius:0}
ol li::before{background:#111;color:#fff;border-radius:0}
.step::before{background:#ff3300;color:#fff;border-radius:0}
h3::before{background:#ff3300;width:12px;height:12px;border-radius:0}
.foot{border-top:8px solid #111;border-style:solid;padding-top:22px;color:#111;
  font-weight:700;letter-spacing:3px;text-align:left}
""",
# Notebook Tabs —— 暗桌面上的米白活页纸：右侧彩色标签 + 左侧装订孔 + 五色序号
"tabs": """
.topbar{height:0}
body{background:#2b2a29}
.wrap{margin:34px;border-radius:8px;padding:0 44px 62px;
  box-shadow:0 20px 44px rgba(0,0,0,.42);
  background-color:#f8f6f1;
  background-image:radial-gradient(circle at 22px 74px,#e2dbcd 0 8px,transparent 9px);
  background-repeat:repeat-y;background-size:44px 300px}
.cover{padding:62px 0 36px;border-bottom:1px solid #e3ddd1;margin-bottom:40px}
.cover .pill{background:#98d4bb;color:#1a1a1a;font-weight:700;font-size:21px;
  letter-spacing:1px;padding:8px 18px;border-radius:3px}
.cover h1{font-family:"Didot","Bodoni 72","Songti SC",serif;font-size:54px;font-weight:700;
  line-height:1.24;letter-spacing:.5px;color:#1a1a1a}
.cover .sub{color:#8a8479;font-size:24px}
.cover .rule{height:9px;width:172px;border-radius:2px;margin-top:34px;
  background:linear-gradient(90deg,#98d4bb 0 20%,#c7b8ea 20% 40%,#f4b8c5 40% 60%,
    #a8d8ea 60% 80%,#ffe6a7 80% 100%)}
h2{position:relative;background:none;border:0;border-left:0;border-radius:0;
  padding:0 60px 15px 0;margin:56px 0 26px;font-family:"Didot","Songti SC",serif;
  font-size:36px;color:#1a1a1a;border-bottom:1px solid #e3ddd1}
h2::after{content:"";position:absolute;right:-44px;top:2px;width:38px;height:48px;
  border-radius:5px 0 0 5px;background:#c7b8ea}
h2:nth-of-type(2)::after{background:#f4b8c5}
.kp{background:#fff;border:1px solid #ece6da;border-radius:6px;
  box-shadow:0 2px 0 #ece6da;padding:18px 22px 18px 76px}
.kp::before{width:36px;height:36px;line-height:36px;border-radius:3px;
  background:#1a1a1a;color:#fff;font-size:22px;left:22px;top:20px}
.kp:nth-child(5n+1)::before{background:#4f9d7c}
.kp:nth-child(5n+2)::before{background:#7d68b8}
.kp:nth-child(5n+3)::before{background:#c4738f}
.kp:nth-child(5n+4)::before{background:#4f8fb3}
.kp:nth-child(5n+5)::before{background:#c9a24a}
.kp p{font-size:29px;line-height:1.62}
p{color:#26231f}
strong{color:#1a1a1a;background:#fbeec8;padding:0 2px}
em{background:#fbeec8}
a{color:#7d68b8;border-bottom-color:#cfc4e4}
hr{height:1px;background:#e3ddd1;margin:44px 0}
blockquote{border-left:6px solid #98d4bb;background:#fff}
.foot{color:#9a978f;border-top:1px solid #e3ddd1}
""",
}



def inline(t):
    t = html.escape(t, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    return t


def split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def is_sep(line):
    return bool(re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", line)) and "-" in line


def md_to_html(md):
    lines = md.split("\n")
    out, i = [], 0
    kp_mode = False
    while i < len(lines):
        ln = lines[i]

        if ln.strip().startswith("```"):
            lang = ln.strip()[3:].strip()
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            body = html.escape("\n".join(buf))
            out.append(f'<pre><code class="lang-{lang}">{body}</code></pre>')
            continue

        if re.fullmatch(r"\s*(---+|\*\*\*+|___+)\s*", ln):
            out.append("<hr>")
            i += 1
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            lv, txt = len(m.group(1)), m.group(2)
            if lv <= 2:
                kp_mode = ("要点" in txt) or ("key" in txt.lower())
            out.append(f"<h{lv}>{inline(txt)}</h{lv}>")
            i += 1
            continue

        # 表格
        if "|" in ln and i + 1 < len(lines) and is_sep(lines[i + 1]):
            head = split_row(ln)
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(split_row(lines[i]))
                i += 1
            if len(head) <= 3:
                th = "".join(f"<th>{inline(c)}</th>" for c in head)
                body = "".join(
                    "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>"
                    for r in rows)
                out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>")
            else:
                # 列太多 → 改成卡片，保证手机上不用横划
                out.append('<div class="cards">' + "".join(
                    '<div class="card"><div class="ct">' + inline(r[0]) + "</div>" +
                    "".join(
                        f'<div class="cr"><span class="ck">{inline(head[k])}：</span>'
                        f"{inline(r[k])}</div>"
                        for k in range(1, min(len(head), len(r))))
                    + "</div>" for r in rows) + "</div>")
            continue

        m = re.match(r"^\s*[-*+]\s+(.*)$", ln)
        if m:
            items = []
            while i < len(lines):
                mm = re.match(r"^\s*[-*+]\s+(.*)$", lines[i])
                if not mm:
                    break
                items.append(mm.group(1))
                i += 1
            if kp_mode:
                out.append('<div class="kps">' + "".join(
                    f'<div class="kp"><p>{inline(x)}</p></div>' for x in items) + "</div>")
            else:
                out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            continue

        m = re.match(r"^\s*\d+[.)]\s+(.*)$", ln)
        if m:
            items = []
            while i < len(lines):
                mm = re.match(r"^\s*\d+[.)]\s+(.*)$", lines[i])
                if not mm:
                    break
                items.append(mm.group(1))
                i += 1
            if kp_mode:
                out.append('<div class="kps">' + "".join(
                    f'<div class="kp"><p>{inline(x)}</p></div>' for x in items) + "</div>")
            elif items and all(re.fullmatch(r"\*\*[^*]+\*\*[：:]?", x.strip()) for x in items):
                # 整块都是「N. **小标题**」→ 渲染成分节标题，正文结构更清楚
                out.append('<div class="steps">' + "".join(
                    '<div class="step">' +
                    inline(re.sub(r"^\*\*([^*]+)\*\*[：:]?$", r"\1", x.strip())) +
                    "</div>" for x in items) + "</div>")
            else:
                out.append("<ol>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ol>")
            continue

        if ln.strip().startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            out.append("<blockquote>" + "".join(f"<p>{inline(b)}</p>" for b in buf) + "</blockquote>")
            continue

        if not ln.strip():
            i += 1
            continue

        out.append(f"<p>{inline(ln)}</p>")
        i += 1
    return "\n".join(out)


def build_html(body, title, sub, width, footer, pill="", extra_css=""):
    cover = ""
    if title:
        cover = ('<div class="cover">' +
                 (f'<div class="pill">{inline(pill)}</div>' if pill else "") +
                 "<h1>" + inline(title) + "</h1>" +
                 (f'<div class="sub">{inline(sub)}</div>' if sub else "") +
                 '<div class="rule"></div></div>')
    foot = f'<div class="foot">{inline(footer)}</div>' if footer else ""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}{extra_css}
   body{{width:{width}px}}</style></head><body><div class="topbar"></div><div class="wrap">{cover}{body}{foot}</div>
   <div id="__h" style="display:none"></div>
   <script>document.getElementById('__h').textContent='H='+document.documentElement.scrollHeight;</script>
   </body></html>"""


def chrome_height(path):
    r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                        "--hide-scrollbars", "--window-size=800,1200",
                        "--virtual-time-budget=1500", "--dump-dom", _file_url(path)],
                       capture_output=True)
    m = re.search(r"H=(\d+)", r.stdout.decode("utf-8", "replace"))
    return int(m.group(1)) if m else 0


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    src = args[0]
    out = None
    width = 720
    title = sub = None
    # 默认走 paper（她 2026-09-20 定稿：后面产出全用这套模板）；--style 可覆盖
    style = "paper"
    i = 1
    while i < len(args):
        if args[i] == "-o":
            out, i = args[i + 1], i + 2
        elif args[i] == "--width":
            width, i = int(args[i + 1]), i + 2
        elif args[i] == "--title":
            title, i = args[i + 1], i + 2
        elif args[i] == "--sub":
            sub, i = args[i + 1], i + 2
        elif args[i] == "--style":
            style, i = args[i + 1], i + 2
        else:
            i += 1
    if style and style not in STYLE_CSS:
        print(f"未知风格 {style}，可选：{' / '.join(STYLE_CSS)}（不传=默认 paper）")
        return

    md = open(src, encoding="utf-8").read()

    # 首行 h1 提到封面，避免「标题写两遍」（按行处理，不要用 $ 匹配多行串）
    lines = md.split("\n")
    pill = ""
    if lines and re.match(r"^\s*#\s+\S", lines[0]):
        if not title:
            title = re.sub(r"^\s*#\s+", "", lines[0]).strip()
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and re.search(r"来源\s*[:：]|归档\s*[:：]|字数\s*[:：]", re.sub(r"\*\*", "", lines[0])):
            # 元信息行：「**来源**：抖音 · 作者 ｜ **归档**：2026-09-20 ｜ **字数**：1042」
            raw = re.sub(r"\*\*", "", lines[0])
            m_src = m_arch = m_cnt = ""
            for part in re.split(r"[｜|]", raw):
                k, sep, v = part.partition("：")
                if not sep:
                    k, sep, v = part.partition(":")
                k, v = k.strip(), v.strip()
                if "来源" in k:
                    m_src = v
                elif "归档" in k:
                    m_arch = v
                elif "字数" in k:
                    m_cnt = v
            pill = m_src
            bits = [m_arch, (m_cnt if (not m_cnt or "字" in m_cnt) else m_cnt + " 字")]
            bits = [x for x in bits if x]
            if not sub and bits:
                sub = " · ".join(bits)
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)
        elif not sub and lines and len(lines[0].strip()) < 60 \
                and "·" in lines[0] and not lines[0].lstrip().startswith(("#", "-", "*", "|", ">")):
            sub = lines[0].strip()
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)
    md = "\n".join(lines)

    # 正文末尾自己写了落款就不再加一行
    auto_foot = not bool(re.search(r"\s*[·•]\s*\d{4}-\d{2}-\d{2}\s*$", md.rstrip()))

    base = os.path.splitext(os.path.basename(src))[0]
    out = out or os.path.join(os.path.dirname(os.path.abspath(src)), base + ".png")

    body = md_to_html(md)
    footer = ("Video2Draft · " + __import__("datetime").date.today().strftime("%Y-%m-%d")) if auto_foot else ""
    page = build_html(body, title, sub, width, footer, pill,
                      STYLE_CSS.get(style, ""))
    hp = os.path.join(os.path.dirname(out), "." + base + ".html")
    open(hp, "w", encoding="utf-8").write(page)

    h = chrome_height(hp)
    if h < 200:
        h = 1200
    h = min(h + 8, 30000)
    print(f"→ 画布 {width}×{h}")

    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                    "--hide-scrollbars", "--force-device-scale-factor=2",
                    f"--window-size={width},{h}",
                    "--virtual-time-budget=2500",
                    f"--screenshot={out}", _file_url(hp)], capture_output=True)

    # 裁掉底部空白（PIL 可选：没装就跳过裁剪，**不影响出图成功**）
    _trim_with_pil(out) if _has_pil() else print("  （未装 Pillow，跳过底部空白裁剪；不影响出图）")

    os.path.exists(hp) and os.unlink(hp)
    print(f"→ 出图 {out}  ({os.path.getsize(out)//1024} KB)")


def _has_pil():
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False


def _trim_with_pil(out):
    """裁掉底部空白：用当前解释器的 Pillow，失败就当没发生。"""
    from PIL import Image
    try:
        im = Image.open(out).convert("RGB")
        w, hh = im.size
        px = im.load()
        bg = px[5, 5]
        last = hh - 1
        while last > 0:
            row = [px[x, last] for x in range(0, w, max(1, w // 40))]
            if any(abs(c[0] - bg[0]) + abs(c[1] - bg[1]) + abs(c[2] - bg[2]) > 24 for c in row):
                break
            last -= 1
        im.crop((0, 0, w, min(hh, last + 40))).save(out)
        print(" 实际高度", min(hh, last + 40), "px")
    except Exception as e:
        print(f"  （裁剪跳过：{type(e).__name__}）")


if __name__ == "__main__":
    if not CHROME:
        raise SystemExit(
            "没有找到 Chrome/Chromium（出图要用它做无头截图）。\n"
            "   → 装一个 Chrome，或用环境变量 YJCG_CHROME 指定浏览器可执行文件路径。"
        )
    main()
