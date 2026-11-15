"""Read-only normalization of observed LPAI v1 response shapes; no platform effects."""
import datetime as dt
import uuid

from comparison_reporting import load_ref
from paired_execution import number
from round_reporting import digest


def data(response):
    if not isinstance(response, dict) or response.get('status') != 'ok' or 'data' not in response:
        raise ValueError('successful_native_response_required')
    return response['data']


def job_matches(actual, base, uid, policy='exact'):
    if policy == 'exact': return actual == base
    if policy != 'lpai-uid-suffix': raise ValueError('declared_job_name_policy_required')
    try: canonical = str(uuid.UUID(uid))
    except (ValueError, TypeError, AttributeError): return False
    return uid == canonical and actual == base + '-' + uid[:8]


def roles(response):
    native = data(response)
    items = native.get('data', {}).get('lpai_task', {}).get('roles')
    if not isinstance(items, list) or not items: raise ValueError('native_task_roles_required')
    signature = []
    for role in items:
        if not isinstance(role, dict): raise ValueError('native_role_object_required')
        resource = role.get('resource', {})
        instances = role.get('replica'); gpus = resource.get('Gpu'); packages = resource.get('replica')
        if (type(instances) is not int or instances < 1 or type(gpus) is not int or gpus < 0
            or type(packages) is not int or packages < 1 or type(resource.get('Id')) not in (str, int)
            or not isinstance(role.get('role'), str) or not role['role']):
            raise ValueError('canonical_role_allocation_required')
        # Observed Gpu is the per-instance total. resource.replica is package count, not another multiplier.
        signature.append({'role': role['role'], 'instances': instances, 'instance_gpus': gpus,
                          'package_count': packages, 'resource_id': resource['Id'], 'gpu_type': resource.get('GpuType')})
    if len({r['role'] for r in signature}) != len(signature): raise ValueError('distinct_role_names_required')
    return {'gpus': sum(r['instances'] * r['instance_gpus'] for r in signature),
            'signature': sorted(signature, key=lambda r: r['role']), 'basis': 'native-role-config-not-utilization'}


def priced_allocation(response, quote_ref, now, currency):
    allocation = roles(response); quote, ref = load_ref(quote_ref); native = data(response)
    if (quote.get('schema') != 'sdm-lpai-role-quote/v1' or quote.get('status') != 'verified'
        or quote.get('cluster') != native.get('cluster') or quote.get('queue_code') != native.get('queue_code')
        or quote.get('namespace') != native.get('namespace') or quote.get('currency') != currency
        or quote.get('role_signature_digest') != digest(allocation['signature'])
        or not number(quote.get('hourly_cost')) or not number(quote.get('observed_at'))
        or not number(quote.get('valid_until')) or not quote['observed_at'] <= now < quote['valid_until']
        or not quote.get('source_ref')):
        raise ValueError('fresh_exact_role_quote_required')
    return {**allocation, 'hourly_cost': quote['hourly_cost'], 'quote_ref': ref,
            'cost_basis': 'bound-role-quote', 'billing_is_measured': False}


def release_readback(task_response, pod_response, uid, namespace, owner):
    native = data(task_response); pods = data(pod_response)
    if any(native.get(k) != v for k, v in {'uid': uid, 'namespace': namespace, 'owner': owner}.items()):
        raise ValueError('release_task_identity_mismatch')
    if not isinstance(pods, list): raise ValueError('explicit_native_pod_list_required')
    for p in pods:
        if not isinstance(p, dict) or p.get('task_uid') != uid or p.get('namespace') != namespace:
            raise ValueError('release_pod_identity_mismatch')
    compute = [p for p in pods if p.get('step') == 'task']
    deleted = bool(compute) and all(p.get('status') == 'deleted' and p.get('finish_timestamp', {}).get('time') for p in compute)
    terminal = native.get('status') in ('succeeded', 'failed', 'stopped')
    return {'released': bool(terminal and deleted), 'uid': uid, 'compute_pods': len(compute),
            'deleted_compute_pods': sum(p.get('status') == 'deleted' for p in compute),
            'basis': 'fresh-task-and-all-compute-pods-deleted', 'task_terminal': terminal,
            'unknown_empty_pods_are_not_release_proof': not bool(compute)}


def observed_field_map():
    """Get/list/component paths observed on 2026-10-14. Start path still needs explicit verification."""
    task = {k: ['data', field] for k, field in {
        'uid': 'uid', 'name': 'name', 'namespace': 'namespace', 'owner': 'owner',
        'component_uid': 'project_uid', 'request_marker': 'describe', 'status': 'status'}.items()}
    return {'component': {'uid': ['data', 'uid'], 'namespace': ['data', 'namespace']},
            'task': task, 'list': {'items': ['data', 'items'], 'total': ['data', 'total']}}
