"""Bounded invocations for a persistent study; no scheduler or paid operations."""
import json
from pathlib import Path
import uuid
from review_storage import locked,atomic_json

def policy(config):
    value=config.get('long_loop')
    if value is None:return None
    if not isinstance(value,dict) or set(value)!={'rounds_per_invocation'} or type(value['rounds_per_invocation']) is not int or value['rounds_per_invocation']<1:
        raise ValueError('bounded_long_loop_slice_required')
    if config.get('preparation_is_read_only') is not True or config.get('segmented_cleanup_supported') is not True:
        raise ValueError('segmented_adapter_contract_required')
    return value

def authority(config):
    from orchestration import load,value_digest
    task={'orchestration':config.get('orchestration',{})}
    proposal,_,_=load(task,'proposal');confirmation,_,_=load(task,'confirmation')
    if proposal is None or confirmation is None:return False
    if proposal.get('planning_status') in {'draft','collecting'} or confirmation.get('confirmed') is not True:return False
    if proposal.get('execution_scope')!={'study_id':config['study_id'],'trial_id':config['trial_id']}:
        raise ValueError('fresh_round_execution_scope_required')
    if confirmation.get('proposal_digest')!=value_digest(proposal):raise ValueError('round_confirmation_mismatch')
    # Full freshness, human authority and queue validation remain in task dispatch_guard.
    return True

def cleanup_digest(path):
    from project_driver import file_digest
    if Path(path).is_file():return file_digest(path)
    from generic_project_loop import read_cleanup
    from task_pipeline import digest
    return digest(read_cleanup(path))


def run(config,output,execute):
    policy(config)
    output=Path(output).resolve()
    with locked(output,'.long-loop.lock','long_loop_busy'):
        if (output/'loop.lock').exists():raise ValueError('legacy_loop_lock_requires_reconciliation')
        path=output/'long-loop-state.json';previous=json.loads(path.read_bytes()) if path.exists() else {}
        prior=previous.get('cleanup_path')
        if prior and previous.get('phase')=='complete':
            from project_driver import file_digest
            if cleanup_digest(prior)!=previous.get('cleanup_digest'):
                raise ValueError('long_loop_cleanup_evidence_changed')
        if previous.get('phase')=='running':
            abandoned=Path(previous['session_root'])
            if (abandoned/'dispatch-intent.json').exists() and not (abandoned/'cleanup-result.json').exists():
                if (abandoned/'cleanup-reconciled.json').exists():
                    from generic_project_loop import read_cleanup
                    read_cleanup(abandoned/'cleanup-result.json')
                    prior=str(abandoned/'cleanup-result.json')
                else:
                    result={'run_id':config['run_id'],'status':'cleanup-required','rounds':[],
                        'reason':'interrupted_slice_requires_reconciliation'}
                    atomic_json(output/'loop-result.json',result)
                    return result
            else:
                prior=str(abandoned/'cleanup-result.json') if (abandoned/'cleanup-result.json').exists() else previous.get('prior_cleanup_path')
        session=output/'sessions'/uuid.uuid4().hex;session.mkdir(parents=True)
        state={'phase':'running','session_root':str(session),'prior_cleanup_path':prior,'cleanup_path':None}
        atomic_json(path,state)
        result=execute(config,output,{'root':session,'prior_cleanup':prior,'new_rounds':0})
        cleanup=session/'cleanup-result.json'
        final_cleanup=str(cleanup) if cleanup.exists() else prior
        from project_driver import file_digest
        phase='running' if (session/'dispatch-intent.json').exists() and not cleanup.exists() else 'complete'
        atomic_json(path,{**state,'phase':phase,'cleanup_path':final_cleanup,
                          'cleanup_digest':cleanup_digest(final_cleanup) if final_cleanup else None})
        return result
