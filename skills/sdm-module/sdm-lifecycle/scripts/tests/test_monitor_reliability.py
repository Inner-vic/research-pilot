"""Read-failure freshness and bounded polling regression; no platform calls."""
from unittest.mock import patch
import task_event_collector as c
from test_task_event_collector import TASK,record


def cfg(tmp_path):return {'task_event_collector':{'version':1,'store':str(tmp_path/'events.sqlite3'),'tasks':[TASK]}}


def failures():return [{'task':TASK,'error':True,'observed_at':t} for t in (500,501,502)]


def test_recent_failed_reads_cannot_refresh_old_observations(tmp_path):
    config=cfg(tmp_path);c.replay(config,[record(at=100),*failures()])
    with patch.object(c.time,'time',return_value=503):
        state=c.inbox(config)['observations'][0]
    assert state['stale'] and state['observation_age_seconds']==403
    assert state['collection_attempt_age_seconds']==1 and not state['collection_available']
    assert state['retry_after_seconds']==119
    # A read outage does not create any failed-training observation.
    events=c.inbox(config)['events']
    assert [e['kind'] for e in events]==['task_state_observed','collection_unavailable']
    assert events[0]['payload']['snapshot']['task_terminal'] is False


def test_no_success_is_explicitly_unknown_and_stale(tmp_path):
    config=cfg(tmp_path);c.replay(config,failures())
    with patch.object(c.time,'time',return_value=503):state=c.inbox(config)['observations'][0]
    assert state['observation_age_seconds'] is None and state['stale']


def test_backoff_skips_queries_then_recovers_same_task_once(tmp_path):
    config=cfg(tmp_path)
    # Exercise the read poller with mocked transport, never mix replay/live DBs.
    db=c.connect(config['task_event_collector'],'live-platform-observation')
    c.observe(db,TASK,record()['native'],[],now=100)
    for row in failures():c.observe(db,TASK,error=True,now=row['observed_at'])
    db.close()
    with patch.object(c.time,'time',return_value=503),patch.object(c,'query',side_effect=AssertionError('queried during backoff')):
        assert c.poll(config,allow_live=True)['cycles']==1
    with patch.object(c.time,'time',return_value=623),patch.object(c,'query',side_effect=[record()['native'],[]]) as query:
        c.poll(config,allow_live=True)
        state=c.inbox(config)['observations'][0]
    assert query.call_count==2 and not state['stale'] and state['collection_available']
    assert state['errors']==0 and state['retry_after_seconds']==0
    assert [e['kind'] for e in c.inbox(config)['events']]==['task_state_observed','collection_unavailable','collection_recovered']


def test_retry_budget_is_bounded_and_other_tasks_are_not_blocked():
    settings={'interval_seconds':60,'failure_backoff_max_seconds':300}
    assert [c.retry_delay(settings,n) for n in (0,2,3,4,5,100000)]==[0,0,120,240,300,300]
