"""Read-only planning checks for a control/treatment group; never dispatches jobs."""
import argparse
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import posixpath
import re

CONDITIONS = ('dataset_version', 'split_digest', 'initial_checkpoint_ref',
              'evaluator_ref', 'metric_definition', 'training_budget',
              'effective_batch', 'precision', 'randomness_pairing',
              'checkpoint_selection')
LIMITS = ('max_concurrent_tasks', 'max_gpus_per_arm', 'max_concurrent_gpus',
          'max_hourly_cost', 'max_gpu_hours', 'max_total_cost', 'max_wall_hours')
PATHS = ('attempt_root', 'workspace_root', 'output_root')


def resolved(value):
    if value is None or value == '':
        return False
    if isinstance(value, dict):
        return bool(value) and all(resolved(v) for v in value.values())
    if isinstance(value, list):
        return bool(value) and all(resolved(v) for v in value)
    return True


def number(value, zero=False, integer=False):
    if type(value) not in ((int,) if integer else (int, float)):
        return False
    try:
        return math.isfinite(value) and (value >= 0 if zero else value > 0)
    except OverflowError:
        return False


def absolute_path(value):
    if not isinstance(value, str) or not value.strip():
        return None
    path = value.replace('\\', '/')
    windows = bool(re.match(r'^[A-Za-z]:/', path) or path.startswith('//'))
    if not (path.startswith('/') or windows):
        return None
    path = posixpath.normpath(path).rstrip('/') or '/'
    return path.casefold() if windows else path


def overlap(left, right):
    return left == right or left.startswith(right.rstrip('/') + '/') or right.startswith(left.rstrip('/') + '/')


def preflight(plan):
    errors = []
    computed = {}
    fingerprint = None
    if not isinstance(plan, dict):
        errors.append('plan_must_be_object')
        plan = {}
    try:
        raw = json.dumps(plan, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
        fingerprint = 'sha256:' + hashlib.sha256(raw).hexdigest()
    except (ValueError, TypeError):
        errors.append('noncanonical_or_nonfinite_plan')
    if plan.get('schema') != 'sdm-comparison-group-planning/v1':
        errors.append('unsupported_schema')
    for field in ('group_id', 'project', 'question', 'changed_factor', 'project_policy_ref', 'adapter_capability_ref'):
        if not isinstance(plan.get(field), str) or not plan[field].strip():
            errors.append('unresolved:' + field)
    schedule = plan.get('schedule')
    if schedule not in ('sequential', 'parallel'):
        errors.append('schedule_must_be_sequential_or_parallel')
    if plan.get('partial_start_policy') not in ('stop-started-arm', 'finish-started-arm'):
        errors.append('partial_start_policy_required')
    if plan.get('arm_failure_policy') not in ('stop-peer', 'finish-peer'):
        errors.append('arm_failure_policy_required')
    limits = plan.get('limits')
    limits = limits if isinstance(limits, dict) else {}
    for key in LIMITS:
        if not number(limits.get(key), integer=key in LIMITS[:3]):
            errors.append('invalid_limit:' + key)
    for key in ('launch_window_hours', 'billing_margin_cost'):
        if not number(plan.get(key), zero=True):
            errors.append('unresolved:' + key)
    arms = plan.get('arms')
    if not isinstance(arms, list) or len(arms) != 2 or any(not isinstance(a, dict) for a in arms):
        errors.append('exactly_two_arms_required')
        arms = []
    arm_paths = []
    for index, arm in enumerate(arms):
        prefix = 'arm' + str(index) + ':'
        for key in ('id', 'job_name', 'source_ref', 'config_ref'):
            if not isinstance(arm.get(key), str) or not arm[key].strip():
                errors.append(prefix + 'unresolved:' + key)
        paths = [absolute_path(arm.get(key)) for key in PATHS]
        if any(path is None for path in paths):
            errors.append(prefix + 'absolute_owned_paths_required')
        arm_paths.append(paths)
        parameters = arm.get('parameters')
        if not isinstance(parameters, dict) or not parameters or not all(resolved(v) for v in parameters.values()):
            errors.append(prefix + 'resolved_parameters_required')
        conditions = arm.get('conditions')
        if not isinstance(conditions, dict) or any(not resolved(conditions.get(k)) for k in CONDITIONS):
            errors.append(prefix + 'comparison_conditions_required')
        alloc = arm.get('allocation')
        alloc = alloc if isinstance(alloc, dict) else {}
        for key in ('gpu_type', 'currency', 'quote_ref'):
            if not isinstance(alloc.get(key), str) or not alloc[key].strip():
                errors.append(prefix + 'unresolved_allocation:' + key)
        for key in ('gpus', 'hourly_cost', 'max_resource_hours'):
            if not number(alloc.get(key), zero=key == 'hourly_cost', integer=key == 'gpus'):
                errors.append(prefix + 'invalid_allocation:' + key)
    if arms:
        left, right = arms
        if left.get('id') == right.get('id') or left.get('job_name') == right.get('job_name'):
            errors.append('arm_and_job_identities_must_be_distinct')
        if all(all(p is not None for p in paths) for paths in arm_paths):
            if any(overlap(a, b) for a in arm_paths[0] for b in arm_paths[1]):
                errors.append('arm_write_roots_overlap')
        lp, rp = left.get('parameters'), right.get('parameters')
        if isinstance(lp, dict) and isinstance(rp, dict) and isinstance(plan.get('changed_factor'), str):
            if set(lp) != set(rp) or {k for k in lp if lp[k] != rp.get(k)} != {plan['changed_factor']}:
                errors.append('exactly_declared_factor_must_differ')
        if left.get('conditions') != right.get('conditions'):
            errors.append('comparison_conditions_mismatch')
        la, ra = left.get('allocation'), right.get('allocation')
        if isinstance(la, dict) and isinstance(ra, dict):
            if la.get('currency') != ra.get('currency'):
                errors.append('currency_mismatch')
            if la.get('gpu_type') != ra.get('gpu_type'):
                errors.append('gpu_type_mismatch_requires_reviewed_design')
    if not errors:
        allocations = [a['allocation'] for a in arms]
        prices = [Decimal(str(a['hourly_cost'])) for a in allocations]
        hours = [Decimal(str(a['max_resource_hours'])) for a in allocations]
        gpu_counts = [a['gpus'] for a in allocations]
        concurrent = schedule == 'parallel'
        totals = {
            'peak_tasks': 2 if concurrent else 1,
            'peak_gpus': sum(gpu_counts) if concurrent else max(gpu_counts),
            'peak_hourly_cost': sum(prices) if concurrent else max(prices),
            'gpu_hours_ceiling': sum(g * h for g, h in zip(gpu_counts, hours)),
            'cost_ceiling': sum(p * h for p, h in zip(prices, hours)) + Decimal(str(plan['billing_margin_cost'])),
            'wall_hours_ceiling': (max(hours) if concurrent else sum(hours)) + Decimal(str(plan['launch_window_hours'])),
        }
        checks = {
            'max_concurrent_tasks': totals['peak_tasks'], 'max_gpus_per_arm': max(gpu_counts),
            'max_concurrent_gpus': totals['peak_gpus'], 'max_hourly_cost': totals['peak_hourly_cost'],
            'max_gpu_hours': totals['gpu_hours_ceiling'], 'max_total_cost': totals['cost_ceiling'],
            'max_wall_hours': totals['wall_hours_ceiling'],
        }
        for limit, actual in checks.items():
            if actual > Decimal(str(limits[limit])):
                errors.append('group_limit_exceeded:' + limit)
        computed = {k: (float(v) if math.isfinite(v) else str(v)) if isinstance(v, Decimal) else v for k, v in totals.items()}
        computed['currency'] = allocations[0]['currency']
    return {'status': 'blocked' if errors else 'pass', 'plan_digest': fingerprint,
            'scope': 'planning-only', 'dispatch_permitted': False,
            'errors': errors, 'computed': computed,
            'remaining_checks': ['user_confirmation_and_native_config_binding',
                                 'live_aggregate_allocations_and_adapter_capability',
                                 'actual_input_path_and_gpu_isolation',
                                 'normal_S12_S13_deadlines_and_per_arm_cleanup']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = preflight(json.loads(args.plan.read_bytes()))
    except (OSError, ValueError) as error:
        report = {'status': 'blocked', 'scope': 'planning-only', 'dispatch_permitted': False,
                  'errors': ['plan_unreadable_or_invalid_json:' + type(error).__name__]}
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0 if report['status'] == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
