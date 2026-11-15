"""Public CPU process integration and fault injection; no LPAI/Codex calls."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch
import pytest
import devspace_execution as d
import task_event_service as s
from task_pipeline import digest

UID = '00000000-0000-4000-8000-000000000002'
ENTRY = Path(d.__file__).with_name('run_project_pipeline.py')


def test_official_get_transport_failure_is_retryable_but_write_is_not():
    plan={'resource':{'cli_path':'official-cli','name':'owned','namespace':'cpu','cluster':'cpu'}}
    failed=subprocess.CompletedProcess([],1,'','proxyconnect tcp: connection refused')
    with patch.object(d.subprocess,'run',return_value=failed):
        with pytest.raises(d.OfficialReadUnavailable):d.resource_call(plan,'get')
        with pytest.raises(ValueError):d.resource_call(plan,'stop')
    with patch.object(d.subprocess,'run',side_effect=subprocess.TimeoutExpired('cli',40)):
        with pytest.raises(d.OfficialReadUnavailable):d.resource_call(plan,'get')
        with pytest.raises(subprocess.TimeoutExpired):d.resource_call(plan,'stop')
    denied=subprocess.CompletedProcess([],1,'','forbidden: authentication required, connection refused')
    with patch.object(d.subprocess,'run',return_value=denied):
        with pytest.raises(ValueError):d.resource_call(plan,'get')


def test_long_official_read_failure_recovers_same_cpu_process_and_cleanup(tmp_path):
    config,root=native_case(tmp_path);phase=['stopped'];offline=[False];calls=[]
    real=d.transport
    def resource(plan,action):
        calls.append(action);assert action=='get'
        if offline[0]:raise d.OfficialReadUnavailable('official_read_transport_unavailable')
        return {'uid':UID,'phase':phase[0],'observed_at':time.time()}
    def local(plan,packet):return real({**plan,'transport':'local-cpu'},packet)
    with patch.object(d,'resource_call',side_effect=resource),patch.object(d,'transport',side_effect=local):
        d.execution(config,root,allow_live=True);phase[0]='ready'
        running=d.execution(config,root,action_receipt(root,'start'),True)
        original=json.loads((tmp_path/'remote/process.json').read_bytes())
        time.sleep(.8);offline[0]=True
        import devspace_observer as observer
        config['devspace_execution']['observer_recovery']={'read_retries':1,'retry_delay':1}
        exhausted=observer.watch(config,root,allow_live=True)
        assert exhausted['observer_retry_exhausted'] and exhausted['process_terminal'] and exhausted['artifacts_collected']
        assert exhausted['outcome']=='succeeded' and not exhausted['stop_verified']
        assert (root/'stop-request.json').exists()
        # After terminal and artifacts are already durable, an unavailable SDK
        # must not add a pointless SSH timeout against the now-stopped space.
        with patch.object(d,'transport',side_effect=AssertionError('redundant post-stop SSH')):
            pending=d.execution(config,root,action_receipt(root,'stop'),True)
        assert not pending['stop_verified'] and 'stop' in pending['intents']
        reconcile=json.loads((root/'resource-reconciliation.json').read_bytes())
        assert reconcile['phase']=='unknown' and reconcile['stop_action_recorded'] and not reconcile['read_verified']
        offline[0]=False;phase[0]='stopped'
        recovered=d.execution(config,root,allow_live=True)
        assert recovered['status']=='awaiting-review' and recovered['stop_verified'] and recovered['outcome']=='succeeded'
        assert recovered['resource_read_recovery_history'][0]['failures']>=3
        assert not recovered['observer_retry_exhausted'] and not recovered['billing_release_verified']
        reconcile=json.loads((root/'resource-reconciliation.json').read_bytes())
        assert reconcile['phase']=='stopped' and reconcile['read_verified'] and reconcile['stop_verified']
        assert recovered['process']['started_at']==original['started_at']
        assert calls and set(calls)=={'get'}


def setup_case(tmp_path, code=None, duration=30):
    source = tmp_path / 'workload.py'
    source.write_text(code or "import json,time\nfrom pathlib import Path\nPath('progress.json').write_text(json.dumps({'optimizer_updates':2}))\ntime.sleep(.4)\nPath('result.json').write_text(json.dumps({'score':.7}))\n")
    plan = {'run_id': 'acceptance', 'transport': 'local-cpu', 'remote_root': str(tmp_path / 'remote'),
            'deadline': time.time() + duration, 'max_seconds': duration, 'hourly_rate': .59, 'budget': 1,
            'resource': {'uid': UID, 'namespace': 'cpu', 'route': 'local-cpu'},
            'argv': [sys.executable, '-B', 'workload.py'],
            'inputs': [{'name': 'workload.py', 'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}],
            'artifacts': ['result.json'], 'raw_materials': {'schema': 'sdm-material-preparation/v1',
                'objective': 'CPU fixture diagnosis, no model claims',
                'roles': [{'id': 'result', 'root': '$artifacts', 'json_globs': ['result.json'],
                           'json_fields': {'score': {'field': 'score', 'type': 'float'}}}]}}
    config = {'devspace_execution': {'version': 1, 'owner': 'cpu-test', 'plan': plan,
              'submission_registry': str(tmp_path / 'registry'),
              'authorization': {'confirmed': True, 'plan_digest': digest(plan), 'source_ref': 'CPU-test-authorization'}},
              'task_event_collector': {'version': 1, 'store': str(tmp_path / 'events.db'), 'tasks': [{'uid': UID, 'namespace': 'cpu'}]},
              'task_event_service': {'version': 1, 'read_only': True, 'evidence_root': str(tmp_path / 'events'),
                                     'bindings': [{'uid': UID, 'namespace': 'cpu', 'cluster': 'cpu'}]}}
    return config, tmp_path / 'output'


def pin(config):
    config['devspace_execution']['authorization']['plan_digest'] = digest(config['devspace_execution']['plan'])


def test_review_failure_notification_distinguishes_reason_without_retry_noise(tmp_path):
    config,root=setup_case(tmp_path);root.mkdir();(root/'artifacts').mkdir()
    (root/'artifact-manifest.json').write_text(json.dumps({'files':[]}))
    state={'uid':UID,'binding':'fixture','evidence_kind':'cpu-simulation',
           'stop_verified':True,'process':{},'outcome':'failed'}
    results=[{'status':'blocked','reason':reason,'error_type':'ValueError'}
             for reason in ('missing citation','missing citation','missing next step')]
    import project_workspace
    with patch.object(project_workspace,'execute',side_effect=results),patch.object(d,'emit') as emit:
        for _ in results:d.review(config,root,state)
    tokens=[call.args[5] for call in emit.call_args_list]
    assert tokens[0]==tokens[1] and tokens[1]!=tokens[2]


def test_explicit_checkpoint_collection_budget_and_timeout(tmp_path):
    config, _ = setup_case(tmp_path)
    plan = config['devspace_execution']['plan']
    plan.update(max_artifact_bytes=512*1024*1024, max_total_bytes=640*1024*1024,
                artifact_transfer_timeout_seconds=300)
    pin(config)
    d.validate(config)
    with patch.object(d.subprocess, 'run', return_value=subprocess.CompletedProcess([],0,'{}','')) as call:
        d.transport(plan, {'action':'collect', 'binding':'fixture', 'artifacts':[]})
        assert call.call_args.kwargs['timeout'] == 300
        d.transport(plan, {'action':'observe', 'binding':'fixture'})
        assert call.call_args.kwargs['timeout'] == 45
    plan['max_total_bytes'] = 1024*1024*1024+1
    pin(config)
    with pytest.raises(ValueError, match='bounded_max_total_bytes'):
        d.validate(config)
    plan['max_total_bytes'] = 640*1024*1024
    plan['artifact_transfer_timeout_seconds'] = 601
    pin(config)
    with pytest.raises(ValueError, match='bounded_artifact_transfer_timeout'):
        d.validate(config)


def public(config, root, *options):
    p = root.parent / 'project.json'; p.write_text(json.dumps(config))
    result = subprocess.run([sys.executable, '-B', str(ENTRY), '--project', str(p),
                             '--output', str(root), '--devspace-execution', *options], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def finish(config, root, public_mode=False):
    until = time.time() + 20
    while time.time() < until:
        result = public(config, root) if public_mode else d.execution(config, root)
        if result['status'] in {'awaiting-review', 'released-evidence-incomplete', 'awaiting-review-plan'}:
            return result
        time.sleep(.1)
    pytest.fail(str(result))


def test_public_process_notifications_review_archive_and_no_repeat(tmp_path):
    config, root = setup_case(tmp_path)
    result = finish(config, root, True)
    assert result['status'] == 'awaiting-review' and result['stop_verified']
    assert result['outcome'] == 'succeeded' and result['evidence_kind'] == 'cpu-simulation'
    kinds = {x['kind'] for x in s.notifications(config)['notifications']}
    assert {'resource_ready', 'execution_started', 'first_optimizer_update', 'process_terminal', 'artifacts_collected', 'resource_stopped', 'review_ready'} <= kinds
    process = json.loads((tmp_path / 'remote/process.json').read_bytes())
    request = json.loads(Path(result['review']['request_path']).read_bytes())
    response = {'request_digest': digest(request), 'evidence_kind': 'cpu-simulation', 'action': 'stop-analysis',
                'facts': [{'evidence_id': 'r1.evaluation.score', 'value': .7}],
                'hypotheses': [{'statement': 'Generated CPU output', 'evidence_ids': ['r1.evaluation.score'], 'limitations': 'No GPU evidence'}],
                'previous_experiment_assessment': 'CPU fixture completed', 'reason': 'Finish local acceptance',
                'next_step': {'action':'End this accepted CPU fixture', 'reason':'Declared objective met',
                              'acceptance':'Checkpoint evidence kept', 'authorization':'none', 'status':'completed',
                              'stop_decision':'stop', 'stop_reason':'Fixture completed', 'evidence_paths':[str(root/'artifacts/result.json')]}}
    Path(result['review']['response_path']).write_text(json.dumps(response))
    done = public(config, root)
    assert done['status'] == 'completed' and not done['review']['execution_allowed']
    assert (root / 'review/latest.json').exists()
    assert done['review']['next_step'] == response['next_step']
    assert done['review']['recommendation'] == response['next_step']['action']
    public(config, root)
    assert json.loads((tmp_path / 'remote/process.json').read_bytes()) == process


def test_archive_only_response_rejected_and_next_step_contract_checked(tmp_path):
    from loop_analysis import validate_next_step
    with pytest.raises(ValueError, match='concrete_next_step'):
        validate_next_step({'reason':'Archived'})
    step={'action':'Investigate failure', 'reason':'Missing checkpoint', 'acceptance':'Original stack and repair regression',
          'authorization':'existing', 'status':'ready', 'stop_decision':'stop', 'stop_reason':'Workload failed'}
    assert validate_next_step({'next_step':step}) == step
    with pytest.raises(ValueError, match='must_be_blocked'):
        validate_next_step({'next_step':{**step,'authorization':'required'}})
    with pytest.raises(ValueError, match='blocker_required'):
        validate_next_step({'next_step':{**step,'status':'blocked'}})
    with pytest.raises(ValueError, match='evidence_required'):
        validate_next_step({'next_step':{**step,'status':'completed','evidence_paths':[str(tmp_path/'missing')]}})


def test_different_command_format_expected_failure_is_exact(tmp_path):
    code = "import json,sys\nfrom pathlib import Path\nPath('failure.json').write_text(json.dumps({'exception_type':'RuntimeError','marker':'fixture-fault','step':6}))\nPath('result.json').write_text(json.dumps({'score':0.0}))\nsys.exit(7)\n"
    config, root = setup_case(tmp_path, code)
    config['devspace_execution']['plan']['expected_failure'] = {'exit_code': 7, 'exception_type': 'RuntimeError', 'marker': 'fixture-fault', 'step': 6}; pin(config)
    result = finish(config, root, True)
    assert result['outcome'] == 'expected-failure'
    request = json.loads(Path(result['review']['request_path']).read_bytes())
    facts = request['evidence']
    assert facts['r2.evaluation.exit_code'] == 7
    assert facts['r2.evaluation.outcome'] == 'expected-failure'
    assert facts['r2.evaluation.expected_failure_verified'] is True
    assert facts['r2.evaluation.failure_marker'] == 'fixture-fault'
    assert request['provenance']['r2.evaluation.exit_code']['path'].endswith('execution.json')
    snap = result['process']; snap['failure']['marker'] = 'unrelated-error'
    assert d.controlled_result(config['devspace_execution']['plan'], snap)['outcome'] == 'failed'


def test_lost_launch_reply_resume_observes_same_process(tmp_path):
    config, root = setup_case(tmp_path)
    real = d.transport; launches = []
    def flaky(plan, packet):
        value = real(plan, packet)
        if packet['action'] == 'launch':
            launches.append(1); raise OSError('SSH reply lost after launch')
        return value
    with patch.object(d, 'transport', side_effect=flaky):
        first = d.execution(config, root)
        assert first['status'] == 'awaiting-reconciliation'
        finish(config, root)
    assert len(launches) == 1
    kinds = [x['kind'] for x in s.notifications(config)['notifications']]
    assert kinds.count('collection_unavailable') == 1 and kinds.count('collection_recovered') == 1
    with pytest.raises(ValueError, match='resume_original'):
        d.execution(config, tmp_path / 'other')


def test_observer_exit_does_not_disable_worker_deadline(tmp_path):
    config, root = setup_case(tmp_path, 'import time\ntime.sleep(60)\n', 8)
    config['devspace_execution']['plan']['artifacts'] = []; pin(config)
    public(config, root)  # this observer process exits; detached worker remains responsible
    deadline = config['devspace_execution']['plan']['deadline']
    time.sleep(max(0, deadline - time.time()) + .6)
    process = json.loads((tmp_path / 'remote/process.json').read_bytes())
    assert process['phase'] == 'exited' and process['termination_reason'] == 'deadline'
    result = d.execution(config, root)
    assert result['stop_verified'] and result['outcome'] == 'failed'


def test_missing_artifact_releases_without_claiming_success(tmp_path):
    config, root = setup_case(tmp_path, 'print("grad_norm: nan")\n')
    result = finish(config, root)
    assert result['stop_verified'] and result['status'] == 'released-evidence-incomplete'
    assert 'required_artifacts_missing' in result['artifact_error']
    assert not any('nan' in x['kind'] for x in s.notifications(config)['notifications'])


def test_cleanup_still_runs_if_ssh_process_cancel_fails(tmp_path):
    config, root = setup_case(tmp_path)
    d.execution(config, root); time.sleep(.6)
    result = d.execution(config, root)
    assert result['cleanup_required']
    real = d.transport
    with patch.object(d, 'transport', side_effect=lambda p, q: (_ for _ in ()).throw(OSError('offline')) if q['action'] == 'collect' else real(p,q)):
        result = d.execution(config, root)
    assert result['stop_verified'] and not result.get('artifacts_collected')


def test_mutated_authorization_and_cross_store_fail_before_spawn(tmp_path):
    config, root = setup_case(tmp_path)
    config['devspace_execution']['plan']['argv'].append('changed')
    with patch.object(d, 'transport', side_effect=AssertionError('spawn')):
        with pytest.raises(ValueError, match='authorization'): d.execution(config, root)
    pin(config); config['task_event_collector']['tasks'][0]['uid'] = '00000000-0000-4000-8000-000000000003'
    with pytest.raises(ValueError, match='binding'): d.execution(config, root)


def test_artifact_tamper_cannot_reenter_review(tmp_path):
    config, root = setup_case(tmp_path); finish(config, root)
    (root / 'artifacts/result.json').write_text('{"score":1}')
    result = d.execution(config, root)
    assert result['status'] == 'awaiting-reconciliation' and 'artifact_changed' in result['last_error']


def test_cli_mode_cannot_mix_inspection_and_execution(tmp_path):
    config, root = setup_case(tmp_path)
    p = tmp_path / 'project.json'; p.write_text(json.dumps(config))
    result = subprocess.run([sys.executable, '-B', str(ENTRY), '--project', str(p), '--devspace-execution', '--task-notifications'],capture_output=True,text=True)
    assert result.returncode != 0 and not (tmp_path / 'remote').exists()


def native_case(tmp_path):
    config, root = setup_case(tmp_path)
    plan = config['devspace_execution']['plan']
    binary = tmp_path / 'lpai-agent'; binary.touch()
    plan.update(transport='ssh', supervised_cleanup=True, route_evidence='CPU mocked official capability inventory',
                ssh={'host':'example.invalid','port':22,'user':'test','key':str(tmp_path/'test-key')})
    plan['resource'].update(route='native-ui', cli_path=str(binary), name='cpu-space', cluster='cpu')
    pin(config)
    return config, root


def test_invalid_receipt_does_not_poison_existing_lifecycle(tmp_path):
    config, root = setup_case(tmp_path)
    public(config, root)
    before = (root / 'execution.json').read_bytes()
    bad = tmp_path / 'bad.json'; bad.write_text('{"action":"stop"}')
    with pytest.raises(ValueError, match='existing_native_action_request_required'):
        d.execution(config, root, bad)
    assert (root / 'execution.json').read_bytes() == before
    finish(config, root)


def test_explicit_unbounded_case_completes_and_still_cleans_up(tmp_path):
    config, root = setup_case(tmp_path)
    plan = config['devspace_execution']['plan']
    plan.update(limit_policy='user-authorized-unbounded', limit_authorization_ref='explicit CPU test authorization',
                deadline=None, max_seconds=None, budget=None)
    pin(config)
    assert d.inspect(config)['linear_cost_estimate'] is None
    result = finish(config, root, True)
    assert result['deadline'] is None and result['stop_verified'] and result['outcome'] == 'succeeded'


def test_null_limits_without_explicit_authorization_rejected(tmp_path):
    config, root = setup_case(tmp_path)
    plan = config['devspace_execution']['plan']
    plan.update(deadline=None, max_seconds=None, budget=None)
    pin(config)
    with pytest.raises(ValueError): d.inspect(config)
    plan['limit_policy'] = 'user-authorized-unbounded'; pin(config)
    with pytest.raises(ValueError, match='authorization'): d.inspect(config)


def test_relative_native_receipt_and_pending_cleanup_not_failure(tmp_path, monkeypatch):
    config, root = native_case(tmp_path)
    phase = {'value': 'stopped'}
    monkeypatch.setattr(d, 'resource_call', lambda *a: {'uid': UID, 'phase': phase['value'], 'observed_at': time.time()})
    d.execution(config, root, allow_live=True)
    req = json.loads((root / 'start-request.json').read_bytes())
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps({'request_digest': digest(req), 'uid': UID, 'action': 'start', 'performed': True, 'at': time.time(), 'source_ref': 'test UI'}))
    monkeypatch.chdir(tmp_path)
    phase['value'] = 'pending'
    result = d.execution(config, root, Path('receipt.json'), True)
    assert result['status'] == 'awaiting-start-readback' and not result.get('cleanup_required')
    state = json.loads((root / 'execution.json').read_bytes()); state['cleanup_required'] = True
    (root / 'execution.json').write_text(json.dumps(state))
    result = d.execution(config, root, allow_live=True)
    assert result['status'] == 'awaiting-stop-readback'
    kinds = [n['kind'] for n in s.notifications(config)['notifications']]
    assert 'cleanup_pending' in kinds and 'cleanup_failed' not in kinds


def test_public_json_uses_utf8_with_legacy_windows_encoding(tmp_path):
    config, root = setup_case(tmp_path)
    p = tmp_path / 'project.json'; p.write_text(json.dumps(config))
    import task_event_collector as collector
    db = collector.connect(collector.validate(config), 'cpu-simulation'); db.close()
    d.emit(config, root, {'binding': digest(config['devspace_execution']['plan']), 'uid': UID,
                         'evidence_kind': 'cpu-simulation'}, 'encoding_check', {})
    result = subprocess.run([sys.executable, '-B', str(ENTRY), '--project', str(p), '--task-notifications'],
                            capture_output=True, env={**os.environ, 'PYTHONIOENCODING': 'ascii'})
    assert result.returncode == 0
    assert '开发空间事件' in json.loads(result.stdout.decode('utf-8'))['notifications'][0]['message']


def action_receipt(root, action):
    request = json.loads((root / (action + '-request.json')).read_bytes())
    path = root / (action + '-test-receipt.json')
    path.write_text(json.dumps({'request_digest':digest(request),'uid':UID,'action':action,
                               'performed':True,'at':time.time(),'source_ref':'mock native UI evidence'}))
    return path


def test_native_actions_require_bound_receipt_and_real_readback(tmp_path):
    config, root = native_case(tmp_path); phase = ['stopped']; calls = []
    def resource(plan, action):
        calls.append(action); assert action == 'get'
        return {'uid':UID,'phase':phase[0],'observed_at':time.time()}
    real = d.transport
    def cpu(plan, packet): return real({**plan,'transport':'local-cpu'}, packet)
    with patch.object(d,'resource_call',side_effect=resource), patch.object(d,'transport',side_effect=cpu):
        assert d.execution(config,root,allow_live=True)['status'] == 'awaiting-start-readback'
        phase[0]='ready'
        assert d.execution(config,root,allow_live=True)['status'] == 'awaiting-start-action-receipt'
        d.execution(config,root,action_receipt(root,'start'),True)
        until=time.time()+10
        while time.time()<until:
            result=d.execution(config,root,allow_live=True)
            if result['status']=='awaiting-stop-readback':break
            time.sleep(.1)
        assert not result['stop_verified'] and result['process_terminal']
        # Clicking stop is not sufficient; unchanged platform readback blocks release.
        receipt=action_receipt(root,'stop')
        assert not d.execution(config,root,receipt,True)['stop_verified']
        phase[0]='stopped'
        result=d.execution(config,root,allow_live=True)
        assert result['stop_verified'] and result['status']=='awaiting-review'
        assert set(calls)=={'get'}


def test_live_route_guard_blocks_before_any_resource_query(tmp_path):
    config,root=native_case(tmp_path)
    with patch.object(d,'resource_call',side_effect=AssertionError('platform')):
        with pytest.raises(ValueError,match='allow_devspace_live'):d.execution(config,root)
        config['devspace_execution']['plan']['supervised_cleanup']=False;pin(config)
        with pytest.raises(ValueError,match='supervised_cleanup'):d.execution(config,root,allow_live=True)


def test_unknown_official_start_is_not_reissued(tmp_path):
    config,root=native_case(tmp_path)
    config['devspace_execution']['plan']['resource']['route']='official-cli';pin(config)
    writes=[]
    def resource(plan,action):
        if action=='get':return {'uid':UID,'phase':'stopped','observed_at':time.time()}
        writes.append(action);raise OSError('unknown response')
    with patch.object(d,'resource_call',side_effect=resource):
        d.execution(config,root,allow_live=True)
        d.execution(config,root,allow_live=True)
        d.execution(config,root,allow_live=True)
    assert writes==['start']
    state=json.loads((root/'execution.json').read_bytes())
    assert not state['stop_verified'] and state['status']=='awaiting-start-intent-reconciliation'


def test_bad_native_receipt_never_launches(tmp_path):
    config,root=native_case(tmp_path)
    with patch.object(d,'resource_call',return_value={'uid':UID,'phase':'stopped','observed_at':time.time()}):
        d.execution(config,root,allow_live=True)
        receipt=action_receipt(root,'start');v=json.loads(receipt.read_bytes());v['uid']='other';receipt.write_text(json.dumps(v))
        with patch.object(d,'transport',side_effect=AssertionError('spawn')):
            before=(root/'execution.json').read_bytes()
            with pytest.raises(ValueError, match='bound_native'):
                d.execution(config,root,receipt,True)
            assert (root/'execution.json').read_bytes()==before


def test_preflight_checks_inputs_without_authorization_or_platform(tmp_path):
    config,root=native_case(tmp_path)
    config['devspace_execution'].pop('authorization')
    with patch.object(d,'resource_call',side_effect=AssertionError('platform')):
        result=d.inspect(config)
        assert not result['execution_authorized'] and not result['platform_queried']
        (tmp_path/'workload.py').write_text('changed')
        with pytest.raises(ValueError,match='pinned_input_changed'):d.inspect(config)


def test_changed_inputs_block_before_resource_mutation(tmp_path):
    config,root=native_case(tmp_path)
    (tmp_path/'workload.py').write_text('changed')
    with patch.object(d,'resource_call',side_effect=AssertionError('platform')):
        with pytest.raises(ValueError,match='pinned_input_changed'):d.execution(config,root,allow_live=True)


def test_process_group_child_does_not_survive_finished_parent(tmp_path):
    if os.name=='nt':pytest.skip('POSIX remote process group contract')
    child="import time;from pathlib import Path;time.sleep(2);Path('orphan-effect').touch()"
    code=f"import subprocess,sys,json,time\nfrom pathlib import Path\nsubprocess.Popen([sys.executable,'-c',{child!r}])\nPath('result.json').write_text(json.dumps({{'score':.7}}))\n"
    config,root=setup_case(tmp_path,code)
    finish(config,root);time.sleep(2)
    assert not (tmp_path/'remote/orphan-effect').exists()


def test_native_cleanup_survives_ssh_failure_and_preserves_unknown(tmp_path):
    config,root=native_case(tmp_path)
    plan=config['devspace_execution']['plan'];plan['adopt_existing_ready']=True;pin(config)
    with patch.object(d,'resource_call',return_value={'uid':UID,'phase':'ready','observed_at':time.time()}), \
         patch.object(d,'transport',side_effect=OSError('SSH unreachable')):
        result=d.execution(config,root,allow_live=True)
        state=json.loads((root/'execution.json').read_bytes());state['deadline']=time.time()-1
        (root/'execution.json').write_text(json.dumps(state))
        result=d.execution(config,root,allow_live=True)
    assert (root/'stop-request.json').exists() and not result['stop_verified']
    assert not result['process_terminal'] and 'SSH' in result['process_stop_error']


def test_foreign_remote_attempt_cannot_be_cancelled_or_collected(tmp_path):
    from devspace_worker import request
    root=tmp_path/'foreign';root.mkdir()
    (root/'capsule.json').write_text(json.dumps({'binding':'other-attempt'}))
    for action in ('observe','cancel','collect'):
        with pytest.raises(ValueError,match='binding_mismatch'):
            request({'root':str(root),'action':action,'binding':'this-attempt'})
    assert not (root/'cancel').exists()


def test_unknown_resource_shape_or_phase_does_not_start(tmp_path):
    config,root=native_case(tmp_path)
    config['devspace_execution']['plan']['resource']['route']='official-cli';pin(config)
    for value in ({'uid':UID,'phase':'unrecognized'}, {'uid':UID}):
        def read_only(plan, action):
            assert action=='get'
            return value
        with patch.object(d,'resource_call',side_effect=read_only):
            result=d.execution(config,root,allow_live=True)
        assert result['status']=='awaiting-reconciliation'
        assert not (root/'start-request.json').exists()
