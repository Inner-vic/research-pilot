"""Synthetic model-call declarations only: no provider/model/platform calls."""
from pathlib import Path
from unittest.mock import patch
import pytest
import proposal_review as r
from review_storage import atomic_json,read_json
from test_proposal_review import start,answer_local,revise,complete_local,accept_pro,URL


def config():
    return {'proposal_review':{'version':2,'backend':'model','min_rounds':2,'max_rounds':3}}


@pytest.mark.parametrize('stored_binding',[None,{}, {'project':'old','session_root':'unavailable'}])
def test_stored_pro_binding_does_not_select_pro_or_block_dual_model_review(tmp_path,stored_binding):
    cfg=config();cfg['external_review']=stored_binding
    assert r.pro_binding(cfg) is None
    accepted=complete_local(cfg,tmp_path)
    assert accepted['status']=='proposal-approved' and accepted['review_rounds']==2
    assert not accepted['execution_authority']
    assert r.gate(cfg,tmp_path)['receipt']==accepted['receipt']


@pytest.mark.parametrize('bad',['same-model','missing-model','same-call','missing-call'])
def test_role_names_cannot_replace_distinct_model_call_declarations(tmp_path,bad):
    cfg=config();wait=start(cfg,tmp_path);body=answer_local(wait)
    actor=body['reviewer']
    if bad=='same-model':actor['model']={'provider':' SYNTHETIC ','id':' MODEL-A '}
    elif bad=='missing-model':actor.pop('model',None)
    elif bad=='same-call':actor['call_ref']='synthetic-only/a-1'
    else:actor.pop('call_ref',None)
    atomic_json(Path(wait['response_path']),body)
    with pytest.raises(ValueError,match='model|call'):r.gate(cfg,tmp_path)
    assert not Path(wait['request_path']).with_name('accepted.json').exists()


def test_reusing_one_critic_call_for_two_turns_does_not_count_as_reassessment(tmp_path):
    cfg=config();wait=start(cfg,tmp_path);answer_local(wait);r.gate(cfg,tmp_path);revise(cfg,tmp_path)
    wait=r.gate(cfg,tmp_path);body=answer_local(wait,'approve')
    body['reviewer']['call_ref']='synthetic-only/b-1';atomic_json(Path(wait['response_path']),body)
    with pytest.raises(ValueError,match='distinct.*call'):r.gate(cfg,tmp_path)


def test_revised_proposal_requires_new_proposer_call(tmp_path):
    cfg=config();wait=start(cfg,tmp_path);answer_local(wait);r.gate(cfg,tmp_path);revise(cfg,tmp_path)
    p=read_json(tmp_path/'proposal.json');p['proposer']['call_ref']='synthetic-only/a-1'
    atomic_json(tmp_path/'proposal.json',p)
    wait=r.gate(cfg,tmp_path);answer_local(wait,'approve')
    with pytest.raises(ValueError,match='new_proposer_call'):r.gate(cfg,tmp_path)


def test_pro_is_an_explicit_optional_backend_and_retains_verified_transport(tmp_path):
    import pro_review_session as session
    cfg=config();cfg['proposal_review']['backend']='pro';cfg['external_review']={}
    with pytest.raises(ValueError,match='pro_binding'):r.gate(cfg,tmp_path/'incomplete')
    pro=tmp_path/'pro';session.bind(pro,'fixture','review',URL)
    cfg['external_review']={'project':'fixture','workflow':'review','session_root':str(pro)}
    root=tmp_path/'proposal';wait=start(cfg,root)
    assert wait['review_mode']=='pro' and 'response_path' not in wait
    accept_pro(cfg,wait);r.gate(cfg,root);revise(cfg,root)
    wait=r.gate(cfg,root);accept_pro(cfg,wait)
    assert r.gate(cfg,root)['status']=='proposal-approved'


def test_both_execution_entries_wait_for_dual_model_review_before_side_effects(tmp_path):
    from test_devspace_main_workflow import fixture
    import devspace_main_workflow as main
    import run_project_pipeline as entry
    cfg,root=fixture(tmp_path);cfg.update(config());cfg['external_review']={}
    assert main.execution(cfg,root)['status']=='awaiting-proposal'
    normal={'provider':'synthetic','supported_graph_revisions':[3],**config(),'external_review':{}}
    with patch.object(entry,'resolve_driver',side_effect=AssertionError('dispatch before A/B review')):
        assert entry.run(normal,tmp_path/'component')['status']=='awaiting-proposal'


def test_explicit_selection_is_not_an_unknown_backend_fallback(tmp_path):
    cfg=config();cfg['proposal_review']['backend']='unknown'
    with pytest.raises(ValueError,match='backend'):r.gate(cfg,tmp_path)


def test_rejected_v2_proposal_stops_immediately_without_filling_more_rounds(tmp_path):
    cfg=config();wait=start(cfg,tmp_path);answer_local(wait,'reject')
    result=r.gate(cfg,tmp_path)
    assert result['status']=='proposal-review-exhausted' and result['review_rounds']==1
    assert not result['execution_authority'] and Path(result['receipt']).is_file()
    again=r.gate(cfg,tmp_path)
    assert again['status']=='proposal-review-exhausted' and again['review_rounds']==1


def test_v2_loop_result_review_uses_selected_model_handoff_with_stored_pro(tmp_path):
    import loop_analysis as loop
    from test_generic_project_loop import response
    cfg=config();cfg.update(external_review={'old':'unavailable'},long_loop=True)
    packet={'remaining_rounds':0,'history':[],'project_metrics':[],
            'evidence':{'r1.evaluation.scores.rmse':0.3}}
    with patch.object(loop,'request',return_value=packet):
        with pytest.raises(loop.AnalysisPending,match='model_analysis_response_required'):
            loop.analyze([],cfg,tmp_path,lambda path,value:atomic_json(path,value))
        bound=read_json(tmp_path/'analysis-request.json')
        atomic_json(tmp_path/'analysis-response.json',response(bound,action='stop-analysis'))
        result=loop.analyze([],cfg,tmp_path,lambda path,value:atomic_json(path,value))
    assert result['analysis_source']=='model-bridge'
