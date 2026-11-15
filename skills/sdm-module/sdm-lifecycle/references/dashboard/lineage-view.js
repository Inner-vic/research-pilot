/* A view-only projection. Every node ID and raw record stays in the SDM snapshot. */
(function(root){
 "use strict";
 function project(data,expanded){
  const groups=data.detail_groups||{},byId=new Map(data.nodes.map(n=>[n.id,n])),keep=new Set(data.nodes.filter(n=>n.id===data.root_id||["plan","result"].includes(n.kind)).map(n=>n.id));
  for(const id of expanded)for(const member of groups[id]||[])keep.add(member);
  const outgoing=new Map();for(const e of data.edges){if(e.primary===false)continue;if(!outgoing.has(e.source))outgoing.set(e.source,[]);outgoing.get(e.source).push(e);}
  const edges=[],seen=new Set(),add=e=>{const key=e.source+">"+e.target+":"+e.kind;if(e.source!==e.target&&!seen.has(key)){seen.add(key);edges.push({...e,id:key});}};
  for(const source of keep){
   const queue=(outgoing.get(source)||[]).map(e=>({edge:e,hidden:[]})),visited=new Set();
   while(queue.length){const {edge,hidden}=queue.shift(),target=edge.target;if(visited.has(target))continue;visited.add(target);
    if(keep.has(target)){
     // Expanded process placement is membership, not a claim that its upstream result executed this plan.
     const owner=byId.get(target)?.detail_group;
     if(owner&&expanded.has(owner)&&source!==owner&&source!=="result:"+owner.slice(5)&&!byId.get(source)?.detail_group){continue;}
     add({...edge,source,target,kind:hidden.length?"evolves":edge.kind,label:hidden.length?"":"",hidden_node_ids:[]});
    }else for(const next of outgoing.get(target)||[])queue.push({edge:next,hidden:[...hidden,target]});
   }
  }
  for(const e of data.edges)if(e.primary===false&&keep.has(e.source)&&keep.has(e.target))add(e);
  for(const owner of expanded){const members=new Set(groups[owner]||[]);for(const member of members){
   const incoming=edges.some(e=>e.target===member&&(members.has(e.source)||e.source===owner||e.source==="result:"+owner.slice(5)));
   if(!incoming)add({source:owner,target:member,kind:"process_detail",primary:true,label:"过程记录",story_visible:true});
  }}
  const nodes=data.nodes.filter(n=>keep.has(n.id)).map(n=>({...n,summary:n.kind==="plan"&&(groups[n.id]||[]).length?n.summary+" · "+(expanded.has(n.id)?"已展开":"可展开")+" "+groups[n.id].length+" 条过程记录":n.summary}));
  const location={...(data.execution_location||{})},anchor=id=>keep.has(id)?id:byId.get(id)?.detail_group||data.root_id;
  location.focus_node_id=anchor(location.focus_node_id);location.active_node_ids=[...new Set((location.active_node_ids||[]).map(anchor))];
  return {...data,nodes,edges,execution_location:location};
 }
 // Coordinates are a presentation only: no layout edge is added to the record.
 function ownerOf(data,node){
  if(!node)return null;
  if(node.kind==="plan"||node.id===data.root_id)return node.id;
  return node.detail_group||(node.round_uid?"plan:"+node.round_uid:node.id.startsWith("result:")?"plan:"+node.id.slice(7):null);
 }
 function layout(data,{compact=false}={}){
  const width=compact?232:264,height=compact?104:116,gap=44,rankGap=54,pad=32;
  const byId=new Map(data.nodes.map(n=>[n.id,n])),order=new Map(data.nodes.map((n,i)=>[n.id,i]));
  const blocks=new Map(),belongs=new Map();
  for(const n of data.nodes)if(n.id===data.root_id||n.kind==="plan")blocks.set(n.id,{id:n.id,items:[n],children:[]});
  for(const n of data.nodes){
   if(blocks.has(n.id)){belongs.set(n.id,n.id);continue;}
   let owner=ownerOf(data,n);if(!blocks.has(owner))owner=data.root_id;
   if(!blocks.has(owner)){blocks.set(n.id,{id:n.id,items:[n],children:[]});owner=n.id;}
   else blocks.get(owner).items.push(n);belongs.set(n.id,owner);
  }
  // Milestone ancestry chooses the block parent. Reference edges never reorder the tree.
  const base=project(data,new Set()),parents=new Map();
  for(const edge of base.edges){
   if(edge.primary===false)continue;
   const source=belongs.get(edge.source),target=belongs.get(edge.target);
   if(!source||!target||source===target||target===data.root_id||parents.has(target))continue;
   let cursor=source,seen=new Set([target]);while(cursor&&!seen.has(cursor)){seen.add(cursor);cursor=parents.get(cursor);}
   if(cursor)continue;parents.set(target,source);
  }
  const compare=(a,b)=>{
   const x=byId.get(a),y=byId.get(b),xn=x?.round_number,yn=y?.round_number;
   return (Number.isFinite(xn)?xn:Infinity)-(Number.isFinite(yn)?yn:Infinity)||(order.get(a)??0)-(order.get(b)??0);
  };
  for(const block of blocks.values()){
   const parent=parents.get(block.id);if(parent&&blocks.has(parent))blocks.get(parent).children.push(block.id);
   block.children.sort(compare);
   const items=block.items,ids=new Set(items.map(n=>n.id)),local=data.edges.filter(e=>e.primary!==false&&ids.has(e.source)&&ids.has(e.target));
   const rank=new Map(items.map(n=>[n.id,0])),degree=new Map(items.map(n=>[n.id,0]));
   // Rank only by registered local edges, rather than padding every process type into a separate level.
   for(const edge of local)degree.set(edge.target,degree.get(edge.target)+1);
   const ready=items.filter(n=>degree.get(n.id)===0).map(n=>n.id);
   while(ready.length){const id=ready.shift();for(const edge of local.filter(e=>e.source===id)){
    rank.set(edge.target,Math.max(rank.get(edge.target),rank.get(id)+1));degree.set(edge.target,degree.get(edge.target)-1);
    if(degree.get(edge.target)===0)ready.push(edge.target);
   }}
   const levels=[...new Set(rank.values())].sort((a,b)=>a-b),rows=levels.map(level=>items.filter(n=>rank.get(n.id)===level));
   const columns=row=>row.some(n=>n.kind==="result")?2*Math.ceil((row.length-1)/2)+1:row.length;
   block.width=Math.max(...rows.map(row=>columns(row)*width+(columns(row)-1)*gap));
   block.height=rows.length*height+(rows.length-1)*rankGap;block.nodes=[];
   rows.forEach((row,r)=>{
    const result=row.find(n=>n.kind==="result");
    row.filter(n=>n!==result).forEach((n,c)=>{
     const x=result?(block.width-width)/2+(c%2?-1:1)*Math.ceil((c+1)/2)*(width+gap):(block.width-(row.length*width+(row.length-1)*gap))/2+c*(width+gap);
     block.nodes.push({...n,x,y:r*(height+rankGap),width,height});
    });
    if(result)block.nodes.push({...result,x:(block.width-width)/2,y:r*(height+rankGap),width,height});
   });
  }
  // Center each parent over its whole descendant span; sibling order stays chronological.
  function measure(id){const b=blocks.get(id);const spans=b.children.map(measure);b.span=Math.max(b.width,spans.reduce((s,x)=>s+x,0)+Math.max(0,spans.length-1)*gap);return b.span;}
  for(const b of blocks.values())b.children.sort(compare);
  const roots=[...blocks.keys()].filter(id=>!parents.has(id)).sort(compare),nodes=[];let total=0,bottom=0;
  function place(id,x,y){const b=blocks.get(id),cx=x+b.span/2;for(const n of b.nodes)nodes.push({...n,x:n.x+cx-b.width/2,y:n.y+y});bottom=Math.max(bottom,y+b.height);
   const span=b.children.reduce((s,id)=>s+blocks.get(id).span,0)+Math.max(0,b.children.length-1)*gap;let left=cx-span/2;
   for(const child of b.children){place(child,left,y+b.height+rankGap);left+=blocks.get(child).span+gap;}
  }
  for(const id of roots){measure(id);place(id,pad+total,pad);total+=blocks.get(id).span+gap;}
  return {nodes,edges:data.edges,width:Math.max(0,total-gap)+pad*2,height:bottom+pad};
 }
 function frame(map,ids,{maxScale=1}={}){
  const boxes=ids.map(id=>map.graph.getCellById(id)?.getBBox()).filter(Boolean),view=map.safeViewport();
  if(!boxes.length||!view)return false;
  const x=Math.min(...boxes.map(b=>b.x)),y=Math.min(...boxes.map(b=>b.y));
  const width=Math.max(...boxes.map(b=>b.x+b.width))-x,height=Math.max(...boxes.map(b=>b.y+b.height))-y;
  const fit=Math.min(maxScale,view.width/width,view.height/height),limited=fit<.65&&boxes.length>1;
  const scale=Math.max(.04,limited?Math.min(.65,view.width/boxes[0].width):fit);
  const cx=limited?boxes[0].x+boxes[0].width/2:x+width/2;
  map.setViewport({scale,tx:view.x+view.width/2-cx*scale,ty:limited?view.y-y*scale:view.y+view.height/2-(y+height/2)*scale});
  if(map.frameNote){map.frameNote.textContent=limited?"局部过程已展开 · 拖动画布查看其余记录":"";map.frameNote.hidden=!limited;}return true;
 }
 // Follow scientific descendants only; evidence-reference links do not own a branch.
 function branch(data,id){
  const base=project(data,new Set()),byId=new Map(base.nodes.map(n=>[n.id,n]));
  if(!byId.has(id))return {plans:[],node_ids:[],process_count:0};
  const reached=new Set([id]),queue=[id];
  if(byId.get(id).kind==="plan")while(queue.length){const source=queue.shift();for(const edge of base.edges){
   if(edge.primary===false||edge.source!==source||reached.has(edge.target))continue;
   reached.add(edge.target);queue.push(edge.target);
  }}
  const plans=[...reached].filter(key=>byId.get(key)?.kind==="plan"||key===data.root_id);
  const ids=new Set(reached);for(const owner of plans)for(const member of data.detail_groups?.[owner]||[])ids.add(member);
  return {plans,node_ids:[id,...[...ids].filter(key=>key!==id)],process_count:new Set(plans.flatMap(owner=>data.detail_groups?.[owner]||[])).size};
 }
 root.SDMLineageView={project,ownerOf,layout,frame,branch};
})(typeof window!=="undefined"?window:globalThis);
