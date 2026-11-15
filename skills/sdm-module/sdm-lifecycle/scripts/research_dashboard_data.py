"""Read-only research projection for the standalone dashboard. No execution imports."""
from contextlib import closing
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path
from review_storage import native_path

STAGES={'watching':'观察训练任务','result-ready':'分析实验结果','reviewing-plan':'方案审议',
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
  studies=[];seen=set()
  for path in indexes[:64]:
   if not path.is_file():continue
   try:
    index=self.read(path);config=Path(index['config']).resolve()
    if index.get('schema')!='bmad-ml-active-study/v1' or not config.is_relative_to(self.project):continue
    cfg=self.read(config);root=Path(cfg['root']).resolve()
    if not root.is_relative_to(self.project) or cfg.get('schema') not in ('sdm-event-service/v2','sdm-event-service/v3'):continue
    sid=cfg['project_id']
    if sid in seen:continue
    seen.add(sid);studies.append((index,cfg,root))
   except (OSError,ValueError,KeyError,TypeError):continue
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
   evidence.append(self.evidence(value['accepted_review_ref'],'原始方案评审'))
  return {'intervention':text(direction.get('intervention')),'action':direction.get('action'),
   'change_level':direction.get('change_level'),'change_label':LEVELS.get(direction.get('change_level'),'方案调整'),
   'parent_uid':value.get('parent_task_uid'),'base_uid':value.get('selected_candidate',{}).get('uid'),
   'decision':{k:text(v) for k,v in decision.items() if isinstance(v,str)},'proposal':fields,
   'review':reviewer,'evidence':[r for r in evidence if r],'approved':True}

 def rounds(self,state):
  rows=[];issues=[];results={}
  for index,original in enumerate(state.get('rounds',[]),1):
   try:
    ready=self.ref(original['result'])
    if ready.get('schema')!='sdm-component-result-ready/v1' or ready.get('valid_training_round') is not True:
     raise ValueError('valid_training_result_required')
    artifact=Path(ready['artifact_root'])/'result.json';expected=ready['artifact_sha256']['result.json']
    result=self.read(artifact,expected);metric=number(result.get('validation',{}).get('macro_f1'))
    if metric is None or not 0<=metric<=1 or abs(metric-ready['primary_metric'])>1e-10:
     raise ValueError('registered_metric_mismatch')
    plan=self.plan(original['accepted_plan']) if original.get('accepted_plan') else None
    row={'number':index,'uid':ready['uid'],'metric':metric,'accuracy':number(result.get('validation',{}).get('accuracy')),
     'selected_epoch':result.get('selected_epoch'),'elapsed_seconds':number(result.get('elapsed_seconds')),
     'config':{k:v for k,v in result.get('config',{}).items() if isinstance(v,(str,int,float,bool)) and (not isinstance(v,str) or len(v)<150)},
     'code_sha256':result.get('code_sha256'),'base_uid':plan.get('base_uid') if plan else None,
     'intervention':plan['intervention'] if plan else '建立初始可比较基线',
     'change_label':plan['change_label'] if plan else '初始基线','plan':plan,
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
   row['new_best']=best is None or row['metric']>best+1e-12;best=max(best or 0,row['metric']);row['best_so_far']=best
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
   'evidence':[],'model_activity':[],'proposal':None,'task':None,'governing_plan':None}
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
   if stage in ('reviewing-plan','reviewing-code','needs-attention'):
    wanted='code_review_root' if stage=='reviewing-code' or str(attempt.get('reason','')).startswith('original-code-review:') else 'replan_root'
    if key!=wanted:continue
    calls=sorted(review_root.glob('proposal-review/*/model-calls/*/request.json'))
    for path in calls[-6:]:
     request=self.read(path);done=path.parent/'backend-completed.json';outcome=self.read(done).get('status') if done.exists() else None
     data['model_activity'].append({'model':request.get('request',{}).get('model'),'role':'方案生成' if request.get('request',{}).get('purpose')=='proposal' else '独立评审',
      'status':outcome or ('未发出' if not (path.parent/'intent.json').exists() else '执行中'),'at':path.stat().st_mtime})
  return data

 def study(self,index,cfg,root):
  path=root/'component-pipeline/pipeline.json';state=self.read(path)
  if state.get('config_digest','').removeprefix('sha256:')!=digest(cfg):raise ValueError('dashboard_study_identity_changed')
  rows,issues=self.rounds(state);current=self.current(cfg,root,state,rows)
  next_plan=current.get('proposal')
  if next_plan and next_plan.get('approved'):
   parent=next((row for row in rows if row['uid']==next_plan.get('parent_uid')),None)
   if parent:parent['result_decision']=next_plan.get('decision');parent['next_plan_pending_execution']=True
  supervision=self.read(root/'supervision.json') if (root/'supervision.json').exists() else {}
  status=state['status'];completed=status in ('research-goal-met','research-stopped')
  host_status=supervision.get('status','unknown');needs=current['needs_attention'] or bool(issues)
  if not completed and host_status in ('stopped','completed','needs-reconciliation','stalled-no-progress'):needs=True
  label='已达目标' if status=='research-goal-met' else '研究已结束' if completed else '需要关注' if needs else '研究进行中'
  title=self.title(cfg);best=max(rows,key=lambda x:x['metric']) if rows else None
  value={'id':cfg['project_id'],'title':title,'objective':text(cfg['component_pipeline'].get('objective'),1800),
   'status':status,'status_label':label,'needs_attention':needs,'completed':completed,'host_status':host_status,
   'goal':state.get('candidate_goal') or {'target':cfg['component_pipeline'].get('decision_policy',{}).get('target')},
   'incumbent':{'uid':best['uid'],'metric':best['metric'],'number':best['number']} if best else None,
   'latest':{'uid':rows[-1]['uid'],'metric':rows[-1]['metric'],'number':rows[-1]['number']} if rows else None,
   'rounds':rows,'round_count':len(rows),'current':current,'issues':issues,'jobs':self.jobs(root),
   'engineering_takeovers':index.get('engineering_takeovers'),
   'updated_at':path.stat().st_mtime,'timer':index.get('timer','unknown'),'read_only':True}
  from dashboard_control import state as pause_state
  value['control']=pause_state(cfg,root,completed)
  value['paused']=bool(value['control']['requested'] and not completed)
  if value['paused']:
   value['status_label']=value['control']['label'];value['needs_attention']=bool(issues)
   current['original_stage']=current['stage'];current['stage']='user-paused';current['label']=value['control']['label']
   current['needs_attention']=False;current['reason_text']=value['control']['scope']
  if value['control'].get('restore_pending'):
   value['status_label']=value['control']['label'];current['label']=value['control']['label'];current['reason_text']='正在核对并恢复原研究后台；可随时取消。'
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
  from dashboard_lineage import mainline,project as lineage
  annotate(self.project,value);value['mainline']=mainline(value)
  value['lineage']=lineage(self,cfg,root,state,value)
  return value

 def snapshot(self,selected=None):
  studies=[];errors=[];detail=None
  discovered=self.discover()
  for index,cfg,root in discovered:
   try:
    value=self.study(index,cfg,root)
    studies.append({k:value[k] for k in ('id','title','title_zh','status_label','needs_attention','completed','paused','round_count','incumbent','updated_at')})
    if cfg['project_id']==selected or selected is None and detail is None:detail=value
   except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as error:
    errors.append({'id':cfg.get('project_id'),'message':str(error) if isinstance(error,ValueError) else '研究记录暂不可读'})
  if selected is not None and not any(x['id']==selected for x in studies):raise KeyError('study_not_registered')
  body={'project':{'name':self.project.name,'root':str(self.project),'registration':'发现已登记研究，不创建独立副本'},'schema':'sdm-research-dashboard/v1','studies':studies,'study':detail,'errors':errors,'read_only':True}
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
