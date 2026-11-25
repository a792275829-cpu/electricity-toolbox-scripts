#!/bin/zsh
set -euo pipefail

launcher_dir=${0:A:h}
root_dir=${launcher_dir:h}

case "${1:-}" in
  "")
    exec "$launcher_dir/Tauri开发版.command" --webview-ui
    ;;
  --webview-ui-sample)
    exec "$launcher_dir/Tauri开发版.command" --webview-ui-sample
    ;;
  --accessibility-probe)
    debug_entry="$root_dir/tooling/accessibility_probe.py"
    debug_args=()
    ;;
  --ui-reference)
    debug_entry="$root_dir/desktop/python/source_debug.py"
    debug_args=(--ui-reference)
    ;;
  *)
    print -u2 -r -- '用法：源码调试版.command [--webview-ui-sample | --ui-reference | --accessibility-probe]'
    exit 2
    ;;
esac

# The Tk verification tools use the development data directory.
export TOOLBOX_CODE_ROOT="$root_dir"
export TOOLBOX_DATA_ROOT="$HOME/Library/Application Support/com.electricity.toolbox/development/data"
export TOOLBOX_SOURCE_DEBUG=1
export PYTHONPATH="$root_dir${PYTHONPATH:+:$PYTHONPATH}"

if [[ "${TOOLBOX_SMOKE:-}" == "1" ]]; then
  print -r -- "DEBUG_SOURCE=$TOOLBOX_CODE_ROOT"
  print -r -- "DEBUG_DATA=$TOOLBOX_DATA_ROOT"
  print -r -- "DEBUG_PYTHON_ENTRY=$debug_entry"
  exit 0
fi

if [[ ! -x "$root_dir/.venv/bin/python" ]]; then
  "$root_dir/setup_macos.command"
fi
exec "$root_dir/.venv/bin/python" "$debug_entry" "${debug_args[@]}"
