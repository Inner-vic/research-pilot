"""Read-only resource-envelope assessment. Never authorizes or dispatches work."""
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path


def request_digest(request):
    raw = json.dumps(request, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def number(value, *, integer=False, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('finite_resource_number_required')
    if value < 0 or (not zero and value == 0) or (integer and int(value) != value):
        raise ValueError('invalid_resource_number')
    return value


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp_string_required')
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('timezone_required')
    return result


def summarize(request):
    for key in ('provider', 'namespace', 'cluster', 'queue_id', 'scheduling_mode'):
        if not isinstance(request.get(key), str) or not request[key].strip():
            raise ValueError('missing_resource_identity:' + key)
    roles = request.get('roles')
    if not isinstance(roles, list) or not roles:
        raise ValueError('resolved_roles_required')
    totals = {'cpu_cores': 0, 'memory_gib': 0, 'instances': 0, 'gpus_by_type': {}}
    seen = set()
    for role in roles:
        name = role.get('name')
        if not isinstance(name, str) or not name or name in seen:
            raise ValueError('unique_role_name_required')
        seen.add(name)
        replicas = number(role['instances'], integer=True)
        cpu = number(role['cpu_cores_per_instance'])
        memory = number(role['memory_gib_per_instance'])
        gpu = number(role['gpus_per_instance'], integer=True, zero=True)
        if gpu and not role.get('gpu_type'):
            raise ValueError('exact_gpu_type_required')
        totals['instances'] += replicas
        totals['cpu_cores'] += replicas * cpu
        totals['memory_gib'] += replicas * memory
        if gpu:
            kind = role['gpu_type']
            totals['gpus_by_type'][kind] = totals['gpus_by_type'].get(kind, 0) + replicas * gpu
    return totals


def assess(request, observations, *, now=None, max_age_seconds=300):
    """Input snapshots must already be authenticated through existing provider rules.

    Positive aggregate headroom never establishes same-node or joint placement.
    A matching request digest binds CPU, memory, exact GPU types and all additional
    placement/network/storage fields included by the caller in the frozen envelope.
    """
    number(max_age_seconds)
    current = now if now is not None else dt.datetime.now(dt.timezone.utc)
    if current.utcoffset() is None:
        raise ValueError('timezone_required')
    totals = summarize(request)
    digest = request_digest(request)
    required = {'catalog', 'queue_headroom', 'space_headroom', 'joint_placement'}
    grouped = {scope: [] for scope in required | {'effective_headroom'}}
    ignored = []
    for record in observations:
        scope = record.get('scope')
        reason = None
        if scope not in grouped:
            reason = 'unsupported_scope'
        elif record.get('request_digest') != digest:
            reason = 'different_resource_envelope'
        elif record.get('status') != 'observed' or not record.get('source_ref'):
            reason = 'missing_observation_or_source'
        else:
            try:
                observed = timestamp(record['observed_at'])
                expiry = timestamp(record['valid_until'])
                if not observed <= current < expiry or (current - observed).total_seconds() > max_age_seconds:
                    reason = 'stale_or_future_observation'
            except (KeyError, TypeError, ValueError):
                reason = 'invalid_observation_time'
        if reason:
            ignored.append({'scope': scope, 'reason': reason})
        else:
            grouped[scope].append(record)

    checks = []
    if grouped['effective_headroom']:
        if grouped['queue_headroom'] or grouped['space_headroom']:
            checks.append({'scope': 'effective_headroom', 'status': 'unknown',
                           'reason': 'overlapping_headroom_sources_require_reconciliation'})
        else:
            required -= {'queue_headroom', 'space_headroom'}
            required.add('effective_headroom')
    for scope in sorted(required):
        records = grouped[scope]
        # Multiple snapshots require reconciliation by the caller, not favorable selection.
        if len(records) != 1:
            checks.append({'scope': scope, 'status': 'unknown',
                           'reason': 'conflicting_observations' if records else 'missing_fresh_evidence'})
            continue
        record = records[0]
        try:
            if scope in ('catalog', 'joint_placement'):
                field = 'supported' if scope == 'catalog' else 'fits_full_request'
                actual = record.get(field)
                if not isinstance(actual, bool):
                    raise ValueError('explicit_boolean_required')
                checks.append({'scope': scope, 'status': 'fits' if actual else 'does-not-fit',
                               'source_ref': record['source_ref']})
            else:
                available = record['available']
                comparisons = [(key, totals[key], number(available[key], zero=True))
                               for key in ('cpu_cores', 'memory_gib')]
                for kind, count in totals['gpus_by_type'].items():
                    # Missing GPU type is unknown, never silently treated as zero or another type.
                    comparisons.append(('gpu:' + kind, count,
                                        number(available['gpus_by_type'][kind], integer=True, zero=True)))
                for dimension, needed, remaining in comparisons:
                    checks.append({'scope': scope, 'dimension': dimension,
                                   'status': 'fits' if needed <= remaining else 'does-not-fit',
                                   'requested': needed, 'available': remaining, 'source_ref': record['source_ref']})
        except (KeyError, TypeError, ValueError):
            checks.append({'scope': scope, 'status': 'unknown', 'reason': 'incomplete_or_invalid_dimensions'})

    statuses = {item['status'] for item in checks}
    status = 'known-constraint' if 'does-not-fit' in statuses else (
        'unknown' if 'unknown' in statuses else 'fits-observed-snapshot')
    return {'schema': 'sdm-resource-feasibility/v1', 'assessment': status,
            'request_digest': digest, 'totals': totals, 'checks': checks, 'ignored': ignored,
            'execution_authorized': False, 'reservation_verified': False,
            'platform_mutations': 0, 'historical_root_cause_established': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-evidence-age-seconds', type=float, default=300)
    args = parser.parse_args()
    data = json.loads(args.input.read_bytes())
    result = assess(data['request'], data.get('observations', []), max_age_seconds=args.max_evidence_age_seconds)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
