#!/bin/zsh

set -e

SCRIPT_DIR="${0:A:h}"
REPO_ROOT="${SCRIPT_DIR:h}"
PYTHON="$REPO_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "未找到工具箱 Python 环境，请先运行：$REPO_ROOT/setup_macos.command"
  exit 1
fi

cd "$SCRIPT_DIR"
exec "$PYTHON" "$SCRIPT_DIR/src/run_500kv_blockage_gui.py"
