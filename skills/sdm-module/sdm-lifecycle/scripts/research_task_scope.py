"""Project-owned task intent and read-only research handoff. Never executes work."""
import hashlib
from pathlib import Path
import re
from review_storage import atomic_json, locked, native_path, read_json

MODES = {'single-evaluation': '单次评测', 'continuous-research': '持续研究'}
PENDING = {
 'goal-not-set': '研究目标和验收要求尚未明确。',
 'scope-unconfirmed': '持续执行范围与停止条件尚未确认。',
 'integration-pending': '项目训练或评价接入仍待完成。',
 'preparing-study': '正在准备研究配置，尚未启动研究控制器。',
 'controller-not-started': '尚未关联已启动的研究控制器。',
}

def folder(project, task_id):
 project=Path(project).resolve()
 if not native_path(project).is_dir() or not isinstance(task_id,str) or not re.fullmatch(r'[a-z][a-z0-9-]{3,80}',task_id):
  raise ValueError('existing_project_and_safe_task_id_required')
 path=project/'.bmad-ml/task-intents'/task_id
 if path.resolve()!=path:raise ValueError('unlinked_task_record_required')
 return project,path

def read(path):
 if native_path(path).stat().st_size>8*1024*1024:raise ValueError('bounded_task_record_required')
 return read_json(path)

def reference(project,path):
 project=Path(project).resolve()
 original=Path(path);original=original if original.is_absolute() else project/original;path=original.resolve()
 if original.is_symlink() or not path.is_relative_to(project) or not native_path(path).is_file():
  raise ValueError('project_owned_regular_task_artifact_required')
 if path.is_relative_to(project/'.bmad-ml/task-intents'):
  raise ValueError('task_control_record_is_not_evidence')
 if native_path(path).stat().st_size>8*1024*1024:raise ValueError('bounded_task_artifact_required')
 with native_path(path).open('rb') as source:sha=hashlib.file_digest(source,'sha256').hexdigest()
 return {'path':str(path),'sha256':sha}

def begin(project,task_id,mode,objective,source_ref):
 project,path=folder(project,task_id)
 if mode not in MODES or not isinstance(objective,str) or not 0<len(objective.strip())<=1800 or not isinstance(source_ref,str) or not 0<len(source_ref.strip())<=1800:
  raise ValueError('explicit_task_mode_objective_and_source_required')
 if mode=='single-evaluation':
  for index in [project/'.bmad-ml/CURRENT.json',*sorted(native_path(project/'.bmad-ml').glob('*/CURRENT.json'))][:64]:
   if not native_path(index).is_file():continue
   try:registered=read(index)
   except (OSError,ValueError):continue
   if registered.get('schema')=='bmad-ml-active-study/v1' and registered.get('study_id')==task_id:
    raise ValueError('existing_research_requires_distinct_single_evaluation_id')
 value={'schema':'sdm-entry-task/v1' ,'task_id':task_id,'project_root':str(project),
  'mode':mode,'objective':objective,'source_ref':source_ref,'execution_authority':False}
 with locked(native_path(path),'.intent.lock','task_intent_busy'):
  intent=path/'intent.json'
  if native_path(intent).exists() and read(intent)!=value:raise ValueError('original_task_scope_immutable_create_a_new_task')
  if not native_path(intent).exists():atomic_json(intent,value)
 return status(project,task_id)

def update(project,task_id,result=None,analysis=None,pending_reason=None,config=None):
 project,path=folder(project,task_id)
 # Serialize ownership checks across tasks, then preserve the original per-task lock.
 with locked(native_path(project/'.bmad-ml/task-intents'),'.bindings.lock','task_binding_busy'),locked(native_path(path),'.intent.lock','task_intent_busy'):
  status(project,task_id)  # Reject damaged existing evidence before any mutation.
  intent=read(path/'intent.json');value=read(path/'progress.json') if native_path(path/'progress.json').exists() else {}
  proposed=dict(value)
  for name,artifact in (('result',result),('analysis',analysis),('config',config)):
   if artifact is None:continue
   bound=reference(project,artifact)
   if name in proposed and proposed[name]!=bound:raise ValueError('original_task_evidence_binding_changed')
   proposed[name]=bound
  if proposed.get('analysis') and not proposed.get('result'):raise ValueError('analysis_requires_registered_result')
  if pending_reason is not None:
   if intent['mode']!='continuous-research' or not isinstance(pending_reason,str) or pending_reason not in PENDING:raise ValueError('typed_continuous_pending_reason_required')
   proposed['pending_reason']=pending_reason
  if proposed.get('config'):
   if intent['mode']!='continuous-research':raise ValueError('single_evaluation_cannot_enter_research_implicitly')
   cfg=read(Path(proposed['config']['path']))
   if not isinstance(cfg,dict) or cfg.get('schema') not in ('sdm-event-service/v2','sdm-event-service/v3') or not cfg.get('component_pipeline') or not isinstance(cfg.get('root'),str) or not Path(cfg['root']).is_absolute() or not Path(cfg['root']).resolve().is_relative_to(project) or not isinstance(cfg.get('project_id'),str):
    raise ValueError('original_project_research_configuration_required')
   proposed['study_id']=cfg['project_id']
   for other in native_path(project/'.bmad-ml/task-intents').glob('*/progress.json'):
    if other.parent.name==task_id:continue
    owner=read(other)
    if not isinstance(owner,dict):continue
    ref=owner.get('config')
    if owner.get('study_id')==proposed['study_id'] or isinstance(ref,dict) and isinstance(ref.get('path'),str) and Path(ref['path']).resolve()==Path(proposed['config']['path']).resolve():
     raise ValueError('original_research_configuration_already_bound')
  view(project,task_id,path,intent,proposed)  # Validate candidate before committing it.
  if proposed!=value:atomic_json(path/'progress.json',proposed)
 return status(project,task_id)

def status(project,task_id):
 project,path=folder(project,task_id);intent=read(path/'intent.json')
 progress=read(path/'progress.json') if native_path(path/'progress.json').exists() else {}
 return view(project,task_id,path,intent,progress)


def view(project,task_id,path,intent,progress):
 if not isinstance(intent,dict) or not isinstance(intent.get('objective'),str) or not intent.get('objective') or not isinstance(intent.get('source_ref'),str) or not intent.get('source_ref') or intent.get('schema')!='sdm-entry-task/v1' or intent.get('task_id')!=task_id or intent.get('project_root')!=str(project) or not isinstance(intent.get('mode'),str) or intent.get('mode') not in MODES or intent.get('execution_authority') is not False:
  raise ValueError('original_project_task_intent_required')
 if not isinstance(progress,dict) or set(progress)-{'result','analysis','config','study_id','pending_reason'}:
  raise ValueError('typed_task_progress_required')
 if progress.get('analysis') and not progress.get('result'):raise ValueError('analysis_requires_registered_result')
 if (progress.get('config') or progress.get('pending_reason')) and intent['mode']!='continuous-research':
  raise ValueError('single_evaluation_cannot_enter_research_implicitly')
 if progress.get('pending_reason') is not None and (not isinstance(progress['pending_reason'],str) or progress['pending_reason'] not in PENDING):raise ValueError('typed_continuous_pending_reason_required')
 if bool(progress.get('config'))!=bool(progress.get('study_id')):raise ValueError('bound_study_identity_required')
 for key in ('result','analysis','config'):
  ref=progress.get(key)
  if ref is None:continue
  if not isinstance(ref,dict) or set(ref)!={'path','sha256'} or not isinstance(ref['path'],str) or not isinstance(ref['sha256'],str):raise ValueError('typed_task_artifact_required:'+key)
  if reference(project,ref['path'])!=ref:raise ValueError('task_artifact_changed:'+key)
 if progress.get('config'):
  cfg=read(Path(progress['config']['path']))
  if not isinstance(cfg,dict) or cfg.get('schema') not in ('sdm-event-service/v2','sdm-event-service/v3') or not cfg.get('component_pipeline') or not isinstance(cfg.get('root'),str) or not Path(cfg['root']).is_absolute() or not Path(cfg['root']).resolve().is_relative_to(project) or not isinstance(cfg.get('project_id'),str) or cfg['project_id']!=progress['study_id']:
   raise ValueError('original_project_research_configuration_required')
 analysis=bool(progress.get('analysis'));result=bool(progress.get('result'));single=intent['mode']=='single-evaluation'
 reason=('任务范围仅包含本次评测与分析；分析完成后正常结束，不会自动修改方案或提交训练。' if single else
  PENDING.get(progress.get('pending_reason','controller-not-started'),PENDING['controller-not-started']))
 label=('单次评测已完成' if analysis else '评测结果已提供，等待分析' if result else '等待评测结果') if single else '持续研究启动状态待核实' if progress.get('config') else '持续研究尚未启动'
 if progress.get('config'):reason='原研究配置已关联，但尚未读到其已登记研究状态。'+reason
 return {**intent,'id':'entry:'+task_id,'mode_label':MODES[intent['mode']], 'label':label,'reason':reason,
  'continuation_expected':not single,'result_received':result,'analysis_completed':analysis,'iteration_entered':None if progress.get('config') else False,'automatic_iteration_active':False,
  'research_started':None if progress.get('config') else False,'pending_reason':progress.get('pending_reason'),'config':progress.get('config'),
  'study_id':progress.get('study_id'),'evidence':{k:progress[k] for k in ('result','analysis') if k in progress},
  'record_path':str(path/'intent.json'),'updated_at':max(native_path(p).stat().st_mtime for p in (path/'intent.json',path/'progress.json') if native_path(p).exists()),
  'scope':'展示原任务范围和已登记文件，不替代执行回执、科学验收或持续执行授权。',
  'model_calls':0,'platform_writes':0}

def unavailable(project,task_id,reason,entry=None):
 """Keep a selected damaged record visible without reusing unverified facts."""
 entry=entry or {};mode=entry.get('mode','unconfirmed')
 return {'id':'entry:'+task_id,'task_id':task_id,'mode':mode,'mode_label':MODES.get(mode,'任务范围待核实'),
  'objective':entry.get('objective','任务记录 '+task_id),'label':'任务记录待核实','reason':reason,
  'needs_attention':True,'result_received':None,'analysis_completed':None,'iteration_entered':None,
  'automatic_iteration_active':False,'research_started':None,'execution_authority':False,
  'config':None,'study_id':None,'evidence':{},'updated_at':entry.get('updated_at',0),
  'scope':'请核对原入口与证据文件；未核实记录不能证明任务完成或授予执行权限。',
  'model_calls':0,'platform_writes':0}


def tasks(project):
 project=Path(project).resolve();values=[];errors=[]
 for path in sorted(native_path(project/'.bmad-ml/task-intents').glob('*/intent.json'))[:64]:
  try:values.append(status(project,path.parent.name))
  except (OSError,ValueError,KeyError,TypeError):
   message='任务范围或证据记录无法核验，请核对原记录。'
   # Preserve independently valid task intent; never preserve failed evidence.
   try:
    _,record=folder(project,path.parent.name);base=view(project,path.parent.name,record,read(path),{})
   except (OSError,ValueError,KeyError,TypeError):base=None
   values.append(unavailable(project,path.parent.name,message,base));errors.append({'id':'entry:'+path.parent.name,'message':message})
 owners={}
 for value in values:
  if value.get('study_id'):owners.setdefault(value['study_id'],[]).append(value['id'])
 conflicts={identity for group in owners.values() if len(group)>1 for identity in group}
 for i,value in enumerate(values):
  if value['id'] not in conflicts:continue
  message='多个入口关联了同一研究，任务范围存在冲突；请核对原关联记录。'
  values[i]=unavailable(project,value['task_id'],message,value);errors.append({'id':value['id'],'message':message})
 return values,errors

def analysis_available(state):
 """Existing original analysis reports may predate entry-task records."""
 # A newly submitted successor has no result yet. Report the latest result's
 # analysis, but never carry it forward to a newer result awaiting analysis.
 attempt=next((a for a in reversed((state or {}).get('attempts') or []) if a.get('result') or a.get('analysis')), {})
 ref=attempt.get('analysis')
 if not ref:return False
 try:
  path=native_path(Path(ref['path']))
  if not path.is_file() or path.stat().st_size>8*1024*1024:return None
  with path.open('rb') as source:actual=hashlib.file_digest(source,'sha256').hexdigest()
  return True if actual==ref['sha256'].removeprefix('sha256:') else None
 except (OSError,ValueError,KeyError,TypeError):return None

def handoff(cfg,state,progress,analysis_completed=False,entry=None):
 """Keep declared mode, historical result routing and current worker health separate."""
 attempt=(state.get('attempts') or [{}])[-1];phase=progress['state']
 mode=entry['mode'] if entry else 'continuous-research' if cfg.get('continuation_policy') else 'unconfirmed'
 routed=any(a.get('result_handoff',{}).get('model_requested') is True and a.get('result_handoff',{}).get('job_id') for a in state.get('attempts',[])) or len(state.get('attempts',[]))>1
 native_result=bool(any(a.get('result') for a in state.get('attempts',[])) or state.get('rounds'))
 result=bool(native_result or entry and entry['result_received'])
 analysis=analysis_completed if native_result or analysis_completed is not False else entry['analysis_completed'] if entry else False
 code=attempt.get('reason') or attempt.get('continuation',{}).get('error_code') or attempt.get('continuation',{}).get('category')
 if mode=='unconfirmed':label,reason='任务范围待核实','旧记录没有明确持续执行声明；不能仅凭已有结果或分析报告推断已进入持续研究。'
 elif phase=='completed':label,reason='持续研究已结束','已达到登记目标。' if state.get('status')=='research-goal-met' else '原研究已按停止决定结束，请查看原停止记录。'
 elif phase=='paused':label,reason='持续研究已暂停','原研究处于暂停状态，不再派发新的分析、方案或训练。'
 elif attempt.get('stage')=='needs-attention' or attempt.get('continuation',{}).get('phase')=='blocked':
  label='持续迭代受阻'
  reasons={'awaiting-proposal-evidence':'方案评审仍有待补证据，尚不能进入后续实施或提交。','model-input-capacity':'分析输入超过允许范围，需要整理证据后接续。','original-model-outcome-unresolved':'原模型调用结果尚未核实，需要先核对原调用。','authentication-blocked':'平台认证尚未就绪，需要处理原认证问题。','integrity-blocked':'原实验或配置的证据完整性尚未通过核验。'}
  reason=next((message for key,message in reasons.items() if str(code).endswith(key)),progress['detail']+(' 记录原因：'+str(code) if code else ''))
 elif phase in ('offline','unverified'):label,reason='持续研究后台未运行' if phase=='offline' else '持续研究状态待核验',('已有评测分析记录，但没有已核验的后台研究线程承接。' if analysis and not routed else '原后台控制器未运行，已保存结果或报告不会自行触发下一轮。' if phase=='offline' else '尚不能核验原后台与研究线程身份。')
 elif routed:label,reason='已进入持续迭代','原结果已交给后继分析与方案流程；当前：'+progress['label']+'。'
 elif attempt.get('stage')=='result-ready':label,reason='结果已回收，等待分析接续','结果已登记，尚未记录后继分析任务的派发。'
 else:label,reason='持续研究已启动，正在首轮流程','后台已接管首轮工作，尚未记录结果驱动的后继接续。当前：'+progress['label']+'。'
 return {'schema':'sdm-task-handoff-view/v1','mode':mode,'mode_label':MODES.get(mode,'任务范围待核实'),
  'label':label,'reason':reason,'reason_code':code,'result_received':result,'analysis_completed':analysis,
  'iteration_entered':routed if mode!='unconfirmed' else None,'research_started':True if progress['controller_running'] or routed else None if attempt.get('stage') else False,
  'automatic_iteration_active':progress['automatic_iteration_active'] if mode=='continuous-research' else False,
  'next_action':progress['next_action'],'next_owner':progress['next_owner'],'execution_authority':False,
  'model_calls':0,'platform_writes':0}
