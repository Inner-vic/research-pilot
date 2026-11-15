"""Reconcile completed original review calls across a pure user pause, never replay unknown effects."""
import hashlib
from pathlib import Path
from review_storage import atomic_json,locked,read_json
from task_pipeline import digest


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resume(root,request_id):
    import uuid
    if not isinstance(request_id,str) or str(uuid.UUID(request_id))!=request_id:raise ValueError('resume_request_identity_required')
    root=Path(root);receipt=root/'resume-receipts'/(request_id+'.json')
    with locked(root,'.research-control.lock'):
        path=root/'research-control.json';current=read_json(path)
        if receipt.exists():
            saved=read_json(receipt)
            if saved['request_id']!=request_id:raise ValueError('review_resume_identity_changed')
            if current==saved['after']:return saved
            if current!=saved['before']:raise ValueError('review_resume_control_changed')
        else:
            if current.get('paused') is not True:raise ValueError('review_not_user_paused')
            from research_model_receipts import checked
            from model_runner import binding,completed
            calls={}
            for intent in root.glob('proposal-review/*/model-calls/*/intent.json'):
                call=intent.parent
                envelope=checked(call/'request.json','sdm-model-runner-envelope/v1')
                if envelope['binding']!=binding(envelope['config'],envelope['request']):raise ValueError('resume_call_binding_changed')
                result=completed(call,envelope['binding'])
                if not result or result['status']!='completed':raise ValueError('resume_original_model_outcome_requires_reconciliation')
                calls[str(call.resolve())]={'request_sha256':sha(call/'request.json'),
                    'completion_sha256':sha(call/'backend-completed.json'),
                    'original_revision':envelope['request']['evidence']['control_revision']}
            saved={'schema':'sdm-review-resume/v1','request_id':request_id,'before':current,
                'after':{'revision':current['revision']+1,'paused':False,'resume_receipt':str(receipt.resolve())},
                'calls':calls,'model_calls':0,'scope':'unchanged original review subject, config, evidence and call bytes only'}
            atomic_json(receipt,saved) # intent before mutable steering; safe to finish this exact transition
        atomic_json(path,saved['after']);return saved


def authorized(root,call,revision):
    root=Path(root);current=read_json(root/'research-control.json')
    if current.get('paused') or current.get('revision')!=revision or not current.get('resume_receipt'):return False
    receipt=Path(current['resume_receipt']).resolve()
    if not receipt.is_relative_to((root/'resume-receipts').resolve()):raise ValueError('resume_receipt_scope_changed')
    saved=read_json(receipt)
    if saved.get('schema')!='sdm-review-resume/v1' or current!=saved['after']:raise ValueError('resume_receipt_control_changed')
    proof=saved['calls'].get(str(Path(call).resolve()))
    if not proof:return False
    if proof['request_sha256']!=sha(Path(call)/'request.json') or proof['completion_sha256']!=sha(Path(call)/'backend-completed.json'):
        raise ValueError('resume_original_call_bytes_changed')
    return True
