"""Read-only research projection for the standalone dashboard. No execution imports."""
from contextlib import closing
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path
from review_storage import native_path

STAGES={'reviewing-initial':'首轮方案与代码审议','watching':'观察训练任务','result-ready':'分析实验结果','reviewing-plan':'方案审议',
 'preparing-implementation':'准备实施','implementing':'实施研究方案','freezing':'核验代码',
 'preflighting-code':'提交前核验','reviewing-code':'代码审议','submitting':'提交训练',
 'initial-submitting':'提交初始训练','needs-attention':'需要关注','research-goal-met':'已达目标',
 'research-stopped':'研究已结束'}
LEVELS={'model':'模型结构','architecture':'模型结构','representation':'特征表征','training':'训练策略',
 'training-objective':'训练目标','training-process':'训练策略','runtime-resource':'执行资源',
 'tuning':'参数调整','data-measurement':'数据与测量','none':'基线','data':'数据方案'}
REASONS={'model-input-capacity':'评审输入超过容量，需要修复输入打包；本次调用尚未发出。',
 'original-model-outcome-unresolved':'原模型调用结果待对账，暂停派发以避免重复执行。',
 'authentication-blocked':'平台认证需要处理。','integrity-blocked':'记录完整性检查未通过。',
 'no-new-evidence-or-plan-progress':'尚无新的证据或方案进展。','diagnostic-no-new-evidence':'需要新的诊断证据。'}


def digest(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def number(value):
 return value if type(value) in (int,float) and math.isfinite(value) else None


def text(value,limit=6000):
 return value[:limit] if isinstance(value,str) else ''


class DashboardData:
 def __init__(self,project):
  self.project=Path(project).resolve();self.registry={};self.cache={}
  if not self.project.is_dir():raise ValueError('existing_project_required')

 def read(self,path,expected=None):
  path=Path(path).resolve();stat=native_path(path).stat();key=(str(path),stat.st_mtime_ns,stat.st_size,expected)
  if key in self.cache:return self.cache[key]
  if stat.st_size>8*1024*1024:raise ValueError('bounded_research_record_required')
  raw=native_path(path).read_bytes()
  if expected and hashlib.sha256(raw).hexdigest()!=expected.removeprefix('sha256:'):
   raise ValueError('research_evidence_changed')
  value=json.loads(raw)
  if len(self.cache)>3000:self.cache.clear()
  self.cache[key]=value;return value

 def ref(self,reference):return self.read(reference['path'],reference['sha256'])

 def evidence(self,ref,label):
  if not isinstance(ref,dict) or not ref.get('path') or not ref.get('sha256'):return None
  path=str(Path(ref['path']).resolve());sha=ref['sha256'].removeprefix('sha256:')
  key=hashlib.sha256((path+'|'+sha).encode()).hexdigest();self.registry[key]={'path':path,'sha256':sha,'label':label}
  return {'id':key,'label':label}

 def evidence_file(self,path,label):
  path=Path(path)
  if not native_path(path).exists():return None
  raw=native_path(path).read_bytes()
  if len(raw)>8*1024*1024:return None
  return self.evidence({'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()},label)

 def discover(self):
  folder=self.project/'.bmad-ml';indexes=[folder/'CURRENT.json',*sorted(folder.glob('*/CURRENT.json'))]
  studies=[];seen=set();self.discovery_errors=[]
  for path in indexes[:64]:
   if not path.is_file():continue
   try:
    index=self.read(path);config=Path(index['config']).resolve()
    if index.get('schema')!='bmad-ml-active-study/v1':raise ValueError('研究登记格式不正确')
    if not config.is_relative_to(self.project):raise ValueError('研究配置不在当前项目内，请打开所属项目的工作台')
    cfg=self.read(config);root=Path(cfg['root']).resolve()
    if not root.is_relative_to(self.project):raise ValueError('研究记录不在当前项目内，请打开所属项目的工作台')
    if cfg.get('schema') not in ('sdm-event-service/v2','sdm-event-service/v3'):raise ValueError('研究配置不是支持的控制器配置')
    sid=cfg['project_id']
    if sid in seen:continue
    seen.add(sid);studies.append((index,cfg,root))
   except (OSError,ValueError,KeyError,TypeError):
    self.discovery_errors.append({'id':None,'index':str(path.relative_to(self.project)),
     'message':'研究登记无法读取或超出当前项目范围；请核对所属项目、登记格式和配置路径。'})
  return studies

 def title(self,cfg):
  spec=cfg['component_pipeline'];workload=spec.get('workload',{})
  if workload.get('source_ref'):
   try:
    source=self.ref(workload['source_ref'])
    for key in ('dataset','dataset_name','name'):
     if isinstance(source.get(key),str):return source[key][:100]
   except (OSError,KeyError,ValueError,TypeError):pass
  import re
  matched=re.search(r'public\s+([A-Za-z][A-Za-z0-9_]+)\s+TRAIN',spec.get('objective',''))
  if matched:return matched.group(1)
  sid=re.sub(r'-[0-9]{8}$','',cfg['project_id'].removeprefix('sdm-'))
  sid=re.sub(r'-(loop|continuous)$','',sid)
  return sid.replace('-',' ').title()

 def plan(self,reference):
  value=self.ref(reference);proposal=value.get('proposal',{});direction=value.get('research_revision',{})
  decision=direction.get('candidate_decision',{})
  fields={k:text(proposal.get(k)) for k in ('hypothesis','evidence_summary','counterevidence','alternatives',
   'comparison','expected_observations','outcome_actions','cost_and_queue','preflight_and_stop')}
  evidence=[self.evidence(reference,'原始研究方案')]
  reviewer=None
  if value.get('accepted_review_ref'):
   accepted=self.ref(value['accepted_review_ref']);response=accepted.get('response',{})
   reviewer={'verdict':response.get('verdict'),'rationale':text(response.get('rationale')),
    'challenges':response.get('challenges',[]),'model':response.get('reviewer',{}).get('model',{}).get('id')}
   evidence.append(self.evidence(value['accepted_review_ref'],'原始方案审议'))
  return {'intervention':text(direction.get('intervention')),'action':direction.get('action'),
   'change_level':direction.get('change_level'),'change_label':LEVELS.get(direction.get('change_level'),'方案调整'),
   'parent_uid':value.get('parent_task_uid'),'base_uid':value.get('selected_candidate',{}).get('uid'),
   'decision':{k:text(v) for k,v in decision.items() if isinstance(v,str)},'proposal':fields,
   'review':reviewer,'evidence':[r for r in evidence if r],'approved':True}

 def rounds(self,state,target=None):
  rows=[];issues=[];results={}
  for index,original in enumerate(state.get('rounds',[]),1):
   try:
    ready=self.ref(original['result'])
    if ready.get('schema') not in ('sdm-component-result-ready/v1','sdm-project-result-ready/v1') or ready.get('valid_training_round') is not True:
     raise ValueError('valid_training_result_required')
    artifact=Path(ready['artifact_root'])/'result.json';expected=ready['artifact_sha256']['result.json']
    result=self.read(artifact,expected)
    metric_name=ready.get('metric_name','validation.macro_f1');metric_key=metric_name.split('.',1)[-1]
    metric=number(result.get('validation',{}).get(metric_key))
    if target and (target.get('metric')!=metric_name or target.get('operator')!=ready.get('metric_operator','>=')):raise ValueError('registered_metric_definition_mismatch')
    if metric is None or metric<0 or (metric_key in ('accuracy','macro_f1','mean_iou') and metric>1) or abs(metric-ready['primary_metric'])>1e-10:
     raise ValueError('registered_metric_mismatch')
    plan=self.plan(original['accepted_plan']) if original.get('accepted_plan') else None
    row={'number':index,'uid':ready['uid'],'metric':metric,'metric_key':metric_key,
     'metric_operator':ready.get('metric_operator','>='),'metric_label':{'macro_f1':'宏平均 F1','mean_iou':'均值 IoU','accuracy':'准确率','mae':'平均绝对误差','mse':'均方误差'}.get(metric_key,metric_key),'accuracy':number(result.get('validation',{}).get('accuracy')),
     'selected_epoch':result.get('selected_epoch'),'elapsed_seconds':number(result.get('elapsed_seconds')),
     'config':{k:v for k,v in result.get('config',{}).items() if isinstance(v,(str,int,float,bool)) and (not isinstance(v,str) or len(v)<150)},
     'code_sha256':result.get('code_sha256'),'base_uid':plan.get('base_uid') if plan else None,
     'intervention':plan['intervention'] if plan else '首轮训练，记录初始结果供后续比较',
     'change_label':plan['change_label'] if plan else '初始实验','plan':plan,
     'validation':result.get('validation',{}),'train':result.get('train',{}),
     'curve':result.get('history',[]),'verified':True,
     'evidence':[self.evidence(original['result'],'结果回收回执'),self.evidence({'path':str(artifact),'sha256':expected},'原始指标与训练曲线')]}
    if plan:row['evidence'].extend(plan['evidence'])
    results[row['uid']]=result;rows.append(row)
   except (OSError,ValueError,KeyError,TypeError) as error:
    issues.append({'round':index,'reason':str(error) if isinstance(error,ValueError) else 'record_unavailable'})
  by_uid={r['uid']:r for r in rows};best=None
  for row in rows:
   base=by_uid.get(row['base_uid']);row['base_number']=base['number'] if base else None
   row['delta_base']=row['metric']-base['metric'] if base else None
   row['parameter_changes']=[{'name':k,'previous':base['config'].get(k),'current':v} for k,v in row['config'].items()
    if base and k not in ('round','expected_minutes') and base['config'].get(k)!=v]
   from evaluation_metrics import classification_metrics,reported_scalar_metrics
   row['class_metrics']=classification_metrics(results[row['uid']],results.get(base['uid']) if base else None)
   row['reported_metrics']=reported_scalar_metrics(results[row['uid']])
   row['code_changed']=bool(base and row['code_sha256']!=base['code_sha256'])
   row['new_best']=best is None or (row['metric']<best-1e-12 if row['metric_operator']=='<=' else row['metric']>best+1e-12)
   if row['new_best']:best=row['metric']
   row['best_so_far']=best
   row['result_decision']=None
  for row in rows:
   if row['plan'] and row['plan']['parent_uid'] in by_uid:by_uid[row['plan']['parent_uid']]['result_decision']=row['plan']['decision']
  return rows,issues

 def jobs(self,root):
  path=root/'delivery.sqlite';items=[]
  if not path.exists():return items
  with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
   db.execute('pragma query_only=on')
   for job,status,raw,updated in db.execute('select id,status,detail,updated from jobs order by updated desc limit 12'):
    detail=json.loads(raw);outcome=detail.get('coordinator_result',{})
    items.append({'id':job,'status':status,'at':updated,'kind':detail.get('kind'),
     'result_status':outcome.get('status') or detail.get('coordinator_status')})
  return items

 def current(self,cfg,root,state,rows):
  attempt=state.get('attempts',[{}])[-1];stage=attempt.get('stage');continuation=attempt.get('continuation') or {}
  reason=continuation.get('category') or attempt.get('reason');data={'stage':stage,'label':STAGES.get(stage,stage or '尚未开始'),
   'needs_attention':stage=='needs-attention','reason':reason,'reason_text':REASONS.get(reason,text(attempt.get('reason'))),
   'observation':attempt.get('observation'),'preflight':attempt.get('execution_preflight'),
   'evidence':[],'model_activity':[],'proposal':None,'task':None,'governing_plan':None,
   'initial_review_phase':attempt.get('initial_review_phase')}
  if stage=='reviewing-initial':data['label']='首轮代码审议' if attempt.get('initial_review_phase')=='code' else '首轮方案审议'
  if attempt.get('science_plan'):
   try:data['governing_plan']=self.plan(attempt['science_plan'])
   except (OSError,ValueError,KeyError,TypeError):pass
  folder=root/'component-pipeline'/attempt.get('id','initial');monitor=folder/'source/monitor.json'
  if monitor.exists():
   observed=self.read(monitor);data['task']={k:observed.get(k) for k in ('uid','platform_status','observed_at','next_observation_at')}
  if stage=='watching' and data['task']:
   status=data['task']['platform_status'];data['label']={'image_pulling':'准备训练镜像','creating':'准备训练环境','pending':'任务排队中','queued':'任务排队中','running':'训练进行中',
    'succeeded':'回收训练产物','failed':'分析执行异常'}.get(status,'观察训练任务')
  if continuation.get('preflight_receipt'):
   ref=self.evidence_file(continuation['preflight_receipt'],'阻塞原因回执')
   if ref:data['evidence'].append(ref)
  root_names=('replan_root','code_review_root') if stage=='reviewing-code' else ('code_review_root','replan_root')
  for key in root_names:
   if not attempt.get(key):continue
   review_root=Path(attempt[key]);plan_path=review_root/'research-plan.json';proposal_path=review_root/'proposal.json'
   if key=='replan_root':
    if plan_path.exists():
     ref=self.evidence_file(plan_path,'当前研究方案')
     if ref:data['proposal']=self.plan(self.registry[ref['id']])
    elif proposal_path.exists():
     proposal=self.read(proposal_path);direction=proposal.get('research_revision',{})
     data['proposal']={'approved':False,'intervention':text(direction.get('intervention')),
      'proposal':{k:text(proposal.get(k)) for k in ('hypothesis','counterevidence','expected_observations')},
      'decision':direction.get('candidate_decision',{}),'base_uid':direction.get('candidate_decision',{}).get('base_task_uid'),
      'parent_uid':data['task'].get('uid') if data['task'] else None,
      'action':direction.get('action'),'evidence':[self.evidence_file(proposal_path,'当前方案草案')]}
   if stage in ('reviewing-plan','reviewing-code','reviewing-initial','needs-attention'):
    wanted='code_review_root' if stage in ('reviewing-code','reviewing-initial') or str(attempt.get('reason','')).startswith(('original-code-review:','original-initial-review:')) else 'replan_root'
    if key!=wanted:continue
    calls=sorted(review_root.glob('proposal-review/*/model-calls/*/request.json'))
    for path in calls[-6:]:
     request=self.read(path);done=path.parent/'backend-completed.json';outcome=self.read(done).get('status') if done.exists() else None
     data['model_activity'].append({'model':request.get('request',{}).get('model'),'role':'方案生成者' if request.get('request',{}).get('purpose')=='proposal' else '独立质询者',
      'status':outcome or ('未发出' if not (path.parent/'intent.json').exists() else '执行中'),'at':path.stat().st_mtime})
  return data

 def intent(self,cfg):
  policy=cfg.get('component_pipeline',{}).get('sdm_intent')
  if policy is None:return None
  seed=self.ref(policy['seed'])
  if (seed.get('schema')!='sdm-research-intent/v1' or seed.get('project_id')!=cfg['project_id']
      or seed.get('engine_contract_digest')!=policy.get('engine_contract_digest')):
   raise ValueError('dashboard_sdm_intent_identity_changed')
  # Project the core-authored receipt, never decide scientific acceptance here.
  return {**seed['projection'],'evidence':self.evidence(policy['seed'],'研究意图与规范校验原件'),
          'scope':seed['scope'],'remaining_integration_steps':seed['remaining_integration_steps']}

 def study(self,index,cfg,root,entry=None):
  path=root/'component-pipeline/pipeline.json';state=self.read(path)
  if state.get('config_digest','').removeprefix('sha256:')!=digest(cfg):raise ValueError('dashboard_study_identity_changed')
  target=cfg['component_pipeline'].get('decision_policy',{}).get('target')
  rows,issues=self.rounds(state,target);current=self.current(cfg,root,state,rows)
  next_plan=current.get('proposal')
  if next_plan and next_plan.get('approved'):
   parent=next((row for row in rows if row['uid']==next_plan.get('parent_uid')),None)
   if parent:parent['result_decision']=next_plan.get('decision');parent['next_plan_pending_execution']=True
  supervision=self.read(root/'supervision.json') if (root/'supervision.json').exists() else {}
  status=state['status'];completed=status in ('research-goal-met','research-stopped')
  host_status=supervision.get('status','unknown');needs=current['needs_attention'] or bool(issues)
  if not completed and host_status in ('stopped','completed','needs-reconciliation','stalled-no-progress'):needs=True
  label='已达目标' if status=='research-goal-met' else '研究已结束' if completed else '需要关注' if needs else '研究进行中'
  title=self.title(cfg);best=(min if target and target.get('operator')=='<=' else max)(rows,key=lambda x:x['metric']) if rows else None
  value={'id':cfg['project_id'],'title':title,'objective':text(cfg['component_pipeline'].get('objective'),1800),
   'status':status,'status_label':label,'needs_attention':needs,'completed':completed,'host_status':host_status,
   'goal':state.get('candidate_goal') or {'target':cfg['component_pipeline'].get('decision_policy',{}).get('target')},
   'incumbent':{'uid':best['uid'],'metric':best['metric'],'number':best['number']} if best else None,
   'latest':{'uid':rows[-1]['uid'],'metric':rows[-1]['metric'],'number':rows[-1]['number']} if rows else None,
   'metric':{'name':(target or {}).get('metric','validation.macro_f1'),'operator':(target or {}).get('operator','>='),
      'label':rows[0]['metric_label'] if rows else {'validation.mean_iou':'均值 IoU','validation.mae':'平均绝对误差','validation.mse':'均方误差','validation.accuracy':'准确率'}.get((target or {}).get('metric'),'宏平均 F1')},
   'rounds':rows,'round_count':len(rows),'current':current,'issues':issues,'jobs':self.jobs(root),
   'engineering_takeovers':index.get('engineering_takeovers'),
   'updated_at':path.stat().st_mtime,'timer':index.get('timer','unknown'),'read_only':True}
  from lifecycle_view import project as lifecycle_view
  value['lifecycle_view']=lifecycle_view(current.get('stage'),current.get('initial_review_phase'))
  from research_acceptance import read_projection as acceptance_project
  acceptance=acceptance_project(cfg,state)
  if acceptance is not None:
   # Read-only title mapping; original evidence, status and qualification stay owned by the freeze.
   from lifecycle_view import catalog
   active={r['step']:r for r in catalog()['steps']}
   displayed={**acceptance,'steps':[dict(row) for row in acceptance['steps']]}
   for row in displayed['steps']:
    current_definition=active.get(row['step'])
    if (current_definition and row.get('sdm_steps')==current_definition['sdm_steps']
        and row['title']!=current_definition['title']):
     row['recorded_title']=row['title'];row['title']=current_definition['title']
     row['detail']=('历史记录名称：'+row['recorded_title']+'；只映射显示名称，原接受状态和证据不变。'+
       ('首轮结果不自动取得基线复现、锁定资格。' if row['step']==3 else ''))
   value['sdm_acceptance']=displayed
  intent=self.intent(cfg)
  if intent is not None:value['sdm_intent']=intent
  from dashboard_control import state as pause_state
  value['control']=pause_state(cfg,root,completed)
  value['paused']=bool(value['control']['requested'] and not completed)
  if value['paused']:
   value['status_label']=value['control']['label'];value['needs_attention']=bool(issues)
   current['original_stage']=current['stage'];current['stage']='user-paused';current['label']=value['control']['label']
   current['needs_attention']=False;current['reason_text']=value['control']['scope']
  from research_progress import probe as progress_probe,runtime_view as progress_view
  host={'owner_alive':False,'worker_ready':False,'paused':value['paused']}
  if not completed and not value['paused']:
   try:host=progress_probe(cfg)
   except (OSError,ValueError,KeyError,TypeError):
    host={'owner_alive':None,'worker_ready':False,'paused':False}
   value['host_alive']=host['owner_alive']
   if host['owner_alive'] is not True or host['worker_ready'] is not True:
    value['host_status']='offline' if host['owner_alive'] is False else 'unverified'
    if not current['needs_attention']:
     value['status_label']='后台已退出' if host['owner_alive'] is False else '研究线程待核验'
    value['needs_attention']=True;current['needs_attention']=True
    note=('后台控制器未在运行，分析记录不会自行触发下一次提交；请沿原恢复入口核对。'
     if host['owner_alive'] is False else '后台进程与研究线程尚未同时核验，不能据此判断自主迭代正在运行。')
    current['reason_text']=(current.get('reason_text','')+' '+note).strip()
  value['progress']=progress_view(state,owner_alive=host['owner_alive'],
      worker_ready=host['worker_ready'],paused=value['paused'] or host['paused'])
  if value['progress']['state']=='blocked':
   value['status_label']=value['progress']['label'];value['needs_attention']=True;current['needs_attention']=True
   current['reason_text']=value['progress']['detail']
  if value['control'].get('restore_pending'):
   value['status_label']=value['control']['label'];current['label']=value['control']['label'];current['reason_text']='正在核对并恢复原研究后台；可随时取消。'
  from research_task_scope import handoff,analysis_available
  analysis_done=analysis_available(state)
  value['task_handoff']=handoff(cfg,state,value['progress'],analysis_done,entry)
  if entry:value['task_handoff']['entry_evidence']=[self.evidence(ref,'入口评测'+('分析' if key=='analysis' else '结果')) for key,ref in entry['evidence'].items()]
  value['title_zh']={'Computers':'计算机负载分类','DistalPhalanxOutlineCorrect':'指骨轮廓分类'}.get(title,title)
  thread=None
  context=Path(cfg.get('research_context',{}).get('root',self.project/'.missing'))/'context.json'
  if context.resolve().is_relative_to(self.project) and context.is_file():
   try:thread=self.read(context).get('thread_id')
   except (OSError,ValueError,KeyError):pass
  value['binding']={'project_root':str(self.project),'study_id':cfg['project_id'],'execution_thread_id':thread,
   'conversation_sync':False,'origin_chat_id':index.get('origin_codex_chat_id'),
   'explanation':'通过同一工作目录和研究索引关联；当前聊天全文未同步。后台原生实施会话与当前聊天窗口不同。'}
  from dashboard_language import annotate
  if cfg.get('working_sessions'):
   from working_sessions import snapshot as session_snapshot
   if all(Path(cfg['working_sessions'][role]['root']).resolve().is_relative_to(self.project) for role in ('main','review')):
    value['binding']['working_sessions']=session_snapshot(cfg['working_sessions'])
    value['binding']['explanation']='同一研究入口关联持续的研究主会话与独立评审会话；主会话承接设计和实施，评审结论通过原记录返回。当前聊天全文不自动同步。'
  from dashboard_lineage import mainline,project as lineage
  annotate(self.project,value);value['mainline']=mainline(value)
  value['lineage']=lineage(self,cfg,root,state,value)
  return value

 def entry_task(self,entry):
  single=entry['mode']=='single-evaluation';completed=single and entry['analysis_completed'] is True
  evidence=[self.evidence(ref,'评测'+('分析' if key=='analysis' else '结果')) for key,ref in entry['evidence'].items()]
  return {'id':entry['id'],'entry_task':True,'title':entry['objective'],'title_zh':entry['objective'],
   'task_mode':entry['mode'],'task_mode_label':entry['mode_label'],'status':'evaluation-completed' if completed else 'entry-pending',
   'status_label':entry['label'],'task_handoff':entry,'needs_attention':bool(entry.get('needs_attention')),'completed':completed,'paused':False,
   'round_count':0,'incumbent':None,'updated_at':entry['updated_at'],'entry_evidence':evidence,
   'control':{'can_pause':False,'can_resume':False,'scope':'该记录不拥有研究控制器。'},
   'binding':{'project_root':str(self.project),'study_id':entry['id'],'explanation':entry['scope']}}

 def snapshot(self,selected=None):
  studies=[];errors=[];detail=None
  from research_task_scope import tasks
  entry_tasks,task_errors=tasks(self.project);errors.extend(task_errors)
  by_study={r['study_id']:r for r in entry_tasks if r.get('study_id')}
  linked=set();selection_alias=None
  selected_entry=next((r for r in entry_tasks if r['id']==selected),None)
  canonical_selected=selected_entry.get('study_id') if selected_entry and selected_entry.get('study_id') else selected
  discovered=self.discover();errors.extend(self.discovery_errors)
  for index,cfg,root in discovered:
   try:
    entry=by_study.get(cfg['project_id'])
    if entry and Path(entry['config']['path']).resolve()!=Path(index['config']).resolve():
     errors.append({'id':entry['id'],'message':'入口关联配置与研究登记不一致，不能据此确认持续迭代。'});entry=None
    value=self.study(index,cfg,root,entry)
    if entry:linked.add(entry['task_id'])
    studies.append({**{'task_mode':value['task_handoff']['mode'],'task_mode_label':value['task_handoff']['mode_label']},**{k:value[k] for k in ('id','title','title_zh','status_label','needs_attention','completed','paused','round_count','incumbent','updated_at')}})
    if cfg['project_id']==canonical_selected or selected is None and detail is None:detail=value
   except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as error:
    errors.append({'id':cfg.get('project_id'),'message':str(error) if isinstance(error,ValueError) else '研究记录暂不可读'})
  for entry in entry_tasks:
   if entry['task_id'] in linked:continue
   value=self.entry_task(entry)
   studies.append({k:value[k] for k in ('id','title','title_zh','status_label','needs_attention','completed','paused','round_count','incumbent','updated_at','task_mode','task_mode_label')})
   if value['id']==selected or selected is None and detail is None:detail=value
  if selected is not None and not any(x['id']==selected for x in studies):
   if selected_entry and selected_entry['task_id'] in linked and detail and detail['id']==canonical_selected:
    selection_alias={'requested':selected,'canonical':canonical_selected}
   else:raise KeyError('study_not_registered')
  body={'project':{'name':self.project.name,'root':str(self.project),'registration':'发现已登记研究，不创建独立副本'},'schema':'sdm-research-dashboard/v1','studies':studies,'study':detail,'errors':errors,'read_only':True}
  if selection_alias:body['selection_alias']=selection_alias
  body['revision']=digest(body);body['served_at']=time.time();return body

 def preview(self,identity):
  if identity not in self.registry:self.snapshot()
  if identity not in self.registry:raise KeyError('unregistered_evidence')
  ref=self.registry[identity];path=native_path(Path(ref['path']));size=path.stat().st_size
  if size>8*1024*1024:raise ValueError('bounded_evidence_preview_required')
  raw=path.read_bytes()
  if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('research_evidence_changed')
  if path.suffix not in ('.json','.jsonl','.md','.txt','.py'):raise ValueError('text_evidence_only')
  return {'label':ref['label'],'verified':True,'text':raw[:128*1024].decode('utf-8',errors='replace'),
   'truncated':len(raw)>128*1024,'bytes':len(raw),'sha256':ref['sha256']}
