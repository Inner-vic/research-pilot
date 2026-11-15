"""Standalone read-only LPAI polling and durable local event inbox.

No training dispatch, stopping, external messages or AI wake-up are implemented.
SQLite transactions atomically commit observations and events. Consumers explicitly
acknowledge events after handling them; delivery is at least once, not exactly once.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import uuid


TERMINAL = {'succeeded', 'failed', 'stopped', 'cancelled', 'canceled'}
KNOWN = TERMINAL | {'pending', 'running', 'created', 'queued', 'preparing',
                    'scheduling', 'starting', 'stopping', 'image_pulling'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def validate(config):
    cfg = config.get('task_event_collector')
    if not isinstance(cfg, dict) or cfg.get('version') not in (1, 2):
        raise ValueError('task_event_collector_version_1_or_2_required')
    if cfg['version'] == 2:
        policy = cfg.get('phase_intervals_seconds')
        if (not isinstance(policy, dict) or set(policy) != {'queued','running','finishing','terminal','unknown'}
                or any(type(v) is not int or not 15 <= v <= 3600 for v in policy.values())):
            raise ValueError('explicit_phase_wait_policy_required')
    store = Path(cfg.get('store', ''))
    if not store.is_absolute():
        raise ValueError('absolute_local_store_required')
    for key, default, low, high in [('interval_seconds', 60, 15, 3600),
                                   ('query_timeout_seconds', 30, 1, 120),
                                   ('failure_backoff_max_seconds', 300, 15, 3600),
                                   ('error_threshold', 3, 1, 100)]:
        value = cfg.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError('invalid_' + key)
    tasks = cfg.get('tasks')
    if not isinstance(tasks, list) or len(tasks) > 100:
        raise ValueError('explicit_bounded_task_list_required')
    keys = set()
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {'uid', 'namespace'}:
            raise ValueError('exact_uid_namespace_binding_required')
        try:
            if str(uuid.UUID(task['uid'])) != task['uid']: raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError('canonical_task_uid_required') from None
        if not isinstance(task['namespace'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', task['namespace']):
            raise ValueError('valid_namespace_required')
        key = (task['namespace'], task['uid'])
        if key in keys: raise ValueError('duplicate_task_binding')
        keys.add(key)
    return cfg


@contextmanager
def collector_lock(store):
    """OS-owned lock releases on process death; no stale PID-file recovery needed."""
    path = Path(str(store) + '.collector.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0: stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError: raise ValueError('collector_already_running') from None
        else:
            import fcntl
            try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError: raise ValueError('collector_already_running') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(stream, fcntl.LOCK_UN)


def connect(cfg, evidence_kind=None):
    path = Path(cfg['store'])
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS observations(
          task_key TEXT PRIMARY KEY, snapshot TEXT, revision INTEGER NOT NULL DEFAULT 0,
          last_attempt REAL NOT NULL, last_success REAL, errors INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS events(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
          task_key TEXT NOT NULL, kind TEXT NOT NULL, observed_at REAL NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS acknowledgements(
          consumer TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES events(event_id),
          handled_at REAL NOT NULL, PRIMARY KEY(consumer,event_id));
    ''')
    try:
        with db:
            db.execute('PRAGMA foreign_keys=ON')
            if evidence_kind:
                db.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)', ('evidence_kind', evidence_kind))
                actual = db.execute('SELECT value FROM metadata WHERE key=?', ('evidence_kind',)).fetchone()[0]
                if actual != evidence_kind: raise ValueError('replay_and_live_stores_must_be_separate')
        return db
    except BaseException:
        db.close(); raise


def query(cfg, task, operation):
    """Only fixed official read operations. Never persist stderr or task command/env."""
    binary = Path(cfg.get('cli_path', ''))
    if not binary.is_absolute() or binary.name.lower() not in {'lpai-agent', 'lpai-agent.exe'} or not binary.is_file():
        raise ValueError('absolute_official_lpai_agent_path_required')
    if operation not in {'get', 'pods'}: raise ValueError('read_operation_required')
    result = subprocess.run([str(binary), 'tasks', operation, '--uid', task['uid'],
                             '--namespace', task['namespace']], capture_output=True,
                            encoding='utf-8', errors='replace', timeout=cfg.get('query_timeout_seconds', 30),
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode: raise ValueError('official_cli_query_failed')
    try: response = json.loads(result.stdout)
    except ValueError: raise ValueError('invalid_cli_json') from None
    if not isinstance(response, dict) or response.get('status') != 'ok' or 'data' not in response:
        raise ValueError('unsuccessful_cli_response')
    return response['data']


def normalize(task, native, pods):
    if not isinstance(native, dict) or any(native.get(k) != task[k] for k in ('uid', 'namespace')):
        raise ValueError('task_identity_mismatch')
    status = native.get('status')
    if not isinstance(status, str) or status.lower() not in KNOWN:
        raise ValueError('unknown_task_status')
    if pods is not None and not isinstance(pods, list): raise ValueError('explicit_complete_pod_list_required')
    selected = []
    names = set()
    for pod in pods or []:
        if not isinstance(pod, dict) or pod.get('task_uid') != task['uid'] or pod.get('namespace') != task['namespace']:
            raise ValueError('pod_identity_mismatch')
        name = pod.get('name')
        if not isinstance(name, str) or not name or name in names: raise ValueError('unique_pod_name_required')
        names.add(name)
        phase = pod.get('status')
        if not isinstance(phase, str) or phase.lower() not in {'pending','running','succeeded','failed','deleted','unknown'}:
            raise ValueError('unknown_pod_status')
        selected.append({'name': name, 'step': pod.get('step'), 'status': phase.lower(),
                         'exit_code': pod.get('exit_code')})
    compute = [p for p in selected if p['step'] == 'task']
    return {**task, 'platform_status': status.lower(), 'pods': sorted(selected, key=lambda p: p['name']),
            'pod_collection': 'unavailable' if pods is None else 'complete',
            'task_terminal': status.lower() in TERMINAL,
            'compute_pods_deleted': bool(compute) and all(p['status'] == 'deleted' for p in compute),
            'training_started_verified': False, 'experiment_success_verified': False,
            'billing_release_verified': False}


def event(db, key, kind, now, payload):
    # Transaction commit makes event and corresponding state visible together.
    eid = str(uuid.uuid4())
    db.execute('INSERT INTO events(event_id,task_key,kind,observed_at,payload) VALUES (?,?,?,?,?)',
               (eid, key, kind, now, canonical(payload)))


def observe(db, task, native=None, pods=None, error=False, threshold=3, now=None):
    now = time.time() if now is None else now
    key = task['namespace'] + '/' + task['uid']
    snapshot = None if error else normalize(task, native, pods)
    db.execute('BEGIN IMMEDIATE')
    try:
        row = db.execute('SELECT * FROM observations WHERE task_key=?', (key,)).fetchone()
        previous = dict(row) if row else {'snapshot':None, 'revision':0, 'errors':0, 'last_success':None}
        if row and now < row['last_attempt']: raise ValueError('observation_clock_regressed')
        if error:
            errors = previous['errors'] + 1
            if errors == threshold:
                event(db, key, 'collection_unavailable', now, {**task, 'consecutive_errors':errors,
                    'task_status':'unknown', 'last_success_at':previous['last_success']})
            db.execute('INSERT OR REPLACE INTO observations VALUES (?,?,?,?,?,?)',
                       (key, previous['snapshot'], previous['revision'], now, previous['last_success'], errors))
        else:
            value = canonical(snapshot)
            changed = value != previous['snapshot']
            if previous['errors'] >= threshold:
                event(db, key, 'collection_recovered', now, task)
            if changed:
                kind = 'task_terminal_observed' if snapshot['task_terminal'] else 'task_state_observed'
                event(db, key, kind, now, {'revision':previous['revision']+1, 'snapshot':snapshot})
            db.execute('INSERT OR REPLACE INTO observations VALUES (?,?,?,?,?,?)',
                       (key, value, previous['revision']+int(changed), now, now, 0))
        db.commit()
    except BaseException:
        db.rollback(); raise


def retry_delay(cfg,errors):
    """Only reads back off; a failed read never causes another submission."""
    threshold=cfg.get('error_threshold',3)
    if errors<threshold:return 0
    base=cfg.get('interval_seconds',60)
    return min(max(base,cfg.get('failure_backoff_max_seconds',300)),base*2**min(errors-threshold+1,8))


def wait_remaining(cfg, previous, now):
    """A persisted read schedule, independent from a model turn's timeout."""
    if previous is None:
        return 0
    if cfg['version'] == 1:
        delay = retry_delay(cfg, previous['errors'])
    else:
        snapshot = json.loads(previous['snapshot']) if previous['snapshot'] else {}
        phase = snapshot.get('platform_status', 'unknown')
        group = ('terminal' if phase in TERMINAL else 'running' if phase == 'running'
                 else 'finishing' if phase == 'stopping' else 'queued' if phase in KNOWN else 'unknown')
        base = cfg['phase_intervals_seconds'][group]
        delay = base if not previous['errors'] else max(base, min(
            cfg.get('failure_backoff_max_seconds',300), cfg.get('interval_seconds',60)*2**min(previous['errors'],8)))
    # Clock rollback cannot cause rapid querying or erase the last observation.
    return max(0, previous['last_attempt'] + delay - now)


def poll(config, allow_live=False, once=True):
    cfg = validate(config)
    if not allow_live: raise ValueError('explicit_allow_live_read_required')
    if not cfg['tasks']: raise ValueError('no_tasks_registered')
    with collector_lock(cfg['store']):
        db = connect(cfg, 'live-platform-observation')
        try:
            cycles = 0
            while True:
                # An operator may stop the collector without touching GPU tasks.
                if Path(str(cfg['store'])+'.stop').exists(): break
                for task in cfg['tasks']:
                    last=db.execute('SELECT last_attempt,errors,snapshot FROM observations WHERE task_key=?',
                                    (task['namespace']+'/'+task['uid'],)).fetchone()
                    if wait_remaining(cfg,last,time.time()) > 0:continue
                    try:
                        native = query(cfg, task, 'get')
                        normalize(task, native, None)
                    except (ValueError, OSError, subprocess.TimeoutExpired):
                        observe(db, task, error=True, threshold=cfg.get('error_threshold',3))
                    else:
                        try:
                            pods = query(cfg, task, 'pods')
                            normalize(task, native, pods)
                        except (ValueError, OSError, subprocess.TimeoutExpired):
                            # A Pod query failure must not hide an observed task failure.
                            pods = None
                        observe(db, task, native, pods, threshold=cfg.get('error_threshold',3))
                cycles += 1
                if once: break
                tick = min(15, min(cfg['phase_intervals_seconds'].values())) if cfg['version']==2 else cfg.get('interval_seconds',60)
                for _ in range(tick):
                    if Path(str(cfg['store'])+'.stop').exists(): break
                    time.sleep(1)
            return {'status':'collector_stopped' if not once else 'collected', 'cycles':cycles,
                    'store':cfg['store'], 'new_training_submissions':0}
        finally: db.close()


def replay(config, records):
    cfg = validate(config)
    if not isinstance(records, list): raise ValueError('replay_records_required')
    with collector_lock(cfg['store']):
        db = connect(cfg, 'cpu-simulation')
        try:
            for item in records:
                task = item['task']
                if task not in cfg['tasks']: raise ValueError('unregistered_replay_task')
                observe(db, task, item.get('native'), item.get('pods'), item.get('error',False),
                        cfg.get('error_threshold',3), item.get('observed_at'))
            return {'status':'replayed', 'records':len(records), 'evidence_kind':'cpu-simulation'}
        finally: db.close()


def inbox(config, consumer='coordinator', limit=100, ack_ids=None):
    cfg = validate(config)
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,80}', consumer): raise ValueError('valid_consumer_required')
    if type(limit) is not int or not 1 <= limit <= 500: raise ValueError('bounded_event_page_required')
    db = connect(cfg)
    try:
        if ack_ids is not None:
            if not isinstance(ack_ids,list) or len(ack_ids)>500 or any(not isinstance(i,str) for i in ack_ids):
                raise ValueError('bounded_ack_ids_required')
            with db:
                for eid in ack_ids:
                    if not db.execute('SELECT 1 FROM events WHERE event_id=?',(eid,)).fetchone():
                        raise ValueError('unknown_event_ack')
                    db.execute('INSERT OR IGNORE INTO acknowledgements VALUES (?,?,?)',(consumer,eid,time.time()))
        rows = db.execute('SELECT e.* FROM events e WHERE NOT EXISTS '
            '(SELECT 1 FROM acknowledgements a WHERE a.consumer=? AND a.event_id=e.event_id) '
            'ORDER BY e.seq LIMIT ?', (consumer,limit+1)).fetchall()
        kind = db.execute('SELECT value FROM metadata WHERE key=?',('evidence_kind',)).fetchone()
        events = []
        for row in rows[:limit]:
            item = dict(row); item['payload'] = json.loads(item['payload'])
            item['next_action'] = ('collect_terminal_logs_artifacts_and_verify_release' if item['kind']=='task_terminal_observed'
                else 'repair_collection_without_inferring_task_failure' if item['kind']=='collection_unavailable'
                else 'inspect_observation_and_verify_training_progress')
            events.append(item)
        states = [dict(r) for r in db.execute('SELECT task_key,last_attempt,last_success,errors FROM observations')]
        stale_after = 2*cfg.get('interval_seconds',60)+2*len(cfg['tasks'])*cfg.get('query_timeout_seconds',30)
        for state in states:
            state['collection_attempt_age_seconds'] = max(0,time.time()-state['last_attempt'])
            state['observation_age_seconds'] = (max(0,time.time()-state['last_success'])
                if state['last_success'] is not None else None)
            state['stale'] = state['observation_age_seconds'] is None or state['observation_age_seconds'] > stale_after
            state['collection_available'] = state['errors']==0
            state['retry_after_seconds'] = max(0,retry_delay(cfg,state['errors'])-state['collection_attempt_age_seconds'])
        return {'schema':'sdm-task-event-inbox/v1', 'consumer':consumer,
                'evidence_kind':kind[0] if kind else 'uninitialized', 'events':events,
                'has_more':len(rows)>limit, 'observations':states,
                'delivery':'at-least-once-explicit-ack', 'automatic_execution':False,
                'ai_wakeup_connected':False}
    finally: db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--once',action='store_true')
    mode.add_argument('--run',action='store_true')
    mode.add_argument('--replay',type=Path)
    parser.add_argument('--allow-live-read',action='store_true')
    args = parser.parse_args()
    config = json.loads(args.project.read_bytes())
    result = replay(config,json.loads(args.replay.read_bytes())) if args.replay else poll(config,args.allow_live_read,args.once)
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    from process_visibility import ensure_background_stdio
    ensure_background_stdio()
    main()
