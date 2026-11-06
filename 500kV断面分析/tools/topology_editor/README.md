# 本机HTML拓扑编辑资源

状态：当前使用。入口为工具箱“500kV断面分析 → 浏览器编辑”。

`index.html`、`style.css`和`app.js`由`toolbox_shared/topology_web.py`提供本机HTTP服务，不能直接以file://打开。节点库、Excel解析、同步、校验和保存复用`toolbox_shared/section_topology.py`。用户操作说明见模块README的“浏览器版与触摸板缩放”。
