"""Read-only scientific ancestry and complete registered activity projection."""
import hashlib
from pathlib import Path
from dashboard_language import brief,verdict

def identity(path):return hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:20]

def mainline(study):
 rows={r['uid']:r for r in study['rounds']};current=study['current'];plan=current.get('proposal')
 head=None
 if not study['completed'] and plan and plan.get('action') not in ('goal-met','stop-research'):
  head={'kind':'plan','title':plan['zh']['title'],'description':plan['zh']['description'],'stage':current['label'],
   'approved':plan.get('approved',False),'trigger_uid':plan.get('parent_uid'),'base_uid':plan.get('base_uid')}
 elif current.get('task') and current['task'].get('uid') not in rows and not study['completed']:
  governing=current.get('governing_plan') or {};b=governing.get('base_uid')
  head={'kind':'trial','title':governing.get('zh',{}).get('title','当前训练任务'),'description':governing.get('zh',{}).get('description',current['label']),'stage':current['label'],'uid':current['task']['uid'],'base_uid':b}
 anchor=head.get('base_uid') if head else study['latest']['uid'] if study.get('latest') else None
 chain=[];seen=set();missing=None
 while anchor and anchor not in seen:
  seen.add(anchor);row=rows.get(anchor)
  if not row:missing=anchor;break
  chain.append(row['uid']);anchor=row.get('base_uid')
 return {'round_uids':list(reversed(chain)),'head':head,'missing_upstream_uid':missing,
  'basis':'按当前方案或最新实验实际选用的基线追溯；结果触发关系另行展示，不伪造顺序因果。'}

def project(data,cfg,root,state,study):
 sid=study['id'];nodes=[];edges=[];known=set();details={};plan_owners={}
 for original in state.get('rounds',[]):
  if original.get('accepted_plan'):
   try:
    ready=data.ref(original['result']);plan_owners[str(Path(original['accepted_plan']['path']).parent.resolve())]='plan:'+ready['uid']
   except (OSError,ValueError,KeyError,TypeError):pass
 def node(id,kind,title,summary='',status='recorded',**extra):
  if id not in known:
   known.add(id);nodes.append({'id':id,'kind':kind,'title':title,'summary':summary,'status':status,**extra})
  return id
 def edge(source,target,kind='evolves',primary=True):
  if source!=target:edges.append({'id':source+'>'+target+':'+kind,'source':source,'target':target,'kind':kind,'primary':primary})
 root_id=node('project:'+sid,'project',study['title_zh'],'全部节点来自已登记的方案、实验、结果和过程回执；未记录的聊天内容不会凭空补齐。')
 by_uid={r['uid']:r for r in study['rounds']}
 for row in study['rounds']:
  uid=row['uid'];pid='plan:'+uid;tid='trial:'+uid;rid='result:'+uid
  node(pid,'plan','实验'+str(row['number'])+'方案：'+row['zh']['title'],row['zh']['description'],'approved',milestone=True,round_uid=uid,round_number=row['number'])
  node(tid,'trial','实验'+str(row['number'])+' · 训练执行','实际任务已完成，使用原任务编号核对。','completed',milestone=False,round_uid=uid,detail_group=pid)
  node(rid,'result','实验'+str(row['number'])+' · 宏平均 F1 '+format(row['metric'],'.4f'),row['zh']['comparison'],'completed',milestone=True,round_uid=uid,
   round_number=row['number'],conclusion='刷新历史最优' if row['new_best'] else '保留本次实际结果，未替换更优候选')
  edge('result:'+row['base_uid'] if row.get('base_uid') in by_uid else root_id,pid,'branches');edge(pid,tid,'executes');edge(tid,rid,'produces')
  if row.get('plan') and row['plan'].get('parent_uid') in by_uid and row['plan']['parent_uid']!=row.get('base_uid'):
   edge('result:'+row['plan']['parent_uid'],pid,'informed_by',False)
 # Older frozen attempts remain discoverable through the verified result references.
 roots={Path(root)}
 for original in state.get('rounds',[]):
  p=Path(original.get('result',{}).get('path',''))
  if len(p.parents)>3 and p.parents[2].name=='component-pipeline':roots.add(p.parents[3])
 def reviews(review_root,parent,label,owner):
  if not review_root:return
  for accepted in sorted(Path(review_root).glob('proposal-review/*/round-*/accepted.json')):
   try:
    raw=data.read(accepted);response=raw['response'];proposal=raw['proposal'];id='review:'+identity(accepted)
    ref=data.evidence_file(accepted,'原始'+label+'记录');v=response.get('verdict')
    text=brief(data.project,proposal.get('research_revision',{}).get('intervention',proposal.get('hypothesis','')),label)
    node(id,'plan_review',label+' · 第'+str(proposal.get('revision','?'))+'版 · '+verdict(v),
      text['title']+'。审议结论为“'+verdict(v)+'”；已记录 '+str(len(response.get('challenges',[])))+' 项质疑。原始记录保留。',
      {'approve':'approved','revise':'request_changes','needs-evidence':'awaiting_input','reject':'rejected','stalled':'blocked'}.get(v,'recorded'),
      milestone=False,evidence=ref,review_verdict=v,detail_group=owner)
    edge(parent,id,'reviewed');details[id]={'node':next(n for n in nodes if n['id']==id),'record':{'审议结论':verdict(v),'质疑数量':len(response.get('challenges',[]))}}
   except (OSError,ValueError,KeyError,TypeError):continue
 for store in sorted(roots):
  try:previous=data.read(store/'component-pipeline/pipeline.json')
  except (OSError,ValueError):continue
  for attempt in previous.get('attempts',[]):
   source=attempt.get('source',{});uid=None;actual_result=None
   if attempt.get('result'):
    try:actual_result=data.ref(attempt['result']);uid=actual_result['uid']
    except (OSError,ValueError,KeyError,TypeError):pass
   parent='result:'+uid if uid in by_uid else root_id
   if actual_result and actual_result.get('schema')=='sdm-component-diagnosis-ready/v1' and actual_result.get('valid_training_round') is False:
    parent=node('failed-trial:'+uid,'trial','训练执行失败 · 准备阶段',
      '实际平台任务失败，尚未形成有效训练结果；保留此记录，不计为有效实验，也不赋质量分数。',
      'failed',milestone=True,task_uid=uid,evidence=data.evidence(attempt['result'],'原始失败诊断'))
    edge(root_id,parent,'executes')
   owner=plan_owners.get(str(Path(attempt['replan_root']).resolve())) if attempt.get('replan_root') else None
   if not owner and store==Path(root) and attempt is previous.get('attempts',[])[-1] and study['current'].get('proposal'):owner='current:'+sid
   owner=owner or ('plan:'+uid if uid in by_uid else root_id)
   group=parent
   if attempt.get('replan_root') or attempt.get('implementation_root') or attempt.get('code_review_root'):
    group=node('continuation:'+identity(attempt.get('replan_root') or store/attempt.get('id','initial')),'branch',
      ('实验'+str(by_uid[uid]['number'])+'结果后' if uid in by_uid else '执行准备')+'的方案迭代',
      '本组记录该结果触发的后继方案、审议与实施，不表示此前实验使用了这些后继改动。','recorded',milestone=False,detail_group=owner)
    edge(parent,group,'evolves')
   reviews(attempt.get('replan_root'),group,'后继方案审议',owner)
   reviews(attempt.get('code_review_root'),group,'后继代码审议',owner)
   for field,label,kind in [('analysis','结果分析','assessment'),('frozen_implementation','代码实施与冻结','run'),('diagnosis','失败诊断','investigation')]:
    ref=attempt.get(field)
    if not ref:continue
    try:
     actual=data.ref(ref);id=field+':'+identity(ref['path']);registered=data.evidence(ref,'原始'+label)
     node(id,kind,label+' · '+('实验'+str(by_uid[uid]['number'])+('结果' if field=='analysis' else '结果后的接续') if uid in by_uid else '执行准备'),
      '此节点来自真实登记回执；过程完成不等于训练质量提升。','completed',milestone=False,evidence=registered,detail_group=('plan:'+uid if uid in by_uid and field in ('analysis','diagnosis') else owner))
     edge(parent if field in ('analysis','diagnosis') else group,id,'research_step')
     if field=='diagnosis' and actual.get('uid'):
      failed=node('failed-trial:'+actual['uid'],'trial','训练执行失败 · 准备阶段',
        '平台任务未形成有效训练结果；失败保留，不计为有效实验，也不赋质量分数。','failed',milestone=False,task_uid=actual['uid'],evidence=registered)
      edge(root_id,failed,'executes');edge(failed,id,'produces')
    except (OSError,ValueError,KeyError,TypeError):continue
   continuation=attempt.get('continuation') or {}
   if continuation.get('phase')=='blocked':
    ref=continuation.get('preflight_receipt');id='block:'+identity(ref or store/attempt.get('id','initial'))
    node(id,'limitation','历史阻塞记录' if store!=Path(root) else '当前阻塞记录',
     '保留原始失败或输入容量回执；历史阻塞不代表当前仍受阻。','blocked',milestone=False,
     evidence=data.evidence_file(ref,'原始阻塞回执') if ref else None,detail_group=owner)
    edge(group,id,'research_step')
 current=study['current'];focus='result:'+study['latest']['uid'] if study.get('latest') else root_id
 task=current.get('task') or {};pending_uid=task.get('uid')
 if pending_uid and pending_uid not in by_uid:
  governing=current.get('governing_plan') or {};b=governing.get('base_uid');parent='result:'+b if b in by_uid else root_id
  pid=node('plan:'+pending_uid,'plan','当前已提交方案',brief(data.project,governing.get('intervention',''))['description'],'approved',milestone=True)
  focus=node('trial:'+pending_uid,'trial','当前训练任务',current['label'],'running' if task.get('platform_status')=='running' else 'pending',milestone=False,task_uid=pending_uid,detail_group=pid)
  edge(parent,pid,'branches');edge(pid,focus,'executes')
 if not study['completed'] and current.get('proposal') and current['proposal'].get('action') not in ('goal-met','stop-research'):
  plan=current['proposal'];parent='result:'+plan.get('base_uid','') if plan.get('base_uid') in by_uid else root_id
  focus=node('current:'+sid,'plan','当前方案：'+plan['zh']['title'],plan['zh']['description'],
    'paused' if study.get('paused') else 'blocked' if current['needs_attention'] else 'approved' if plan.get('approved') else 'proposed',milestone=True)
  edge(parent,focus,'branches')
  if plan.get('parent_uid') in by_uid and plan['parent_uid']!=plan.get('base_uid'):
   edge('result:'+plan['parent_uid'],focus,'informed_by',False)
 live='completed' if study['completed'] else 'paused' if study.get('paused') or study['needs_attention'] else 'waiting' if current['stage']=='watching' and task.get('platform_status')!='running' else 'running'
 if study.get('engineering_takeovers'):
  id=node('engineering:'+sid,'correction','工程接管累计 '+str(study['engineering_takeovers'])+' 次',
    '这是登记索引中的工程介入累计数，不是新增实验，也不代表全程无人接管。','recorded',milestone=False)
  edge(root_id,id,'research_step')
 groups={}
 for item in nodes:
  if item['id']==root_id or item['kind'] in ('plan','result'):continue
  owner=item.get('detail_group',root_id)
  if owner not in known:owner=root_id
  item['detail_group']=owner;groups.setdefault(owner,[]).append(item['id'])
 return {'project_id':sid,'root_id':root_id,'nodes':nodes,'edges':edges,'details':details,'detail_groups':groups,

  'execution_location':{'state':live,'phase':current['stage'],'phase_label':current['label'],'summary':current['label'],
    'active_node_ids':[focus] if live=='running' else [],'focus_node_id':focus,
    'retained_node_ids':['result:'+study['incumbent']['uid']] if study.get('incumbent') else [],
    'source':'SDM 原始状态投影'},'coverage':'覆盖已登记实验及可追溯冻结运行中的审议、分析、实施、失败与阻塞；未登记的聊天不计入。'}
