"""Portable process capsule used by the shared devspace executor (stdlib only).

This runs inside the authorized attempt directory, not inside the coordinator.
Its deadline survives loss of the observer. It stops the workload process group;
it cannot release a platform allocation. A claim is never automatically stolen.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from devspace_health import HealthGuard, validate_health


def save(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    # Windows readers briefly deny rename without DELETE sharing. Retry this
    # local publication only, never relaunch computation or retry platform calls.
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as error:
            if os.name != 'nt' or getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 9:
                raise
            time.sleep(.02 * (attempt + 1))


def relative(name):
    p = Path(name)
    if not name or p.is_absolute() or '..' in p.parts or ':' in name or '\\' in name:
        raise ValueError('portable_relative_file_required')
    return p


def child(root, name):
    p = root / relative(name)
    if p.resolve() != p or not p.is_relative_to(root):
        raise ValueError('linked_capsule_file_rejected')
    return p


def read(root, name):
    p = child(root, name)
    if not p.exists():
        return None
    if p.stat().st_size > 1024 * 1024:
        raise ValueError('observation_too_large')
    return json.loads(p.read_bytes())


def snapshot(root):
    result = read(root, 'process.json') or {'phase': 'unknown'}
    result['progress'] = read(root, 'progress.json')
    result['failure'] = read(root, 'failure.json')
    if any(result[k] is not None and not isinstance(result[k],dict) for k in ('progress','failure')):
        raise ValueError('structured_process_observation_required')
    result['claim_exists'] = (root / 'claim').exists()
    return result


def stop_group(process):
    if os.name == 'nt':
        # Kill the owned tree, never by process name or a reused saved PID.
        if process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=15)


def run(root):
    plan = read(root, 'capsule.json')
    # Durable claim BEFORE spawn: a crash in this gap requires reconciliation.
    try:
        (root / 'claim').mkdir()
    except FileExistsError:
        return
    state = {'phase': 'launching', 'binding': plan['binding'], 'started_at': time.time(),
             'deadline': plan['deadline'], 'worker_pid': os.getpid()}
    save(root / 'process.json', state)
    process = None
    try:
        validate_health(plan.get('health_policy'))
        guard = HealthGuard(root, plan['binding'], plan['health_policy']) if plan.get('health_policy') else None
        if plan['deadline'] is not None and time.time() >= plan['deadline']:
            raise TimeoutError('deadline_before_spawn')
        with (root / 'stdout.log').open('wb') as log:
            process = subprocess.Popen(plan['argv'], cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                       env={**os.environ, 'SDM_EXECUTION_BINDING': plan['binding']},
                                       start_new_session=os.name != 'nt',
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            state.update(phase='running', process_pid=process.pid)
            save(root / 'process.json', state)
            while process.poll() is None:
                if (root / 'cancel').exists() or (plan['deadline'] is not None and time.time() >= plan['deadline']):
                    state['termination_reason'] = 'cancelled' if (root / 'cancel').exists() else 'deadline'
                    stop_group(process)
                    break
                reason = guard.check() if guard else None
                if reason:
                    state['termination_reason'] = 'health:' + reason
                    stop_group(process)
                    break
                time.sleep(min(.1, plan['health_policy']['interval_seconds']) if guard else .1)
            if guard:
                reason = guard.poll()
                if reason: state.setdefault('termination_reason', 'health:' + reason)
                qualification = guard.qualification()
                if qualification: state.setdefault('termination_reason', 'health:' + qualification)
                state['health'] = {'accepted_evaluations': guard.accepted, 'last_healthy': guard.healthy,
                                  'first_anomaly': guard.anomaly, 'current_episode':guard.episode,
                                  'evidence_complete':qualification is None, 'errors': guard.errors[-20:]}
            state.update(phase='exited', exit_code=process.wait(), finished_at=time.time())
            # POSIX children must not outlive a finished parent, including exit 0.
            if os.name != 'nt':
                stop_group(process)
    except BaseException as error:
        if process is not None:
            stop_group(process)
        state.update(phase='worker_failed', error_type=type(error).__name__,
                     error=str(error)[:500], finished_at=time.time())
    finally:
        save(root / 'process.json', state)
        # New frozen runners publish a durable notification after terminal state.
        # It is a wake-up hint; the original coordinator still verifies/collects.
        save(root / 'completion-event.json', completion_event(plan['binding'], state))


def completion_event(binding, state):
    if state.get('binding') != binding or state.get('phase') not in ('exited','worker_failed'):
        raise ValueError('bound_terminal_process_required')
    raw = json.dumps(state, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return {'schema':'sdm-completion-event/v1', 'binding':binding, 'phase':state['phase'],
        'state':state, 'state_sha256':hashlib.sha256(raw.encode()).hexdigest()}


def follow_completion(root, binding):
    """Linux SSH stdout push, with durable catch-up and no interval scheduler.

    Install the directory watch BEFORE checking the terminal file, so a finish
    between subscription and initial read cannot be lost. SSH reconnection can
    replay the same event; host delivery identity prevents another model task.
    """
    import ctypes
    if sys.platform != 'linux': raise ValueError('inotify_completion_stream_requires_linux')
    root = root.resolve()
    if (read(root, 'capsule.json') or {}).get('binding') != binding:
        raise ValueError('completion_stream_binding_changed')
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.inotify_init1(os.O_CLOEXEC)
    if fd < 0: raise OSError(ctypes.get_errno(), 'inotify_init1')
    try:
        if libc.inotify_add_watch(fd, os.fsencode(root), 0x80 | 0x8 | 0x400 | 0x800) < 0:
            raise OSError(ctypes.get_errno(), 'inotify_add_watch')
        while True:
            event = read(root, 'completion-event.json')
            if event is None:
                state = read(root, 'process.json') or {}
                if state.get('phase') in ('exited','worker_failed'):
                    # Crash after terminal publication but before event publication:
                    # reconstruct the identical event, never rerun the workload.
                    event = completion_event(binding, state)
            if event is not None:
                if event.get('binding') != binding: raise ValueError('completion_event_binding_changed')
                print(json.dumps(event, ensure_ascii=False), flush=True)
                return
            os.read(fd, 65536)  # Kernel event wake-up, not time-based scanning.
            if not root.is_dir(): raise ValueError('completion_source_directory_removed')
    finally: os.close(fd)


def request(packet):
    root = Path(packet['root'])
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError('absolute_unlinked_capsule_root_required')
    action = packet['action']
    capsule = read(root, 'capsule.json') if root.exists() else None
    if capsule is not None and capsule.get('binding') != packet.get('binding'):
        raise ValueError('remote_attempt_binding_mismatch')
    if action == 'preflight':
        source=packet['source']
        if hashlib.sha256(source.encode('utf-8')).hexdigest()!=packet['sha256']:
            raise ValueError('preflight_source_digest_mismatch')
        seconds=packet['timeout_seconds']
        if type(seconds) is not int or not 1<=seconds<=300:raise ValueError('bounded_preflight_timeout_required')
        result=subprocess.run([sys.executable,'-B','-c',source],input=json.dumps(packet['parameters']),
                              capture_output=True,text=True,timeout=seconds, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            return {'status':'failed','exit_code':result.returncode,'stdout':result.stdout[-8000:],'stderr':result.stderr[-8000:]}
        return {'status':'passed','exit_code':0,'report':json.loads(result.stdout)}
    if action == 'launch':
        root.mkdir(parents=True, exist_ok=True)
        if any(root.iterdir()) and not (root / 'launch-intent.json').exists():
            raise ValueError('fresh_dedicated_attempt_directory_required')
        plan = packet['plan']
        if plan.get('binding') != packet.get('binding'):
            raise ValueError('launch_binding_mismatch')
        existing = read(root, 'capsule.json')
        if existing is not None and existing != plan:
            raise ValueError('capsule_binding_changed')
        # Immutable launch request. Never remove the intent to retry an unknown launch.
        try:
            fd = os.open(root / 'launch-intent.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return snapshot(root)
        with os.fdopen(fd, 'w') as f:
            json.dump({'binding': plan['binding'], 'at': time.time()}, f)
        save(root / 'capsule.json', plan)
        for row in packet['inputs']:
            data = base64.b64decode(row['data'], validate=True)
            if hashlib.sha256(data).hexdigest() != row['sha256']:
                raise ValueError('input_digest_changed')
            target = child(root, row['name'])
            if target.name in {'capsule.json', 'process.json', 'launch-intent.json', 'worker.py', 'cancel', 'completion-event.json'}:
                raise ValueError('reserved_capsule_filename')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        worker = child(root, 'worker.py')
        worker.write_text(packet['worker'], encoding='utf-8')
        with (root / 'worker.log').open('ab') as log:
            subprocess.Popen([sys.executable, '-B', str(worker), '--run', str(root)],
                             cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=os.name != 'nt',
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return snapshot(root)
    if action == 'observe':
        return snapshot(root)
    if action == 'cancel':
        if not (root / 'capsule.json').exists():
            raise ValueError('no_owned_capsule')
        (root / 'cancel').touch()
        return snapshot(root)
    if action == 'artifact-manifest-v1':
        if capsule is None:raise ValueError('owned_capsule_required_for_artifact_manifest')
        files={};total=0
        for name in packet['artifacts']:
            path=child(root,name)
            if not path.is_file():continue
            size=path.stat().st_size;total+=size
            if size>packet['max_artifact_bytes'] or total>packet['max_total_bytes']:
                raise ValueError('artifact_collection_budget_exceeded')
            with path.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
            files[name]={'bytes':size,'sha256':sha}
        return files
    if action == 'collect':
        files = {}
        total = 0
        for name in packet['artifacts']:
            path = child(root, name)
            if not path.is_file():
                continue
            size = path.stat().st_size
            total += size
            if size > packet['max_artifact_bytes'] or total > packet['max_total_bytes']:
                raise ValueError('artifact_collection_budget_exceeded')
            files[name] = base64.b64encode(path.read_bytes()).decode()
        return files
    raise ValueError('unknown_capsule_action')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--run':
        run(Path(sys.argv[2]).resolve())
    elif len(sys.argv) == 4 and sys.argv[1] == '--follow-completion':
        follow_completion(Path(sys.argv[2]), sys.argv[3])
    else:
        print(json.dumps(request(json.load(sys.stdin))))
