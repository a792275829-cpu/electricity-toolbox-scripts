# 维护与验证入口

当前工作区保留各工具的独立入口，不要求把整个仓库安装成一个 Python 包。

## 环境与测试

- 源码初始化运行 `./setup_macos.command`，使用 `.venv` 与 Python 3.11；版本和哈希唯一来源是 `packaging/tauri/requirements-macos.lock`，根 `requirements.txt` 引用同一锁。Node 仍仅供需要它的子工具使用。
- 完整验证：`.venv/bin/python tooling/run_tests.py --native --report logs/tests.json`。先运行 `.venv/bin/python native/price-xlsx/build.py` 准备 Rust 后端。
- 针对性验证：增加 `--suite toolbox --pattern 'test_market_table*.py'`；可选套件为 toolbox、desktop、section、trade、morning、wind、spot、group、transaction、report、forecast、maintenance。
- 每个测试模块使用独立进程与工作目录，超时默认 180 秒，超时会终止其进程组；失败保留原始输出。无测试用例也视为失败。结果 JSON 包含用例计数、耗时、退出码和完整诊断，每完成一个模块立即原子保存；中断保留已完成结果。每个模块使用独立临时数据目录，避免读写真实配置或登录态。
- CI 与正式发布共用这个入口。新增目录必须登记到 `SUITES`；维护测试会检查是否有遗漏，防止新工具测试游离于 CI。
- 清理工作区：`.venv/bin/python tooling/clean_workspace.py`（干跑，列出待删项与体积），确认后加 `--apply`。默认只保留最新两个版本的安装包，删除 `dist/release-v*/` 内的重复副本、`desktop/src-tauri/target` 与 `payload`；`dist/updates` 的增量补丁、`dist/tauri-build` 的下载缓存和发行日志一律保留。`--keep N` 调整保留版本数，`--keep-build` 只清安装包副本、保留构建缓存。
- 更新依赖时重新生成并审查锁文件，在源码环境执行初始化、全量测试和启动器 smoke。涉及 openpyxl 时必须保留私有接口兼容测试和现有 Excel 写入／格式／公式回读测试，不单凭版本号判断兼容。

## 业务与模块边界

- 批量交易分析的完整规划、文件名冲突检查、会话复用和失败策略位于 `toolbox_shared/trade_batch.py`。各 GUI、服务和 CLI 调用同一执行器；独立 GUI 保留部分完成，服务和 CLI 保留遇错停止。
- 交易报表计算与生成位于 `toolbox_shared/trade_report.py`。工具箱页面、服务和偏差分析通过普通导入调用；独立脚本保留旧导出，Tk 组件仅在打开 GUI 时加载。固定日前／实时 HTML 摘要用于核对重构前后输出。
- 集团上传的省内表计算与写入位于 `toolbox_shared/province_excel.py`。A2 日期、B2 台数、C2 数值及格式回读后才提交；公式在内存中求值，一次序列化，已有数值原样保留。已有目标复用公共提交／回滚，新目标通过排他创建发布，失败清理临时文件并保留原数据。
- `toolbox_shared/market_contracts.py` 维护市场表的精确字段顺序、工作表、列范围和来源日期。`tooling/fixtures/market_contract.json` 是固定契约快照；业务变更需明确核对后同步更新。D、D-1、D-2、D-6 为现有语义，不能依据报表名称猜测。
- WPS 数值和区域逻辑位于 `toolbox_shared/wps_values.py`，浏览器／CDP 生命周期位于 `toolbox_shared/wps_browser.py`，Excel读取、映射和写入回读位于 `toolbox_shared/wps_service.py`；后两者均不依赖Tk。路径与启动锁由 `toolbox_shared/wps_paths.py` 共用。原GUI路径及函数导出保留，远程读取转发在GUI适配层处理。浏览器profile、锁与登录状态继续按工具隔离。
- 500kV 的 HTML 展示位于 `500kV断面分析/src/map_html_view.py`，分析、拓扑和坐标构建保留在原模块。两种视图的固定输出摘要验证拆分时字节一致；有意调整展示时重新审核快照。
- 市场日报的 Excel 显示值、主题颜色、单元格样式和合并区域布局位于 `gd_power_report/clearing_table_view.py`，业务指标解析仍在原模块；旧私有函数继续导出，百分比、日期、金额和合并单元格通过保存／重开回读验证。
- 页面通过显式 `tool_id`、动作参数和 `page_contracts.py` 的字段契约路由业务，不以类名或提示文案推断动作。汇总文件路径由服务按复盘文件夹名称生成。
- 任务失败在原错误文字之外携带稳定的 `error_detail`，包含错误码、工具、动作、失败阶段、原异常类型及退出码；旧客户端继续使用原错误文字，未知类别保留unknown。
- 新输出通过macOS `renamex_np(RENAME_EXCL)` 排他发布，文件系统不支持该能力时尝试硬链接；两者均不可用时保留现有文件并报错，禁止退化为有覆盖竞态的先检查后替换。
- 新的稳定纯逻辑使用正常导入；`runtime.load_module` 保留为旧独立脚本的兼容适配器。公共模块通过 `toolbox_shared.<模块>` 导入，保持模块／类型／锁的唯一身份；工具目录的同名辅助模块继续隔离。避免新增依赖进程全局 `sys.path`／`sys.modules` 修改的业务逻辑。
- 发行源码过滤会包含 `toolbox_shared/` 与已有工具目录中的新源码，维护测试同时检查本轮提取模块实际被包含。

## 更新兼容矩阵

| 场景 | 预期路径 | 自动验证 |
|---|---|---|
| 同主次版本内最近三个旧版 → 新版 | 校验后还原临时应用 | 合成应用逐版还原并比较完整清单及安装器 |
| 多步路径比直达更小 | 选择总下载量最小的路径 | 最短路、直达、多步及完整包大小比较 |
| 跨主／次版本 | 完整 DMG | 生产端拒绝跨版本补丁；消费端忽略跨版本边 |
| 不认识的补丁协议 | 完整 DMG | 索引拒绝及既有下载回退测试 |
| 本地旧应用变化／补丁损坏 | 完整 DMG | 文件摘要、权限、链接、清单和下载回退测试 |
| 用户取消 | 结束更新 | 不触发完整包回退 |
| 安装或启动失败 | 恢复旧版 | 自动安装与回滚隔离测试 |

普通测试中的历史版本使用合成应用，不能替代正式发布时的真实旧版验证。发布仍必须从最近三个正式版本的应用副本实际还原，核对全部文件、权限、符号链接和签名，校验附件摘要；重大更新使用 `--full-only`。不改变历史附件保留规则。
