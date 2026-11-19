"""Management transactions, fault cases and config fidelity; no platform writes."""
import copy
import json
from pathlib import Path
import sys
import uuid
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lpai_manager import Manager, digest_file, native_data, subset_matches, output_target, summary, task_name_matches, native_config_matches, sdk_component_config_matches
from lpai_manager_backend import payload, OfficialBackend
from review_storage import atomic_json
from submission_guard import PendingApproval


def data():
    return {'lpai_envs': [{'key': 'CUDA_VISIBLE_DEVICES', 'value': '0'}],
            'lpai_task': {'cmd': 'python train.py', 'roles': [
                {'role': 'worker', 'replica': 1, 'cmd': 'python train.py',
                 'image_info': {'image': 'official-image:1'}, 'resource': {'Id': 5, 'Gpu': 0}}]}}


class FakeBackend:
    def __init__(self):
        self.writes = 0
        self.component = {'uid': 'component-1', 'namespace': 'ns', 'owner': 'owner@example.com',
                          'name': 'base', 'cluster': 'cluster', 'data': data()}
        self.task = None
        self.failure = None
        self.pending = False
        self.bad_env = False
        self.stop_phase = 'stopping'
        self.space = {'metadata': {'name': 'space', 'namespace': 'ns', 'uid': 'space-uid',
                                  'annotations': {'lpai.org/owner': 'owner@example.com'}},
                      'spec': {'clusterName': 'cluster'}, 'status': {'phase': 'stopped'}}

    def read(self, kind, uid=None, name=None):
        obj = self.component if kind == 'component' else self.task if kind == 'task' else self.space
        if obj is None: raise ValueError('read_unavailable')
        return copy.deepcopy(obj)

    def validate_task_data(self, value):
        # Explicit transport substitute; retain the original native-data checks.
        # This is not an assertion of official SDK/network qualification.
        native_data(value)
        return {'status':'valid','qualification':'synthetic transport fixture','platform_writes':0}

    def mutation_receipt(self, request, plan_path):
        return None  # No SDK child exists in this explicit transport fixture.

    def task_candidates(self, name, component_uid):
        return [copy.deepcopy(self.task)] if self.task and self.task['name']==name and self.task['project_uid']==component_uid else []

    def create_candidates(self, name):
        return [copy.deepcopy(self.component)] if self.component.get('name') == name else []

    def compile_component(self, configuration):
        d = data(); d['lpai_envs'] = []
        return {'name': configuration['name'], 'namespace': 'ns', 'cluster': 'cluster', 'queue_code': 'queue',
                'framework_type': 'custom', 'gems_project': 'project', 'data': d}

    def mutate(self, request, plan_path):
        self.writes += 1
        if self.pending: raise PendingApproval({'status': 'pending_approval', 'approval_id': 'approval-1', 'mode': 'self'})
        if self.failure: raise self.failure
        if request['action'] == 'task.submit':
            d = copy.deepcopy(request['data'])
            if self.bad_env: d['lpai_envs'][0]['key'] = 'Cuda_visible_devices'
            self.task = {'uid': 'task-1', 'namespace': 'ns', 'owner': 'owner@example.com',
                         'name': request['name'], 'project_uid': request['uid'], 'cluster': 'cluster',
                         'data': d, 'status': 'pending'}
            return 'task-1'
        if request['action'] == 'task.stop': self.task['status'] = self.stop_phase; return 1
        if request['action'] == 'component.create':
            self.component = {**copy.deepcopy(request['body']), 'uid': 'new-component', 'owner': 'owner@example.com'}
            return 'new-component'
        self.space['status']['phase'] = 'starting'
        return {'requested': True}


class ManagerTests(unittest.TestCase):
    def setUp(self):
        # Windows sandbox may deny children of tempfile's restricted ACL.
        # Keep isolated local fixture evidence under the authorized workspace.
        self.root = (Path.cwd() / '_bmad-output/lpai-manager-20261028/test-work' / uuid.uuid4().hex).resolve()
        self.root.mkdir(parents=True)
        self.config = {'schema': 'lpai-manager/v1', 'namespace': 'ns', 'owner': 'owner@example.com',
                       'state_root': str(self.root / 'state'), 'submission_registry': str(self.root / 'registry')}
        self.backend = FakeBackend()
        self.manager = Manager(self.config, self.backend)

    def tearDown(self): pass

    def request(self):
        return {'action': 'task.submit', 'request_key': 'logical-trial-1', 'uid': 'component-1',
                'name': 'trial', 'cluster': 'cluster', 'data': data(),
                'task_info': {'problem_statement': 'problem', 'solution_approach': 'solution',
                              'expected_result': 'result', 'expected_duration': 1, 'expected_duration_desc': 'CPU sentinel'}}

    def files(self, request=None):
        plan = self.manager.plan(request or self.request())
        p = self.root / 'plan.json'; atomic_json(p, plan)
        a = self.root / 'auth.json'
        atomic_json(a, {'schema': 'lpai-manager-authorization/v1', 'plan_sha256': digest_file(p),
                        'direct_user_confirmation': True, 'source_ref': 'test-fixture-only'})
        return p, a

    def test_plan_preserves_uppercase_env_and_does_not_write(self):
        plan = self.manager.plan(self.request())
        self.assertEqual(plan['request']['data']['lpai_envs'][0]['key'], 'CUDA_VISIBLE_DEVICES')
        self.assertEqual(self.backend.writes, 0)
        self.assertEqual(plan['preview']['requested_gpus'], 0)

    def test_result_cannot_replace_plan_authorization_or_internal_ledger(self):
        with self.assertRaisesRegex(ValueError, 'result_must_not_overwrite_input'):
            output_target(self.config, self.root / 'plan.json', [self.root / 'plan.json'])
        with self.assertRaisesRegex(ValueError, 'result_must_not_overwrite_internal_state'):
            output_target(self.config, self.root / 'registry/guard.json', [])

    def test_sdk_process_timeout_is_bounded_and_no_auto_retry(self):
        import subprocess
        backend = OfficialBackend(self.config)
        with patch('lpai_manager_backend.subprocess.run', side_effect=subprocess.TimeoutExpired('sdk', 60)) as run:
            from lpai_manager_backend import OfficialReadFailure
            with self.assertRaises(OfficialReadFailure) as raised: backend.read('component', uid='component-1')
            self.assertTrue(raised.exception.read_only and raised.exception.retryable)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs['timeout'], 60)

    def test_sdk_null_items_is_empty_only_when_total_is_exactly_zero(self):
        class Project:
            def list(self, **kwargs): return self.reply
        project = Project()
        backend = OfficialBackend(self.config, worker_mode=True)
        backend._project, backend._task = project, object()
        project.reply = {'items': None, 'total': 0}
        self.assertEqual(backend.create_candidates('new'), [])
        project.reply = {'items': None, 'total': 1}
        with self.assertRaisesRegex(ValueError, 'complete_component_lookup_required'): backend.create_candidates('new')
        project.reply = {'items': [], 'total': 1}
        with self.assertRaisesRegex(ValueError, 'component_lookup_pagination_incomplete'): backend.create_candidates('new')

    def test_submit_repeated_apply_has_one_platform_write(self):
        p, a = self.files()
        self.assertEqual(self.manager.apply(p, a)['status'], 'verified')
        self.assertEqual(self.manager.apply(p, a)['status'], 'already-completed')
        self.assertEqual(self.backend.writes, 1)

    def test_component_create_reads_back_exact_native_body_and_creates_once(self):
        r = {'action': 'component.create', 'request_key': 'create-1', 'name': 'new', 'body': {
            'name': 'new', 'namespace': 'ns', 'cluster': 'cluster', 'queue_code': 'queue',
            'framework_type': 'pytorch', 'gems_project': 'project', 'data': data()}}
        p, a = self.files(r)
        self.assertEqual(self.manager.apply(p, a)['resource']['uid'], 'new-component')
        self.assertEqual(self.manager.apply(p, a)['status'], 'already-completed')
        self.assertEqual(self.backend.writes, 1)

    def sdk_create_request(self):
        return {'action': 'component.create', 'request_key': 'sdk-create', 'name': 'sdk-new', 'sdk_config': {
            'name': 'sdk-new', 'describe': 'CPU acceptance', 'cluster': 'cluster', 'queue_code': 'queue',
            'framework': 'custom', 'gems_project': 'project', 'cmd': 'echo sentinel', 'image': 'official:1',
            'envs': {}, 'task_role_specs': [{'rolename': 'worker', 'replica': '1', 'resource': {'id': 5, 'replica': 1}}]}}

    def test_sdk_create_compiles_and_preserves_selected_route_with_single_write(self):
        r = self.sdk_create_request()
        p, a = self.files(r)
        self.assertEqual(json.loads(p.read_bytes())['route'], 'official-sdk')
        self.assertEqual(self.manager.apply(p, a)['status'], 'verified')
        self.assertEqual(self.manager.apply(p, a)['status'], 'already-completed')
        self.assertEqual(self.backend.writes, 1)

    def test_sdk_config_rejects_silent_drops_and_env_mutation_before_write(self):
        changes = [('envs', {'CUDA_VISIBLE_DEVICES':'0'}, 'installed_sdk_changes_environment_keys'),
                   ('permission', 'user', 'unsupported_sdk_component_configuration_fields'),
                   ('code', [{'type':'OTHER'}], 'sdk_would_drop_unsupported_code_source'),
                   ('retry', True, 'sdk_retry_and_wait_types_required')]
        for field, value, reason in changes:
            r = self.sdk_create_request(); r['sdk_config'][field] = value
            with self.assertRaisesRegex(ValueError, reason): self.manager.plan(r)
        r = self.sdk_create_request(); r['sdk_config']['task_role_specs'][0]['replica'] = '1:2'
        with self.assertRaisesRegex(ValueError, 'sdk_fixed_role_configuration_required'): self.manager.plan(r)
        r = self.sdk_create_request(); r['sdk_config']['task_role_specs'][0]['resource']['cpu'] = 99
        with self.assertRaisesRegex(ValueError, 'sdk_resource_package_configuration_required'): self.manager.plan(r)
        self.assertEqual(self.backend.writes, 0)

    def test_sdk_package_catalog_projection_is_specific_and_keeps_compute_fields(self):
        wanted = data(); wanted['lpai_task']['roles'][0]['resource'].update(CpuLimit=1, Cpu=1, Mem=4)
        actual = copy.deepcopy(wanted); actual['lpai_task']['roles'][0]['resource'].pop('CpuLimit')
        self.assertFalse(native_config_matches(wanted, actual))
        self.assertTrue(sdk_component_config_matches(wanted, actual))
        actual['lpai_task']['roles'][0]['resource']['cpu'] = 0
        self.assertTrue(sdk_component_config_matches(wanted, actual))
        actual['lpai_task']['roles'][0]['resource']['Cpu'] = 8
        self.assertFalse(sdk_component_config_matches(wanted, actual))

    def test_official_notebook_transport_uses_exact_patch_without_cli(self):
        from unittest.mock import Mock
        cfg = {**self.config, 'devspace_transport':'official-api'}
        backend = OfficialBackend(cfg, worker_mode=True)
        client = Mock(); client.patch.return_value = {'code':0}
        with patch.object(backend,'notebook_api',return_value=(client,'/official/notebook')), patch.object(backend,'cli') as cli:
            backend.mutate({'action':'devspace.start','name':'space'},self.root/'plan.json')
            client.patch.assert_called_once_with('/official/notebook', {'status':'start'})
            cli.assert_not_called()
        self.backend.space['metadata']['annotations']['lpai.org/notebookVersion'] = 'v2'
        self.assertEqual(Manager(cfg,self.backend).plan({'action':'devspace.stop','request_key':'api-stop','name':'space'})['route'], 'official-api-sdk-rest')

    def test_creation_lost_ack_is_reconciled_by_unique_provider_object(self):
        r = {'action': 'component.create', 'request_key': 'create-unknown', 'name': 'new', 'body': {
            'name': 'new', 'namespace': 'ns', 'cluster': 'cluster', 'queue_code': 'queue',
            'framework_type': 'pytorch', 'gems_project': 'project', 'data': data()}}
        p, a = self.files(r)
        original = self.backend.mutate
        def lose(*args): original(*args); raise TimeoutError('lost ack')
        self.backend.mutate = lose
        with self.assertRaisesRegex(ValueError, 'write_outcome_unverified_reconcile_only'): self.manager.apply(p, a)
        self.assertEqual(self.manager.reconcile(p)['resource']['uid'], 'new-component')
        self.assertEqual(self.backend.writes, 1)

    def test_missing_or_changed_authorization_prevents_write(self):
        p, a = self.files()
        atomic_json(a, {'direct_user_confirmation': True})
        with self.assertRaisesRegex(ValueError, 'bound_user_authorization_required'): self.manager.apply(p, a)
        self.assertEqual(self.backend.writes, 0)

    def test_changed_plan_bytes_invalidate_authorization(self):
        p, a = self.files()
        p.write_bytes(p.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'bound_user_authorization_required'): self.manager.apply(p, a)

    def test_changed_runtime_is_rejected_even_with_rebound_authorization(self):
        p, a = self.files()
        value = json.loads(p.read_bytes()); value['runtime_digest'] = 'changed'
        atomic_json(p, value)
        auth = json.loads(a.read_bytes()); auth['plan_sha256'] = digest_file(p); atomic_json(a, auth)
        with self.assertRaisesRegex(ValueError, 'manager_runtime_changed_since_plan'): self.manager.apply(p, a)
        self.assertEqual(self.backend.writes, 0)

    def test_runtime_repair_allows_only_bound_readonly_reconciliation(self):
        self.backend.task = {'uid': 'task-1', 'owner': 'owner@example.com', 'namespace': 'ns', 'status': 'running'}
        p, a = self.files({'action': 'task.stop', 'request_key': 'repair-stop', 'uid': 'task-1'})
        self.manager.apply(p, a)
        plan = json.loads(p.read_bytes())
        proof = self.root / 'read-repair.json'
        atomic_json(proof, {'schema': 'lpai-manager-readonly-runtime-repair/v1', 'read_only': True,
                           'original_plan_sha256': digest_file(p), 'from_runtime': plan['runtime_digest'],
                           'to_runtime': 'fixed-runtime', 'source_ref': 'test-only'})
        self.backend.task['status'] = 'stopped'
        with patch('lpai_manager.runtime_digest', return_value='fixed-runtime'):
            with self.assertRaisesRegex(ValueError, 'manager_runtime_changed_since_plan'): self.manager.apply(p, a)
            self.assertEqual(self.manager.reconcile(p, proof)['status'], 'verified')
        self.assertEqual(self.backend.writes, 1)

    def test_worker_ascii_json_is_safe_for_legacy_console(self):
        import contextlib, io
        import lpai_sdk_worker
        value = {'text': '验收通过😀'}
        class Backend:
            def __init__(self, *args, **kwargs): pass
            def read(self, **kwargs): return value
        raw = io.BytesIO()
        writer = io.TextIOWrapper(raw, encoding='cp936', write_through=True)
        source = self.root / 'worker-input.json'
        atomic_json(source, {'config':{}, 'operation':'read', 'arguments':{}})
        with patch('sys.argv', ['worker', '--input', str(source)]), patch.object(lpai_sdk_worker, 'OfficialBackend', Backend):
            with contextlib.redirect_stdout(writer): self.assertEqual(lpai_sdk_worker.main(), 0)
        self.assertEqual(json.loads(raw.getvalue())['data'], value)

    def test_provider_empty_mount_and_resource_display_normalization(self):
        wanted = data(); wanted['lpai_git'] = []; wanted['lpai_model_download'] = []
        role = wanted['lpai_task']['roles'][0]
        role['use_role_cmd'] = None
        role['resource'].update(Cpu=1, Mem=4, cpu=0, memory=0, Name='display', PackageType='display')
        actual = copy.deepcopy(wanted)
        actual['lpai_git'] = actual['lpai_model_download'] = None
        r = actual['lpai_task']['roles'][0]
        r['use_role_cmd'] = False
        r['resource'].update(cpu=1, memory=4, Name='', PackageType='')
        role['resource']['LimitSum'] = 0; r['resource']['LimitSum'] = 60
        role['resourceInfo'] = ''; r['resourceInfo'] = 'CPU 1, memory 4GiB'
        self.assertTrue(native_config_matches(wanted, actual))
        r['resource']['Gpu'] = 8
        self.assertFalse(native_config_matches(wanted, actual))
        r['resource']['Gpu'] = 0; r['resource']['cpu'] = 99
        self.assertFalse(native_config_matches(wanted, actual))

    def test_semantic_readback_never_ignores_env_case_or_nonempty_mounts(self):
        wanted = data(); wanted['lpai_git'] = [{'name':'repo'}]
        actual = copy.deepcopy(wanted); actual['lpai_git'] = None
        self.assertFalse(native_config_matches(wanted, actual))

    def test_gpu_type_alias_can_only_omit_known_resource_namespace(self):
        wanted=data();wanted['lpai_task']['roles'][0]['resource']['GpuType']='nvidia.com/NVIDIA-A800-SXM4-80GB-RDMA-1T'
        actual=copy.deepcopy(wanted)
        actual['lpai_task']['roles'][0]['resource']['gpu_type']='NVIDIA-A800-SXM4-80GB-RDMA-1T'
        self.assertTrue(native_config_matches(wanted,actual))
        actual['lpai_task']['roles'][0]['resource']['gpu_type']='NVIDIA-A100'
        self.assertFalse(native_config_matches(wanted,actual))
        actual = copy.deepcopy(wanted); actual['lpai_envs'][0]['key'] = 'Cuda_visible_devices'
        self.assertFalse(native_config_matches(wanted, actual))

    def test_same_logical_request_lock_blocks_concurrent_execution(self):
        p, a = self.files()
        guard = self.manager.guard(json.loads(p.read_bytes()))
        with guard.lock():
            with self.assertRaisesRegex(ValueError, 'submission_request_busy'): self.manager.apply(p, a)
        self.assertEqual(self.backend.writes, 0)

    def test_owner_mismatch_is_rejected(self):
        self.backend.component['owner'] = 'someone-else@example.com'
        with self.assertRaisesRegex(ValueError, 'resource_owner_or_namespace_mismatch'): self.files()

    def test_changed_component_after_plan_prevents_write(self):
        p, a = self.files()
        self.backend.component['data']['lpai_task']['cmd'] = 'different'
        with self.assertRaisesRegex(ValueError, 'provider_configuration_changed_since_plan'): self.manager.apply(p, a)
        self.assertEqual(self.backend.writes, 0)

    def test_pending_approval_is_durable_and_reconcile_does_not_resubmit(self):
        p, a = self.files(); self.backend.pending = True
        self.assertEqual(self.manager.apply(p, a)['status'], 'pending_approval')
        self.assertEqual(self.manager.reconcile(p)['status'], 'pending-approval-unresolved')
        with self.assertRaisesRegex(ValueError, 'pending_or_unknown_write_reconcile_only'): self.manager.apply(p, a)
        self.assertEqual(self.backend.writes, 1)

    def test_timeout_after_write_does_not_retry(self):
        p, a = self.files(); self.backend.failure = TimeoutError('simulated')
        with self.assertRaisesRegex(ValueError, 'write_outcome_unverified_reconcile_only'): self.manager.apply(p, a)
        self.backend.failure = None
        with self.assertRaisesRegex(ValueError, 'pending_or_unknown_write_reconcile_only'): self.manager.apply(p, a)
        self.assertEqual(self.backend.writes, 1)

    def test_readback_env_mutation_is_not_success(self):
        p, a = self.files(); self.backend.bad_env = True
        with self.assertRaisesRegex(ValueError, 'write_outcome_unverified_reconcile_only'): self.manager.apply(p, a)
        self.assertEqual(self.backend.writes, 1)

    def test_wrong_task_parent_or_uid_readback_is_rejected(self):
        p, a = self.files()
        original = self.backend.mutate
        def bad(*args):
            result = original(*args); self.backend.task['project_uid'] = 'foreign'; return result
        self.backend.mutate = bad
        with self.assertRaisesRegex(ValueError, 'write_outcome_unverified_reconcile_only'): self.manager.apply(p, a)

    def test_stop_awaits_terminal_readback_and_does_not_claim_release(self):
        self.backend.task = {'uid': 'task-1', 'owner': 'owner@example.com', 'namespace': 'ns', 'status': 'running'}
        p, a = self.files({'action': 'task.stop', 'request_key': 'stop-1', 'uid': 'task-1'})
        self.assertEqual(self.manager.apply(p, a)['status'], 'awaiting-readback')
        self.backend.task['status'] = 'stopped'
        result = self.manager.reconcile(p)
        self.assertEqual(result['status'], 'verified')
        self.assertFalse(result['physical_resource_release_verified'])
        self.assertEqual(self.backend.writes, 1)

    def test_devspace_start_tracks_existing_uid_and_reconciles_without_write(self):
        p, a = self.files({'action': 'devspace.start', 'request_key': 'start-space-1', 'name': 'space'})
        self.assertEqual(self.manager.apply(p, a)['status'], 'awaiting-readback')
        self.backend.space['status']['phase'] = 'ready'
        self.assertEqual(self.manager.reconcile(p)['resource']['uid'], 'space-uid')
        self.assertEqual(self.backend.writes, 1)

    def test_recreated_devspace_with_same_name_is_not_original_uid(self):
        p, a = self.files({'action': 'devspace.start', 'request_key': 'space-identity', 'name': 'space'})
        self.manager.apply(p, a)
        self.backend.space['metadata']['uid'] = 'replacement-uid'
        self.backend.space['status']['phase'] = 'ready'
        with self.assertRaisesRegex(ValueError, 'devspace_uid_changed'): self.manager.reconcile(p)

    def test_legacy_pending_original_name_blocks_alternate_sdk_submit(self):
        b = self.root / 'legacy'; b.mkdir()
        atomic_json(b / 'create-intent.json', {})
        atomic_json(b / 'create-response.private.json', {'stdout': '{"status":"pending_approval","approval_id":"old-1"}'})
        atomic_json(b / 'create-body.json', {'name': 'trial', 'namespace': 'ns'})
        self.config['legacy_pending_cases'] = [str(b)]
        with self.assertRaisesRegex(ValueError, 'legacy_pending_request_requires_resolution'): self.files()
        self.assertEqual(self.backend.writes, 0)

    def test_renaming_same_native_pending_payload_does_not_bypass_guard(self):
        b = self.root / 'legacy'; b.mkdir()
        atomic_json(b / 'create-intent.json', {})
        atomic_json(b / 'create-response.private.json', {'stdout': '{"status":"pending_approval"}'})
        atomic_json(b / 'create-body.json', {'name': 'old-name', 'namespace': 'ns', 'data': data()})
        self.config['legacy_pending_cases'] = [str(b)]
        with self.assertRaisesRegex(ValueError, 'legacy_pending_request_requires_resolution'): self.files()

    def test_undeclared_override_not_ignored(self):
        r = self.request(); r['recipient'] = 'other@example.com'
        with self.assertRaisesRegex(ValueError, 'unknown_operation_request_fields'): self.manager.plan(r)

    def test_missing_task_information_and_unsupported_interfaces_are_rejected(self):
        r = self.request(); del r['task_info']['expected_duration']
        with self.assertRaisesRegex(ValueError, 'five_exact_task_information_fields_required'): self.manager.plan(r)
        for action in ('devspace.create', 'devspace.edit', 'approval.cancel', 'component.update'):
            with self.assertRaisesRegex(ValueError, 'unsupported_operation_or_unverified_official_interface'):
                self.manager.plan({'action': action, 'request_key': 'x'})

    def test_duplicate_env_and_elastic_role_are_not_silently_normalized(self):
        d = data(); d['lpai_envs'] *= 2
        with self.assertRaisesRegex(ValueError, 'unique_env_keys_required'): native_data(d)
        d = data(); d['lpai_task']['roles'][0]['replica'] = '1:2'
        with self.assertRaisesRegex(ValueError, 'fixed_positive_role_replicas_required'): native_data(d)

    def test_observed_server_env_rewrite_is_rejected_before_write(self):
        r=self.request();r['data']['lpai_envs'][0]['value']='payload$$withdollars'
        with self.assertRaisesRegex(ValueError,'provider_rewrites_double_dollar_environment_value'):self.manager.plan(r)
        self.assertEqual(self.backend.writes,0)

    def test_readback_comparison_allows_only_extra_provider_fields(self):
        self.assertTrue(subset_matches({'a': [{'x': 1}]}, {'a': [{'x': 1, 'default': 2}]}))
        self.assertFalse(subset_matches({'a': [1]}, {'a': [1, 2]}))
        self.assertFalse(subset_matches({'a': True}, {'a': 1}))

    def test_terminal_task_null_data_is_unknown_not_zero_gpus(self):
        result = summary({'uid': 'task', 'status': 'failed', 'data': None}, 'task')
        self.assertIsNone(result['gpus'])
        self.assertIsNone(result['env_keys'])
        result = summary({'uid': 'task', 'status': 'failed', 'data': {'lpai_envs': None, 'lpai_task': None}}, 'task')
        self.assertIsNone(result['gpus'])

    def test_provider_name_suffix_must_match_exact_returned_uid(self):
        uid = '34af597b-ed43-4306-a797-733fb798a8b7'
        self.assertTrue(task_name_matches('trial', 'trial-34af597b', uid))
        self.assertFalse(task_name_matches('trial', 'trial-deadbeef', uid))
        self.assertFalse(task_name_matches('trial', 'trial-anything', uid))

    def test_nested_pending_response_not_unwrapped_into_success(self):
        with self.assertRaises(PendingApproval): payload({'status': 'ok', 'data': {'status': 'pending_approval', 'approval_id': 'p'}})
        with self.assertRaisesRegex(ValueError, 'provider_error_response'): payload({'code': 500})

    def test_official_full_data_submit_preserves_exact_env_without_builder(self):
        class Task:
            def submit(self, **kwargs): self.arguments = kwargs; return 'task-1'
        task = Task(); backend = OfficialBackend(self.config)
        backend._project, backend._task = object(), task
        r = self.request(); self.assertEqual(backend.mutate(r, 'unused'), 'task-1')
        self.assertEqual(task.arguments['data'], r['data'])
        self.assertEqual(task.arguments['expected_duration'], 1)


if __name__ == '__main__': unittest.main()
