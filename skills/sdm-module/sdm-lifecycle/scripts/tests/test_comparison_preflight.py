"""Planning admission boundaries, arithmetic and write isolation; no platform calls."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from comparison_preflight import preflight


def plan():
    conditions = dict(dataset_version='fixture-v1', split_digest='fixture-split',
                      initial_checkpoint_ref='fixture-checkpoint', evaluator_ref='fixture-evaluator',
                      metric_definition={'name': 'fixture-quality', 'direction': 'maximize'},
                      training_budget={'healthy_updates': 10}, effective_batch=8, precision='fixture-fp32',
                      randomness_pairing='fixture-seed-1', checkpoint_selection='last-healthy')
    arms = []
    for name, value in [('control', False), ('treatment', True)]:
        arms.append(dict(id=name, job_name='fixture-' + name,
                         attempt_root='/fixture/' + name + '/attempt',
                         workspace_root='/fixture/' + name + '/workspace',
                         output_root='/fixture/' + name + '/outputs',
                         source_ref='fixture-source-' + name, config_ref='fixture-config-' + name,
                         parameters={'module_enabled': value, 'lr': 0.001}, conditions=deepcopy(conditions),
                         allocation=dict(gpu_type='fixture-GPU', gpus=4, hourly_cost=2.5,
                                         currency='fixture-currency', max_resource_hours=2, quote_ref='fixture-quote')))
    return dict(schema='sdm-comparison-group-planning/v1', group_id='fixture-pair', project='fixture-project',
                question='Does the module change the fixture outcome?', changed_factor='module_enabled',
                schedule='parallel', project_policy_ref='fixture-policy', adapter_capability_ref='fixture-adapter',
                partial_start_policy='stop-started-arm', arm_failure_policy='stop-peer',
                launch_window_hours=0.1, billing_margin_cost=0.5,
                limits=dict(max_concurrent_tasks=2, max_gpus_per_arm=4, max_concurrent_gpus=8,
                            max_hourly_cost=5, max_gpu_hours=16, max_total_cost=10.5, max_wall_hours=2.1),
                arms=arms, confirmation={'status': 'pending'}, execution_authorized=False)


def test_parallel_and_sequential_cost_same_peak_and_elapsed_differ():
    p = plan()
    parallel = preflight(p)
    assert parallel['status'] == 'pass' and not parallel['dispatch_permitted']
    p['schedule'] = 'sequential'
    p['limits']['max_wall_hours'] = 4.1
    sequential = preflight(p)
    assert sequential['status'] == 'pass'
    assert parallel['computed']['cost_ceiling'] == sequential['computed']['cost_ceiling'] == 10.5
    assert parallel['computed']['gpu_hours_ceiling'] == sequential['computed']['gpu_hours_ceiling'] == 16
    assert (parallel['computed']['peak_gpus'], sequential['computed']['peak_gpus']) == (8, 4)
    assert (parallel['computed']['wall_hours_ceiling'], sequential['computed']['wall_hours_ceiling']) == (2.1, 4.1)


@pytest.mark.parametrize('limit,value', [('max_concurrent_tasks', 1), ('max_gpus_per_arm', 3),
                                      ('max_concurrent_gpus', 4), ('max_hourly_cost', 4),
                                      ('max_gpu_hours', 15), ('max_total_cost', 10), ('max_wall_hours', 2)])
def test_every_group_limit_is_checked(limit, value):
    p = plan(); p['limits'][limit] = value
    assert 'group_limit_exceeded:' + limit in preflight(p)['errors']


@pytest.mark.parametrize('condition,value', [('split_digest', 'another-split'), ('effective_batch', 16),
                                           ('training_budget', {'healthy_updates': 11}),
                                           ('checkpoint_selection', 'best-of-many')])
def test_hidden_comparison_changes_block(condition, value):
    p = plan(); p['arms'][1]['conditions'][condition] = value
    assert 'comparison_conditions_mismatch' in preflight(p)['errors']


def test_unreported_extra_factor_change_and_no_contrast_block():
    p = plan(); p['arms'][1]['parameters']['lr'] = 0.002
    assert 'exactly_declared_factor_must_differ' in preflight(p)['errors']
    p = plan(); p['arms'][1]['parameters'] = deepcopy(p['arms'][0]['parameters'])
    assert 'exactly_declared_factor_must_differ' in preflight(p)['errors']


@pytest.mark.parametrize('path', ['/fixture/control/workspace', '/fixture/control/workspace/cache',
                                '/fixture/control/workspace/../outputs'])
def test_shared_or_nested_cross_arm_writes_block(path):
    p = plan(); p['arms'][1]['output_root'] = path
    assert 'arm_write_roots_overlap' in preflight(p)['errors']


def test_windows_case_aliases_are_not_separate_roots():
    p = plan()
    p['arms'][0]['workspace_root'] = 'C:\\Fixture\\Control'
    p['arms'][1]['output_root'] = 'c:/fixture/CONTROL/outputs'
    assert 'arm_write_roots_overlap' in preflight(p)['errors']


@pytest.mark.parametrize('bad', [None, True, 0, -1, float('nan'), float('inf')])
def test_unresolved_or_invalid_gpu_count_never_passes(bad):
    p = plan(); p['arms'][0]['allocation']['gpus'] = bad
    assert preflight(p)['status'] == 'blocked'


def test_different_currency_or_hardware_requires_a_reviewed_design():
    p = plan(); p['arms'][1]['allocation']['currency'] = 'another-currency'
    assert 'currency_mismatch' in preflight(p)['errors']
    p = plan(); p['arms'][1]['allocation']['gpu_type'] = 'another-GPU'
    assert 'gpu_type_mismatch_requires_reviewed_design' in preflight(p)['errors']


def test_hash_changes_with_scope_but_confirmation_does_not_grant_dispatch():
    p = plan(); original = preflight(p)
    p['confirmation'] = {'status': 'confirmed', 'user_message_ref': 'fixture-user'}
    p['execution_authorized'] = True
    checked = preflight(p)
    assert checked['status'] == 'pass' and checked['plan_digest'] != original['plan_digest']
    assert checked['dispatch_permitted'] is False


def test_decimal_prices_do_not_fail_on_binary_rounding():
    p = plan()
    for a in p['arms']: a['allocation'].update(hourly_cost=0.1, max_resource_hours=1)
    p['billing_margin_cost'] = 0.1
    p['limits'].update(max_hourly_cost=0.2, max_total_cost=0.3)
    result = preflight(p)
    assert result['status'] == 'pass' and result['computed']['cost_ceiling'] == 0.3


def test_cli_is_read_only_and_rejects_unfilled_template(tmp_path):
    script = Path(__file__).parents[1] / 'comparison_preflight.py'
    source = Path(__file__).parents[2] / 'references/comparison-group.template.json'
    template = tmp_path / 'group.json'; template.write_bytes(source.read_bytes())
    before = template.read_bytes()
    result = subprocess.run([sys.executable, '-B', str(script), '--plan', str(template)],
                            capture_output=True, timeout=10)
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report['status'] == 'blocked' and report['dispatch_permitted'] is False
    assert template.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['group.json']


def test_malformed_shape_returns_a_block_not_an_exception():
    for p in (None, [], {'arms': [None, 'bad']}, {'arms': []}):
        assert preflight(p)['status'] == 'blocked'
    p = plan(); p['changed_factor'] = []
    assert preflight(p)['status'] == 'blocked'
    p = plan(); p['arms'][0]['allocation']['gpus'] = 10 ** 400
    assert preflight(p)['status'] == 'blocked'
