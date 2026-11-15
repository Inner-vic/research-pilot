"""CPU-only event transport acceptance; no platform calls or GPU execution."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SCRIPTS))
import task_event_collector as c

TASK={'uid':'00000000-0000-4000-8000-000000000001','namespace':'vc-test'}


def record(state='pending', pods=None, at=1):
    return {'task':TASK,'native':{**TASK,'status':state,'cmd':'NEVER_STORE_SECRET'},
            'pods':[] if pods is None else pods,'observed_at':at}


def pod(state='Pending', name='master'):
    return {'name':name,'namespace':TASK['namespace'],'task_uid':TASK['uid'],
            'step':'task','status':state,'exit_code':None}


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.cfg={'task_event_collector':{'version':1,'store':str(self.root/'events.sqlite3'),
                                        'tasks':[dict(TASK)],'error_threshold':2}}
    def tearDown(self): self.temp.cleanup()
    def events(self): return c.inbox(self.cfg)['events']

    def test_unchanged_state_deduplicated_across_restarts(self):
        for at in (1,2,3): c.replay(self.cfg,[record(at=at)])
        self.assertEqual(len(self.events()),1)
        self.assertEqual(c.inbox(self.cfg)['observations'][0]['last_success'],3)

    def test_pod_order_does_not_create_event_but_transition_does(self):
        pods=[pod(name='master'),pod(name='worker')]
        c.replay(self.cfg,[record(pods=pods),record(pods=list(reversed(pods)),at=2),
                           record('running',[pod('Running')],3)])
        self.assertEqual(len(self.events()),2)
        snapshot=self.events()[-1]['payload']['snapshot']
        self.assertFalse(snapshot['training_started_verified'])

    def test_terminal_is_not_experiment_success_or_billing_proof(self):
        c.replay(self.cfg,[record('succeeded')])
        e=self.events()[0]
        self.assertEqual(e['kind'],'task_terminal_observed')
        for field in ('experiment_success_verified','compute_pods_deleted','billing_release_verified'):
            self.assertFalse(e['payload']['snapshot'][field])

    def test_deleted_compute_after_terminal_emits_release_observation(self):
        c.replay(self.cfg,[record('failed',[pod('Failed')]),record('failed',[pod('deleted')],2)])
        self.assertEqual(len(self.events()),2)
        self.assertTrue(self.events()[-1]['payload']['snapshot']['compute_pods_deleted'])
        self.assertFalse(self.events()[-1]['payload']['snapshot']['billing_release_verified'])

    def test_query_failure_retains_last_known_state_and_recovers(self):
        c.replay(self.cfg,[record(),*[{'task':TASK,'error':True,'observed_at':at} for at in (2,3,4)],record(at=5)])
        self.assertEqual([x['kind'] for x in self.events()],
                         ['task_state_observed','collection_unavailable','collection_recovered'])
        self.assertEqual(self.events()[1]['payload']['task_status'],'unknown')

    def test_ack_explicit_idempotent_and_consumer_specific(self):
        c.replay(self.cfg,[record()]); eid=self.events()[0]['event_id']
        self.assertEqual(self.events()[0]['event_id'],eid)
        for _ in range(2): self.assertEqual(c.inbox(self.cfg,ack_ids=[eid])['events'],[])
        self.assertEqual(len(c.inbox(self.cfg,consumer='other')['events']),1)

    def test_bad_ack_rolls_back_entire_batch(self):
        c.replay(self.cfg,[record()]); eid=self.events()[0]['event_id']
        with self.assertRaisesRegex(ValueError,'unknown_event_ack'):
            c.inbox(self.cfg,ack_ids=[eid,'unknown'])
        self.assertEqual(len(self.events()),1)

    def test_page_does_not_ack_unread_events(self):
        c.replay(self.cfg,[record(),record('running',at=2),record('failed',at=3)])
        page=c.inbox(self.cfg,limit=2); self.assertTrue(page['has_more'])
        page=c.inbox(self.cfg,limit=2,ack_ids=[x['event_id'] for x in page['events']])
        self.assertEqual(len(page['events']),1); self.assertFalse(page['has_more'])

    def test_simulation_and_live_store_cannot_mix(self):
        c.replay(self.cfg,[record()])
        with self.assertRaisesRegex(ValueError,'stores_must_be_separate'):
            c.connect(c.validate(self.cfg),'live-platform-observation')

    def test_no_credentials_in_database(self):
        c.replay(self.cfg,[record()])
        db=c.connect(c.validate(self.cfg))
        try: dump='\n'.join(db.iterdump())
        finally: db.close()
        self.assertNotIn('NEVER_STORE_SECRET',dump)

    def test_identity_unknown_status_and_incomplete_pods_rejected(self):
        for native,pods in [({**TASK,'uid':'different','status':'running'},[]),
                            ({**TASK,'status':'mystery'},[]),({**TASK,'status':'running'},{}),
                            ({**TASK,'status':'running'},[{**pod(),'task_uid':'wrong'}])]:
            with self.subTest(native=native,pods=pods),self.assertRaises(ValueError):
                c.normalize(TASK,native,pods)

    def test_no_live_poll_without_explicit_permission(self):
        with patch.object(c,'query',side_effect=AssertionError('network_called')):
            with self.assertRaisesRegex(ValueError,'explicit_allow_live'):
                c.poll(self.cfg)

    def test_transaction_failure_does_not_commit_event_without_state(self):
        c.replay(self.cfg,[record()])
        db=c.connect(c.validate(self.cfg),'cpu-simulation')
        try:
            db.execute("CREATE TRIGGER reject_observation BEFORE INSERT ON observations BEGIN SELECT RAISE(ABORT,'fault'); END")
            with self.assertRaises(Exception): c.observe(db,TASK,record('running')['native'],[],now=2)
        finally: db.close()
        self.assertEqual(len(self.events()),1)
        self.assertEqual(c.inbox(self.cfg)['observations'][0]['last_success'],1)

    def test_second_collector_lock_rejected_and_released(self):
        with c.collector_lock(self.cfg['task_event_collector']['store']):
            script='from task_event_collector import collector_lock; import sys\nwith collector_lock(sys.argv[1]): pass'
            p=subprocess.run([sys.executable,'-c',script,self.cfg['task_event_collector']['store']],
                             cwd=SCRIPTS,capture_output=True,text=True)
            self.assertNotEqual(p.returncode,0); self.assertIn('collector_already_running',p.stderr)
        with c.collector_lock(self.cfg['task_event_collector']['store']): pass

    def test_official_cli_argv_read_only_and_redacted_errors(self):
        binary=self.root/'lpai-agent.exe'; binary.touch()
        cfg={**c.validate(self.cfg),'cli_path':str(binary)}
        with patch.object(c.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps({'status':'ok','data':{}}),'')) as run:
            c.query(cfg,TASK,'get')
            self.assertEqual(run.call_args.args[0][1:3],['tasks','get'])
            self.assertEqual(run.call_args.kwargs['creationflags'],getattr(subprocess,'CREATE_NO_WINDOW',0))
        with patch.object(c.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','SECRET')):
            with self.assertRaisesRegex(ValueError,'^official_cli_query_failed$'): c.query(cfg,TASK,'pods')
        with self.assertRaises(ValueError): c.query(cfg,TASK,'stop')

    def test_public_coordinator_inbox_and_ack_do_not_dispatch(self):
        c.replay(self.cfg,[record(),record('failed',at=2)])
        project=self.root/'project.json'; project.write_text(json.dumps(self.cfg))
        command=[sys.executable,str(SCRIPTS/'run_project_pipeline.py'),'--project',str(project),'--task-events']
        p=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        result=json.loads(p.stdout); self.assertFalse(result['automatic_execution'])
        self.assertEqual(result['evidence_kind'],'cpu-simulation')
        ack=self.root/'ack.json'; ack.write_text(json.dumps([e['event_id'] for e in result['events']]))
        p=subprocess.run(command+['--ack-task-events',str(ack)],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr); self.assertEqual(json.loads(p.stdout)['events'],[])

    def test_public_entry_rejects_training_mode_with_inbox(self):
        project=self.root/'project.json'; project.write_text(json.dumps(self.cfg))
        p=subprocess.run([sys.executable,str(SCRIPTS/'run_project_pipeline.py'),'--project',str(project),
                          '--task-events','--bind-roadmodel-native'],capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0); self.assertIn('choose exactly one',p.stderr)

    def test_live_interface_mock_checks_once_without_external_calls(self):
        # Deliberately separate mocked transport evidence from live acceptance.
        with patch.object(c,'query',side_effect=[record()['native'],[]]):
            result=c.poll(self.cfg,allow_live=True)
        self.assertEqual(result['cycles'],1)
        self.assertEqual(result['new_training_submissions'],0)

    def test_stop_marker_prevents_queries(self):
        Path(self.cfg['task_event_collector']['store']+'.stop').touch()
        with patch.object(c,'query',side_effect=AssertionError('network_called')):
            self.assertEqual(c.poll(self.cfg,allow_live=True)['cycles'],0)

    def test_task_failure_visible_even_when_pod_query_fails(self):
        with patch.object(c,'query',side_effect=[record('failed')['native'],ValueError('pod_failure')]):
            c.poll(self.cfg,allow_live=True)
        event=self.events()[0]
        self.assertEqual(event['kind'],'task_terminal_observed')
        self.assertEqual(event['payload']['snapshot']['pod_collection'],'unavailable')
        self.assertFalse(event['payload']['snapshot']['compute_pods_deleted'])

    def test_unknown_live_task_counts_as_collection_failure(self):
        for _ in range(2):
            with patch.object(c,'query',return_value=record('unsupported')['native']):
                c.poll(self.cfg,allow_live=True)
        self.assertEqual([e['kind'] for e in self.events()],['collection_unavailable'])

    def test_staleness_is_explicit_when_collector_no_longer_runs(self):
        c.replay(self.cfg,[record()])
        self.assertTrue(c.inbox(self.cfg)['observations'][0]['stale'])

    def test_backward_clock_rejected_without_duplicate_event(self):
        c.replay(self.cfg,[record(at=3)])
        with self.assertRaisesRegex(ValueError,'clock_regressed'):
            c.replay(self.cfg,[record('failed',at=2)])
        self.assertEqual(len(self.events()),1)


if __name__=='__main__': unittest.main()
