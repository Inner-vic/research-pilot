"""Generic raw production contracts; fixtures are not model evidence."""
import json,struct,zipfile
from pathlib import Path
import pytest
import project_workspace as w
from raw_materials import inspect_npy,inspect_zip


def npy(path,values=(1.0,2.0)):
    header=repr({'descr':'<f4','fortran_order':False,'shape':(len(values),)}).encode()+b'\n'
    path.write_bytes(b'\x93NUMPY\x01\x00'+struct.pack('<H',len(header))+header+struct.pack('<'+'f'*len(values),*values))


@pytest.fixture
def project(tmp_path):
    source=tmp_path/'raw';source.mkdir();(source/'run.log').write_text('score=0.75 n=12\nwarning\n')
    npy(source/'tensor.npy')
    with zipfile.ZipFile(source/'output.zip','w') as archive:archive.writestr('raw.txt','value')
    config={'schema':'sdm-project-workspace/v1','project_id':'unfamiliar','material_roots':[str(source)],
        'material_preparation':{'schema':'sdm-material-preparation/v1','objective':'Inspect retained observations',
            'inspect_array_finite':True,'inspect_zip_crc':True,'project_metrics':['evaluation.score'],
            'roles':[{'id':'observed','root':str(source),'log_globs':['*.log'],
                'text_rules':[{'pattern':r'score=(?P<score>[\d.]+) n=(?P<count>\d+)','groups':{'score':'float','count':'int'}}],
                'text_flags':[{'name':'warning_present','pattern':'warning'}]}]}}
    return config,tmp_path/'workspace',source


def respond(path):
    packet=json.loads((path/'analysis-request.json').read_bytes())
    manifest=json.loads((path/'manifest.json').read_bytes())
    response={'request_digest':manifest['request_digest'],'action':'stop-analysis',
        'facts':[{'evidence_id':'r1.evaluation.score','value':packet['evidence']['r1.evaluation.score']}],
        'hypotheses':[{'statement':'Observed proxy only','limitations':'Fixture, not model quality','evidence_ids':['r1.evaluation.score']}],
        'previous_experiment_assessment':'Offline observation','reason':'Archive only'}
    (path/'analysis-response.json').write_text(json.dumps(response))


def test_raw_default_registers_artifacts_and_emits_context_task(project):
    config,root,source=project;config.pop('material_preparation')
    waiting=w.run(config,root)
    assert waiting['status']=='awaiting-evidence'
    task=json.loads((root/'material-preparation-inbox.json').read_bytes())
    assert len(task['inventory'])==3 and task['execution_allowed'] is False
    assert not (root/'runs').exists()


def test_declarative_default_produces_request_without_script_or_copy(project):
    config,root,source=project;waiting=w.run(config,root)
    assert waiting['status']=='awaiting-review'
    packet=json.loads(Path(waiting['request_path']).read_bytes())
    from review_guidance import PROMPT_VERSION,iteration_guidance
    assert packet['planning_prompt_version']==PROMPT_VERSION
    assert packet['planning_guidance']==iteration_guidance()
    assert packet['evidence']['r1.evaluation.score']==0.75
    assert packet['evidence']['r1.evaluation.warning_present'] is True
    assert packet['material_metadata'][str(source/'tensor.npy')]['finite'] is True
    assert packet['material_metadata'][str(source/'output.zip')]['crc']=='pass'
    assert not list(root.rglob('*.npy')) and not list(root.rglob('*.log'))
    assert not list(root.rglob('*.py'))
    folder=Path(waiting['request_path']).parent;respond(folder)
    result=w.run(config,root);assert result['status']=='completed'
    assert result['review_origin']=='response-for-current-produced-materials'
    assert w.run(config,root)['resumed'] is True
    assert not result['execution_allowed'] and result['nodes_added']==0


def test_changed_original_requires_new_review_instead_of_cached_acceptance(project):
    config,root,source=project;waiting=w.run(config,root);respond(Path(waiting['request_path']).parent)
    w.run(config,root);(source/'run.log').write_text('score=0.65 n=12\nwarning\n')
    next_run=w.run(config,root)
    assert next_run['status']=='awaiting-review' and next_run['request_path']!=waiting['request_path']
    assert len(list((root/'runs').iterdir()))==1


def test_missing_or_ambiguous_metric_blocks_without_success(project):
    config,root,source=project;(source/'run.log').write_text('score=0.5 n=3\nscore=0.6 n=3\n')
    result=w.execute(config,root)
    assert result['status']=='blocked' and result['reason']=='one_unambiguous_text_observation_required'
    assert not (root/'latest.json').exists()


def test_original_sources_cannot_be_replaced_with_unregistered_reference(project,tmp_path):
    config,root,_=project;waiting=w.run(config,root);folder=Path(waiting['request_path']).parent;respond(folder)
    manifest=folder/'manifest.json';v=json.loads(manifest.read_bytes());v['sources'][0]['path']=str(tmp_path/'outside.log');manifest.write_text(json.dumps(v))
    assert w.execute(config,root)['status']=='blocked'


def test_material_include_globs_are_portable_and_explicit(project):
    config,root,source=project;config['material_include_globs']=['*.log','*.npy']
    result=w.run(config,root);packet=json.loads(Path(result['request_path']).read_bytes())
    assert str(source/'output.zip') not in packet['material_metadata']
    config['material_include_globs']=['..\\secret']
    assert w.execute(config,root)['status']=='blocked'


@pytest.mark.parametrize('kind',['byte','json'])
def test_generic_pair_comparison_by_relative_key(project,tmp_path,kind):
    config,root,source=project;other=tmp_path/'other';other.mkdir()
    (other/'run.log').write_text((source/'run.log').read_text())
    (source/'identity.json').write_text('{"scene":{"id":"x"}}')
    (other/'identity.json').write_text('{"scene":{"id":"x"}}')
    config['material_roots'].append(str(other))
    role=json.loads(json.dumps(config['material_preparation']['roles'][0]));role.update(id='other',root=str(other))
    config['material_preparation']['roles'].append(role)
    rule={'id':'same_identity','left':'observed','right':'other','globs':['identity.json']}
    if kind=='json':rule['json_field']='scene.id'
    config['material_preparation']['comparisons']=[rule]
    waiting=w.run(config,root);packet=json.loads(Path(waiting['request_path']).read_bytes())
    assert packet['evidence']['comparison.same_identity'] is True
    assert packet['evidence']['comparison.same_identity_pairs']==1


def test_pair_key_mismatch_blocks(project,tmp_path):
    config,root,source=project
    config['material_preparation']['comparisons']=[{'id':'empty','left':'observed','right':'observed','globs':['missing.json']}]
    assert w.execute(config,root)['reason']=='paired_material_keys_mismatch'


def test_nonfinite_array_rejected_without_pickle(project):
    config,root,source=project;npy(source/'tensor.npy',(float('nan'),))
    assert w.execute(config,root)['reason']=='nonfinite_array_material'


def test_truncated_npy_and_unsafe_zip(tmp_path):
    path=tmp_path/'x.npy';npy(path);path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError,match='npy_payload_size_mismatch'):inspect_npy(path,True)
    z=tmp_path/'x.zip'
    with zipfile.ZipFile(z,'w') as archive:archive.writestr('../outside','x')
    with pytest.raises(ValueError,match='unsafe_zip_member'):inspect_zip(z,True)


def test_prepared_request_tamper_is_not_silently_overwritten(project):
    config,root,_=project;waiting=w.run(config,root);Path(waiting['request_path']).write_text('{}')
    assert w.execute(config,root)['reason']=='prepared_material_record_changed'


def test_new_plan_cannot_consume_old_response(project):
    config,root,_=project;waiting=w.run(config,root);respond(Path(waiting['request_path']).parent);w.run(config,root)
    config['material_preparation']['objective']='New question, same raw files'
    assert w.run(config,root)['status']=='awaiting-review'


@pytest.mark.parametrize('name,data',[('tensor.npy',b'not-numpy'),('tensor.npy',b'\x93NUMPY\x01\x00'),('tensor.npy',b'\x93NUMPY\x01\x00\x02\x000\n'),('output.zip',b'not-zip')])
def test_malformed_artifacts_block_with_structured_failure(project,name,data):
    config,root,source=project;(source/name).write_bytes(data)
    result=w.execute(config,root)
    assert result['status']=='blocked' and Path(result['failure_record']).is_file()


def test_unfamiliar_metric_and_units_need_no_project_code(project):
    config,root,source=project;(source/'run.log').write_text('latency_ms=12.5\n')
    config['material_preparation']['roles'][0]['text_rules']=[{'pattern':r'latency_ms=(?P<latency_ms>[\d.]+)','groups':{'latency_ms':'float'}}]
    waiting=w.run(config,root);packet=json.loads(Path(waiting['request_path']).read_bytes())
    assert packet['evidence']['r1.evaluation.latency_ms']==12.5
    assert 'r1.evaluation.score' not in packet['evidence']


def test_explicit_context_does_not_allow_unknown_group_or_unsafe_pattern(project):
    config,root,_=project
    config['material_preparation']['roles'][0]['text_rules'][0]['groups']={'invented':'float'}
    assert w.execute(config,root)['reason']=='named_material_groups_required'
    config['material_preparation']['roles'][0]['text_rules'][0]['pattern']='(x+)+'
    assert w.execute(config,root)['reason']=='unsupported_material_pattern'

@pytest.fixture
def json_project(tmp_path):
    source=tmp_path/'json-raw';source.mkdir()
    (source/'metrics.json').write_text(json.dumps({'test':{'accuracy':0.8,'count':9},'model':'baseline'}))
    config={'schema':'sdm-project-workspace/v1','project_id':'json-only','material_roots':[str(source)],
        'material_preparation':{'schema':'sdm-material-preparation/v1','objective':'Retained JSON observations',
            'project_metrics':['evaluation.score'],'roles':[{'id':'candidate','root':str(source),'json_globs':['metrics.json'],
            'json_fields':{'score':{'field':'test.accuracy','type':'float'},'count':{'field':'test.count','type':'int'},'model':{'field':'model','type':'str'}}}]}}
    return config,tmp_path/'json-workspace',source


def test_json_only_project_review_archive_and_resume(json_project):
    config,root,source=json_project
    waiting=w.run(config,root);folder=Path(waiting['request_path']).parent
    packet=json.loads((folder/'analysis-request.json').read_bytes())
    assert packet['evidence']['r1.evaluation.score']==0.8
    assert packet['provenance']['r1.evaluation.score']['json_field']=='test.accuracy'
    assert packet['provenance']['r1.evaluation.score']['digest']==w.file_digest(source/'metrics.json')
    respond(folder)
    assert w.run(config,root)['status']=='completed'
    assert w.run(config,root)['resumed'] is True
    assert not list(root.rglob('*.py')) and not list(root.rglob('metrics.json'))
    raw=source/'metrics.json';old=raw.stat();raw.write_text(raw.read_text().replace('0.8','0.7'))
    __import__('os').utime(raw,ns=(old.st_atime_ns,old.st_mtime_ns))
    assert w.run(config,root)['status']=='awaiting-review'


@pytest.mark.parametrize('raw,reason',[
    ('{"test":{"accuracy":true,"count":9},"model":"x"}','json_material_field_type_mismatch'),
    ('{"test":{"accuracy":1,"count":9},"model":"x"}','json_material_field_type_mismatch'),
    ('{"test":{"count":9},"model":"x"}','json_material_field_missing'),
    ('{"test":{"accuracy":NaN,"count":9},"model":"x"}','nonfinite_material_observation'),
    ('{"test":{"accuracy":0.8,"accuracy":0.7,"count":9},"model":"x"}','duplicate_json_material_key'),
    ('{"test":','JSONDecodeError'),
])
def test_json_invalid_values_block_without_archive(json_project,raw,reason):
    config,root,source=json_project;(source/'metrics.json').write_text(raw)
    result=w.execute(config,root)
    assert result['status']=='blocked' and reason in result['reason']
    assert not (root/'latest.json').exists()


def test_json_ambiguous_selector_and_mixed_formats_rejected(json_project):
    config,root,source=json_project;role=config['material_preparation']['roles'][0]
    role['log_globs']=['*.log']
    assert w.execute(config,root)['reason']=='one_material_observation_format_per_role'
    role.pop('log_globs');role['json_globs']=['*.json'];(source/'other.json').write_text('{}')
    assert w.execute(config,root)['status']=='blocked'


def test_json_projection_reads_same_document_once_for_fields_and_comparisons(json_project,monkeypatch):
    from raw_materials import json_document
    config,root,source=json_project;p=source/'metrics.json';raw=p.read_bytes()
    row={'path':str(p),'bytes':len(raw),'digest':w.file_digest(p)};cache={};reads=[]
    original=Path.read_bytes
    def read(path):
        if path==p:reads.append(path)
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',read)
    assert json_document(row,cache)['test']['accuracy']==0.8
    assert json_document(row,cache)['test']['count']==9
    assert len(reads)==1
    p.write_text(raw.decode().replace('0.8','0.7'))
    with pytest.raises(ValueError,match='material_changed_during_json_projection'):json_document(row,{})


def test_archive_keeps_review_reasoning_and_detects_review_tampering(json_project):
    config,root,_=json_project;wait=w.run(config,root);folder=Path(wait['request_path']).parent;respond(folder)
    result=w.run(config,root);case=root/'runs'/json.loads((root/'latest.json').read_bytes())['case_id']
    original=json.loads((folder/'analysis-response.json').read_bytes())
    assert json.loads((case/'review.json').read_bytes())==original
    report=(case/'REPORT.md').read_text()
    assert original['hypotheses'][0]['statement'] in report
    assert original['hypotheses'][0]['limitations'] in report
    assert original['facts'][0]['evidence_id'] in report
    (case/'review.json').write_text('{}')
    assert w.execute(config,root)['reason']=='project_archive_changed:review.json'
