#!/bin/zsh
set -euo pipefail

launcher_dir=${0:A:h}
cd "$launcher_dir"

if ! command -v python3 >/dev/null 2>&1; then
  print -r -- "python3 was not found. Install Python 3.11, then run this script again."
  exit 1
fi

if [[ ! -d ".venv" ]]; then
  print -r -- "Creating local Python virtual environment: .venv"
  bootstrap_python=$(command -v python3.11 || command -v python3)
  "$bootstrap_python" -c 'import sys; sys.exit("Install Python 3.11 to match the release runtime.") if sys.version_info[:2] != (3, 11) else None'
  "$bootstrap_python" -m venv .venv
fi

venv_python="$launcher_dir/.venv/bin/python"
if [[ ! -x "$venv_python" ]]; then
  print -r -- "Virtual environment Python not found: $venv_python"
  exit 1
fi

print -r -- "Installing Python dependencies..."
"$venv_python" -c 'import sys; sys.exit("Please recreate .venv with Python 3.11 (the release runtime).") if sys.version_info[:2] != (3, 11) else None'
"$venv_python" -m pip install --require-hashes --no-deps --only-binary :all: -r packaging/tauri/requirements-macos.lock
"$venv_python" -m pip check

print -r -- "Installing Playwright Chromium browser..."
PLAYWRIGHT_SKIP_BROWSER_GC=1 "$venv_python" -m playwright install chromium

if command -v cargo >/dev/null 2>&1 || [[ -x "$HOME/.cargo/bin/cargo" ]]; then
  print -r -- "Building the shared Rust backend (including market report inputs)..."
  "$venv_python" native/price-xlsx/build.py
elif [[ ! -x "500kV断面分析/bin/toolbox-price-xlsx" ]]; then
  print -r -- "Rust backend not found. Other Python tools remain available."
  print -r -- "For market report manual inputs, install Rust and run: .venv/bin/python native/price-xlsx/build.py"
fi

uploader_dir="$launcher_dir/电力工具脚本/private-data-uploader-tool"
if [[ -f "$uploader_dir/package.json" ]]; then
  if command -v npm >/dev/null 2>&1; then
    if [[ ! -d "$uploader_dir/node_modules" || \
          "$uploader_dir/package-lock.json" -nt "$uploader_dir/node_modules/.package-lock.json" ]]; then
      print -r -- "Installing Node dependencies for private data uploader..."
      (cd "$uploader_dir" && npm ci)
    else
      print -r -- "Private data uploader Node dependencies are already installed."
    fi
  else
    print -r -- "Node.js/npm not found. Skipping private data uploader dependencies."
    print -r -- "Install Node.js and run: cd \"$uploader_dir\" && npm install"
  fi
fi

chmod +x \
  "$launcher_dir/00_启动/电力工具箱.command" \
  "$launcher_dir/wps自动/启动WPS专用Chrome.command" \
  2>/dev/null || true

print -r -- ""
print -r -- "Setup complete. You can launch the toolbox with: ./00_启动/电力工具箱.command"
