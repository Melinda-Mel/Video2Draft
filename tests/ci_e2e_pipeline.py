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

import os
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
        lines.append("| 结论 | ✗ 失败（缺 ffmpeg） |")
        summary(lines)
        return 1

    # ② 合成语音 + 抽音频（走仓库里的 extract_audio，真的调用 ffmpeg）
    step("② 合成语音 → ffmpeg 抽 16k wav")
    raw = work / "speech_raw.wav"
    make_speech_wav(raw)
    wav = work / "speech16k.wav"
    audio.extract_audio(str(raw), str(wav))
    size = wav.stat().st_size if wav.exists() else 0
    print(f"   wav: {size} 字节")
    lines.append(f"| ffmpeg 抽音频 | {'✓ ' + str(size) + ' 字节' if size else '✗'} |")
    ok_all &= size > 0

    # ③ 转写（真跑 faster-whisper）
    step(f"③ 转写（faster-whisper {model}）")
    t0 = time.time()
    segs = transcribe.transcribe(str(wav), model=model)
    text = "".join(s["text"] for s in segs).strip()
    print(f"   结果: {text[:200]!r}")
    lines.append(f"| 转写 | {'✓ ' + str(len(text)) + ' 字 / ' + str(round(time.time() - t0, 1)) + 's' if text else '✗ 空文本'} |")
    ok_all &= bool(text)

    # ④ 成稿（无 DeepSeek Key 时自动退回原始稿，这里正是无 Key 路径）
    step("④ 生成 MD")
    duration = int(segs[-1]["end"]) if segs else 0
    doc = format_doc.format_transcript("CI 管线测试", "CI", duration, len(text), "", text)
    md = out_dir / "ci_pipeline.md"
    md.write_text(doc, encoding="utf-8")
    print(f"   md: {md}（{md.stat().st_size} 字节）")
    lines.append(f"| 生成 MD | {'✓ ' + str(md.stat().st_size) + ' 字节' if md.exists() else '✗'} |")
    ok_all &= md.exists()

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

    lines.append(f"| MD 是否保留 | {'✓' if md.exists() else '✗'} |")
    lines.append(f"| 结论 | {'✓ 通过' if (ok_all and ok_png) else ('⚠ 部分通过' if ok_all else '✗ 失败')} |")
    summary(lines)
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
