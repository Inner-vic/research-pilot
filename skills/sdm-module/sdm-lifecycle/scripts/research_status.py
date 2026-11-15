"""Stable read-only frontend projection; no prompts, tokens or credential data."""
import argparse
import json
from pathlib import Path
import sqlite3
import time
from review_storage import read_json
from task_pipeline import digest


def overview(cfg):
    root=Path(cfg['root']);pipeline=root/'component-pipeline/pipeline.json'
    state=read_json(pipeline) if pipeline.exists() else {}
    if state and state.get('config_digest')!=digest(cfg):raise ValueError('overview_original_configuration_changed')
    current=state.get('attempts',[{}])[-1];jobs=[];milestones=[]
    if (root/'delivery.sqlite').exists():
        with sqlite3.connect((root/'delivery.sqlite').as_uri()+'?mode=ro',uri=True) as db:
            jobs=[{'status':s,'count':n} for s,n in db.execute('select status,count(*) from jobs group by status')]
            milestones=[{'job_id':j,'status':s,'at':at} for j,s,at in db.execute('select job_id,status,at from changes order by rowid desc limit 40')]
    health=root/'component-pipeline/health.json';supervision=root/'supervision.json';recovery=root/'recovery.json'
    report={'schema':'sdm-research-overview/v1','project_id':cfg['project_id'],'config_sha256':digest(cfg),
        'status':state.get('status','not-started'),'stage':current.get('stage'),'paused':(root/'PAUSED').exists(),
        'goal':state.get('candidate_goal'),'incumbent':{k:state.get('incumbent',{}).get(k) for k in ('uid','metric')},
        'observed_task_uids':state.get('observed_task_uids',[]),'jobs':jobs,'milestones':milestones,
        'status_transport':'official provider observations with phase-dependent polling; no frontend wakeup',
        'os_login_registration':{'status':'not-installed-by-this-runtime','os_login_recovery_verified':False}}
    registration=root/'host-login-registration.json'
    if registration.exists():
        registered=read_json(registration)
        if registered['config_sha256']!=digest(cfg):raise ValueError('login_registration_configuration_changed')
        report['os_login_registration']={k:registered[k] for k in ('task_name','installed','observed_at','os_login_recovery_verified')}
    if health.exists():report['health']=read_json(health)
    folder=Path(current.get('continuation_folder',root/'component-pipeline'/current.get('id','initial')))
    source=folder/'source';submitted=source/'verified-submission.json'
    if not submitted.exists():
        for attempt in state.get('attempts',[]):
            if attempt.get('manager_plan') and attempt['manager_plan']==current.get('source',{}).get('plan'):
                candidate=Path(attempt.get('continuation_folder',root/'component-pipeline'/attempt['id']))/'submission-observation.json'
                if candidate.exists():submitted=candidate
    report['current_task']=None
    if submitted.exists():
        receipt=read_json(submitted);resource=receipt.get('resource',{})
        report['current_task']={'uid':resource.get('uid',receipt.get('uid')),'namespace':resource.get('namespace'),
            'last_known_platform_status':resource.get('status'),'submission_status':receipt.get('status'),
            'source':'persisted original Manager observation, not a new provider query'}
    observations=source/'observations.sqlite'
    if observations.exists():
        with sqlite3.connect(observations.as_uri()+'?mode=ro',uri=True) as db:
            row=db.execute('select task_key,snapshot,last_success,errors from observations order by last_attempt desc limit 1').fetchone()
        if row and row[1]:
            snapshot=json.loads(row[1]);report['current_task']={**(report['current_task'] or {}),
                'uid':snapshot['uid'],'namespace':snapshot['namespace'],
                'last_known_platform_status':snapshot['platform_status'],'last_success_at':row[2],
                'status_age_seconds':max(0,time.time()-row[2]) if row[2] else None,'observation_errors':row[3],
                'source':'persisted official platform observation; freshness is explicit'}
    if report['current_task'] and report['current_task']['uid']:
        report['observed_task_uids']=list(dict.fromkeys(report['observed_task_uids']+[report['current_task']['uid']]))
    if supervision.exists():
        v=read_json(supervision);report['supervision']={'status':v['status'],'generations':len(v['generations'])}
    if recovery.exists():
        v=read_json(recovery);report['recovery']={k:v.get(k) for k in ('action','unresolved','recoverable_receipts')}
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True)
    args=parser.parse_args();print(json.dumps(overview(read_json(args.config)),ensure_ascii=False))
