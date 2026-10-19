# Tauri 桌面版

Tauri 2 负责单实例和系统托盘；主窗口和更新窗口直接使用原来的 Tk 界面，React 仅保留启动故障提示。当前仅维护 Apple 芯片 Mac（macOS 14+），不包含 Intel Mac。

主界面没有另一套网页实现：原来的布局、控件、字体主题、日期选择、文件对话框、快捷键、任务取消、账号保存、日志、WPS 配置表格和弹窗，都由原版代码执行。Mac 发行包固定携带 Tcl/Tk 8.6.18，与本机原版一致，避免 Tk 9 的字体／缩放差异。启动和重新打开时后台检查 GitHub 正式版，发现新版在“电力工作台”下显示红色 `new`。点击可查看更新说明、下载进度、取消或重试；点击一次“下载并更新”后，由 Tauri 下载并验证内置公钥签名，检查任务和保存配置，再自动替换应用并重启。右下角更新入口已移除，Mac 菜单栏图标仍保留手动检查。侧栏仅展示业务模块，启动默认进入“导出上网电量”。现有 Tk 启动器保留。

## 日常维护

双击仓库的 `00_启动/Tauri开发版.command`。

首次开发需要 Node 22.23.3、Rust stable、项目根目录 `.venv`。需要 Xcode Command Line Tools；Python 依赖由根目录 `setup_macos.command` 初始化。前端首次执行 `cd desktop && npm ci`。

开发启动器运行 `npm run dev`，自动编译必要的 Rust 改动。日常业务和界面仍维护原 Python 文件，修改已加载的界面代码后关闭并重新启动即可，无需制作安装包；脚本任务在下一次运行时读取脚本源码。React/CSS 只影响启动故障提示。修改 `desktop/python/native_bridge.py` 或依赖后重启应用。

接收电脑安装发行包即可使用，内置 Python 3.11.15、Node 22.23.3、Playwright 1.55.0 和 Chromium。浏览器登录和网站取数仍需要网络。

## 本地构建

在目标原生构建机执行：

```sh
python packaging/tauri/prepare_payload.py --target aarch64-apple-darwin
cd desktop
npm ci
npm run build -- --bundles app
cd ..
python packaging/tauri/build_dmg.py desktop/src-tauri/target/release/bundle/macos/电力工具箱.app --output dist
```

构建机需要 `uv`、Node/npm、Rust 和根目录 Python 依赖。资源输出到 Git 忽略的 `desktop/src-tauri/payload/`；安装包位于 `desktop/src-tauri/target/release/bundle/`，指定 `--target` 时多一层目标目录。

本地生成签名包需要设置 `TAURI_SIGNING_PRIVATE_KEY` 和 `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`；不要把私钥写入仓库、命令行参数或日志。首次生成的本机私钥保存在 `~/.config/electricity-toolbox/signing/updater.key`，目录权限 700、文件权限 600。请单独备份，不能每次发版重新生成。其公钥已写入 `src-tauri/tauri.conf.json`。

## GitHub 发布与应用内更新

仓库：a792275829-cpu/electricity-toolbox-scripts。`.github/workflows/desktop-ci.yml` 执行Mac 原生检查；`desktop-release.yml` 由版本标签或指定已有标签触发。

1. 提交需要分发的源码和桌面相关文件，先让 Desktop checks 通过。当前工作区中的未提交、未跟踪业务源码不会自动出现在 GitHub 的构建机上。
2. `.venv/bin/python packaging/tauri/version.py set X.Y.Z --notes "具体新增或修复内容"` 同步 npm、Tauri、Cargo、锁文件版本和根目录 `CHANGELOG.md`；多项变更重复传入 `--notes`。补充实际验证结果，提交这些修改。
3. 执行 `.venv/bin/python packaging/tauri/version.py check --tag vX.Y.Z --require-notes`，再创建并推送对应标签 `vX.Y.Z`。发布流程自动提取该版本日志并拼接通用安装说明，缺少日志时阻止发布。
4. Actions 创建草稿 → Mac arm64 原生构建 → 生成并上传 `ElectricityToolbox-<版本>-apple-silicon.dmg` 及 `.sha256` → 校验 GitHub 附件摘要和大小 → 发布。同时上传统一命名的签名 `.app.tar.gz`、`.sig` 与 `latest.json`，供新版自动更新。0.1.6 及更早的 PKG 客户端需从发布页面下载 DMG 迁移一次。
5. 新版客户端直接请求 GitHub `/releases/latest`，只提示数字版本更高的正式版。自动检查错误保持安静，菜单手动检查显示具体错误。

GitHub Actions 使用仓库 Secrets `TAURI_SIGNING_PRIVATE_KEY`、`TAURI_SIGNING_PRIVATE_KEY_PASSWORD`（当前私钥无密码，后者可留空）。本次已配置前者；发布流程缺少签名私钥时会失败，不会发布无签名更新。不要在客户端内置 GitHub PAT。更新索引只包含 `darwin-aarch64`，构建失败保留草稿，不发布不完整版本。

Mac 当前采用 ad-hoc 签名，尚未使用 Apple Developer ID 或公证；外部分发首次打开仍可能需要在系统“隐私与安全性”中允许。更新包的 Tauri 签名与系统发行证书是两个独立机制。

## 数据与更新行为

发行版从应用资源中运行内置环境，用户目录使用以下结构：

- `app/`：清单管理的业务源码。
- `data/`：账号配置、各工具独立登录态／profile、数据总表和输出。
- `samples/`：全部模块的独立测试样例。
- `state/`、`backups/`、`logs/`、`cache/`：更新记录、备份、日志和缓存。

用户目录为 `~/Library/Application Support/com.electricity.toolbox`。开发版直接执行仓库源码，使用根目录下 `development/data/`，与已安装版本分开。业务模块的中文名称、相对路径、输出文件名及原版源码启动方式保留。

WPS 的 3 项写入配置及 34 个写入区域作为功能默认配置随软件打包，首次安装写入 `data/wps自动/wps_excel_to_kdocs_config.json`。升级保留已有本机配置；新电脑需要重新选择本地来源文件并登录 WPS。默认业务配置维护在源码的 `wps自动/wps_excel_to_kdocs_config.defaults.json`，不包含账号、Cookie、浏览器 profile 或旧电脑的文件路径。

首次新版启动会按旧安装清单迁移原 `workspace/`，备份并修正内部绝对路径，失败回滚。更新仅替换 `app/` 的清单文件，保留 `data/` 和 `samples/`；本地源码修改仍阻止覆盖。完整目录职责及迁移说明见 [目录约定](docs/directory-layout.md)。

## 首次安装与旧版迁移

打开 DMG，先退出所有正在运行的工具箱，再双击“安装到我的应用程序.command”。它使用包内 Python 将应用安装到 `~/Applications/电力工具箱.app`，验证应用签名后自动打开，不调用系统安装器或管理员授权。也可手动拖入个人用户目录的 Applications 文件夹。安装器拒绝覆盖其他应用或较新版本，替换失败时恢复旧应用；若恢复也失败，保留备份供恢复。

旧 PKG 版从 DMG 迁移一次。`~/Library/Application Support/com.electricity.toolbox` 继续使用，账号、独立登录态、业务数据和样例保留。`/Applications` 的旧副本不会自动删除；确认新版运行正常后可手动删除旧副本，并更新 Dock 或桌面快捷方式，今后从 `~/Applications` 启动。

## 自动更新行为

点击“下载并更新”后，原生 Tauri 组件读取 `latest.json`，核对所选版本，下载应用包并验证内置公钥签名。下载可取消，期间不锁定业务操作。验签后再次检查任务并保存配置；有任务或保存失败时阻止替换并说明原因。安装阶段锁定业务输入，失败恢复可用；成功后正常退出原版 Tk 并自动重启新版，不经过 PKG 安装器。

仅允许在当前用户拥有、可写的 `~/Applications/电力工具箱.app` 中自动更新。系统目录、挂载 DMG 和其他路径均提示迁移，避免正常流程触发管理员授权。开发版源码启动不执行自动安装，继续使用原开发启动器。首次运行的 macOS 安全确认及设备管理策略仍由系统决定。


## 验证

```sh
python -m unittest discover -s desktop/tests/python -v
cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked
npm --prefix desktop test
npm --prefix desktop run web:build
```

原版交互回归在 `电力工具箱/` 中运行 `../.venv/bin/python -m unittest tests.test_platform_ui tests.test_date_inputs tests.test_task_widgets tests.test_wind_forecast_page tests.test_summary_upload_page tests.test_spot_price_page tests.test_transaction_info_page tests.test_login_ui tests.test_catalog_loading`。

真实取数、账号登录、Excel/WPS 写入、上传和 Mac 安装／更新，需要对应系统和有效账号进行业务验收。自动测试不会向生产网站上传测试数据。
