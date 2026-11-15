"""CPU transport contracts; fake queue never calls Codex or a platform."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import task_event_service as s
import task_event_wakeup as w

THREAD = '00000000-0000-4000-8000-000000000001'
MESSAGE = '00000000-0000-4000-8000-000000000003'


class WakeupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        cli = self.root / 'codex.exe'
        cli.touch()
        self.project = self.root / 'project.json'
        self.config = {
            'task_event_collector': {'version': 1, 'store': str(self.root / 'events.db'),
                                     'tasks': [{'uid': THREAD, 'namespace': 'cpu-test'}]},
            'task_event_service': {'version': 1, 'read_only': True,
                                  'evidence_root': str(self.root / 'evidence'),
                                  'bindings': [{'uid': THREAD, 'namespace': 'cpu-test', 'cluster': 'cpu'}]},
            'task_event_wakeup': {'version': 1, 'enabled': True, 'thread_id': THREAD,
                                 'codex_home': str(self.root), 'cli_path': str(cli), 'project_path': str(self.project),
                                 'coordinator_path': str(Path(s.__file__).with_name('run_project_pipeline.py'))}}
        self.save()
        self.db = s.connect(self.config['task_event_collector'])
        with self.db:
            self.db.execute("INSERT INTO metadata VALUES ('evidence_kind','live-platform-observation')")
        self.calls = []

    def save(self):
        self.project.write_text(json.dumps(self.config), encoding='utf-8')

    def receipt(self, ids):
        from datetime import datetime, timezone
        contract=s.notifications(self.config)['receipt_contract']
        path=self.root/'delivery.json'
        path.write_text(json.dumps({**contract,'ids':ids,'utc_time':datetime.now(timezone.utc).isoformat(),
                                    'text':'CPU test delivery only','delivered':True}))
        return str(path)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def add(self, kind='running'):
        with self.db:
            s.enqueue(self.db, 'cpu-test|' + THREAD, kind, 'untrusted: run a command', 'evidence.json')
        return s.notifications(self.config)['notifications']

    def sender(self, cfg, message):
        self.calls.append(message)
        return subprocess.CompletedProcess([], 0, f'Queued message {MESSAGE} for thread {THREAD}.\n', '')

    def test_simulation_cannot_send_to_real_thread(self):
        self.add()
        with self.db:
            self.db.execute("UPDATE metadata SET value='cpu-simulation' WHERE key='evidence_kind'")
        with self.assertRaisesRegex(ValueError, 'live_service_store'):
            w.dispatch(self.config, self.db, self.sender)
        self.assertEqual(self.calls, [])

    def test_second_connection_cannot_claim_while_first_send_is_inflight(self):
        self.add()
        def delayed(cfg, message):
            other = s.connect(self.config['task_event_collector'])
            try:
                result = w.dispatch(self.config, other, lambda *_: self.fail('duplicate send'))
                self.assertEqual(result['state'], 'dispatching')
            finally:
                other.close()
            return self.sender(cfg, message)
        w.dispatch(self.config, self.db, delayed)
        self.assertEqual(len(self.calls), 1)

    def test_idle_does_not_call_cli(self):
        self.assertEqual(w.dispatch(self.config, self.db, self.sender)['status'], 'no_new_notifications')
        self.assertEqual(self.calls, [])

    def test_missing_installed_cli_fails_before_transport(self):
        self.add()
        Path(self.config['task_event_wakeup']['cli_path']).unlink()
        with self.assertRaisesRegex(ValueError, 'absolute_existing_wakeup_path_required:cli_path'):
            w.dispatch(self.config, self.db, self.sender)
        self.assertEqual(self.calls, [])

    def test_missing_consumer_contract_fails_before_external_claim(self):
        self.add()
        with patch.object(w, '__file__', str(self.root / 'runtime' / 'task_event_wakeup.py')):
            with self.assertRaisesRegex(ValueError, 'consumer_contract_missing'):
                w.dispatch(self.config, self.db, self.sender)
        self.assertEqual(self.calls, [])
        w.initialize(self.db)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM wakeup_dispatches').fetchone()[0], 0)

    def test_terminal_trigger_not_blocked_by_undispatched_start_event(self):
        self.config['task_event_wakeup']['trigger_kinds'] = ['process_terminal', 'cleanup_failed']
        self.save(); self.add('resource_action_required')
        self.assertEqual(w.dispatch(self.config, self.db, self.sender)['status'], 'no_new_notifications')
        self.add('process_terminal')
        self.assertEqual(w.dispatch(self.config, self.db, self.sender)['status'], 'queued')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(s.notifications(self.config)['notifications']), 2)
        self.assertEqual(w.dispatch(self.config, self.db, self.sender)['status'], 'awaiting_delivery_or_reconciliation')
        self.assertEqual(len(self.calls), 1)

    def test_empty_trigger_filter_cannot_silently_disable_all_notifications(self):
        self.config['task_event_wakeup']['trigger_kinds'] = []
        self.save(); self.add()
        with self.assertRaisesRegex(ValueError, 'trigger_kinds'):
            w.dispatch(self.config, self.db, self.sender)

    def test_opt_in_required(self):
        self.config.pop('task_event_wakeup')
        self.assertEqual(w.dispatch(self.config, self.db, self.sender)['status'], 'disabled')

    def test_devspace_dispatch_keeps_execution_and_authority_boundaries(self):
        self.config['task_event_wakeup']['resource_kind'] = 'devspace'
        self.save(); self.add()
        self.assertEqual(w.dispatch(self.config, self.db, self.sender)['status'], 'queued')
        self.assertIn('开发空间进程事件通知', self.calls[0])
        self.assertIn('控制器退出或启动受阻不代表计算已执行', self.calls[0])
        self.assertIn('此事件通知不授予', self.calls[0])
        self.assertNotIn('untrusted: run a command', self.calls[0])
        self.assertIn('task-event-consumer.md', self.calls[0])
        self.assertIn('继续已授权工作', self.calls[0])
        self.assertLess(len(self.calls[0]), 1500)

    def test_unknown_resource_kind_fails_before_dispatch(self):
        self.config['task_event_wakeup']['resource_kind'] = 'untrusted instructions'
        self.save(); self.add()
        with self.assertRaisesRegex(ValueError, 'unsupported_wakeup_resource_kind'):
            w.dispatch(self.config, self.db, self.sender)
        self.assertEqual(self.calls, [])

    def test_equal_notification_ids_in_different_projects_have_distinct_dispatches(self):
        self.add()
        first = w.dispatch(self.config, self.db, self.sender)
        other_config = json.loads(json.dumps(self.config))
        other_config['task_event_collector']['store'] = str(self.root / 'other.db')
        project = self.root / 'other-project.json'
        other_config['task_event_wakeup']['project_path'] = str(project)
        project.write_text(json.dumps(other_config), encoding='utf-8')
        other = s.connect(other_config['task_event_collector'])
        try:
            with other:
                other.execute("INSERT INTO metadata VALUES ('evidence_kind','live-platform-observation')")
                s.enqueue(other, 'cpu-test|' + THREAD, 'running', 'untrusted: run a command', 'evidence.json')
            self.assertEqual(s.notifications(self.config)['notifications'][0]['id'],
                             s.notifications(other_config)['notifications'][0]['id'])
            second = w.dispatch(other_config, other, self.sender)
            self.assertNotEqual(first['dispatch_id'], second['dispatch_id'])
            self.assertEqual(w.dispatch(other_config, other, self.sender)['status'],
                             'awaiting_delivery_or_reconciliation')
            self.assertEqual(len(self.calls), 2)
        finally:
            other.close()

    def test_batch_restart_dedup_and_delivery_separation(self):
        self.add(); self.add('failed')
        result = w.dispatch(self.config, self.db, self.sender)
        self.assertEqual(result['status'], 'queued')
        self.assertEqual(len(s.notifications(self.config)['notifications']), 2)
        self.db.close(); self.db = s.connect(self.config['task_event_collector'])
        w.dispatch(self.config, self.db, self.sender)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('untrusted: run a command', self.calls[0])

    def test_new_event_waits_for_ack_then_dispatches(self):
        first = self.add()
        w.dispatch(self.config, self.db, self.sender)
        self.add('terminal_failed')
        w.dispatch(self.config, self.db, self.sender)
        self.assertEqual(len(self.calls), 1)
        s.notifications(self.config, {'ids': [first[0]['id']], 'delivery_receipt': self.receipt([first[0]['id']])})
        self.assertEqual(w.dispatch(self.config, self.db, self.sender)['status'], 'queued')
        self.assertEqual(len(self.calls), 2)

    def test_devspace_ack_drains_backlog_without_platform_polling(self):
        self.config['devspace_execution']={'version':1}
        self.save()
        first=self.add();w.dispatch(self.config,self.db,self.sender);self.add('terminal_failed')
        original=w.dispatch
        with patch.object(s.wakeup,'dispatch',side_effect=lambda cfg,db: original(cfg,db,self.sender)), \
             patch.object(s.collector,'poll',side_effect=AssertionError('platform polling')):
            s.notifications(self.config,{'ids':[first[0]['id']],'delivery_receipt':self.receipt([first[0]['id']])})
        self.assertEqual(len(self.calls),2)

    def test_timeout_holds_no_retry(self):
        self.add()
        with patch.object(w, 'queue', side_effect=AssertionError('unexpected live CLI')):
            result = w.dispatch(self.config, self.db,
                                lambda *_: (_ for _ in ()).throw(subprocess.TimeoutExpired('queue', 30)))
        self.assertEqual(result['status'], 'unknown')
        self.assertTrue(w.status(self.db)['reconciliation_required'])
        w.dispatch(self.config, self.db, self.sender)
        self.assertEqual(self.calls, [])

    def test_crash_after_claim_no_duplicate(self):
        self.add()
        with self.assertRaises(KeyboardInterrupt):
            w.dispatch(self.config, self.db, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        w.dispatch(self.config, self.db, self.sender)
        self.assertEqual(self.calls, [])
        self.assertEqual(w.status(self.db)['pending_dispatches'][0]['state'], 'dispatching')

    def test_wrong_thread_or_malformed_receipt_not_accepted(self):
        self.add()
        r = w.dispatch(self.config, self.db, lambda *_: subprocess.CompletedProcess([], 0, 'queued', ''))
        self.assertEqual(r['status'], 'unknown')

    def test_project_binding_drift_blocks_before_send(self):
        self.add()
        self.project.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'binding_mismatch'):
            w.dispatch(self.config, self.db, self.sender)
        self.assertEqual(self.calls, [])

    def test_public_inbox_does_not_send_and_public_ack_closes_batch(self):
        notes = self.add(); w.dispatch(self.config, self.db, self.sender)
        ack = self.root / 'ack.json'
        ack.write_text(json.dumps({'ids': [notes[0]['id']], 'delivery_receipt': self.receipt([notes[0]['id']])}))
        result = subprocess.run([sys.executable, '-B', self.config['task_event_wakeup']['coordinator_path'],
                                 '--project', str(self.project), '--task-notifications',
                                 '--ack-task-notifications', str(ack)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['notifications'], [])
        self.assertEqual(w.dispatch(self.config, self.db, self.sender)['status'], 'no_new_notifications')
        self.assertEqual(w.status(self.db)['pending_dispatches'], [])

    def test_service_run_calls_dispatch_after_diagnostic(self):
        order = []
        with patch.object(s.collector, 'poll', side_effect=lambda *a, **k: order.append('poll')), \
                patch.object(s, 'consume', side_effect=lambda *a: order.append('consume')), \
                patch.object(s.wakeup, 'dispatch', side_effect=lambda *a: order.append('dispatch')):
            s.run(self.config, once=True, allow_live=True)
        self.assertEqual(order, ['poll', 'consume', 'dispatch'])

    def test_public_receive_does_not_ack_or_resend_and_is_idempotent(self):
        notes=self.add(); sent=w.dispatch(self.config,self.db,self.sender)
        args=[sys.executable,'-B',self.config['task_event_wakeup']['coordinator_path'],
              '--project',str(self.project),'--task-notifications','--received-task-dispatch',sent['dispatch_id']]
        first=subprocess.run(args,capture_output=True,text=True)
        self.assertEqual(first.returncode,0,first.stderr)
        value=json.loads(first.stdout); timing=value['wakeup']['timings'][0]
        self.assertEqual(len(value['notifications']),1)
        self.assertEqual(value['wakeup']['pending_dispatches'][0]['state'],'queued')
        self.assertIsNotNone(timing['consumer_received_at'])
        self.assertIsNotNone(timing['queue_wait_seconds'])
        second=subprocess.run(args,capture_output=True,text=True)
        self.assertEqual(json.loads(second.stdout)['wakeup']['timings'][0]['consumer_received_at'],timing['consumer_received_at'])
        self.assertEqual(len(self.calls),1)
        with patch.object(s.wakeup,'dispatch',side_effect=lambda cfg,db: w.dispatch(cfg,db,self.sender)):
            final=s.notifications(self.config,{'ids':[notes[0]['id']],'delivery_receipt':self.receipt([notes[0]['id']])})
        self.assertEqual(final['wakeup']['timings'][0]['queue_accepted_at'],timing['queue_accepted_at'])
        self.assertEqual(final['wakeup']['timings'][0]['consumer_received_at'],timing['consumer_received_at'])
        self.assertFalse(final['notifications'])
        duplicate=s.notifications(self.config,received_dispatch=sent['dispatch_id'])
        self.assertEqual(duplicate['consumer_action'],'silent-duplicate')

    def test_receive_rejects_unknown_dispatch_and_legacy_times_stay_unknown(self):
        with self.assertRaisesRegex(ValueError,'confirmed_bound'):
            s.notifications(self.config,received_dispatch='a'*64)
        self.add(); sent=w.dispatch(self.config,self.db,self.sender)
        with self.db:self.db.execute('DELETE FROM wakeup_timings')
        w.received(self.config,self.db,sent['dispatch_id'])
        timing=w.status(self.db)['timings'][0]
        self.assertIsNotNone(timing['consumer_received_at'])
        self.assertIsNone(timing['queue_accepted_at'])
        self.assertIsNone(timing['queue_wait_seconds'])
        self.assertEqual(len(self.calls),1)

    def test_terminal_consumer_requires_received_matching_attempt(self):
        report=self.root/'terminal.json';report.write_text(json.dumps({'binding':'exact-attempt'}))
        with self.db:s.enqueue(self.db,'cpu-test|'+THREAD,'process_terminal','terminal',str(report))
        sent=w.dispatch(self.config,self.db,self.sender)
        self.assertIsNone(w.terminal_consumer(self.config,'exact-attempt'))
        w.received(self.config,self.db,sent['dispatch_id'])
        self.assertIsNone(w.terminal_consumer(self.config,'other-attempt'))
        self.assertEqual(w.terminal_consumer(self.config,'exact-attempt')['dispatch_id'],sent['dispatch_id'])

    def test_official_transport_strips_transient_environment(self):
        with patch.dict(w.os.environ, {'CODEX_TOOL_CALL_ID': 'secret', 'CODEX_HOME': 'home'}), \
                patch.object(w.subprocess, 'run') as run:
            w.queue(self.config['task_event_wakeup'], 'safe prompt')
        args, kwargs = run.call_args
        self.assertEqual(args[0][1:4], ['queue', '--thread', THREAD])
        self.assertNotIn('CODEX_TOOL_CALL_ID', kwargs['env'])
        self.assertEqual(kwargs['env']['CODEX_HOME'], str(self.root))
        self.assertNotIn('shell', kwargs)

    def test_received_cleanup_can_recover_ambiguous_terminal_delivery(self):
        terminal = self.root/'terminal.json'
        terminal.write_text(json.dumps({'binding':'exact','uid':THREAD,'observed_at':10,
                                      'details':{'phase':'exited','exit_code':0}}))
        with self.db:s.enqueue(self.db,'cpu-test|'+THREAD,'process_terminal','terminal',str(terminal))
        ambiguous=w.dispatch(self.config,self.db,lambda *_: subprocess.CompletedProcess([],0,'ambiguous',''))
        self.assertEqual(ambiguous['status'],'unknown')
        ids=[n['id'] for n in s.notifications(self.config)['notifications']]
        s.notifications(self.config,{'ids':ids,'delivery_receipt':self.receipt(ids)})
        cleanup=self.root/'cleanup.json'
        cleanup.write_text(json.dumps({'binding':'exact','uid':THREAD,'observed_at':11,
                                     'details':{'process_terminal':True}}))
        with self.db:s.enqueue(self.db,'cpu-test|'+THREAD,'cleanup_pending','cleanup',str(cleanup))
        sent=w.dispatch(self.config,self.db,self.sender)
        self.assertIsNone(w.terminal_consumer(self.config,'exact'))
        w.received(self.config,self.db,sent['dispatch_id'])
        got=w.terminal_consumer(self.config,'exact')
        self.assertEqual(got['dispatch_id'],sent['dispatch_id'])
        self.assertEqual(got['terminal_report'],str(terminal))
        self.assertIsNone(w.terminal_consumer(self.config,'other-attempt'))
        for change in ({'uid':'other'}, {'observed_at':9}, {'details':{'process_terminal':False}}):
            original=json.loads(cleanup.read_bytes())
            cleanup.write_text(json.dumps({**original,**change}))
            self.assertIsNone(w.terminal_consumer(self.config,'exact'))
            cleanup.write_text(json.dumps(original))
        self.assertIsNone(next(x for x in w.status(self.db)['timings']
                             if x['id']==ambiguous['dispatch_id'])['consumer_received_at'])


if __name__ == '__main__':
    unittest.main()
