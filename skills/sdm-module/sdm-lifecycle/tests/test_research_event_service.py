"""Control-only reliability checks, no model inference or ML."""
import copy
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import urllib.request

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from research_event_service import Inbox, create_server, digest
from review_storage import atomic_json, locked


def setup(tmp_path):
    cfg = {'schema':'sdm-event-service/v1', 'project_id':'event-control',
        'runtime_root':str(Path(__file__).resolve().parents[1]/'scripts'),
        'runtime_manifest':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
            (Path(__file__).resolve().parents[1]/'scripts').glob('*.py')},
        'root':str(tmp_path/'service'), 'cwd':str(tmp_path), 'sdk_root':str(tmp_path/'sdk'),
        'cli_path':str(tmp_path/'codex.exe'), 'codex_home':str(tmp_path/'home'),
        'timeout_seconds':60, 'sandbox':'workspace-write', 'model':'control',
        'tasks':{'attempt-1':{'instructions':'Control only', 'evidence_kind':'non-ML control'}}}
    state = {'binding':'attempt-1', 'phase':'exited', 'exit_code':0}
    event = {'schema':'sdm-completion-event/v1', 'binding':'attempt-1', 'phase':'exited',
        'state':state, 'state_sha256':digest(state)}
    return cfg, event


def stop(inbox, worker):
    inbox.done.set()
    with inbox.condition: inbox.condition.notify_all()
    worker.join(3); assert not worker.is_alive()


def test_durable_receipt_duplicate_conflict_and_binding(tmp_path):
    cfg, event = setup(tmp_path); inbox = Inbox(cfg)
    first = inbox.accept(event); assert not first['duplicate']
    assert Inbox(cfg).accept(event)['duplicate']
    changed = copy.deepcopy(event); changed['state']['exit_code'] = 1; changed['state_sha256'] = digest(changed['state'])
    with pytest.raises(ValueError, match='conflicting_completion'): inbox.accept(changed)
    changed = copy.deepcopy(event); changed['binding'] = 'another-project'
    with pytest.raises(ValueError, match='binding'): inbox.accept(changed)
    changed = copy.deepcopy(event); changed['state']['exit_code'] = 2
    with pytest.raises(ValueError, match='binding'): inbox.accept(changed)
    cfg['tasks']['attempt-1']['instructions'] = 'Changed'
    with pytest.raises(ValueError, match='new_frozen_identity'): Inbox(cfg)


def test_restart_unknown_never_relaunches_and_completed_receipt_recovers(tmp_path):
    cfg, event = setup(tmp_path); inbox = Inbox(cfg); job = inbox.accept(event)['id']
    inbox.transition(job, 'running', thread_id='actual-thread', turn_id='actual-turn')
    Inbox(cfg).recover(); assert inbox.snapshot()['jobs'][0]['status'] == 'unknown'
    inbox.transition(job, 'running')
    atomic_json(Path(cfg['root'])/job/'result.json', {'job_id':job,'status':'completed','detail':{'terminal':'completed'}})
    Inbox(cfg).recover(); assert inbox.snapshot()['jobs'][0]['status'] == 'completed'
    assert inbox.accept(event)['status'] == 'completed'


def test_event_wakes_serial_consumer_once_without_interval_scheduler(tmp_path):
    cfg, event = setup(tmp_path); inbox = Inbox(cfg); called = []; done = threading.Event()
    def execute(owner, row, folder):
        called.append(row['id']); owner.transition(row['id'], 'running', turn_id='control-turn')
        done.set(); return {'status':'completed','detail':{'evidence_kind':'non-ML software substitute'}}
    worker = threading.Thread(target=inbox.work, args=(execute,)); worker.start()
    try:
        job = inbox.accept(event)['id']; assert done.wait(3)
        inbox.accept(event)
        deadline = time.monotonic()+3
        while inbox.snapshot()['jobs'][0]['status'] != 'completed' and time.monotonic() < deadline: time.sleep(.01)
        assert called == [job]; assert inbox.snapshot()['jobs'][0]['status'] == 'completed'
    finally: stop(inbox, worker)


def test_http_auth_and_single_owner(tmp_path):
    cfg, event = setup(tmp_path); inbox = Inbox(cfg); server = create_server(inbox, 'secret')
    worker = threading.Thread(target=server.serve_forever); worker.start()
    try:
        url = f'http://127.0.0.1:{server.server_port}/completion'
        request = urllib.request.Request(url, data=json.dumps(event).encode(), headers={'Authorization':'Bearer secret'})
        assert json.load(urllib.request.urlopen(request))['status'] == 'received'
        with pytest.raises(urllib.error.HTTPError): urllib.request.urlopen(urllib.request.Request(url,data=b'{}'))
        with locked(inbox.root, '.service.lock'):
            with pytest.raises(ValueError):
                with locked(inbox.root, '.service.lock'): pass
    finally: server.shutdown(); server.server_close(); worker.join()


def test_coordinator_registration_validates_before_any_command(tmp_path):
    from event_coordinator import run
    cfg, _ = setup(tmp_path)
    project = tmp_path/'project.json'; project.write_text('{}')
    entry = tmp_path/'run_project_pipeline.py'; entry.write_text('raise Exception("must never launch")')
    selected = {'project':str(project),'entry':str(entry),'output':str(tmp_path/'study'),
        'project_sha256':hashlib.sha256(project.read_bytes()).hexdigest(),
        'runtime_manifest':{'run_project_pipeline.py':'changed'},'allow_devspace_live':False}
    with pytest.raises(ValueError, match='runtime_changed'): run(selected, tmp_path, 30)
    assert not (tmp_path/'coordinator-intent.json').exists()


def test_terminal_event_reconstruction_has_identical_identity(tmp_path):
    from devspace_worker import completion_event
    cfg, expected = setup(tmp_path)
    assert completion_event(expected['binding'], expected['state']) == expected
    with pytest.raises(ValueError, match='terminal'): completion_event('wrong', expected['state'])


def test_pause_resume_preserves_queue_and_wakes_consumer(tmp_path):
    cfg, event = setup(tmp_path); inbox = Inbox(cfg); called = threading.Event()
    server = create_server(inbox, 'secret'); server_thread = threading.Thread(target=server.serve_forever)
    worker = threading.Thread(target=inbox.work, args=(lambda *args: (called.set() or
        {'status':'completed','detail':{'evidence_kind':'non-ML substitute'}}),))
    def control(action):
        request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/'+action,
            data=b'', headers={'Authorization':'Bearer secret'})
        urllib.request.urlopen(request).close()
    server_thread.start(); control('pause'); worker.start()
    try:
        inbox.accept(event); assert not called.wait(.1)
        control('resume'); assert called.wait(3)
    finally:
        stop(inbox, worker); server.shutdown(); server.server_close(); server_thread.join()


def test_terminal_goal_requires_original_consumption_and_unchanged_result(tmp_path):
    from research_terminal import inspect
    project_path = tmp_path/'project.json'; root = tmp_path/'study'; root.mkdir()
    config = {'research_worker':{'project_path':str(project_path),'output':str(root)},
        'research_iteration':{'targets':[{'field':'validation_accuracy','operator':'>=','value':.8}]}}
    atomic_json(project_path, config)
    result = root/'result.json'; atomic_json(result, {'validation_accuracy':.81})
    state = {'binding':'control','status':'completed','study_complete':True,'research_complete':True,
        'stop_verified':True,'main_workflow':{'status':'complete'},'review':{'binding':{'request_digest':'original-request'}},
        'history':[{'result_path':str(result),'result_digest':'sha256:'+hashlib.sha256(result.read_bytes()).hexdigest()}]}
    atomic_json(root/'execution.json',state)
    assert inspect(config,root)['status']=='completed-unverified'
    atomic_json(root/'research-decisions/control/commit.json', {'consumed':True,'record':{
        'request_digest':'original-request','sources':{str(project_path):'sha256:'+hashlib.sha256(project_path.read_bytes()).hexdigest()}}})
    assert inspect(config,root)['status']=='research-goal-met'
    atomic_json(result, {'validation_accuracy':.99})
    with pytest.raises(ValueError,match='digest_changed'): inspect(config,root)
