# 广东电价预测

工具箱“数据采集 → 广东电价预测”。两件事：补齐广东现货历史数据总表，然后用滚动训练生成 D+1 日前电价预测。

## 使用

补齐数据总表：

1. 确认工具箱右侧“清能网站账号”已配置。
2. 填写“运行日 D”。
3. 点击“补齐数据总表”。工具检查截至 D 日的标准 96 时点、日前电价和已发布的供需字段，补抓缺口，并按 `refreshDays` 回看近期日期。
4. 增量空值不覆盖已有有效值，数据始终写入唯一的 `广东电价数据总表.xlsx`。

运行预测：

1. 确认总表存在且历史足够。
2. 确认建模输入表与预测输出目录。
3. 设置“滚动训练月数”：默认 `0` 表示自动——预测日供需数据齐全时用 12 个月，需要回退时用 3 个月；手工指定时最少 2 个月，最大 24。
4. 点击“运行 D+1 预测”，固定输出 96 点日前电价；同时核验预测日供需曲线的发布时间。

## 命令行

```bash
.venv/bin/python 广东电价预测/update_gdfire_data.py --date 2026-11-02
.venv/bin/python 广东电价预测/train_d1_forecast.py --training-months 0 --as-of 2026-11-02
```

`train_d1_forecast.py --forecast-days` 只接受 `1`；`--compare-algorithms`、`--historical-only`、`--fetch-future-inputs` 是开发期实验开关，日常不用。

## 配置

`广东电价预测/config.json`（从 `config.example.json` 复制，示例不含账号）：

| 字段 | 作用 |
| --- | --- |
| `masterWorkbook` | 长期维护的唯一数据总表，同时是默认建模输入 |
| `predictionOutputDir` | D+1 预测输出目录 |
| `initialWorkbook` | 首次创建总表时导入的历史表；已有总表时不再使用 |
| `initialDate` | 首次自动生成总表时的起始日期 |
| `refreshDays` | 已有总表默认回看最近 N 天补入延后发布或修订的数据；`0` 关闭回看，历史缺口仍补抓 |

相对路径以 `广东电价预测/` 为基准，支持 `~`。页面修改总表路径后，补齐与预测都使用该路径。

## 输出

- `广东电价预测/输出/广东电价数据总表.xlsx`
- `广东电价预测/输出/D+1预测/D+1日前电价.xlsx`：D+1 日期 + 96 个十五分钟时点
- `广东电价预测/输出/D+1预测/MAE.json`：模型误差指标

建模自动排除实际、实时等会造成未来信息泄漏的字段。初始历史表放在 `广东电价预测/输入/广东数据初始总表.xlsx`，或用 `initialWorkbook` 指定。

## 登录态与测试

本模块独立维护 `auth_state.json`、锁和 `.browser-profile/`。测试随 `tooling/run_tests.py --suite forecast` 运行。
