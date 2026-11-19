"""Classify only this frozen host's receipts. Never retries a model or a POST."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
from review_storage import read_json
from task_pipeline import digest


def inspect(cfg):
    root=Path(cfg['root']); path=root/'component-pipeline/pipeline.json'
    state=read_json(path) if path.exists() else None
    report={'schema':'sdm-host-recovery/v1','config_sha256':digest(cfg),'can_restart_host':True,
        'action':'start-original-host','unresolved':[],'recoverable_receipts':[],
        'platform_writes':0,'model_calls':0,'scope':'same frozen config, Inbox, Context and original Guards only'}
    if state and state.get('config_digest')!=digest(cfg):raise ValueError('recovery_original_configuration_changed')
    if state and cfg.get('component_pipeline',{}).get('sdm_governance'):
        from research_acceptance import project as acceptance_project
        try:report['sdm_acceptance']=acceptance_project(cfg,state)
        except (ValueError,KeyError) as error:
            report['can_restart_host']=False
            report['unresolved'].append({'kind':'sdm-acceptance','action':'reconcile-original-evidence-no-resend','reason':str(error)[:180]})
    context_path=Path(cfg['research_context']['root'])/'context.json'
    context=read_json(context_path) if context_path.exists() else {}
    jobs=[]
    if (root/'delivery.sqlite').exists():
        with closing(sqlite3.connect((root/'delivery.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            jobs=[dict(row) for row in db.execute('select id,status from jobs order by rowid')]
    for row in jobs:
        if row['status'] in ('completed','paused') and row['id']!=context.get('pending_job'):continue
        receipt=root/row['id']/'result.json'
        if receipt.exists():
            saved=read_json(receipt)
            valid=(saved.get('job_id')==row['id'] and saved.get('status')=='completed')
            if row['id']==context.get('pending_job'):
                detail=saved.get('detail',{})
                valid=valid and detail.get('thread_id')==context.get('thread_id') and detail.get('turn_id')==context.get('pending_turn')
            if valid:
                report['recoverable_receipts'].append({'job_id':row['id'],
                    'sha256':hashlib.sha256(receipt.read_bytes()).hexdigest()});continue
        if row['status']=='received' and row['id']!=context.get('pending_job'):continue
        report['unresolved'].append({'job_id':row['id'],'status':row['status'],'action':'reconcile-original-call-no-resend'})
    pending=context.get('pending_job')
    if pending and pending not in {r['job_id'] for r in report['recoverable_receipts']}:
        report['unresolved'].append({'job_id':pending,'action':'reconcile-original-native-turn'})
    if state:
        current=state['attempts'][-1]
        report.update(stage=current['stage'],pipeline_status=state['status'])
        # The original host is the only executor of submission reconciliation.
        # A restart is allowed to reach that code; this inspector never applies it.
        if current.get('source',{}).get('plan'):
            report['original_plan']=current['source']['plan']
        if state['status']!='running':
            report.update(can_restart_host=False,action='terminal-no-restart')
    if report['unresolved']:report.update(can_restart_host=False,action='needs-reconciliation')
    report['progress_fingerprint']=digest({'state':state,'jobs':jobs,'context':context})
    return report


def exercise_settings(exercise):
    if (not isinstance(exercise,dict) or exercise.get('version')!=1 or not exercise.get('source_ref')
            or set(exercise)-{'version','source_ref','checkpoints'}
            or not isinstance(exercise.get('checkpoints'),list)
            or not set(exercise['checkpoints'])<={'native-receipt-durable','known-task-wait'}):
        raise ValueError('explicit_bounded_recovery_exercise_required')
    return exercise


def checkpoint(cfg,name,**detail):
    """Opt-in one-shot, durable-boundary local interruption for real exercises."""
    exercise=cfg.get('recovery_exercise')
    if exercise is None:return
    exercise_settings(exercise)
    if name not in exercise['checkpoints']:return
    from review_storage import atomic_json
    marker=Path(cfg['root'])/'recovery-exercise'/ (name+'.json')
    if marker.exists():
        if read_json(marker)['config_sha256']!=digest(cfg):raise ValueError('recovery_exercise_binding_changed')
        return
    atomic_json(marker,{'schema':'sdm-real-recovery-checkpoint/v1','checkpoint':name,
        'config_sha256':digest(cfg),'source_ref':exercise['source_ref'],'detail':detail,
        'platform_writes':0,'scope':'interrupt owned local host after durable state; no unknown POST injection'})
    import os
    os._exit(85)
