# 共用应用图标

`app-icon-original.png` 保留用户提供的原图。`app-icon.png` 是正式图标，外围为透明留白，主体约占画布的 80%，使 Dock 中的视觉大小与其他 macOS 应用接近。Mac 原版 Tk 与 Tauri 均使用这张图。更换图案时保留这层透明留白，不要将主体放大到铺满画布。

`app-icon-256.png` 用于 Tk 窗口／Dock；`../src-tauri/icons/` 中的 PNG、ICNS 用于 Tauri 和 Mac 原生 Tk 进程。这些平台资源从正式图标转换尺寸和格式，保留透明通道。

更换正式图标后，使用 macOS `sips` 按完整画布缩放生成 PNG，并用 `iconutil` 从标准 `.iconset` 生成 ICNS；不要按非透明区域裁剪。将 256 像素 PNG 同步为 `app-icon-256.png`、`256x256.png` 和 `128x128@2x.png`。此步骤只生成 macOS 图标，不制作安装包。
