"""Guarded source-backed v2 notebook creation through official SDK transport.

No change to the existing manager runtime or its frozen training plans. Only
the narrowly qualified own-account CPU notebook creation contract is exposed.
"""
import contextlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
from lpai_manager import digest_file, require, runtime_digest
from lpai_manager_backend import OfficialBackend, payload
from external_operations import sha
from review_storage import atomic_json, read_json
from submission_guard import Guard


def validate_body(config, body):
    require(set(body) == {'metadata', 'spec'}, 'notebook_metadata_and_spec_required')
    meta, spec = body['metadata'], body['spec']
    require(set(meta) == {'name', 'namespace', 'labels', 'annotations'}, 'notebook_clean_create_metadata_required')
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{0,29}', meta.get('name', '')), 'notebook_name_invalid')
    require(meta['namespace'] == config['namespace'], 'notebook_namespace_mismatch')
    require(meta['labels'] == {'lpai.org/kind': 'notebook', 'lpai.org/visible': 'personal'}, 'personal_notebook_required')
    require(set(meta['annotations']) == {'lpai.org/desc', 'lpai.org/gems-project'} and all(meta['annotations'].values()), 'notebook_description_and_project_required')
    require(set(spec) == {'image', 'sshSecretName', 'resources', 'clusterName', 'autoCulling', 'env', 'dataVols', 'accesses'}, 'complete_notebook_spec_required')
    r = spec['resources']
    require(r['cpu'] == 1 and r['gpu'] == 0 and r['memory'] == 4 and r['resourceComboId'] == '5' and r['resourceComboNum'] == 1,
            'qualified_one_CPU_four_GiB_zero_GPU_package_required')
    require(r['request'] == r['limit'] == {'cpu': '1', 'memory': '4Gi', 'nvidia.com/gpu': '0'}, 'CPU_resource_requests_limits_mismatch')
    require(bool(spec['image']) and bool(spec['clusterName']) and bool(r['resourceQueue']), 'notebook_image_cluster_queue_required')
    require(spec['env'] == {} and spec['dataVols'] == [], 'hello_notebook_must_not_mount_assets_or_inject_environment')
    require(spec['autoCulling'] == {'enable': 'true', 'windowMinutes': '60'}, 'notebook_idle_culling_required')
    accesses = spec['accesses']
    require(sorted(x['name'] for x in accesses) == ['jupyter-lab', 'ssh'], 'qualified_notebook_services_required')
    ssh = next(x for x in accesses if x['name'] == 'ssh')
    require(ssh['pubkeyName'] == spec['sshSecretName'] and bool(spec['sshSecretName']), 'existing_SSH_key_reference_required')


def read_or_absent(config, name):
    backend = OfficialBackend(config, worker_mode=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        client, path = backend.notebook_api(name)
        try:
            obj = payload(client.get(path, timeout=25))
        except Exception as exc:
            # Only a definite HTTP404 establishes absence. Auth/network/error
            # outcomes never authorize a POST or a renamed replacement.
            if "Invalid status code '404' from " in str(exc):
                return None
            raise ValueError('official_notebook_GET_failed_absence_not_proven') from None
    meta = obj.get('metadata', {})
    require(meta.get('name') == name and meta.get('namespace') == config['namespace'] and meta.get('uid'), 'notebook_identity_mismatch')
    require(meta.get('annotations', {}).get('lpai.org/owner') == config['owner'], 'notebook_owner_mismatch')
    return obj


def check_capacity(config, body):
    """Official UI limits personal spaces to five per owner and cluster."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        client, path = OfficialBackend(config, worker_mode=True).notebook_api(body['metadata']['name'])
        rows = payload(client.get(path.rsplit('/', 1)[0], timeout=25))
    while isinstance(rows, dict):
        key = next((key for key in ('items', 'result', 'data') if key in rows), None)
        require(key is not None, 'notebook_capacity_list_unrecognized')
        rows = rows[key]
    require(isinstance(rows, list), 'notebook_capacity_list_required')
    count = sum(obj.get('metadata', {}).get('annotations', {}).get('lpai.org/owner') == config['owner']
                and obj.get('spec', {}).get('clusterName') == body['spec']['clusterName'] for obj in rows)
    require(count < 5, 'notebook_capacity_five_reached_no_POST_performed')
    return count


def verify(config, body, obj):
    validate_body(config, body)
    require(obj is not None, 'created_notebook_not_observed_reconcile_only')
    meta, spec = obj['metadata'], obj['spec']
    require(meta['name'] == body['metadata']['name'] and meta['namespace'] == config['namespace']
            and meta['annotations']['lpai.org/owner'] == config['owner']
            and meta['annotations']['lpai.org/notebookVersion'] == 'v2', 'created_notebook_identity_owner_or_version_mismatch')
    for key in ('clusterName', 'sshSecretName'):
        require(spec.get(key) == body['spec'][key], 'created_notebook_spec_mismatch:' + key)
    require(spec['image'] == body['spec']['image'] or meta['annotations'].get('lpai.org/baseImage') == body['spec']['image'], 'created_notebook_base_image_mismatch')
    for key, value in body['spec']['resources'].items():
        require(spec['resources'].get(key) == value, 'created_notebook_resource_mismatch:' + key)
    require(sorted(x['name'] for x in spec['accesses']) == ['jupyter-lab', 'ssh'], 'created_notebook_services_mismatch')
    require(not spec.get('dataVols') and not spec.get('env'), 'unexpected_notebook_assets_or_environment')
    require(spec.get('autoCulling') == body['spec']['autoCulling'], 'created_notebook_culling_mismatch')


def run(config_path, plan_path, authorization_path, operation):
    config = read_json(config_path); plan_path = Path(plan_path); plan = read_json(plan_path)
    require(plan['schema'] == 'lpai-notebook-create/v1' and plan['config_sha256'] == digest_file(config_path)
            and plan['tool_sha256'] == digest_file(__file__) and plan['manager_runtime_digest'] == runtime_digest(), 'frozen_notebook_plan_or_tool_changed')
    body = plan['body']; validate_body(config, body)
    guard = Guard({'provider': 'lpai', 'namespace': config['namespace'], 'owner': config['owner'],
                   'request_key': plan['request_key'], 'component_uid': 'notebook-create:' + body['metadata']['name'],
                   'configuration_digest': sha(plan), 'submission_registry': config['submission_registry']}, plan_path)
    with guard.lock():
        guard.load()
        if guard.state['phase'] == 'completed':
            return {'status': 'already-completed', 'uid': guard.state['uid'], 'new_platform_writes': 0}
        if operation == 'apply':
            auth = read_json(authorization_path)
            require(auth.get('direct_user_confirmation') is True and auth.get('plan_sha256') == digest_file(plan_path)
                    and auth.get('source_ref'), 'bound_user_authorization_required')
            require(guard.state['phase'] == 'new', 'uncertain_notebook_create_reconcile_only')
            require(read_or_absent(config, body['metadata']['name']) is None, 'notebook_name_already_exists')
            check_capacity(config, body)
            guard.before_submit(plan_path)
            guard.state['authorization'] = {'path': str(authorization_path), 'sha256': digest_file(authorization_path)}; guard.save()
            try:
                # Bound the SDK POST, whose public post() does not forward a
                # timeout argument. A timeout keeps the intent occupied.
                result = subprocess.run([config['sdk_python'], '-X', 'utf8', '-B', __file__, '--worker', str(config_path), str(plan_path)],
                                        capture_output=True, encoding='utf-8', timeout=60,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                try: receipt = json.loads(result.stdout)
                except ValueError: receipt = {'post_returned': False, 'worker_unstructured_failure': True}
                receipt_path = Path(config['state_root']) / 'notebook-POST-receipt.json'
                atomic_json(receipt_path, receipt)
                guard.state['POST_receipt'] = {'path': str(receipt_path), 'sha256': digest_file(receipt_path)}; guard.save()
                require(result.returncode == 0 and receipt.get('post_returned') is True,
                        'notebook_POST_outcome_unverified_reconcile_only')
            except Exception:
                raise ValueError('notebook_POST_outcome_unverified_reconcile_only') from None
        else:
            require(operation == 'reconcile' and guard.state['phase'] == 'intent', 'existing_notebook_create_intent_required')
            auth = guard.state.get('authorization', {})
            require(auth.get('path') and auth['sha256'] == digest_file(auth['path']), 'original_notebook_authorization_changed')
        obj = read_or_absent(config, body['metadata']['name']); verify(config, body, obj)
        guard.completed(obj['metadata']['uid'])
        out = Path(config['state_root']) / 'created-notebook.private.json'; atomic_json(out, obj)
        return {'status': 'verified', 'name': obj['metadata']['name'], 'uid': obj['metadata']['uid'],
                'owner': config['owner'], 'phase': obj['status']['phase'], 'readback_path': str(out),
                'route': 'official-notebook-API-via-SDK-RestClient', 'feishu_confirmation': False}


if __name__ == '__main__':
    if sys.argv[1] == '--worker':
        config = read_json(sys.argv[2]); plan = read_json(sys.argv[3]); validate_body(config, plan['body'])
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                backend = OfficialBackend(config, worker_mode=True); client, path = backend.notebook_api(plan['body']['metadata']['name'])
                payload(client.post(path.rsplit('/', 1)[0], plan['body']))
            receipt = {'post_returned': True}
        except Exception as exc:
            match = re.search(r"Invalid status code '([0-9]{3})' from ", str(exc))
            receipt = {'post_returned': False, 'error_type': type(exc).__name__,
                       'http_status': int(match[1]) if match else None,
                       'notebook_capacity_rejection': 'notebook-num-limit' in str(exc)}
        print(json.dumps(receipt))
    else:
        # config plan authorization operation output
        result = run(*sys.argv[1:5]); atomic_json(sys.argv[5], result); print(json.dumps(result))
