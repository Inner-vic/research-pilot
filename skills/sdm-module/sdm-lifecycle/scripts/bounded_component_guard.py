"""POSIX component process-group guard. Process exit alone does not release platform allocations."""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time

from review_storage import atomic_json


def guard(argv, deadline, terminate_grace, receipt):
    if os.name != 'posix': raise ValueError('posix_process_group_guard_required')
    if not argv or not Path(argv[0]).is_absolute(): raise ValueError('absolute_program_argv_required')
    if not math.isfinite(deadline) or not math.isfinite(terminate_grace) or not 0 < terminate_grace <= 60:
        raise ValueError('finite_deadline_and_bounded_grace_required')
    if deadline <= time.time(): raise ValueError('component_lease_already_expired')
    interrupted = [False]
    def on_signal(*_): interrupted[0] = True
    previous = {s: signal.signal(s, on_signal) for s in (signal.SIGTERM, signal.SIGINT)}
    child = None
    try:
        child = subprocess.Popen(argv, shell=False, start_new_session=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result = {'schema': 'sdm-component-process-guard/v1', 'pid': child.pid, 'pgid': child.pid,
                  'deadline': deadline, 'started_at': time.time(), 'scope': 'process-stop', 'allocation_release_verified': False}
        atomic_json(receipt, result)
        monotonic_deadline = time.monotonic() + max(0, deadline - time.time())
        reason = None
        while child.poll() is None:
            if interrupted[0]: reason = 'external-stop'; break
            if time.time() >= deadline or time.monotonic() >= monotonic_deadline: reason = 'deadline'; break
            time.sleep(min(.1, max(.001, monotonic_deadline - time.monotonic())))
        if reason:
            try: os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: child.wait(timeout=terminate_grace)
            except subprocess.TimeoutExpired: pass
        # The leader may exit before descendants. Always clean its still-owned process group.
        try: os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        child.wait(timeout=terminate_grace)
        result.update(finished_at=time.time(), returncode=child.returncode, stop_reason=reason, process_group_stop_signalled=True)
        atomic_json(receipt, result); return result
    finally:
        if child is not None and child.poll() is None:
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            child.wait(timeout=terminate_grace)
        for sig, handler in previous.items(): signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deadline', type=float, required=True); parser.add_argument('--terminate-grace', type=float, default=5)
    parser.add_argument('--receipt', type=Path, required=True); parser.add_argument('argv', nargs=argparse.REMAINDER)
    args = parser.parse_args(); argv = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
    try:
        result = guard(argv, args.deadline, args.terminate_grace, args.receipt)
        print(json.dumps(result)); return 0 if result['returncode'] == 0 and not result['stop_reason'] else 2
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'failed', 'problem': type(error).__name__, 'allocation_release_verified': False})); return 2


if __name__ == '__main__': raise SystemExit(main())
