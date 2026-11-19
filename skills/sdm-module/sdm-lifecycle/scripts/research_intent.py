"""Adapt component intent to original SDM S01/S02 validators and row gates.

No alternative scientific pass policy. No authority, lifecycle cursor, training,
model call or D05. The original Manager/Guard still owns execution effects.
"""
import copy
from datetime import datetime, timezone
from pathlib import Path

from canonical import digest
from contracts import AssetBundle
from lifecycle_rules import check_records


def _record(kind, key, content, project_id, created_at):
    return {'schema': 'sdm-artifact/v1', 'artifact_type': kind,
            'artifact_id': key, 'instance_key': key, 'version': 1, 'status': 'draft',
            'subjects': {'project_id': project_id}, 'content': copy.deepcopy(content),
            'content_digest': digest(content), 'uri': 'artifact://' + kind + '/' + key,
            'created_at': created_at, 'created_by': 'sdm-lifecycle'}


def check(bundle, requirement, brief):
    """Keep original field/schema semantics; add only exact reference binding."""
    if not isinstance(requirement, dict) or not isinstance(brief, dict):
        raise ValueError('original_sdm_intent_records_required')
    s01 = requirement.get('content', {}); s02 = brief.get('content', {})
    if (not isinstance(s01, dict) or not isinstance(s02, dict)
            or not isinstance(s02.get('source_refs'), dict)
            or s02.get('requirement_id') != s01.get('requirement_id')
            or s02.get('source_refs', {}).get('S01:' + requirement.get('instance_key', '')) != digest(requirement)):
        raise ValueError('sdm_brief_requires_exact_original_requirement')
    checks = [check_records(bundle, 1, {'S01': [requirement]}),
              check_records(bundle, 2, {'S02': [brief]})]
    if not all(row['passed'] for row in checks):
        errors = [finding['code'] for row in checks for finding in row['findings']]
        errors += [missing for row in checks for missing in row['missing']]
        raise ValueError('sdm_intent_blocked:' + ','.join(errors))
    return checks


def prepare(spec):
    """Validate before initialization creates any files or external operations."""
    value = spec.get('research_intent')
    if not isinstance(value, dict) or set(value) != {'requirement', 'brief'}:
        raise ValueError('explicit_sdm_requirement_and_brief_required')
    requirement = value['requirement']; brief = copy.deepcopy(value['brief'])
    if not isinstance(requirement, dict) or not isinstance(brief, dict):
        raise ValueError('original_sdm_intent_content_required')
    created = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    s01 = _record('S01', requirement.get('requirement_id', ''), requirement, spec['project_id'], created)
    sources = brief.get('source_refs', {})
    root_ref = 'S01:' + s01['instance_key']
    if not isinstance(sources, dict) or root_ref in sources:
        raise ValueError('initial_s01_reference_is_derived_from_exact_record')
    # Original lineage uses the complete canonical artifact digest, including
    # envelope metadata. The adapter derives this reference after creating S01;
    # it must not invent a separate content-only digest convention.
    brief['source_refs'] = {**sources, root_ref: digest(s01)}
    s02 = _record('S02', brief.get('brief_id', ''), brief, spec['project_id'], created)
    bundle = AssetBundle.load(); checks = check(bundle, s01, s02)
    return {'schema': 'sdm-research-intent/v1', 'project_id': spec['project_id'],
            'input_digest': digest(spec), 'engine_contract_digest': bundle.engine_contract_digest,
            'requirement': s01, 'brief': s02, 'record_checks': checks,
            'projection': {'label': '需求、非目标、假设与不确定性已通过规范校验',
                           'checked_steps': [1, 2], 'transition_accepted': False,
                           'scientific_outcome': 'not-assessed'},
            'remaining_integration_steps': list(range(3, 22)),
            'scope': 'Original S01/S02 object and row checks. Other SDM duties are not accepted by this receipt.',
            'execution_authority': False}


def publish(prepared, path):
    from component_payload import ref
    from review_storage import read_json
    from research_replan import once
    path = Path(path)
    if path.exists():
        old = read_json(path)
        # Replay initialization keeps original timestamps and exact receipt.
        if any(old.get(key) != prepared[key] for key in ('schema', 'project_id', 'input_digest', 'engine_contract_digest')):
            raise ValueError('original_sdm_initial_intent_changed')
        for kind in ('requirement', 'brief'):
            old_content = copy.deepcopy(old[kind]['content'])
            new_content = copy.deepcopy(prepared[kind]['content'])
            if kind == 'brief':
                root_ref = 'S01:' + old['requirement']['instance_key']
                old_content['source_refs'].pop(root_ref, None)
                new_content['source_refs'].pop(root_ref, None)
            if old_content != new_content:
                raise ValueError('original_sdm_initial_intent_changed')
    else:
        once(path, prepared)
    policy = {'version': 1, 'seed': ref(path), 'engine_contract_digest': prepared['engine_contract_digest']}
    load(policy)
    return policy


def load(policy):
    from component_research import reference
    from review_storage import read_json
    if (not isinstance(policy, dict) or set(policy) - {'version', 'seed', 'engine_contract_digest', 'fixed_brief'}
            or policy.get('version') != 1 or type(policy['version']) is not int):
        raise ValueError('explicit_sdm_intent_policy_v1_required')
    seed = read_json(reference(policy['seed']))
    bundle = AssetBundle.load()
    if (seed.get('schema') != 'sdm-research-intent/v1'
            or seed.get('engine_contract_digest') != policy.get('engine_contract_digest')
            or bundle.engine_contract_digest != policy['engine_contract_digest']):
        raise ValueError('sdm_intent_pinned_engine_changed')
    check(bundle, seed['requirement'], seed['brief'])
    return bundle, seed


def for_review(policy, fixed_brief=None):
    value = {key: copy.deepcopy(policy[key]) for key in ('version', 'seed', 'engine_contract_digest')}
    if fixed_brief is not None:
        value['fixed_brief'] = copy.deepcopy(fixed_brief)
    load(value)
    return value


def review_context(policy):
    _, seed = load(policy)
    return {'requirement': seed['requirement']['content'], 'original_brief': seed['brief']['content'],
            'fixed_brief': policy.get('fixed_brief'),
            'requirement_source': {'ref': 'S01:' + seed['requirement']['instance_key'],
                                   'digest': digest(seed['requirement'])},
            'scope': seed['scope'], 'execution_authority': False}


def proposal_schema(policy):
    """Model output supplies actual S02 facts, not caller-supplied pass flags."""
    _, seed = load(policy)
    from research_model_runner import object_schema, value_schema
    if 'fixed_brief' in policy:
        # Preserve every original S02 field and supported uncertainty shape.
        # A fixed read-only brief must not become impossible to reproduce just
        # because a new adapter assumed uncertainty was always plain text.
        return value_schema(policy['fixed_brief'])
    text = {'type': 'string'}
    uncertainty = value_schema(seed['brief']['content']['uncertainty'])
    if uncertainty != text:
        uncertainty = {'anyOf': [text, uncertainty]}
    return object_schema({**{key: text for key in ('brief_id', 'requirement_id', 'hypothesis', 'mechanism', 'uncertainty')},
        'uncertainty': uncertainty,
        'history': {'type': 'array', 'items': text},
        'source_refs': value_schema(seed['brief']['content']['source_refs']),
        **({'ml_contract_applicability': value_schema(seed['brief']['content']['ml_contract_applicability'])}
           if 'ml_contract_applicability' in seed['brief']['content'] else {}),
        'target_as_must': {'type': 'boolean'}})


def validate_proposal(proposal, policy):
    bundle, seed = load(policy)
    content = proposal.get('sdm_brief')
    if not isinstance(content, dict) or content.get('hypothesis') != proposal.get('hypothesis'):
        raise ValueError('proposal_requires_same_original_sdm_hypothesis')
    if 'fixed_brief' in policy and content != policy['fixed_brief']:
        raise ValueError('read_only_review_cannot_replace_frozen_sdm_brief')
    brief = _record('S02', content.get('brief_id', ''), content,
                    seed['project_id'], seed['brief']['created_at'])
    return check(bundle, seed['requirement'], brief)


def verify_submission(spec, science_config, code_config, proposal):
    policy = spec.get('sdm_intent')
    if policy is None:
        return None
    _, seed = load(policy)
    if (science_config.get('component_submission_review') is not None
            and science_config.get('sdm_intent', {}).get('fixed_brief') != seed['brief']['content']):
        raise ValueError('initial_review_requires_original_frozen_sdm_brief')
    for config in (science_config, code_config):
        actual = config.get('sdm_intent') or {}
        if any(actual.get(key) != policy.get(key) for key in ('version', 'seed', 'engine_contract_digest')):
            raise ValueError('submission_requires_original_sdm_intent')
    if code_config['sdm_intent'].get('fixed_brief') != proposal.get('sdm_brief'):
        raise ValueError('code_review_requires_accepted_sdm_brief')
    checks = validate_proposal(proposal, science_config['sdm_intent'])
    return {'schema': 'sdm-submission-intent-check/v1', 'seed': policy['seed'],
            'engine_contract_digest': policy['engine_contract_digest'],
            'record_checks': checks, 'brief_digest': digest(proposal['sdm_brief']),
            'checked_steps': [1, 2], 'remaining_integration_steps': list(range(3, 22)),
            'transition_accepted': False, 'execution_authority': False}
