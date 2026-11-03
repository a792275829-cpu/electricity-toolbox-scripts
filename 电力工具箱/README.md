# 电力工具箱（Tk 界面层）

统一 GUI 入口，负责导航、页面、任务中心、账号区、运行诊断，并把用户操作转成各工具的命令行调用。业务实现不在本目录，见各自工具目录与 `toolbox_shared/`。

启动：`00_启动/电力工具箱.command`（源码）或已安装的 `电力工具箱.app`。调试用 `00_启动/源码调试版.command`，涉及 Tauri 生命周期时用 `00_启动/Tauri开发版.command`。

## 代码地图

```text
电力工具箱/
├── toolbox/
│   ├── catalog.py          # 15 个工具的 tool_id、名称、分组、页面类
│   ├── pages.py            # 大部分工具页面（ToolPage 子类）
│   ├── *_page.py           # 独立页面的工具（现货价格、交易系统、风力、500kV、日报、市场日报、功率收益、汇总上传）
│   ├── commands.py         # 页面参数 → 子进程命令行
│   ├── runtime.py          # ToolPaths（各工具脚本与输出路径）、load_module
│   ├── tasks/              # 进程执行、取消、进程组回收、任务事件
│   ├── workflows.py        # 组合流程（如抓取 → 生成报告）
│   ├── diagnostics.py      # 运行诊断检查项
│   ├── page_contracts.py   # 桌面服务用的字段契约
│   ├── service.py          # Tauri 业务服务
│   ├── ui/                 # 导航、任务中心、账号区、诊断面板、主题、视口
│   └── widgets.py          # ToolPage 等公共控件
└── tests/                  # 页面、导航、任务、业务契约测试
```

`toolbox_shared/` 是与 Tk 无关的公共实现（登录、凭据、存储路径、市场契约、WPS、交易报表等），由各工具和界面共同导入；根目录不再放实现模块。

## 新增一个工具

1. 在工具目录实现业务脚本，提供 `--login`、日期等命令行参数，输出写到自己的目录。
2. 在 `toolbox/catalog.py` 的 `default_catalog()` 注册 `ToolDescriptor`（`tool_id`、侧栏名称、分组、页面类）。
3. 在 `toolbox/pages.py` 或独立 `*_page.py` 写 `ToolPage` 子类：`tool_id` 与目录一致，声明标题、说明和按钮。
4. 在 `toolbox/commands.py` 里把页面参数拼成命令行，页面只负责收集参数与展示日志。
5. 需要桌面版字段契约时同步 `page_contracts.py` 与 `service_catalog.py`。
6. 补测试：页面创建与参数、命令拼装、失败提示；确认 `test_catalog_loading.py` 会遍历到新页面。
7. 更新根 README 的工具索引与模块 README。

## 约定

- 页面不直接执行业务，统一走 `run_process`／任务引擎；取消只能作用于本次启动的进程组。
- 页面不得跨工具共用登录态或浏览器 profile；账号读写统一走 `toolbox_shared.credentials`。
- 主线程不导入 openpyxl、NumPy、pandas、Playwright；这些只能在后台任务或子进程里加载。
- 日期输入统一用 `DateInput`，解析用 `toolbox_shared.dates`。

## 测试

```bash
.venv/bin/python tooling/run_tests.py --suite toolbox
```

逐页面的性能与历史记录见 [docs/performance](docs/performance/2026-10-10-page-loading.md)，历史设计与计划文档索引见 [docs/README.md](docs/README.md)。
