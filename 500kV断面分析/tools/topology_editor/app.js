'use strict';
const $ = id => document.getElementById(id);
const svg = $('canvas'), ns = 'http://www.w3.org/2000/svg';
const token = location.hash.slice(1);
let data, selected = null, edge = null, source = null, pending = null, mode = 'select';
let step = 'diagram', mapIdentity = null, mapURL = null;
const skipped = new Set();
let dirty = false, busy = false, undo = [], redo = [], drag = null, gesture = null;
let view = {x: 0, y: 0, k: .12}, fitScale = .12, autoFit = true, drawQueued = false, lastPoint;
const checked = new Set(), measure = document.createElement('canvas').getContext('2d');
measure.font = '18px sans-serif';
const clone = value => structuredClone(value);
const node = id => data?.nodes.find(n => n.id === id);
const key = e => [e.a, e.b].sort().join('|');
const groupKey = n => JSON.stringify([n.station,n.voltage]);
const dimensions = () => step!=='diagram'&&data?.base_map ? data.base_map : data.image;
const anchor = n => n && (step!=='diagram'&&data?.base_map ? n.map_anchor : n.anchor);
const unmarked = n => data?.base_map ? !n.map_anchor : !n.anchor;
const visible = n => anchor(n) && (!data.base_map||step!=='diagram'||n.present&&n.map_anchor) && $(n.voltage === 500 ? 'v500' : n.voltage === 220 ? 'v220' : 'vNone').checked;
const point = event => { const r = svg.getBoundingClientRect(); return {x:event.clientX-r.left, y:event.clientY-r.top}; };
const world = p => ({x:Math.max(0, Math.min(dimensions().width,(p.x-view.x)/view.k)), y:Math.max(0,Math.min(dimensions().height,(p.y-view.y)/view.k))});
const gesturePoint = event => {const p=point(event);return Number.isFinite(p.x)&&Number.isFinite(p.y)&&p.x>=0&&p.y>=0&&p.x<=svg.clientWidth&&p.y<=svg.clientHeight?p:lastPoint||{x:svg.clientWidth/2,y:svg.clientHeight/2};};
function scheduleDraw(){if(!drawQueued){drawQueued=true;requestAnimationFrame(()=>{drawQueued=false;draw();});}}
const notice = text => { $('notice').textContent = text; };
const sEl = (name, attrs) => {const e=document.createElementNS(ns,name);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);return e;};
const button = (text, action) => {const b=document.createElement('button');b.textContent=text;b.onclick=action;return b;};
const overlaps = (a,b) => a[0]<b[2]+3 && b[0]<a[2]+3 && a[1]<b[3]+3 && b[1]<a[3]+3;
function label(n) {
  const group=data.nodes.filter(o=>o.station===n.station && o.voltage===n.voltage);
  return `${n.name} · ${n.voltage ? n.voltage+'kV' : '电压待确认'}${group.length>1?' 〔'+(group.indexOf(n)+1)+'〕':''}`;
}
async function api(path, body, raw=false, name='') {
  const options={headers:{'X-Editor-Token':token}};
  if(body!==undefined){options.method='POST';options.body=raw?body:JSON.stringify(body);options.headers['Content-Type']=raw?'application/octet-stream':'application/json';}
  if(name)options.headers['X-File-Name']=encodeURIComponent(name);
  const response=await fetch('/api/'+path,options), result=await response.json();
  if(!response.ok)throw new Error(result.error || '本机编辑服务不可用');
  return result;
}
function markDirty() { dirty=true; api('status',{dirty:true}).catch(()=>{}); }
function remember() {undo.push(clone(data));undo=undo.slice(-40);redo=[];}
async function transaction(change) {
  if(busy)return false;
  const proposed=clone(data); change(proposed);
  busy=true;renderButtons();
  try {await api('validate',{data:proposed});remember();data=proposed;markDirty();render();return true;}
  catch(e){notice(e.message);return false;}finally{busy=false;renderButtons();}
}
function renderButtons() {
  for(const id of ['save','apply','importPrices','importJson','reload','importMap','stepMap','stepReview','stepDiagram'])$(id).disabled=busy || !data;
  $('undo').disabled=busy || !undo.length;$('redo').disabled=busy || !redo.length;
  $('delete').disabled=busy || !(selected || edge);
  const currentEdge=data?.edges.find(e=>key(e)===edge);
  $('reviewEdge').disabled=busy||!currentEdge||!node(currentEdge.a).map_anchor||!node(currentEdge.b).map_anchor;
  for(const id of ['updateNode','updateEdge','unreviewEdge','resetLayout','nextReview'])$(id).disabled=busy;
  $('place').disabled=busy || !data || !data.nodes.some(n=>(checked.has(n.id)||n.id===selected)&&!n.anchor);
  $('cleanMissing').disabled=busy || !data?.nodes.some(n=>!n.present);
  const current=node(selected);
  $('comparePrices').disabled=busy || !current || !data.nodes.some(n=>n.station===current.station&&n.voltage===current.voltage&&n.curve.length);
  $('locate').disabled=!anchor(node(selected))&&!data?.edges.some(e=>key(e)===edge&&anchor(node(e.a))&&anchor(node(e.b)));
  $('apply').disabled=busy||!data||Boolean(data.base_map&&!data.schematic_generated);
  $('place').hidden=Boolean(data?.base_map);
  $('annotationTools').hidden=step!=='map';
  $('skip').disabled=busy||!current;
  $('clearMark').disabled=busy||!current?.map_anchor;
  $('nextReview').hidden=step!=='review';
  $('resetLayout').hidden=step!=='diagram'||!data?.base_map;
  for(const id of ['connectMode','startConnection'])$(id).hidden=step==='map';
  $('cancelMode').hidden=step==='map';
  for(const [id,value] of [['stepMap','map'],['stepReview','review'],['stepDiagram','diagram']])$(id).setAttribute('aria-pressed',step===value);
  $('startConnection').disabled=busy || !anchor(node(selected));
  $('cancelMode').disabled=mode!=='connect';
  $('selectMode').setAttribute('aria-pressed',mode==='select');$('connectMode').setAttribute('aria-pressed',mode==='connect');
}
function renderList() {
  const list=$('nodeList');list.replaceChildren();const query=$('search').value.trim().toLowerCase(),filter=$('filter').value;
  for(const voltage of [500,220,null]) {
    const values=data.nodes.filter(n=>n.voltage===voltage && (n.name+n.station).toLowerCase().includes(query)
      && (filter==='all'||filter==='unmarked'&&n.present&&unmarked(n)||filter==='marked'&&n.map_anchor||filter==='unplaced'&&!n.anchor||filter==='missing'&&!n.present||filter==='pending'&&(n.pending||n.voltage===null)));
    if(!values.length)continue;
    const group=document.createElement('details');group.open=true;
    const title=document.createElement('summary');title.textContent=`${voltage?voltage+'kV':'电压待确认'}（${values.length}）`;group.append(title);
    for(const n of values){const row=document.createElement('div');row.className='nodeRow'+(selected===n.id?' active':'')+(!n.present?' missing':'');
      const check=document.createElement('input');check.type='checkbox';check.hidden=Boolean(data.base_map);check.checked=checked.has(n.id);check.setAttribute('aria-label','勾选'+n.name);
      check.onchange=()=>{check.checked?checked.add(n.id):checked.delete(n.id);renderButtons();};
      const b=button(label(n)+(n.pending?' [待对应]':data.base_map?(n.map_anchor?' [已标注]':' [未标注]'):!n.anchor?' [未入图]':''),()=>chooseNode(n.id));b.dataset.nodeId=n.id;b.ondblclick=locateSelection;
      row.append(check,b);group.append(row);
    }list.append(group);
  }
}
function inspector() {
  const n=node(selected), e=data.edges.find(e=>key(e)===edge);
  $('nodeForm').hidden=!n;$('edgeForm').hidden=!e;
  $('selection').textContent=n?label(n):e?`${label(node(e.a))} ↔ ${label(node(e.b))}`:'选择节点或线路';
  if(n){$('name').value=n.name;$('city').value=n.city||'';$('voltage').value=n.voltage||'';
    $('connections').replaceChildren();for(const item of data.edges.filter(e=>[e.a,e.b].includes(n.id))){const other=node(item.a===n.id?item.b:item.a);$('connections').append(button(label(other)+(data.base_map?(item.reviewed?' · 已确认':' · 待核对'):''),()=>chooseEdge(key(item))));}
    $('variant').hidden=!(n.present&&n.pending);$('oldNode').replaceChildren();
    for(const old of data.nodes.filter(o=>!o.present&&o.station===n.station&&(o.source_voltage??o.voltage)===(n.source_voltage??n.voltage))){const option=document.createElement('option');option.value=old.id;option.textContent=label(old);$('oldNode').append(option);}
    $('resolveOld').disabled=!$('oldNode').options.length;
  }
  if(e){
    $('edgeKind').value=e.kind;
    $('edgeReviewStatus').textContent=e.reviewed?'已核对确认':'待核对，不会进入新简图';
    $('reviewEdge').hidden=!data.base_map;
    $('unreviewEdge').hidden=!data.base_map||!e.reviewed;
  }
}
function render() {
  if(!data)return;
  document.querySelector('main').classList.toggle('hasSelection',Boolean(node(selected)||edge));
  $('mapEmpty').hidden=step==='diagram'||Boolean(data.base_map);
  updateBasemap();
  renderList();inspector();renderButtons();draw();
  const stations=new Map(data.nodes.filter(n=>n.present).map(n=>[groupKey(n),n]));
  $('progress').textContent=data.base_map?`已标注 ${[...stations.values()].filter(n=>!unmarked(n)).length}/${stations.size} 站 · 已核对 ${data.edges.filter(e=>e.reviewed).length}/${data.edges.length} 条`:'已有简图 · 导入底图后开始标注';
  const count=v=>data.nodes.filter(n=>n.voltage===v&&n.present).length;
  $('summary').textContent=`${dirty?'有未保存修改 · ':''}${data.source} | 500kV ${count(500)} · 220kV ${count(220)} · 待确认 ${count(null)} · 缺失 ${data.nodes.filter(n=>!n.present).length} · 连接 ${data.edges.length}`;
}
function draw() {
  if(!data)return;
  const focused=document.activeElement?.getAttribute('data-node');
  // Scale glyphs with the diagram, using readable sizes at the full-map view.
  const size=step==='diagram'?view.k/fitScale:Math.max(.7,Math.min(1.4,view.k/fitScale)), radius=8*size, font=18*size, gap=13*size;
  const image=$('baseImage');image.setAttribute('width',dimensions().width*view.k);image.setAttribute('height',dimensions().height*view.k);
  image.style.display=step!=='diagram'&&data.base_map?'':'none';
  $('view').setAttribute('transform',`translate(${view.x} ${view.y})`);
  svg.dataset.viewScale=view.k;
  $('zoomValue').textContent=Math.round(view.k/fitScale*100)+'%';
  $('edges').replaceChildren();$('nodes').replaceChildren();$('preview').replaceChildren();
  for(const e of data.edges){
    if(step!=='diagram'&&!data.base_map)continue;
    if(data.base_map&&(step==='map'||step==='diagram'&&!e.reviewed||step==='review'&&key(e)!==edge&&![e.a,e.b].includes(selected)))continue;
    const a=node(e.a),b=node(e.b);if(!visible(a)||!visible(b))continue;
    const group=sEl('g',{'data-edge':key(e)}),attrs={x1:anchor(a).x*view.k,y1:anchor(a).y*view.k,x2:anchor(b).x*view.k,y2:anchor(b).y*view.k};
    group.append(sEl('line',{...attrs,class:'edgeHit'}),sEl('line',{...attrs,'stroke-width':(edge===key(e)?3:2)*size,class:'edge'+(edge===key(e)?' active':'')+(e.kind==='transformer'?' transformer':'')+(data.base_map&&!e.reviewed?' unreviewed':'')}));
    group.addEventListener('pointerdown',event=>{event.stopPropagation();if(mode==='select')chooseEdge(key(e));});$('edges').append(group);
  }
  let values=step!=='diagram'&&!data.base_map?[]:data.nodes.filter(visible);
  if(step!=='diagram'&&data.base_map){
    const groups=new Map();for(const n of values){if(!groups.has(groupKey(n))||n.id===selected)groups.set(groupKey(n),n);}values=[...groups.values()];
  }
  const activeEdge=data.edges.find(e=>key(e)===edge),activeIds=[selected,source,pending?.b,activeEdge?.a,activeEdge?.b];
  const placed=values.map(n=>{const p={x:anchor(n).x*view.k+view.x,y:anchor(n).y*view.k+view.y};return {id:n.id,box:[p.x-radius-2,p.y-radius-2,p.x+radius+2,p.y+radius+2]};}),occupied=[];
  values.sort((a,b)=>Number(!activeIds.includes(a.id))-Number(!activeIds.includes(b.id)));
  for(const n of values){const g=sEl('g',{class:'node'+(activeIds.includes(n.id)?' active':''),'data-node':n.id,tabindex:0,role:'button','aria-label':label(n)});
    const title=sEl('title',{});title.textContent=label(n);g.append(title);
    g.append(sEl('circle',{cx:anchor(n).x*view.k,cy:anchor(n).y*view.k,r:radius,fill:!n.present?'#aebac4':n.pending?'#d99a32':n.voltage===500?'#b54b6c':'#268c82',stroke:'white','stroke-width':2*size}));
    const variants=data.nodes.filter(o=>o.station===n.station&&o.voltage===n.voltage&&o.present);
    const textName=n.name+(step==='diagram'&&variants.length>1?'〔'+(variants.indexOf(n)+1)+'〕':'');
    const x=anchor(n).x*view.k+view.x,y=anchor(n).y*view.k+view.y,w=measure.measureText(textName).width*size,h=22*size;
    const candidates=[[x+gap,y-h-3*size,x+gap+w,y-3*size],[x+gap,y+5*size,x+gap+w,y+5*size+h],[x-gap-w,y-h-3*size,x-gap,y-3*size],[x-gap-w,y+5*size,x-gap,y+5*size+h]];
    const box=candidates.find(b=>b[0]>2&&b[2]<svg.clientWidth-2&&b[1]>2&&b[3]<svg.clientHeight-2&&!occupied.some(o=>overlaps(b,o))&&!placed.some(o=>o.id!==n.id&&overlaps(b,o.box)));
    if(box){occupied.push(box);const text=sEl('text',{x:box[0]-view.x,y:box[1]+font-view.y,'font-size':font,'stroke-width':4*size});text.textContent=textName;g.append(text);}
    g.addEventListener('pointerdown',event=>{event.stopPropagation();if(busy)return;const p=point(event);chooseNode(n.id,true);if(mode==='select'){drag={type:'node',id:n.id,start:p,before:clone(anchor(n)),moved:false};svg.setPointerCapture(event.pointerId);}});
    g.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();chooseNode(n.id);}});$('nodes').append(g);
  }
  if(focused){const target=[...$('nodes').children].find(e=>e.getAttribute('data-node')===focused);target?.focus({preventScroll:true});}
  if(source&&pending){const a=node(source),b=node(pending.b);$('preview').append(sEl('line',{x1:anchor(a).x*view.k,y1:anchor(a).y*view.k,x2:anchor(b).x*view.k,y2:anchor(b).y*view.k,class:'preview','stroke-width':3*size}));}
}
function fit() {
  if(!data||svg.clientWidth<20||svg.clientHeight<20)return;
  const points=step!=='diagram'&&data.base_map?[{x:0,y:0},{x:data.base_map.width,y:data.base_map.height}]:data.nodes.filter(visible).map(anchor);if(!points.length)return;
  const xs=points.map(p=>p.x),ys=points.map(p=>p.y),x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
  view.k=Math.min((svg.clientWidth-100)/Math.max(300,x1-x0),(svg.clientHeight-70)/Math.max(300,y1-y0));
  fitScale=view.k;view.x=(svg.clientWidth-(x0+x1)*view.k)/2;view.y=(svg.clientHeight-(y0+y1)*view.k)/2;autoFit=true;draw();
}
function zoom(factor,x=svg.clientWidth/2,y=svg.clientHeight/2) {
  const wx=(x-view.x)/view.k,wy=(y-view.y)/view.k;view.k=Math.max(.015,Math.min(8,view.k*factor));view.x=x-wx*view.k;view.y=y-wy*view.k;autoFit=false;scheduleDraw();
}
function cancelConnect() {if($('connectionDialog').open)$('connectionDialog').close();mode='select';source=null;pending=null;renderButtons();draw();notice('已退出连线模式，未确认的连接不会保存。');}
function chooseNode(id,fromCanvas=false) {
  const n=node(id);
  if(mode==='connect'){
    for(const value of [n,node(source)].filter(Boolean))$(value.voltage===500?'v500':value.voltage===220?'v220':'vNone').checked=true;
    if(!anchor(n)){notice('请先将该节点放入图中。');return;}
    if(!source){source=id;selected=id;edge=null;render();notice('起点：'+label(n)+'；请在左侧或图上选择终点。');return;}
    if(source===id)return;
    if(data.edges.some(e=>[e.a,e.b].includes(source)&&[e.a,e.b].includes(id))){notice('这两个节点已经连接。');return;}
    pending={a:source,b:id};$('connectionTitle').textContent='确认新增连接';$('connectionFrom').textContent='起点：'+label(node(source));$('connectionTo').textContent='终点：'+label(n);$('connectionKind').value='line';validateConnection();draw();$('connectionDialog').showModal();return;
  }
  selected=id;edge=null;if(step==='map')mode=fromCanvas?'select':'mark';render();
  if(step==='map')notice(`已选 ${label(n)}；${mode==='mark'?'在底图点一下标注位置，拖动空白仍可平移。':'可拖动标记调整位置。'}`);
}
function chooseEdge(value) {if(step==='map'){step='review';$('filter').value='all';}if(mode==='connect'){notice('请选节点作为终点，或先取消连线。');return;}edge=value;selected=null;mode='select';render();if(data.base_map)locateSelection();}
function validateConnection(){const invalid=pending&&node(pending.a).voltage!==node(pending.b).voltage&&$('connectionKind').value==='line';$('confirmConnection').disabled=invalid;$('connectionHint').textContent=invalid?'两端电压不同，请核对并选择“站内变压器”。':'点击确认才新增线路，取消不会修改拓扑。';}
async function commitNode(){const n=node(selected);if(!n)return true;const patch={name:$('name').value.trim(),city:$('city').value.trim(),voltage:$('voltage').value?Number($('voltage').value):null};if(Object.entries(patch).every(([k,v])=>n[k]===v))return true;return transaction(d=>{const target=d.nodes.find(o=>o.id===n.id);if(!('source_voltage'in target))target.source_voltage=target.voltage;Object.assign(target,patch,{voltage_override:patch.voltage});if(d.base_map)d.schematic_generated=false;});}
async function save(apply=false) {
  if(busy||!await commitNode())return;busy=true;renderButtons();
  try{const result=await api(apply?'apply':'save',{data});data=result.data;dirty=false;render();notice(apply?`已应用到分析：${result.nodes}个500kV节点、${result.edges}条线路。`:'草稿已保存，可关闭页面。');}
  catch(e){notice(e.message);}finally{busy=false;renderButtons();}
}
svg.addEventListener('wheel',event=>{event.preventDefault();if(!data||gesture)return;const p=point(event),unit=event.deltaMode===1?16:event.deltaMode===2?svg.clientHeight:1;
  if(event.ctrlKey||event.metaKey)zoom(Math.exp(Math.max(-1,Math.min(1,-event.deltaY*unit*.003))),p.x,p.y);
  else{view.x-=event.deltaX*unit;view.y-=event.deltaY*unit;autoFit=false;scheduleDraw();}
},{passive:false});
svg.addEventListener('gesturestart',event=>{event.preventDefault();gesture={scale:event.scale||1};},{passive:false});
svg.addEventListener('gesturechange',event=>{event.preventDefault();if(!gesture||!data)return;const p=gesturePoint(event);zoom(event.scale/gesture.scale,p.x,p.y);gesture.scale=event.scale;},{passive:false});
svg.addEventListener('gestureend',event=>{event.preventDefault();gesture=null;},{passive:false});
svg.addEventListener('pointerdown',event=>{if(!data||busy)return;drag={type:'pan',start:point(event),offset:{x:view.x,y:view.y},moved:false};svg.setPointerCapture(event.pointerId);});
svg.addEventListener('pointermove',event=>{lastPoint=point(event);if(!drag)return;const p=lastPoint;if(Math.hypot(p.x-drag.start.x,p.y-drag.start.y)>3)drag.moved=true;if(!drag.moved)return;autoFit=false;
  if(drag.type==='pan'){view.x=drag.offset.x+p.x-drag.start.x;view.y=drag.offset.y+p.y-drag.start.y;}else node(drag.id)[step==='diagram'?'anchor':'map_anchor']=world(p);scheduleDraw();});
svg.addEventListener('pointerup',async event=>{
  if(!drag)return;const finished=drag;drag=null;if(svg.hasPointerCapture(event.pointerId))svg.releasePointerCapture(event.pointerId);
  if(finished.type==='node'&&finished.moved){
    const field=step==='diagram'?'anchor':'map_anchor',after=clone(node(finished.id)[field]);node(finished.id)[field]=finished.before;
    await transaction(d=>{if(field==='map_anchor')markStation(d,finished.id,after);else{const n=d.nodes.find(n=>n.id===finished.id);n.anchor=after;n.diagram_manual=true;}});
  }else if(finished.type==='pan'&&!finished.moved&&step==='map'&&mode==='mark'&&selected){
    const p=point(event),x=(p.x-view.x)/view.k,y=(p.y-view.y)/view.k;
    if(!data.base_map){notice('请先导入底图。');return;}
    if(x<0||y<0||x>data.base_map.width||y>data.base_map.height){notice('请在底图范围内标注。');return;}
    const id=selected;if(await transaction(d=>markStation(d,id,{x,y}))){
      notice(label(node(id))+' 已标注；请保存草稿。');
      if($('continuous').checked)nextStation(id);else mode='select';
    }
  }
});
svg.addEventListener('pointercancel',()=>{if(drag?.type==='node')node(drag.id)[step==='diagram'?'anchor':'map_anchor']=drag.before;drag=null;draw();});
let previousSize;
new ResizeObserver(()=>{const size={w:svg.clientWidth,h:svg.clientHeight};if(autoFit)fit();else if(previousSize){view.x+=(size.w-previousSize.w)/2;view.y+=(size.h-previousSize.h)/2;draw();}previousSize=size;}).observe(svg);
$('fit').onclick=fit;$('zoomIn').onclick=()=>zoom(1.3);$('zoomOut').onclick=()=>zoom(1/1.3);
function locateSelection(){
  const e=data.edges.find(e=>key(e)===edge),values=node(selected)?[node(selected)]:e?[node(e.a),node(e.b)]:[];
  const points=values.map(anchor).filter(Boolean);if(!points.length)return;
  for(const n of values)$(n.voltage===500?'v500':n.voltage===220?'v220':'vNone').checked=true;
  if(points.length===1)view.k=Math.max(view.k,fitScale*4);
  else view.k=Math.min((svg.clientWidth-100)/Math.max(300,Math.abs(points[0].x-points[1].x)),(svg.clientHeight-80)/Math.max(300,Math.abs(points[0].y-points[1].y)));
  view.x=svg.clientWidth/2-points.reduce((s,p)=>s+p.x,0)/points.length*view.k;
  view.y=svg.clientHeight/2-points.reduce((s,p)=>s+p.y,0)/points.length*view.k;autoFit=false;draw();
}
$('locate').onclick=locateSelection;
for(const id of ['v500','v220','vNone'])$(id).onchange=()=>{autoFit?fit():draw();};
$('search').oninput=renderList;$('filter').onchange=renderList;
$('selectMode').onclick=cancelConnect;$('cancelMode').onclick=cancelConnect;
$('connectMode').onclick=async()=>{if(busy||!await commitNode())return;mode='connect';source=anchor(node(selected))?selected:null;renderButtons();draw();notice(source?'起点：'+label(node(source))+'；请选择终点。':'请在左侧节点库或画布选择起点。');};
$('startConnection').onclick=()=>{$('connectMode').click();};
$('connectionKind').onchange=validateConnection;
$('connectionDialog').addEventListener('cancel',event=>{event.preventDefault();cancelConnect();});
$('connectionDialog').addEventListener('close',()=>{if(pending)cancelConnect();});
$('confirmConnection').onclick=async()=>{
  if(!pending||busy)return;
  const {a,b,existing}=pending,proposed={a,b,kind:$('connectionKind').value,group:'人工编辑',reviewed:true};
  if(await transaction(d=>{if(existing)Object.assign(d.edges.find(e=>key(e)===existing),{kind:proposed.kind,reviewed:true});else d.edges.push(proposed);if(d.base_map)d.schematic_generated=false;})){
    pending=null;cancelConnect();notice('连接已核对确认，可撤销；保存草稿后持久保留。');
  }
};
$('updateNode').onclick=()=>commitNode();
$('updateEdge').onclick=()=>transaction(d=>{const e=d.edges.find(e=>key(e)===edge);e.kind=$('edgeKind').value;if(d.base_map){e.reviewed=false;d.schematic_generated=false;}});
$('reviewEdge').onclick=()=>{
  const e=data.edges.find(e=>key(e)===edge);if(!e)return;
  source=e.a;pending={a:e.a,b:e.b,existing:key(e)};
  $('connectionTitle').textContent='核对并确认已有连接';$('connectionFrom').textContent='起点：'+label(node(e.a));$('connectionTo').textContent='终点：'+label(node(e.b));$('connectionKind').value=$('edgeKind').value;validateConnection();draw();$('connectionDialog').showModal();
};
$('unreviewEdge').onclick=()=>transaction(d=>{d.edges.find(e=>key(e)===edge).reviewed=false;d.schematic_generated=false;});
$('save').onclick=()=>save();$('apply').onclick=()=>save(true);
$('undo').onclick=()=>{if(!undo.length||busy)return;const revision=data.revision;redo.push(clone(data));data=undo.pop();data.revision=revision;markDirty();selected=null;edge=null;cancelConnect();render();notice('已撤销。');};
$('redo').onclick=()=>{if(!redo.length||busy)return;const revision=data.revision;undo.push(clone(data));data=redo.pop();data.revision=revision;markDirty();selected=null;edge=null;render();notice('已重做。');};
$('delete').onclick=async()=>{if(!confirm('删除所选节点及其相连线路，或删除所选线路？可以撤销。'))return;const id=selected,value=edge;await transaction(d=>{if(id){d.nodes=d.nodes.filter(n=>n.id!==id);d.edges=d.edges.filter(e=>e.a!==id&&e.b!==id);}else d.edges=d.edges.filter(e=>key(e)!==value);});selected=null;edge=null;render();};
$('cleanMissing').onclick=async()=>{
  const ids=new Set(data.nodes.filter(n=>!n.present).map(n=>n.id));
  const edges=data.edges.filter(e=>ids.has(e.a)||ids.has(e.b)).length;
  if(!ids.size||!confirm(`清理${ids.size}个缺失节点及其${edges}条关联线路？可撤销；重新导入时作为新增节点处理。`))return;
  if(await transaction(d=>{d.nodes=d.nodes.filter(n=>!ids.has(n.id));d.edges=d.edges.filter(e=>!ids.has(e.a)&&!ids.has(e.b));})){
    for(const id of ids)checked.delete(id);if(ids.has(selected))selected=null;edge=null;cancelConnect();render();notice('缺失节点及关联线路已清理，可撤销。');
  }
};
$('comparePrices').onclick=()=>{
  const current=node(selected);if(!current)return;
  const nodes=data.nodes.filter(n=>n.station===current.station&&n.voltage===current.voltage);
  const headers=[...new Set(nodes.flatMap(n=>n.curve_headers||data.headers))],table=$('pricesTable');table.replaceChildren();
  const add=(parent,tag,text)=>{const cell=document.createElement(tag);cell.textContent=text;parent.append(cell);return cell;};
  const head=document.createElement('tr');add(head,'th','时点');
  for(const n of nodes)add(head,'th',label(n)+(n.present?'':' [缺失]')+(n.pending?' [待对应]':''));
  table.append(head);
  for(const header of headers){
    const values=nodes.map(n=>{const i=(n.curve_headers||data.headers).indexOf(header);return i<0?null:n.curve[i]??null;});
    const row=document.createElement('tr');if(new Set(values).size>1)row.className='priceDifference';add(row,'th',header);
    for(const value of values)add(row,'td',value===null?'—':String(value));table.append(row);
  }
  $('pricesDialog').showModal();
};
$('place').onclick=()=>{const ids=data.nodes.filter(n=>!n.anchor&&(checked.has(n.id)||n.id===selected)).map(n=>n.id);const cols=Math.ceil(Math.sqrt(ids.length*data.image.width/data.image.height)),rows=Math.ceil(ids.length/cols);transaction(d=>{ids.forEach((id,i)=>{d.nodes.find(n=>n.id===id).anchor={x:(i%cols+1)*d.image.width/(cols+1),y:(Math.floor(i/cols)+1)*d.image.height/(rows+1)};});});};
async function resolve(old){if(!selected||busy)return;busy=true;renderButtons();try{const result=await api('resolve',{data,candidate:selected,old});remember();data=result.data;selected=result.selected;markDirty();render();notice('价格节点已确认。');}catch(e){notice(e.message);}finally{busy=false;renderButtons();}}
$('resolveOld').onclick=()=>resolve($('oldNode').value);$('resolveNew').onclick=()=>resolve(null);
$('export').onclick=async()=>{if(!data||busy)return;const committed=await commitNode();let backup=data;try{backup=(await api('backup',{data})).data;}catch(e){if(data.base_map)notice('已导出标注数据，但无法附带底图；恢复时需重新导入同一底图。');}
  const blob=new Blob([JSON.stringify(backup,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='500和220kV拓扑.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);if(!committed)notice('已导出图中已有数据；未应用的属性输入未包含，请另行保留。');};
$('importPrices').onclick=()=>$('pricesFile').click();$('importJson').onclick=()=>$('jsonFile').click();
$('pricesFile').onchange=async()=>{const file=$('pricesFile').files[0];if(!file||busy)return;busy=true;renderButtons();notice('正在读取电价表并同步节点…');try{const imported=await api('read-prices',file,true,file.name),result=await api('sync',{data,imported});remember();data=result.data;if(data.base_map)data.schematic_generated=false;selected=null;edge=null;markDirty();render();const s=data.summary;notice(`同步完成：新增${s.added}，更新${s.updated}，缺失${s.missing}，待对应${s.pending}；已有位置和线路保留。`);}catch(e){notice(e.message);}finally{busy=false;$('pricesFile').value='';renderButtons();}};
$('jsonFile').onchange=async()=>{const file=$('jsonFile').files[0];if(!file)return;try{let candidate=JSON.parse(await file.text());candidate.revision=data.revision;candidate=(await api('import-backup',{data:candidate})).data;if(dirty&&!confirm('当前有未保存修改，确定用备份替换？可撤销。'))return;remember();data=candidate;mapIdentity=null;selected=null;edge=null;step=data.base_map?'map':'diagram';$('filter').value=data.base_map?'unmarked':'all';markDirty();render();fit();notice('备份已导入，检查后保存草稿。');}catch(e){notice(e.message);}finally{$('jsonFile').value='';}};

function markStation(d,id,point){
  const current=d.nodes.find(n=>n.id===id);
  for(const n of d.nodes.filter(n=>groupKey(n)===groupKey(current)))n.map_anchor=point?clone(point):null;
  d.schematic_generated=false;
}
function nextStation(previous,skip=false){
  if(skip&&node(previous))skipped.add(groupKey(node(previous)));
  const candidates=data.nodes.filter(n=>n.present&&unmarked(n)&&!skipped.has(groupKey(n)));
  const next=candidates.find(n=>n.voltage===node(previous)?.voltage)||candidates[0];
  $('search').value='';selected=null;edge=null;
  if(next){$('filter').value='unmarked';chooseNode(next.id);}else{mode='select';render();notice('当前待标注站点已处理；可切换“全部节点”查看或进入核对连接。');}
}
async function updateBasemap(){
  const id=data.base_map?.id;if(id===mapIdentity)return;
  mapIdentity=id;if(mapURL){URL.revokeObjectURL(mapURL);mapURL=null;}$('baseImage').removeAttribute('href');
  if(!id)return;
  try{
    const response=await fetch('/api/base-map/'+id,{headers:{'X-Editor-Token':token}});
    if(!response.ok)throw new Error('底图文件不在本机，请重新导入同一底图或带底图的备份。');
    const blob=await response.blob();if(mapIdentity!==id)return;
    mapURL=URL.createObjectURL(blob);$('baseImage').setAttribute('href',mapURL);
  }catch(e){notice(e.message);}
}
async function setStep(value){
  if(busy||!await commitNode())return;cancelConnect();step=value;$('search').value='';
  $('filter').value=value==='map'?'unmarked':'all';render();fit();
  notice(value==='map'?'先选站名，再在底图点一下；拖动空白平移，标记可拖动调整。':'选择站点查看关联线路；核对已有连接或新增连接，确认后才进入简图。');
}
async function generate(reset=false){
  if(busy||!await commitNode())return;
  if(!data.base_map){step='diagram';render();fit();notice('当前为已有简图；导入底图后可开始标注。');return;}
  if(reset&&!confirm('按底图重新排版会替换简图中的手动位置；底图标注不变，可撤销。'))return;
  busy=true;renderButtons();
  try{const result=await api('generate',{data,reset_layout:reset});remember();data=result.data;step='diagram';selected=null;edge=null;mode='select';$('search').value='';$('filter').value='all';markDirty();render();fit();notice('简图已生成，仅显示已标注站点及确认连接；可拖动调整简图位置，底图标注不变。');}
  catch(e){notice(e.message);}finally{busy=false;renderButtons();}
}
$('stepMap').onclick=()=>setStep('map');$('stepReview').onclick=()=>setStep('review');$('stepDiagram').onclick=()=>generate();
$('resetLayout').onclick=()=>generate(true);
$('skip').onclick=()=>nextStation(selected,true);
$('clearMark').onclick=()=>transaction(d=>markStation(d,selected,null));
$('nextReview').onclick=()=>{
  const e=data.edges.find(e=>!e.reviewed&&node(e.a).present&&node(e.b).present&&node(e.a).map_anchor&&node(e.b).map_anchor);
  if(e){chooseEdge(key(e));notice('对照底图核对两端及线路类型，右侧确认或删除错误连接。');}
  else notice('没有两端均已标注的待核对连接；请继续标点，或手动新增缺少的连接。');
};
$('closeInspector').onclick=()=>{selected=null;edge=null;cancelConnect();render();};
$('importMap').onclick=()=>$('mapFile').click();
$('mapFile').onchange=async()=>{
  const file=$('mapFile').files[0];if(!file||busy)return;
  busy=true;renderButtons();notice('正在读取底图，保留原始清晰度…');
  try{
    const metadata=await api('read-map',file,true,file.name);
    if(data.base_map&&data.base_map.id!==metadata.id&&!confirm('更换底图会清除原底图标注，保留节点、连接和简图布局；可撤销。继续吗？')){notice('已取消更换底图，原标注保留。');return;}
    const proposed=clone(data);
    if(proposed.base_map?.id!==metadata.id){for(const n of proposed.nodes)delete n.map_anchor;for(const e of proposed.edges)e.reviewed=false;proposed.schematic_generated=false;}
    proposed.base_map=metadata;await api('validate',{data:proposed});remember();data=proposed;selected=null;edge=null;step='map';mode='select';skipped.clear();$('filter').value='unmarked';mapIdentity=null;markDirty();render();fit();notice('底图已载入；请选择左侧站名，再在图中点选位置。');
  }catch(e){notice(e.message);}finally{busy=false;$('mapFile').value='';renderButtons();}
};
async function load(){const result=await api('state');data=result.data;mapIdentity=null;dirty=result.dirty;undo=[];redo=[];selected=null;edge=null;source=null;pending=null;mode='select';step=data.base_map?'map':'diagram';$('filter').value=data.base_map?'unmarked':'all';render();fit();notice(data.base_map?'先选左侧站名，再点底图位置；完成标点后核对连接、生成简图。':'双指捏合可缩放，双指滑动可平移；选两端后需确认才连线。');}
$('reload').onclick=()=>{if(!dirty||confirm('放弃未保存修改并重新加载？'))load().catch(e=>notice(e.message));};
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
window.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('connectionDialog').open)cancelConnect();if(/INPUT|SELECT|TEXTAREA/.test(event.target.tagName))return;if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='s'){event.preventDefault();save();}if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='z'){event.preventDefault();$(event.shiftKey?'redo':'undo').click();}});
load().catch(e=>notice(e.message));
