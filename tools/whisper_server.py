#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""whisperd —— 常驻转写服务（本地、离线、零积分）。

为什么要有它（2026-09-20 性能剖析）：
    mlx-whisper 的 large-v3-turbo 权重 1.5GB，每新起一个进程都要重新读进统一内存，
    实测固定吃掉约 7.7s（冷启动 17.1s vs 同进程第二次 9.4s）。
    把模型常驻成一个小 HTTP 服务后，pipeline.transcribe() 先问它要结果，
    命中就省掉这 7.7s —— 一条视频从 25s 量级降到 17s 量级。

红线（不许破，来自 AGENTS.md 的用户硬性要求）：
    * 全程本机 mlx / faster-whisper，音频不出机器、不调云端 ASR、不花一分钱。
    * mlx 分支**不传 beam_size**（没实现，传了抛 NotImplementedError 并静默回退 CPU）。
    * **不写死 language="zh"**（英文视频会被按中文解码成乱码）。
    * 只监听 127.0.0.1。
    * 失败必须是「回落」而不是「卡死」：pipeline 侧超时/连不上就退回原来的 subprocess 路径。

接口：
    GET  /health
        → {"ok":true,"ready":true,"backend":"mlx","model":"...","n":12,"last_cost":9.4}
    POST /transcribe   {"wav": "/abs/path.wav"}
        → {"ok":true,"segments":[{"start":0.0,"end":3.2,"text":"..."}],"cost":9.4,"used":"mlx"}
        → 失败 {"ok":false,"err":"..."}（HTTP 4xx/5xx）

用法（由 LaunchAgent com.ilenia.whisperd 托管，别在前台长期跑）：
    启动  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ilenia.whisperd.plist
    停止  launchctl bootout gui/$(id -u)/com.ilenia.whisperd
    健康  curl -s --noproxy '*' http://127.0.0.1:2024/health
"""
import json
import os
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---- 环境：与 pipeline 的 mlx 分支保持一致 ----
# 放开离线限制以避免「缓存被判过期」的边界情况；指国内镜像 + 关 Xet CDN。
# 模型已在本机缓存，命中时不会真的联网。
os.environ.pop("HF_HUB_OFFLINE", None)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

PORT = int(os.environ.get("SPH_WHISPERD_PORT", "2024"))
MLX_REPO = os.environ.get("SPH_WHISPERD_REPO", "mlx-community/whisper-large-v3-turbo")
CPU_MODEL = os.environ.get("SPH_WHISPER_MODEL", "small")
PROMPT = "以下是普通话视频口播内容。"
SILENCE = "/tmp/.whisperd_silence.wav"

_lock = threading.Lock()          # 一次只跑一条，避免抢 GPU / 内存
_state = {"ready": False, "backend": None, "model": None,
          "n": 0, "last_cost": None, "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}
_mlx = None
_fw = None


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _silence_wav(sec=1.0, sr=16000):
    """1 秒静音 wav：只用来把模型权重逼进内存，不产生任何实际转写内容。"""
    if not os.path.exists(SILENCE):
        with wave.open(SILENCE, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(b"\x00\x00" * int(sr * sec))
    return SILENCE


def _load_mlx():
    global _mlx
    import mlx_whisper
    t0 = time.time()
    # ⚠️ 不要传 beam_size；不要写死 language。理由见文件头红线。
    mlx_whisper.transcribe(_silence_wav(), path_or_hf_repo=MLX_REPO,
                           language=None, initial_prompt=PROMPT)
    _mlx = mlx_whisper
    log(f"mlx 预热完成 {time.time()-t0:.1f}s（之后每次转写都省掉这段加载）")


def _load_cpu():
    global _fw
    from faster_whisper import WhisperModel
    t0 = time.time()
    _fw = WhisperModel(CPU_MODEL, device="cpu", compute_type="int8", cpu_threads=4)
    # WhisperModel 是懒加载，真起权重要等第一次 transcribe，所以这里也喂一次静音
    list(_fw.transcribe(_silence_wav(), beam_size=1, condition_on_previous_text=False)[0])
    log(f"faster-whisper {CPU_MODEL}(int8/CPU) 预热完成 {time.time()-t0:.1f}s")


def warmup():
    try:
        _load_mlx()
        _state.update(ready=True, backend="mlx", model=MLX_REPO)
    except Exception as e:
        log(f"mlx 不可用（{type(e).__name__}: {e}）→ 回退 faster-whisper CPU")
        try:
            _load_cpu()
            _state.update(ready=True, backend="fw", model=CPU_MODEL)
        except Exception as e2:
            log(f"CPU 回退也失败：{type(e2).__name__}: {e2}")
    if _state["ready"]:
        log(f"就绪 [{'mlx' if _state['backend']=='mlx' else 'CPU'}] "
            f"{_state['model']}，监听 127.0.0.1:{PORT}")


def _run_mlx(wav):
    res = _mlx.transcribe(wav, path_or_hf_repo=MLX_REPO, language=None, initial_prompt=PROMPT)
    raw = res[0] if isinstance(res, tuple) else res.get("segments", [])
    out = []
    for s in raw:
        out.append({"start": round(getattr(s, "start", 0) or 0, 1),
                    "end": round(getattr(s, "end", 0) or 0, 1),
                    "text": (s.text if hasattr(s, "text") else s.get("text", "")).strip()})
    return out


def _run_cpu(wav):
    r, _info = _fw.transcribe(wav, beam_size=1, condition_on_previous_text=False,
                              initial_prompt=PROMPT)
    out = []
    for s in r:
        out.append({"start": round(s.start, 1), "end": round(s.end, 1),
                    "text": s.text.strip()})
    return out


def do_transcribe(wav):
    with _lock:
        t0 = time.time()
        used = _state["backend"]
        try:
            segs = _run_mlx(wav) if used == "mlx" else _run_cpu(wav)
        except Exception as e:
            if used == "mlx":
                # mlx 这条突然挂了（升级/模型坏了）：当场切 CPU 重试，别让调用方失败
                log(f"mlx 转写失败（{type(e).__name__}: {e}）→ 本次改用 CPU")
                try:
                    _load_cpu()
                except Exception as e2:
                    raise RuntimeError(f"mlx 失败且 CPU 回退失败: {e2}")
                _state.update(backend="fw", model=CPU_MODEL)
                used = "fw"
                segs = _run_cpu(wav)
            else:
                raise
        return {"segments": segs, "cost": round(time.time() - t0, 1), "used": used}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):        # 别把每条请求刷进日志
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.split("?")[0] in ("/health", "/"):
            self._json({"ok": True, **_state})
        else:
            self._json({"ok": False, "err": "not found"}, 404)

    def do_POST(self):
        if self.path.split("?")[0] != "/transcribe":
            self._json({"ok": False, "err": "not found"}, 404)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json({"ok": False, "err": "请求体不是合法 JSON"}, 400)
            return
        wav = req.get("wav") or ""
        if not wav or not os.path.exists(wav):
            self._json({"ok": False, "err": f"wav 不存在: {wav}"}, 400)
            return
        if not _state["ready"]:
            self._json({"ok": False, "err": "模型还在加载中"}, 503)
            return
        try:
            d = do_transcribe(wav)
            _state["n"] += 1
            _state["last_cost"] = d["cost"]
            log(f"转写 {os.path.basename(wav)} → {len(d['segments'])} 段 / {d['cost']}s [{d['used']}]")
            self._json({"ok": True, **d})
        except Exception as e:
            log(f"转写失败：{type(e).__name__}: {e}")
            self._json({"ok": False, "err": f"{type(e).__name__}: {e}"}, 500)


def main():
    log(f"whisperd 启动，python={sys.executable}")
    threading.Thread(target=warmup, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
