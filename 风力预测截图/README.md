# 风力预测截图

工具箱“数据采集 → 风力预测截图”。按基准日期抓取连续 7 个预测日的风力数据，渲染成日期横向、00:00—23:00 纵向的表格截图。

## 使用

1. 首次使用先在工具箱右侧“清能网站账号”填写并保存账号密码。
2. 选择“运行日期”，默认明天；抓取从该日起连续 7 个预测日。
3. 点击“生成截图”，在页面日志确认结果；当天没有可抓数据时会弹窗说明原因。
4. 点击“打开输出目录”查看图片。

## 命令行

```bash
.venv/bin/python 风力预测截图/wind_forecast_screenshot.py 2026-11-04
.venv/bin/python 风力预测截图/wind_forecast_screenshot.py --login
```

## 配置

`风力预测截图/config.json`（从 `config.example.json` 复制）：

| 字段 | 作用 |
| --- | --- |
| `headless` | 是否隐藏浏览器 |
| `outputDir` | 输出目录；留空为 `~/Downloads/风力预测截图/`，相对路径以本模块目录为基准 |
| `orgId`、`unitId` | 目标公司、机组 ID |
| `orgName`、`unitName` | 目标公司、机组名称，用于日志和文件名 |

`orgId`、`unitId` 等默认值对应广澳海风场；换机组时三个 ID、名称字段要一起改。

## 输出

`<outputDir>/风力预测_<起始日>_至_<结束日>.png`，日期格式 `YYYYMMDD`，共 7 个预测日。

## 登录态与测试

本模块独立维护 `auth_state.json`、锁文件和 `.browser-profile/`，与其它工具互不影响。测试随 `tooling/run_tests.py --suite wind` 运行。
