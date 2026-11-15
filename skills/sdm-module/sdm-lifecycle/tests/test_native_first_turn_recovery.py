"""First-turn crash controls; all provider/Job effects here are substitutes."""
import copy
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from research_context import Context
from research_event_service import Inbox
from research_model_receipts import seal
from review_storage import atomic_json,read_json
from test_research_context import config
from test_research_event_service import setup,stop


class InvalidRequestError(Exception):
    __module__='openai_codex.errors'
    def __init__(self,thread,code=-32600):self.code=code;self.message='no rollout found for thread id '+thread


class Native:
    def __init__(self):self.starts=[];self.resumes=[];self.fail_new=False;self.error=None
    def thread_start(self,**kwargs):
        self.starts.append(kwargs)
        if self.fail_new:raise TimeoutError('create outcome unknown')
        return SimpleNamespace(id='thread-'+str(len(self.starts)))
    def thread_resume(self,identity,**kwargs):
        self.resumes.append(identity)
        raise self.error or InvalidRequestError(identity)


def context_config(tmp_path):
    cfg=config(tmp_path);cfg['research_context'].update(stable_instructions=True,first_turn_journal=True)
    return cfg


def test_empty_thread_is_retired_only_after_exact_provider_no_rollout_and_keeps_job(tmp_path):
    cfg=context_config(tmp_path);native=Native();ctx=Context(cfg);args={'developer_instructions':'fixed'}
    assert ctx.open(native,args,job_id='job').id=='thread-1'
    assert ctx.load()['phase']=='created' and ctx.empty_for('job')
    with pytest.raises(ValueError,match='original_delivery'):Context(cfg).open(native,args)
    with pytest.raises(ValueError,match='bound_to_original'):Context(cfg).open(native,args,job_id='other')
    assert Context(cfg).open(native,args,job_id='job').id=='thread-2'
    state=ctx.load();assert state['empty_threads'][0]['thread_id']=='thread-1'
    assert state['empty_threads'][0]['opening_job']=='job' and ctx.empty_for('job')
    with pytest.raises(ValueError,match='bound_to_original'):ctx.intent('other')
    ctx.intent('job')
    assert ctx.load()['phase']=='ready' and ctx.load()['pending_job']=='job'
    with pytest.raises(ValueError,match='unknown_delivery'):Context(cfg).open(native,args,job_id='job')
    assert len(native.starts)==2 and native.resumes==['thread-1']


@pytest.mark.parametrize('failure',[TimeoutError('unknown resume'),InvalidRequestError('another'),InvalidRequestError('thread-1',-32000)])
def test_other_errors_never_create_a_replacement_thread(tmp_path,failure):
    cfg=context_config(tmp_path);native=Native();ctx=Context(cfg);ctx.open(native,{},job_id='job')
    native.error=failure
    with pytest.raises(type(failure)):Context(cfg).open(native,{},job_id='job')
    assert len(native.starts)==1 and ctx.load()['thread_id']=='thread-1' and not ctx.load().get('empty_threads')


def test_unknown_replacement_creation_stays_creating_and_is_not_repeated(tmp_path):
    cfg=context_config(tmp_path);native=Native();ctx=Context(cfg);ctx.open(native,{},job_id='job')
    native.fail_new=True
    with pytest.raises(TimeoutError):ctx.open(native,{},job_id='job')
    assert ctx.load()['phase']=='creating' and len(ctx.load()['empty_threads'])==1
    with pytest.raises(ValueError,match='unknown_delivery'):Context(cfg).open(native,{},job_id='job')
    assert len(native.starts)==2


def fixture(tmp_path):
    cfg,event=setup(tmp_path);workspace=tmp_path/'workspace';workspace.mkdir()
    cfg.update(schema='sdm-event-service/v3',cwd=str(workspace),research_context={
        'version':1,'root':str(tmp_path/'context'),'stable_instructions':True,'first_turn_journal':True},
        task_templates={'report':{'kind':'report','instructions':'control','evidence_kind':'control'}})
    cfg['tasks']['attempt-1']['kind']='report'
    inbox=Inbox(cfg);job=inbox.accept(event)['id'];ctx=Context(cfg);native=Native()
    ctx.open(native,{},job_id=job);inbox.transition(job,'running')
    evidence(inbox,job)
    return inbox,event,job,ctx,native


def evidence(inbox,job):
    with inbox.db() as db:row=dict(db.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone())
    folder=inbox.execution_folder(row);folder.mkdir(parents=True,exist_ok=True)
    binding={'job_id':job};name='Local\\SDM-Model-'+'a'*32
    atomic_json(folder/'request.json',{'binding':binding,'config':inbox.cfg,'row':row})
    atomic_json(folder/'dispatch-intent.json',{'binding':binding})
    atomic_json(folder/'containment.json',seal({'schema':'sdm-model-containment/v1','binding':binding,
        'job_name':name,'kill_on_last_close':True,'breakaway_allowed':False,'state':'stopped-empty',
        'local_empty_verified':True,'member_pids':[]}))
    atomic_json(folder/'guard-joined.json',seal({'schema':'sdm-model-guard-joined/v1','binding':binding,
        'job_name':name,'helper_pid':123,'temporary_job_handle_closed':True}))
    return folder


def test_no_turn_recovery_consumes_same_job_once_and_retains_old_attempt(tmp_path):
    inbox,event,job,ctx,native=fixture(tmp_path);old=inbox.root/job/'attempts/a0/request.json';raw=old.read_bytes()
    Inbox(inbox.cfg).recover()
    assert inbox.snapshot()['jobs'][0]['status']=='received'
    assert len(inbox.snapshot()['jobs'][0]['detail']['pre_turn_recoveries'])==1
    called=[]
    def execute(owner,row,folder):
        assert folder.name=='a1';called.append(row['id'])
        with Context(owner.cfg).lock():
            thread=Context(owner.cfg).open(native,{},job_id=job)
            Context(owner.cfg).intent(job);Context(owner.cfg).started(job,'one-turn')
        return {'status':'completed','detail':{'thread_id':thread.id,'turn_id':'one-turn','terminal':'completed'}}
    worker=threading.Thread(target=inbox.work,args=(execute,));worker.start()
    try:
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and inbox.snapshot()['jobs'][0]['status']!='completed':time.sleep(.01)
        assert inbox.snapshot()['jobs'][0]['status']=='completed' and called==[job]
        assert inbox.accept(event)['status']=='completed' and len(inbox.snapshot()['jobs'])==1
        assert old.read_bytes()==raw and Context(inbox.cfg).load()['pending_job'] is None
    finally:stop(inbox,worker)


@pytest.mark.parametrize('change',['pending','missing-owner','wrong-owner','changed-request','live-owner'])
def test_missing_or_uncertain_evidence_stays_unknown(tmp_path,change):
    inbox,event,job,ctx,native=fixture(tmp_path);folder=inbox.root/job/'attempts/a0'
    if change=='pending':ctx.intent(job)
    if change=='missing-owner':(folder/'guard-joined.json').unlink()
    if change=='wrong-owner':
        value=read_json(folder/'containment.json');value.pop('integrity');value['binding']={'job_id':'other'}
        atomic_json(folder/'containment.json',seal(value))
    if change=='changed-request':
        value=read_json(folder/'request.json');value['config']['model']='other';atomic_json(folder/'request.json',value)
    if change=='live-owner':
        with patch('model_containment.verify_native_owner_empty',side_effect=ValueError('still alive')):inbox.recover()
    else:inbox.recover()
    assert inbox.snapshot()['jobs'][0]['status']=='unknown'
    assert not inbox.snapshot()['jobs'][0]['detail'].get('pre_turn_recoveries') and len(native.starts)==1


def test_repeat_same_unstarted_checkpoint_stalls_and_proof_publication_crash_is_idempotent(tmp_path):
    inbox,event,job,ctx,native=fixture(tmp_path)
    original=inbox.transition
    def interrupted(key,status,**detail):
        if status=='received':raise OSError('crash after proof before publication')
        return original(key,status,**detail)
    with patch.object(inbox,'transition',side_effect=interrupted):inbox.recover()
    assert inbox.snapshot()['jobs'][0]['status']=='unknown'
    proof=inbox.root/job/'recovery/a0/proof.json';raw=proof.read_bytes()
    inbox.recover();assert inbox.snapshot()['jobs'][0]['status']=='received' and proof.read_bytes()==raw
    inbox.transition(job,'running');evidence(inbox,job);inbox.recover()
    assert inbox.snapshot()['jobs'][0]['status']=='unknown'
    assert inbox.snapshot()['jobs'][0]['detail']['reason']=='pre_turn_recovery_stalled'
    before=inbox.snapshot()['revision'];inbox.recover();assert inbox.snapshot()['revision']==before


def test_durable_attempt_completion_is_promoted_without_repeating_a_model_call(tmp_path):
    inbox,event,job,ctx,native=fixture(tmp_path);ctx.intent(job);ctx.started(job,'completed-turn')
    folder=inbox.root/job/'attempts/a0'
    value={'job_id':job,'status':'completed','detail':{'thread_id':'thread-1','turn_id':'completed-turn','terminal':'completed'}}
    raw=json.dumps(value,separators=(',',':')).encode();(folder/'result.json').write_bytes(raw)
    inbox.recover();inbox.recover()
    assert inbox.snapshot()['jobs'][0]['status']=='completed' and (inbox.root/job/'result.json').read_bytes()==raw
    assert ctx.load()['pending_job'] is None and len(native.starts)==1
