const STORE_KEY='layer-review-'+window.reviewDatasetKey;
let reviewData;
function validateNode(n){
 if(!n||typeof n.name!=='string'||!n.name.trim()||!['500kV','220kV','其他','待核验'].includes(n.voltage)||!['待核验','已确认','无匹配','排除'].includes(n.mapping_status)||!['待定位','候选','已确认'].includes(n.position_status)||typeof n.mapping!=='string'||typeof n.note!=='string')throw Error('节点字段无效');
 if(n.mapping_status==='已确认'&&(!n.mapping.trim()||!['500kV','220kV'].includes(n.voltage)))throw Error('确认映射前须填写电价原始名称并确定电压等级');
 if(n.anchor!==null&&(!n.anchor||!Number.isFinite(n.anchor.x)||!Number.isFinite(n.anchor.y)||n.anchor.x<0||n.anchor.x>7111||n.anchor.y<0||n.anchor.y>5025))throw Error('坐标超出底图或格式无效');
 if(n.position_status!=='待定位'&&!n.anchor)throw Error('候选或已确认坐标不能为空');
}
function validateData(data){if(!data||data.version!==1||!Array.isArray(data.nodes)||data.nodes.length!==window.reviewSeed.nodes.length||!Array.isArray(data.connections))throw Error('文件格式或节点数量不符');const ids=new Set();for(const n of data.nodes){if(typeof n.id!=='string'||ids.has(n.id))throw Error('节点编号无效');ids.add(n.id);validateNode(n)}for(const n of window.reviewSeed.nodes)if(!ids.has(n.id))throw Error('节点编号与本平台不符')}
function persist(data){localStorage.setItem(STORE_KEY,JSON.stringify(data))}
window.reviewStore={
 async load(){const stored=localStorage.getItem(STORE_KEY);reviewData=stored?JSON.parse(stored):structuredClone(window.reviewSeed);validateData(reviewData);return reviewData},
 async save(id,patch){validateNode(patch);const node=reviewData.nodes.find(n=>n.id===id);if(!node)throw Error('节点不存在');const updated={...node,...patch,updated_at:new Date().toISOString()};const next={...reviewData,nodes:reviewData.nodes.map(n=>n.id===id?updated:n)};persist(next);reviewData=next;return updated}
};
document.querySelector('#export').onclick=()=>{if(state.dirty){alert('请先保存当前项，再导出。');return}const blob=new Blob([JSON.stringify(reviewData,null,2)],{type:'application/json'});const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='500和220kV节点校对结果.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000)};
document.querySelector('#import').onchange=async e=>{const file=e.target.files[0];e.target.value='';if(!file)return;try{if(state.dirty){alert('请先保存当前项，再导入。');return}const incoming=JSON.parse(await file.text());validateData(incoming);if(!confirm('导入会替换此浏览器中的校对进度。请确认已导出备份。'))return;persist(incoming);reviewData=incoming;state.nodes=incoming.nodes;state.selected=null;renderList();select(state.nodes[0]);document.querySelector('#message').textContent='已导入并保存'}catch(err){alert('导入失败：'+err.message)}};
