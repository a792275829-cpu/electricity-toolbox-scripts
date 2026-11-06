#!/bin/zsh
set -e
SCRIPT_DIR="${0:A:h}"
PYTHON="${SCRIPT_DIR:h}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "未找到 Python 环境，请先运行 setup_macos.command"
  exit 1
fi
cd "$SCRIPT_DIR"
"$PYTHON" tools/layer_review_server.py
