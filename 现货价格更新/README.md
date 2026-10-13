# 现货价格分析表更新

从华能广东公司交易辅助决策系统“新能源现货交易结果”页面对应接口抓取 24 个整点的日前、实时出清电价，并写入所选场站的现货价格分析工作簿。工具箱页面可勾选归湖、鮀莲、海风中的一个或多个场站，只检查并更新已选场站的文件。

- 华能（潮州潮安）新能源有限责任公司 → 归湖工作簿
- 华能（汕头金平）新能源有限责任公司 → 鮀莲工作簿
- 华能广东汕头海上风电有限责任公司 / 广澳海风场 → 海风工作簿
- `dataType=1` → `日前价格` 工作表 B:Y（0时至23时）
- `dataType=2` → `实时价格` 工作表 B:Y（0时至23时）

模块使用独立的 `auth_state.json`、锁文件和 `.browser-profile/`。这些本地状态均已由仓库 `.gitignore` 排除。

```bash
# 登录/刷新独立登录态
../.venv/bin/python update_spot_prices.py --login

# 更新单日，默认写入前生成时间戳备份
../.venv/bin/python update_spot_prices.py --start-date 2026-08-30

# 更新日期区间
../.venv/bin/python update_spot_prices.py --start-date 2026-08-30 --end-date 2026-09-01

# 只抓取并校验，不写工作簿
../.venv/bin/python update_spot_prices.py --start-date 2026-08-30 --dry-run

# 只更新海风，默认文件为 ~/Desktop/日常工作/2026年现货价格分析-海风.xlsx
../.venv/bin/python update_spot_prices.py --start-date 2026-09-26 --stations 海风

# 只更新归湖和海风
../.venv/bin/python update_spot_prices.py --start-date 2026-09-26 --stations 归湖 海风
```

未指定 `--stations` 时默认更新三个场站。默认工作簿位于桌面“日常工作”目录，归湖、鮀莲保留原文件名，海风为 `2026年现货价格分析-海风.xlsx`。可通过 `--guihu-workbook`、`--tuolian-workbook`、`--haifeng-workbook` 或工具箱页面更改。

日期使用数据源中的同日日期，在目标工作表 A 列按日期匹配，按 0时至23时顺序写入 B:Y。日前和实时逐日独立更新：完整 24 点才写入；缺失、点数不足或无效值只跳过该日期和价格类型，保留原单元格。默认覆盖已有价格并生成时间戳备份，`--skip-existing` 可跳过已有非空价格。保存采用同目录临时文件原子替换，保存后会重新打开回读 24 个值和原数字格式。
