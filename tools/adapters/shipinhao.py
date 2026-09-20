# -*- coding: utf-8 -*-
"""视频号适配器（可选）：依赖第三方解析服务，本项目不内置、不分发。

平台支持实况（2026-09-20 核查，README 与 docs 口径以此为准）：
- 第三方上游 `ltaoo/wx_channels_download`（即 wx_video_download）官网标注支持
  Windows / macOS / Linux(ARM)，并且必须配合**微信 PC 客户端**使用。
- 本项目**只在 macOS 上实测过**这条链路。Windows 端未实机验证，
  因此对外一律写「macOS 已验证 / Windows 未验证」，不标 Windows 已支持。

怎么用：
    设好环境变量 `YJCG_SPH_ENDPOINT`（第三方服务地址，如 http://127.0.0.1:<端口>），
    本适配器按其 HTTP 契约取回视频直链后再下载。
    没设这个变量 = 视频号能力未启用，报一句人话提示，不影响其它平台。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT = int(os.environ.get("YJCG_SPH_TIMEOUT", "60"))

# 第三方服务支持情况（用于文档与报错提示，不要凭猜改）
SUPPORT = {
    "macOS": "已验证（本机实测通过）",
    "Windows": "未验证（上游自称支持，但本项目未实机验证，故不标记为支持）",
    "Linux": "未验证",
}


def _endpoint() -> str:
    return (os.environ.get("YJCG_SPH_ENDPOINT") or "").rstrip("/")


def available() -> bool:
    return bool(_endpoint())


def _post(path: str, payload: dict, timeout: int = TIMEOUT) -> dict:
    req = urllib.request.Request(
        _endpoint() + path,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _download(url: str, dst: str) -> int:
    with urllib.request.urlopen(url, timeout=300) as r, open(dst, "wb") as f:
        while True:
            chunk = r.read(262144)
            if not chunk:
                break
            f.write(chunk)
    return Path(dst).stat().st_size


def fetch(url: str, workdir: str, platform: str = "视频号") -> dict:
    if not available():
        raise RuntimeError(
            "视频号是可选适配器：需要先自建第三方解析服务。\n"
            "   → 上游 wx_video_download（GitHub: ltaoo/wx_channels_download）官网标注支持 "
            "Windows/macOS，但**本项目只在 macOS 实测过，Windows 未验证**。\n"
            "   → 装好并启动后，设置环境变量 YJCG_SPH_ENDPOINT=http://127.0.0.1:<端口> 再试。\n"
            "   → 不装也不影响抖音/B站/YouTube/小红书/X 五个平台。"
        )

    last_err = None
    for _ in range(3):
        try:
            r = _post("/api/scraper/fetch",
                      {"url": url, "force_refresh": True, "timeout_seconds": 40})
            job = (r.get("data") or {}).get("id")
            if not job:
                raise RuntimeError(f"解析服务没给 job id：{str(r)[:200]}")
            deadline = time.time() + 66
            while time.time() < deadline:
                d = (_post(f"/api/scraper/job?id={job}", {}, timeout=15) or {}).get("data") or {}
                st = d.get("status")
                if st in ("completed", "success"):
                    c = d.get("content") or {}
                    acc = d.get("account") or {}
                    vurl = c.get("video_url") or c.get("download_url") or c.get("url")
                    if not vurl:
                        raise RuntimeError("解析服务没返回视频直链字段（契约可能变了）")
                    mp4 = str(Path(workdir) / f"sph_{int(time.time())}.mp4")
                    size = _download(vurl, mp4)
                    print(f"   ✓ 视频号 {size / 1048576:.1f} MB", flush=True)
                    return {
                        "platform": "视频号",
                        "title": (c.get("title") or "视频号作品").strip(),
                        "author": (acc.get("nickname") or "").strip(),
                        "tags": "",
                        "publish_time": int(c.get("publish_time") or 0),
                        "video_path": mp4,
                        "audio_path": None,
                        "text": None,
                        "source_url": url,
                    }
                if st == "failed":
                    raise RuntimeError(str(d.get("error") or "解析失败"))
                time.sleep(0.5)
            raise RuntimeError("视频号解析超时（66s）")
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1)
    raise RuntimeError(
        f"视频号解析失败：{last_err}\n"
        "   → 该服务需要微信 PC 端在线；若本机没装或没起服务，请改用其它平台，"
        "或先启动第三方服务并设置 YJCG_SPH_ENDPOINT。"
    )
