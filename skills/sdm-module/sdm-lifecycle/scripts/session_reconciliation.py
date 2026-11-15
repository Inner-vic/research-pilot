"""Independent read-only release acceptance for a segmented Loop session."""
import json
from pathlib import Path
import re
from cleanup_reconciliation import ownership,release_observation
from project_driver import file_digest
from review_storage import locked,atomic_json

def scope(study,session,config):
    from task_pipeline import digest
    study=Path(study).resolve();session=Path(session).absolute()
    if session.resolve()!=session or session.parent!=study/'sessions' or not session.is_dir():
        raise ValueError('owned_session_directory_required')
    if json.loads((study/'loop-config.json').read_bytes())!=config:raise ValueError('session_configuration_mismatch')
    original=session/'cleanup-result.json'
    missing=not original.exists()
    value=({'status':'failed','reason':'interrupted_session_without_cleanup_receipt'} if missing else json.loads(original.read_bytes()))
    if value.get('status')=='completed':raise ValueError('session_cleanup_already_completed')
    if value.get('receipt') and file_digest(value['receipt'])!=value.get('receipt_digest'):
        raise ValueError('session_cleanup_receipt_changed')
    intents=session/'dispatch-intents.json'
    if not intents.is_file():raise ValueError('complete_session_dispatch_manifest_required')
    entries=json.loads(intents.read_bytes())
    if not isinstance(entries,list) or not entries:raise ValueError('session_dispatches_required')
    roots=set()
    for item in entries:
        root=Path(item['round_root']).resolve()
        if root.parent!=study or root.name!=f"round-{item['round']:02d}":raise ValueError('session_round_binding_invalid')
        roots.add(str(root))
    if missing:
        last=session/'dispatch-intent.json'
        if not last.is_file() or json.loads(last.read_bytes())!=entries[-1]:
            raise ValueError('missing_cleanup_requires_complete_dispatch_binding')
    return study,session,value,{'configuration_digest':digest(config),'original_cleanup_digest':None if missing else file_digest(original),
                              'dispatch_manifest_digest':file_digest(intents)},roots

def witnesses(allocations,roots):
    from task_pipeline import digest
    if not isinstance(allocations,list) or not allocations:raise ValueError('session_allocations_required')
    covered=set();proof=[];identities={};seen=set()
    for item in allocations:
        attempt=Path(item['attempt']).absolute()
        if attempt.resolve()!=attempt or attempt.name!='attempt-01' or str(attempt.parent) not in roots:
            raise ValueError('allocation_outside_session')
        key=(str(attempt),item['node'],item['identity_field'])
        if key in seen:raise ValueError('duplicate_ownership_witness')
        seen.add(key)
        identity,witness,task_digest=ownership(attempt,item['node'],item['identity_field'])
        task=json.loads((attempt/'coordinator-task.json').read_bytes())
        if digest(task)!=task_digest:raise ValueError('session_attempt_task_mismatch')
        identities[digest(identity)]=identity;covered.add(str(attempt.parent))
        proof.append({**item,'ownership_event_digest':witness,'task_digest':task_digest})
    if covered!=roots:raise ValueError('all_session_dispatches_must_be_reconciled')
    return proof,identities

def observations(payload,identities,fresh):
    from task_pipeline import digest
    rows=payload.get('resource_readbacks')
    if payload.get('status')!='completed' or payload.get('task_node')!='cleanup-readback' or not isinstance(rows,list):
        raise ValueError('session_readback_contract_required')
    seen=set()
    for row in rows:
        identity=row.get('resource_identity');key=digest(identity)
        if key not in identities or key in seen:raise ValueError('exact_session_resources_required')
        release_observation(row,identities[key],fresh=fresh);seen.add(key)
    if seen!=set(identities):raise ValueError('all_session_resources_must_be_released')

def effective(path,value):
    from task_pipeline import digest
    session=Path(path).resolve().parent;acceptance_path=session/'cleanup-reconciled.json'
    if not acceptance_path.exists():return value
    study=session.parent.parent;config=json.loads((study/'loop-config.json').read_bytes())
    _,_,_,binding,roots=scope(study,session,config)
    accepted=json.loads(acceptance_path.read_bytes())
    if accepted.get('schema')!='sdm-session-reconciliation-acceptance/v1' or accepted.get('binding')!=binding:
        raise ValueError('session_reconciliation_binding_changed')
    proof,identities=witnesses(accepted['allocations'],roots)
    if proof!=accepted['ownership_proofs']:raise ValueError('session_ownership_changed')
    request_path=Path(accepted['receipt']).parent/'request.json'
    if digest(json.loads(request_path.read_bytes()))!=accepted['request_digest']:raise ValueError('session_request_changed')
    if file_digest(accepted['receipt'])!=accepted['receipt_digest']:raise ValueError('session_readback_changed')
    payload=json.loads(Path(accepted['receipt']).read_bytes());observations(payload,identities,False)
    return {'status':'completed','receipt':accepted['receipt'],'receipt_digest':accepted['receipt_digest'],
        'observation':{**payload,'resource':{'phase':'stopped'},'original_failure_retained':binding['original_cleanup_digest'] is not None,
                       'original_cleanup_missing':binding['original_cleanup_digest'] is None,
                       'qualification':'project-session-readback-not-platform-certification'}}

def reconcile(config,study,session,request):
    from task_pipeline import digest,execute
    study=Path(study).resolve()
    with locked(study,'.long-loop.lock','long_loop_busy'):
        if (study/'loop.lock').exists():raise ValueError('legacy_loop_lock_requires_reconciliation')
        _,session,value,binding,roots=scope(study,session,config)
        if request.get('schema')!='sdm-session-reconciliation/v1' or 'original_cleanup_digest' not in request or request.get('original_cleanup_digest')!=binding['original_cleanup_digest']:
            raise ValueError('session_request_binding_required')
        if request.get('missing_cleanup',False)!=(binding['original_cleanup_digest'] is None):
            raise ValueError('explicit_missing_cleanup_recovery_required')
        proof,identities=witnesses(request.get('allocations'),roots)
        accepted_path=session/'cleanup-reconciled.json'
        if accepted_path.exists():
            if json.loads(accepted_path.read_bytes())['request_digest']!=digest(request):raise ValueError('session_already_reconciled_with_different_request')
            return effective(session/'cleanup-result.json',value)
        key=request.get('reconciliation_id')
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',key):raise ValueError('reconciliation_id_required')
        root=session/'reconciliation'/key;root.mkdir(parents=True,exist_ok=True)
        driver=request['driver']
        if driver.get('read_only') is not True or driver.get('external_operation',{}).get('action','read') not in {'read','diagnose'}:
            raise ValueError('read_only_reconciliation_driver_required')
        if Path(driver['receipt']).resolve()!=root/'readback.json':raise ValueError('isolated_session_receipt_required')
        path=root/'request.json'
        if path.exists():
            if json.loads(path.read_bytes())!=request:raise ValueError('session_reconciliation_request_changed')
        else:atomic_json(path,request)
        result=execute(driver,root,'cleanup-readback',min(driver.get('timeout_seconds',60),300))
        atomic_json(root/'result.json',result)
        if result['status']!='completed':return {**result,'reconciled':False}
        observations(result['observation'],identities,True)
        atomic_json(accepted_path,{'schema':'sdm-session-reconciliation-acceptance/v1','binding':binding,
            'allocations':request['allocations'],'ownership_proofs':proof,'request_digest':digest(request),
            'receipt':result['receipt'],'receipt_digest':result['receipt_digest']})
        return effective(session/'cleanup-result.json',value)
