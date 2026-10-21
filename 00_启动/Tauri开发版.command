#!/bin/zsh
set -euo pipefail
launcher_dir=${0:A:h}
root_dir=${launcher_dir:h}
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [[ "${TOOLBOX_SMOKE:-}" == "1" ]]; then
  print -r -- "TAURI_PROJECT=$root_dir/desktop"
  print -r -- "PYTHON_SERVICE=$root_dir/desktop/python/service_bridge.py"
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
exec npm run dev
