# 12 · Windows 真机验收（真实 YouTube → MD + PNG + JSON）

> 用途：在**一台真实 Windows 机器**上，把「链接 → 成稿」整条链路跑通并留证。
> 前置：这台机器能正常打开 youtube.com（有正常网络出口），浏览器最好已登录 YouTube。
>
> **当前口径（不要提前改）**：Windows 的「真实 YouTube 链接」端到端**尚未验证**——
> CI 用的是机房 IP，被 YouTube 反爬拦截（`Sign in to confirm you're not a bot`）。
> 本文档跑完并把结论发回复核后，才会把文档口径改成「已验证」。

---

## 0. 跑完能证明什么

| # | 要证明的事 | 通过标准 |
|---|---|---|
| 1 | 环境检查与依赖安装 | `install.ps1` 输出 `OK Python / OK pip / OK ffmpeg`，无 `X` |
| 2 | 真实 YouTube 链接能下载 | 日志出现 `① 平台：YouTube` 与视频标题（不再报反爬/网络错） |
| 3 | 能提取音频并转写 | 日志出现 `转写耗时 xx.xs，n 段`，且转写非空 |
| 4 | 能生成 MD、PNG、JSON | 输出目录里三个文件都存在且非 0 字节 |
| 5 | `--no-push` 成立 | CLI 全程不触碰任何推送组件（静态核验 0 命中 + 运行成功） |
| 6 | 没有 DeepSeek Key 也不丢稿 | MD 内含「已退化为原始稿」标记，且原始转写文字**一字不少** |

第 5、6 项由 `tests/win_acceptance.py` 一次性验掉；第 1 项由 `install.ps1` 验。

---

## 1. 装环境（一次性，约 5 分钟）

管理员 PowerShell（右键「开始」→ 终端(管理员)）里执行：

```powershell
winget install --id Python.Python.3.12 -e
winget install --id Gyan.FFmpeg -e
winget install --id Google.Chrome -e
```

装完**关掉这个窗口，重新开一个普通 PowerShell**（让 PATH 生效）。

> 不装 Chrome 也可以跑通 MD/JSON，只是出不了长图。

---

## 2. 拉代码

```powershell
cd $env:USERPROFILE
git clone https://github.com/Melinda-Mel/Video2Draft.git
cd Video2Draft
git checkout cross-platform-v2
```

预期：`git status` 干净，当前分支 `cross-platform-v2`。

---

## 3. 检查环境（只看，不安装）

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -Check
```

预期输出（节选）：

```
OK  Python 3.12.x (python)
OK  pip 可用
OK  ffmpeg：C:\...\ffmpeg.exe
OK  浏览器：C:\Program Files\Google\Chrome\Application\chrome.exe
```

看到 `X` 的项，按它给的提示装好再继续。

---

## 4. 装依赖 + 自检

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

预期结尾：

```
自检：
Video2Draft 环境自检
  ...
OK  搞定。用法： python tools\video2draft.py "<视频链接>"
```

---

## 5. 一键真机验收（核心步骤）

```powershell
python tests\win_acceptance.py --cookies-from-browser chrome
```

> 浏览器登录的是 Edge 就换成 `--cookies-from-browser edge`；
> 或用导出的 cookies 文件：`--cookies-file C:\path\to\cookies.txt`（Netscape 格式）。
> 想跑得更快可以加 `--model tiny`（默认就是 tiny）。

预期输出（节选）：

```
[..] 运行（nokey）：video2draft.py <url> --model tiny --no-push [已清空 Key]
[..] ① 平台：YouTube
   ✓ Me at the zoo | jawed
   ✓ [cpu] 转写耗时 13.2s，2 段
[format_doc] 退回原始稿：未配置 DEEPSEEK_API_KEY
⑤ 成稿 ✓ C:\Users\<你>\Video2Draft-acceptance\Me at the zoo.md
⑥ 长图 ✓ C:\Users\<你>\Video2Draft-acceptance\Me at the zoo.png（156 KB）
====================
结论：PASS
  OK  fetch_ok
  OK  transcribe_ok
  OK  md_ok
  OK  png_ok
  OK  json_ok
  OK  md_has_fallback_marker
  OK  md_keeps_raw_transcript
本机完整结果（含路径，请勿外发）：C:\Users\<你>\Video2Draft-acceptance\win_acceptance_result.json
本机日志（含路径，请勿外发）：C:\Users\<你>\AppData\Local\Temp\v2d_accept_nokey.log
可外发脱敏摘要：C:\Users\<你>\Video2Draft-acceptance\win_acceptance_result_share.json
```

**通过标准**：结论为 `PASS`，且 7 项检查**全部 OK**。
（`--no-push` 与「无 Key 保留原始稿」已包含在内，不需要另外跑。）

如果结论是 `BLOCKED`：说明这台机器出口也被 YouTube 拦了，先按下面的「常见问题」换登录态再跑。

> 这个脚本**只允许在 Windows 上跑**：非 Windows 环境会立刻中止（退出码 3），
> 不下载视频、不读取浏览器 Cookies、不执行任何验收步骤，也不会输出 `PASS`。

---

## 6. 分步手动跑（可选，想逐步看每步产出时用）

```powershell
# 只看平台识别 + 下载 + 转写 + 出稿的全过程日志
python tools\video2draft.py "https://www.youtube.com/watch?v=jNQXAC9IVRw" --model tiny --no-push

# 环境自检
python tools\video2draft.py --doctor
```

---

## 7. 逐项验收清单（对照表）

| 要求 | 命令 | 预期结果 | 产物路径 | 通过标准 |
|---|---|---|---|---|
| 环境检查与依赖安装 | `install.ps1` | 全 `OK`，结尾 `搞定` | — | 无 `X` 项 |
| 真实 YouTube 下载 | 第 5 步脚本，或第 6 步命令 | `① 平台：YouTube` + 标题 | `%USERPROFILE%\Video2Draft-acceptance\` | 不报反爬/网络错 |
| 提取音频并转写 | 同上（自动串联） | `转写耗时 xx.xs，n 段` | 临时目录（`--keep-video` 可留音频） | 转写非空 |
| 生成 MD / PNG / JSON | 同上 | `⑤ 成稿 ✓` `⑥ 长图 ✓` | `<标题>.md` / `.txt` / `.png` / `.json` | 三个文件均 > 0 字节 |
| `--no-push` | 第 5 步默认带 | 静态核验 0 命中，运行成功 | — | 无推送依赖、无推送动作 |
| 无 Key 保留原始转写稿 | 第 5 步已清空 Key | MD 内含「已退化为原始稿」 | `<标题>.md` | 原始转写文字完整保留 |

---

## 8. 产物在哪

| 跑法 | 输出目录 |
|---|---|
| `tests\win_acceptance.py` | `%USERPROFILE%\Video2Draft-acceptance\` |
| 直接跑 `tools\video2draft.py` | `%USERPROFILE%\Video2Draft\` |

脚本在输出目录里留两份结果，**用途不同、别搞混**：

| 文件 | 内容 | 能不能外发 |
|---|---|---|
| `win_acceptance_result.json` | 完整版，含本机绝对路径 | ❌ 只留本机排查用 |
| `win_acceptance_result_share.json` | **脱敏版**，脚本打印的 JSON / base64 就是它 | ✅ 可贴回复核 |

每次产出四个内容文件：`<标题>.md`（成品）、`<标题>.txt`（原始转写）、`<标题>.png`（长图）、`<标题>.json`（结果 JSON）。
想换目录：加 `--out D:\v2d-acceptance`，或设环境变量 `YJCG_OUTPUT_DIR`。

---

## 9. 跑完把什么发回来复核

脚本结尾会打印**一行脱敏 JSON**（外加同一份摘要的 base64 备用，防止被聊天工具截断），整行贴回来即可：

- `verdict` / `pass_checks`：判定与逐项结果；
- `os` / `env`：平台、Python、**ffmpeg 只留文件名**、是否找到 Chrome、yt-dlp 版本；
- `main`：标题、作者、时长、字数、MD/PNG/JSON 的**字节数**；
- `no_push`：静态核验结果。

这一行**不含**：本机绝对路径、Windows 用户名、日志绝对路径、cookies 路径、API Key、账号信息。
（带路径的完整版写在 `win_acceptance_result.json`，那是给你自己排查用的，**别发出去**。）

**不要发**：cookies 文件内容、任何 API Key、账号密码、聊天记录。

---

## 10. 隐私红线（务必遵守）

- **绝不**把 cookies 文件、`.sph_config.json`、Key、账号信息提交到 GitHub。
- `.gitignore` 已屏蔽 `*.cookies.txt`、`cookies.txt`、`tools/.sph_config.json`、音视频文件等；
  但**别把 cookies 放在仓库目录里**才是根本。
- Cookies 只有两个来源：① 从本机浏览器实时读取（`--cookies-from-browser`）；
  ② 你本地的 cookies 文件路径（`--cookies-file`）。除此之外不接受任何传入方式。

---

## 11. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `Sign in to confirm you're not a bot` | 出口被 YouTube 判为机器人 | 在本机浏览器登录 YouTube，再用 `--cookies-from-browser chrome`（或 `edge`）；或换网络出口 |
| `没找到 ffmpeg` | 没装 / PATH 未生效 | `winget install Gyan.FFmpeg` 后**重开** PowerShell；或设 `$env:YJCG_FFMPEG="C:\ffmpeg\bin\ffmpeg.exe"` |
| `⑥ 长图 ✗` | 没装 Chrome | 装 Chrome，或设 `$env:YJCG_CHROME="C:\path\to\chrome.exe"`（只影响出图，MD 照出） |
| 转写很慢 | 模型档位高 | 加 `--model tiny` |
| 中文乱码 | 老版本未处理 Windows 代码页 | 已在 v2 修复（子进程统一 UTF-8 + 控制台 UTF-8），确认在 `cross-platform-v2` 分支上 |

---

## 12. 通过之后

把第 9 步那一行结论发回来复核。**确认无误后**，才把
[docs/11-跨平台重构与验收.md](11-跨平台重构与验收.md) 与 [README](../README.md) 里
「Windows 真实 YouTube 尚未验证」的口径改成「已验证」，并记录本次证据。
在此之前，所有文档**保持现状**。
