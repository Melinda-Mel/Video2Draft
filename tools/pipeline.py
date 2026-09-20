#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频号 → 文字稿 全自动流水线

用法:
    python3 pipeline.py "https://weixin.qq.com/sph/xxxx"
    python3 pipeline.py "https://weixin.qq.com/sph/xxxx" --keep-video

流程:
    1. 解析链接（拿到视频直链 + 解密种子）
    2. 下载并自动解密（.mp4）
    3. ffmpeg 提取音频（16kHz 单声道 wav）
    4. faster-whisper 本地转写（不出本机、免费）
    5. 输出文字稿到 output/ 目录

转写模型策略（用户硬性要求：优先消耗最低、当前免费）:
    - 只用**本机离线** faster-whisper，音频不出机器、不调任何云端 ASR、不花积分。
    - 默认 small（int8 量化 / CPU）：免费档里速度、准确率最平衡。
    - 需要更准可临时加 --model medium（同样免费，但慢 3~4 倍、内存更大），
      默认不要改。禁止替换为任何按量计费的云端转写服务。

前置:
    - wx_video_download 服务端在跑（127.0.0.1:2022），且微信 PC 在线
"""
import json
import os
import tempfile
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---- 环境（避开系统代理，否则本地请求会被 Clash 拦）----
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
# 模型已在本机缓存，强制离线：否则每次都会尝试联网校验（走代理会 502 重试，白白多耗十几秒）
os.environ.setdefault("HF_HUB_OFFLINE", "1")   # 首次下载模型时设 HF_HUB_OFFLINE=0

BASE = os.environ.get("YJCG_BASE", os.path.join(os.path.expanduser("~"), "yijianchenggao-workspace"))
TOOLS = f"{BASE}/tools"
OUT = f"{BASE}/output"

# 专有名词纠错（L1 热词偏置 + L2 确定性替换），词表见 fix_terms.py 的 TERMS
sys.path.insert(0, TOOLS)
import fix_terms  # noqa: E402

API = "http://127.0.0.1:2022"
FFMPEG = os.environ.get("YJCG_FFMPEG", "ffmpeg")   # 默认走 PATH，可用环境变量指定
NODE = os.environ.get("YJCG_NODE", "node")
MCP_CALL = f"{TOOLS}/mcp_call.mjs"          # MCP 调用小工具
WXCD_DIR = f"{TOOLS}/wxcd"
# 转写模型：固定走本机离线，零积分零花费。默认 small（int8/CPU）。
# 覆盖方式：环境变量 SPH_WHISPER_MODEL=medium，或命令行 --model medium。
# 档位（全是免费本地模型，差别只在速度和准确率）：
#   tiny    最快   准确率最低，只适合试链路
#   base    快     短句还行，专有名词易错
#   small   中等   ★默认，性价比最好
#   medium  慢 3~4倍  明显更少同音字错误
#   large   很慢   需要好机器，一般不必
# 绝不要换成云端按量计费的转写 API。
WHISPER_MODEL = os.environ.get("SPH_WHISPER_MODEL", "small")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def api(path, data=None, timeout=30, method=None):
    url = API + path
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(
        url, data=body, method=method or ("POST" if body else "GET"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def safe_name(s, n=60):
    s = re.sub(r"[\\/:*?\"<>|#\n\r\t]", "_", s).strip("_ ")
    return s[:n] or "video"


# ---------- 短链展开 ----------
# 手机分享出来的基本都是短链，而解析服务只认各平台完整 URL：
#   直接喂 b23.tv 会报「不支持的B站URL」；youtu.be / v.douyin.com 同理。
# 所以发去解析前先自己展开一次，用户在微信里怎么分享都能用。
SHORT_HOSTS = ("b23.tv", "youtu.be", "v.douyin.com", "xhslink.com",
               "v.kuaishou.com", "t.cn", "dwz.cn")
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def expand_short_url(url, max_hop=6):
    """短链 → 完整链接。任何一步失败都原样返回，绝不阻断主流程。"""
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
        # B站：分享链接带一大堆 buvid/mid/share_* 参数，归一化成纯净视频页更稳
        m = re.match(r"https?://(?:www\.)?bilibili\.com/video/(BV[0-9A-Za-z]+)", cur)
        if m:
            cur = f"https://www.bilibili.com/video/{m.group(1)}"
        if cur != url:
            log(f"   短链展开: {url} → {cur[:80]}")
        return cur
    except Exception as e:
        log(f"   短链展开跳过（{e}）")
        return url


# ---------- 抖音链接归一化 ----------
# 抖音短链 v.douyin.com 极不稳定（TUN 常把它路由到会 reset 的出口），而且
# scraper 的抖音适配器**解析不了短链**、也不认 www.douyin.com/video/<id>
# （报 `has no item_list`）。实测唯一稳的形式是 iesdouyin 分享链：
#   https://www.iesdouyin.com/share/video/<id>/
# 让 scraper 自己去啃短链，会白等它 PC/mobile/web 三个适配器各重试 5 次（约 50s）。
# 所以先自己把任意抖音链接归一化成 iesdouyin 形式，再交给 scraper。
DOUYIN_HOSTS = ("v.douyin.com", "douyin.com", "iesdouyin.com")


def douyin_canonical(url):
    """抖音链接/短链 → iesdouyin 完整分享链；拿不到就原样返回（不阻断主流程）。"""
    if not any(h in url for h in DOUYIN_HOSTS):
        return url
    m = re.search(r"/(?:share/)?video/(\d{6,})", url)
    if not m:
        # v.douyin.com 短链：跟随 302 取 video_id。
        # ⚡快速失败：总预算 12s 封顶。原先是 5 次 × (10s 超时 + 1s 等待) ≈ 55s，
        # 隧道一抖就要干等近一分钟，这是用户抱怨「慢」的最大来源（2026-09-20）。
        budget = time.time() + 12
        for _ in range(3):
            if time.time() > budget:
                break
            try:
                op = urllib.request.build_opener(_NoRedirect)
                req = urllib.request.Request(url, method="GET", headers={"User-Agent": UA})
                try:
                    resp = op.open(req, timeout=5)
                except urllib.error.HTTPError as e:
                    resp = e
                loc = resp.headers.get("Location") or ""
                m = re.search(r"/(?:share/)?video/(\d{6,})", loc)
                if m:
                    break
            except Exception:
                pass
            if time.time() < budget:
                time.sleep(0.4)
    if m:
        full = f"https://www.iesdouyin.com/share/video/{m.group(1)}/"
        if full != url:
            log(f"   抖音归一化: {url[:56]} → {full}")
        return full
    return url


# ---------- 1. 解析 ----------
# ⚡快速失败参数（2026-09-20 加）：以前交给 scraper 的 timeout_seconds=90，
# 而 scraper 遇到拉不通的域名时会 PC/mobile/web 三个适配器各重试 5 次、
# 每次等到超时才放弃 —— 实测干等约 50 秒才报错。现在压到 24s 封顶，
# 并且一拿到 failed 就立刻返回，不再把 36 行的轮询跑满。
SCRAPER_TIMEOUT = 24
# 命中这些字样说明是「网络/出口被掐」而非链接本身有问题，直接翻译成人话。
NET_HINTS = ("reset by peer", "connection reset", "eof", "timeout",
             "no such host", "connection refused", "tls", "ssl")


def resolve(url):
    log("① 解析链接…")
    url = douyin_canonical(url)
    # 抖音短链如果归一化没成功（还停在 v.douyin.com），**直接失败**：
    # scraper 的抖音适配器从来就解析不了短链，喂给它只会白等它重试。
    if "v.douyin.com" in url:
        raise RuntimeError(
            f"抖音短链展开失败，拿不到 video_id：{url}\n"
            "   → 多半是隧道/出口把 v.douyin.com 掐了（时好时坏）。稍后原样重发这条链接即可，不用换链接。"
        )
    url = expand_short_url(url)
    r = api("/api/scraper/fetch",
            {"url": url, "force_refresh": True, "timeout_seconds": SCRAPER_TIMEOUT})
    job = r["data"]["id"]
    # 先立刻查一次（很多短链 1 秒内就完成），之后 0.4s 轮询，别一上来就睡 2 秒
    deadline = time.time() + SCRAPER_TIMEOUT + 6
    for _ in range(int((SCRAPER_TIMEOUT + 6) / 0.4)):
        d = api(f"/api/scraper/job?id={job}")["data"]
        st = d.get("status")
        if st in ("completed", "success"):
            c = d["content"]
            log(f"   ✓ {c.get('title', '')[:40]}")
            log(f"     作者: {(d.get('account') or {}).get('nickname', '?')}")
            return job, d
        if st == "failed":
            err = str(d.get("error") or "")
            hint = ""
            if any(h in err.lower() for h in NET_HINTS):
                hint = "\n   → 像是本机隧道/出口把该域名掐了（不是链接问题）。稍后原样重发即可。"
            elif "item_list" in err.lower():
                hint = "\n   → 平台页面结构变了或触发了反爬。把完整分享链（不是短链）发我，我再试。"
            raise RuntimeError(f"解析失败: {err}{hint}")
        if time.time() > deadline:
            raise RuntimeError(
                f"解析超时：{SCRAPER_TIMEOUT + 6}s 内未完成，已提前中止（不再干等）。"
            )
        time.sleep(0.4)
    raise RuntimeError("解析超时")


# ---------- 2. 下载（工具自带解密）----------
def download(job, out_dir):
    log("② 下载并解密…")
    os.makedirs(out_dir, exist_ok=True)
    cmd = [NODE, MCP_CALL, "download_content",
           json.dumps({
               "job_id": job,
               "wait_for_completion": True,
               "download_dir": out_dir,
               # 同一视频重复处理时必须重新下，否则报「已存在该下载内容」
               "existing_action": "overwrite",
           }),
           "220000", "api"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    m = re.search(r"\{.*\}", p.stdout, re.S)
    if not m:
        raise RuntimeError(f"下载失败: {p.stdout[:300]} {p.stderr[:300]}")
    d = json.loads(m.group(0))
    files = (d.get("task") or {}).get("files") or []
    if not files:
        raise RuntimeError("下载未产出文件")
    fp = files[0]["file_path"]
    log(f"   ✓ {os.path.basename(fp)} ({files[0].get('size', 0) / 1048576:.1f} MB)")
    return fp


# ---------- 3. 提音频 ----------
def extract_audio(video, wav):
    log("③ 提取音频…")
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", video,
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav],
        check=True, timeout=600,
    )
    log(f"   ✓ {os.path.getsize(wav) / 1048576:.1f} MB")
    return wav


# ---------- 4. 转写 ----------
# 优先 mlx-whisper（Apple 神经引擎，比 CPU 快 3~5 倍）；不可用时退回 faster-whisper CPU。
# 注意：mlx 模型需要从 HuggingFace 拉一次，所以用它时不强制离线。
#
# ⚡2026-09-20 提速①（**已默认关闭**）：常驻服务 whisperd 只省约 1s（http://127.0.0.1:2024）。
# 实测收益远小于当初估计的 7.7s，却要额外背一个后台进程 + 自愈拉起 + 回退维护成本，
# 她 2026-09-20 明确要求放弃。**默认不启用**：只有显式给 SPH_WHISPERD=http://127.0.0.1:2024
# 时才会走热服务；将来真要高频批量转写，再把它打开即可（whisperd start）。
# 走不走热服务都不影响结果，拿不到永远静默回落下面的 subprocess 冷启动。
WHISPERD = os.environ.get("SPH_WHISPERD", "")
WHISPERD_TIMEOUT = int(os.environ.get("SPH_WHISPERD_TIMEOUT", "1800"))
# 健康检查只等 1.5s：服务没起来时立刻放弃，别在连不上这件事上耗时间
WHISPERD_PROBE = 1.5
SPH_PY = os.environ.get("YJCG_PYTHON", sys.executable)
WHISPER_SERVER = f"{TOOLS}/whisper_server.py"
TMP = tempfile.gettempdir()          # 跨平台临时目录（Windows 下是 %TEMP%）
WHISPERD_LOCK = os.path.join(TMP, ".whisperd.boot.lock")


def _hot_health():
    """探活。服务没起是常态，返回 None，不刷日志吓人。"""
    try:
        with urllib.request.urlopen(WHISPERD + "/health", timeout=WHISPERD_PROBE) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def ensure_whisperd():
    """没起就 detached 拉起常驻转写服务。

    注意：**不等它预热完**——本次请求照旧走冷启动 subprocess，绝不当场变慢；
    下一条视频就吃到热模型了。这是权宜之计：本机 launchd 域当前拒绝 bootstrap
    （`launchctl bootstrap` 报 5: Input/output error，连最小 plist 也一样），
    所以改成「用之前自己醒过来」的自愈模式，比守着 LaunchAgent 更省事。
    """
    h = _hot_health()
    if h and h.get("ready"):
        return True
    try:
        if time.time() - os.path.getmtime(WHISPERD_LOCK) < 90:
            return False                 # 刚有人拉过（或正在拉），别重复起
    except OSError:
        pass
    try:
        with open(WHISPERD_LOCK, "w") as f:
            f.write(str(time.time()))
        with open(os.path.join(TMP, "whisperd.spawn.log"), "ab") as lf:
            subprocess.Popen([SPH_PY, WHISPER_SERVER], cwd=TOOLS,
                             stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
                             start_new_session=True)
        log("   （whisperd 没在跑，已顺手拉起；本条仍走冷启动，下条就快了）")
    except Exception as e:
        log(f"   （whisperd 拉起失败：{type(e).__name__}: {e}）")
    return False


def transcribe_hot(wav):
    """常驻服务转写；任何异常都返回 None（调用方自动回落 subprocess）。"""
    if not WHISPERD:
        return None                      # 默认关闭，不启服务、不探活、不留日志
    h = _hot_health()
    if not h:
        ensure_whisperd()
        return None
    if not h.get("ready"):
        log("   （whisperd 还在预热，本次走冷启动）")
        return None

    try:
        t0 = time.time()
        body = json.dumps({"wav": wav}).encode("utf-8")
        req = urllib.request.Request(
            WHISPERD + "/transcribe", data=body, method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=WHISPERD_TIMEOUT) as r:
            d = json.loads(r.read().decode("utf-8"))
        if d.get("ok") and d.get("segments"):
            log(f"   ✓ [whisperd/{d.get('used')}] 热转写 {d.get('cost')}s，"
                f"{len(d['segments'])} 段（省掉模型加载）")
            return d["segments"]
        log(f"   （whisperd 返回异常，回落冷启动：{str(d.get('err'))[:120]}）")
    except Exception as e:
        log(f"   （whisperd 不可用，回落冷启动：{type(e).__name__}，累计 {time.time()-t0:.1f}s）")
    return None


def _transcribe_raw(wav):
    log("④ 本地转写（优先 mlx 神经引擎，否则 CPU）…")
    hot = transcribe_hot(wav)
    if hot:
        return hot
    # L1 热词偏置：把专有名词表喂给 whisper，让它在解码阶段就倾向正确写法（词表见 fix_terms.py）
    HOT = json.dumps(fix_terms.hotwords_prompt(), ensure_ascii=False)
    script = f'''
import time, json, os
t0 = time.time()
segs = []
used = "cpu"
try:
    import mlx_whisper
    # 模型缓存在 ~/.cache/huggingface（mlx-community/whisper-large-v3-turbo）。
    # 放开离线限制以避免"缓存被判为过期"的边界情况，但必须指国内镜像 + 关 Xet CDN，
    # 否则 HF 的 Xet 直连会报 CAS Client Error（2026-09-20 实测）。缓存命中时不会真的联网。
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    # 固定用 large-v3-turbo：GPU 上实测 245s 音频只要 16s，比 small 的 CPU 还快，
    # 准确率却高一个档次，所以这里**不再跟随 --model**（--model 只作用于下面的 CPU 回退）。
    _repo = "mlx-community/whisper-large-v3-turbo"
    # language=None = 自动检测。**不要写死 "zh"**：英文视频会被按中文解码成乱码（已踩过）。
    # ⚠️ 也**不要传 beam_size**：mlx_whisper 0.4.3 没实现 beam search，
    #    传任何 beam_size 都会抛 NotImplementedError: Beam search decoder is not yet implemented
    #    ——这正是本分支长期静默失败、一直回退 CPU 的真正原因（2026-09-20 找到）。
    #    它默认走 greedy，速度与质量都够用。
    res = mlx_whisper.transcribe("{wav}", path_or_hf_repo=_repo,
                                 language=None,
                                 initial_prompt={HOT})
    _raw = res[0] if isinstance(res, tuple) else res.get("segments", [])
    for s in _raw:
        segs.append({{"start": round(getattr(s,'start',0),1),
                      "end": round(getattr(s,'end',0),1),
                      "text": (s.text if hasattr(s,'text') else s.get('text','')).strip()}})
    used = "mlx"
except Exception as e:
    from faster_whisper import WhisperModel
    m = WhisperModel("{WHISPER_MODEL}", device="cpu", compute_type="int8", cpu_threads=4)
    r, info = m.transcribe("{wav}", language="zh", vad_filter=True, beam_size=1,
                           condition_on_previous_text=False,
                           initial_prompt={HOT})
    for s in r:
        segs.append({{"start": round(s.start,1), "end": round(s.end,1), "text": s.text.strip()}})
print("@@JSON@@" + json.dumps({{"segments": segs, "cost": round(time.time()-t0,1), "used": used}}, ensure_ascii=False))
'''
    p = subprocess.run([os.environ.get("YJCG_PYTHON", sys.executable), "-c", script],
                       capture_output=True, text=True, timeout=3600)
    m = re.search(r"@@JSON@@(\{.*\})", p.stdout, re.S)
    if not m:
        raise RuntimeError(f"转写失败: {p.stdout[-400:]} {p.stderr[-400:]}")
    d = json.loads(m.group(1))
    log(f"   ✓ [{d.get('used')}] 耗时 {d['cost']}s，{len(d['segments'])} 段")
    return d["segments"]


def transcribe(wav):
    """对外唯一入口：转写 → L2 专有名词纠错 → 可疑词巡检。

    所有平台（抖音/视频号/B站/YouTube/微博/X/小红书）都从这里出稿，
    所以纠错只在这一处做，不必每个脚本各改一遍。
    """
    segs = _transcribe_raw(wav)
    n = fix_terms.fix_segments(segs)
    if n:
        log(f"   ✓ 专有名词纠正 {n} 处（GV→Jev、Worker Body→WorkBuddy 这类，词表在 fix_terms.py）")
    segs, cut = fix_terms.trim_loops(segs)
    if cut:
        log(f"   ✓ 复读幻觉清理 {cut} 段（结尾纯音乐上 whisper 会连吐同一句）")
    sus = fix_terms.suspects("".join(s.get("text", "") for s in segs))
    if sus:
        log(f"   ⚠️ 待确认英文词：{'、'.join(sus)}"
            f"（确是专有名词就加进 fix_terms.py 的 TERMS，下次自动纠）")
    return segs


# ---------- 主流程 ----------
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    global WHISPER_MODEL
    url = sys.argv[1]
    keep_video = "--keep-video" in sys.argv
    if "--model" in sys.argv:
        try:
            WHISPER_MODEL = sys.argv[sys.argv.index("--model") + 1]
        except Exception:
            pass
    log(f"转写模型: {WHISPER_MODEL}（本地离线，零积分）")

    t0 = time.time()
    job, meta = resolve(url)
    c = meta["content"]
    acc = meta.get("account") or {}
    # 平台把「标题 + 话题标签」塞在同一个字段里，拆开用
    _parts = [p.strip() for p in (c.get("title") or "").split("\n") if p.strip()]
    title = _parts[0] if _parts else "未命名"
    tags = next((p for p in _parts[1:] if p.startswith("#")), "")

    video = download(job, OUT)
    wav = os.path.join(TMP, f"{safe_name(title, 30)}.wav")
    extract_audio(video, wav)
    segs = transcribe(wav)

    text = "".join(s["text"] for s in segs)
    stem = safe_name(title, 80)
    md = f"{OUT}/{stem}.md"

    with open(md, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        if tags:
            f.write(f"{tags}\n\n")
        f.write(f"- **作者**：{acc.get('nickname', '')}\n")
        if acc.get("signature"):
            f.write(f"- **简介**：{acc['signature'].splitlines()[0]}\n")
        if c.get("publish_time"):
            f.write(f"- **发布**：{time.strftime('%Y-%m-%d', time.localtime(c['publish_time']))}\n")
        f.write(f"- **原链接**：{url}\n\n")
        f.write("## 口播全文\n\n" + text + "\n\n")
        f.write("## 分段对照\n\n")
        for s in segs:
            f.write(f"- [{s['start']:.0f}s] {s['text']}\n")

    with open(f"{OUT}/{stem}.txt", "w", encoding="utf-8") as f:
        f.write(text)

    if not keep_video and os.path.exists(video):
        os.remove(video)
        log("   已删除视频源文件（加 --keep-video 可保留）")

    log(f"⑤ 完成！总耗时 {time.time() - t0:.0f}s")
    log(f"   文字稿: {md}")
    log(f"   纯文本: {OUT}/{stem}.txt")

    duration = round(segs[-1]["end"]) if segs else 0
    result = {
        "ok": True,
        "title": title,
        "author": acc.get("nickname", ""),
        "tags": tags,
        "duration": duration,
        "chars": len(text),
        "segments": segs,
        "text": text,
        "md_path": md,
        "txt_path": f"{OUT}/{stem}.txt",
        "cost": round(time.time() - t0),
    }
    print("@@RESULT@@" + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
