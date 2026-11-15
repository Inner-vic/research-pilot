"""Read-only release reconciliation; preserve failed cleanup and never resubmit work."""
import datetime as dt
import json
from pathlib import Path
import re

from external_operations import TERMINAL
from loop_contract import field
from review_storage import locked, atomic_json


def original(root):
    from task_pipeline import digest_bytes
    path=Path(root)/'cleanup.json'
    raw=path.read_bytes();value=json.loads(raw)
    if value.get('status')=='completed':raise ValueError('cleanup_already_completed')
    if value.get('receipt') and digest_bytes(Path(value['receipt']).read_bytes())!=value['receipt_digest']:
        raise ValueError('cleanup_receipt_changed')
    return value,digest_bytes(raw)


def ownership(root,node,identity_field):
    from task_pipeline import digest
    if node not in {'5','7','14','15','16','17'}:raise ValueError('allocation_ownership_node_required')
    rows=[json.loads(line) for line in (Path(root)/'events.jsonl').read_text(encoding='utf-8').splitlines()]
    matches=[row for row in rows if row['node']==node]
    if not matches:raise ValueError('accepted_allocation_ownership_required')
    row=matches[-1];result=row['result']
    if result.get('status')!='completed':raise ValueError('accepted_allocation_ownership_required')
    if result.get('receipt'):
        from task_pipeline import digest_bytes
        if digest_bytes(Path(result['receipt']).read_bytes())!=result['receipt_digest']:
            raise ValueError('ownership_receipt_changed')
    identity=field(result,identity_field)
    if not isinstance(identity,dict) or set(identity)!={'provider','namespace','uid'} or any(
            not isinstance(v,str) or not v.strip() for v in identity.values()):
        raise ValueError('same_resource_identity_required')
    return identity,digest(row),row['task_digest']


def release_observation(payload,identity,fresh=True):
    if payload.get('resource_identity')!=identity:raise ValueError('reconciliation_identity_mismatch')
    resource=payload.get('resource',{})
    if resource.get('status') not in TERMINAL or resource.get('released') is not True:
        raise ValueError('release_not_verified')
    if resource.get('pods_complete') is not True or not isinstance(resource.get('pods'),list) or any(
            not isinstance(p,dict) or p.get('status') not in TERMINAL|{'Succeeded','Failed'} for p in resource['pods']):
        raise ValueError('complete_terminal_pods_required')
    if not isinstance(payload.get('source_ref'),str) or not payload['source_ref'].strip():
        raise ValueError('release_source_required')
    stamp=dt.datetime.fromisoformat(payload['observed_at'])
    if stamp.utcoffset() is None:raise ValueError('timezone_required')
    if fresh and not 0<=(dt.datetime.now(dt.timezone.utc)-stamp).total_seconds()<=300:
        raise ValueError('release_readback_stale')


def effective(root,value):
    """Use independent acceptance without rewriting the original cleanup failure."""
    from task_pipeline import digest_bytes
    path=Path(root)/'cleanup-reconciled.json'
    if not path.exists():return value
    acceptance=json.loads(path.read_bytes())
    _,original_digest=original(root)
    if acceptance['original_cleanup_digest']!=original_digest:raise ValueError('original_cleanup_changed')
    identity,witness,task_digest=ownership(root,acceptance['ownership_node'],acceptance['identity_field'])
    if witness!=acceptance['ownership_event_digest'] or task_digest!=acceptance['task_digest']:
        raise ValueError('ownership_binding_changed')
    receipt=Path(acceptance['receipt'])
    raw=receipt.read_bytes()
    if digest_bytes(raw)!=acceptance['receipt_digest']:raise ValueError('reconciliation_receipt_changed')
    payload=json.loads(raw)
    if payload.get('status')!='completed' or payload.get('task_node')!='cleanup-readback':
        raise ValueError('release_readback_not_completed')
    release_observation(payload,identity,fresh=False)
    return {'status':'completed','reason':'independent_release_reconciled',
            'receipt':str(receipt),'receipt_digest':acceptance['receipt_digest'],
            'resource_identity':identity,'original_failure_retained':True,
            'reconciliation':str(path),'qualification':'project-readback-not-platform-certification'}


def reconcile(task,root,request):
    from task_pipeline import current_task,digest,execute
    root=Path(root).resolve()
    with locked(root,'.coordinator.lock','task_coordinator_busy'):
        if (root/'coordinator.lock').exists():raise ValueError('legacy_coordinator_lock_requires_reconciliation')
        value,cleanup_digest=original(root)
        if request.get('schema')!='sdm-cleanup-reconciliation/v1':raise ValueError('reconciliation_schema_required')
        identity,witness,task_digest=ownership(root,request['ownership_node'],request['identity_field'])
        if task_digest!=digest(current_task(task,root)):raise ValueError('reconciliation_task_mismatch')
        if request.get('original_cleanup_digest')!=cleanup_digest:raise ValueError('original_cleanup_binding_required')
        if (root/'cleanup-reconciled.json').exists():
            accepted=json.loads((root/'cleanup-reconciled.json').read_bytes())
            if accepted['request_digest']!=digest(request):raise ValueError('release_already_reconciled_with_different_request')
            return effective(root,value)
        key=request.get('reconciliation_id')
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',key):raise ValueError('reconciliation_id_required')
        run=root/'cleanup-reconciliation'/key;run.mkdir(parents=True,exist_ok=True)
        bound=run/'request.json'
        if bound.exists():
            if json.loads(bound.read_bytes())!=request:raise ValueError('reconciliation_request_changed')
        else:atomic_json(bound,request)
        driver=request['driver']
        if driver.get('read_only') is not True or driver.get('external_operation',{}).get('action','read') not in {'read','diagnose'}:
            raise ValueError('read_only_reconciliation_driver_required')
        if Path(driver['receipt']).resolve()!=run/'readback.json':raise ValueError('isolated_readback_receipt_required')
        result=execute(driver,run,'cleanup-readback',min(driver.get('timeout_seconds',60),300))
        atomic_json(run/'result.json',result)
        if result['status']!='completed':return {**result,'reconciled':False}
        release_observation(result['observation'],identity)
        acceptance={'schema':'sdm-cleanup-reconciliation-acceptance/v1','task_digest':task_digest,
            'original_cleanup_digest':cleanup_digest,'ownership_node':request['ownership_node'],
            'identity_field':request['identity_field'],'ownership_event_digest':witness,
            'request_digest':digest(request),'receipt':result['receipt'],'receipt_digest':result['receipt_digest']}
        atomic_json(root/'cleanup-reconciled.json',acceptance)
        return effective(root,value)
