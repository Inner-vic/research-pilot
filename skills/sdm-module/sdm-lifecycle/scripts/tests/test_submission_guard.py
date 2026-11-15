"""CPU-only pending approval, shared request deduplication and public recovery."""
import json
from pathlib import Path
import subprocess
import sys
import time
from copy import deepcopy
import pytest
import external_operations as external
import task_pipeline as pipeline
import submission_guard as guard
import test_external_operations as fixtures


@pytest.fixture
def case():
    f=fixtures.ExternalOperationTests();f.setUp()
    f.spec['action']='submit';f.spec['old_uid']=None;f.authorize()
    root=f.root/'attempt';root.mkdir()
    driver={'external_operation':f.spec,'receipt':str(root/'14-receipt.json')}
    task={'drivers':{'14':driver}}
    f.fixture(error_command='submit',error={'status':'pending_approval','approval_id':'cpu-approval','mode':'self','recipients':['DO-NOT-STORE']})
    result=pipeline.execute(driver,root,'14',15)
    assert result['status']=='failed' and result['uncertain_effect']
    state=json.loads((root/'14-external-state.json').read_bytes())
    assert state['pending_approval']['approval_id']=='cpu-approval'
    assert 'recipients' not in state['pending_approval']
    g=guard.Guard(f.spec,root/'14-external-state.json')
    run=root/'submission-reconciliation/14/check-1'
    payload=f.root/'payload.json'
    value={'task_node':'submission-readback','status':'completed','scope':g.scope,
        'approval':{'approval_id':'cpu-approval','status':'cancelled','irreversible':True,'no_future_execution':True,
                    'observed_at':time.time(),'source_ref':'CPU simulated gateway only'},
        'lookup':{'complete':True,'matches':[],'observed_at':time.time(),'source_ref':'CPU simulated complete task listing'}}
    payload.write_text(json.dumps(value))
    script=f.root/'readback.py';script.write_text('from pathlib import Path\nimport sys\nPath(sys.argv[2]).write_bytes(Path(sys.argv[1]).read_bytes())\n')
    request={'schema':'sdm-submission-reconciliation/v1','node':'14','reconciliation_id':'check-1',
        'task_digest':pipeline.digest(pipeline.current_task(task,root)),
        'pending_approval':{k:state['pending_approval'][k] for k in ['status','approval_id','mode']},
        'driver':{'read_only':True,'argv':[sys.executable,'-B',str(script),str(payload),str(run/'readback.json')],
                  'receipt':str(run/'readback.json'),'timeout_seconds':10}}
    try:yield f,root,task,g,request,payload
    finally:f.doCleanups()


def fresh(f,name='other'):
    root=f.root/name;root.mkdir(exist_ok=True)
    return external.Operation(f.spec,root/'operation.json',15)


def test_pending_blocks_new_attempt_and_other_channel_without_probes(case):
    f,root,task,g,request,payload=case;before=f.calls()
    other=deepcopy(f.spec);other['routes']=[r for r in other['routes'] if r['kind']=='browser']
    target=f.root/'browser';target.mkdir()
    with pytest.raises(ValueError,match='approval_pending_do_not_fallback'):
        external.Operation(other,target/'operation.json',10).run()
    assert f.calls()==before
    with pytest.raises(ValueError,match='approval_pending_do_not_fallback'):fresh(f).run()
    assert f.calls()==before


@pytest.mark.parametrize('problem',['pending','reversible','future','wrong-id','stale','scope','partial','ambiguous','foreign-match','old-lookup'])
def test_bad_closure_never_releases_request(case,problem):
    f,root,task,g,request,payload=case;v=json.loads(payload.read_bytes())
    if problem=='pending':v['approval']['status']='pending'
    if problem=='reversible':v['approval']['irreversible']=False
    if problem=='future':v['approval']['no_future_execution']=False
    if problem=='wrong-id':v['approval']['approval_id']='other'
    if problem=='stale':v['approval']['observed_at']=time.time()-500
    if problem=='scope':v['scope']['owner']='foreign'
    if problem=='partial':v['lookup']['complete']=False
    if problem=='old-lookup':v['lookup']['observed_at']=v['approval']['observed_at']-1
    if problem=='ambiguous':v['lookup']['matches']=[{},{}]
    if problem=='foreign-match':v['lookup']['matches']=[{'uid':'other','provider':'foreign'}]
    payload.write_text(json.dumps(v))
    with pytest.raises(ValueError):guard.reconcile(task,root,request)
    with pytest.raises(ValueError,match='approval_pending'):fresh(f).run()
    g.load();assert g.state['phase']=='pending-approval'
    assert sum(name=='submit' for _,name in f.calls())==1


def test_empty_task_list_alone_does_not_prove_approval_is_closed(case):
    f,root,task,g,request,payload=case
    f.fixture(matches=0)
    with pytest.raises(ValueError,match='approval_pending'):fresh(f).run()
    assert sum(name=='submit' for _,name in f.calls())==1


def test_resolution_keeps_failed_receipt_and_allows_one_fresh_attempt(case):
    f,root,task,g,request,payload=case;original=(root/'14-receipt.json').read_bytes()
    result=guard.reconcile(task,root,request);assert result['no_submission_performed']
    count=len(f.calls());guard.reconcile(task,root,request);assert len(f.calls())==count
    f.fixture();assert fresh(f).run()['uid']=='new'
    assert sum(name=='submit' for _,name in f.calls())==2 # one pending request, one after irrevocable closure
    with pytest.raises(ValueError,match='do_not_resubmit'):fresh(f,'third').run()
    assert (root/'14-receipt.json').read_bytes()==original
    with pytest.raises(ValueError,match='new_attempt'):external.Operation(f.spec,root/'14-external-state.json',10).run()


def test_existing_native_task_is_adopted_only_after_closure_without_submission(case):
    f,root,task,g,request,payload=case;v=json.loads(payload.read_bytes())
    v['lookup']['matches']=[{**g.scope,'uid':'new','status':'pending','observed_at':time.time()}]
    payload.write_text(json.dumps(v));guard.reconcile(task,root,request)
    f.fixture(matches=1);assert fresh(f).run()['uid']=='new'
    assert sum(name=='submit' for _,name in f.calls())==1


def test_tampered_accepted_proof_blocks_next_attempt(case):
    f,root,task,g,request,payload=case;guard.reconcile(task,root,request)
    Path(request['driver']['receipt']).write_text('{}')
    with pytest.raises(ValueError,match='evidence_changed'):fresh(f).run()


def test_readonly_recovery_cli_does_not_resume_failed_node_or_submit(case):
    f,root,task,g,request,payload=case
    taskfile=f.root/'task.json';taskfile.write_text(json.dumps(task));req=f.root/'request.json';req.write_text(json.dumps(request))
    command=[sys.executable,'-B',str(Path(pipeline.__file__)),'--task',str(taskfile),'--output',str(root),'--reconcile-submission',str(req)]
    before=f.calls()
    for _ in range(2):
        result=subprocess.run(command,capture_output=True,text=True,timeout=20)
        assert result.returncode==0,result.stderr
        assert json.loads(result.stdout)['no_submission_performed']
    assert f.calls()==before
    assert not (root/'events.jsonl').exists()


def test_shared_scope_drift_is_rejected_before_provider_call(case):
    f,root,task,g,request,payload=case;before=f.calls();f.spec['configuration_digest']='different';f.authorize()
    with pytest.raises(ValueError,match='scope_changed'):fresh(f).run()
    assert f.calls()==before


def test_registry_lock_blocks_concurrent_new_submit(case):
    f,root,task,g,request,payload=case
    with g.lock():
        with pytest.raises(ValueError,match='submission_request_busy'):fresh(f).run()


def test_reconciliation_cannot_run_mutating_driver(case):
    f,root,task,g,request,payload=case;request['driver']['read_only']=False
    with pytest.raises(ValueError,match='read_only_submission'):guard.reconcile(task,root,request)
    assert not Path(request['driver']['receipt']).exists()


def test_old_cancelled_request_cannot_be_implicitly_retried_in_same_attempt(case):
    f,root,task,g,request,payload=case;guard.reconcile(task,root,request)
    before=f.calls()
    with pytest.raises(ValueError,match='new_attempt'):external.Operation(f.spec,root/'14-external-state.json',10).run()
    assert f.calls()==before


def test_new_pending_request_after_valid_closure_has_its_own_resolution(case):
    f,root,task,g,request,payload=case;guard.reconcile(task,root,request)
    next_root=f.root/'next-attempt';next_root.mkdir()
    f.fixture(error_command='submit',error={'status':'pending_approval','approval_id':'second','mode':'self'})
    driver={'external_operation':f.spec,'receipt':str(next_root/'14-receipt.json')}
    assert pipeline.execute(driver,next_root,'14',15)['status']=='failed'
    next_task={'drivers':{'14':driver}};next_request=deepcopy(request)
    next_request.update(task_digest=pipeline.digest(pipeline.current_task(next_task,next_root)),
                        pending_approval={'status':'pending_approval','approval_id':'second','mode':'self'})
    receipt=next_root/'submission-reconciliation/14/check-1/readback.json'
    next_request['driver']['receipt']=str(receipt);next_request['driver']['argv'][-1]=str(receipt)
    value=json.loads(payload.read_bytes());value['approval']['approval_id']='second';payload.write_text(json.dumps(value))
    assert guard.reconcile(next_task,next_root,next_request)['no_submission_performed']
    g.load();assert g.state['phase']=='resolved-no-effect'
    assert any(row.get('event')=='previous-resolution' for row in g.state['history'])


def test_two_actual_local_processes_cannot_both_submit(case):
    f,root,task,g,request,payload=case;guard.reconcile(task,root,request);f.fixture()
    path=f.root/'spec.json';path.write_text(json.dumps(f.spec));jobs=[]
    for name in ['parallel-a','parallel-b']:
        target=f.root/name;target.mkdir()
        argv=[sys.executable,'-B',str(Path(external.__file__)),'--spec',str(path),'--state',str(target/'op.json'),
              '--receipt',str(target/'receipt.json'),'--node','14','--timeout','15']
        jobs.append(subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
    for job in jobs:
        stdout,stderr=job.communicate(timeout=20)
        assert job.returncode in (0,1),(stdout,stderr)
    assert sorted(job.returncode for job in jobs)==[0,1]
    assert sum(name=='submit' for _,name in f.calls())==2


def test_public_full_coordinator_retains_pending_and_never_falls_back(case):
    import test_orchestration as fixtures
    f,root,_,g,request,payload=case
    original=fixtures.OrchestrationTests();original.setUp()
    try:
        target=f.root/'public-attempt';target.mkdir();task=original.task;task['drivers'].pop('5');task['timeout_seconds']=60
        script=f.root/'node.py';script.write_text("import json,sys;from pathlib import Path;Path(sys.argv[2]).write_text(json.dumps({'task_node':sys.argv[1],'status':'failed' if sys.argv[1]=='15' else 'completed'}))")
        for node in task['drivers']:
            task['drivers'][node]={'argv':[sys.executable,'-B',str(script),node,str(target/(node+'.json'))],'receipt':str(target/(node+'.json'))}
        task['drivers']['14']['external_operation']=f.spec
        task['cleanup_driver']={'argv':[sys.executable,'-B',str(script),'cleanup',str(target/'release.json')],'receipt':str(target/'release.json')}
        path=f.root/'full-task.json';path.write_text(json.dumps(task));before=f.calls()
        command=[sys.executable,'-B',str(Path(pipeline.__file__)),'--task',str(path),'--output',str(target)]
        for _ in range(2):
            result=subprocess.run(command,capture_output=True,text=True,timeout=30)
            assert result.returncode==0,result.stderr
            summary=json.loads(result.stdout)
            assert summary['nodes']['14']['status']=='failed' and summary['nodes']['14']['uncertain_effect']
            assert summary['nodes']['14']['observation']['pending_approval']['approval_id']=='cpu-approval'
        assert f.calls()==before
        assert len(pipeline.graph(task))==34
    finally:original.doCleanups()


def test_missing_project_registry_cannot_silently_create_attempt_local_guard(case):
    f,root,task,g,request,payload=case;f.spec.pop('submission_registry');before=f.calls()
    with pytest.raises(ValueError,match='shared_submission_registry_required'):fresh(f).run()
    assert f.calls()==before


def test_repeated_resolution_allows_semantically_identical_json_key_order(case):
    f,root,task,g,request,payload=case;guard.reconcile(task,root,request)
    reordered=dict(reversed(list(request.items())))
    assert guard.reconcile(task,root,reordered)['no_submission_performed']


def test_approval_in_shared_ledger_survives_missing_local_response_checkpoint(case):
    f,root,task,g,request,payload=case
    state=root/'14-external-state.json';value=json.loads(state.read_bytes());value.pop('pending_approval');external.save(state,value)
    assert guard.reconcile(task,root,request)['no_submission_performed']


def test_distinct_authorized_experiments_do_not_share_a_global_single_job_lock(case):
    f,root,task,g,request,payload=case
    different={**f.spec,'request_key':'different-experiment','component_uid':'other-component'}
    second=guard.Guard(different,f.root/'different-state.json')
    with g.lock(),second.lock():
        second.load();assert second.state['phase']=='new'
        assert second.path!=g.path
