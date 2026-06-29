# 电力工作工具箱 macOS 版

这是电力工作相关脚本的 macOS 可用版本，统一入口是 Tkinter 桌面工具箱。工具箱内集成上网电量导出、电力交易分析、电量汇总、日报生成、私有数据上传、集团每日数据上传和 WPS/KDocs 写入工具。

## 环境要求

- macOS
- Python 3.11 或更新版本
- Google Chrome（用于 Playwright 登录和上传流程）
- Node.js（仅“私有数据上传”需要）

首次使用建议在仓库根目录执行：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install openpyxl playwright
python -m playwright install chromium

cd "电力工具脚本/private-data-uploader-tool"
npm install
```

如果已经有可用的 `.venv`，可以跳过创建虚拟环境。

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

启动脚本会优先使用仓库根目录下的 `.venv/bin/python`，否则使用系统 `python3`。

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

然后编辑 `config.json`，填写 `username`、`password` 等字段。登录态会保存到：

```text
上网电量抓取/auth_state.json
```

集团每日上传默认复用 `上网电量抓取/config.json` 和 `上网电量抓取/auth_state.json`，也可以单独创建：

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
