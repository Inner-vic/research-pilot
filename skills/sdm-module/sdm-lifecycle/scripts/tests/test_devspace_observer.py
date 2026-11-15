"""Real local observer death plus bounded read retry; no platform calls."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from unittest.mock import patch
import pytest
import devspace_execution as d
import devspace_observer as o
from test_devspace_execution import setup_case, public, pin, ENTRY


def test_public_background_survives_parent_exit_and_does_not_spawn_twice(tmp_path):
    config,root=setup_case(tmp_path,"import time,json\nfrom pathlib import Path\ntime.sleep(3)\nPath('result.json').write_text('{\"score\":0.8}')\n",30)
    public(config,root)
    original=json.loads((tmp_path/'remote/process.json').read_bytes())
    started=public(config,root,'--background-devspace')
    assert started['yield_turn'] and started['new_process_started'] and started.get('observer_started_verified')
    repeated=public(config,root,'--background-devspace')
    assert not repeated['new_process_started'] and repeated['supervisor_pid']==started['supervisor_pid']
    until=time.time()+15
    while time.time()<until:
        state=json.loads((root/'execution.json').read_bytes())
        if state['status']=='awaiting-review':break
        time.sleep(.1)
    else:pytest.fail('Detached shared supervisor did not complete')
    assert state['stop_verified'] and state['process']['started_at']==original['started_at']


def test_public_supervisor_restarts_killed_observer_without_relaunch(tmp_path):
    config, root = setup_case(tmp_path, "import time,json\nfrom pathlib import Path\nPath('progress.json').write_text('{\"optimizer_updates\":1}')\ntime.sleep(8)\nPath('result.json').write_text('{\"score\":0.8}')\n", 30)
    config['devspace_execution']['observer_recovery']={'restart_delay':1}
    public(config, root)
    original=json.loads((tmp_path/'remote/process.json').read_bytes())
    project=tmp_path/'project.json'
    parent=subprocess.Popen([sys.executable,'-B',str(ENTRY),'--project',str(project),'--output',str(root),
                             '--devspace-execution','--supervise-devspace'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        until=time.time()+10
        while time.time()<until:
            if (root/'supervisor.json').exists():
                sup=json.loads((root/'supervisor.json').read_bytes())
                if sup.get('status')=='watching':break
            time.sleep(.1)
        else:pytest.fail('supervisor child not started')
        os.kill(sup['child_pid'],signal.SIGTERM)
        out,err=parent.communicate(timeout=25)
        assert parent.returncode==0,err
        done=json.loads(out)
        assert done['restart_count']==1 and len(done['children'])==2
        state=json.loads((root/'execution.json').read_bytes())
        assert state['status']=='awaiting-review' and state['stop_verified']
        assert state['process']['process_pid']==original['process_pid']
        assert state['process']['started_at']==original['started_at']
    finally:
        if parent.poll() is None:parent.kill();parent.wait()


def test_read_failure_retries_same_process_and_recovers(tmp_path):
    config,root=setup_case(tmp_path)
    public(config,root)
    real=d.transport;failures=[]
    def flaky(plan,packet):
        if packet['action']=='observe' and not failures:
            failures.append(1);raise OSError('temporary read interruption')
        return real(plan,packet)
    with patch.object(d,'transport',side_effect=flaky):
        result=o.watch(config,root)
    assert result['status']=='awaiting-review' and result['stop_verified']
    assert not result['collection_failed'] and result['consecutive_collection_failures']==0


def test_identity_fault_does_not_retry(tmp_path):
    config,root=setup_case(tmp_path);public(config,root)
    with patch.object(d,'transport',side_effect=ValueError('remote_attempt_identity_changed')) as call:
        result=o.watch(config,root)
    assert result['status']=='awaiting-reconciliation' and not result['retryable_collection']
    assert call.call_count==1


def test_persistent_read_failure_exhausts_without_relaunch(tmp_path):
    config,root=setup_case(tmp_path);public(config,root)
    config['devspace_execution']['observer_recovery']={'read_retries':1,'retry_delay':1}
    with patch.object(d,'transport',side_effect=OSError('offline')) as call:
        result=o.watch(config,root)
    assert result['observer_retry_exhausted'] and call.call_count==2
    assert not result.get('stop_verified')


def test_recovery_policy_bounded(tmp_path):
    config,_=setup_case(tmp_path)
    config['devspace_execution']['observer_recovery']={'max_restarts':0}
    with pytest.raises(ValueError,match='bounded_observer'):o.policy(config)


def cleanup_case(tmp_path):
    config,root=setup_case(tmp_path)
    public(config,root)
    until=time.time()+10
    while time.time()<until:
        result=d.execution(config,root)
        if result['status']=='cleanup-required':return config,root,result['process']
        time.sleep(.05)
    pytest.fail('workload did not terminate')


def test_cleanup_transient_artifact_read_retries_without_relaunch(tmp_path):
    config,root,original=cleanup_case(tmp_path)
    real=d.transport; calls=[]
    def flaky(plan,packet):
        calls.append(packet['action'])
        if packet['action']=='collect' and calls.count('collect')==1:
            raise OSError('temporary artifact read interruption')
        return real(plan,packet)
    with patch.object(d,'transport',side_effect=flaky),patch.object(d.time,'sleep'):
        result=d.execution(config,root)
    assert result['status']=='awaiting-review' and result['stop_verified']
    assert result['artifacts_collected'] and result['artifact_read_failures']==1
    assert result['process']==original and calls==['collect','collect']
    kinds=[n['kind'] for n in __import__('task_event_service').notifications(config)['notifications']]
    assert kinds.count('artifact_collection_unavailable')==kinds.count('artifact_collection_recovered')==1
    assert 'artifact_collection_failed' not in kinds


def test_cleanup_read_retry_survives_observer_exit_and_exhausts_to_release(tmp_path):
    config,root,original=cleanup_case(tmp_path)
    config['devspace_execution']['observer_recovery']={'read_retries':1,'retry_delay':1}
    with patch.object(d,'transport',side_effect=OSError('offline')),patch.object(d.time,'sleep',side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):d.execution(config,root)
    saved=json.loads((root/'execution.json').read_bytes())
    assert saved['artifact_retry_pending'] and saved['artifact_read_failures']==1
    with patch.object(d,'transport',side_effect=OSError('still offline')) as call:
        result=d.execution(config,root)
    assert call.call_count==1 and result['artifact_read_failures']==2
    assert result['stop_verified'] and result['status']=='released-evidence-incomplete'
    assert result['artifact_retry_exhausted'] and not result.get('artifacts_collected')
    assert result['process']==original
    with patch.object(d,'transport',side_effect=AssertionError('must not relaunch or retry exhausted collection')):
        again=d.execution(config,root)
    assert again==result
    kinds=[n['kind'] for n in __import__('task_event_service').notifications(config)['notifications']]
    assert kinds.count('artifact_collection_failed')==1


def test_cleanup_identity_fault_releases_without_read_retry(tmp_path):
    config,root,_=cleanup_case(tmp_path)
    with patch.object(d,'transport',side_effect=ValueError('remote_attempt_identity_changed')) as call:
        result=d.execution(config,root)
    assert call.call_count==1 and result['stop_verified']
    assert result['status']=='released-evidence-incomplete' and result['artifact_retry_exhausted']
