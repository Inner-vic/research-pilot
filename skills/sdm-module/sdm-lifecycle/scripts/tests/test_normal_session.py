"""CPU-only native session integration with real S12/S13 Ed25519 verification.

Platform clients, shared deployment and model outputs are explicit CPU fixtures.
No real resource authorization, SDK call, GPU use or training is exercised.
"""
import json
from copy import deepcopy
from pathlib import Path
import sys
import time
import pytest

from canonical import digest,canonical_bytes
from project_driver import file_digest
from task_pipeline import digest as plan_digest,verified_s13
from execution_contracts import pinned_json
from roadmodel_normal_contract import validate_project
from test_roadmodel_normal import case,pin
from producer_signing import seal_platform_result,TEST_PRODUCER_PRINCIPAL_ID
import normal_session as session
import normal_shared as shared


@pytest.fixture
def native(case,workspace):
    _,config,root,marker=case;plan=validate_project(config)
    control=root/'control';control.mkdir();output=root/'outputs';output.mkdir()
    attempt=root/'attempt';attempt.mkdir();config['root']=str(attempt)
    signer=root/'signer.py';signer.write_text('# CPU injected Ed25519 signer only\n')
    bundle={'session_nonce':'CPU-MOUNT-NONCE','plan':plan,'plan_digest':plan_digest(plan)}
    resource={'provider':'lpai','namespace':'CPU-NO-PLATFORM','owner':'CPU-FIXTURE','component_uid':'CPU-COMPONENT',
        'request_key':'CPU-REQUEST','uid':'CPU-TASK','observed_at':time.time(),'status':'running','released':False,'pods':[{'uid':'CPU-POD','status':'Running'}]}
    config['external_operations']={'14':{k:resource[k] for k in ('provider','namespace','owner','component_uid','request_key')}}
    auth={'schema':'sdm-normal-training-authorization/v1','direct_user_confirmation':True,
        'source_ref':'CPU-SIMULATION-NOT-USER-AUTHORITY','scope':'normal-training','allow_evaluation':True,'allow_cleanup':True,
        'plan_digest':plan_digest(plan),'identity':plan['identity'],'evidence_kind':'cpu-simulation',
        'component_uid':resource['component_uid'],'namespace':resource['namespace'],'owner':resource['owner'],
        'training_wall_seconds':None,'queue_wall_seconds':None,'total_budget':None}
    bundle.update(authorization_digest=digest(auth),component_uid=resource['component_uid'],normal_session=True)
    config['normal_native']={'runtime_control_root':'/CPU-NOT-REAL-MOUNT/control','restricted_shared_control_mount':True,
        'adapter':{'component_uid':resource['component_uid'],'namespace':resource['namespace'],'owner':resource['owner']},
        'training_authorization':pin(root/'authorization.json',auth),'bundle_ref':pin(root/'bundle.json',bundle),
        'execution':{'mode':'cpu-fixture','local_control_root':str(control),'local_output_root':str(output),'workspace':str(workspace),
            'evaluation_metrics':{'miou':{'pattern':r'mIoU: ([0-9.]+)'}},
            'signer':{'entry':{'path':str(signer),'digest':file_digest(signer)},'python':sys.executable,
                'principal':TEST_PRODUCER_PRINCIPAL_ID}}}
    (control/'bundle.json').write_bytes((root/'bundle.json').read_bytes())
    event={'node':'14','task_digest':'CPU-TASK-GRAPH','at':time.time(),
        'result':{'status':'completed','observation':{'external_operation':{'observation':deepcopy(resource)}}}}
    (attempt/'events.jsonl').write_text(json.dumps(event)+'\n')
    ready={k:plan[k] for k in ('identity','source_commit','data_digest','effective_config_digest','world_size','runtime_output_root')}
    ready.update(visible_gpus_per_node=plan['distributed']['gpus_per_node'],output_empty=True,
        observed_at=time.time(),valid_until=time.time()+300,bundle_digest=config['normal_native']['bundle_ref']['digest'],
        session_nonce=bundle['session_nonce'],runtime_control_root=config['normal_native']['runtime_control_root'])
    shared.publish(control,'ready.json',ready)
    calls=[]
    def transport(config,operation,uid):
        assert uid=='CPU-TASK';calls.append(operation)
        if operation=='read':return {**resource,'observed_at':time.time()}
        assert operation=='stop';return {'requested':True,'uid':uid}
    def signer_callback(config,request,unsigned,target):
        return seal_platform_result(request,json.loads(unsigned.read_bytes()))
    def step(node):return session.step(config,plan,node,transport,signer_callback)
    return config,plan,control,output,resource,calls,step,marker


def produce_training(native,epochs=300):
    config,plan,control,output,resource,calls,step,_=native
    permit=json.loads((control/'start-permit.json').read_bytes())
    checkpoint=output/'checkpoint.pth';checkpoint.write_bytes(b'CPU TEXT NOT A MODEL')
    index={'schema':'roadmodel-checkpoint-index/v1','checkpoints':[{'id':'target','origin':plan['identity'],
        'complete':True,'epoch':epochs,'relative_path':'checkpoint.pth','digest':file_digest(checkpoint),
        'components':['model','optimizer','scheduler','epoch','global_iter']}]}
    shared.publish(control,'checkpoint-index.json',index)
    raw={'request_id':permit['request_id'],'exit_code':0,'completed_epochs':epochs,'optimizer_updates':10,
        'world_size':8,'completed':True,'checkpoint_index_digest':file_digest(control/'checkpoint-index.json')}
    shared.publish(control,'training-result.json',raw)


def test_normal_s12_permit_s13_evaluation_and_cleanup(native):
    config,plan,control,output,resource,calls,step,marker=native
    assert step('7')['status']=='completed' and step('13')['status']=='completed'
    first=step('15');assert first['status']=='paused'
    from roadmodel_component_gate import normal_permit,sha
    assert normal_permit(json.loads((control/'bundle.json').read_bytes()),control,'15',sha(control/'ready.json'))
    permit=(control/'start-permit.json').read_bytes()
    assert step('15')['status']=='paused' and (control/'start-permit.json').read_bytes()==permit
    produce_training(native)
    training=step('15');assert training['status']=='completed' and verified_s13(training['accepted_s13'])
    assert training['actual']['evidence_kind']=='cpu-simulation'
    assert step('15')==training
    assert step('16')['status']=='paused'
    evaluation_permit=json.loads((control/'evaluation-permit.json').read_bytes())
    shared.publish(control,'evaluation-result.json',{'request_id':evaluation_permit['request_id'],'exit_code':0,
        'evaluation_complete':True,'checkpoint_digest':evaluation_permit['checkpoint_digest'],'metrics':{'miou':1.25}})
    evaluation=step('16');assert evaluation['status']=='completed' and verified_s13(evaluation['accepted_s13'])
    pending=step('cleanup');assert pending['status']=='failed' and calls.count('stop')==1
    assert step('cleanup')['status']=='failed' and calls.count('stop')==1
    resource.update(status='stopped',released=True,pods=[])
    assert step('cleanup')['status']=='completed' and calls.count('stop')==1
    assert not marker.exists()


def test_normal_training_incomplete_is_signed_failure(native):
    *_,step,marker=native
    step('7');step('13');step('15');produce_training(native,epochs=1)
    result=step('15')
    assert result['status']=='failed' and verified_s13(result['signed_result'],'failed')
    assert not marker.exists()


@pytest.mark.parametrize('field,value',[('direct_user_confirmation',False),('scope','branch-diagnostic'),('training_wall_seconds',1800),('evidence_kind','project-execution')])
def test_normal_authorization_cannot_be_substituted(native,field,value):
    config,plan,*_=native;ref=config['normal_native']['training_authorization'];path=Path(ref['path'])
    auth=json.loads(path.read_bytes());auth[field]=value;ref.update(pin(path,auth))
    with pytest.raises(ValueError):session.validate(config,plan)


def test_wrong_shared_mapping_and_foreign_resource_are_rejected(native):
    config,plan,control,output,resource,calls,step,_=native
    resource['uid']='FOREIGN'
    with pytest.raises(ValueError,match='resource_identity'):step('7')
    assert not (control/'start-permit.json').exists()


def test_checkpoint_substitution_blocks_evaluation(native):
    config,plan,control,output,resource,calls,step,_=native
    step('7');step('13');step('15');produce_training(native);assert step('15')['status']=='completed'
    (output/'checkpoint.pth').write_bytes(b'SUBSTITUTED')
    with pytest.raises(ValueError,match='checkpoint_changed'):step('16')
    assert not (control/'evaluation-permit.json').exists()


@pytest.mark.parametrize('profile',['legacy','exploration'])
def test_public_coordinator_cpu_session_pauses_without_reacquiring(native,monkeypatch,profile):
    from test_orchestration import OrchestrationTests
    from task_pipeline import coordinate
    config,plan,control,output,resource,calls,step,marker=native
    fixture=OrchestrationTests();fixture.setUp()
    try:
        task=deepcopy(fixture.task);task['workflow_profile']=profile
        root=Path(config['root']);(root/'events.jsonl').unlink()
        for node in ('7','13','15','16'):task['drivers'][node]['resumable_observation']=True
        from task_pipeline import current_task
        (root/'coordinator-task.json').write_text(json.dumps(current_task(task,root)))
        (root/'project.json').write_text(json.dumps(config))
        seen=[];errors=[]
        def driver(binding,work,node,budget):
            seen.append(node)
            if node=='14':return {'status':'completed','observation':{'external_operation':{
                'observation':deepcopy(resource),'resource_identity':{k:resource[k] for k in ('provider','namespace','uid')}}}}
            if node in {'7','13','15','16','cleanup'}:
                try:value=step(node)
                except ValueError as error:
                    errors.append((node,str(error)));raise
                return {'status':value['status'],'observation':value}
            return {'status':'completed'}
        first=coordinate(task,root,driver)
        assert first['status']=='paused' and 'cleanup' not in seen,errors
        produce_training(native)
        second=coordinate(task,root,driver)
        assert second['status']=='paused' and seen.count('14')==1 and 'cleanup' not in seen
        permit=json.loads((control/'evaluation-permit.json').read_bytes())
        shared.publish(control,'evaluation-result.json',{'request_id':permit['request_id'],'exit_code':0,
            'evaluation_complete':True,'checkpoint_digest':permit['checkpoint_digest'],'metrics':{'miou':1.25}})
        final=coordinate(task,root,driver)
        assert final['status']=='cleanup-required' and seen.count('14')==1 and seen.count('cleanup')==1
        original=(root/'cleanup.json').read_bytes();assert calls.count('stop')==1
        resource.update(status='completed',released=True,pods=[])
        import task_pipeline
        def read_only_execute(binding,work,node,budget):
            assert node=='cleanup-readback' and binding['read_only'] is True and '--readback' in binding['argv']
            payload=session.cleanup_readback(config,lambda c,op,uid: ({**resource,'observed_at':time.time()} if op=='read' else pytest.fail('readonly reconciliation tried stop')))
            receipt=Path(binding['receipt']);receipt.write_text(json.dumps(payload))
            return {'status':'completed','observation':payload,'receipt':str(receipt),'receipt_digest':file_digest(receipt)}
        monkeypatch.setattr(task_pipeline,'execute',read_only_execute)
        closed=session.reconcile_cleanup(config)
        assert closed['status']=='completed' and closed['original_failure_retained']
        assert session.reconcile_cleanup(config)==closed
        assert (root/'cleanup.json').read_bytes()==original and calls.count('stop')==1
        before=list(seen);final=coordinate(task,root,driver)
        assert final['status']=='complete' and seen==before+['21']
        before=list(seen);coordinate(task,root,driver);assert seen==before
        assert all(final['nodes'][key]['status']=='completed' for key in ('1','2','3','4','6','8','14','7','13','15','16','18','21'))
        assert not marker.exists()
    finally:fixture.doCleanups()


def test_resumable_process_receipts_are_immutable_and_completed_is_not_repeated(tmp_path):
    from task_pipeline import execute
    script=tmp_path/'observe.py';calls=tmp_path/'calls.txt'
    script.write_text('''import argparse,json,pathlib
p=argparse.ArgumentParser();p.add_argument('--receipt');a=p.parse_args()
calls=pathlib.Path(__file__).with_name('calls.txt')
n=len(calls.read_text()) if calls.exists() else 0
calls.write_text('x'*(n+1))
value={'task_node':'7','status':'paused' if n<2 else 'completed','uncertain_effect':False,'continuation':'same-normal-task-readback'}
pathlib.Path(a.receipt).write_text(json.dumps(value))
''')
    work=tmp_path/'work';work.mkdir()
    receipt=work/'receipt.json'
    driver={'argv':[sys.executable,'-B',str(script),'--receipt',str(receipt)],'receipt':str(receipt),
        'timeout_seconds':10,'resumable_observation':True}
    one=execute(driver,work,'7',10);original=Path(one['receipt']).read_bytes()
    assert one['status']=='paused' and execute(driver,work,'7',10)['status']=='paused'
    assert execute(driver,work,'7',10)['status']=='completed'
    assert execute(driver,work,'7',10)['status']=='completed' and calls.read_text()=='xxx'
    assert Path(one['receipt']).read_bytes()==original


def test_normal_schema_rejects_a_validly_signed_foreign_actual(native):
    from normal_execution import unsigned_result
    import handoffs
    from ledger import replay
    config,plan,control,output,resource,calls,step,_=native
    step('7');step('13');step('15');produce_training(native)
    # Construct correct result, then substitute its actual identity and re-sign.
    result=step('15');workspace=Path(config['normal_native']['execution']['workspace'])
    request=json.loads((workspace/'packets/S12'/(result['accepted_s13']['request_id']+'.json')).read_bytes())
    from normal_execution import validate_result
    packet=json.loads((workspace/'packets/S13'/(request['request_id']+'.json')).read_bytes())
    packet=deepcopy(packet);value=packet['evidence'][0]['value'];actual=json.loads(value['actual_report'])
    actual['resource_uid']='FOREIGN';value.update(actual_report=json.dumps(actual),observation_digest=digest(actual))
    sealed=seal_platform_result(request,packet)
    trust,trust_digest=handoffs._producer_trust(replay(workspace))
    from ledger import utc_now
    with pytest.raises(handoffs.HandoffError) as caught:
        handoffs._validate_result(request,sealed,replay(workspace).bundles[replay(workspace).active_engine_digest],
            trusted_snapshot=trust,trusted_snapshot_digest=trust_digest,consumption_time=utc_now())
    assert caught.value.code=='normal_result_binding_invalid'


def test_cleanup_is_independent_of_model_assets_and_signer(native):
    config,plan,control,output,resource,calls,step,_=native
    Path(config['normal_effective_config']['path']).unlink()
    Path(config['normal_native']['execution']['signer']['entry']['path']).unlink()
    assert step('cleanup')['status']=='failed' and calls.count('stop')==1
    resource.update(status='stopped',released=True,pods=[])
    assert step('cleanup')['status']=='completed' and calls.count('stop')==1


def test_cpu_mode_cannot_reach_official_sdk(native):
    config,plan,*_=native
    with pytest.raises(ValueError,match='injected_transport'):session.step(config,plan,'7')
