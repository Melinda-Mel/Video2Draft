# 一键成稿 · Video2Draft
当前仅限国内外六平台视频分享链接 → 发送微信ClawBot → 一键提取文案生成（图片*1张+文案.md *一份）→ WorkBuddy处理

**国内外主流视频，一条链接 → 可读文案与要点，秒级出稿。**

微信端把视频链接发给机器人，几十秒后收到：结构化要点 + 完整文案 + 排版长图。
也可以不接微信，纯命令行本地出稿（MD + PNG）。

![一图看懂](assets/需求总表-分享版.png)

## 支持平台

| 平台 | 状态 | 说明 |
|---|---|---|
| 视频号 | ✅ | 需自建 `wx_video_download` 本地服务 + 微信 PC 在线（见「能力边界」） |
| 抖音 | ✅ | yt-dlp |
| 小红书 | ✅ | yt-dlp |
| B站 | ✅ | yt-dlp |
| YouTube | ✅ | yt-dlp |
| X (Twitter) | ✅ | yt-dlp |

## 运行环境

| 系统 | 状态 | 说明 |
|---|---|---|
| macOS | ✅ 已实测 | 开发环境，含可选加速（mlx-whisper，Apple 芯片快 3~5 倍） |
| Windows | ✅ 已适配 | 代码跨平台，装好 Python + ffmpeg + yt-dlp 即可；`md2pic` 出图需 Chrome |
| Linux | ✅ 理论可用 | 同 Windows 路径，未实测 |

Windows 注意事项：
- 环境变量用 `set YJCG_LIB=D:\draft`（cmd）或 `$env:YJCG_LIB="D:\draft"`（PowerShell）设置
- ffmpeg / yt-dlp 下载后把所在目录加进系统 PATH
- 微信推送（`wx-send`）是 macOS 本地组件，Windows 上自动跳过，用 `--no-push` 参数即可
- 视频号下载需要微信 PC 版在线 + 自建下载服务（见「能力边界」）

## 快速开始

```bash
# 1. 安装依赖（Python 3.10+）
pip install -r requirements.txt
# 另需系统安装 ffmpeg 与 yt-dlp，并确保在 PATH 中

# 2. 出稿（默认输出到 ~/一键成稿/，可用环境变量 YJCG_LIB 修改）
python3 tools/make_card.py "<视频链接>"
python3 tools/make_card.py "<链接>" --no-push      # 不接微信时用这个
python3 tools/make_card.py "<链接>" --platform 抖音  # 平台识别失败时手动指定

# 3. 只跑转写，不出成品
python3 tools/pipeline.py "<视频链接>"
```

Windows（PowerShell）对应写法——注意命令是 `python` 不是 `python3`：

```powershell
pip install -r requirements.txt
$env:YJCG_LIB = "D:\一键成稿"          # 归档目录，按需修改
python tools\make_card.py "<视频链接>" --no-push
```

> 默认按 macOS/Linux 写法输出到 `~/一键成稿/`；Windows 下建议显式设置 `YJCG_LIB`，避免 `~` 落到用户目录里找不到。

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `YJCG_LIB` | `~/一键成稿` | 归档目录（MD + PNG） |
| `YJCG_BASE` | `~/yijianchenggao-workspace` | 中间产物目录（音频/临时视频） |
| `YJCG_FFMPEG` | `ffmpeg` | ffmpeg 路径 |
| `YJCG_YTDLP` | `yt-dlp` | yt-dlp 路径 |
| `YJCG_PYTHON` | 当前解释器 | 用于转写子进程 |
| `YJCG_YTDLP` | `yt-dlp` | X 适配器用的 yt-dlp |
| `SPH_WHISPER_MODEL` | `small` | whisper 档位：tiny/base/small/medium/large |
| `HF_HUB_OFFLINE` | `1` | 首次下载模型时请设为 `0` |

云端整理使用 DeepSeek API：复制 `tools/.sph_config.json.example` 为 `tools/.sph_config.json` 并填入自己的 Key（不配置则跳过智能整理，输出原始转写）。

## 能力边界（诚实版）

- **视频号下载**依赖第三方解码方案，涉及微信登录态，本仓库不直接分发该组件；有它 = 全自动，没它 = 其余 5 个平台照常可用。
- 只验证过「手机端点击转发后复制的那条链接」这一种链接形态。
- 专有名词纠错内置 394 条词表（术语库见 `tools/fix_terms.py`），覆盖科技/创投/区块链等 10 个领域，可在词表里继续追加。
- 转写全本机离线（faster-whisper），音频不出机器。

## 文档

全套设计文档在 [`docs/`](docs/)：总览、卡点与解决、可移植性拆分、发布检查、需求全集等 13 份。

## License

MIT — 见 [LICENSE](LICENSE)。
