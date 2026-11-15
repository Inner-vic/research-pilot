"""Durable two-arm asynchronous coordination; platform effects belong to a pinned adapter."""
import argparse
from decimal import Decimal
import importlib.util
import json
import math
from pathlib import Path
import time
import uuid

from comparison_preflight import preflight
from comparison_reporting import load_ref
from project_driver import resolve
from review_storage import atomic_json, locked, read_json
from round_reporting import digest, safe_id

ACTIVE = {'pending', 'starting', 'running', 'stopping', 'unknown'}
TERMINAL = {'succeeded', 'failed', 'stopped'}


def number(value, minimum=0):
    if type(value) not in (int, float): return False
    try: return math.isfinite(value) and value >= minimum
    except OverflowError: return False


def contract(config):
    return digest({k: v for k, v in config.items() if k != 'authorization_ref'})


def bound_module(binding):
    path = resolve({'project_driver': binding}, Path.cwd())
    module_spec = importlib.util.spec_from_file_location('sdm_paired_' + uuid.uuid4().hex, path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


class AdapterBinding:
    def __init__(self, config):
        self.binding = config['adapter']; self.config = config
        self.adapter = bound_module(self.binding).create_adapter(config['adapter_settings'])
        if type(getattr(self.adapter, 'is_live', None)) is not bool: raise ValueError('adapter_live_mode_required')
        self.is_live = self.adapter.is_live
    def call(self, operation, *args):
        resolve({'project_driver': self.binding}, Path.cwd())
        value = getattr(self.adapter, operation)(*args)
        if not isinstance(value, dict): raise ValueError('adapter_response_object_required')
        return value


def validate_config(config):
    if not isinstance(config, dict) or config.get('schema') != 'sdm-paired-execution/v1':
        raise ValueError('paired_execution_schema_required')
    plan, reference = load_ref(config.get('plan_ref'))
    checks = preflight(plan)
    if checks['status'] != 'pass': raise ValueError('paired_plan_not_ready:' + ','.join(checks['errors']))
    roles = {a.get('role', a.get('id')): a for a in plan['arms']}
    if set(roles) != {'control', 'treatment'}: raise ValueError('paired_roles_required')
    for arm in plan['arms']: safe_id(arm['id'])
    if config.get('platform_mode') not in ('live', 'fixture'): raise ValueError('explicit_platform_mode_required')
    monitor = config.get('monitoring', {})
    for key in ('poll_seconds', 'call_timeout_seconds', 'cleanup_reserve_seconds', 'stop_retry_seconds',
                'health_max_age_seconds', 'stall_seconds'):
        if not number(monitor.get(key), 0.001): raise ValueError('positive_monitoring_setting_required:' + key)
    for key in ('health_start_grace_seconds',):
        if not number(monitor.get(key)): raise ValueError('monitoring_setting_required:' + key)
    if type(monitor.get('max_stop_attempts')) is not int or not 1 <= monitor['max_stop_attempts'] <= 3:
        raise ValueError('bounded_stop_attempts_required')
    if type(monitor.get('health_required')) is not bool: raise ValueError('explicit_health_scope_required')
    if monitor['poll_seconds'] > 60 or monitor['call_timeout_seconds'] > 60:
        raise ValueError('bounded_poll_and_call_timeout_required')
    if not isinstance(monitor.get('stop_flags'), list) or not all(isinstance(v, str) for v in monitor['stop_flags']):
        raise ValueError('declared_health_stop_flags_required')
    reserve = monitor['cleanup_reserve_seconds']
    for arm in plan['arms']:
        if arm['allocation']['max_resource_hours'] * 3600 <= reserve + monitor['call_timeout_seconds']:
            raise ValueError('arm_lease_must_include_cleanup_and_call_reserve')
    resolve({'project_driver': config.get('adapter')}, Path.cwd())
    if not isinstance(config.get('adapter_settings'), dict): raise ValueError('adapter_settings_required')
    return plan, reference


def authorize(config, plan, now, submitting=False):
    authorization, _ = load_ref(config.get('authorization_ref'))
    expected = {'schema': 'sdm-paired-authorization/v1', 'contract_digest': contract(config),
                'plan_digest': digest(plan), 'status': 'confirmed'}
    if any(authorization.get(k) != v for k, v in expected.items()): raise ValueError('paired_authorization_binding_mismatch')
    if not authorization.get('user_message_ref') or authorization.get('operations') != ['submit', 'stop-owned-until-released']:
        raise ValueError('explicit_submit_and_stop_scope_required')
    if not number(authorization.get('submit_before')) or not number(authorization.get('resource_deadline')):
        raise ValueError('absolute_authorization_deadlines_required')
    if submitting and now >= authorization['submit_before']: raise ValueError('submission_authorization_expired')
    return authorization


def cost_usage(state, plan, now):
    gpu_hours = Decimal(0); cost = Decimal(str(plan['billing_margin_cost'])); peak_gpus = 0; peak_price = Decimal(0)
    for arm in plan['arms']:
        saved = state['arms'][arm['id']]
        if saved.get('submit_at') is None: continue
        endpoint = saved.get('released_at', now)
        hours = Decimal(str(max(0, endpoint - saved['submit_at']))) / Decimal(3600)
        gpus = max(arm['allocation']['gpus'], saved.get('observed_gpus', 0))
        price = max(Decimal(str(arm['allocation']['hourly_cost'])), Decimal(str(saved.get('observed_hourly_cost', 0))))
        gpu_hours += hours * gpus; cost += hours * price
        if 'released_at' not in saved: peak_gpus += gpus; peak_price += price
    return {'gpu_hours_upper_bound': float(gpu_hours), 'cost_upper_bound': float(cost),
            'reserved_gpus': peak_gpus, 'reserved_hourly_cost': float(peak_price),
            'billing_is_measured': False}


def request_for(state, plan, arm, now):
    deadline = min(state['resource_deadline'], now + arm['allocation']['max_resource_hours'] * 3600)
    request = {'schema': 'sdm-paired-arm-request/v1', 'group_id': plan['group_id'], 'project': plan['project'],
            'arm_id': arm['id'], 'job_name': arm['job_name'], 'plan_digest': digest(plan),
            'contract_digest': state['contract_digest'], 'request_key': digest({'nonce': state['nonce'], 'arm': arm['id']}),
            'deadline': deadline, 'call_timeout_seconds': state['monitoring']['call_timeout_seconds'],
            'observation_max_age_seconds': state['monitoring']['health_max_age_seconds'],
            'declared_arm': arm, 'declared_arm_digest': digest(arm),
            'group_schedule': plan['schedule'], 'group_limits': plan['limits'],
            'owned_task_uids': sorted(v['uid'] for v in state['arms'].values() if v.get('uid'))}
    request['request_digest'] = digest(request)
    return request


def observation(value, request, uid):
    if any(value.get(k) != v for k, v in {'uid': uid, 'request_key': request['request_key'], 'job_name': request['job_name']}.items()):
        raise ValueError('observed_task_identity_mismatch')
    if value.get('phase') not in ACTIVE | TERMINAL or type(value.get('released')) is not bool:
        raise ValueError('explicit_phase_and_release_readback_required')
    if type(value.get('gpus')) is not int or value['gpus'] < 0 or not number(value.get('hourly_cost')):
        raise ValueError('actual_allocation_observation_required')
    if value['released'] and value['phase'] not in TERMINAL: raise ValueError('active_task_cannot_be_released')
    return value


def advance(config, output, adapter=None, now=None, allow_live=False, allow_submit=True, force_stop=False):
    plan, plan_ref = validate_config(config); timestamp = time.time() if now is None else now
    if not number(timestamp): raise ValueError('valid_current_time_required')
    approval = authorize(config, plan, timestamp)
    if config['platform_mode'] == 'live' and not allow_live: raise ValueError('live_platform_effects_disabled')
    provider = adapter or AdapterBinding(config)
    if provider.is_live != (config['platform_mode'] == 'live'): raise ValueError('adapter_mode_mismatch')
    root = Path(output)
    if '.git' in root.resolve().parts or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('plain_paired_state_root_required')
    monitor = config['monitoring']; arms = plan['arms']; fingerprint = contract(config)
    monotonic_start = time.monotonic(); tick_start = timestamp
    with locked(root):
        path = root / 'state.json'
        if path.exists():
            state = read_json(path)
            if state['contract_digest'] != fingerprint: raise ValueError('changed_pair_requires_fresh_group_directory')
        else:
            state = {'schema': 'sdm-paired-state/v1', 'contract_digest': fingerprint, 'plan_ref': plan_ref,
                     'nonce': uuid.uuid4().hex, 'opened_at': timestamp, 'last_tick': timestamp,
                     'resource_deadline': min(approval['resource_deadline'], timestamp + plan['limits']['max_wall_hours'] * 3600),
                     'monitoring': monitor, 'arms': {a['id']: {'phase': 'not-submitted', 'stop_attempts': 0, 'observation_seq': 0} for a in arms},
                     'events': [], 'group_stop': None}
            atomic_json(root / 'execution-config.json', config)
        def persist(event=None):
            state['last_tick'] = timestamp
            if event:
                state['events'].append({'at': timestamp, **event})
                state['event_count'] = state.get('event_count', 0) + 1
                state['events'] = state['events'][-1000:]
            atomic_json(path, state)
        def call(name, *args):
            nonlocal timestamp
            try:
                if isinstance(provider, AdapterBinding): return provider.call(name, *args)
                value = getattr(provider, name)(*args)
                if not isinstance(value, dict): raise ValueError('adapter_response_object_required')
                return value
            finally:
                if now is None: timestamp = max(timestamp, time.time(), tick_start + time.monotonic() - monotonic_start)
        if timestamp < state['last_tick']: state['group_stop'] = 'clock_moved_backwards'
        timestamp = max(timestamp, state['last_tick']); state['last_tick'] = timestamp
        if force_stop: state['group_stop'] = state['group_stop'] or 'requested-group-stop'
        if timestamp + monitor['cleanup_reserve_seconds'] >= state['resource_deadline']:
            state['group_stop'] = state['group_stop'] or 'group-deadline'
        # Reconcile every existing effect before considering another submission.
        for arm in arms:
            saved = state['arms'][arm['id']]; req = saved.get('request')
            if req is None or 'released_at' in saved: continue
            if not saved.get('uid'):
                try:
                    found = call('lookup', req)
                    matches = found.get('matches')
                    if found.get('complete') is not True or not isinstance(matches, list) or len(matches) != 1:
                        raise ValueError('submission_outcome_requires_reconciliation')
                    uid = matches[0]['uid']; observation(matches[0], req, uid)
                    if not isinstance(uid, str) or not uid or any(v.get('uid') == uid for k, v in state['arms'].items() if k != arm['id']):
                        raise ValueError('distinct_task_uids_required')
                    saved['uid'] = uid; saved['phase'] = matches[0]['phase']
                    persist({'arm': arm['id'], 'event': 'submission-reconciled', 'uid': uid})
                except (ValueError, OSError, KeyError, RuntimeError) as error:
                    saved['phase'] = 'uncertain-submission'; saved['problem'] = type(error).__name__
                    state['group_stop'] = state['group_stop'] or 'uncertain-submission'
                    continue
            try:
                observed = observation(call('observe', saved['uid'], req), req, saved['uid'])
                saved['observation_seq'] += 1; saved['observation'] = observed; saved['phase'] = observed['phase']
                saved['observed_gpus'] = max(saved.get('observed_gpus', 0), observed['gpus'])
                saved['observed_hourly_cost'] = max(saved.get('observed_hourly_cost', 0), observed['hourly_cost'])
                saved.pop('problem', None)
                if observed['released']:
                    saved['released_at'] = timestamp; persist({'arm': arm['id'], 'event': 'release-verified', 'uid': saved['uid']})
            except (ValueError, OSError, KeyError, RuntimeError) as error:
                saved['problem'] = type(error).__name__; saved['phase'] = 'unknown'
                state['group_stop'] = state['group_stop'] or 'task-observation-unavailable'
        failed_ids = set()
        for arm in arms:
            saved = state['arms'][arm['id']]
            if saved.get('submit_at') is None: continue
            if saved['phase'] == 'failed': failed_ids.add(arm['id']); saved['stop_reason'] = 'task-failed'
            if 'released_at' in saved: continue
            req = saved['request']; observed = saved.get('observation', {})
            if timestamp + monitor['cleanup_reserve_seconds'] >= req['deadline']:
                saved['stop_reason'] = 'arm-deadline'; failed_ids.add(arm['id'])
            if observed.get('gpus', 0) > arm['allocation']['gpus'] or observed.get('hourly_cost', 0) > arm['allocation']['hourly_cost']:
                state['group_stop'] = state['group_stop'] or 'actual-allocation-exceeds-plan'
            if saved['phase'] == 'running' and timestamp - saved['submit_at'] >= monitor['health_start_grace_seconds']:
                health = observed.get('health')
                fresh = (isinstance(health, dict) and health.get('uid') == saved['uid'] and health.get('request_key') == req['request_key']
                         and number(health.get('observed_at')) and 0 <= timestamp - health['observed_at'] <= monitor['health_max_age_seconds'])
                if not fresh and monitor['health_required']:
                    saved['stop_reason'] = 'health-evidence-unavailable'; failed_ids.add(arm['id'])
                elif fresh:
                    flags = health.get('flags')
                    if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
                        saved['stop_reason'] = 'health-evidence-malformed'; failed_ids.add(arm['id'])
                    elif set(flags) & set(monitor['stop_flags']):
                        saved['stop_reason'] = 'declared-health-stop'; failed_ids.add(arm['id'])
                    progress = health.get('last_progress_at')
                    if monitor['health_required'] and (not number(progress) or progress > timestamp):
                        saved['stop_reason'] = 'progress-evidence-unavailable'; failed_ids.add(arm['id'])
                    if number(progress) and timestamp - progress >= monitor['stall_seconds']:
                        saved['stop_reason'] = 'progress-stalled'; failed_ids.add(arm['id'])
        if failed_ids and plan['arm_failure_policy'] == 'stop-peer': state['group_stop'] = state['group_stop'] or 'peer-failed'
        usage = cost_usage(state, plan, timestamp)
        reserve_hours = monitor['cleanup_reserve_seconds'] / 3600
        if (usage['cost_upper_bound'] + usage['reserved_hourly_cost'] * reserve_hours >= plan['limits']['max_total_cost']
            or usage['gpu_hours_upper_bound'] + usage['reserved_gpus'] * reserve_hours >= plan['limits']['max_gpu_hours']):
            state['group_stop'] = state['group_stop'] or 'aggregate-budget'
        # Normal submissions are serialized. Only their remote execution overlaps.
        for arm in arms:
            saved = state['arms'][arm['id']]
            if saved['phase'] != 'not-submitted' or state['group_stop'] or not allow_submit: continue
            if plan['schedule'] == 'sequential' and any(v.get('submit_at') is not None and 'released_at' not in v for v in state['arms'].values()): continue
            started = [v['submit_at'] for v in state['arms'].values() if v.get('submit_at') is not None]
            if plan['schedule'] == 'parallel' and started and timestamp > min(started) + plan['launch_window_hours'] * 3600:
                saved['phase'] = 'not-started'; saved['stop_reason'] = 'launch-window-exhausted'
                if plan['partial_start_policy'] == 'stop-started-arm': state['group_stop'] = 'partial-start'
                continue
            req = request_for(state, plan, arm, timestamp)
            try:
                authorize(config, plan, timestamp, submitting=True)
                admission = call('preflight', req)
                guard = admission.get('lease_guard', {})
                if admission.get('ready') is not True or not admission.get('normal_preflight_ref'):
                    raise ValueError('normal_project_preflight_required')
                if (guard.get('deadline') != req['deadline'] or not guard.get('evidence_ref')
                    or guard.get('independent') is not True or guard.get('scope') != 'allocation-release'):
                    raise ValueError('independent_runtime_lease_guard_required')
                external = admission.get('external_allocations')
                at = admission.get('observed_at')
                if (not isinstance(external, list) or admission.get('project') != plan['project']
                    or not number(at) or not 0 <= timestamp - at <= monitor['health_max_age_seconds']):
                    raise ValueError('fresh_scoped_external_allocation_snapshot_required')
                owned_uids = {v.get('uid') for v in state['arms'].values() if v.get('uid')}
                if any(not isinstance(x, dict) or not isinstance(x.get('uid'), str) or not x['uid']
                       or x['uid'] in owned_uids or type(x.get('gpus')) is not int or x['gpus'] < 0
                       or not number(x.get('hourly_cost')) for x in external):
                    raise ValueError('valid_other_allocation_snapshot_required')
                if len({x['uid'] for x in external}) != len(external): raise ValueError('distinct_external_allocations_required')
                ext_gpus = sum(x['gpus'] for x in external); ext_price = sum(x['hourly_cost'] for x in external)
                own = cost_usage(state, plan, timestamp)
                if (own['reserved_gpus'] + ext_gpus + arm['allocation']['gpus'] > plan['limits']['max_concurrent_gpus']
                    or sum(v.get('submit_at') is not None and 'released_at' not in v for v in state['arms'].values()) + len(external) + 1 > plan['limits']['max_concurrent_tasks']
                    or own['reserved_hourly_cost'] + ext_price + arm['allocation']['hourly_cost'] > plan['limits']['max_hourly_cost']):
                    raise ValueError('aggregate_capacity_unavailable')
                lookup = call('lookup', req)
                if lookup.get('complete') is not True or lookup.get('matches') != []:
                    raise ValueError('fresh_submission_identity_required')
                authorize(config, plan, timestamp, submitting=True)
                if timestamp + monitor['cleanup_reserve_seconds'] + monitor['call_timeout_seconds'] >= req['deadline']:
                    raise ValueError('launch_lease_exhausted')
            except (ValueError, OSError, KeyError, RuntimeError) as error:
                saved['phase'] = 'not-started'; saved['problem'] = str(error) if isinstance(error, ValueError) else type(error).__name__
                if started and plan['partial_start_policy'] == 'stop-started-arm': state['group_stop'] = 'partial-start'
                if not started:
                    state['group_stop'] = 'initial-admission-failed'
                continue
            saved.update(request=req, submit_at=timestamp, phase='submission-intent')
            atomic_json(root / ('request-' + arm['id'] + '.json'), req)
            persist({'arm': arm['id'], 'event': 'submission-intent'})
            try:
                response = call('submit', req)
                if response.get('outcome') == 'approval-pending':
                    saved['approval_id'] = response.get('approval_id')
                    saved['approval_pending'] = True
                    raise ValueError('platform_approval_pending_not_a_task')
                if response.get('outcome') != 'submitted' or not isinstance(response.get('uid'), str) or not response['uid']:
                    raise ValueError('submission_outcome_not_confirmed')
                uid = response['uid']; observation(response['observation'], req, uid)
                if any(v.get('uid') == uid for k, v in state['arms'].items() if k != arm['id']):
                    raise ValueError('distinct_task_uids_required')
                saved.update(uid=uid, phase=response['observation']['phase'], observation=response['observation'])
                saved['observed_gpus'] = response['observation']['gpus']; saved['observed_hourly_cost'] = response['observation']['hourly_cost']
                if (saved['observed_gpus'] > arm['allocation']['gpus'] or saved['observed_hourly_cost'] > arm['allocation']['hourly_cost']):
                    state['group_stop'] = 'actual-allocation-exceeds-plan'
                persist({'arm': arm['id'], 'event': 'submitted', 'uid': uid})
            except (ValueError, OSError, KeyError, RuntimeError) as error:
                saved['phase'] = 'uncertain-submission'; saved['problem'] = type(error).__name__
                state['group_stop'] = state['group_stop'] or 'uncertain-submission'; persist({'arm': arm['id'], 'event': 'submission-uncertain'})
        for arm in arms:
            saved = state['arms'][arm['id']]
            if not saved.get('uid') or 'released_at' in saved or not (state['group_stop'] or saved.get('stop_reason')): continue
            saved['stop_reason'] = saved.get('stop_reason') or state['group_stop']
            if saved.get('stop_approval_pending'): continue
            previous = saved.get('last_stop_at')
            retry = previous is None or (timestamp - previous >= monitor['stop_retry_seconds'] and saved['observation_seq'] > saved.get('stop_observation_seq', -1))
            if not retry or saved['stop_attempts'] >= monitor['max_stop_attempts']: continue
            saved['stop_attempts'] += 1; saved['last_stop_at'] = timestamp; saved['stop_observation_seq'] = saved['observation_seq']
            saved.setdefault('cleanup_deadline', min(saved['request']['deadline'], timestamp + monitor['cleanup_reserve_seconds']))
            persist({'arm': arm['id'], 'event': 'stop-intent', 'uid': saved['uid']})
            try:
                stopped = call('stop', saved['uid'], saved['request'], saved['stop_reason'])
                saved['stop_acknowledged'] = stopped.get('acknowledged') is True
                if stopped.get('approval_pending'):
                    saved['stop_approval_pending'] = True; saved['stop_approval_id'] = stopped.get('approval_id')
            except (ValueError, OSError, RuntimeError): saved['stop_acknowledged'] = False
        unreleased = [k for k, v in state['arms'].items() if v.get('submit_at') is not None and 'released_at' not in v]
        expired_cleanup = any(timestamp >= v.get('cleanup_deadline', math.inf) for v in state['arms'].values() if v.get('submit_at') is not None and 'released_at' not in v)
        unresolved = any(v['phase'] == 'uncertain-submission' for v in state['arms'].values())
        all_submitted = all(v.get('submit_at') is not None for v in state['arms'].values())
        if expired_cleanup or unresolved: state['status'] = 'cleanup-required'
        elif unreleased: state['status'] = 'stopping' if state['group_stop'] or failed_ids else 'running'
        elif all_submitted: state['status'] = 'executions-terminal-evidence-required'
        else: state['status'] = 'incomplete' if state['group_stop'] or any(v['phase'] != 'not-submitted' for v in state['arms'].values()) else 'ready'
        state['usage'] = cost_usage(state, plan, timestamp)
        state['unreleased_arms'] = unreleased
        state['qualification'] = 'not-assessed'; state['next_check_at'] = timestamp + monitor['poll_seconds']
        persist(); return state


def run(config, output, allow_live=False):
    plan, _ = validate_config(config); authorize(config, plan, time.time())
    if config['platform_mode'] == 'live' and not allow_live: raise ValueError('live_platform_effects_disabled')
    provider = AdapterBinding(config)
    interrupted = False
    while True:
        try:
            state = advance(config, output, provider, allow_live=allow_live,
                            allow_submit=not interrupted, force_stop=interrupted)
            if state['status'] in ('incomplete', 'executions-terminal-evidence-required', 'cleanup-required'): return state
            time.sleep(config['monitoring']['poll_seconds'])
        except KeyboardInterrupt:
            interrupted = True  # Keep reconciling within the original cleanup bound; never resume submission.
        except ValueError as error:
            if str(error) != 'review_workflow_busy': raise
            time.sleep(config['monitoring']['poll_seconds'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'status', 'advance', 'run', 'stop'))
    parser.add_argument('--config', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--apply', action='store_true'); parser.add_argument('--allow-live', action='store_true')
    args = parser.parse_args()
    try:
        config = read_json(args.config)
        if args.action == 'plan':
            plan, _ = validate_config(config); result = {'status': 'planned', 'contract_digest': contract(config), 'plan_digest': digest(plan), 'execution_authorized': False}
        elif args.action == 'status':
            result = read_json(args.output / 'state.json')
            if result['contract_digest'] != contract(config): raise ValueError('state_config_binding_mismatch')
        else:
            if not args.apply: raise ValueError('explicit_apply_required')
            if args.action == 'run': result = run(config, args.output, args.allow_live)
            else: result = advance(config, args.output, allow_live=args.allow_live, allow_submit=False, force_stop=args.action == 'stop')
        print(json.dumps(result, ensure_ascii=False))
        return 2 if args.action != 'status' and result.get('status') in ('incomplete', 'cleanup-required', 'blocked') else 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
        problem = str(error) if isinstance(error, ValueError) else type(error).__name__
        if problem == 'review_workflow_busy':
            print(json.dumps({'status':'busy','problem':problem})); return 2
        result = {'status': 'blocked', 'problem': problem}
        # A changed binding cannot be used to stop jobs. Preserve their exact identities for explicit reconciliation.
        path = args.output / 'state.json'
        if path.is_file():
            with locked(args.output):
                saved = read_json(path)
                pending = {k: v.get('uid') for k, v in saved['arms'].items() if v.get('submit_at') is not None and 'released_at' not in v}
                if pending:
                    saved.update(status='cleanup-required', binding_problem=problem)
                    atomic_json(path, saved); result.update(status='cleanup-required', owned_tasks=pending)
        print(json.dumps(result)); return 2


if __name__ == '__main__': raise SystemExit(main())
