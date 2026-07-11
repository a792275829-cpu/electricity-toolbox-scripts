# WPS 写入工具响应式按钮布局 Implementation Plan

**Goal:** 将 Document configs 的十个操作按钮调整为两行分组布局，避免小窗口裁切。

**Architecture:** 保留现有 `WpsWriterFrame` 和按钮回调，只把单个 `mapping_toolbar` 拆成上下两个 `ttk.Frame`，使用 grid 分配左右空间。

**Tech Stack:** Python 3.11、Tkinter/ttk、unittest。

### Task 1: 两行响应式工具栏

**Files:**
- Modify: `wps自动/wps_excel_to_kdocs_gui.py`
- Create: `电力工具箱/tests/test_wps_layout.py`

- [ ] 先创建宽度 520 像素的 Tk 回归测试，断言十个具名按钮均映射且未超出工具栏右边界。
- [ ] 运行测试，确认当前单行布局失败。
- [ ] 把按钮改为两行分组布局，并为上下行设置可扩展列。
- [ ] 运行回归测试、完整工具箱测试、语法编译和启动器冒烟。
