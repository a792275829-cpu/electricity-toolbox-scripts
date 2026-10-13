# Windows x64 便携版

目标：Windows 10 22H2 / Windows 11，Intel 或 AMD 64 位系统。

解压整个文件夹到本地可写目录，双击根目录的 `电力工具箱.exe`。
不需要安装 Python、Node.js 或 Chrome；不要单独移动 EXE。
风力预测截图自带 Noto Serif SC 中文字体，无需额外安装字体或中文语言包。
首次使用在工具箱右侧填写账号，WPS 需单独登录。联网业务需要网络。
包内不包含旧电脑的账号、Cookie、浏览器 profile、业务输出或真实配置。

双击 `环境自检.exe` 执行离线检查。窗口会保留结果，报告写入
`logs/windows-self-test.json`。检查包含依赖、全部功能页、浏览器、PDF
和使用临时文件的 Excel/并发回归、风力预测中文字体渲染，不会上传数据或写入真实业务工作簿。

浏览器与 Python/Node Playwright 固定为配套版本；不要单独升级其中一个。
兼容性版本为 Playwright 1.55.0，内置对应 Chromium。便携版优先使用
自带浏览器，避免受到电脑上 Chrome 升级的影响。

## 重新打包

在仓库根目录使用 `.venv` 执行：

```sh
.venv/bin/python packaging/windows/package_windows.py
```

构建机器需要网络、Git、pip、uv 和 npm；最终使用者无需这些环境。
打包器支持在 Mac 上组装 Windows 文件，但 Mac 上的静态检查不能替代
Windows 10/11 实机验收。Windows 原生运行结果以自检报告为准。
所有下载、运行时、构建缓存和 ZIP 均在 Git 忽略的 `dist/` 内。
Python 来自 Astral python-build-standalone，含 Tcl/Tk 和 VC 运行时；
Node 来自 nodejs.org。保留各依赖自带的许可证。
