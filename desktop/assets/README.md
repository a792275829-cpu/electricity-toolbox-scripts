# 共用应用图标

`app-icon-original.png` 保留用户提供的原图。`app-icon.png` 是根据用户要求去掉外围白边、放大主体后的正式图标，圆角外透明，主体铺满画布。Mac 与 Windows、原版 Tk 与 Tauri 均使用这张图。

`app-icon-256.png` 用于 Tk 窗口／Dock；`../src-tauri/icons/` 中的 PNG、ICNS、ICO 用于 Tauri、Mac 原生 Tk 进程、Windows 安装和窗口图标。这些平台资源从正式图标转换尺寸和格式，保留透明通道。

更换原图后，使用 Tauri CLI 的 `icon` 命令生成平台资源，并将 256 像素 PNG 同步为 `app-icon-256.png` 和 `128x128@2x.png`。此步骤只生成图标，不制作安装包。
