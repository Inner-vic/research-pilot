"""Complete decision and recovery verification without full observation materialization."""
from copy import deepcopy
import json
from unittest.mock import patch
import pytest
import history_context as context
import loop_analysis as analysis
from task_pipeline import digest
from test_history_context import fixture,response


def test_streamed_decision_does_not_materialize_history_or_skip_old_parameters(tmp_path):
    config,history,root=fixture(tmp_path);packet=analysis.request(history,config,root)
    with patch.object(context,'complete',side_effect=AssertionError('full observation list')):
        assert analysis.validate(response(packet),packet)['action']=='run-next-round'
        with pytest.raises(ValueError,match='repeated_loop_configuration'):analysis.validate(response(packet,2),packet)


def test_stream_scan_equals_full_reference_with_unicode_ties_failures_and_uncertainty(tmp_path):
    config,history,root=fixture(tmp_path)
    # Hash compatibility includes non-ASCII values and JSON ordering.
    history[0]['unicode']='原始证据'
    (root.parent/'round-01/round-observation.json').write_text(json.dumps(history[0],ensure_ascii=False),encoding='utf-8')
    ref=context.archive(history,root)
    result=context.scan(ref,config['project_metrics'],['r1.unavailable','r50.evaluation.scores.rmse'],history[1]['parameters'])
    assert result['summary']==context.aggregate(history,config['project_metrics'])
    assert result['latest']==history[-1] and result['repeated_parameters']
    assert context.sequence_digest(iter(history))==digest(history)
    assert result['citations']=={'r50.evaluation.scores.rmse':0.3}
    stat=result['summary']['metric_observations']['evaluation.scores.rmse']
    assert stat['min_round']==1 and stat['max_round']==100 and stat['count']==98


@pytest.mark.parametrize('change',['late-observation','late-index','history-digest'])
def test_early_citation_and_duplicate_match_still_verify_entire_archive(tmp_path,change):
    config,history,root=fixture(tmp_path);ref=context.archive(history,root)
    if change=='late-observation':(root.parent/'round-99/round-observation.json').write_text('{}')
    if change=='late-index':
        path=__import__('pathlib').Path(ref['path']);path.write_bytes(path.read_bytes().replace(b'"round":100',b'"round":999'))
    if change=='history-digest':ref['history_digest']='sha256:'+'0'*64
    with pytest.raises(ValueError):context.scan(ref,config['project_metrics'],['r1.evaluation.scores.rmse'],history[0]['parameters'])


def test_nonzero_invocation_cannot_be_accepted_after_recovery(tmp_path):
    import sys
    from task_pipeline import execute
    driver=tmp_path/'driver.py';receipt=tmp_path/'receipt.json'
    driver.write_text('import json,sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps({"task_node":"inventory","status":"completed"}))\nraise SystemExit(3)\n')
    spec={'argv':[sys.executable,'-B',str(driver),str(receipt)],'receipt':str(receipt),'read_only':True}
    assert execute(spec,tmp_path,'inventory',10)['reason']=='driver_exit_conflicts_with_completed_receipt'
    original=(tmp_path/'inventory-invocation.json').read_bytes()
    assert execute(spec,tmp_path,'inventory',10)['reason']=='driver_exit_conflicts_with_completed_receipt'
    assert (tmp_path/'inventory-invocation.json').read_bytes()==original
