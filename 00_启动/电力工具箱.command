#!/bin/zsh
set -euo pipefail

launcher_dir=${0:A:h}
root_dir=${launcher_dir:h}
script_path="$root_dir/电力工具箱/toolbox_launcher.pyw"

if [[ ! -f "$script_path" ]]; then
  print -r -- "Toolbox launcher not found: $script_path"
  exit 1
fi

if [[ "${TOOLBOX_SMOKE:-}" == "1" ]]; then
  print -r -- "TOOLBOX_SCRIPT=$script_path"
  exit 0
fi

venv_python="$root_dir/.venv/bin/python"
dependencies_ready() {
  "$venv_python" -c 'import importlib.util; raise SystemExit(not all(importlib.util.find_spec(name) for name in ("cryptography", "docx", "openpyxl", "pandas", "playwright", "sklearn")))' >/dev/null 2>&1
}

if [[ ! -x "$venv_python" ]] || ! dependencies_ready; then
  "$root_dir/setup_macos.command"
  "$venv_python" -c 'import cryptography, docx, openpyxl, pandas, playwright, sklearn'
fi

exec "$venv_python" "$script_path"
