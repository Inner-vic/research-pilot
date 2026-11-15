"""User-requested same-study host restoration. No science engine or platform submitter.

Starts an original/new frozen supervisor behind pause gates, reconciles receipts,
then uses the original checked resume API. The browser can disappear at any time.
"""
import argparse,copy,hashlib,json,os,sqlite3,subprocess,sys,time,uuid
from contextlib import closing
from pathlib import Path
from urllib.request import Request,urlopen
from review_storage import atomic_json,locked,read_json
from task_pipeline import digest

_children={}
TERMINAL={'resumed','cancelled','blocked'}
LABELS={'requested':'恢复检查中','checking':'核对原会话与回执','preparing':'准备冻结运行时','starting':'启动研究后台','ready':'核验后台身份','resuming':'接续原研究','resumed':'研究已继续','cancelling':'正在取消恢复','cancelled':'已取消恢复','blocked':'恢复受阻'}


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def status(cfg):
    for key,child in list(_children.items()):
        if child.poll() is not None:_children.pop(key,None)
    marker=Path(cfg['root'])/'restore-control.json'
    if not marker.exists():return None
    value=read_json(marker)
    if value.get('config_sha256')!=digest(cfg):raise ValueError('restore_control_binding_changed')
    folder=Path(value['folder']).resolve();project=Path(value['project']).resolve()
    if not folder.is_relative_to(project/'.bmad-ml/restores'):raise ValueError('restore_control_scope_changed')
    saved=read_json(folder/'state.json')
    if saved.get('study_id')!=cfg['project_id']:raise ValueError('restore_study_identity_changed')
    phase=saved['phase']
    if phase not in TERMINAL and (folder/'ready.json').exists():
        from research_runtime import owner_alive
        if not owner_alive({'root':str(folder)}):
            return {'phase':'blocked','label':'恢复进程中断，需对账','pending':True,'request_id':saved['request']['request_id'],
                'reason':'restore_worker_exited_reconcile_same_identity','folder':str(folder)}
    return {'phase':phase,'label':LABELS[phase],'pending':phase not in TERMINAL,'request_id':saved['request']['request_id'],
        'reason':saved.get('reason'),'folder':str(folder)}


def eligible(cfg):
    """Cheap UI availability; full effect preflight always runs again in worker."""
    root=Path(cfg['root'])
    if cfg.get('component_pipeline') and cfg['component_pipeline'].get('version')!=4:return False
    path=root/'component-pipeline/pipeline.json'
    return path.exists() and read_json(path).get('status')=='running'


def update(folder,phase,**changes):
    folder=Path(folder);value=read_json(folder/'state.json');value.update(changes,phase=phase,updated_at=time.time())
    value.setdefault('history',[]).append({'phase':phase,'at':value['updated_at']});atomic_json(folder/'state.json',value);return value


def pointer(cfg,project,folder):
    atomic_json(Path(cfg['root'])/'restore-control.json',{'config_sha256':digest(cfg),'project':str(Path(project).resolve()),'folder':str(Path(folder).resolve())})


def index_path(project,index):
    project=Path(project).resolve()
    matches=[p for p in (project/'.bmad-ml').glob('**/CURRENT.json') if read_json(p).get('config')==index['config']]
    if len(matches)!=1:raise ValueError('unique_registered_restore_index_required')
    return matches[0]


def start(project,index,cfg,request):
    project=Path(project).resolve();folder=project/'.bmad-ml/restores'/request['request_id']
    with locked(project/'.bmad-ml/restores','.requests.lock','restore_request_busy'):
        if (folder/'state.json').exists():
            saved=read_json(folder/'state.json')
            if saved['request']!=request:raise ValueError('restore_request_identity_changed')
            return {'status':saved['phase'],'restoration':status(cfg),'duplicate':True}
        prior=status(cfg)
        if prior and prior['pending']:raise ValueError('original_restore_already_in_progress')
        if prior:
            previous=read_json(Path(prior['folder'])/'state.json')
            if previous.get('target_config') and previous['target_config']!=index['config']:
                from research_runtime import owner_alive
                if owner_alive(read_json(previous['target_config'])):raise ValueError('previous_restored_host_requires_reconciliation')
        from research_pause import generation
        if request['pause_generation']!=generation(cfg['root']):raise ValueError('pause_generation_changed_refresh_required')
        path=index_path(project,index);config=Path(index['config']).resolve()
        if not config.is_relative_to(project):raise ValueError('restore_config_scope_changed')
        from research_runtime import freeze
        worker_manifest=freeze(folder/'worker-runtime');worker=Path(worker_manifest['runtime_root'])/'research_restore.py';files=worker_manifest['runtime_manifest']
        saved={'schema':'sdm-host-restore/v1','project':str(project),'study_id':cfg['project_id'],'request':request,
            'original_config':str(config),'original_config_sha256':sha(config),'index':str(path),'index_sha256':sha(path),
            'worker_root':str(worker.parent),'worker_manifest':files,'phase':'requested','created_at':time.time(),'history':[],
            'model_calls_by_restore':0,'platform_submissions_by_restore':0}
        atomic_json(folder/'state.json',saved);pointer(cfg,project,folder)
        # Durable launch intent precedes Popen. Duplicates never spawn another worker.
        with (folder/'stdout.log').open('xb') as out,(folder/'stderr.log').open('xb') as err:
            child=subprocess.Popen([sys.executable,'-B',str(worker),'--work',str(folder)],stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))
        _children[str(folder)]=child
        launched={'pid':child.pid,'at':time.time(),'state_identity':digest(saved)}
        atomic_json(folder/'launcher.json',launched);atomic_json(folder/'ready.json',launched)
        return {'status':'restoring','restoration':status(cfg),'duplicate':False}


def cancel(cfg):
    actual=status(cfg)
    if not actual or not actual['pending']:return None
    folder=Path(actual['folder']);atomic_json(folder/'CANCEL',{'request_id':actual['request_id'],'at':time.time()})
    saved=read_json(folder/'state.json')
    if actual.get('reason')=='restore_worker_exited_reconcile_same_identity' and not saved.get('target_config'):
        update(folder,'cancelled');return {'status':'cancelled','restore_request_id':actual['request_id']}
    return {'status':'cancelling','restore_request_id':actual['request_id']}


def preflight(cfg):
    from research_event_service import configuration
    from research_recovery import inspect
    from research_runtime import owner_alive
    configuration(cfg,control_only=True)
    if owner_alive(cfg):raise ValueError('original_host_still_running')
    report=inspect(cfg)
    if report.get('recoverable_receipts') and not report['unresolved']:
        code="import sys;sys.path.insert(0,sys.argv[1]);from review_storage import read_json;from research_event_service import Inbox;Inbox(read_json(sys.argv[2])).recover()"
        packet=Path(cfg['root'])/'restore-reconcile-config.json';atomic_json(packet,cfg)
        reconciled=subprocess.run([sys.executable,'-I','-S','-B','-c',code,cfg['runtime_root'],str(packet)],capture_output=True,text=True,timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if reconciled.returncode:raise ValueError('original_durable_receipt_reconciliation_failed')
        report=inspect(cfg)
    if not report['can_restart_host']:raise ValueError('original_outcome_requires_reconciliation')
    from research_context import Context
    context=Context(cfg).load()
    if context.get('pending_job') or context.get('pending_turn'):raise ValueError('original_native_turn_requires_reconciliation')
    root=Path(cfg['root'])
    with closing(sqlite3.connect((root/'delivery.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
        if any(row[0] not in ('completed','paused') for row in db.execute('select status from jobs')):raise ValueError('restore_requires_accounted_original_jobs')
    state=read_json(root/'component-pipeline/pipeline.json') if (root/'component-pipeline/pipeline.json').exists() else None
    evidence=[]
    if state:
        from research_pause import active_review
        selected=active_review(root,state['attempts'][-1])
        if selected:
            from research_model_receipts import checked
            from model_runner import binding,completed
            for intent in selected[1].glob('proposal-review/*/model-calls/*/intent.json'):
                call=intent.parent;envelope=checked(call/'request.json','sdm-model-runner-envelope/v1')
                if envelope['binding']!=binding(envelope['config'],envelope['request']):raise ValueError('restore_model_binding_changed')
                reply=completed(call,envelope['binding'])
                if not reply or reply['status']!='completed':raise ValueError('original_model_outcome_requires_reconciliation')
                target=call/'canonical.json' if (call/'canonical.json').exists() else call/'backend-completed.json'
                evidence.append({'path':str(target),'sha256':sha(target)})
    return report,state,context,evidence


def gates(cfg,folder):
    root=Path(cfg['root']);archive=Path(folder)/'original-control-markers';archive.mkdir(exist_ok=True)
    for name in ('host-stop.json','STOP_AFTER_DRAIN','DRAINING','PAUSED'):
        path=root/name
        if path.exists():
            raw=path.read_bytes();dest=archive/name
            if dest.exists() and dest.read_bytes()!=raw:raise ValueError('restore_marker_archive_changed')
            if not dest.exists():dest.write_bytes(raw)
            value=read_json(path)
            if name!='PAUSED' and str(value.get('config_sha256','')).removeprefix('sha256:')!=digest(cfg).removeprefix('sha256:'):
                raise ValueError('restore_stop_marker_binding_changed')
    # Keep the dispatch gate throughout startup; archived stop markers are mutable controls, not deleted evidence.
    atomic_json(root/'DRAINING',{'config_sha256':digest(cfg),'at':time.time()})
    atomic_json(root/'PAUSED',{'at':time.time(),'restore_request':Path(folder).name})
    for name in ('host-stop.json','STOP_AFTER_DRAIN'):(root/name).unlink(missing_ok=True)


def health(cfg,started):
    from dashboard_control import host
    ready=Path(cfg['root'])/'ready.json'
    if not ready.exists() or ready.stat().st_mtime<started:return None
    try:
        live=host(cfg)
        if live.get('paused') is not True or live.get('drain',{}).get('quiescent') is not True:return None
        if cfg.get('component_pipeline') and not live.get('component_owner',{}).get('thread_alive'):return None
        return live
    except (OSError,ValueError,KeyError):return None


def _seed_local(cfg,original):
    """New version, same verified history. No original ledger is modified."""
    from types import SimpleNamespace
    from component_pipeline import ComponentPipeline
    from component_research import round_evidence
    for row in original['rounds']:round_evidence(row['result'])
    owner=ComponentPipeline(SimpleNamespace(cfg=cfg,root=Path(cfg['root'])))
    state=owner.load();state['rounds']=copy.deepcopy(original['rounds'])
    for key in ('observed_task_uids','incumbent','candidate_goal','submission_budget','goal_assessment'):
        if key in original:state[key]=copy.deepcopy(original[key])
    state['restored_from']={'original_config_digest':original['config_digest'],'original_last_attempt':original['attempts'][-1]['id']}
    owner.save(state)


def seed(cfg,original):
    # Dependency paths are part of the frozen guidance contract. Validate/seed in
    # the TARGET runtime, never with installer/worker module globals.
    packet=Path(cfg['root']).parent/'restore-seed.json';atomic_json(packet,{'config':cfg,'original':original})
    code="import sys;sys.path.insert(0,sys.argv[1]);from review_storage import read_json;from research_restore import _seed_local;p=read_json(sys.argv[2]);_seed_local(p['config'],p['original'])"
    result=subprocess.run([sys.executable,'-I','-S','-B','-c',code,cfg['runtime_root'],str(packet)],
        capture_output=True,text=True,encoding='utf-8',timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('restore_target_seed_failed:'+result.stderr.splitlines()[-1][:180])


def control(cfg,config,action,request_id=None):
    from dashboard_control import run_public
    args=['--config',str(config),'--control',action]
    if action=='resume':
        from research_pause import generation
        args+=['--expected-pause',generation(cfg['root']),'--request-id',request_id]
    return run_public(cfg,'research_event_service.py',args)


def stop_paused(cfg,config,folder):
    try:
        from dashboard_control import host
        host(cfg)
    except (OSError,ValueError,KeyError):return
    control(cfg,config,'drain');control(cfg,config,'pause');control(cfg,config,'stop')
    deadline=time.monotonic()+20
    from research_runtime import owner_alive
    while owner_alive(cfg) and time.monotonic()<deadline:time.sleep(.1)
    if owner_alive(cfg):raise ValueError('cancelled_restore_host_still_draining')


def directive(saved,folder,state):
    if not Path(saved['index']).exists():return
    with locked(Path(saved['project'])/'.bmad-ml/restores','.index.lock','study_index_update_busy'):
        index=read_json(saved['index'])
        if index.get('latest_restore')!=str(Path(folder)/'state.json'):return
        index['user_directive']={'state':state,'requires_explicit_resume':state!='active',
            'source':'web-checked-resume','request_id':Path(folder).name}
        atomic_json(saved['index'],index)


def work(folder):
    folder=Path(folder).resolve();saved=read_json(folder/'state.json');project=Path(saved['project']).resolve()
    if not folder.is_relative_to(project/'.bmad-ml/restores') or str(uuid.UUID(folder.name))!=folder.name:raise ValueError('restore_worker_scope_changed')
    if {p.name:sha(p) for p in Path(saved['worker_root']).glob('*.py')}!=saved['worker_manifest']:raise ValueError('restore_worker_implementation_changed')
    if saved['phase']!='requested':return # Unknown launch is reconciled, never blindly replayed.
    cfg=read_json(saved['original_config']);target=cfg;config=Path(saved['original_config']);started=False;published=False
    with locked(folder,'.worker.lock','restore_worker_already_owned'),locked(Path(cfg['root']),'.restore.lock','original_restore_already_owned'):
        try:
            update(folder,'checking')
            if sha(config)!=saved['original_config_sha256'] or digest(cfg)!=saved['request']['config_sha256']:raise ValueError('restore_original_config_changed')
            from research_pause import generation
            if generation(cfg['root'])!=saved['request']['pause_generation']:raise ValueError('pause_generation_changed_refresh_required')
            if sha(saved['index'])!=saved['index_sha256']:raise ValueError('registered_study_changed_before_restore')
            with locked(cfg['root'],'.supervisor.lock','original_supervisor_still_running'):
                report,original,context,evidence=preflight(cfg)
            atomic_json(folder/'preflight.json',{'report':report,'context':context,'completed_review_evidence':evidence,
                'original_state_sha256':digest(original),'model_calls':0,'platform_writes':0})
            if (folder/'CANCEL').exists():update(folder,'cancelled');return
            if 'research_restore.py' not in cfg['runtime_manifest']:
                update(folder,'preparing')
                if not original or cfg.get('component_pipeline',{}).get('version')!=4:raise ValueError('unsupported_legacy_restore_boundary')
                from research_runtime import adopt
                destination=project/'.research/restores'/folder.name
                patch=folder/'adoption-patch.json';atomic_json(patch,{'component_pipeline':{'additional_evidence':list({row['path']:row for row in [*cfg['component_pipeline'].get('additional_evidence',[]),*evidence]}.values())}})
                config=destination/'service.json'
                adopt(saved['original_config'],None,destination/'runtime',destination/'service',config,patch)
                target=read_json(config);seed(target,original)
            pointer(target,project,folder)
            update(folder,'starting',target_config=str(config),target_config_sha256=sha(config),cold_adoption=target['root']!=cfg['root'])
            gates(target,folder)
            if (folder/'CANCEL').exists():
                atomic_json(Path(target['root'])/'host-stop.json',{'config_sha256':digest(target),'at':time.time(),'reason':'cancelled-before-start'})
                update(folder,'cancelled');return
            # A single original frozen supervisor owns all model/platform work.
            launched=time.time()
            with (folder/'supervisor.stdout.log').open('xb') as out,(folder/'supervisor.stderr.log').open('xb') as err:
                child=subprocess.Popen([sys.executable,'-B',str(Path(target['runtime_root'])/'research_supervisor.py'),'--config',str(config)],
                    stdin=subprocess.DEVNULL,stdout=out,stderr=err,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))
            _children['supervisor']=child
            started=True;atomic_json(folder/'supervisor-launch.json',{'pid':child.pid,'at':launched,'config_sha256':digest(target)})
            deadline=time.monotonic()+90;live=None
            while time.monotonic()<deadline:
                if (folder/'CANCEL').exists():update(folder,'cancelling')
                live=health(target,launched)
                if live:break
                if child.poll() is not None:raise ValueError('restored_supervisor_exited_before_ready')
                time.sleep(.15)
            if not live:raise ValueError('restored_host_readiness_unresolved')
            update(folder,'ready',host_identity={k:live[k] for k in ('pid','project_id','config_sha256')})
            if (folder/'CANCEL').exists():
                stop_paused(target,config,folder);directive(saved,folder,'paused');update(folder,'cancelled');return
            # Publish only the real paused/healthy host. Original index bytes are archived.
            with locked(project/'.bmad-ml/restores','.index.lock','study_index_update_busy'):
                if sha(saved['index'])!=saved['index_sha256']:raise ValueError('registered_study_changed_before_publish')
                index=read_json(saved['index']);atomic_json(folder/'original-index.json',index)
                index.update(config=str(config),progress=str(Path(target['root'])/'component-pipeline/progress.json'),
                    supervision=str(Path(target['root'])/'supervision.json'),latest_restore=str(folder/'state.json'),
                    automatic_host_restores=index.get('automatic_host_restores',0)+1)
                index['user_directive']={'state':'resume-requested','requires_explicit_resume':False,'source':'web-checked-resume','request_id':folder.name}
                atomic_json(saved['index'],index);published=True
            update(folder,'resuming');control(target,config,'resume',folder.name)
            if (folder/'CANCEL').exists():
                stop_paused(target,config,folder);directive(saved,folder,'paused');update(folder,'cancelled');return
            from dashboard_control import host
            actual=host(target)
            if actual.get('paused') or actual.get('drain',{}).get('requested'):raise ValueError('restored_host_resume_not_verified')
            directive(saved,folder,'active')
            update(folder,'resumed',scope='original study, Context and task Guards; no chat message',same_thread_id=context.get('thread_id'))
        except Exception as error:
            # If a restore operation fails after launch, keep the new host quiescent.
            if started:
                try:stop_paused(target,config,folder)
                except Exception:pass
                # Never leave startup able to dispatch after a failed/unknown control outcome.
                atomic_json(Path(target['root'])/'DRAINING',{'config_sha256':digest(target),'at':time.time()})
                atomic_json(Path(target['root'])/'PAUSED',{'at':time.time(),'reason':'restore-blocked'})
                if not published and child.poll() is None:
                    child.terminate();child.wait(timeout=10) # only this paused startup's owned supervisor

            if published:directive(saved,folder,'paused')
            update(folder,'blocked',reason=str(error)[:240],index_published=published,
                recovery='reconcile this same restore identity; never submit a replacement task')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--work',type=Path)
    parser.add_argument('--project-root',type=Path);parser.add_argument('--request-file',type=Path)
    args=parser.parse_args()
    if args.work:
        work(args.work)
        # Keep the supervisor process handle until it exits; closing the browser does not own this lifetime.
        if 'supervisor' in _children:_children['supervisor'].wait()
    elif args.project_root and args.request_file:
        from dashboard_control import resume
        print(json.dumps(resume(args.project_root,read_json(args.request_file)),ensure_ascii=False))
    else:parser.error('use --work or --project-root with --request-file')
