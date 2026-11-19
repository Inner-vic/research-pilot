"""LPAI management supplement: read, exact native-data planning, guarded execution.

Run with the existing official SDK Python interpreter. No platform writes by
default. Existing SDM frozen tasks are never migrated by this tool.
"""
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
import sys
import time

from external_operations import sha
from review_storage import atomic_json, read_json
from submission_guard import Guard, PendingApproval
from lpai_manager_backend import OfficialBackend

ACTIONS = {'component.create', 'task.submit', 'task.stop', 'devspace.start', 'devspace.stop'}
STABLE_FIELDS = ('uid', 'namespace', 'owner', 'name', 'cluster', 'queue_code', 'framework_type',
                 'gems_project', 'max_retry', 'elastic_train', 'wait_roles', 'data')
DATA_KEYS = ('lpai_git', 'lpai_envs', 'lpai_task', 'lpai_input_dataset', 'lpai_input_pvc',
             'lpai_model_download', 'lpai_model_register', 'lpai_output_pvc', 'lpai_skip')


def digest_file(path):
    return 'sha256:' + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runtime_digest():
    root = Path(__file__).resolve().parent
    return sha({name: digest_file(root / name) for name in ('lpai_manager.py', 'lpai_manager_backend.py',
               'lpai_sdk_worker.py', 'research_execution.py', 'submission_guard.py', 'external_operations.py', 'review_storage.py')})


def require(value, reason):
    if not value:
        raise ValueError(reason)


def authorized_plan(plan_path, authorization_path, config):
    """Shared original Manager authorization checks; no platform or Guard effect."""
    plan = read_json(plan_path)
    require(plan.get('schema') == 'lpai-manager-plan/v1' and plan.get('manager_digest') == sha(config), 'plan_or_manager_configuration_changed')
    require(plan.get('runtime_digest') == runtime_digest(), 'manager_runtime_changed_since_plan')
    auth = read_json(authorization_path)
    require(auth.get('schema') == 'lpai-manager-authorization/v1' and auth.get('plan_sha256') == digest_file(plan_path)
            and auth.get('direct_user_confirmation') is True and bool(auth.get('source_ref')), 'bound_user_authorization_required')
    return plan, auth


def check_readonly_runtime(plan_path, runtime_repair=None):
    """Bind a read-only recovery runtime without changing the original plan."""
    plan = read_json(plan_path)
    current = runtime_digest()
    if runtime_repair is None:
        require(plan.get('runtime_digest') == current, 'manager_runtime_changed_since_plan')
        return
    proof = read_json(runtime_repair)
    require(proof.get('schema') == 'lpai-manager-readonly-runtime-repair/v1'
            and proof.get('read_only') is True
            and proof.get('original_plan_sha256') == digest_file(plan_path)
            and proof.get('from_runtime') == plan.get('runtime_digest')
            and proof.get('to_runtime') == current
            and bool(proof.get('source_ref')), 'bound_readonly_runtime_repair_required')


def output_target(config, output, inputs):
    target = Path(output).resolve()
    require(target not in {Path(p).resolve() for p in inputs if p}, 'result_must_not_overwrite_input')
    for key in ('state_root', 'submission_registry'):
        root = Path(config[key]).resolve()
        require(not target.is_relative_to(root), 'result_must_not_overwrite_internal_state')
    return target


def native_data(data):
    require(isinstance(data, dict) and isinstance(data.get('lpai_task'), dict), 'native_task_data_required')
    require(not set(data) - set(DATA_KEYS), 'unknown_native_data_fields')
    task = data['lpai_task']
    require(isinstance(task.get('cmd'), str) and task['cmd'].strip(), 'native_command_required')
    roles = task.get('roles')
    require(isinstance(roles, list) and roles, 'native_roles_required')
    names = set()
    for role in roles:
        require(isinstance(role.get('role'), str) and role['role'] not in names, 'unique_native_roles_required')
        names.add(role['role'])
        require(type(role.get('replica')) is int and role['replica'] > 0, 'fixed_positive_role_replicas_required')
        resource = role.get('resource', {})
        require(type(resource.get('Id')) is int and type(resource.get('Gpu')) is int and resource['Gpu'] >= 0,
                'native_resource_identity_required')
        require(isinstance(role.get('cmd'), str) and role['cmd'].strip(), 'role_command_required')
        require(bool(role.get('image_info', {}).get('image')), 'role_image_required')
    envs = data.get('lpai_envs', [])
    require(isinstance(envs, list), 'native_env_list_required')
    keys = [item.get('key') for item in envs]
    require(all(isinstance(k, str) and k for k in keys) and len(keys) == len(set(keys)), 'unique_env_keys_required')
    require(all(isinstance(item.get('value'), str) for item in envs), 'native_environment_string_values_required')
    # Actual Task API readback collapses $$ to $, corrupting arbitrary base85
    # payloads. Reject known non-faithful values before any create/submit.
    require(all('$$' not in item['value'] for item in envs), 'provider_rewrites_double_dollar_environment_value')
    return data


def subset_matches(wanted, actual):
    """Provider may add fields, but no declared field or list entry can disappear."""
    if isinstance(wanted, dict):
        return isinstance(actual, dict) and all(k in actual and subset_matches(v, actual[k]) for k, v in wanted.items())
    if isinstance(wanted, list):
        return isinstance(actual, list) and len(wanted) == len(actual) and all(subset_matches(a, b) for a, b in zip(wanted, actual))
    return type(wanted) is type(actual) and wanted == actual


def task_name_matches(requested, actual, uid):
    # Observed task GET names include the task's own UUID prefix. Accept only
    # that exact provider suffix, never an arbitrary name prefix match.
    if requested == actual:
        return True
    return bool(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', uid or '')
                and actual == requested + '-' + uid[:8])


def native_config_matches(wanted, actual):
    """Account for observed provider metadata normalization, never commands/resources.

    Keep the raw readback separately. Canonical uppercase resource fields remain
    authoritative; lower-case provider mirrors must agree with them on readback.
    """
    if not isinstance(wanted, dict) or not isinstance(actual, dict):
        return False
    def canonical(value, provider):
        value = copy.deepcopy(value)
        for key in ('lpai_git', 'lpai_model_download', 'lpai_model_register', 'lpai_envs',
                    'lpai_input_dataset', 'lpai_input_pvc', 'lpai_output_pvc'):
            if key in value and value[key] is None: value[key] = []
        task = value.get('lpai_task') or {}
        for role in task.get('roles') or []:
            if role.get('use_role_cmd') is None: role['use_role_cmd'] = False
            role.pop('resourceInfo', None)  # Provider-generated human-readable resource summary.
            resource = role.get('resource') or {}
            for alias, original in (('cpu', 'Cpu'), ('memory', 'Mem'), ('gpu', 'Gpu'), ('gpu_type', 'GpuType')):
                if provider and alias in resource and original in resource:
                    matches = resource[alias] == resource[original]
                    # GPU task API's display alias omits the Kubernetes
                    # nvidia.com/ resource namespace. Keep the authoritative
                    # GpuType exact and accept only this observed prefix rule.
                    if alias == 'gpu_type' and isinstance(resource[original], str):
                        matches = matches or (resource[original].startswith('nvidia.com/')
                                              and resource[alias] == resource[original].removeprefix('nvidia.com/'))
                    require(matches, 'provider_resource_alias_inconsistent')
                resource.pop(alias, None)
            for display in ('Name', 'PackageType', 'LimitSum'):
                resource.pop(display, None)
        return value
    try:
        return subset_matches(canonical(wanted, False), canonical(actual, True))
    except ValueError:
        return False


def sdk_component_config_matches(wanted, actual):
    # The public builder copies package catalog metadata that the component
    # storage schema does not retain. Only SDK-created components get this
    # projection; full-native task configurations still compare every field.
    wanted, actual = copy.deepcopy(wanted), copy.deepcopy(actual)
    for value in (wanted, actual):
        for role in (value.get('lpai_task') or {}).get('roles') or []:
            resource = role.get('resource') or {}
            for key in ('GpuCore', 'NodeSelected', 'CpuLimit', 'MemLimit', 'GpuLimit', 'gpuAbbr', 'gpuResourceName',
                        'ComputePackageNotebookShare', 'resourceName'):
                resource.pop(key, None)
            # Component GET emits zero-valued lowercase compatibility slots;
            # its authoritative package values are the uppercase SDK fields.
            for key in ('cpu', 'memory', 'gpu', 'gpu_type'):
                resource.pop(key, None)
            for key in ('Annotation', 'Label'):
                values = resource.get(key)
                if isinstance(values, dict) and values.get('lpai.org/gpu-type', 'missing') is None:
                    values['lpai.org/gpu-type'] = ''
    return native_config_matches(wanted, actual)


def summary(obj, kind):
    if kind == 'devspace':
        meta, spec = obj['metadata'], obj.get('spec', {})
        return {'kind': kind, 'uid': meta['uid'], 'name': meta['name'], 'namespace': meta['namespace'],
                'owner': meta.get('annotations', {}).get('lpai.org/owner'),
                'status': obj.get('status', {}).get('phase'), 'cluster': spec.get('clusterName'),
                'resources': spec.get('resources', {}), 'image': spec.get('image')}
    raw_data = obj.get('data')
    data = raw_data if isinstance(raw_data, dict) else {}
    roles = (data.get('lpai_task') or {}).get('roles') or []
    envs = data.get('lpai_envs')
    counts_known = bool(roles) and all(type(r.get('replica')) is int and
                                      type((r.get('resource') or {}).get('Gpu')) is int for r in roles)
    return {'kind': kind, **{k: obj.get(k) for k in STABLE_FIELDS if k != 'data'},
            'status': obj.get('status'), 'last_task_uid': obj.get('last_task_uid'),
            'data_digest': sha(raw_data), 'env_keys': [v['key'] for v in envs] if isinstance(envs, list) else None,
            'gpus': sum(r['replica'] * r['resource']['Gpu'] for r in roles) if counts_known else None,
            'role_count': len(roles) if isinstance(raw_data, dict) else None}


class ManagerCore:
    def __init__(self, config, backend=None):
        for field in ('namespace', 'owner', 'state_root', 'submission_registry'):
            require(isinstance(config.get(field), str) and config[field].strip(), 'manager_scope_required:' + field)
        require(config.get('schema') == 'lpai-manager/v1', 'manager_schema_required')
        require(config.get('component_transport', 'official-sdk') in ('official-cli', 'official-sdk'), 'unknown_component_transport')
        require(config.get('devspace_transport', 'official-cli') in ('official-cli', 'official-api'), 'unknown_devspace_transport')
        self.config = config
        self.backend = backend or OfficialBackend(config)

    def checked(self, kind, uid=None, name=None):
        require(kind in ('component', 'task', 'devspace'), 'unknown_resource_kind')
        require(bool(name) if kind == 'devspace' else bool(uid), 'resource_identity_required')
        obj = self.backend.read(kind, uid=uid, name=name)
        require(isinstance(obj, dict), 'resource_readback_required')
        if kind == 'devspace':
            meta = obj.get('metadata', {})
            require(meta.get('name') == name and bool(meta.get('uid')), 'devspace_identity_mismatch')
            require(uid is None or meta['uid'] == uid, 'devspace_uid_changed')
            ns = meta.get('namespace')
            owner = meta.get('annotations', {}).get('lpai.org/owner')
        else:
            require(obj.get('uid') == uid, 'resource_uid_mismatch')
            ns, owner = obj.get('namespace'), obj.get('owner')
        require(ns == self.config['namespace'] and owner == self.config['owner'], 'resource_owner_or_namespace_mismatch')
        return obj

    def legacy_pending(self, request):
        if request['action'] not in ('component.create', 'task.submit'):
            return
        for directory in self.config.get('legacy_pending_cases', []):
            root = Path(directory)
            if not (root / 'create-intent.json').exists():
                continue
            response = read_json(root / 'create-response.private.json')
            reply = json.loads(response['stdout'])
            body = read_json(root / 'create-body.json')
            require(not (reply.get('status') == 'pending_approval'
                         and body.get('namespace') == self.config['namespace']
                         and (request.get('name') == body.get('name')
                              or sha(request.get('data', request.get('body', {}).get('data'))) == sha(body.get('data')))),
                    'legacy_pending_request_requires_resolution')

    def plan(self, request):
        if self.config.get('component_transport') == 'official-cli' or self.config.get('platform_requirements'):
            from required_skills import inspect
            require(inspect(self.config)['status'] == 'required-skills-available', 'official_lpai_skill_required')
        request = copy.deepcopy(request)
        action = request.get('action')
        require(action in ACTIONS, 'unsupported_operation_or_unverified_official_interface')
        fields = {'action', 'request_key'} | {
            'component.create': {'name', 'body', 'sdk_config', 'transport'},
            'task.submit': {'name', 'uid', 'cluster', 'data', 'task_info', 'component_settings'},
            'task.stop': {'uid'}, 'devspace.start': {'name'}, 'devspace.stop': {'name'}}[action]
        require(not set(request) - fields, 'unknown_operation_request_fields')
        require(isinstance(request.get('request_key'), str) and request['request_key'].strip(), 'logical_request_key_required')
        if action=='task.submit' and self.config.get('component_transport','official-sdk')=='official-sdk':
            self.backend.validate_task_data(request.get('data',{}))
        self.legacy_pending(request)
        baseline = None
        if action == 'component.create':
            transport = request.get('transport', 'official-sdk' if 'sdk_config' in request else 'official-cli')
            require(transport in ('official-sdk', 'official-cli'), 'unknown_component_creation_transport')
            if transport == 'official-sdk':
                require(self.config.get('component_transport') != 'official-cli', 'explicit_cli_mode_forbids_sdk_fallback')
                cfg = request.get('sdk_config', {})
                require(isinstance(cfg, dict) and not set(cfg) - {
                    'name', 'describe', 'cluster', 'queue_code', 'image', 'cmd', 'framework', 'gems_project',
                    'task_role_specs', 'envs', 'retry', 'wait', 'code', 'input_model', 'input_data_volume',
                    'input_dataset', 'register_model', 'output_data_volume'}, 'unsupported_sdk_component_configuration_fields')
                for field in ('name', 'describe', 'cluster', 'queue_code', 'image', 'cmd', 'framework', 'gems_project'):
                    require(isinstance(cfg.get(field), str) and cfg[field].strip(), 'sdk_component_configuration_required:' + field)
                require(cfg['name'] == request.get('name') and isinstance(cfg.get('task_role_specs'), list)
                        and cfg['task_role_specs'], 'sdk_component_name_and_roles_required')
                require(type(cfg.get('retry', 0)) is int and cfg.get('retry', 0) >= 0
                        and type(cfg.get('wait', False)) is bool, 'sdk_retry_and_wait_types_required')
                for role in cfg['task_role_specs']:
                    require(isinstance(role, dict) and not set(role) - {'rolename', 'replica', 'resource'}
                            and isinstance(role.get('replica'), str) and re.fullmatch(r'[1-9][0-9]*', role['replica']),
                            'sdk_fixed_role_configuration_required')
                    resource = role.get('resource', {})
                    require(isinstance(resource, dict) and not set(resource) - {'id', 'replica'}
                            and type(resource.get('id')) is int and resource['id'] > 0
                            and type(resource.get('replica', 1)) is int and resource.get('replica', 1) > 0,
                            'sdk_resource_package_configuration_required')
                for key in ('code', 'input_model', 'input_data_volume', 'input_dataset', 'register_model', 'output_data_volume'):
                    require(isinstance(cfg.get(key, []), list), 'sdk_asset_list_required')
                require(all(isinstance(code, dict) and str(code.get('type', '')).upper() == 'LPAI'
                            for code in cfg.get('code', [])), 'sdk_would_drop_unsupported_code_source')
                require(type(cfg.get('envs', {})) is dict, 'sdk_environment_dictionary_required')
                require(all(str(k).capitalize() == str(k) for k in cfg.get('envs', {})), 'installed_sdk_changes_environment_keys')
                compiled = self.backend.compile_component(cfg)
                require('body' not in request or request['body'] == compiled, 'sdk_component_body_changed')
                request['body'] = compiled
                request['transport'] = transport
            body = request.get('body', {})
            require(body.get('name') == request.get('name') and body.get('namespace') == self.config['namespace'], 'create_body_scope_mismatch')
            for field in ('name', 'cluster', 'queue_code', 'framework_type', 'gems_project'):
                require(isinstance(body.get(field), str) and body[field].strip(), 'complete_component_body_required:' + field)
            require(body.get('owner', self.config['owner']) == self.config['owner'], 'create_owner_mismatch')
            native_data(body.get('data'))
            require(self.backend.create_candidates(request['name']) == [], 'component_name_already_exists')
        else:
            kind = 'component' if action == 'task.submit' else action.split('.')[0]
            baseline = self.checked(kind, request.get('uid'), request.get('name'))
            if kind == 'devspace' and self.config.get('devspace_transport') == 'official-api':
                require(baseline.get('metadata', {}).get('annotations', {}).get('lpai.org/notebookVersion') == 'v2',
                        'official_notebook_v2_contract_required')
            if action == 'task.submit':
                require(bool(request.get('name')) and request.get('cluster') == baseline.get('cluster'), 'task_name_and_component_cluster_required')
                settings = {k: baseline[k] for k in ('queue_code', 'framework_type', 'gems_project', 'permission', 'priority') if k in baseline}
                require('component_settings' not in request or request['component_settings'] == settings, 'component_settings_override_not_supported')
                request['component_settings'] = settings
                native_data(request.get('data'))
                info = request.get('task_info', {})
                require(set(info) == {'problem_statement', 'solution_approach', 'expected_result', 'expected_duration', 'expected_duration_desc'}, 'five_exact_task_information_fields_required')
                require(type(info['expected_duration']) is int and info['expected_duration'] > 0, 'positive_expected_duration_required')
                require(all(isinstance(v, str) and v.strip() for k, v in info.items() if k != 'expected_duration'), 'nonempty_task_information_required')
        projected = self.projected(baseline, action) if baseline else None
        result = {'schema': 'lpai-manager-plan/v1', 'created_at': time.time(), 'request': request,
                  'runtime_digest': runtime_digest(),
                  'manager_digest': sha(self.config), 'baseline': projected,
                  'baseline_digest': sha(projected), 'namespace': self.config['namespace'], 'owner': self.config['owner'],
                  'route': (request.get('transport', 'official-cli') if action == 'component.create' else
                            ('official-api-sdk-rest' if self.config.get('devspace_transport') == 'official-api' else 'official-cli')
                            if action.startswith('devspace.') else self.config.get('component_transport', 'official-sdk')),
                  'preview': {'action': action, 'identity': request.get('uid') or request.get('name'),
                              'platform_writes': 0, 'execution_requires_bound_authorization': True}}
        data = request.get('data', request.get('body', {}).get('data'))
        if data:
            result['preview'].update(data_digest=sha(data), env_keys=[v['key'] for v in data.get('lpai_envs', [])],
                                     requested_gpus=sum(r['replica'] * r['resource']['Gpu'] for r in data['lpai_task']['roles']),
                                     changed_data_sections=[key for key in DATA_KEYS if (baseline or {}).get('data', {}).get(key) != data.get(key)])
        return result

    def projected(self, obj, action):
        if action.startswith('devspace.'):
            meta = obj['metadata']
            return {'uid': meta['uid'], 'namespace': meta['namespace'], 'name': meta['name'],
                    'owner': meta.get('annotations', {}).get('lpai.org/owner'), 'spec': obj.get('spec')}
        return {key: obj.get(key) for key in STABLE_FIELDS}

    def guard(self, plan):
        request = plan['request']
        component = request.get('uid') or 'create:' + request['name']
        spec = {'provider': 'lpai', 'namespace': self.config['namespace'], 'owner': self.config['owner'],
                'request_key': request['request_key'], 'component_uid': component,
                'configuration_digest': sha(plan), 'submission_registry': str(Path(self.config['submission_registry']).resolve())}
        return Guard(spec, Path(self.config['state_root']).resolve() / (sha(plan).split(':')[1] + '.json'))

    def apply(self, plan_path, authorization_path):
        plan, auth = authorized_plan(plan_path, authorization_path, self.config)
        request = plan['request']
        require(request.get('action') in ACTIONS, 'unsupported_operation_or_unverified_official_interface')
        self.legacy_pending(request)
        guard = self.guard(plan)
        with guard.lock():
            guard.load()
            if guard.state['phase'] == 'completed':
                return {'status': 'already-completed', 'uid': guard.state['uid'], 'new_platform_writes': 0}
            require(guard.state['phase'] == 'new', 'pending_or_unknown_write_reconcile_only')
            # Rebuild read-only preflight; a changed provider config invalidates plan.
            fresh = self.plan(request)
            require(fresh['baseline_digest'] == plan['baseline_digest'] and fresh['request'] == request, 'provider_configuration_changed_since_plan')
            state_path = Path(self.config['state_root']).resolve() / (guard.key + '-operation.json')
            guard.before_submit(state_path)
            guard.state['manager_authorization'] = {'path': str(Path(authorization_path).resolve()), 'sha256': digest_file(authorization_path)}
            guard.save()
            try:
                response = self.backend.mutate(request, plan_path)
                if isinstance(response, dict) and response.get('status') in ('pending_approval', 'approval-pending'):
                    raise PendingApproval(response)
                candidate = response if isinstance(response, str) else response.get('uid') if isinstance(response, dict) else None
                if request['action'] in ('task.stop', 'devspace.start', 'devspace.stop'):
                    candidate = plan['baseline']['uid']
                if candidate:
                    guard.state['candidate_uid'] = candidate
                    guard.save()
                return self.finish(plan, guard)
            except PendingApproval as error:
                guard.pending(error.approval, plan['route'])
                return {'status': 'pending_approval', **error.approval, 'resubmit_allowed': False}
            except Exception as error:
                # Intent stays occupied even if a provider printed an error or
                # timed out after actually creating the resource.
                guard.state['last_error_type'] = type(error).__name__
                if getattr(self.backend,'last_mutation_receipt',None):
                    guard.state['sdk_outcome_receipt']=self.backend.last_mutation_receipt
                guard.save()
                raise ValueError('write_outcome_unverified_reconcile_only') from None

    def finish(self, plan, guard):
        request, action = plan['request'], plan['request']['action']
        uid = guard.state.get('candidate_uid')
        if action == 'component.create' and not uid:
            matches = self.backend.create_candidates(request['name'])
            require(len(matches) == 1, 'unique_created_component_not_observed')
            uid = matches[0]['uid']
            guard.state['candidate_uid'] = uid
            guard.save()
        require(bool(uid), 'candidate_uid_missing_no_resubmit')
        kind = 'task' if action.startswith('task.') else action.split('.')[0]
        obj = self.checked(kind, uid=uid, name=request.get('name'))
        if action == 'component.create':
            wanted = {k:v for k,v in request['body'].items() if k != 'data'}
            observed = copy.deepcopy(obj)
            if wanted.get('wait_roles') == [] and observed.get('wait_roles') is None: observed['wait_roles'] = []
            compare = sdk_component_config_matches if request.get('transport') == 'official-sdk' else native_config_matches
            require(subset_matches(wanted, observed)
                    and compare(request['body']['data'], obj.get('data')), 'created_component_configuration_mismatch')
        elif action == 'task.submit':
            require(obj.get('project_uid') == request['uid'] and task_name_matches(request['name'], obj.get('name'), uid)
                    and obj.get('cluster') == request['cluster'], 'submitted_task_parent_or_identity_mismatch')
            require(subset_matches(request['component_settings'], obj), 'submitted_task_component_settings_mismatch')
            require(native_config_matches(request['data'], obj.get('data')), 'submitted_task_configuration_mismatch')
        else:
            phase = obj.get('status', {}).get('phase') if kind == 'devspace' else str(obj.get('status', '')).lower()
            desired = {'ready', 'running'} if action == 'devspace.start' else {'stopped', 'succeeded', 'failed', 'completed', 'cancelled'}
            if phase not in desired:
                return {'status': 'awaiting-readback', 'uid': uid, 'phase': phase, 'resubmit_allowed': False}
        guard.completed(uid)
        return {'status': 'verified', 'resource': summary(obj, kind), 'training_completed': False,
                'physical_resource_release_verified': False}

    def reconcile(self, plan_path, runtime_repair=None):
        plan = read_json(plan_path)
        require(plan.get('manager_digest') == sha(self.config), 'manager_configuration_changed')
        check_readonly_runtime(plan_path, runtime_repair)
        guard = self.guard(plan)
        with guard.lock():
            guard.load()
            if runtime_repair is not None:
                auth = guard.state.get('manager_authorization', {})
                require(auth.get('path') and auth.get('sha256') == digest_file(auth['path']), 'original_authorization_changed')
                guard.state['readonly_runtime_repair'] = {'path': str(Path(runtime_repair).resolve()),
                                                         'sha256': digest_file(runtime_repair)}
                guard.save()
            if guard.state['phase'] == 'completed':
                return {'status': 'already-completed', 'uid': guard.state['uid'], 'new_platform_writes': 0}
            require(guard.state['phase'] in ('pending-approval', 'intent'), 'existing_write_intent_required')
            request = plan['request']
            if plan['route']=='official-sdk' and not guard.state.get('candidate_uid'):
                receipt=self.backend.mutation_receipt(request,plan_path)
                if receipt:
                    guard.state['sdk_outcome_receipt']=receipt['reference']
                    if receipt.get('uid'):guard.state['candidate_uid']=receipt['uid']
                    guard.save()
            if request['action'] == 'task.submit' and plan['route'] in ('official-cli', 'official-sdk'):
                # Both an approval and a lost write response can leave the
                # original intent without a UID. Discover read-only, once;
                # after binding a candidate only that exact UID may be read.
                if not guard.state.get('candidate_uid'):
                    candidates = self.backend.task_candidates(request['name'], request['uid'])
                    require(len(candidates) <= 1, 'ambiguous_task_discovery_reconcile_only')
                    if candidates:
                        candidate = candidates[0]['uid']
                        require(bool(candidate), 'submission_candidate_uid_required')
                        guard.state['candidate_uid'] = candidate
                        guard.state['history'].append({'event': 'readonly-task-discovery', 'uid': candidate, 'at': time.time()})
                        guard.save()
                if guard.state.get('candidate_uid'):
                    return self.finish(plan, guard)  # exact parent/config readback still required
                if guard.state['phase'] == 'intent':
                    return {'status': 'submission-outcome-unresolved', 'phase': 'intent',
                            'new_platform_writes': 0, 'resubmit_allowed': False}
            if guard.state['phase'] == 'pending-approval':
                return {'status': 'pending-approval-unresolved', 'approval': guard.state['approval'], 'new_platform_writes': 0}
            return self.finish(plan, guard)


# Stable original transaction implementation; legacy Manager imports and test
# provider injection retain their public identity.
Manager = ManagerCore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', required=True)
    subs = parser.add_subparsers(dest='operation', required=True)
    inspect = subs.add_parser('inspect')
    inspect.add_argument('--kind', choices=('component', 'task', 'devspace'), required=True)
    inspect.add_argument('--uid'); inspect.add_argument('--name')
    prepare = subs.add_parser('plan'); prepare.add_argument('--request', required=True)
    apply = subs.add_parser('apply'); apply.add_argument('--plan', required=True); apply.add_argument('--authorization', required=True)
    rec = subs.add_parser('reconcile'); rec.add_argument('--plan', required=True)
    rec.add_argument('--runtime-repair')
    args = parser.parse_args()
    result_path = None
    try:
        config = read_json(args.config)
        result_path = output_target(config, args.output, [args.config, getattr(args, 'plan', None),
                                    getattr(args, 'authorization', None), getattr(args, 'request', None), getattr(args, 'runtime_repair', None)])
        manager = Manager(config)
        if args.operation == 'inspect':
            value = {'status': 'observed', 'platform_writes': 0,
                     'resource': summary(manager.checked(args.kind, args.uid, args.name), args.kind)}
        elif args.operation == 'plan': value = manager.plan(read_json(args.request))
        elif args.operation == 'apply': value = manager.apply(args.plan, args.authorization)
        else: value = manager.reconcile(args.plan, args.runtime_repair)
        atomic_json(result_path, value)
        print(json.dumps({'status': value.get('status', 'planned'), 'output': str(Path(args.output).resolve())}))
        return 0
    except Exception as error:
        # Bounded local reason only. Never include SDK/CLI response text.
        reason = str(error) if type(error) is ValueError and re.fullmatch(r'[a-z0-9_:-]{1,160}', str(error)) else type(error).__name__
        value = {'status': 'blocked', 'reason': reason}
        if result_path is not None: atomic_json(result_path, value)
        print(json.dumps(value)); return 1


if __name__ == '__main__':
    sys.exit(main())
