"""Bounded analysis packets retain source retrieval and full-history validation."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import pytest
import loop_analysis as analysis
import history_context as context
from task_pipeline import digest
from test_generic_project_loop import config,record

def fixture(tmp_path,count=100):
    cfg=config();cfg.update(max_rounds=count+5,history_context={'recent_rounds':2,'max_context_bytes':16000})
    cfg['allowed_variables']['depth']['maximum']=1000
    study=tmp_path/'study';study.mkdir();history=[]
    for number in range(1,count+1):
        row=record({'depth':number,'optimizer':'adam'});row['round']=number
        row['comparison']={'comparable':number!=21}
        row['training']['actual']['log_excerpt']='CPU fixture detail '*200
        if number==17:row['training']['status']='failed'
        if number==9:row['training']['uncertain_effect']=True
        root=study/f'round-{number:02d}';root.mkdir();(root/'round-observation.json').write_text(json.dumps(row))
        history.append(row)
    root=study/f'round-{count:02d}'
    return cfg,history,root

def response(packet,depth=101):
    key='r50.evaluation.scores.rmse'
    return {'request_digest':digest(packet),'action':'run-next-round',
        'facts':[{'evidence_id':key,'value':0.3}],
        'hypotheses':[{'statement':'CPU fixture alternative','evidence_ids':[key],'limitations':'Not training evidence'}],
        'previous_experiment_assessment':'Compare retained trials','reason':'CPU contract check',
        'changed_variable':'depth','next_parameters':{'depth':depth,'optimizer':'adam'},
        'hypothesis':'A different depth may change results','expected_observations':['Compare RMSE']}

def test_hundred_round_context_is_bounded_and_critical_counts_remain(tmp_path):
    cfg,history,root=fixture(tmp_path);packet=analysis.request(history,cfg,root)
    assert len(json.dumps(packet,ensure_ascii=False,separators=(',',':')).encode())<=16000
    assert len(packet['history'])<=5 and packet['round']==100
    assert packet['history_summary']['training_failure_count']==1
    assert packet['history_summary']['incomparable_count']==1
    assert packet['history_summary']['uncertain_round_count']==1
    assert packet['history_summary']['metric_observations']['evaluation.scores.rmse']['count']==98
    assert context.complete(packet['history_archive'])==history

def test_omitted_original_round_can_be_retrieved_and_cited(tmp_path):
    cfg,history,root=fixture(tmp_path);packet=analysis.request(history,cfg,root)
    assert not any(row['round']==50 for row in packet['history'])
    assert context.read_round(packet['history_archive'],50)==history[49]
    assert analysis.validate(response(packet),packet)['action']=='run-next-round'

def test_parameter_seen_only_in_omitted_history_is_still_rejected(tmp_path):
    cfg,history,root=fixture(tmp_path);packet=analysis.request(history,cfg,root)
    with pytest.raises(ValueError,match='repeated_loop_configuration'):analysis.validate(response(packet,50),packet)

def test_changed_archived_observation_is_rejected(tmp_path):
    cfg,history,root=fixture(tmp_path);packet=analysis.request(history,cfg,root)
    (root.parent/'round-50/round-observation.json').write_text('{}')
    with pytest.raises(ValueError,match='observation_digest_mismatch'):analysis.validate(response(packet),packet)

def test_summary_cannot_hide_archived_failure(tmp_path):
    cfg,history,root=fixture(tmp_path);packet=analysis.request(history,cfg,root)
    packet['history_summary']['training_failure_count']=0
    with pytest.raises(ValueError,match='summary_not_supported'):analysis.validate(response(packet),packet)

def test_index_append_does_not_invalidate_old_analysis_reference(tmp_path):
    cfg,history,root=fixture(tmp_path);packet=analysis.request(history,cfg,root)
    extra=deepcopy(history[-1]);extra['round']=101;extra['parameters']['depth']=101
    next_root=root.parent/'round-101';next_root.mkdir();(next_root/'round-observation.json').write_text(json.dumps(extra))
    analysis.request(history+[extra],cfg,next_root)
    assert context.complete(packet['history_archive'])==history

def test_too_small_budget_fails_without_silent_truncation(tmp_path):
    cfg,history,root=fixture(tmp_path);cfg['history_context']['max_context_bytes']=100
    with pytest.raises(ValueError,match='history_context_budget_exceeded'):analysis.request(history,cfg,root)
    assert context.read_round(context.archive(history,root),100)==history[-1]

def test_public_loop_cli_reads_archive_without_project_driver(tmp_path):
    cfg,history,root=fixture(tmp_path);packet=analysis.request(history,cfg,root)
    source=root/'analysis-request.json';source.write_text(json.dumps(packet))
    result=subprocess.run([sys.executable,'-B',str(Path(analysis.__file__).with_name('run_project_loop.py')),
        '--history-request',str(source),'--history-round','50'],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==history[49]

def test_analysis_entrypoint_writes_compact_request_then_yields(tmp_path):
    cfg,history,root=fixture(tmp_path);cfg['long_loop']={'rounds_per_invocation':1}
    from generic_project_loop import write_once
    with pytest.raises(analysis.AnalysisPending):analysis.analyze(history,cfg,root,write_once)
    packet=json.loads((root/'analysis-request.json').read_bytes())
    assert packet['history_archive']['count']==100
    assert len((root/'analysis-request.json').read_bytes())<=16000

def test_legacy_request_without_policy_stays_full(tmp_path):
    cfg,history,root=fixture(tmp_path,4);cfg.pop('history_context')
    packet=analysis.request(history,cfg)
    assert packet['history']==history and 'history_archive' not in packet


@pytest.mark.parametrize('round_number',[1,50,100])
def test_streamed_lookup_validates_entire_index_before_return(tmp_path,round_number):
    cfg,history,root=fixture(tmp_path);ref=context.archive(history,root)
    path=Path(ref['path']);rows=path.read_bytes().splitlines(keepends=True)
    rows[-1]=rows[-1].replace(b'sha256:',b'sha257:');path.write_bytes(b''.join(rows))
    with pytest.raises(ValueError,match='index_digest_mismatch'):context.read_round(ref,round_number)


def test_streamed_read_does_not_build_full_index_list(tmp_path,monkeypatch):
    cfg,history,root=fixture(tmp_path);ref=context.archive(history,root)
    def forbidden(*args):raise AssertionError('full index list used')
    monkeypatch.setattr(context,'entries',forbidden)
    assert context.read_round(ref,50)==history[49]
    assert context.complete(ref)==history
