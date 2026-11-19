"""Start the original frozen supervisor once, register its view, verify ownership.

No science, reviews, platform submission or replacement experiment loop lives here.
Existing/unknown launches use the original restore/reconciliation path, never retry.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request,urlopen

if __package__ in (None, ''):
    sys.path.insert(0,str(Path(__file__).resolve().parent))

from review_storage import atomic_json,locked,native_path,read_json
from task_pipeline import digest
from research_runtime import owner_alive


def scoped(config_path,project):
    config_path=Path(config_path).resolve();project=Path(project).resolve()
    if not project.is_dir() or not config_path.is_relative_to(project):
        raise ValueError('research_config_must_belong_to_current_project')
    cfg=read_json(config_path)
    from research_task_scope import read as read_scope
    task_folder=project/'.bmad-ml/task-intents'/str(cfg.get('project_id','invalid'))
    if task_folder.resolve().is_relative_to(project/'.bmad-ml/task-intents') and native_path(task_folder/'intent.json').exists() and read_scope(task_folder/'intent.json').get('mode')!='continuous-research':
        raise ValueError('single_evaluation_cannot_launch_research')
    if cfg.get('schema')!='sdm-event-service/v3' or cfg.get('component_pipeline',{}).get('version')!=4:
        raise ValueError('single_study_or_analysis_is_not_managed_research')
    from research_event_service import configuration
    configuration(cfg,control_only=True)
    from research_continuation import enabled
    if not enabled(cfg):raise ValueError('managed_research_continuation_policy_required')
    root=Path(cfg['root']).resolve()
    if not root.is_relative_to(project):raise ValueError('research_store_must_belong_to_current_project')
    for key in ('coordinator_template','implementation_template','source_template'):
        if not cfg.get('task_templates',{}).get(cfg['component_pipeline'].get(key)):
            raise ValueError('managed_research_continuation_templates_required')
    return cfg,root,project,config_path


def inspect(config_path,project):
    cfg,root,project,config_path=scoped(config_path,project)
    state=read_json(root/'component-pipeline/pipeline.json') if (root/'component-pipeline/pipeline.json').exists() else None
    if state and state.get('config_digest')!=digest(cfg):raise ValueError('original_pipeline_configuration_changed')
    terminal=bool(state and state['status'] in ('research-goal-met','research-stopped'))
    paused=any((root/name).exists() for name in ('PAUSED','DRAINING','host-stop.json','STOP_AFTER_DRAIN'))
    from research_progress import probe,runtime_view as progress_view
    host=probe(cfg,paused=paused,terminal=terminal,owner_check=owner_alive,fetch=urlopen)
    progress=progress_view(state,owner_alive=host['owner_alive'],worker_ready=host['worker_ready'],paused=host['paused'])
    from research_task_scope import handoff,analysis_available
    task_handoff=handoff(cfg,state or {},progress,analysis_available(state))
    return {'task_handoff':task_handoff,'schema':'sdm-managed-research-status/v1','project_id':cfg['project_id'],'config_sha256':digest(cfg),
        'owner_alive':host['owner_alive'],'controller_running':progress['controller_running'],
        'automatic_iteration_active':progress['automatic_iteration_active'],
        'status':'research-blocked' if progress['state']=='blocked' else host['status'],
        'stage':progress['stage'],'progress':progress,
        'analysis_completion_is_research_completion':False,'original_records_preserved':True,
        'model_calls':0,'platform_writes':0}


def register(config_path,project):
    cfg,root,project,config_path=scoped(config_path,project)
    with locked(project/'.bmad-ml','.registration.lock','research_registration_busy'):
        index=_register(cfg,root,project,config_path)
        from research_task_scope import update
        task_folder=project/'.bmad-ml/task-intents'/cfg['project_id']
        if task_folder.resolve().is_relative_to(project/'.bmad-ml/task-intents') and native_path(task_folder/'intent.json').exists():update(project,cfg['project_id'],config=config_path)
        return index


def _register(cfg,root,project,config_path):
    primary=project/'.bmad-ml/CURRENT.json';index=primary
    for candidate in (primary,*sorted(primary.parent.glob('*/CURRENT.json'))):
        if not candidate.exists():continue
        record=read_json(candidate)
        if record.get('study_id')==cfg['project_id']:
            if record.get('schema')!='bmad-ml-active-study/v1' or Path(record.get('config','')).resolve()!=config_path:
                raise ValueError('research_registration_owned_by_another_configuration')
            return str(candidate)
    if primary.exists():
        previous=read_json(primary)
        if previous.get('study_id')!=cfg['project_id']:
            index=project/'.bmad-ml'/('study-'+hashlib.sha256(cfg['project_id'].encode()).hexdigest()[:16])/'CURRENT.json'
    value={'schema':'bmad-ml-active-study/v1','study_id':cfg['project_id'],'root':str(project),
        'config':str(config_path),'progress':str(root/'component-pipeline/progress.json'),
        'supervision':str(root/'supervision.json'),'timer':'not-used'}
    if index.exists():
        previous=read_json(index)
        if any(previous.get(k)!=value[k] for k in ('schema','study_id','config')):
            raise ValueError('research_registration_owned_by_another_study')
        # Preserve original provenance and optional display metadata.
        value={**previous,**value}
    else:
        try:value['origin_codex_chat_id']=str(__import__('uuid').UUID(os.environ.get('CODEX_THREAD_ID','')))
        except ValueError:pass
    atomic_json(index,value)
    return str(index)


def launch(config_path,project,timeout=20):
    cfg,root,project,config_path=scoped(config_path,project)
    if Path(__file__).resolve().parent!=Path(cfg['runtime_root']).resolve():
        raise ValueError('launch_from_original_frozen_runtime_required')
    if not 0<timeout<=30:raise ValueError('bounded_launch_readiness_wait_required')
    root.mkdir(parents=True,exist_ok=True)
    with locked(root,'.managed-launch.lock','research_launch_already_owned'):
        status=inspect(config_path,project)
        if status.get('controller_running') or status['automatic_iteration_active']:
            return {**status,'launch':'already-running' if status['automatic_iteration_active'] else 'already-running-blocked',
                    'index':register(config_path,project)}
        if status['status'] in ('completed','paused-or-stopped'):
            raise ValueError('completed_or_paused_research_cannot_be_launched')
        if status['owner_alive']:raise ValueError('existing_research_owner_requires_reconciliation')
        marker=root/'managed-launch.json'
        if marker.exists() or (root/'component-pipeline/pipeline.json').exists() or (root/'supervision.json').exists():
            raise ValueError('existing_research_requires_original_restore_not_new_launch')
        script=Path(cfg['runtime_root'])/'research_supervisor.py'
        expected=cfg['runtime_manifest'].get(script.name)
        if not expected or hashlib.sha256(native_path(script).read_bytes()).hexdigest()!=expected:
            raise ValueError('original_frozen_supervisor_changed')
        index=register(config_path,project)
        binding={'config_sha256':digest(cfg),'project_id':cfg['project_id'],'supervisor_sha256':expected}
        atomic_json(marker,{'schema':'sdm-managed-launch/v1','binding':binding,'phase':'launch-intent','at':time.time(),
            'scope':'Original supervisor owns the lifecycle; no new execution authority or budget.'})
        try:
            with native_path(root/'managed-launch.stdout.log').open('xb') as out,native_path(root/'managed-launch.stderr.log').open('xb') as err:
                child=subprocess.Popen([sys.executable,'-I','-S','-X','utf8','-B',str(script),'--config',str(config_path)],
                    stdin=subprocess.DEVNULL,stdout=out,stderr=err,start_new_session=os.name!='nt',
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))
        except Exception:
            atomic_json(marker,{'schema':'sdm-managed-launch/v1','binding':binding,'phase':'launch-outcome-unresolved','at':time.time()})
            raise
        atomic_json(marker,{'schema':'sdm-managed-launch/v1','binding':binding,'phase':'spawned','pid':child.pid,'at':time.time()})
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try:
                status=inspect(config_path,project)
                if status.get('controller_running') or status['automatic_iteration_active']:
                    return {**status,'launch':'started' if status['automatic_iteration_active'] else 'started-blocked',
                        'index':index,'submission_owner':'original-Manager/Guard',
                        'foreground_may_return':True}
            except (OSError,ValueError,KeyError):pass
            if child.poll() is not None:break
            time.sleep(.1)
        raise ValueError('research_launch_not_verified_reconcile_original_launch')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--project-root',type=Path,required=True);parser.add_argument('--inspect',action='store_true')
    args=parser.parse_args();print(json.dumps((inspect if args.inspect else launch)(args.config,args.project_root),ensure_ascii=False))
