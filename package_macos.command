#!/bin/zsh
set -euo pipefail

workspace_root=${0:A:h}
cd "$workspace_root"

if ! command -v git >/dev/null 2>&1 || [[ ! -d .git ]]; then
  print -r -- "This packaging command must be run from the project Git workspace."
  exit 1
fi

build_date=${BUILD_DATE:-$(date +%Y%m%d)}
package_name="电力工具箱_macOS_${build_date}"
output_dir="$workspace_root/dist"
archive_path="$output_dir/${package_name}.zip"
staging_root=$(mktemp -d "${TMPDIR:-/tmp}/electricity-toolbox-package.XXXXXX")
staging_project="$staging_root/$package_name"

cleanup() {
  rm -rf "$staging_root"
}
trap cleanup EXIT INT TERM

mkdir -p "$staging_project" "$output_dir"

# Export tracked files plus current untracked source files. Git's ignore rules keep
# local credentials, browser profiles, virtual environments, caches and outputs out.
git ls-files -co --exclude-standard -z |
  while IFS= read -r -d '' source_path; do
    # These two calibration previews are regenerated from the source image and JSON.
    case "$source_path" in
      500kV断面分析/data/500kv_node_position_candidates.png|\
      500kV断面分析/data/500kv_node_position_candidates_contact_sheet.png) continue ;;
    esac
    if [[ -e "$source_path" || -L "$source_path" ]]; then
      printf '%s\0' "$source_path"
    fi
  done |
  rsync -a --from0 --files-from=- ./ "$staging_project/"

chmod +x \
  "$staging_project/setup_macos.command" \
  "$staging_project/00_启动/电力工具箱.command" \
  "$staging_project/wps自动/启动WPS专用Chrome.command" \
  "$staging_project/package_macos.command" \
  2>/dev/null || true

commit_id=$(git rev-parse --short HEAD 2>/dev/null || print -r -- "unknown")
if [[ -n "$(git status --short)" ]]; then
  workspace_state="包含打包时工作区内尚未提交的当前修改"
else
  workspace_state="工作区无未提交修改"
fi

cat > "$staging_project/新Mac首次使用说明.txt" <<EOF
电力工具箱 macOS 迁移包
打包日期：${build_date}
Git 基准版本：${commit_id}
版本状态：${workspace_state}

首次使用：
1. 把整个“${package_name}”文件夹复制到新 Mac 的“文稿”或其他可写目录。
2. 打开“终端”，输入 cd 和一个空格，再把该文件夹拖入终端，按回车。
3. 依次执行：
   xattr -dr com.apple.quarantine .
   chmod +x setup_macos.command 00_启动/电力工具箱.command
   ./setup_macos.command
4. 安装完成后，双击“00_启动/电力工具箱.command”启动。
5. 按 README.md 的“首次登录和本地配置”章节，在新电脑重新配置账号与登录态。

说明：
- 本包不含旧电脑的 .venv、缓存、输出文件、真实配置、Cookie 或浏览器登录态。
- WPS 写入工具自带随包默认配置，首次使用即可看到写入项目和区域；请在新电脑重新选择来源表格文件并登录 WPS。
- 首次安装需要网络连接，以及 Python 3.11 或更新版本。
- 私有数据上传功能另需 Node.js 18 或更新版本；其他 Python 工具不依赖 Node.js。
EOF

rm -f "$archive_path"
if command -v ditto >/dev/null 2>&1; then
  ditto -c -k --sequesterRsrc --keepParent "$staging_project" "$archive_path"
else
  (cd "$staging_root" && zip -qry "$archive_path" "$package_name")
fi

archive_size=$(du -h "$archive_path" | awk '{print $1}')
print -r -- "Package created: $archive_path"
print -r -- "Archive size: $archive_size"
