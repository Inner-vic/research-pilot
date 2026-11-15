"""Persistent multi-invocation studies; CPU simulations only, never platform calls."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
import pytest
import generic_project_loop as core
import long_loop
from project_driver import file_digest
from test_generic_project_loop import config,record,response
from loop_analysis import request,validate

def settings():
    cfg=config();cfg.update(max_rounds=4,long_loop={'rounds_per_invocation':1},
        preparation_is_read_only=True,segmented_cleanup_supported=True)
    return cfg

def fixtures(tmp_path,cleanup_failed=False):
    preparations=[];dispatches=[];analyses=[]
    def prepare(*args):preparations.append(args[2]);return {}
    adapter=SimpleNamespace(prepare_round=prepare,retention_driver=lambda *args:None,
        cleanup_driver=lambda cfg,root,*args:{'argv':['CPU-stop-fixture'],'receipt':str(root/'receipt.json')})
    def pipeline(cfg,root):dispatches.append(cfg['run_id']);return {'attempts':[{'status':'complete','nodes':{}}]}
    def observed(adapter,root,summary,stage,node):
        params=json.loads((root/'round-plan.json').read_bytes())['parameters'];row=record(params)[stage]
        if stage=='training':row['actual']['loop_parameters']=params
        return row
    def analyze(history,cfg,root,writer):
        analyses.append(len(history));packet=request(history,cfg);result=response(packet)
        result['next_parameters']={'depth':history[-1]['parameters']['depth']+1,'optimizer':'adam'}
        if len(history)==cfg['max_rounds']:result['action']='stop-budget'
        return validate(result,packet)
    cleanup={'status':'failed' if cleanup_failed else 'completed','observation':{'resource':{'phase':'stopped'}}}
    return adapter,pipeline,observed,analyze,cleanup,preparations,dispatches,analyses

def test_four_round_study_runs_in_four_bounded_invocations_without_replaying(tmp_path):
    cfg=settings();adapter,pipe,observe,analyze,cleanup,prepared,calls,analyses=fixtures(tmp_path)
    with patch.object(core,'load_adapter',return_value=adapter),patch.object(core,'resolve',return_value=Path('/fixture')), \
         patch.object(core,'pipeline',side_effect=pipe),patch.object(core,'observation',side_effect=observe), \
         patch.object(core,'model_analyze',side_effect=analyze),patch.object(core,'execute',return_value=cleanup) as stop, \
         patch.object(long_loop,'authority',return_value=True):
        results=[core.run(cfg,tmp_path/'study') for _ in range(5)]
    assert [r['status'] for r in results]==['paused','paused','paused','stopped','stopped']
    assert len(calls)==len(prepared)==len(analyses)==stop.call_count==4
    assert prepared==analyses==[1,2,3,4]
    assert len(results[-1]['rounds'])==4
    assert not (tmp_path/'study/loop.lock').exists()

def test_completed_study_resume_does_not_republish_historical_delivery(tmp_path):
    cfg=settings();cfg.update(max_rounds=1,result_records={'CPU-fixture':True})
    adapter,pipe,observe,analyze,cleanup,_,_,_=fixtures(tmp_path)
    with patch.object(core,'load_adapter',return_value=adapter),patch.object(core,'resolve',return_value=Path('/fixture')), \
         patch.object(core,'pipeline',side_effect=pipe),patch.object(core,'observation',side_effect=observe), \
         patch.object(core,'model_analyze',side_effect=analyze),patch.object(core,'execute',return_value=cleanup), \
         patch.object(long_loop,'authority',return_value=True),patch.object(core,'deliver') as delivery:
        core.run(cfg,tmp_path/'study');count=delivery.call_count
        assert count==2
        core.run(cfg,tmp_path/'study')
        assert delivery.call_count==count

def test_failed_segment_cleanup_blocks_next_dispatch(tmp_path):
    cfg=settings();adapter,pipe,observe,analyze,cleanup,prepared,calls,_=fixtures(tmp_path,True)
    with patch.object(core,'load_adapter',return_value=adapter),patch.object(core,'resolve',return_value=Path('/fixture')), \
         patch.object(core,'pipeline',side_effect=pipe),patch.object(core,'observation',side_effect=observe), \
         patch.object(core,'model_analyze',side_effect=analyze),patch.object(core,'execute',return_value=cleanup) as stop, \
         patch.object(long_loop,'authority',return_value=True):
        first=core.run(cfg,tmp_path/'study');again=core.run(cfg,tmp_path/'study')
    assert first['status']==again['status']=='cleanup-required'
    assert len(calls)==stop.call_count==1

def test_abandoned_dispatch_with_no_cleanup_requires_reconciliation(tmp_path):
    root=tmp_path/'study';session=root/'sessions/old';session.mkdir(parents=True)
    (session/'dispatch-intent.json').write_text('{}')
    (root/'long-loop-state.json').write_text(json.dumps({'phase':'running','session_root':str(session)}))
    with patch.object(core,'_run') as execute:
        result=core.run(settings(),root)
    assert result['status']=='cleanup-required';execute.assert_not_called()

def test_changed_accepted_segment_cleanup_blocks_new_dispatch(tmp_path):
    root=tmp_path/'study';root.mkdir();cleanup=root/'cleanup.json';cleanup.write_text('{}')
    (root/'long-loop-state.json').write_text(json.dumps({'phase':'complete','cleanup_path':str(cleanup),'cleanup_digest':'changed'}))
    with patch.object(core,'_run') as execute:
        with pytest.raises(ValueError,match='cleanup_evidence_changed'):core.run(settings(),root)
    execute.assert_not_called()

def test_development_defaults_are_not_application_limits():
    cfg=config();cfg['max_rounds']=4
    assert core.check_config(cfg)[0]==4
    cfg=settings();cfg['max_rounds']=100;cfg['loop_timeout_seconds']=7200
    cfg['long_loop']['rounds_per_invocation']=10
    assert core.check_config(cfg)==(100,7200)
    cfg['long_loop']['rounds_per_invocation']=0
    with pytest.raises(ValueError,match='bounded_long_loop_slice'):core.check_config(cfg)

def test_open_study_requires_explicit_stop_criteria():
    cfg=settings();cfg['max_rounds']=None;cfg['loop_timeout_seconds']=None
    with pytest.raises(ValueError,match='stop_criteria'):core.check_config(cfg)
    cfg['study_stop_criteria']='Stop after sufficient comparable evidence or explicit user stop'
    assert core.check_config(cfg)==(None,None)

def test_open_study_remains_bounded_per_invocation(tmp_path):
    cfg=settings();cfg.update(max_rounds=None,loop_timeout_seconds=None,study_stop_criteria='Stop on sufficient evidence or user pause')
    adapter,pipe,observe,analyze,cleanup,prepared,calls,analyses=fixtures(tmp_path)
    with patch.object(core,'load_adapter',return_value=adapter),patch.object(core,'resolve',return_value=Path('/fixture')), \
         patch.object(core,'pipeline',side_effect=pipe),patch.object(core,'observation',side_effect=observe), \
         patch.object(core,'model_analyze',side_effect=analyze),patch.object(core,'execute',return_value=cleanup), \
         patch.object(long_loop,'authority',return_value=True):
        first=core.run(cfg,tmp_path/'study');second=core.run(cfg,tmp_path/'study')
    assert first['status']==second['status']=='paused'
    assert len(calls)==2 and len(second['rounds'])==2

def test_long_study_yields_immediately_when_analysis_is_missing(tmp_path):
    import loop_analysis
    cfg=settings();row=record()
    with patch.object(loop_analysis.time,'sleep',side_effect=AssertionError('must yield instead of waiting')):
        with pytest.raises(loop_analysis.AnalysisPending):loop_analysis.analyze([row],cfg,tmp_path,core.write_once)
    assert (tmp_path/'analysis-request.json').exists()

def test_large_driver_timeout_is_honored_without_waiting_for_it(tmp_path):
    import task_pipeline
    driver=tmp_path/'driver.py';receipt=tmp_path/'receipt.json'
    driver.write_text('import json,sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps({"task_node":"3","status":"completed"}))')
    result=task_pipeline.execute({'argv':[sys.executable,'-B',str(driver),str(receipt)],
        'receipt':str(receipt),'timeout_seconds':7200},tmp_path,'3',10000)
    assert result['status']=='completed'
    assert task_pipeline._configuration({'objective':'execute-and-learn','timeout_seconds':7200,
        'wall_time_seconds':36000,'cleanup_timeout_seconds':7200})[1:]==(7200,36000,7200)

def test_old_proposal_scope_cannot_authorize_new_round(tmp_path):
    from test_orchestration import OrchestrationTests
    f=OrchestrationTests();f.setUp()
    try:
        cfg={'study_id':'s','trial_id':'new','orchestration':f.task['orchestration']}
        with pytest.raises(ValueError,match='fresh_round_execution_scope'):long_loop.authority(cfg)
        f.update('proposal',execution_scope={'study_id':'s','trial_id':'new'})
        import orchestration as o
        f.update('confirmation',proposal_digest=o.value_digest(o.needed(f.task,'proposal')))
        assert long_loop.authority(cfg)
    finally:f.doCleanups()

def test_public_cli_pause_and_missing_confirmation_do_not_launch_driver(tmp_path):
    cfg=settings();adapter=tmp_path/'adapter.py'
    adapter.write_text('def prepare_round(*args): return {}\ndef observation_witness(*args): raise AssertionError("no observation")\n'
        'def retention_driver(*args): return None\ndef cleanup_driver(*args): raise AssertionError("no acquired resource")\n')
    driver=tmp_path/'driver.py';driver.write_text('raise AssertionError("must not execute")')
    cfg['loop_adapter']={'path':str(adapter),'digest':file_digest(adapter)}
    cfg['project_driver']={'path':str(driver),'digest':file_digest(driver)}
    source=tmp_path/'project.json';source.write_text(json.dumps(cfg))
    output=tmp_path/'study';output.mkdir();(output/'PAUSE').write_text('user pause')
    cmd=[sys.executable,'-B',str(Path(core.__file__).with_name('run_project_loop.py')),
         '--project',str(source),'--output',str(output)]
    result=subprocess.run(cmd,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['status']=='paused'
    (output/'PAUSE').unlink()
    result=subprocess.run(cmd,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['status']=='awaiting-user-confirmation'
