## 下载安装

仅支持 macOS 14+、Apple 芯片。

下载 `ElectricityToolbox-<版本>-apple-silicon.dmg`，打开后先退出旧工具箱，再双击“安装到我的应用程序.command”。应用安装到 `~/Applications/电力工具箱.app` 并自动打开，也可手动拖入个人用户目录的 Applications 文件夹。

后续版本只发布 DMG。下载并打开新版 DMG，先退出旧工具箱，再运行镜像中的安装命令覆盖应用；原配置、登录态、业务数据和样例保留。用户应用目录安装不请求管理员权限。

0.1.6 及更早 PKG 版需要从发布页面下载本 DMG 迁移一次，账号、登录态、业务数据和样例继续保留在原用户数据目录。旧 `/Applications` 副本不会自动删除，确认新版正常后可手动清理，并更新旧 Dock／桌面快捷方式。

首次运行仍可能需要按 macOS“隐私与安全性”提示允许。

`.dmg.sha256` 是 DMG 校验文件。不再分发独立 App 压缩包、签名或自动更新索引；源码附件无需安装。

已内置 Python、Node、Chromium 与 Tcl/Tk。
