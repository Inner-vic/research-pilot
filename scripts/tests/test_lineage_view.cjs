"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const context={};vm.createContext(context);
vm.runInContext(fs.readFileSync("skills/sdm-module/sdm-lifecycle/references/dashboard/lineage-view.js","utf8"),context);
const view=context.SDMLineageView;
const data={root_id:"root",nodes:[{id:"root",kind:"project"},...[1,2].flatMap(n=>[
 {id:"plan:"+n,kind:"plan",round_number:n,round_uid:String(n),title:"方案"+n},
 {id:"trial:"+n,kind:"trial",detail_group:"plan:"+n,status:"running"},
 {id:"result:"+n,kind:"result",round_uid:String(n),round_number:n,status:"completed",summary:"实测结果"},
 {id:"analysis:"+n,kind:"assessment",detail_group:"plan:"+n}]),
 ...["branch","plan_review","run","limitation"].map((kind,i)=>({id:"process:"+i,kind,title:"原记录"+i,detail_group:"plan:2",evidence:{id:"e"+i}}))],
 edges:[["root","plan:1"],["plan:1","trial:1"],["trial:1","result:1"],["result:1","analysis:1"],
 ["result:1","plan:2"],["plan:2","trial:2"],["trial:2","result:2"],["result:2","analysis:2"],
 ["result:1","process:0"],...["1","2","3"].map(i=>["process:0","process:"+i])].map(([source,target])=>({source,target,primary:true,kind:"evolves"})),
 detail_groups:{"plan:1":["trial:1","analysis:1"],"plan:2":["trial:2","analysis:2","process:0","process:1","process:2","process:3"]},
 details:{"process:1":{review:{round:2,revision:1,approved:false,verdict:"approve",type:"方案评审"}}},
 execution_location:{focus_node_id:"process:1",active_node_ids:["trial:2"],retained_node_ids:["result:1"]}};
const before=JSON.stringify(data),p=view.project(data);
assert.equal(p.nodes.length,5);assert(!p.nodes.some(n=>["assessment","plan_review","run","branch","limitation"].includes(n.kind)));
const chain=["root","plan:1","experiment:1","plan:2","experiment:2"];
for(let i=1;i<chain.length;i++)assert(p.edges.some(e=>e.primary!==false&&e.source===chain[i-1]&&e.target===chain[i]));
assert.equal(p.execution_location.focus_node_id,"plan:2");assert.equal(p.execution_location.active_node_ids[0],"experiment:2");
assert.equal(p.execution_location.retained_node_ids[0],"experiment:1");
const exp=p.nodes.find(n=>n.id==="experiment:2"),plan=p.nodes.find(n=>n.id==="plan:2");
assert.equal(exp.source_ids.length,3);assert.equal(plan.record_ids.length,4);
assert.equal(view.detail(data,plan).document.content.sdm_plan.records[1].detail.review.round,2);
assert.equal(view.detail(data,exp).document.content.sdm_experiment.records.length,3);
assert.equal(JSON.stringify(data),before);
const pending={...data,nodes:data.nodes.filter(n=>!["result:2","analysis:2"].includes(n.id)),edges:data.edges.filter(e=>!["result:2","analysis:2"].includes(e.target))};
assert(view.project(pending).nodes.some(n=>n.id==="experiment:2"&&n.status==="running"));
assert(p.nodes.some(n=>n.id==="experiment:2"&&n.status==="completed"));
const failed=view.project({...data,nodes:[...data.nodes,{id:"failed-trial:bad",kind:"trial",task_uid:"bad",status:"failed",detail_group:"root"}],edges:[...data.edges,{source:"root",target:"failed-trial:bad",primary:true}]});
assert(failed.nodes.some(n=>n.id==="experiment:bad"&&n.status==="failed"&&!n.metric));
console.log("PASS: plan/experiment skeleton, stable task identity, pending/failure, complete record ownership and read-only data");

const alternatives={...data,nodes:[...data.nodes,...[10,3].flatMap(n=>[
 {id:"plan:"+n,kind:"plan",round_number:n},{id:"result:"+n,kind:"result",round_uid:String(n),round_number:n,detail_group:"plan:"+n}])],
 edges:[...data.edges,...[10,3].flatMap(n=>[
 {source:"result:1",target:"plan:"+n,primary:true},{source:"plan:"+n,target:"result:"+n,primary:true}])]};
const siblings=view.layout(view.project(alternatives)),byId=new Map(siblings.nodes.map(n=>[n.id,n]));
assert(byId.get("plan:2").x<byId.get("plan:3").x);assert(byId.get("plan:3").x<byId.get("plan:10").x);
const divergent=view.project({...alternatives,edges:[...alternatives.edges,{source:"result:2",target:"plan:10",kind:"informed_by",primary:false}]}),layout=view.layout(divergent),at=new Map(layout.nodes.map(n=>[n.id,n]));
assert(divergent.edges.some(e=>e.source==="experiment:1"&&e.target==="plan:10"&&e.primary));
assert(divergent.edges.some(e=>e.source==="experiment:2"&&e.target==="plan:10"&&e.primary===false&&e.kind==="informed_by"));
for(const e of divergent.edges.filter(e=>e.primary!==false))assert(at.get(e.source).y+at.get(e.source).height<=at.get(e.target).y);
for(const n of [1,2,3,10])assert.equal(at.get("plan:"+n).x,at.get("experiment:"+n).x);
console.log("PASS: candidate branches, numerical ordering, scientific ancestry separated from result triggers and downward flow");

const dash=fs.readFileSync("skills/sdm-module/sdm-lifecycle/references/dashboard/dashboard.js","utf8");
const escape=s=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const renderer={escapeHtml:escape,prose:s=>s?"<p>"+escape(s)+"</p>":"",evidenceButton:e=>"<button>"+escape(e.label||e.id)+"</button>",metricDetails:()=>""};
vm.createContext(renderer);vm.runInContext(dash.slice(dash.indexOf("function renderGraphRecord("),dash.indexOf("// Keep one toolbar row;")),renderer);
const html=renderer.renderGraphRecord({sdm_plan:{node:{summary:"<script>bad</script>"},records:[{node:{kind:"plan_review",title:"<b>raw</b>"},detail:{review:{type:"方案评审",round:3,revision:2,disposition:"等待本轮评审",pending:true,challenges:[{question:"<img onerror=bad>"}]}}}],issues:[]}});
assert(html.includes("第3轮"));assert(html.includes("方案第2版"));assert(html.includes("等待本轮评审"));assert(html.includes("&lt;script&gt;"));assert(!html.includes("<img"));
const bundle=fs.readFileSync("skills/sdm-module/sdm-lifecycle/references/dashboard/exploration-map.js","utf8"),start=bundle.indexOf("mn="),end=bundle.indexOf(",DI=",start);
const kinds={cy:k=>k};vm.createContext(kinds);vm.runInContext("const "+bundle.slice(start,end)+";this.label=mn;",kinds);
assert.equal(kinds.label({kind:"result",kind_label:"实验"}),"实验");assert.equal(kinds.label({kind:"trial"}),"trial");
console.log("PASS: readable multi-round history, pending/revision distinction, escaped record content and library labels");

// A result must expose the recorded reason for the successor, not only its score.
const study={rounds:[{uid:"1",number:1,metric:.7,config:{epochs:2},zh:{comparison:"固定比较"}},
 {uid:"2",number:2,plan:{parent_uid:"1",decision:{reason:"验证错误集中在长距离关系"},proposal:{hypothesis:"扩大感受野"}}}]};
const resultDetail=view.detail(data,exp.id==="experiment:1"?exp:p.nodes.find(n=>n.id==="experiment:1"),study);
assert.equal(resultDetail.document.content.sdm_experiment.decisions[0].plan.decision.reason,"验证错误集中在长距离关系");
const resultHtml=renderer.renderGraphRecord(resultDetail.document.content);
assert(resultHtml.includes("选择依据"));assert(resultHtml.includes("验证错误集中在长距离关系"));
console.log("PASS: real successor decision reasons remain attached to the originating experiment");

// Background updates in another study must not rebuild an unchanged inspector.
let updates=0;
const graphContext={state:{snapshot:{revision:"global-1"},map:{update:()=>updates++,resize:()=>{}}},
 window:{IterLabExplorationMap:{},SDMLineageView:{project:()=>({nodes:[]})}},
 $:()=>({}),compactGraphChrome:()=>{}};
vm.createContext(graphContext);
vm.runInContext(dash.slice(dash.indexOf("function renderGraph(s)"),dash.indexOf("function renderGraphRecord(")),graphContext);
const selected={id:"selected",round_count:2,lineage:{nodes:[]},rounds:[]};
graphContext.renderGraph(selected);assert.equal(updates,1);
graphContext.state.snapshot.revision="another-study-updated";
graphContext.renderGraph(selected);assert.equal(updates,1,"unrelated refresh must preserve expanded review");
graphContext.renderGraph({...selected,lineage:{nodes:[{id:"new-review"}]}});assert.equal(updates,2,"real selected-study change must refresh");
console.log("PASS: unrelated background refresh preserves review disclosure; real record changes still update");

const complete=view.layout(view.project(alternatives,{mainline:{round_uids:["1","2"]}}));
assert.equal(complete.nodes.length,view.project(alternatives).nodes.length);
const completePositions=new Map(complete.nodes.map(n=>[n.id,n]));
assert(completePositions.get("plan:3").x<completePositions.get("plan:10").x);
for(const n of complete.nodes)assert(Number.isFinite(n.x)&&Number.isFinite(n.y));
console.log("PASS: restored tree layout retains every candidate and recorded branch");
