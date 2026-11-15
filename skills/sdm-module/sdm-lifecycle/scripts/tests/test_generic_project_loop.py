"""A second project exercises the generic contract without RoadModel fields."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import pytest
sys.path.insert(0, str(Path(__file__).parents[1]))
import generic_project_loop as core
from loop_analysis import request, validate
from loop_contract import compare, load_adapter, parameter_values
from task_pipeline import digest, digest_bytes


def config():
    return {'run_id': 'forecast-fixture', 'provider': 'forecast-v1', 'max_rounds': 2,
            'max_attempts': 1, 'analysis_objective': 'Diagnose forecast RMSE',
            'initial_parameters': {'depth': 2, 'optimizer': 'adam'},
            'allowed_variables': {'depth': {'type': 'integer', 'minimum': 1, 'maximum': 5},
                                  'optimizer': {'type': 'enum', 'choices': ['adam', 'sgd']}},
            'project_metrics': ['evaluation.scores.rmse'],
            'loop_adapter': {'path': '/fixture/adapter.py', 'digest': 'fixture'},
            'project_driver': {'path': '/fixture/driver.py', 'digest': 'fixture'},
            'comparison_conditions': [
                {'name': 'code', 'role': 'code', 'training_field': 'version', 'expected': 'c1'},
                {'name': 'data', 'role': 'data', 'training_field': 'dataset', 'expected': 'd1'},
                {'name': 'window', 'training_field': 'window', 'evaluation_field': 'window'},
                {'name': 'optional-clock', 'training_field': 'clock', 'required': False}],
            'cleanup_requirements': [{'field': 'resource.phase', 'equals': 'stopped'}]}


def record(parameters=None, drift=False, failed=False):
    return {'round': 1, 'parameters': parameters or config()['initial_parameters'],
            'training': {'status': 'failed' if failed else 'completed',
                         'actual': {'version': 'c1', 'dataset': 'd1', 'window': 99 if drift else 24}},
            'evaluation': {'status': 'completed', 'actual': {'window': 24, 'scores': {'rmse': 0.3}}}}


def response(packet, action='run-next-round'):
    key = 'r1.evaluation.scores.rmse'
    return {'request_digest': digest(packet), 'action': action,
            'facts': [{'evidence_id': key, 'value': 0.3}],
            'hypotheses': [{'statement': 'Model capacity may matter', 'evidence_ids': [key],
                            'limitations': 'One fixture result cannot establish a cause'}],
            'previous_experiment_assessment': 'Initial observation', 'reason': 'Test capacity',
            'changed_variable': 'depth', 'next_parameters': {'depth': 3, 'optimizer': 'adam'},
            'hypothesis': 'An extra depth may change RMSE', 'expected_observations': ['Compare RMSE']}


def test_arbitrary_integer_enum_nested_metric_and_optional_missing_condition():
    cfg = config(); row = record(); row['comparison'] = compare(cfg, row['training'], row['evaluation'], None)
    assert row['comparison']['comparable']
    assert row['comparison']['findings'] == [{'condition': 'optional-clock', 'problem': 'unmeasured', 'required': False}]
    packet = request([row], cfg)
    assert validate(response(packet), packet)['next_parameters']['depth'] == 3
    proposal = response(packet); proposal.update(changed_variable='optimizer', next_parameters={'depth': 2, 'optimizer': 'sgd'})
    assert validate(proposal, packet)['next_parameters']['optimizer'] == 'sgd'
    row['execution_findings'] = [{'node': '1', 'status': 'failed', 'reason': 'configuration missing'}]
    assert request([row], cfg)['evidence']['r1.execution_findings'] == row['execution_findings']


@pytest.mark.parametrize('drift,failed', [(True, False), (False, True)])
def test_bad_comparison_or_failed_execution_still_allows_grounded_diagnosis(drift, failed):
    cfg = config(); row = record(drift=drift, failed=failed)
    row['comparison'] = compare(cfg, row['training'], row['evaluation'], None)
    packet = request([row], cfg)
    with pytest.raises(ValueError, match='incomparable|incomplete'):
        validate(response(packet), packet)
    assert validate(response(packet, 'stop-analysis'), packet)['action'] == 'stop-analysis'


def test_bounds_types_code_data_and_adapter_digest_are_enforced(tmp_path):
    cfg = config()
    for params in ({'depth': 2.0, 'optimizer': 'adam'}, {'depth': 9, 'optimizer': 'adam'}, {'depth': 2, 'optimizer': 'unknown'}):
        with pytest.raises(ValueError): parameter_values(params, cfg['allowed_variables'])
    cfg['comparison_conditions'][0]['required'] = False
    with pytest.raises(ValueError, match='code_and_data'): core.check_config(cfg)
    adapter = tmp_path / 'adapter.py'; adapter.write_text('raise AssertionError("changed code must never execute")')
    with pytest.raises(ValueError, match='adapter_changed'): load_adapter({'path': str(adapter), 'digest': 'wrong'})


def test_actual_packet_hash_is_checked_before_interpreting_report(tmp_path):
    raw = json.dumps({'report': {'version': 'c1'}}).encode(); path = tmp_path / 'packet.json'; path.write_bytes(raw)
    adapter = SimpleNamespace(observation_witness=lambda *args: {'packet_path': str(path), 'report_pointer': 'report', 'digest_pointer': 'hash'})
    summary = {'nodes': {'15': {'status': 'completed', 'observation': {'signed_result': {'result_digest': digest_bytes(raw)}}}}}
    with patch.object(core, 'verified_s13', return_value=False):
        with pytest.raises(ValueError, match='not_verified'): core.observation(adapter, tmp_path, summary, 'training', '15')
    path.write_text('{}')
    with patch.object(core, 'verified_s13', return_value=True):
        with pytest.raises(ValueError, match='packet_changed'): core.observation(adapter, tmp_path, summary, 'training', '15')


def test_generic_two_round_entry_resume_and_error_cleanup(tmp_path):
    cfg = config(); output = tmp_path / 'output'; dispatches = []; preparations = []
    def prepare(*args):
        preparations.append(args[2])
        return {'source_branch_head': 'report-' + str(len(preparations))}
    adapter = SimpleNamespace(prepare_round=prepare, retention_driver=lambda *args: None,
                              cleanup_driver=lambda *args: {'argv': ['stop'], 'receipt': str(tmp_path / 'cleanup.json')})
    def pipeline(conf, root):
        receipt = root / 'fixture-dispatch.json'
        if not receipt.exists():
            receipt.write_text('{}'); dispatches.append(conf['loop_parameters'])
        return {'attempts': [{'status': 'complete'}]}
    def observed(adapter, root, summary, stage, node):
        params = json.loads((root / 'round-plan.json').read_bytes())['parameters']
        row = record(params)[stage]
        if stage == 'training': row['actual']['loop_parameters'] = params
        return row
    def analyze(history, conf, root, write_once):
        packet = request(history, conf); value = response(packet)
        if len(history) == 2: value['action'] = 'stop-budget'
        return validate(value, packet)
    cleanup = {'status': 'completed', 'observation': {'status': 'completed', 'resource': {'phase': 'stopped'}}}
    with patch.object(core, 'load_adapter', return_value=adapter), patch.object(core, 'resolve', return_value=Path('/driver')), \
         patch.object(core, 'pipeline', side_effect=pipeline), patch.object(core, 'observation', side_effect=observed), \
         patch.object(core, 'model_analyze', side_effect=analyze), patch.object(core, 'execute', return_value=cleanup) as stop:
        result = core.run(cfg, output); again = core.run(cfg, output)
    assert result['status'] == again['status'] == 'stopped'
    assert len(dispatches) == 2 and stop.call_count == 1 and preparations == [1, 2]
    assert result['rounds'][1]['changed_variable'] == 'depth'
    with patch.object(core, 'load_adapter', return_value=adapter), patch.object(core, 'resolve', return_value=Path('/driver')), \
         patch.object(core, 'pipeline', side_effect=ValueError('fixture fault')), patch.object(core, 'execute', return_value=cleanup) as stop:
        failed = core.run(cfg, tmp_path / 'failed')
    assert failed['status'] == 'blocked' and stop.call_count == 1
    assert failed['cleanup']['resource']['phase'] == 'stopped'


def test_external_review_releases_resources_and_resume_never_dispatches_again(tmp_path):
    from pro_review_session import bind
    import pro_review_workflow as reviews
    from review_storage import atomic_json, read_json
    cfg=config();output=tmp_path/'external-loop';session_root=tmp_path/'session'
    url='https://chatgpt.com/c/11111111-1111-1111-1111-111111111111'
    bind(session_root,'forecast','capacity-review',url)
    cfg.update(analysis_mode='external-review',external_review={
        'project':'forecast','workflow':'capacity-review','session_root':str(session_root)})
    adapter=SimpleNamespace(prepare_round=lambda *args:{},retention_driver=lambda *args:None,
        cleanup_driver=lambda *args:{'argv':['fixture-stop'],'receipt':str(tmp_path/'cleanup.json')})
    def observed(adapter,root,summary,stage,node):
        params=read_json(root/'round-plan.json')['parameters'];row=record(params)[stage]
        if stage=='training':row['actual']['loop_parameters']=params
        return row
    cleanup={'status':'completed','observation':{'status':'completed','resource':{'phase':'stopped'}}}
    with patch.object(core,'load_adapter',return_value=adapter),patch.object(core,'resolve',return_value=Path('/driver')), \
         patch.object(core,'pipeline',return_value={'attempts':[{'status':'complete','nodes':{}}]}) as dispatch, \
         patch.object(core,'observation',side_effect=observed),patch.object(core,'execute',return_value=cleanup) as stop:
        first=core.run(cfg,output)
        assert first['status']=='awaiting-analysis' and stop.call_count==1 and dispatch.call_count==1
        request_path=output/'round-01/analysis-request.json'
        context={'schema':'sdm-pro-review-context/v1','project':'forecast','workflow':'capacity-review',
            'fixed_conditions':{'code':'c1','dataset':'d1','window':24},'excluded_explanations':[],
            'remaining_unknowns':['Cause of RMSE'],'current_authorization':{
                'execution_authorized':False,'model_inference_allowed':False,'training_submission_allowed':False},
            'new_experiment_observations':True,'gap_matrix':[]}
        atomic_json(tmp_path/'context.json',context)
        item=reviews.prepare(session_root,request_path,tmp_path/'context.json',[],url,0)
        proof={'url':url,'model':'Pro','verified_visible':True,'request_digest':item['request_digest']}
        atomic_json(tmp_path/'receipt.json',proof);reviews.mark_delivery(session_root,tmp_path/'receipt.json')
        packet=read_json(Path(item['bundle'])/'analysis-request.json');raw=response(packet)
        raw.update(session_context_revision=0,session_context_digest=None,continuation_acknowledgement={
            'status':'continued','objective':'Review capacity','fixed_conditions':context['fixed_conditions'],
            'excluded_explanations':[],'remaining_unknowns':context['remaining_unknowns'],
            'current_scope':'review and plan','no_new_observations':False})
        atomic_json(tmp_path/'response.json',raw)
        reviews.ingest(session_root,tmp_path/'response.json',tmp_path/'receipt.json');reviews.plan(session_root)
        reviews.export_loop_result(session_root,item['review_id'],request_path,output/'round-01/external-review-result.json')
        again=core.run(cfg,output)
    assert again['status']=='planning-required'
    assert again['decision']['next_parameters']['depth']==3
    assert stop.call_count==1 and dispatch.call_count==1
    assert not (output/'round-02').exists()


def test_failed_cleanup_resume_cannot_prepare_or_dispatch_again(tmp_path):
    cfg=config();output=tmp_path/'failed-release'
    adapter=SimpleNamespace(prepare_round=lambda *args:{},retention_driver=lambda *args:None,
        cleanup_driver=lambda *args:{'argv':['fixture-stop'],'receipt':str(tmp_path/'cleanup.json')})
    def observed(adapter,root,summary,stage,node):
        params=json.loads((root/'round-plan.json').read_bytes())['parameters'];row=record(params)[stage]
        if stage=='training':row['actual']['loop_parameters']=params
        return row
    cleanup={'status':'failed','reason':'unknown stop outcome','uncertain_effect':True}
    with patch.object(core,'load_adapter',return_value=adapter),patch.object(core,'resolve',return_value=Path('/driver')), \
         patch.object(core,'pipeline',return_value={'attempts':[{'status':'complete','nodes':{}}]}) as dispatch, \
         patch.object(core,'observation',side_effect=observed), \
         patch.object(core,'model_analyze',return_value={'action':'stop-analysis'}) as analysis, \
         patch.object(core,'execute',return_value=cleanup) as stop:
        first=core.run(cfg,output);again=core.run(cfg,output)
    assert first['status']==again['status']=='cleanup-required'
    assert again['reason']=='prior_cleanup_requires_platform_reconciliation'
    assert dispatch.call_count==analysis.call_count==stop.call_count==1
    assert len(again['rounds'])==1


def test_released_resource_cannot_be_reacquired_for_new_round_on_resume(tmp_path):
    cfg=config();cfg['loop_timeout_seconds']=10;output=tmp_path/'budget-release'
    preparations=[]
    def prepare(*args):preparations.append(args[2]);return {}
    adapter=SimpleNamespace(prepare_round=prepare,retention_driver=lambda *args:None,
        cleanup_driver=lambda *args:{'argv':['fixture-stop'],'receipt':str(tmp_path/'cleanup.json')})
    def observed(adapter,root,summary,stage,node):
        params=json.loads((root/'round-plan.json').read_bytes())['parameters'];row=record(params)[stage]
        if stage=='training':row['actual']['loop_parameters']=params
        return row
    decision={'action':'run-next-round','next_parameters':{'depth':3,'optimizer':'adam'},'hypothesis':'fixture capacity'}
    cleanup={'status':'completed','observation':{'status':'completed','resource':{'phase':'stopped'}}}
    with patch.object(core,'load_adapter',return_value=adapter),patch.object(core,'resolve',return_value=Path('/driver')), \
         patch.object(core,'pipeline',return_value={'attempts':[{'status':'complete','nodes':{}}]}) as dispatch, \
         patch.object(core,'observation',side_effect=observed),patch.object(core,'model_analyze',return_value=decision), \
         patch.object(core,'execute',return_value=cleanup) as stop:
        with patch.object(core.time,'monotonic',side_effect=[0,0,11]):first=core.run(cfg,output)
        assert first['decision']['action']=='stop-time-budget'
        again=core.run(cfg,output)
    assert again['status']=='planning-required'
    assert dispatch.call_count==stop.call_count==1 and preparations==[1]
    assert not (output/'round-02').exists()


def test_uncertain_task_finding_cannot_authorize_successor_but_can_be_analyzed():
    cfg=config();row=record()
    row['comparison']=compare(cfg,row['training'],row['evaluation'],None)
    row['execution_findings']=[{'node':'20','status':'failed','uncertain_effect':True}]
    packet=request([row],cfg)
    with pytest.raises(ValueError,match='uncertain_task_effect'):
        validate(response(packet),packet)
    assert validate(response(packet,'stop-analysis'),packet)['action']=='stop-analysis'


@pytest.mark.parametrize('name', ['task_timeout_seconds','node_timeout_seconds',
                                 'task_wall_time_seconds','cleanup_timeout_seconds'])
def test_round_adapter_cannot_change_approved_time_budget(tmp_path,name):
    cfg=config()
    adapter=SimpleNamespace(prepare_round=lambda *args:{name:1800},
        cleanup_driver=lambda *args:{'argv':['fixture-stop'],'receipt':str(tmp_path/'stop.json')})
    with patch.object(core,'load_adapter',return_value=adapter), \
         patch.object(core,'resolve',return_value=Path('/fixture-driver')), \
         patch.object(core,'pipeline') as dispatch, \
         patch.object(core,'execute',return_value={'status':'completed',
             'observation':{'resource':{'phase':'stopped'}}}):
        result=core.run(cfg,tmp_path/'run')
    assert result['status']=='blocked'
    assert result['reason']=='round_adapter_cannot_change_execution_authority'
    dispatch.assert_not_called()
