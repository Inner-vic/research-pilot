"""Shared logical-submit ledger and independent read-only approval resolution."""
import json
from pathlib import Path
import re
import time
from review_storage import atomic_json,locked


class PendingApproval(ValueError):
    def __init__(self,value):
        super().__init__('provider_approval_pending')
        self.approval={k:value[k] for k in ('approval_id','status','mode') if k in value}


class Guard:
    def __init__(self,spec,state_path):
        from external_operations import sha
        self.scope={k:spec[k] for k in ('provider','namespace','owner','request_key','component_uid','configuration_digest')}
        if any(not isinstance(v,str) or not v.strip() for v in self.scope.values()):raise ValueError('complete_submission_scope_required')
        if not spec.get('submission_registry'):raise ValueError('shared_submission_registry_required')
        directory=Path(spec['submission_registry'])
        if not directory.is_absolute() or directory.resolve()!=directory:raise ValueError('canonical_submission_registry_required')
        self.root=directory
        self.key=sha({k:spec[k] for k in ('provider','namespace','owner','request_key')}).split(':')[1]
        self.path=directory/(self.key+'.json');self.state={}

    def lock(self):return locked(self.root,self.key+'.lock','submission_request_busy')

    def load(self):
        self.state=json.loads(self.path.read_bytes()) if self.path.exists() else {'schema':'sdm-submission-guard/v1','scope':self.scope,'phase':'new','history':[]}
        if self.state.get('schema')!='sdm-submission-guard/v1' or self.state.get('scope')!=self.scope:raise ValueError('submission_request_scope_changed')
        if self.state.get('phase') not in {'new','intent','pending-approval','resolved-no-effect','completed'}:raise ValueError('submission_guard_phase_invalid')
        proofs=[self.state.get('resolution')]+[row.get('resolution') for row in self.state['history']]
        for proof in filter(None,proofs):
            from task_pipeline import digest_bytes
            for name in ('request','receipt'):
                if digest_bytes(Path(proof[name]).read_bytes())!=proof[name+'_digest']:raise ValueError('submission_resolution_evidence_changed')
        for row in self.state['history']:
            if row.get('event') == 'unlaunched-rebind':
                from project_driver import file_digest
                if file_digest(row['proof']) != row['sha256']:
                    raise ValueError('unlaunched_rebind_proof_changed')
                proof=json.loads(Path(row['proof']).read_bytes())
                if any(file_digest(proof[key]) != proof[key+'_sha256'] for key in ('old_project','new_project')):
                    raise ValueError('unlaunched_rebind_project_changed')

    def save(self):atomic_json(self.path,self.state)

    def before_submit(self,state_path):
        if self.state['phase'] not in {'new','resolved-no-effect'}:raise ValueError('uncertain_submission_do_not_resubmit')
        if self.state.get('resolution'):
            self.state['history'].append({'event':'previous-resolution','resolution':self.state.pop('resolution')})
        for field in ('approval','route','uid'):self.state.pop(field,None)
        self.state.update(phase='intent',state_path=str(Path(state_path).resolve()))
        self.state['history'].append({'event':'intent','state_path':self.state['state_path'],'at':time.time()});self.save()

    def pending(self,approval,route):
        self.state.update(phase='pending-approval',approval=approval,route=route)
        self.state['history'].append({'event':'pending-approval','route':route,**approval});self.save()

    def completed(self,uid):
        if self.state['phase']=='completed' and self.state.get('uid')!=uid:raise ValueError('shared_submission_uid_changed')
        self.state.update(phase='completed',uid=uid);self.save()


def reconcile(task,root,request):
    from task_pipeline import current_task,digest,digest_bytes,execute
    from external_operations import Operation,sha
    root=Path(root).resolve()
    with locked(root,'.coordinator.lock','task_coordinator_busy'):
        if (root/'coordinator.lock').exists():raise ValueError('legacy_coordinator_lock_requires_reconciliation')
        task=current_task(task,root);node=request.get('node')
        if request.get('schema')!='sdm-submission-reconciliation/v1' or node not in task.get('drivers',{}):raise ValueError('submission_reconciliation_scope_required')
        spec=task['drivers'][node].get('external_operation')
        if not spec or spec.get('action') not in {'submit','switch'}:raise ValueError('submission_operation_required')
        statepath=root/(node+'-external-state.json')
        saved=json.loads(statepath.read_bytes())
        if saved.get('spec_digest')!=sha(spec) or 'submit' not in saved.get('intents',[]):raise ValueError('original_submit_intent_required')
        marker=json.loads((root/(node+'-started.json')).read_bytes())
        if marker.get('driver_digest')!=digest(task['drivers'][node]):raise ValueError('original_submission_driver_changed')
        if sha(json.loads((root/(node+'-external-operation.json')).read_bytes()))!=sha(spec):raise ValueError('original_submission_spec_changed')
        op=Operation(spec,statepath,60);guard=Guard(spec,statepath)
        with guard.lock():
            guard.load();phase=guard.state['phase']
            if phase not in {'pending-approval','resolved-no-effect','completed'} or not guard.state.get('approval',{}).get('approval_id'):raise ValueError('identified_pending_approval_required')
            if request.get('pending_approval')!=guard.state['approval'] or request.get('task_digest')!=digest(task):raise ValueError('pending_submission_binding_required')
            pending={k:v for k,v in saved.get('pending_approval',{}).items() if k not in {'command','route'}}
            if (pending and pending!=guard.state['approval']) or guard.state.get('state_path')!=str(statepath):raise ValueError('original_pending_attempt_required')
            op.authorization()
            if guard.state.get('resolution'):
                if sha(request)!=guard.state['resolution']['request_value_digest']:raise ValueError('submission_already_resolved_with_different_request')
                return {'status':'completed','resolution':guard.state['resolution'],'next_action':'fresh_authorized_attempt_required','no_submission_performed':True}
            key=request.get('reconciliation_id')
            if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',key):raise ValueError('reconciliation_id_required')
            run=root/'submission-reconciliation'/node/key;run.mkdir(parents=True,exist_ok=True)
            driver=request['driver']
            if driver.get('read_only') is not True or driver.get('external_operation',{}).get('action','read') not in {'read','diagnose'}:raise ValueError('read_only_submission_reconciliation_required')
            if Path(driver['receipt']).resolve()!=run/'readback.json':raise ValueError('isolated_submission_readback_required')
            bound=run/'request.json'
            if bound.exists() and json.loads(bound.read_bytes())!=request:raise ValueError('submission_reconciliation_request_changed')
            if not bound.exists():atomic_json(bound,request)
            result=execute(driver,run,'submission-readback',min(driver.get('timeout_seconds',60),300))
            atomic_json(run/'result.json',result)
            if result['status']!='completed':return {**result,'reconciled':False}
            payload=result['observation'];proof=payload.get('approval',{})
            if payload.get('task_node')!='submission-readback' or payload.get('scope')!=guard.scope:raise ValueError('submission_readback_scope_mismatch')
            if proof.get('approval_id')!=guard.state['approval']['approval_id'] or proof.get('status') not in {'cancelled','rejected','expired'} or proof.get('irreversible') is not True or proof.get('no_future_execution') is not True:raise ValueError('approval_irrevocably_closed_required')
            observed=proof.get('observed_at')
            if type(observed) not in (int,float) or not 0<=time.time()-observed<=120 or not proof.get('source_ref'):raise ValueError('fresh_approval_closure_proof_required')
            lookup=payload.get('lookup',{})
            if lookup.get('complete') is not True or not isinstance(lookup.get('matches'),list):raise ValueError('complete_submission_lookup_required')
            lookup_at=lookup.get('observed_at')
            if type(lookup_at) not in (int,float) or not 0<=time.time()-lookup_at<=120 or lookup_at<observed or not lookup.get('source_ref'):raise ValueError('fresh_lookup_after_closure_required')
            matches=lookup['matches']
            if len(matches)>1:raise ValueError('ambiguous_submission_identity')
            uid=op.identity(matches[0],request=True)['uid'] if matches else None
            resolution={'request':str(bound),'request_digest':digest_bytes(bound.read_bytes()),'request_value_digest':sha(request),'receipt':result['receipt'],'receipt_digest':result['receipt_digest'],'outcome':'adopt-existing' if uid else 'no-effect','uid':uid,'at':time.time()}
            guard.state.update(phase='completed' if uid else 'resolved-no-effect',resolution=resolution)
            if uid:guard.state['uid']=uid
            guard.state['history'].append({'event':'approval-resolved','outcome':resolution['outcome']});guard.save()
            return {'status':'completed','resolution':resolution,'next_action':'fresh_authorized_attempt_required','no_submission_performed':True}
