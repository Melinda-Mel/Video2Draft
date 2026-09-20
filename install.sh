#!/usr/bin/env bash
# Video2Draft 环境检查与安装（macOS / Linux）
# 用法：  bash install.sh          # 检查 + 安装 Python 依赖
#         bash install.sh --check  # 只检查，不安装
set -u

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; DIM=$'\033[2m'; NC=$'\033[0m'
ok()   { printf '%s✓%s %s\n' "$GREEN" "$NC" "$1"; }
warn() { printf '%s!%s %s\n' "$YELLOW" "$NC" "$1"; }
bad()  { printf '%s✗%s %s\n' "$RED" "$NC" "$1"; }
tip()  { printf '%s   → %s%s\n' "$DIM" "$1" "$NC"; }

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || exit 1

echo "Video2Draft 安装检查（$(uname -s)）"
echo "仓库目录：$HERE"
echo

FAIL=0

# ---- 1. Python ----
PY=""
for c in python3 python; do command -v "$c" >/dev/null 2>&1 && PY="$c" && break; done
if [ -z "$PY" ]; then
  bad "没找到 Python"
  tip "macOS：brew install python   或到 python.org 下载 3.10+"
  tip "Linux：sudo apt install python3 python3-pip"
  FAIL=1
else
  VER="$($PY -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)"
  if $PY -c 'import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null; then
    ok "Python $VER（$PY）"
  else
    bad "Python 版本过低：$VER（需要 3.10+）"
    FAIL=1
  fi
fi

# ---- 2. pip ----
if [ -n "$PY" ] && $PY -m pip --version >/dev/null 2>&1; then
  ok "pip 可用"
else
  bad "pip 不可用"
  tip "python3 -m ensurepip --upgrade"
  FAIL=1
fi

# ---- 3. ffmpeg ----
if command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg：$(command -v ffmpeg)"
else
  bad "没找到 ffmpeg（提取音频必需）"
  case "$(uname -s)" in
    Darwin) tip "brew install ffmpeg" ;;
    *)      tip "sudo apt install ffmpeg" ;;
  esac
  tip "或用环境变量指定：export YJCG_FFMPEG=/path/to/ffmpeg"
  FAIL=1
fi

# ---- 4. Chrome（出图用，可选）----
CHROME=""
for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
         "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
  [ -x "$c" ] && CHROME="$c" && break
done
[ -z "$CHROME" ] && CHROME="$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)"
if [ -n "$CHROME" ]; then
  ok "Chrome/Chromium：$CHROME"
else
  warn "没找到 Chrome（只影响出长图，不影响出 MD）"
  tip "装 Chrome，或 export YJCG_CHROME=/path/to/chrome"
fi

# ---- 5. Python 依赖 ----
if [ "$CHECK_ONLY" = "0" ] && [ "$FAIL" = "0" ]; then
  echo
  echo "安装 Python 依赖…"
  $PY -m pip install -r requirements.txt || { bad "依赖安装失败"; FAIL=1; }
fi

if [ "$CHECK_ONLY" = "0" ] && [ "$FAIL" = "0" ]; then
  echo
  echo "自检："
  $PY tools/video2draft.py --doctor || true
  echo
  ok "搞定。用法： $PY tools/video2draft.py \"<视频链接>\""
  echo "   输出目录默认 ~/Video2Draft，可用 YJCG_OUTPUT_DIR 修改。"
else
  echo
  if [ "$FAIL" != "0" ]; then
    bad "有必需项没就绪，先按上面的提示装好再跑一次。"
    exit 1
  fi
  warn "只检查不安装（--check）。去掉 --check 可自动装依赖。"
fi
