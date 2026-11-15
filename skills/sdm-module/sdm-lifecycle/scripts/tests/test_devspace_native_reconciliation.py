"""Control-plane-only regressions: no model, GPU, training or platform calls."""
import hashlib
import json
import os
import subprocess
import time
from unittest.mock import patch
import pytest
import devspace_execution as d
import reconcile_devspace_native_start as r
from review_storage import atomic_json,read_json
from task_pipeline import digest


def case(tmp):
    root=tmp/'attempt';root.mkdir();now=time.time()
    plan={'resource':{'uid':'owned','name':'own-space','cluster':'own-cluster'}}
    state={'binding':'bound','uid':'owned','intents':['start'],'cleanup_required':True,
           'last_error':'official_operation_unconfirmed:pending_approval',
           'original_failure':{'error':'official_operation_unconfirmed:pending_approval'}}
    request={'action':'start','uid':'owned','requested_at':now-5}
    atomic_json(root/'execution.json',state);atomic_json(root/'start-request.json',request)
    receipt=tmp/'native.json';readback=tmp/'readback.json'
    atomic_json(receipt,{'request_digest':digest(request),'uid':'owned','action':'start',
                        'performed':True,'source_ref':'actual-test-only-native-proof','at':now-1})
    atomic_json(readback,{'status':'ok','data':{'metadata':{'uid':'owned','name':'own-space'},
        'spec':{'clusterName':'own-cluster'},'status':{'phase':'ready'}}})
    return root,plan,receipt,readback


def invoke(c):
    root,plan,receipt,readback=c
    with (patch.object(r.execution,'validate',return_value=({},plan,'bound','live-platform-observation')),
          patch.object(r.execution,'resource_call',side_effect=AssertionError('no platform write/query'))):
        return r.reconcile({},root,receipt,readback)


def test_matching_native_start_preserves_failure_and_reentry_does_not_reclear_state(tmp_path):
    c=case(tmp_path);root,_,_,_=c
    result=invoke(c);assert result['status']=='reconciled-awaiting-original-resume'
    state=read_json(root/'execution.json')
    assert not state['cleanup_required'] and state['intents']==['start']
    assert state['original_failure']['error']=='official_operation_unconfirmed:pending_approval'
    state.update(launch_intent=True,status='running');atomic_json(root/'execution.json',state)
    before=(root/'execution.json').read_bytes()
    assert invoke(c)['status']=='already-reconciled'
    assert (root/'execution.json').read_bytes()==before


@pytest.mark.parametrize('fault',['uid','request','launched','stale','unrelated-failure'])
def test_reconcile_rejects_unrelated_or_ambiguous_evidence_without_modifying_state(tmp_path,fault):
    c=case(tmp_path);root,_,receipt,readback=c
    if fault in {'launched','unrelated-failure'}:
        s=read_json(root/'execution.json')
        if fault=='launched':s['launch_intent']=True
        else:s['original_failure']['error']='capsule_transport_failed'
        atomic_json(root/'execution.json',s)
    elif fault=='stale':os.utime(readback,(time.time()-1000,time.time()-1000))
    else:
        s=read_json(receipt);s['uid' if fault=='uid' else 'request_digest']='wrong';atomic_json(receipt,s)
    before=(root/'execution.json').read_bytes()
    with pytest.raises(ValueError):invoke(c)
    assert (root/'execution.json').read_bytes()==before


def test_completed_reconciliation_rechecks_retained_evidence(tmp_path):
    c=case(tmp_path);invoke(c);atomic_json(c[2],{})
    with pytest.raises(ValueError,match='reconciliation_evidence_changed'):invoke(c)


def test_explicit_openssh_path_is_used_without_disabling_host_verification(tmp_path):
    plan={'transport':'ssh','remote_root':'/tmp/control-only','ssh':{'host':'example.test','user':'tester',
        'port':22,'key':str(tmp_path/'key'),'executable':'/usr/bin/ssh'}}
    with patch.object(d.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{}','')) as call:
        d.transport(plan,{'action':'observe','binding':'test'})
    argv=call.call_args.args[0]
    assert argv[0]=='/usr/bin/ssh' and 'StrictHostKeyChecking=yes' in argv and 'IdentitiesOnly=yes' in argv


def test_installed_worker_roundtrips_over_stdin_with_bounded_windows_command_line(tmp_path):
    import shlex,sys
    real_run=subprocess.run
    seen=[]
    def remote_python(argv,**kw):
        seen.append(argv)
        command=shlex.split(argv[-1])
        assert command[:3]==['python3','-B','-c']
        assert len(subprocess.list2cmdline(argv))<2048
        return real_run([sys.executable,'-B','-c',command[3]],input=kw['input'],capture_output=True,text=True,timeout=15)
    plan={'transport':'ssh','remote_root':str(tmp_path/'no-allocation'),
          'ssh':{'host':'example.test','user':'tester','port':22,'key':str(tmp_path/'key'),'executable':'/usr/bin/ssh'}}
    source="import json,sys;v=json.load(sys.stdin);print(json.dumps({'received':v['value'],'gpu_compute_invoked':False}))"
    packet={'action':'preflight','binding':'fixture','source':source,'sha256':hashlib.sha256(source.encode()).hexdigest(),
            'parameters':{'value':'real-bootstrap-roundtrip'},'timeout_seconds':10}
    with patch.object(d.subprocess,'run',side_effect=remote_python):result=d.transport(plan,packet)
    assert result['status']=='passed' and result['report']['received']=='real-bootstrap-roundtrip'
    assert not (tmp_path/'no-allocation').exists() and len(seen)==1
