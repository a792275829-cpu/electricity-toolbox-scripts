电力工具箱目录说明

当前仓库仅维护 macOS 版。

统一入口：
- 双击“00_启动/电力工具箱.command”启动 Tk 工具箱。
- 双击“00_启动/源码调试版.command”调试当前源码，双击“00_启动/Tauri开发版.command”启动桌面开发版。
- 首次使用运行根目录 setup_macos.command。

原有 Python 业务脚本保留在各工具目录，使用根目录 .venv 执行；业务模块、模板和已有数据维持各自目录。各页面的输入、日志和运行状态互相独立，切换页面不会中断正在运行的任务。账号配置、独立登录态、浏览器 profile、缓存和输出不进入版本控制。

文档入口：
- 安装、启动、账号、输出位置、迁移、常见问题：根目录 README.md
- 界面与代码地图、新增工具步骤：电力工具箱/README.md
- 各工具的操作步骤与写入口径：对应工具目录的 README.md
- 维护与测试：tooling/README.md
- 桌面版架构与发布：desktop/README.md、packaging/tauri/README.md
