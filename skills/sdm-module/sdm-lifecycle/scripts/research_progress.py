"""Derived progress from verified coordinator facts; no model/platform actions."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from review_storage import atomic_json,read_json
from task_pipeline import digest


def project(cfg,state):
    if state.get('config_digest')!=digest(cfg):raise ValueError('progress_configuration_changed')
    rows=[]
    for row in state['rounds']:
        ref=row['result'];ready_path=Path(ref['path']);raw=ready_path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ref['sha256'].removeprefix('sha256:'):
            raise ValueError('progress_original_result_reference_changed')
        ready=json.loads(raw);path=Path(ready['artifact_root'])/'result.json';raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ready['artifact_sha256']['result.json']:
            raise ValueError('progress_original_metric_artifact_changed')
        result=json.loads(raw);accepted=None
        if row.get('accepted_plan'):
            p=row['accepted_plan'];body=Path(p['path']).read_bytes()
            if hashlib.sha256(body).hexdigest()!=p['sha256'].removeprefix('sha256:'):
                raise ValueError('progress_accepted_plan_changed')
            accepted=json.loads(body)
        rows.append({'uid':ready['uid'],'metric':ready['primary_metric'],
            'validation':result['validation'],'config':result['config'],
            'selected_epoch':result['selected_epoch'],'result':ref,
            'base_uid':accepted.get('selected_candidate',{}).get('uid') if accepted else None,
            'intervention':accepted['research_revision']['intervention'] if accepted else 'initial baseline'})
    current=state['attempts'][-1];stage=current['stage'];reason=current.get('reason')
    health_path=Path(cfg['root'])/'component-pipeline/health.json'
    health=read_json(health_path) if health_path.exists() else {}
    health_blocked=(health.get('status')=='requires-reconciliation' and health.get('stage')==stage
        and health.get('config_sha256',digest(cfg))==digest(cfg)
        and health.get('state_sha256',digest(state))==digest(state))
    if health_blocked:reason=health.get('error_code') or health.get('error_type') or current.get('continuation',{}).get('category')
    from research_continuation import UNKNOWN,APPROVAL
    unresolved=current.get('observation') in UNKNOWN|APPROVAL or current.get('initial_submission_status') in UNKNOWN|APPROVAL and not current.get('observation')
    if unresolved:reason=current.get('observation') or current.get('initial_submission_status')
    # Scientific replanning folders do not own a different platform task.
    folder=Path(cfg['root'])/'component-pipeline'/current['id'];uid=None
    receipt=folder/'source/verified-submission.json'
    if receipt.exists():
        observed=read_json(receipt);uid=observed.get('resource',{}).get('uid',observed.get('uid'))
    if uid is None and current.get('result'):
        ref=current['result'];raw=Path(ref['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ref['sha256'].removeprefix('sha256:'):
            raise ValueError('progress_current_result_reference_changed')
        uid=json.loads(raw).get('uid')
    monitor_path=folder/'source/monitor.json'
    monitor=read_json(monitor_path) if monitor_path.exists() else None
    job=current.get('job');job_status=None
    database=Path(cfg['root'])/'delivery.sqlite'
    if job and database.exists():
        import sqlite3
        with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
            row=db.execute('select status from jobs where id=?',(job,)).fetchone()
        job_status=row[0] if row else None
    active_job=job if job_status in {'received','preparing','running'} else None
    goal=state.get('candidate_goal')
    target=cfg.get('component_pipeline',{}).get('decision_policy',{}).get('target')
    if goal is None and target is not None:
        goal={'target':target,'met':None,'quality_met':None,'verification':None,'gap':None,
            'status':'awaiting-first-verified-result'}
    return {'schema':'sdm-research-progress/v1','project_id':cfg['project_id'],'config_sha256':digest(cfg),
        'state_sha256':digest(state),'projected_at':time.time(),'status':state['status'],'stage':stage,
        'needs_intervention':stage=='needs-attention' or health_blocked or unresolved,'intervention_reason':reason,
        'continuation':current.get('continuation'),'automatic_resubmit':False,
        'monitoring':monitor,'result_handoff':current.get('result_handoff'),
        'execution_preflight':current.get('execution_preflight'),
        'health':health,
        'current_task_uid':uid,'last_observation':current.get('observation'),
        'goal':goal,'incumbent':{k:state.get('incumbent',{}).get(k) for k in ('uid','metric')},
        'rounds':rows,'active_job':active_job,'last_job':job,'last_job_status':job_status,
        'authority':'Derived report only; original pipeline/Guard/result receipts own decisions.',
        'scope':'Persisted verified results, not new provider observations or an automatic chat notification.'}


def publish(cfg,state):
    value=project(cfg,state);root=Path(cfg['root'])/'component-pipeline'
    path=root/'progress.json'
    if path.exists() and read_json(path).get('config_sha256')!=digest(cfg):
        raise ValueError('progress_output_owned_by_other_configuration')
    atomic_json(path,value)
    lines=['# Research progress','',f"Status: {value['status']} / {value['stage']}",
        f"Needs intervention: {value['needs_intervention']}; reason: {value['intervention_reason']}",
        f"Current task: {value['current_task_uid']}; observation: {value['last_observation']}",
        'Goal: '+json.dumps(value['goal'],ensure_ascii=False),
        'Incumbent: '+json.dumps(value['incumbent'],ensure_ascii=False),'',
        '| Task | macro-F1 | Actual base | Intervention |','| --- | --- | --- | --- |']
    for row in value['rounds']:
        text=row['intervention'].replace('|','/').replace('\n',' ')
        lines.append(f"| {row['uid']} | {row['metric']:.6f} | {row['base_uid'] or 'initial'} | {text} |")
    lines+=['','Derived from immutable result references. No model call, training or submission occurs in this renderer.']
    # This is an explicitly mutable projection, never a frozen evidence artifact.
    import uuid
    target=root/'PROGRESS.md';temp=target.with_name('.progress-'+uuid.uuid4().hex+'.tmp')
    temp.write_text('\n'.join(lines)+'\n',encoding='utf-8');temp.replace(target)
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True)
    args=parser.parse_args();cfg=read_json(args.config)
    value=publish(cfg,read_json(Path(cfg['root'])/'component-pipeline/pipeline.json'))
    print(json.dumps({'status':'projected','rounds':len(value['rounds']),'stage':value['stage'],
        'needs_intervention':value['needs_intervention'],'platform_writes':0,'model_calls':0}))
