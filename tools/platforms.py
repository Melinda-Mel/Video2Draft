#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""链接 → 平台 识别（enqueue / sph_bot / queue_worker 共用）。

只做一件事：从一段文本里挖出「视频/文章链接」，并标出它属于哪个平台。
不联网、不展开短链（展开交给 pipeline.py，避免入队变慢）。
"""
import hashlib
import re

# 尾部常见的多余标点，命中后剪掉
TRIM = '，,。、；;：:）)】]》>＞"\'”’!！?？ \t'

# 顺序 = 优先级。先命中先算数，所以更具体的写前面。
# 注意 weixin.qq.com/sph（视频号）与 mp.weixin.qq.com（公众号）要分开，
# 后者限定 /s 开头，否则会互相误吞。
PATTERNS = [
    ('视频号', re.compile(
        r'https?://(?:weixin\.qq\.com/sph/|channels\.weixin\.qq\.com/|finder\.video\.qq\.com/)'
        r'[^\s，,。、）)】\]"\'<>]+')),
    ('公众号', re.compile(
        r'https?://mp\.weixin\.qq\.com/s[^\s，,。、）)】\]"\'<>]*')),
    ('B站', re.compile(
        r'https?://(?:www\.|m\.)?bilibili\.com/video/[^\s，,。、）)】\]"\'<>]+'
        r'|https?://b23\.tv/[A-Za-z0-9]+')),
    ('抖音', re.compile(
        r'https?://v\.douyin\.com/[A-Za-z0-9\-_]+'
        r'|https?://(?:www\.)?douyin\.com/video/\d+'
        r'|https?://(?:www\.)?iesdouyin\.com/share/video/\d+')),
    ('YouTube', re.compile(
        r'https?://(?:www\.)?youtube\.com/(?:shorts/|watch\?v=)[^\s，,。、）)】\]"\'<>]+'
        r'|https?://youtu\.be/[^\s，,。、）)】\]"\'<>]+')),
    ('快手', re.compile(
        r'https?://v\.kuaishou\.com/[A-Za-z0-9\-_]+'
        r'|https?://(?:www\.)?kuaishou\.com/(?:short-video|fw)/[^\s，,。、）)】\]"\'<>]+')),
    ('小红书', re.compile(
        # 短链域名有 .com 也有 .cn（她 2026-09-20 发的分享链就是 xhslink.cn/o/xxx），两个都要认
        r'https?://(?:www\.)?(?:xhslink\.cn|xhslink\.com)/[^\s，,。、）)】\]"\'<>]+'
        r'|https?://(?:www\.)?xiaohongshu\.com/[^\s，,。、）)】\]"\'<>]+')),
    ('X', re.compile(
        # x.com / twitter.com；带 /video/1 或 ?s=46 尾巴都照收（x_reply.py 里再挖推文 ID）
        r'https?://(?:www\.)?(?:x\.com|twitter\.com)/[^\s，,。、）)】\]"\'<>]+')),
    ('微博', re.compile(
        r'https?://(?:video\.weibo\.com|m\.weibo\.cn|weibo\.com)/[^\s，,。、）)】\]"\'<>]+')),
    ('TikTok', re.compile(
        r'https?://(?:www\.)?tiktok\.com/[^\s，,。、）)】\]"\'<>]+')),
]

# 微信/iLink 卡片里常见 &amp; &#38; 这类转义，还原一下再匹配
UNESCAPE = [('&amp;', '&'), ('&#38;', '&'), ('&quot;', '"'), ('&lt;', '<'), ('&gt;', '>')]


def extract(text):
    """从文本里挖链接。返回 (平台名, 链接)；没命中返回 (None, None)。"""
    if not text:
        return None, None
    for a, b in UNESCAPE:
        if a in text:
            text = text.replace(a, b)
    for name, pat in PATTERNS:
        m = pat.search(text)
        if m:
            return name, m.group(0).rstrip(TRIM)
    return None, None


def url_key(url):
    """去重用的稳定 key：同一链接（忽略首尾空白）恒定。"""
    return hashlib.sha1((url or '').strip().encode('utf-8')).hexdigest()[:16]
