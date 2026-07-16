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
.venv/bin/python update_gdfire_data.py --date 2026-07-11
```

脚本从本模块 `config.json` 读取账号密码；若该文件不存在，则复用 `上网电量抓取/config.json`。它会检查截至运行日 D 缺少 96 个时点的日期，只维护一份 `输出/广东电价数据总表.xlsx`，不会再要求手动选择和合并增量表。该总表也是预测脚本的默认输入。
