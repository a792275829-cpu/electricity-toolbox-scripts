'use strict';
const $ = id => document.getElementById(id);
const svg = $('canvas'), ns = 'http://www.w3.org/2000/svg';
const token = location.hash.slice(1);
let data, selected = null, edge = null, source = null, pending = null, mode = 'select';
let dirty = false, busy = false, undo = [], redo = [], drag = null, gesture = null;
let view = {x: 0, y: 0, k: .12}, fitScale = .12, autoFit = true, drawQueued = false, lastPoint;
const checked = new Set(), measure = document.createElement('canvas').getContext('2d');
measure.font = '12px sans-serif';
const clone = value => structuredClone(value);
const node = id => data?.nodes.find(n => n.id === id);
const key = e => [e.a, e.b].sort().join('|');
const visible = n => n.anchor && $(n.voltage === 500 ? 'v500' : n.voltage === 220 ? 'v220' : 'vNone').checked;
const point = event => { const r = svg.getBoundingClientRect(); return {x:event.clientX-r.left, y:event.clientY-r.top}; };
const world = p => ({x:Math.max(0, Math.min(data.image.width,(p.x-view.x)/view.k)), y:Math.max(0,Math.min(data.image.height,(p.y-view.y)/view.k))});
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
  for(const id of ['save','apply','importPrices','importJson','reload'])$(id).disabled=busy || !data;
  $('undo').disabled=busy || !undo.length;$('redo').disabled=busy || !redo.length;
  $('delete').disabled=busy || !(selected || edge);
  $('place').disabled=busy || !data || !data.nodes.some(n=>(checked.has(n.id)||n.id===selected)&&!n.anchor);
  $('cleanMissing').disabled=busy || !data?.nodes.some(n=>!n.present);
  const current=node(selected);
  $('comparePrices').disabled=busy || !current || !data.nodes.some(n=>n.station===current.station&&n.voltage===current.voltage&&n.curve.length);
  $('locate').disabled=!node(selected)?.anchor;
  $('startConnection').disabled=busy || !node(selected)?.anchor;
  $('cancelMode').disabled=mode!=='connect';
  $('selectMode').setAttribute('aria-pressed',mode==='select');$('connectMode').setAttribute('aria-pressed',mode==='connect');
}
function renderList() {
  const list=$('nodeList');list.replaceChildren();const query=$('search').value.trim().toLowerCase(),filter=$('filter').value;
  for(const voltage of [500,220,null]) {
    const values=data.nodes.filter(n=>n.voltage===voltage && (n.name+n.station).toLowerCase().includes(query)
      && (filter==='all'||filter==='unplaced'&&!n.anchor||filter==='missing'&&!n.present||filter==='pending'&&(n.pending||n.voltage===null)));
    if(!values.length)continue;
    const group=document.createElement('details');group.open=true;
    const title=document.createElement('summary');title.textContent=`${voltage?voltage+'kV':'电压待确认'}（${values.length}）`;group.append(title);
    for(const n of values){const row=document.createElement('div');row.className='nodeRow'+(selected===n.id?' active':'')+(!n.present?' missing':'');
      const check=document.createElement('input');check.type='checkbox';check.checked=checked.has(n.id);check.setAttribute('aria-label','勾选'+n.name);
      check.onchange=()=>{check.checked?checked.add(n.id):checked.delete(n.id);renderButtons();};
      const b=button(label(n)+(n.pending?' [待对应]':!n.anchor?' [未入图]':''),()=>chooseNode(n.id));b.dataset.nodeId=n.id;
      row.append(check,b);group.append(row);
    }list.append(group);
  }
}
function inspector() {
  const n=node(selected), e=data.edges.find(e=>key(e)===edge);
  $('nodeForm').hidden=!n;$('edgeForm').hidden=!e;
  $('selection').textContent=n?label(n):e?`${label(node(e.a))} ↔ ${label(node(e.b))}`:'选择节点或线路';
  if(n){$('name').value=n.name;$('city').value=n.city||'';$('voltage').value=n.voltage||'';
    $('connections').replaceChildren();for(const item of data.edges.filter(e=>[e.a,e.b].includes(n.id))){const other=node(item.a===n.id?item.b:item.a);$('connections').append(button(label(other),()=>chooseEdge(key(item))));}
    $('variant').hidden=!(n.present&&n.pending);$('oldNode').replaceChildren();
    for(const old of data.nodes.filter(o=>!o.present&&o.station===n.station&&(o.source_voltage??o.voltage)===(n.source_voltage??n.voltage))){const option=document.createElement('option');option.value=old.id;option.textContent=label(old);$('oldNode').append(option);}
    $('resolveOld').disabled=!$('oldNode').options.length;
  }
  if(e)$('edgeKind').value=e.kind;
}
function render() {
  if(!data)return;renderList();inspector();renderButtons();draw();
  const count=v=>data.nodes.filter(n=>n.voltage===v&&n.present).length;
  $('summary').textContent=`${dirty?'有未保存修改 · ':''}${data.source} | 500kV ${count(500)} · 220kV ${count(220)} · 待确认 ${count(null)} · 缺失 ${data.nodes.filter(n=>!n.present).length} · 连接 ${data.edges.length}`;
}
function draw() {
  if(!data)return;
  $('view').setAttribute('transform',`translate(${view.x} ${view.y}) scale(${view.k})`);
  $('zoomValue').textContent=Math.round(view.k/fitScale*100)+'%';
  $('edges').replaceChildren();$('nodes').replaceChildren();$('preview').replaceChildren();
  for(const e of data.edges){const a=node(e.a),b=node(e.b);if(!visible(a)||!visible(b))continue;
    const group=sEl('g',{'data-edge':key(e)}),attrs={x1:a.anchor.x,y1:a.anchor.y,x2:b.anchor.x,y2:b.anchor.y};
    group.append(sEl('line',{...attrs,class:'edgeHit'}),sEl('line',{...attrs,class:'edge'+(edge===key(e)?' active':'')+(e.kind==='transformer'?' transformer':'')}));
    group.addEventListener('pointerdown',event=>{event.stopPropagation();if(mode==='select')chooseEdge(key(e));});$('edges').append(group);
  }
  const values=data.nodes.filter(visible),placed=values.map(n=>{const p={x:n.anchor.x*view.k+view.x,y:n.anchor.y*view.k+view.y};return {id:n.id,box:[p.x-8,p.y-8,p.x+8,p.y+8]};}),occupied=[];
  values.sort((a,b)=>Number(![selected,source,pending?.b].includes(a.id))-Number(![selected,source,pending?.b].includes(b.id)));
  for(const n of values){const g=sEl('g',{class:'node'+([selected,source,pending?.b].includes(n.id)?' active':''),'data-node':n.id,tabindex:0,role:'button','aria-label':label(n)});
    const title=sEl('title',{});title.textContent=label(n);g.append(title);
    g.append(sEl('circle',{cx:n.anchor.x,cy:n.anchor.y,r:7/view.k,fill:!n.present?'#aebac4':n.pending?'#d99a32':n.voltage===500?'#b54b6c':'#268c82',stroke:'white','stroke-width':2/view.k}));
    const x=n.anchor.x*view.k+view.x,y=n.anchor.y*view.k+view.y,w=measure.measureText(n.name).width,h=15;
    const candidates=[[x+11,y-h-3,x+11+w,y-3],[x+11,y+5,x+11+w,y+5+h],[x-11-w,y-h-3,x-11,y-3],[x-11-w,y+5,x-11,y+5+h]];
    const box=candidates.find(b=>b[0]>2&&b[2]<svg.clientWidth-2&&b[1]>2&&b[3]<svg.clientHeight-2&&!occupied.some(o=>overlaps(b,o))&&!placed.some(o=>o.id!==n.id&&overlaps(b,o.box)));
    if(box){occupied.push(box);const text=sEl('text',{x:(box[0]-view.x)/view.k,y:(box[1]+12-view.y)/view.k,'font-size':12/view.k,'stroke-width':4/view.k});text.style.fontSize=12/view.k+'px';text.style.strokeWidth=4/view.k+'px';text.textContent=n.name;g.append(text);}
    g.addEventListener('pointerdown',event=>{event.stopPropagation();if(busy)return;const p=point(event);chooseNode(n.id);if(mode==='select'){drag={type:'node',id:n.id,start:p,before:clone(n.anchor),moved:false};svg.setPointerCapture(event.pointerId);}});
    g.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();chooseNode(n.id);}});$('nodes').append(g);
  }
  if(source&&pending){const a=node(source),b=node(pending.b);$('preview').append(sEl('line',{x1:a.anchor.x,y1:a.anchor.y,x2:b.anchor.x,y2:b.anchor.y,class:'preview'}));}
}
function fit() {
  if(!data||svg.clientWidth<20||svg.clientHeight<20)return;
  const points=data.nodes.filter(visible).map(n=>n.anchor);if(!points.length)return;
  const xs=points.map(p=>p.x),ys=points.map(p=>p.y),x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
  view.k=Math.min((svg.clientWidth-100)/Math.max(300,x1-x0),(svg.clientHeight-70)/Math.max(300,y1-y0));
  fitScale=view.k;view.x=(svg.clientWidth-(x0+x1)*view.k)/2;view.y=(svg.clientHeight-(y0+y1)*view.k)/2;autoFit=true;draw();
}
function zoom(factor,x=svg.clientWidth/2,y=svg.clientHeight/2) {
  const wx=(x-view.x)/view.k,wy=(y-view.y)/view.k;view.k=Math.max(.015,Math.min(8,view.k*factor));view.x=x-wx*view.k;view.y=y-wy*view.k;autoFit=false;scheduleDraw();
}
function cancelConnect() {if($('connectionDialog').open)$('connectionDialog').close();mode='select';source=null;pending=null;renderButtons();draw();notice('已退出连线模式，未确认的连接不会保存。');}
function chooseNode(id) {
  const n=node(id);
  if(mode==='connect'){
    if(!n.anchor){notice('请先将该节点放入图中。');return;}
    if(!source){source=id;selected=id;edge=null;render();notice('起点：'+label(n)+'；请在左侧或图上选择终点。');return;}
    if(source===id)return;
    if(data.edges.some(e=>[e.a,e.b].includes(source)&&[e.a,e.b].includes(id))){notice('这两个节点已经连接。');return;}
    pending={a:source,b:id};$('connectionFrom').textContent='起点：'+label(node(source));$('connectionTo').textContent='终点：'+label(n);$('connectionKind').value='line';validateConnection();draw();$('connectionDialog').showModal();return;
  }
  selected=id;edge=null;render();
}
function chooseEdge(value) {if(mode==='connect'){notice('请选节点作为终点，或先取消连线。');return;}edge=value;selected=null;render();}
function validateConnection(){const invalid=pending&&node(pending.a).voltage!==node(pending.b).voltage&&$('connectionKind').value==='line';$('confirmConnection').disabled=invalid;$('connectionHint').textContent=invalid?'两端电压不同，请核对并选择“站内变压器”。':'点击确认才新增线路，取消不会修改拓扑。';}
async function commitNode(){const n=node(selected);if(!n)return true;const patch={name:$('name').value.trim(),city:$('city').value.trim(),voltage:$('voltage').value?Number($('voltage').value):null};if(Object.entries(patch).every(([k,v])=>n[k]===v))return true;return transaction(d=>{const target=d.nodes.find(o=>o.id===n.id);if(!('source_voltage'in target))target.source_voltage=target.voltage;Object.assign(target,patch,{voltage_override:patch.voltage});});}
async function save(apply=false) {
  if(busy||!await commitNode())return;busy=true;renderButtons();
  try{const result=await api(apply?'apply':'save',{data});data=result.data;dirty=false;render();notice(apply?`已应用到分析：${result.nodes}个500kV节点、${result.edges}条线路。`:'草稿已保存，可关闭页面。');}
  catch(e){notice(e.message);}finally{busy=false;renderButtons();}
}
svg.addEventListener('wheel',event=>{event.preventDefault();if(!data||gesture)return;const p=point(event),unit=event.deltaMode===1?16:event.deltaMode===2?svg.clientHeight:1;
  if(event.ctrlKey||event.metaKey)zoom(Math.exp(Math.max(-1,Math.min(1,-event.deltaY*unit*.01))),p.x,p.y);
  else{view.x-=event.deltaX*unit;view.y-=event.deltaY*unit;autoFit=false;scheduleDraw();}
},{passive:false});
svg.addEventListener('gesturestart',event=>{event.preventDefault();gesture={scale:event.scale||1};},{passive:false});
svg.addEventListener('gesturechange',event=>{event.preventDefault();if(!gesture||!data)return;const p=gesturePoint(event);zoom(event.scale/gesture.scale,p.x,p.y);gesture.scale=event.scale;},{passive:false});
svg.addEventListener('gestureend',event=>{event.preventDefault();gesture=null;},{passive:false});
svg.addEventListener('pointerdown',event=>{if(!data||busy)return;drag={type:'pan',start:point(event),offset:{x:view.x,y:view.y},moved:false};svg.setPointerCapture(event.pointerId);});
svg.addEventListener('pointermove',event=>{lastPoint=point(event);if(!drag)return;const p=lastPoint;if(Math.hypot(p.x-drag.start.x,p.y-drag.start.y)>3)drag.moved=true;if(!drag.moved)return;autoFit=false;
  if(drag.type==='pan'){view.x=drag.offset.x+p.x-drag.start.x;view.y=drag.offset.y+p.y-drag.start.y;}else node(drag.id).anchor=world(p);scheduleDraw();});
svg.addEventListener('pointerup',async event=>{if(!drag)return;const finished=drag;drag=null;if(svg.hasPointerCapture(event.pointerId))svg.releasePointerCapture(event.pointerId);if(finished.type==='node'&&finished.moved){const after=clone(node(finished.id).anchor);node(finished.id).anchor=finished.before;await transaction(d=>{d.nodes.find(n=>n.id===finished.id).anchor=after;});}});
svg.addEventListener('pointercancel',()=>{if(drag?.type==='node')node(drag.id).anchor=drag.before;drag=null;draw();});
let previousSize;
new ResizeObserver(()=>{const size={w:svg.clientWidth,h:svg.clientHeight};if(autoFit)fit();else if(previousSize){view.x+=(size.w-previousSize.w)/2;view.y+=(size.h-previousSize.h)/2;draw();}previousSize=size;}).observe(svg);
$('fit').onclick=fit;$('zoomIn').onclick=()=>zoom(1.3);$('zoomOut').onclick=()=>zoom(1/1.3);
$('locate').onclick=()=>{const n=node(selected);if(!n?.anchor)return;$(n.voltage===500?'v500':n.voltage===220?'v220':'vNone').checked=true;view.k=Math.max(.5,view.k);view.x=svg.clientWidth/2-n.anchor.x*view.k;view.y=svg.clientHeight/2-n.anchor.y*view.k;autoFit=false;draw();};
for(const id of ['v500','v220','vNone'])$(id).onchange=()=>{autoFit?fit():draw();};
$('search').oninput=renderList;$('filter').onchange=renderList;
$('selectMode').onclick=cancelConnect;$('cancelMode').onclick=cancelConnect;
$('connectMode').onclick=async()=>{if(busy||!await commitNode())return;mode='connect';source=node(selected)?.anchor?selected:null;renderButtons();draw();notice(source?'起点：'+label(node(source))+'；请选择终点。':'请在左侧节点库或画布选择起点。');};
$('startConnection').onclick=()=>{$('connectMode').click();};
$('connectionKind').onchange=validateConnection;
$('connectionDialog').addEventListener('cancel',event=>{event.preventDefault();cancelConnect();});
$('connectionDialog').addEventListener('close',()=>{if(pending)cancelConnect();});
$('confirmConnection').onclick=async()=>{if(!pending||busy)return;const proposed={...pending,kind:$('connectionKind').value,group:'人工编辑'};if(await transaction(d=>d.edges.push(proposed))){pending=null;cancelConnect();notice('连接已确认并新增，可撤销；保存草稿后持久保留。');}};
$('updateNode').onclick=()=>commitNode();$('updateEdge').onclick=()=>transaction(d=>{d.edges.find(e=>key(e)===edge).kind=$('edgeKind').value;});
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
$('export').onclick=async()=>{if(!data||busy)return;const committed=await commitNode();const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='500和220kV拓扑.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);if(!committed)notice('已导出图中已有数据；未应用的属性输入未包含，请另行保留。');};
$('importPrices').onclick=()=>$('pricesFile').click();$('importJson').onclick=()=>$('jsonFile').click();
$('pricesFile').onchange=async()=>{const file=$('pricesFile').files[0];if(!file||busy)return;busy=true;renderButtons();notice('正在读取电价表并同步节点…');try{const imported=await api('read-prices',file,true,file.name),result=await api('sync',{data,imported});remember();data=result.data;selected=null;edge=null;markDirty();render();const s=data.summary;notice(`同步完成：新增${s.added}，更新${s.updated}，缺失${s.missing}，待对应${s.pending}；已有位置和线路保留。`);}catch(e){notice(e.message);}finally{busy=false;$('pricesFile').value='';renderButtons();}};
$('jsonFile').onchange=async()=>{const file=$('jsonFile').files[0];if(!file)return;try{const candidate=JSON.parse(await file.text());candidate.revision=data.revision;await api('validate',{data:candidate});if(dirty&&!confirm('当前有未保存修改，确定用备份替换？可撤销。'))return;remember();data=candidate;selected=null;edge=null;markDirty();render();fit();notice('备份已导入，检查后保存草稿。');}catch(e){notice(e.message);}finally{$('jsonFile').value='';}};
async function load(){const result=await api('state');data=result.data;dirty=result.dirty;undo=[];redo=[];selected=null;edge=null;source=null;pending=null;mode='select';render();fit();notice('双指捏合可缩放，双指滑动可平移；选两端后需确认才连线。');}
$('reload').onclick=()=>{if(!dirty||confirm('放弃未保存修改并重新加载？'))load().catch(e=>notice(e.message));};
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
window.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('connectionDialog').open)cancelConnect();if(/INPUT|SELECT|TEXTAREA/.test(event.target.tagName))return;if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='s'){event.preventDefault();save();}if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='z'){event.preventDefault();$(event.shiftKey?'redo':'undo').click();}});
load().catch(e=>notice(e.message));
