# 私有数据上传工具

桌面入口：

```text
C:\Users\lllg\Desktop\私有数据上传工具.bat
```

工具本体都在当前目录：

- `private-data-uploader.ps1`：可视化窗口。
- `scripts\upload-private-data.mjs`：实际上传脚本。
- `upload.config.json`：上传配置。
- `package.json` / `node_modules`：Node 运行依赖。

使用流程：

1. 双击桌面的 `私有数据上传工具.bat`。
2. 选择复盘日期文件夹，例如 `C:\Users\lllg\Desktop\复盘\5-12`。
3. 点击“预览”确认文件。
4. 点击“开始上传”执行上传。

账号密码配置可以继续从其他工具读取，但私有上传使用自己独立的登录态、锁文件和浏览器 profile：

```text
C:\Users\lllg\Desktop\上网电量抓取\config.json
当前工具目录\auth_state.json
当前工具目录\auth_state.json.lock
当前工具目录\.browser-profile-uploader
```
