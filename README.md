# 电力工作工具箱 macOS 版

这是一个面向 macOS 的电力业务桌面工具集合。统一入口为 Tkinter 图形界面，当前集成 15 个工具，名称与工具箱侧栏一致：导出上网电量、风力预测截图、市场表更新、现货价格分析表更新、交易系统信息更新、功率调整收益统计表更新、广东电价预测、出清结果汇总上传、广东运行日报解析上传、出力受限分析、500kV断面分析、南方分公司电力市场日报、早报生成、上传集团每日数据和 WPS写入工具。

仓库公开地址：[a792275829-cpu/electricity-toolbox-scripts](https://github.com/a792275829-cpu/electricity-toolbox-scripts)

[下载最新 macOS 版](https://github.com/a792275829-cpu/electricity-toolbox-scripts/releases/latest) · [更新日志](CHANGELOG.md) · [贡献指南](CONTRIBUTING.md) · [问题反馈](https://github.com/a792275829-cpu/electricity-toolbox-scripts/issues) · [安全政策](SECURITY.md)

本仓库代码采用 [MIT 许可证](LICENSE)。随附的 Noto Serif SC 字体采用 [SIL Open Font License 1.1](风力预测截图/fonts/OFL.txt)，其他第三方依赖或资源遵循其各自的许可证。部分功能需要相应业务系统的账号和访问权限。

## DMG 桌面版

macOS 14+、Apple 芯片电脑使用 DMG 桌面版，已内置 Python、Node、Chromium 与 Tcl/Tk。打开 DMG，先退出旧工具箱，再双击“安装到我的应用程序.command”，安装到 `~/Applications/电力工具箱.app` 并自动打开；也可手动拖入个人用户目录的 Applications 文件夹。

后续在应用中点击“开始更新”，自动完成下载、校验、退出旧程序、安装和重新启动，无需再操作镜像或安装命令。日常优先选择下载量较小的增量升级路径，补丁不适用时自动回退完整 DMG；配置、登录态、业务数据和样例保留。0.1.12 及更早更新器仍需按旧提示完成一次升级，之后才能全自动更新。旧 PKG 版需要从 DMG 迁移一次；确认新版正常后，可手动清理 `/Applications` 的旧副本并更新 Dock 图标。详细说明见 [桌面版安装与更新](desktop/README.md)。

以下环境要求和初始化步骤适用于源码运行方式。

## 目录

- [DMG 桌面版](#dmg-桌面版)
- [环境要求](#环境要求)
- [首次安装](#首次安装)
- [启动工具箱](#启动工具箱)
- [首次登录和本地配置](#首次登录和本地配置)
- [功能使用说明](#功能使用说明)
- [后台任务与取消](#后台任务与取消)
- [迁移到另一台 Mac](#迁移到另一台-mac)
- [代理和网络](#代理和网络)
- [常见问题](#常见问题)
- [测试和自检](#测试和自检)

## 环境要求

- macOS 14 或更新版本（与桌面发行版一致；更旧的系统未验证）
- Python 3.11（与发行运行时一致）
- Google Chrome，用于网页登录、上传和 WPS/KDocs 流程
- Node.js 18 或更新版本，仅“出清结果汇总上传”的上传功能需要
- 能访问相关业务系统的网络环境；如公司网络要求代理或 VPN，请先连接

项目使用根目录下的 `.venv` 作为 Python 环境。不要把系统 Python 或个人电脑上的绝对解释器路径写入项目配置。

## 首次安装

### 1. 获取项目

使用 Git 克隆：

```bash
git clone https://github.com/a792275829-cpu/electricity-toolbox-scripts.git
cd electricity-toolbox-scripts
```

也可以从 GitHub 下载 ZIP，解压后进入项目根目录。

### 2. 解除 macOS 下载限制

从网页下载的项目可能带有隔离属性。首次使用时在项目根目录执行：

```bash
xattr -dr com.apple.quarantine .
chmod +x setup_macos.command "00_启动/电力工具箱.command" \
  "wps自动/启动WPS专用Chrome.command"
```

### 3. 自动安装依赖

在项目根目录运行：

```bash
./setup_macos.command
```

安装脚本会完成以下工作：

1. 创建项目专用 Python 环境 `.venv`。
2. 使用 Python 3.11，按 `requirements.txt` 引用的发行锁文件安装并校验 Python 依赖。
3. 安装 Playwright Chromium。
4. 检测 Node.js/npm；如果存在，则安装私有数据上传工具的 Node 依赖。
5. 修复工具箱和 WPS 专用 Chrome 的 `.command` 启动权限。

如果暂时没有 Node.js，其他 Python 工具仍可使用，只有“出清结果汇总上传”的上传功能不可用，汇总仍可使用。安装 Node.js 后执行：

```bash
cd "电力工具脚本/private-data-uploader-tool"
npm install
```

### 4. 手动安装命令

只有自动安装失败时才需要使用以下等价命令：

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
python -m playwright install chromium

cd "电力工具脚本/private-data-uploader-tool"
npm install
```

完整分模块测试、依赖升级、业务契约与更新兼容验证见 [维护指南](tooling/README.md)。

## 启动工具箱

在 Finder 中双击：

```text
00_启动/电力工具箱.command
```

也可以在项目根目录从终端启动：

```bash
./00_启动/电力工具箱.command
```

启动器使用 `.venv/bin/python`。如果 `.venv` 或关键依赖缺失，会自动调用 `setup_macos.command`。

启动后默认显示“今日概览”：

- 左侧按“数据采集”“分析与报告”“上传与写入”分组。
- 工具页面首次打开时加载，切换页面不会清空当前状态。
- 右侧任务中心显示后台任务、运行状态和日志，并提供可用的取消操作。
- 右侧“运行诊断”常驻检查 Python、17 个工具入口文件（含 500kV 拓扑名单等运行资源）、工作目录写入权限、可用磁盘空间和可选的 Node.js 依赖。
- 诊断面板支持“重新检查”“检查文件”和“复制报告”；出现异常时先选中项目查看处理建议。

## 首次登录和本地配置

真实账号、密码、Cookie、浏览器 profile、登录态和 WPS 本机配置均由 `.gitignore` 忽略，不会上传到 GitHub。WPS 写入功能的随包默认配置单独维护，不含账号、登录态或旧电脑的本地文件路径。

### 广东现货系统账号

打开工具箱，在右侧“任务中心”下方的“清能网站账号”填写账号、密码。编辑内容先保留为草稿，点击“保存”或启动需要账号的任务时提交；关闭窗口不保存草稿。密码默认隐藏，可主动勾选“显示密码”。

应用启动后在后台验证已保存账号，主界面可正常操作。状态同时使用文字、图标和颜色提示：

| 状态 | 提示颜色与操作 |
| --- | --- |
| 待填写／待验证、正在验证账号 | 灰色 |
| ✓ 账号有效 | 绿色 |
| ✗ 账号或密码错误、连接失败、登录服务暂不可用 | 红色；点击“重试验证”重新检查已保存账号 |
| ⚠ 需要人工验证 | 橙色；在需要登录的功能页点击账号区“打开登录窗口” |

“保存”立即把账号写到本机，账号发生变化时等待 500 毫秒后台验证一次；重复保存相同内容不重复验证。启动检查等待界面显示后执行一次，不做定时轮询。账号错误、网络失败或验证码要求均停止自动验证，需手动重试或修改后保存；同一账号的手动重试至少间隔 10 秒。

验证期间可点击“停止验证”；编辑账号、关闭窗口或进入应用更新流程也会停止等待并作废旧结果。每轮界面等待最多 20 秒，单个网络请求最多 5 秒；停止后不发出后续请求，已经发出的请求按短超时收尾。取消更新不自动恢复验证，可手动重试。任意时刻只允许一个验证工作线程，新的账号检查会等待旧请求结束。

后台验证不写账号配置或工具登录态。“账号有效”表示上次账号核验通过，各工具在任务执行时建立或复用自己的独立会话；任务启动时提交账号草稿由任务自行检查登录，不额外启动一次账号验证。

统一账号保存在 `.toolbox/qingneng/config.json`（仅本机、Git 忽略、权限 `0600`）。导出上网电量、风力预测截图、市场表更新、现货价格分析表更新、交易系统信息更新、功率调整收益统计表更新、早报生成、南方分公司电力市场日报、上传集团每日数据和私有数据上传统一读取此账号。各工具的日期、路径、机构等业务配置仍保存在原来的配置文件中。

修改账号密码后，从下次启动任务开始使用新设置，各工具首次运行会重新登录。正在运行的任务继续使用启动时的设置。每个工具各自维护登录态、锁和浏览器 profile；账号修改不会删除这些文件。

业务选项与登录分开保存：账号密码优先读统一账号 `.toolbox/qingneng/config.json`，各工具的 `config.json` 只放业务或调试选项。需要时先复制对应示例再编辑，未列出的工具不需要 `config.json`：

| 工具 | 配置路径 | 可配置字段 |
| --- | --- | --- |
| 导出上网电量 | `上网电量抓取/config.json` | `headless`、`requestDelaySeconds`、`outputDir` |
| 风力预测截图 | `风力预测截图/config.json` | `headless`、`outputDir`、`orgId`、`orgName`、`unitId`、`unitName` |
| 早报生成 | `每日生产经营情况汇报自动生成工具/scripts/config.json` | `headless`、`requestDelaySeconds` |
| 广东电价预测 | `广东电价预测/config.json` | `masterWorkbook`、`predictionOutputDir`、`initialWorkbook`、`initialDate`、`refreshDays` |
| 现货价格更新 | `现货价格更新/config.json` | `headless` |
| 交易系统信息更新 | `交易系统信息更新/config.json` | `headless` |
| 广东运行日报解析上传 | `广东运行日报PDF解析/config.json` | `headless` |
| 集团每日上传 | `集团每日上传/config.json` | `headless`（仅命令行；界面始终显示浏览器）；登录读统一账号 |
| 功率调整收益统计表更新 | 不需要 | 只用命令行 `--headed`／`--login` 控制浏览器；登录读统一账号 |

```bash
cp "现货价格更新/config.example.json" "现货价格更新/config.json"
```

几条规则：

- 登录只写统一账号。示例文件不含 `username`、`password`；只有命令行调试且尚未保存统一账号时，才在工具自己的 `config.json` 里补这两项。
- 工具自己的 `config.json` 缺少账号时，登录会回退到 `上网电量抓取/config.json`，本地文件里的业务选项不会把共享账号顶掉。
- 示例文件只列真正生效的字段；`tooling/tests/test_maintenance_next.py` 会检查这一点，避免文档承诺无效开关。

下列工具各自保存独立登录态，可以并行运行，互不占用同一个浏览器 profile：

```text
上网电量抓取/auth_state.json
风力预测截图/auth_state.json
市场表更新/auth_state.json
现货价格更新/auth_state.json
交易系统信息更新/auth_state.json
功率调整收益统计表更新/auth_state.json
广东电价预测/auth_state.json
广东运行日报PDF解析/auth_state.json
南方分公司电力市场日报/.state/auth_state.json
集团每日上传/auth_state.json
每日生产经营情况汇报自动生成工具/scripts/auth_state.json
```

私有数据上传工具的登录态另见[私有数据上传配置](#私有数据上传配置)。

使用“导出上网电量”“市场表更新”或“上传集团每日数据”等功能时：

1. 在右侧“清能网站账号”填写并保存账号密码。
2. 打开对应功能页，直接运行任务。
3. 工具自动检测已有登录态，必要时使用保存的账号密码登录，并保存各模块自己的登录态。

自动登录失败时，检查账号密码、网络和网站提示后重新运行任务。不要把某个工具的 `auth_state.json` 或 `.browser-profile/` 复制给另一个工具。

右侧账号区负责填写、保存和验证统一账号，并在需要人工验证时提供当前工具的登录入口。运行诊断仅列出异常或提醒项目；没有问题时收起明细列表。

### 集团每日上传配置

配置路径 `集团每日上传/config.json`，只影响命令行（`headless`）；界面上的上传始终显示浏览器。详见[模块说明](集团每日上传/README.md)。

### 广东电价预测配置

需要自定义总表路径或输出目录时：

```bash
cp "广东电价预测/config.example.json" "广东电价预测/config.json"
```

字段含义与默认值见[模块说明](广东电价预测/README.md)。

### 私有数据上传配置

```bash
cp "电力工具脚本/private-data-uploader-tool/upload.config.example.json" \
   "电力工具脚本/private-data-uploader-tool/upload.config.json"
```

根据示例填写上传地址、匹配规则和浏览器配置。macOS 上如需显式指定 Chrome：

```text
/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
```

私有数据上传只复用账号密码配置，不再复用其他工具的浏览器登录态。它会在子工具目录维护独立文件：

```text
电力工具脚本/private-data-uploader-tool/auth_state.json
电力工具脚本/private-data-uploader-tool/auth_state.json.lock
电力工具脚本/private-data-uploader-tool/.browser-profile-uploader/
```

首次执行“预览”或“开始上传”时会自动建立登录态。网络不可达时，日志会提示检查内网、VPN、代理和 `baseUrl`。

### WPS/KDocs 配置

本机配置、登录 profile 和日志：`wps自动/wps_excel_to_kdocs_config.json`、`wps自动/wps-browser-profile/`、`wps自动/logs/`。

软件自带 `wps自动/wps_excel_to_kdocs_config.defaults.json`（3 项写入配置、34 个写入区域），首次使用且没有本机配置时自动载入；已有本机配置优先保留。桌面安装包首次安装把它初始化到用户目录 `data/wps自动/`。默认配置保留目标链接、工作表和区域，本地来源文件留空，需在新电脑重新选择并登录。

macOS 默认通过本机 `127.0.0.1:9222` 连接独立的专用 Chrome，可在 WPS 页面点“Start/open dedicated Chrome”和“Check status”，或双击：

```text
wps自动/启动WPS专用Chrome.command
```

该窗口使用独立 profile，登录后保持运行再预览或写入。完整说明见[模块说明](wps自动/README.md)。

## 功能使用说明

15 个工具按侧栏分组，每个工具的操作步骤、写入口径和故障处理在各自模块的 `README.md` 中维护，这里只列入口、默认输出和文档位置。

日期输入统一支持日历选择、手动输入和粘贴：`2026-7-6`、`2026/7/6`、`2026.7.6`、`2026年7月6日`、`20260706` 都会转换为 `YYYY-MM-DD`，无效日期提示纠正。界面中的 `D` 表示填写的基准运行日。

### 数据采集

| 工具 | 用途 | 默认输出 | 文档 |
| --- | --- | --- | --- |
| 导出上网电量 | 按日期抓取各公司、各机组上网电量并汇总 Excel | `上网电量抓取/输出/` | [说明](上网电量抓取/README.md) |
| 风力预测截图 | 抓取连续 7 个预测日的风力数据并生成表格截图 | `~/Downloads/风力预测截图/` | [说明](风力预测截图/README.md) |
| 市场表更新 | 按基准日抓取市场数据，写入年度市场 Excel | 直接更新所选工作簿 | [说明](市场表更新/README.md) |
| 现货价格分析表更新 | 抓取日前／实时出清电价，写入归湖、鮀莲、海风工作簿 | 直接更新所选工作簿 | [说明](现货价格更新/README.md) |
| 交易系统信息更新 | 按日前 D、实际 D-3 接续更新市场负荷信息 | 直接更新所选工作簿（命令行默认备份） | [说明](交易系统信息更新/README.md) |
| 功率调整收益统计表更新 | 补齐归湖、鮀莲六列，海风按日期覆盖五列 | 直接补齐原表并默认备份 | [说明](功率调整收益统计表更新/README.md) |
| 广东电价预测 | 补齐现货数据总表并生成 D+1 日前电价预测 | `广东电价预测/输出/` | [说明](广东电价预测/README.md) |
| 出清结果汇总上传 | 归类复盘目录，汇总出清结果并上传 | 复盘目录下 `*_汇总.xlsx` | [说明](电力工具脚本/电量汇总工具/README.md) |
| 广东运行日报解析上传 | 解析运行日报报价分布图并上传 PDF 与容量 Excel | PDF 所在目录的 `现货运行日报报价容量区间YYYYMMDD.xlsx` | [说明](广东运行日报PDF解析/README.md) |

### 分析与报告

| 工具 | 用途 | 默认输出 | 文档 |
| --- | --- | --- | --- |
| 出力受限分析 | 批量读取“出清情况”Excel 并生成 HTML 报告 | 所选目录，默认 `~/Downloads` | [说明](电力工具脚本/电力交易分析工具/README.md) |
| 500kV断面分析 | 清洗节点电价，识别价格分化断面并生成拓扑网页 | 所选目录（默认 `~/Downloads`）下的 `<原文件名>_500kV分析.html` | [说明](500kV断面分析/README.md) |
| 南方分公司电力市场日报 | 读取 D、D-1、D-2 收益表生成手机预览版与打印版 PDF | `南方分公司电力市场日报/输出/<日期>/` | [说明](南方分公司电力市场日报/README.md) |
| 早报生成 | 抓取网站数据并生成生产经营情况 Word 报告 | `每日生产经营情况汇报自动生成工具/输出/` | [说明](每日生产经营情况汇报自动生成工具/README.md) |

### 上传与写入

| 工具 | 用途 | 默认输出 | 文档 |
| --- | --- | --- | --- |
| 上传集团每日数据 | 上传能销和省内日报数据并新建集团日报 | 外部集团系统 | [说明](集团每日上传/README.md) |
| WPS写入工具 | 把本地或在线表格区域写入 KDocs/WPS | 用户配置的目标表格 | [说明](wps自动/README.md) |

输出、日志、缓存、浏览器 profile 和真实配置不写入版本控制。

## 后台任务与取消

耗时任务在后台运行，切换功能页不会中断任务。右侧任务中心显示任务状态和日志。

- 数据读取、预览和报告生成等任务在安全时可以取消。
- 上传、覆盖 Excel 和外部写入不会自动重试，部分任务也不可取消。
- 关闭工具箱时，如果仍有任务运行，程序会列出任务并要求确认。
- 工具箱只终止由本次工具箱启动的线程或子进程，不会关闭其他独立运行的程序。

同一个 Excel 正在被 Excel/WPS 打开时，写入工具可能报权限错误。先关闭工作簿，再重新运行。

## 迁移到另一台 Mac

推荐使用 Git 克隆最新版本。如果需要离线迁移，也可以复制整个项目文件夹，但不要依赖旧电脑的 `.venv`：

在当前 Mac 上生成不含登录态、缓存和真实配置的迁移压缩包：

```bash
./package_macos.command
```

压缩包会生成到 `dist/`，并包含当前工作区里尚未提交的源码修改和新文件。

1. 把项目复制到新 Mac。
2. 可以删除复制来的 `.venv`，由新电脑重新创建。
3. 在项目根目录执行隔离属性和执行权限命令。
4. 运行 `./setup_macos.command`。
5. 按“首次登录和本地配置”重新准备配置与登录态。
6. 运行启动 smoke test，再启动图形界面。

本地账号配置可以通过安全方式单独迁移，但浏览器 profile 和登录态可能与旧电脑环境绑定；遇到失效时应在新电脑重新登录。

## 代理和网络

脚本会读取常见代理环境变量。例如本机代理端口为 `7897` 时：

```bash
export HTTPS_PROXY=http://127.0.0.1:7897
export HTTP_PROXY=http://127.0.0.1:7897
```

如果代理端口不同，请按实际设置修改。遇到登录页面打不开、Playwright 超时或上传请求失败时：

1. 先用 Chrome 手动确认目标系统可以访问。
2. 检查 VPN、代理软件和代理端口是否正常。
3. 确认右侧保存的账号密码正确后，重新运行对应任务。
4. 查看页面中最早出现的错误，不要只根据最后一行判断原因。

## 常见问题

### 双击 `.command` 没有反应

在项目根目录执行：

```bash
xattr -dr com.apple.quarantine .
chmod +x setup_macos.command "00_启动/电力工具箱.command" \
  "wps自动/启动WPS专用Chrome.command"
./00_启动/电力工具箱.command
```

终端会保留具体错误信息，便于定位安装或路径问题。

### 提示缺少 Python 模块

重新安装根目录依赖：

```bash
./setup_macos.command
```

确认测试或脚本使用 `.venv/bin/python`，不要混用另一个 Python 环境。

### Playwright 找不到浏览器

```bash
.venv/bin/python -m playwright install chromium
```

### 登录状态失效或页面跳回登录页

确认右侧“清能网站账号”的账号密码正确后，重新运行对应任务。各工具独立检测和刷新自己的登录态。

### Excel 提示权限错误或保存失败

关闭 Excel/WPS 中正在打开的目标文件，确认文件不是只读且目录可写，然后重新运行。市场表更新会直接修改所选文件，建议事先备份。

### 私有数据上传提示找不到 Node.js

安装 Node.js 18 或更新版本，然后执行：

```bash
cd "电力工具脚本/private-data-uploader-tool"
npm install
```

### WPS 提示无法连接 Chrome 或调试端口

先关闭使用 `wps-browser-profile` 的旧专用 Chrome 窗口，再双击：

```text
wps自动/启动WPS专用Chrome.command
```

回到 WPS 工具页点击“Check status”。专用 Chrome 需要保持运行，并使用独立 profile；不要让另一个 Chrome 进程占用 `127.0.0.1:9222`。

### 测试提示 `No module named 'toolbox'`

直接调用 `unittest` 时需要从 `电力工具箱` 目录运行，并把仓库根目录放进 `PYTHONPATH`：

```bash
cd "电力工具箱"
PYTHONPATH=.. ../.venv/bin/python -m unittest tests.test_catalog_loading
```

用 `tooling/run_tests.py` 跑完整套件时不需要手工设置，它按模块自行准备环境。

## 测试和自检

启动器定位和依赖 smoke test：

```bash
TOOLBOX_SMOKE=1 ./00_启动/电力工具箱.command
```

完整初始化检查：

```bash
./setup_macos.command
```

运行完整分模块测试（与 CI 同一入口，逐模块独立进程与临时数据目录）：

```bash
.venv/bin/python tooling/run_tests.py --report logs/tests.json
```

针对性运行某一套件或某些文件，例如工具箱的市场表测试：

```bash
.venv/bin/python tooling/run_tests.py --suite toolbox --pattern 'test_market_table*.py'
```

只跑某个模块时也可以直接调用 `unittest`，但缺少数套件的隔离、超时与进程组回收：

```bash
cd "电力工具箱"
PYTHONPATH=.. ../.venv/bin/python -m unittest tests.test_toolbox_pages
```

运行 500kV 断面分析测试：

```bash
cd "500kV断面分析"
../.venv/bin/python -m unittest discover -s tests -v
```

启动性能对比：

```bash
cd "电力工具箱"
python scripts/benchmark_toolbox_startup.py
python scripts/benchmark_toolbox_startup.py --eager
```

提交前检查本地配置和登录态未进入 Git：

```bash
git status --short --ignored
git diff --cached --check
```

`.gitignore` 已覆盖常见真实配置、登录态、浏览器 profile、输出文件、缓存、日志、虚拟环境和 CodeGraph 索引。
