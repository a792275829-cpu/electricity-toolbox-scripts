#!/bin/zsh
set -euo pipefail

launcher_dir=${0:A:h}
root_dir=${launcher_dir:h}
exec "$root_dir/电力工具箱.command"
