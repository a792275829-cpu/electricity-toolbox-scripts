# WPS 写入工具响应式按钮布局设计

## 问题

`WpsWriterFrame` 的 Document configs 工具栏将十个按钮固定在同一行。工具嵌入统一工作台后，侧栏和任务中心压缩了页面宽度；Tk `pack` 不会自动换行，因此部分按钮被裁切。

## 设计

- 工具栏改为上下两行，始终占满可用宽度。
- 第一行放置配置编辑操作：Add config、Edit、Copy、Remove、Move up、Move down。
- 第二行左侧放置 Import、Export，右侧放置 Preview、Write to WPS。
- 两行分别使用独立容器，保持当前按钮命令和文字不变。
- 不增加横向滚动，不把功能藏入下拉菜单。

## 验收

- 在宽度 520 像素的内嵌窗口中，十个按钮均完成布局且位于工具栏可见范围内。
- Document configs 继续保持不低于现有最小高度。
- 配置导入、导出和 WPS 写入逻辑不变。
- WPS 布局测试和工具箱完整测试通过。
