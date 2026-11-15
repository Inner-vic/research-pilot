"""Recover review consumption from the shared executor's atomic state, worker v4."""
from pathlib import Path

import research_decision_store as store
from review_storage import atomic_json, read_json
from task_pipeline import digest


def execution_root(root, record):
    case = Path(record['response_path']).resolve().parent
    if case.name == 'review':
        result = case.parent
    elif case.parent.name == 'prepared' and case.parent.parent.name == 'review':
        result = case.parent.parent.parent
    else:
        raise ValueError('bound_coordinator_review_location_required')
    if not result.is_relative_to(Path(root).resolve()):
        raise ValueError('coordinator_acceptance_outside_worker')
    return result


def proof(config, root, path, value, state, child_config):
    record = value['record']
    folder = execution_root(root, record)
    review = state.get('review', {})
    plan = child_config['devspace_execution']['plan']
    from loop_analysis import validate as validated_decision
    accepted_decision = validated_decision(value['response'], read_json(record['request_path']))
    if (review.get('status') != 'completed'
            or Path(review.get('case_directory', '')).resolve() != Path(record['response_path']).parent.resolve()
            or digest(review.get('decision')) != digest(accepted_decision)
            or state.get('binding') != digest(plan)
            or state.get('uid') != plan['resource']['uid']
            or plan['resource']['uid'] != config['devspace_execution']['plan']['resource']['uid']):
        raise ValueError('coordinator_acceptance_binding_invalid')
    return {'schema': 'sdm-research-coordinator-acceptance/v1',
        'decision_id': path.parent.name, 'project_digest': digest(config),
        'execution_root': str(folder), 'execution_binding': state['binding'], 'uid': state['uid'],
        'execution_project_digest': digest(child_config), 'control_revision': record['control_revision'],
        'request_digest': record['request_digest'], 'response_digest': record['response_digest'],
        'review_digest': digest({k: v for k, v in review.items() if k != 'research_acceptance'})}


def stamp(child_config, folder, state):
    """Called before the shared executor atomically saves its accepted review."""
    cfg = child_config.get('research_worker', {})
    if cfg.get('version',0) < 4 or state.get('review', {}).get('status') != 'completed':
        return
    config = read_json(cfg['project_path'])
    if config['research_worker'] != cfg:
        raise ValueError('coordinator_parent_worker_changed')
    root = store.check_root(config, cfg['output'])
    case = Path(state['review']['case_directory']).resolve()
    owners = []
    for path in (root / 'research-decisions').glob('*/commit.json'):
        value = store.validate(config, root, path)
        if Path(value['record']['response_path']).parent.resolve() == case:
            owners.append((path, value))
    if len(owners) != 1:
        raise ValueError('unique_approved_coordinator_review_required')
    path, value = owners[0]
    if execution_root(root, value['record']) != Path(folder).resolve():
        raise ValueError('coordinator_execution_root_changed')
    state['review']['research_acceptance'] = proof(config, root, path, value, state, child_config)


def recover(config, root, path, value):
    """Bookkeeping only: no executor entry, model calls or platform operations."""
    if value['consumed']:
        return value
    folder = execution_root(root, value['record'])
    state_path = folder / 'execution.json'
    if not state_path.exists():
        return value
    state = read_json(state_path)
    accepted = state.get('review', {}).get('research_acceptance')
    if accepted is None:
        return value
    if folder == Path(root).resolve():
        child_config = config
    else:
        child_config = read_json(folder / 'project.json')
        if child_config.get('research_worker') != config['research_worker']:
            raise ValueError('coordinator_child_worker_changed')
    expected = proof(config, root, path, value, state, child_config)
    if accepted != expected:
        raise ValueError('durable_coordinator_acceptance_changed')
    consumed = store.seal({**value, 'consumed': True,
        'consumption_evidence': {'state_path': str(state_path),
            'acceptance': accepted, 'acceptance_digest': digest(accepted)}})
    atomic_json(path, consumed)
    store.materialize(config, root, path)
    return consumed
