"""CPU detached-job fixtures; none of these observations are live S12/S13 evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from paired_execution import advance, authorize, contract, cost_usage, validate_config
from round_reporting import digest
from review_storage import read_json
from test_comparison_preflight import plan


def ref(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')
    return {'path': str(path.resolve()), 'digest': 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()}


def config(tmp_path, p=None, mode='fixture'):
    p = p or plan()
    entry = tmp_path / 'adapter.py'
    entry.write_text('def create_adapter(settings):\n    raise RuntimeError("fixture injection required")\n')
    c = {'schema': 'sdm-paired-execution/v1', 'platform_mode': mode,
         'plan_ref': ref(tmp_path / 'plan.json', p), 'adapter': {'path': str(entry.resolve()),
         'digest': 'sha256:' + hashlib.sha256(entry.read_bytes()).hexdigest()}, 'adapter_settings': {},
         'monitoring': {'poll_seconds': 1, 'call_timeout_seconds': 2, 'cleanup_reserve_seconds': 20,
                        'stop_retry_seconds': 2, 'health_max_age_seconds': 5, 'stall_seconds': 15,
                        'health_start_grace_seconds': 3, 'max_stop_attempts': 2,
                        'health_required': True, 'stop_flags': ['nan', 'oom', 'collapse']}}
    a = {'schema': 'sdm-paired-authorization/v1', 'contract_digest': contract(c), 'plan_digest': digest(p),
         'status': 'confirmed', 'user_message_ref': 'SYNTHETIC CPU test only',
         'operations': ['submit', 'stop-owned-until-released'], 'submit_before': 5000, 'resource_deadline': 9000}
    c['authorization_ref'] = ref(tmp_path / 'approval.json', a)
    return c


class Fake:
    is_live = False
    def __init__(self):
        self.jobs = {}; self.submits = []; self.stops = []; self.now = 1000
        self.fail_arm = None; self.external = []; self.unknown = None; self.lookup_complete = True
        self.release_on_stop = True
    def preflight(self, r):
        if self.fail_arm == r['arm_id']: raise ValueError('fixture_admission_failed')
        return {'ready': True, 'project': r['project'], 'observed_at': self.now, 'external_allocations': self.external,
                'normal_preflight_ref': 'SYNTHETIC preflight only',
                'lease_guard': {'deadline': r['deadline'], 'evidence_ref': 'SYNTHETIC guard only',
                                'independent': True, 'scope': 'allocation-release'}}
    def lookup(self, r):
        return {'complete': self.lookup_complete, 'matches': [self.observe(u, r) for u, j in self.jobs.items() if j['request_key'] == r['request_key']]}
    def submit(self, r):
        uid = 'fixture-' + r['arm_id']; self.submits.append(r['arm_id'])
        self.jobs[uid] = {'uid': uid, 'request_key': r['request_key'], 'job_name': r['job_name'],
                          'phase': 'running', 'released': False, 'gpus': r['declared_arm']['allocation']['gpus'],
                          'hourly_cost': r['declared_arm']['allocation']['hourly_cost']}
        if self.unknown == 'crash': raise SystemExit('synthetic crash after platform create')
        if self.unknown == 'timeout': raise RuntimeError('synthetic lost response')
        return {'outcome': 'submitted', 'uid': uid, 'observation': self.observe(uid, r)}
    def observe(self, uid, r):
        value = deepcopy(self.jobs[uid])
        value.setdefault('health', {'uid': uid, 'request_key': r['request_key'], 'observed_at': self.now,
                                   'last_progress_at': self.now, 'flags': []})
        return value
    def stop(self, uid, r, reason):
        self.stops.append((uid, reason))
        if self.release_on_stop: self.jobs[uid].update(phase='stopped', released=True)
        return {'acknowledged': True}


def tick(c, root, f, now, **kwargs):
    f.now = now
    return advance(c, root, f, now=now, **kwargs)


def test_both_remote_jobs_overlap_serial_submissions_and_no_scientific_certificate(tmp_path):
    c = config(tmp_path); f = Fake(); out = tmp_path / 'run'
    state = tick(c, out, f, 1000)
    assert f.submits == ['control', 'treatment'] and state['status'] == 'running'
    assert state['usage']['reserved_gpus'] == 8 and state['qualification'] == 'not-assessed'
    assert [e['event'] for e in state['events']] == ['submission-intent', 'submitted', 'submission-intent', 'submitted']
    for job in f.jobs.values(): job.update(phase='succeeded', released=True)
    state = tick(c, out, f, 1100)
    assert state['status'] == 'executions-terminal-evidence-required'
    assert state['usage']['reserved_gpus'] == 0 and not state['usage']['billing_is_measured']
    assert tick(c, out, f, 1101)['status'] == state['status'] and len(f.submits) == 2


def test_sequential_does_not_apply_parallel_launch_skew(tmp_path):
    p = plan(); p['schedule'] = 'sequential'; p['limits']['max_wall_hours'] = 4.1
    c = config(tmp_path, p); f = Fake(); out = tmp_path / 'run'
    tick(c, out, f, 1000); assert f.submits == ['control']
    f.jobs['fixture-control'].update(phase='succeeded', released=True)
    state = tick(c, out, f, 2000)
    assert f.submits == ['control', 'treatment'] and state['status'] == 'running'


def test_single_task_policy_blocks_pair_before_any_adapter_operation(tmp_path):
    p = plan(); p['limits']['max_concurrent_tasks'] = 1; c = config(tmp_path, p); f = Fake()
    with pytest.raises(ValueError, match='max_concurrent_tasks'): tick(c, tmp_path / 'run', f, 1000)
    assert not f.submits


@pytest.mark.parametrize('fault', ['expired', 'binding', 'scope', 'live'])
def test_unapproved_live_or_changed_scope_never_imports_adapter_or_submits(tmp_path, fault):
    c = config(tmp_path, mode='live' if fault == 'live' else 'fixture'); f = Fake()
    a = read_json(Path(c['authorization_ref']['path']))
    if fault == 'expired': a['submit_before'] = 999
    if fault == 'binding': a['contract_digest'] = 'wrong'
    if fault == 'scope': a['operations'] = ['submit']
    c['authorization_ref'] = ref(tmp_path / 'approval.json', a)
    if fault == 'expired':
        state = tick(c, tmp_path / 'run', f, 1000); assert state['status'] == 'incomplete'
    else:
        with pytest.raises(ValueError): tick(c, tmp_path / 'run', f, 1000)
    assert not f.submits


@pytest.mark.parametrize('partial,stop', [('stop-started-arm', True), ('finish-started-arm', False)])
def test_second_admission_failure_respects_partial_policy(tmp_path, partial, stop):
    p = plan(); p['partial_start_policy'] = partial; c = config(tmp_path, p)
    f = Fake(); f.fail_arm = 'treatment'; out = tmp_path / 'run'
    state = tick(c, out, f, 1000)
    assert f.submits == ['control'] and bool(f.stops) is stop
    if not stop: f.jobs['fixture-control'].update(phase='succeeded', released=True)
    state = tick(c, out, f, 1001)
    assert state['status'] == 'incomplete' and f.submits == ['control']


def test_initial_admission_failure_does_not_launch_unpaired_treatment(tmp_path):
    c = config(tmp_path); f = Fake(); f.fail_arm = 'control'
    assert tick(c, tmp_path / 'run', f, 1000)['status'] == 'incomplete' and not f.submits


@pytest.mark.parametrize('unknown', ['timeout', 'crash'])
def test_lost_submit_response_or_crash_reconciles_uid_without_resubmission(tmp_path, unknown):
    c = config(tmp_path); f = Fake(); f.unknown = unknown; out = tmp_path / 'run'
    if unknown == 'crash':
        with pytest.raises(SystemExit): tick(c, out, f, 1000)
    else: assert tick(c, out, f, 1000)['status'] == 'cleanup-required'
    first = read_json(out / 'state.json'); deadline = first['arms']['control']['request']['deadline']
    f.unknown = None; state = tick(c, out, f, 1001, allow_submit=False)
    assert state['arms']['control']['uid'] == 'fixture-control' and f.submits == ['control']
    assert state['arms']['control']['request']['deadline'] == deadline


def test_incomplete_lookup_never_retries_submission(tmp_path):
    c = config(tmp_path); f = Fake(); f.unknown = 'timeout'; out = tmp_path / 'run'
    tick(c, out, f, 1000); f.lookup_complete = False
    state = tick(c, out, f, 1001)
    assert state['status'] == 'cleanup-required' and f.submits == ['control'] and not f.stops


@pytest.mark.parametrize('failure_policy,stops', [('stop-peer', 1), ('finish-peer', 0)])
def test_one_failed_released_arm_applies_peer_policy(tmp_path, failure_policy, stops):
    p = plan(); p['arm_failure_policy'] = failure_policy; c = config(tmp_path, p); f = Fake(); out = tmp_path / 'run'
    tick(c, out, f, 1000); f.jobs['fixture-control'].update(phase='failed', released=True)
    tick(c, out, f, 1004); assert len(f.stops) == stops


@pytest.mark.parametrize('health', ['stale', 'nan', 'malformed', 'stall', 'missing-progress'])
def test_declared_health_failures_stop_both(tmp_path, health):
    c = config(tmp_path); f = Fake(); out = tmp_path / 'run'; tick(c, out, f, 1000)
    uid = 'fixture-control'; key = f.jobs[uid]['request_key']
    proof = {'uid': uid, 'request_key': key, 'observed_at': 1020, 'last_progress_at': 1020, 'flags': []}
    if health == 'stale': proof['observed_at'] = 1000
    if health == 'nan': proof['flags'] = ['nan']
    if health == 'malformed': proof['flags'] = [{}]
    if health == 'stall': proof['last_progress_at'] = 1000
    if health == 'missing-progress': proof.pop('last_progress_at')
    f.jobs[uid]['health'] = proof
    tick(c, out, f, 1020); assert len(f.stops) == 2


@pytest.mark.parametrize('kind', ['gpu', 'price', 'external', 'stale-snapshot'])
def test_actual_allocation_and_capacity_limits_block_or_stop(tmp_path, kind):
    c = config(tmp_path); f = Fake(); out = tmp_path / 'run'
    if kind == 'external': f.external = [{'uid': 'other-owned-task', 'gpus': 8, 'hourly_cost': 5}]
    if kind == 'stale-snapshot':
        original = f.preflight
        def stale(r):
            value = original(r); value['observed_at'] = 900; return value
        f.preflight = stale
    state = tick(c, out, f, 1000)
    if kind in ('external', 'stale-snapshot'): assert not f.submits and state['status'] == 'incomplete'
    else:
        f.jobs['fixture-control']['gpus' if kind == 'gpu' else 'hourly_cost'] = 100
        state = tick(c, out, f, 1001); assert len(f.stops) == 2 and state['group_stop'] == 'actual-allocation-exceeds-plan'


def test_group_and_arm_deadlines_survive_restart_and_include_cleanup(tmp_path):
    c = config(tmp_path); f = Fake(); out = tmp_path / 'run'; state = tick(c, out, f, 1000)
    deadline = state['resource_deadline']; arm_deadline = state['arms']['control']['request']['deadline']
    state = tick(c, out, f, arm_deadline - 19)
    assert len(f.stops) == 2 and state['resource_deadline'] == deadline
    state = tick(c, out, f, arm_deadline - 18)
    assert not state['unreleased_arms'] and len(f.submits) == 2


def test_stop_ack_is_not_release_and_retries_require_fresh_readback(tmp_path):
    c = config(tmp_path); f = Fake(); f.release_on_stop = False; out = tmp_path / 'run'
    tick(c, out, f, 1000)
    state = tick(c, out, f, 1001, force_stop=True)
    assert state['status'] == 'stopping' and len(f.stops) == 2 and len(state['unreleased_arms']) == 2
    tick(c, out, f, 1001); assert len(f.stops) == 2
    tick(c, out, f, 1004); assert len(f.stops) == 4
    state = tick(c, out, f, 1022); assert state['status'] == 'cleanup-required' and len(f.stops) == 4
    for j in f.jobs.values(): j.update(phase='stopped', released=True)
    assert tick(c, out, f, 1023)['status'] == 'executions-terminal-evidence-required'


def test_observation_identity_mismatch_never_silently_accepts_other_task(tmp_path):
    c = config(tmp_path); f = Fake(); out = tmp_path / 'run'; tick(c, out, f, 1000)
    f.jobs['fixture-control']['request_key'] = 'foreign-request'
    state = tick(c, out, f, 1001)
    assert state['group_stop'] == 'task-observation-unavailable' and state['arms']['control']['phase'] == 'unknown'


def test_expired_submit_scope_still_allows_owned_cleanup(tmp_path):
    c = config(tmp_path); f = Fake(); out = tmp_path / 'run'; tick(c, out, f, 1000)
    state = tick(c, out, f, 5001, allow_submit=False, force_stop=True)
    assert len(f.stops) == 2 and state['status'] == 'stopping'


def test_clock_rollback_does_not_extend_budget(tmp_path):
    c = config(tmp_path); f = Fake(); out = tmp_path / 'run'; state = tick(c, out, f, 1000)
    deadline = state['resource_deadline']; state = tick(c, out, f, 999)
    assert state['group_stop'] == 'clock_moved_backwards' and state['resource_deadline'] == deadline and len(f.stops) == 2


def test_no_submission_after_deadline_without_ready_loop(tmp_path):
    c = config(tmp_path); a = read_json(Path(c['authorization_ref']['path'])); a['resource_deadline'] = 999
    c['authorization_ref'] = ref(tmp_path / 'approval.json', a); f = Fake()
    state = tick(c, tmp_path / 'run', f, 1000)
    assert state['status'] == 'incomplete' and not f.submits


def test_budget_conservative_until_confirmed_release(tmp_path):
    c = config(tmp_path); f = Fake(); out = tmp_path / 'run'; state = tick(c, out, f, 1000)
    usage = cost_usage(state, plan(), 4600)
    assert usage['gpu_hours_upper_bound'] == 8 and usage['cost_upper_bound'] == 5.5
    assert usage['reserved_hourly_cost'] == 5 and not usage['billing_is_measured']


def test_historical_allocation_peak_survives_zero_resource_release_readback(tmp_path):
    c = config(tmp_path); f = Fake(); out = tmp_path/'run'; tick(c, out, f, 1000)
    f.jobs['fixture-control'].update(gpus=8, hourly_cost=10)
    tick(c, out, f, 1001)
    for j in f.jobs.values(): j.update(gpus=0, hourly_cost=0, phase='stopped', released=True)
    state = tick(c, out, f, 1002)
    assert state['arms']['control']['observed_gpus'] == 8 and state['arms']['control']['observed_hourly_cost'] == 10
    assert state['usage']['gpu_hours_upper_bound'] == pytest.approx(12 * 2 / 3600)
    assert state['usage']['cost_upper_bound'] == pytest.approx(.5 + 12.5 * 2 / 3600)


def test_binding_changes_cannot_execute_mutated_adapter(tmp_path):
    c = config(tmp_path); f = Fake(); out = tmp_path / 'run'; tick(c, out, f, 1000)
    Path(c['adapter']['path']).write_text('raise RuntimeError("must never execute")')
    with pytest.raises(ValueError): tick(c, out, f, 1001)
    assert len(f.submits) == 2 and not f.stops


def test_run_interrupt_continues_cleanup_readback_without_new_submissions(tmp_path, monkeypatch):
    import paired_execution as module
    c = config(tmp_path); calls = []
    monkeypatch.setattr(module.time, 'time', lambda: 1000)
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(module, 'AdapterBinding', lambda config: Fake())
    def step(config, output, provider, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1: raise KeyboardInterrupt()
        return {'status': 'stopping' if len(calls) == 2 else 'executions-terminal-evidence-required'}
    monkeypatch.setattr(module, 'advance', step)
    assert module.run(c, tmp_path/'run')['status'] == 'executions-terminal-evidence-required'
    assert len(calls) == 3 and all(x['force_stop'] and not x['allow_submit'] for x in calls[1:])


def test_stop_approval_pending_is_preserved_without_duplicate_requests(tmp_path):
    c=config(tmp_path);f=Fake();out=tmp_path/'run';tick(c,out,f,1000)
    def pending(uid,r,reason):
        f.stops.append((uid,reason));return {'acknowledged':False,'approval_pending':True,'approval_id':'fixture-stop-approval'}
    f.stop=pending
    state=tick(c,out,f,1001,force_stop=True)
    assert all(v.get('stop_approval_id')=='fixture-stop-approval' and not v['stop_acknowledged'] for v in state['arms'].values())
    state=tick(c,out,f,1005)
    assert len(f.stops)==2 and len(state['unreleased_arms'])==2


def test_pending_start_retains_approval_without_creating_uid_or_resending(tmp_path):
    c=config(tmp_path);f=Fake();out=tmp_path/'run'
    def pending(r):
        f.submits.append(r['arm_id']);return {'outcome':'approval-pending','approval_id':'fixture-start-approval'}
    f.submit=pending
    state=tick(c,out,f,1000);state=tick(c,out,f,1001)
    assert f.submits==['control'] and state['status']=='cleanup-required'
    assert state['arms']['control']['approval_id']=='fixture-start-approval' and 'uid' not in state['arms']['control']
