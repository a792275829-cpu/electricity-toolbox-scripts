#!/bin/zsh
set -euo pipefail

script_dir=${0:A:h}
workspace_root=${script_dir:h}
venv_python="$workspace_root/.venv/bin/python"
writer_script="$script_dir/wps_excel_to_kdocs_gui.py"

if [[ ! -x "$venv_python" ]]; then
  print -r -- "Python environment not found. Run setup_macos.command first."
  exit 1
fi

exec "$venv_python" "$writer_script" --launch-browser
