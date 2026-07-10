# 电力工作工具箱 macOS 版

这是电力工作相关脚本的 macOS 可用版本，统一入口是 Tkinter 桌面工具箱。工具箱内集成上网电量导出、电力交易分析、电量汇总、日报生成、私有数据上传、集团每日数据上传和 WPS/KDocs 写入工具。

## 环境要求

- macOS
- Python 3.11 或更新版本
- Google Chrome（用于 Playwright 登录和上传流程）
- Node.js（仅“私有数据上传”需要）

## 迁移到另一台 Mac

把整个项目文件夹复制到另一台 Mac 后，在项目根目录运行一次：

```bash
./setup_macos.command
```

这个脚本会在项目目录内创建 `.venv`、安装根目录 `requirements.txt` 中的 Python 依赖、安装 Playwright Chromium，并在本机有 Node.js/npm 时自动安装“私有数据上传”子工具的 Node 依赖。

如果是从网页下载的压缩包，macOS 可能会阻止首次运行。可以在项目根目录执行：

```bash
xattr -dr com.apple.quarantine .
chmod +x setup_macos.command "电力工具箱.command" "00_启动/电力工具箱.command"
```

也可以不手动运行 `setup_macos.command`：双击 `电力工具箱.command` 时，如果本地 `.venv` 或关键依赖缺失，启动器会自动执行同一套初始化流程。

手动排障时可按下面的等价命令执行：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium

cd "电力工具脚本/private-data-uploader-tool"
npm install
```

## 启动工具箱

在 Finder 里双击下面任意一个文件：

```text
电力工具箱.command
00_启动/电力工具箱.command
```

如果 macOS 提示文件没有执行权限，在仓库根目录运行：

```bash
chmod +x "电力工具箱.command" "00_启动/电力工具箱.command"
```

也可以用终端启动：

```bash
./电力工具箱.command
```

新版统一入口默认先打开“今日概览”，工具页在首次进入时加载并保留状态。左侧按数据采集、分析与报告、上传与写入分组；右侧任务中心可同时查看多个后台任务。旧的独立启动入口、配置文件、登录状态和输出格式保持不变。

## 后台任务与取消

耗时任务在后台运行，切换功能页不会中断任务。运行中的任务可以取消；关闭工具箱时会列出仍在运行的任务，确认后只终止由本工具箱启动的线程或子进程。

数据读取任务遇到明确的瞬时网络错误时可以有限重试。上传、覆盖和外部写入不会自动重试；每次重试前仍需确认日期、文件和目标。

“今日概览”提供环境诊断入口，用于检查 Python、八个工具脚本和可选 Node.js 依赖。启动性能可用以下命令复测：

```bash
cd "电力工具箱"
python scripts/benchmark_toolbox_startup.py
python scripts/benchmark_toolbox_startup.py --eager
```

启动脚本会优先使用仓库根目录下的 `.venv/bin/python`。如果 `.venv` 缺失或缺少关键依赖，会先运行 `setup_macos.command`；如果仍不可用，才回退到系统 `python3`。

## 功能说明

- `导出上网电量`：按日期抓取各公司、各机组上网电量，生成 Excel 汇总。
- `电力交易分析`：选择一个或多个出清 Excel，批量生成 HTML 分析报告。
- `电量汇总`：汇总日前和实时交易结果，输出 Excel。
- `生成报告`：抓取每日数据，并基于三个 Excel 数据源生成生产经营情况 Word 报告。
- `私有数据上传`：选择复盘目录，先预览匹配结果，再确认上传私有数据。
- `上传集团每日数据`：上传能销和省内日报数据，支持登录态刷新和覆盖上传。
- `WPS写入工具`：把本地 Excel 或 KDocs 表格区域写入目标 KDocs/WPS 表格，支持配置导入导出。

## 首次登录和本地配置

仓库只提交示例配置，真实账号、密码、浏览器登录态和 WPS 配置都保存在本地忽略文件中，不会提交到 GitHub。

上网电量和日报抓取：

```bash
cp "上网电量抓取/config.example.json" "上网电量抓取/config.json"
cp "每日生产经营情况汇报自动生成工具/scripts/config.example.json" "每日生产经营情况汇报自动生成工具/scripts/config.json"
```

然后编辑 `config.json`，填写 `username`、`password` 等字段。为支持多个工具并行运行，浏览器 profile 和登录态按工具分别保存：

```text
上网电量抓取/auth_state.json
市场表更新/auth_state.json
集团每日上传/auth_state.json
每日生产经营情况汇报自动生成工具/scripts/auth_state.json
```

集团每日上传默认复用 `上网电量抓取/config.json` 里的账号密码，但会保存自己的登录态；也可以单独创建：

```text
集团每日上传/config.json
```

私有数据上传：

```bash
cp "电力工具脚本/private-data-uploader-tool/upload.config.example.json" \
   "电力工具脚本/private-data-uploader-tool/upload.config.json"
```

macOS 上如需显式指定 Chrome，可把 `chromeExecutablePath` 改为：

```text
/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
```

WPS/KDocs 写入工具的本地配置保存在：

```text
wps自动/wps_excel_to_kdocs_config.json
wps自动/wps-browser-profile/
```

这些文件和目录包含本地使用状态，不要手动加入 Git。

## 代理和网络

脚本会读取常见代理环境变量：

```bash
export HTTPS_PROXY=http://127.0.0.1:7897
export HTTP_PROXY=http://127.0.0.1:7897
```

如果本机代理端口不同，按实际端口修改。遇到登录页面打不开、Playwright 超时或上传请求失败时，先确认 Chrome 能正常访问目标系统，再检查代理环境变量。

## 常用输出位置

- 上网电量导出：`上网电量抓取/输出/`
- 日报生成：`每日生产经营情况汇报自动生成工具/输出/`
- 集团每日上传：选择或自动匹配 `集团每日上传/` 下的 Excel 文件
- 电力交易分析：默认输出到用户选择的目录，通常是 `~/Downloads`
- 电量汇总：默认在复盘文件夹内生成 `*_汇总.xlsx`

## 测试和自检

启动脚本定位自检：

```bash
TOOLBOX_SMOKE=1 ./电力工具箱.command
```

初始化脚本自检：

```bash
./setup_macos.command
```

运行工具箱测试：

```bash
cd "电力工具箱"
python3 -m unittest discover -s tests
```

如果测试里出现 `ModuleNotFoundError: No module named 'toolbox'`，通常是因为从仓库根目录直接运行了测试；进入 `电力工具箱` 目录后再运行即可。

## GitHub 同步注意事项

提交前建议确认不会把本地账号或登录态加入 Git：

```bash
git status --short --ignored
git diff --cached --check
```

`.gitignore` 已忽略常见本地配置、浏览器 profile、输出文件、缓存目录和日志文件。
