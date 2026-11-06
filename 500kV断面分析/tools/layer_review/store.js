window.reviewStore={
  async load(){const r=await fetch('/api/data');if(!r.ok)throw Error('数据读取失败');return r.json()},
  async save(id,patch){const r=await fetch('/api/nodes/'+encodeURIComponent(id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(patch)});const data=await r.json();if(!r.ok)throw Error(data.error);return data}
};
