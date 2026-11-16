# 2026-11-17 macOS 辅助功能试验

状态：已完成首个页面的可行性验证，原生操作验收未通过，不作为正式版兼容承诺。结果绑定以下运行时和上游源码；升级后需重新测量。

## 范围与环境

- 真实 `OnlineEnergyPage`，外部执行边界由验证桩替换。实际日期校验、命令构建、控件和主题仍来自仓库。
- macOS Apple 芯片，控制端 `AXIsProcessTrusted()` 为 true，VoiceOver 关闭。
- 主环境 Python 3.11.15 / Tk 8.6.18；隔离试验 Python 3.14.6 / Tcl/Tk 9.1.0。
- 开始日期、结束日期、开始导出、打开输出目录、取消任务、任务状态，共 6 个业务控件显式注册原生 AX 信息。
- 没有执行真实登录、取数、Excel 写入、上传、打包或发布。

## 跨进程实测

`tooling/accessibility_probe.py --audit PID` 调用系统 AX API，结果为 1/6 项通过，退出码 2。

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| 6 个控件的角色与中文名称可读 | 通过 | 开始／结束日期为 AXTextField，按钮为 AXButton，状态为 AXStaticText |
| 控件标识稳定 | 未通过 | 对同一元素连续读取 AXIdentifier，返回不同随机数字 |
| 可用按钮启用状态正确 | 未通过 | 页面可用的“开始导出”暴露 AXEnabled=0 |
| 日期字段声明可写 | 未通过 | 两个字段的 AXValue 均声明不可设置 |
| 日期设置后双重回读一致 | 未通过 | AX 返回 success；AXValue 和页面 StringVar 均保持原日期 |
| 点击实际到达原页面 | 未通过 | AXPress 返回 success；等待后验证请求计数仍为 0 |

原始证据在 Git 忽略的 `logs/accessibility-ax-*.json`、`logs/accessibility-probe-ui.json` 和 `logs/accessibility-probe-summary.json`；运行步骤以 `tooling/README.md` 为准。审计分别检查传输返回码、AX 回读和页面实际状态，不用返回码代替操作结果。

## 源码与原因边界

[Tk 9.1 官方接口](https://www.tcl-lang.org/man/tcl9.1/TkCmd/accessible.html)提供名称、角色、值、状态与动作设置，但[固定版本的 macOS 实现](https://github.com/tcltk/tk/blob/core-9-1-0/macosx/tkMacOSXAccessibility.c)仍有明显限制：

- `accessibilityIdentifier` 每次读取都生成随机数。
- 未找到 `setAccessibilityValue:` 实现；日期写入失败与此一致。
- 脚本层按 VoiceOver 是否运行决定初始化。试验直接调用已有 C 层命令，为六个真实控件显式注册，因此 VoiceOver 关闭时仍可读取；没有伪造 VoiceOver 状态，也未修改上游源码或系统权限。
- 启用状态、按钮分发的失败由跨进程实测确认；没有仅凭源码推断已定位其全部根因。

## 验证与决策

- 主环境：81 项界面／日期／任务／运行时用例，75 通过、6 项仅适用于 Tk 9.1 的原生测试跳过；另 15 项桌面生命周期测试全部通过。
- 隔离 Tk 9.1：7 项辅助功能契约用例，6 通过、1 项仅适用于 Tk 8.6 的回退测试跳过。覆盖六控件描述、日期与状态同步、页面重新显示、原日期校验和销毁后 trace 清理。
- 初次扩大隔离测试范围时，部分业务用例因未安装 openpyxl 等依赖阻塞；没有完成 Tk 9.1 的完整业务兼容测试。
- 原普通源码启动入口 smoke test 通过。正式版运行时未切换。

不建议仅升级到未修改的 Tk 9.1 后宣布支持 AI computer-use。若继续要求通过本次六项验收，应评估具有完整 AX 支持的 AppKit／SwiftUI 或语义化 WebView 界面，并继续复用当前 Python 业务服务；若保留 Tk，则必须维护并验证其原生 AX 后端修复，不能只补 Python 标签。
