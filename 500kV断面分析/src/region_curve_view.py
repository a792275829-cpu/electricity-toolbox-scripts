"""Embedded, offline region price view for the existing analysis document."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def view_assets():
    return ((ROOT / "region_curves.css").read_text(encoding="utf-8"),
            (ROOT / "region_curves.js").read_text(encoding="utf-8"))


VIEW_HTML = """
<section id="regionCurveView" class="region-price" hidden aria-label="区域电价曲线">
  <div class="curve-layout">
    <aside class="curve-picker" aria-label="大区市节点选择">
      <div class="curve-search-box"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg>
        <input id="curveSearch" type="search" aria-label="搜索大区、市或节点" placeholder="搜索大区、市或节点" autocomplete="off"></div>
      <p id="curveSelection" role="status" aria-live="polite"></p>
      <div id="curveTree"></div>
    </aside>
    <main class="curve-main">
      <div class="curve-controls"><label for="curveMode">显示方式</label>
        <select id="curveMode"><option value="nodes">各节点曲线</option>
          <option value="city">按市汇总（已选节点等权均价）</option>
          <option value="region">按大区汇总（已选节点等权均价）</option></select>
        <button type="button" id="curveExport">导出当前曲线 CSV</button>
        <label class="curve-popup-toggle"><input id="curvePopupEnabled" type="checkbox" checked> 显示弹框</label></div>
      <div class="curve-legend-tools"><span id="curveVisibleCount"></span>
        <button type="button" id="curveShowAll">显示全部曲线</button>
        <button type="button" id="curveHideAll">隐藏全部曲线</button></div>
      <div id="curveLegend" class="curve-legend" aria-label="点击图例显示或隐藏曲线"></div>
      <p id="curveEmpty" role="status" hidden></p>
      <svg id="regionPriceChart" role="img" aria-label="所选节点分时电价曲线"></svg>
      <div class="curve-time"><label for="curveTime">时间</label>
        <div class="time-slider-track"><input id="curveTime" type="range" min="0" value="0" aria-label="查看曲线时点" class="time-slider">
          <output id="curveTimeLabel" for="curveTime" class="time-slider-readout"></output>
          <div id="curveTimeTicks" class="time-slider-ticks" aria-hidden="true"></div></div></div>
      <section class="curve-rankings" aria-label="节点日均价排序">
        <div class="curve-ranking-grid"><section><div class="curve-rank-heading"><h2 id="curveRankHighTitle">节点日均价前10</h2><button type="button" id="curveRankReverse" aria-pressed="false">切换倒序</button></div>
          <div class="curve-rank-columns" aria-hidden="true"><span>节点</span><span>日均价（元/MWh）</span><span>有效时点</span></div><ol id="curveRankHigh"></ol></section>
          <section><div class="curve-rank-heading"><h2 id="curveRankLowTitle">节点日均价倒数前10</h2></div>
          <div class="curve-rank-columns" aria-hidden="true"><span>节点</span><span>日均价（元/MWh）</span><span>有效时点</span></div><ol id="curveRankLow"></ol></section></div>
      </section>
    </main>
  </div>
</section>
<div id="curveTooltip" class="curve-tooltip" role="tooltip" hidden></div>
"""
