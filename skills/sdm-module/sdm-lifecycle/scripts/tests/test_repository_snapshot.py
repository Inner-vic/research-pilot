"""Snapshot equivalence, invalidation and private-index safety on CPU."""
import json,os,subprocess
from pathlib import Path
import pytest
import repository_snapshot as q


@pytest.fixture
def gate(tmp_path,monkeypatch):
    state={'identity':{'root':'/mnt/z/repo','head':'a'*40,'index_digest':'index-1'},
           'native':{'commit':'a'*40,'dirty':True,'tracked_diff_digest':'diff-1','status_digest':'status-1','snapshot_mode':'fresh-index-v2'},
           'baseline':None,'calls':[]}
    monkeypatch.setattr(q.shutil,'which',lambda _: '/native/git.exe')
    monkeypatch.setattr(q,'inputs',lambda _: (dict(state['identity']),b'listing',{}))
    monkeypatch.setattr(q,'command',lambda *a,**k: b'version')
    monkeypatch.setattr(q,'file_digest',lambda _: 'fixed')
    def fresh(*args):
        native=args[-2];state['calls'].append(native)
        return dict(state['native'] if native or state['baseline'] is None else state['baseline'])
    monkeypatch.setattr(q,'fresh',fresh)
    return state,Path('/mnt/z/repo'),tmp_path


def test_equivalence_required_before_reusing_native_content_result(gate):
    state,repo,root=gate;profile={};first=q.snapshot(repo,root,profile)
    assert profile['optimization']=='equivalence-established' and state['calls']==[True,False]
    profile={};assert q.snapshot(repo,root,profile)==first
    assert profile['optimization']=='verified-native-content' and state['calls']==[True,False,True]


def test_changed_contents_need_new_equivalence_even_without_index_change(gate):
    state,repo,root=gate;q.snapshot(repo,root,{})
    state['native']['tracked_diff_digest']='different-current-content'
    profile={};q.snapshot(repo,root,profile)
    assert profile['optimization']=='equivalence-established' and state['calls']==[True,False,True,False]


def test_mismatch_uses_baseline_and_does_not_certify_candidate(gate):
    state,repo,root=gate;state['baseline']={**state['native'],'dirty':False}
    profile={};_,result=q.snapshot(repo,root,profile)
    assert result['dirty'] is False and profile['optimization']=='baseline-on-mismatch'
    assert not list((root/'repository-equivalence').glob('*.json'))


def test_config_or_index_fingerprint_drift_requires_verification(gate):
    state,repo,root=gate;q.snapshot(repo,root,{})
    state['identity']['index_digest']='index-2';q.snapshot(repo,root,{})
    assert state['calls']==[True,False,True,False]


def test_corrupted_proof_is_reverified_not_trusted_or_failed_open(gate):
    state,repo,root=gate;q.snapshot(repo,root,{})
    next((root/'repository-equivalence').glob('*.json')).write_text('{broken')
    profile={};q.snapshot(repo,root,profile)
    assert profile['optimization']=='equivalence-established' and state['calls']==[True,False,True,False]


def test_proof_cannot_claim_equal_baseline_with_different_snapshot(gate):
    state,repo,root=gate;q.snapshot(repo,root,{})
    path=next((root/'repository-equivalence').glob('*.json'));record=json.loads(path.read_bytes());record['baseline']['dirty']=False;path.write_text(json.dumps(record))
    q.snapshot(repo,root,{})
    assert state['calls']==[True,False,True,False]


def test_repository_drift_during_query_blocks_instead_of_publishing(gate,monkeypatch):
    state,repo,root=gate;seen=[]
    def inputs(_):
        seen.append(1);return ({**state['identity'],'head':('a' if len(seen)==1 else 'b')*40},b'listing',{})
    monkeypatch.setattr(q,'inputs',inputs)
    with pytest.raises(ValueError,match='repository_changed_during_snapshot'):q.snapshot(repo,root,{})


def test_no_native_or_non_windows_mount_uses_legacy(gate,monkeypatch):
    state,repo,root=gate;profile={}
    assert q.snapshot(Path('/tmp/repo'),root,profile) is None
    monkeypatch.setattr(q.shutil,'which',lambda _:None)
    assert q.snapshot(repo,root,{}) is None and state['calls']==[]


@pytest.fixture
def repo(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    def git(*args):
        p=subprocess.run(['git','-C',str(repo),*args],capture_output=True);assert p.returncode==0,p.stderr
        return p.stdout
    git('init');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    git('config','core.filemode','false');git('config','core.autocrlf','true')
    (repo/'file.txt').write_bytes(b'line\r\n');git('add','.');git('commit','-m','fixture')
    return repo,git


def test_private_index_corrects_cross_config_stat_cache_without_touching_real_index(repo,tmp_path):
    repo,git=repo;git('config','core.autocrlf','false')
    identity,listing,values=q.inputs(repo)
    before=(repo/'.git/index').read_bytes();config=(repo/'.git/config').read_bytes()
    result=q.fresh('git',repo,listing,values,tmp_path,False,identity['head'])
    assert result['dirty'] and result['snapshot_mode']=='fresh-index-v2'
    assert (repo/'.git/index').read_bytes()==before and (repo/'.git/config').read_bytes()==config


def test_assume_unchanged_is_not_reconstructed_or_ignored(repo):
    repo,git=repo;git('update-index','--assume-unchanged','file.txt')
    with pytest.raises(q.Unsupported,match='index_flags'):q.inputs(repo)


def test_attributes_require_conservative_fallback(repo):
    repo,git=repo;(repo/'.gitattributes').write_text('*.txt text eol=lf\n')
    with pytest.raises(q.Unsupported,match='repository_attributes'):q.inputs(repo)


def test_native_environment_reproduces_baseline_eol_and_disables_lazy_fetch():
    values={'core.autocrlf':'false','core.filemode':'false','remote.origin.promisor':'true'}
    env=q.environment(values,Path('/mnt/d/private/index'),True)
    mapped={env['GIT_CONFIG_KEY_'+str(i)]:env['GIT_CONFIG_VALUE_'+str(i)] for i in range(int(env['GIT_CONFIG_COUNT']))}
    assert mapped['core.autocrlf']=='false' and mapped['remote.origin.promisor']=='false'
    assert env['GIT_CONFIG_NOSYSTEM']=='1' and env['GIT_INDEX_FILE']=='D:/private/index'
    assert 'GIT_INDEX_FILE' in env['WSLENV'].split(':') and 'GIT_CONFIG_COUNT' in env['WSLENV'].split(':')


def test_index_path_preflight_refuses_any_write_to_original_index(tmp_path,monkeypatch):
    calls=[]
    def command(*args):calls.append(args[2]);return b'.git/index\n'
    monkeypatch.setattr(q,'command',command)
    with pytest.raises(q.Unsupported,match='private_index_environment_not_honored'):
        q.fresh('git',Path('/mnt/d/repo'),b'listing',{},Path('/mnt/d/private'),True,'a'*40)
    assert calls==[['rev-parse','--git-path','index']]


def test_environment_override_is_unsupported(repo,monkeypatch):
    repo,_=repo;monkeypatch.setenv('GIT_INDEX_FILE','/different/index')
    with pytest.raises(q.Unsupported,match='custom_git_environment'):q.inputs(repo)


def test_sparse_skip_flags_and_absent_paths_are_preserved(repo,tmp_path):
    repo,git=repo;git('config','core.autocrlf','false')
    git('update-index','--skip-worktree','file.txt');(repo/'file.txt').unlink()
    identity,listing,values=q.inputs(repo);before=(repo/'.git/index').read_bytes()
    assert listing.skip_paths==(b'file.txt',)
    result=q.fresh('git',repo,listing,values,tmp_path,False,identity['head'])
    assert result['dirty'] is False and (repo/'.git/index').read_bytes()==before


def test_registered_subdirectory_keeps_legacy_path(repo):
    repo,_=repo;sub=repo/'sub';sub.mkdir()
    with pytest.raises(q.Unsupported,match='registered_subdirectory'):q.inputs(sub)
