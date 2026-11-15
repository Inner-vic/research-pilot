"""A changed operational fact must not rewrite a consumed frozen review."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import devspace_study as study
import research_observation as observation
import research_worker as worker
from research_model_runner import shared_evidence
from review_storage import atomic_json, read_json
from study_successor import prepare, validate_inheritance, rebind_unlaunched
from project_driver import file_digest
from task_pipeline import digest
from test_devspace_main_workflow import plan_reply, result_reply
from test_research_consumption import until_wait
from test_study_review_chain import chain, public
from worker_conversation import snapshot


def test_blocked_consumed_round_exports_proposal_without_rewriting_or_computing(tmp_path):
    config, root, _ = chain(tmp_path)
    first = public(config, root)
    plan_reply(first)
    first = until_wait(config, root)
    result_reply(first, 'run-next-round')
    response = Path(first['review']['response_path'])
    draft = read_json(response)
    draft['workflow_assessment']['alternatives'] = [draft['workflow_assessment']['alternatives']]
    draft['next_step'].update(status='blocked', stop_decision='continue', blocker='Historical fixture occupancy; no resource grant')
    response.unlink()  # Unpublished fixture draft only.
    def caller(cfg, role, prompt, folder):
        return copy.deepcopy(draft) if role == 'a' else {'draft_digest': digest(draft),
            'verdict': 'approve', 'reason': 'Fixture bounded scientific proposal', 'challenges': []}
    assert worker.review(config, root, first, caller)['status'] == 'approved'
    blocked = public(config, root)
    assert blocked['status'] == 'study-blocked'
    assert blocked['reason'] == 'study_continuation_not_authorized_or_ready'
    before = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    handoff = prepare(config, root)
    assert handoff['parameters'] == {'learning_rate': .02}
    assert handoff['remaining_rounds'] == 1
    assert handoff['original_next_step_status'] == 'blocked'
    assert handoff['original_main_quality_passed'] and handoff['original_main_failures'] == {}
    assert handoff['execution_authority'] is False and handoff['computation_imported'] is False
    assert before == {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert not (tmp_path / 'remote/r02/process.json').exists()
    assert read_json(response)['next_step']['status'] == 'blocked'
    handoff_path = tmp_path / 'handoff.json'
    atomic_json(handoff_path, handoff)
    linked = copy.deepcopy(config)
    linked['devspace_study'].update(study_id='successor-' + handoff['handoff_digest'].removeprefix('sha256:')[:24],
        initial_parameters=handoff['parameters'], max_rounds=handoff['remaining_rounds'],
        predecessor={'path': str(handoff_path), 'sha256': file_digest(handoff_path)})
    validate_inheritance(linked)
    wider = copy.deepcopy(linked)
    wider['devspace_study']['max_rounds'] += 1
    with pytest.raises(ValueError, match='scope_must_narrow'):
        validate_inheritance(wider)
    wider = copy.deepcopy(linked)
    wider['devspace_execution']['plan']['resource']['uid'] = 'another-resource'
    with pytest.raises(ValueError, match='resource_scope_changed'):
        validate_inheritance(wider)
    wider = copy.deepcopy(linked)
    wider['devspace_execution']['plan']['inputs'][0]['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='training_inputs_changed'):
        validate_inheritance(wider)
    # A corrected fresh profile can take a never-acquired logical claim, while
    # the old frozen output remains untouched and cannot later submit a duplicate.
    from submission_guard import Guard
    old_root, new_root = tmp_path / 'unlaunched', tmp_path / 'corrected'
    old_cfg, new_cfg = copy.deepcopy(linked), copy.deepcopy(linked)
    for value, folder, name in ((old_cfg, old_root, 'old.json'), (new_cfg, new_root, 'new.json')):
        value['research_worker'].update(project_path=str(tmp_path / name), output=str(folder))
        value['devspace_study']['authorization']['study_digest'] = study.binding(value)
        atomic_json(tmp_path / name, value)
    old_root.mkdir()
    atomic_json(old_root / 'study-config.json', old_cfg)
    atomic_json(old_root / 'execution.json', {'status': 'proposal-review-stalled'})
    def guard(value, folder):
        return Guard({'provider': 'sdm-devspace-study', 'namespace': value['devspace_execution']['plan']['resource']['namespace'],
            'owner': value['devspace_execution']['owner'], 'request_key': value['devspace_study']['study_id'],
            'component_uid': value['devspace_execution']['plan']['resource']['uid'], 'configuration_digest': study.binding(value),
            'submission_registry': value['devspace_execution']['submission_registry']}, folder / 'execution.json')
    old_guard, new_guard = guard(old_cfg, old_root), guard(new_cfg, new_root)
    old_guard.load()
    old_guard.before_submit(old_root / 'execution.json')
    child_state = old_root / 'round-01/execution.json'
    atomic_json(child_state, {'launch_intent': True})
    with pytest.raises(ValueError, match='existing_execution_cannot_rebind'):
        rebind_unlaunched(old_cfg, old_root, tmp_path / 'new.json')
    child_state.unlink()
    request = old_root / 'round-01/proposal-review/control/model-calls/0001-a-0001/request.json'
    from research_model_receipts import seal
    atomic_json(request, seal({'schema': 'sdm-model-runner-envelope/v1', 'binding': 'unknown-fixture'}))
    with pytest.raises((ValueError, OSError)):
        rebind_unlaunched(old_cfg, old_root, tmp_path / 'new.json')
    request.unlink()
    original_files = {str(p): p.read_bytes() for p in old_root.rglob('*') if p.is_file()}
    rebound = rebind_unlaunched(old_cfg, old_root, tmp_path / 'new.json')
    assert rebound['status'] == 'rebound-unlaunched-only'
    assert original_files == {str(p): p.read_bytes() for p in old_root.rglob('*') if p.is_file()}
    assert not new_root.exists()
    with pytest.raises(ValueError, match='scope_changed'):
        old_guard.load()
    new_guard.load()
    assert rebind_unlaunched(old_cfg, old_root, tmp_path / 'new.json')['status'] == 'reused'
    with pytest.raises(ValueError, match='not_authorized_or_ready'):
        study.continuation(config['devspace_study'], blocked['history'][0], blocked['history'])
    entry = Path(study.__file__).with_name('run_project_pipeline.py')
    argv = [sys.executable, '-B', str(entry), '--project', config['research_worker']['project_path'],
            '--output', str(root), '--study-successor-plan']
    result = subprocess.run(argv, capture_output=True, encoding='utf-8', env={**os.environ, 'PYTHONUTF8': '1'}, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == handoff
    denied = subprocess.run(argv + ['--allow-devspace-live'], capture_output=True, encoding='utf-8', timeout=30)
    assert denied.returncode != 0
    consumption = root / 'round-01/review-consumption.json'
    old = consumption.read_bytes()
    changed = read_json(consumption)
    changed['execution_binding'] = 'another-binding'
    atomic_json(consumption, changed)
    with pytest.raises(ValueError, match='original_review_consumption_required'):
        prepare(config, root)
    consumption.write_bytes(old)
    result_path = root / 'round-01/artifacts/result.json'
    result_path.write_text('{}')
    with pytest.raises(ValueError, match='review_source_changed'):
        prepare(config, root)


def test_latest_user_fact_is_shared_immutably_with_both_roles(tmp_path):
    config = {'research_worker': {'live_context_required': True}, 'research_observation': {
        'version': 1, 'project_id': 'context-control', 'question_id': 'q-main',
        'store': str(tmp_path / 'records.sqlite3'), 'run_roots': [str(tmp_path / 'run')]}}
    def record(kind, node, text, actor='agent'):
        return observation.event(config, {'schema': 'sdm-research-event/v1', 'request_id': node,
            'id': node, 'kind': kind, 'question_id': 'q-main', 'text': text,
            'actor': {'kind': actor, 'source_ref': 'Synthetic control conversation; not actual consent'},
            **({'next_action': {'action': 'resume', 'authority': False},
                'relations': [{'relation': 'related_to', 'target': 'q-main', 'basis': 'observed'}]} if kind == 'decision' else {})})
    record('question', 'q-main', 'Fixture research goal')
    record('decision', 'hold', 'Other fixture session is using the resource', 'user')
    sources = {}
    original = snapshot(config, tmp_path / 'run', sources)
    old = original.read_bytes()
    assert snapshot(config, tmp_path / 'run', {}) == original
    record('decision', 'released', 'The other fixture session finished; resume after independent Guard checks', 'user')
    sources = {}
    latest = snapshot(config, tmp_path / 'run', sources)
    assert latest != original and original.read_bytes() == old
    shared = shared_evidence(config, {}, {'schema': 'control'}, sources, tmp_path / 'job')
    material = next(row for row in shared['raw_sources'] if row['path'] == str(latest))
    assert material['omitted_bytes'] == 0
    assert 'other fixture session finished' in material['text']
    assert shared['project']['research_worker']['live_context_required'] is True
    assert read_json(latest)['execution_authority'] is False
    assert not (tmp_path / 'run/execution.json').exists()
