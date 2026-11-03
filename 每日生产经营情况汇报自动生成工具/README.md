# 早报生成

工具箱“分析与报告 → 早报生成”。抓取每日网站数据并基于 3 个 Excel 数据源生成生产经营情况 Word 报告；也可以只用现有 Excel 生成报告。

## 数据日期口径

- 前一日 Excel：报告日 D-1 的上网电量。
- 报告日 Excel：报告日 D 的日前电量／价格。
- 前两日 Excel：报告日 D-2 的日清数据。

生成 Word 时三份 Excel 都是必填。

## 使用

1. 填写“报告日期”，默认今天。
2. 点击“按日期自动匹配 Excel”，从 `~/Downloads` 按文件名含“出清情况”匹配 D-1、D、D-2 三份；也可以手动逐一选择。
3. 需要指定格式时选择 Word 模板，留空使用脚本默认模板。
4. 勾选“抓取网站数据并汇总 Excel”和／或“生成 Word 报告”，至少选一项。
5. 点击“开始生成”。两项都勾选时先抓取，再生成 Word。

输出目录：`每日生产经营情况汇报自动生成工具/输出/`。

## 目录结构

```text
每日生产经营情况汇报自动生成工具/
├── scripts/
│   ├── report_gui.py                  # 独立 GUI 入口，工具箱页面复用同一套流程
│   ├── generate_red_marked_report.py  # Word 生成（含红头版本）
│   ├── fetch_daily_data.py            # D-2 日清数据
│   ├── fetch_load_compare.py          # 负荷对比
│   ├── fetch_price_board.py           # 日前价格看板
│   └── export_online_energy.py        # D-1 上网电量导出
├── 模板/每日生产经营情况汇报（自动）.docx
└── 输出/
```

## 配置

`每日生产经营情况汇报自动生成工具/scripts/config.json`（从 `config.example.json` 复制，示例不含账号）：

| 字段 | 作用 |
| --- | --- |
| `headless` | 抓取数据时是否隐藏浏览器 |
| `requestDelaySeconds` | 接口请求间隔（秒） |

账号读统一账号；只有命令行调试时才在本文件补 `username`、`password`。

## 登录态与测试

本模块独立维护 `auth_state.json`、锁和 `.browser-profile/`。测试随 `tooling/run_tests.py --suite morning` 运行。
