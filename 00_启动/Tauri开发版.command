#!/bin/zsh
set -euo pipefail
launcher_dir=${0:A:h}
root_dir=${launcher_dir:h}
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
# Each launch selects one mode; do not inherit an offline service from a parent.
unset TOOLBOX_WEBVIEW_UI TOOLBOX_WEBVIEW_UI_SAMPLE TOOLBOX_WEBVIEW_PILOT TOOLBOX_WEBVIEW_PILOT_FIXTURE
toolbox_webview_config=""
case "${1:-}" in
  --webview-ui|--webview-ui-sample)
    export TOOLBOX_WEBVIEW_UI=1
    export TOOLBOX_APP_HOME="$HOME/Library/Application Support/com.electricity.toolbox/development/data/webview-ui"
    toolbox_webview_config="src-tauri/tauri.webview-ui.conf.json"
    if [[ "${1:-}" == "--webview-ui-sample" ]]; then
      export TOOLBOX_WEBVIEW_UI_SAMPLE=1
      export TOOLBOX_APP_HOME="$HOME/Library/Application Support/com.electricity.toolbox/development/data/webview-ui-sample"
    fi
    ;;
  --webview-pilot)
    export TOOLBOX_WEBVIEW_PILOT=1
    export TOOLBOX_WEBVIEW_PILOT_FIXTURE=1
    export TOOLBOX_APP_HOME="$HOME/Library/Application Support/com.electricity.toolbox/development/data/webview-pilot"
    toolbox_webview_config="src-tauri/tauri.webview-pilot.conf.json"
    ;;
esac
if [[ "${TOOLBOX_SMOKE:-}" == "1" ]]; then
  print -r -- "TAURI_PROJECT=$root_dir/desktop"
  print -r -- "PYTHON_SERVICE=$root_dir/desktop/python/service_bridge.py"
  if [[ "${TOOLBOX_WEBVIEW_PILOT:-}" == "1" ]]; then
    print -r -- "WEBVIEW_PILOT_CONFIG=$root_dir/desktop/$toolbox_webview_config"
    print -r -- 'WEBVIEW_PILOT_MODE=offline'
  fi
  if [[ "${TOOLBOX_WEBVIEW_UI:-}" == "1" ]]; then
    print -r -- "WEBVIEW_UI_CONFIG=$root_dir/desktop/$toolbox_webview_config"
    if [[ "${TOOLBOX_WEBVIEW_UI_SAMPLE:-}" == "1" ]]; then
      print -r -- "WEBVIEW_UI_MODE=offline"
    else
      print -r -- "WEBVIEW_UI_MODE=live"
    fi
    print -r -- "WEBVIEW_UI_SAMPLE=${TOOLBOX_WEBVIEW_UI_SAMPLE:-0}"
    print -r -- "WEBVIEW_PILOT_FIXTURE=${TOOLBOX_WEBVIEW_PILOT_FIXTURE:-0}"
    print -r -- "WEBVIEW_DATA_HOME=$TOOLBOX_APP_HOME"
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
if [[ -n "$toolbox_webview_config" ]]; then
  exec npm run dev -- --config "$toolbox_webview_config"
fi
exec npm run dev
