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
if [[ ! -f data/500kv_node_position_candidates.json ]]; then
  "$PYTHON" tools/build_500kv_position_candidates.py
fi

"$PYTHON" tools/run_500kv_anchor_calibrator.py &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT INT TERM
sleep 1
open "http://127.0.0.1:8766"
wait $SERVER_PID
