const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('skills/sdm-module/sdm-lifecycle/references/dashboard/dashboard.js','utf8');
const render=source.slice(source.indexOf('function renderActivity('),source.indexOf('function renderInsight('));
function project(acceptance,expanded=false){
 const nodes={};const context={study:{current:{stage:'watching',label:'观察训练任务'},completed:false,paused:false,
  lifecycle_view:{schema:'sdm-lifecycle-view/v2',phases:[],related_steps:[],scope:'阶段不能推定通过'},sdm_acceptance:acceptance},
  document:{getElementById:id=>nodes[id]||=( {innerHTML:'',textContent:'',querySelector:()=>({open:expanded})})}};
 vm.runInNewContext(source.slice(source.indexOf('const $='),source.indexOf('const fmt='))+'const state={};'+render+';renderActivity(study);',context);
 return nodes.activity.innerHTML;
}
const legacy=project();assert(!legacy.includes('生命周期证据覆盖'));
const full=project({steps:Array.from({length:13},(_,i)=>({step:i+1,title:'步骤 '+(i+1),status:i===7?'evidence-pending':'operational-verified'})),scope:'证据只适用于对应运行'});
assert(full.includes('生命周期证据覆盖 · 13 步'));assert(full.includes('待补证据'));assert(full.includes('已有执行核验记录'));
assert(!full.includes('全部通过'));
const unsafe=project({steps:[{step:1,title:'<img src=x onerror=alert(1)>',status:'evidence-pending'}],scope:'<script>bad</script>'});
assert(!unsafe.includes('<img'));assert(!unsafe.includes('<script>'));assert(unsafe.includes('&lt;img'));
console.log('PASS: core-owned coverage, pending evidence, legacy isolation and escaped content');

const expanded=project({steps:[],scope:"刷新保留展开状态"},true);assert(expanded.includes('class="core-acceptance" open'));
