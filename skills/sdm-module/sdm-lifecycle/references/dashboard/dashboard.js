"use strict";
const $=id=>document.getElementById(id);
const escapeHtml=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt=(n,d=4)=>Number.isFinite(n)?n.toFixed(d):"—";
const signed=n=>Number.isFinite(n)?(n>0?"+":"")+n.toFixed(4):"—";
const timeLabel=n=>Number.isFinite(n)?new Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(new Date(n*1000)):"—";
const shortUID=uid=>uid?uid.slice(0,8):"—";
const roundName=n=>"实验"+String(n);
const state={snapshot:null,study:new URLSearchParams(location.search).get("study"),view:(["overview","experiments","story","lineage"].includes(new URLSearchParams(location.search).get("view"))?new URLSearchParams(location.search).get("view"):"overview"),storyAll:false,detail:null,detailKey:null,stream:null,connected:false,map:null,mapStudy:null,selectedGraphNode:null,expandedPlans:new Set(),pauseRequest:null};
const tokenKey="sdm-dashboard-session:"+location.origin;
const supplied=new URLSearchParams(location.hash.slice(1)).get("token");
if(supplied){sessionStorage.setItem(tokenKey,supplied);history.replaceState(null,"",location.pathname+location.search);}
const token=sessionStorage.getItem(tokenKey)||"";
const controlKey="sdm-dashboard-control:"+location.origin;
function savedControl(){try{return JSON.parse(sessionStorage.getItem(controlKey)||"null");}catch{return null;}}
function rememberControl(value){try{if(value)sessionStorage.setItem(controlKey,JSON.stringify(value));else sessionStorage.removeItem(controlKey);}catch{}}
let toastTimer;
function toast(message){$("toast").textContent=message;$("toast").hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$("toast").hidden=true,2200);}
function connection(ok,message){state.connected=ok;$("connection-dot").classList.toggle("offline",!ok);$("connection-text").textContent=message||(ok?"研究记录已连接":"连接暂时中断");}
function banner(message){$("error-banner").textContent=message;$("error-banner").hidden=!message;}
async function api(path,signal){const response=await fetch(path,{headers:{Authorization:"Bearer "+token},cache:"no-store",signal});if(!response.ok)throw new Error(response.status===403?"请通过工作台的完整访问链接打开页面。":"研究记录暂时不可读，正在保留上次显示。");return response.json();}
function query(path){return path+(state.study?"?study="+encodeURIComponent(state.study):"");}
function syncRoute(){const query=new URLSearchParams(location.search);if(state.study)query.set("study",state.study);query.set("view",state.view);history.replaceState(null,"",location.pathname+"?"+query.toString());}
function view(name){if(!["overview","experiments","story","lineage"].includes(name))return;state.view=name;if(name!=="lineage")setGraphFocus(false);$("metrics").hidden=name!=="overview";document.body.dataset.view=name;document.querySelector(".content").classList.toggle("graph-view",name==="lineage");document.querySelectorAll(".tabs button").forEach(b=>{const on=b.dataset.view===name;b.classList.toggle("active",on);if(on)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current");});for(const key of ["overview","experiments","story","lineage"])$("view-"+key).hidden=key!==name;syncRoute();if(state.snapshot?.study)renderViews(state.snapshot.study);}
function selectStudy(id){if(id===state.study)return;if(state.stream){state.stream.abort();state.stream=null;}state.studyLoading=true;$("pause-study").disabled=true;state.study=id;state.expandedPlans.clear();state.openPlanBranch=null;state.selectedGraphNode=null;$("graph-open-record").hidden=true;$("graph-open-evidence").hidden=true;if(state.map){releaseGraphChrome();state.map.destroy();state.map=null;}$("search").value="";closeDetail();closeEvidence();syncRoute();load(true);}
function apply(data){if(state.study&&data.study&&data.study.id!==state.study)return;if(state.snapshot?.revision===data.revision){state.studyLoading=false;connection(true);if(data.study)renderPause(data.study);banner(state.controlError||(data.errors.length?"部分研究记录暂不可读":""));return;}state.snapshot=data;state.studyLoading=false;if(!state.study&&data.study)state.study=data.study.id;connection(true);banner(state.controlError||(data.errors.length?"部分研究记录暂时不可读：请核对原始研究回执":""));syncRoute();render(data);}
async function load(reconnect=false){$("refresh").disabled=true;try{const data=await api(query("/api/snapshot"));apply(data);if(reconnect||!state.stream)stream();}catch(e){connection(false);banner(e.message);}finally{$("refresh").disabled=false;}}
async function stream(){if(state.stream)state.stream.abort();const controller=new AbortController();state.stream=controller;try{const response=await fetch(query("/api/events"),{headers:{Authorization:"Bearer "+token},cache:"no-store",signal:controller.signal});if(!response.ok)throw new Error("stream_unavailable");const reader=response.body.getReader(),decoder=new TextDecoder();let buffer="";while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let end;while((end=buffer.indexOf("\n\n"))>=0){const event=buffer.slice(0,end);buffer=buffer.slice(end+2);const payload=event.split("\n").filter(x=>x.startsWith("data: ")).map(x=>x.slice(6)).join("\n");if(event.startsWith("event: snapshot")&&payload)apply(JSON.parse(payload));else if(event.startsWith("event: record-error"))banner("研究记录正在更新，暂时显示上次读取结果。");}}}catch(e){if(e.name==="AbortError")return;}if(!controller.signal.aborted){connection(false,"正在重新连接");state.stream=null;setTimeout(()=>load(true),3000);}}
function render(data){$("study-count").textContent=data.studies.length;$("study-list").innerHTML=data.studies.map(s=>`<button class="study-button ${s.id===state.study?"active":""}" data-study="${escapeHtml(s.id)}" title="${escapeHtml((s.title_zh||s.title)+" · "+s.status_label)}" aria-label="${escapeHtml((s.title_zh||s.title)+" · "+s.status_label)}" aria-pressed="${s.id===state.study}"><span class="study-avatar">${icon("project")}<i class="study-state ${s.completed?"completed":s.paused?"paused":"active"}" aria-hidden="true"></i></span><span class="study-copy"><strong>${escapeHtml(s.title_zh||s.title)}</strong><small>${s.round_count} 次有效实验 · ${escapeHtml(s.status_label)}</small></span></button>`).join("")||'<div class="empty">当前项目尚无已登记研究</div>';
 const s=data.study;document.body.classList.toggle("no-studies",!s);$("empty-workbench").hidden=Boolean(s);document.querySelectorAll(".tabs button").forEach(b=>b.disabled=!s);if(!s){$("study-title").textContent="暂无研究记录";$("study-subtitle").textContent="通过 Codex 启动研究后，实验记录会自动出现在这里。";$("metrics").innerHTML="";$("status-badge").textContent="尚未开始";return;}
 $("breadcrumb").textContent=s.title_zh||s.title;$("study-title").textContent=(s.title_zh||s.title)+" 研究";const goal=s.goal?.target?.value;$("study-subtitle").textContent=(Number.isFinite(goal)?"目标 宏平均 F1 ≥ "+fmt(goal)+" · ":"")+"真实实验与方案迭代";$("status-badge").textContent=s.status_label;$("status-badge").className="badge"+(s.paused?" neutral":s.needs_attention?" warn":s.completed&&s.status!=="research-goal-met"?" neutral":"");$("round-count-tab").textContent=s.round_count;$("sync-time").textContent="同步于 "+timeLabel(data.served_at);renderPause(s);renderBinding(s);renderMetrics(s);renderAttention(s);renderViews(s);if(state.detail)showDetail(state.detail,true);}
function renderMetrics(s){const best=s.incumbent,last=s.latest,goal=s.goal?.target?.value;const gap=best&&Number.isFinite(goal)?Math.max(0,goal-best.metric):null;const latest=s.rounds.at(-1);$("metrics").innerHTML=[
 ["最优候选",fmt(best?.metric),best?"来自 "+roundName(best.number):"等待首个有效结果","featured",""],
 ["最新结果",fmt(last?.metric),latest?.base_number?`${signed(latest.delta_base)} · 相对实际基线 ${roundName(latest.base_number)}`:last?"初始可比较基线":"训练结果尚未返回","",latest?.delta_base>0?"positive":latest?.delta_base<0?"negative":""],
 ["有效实验",String(s.round_count),"已回收并核验的训练结果","",""],
 ["距离目标",fmt(gap),s.status==="research-goal-met"?"登记目标已达到":Number.isFinite(goal)?"目标 宏平均 F1 ≥ "+fmt(goal):"尚未登记数值目标","",""]
 ].map(([label,value,note,cls,sub])=>`<div class="metric ${cls}"><div class="metric-label">${label}<span>${label==="有效实验"?"次数":""}</span></div><div class="metric-value">${value}</div><div class="metric-note ${sub}">${escapeHtml(note)}</div></div>`).join("");}
function renderAttention(s){const attention=$("attention");attention.hidden=!s.needs_attention;if(!s.needs_attention)return;const c=s.current;const message=(/[\u4e00-\u9fff]/.test(c.reason_text||"")?c.reason_text:"")||(s.issues.length?"有实验记录未通过证据核验，相关指标不会显示为有效结果。":"后台当前未推进，需要核对运行状态。");attention.innerHTML=`<span class="attention-icon">!</span><div><strong>研究需要关注</strong><div>${escapeHtml(message)}</div>${(c.evidence||[]).map(e=>evidenceButton(e)).join("")}</div>`;}
function renderViews(s){if(!s)return;renderActivity(s);renderInsight(s);renderChart(s);$("recent-table").innerHTML=table(s.rounds.slice(-5).reverse(),s);renderTable(s);renderStory(s);if(state.view==="lineage")renderGraph(s);}
function renderActivity(s){const c=s.current;$("activity-phase").textContent=c.label;const stage=c.stage;let title=c.label,note="";
 if(s.status==="research-goal-met"){title="本研究已达到登记目标";note="后台已按目标退出，实验记录和研究依据保留供复盘。";}
 else if(s.paused){title=s.control.quiescent?"研究已暂停，保留现场":"已停止新派发，正在收尾已发调用";note=s.control.scope;}
 else if(c.needs_attention){title="当前接续已暂停";note="后台宿主存活不代表研究仍在推进，请查看上方原因与原始回执。";}
 else if(stage==="reviewing-plan"){title=s.latest?roundName(s.latest.number)+" 结果已回收，正在审议下一方案":"正在审议初始研究方案";note="结合全部测量与负面证据，判断继续、回退或调整研究方向。";}
 else if(stage==="implementing"||stage==="preparing-implementation"){title="研究方案已通过，正在实施实验改动";note="实施结果还需核验与代码审议，当前阶段不代表训练已提交。";}
 else if(stage==="reviewing-code"||stage==="preflighting-code"||stage==="freezing"){title=c.label;note="核对方案与实现、可比性和远端执行条件；审议完成后才会实际提交。";}
 else if(stage==="watching"){title=c.label;note=c.task?.uid?"平台任务 "+c.task.uid:"等待原任务的正式平台回执。";}
 const order=["结果分析","方案审议","代码实施","训练执行","结果回收"];let active={"result-ready":0,"reviewing-plan":1,"preparing-implementation":2,"implementing":2,"freezing":2,"preflighting-code":2,"reviewing-code":2,"submitting":3,"initial-submitting":3,"watching":3}[stage];if(s.completed)active=4;
 const track=(c.needs_attention||s.paused)?'<span class="muted tiny">'+(s.paused?'用户已暂停 · 等待明确恢复':'保留原身份 · 等待问题处理')+'</span>':order.map((v,i)=>`<span class="${i===active?"on":i<active?"past":""}">${i<active||s.completed?"✓ ":""}${v}</span>${i<4?"<b>›</b>":""}`).join("");
 const models=(c.needs_attention||s.paused&&s.control.quiescent?[]:c.model_activity||[]).slice(-2).map(x=>`<span class="model-chip ${x.status==="执行中"?"busy":""}">${escapeHtml(x.role)} · ${escapeHtml(x.model)} · ${escapeHtml(({completed:"完成",failed:"失败",interrupted:"已中断"})[x.status]||x.status)}</span>`).join("");
 $("activity").innerHTML=`<div class="activity-content"><div><div class="activity-title">${escapeHtml(title)}</div><div class="activity-note">${escapeHtml(note)}</div>${models?`<div class="activity-models">${models}</div>`:""}</div><div class="stage-track">${track}</div></div>`;}
function renderInsight(s){const p=s.current.proposal||(!s.completed?s.current.governing_plan:null),last=s.rounds.at(-1);let content;
 if(p){const zh=p.zh||{title:"当前研究方案",description:"方案已登记，具体变更以原始回执为准。"};content=`<div class="insight-kicker">${s.paused?(p.approved?"暂停时已通过的方案":"暂停时的方案草案"):s.current.stage==="watching"&&!s.completed?"已提交的实验方案":p.approved?"已通过的研究方案":"正在审议的方案"}</div><div class="insight-title">${escapeHtml(zh.title)}</div><p class="prose">${escapeHtml(zh.description)}</p><div class="tags"><span class="tag">中文概要</span><span class="tag green">${s.completed?"研究已结束":s.current.label}</span></div>`;}
 else content=last?`<div class="insight-kicker">最近一次有效结果</div><div class="insight-title">${escapeHtml(last.zh.title)}</div><p class="prose">${escapeHtml(last.zh.comparison)}</p>`:'<div class="empty">等待初始方案与结果</div>';
 $("research-insight").innerHTML='<div class="insight-body">'+content+'</div>'; }

function renderChart(s){const rows=s.rounds;if(!rows.length){$("metric-chart").innerHTML='<div class="empty">首个结果返回后，自动绘制实验表现</div>';return;}const width=800,height=262,left=51,right=24,top=18,bottom=39;const goal=s.goal?.target?.value;const values=rows.map(r=>r.metric);if(Number.isFinite(goal))values.push(goal);const min=Math.max(0,Math.floor((Math.min(...values)-.045)*10)/10),max=Math.min(1,Math.ceil((Math.max(...values)+.025)*10)/10);const low=min,high=Math.max(max,min+.1);const x=i=>left+(width-left-right)*(rows.length===1?.5:i/(rows.length-1));const y=v=>top+(height-top-bottom)*(1-(v-low)/(high-low));let grid="";for(let i=0;i<5;i++){const v=low+(high-low)*i/4,yy=y(v);grid+=`<line x1="${left}" y1="${yy}" x2="${width-right}" y2="${yy}" stroke="var(--line)"/><text x="${left-12}" y="${yy+4}" text-anchor="end" fill="var(--muted)" font-size="10">${v.toFixed(2)}</text>`;}
 let ticks="";const stride=Math.max(1,Math.ceil(rows.length/7));rows.forEach((r,i)=>{if(i%stride===0||i===rows.length-1)ticks+=`<text x="${x(i)}" y="${height-14}" text-anchor="middle" fill="var(--muted)" font-size="10">${roundName(r.number)}</text>`;});
 const actual=rows.map((r,i)=>x(i)+","+y(r.metric)).join(" ");let best="";rows.forEach((r,i)=>{if(i===0)best=`M ${x(i)} ${y(r.best_so_far)}`;else best+=` H ${x(i)} V ${y(r.best_so_far)}`;});const area=best+` L ${x(rows.length-1)} ${height-bottom} L ${x(0)} ${height-bottom} Z`;
 const dots=rows.map((r,i)=>`<circle class="chart-point" data-round="${escapeHtml(r.uid)}" data-tip="${roundName(r.number)} · 宏平均 F1 ${fmt(r.metric)}${r.new_best?" · 刷新最优":""}" tabindex="0" role="button" aria-label="查看 ${roundName(r.number)}，宏平均 F1 ${fmt(r.metric)}" cx="${x(i)}" cy="${y(r.metric)}" r="${r.new_best?3.7:3}" fill="${r.new_best?"var(--accent)":"var(--plot-actual)"}" stroke="var(--surface)" stroke-width="1.3"/>`).join("");
 const goalLine=Number.isFinite(goal)?`<line x1="${left}" y1="${y(goal)}" x2="${width-right}" y2="${y(goal)}" stroke="var(--plot-goal)" stroke-dasharray="4 5"/><text x="${width-right-2}" y="${y(goal)-7}" text-anchor="end" fill="var(--muted)" font-size="9">目标 ${fmt(goal,2)}</text>`:"";
 $("metric-chart").innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="宏平均 F1 按有效实验顺序变化，绿色为历史最优"><defs><linearGradient id="best-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="var(--accent-soft)" stop-opacity=".8"/><stop offset="1" stop-color="var(--accent-soft)" stop-opacity=".25"/></linearGradient></defs>${grid}<path d="${area}" fill="url(#best-fill)"/>${goalLine}<polyline points="${actual}" fill="none" stroke="var(--plot-actual)" stroke-width="1.3"/><path d="${best}" fill="none" stroke="var(--accent)" stroke-width="1.9"/>${dots}${ticks}</svg>`;}
function table(rows,s){if(!rows.length)return '<div class="empty">没有匹配的实验记录</div>';return `<table class="research-table"><thead><tr><th>实验</th><th>方案改动</th><th>实际基线</th><th>宏平均 F1</th><th>相对基线</th><th>训练参数</th><th>记录</th></tr></thead><tbody>${rows.map(r=>`<tr data-round="${escapeHtml(r.uid)}" tabindex="0" role="button" aria-label="查看 ${roundName(r.number)} 实验"><td><span class="run-number">${roundName(r.number)}</span></td><td class="change"><strong>${escapeHtml(r.change_label)}</strong><p title="${escapeHtml(r.zh.description)}">${escapeHtml(r.zh.description)}</p></td><td>${r.base_number?roundName(r.base_number):"初始基线"}</td><td class="metric-cell">${fmt(r.metric)}</td><td class="${r.delta_base>0?"delta-up":r.delta_base<0?"delta-down":"delta-flat"}">${signed(r.delta_base)}</td><td class="muted tiny">${r.config.epochs??"—"} 轮 · 学习率 ${r.config.lr??"—"}</td><td>${r.uid===s.incumbent?.uid?'<span class="best-badge">当前最优</span>':r.new_best?'<span class="tag">曾刷新最优</span>':'<span class="table-count">已核验</span>'}</td></tr>`).join("")}</tbody></table>`;}
function renderTable(s){const term=$("search").value.trim().toLowerCase();let rows=s.rounds.filter(r=>!term||[r.uid,r.zh.description,roundName(r.number),r.change_label].join(" ").toLowerCase().includes(term));const sort=$("sort").value;if(sort==="recent")rows=rows.slice().reverse();else if(sort==="metric")rows=rows.slice().sort((a,b)=>b.metric-a.metric);$("experiment-table").innerHTML=table(rows,s);$("table-summary").textContent=`显示 ${rows.length} / ${s.round_count} 次有效实验 · 指标来自已核验结果，比较基线按原方案记录`+ (s.issues.length?` · ${s.issues.length} 份记录暂未通过读取核验`:"");}
function renderStory(s){const selected=new Set(s.mainline.round_uids);const rows=state.storyAll?s.rounds:s.rounds.filter(r=>selected.has(r.uid));$("story-key").classList.toggle("active",!state.storyAll);$("story-all").classList.toggle("active",state.storyAll);
 $("story-summary").textContent=state.storyAll?`历史列表：${s.round_count} 次有效实验，保留原先按时间展开的形式`:`当前主线：${rows.length} 个已完成上游实验`+(s.mainline.head?"＋当前任务或方案":"")+"；其他尝试请切换研究脉络";
 const cards=rows.map(r=>`<div class="story-row"><div class="story-rail"><span class="story-node ${r.new_best?"best":""}">${r.number}</span></div><article class="story-card" data-round="${escapeHtml(r.uid)}" tabindex="0" role="button" aria-label="查看 ${roundName(r.number)}"><div class="story-top"><strong>${roundName(r.number)} · ${escapeHtml(r.zh.title)}</strong><span class="metric-cell">${fmt(r.metric)}${r.uid===s.incumbent?.uid?" · 当前最优":""}</span></div><p class="intervention">${escapeHtml(r.zh.description)}</p><div class="story-bottom"><span>${r.base_number?"实际基线 "+roundName(r.base_number):"初始可比较基线"}</span><span>相对基线 <b class="${r.delta_base>0?"delta-up":r.delta_base<0?"delta-down":""}">${signed(r.delta_base)}</b></span><span>${r.config.epochs??"—"} 轮训练</span></div>${state.storyAll&&r.next_decision_zh?`<div class="story-reason">${escapeHtml(r.next_decision_zh)}</div>`:""}</article></div>`).join("");
 const head=!state.storyAll&&s.mainline.head?s.mainline.head:null;const pending=head?`<div class="story-row"><div class="story-rail"><span class="story-node">●</span></div><article class="story-card story-head"><div class="story-top"><strong>当前 · ${escapeHtml(head.title)}</strong><span class="tag green">${escapeHtml(head.stage||s.current.label)}</span></div><p class="intervention">${escapeHtml(head.description)}</p><p class="tiny muted">${head.kind==="trial"?"已实际提交，结果尚未归档":"方案或实施阶段，不计为已完成实验"}</p></article></div>`:"";
 $("story").innerHTML='<div class="mainline-basis">'+escapeHtml(s.mainline.basis)+'</div><div class="story-list">'+cards+pending+'</div>'; }
function renderBinding(s){const b=s.binding;$("binding-details").innerHTML=`<div class="binding-grid"><span>工作目录</span><strong>${escapeHtml(b.project_root)}</strong><span>研究编号</span><strong>${escapeHtml(b.study_id)}</strong><span>研究实施会话</span><strong>${escapeHtml(b.execution_thread_id||"尚未建立")}</strong><span>来源 Codex 会话</span><strong>${escapeHtml(b.origin_chat_id||"未登记来源会话编号")}</strong><span>当前聊天记录</span><strong>在 Codex 中查看</strong><p class="binding-note">${escapeHtml(b.explanation)} 加载技能本身不创建研究；确认并建立研究、写入索引后，页面自动发现，不复制另一份项目。</p></div>`;}
function renderGraph(s){state.planScopes=new Map();const data={...window.SDMLineageView.project(s.lineage,state.expandedPlans),revision:state.snapshot.revision};$("lineage-coverage").textContent=`${s.round_count} 次实验 · ${data.nodes.length} 个可见节点${state.expandedPlans.size?" · 局部展开 "+state.expandedPlans.size+" 处":""}`;$("lineage-coverage").title=data.coverage;
 if(!window.IterLabExplorationMap){$("lineage-graph").innerHTML='<div class="empty">图组件尚未加载，请刷新页面。</div>';return;}
 if(!state.map){$("lineage-graph").replaceChildren();state.map=window.IterLabExplorationMap.mount($("lineage-graph"),{compact:false,data,focusOnly:true,layout:window.SDMLineageView.layout,
  localDisclosure:()=>({count:0}), // Node activation is the only process disclosure control.
  loadDetail:async(id,node)=>state.snapshot.study.lineage.details[id]||{node,record:{"记录类型":({plan:"研究方案",trial:"训练任务",result:"实测结果",plan_review:"方案审议",run:"实施过程",limitation:"阻塞记录",assessment:"结果分析"})[node.kind]||"研究记录","记录说明":node.summary}},
  onSelect:node=>{selectGraphNode(node);if(node.kind==="plan"){
   const study=state.study,map=state.map;queueMicrotask(()=>{if(state.study===study&&state.map===map&&state.selectedGraphNode?.id===node.id)setPlanBranch(node.id,state.openPlanBranch!==node.id);});
  }},
  onDeselect:()=>selectGraphNode(null),
  renderRecord:value=>'<div class="definition-grid">'+Object.entries(value||{}).map(([k,v])=>'<div><small>'+escapeHtml(k)+'</small><span>'+escapeHtml(typeof v==="object"?JSON.stringify(v):v)+'</span></div>').join('')+'</div>'});state.map.setSpineOnly(false);const map=state.map;requestAnimationFrame(()=>{if(state.map!==map)return;map.resize();map.frameContext(data.execution_location?.focus_node_id||data.root_id);});}
 else state.map.update(data);state.mapStudy=s.id;compactGraphChrome();state.map.resize();if(state.selectedGraphNode)selectGraphNode(data.nodes.find(n=>n.id===state.selectedGraphNode.id)||null); }


// Keep one toolbar row; move existing controls rather than duplicating their behavior.
function compactGraphChrome(){
 const map=state.map;if(!map)return;
 const actions=document.querySelector(".graph-actions"),focus=$("graph-focus");
 if(!map.chromeHomes){
  const actionsHome=document.createComment("graph actions home"),focusHome=document.createComment("graph focus home");
  actions.before(actionsHome);focus.before(focusHome);map.chromeHomes={actionsHome,focusHome};
  map.toolbar.append(map.locationBar,actions);map.controls.append(focus);
 }
 map.locationButton.setAttribute("aria-label",state.snapshot.study.completed?"定位最新结果":"定位当前方案或结果");
 const info=map.locationBar.querySelector(".em-location-info"),popup=info?.querySelector(".em-location-popover");
 if(info){const label=info.querySelector("summary");label.setAttribute("aria-label","研究状态与图谱说明");label.title="研究状态与图谱说明";}
 if(popup){let summary=popup.querySelector(".sdm-graph-summary");if(!summary){summary=document.createElement("p");summary.className="sdm-graph-summary";popup.prepend(summary);}summary.textContent=$("lineage-coverage").textContent+"。"+document.querySelector(".graph-layout-help").title;}
}
function releaseGraphChrome(){
 const homes=state.map?.chromeHomes;if(!homes)return;
 homes.focusHome.replaceWith($("graph-focus"));homes.actionsHome.replaceWith(document.querySelector(".graph-actions"));state.map.chromeHomes=null;
}

function evidenceButton(e){return e?`<button class="evidence-button" data-evidence="${escapeHtml(e.id)}">↗ ${escapeHtml(e.label)}</button>`:"";}
function section(title,body){return body?`<section class="detail-section"><h3>${title}</h3>${body}</section>`:"";}
function prose(value){return value?`<p class="prose">${escapeHtml(value)}</p>`:"";}
function curveSVG(row){const points=row.curve.map(p=>({epoch:p.epoch,value:p.validation?.macro_f1??p.macro_f1})).filter(p=>Number.isFinite(p.value));if(!points.length)return "";const w=430,h=154,pad=24;const x=i=>pad+(w-pad*2)*i/Math.max(1,points.length-1),y=v=>h-pad-(h-pad*2)*v;let ticks="";for(const value of [0,.5,1])ticks+=`<line x1="${pad}" y1="${y(value)}" x2="${w-pad}" y2="${y(value)}" stroke="var(--line)"/><text x="${pad-6}" y="${y(value)+3}" text-anchor="end" font-size="8" fill="var(--muted)">${value.toFixed(1)}</text>`;return `<div class="mini-chart"><svg viewBox="0 0 ${w} ${h}" aria-label="逐轮 验证 宏平均 F1">${ticks}<polyline points="${points.map((p,i)=>x(i)+","+y(p.value)).join(" ")}" fill="none" stroke="var(--accent)" stroke-width="1.5"/><text x="${pad}" y="${h-4}" font-size="8" fill="var(--muted)">训练轮次 ${points[0].epoch}</text><text x="${w-pad}" y="${h-4}" font-size="8" fill="var(--muted)" text-anchor="end">训练轮次 ${points.at(-1).epoch}</text></svg></div><p class="muted tiny">按原训练器选择 训练轮次 ${row.selected_epoch??"—"}；完整指标见原始结果。</p>`;}
function metricDetails(row){
 const report=row.class_metrics,scalars=(row.reported_metrics||[]).filter(m=>m.key!=="macro_f1");
 if(!report&&!scalars.length)return "";
 const value=(v,delta)=>fmt(v)+(Number.isFinite(delta)?`<small class="${delta>0?"delta-up":delta<0?"delta-down":"muted"}">${signed(delta)}</small>`:"");
 const totals=scalars.length?'<div class="aux-metrics">'+scalars.map(m=>`<div><span>${escapeHtml(m.label)}</span><strong>${fmt(m.value)}</strong></div>`).join("")+"</div>":"";
 let classes="";
 if(report){const rows=report.classes;
  classes=`<p class="muted tiny">${report.sample_count} 个验证样本 · 明细由已核验结果中的混淆计数计算。${report.baseline_comparable?"差值相对本实验实际选用的基线。":"本轮无可比较的类别基线差值。"}</p><input class="class-filter" type="search" data-class-filter placeholder="查找类别" aria-label="查找类别指标"><div class="class-metric-table"><table><thead><tr><th>类别</th><th>样本数</th><th>精确率</th><th>召回率</th><th>F1</th><th>一对其余准确率</th></tr></thead><tbody>${rows.map(r=>`<tr data-class-label="${escapeHtml(r.label.toLowerCase())}"><th scope="row">${escapeHtml(r.label)}</th><td>${r.support}</td>${["precision","recall","f1","accuracy_ovr"].map(k=>`<td>${value(r[k],r.delta?.[k])}</td>`).join("")}</tr>`).join("")}</tbody></table></div><p class="tiny muted" data-class-empty hidden>没有匹配类别。</p><p class="tiny muted">类别命中率对应召回率；一对其余准确率为 (TP+TN)/全部样本。分母为零显示“—”，不擅自记为零。</p>`;
  classes+=rows.length<=12?`<details class="matrix-disclosure"><summary>混淆矩阵</summary><p class="muted tiny">行是真实类别，列是预测类别；数值为样本数。</p><div class="class-metric-table"><table><thead><tr><th>真实 / 预测</th>${rows.map(r=>`<th>${escapeHtml(r.label)}</th>`).join("")}</tr></thead><tbody>${report.confusion_matrix.map((counts,i)=>`<tr><th scope="row">${escapeHtml(rows[i].label)}</th>${counts.map(v=>`<td>${v}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details>`:'<p class="tiny muted">类别较多，完整混淆矩阵请查看原始结果。</p>';
 }
 return `<details class="metric-disclosure"><summary>分类明细与辅助指标 <span>${report?report.classes.length+" 个类别":"已登记指标"}</span></summary><div class="metric-disclosure-body">${totals}${classes}</div></details>`;
}
function showDetail(uid,update=false){const s=state.snapshot?.study,row=s?.rounds.find(r=>r.uid===uid);if(!row)return;const key=JSON.stringify(row);if(update&&key===state.detailKey)return;const scroll=$("detail-body").scrollTop;state.detail=uid;state.detailKey=key;$("detail-title").textContent=roundName(row.number)+" · "+row.zh.title;
 const names={batch_size:"批量大小",channels:"卷积通道",epochs:"训练轮数",lr:"学习率",model:"模型",round:"实验编号",seed:"随机种子",threads:"执行线程"};const model={"fcn-bn-gap":"全卷积分类网络"};
 const params=Object.entries(row.config).filter(([k])=>k!=="expected_minutes");const plan=row.plan;
 $("detail-body").innerHTML='<div class="source-note">以下为基于原始记录整理的中文概要；不是逐字译文，不修改指标、方案或审议结论。</div>'+`<div class="detail-metrics"><div><strong>${fmt(row.metric)}</strong><span>验证宏平均 F1</span></div><div><strong>${signed(row.delta_base)}</strong><span>相对实际基线 ${row.base_number?roundName(row.base_number):"—"}</span></div><div><strong>${Number.isFinite(row.elapsed_seconds)?fmt(row.elapsed_seconds,1)+"秒":"—"}</strong><span>训练上报耗时</span></div></div>`+
 metricDetails(row)+
 section("实际任务",`<button class="uid-button" data-copy="${escapeHtml(row.uid)}">${escapeHtml(row.uid)} ⧉</button><div class="tags"><span class="tag green">结果已核验</span><span class="tag">${row.base_number?"实际基线 "+roundName(row.base_number):"初始基线"}</span></div>`)+
 section("实验改动（中文概要）",prose(row.zh.description))+
 section("本轮检验",prose(row.zh.question))+
 section("实际结果与比较",prose(row.zh.comparison))+
 section("结果后的实际决策",prose(row.next_decision_zh)||'<p class="muted tiny">尚未登记后继决策。</p>')+
 section("逐轮验证曲线",curveSVG(row))+
 section("实际训练参数",`<div class="definition-grid">${params.map(([k,v])=>`<div><small>${escapeHtml(names[k]||"已登记参数")}</small><span>${escapeHtml(model[v]??(v===true?"开启":v===false?"关闭":v))}</span></div>`).join("")}</div>`)+
 section("独立评审",plan?.review?`<div class="tags"><span class="tag green">${({approve:"通过",revise:"要求修改",reject:"否决","needs-evidence":"要求补证",stalled:"停滞"})[plan.review.verdict]||"已记录"}</span><span class="tag">${escapeHtml(plan.review.model||"独立模型")}</span></div><p class="prose">结论来自原始独立评审回执；完整质疑、反证和答复保留在研究脉络与原始证据中。</p>`:"<p class=muted>初始基线记录。</p>")+
 section("证据适用范围",prose("本次结果仅对应登记的数据拆分、随机种子和验证协议。单次差值不等于机制或泛化能力得到证明；负面结果同样保留。"))+
 section("原始技术证据（保持原样）",row.evidence.filter(Boolean).map(evidenceButton).join(""));
 $("drawer-backdrop").hidden=false;$("detail-drawer").hidden=false;$("detail-body").scrollTop=update?scroll:0;if(!update)$("close-drawer").focus(); }


function closeDetail(){state.detail=null;state.detailKey=null;$("detail-drawer").hidden=true;if($("evidence-dialog").hidden)$("drawer-backdrop").hidden=true;}
async function showEvidence(id){$("evidence-dialog").hidden=false;$("drawer-backdrop").hidden=false;$("evidence-title").textContent="读取原始证据";$("evidence-meta").textContent="";$("evidence-body").textContent="正在核验登记摘要…";$("close-evidence").focus();try{const data=await api("/api/evidence?id="+encodeURIComponent(id));$("evidence-title").textContent=data.label;$("evidence-meta").textContent=`SHA256 ${data.sha256} · ${data.bytes.toLocaleString()} 字节 · 摘要匹配`+(data.truncated?" · 仅预览前 128 KiB":"");$("evidence-body").textContent=data.text;}catch(e){$("evidence-body").textContent=e.message;}}
function closeEvidence(){$("evidence-dialog").hidden=true;if($("detail-drawer").hidden)$("drawer-backdrop").hidden=true;}
document.addEventListener("click",event=>{const target=event.target.closest("[data-study],[data-view],[data-evidence],[data-copy],[data-round]");if(!target)return;if(target.dataset.study){selectStudy(target.dataset.study);return;}if(target.dataset.view){view(target.dataset.view);return;}if(target.dataset.evidence){showEvidence(target.dataset.evidence);return;}if(target.dataset.copy){navigator.clipboard.writeText(target.dataset.copy).then(()=>toast("已复制任务 UID"),()=>toast("当前浏览器不允许复制"));return;}if(target.dataset.round)showDetail(target.dataset.round);});
document.addEventListener("keydown",event=>{if(event.key==="Escape"){if(document.querySelector("dialog[open]"))return;if(!$("evidence-dialog").hidden)closeEvidence();else if(!$("detail-drawer").hidden)closeDetail();else if($("theme-picker").open)$("theme-picker").open=false;else if(state.graphFocused)setGraphFocus(false);else if(state.selectedGraphNode&&state.view==="lineage")state.map?.clearSelection();else if(matchMedia("(max-width:620px)").matches&&!document.body.classList.contains("sidebar-collapsed"))foldSidebar();}if((event.key==="Enter"||event.key===" ")&&event.target.matches("[data-round]")){event.preventDefault();showDetail(event.target.dataset.round);}});

$("metric-chart").addEventListener("pointermove",event=>{const dot=event.target.closest("[data-tip]");if(!dot){$("chart-tooltip").hidden=true;return;}$("chart-tooltip").textContent=dot.dataset.tip;$("chart-tooltip").hidden=false;$("chart-tooltip").style.left=Math.min(event.clientX+13,innerWidth-230)+"px";$("chart-tooltip").style.top=(event.clientY-37)+"px";});$("metric-chart").addEventListener("pointerleave",()=>$("chart-tooltip").hidden=true);
$("refresh").addEventListener("click",()=>load(false));$("show-all").addEventListener("click",()=>view("experiments"));$("search").addEventListener("input",()=>renderTable(state.snapshot.study));$("sort").addEventListener("change",()=>renderTable(state.snapshot.study));$("story-key").addEventListener("click",()=>{state.storyAll=false;renderStory(state.snapshot.study);});$("story-all").addEventListener("click",()=>{state.storyAll=true;renderStory(state.snapshot.study);});$("close-drawer").addEventListener("click",closeDetail);$("close-evidence").addEventListener("click",closeEvidence);$("drawer-backdrop").addEventListener("click",()=>{closeEvidence();closeDetail();});
$("graph-open-record").addEventListener("click",()=>{if(state.selectedGraphNode?.round_uid)showDetail(state.selectedGraphNode.round_uid);});
$("graph-open-evidence").addEventListener("click",()=>{if(state.selectedGraphNode?.evidence)showEvidence(state.selectedGraphNode.evidence.id);});
function renderPause(s){const control=s.control,button=$("pause-study");button.disabled=state.controlPending||state.studyLoading||!(savedControl()?.request?.study_id===s.id||control?.can_pause||control?.can_resume||s.paused&&control?.quiescent);button.textContent=state.controlPending?(state.controlAction==="resume"?"正在核对并恢复…":"正在暂停…"):savedControl()?.request?.study_id===s.id?"核对上次请求":s.completed?"研究已结束":control?.restore_pending?"取消恢复":control?.can_resume?"▷ 继续研究":s.paused&&control?.quiescent?"▷ 继续研究":s.paused?control.label:"Ⅱ 暂停研究";button.title=s.paused?control?.resume_reason:control?.scope||"暂无可暂停研究";$("pause-notice").hidden=!s.paused;if(s.paused)$("pause-notice").innerHTML='<div class="pause-brief"><strong>'+escapeHtml(control.label)+'</strong><span>'+escapeHtml(control.restore_pending?"正在核对并恢复原后台，可随时取消。":control.quiescent?"研究接续已暂停，不影响已提交的平台任务。":"正在保留已发调用的回执，完成收尾后保持暂停。")+'</span></div><details class="pause-details"><summary>查看说明</summary><div>'+escapeHtml(control.resume_reason)+'。'+escapeHtml(control.scope)+'</div></details>';}
async function reconcileControl(){
 const saved=savedControl();if(!saved)return;
 state.controlPending=true;state.controlAction=saved.action;
 try{const result=await api("/api/study/control?request_id="+encodeURIComponent(saved.request.request_id));
  if(result.study_id&&result.study_id!==saved.request.study_id)throw new Error("恢复回执的研究身份不一致，请在 Codex 中核对。");
  if(result.recorded&&(result.pending||["verified","restoring","resumed","cancelled","blocked"].includes(result.phase))){rememberControl(null);state.controlError=null;toast(result.label||"已核对原操作回执");}
  else state.controlError="上次请求结果仍待核对（"+saved.request.request_id.slice(0,8)+"）。这里只查询原回执，不会重复启动。";
 }catch(error){state.controlError=error.message;}finally{state.controlPending=false;if(state.snapshot?.study)renderPause(state.snapshot.study);if(state.controlError)banner(state.controlError);}
}
async function controlStudy(){
 const s=state.snapshot?.study,c=s?.control;if(state.controlPending||state.studyLoading)return;
 if(savedControl()){await reconcileControl();await load(false);return;}
 if(!(c?.can_pause||c?.can_resume))return;
 const action=c.can_resume?"resume":"pause",request={study_id:s.id,config_sha256:c.config_sha256,request_id:crypto.randomUUID(),...(action==="resume"?{pause_generation:c.pause_generation}:{})};
 rememberControl({action,request});state.controlAction=action;state.controlError=null;state.controlPending=true;renderPause(s);
 try{
  const response=await fetch("/api/study/"+action,{method:"POST",headers:{Authorization:"Bearer "+token,"Content-Type":"application/json"},body:JSON.stringify(request),signal:AbortSignal.timeout(25000)});
  const result=await response.json();
  if(!response.ok){
   const safe=["study_version_changed_refresh_required","pause_generation_changed_refresh_required","study_not_registered","original_host_resume_unavailable_use_codex","exact_resume_request_required","exact_pause_request_required","dashboard_session_required"];
   if(safe.includes(result.error))rememberControl(null);
   throw new Error(["study_version_changed_refresh_required","pause_generation_changed_refresh_required"].includes(result.error)?"研究或暂停代次已更新，请刷新后核对对象。":"操作未完成（"+(result.error||"未知错误")+"），正在核对原请求；不会自动重发。");
  }
  rememberControl(null);
  toast(result.status==="restoring"?"已受理，正在后台恢复原研究":result.status==="cancelling"?"已请求取消恢复":action==="resume"?(result.control.requested?"恢复请求已有回执；当前仍为"+result.control.label:"已恢复原研究接续"):result.control.label);
 }catch(error){state.controlError=error.name==="TimeoutError"?"请求响应超时，正在查询原回执；不会重复启动。":error.message;banner(state.controlError);}
 finally{
  state.controlPending=false;renderPause(state.snapshot?.study||s);
  if(savedControl())await reconcileControl();
  await load(false);
 }
}
function showResumeHelp(){const s=state.snapshot?.study;if(!s?.paused)return;$("resume-help-reason").textContent=s.control.resume_reason+"。";$("resume-instruction").value=`继续 ResearchPilot 研究「${s.title_zh||s.title}」（研究ID：${s.id}）。先核对原后台、原会话、任务和 Guard 回执；未知结果先对账，不重复提交。核对可接续后恢复持续迭代，沿用已有目标和授权资源范围。定时任务保持暂停。`;$("resume-copy-status").textContent="";$("resume-help").showModal();}
$("pause-study").addEventListener("click",()=>{const s=state.snapshot?.study;if(s?.paused&&!s.control.can_resume&&!s.control.restore_pending)showResumeHelp();else controlStudy();});
$("close-resume-help").addEventListener("click",()=>$("resume-help").close());
$("copy-resume-instruction").addEventListener("click",async()=>{try{await navigator.clipboard.writeText($("resume-instruction").value);$("resume-copy-status").textContent="已复制，请粘贴到 Codex 发送。";}catch{$("resume-copy-status").textContent="请选中上方指令复制，再粘贴到 Codex。";$("resume-instruction").focus();$("resume-instruction").select();}});
const ICONS={project:"M3 7V5a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7z",focus:"M12 2v3m0 14v3M2 12h3m14 0h3M18 12a6 6 0 1 1-12 0 6 6 0 0 1 12 0","panel-open":"M9 3v18M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1m9 6 3 3-3 3","panel-close":"M9 3v18M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1m7 9 3-3m-3 3 3 3",close:"m6 6 12 12M6 18 18 6",refresh:"M20 7v5h-5M4 17v-5h5M19 12a7 7 0 0 0-12-5L4 10m1 2a7 7 0 0 0 12 5l3-3",info:"M12 11v6m0-10v.1M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",sun:"M12 3V1m0 22v-2M3 12H1m22 0h-2M4.2 4.2 2.8 2.8m18.4 18.4-1.4-1.4M4.2 19.8l-1.4 1.4M21.2 2.8l-1.4 1.4M17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0",moon:"M20.5 14.5A9 9 0 0 1 9.5 3.5a9 9 0 1 0 11 11",monitor:"M3 3h18v14H3zM8 21h8m-4-4v4",maximize:"M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5",minimize:"M3 8h5V3m8 0v5h5M3 16h5v5m8 0v-5h5"};
function icon(name){return `<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${ICONS[name]||ICONS.info}"/></svg>`;}
document.querySelectorAll("[data-icon]").forEach(el=>el.innerHTML=icon(el.dataset.icon));
const sidebarKey="sdm-dashboard-sidebar";let sidebarChoice=null;try{sidebarChoice=localStorage.getItem(sidebarKey);}catch{}
function applySidebar(){const collapsed=sidebarChoice===null?matchMedia("(max-width:1000px)").matches:sidebarChoice==="collapsed";document.body.classList.toggle("sidebar-collapsed",collapsed);const button=$("toggle-sidebar");button.setAttribute("aria-expanded",String(!collapsed));button.setAttribute("aria-label",collapsed?"展开项目栏":"收起项目栏");button.title=collapsed?"展开项目栏":"收起项目栏";button.innerHTML=icon(collapsed?"panel-open":"panel-close");const modal=matchMedia("(max-width:620px)").matches&&!collapsed&&!state.graphFocused;document.querySelector(".main").inert=modal;const aside=$("project-sidebar");if(modal){aside.setAttribute("role","dialog");aside.setAttribute("aria-modal","true");aside.setAttribute("aria-label","研究项目");if(!state.mobileNavOpen)requestAnimationFrame(()=>$("sidebar-close").focus());}else{aside.removeAttribute("role");aside.removeAttribute("aria-modal");aside.removeAttribute("aria-label");if(state.mobileNavOpen)$("toggle-sidebar").focus();}state.mobileNavOpen=modal;}
function foldSidebar(){sidebarChoice="collapsed";try{localStorage.setItem(sidebarKey,sidebarChoice);}catch{}applySidebar();}
$("toggle-sidebar").addEventListener("click",()=>{sidebarChoice=document.body.classList.contains("sidebar-collapsed")?"expanded":"collapsed";try{localStorage.setItem(sidebarKey,sidebarChoice);}catch{}applySidebar();});
$("sidebar-close").addEventListener("click",foldSidebar);$("sidebar-overlay").addEventListener("click",foldSidebar);
window.addEventListener("resize",applySidebar);applySidebar();
let themeChoice=document.documentElement.dataset.themeChoice||"auto";const systemTheme=matchMedia("(prefers-color-scheme: dark)");
function applyTheme(choice=themeChoice){if(!["auto","light","dark"].includes(choice))return;themeChoice=choice;const theme=choice==="auto"?(systemTheme.matches?"dark":"light"):choice;document.documentElement.dataset.theme=theme;document.documentElement.dataset.themeChoice=choice;try{localStorage.setItem("sdm-dashboard-theme",choice);}catch{}$("theme-icon").innerHTML=icon(choice==="auto"?"monitor":theme==="dark"?"moon":"sun");const label={auto:"跟随系统",light:"亮色",dark:"暗色"}[choice];$("theme-picker").querySelector("summary").setAttribute("aria-label","外观主题："+label);document.querySelectorAll("[data-theme-choice]").forEach(button=>{const active=button.dataset.themeChoice===choice;button.setAttribute("aria-checked",String(active));button.querySelector(".theme-check").textContent=active?"✓":"";});}
document.querySelectorAll("[data-theme-choice]").forEach(button=>button.addEventListener("click",()=>{applyTheme(button.dataset.themeChoice);$("theme-picker").open=false;$("theme-picker").querySelector("summary").focus();}));$("theme-picker").addEventListener("keydown",e=>{const buttons=[...document.querySelectorAll("[data-theme-choice]")];if(e.key==="Escape"){e.stopPropagation();$("theme-picker").open=false;$("theme-picker").querySelector("summary").focus();return;}if(!$("theme-picker").open)return;if(["ArrowDown","ArrowUp","Home","End"].includes(e.key)){e.preventDefault();const i=buttons.indexOf(document.activeElement);buttons[e.key==="Home"?0:e.key==="End"?buttons.length-1:(i+(e.key==="ArrowDown"?1:buttons.length-1)+buttons.length)%buttons.length].focus();}});systemTheme.addEventListener("change",()=>{if(themeChoice==="auto")applyTheme();});document.addEventListener("pointerdown",e=>{if(!$("theme-picker").contains(e.target))$("theme-picker").open=false;});applyTheme();
function setGraphFocus(value){state.graphFocused=Boolean(value);if(state.graphFocused&&!state.controlHome){state.controlHome=document.createComment("research-control-home");$("pause-study").before(state.controlHome);document.querySelector(".graph-actions").prepend($("pause-study"));}else if(!state.graphFocused&&state.controlHome){state.controlHome.replaceWith($("pause-study"));state.controlHome=null;}document.body.classList.toggle("focus-mode",state.graphFocused);$("graph-focus").setAttribute("aria-pressed",String(state.graphFocused));$("graph-focus").setAttribute("aria-label",state.graphFocused?"退出专注模式":"专注模式");$("graph-focus").title=state.graphFocused?"退出专注模式（Esc）":"专注模式（Esc 退出）";$("graph-focus").innerHTML=icon(state.graphFocused?"minimize":"maximize");requestAnimationFrame(()=>state.map?.resize());}
$("graph-focus").addEventListener("click",()=>setGraphFocus(!state.graphFocused));
$("show-study-info").addEventListener("click",()=>$("study-info").showModal());$("close-study-info").addEventListener("click",()=>$("study-info").close());
view(state.view);
if(!token){connection(false,"需要访问链接");banner("请通过 Codex 提供的完整工作台链接访问；研究数据不会向未授权页面公开。");}else {load(true);if(savedControl())reconcileControl();}

document.addEventListener("input",event=>{if(!event.target.matches("[data-class-filter]"))return;const panel=event.target.closest(".metric-disclosure"),needle=event.target.value.trim().toLowerCase();let count=0;panel.querySelectorAll("[data-class-label]").forEach(row=>{row.hidden=!row.dataset.classLabel.includes(needle);if(!row.hidden)count++;});panel.querySelector("[data-class-empty]").hidden=count>0;});

function selectGraphNode(node){
 state.selectedGraphNode=node;
 const row=state.snapshot.study.rounds.find(r=>r.uid===node?.round_uid);
 $("graph-open-record").disabled=!row;$("graph-open-record").hidden=!row;$("graph-open-record").textContent=row?"查看"+roundName(row.number)+"详情":"实验详情";$("graph-open-evidence").hidden=!node?.evidence;
}
function clearGraphSearch(){const map=state.map;if(!map)return;map.query="";map.searchInput.value="";map.renderSearch();map.refreshCards();}
function planScope(id){
 state.planScopes||=new Map();if(!state.planScopes.has(id))state.planScopes.set(id,window.SDMLineageView.branch(state.snapshot.study.lineage,id));return state.planScopes.get(id);
}
function setPlanBranch(id,open){
 const scope=planScope(id);
 if(!scope.plans.length)return;
 state.expandedPlans.clear();state.openPlanBranch=open?id:null;
 if(open)for(const plan of scope.plans)state.expandedPlans.add(plan);
 clearGraphSearch();state.map.clearSelection();renderGraph(state.snapshot.study);selectGraphNode(null);
 window.SDMLineageView.frame(state.map,open?scope.node_ids:[id]);
}

