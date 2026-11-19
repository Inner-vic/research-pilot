"""Narrative projections must not manufacture governance acceptance."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from lifecycle_view import catalog, validate_catalog, project as view, describe
from research_progress import project, publish
from research_dashboard_data import DashboardData
from task_pipeline import digest
from review_storage import atomic_json


def fixture(tmp_path, stage='reviewing-code'):
    root=tmp_path/'study';(root/'component-pipeline').mkdir(parents=True)
    cfg={'root':str(root),'project_id':'sdm-narrative-test','component_pipeline':{
        'version':4,'objective':'Synthetic projection, not research permission',
        'decision_policy':{'target':{'metric':'validation.macro_f1','value':.9}}}}
    state={'config_digest':digest(cfg),'status':'running','rounds':[],
           'attempts':[{'id':'original-attempt','stage':stage}]}
    atomic_json(root/'component-pipeline/pipeline.json',state)
    return cfg,state,root


def test_current_activities_have_explicit_original_provenance():
    original=json.loads((Path(__file__).resolve().parents[1]/'assets/lifecycle-steps.json').read_bytes())
    data=catalog()
    assert data['schema']=='sdm-lifecycle-narrative/v2'
    assert data['numbering']=='researchpilot-workflow'
    assert len(data['steps'])==13  # Current release, not a validator requirement.
    assert sorted(i for r in data['steps'] for i in r['sdm_steps'])==[r['step'] for r in original['steps']]
    assert data['steps'][5]['sdm_steps']==[9,10]
    assert all(r.get('inputs') and r.get('outputs') and r.get('evolution') and r.get('code') for r in data['steps'])
    assert all('gate' not in r for r in data['steps'])


def test_catalog_supports_added_activity_and_new_phase_without_21_or_five_limit():
    data=copy.deepcopy(catalog());last=len(data['steps'])+1;phase=len(data['phases'])+1
    data['steps'].append({'step':last,'phase':phase,'title':'新增职责示例','sdm_steps':[]})
    data['phases'].append({'id':phase,'title':'新增分组','short_title':'新增分组','steps':[last]})
    assert len(validate_catalog(data)['steps'])==14


def test_catalog_supports_deleted_inapplicable_activity_and_regrouping():
    data=copy.deepcopy(catalog());data['steps'].pop()
    data['phases']=[{'id':1,'title':'已整合分组','short_title':'整合','steps':list(range(1,len(data['steps'])+1))}]
    for row in data['steps']:row['phase']=1
    assert len(validate_catalog(data)['steps'])==12


@pytest.mark.parametrize('damage',['duplicate-step','overlapping-phase','unknown-origin','bool-origin','empty-title'])
def test_catalog_rejects_broken_identities_without_scientific_decision(damage):
    data=copy.deepcopy(catalog())
    if damage=='duplicate-step':data['steps'][1]['step']=1
    elif damage=='overlapping-phase':data['phases'][1]['steps'].insert(0,1)
    elif damage=='unknown-origin':data['steps'][0]['sdm_steps']=[22]
    elif damage=='bool-origin':data['steps'][0]['sdm_steps']=[True]
    else:data['steps'][0]['title']=''
    with pytest.raises(ValueError,match='sdm_narrative_catalog_invalid'):validate_catalog(data)


@pytest.mark.parametrize('stage',['watching','research-goal-met','research-stopped','needs-attention','user-paused','unknown-new-state'])
def test_stage_or_terminal_state_never_yields_scientific_acceptance(stage):
    value=view(stage)
    assert value['acceptance_inferred'] is False
    assert not any(key in json.dumps(value) for key in ('"passed"','"completed"','"transition_accepted"','"scientific_outcome"'))
    if stage in ('needs-attention','user-paused','unknown-new-state'):
        assert value['active_phase'] is None and not value['related_steps']
    if stage=='research-goal-met':
        assert [r['step'] for r in value['related_steps']]==[13]  # Activity association, never a D05 receipt.


def test_initial_science_and_code_describe_their_actual_different_duties():
    assert view('reviewing-initial','science')['active_phase']==2
    code=view('reviewing-initial','code')
    assert code['active_phase']==3 and [r['step'] for r in code['related_steps']]==[7]


def test_progress_and_dashboard_consume_same_projection_without_mutating_state(tmp_path):
    cfg,state,root=fixture(tmp_path)
    before=copy.deepcopy((cfg,state));raw=(root/'component-pipeline/pipeline.json').read_bytes()
    progress=publish(cfg,state)
    study=DashboardData(tmp_path).study({},cfg,root)
    assert progress['lifecycle_view']==study['lifecycle_view']==view('reviewing-code')
    report=(root/'component-pipeline/PROGRESS.md').read_text(encoding='utf8')
    assert describe(progress['lifecycle_view']) in report and '不等于 D05' in report
    assert json.loads((root/'component-pipeline/progress.json').read_bytes())['lifecycle_view']==progress['lifecycle_view']
    assert (cfg,state)==before and (root/'component-pipeline/pipeline.json').read_bytes()==raw


def test_configuration_integrity_still_blocks_before_projection(tmp_path):
    cfg,state,root=fixture(tmp_path);cfg['project_id']='different-study'
    with pytest.raises(ValueError,match='progress_configuration_changed'):project(cfg,state)
    with pytest.raises(ValueError,match='dashboard_study_identity_changed'):DashboardData(tmp_path).study({},cfg,root)
    assert not (root/'component-pipeline/PROGRESS.md').exists()


def test_corrupt_result_is_not_replaced_by_a_narrative_label(tmp_path):
    cfg,state,root=fixture(tmp_path,'result-ready');p=root/'result-ready.json';p.write_bytes(b'{}')
    state['rounds']=[{'result':{'path':str(p),'sha256':hashlib.sha256(b'original').hexdigest()}}]
    with pytest.raises(ValueError,match='progress_original_result_reference_changed'):publish(cfg,state)
    assert not (root/'component-pipeline/PROGRESS.md').exists()
