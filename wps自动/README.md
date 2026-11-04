# WPS 写入工具

把本地 Excel 或 KDocs 的来源区域，写入一个或多个目标 KDocs/WPS 表格区域。工具箱入口在“上传与写入 → WPS写入工具”。

## 使用

1. 新建或选择一个文档配置。
2. 填写目标 KDocs URL。
3. 选择来源类型：本地 Excel 或 KDocs。本地 Excel 需要选择文件，KDocs 需要填写来源 URL。
4. 为每个区域填写来源工作表、来源起止单元格、目标工作表和目标起止单元格。
5. 确认每个区域的来源与目标行列数一致。
6. 保持默认的“Dedicated WPS Chrome (recommended)”，点击“Start/open dedicated Chrome”。
7. 在专用 Chrome 中完成 WPS/KDocs 登录，回到页面点“Check status”确认连接可用。
8. 选择一个或多个文档配置点“Preview”，核对来源、目标和区域尺寸。
9. 点“Write to WPS”，在页面专用日志确认写入与保存结果。
10. 需要复用配置时使用 Import/Export。

写入前会把所有目标工作表和单元格区域一次性定位校验；任意一个区域不存在或无法定位时立即报错，不写入任何单元格，错误信息标明第几个区域及其目标位置。赋值完成后保存一次，再回读每个区域的值与来源核对。

写入前建议在目标表保留副本；不要同时用多个窗口写同一个目标区域。

## 专用 Chrome

macOS 默认通过本机 `127.0.0.1:9222` 连接独立 Chrome 会话，profile 与日常 Chrome 分开：

```text
wps自动/启动WPS专用Chrome.command
```

首次启动后在这个窗口登录 WPS/KDocs，保持窗口运行再回工具箱预览或写入。提示无法连接时，先关闭占用 `9222` 的旧专用窗口，再重新启动。

## 配置文件

| 文件 | 说明 |
| --- | --- |
| `wps自动/wps_excel_to_kdocs_config.json` | 本机配置：账号无关的文档配置、目标链接、区域和界面选项 |
| `wps自动/wps_excel_to_kdocs_config.defaults.json` | 随包默认配置，3 项写入配置、共 34 个写入区域 |
| `wps自动/logs/` | 写入日志 |

首次使用且没有本机配置时自动载入默认配置；已有本机配置（包括自行清空的）优先保留，不会被更新覆盖。桌面安装包首次安装把默认配置初始化到用户目录 `data/wps自动/wps_excel_to_kdocs_config.json`。

默认配置保留目标表格链接、来源与目标工作表、单元格范围和字段顺序；本地来源文件留空，需要在新电脑选择表格文件后再写入。调整随包业务配置时改 `.defaults.json`，日常界面修改继续留在本机配置里。

## 实现

- `wps自动/config_model.py`：配置字典与 `DocumentConfig`／`MappingRow` 互转。
- `toolbox_shared/wps_values.py`：数值转换与区域逻辑。
- `toolbox_shared/wps_browser.py`：浏览器与 CDP 生命周期（不依赖 Tk）。
- `toolbox_shared/wps_service.py`：Excel 读取、映射、写入与回读。写入前统一预检目标工作表／区域，赋值后一次保存并并行回读核对值。
- `toolbox_shared/wps_config.py`、`wps_urls.py`、`wps_paths.py`：默认配置合并、URL 解析、路径与启动锁。

独立入口为 `wps自动/wps_excel_to_kdocs_gui.py`（`wps写入工具.pyw` 为双击版本）。登录状态、锁和浏览器 profile 按工具隔离，不进入版本控制。
