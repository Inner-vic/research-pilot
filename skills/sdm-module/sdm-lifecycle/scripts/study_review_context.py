"""Produce proposal inputs from a frozen Study seed and accepted actual history.

No judgments, calls, platform actions or execution grants are produced here.
"""
import copy
from pathlib import Path

from project_driver import file_digest
from review_context import FIELDS, validate_context
from review_storage import read_json
from task_pipeline import digest


def seed(spec):
    cfg = spec.get('review_context')
    if cfg is None:
        return None
    if not isinstance(cfg, dict) or set(cfg) != {'version', 'path', 'sha256'} or cfg['version'] != 1:
        raise ValueError('study_review_context_v1_required')
    path = Path(cfg['path'])
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or file_digest(path) != cfg['sha256']:
        raise ValueError('frozen_study_review_context_required')
    value = read_json(path)
    if value.get('schema') != 'sdm-study-review-context/v1':
        raise ValueError('study_review_context_seed_schema_required')
    context = {**{k: value.get(k) for k in FIELDS}, 'sources': value.get('sources'),
               'schema': 'sdm-review-context/v1', 'subject_digest': 'seed-validation'}
    validate_context(context, 'seed-validation')
    retained = value.get('retained_roles', [])
    if not isinstance(retained, list) or len(retained) > 8:
        raise ValueError('bounded_declared_retained_roles_required')
    registered = {str(Path(row['path'])) for row in context['sources']}
    from raw_materials import json_observations
    for role in retained:
        if not isinstance(role, dict) or not isinstance(role.get('json_globs'), list) or len(role['json_globs']) != 1:
            raise ValueError('single_registered_retained_json_required')
        name = role['json_globs'][0]
        if not isinstance(name, str) or Path(name).name != name or not name.endswith('.json'):
            raise ValueError('single_registered_retained_json_required')
        path = Path(role.get('root', '')) / name
        if str(path) not in registered:
            raise ValueError('retained_role_source_not_registered')
        json_observations({'path': str(path), 'bytes': path.stat().st_size, 'digest': file_digest(path)}, role.get('json_fields'), {})
    context['retained_roles'] = copy.deepcopy(retained)
    return context


def history_materials(config, child, history):
    """Retain numeric observations from prior actual results in result review."""
    if config['devspace_study'].get('review_context') is None:
        return
    material = child['devspace_execution']['plan'].get('raw_materials', {})
    roles = material.setdefault('roles', [])
    for role in seed(config['devspace_study'])['retained_roles']:
        if any(row.get('id') == role.get('id') for row in roles):
            raise ValueError('duplicate_retained_material_role')
        roles.append(copy.deepcopy(role))
    if not history:
        return
    report = config['devspace_study']['result_artifact']
    candidates = [r for r in material.get('roles', [])
                  if r.get('root') == '$artifacts' and r.get('json_globs') == [report]]
    if len(candidates) != 1:
        raise ValueError('single_declared_study_json_result_role_required')
    roles = material['roles']
    for row in history:
        path = Path(row['result_path'])
        if path.is_symlink() or not path.is_file() or file_digest(path) != row['result_digest']:
            raise ValueError('accepted_study_history_result_changed')
        role = {**copy.deepcopy(candidates[0]), 'id': 'previous-round-%02d' % row['round'],
                'root': str(path.parent)}
        if any(r['id'] == role['id'] for r in roles):
            raise ValueError('reserved_study_history_material_role')
        roles.append(role)


def produce(config, child, folder, history, parent, once):
    value = seed(config['devspace_study'])
    if value is None:
        return
    from proposal_review import subject
    value['subject_digest'] = digest(subject(child))
    value['configuration'] = {'seed': value['configuration'], 'actual_subject': subject(child),
        'planned_parameters': child['devspace_round_context']['planned_parameters'],
        'parent_decision': parent}
    observations = []
    for row in history:
        result = Path(row['result_path'])
        if file_digest(result) != row['result_digest']:
            raise ValueError('accepted_study_history_result_changed')
        previous = result.parent.parent
        observations.append({k: row[k] for k in ('round', 'parameters', 'metrics', 'result_path', 'result_digest')})
        for label, path, kind in (
                ('result', result, 'metric'),
                ('decision', previous / 'iteration-decision.json', 'history'),
                ('observation', previous / 'round-observation.json', 'history'),
                ('consumption', previous / 'review-consumption.json', 'artifact')):
            if not path.is_file():
                raise ValueError('accepted_prior_round_material_required:' + label)
            value['sources'].append({'id': 'previous-%02d-%s' % (row['round'], label),
                'kind': kind, 'path': str(path), 'sha256': file_digest(path)})
    value['history'] = {'seed': value['history'], 'actual_accepted_rounds': observations,
                        'parent_decision': parent}
    validate_context(value, value['subject_digest'])
    once(folder / 'review-context.json', value)
