"""Actual offline driver behavior through the public coordinator; no platform SDK."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import pytest
import driver_handshake as handshake
import run_project_pipeline as entry
import task_pipeline as core
from project_driver import file_digest
import test_project_driver_bundle as bundles
import test_orchestration as orchestration


@pytest.fixture
def project(tmp_path):
    _,config=bundles.fixture(tmp_path)
    records=orchestration.OrchestrationTests();records.setUp()
    config['orchestration']=deepcopy(records.task['orchestration'])
    try:yield config
    finally:records.doCleanups()


def run(config,tmp_path):
    return entry.run(config,tmp_path/'run')['attempts'][0]


def test_actual_handshake_and_node_contracts_survive_resume_without_redispatch(project,tmp_path):
    first=run(project,tmp_path);root=tmp_path/'run/attempt-01'
    assert first['driver_handshake']['status']=='passed'
    assert first['nodes']['16']['status']=='failed' # no fabricated model/S13 success
    assert first['nodes']['14']['observation']['driver_contract']['node']=='14'
    calls=(root/'fixture-calls.jsonl').read_bytes();probe=(root/'handshake-calls.jsonl').read_bytes()
    assert probe.count(b'handshake')==1
    run(project,tmp_path)
    assert (root/'handshake-calls.jsonl').read_bytes()==probe and (root/'fixture-calls.jsonl').read_bytes()==calls


@pytest.mark.parametrize('fault',['all-success','missing-node','stale-nonce','drop-result'])
def test_declaration_without_actual_contract_behavior_blocks_before_resource(project,tmp_path,fault):
    project['fixture_fault']=fault;summary=run(project,tmp_path);root=tmp_path/'run/attempt-01'
    assert summary['status']=='blocked' and summary['reason']=='driver_behavior_handshake_failed'
    assert not (root/'fixture-calls.jsonl').exists() and not (root/'cleanup.json').exists()


@pytest.mark.parametrize('fault',['runtime-contract','wrong-contract'])
def test_handshake_pass_does_not_excuse_inconsistent_runtime_receipt(project,tmp_path,fault):
    project['fixture_fault']=fault;summary=run(project,tmp_path)
    assert summary['driver_handshake']['status']=='passed'
    assert summary['nodes']['14']['status']=='failed' and summary['nodes']['14']['uncertain_effect']
    assert summary['nodes']['14']['reason']=='runtime_driver_contract_mismatch'
    assert summary['cleanup']['status']=='completed'
    calls=(tmp_path/'run/attempt-01/fixture-calls.jsonl').read_text()
    assert '"16"' not in calls


def test_v1_declaration_does_not_silently_gain_behavioral_qualification(project,tmp_path):
    path=Path(project['driver_capabilities']['path']);value=json.loads(path.read_bytes());value['schema']='sdm-driver-capabilities/v1'
    path.write_text(json.dumps(value));project['driver_capabilities']['digest']=file_digest(path)
    result=run(project,tmp_path)
    assert result['status']=='blocked' and not (tmp_path/'run/attempt-01/fixture-calls.jsonl').exists()


@pytest.mark.parametrize('problem',['no-invalid','wrong-valid','missing-cleanup'])
def test_inadequate_case_declaration_does_not_launch_driver(project,tmp_path,problem):
    path=Path(project['driver_capabilities']['path']);value=json.loads(path.read_bytes())
    if problem=='no-invalid':value['nodes']['14']['behavior_cases']=[value['nodes']['14']['behavior_cases'][0]]*2
    if problem=='wrong-valid':value['nodes']['14']['behavior_cases'][0]['expected']['status']='failed'
    if problem=='missing-cleanup':value['nodes'].pop('cleanup')
    path.write_text(json.dumps(value));project['driver_capabilities']['digest']=file_digest(path)
    with pytest.raises((ValueError,KeyError)):run(project,tmp_path)
    assert not (tmp_path/'run/attempt-01/handshake-calls.jsonl').exists()


@pytest.mark.parametrize('change',['response','acceptance'])
def test_replay_revalidates_handshake_artifacts(project,tmp_path,change):
    run(project,tmp_path);root=tmp_path/'run/attempt-01';calls=(root/'fixture-calls.jsonl').read_bytes()
    name='driver-handshake-response.json' if change=='response' else 'driver-handshake-accepted.json'
    (root/name).write_text('{}')
    result=run(project,tmp_path)
    assert result['status']=='blocked' and (root/'fixture-calls.jsonl').read_bytes()==calls
    assert (root/'handshake-calls.jsonl').read_text().count('handshake')==1


def test_missing_runtime_contract_binding_is_rejected_before_handshake_or_resource(project,tmp_path):
    captured=[]
    def capture(task,root):captured.append(task);return {'status':'blocked','nodes':{}}
    with patch.object(entry,'coordinate',side_effect=capture):run(project,tmp_path)
    task=captured[0];task['drivers']['14'].pop('behavior_contract')
    result=core.coordinate(task,tmp_path/'run/attempt-01')
    assert result['status']=='blocked' and 'runtime_binding_missing' in result['detail']
    assert not (tmp_path/'run/attempt-01/handshake-calls.jsonl').exists()


def test_public_cli_runs_local_handshake_before_normal_dispatch(project,tmp_path):
    path=tmp_path/'config.json';path.write_text(json.dumps(project));output=tmp_path/'public'
    command=[sys.executable,'-B',str(Path(entry.__file__)),'--project',str(path),'--output',str(output)]
    result=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    summary=json.loads((output/'attempt-01/summary.json').read_bytes())
    assert summary['driver_handshake']['status']=='passed'
    assert len(core.graph(json.loads((output/'attempt-01/coordinator-task.json').read_bytes())))==34


def test_changed_driver_code_cannot_reuse_old_handshake(project,tmp_path):
    run(project,tmp_path);path=Path(project['project_driver']['path']);path.write_text(path.read_text()+'\n# change')
    with pytest.raises(ValueError,match='changed'):run(project,tmp_path)


def test_windows_handshake_arguments_use_existing_runtime_conversion(tmp_path):
    provider=tmp_path/'driver.py';provider.write_text('# CPU path conversion only')
    from project_driver_launch import command
    config={'project_driver':{'path':str(provider),'digest':file_digest(provider)},'linux_host':str(tmp_path),
            'driver_runtime':{'mode':'windows-wsl','python':'/mnt/c/Python/python.exe'}}
    with patch('run_project_pipeline.windows',side_effect=lambda p:'WIN:'+str(p)):
        argv=command(config,tmp_path/'project.json',None,tmp_path/'receipt.json',tmp_path/'challenge.json')
    assert '--handshake' in argv and 'WIN:'+str(tmp_path/'challenge.json') in argv
