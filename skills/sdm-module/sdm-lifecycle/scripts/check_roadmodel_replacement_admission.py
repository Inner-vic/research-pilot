"""Exercise actual recovery/dispatch entrypoints against a no-network API fixture."""
import argparse
import contextlib
import copy
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import roadmodel_vision_dispatch as dispatch
import roadmodel_vision_recovery as recovery
from review_storage import atomic_json


def check(output):
    root = Path(tempfile.mkdtemp(prefix='admission-check-', dir=output))
    results = []
    def fixture(label, healthy_status='running', running_old_role=False):
        directory = root / label
        directory.mkdir()
        registry = directory / 'registry'
        registry.mkdir()
        parent = directory / 'parent'
        parent.mkdir()
        config = {'submission_registry': str(registry), 'owner': 'fixture@example.invalid', 'namespace': 'fixture'}
        spec = {'failed_arm': 'dinov3', 'healthy_arm': 'cbb1', 'failed_task_uid': 'fixture-failed',
            'healthy_task_uid': 'fixture-peer', 'failed_component_uid': 'fixture-old-component',
            'healthy_component_uid': 'fixture-peer-component', 'new_task_allowance': 1, 'parent_case': str(parent)}
        oldstate = {'arms': {'cbb1': {'phase': 'task-verified', 'task_uid': spec['healthy_task_uid'], 'component_uid': spec['healthy_component_uid']},
                             'dinov3': {'phase': 'task-verified', 'task_uid': spec['failed_task_uid'], 'component_uid': spec['failed_component_uid']}}}
        atomic_json(parent / 'dispatch-state.json', oldstate)
        atomic_json(directory / 'manager.json', config)
        atomic_json(directory / 'group-plan.json', {'replacement': spec})
        (directory / 'AUTHORIZATION.md').write_text('CPU fixture; no platform authority')
        for arm in ('cbb1', 'dinov3'):
            (directory / arm).mkdir()
            atomic_json(directory / arm / 'plan.json', {'name': 'fixture-' + arm})
            atomic_json(directory / arm / 'create-plan.json', {'fixture': True})
        calls = []
        tasks = {spec['failed_task_uid']: {'uid': spec['failed_task_uid'], 'project_uid': spec['failed_component_uid'],
                    'owner': config['owner'], 'namespace': config['namespace'], 'status': 'failed'},
                 spec['healthy_task_uid']: {'uid': spec['healthy_task_uid'], 'project_uid': spec['healthy_component_uid'],
                    'owner': config['owner'], 'namespace': config['namespace'], 'status': healthy_status}}
        pods = [{'name': 'fixture-old-role-' + str(i), 'task_uid': spec['failed_task_uid'], 'step': 'task',
                 'pod_type': 'user', 'status': 'Running' if running_old_role and i == 0 else 'Failed'} for i in range(4)]
        control = {'tasks': tasks, 'pods': pods, 'missing_failed': False, 'missing_pods': False}
        class Backend:
            def sdk(self): return None
            def cli(self, args): return None if control['missing_pods'] else copy.deepcopy(pods)
            def create_candidates(self, name): return []
        class Manager:
            def __init__(self, cfg): self.backend = Backend()
            def checked(self, kind, uid):
                if control['missing_failed'] and uid == spec['failed_task_uid']: return None
                return copy.deepcopy(tasks[uid])
            def apply(self, plan, authorization):
                calls.append({'plan': str(plan), 'authorization': str(authorization)})
                return {'status': 'verified', 'resource': {'uid': 'fixture-new-task' if Path(plan).name == 'submit-plan.json' else 'fixture-new-component'}}
        review = {'subject_digest': 'sha256:fixture-review-subject', 'receipt': 'fixture-receipt'}
        patches = contextlib.ExitStack()
        for module in (dispatch, recovery):
            patches.enter_context(patch.object(module, 'Manager', Manager))
            patches.enter_context(patch.object(module, 'verify_frozen', return_value=review))
            patches.enter_context(patch.object(module, 'locked', side_effect=lambda *a: contextlib.nullcontext()))
        patches.enter_context(patch.object(dispatch, 'inventory', return_value=({'total': 2, 'pages': 1}, [])))
        statepath = registry / ('vision-comparison-' + directory.name + '.json')
        return directory, statepath, calls, patches, control
    def expect_block(action, calls, wanted):
        try:
            action()
        except (ValueError, AssertionError) as error:
            if wanted is not None:
                assert wanted in str(error), repr(error)
        else:
            raise AssertionError('unadmitted_dispatch_allowed')
        assert calls == []
    for label, terminal_peer, old_running in (('no-init-terminal-peer', True, False), ('no-init-running-old-role', False, True)):
        directory, statepath, calls, patches, control = fixture(label, 'succeeded' if terminal_peer else 'running', old_running)
        with patches:
            expect_block(lambda: dispatch.run(directory, 'dinov3', 'create'), calls, 'replacement_admission_required')
            assert not statepath.exists()
        results.append({'case': label, 'blocked_before_mutation': True})
    directory, statepath, calls, patches, control = fixture('initialize-nonterminal-old-role', running_old_role=True)
    with patches:
        expect_block(lambda: recovery.initialize(directory), calls, None)
        assert not statepath.exists() and not (directory / 'replacement-admission.json').exists()
    results.append({'case': 'failed-task-status-but-old-role-running', 'admission_rejected': True})
    for label in ('wrong-arm', 'wrong-identity', 'wrong-review', 'nonterminal-proof', 'valid'):
        directory, statepath, calls, patches, control = fixture(label)
        with patches:
            recovery.initialize(directory)
            if label == 'wrong-arm':
                expect_block(lambda: dispatch.run(directory, 'cbb1', 'create'), calls, 'restricted_to_failed_arm')
            elif label != 'valid':
                path = directory / 'replacement-admission.json'
                admission = json.loads(path.read_bytes())
                state = json.loads(statepath.read_bytes())
                if label == 'wrong-identity':
                    admission['binding']['failed_task_uid'] = 'another-task'
                    state['replacement_admission'] = admission['binding']
                elif label == 'wrong-review':
                    admission['binding']['review_subject_digest'] = 'sha256:another-review'
                    state['replacement_admission'] = admission['binding']
                else:
                    admission['training_role_statuses'][0]['status'] = 'Running'
                atomic_json(path, admission)
                state['replacement_admission_sha256'] = dispatch.digest_file(path)
                atomic_json(statepath, state)
                expect_block(lambda: dispatch.run(directory, 'dinov3', 'create'), calls,
                    'old_training_roles_not_terminal' if label == 'nonterminal-proof' else 'binding_mismatch')
            else:
                dispatch.run(directory, 'dinov3', 'create')
                assert len(calls) == 1
                saved = json.loads(statepath.read_bytes())
                assert saved['arms']['dinov3']['phase'] == 'component-verified'
                assert saved['arms']['cbb1']['task_uid'] == 'fixture-peer'
        results.append({'case': label, 'passed': True, 'platform_mutations': 0,
                        'fixture_apply_calls': len(calls)})
    for label in ('role-changed-after-admission', 'missing-task-after-admission', 'missing-pods-after-admission', 'missing-peer-status', 'valid-submit'):
        directory, statepath, calls, patches, control = fixture(label)
        with patches:
            recovery.initialize(directory)
            state = json.loads(statepath.read_bytes())
            atomic_json(directory / 'dinov3/submit-plan.json', {'fixture': True})
            atomic_json(directory / 'dinov3/submit-authorization.json', {'fixture': True})
            state['arms']['dinov3'].update(phase='task-planned', component_uid='fixture-new-component',
                submit_plan_sha256=dispatch.digest_file(directory / 'dinov3/submit-plan.json'))
            atomic_json(statepath, state)
            if label == 'role-changed-after-admission':
                control['pods'][0]['status'] = 'Running'
            elif label == 'missing-task-after-admission':
                control['missing_failed'] = True
            elif label == 'missing-pods-after-admission':
                control['missing_pods'] = True
            elif label == 'missing-peer-status':
                control['tasks']['fixture-peer']['status'] = None
            if label != 'valid-submit':
                expect_block(lambda: dispatch.run(directory, 'dinov3', 'submit'), calls, 'replacement_live_')
                assert json.loads(statepath.read_bytes())['arms']['dinov3']['phase'] == 'task-planned'
            else:
                dispatch.run(directory, 'dinov3', 'submit')
                saved = json.loads(statepath.read_bytes())
                assert saved['arms']['dinov3']['phase'] == 'task-verified'
                assert saved['arms']['dinov3']['task_uid'] == 'fixture-new-task' and len(calls) == 1
                assert (directory / 'dinov3/replacement-live-before-submit.json').exists()
        results.append({'case': label, 'passed': True, 'platform_mutations': 0, 'fixture_apply_calls': len(calls)})
    result = {'passed': True, 'tests': results,
              'scope': 'Actual Python entrypoints with mocked official API/gate and real local files; no LPAI mutation or GPU qualification'}
    atomic_json(output / 'admission-tests.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    check(args.directory.resolve())
