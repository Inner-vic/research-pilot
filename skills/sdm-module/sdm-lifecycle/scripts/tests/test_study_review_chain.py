"""Original public coordinator to review/consume/archive/successor, control only."""
import json
import copy
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

import devspace_study as study
import research_consumption as consumption
from project_driver import file_digest
from review_storage import atomic_json, read_json
from task_pipeline import digest
from test_devspace_main_workflow import plan_reply, result_reply
from test_research_consumption import integrated_case, step, until_wait


def approve(config, root, state, action):
    import research_worker as worker
    result_reply(state, action)
    response = Path(state['review']['response_path'])
    draft = read_json(response)
    draft['workflow_assessment']['alternatives'] = [draft['workflow_assessment']['alternatives']]
    response.unlink()  # generated control draft only, before atomic publication
    def caller(cfg, role, prompt, folder):
        return copy.deepcopy(draft) if role == 'a' else {'draft_digest': digest(draft),
            'verdict': 'approve', 'reason': 'Control acceptance only', 'challenges': []}
    result = worker.review(config, root, state, caller)
    assert result['status'] == 'approved', result


def public(config, root):
    result = subprocess.run([sys.executable, '-B', str(Path(study.__file__).with_name('run_project_pipeline.py')),
        '--project', config['research_worker']['project_path'], '--output', str(root), '--devspace-execution'],
        capture_output=True, encoding='utf-8', env={**os.environ, 'PYTHONUTF8': '1'}, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def chain(tmp_path):
    config, root = integrated_case(tmp_path, True)
    evidence = tmp_path / 'seed-evidence.txt'
    evidence.write_text('Synthetic control observations only; no ML evidence.')
    seed = tmp_path / 'review-seed.json'
    (tmp_path / 'retained').mkdir()
    control = tmp_path / 'retained/control.json'
    atomic_json(control, {'score': .005})
    atomic_json(seed, {'schema': 'sdm-study-review-context/v1',
        'goal': 'Control two accepted rounds', 'constraints': 'No ML or platform calls',
        'data': 'Generated numbers', 'code_revision': 'Pinned fixture', 'configuration': 'Fixed fixture',
        'history': 'No prior scientific results', 'unknowns': 'Only test coordinator behavior',
        'sources': [{'id': 'seed', 'kind': 'artifact', 'path': str(evidence), 'sha256': file_digest(evidence)},
                    {'id': 'control', 'kind': 'metric', 'path': str(control), 'sha256': file_digest(control)}],
        'retained_roles': [{'id': 'retained-control', 'root': str(control.parent), 'json_globs': [control.name],
                            'json_fields': {'score': {'field': 'score', 'type': 'float'}}}]})
    config['devspace_study']['review_context'] = {'version': 1, 'path': str(seed), 'sha256': file_digest(seed)}
    config['devspace_study']['authorization']['study_digest'] = study.binding(config)
    atomic_json(config['research_worker']['project_path'], config)
    return config, root, seed


def first_result(config, root):
    first = public(config, root)
    assert first['status'] == 'awaiting-main-plan-review', first
    assert (root / 'round-01/review-context.json').is_file()
    plan_reply(first)
    result = until_wait(config, root)
    assert result['status'] == 'awaiting-review', result
    packet = read_json(result['review']['request_path'])
    assert any(r['case_role'] == 'retained-control' for r in packet['history'])
    approve(config, root, result, 'run-next-round')
    return result


def test_public_coordinator_consumes_then_produces_successor_and_finishes(tmp_path):
    config, root, seed = chain(tmp_path)
    first_result(config, root)
    commit = next(root.glob('research-decisions/*/commit.json'))
    assert not read_json(commit)['consumed']
    second = public(config, root)
    assert second['active_round'] == 2 and second['status'] == 'awaiting-main-plan-review', second
    assert read_json(commit)['consumed']
    assert read_json(root / 'round-01/main/summary.json')['status'] == 'complete'
    coverage = read_json(root / 'round-01/main/summary.json')['coverage']
    assert coverage['required_completed_count'] == coverage['required_node_count']
    assert not coverage['failed']
    context = read_json(root / 'round-02/review-context.json')
    assert context['history']['actual_accepted_rounds'][0]['metrics'] == {'score': .01}
    assert {'previous-01-result', 'previous-01-consumption', 'previous-01-decision'} <= {s['id'] for s in context['sources']}
    assert not (tmp_path / 'remote/r02/process.json').exists()
    plan_reply(second)
    second = until_wait(config, root)
    assert second['status'] == 'awaiting-review', second
    packet = read_json(second['review']['request_path'])
    assert any(r['case_role'] == 'previous-round-01' for r in packet['history'])
    approve(config, root, second, 'stop')
    done = public(config, root)
    assert done['status'] == 'completed' and len(done['history']) == 2, done
    assert all(read_json(p)['consumed'] for p in root.glob('research-decisions/*/commit.json'))
    before = {str(p): p.read_bytes() for p in (tmp_path / 'remote').rglob('process.json')}
    assert public(config, root)['status'] == 'completed'
    assert before == {str(p): p.read_bytes() for p in (tmp_path / 'remote').rglob('process.json')}


def test_consumption_commit_crash_recovers_without_recomputing_first_round(tmp_path):
    config, root, seed = chain(tmp_path)
    first_result(config, root)
    process = tmp_path / 'remote/r01/process.json'
    original = process.read_bytes()
    real = consumption.atomic_json
    def crash(path, value):
        real(path, value)
        if Path(path).name == 'commit.json' and value.get('consumed'):
            raise OSError('after consumed commit, before successor')
    with patch.object(consumption, 'atomic_json', side_effect=crash):
        with pytest.raises(OSError, match='after consumed commit'):
            step(config, root)
    assert not (root / 'round-02/review-context.json').exists()
    recovered = public(config, root)
    assert recovered['active_round'] == 2 and recovered['status'] == 'awaiting-main-plan-review', recovered
    assert process.read_bytes() == original


def test_changed_seed_rejected_before_any_process(tmp_path):
    config, root, seed = chain(tmp_path)
    seed.write_text('{}')
    with pytest.raises(ValueError, match='frozen_study_review_context_required'):
        study.execution(config, root)
    assert not (tmp_path / 'remote').exists()
