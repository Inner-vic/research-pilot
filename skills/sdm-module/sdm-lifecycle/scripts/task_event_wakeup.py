"""Opt-in Codex queue transport for the resident notification outbox.

Queue acceptance is not user delivery. Ambiguous outcomes are held for explicit
reconciliation, never automatically resent. No platform operations occur here.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid


def consumer_contract():
    path = Path(__file__).resolve().parents[1] / 'references' / 'task-event-consumer.md'
    if not path.is_file():
        raise ValueError('event_consumer_contract_missing')
    return path


def settings(config):
    cfg = config.get('task_event_wakeup')
    if cfg is None:
        return None
    if cfg.get('version') != 1 or cfg.get('enabled') is not True:
        raise ValueError('explicit_event_wakeup_v1_required')
    if cfg.get('resource_kind', 'training-task') not in {'training-task', 'devspace'}:
        raise ValueError('unsupported_wakeup_resource_kind')
    triggers = cfg.get('trigger_kinds')
    if triggers is not None and (not isinstance(triggers, list) or not triggers or len(triggers) > 64
        or any(not isinstance(k, str) or not re.fullmatch('[a-z][a-z0-9_]{0,63}', k) for k in triggers)):
        raise ValueError('explicit_valid_wakeup_trigger_kinds_required')
    if str(uuid.UUID(cfg['thread_id'])) != cfg['thread_id']:
        raise ValueError('canonical_thread_id_required')
    for key in ('cli_path', 'project_path', 'coordinator_path'):
        path = Path(cfg[key])
        if not path.is_absolute() or not path.is_file():
            raise ValueError('absolute_existing_wakeup_path_required:' + key)
    if not Path(cfg['codex_home']).is_absolute() or not Path(cfg['codex_home']).is_dir():
        raise ValueError('absolute_codex_home_required')
    if Path(cfg['cli_path']).name.lower() not in {'codex', 'codex.exe'}:
        raise ValueError('official_codex_cli_required')
    if Path(cfg['coordinator_path']).name != 'run_project_pipeline.py':
        raise ValueError('public_coordinator_required')
    if json.loads(Path(cfg['project_path']).read_bytes()) != config:
        raise ValueError('persisted_project_binding_mismatch')
    consumer_contract()  # validate packaging before claiming an external dispatch
    return cfg


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS wakeup_dispatches(
          id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, notification_ids TEXT NOT NULL,
          state TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
          queue_message_id TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS wakeup_members(
          notification_id TEXT PRIMARY KEY, dispatch_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS wakeup_timings(
          dispatch_id TEXT PRIMARY KEY, queue_started_at REAL,
          queue_accepted_at REAL, consumer_received_at REAL);
    ''')


def prompt(cfg, batch, ids):
    devspace = cfg.get('resource_kind') == 'devspace'
    title = '开发空间进程' if devspace else '训练'
    contract = consumer_contract()
    binding = json.dumps({'dispatch_id': batch, 'notification_ids': ids,
                          'project_path': cfg['project_path'],
                          'coordinator_path': cfg['coordinator_path']}, ensure_ascii=False)
    return ('[SDM 只读' + title + '事件通知] 后台发现变化后触发。绑定：' + binding + '\n'
            '先用绑定公开入口 --project PROJECT --task-notifications --received-task-dispatch ' + batch + ' 记录接收。consumer_action=silent-duplicate时直接静默结束，不发检查进度或重复确认。否则读取处理合同：' + str(contract) + '及报告。'
            '核对原会话授权，判断是否终止；有错误定位并修复，有结果分析并制定下一步，继续已授权工作。'
            'Study逐轮简短展示实际结果、分析依据、修改方案、下一轮实际参数；次轮完成后展示实际比较。启动后台观察后立即交还回合，不在前台等待终态。详细证据放归档。'
            '控制器退出或启动受阻不代表计算已执行。此事件通知不授予新权限。'
            '先实际通知，再按receipt_contract保存完整回执并确认；未知项如实保留。')


def queue(cfg, message):
    # Do not depend on transient tool-call pipe/session environment.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('CODEX_') or k == 'CODEX_HOME'}
    env['CODEX_HOME'] = cfg['codex_home']
    return subprocess.run([cfg['cli_path'], 'queue', '--thread', cfg['thread_id'],
                           '--message', message], env=env, capture_output=True,
                          text=True, encoding='utf-8', errors='replace', timeout=30,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def dispatch(config, db, sender=queue):
    cfg = settings(config)
    if cfg is None:
        return {'status': 'disabled'}
    kind = db.execute("SELECT value FROM metadata WHERE key='evidence_kind'").fetchone()
    if not kind or kind[0] != 'live-platform-observation':
        raise ValueError('live_service_store_required')
    initialize(db)
    # Serialize claims before any external call. A crash after this commit leaves
    # an unresolved claim instead of causing an unsafe automatic retry.
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute("UPDATE wakeup_dispatches SET state='delivered',updated_at=? "
                   "WHERE state!='delivered' AND NOT EXISTS (SELECT 1 FROM wakeup_members m "
                   "JOIN notification_outbox n ON n.id=m.notification_id "
                   "WHERE m.dispatch_id=wakeup_dispatches.id AND n.delivered_at IS NULL)", (time.time(),))
        active = db.execute("SELECT id,state FROM wakeup_dispatches WHERE state!='delivered' LIMIT 1").fetchone()
        if active:
            db.commit()
            return {'status': 'awaiting_delivery_or_reconciliation', 'dispatch_id': active['id'],
                    'state': active['state']}
        trigger_sql = ''
        parameters = []
        if cfg.get('trigger_kinds'):
            parameters = cfg['trigger_kinds']
            trigger_sql = ' AND n.kind IN (' + ','.join('?' for _ in parameters) + ')'
        rows = db.execute('SELECT n.id FROM notification_outbox n LEFT JOIN wakeup_members m '
                          'ON m.notification_id=n.id WHERE n.delivered_at IS NULL AND '
                          'm.notification_id IS NULL' + trigger_sql + ' ORDER BY n.created_at,n.id LIMIT 100', parameters).fetchall()
        ids = [r['id'] for r in rows]
        if not ids:
            db.commit()
            return {'status': 'no_new_notifications'}
        if any(not re.fullmatch('[0-9a-f]{64}', eid) for eid in ids):
            raise ValueError('invalid_notification_identity')
        # Notification IDs are scoped to a store. Independent project outboxes
        # can contain equal IDs; their dispatch identities must remain distinct.
        batch = hashlib.sha256(json.dumps({
            'thread_id': cfg['thread_id'],
            'project_path': str(Path(cfg['project_path']).resolve()),
            'notification_ids': ids}, sort_keys=True).encode()).hexdigest()
        now = time.time()
        db.execute('INSERT INTO wakeup_dispatches VALUES (?,?,?,?,?,?,?,?)',
                   (batch, cfg['thread_id'], json.dumps(ids), 'dispatching', now, now, None, None))
        db.executemany('INSERT INTO wakeup_members VALUES (?,?)', [(eid, batch) for eid in ids])
        db.commit()
    except BaseException:
        db.rollback()
        raise
    state, message_id, error = 'unknown', None, None
    with db:
        db.execute('INSERT INTO wakeup_timings(dispatch_id,queue_started_at) VALUES (?,?)', (batch,time.time()))
    try:
        result = sender(cfg, prompt(cfg, batch, ids))
        match = re.fullmatch(r'Queued message ([0-9a-f-]{36}) for thread '
                             + re.escape(cfg['thread_id']) + r'\.\s*', result.stdout)
        if result.returncode == 0 and match:
            message_id = str(uuid.UUID(match[1]))
            state = 'queued'
            with db:
                db.execute('UPDATE wakeup_timings SET queue_accepted_at=? WHERE dispatch_id=?', (time.time(),batch))
        else:
            error = 'queue_result_not_confirmed'
    except (OSError, subprocess.TimeoutExpired, ValueError):
        error = 'queue_transport_outcome_unconfirmed'
    with db:
        db.execute('UPDATE wakeup_dispatches SET state=?,updated_at=?,queue_message_id=?,error=? WHERE id=?',
                   (state, time.time(), message_id, error, batch))
    return {'status': state, 'dispatch_id': batch, 'queue_message_id': message_id}


def received(config, db, dispatch_id):
    """First consumer entry is distinct from transport acceptance and delivery ack."""
    cfg = settings(config)
    if cfg is None or not re.fullmatch('[0-9a-f]{64}', dispatch_id):
        raise ValueError('bound_wakeup_dispatch_required')
    initialize(db)
    row = db.execute('SELECT thread_id,state FROM wakeup_dispatches WHERE id=?',(dispatch_id,)).fetchone()
    if not row or row['thread_id'] != cfg['thread_id'] or row['state'] not in {'queued','delivered'}:
        raise ValueError('confirmed_bound_wakeup_dispatch_required')
    pending=db.execute('SELECT 1 FROM wakeup_members m JOIN notification_outbox n ON n.id=m.notification_id '
                       'WHERE m.dispatch_id=? AND n.delivered_at IS NULL LIMIT 1',(dispatch_id,)).fetchone()
    with db:
        db.execute('INSERT OR IGNORE INTO wakeup_timings(dispatch_id) VALUES (?)',(dispatch_id,))
        db.execute('UPDATE wakeup_timings SET consumer_received_at=COALESCE(consumer_received_at,?) WHERE dispatch_id=?',
                   (time.time(),dispatch_id))
        if pending is None:
            db.execute("UPDATE wakeup_dispatches SET state='delivered',updated_at=? WHERE id=?",(time.time(),dispatch_id))
    return 'delivered' if pending is None else row['state']


def terminal_consumer(config, execution_binding):
    """Local proof of a received, queued terminal event for this exact attempt."""
    import task_event_service as service
    cfg,_ = service.settings(config)
    db = service.connect(cfg)
    try:
        initialize(db)
        rows = db.execute('SELECT d.id,t.consumer_received_at,n.report FROM wakeup_dispatches d '
            'JOIN wakeup_timings t ON t.dispatch_id=d.id JOIN wakeup_members m ON m.dispatch_id=d.id '
            'JOIN notification_outbox n ON n.id=m.notification_id '
            "WHERE d.state IN ('queued','delivered') AND t.consumer_received_at IS NOT NULL "
            "AND n.kind='process_terminal' ORDER BY t.consumer_received_at").fetchall()
        for row in rows:
            report = json.loads(Path(row['report']).read_bytes())
            if report.get('binding') == execution_binding:
                return {'dispatch_id':row['id'], 'received_at':row['consumer_received_at'],
                        'report':row['report'], 'execution_binding':execution_binding}
        # A terminal notification can be delivered during another received
        # batch even when its own queue result is unknown. A later, genuinely
        # received cleanup event may continue that exact completed attempt.
        # Preserve the missing transport/consumer timestamps of the first send.
        rows = db.execute('SELECT d.id,t.consumer_received_at,n.report FROM wakeup_dispatches d '
            'JOIN wakeup_timings t ON t.dispatch_id=d.id JOIN wakeup_members m ON m.dispatch_id=d.id '
            'JOIN notification_outbox n ON n.id=m.notification_id '
            "WHERE d.state IN ('queued','delivered') AND t.consumer_received_at IS NOT NULL "
            "AND n.kind='cleanup_pending' ORDER BY t.consumer_received_at").fetchall()
        terminals = db.execute("SELECT report FROM notification_outbox WHERE kind='process_terminal'").fetchall()
        for row in rows:
            followup = json.loads(Path(row['report']).read_bytes())
            if (followup.get('binding') != execution_binding
                    or followup.get('details', {}).get('process_terminal') is not True):
                continue
            for terminal in terminals:
                report = json.loads(Path(terminal['report']).read_bytes())
                details = report.get('details', {})
                if (report.get('binding') == execution_binding
                        and report.get('uid') and report['uid'] == followup.get('uid')
                        and details.get('phase') == 'exited'
                        and type(details.get('exit_code')) is int
                        and isinstance(report.get('observed_at'), (int, float))
                        and isinstance(followup.get('observed_at'), (int, float))
                        and report['observed_at'] <= followup['observed_at']):
                    return {'dispatch_id': row['id'], 'received_at': row['consumer_received_at'],
                            'report': row['report'], 'terminal_report': terminal['report'],
                            'execution_binding': execution_binding,
                            'continuation_evidence': 'received-bound-post-terminal-cleanup'}
        return None
    finally: db.close()


def status(db):
    initialize(db)
    rows = [dict(r) for r in db.execute("SELECT id,thread_id,state,created_at,updated_at,queue_message_id,error "
                                      "FROM wakeup_dispatches WHERE state!='delivered' ORDER BY created_at LIMIT 10")]
    timings = [dict(r) for r in db.execute('SELECT d.id,d.state,t.queue_started_at,t.queue_accepted_at,t.consumer_received_at '
        'FROM wakeup_dispatches d LEFT JOIN wakeup_timings t ON t.dispatch_id=d.id ORDER BY d.created_at DESC LIMIT 10')]
    for row in timings:
        start,accepted,received_at = (row[k] for k in ('queue_started_at','queue_accepted_at','consumer_received_at'))
        row['queue_transport_seconds'] = max(0,accepted-start) if accepted is not None and start is not None else None
        row['queue_wait_seconds'] = max(0,received_at-accepted) if accepted is not None and received_at is not None else None
        row['awaiting_consumer_seconds'] = max(0,time.time()-accepted) if accepted is not None and received_at is None and row['state']!='delivered' else None
    return {'pending_dispatches': rows, 'timings':timings,
            'reconciliation_required': any(r['state'] in {'unknown', 'dispatching'} for r in rows),
            'delivery_is_separate': True, 'queue_waits_for_active_turn': True,
            'latency_qualification':'Queue acceptance, first consumer entry and actual delivery are separate; missing historical timestamps stay unknown. Do not resend an accepted message to reduce wait.'}
