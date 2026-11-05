# 私有数据上传工具

从 macOS 工具箱的“出清结果汇总上传”页面选择复盘目录，预览后执行上传；或从“私有数据批量上传”页面按单元直连网站批量上传接口。

实际上传逻辑为 `scripts/upload-private-data.mjs`，纯逻辑（单元清单、分片、multipart 组装、响应归纳）在 `scripts/batch-plan.mjs`。Node 依赖仅作用于本子工具。首次依赖安装由根目录 `setup_macos.command` 完成；也可以在本目录运行 `npm install`。

需要命令行预览时，在本目录执行：

```sh
node scripts/upload-private-data.mjs --plan --source "$HOME/Desktop/复盘/5-12"
```

确认预览后可使用 `--execute` 执行真实上传；`--login` 用于重新登录。

## 批量上传模式

```sh
node scripts/upload-private-data.mjs --batch --unit 汕头 \
  --files "/path/日前.xlsx,/path/实时.xlsx"
```

- `--unit`：海风、鮀莲、归湖、东莞、汕头、海门（也接受 金平、潮州、潮安、谢岗 等别名）。
- `--files`：逗号分隔，可重复传多次；脚本按网站限制拆批（最多 12 个文件、合计 10MB）。
- `--summary-type formal|temporary`：上传月度结算单时使用，对应网站的“正式账单／临时账单”。
- `--power-source 北京东润环能|国能日新|南瑞|北京华软恒信|其他`：新能源单元上传原始功率预测数据时的“数据来源”。不传则只固定申报功率预测数据为“调度OPS平台”。
- `--no-verify`：跳过上传后的数据状态回读。
- `--login --batch`：只登录并保存批量上传自己的登录态。

上传直接 `POST /gdfire/api/data/personal/import`（multipart，`files` 字段重复出现，另带 `personalDataTypeSourceMap`），不经过“点按钮选文件”的界面操作。接口返回逐文件结果，`detail` 非空即该文件失败，脚本按文件汇报并在末尾汇总失败项。

`personalDataTypeSourceMap` 是“数据类型 → 来源编码”的 JSON：新能源单元固定带 `DECLARE_POWER_PREDICT: DISPATCH_OPS_PLATFORM`，原始功率预测数据按 `--power-source` 填 `RAW_POWER_PREDICT`（东润 `BEIJING_DR_HN`、国能日新 `GUO_HN_RI_XIN`、南瑞 `NAN_RUI`、华软 `BEIJING_HR_HX`、其他 `OTHER`）。

## 查询数据状态

```sh
node scripts/upload-private-data.mjs --status --unit 汕头 --date 2026-11-05
```

只读，不写入。走页面同款 `GET /gdfire/api/data/personal?startTime=&endTime=&currentTime=&dataTypes=TRADE&dataTypes=POWER_GENERATE&dataTypes=MONTH`（三类要重复传，用逗号串会被接口拒绝），逐条打印数据项、包含机组、数据来源、`已上传／未上传` 与更新时间；日期默认取北京时间当天。

账号由工具箱统一配置。两条上传路径各自保留独立的 `auth_state.json`、锁文件和浏览器目录，可以并行运行：

```text
auth_state.json / auth_state.json.lock / .browser-profile-uploader/   # 目录模式
.batch-upload/auth_state.json / .batch-upload/.browser-profile/                     # 批量上传
```

真实配置及登录态保存在本地忽略目录，不进入版本控制。
