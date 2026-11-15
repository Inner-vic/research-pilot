"""Same-attempt watcher and independent, bounded local process supervisor."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from review_storage import atomic_json
from task_pipeline import digest
from task_event_collector import collector_lock
from devspace_execution import project_execution, emit, validate, recovery_policy as policy


def watch(config, output, receipt=None, allow_live=False):
    root = Path(output).absolute()
    rules = policy(config)
    advance = project_execution(config,root)
    # Unlike the per-tick execution lock, this owns the full observer lifetime.
    with collector_lock(str(root / 'observer')):
        while True:
            # project_execution owns the same pre-action guard for public
            # single-step calls and watched calls, including worker decisions.
            result = advance(config, root, receipt, allow_live)
            from research_observation import capture_execution
            observation = capture_execution(config, result, root)
            if observation is not None:
                result = {**result, 'research_observation': observation}
            receipt = None
            settling = result['status'] == 'launch-outcome-unknown' and time.time() < min(
                result['deadline'] if result['deadline'] is not None else float('inf'), result['created_at'] + 30)
            transient = result['status'] == 'awaiting-reconciliation' and result.get('retryable_collection')
            if transient:
                failures = result['consecutive_collection_failures']
                if failures > rules['read_retries']:
                    result['observer_retry_exhausted'] = True
                    active = Path(result.get('execution_root', root))
                    active_config = json.loads(Path(result['execution_project']).read_bytes()) if result.get('execution_project') else config
                    single_state = json.loads((active / 'execution.json').read_bytes())
                    single_state['observer_retry_exhausted'] = True
                    atomic_json(active / 'execution.json', single_state)
                    atomic_json(root / 'execution.json', result)
                    emit(active_config, active, single_state, 'collection_recovery_exhausted',
                         {'error': result['last_error'], 'automatic_resubmit': False}, result.get('failure_generation', 0),
                         config if config.get('devspace_study') else None)
                    return result
                time.sleep(min(30, rules['retry_delay'] * failures))
                continue
            starting = result['status']=='awaiting-start-readback' and 'start' in result.get('intents', [])
            if not settling and not starting and result['status'] not in {'running', 'cleanup-required', 'awaiting-process-stop'}:
                return result
            time.sleep(.2 if result['evidence_kind'] == 'cpu-simulation' else 5)


def background(config, project, output, entry, allow_live=False):
    """Detach existing supervision, return promptly, and never duplicate its claim."""
    if config.get('devspace_study'):
        from devspace_study import validate as validate_study
        validate_study(config)
    _,plan,_,kind=validate(config)
    if kind!='cpu-simulation':
        if not allow_live:raise ValueError('explicit_allow_devspace_live_required')
        if config.get('research_worker'):
            from research_worker import settings as worker_settings
            worker_settings(config, output)
            if plan['resource']['route']=='native-ui':
                raise ValueError('independent_native_lifecycle_not_qualified_keep_active_agent')
        else:
            from task_event_wakeup import settings
            wake=settings(config)
            if not wake or not {'process_terminal','cleanup_pending'} & set(wake.get('trigger_kinds', [])):
                raise ValueError('terminal_wakeup_required_before_background_handoff')
    root=Path(output).resolve(); project=Path(project).resolve(); entry=Path(entry).resolve()
    state=json.loads((root/'execution.json').read_bytes())
    if state['status'] not in {'running','launch-outcome-unknown','awaiting-start-readback'}:
        raise ValueError('started_attempt_required_before_background_handoff')
    if 'start' not in state.get('intents', []) and kind!='cpu-simulation':
        raise ValueError('recorded_start_required_before_background_handoff')
    fingerprint=digest({'config':config,'project':str(project),'entry':str(entry),'output':str(root),
                        'execution_binding':state['binding']})
    folder=root/'background-observers'; folder.mkdir(exist_ok=True)
    path=folder/(state['binding'].split(':')[-1]+'.json')
    with collector_lock(str(root/'background-handoff')):
        if path.exists():
            previous=json.loads(path.read_bytes())
            if previous['fingerprint']!=fingerprint:raise ValueError('background_handoff_binding_changed')
            return {**previous,'status':'existing-background-handoff','new_process_started':False}
        try:
            with collector_lock(str(root/'supervisor')):pass
        except ValueError as error:
            if 'collector_already_running' not in str(error):raise
            return {'status':'existing-supervisor-active','new_process_started':False,
                    'yield_turn':True,'execution_binding':state['binding']}
        record={'schema':'sdm-background-handoff/v1','fingerprint':fingerprint,'execution_binding':state['binding'],
                'status':'launch-outcome-unknown','started_at':time.time(),'yield_turn':True,
                'project':str(project),'output':str(root),'evidence_kind':kind}
        atomic_json(path,record)  # unknown process launches are not silently retried
        argv=[sys.executable,'-B',str(entry),'--project',str(project),'--output',str(root),
              '--devspace-execution','--supervise-devspace']+(['--allow-devspace-live'] if allow_live else [])
        flags=(getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
               |getattr(subprocess,'DETACHED_PROCESS',0))
        with path.with_suffix('.stdout.log').open('ab') as stdout, path.with_suffix('.stderr.log').open('ab') as stderr:
            child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                creationflags=flags,start_new_session=os.name!='nt')
        record.update(supervisor_pid=child.pid,status='background-observation-started',new_process_started=True)
        atomic_json(path,record)
        until=time.time()+3
        while time.time()<until:
            sup=root/'supervisor.json'
            if sup.exists():
                value=json.loads(sup.read_bytes())
                if value.get('supervisor_pid')==child.pid and value.get('status')=='watching':
                    record.update(observer_pid=value['child_pid'],observer_started_verified=True);break
            if child.poll() is not None:
                record.update(status='background-handoff-failed',exit_code=child.returncode);break
            time.sleep(.05)
        atomic_json(path,record)
        return record


def supervise(config, project, output, entry, allow_live=False):
    if config.get('devspace_study'):
        from devspace_study import validate as validate_study
        validate_study(config)
    _, plan, binding, kind = validate(config)
    if kind != 'cpu-simulation' and not allow_live:
        raise ValueError('explicit_allow_devspace_live_required')
    rules = policy(config)
    root = Path(output).absolute(); root.mkdir(parents=True, exist_ok=True)
    project = Path(project).resolve(); entry = Path(entry).resolve()
    fingerprint = digest({'config': config, 'project': str(project), 'entry': str(entry), 'output': str(root)})
    with collector_lock(str(root / 'supervisor')):
        path = root / 'supervisor.json'
        state = json.loads(path.read_bytes()) if path.exists() else {
            'schema': 'sdm-devspace-supervisor/v1', 'binding': binding, 'fingerprint': fingerprint,
            'restart_count': 0, 'children': [], 'uid': plan['resource']['uid']}
        if state['fingerprint'] != fingerprint:
            raise ValueError('supervisor_binding_changed')
        while True:
            state.update(status='starting', supervisor_pid=os.getpid())
            atomic_json(path, state)
            index = len(state['children'])
            argv = [sys.executable, '-B', str(entry), '--project', str(project), '--output', str(root),
                    '--devspace-execution', '--watch-devspace'] + (['--allow-devspace-live'] if allow_live else [])
            with (root / f'observer-{index}.stdout.log').open('ab') as out, (root / f'observer-{index}.stderr.log').open('ab') as err:
                child = subprocess.Popen(argv, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                row = {'pid': child.pid, 'started_at': time.time()}
                state['children'].append(row); state.update(status='watching', child_pid=child.pid)
                atomic_json(path, state)
                while child.poll() is None:
                    if (root / 'supervisor.stop').exists():
                        child.terminate(); child.wait(timeout=15)
                        state['status'] = 'stopped-locally'; atomic_json(path, state); return state
                    time.sleep(.2)
                row.update(exit_code=child.returncode, finished_at=time.time())
            current = json.loads((root / 'execution.json').read_bytes()) if (root / 'execution.json').exists() else {}
            active = Path(current.get('execution_root', root))
            active_config = json.loads(Path(current['execution_project']).read_bytes()) if current.get('execution_project') else config
            active_state = json.loads((active / 'execution.json').read_bytes()) if (active / 'execution.json').exists() else current
            if child.returncode == 0:
                if config.get('research_worker'):
                    from research_worker import run as decide
                    state.update(status='deciding', decision_worker_pid=os.getpid())
                    atomic_json(path, state)
                    state['decision_result'] = decide(config, root, allow_live)
                    current = json.loads((root / 'execution.json').read_bytes())
                state.update(status='handoff', execution_status=current.get('status'))
                atomic_json(path, state); return state
            stderr = (root / f'observer-{index}.stderr.log').read_text(encoding='utf-8', errors='replace')
            if 'collector_already_running' in stderr or state['restart_count'] >= rules['max_restarts']:
                state.update(status='blocked', reason='observer_conflict' if 'collector_already_running' in stderr else 'restart_limit')
                atomic_json(path, state)
                if current:
                    emit(active_config, active, active_state, 'observer_recovery_exhausted', {'reason': state['reason']},
                         notification_config=config if config.get('devspace_study') else None)
                return state
            state['restart_count'] += 1
            state.update(status='restarting', last_exit_code=child.returncode)
            atomic_json(path, state)
            if current:
                emit(active_config, active, active_state, 'observer_restarted', {'restart_count': state['restart_count'], 'previous_pid': child.pid}, state['restart_count'],
                     config if config.get('devspace_study') else None)
            time.sleep(rules['restart_delay'])
