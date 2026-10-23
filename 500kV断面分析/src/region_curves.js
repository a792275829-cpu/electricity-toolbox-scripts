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
  globalThis.RegionPriceCurves = {aggregateSeries, linePath};
  if (typeof document === 'undefined') return;
  const model = DATA.curve_data;
  if (!model) return;
  const $ = id => document.getElementById(id);
  const panel = $('regionCurveView'), mapPanel = document.querySelector('.app');
  const nodes = model.nodes, times = DATA.time_headers;
  const byName = new Map(nodes.map(n => [n.name, n]));
  const selected = new Set();
  const controls = [];
  let series = [], currentIndex = 0, guide = null, chartX = null;
  const colors = ['#1261d1','#c23725','#13836c','#9254a8','#a56900','#007d99','#a53575','#456437','#6258b4','#9b4f17','#32738e','#77616d'];
  const styleNames = {
    nodes:nodes.map(n => n.name), city:model.regions.flatMap(r => r.cities.map(c => c.name)),
    region:model.regions.map(r => r.name)
  };
  const styleIndex = name => Math.max(0,styleNames[$('curveMode').value].indexOf(name));
  const color = name => colors[styleIndex(name) % colors.length];
  const dash = name => ['', '7 3', '2 3', '10 3 2 3'][Math.floor(styleIndex(name)/colors.length)%4];
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
    return hash.startsWith('curves');
  }
  function save() {
    const params = new URLSearchParams({mode:$('curveMode').value,nodes:[...selected].join('|')});
    try { history.replaceState(null,'',`#curves?${params}`); } catch (_) { /* File viewers may restrict history. */ }
  }
  function switchView(curves) {
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
  function showTime(index) {
    currentIndex=Math.max(0,Math.min(times.length-1,index));
    $('curveTime').value=String(currentIndex);
    $('curveTimeLabel').textContent=times[currentIndex] || '无时点';
    $('curveTime').setAttribute('aria-valuetext',times[currentIndex] || '无时点');
    if (guide && chartX) { guide.setAttribute('x1',chartX(currentIndex)); guide.setAttribute('x2',chartX(currentIndex)); }
    const values=$('curveValues'); values.replaceChildren();
    series.forEach(item => {
      const row=document.createElement('div'); row.className='curve-reading';
      const label=document.createElement('span'), swatch=document.createElement('i');
      swatch.className='curve-swatch'; swatch.style.borderColor=color(item.name);
      if (dash(item.name)) swatch.style.borderTopStyle='dashed';
      label.append(swatch,document.createTextNode(item.name));
      const value=document.createElement('b'), price=item.prices[currentIndex];
      value.textContent=finite(price) ? price.toFixed(2) : '暂无数据';
      value.title='元/MWh'; row.append(label,value);
      if ($('curveMode').value!=='nodes') {
        const count=document.createElement('span'); count.textContent=`${item.counts[currentIndex]}/${item.total} 节点有效`;row.append(count);
      }
      values.append(row);
    });
  }
  function draw() {
    if (panel.hidden) return;
    const svg=$('regionPriceChart'); svg.replaceChildren(); guide=null;
    series=aggregateSeries(nodes,selected,$('curveMode').value,times.length);
    const all=series.flatMap(s => s.prices.filter(finite));
    $('curveEmpty').hidden=all.length>0;
    $('curveEmpty').textContent=selected.size ? '所选节点在这份数据中暂无有效电价，请更换节点或数据文件。' : '请从左侧勾选大区、市或节点。';
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
    chartX=x;
    for (let step=0;step<=4;step++) {
      const v=max-step/4*(max-min),py=y(v);
      svg.append(make('line',{x1:margin.left,y1:py,x2:width-margin.right,y2:py,stroke:'var(--line)'}));
      svg.append(make('text',{x:margin.left-9,y:py+4,'text-anchor':'end','font-size':12,fill:'var(--muted)'},v.toFixed(1)));
    }
    svg.append(make('text',{x:margin.left,y:15,'font-size':12,fill:'var(--muted)'},'元/MWh'));
    [...new Set([0,Math.round((times.length-1)/4),Math.round((times.length-1)/2),Math.round((times.length-1)*3/4),times.length-1])].forEach(i => {
      svg.append(make('text',{x:x(i),y:height-10,'text-anchor':'middle','font-size':12,fill:'var(--muted)'},times[i] || ''));
    });
    series.forEach(item => {
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
    const hit=make('rect',{x:margin.left,y:margin.top,width:width-margin.left-margin.right,height:height-margin.top-margin.bottom,fill:'transparent'});
    function point(event) {
      const b=svg.getBoundingClientRect(),local=(event.clientX-b.left)/b.width*width;
      showTime(Math.round((local-margin.left)/(width-margin.left-margin.right)*Math.max(times.length-1,1)));
    }
    hit.addEventListener('pointermove',point);hit.addEventListener('pointerdown',point);svg.append(hit);
    showTime(currentIndex);
  }
  function update() { syncChecks(); save(); draw(); }
  $('curveSearch').addEventListener('input',renderTree);
  $('curveMode').addEventListener('change',update);
  $('curveTime').addEventListener('input',event => showTime(Number(event.target.value)));
  $('curveAll').addEventListener('click',() => { nodes.forEach(n => selected.add(n.name)); update(); });
  $('curveClear').addEventListener('click',() => { selected.clear();update(); });
  $('curveMatches').addEventListener('click',() => {
    const term=searchKey($('curveSearch').value);
    nodes.filter(n => searchKey(`${n.region} ${n.city} ${n.name}`).includes(term)).forEach(n => selected.add(n.name));update();
  });
  $('showRegionCurves').addEventListener('click',() => switchView(true));
  $('showTopology').addEventListener('click',() => switchView(false));
  $('curveCopyLink').addEventListener('click',async () => {
    save();try { await navigator.clipboard.writeText(location.href);$('curveCopyLink').textContent='已复制当前选择链接'; }
    catch (_) { window.prompt('复制当前选择链接',location.href); }
  });
  $('curveExport').addEventListener('click',() => {
    const cell=value => `"${String(value??'').replaceAll('"','""')}"`;
    const text=value => cell(/^[=+@-]/.test(value) ? "'"+value : value);
    const rows=[['时刻',...series.flatMap(s => [`${s.name}（元/MWh）`,`${s.name}有效节点数`,`${s.name}已选节点数`])].map(text).join(',')];
    times.forEach((time,i) => rows.push([cell(time),...series.flatMap(s => [cell(s.prices[i]),cell(s.counts[i]),cell(s.total)])].join(',')));
    const url=URL.createObjectURL(new Blob(['\uFEFF'+rows.join('\r\n')],{type:'text/csv;charset=utf-8'}));
    const a=document.createElement('a');a.href=url;a.download='区域电价曲线.csv';a.click();setTimeout(() => URL.revokeObjectURL(url),1000);
  });
  let resizeTimer;
  window.addEventListener('resize',() => {clearTimeout(resizeTimer);resizeTimer=setTimeout(draw,100);});
  window.addEventListener('hashchange',() => {const curves=restore();renderTree();switchView(curves);});
  const initialCurves=restore();renderTree();switchView(initialCurves);
})();
