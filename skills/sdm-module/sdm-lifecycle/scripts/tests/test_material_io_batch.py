"""Integrity-preserving I/O consolidation, CPU fixtures only."""
import json,os,subprocess
from pathlib import Path
import pytest
import project_workspace as w
import raw_materials as raw
from test_raw_materials import project,respond


def test_final_boundary_still_hashes_each_original_after_production(project,monkeypatch):
    config,root,source=project;calls=[];original=w.file_digest
    def count(path):
        if Path(path).parent==source:calls.append(Path(path).name)
        return original(path)
    monkeypatch.setattr(w,'file_digest',count)
    assert w.run(config,root)['status']=='awaiting-review'
    # One discovery content read and one final acceptance content read, no
    # stat-only cache. Format inspections separately read the actual payload.
    assert {p:calls.count(p) for p in ('run.log','tensor.npy','output.zip')}=={'run.log':2,'tensor.npy':2,'output.zip':2}


def test_source_changed_between_production_and_final_boundary_blocks(project,monkeypatch):
    config,root,source=project;original=raw._coordinator_prepare
    def mutate(*args):
        result=original(*args);(source/'run.log').write_text('score=0.25 n=12\nwarning\n');return result
    monkeypatch.setattr(raw,'_coordinator_prepare',mutate)
    result=w.execute(config,root)
    assert result['status']=='blocked' and 'original_material_not_registered_or_changed' in result['reason']
    assert not (root/'latest.json').exists() and not (root/'review-inbox.json').exists()


def test_standalone_producer_keeps_its_own_final_check(project,monkeypatch):
    config,root,source=project;root.mkdir();materials=w.discover([source]);original=raw.text_observations
    def mutate(*args):
        result=original(*args);p=source/'tensor.npy';p.write_bytes(p.read_bytes()[:-4]+b'ABCD');return result
    monkeypatch.setattr(raw,'text_observations',mutate)
    with pytest.raises(ValueError,match='material_changed_during_preparation'):raw.prepare(config,materials,root)


def test_restored_timestamp_does_not_reuse_old_review(project):
    config,root,source=project;wait=w.run(config,root);respond(Path(wait['request_path']).parent);w.run(config,root)
    log=source/'run.log';previous=log.stat();log.write_text(log.read_text().replace('0.75','0.65'))
    os.utime(log,ns=(previous.st_atime_ns,previous.st_mtime_ns))
    next_run=w.run(config,root)
    assert next_run['status']=='awaiting-review' and next_run['request_path']!=wait['request_path']


def test_one_log_read_supplies_all_fact_provenance(project,monkeypatch):
    config,root,source=project;original=Path.read_bytes;reads=[]
    def count(path):
        if path==source/'run.log':reads.append(path)
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',count)
    wait=w.run(config,root);packet=json.loads(Path(wait['request_path']).read_bytes())
    assert len(reads)==1 and len(packet['provenance'])==3
    assert all(row['digest']==packet['evidence']['r1.evaluation.log_digest'] for row in packet['provenance'].values())


def test_json_projections_share_document_only_within_this_invocation(project,monkeypatch):
    config,root,source=project;p=source/'id.json';p.write_text('{"scene":"x","frame":7}')
    config['material_preparation']['comparisons']=[{'id':field,'left':'observed','right':'observed','globs':['id.json'],'json_field':field} for field in ['scene','frame']]
    original=Path.read_bytes;reads=[]
    def count(path):
        if path==p:reads.append(path)
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',count)
    assert w.run(config,root)['status']=='awaiting-review' and len(reads)==1
    assert w.run(config,root)['status']=='awaiting-review' and len(reads)==2


def test_combined_git_identity_preserves_snapshot_and_dirty_status(project,tmp_path):
    config,root,_=project;repo=tmp_path/'repo with space';repo.mkdir()
    def git(*args):
        proc=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True);assert proc.returncode==0,proc.stderr
    git('init');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    (repo/'tracked.txt').write_text('one');git('add','.');git('commit','-m','initial');(repo/'tracked.txt').write_text('two')
    config['repository']=str(repo);root.mkdir();profile={};identity,_,snapshot=w.registration(config,root,profile)
    from task_pipeline import digest
    assert identity['repository_root']==w.git(repo,'rev-parse','--show-toplevel')
    assert snapshot=={'commit':w.git(repo,'rev-parse','HEAD'),'dirty':bool(w.git(repo,'status','--porcelain','--untracked-files=no')),
                      'tracked_diff_digest':digest(w.git(repo,'diff','--binary','HEAD'))}
    assert set(profile)=={'identity_seconds','status_seconds','diff_seconds'}


def test_excluded_symlink_is_still_rejected(project,tmp_path):
    config,root,source=project;other=tmp_path/'opaque';other.write_text('not input')
    (source/'excluded.unknown').symlink_to(other)
    with pytest.raises(ValueError,match='linked_project_material_rejected'):w.run(config,root)
