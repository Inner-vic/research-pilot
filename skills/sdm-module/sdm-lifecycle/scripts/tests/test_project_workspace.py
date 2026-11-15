"""Default project workflow: actual files/Git, synthetic evidence, no adapters/GPU."""
import json,subprocess,sys
from pathlib import Path
import pytest
import project_workspace as w
from task_pipeline import digest
from project_driver import file_digest


def test_explicit_discovery_byte_limits_preserve_both_bounds(tmp_path):
    p=tmp_path/'checkpoint.pth';p.write_bytes(b'X'*2048)
    assert len(w.discover([tmp_path],byte_limits={'max_file_bytes':2048,'max_total_bytes':2048}))==1
    with pytest.raises(ValueError,match='material_discovery_budget'):
        w.discover([tmp_path],byte_limits={'max_file_bytes':2047,'max_total_bytes':2048})
    with pytest.raises(ValueError,match='material_discovery_budget'):
        w.discover([tmp_path],byte_limits={'max_file_bytes':2048,'max_total_bytes':2047})
    with pytest.raises(ValueError,match='bounded_material_discovery_limits'):
        w.discover([tmp_path],byte_limits={'max_file_bytes':1024*1024*1024+1})


@pytest.fixture
def case(tmp_path):
    materials=tmp_path/'materials';materials.mkdir()
    log=materials/'evidence-000.txt';log.write_text('retained log\n')
    packet={'history':[{'round':1,'parameters':{'lr':0.1},'training':{'status':'completed','actual':{'log_digest':file_digest(log)}},
        'evaluation':{'status':'completed','actual':{'score':0.7}}}],
        'remaining_rounds':0,'project_metrics':['evaluation.score','training.not_measured'],
        'evidence':{'r1.evaluation.score':0.7}}
    request=materials/'analysis-request.json';request.write_text(json.dumps(packet))
    response={'request_digest':digest(packet),'action':'stop-budget','facts':[{'evidence_id':'r1.evaluation.score','value':0.7}],
        'hypotheses':[{'statement':'Fixture only','evidence_ids':['r1.evaluation.score'],'limitations':'Not model evidence'}],
        'previous_experiment_assessment':'No new model','reason':'Retained review ends'}
    (materials/'analysis-response.json').write_text(json.dumps(response))
    manifest={'schema':'sdm-model-review-manifest/v1','request_digest':digest(packet),
        'files':{p.name:file_digest(p) for p in (log,request)}}
    (materials/'manifest.json').write_text(json.dumps(manifest))
    return {'schema':'sdm-project-workspace/v1','project_id':'fixture','material_roots':[str(materials)]},tmp_path/'workspace',materials


def test_default_discovery_execution_archive_and_resume_without_adapter(case):
    config,root,_=case;first=w.run(config,root);second=w.run(config,root)
    assert first['status']=='completed' and second['resumed']
    assert not first['execution_allowed'] and not first['training_dispatched'] and first['nodes_added']==0
    target=next((root/'runs').iterdir());diagnosis=json.loads((target/'diagnosis.json').read_bytes())
    assert diagnosis['missing_metric_fields']==[{'round':1,'metric':'training.not_measured'}]
    assert (root/'project.json').is_file() and (root/'latest.json').is_file()


def test_ambiguous_material_does_not_pick_latest_or_request_adapter(case,tmp_path):
    config,root,source=case
    import shutil
    other=tmp_path/'other';shutil.copytree(source,other);config['material_roots'].append(str(other))
    result=w.run(config,root)
    assert result['status']=='awaiting-project-selection' and len(result['candidates'])==2
    config['case_directory']=str(source)
    assert w.run(config,root)['status']=='completed'


def test_material_tamper_blocks_even_when_review_facts_are_unchanged(case):
    config,root,source=case;(source/'evidence-000.txt').write_text('changed')
    with pytest.raises(ValueError,match='evidence_integrity_failed'):w.run(config,root)
    assert not (root/'latest.json').exists()


def test_invented_review_fact_is_rejected(case):
    config,root,source=case;p=source/'analysis-response.json';v=json.loads(p.read_bytes());v['facts'][0]['value']=0.9;p.write_text(json.dumps(v))
    with pytest.raises(ValueError,match='invented_analysis_evidence'):w.run(config,root)


def test_configured_pro_rejects_local_result_and_waits_for_verified_handoff(case,tmp_path):
    config,root,source=case
    config['external_review']={'project':'fixture','workflow':'review','session_root':str(tmp_path/'pro')}
    config['review_mode']='local-agent'  # Cannot override a configured Pro binding.
    with pytest.raises(ValueError,match='configured_pro_cannot_use_local_review'):w.run(config,root)
    (source/'analysis-response.json').unlink()
    wait=w.run(config,root)
    assert wait['status']=='awaiting-review' and wait['review_mode']=='pro'
    assert Path(wait['external_review_result']).name=='external-review-result.json'
    assert not wait['execution_allowed'] and not (root/'latest.json').exists()


def test_v2_result_review_does_not_require_stored_unselected_pro(case):
    config,root,_=case
    config.update(proposal_review={'version':2,'backend':'model'},external_review={'old':'unavailable'})
    result=w.run(config,root)
    assert result['status']=='completed' and not result['training_dispatched']


@pytest.mark.parametrize('name',['REPORT.md','result.json'])
def test_modified_archive_not_reused(case,name):
    config,root,_=case;w.run(config,root);target=next((root/'runs').iterdir());(target/name).write_text('{}')
    with pytest.raises(ValueError,match='project_(archive|result)_changed'):w.run(config,root)


def test_git_registration_tracks_new_commit_without_copy_or_new_workspace(case,tmp_path):
    config,root,_=case;repo=tmp_path/'repo';repo.mkdir()
    def git(*args):
        r=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True);assert r.returncode==0,r.stderr
    git('init');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    (repo/'model.txt').write_text('one');git('add','.');git('commit','-m','first')
    config['repository']=str(repo);first=w.run(config,root)
    (repo/'model.txt').write_text('two');git('add','.');git('commit','-m','second')
    second=w.run(config,root)
    assert first['repository_snapshot']['commit']!=second['repository_snapshot']['commit']
    assert len(list((root/'runs').iterdir()))==2 and not (root/'.git').exists()


def test_public_entry_uses_project_configuration_without_driver(case,tmp_path):
    config,root,_=case;p=tmp_path/'config.json';p.write_text(json.dumps(config))
    r=subprocess.run([sys.executable,'-B',str(Path(w.__file__).with_name('run_project_pipeline.py')),
        '--project',str(p),'--output',str(root),'--project-workspace'],capture_output=True,text=True,timeout=20)
    assert r.returncode==0,r.stderr
    assert json.loads(r.stdout)['status']=='completed'


def test_workspace_inside_material_roots_is_rejected(case):
    config,_,source=case
    with pytest.raises(ValueError,match='workspace_must_not_be_discovered'):w.run(config,source/'workspace')


def test_missing_review_is_prepared_for_agent_instead_of_requesting_adapter(case):
    config,root,source=case;p=source/'analysis-response.json';raw=p.read_bytes();p.unlink()
    waiting=w.run(config,root)
    assert waiting['status']=='awaiting-review' and waiting['review_mode']=='local-agent'
    assert (root/'review-inbox.json').is_file() and not (root/'runs').exists()
    p.write_bytes(raw)
    assert w.run(config,root)['status']=='completed'


def test_blocked_workflow_preserves_failure_without_success_delivery(case):
    config,root,source=case;(source/'evidence-000.txt').write_text('changed')
    result=w.execute(config,root)
    assert result['status']=='blocked' and Path(result['failure_record']).is_file()
    assert not (root/'latest.json').exists() and not result['execution_allowed']


def test_project_material_roots_can_evolve_without_overwriting_previous_runs(case,tmp_path):
    config,root,_=case;first=w.run(config,root)
    more=tmp_path/'new-material';more.mkdir();config['material_roots'].append(str(more))
    second=w.run(config,root)
    assert second['status']=='completed' and len(list((root/'runs').iterdir()))==2
    assert len(list((root/'registrations').iterdir()))==1
    config['project_id']='other'
    with pytest.raises(ValueError,match='project_identity_changed'):w.run(config,root)


def test_raw_logs_are_discovered_without_fake_review_or_adapter_request(tmp_path):
    source=tmp_path/'logs';source.mkdir();(source/'train.log').write_text('actual raw material')
    result=w.run({'schema':'sdm-project-workspace/v1','project_id':'raw','material_roots':[str(source)]},tmp_path/'workspace')
    assert result['status']=='awaiting-evidence' and result['discovered_files']==1
    assert 'no new adapter' in result['next_action']
