"""Read retained Study evidence without executing, polling or acknowledging work."""
import json
import math
from pathlib import Path
import sqlite3

from task_pipeline import digest


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def duration(start, end):
    start, end = number(start), number(end)
    return end - start if start is not None and end is not None and end >= start else None


def read(path):
    if path.is_symlink():
        raise ValueError('accounting_symlink_evidence_rejected')
    return json.loads(path.read_bytes())


def summarize(config, output):
    root = Path(output).absolute()
    # Historical evidence needs no new resource authorization, but must belong to
    # the exact supplied project. Do not call execution/notifications/connect().
    if digest(read(root / 'study-config.json')) != digest(config):
        raise ValueError('accounting_project_binding_mismatch')
    if root.is_symlink():
        raise ValueError('accounting_symlink_evidence_rejected')
    rounds = []; reports = set(); issues = []
    folders = sorted(root.glob('round-*'))
    folders += sorted(root.glob('round-*/attempt-*'))
    for folder in folders:
        if folder.is_symlink():
            raise ValueError('accounting_symlink_evidence_rejected')
        if not (folder / 'execution.json').is_file():
            continue
        state = read(folder / 'execution.json')
        events = []
        for path in sorted((folder / 'events').glob('*.json')):
            event = read(path)
            if event.get('binding') != state.get('binding'):
                raise ValueError('accounting_event_binding_mismatch')
            events.append(event); reports.add(str(path.resolve()))
        def at(kind):
            values = [number(e.get('observed_at')) for e in events if e.get('kind') == kind]
            values = [v for v in values if v is not None]
            return min(values) if values else None
        process = state.get('process', {})
        start, finish = process.get('started_at'), process.get('finished_at')
        archived = at('review_completed')
        created = number(state.get('created_at'))
        review = state.get('review', {})
        timings = review.get('timings', {})
        rounds.append({'attempt_path': str(folder), 'binding': state.get('binding'),
            'status': state['status'], 'created_at': created,
            'process_started_at': number(start), 'process_finished_at': number(finish),
            'archived_at': archived,
            'prelaunch_seconds': duration(created, start),
            'compute_seconds': duration(start, finish),
            'postcompute_to_archive_seconds': duration(finish, archived),
            'review_engine_seconds': number(review.get('elapsed_seconds')),
            'material_engine_seconds': {k: number(v) for k, v in timings.items() if k.endswith('_seconds')},
            'terminal_detection_seconds': duration(finish, at('process_terminal')),
            'stop_verified': state.get('stop_verified') is True,
            'artifacts_collected': state.get('artifacts_collected') is True,
            'evidence_kind': state.get('evidence_kind'),
            'exit_code': process.get('exit_code'),
            'events_observed': len(events)})
    delivery = None; wakeups = []
    store = config.get('task_event_collector', {}).get('store')
    if store:
        path = Path(store)
        if path.is_symlink(): raise ValueError('accounting_symlink_evidence_rejected')
        if path.is_file():
            try:
                # Read-only URI: missing schema is an unknown, never initialize it.
                with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
                    db.row_factory = sqlite3.Row
                    rows = [dict(r) for r in db.execute('SELECT id,report,delivered_at FROM notification_outbox')
                            if str(Path(r['report']).resolve()) in reports]
                    delivery = {'observed_outbox_entries': len(rows),
                                'delivered_entries': sum(r['delivered_at'] is not None for r in rows),
                                'unread_entries': sum(r['delivered_at'] is None for r in rows)}
                    ids = {r['id'] for r in rows}
                    members = list(db.execute('SELECT dispatch_id,notification_id FROM wakeup_members'))
                    dispatches = {r['dispatch_id'] for r in members if r['notification_id'] in ids}
                    for row in db.execute('SELECT * FROM wakeup_timings'):
                        row = dict(row)
                        if row['dispatch_id'] in dispatches:
                            wakeups.append({'dispatch_id': row['dispatch_id'],
                                'queue_transport_seconds': duration(row.get('queue_started_at'), row.get('queue_accepted_at')),
                                'queue_to_consumer_seconds': duration(row.get('queue_accepted_at'), row.get('consumer_received_at'))})
            except sqlite3.Error as error:
                issues.append('notification_accounting_unavailable:' + type(error).__name__)
        else:
            issues.append('notification_store_missing')
    starts = [r['created_at'] for r in rounds if r['created_at'] is not None]
    ends = [r['archived_at'] for r in rounds if r['archived_at'] is not None]
    state = read(root / 'execution.json')
    all_archived = bool(rounds) and all(r['archived_at'] is not None for r in rounds)
    compute = [r['compute_seconds'] for r in rounds]
    return {'schema': 'sdm-study-accounting/v1', 'status': state['status'],
        'study_digest': state.get('study_digest'), 'scope': 'execution-through-review-archive',
        'wall_seconds': duration(min(starts), max(ends)) if starts and ends and all_archived else None,
        'compute_seconds': sum(compute) if compute and all(v is not None for v in compute) else None,
        'attempts_observed': len(rounds), 'rounds': rounds, 'notifications': delivery, 'wakeups': wakeups,
        'agent_operations': None, 'human_interventions': None, 'human_active_seconds': None,
        'installation_seconds': None, 'missed_notifications': None, 'duplicate_computations': None,
        'speedup_vs_original_sdm': None, 'issues': issues,
        'qualification': 'Retained timestamps only. Prelaunch includes scheduling and native UI; engine timings exclude Agent thinking. No timestamp from file mtime. Unknown counters are not zero; no matched old-SDM baseline or speedup claim. Outbox delivery does not prove no unobserved platform event.',
        'platform_calls': 0, 'resource_mutations': 0, 'acknowledgements': 0}
