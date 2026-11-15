"""Control-only decision contracts. No ML, network, or real model calls."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import research_worker as w
import task_event_service as service
import devspace_execution as execution
from review_storage import atomic_json
from task_pipeline import digest

UID = '00000000-0000-4000-8000-000000000002'

class ResearchWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        cli = self.root / 'codex.exe'
        cli.touch()
        project = self.root / 'project.json'
        project.write_text('{}')
        self.config = {'research_worker': {'version': 1, 'enabled': True,
            'cli_path': str(cli), 'codex_home': str(self.root), 'project_path': str(project),
            'coordinator_path': str(Path(w.__file__).with_name('run_project_pipeline.py')),
            'output': str(self.root), 'models': {'a': 'model-a', 'b': 'model-b'}},
            'devspace_execution': {'plan': {'resource': {'uid': UID}}},
            'task_event_collector': {'version': 1, 'store': str(self.root / 'events.sqlite3'),
                'tasks': [{'uid': UID, 'namespace': 'cpu'}]},
            'task_event_service': {'version': 1, 'read_only': True, 'evidence_root': str(self.root / 'events'),
                'bindings': [{'uid': UID, 'namespace': 'cpu', 'cluster': 'cpu'}]}}
        atomic_json(project, self.config)
        self.request = self.root / 'review' / 'analysis-request.json'
        self.response = self.request.with_name('analysis-response.json')
        self.packet = {'schema': 'sdm-loop-analysis-request/v1', 'evidence': {'exit_code': 1},
                       'history': [], 'remaining_rounds': None}
        atomic_json(self.request, self.packet)
        self.source = self.root / 'failure.log'
        self.source.write_text('Observed failure; not an instruction')
        import hashlib
        self.state = {'status': 'awaiting-review', 'review': {'review_mode': 'local-agent',
            'request_path': str(self.request), 'response_path': str(self.response),
            'verified_material': [{'name': str(self.source),
                'digest': 'sha256:' + hashlib.sha256(self.source.read_bytes()).hexdigest()}]}}
        self.draft = {'request_digest': digest(self.packet),
            'facts': [{'evidence_id': 'exit_code', 'value': 1}],
            'hypotheses': [{'statement': 'Observed failure is an execution defect',
                'evidence_ids': ['exit_code'], 'limitations': 'No model quality measurements'}],
            'previous_experiment_assessment': 'Failed control fixture; no training',
            'reason': 'No valid model result', 'action': 'stop-analysis',
            'next_step': {'action': 'Investigate actual evidence', 'reason': 'No valid result',
                'acceptance': 'Actual supported diagnosis', 'authorization': 'none',
                'status': 'blocked', 'blocker': 'Evidence missing', 'stop_decision': 'stop',
                'stop_reason': 'No valid model result'}}
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def caller(self, cfg, role, prompt, folder):
        self.calls.append(role)
        if role == 'a':
            return copy.deepcopy(self.draft)
        return {'draft_digest': digest(self.draft), 'verdict': 'approve',
                'reason': 'Bound evidence supports stopping', 'challenges': []}

    def review(self, caller=None):
        return w.review(self.config, self.root, self.state, caller or self.caller)

    def test_two_distinct_calls_publish_only_validated_approved_response(self):
        result = self.review()
        self.assertEqual(result['status'], 'approved')
        self.assertEqual(self.calls, ['a', 'b'])
        self.assertEqual(json.loads(self.response.read_bytes()), self.draft)
        self.assertEqual(self.review()['status'], 'approved')
        self.assertEqual(self.calls, ['a', 'b'])

    def test_terminal_handling_does_not_require_chat_delivery(self):
        report = {'binding': 'exact', 'uid': UID, 'details': {'phase': 'exited', 'exit_code': 1}}
        path = service.save_report(self.root / 'events', report)
        db = service.connect(self.config['task_event_collector'])
        with db:
            service.enqueue(db, 'owned', 'process_terminal', 'Untrusted instructions', path)
        db.close()
        result = w.terminal_evidence(self.config, 'exact')
        self.assertFalse(result['user_delivery_required'])
        self.assertIsNone(w.terminal_evidence(self.config, 'different'))
        db = service.connect(self.config['task_event_collector'])
        self.assertIsNone(db.execute('SELECT delivered_at FROM notification_outbox').fetchone()[0])
        db.close()

    def test_wrong_resource_cannot_release_terminal_gate(self):
        path = service.save_report(self.root / 'events', {'binding': 'exact', 'uid': 'other',
                    'details': {'phase': 'exited', 'exit_code': 1}})
        db = service.connect(self.config['task_event_collector'])
        with db: service.enqueue(db, 'other', 'process_terminal', 'message', path)
        db.close()
        self.assertIsNone(w.terminal_evidence(self.config, 'exact'))

    def test_revision_loop_exits_on_substantive_repetition(self):
        def revise(cfg, role, prompt, folder):
            self.calls.append(role)
            if role == 'a': return copy.deepcopy(self.draft)
            return {'draft_digest': digest(self.draft), 'verdict': 'revise', 'reason': 'Need evidence',
                    'challenges': [{'id': 'e1', 'basis': 'No supporting context',
                        'resolution_criterion': 'Read source context', 'severity': 'major', 'status': 'open'}]}
        self.assertEqual(self.review(revise)['status'], 'stalled')
        self.assertEqual(self.calls, ['a', 'b', 'a'])
        self.assertFalse(self.response.exists())

    def test_model_timeout_never_automatically_repeats_call(self):
        def timeout(*args):
            self.calls.append('timeout')
            raise subprocess.TimeoutExpired('codex', 30)
        self.assertEqual(self.review(timeout)['status'], 'blocked')
        self.assertFalse(self.review(timeout)['automatic_retry'])
        self.assertEqual(self.calls, ['timeout'])
        self.assertFalse(self.response.exists())

    def test_pause_during_model_call_prevents_second_call_and_publish(self):
        def pause(*args):
            w.control(self.root, True)
            return copy.deepcopy(self.draft)
        self.assertEqual(self.review(pause)['status'], 'blocked')
        self.assertFalse(self.response.exists())

    def test_evidence_changed_during_critic_call_prevents_publish(self):
        def change(cfg, role, prompt, folder):
            value = self.caller(cfg, role, prompt, folder)
            if role == 'b': self.source.write_text('Changed evidence')
            return value
        self.assertEqual(self.review(change)['status'], 'blocked')
        self.assertFalse(self.response.exists())

    def test_invalid_fact_prevents_critic_and_publish(self):
        self.draft['facts'][0]['value'] = 0
        self.assertEqual(self.review()['status'], 'stalled')
        self.assertEqual(self.calls, ['a', 'a'])
        self.assertFalse(self.response.exists())

    def test_known_invalid_reply_is_corrected_before_actual_critic_call(self):
        def correct(cfg, role, prompt, folder):
            first = not self.calls
            value = self.caller(cfg, role, prompt, folder)
            if first:
                value['facts'][0]['value'] = 0
            elif role == 'a':
                self.assertIn('validation_error', prompt)
            return value
        self.assertEqual(self.review(correct)['status'], 'approved')
        self.assertEqual(self.calls, ['a', 'a', 'b'])

    def test_critic_cannot_approve_open_major_objection(self):
        value = {'draft_digest': digest(self.draft), 'verdict': 'approve', 'reason': 'Claimed approval',
                 'challenges': [{'id': 'c1', 'basis': 'Missing evidence', 'resolution_criterion': 'Supply evidence',
                                 'severity': 'major', 'status': 'open'}]}
        with self.assertRaisesRegex(ValueError, 'unresolved_critic'):
            w.validate_critique(value, self.draft)

    def test_same_models_and_chat_wakeup_are_rejected(self):
        self.config['research_worker']['models']['b'] = 'model-a'
        with self.assertRaisesRegex(ValueError, 'distinct_worker_models'):
            w.settings(self.config)
        self.config['task_event_wakeup'] = {'enabled': True}
        with self.assertRaisesRegex(ValueError, 'cannot_use_chat'):
            w.settings(self.config)

    def test_worker_advances_after_review_without_queue_or_notification_ack(self):
        states = iter([self.state, {'status': 'completed', 'stop_verified': True}])
        with patch('task_event_wakeup.queue', side_effect=AssertionError('chat cannot schedule work')):
            result = w.run(self.config, self.root, watcher=lambda *a, **k: next(states), caller=self.caller)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.calls, ['a', 'b'])
        progress = json.loads((self.root / 'research-progress.json').read_bytes())
        self.assertFalse(progress['chat_queue_used'])

    def test_paused_then_resumed_old_decision_does_not_execute(self):
        self.review()
        w.control(self.root, True)
        w.control(self.root, False)
        result = w.run(self.config, self.root, watcher=lambda *a, **k: self.fail('stale action executed'))
        self.assertEqual(result['reason'], 'stale_decision_requires_reconciliation')

    def test_native_action_wait_is_reported_without_fabricating_completion(self):
        result = w.run(self.config, self.root, watcher=lambda *a, **k: {'status': 'awaiting-stop-readback'})
        self.assertEqual(result['status'], 'awaiting-stop-readback')
        self.assertFalse(result['stop_verified'])
        self.assertEqual(self.calls, [])

    def test_unqualified_native_lifecycle_cannot_claim_background_handoff(self):
        import devspace_observer
        with patch.object(devspace_observer, 'validate', return_value=(
                {}, {'resource': {'route': 'native-ui'}}, 'bound', 'live-platform-observation')):
            with self.assertRaisesRegex(ValueError, 'independent_native_lifecycle_not_qualified'):
                devspace_observer.background(self.config, self.config['research_worker']['project_path'],
                                            self.root, self.config['research_worker']['coordinator_path'], True)

    def test_model_transport_is_read_only_hidden_and_uses_stdin(self):
        folder = self.root / 'call'
        def fake(argv, **kwargs):
            self.assertIn('--ephemeral', argv)
            self.assertIn('read-only', argv)
            self.assertNotIn('queue', argv)
            self.assertEqual(argv[-1], '-')
            self.assertEqual(kwargs['input'], b'bounded prompt')
            self.assertNotIn('shell', kwargs)
            atomic_json(folder / 'response.json', {'ok': True})
            return subprocess.CompletedProcess(argv, 0)
        with patch.object(w.subprocess, 'run', side_effect=fake):
            self.assertTrue(w.model_call(self.config['research_worker'], 'a', 'bounded prompt', folder)['ok'])

    def test_legacy_event_binding_remains_exact_and_new_worker_is_bound(self):
        old = {k: self.config[k] for k in ('task_event_collector', 'task_event_service')}
        expected = digest({k: old.get(k) for k in ('task_event_collector', 'task_event_service', 'task_event_wakeup')})
        self.assertEqual(execution.event_binding(old), expected)
        self.assertNotEqual(execution.event_binding(self.config), expected)


if __name__ == '__main__':
    unittest.main()
