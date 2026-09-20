# -*- coding: utf-8 -*-
"""CI 端到端（不依赖任何视频网站）：合成语音 → ffmpeg 抽音频 → 转写 → MD → PNG。

为什么单独有这一条：真实 YouTube 端到端受出口 IP 影响（数据中心 IP 常被反爬拦截），
而本脚本用**操作系统自带的语音合成**造一段真实人声，把剩下的全链路（ffmpeg、
faster-whisper 推理、成稿、出图）在 macOS 与 Windows 上各跑一遍，结论稳定可比。

用法：
    python tests/ci_e2e_pipeline.py [--model tiny]

退出码：0 = 管线端到端通过（MD 必产出；无浏览器时 PNG 记 SKIPPED 并给告警）
        1 = 管线端到端失败
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

PHRASE = (
    "Hello, this is a cross platform pipeline test for the Video2Draft project. "
    "Whisper should turn this sentence into readable text, and the renderer should "
    "produce a long image from the markdown file."
)


def step(msg: str) -> None:
    print(f"--- {msg}", flush=True)


def summary(lines: list[str]) -> None:
    """把结果写进 GitHub Actions 的 Job Summary（本地跑时静默忽略）。"""
    dst = os.environ.get("GITHUB_STEP_SUMMARY")
    if dst:
        with open(dst, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def emit_b64(prefix: str, text: str, chunk: int = 300, maxn: int = 8, level: str = "notice") -> None:
    """把结果以 base64 分片发成 annotation：纯 ASCII，公共仓库免登录即可从 API 读到。"""
    import base64

    b = base64.b64encode(text.encode("utf-8", "replace")).decode("ascii")
    parts = [b[i:i + chunk] for i in range(0, len(b), chunk)][-maxn:]
    for n, p in enumerate(parts, 1):
        print(f"::{level}::{prefix}[{n}/{len(parts)}]{p}")


def mean_volume(ffmpeg_exe: str, wav: Path) -> float | None:
    """用 ffmpeg 量平均音量：区分「TTS 出来是静音」还是「模型没识别出字」。"""
    try:
        p = subprocess.run([ffmpeg_exe, "-hide_banner", "-i", str(wav),
                            "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", p.stderr or "")
        return float(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        return None


def make_speech_wav(raw: Path) -> None:
    """用操作系统自带语音合成生成一段真实人声（不联网、不需要额外模型）。"""
    if sys.platform == "darwin":
        aiff = raw.with_suffix(".aiff")
        subprocess.run(["say", "-o", str(aiff), PHRASE], check=True)
        raw.write_bytes(aiff.read_bytes())
        aiff.unlink(missing_ok=True)
        return

    if os.name == "nt":
        ps1 = raw.with_suffix(".ps1")
        ps1.write_text(
            "Add-Type -AssemblyName System.Speech\n"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
            f'$s.SetOutputToWaveFile("{raw}")\n'
            f'$s.Speak("{PHRASE}")\n'
            "$s.Dispose()\n",
            encoding="utf-8-sig",
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
            check=True,
        )
        return

    # Linux：优先 espeak-ng / espeak
    import shutil

    for exe in ("espeak-ng", "espeak"):
        if shutil.which(exe):
            subprocess.run([exe, "-w", str(raw), PHRASE], check=True)
            return
    raise RuntimeError("这台机器没有可用的语音合成（macOS: say / Windows: SAPI / Linux: espeak）")


def main() -> int:
    model = "tiny"
    if "--model" in sys.argv:
        model = sys.argv[sys.argv.index("--model") + 1]

    from v2d import audio, config, render, transcribe
    import format_doc

    out_dir = config.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="v2d_ci_"))
    print(f"平台: {sys.platform} | Python: {sys.version.split()[0]} | 输出目录: {out_dir}")

    lines = [f"### 管线端到端（{sys.platform}）", "", "| 步骤 | 结果 |", "|---|---|"]
    ok_all = True

    # ① ffmpeg
    step("① 检查 ffmpeg")
    exe = config.find_ffmpeg()
    print(f"   ffmpeg: {exe or '未找到'}")
    lines.append(f"| ffmpeg | {'✓ ' + str(exe) if exe else '✗ 未找到'} |")
    if not exe:
        print(audio.FFMPEG_HINT)
        print("::error::管线端到端失败：找不到 ffmpeg（" + sys.platform + "）")
        lines.append("| 结论 | ✗ 失败（缺 ffmpeg） |")
        summary(lines)
        return 1

    # ② 合成语音 + 抽音频（走仓库里的 extract_audio，真的调用 ffmpeg）
    step("② 合成语音 → ffmpeg 抽 16k wav")
    raw = work / "speech_raw.wav"
    try:
        make_speech_wav(raw)
    except Exception as e:  # noqa: BLE001
        print("::error::语音合成失败（" + sys.platform + "）：" + f"{type(e).__name__}: {e}")
        lines.append("| 语音合成 | ✗ |")
        summary(lines)
        return 1
    wav = work / "speech16k.wav"
    try:
        audio.extract_audio(str(raw), str(wav))
    except Exception as e:  # noqa: BLE001
        print("::error::ffmpeg 抽音频失败（" + sys.platform + "）：" + f"{type(e).__name__}: {e}")
        lines.append("| ffmpeg 抽音频 | ✗ |")
        summary(lines)
        return 1
    size = wav.stat().st_size if wav.exists() else 0
    print(f"   wav: {size} 字节")
    lines.append(f"| ffmpeg 抽音频 | {'✓ ' + str(size) + ' 字节' if size else '✗'} |")
    ok_all &= size > 0

    # ③ 转写（真跑 faster-whisper；短视频偶发空转写，做有限重试并查音量）
    step(f"③ 转写（faster-whisper {model}）")
    t0 = time.time()
    text, segs, tried = "", [], []
    for mdl, lang in [(model, "en"), (model, None), ("base", "en"), ("base", None)]:
        try:
            segs = transcribe.transcribe(str(wav), model=mdl, language=lang)
        except Exception as e:  # noqa: BLE001
            tried.append(f"{mdl}/lang={lang}: {type(e).__name__}: {e}"[:200])
            continue
        text = "".join(s["text"] for s in segs).strip()
        tried.append(f"{mdl}/lang={lang}: {len(text)} 字")
        print(f"   {mdl}/lang={lang} → {text[:120]!r}")
        if text:
            break
    print(f"   尝试记录: {tried}")
    lines.append(f"| 转写 | {'✓ ' + str(len(text)) + ' 字 / ' + str(round(time.time() - t0, 1)) + 's' if text else '✗ 空文本'} |")
    if not text:
        vol = mean_volume(exe, wav)
        print(f"::error::转写结果为空（{sys.platform}）：音频 {size} 字节、平均音量 {vol} dB，4 次尝试均空")
        emit_b64("PIPEFAIL", json.dumps({
            "platform": sys.platform, "python": sys.version.split()[0], "model": model,
            "wav_bytes": size, "mean_volume_db": vol, "tried": tried,
        }, ensure_ascii=False, indent=2), level="error")
        summary(lines)
        return 1

    # ④ 成稿（无 DeepSeek Key 时自动退回原始稿，这里正是无 Key 路径）
    step("④ 生成 MD")
    duration = int(segs[-1]["end"]) if segs else 0
    doc = format_doc.format_transcript("CI 管线测试", "CI", duration, len(text), "", text)
    md = out_dir / "ci_pipeline.md"
    md.write_text(doc, encoding="utf-8")
    print(f"   md: {md}（{md.stat().st_size} 字节）")
    lines.append(f"| 生成 MD | {'✓ ' + str(md.stat().st_size) + ' 字节' if md.exists() else '✗'} |")
    ok_all &= md.exists()
    if not md.exists():
        print("::error::MD 未生成（" + sys.platform + "）")
        summary(lines)
        return 1

    # ⑤ 出图（失败不影响 MD；CI 里无浏览器就记 SKIPPED 并列告警）
    step("⑤ 生成 PNG")
    chrome = config.find_chrome()
    ok_png, png, msg = render.render(str(md))
    if ok_png and png and Path(png).exists():
        print(f"   png: {png}（{Path(png).stat().st_size} 字节）")
        lines.append(f"| 生成 PNG | ✓ {Path(png).stat().st_size} 字节 |")
        print(f"::notice::PNG 已生成：{Path(png).name}")
    else:
        print(f"   png 失败: {msg}（chrome={chrome}）")
        lines.append(f"| 生成 PNG | ⚠ SKIPPED（无可用浏览器） |")
        print(f"::warning::PNG 未生成（{msg}）——MD 仍已产出，符合“出图失败不判失败”的设计")
        if chrome:
            ok_all = False  # 有浏览器却出图失败 = 真 bug
            print("::error::PNG 出图失败（" + sys.platform + "，浏览器=" + str(chrome) + "）：" + str(msg)[:600])

    lines.append(f"| MD 是否保留 | {'✓' if md.exists() else '✗'} |")
    lines.append(f"| 结论 | {'✓ 通过' if (ok_all and ok_png) else ('⚠ 部分通过' if ok_all else '✗ 失败')} |")
    summary(lines)

    # 证据回传：产物大小 / 浏览器 / ffmpeg / 转写后端（base64 notice，免登录可读）
    emit_b64("PIPEPASS", json.dumps({
        "platform": sys.platform, "python": sys.version.split()[0],
        "ffmpeg": exe, "chrome": chrome,
        "md": md.name, "md_bytes": md.stat().st_size if md.exists() else None,
        "png": Path(png).name if (ok_png and png) else None,
        "png_bytes": Path(png).stat().st_size if (ok_png and png and Path(png).exists()) else None,
        "transcript_chars": len(text), "transcript": text[:200],
        "model": model,
    }, ensure_ascii=False, indent=2), level="notice")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
