"""CPU-only deliberation/receipt contracts; synthetic Pro receipts, zero model calls."""
import json
import time
from pathlib import Path
from unittest.mock import patch
import pytest
import proposal_review as r
from task_pipeline import digest
from review_storage import atomic_json,read_json


def config():return {'proposal_review':{'version':1,'min_rounds':2,'max_rounds':3}}


def proposal(cfg,revision=1):
    value={'schema':'sdm-proposal/v1','subject_digest':digest(r.subject(cfg)),'revision':revision,
        'proposer':{'role':'proposer','identity':'fixture-A','source_ref':'synthetic-test/A'},
        **{k:'Synthetic fixture reasoning: '+k for k in r.FIELDS},'evidence':[]}
    if (cfg.get('proposal_review') or {}).get('version') in {2,3}:
        value['proposer'].update(model={'provider':'synthetic','id':'model-a'},call_ref=f'synthetic-only/a-{revision}')
    return value


def response(packet,verdict='revise'):
    second=packet['evidence']['review_round']>1
    value={'schema':'sdm-proposal-review/v1','request_digest':digest(packet),
        'reviewer':{'role':'critic','identity':'fixture-B','source_ref':'synthetic-test/B'},
        'verdict':verdict,'rationale':'Fixture: demand a counterfactual, then verify the revision',
        'challenges':[] if second else [{'id':'comparison','severity':'major','question':'What outcome falsifies the hypothesis?'}],
        'resolutions':[{'challenge_id':'comparison','status':'resolved','reason':'Revision adds an explicit counterfactual'}] if second else []}
    if packet.get('proposal_review_contract',{}).get('distinct_models_required'):
        value['reviewer'].update(model={'provider':'synthetic','id':'model-b'},
                                 call_ref=f"synthetic-only/b-{packet['evidence']['review_round']}")
    return value


def start(cfg,root):
    pending=r.gate(cfg,root);assert pending['status']=='awaiting-proposal'
    atomic_json(root/'proposal.json',proposal(cfg))
    if (cfg.get('proposal_review') or {}).get('context_required'):
        import hashlib
        source=root/'fixture-config.json';atomic_json(source,cfg)
        atomic_json(root/'review-context.json',{'schema':'sdm-review-context/v1','subject_digest':digest(r.subject(cfg)),
            **{k:'Synthetic fixture '+k for k in ('goal','constraints','data','code_revision','configuration','history','unknowns')},
            'sources':[{'id':'config','kind':'config','path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}]})
    pending=r.gate(cfg,root);assert pending['status']=='awaiting-proposal-review'
    return pending


def answer_local(pending,verdict='revise'):
    packet=read_json(pending['request_path']);body=response(packet,verdict)
    if 'shared_context' in packet:
        body['evidence_assessment']={'source_ids':['config'],'judgment':'hypothesis','limitations':['Synthetic CPU fixture']}
        for c in body['challenges']:c.update(basis='Synthetic configuration comparison',resolution_criterion='Add explicit falsification outcome')
    atomic_json(Path(pending['response_path']),body);return body


def revise(cfg,root):
    p=proposal(cfg,2);p['addresses']=[{'challenge_id':'comparison','reason':'Add an outcome that would reject the hypothesis'}]
    if (cfg.get('proposal_review') or {}).get('context_required'):
        p['advice_dispositions']=[{'challenge_id':'comparison','decision':'accept','applicability':'Applies to fixture comparison','reason':'Add falsification outcome'}]
    p['expected_observations']='If the held-out result stays flat, reject this mechanism under the fixed recipe'
    atomic_json(root/'proposal.json',p)


def test_two_rounds_are_bound_idempotent_and_require_real_revision(tmp_path):
    cfg=config();wait=start(cfg,tmp_path);answer_local(wait)
    assert r.gate(cfg,tmp_path)['status']=='proposal-revision-required'
    # Open a second review, but do not allow acceptance of the unchanged proposal.
    again=r.gate(cfg,tmp_path);answer_local(again,'approve')
    with pytest.raises(ValueError,match='revision_not_applied'):r.gate(cfg,tmp_path)


def complete_local(cfg,root):
    wait=start(cfg,root);answer_local(wait);r.gate(cfg,root);revise(cfg,root)
    wait=r.gate(cfg,root);answer_local(wait,'approve');return r.gate(cfg,root)


def test_resume_approval_and_changed_plan_invalidates_it(tmp_path):
    cfg=config();accepted=complete_local(cfg,tmp_path)
    assert accepted['status']=='proposal-approved' and accepted['review_rounds']==2
    assert not accepted['execution_authority'] and Path(accepted['receipt']).is_file()
    assert r.gate(cfg,tmp_path)['receipt']==accepted['receipt']
    p=read_json(tmp_path/'proposal.json');p['hypothesis']='Changed after approval';atomic_json(tmp_path/'proposal.json',p)
    with pytest.raises(ValueError,match='approved_proposal_changed'):r.gate(cfg,tmp_path)
    cfg['new_execution_revision']=2
    with pytest.raises(ValueError,match='execution_subject'):r.gate(cfg,tmp_path)


@pytest.mark.parametrize('bad',['critic-is-author','unclosed','redefined','tampered-round'])
def test_strict_review_and_history_integrity(tmp_path,bad):
    cfg=config();wait=start(cfg,tmp_path);body=answer_local(wait)
    if bad=='critic-is-author':
        body['reviewer']['identity']='fixture-A';atomic_json(Path(wait['response_path']),body)
        with pytest.raises(ValueError,match='separate_proposer'):r.gate(cfg,tmp_path)
        return
    r.gate(cfg,tmp_path)
    if bad=='tampered-round':
        path=Path(wait['request_path']).with_name('accepted.json');v=read_json(path);v['approved']=True;atomic_json(path,v)
        with pytest.raises(ValueError,match='round_changed'):r.gate(cfg,tmp_path)
        return
    revise(cfg,tmp_path);wait=r.gate(cfg,tmp_path);body=answer_local(wait,'approve')
    if bad=='unclosed':body['resolutions']=[]
    else:body['challenges']=[{'id':'comparison','severity':'minor','question':'Downgrade an old blocker?'}]
    atomic_json(Path(wait['response_path']),body)
    with pytest.raises(ValueError,match='critical_challenges|redefined'):r.gate(cfg,tmp_path)


def test_rejection_is_persistent_and_does_not_mint_authority(tmp_path):
    cfg=config();wait=start(cfg,tmp_path);answer_local(wait,'reject');r.gate(cfg,tmp_path)
    state=r.gate(cfg,tmp_path)
    assert state['status']=='proposal-review-exhausted' and not state['execution_authority']


@pytest.mark.parametrize('binding',[None,{}, {'project':'p','workflow':'w','session_root':'relative'}])
def test_incomplete_pro_cannot_fall_back_to_local(binding,tmp_path):
    cfg=config();cfg['proposal_review']['pro']=binding
    with pytest.raises(ValueError,match='pro_binding|session_root'):r.gate(cfg,tmp_path)


def test_legacy_configs_and_configured_pro_defaults(tmp_path):
    assert r.gate({},tmp_path) is None
    cfg={'external_review':{'project':'p','workflow':'w','session_root':str(tmp_path/'pro')}}
    assert r.settings(cfg)['min_rounds']==2
    wait=start(cfg,tmp_path)
    atomic_json(Path(wait['request_path']).with_name('review-response.json'),{})
    with pytest.raises(ValueError,match='cannot_use_local'):r.gate(cfg,tmp_path)


URL='https://chatgpt.com/c/11111111-1111-1111-1111-111111111111'


def accept_pro(cfg,wait):
    """Exercise actual workflow validation with clearly synthetic transport evidence."""
    import pro_review_workflow as flow
    import pro_review_session as session
    root=Path(cfg['external_review']['session_root']);packet=read_json(wait['request_path'])
    state=session.check(root,URL)
    context={'schema':'sdm-pro-review-context/v1','project':'fixture','workflow':'review',
        'fixed_conditions':{'scope':'synthetic proposal fixture'},'excluded_explanations':[],
        'remaining_unknowns':['Scientific utility is not proved by this test'],
        'current_authorization':{'execution_authorized':False,'model_inference_allowed':False,'training_submission_allowed':False},
        'new_experiment_observations':False,'gap_matrix':[]}
    atomic_json(root/'context-input.json',context)
    item=flow.prepare(root,wait['request_path'],root/'context-input.json',[],URL,state['revision'])
    receipt={'url':URL,'model':'Pro','verified_visible':True,'request_digest':item['request_digest'],'qualification':'synthetic fixture; no Pro model called'}
    atomic_json(root/'receipt-input.json',receipt);flow.mark_delivery(root,root/'receipt-input.json')
    body={'request_digest':item['request_digest'],'session_context_revision':state['revision'],
        'session_context_digest':state.get('context_digest'),
        'facts':[{'evidence_id':'proposal_digest','value':packet['evidence']['proposal_digest']}],
        'hypotheses':[{'statement':'Counterfactual may distinguish alternatives','evidence_ids':['proposal_digest'],'limitations':'Synthetic contract validation only'}],
        'previous_experiment_assessment':'No training performed','reason':'Review only','action':'stop-analysis',
        'continuation_acknowledgement':{'status':'continued','objective':packet['objective'],
            'fixed_conditions':context['fixed_conditions'],'excluded_explanations':[],
            'remaining_unknowns':context['remaining_unknowns'],'current_scope':'review only','no_new_observations':True},
        'evidence_gap_matrix':[], 'proposal_review':response(packet,'approve' if state['revision'] else 'revise')}
    atomic_json(root/'response-input.json',body)
    flow.ingest(root,root/'response-input.json',root/'receipt-input.json',item['review_id'])
    flow.plan(root,item['review_id'])
    flow.export_loop_result(root,item['review_id'],wait['request_path'],wait['external_review_result'])
    return item,packet


def test_two_pro_turns_and_historical_receipt_verification(tmp_path):
    import pro_review_session as session
    import pro_review_workflow as flow
    root=tmp_path/'dialogue';pro=tmp_path/'pro';session.bind(pro,'fixture','review',URL)
    cfg=config();cfg['external_review']={'project':'fixture','workflow':'review','session_root':str(pro)}
    wait=start(cfg,root);first,packet=accept_pro(cfg,wait)
    assert r.gate(cfg,root)['status']=='proposal-revision-required'
    revise(cfg,root);wait=r.gate(cfg,root);accept_pro(cfg,wait)
    assert r.gate(cfg,root)['status']=='proposal-approved'
    assert r.gate(cfg,root)['review_rounds']==2
    # General result handoff still rejects stale session revisions by default.
    with pytest.raises(ValueError,match='stale_session_revision'):flow.source_decision(pro,first['review_id'],packet)
    assert flow.source_decision(pro,first['review_id'],packet,allow_historical=True)['proposal_review']['verdict']=='revise'
    p=pro/'records/0001.json';event=read_json(p);event['response_sha256']='tampered';atomic_json(p,event)
    with pytest.raises(ValueError,match='chain_failed|head_mismatch'):r.gate(cfg,root)


def test_devspace_gate_precedes_resource_calls_and_durable_intent(tmp_path):
    from test_devspace_execution import native_case
    import devspace_execution as d
    cfg,root=native_case(tmp_path);cfg['proposal_review']={'version':1}
    with patch.object(d,'resource_call',side_effect=AssertionError('resource query before review')):
        assert d.execution(cfg,root,allow_live=True)['status']=='awaiting-proposal'
    assert not (root/'execution.json').exists()


def test_main_and_component_entry_gate_precedes_driver_dispatch(tmp_path):
    from test_devspace_main_workflow import fixture
    import devspace_main_workflow as main
    import run_project_pipeline as entry
    cfg,root=fixture(tmp_path);cfg['proposal_review']={'version':1}
    assert main.execution(cfg,root)['status']=='awaiting-proposal'
    normal={'provider':'synthetic','supported_graph_revisions':[3],'proposal_review':{'version':1}}
    with patch.object(entry,'resolve_driver',side_effect=AssertionError('driver dispatched before review')):
        assert entry.run(normal,tmp_path/'component')['status']=='awaiting-proposal'


def test_cleanup_resumes_existing_allocation_without_new_dialogue(tmp_path):
    from test_devspace_execution import setup_case
    import devspace_execution as d
    cfg,root=setup_case(tmp_path)
    d.execution(cfg,root)
    # A later request for review must not prevent cancellation of owned work.
    cfg['proposal_review']={'version':1}
    with patch.object(r,'gate',side_effect=AssertionError('cleanup waited for dialogue')):
        until=time.monotonic()+10
        while True:
            state=d.execution(cfg,root,force_cleanup=True)
            if state['stop_verified'] and state['process_terminal'] or time.monotonic()>=until:break
            time.sleep(.05)
    assert state['stop_verified'] and state['process_terminal']


def test_v4_prompt_bytes_and_local_planning_replay_are_preserved():
    import hashlib
    from model_review_bridge import render_prompt
    from review_guidance import bind_iteration_guidance,iteration_guidance_v4,PROMPT_VERSION
    assert hashlib.sha256(render_prompt({'history':[],'evidence':{}},'sdm-research-review/v4')).hexdigest()=='fa0b9c19fdbaf020dca6be72d1227098c94fef18448ba398662280dfb267eb4e'
    old={'objective':'old','planning_prompt_version':'sdm-research-review/v4','planning_guidance':iteration_guidance_v4()}
    assert bind_iteration_guidance({'objective':'old'},old)==old
    assert bind_iteration_guidance({'objective':'new'})['planning_prompt_version']==PROMPT_VERSION
