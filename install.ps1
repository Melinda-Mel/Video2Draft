# Video2Draft 环境检查与安装（Windows / PowerShell）
# 用法：
#   powershell -ExecutionPolicy Bypass -File install.ps1          # 检查 + 安装依赖
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Check  # 只检查
param([switch]$Check)

$ErrorActionPreference = "Continue"
function Ok($m)   { Write-Host "OK  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "!   $m" -ForegroundColor Yellow }
function Bad($m)  { Write-Host "X   $m" -ForegroundColor Red }
function Tip($m)  { Write-Host "    -> $m" -ForegroundColor DarkGray }

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

Write-Host "Video2Draft 安装检查（Windows）"
Write-Host "仓库目录：$Here"
Write-Host ""

$Fail = $false

# ---- 1. Python ----
$Py = $null
foreach ($c in @("python", "py")) {
  if (Get-Command $c -ErrorAction SilentlyContinue) { $Py = $c; break }
}
if (-not $Py) {
  Bad "没找到 Python"
  Tip "winget install Python.Python.3.12   或到 python.org 下载 3.10+（安装时勾选 Add to PATH）"
  $Fail = $true
} else {
  $Ver = & $Py -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
  & $Py -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" 2>$null
  if ($LASTEXITCODE -eq 0) { Ok "Python $Ver ($Py)" }
  else { Bad "Python 版本过低：$Ver（需要 3.10+）"; $Fail = $true }
}

# ---- 2. pip ----
if ($Py) {
  & $Py -m pip --version *> $null
  if ($LASTEXITCODE -eq 0) { Ok "pip 可用" }
  else { Bad "pip 不可用"; Tip "python -m ensurepip --upgrade"; $Fail = $true }
}

# ---- 3. ffmpeg ----
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
  Ok "ffmpeg：$((Get-Command ffmpeg).Source)"
} else {
  Bad "没找到 ffmpeg（提取音频必需）"
  Tip "winget install Gyan.FFmpeg   或到 ffmpeg.org 下载后把 bin 目录加进 PATH"
  Tip "也可以设置环境变量：`$env:YJCG_FFMPEG = 'C:\ffmpeg\bin\ffmpeg.exe'"
  $Fail = $true
}

# ---- 4. Chrome（出图用，可选）----
$Chrome = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($Chrome) { Ok "浏览器：$Chrome" }
else {
  Warn "没找到 Chrome/Edge（只影响出长图，不影响出 MD）"
  Tip "装 Chrome，或设置 `$env:YJCG_CHROME = 'C:\path\to\chrome.exe'"
}

# ---- 5. 依赖 ----
if (-not $Check -and -not $Fail) {
  Write-Host ""
  Write-Host "安装 Python 依赖…"
  & $Py -m pip install -r requirements.txt
  if ($LASTEXITCODE -ne 0) { Bad "依赖安装失败"; $Fail = $true }
}

if (-not $Check -and -not $Fail) {
  Write-Host ""
  Write-Host "自检："
  & $Py tools\video2draft.py --doctor
  Write-Host ""
  Ok "搞定。用法： $Py tools\video2draft.py `"<视频链接>`""
  Write-Host "   输出目录默认 %USERPROFILE%\Video2Draft，可用 YJCG_OUTPUT_DIR 修改。"
} elseif ($Fail) {
  Write-Host ""
  Bad "有必需项没就绪，按上面的提示装好后再跑一次。"
  exit 1
} else {
  Warn "只检查不安装（-Check）。去掉 -Check 可自动装依赖。"
}
