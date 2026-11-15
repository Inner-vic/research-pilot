"""Sealed project dependencies and actual CPU-only public driver launches."""
import json
from pathlib import Path
import subprocess
import sys
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]))
from package_project_driver import package
from project_driver import bind_bundle, resolve
from project_driver_launch import command
import run_project_pipeline as pipeline

CORE=Path(__file__).parents[1]

DRIVER='''import argparse,json,pathlib,hashlib
from facts import observation
p=argparse.ArgumentParser();p.add_argument('--project');p.add_argument('--node');p.add_argument('--receipt');p.add_argument('--handshake');p.add_argument('--handshake-context');a=p.parse_args()
c=json.loads(pathlib.Path(a.project).read_bytes());root=pathlib.Path(c['root'])
def digest(v):return 'sha256:'+hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def handle(node,inputs):
    supported={str(n) for n in range(1,22)}|{'23','cleanup'}
    if c.get('fixture_fault')=='missing-node':supported.discard('14')
    if node not in supported:return {'task_node':node,'status':'failed','reason':'unsupported_node'}
    if type(inputs.get('value')) is not int:return {'task_node':node,'status':'failed','reason':'input_contract_violation'}
    return {'task_node':node,'status':'completed','actual':{'double':inputs['value']*2}}
if a.handshake:
    request=json.loads(pathlib.Path(a.handshake).read_bytes())
    with (root/'handshake-calls.jsonl').open('a') as stream:stream.write('CPU-only handshake\\n')
    rows=[]
    for row in request['cases']+[request['unsupported_case']]:
        result=handle(row['node'],row['input'])
        if c.get('fixture_fault')=='all-success':result={'task_node':row['node'],'status':'completed'}
        rows.append({'case_id':row['case_id'],'result':result})
    value={'task_node':'driver-handshake','status':'completed','schema':'sdm-driver-handshake-response/v1',
           'request_digest':digest(request),'nonce':request['nonce'],'execution_mode':'offline-contract-test','results':rows}
    if c.get('fixture_fault')=='stale-nonce':value['nonce']='other'
    if c.get('fixture_fault')=='drop-result':value['results'].pop()
    pathlib.Path(a.receipt).write_text(json.dumps(value));raise SystemExit(0)

with (root/'fixture-calls.jsonl').open('a') as f:f.write(json.dumps({'node':a.node})+'\\n')
result={'task_node':a.node,'status':'completed','fixture_only':True,'model_compute_invoked':False}
if a.node=='16':result.update(status='failed',reason='synthetic_fixture_does_not_execute_models',actual=observation())
if a.node=='cleanup':result.update(resource={'phase':'stopped','fixture_only':True})
if a.node=='20':
 decision={'action':'stop-analysis','reason':'No model execution was authorized in this software fixture'}
 (root/'decision.json').write_text(json.dumps(decision));result['decision']=decision
if a.handshake_context:
 context=json.loads(pathlib.Path(a.handshake_context).read_bytes())
 result['driver_contract']={'schema':'sdm-driver-execution-contract/v1','provider':context['provider'],
  'driver_digest':context['driver_digest'],'manifest_digest':context['manifest_digest'],
  'handshake_request_digest':digest(context),'node':a.node,**context['nodes'][a.node]}
 if c.get('fixture_fault')=='runtime-contract' and a.node=='14':result.pop('driver_contract')
 if c.get('fixture_fault')=='wrong-contract' and a.node=='14':result['driver_contract']['output_contract']='foreign'
pathlib.Path(a.receipt).write_text(json.dumps(result))
'''


def fixture(tmp_path,name='forecast'):
    source=tmp_path/(name+'-source');source.mkdir()
    (source/'driver.py').write_text(DRIVER)
    metrics={'rmse':0.3,'window':24} if name=='forecast' else {'accuracy':0.8,'classes':3}
    (source/'facts.py').write_text('def observation(): return '+repr(metrics)+'\n')
    result=package(source,'driver.py',['driver.py','facts.py'],tmp_path/(name+'-project'))
    cfg={'supported_graph_revisions':[3],'run_id':name,'provider':name+'-v1','project_driver':result['project_driver'],
        'driver_runtime':{'mode':'native','python':sys.executable},'objective':'evaluate-only',
        'supported_objectives':['evaluate-only'],'max_attempts':1,'read_only_task_nodes':[]}
    from project_driver import file_digest
    from task_pipeline import graph
    required={k for k,n in graph({'objective':'evaluate-only'}).items() if n['policy']=='required' and not n.get('builtin')}
    manifest=tmp_path/(name+'-capabilities.json')
    manifest.write_text(json.dumps({'schema':'sdm-driver-capabilities/v2','handshake_protocol':'sdm-driver-handshake/v1','provider':cfg['provider'],
        'driver_digest':result['project_driver']['digest'],'graph_revision':3,
        'nodes':{k:{'input_contract':'CPU fixture project inputs','output_contract':'SDM node receipt','behavior_cases':[
            {'kind':'valid','input':{'value':3},'expected':{'task_node':k,'status':'completed','actual':{'double':6}}},
            {'kind':'invalid','input':{},'expected':{'task_node':k,'status':'failed','reason':'input_contract_violation'}}]} for k in required|{'cleanup'}}}))
    cfg['driver_capabilities']={'path':str(manifest),'digest':file_digest(manifest)}
    return result,cfg


@pytest.mark.parametrize('name',['forecast','classifier'])
def test_two_projects_launch_native_pipeline_and_resume_without_reexecution(tmp_path,name):
    result,cfg=fixture(tmp_path,name);output=tmp_path/(name+'-run')
    # A current public run supplies actual orchestration records rather than
    # selecting the retired ungated 21-node route. Values remain CPU fixtures.
    from test_orchestration import OrchestrationTests
    from copy import deepcopy
    sample=OrchestrationTests();sample.setUp()
    try:
        cfg['orchestration']=deepcopy(sample.task['orchestration'])
        records=tmp_path/'orchestration-records';records.mkdir()
        for key,ref in cfg['orchestration']['records'].items():
            target=records/(key+'.json');target.write_bytes(Path(ref['path']).read_bytes())
            ref['path']=str(target)
    finally:sample.doCleanups()
    first=pipeline.run(cfg,output)
    import task_pipeline
    frozen_task=json.loads((output/'attempt-01/coordinator-task.json').read_bytes())
    first_event=json.loads((output/'attempt-01/events.jsonl').read_text().splitlines()[0])
    assert task_pipeline.digest(frozen_task)==first_event['task_digest']
    assert json.loads((output/'attempt-01/experiment-identity.json').read_bytes())['trial_id']==name
    calls=(output/'attempt-01/fixture-calls.jsonl').read_bytes()
    again=pipeline.run(cfg,output)
    assert (output/'attempt-01/fixture-calls.jsonl').read_bytes()==calls
    for run in (first,again):
        summary=run['attempts'][0]
        assert summary['status']=='complete' and summary['nodes']['15']['status']=='not-applicable'
        assert summary['nodes']['16']['status']=='failed'
        assert summary['nodes']['16']['observation']['model_compute_invoked'] is False
        assert summary['cleanup']['status']=='completed'
        assert run['decision']['action']=='stop'
    assert not any((Path(result['project_driver']['path']).parent).rglob('*.pyc'))


@pytest.mark.parametrize('change',['helper','missing','extra'])
def test_dependency_changes_block_actual_cleanup_launch_before_project_code(tmp_path,change):
    result,cfg=fixture(tmp_path)
    root=Path(result['project_driver']['path']).parent
    if change=='helper':(root/'facts.py').write_text('raise AssertionError("must not execute")')
    if change=='missing':(root/'facts.py').unlink()
    if change=='extra':(root/'unbound.py').write_text('print("extra")')
    cfg.update(linux_host=str(tmp_path),root=str(tmp_path))
    config=tmp_path/'config.json';config.write_text(json.dumps(cfg));receipt=tmp_path/'cleanup.json'
    process=subprocess.run([sys.executable,'-B',str(CORE/'project_driver_launch.py'),'--project',str(config),
        '--node','cleanup','--receipt',str(receipt)],capture_output=True,text=True)
    assert process.returncode==3,process.stderr
    value=json.loads(receipt.read_bytes())
    assert value['reason']=='project_bundle_changed_create_new_run' and value['execution_started'] is False
    assert not (tmp_path/'fixture-calls.jsonl').exists()


def test_declared_file_hash_cannot_be_changed_without_manifest_binding(tmp_path):
    result,cfg=fixture(tmp_path)
    cfg['project_driver']['files']['facts.py']='sha256:'+'0'*64
    with pytest.raises(ValueError,match='bundle_binding_mismatch'):resolve(cfg,tmp_path)


def test_project_bundle_link_and_case_collisions_are_rejected(tmp_path):
    result,cfg=fixture(tmp_path);root=Path(result['project_driver']['path']).parent
    (root/'linked.py').symlink_to(root/'facts.py')
    with pytest.raises(ValueError,match='links_not_allowed'):resolve(cfg,tmp_path)
    (root/'linked.py').unlink();(root/'FACTS.py').write_text('pass')
    with pytest.raises(ValueError,match='case_unique'):bind_bundle(root/'driver.py')


@pytest.mark.parametrize('ancestor',[False,True])
def test_whole_package_directory_cannot_be_rebound_through_a_link(tmp_path,ancestor):
    result,cfg=fixture(tmp_path);entry=Path(result['project_driver']['path'])
    link=tmp_path/'alias'
    link.symlink_to(entry.parent.parent if ancestor else entry.parent,target_is_directory=True)
    alias=link/'adapter/driver.py' if ancestor else link/'driver.py'
    cfg['project_driver']['path']=str(alias)
    with pytest.raises(ValueError,match='links_not_allowed'):resolve(cfg,tmp_path)
    with pytest.raises(ValueError,match='links_not_allowed'):bind_bundle(alias)


def test_package_refuses_escape_or_existing_target_and_preserves_source(tmp_path):
    result,cfg=fixture(tmp_path);source=tmp_path/'forecast-source'
    before={p.name:p.read_bytes() for p in source.iterdir()}
    with pytest.raises(ValueError,match='fresh_adapter_package'):
        package(source,'driver.py',['driver.py','facts.py'],tmp_path/'forecast-project')
    with pytest.raises(ValueError,match='relative_file'):
        package(source,'driver.py',['driver.py','../outside.py'],tmp_path/'bad')
    assert {p.name:p.read_bytes() for p in source.iterdir()}==before
    assert not (tmp_path/'bad').exists()


def test_public_packaging_cli_emits_exact_bundle_binding(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'driver.py').write_text('raise RuntimeError("never execute during packaging")')
    result=subprocess.run([sys.executable,'-B',str(CORE/'package_project_driver.py'),'--source',str(source),
        '--entry','driver.py','--file','driver.py','--output',str(tmp_path/'project')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    value=json.loads(result.stdout)
    assert resolve({'project_driver':value['project_driver']},tmp_path).name=='driver.py'
    assert value['project_execution_performed'] is False


def test_runtime_commands_preserve_legacy_windows_and_reject_ambiguous_python(tmp_path):
    _,cfg=fixture(tmp_path);cfg['linux_host']=str(tmp_path)
    cfg['driver_runtime']={'mode':'native','python':'python -c injected'}
    with pytest.raises(ValueError,match='absolute_project_python'):command(cfg,tmp_path/'config.json','1',tmp_path/'receipt')
    cfg.pop('driver_runtime');cfg['windows_python_wsl']='/mnt/c/python.exe'
    from unittest.mock import patch
    with patch.object(pipeline,'windows',side_effect=lambda p:'W:'+str(p)):
        argv=command(cfg,tmp_path/'config.json','1',tmp_path/'receipt')
    assert argv[:2]==['/mnt/c/python.exe','-B'] and argv[2].startswith('W:')


def test_source_isolation_node_23_has_a_public_project_driver_route(tmp_path):
    _,cfg=fixture(tmp_path)
    cfg['linux_host']=str(tmp_path)
    argv=command(cfg,tmp_path/'project.json','23',tmp_path/'worktree-receipt.json')
    assert argv[argv.index('--node')+1]=='23'
    assert argv[0]==sys.executable
