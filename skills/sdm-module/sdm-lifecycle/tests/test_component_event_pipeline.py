"""Connected deterministic pipeline controls, not a real ML/platform claim."""
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/tests'))
from test_component_results import fixture
from test_research_event_service import setup, stop
from research_event_service import Inbox
from component_event_source import ComponentSource
from research_context import Context
from review_storage import atomic_json


def source_fixture(tmp_path):
    task,receipt,lines,expected=fixture()
    cfg,_=setup(tmp_path);workspace=tmp_path/'workspace';workspace.mkdir()
    cfg.update(schema='sdm-event-service/v2',cwd=str(workspace),
               research_context={'version':1,'root':str(tmp_path/'context')})
    def ref(name,value):
        path=tmp_path/name;atomic_json(path,value)
        return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    spec={'version':1,'binding':'attempt-1',
          'manager_config':ref('manager.json',{'component_transport':'official-cli','official_cli':'lpai-agent'}),
          'plan':ref('plan.json',{'route':'official-cli','request':{'action':'task.submit','uid':'component'}}),
          'expected':expected,
          'phase_intervals_seconds':{'queued':120,'running':30,'finishing':15,'terminal':300,'unknown':60}}
    cfg['component_source']=spec
    inbox=Inbox(cfg);source=ComponentSource(inbox,spec)
    manager=SimpleNamespace(checked=lambda *a,**kw:task)
    source.reconcile=lambda:(manager,receipt)
    logs={'status':'ok','data':{'resultType':'streams','result':[{'stream':{
        'namespace':'ns','pod':'bound-pod','container':'main'},'values':[[str(i),line] for i,line in enumerate(lines)]}]}}
    pods=[{'task_uid':'task','namespace':'ns','step':'task','pod_type':'user','name':'bound-pod'}]
    return inbox,source,task,logs,pods


def test_queued_wait_never_starts_a_model_and_keeps_due_time_after_restart(tmp_path):
    inbox,source,task,logs,pods=source_fixture(tmp_path);task['status']='queued'
    status,delay=source.cycle();assert status=='queued' and 119<=delay<=120
    assert inbox.snapshot()['jobs']==[]
    status,remaining=source.cycle();assert status=='waiting-observation' and remaining>0
    assert inbox.snapshot()['jobs']==[]


def test_publication_crash_replay_wakes_one_native_delivery_and_commits_receipt(tmp_path):
    inbox,source,task,logs,pods=source_fixture(tmp_path)
    with patch('component_event_source.collector.query',return_value=pods),patch('component_event_source.read_logs',return_value=logs):
        with patch.object(inbox,'accept',side_effect=OSError('crash after outbox commit')):
            with pytest.raises(OSError):source.cycle()
    assert (source.root/'completion-event.json').is_file() and not inbox.snapshot()['jobs']
    called=[]
    def execute(owner,row,folder):
        context=Context(owner.cfg)
        native=SimpleNamespace(thread_start=lambda **kw:SimpleNamespace(id='native-thread'))
        with context.lock():
            context.open(native,{});context.intent(row['id']);context.started(row['id'],'native-turn')
        called.append(row['id'])
        return {'status':'completed','detail':{'thread_id':'native-thread','turn_id':'native-turn',
                'terminal':'completed','evidence_kind':'synthetic connected pipeline control'}}
    worker=threading.Thread(target=inbox.work,args=(execute,));worker.start()
    try:
        assert source.cycle()==('delivered',None)
        assert source.cycle()==('delivered',None)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            jobs=inbox.snapshot()['jobs']
            if jobs and jobs[0]['status']=='completed':break
            time.sleep(.01)
        assert len(called)==1 and jobs[0]['status']=='completed'
        assert Context(inbox.cfg).load()['pending_job'] is None
        assert json.loads((source.root/'result-ready.json').read_bytes())['valid_training_round'] is True
    finally:stop(inbox,worker)


def test_incomplete_result_does_not_enter_native_delivery_queue(tmp_path):
    inbox,source,task,logs,pods=source_fixture(tmp_path)
    logs['data']['result'][0]['values'].pop()
    with patch('component_event_source.collector.query',return_value=pods),patch('component_event_source.read_logs',return_value=logs):
        with pytest.raises(ValueError):source.cycle()
    assert not (source.root/'completion-event.json').exists()
    assert not inbox.snapshot()['jobs']


def test_v2_rejects_model_writable_delivery_state(tmp_path):
    inbox,source,task,logs,pods=source_fixture(tmp_path)
    cfg=dict(inbox.cfg);cfg['root']=str(Path(cfg['cwd'])/'untrusted-delivery')
    with pytest.raises(ValueError,match='delivery_state_must_be_outside'):
        Inbox(cfg)


def test_unknown_write_wait_is_not_reported_as_platform_approval(tmp_path):
    inbox,source,task,logs,pods=source_fixture(tmp_path)
    source.reconcile=lambda:None
    source.unresolved={'status':'submission-outcome-unresolved','new_platform_writes':0}
    assert source.cycle()==('submission-outcome-unknown',120)
    assert inbox.snapshot()['jobs']==[]


def test_readonly_repair_is_bound_before_reads_and_forwarded_only_to_reconcile(tmp_path):
    from component_event_source import settings
    from lpai_manager import runtime_digest,digest_file,Manager
    inbox,source,task,logs,pods=source_fixture(tmp_path)
    spec=source.spec
    plan=Path(spec['plan']['path']);value=json.loads(plan.read_bytes());value['runtime_digest']='sha256:old'
    atomic_json(plan,value);spec['plan']['sha256']=hashlib.sha256(plan.read_bytes()).hexdigest()
    proof=tmp_path/'repair.json'
    def save_proof(value):
        atomic_json(proof,value)
        spec['readonly_runtime_repair']={'path':str(proof),'sha256':hashlib.sha256(proof.read_bytes()).hexdigest()}
    bound={'schema':'lpai-manager-readonly-runtime-repair/v1','read_only':True,
           'original_plan_sha256':digest_file(plan),'from_runtime':value['runtime_digest'],
           'to_runtime':runtime_digest(),'source_ref':'control-only explicit recovery'}
    config_path=Path(spec['manager_config']['path'])
    manager=json.loads(config_path.read_bytes());manager.update(schema='lpai-manager/v1',namespace='ns',
        owner='owner@example.com',state_root=str(tmp_path/'manager-state'),submission_registry=str(tmp_path/'registry'))
    atomic_json(config_path,manager)
    spec['manager_config']['sha256']=hashlib.sha256(config_path.read_bytes()).hexdigest()
    save_proof(bound)
    with pytest.raises(ValueError,match='requires_component_source_v2'):settings(spec,inbox.cfg)
    spec['version']=2
    settings(spec,inbox.cfg)
    original=ComponentSource(inbox,spec)
    with patch.object(Manager,'reconcile',return_value={'status':'submission-outcome-unresolved'}) as read,\
            patch.object(Manager,'apply',side_effect=AssertionError('no writes')):
        assert original.reconcile() is None
        assert read.call_args.kwargs['runtime_repair']==proof
    for field,bad in (('original_plan_sha256','sha256:other'),('from_runtime','sha256:other'),
                      ('to_runtime','sha256:other'),('read_only',False),('source_ref','')):
        save_proof({**bound,field:bad})
        with pytest.raises(ValueError,match='bound_readonly_runtime_repair_required'):settings(spec,inbox.cfg)
    save_proof(bound);proof.write_text('{}')
    with pytest.raises(ValueError,match='frozen_input_changed'):settings(spec,inbox.cfg)


def test_terminal_discovery_is_durable_before_result_collection_and_replay(tmp_path):
    from review_storage import read_json
    inbox,source,task,logs,pods=source_fixture(tmp_path)
    with patch('component_event_source.collector.query',return_value=[]):
        with pytest.raises(ValueError,match='component_terminal_pod_not_ready'):source.cycle()
    first=read_json(source.root/'terminal-observed.json')['first_terminal_observed_at']
    assert read_json(source.root/'monitor.json')['phase']=='collecting'
    assert not inbox.snapshot()['jobs']
    with patch('component_event_source.collector.query',return_value=pods),patch('component_event_source.read_logs',return_value=logs):
        assert source.cycle()==('delivered',None)
        assert source.cycle()==('delivered',None)
    monitor=read_json(source.root/'monitor.json')
    assert monitor['first_terminal_observed_at']==first
    assert monitor['phase']=='result-verified' and monitor['result_verified_at']>=first
    assert len(inbox.snapshot()['jobs'])==1
