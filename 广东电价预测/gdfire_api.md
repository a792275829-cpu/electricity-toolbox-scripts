# 华能广东现货系统数据接口

站点：`https://xhxt.chng.com.cn/gdfire/SpotDecisionSupport/InfoCompare`

## 登录

1. `GET /usercenter/web/pf/login/info/publicKey`
2. 使用返回的 RSA 公钥和 PKCS#1 v1.5 加密密码。
3. `POST /usercenter/web/login`，查询参数：
   - `loginMode=2`
   - `username`
   - `password`（RSA 密文的 Base64）
4. 后续请求复用登录响应设置的 Cookie。

密码不写入脚本、配置或输出文件；更新脚本每次运行时通过隐藏输入读取。

## 已验证的长期更新接口

### 负荷、电源结构和新能源

`GET /gdfire/api/data/net/load`

参数：`startDate`、`endDate`、`provinceAreaId=044`。

返回 `dataNetLoadDTOList`，每个 `loadType` 同时包含日前
`forecastPeriodList` 和实际 `actualPeriodList`，每个有效日期含 96 个十五分钟值。
同一天存在多个版本时，使用 `versionDate` 最新的版本。

### 全省平均节点电价

`GET /gdfire/api/data/price/avg/node/price`

参数：`startDate`、`endDate`。

- `preAvgNodePriceDTOList`：日前全省平均节点电价。
- `rtAvgNodePriceDTOList`：实时全省平均节点电价。
- `preForecastAvgNodePriceDTOList`：日前预测电价，可作为额外预测特征。
- 每条记录包含 `date`、`tvMeta` 和 96 点 `avgNodePrice`。

### 机组检修

`GET /gdfire/api/unit/overhaulInfo`

参数：`startDate`、`endDate`。

返回机组、检修类别、原因、开始和结束时间。该数据不是 96 点曲线，单独保存；
用于建模时应按每个 15 分钟时点判断检修区间是否覆盖，并进一步汇总容量后再合并。

## 已识别但需要按业务参数调用的接口

- `/gdfire/api/data/net/channel`：西电东送通道，需要 `provinceAreaId` 和 `channelIdList`。
- `/gdfire/api/data/net/capacity`：容量信息。
- `/gdfire/api/data/net/market/main`：市场主体/运行信息。
- `/gdfire/api/enter/market/new-energy/query/compare`：新能源对比，POST。
- `/gdfire/api/data/net/ptt/queryPageInfo`：输变电检修信息。
- `/gdfire/api/data/net/block/queryPageInfo`：阻塞信息。
- `/gdfire/api/device/overhaul`：设备检修。
- `/gdfire/api/device/retire`：设备退役。
- `/gdfire/api/section/bound/list`：断面限额。

这些接口的原始结果不应在时间或单位口径未确认前直接拼入训练表。

## 更新命令

```bash
.venv/bin/python "广东电价预测/update_gdfire_data.py" --date 2026-07-11
```

账号优先使用工具箱保存的统一账号，也兼容本模块或上网电量模块的本地账号配置。业务路径只读取本模块 `config.json`。它会检查截至运行日 D 缺少 96 个时点的日期，只维护一份 `输出/广东电价数据总表.xlsx`，不会再要求手动选择和合并增量表。该总表也是预测脚本的默认输入。


`masterWorkbook`（默认 `输出/广东电价数据总表.xlsx`）、`predictionOutputDir`（默认 `输出/D+1预测`）和 `initialWorkbook`（默认 `输入/广东数据初始总表.xlsx`）均以本模块目录解析相对路径，支持 `~`。预测命令默认与更新任务使用同一总表：

```bash
.venv/bin/python "广东电价预测/train_d1_forecast.py"
```

预测固定导出 D+1 的 96 点电价及 `MAE.json`。`--input`、`--output-dir` 可显式覆盖预测路径；更新命令的 `--master-workbook` 可覆盖总表路径。旧增量、清洗表、检修表导出旁路已移除。

## 算法对照实验

普通预测入口已采用本次实验表现较好的两套方案，并自动选择：

- 预测日10类供需曲线齐全、数值有限、发布时间不晚于快照截止时间时，使用12个月当日供需模型，含节假日阶段及供需衍生特征，模型权重100%。
- 曲线缺失、发布记录缺失、抓取失败或禁用抓取时，使用3个月历史供需模型，25%模型与75%D-1同刻电价集成。历史供需按日期及时刻取D-1/D-7，训练与预测使用一致口径。

两套方案均沿用实验中的HistGradientBoosting参数（200轮、学习率0.05、15叶、最小叶样本40、L2=3、关闭提前停止），不因本机额外安装模型库而改变默认模型。
CLI 的 `--as-of` 表示预测基准日 D，默认上海时区当天；完整日前电价必须更新到 D，否则拒绝生成过期结果。
工具箱页面使用同一入口，并传递页面上选择的运行日D。滚动训练月数默认0表示自动选12/3个月，也可填写至少2个月覆盖自动窗口。
CLI支持`--historical-only`只使用历史信息，`--training-months 0`自动选窗口。
历史日期运行不会抓取当前接口来伪造历史快照。

保留原有三列、96点工作簿格式。`MAE.json`同时记录实际方案、训练窗口、模型权重、回测范围、快照截止时间及回退原因。
正式预测抓取的快照按每次运行时刻归档在预测目录的`预测日快照/`，供以后按决策时点验证。
当日供需方案的历史MAE仍是条件回测，历史发布版本尚未归档，不能视为实盘收益保证。

执行节假日阶段、供需衍生、3/6/12月窗口、近期权重、集成权重和分位数区间对照：

```bash
OMP_NUM_THREADS=4 .venv/bin/python "广东电价预测/train_d1_forecast.py" \
  --compare-algorithms --fetch-future-inputs --as-of 2026-10-11 \
  --output-dir "广东电价预测/输出/算法实验/20261011"
```

实验仅使用现有 scikit-learn，不强制安装 LightGBM/XGBoost。开发期跨多个季节及假期选择窗口、权重，
最近14个完整日期作为独立留出期；每个窗口开始前训练一次，每天使用更新的历史特征。
输出 `算法实验对比.xlsx`、逐点CSV、`实验结果.json`、日期与发布时间核验后的预测日供需快照及实验预测。
所有输出放在实验目录，不覆盖工具箱原有预测文件。

当日供需曲线通过现有接口抓取，仅接受 `versionDate` 不晚于快照截止时刻的96点预测曲线，排除实际值和价格标签。
历史总表没有逐日发布版本，故历史当日供需情景单列；若预测日10类供需曲线完整，再单独导出
`当日供需条件实验_D+1日前电价.xlsx`。它只适用于快照截止时间之后，不能证明更早交易决策时可用。
天气列缺少空间与来源确认，滞后天气只作探索；预测区间以开发期残差校准，留出期覆盖率必须同时查看。
