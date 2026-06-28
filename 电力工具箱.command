#!/bin/zsh
set -euo pipefail

launcher_dir=${0:A:h}

is_toolbox_root() {
  local folder=$1
  [[ -f "$folder/toolbox_launcher.pyw" && -f "$folder/toolbox/app.py" ]]
}

find_in_tree() {
  local root=$1
  [[ -d "$root" ]] || return 1
  if is_toolbox_root "$root"; then
    print -r -- "$root/toolbox_launcher.pyw"
    return 0
  fi
  local found
  found=$(find "$root" -name toolbox_launcher.pyw -type f -print 2>/dev/null | while IFS= read -r candidate; do
    local folder=${candidate:h}
    if [[ -f "$folder/toolbox/app.py" ]]; then
      print -r -- "$candidate"
      break
    fi
  done)
  [[ -n "$found" ]] || return 1
  print -r -- "$found"
}

find_toolbox_script() {
  local candidates=(
    "$launcher_dir"
    "${launcher_dir:h}"
    "$PWD"
    "$HOME/Desktop"
    "$HOME/Documents"
  )
  local root found
  for root in "${candidates[@]}"; do
    [[ -n "$root" && -d "$root" ]] || continue
    if found=$(find_in_tree "$root"); then
      print -r -- "$found"
      return 0
    fi
  done
  return 1
}

script_path=$(find_toolbox_script || true)
if [[ -z "$script_path" ]]; then
  print -r -- "Toolbox launcher not found. Put this command beside toolbox_launcher.pyw, inside 00_启动, or keep the toolbox folder under Desktop/Documents."
  exit 1
fi

if [[ "${TOOLBOX_SMOKE:-}" == "1" ]]; then
  print -r -- "TOOLBOX_SCRIPT=$script_path"
  exit 0
fi

toolbox_root=${script_path:h}
workspace_root=${toolbox_root:h}
venv_python="$workspace_root/.venv/bin/python"

cd "$toolbox_root"
if [[ -x "$venv_python" ]]; then
  exec "$venv_python" "$script_path"
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$script_path"
fi

print -r -- "python3 was not found. Install Python 3.11 or run the macOS setup first."
exit 1
