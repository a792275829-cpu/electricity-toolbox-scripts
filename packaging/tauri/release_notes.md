## 下载安装

仅支持 macOS 14+、Apple 芯片。

下载 `ElectricityToolbox-<版本>-apple-silicon.dmg`，打开后先退出旧工具箱，再双击“安装到我的应用程序.command”。应用安装到 `~/Applications/电力工具箱.app` 并自动打开，也可手动拖入个人用户目录的 Applications 文件夹。

后续点击“下载并更新”，自动下载、验签、检查任务、保存配置、替换并重启，无需重复运行安装器。用户拥有的可写应用目录通常不需要管理员授权；系统目录或 DMG 挂载目录内不执行自动更新。

0.1.6 及更早 PKG 版需要从发布页面下载本 DMG 迁移一次，账号、登录态、业务数据和样例继续保留在原用户数据目录。旧 `/Applications` 副本不会自动删除，确认新版正常后可手动清理，并更新旧 Dock／桌面快捷方式。

首次运行仍可能需要按 macOS“隐私与安全性”提示允许。

`.sha256` 是 DMG 校验文件。`latest.json`、`.app.tar.gz`、`.sig` 用于自动更新；源码附件无需安装。

已内置 Python、Node、Chromium 与 Tcl/Tk。
