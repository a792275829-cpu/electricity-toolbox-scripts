# Tauri 工程与运行目录

## 源码工程

原版各业务工具的目录和启动入口保持原位，供原版与桌面版共同使用。

```text
desktop/
  src/                  React 更新窗口
  src-tauri/src/        Rust 生命周期、托盘、更新与 Python 进程管理
  src-tauri/icons/      安装图标
  src-tauri/capabilities/ Tauri 权限声明
  python/               原版 Tk 界面与 Tauri 之间的进程协议
  tests/python/         桌面、迁移、目录与生命周期测试
  docs/                 桌面工程文档
packaging/tauri/         资源准备、目录迁移与发布工具
.github/workflows/      双端 CI 与发布流程
```

`desktop/node_modules/`、`desktop/frontend-dist/`、`desktop/src-tauri/target/`、`desktop/src-tauri/payload/` 和根目录 `dist/` 都是忽略的依赖或生成物。不会把这些目录复制进可编辑业务源码。

资源准备后的结构为 `payload/app/`（仅运行源码与必要模板／字体）、`payload/runtime/`（Python、Node、浏览器）、`payload/defaults/`（空账号的初始配置）、`payload/bootstrap/`（安装和目录迁移脚本）和 `payload/manifest.json`。测试、CI、安装脚本和前端工程源码不进入 `payload/app/`。

## 用户运行目录 v2

Mac 用户目录：`~/Library/Application Support/com.electricity.toolbox`；Windows：`%APPDATA%\com.electricity.toolbox`。

```text
用户目录/
  app/                  受发行清单管理的业务源码，更新可替换
  data/                 用户拥有的运行数据，更新保留
    .toolbox/qingneng/   共享账号（不共享浏览器 profile）
    电力工具箱/         文件选择器设置
    各原版模块目录/      配置、独立登录态、profile、数据总表、输出
  samples/              各模块的独立测试文件与说明
  state/                目录版本、安装清单及迁移事务记录
  backups/              原配置、迁移记录和源码更新备份
  logs/                 桌面诊断日志的保留位置
  cache/                可重新生成的缓存
  development/          开发版独立使用同样的 v2 结构
```

数据中保留原模块中文名称、内部相对路径和输出文件名。源代码和静态模板从 `app/` 读取；需要写入的内部路径统一通过 `toolbox_storage.data_path()`，Node 上传模块使用对应的 `storage-paths.mjs`。它们只在桌面进程设置 `TOOLBOX_CODE_ROOT`、`TOOLBOX_DATA_ROOT` 时生效。外部工作簿路径保持原值，原版源码启动没有这些变量时保持原行为。

开发版直接执行仓库源码，数据保存在 `development/data/`，修改 Python 后重启即可，不需要打包。开发数据与已安装版本的数据独立；首次测试可从旧版工作目录复制一份，再执行目录迁移。不会在每次启动时覆盖开发配置。

首次导入开发数据可执行 `python packaging/tauri/workspace_layout.py 开发用户目录 --import-from 旧版workspace目录`。该命令复制并迁移，同时把复制后的内部路径改到开发目录；开发目录已有数据时拒绝重复覆盖。关闭来源工具箱及其浏览器任务后再导入。

## 兼容迁移

新版首次启动会将旧 `workspace/` 按原安装清单拆分为 `app/` 与 `data/`，把原 `测试样例/` 移入 `samples/`，把更新记录和备份移入对应目录。仅修改配置中指向旧工作目录的绝对路径；外部路径和业务字段不变，修改前备份原配置。

迁移前检查冲突；移动或配置写入失败时回滚；启动中断后通过事务记录恢复。没有原安装清单时停止，避免猜测文件归属。已有本地源码修改会保留，后续源码更新遇到冲突仍停止覆盖。

手动迁移只使用 `python packaging/tauri/workspace_layout.py 用户目录`；执行前关闭对应工具箱及其浏览器任务。此命令不构建或打包。旧安装包不认识 v2 结构，因此先在 `development/` 验证，正式用户目录由后续新版启动时迁移。

测试样例迁入新版使用 `migrate_samples.py --source 原版目录 --destination 用户目录 --app-home`。预测数据迁入 `用户目录/data/`。发布样例包仍单独分发，不将真实账号和业务数据纳入通用发行源码。
