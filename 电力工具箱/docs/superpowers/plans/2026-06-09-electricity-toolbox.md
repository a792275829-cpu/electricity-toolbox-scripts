# 电力脚本统一工具箱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建一个包含六个现有电力工具功能页的单窗口桌面工具箱，并保证各页任务、日志和运行状态互相隔离。

**Architecture:** 新增独立的 `toolbox` Python 包，由主窗口负责导航、页面生命周期和任务注册。六个页面通过现有脚本公开函数或独立子进程复用业务逻辑；耗时任务统一通过线程或子进程运行，并把输出安全地送回 Tkinter 主线程。

**Tech Stack:** Python 3.11、Tkinter/ttk、threading、subprocess、pathlib、现有 Playwright/Node/openpyxl/python-docx 依赖。

---

## 文件结构

- Create: `toolbox/__init__.py`，包标记。
- Create: `toolbox/runtime.py`，路径解析、模块加载、后台线程和子进程管理。
- Create: `toolbox/widgets.py`，日志框、页面标题、忙碌状态等共享控件。
- Create: `toolbox/pages.py`，六个功能页面及其对现有脚本的适配。
- Create: `toolbox/app.py`，主窗口、左侧导航、页面切换、关闭处理。
- Create: `toolbox_launcher.pyw`，稳定的图形界面入口。
- Create: `00_启动/电力工具箱.bat`，双击启动器。
- Create: `tests/test_toolbox.py`，路径、模块加载、页面创建、切换和子进程参数测试。
- Modify: `README_整理说明.txt`，记录新入口和保留旧入口。

### Task 1: 运行时基础设施

**Files:**
- Create: `toolbox/__init__.py`
- Create: `toolbox/runtime.py`
- Test: `tests/test_toolbox.py`

- [ ] **Step 1: 编写路径和模块加载失败测试**

```python
from pathlib import Path

from toolbox.runtime import ToolPaths, load_module


def test_tool_paths_resolve_from_workspace(tmp_path: Path):
    paths = ToolPaths(tmp_path)
    assert paths.workspace == tmp_path.resolve()
    assert paths.online_energy == tmp_path / "上网电量抓取" / "export_online_energy.py"


def test_load_module_rejects_missing_file(tmp_path: Path):
    try:
        load_module("missing_tool", tmp_path / "missing.py")
    except FileNotFoundError as exc:
        assert "missing.py" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe -m unittest discover -s tests -v
```

Expected: FAIL，提示 `toolbox.runtime` 不存在。

- [ ] **Step 3: 实现路径、动态模块加载和 UTF-8 环境**

`ToolPaths` 必须从工作区根目录生成六个脚本路径和输出目录。`load_module(name, path)` 使用 `importlib.util.spec_from_file_location` 加载模块，缺失文件时抛出 `FileNotFoundError`。`utf8_environment()` 返回包含以下值的环境副本：

```python
env["PYTHONUTF8"] = "1"
env["PYTHONIOENCODING"] = "utf-8"
```

同时实现 `python_executable()`，优先返回固定 Python 3.11 路径，找不到时返回 `sys.executable`。

- [ ] **Step 4: 实现任务注册和子进程终止**

新增 `TaskRegistry`：

```python
class TaskRegistry:
    def register_process(self, process: subprocess.Popen[str]) -> None: ...
    def unregister_process(self, process: subprocess.Popen[str]) -> None: ...
    def has_running_tasks(self) -> bool: ...
    def terminate_all(self) -> None: ...
```

`terminate_all()` 只终止工具箱自身注册的仍在运行子进程，不扫描或结束系统中的其他 Python、Node、Edge 进程。

- [ ] **Step 5: 运行基础测试**

Run:

```powershell
C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe -m unittest discover -s tests -v
```

Expected: PASS。

### Task 2: 共享控件和主窗口导航

**Files:**
- Create: `toolbox/widgets.py`
- Create: `toolbox/app.py`
- Modify: `tests/test_toolbox.py`

- [ ] **Step 1: 编写主窗口页面切换测试**

测试在 Tk 可用时创建 `ToolboxApp`, 验证：

```python
assert list(app.pages) == [
    "导出上网电量",
    "电力交易分析",
    "电量汇总",
    "生成报告",
    "私有数据上传",
    "上传集团每日数据",
]
app.show_page("电量汇总")
assert app.current_page == "电量汇总"
```

测试结束必须调用 `app.destroy()`。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe -m unittest tests.test_toolbox.ToolboxAppTests -v
```

Expected: FAIL，提示 `ToolboxApp` 不存在。

- [ ] **Step 3: 实现共享页面基类**

`widgets.py` 提供：

```python
class ToolPage(ttk.Frame):
    def set_busy(self, busy: bool, status: str = "") -> None: ...
    def append_log(self, text: str) -> None: ...
    def clear_log(self) -> None: ...
    def run_in_thread(self, worker, on_success=None) -> None: ...
```

所有 Tk 控件更新通过 `after()` 回到主线程。每页保留自己的日志和忙碌状态。

- [ ] **Step 4: 实现主窗口**

`ToolboxApp` 使用约 220 像素宽左侧导航和右侧页面容器，窗口默认 `1100x720`。页面对象启动时一次创建，`show_page()` 仅调用 `tkraise()`，不销毁页面。底部状态栏显示当前页面和运行任务状态。

- [ ] **Step 5: 运行导航测试**

Run:

```powershell
C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe -m unittest tests.test_toolbox.ToolboxAppTests -v
```

Expected: PASS。

### Task 3: 接入上网电量、电力交易分析和电量汇总

**Files:**
- Create: `toolbox/pages.py`
- Modify: `tests/test_toolbox.py`

- [ ] **Step 1: 编写三个页面的参数构造测试**

验证：

```python
assert online_page.build_export_command("2026-06-09")[-1] == "2026-06-09"
assert trade_page.output_dir.get()
assert summary_page.default_output_for(Path("C:/data")).suffix == ".xlsx"
```

不得在测试中访问网络或真实生成报告。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe -m unittest tests.test_toolbox.CorePageTests -v
```

Expected: FAIL，提示页面类不存在。

- [ ] **Step 3: 实现导出上网电量页面**

页面包含日期、导出、登录/刷新登录、打开输出目录和日志。日期使用现有模块的 `validate_date()` 校验。导出和登录均作为独立 Python 子进程运行，工作目录为 `上网电量抓取`，输出实时追加到本页日志。

- [ ] **Step 4: 实现电力交易分析页面**

页面保留文件列表、添加/移除/清空、输出目录、开始分析和打开目录。后台线程调用现有模块的 `generate_report()`，每个文件单独捕获异常并记录成功或失败。

- [ ] **Step 5: 实现电量汇总页面**

页面保留输入文件夹、输出文件、开始汇总和打开输出目录。后台线程调用现有模块的 `run_summary()`，并把 logger 回调映射到本页日志。不得修改 `NumericCell`、`number_format` 或汇总业务函数。

- [ ] **Step 6: 运行页面测试和语法检查**

Run:

```powershell
C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe -m unittest tests.test_toolbox.CorePageTests -v
C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe -m py_compile toolbox\runtime.py toolbox\widgets.py toolbox\pages.py toolbox\app.py
```

Expected: 全部 PASS，`py_compile` 无输出且退出码为 0。

### Task 4: 接入生成报告页面

**Files:**
- Modify: `toolbox/pages.py`
- Modify: `tests/test_toolbox.py`

- [ ] **Step 1: 编写日期映射和命令构造测试**

验证报告日期 `2026-06-09` 对应：

```python
assert page.source_dates("2026-06-09") == (
    date(2026, 6, 8),
    date(2026, 6, 9),
    date(2026, 6, 7),
)
```

并验证启用生成 Word 时，命令包含三个明确的 `--online-workbook`、`--day-ahead-workbook`、`--daily-clearing-workbook` 参数。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe -m unittest tests.test_toolbox.ReportPageTests -v
```

Expected: FAIL。

- [ ] **Step 3: 实现报告页面**

页面包含报告日期、自动匹配、三个 Excel 文件、可选 Word 模板、抓取网站数据、生成 Word、开始生成、打开输出目录和日志。复用现有 `find_workbook_for_day` 规则；执行时按顺序启动：

```text
fetch_daily_data.py REPORT_DATE
generate_red_marked_report.py REPORT_DATE --online-workbook ... --day-ahead-workbook ... --daily-clearing-workbook ...
```

每个命令完成后再运行下一个命令，任一命令失败则停止本页任务并显示错误。

- [ ] **Step 4: 运行报告页面测试**

Run:

```powershell
C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe -m unittest tests.test_toolbox.ReportPageTests -v
```

Expected: PASS。

### Task 5: 接入两个上传页面

**Files:**
- Modify: `toolbox/pages.py`
- Modify: `tests/test_toolbox.py`

- [ ] **Step 1: 编写私有数据上传命令测试**

验证预览和正式上传命令：

```python
assert private_page.build_command("--plan", Path("C:/review"))[-3:] == [
    "--plan", "--source", "C:\\review"
]
assert "--execute" in private_page.build_command("--execute", Path("C:/review"))
```

- [ ] **Step 2: 编写集团每日上传文件校验测试**

通过动态加载的现有模块调用 `prepare_upload_files()`，验证测试文件名能识别“能销”和“省内”，不同日期应失败。测试只创建空临时文件，不发起上传。

- [ ] **Step 3: 实现私有数据上传页面**

页面包含文件夹、预览、开始上传和日志。启动命令：

```text
node scripts/upload-private-data.mjs --plan --source PATH
node scripts/upload-private-data.mjs --execute --source PATH
```

正式上传前使用确认对话框。子进程工作目录为 `电力工具脚本/private-data-uploader-tool`。

- [ ] **Step 4: 实现集团每日数据上传页面**

页面包含文件选择、识别结果、覆盖选项、开始上传、登录/刷新和日志。文件选择后调用现有模块 `prepare_upload_files()` 校验。上传和登录使用独立 Python 子进程：

```text
python upload_daily_report.py FILE... [--force]
python upload_daily_report.py --login
```

正式上传前使用确认对话框。

- [ ] **Step 5: 运行上传页面测试**

Run:

```powershell
C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe -m unittest tests.test_toolbox.UploadPageTests -v
```

Expected: PASS，且测试日志中不出现真实上传请求。

### Task 6: 启动器、说明和关闭保护

**Files:**
- Create: `toolbox_launcher.pyw`
- Create: `00_启动/电力工具箱.bat`
- Modify: `README_整理说明.txt`
- Modify: `tests/test_toolbox.py`

- [ ] **Step 1: 编写入口导入测试**

测试 `toolbox_launcher.pyw` 可通过 `runpy.run_path(..., run_name="toolbox_smoke")` 加载且不会自动进入 `mainloop()`。

- [ ] **Step 2: 实现 Python 图形入口**

入口只负责：

```python
from toolbox.app import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: 实现批处理启动器**

启动器根据 `%~dp0` 定位工作区根目录，优先：

```text
C:\Users\lllg\AppData\Local\Programs\Python\Python311\pythonw.exe
```

找不到时尝试 `pyw -3.11`。启动时不保留控制台窗口，失败时显示明确提示。

- [ ] **Step 4: 实现关闭保护**

主窗口关闭时：

```python
if registry.has_running_tasks():
    confirmed = messagebox.askyesno("仍有任务运行", "关闭工具箱将终止由工具箱启动的任务，确定关闭吗？")
```

未确认则取消关闭；确认后调用 `registry.terminate_all()` 再销毁窗口。

- [ ] **Step 5: 更新整理说明**

在 `README_整理说明.txt` 中写明统一入口 `00_启动\电力工具箱.bat`，并说明原六个启动器继续保留用于单独启动和故障回退。

- [ ] **Step 6: 运行入口测试**

Run:

```powershell
C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe -m unittest tests.test_toolbox.LauncherTests -v
```

Expected: PASS。

### Task 7: 全量验证

**Files:**
- Modify only if verification exposes defects.

- [ ] **Step 1: 运行完整自动化测试**

Run:

```powershell
C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe -m unittest discover -s tests -v
```

Expected: 所有测试 PASS。

- [ ] **Step 2: 编译所有 Python 入口**

Run:

```powershell
C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe -m py_compile toolbox_launcher.pyw toolbox\*.py 上网电量抓取\export_online_energy.py 电力工具脚本\电力交易分析工具\generate_electricity_report.py 电力工具脚本\电量汇总工具\summarize_511_excel.py 每日生产经营情况汇报自动生成工具\scripts\report_gui.py 集团每日上传\upload_daily_report.py
```

Expected: 退出码 0。

- [ ] **Step 3: 无上传风险的主窗口冒烟测试**

以测试模式创建窗口，依次调用六个导航按钮，验证：

- 当前页面标题正确。
- 每页控件可见。
- 页面切换后原页面变量值保持。
- 不启动网络请求或上传。

- [ ] **Step 4: 子进程隔离测试**

使用短时 Python 测试子进程模拟后台任务，在其运行时切换其他页面，验证主窗口仍能处理 `update()` 且其他页面按钮可操作。结束后确认注册表为空。

- [ ] **Step 5: 启动器路径测试**

从工作区外的临时当前目录调用 `00_启动\电力工具箱.bat` 的路径解析测试模式，确认其仍能定位 `toolbox_launcher.pyw`。

- [ ] **Step 6: 原入口回归检查**

检查原六个 `.bat` 指向的目标脚本存在，并对其 Python 目标执行 `py_compile`。不触发真实上传、网站抓取或生产报告。

- [ ] **Step 7: 人工界面检查**

实际打开工具箱，检查中文显示、窗口缩放、六页切换、日志区域和按钮状态。确认关闭测试窗口后没有残留由测试启动的 Python 或 Node 子进程。
