"""Independently supervised paired release watcher. Never submits a task or renews a lease."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from comparison_reporting import load_ref
from paired_execution import advance, authorize, contract, validate_config
from review_storage import atomic_json, read_json
from round_reporting import digest


def process_birth(pid):
    if os.name != 'posix': raise ValueError('linux_supervised_guard_required')
    value = Path('/proc')/str(pid)/'stat'
    try: return value.read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError): raise ValueError('guard_process_unavailable') from None


def proof(path, request, max_age=5):
    raw = Path(path).read_bytes(); record = json.loads(raw); now = time.time()
    if (record.get('schema') != 'sdm-paired-release-watch/v1' or record.get('contract_digest') != request['contract_digest']
        or record.get('status') != 'armed' or record.get('platform_mutations_enabled') is not True
        or record.get('pid') in (os.getpid(), os.getppid()) or not 0 <= now-record.get('observed_at', 0) <= max_age
        or record.get('process_birth') != process_birth(record.get('pid'))
        or record.get('resource_deadline', 0) < request['deadline']):
        raise ValueError('fresh_independent_release_watch_required')
    # Readiness is a local live process observation, not proof that platform stop/release has been qualified.
    return {'deadline': request['deadline'], 'scope': 'allocation-release', 'independent': True,
            'evidence_ref': {'path': str(Path(path).resolve()), 'digest': 'sha256:'+hashlib.sha256(raw).hexdigest()},
            'guard_pid': record['pid'], 'platform_release_qualification_ref': record.get('platform_release_qualification_ref')}


def watch(config_ref, state_root, heartbeat, allow_live=False, max_observation_seconds=None):
    config, _ = load_ref(config_ref); plan, _ = validate_config(config)
    approval = authorize(config, plan, time.time())
    if config['platform_mode']=='live' and not allow_live: raise ValueError('live_platform_effects_disabled')
    record = {'schema':'sdm-paired-release-watch/v1','contract_digest':contract(config),'pid':os.getpid(),
              'process_birth':process_birth(os.getpid()), 'started_at':time.time(),
              'resource_deadline':approval['resource_deadline'], 'status':'armed',
              'platform_mutations_enabled':True,'scope':'stop-owned-until-released',
              'platform_release_qualification_ref':config['adapter_settings'].get('release_qualification_ref'),
              'config_ref':config_ref,'new_training_submissions':0}
    begin=time.monotonic(); poll=min(config['monitoring']['poll_seconds'], 2)
    while True:
        record['observed_at']=time.time(); atomic_json(heartbeat, record)
        load_ref(config_ref)  # Drift stops this watcher; never import a replacement adapter.
        if (Path(state_root)/'state.json').is_file():
            try:
                state=advance(config,state_root,allow_live=allow_live,allow_submit=False)
            except ValueError as error:
                if str(error)!='review_workflow_busy': raise
                time.sleep(poll); continue
            record['group_status']=state['status']; record['unreleased_arms']=state.get('unreleased_arms',[])
            has_intent=any(v.get('submit_at') is not None for v in state.get('arms',{}).values())
            if not record['unreleased_arms'] and (state['status']=='executions-terminal-evidence-required' or has_intent):
                record.update(status='released',observed_at=time.time()); atomic_json(heartbeat,record); return record
            if state['status']=='cleanup-required':
                # Continue bounded readback/stop attempts under the same UID/deadline; do not reacquire.
                record['cleanup_required']=True
        if time.time() >= approval['resource_deadline']+config['monitoring']['cleanup_reserve_seconds']:
            record.update(status='cleanup-required',observed_at=time.time()); atomic_json(heartbeat,record); return record
        if max_observation_seconds is not None and time.monotonic()-begin >= max_observation_seconds:
            record.update(status='observation-ended-unqualified',observed_at=time.time()); atomic_json(heartbeat,record); return record
        time.sleep(poll)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-ref',type=Path,required=True); parser.add_argument('--state-root',type=Path,required=True)
    parser.add_argument('--heartbeat',type=Path,required=True); parser.add_argument('--apply',action='store_true')
    parser.add_argument('--allow-live',action='store_true'); args=parser.parse_args()
    try:
        if not args.apply: raise ValueError('explicit_watch_apply_required')
        result=watch(read_json(args.config_ref),args.state_root,args.heartbeat,args.allow_live)
        print(json.dumps(result)); return 0 if result['status']=='released' else 2
    except Exception as error:
        atomic_json(args.heartbeat,{'schema':'sdm-paired-release-watch/v1','status':'failed','observed_at':time.time(),
                                   'problem':str(error) if isinstance(error,ValueError) else type(error).__name__,
                                   'new_training_submissions':0})
        print(json.dumps({'status':'cleanup-required','problem':type(error).__name__})); return 2


if __name__=='__main__': raise SystemExit(main())
