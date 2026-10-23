"""Embedded, offline region price view for the existing analysis document."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def view_assets():
    return ((ROOT / "region_curves.css").read_text(encoding="utf-8"),
            (ROOT / "region_curves.js").read_text(encoding="utf-8"))


VIEW_HTML = """
<section id="regionCurveView" class="region-price" hidden aria-label="区域电价曲线">
  <header class="curve-heading"><div><h1>区域电价曲线</h1>
    <p>按大区、市、节点选择，比较同一份数据中的分时电价。</p></div>
    <button type="button" id="curveCopyLink">复制当前选择链接</button></header>
  <div class="curve-layout">
    <aside class="curve-picker" aria-label="大区市节点选择">
      <label for="curveSearch">搜索大区、市或节点</label>
      <input id="curveSearch" type="search" placeholder="例如：汕尾、铜鼓" autocomplete="off">
      <div class="curve-actions"><button type="button" id="curveAll">全选</button>
        <button type="button" id="curveClear">清空</button>
        <button type="button" id="curveMatches">勾选搜索结果</button></div>
      <p id="curveSelection" role="status" aria-live="polite"></p>
      <div id="curveTree"></div>
    </aside>
    <main class="curve-main">
      <div class="curve-controls"><label for="curveMode">显示方式</label>
        <select id="curveMode"><option value="nodes">各节点曲线</option>
          <option value="city">按市汇总（已选节点等权均价）</option>
          <option value="region">按大区汇总（已选节点等权均价）</option></select>
        <button type="button" id="curveExport">导出当前曲线 CSV</button></div>
      <p id="curveBasis">沿用原分析的同节点多母线逐时均价；单位：元/MWh。</p>
      <div class="curve-display-controls">
        <label><input id="curvePopupEnabled" type="checkbox" checked> 显示弹框</label>
        <label for="curveSort">价格读数排序</label>
        <select id="curveSort"><option value="time-desc">当前时点：高价在前</option>
          <option value="time-asc">当前时点：低价在前</option>
          <option value="mean-desc">日均价：高价在前</option>
          <option value="mean-asc">日均价：低价在前</option>
          <option value="name">按名称</option></select>
      </div>
      <div class="curve-legend-tools"><span id="curveVisibleCount"></span>
        <button type="button" id="curveShowAll">显示全部曲线</button>
        <button type="button" id="curveHideAll">隐藏全部曲线</button></div>
      <div id="curveLegend" class="curve-legend" aria-label="点击图例显示或隐藏曲线"></div>
      <p id="curveEmpty" role="status" hidden></p>
      <svg id="regionPriceChart" role="img" aria-label="所选节点分时电价曲线"></svg>
      <div class="curve-time"><label for="curveTime">时间：<strong id="curveTimeLabel"></strong></label>
        <input id="curveTime" type="range" min="0" value="0" aria-label="查看曲线时点"></div>
      <div id="curveValues" class="curve-values" aria-label="所选时点电价与有效节点数"></div>
      <p class="curve-note">市／大区汇总只计算已勾选节点：每个时点按有效节点等权平均，缺失值不按零计；无有效值时断开曲线。有效节点数变化时请结合读数比较。勾选上级会选择其全部节点。</p>
      <section class="curve-rankings" aria-label="节点日均价排序">
        <p class="curve-rank-note">节点日均价：按当前已勾选节点在文件中的有效时点算术平均，单位元/MWh。缺失时点不算零，无数据节点不参与排序；隐藏曲线不改变节点选择、汇总分母或排名。有效时点不足96个时，请勿视作完整日均价。条形中线为零，负价向左、正价向右。</p>
        <div class="curve-ranking-grid"><section><h2>节点日均价前10</h2><ol id="curveRankHigh"></ol></section>
          <section><h2>节点日均价倒数前10</h2><ol id="curveRankLow"></ol></section></div>
      </section>
    </main>
  </div>
</section>
<div id="curveTooltip" class="curve-tooltip" role="tooltip" hidden></div>
"""
