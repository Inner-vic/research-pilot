"""Frozen official transport -> verified result -> existing native delivery Inbox.

No submission/stop/restart or scientific decisions. Config and code are frozen
by the owning event service. A durable completion envelope is replayed verbatim.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone

import task_event_collector as collector
from component_results import marker_lines, verify
from review_storage import atomic_json, locked, read_json, native_path


def file_ref(ref):
    path = Path(ref['path'])
    if not path.is_absolute() or hashlib.sha256(path.read_bytes()).hexdigest() != ref['sha256']:
        raise ValueError('component_source_frozen_input_changed')
    return path


def settings(spec, cfg, registered=None):
    if spec.get('version') not in (1, 2, 3) or cfg.get('schema') not in ('sdm-event-service/v2','sdm-event-service/v3'):
        raise ValueError('component_source_requires_new_event_service_v2')
    if spec.get('binding') not in cfg.get('tasks',{}) and registered is None:
        raise ValueError('registered_component_completion_binding_required')
    for name in ('manager_config', 'plan'):
        file_ref(spec[name])
    manager = read_json(spec['manager_config']['path'])
    plan = read_json(spec['plan']['path'])
    transport = manager.get('component_transport')
    if spec.get('version') == 3:
        if (transport not in ('official-cli', 'official-sdk') or spec.get('transport') != transport
                or plan.get('route') != transport):
            raise ValueError('frozen_official_component_transport_required')
    elif transport != 'official-cli' or plan.get('route') != 'official-cli':
        raise ValueError('original_official_cli_task_plan_required')
    if plan.get('request', {}).get('action') != 'task.submit':
        raise ValueError('original_official_task_plan_required')
    if spec.get('version') == 2 or (spec.get('version') == 3 and 'readonly_runtime_repair' in spec):
        from lpai_manager import check_readonly_runtime
        repair = file_ref(spec['readonly_runtime_repair'])
        check_readonly_runtime(spec['plan']['path'], repair)
    elif 'readonly_runtime_repair' in spec:
        raise ValueError('readonly_runtime_repair_requires_supported_component_source')
    if spec.get('expected', {}).get('config', {}).get('epochs', 0) <= 0:
        raise ValueError('frozen_component_result_contract_required')
    if 'result_identity' in spec and (spec['version']!=3 or
            spec['result_identity']!={'version':1,'mode':'original-manager-native-config'}):
        raise ValueError('explicit_versioned_component_result_identity_required')
    collector.validate({'task_event_collector':{'version':2, 'store':str(Path(cfg['root'])/'component-source/observations.sqlite'),
        'tasks':[], 'phase_intervals_seconds':spec.get('phase_intervals_seconds')}})
    return manager, plan


def log_window(task, worker=None):
    """Bind historical logs to observed times, not the CLI's recent default."""
    def parse(value):
        if isinstance(value,dict):value=value.get('time')
        if not value:return None
        if not isinstance(value,str):raise ValueError('official_timestamp_shape_required')
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        if result.tzinfo is None:raise ValueError('official_timestamp_timezone_required')
        return result.astimezone(timezone.utc)
    worker=worker or {}
    # Control-plane and Pod times can be out of order: a real completed worker
    # reported finish before API creation. Span ALL observed times for this
    # exact UID/Pod; earliest-create/latest-finish alone dropped every marker.
    points=[parse(obj.get(key)) for obj in (task,worker) for key in
        ('create_timestamp','start_timestamp','job_init_timestamp','schedule_timestamp',
         'update_timestamp','finish_timestamp')]
    points=[value for value in points if value]
    if len(points)<2:
        raise ValueError('terminal_log_observed_time_window_required')
    return ((min(points)-timedelta(seconds=60)).isoformat().replace('+00:00','Z'),
            (max(points)+timedelta(seconds=60)).isoformat().replace('+00:00','Z'))


def read_logs(manager, task, pod, errors_only=False, worker=None):
    pattern = 'Traceback|Error|Exception|failed' if errors_only else '^SDM_JSON '
    start,end=log_window(task,worker)
    args = [manager['official_cli'], 'logs', 'pod', '--namespace', task['namespace'],
            '--cluster', task['cluster'], '--pod', pod, '--container', 'main',
            '--direction', 'forward', '--limit', '5000', '--grep', pattern,
            '--start',start,'--end',end]
    result = subprocess.run(args, capture_output=True, encoding='utf-8', errors='replace',
        timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('component_result_log_read_unavailable')
    value = json.loads(result.stdout)
    if value.get('status') != 'ok':
        raise ValueError('component_result_log_not_ok')
    return value


def publish_bytes(path, raw):
    path = native_path(path)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('original_component_artifact_changed')
        return
    temp = path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temp.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)


class ComponentSource:
    def __init__(self, inbox, spec, root=None, deliver=None):
        self.inbox, self.spec = inbox, spec
        self.manager_config, self.plan = settings(spec, inbox.cfg, inbox.task(spec.get('binding')))
        self.root = Path(root) if root is not None else inbox.root/'component-source'
        if not self.root.resolve().is_relative_to(inbox.root.resolve()):
            raise ValueError('component_source_state_inside_owning_inbox_required')
        self.root.mkdir(parents=True,exist_ok=True)
        self.deliver = deliver
        self.unresolved = None
        self.binding = spec['binding']
        self.observer = {'version':2,'store':str(self.root/'observations.sqlite'), 'tasks':[],
            'phase_intervals_seconds':spec['phase_intervals_seconds'], 'failure_backoff_max_seconds':900}

    def reconcile(self):
        # Original Manager/Guard performs read-only discovery/configuration
        # verification. It is never replaced by another submission ledger.
        from lpai_manager import Manager
        manager = Manager(self.manager_config)
        receipt = self.root/'verified-submission.json'
        if receipt.exists():
            saved = read_json(receipt)
            guard = manager.guard(self.plan)
            with guard.lock():
                guard.load()
                if guard.state['phase'] != 'completed' or guard.state.get('uid') != saved['resource']['uid']:
                    raise ValueError('component_source_original_guard_changed')
            return manager, saved
        repair = self.spec.get('readonly_runtime_repair')
        result = (manager.reconcile(self.spec['plan']['path'], runtime_repair=file_ref(repair)) if repair
                  else manager.reconcile(self.spec['plan']['path']))
        if result['status'] == 'already-completed':
            guard = manager.guard(self.plan)
            with guard.lock():
                guard.load(); result = manager.finish(self.plan, guard)
        if result['status'] != 'verified':
            self.unresolved = result
            return None
        if receipt.exists():
            original = read_json(receipt)
            if original['resource']['uid'] != result['resource']['uid'] or original['resource']['data_digest'] != result['resource']['data_digest']:
                raise ValueError('component_source_original_submission_changed')
            result = original
        else:
            atomic_json(receipt, result)
        return manager, result

    def produce(self, manager, receipt, task):
        resource = receipt['resource']; uid = resource['uid']; namespace = resource['namespace']
        if task['status'] not in collector.TERMINAL:
            return None
        cfg = {'cli_path':self.manager_config['official_cli'], 'query_timeout_seconds':30}
        pods = collector.query(cfg, {'uid':uid,'namespace':namespace}, 'pods')
        workers = [p for p in pods if p.get('task_uid') == uid and p.get('namespace') == namespace
                   and p.get('step') == 'task' and p.get('pod_type') == 'user']
        if not workers:raise ValueError('component_terminal_pod_not_ready')
        if len(workers) != 1:
            raise ValueError('qualified_single_worker_result_source_required')
        pod = workers[0]['name']
        expected = {**self.spec['expected'], **{k:resource[k] for k in ('uid','namespace','owner','cluster')},
                    'component_uid':self.plan['request']['uid']}
        if self.spec.get('result_identity'):
            expected['submission_identity']={'version':1,'manager_plan':self.spec['plan']}
        if task['status'] == 'succeeded':
            logs = read_logs(self.manager_config, task, pod, worker=workers[0])
            facts, artifacts = verify(task, receipt, marker_lines(logs, namespace, pod), expected)
            for name, raw in artifacts.items():
                publish_bytes(self.root/name, raw)
            ready = {'schema':'sdm-component-result-ready/v1', **facts,
                     'artifact_root':str(self.root), 'original_plan_sha256':self.spec['plan']['sha256']}
            phase = 'exited'
        else:
            from task_event_service import log_lines
            logs = read_logs(self.manager_config, task, pod, errors_only=True, worker=workers[0])
            ready = {'schema':'sdm-component-diagnosis-ready/v1', 'uid':uid, 'namespace':namespace,
                     'platform_status':task['status'], 'valid_training_round':False,
                     'logs':log_lines(logs['data']), 'original_plan_sha256':self.spec['plan']['sha256']}
            phase = 'worker_failed'
        ready_path = self.root/'result-ready.json'
        atomic_json(ready_path, ready)
        state = {'binding':self.binding, 'phase':phase, 'task_uid':uid, 'namespace':namespace,
                 'platform_status':task['status'], 'valid_training_round':ready['valid_training_round'],
                 'result_ready':{'path':str(ready_path),'sha256':hashlib.sha256(ready_path.read_bytes()).hexdigest()}}
        from research_event_service import digest
        return {'schema':'sdm-completion-event/v1','binding':self.binding,'phase':phase,
                'state':state,'state_sha256':digest(state)}

    def cycle(self):
        # The publication file is the transactional outbox. A crash after
        # publication/before acceptance replays exactly the same envelope.
        output = self.root/'completion-event.json'
        if output.exists():
            event = read_json(output)
            ready = event['state']['result_ready']
            file_ref(ready)
        else:
            resolved = self.reconcile()
            if resolved is None:
                status = ('submission-outcome-unknown' if self.unresolved and
                          self.unresolved.get('status') == 'submission-outcome-unresolved' else 'pending-approval')
                return status, self.spec['phase_intervals_seconds']['queued']
            manager, receipt = resolved
            task = receipt['resource']
            key = task['namespace']+'/'+task['uid']
            db = collector.connect(self.observer, 'live-platform-observation')
            try:
                previous = db.execute('SELECT * FROM observations WHERE task_key=?',(key,)).fetchone()
                remaining = collector.wait_remaining(self.observer, previous, time.time())
                if previous and previous['snapshot'] and json.loads(previous['snapshot']).get('task_terminal'):
                    remaining = 0  # artifact retries have their own backoff, not terminal-cleanup cadence
                if remaining > 0:
                    return 'waiting-observation', remaining
                bound = {'uid':task['uid'],'namespace':task['namespace']}
                try:
                    observation = manager.checked('task', uid=task['uid'])
                except Exception:
                    collector.observe(db, bound, error=True)
                    raise
                collector.observe(db, bound, native=observation, pods=None)
                latest = db.execute('SELECT * FROM observations WHERE task_key=?',(key,)).fetchone()
                delay = collector.wait_remaining(self.observer, latest, time.time())
            finally:
                db.close()
            current = observation['status']
            # Derived monitor facts survive a collector/log failure. The original
            # task and result outbox remain the only completion authority.
            now=time.time()
            monitor={'schema':'sdm-component-monitor/v1','uid':task['uid'],'namespace':task['namespace'],
                'platform_status':current,'observed_at':now,'next_observation_at':now+delay,
                'phase':'collecting' if current in collector.TERMINAL else 'watching',
                'completion_authority':'verified result-ready and completion outbox, never status alone'}
            terminal=self.root/'terminal-observed.json'
            if current in collector.TERMINAL:
                if not terminal.exists():atomic_json(terminal,{**monitor,'first_terminal_observed_at':now})
                first=read_json(terminal)
                if first['uid']!=task['uid'] or first['namespace']!=task['namespace']:
                    raise ValueError('original_terminal_observation_identity_changed')
                monitor['first_terminal_observed_at']=first['first_terminal_observed_at']
            atomic_json(self.root/'monitor.json',monitor)
            if current not in collector.TERMINAL:
                return current, delay
            event = self.produce(manager, receipt, observation)
            if event is None:
                return 'collecting', 15
            atomic_json(output, event)
            monitor.update(phase='result-verified',result_verified_at=time.time(),next_observation_at=None)
            atomic_json(self.root/'monitor.json',monitor)
        accepted = (self.deliver or self.inbox.accept)(event)
        atomic_json(self.root/'delivery.json', accepted)
        return 'delivered', None

    def run(self):
        with locked(self.root, '.source.lock', 'component_source_already_owned'):
            errors = 0
            while not self.inbox.done.is_set():
                try:
                    # Frozen inputs are checked again before every provider read.
                    settings(self.spec, self.inbox.cfg, self.inbox.task(self.binding))
                    status, delay = self.cycle(); errors = 0
                    atomic_json(self.root/'health.json', {'status':status,'observed_at':time.time(),'model_calls':0})
                    if delay is None:
                        return
                except Exception as exc:
                    errors += 1; delay = min(900,15*2**min(errors,6))
                    atomic_json(self.root/'health.json', {'status':'observation-or-collection-unavailable',
                        'error_type':type(exc).__name__,'at':time.time(),'retry_after_seconds':delay,
                        'original_task_preserved':True,'model_calls':0})
                self.inbox.done.wait(delay)
