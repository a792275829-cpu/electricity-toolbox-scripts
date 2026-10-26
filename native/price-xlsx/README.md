# Rust Excel 后端

可执行程序名和安装位置沿用首阶段的 `toolbox-price-xlsx`，共用于 500kV 清洗、电量汇总和市场日报的实时机组复盘取数。源码构建：

```bash
.venv/bin/python native/price-xlsx/build.py
```

WPS 配置窗口复用此 Rust 核心的 `wps_url_choices` 操作，将已读取的表格名称显示为 `网址（表格名称）`，下拉项与输入框保持一致。名称单独缓存在本机配置，导入／导出时一并保留；网络请求与写入配置始终使用原始网址，标题内含括号时也不会截断网址。尚未读取名称的链接保留原网址，读取成功后自动更新。

市场日报的 `market_manual` 操作负责 36 格补录校验、逐字段来源优先级、日期匹配、煤气机台数合并及增减计算。`gd_power_report/manual_market.py` 仅传递数据和定义界面标签，沿用现有 Excel/PDF 流程。网站有效值优先，手填优先于工作簿补值；0 有效、均价允许负数、机组台数为非负整数。报告日期变化后清空补录，缺少当前值或对比值时阻止输出。源码初始化在有 Cargo 时构建共享后端；桌面发行继续随包携带后端，无需用户另装 Rust。真实 PDF 集成回归可通过 `TOOLBOX_REPORT_PDF_E2E=1` 启用。

## 其余热点优化

- 500kV 匹配：每份文件一次构建规范化节点索引，保留“精确匹配优先、最长名称优先”的原规则；行名称缓存避免重复处理，浮点数及 Decimal 计算口径不变。
- 预测总表：`read_frame` 精简协议只读取目标工作表，省略普通数值的逐格样式／原始文本映射；日期和超大整数保留必要元数据。继续使用 pandas 原有表头、NA 和 dtype 推断，按原规则验证每日 96 个时点及日期连续性。
- 汇总写出：Rust 序列化 `sheetData`，样式、合并、冻结窗格、行列尺寸与文件包关系仍由 openpyxl 原实现生成；复用字体／边框／对齐对象，避免逐单元格重复创建。调用方保留同目录临时文件、保存回读和原子替换。复杂工作簿自动使用 Python 兼容路径。
- PDF：一次分析作用域内复用 PDF 文档，避免重复申报解析；Rust 计算颜色解混与每行中位覆盖率。原图像以 uint8 传输，数值以小端 float64 传输；一次图表分析只使用一个有界生命周期的子进程。保留原色相支持、噪声排除和容量校准规则。
- 特征：复用日期键索引和延迟位置，分组 rolling 替代逐组 Python 回调，批量合并外生字段以减少 DataFrame 碎片；这部分采用算法优化，继续使用原 pandas 数值内核。

新增开关：`TOOLBOX_COMPUTE_BACKEND=auto|python|rust` 控制 PDF 计算，`TOOLBOX_FEATURE_BACKEND=auto|python` 控制特征实现。均默认 auto，兼容路径可单独选择。精简预测读取、汇总读取与写出继续由 `TOOLBOX_XLSX_BACKEND` 控制。节点索引没有新开关，其规则与原算法一致。

本轮性能对比脚本为 `benchmark_remaining.py`。需要提供优化前的五个源码文件快照目录作为 `--baseline-dir`，以及本机原始数据样本；对比中间路径固定，以同时检查最终网页字节、工作簿内容／样式、预测数据与特征（含 dtype 和 NaN）、PDF 像素／报价结果。所有业务输出写入临时目录。

`toolbox_native_excel.py` 提供只读值／数字格式接口；不提供完整样式对象或模板编辑。500kV 使用原 `read`／`write` 协议；新消费者使用 `read_cells`，电量汇总在一次进程调用内通过 `read_cells_many` 批量读取所有来源工作簿。文件遍历、场站／机组排序、同小时后写覆盖、结算电价优先、格式推断和输出样式继续采用既有业务代码。

读取保留自定义数字格式、1900／1904 日期基准、Excel 时间／时长、缓存公式值、错误单元格、空白格式和大整数原始数值文本。重置维度时使用实际 XML 行列，避免导出文件的陈旧 dimension 截断数据。

## 后端选择

- `TOOLBOX_XLSX_BACKEND=auto`（默认）：新消费者优先原生读取；缺少程序或不支持的格式回退 Python，读取失败保留诊断警告。
- `TOOLBOX_XLSX_BACKEND=rust`：原生后端缺失或读取失败即报错，供验收使用。
- `TOOLBOX_XLSX_BACKEND=python`：新消费者恢复原读取路径。
- 500kV 保留独立的 `TOOLBOX_500KV_BACKEND` 选项。

市场日报中的完整表格样式仍由 openpyxl 处理。如果当前报告作用域已加载同一份完整工作簿，机组复盘直接复用它；只有未缓存时才进行原生取数。因此不能将机组复盘独立解析的加速比等同于整份日报生成的加速比。

批量读取会驻留所有来源数据；峰值内存尚未量化。超大目录可指定 Python 流式路径。运行端不需要 Cargo；打包时从当前源码构建原生程序，升级清单同时管理程序与根目录 Python 适配模块。

## 验证和性能比较

```bash
cd 电力工具箱
TOOLBOX_XLSX_BACKEND=rust PYTHONPATH=.. ../.venv/bin/python -m unittest discover -s tests -p test_native_excel.py -v
```

从仓库根目录对自备样本比较，所有导出结果仅写临时目录：

```bash
.venv/bin/python native/price-xlsx/benchmark_consumers.py \
  --summary-dir /path/to/日期场站目录 \
  --market-file /path/to/出清情况.xlsx \
  --market-date 2026-09-20 --repeats 5 --output /path/to/benchmark.json
```

比较电量汇总读取、完整本地汇总导出、独立机组复盘解析；包含原生进程启动和 JSON 通信。核对机组数据、全部输出单元格值／类型／数字格式／样式、合并区域、冻结窗格、行列尺寸及完整复盘数据。不会登录网站、上传文件或生成远程市场数据。

## 总表更新与交易分析

预测增量更新复用 `read_frame`；重复列识别、按日期和时刻取最新非空值、每日 96 点检查保持原业务规则；增量合并见下述四项试验。格式提取只读取前两行和列宽，并复制实际字体、边框、填充等样式组件，避免跨工作簿直接引用样式表索引。

`write_frame` 接收临时逐行 JSON 文件的位置，由 Rust 流式生成工作表 XML；日期序号、16 位有效数字、空值、布尔、错误文本和公式沿用 openpyxl 的转换规则。Python 只创建每列各类型的样式样本，压缩包及样式表继续由 openpyxl 生成。每行传输受行宽约束，数据不作为整份 JSON 请求驻留；临时文件随调用退出清理，最终仍由原调用方原子替换。格式源不会被修改。`auto` 模式遇到旧 helper 或失败时恢复 Python 流式写入，严格 `rust` 模式保留异常。

收益统计目前优先使用下述局部检查；回退完整工作簿时，缓存值读取使用共享数值读取器。交易分析报告和出力偏差筛选在后续共享扫描优化后默认启用 Rust（五轮真实样本复测均有收益）；交易系统信息的尾部检查／数值回读仍设置 `native_in_auto=False`，默认继续使用原 Python 读取，严格模式可对比。公式判断和原 XLSX 包修补继续由原逻辑处理；现货价格的公式回读没有改成仅缓存值读取。

`benchmark_updates.py` 对比显式源码快照，输入 `--forecast`、`--report`、`--benefit`、`--transaction`、`--baseline-dir` 和 `--output`。验证完整 DataFrame、导出全部单元格值／类型／数字格式／样式摘要、报告 HTML 字节、偏差行、收益目标计划及交易表日期尾部。计时包含进程启动、传输、流式临时文件和压缩，不含验证；不会登录网站或改写真实输入。

## 共享内核的后续优化

工作表元数据扫描改为直接处理 XML 事件，跳过无关节点的属性映射和回调字符串分配。精简 `read_frame` 响应增加 `prepared_frame` 标记：Rust 先将普通空值和可安全表示的整数规范化，Python 仅转换日期、错误、ISO 时间和大数覆盖值；pandas 的表头、NA、dtype 推断仍是最终解析器。旧程序没有该标记时继续走原逐格兼容路径，通用只读接口保持原单元格语义。

格式提取使用局部只读工作表子类与现有 openpyxl 工作簿／样式解析器，跳过初始化尺寸探测，仅读取前两行和列宽。没有 `dimension` 的流式 XLSX 也不会为了提取样式先扫描全部数据；不会全局修改 openpyxl 类，其他调用和并发任务不受影响。

`benchmark_hotspots.py` 需要上一轮 `toolbox_native_excel.py`、`toolbox_native_write.py` 及 `bin/toolbox-price-xlsx` 的快照目录作为 `--baseline-dir`，另提供 `--forecast` 与 `--output`。对比包含进程启动和传输，验证完整 DataFrame、格式布局、输出整张工作表／样式表／工作簿 XML 摘要，并重开导出文件核对数据。

## 收益统计的局部检查与内存

`toolbox_workbook_inspection.py` 复用 openpyxl 的流式解析器，一次读取保留 A、B、C、J–N 列的公式／数值，以及 A 列的公式缓存；不创建全表可编辑单元格对象，也不额外启动数值读取子进程。原有日期公式解析、表头定位、0–23 点完整性、月份／起止日期筛选、仅填空白及字段映射保持原调用逻辑。

样式 ID 按完整工作簿相同的访问顺序延迟规范化，缺失单元格使用原默认空样式。共享公式母式即使位于未保留列，也交给原解析器登记。合并日期的非锚点值会清除；数据区目标列有合并、锚点边框需要传播或非月份页含合并样式时，自动恢复完整工作簿路径。

`TOOLBOX_BENEFIT_INSPECT_BACKEND=auto|full|stream` 控制检查实现：默认 auto；full 保留优化前流程；stream 用于严格验证，不隐藏不支持模板的异常。此开关不改变写入／登录／覆盖规则。写后校验现在也默认采用局部读取（见下文），复杂模板自动回退完整工作簿；检查阶段的指标仍不能直接等同于整次联网更新的峰值。

`benchmark_inspection.py` 用交替顺序、每次独立进程对比快照和当前实现，记录检查耗时、Python 主进程与子进程各自的 RSS 高水位，并比较包含格式／样式 ID 的完整待填计划。RSS 包括工作进程导入及检查，测量在结果序列化前完成；两种进程的高水位不相加，不冒充整个应用的同时峰值。提供 `--baseline-dir`、`--inputs`、`--output`，可设置 `--end-date` 和 `--repeats`。


## 四项性能试验（2026-10-21）

- 收益写后校验仅保留实际写入的 C/J–N 单元格，继续核对值、数字格式和规范化样式编号。`TOOLBOX_BENEFIT_VERIFY_BACKEND=auto|full|stream` 默认 auto，使用与检查阶段相同的复杂合并回退规则；full 可恢复完整加载。
- `read_frame` 默认通过临时文件接收 JSON，Python 复用私有解析结果中的行列表；Rust 提前释放 XML／calamine 数据，并转移 JSON 数组所有权，减少整表复制。`TOOLBOX_FRAME_TRANSPORT=file|pipe` 默认 file；其他操作保持原传输。临时文件随调用关闭，数据／dtype 和旧 helper 协议不变。
- 总表按日期／时刻只对受影响的历史行和增量做原有 groupby.last，再与未触及历史拼接排序。仅唯一且非空的历史键、受支持列类型与一致 dtype 才启用；其他情况回退原完整合并。`TOOLBOX_FORECAST_MERGE=incremental|full` 默认 incremental。
- 交易报告批次包含重复来源时，默认在批次作用域内复用原生进程和只读 JSON 缓存；不同来源批次仍用原路径。`TOOLBOX_EXCEL_SESSION=auto|oneshot|session` 可选择策略。缓存最多 4 项／16 MiB，按文件身份、大小、mtime／ctime 与请求参数失效；每次命中重新解析对象，退出批次清空并回收进程。read_frame 不缓存。

真实样本每项交替三轮、独立工作进程的中位数：收益完整检查／副本写入／回读 2.177→1.968 秒（其中回读 0.505→0.281 秒）；97,440×55 总表读取 3.145→2.821 秒，Python／Rust 各自峰值 557→452／918→549 MiB；192 条含覆盖增量合并 0.110→0.092 秒，96 条纯追加 0.107→0.088 秒；重复来源四次批量 0.341→0.243 秒。不同文件批量仅 0.343→0.337 秒，收益不足，未默认启用会话。

`benchmark_all_trials.py` 使用 `--baseline-dir` 快照（包含 bin/toolbox-price-xlsx）、`--forecast`、`--benefit`、`--batch-inputs`、`--output`。全部业务输出在临时目录；核对全部 XLSX 包成员、DataFrame 数据／dtype 及 HTML 字节，确认原输入摘要不变。RSS 包含进程导入和输入准备，分别记录 Python／子进程各自高水位，不相加，也不代表完整 GUI／联网任务峰值。无需新增依赖或二进制传输协议。

验证：590 项回归为 589 通过、1 项已有日期弹窗 Escape 关闭测试失败；兼容路径额外 35 项、启动器 smoke test 和 Rust 格式检查通过。两份真实收益模板副本共 2,160 格经局部及完整加载分别回读值／格式／样式，原文件未改。未构建／发布 DMG，未测跨 Mac 安装或联网更新全流程。
