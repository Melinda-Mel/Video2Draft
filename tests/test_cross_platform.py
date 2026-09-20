# -*- coding: utf-8 -*-
"""跨平台基础测试：不联网、不需要模型，纯静态 + 轻量运行时检查。

跑法：
    python3 -m unittest discover -s tests -v
（需要先把仓库根加入 sys.path，脚本里已处理）
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
sys.path.insert(0, str(TOOLS))

# 允许保留个人路径的「历史脚本」白名单（Legacy，macOS 本地链路；不参与跨平台）
LEGACY_FILES = {
    "tools/pipeline.py", "tools/make_card.py", "tools/x_reply.py", "tools/xhs_reply.py",
    "tools/whisper_server.py", "tools/enqueue.py", "tools/queue_worker.py",
    "tools/sph_bot.py", "tools/sph_reply.py", "tools/sph_worker.py",
    "tools/ilink.py", "tools/ilink_listen.py", "tools/ilink_probe.py",
    "tools/ilink_send_test.py", "tools/ima_upload.py",
}

FORBIDDEN = (
    "/Users/ilenia",
    "~/WorkBuddy",
    ".workbuddy/binaries",
    ".local/bin/md2pic",
    ".local/bin/wx-send",
    "127.0.0.1:2022",
    "127.0.0.1:2024",
)

SCAN_SUFFIX = {".py", ".sh", ".ps1", ".md", ".yml", ".yaml", ".txt", ".json"}


class TestNoPersonalPaths(unittest.TestCase):
    """硬红线：跨平台代码里不许出现个人路径 / 本机服务地址。"""

    def test_no_personal_paths_outside_legacy(self):
        bad = []
        for p in REPO.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in SCAN_SUFFIX:
                continue
            if any(part in {".git", "__pycache__", "docs", "tests"} for part in p.parts):
                continue
            rel = p.relative_to(REPO).as_posix()
            if rel in LEGACY_FILES:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for token in FORBIDDEN:
                if token in text:
                    bad.append(f"{rel}: 命中 {token}")
        self.assertEqual(bad, [], "发现个人路径/本机服务依赖：\n" + "\n".join(bad))


class TestConfig(unittest.TestCase):
    """代码目录与输出目录必须解耦。"""

    def test_code_dir_is_repo(self):
        from v2d import config
        self.assertEqual(Path(config.CODE_DIR).resolve(), TOOLS.resolve())
        self.assertEqual(Path(config.REPO_DIR).resolve(), REPO.resolve())

    def test_output_dir_independent_of_code_dir(self):
        from v2d import config
        self.assertNotEqual(Path(config.OUTPUT_DIR).resolve(), Path(config.CODE_DIR).resolve())

    def test_output_dir_env_override(self):
        out = tempfile.mkdtemp(prefix="v2d-out-")
        env = dict(os.environ, YJCG_OUTPUT_DIR=out, PYTHONPATH=str(TOOLS))
        p = subprocess.run([sys.executable, "-c",
                            "from v2d import config; print(config.OUTPUT_DIR)"],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(p.stdout.strip(), out, p.stderr)


class TestPlatforms(unittest.TestCase):
    def test_detect_six_platforms(self):
        import platforms
        cases = {
            "https://weixin.qq.com/sph/AbCdEf": "视频号",
            "https://v.douyin.com/iABCdef/": "抖音",
            "https://www.bilibili.com/video/BV1xx411c7mD": "B站",
            "https://www.youtube.com/watch?v=jNQXAC9IVRw": "YouTube",
            "https://x.com/user/status/1234567890": "X",
            "https://www.xiaohongshu.com/explore/abc": "小红书",
        }
        for url, want in cases.items():
            got, _ = platforms.extract(url)
            self.assertEqual(got, want, f"{url} → {got}")


class TestDegradation(unittest.TestCase):
    """降级路径：没 Key / 没 ffmpeg / 没 Chrome 时的行为。"""

    def test_no_deepseek_key_keeps_raw_text(self):
        import format_doc
        raw = "这是一段原始转写文稿，用来验证没有 DeepSeek Key 时也能出稿。"
        doc = format_doc.format_transcript("测试标题", "测试作者", 12, len(raw), "#测试", raw)
        self.assertIn(raw, doc)
        self.assertIn("测试标题", doc)

    def test_missing_ffmpeg_message_is_helpful(self):
        env = dict(os.environ, PATH="/nonexistent-path-for-test", YJCG_FFMPEG="/nope/ffmpeg",
                   PYTHONPATH=str(TOOLS))
        code = ("from v2d import audio;"
                "audio.require_ffmpeg()")
        p = subprocess.run([sys.executable, "-c",
                            "from v2d import audio\n"
                            "try:\n"
                            "    audio.require_ffmpeg()\n"
                            "except RuntimeError as e:\n"
                            "    print(str(e))\n"],
                           capture_output=True, text=True, env=env, timeout=60)
        msg = p.stdout
        self.assertIn("ffmpeg", msg)
        self.assertTrue(any(k in msg for k in ("brew", "winget", "apt")), msg)

    def test_render_failure_keeps_md(self):
        """出图失败（没有浏览器）时：返回失败，但 MD 必须还在。"""
        from unittest import mock
        from v2d import config, render
        with tempfile.TemporaryDirectory() as d:
            md = Path(d) / "t.md"
            md.write_text("# 标题\n\n正文一段。\n", encoding="utf-8")
            with mock.patch.object(config, "find_chrome", return_value=None):
                ok, png, msg = render.render(str(md))
            self.assertFalse(ok)
            self.assertIsNone(png)
            self.assertIn("Chrome", msg)
            self.assertTrue(md.exists(), "出图失败后 MD 必须保留")

    def test_render_bad_chrome_path_falls_back(self):
        """环境变量给的浏览器路径无效时，应回退到自动探测（而不是直接失败）。"""
        from v2d import config
        env_backup = os.environ.get("YJCG_CHROME")
        os.environ["YJCG_CHROME"] = "/nonexistent/chrome"
        try:
            found = config.find_chrome()
        finally:
            if env_backup is None:
                os.environ.pop("YJCG_CHROME", None)
            else:
                os.environ["YJCG_CHROME"] = env_backup
        # 本机有 Chrome 时应当回退成功；没有 Chrome 的机器允许 None
        if found:
            self.assertTrue(Path(found).exists())

    def test_render_works_when_chrome_available(self):
        from v2d import config, render
        if not config.find_chrome():
            self.skipTest("本机没有 Chrome，跳过出图测试")
        with tempfile.TemporaryDirectory() as d:
            md = Path(d) / "card.md"
            md.write_text("# 出图测试\n\n- 一行\n- 两行\n", encoding="utf-8")
            ok, png, msg = render.render(str(md), str(Path(d) / "card.png"))
            self.assertTrue(ok, msg)
            self.assertTrue(Path(png).exists())


class TestYtdlp(unittest.TestCase):
    def test_ytdlp_importable_or_skipped(self):
        try:
            import yt_dlp
        except ImportError:
            self.skipTest("未安装 yt-dlp（pip install -r requirements.txt）")
        self.assertTrue(yt_dlp.version.__version__)


if __name__ == "__main__":
    unittest.main(verbosity=2)
