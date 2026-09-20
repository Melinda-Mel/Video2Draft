# -*- coding: utf-8 -*-
"""Video2Draft 跨平台 CLI：一条链接 → MD + PNG + 结果 JSON。

用法：
    python3 tools/video2draft.py "<视频链接>"
    python3 tools/video2draft.py "<链接>" --no-push          # 兼容旧参数：不推送（本 CLI 本来也不推送）
    python3 tools/video2draft.py "<链接>" --platform 抖音      # 平台识别失败时手动指定
    python3 tools/video2draft.py --doctor                    # 环境自检

产出（默认输出到 ~/Video2Draft，可用 YJCG_OUTPUT_DIR 改）：
    <标题>.md      成品文案（无 DeepSeek Key 时自动退化为「标题 + 原始转写稿」）
    <标题>.png     排版长图（出图失败**不影响** MD 产出）
    <标题>.json    结果 JSON（标题/作者/时长/字数/路径/各步骤状态）

说明：本 CLI 只负责出稿，不做任何微信推送（推送由外层 WorkBuddy 负责）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from v2d import audio as v2d_audio          # noqa: E402
from v2d import config as v2d_config        # noqa: E402
from v2d import render as v2d_render        # noqa: E402
from v2d import transcribe as v2d_transcribe  # noqa: E402
import adapters                              # noqa: E402
import platforms                             # noqa: E402


def safe_name(s: str, n: int = 60) -> str:
    import re
    s = re.sub(r'[\\/:*?"<>|#\n\r\t]', "_", s or "").strip("_ ")
    return s[:n] or "video"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def doctor() -> int:
    """环境自检：告诉用户缺什么、怎么装。"""
    print("Video2Draft 环境自检")
    print(f"  Python      : {sys.version.split()[0]} ({sys.executable})")
    print(f"  代码目录    : {v2d_config.CODE_DIR}")
    print(f"  输出目录    : {v2d_config.OUTPUT_DIR}")
    print(f"  ffmpeg      : {v2d_audio.ffmpeg_version()}")
    try:
        import yt_dlp
        print(f"  yt-dlp      : {yt_dlp.version.__version__}")
    except Exception:
        print("  yt-dlp      : 未安装 → pip install -r requirements.txt")
    ok, msg = v2d_transcribe.available()
    print(f"  转写后端    : {msg}")
    ok_r, msg_r = v2d_render.available()
    print(f"  出图        : {msg_r}")
    chrome = v2d_config.find_chrome()
    print(f"  Chrome      : {chrome or '未找到（出图会跳过，不影响 MD）'}")
    import adapters.shipinhao as _sph
    print(f"  视频号适配器: {'已启用 ' + _sph._endpoint() if _sph.available() else '未启用（可选，需第三方服务；未验证 Windows）'}")
    return 0


def run(url: str, force_platform: str | None, model: str | None, keep_video: bool) -> dict:
    t0 = time.time()
    out_dir = v2d_config.OUTPUT_DIR
    work = v2d_config.TMP_DIR / f"v2d_{int(time.time())}"
    work.mkdir(parents=True, exist_ok=True)
    v2d_config.ensure_dirs()

    result = {"ok": False, "url": url, "steps": {}, "warnings": []}

    # ① 平台识别
    platform = force_platform
    if not platform:
        platform, found = platforms.extract(url)
        if not platform:
            raise RuntimeError("没认出这是什么平台的链接（支持：视频号/抖音/小红书/B站/YouTube/X）")
        url = found or url
    log(f"① 平台：{platform}")

    # ② 取媒体（下载）
    adapter = adapters.get_adapter(platform)
    info = adapter(url, str(work), platform=platform)
    log(f"   ✓ {info.get('title', '')[:40]} | {info.get('author', '')}")
    result["steps"]["fetch"] = "ok"
    result["platform"] = platform

    # ③ 音频
    wav = info.get("audio_path")
    if not wav and info.get("video_path"):
        wav = str(work / "audio.wav")
        v2d_audio.extract_audio(info["video_path"], wav)
        result["steps"]["audio"] = "ok"
    if not wav and not info.get("text"):
        raise RuntimeError("适配器既没给音频也没给文本，无法转写")

    # ④ 转写
    if info.get("text"):
        text = info["text"]
        segs = [{"start": 0, "end": 0, "text": text}]
        result["steps"]["transcribe"] = "skipped(已有文本)"
    else:
        segs = v2d_transcribe.transcribe(wav, model=model)
        text = "".join(s["text"] for s in segs).strip()
        result["steps"]["transcribe"] = "ok"
    if not text:
        raise RuntimeError("转写结果为空（视频可能没有语音）")

    duration = int(segs[-1]["end"]) if segs else int(info.get("duration") or 0)

    # ⑤ 整理成稿（无 Key 自动退化为原始稿）
    import format_doc
    doc = format_doc.format_transcript(info.get("title", ""), info.get("author", ""),
                                       duration, len(text), info.get("tags", ""), text)
    stem = safe_name(info.get("title") or "video", 80)
    md = out_dir / f"{stem}.md"
    md.write_text(doc, encoding="utf-8")
    (out_dir / f"{stem}.txt").write_text(text, encoding="utf-8")
    result["steps"]["md"] = "ok"
    log(f"⑤ 成稿 ✓ {md}")

    # ⑥ 出图（失败不算任务失败）
    ok_png, png, msg = v2d_render.render(str(md))
    if ok_png:
        log(f"⑥ 长图 ✓ {png}（{msg}）")
        result["steps"]["png"] = "ok"
    else:
        log(f"⑥ 长图 ✗ {msg}")
        result["steps"]["png"] = "skipped"
        result["warnings"].append(msg)

    if not keep_video and info.get("video_path"):
        try:
            Path(info["video_path"]).unlink()
        except Exception:  # noqa: BLE001
            pass

    result.update({
        "ok": True,
        "title": info.get("title", ""),
        "author": info.get("author", ""),
        "tags": info.get("tags", ""),
        "duration": duration,
        "chars": len(text),
        "md_path": str(md),
        "txt_path": str(out_dir / f"{stem}.txt"),
        "png_path": png,
        "cost": round(time.time() - t0),
    })
    js = out_dir / f"{stem}.json"
    js.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["json_path"] = str(js)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Video2Draft：链接 → 文案 + 长图 + 结果 JSON")
    ap.add_argument("url", nargs="?", help="视频链接")
    ap.add_argument("--platform", help="手动指定平台（视频号/抖音/小红书/B站/YouTube/X）")
    ap.add_argument("--model", help="whisper 档位：tiny/base/small/medium/large（默认 small）")
    ap.add_argument("--no-push", action="store_true", help="兼容旧参数：不推送（本 CLI 从不推送）")
    ap.add_argument("--keep-video", action="store_true", help="保留下载的视频文件")
    ap.add_argument("--doctor", action="store_true", help="环境自检")
    args = ap.parse_args()

    if args.doctor:
        return doctor()
    if not args.url:
        ap.print_help()
        return 1
    try:
        r = run(args.url, args.platform, args.model, args.keep_video)
        print("@@RESULT@@" + json.dumps(r, ensure_ascii=False))
        return 0
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        print(f"❌ 没成功：{msg}", file=sys.stderr)
        print("@@ERROR@@" + json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        if "--debug" in sys.argv:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
