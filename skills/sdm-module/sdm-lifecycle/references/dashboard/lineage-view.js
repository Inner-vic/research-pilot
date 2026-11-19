/* Read-only presentation: plans and experiments; all source records stay intact. */
(function(root){
 "use strict";
 function project(data,study={}){
  const raw=new Map(data.nodes.map(n=>[n.id,n])),rows=new Map((study.rounds||[]).map(r=>[r.uid,r]));
  const aliases=new Map(),nodes=[],experiments=new Map(),plans=data.nodes.filter(n=>n.kind==="plan");
  const rootNode=raw.get(data.root_id);if(rootNode){nodes.push({...rootNode});aliases.set(rootNode.id,rootNode.id);}
  for(const plan of plans){
   const row=rows.get(plan.round_uid),records=(data.detail_groups?.[plan.id]||[]).map(id=>raw.get(id)).filter(n=>n&&!["trial","assessment","investigation"].includes(n.kind));
   const reviews=records.filter(n=>n.kind==="plan_review"),pending=reviews.some(n=>n.status==="pending"),count=reviews.length;
   nodes.push({...plan,kind_label:"研究方案",original_title:plan.title,
    title:plan.round_number?"方案"+plan.round_number+(row?.change_label?" · "+row.change_label:""):"当前研究方案",
    plan_summary:plan.summary,summary:(plan.summary||"")+(count?" · "+count+"条评审记录"+(pending?"，含待完成轮次":""):"")+" · 点击查看方案与评审",
    record_ids:records.map(n=>n.id),presentation_only:true});
   aliases.set(plan.id,plan.id);
  }
  // One stable experiment identity spans queued/running/completed/failed states.
  for(const n of data.nodes.filter(n=>["trial","result"].includes(n.kind))){
   const uid=n.round_uid||n.task_uid||n.id.replace(/^(failed-trial|trial|result):/,""),id="experiment:"+uid;
   let exp=experiments.get(id);
   if(!exp){exp={id,kind:n.kind,kind_label:"实验",title:n.title,status:n.status,summary:n.summary,source_ids:[],round_uid:uid,
    round_number:n.round_number,detail_group:n.detail_group||"plan:"+uid,presentation_only:true};experiments.set(id,exp);}
   exp.source_ids.push(n.id);aliases.set(n.id,id);
   if(n.kind==="result")Object.assign(exp,{kind:"result",status:n.status,summary:n.summary,conclusion:n.conclusion,round_number:n.round_number});
  }
  for(const n of data.nodes.filter(n=>["assessment","investigation"].includes(n.kind))){
   let exp=[...experiments.values()].find(e=>e.detail_group===n.detail_group&&n.detail_group!==data.root_id);
   if(!exp){const edge=data.edges.find(e=>e.target===n.id&&aliases.has(e.source)&&experiments.has(aliases.get(e.source)));if(edge)exp=experiments.get(aliases.get(edge.source));}
   if(exp){exp.source_ids.push(n.id);aliases.set(n.id,exp.id);}
  }
  for(const exp of experiments.values()){
   const row=rows.get(exp.round_uid),measured=exp.kind==="result",prefix=exp.round_number?"实验"+exp.round_number:exp.status==="failed"?"失败实验":"当前实验";
   exp.title=row?prefix+" · "+(study.metric?.label||"主要指标")+" "+Number(row.metric).toFixed(4):
    measured?prefix+" · 已完成":prefix+" · "+({running:"训练中",pending:"等待执行",failed:"执行失败",completed:"执行完成"}[exp.status]||"已登记");
   exp.summary=(row?.next_decision_zh||row?.zh?.comparison||exp.summary||"")+" · 点击查看配置、执行、结果与分析";
   nodes.push(exp);
  }
  for(const n of data.nodes)if(!aliases.has(n.id))aliases.set(n.id,aliases.get(n.detail_group)||data.root_id);
  const ids=new Set(nodes.map(n=>n.id)),edges=[],seen=new Set();
  const add=(source,target,kind,primary=true,label="")=>{
   source=aliases.get(source)||source;target=aliases.get(target)||target;
   if(source===target||!ids.has(source)||!ids.has(target))return;
   const id=source+">"+target+":"+kind;if(!seen.has(id)){seen.add(id);edges.push({id,source,target,kind,primary,label});}
  };
  // Only registered plan ancestry and execution relationships form main edges.
  // Membership of reviews/implementation never becomes a scientific branch.
  for(const plan of plans){
   const incoming=data.edges.filter(e=>e.target===plan.id&&(e.source===data.root_id||raw.get(e.source)?.kind==="result"));
   const trigger=incoming.find(e=>e.kind==="informed_by"),base=incoming.find(e=>e.primary!==false);
   const chosen=base||trigger;
   if(chosen){add(chosen.source,plan.id,"evolves",true,base?"候选起点":"仅记录触发关系");
    const number=raw.get(chosen.source)?.round_number,node=nodes.find(n=>n.id===plan.id);
    if(number&&node)node.summary="候选起点：实验"+number+" · "+node.summary;
   }
   if(trigger&&base&&trigger.source!==base.source)add(trigger.source,plan.id,"informed_by",false,"结果触发");
  }
  for(const exp of experiments.values()){
   const registered=data.edges.find(e=>exp.source_ids.includes(e.target)&&(raw.get(e.source)?.kind==="plan"||e.source===data.root_id)&&e.primary!==false);
   if(registered)add(registered.source,exp.id,"executes");
  }
  for(const e of data.edges)if(e.primary===false&&e.kind!=="informed_by")add(e.source,e.target,e.kind,false,e.label||"");
  const anchor=id=>ids.has(id)?id:aliases.get(id)||data.root_id,location={...(data.execution_location||{})};
  location.focus_node_id=anchor(location.focus_node_id);
  for(const key of ["active_node_ids","retained_node_ids"])location[key]=[...new Set((location[key]||[]).map(anchor))];
  return {...data,nodes,edges,execution_location:location,selected_round_uids:study.mainline?.round_uids||[]};
 }
 function detail(data,node,study={}){
  const raw=new Map(data.nodes.map(n=>[n.id,n])),records=ids=>(ids||[]).map(id=>({node:raw.get(id),detail:data.details?.[id]})).filter(r=>r.node);
  if(node.kind==="plan")return {node,document:{title:"方案内容与评审过程",content:{sdm_plan:{node,records:records(node.record_ids),issues:(data.review_issues||[]).filter(i=>i.owner===node.id)}}}};
  if(node.source_ids){
   const successors=(study.rounds||[]).filter(r=>r.plan?.parent_uid===node.round_uid).map(r=>({title:"方案"+r.number,plan:r.plan}));
   if(study.current?.proposal?.parent_uid===node.round_uid)successors.push({title:"已登记的后续决策",plan:study.current.proposal});
   const decisions=successors.filter((item,i,all)=>all.findIndex(x=>JSON.stringify(x.plan)===JSON.stringify(item.plan))===i);
   return {node,document:{title:"实验配置、执行与分析",content:{sdm_experiment:{node,row:(study.rounds||[]).find(r=>r.uid===node.round_uid),decisions,records:records(node.source_ids)}}}};
  }
  return data.details?.[node.id]||{node,document:{title:"研究记录",content:{sdm_process_records:records(data.detail_groups?.[node.id])}}};
 }
 function ownerOf(data,node){
  if(!node)return null;
  if(node.kind==="plan"||node.id===data.root_id)return node.id;
  return node.detail_group||data.root_id;
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
  const base=data,parents=new Map();
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
  const fit=Math.min(maxScale,view.width/width,view.height/height),limited=fit<.8&&boxes.length>1;
  const scale=Math.max(.04,limited?Math.min(.8,view.width/boxes[0].width):fit);
  const cx=limited?boxes[0].x+boxes[0].width/2:x+width/2;
  map.setViewport({scale,tx:view.x+view.width/2-cx*scale,ty:limited?view.y-y*scale:view.y+view.height/2-(y+height/2)*scale});
  if(map.frameNote){map.frameNote.textContent=limited?"本轮流程已展开 · 沿纵向拖动画布查看":"";map.frameNote.hidden=!limited;}return true;
 }

 root.SDMLineageView={project,detail,ownerOf,layout,frame};
})(typeof window!=="undefined"?window:globalThis);
