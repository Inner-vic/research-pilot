"""Durable approval publication faults; no real models, ML or platform calls."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_research_worker as fixtures
import research_worker as w
import research_decision_store as store
from review_storage import atomic_json, locked
from task_pipeline import digest


class AtomicPublicationTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ResearchWorkerTests()
        self.case.setUp()
        self.case.config['research_worker']['version'] = 2
        atomic_json(Path(self.case.config['research_worker']['project_path']), self.case.config)
        self.entered = []

    def tearDown(self):
        self.case.tearDown()

    def commit(self):
        return next(self.case.root.glob('research-decisions/*/commit.json'))

    def no_more_calls(self, *args):
        self.fail('Completed or unknown model call was repeated')

    def coordinator(self, *args, **kwargs):
        self.entered.append(True)
        return {'status': 'completed', 'review': {'status': 'completed',
            'case_directory': str(self.case.response.parent),
            'decision': {'request_digest': digest(self.case.packet)}}}

    def gate(self):
        return w.advance_checked(self.case.config, self.case.root, self.coordinator)

    def inject(self, boundary):
        original = store.atomic_json
        def fail(path, value):
            path = Path(path)
            if boundary == 'commit' and path.name == 'commit.json':
                raise OSError('injected before commit')
            if boundary == 'response' and path.name == 'analysis-response.json':
                raise OSError('injected after commit before response projection')
            if boundary == 'cache' and path.name == 'state.json':
                raise OSError('injected after response before cache projection')
            return original(path, value)
        with patch.object(store, 'atomic_json', side_effect=fail):
            return self.case.review()

    def test_payload_approval_and_pending_notice_share_one_commit(self):
        result = self.case.review()
        self.assertEqual(result['status'], 'approved')
        value = json.loads(self.commit().read_bytes())
        self.assertEqual(value['response'], self.case.draft)
        self.assertEqual(value['critique']['verdict'], 'approve')
        self.assertEqual(value['record']['response_digest'], digest(self.case.draft))
        self.assertFalse(value['consumed'])
        self.assertFalse(value['outbox']['delivered'])
        self.assertFalse((self.case.root / 'research-ready.json').exists())
        self.assertEqual(self.case.calls, ['a', 'b'])

    def test_failure_before_commit_does_not_publish_or_repeat_models(self):
        self.assertEqual(self.inject('commit')['status'], 'blocked')
        self.assertFalse(list(self.case.root.glob('research-decisions/*/commit.json')))
        self.assertFalse(self.case.response.exists())
        self.assertEqual(self.case.review(self.no_more_calls)['status'], 'blocked')
        self.assertEqual(self.case.calls, ['a', 'b'])

    def test_after_commit_response_failure_recovers_without_inference(self):
        self.assertEqual(self.inject('response')['status'], 'awaiting-publication-recovery')
        committed = self.commit().read_bytes()
        self.assertFalse(self.case.response.exists())
        self.assertEqual(self.case.review(self.no_more_calls)['status'], 'approved')
        self.assertEqual(self.commit().read_bytes(), committed)
        self.assertEqual(json.loads(self.case.response.read_bytes()), self.case.draft)
        self.assertEqual(self.case.calls, ['a', 'b'])

    def test_after_response_cache_failure_recovers_without_inference(self):
        self.assertEqual(self.inject('cache')['status'], 'awaiting-publication-recovery')
        self.assertTrue(self.case.response.exists())
        self.assertEqual(self.case.review(self.no_more_calls)['status'], 'approved')
        self.assertEqual(self.case.calls, ['a', 'b'])

    def test_guard_materializes_missing_response_before_coordinator(self):
        self.inject('response')
        def inspect(*args, **kwargs):
            self.assertEqual(json.loads(self.case.response.read_bytes()), self.case.draft)
            return self.coordinator(*args, **kwargs)
        result = w.advance_checked(self.case.config, self.case.root, inspect)
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(json.loads(self.commit().read_bytes())['consumed'])
        self.assertEqual(self.case.calls, ['a', 'b'])

    def test_changed_view_blocks_before_coordinator(self):
        self.case.review()
        atomic_json(self.case.response, {**self.case.draft, 'reason': 'concurrent alteration'})
        with self.assertRaisesRegex(ValueError, 'published_response_changed'):
            self.gate()
        self.assertEqual(self.entered, [])

    def test_changed_commit_blocks_before_coordinator(self):
        self.case.review()
        value = json.loads(self.commit().read_bytes())
        value['response']['reason'] += '; changed'
        atomic_json(self.commit(), value)
        with self.assertRaisesRegex(ValueError, 'decision_commit_integrity_failed'):
            self.gate()
        self.assertEqual(self.entered, [])

    def test_uncommitted_response_never_enters_coordinator(self):
        atomic_json(self.case.response, self.case.draft)
        with self.assertRaisesRegex(ValueError, 'uncommitted_research_response'):
            self.gate()
        self.assertEqual(self.entered, [])

    def test_changed_original_material_blocks_v2_before_coordinator(self):
        self.case.review()
        for path in (self.case.source, self.case.request,
                     Path(self.case.config['research_worker']['project_path'])):
            original = path.read_bytes()
            try:
                with self.subTest(source=path.name):
                    if path.suffix == '.json':
                        value = json.loads(original)
                        value['changed'] = True
                        atomic_json(path, value)
                    else:
                        path.write_text('Changed original evidence', encoding='utf-8')
                    with self.assertRaises(ValueError):
                        self.gate()
                    self.assertEqual(self.entered, [])
            finally:
                path.write_bytes(original)

    def test_unrelated_completed_case_cannot_consume_approval(self):
        self.case.review()
        def other_case(*args, **kwargs):
            result = self.coordinator()
            result['review']['case_directory'] = str(self.case.root / 'different-case')
            return result
        w.advance_checked(self.case.config, self.case.root, other_case)
        self.assertFalse(json.loads(self.commit().read_bytes())['consumed'])
        self.gate()
        self.assertTrue(json.loads(self.commit().read_bytes())['consumed'])

    def test_stale_committed_result_never_becomes_a_new_model_call(self):
        self.inject('response')
        w.control(self.case.root, True)
        w.control(self.case.root, False)
        with self.assertRaisesRegex(ValueError, 'stale_decision_requires_reconciliation'):
            self.case.review(self.no_more_calls)
        self.assertFalse(self.case.response.exists())
        self.assertEqual(self.case.calls, ['a', 'b'])

    def test_uncommitted_claim_survives_control_revision_change(self):
        def unknown(*args):
            raise OSError('unknown model outcome')
        self.assertEqual(self.case.review(unknown)['status'], 'blocked')
        w.control(self.case.root, True)
        w.control(self.case.root, False)
        with self.assertRaisesRegex(ValueError, 'unreconciled_decision_claim'):
            self.case.review(self.no_more_calls)

    def test_consumption_commit_survives_cache_write_failure(self):
        self.case.review()
        original = store.atomic_json
        def fail_cache(path, value):
            if Path(path).name == 'state.json':
                raise OSError('injected after consumption commit')
            return original(path, value)
        with patch.object(store, 'atomic_json', side_effect=fail_cache):
            with self.assertRaises(OSError):
                self.gate()
        self.assertEqual(len(self.entered), 1)
        value = json.loads(self.commit().read_bytes())
        self.assertTrue(value['consumed'])
        with locked(self.case.root, '.research-control.lock'):
            store.before_advance(self.case.config, self.case.root)
        self.assertTrue(json.loads(self.commit().with_name('state.json').read_bytes())['consumed'])
        self.assertEqual(len(self.entered), 1)
        self.assertEqual(self.case.calls, ['a', 'b'])

    def test_repeated_recovery_has_one_pending_outbox_identity(self):
        self.case.review()
        first = json.loads(self.commit().read_bytes())['outbox']
        for _ in range(2):
            self.case.review(self.no_more_calls)
        self.assertEqual(json.loads(self.commit().read_bytes())['outbox'], first)
        self.assertEqual(len(list(self.case.root.glob('research-decisions/*/commit.json'))), 1)
        self.assertFalse(first['delivered'])

    def test_consumed_history_does_not_block_later_control_revision(self):
        self.case.review()
        self.gate()
        w.control(self.case.root, True)
        w.control(self.case.root, False)
        self.assertEqual(self.gate()['status'], 'completed')
        self.assertEqual(self.case.calls, ['a', 'b'])

    def test_bad_derived_cache_is_rebuilt_from_commit(self):
        self.case.review()
        cache = self.commit().with_name('state.json')
        cache.write_text('{broken derived cache', encoding='utf-8')
        self.assertEqual(self.gate()['status'], 'completed')
        self.assertEqual(json.loads(cache.read_bytes())['status'], 'approved')

    def test_v1_workspace_cannot_be_hot_migrated(self):
        legacy = fixtures.ResearchWorkerTests()
        legacy.setUp()
        try:
            legacy.review()
            legacy.config['research_worker']['version'] = 2
            atomic_json(Path(legacy.config['research_worker']['project_path']), legacy.config)
            with self.assertRaisesRegex(ValueError, 'new_frozen_attempt_required'):
                legacy.review(self.no_more_calls)
        finally:
            legacy.tearDown()

    def test_worker_runs_v2_without_chat_acknowledgement(self):
        states = iter([self.case.state, self.coordinator()])
        self.entered.clear()
        result = w.run(self.case.config, self.case.root, watcher=lambda *a, **k: next(states), caller=self.case.caller)
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(json.loads(self.commit().read_bytes())['consumed'])
        self.assertEqual(self.case.calls, ['a', 'b'])


if __name__ == '__main__':
    unittest.main()
