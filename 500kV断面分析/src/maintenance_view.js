// Maintenance colors and hover details on the existing SVG; price rules stay independent.
(() => {
  const maintenance = DATA.maintenance;
  if (!maintenance) return;
  const panel = document.createElement('details');
  panel.id = 'maintenancePanel';
  panel.className = 'maintenance-panel';
  const summary = document.createElement('summary');
  summary.textContent = `检修 ${maintenance.matched_edges} 条边 · ${maintenance.matched_records} 条记录`;
  panel.appendChild(summary);
  const description = document.createElement('p');
  description.textContent = `运行日 ${maintenance.run_date} / 对比日 ${maintenance.compare_date}。黑色表示合并边含检修回路，不表示所有回路停运。检修名称和开始时间平时隐藏，移到线路或节点上时与电价一起显示。`;
  panel.appendChild(description);
  const list = document.createElement('ul');
  DATA.edges.filter(edge => edge.maintenance?.length).forEach(edge => {
    const line = edgeLines.get(edge.edge);
    line.classList.add('maintenance');
    const hit = edgeHitLines.get(edge.edge);
    const details = edge.maintenance.map(record => `${record.elementName}，${record.startDate} 起`).join('；');
    hit.setAttribute('aria-label', `${hit.getAttribute('aria-label')}，${details}`);
    [line, hit, flowArrowItems.get(edge.edge)?.group].filter(Boolean).forEach(element =>
      bindNodePriceTooltip(element, nodeByName.get(edge.a), edge));
    const item = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = `${edge.edge}：${edge.maintenance.map(record => record.label.split(' · ')[0]).join('；')}`;
    bindNodePriceTooltip(button, nodeByName.get(edge.a), edge);
    button.addEventListener('click', () => selectEdge(edge, line));
    item.appendChild(button);
    list.appendChild(item);
  });
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
})();
