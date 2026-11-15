"""Reconcile a known CLI gateway wait with a real, authorized native UI start.

No platform write or workload launch. Preserve the exact execution and original
failure, require a matching native receipt and fresh official same-UID readback.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import devspace_execution as execution
from review_storage import atomic_json
from task_event_collector import collector_lock
from task_pipeline import digest


def reconcile(config, root, receipt_path, readback_path):
    _, plan, binding, kind = execution.validate(config)
    if kind != 'live-platform-observation': raise ValueError('live_devspace_required')
    root=Path(root).resolve();receipt_path=Path(receipt_path).resolve();readback_path=Path(readback_path).resolve()
    with collector_lock(str(root/'execution')):
        statepath=root/'execution.json';raw=statepath.read_bytes();state=json.loads(raw)
        target=root/'native-start-reconciliation.json'
        if target.exists():
            prior=json.loads(target.read_bytes())
            if prior['binding']!=binding or state.get('binding')!=binding:raise ValueError('reconciliation_binding_changed')
            if any(prior.get(key)!=str(path) or prior.get(key+'_sha256')!=hashlib.sha256(path.read_bytes()).hexdigest()
                   for key,path in [('native_receipt',receipt_path),('official_readback',readback_path)]):
                raise ValueError('reconciliation_evidence_changed')
            return {'status':'already-reconciled','receipt':str(target),'new_platform_action':False}
        request=json.loads((root/'start-request.json').read_bytes())
        receipt=json.loads(receipt_path.read_bytes());readback=json.loads(readback_path.read_bytes())
        if state['binding']!=binding or state['uid']!=plan['resource']['uid']:
            raise ValueError('original_execution_identity_required')
        if (state.get('intents')!=['start'] or state.get('launch_intent') or state.get('ready_observed')
            or (root/'stop-request.json').exists() or state.get('original_failure',{}).get('error')!='official_operation_unconfirmed:pending_approval'):
            raise ValueError('only_known_prelaunch_gateway_wait_reconcilable')
        if (receipt.get('request_digest')!=digest(request) or receipt.get('uid')!=state['uid']
            or receipt.get('action')!='start' or receipt.get('performed') is not True or not receipt.get('source_ref')
            or type(receipt.get('at')) not in (int,float) or not request['requested_at']<=receipt['at']<=time.time()):
            raise ValueError('matching_actual_native_start_receipt_required')
        obj=readback.get('data',{});meta=obj.get('metadata',{})
        if (readback.get('status')!='ok' or meta.get('uid')!=state['uid'] or meta.get('name')!=plan['resource']['name']
            or obj.get('spec',{}).get('clusterName')!=plan['resource']['cluster']
            or obj.get('status',{}).get('phase') not in {'pulling','starting','ready'}
            or not receipt['at']<=readback_path.stat().st_mtime<=time.time()
            or time.time()-readback_path.stat().st_mtime>600):
            raise ValueError('fresh_official_same_resource_start_readback_required')
        proof={'schema':'sdm-native-start-reconciliation/v1','binding':binding,'uid':state['uid'],
               'before_state_sha256':hashlib.sha256(raw).hexdigest(),'native_receipt':str(receipt_path),
               'native_receipt_sha256':hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
               'official_readback':str(readback_path),'official_readback_sha256':hashlib.sha256(readback_path.read_bytes()).hexdigest(),
               'phase':obj['status']['phase'],'at':time.time(),'new_platform_action':False,
               'qualification':'Gateway wait was not Feishu delivery or platform approval; actual authorized native start observed'}
        atomic_json(target,proof)
        state.update(status='awaiting-start-readback',cleanup_required=False,collection_failed=False,
                     retryable_collection=False,native_start_reconciliation=str(target))
        state.pop('last_error',None)
        atomic_json(statepath,state)
        return {'status':'reconciled-awaiting-original-resume','receipt':str(target),'new_platform_action':False}


def main():
    p=argparse.ArgumentParser();p.add_argument('--project',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--native-receipt',type=Path,required=True);p.add_argument('--official-readback',type=Path,required=True)
    a=p.parse_args();print(json.dumps(reconcile(json.loads(a.project.read_bytes()),a.output,a.native_receipt,a.official_readback)))


if __name__=='__main__':main()
