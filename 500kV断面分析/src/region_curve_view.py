"""Embedded, offline region price view for the existing analysis document."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def view_assets():
    return ((ROOT / "region_curves.css").read_text(encoding="utf-8"),
            (ROOT / "region_curves.js").read_text(encoding="utf-8"))


VIEW_HTML = """
<section id="regionCurveView" class="region-price" hidden aria-label="区域电价曲线">
  <div class="curve-layout">
    <main class="curve-main">
      <div class="curve-controls">
        <label class="curve-popup-toggle"><input id="curvePopupEnabled" type="checkbox" checked> 显示弹框</label>
        <button type="button" id="curveExport">导出当前曲线</button></div>
      <div class="curve-content">
      <div id="curveLegend" class="curve-legend" aria-label="点击图例显示或隐藏曲线"></div>
      <p id="curveEmpty" role="status" hidden></p>
      <svg id="regionPriceChart" role="img" aria-label="所选节点分时电价曲线"></svg>
      <section class="curve-rankings" aria-label="节点日均价排序">
        <div class="curve-rank-heading"><h2 id="curveRankTitle">节点日均价前10（元/MWh）</h2><button type="button" id="curveRankReverse" aria-pressed="false">切换倒序</button></div>
        <div class="curve-rank-scroll"><ol id="curveRankList" aria-label="两排展示，第一排1至5名，第二排6至10名，每排从左到右排列"></ol></div>
      </section>
      </div>
    </main>
    <aside class="curve-picker" aria-label="大区市节点选择">
      <div class="curve-search-box"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg>
        <input id="curveSearch" type="search" aria-label="搜索大区、市或节点" placeholder="搜索大区、市或节点" autocomplete="off"></div>
      <div id="curveTree"></div>
    </aside>
  </div>
</section>
<div id="curveTooltip" class="curve-tooltip" role="tooltip" hidden></div>
"""
