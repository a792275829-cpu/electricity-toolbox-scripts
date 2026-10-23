#!/bin/zsh
set -euo pipefail

launcher_dir=${0:A:h}
root_dir=${launcher_dir:h}

# Reuse the desktop development data directory and the existing source launcher.
export TOOLBOX_CODE_ROOT="$root_dir"
export TOOLBOX_DATA_ROOT="$HOME/Library/Application Support/com.electricity.toolbox/development/data"
export TOOLBOX_SOURCE_DEBUG=1
export PYTHONPATH="$root_dir${PYTHONPATH:+:$PYTHONPATH}"

if [[ "${TOOLBOX_SMOKE:-}" == "1" ]]; then
  print -r -- "DEBUG_SOURCE=$TOOLBOX_CODE_ROOT"
  print -r -- "DEBUG_DATA=$TOOLBOX_DATA_ROOT"
else
  mkdir -p "$TOOLBOX_DATA_ROOT"
  if [[ ! -x "$root_dir/.venv/bin/python" ]]; then
    "$root_dir/setup_macos.command"
  fi
  exec "$root_dir/.venv/bin/python" "$root_dir/desktop/python/source_debug.py"
fi

exec "$launcher_dir/电力工具箱.command"
