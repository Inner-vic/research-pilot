"""Authenticated controls delegated to the original frozen host, never a second research engine."""
import hashlib,json,sqlite3,subprocess,sys,time,uuid
from pathlib import Path
from urllib.request import Request,urlopen
from review_storage import atomic_json,locked,read_json
from task_pipeline import digest


def host(cfg):
 root=Path(cfg['root']);ready=read_json(root/'ready.json')
 if str(ready.get('config_sha256','')).removeprefix('sha256:')!=digest(cfg).removeprefix('sha256:'):raise ValueError('original_host_binding_changed')
 if type(ready.get('port')) is not int or not 1<=ready['port']<=65535:raise ValueError('original_host_port_invalid')
 base='http://127.0.0.1:'+str(ready['port'])
 with urlopen(Request(base+'/health',headers={'Authorization':'Bearer '+ready['token']}),timeout=1) as response:live=json.load(response)
 if any(live.get(k)!=ready.get(k) for k in ('pid','project_id','config_sha256')):raise ValueError('original_host_identity_changed')
 return live


def state(cfg,root,completed=False):
 root=Path(root);expected=digest(cfg);requested=(root/'PAUSED').exists();gates=[]
 for name in ('DRAINING','host-stop.json'):
  path=root/name
  if path.exists():
   marker=read_json(path)
   if marker.get('config_sha256')!=expected:raise ValueError('pause_gate_binding_changed')
   requested=True;gates.append(name)
 quiescent=False
 if (root/'drain-completed.json').exists():
  receipt=read_json(root/'drain-completed.json')
  quiescent=receipt.get('config_sha256')==expected and receipt.get('pending_jobs')==0 and receipt.get('pending_native_context') is False and receipt.get('pipeline_step_active') is False
 elif requested:
  try:quiescent=host(cfg).get('drain',{}).get('quiescent',False)
  except (OSError,ValueError,KeyError):pass
 from research_pause import generation
 from research_restore import status as restore_status,eligible
 restoration=restore_status(cfg)
 supported='research_pause.py' in cfg.get('runtime_manifest',{})
 resumable=False;live_health=None;resume_reason='研究未暂停'
 if requested:
  try:
   live_health=host(cfg);quiescent=bool(live_health.get('drain',{}).get('quiescent',False))
  except (OSError,ValueError,KeyError):pass
  resume_reason='暂停收尾完成后才可继续'
  if quiescent:
   resume_reason='该研究已停止或使用旧冻结版本，请在 Codex 中明确要求同身份接续'
   if supported and not any((root/name).exists() for name in ('host-stop.json','STOP_AFTER_DRAIN')):
    try:
     live=live_health or host(cfg);quiescent=bool(live.get('drain',{}).get('quiescent'));resumable=bool(live.get('paused')) and quiescent
     resume_reason='继续原研究，保留原会话、结果和任务身份' if resumable else '原宿主暂停状态待核对'
    except (OSError,ValueError,KeyError):resume_reason='原宿主不在线，请在 Codex 中核对并接续'
 pending=bool(restoration and restoration['pending'])
 restore_problem=bool(restoration and restoration['phase']=='blocked')
 if not completed and not resumable and eligible(cfg) and not pending:
  from research_runtime import owner_alive
  if not owner_alive(cfg):
   resumable=True;resume_reason='核对原回执后启动唯一后台，自动接续原研究'
 if pending:resumable=False
 if restore_problem:resume_reason='上次恢复已受阻，原回执与暂停现场保留；请核对恢复报告后重试。'
 return {'restore_problem':restore_problem,'restoration':restoration,'restore_pending':pending,'requested':requested,'quiescent':quiescent,'phase':'completed' if completed else 'restoring' if pending else 'paused' if requested and quiescent else 'pausing' if requested else 'active',
  'label':'研究已结束' if completed else restoration['label'] if pending or restore_problem else '已暂停' if requested and quiescent else '暂停收尾中' if requested else '可暂停研究',
  'can_pause':not completed and (not requested or pending),'can_resume':not completed and resumable,'resume_reason':resume_reason,'pause_generation':generation(root),'config_sha256':expected,
  'scope':'暂停后不再发起新的分析、方案或训练；已提交的平台训练不会因此自动停止。调整目标、方案和资源请在 Codex 中说明。',
  'actions':['pause-study',*(['resume-study'] if supported else [])],'gates':gates}


def run_public(cfg,script,args):
 runtime=Path(cfg['runtime_root']).resolve();file=runtime/script
 expected=cfg.get('runtime_manifest',{}).get(script)
 if not expected or file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest()!=expected:raise ValueError('original_public_control_script_changed')
 result=subprocess.run([sys.executable,'-B',str(file),*args],capture_output=True,text=True,encoding='utf-8',timeout=30,
  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 if result.returncode:raise ValueError('original_pause_control_failed')
 return json.loads(result.stdout)


def pause(project,request):
 if set(request)!={'study_id','config_sha256','request_id'}:raise ValueError('exact_pause_request_required')
 try:
  if str(uuid.UUID(request['request_id']))!=request['request_id']:raise ValueError()
 except (ValueError,AttributeError,TypeError):raise ValueError('pause_request_identity_required')
 from research_dashboard_data import DashboardData
 data=DashboardData(project);found=next(((index,cfg,root) for index,cfg,root in data.discover() if cfg['project_id']==request['study_id']),None)
 if not found:raise ValueError('study_not_registered')
 index,cfg,root=found
 if request['config_sha256']!=digest(cfg):raise ValueError('study_version_changed_refresh_required')
 runtime=Path(cfg['runtime_root']).resolve()
 if not runtime.is_relative_to(Path(project).resolve()):raise ValueError('original_runtime_must_belong_to_project')
 config=Path(index['config']).resolve();pipeline=read_json(root/'component-pipeline/pipeline.json')
 if pipeline.get('config_digest','').removeprefix('sha256:')!=digest(cfg).removeprefix('sha256:'):raise ValueError('study_configuration_changed')
 completed=pipeline['status'] in ('research-goal-met','research-stopped');folder=Path(project)/'.bmad-ml/dashboard/controls';receipt=folder/(request['request_id']+'.json')
 with locked(folder,'.pause.lock','pause_already_in_progress'):
  if receipt.exists():
   saved=read_json(receipt)
   if saved.get('request')!=request:raise ValueError('pause_receipt_identity_changed')
   actual=state(cfg,root,completed)
   if actual['requested'] or completed:return {'status':actual['phase'],'control':actual,'duplicate':True,'receipt_id':request['request_id']}
   # Unknown previous effect must be reconciled, never blindly reissued.
   raise ValueError('original_pause_outcome_requires_reconciliation')
  original=state(cfg,root,completed)
  if original.get('restore_pending'):
   from research_restore import cancel
   result=cancel(cfg)
   atomic_json(receipt,{'schema':'sdm-dashboard-pause-receipt/v1','request':request,'phase':'verified','at':time.time(),'result':result,'platform_actions':0})
   return {**result,'control':state(cfg,root,completed),'receipt_id':request['request_id'],'duplicate':False}
  atomic_json(receipt,{'schema':'sdm-dashboard-pause-receipt/v1','request':request,'phase':'intent','at':time.time(),'platform_actions':0})
  if original['requested'] or completed:
   actual=original;result={'status':actual['phase'],'control':actual,'duplicate':False,'receipt_id':request['request_id']}
  else:
   host(cfg) # exact original live identity before any mutable control action
   actions=[]
   for action in ('drain','pause'):
    result=run_public(cfg,'research_event_service.py',['--config',str(config),'--control',action]);actions.append(action)
   current=read_json(root/'component-pipeline/pipeline.json')['attempts'][-1];stage=current['stage'];selected=None
   if stage=='reviewing-code' or stage=='needs-attention' and str(current.get('reason','')).startswith('original-code-review:'):
    if current.get('code_review_project') and current.get('code_review_root'):selected=(Path(current['code_review_project']),Path(current['code_review_root']))
   elif stage=='reviewing-plan' or stage=='needs-attention' and current.get('replan_root'):
    if current.get('replan_root'):selected=(Path(current['replan_root']).parent/'replan-project.json',Path(current['replan_root']))
   if selected:
    selected_project,output=selected
    if not selected_project.resolve().is_relative_to(root.resolve()) or not output.resolve().is_relative_to(root.resolve()):raise ValueError('pause_review_must_belong_to_original_store')
    if not (output/'research-control.json').exists() or read_json(output/'research-control.json').get('paused') is not True:
     run_public(cfg,'run_project_pipeline.py',['--project',str(selected_project),'--output',str(output),'--pause-research-worker']);actions.append('pause-original-review')
   actual=state(cfg,root,False);result={'status':actual['phase'],'control':actual,'receipt_id':request['request_id'],'duplicate':False}
   if not actual['requested']:raise ValueError('original_pause_gate_not_verified')
  atomic_json(receipt,{'schema':'sdm-dashboard-pause-receipt/v1','request':request,'phase':'verified','at':time.time(),'result':result,'platform_actions':0})
  return result


def resume(project,request):
 if set(request)!={'study_id','config_sha256','pause_generation','request_id'}:raise ValueError('exact_resume_request_required')
 try:
  if str(uuid.UUID(request['request_id']))!=request['request_id']:raise ValueError()
 except (ValueError,AttributeError,TypeError):raise ValueError('resume_request_identity_required')
 from research_dashboard_data import DashboardData
 found=next(((index,cfg,root) for index,cfg,root in DashboardData(project).discover() if cfg['project_id']==request['study_id']),None)
 if not found:raise ValueError('study_not_registered')
 index,cfg,root=found
 if request['config_sha256']!=digest(cfg):raise ValueError('study_version_changed_refresh_required')
 if not Path(cfg['runtime_root']).resolve().is_relative_to(Path(project).resolve()):raise ValueError('original_runtime_must_belong_to_project')
 pipeline=read_json(root/'component-pipeline/pipeline.json')
 if pipeline.get('config_digest')!=digest(cfg):raise ValueError('study_configuration_changed')
 completed=pipeline['status'] in ('research-goal-met','research-stopped')
 folder=Path(project)/'.bmad-ml/dashboard/controls';receipt=folder/(request['request_id']+'.json')
 with locked(folder,'.pause.lock','control_already_in_progress'):
  if receipt.exists():
   saved=read_json(receipt)
   if saved.get('request')!=request:raise ValueError('resume_receipt_identity_changed')
   actual=state(cfg,root,completed)
   if saved.get('phase') in ('verified','restoring') or (root/'resume-requests'/(request['request_id']+'.json')).exists() and read_json(root/'resume-requests'/(request['request_id']+'.json')).get('phase')=='applied':
    return {'status':'restoring' if actual.get('restore_pending') else 'resume-applied','control':actual,'duplicate':True,'receipt_id':request['request_id']}
   raise ValueError('original_resume_outcome_requires_reconciliation')
  actual=state(cfg,root,completed)
  if actual['pause_generation']!=request['pause_generation']:raise ValueError('pause_generation_changed_refresh_required')
  if not actual['can_resume']:raise ValueError('original_host_resume_unavailable_use_codex')
  if actual.get('restore_pending'):raise ValueError('original_restore_already_in_progress')
  try:host(cfg);online=True
  except (OSError,ValueError,KeyError):online=False
  if not online:
   from research_restore import start
   result=start(project,index,cfg,request)
   result.update(control=state(cfg,root,completed),receipt_id=request['request_id'])
   atomic_json(receipt,{'schema':'sdm-dashboard-resume/v1','request':request,'phase':'restoring','at':time.time(),'result':result,'platform_actions':0})
   return result
  atomic_json(receipt,{'schema':'sdm-dashboard-resume/v1','request':request,'phase':'intent','at':time.time(),'platform_actions':0})
  host(cfg)
  run_public(cfg,'research_event_service.py',['--config',str(Path(index['config']).resolve()),'--control','resume',
   '--expected-pause',request['pause_generation'],'--request-id',request['request_id']])
  actual=state(cfg,root,completed)
  if actual['requested']:raise ValueError('original_resume_gate_requires_reconciliation')
  result={'status':'resume-applied','control':actual,'receipt_id':request['request_id'],'duplicate':False}
  atomic_json(receipt,{'schema':'sdm-dashboard-resume/v1','request':request,'phase':'verified','at':time.time(),'result':result,'platform_actions':0})
  return result


def receipt_status(project,request_id):
 """Read one original control identity even while the research index changes."""
 try:
  if str(uuid.UUID(request_id))!=request_id:raise ValueError()
 except (ValueError,AttributeError,TypeError):raise ValueError('control_request_identity_required')
 project=Path(project).resolve();path=project/'.bmad-ml/dashboard/controls'/(request_id+'.json')
 restore=project/'.bmad-ml/restores'/request_id/'state.json'
 value=read_json(path) if path.exists() else None
 if value and value.get('request',{}).get('request_id')!=request_id:raise ValueError('control_receipt_identity_changed')
 result={'request_id':request_id,'phase':value.get('phase') if value else 'not-recorded','recorded':bool(value),
         'study_id':value.get('request',{}).get('study_id') if value else None,'result':value.get('result') if value else None}
 if restore.exists():
  saved=read_json(restore)
  if saved['request']['request_id']!=request_id or Path(saved['project']).resolve()!=project:raise ValueError('restore_receipt_identity_changed')
  from research_restore import LABELS,TERMINAL
  result.update(recorded=True,study_id=saved['study_id'],phase=saved['phase'],label=LABELS[saved['phase']],
                pending=saved['phase'] not in TERMINAL,reason=saved.get('reason'))
  if result['pending'] and (restore.parent/'ready.json').exists():
   from research_runtime import owner_alive
   if not owner_alive({'root':str(restore.parent)}):
    result.update(phase='blocked',label='恢复进程中断，需对账',reason='restore_worker_exited_reconcile_same_identity')
 return result
