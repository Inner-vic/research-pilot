"""Finish an interrupted read-only review after exact original model reconciliation."""
import argparse
import hashlib
import json
from pathlib import Path
from review_storage import atomic_json,read_json,locked
from task_pipeline import digest


def finish(config_path,job_id):
    from research_runtime import owner_alive
    from research_context import Context
    from research_event_service import Inbox
    from model_containment import verify_native_owner_empty
    from research_model_receipts import checked
    from model_runner import binding
    from event_coordinator import run
    cfg=read_json(config_path);root=Path(cfg['root']);folder=root/job_id
    if owner_alive(cfg):raise ValueError('original_owner_must_exit_before_review_reconciliation')
    context=Context(cfg).load()
    if context.get('pending_job') or context.get('pending_turn'):raise ValueError('idle_original_native_context_required')
    with locked(folder,'.coordinator-recovery.lock','original_coordinator_recovery_busy'):
        inbox=Inbox(cfg)
        with inbox.db() as db:row=db.execute('select * from jobs where id=?',(job_id,)).fetchone()
        if row is None:raise ValueError('original_review_job_required')
        if row['status']=='completed':return {'status':'already-accounted','job_id':job_id}
        if row['status'] not in {'preparing','unknown'}:raise ValueError('original_interrupted_coordinator_required')
        task=inbox.task(row['binding']);selected=task.get('coordinator',{})
        if task.get('kind')!='coordinator' or selected.get('mode') not in {'proposal-review','research-replan'} or selected.get('allow_devspace_live'):
            raise ValueError('only_readonly_original_review_may_be_reconciled')
        request=read_json(folder/'request.json')
        if request.get('binding')!={'job_id':job_id} or request.get('config')!=cfg:
            raise ValueError('original_review_dispatch_binding_changed')
        owner=verify_native_owner_empty(folder,{'job_id':job_id})
        project=read_json(selected['project']);runner=project['proposal_review']['model_runner'];proofs=[];interrupted=[]
        for path in Path(selected['output']).glob('proposal-review/*/model-calls/*/request.json'):
            call=path.parent;saved=checked(path,'sdm-model-runner-envelope/v1')
            if saved['config']!=runner or saved['binding']!=binding(runner,saved['request']):
                raise ValueError('original_review_model_binding_changed')
            if not (call/'intent.json').exists():continue
            receipt=call/'backend-completed.json'
            if not receipt.exists():raise ValueError('original_model_outcome_unknown')
            result=checked(receipt,'sdm-model-backend-completed/v1')
            if result['binding']!=saved['binding'] or result['status'] not in {'completed','failed','interrupted','cancelled-local'}:
                raise ValueError('original_model_completion_binding_changed')
            proofs.append({'path':str(receipt),'sha256':hashlib.sha256(receipt.read_bytes()).hexdigest()})
            if result['status']!='completed':interrupted.append(str(call))
        if not interrupted:raise ValueError('known_original_model_termination_required')
        recovery=folder/'known-model-recovery';recovery.mkdir(exist_ok=True)
        atomic_json(recovery/'proof.json',{'schema':'sdm-original-coordinator-recovery/v1','job_id':job_id,
            'config_digest':digest(cfg),'owner':owner,'known_calls':proofs,'terminated_calls':interrupted,
            'scope':'Original read-only coordinator handles known terminal call; no model resend, native turn or platform mutation.'})
        result=run(selected,recovery,cfg['timeout_seconds'])
        if result.get('status')!='proposal-model-failed' or result.get('known_terminal') is not True:
            raise ValueError('original_coordinator_must_report_known_model_termination')
        detail={'kind':'coordinator-only','coordinator_result':result,'coordinator_status':result['status'],
            'evidence_kind':task['evidence_kind'],'recovered_original_delivery':str(recovery/'proof.json')}
        atomic_json(folder/'result.json',{'job_id':job_id,'status':'completed','detail':detail})
        inbox.recover()
        return {'status':'original-review-accounted','job_id':job_id,'model_status':result['terminal_status'],
            'new_model_calls':0,'platform_writes':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True);parser.add_argument('--job',required=True)
    args=parser.parse_args()
    import sys,subprocess
    import review_storage
    cfg=read_json(args.config)
    if Path(review_storage.__file__).resolve().parent!=Path(cfg['runtime_root']).resolve():
        code='import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_path(sys.argv.pop(1),run_name="__main__")'
        proc=subprocess.run([getattr(sys,'_base_executable',None) or sys.executable,'-I','-S','-X','utf8','-B','-c',code,
            cfg['runtime_root'],str(Path(__file__).resolve()),'--config',str(Path(args.config).resolve()),'--job',args.job],
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        raise SystemExit(proc.returncode)
    print(json.dumps(finish(args.config,args.job)))
