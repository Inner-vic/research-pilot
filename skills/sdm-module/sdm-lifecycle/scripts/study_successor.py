"""Read-only handoff from a consumed Study decision to a fresh frozen plan.

This does not amend a review, import a run, grant execution, or clear a blocker.
"""
from pathlib import Path

from devspace_study import binding, iteration_proposal, observed
from project_driver import file_digest
from research_decision_store import validate as validate_commit
from review_storage import read_json
from task_pipeline import digest


def prepare(config, output):
    root = Path(output).resolve()
    if root.is_symlink() or read_json(root / 'study-config.json') != config:
        raise ValueError('exact_stored_study_required')
    state = read_json(root / 'execution.json')
    if state.get('study_digest') != binding(config) or state.get('study_complete'):
        raise ValueError('unfinished_exact_study_required')
    number = state.get('active_round')
    if type(number) is not int or number < 1 or state.get('active_attempt', 1) != 1:
        raise ValueError('successful_first_attempt_handoff_required')
    folder = root / ('round-%02d' % number)
    child = read_json(folder / 'execution.json')
    child_config = read_json(folder / 'project.json')
    if child.get('status') != 'completed' or child['binding'] != digest(child_config['devspace_execution']['plan']):
        raise ValueError('completed_exact_round_required')
    accepted = child.get('review', {}).get('research_acceptance', {})
    commit_path = root / 'research-decisions' / accepted.get('decision_id', '') / 'commit.json'
    commit = validate_commit(config, root, commit_path)
    consumption_path = folder / 'review-consumption.json'
    consumed = read_json(consumption_path)
    if (not commit['consumed'] or consumed['consumption_evidence'] != commit.get('consumption_evidence')
            or consumed['execution_binding'] != child['binding']
            or consumed['request_digest'] != commit['record']['request_digest']
            or consumed['response_digest'] != commit['record']['response_digest']):
        raise ValueError('original_review_consumption_required')
    main = read_json(folder / 'main/summary.json')
    if main['status'] != 'complete':
        raise ValueError('original_round_archive_required')
    spec = config['devspace_study']
    params = read_json(folder / 'round-plan.json')['parameters']
    record = observed(spec, child, folder, params, None)
    record['round'] = number
    prior = [row for row in state.get('history', []) if row['round'] < number]
    history = prior + [record]
    step = record['review']['next_step']
    if step.get('authorization') != 'existing' or step.get('stop_decision') != 'continue':
        raise ValueError('existing_scoped_successor_proposal_required')
    # Validate domain/scope only. The original blocked judgment is retained;
    # this read-only path never calls the operational continuation entry.
    decision = iteration_proposal(spec, record, history)
    if decision['action'] != 'run-next-round':
        raise ValueError('new_round_proposal_required')
    remaining = spec['max_rounds'] - len(history)
    if remaining < 1:
        raise ValueError('study_round_authorization_exhausted')
    refs = [root / 'study-config.json', commit_path, consumption_path,
            folder / 'main/summary.json', folder / 'round-plan.json', Path(record['result_path'])]
    value = {'schema': 'sdm-study-successor-plan/v1', 'status': 'proposal-only',
        'parent_study_digest': binding(config), 'parent_study_id': spec['study_id'],
        'parent_project': config['research_worker']['project_path'], 'parent_output': str(root),
        'parent_round': number, 'parent_decision_id': commit['decision_id'],
        'parameters': decision['parameters'], 'decision': decision,
        'original_next_step_status': step['status'], 'original_blocker': step.get('blocker'),
        'remaining_rounds': remaining, 'allowed_variables': spec['allowed_variables'],
        'resource_uid': config['devspace_execution']['plan']['resource']['uid'],
        'source_result': {'path': record['result_path'], 'sha256': record['result_digest']},
        'original_main_coverage': main['coverage'],
        'original_main_failures': {key: value for key, value in main['nodes'].items() if value.get('status') == 'failed'},
        'original_main_quality_passed': main['coverage']['required_completed_count'] == main['coverage']['required_node_count'],
        'sources': [{'path': str(path), 'sha256': file_digest(path)} for path in refs],
        'requirements': ['Fresh configuration and explicit current context', 'Original proposal A/B gate',
                         'Same inherited human scope and resource Guard; resolve original blockers independently'],
        'execution_authority': False, 'source_modified': False, 'computation_imported': False}
    return {**value, 'handoff_digest': digest(value)}


def validate_inheritance(config):
    """A fresh linked profile may only narrow the consumed parent's human scope."""
    spec = config['devspace_study']
    ref = spec.get('predecessor')
    if ref is None:
        return
    if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
        raise ValueError('pinned_study_predecessor_required')
    path = Path(ref['path'])
    if not path.is_absolute() or path.is_symlink() or file_digest(path) != ref['sha256']:
        raise ValueError('pinned_study_predecessor_changed')
    handoff = read_json(path)
    parent = read_json(handoff['parent_project'])
    if prepare(parent, handoff['parent_output']) != handoff:
        raise ValueError('original_study_handoff_changed')
    previous = parent['devspace_study']
    if (spec['study_id'] != 'successor-' + handoff['handoff_digest'].removeprefix('sha256:')[:24]
            or spec['initial_parameters'] != handoff['parameters']
            or spec['max_rounds'] > handoff['remaining_rounds']
            or spec.get('max_attempts_per_round', 1) > previous.get('max_attempts_per_round', 1)):
        raise ValueError('successor_scope_must_narrow_original_grant')
    for key in ('allowed_variables', 'parameter_flags', 'result_artifact', 'fixed_fields', 'metrics', 'required_values', 'checkpoint_resume'):
        if spec.get(key) != previous.get(key):
            raise ValueError('successor_experiment_contract_changed:' + key)
    current, original = config['devspace_execution'], parent['devspace_execution']
    if current['owner'] != original['owner'] or current['plan']['resource'] != original['plan']['resource']:
        raise ValueError('successor_resource_scope_changed')
    if current['authorization']['source_ref'] != original['authorization']['source_ref']:
        raise ValueError('successor_human_authorization_source_changed')
    left, right = current['plan'], original['plan']
    for key in ('argv', 'preflight', 'artifacts', 'artifact_collection_mode', 'health_policy',
                'max_artifact_bytes', 'max_total_bytes', 'timeout_seconds'):
        if left.get(key) != right.get(key):
            raise ValueError('successor_execution_contract_changed:' + key)
    if {row['name']: row['sha256'] for row in left['inputs']} != {row['name']: row['sha256'] for row in right['inputs']}:
        raise ValueError('successor_training_inputs_changed')


def rebind_unlaunched(config, output, replacement_project):
    """Transfer only a never-acquired logical claim to its corrected fresh version."""
    from review_storage import atomic_json
    from submission_guard import Guard
    from task_event_collector import collector_lock
    root = Path(output).resolve()
    target = read_json(replacement_project)
    validate_inheritance(config)
    validate_inheritance(target)
    if (config['devspace_study'].get('predecessor') is None
            or target['devspace_study'].get('predecessor') != config['devspace_study']['predecessor']
            or target['devspace_study']['study_id'] != config['devspace_study']['study_id']):
        raise ValueError('same_pinned_successor_claim_required')
    target_root = Path(target['research_worker']['output']).resolve()
    if (read_json(root / 'study-config.json') != config or root == target_root
            or str(Path(replacement_project).resolve()) != target['research_worker']['project_path']):
        raise ValueError('fresh_exact_replacement_required')
    def guard_for(value, folder):
        return Guard({'provider': 'sdm-devspace-study', 'namespace': value['devspace_execution']['plan']['resource']['namespace'],
            'owner': value['devspace_execution']['owner'], 'request_key': value['devspace_study']['study_id'],
            'component_uid': value['devspace_execution']['plan']['resource']['uid'],
            'configuration_digest': binding(value), 'submission_registry': value['devspace_execution']['submission_registry']}, folder / 'execution.json')
    old, new = guard_for(config, root), guard_for(target, target_root)
    if old.path != new.path:
        raise ValueError('same_global_successor_registry_required')
    with collector_lock(str(root / 'study')):
        with old.lock():
            stored = read_json(old.path)
            if stored['scope'] == new.scope and stored.get('state_path') == str(target_root / 'execution.json'):
                new.load()
                return {'status': 'reused', 'execution_authority': False, 'source_modified': False}
            old.load()
            if old.state['phase'] != 'intent' or old.state.get('state_path') != str(root / 'execution.json'):
                raise ValueError('never_acquired_study_intent_required')
            if any(root.glob('round-*/execution.json')) or any(root.glob('round-*/attempt-*/execution.json')):
                raise ValueError('existing_execution_cannot_rebind')
            for pattern in ('**/start-request.json', '**/stop-request.json', '**/process.json'):
                if any(root.glob(pattern)):
                    raise ValueError('possible_external_effect_cannot_rebind')
            if target_root.exists():
                raise ValueError('replacement_output_must_be_fresh')
            from model_runner import completed
            from research_model_receipts import checked
            calls = []
            for request in root.glob('round-*/proposal-review/*/model-calls/*/request.json'):
                envelope = checked(request, 'sdm-model-runner-envelope/v1')
                result = completed(request.parent, envelope['binding'])
                if result.get('status') != 'completed':
                    raise ValueError('unknown_model_outcome_cannot_rebind')
                calls.append({'path': str(request.parent), 'completion_digest': digest(result)})
            proof = {'schema': 'sdm-unlaunched-study-rebind/v1', 'old_scope': old.scope, 'new_scope': new.scope,
                'old_output': str(root), 'new_output': str(target_root), 'old_project': config['research_worker']['project_path'],
                'old_project_sha256': file_digest(config['research_worker']['project_path']),
                'new_project': str(Path(replacement_project).resolve()), 'new_project_sha256': file_digest(replacement_project),
                'old_guard': old.state, 'known_completed_model_calls': calls,
                'external_intents': [], 'execution_authority': False, 'source_modified': False}
            path = old.root / 'unlaunched-rebind' / (digest(proof).removeprefix('sha256:') + '.json')
            if path.exists() and read_json(path) != proof:
                raise ValueError('immutable_unlaunched_rebind_changed')
            if not path.exists():atomic_json(path, proof)
            state = {**old.state, 'scope': new.scope, 'state_path': str(target_root / 'execution.json'),
                'history': old.state['history'] + [{'event': 'unlaunched-rebind', 'proof': str(path), 'sha256': file_digest(path)}]}
            atomic_json(old.path, state)
            return {'status': 'rebound-unlaunched-only', 'proof': str(path), 'known_completed_model_calls': len(calls),
                'execution_authority': False, 'source_modified': False}
