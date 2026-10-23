/* Embedded in the offline analysis HTML. No network or chart library required. */
(() => {
  'use strict';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const searchKey = value => String(value).replace(/\s+/g,'').toLowerCase();
  function aggregateSeries(nodes, selected, mode, count) {
    const chosen = nodes.filter(n => selected.has(n.name));
    const groups = new Map();
    chosen.forEach(node => {
      const key = mode === 'region' ? node.region : mode === 'city' ? node.city : node.name;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(node);
    });
    return [...groups].map(([name, members]) => {
      const counts = [], prices = [];
      for (let i = 0; i < count; i++) {
        const values = members.map(n => n.prices[i]).filter(finite);
        counts.push(values.length);
        prices.push(values.length ? values.reduce((a, b) => a + b, 0) / values.length : null);
      }
      return {name, members:members.map(n => n.name), total:members.length, prices, counts};
    });
  }
  function linePath(prices, x, y) {
    let joined = false;
    return prices.map((value, index) => {
      if (!finite(value)) { joined = false; return ''; }
      const command = joined ? 'L' : 'M';
      joined = true;
      return `${command}${x(index).toFixed(2)},${y(value).toFixed(2)}`;
    }).filter(Boolean).join(' ');
  }
  function meanPrice(prices) {
    const values=prices.filter(finite);
    return values.length ? values.reduce((a,b) => a+b,0)/values.length : null;
  }
  function sortReadings(items, index, order) {
    return [...items].sort((a,b) => {
      if (order==='name') return a.name.localeCompare(b.name,'zh-CN');
      const av=order.startsWith('mean') ? meanPrice(a.prices) : a.prices[index];
      const bv=order.startsWith('mean') ? meanPrice(b.prices) : b.prices[index];
      if (finite(av)!==finite(bv)) return finite(av) ? -1 : 1;
      if (finite(av) && av!==bv) return (order.endsWith('asc') ? 1 : -1)*(av-bv);
      return a.name.localeCompare(b.name,'zh-CN');
    });
  }
  function rankNodes(nodes, selected, descending=true, limit=10) {
    return nodes.filter(n => selected.has(n.name)).map(n => ({name:n.name,mean:meanPrice(n.prices),
      count:n.prices.filter(finite).length,total:n.prices.length})).filter(n => finite(n.mean))
      .sort((a,b) => (descending ? b.mean-a.mean : a.mean-b.mean) || a.name.localeCompare(b.name,'zh-CN')).slice(0,limit);
  }
  function rankingScale(values) {
    const valid=values.filter(finite),min=Math.min(0,...valid),max=Math.max(0,...valid);
    const span=max-min || 1,zero=min<0 ? -min/span*100 : 0;
    return {zero,mixed:min<0 && max>0,bar:value => {
      const point=(value-min)/span*100;
      return {left:Math.min(point,zero),width:Math.abs(point-zero)};
    }};
  }
  globalThis.RegionPriceCurves = {aggregateSeries, linePath, meanPrice, sortReadings, rankNodes, rankingScale};
  if (typeof document === 'undefined') return;
  const model = DATA.curve_data;
  if (!model) return;
  const $ = id => document.getElementById(id);
  const panel = $('regionCurveView'), mapPanel = document.querySelector('.app');
  const nodes = model.nodes, times = DATA.time_headers;
  const byName = new Map(nodes.map(n => [n.name, n]));
  const selected = new Set();
  const hiddenSeries = new Set();
  const controls = [];
  let series = [], currentIndex = 0, guide = null, chartX = null, chartY = null, markerLayer = null;
  let lastPointer = null, tooltipKey = null;
  const colors = ['#1261d1','#c23725','#13836c','#9254a8','#a56900','#007d99','#a53575','#456437','#6258b4','#9b4f17','#32738e','#77616d'];
  const styleNames = {
    nodes:nodes.map(n => n.name), city:model.regions.flatMap(r => r.cities.map(c => c.name)),
    region:model.regions.map(r => r.name)
  };
  const styleIndex = name => Math.max(0,styleNames[$('curveMode').value].indexOf(name));
  const color = name => colors[styleIndex(name) % colors.length];
  const dash = name => ['', '7 3', '2 3', '10 3 2 3'][Math.floor(styleIndex(name)/colors.length)%4];
  const seriesKey = name => `${$('curveMode').value}:${name}`;
  const visibleSeries = () => series.filter(item => !hiddenSeries.has(seriesKey(item.name)));
  const available = name => (byName.get(name)?.prices || []).some(finite);
  const make = (tag, attributes={}, text='') => {
    const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attributes).forEach(([key,value]) => element.setAttribute(key,value));
    element.textContent = text;
    return element;
  };
  function restore() {
    selected.clear();
    const hash = location.hash.slice(1), params = new URLSearchParams(hash.split('?')[1] || '');
    const names = params.has('nodes') ? params.get('nodes').split('|') :
      (model.regions[0]?.cities[0]?.nodes || []).filter(available);
    names.filter(name => byName.has(name)).forEach(name => selected.add(name));
    $('curveMode').value = ['nodes','city','region'].includes(params.get('mode')) ? params.get('mode') : 'nodes';
    $('curveSort').value = ['time-desc','time-asc','mean-desc','mean-asc','name'].includes(params.get('sort')) ? params.get('sort') : 'time-desc';
    $('curvePopupEnabled').checked=params.get('popup')!=='0';
    hiddenSeries.clear();
    try {
      const hidden=JSON.parse(params.get('hidden') || '[]');
      if (Array.isArray(hidden)) hidden.forEach(key => {
        if (typeof key!=='string') return;
        const at=key.indexOf(':'),mode=key.slice(0,at),name=key.slice(at+1);
        if (styleNames[mode]?.includes(name)) hiddenSeries.add(key);
      });
    } catch (_) { /* Ignore malformed saved visibility state. */ }
    return hash.startsWith('curves');
  }
  function save() {
    const params = new URLSearchParams({mode:$('curveMode').value,nodes:[...selected].join('|'),
      sort:$('curveSort').value,popup:$('curvePopupEnabled').checked ? '1' : '0',hidden:JSON.stringify([...hiddenSeries])});
    try { history.replaceState(null,'',`#curves?${params}`); } catch (_) { /* File viewers may restrict history. */ }
  }
  function switchView(curves) {
    hideTooltip();
    panel.hidden = !curves; mapPanel.hidden = curves;
    $('showRegionCurves').setAttribute('aria-pressed',String(curves));
    $('showTopology').setAttribute('aria-pressed',String(!curves));
    if (curves) { save(); draw(); } else {
      try { history.replaceState(null,'',location.pathname + location.search + '#topology'); } catch (_) {}
    }
  }
  function checkbox(names, label) {
    const input = document.createElement('input'); input.type='checkbox'; input.setAttribute('aria-label',label);
    controls.push({input,names});
    input.addEventListener('click',event => event.stopPropagation());
    input.addEventListener('change',() => {
      names.forEach(name => input.checked ? selected.add(name) : selected.delete(name));
      update();
    });
    return input;
  }
  function renderTree() {
    const tree = $('curveTree'); tree.replaceChildren(); controls.length=0;
    const term = searchKey($('curveSearch').value);
    let matches = 0;
    model.regions.forEach(region => {
      const regionMatches = region.cities.map(city => ({...city, nodes:city.nodes.filter(name =>
        !term || searchKey(`${region.name} ${city.name} ${name}`).includes(term))})).filter(c => c.nodes.length);
      if (!regionMatches.length) return;
      const group = document.createElement('details'); group.open=true;
      const heading = document.createElement('summary');
      const all = region.cities.flatMap(c => c.nodes);
      heading.append(checkbox(all,`${region.name}全选`),document.createTextNode(`${region.name} · ${all.length}`));
      group.append(heading);
      regionMatches.forEach(city => {
        const details=document.createElement('details'); details.className='curve-city';
        details.open=Boolean(term) || city.nodes.some(n => selected.has(n));
        const title=document.createElement('summary');
        const wholeCity = region.cities.find(c => c.name===city.name).nodes;
        title.append(checkbox(wholeCity,`${city.name}全选`),document.createTextNode(`${city.name} · ${wholeCity.length}`));
        details.append(title);
        city.nodes.forEach(name => {
          matches++;
          const label=document.createElement('label'); label.className='curve-node';
          label.append(checkbox([name],name),document.createTextNode(name));
          if (!available(name)) {
            const note=document.createElement('span'); note.className='curve-unavailable'; note.textContent='暂无电价'; label.append(note);
          }
          details.append(label);
        });
        group.append(details);
      });
      tree.append(group);
    });
    if (!matches) tree.textContent='没有匹配的节点';
    syncChecks();
  }
  function syncChecks() {
    controls.forEach(({input,names}) => {
      const count=names.filter(n => selected.has(n)).length;
      input.checked=names.length>0 && count===names.length;
      input.indeterminate=count>0 && count<names.length;
    });
    const valid=[...selected].filter(available).length;
    $('curveSelection').textContent=`已选 ${selected.size} / ${nodes.length} 个节点，其中 ${valid} 个有电价`;
  }
  function hideTooltip() { $('curveTooltip').hidden=true; lastPointer=null; tooltipKey=null; }
  function showTooltip(clientX, clientY) {
    if (!$('curvePopupEnabled').checked || panel.hidden || !visibleSeries().length) return;
    const tooltip=$('curveTooltip');
    const key=`${currentIndex}:${$('curveSort').value}`;
    if (tooltipKey!==key || tooltip.hidden) {
      tooltip.replaceChildren();
      const title=document.createElement('h3'); title.textContent=`${times[currentIndex] || '无时点'} · 电价（元/MWh）`; tooltip.append(title);
      const items=sortReadings(visibleSeries(),currentIndex,$('curveSort').value);
      const limit=Math.max(1,Math.min(12,Math.floor((innerHeight-100)/24)));
      items.slice(0,limit).forEach(item => {
        const row=document.createElement('div');row.className='curve-tooltip-row';
        const label=document.createElement('span'),swatch=document.createElement('i');
        swatch.className='curve-swatch';swatch.style.borderColor=color(item.name);
        const name=$('curveMode').value==='nodes' ? item.name : `${item.name}（${item.counts[currentIndex]}/${item.total} 节点有效）`;
        label.append(swatch,document.createTextNode(name));
        const value=document.createElement('b'),price=item.prices[currentIndex];
        value.textContent=finite(price) ? price.toFixed(4) : '暂无数据';row.append(label,value);tooltip.append(row);
      });
      const note=document.createElement('small');
      note.textContent=($('curveSort').value.startsWith('mean') ? '顺序按日均价，数值为当前时点价格。' : '') +
        (items.length>limit ? `另有 ${items.length-limit} 条曲线，可缩小选择范围查看。` : '');
      if (note.textContent) tooltip.append(note);
      tooltipKey=key;
    }
    tooltip.hidden=false;
    const box=tooltip.getBoundingClientRect();
    const left=clientX+16+box.width<innerWidth-12 ? clientX+16 : clientX-box.width-16;
    tooltip.style.left=`${Math.max(12,Math.min(innerWidth-box.width-12,left))}px`;
    tooltip.style.top=`${Math.max(12,Math.min(innerHeight-box.height-12,clientY+14))}px`;
    lastPointer={x:clientX,y:clientY};
  }
  function renderLegend() {
    const legend=$('curveLegend'),focus=document.activeElement?.dataset.series;
    legend.replaceChildren();
    series.forEach(item => {
      const button=document.createElement('button');button.type='button';button.dataset.series=item.name;
      const visible=!hiddenSeries.has(seriesKey(item.name));
      button.setAttribute('aria-pressed',String(visible));button.setAttribute('aria-label',`${item.name}曲线`);
      button.title=visible ? '点击隐藏曲线（保留节点选择）' : '点击显示曲线';
      const swatch=document.createElement('i');swatch.className='curve-swatch';swatch.style.borderColor=color(item.name);
      if (dash(item.name)) swatch.style.borderTopStyle='dashed';
      button.append(swatch,document.createTextNode(item.name));
      button.addEventListener('click',() => {
        const key=seriesKey(item.name);hiddenSeries.has(key) ? hiddenSeries.delete(key) : hiddenSeries.add(key);
        save();draw();
      });
      legend.append(button);
    });
    $('curveVisibleCount').textContent=`显示 ${visibleSeries().length} / ${series.length} 条曲线 · 点击图例切换`;
    if (focus) [...legend.children].find(b => b.dataset.series===focus)?.focus({preventScroll:true});
  }
  function renderRankings() {
    const high=rankNodes(nodes,selected),low=rankNodes(nodes,selected,false);
    const scale=rankingScale(high.concat(low).map(n => n.mean));
    [[high,'curveRankHigh'],[low,'curveRankLow']].forEach(([items,id]) => {
      const list=$(id);list.replaceChildren();
      if (!items.length) {const empty=document.createElement('li');empty.textContent='所选节点暂无有效电价';list.append(empty);}
      items.forEach((item,index) => {
        const row=document.createElement('li');row.className='curve-rank-row';row.dataset.node=item.name;
        const label=document.createElement('div');label.className='curve-rank-label';
        const name=document.createElement('span');name.textContent=`${index+1}. ${item.name}`;
        const value=document.createElement('b');value.textContent=item.mean.toFixed(4);
        const count=document.createElement('small');count.textContent=`${item.count}/${item.total} 时点`;
        label.append(name,value,count);
        const track=document.createElement('div'),bar=document.createElement('span');
        track.className='curve-rank-track';bar.className=`curve-rank-bar${item.mean<0 ? ' is-negative' : ''}`;
        const geometry=scale.bar(item.mean);bar.style.width=`${geometry.width}%`;bar.style.left=`${geometry.left}%`;
        track.style.setProperty('--rank-zero',`${scale.zero}%`);track.classList.toggle('has-zero',scale.mixed);
        track.append(bar);row.append(label,track);list.append(row);
      });
    });
  }
  function showTime(index) {
    currentIndex=Math.max(0,Math.min(times.length-1,index));
    $('curveTime').value=String(currentIndex);
    $('curveTimeLabel').textContent=times[currentIndex] || '无时点';
    $('curveTime').setAttribute('aria-valuetext',times[currentIndex] || '无时点');
    if (guide && chartX) { guide.setAttribute('x1',chartX(currentIndex)); guide.setAttribute('x2',chartX(currentIndex)); }
    if (markerLayer && chartY) {
      markerLayer.replaceChildren();
      visibleSeries().forEach(item => {
        const value=item.prices[currentIndex];
        if (finite(value)) markerLayer.append(make('circle',{cx:chartX(currentIndex),cy:chartY(value),r:4,
          fill:'var(--panel)',stroke:color(item.name),'stroke-width':2}));
      });
    }
    if (lastPointer) showTooltip(lastPointer.x,lastPointer.y);
  }
  function draw() {
    if (panel.hidden) return;
    hideTooltip();
    const svg=$('regionPriceChart'); svg.replaceChildren(); guide=null;markerLayer=null;
    series=aggregateSeries(nodes,selected,$('curveMode').value,times.length);
    renderLegend();renderRankings();
    const visible=visibleSeries();
    const all=visible.flatMap(s => s.prices.filter(finite));
    $('curveEmpty').hidden=all.length>0;
    $('curveEmpty').textContent=!selected.size ? '请从左侧勾选大区、市或节点。' : !visible.length ?
      '所有曲线已隐藏，可点击图例或“显示全部曲线”恢复。' : '所选节点在这份数据中暂无有效电价，请更换节点或数据文件。';
    svg.hidden=!all.length; $('curveExport').disabled=!all.length;
    const source=String(DATA.metadata.original_path || '').split(/[\\/]/).pop();
    $('curveBasis').textContent=($('curveMode').value==='nodes' ? '同一节点多母线沿用原分析的逐时均价。' : '仅对已选节点逐时等权平均，分母为当时点有效节点数。') + ` 单位：元/MWh。来源：${source}`;
    $('curveTime').max=String(Math.max(0,times.length-1)); $('curveTime').disabled=!all.length;
    if (!all.length) { showTime(currentIndex); return; }
    const width=Math.max(340,Math.round(svg.parentElement.clientWidth-36)), height=360;
    svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
    const margin={left:62,right:18,top:26,bottom:38};
    const lo=Math.min(...all),hi=Math.max(...all),pad=Math.max((hi-lo)*.08,1);
    const min=lo-pad,max=hi+pad;
    const x=i => margin.left + i/Math.max(times.length-1,1)*(width-margin.left-margin.right);
    const y=v => margin.top+(max-v)/(max-min)*(height-margin.top-margin.bottom);
    chartX=x;chartY=y;
    for (let step=0;step<=4;step++) {
      const v=max-step/4*(max-min),py=y(v);
      svg.append(make('line',{x1:margin.left,y1:py,x2:width-margin.right,y2:py,stroke:'var(--line)'}));
      svg.append(make('text',{x:margin.left-9,y:py+4,'text-anchor':'end','font-size':12,fill:'var(--muted)'},v.toFixed(1)));
    }
    svg.append(make('text',{x:margin.left,y:15,'font-size':12,fill:'var(--muted)'},'元/MWh'));
    [...new Set([0,Math.round((times.length-1)/4),Math.round((times.length-1)/2),Math.round((times.length-1)*3/4),times.length-1])].forEach(i => {
      svg.append(make('text',{x:x(i),y:height-10,'text-anchor':'middle','font-size':12,fill:'var(--muted)'},times[i] || ''));
    });
    visible.forEach(item => {
      const path=make('path',{d:linePath(item.prices,x,y),fill:'none',stroke:color(item.name),'stroke-width':2,
        'stroke-dasharray':dash(item.name),'data-series':item.name});
      path.append(make('title',{},item.name)); svg.append(path);
      item.prices.forEach((v,j) => {
        if (finite(v) && !finite(item.prices[j-1]) && !finite(item.prices[j+1]))
          svg.append(make('circle',{cx:x(j),cy:y(v),r:3,fill:color(item.name)}));
      });
    });
    guide=make('line',{y1:margin.top,y2:height-margin.bottom,stroke:'var(--muted)','stroke-dasharray':'4 4','pointer-events':'none'});
    svg.append(guide);
    markerLayer=make('g',{'pointer-events':'none'});svg.append(markerLayer);
    const hit=make('rect',{x:margin.left,y:margin.top,width:width-margin.left-margin.right,height:height-margin.top-margin.bottom,fill:'transparent'});
    function point(event) {
      const b=svg.getBoundingClientRect(),local=(event.clientX-b.left)/b.width*width;
      const index=Math.max(0,Math.min(times.length-1,Math.round((local-margin.left)/(width-margin.left-margin.right)*Math.max(times.length-1,1))));
      if (index!==currentIndex) {lastPointer=null;showTime(index);}
      showTooltip(event.clientX,event.clientY);
    }
    hit.addEventListener('pointermove',point);hit.addEventListener('pointerdown',point);hit.addEventListener('pointerleave',hideTooltip);svg.append(hit);
    showTime(currentIndex);
  }
  function update() { syncChecks(); save(); draw(); }
  $('curveSearch').addEventListener('input',renderTree);
  $('curveMode').addEventListener('change',update);
  $('curveSort').addEventListener('change',() => {save();showTime(currentIndex);});
  $('curvePopupEnabled').addEventListener('change',() => {hideTooltip();save();});
  $('curveShowAll').addEventListener('click',() => {series.forEach(s => hiddenSeries.delete(seriesKey(s.name)));save();draw();});
  $('curveHideAll').addEventListener('click',() => {series.forEach(s => hiddenSeries.add(seriesKey(s.name)));save();draw();});
  $('curveTime').addEventListener('input',event => showTime(Number(event.target.value)));
  $('curveAll').addEventListener('click',() => { nodes.forEach(n => selected.add(n.name)); update(); });
  $('curveClear').addEventListener('click',() => { selected.clear();update(); });
  $('curveMatches').addEventListener('click',() => {
    const term=searchKey($('curveSearch').value);
    nodes.filter(n => searchKey(`${n.region} ${n.city} ${n.name}`).includes(term)).forEach(n => selected.add(n.name));update();
  });
  $('showRegionCurves').addEventListener('click',() => switchView(true));
  $('showTopology').addEventListener('click',() => switchView(false));
  $('curveExport').addEventListener('click',() => {
    const cell=value => `"${String(value??'').replaceAll('"','""')}"`;
    const text=value => cell(/^[=+@-]/.test(value) ? "'"+value : value);
    const exported=visibleSeries();
    const rows=[['时刻',...exported.flatMap(s => [`${s.name}（元/MWh）`,`${s.name}有效节点数`,`${s.name}已选节点数`])].map(text).join(',')];
    times.forEach((time,i) => rows.push([cell(time),...exported.flatMap(s => [cell(s.prices[i]),cell(s.counts[i]),cell(s.total)])].join(',')));
    const url=URL.createObjectURL(new Blob(['\uFEFF'+rows.join('\r\n')],{type:'text/csv;charset=utf-8'}));
    const a=document.createElement('a');a.href=url;a.download='区域电价曲线.csv';a.click();setTimeout(() => URL.revokeObjectURL(url),1000);
  });
  let resizeTimer;
  window.addEventListener('resize',() => {clearTimeout(resizeTimer);resizeTimer=setTimeout(draw,100);});
  window.addEventListener('hashchange',() => {const curves=restore();renderTree();switchView(curves);});
  const initialCurves=restore();renderTree();switchView(initialCurves);
})();
