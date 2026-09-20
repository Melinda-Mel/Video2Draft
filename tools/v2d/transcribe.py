# -*- coding: utf-8 -*-
"""转写：默认走本机离线 faster-whisper（CPU / int8），Apple 芯片有 mlx 就先用 mlx。

设计（2026-09-20 重构）：
- 不再依赖任何本地常驻服务（原热转写服务已不再是必需项）。
- 转写在**当前解释器**里跑（`config.python_exe()`），新机器 clone 后装好依赖即可。
- 首次运行会自动下载模型（体积：tiny≈75MB / small≈500MB）；离线环境可设
  YJCG_WHISPER_LOCAL_ONLY=1 + 预置模型目录。
"""
from __future__ import annotations

import json
import os
import subprocess
import re
from pathlib import Path

from v2d import config


def _load_fix_terms():
    """复用仓库里的术语库（纠错 + whisper 热词偏置）。"""
    import sys
    tools_dir = str(config.CODE_DIR)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import fix_terms  # noqa: E402
    return fix_terms


def transcribe(wav: str, model: str | None = None, language: str | None = None) -> list:
    """wav → [{"start","end","text"}]，并做专有名词纠错与复读清理。"""
    model = model or config.whisper_model()
    raw = _transcribe_raw(wav, model, language)

    ft = _load_fix_terms()
    n = ft.fix_segments(raw)
    if n:
        print(f"   ✓ 专有名词纠正 {n} 处（词表在 tools/fix_terms.py）", flush=True)
    segs, cut = ft.trim_loops(raw)
    if cut:
        print(f"   ✓ 复读幻觉清理 {cut} 段", flush=True)
    return segs


def _transcribe_raw(wav: str, model: str, language: str | None) -> list:
    ft = _load_fix_terms()
    hot = json.dumps(ft.hotwords_prompt(), ensure_ascii=False)
    local_only = os.environ.get("YJCG_WHISPER_LOCAL_ONLY") == "1"
    # 注意：这里是 **Python 源码字面量**，不是 JSON —— None 必须写 None，写 null 会 NameError
    lang = "None" if language is None else repr(language)

    script = f'''
import json, os, time
t0 = time.time()
segs, used = [], "cpu"
if not os.environ.get("YJCG_NO_MLX"):
    try:
        import mlx_whisper
        os.environ.pop("HF_HUB_OFFLINE", None)
        res = mlx_whisper.transcribe({wav!r}, path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
                                     language={lang}, initial_prompt={hot})
        _raw = res[0] if isinstance(res, tuple) else res.get("segments", [])
        for s in _raw:
            segs.append({{"start": round(getattr(s, "start", 0), 1),
                          "end": round(getattr(s, "end", 0), 1),
                          "text": (s.text if hasattr(s, "text") else s.get("text", "")).strip()}})
        used = "mlx"
    except Exception:
        segs = []
try:
    if not segs:
        from faster_whisper import WhisperModel
        m = WhisperModel({model!r}, device="cpu", compute_type="int8", cpu_threads=4)
        r, info = m.transcribe({wav!r}, language={lang}, vad_filter=True, beam_size=1,
                               condition_on_previous_text=False, initial_prompt={hot})
        for s in r:
            segs.append({{"start": round(s.start, 1), "end": round(s.end, 1), "text": s.text.strip()}})
        used = "cpu"
except Exception as e:
    print("@@ERR@@" + f"{{type(e).__name__}}: {{e}}")
    raise
print("@@JSON@@" + json.dumps({{"segments": segs, "cost": round(time.time() - t0, 1), "used": used}},
                             ensure_ascii=False))
'''
    env = dict(os.environ)
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    env.setdefault("HF_HUB_DISABLE_XET", "1")
    if local_only:
        env["HF_HUB_OFFLINE"] = "1"
    p = subprocess.run([config.python_exe(), "-c", script],
                       capture_output=True, text=True, timeout=7200, env=env)
    m = re.search(r"@@JSON@@(\{.*\})", p.stdout or "", re.S)
    if not m:
        err = ""
        me = re.search(r"@@ERR@@(.*)", p.stdout or "")
        if me:
            err = me.group(1).strip()
        if "ModuleNotFoundError" in (err + (p.stderr or "")):
            raise RuntimeError(
                "转写依赖没装（缺 faster-whisper）。\n"
                "   → 修复：pip install -r requirements.txt"
            )
        raise RuntimeError(f"转写失败：{err or (p.stdout or '')[-300:]} {(p.stderr or '')[-300:]}")
    d = json.loads(m.group(1))
    print(f"   ✓ [{d.get('used')}] 转写耗时 {d['cost']}s，{len(d['segments'])} 段", flush=True)
    return d["segments"]


def available() -> tuple[bool, str]:
    """自检：转写能力是否就绪（不下载模型，只探依赖）。"""
    p = subprocess.run(
        [config.python_exe(), "-c",
         "import importlib;\n"
         "ok = []\n"
         "for m in ('faster_whisper','mlx_whisper'):\n"
         "    try:\n"
         "        importlib.import_module(m); ok.append(m)\n"
         "    except Exception: pass\n"
         "print(','.join(ok))"],
        capture_output=True, text=True, timeout=120)
    mods = (p.stdout or "").strip()
    if not mods:
        return False, "未安装转写依赖：pip install -r requirements.txt（需 faster-whisper）"
    return True, f"可用：{mods}"
