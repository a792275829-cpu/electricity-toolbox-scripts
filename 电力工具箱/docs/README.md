# 电力工具箱文档索引

这里只放**历史记录**（设计、计划、性能测量）。描述当前行为的文档是：

- 界面与代码地图：[../README.md](../README.md)
- 工具用法：各工具目录的 `README.md`，根 README 有索引
- 维护与测试入口：[../../tooling/README.md](../../tooling/README.md)
- 桌面版架构：[../../desktop/docs/architecture.md](../../desktop/docs/architecture.md)

## 性能测量

| 文档 | 内容 | 状态 |
| --- | --- | --- |
| [performance/2026-07-10-startup.md](performance/2026-07-10-startup.md) | 启动耗时基线与优化 | 历史，数字为当时结果 |
| [performance/2026-10-10-page-loading.md](performance/2026-10-10-page-loading.md) | 页面首次加载与切换重绘 | 历史，当时 14 个导航模块 |

## 设计与计划

| 文档 | 内容 | 状态 |
| --- | --- | --- |
| [superpowers/specs/2026-07-10-toolbox-platform-optimization-design.md](superpowers/specs/2026-07-10-toolbox-platform-optimization-design.md) | 平台化与进程隔离设计 | 历史设计，结论已落到 `toolbox/tasks/`、`service.py` |
| [superpowers/specs/2026-07-11-wps-responsive-toolbar-design.md](superpowers/specs/2026-07-11-wps-responsive-toolbar-design.md) | WPS 工具栏响应式布局 | 历史设计 |
| [superpowers/plans/2026-07-10-toolbox-stability-foundation.md](superpowers/plans/2026-07-10-toolbox-stability-foundation.md) | 稳定性改造分阶段计划 | 历史计划，不作为当前规范 |
| [superpowers/plans/2026-07-11-wps-responsive-toolbar.md](superpowers/plans/2026-07-11-wps-responsive-toolbar.md) | WPS 工具栏实施步骤 | 历史计划 |

阅读历史文档时以代码和当前模块 README 为准：其中的文件路径、模块数量、启动命令可能已经过时。
