"""CPU regression for real-config export and the component preflight boundary."""
import json
from pathlib import Path
from unittest.mock import Mock
import pytest

from project_driver import file_digest
from roadmodel_component_gate import validate_permit, run
from roadmodel_config_export import export
from lpai_operation_adapter import LpaiAdapter
from test_roadmodel_normal import case, pin


def test_native_preparation_binds_platform_and_model_digests_separately(case):
    import sys
    from roadmodel_native_prepare import build
    from roadmodel_normal_contract import validate_project
    config,ready,root,marker=case
    protocol=json.loads(Path(config['normal_protocol']['path']).read_bytes())
    protocol.update(evidence_kind='project-execution',initialization={'mode':'none'})
    config['normal_protocol']=pin(root/'native-protocol.json',protocol)
    template=pin(root/'template.json',{'cmd':'OLD AUTOTRAIN','name':'new-component','queue_code':'q'})
    config['normal_native']={'runtime_control_root':'/new-only/control','restricted_shared_control_mount':True,
        'runtime_assets':[{'kind':'data-index','path':'/data/index.json','digest':protocol['data_digest']}],
        'component_template':template,'sdk_python':sys.executable,'request_key':'unit-only',
        'authorization_ref':{'path':'/CPU-NOT-AUTHORIZATION','digest':'sha256:'+'0'*64},
        'adapter':{'namespace':'cpu','owner':'cpu','component_uid':'not-real','submission_registry':str(root/'registry')}}
    result=build(config,root/'native')
    project=json.loads(Path(result['project']).read_bytes());plan=validate_project(project)
    binding=project['external_operations']['14']
    assert binding['configuration_digest']!=binding['normal_effective_config_digest']==plan['effective_config_digest']
    assert binding['allocation_only'] and not result['execution_ready'] and result['platform_calls']==0
    import run_project_pipeline
    with pytest.raises(ValueError,match='preparation_only'):
        run_project_pipeline.run(project,root/'must-not-execute')
    assert not (root/'must-not-execute').exists()
    native=json.loads((root/'native/component-config.json').read_bytes())
    assert '--bundle' in native['cmd'] and '--control' in native['cmd'] and not marker.exists()


def test_config_export_discards_inherited_environment_and_preserves_model(case,monkeypatch):
    config,ready,root,marker=case
    source=root/'config.py'
    source.write_text('import os\n_dataset_paths=["/real/data"]\nmodel=dict(type="RealArchitecture",layers=[1,2,3])\n'
        'max_epochs=int(os.environ["MAX_EPOCH"])\n'
        'train_loader=dict(batch_size=12,num_workers=4)\n'
        'optimizer=dict(optimizer=dict(lr=4e-5))\n')
    monkeypatch.setenv('MAX_EPOCH','1')
    spec={'source':{'path':str(source),'digest':file_digest(source)},
          'protocol':config['normal_protocol'],'environment':{'MAX_EPOCH':'300'}}
    value=export(spec,root/'export')
    resolved=json.loads(Path(value['resolved_config']['path']).read_bytes())
    assert resolved['max_epochs']==300 and resolved['model']['layers']==[1,2,3]
    assert resolved['amp'] is True and not marker.exists()
    assert resolved['_dataset_paths']==['/real/data']
    with pytest.raises(ValueError,match='fresh'):export(spec,root/'export')


def test_export_rejects_source_change_before_execution(case):
    config,ready,root,marker=case
    spec={'source':{'path':str(root/'source/train.py'),'digest':'sha256:'+'0'*64},
          'protocol':config['normal_protocol'],'environment':{}}
    with pytest.raises(ValueError,match='source_changed'):export(spec,root/'bad')
    assert not marker.exists()


@pytest.mark.parametrize('field,value',[('plan_digest','wrong'),('node','14'),('identity',{}),('ready_digest','stale')])
def test_gate_rejects_wrong_plan_or_preflight(field,value):
    bundle={'plan_digest':'sha256:plan','plan':{'identity':{'attempt':'a'}}}
    permit={'schema':'roadmodel-start-permit/v1','plan_digest':'sha256:plan',
            'identity':{'attempt':'a'},'ready_digest':'sha256:ready','node':'15'}
    validate_permit(bundle,'sha256:ready',permit)
    permit[field]=value
    with pytest.raises(ValueError,match='permit_mismatch'):validate_permit(bundle,'sha256:ready',permit)


def test_prior_start_is_not_retried(tmp_path):
    (tmp_path/'started.json').write_text('{}')
    with pytest.raises(ValueError,match='reconciliation'):
        run({'plan':{'distributed':{'nodes':1}},'runtime_assets':[{}]},tmp_path)


def test_waiting_component_cannot_autostart(tmp_path,monkeypatch):
    import roadmodel_component_gate as gate
    monkeypatch.setattr(gate,'preflight',lambda *args:{'checked':'CPU fixture'})
    execution=Mock();monkeypatch.setattr(gate.subprocess,'run',execution)
    def stop_wait(_):raise InterruptedError('CPU test ends waiting phase')
    monkeypatch.setattr(gate.time,'sleep',stop_wait)
    with pytest.raises(InterruptedError):
        run({'plan':{'distributed':{'nodes':1}},'runtime_assets':[{}]},tmp_path)
    assert (tmp_path/'ready.json').exists() and not (tmp_path/'started.json').exists()
    execution.assert_not_called()


def test_component_rechecks_preflight_before_start(tmp_path,monkeypatch):
    import roadmodel_component_gate as gate
    bundle={'plan_digest':'p','plan':{'identity':{'attempt':'a'},'distributed':{'nodes':1}},'runtime_assets':[{}]}
    calls=[]
    def preflight(*args):
        calls.append(1)
        if len(calls)>1:raise ValueError('runtime_asset_digest_mismatch')
        return {'checked':'CPU fixture'}
    monkeypatch.setattr(gate,'preflight',preflight)
    def release(_):
        (tmp_path/'start-permit.json').write_text(json.dumps({'schema':'roadmodel-start-permit/v1',
            'plan_digest':'p','identity':{'attempt':'a'},'node':'15','ready_digest':gate.sha(tmp_path/'ready.json')}))
    monkeypatch.setattr(gate.time,'sleep',release)
    execution=Mock();monkeypatch.setattr(gate.subprocess,'run',execution)
    with pytest.raises(ValueError,match='asset_digest'):run(bundle,tmp_path)
    assert len(calls)==2 and not (tmp_path/'started.json').exists()
    execution.assert_not_called()


def test_submit_rechecks_gate_before_official_sdk_call():
    config={'component_uid':'c','namespace':'n','owner':'o','cluster':'cl','normal_gate_command':'GATED'}
    project=Mock();task=Mock()
    project.get.return_value={'uid':'c','namespace':'n','owner':'o','cluster':'cl',
        'data':{'lpai_task':{'cmd':'AUTOTRAIN','roles':[{'cmd':'AUTOTRAIN'}]}}}
    with pytest.raises(ValueError,match='gate_command_readback'):
        LpaiAdapter(config,project,task,Mock()).submit('key')
    task.submit_project_task.assert_not_called()


def test_explicit_wsl_windows_sdk_mapping_does_not_guess_paths():
    from lpai_operation_adapter import runtime_path
    config={'runtime_path_map':[{'local':'/mnt/d/project','runtime':'D:/project'}]}
    assert runtime_path(config,'/mnt/d/project/config.json')=='D:/project/config.json'
    assert runtime_path(config,'/other/config.json')=='/other/config.json'


def test_connected_native_payload_activates_only_after_final_resource_authorization(case,workspace):
    import sys
    import roadmodel_normal_contract as normal
    from roadmodel_native_prepare import build,activate
    from producer_signing import TEST_PRODUCER_PRINCIPAL_ID
    from task_pipeline import digest
    config,_,root,marker=case
    (root/'source/eval.py').write_text('# CPU interface only, never executed\n')
    protocol=json.loads(Path(config['normal_protocol']['path']).read_bytes())
    protocol.update(evidence_kind='project-execution',initialization={'mode':'none'},
        runtime_output_root='/CPU-PVC/output',runtime_config_path='/CPU-PVC/config.py')
    protocol['source_files']['eval.py']=file_digest(root/'source/eval.py')
    config['normal_protocol']=pin(root/'real-intent-protocol.json',protocol)
    normal.prepare(config,root/'pre-plan')
    plan=json.loads((root/'pre-plan/normal-plan.json').read_bytes())
    auth={'schema':'sdm-normal-training-authorization/v1','direct_user_confirmation':True,
        'source_ref':'CPU-AUTH-FIXTURE-NOT-REAL-PERMISSION','scope':'normal-training','allow_evaluation':True,'allow_cleanup':True,
        'plan_digest':digest(plan),'identity':plan['identity'],'evidence_kind':plan['evidence_kind'],
        'component_uid':'CPU-COMPONENT','namespace':'CPU-SPACE','owner':'CPU-OWNER',
        'training_wall_seconds':None,'queue_wall_seconds':None,'total_budget':None}
    signer=root/'CPU-signer.py';signer.write_text('# Never execute this CPU test signer\n')
    trust=root/'CPU-trust';trust.mkdir();private=root/'CPU-private';private.mkdir()
    volume=root/'volume';volume.mkdir()
    template=pin(root/'component-template.json',{'name':'CPU-component','cmd':'OLD','queue_code':'CPU-QUEUE','image':'CPU-IMAGE',
        'code':[{'name':'CPU-CODE','commit_id':plan['source_commit']}],
        'task_role_specs':[{'rolename':'master','replica':'1','resource':{'id':41,'replica':1}}],
        'input_data_volume':[{'name':'CPU-PVC','env_value':'/CPU-PVC','env_key':'CPU_VOLUME'}]})
    config['normal_native']={'runtime_control_root':'/CPU-PVC/control','restricted_shared_control_mount':True,
        'runtime_assets':[{'kind':'data-index','path':'/CPU-DATA/index.json','digest':plan['data_digest']}],
        'component_template':template,'sdk_python':sys.executable,'request_key':'CPU-REQUEST',
        'authorization_ref':{'path':'/CPU-UNBOUND-AUTH','digest':'sha256:'+'0'*64},
        'training_authorization':pin(root/'training-auth.json',auth),
        'adapter':{'namespace':'CPU-SPACE','owner':'CPU-OWNER','cluster':'CPU-CLUSTER','component_uid':'CPU-COMPONENT','submission_registry':str(root/'registry'),
            'task_name':'CPU-normal','total_gpus':8,'task_info':{'problem_statement':'CPU fixture','solution_approach':'CPU fixture',
                'expected_result':'CPU fixture','expected_duration':10,'expected_duration_desc':'CPU metadata, not a training deadline'}},
        'execution':{'workspace':str(workspace),'local_control_root':str(volume/'control'),'local_output_root':str(volume/'output'),
            'shared_volumes':[{'name':'CPU-PVC','runtime_root':'/CPU-PVC','local_root':str(volume)}],
            'evaluation_metrics':{'miou':{'pattern':r'mIoU: ([0-9.]+)'}},
            'signer':{'entry':{'path':str(signer),'digest':file_digest(signer)},'python':sys.executable,
                'lifecycle_root':str(Path(__file__).resolve().parents[2]),'trust_root':str(trust),'private_root':str(private),
                'principal':TEST_PRODUCER_PRINCIPAL_ID}}}
    result=build(config,root/'native-connected')
    assert result['execution_chain_configured'] and not result['execution_ready']
    prepared=json.loads(Path(result['project']).read_bytes());binding=prepared['external_operations']['14']
    resource_auth={k:binding[k] for k in ('provider','namespace','owner','request_key','action','component_uid','configuration_digest','old_uid')}
    resource_auth.update(direct_user_confirmation=True,source_ref='CPU-AUTH-FIXTURE-NOT-REAL-PERMISSION')
    ref=pin(root/'resource-auth.json',resource_auth)
    active=activate({'prepared_project':{'path':result['project'],'digest':file_digest(Path(result['project']))},
        'resource_authorization':ref},root/'active')
    assert active['execution_ready'] and not active['resource_started']
    project=json.loads(Path(active['project']).read_bytes())
    assert project['normal_native_session_version']==1 and not project['normal_native_preparation_only']
    assert project['external_operations']['14']['authorization_ref']==ref and not marker.exists()
