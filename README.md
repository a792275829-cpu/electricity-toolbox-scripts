# 电力工作工具箱 · macOS

面向电力业务的桌面工具箱，集成数据采集、Excel 更新、电价预测、分析报告与系统上传，共 **16 个工具**，通过统一图形界面使用。

**[下载最新版](https://github.com/a792275829-cpu/electricity-toolbox-scripts/releases/latest)** · [更新日志](CHANGELOG.md) · [问题反馈](https://github.com/a792275829-cpu/electricity-toolbox-scripts/issues)

[安装与启动](#安装与启动) · [工具一览](#工具一览) · [账号与数据](#账号与数据) · [常见问题](#常见问题) · [开发与验证](#开发与验证)

## 安装与启动

### DMG 桌面版（推荐）

适用于 **macOS 14+、Apple 芯片 Mac**，内置 Python、Node、Chromium 与 Tcl/Tk，无需单独安装运行环境。

1. [下载最新 DMG](https://github.com/a792275829-cpu/electricity-toolbox-scripts/releases/latest)，退出正在运行的旧工具箱。
2. 打开 DMG，双击“安装到我的应用程序.command”。
3. 安装器将应用装到 `~/Applications/电力工具箱.app` 并自动打开。

后续在应用内点击“开始更新”，自动下载、校验、安装并重启，保留配置、登录态与业务数据。首次打开被 macOS 拦截、旧版迁移或更新故障，见[桌面版说明](desktop/README.md#首次安装与旧版迁移)和[启动故障恢复](desktop/docs/startup-recovery.md)。

### 源码运行

需要 macOS 14+、Python 3.11，以及用于网页登录的 Google Chrome。Node.js 18+ 仅供“出清结果汇总上传”“私有数据批量上传”的上传功能使用；缺少 Node 时仍可汇总及使用其他 Python 工具。

```bash
git clone https://github.com/a792275829-cpu/electricity-toolbox-scripts.git
cd electricity-toolbox-scripts
./setup_macos.command
./00_启动/电力工具箱.command
```

也可下载 ZIP 解压后，在项目根目录执行最后两条命令。初始化脚本创建 `.venv`、安装依赖和 Chromium；检测到 Rust 时构建共享后端。部分功能需要 Rust 后端，准备方式见[维护指南](tooling/README.md#环境与测试)。

以后在 Finder 双击 `00_启动/电力工具箱.command` 即可启动。若下载的源码或 `.command` 被隔离，在项目根目录执行后重新初始化：

```bash
xattr -dr com.apple.quarantine .
chmod +x setup_macos.command "00_启动/电力工具箱.command"
```

## 工具一览

名称与侧栏一致。点击工具名查看操作步骤、日期口径、覆盖规则和输出位置；涉及原工作簿写入时，先备份并关闭 Excel/WPS 中打开的文件。

### 数据采集

| 工具与说明 | 用途 |
| --- | --- |
| [导出上网电量](上网电量抓取/README.md) | 按日期范围抓取各公司、各机组上网电量，逐日汇总 Excel |
| [风力预测截图](风力预测截图/README.md) | 抓取连续 7 个预测日的风力数据并生成表格截图 |
| [市场表更新](市场表更新/README.md) | 按基准日抓取市场数据，写入年度市场 Excel |
| [现货价格分析表更新](现货价格更新/README.md) | 抓取日前／实时出清电价，写入归湖、鮀莲、海风工作簿 |
| [交易系统信息更新](交易系统信息更新/README.md) | 接续更新市场负荷信息 |
| [功率调整收益统计表更新](功率调整收益统计表更新/README.md) | 按日期更新归湖、鮀莲、海风收益统计表 |
| [广东电价预测](广东电价预测/README.md) | 补齐现货数据总表并生成 D+1 至 D+15 日前电价预测 |
| [出清结果汇总上传](电力工具脚本/电量汇总工具/README.md) | 归类复盘目录，汇总出清结果并上传 |
| [广东运行日报解析上传](广东运行日报PDF解析/README.md) | 解析运行日报报价分布图并上传 PDF 与容量 Excel |

### 分析与报告

| 工具与说明 | 用途 |
| --- | --- |
| [出力受限分析](电力工具脚本/电力交易分析工具/README.md) | 批量读取“出清情况”Excel 并生成 HTML 报告 |
| [500kV断面分析](500kV断面分析/README.md) | 清洗节点电价，识别价格分化断面并生成拓扑网页 |
| [南方分公司电力市场日报](南方分公司电力市场日报/README.md) | 生成手机预览版与打印版 PDF |
| [早报生成](每日生产经营情况汇报自动生成工具/README.md) | 抓取网站数据并生成生产经营情况 Word 报告 |

### 上传与写入

| 工具与说明 | 用途 |
| --- | --- |
| [私有数据批量上传](电力工具脚本/private-data-uploader-tool/README.md) | 按单元批量上传私有数据，回读上传状态 |
| [上传集团每日数据](集团每日上传/README.md) | 上传能销和省内日报数据并新建集团日报 |
| [WPS写入工具](wps自动/README.md) | 把本地或在线表格区域写入 KDocs/WPS |

## 账号与数据

首次使用需在右侧“清能网站账号”填写账号密码，完整输入会自动保存。各工具共享账号密码，分别维护登录态、锁和浏览器目录，可并行运行；部分功能需要对应业务系统的访问权限。账号状态、人工验证与停止验证见[账号与登录](电力工具箱/README.md#账号与登录)。

- **桌面版数据**：保存在 `~/Library/Application Support/com.electricity.toolbox/`；配置、登录态和业务数据位于 `data/`，详见[目录约定](desktop/docs/directory-layout.md)。
- **源码版数据**：统一账号在 `.toolbox/qingneng/config.json`，业务配置与输出位置见各工具文档。
- **WPS/KDocs**：需单独登录专用 Chrome，并在新电脑重新选择本地来源文件，见 [WPS 配置说明](wps自动/README.md#配置文件)。
- **Git 忽略项**：真实配置、登录态、浏览器 profile、输出、日志和缓存均不纳入版本控制。

迁移到另一台 Mac，推荐重新安装 DMG 或克隆源码并初始化。源码工作区可运行 `./package_macos.command`，在 `dist/` 生成不含真实配置、登录态与缓存的迁移 ZIP；复制整个文件夹时，应在新电脑重建 `.venv`。账号配置可通过安全方式单独迁移，失效的登录态需重新登录。

## 常见问题

| 问题 | 处理方式 |
| --- | --- |
| 源码启动失败或缺少 Python 模块 | 在项目根目录重新运行 `./setup_macos.command`，使用根目录 `.venv`；从终端启动以保留首条错误 |
| Playwright 找不到浏览器 | 运行 `.venv/bin/python -m playwright install chromium` |
| 登录失效或网页超时 | 先用 Chrome 确认网站可访问，检查账号、VPN 和代理，再运行任务；各工具自动刷新自己的登录态 |
| Excel 权限错误或保存失败 | 关闭已打开的工作簿，确认文件及目录可写；具体覆盖与备份规则见对应工具说明 |
| 上传提示找不到 Node.js | 安装 Node.js 18+，在 `电力工具脚本/private-data-uploader-tool/` 运行 `npm install` |
| WPS 无法连接 Chrome | 按 [WPS 专用 Chrome 说明](wps自动/README.md#专用-chrome)重新启动，再刷新登录状态 |

公司网络需要代理时，脚本读取常见代理环境变量；以下端口仅为示例，请按实际修改：

```bash
export HTTPS_PROXY=http://127.0.0.1:7897
export HTTP_PROXY=http://127.0.0.1:7897
```

任务日志和右侧“运行诊断”可用于排障。[提交问题](https://github.com/a792275829-cpu/electricity-toolbox-scripts/issues)时附首条有效错误、退出码与复现步骤，隐去账号、Cookie 和业务敏感数据。

## 开发与验证

在仓库根目录执行启动自检：

```bash
TOOLBOX_SMOKE=1 ./00_启动/电力工具箱.command
```

完整测试与 CI 使用同一入口，先准备 Rust 后端：

```bash
.venv/bin/python native/price-xlsx/build.py
.venv/bin/python tooling/run_tests.py --native --report logs/tests.json
```

[维护与针对性测试](tooling/README.md) · [界面开发](电力工具箱/README.md) · [桌面版开发](desktop/README.md) · [打包与发布](packaging/tauri/README.md) · [贡献指南](CONTRIBUTING.md) · [安全政策](SECURITY.md)

## 许可证

代码采用 [MIT 许可证](LICENSE)。随附 Noto Serif SC 字体采用 [SIL Open Font License 1.1](风力预测截图/fonts/OFL.txt)，其他第三方依赖及资源遵循各自许可证。
