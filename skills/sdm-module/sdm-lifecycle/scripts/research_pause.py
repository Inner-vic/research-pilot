"""Same-host user resume contract. This never starts a host, creates a model call or submits training."""
import hashlib,time
from pathlib import Path
from review_storage import atomic_json,read_json
from task_pipeline import digest


def generation(root):
    root=Path(root)
    return digest({name:hashlib.sha256((root/name).read_bytes()).hexdigest() if (root/name).exists() else None
        for name in ('DRAINING','PAUSED','STOP_AFTER_DRAIN','host-stop.json')})


def active_review(root,current):
    selected=None;stage=current['stage']
    if stage=='reviewing-code' or stage=='needs-attention' and str(current.get('reason','')).startswith('original-code-review:'):
        if current.get('code_review_project') and current.get('code_review_root'):
            selected=(Path(current['code_review_project']),Path(current['code_review_root']),'reviewing-code','proposal-review')
    elif stage=='reviewing-plan' or stage=='needs-attention' and str(current.get('reason','')).startswith('original-plan-review:'):
        if current.get('replan_root'):selected=(Path(current['replan_root']).parent/'replan-project.json',Path(current['replan_root']),'reviewing-plan','research-replan')
    if selected:
        if any(not path.resolve().is_relative_to(Path(root).resolve()) for path in selected[:2]):raise ValueError('pause_review_scope_changed')
    return selected


def resume(inbox,expected_generation,request_id):
    from research_drain import status,resume as release
    from research_recovery import inspect
    import uuid
    if not isinstance(request_id,str) or str(uuid.UUID(request_id))!=request_id:raise ValueError('resume_request_identity_required')
    with inbox.condition:
        receipt=inbox.root/'resume-requests'/(request_id+'.json');request={'generation':expected_generation,'request_id':request_id,'config_sha256':digest(inbox.cfg)}
        saved=read_json(receipt) if receipt.exists() else None
        if saved and saved['request']!=request:raise ValueError('resume_request_identity_changed')
        if saved and saved.get('phase')=='applied':return {'status':'resume-applied','duplicate':True,'receipt':str(receipt)}
        from research_restore import status as restore_status
        restoring=restore_status(inbox.cfg)
        if restoring and (Path(restoring['folder'])/'CANCEL').exists() and restoring['request_id']==request_id:
            raise ValueError('restore_cancelled_before_dispatch')
        if generation(inbox.root)!=expected_generation:raise ValueError('pause_generation_changed_refresh_required')
        if (inbox.root/'STOP_AFTER_DRAIN').exists() or (inbox.root/'host-stop.json').exists():raise ValueError('explicitly_stopped_host_requires_codex_reconciliation')
        drained=status(inbox)
        if not drained['requested'] or not drained['quiescent']:raise ValueError('resume_requires_quiescent_user_pause')
        if inbox.cfg.get('component_pipeline'):
            recovery=inspect(inbox.cfg)
            if not recovery['can_restart_host']:raise ValueError('resume_original_outcome_requires_reconciliation')
        path=inbox.root/'component-pipeline/pipeline.json'
        current=read_json(path)['attempts'][-1] if path.exists() else {}
        selected=active_review(inbox.root,current) if current else None;review=None
        job=None
        if current.get('job'):
            with inbox.db() as db:job=db.execute('select status from jobs where id=?',(current['job'],)).fetchone()
        if job and job[0]=='paused':selected=None # resume the same queued job, not a second coordinator delivery
        if saved is None:
            saved={'schema':'sdm-host-resume/v1','request':request,'phase':'intent','at':time.time(),'original_job':current.get('job'),'platform_actions':0,'model_calls':0}
            atomic_json(receipt,saved)
        if selected:
            project,root,stage,mode=selected
            steering=root/'research-control.json'
            from research_worker import control as review_control
            if not steering.exists() or not read_json(steering).get('paused'):
                review_control(root,True)
            from review_resume import resume as resume_review
            proof=resume_review(root,request_id)
            review={'project':str(project),'output':str(root),'stage':stage,'mode':mode,
                'original_job':saved['original_job'],'receipt':str(root/'resume-receipts'/(request_id+'.json')),'proof_digest':digest(proof)}
        atomic_json(inbox.root/'pipeline-resume.json',{'id':request_id,'config_sha256':digest(inbox.cfg),'at':time.time(),'review':review,'scope':'user-pause-only'})
        # The original condition excludes dispatch until both gates and replay marker agree.
        release(inbox);(inbox.root/'PAUSED').unlink(missing_ok=True)
        atomic_json(receipt,{**saved,'phase':'applied','review':review,'at':time.time()})
        inbox.condition.notify_all()
        return {'status':'resume-applied','duplicate':False,'receipt':str(receipt)}
