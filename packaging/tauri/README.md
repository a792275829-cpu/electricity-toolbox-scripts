# 桌面构建与发布

见 `../../desktop/README.md`。

- `build_support.py`：Mac 构建共用的源码导出、隐私排除、哈希、下载和解包逻辑。
- `prepare_payload.py`：干净导出源码，在目标系统构建锁定运行环境及浏览器。
- 预测总表是**可选种子**：构建机上有总表时放入 `payload/defaults/广东电价预测/输出/广东电价数据总表.xlsx`，首次启动复制到用户 `data/`（升级不覆盖已有总表或配置）；没有则跳过，安装包不含总表。可用 `--forecast-workbook /绝对路径/总表.xlsx`、`FORECAST_MASTER_WORKBOOK` 或 `FORECAST_MASTER_URL`（配 `FORECAST_MASTER_SHA256` 摘要）显式指定；显式指定但文件不存在或表头不符时仍然失败，不会静默改用其他文件。副本逐字节保留，普通输出、实验模型和登录态仍不打包。
- 总表属于本地业务数据，保持 Git 忽略。未内置时，首次运行 `train_d1_forecast.py` 检测到缺失会调用 `update_gdfire_data.update_master_workbook`，按模块配置的 `initialDate` 到当天从广电网接口一次性补齐并生成 `整合数据` 工作表；因此首次预测需要可登录的账号与网络，跨度过大时需自行调整 `initialDate`。本地源码迁移 ZIP 仍可附带经过校验的总表，需先初始化根目录 `.venv`。
- Desktop checks 用显式生成的合成总表做资源包 smoke build，不上传安装包；正式发布不再要求安装包内必须带真实总表。本地发版若有真实总表，仍按上面的校验流程随包携带。
- 固定运行资源在 `REQUIRED_RUNTIME_ASSETS` 集中检查：500kV 拓扑／坐标／接线图、中文字体、日报 Word 模板与主体映射、市场日报 Jinja／CSS／JS 模板、PDF 识别脚本、WPS 默认配置和应用图标。
- `prepare_tk.py`：从校验过的上游源码构建 Mac Tcl/Tk 8.6.18，保持原版控件／字体／交互，动态库最低部署版本 macOS 14。
- `prepare_workspace.py`：安装／升级 `app/` 中的托管源码；初始配置仅写入 `data/`，更新记录与备份分别放入 `state/`、`backups/`。检测本地修改并在失败时回滚。
- `workspace_layout.py`：将旧混合工作目录迁移到 v2 结构，保留账号、独立 profile、输出与样例，修正内部绝对路径；开发版使用独立目录。详见 `../../desktop/docs/directory-layout.md`。
- `migrate_forecast_data.py`：退出新版后，以 `--source 原版工作目录 --destination 新版用户数据工作目录` 迁入实际配置的数据总表，设置模块相对路径和预测输出目录，备份旧配置并校验文件；保留已有不同数据。初始化底表存在时一起迁入。总表和账号配置保存在用户目录，后续源码更新保留这些本地文件。
- `migrate_samples.py`：按原版本地 `.toolbox/migration-samples.json` 清单迁移全部模块的独立测试副本，校验哈希，保留已修改样例。参数为 `--source 原版工作目录 --destination 新版用户数据工作目录`，可加 `--zip 样例包.zip` 生成带相对路径说明的伴随分发包。增加 `--app-home` 时样例存放在新版 `samples/`（兼容旧目标的 `测试样例/`），本机来源快照位于原版 `.toolbox/`，均不进入源码发行包。
- `version.py`：统一版本号并核对发布标签。`set` 时用 `--notes` 写完整变更、`--highlight` 写 GitHub Release 展示的要点（3～6 条、每条 ≤120 字，落在版本段的 `### 发布要点`）；`check --release-notes` 只导要点并拼接 `release_notes.md` 的通用安装说明，没有要点时才回退整段日志。
- `release_index.py`：历史索引格式参考；命令行发布已停用，不再生成或上传 `latest.json`。
- `build_dmg.py`、`install_user_app.py`：生成用户安装 DMG；首次复制到 `~/Applications`，保留数据、校验签名、替换失败回滚，不请求管理员权限。
- `build_pkg.py`、`pkg-scripts/`：历史 PKG 兼容代码，不再用于当前发行流程。
- `validate_installer.py`：发布前核对 DMG、补丁、索引、校验文件、大小与 GitHub SHA-256 digest；核对索引与实际附件一致。
- `transfer_asset.py`：大安装包切片补传。`gh release upload` 直传 700 MB 偶发中断时，`split` 把 DMG 切成同源分片（默认 16 个，写 `logs/release-<tag>/transfer/`，拒绝写进 `dist/`），`upload` 把分片作为草稿发布附件补传（已存在且摘要一致的跳过），`join` 在任意稳定网络环境合并并校验总摘要，`clean` 只在发布上出现摘要一致的完整包后删除分片资产与本地分片，否则保留并报错。
- `dist/` 只放产物：`ElectricityToolbox-<版本>-apple-silicon.dmg` 及 `.sha256`、`updates/`、`tauri-build/`、迁移 `*.zip`。发布过程的日志、一次性脚本、分片与索引快照一律放 `logs/release-<tag>/`；`tooling/clean_workspace.py` 会按这个白名单清理 `dist/` 中的其它内容。
- `build_updates.py`：为最近三个同主／次版本正式版生成文件增量包，每个补丁必须实际还原并验签；继承已校验的历史索引，保留跨多个维护版本的升级路径。每次都保留完整 DMG。
- `desktop/python/delta_update.py`：版本绑定、文件／权限／链接校验和最小下载路径。客户端仅操作临时应用副本，借助共享 `installer_image.py` 生成本地安装镜像，继续复用原安装器。
- 跨主／次版本默认全量；同系列重大更新用 `build_updates.py --full-only` 或手动发布的 `full_only`，清空本次补丁路径以重设基线。历史发行附件不清理。
- 本地验证：先构建当前 DMG，再运行 `build_updates.py <当前.app> --base-app <只读旧版.app> --no-fetch-history --output dist`；不要用已改动的安装目录代替正式旧包。
- `requirements-macos.lock`：锁定 Mac 发行包的 Python 依赖与校验哈希。

流程参考 [Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev) 的 Tauri/GitHub Releases 分发方式，并按 [Tauri 官方发布文档](https://v2.tauri.app/distribute/pipelines/github/) 实现；没有复制其界面。

## 安装包体积控制

- `prune_runtime.py` 在签名前裁剪 staging 运行环境：移除单独暂存的 `runtime/node`、`pip`／`setuptools`／`_distutils_hack`（连同会让解释器启动报错的 `distutils-precedence.pth`）、`ensurepip`、Tk 9 目录与 dylib、明确列出的依赖测试目录；保留许可证、包元数据、模板及全部业务依赖。NumPy 的测试树存在运行时导入，必须保留；Python 头文件用于重复构建 Tk 扩展，同样保留。`python3`／`python` 与 `python3.11` 硬链接为同一份解释器。
- 仅保留 A 方案。完整 Chromium 与 headless shell 同时打包，Python 后台／PDF 沿用 Playwright 默认 headless shell，可见窗口使用完整 Chromium。删掉 headless shell 改让后台也用完整 Chromium，DMG 可再小约 82 MiB，但 2026-11 复测（单浏览器打开一页、4 次取中位）headless 启动 0.12 → 0.21 秒、进程树 RSS 255 → 743 MiB，双浏览器并发约 1.5 GB，因此仍不采用。源码运行仍优先系统 Chrome，Node 上传逻辑不变；各工具的 profile、登录态和锁保持独立。
- 新环境使用 `playwright install chromium`；复用曾按 B 裁剪的 staging 时自动补回缺失浏览器，跨平台传入不完整运行环境则拒绝构建。运行环境不内置 Node/npm 与 pip：业务 JS 脚本复用 Playwright 轮子自带的 `driver/node`，`toolbox.runtime.node_executable()` 按 `sys.prefix` 在磁盘上解析该路径，不 import Playwright。
- `subset_font.py` 把兜底中文字体按 GB2312 全部字符加该模块源码里出现的非 ASCII 字符做子集，保留可变字重轴与字形宽度：23.96 → 5.45 MiB。缺 `fontTools` 时保留完整字体并打印警告，构建不中断。
- `forecast_seed.py` 把首次安装的电价预测总表裁成近 12 个月、只留模型读取的 13 列（`日期`、`时刻`、目标价与 10 个 `日前*` 出力列），24.3 → 2.9 MiB，并回读校验数值。仓库里的原始总表不动，裁剪只发生在打包阶段；界面里 24 个月训练窗口因此只覆盖已积累的历史。
- 构建生成 `payload/runtime-pruning.json` 记录当次移除项及逻辑字节数。该数值不是 DMG 压缩收益。原生构建会执行 `validate_runtime.py`，离线验证科学计算、Python/Node 独立临时 profile、截图与 PDF。可用包内 Python 执行 `validate_runtime.py /绝对路径/payload --headed` 追加可见窗口验证；不会读取生产登录态或上传数据。
- 安装镜像用 LZMA（`hdiutil -format ULMO`，见 `desktop/python/installer_image.py` 的 `RELEASE_FORMAT`），不用 hdiutil 默认的 zlib（`UDZO`）。同一份 `.app` 实测：UDZO 587.7 MiB／20 秒，LZMA 421.1～422.7 MiB／165～167 秒，**−165 MiB（−28%）**，代价是构建多约 2.4 分钟。已核 LZMA 镜像可挂载（4 秒 → 11 秒）、`codesign --verify --deep --strict` 通过、`ditto` 复制出的应用可运行。应用内增量更新重建的镜像不下载，改用 `LOCAL_FORMAT`（ULFO），否则每次更新都多等 2.5 分钟；ULFO 实测 541.6 MiB／构建 17 秒／挂载 2 秒（LZMA 为 421.5 MiB／165 秒／11 秒），适合“本地产物、速度优先”的场景。
- 顺带记录 deflate 的实测边界，避免有人回头再试：`UDZO` 默认是 **zlib 1 级**（`man hdiutil` 明写 default 1, fastest），故 `-imagekey zlib-level=9` 在纯应用镜像上由 576.6 降到 541.7 MiB（+39 秒），仍比 LZMA 大 120 MiB；把同样的 tar 整流交给 `gzip -9` 是 476.6 MiB，说明 deflate 的 32 KB 窗口是主要瓶颈，而 UDIF 分块压缩又比整流多花约 65 MiB。整包 `xz -9e` 为 299.9 MiB，但要拿到这 120 MiB 得改成“镜像内放压缩包、安装时解压”，会破坏拖拽安装。
- 比较体积仍以 `.app` 的 `du -sk` 为准：UDZO 同内容连续三次波动约 20 MiB（592.7／611.6／612.3），LZMA 两次为 422.7／421.1 MiB（约 1.6 MiB）。小于该量级的差异不能当作真实收益。
