# Tauri 桌面版

桌面版分为三层：原版 Tk 界面、Rust/Tauri 应用管理、独立 Python 业务服务。Tauri 负责单实例、系统托盘、进程生命周期和请求转发；原界面通过服务执行任务，正式模式的 React 保留启动故障提示；完整 WebView / React 源码版见下文。当前仅维护 Apple 芯片 Mac（macOS 14+），不包含 Intel Mac。

正式版主界面仍由 Tk 实现：原来的布局、控件、字体主题、日期选择、文件对话框、快捷键、任务取消、账号保存、日志、WPS 配置表格和弹窗，都由原版代码执行。Mac 发行包固定携带 Tcl/Tk 8.6.18，与本机原版一致，避免 Tk 9 的字体／缩放差异。启动和重新打开时后台检查 GitHub 正式版，发现新版在“电力工作台”下显示红色 `new`。点击可查看更新说明，点击“开始更新”后自动执行后续流程，可在下载阶段取消；优先下载增量补丁，在临时副本中还原并校验完整应用，再生成安装镜像。补丁不适用时自动下载完整 DMG。镜像准备完成后自动只读挂载并校验应用，正常退出工具箱，由独立进程运行镜像安装器并重新打开新版；无需手动操作。右下角更新入口已移除，Mac 菜单栏图标仍保留手动检查。侧栏仅展示业务模块，启动默认进入“导出上网电量”。现有 Tk 启动器保留。

三层的接口、生命周期和验证说明见 [架构说明](docs/architecture.md)。外壳通过 `LSUIElement` 和后台激活策略保持隐藏，只有原生界面显示工具箱 Dock 图标。分层不等于单进程：空闲时有应用管理、界面和业务服务三个进程，耗时业务按需启动工作进程。

## 日常维护

界面日常调试固定使用 `00_启动/源码调试版.command`：直接运行当前 `main` 工作副本及根目录 `.venv`，不复制源码、不打包；窗口标注“源码调试版”并默认打开 WPS 写入页面。Rust 核心沿用源码构建结果；修改 Rust 后先执行 `.venv/bin/python native/price-xlsx/build.py`。开发配置与登录资料继续使用 `~/Library/Application Support/com.electricity.toolbox/development/data/`，与安装版独立；界面代码修改后关闭调试窗口，再双击同一入口重启。

禁止把调试源码复制到正式版用户目录的 `app/` 或已安装 `.app`。正式版提示“检测到本地源码修改”时，按[启动故障恢复约定](docs/startup-recovery.md)备份并恢复发行源码；该文档同时记录后续恢复入口的改进要求。

双击仓库的 `00_启动/Tauri开发版.command`。

首次开发需要 Node 22、Rust stable、项目根目录 `.venv`。需要 Xcode Command Line Tools；Python 依赖由根目录 `setup_macos.command` 初始化。前端首次执行 `cd desktop && npm ci`。

开发启动器运行 `npm run dev`，自动编译必要的 Rust 改动。界面仍维护原 Python 页面；任务适配在 `toolbox/ui_tasks.py`，业务服务和工作流在 `toolbox/service.py`、`toolbox/workflows.py`。修改已加载的界面或服务后重启应用；脚本任务在下一次运行时读取业务脚本源码。不带参数时 React/CSS 影响启动故障提示；`--webview-ui` 使用完整 React 界面。`00_启动/电力工具箱.command` 保留不依赖 Node/Rust 的原版源码启动方式。

接收电脑安装发行包即可使用，内置 Python 3.11.15、Node 22.18.0（随 Playwright 轮子）、Playwright 1.55.0 和 Chromium。浏览器登录和网站取数仍需要网络。

## WebView / React 完整界面源码版

真实业务测试请双击 `00_启动/真实业务测试版.command`。窗口标题为“WebView 真实业务测试版”，底栏显示“真实业务模式”；账号区可“保存并验证”和“登录当前工具”，各业务页也保留独立登录入口，WPS 使用自己的登录按钮。填写完整账号后可自动保存，或手动保存并验证，再选择业务文件运行。登录态、配置和浏览器目录仍按工具隔离。启动入口会清除继承的样例模式标记，避免真实模式误用离线服务。

若离线样例窗口仍在运行，先退出样例，再双击真实业务入口。离线窗口标题为“WebView 离线样例”，底栏明确显示“离线样例”；它用于验收，不用于真实登录、上传或写入。


```bash
./00_启动/Tauri开发版.command --webview-ui
# 不联网的验收模式
./00_启动/Tauri开发版.command --webview-ui-sample
```

完整 React 界面按原版 16 个页面的三栏布局、字段、按钮和说明复刻，包含双月日期选择、任务中心、账号、诊断、日志、手动市场数据及 WPS 配置／区域编辑器。业务继续调用原 `ToolboxService`、任务引擎、解析器及写入逻辑。正式安装版和不带参数的启动入口仍使用 Tk，待真实业务验收后切换。

两种模式均使用独立应用标识 `com.electricity.toolbox.webview-ui`，仅在 debug 构建启用。数据分别位于 `~/Library/Application Support/com.electricity.toolbox/development/data/webview-ui/data/` 和 `webview-ui-sample/data/`，不读取正式版账号与浏览器状态。真实模式需要自行填写账号和业务文件；样例模式仅允许运行上网电量导出（最多 3 天），其他页面可查看和编辑本地配置。样例模式不会登录网站或上传业务数据。

需要根 `.venv`、Node/npm、Rust 和 Xcode Command Line Tools。右下角“重启源码版”会先保存完整账号、检查任务和未应用的接线图编辑，然后重新启动开发入口及业务服务。版本入口复用原更新服务；源码开发环境不用于实际安装发行包。

验收与原版对照：

```bash
./00_启动/源码调试版.command --ui-reference
.venv/bin/python tooling/webview_ui_audit.py --pid <logs/webview-ui-process.json 中的 ui_pid>
```

原版捕获使用隔离配置，输出 16 页截图与控件树至 `logs/ui-reference/`。AX 验收要求控制端已有 macOS 辅助功能权限，只允许操作离线样例窗口；检查 16 页导航、日期读写、取消、真实样例 Excel 回读、WPS 区域弹窗及侧栏开合，输出 `logs/ui-replica/summary.json`。不通过 DOM 注入替代系统操作。真实网站登录、上传、在线 WPS 写入和安装更新需另行业务验收；目前不宣称逐像素相同。结果与边界见[完整界面验证记录](docs/2026-11-17-webview-ui.md)。真实抓取、PDF、出力分析、交易系统副本写入及 WPS 在线读取的结果见[真实业务验证](docs/2026-11-17-webview-business.md)。16 个业务、36 个操作的接入清单和覆盖边界见[全业务接入覆盖](docs/2026-11-17-webview-business-coverage.md)。

## WebView / React 导出页试用

在仓库根目录运行：

```sh
./00_启动/Tauri开发版.command --webview-pilot
```

这是独立的开发试用模式：显示 React“导出上网电量”页面，不启动 Tk，使用独立应用标识 `com.electricity.toolbox.webview-pilot`；业务解释器固定使用根 `.venv`。需要现有 Node、npm、Rust 和 Xcode Command Line Tools。普通开发入口、源码启动及正式版仍使用原 Tk 界面。

试用默认离线：开始、结束日期由原业务目录提供默认值，通过原 `prepare_task` 校验；开始导出走现有 Tauri → Python JSON 通信、任务引擎、独立工作进程、取消和日志。只将网站采集替换为已知样例，调用原导出器生成真实 Excel，重新打开核对“汇总”表的标题、机组、数据与数字格式。每次最多导出 3 天，反向日期立即拒绝；开始导出在任务运行时禁用，取消完成后恢复。不会读取实际账号、登录网站或修改已有业务工作簿。

数据位于 `~/Library/Application Support/com.electricity.toolbox/development/data/webview-pilot/data/`。样例结果位于其中的 `上网电量抓取/输出/<本次任务标识>/上网电量汇总_YYYY-MM-DD.xlsx`，每次使用新目录；页面可直接打开输出目录和生成文件。配置、数据、浏览器与正式版隔离；试用入口仅在 Rust debug 构建中生效，不属于正式发布功能。

打开窗口后，用第二个终端从仓库根目录执行真实 macOS AX 验收：

```sh
.venv/bin/python tooling/webview_accessibility_audit.py
```

脚本从 `logs/webview-pilot-process.json` 读取当前试用 PID，也支持 `--pid PID`。控制端必须已有辅助功能权限；脚本不修改系统授权。它编译本地 Swift AX 客户端，经真实系统接口读写日期、点击按钮、检查状态、取消任务，然后读取实际生成的两份工作簿；不使用 DOM 注入或浏览器模拟来代替原生操作。WebKit 的稳定 DOM 标识通过 `AXDOMIdentifier` 读取，并检查同次及跨任务一致性。

全部通过返回 0；结果为 `logs/webview-accessibility-summary.json`，完整 AX 树为 `logs/webview-ax-*.json`。当前首个页面六项主验收和反向日期／取消两项附加验收通过；这不代表其他模块、真实登录取数或所有 macOS 版本已经验证。测量与限制见[试用验证记录](docs/2026-11-17-webview-accessibility.md)。

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

构建机需要 `uv`、Node/npm、Rust 和根目录 Python 依赖；`.venv` 里装有 `fontTools` 时打包会把兜底中文字体裁成 GB2312 子集（23.96 → 5.45 MiB），没有则保留完整字体并打印警告。资源输出到 Git 忽略的 `desktop/src-tauri/payload/`；安装包位于 `desktop/src-tauri/target/release/bundle/`，指定 `--target` 时多一层目标目录。

`payload/runtime/` 只保留一份 Node：Python Playwright 轮子自带的 `driver/node`，业务 JS 脚本通过 `toolbox.runtime.node_executable()` 复用它；打包时 `prune_runtime.py` 会删除独立暂存的 `runtime/node`、`pip`/`setuptools`、Tk 9 和重复的 `python3` 副本。

DMG 内部仍需构建 `.app` 作为中间产物，并保留 macOS ad-hoc 代码签名；不生成或分发独立 App 压缩包。

## GitHub 发布与应用内更新

仓库：a792275829-cpu/electricity-toolbox-scripts。`.github/workflows/desktop-ci.yml` 执行Mac 原生检查；`desktop-release.yml` 由版本标签或指定已有标签触发。

1. 提交需要分发的源码和桌面相关文件，先让 Desktop checks 通过。当前工作区中的未提交、未跟踪业务源码不会自动出现在 GitHub 的构建机上。
2. `.venv/bin/python packaging/tauri/version.py set X.Y.Z --notes "具体新增或修复内容"` 同步 npm、Tauri、Cargo、锁文件版本和根目录 `CHANGELOG.md`；多项变更重复传入 `--notes`。补充实际验证结果，提交这些修改。
3. 执行 `.venv/bin/python packaging/tauri/version.py check --tag vX.Y.Z --require-notes`，再创建并推送对应标签 `vX.Y.Z`。发布流程自动提取该版本日志并拼接通用安装说明，缺少日志时阻止发布。
4. Actions 创建草稿 → Mac arm64 原生构建 → 生成完整 DMG 与校验文件 → 为最近三个正式版本生成适用的直达补丁并实际还原验签 → 上传完整包、补丁与 `update-manifest.json` → 核对 GitHub 附件摘要、索引与大小 → 发布。校验器继续拒绝独立 App 压缩包、`.sig` 及旧 `latest.json`。
5. 新版客户端请求 GitHub API `/releases/latest`，只提示数字版本更高的正式版。API 返回 HTTP 403／429 时，自动通过 GitHub 正式版页面与附件列表检测，并从附件 HEAD 响应读取精确大小，继续校验 GitHub 公布的 SHA-256；不需要客户端令牌。备用检测失败保留原始 HTTP 错误。自动检查错误保持安静，菜单手动检查显示具体错误。

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

安装器拒绝替换运行中的应用，替换失败时回滚；准备失败时恢复原界面，退出后安装失败会记录诊断并尝试重新打开保留的应用。0.1.12 及更早更新器需要按旧提示完成一次升级，之后才具备全自动流程。首次从 DMG 安装仍可双击镜像中的安装命令。DMG 内部包含运行所需 `.app`，它不作为独立附件发布。

## 验证

```sh
python -m unittest discover -s desktop/tests/python -v
cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked
npm --prefix desktop test
npm --prefix desktop run web:build
```

原版交互回归在 `电力工具箱/` 中运行 `../.venv/bin/python -m unittest tests.test_platform_ui tests.test_date_inputs tests.test_task_widgets tests.test_wind_forecast_page tests.test_summary_upload_page tests.test_spot_price_page tests.test_transaction_info_page tests.test_login_ui tests.test_catalog_loading`。

真实取数、账号登录、Excel/WPS 写入、上传和 Mac 安装／更新，需要对应系统和有效账号进行业务验收。自动测试不会向生产网站上传测试数据。
