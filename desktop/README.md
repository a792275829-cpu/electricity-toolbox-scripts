# Tauri 桌面版

桌面版分为三层：原版 Tk 界面、Rust/Tauri 应用管理、独立 Python 业务服务。Tauri 负责单实例、系统托盘、进程生命周期和请求转发；原界面通过服务执行任务，React 仅保留启动故障提示。当前仅维护 Apple 芯片 Mac（macOS 14+），不包含 Intel Mac。

主界面没有另一套网页实现：原来的布局、控件、字体主题、日期选择、文件对话框、快捷键、任务取消、账号保存、日志、WPS 配置表格和弹窗，都由原版代码执行。Mac 发行包固定携带 Tcl/Tk 8.6.18，与本机原版一致，避免 Tk 9 的字体／缩放差异。启动和重新打开时后台检查 GitHub 正式版，发现新版在“电力工作台”下显示红色 `new`。点击可查看更新说明，点击“开始更新”后自动执行后续流程，可在下载阶段取消；优先下载增量补丁，在临时副本中还原并校验完整应用，再生成安装镜像。补丁不适用时自动下载完整 DMG。镜像准备完成后自动只读挂载并校验应用，正常退出工具箱，由独立进程运行镜像安装器并重新打开新版；无需手动操作。右下角更新入口已移除，Mac 菜单栏图标仍保留手动检查。侧栏仅展示业务模块，启动默认进入“导出上网电量”。现有 Tk 启动器保留。

三层的接口、生命周期和验证说明见 [架构说明](docs/architecture.md)。外壳通过 `LSUIElement` 和后台激活策略保持隐藏，只有原生界面显示工具箱 Dock 图标。分层不等于单进程：空闲时有应用管理、界面和业务服务三个进程，耗时业务按需启动工作进程。

## 日常维护

双击仓库的 `00_启动/Tauri开发版.command`。

首次开发需要 Node 22.23.3、Rust stable、项目根目录 `.venv`。需要 Xcode Command Line Tools；Python 依赖由根目录 `setup_macos.command` 初始化。前端首次执行 `cd desktop && npm ci`。

开发启动器运行 `npm run dev`，自动编译必要的 Rust 改动。界面仍维护原 Python 页面；任务适配在 `toolbox/ui_tasks.py`，业务服务和工作流在 `toolbox/service.py`、`toolbox/workflows.py`。修改已加载的界面或服务后重启应用；脚本任务在下一次运行时读取业务脚本源码。React/CSS 只影响启动故障提示。`00_启动/电力工具箱.command` 保留不依赖 Node/Rust 的原版源码启动方式。

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

DMG 内部仍需构建 `.app` 作为中间产物，并保留 macOS ad-hoc 代码签名；不生成或分发独立 App 压缩包。

## GitHub 发布与应用内更新

仓库：a792275829-cpu/electricity-toolbox-scripts。`.github/workflows/desktop-ci.yml` 执行Mac 原生检查；`desktop-release.yml` 由版本标签或指定已有标签触发。

1. 提交需要分发的源码和桌面相关文件，先让 Desktop checks 通过。当前工作区中的未提交、未跟踪业务源码不会自动出现在 GitHub 的构建机上。
2. `.venv/bin/python packaging/tauri/version.py set X.Y.Z --notes "具体新增或修复内容"` 同步 npm、Tauri、Cargo、锁文件版本和根目录 `CHANGELOG.md`；多项变更重复传入 `--notes`。补充实际验证结果，提交这些修改。
3. 执行 `.venv/bin/python packaging/tauri/version.py check --tag vX.Y.Z --require-notes`，再创建并推送对应标签 `vX.Y.Z`。发布流程自动提取该版本日志并拼接通用安装说明，缺少日志时阻止发布。
4. Actions 创建草稿 → Mac arm64 原生构建 → 生成完整 DMG 与校验文件 → 为最近三个正式版本生成适用的直达补丁并实际还原验签 → 上传完整包、补丁与 `update-manifest.json` → 核对 GitHub 附件摘要、索引与大小 → 发布。校验器继续拒绝独立 App 压缩包、`.sig` 及旧 `latest.json`。
5. 新版客户端直接请求 GitHub `/releases/latest`，只提示数字版本更高的正式版。自动检查错误保持安静，菜单手动检查显示具体错误。

构建失败保留草稿，不发布不完整版本。不再使用 Tauri 更新签名私钥，也不上传 `.app.tar.gz`、`.sig` 或 `latest.json`。不要在客户端内置 GitHub PAT。

日常补丁包含基准／目标版本、完整文件摘要、权限与符号链接清单及变化文件。客户端验证 GitHub 索引摘要和每个补丁摘要，选择总下载量最小且小于完整包的路径；可直达，也可连续应用历史补丁。每步严格检查旧应用与还原结果，任何不匹配均回退完整 DMG，取消操作不回退。完整包每个版本都保留，历史补丁附件不得清理，否则旧版会退回完整更新。跨主／次版本默认全量；同系列重大更新可在 Actions 手动发布时选择 `full_only`。0.1.10 及更早更新器需要先完整升级一次。

增量只修改临时应用副本；自动更新等待旧进程退出后复用镜像内安装器及其回滚逻辑，不会在线覆盖运行中的应用或业务数据。没有新增依赖、管理员权限或客户端令牌。

Mac 当前采用 ad-hoc 签名，尚未使用 Apple Developer ID 或公证；首次打开仍可能需要在系统“隐私与安全性”中允许。

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

## DMG 更新行为

点击“开始更新”后，自动保存账号设置并阻止新业务任务，后台下载增量补丁或完整 DMG；核对大小、SHA-256、镜像完整性、应用标识、版本和签名。下载可取消，取消不会触发完整包回退。校验通过后，独立安装进程就绪才自动退出旧程序；安装进程等待旧进程结束，再安装到 `~/Applications/电力工具箱.app` 并重新打开。原配置、登录态、业务数据和样例保留，后续无需继续操作。

安装器拒绝替换运行中的应用，替换失败时回滚；准备失败时恢复原界面，退出后安装失败会记录诊断并尝试重新打开保留的应用。0.1.11 及更早更新器需要按旧提示完成一次升级，之后才具备全自动流程。首次从 DMG 安装仍可双击镜像中的安装命令。DMG 内部包含运行所需 `.app`，它不作为独立附件发布。

## 验证

```sh
python -m unittest discover -s desktop/tests/python -v
cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked
npm --prefix desktop test
npm --prefix desktop run web:build
```

原版交互回归在 `电力工具箱/` 中运行 `../.venv/bin/python -m unittest tests.test_platform_ui tests.test_date_inputs tests.test_task_widgets tests.test_wind_forecast_page tests.test_summary_upload_page tests.test_spot_price_page tests.test_transaction_info_page tests.test_login_ui tests.test_catalog_loading`。

真实取数、账号登录、Excel/WPS 写入、上传和 Mac 安装／更新，需要对应系统和有效账号进行业务验收。自动测试不会向生产网站上传测试数据。
