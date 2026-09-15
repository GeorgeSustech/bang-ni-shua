#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"
PYTHON_PKG="$PROJECT_DIR/installers/python-3.14.7-macos11.pkg"
PYTHON_SHA256="70c5239ad2d62925d2947e46921d0ddd3d35be3d2f0a2d50db33da507dbcb419"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "此安装包只支持 macOS。"
  exit 1
fi

version_ok() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

PYTHON_BIN=""
for candidate in \
  /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
  /opt/homebrew/bin/python3 \
  /usr/local/bin/python3 \
  /usr/bin/python3; do
  if [[ -x "$candidate" ]] && version_ok "$candidate"; then
    PYTHON_BIN="$candidate"
    break
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  if [[ ! -f "$PYTHON_PKG" ]]; then
    echo "缺少 Python 安装包：$PYTHON_PKG"
    exit 1
  fi
  ACTUAL_SHA256="$(shasum -a 256 "$PYTHON_PKG" | awk '{print $1}')"
  if [[ "$ACTUAL_SHA256" != "$PYTHON_SHA256" ]]; then
    echo "Python 安装包校验失败，已停止。"
    exit 1
  fi
  echo "将安装 Python 3.14.7（需要 macOS 管理员密码）。"
  sudo /usr/sbin/installer -pkg "$PYTHON_PKG" -target /
  PYTHON_BIN="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
fi

if [[ ! -x "$PYTHON_BIN" ]] || ! version_ok "$PYTHON_BIN"; then
  echo "未找到可用的 Python 3.11+。"
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

if [[ ! -d "/Applications/Google Chrome.app" ]]; then
  echo "未检测到 Google Chrome，将打开官方安装页面。"
  open "https://www.google.com/chrome/"
  read -r "?请安装 Chrome 后按回车继续："
fi

open "$PROJECT_DIR/雨课堂视频助手.app"
