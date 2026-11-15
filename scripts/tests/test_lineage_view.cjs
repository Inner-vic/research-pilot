const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context={};vm.createContext(context);vm.runInContext(fs.readFileSync('skills/sdm-module/sdm-lifecycle/references/dashboard/lineage-view.js','utf8'),context);
const data={root_id:'root',nodes:[{id:'root',kind:'project'},{id:'plan:a',kind:'plan'},{id:'trial:a',kind:'trial',detail_group:'plan:a'},{id:'result:a',kind:'result'},{id:'review:a',kind:'plan_review',detail_group:'plan:a'},{id:'plan:b',kind:'plan'},{id:'trial:b',kind:'trial',detail_group:'plan:b'},{id:'result:b',kind:'result'}],edges:[['root','plan:a'],['plan:a','trial:a'],['trial:a','result:a'],['result:a','review:a'],['result:a','plan:b'],['plan:b','trial:b'],['trial:b','result:b']].map(([source,target])=>({source,target,primary:true,kind:'evolves'})),detail_groups:{'plan:a':['trial:a','review:a'],'plan:b':['trial:b']},execution_location:{focus_node_id:'trial:b',active_node_ids:['trial:b']}};
const project=context.SDMLineageView.project;
const base=project(data,new Set()),expanded=project(data,new Set(['plan:a']));
assert.equal(base.nodes.length,5);assert.equal(expanded.nodes.length,7);assert(!expanded.nodes.some(n=>n.id==='trial:b'));
assert.equal(base.execution_location.focus_node_id,'plan:b');
assert(expanded.edges.some(e=>e.source==='result:a'&&e.target==='review:a'));
assert(expanded.edges.every(e=>expanded.nodes.some(n=>n.id===e.source)&&expanded.nodes.some(n=>n.id===e.target)));
assert.equal(project(data,new Set()).nodes.length,5);
console.log('PASS: local disclosure, true result-analysis order, no other branch expansion, focus mapping and collapse');

// Same-baseline siblings have a stable numeric order; expansion keeps the measured
// result on its plan axis and lays every registered primary edge downward.
const graph={root_id:'root',nodes:[{id:'root',kind:'project'},...[10,2,3].flatMap(number=>[
 {id:'plan:'+number,kind:'plan',round_number:number},{id:'result:'+number,kind:'result',round_number:number,round_uid:String(number)}]),
 {id:'trial:2',kind:'trial',detail_group:'plan:2'},{id:'analysis:2',kind:'assessment',detail_group:'plan:2'}],
edges:[...[10,2,3].flatMap(number=>[{source:'root',target:'plan:'+number},{source:'plan:'+number,target:number===2?'trial:2':'result:'+number}]),
 {source:'trial:2',target:'result:2'},{source:'result:2',target:'analysis:2'}].map((e,i)=>({...e,id:String(i),primary:true})),detail_groups:{'plan:2':['trial:2','analysis:2']}};
for(const expanded of [new Set(),new Set(['plan:2'])]){
 const p=context.SDMLineageView.project(graph,expanded),layout=context.SDMLineageView.layout(p),at=new Map(layout.nodes.map(n=>[n.id,n]));
 assert.equal(at.size,p.nodes.length);assert(at.get('plan:2').x<at.get('plan:3').x);assert(at.get('plan:3').x<at.get('plan:10').x);
 for(const n of layout.nodes)assert([n.x,n.y,n.width,n.height].every(Number.isFinite));
 for(const e of p.edges.filter(e=>e.primary!==false))assert(at.get(e.source).y+at.get(e.source).height<=at.get(e.target).y);
 for(const number of [2,3,10])assert.equal(at.get('plan:'+number).x,at.get('result:'+number).x);
}
assert.equal(context.SDMLineageView.ownerOf(graph,graph.nodes.find(n=>n.id==='result:2')),'plan:2');
let viewport;const map={graph:{getCellById:id=>id==='target'?{getBBox:()=>({x:100,y:200,width:264,height:116})}:null},safeViewport:()=>({x:22,y:22,width:800,height:400}),setViewport:v=>viewport=v};
assert(context.SDMLineageView.frame(map,['target']));assert.equal(viewport.tx+(100+132)*viewport.scale,422);assert.equal(viewport.ty+(200+58)*viewport.scale,222);
console.log('PASS: chronological siblings, true downward ancestry, paired axis, result ownership and exact target center');

const subtree=context.SDMLineageView.branch(data,'plan:a');
assert.deepEqual(Array.from(subtree.plans),['plan:a','plan:b']);assert(subtree.node_ids.includes('trial:a'));assert(subtree.node_ids.includes('review:a'));assert(subtree.node_ids.includes('trial:b'));
const isolated=context.SDMLineageView.branch({...data,edges:data.edges.map(e=>e.source==='result:a'&&e.target==='plan:b'?{...e,primary:false}:e)},'plan:a');
assert(!isolated.plans.includes('plan:b'));assert(!isolated.node_ids.includes('trial:b'));
const onlyB=context.SDMLineageView.branch(data,'plan:b');assert.deepEqual(Array.from(onlyB.plans),['plan:b']);assert(!onlyB.node_ids.includes('trial:a'));
console.log('PASS: complete scientific descendant disclosure, reference isolation and sibling isolation');
