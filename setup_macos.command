#!/bin/zsh
set -euo pipefail

launcher_dir=${0:A:h}
cd "$launcher_dir"

if ! command -v python3 >/dev/null 2>&1; then
  print -r -- "python3 was not found. Install Python 3.11 or newer, then run this script again."
  exit 1
fi

if [[ ! -d ".venv" ]]; then
  print -r -- "Creating local Python virtual environment: .venv"
  python3 -m venv .venv
fi

venv_python="$launcher_dir/.venv/bin/python"
if [[ ! -x "$venv_python" ]]; then
  print -r -- "Virtual environment Python not found: $venv_python"
  exit 1
fi

print -r -- "Installing Python dependencies..."
"$venv_python" -m pip install --upgrade pip
"$venv_python" -m pip install -r requirements.txt

print -r -- "Installing Playwright Chromium browser..."
"$venv_python" -m playwright install chromium

uploader_dir="$launcher_dir/电力工具脚本/private-data-uploader-tool"
if [[ -f "$uploader_dir/package.json" ]]; then
  if command -v npm >/dev/null 2>&1; then
    print -r -- "Installing Node dependencies for private data uploader..."
    (cd "$uploader_dir" && npm install)
  else
    print -r -- "Node.js/npm not found. Skipping private data uploader dependencies."
    print -r -- "Install Node.js and run: cd \"$uploader_dir\" && npm install"
  fi
fi

chmod +x "$launcher_dir/电力工具箱.command" "$launcher_dir/00_启动/电力工具箱.command" 2>/dev/null || true

print -r -- ""
print -r -- "Setup complete. You can launch the toolbox with: ./电力工具箱.command"
