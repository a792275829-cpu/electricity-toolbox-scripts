#!/bin/zsh
set -euo pipefail
launcher_dir=${0:A:h}
root_dir=${launcher_dir:h}
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [[ "${TOOLBOX_SMOKE:-}" == "1" ]]; then
  print -r -- "TAURI_PROJECT=$root_dir/desktop"
  print -r -- "PYTHON_SERVICE=$root_dir/desktop/python/service_bridge.py"
  if [[ "${1:-}" == "--webview-pilot" ]]; then
    print -r -- "WEBVIEW_PILOT_CONFIG=$root_dir/desktop/src-tauri/tauri.webview-pilot.conf.json"
    print -r -- 'WEBVIEW_PILOT_MODE=offline'
  fi
  exit 0
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  print -r -- 'Tauri 版本仅支持 Apple 芯片 Mac。'
  exit 1
fi
if [[ ! -x "$root_dir/.venv/bin/python" ]]; then
  "$root_dir/setup_macos.command"
fi
for dependency in node npm cargo; do
  if ! command -v "$dependency" >/dev/null; then
    print -r -- "缺少开发依赖 $dependency，请参阅 desktop/README.md。正式安装版不需要开发依赖。"
    exit 1
  fi
done
cd "$root_dir/desktop"
if [[ ! -d node_modules ]]; then
  npm ci
fi
# Resources are generated only for distributions. Dev mode uses the shared source tree.
mkdir -p src-tauri/payload
if [[ "${1:-}" == "--webview-pilot" ]]; then
  export TOOLBOX_WEBVIEW_PILOT=1
  export TOOLBOX_WEBVIEW_PILOT_FIXTURE=1
  export TOOLBOX_APP_HOME="$HOME/Library/Application Support/com.electricity.toolbox/development/data/webview-pilot"
  exec npm run dev -- --config src-tauri/tauri.webview-pilot.conf.json
fi
exec npm run dev
