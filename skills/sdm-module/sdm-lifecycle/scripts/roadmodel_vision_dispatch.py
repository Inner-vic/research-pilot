"""Serialized, idempotent dispatch for one explicitly authorized two-arm group.

Only exact known group task UIDs are exempted from the owned-A800 conflict gate.
An uncertain write is reconciled, never repeated. No automatic replacement.
"""
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import time

from lpai_manager import Manager, digest_file, sdk_component_config_matches
from lpai_manager_backend import OfficialBackend
from proposal_review import gate
from review_storage import atomic_json, locked


def verify_frozen(directory):
    project = json.loads((directory / 'review-project.json').read_bytes())
    approved = gate(project, directory / 'review')
    if approved['status'] != 'proposal-approved':
        raise ValueError('actual_dual_model_review_required')
    frozen = json.loads((directory / 'review/FROZEN.json').read_bytes())
    for filename, expected in frozen['artifacts'].items():
        path = directory / filename
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('reviewed_artifact_changed:' + filename)
    if json.loads((directory / 'tests.json').read_bytes())['passed'] is not True:
        raise ValueError('actual_tests_required')
    return {'subject_digest': approved['subject_digest'], 'receipt': approved['receipt'],
            'receipt_sha256': digest_file(approved['receipt'])}


def inventory(manager, config):
    # Established SDK pagination; fail closed unless the full owner inventory
    # count and unique rows agree. Every foreign active A800 task blocks writes.
    from lpai_task_inventory import owner_inventory, active_a800_conflicts
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        _, api = OfficialBackend(config, worker_mode=True).sdk()
        result = owner_inventory(api, config['owner'])
    return result, active_a800_conflicts(result['items'], manager.checked)


def require_replacement_admission(directory, arm, state, group, review):
    spec = group.get('replacement')
    if spec is None:
        return
    if arm != spec['failed_arm'] or spec.get('new_task_allowance') != 1:
        raise ValueError('replacement_writes_restricted_to_failed_arm')
    if state is None:
        raise ValueError('replacement_admission_required')
    path = directory / 'replacement-admission.json'
    if not path.is_file() or state.get('replacement_admission_sha256') != digest_file(path):
        raise ValueError('replacement_admission_missing_or_changed')
    expected = {'directory': str(directory), 'group_plan_sha256': digest_file(directory / 'group-plan.json'),
        'review_subject_digest': review['subject_digest'], 'failed_arm': spec['failed_arm'],
        'healthy_arm': spec['healthy_arm'], 'failed_task_uid': spec['failed_task_uid'],
        'healthy_task_uid': spec['healthy_task_uid'], 'failed_component_uid': spec['failed_component_uid'],
        'healthy_component_uid': spec['healthy_component_uid'], 'new_task_allowance': 1}
    admission = json.loads(path.read_bytes())
    if admission.get('binding') != expected or state.get('replacement_admission') != expected:
        raise ValueError('replacement_admission_binding_mismatch')
    peer = state.get('arms', {}).get(spec['healthy_arm'], {})
    if peer.get('task_uid') != spec['healthy_task_uid'] or peer.get('component_uid') != spec['healthy_component_uid']:
        raise ValueError('replacement_peer_identity_mismatch')
    roles = admission.get('training_role_statuses', [])
    if (admission.get('failed_status') != 'failed' or not admission.get('all_four_failed_training_roles_terminal')
            or len(roles) != 4 or len({p['name'] for p in roles}) != 4
            or any(p['status'].lower() not in {'failed', 'deleted', 'stopped', 'succeeded'}
                   or p.get('task_uid') != spec['failed_task_uid'] for p in roles)):
        raise ValueError('replacement_old_training_roles_not_terminal')


def check_replacement_live(manager, config, group):
    spec = group.get('replacement')
    if spec is None:
        return None
    try:
        failed = manager.checked('task', spec['failed_task_uid'])
        peer = manager.checked('task', spec['healthy_task_uid'])
        pods = manager.backend.cli(['tasks', 'pods', '--uid', spec['failed_task_uid']])
    except Exception:
        raise ValueError('replacement_live_read_failed') from None
    for task, uid, component in ((failed, spec['failed_task_uid'], spec['failed_component_uid']),
                                 (peer, spec['healthy_task_uid'], spec['healthy_component_uid'])):
        if (not isinstance(task, dict) or task.get('uid') != uid or task.get('project_uid') != component
                or task.get('owner') != config['owner'] or task.get('namespace') != config['namespace']):
            raise ValueError('replacement_live_task_identity_missing_or_mismatched')
    if failed.get('status') != 'failed' or not isinstance(pods, list):
        raise ValueError('replacement_live_failed_task_or_roles_unreadable')
    if not isinstance(peer.get('status'), str) or not peer['status']:
        raise ValueError('replacement_live_peer_status_unreadable')
    roles = [p for p in pods if p.get('step') == 'task' and p.get('pod_type') == 'user']
    if (len(roles) != 4 or len({p.get('name') for p in roles}) != 4
            or any(p.get('task_uid') != failed['uid'] or str(p.get('status', '')).lower()
                   not in {'failed', 'deleted', 'stopped', 'succeeded'} for p in roles)):
        raise ValueError('replacement_live_old_training_roles_not_terminal')
    return {'checked_at': time.time(), 'failed_task_uid': failed['uid'], 'failed_status': failed['status'],
            'healthy_task_uid': peer['uid'], 'healthy_status': peer.get('status'),
            'all_four_old_training_roles_terminal': True,
            'roles': [{'name': p['name'], 'status': p['status'], 'task_uid': p['task_uid']} for p in roles]}


def run(directory, arm, operation):
    config = json.loads((directory / 'manager.json').read_bytes())
    registry = Path(config['submission_registry'])
    state_path = registry / ('vision-comparison-' + directory.name + '.json')
    expected_arms = {'cbb1', 'dinov3'}
    if arm not in expected_arms:
        raise ValueError('unknown_arm')
    manager = Manager(config)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        manager.backend.sdk()
    case = directory / arm
    plan = json.loads((case / 'plan.json').read_bytes())
    with locked(registry, 'a800-e40-engineering-once.lock', 'a800_comparison_dispatch_busy'):
        existing = json.loads(state_path.read_bytes()) if state_path.exists() else None
        group = json.loads((directory / 'group-plan.json').read_bytes())
        if operation in {'create', 'prepare-task', 'submit'} and group.get('replacement') and existing is None:
            raise ValueError('replacement_admission_required')
        state = existing if existing is not None else {
            'schema': 'roadmodel-vision-group-dispatch/v1', 'directory': str(directory),
            'authorized_arms': sorted(expected_arms), 'peak_gpus': 64, 'arms': {}}
        if state['directory'] != str(directory) or set(state['authorized_arms']) != expected_arms or state['peak_gpus'] != 64:
            raise ValueError('group_identity_mismatch')
        item = state['arms'].setdefault(arm, {'phase': 'new'})
        if operation in {'create', 'prepare-task', 'submit'}:
            review = verify_frozen(directory)
            require_replacement_admission(directory, arm, existing, group, review)
            checked, conflicts = inventory(manager, config)
            allowed = {x['task_uid'] for x in state['arms'].values() if x.get('task_uid')}
            foreign = [r for r in conflicts if r.get('uid', r.get('task_uid')) not in allowed]
            if foreign:
                raise ValueError('foreign_owned_active_A800_task:' + json.dumps(foreign))
            if any(x['phase'] in {'task-intent', 'component-intent'} for a, x in state['arms'].items() if a != arm):
                raise ValueError('other_arm_uncertain_write_reconcile_first')
            atomic_json(case / ('inventory-' + operation + '.json'), {'at': time.time(), 'complete': True,
                'total': checked['total'], 'pages': checked['pages'], 'conflicts': conflicts,
                'allowed_exact_group_uids': sorted(allowed), 'foreign_conflicts': foreign})
        def authorize(label, path):
            target = case / (label + '-authorization.json')
            atomic_json(target, {'schema': 'lpai-manager-authorization/v1', 'plan_sha256': digest_file(path),
                                'direct_user_confirmation': True, 'source_ref': str(directory / 'AUTHORIZATION.md')})
            return target
        def persist():
            atomic_json(state_path, state)
            atomic_json(directory / 'dispatch-state.json', state)
        def live_replacement_check():
            observation = check_replacement_live(manager, config, group)
            if observation is not None:
                atomic_json(case / ('replacement-live-before-' + operation + '.json'), observation)
        if operation == 'create':
            if item['phase'] != 'new':
                raise ValueError('write_intent_exists_reconcile_only')
            if manager.backend.create_candidates(plan['name']):
                raise ValueError('same_name_component_exists')
            live_replacement_check()
            item.update(phase='component-intent', review=review)
            persist()
            result = manager.apply(case / 'create-plan.json', authorize('create', case / 'create-plan.json'))
            atomic_json(case / 'create-result.json', result)
            if result['status'] == 'verified':
                item.update(phase='component-verified', component_uid=result['resource']['uid'])
        elif operation == 'reconcile-component':
            if item['phase'] != 'component-intent':
                raise ValueError('component_intent_required')
            result = manager.reconcile(case / 'create-plan.json')
            atomic_json(case / 'create-result.json', result)
            if result['status'] == 'verified':
                item.update(phase='component-verified', component_uid=result['resource']['uid'])
        elif operation == 'prepare-task':
            if item['phase'] != 'component-verified':
                raise ValueError('verified_component_required')
            live_replacement_check()
            actual = manager.checked('component', item['component_uid'])
            wanted = json.loads((case / 'create-body.json').read_bytes())['data']
            if actual['name'] != plan['name'] or actual.get('run_times', 0) or actual.get('last_task_uid') or not sdk_component_config_matches(wanted, actual['data']):
                raise ValueError('actual_component_mismatch')
            data = json.loads(json.dumps(actual['data']))
            for role in data['lpai_task']['roles']:
                role['resource']['Network'] = 3
            if [(r['role'], r['replica'], r['resource']['Id'], r['resource']['Gpu']) for r in data['lpai_task']['roles']] != [('master', 1, 41, 8), ('worker', 3, 41, 8)]:
                raise ValueError('exact_32_A800_topology_required')
            info = json.loads((case / 'task-info.json').read_bytes())
            info.pop('name')
            request = {'action': 'task.submit', 'request_key': plan['name'] + '-one-task',
                       'uid': actual['uid'], 'name': plan['name'], 'cluster': actual['cluster'], 'data': data, 'task_info': info}
            target = case / 'submit-plan.json'
            if target.exists():
                raise ValueError('existing_task_plan_no_replay')
            atomic_json(target, manager.plan(request))
            authorize('submit', target)
            item.update(phase='task-planned', submit_plan_sha256=digest_file(target))
            result = {'status': 'task-planned'}
        elif operation == 'submit':
            if item['phase'] != 'task-planned' or item['submit_plan_sha256'] != digest_file(case / 'submit-plan.json'):
                raise ValueError('unchanged_unique_task_plan_required')
            live_replacement_check()
            item['phase'] = 'task-intent'
            persist()
            result = manager.apply(case / 'submit-plan.json', case / 'submit-authorization.json')
            atomic_json(case / 'submit-result.json', result)
            if result['status'] == 'verified':
                item.update(phase='task-verified', task_uid=result['resource']['uid'])
        elif operation == 'reconcile-task':
            if item['phase'] != 'task-intent':
                raise ValueError('task_intent_required')
            result = manager.reconcile(case / 'submit-plan.json')
            atomic_json(case / 'submit-result.json', result)
            if result['status'] == 'verified':
                item.update(phase='task-verified', task_uid=result['resource']['uid'])
        elif operation == 'inspect':
            result = {'status': item['phase']}
            if item.get('task_uid'):
                actual = manager.checked('task', item['task_uid'])
                pods = manager.backend.cli(['tasks', 'pods', '--uid', item['task_uid']])
                atomic_json(case / 'task-latest.private.json', actual)
                atomic_json(case / 'pods.json', pods)
                result.update(status=actual['status'], task_uid=item['task_uid'],
                              pods=[{'name': p['name'], 'status': p['status']} for p in pods])
        else:
            raise ValueError('unsupported_operation')
        persist()
        atomic_json(case / 'execution-latest.json', {'operation': operation, 'state': item, 'result': result})
        print(json.dumps({'arm': arm, 'operation': operation, 'state': item, 'result_status': result['status']}))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--arm', required=True)
    p.add_argument('--operation', required=True)
    a = p.parse_args()
    run(a.directory.resolve(), a.arm, a.operation)
