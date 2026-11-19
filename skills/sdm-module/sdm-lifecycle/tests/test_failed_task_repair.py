"""Failure-to-implementation controls; no local ML, model or platform calls."""
import copy
from pathlib import Path
from unittest.mock import patch

import pytest
from test_component_pipeline import fixture
from component_pipeline import ComponentPipeline
from component_payload import ref
from review_storage import atomic_json


def failed_review(tmp_path, prior_success=False):
    inbox,baseline=fixture(tmp_path)
    pipeline=ComponentPipeline(inbox);state=pipeline.load();current=state['attempts'][-1]
    failed=tmp_path/'failure.json'
    atomic_json(failed,{'schema':'sdm-component-diagnosis-ready/v1','valid_training_round':False,
        'uid':'failed-remote-task','platform_status':'failed','logs':[{'text':'tiny-fit failed'}],
        'original_plan_sha256':current['source']['plan']['sha256']})
    plan=tmp_path/'reviewed-repair.json'
    value={'schema':'sdm-research-plan/v1','parent_task_uid':'failed-remote-task',
        'parent_diagnosis_ref':ref(failed),'research_revision':{'action':'diagnose','change_level':'tuning'},
        **({} if prior_success else {'preparation_recovery':{'version':1}})}
    atomic_json(plan,value)
    current.update(stage='reviewing-plan',result=ref(failed),job='completed-original-review')
    state['rounds']=[baseline] if prior_success else []
    completed={'coordinator_result':{'status':'research-plan-approved-awaiting-diagnosis',
        'agent_continuation_required':True,'plan_path':str(plan)}}
    return pipeline,state,current,plan,value,completed


@pytest.mark.parametrize('prior_success',[False,True])
@pytest.mark.parametrize('action',['diagnose','refine-current','revise-plan','continue-plan'])
def test_reviewed_failed_uid_reaches_original_implementation_without_counting_failure(tmp_path,prior_success,action):
    from research_replan import DIRECTION_STATUSES
    pipeline,state,current,plan,value,completed=failed_review(tmp_path,prior_success)
    value['research_revision']['action']=action
    if action=='revise-plan':value['research_revision']['change_level']='model'
    atomic_json(plan,value)
    completed['coordinator_result']['status']=DIRECTION_STATUSES[action]
    before=copy.deepcopy(state['rounds'])
    with patch.object(pipeline,'completed',return_value=completed),\
            patch.object(pipeline,'implementation') as implement,\
            patch('component_diagnostics.prepare',side_effect=AssertionError('no success-only aggregate for failure')):
        assert pipeline.step(state)==0
    implement.assert_called_once()
    assert implement.call_args.args[0] is current
    assert implement.call_args.args[2]==plan
    assert state['rounds']==before
    assert len(state['attempts'])==1
    assert current['result']==value['parent_diagnosis_ref']


@pytest.mark.parametrize('field,value',[
    ('parent_task_uid','another-failed-task'),
    ('parent_diagnosis_ref',{'path':'unrelated','sha256':'0'*64}),
    ('preparation_recovery',None),
])
def test_failed_repair_cannot_switch_original_uid_or_preparation_scope(tmp_path,field,value):
    pipeline,state,current,plan,record,completed=failed_review(tmp_path)
    record[field]=value;atomic_json(plan,record)
    with patch.object(pipeline,'completed',return_value=completed),patch.object(pipeline,'implementation') as implement:
        with pytest.raises(ValueError,match='exact_failed_parent'):pipeline.step(state)
    implement.assert_not_called()


def test_successful_result_diagnosis_still_uses_original_evidence_adapter(tmp_path):
    pipeline,state,current,plan,record,completed=failed_review(tmp_path,True)
    record.pop('parent_diagnosis_ref');atomic_json(plan,record)
    with patch.object(pipeline,'completed',return_value=completed),\
            patch('component_diagnostics.prepare',return_value=[{'path':'index','sha256':'1'*64}]) as aggregate,\
            patch.object(pipeline,'replan') as replan,patch.object(pipeline,'implementation') as implement:
        assert pipeline.step(state)==0
    aggregate.assert_called_once();replan.assert_called_once();implement.assert_not_called()


def test_read_only_failure_diagnosis_never_silently_becomes_model_edit(tmp_path):
    from review_storage import read_json
    pipeline,state,current,plan,record,completed=failed_review(tmp_path)
    record['research_revision']['change_level']='none';atomic_json(plan,record)
    with patch.object(pipeline,'completed',return_value=completed),\
            patch.object(pipeline,'replan') as replan,patch.object(pipeline,'implementation') as implement:
        assert pipeline.step(state)==0
    replan.assert_called_once();implement.assert_not_called()
    evidence=state['diagnostic_history'][0]['evidence'][0]
    actual=read_json(evidence['path'])
    assert actual['uid']=='failed-remote-task' and actual['primary_metric'] is None
    assert not actual['new_measurements'] and not actual['execution_authority']
    assert state['rounds']==[]
    with patch.object(pipeline,'completed',return_value=completed),patch.object(pipeline,'implementation') as implement:
        pipeline.step(state)
    assert current['stage']=='needs-attention'
    assert current['reason']=='diagnosis-requires-new-supported-evidence'
    implement.assert_not_called()


def test_foreign_inventory_is_retained_without_relaxing_parent_or_candidate_decoding():
    from component_execution_evidence import inventory_fingerprint,fingerprint
    task={'data':{'lpai_task':{'cmd':'GENERATED_REVIEWED_PAYLOAD','roles':[{'cmd':'python roadmodel_cpu_check.py'}]}}}
    found=inventory_fingerprint(task)
    assert found['comparable'] is False and found['native_data_sha256']
    assert len(found['command_sha256'])==2 and 'retry permission' in found['authority']
    with pytest.raises(ValueError,match='same_exact_executed_role_payload_required'):fingerprint(task)


@pytest.mark.parametrize('commands',[
    ['unqualified\nSDM_PAYLOAD','foreign'],
    ['damaged SDM_PAYLOAD wrapper','damaged SDM_PAYLOAD wrapper'],
])
def test_mixed_or_damaged_sdm_inventory_cannot_hide_as_foreign_history(commands):
    from component_execution_evidence import inventory_fingerprint
    task={'data':{'lpai_task':{'cmd':commands[0],'roles':[{'cmd':commands[1]}]}}}
    with pytest.raises(ValueError):inventory_fingerprint(task)
