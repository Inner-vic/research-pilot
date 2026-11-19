"""Launch ownership and registration controls. No real subprocess, ML or platform."""
import copy,io,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
import research_launch as entry
from review_storage import atomic_json,read_json
from task_pipeline import digest
from test_research_event_service import setup


def fixture(tmp):
    cfg,_=setup(tmp)
    cfg.update(schema='sdm-event-service/v3',continuation_policy={'version':1},tasks={},task_templates={
      'coord':{'kind':'coordinator','instructions':'synthetic','evidence_kind':'control'},
      'impl':{'kind':'implementation','instructions':'synthetic','evidence_kind':'control'},
      'source':{'kind':'component-source','instructions':'synthetic','evidence_kind':'control'}},
      component_pipeline={'version':4,'coordinator_template':'coord','implementation_template':'impl','source_template':'source'})
    p=tmp/'config.json';atomic_json(p,cfg)
    return cfg,p


def live(cfg,owner=None):
    ready={'pid':123,'port':1234,'token':'synthetic-local-token','project_id':cfg['project_id'],'config_sha256':digest(cfg)}
    atomic_json(Path(cfg['root'])/'ready.json',ready)
    health={k:v for k,v in ready.items() if k not in ('port','token')}
    health['component_owner']=owner or {'status':'running','thread_alive':True,'config_sha256':digest(cfg)}
    return health


def test_preparation_is_not_started_and_legacy_analysis_is_not_owner(tmp_path):
    cfg,p=fixture(tmp_path)
    from research_initialize import startup
    value=startup(p)
    assert value['automatic_iteration_active'] is False and value['launch_script'].endswith('research_launch.py')
    with patch.object(entry,'owner_alive',return_value=False):
        assert entry.inspect(p,tmp_path)['automatic_iteration_active'] is False
    cfg['component_pipeline']['version']=3;atomic_json(p,cfg)
    with pytest.raises(ValueError,match='not_managed'):entry.inspect(p,tmp_path)


@pytest.mark.parametrize('owner',[{'status':'starting','thread_alive':False}, {'status':'failed','thread_alive':True}])
def test_http_alive_without_research_worker_is_not_automatic(tmp_path,owner):
    cfg,p=fixture(tmp_path);health=live(cfg,owner)
    with patch.object(entry,'owner_alive',return_value=True),patch.object(entry,'urlopen',side_effect=lambda *a,**k:io.BytesIO(json.dumps(health).encode())):
        value=entry.inspect(p,tmp_path)
        assert value['automatic_iteration_active'] is False and value['owner_alive'] is True
        with patch.object(entry.subprocess,'Popen') as process:
            with pytest.raises(ValueError,match='existing_research_owner'):entry.launch(p,tmp_path)
            process.assert_not_called()


def test_live_identity_matches_and_no_second_supervisor(tmp_path):
    cfg,p=fixture(tmp_path);health=live(cfg)
    with patch.object(entry,'owner_alive',return_value=True),patch.object(entry,'urlopen',side_effect=lambda *a,**k:io.BytesIO(json.dumps(health).encode())),patch.object(entry.subprocess,'Popen') as process:
        value=entry.launch(p,tmp_path)
        assert value['automatic_iteration_active'] is True and value['launch']=='already-running'
        process.assert_not_called()
        health['project_id']='another'
        with pytest.raises(ValueError,match='identity_changed'):entry.inspect(p,tmp_path)


@pytest.mark.parametrize('record',['PAUSED','managed-launch.json','supervision.json','component-pipeline/pipeline.json'])
def test_paused_unknown_or_existing_launch_is_never_retried(tmp_path,record):
    cfg,p=fixture(tmp_path)
    value={'config_digest':digest(cfg),'status':'running','attempts':[]} if record.endswith('pipeline.json') else {}
    atomic_json(Path(cfg['root'])/record,value)
    with patch.object(entry,'owner_alive',return_value=False),patch.object(entry.subprocess,'Popen') as process:
        with pytest.raises(ValueError):entry.launch(p,tmp_path)
        process.assert_not_called()


def test_registration_preserves_existing_primary_and_origin(tmp_path):
    cfg,p=fixture(tmp_path);primary=tmp_path/'.bmad-ml/CURRENT.json'
    old={'schema':'bmad-ml-active-study/v1','study_id':'old','config':'old-config','origin_codex_chat_id':'old-origin'}
    atomic_json(primary,old);before=primary.read_bytes()
    path=Path(entry.register(p,tmp_path));assert path!=primary and primary.read_bytes()==before
    value=read_json(path);value['origin_codex_chat_id']='first-chat';atomic_json(path,value)
    assert entry.register(p,tmp_path)==str(path) and read_json(path)['origin_codex_chat_id']=='first-chat'
    other=tmp_path/'other.json';atomic_json(other,cfg)
    with pytest.raises(ValueError,match='another_configuration'):entry.register(other,tmp_path)


def test_wrong_project_or_missing_continuation_is_rejected(tmp_path):
    cfg,p=fixture(tmp_path);outside=tmp_path/'other';outside.mkdir()
    with pytest.raises(ValueError,match='current_project'):entry.inspect(p,outside)
    cfg.pop('continuation_policy');atomic_json(p,cfg)
    with pytest.raises(ValueError,match='continuation_policy'):entry.inspect(p,tmp_path)


def test_single_spawn_is_journaled_and_only_ready_original_owner_returns(tmp_path):
    cfg,p=fixture(tmp_path);calls=[]
    def spawn(argv,**kwargs):
        calls.append(argv)
        assert argv[-2:]==['--config',str(p)] and argv[6].endswith('research_supervisor.py')
        assert read_json(Path(cfg['root'])/'managed-launch.json')['phase']=='launch-intent'
        return SimpleNamespace(pid=789,poll=lambda:None)
    inactive={'automatic_iteration_active':False,'owner_alive':False,'status':'no-live-research-owner'}
    active={'automatic_iteration_active':True,'owner_alive':True,'status':'managed-owner-running'}
    with patch.object(entry,'inspect',side_effect=[inactive,active]),patch.object(entry.subprocess,'Popen',side_effect=spawn):
        value=entry.launch(p,tmp_path)
    assert len(calls)==1 and value['foreground_may_return'] is True
    assert read_json(Path(cfg['root'])/'managed-launch.json')['phase']=='spawned'


def test_spawn_unknown_is_not_replayed(tmp_path):
    cfg,p=fixture(tmp_path)
    with patch.object(entry,'owner_alive',return_value=False),patch.object(entry.subprocess,'Popen',side_effect=OSError('synthetic unknown')) as process:
        with pytest.raises(OSError):entry.launch(p,tmp_path)
        with pytest.raises(ValueError,match='original_restore'):entry.launch(p,tmp_path)
        assert process.call_count==1
    assert read_json(Path(cfg['root'])/'managed-launch.json')['phase']=='launch-outcome-unresolved'


def test_actual_service_raw_hash_matches_same_configuration(tmp_path):
    cfg,p=fixture(tmp_path);health=live(cfg)
    path=Path(cfg['root'])/'ready.json';ready=read_json(path)
    ready['config_sha256']=ready['config_sha256'].removeprefix('sha256:')
    health['config_sha256']=ready['config_sha256'];atomic_json(path,ready)
    with patch.object(entry,'owner_alive',return_value=True),patch.object(entry,'urlopen',side_effect=lambda *a,**k:io.BytesIO(json.dumps(health).encode())):
        assert entry.inspect(p,tmp_path)['automatic_iteration_active'] is True
        ready['config_sha256']='0'*64;atomic_json(path,ready)
        with pytest.raises(ValueError,match='binding_changed'):entry.inspect(p,tmp_path)


def test_blocked_original_controller_is_reported_without_starting_another(tmp_path):
    cfg,p=fixture(tmp_path);health=live(cfg)
    atomic_json(Path(cfg['root'])/'component-pipeline/pipeline.json',
      {'config_digest':digest(cfg),'status':'running','attempts':[{'stage':'needs-attention',
       'reason':'original-initial-review:awaiting-proposal-evidence','continuation':{'phase':'blocked'}}]})
    with patch.object(entry,'owner_alive',return_value=True),patch.object(entry,'urlopen',
         side_effect=lambda *a,**k:io.BytesIO(json.dumps(health).encode())),patch.object(entry.subprocess,'Popen') as spawn:
        status=entry.inspect(p,tmp_path);launched=entry.launch(p,tmp_path)
    assert status['controller_running'] and not status['automatic_iteration_active']
    assert status['status']=='research-blocked' and status['progress']['blocking_code']
    assert launched['launch']=='already-running-blocked';spawn.assert_not_called()


def test_fresh_launch_can_report_early_block_without_false_active_or_second_spawn(tmp_path):
    cfg,p=fixture(tmp_path)
    inactive={'automatic_iteration_active':False,'controller_running':False,'owner_alive':False,'status':'no-live-research-owner'}
    blocked={'automatic_iteration_active':False,'controller_running':True,'owner_alive':True,'status':'research-blocked'}
    with patch.object(entry,'inspect',side_effect=[inactive,blocked]),patch.object(entry.subprocess,'Popen',
         return_value=SimpleNamespace(pid=789,poll=lambda:None)) as spawn:
        status=entry.launch(p,tmp_path)
    assert status['launch']=='started-blocked' and not status['automatic_iteration_active']
    assert status['foreground_may_return'];assert spawn.call_count==1

def test_single_evaluation_scope_cannot_launch_managed_research(tmp_path):
    from research_task_scope import begin
    cfg,path=fixture(tmp_path)
    begin(tmp_path,cfg['project_id'],'single-evaluation','Synthetic evaluation scope','Offline control')
    with patch.object(entry.subprocess,'Popen',side_effect=AssertionError('no process allowed')):
        with pytest.raises(ValueError,match='single_evaluation_cannot_launch_research'):entry.inspect(path,tmp_path)

def test_registration_links_declared_continuous_scope_without_rewriting_config(tmp_path):
    from research_task_scope import begin,status
    cfg,path=fixture(tmp_path);original=path.read_bytes()
    begin(tmp_path,cfg['project_id'],'continuous-research','Synthetic continuous scope','Offline control')
    entry.register(path,tmp_path);value=status(tmp_path,cfg['project_id'])
    assert value['study_id']==cfg['project_id'] and value['research_started'] is None
    assert path.read_bytes()==original and value['execution_authority'] is False
