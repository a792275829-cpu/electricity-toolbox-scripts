# 出力受限分析

工具箱“分析与报告 → 出力受限分析”。读取一个或多个“出清情况”Excel，批量生成 HTML 分析报告。

## 使用

1. 点击“添加文件”选择一个或多个 `.xlsx`；也可以点“自动匹配D和D-2”，在 `~/Downloads` 中按文件名含“出清情况”为 D 和 D-2 各选一份最后修改的文件。
2. 用“移除选中”整理列表。
3. 选择输出目录，默认 `~/Downloads`。
4. 点击“开始分析”，每个输入生成一个 `<原文件名>_分析报告.html`。

## 命令行

```bash
.venv/bin/python 电力工具脚本/电力交易分析工具/generate_electricity_report.py --output-dir ~/Downloads a.xlsx b.xlsx
```

`--output` 指定单个 HTML 路径，`--no-gui` 强制命令行模式。

## 实现位置

- 批量执行、文件名冲突检查、会话复用与失败策略：`toolbox_shared/trade_batch.py`。GUI 保留部分完成，服务和 CLI 遇错停止。
- 报表计算与 HTML 生成：`toolbox_shared/trade_report.py`。工具箱页面、服务和偏差分析都通过普通导入调用；Tk 组件只在打开 GUI 时加载。
- `find_output_range_deviation_days.py` 是配套的出力受限天数统计脚本，`--help` 查看参数。

## 测试

`tooling/run_tests.py --suite trade`。改动 HTML 展示时用固定日前／实时摘要核对重构前后输出是否一致。
