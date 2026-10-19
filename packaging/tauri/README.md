# 桌面构建与发布

见 `../../desktop/README.md`。

- `build_support.py`：Mac 构建共用的源码导出、隐私排除、哈希、下载和解包逻辑。
- `prepare_payload.py`：干净导出源码，在目标系统构建锁定运行环境及浏览器。
- `prepare_tk.py`：从校验过的上游源码构建 Mac Tcl/Tk 8.6.18，保持原版控件／字体／交互，动态库最低部署版本 macOS 14。
- `prepare_workspace.py`：安装／升级 `app/` 中的托管源码；初始配置仅写入 `data/`，更新记录与备份分别放入 `state/`、`backups/`。检测本地修改并在失败时回滚。
- `workspace_layout.py`：将旧混合工作目录迁移到 v2 结构，保留账号、独立 profile、输出与样例，修正内部绝对路径；开发版使用独立目录。详见 `../../desktop/docs/directory-layout.md`。
- `migrate_forecast_data.py`：退出新版后，以 `--source 原版工作目录 --destination 新版用户数据工作目录` 迁入实际配置的数据总表，设置模块相对路径和预测输出目录，备份旧配置并校验文件；保留已有不同数据。初始化底表存在时一起迁入。总表和账号配置保存在用户目录，后续源码更新保留这些本地文件。
- `migrate_samples.py`：按原版本地 `.toolbox/migration-samples.json` 清单迁移全部模块的独立测试副本，校验哈希，保留已修改样例。参数为 `--source 原版工作目录 --destination 新版用户数据工作目录`，可加 `--zip 样例包.zip` 生成带相对路径说明的伴随分发包。增加 `--app-home` 时样例存放在新版 `samples/`（兼容旧目标的 `测试样例/`），本机来源快照位于原版 `.toolbox/`，均不进入源码发行包。
- `version.py`：统一版本号并核对发布标签。
- `release_index.py`：继续为旧版客户端生成签名更新索引。
- `build_pkg.py`、`pkg-scripts/`：生成固定安装到 `/Applications` 的 PKG，安装前检查退出，安装后清理已确认的旧应用副本。
- `validate_installer.py`：发布前核对 PKG 附件、校验文件、大小与 GitHub SHA-256 digest。
- `requirements-macos.lock`：锁定 Mac 发行包的 Python 依赖与校验哈希。

流程参考 [Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev) 的 Tauri/GitHub Releases 分发方式，并按 [Tauri 官方发布文档](https://v2.tauri.app/distribute/pipelines/github/) 实现；没有复制其界面。
