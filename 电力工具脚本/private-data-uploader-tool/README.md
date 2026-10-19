# 私有数据上传工具

从 macOS 工具箱的“出清结果汇总上传”页面选择复盘目录，预览后执行上传。

实际上传逻辑为 `scripts/upload-private-data.mjs`，Node 依赖仅作用于本子工具。首次依赖安装由根目录 `setup_macos.command` 完成；也可以在本目录运行 `npm install`。

需要命令行预览时，在本目录执行：

```sh
node scripts/upload-private-data.mjs --plan --source "$HOME/Desktop/复盘/5-12"
```

确认预览后可使用 `--execute` 执行真实上传；`--login` 用于重新登录。
账号由工具箱统一配置，上传工具保留各自独立的 `auth_state.json`、锁文件和 `.browser-profile-uploader/`。真实配置及登录态保存在本地忽略目录，不进入版本控制。
