"""Planner–Executor policy; Generator–Critic is the Planner's review loop.

This module selects models, never invokes them or grants execution authority.
Absent policy preserves historical frozen configurations byte for byte.
"""
import copy
import re

ROLES = ('planner', 'executor', 'generator', 'critic')
DEFAULT_MODELS = dict(planner='gpt-6-astra', executor='gpt-6.1-sol',
                      generator='gpt-6-astra', critic='gpt-6.1-sol')


def policy(config):
    value = config.get('agent_roles')
    if value is None:
        return None
    if (not isinstance(value, dict) or type(value.get('version')) is not int
            or value['version'] != 1 or set(value) != {'version', 'models'}):
        raise ValueError('agent_roles_v1_required')
    models = value['models']
    if (not isinstance(models, dict) or set(models) != set(ROLES)
            or any(not isinstance(m, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', m)
                   for m in models.values())):
        raise ValueError('explicit_four_role_models_required')
    if models['generator'].casefold() == models['critic'].casefold():
        raise ValueError('distinct_generator_critic_models_required')
    return copy.deepcopy(value)


def review_settings(config, key):
    """Resolve the existing read-only call path, without mutating its subject."""
    selected = policy(config)
    value = config[key]
    if selected is None:
        return value
    if key not in {'proposal_review', 'research_worker'} or not isinstance(value, dict):
        raise ValueError('role_review_settings_required')
    models = {'a': selected['models']['generator'], 'b': selected['models']['critic']}
    if 'models' in value and value['models'] != models:
        raise ValueError('legacy_models_conflict_with_agent_roles')
    return {**value, 'models': models, 'dialogue_version': value.get('dialogue_version', 3)}


def describe(config):
    selected = policy(config)
    if selected is None:
        return {'enabled': False, 'historical_routing': True}
    return {'enabled': True, 'architecture': 'Planner–Executor',
            'planning_loop': 'Generator–Critic', **selected,
            'executor_runtime': 'existing guarded project driver; no model invocation',
            'executor_model_scope': 'agent implementation work, not deterministic driver execution',
            'execution_authority': False}
