# -*- coding: utf-8 -*-
"""下载适配层：把「链接 → 本地视频/音频 + 元信息」这件事按平台拆开。

约定：每个适配器实现 `fetch(url, workdir) -> dict`：
    {
      "platform": "YouTube",
      "title": str, "author": str, "tags": str, "publish_time": int|0,
      "video_path": str | None,      # 下载到的视频文件
      "audio_path": str | None,      # 或者直接给音频
      "text": str | None,            # 或者直接给文字（无需转写）
    }
统一在 adapters/__init__.py 里注册，pipeline 只认这个接口。
"""
from __future__ import annotations

from . import ytdlp_platforms, xiaohongshu, x_twitter, shipinhao

# 平台名 → 适配器（平台名与 platforms.py 的识别结果一致）
REGISTRY = {
    "抖音": ytdlp_platforms.fetch,
    "B站": ytdlp_platforms.fetch,
    "YouTube": ytdlp_platforms.fetch,
    "小红书": xiaohongshu.fetch,
    "X": x_twitter.fetch,
    "视频号": shipinhao.fetch,      # 可选：依赖第三方解析服务，未装则明确报错
}

# 需要 yt-dlp 的平台（用于安装自检提示）
YTDLP_PLATFORMS = ("抖音", "B站", "YouTube")


def get_adapter(platform: str):
    fn = REGISTRY.get(platform)
    if not fn:
        raise RuntimeError(f"暂不支持该平台：{platform}")
    return fn
