"""Invoke the registered original coordinator, never commands from an event.

Only new frozen integrations opt in. Existing worker ownership, model receipts,
resource authorization and native-lifecycle gates remain authoritative.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from review_storage import atomic_json


def run(selected, folder, timeout):
    project = Path(selected['project']); entry = Path(selected['entry'])
    output = Path(selected['output']); runtime = entry.parent
    if (not all(p.is_absolute() for p in (project, entry, output))
            or entry.name != 'run_project_pipeline.py'
            or hashlib.sha256(project.read_bytes()).hexdigest() != selected['project_sha256']):
        raise ValueError('registered_original_coordinator_required')
    manifest = selected['runtime_manifest']
    actual = {p.relative_to(runtime).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in runtime.rglob('*.py')}
    if not manifest or actual != manifest:
        raise ValueError('registered_frozen_coordinator_runtime_changed')
    if type(selected.get('allow_devspace_live')) is not bool:
        raise ValueError('explicit_coordinator_live_scope_required')
    modes={'research-replan':'--research-replan','prepare-implementation':'--research-implementation',
           'freeze-implementation':'--freeze-research-implementation','proposal-review':'--proposal-review'}
    if selected.get('mode', 'continue') not in ('continue','completed-replay',*modes):
        raise ValueError('registered_coordinator_mode_required')
    if selected.get('mode') == 'completed-replay':
        if selected['allow_devspace_live']:
            raise ValueError('terminal_replay_forbids_live_execution_scope')
        raw = (output/'execution.json').read_bytes(); state = json.loads(raw)
        if (hashlib.sha256(raw).hexdigest() != selected.get('terminal_state_sha256')
                or state.get('status') != 'completed' or not state.get('study_complete')
                or not state.get('research_complete') or not state.get('stop_verified')):
            raise ValueError('completed_released_goal_receipt_required_for_replay')
    argv = [sys.executable, '-B', str(entry), '--project', str(project), '--output', str(output)]
    if selected.get('mode') in modes:
        if selected['allow_devspace_live']:raise ValueError('research_preparation_forbids_live_resource_scope')
        argv.append(modes[selected['mode']])
    elif selected.get('mode') == 'completed-replay': argv.append('--research-status')
    else:
        argv += ['--devspace-execution', '--research-worker']
        if selected['allow_devspace_live']: argv.append('--allow-devspace-live')
    atomic_json(folder/'coordinator-intent.json', {'selected':selected,'argv':argv,'at':time.time()})
    with (folder/'coordinator-stdout.log').open('wb') as stdout, (folder/'coordinator-stderr.log').open('wb') as stderr:
        completed = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            timeout=timeout, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    raw = (folder/'coordinator-stdout.log').read_bytes()
    if completed.returncode or len(raw) > 2*1024*1024:
        raise ValueError('original_coordinator_failed_or_oversized_reconcile')
    result = json.loads(raw)
    if not isinstance(result, dict): raise ValueError('original_coordinator_receipt_required')
    if selected.get('mode') == 'completed-replay' and result.get('status') != 'research-goal-met':
        raise ValueError('completed_coordinator_replay_not_goal_met_reconcile')
    atomic_json(folder/'coordinator-result.json', result)
    return result
