"""Project-defined variable domains and comparisons; no model-specific policy."""
import hashlib
import json
import math
from pathlib import Path
from types import ModuleType


def parameter_values(parameters, variables):
    if not isinstance(parameters, dict) or not parameters or set(parameters) != set(variables):
        raise ValueError('project_variable_keys_required')
    for key, bounds in variables.items():
        value = parameters[key]
        kind = bounds.get('type')
        if kind == 'boolean':
            if type(value) is not bool: raise ValueError('variable_type_mismatch')
        elif kind in {'number', 'integer'}:
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or (kind == 'integer' and type(value) is not int)):
                raise ValueError('variable_type_mismatch')
            if not all(type(bounds.get(k)) in (int, float) and math.isfinite(bounds[k]) for k in ('minimum', 'maximum')):
                raise ValueError('finite_variable_bounds_required')
            if not bounds['minimum'] <= value <= bounds['maximum']:
                raise ValueError('variable_outside_bounds')
            if 'enum' in bounds:
                choices = bounds['enum']
                if (not isinstance(choices, list) or not 1 <= len(choices) <= 64 or any(
                        type(v) not in (int, float) or not math.isfinite(v)
                        or kind == 'integer' and type(v) is not int
                        or not bounds['minimum'] <= v <= bounds['maximum'] for v in choices)):
                    raise ValueError('finite_numeric_enum_choices_required')
                if value not in choices:
                    raise ValueError('variable_outside_choices')
        elif kind == 'enum':
            choices = bounds.get('choices')
            if (not isinstance(choices, list) or not choices or
                    not all(type(v) in (str, bool, int, float) and (type(v) not in (int, float) or math.isfinite(v)) for v in choices)):
                raise ValueError('finite_enum_choices_required')
            if not any(type(value) is type(v) and value == v for v in choices):
                raise ValueError('variable_outside_choices')
        else:
            raise ValueError('unsupported_variable_contract')


def load_adapter(binding):
    if not isinstance(binding, dict) or set(binding) != {'path', 'digest'}:
        raise ValueError('project_loop_adapter_binding_required')
    path = Path(binding['path'])
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('absolute_loop_adapter_required')
    raw = path.read_bytes()
    if 'sha256:' + hashlib.sha256(raw).hexdigest() != binding['digest']:
        raise ValueError('loop_adapter_changed_create_new_run')
    module = ModuleType('bound_project_loop_adapter')
    module.__file__ = str(path)
    # Execute the exact verified bytes, not a second mutable file read/pyc cache.
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    for name in ('prepare_round', 'observation_witness', 'retention_driver', 'cleanup_driver'):
        if not callable(getattr(module, name, None)):
            raise ValueError('loop_adapter_function_required:' + name)
    return module


def field(value, path):
    parts = path.split('.') if isinstance(path, str) else path
    for part in parts:
        if isinstance(value, dict) and part in value: value = value[part]
        elif isinstance(value, list) and type(part) is int and 0 <= part < len(value): value = value[part]
        else: return None
    return value


def validate_comparisons(config):
    conditions = config.get('comparison_conditions')
    if not isinstance(conditions, list) or not conditions:
        raise ValueError('project_comparison_conditions_required')
    names = set(); roles = set()
    for item in conditions:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name'] or item['name'] in names:
            raise ValueError('unique_comparison_condition_required')
        names.add(item['name'])
        if not isinstance(item.get('training_field'), str) or not item['training_field']:
            raise ValueError('comparison_observation_field_required')
        for option in ('required', 'across_rounds'):
            if type(item.get(option, True)) is not bool: raise ValueError('boolean_comparison_option_required')
        if item.get('required', True): roles.add(item.get('role'))
    if not {'code', 'data'} <= roles:
        raise ValueError('code_and_data_identity_conditions_required')
    return conditions


def compare(config, training, evaluation, previous):
    values, findings, comparable = {}, [], True
    for item in validate_comparisons(config):
        name = item['name']; required = item.get('required', True)
        value = field(training.get('actual', {}), item['training_field'])
        issues = []
        if value is None: issues.append('unmeasured')
        else:
            values[name] = value
            if 'expected' in item and (type(value) is not type(item['expected']) or value != item['expected']): issues.append('expected-value-mismatch')
            if item.get('across_rounds', True) and previous is not None and (name not in previous or previous[name] != value):
                issues.append('changed-between-rounds')
            if item.get('evaluation_field') and evaluation.get('status') == 'completed':
                other = field(evaluation.get('actual', {}), item['evaluation_field'])
                if type(other) is not type(value) or other != value: issues.append('training-evaluation-mismatch')
        for issue in issues:
            findings.append({'condition': name, 'problem': issue, 'required': required})
            if required: comparable = False
    return {'values': values, 'findings': findings, 'comparable': comparable}
