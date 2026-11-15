"""Whole-session read-only reconciliation with real local readback subprocesses."""
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import pytest
import task_pipeline as tasks
import generic_project_loop as core
import session_reconciliation as rec

def case(tmp_path,count=2):
    study=tmp_path/'study';session=study/'sessions/s1';session.mkdir(parents=True)
    cfg={'run_id':'CPU-study'};(study/'loop-config.json').write_text(json.dumps(cfg))
    allocations=[];intents=[];readbacks=[]
    for index in range(1,count+1):
        root=study/f'round-{index:02d}';attempt=root/'attempt-01';attempt.mkdir(parents=True)
        task={'run_id':f'CPU-trial-{index}'};(attempt/'coordinator-task.json').write_text(json.dumps(task))
        identity={'provider':'CPU-fixture','namespace':'local','uid':str(index)}
        row={'node':'14','task_digest':tasks.digest(task),'result':{'status':'completed','observation':{'resource_identity':identity}}}
        (attempt/'events.jsonl').write_text(json.dumps(row)+'\n')
        allocations.append({'attempt':str(attempt),'node':'14','identity_field':'observation.resource_identity'})
        intents.append({'round':index,'round_root':str(root)})
        readbacks.append({'resource_identity':identity,'resource':{'status':'stopped','released':True,'pods_complete':True,'pods':[]},
            'source_ref':'CPU simulated readback only','observed_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    (session/'dispatch-intents.json').write_text(json.dumps(intents))
    failure={'status':'failed','reason':'CPU simulated cleanup failure'};(session/'cleanup-result.json').write_text(json.dumps(failure))
    payload=tmp_path/'payload.json';payload.write_text(json.dumps({'task_node':'cleanup-readback','status':'completed','resource_readbacks':readbacks}))
    counter=tmp_path/'calls';script=tmp_path/'readback.py'
    script.write_text('from pathlib import Path\nimport sys\np,r,c=map(Path,sys.argv[1:])\nc.write_text(c.read_text()+"read\\n" if c.exists() else "read\\n")\nr.write_bytes(p.read_bytes())\n')
    run=session/'reconciliation/check-1'
    request={'schema':'sdm-session-reconciliation/v1','reconciliation_id':'check-1',
        'original_cleanup_digest':rec.file_digest(session/'cleanup-result.json'),'allocations':allocations,
        'driver':{'read_only':True,'timeout_seconds':10,'argv':[sys.executable,'-B',str(script),str(payload),str(run/'readback.json'),str(counter)],
                  'receipt':str(run/'readback.json')}}
    return cfg,study,session,request,payload,counter

def test_all_resources_are_accepted_without_overwriting_failure_and_no_repeat(tmp_path):
    cfg,study,session,request,payload,counter=case(tmp_path)
    original=(session/'cleanup-result.json').read_bytes()
    assert rec.reconcile(cfg,study,session,request)['status']=='completed'
    assert core.read_cleanup(session/'cleanup-result.json')['status']=='completed'
    rec.reconcile(cfg,study,session,request)
    assert counter.read_text()=='read\n'
    assert (session/'cleanup-result.json').read_bytes()==original

def test_uncovered_dispatch_cannot_be_ignored(tmp_path):
    cfg,study,session,request,payload,counter=case(tmp_path)
    request['allocations'].pop()
    with pytest.raises(ValueError,match='all_session_dispatches'):rec.reconcile(cfg,study,session,request)
    assert not counter.exists()

@pytest.mark.parametrize('problem',['missing','wrong-uid','incomplete-pods','stale'])
def test_incomplete_or_invalid_release_evidence_is_rejected(tmp_path,problem):
    cfg,study,session,request,path,counter=case(tmp_path);value=json.loads(path.read_bytes())
    if problem=='missing':value['resource_readbacks'].pop()
    if problem=='wrong-uid':value['resource_readbacks'][0]['resource_identity']['uid']='unrelated'
    if problem=='incomplete-pods':value['resource_readbacks'][0]['resource']['pods_complete']=False
    if problem=='stale':value['resource_readbacks'][0]['observed_at']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=6)).isoformat()
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):rec.reconcile(cfg,study,session,request)
    assert not (session/'cleanup-reconciled.json').exists()

def test_mutating_driver_is_not_launched(tmp_path):
    cfg,study,session,request,path,counter=case(tmp_path);request['driver']['read_only']=False
    with pytest.raises(ValueError,match='read_only'):rec.reconcile(cfg,study,session,request)
    assert not counter.exists()

def test_accepted_proof_detects_original_and_readback_changes(tmp_path):
    cfg,study,session,request,path,counter=case(tmp_path);rec.reconcile(cfg,study,session,request)
    receipt=Path(request['driver']['receipt']);raw=receipt.read_bytes();receipt.write_text('{}')
    with pytest.raises(ValueError,match='readback_changed'):core.read_cleanup(session/'cleanup-result.json')
    receipt.write_bytes(raw);(session/'cleanup-result.json').write_text('{"status":"failed","reason":"changed"}')
    with pytest.raises(ValueError,match='binding_changed'):core.read_cleanup(session/'cleanup-result.json')

@pytest.mark.parametrize('missing_cleanup',[False,True])
def test_public_loop_cli_reconciles_only_and_keeps_failure(tmp_path,missing_cleanup):
    cfg,study,session,request,path,counter=case(tmp_path)
    if missing_cleanup:
        (session/'cleanup-result.json').unlink()
        (session/'dispatch-intent.json').write_text(json.dumps(json.loads((session/'dispatch-intents.json').read_bytes())[-1]))
        request.update(original_cleanup_digest=None,missing_cleanup=True)
    cfgfile=tmp_path/'project.json';cfgfile.write_text(json.dumps(cfg));source=tmp_path/'request.json';source.write_text(json.dumps(request))
    command=[sys.executable,'-B',str(Path(core.__file__).with_name('run_project_loop.py')),
             '--project',str(cfgfile),'--output',str(study),'--reconcile-session',str(session),'--reconciliation-request',str(source)]
    for _ in range(2):
        result=subprocess.run(command,capture_output=True,text=True,timeout=20)
        assert result.returncode==0,result.stderr
        assert json.loads(result.stdout)['status']=='completed'
    assert counter.read_text()=='read\n'

def test_missing_cleanup_after_failed_driver_does_not_fall_back_to_old_success(tmp_path):
    from test_long_loop import settings,fixtures
    import long_loop
    cfg=settings();adapter,pipe,observe,analyze,cleanup,prepared,calls,_=fixtures(tmp_path)
    with patch.object(core,'load_adapter',return_value=adapter),patch.object(core,'resolve',return_value=Path('/CPU-fixture')), \
         patch.object(core,'pipeline',side_effect=pipe),patch.object(core,'observation',side_effect=observe), \
         patch.object(core,'model_analyze',side_effect=analyze),patch.object(core,'execute',side_effect=ValueError('CPU cleanup exception')), \
         patch.object(long_loop,'authority',return_value=True):
        first=core.run(cfg,tmp_path/'actual-study');second=core.run(cfg,tmp_path/'actual-study')
    assert first['status']==second['status']=='cleanup-required' and len(calls)==1
    assert second['reason']=='interrupted_slice_requires_reconciliation'


@pytest.mark.parametrize('missing_cleanup',[False,True])
def test_reconciled_segment_resumes_only_next_round(tmp_path,missing_cleanup):
    from test_long_loop import settings,fixtures
    import long_loop
    cfg=settings();cfg['max_rounds']=2
    adapter,pipe,observe,analyze,cleanup,prepared,calls,_=fixtures(tmp_path,True)
    _,seed,seed_session,request,payload,counter=case(tmp_path/'seed',1)
    study=tmp_path/'actual-study'
    def pipeline(config,root):
        import shutil
        shutil.copytree(seed/'round-01/attempt-01',root/'attempt-01')
        return pipe(config,root)
    with patch.object(core,'load_adapter',return_value=adapter),patch.object(core,'resolve',return_value=Path('/CPU-fixture')), \
         patch.object(core,'pipeline',side_effect=pipeline),patch.object(core,'observation',side_effect=observe), \
         patch.object(core,'model_analyze',side_effect=analyze),patch.object(core,'execute',return_value=cleanup) as stop, \
         patch.object(long_loop,'authority',return_value=True):
        assert core.run(cfg,study)['status']=='cleanup-required'
        session=Path(json.loads((study/'long-loop-state.json').read_bytes())['cleanup_path']).parent
        original=(session/'cleanup-result.json').read_bytes()
        request['original_cleanup_digest']=rec.file_digest(session/'cleanup-result.json')
        if missing_cleanup:
            (session/'cleanup-result.json').unlink()
            request.update(original_cleanup_digest=None,missing_cleanup=True)
            state=json.loads((study/'long-loop-state.json').read_bytes());state['phase']='running'
            (study/'long-loop-state.json').write_text(json.dumps(state))
        request['allocations'][0]['attempt']=str(study/'round-01/attempt-01')
        request['driver']['receipt']=str(session/'reconciliation/check-1/readback.json')
        request['driver']['argv'][-2]=request['driver']['receipt']
        rec.reconcile(cfg,study,session,request)
        stop.return_value={'status':'completed','observation':{'resource':{'phase':'stopped'}}}
        (study/'PAUSE').touch()
        assert core.run(cfg,study)['status']=='paused'
        assert len(calls)==1 and stop.call_count==1
        (study/'PAUSE').unlink()
        assert core.run(cfg,study)['status']=='stopped'
    assert len(calls)==stop.call_count==2 and prepared==[1,2]
    if missing_cleanup:assert not (session/'cleanup-result.json').exists()
    else:assert (session/'cleanup-result.json').read_bytes()==original
    assert counter.read_text()=='read\n'


def test_missing_cleanup_requires_explicit_absence_and_preserves_it(tmp_path):
    cfg,study,session,request,payload,counter=case(tmp_path,1)
    (session/'dispatch-intent.json').write_bytes((session/'dispatch-intents.json').read_bytes().strip()[1:-1])
    (session/'cleanup-result.json').unlink();request['original_cleanup_digest']=None
    with pytest.raises(ValueError,match='explicit_missing'):rec.reconcile(cfg,study,session,request)
    assert not counter.exists()
    request['missing_cleanup']=True
    value=rec.reconcile(cfg,study,session,request)
    assert value['observation']['original_cleanup_missing'] and not value['observation']['original_failure_retained']
    assert not (session/'cleanup-result.json').exists()
    rec.reconcile(cfg,study,session,request);assert counter.read_text()=='read\n'
    (session/'cleanup-result.json').write_text('{"status":"failed"}')
    with pytest.raises(ValueError,match='binding_changed'):core.read_cleanup(session/'cleanup-result.json')


def test_missing_dispatch_manifest_still_blocks_recovery(tmp_path):
    cfg,study,session,request,payload,counter=case(tmp_path,1)
    (session/'dispatch-intents.json').unlink()
    with pytest.raises(ValueError,match='complete_session_dispatch_manifest'):rec.reconcile(cfg,study,session,request)
    assert not counter.exists()
