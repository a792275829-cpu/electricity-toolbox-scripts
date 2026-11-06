# 本机HTML接线图标注资源

状态：当前使用。入口为工具箱“500kV断面分析 → 浏览器编辑”，操作说明见模块README的“浏览器编辑”。

- `index.html`、`style.css`、`app.js` 由 `toolbox_shared/topology_web.py` 提供本机HTTP服务，不能通过file://打开。
- 节点库、Excel解析、同步、校验、简图生成、分析适配及保存复用 `toolbox_shared/section_topology.py`；底图导入、原图保存和带图备份由 `toolbox_shared/section_basemap.py` 管理，不引入Node依赖或外部CDN。
- 工作流为底图标点、核对连接、生成简图。节点 `map_anchor` 是底图像素坐标，`anchor` 为原有分析图坐标；`diagram_manual` 保护简图手动布局。底图切换须确认，旧简图与旧JSON继续兼容。
- `base_map` 保存原图尺寸及内容摘要，不保存用户机器的绝对路径。图片按摘要存入本机数据目录，HTTP读取需要会话令牌。PDF直接提取单页整页图片的原始JPEG／PNG；其他类型先导出为图片。
- 原有线路的 `reviewed` 默认为未确认，新连接及核对均经确认框提交。生成及分析过滤未标注、未确认的关系；多价格变体只共享物理标点，不复制电气连接。
- JSON备份携带 `base_map_image`，导入时校验图片摘要及尺寸，图片单独落盘，草稿不重复保存图片编码。并行保存继续使用原有版本检查。
- 网页基础字号17px、节点库加宽、属性区按需展开。简图全图圆点直径16px、站名字号18px、普通线宽2px，随后按相对缩放比例同步变化。标点阶段不铺满新线路，底图按原始分辨率缩放，叠加标记尺寸受限以免遮挡。
- `test_topology_web.py` 覆盖旧简图及触摸板事件；`test_topology_annotation.py` 覆盖标点、核对、独立布局、带图备份、每日同步和应用回读。测试浏览器独立无界面，不控制用户浏览器。
