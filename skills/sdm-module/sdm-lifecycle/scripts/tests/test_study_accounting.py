"""Accounting contracts with retained CPU fixtures; no platform or queue transport."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from study_accounting import summarize


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def fixture(tmp_path):
    root = tmp_path / 'attempt'
    config = {'devspace_study': {'study_id': 'cpu-accounting'}}
    write(root / 'study-config.json', config)
    write(root / 'execution.json', {'status': 'completed', 'study_digest': 'fixture'})
    for rn, created in [(1, 100), (2, 150)]:
        folder = root / f'round-{rn:02d}'
        write(folder / 'execution.json', {'binding': str(rn), 'status': 'completed',
            'created_at': created, 'process': {'started_at': created + 10, 'finished_at': created + 20, 'exit_code': 0},
            'stop_verified': True, 'artifacts_collected': True, 'evidence_kind': 'cpu-simulation',
            'review': {'elapsed_seconds': .02, 'timings': {'preparation_seconds': .01}}})
        for name, t in [('process_terminal', created + 22), ('review_completed', created + 30)]:
            write(folder / 'events' / (name + '.json'), {'binding': str(rn), 'kind': name, 'observed_at': t})
    return config, root


def test_complete_accounting_exposes_bounds_and_unknowns_without_writes(tmp_path):
    config, root = fixture(tmp_path)
    before = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    result = summarize(config, root)
    assert result['wall_seconds'] == 80 and result['compute_seconds'] == 20
    assert result['rounds'][0]['terminal_detection_seconds'] == 2
    assert result['human_interventions'] is None and result['duplicate_computations'] is None
    assert result['speedup_vs_original_sdm'] is None and result['platform_calls'] == 0
    assert before == {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_incomplete_time_never_substitutes_mtime_or_negative_duration(tmp_path):
    config, root = fixture(tmp_path)
    (root / 'round-02/events/review_completed.json').unlink()
    path = root / 'round-02/execution.json'
    state = json.loads(path.read_bytes()); state['process']['finished_at'] = 1
    write(path, state)
    report = summarize(config, root)
    assert report['wall_seconds'] is None and report['compute_seconds'] is None


def test_project_and_event_binding_mismatches_are_rejected(tmp_path):
    config, root = fixture(tmp_path)
    with pytest.raises(ValueError, match='project_binding'):
        summarize({'devspace_study': {'study_id': 'another'}}, root)
    write(root / 'round-01/events/foreign.json', {'binding': 'other', 'observed_at': 1})
    with pytest.raises(ValueError, match='event_binding'):
        summarize(config, root)


def test_read_only_database_filters_other_projects_and_missing_schema(tmp_path):
    config, root = fixture(tmp_path)
    store = tmp_path / 'events.sqlite3'
    config['task_event_collector'] = {'store': str(store)}
    write(root / 'study-config.json', config)
    with sqlite3.connect(store) as db:
        db.executescript('CREATE TABLE notification_outbox(id TEXT,report TEXT,delivered_at REAL);'
            'CREATE TABLE wakeup_members(dispatch_id TEXT,notification_id TEXT);'
            'CREATE TABLE wakeup_timings(dispatch_id TEXT,queue_started_at REAL,queue_accepted_at REAL,consumer_received_at REAL);')
        db.executemany('INSERT INTO notification_outbox VALUES(?,?,?)', [
            ('ours', str((root / 'round-01/events/process_terminal.json').resolve()), 140),
            ('foreign', '/another-project/events/event.json', None)])
        db.execute("INSERT INTO wakeup_members VALUES('dispatch','ours')")
        db.execute("INSERT INTO wakeup_timings VALUES('dispatch',122,123,125)")
    before = store.read_bytes(); result = summarize(config, root)
    assert result['notifications'] == {'observed_outbox_entries': 1, 'delivered_entries': 1, 'unread_entries': 0}
    assert result['wakeups'][0]['queue_to_consumer_seconds'] == 2
    assert store.read_bytes() == before
    store.unlink(); sqlite3.connect(store).close()
    assert summarize(config, root)['issues'] == ['notification_accounting_unavailable:OperationalError']


def test_public_route_requires_exact_read_only_workflow(tmp_path):
    config, root = fixture(tmp_path); project = tmp_path / 'project.json'; write(project, config)
    entry = Path(__file__).parents[1] / 'run_project_pipeline.py'
    argv = [sys.executable, '-B', str(entry), '--project', str(project), '--output', str(root), '--study-accounting']
    good = subprocess.run(argv, capture_output=True, text=True)
    assert good.returncode == 0, good.stderr
    assert json.loads(good.stdout)['attempts_observed'] == 2
    bad = subprocess.run(argv + ['--allow-devspace-live'], capture_output=True, text=True)
    assert bad.returncode != 0 and 'cannot execute' in bad.stderr
