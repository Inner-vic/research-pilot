"""RoadModel normal-v2 offline contracts; all training/S13 authority in these tests is simulated."""
from copy import deepcopy
import ast,json,subprocess,sys,time
from pathlib import Path
import pytest
import roadmodel_normal_contract as normal
import roadmodel_normal_driver as driver
from project_driver import file_digest
from execution_contracts import pinned_json
import run_project_pipeline as entry
import test_orchestration as orchestration


def pin(path,value):
    path.write_text(json.dumps(value,indent=2));return {'path':str(path),'digest':file_digest(path)}

@pytest.fixture
def case(tmp_path):
    source=tmp_path/'source';(source/'misc').mkdir(parents=True)
    flags=['--py-config','--work-dir','--amp','--gradient-accumulation','--resume-from','--load-from','--seed']
    marker=tmp_path/'MODEL-MUST-NOT-RUN'
    (source/'train.py').write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\nimport argparse\np=argparse.ArgumentParser()\n'+''.join(f'p.add_argument({x!r})\n' for x in flags))
    for name in normal.SOURCES[1:]:(source/name).write_text('raise RuntimeError("NEVER IMPORT MODEL")\n')
    inputs=normal.fixture_inputs();protocol=inputs['protocol'];protocol['source_files']={name:file_digest(source/name) for name in normal.SOURCES}
    config={'provider':normal.PROVIDER,'run_id':'normal-cpu-contract','normal_source_root':str(source),
        'normal_protocol':pin(tmp_path/'protocol.json',protocol),'normal_resolved_config':pin(tmp_path/'resolved.json',inputs['resolved'])}
    prepared=normal.prepare(config,tmp_path/'prepared');ready=json.loads(Path(prepared['project']).read_bytes())
    return config,ready,tmp_path,marker


def test_normal_plan_has_full_parameters_torchrun_and_no_smoke_cap(case):
    config,ready,root,marker=case;plan=normal.validate_project(ready)
    assert ready['proposal_review']['version']==3 and ready['proposal_review']['backend']=='model'
    assert plan['training']['epochs']==300 and plan['training']['batch_per_gpu']==12 and plan['training']['workers_per_gpu']==4
    assert plan['world_size']==8 and plan['effective_batch']==192 and plan['training_wall_seconds'] is None
    assert 'torch.distributed.run' in plan['argv'] and '--nproc_per_node' in plan['argv'] and '--debug-mode' not in plan['argv']
    tree=ast.parse(Path(ready['normal_effective_config']['path']).read_text());actual={n.targets[0].id:ast.literal_eval(n.value) for n in tree.body}
    assert actual=={**pinned_json(config['normal_resolved_config']),'warmup_iters':500}
    assert not marker.exists() and plan['execution_authority_created'] is False
    assert normal.prepare(config,root/'prepared')['status']=='prepared-for-offline-validation'


@pytest.mark.parametrize('epochs,workers,amp,wall',[(1000,0,False,None),(7,8,True,72000),(10,4,False,None),(20,4,True,None),(30,4,False,None)])
def test_application_parameters_are_not_fixed_to_fixture_defaults(case,epochs,workers,amp,wall):
    config,_,root,_=case;p=pinned_json(config['normal_protocol']);r=pinned_json(config['normal_resolved_config'])
    p['training'].update(epochs=epochs,workers_per_gpu=workers,amp=amp);p['training_wall_seconds']=wall
    r.update(max_epochs=epochs,amp=amp);r['train_loader']['num_workers']=workers
    config['normal_protocol']=pin(root/'protocol-new.json',p);config['normal_resolved_config']=pin(root/'resolved-new.json',r)
    normal.prepare(config,root/'new');plan=json.loads((root/'new/normal-plan.json').read_bytes())
    assert plan['training']['epochs']==epochs and plan['training_wall_seconds']==wall
    assert ('--amp' in plan['argv']) is amp


def test_card_count_is_not_capped_at_old_forty_card_project_preference():
    inputs=normal.fixture_inputs();inputs['protocol']['distributed'].update(nodes=8,gpus_per_node=8)
    inputs['protocol']['training']['epochs']=20;inputs['resolved']['max_epochs']=20
    result=normal.business('6',inputs)
    assert result['status']=='completed' and result['actual']['world_size']==64


@pytest.mark.parametrize('field,value,reason',[
    ('batch_per_gpu',0,'normal_positive_integer_required'),('workers_per_gpu',-1,'normal_positive_integer_required'),
    ('amp','true','normal_amp_boolean_required'),('epochs',True,'normal_positive_integer_required')])
def test_bad_training_parameters_fail_business_handshake(field,value,reason):
    inputs=normal.fixture_inputs();inputs['protocol']['training'][field]=value
    result=normal.business('6',inputs);assert result['status']=='failed' and reason in result['reason']


def test_declared_warmup_cannot_silently_fall_back():
    inputs=normal.fixture_inputs();inputs['protocol']['training']['warmup_epochs']=3
    assert normal.business('6',inputs)['reason']=='normal_warmup_config_mismatch'
    inputs['resolved']['warmup_epochs']=3
    assert normal.business('6',inputs)['status']=='completed'
    inputs['resolved']['warmup_iters']=500
    assert normal.business('6',inputs)['reason']=='normal_warmup_config_mismatch'


def test_resolved_config_mismatch_and_changed_source_block_before_dispatch(case):
    config,ready,root,_=case;r=pinned_json(config['normal_resolved_config']);r['train_loader']['batch_size']=1
    config['normal_resolved_config']=pin(root/'wrong.json',r)
    with pytest.raises(ValueError,match='loader_config_mismatch'):normal.prepare(config,root/'bad')
    (Path(ready['normal_source_root'])/'train.py').write_text('raise RuntimeError("changed")')
    with pytest.raises(ValueError,match='source_file_changed'):entry.run(ready,root/'not-started')
    assert not (root/'not-started').exists()


def index():
    origin={'study':'cpu','trial':'normal','attempt':'a1'}
    return {'schema':'roadmodel-checkpoint-index/v1','checkpoints':[
        {'id':name,'origin':origin,'epoch':epoch,'complete':complete,'relative_path':name+'.pth','digest':'sha256:'+digit*64,
        'components':['model','optimizer','scheduler','epoch','global_iter'],'metrics':{'score':score}}
        for name,epoch,complete,digit,score in [('old',1,True,'a',0.5),('selected',7,True,'b',0.8),('incomplete',9,False,'c',0.9)]]}


def test_checkpoint_selection_is_origin_bound_complete_and_explicit():
    rows=index();identity={'study':'cpu','trial':'normal','attempt':'a2'};origin=rows['checkpoints'][0]['origin']
    assert normal.choose_checkpoint(rows,{'policy':'latest-complete','origin':origin},identity,'resume')['id']=='selected'
    assert normal.choose_checkpoint(rows,{'policy':'best','metric':'score','direction':'min','origin':origin},identity,'weights')['id']=='old'
    assert normal.choose_checkpoint(rows,{'policy':'exact','id':'selected','origin':origin},identity,'weights')['id']=='selected'
    with pytest.raises(ValueError,match='one_complete_checkpoint'):normal.choose_checkpoint(rows,{'policy':'exact','id':'incomplete','origin':origin},identity,'weights')
    rows['checkpoints'].append(deepcopy(rows['checkpoints'][0]))
    with pytest.raises(ValueError,match='unique_normal_checkpoint_ids'):normal.choose_checkpoint(rows,{'policy':'latest-complete','origin':origin},identity,'weights')


@pytest.mark.parametrize('fault',['tie','foreign','components','missing_metric'])
def test_checkpoint_ambiguity_identity_and_completeness_block(fault):
    rows=index();identity={'study':'cpu','trial':'normal','attempt':'a2'};origin=rows['checkpoints'][0]['origin'];choice={'policy':'latest-complete','origin':origin}
    if fault=='tie':rows['checkpoints'][0]['epoch']=7
    if fault=='foreign':choice['origin']={**origin,'trial':'other'}
    if fault=='components':rows['checkpoints'][1]['components']=['model']
    if fault=='missing_metric':choice.update(policy='best',metric='unmeasured',direction='max')
    with pytest.raises(ValueError):normal.choose_checkpoint(rows,choice,identity,'resume')


def test_checkpoint_bytes_and_resume_kind_are_checked_without_deserialization(case):
    config,_,root,_=case;cp=root/'checkpoints';cp.mkdir();(cp/'old.pth').write_bytes(b'CPU MODEL FIXTURE, NOT A PICKLE')
    p=pinned_json(config['normal_protocol']);rows=index();rows['checkpoints']=rows['checkpoints'][:1];rows['checkpoints'][0]['digest']=file_digest(cp/'old.pth')
    p['initialization']={'mode':'resume','resume_kind':'full','selector':{'policy':'exact','id':'old','origin':rows['checkpoints'][0]['origin']}};p['runtime_checkpoint_root']='/cpu/checkpoints'
    config.update(normal_protocol=pin(root/'resume.json',p),normal_checkpoint_index=pin(root/'index.json',rows),normal_checkpoint_root=str(cp))
    with pytest.raises(ValueError,match='full_resume_not_qualified'):normal.prepare(config,root/'full')
    p['initialization']['resume_kind']='optimizer-schedule';config['normal_protocol']=pin(root/'partial.json',p)
    normal.prepare(config,root/'partial');plan=json.loads((root/'partial/normal-plan.json').read_bytes())
    assert '--resume-from' in plan['argv'] and plan['argv'][-1]=='/cpu/checkpoints/old.pth'
    assert 'NOT qualified' in plan['resume_qualification']
    (cp/'old.pth').write_bytes(b'CHANGED')
    with pytest.raises(ValueError,match='checkpoint_content_changed'):normal.prepare(config,root/'changed')


def test_runtime_requires_fresh_output_no_implicit_latest_and_actual_complete_epochs(case):
    _,ready,_,_=case;plan=normal.validate_project(ready);now=time.time()
    observed={k:plan[k] for k in ('source_commit','data_digest','effective_config_digest','world_size','runtime_output_root')}
    observed.update(visible_gpus_per_node=8,output_empty=True,observed_at=now-1,valid_until=now+100)
    assert normal.check_runtime(plan,observed)['runtime_contract_verified']
    observed['output_empty']=False
    with pytest.raises(ValueError,match='fresh_output'):normal.check_runtime(plan,observed)
    actual={k:plan[k] for k in ('identity','source_commit','data_digest','effective_config_digest','runtime_output_root','world_size')}
    actual.update(plan_digest=normal.digest(plan),exit_code=0,completed_epochs=1,optimizer_updates=100,checkpoint_index={'path':'not-opened'})
    with pytest.raises(ValueError,match='target_epochs_not_completed'):normal.check_training(plan,actual)
    actual['completed_epochs']=300;assert normal.check_training(plan,actual)['completed_epochs']==300
    actual['optimizer_updates']=0
    with pytest.raises(ValueError,match='effective_updates_required'):normal.check_training(plan,actual)


def test_new_driver_manifest_has_real_parameter_and_checkpoint_cases(case):
    _,ready,root,_=case;manifest=pinned_json(ready['driver_capabilities'])
    for node,row in manifest['nodes'].items():
        for challenge in row['behavior_cases']:assert normal.business(node,challenge['input'])==challenge['expected']
    assert normal.business('unknown',{})['reason']=='unsupported_node'
    assert driver.implementation(ready)==manifest
    assert driver.runtime(ready,'6')['actual']['training']['epochs']==300
    with pytest.raises(ValueError,match='native_submit_requires_external'):driver.runtime(ready,'14')
    with pytest.raises(ValueError,match='execution_bridge_binding_required'):driver.runtime(ready,'15')


def test_public_prepare_inspect_handshake_and_resume_never_run_model(case):
    config,_,root,marker=case;path=root/'config.json';path.write_text(json.dumps(config));target=root/'cli-prepared'
    process=subprocess.run([sys.executable,'-B',str(Path(entry.__file__)),'--project',str(path),'--output',str(target),'--prepare-roadmodel-driver'],capture_output=True,text=True,timeout=20)
    assert process.returncode==0,process.stderr
    ready=json.loads((target/'project.json').read_bytes());fixture=orchestration.OrchestrationTests();fixture.setUp()
    try:
        ready['orchestration']=deepcopy(fixture.task['orchestration'])
        assert ready['workflow_profile']=='legacy'  # Compact default is held pending responsibility-equivalence audit.
        assert entry.run(ready,root/'pipeline')['status']=='awaiting-proposal'
        assert not marker.exists()
        from test_proposal_review import complete_local
        assert complete_local(ready,root/'pipeline')['status']=='proposal-approved'
        result=entry.run(ready,root/'pipeline')['attempts'][0]
        assert result['driver_handshake']['status']=='passed'
        assert result['nodes']['6']['observation']['actual']['training']['epochs']==300
        assert all(result['nodes'][key]['status']=='completed' for key in ('1','2','3','4','6','8'))
        assert result['nodes']['14']['observation']['reason']=='normal_native_submit_requires_external_operation_binding'
        assert result['nodes']['7']['status']=='skipped' and result['nodes']['15']['status']=='skipped'
        assert result['cleanup']['status']=='failed' # no fabricated platform release
        request=(root/'pipeline/attempt-01/driver-handshake-response.json').read_bytes()
        entry.run(ready,root/'pipeline')
        assert (root/'pipeline/attempt-01/driver-handshake-response.json').read_bytes()==request
        assert len(__import__('task_pipeline').graph(json.loads((root/'pipeline/attempt-01/coordinator-task.json').read_bytes())))==34
    finally:fixture.doCleanups()
    assert not marker.exists()


def test_simulated_protocol_cannot_dispatch_external_operation(case):
    _,ready,root,_=case;ready['external_operations']={'14':{}}
    with pytest.raises(ValueError,match='simulated_normal_protocol'):entry.run(ready,root/'no-dispatch')
    assert not (root/'no-dispatch').exists()


def test_normal_argv_reaches_bound_cpu_gateway_once_without_training(case):
    _,ready,root,_=case;gateway=root/'cpu_gateway.py';workspace=root/'gateway-work';workspace.mkdir()
    gateway.write_text('''import argparse,json,pathlib
p=argparse.ArgumentParser();p.add_argument('--request');p.add_argument('--receipt');a=p.parse_args()
r=json.loads(pathlib.Path(a.request).read_bytes());base=pathlib.Path(a.receipt).parent
(base/'received.json').write_text(json.dumps(r))
with (base/'calls.txt').open('a') as f:f.write('CPU request only\\n')
pathlib.Path(a.receipt).write_text(json.dumps({'task_node':r['node'],'status':'failed','reason':'cpu_fixture_no_model_execution','uncertain_effect':False}))
raise SystemExit(3)
''')
    ready.update(root=str(workspace),normal_execution_bridge={'kind':'cpu-fixture','python':sys.executable,
        'entry':{'path':str(gateway),'digest':file_digest(gateway)},'call_timeout_seconds':10})
    for _ in range(2):
        with pytest.raises(ValueError,match='normal_bridge_not_accepted'):driver.runtime(ready,'15')
    p=workspace/'normal-execution-bridge/15';received=json.loads((p/'received.json').read_bytes());plan=normal.validate_project(ready)
    assert received['argv']==plan['argv'] and received['training_wall_seconds'] is None
    assert received['effective_config_ref']['digest']==plan['effective_config_digest']
    assert (p/'calls.txt').read_text().count('CPU request only')==1


def test_signed_observation_actual_cannot_be_replaced_by_a_summary(case,monkeypatch):
    from canonical import digest as canonical_digest
    _,ready,root,_=case;signed=root/'signed';(signed/'packets/S13').mkdir(parents=True);(signed/'packets/S12').mkdir()
    actual={'a':1};packet={'request_id':'normal-train','evidence':[{'type':'execution_observation','value':{'actual_report':json.dumps(actual),'observation_digest':canonical_digest(actual)}}]}
    (signed/'packets/S13/normal-train.json').write_text(json.dumps(packet));(signed/'packets/S12/normal-train.json').write_text(json.dumps({'subjects':{'execution_kind':'run'}}))
    value={'accepted_s13':{'workspace':str(signed),'request_id':'normal-train','result_digest':canonical_digest(packet)},'actual':actual}
    monkeypatch.setattr(driver,'verified_s13',lambda ref:True) # CPU authority stub only, not real S13 qualification
    assert driver.accepted_observation(ready,'15',value)['actual']==actual
    value['actual']={'a':2}
    with pytest.raises(ValueError,match='exported_observation_not_signed'):driver.accepted_observation(ready,'15',value)


def test_changed_helper_invalidates_manifest_before_runtime(case,monkeypatch):
    _,ready,_,_=case;original=driver.file_digest
    monkeypatch.setattr(driver,'file_digest',lambda p:'sha256:'+'0'*64 if Path(p).name=='roadmodel_normal_contract.py' else original(p))
    with pytest.raises(ValueError,match='implementation_changed'):driver.implementation(ready)


def test_checkpoint_body_not_rehashed_for_identity_but_rechecked_before_dispatch(case,monkeypatch):
    config,_,root,_=case;cp=root/'weights';cp.mkdir();weight=cp/'old.pth';weight.write_bytes(b'CPU fixture')
    p=pinned_json(config['normal_protocol']);rows=index();rows['checkpoints']=rows['checkpoints'][:1];rows['checkpoints'][0]['digest']=file_digest(weight)
    p['initialization']={'mode':'weights','selector':{'policy':'exact','id':'old','origin':rows['checkpoints'][0]['origin']}};p['runtime_checkpoint_root']='/cpu/weights'
    config.update(normal_protocol=pin(root/'weights-protocol.json',p),normal_checkpoint_index=pin(root/'weights-index.json',rows),normal_checkpoint_root=str(cp))
    normal.prepare(config,root/'weights-prepared');ready=json.loads((root/'weights-prepared/project.json').read_bytes())
    original=normal.file_digest;reads=[]
    def checked(path):
        if Path(path)==weight:reads.append(str(path))
        return original(path)
    monkeypatch.setattr(normal,'file_digest',checked)
    assert driver.runtime(ready,'1')['actual']=={'identity':p['identity']}
    assert driver.runtime(ready,'8')['actual']['effective_config_digest']
    assert reads==[]
    weight.write_bytes(b'changed')
    with pytest.raises(ValueError,match='checkpoint_content_changed'):driver.runtime(ready,'13')
    assert len(reads)==1


def test_bridge_crash_preserves_uncertainty_and_never_redispatches(case):
    import driver_handshake
    _,ready,root,_=case;workspace=root/'crash-work';workspace.mkdir();gateway=root/'crash_gateway.py'
    gateway.write_text('''import pathlib
with pathlib.Path(__file__).with_name('crash-calls.txt').open('a') as f:f.write('CPU dispatch\\n')
raise RuntimeError('CPU gateway interrupted without receipt')
''')
    ready.update(root=str(workspace),normal_execution_bridge={'kind':'cpu-fixture','python':sys.executable,
        'entry':{'path':str(gateway),'digest':file_digest(gateway)},'call_timeout_seconds':10})
    config=root/'crash-project.json';config.write_text(json.dumps(ready))
    spec=driver_handshake.build(ready,{'15'},Path(ready['project_driver']['path']),[],workspace,str,30)
    for number in (1,2):
        receipt=root/f'crash-receipt-{number}.json'
        p=subprocess.run([sys.executable,'-B',str(Path(driver.__file__)),'--project',str(config),'--node','15','--receipt',str(receipt),'--handshake-context',spec['request_ref']['path']],capture_output=True,text=True,timeout=20)
        value=json.loads(receipt.read_bytes());assert p.returncode==3 and value['status']=='failed'
        assert value['uncertain_effect'] is True and 'normal_bridge_not_accepted' in value['reason']
        assert value['execution_started'] is None and value['bridge_result']['status']=='failed'
    assert (root/'crash-calls.txt').read_text().count('CPU dispatch')==1
