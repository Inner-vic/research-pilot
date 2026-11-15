"""Read-only resident collector, diagnostic consumer and notification outbox.

The only platform operations are tasks get/pods/summary-events and logs pod.
Notification delivery is separate; an explicitly configured wake-up may queue Codex.
No training writes are available.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from datetime import datetime, timezone

import task_event_collector as collector
import task_event_wakeup as wakeup


def settings(config):
    cfg=collector.validate(config)
    service=config.get('task_event_service',{})
    if service.get('version')!=1 or service.get('read_only') is not True:
        raise ValueError('read_only_service_v1_required')
    root=Path(service.get('evidence_root',''))
    if not root.is_absolute():raise ValueError('absolute_evidence_root_required')
    bindings=service.get('bindings',[])
    if len(bindings)!=len(cfg['tasks']):raise ValueError('exact_service_task_bindings_required')
    for task in cfg['tasks']:
        matches=[b for b in bindings if b.get('uid')==task['uid'] and b.get('namespace')==task['namespace']]
        if len(matches)!=1 or not re.fullmatch(r'[a-zA-Z0-9_-]+',matches[0].get('cluster','')):
            raise ValueError('verified_cluster_binding_required')
    return cfg,service


def connect(cfg):
    db=collector.connect(cfg)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS diagnostic_receipts(
          event_id TEXT PRIMARY KEY, report TEXT NOT NULL, handled_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS diagnostic_latest(
          task_key TEXT PRIMARY KEY, report TEXT NOT NULL, checked_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS notification_outbox(
          id TEXT PRIMARY KEY, task_key TEXT NOT NULL, kind TEXT NOT NULL,
          created_at REAL NOT NULL, message TEXT NOT NULL, report TEXT NOT NULL,
          delivered_at REAL, delivery_receipt TEXT);
        CREATE TABLE IF NOT EXISTS service_health(
          id INTEGER PRIMARY KEY CHECK(id=1), pid INTEGER, updated_at REAL, status TEXT);
    ''')
    return db


def redact(text):
    text=re.sub(r'(?i)(bearer\s+)[a-z0-9._~+/-]+',r'\1[REDACTED]',text)
    text=re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|password|authorization|secret)\s*[=:]\s*)[^\s,;]+',r'\1[REDACTED]',text)
    text=re.sub(r'\blpak_[A-Za-z0-9_-]+','[REDACTED]',text)
    return text


def read_cli(cfg,task,operation,cluster=None,pod=None):
    binary=Path(cfg.get('cli_path',''))
    if not binary.is_absolute() or binary.name.lower() not in {'lpai-agent','lpai-agent.exe'} or not binary.is_file():
        raise ValueError('absolute_official_lpai_agent_path_required')
    if operation=='summary-events':
        argv=['tasks','summary-events','--uid',task['uid'],'--namespace',task['namespace']]
    elif operation=='logs' and cluster and pod:
        argv=['logs','pod','--namespace',task['namespace'],'--cluster',cluster,
              '--pod',pod,'--direction','backward','--limit','160']
    else:raise ValueError('read_only_diagnostic_operation_required')
    result=subprocess.run([str(binary),*argv],capture_output=True,text=True,encoding='utf-8',
                          errors='replace',timeout=cfg.get('query_timeout_seconds',30),
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('diagnostic_cli_query_failed')
    try:value=json.loads(result.stdout)
    except ValueError:raise ValueError('invalid_diagnostic_json') from None
    if not isinstance(value,dict) or value.get('status')!='ok':raise ValueError('diagnostic_query_not_ok')
    return value.get('data')


def log_lines(data):
    if not isinstance(data,dict) or not isinstance(data.get('result'),list):
        raise ValueError('unsupported_log_shape')
    lines=[]
    for stream in data['result']:
        for pair in stream.get('values',[]):
            if not isinstance(pair,list) or len(pair)!=2 or not str(pair[0]).isdigit() or not isinstance(pair[1],str):
                raise ValueError('unsupported_log_entry')
            lines.append({'timestamp':str(pair[0]),'text':redact(pair[1][:12000])})
    return sorted(lines,key=lambda row:int(row['timestamp']))[-160:]


def analyze(snapshot,logs):
    lines=[row['text'] for part in logs.values() for row in part]
    steps=[];updates=[];signals=set();bad_update_seen=False
    for line in lines:
        match=re.search(r'\bEpoch\s+(\d+)\s+Iter\s+(\d+)',line)
        if match:steps.append([int(match[1]),int(match[2])])
        if 'NORMAL_UPDATE_HEALTH ' in line:
            try:
                health=json.loads(line.split('NORMAL_UPDATE_HEALTH ',1)[1])
                count=health.get('optimizer_updates')
                if type(count) is int and count>0:updates.append(count)
                bad_update_seen=bad_update_seen or health.get('consecutive_bad_updates',0)>0
            except (ValueError,TypeError):pass
        if re.search(r'CUDA out of memory|OutOfMemoryError',line,re.I):signals.add('oom_log_evidence')
        if re.search(r'Traceback \(most recent call last\)|RuntimeError:',line):signals.add('exception_log_evidence')
        if re.search(r'NCCL.*(?:error|timeout|failed)',line,re.I):signals.add('nccl_error_log_evidence')
        if re.search(r'non.?finite|\bnan\b|\binf\b',line,re.I):signals.add('nonfinite_text_requires_update_context')
    role_pods=[p for p in snapshot['pods'] if re.search(r'-(?:master|worker)-\d+$',p['name'])]
    return {'platform_status':snapshot['platform_status'],
            'gpu_role_pods_running':sum(p['status']=='running' for p in role_pods),
            'gpu_role_pods_observed':len(role_pods),'model_iterations_observed':bool(steps),
            'latest_epoch_iter':max(steps) if steps else None,
            'optimizer_updates_observed':max(updates) if updates else None,
            'nonfinite_update_observed':bad_update_seen,'signals':sorted(signals),
            'experiment_success_verified':False,'billing_release_verified':False,
            'root_cause_confirmed':False,'log_scope':'bounded_redacted_recent_excerpts',
            'actions_taken':['read_only_collection'], 'training_mutations':0}


def diagnose(config,snapshot,reader=read_cli):
    cfg,service=settings(config)
    task={k:snapshot[k] for k in ('uid','namespace')}
    binding=next(b for b in service['bindings'] if b['uid']==task['uid'] and b['namespace']==task['namespace'])
    candidates=[p for p in snapshot['pods'] if p['status']!='pending' and re.search(r'-(?:master|worker)-\d+$',p['name'])]
    if snapshot['task_terminal'] or any(p['status']=='failed' for p in snapshot['pods']):
        candidates+=sorted((p for p in snapshot['pods'] if p not in candidates),key=lambda p:p['status']!='failed')
    logs={};errors=[]
    for pod in candidates[:8]:
        try:logs[pod['name']]=log_lines(reader(cfg,task,'logs',binding['cluster'],pod['name']))
        except (ValueError,OSError,subprocess.TimeoutExpired):errors.append({'pod':pod['name'],'reason':'log_query_unavailable'})
    events=None
    if snapshot['task_terminal'] or any(p['status']=='failed' for p in snapshot['pods']):
        try:events=json.loads(redact(json.dumps(reader(cfg,task,'summary-events'),ensure_ascii=False)))
        except (ValueError,OSError,subprocess.TimeoutExpired):errors.append({'reason':'summary_events_unavailable'})
    return {'schema':'sdm-read-only-task-diagnosis/v1','task':task,'observed_at':time.time(),
            'snapshot':snapshot,'analysis':analyze(snapshot,logs),'logs':logs,'platform_events':events,
            'collection_errors':errors,'evidence_kind':'live-platform-observation'}


def save_report(root,report):
    content=collector.canonical(report)
    path=Path(root)/(hashlib.sha256(content.encode()).hexdigest()+'.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():
        temp=path.with_suffix('.tmp');temp.write_text(content,encoding='utf-8');os.replace(temp,path)
    return str(path)


def enqueue(db,key,kind,message,report,token=None):
    eid=hashlib.sha256((key+'|'+kind+'|'+str(token or '')).encode()).hexdigest()
    db.execute('INSERT OR IGNORE INTO notification_outbox(id,task_key,kind,created_at,message,report) VALUES (?,?,?,?,?,?)',
               (eid,key,kind,time.time(),message,report))


def classify(db,key,report,path):
    a=report['analysis'];s=report['snapshot'];status=s['platform_status']
    if a['gpu_role_pods_running']:
        enqueue(db,key,'execution_started','任务训练容器已开始运行；是否完成实际优化器更新请核对诊断记录。',path)
    elif status=='running':
        enqueue(db,key,'platform_running','平台任务进入运行态，尚未确认GPU训练容器启动。',path)
    if a['model_iterations_observed'] or a['optimizer_updates_observed']:
        enqueue(db,key,'training_progress_observed','任务已出现模型训练迭代或实际优化器更新证据。',path)
    if status in collector.TERMINAL:
        message=('任务平台报告成功，仍需验收实际轮数、指标和checkpoint。' if status=='succeeded'
                 else '任务已进入终态：'+status+'。已只读取证，没有停止或重提任务。')
        enqueue(db,key,'terminal_'+status,message,path)
    elif any(p['status']=='failed' for p in s['pods']):
        enqueue(db,key,'pod_failure','任务关联Pod出现失败，平台任务未确认终态；已保存只读诊断。',path)
    for signal in a['signals']:
        if signal!='nonfinite_text_requires_update_context':
            enqueue(db,key,'signal_'+signal,'任务日志出现异常线索：'+signal+'，待结合原始上下文确认原因；未操作训练。',path)


def consume(config,reader=read_cli):
    cfg,service=settings(config)
    inbox=collector.inbox(config,consumer='resident-diagnostics',limit=100)
    if inbox['evidence_kind']!='live-platform-observation':raise ValueError('live_service_store_required')
    db=connect(cfg)
    try:
        for event in inbox['events']:
            key=event['task_key'];payload=event['payload']
            if event['kind'] in {'collection_unavailable','collection_recovered'}:
                report={'event':event,'evidence_kind':'live-platform-observation','training_mutations':0}
            else:
                report=diagnose(config,payload['snapshot'],reader)
            path=save_report(service['evidence_root'],report)
            with db:
                if event['kind'] in {'collection_unavailable','collection_recovered'}:
                    enqueue(db,key,event['kind'],'任务只读采集'+('连续失败，当前训练状态未知。' if event['kind']=='collection_unavailable' else '已恢复。'),path,event['event_id'])
                else:
                    classify(db,key,report,path)
                    db.execute('INSERT OR REPLACE INTO diagnostic_latest VALUES (?,?,?)',(key,path,time.time()))
                db.execute('INSERT OR IGNORE INTO diagnostic_receipts VALUES (?,?,?)',(event['event_id'],path,time.time()))
                db.execute('INSERT OR IGNORE INTO acknowledgements VALUES (?,?,?)',('resident-diagnostics',event['event_id'],time.time()))
        # Logs may start changing without any task/Pod state transition.
        fresh=collector.inbox(config,consumer='resident-diagnostics')['observations']
        for state in fresh:
            if state['errors'] or state['stale']:continue
            row=db.execute('SELECT snapshot FROM observations WHERE task_key=?',(state['task_key'],)).fetchone()
            snap=json.loads(row[0]);latest=db.execute('SELECT checked_at FROM diagnostic_latest WHERE task_key=?',(state['task_key'],)).fetchone()
            if latest and time.time()-latest[0]<cfg.get('interval_seconds',60):continue
            if not any(p['status']=='running' and re.search(r'-(?:master|worker)-\d+$',p['name']) for p in snap['pods']):continue
            report=diagnose(config,snap,reader);path=save_report(service['evidence_root'],report)
            with db:
                classify(db,state['task_key'],report,path)
                db.execute('INSERT OR REPLACE INTO diagnostic_latest VALUES (?,?,?)',(state['task_key'],path,time.time()))
        with db:db.execute('INSERT OR REPLACE INTO service_health VALUES (1,?,?,?)',(os.getpid(),time.time(),'healthy'))
        return {'status':'consumed','events':len(inbox['events']),'training_mutations':0}
    finally:db.close()


def delivery_binding(config):
    """Bind a receipt to the exact local project configuration and event store."""
    return hashlib.sha256(collector.canonical(config).encode()).hexdigest()


def validate_delivery(config, db, ack):
    if (not isinstance(ack,dict) or not isinstance(ack.get('ids'),list)
        or not 1 <= len(ack['ids']) <= 100 or any(not isinstance(x,str) for x in ack['ids'])
        or len(set(ack['ids'])) != len(ack['ids'])):
        raise ValueError('unique_bounded_delivery_ids_required')
    path=Path(ack.get('delivery_receipt',''))
    if not path.is_absolute() or path.resolve()!=path or not path.is_file() or path.stat().st_size>1024*1024:
        raise ValueError('existing_absolute_delivery_receipt_required')
    raw=path.read_bytes();value=json.loads(raw)
    if (not isinstance(value,dict) or value.get('schema')!='sdm-notification-delivery/v1'
        or value.get('project_binding')!=delivery_binding(config)
        or value.get('store')!=str(Path(config['task_event_collector']['store']).resolve())
        or value.get('ids')!=ack['ids'] or not isinstance(value.get('text'),str) or not value['text'].strip()
        or value.get('delivered') is not True):
        raise ValueError('bound_delivery_receipt_required')
    kind=db.execute("SELECT value FROM metadata WHERE key='evidence_kind'").fetchone()
    if not kind or value.get('evidence_kind')!=kind[0]:raise ValueError('delivery_evidence_kind_mismatch')
    try:
        at=datetime.fromisoformat(value['utc_time'].replace('Z','+00:00'))
        if at.tzinfo is None or at.utcoffset().total_seconds()!=0:raise ValueError()
        stamp=at.timestamp()
    except (ValueError,KeyError,TypeError,AttributeError):raise ValueError('delivery_utc_time_required') from None
    if stamp>time.time()+5:raise ValueError('future_delivery_receipt')
    content_digest=hashlib.sha256(raw).hexdigest()
    for eid in ack['ids']:
        row=db.execute('SELECT created_at FROM notification_outbox WHERE id=?',(eid,)).fetchone()
        if not row:raise ValueError('unknown_notification')
        if stamp<row['created_at']:raise ValueError('delivery_before_notification')
        prior=db.execute('SELECT digest FROM delivery_integrity WHERE id=?',(eid,)).fetchone()
        if prior and prior[0]!=content_digest:raise ValueError('delivery_receipt_changed')
    return str(path),content_digest


def notifications(config,ack=None,received_dispatch=None):
    cfg,_=settings(config);db=connect(cfg)
    try:
        received_state=None
        if received_dispatch is not None:
            received_state=wakeup.received(config,db,received_dispatch)
        if ack is not None:
            db.execute('CREATE TABLE IF NOT EXISTS delivery_integrity(id TEXT PRIMARY KEY,digest TEXT NOT NULL)')
            path,receipt_digest=validate_delivery(config,db,ack)
            with db:
                for eid in ack['ids']:
                    if not db.execute('SELECT 1 FROM notification_outbox WHERE id=?',(eid,)).fetchone():raise ValueError('unknown_notification')
                    db.execute('UPDATE notification_outbox SET delivered_at=COALESCE(delivered_at,?),delivery_receipt=COALESCE(delivery_receipt,?) WHERE id=?',
                               (time.time(),path,eid))
                    db.execute('INSERT OR IGNORE INTO delivery_integrity VALUES (?,?)',(eid,receipt_digest))
            # A bounded devspace observer may have exited after cleanup. Advance
            # an already authorized queue after delivery, without platform polling
            # or a second collector. Unknown dispatch claims remain held.
            if config.get('devspace_execution') and config.get('task_event_wakeup',{}).get('enabled'):
                wakeup.dispatch(config,db)
        rows=[dict(row) for row in db.execute('SELECT * FROM notification_outbox WHERE delivered_at IS NULL ORDER BY created_at,id LIMIT 101')]
        health=db.execute('SELECT * FROM service_health WHERE id=1').fetchone()
        value=dict(health) if health else {'status':'not_started'}
        value['stale']=not health or time.time()-health['updated_at']>max(300,cfg.get('interval_seconds',60)*3+cfg.get('query_timeout_seconds',30)*20)
        wake_status=wakeup.status(db) if config.get('task_event_wakeup') else {'enabled':False}
        kind=db.execute("SELECT value FROM metadata WHERE key='evidence_kind'").fetchone()
        return {'wakeup':wake_status,'notifications':rows[:100],'has_more':len(rows)>100,'service_health':value,
                'consumer_action':('silent-duplicate' if received_state=='delivered' and not rows else 'process' if rows else 'idle'),
                'receipt_contract':{'schema':'sdm-notification-delivery/v1','project_binding':delivery_binding(config),
                    'store':str(Path(cfg['store']).resolve()),'evidence_kind':kind[0] if kind else None,
                    'required_fields':['ids','utc_time','text','delivered']},
                'training_mutations':0,'delivery':'at-least-once-explicit-receipt'}
    finally:db.close()


def run(config,once=False,allow_live=False):
    cfg,_=settings(config)
    if not allow_live:raise ValueError('explicit_allow_live_read_required')
    wakeup.settings(config)
    with collector.collector_lock(str(cfg['store'])+'.service'):
        while not Path(str(cfg['store'])+'.stop').exists():
            collector.poll(config,allow_live=True,once=True)
            consume(config)
            db=connect(cfg)
            try:wakeup.dispatch(config,db)
            finally:db.close()
            if once:return {'status':'cycle_complete','training_mutations':0}
            for _ in range(cfg.get('interval_seconds',60)):
                if Path(str(cfg['store'])+'.stop').exists():break
                time.sleep(1)
        return {'status':'service_stopped','training_mutations':0}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',type=Path,required=True)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--run',action='store_true');mode.add_argument('--once',action='store_true')
    mode.add_argument('--notifications',action='store_true')
    p.add_argument('--allow-live-read',action='store_true');p.add_argument('--ack-notifications',type=Path)
    a=p.parse_args();config=json.loads(a.project.read_bytes())
    if a.ack_notifications and not a.notifications:p.error('ack requires notifications')
    result=notifications(config,json.loads(a.ack_notifications.read_bytes()) if a.ack_notifications else None) if a.notifications else run(config,a.once,a.allow_live_read)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    from process_visibility import ensure_background_stdio
    ensure_background_stdio()
    main()
