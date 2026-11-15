"""Context continuity and failure semantics; fake transport, no model/platform."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from research_context import Context
from review_storage import atomic_json
from task_event_collector import validate, wait_remaining


def config(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    return {'project_id':'p','cwd':str(workspace),'model':'m','sandbox':'read-only',
            'codex_home':str(tmp_path/'home'),'research_context':{'version':1,'root':str(tmp_path/'context')}}


class Native:
    def __init__(self):self.starts=[];self.resumes=[]
    def thread_start(self,**args):self.starts.append(args);return SimpleNamespace(id='same-thread')
    def thread_resume(self,id,**args):self.resumes.append(id);return SimpleNamespace(id=id)


def test_new_instance_resumes_same_native_thread_without_rebuilding_history(tmp_path):
    cfg=config(tmp_path);native=Native();context=Context(cfg)
    with context.lock():
        first=context.open(native,{'model':'m'});context.intent('a');context.started('a','turn-1')
        receipt=tmp_path/'receipt.json';atomic_json(receipt,{'job_id':'a','status':'completed',
            'detail':{'thread_id':first.id,'turn_id':'turn-1','terminal':'completed'}})
        context.acknowledge('a',receipt)
        context.acknowledge('a',receipt)  # replay is idempotent
    with Context(cfg).lock():second=Context(cfg).open(native,{'model':'m'})
    assert first.id==second.id and len(native.starts)==1 and native.resumes==['same-thread']


def test_unknown_turn_blocks_another_model_delivery(tmp_path):
    cfg=config(tmp_path);native=Native();context=Context(cfg)
    context.open(native,{});context.intent('a')  # crash before turn ID receipt
    with pytest.raises(ValueError,match='unknown_delivery'):Context(cfg).open(native,{})
    assert len(native.starts)==1 and native.resumes==[]


def test_unknown_thread_creation_does_not_create_a_second_thread(tmp_path):
    cfg=config(tmp_path);context=Context(cfg)
    def uncertain(**kwargs):raise TimeoutError('simulated RPC lost')
    with pytest.raises(TimeoutError):context.open(SimpleNamespace(thread_start=uncertain),{})
    native=Native()
    with pytest.raises(ValueError,match='unknown_delivery'):Context(cfg).open(native,{})
    assert not native.starts


def test_stable_native_policy_keeps_one_template_and_rejects_instruction_changes_before_resume(tmp_path):
    cfg=config(tmp_path);cfg['research_context']['stable_instructions']=True
    native=Native();context=Context(cfg)
    arguments={'developer_instructions':'Implement each current bound job under the same fixed scope.'}
    context.open(native,arguments)
    original=context.load()['developer_instructions_sha256']
    assert Context(cfg).open(native,arguments).id=='same-thread'
    with pytest.raises(ValueError,match='instructions_changed_or_unbound'):
        Context(cfg).open(native,{'developer_instructions':'Different task-specific policy'})
    assert native.resumes==['same-thread'] and Context(cfg).load()['developer_instructions_sha256']==original


def test_opt_in_cannot_silently_adopt_unbound_existing_thread(tmp_path):
    cfg=config(tmp_path);native=Native();Context(cfg).open(native,{})
    cfg['research_context']['stable_instructions']=True
    with pytest.raises(ValueError,match='instructions_changed_or_unbound'):Context(cfg).open(native,{})
    assert native.resumes==[] and len(native.starts)==1


def test_wrong_or_changed_receipt_cannot_release_context(tmp_path):
    cfg=config(tmp_path);context=Context(cfg);context.open(Native(),{});context.intent('a');context.started('a','t1')
    receipt=tmp_path/'receipt.json';atomic_json(receipt,{'job_id':'a','status':'completed',
        'detail':{'thread_id':'other-thread','turn_id':'t1','terminal':'completed'}})
    with pytest.raises(ValueError,match='receipt_required'):context.acknowledge('a',receipt)
    assert context.load()['pending_job']=='a'


def test_other_project_or_model_cannot_adopt_context(tmp_path):
    cfg=config(tmp_path);Context(cfg).open(Native(),{})
    for field in ('project_id','model'):
        changed={**cfg,field:'other'}
        with pytest.raises(ValueError,match='scope_changed'):Context(changed).load()
    with pytest.raises(ValueError,match='outside_model'):
        Context({**cfg,'research_context':{'version':1,'root':str(Path(cfg['cwd'])/'state')}})


def test_phase_wait_schedule_survives_restart_and_separates_queue_from_running(tmp_path):
    cfg={'version':2,'store':str(tmp_path/'observer.sqlite'),'tasks':[],
         'phase_intervals_seconds':{'queued':120,'running':30,'finishing':15,'terminal':300,'unknown':60},
         'failure_backoff_max_seconds':900}
    assert validate({'task_event_collector':cfg})==cfg
    previous={'last_attempt':100,'errors':0,'snapshot':json.dumps({'platform_status':'queued'})}
    assert wait_remaining(cfg,previous,200)==20
    assert wait_remaining(cfg,json.loads(json.dumps(previous)),200)==20
    previous['snapshot']=json.dumps({'platform_status':'running'})
    assert wait_remaining(cfg,previous,120)==10
    previous['errors']=8
    assert wait_remaining(cfg,previous,100)==900
    assert wait_remaining(cfg,previous,100+48*3600)==0  # still observe; never cancel a queued task
    legacy={'version':1};previous['errors']=0
    assert wait_remaining(legacy,previous,120)==0


def test_v2_requires_explicit_wait_policy(tmp_path):
    with pytest.raises(ValueError,match='phase_wait_policy'):
        validate({'task_event_collector':{'version':2,'store':str(tmp_path/'s'),'tasks':[]}})
