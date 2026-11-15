from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import comparison_reporting as reporting


def bound(path, value):
    path.write_text(json.dumps(value, allow_nan=False), encoding='utf-8')
    return {'path': str(path), 'digest': 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()}


def case(tmp_path):
    definition = {'evaluation.score': {'direction': 'maximize', 'unit': 'ratio'},
                  'evaluation.error': {'direction': 'minimize', 'unit': 'cm'}}
    fixed = dict(initial_checkpoint_ref='fixture-checkpoint', evaluator_ref='fixture-evaluator',
                 training_budget={'healthy_updates': 10}, effective_batch=8, precision='fixture-fp32',
                 randomness_pairing='fixture-seed-1', checkpoint_selection='last-healthy')
    conditions = {**fixed, 'dataset_version': 'fixture-data', 'split_digest': 'fixture-split', 'metric_definition': definition}
    plan = {'schema': 'sdm-comparison-group-planning/v1', 'group_id': 'fixture-pair', 'project': 'fixture-project',
            'question': 'Fixture comparison only', 'changed_factor': 'module', 'schedule': 'sequential', 'arms': []}
    spec = {'schema': 'sdm-comparison-export/v1', 'record_refs': {},
            'metrics': [{'field': k, **v} for k, v in definition.items()]}
    records = {}
    for index, name in enumerate(('control', 'treatment')):
        params = {'module': bool(index), 'lr': 0.001}; config_digest = 'fixture-config-' + name
        record = {'schema': 'sdm-round-record/v1', 'run_id': 'fixture-' + name, 'round': 1, 'status': 'completed',
                  'parameters': params, 'provenance': {'source_commit': 'fixture-source-' + name,
                  'data': {'version': 'fixture-data', 'split_digest': 'fixture-split'},
                  'fixed_conditions': {**fixed, 'config_digest': config_digest}}, 'metric_definitions': definition,
                  'metrics': {'evaluation.score': {'value': 0.8 + index * 0.05}, 'evaluation.error': {'value': 2.0 - index * 0.5}},
                  'stages': {s: {'status': 'completed', 'receipt': {'fixture_arm': name, 'stage': s}} for s in ('training', 'evaluation')},
                  'limitations': ['Synthetic CPU fixture, not a real model experiment.']}
        plan['arms'].append({'id': name, 'record_identity': {'run_id': record['run_id'], 'round': 1},
                             'source_commit': record['provenance']['source_commit'], 'config_digest': config_digest,
                             'parameters': params, 'conditions': deepcopy(conditions)})
        records[name] = record
        spec['record_refs'][name] = bound(tmp_path / (name + '.json'), record)
    spec['plan_ref'] = bound(tmp_path / 'plan.json', plan)
    return spec, plan, records


def update_record(tmp_path, spec, records, name):
    spec['record_refs'][name] = bound(tmp_path / (name + '.json'), records[name])


def test_paired_direction_and_provenance_are_preserved(tmp_path):
    spec, plan, records = case(tmp_path)
    calls = []
    def verify(ref): calls.append(ref); return True
    result = reporting.build(spec, verify)
    assert result['status'] == 'paired-results-ready' and len(calls) == 4
    assert result['contrasts'][0]['treatment_minus_control'] == pytest.approx(0.05)
    assert result['contrasts'][1]['treatment_minus_control'] == -0.5
    assert result['contrasts'][1]['direction_adjusted_gain'] == 0.5
    assert result['qualification'] == 'not-assessed' and not result['execution_authorized']
    assert result['arms'][0]['record_ref'] == spec['record_refs']['control']


def test_valid_metrics_without_authenticated_receipts_are_descriptive(tmp_path):
    spec, _, _ = case(tmp_path)
    result = reporting.build(spec, lambda ref: False)
    assert result['status'] == 'paired-observations'
    assert all(a['receipts']['training'] == 'not-verified' for a in result['arms'])


def test_missing_arm_waits_and_failed_arm_does_not_create_gain(tmp_path):
    spec, _, records = case(tmp_path)
    spec['record_refs']['treatment'] = None
    result = reporting.build(spec, lambda ref: True)
    assert result['status'] == 'waiting' and result['contrasts'][0]['treatment_minus_control'] is None
    update_record(tmp_path, spec, records, 'treatment')
    records['treatment']['status'] = 'failed'; records['treatment']['stages']['training']['status'] = 'failed'
    update_record(tmp_path, spec, records, 'treatment')
    result = reporting.build(spec, lambda ref: True)
    assert result['status'] == 'incomplete' and result['contrasts'][0]['treatment_minus_control'] is None


@pytest.mark.parametrize('field,value', [('split_digest', 'other-split'), ('effective_batch', 16),
                                       ('training_budget', {'healthy_updates': 11}), ('precision', 'another-precision')])
def test_actual_comparison_mismatch_blocks_delta(tmp_path, field, value):
    spec, _, records = case(tmp_path)
    if field == 'split_digest': records['treatment']['provenance']['data'][field] = value
    else: records['treatment']['provenance']['fixed_conditions'][field] = value
    update_record(tmp_path, spec, records, 'treatment')
    result = reporting.build(spec, lambda ref: True)
    assert result['status'] == 'invalid-comparison' and result['contrasts'][0]['treatment_minus_control'] is None


@pytest.mark.parametrize('change', ['source', 'config', 'run', 'extra-parameter', 'metric-unit', 'stage-status'])
def test_wrong_identity_or_hidden_change_is_visible(tmp_path, change):
    spec, _, records = case(tmp_path); record = records['treatment']
    if change == 'source': record['provenance']['source_commit'] = 'other-source'
    if change == 'config': record['provenance']['fixed_conditions']['config_digest'] = 'other-config'
    if change == 'run': record['run_id'] = 'wrong-run'
    if change == 'extra-parameter': record['parameters']['lr'] = 0.02
    if change == 'metric-unit': record['metric_definitions']['evaluation.score']['unit'] = 'percent'
    if change == 'stage-status': record['stages']['evaluation']['status'] = 'failed'
    update_record(tmp_path, spec, records, 'treatment')
    assert reporting.build(spec, lambda ref: True)['status'] == 'invalid-comparison'


def test_missing_metric_or_comparison_condition_remains_missing(tmp_path):
    spec, _, records = case(tmp_path)
    records['treatment']['metrics']['evaluation.score']['value'] = None
    del records['treatment']['provenance']['fixed_conditions']['evaluator_ref']
    update_record(tmp_path, spec, records, 'treatment')
    result = reporting.build(spec, lambda ref: True)
    assert result['status'] == 'incomplete'
    assert result['contrasts'][0]['treatment'] is None and result['contrasts'][0]['treatment_minus_control'] is None


def test_same_round_cannot_be_used_as_both_arms(tmp_path):
    spec, _, _ = case(tmp_path); spec['record_refs']['treatment'] = spec['record_refs']['control']
    assert reporting.build(spec, lambda ref: True)['status'] == 'invalid-comparison'


def test_changed_evidence_bytes_are_rejected(tmp_path):
    spec, _, _ = case(tmp_path)
    Path(spec['record_refs']['control']['path']).write_text('{}')
    with pytest.raises(ValueError, match='evidence_bytes_changed'): reporting.build(spec)


def test_export_reentry_preserves_files_and_rejects_changed_input(tmp_path):
    spec, _, _ = case(tmp_path); output = tmp_path / 'export'
    first = reporting.export(spec, output)
    paths = list(output.iterdir()); before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    assert reporting.export(spec, output) == first
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    changed = deepcopy(spec); changed['actual_condition_paths'] = {'precision': 'missing'}
    with pytest.raises(ValueError, match='fresh_revision'): reporting.export(changed, output)


def test_output_tampering_is_detected_without_rewriting_history(tmp_path):
    spec, _, _ = case(tmp_path); output = tmp_path / 'export'
    reporting.export(spec, output); path = output / 'comparison.json'
    record = json.loads(path.read_bytes()); record['contrasts'][0]['control'] = 99
    path.write_text(json.dumps(record))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='historical_comparison_output_changed'): reporting.export(spec, output)
    assert path.read_bytes() == before


def test_partial_export_can_resume_without_model_execution(tmp_path):
    spec, _, _ = case(tmp_path); output = tmp_path / 'export'; output.mkdir()
    record = reporting.build(spec)
    (output / 'comparison-input.json').write_text(json.dumps(spec))
    (output / 'comparison.json').write_text(json.dumps(record))
    assert reporting.export(spec, output) == record
    assert (output / 'export-binding.json').is_file() and (output / 'report.md').is_file()


def test_public_cli_produces_records_without_touching_evidence(tmp_path):
    spec, _, _ = case(tmp_path); config = tmp_path / 'export-spec.json'; config.write_text(json.dumps(spec))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    result = subprocess.run([sys.executable, '-B', str(Path(reporting.__file__)), '--spec', str(config),
                             '--output', str(tmp_path / 'export')], capture_output=True, timeout=20)
    assert result.returncode == 0 and json.loads(result.stdout)['status'] == 'paired-observations'
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}


def test_snapshot_source_and_config_must_match_actual_record(tmp_path):
    spec, plan, records = case(tmp_path)
    snapshot = {'schema': 'sdm-source-snapshot/v1', 'source_commit': 'wrong-source',
                'configuration': {'digest': plan['arms'][1]['config_digest']}}
    plan['arms'][1]['source_snapshot_ref'] = bound(tmp_path / 'snapshot.json', snapshot)
    spec['plan_ref'] = bound(tmp_path / 'plan.json', plan)
    assert reporting.build(spec, lambda ref: True)['status'] == 'invalid-comparison'


def test_numeric_overflow_does_not_export_nonfinite_delta(tmp_path):
    spec, _, records = case(tmp_path)
    records['control']['metrics']['evaluation.score']['value'] = -1e308
    records['treatment']['metrics']['evaluation.score']['value'] = 1e308
    for name in records: update_record(tmp_path, spec, records, name)
    result = reporting.build(spec, lambda ref: True)
    assert result['status'] == 'invalid-comparison' and result['contrasts'][0]['treatment_minus_control'] is None
    json.dumps(result, allow_nan=False)


def test_output_symlink_does_not_redirect_record_writes(tmp_path):
    spec, _, _ = case(tmp_path); destination = tmp_path / 'destination'; destination.mkdir()
    link = tmp_path / 'link'; link.symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError, match='plain_project_record_output'): reporting.export(spec, link)
    assert not list(destination.iterdir())


def test_arm_order_does_not_reverse_the_declared_contrast(tmp_path):
    spec, plan, _ = case(tmp_path); plan['arms'].reverse()
    spec['plan_ref'] = bound(tmp_path / 'plan.json', plan)
    result = reporting.build(spec, lambda ref: True)
    assert [a['role'] for a in result['arms']] == ['control', 'treatment']
    assert result['contrasts'][0]['treatment_minus_control'] == pytest.approx(0.05)


def test_missing_retained_output_is_not_silently_regenerated(tmp_path):
    spec, _, _ = case(tmp_path); output = tmp_path / 'export'
    reporting.export(spec, output); (output / 'comparison.json').unlink()
    with pytest.raises(ValueError, match='historical_comparison_output_missing'): reporting.export(spec, output)
    assert not (output / 'comparison.json').exists()
