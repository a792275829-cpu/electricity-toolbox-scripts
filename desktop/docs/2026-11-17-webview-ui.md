# WebView 完整界面源码版验证

状态：开发入口可用，尚未替换正式版 Tk；截至 2026-11-17。后续界面或业务变更后需要重新执行验收。

## 实现范围

React 按原 Tk 的三栏结构、灰白主题、字号、间距、中文字段与说明实现 16 个业务页面，包含日期双月弹层、任务中心、账号保存与验证、诊断、日志展开／检索／保存、市场数据输入、WPS 配置和区域编辑器。任务执行、登录、Excel、WPS、上传、接线图浏览器与更新均复用已有 Python 服务或共享模块。正式版入口不变。

真实业务入口为 `00_启动/Tauri开发版.command --webview-ui`；离线入口为 `--webview-ui-sample`。两个入口各自使用开发数据目录，不复用正式账号或浏览器 profile。具体路径和命令见 [桌面版说明](../README.md#webview--react-完整界面源码版)。

## 验证结果

| 验证 | 结果 |
| --- | --- |
| 原 Tk 窗口捕获 | 16 页，0 个回调错误；截图与控件树在 `logs/ui-reference/` |
| Python 针对性回归 | 61 项通过：界面适配、账号验证生命周期、原业务服务、离线工作进程、原生 GUI、界面分层、更新界面 |
| Rust 测试 | 3 项通过，1 项显式忽略（需要指定旧版发行产物的生产增量更新检查） |
| TypeScript 与生产前端构建 | 通过 |
| 原生 AX 页面导航 | 16 页；读取实际 WKWebView 辅助功能树 |
| 原生 AX 交互 | 6 类通过：全部页面、日期输入与导出、取消、辅助面板、WPS 区域六字段、双月日历选择 |
| Excel 回读 | 两份样例工作簿“汇总”表 A3 为 12.5、13.5；数字格式均为 General |
| 安全重启 | 原生 AX 触发，旧界面与后台退出，开发服务器、完整界面与新后台恢复 |
| 启动入口 | 原版启动器与 WebView 模式 smoke 通过 |

原生 AX 验收由 `tooling/webview_ui_audit.py` 执行；完整结果为 `logs/ui-replica/summary.json`。它只操作离线样例，使用编译后的 Swift AX 客户端，不通过 DOM 注入完成验收。WebKit 偶尔先确认 AXPress 再派发点击，页面导航允许一次有界重试；业务提交不自动重放。

Python 回归命令：

```sh
.venv/bin/python -m unittest desktop.tests.python.test_web_ui desktop.tests.python.test_service desktop.tests.python.test_webview_pilot desktop.tests.python.test_native_gui desktop.tests.python.test_ui_layers desktop.tests.python.test_update_ui
```

## 验证边界

已实现完整页面与本地交互，尚未完成逐像素验收；WebView 与 Tk 的字体抗锯齿、系统控件和弹窗呈现仍有差异。原版页面截图、控件树及新界面 AX 树可供后续逐项校准。

首轮界面验收没有执行真实网站登录、业务上传、在线 WPS 写入、真实工作簿覆盖或发行版安装。后续真实抓取、解析、分析、副本写入与 WPS 在线读取已另行验证，见[真实业务验证](2026-11-17-webview-business.md)。离线模式只运行上网电量样例任务；其他页面的业务动作由模式边界拒绝。真实模式接入原业务服务，仍需相应业务账号、文件和真实流程验收。源码开发入口不用于执行发行包安装。
