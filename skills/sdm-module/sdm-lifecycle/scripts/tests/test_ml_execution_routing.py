"""LPAI execution replaces provider-specific gates, retaining evidence checks."""
from pathlib import Path
from contracts import AssetBundle


def test_live_execution_gates_route_without_ci_binding():
    bundle = AssetBundle.load(Path(__file__).resolve().parents[2] / 'assets')
    steps = bundle.document('lifecycle-steps.json')['steps']
    operations = [operation for row in steps for operation in row['platform_operations']]
    assert not any(operation.startswith('jenkins.') for operation in operations)
    for operation in operations:
        if operation.startswith('ml.'):
            domain, _, binding = bundle.operation_contract(operation)
            assert domain == 'ml-execution-platform'
            assert binding == 'ml_execution_platform_skill'


def test_reproduction_keeps_version_and_actual_result_requirements():
    bundle = AssetBundle.load(Path(__file__).resolve().parents[2] / 'assets')
    _, original, _ = bundle.operation_contract('jenkins.baseline-reproduction')
    _, current, binding = bundle.operation_contract('ml.baseline-reproduction')
    assert binding == 'ml_execution_platform_skill'
    # Changing the execution provider must not discard any evidence requirement.
    assert current['required_evidence'] == original['required_evidence']
    assert current['evidence_value_profiles'] == original['evidence_value_profiles']
    assert {'revision', 'tree_digest', 'dataset_manifest_digest',
            'split_manifest_digest', 'checkpoint_digest', 'metrics_digest',
            'runtime_environment_digest', 'terminal_status'}.issubset(current['required_evidence'])


def test_baseline_evaluation_does_not_require_future_training_reports():
    bundle = AssetBundle.load(Path(__file__).resolve().parents[2] / 'assets')
    _, baseline, _ = bundle.operation_contract('ml.baseline-locked-evaluation')
    _, trained, _ = bundle.operation_contract('ml.locked-evaluation')
    future = {'training_r07_digest', 'training_r08_digest', 'run_manifest_digest'}
    assert not future.intersection(baseline['required_evidence'])
    assert future.issubset(trained['required_evidence'])
    assert {'checkpoint_digest', 'evaluator_digest', 'data_digest', 'split_digest',
            'environment_manifest', 'environment_digest', 'metrics_digest',
            'scientific_facts', 'attempt_validity', 'artifact_completeness'}.issubset(baseline['required_evidence'])
    assert set(baseline['subject_requirements']['by_step']) == {'5'}
