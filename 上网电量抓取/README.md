# 导出上网电量

工具箱“数据采集 → 导出上网电量”。按日期抓取各公司、各机组的上网电量，写出一个 Excel 汇总表。

## 使用

1. 首次使用先在工具箱右侧“清能网站账号”填写并保存账号密码。
2. 在页面选择“导出日期”，默认昨天。
3. 点击“开始导出”，在页面日志确认抓取与保存结果；完成后可用“打开输出目录”。

输出目录默认 `上网电量抓取/输出/`，可用 `config.json` 的 `outputDir` 改变。

## 命令行

```bash
.venv/bin/python 上网电量抓取/export_online_energy.py 2026-11-02
.venv/bin/python 上网电量抓取/export_online_energy.py --login
```

`--login` 只打开登录窗口并保存登录态，不导出。

## 配置

业务选项放 `上网电量抓取/config.json`（从 `config.example.json` 复制），可用字段：

| 字段 | 作用 |
| --- | --- |
| `headless` | 命令行是否隐藏浏览器 |
| `requestDelaySeconds` | 接口请求间隔（秒） |
| `outputDir` | 汇总表输出目录，默认 `上网电量抓取/输出/` |

示例文件不含账号，账号统一写在工具箱右侧或 `.toolbox/qingneng/config.json`；只有命令行调试时才在本文件补 `username`、`password`。

## 数据校验

- 响应日期、机组身份、重复记录和整条记录缺失都会校验；异常时停止导出并保留已有文件。
- 未填报的时段导出为空白并提示缺失时段数，不按 0 参与计算，也不覆盖已有数据。
- 汇总表按机组分块，清远区域右侧另有 D+2 计划电量列；计划机组或计划日期与实际不符时停止导出。

## 登录态

本工具独立维护 `auth_state.json`、`auth_state.json.lock`、`.browser-profile/` 与 `.qingneng-session/`，不与其它工具共用，可并行运行。

## 测试

`电力工具箱/tests` 中的上网电量相关用例随 `tooling/run_tests.py --suite toolbox` 运行。
