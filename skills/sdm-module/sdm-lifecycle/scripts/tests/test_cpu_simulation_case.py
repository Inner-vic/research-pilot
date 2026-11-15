"""Synthetic CSV/TSV full local lifecycle; no model/platform evidence."""
import json,struct,zipfile,subprocess,sys,os
from pathlib import Path
import pytest
import project_workspace as w
from project_driver import file_digest

@pytest.fixture
def simulated(tmp_path):
    raw=tmp_path/'raw';raw.mkdir();(raw/'metrics.csv').write_text('split,score,count\ntrain,0.9,30\nvalidation,0.8,10\n')
    config={'schema':'sdm-project-workspace/v1','project_id':'synthetic','evidence_kind':'cpu-simulation','material_roots':[str(raw)],
        'material_preparation':{'schema':'sdm-material-preparation/v1','objective':'CPU simulation only','project_metrics':['evaluation.score'],
          'roles':[{'id':'candidate','root':str(raw),'table_globs':['metrics.csv'],'table_format':'csv','row_selector':{'split':'validation'},
                    'table_fields':{'score':{'column':'score','type':'float'},'count':{'column':'count','type':'int'}}}]}}
    return config,tmp_path/'workspace',raw


def respond(folder,kind='cpu-simulation'):
    from task_pipeline import digest
    packet=json.loads((folder/'analysis-request.json').read_bytes())
    value={'request_digest':digest(packet),'evidence_kind':kind,'action':'stop-analysis',
        'facts':[{'evidence_id':'r1.evaluation.score','value':packet['evidence']['r1.evaluation.score']}],
        'hypotheses':[{'statement':'Synthetic score observed','limitations':'Generated fixture; no model evidence','evidence_ids':['r1.evaluation.score']}],
        'previous_experiment_assessment':'CPU simulation only','reason':'Stop simulated offline analysis'}
    (folder/'analysis-response.json').write_text(json.dumps(value));return value


def test_full_table_case_preserves_simulation_through_cli_archive_and_resume(simulated):
    config,root,raw=simulated;waiting=w.run(config,root)
    assert waiting['evidence_kind']=='cpu-simulation' and waiting['status']=='awaiting-review'
    folder=Path(waiting['request_path']).parent;packet=json.loads((folder/'analysis-request.json').read_bytes())
    location=packet['provenance']['r1.evaluation.score']
    assert packet['evidence_kind']=='cpu-simulation' and location['record']==2 and location['end_line']==3 and location['column']=='score'
    assert location['digest']==file_digest(raw/'metrics.csv')
    respond(folder);result=w.run(config,root);assert result['evidence_kind']=='cpu-simulation'
    assert result['qualification'].startswith('CPU simulation') and not result['training_dispatched']
    case=next((root/'runs').iterdir())
    for name in ('diagnosis.json','review.json'):assert json.loads((case/name).read_bytes())['evidence_kind']=='cpu-simulation'
    assert '仅为CPU模拟验收' in (case/'REPORT.md').read_text()
    assert w.run(config,root)['resumed']
    cfg=root.parent/'config.json';cfg.write_text(json.dumps(config))
    r=subprocess.run([sys.executable,'-B',str(Path(w.__file__).with_name('run_project_pipeline.py')),'--project',str(cfg),'--output',str(root),'--project-workspace'],capture_output=True,text=True,timeout=20)
    public=json.loads(r.stdout);assert public['evidence_kind']=='cpu-simulation' and public['resumed']


def test_simulated_wait_and_failed_review_keep_qualification(simulated):
    config,root,_=simulated;plan=config.pop('material_preparation');assert w.run(config,root)['evidence_kind']=='cpu-simulation'
    assert json.loads((root/'material-preparation-inbox.json').read_bytes())['evidence_kind']=='cpu-simulation'
    config['material_preparation']=plan;wait=w.run(config,root);respond(Path(wait['request_path']).parent,'retained-material')
    failure=w.execute(config,root)
    assert failure['reason']=='simulation_review_qualification_required' and failure['evidence_kind']=='cpu-simulation'
    assert not (root/'latest.json').exists()


@pytest.mark.parametrize('replacement',[None,'retained-material'])
def test_registered_simulation_cannot_be_promoted_in_same_workspace(simulated,replacement):
    config,root,_=simulated;w.run(config,root)
    if replacement is None:config.pop('evidence_kind')
    else:config['evidence_kind']=replacement
    blocked=w.execute(config,root)
    assert blocked['reason']=='project_evidence_kind_changed_use_separate_workspace'
    assert blocked['evidence_kind']=='cpu-simulation' and blocked['requested_evidence_kind']=='retained-material'


@pytest.mark.parametrize('raw,reason',[
    ('split,score,count\nvalidation,0.8,10\nvalidation,0.9,12\n','one_unambiguous_table_row_required'),
    ('split,score,score\nvalidation,0.8,10\n','unique_table_headers_required'),
    ('split,score,count\nvalidation,0.8\n','rectangular_bounded_table_required'),
    ('split,score,count\ntrain,0.8,10\n','one_unambiguous_table_row_required'),
    ('split,score,count\nvalidation,NaN,10\n','nonfinite_material_observation'),
    ('split,score,count\nvalidation,0.8,1.5\n','table_integer_required'),
    ('split,score,count\nvalidation,,10\n','table_observation_value_missing'),
    ('split,score,count\n"validation,0.8,10\n','invalid_material_production_input:Error'),
])
def test_bad_tables_block_without_success(simulated,raw,reason):
    config,root,source=simulated;(source/'metrics.csv').write_text(raw)
    result=w.execute(config,root)
    assert result['status']=='blocked' and reason in result['reason']
    assert result['evidence_kind']=='cpu-simulation' and not (root/'latest.json').exists()


def test_tsv_selection_and_quoted_csv_are_supported(simulated):
    config,root,raw=simulated;p=raw/'metrics.csv';p.unlink();(raw/'metrics.tsv').write_text('split\tscore\tcount\nvalidation\t0.7\t12\n')
    role=config['material_preparation']['roles'][0];role.update(table_globs=['metrics.tsv'],table_format='tsv')
    wait=w.run(config,root);packet=json.loads(Path(wait['request_path']).read_bytes());assert packet['evidence']['r1.evaluation.score']==0.7
    (raw/'metrics.tsv').unlink();p.write_text('split,score,count\n"validation",0.8,10\n');role.update(table_globs=['metrics.csv'],table_format='csv')
    wait=w.run(config,root);assert json.loads(Path(wait['request_path']).read_bytes())['evidence']['r1.evaluation.score']==0.8


def test_same_stat_content_drift_invalidates_simulated_review(simulated):
    config,root,raw=simulated;wait=w.run(config,root);respond(Path(wait['request_path']).parent);w.run(config,root)
    p=raw/'metrics.csv';stat=p.stat();p.write_bytes(p.read_bytes().replace(b'0.8',b'0.7'));os.utime(p,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    changed=w.run(config,root);assert changed['status']=='awaiting-review' and changed['request_path']!=wait['request_path']


def test_table_bounds_and_missing_selector_reject(simulated):
    config,root,raw=simulated;role=config['material_preparation']['roles'][0];role.pop('row_selector')
    assert w.execute(config,root)['reason']=='explicit_table_row_selector_required'
    role['row_selector']={'split':'validation'};(raw/'metrics.csv').write_text('split,score,count\n'+'train,0.1,3\n'*4097)
    assert w.execute(config,root)['reason']=='table_row_budget_exceeded'


def test_simulation_packet_cannot_be_imported_as_real_case(simulated,tmp_path):
    config,root,_=simulated;wait=w.run(config,root);folder=Path(wait['request_path']).parent;respond(folder)
    real={'schema':'sdm-project-workspace/v1','project_id':'real','material_roots':[str(folder),*config['material_roots']]}
    result=w.execute(real,tmp_path/'other');assert result['reason']=='project_packet_evidence_kind_mismatch'
