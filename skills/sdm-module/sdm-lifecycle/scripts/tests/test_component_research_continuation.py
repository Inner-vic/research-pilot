"""Synthetic control records only; no ML/platform/model calls."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch
import pytest

import component_research as component
import research_replan
import research_implementation as implementation
from review_storage import atomic_json,read_json
from task_pipeline import digest


def ref(path):
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def required(root):
    folder=root/'official';folder.mkdir()
    (folder/'SKILL.md').write_text('---\nname: lpai\n---\nFixture only')
    atomic_json(folder/'manifest.json',{'name':'lpai','version':'fixture','build_sha':'fixture'})
    def skill_ref(path):
        value=ref(path);value['sha256']='sha256:'+value['sha256'];return value
    return {'version':1,'platform':'lpai','required_skills':[{'name':'lpai',
        'skill':skill_ref(folder/'SKILL.md'),'manifest':skill_ref(folder/'manifest.json')}]}


def result(root,index,pred):
    folder=root/('round-'+str(index));folder.mkdir()
    y=[0,0,1,1];cm=[[sum(a==i and b==j for a,b in zip(y,pred)) for j in (0,1)] for i in (0,1)]
    f1=sum(2*cm[i][i]/max(1,2*cm[i][i]+cm[i][1-i]+cm[1-i][i]) for i in (0,1))/2
    split={'protocol':'control-v1','train_ids':[10,11],'validation_ids':[0,1,2,3],'test_used':False}
    atomic_json(folder/'split.json',split)
    report={'schema':'sdm-component-result/v1','protocol':'control-v1','source_sha256':'b'*64,
            'split_sha256':ref(folder/'split.json')['sha256'],'selection':'fixed validation macro_f1 selection',
            'config':{'epochs':2,'seed':42},'test_used':False,'validation':{'macro_f1':f1,'accuracy':sum(a==b for a,b in zip(y,pred))/4}}
    atomic_json(folder/'result.json',report)
    atomic_json(folder/'predictions.json',{'y_true':y,'y_pred':pred,'row_ids':[0,1,2,3]})
    (folder/'checkpoint.pt').write_bytes(b'synthetic evidence, not model weights')
    ready={'schema':'sdm-component-result-ready/v1','valid_training_round':True,
           'uid':'00000000-0000-4000-8000-'+str(index).zfill(12),'primary_metric':f1,'artifact_root':str(folder),
           'artifact_sha256':{p.name:ref(p)['sha256'] for p in folder.iterdir()}}
    atomic_json(folder/'ready.json',ready)
    return {'result':ref(folder/'ready.json')}


def direction(action='revise-plan'):
    return {'action':action,'change_level':'model' if action=='revise-plan' else 'none',
        'goal_status':'met' if action=='goal-met' else 'unmet','evidence_ids':['component-source-0'],
        'assumption':'control hypothesis','intervention':'a concrete model intervention',
        'expected_decision_value':'distinguish a capacity limitation from optimizer behavior'}


def successor_plan(root,index,parent):
    folder=root/('review-'+str(index));folder.mkdir()
    proposal={'research_revision':direction(),'comparison':'fixed split and metric','outcome_actions':'revise after actual result'}
    accepted={'approved':True,'proposal':proposal,'response':{'verdict':'approve'}}
    atomic_json(folder/'accepted.json',accepted)
    plan={'schema':'sdm-research-plan/v1','research_goal_met':False,'execution_authority':False,'budget_renewed':False,
          'parent_result_sha256':parent['result']['sha256'],'research_revision':direction(),
          'proposal':proposal,'accepted_review':str(folder/'accepted.json'),
          'accepted_review_ref':ref(folder/'accepted.json'),'evidence_ids':['component-source-0']}
    atomic_json(folder/'research-plan.json',plan)
    return ref(folder/'research-plan.json')


def test_goal_requires_three_real_records_two_parent_linked_revisions_and_final_improvement(tmp_path):
    a=result(tmp_path,1,[0,1,1,0]);b=result(tmp_path,2,[0,0,1,0]);c=result(tmp_path,3,[0,0,1,1])
    b['accepted_plan']=successor_plan(tmp_path,2,a);c['accepted_plan']=successor_plan(tmp_path,3,b)
    assert not component.progress([a])['goal_met']
    assert not component.progress([a,b])['goal_met']
    final=component.progress([a,b,c])
    assert final['goal_met'] and final['valid_training_rounds']==3 and final['result_driven_plan_revisions']==2
    assert final['primary_metric_delta']>0 and not final['execution_authority']


def test_bad_final_round_is_not_hidden_by_best_intermediate_score(tmp_path):
    a=result(tmp_path,1,[0,0,1,0]);b=result(tmp_path,2,[0,0,1,1]);c=result(tmp_path,3,[1,1,0,0])
    b['accepted_plan']=successor_plan(tmp_path,2,a);c['accepted_plan']=successor_plan(tmp_path,3,b)
    assert not component.progress([a,b,c])['goal_met']


def test_duplicates_failed_rounds_and_tampered_artifacts_do_not_count(tmp_path):
    a=result(tmp_path,1,[0,1,1,0])
    with pytest.raises(ValueError,match='cannot_count_twice'):component.progress([a,a])
    ready=read_json(a['result']['path']);ready['valid_training_round']=False;atomic_json(a['result']['path'],ready)
    a['result']=ref(Path(a['result']['path']))
    with pytest.raises(ValueError,match='verified_component_training'):component.progress([a])


def test_protocol_change_requires_new_comparison_not_a_claimed_improvement(tmp_path):
    a=result(tmp_path,1,[0,1,1,0]);b=result(tmp_path,2,[0,0,1,1]);b['accepted_plan']=successor_plan(tmp_path,2,a)
    ready=read_json(b['result']['path']);folder=Path(ready['artifact_root'])
    report=read_json(folder/'result.json');report['selection']='changed selection rule';atomic_json(folder/'result.json',report)
    ready['artifact_sha256']['result.json']=ref(folder/'result.json')['sha256'];atomic_json(b['result']['path'],ready)
    b['result']=ref(Path(b['result']['path']))
    with pytest.raises(ValueError,match='protocol_changed'):component.progress([a,b])


def test_diagnostic_fit_is_preserved_without_counting_as_a_plan_improvement(tmp_path):
    a=result(tmp_path,1,[0,1,1,0]);b=result(tmp_path,2,[0,0,1,1]);b['accepted_plan']=successor_plan(tmp_path,2,a)
    plan_path=Path(b['accepted_plan']['path']);plan=read_json(plan_path)
    plan['research_revision']['action']='diagnose';plan['research_revision']['change_level']='data-measurement'
    plan['proposal']['research_revision']=plan['research_revision']
    receipt_path=Path(plan['accepted_review']);receipt=read_json(receipt_path);receipt['proposal']=plan['proposal']
    atomic_json(receipt_path,receipt);plan['accepted_review_ref']=ref(receipt_path);atomic_json(plan_path,plan)
    b['accepted_plan']=ref(plan_path)
    value=component.progress([a,b]);assert value['valid_training_rounds']==2 and value['result_driven_plan_revisions']==0


def test_original_replan_routes_component_raw_evidence_to_original_ab_gate(tmp_path):
    a=result(tmp_path,1,[0,1,1,0]);policy=required(tmp_path)
    config={'platform_requirements':policy,'proposal_review':{'version':3,'backend':'model','context_required':True},
        'research_replan':{'version':2,'parent_route':'lpai-component','result_contract':'sdm-binary-classification/v1',
            'objective':'three rounds and final improvement','rounds':[a]}}
    output=tmp_path/'replan'
    def approved(cfg,root,query):
        context=read_json(root/'review-context.json');ids=[s['id'] for s in context['sources']]
        revision=direction();revision['evidence_ids']=[ids[0]]
        path=root/'accepted.json';atomic_json(path,{'approved':True,'proposal':{'research_revision':revision,
            'comparison':'same real split','outcome_actions':'test actual next result'},'response':{'verdict':'approve'}})
        return {'status':'proposal-approved','receipt':str(path)}
    with patch('proposal_review.gate',side_effect=approved) as gate,patch.object(research_replan,'parent_evidence') as old:
        value=research_replan.advance(config,output)
    old.assert_not_called();assert gate.call_count==1
    assert value['agent_continuation_required'] and not value['research_complete']
    request=read_json(output/'implementation-request.json')
    assert request['platform_requirements']==policy and request['parent_route']=='lpai-component'
    assert not request['execution_authority']


@pytest.mark.parametrize('ending',['diagnose','refine-current','revise-plan','continue-plan','stop-research','goal-met'])
def test_failed_component_can_enter_original_diagnosis_without_inventing_a_round(tmp_path,ending):
    policy=required(tmp_path);failure=tmp_path/'failure.json'
    atomic_json(failure,{'schema':'sdm-component-diagnosis-ready/v1','valid_training_round':False,
                        'uid':'failed-control-task','platform_status':'failed','logs':[{'text':'synthetic pre-fit error'}]})
    config={'platform_requirements':policy,'proposal_review':{'version':3,'backend':'model','context_required':True},
        'research_replan':{'version':2,'parent_route':'lpai-component','result_contract':'sdm-binary-classification/v1',
            'objective':'recover actual failure then complete three valid rounds','rounds':[],'parent_diagnosis':ref(failure),'preparation_recovery':{'version':1}}}
    output=tmp_path/'replan'
    def approved(cfg,root,query):
        revision=direction(ending)
        if ending=='refine-current':revision['change_level']='tuning'
        path=root/'accepted.json';atomic_json(path,{'approved':True,'proposal':{'research_revision':revision,
            'comparison':'retain original evaluation','outcome_actions':'no fit counts until verified'},'response':{'verdict':'approve'}})
        return {'status':'proposal-approved','receipt':str(path)}
    with patch('proposal_review.gate',side_effect=approved):
        if ending=='goal-met':
            with pytest.raises(ValueError,match='goal_not_reached'):research_replan.advance(config,output)
        else:
            value=research_replan.advance(config,output)
            assert value['status']==research_replan.DIRECTION_STATUSES[ending]
            assert value['goal_assessment']['valid_training_rounds']==0
            assert value['goal_assessment']['baseline_primary_metric'] is None
            assert not value['research_complete']
            assert (output/'implementation-request.json').exists()==(ending in research_replan.IMPLEMENTATION_ACTIONS)


def implementation_fixture(root):
    policy=required(root);workspace=root/'model-workspace';workspace.mkdir()
    accepted=root/'accepted.json';atomic_json(accepted,{'approved':True,'proposal':{},'response':{'verdict':'approve'}})
    reviewcfg=root/'review-project.json';atomic_json(reviewcfg,{'platform_requirements':policy})
    plan={'schema':'sdm-research-plan/v1','research_goal_met':False,'budget_renewed':False,'execution_authority':False,
          'platform_requirements':policy,'research_revision':direction(),'proposal':{},'accepted_review':str(accepted),'accepted_review_ref':ref(accepted),
          'review_project_ref':ref(reviewcfg),'review_root':str(root/'review')}
    atomic_json(root/'plan.json',plan)
    request={'schema':'sdm-research-implementation-request/v1','parent_plan_digest':digest(plan),
             'execution_authority':False,'platform_requirements':policy}
    atomic_json(root/'request.json',request)
    protected=root/'evaluator.py';protected.write_text('raise AssertionError("must not execute locally")')
    contract={'schema':'sdm-component-implementation-contract/v1',
              'comparison_identity':{'protocol':'control','source_sha256':'b'*64,'split_sha256':'c'*64,'selection':'fixed'},
              'execution_scope':{'provider':'lpai','namespace':'ns','owner':'owner@example.com','component_uid':'component'},
              'allowed_files':['model.py','parameters.json'],'parameters_file':'parameters.json','protected_inputs':[ref(protected)]}
    atomic_json(root/'contract.json',contract)
    cfg={'platform_requirements':policy,'research_implementation':{'version':1,'approved_plan':ref(root/'plan.json'),
         'implementation_request':ref(root/'request.json'),'review_project':ref(reviewcfg),'review_root':str(root/'review'),
         'workspace':str(workspace),'execution_contract':ref(root/'contract.json')}}
    reviewer=lambda c,r:{'status':'proposal-approved','receipt':str(accepted)}
    return cfg,reviewer,plan,workspace,contract


def test_approved_plan_becomes_native_job_and_frozen_profile_without_execution(tmp_path):
    cfg,reviewer,plan,workspace,contract=implementation_fixture(tmp_path);root=tmp_path/'handoff'
    prepared=implementation.prepare(cfg,root,reviewer)
    job=read_json(prepared['job_path']);assert not job['execution_authority']
    (workspace/'model.py').write_text('raise AssertionError("syntax only, never import/execute in this control")')
    atomic_json(workspace/'parameters.json',{'comparison_identity':contract['comparison_identity'],
        'execution_scope':contract['execution_scope'],
        'platform_requirements':cfg['platform_requirements']})
    atomic_json(workspace/'implementation.json',{'schema':'sdm-research-implementation-output/v1','status':'implemented',
        'job_id':job['job_id'],'parent_plan_digest':digest(plan),
        'contract_sha256':cfg['research_implementation']['execution_contract']['sha256'],
        'files':[{'path':name,'sha256':ref(workspace/name)['sha256']} for name in contract['allowed_files']]})
    frozen=implementation.freeze(cfg,root)
    assert frozen['syntax_verified'] and frozen['code_review_required']
    assert not frozen['execution_authority'] and not frozen['model_functional_verification'] and frozen['platform_actions']==0
    assert frozen['platform_requirements']==cfg['platform_requirements']
    assert implementation.freeze(cfg,root)==frozen


def test_unapproved_or_changed_policy_cannot_prepare_implementation(tmp_path):
    cfg,reviewer,plan,workspace,contract=implementation_fixture(tmp_path)
    value=implementation.prepare(cfg,tmp_path/'pending',lambda c,r:{'status':'proposal-revision-required'})
    assert value['implementation_started'] is False
    assert not (tmp_path/'pending/implementation-job.json').exists()
    broken=copy.deepcopy(cfg);broken['platform_requirements']['required_skills'][0]['name']='sdm-lpai'
    with pytest.raises(ValueError):implementation.prepare(broken,tmp_path/'bad',reviewer)


def test_changed_accepted_receipt_blocks_before_any_review_model_call(tmp_path):
    cfg,reviewer,plan,workspace,contract=implementation_fixture(tmp_path)
    atomic_json(Path(plan['accepted_review']),{'approved':False})
    with patch('proposal_review.gate') as gate,pytest.raises(ValueError,match='evidence_changed'):
        implementation.prepare(cfg,tmp_path/'handoff')
    gate.assert_not_called()


def test_public_implementation_entry_does_not_mix_with_execution_options(tmp_path):
    import run_project_pipeline
    cfg,reviewer,plan,workspace,contract=implementation_fixture(tmp_path)
    path=tmp_path/'project.json';atomic_json(path,cfg)
    argv=['sdm','--project',str(path),'--output',str(tmp_path/'handoff'),'--research-implementation','--allow-devspace-live']
    with patch.object(sys,'argv',argv),patch.object(implementation,'prepare') as call,pytest.raises(SystemExit) as stopped:
        run_project_pipeline.main()
    assert stopped.value.code==2;call.assert_not_called()


def test_public_implementation_entry_uses_original_adapter(tmp_path):
    import run_project_pipeline
    cfg,reviewer,plan,workspace,contract=implementation_fixture(tmp_path)
    path=tmp_path/'project.json';atomic_json(path,cfg)
    argv=['sdm','--project',str(path),'--output',str(tmp_path/'handoff'),'--research-implementation']
    with patch.object(sys,'argv',argv),patch.object(implementation,'prepare',return_value={'status':'control'}) as call:
        run_project_pipeline.main()
    assert call.call_args.args==(cfg,tmp_path/'handoff')


@pytest.mark.parametrize('change',['evaluation','required-skill','execution-scope','unlisted-file','protected-input','nonfinite','invalid-file-reference'])
def test_model_output_cannot_change_evaluation_or_expand_frozen_files(tmp_path,change):
    cfg,reviewer,plan,workspace,contract=implementation_fixture(tmp_path);root=tmp_path/'handoff'
    prepared=implementation.prepare(cfg,root,reviewer);job=read_json(prepared['job_path'])
    (workspace/'model.py').write_text('raise AssertionError("never execute")')
    parameters={'comparison_identity':copy.deepcopy(contract['comparison_identity']),
                'execution_scope':copy.deepcopy(contract['execution_scope']),
                'platform_requirements':copy.deepcopy(cfg['platform_requirements'])}
    if change=='evaluation':parameters['comparison_identity']['selection']='select on test set'
    if change=='required-skill':parameters['platform_requirements']['required_skills'][0]['name']='sdm-lpai'
    if change=='execution-scope':parameters['execution_scope']['namespace']='other-owner-namespace'
    if change=='protected-input':Path(contract['protected_inputs'][0]['path']).write_text('changed evaluator')
    atomic_json(workspace/'parameters.json',parameters)
    if change=='nonfinite':
        path=workspace/'parameters.json';path.write_text(path.read_text().rstrip()[:-1]+',"learning_rate":NaN}')
    rows=[{'path':name,'sha256':ref(workspace/name)['sha256']} for name in contract['allowed_files']]
    if change=='unlisted-file':rows.append({'path':'../outside.py','sha256':'f'*64})
    if change=='invalid-file-reference':rows.append(None)
    atomic_json(workspace/'implementation.json',{'schema':'sdm-research-implementation-output/v1','status':'implemented',
        'job_id':job['job_id'],'parent_plan_digest':digest(plan),
        'contract_sha256':cfg['research_implementation']['execution_contract']['sha256'],'files':rows})
    with pytest.raises(ValueError):implementation.freeze(cfg,root)
    assert not (root/'implemented-profile.json').exists()
