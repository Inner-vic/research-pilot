"""Shared resumable execution for an explicitly authorized existing devspace.

The coordinator owns resource intents, a detached capsule owns the workload
deadline, and the existing outbox owns notification delivery. No project code is
imported by the coordinator. Local transport is CPU simulation, never LPAI proof.
"""
import base64
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import time

from review_storage import atomic_json
from task_pipeline import digest
import task_event_collector as collector
import task_event_service as service
import task_event_wakeup as wakeup
from submission_guard import Guard


def recovery_policy(config):
    value = config['devspace_execution'].get('observer_recovery', {})
    result = {'max_restarts': 3, 'restart_delay': 2, 'read_retries': 3, 'retry_delay': 2}
    if not isinstance(value, dict) or set(value) - set(result):
        raise ValueError('observer_recovery_policy_required')
    result.update(value)
    for k, v in result.items():
        if type(v) is not int or not 1 <= v <= (10 if k.endswith('restarts') or k.endswith('retries') else 30):
            raise ValueError('bounded_observer_recovery_policy_required')
    return result



def absolute(value):
    p = Path(value)
    if not p.is_absolute() or p.resolve() != p:
        raise ValueError('absolute_unlinked_execution_path_required')
    return p


def plan_digest(plan):
    return digest(plan)


def pinned_inputs(plan):
    inputs = []
    for row in plan.get('inputs', []):
        path = absolute(row['path'])
        if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('bounded_pinned_input_required')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise ValueError('pinned_input_changed')
        inputs.append({**row, 'data': base64.b64encode(raw).decode()})
    return inputs


def validate(config, require_authorization=True):
    if config.get('research_worker'):
        from research_worker import settings as worker_settings
        worker_settings(config)
    cfg = config['devspace_execution']
    plan = cfg['plan']
    if plan.get('artifact_collection_mode') not in (None,'scp-sha256-v1'):
        raise ValueError('unknown_artifact_collection_mode')
    if plan.get('artifact_collection_mode')=='scp-sha256-v1' and plan.get('transport')!='ssh':
        raise ValueError('streamed_artifacts_require_ssh')
    from devspace_health import validate_health
    validate_health(plan.get('health_policy'))
    if cfg.get('version') != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', plan.get('run_id', '')):
        raise ValueError('devspace_execution_v1_required')
    binding = plan_digest(plan)
    auth = cfg.get('authorization', {})
    if require_authorization and (auth.get('plan_digest') != binding or auth.get('confirmed') is not True or not auth.get('source_ref')):
        raise ValueError('exact_devspace_plan_authorization_required')
    unlimited = plan.get('limit_policy') == 'user-authorized-unbounded'
    if unlimited and (not plan.get('limit_authorization_ref') or any(plan.get(k) is not None for k in ('deadline', 'max_seconds', 'budget'))):
        raise ValueError('explicit_unbounded_limits_and_authorization_required')
    for key in (('hourly_rate',) if unlimited else ('deadline', 'max_seconds', 'hourly_rate', 'budget')):
        v = plan.get(key)
        zero_cost_cpu = key == 'hourly_rate' and plan.get('transport') == 'local-cpu' and v == 0
        if type(v) not in (int, float) or not math.isfinite(v) or (v <= 0 and not zero_cost_cpu):
            raise ValueError('positive_finite_' + key + '_required')
    if not unlimited and plan['max_seconds'] / 3600 * plan['hourly_rate'] > plan['budget']:
        raise ValueError('duration_exceeds_authorized_budget')
    if plan.get('transport') not in {'local-cpu', 'ssh'}:
        raise ValueError('supported_devspace_transport_required')
    kind = 'cpu-simulation' if plan['transport'] == 'local-cpu' else 'live-platform-observation'
    ecfg, _ = service.settings(config)
    resource = plan['resource']
    if ecfg['tasks'] != [{k: resource[k] for k in ('uid', 'namespace')}]:
        raise ValueError('single_exact_devspace_event_binding_required')
    if plan['transport'] == 'ssh' and config['task_event_service']['bindings'][0]['cluster'] != resource.get('cluster'):
        raise ValueError('devspace_cluster_binding_required')
    if config.get('task_event_wakeup', {}).get('enabled') and (
            kind == 'cpu-simulation' or config['task_event_wakeup'].get('resource_kind') != 'devspace'):
        raise ValueError('live_devspace_wakeup_binding_required')
    if resource.get('route') not in {'native-ui', 'official-cli', 'local-cpu'}:
        raise ValueError('explicit_resource_route_required')
    if plan['transport'] == 'local-cpu' and resource['route'] != 'local-cpu':
        raise ValueError('simulation_resource_route_required')
    if plan['transport'] == 'ssh':
        if resource['route'] == 'local-cpu' or plan.get('supervised_cleanup') is not True:
            raise ValueError('supervised_cleanup_required_no_independent_allocation_stop_guarantee')
        for key in ('name', 'cluster', 'namespace'):
            if not re.fullmatch(r'[A-Za-z0-9_-]+', resource.get(key, '')):
                raise ValueError('valid_resource_' + key + '_required')
        cli = absolute(resource['cli_path'])
        if cli.name.lower() not in {'lpai-agent', 'lpai-agent.exe'} or not cli.is_file():
            raise ValueError('official_lpai_cli_required')
        ssh = plan['ssh']
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+', ssh['host']) or not re.fullmatch(r'[a-zA-Z0-9_-]+', ssh['user']):
            raise ValueError('ssh_endpoint_required')
        if type(ssh['port']) is not int or not 1 <= ssh['port'] <= 65535:
            raise ValueError('ssh_port_required')
        absolute(ssh['key'])
        if 'executable' in ssh:
            binary=absolute(ssh['executable'])
            if not binary.is_file() or binary.name.lower() not in {'ssh','ssh.exe'}:
                raise ValueError('native_openssh_executable_required')
        if not plan.get('route_evidence'):
            raise ValueError('official_capability_route_evidence_required')
    remote = plan['remote_root']
    if plan['transport'] == 'local-cpu':
        absolute(remote)
    elif not re.fullmatch(r'/[A-Za-z0-9_./-]+', remote) or '..' in PurePosixPath(remote).parts or remote == '/':
        raise ValueError('safe_absolute_remote_root_required')
    argv = plan['argv']
    if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or not a or '\0' in a for a in argv):
        raise ValueError('explicit_workload_argv_required')
    from devspace_worker import relative
    names = set()
    for row in plan.get('inputs', []):
        name = row['name']; relative(name)
        if name in names or name.split('/')[0] in {'health', 'health-sample.json', 'claim', 'worker.py', 'worker.log', 'capsule.json', 'process.json', 'launch-intent.json', 'cancel', 'stdout.log', 'progress.json', 'failure.json', 'completion-event.json'}:
            raise ValueError('unique_nonreserved_input_required')
        names.add(name)
        path = absolute(row['path'])
        if not re.fullmatch('[0-9a-f]{64}', row['sha256']): raise ValueError('pinned_input_digest_required')
    if len(names) > 64:
        raise ValueError('bounded_input_list_required')
    artifacts = plan.get('artifacts', [])
    if not isinstance(artifacts, list) or len(artifacts) > 64:
        raise ValueError('bounded_artifacts_required')
    for name in artifacts: relative(name)
    for key, default in [('max_artifact_bytes', 8 * 1024 * 1024), ('max_total_bytes', 32 * 1024 * 1024)]:
        value = plan.get(key, default)
        if type(value) is not int or not 1 <= value <= 1024 * 1024 * 1024:
            raise ValueError('bounded_' + key + '_required')
    transfer_timeout = plan.get('artifact_transfer_timeout_seconds', 45)
    if type(transfer_timeout) is not int or not 1 <= transfer_timeout <= 600:
        raise ValueError('bounded_artifact_transfer_timeout_required')
    if plan.get('preflight') is not None:
        probe=plan['preflight']
        if (not isinstance(probe,dict) or set(probe)!={'input_name','parameters','timeout_seconds'}
            or probe['input_name'] not in {r['name'] for r in plan['inputs']}
            or not isinstance(probe['parameters'],dict)
            or type(probe['timeout_seconds']) is not int or not 1<=probe['timeout_seconds']<=300):
            raise ValueError('pinned_bounded_preflight_required')
    absolute(cfg['submission_registry'])
    return cfg, plan, binding, kind


def inspect(config,output=None):
    cfg, plan, binding, kind = validate(config, require_authorization=False)
    if config.get('devspace_main_workflow'):
        from devspace_main_workflow import validate_research_spec
        validate_research_spec(config['devspace_main_workflow'])
    pinned_inputs(plan)
    from native_lifecycle import capabilities,prepare
    authorization=cfg.get('authorization',{})
    authorization_matches=authorization.get('confirmed') is True and authorization.get('plan_digest')==binding
    pending={}
    if output is not None:
        for action in ('start','stop'):
            if (Path(output)/(action+'-request.json')).is_file():pending[action]=prepare(plan,binding,output,action)
    return {'status':'prepared','plan_digest':binding,'evidence_kind':kind,'resource':plan['resource'],
            'runtime_capabilities':capabilities(plan),'native_action_requests':pending,
            'authorization_record_matches_plan':authorization_matches,
            'max_seconds':plan['max_seconds'],'deadline':plan['deadline'],'budget':plan['budget'],
            'linear_cost_estimate':None if plan.get('max_seconds') is None else plan['max_seconds']/3600*plan['hourly_rate'],
            'platform_queried':False,'execution_authorized':False,
            'cleanup':('No time/spend hard cap by explicit authorization; terminal cleanup and supervised allocation stop/readback still required.'
                       if plan.get('limit_policy') == 'user-authorized-unbounded' else
                       'Local worker enforces workload deadline; platform allocation requires supervised stop and readback.'),
            'next_action':('Use the existing bound authorization and original workspace; inspection grants or performs no action.'
                           if authorization_matches else
                           'Reconcile missing or changed resource/command/budget/route authorization; preserve original request identity.')}


class OfficialReadUnavailable(OSError):
    """Only a failed official GET transport; never an ambiguous write or identity fault."""


def resource_call(plan, action):
    r = plan['resource']
    try:
        result = subprocess.run([r['cli_path'], 'devspaces', action, '--name', r['name'],
                             '--namespace', r['namespace'], '--cluster', r['cluster']],
                            capture_output=True, text=True, encoding='utf-8', timeout=40,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        if action == 'get': raise OfficialReadUnavailable('official_read_transport_timeout') from None
        raise
    if action == 'get' and result.returncode and any(word in result.stderr.lower() for word in (
            'connection refused', 'actively refused', 'proxyconnect', 'i/o timeout', 'deadline exceeded',
            'no such host', 'connection reset', 'network is unreachable')):
        if not any(word in result.stderr.lower() for word in ('unauthorized', 'forbidden', 'certificate', 'authentication')):
            raise OfficialReadUnavailable('official_read_transport_unavailable')
    value = json.loads(result.stdout)
    if result.returncode or value.get('status') != 'ok':
        # No claim of a Feishu delivery or native approval from gateway metadata.
        raise ValueError('official_operation_unconfirmed:' + str(value.get('status')))
    if action != 'get':
        return value
    obj = value['data']
    if obj['metadata']['uid'] != r['uid'] or obj['metadata']['name'] != r['name']:
        raise ValueError('resource_identity_mismatch')
    if plan['transport'] == 'ssh' and obj['status']['phase'] == 'ready':
        s = plan['ssh']
        expected = f"ssh -p {s['port']} {s['user']}@{s['host']} -t bash"
        if expected not in [a.get('address') for a in obj['spec'].get('accesses', []) if a.get('name') == 'ssh']:
            raise ValueError('official_ssh_endpoint_changed_rebind_plan')
    return {'uid': r['uid'], 'phase': obj['status']['phase'], 'observed_at': time.time()}


def transport(plan, packet):
    worker = Path(__file__).with_name('devspace_worker.py')
    # Ship the installed stdlib health implementation with the capsule, rather
    # than depending on project-specific scripts or remote Python packages.
    health = worker.with_name('devspace_health.py').read_text(encoding='utf-8')
    code = health + '\n' + worker.read_text(encoding='utf-8').replace('from devspace_health import HealthGuard, validate_health', '')
    packet = {**packet, 'root': plan['remote_root']}
    if packet['action'] == 'launch':
        packet['worker'] = code
    if plan['transport'] == 'local-cpu':
        argv = [sys.executable, '-B', str(worker)]
    else:
        s = plan['ssh']
        # Keep installed source off argv: Windows CreateProcess limits the full
        # quoted command line to 32767 characters. The pinned SSH channel carries
        # code and data on stdin; reports/logs never become executable source.
        packet['_sdm_worker_source']=code
        bootstrap=("import json,sys;p=json.load(sys.stdin);c=p.pop('_sdm_worker_source');"
                   "n={'__name__':'sdm_transport'};exec(compile(c,'<sdm-worker>','exec'),n);"
                   "print(json.dumps(n['request'](p)))")
        # Fixed, installed worker code; log/report text never becomes a command.
        argv = [s.get('executable','ssh'), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
                '-o', 'IdentitiesOnly=yes', '-i', s['key'], '-p', str(s['port']), s['user'] + '@' + s['host'],
                'python3 -B -c ' + shlex.quote(bootstrap)]
    result = subprocess.run(argv, input=json.dumps(packet), capture_output=True, text=True,
                            encoding='utf-8', timeout=(plan.get('artifact_transfer_timeout_seconds', 45)
                                if packet['action'] == 'collect' else
                                packet['timeout_seconds']+15 if packet['action']=='preflight' else 45),
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        if plan['transport'] == 'ssh' and packet['action'] in {'observe', 'collect'} and not any(
                v in result.stderr.lower() for v in ('traceback', 'permission denied', 'host key', 'verification failed')):
            raise OSError('capsule_read_unavailable')
        raise ValueError('capsule_transport_failed')
    value=json.loads(result.stdout)
    if not isinstance(value,dict):raise ValueError('capsule_response_object_required')
    return value


def notification_owner(config, owner=None):
    if owner is None:
        return config
    if any(config.get(k) != owner.get(k) for k in ('task_event_collector','task_event_service','task_event_wakeup','research_worker')):
        raise ValueError('study_notification_owner_binding_mismatch')
    from devspace_study import validate as validate_study
    validate_study(owner)
    return owner


def event_binding(config):
    fields = ['task_event_collector','task_event_service','task_event_wakeup']
    if 'research_worker' in config:
        fields.append('research_worker')
    return digest({k: config.get(k) for k in fields})


def emit(config, root, state, kind, details, token='', notification_config=None):
    notify = notification_owner(config, notification_config)
    key = state['binding'] + ':' + str(token)
    eid = hashlib.sha256((state['uid'] + '|' + kind + '|' + key).encode()).hexdigest()
    db = service.connect(collector.validate(config))
    try:
        if db.execute('SELECT 1 FROM notification_outbox WHERE id=?', (eid,)).fetchone():
            with db:
                db.execute('INSERT OR REPLACE INTO service_health VALUES (1,?,?,?)', (os.getpid(), time.time(), 'healthy'))
            if notify.get('task_event_wakeup', {}).get('enabled'): wakeup.dispatch(notify, db)
            return
    finally: db.close()
    report = {'schema': 'sdm-devspace-observation/v1', 'evidence_kind': state['evidence_kind'],
              'binding': state['binding'], 'uid': state['uid'], 'observed_at': time.time(),
              'kind': kind, 'details': details, 'billing_release_verified': False,
              'experiment_success_verified': False, 'retry_authorized': False}
    path = service.save_report(root / 'events', report)
    db = service.connect(collector.validate(config))
    try:
        with db:
            service.enqueue(db, state['uid'], kind, '开发空间事件：' + kind + '；请读取绑定证据。', path,
                            key)
            db.execute('INSERT OR REPLACE INTO service_health VALUES (1,?,?,?)', (os.getpid(), time.time(), 'healthy'))
        if notify.get('task_event_wakeup', {}).get('enabled'):
            wakeup.dispatch(notify, db)
    finally:
        db.close()


def controlled_result(plan, observation):
    if plan.get('health_policy') and (observation.get('health') or {}).get('evidence_complete') is not True:
        return {'outcome':'failed', 'expected_failure_verified':False}
    expected = plan.get('expected_failure')
    if expected is None:
        return {'outcome': 'succeeded' if observation.get('exit_code') == 0 and not observation.get('termination_reason') else 'failed',
                'expected_failure_verified': False}
    failure = observation.get('failure') or {}
    matched = (set(expected) == {'exit_code', 'exception_type', 'marker', 'step'}
               and type(expected['exit_code']) is int and expected['exit_code'] != 0
               and type(expected['step']) is int and expected['step'] >= 0
               and all(isinstance(expected[k], str) and expected[k] for k in ('exception_type', 'marker'))
               and observation.get('exit_code') == expected['exit_code']
               and not observation.get('termination_reason')
               and all(failure.get(k) == expected[k] for k in ('exception_type', 'marker', 'step')))
    return {'outcome': 'expected-failure' if matched else 'failed', 'expected_failure_verified': bool(matched)}


def owns_allocation(state):
    """Existing ownership/unknown-start evidence; a configured UID alone is insufficient."""
    return bool(state.get('ready_observed') or 'start' in state.get('intents',[]) or state.get('launch_intent'))


def project_execution(config,output=None):
    """Honor the declared project workflow at every public execution/watch entry."""
    if config.get('devspace_study'):
        from devspace_study import execution as advance
    elif config.get('devspace_main_workflow'):
        if output is not None:
            root=Path(output)
            # Pre-fix public single-round calls used the standalone executor.
            # Existing resource state without a main journal stays on that route;
            # the fix must not reinterpret an owned/frozen attempt midflight.
            legacy_single = ((root/'execution.json').exists() and not (root/'main/events.jsonl').exists()
                             and not (root/'main/preparation-failure.json').exists())
        else:legacy_single = False
        if legacy_single:advance = execution
        else:
            from devspace_main_workflow import execution as advance
    else:advance=execution
    if config.get('research_worker'):
        original = advance
        def checked(config, root, *args, **kwargs):
            from research_worker import advance_checked
            return advance_checked(config, root, original, *args, **kwargs)
        return checked
    return advance


def execution(config, output, action_receipt=None, allow_live=False, notification_config=None,
              acquire_only=False, force_cleanup=False):
    cfg, plan, binding, kind = validate(config)
    notification_owner(config, notification_config)
    # CLI paths may be relative. Invalid receipt input must not poison the
    # persisted resource lifecycle or trigger allocation cleanup.
    if action_receipt is not None:
        action_receipt = str(Path(action_receipt).resolve())
        json.loads(Path(action_receipt).read_bytes())
    root = absolute(str(Path(output).absolute()))
    if action_receipt is not None:
        supplied = json.loads(Path(action_receipt).read_bytes())
        action = supplied.get('action')
        if action not in {'start', 'stop'} or not (root / (action + '-request.json')).is_file():
            raise ValueError('existing_native_action_request_required')
        request = json.loads((root / (action + '-request.json')).read_bytes())
        from native_lifecycle import receipt as validate_receipt
        validate_receipt(supplied,request,binding,plan['resource']['uid'],action,plan['resource']['route'])
    root.mkdir(parents=True, exist_ok=True)
    if kind != 'cpu-simulation' and not allow_live:
        raise ValueError('explicit_allow_devspace_live_required')
    with collector.collector_lock(str(root / 'execution')):
        statepath = root / 'execution.json'
        if not statepath.exists() and action_receipt is None and not force_cleanup:
            from required_skills import inspect as inspect_skills
            inspect_skills(config)
            from proposal_review import gate
            readiness=gate(config,root)
            if readiness and readiness['status']!='proposal-approved':return readiness
        if not statepath.exists(): pinned_inputs(plan)  # before any resource query/write
        state = json.loads(statepath.read_bytes()) if statepath.exists() else {
            'schema': 'sdm-devspace-execution/v1', 'binding': binding, 'uid': plan['resource']['uid'],
            'evidence_kind': kind, 'status': 'new', 'created_at': time.time(),
            'deadline': None if plan.get('limit_policy') == 'user-authorized-unbounded' else min(plan['deadline'], time.time() + plan['max_seconds']), 'intents': [],
            'process_terminal': False, 'stop_verified': False, 'billing_release_verified': False,
            'event_binding': event_binding(config)}
        if state['binding'] != binding:
            raise ValueError('execution_plan_changed')
        if state.get('event_binding') != event_binding(config):
            raise ValueError('execution_event_binding_changed')
        # A shared logical request must not move to another local output/attempt.
        guard = Guard({'provider': 'sdm-devspace-execution', 'namespace': plan['resource']['namespace'],
                       'owner': cfg['owner'], 'request_key': plan['run_id'], 'component_uid': state['uid'],
                       'configuration_digest': binding, 'submission_registry': cfg['submission_registry']}, statepath)
        with guard.lock():
            guard.load()
            if guard.state['phase'] == 'new':
                guard.before_submit(statepath)
            elif guard.state.get('state_path') != str(statepath.resolve()):
                raise ValueError('existing_execution_resume_original_workspace')
        if not statepath.exists(): atomic_json(statepath, state)
        db = collector.connect(collector.validate(config), kind); db.close()

        def save():
            if state.get('resource_reconciliation'):
                value = {**state['resource_reconciliation'], 'schema':'sdm-devspace-resource-reconciliation/v1',
                         'binding':binding, 'uid':state['uid'], 'stop_requested':(root / 'stop-request.json').exists(),
                         'stop_action_recorded':'stop' in state['intents'], 'stop_verified':state['stop_verified'],
                         'process_terminal':state['process_terminal'], 'artifacts_collected':state.get('artifacts_collected', False),
                         'billing_release_verified':False}
                atomic_json(root / 'resource-reconciliation.json', value)
            atomic_json(statepath, state)
        def event(name, value, token=''): emit(config, root, state, name, value, token, notification_config)
        def observation():
            if kind == 'cpu-simulation':
                return {'uid': state['uid'], 'phase': 'stopped' if state['stop_verified'] else 'ready', 'observed_at': time.time()}
            try:
                result = resource_call(plan, 'get')
            except OfficialReadUnavailable:
                state['resource_read_failures'] = state.get('resource_read_failures', 0) + 1
                state.setdefault('resource_read_unavailable_since', time.time())
                state['resource_reconciliation'] = {'phase':'unknown', 'read_verified':False,
                    'failed_at':time.time(), 'failure_count':state['resource_read_failures']}
                save()
                raise
            state['resource_reconciliation'] = {**result, 'read_verified':True}
            if state.get('resource_read_unavailable_since') is not None:
                recovered = {'failures':state['resource_read_failures'],
                    'unavailable_since':state['resource_read_unavailable_since'],
                    'recovered_at':time.time(), 'resource':result}
                state.setdefault('resource_read_recovery_history', []).append(recovered)
                state.pop('resource_read_unavailable_since')
                state.update(retryable_collection=False, consecutive_collection_failures=0,
                             observer_retry_exhausted=False, collection_failed=False)
                save(); event('resource_read_recovered', recovered, len(state['resource_read_recovery_history']))
            return result
        def lifecycle(action):
            request = {'schema': 'sdm-devspace-action/v1', 'binding': binding, 'uid': state['uid'],
                       'action': action, 'route': plan['resource']['route'], 'requested_at': time.time()}
            path = root / (action + '-request.json')
            from native_lifecycle import request as validate_request,receipt as validate_receipt
            if path.exists():request=json.loads(path.read_bytes())
            validate_request(request,binding,state['uid'],action,plan['resource']['route'])
            if not path.exists():atomic_json(path,request)
            if action_receipt is not None:
                receipt_path = absolute(str(action_receipt))
                receipt = json.loads(receipt_path.read_bytes())
                validate_receipt(receipt,request,binding,state['uid'],action,plan['resource']['route'])
                atomic_json(root / (action + '-receipt.json'), receipt)
                if action not in state['intents']: state['intents'].append(action); save()
            if plan['resource']['route'] == 'official-cli' and action not in state['intents']:
                state['intents'].append(action); save()  # before external write, never repeat on ambiguous outcome
                resource_call(plan, action)
            state['status'] = 'awaiting-' + action + '-readback'
            save()
            event('resource_action_required', request, action)
            return request

        rules = recovery_policy(config)  # validate before resource access
        worker = config.get('research_worker')
        if worker and not state.get('launch_intent'):
            steering = Path(worker['output']) / 'research-control.json'
            if steering.is_file() and json.loads(steering.read_bytes()).get('paused') is True:
                if owns_allocation(state):
                    state['cleanup_required'] = True
                else:
                    state['status'] = 'research-paused'; save(); return state
        try:
            if force_cleanup:
                if not owns_allocation(state):
                    state['status']='ownership-reconciliation-required';save();return state
                state['cleanup_required'] = True; save()
            # Cleanup is reconciled separately from workload result. A stopped space
            # does not make a missing process result or missing artifact successful.
            if state['stop_verified']:
                if state.get('artifacts_collected') and state['process_terminal']:
                    return review(config, root, state, notification_config)
                return state
            if action_receipt is not None:
                supplied = json.loads(absolute(str(action_receipt)).read_bytes())
                action = supplied.get('action')
                if action not in {'start', 'stop'} or not (root / (action + '-request.json')).exists():
                    raise ValueError('existing_native_action_request_required')
                lifecycle(action)
                action_receipt = None
                # Recover the historical relative-path validation failure only
                # after a valid, bound start receipt; never clear real failures.
                if (action == 'start' and not state.get('launch_intent')
                    and not (root / 'stop-request.json').exists()
                    and state.get('last_error') in {'absolute_unlinked_execution_path_required','persisted_project_binding_mismatch'}
                    and state.get('original_failure', {}).get('error') == state.get('last_error')):
                    state['cleanup_required'] = False
                    state['input_error_reconciled'] = True
                    save()
            expired = state['deadline'] is not None and time.time() >= state['deadline']
            res = observation(); atomic_json(root / 'resource-observation.json', res)
            if expired and not state.get('launch_intent') and 'start' not in state['intents'] and not (root / 'start-request.json').exists():
                state.update(status='expired-before-start', stop_verified=res['phase'] == 'stopped' or kind == 'cpu-simulation')
                save(); return state
            cleanup = state.get('cleanup_required') or expired
            if cleanup:
                if state.get('launch_intent') and not state['process_terminal'] and res['phase'] == 'ready':
                    try:
                        transport(plan, {'action': 'cancel', 'binding': binding})
                        snap = transport(plan, {'action': 'observe', 'binding': binding})
                        state['process'] = snap
                        state['process_terminal'] = snap['phase'] in {'exited', 'worker_failed'}
                        if state['process_terminal']:
                            state.update(controlled_result(plan, snap))
                            event('process_terminal', {**snap, 'result': state['outcome']})
                        save()
                        if not state['process_terminal'] and kind == 'cpu-simulation':
                            state['status'] = 'awaiting-process-stop'; save(); return state
                    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
                        state['process_stop_error'] = str(error); save()
                        event('cleanup_failed', {'process_stop_error': str(error)}, 'process')
                        # Allocation stop is still attempted when SSH is unavailable.
                # Retry only transient reads, preserving the counter across observer restarts.
                # Exhaustion/identity faults still release the allocation and retain missing evidence.
                if (state.get('launch_intent') and not state.get('artifacts_collected')
                    and not state.get('artifact_retry_exhausted') and res['phase'] == 'ready'):
                    while True:
                        try:
                            collect(config, plan, root, state, notification_config)
                            if state.get('artifact_read_failures'):
                                event('artifact_collection_recovered', {'failures': state['artifact_read_failures']})
                            state['artifact_retry_pending'] = False
                            save(); break
                        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
                            state['artifact_error'] = str(error)
                            state['artifact_read_failures'] = state.get('artifact_read_failures', 0) + 1
                            failures = state['artifact_read_failures']
                            retry = (isinstance(error, (OSError, subprocess.TimeoutExpired))
                                     and failures <= rules['read_retries']
                                     and (state['deadline'] is None or time.time() < state['deadline']))
                            state.update(artifact_retry_pending=retry, artifact_retry_exhausted=not retry)
                            save()
                            if not retry:
                                event('artifact_collection_failed', {'error': str(error), 'failures': failures,
                                      'automatic_resubmit': False})
                                break
                            event('artifact_collection_unavailable', {'error': str(error), 'automatic_resubmit': False})
                            delay = min(30, rules['retry_delay'] * failures)
                            if state['deadline'] is not None:
                                delay = min(delay, max(0, state['deadline'] - time.time()))
                            time.sleep(delay)
                if kind == 'cpu-simulation':
                    state['stop_verified'] = state['process_terminal'] or not state.get('launch_intent')
                elif res['phase'] == 'stopped':
                    state['stop_verified'] = True
                else:
                    lifecycle('stop')
                    res = observation(); atomic_json(root / 'resource-observation.json', res)
                    state['stop_verified'] = res['phase'] == 'stopped'
                state['cleanup_required'] = not state['stop_verified']
                if plan['resource']['route'] == 'official-cli' and 'start' in state['intents'] and not state.get('ready_observed'):
                    state.update(status='awaiting-start-intent-reconciliation', cleanup_required=True,
                                 stop_verified=False, last_observed_resource_phase=res['phase'])
                    save(); event('cleanup_failed', {'reason':'Unresolved start may take effect later; stopped readback alone is insufficient.'}, 'unresolved-start')
                    return state
                state['status'] = ('ready-for-review' if state.get('artifacts_collected') and state['process_terminal']
                                   else 'released-evidence-incomplete') if state['stop_verified'] else 'awaiting-stop-readback'
                save()
                pending_native = plan['resource']['route'] == 'native-ui' and 'stop' not in state['intents']
                event('resource_stopped' if state['stop_verified'] else ('cleanup_pending' if pending_native else 'cleanup_failed'),
                      {'resource': res, 'process_terminal': state['process_terminal'], 'artifact_error': state.get('artifact_error')})
                if state['stop_verified'] and state.get('artifacts_collected') and state['process_terminal']:
                    return review(config, root, state, notification_config)
                return state
            if res['phase'] != 'ready':
                if state.get('launch_intent'):
                    state.update(cleanup_required=True, status='cleanup-required'); save()
                    event('resource_lost', res)
                    return state
                if res['phase'] == 'stopped':
                    lifecycle('start')
                elif (root / 'start-request.json').exists():
                    state['status']='awaiting-start-readback';save()
                else:
                    raise ValueError('resource_not_stopped_or_owned_do_not_start')
                return state
            if kind != 'cpu-simulation' and not (root / 'start-request.json').exists() and plan.get('adopt_existing_ready') is not True:
                raise ValueError('existing_ready_space_requires_explicit_adoption')
            if plan['resource']['route'] == 'native-ui' and (root / 'start-request.json').exists() and not (root / 'start-receipt.json').exists():
                state['status'] = 'awaiting-start-action-receipt'; save(); return state
            state['ready_observed'] = True; save()
            event('resource_ready', res)
            if acquire_only and not state.get('launch_intent'):
                state['status'] = 'resource-ready'; save(); return state
            if not state.get('launch_intent'):
                inputs = pinned_inputs(plan)
                state['launch_intent'] = True; state['status'] = 'launching'; save()
                packet = {'action': 'launch', 'binding': binding, 'plan': {'binding': binding, 'argv': plan['argv'], 'deadline': state['deadline']},
                          'inputs': inputs}
                if plan.get('health_policy'): packet['plan']['health_policy'] = plan['health_policy']
                transport(plan, packet)
            snap = transport(plan, {'action': 'observe', 'binding': binding})
            if snap.get('binding') not in (None, binding): raise ValueError('remote_attempt_identity_changed')
            state['process'] = snap
            state['retryable_collection'] = False
            state['consecutive_collection_failures'] = 0
            state['observer_retry_exhausted'] = False
            if snap.get('started_at'): event('execution_started', snap)
            progress = snap.get('progress') or {}
            if type(progress.get('optimizer_updates')) is int and progress['optimizer_updates'] > 0:
                event('first_optimizer_update', progress)
            state['process_terminal'] = snap['phase'] in {'exited', 'worker_failed'}
            if state.get('collection_failed'):
                state['collection_failed'] = False
                event('collection_recovered', snap, state.get('failure_generation', 0))
            if state['process_terminal']:
                state.update(cleanup_required=True, status='cleanup-required', **controlled_result(plan, snap))
                if state['outcome']=='failed':state.setdefault('original_failure',{'kind':'workload','observation':snap})
                save(); event('process_terminal', {**snap, 'result': state['outcome']})
            else:
                state['status'] = 'running' if snap['phase'] == 'running' else 'launch-outcome-unknown'
                save()
            return state
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
            if isinstance(error, OfficialReadUnavailable) and state.get('launch_intent'):
                # A failed platform read is not a failed workload. The already
                # bound SSH endpoint can still supply same-attempt read-only proof.
                try:
                    if not (state.get('process_terminal') and state.get('artifacts_collected')):
                        snap = transport(plan, {'action':'observe', 'binding':binding})
                        if snap.get('binding') != binding: raise ValueError('remote_attempt_identity_changed')
                        state['process'] = snap
                        state['process_terminal'] = snap['phase'] in {'exited','worker_failed'}
                    if state['process_terminal']:
                        state.update(cleanup_required=True, **controlled_result(plan, state['process']))
                        event('process_terminal', {**state['process'], 'result':state['outcome']})
                        if not state.get('artifacts_collected'):
                            collect(config, plan, root, state, notification_config)
                except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as evidence_error:
                    state['outage_evidence_error'] = str(evidence_error)
                if state.get('cleanup_required'):
                    try: lifecycle('stop')  # existing authorization; intents still prevent write retries
                    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as stop_error:
                        state['outage_stop_error'] = str(stop_error)
                state['stop_verified'] = False  # neither native action nor cached phase proves readback
            if not state.get('collection_failed'): state['failure_generation'] = state.get('failure_generation', 0) + 1
            state.update(collection_failed=True, last_error=str(error), status='awaiting-reconciliation')
            state['retryable_collection'] = bool(isinstance(error, OfficialReadUnavailable) or
                (state.get('launch_intent') and not state.get('cleanup_required')
                 and isinstance(error, (OSError, subprocess.TimeoutExpired))))
            if state['retryable_collection']:
                state['consecutive_collection_failures'] = state.get('consecutive_collection_failures', 0) + 1
            state.setdefault('original_failure',{'kind':'execution_or_collection','error':str(error),'at':time.time()})
            if (state['deadline'] is not None and time.time() >= state['deadline']) or (not state.get('launch_intent') and (state.get('ready_observed') or (root / 'start-request.json').exists())): state['cleanup_required'] = True
            save(); event('collection_unavailable', {'error': str(error), 'automatic_resubmit': False}, state['failure_generation'])
            if state.get('cleanup_required'):event('cleanup_failed',{'error':str(error),'stop_verified':state['stop_verified']},'operation')
            return state


def collect(config, plan, root, state, notification_config=None):
    streaming=plan.get('artifact_collection_mode')=='scp-sha256-v1'
    names = sorted(set(plan.get('artifacts', []) + ['process.json', 'progress.json', 'failure.json', 'stdout.log', 'worker.log']))
    if plan.get('health_policy'):
        from devspace_health import HEALTH_FILES
        names = sorted(set(names + HEALTH_FILES))
    if streaming:
        from devspace_artifacts import collect as streamed_collect
        transfer=streamed_collect(plan,root,transport,names)
        staged=transfer['entries']
        state['artifact_transfer_failures']=transfer['failures']
        files={row['name']:None for row in staged}
    else:
        files = transport(plan, {'action': 'collect', 'binding': state['binding'], 'artifacts': names,
                            'max_artifact_bytes': plan.get('max_artifact_bytes', 8 * 1024 * 1024),
                            'max_total_bytes': plan.get('max_total_bytes', 32 * 1024 * 1024)})
    if not isinstance(files, dict) or not set(files) <= set(names): raise ValueError('unexpected_artifact')
    artifacts = root / 'artifacts'; artifacts.mkdir(exist_ok=True)
    entries = []; total = 0
    for name, encoded in (() if streaming else files.items()):
        data = base64.b64decode(encoded, validate=True); total += len(data)
        if len(data) > plan.get('max_artifact_bytes', 8 * 1024 * 1024) or total > plan.get('max_total_bytes', 32 * 1024 * 1024):
            raise ValueError('download_budget_exceeded')
        target = artifacts / name
        if target.resolve() != target or not target.is_relative_to(artifacts): raise ValueError('artifact_path_escape')
        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        entries.append({'path': str(target), 'bytes': len(data), 'digest': 'sha256:' + hashlib.sha256(data).hexdigest()})
    if streaming:entries=[{k:v for k,v in row.items() if k!='name'} for row in staged]
    missing = sorted(set(plan.get('artifacts', [])) - set(files))
    context = config.get('devspace_round_context', {})
    diagnostic = bool(context.get('failure_diagnostic_result') and state.get('outcome') == 'failed'
        and state.get('process_terminal') and type(state.get('process', {}).get('exit_code')) is int
        and state['process']['exit_code'] != 0)
    state.update(required_artifacts_complete=not missing, missing_artifacts=missing)
    if diagnostic: state['diagnostic_only'] = True
    atomic_json(root / 'artifact-manifest.json', {'binding': state['binding'], 'evidence_kind': state['evidence_kind'],
                'files': entries, 'missing': missing, 'diagnostic_only':diagnostic,
                **({'transfer_failures':transfer['failures']} if streaming else {})})
    if missing and not diagnostic: raise ValueError('required_artifacts_missing:' + ','.join(missing))
    if plan.get('health_policy'):
        from devspace_health import snapshot_predictions
        health = state.get('process', {}).get('health') or {}
        state['health_evidence_collected'] = False
        for key in ('last_healthy','first_anomaly','current_episode'):
            if health.get(key): snapshot_predictions(artifacts, health[key], state['binding'])
        state['health_evidence_collected'] = health.get('evidence_complete') is True
    state['artifacts_collected'] = True
    emit(config, root, state, 'artifacts_collected', {'files': entries, 'missing': missing}, notification_config=notification_config)


def review_material_roots(material, artifacts, execution_root):
    """Register only the explicit material roles, including retained comparisons.

    Path and discovery budgets are still checked by project_workspace. A role
    root is already part of the reviewed plan; never infer parents or scan the
    project directory to find missing inputs.
    """
    from project_workspace import plain
    roots = list(dict.fromkeys((str(plain(artifacts)), str(plain(execution_root)))))
    for role in material.get('roles', []):
        value = role.get('root')
        if not isinstance(value, str) or not value or value.startswith('$'):
            raise ValueError('explicit_review_material_root_required')
        plain(value)
        if value not in roots:
            roots.append(value)
    if len(roots) > 16:
        raise ValueError('bounded_material_root_list_required')
    return roots


def review(config, root, state, notification_config=None):
    """Use the existing raw-material/review/archive engine; never synthesize review."""
    from project_workspace import execute
    plan = config['devspace_execution']['plan']
    manifest = json.loads((root / 'artifact-manifest.json').read_bytes())
    for row in manifest['files']:
        path = absolute(row['path'])
        if not path.is_relative_to(root / 'artifacts') or 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest() != row['digest']:
            raise ValueError('collected_artifact_changed')
    material = plan.get('raw_materials')
    if not material:
        state['status'] = 'awaiting-review-plan'
    else:
        material = json.loads(json.dumps(material))
        for role in material.get('roles', []):
            if role.get('root') == '$artifacts': role['root'] = str(root / 'artifacts')
        # Workload metrics may have been written before a later failure. Always
        # bind the executor's outcome alongside the project observations.
        if any(r.get('id') == 'sdm_execution' for r in material.get('roles', [])):
            raise ValueError('reserved_execution_material_role')
        execution_root = root / 'execution-evidence'
        study_context = config.get('devspace_round_context')
        diagnostic_fallback = False
        if state.get('diagnostic_only'):
            # A failed workload need not have produced a final metric/result.
            # Keep raw originals, review executor/log evidence without inventing
            # successful workload observations or weakening successful rounds.
            try:
                from loop_contract import field
                report = json.loads((root / 'artifacts' / study_context['failure_diagnostic_result']).read_bytes())
                metrics = [field(report, name) for name in json.loads(study_context['failure_diagnostic_metrics'])]
                diagnostic_fallback = any(type(v) not in (int,float) or not math.isfinite(v) for v in metrics)
            except (OSError, ValueError, KeyError, TypeError):
                diagnostic_fallback = True
            if diagnostic_fallback: material['roles'] = []
        if study_context is not None:
            if not isinstance(study_context, dict) or any(type(v) not in (str, int) for v in study_context.values()):
                raise ValueError('scalar_study_review_context_required')
            atomic_json(execution_root / 'study-context.json', study_context)
            material.setdefault('roles', []).append({'id':'study_context', 'root':str(execution_root),
                'json_globs':['study-context.json'], 'json_fields':{
                    k:{'field':k, 'type':'int' if type(v) is int else 'str'} for k,v in study_context.items()}})
        execution_root.mkdir(exist_ok=True)
        snap = state.get('process', {})
        context = {'outcome': state.get('outcome', 'unknown'),
                   'expected_failure_verified': state.get('expected_failure_verified', False),
                   'phase': snap.get('phase', 'unknown'), 'stop_verified': state['stop_verified']}
        if state.get('diagnostic_only'):
            context.update(diagnostic_only=True, required_artifacts_complete=state['required_artifacts_complete'],
                missing_artifacts=json.dumps(state.get('missing_artifacts', [])), training_quality_verified=False)
            if diagnostic_fallback and (root / 'artifacts/stdout.log').is_file():
                with (root / 'artifacts/stdout.log').open('rb') as stream:
                    stream.seek(0, 2); stream.seek(max(0, stream.tell()-8000))
                    context['failure_log_tail'] = stream.read().decode('utf-8', errors='replace')
        if state.get('resource_reconciliation'):
            context.update(resource_read_failures=state.get('resource_read_failures', 0),
                resource_read_recovered=bool(state.get('resource_read_recovery_history')),
                resource_reconciliation_phase=state['resource_reconciliation']['phase'],
                billing_release_verified=False)
        for key in ('exit_code', 'error_type', 'error', 'termination_reason'):
            if type(snap.get(key)) in (int, str): context[key] = snap[key]
        if snap.get('health'):
            context['health_accepted_evaluations'] = snap['health']['accepted_evaluations']
            context['health_snapshot_errors'] = json.dumps(snap['health']['errors'])
            context['health_evidence_complete'] = snap['health'].get('evidence_complete') is True
        for key, value in (snap.get('failure') or {}).items():
            if key in {'exception_type', 'marker', 'step', 'message'} and type(value) in (int, str):
                context['failure_' + key] = value
        atomic_json(execution_root / 'execution.json', context)
        types = {str: 'str', int: 'int', bool: 'bool'}
        material.setdefault('roles', []).append({'id': 'sdm_execution', 'root': str(execution_root),
            'json_globs': ['execution.json'],
            'json_fields': {k: {'field': k, 'type': types[type(v)]} for k, v in context.items()}})
        cfg = {'schema': 'sdm-project-workspace/v1', 'project_id': plan['run_id'],
               'material_roots': review_material_roots(material, root / 'artifacts', execution_root), 'material_preparation': material,
               'material_discovery_limits': {'max_file_bytes': max(256*1024*1024, plan.get('max_artifact_bytes',8*1024*1024)),
                                            'max_total_bytes':1024*1024*1024},
               'evidence_kind': 'cpu-simulation' if state['evidence_kind'] == 'cpu-simulation' else 'retained-material',
               'require_next_step': True}
        from proposal_review import mode,pro_binding
        cfg['review_mode']=mode(config)
        if pro_binding(config) is not None:cfg['external_review']=pro_binding(config)
        atomic_json(root / 'review-project.json', cfg)
        result = execute(cfg, root / 'review')
        from research_observation import review_context
        observation = review_context(config, root / 'review')
        if observation is not None: result['research_context'] = observation
        state['review'] = result
        state['status'] = 'completed' if result['status'] == 'completed' else result['status']
        review_token = result.get('request_digest') or digest({
            'binding': result.get('binding', {}), 'status': result.get('status'),
            'reason': result.get('reason'), 'error_type': result.get('error_type')})
        emit(config, root, state, 'review_ready' if result['status'] == 'awaiting-review' else 'review_' + result['status'], result, review_token, notification_config)
    if config.get('research_worker', {}).get('version',0) >= 4:
        from research_consumption import stamp
        stamp(config, root, state)
    atomic_json(root / 'execution.json', state)
    return state
