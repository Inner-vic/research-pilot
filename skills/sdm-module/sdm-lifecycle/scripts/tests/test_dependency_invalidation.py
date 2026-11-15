"""Real local coordinator checks may be reused; compute and live facts may not."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch
import pytest
import subprocess
import sys
import task_pipeline as pipeline
import dependency_invalidation as deps

def inputs():return {'goal':'g1','source':'s1','data':'d1','parameters':{'depth':2},'protocol':'p1','environment':'e1'}

def parent(tmp_path):
    root=tmp_path/'parent';root.mkdir()
    cfg={'dependency_inputs':inputs(),'check_dependencies':{key:sorted(value) for key,value in deps.SCOPES.items()}};ctx=deps.context(cfg)
    nodes=pipeline.graph({'objective':'execute-and-learn'})
    task=pipeline.current_task({'objective':'execute-and-learn','dependency_context':ctx,
        'drivers':{'3':{'read_only':True},'4':{'read_only':True}}},root)
    (root/'coordinator-task.json').write_text(json.dumps(task))
    events=[]
    for key in ('3','4'):
        path=root/(key+'.json');path.write_text(json.dumps({'task_node':key,'status':'completed',
            'reusable_check':{'scope':'immutable-input-check','self_contained':True}}))
        events.append({'node':key,'task_digest':pipeline.digest(task),'result':{'status':'completed',
            'receipt':str(path),'receipt_digest':pipeline.digest_bytes(path.read_bytes())}})
    (root/'events.jsonl').write_text('\n'.join(json.dumps(row) for row in events)+'\n')
    return root,cfg,nodes

def test_parameter_change_invalidates_training_but_can_reuse_static_data_check(tmp_path):
    root,cfg,nodes=parent(tmp_path);cfg['reuse_from']=str(root);cfg['experiment_parameters']={'depth':3}
    output=tmp_path/'next';output.mkdir()
    task={'drivers':{'3':{'read_only':True,'receipt':str(output/'3.json')},
                     '4':{'read_only':True,'receipt':str(output/'4.json')}}}
    deps.reuse_drivers(cfg,task,nodes,output)
    report=json.loads((output/'dependency-invalidation.json').read_bytes())
    assert report['reused']==['3','4'] and '15' in report['invalidated']
    with patch.object(pipeline.subprocess,'Popen',side_effect=AssertionError('must not dispatch')):
        result=pipeline.execute(task['drivers']['3'],output,'3',10)
        assert pipeline.execute(task['drivers']['3'],output,'3',10)==result
    assert result['reused'] and result['qualification']=='reused-project-check-not-new-execution'
    coverage=pipeline.coverage(nodes,{'3':result})
    assert coverage['reused_project_checks']==['3'] and '3' not in coverage['completed_project_drivers']

@pytest.mark.parametrize('dimension',['goal','source','data','protocol','environment'])
def test_relevant_input_change_invalidates_data_check(tmp_path,dimension):
    root,cfg,nodes=parent(tmp_path);before=deps.context(cfg)
    cfg['dependency_inputs'][dimension]='changed';after=deps.context(cfg)
    changed,affected=deps.invalidated(nodes,before,after)
    assert dimension in changed and '3' in affected and '15' in affected

def test_missing_project_cache_contract_falls_back_to_real_execution(tmp_path):
    root,cfg,nodes=parent(tmp_path);path=root/'3.json';path.write_text(json.dumps({'task_node':'3','status':'completed'}))
    rows=[json.loads(v) for v in (root/'events.jsonl').read_text().splitlines()]
    rows[0]['result']['receipt_digest']=pipeline.digest_bytes(path.read_bytes())
    (root/'events.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    cfg['reuse_from']=str(root);out=tmp_path/'out';out.mkdir()
    task={'drivers':{'3':{'read_only':True,'receipt':str(out/'3.json')}}}
    deps.reuse_drivers(cfg,task,nodes,out)
    assert 'reuse_receipt' not in task['drivers']['3']

def test_live_external_observation_is_never_replaced_by_cache(tmp_path):
    root,cfg,nodes=parent(tmp_path);cfg['reuse_from']=str(root);out=tmp_path/'out';out.mkdir()
    task={'drivers':{'3':{'read_only':True,'receipt':str(out/'3.json'),'external_operation':{'action':'read'}}}}
    deps.reuse_drivers(cfg,task,nodes,out)
    assert 'external_operation' in task['drivers']['3']

def test_changed_source_receipt_is_rejected(tmp_path):
    root,cfg,nodes=parent(tmp_path);(root/'3.json').write_text('{}');cfg['reuse_from']=str(root)
    out=tmp_path/'out';out.mkdir();task={'drivers':{'3':{'read_only':True,'receipt':str(out/'3.json')}}}
    with pytest.raises(ValueError,match='pinned_json_changed'):deps.reuse_drivers(cfg,task,nodes,out)

def test_compute_node_cannot_enter_receipt_reuse(tmp_path):
    with pytest.raises(ValueError,match='immutable_check_reuse_only'):
        deps.execute_reuse({'reuse_node':'15','read_only':True},'15')

def test_default_dependencies_do_not_assume_parameter_independence(tmp_path):
    root,cfg,nodes=parent(tmp_path);cfg.pop('check_dependencies')
    first=deps.context(cfg);cfg['experiment_parameters']={'depth':9};second=deps.context(cfg)
    assert first['fingerprints']['3']!=second['fingerprints']['3']

def test_project_cannot_drop_required_data_dependency():
    with pytest.raises(ValueError,match='conservative_check_dependencies'):
        deps.context({'dependency_inputs':inputs(),'check_dependencies':{'3':['source']}})

def test_two_trials_through_public_project_cli_reuse_only_opted_in_checks(tmp_path):
    import test_project_driver_bundle as bundles
    from test_orchestration import OrchestrationTests
    code=bundles.DRIVER.replace('pathlib.Path(a.receipt).write_text(json.dumps(result))',
        'if a.node in ("3","4"):result["reusable_check"]={"scope":"immutable-input-check","self_contained":True}\npathlib.Path(a.receipt).write_text(json.dumps(result))')
    with patch.object(bundles,'DRIVER',code):_,cfg=bundles.fixture(tmp_path)
    f=OrchestrationTests();f.setUp()
    try:
        cfg.update(orchestration=deepcopy(f.task['orchestration']),dependency_inputs=inputs(),
                   read_only_task_nodes=['3','4'],experiment_parameters={'depth':2},
                   check_dependencies={key:sorted(value) for key,value in deps.SCOPES.items()},
                   task_timeout_seconds=7200,node_timeout_seconds=7200,
                   cleanup_timeout_seconds=7200,task_wall_time_seconds=36000)
        entry=Path(pipeline.__file__).with_name('run_project_pipeline.py')
        def invoke(configuration,name):
            path=tmp_path/(name+'.json');path.write_text(json.dumps(configuration));output=tmp_path/name
            result=subprocess.run([sys.executable,'-B',str(entry),'--project',str(path),'--output',str(output)],
                                  capture_output=True,text=True,timeout=30)
            assert result.returncode==0,result.stderr
            return output/'attempt-01'
        first=invoke(cfg,'first');next_cfg=deepcopy(cfg)
        next_cfg.update(run_id='forecast-next',reuse_from=str(first),experiment_parameters={'depth':3})
        second=invoke(next_cfg,'second')
        summary=json.loads((second/'summary.json').read_bytes())
        assert summary['coverage']['reused_project_checks']==['3','4']
        calls=[json.loads(line)['node'] for line in (second/'fixture-calls.jsonl').read_text().splitlines()]
        assert '3' not in calls and '4' not in calls and '16' in calls
        assert summary['nodes']['16']['status']=='failed'  # No model was executed.
    finally:f.doCleanups()
