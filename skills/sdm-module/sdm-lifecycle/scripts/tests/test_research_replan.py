"""A closed local experiment must continue goal-level research, not fake success."""
import copy
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

import devspace_study as study
import devspace_main_workflow as main
import research_worker as worker
import proposal_review
from model_runner import ModelRunner
from model_runner_transport import save
from research_replan import goal_check, validate_direction, validate_result, validate_goal_claim
from review_storage import atomic_json, read_json
from task_pipeline import digest
from test_devspace_main_workflow import plan_reply, result_reply
from test_research_consumption import until_wait
from test_study_review_chain import chain


def direction(ids):
    return {'action':'revise-plan','change_level':'training-method','goal_status':'unmet','evidence_ids':list(ids),
        'assumption':'Control fixture tests a different method, not a model ceiling',
        'intervention':'Implement a changed training method in a new frozen profile',
        'expected_decision_value':'A matched result distinguishes this method hypothesis from repeated parameter interpolation'}


def test_actual_metrics_not_finished_scope_determine_goal():
    config={'research_iteration':{'version':1,'objective':'Control target',
        'targets':[{'field':'score','operator':'>=','value':.8}]}}
    assert goal_check(config,{'score':.72})['met'] is False
    assert goal_check(config,{'score':.8})['met'] is True
    bad=direction(['known']);bad.update(action='goal-met',goal_status='met',change_level='none')
    response={'next_step':{'research_direction':bad,'iteration':{'action':'stop'}}}
    with pytest.raises(ValueError,match='not_reached'):
        validate_result(response,{'evidence':{'known':.72}},{'research_goal_evaluation':{'status':'unmet'}})
    revised=direction(['known'])
    assert validate_direction(revised,['known'])['change_level']=='training-method'
    revised['change_level']='tuning'
    with pytest.raises(ValueError,match='different_intervention'):
        validate_direction(revised,['known'])


def test_replan_cannot_claim_eighty_percent_from_a_finished_seventy_two_percent_run():
    spec={'objective':'Actual user target','targets':[{'field':'validation_accuracy','operator':'>=','value':.8}]}
    claim={'action':'goal-met'}
    with pytest.raises(ValueError,match='not_reached'):
        validate_goal_claim(spec,claim,{'validation_accuracy':.7272})
    assert validate_goal_claim(spec,claim,{'validation_accuracy':.8})['met']
    with pytest.raises(ValueError,match='not_reached'):
        validate_goal_claim({'objective':'Unspecified target'},claim,{'validation_accuracy':.9})


def test_prespecified_representation_control_is_same_plan_not_forced_tuning():
    value=direction(['observed']);value.update(action='continue-plan',change_level='representation')
    response={'next_step':{'research_direction':value,'iteration':{'action':'run-next-round'}}}
    assert validate_result(response,{'evidence':{'observed':.77}},{'research_goal_evaluation':{'status':'unmet'}})==value


def test_windows_status_publication_retries_sharing_without_recomputing(tmp_path):
    import devspace_worker as capsule
    target=tmp_path/'process.json';atomic_json(target,{'phase':'running'})
    real=capsule.os.replace;calls=[]
    def replace(source,destination):
        calls.append(1)
        if len(calls)<3:
            error=PermissionError('Control sharing violation');error.winerror=32;raise error
        return real(source,destination)
    with patch.object(capsule.os,'name','nt'),patch.object(capsule.os,'replace',replace),patch.object(capsule.time,'sleep'):
        capsule.save(target,{'phase':'exited','exit_code':0})
    assert len(calls)==3 and read_json(target)=={'phase':'exited','exit_code':0}


def test_windows_status_publication_does_not_hide_permanent_failure(tmp_path):
    import devspace_worker as capsule
    target=tmp_path/'process.json';atomic_json(target,{'phase':'running'})
    error=PermissionError('Control permanent file denial');error.winerror=5
    with patch.object(capsule.os,'name','nt'),patch.object(capsule.os,'replace',side_effect=error) as replace,patch.object(capsule.time,'sleep'):
        with pytest.raises(PermissionError):capsule.save(target,{'phase':'exited'})
    assert replace.call_count==10 and read_json(target)=={'phase':'running'}


@pytest.mark.parametrize('ending',['revise-plan','stop-research','goal-met'])
def test_original_worker_consumes_archives_then_automatically_reviews_new_plan(tmp_path,ending):
    config,root,_=chain(tmp_path)
    cfg=config['research_worker'];cfg['version']=5
    runner={'version':1,'backend':'cli','cli_path':cfg['cli_path'],
        'cli_sha256':hashlib.sha256(Path(cfg['cli_path']).read_bytes()).hexdigest(),
        'codex_home':cfg['codex_home'],'cwd':str(tmp_path),'timeout_seconds':30}
    cfg['model_runner']=runner
    config['proposal_review']={'version':4,'backend':'model','context_required':True,
        'dialogue_version':2,'models':cfg['models'],'model_runner':copy.deepcopy(runner)}
    config['research_iteration']={'version':1,'objective':'Reach control score .02, beyond this one local run',
        'targets':[{'field':'score','operator':'>=','value':.005 if ending=='goal-met' else .02}]}
    config['devspace_study']['max_rounds']=1
    config['devspace_study']['authorization']['study_digest']=study.binding(config)
    atomic_json(cfg['project_path'],config)
    # Initial submission review is covered elsewhere; this fixture isolates the
    # real result/consumption/archive -> new plan gate instead of launching ML.
    with patch.object(proposal_review,'gate',return_value=None), patch.object(main,'proposal_gate',return_value=None), patch.object(main,'proposal_settings',return_value=None):
        first=study.execution(config,root)
        plan_reply(first);first=until_wait(config,root)
    assert first['status']=='awaiting-review' and first['research_goal_evaluation']['met'] is (ending=='goal-met')
    result_reply(first,'stop')
    response=Path(first['review']['response_path']);draft=read_json(response)
    draft['workflow_assessment']['alternatives']=[draft['workflow_assessment']['alternatives']]
    draft['next_step']['research_direction']=direction([draft['facts'][0]['evidence_id']])
    if ending!='revise-plan':
        draft['next_step']['research_direction'].update(action=ending,change_level='none',
            goal_status='met' if ending=='goal-met' else 'unmet')
    response.unlink() # Unpublished synthetic fixture draft only.
    def result_model(cfg,role,prompt,folder):
        return copy.deepcopy(draft) if role=='a' else {'draft_digest':digest(draft),'verdict':'approve',
            'reason':'Control result judgment, not goal success','challenges':[]}
    calls=[]
    def plan_backend(runner,folder):
        envelope=read_json(folder/'request.json');request=envelope['request'];payload=request['evidence']
        role='a' if request['purpose']=='proposal' else 'b';calls.append(role)
        ids=[row['id'] for row in payload['shared']['packet']['context']['sources']]
        if role=='a':
            raw={key:'Synthetic reasoned plan '+key for key in proposal_review.FIELDS}
            raw.update(addresses=[],advice_dispositions=[],research_revision=direction(ids[:1]))
        else:
            raw={'verdict':'approve','rationale':'Fixture new-method review, no execution grant',
                'challenges':[],'resolutions':[],
                'evidence_assessment':{'source_ids':ids[:1],'judgment':'hypothesis','limitations':['Control only']}}
        save(folder,envelope['binding'],'completed',raw,{'backend':'control-fixture'})
    def watcher(config,root,**kwargs):
        with patch.object(proposal_review,'gate',return_value=None), patch.object(main,'proposal_gate',return_value=None), patch.object(main,'proposal_settings',return_value=None):
            return study.execution(config,root)
    with patch.object(ModelRunner,'_launch',plan_backend):
        result=worker.run(config,root,watcher=watcher,caller=result_model)
    if ending!='revise-plan':
        assert result['status']==('research-goal-met' if ending=='goal-met' else 'research-stopped-unmet'),result
        assert result['research_complete'] is (ending=='goal-met') and calls==[]
        if ending=='stop-research':assert result['agent_continuation_required']
        assert read_json(root/'study-result.json')['research_complete'] is (ending=='goal-met')
        return
    assert result['status']=='research-plan-approved-awaiting-implementation',result
    assert result['agent_continuation_required'] and result['research_complete'] is False
    assert calls==['a','b']
    archived=read_json(root/'study-result.json')
    assert archived['status']=='completed' and archived['research_complete'] is False
    assert read_json(root/'research-replan/implementation-request.json')['change_level']=='training-method'
    assert read_json(next(root.glob('research-decisions/*/commit.json')))['consumed']
    assert not (tmp_path/'remote/r02').exists()
