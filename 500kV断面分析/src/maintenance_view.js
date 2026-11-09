// One annotation layer on the existing SVG; price/congestion state stays independent.
(() => {
  const maintenance = DATA.maintenance;
  if (!maintenance) return;
  const ns = 'http://www.w3.org/2000/svg';
  const layer = document.createElementNS(ns, 'g');
  layer.id = 'maintenanceLabels';
  layer.setAttribute('aria-label', '500kV线路检修与开始日期');
  document.getElementById('mapViewport').appendChild(layer);
  const panel = document.createElement('details');
  panel.id = 'maintenancePanel';
  panel.className = 'maintenance-panel';
  const summary = document.createElement('summary');
  summary.textContent = `检修 ${maintenance.matched_edges} 条边 · ${maintenance.matched_records} 条记录`;
  panel.appendChild(summary);
  const description = document.createElement('p');
  description.textContent = `运行日 ${maintenance.run_date} / 对比日 ${maintenance.compare_date}。黑色表示合并边含检修回路，具体回路及开始时间见线旁标签；不表示所有回路停运。`;
  panel.appendChild(description);
  const list = document.createElement('ul');
  const annotations = [];
  DATA.edges.filter(edge => edge.maintenance?.length).forEach(edge => {
    const line = edgeLines.get(edge.edge);
    line.classList.add('maintenance');
    const title = document.createElementNS(ns, 'title');
    title.textContent = edge.maintenance.map(record => `${record.elementName}，${record.startDate} 起`).join('\n');
    line.appendChild(title);
    const hit = edgeHitLines.get(edge.edge);
    hit.setAttribute('aria-label', `${hit.getAttribute('aria-label')}，${title.textContent}`);
    const label = document.createElementNS(ns, 'text');
    label.setAttribute('class', 'maintenance-label');
    label.dataset.edge = edge.edge;
    // Stagger multi-record labels along the perpendicular, rather than overprinting them.
    edge.maintenance.forEach((record, index) => {
      const text = document.createElementNS(ns, 'tspan');
      text.setAttribute('x', 0);
      text.setAttribute('y', index * 14);
      text.textContent = record.label;
      label.appendChild(text);
    });
    layer.appendChild(label);
    annotations.push({edge,label});
    const item = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = `${edge.edge}：${edge.maintenance.map(record => record.label).join('；')}`;
    button.addEventListener('click', () => selectEdge(edge, line));
    item.appendChild(button);
    list.appendChild(item);
  });
  function layoutAnnotations() {
    const obstacles = [...document.querySelectorAll('#nodeLabels .label')].map(element => {
      const box = element.getBBox();
      const matrix = mapViewport.getCTM().inverse().multiply(element.getCTM());
      const a = new DOMPoint(box.x, box.y).matrixTransform(matrix);
      const b = new DOMPoint(box.x + box.width, box.y + box.height).matrixTransform(matrix);
      return {x: a.x - 12, y: a.y - 12, width: b.x - a.x + 24, height: b.y - a.y + 24};
    });
    const overlaps = (a,b) => a.x < b.x+b.width && a.x+a.width > b.x && a.y < b.y+b.height && a.y+a.height > b.y;
    annotations.forEach(item => {
      const {edge,label} = item;
      const box = label.getBBox();
      const mx = (edge.x1 + edge.x2)/2, my = (edge.y1 + edge.y2)/2;
      const length = Math.max(1, Math.hypot(edge.x2-edge.x1, edge.y2-edge.y1));
      const nx = -(edge.y2-edge.y1)/length, ny = (edge.x2-edge.x1)/length;
      const candidates = [];
      for (const offset of [-18, 18, -36, 36, -58, 58, -82, 82, -112, 112]) {
        for (const along of [0, -24, 24, -48, 48]) {
          for (const shift of [0, -.6*box.width, .6*box.width]) {
            const x = mx + nx*offset + (edge.x2-edge.x1)/length*along + shift;
            const y = my + ny*offset + (edge.y2-edge.y1)/length*along;
            const bounds = {x:x+box.x-4, y:y+box.y-4, width:box.width+8, height:box.height+8};
            const collisions = obstacles.filter(other => overlaps(bounds,other)).length;
            const outside = bounds.x < 0 || bounds.x+bounds.width > DATA.metadata.layout_canvas_width ||
              bounds.y < 0 || bounds.y+bounds.height > DATA.metadata.layout_canvas_height;
            candidates.push({x,y,bounds,score:collisions*10000+(outside?100000:0)+Math.abs(offset)+Math.abs(along)+Math.abs(shift)});
          }
        }
      }
      const best = candidates.sort((a,b)=>a.score-b.score)[0];
      label.setAttribute('transform', `translate(${best.x} ${best.y})`);
      obstacles.push(best.bounds);
      const leader = item.leader || (item.leader = document.createElementNS(ns, 'line'));
      leader.setAttribute('class', 'maintenance-leader');
      leader.setAttribute('x1', mx);leader.setAttribute('y1', my);
      leader.setAttribute('x2', Math.max(best.bounds.x,Math.min(mx,best.bounds.x+best.bounds.width)));
      leader.setAttribute('y2', Math.max(best.bounds.y,Math.min(my,best.bounds.y+best.bounds.height)));
      layer.insertBefore(leader,label);
    });
  }
  layoutAnnotations();
  let layoutFrame;
  const observer = new MutationObserver(() => {
    cancelAnimationFrame(layoutFrame);
    layoutFrame = requestAnimationFrame(layoutAnnotations);
  });
  observer.observe(document.getElementById("nodeLabels"), {subtree:true, attributes:true, childList:true});
  document.fonts?.ready.then(layoutAnnotations);
  panel.appendChild(list);
  const addRecords = (title, records) => {
    if (!records?.length) return;
    const heading = document.createElement('p');
    heading.textContent = `${title}（${records.length}）`;
    panel.appendChild(heading);
    const ul = document.createElement('ul');
    records.forEach(record => {
      const li = document.createElement('li');
      li.textContent = `${record.elementName} · ${record.startDate} 起`;
      ul.appendChild(li);
    });
    panel.appendChild(ul);
  };
  addRecords('未匹配到图中边，未标黑', maintenance.unmatched);
  addRecords('其他设备检修，另列', maintenance.separate);
  addRecords('对比日有、运行日已减少，不标黑', maintenance.removed);
  const source = document.createElement('p');
  source.className = 'maintenance-date';
  source.textContent = '来源：披露信息对比 · 输变电设备检修。结束日期保留接口原值，不据此推断实际复电时刻。';
  panel.appendChild(source);
  document.querySelector('.side').prepend(panel);
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'toggle';
  toggle.id = 'toggleMaintenance';
  toggle.textContent = '检修标签';
  toggle.setAttribute('aria-pressed', 'true');
  toggle.setAttribute('aria-controls', layer.id);
  toggle.addEventListener('click', () => {
    const visible = toggle.getAttribute('aria-pressed') !== 'true';
    toggle.setAttribute('aria-pressed', String(visible));
    layer.toggleAttribute('hidden', !visible);
  });
  document.querySelector('.zoom-controls').prepend(toggle);
})();
