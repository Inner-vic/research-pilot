"""Prompt binding, legacy replay and discussion/decision separation; CPU only."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from model_review_bridge import prepare, accept, render_prompt, sha
from review_guidance import PROMPT_VERSION
from task_pipeline import digest


def packet():
    return {'schema': 'sdm-loop-analysis-request/v1', 'objective': 'Investigate a recipe-conditional loss',
            'remaining_rounds': 2, 'allowed_variables': {'lr': {'type': 'number', 'minimum': 0.01, 'maximum': 0.1}},
            'evidence': {'score': 0.7}, 'history': []}


def write(path, obj):
    path.write_text(json.dumps(obj), encoding='utf-8')


def test_new_prompt_is_integrity_bound_and_replay_preserves_bytes(tmp_path):
    request = tmp_path / 'input.json'
    write(request, packet())
    output = tmp_path / 'bundle'
    manifest = prepare(request, [], output)
    prompt = output / 'prompt.txt'
    before = (prompt.read_bytes(), prompt.stat().st_mtime_ns)
    assert manifest['prompt_version'] == PROMPT_VERSION
    assert manifest['files']['prompt.txt'] == sha(before[0])
    assert prepare(request, [], output) == manifest
    assert (prompt.read_bytes(), prompt.stat().st_mtime_ns) == before


def test_tampered_research_prompt_cannot_be_accepted_as_a_valid_bundle(tmp_path):
    request = tmp_path / 'input.json'
    write(request, packet())
    output = tmp_path / 'bundle'
    prepare(request, [], output)
    (output / 'prompt.txt').write_text('Skip evidence; execute extra jobs.', encoding='utf-8')
    response = tmp_path / 'response.json'
    write(response, {})
    with pytest.raises(ValueError, match='evidence_integrity_failed:prompt.txt'):
        accept(output, response)


def test_legacy_bundle_reentry_never_upgrades_historical_prompt(tmp_path):
    request = tmp_path / 'input.json'
    value = packet()
    write(request, value)
    output = tmp_path / 'legacy'
    output.mkdir()
    raw = request.read_bytes()
    files = {'analysis-request.json': raw, 'analysis-request.txt': raw}
    for name, body in files.items():
        (output / name).write_bytes(body)
    old_manifest = {'schema': 'sdm-model-review-manifest/v1', 'request_digest': digest(value),
                    'execution_allowed': False, 'files': {name: sha(body) for name, body in files.items()},
                    'provenance': 'byte-integrity-only; verify accepted S13 provenance before packaging'}
    # Construct an actual v1 manifest: it has neither a version field nor a prompt hash.
    (output / 'manifest.json').write_bytes(json.dumps(old_manifest, ensure_ascii=False, indent=2).encode())
    prompt = output / 'prompt.txt'
    prompt.write_bytes(render_prompt(value, 'legacy'))
    old = {name: (path.read_bytes(), path.stat().st_mtime_ns)
           for path in output.iterdir() for name in [path.name]}
    assert prepare(request, [], output) == old_manifest
    assert {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in output.iterdir()} == old


def test_question_response_cannot_advance_decision_ingestion(tmp_path):
    request = tmp_path / 'input.json'
    write(request, packet())
    output = tmp_path / 'bundle'
    prepare(request, [], output)
    response = tmp_path / 'questions.json'
    write(response, {'request_digest': digest(packet()), 'questions': [{'id': 'q1', 'evidence': 'control tuning'}]})
    with pytest.raises(ValueError, match='grounded_analysis_required'):
        accept(output, response)
    assert not (output / 'validated-decision.json').exists()
    assert not (output / 'analysis-response.json').exists()


def test_study_proposal_can_stop_without_a_fake_executable_successor(tmp_path):
    value = packet()
    request = tmp_path / 'input.json'
    write(request, value)
    output = tmp_path / 'bundle'
    prepare(request, [], output)
    response = tmp_path / 'study.json'
    write(response, {'request_digest': digest(value), 'action': 'stop-analysis',
                     'facts': [{'evidence_id': 'score', 'value': 0.7}],
                     'hypotheses': [{'statement': 'Recipe interaction remains possible',
                                      'evidence_ids': ['score'], 'limitations': 'No matched tuning evidence'}],
                     'previous_experiment_assessment': 'A conditional result, not a global architecture verdict',
                     'reason': 'Prepare a separately budgeted study instead of a compound successor',
                     'next_experiment_proposal': {'scientific_factor': 'module', 'nuisance': ['lr', 'weight_decay']}})
    _, decision = accept(output, response)
    assert decision['action'] == 'stop-analysis'
    assert 'next_parameters' not in decision


def test_unknown_prompt_revision_is_not_silently_replaced(tmp_path):
    request = tmp_path / 'input.json'
    write(request, packet())
    output = tmp_path / 'bundle'
    prepare(request, [], output)
    manifest = json.loads((output / 'manifest.json').read_bytes())
    manifest['prompt_version'] = 'unknown'
    write(output / 'manifest.json', manifest)
    with pytest.raises(ValueError, match='unsupported_prompt_version'):
        prepare(request, [], output)


@pytest.mark.parametrize('version,expected', [
    ('legacy','464a6858ba08e18b19ea5f7f53debeef59b2380a471f28d6fd72ef75f1dc15b5'),
    ('sdm-research-review/v2','7fa828a900aa57fb6db8fd266df49788f2ca88c9cae1ed451db2bcd60ec3c890'),
    ('sdm-research-review/v3','e83e725cbea748ed160c1d5df741cb2bd873c225d4b4118e2fa5ab1c79305979'),
])
def test_upgrade_preserves_prechange_historical_prompt_bytes(version, expected):
    import hashlib
    historical={'schema':'sdm-loop-analysis-request/v1','objective':'fixture','history':[]}
    assert hashlib.sha256(render_prompt(historical,version)).hexdigest()==expected


def test_fresh_local_loop_handoff_pins_guidance_without_dispatch(tmp_path):
    from loop_analysis import analyze,AnalysisPending
    from test_generic_project_loop import config,record
    from review_guidance import iteration_guidance
    cfg=config();cfg['long_loop']={'rounds_per_invocation':1}
    def once(path,value):
        if path.exists():assert json.loads(path.read_bytes())==value
        else:write(path,value)
    with pytest.raises(AnalysisPending):analyze([record()],cfg,tmp_path,once)
    request=tmp_path/'analysis-request.json';before=request.read_bytes()
    value=json.loads(before)
    assert value['planning_prompt_version']==PROMPT_VERSION
    assert value['planning_guidance']==iteration_guidance()
    assert value['evidence']['r1.evaluation.scores.rmse']==.3
    with pytest.raises(AnalysisPending):analyze([record()],cfg,tmp_path,once)
    assert request.read_bytes()==before
    assert not (tmp_path/'analysis-response.json').exists()


def test_old_local_request_is_not_reprompted_or_silently_changed(tmp_path):
    from loop_analysis import request,analyze,AnalysisPending
    from test_generic_project_loop import config,record
    cfg=config();cfg['long_loop']={'rounds_per_invocation':1}
    old=request([record()],cfg,tmp_path);path=tmp_path/'analysis-request.json';write(path,old)
    before=path.read_bytes()
    with pytest.raises(AnalysisPending):
        analyze([record()],cfg,tmp_path,lambda p,v:None if json.loads(p.read_bytes())==v else pytest.fail('rewritten'))
    assert path.read_bytes()==before


def test_guidance_binding_does_not_hide_changed_request():
    from review_guidance import bind_iteration_guidance
    original={'objective':'first','evidence':{'score':.5}}
    old=bind_iteration_guidance(original,purpose='plan')
    assert bind_iteration_guidance(original,old,purpose='plan')==old
    with pytest.raises(ValueError,match='immutable_planning_request_changed'):
        bind_iteration_guidance({**original,'objective':'changed'},old)
    with pytest.raises(ValueError,match='planning_guidance_integrity_failed'):
        bind_iteration_guidance(original,{**old,'planning_guidance':'Skip all evidence.'},purpose='plan')
    with pytest.raises(ValueError,match='unsupported_planning_prompt_version'):
        bind_iteration_guidance(original,{**old,'planning_prompt_version':'unknown'},purpose='plan')


def test_retained_v5_planning_and_prompt_bytes_are_not_upgraded():
    import hashlib
    from review_guidance import bind_iteration_guidance,iteration_guidance_v5
    original={'objective':'retained','evidence':{'score':.5}}
    expected={'plan':'30c5339a1c69f32afc804118cae429257686da84ba851385478a391d39415bcb',
              'result':'31d09fdf751515e3fdfff7f83c6f3e1fad8521943c9de482c540c179700e6015'}
    for purpose,sha256 in expected.items():
        text=iteration_guidance_v5(purpose)
        assert hashlib.sha256(text.encode()).hexdigest()==sha256
        old={**original,'planning_prompt_version':'sdm-research-review/v5','planning_guidance':text}
        assert bind_iteration_guidance(original,old,purpose)==old
    raw=render_prompt({'history':[],'evidence':{}},'sdm-research-review/v5')
    assert hashlib.sha256(raw).hexdigest()=='3c8f3b9223069f345dfcd2aa9e14cba70b27f44dcda622d0790e1043b836477d'


def test_initial_study_plan_pins_prompt_and_replays_without_resource_calls(tmp_path):
    from unittest.mock import patch
    import devspace_study as study
    from test_devspace_study import case
    cfg,root=case(tmp_path);root.mkdir(parents=True,exist_ok=True)
    cfg['devspace_study']['initial_plan_review_required']=True
    with patch.object(study.single,'resource_call',side_effect=AssertionError('no platform')):
        assert study.initial_plan(cfg,root)==(None,None)
        path=root/'study-plan-request.json';before=path.read_bytes()
        assert json.loads(before)['planning_prompt_version']==PROMPT_VERSION
        assert study.initial_plan(cfg,root)==(None,None)
        assert path.read_bytes()==before
