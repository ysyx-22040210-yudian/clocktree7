const $ = id => document.getElementById(id);
const ns = 'http://www.w3.org/2000/svg';
let graph = null, positions = new Map(), selected = null, box = {x: 0, y: 0, w: 2100, h: 700};
let drag = null, activeJob = null, nodeIndex = new Map(), viewNodes = new Map();
let viewScope = '', viewDepth = 5;
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function element(tag, attrs = {}, text) { const node = document.createElementNS(ns, tag); for (const [k,v] of Object.entries(attrs)) node.setAttribute(k,v); if (text !== undefined) node.textContent = text; return node; }
function download(name, data, type='application/json') { const url=URL.createObjectURL(new Blob([data],{type})); const a=document.createElement('a'); a.href=url; a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); }
function addRule(container, data={module:'',clock_inputs:[],clock_output:''}) {
  const row=document.createElement('div'); row.className='rule';
  row.innerHTML='<input class="module" placeholder="module 定义名"><select class="kind"><option value="">选择器件类型</option><option value="mux">选择器</option><option value="gate">门控</option><option value="occ">OCC</option></select><div class="pair"><input class="inputs" placeholder="输入口，逗号分隔"><input class="output" placeholder="唯一输出口"></div><button class="remove">删除规则</button>';
  row.querySelector('.kind').value=data.kind||'';
  row.querySelector('.module').value=data.module; row.querySelector('.inputs').value=data.clock_inputs.join(', '); row.querySelector('.output').value=data.clock_output;
  row.querySelector('.remove').onclick=()=>row.remove(); container.appendChild(row);
}
function setConfig(config) {
  const design=config.design || {}; $('top').value=design.top || ''; $('verdi').value=design.verdi_home || '/home/synopsys/verdi/Verdi_O-2018.09-SP2';
  $('designMode').value=design.elab?'elab':'filelist'; $('designPath').value=design.elab || design.filelist || '';
  $('layers').replaceChildren();
  for(let i=1;i<=7;i++) {
    const data=(config.layers || []).find(l=>l.index===i) || {index:i,hierarchy:'',modules:[]};
    const row=document.createElement('details'); row.className='layer'; row.open=i===1 || data.modules.length>0;
    row.innerHTML=`<summary>L${i} <small>层次 ${i}</small></summary><div class="layer-inner"><input class="hierarchy" placeholder="RTL 层次范围"><div class="rules"></div><button class="add">＋ 添加 module</button></div>`;
    row.querySelector('.hierarchy').value=data.hierarchy;
    for(const rule of data.modules) addRule(row.querySelector('.rules'),rule);
    row.querySelector('.add').onclick=()=>addRule(row.querySelector('.rules'));
    $('layers').appendChild(row);
  }
  $('configJson').value=JSON.stringify(config,null,2);
}
function getConfig() {
  const config={design:{top:$('top').value.trim(),verdi_home:$('verdi').value.trim(),[$('designMode').value]:$('designPath').value.trim()},layers:[]};
  [...$('layers').children].forEach((layer,i)=>config.layers.push({index:i+1,hierarchy:layer.querySelector('.hierarchy').value.trim(),modules:[...layer.querySelectorAll('.rule')].map(r=>({module:r.querySelector('.module').value.trim(),clock_inputs:r.querySelector('.inputs').value.split(',').map(s=>s.trim()).filter(Boolean),clock_output:r.querySelector('.output').value.trim(),...(r.querySelector('.kind').value?{kind:r.querySelector('.kind').value}:{})}))}));
  return config;
}
function setBox() { $('graph').setAttribute('viewBox',`${box.x} ${box.y} ${box.w} ${box.h}`); }
function fit() {
  if (!positions.size) return;
  const all=[...positions.values()];
  const width=Math.max(2100,Math.max(...all.map(p=>p.x))+260), height=Math.max(400,Math.max(...all.map(p=>p.y))+140);
  const ratio=$('canvas').clientWidth/$('canvas').clientHeight;
  box={x:0,y:0,w:Math.max(width,height*ratio),h:Math.max(height,width/ratio)}; setBox();
}
function zoom(factor,cx,cy) {
  const px=cx ?? box.x+box.w/2, py=cy ?? box.y+box.h/2;
  box={x:px-(px-box.x)*factor,y:py-(py-box.y)*factor,w:box.w*factor,h:box.h*factor}; setBox();
}
function details(title, object) { $('details').innerHTML=`<h3>${esc(title)}</h3><pre>${esc(JSON.stringify(object,null,2))}</pre>`; }
function selectNode(node) {
  if(node.aggregate) {
    details('RTL 层次聚合 · '+node.path,{hierarchy:node.path,layer:node.layer,instances:node.members.length,modules:node.moduleCounts,roots:node.rootCount,terminals:node.terminalCount,internal_connections:node.internalEdges,instance_examples:node.members.slice(0,30)});
    const button=document.createElement('button');button.textContent='展开此层次的 '+node.members.length+' 个实例';
    button.onclick=()=>{viewScope=node.path;viewDepth=node.path.split('.').length+1;$('viewMode').value=node.members.length<=150?'instances':'hierarchy';draw();};
    $('details').prepend(button);return;
  }
  selected=node.id; document.querySelectorAll('.node.selected').forEach(n=>n.classList.remove('selected')); document.querySelectorAll('.edge.selected').forEach(n=>n.classList.remove('selected'));
  const relevant=graph.edges.filter(e=>e.source===node.id || e.target===node.id);
  const relevantIds=new Set(relevant.map(e=>e.id));
  document.querySelectorAll('.node').forEach(n=>n.classList.toggle('selected',n.dataset.id===node.id));
  document.querySelectorAll('.edge').forEach(n=>n.classList.toggle('selected',relevantIds.has(n.dataset.id)));
  details(node.id,{...node,upstream:relevant.filter(e=>e.target===node.id).map(e=>({instance:e.source,output:e.source_pin,input:e.target_pin})),downstream:relevant.filter(e=>e.source===node.id).map(e=>({instance:e.target,input:e.target_pin,output:e.source_pin})),boundaries:graph.boundaries.filter(b=>b.instance===node.id)});
}
function inScope(path) {return !viewScope || path===viewScope || path.startsWith(viewScope+'.') || path.startsWith(viewScope+'[');}
function makeView(nodes, edges) {
  if($('viewMode').value==='instances') return {nodes,edges};
  const groups=new Map(),owners=new Map();
  for(const node of nodes) {
    const prefix=node.id.split('.').slice(0,viewDepth).join('.'),key=`group:${node.component}:${node.layer}:${prefix}`;
    if(!groups.has(key)) groups.set(key,{id:key,path:prefix,layer:node.layer,component:node.component,aggregate:true,members:[],moduleCounts:{},inputs:['输入'],output:'输出',rootCount:0,terminalCount:0,internalEdges:0});
    const group=groups.get(key);group.members.push(node.id);group.moduleCounts[node.module]=(group.moduleCounts[node.module]||0)+1;group.rootCount+=Number(node.root);group.terminalCount+=Number(node.terminal);group.has_unresolved_output ||= node.has_unresolved_output;owners.set(node.id,group);
  }
  const display=[];
  for(const group of groups.values()) {
    if(group.members.length===1) {const node=nodeIndex.get(group.members[0]);display.push(node);owners.set(node.id,node);}
    else {group.module=`${group.members.length} 个实例 · ${Object.keys(group.moduleCounts).length} 种 module`;group.root=group.rootCount>0;group.terminal=group.terminalCount>0;display.push(group);}
  }
  const bundles=new Map();
  for(const edge of edges) {
    const source=owners.get(edge.source),target=owners.get(edge.target);
    if(source.id===target.id) {source.internalEdges++;continue;}
    const sourcePin=source.aggregate?'输出':edge.source_pin,targetPin=target.aggregate?'输入':edge.target_pin;
    const key=JSON.stringify([source.id,sourcePin,target.id,targetPin]);
    if(!bundles.has(key)) bundles.set(key,{id:'bundle:'+bundles.size,source:source.id,target:target.id,source_pin:sourcePin,target_pin:targetPin,layer_delta:target.layer-source.layer,members:[]});
    bundles.get(key).members.push(edge);
  }
  return {nodes:display,edges:[...bundles.values()]};
}
function draw() {
  if(!graph) return;
  const scene=$('scene'); scene.replaceChildren(); positions=new Map();
  const filter=$('component').value;
  const actual=graph.nodes.filter(n=>(filter==='all' || String(n.component)===filter)&&inScope(n.id));
  const ids=new Set(actual.map(n=>n.id)), actualEdges=graph.edges.filter(e=>ids.has(e.source)&&ids.has(e.target));
  const {nodes,edges}=makeView(actual,actualEdges);viewNodes=new Map(nodes.map(n=>[n.id,n]));
  $('scopeBar').hidden=!viewScope;$('scopeName').textContent=viewScope?`${viewScope} · ${actual.length} 个实例（局部视图）`:'';
  const groups=new Map();
  for(const n of nodes) { const key=n.component; if(!groups.has(key)) groups.set(key,[]); groups.get(key).push(n); }
  let offset=84;
  for(const [component,members] of [...groups.entries()].sort((a,b)=>a[0]-b[0])) {
    let maxHeight=108;
    for(let layer=1;layer<=7;layer++) {
      const local=members.filter(n=>n.layer===layer), localIds=new Set(local.map(n=>n.id)),localMap=new Map(local.map(n=>[n.id,n]));
      const degree=new Map(local.map(n=>[n.id,0])), successors=new Map(local.map(n=>[n.id,[]]));
      for(const e of edges) if(localIds.has(e.source)&&localIds.has(e.target)) { degree.set(e.target,degree.get(e.target)+1); successors.get(e.source).push(e.target); }
      const queue=local.filter(n=>degree.get(n.id)===0).sort((a,b)=>a.id.localeCompare(b.id)), ordered=[], added=new Set();
      for(let head=0;head<queue.length;head++) {const n=queue[head];if(added.has(n.id))continue;ordered.push(n);added.add(n.id);for(const target of successors.get(n.id)){degree.set(target,degree.get(target)-1);if(degree.get(target)===0)queue.push(localMap.get(target));}}
      ordered.push(...local.filter(n=>!added.has(n.id)));
      let rowY=offset;
      ordered.forEach(n=>{const height=Math.max(76,40+n.inputs.length*17);positions.set(n.id,{x:30+(layer-1)*300,y:rowY,height});rowY+=Math.max(108,height+32);});
      maxHeight=Math.max(maxHeight,rowY-offset);
    }
    offset+=maxHeight+38;
  }
  for(let layer=1;layer<=7;layer++) {
    scene.appendChild(element('rect',{x:15+(layer-1)*300,y:14,width:280,height:Math.max(330,offset-12),class:'column-bg'}));
    scene.appendChild(element('text',{x:30+(layer-1)*300,y:40,class:'column-title'},`L${layer}`));
    scene.appendChild(element('text',{x:74+(layer-1)*300,y:39,class:'column-count'},`${actual.filter(n=>n.layer===layer).length} 实例 · ${nodes.filter(n=>n.layer===layer).length} 图元`));
  }
  const edgeGroup=element('g');scene.appendChild(edgeGroup);
  for(const e of edges) {
    const src=positions.get(e.source),dst=positions.get(e.target),target=viewNodes.get(e.target);
    const inputIndex=Math.max(0,target.inputs.indexOf(e.target_pin));
    const sx=src.x+236,sy=src.y+src.height/2,tx=dst.x,ty=dst.y+43+inputIndex*17;
    let d=`M${sx},${sy} C${sx+65},${sy} ${tx-65},${ty} ${tx},${ty}`;
    if(e.layer_delta===0) { const lane=sx+25; d=`M${sx},${sy} L${lane},${sy} L${lane},${dst.y-15} L${tx-12},${dst.y-15} L${tx-12},${ty} L${tx},${ty}`; }
    const line=element('path',{d,class:'edge'+(e.layer_delta<0?' reverse':''),'marker-end':'url(#arrow)','data-id':e.id});
    line.appendChild(element('title',{},e.members?`${e.members.length} 条实际端口连接（点击查看证据）`:`${e.source}.${e.source_pin} → ${e.target}.${e.target_pin}`));
    const inspect=()=>details(e.members?'聚合连线 · 实际连接及双向 NPI 证据':'端口连线与双向 NPI 证据',e.members?{connections:e.members.length,examples:e.members.slice(0,8)}:e);
    const hit=element('path',{d,class:'edge-hit'});hit.onclick=inspect;line.onclick=inspect;edgeGroup.appendChild(hit);edgeGroup.appendChild(line);
  }
  for(const node of nodes) {
    const p=positions.get(node.id),g=element('g',{class:'node'+(node.aggregate?' aggregate':''),transform:`translate(${p.x},${p.y})`,'data-id':node.id});
    g.appendChild(element('rect',{width:236,height:p.height}));
    const short=(node.path||node.id).split('.').slice(-2).join('.');
    g.appendChild(element('text',{x:12,y:19,class:'name'},short.length>30?short.slice(0,28)+'…':short));
    g.appendChild(element('text',{x:12,y:34,class:'module'},node.module));
    node.inputs.forEach((pin,i)=>{g.appendChild(element('circle',{cx:0,cy:43+i*17,r:3,class:'port'}));g.appendChild(element('text',{x:9,y:46+i*17,class:'pin'},pin));});
    g.appendChild(element('circle',{cx:236,cy:p.height/2,r:3,class:'port'}));g.appendChild(element('text',{x:227,y:p.height/2-7,class:'pin','text-anchor':'end'},node.output));
    if(node.root) g.appendChild(element('circle',{cx:215,cy:15,r:4,fill:'#38a381'}));
    if(node.terminal) g.appendChild(element('circle',{cx:202,cy:15,r:4,fill:'#d39832'}));
    if(node.has_unresolved_output) g.appendChild(element('circle',{cx:189,cy:15,r:4,fill:'#e16662'}));
    g.appendChild(element('title',{},node.aggregate?`${node.path}（聚合 ${node.members.length} 个真实实例，点击展开）`:node.id));g.onclick=()=>selectNode(node);scene.appendChild(g);
  }
  $('viewCount').textContent=`${nodes.length} 个图元 · 当前 ${actual.length} / 全量 ${graph.nodes.length} 实例`;
  $('empty').style.display=nodes.length?'none':'flex';fit();search();
}
function search() {const query=$('search').value.toLowerCase();document.querySelectorAll('.node').forEach(n=>{const node=viewNodes.get(n.dataset.id);n.classList.toggle('search-match',!!query&&(node.aggregate?node.members.some(id=>id.toLowerCase().includes(query)||nodeIndex.get(id).module.toLowerCase().includes(query)):node.id.toLowerCase().includes(query)||node.module.toLowerCase().includes(query)));});}
function locate() {
  if(!graph)return;const query=$('search').value.trim().toLowerCase();if(!query)return;
  const node=graph.nodes.find(n=>n.id.toLowerCase()===query)||graph.nodes.find(n=>n.id.toLowerCase().includes(query)||n.module.toLowerCase().includes(query));
  if(!node){details('没有匹配实例',{query});return;}
  $('viewMode').value='instances';$('component').value='all';viewScope='';draw();
  const p=positions.get(node.id),ratio=$('canvas').clientWidth/$('canvas').clientHeight;
  box={x:p.x-280,y:p.y-160,w:900,h:900/ratio};setBox();selectNode(node);
}
function showGraph(result) {
  if(!Array.isArray(result.nodes)||!Array.isArray(result.edges)||!result.summary)throw new Error('请选择 ClockTree7 结果 JSON');
  graph=result;nodeIndex=new Map(result.nodes.map(n=>[n.id,n]));viewScope='';viewDepth=5;$('viewMode').value=result.nodes.length>400?'hierarchy':'instances';setConfig(result.config);
  const s=result.summary,values=[s.instances,s.edges,s.roots,s.terminals,s.components,s.unresolved];
  [...$('stats').children].forEach((n,i)=>n.querySelector('b').textContent=values[i].toLocaleString());
  $('state').textContent=!s.connectivity_complete?'含未解析项 / 诊断':s.topology_valid===false?'连接已解析 · 存在结构异常':'全量连接已解析';$('state').className='state '+(s.connectivity_complete&&s.topology_valid!==false?'pass':'warn');
  $('component').innerHTML='<option value="all">全部时钟网络</option>'+result.components.map(c=>`<option value="${c.id}">分量 ${c.id+1} · ${c.nodes.length} 实例</option>`).join('');
  $('logTitle').textContent=`提取日志与诊断 · ${s.diagnostics} 项诊断 · ${s.unresolved} 个未解析断点`;
  $('log').textContent=JSON.stringify({summary:s,metadata:result.metadata,diagnostics:result.diagnostics},null,2);
  details('全量提取概况',{summary:s,terminal_layers:s.terminal_layers,metadata:result.metadata});draw();
}
async function api(path,options) {const response=await fetch(path,options);const data=await response.json();if(!response.ok)throw new Error(data.error || response.statusText);return data;}
async function loadRuns(){const data=await api('/api/runs');$('runs').innerHTML='<option value="">运行记录</option>'+data.runs.map(r=>`<option value="${esc(r.id)}">${esc(r.id)} · ${r.summary.instances} 实例</option>`).join('');return data;}
async function run(){try{const config=getConfig();$('configJson').value=JSON.stringify(config,null,2);$('run').disabled=true;const data=await api('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)});activeJob=data.id;$('state').textContent='正在解析 elab';$('state').className='state';document.querySelector('.console').open=true;pollJob();}catch(error){$('run').disabled=false;alert(error.message);}}
async function pollJob(){if(!activeJob)return;try{const data=await api('/api/job?id='+encodeURIComponent(activeJob));$('log').textContent=data.log||data.error||'正在启动 Verdi…';if(data.state==='running'){setTimeout(pollJob,1500);return;}const id=activeJob;activeJob=null;$('run').disabled=false;await loadRuns();if(data.state==='finished'){showGraph(await api('/api/result?run='+encodeURIComponent(id)));$('runs').value=id;}else{$('state').textContent='提取失败';$('state').className='state warn';}}catch(error){$('log').textContent=error.message;$('run').disabled=false;activeJob=null;}}
function chooseFile(callback){$('file').value='';$('file').onchange=async()=>{try{const file=$('file').files[0];if(file)callback(JSON.parse(await file.text()));}catch(error){alert(error.message);}};$('file').click();}
$('loadConfig').onclick=()=>chooseFile(setConfig);$('loadResult').onclick=()=>chooseFile(showGraph);$('saveConfig').onclick=()=>download('clocktree7-config.json',JSON.stringify(getConfig(),null,2));
$('exportResult').onclick=()=>{if(graph)download('clocktree7-full.json',JSON.stringify(graph,null,2));};
$('exportSvg').onclick=()=>{if(graph){const copy=$('graph').cloneNode(true);const style=element('style',{},[...document.styleSheets[0].cssRules].map(r=>r.cssText).join('\n'));copy.prepend(style);download('clocktree7-view.svg',new XMLSerializer().serializeToString(copy),'image/svg+xml');}};
$('applyJson').onclick=()=>{try{setConfig(JSON.parse($('configJson').value));}catch(error){alert(error.message);}};
$('component').onchange=()=>{viewScope='';draw();};$('viewMode').onchange=draw;$('allScope').onclick=()=>{viewScope='';viewDepth=5;$('viewMode').value=graph.nodes.length>400?'hierarchy':'instances';draw();};$('search').oninput=search;$('search').onkeydown=e=>{if(e.key==='Enter')locate();};$('locate').onclick=locate;$('run').onclick=run;$('fit').onclick=fit;$('zoomIn').onclick=()=>zoom(.75);$('zoomOut').onclick=()=>zoom(1.3);
$('runs').onchange=async()=>{if($('runs').value)try{showGraph(await api('/api/result?run='+encodeURIComponent($('runs').value)));}catch(error){alert(error.message);}};
$('graph').onwheel=event=>{event.preventDefault();const rect=$('graph').getBoundingClientRect();zoom(event.deltaY>0?1.12:.89,box.x+(event.clientX-rect.left)/rect.width*box.w,box.y+(event.clientY-rect.top)/rect.height*box.h);};
$('graph').onpointerdown=event=>{if(event.target.closest('.node,.edge'))return;drag={x:event.clientX,y:event.clientY,box:{...box}};$('graph').setPointerCapture(event.pointerId);};
$('graph').onpointermove=event=>{if(!drag)return;const rect=$('graph').getBoundingClientRect();box.x=drag.box.x-(event.clientX-drag.x)/rect.width*drag.box.w;box.y=drag.box.y-(event.clientY-drag.y)/rect.height*drag.box.h;setBox();};
$('graph').onpointerup=()=>drag=null;
setConfig({design:{},layers:[]});loadRuns().then(data=>{if(data.initial)return api('/api/result').then(showGraph);}).catch(error=>{$('log').textContent=error.message;});
