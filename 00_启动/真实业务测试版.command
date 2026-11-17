#!/bin/zsh
set -euo pipefail
launcher_dir=${0:A:h}
exec "$launcher_dir/Tauri开发版.command" --webview-ui
