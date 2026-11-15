"""Fault-injected pre-action decision gates; no model or platform calls."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_research_worker as fixtures
import research_worker as w
import devspace_execution as execution
import devspace_observer as observer
from review_storage import atomic_json


class PreActionDecisionTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ResearchWorkerTests()
        self.case.setUp()
        self.entered = []

    def tearDown(self):
        self.case.tearDown()

    def coordinator(self, *args, **kwargs):
        self.entered.append(True)
        return {'status': 'completed'}

    def gate(self):
        return w.advance_checked(self.case.config, self.case.root, self.coordinator)

    def record_path(self):
        return next(self.case.root.glob('research-decisions/*/state.json'))

    def alter_response(self):
        value = json.loads(self.case.response.read_bytes())
        value['reason'] += '; concurrent revision'
        atomic_json(self.case.response, value)

    def test_changed_response_is_rejected_before_coordinator(self):
        self.case.review()
        self.alter_response()
        with self.assertRaisesRegex(ValueError, 'published_response_changed'):
            self.gate()
        self.assertEqual(self.entered, [])

    def test_worker_checks_changed_response_before_injected_watcher(self):
        self.case.review()
        self.alter_response()
        result = w.run(self.case.config, self.case.root, watcher=self.coordinator, caller=self.case.caller)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('published_response_changed', result['reason'])
        self.assertEqual(self.entered, [])
        self.assertEqual(self.case.calls, ['a', 'b'])

    def test_public_selector_uses_same_gate(self):
        self.case.review()
        self.alter_response()
        with patch.object(execution, 'execution', side_effect=self.coordinator):
            advance = execution.project_execution(self.case.config, self.case.root)
            with self.assertRaisesRegex(ValueError, 'published_response_changed'):
                advance(self.case.config, self.case.root)
        self.assertEqual(self.entered, [])

    def test_shared_watcher_uses_same_gate(self):
        self.case.review()
        self.alter_response()
        with patch.object(execution, 'execution', side_effect=self.coordinator):
            with self.assertRaisesRegex(ValueError, 'published_response_changed'):
                observer.watch(self.case.config, self.case.root)
        self.assertEqual(self.entered, [])

    def test_main_and_study_selection_cannot_skip_the_gate(self):
        for field, module in (('devspace_main_workflow', 'devspace_main_workflow'),
                              ('devspace_study', 'devspace_study')):
            with self.subTest(workflow=field):
                case = fixtures.ResearchWorkerTests()
                case.setUp()
                try:
                    case.config[field] = {'version': 1}
                    atomic_json(Path(case.config['research_worker']['project_path']), case.config)
                    case.review()
                    value = json.loads(case.response.read_bytes())
                    atomic_json(case.response, {**value, 'reason': value['reason'] + '; changed'})
                    with patch(module + '.execution', side_effect=self.coordinator):
                        advance = execution.project_execution(case.config, case.root)
                        with self.assertRaisesRegex(ValueError, 'published_response_changed'):
                            advance(case.config, case.root)
                    self.assertEqual(self.entered, [])
                finally:
                    case.tearDown()

    def test_partial_publish_blocks_coordinator_after_ready_write_failure(self):
        original = w.atomic_json
        def crash(path, value):
            if Path(path).name == 'research-ready.json':
                raise OSError('injected publication failure')
            return original(path, value)
        with patch.object(w, 'atomic_json', side_effect=crash):
            result = self.case.review()
        self.assertEqual(result['status'], 'blocked')
        self.assertTrue(self.case.response.exists())
        self.assertFalse((self.case.root / 'research-ready.json').exists())
        with self.assertRaisesRegex(ValueError, 'uncommitted_research_response'):
            self.gate()
        self.assertEqual(self.entered, [])

    def test_visible_response_without_approval_receipt_is_rejected(self):
        self.case.review()
        record = json.loads(self.record_path().read_bytes())
        record['status'] = 'running'
        atomic_json(self.record_path(), record)
        with self.assertRaisesRegex(ValueError, 'uncommitted_research_response'):
            self.gate()
        self.assertEqual(self.entered, [])

    def test_changed_original_source_is_rejected_before_coordinator(self):
        self.case.review()
        self.case.source.write_text('changed raw source')
        with self.assertRaisesRegex(ValueError, 'review_source_changed'):
            self.gate()
        self.assertEqual(self.entered, [])

    def test_changed_project_object_cannot_reuse_another_projects_approval(self):
        self.case.review()
        changed = copy.deepcopy(self.case.config)
        changed['devspace_execution']['plan']['resource']['uid'] = 'another-resource'
        with self.assertRaisesRegex(ValueError, 'persisted_worker_project_changed'):
            w.advance_checked(changed, self.case.root, self.coordinator)
        self.assertEqual(self.entered, [])

    def test_partial_response_at_another_registered_location_is_rejected(self):
        other = self.case.root / 'bound-output' / 'analysis-request.json'
        atomic_json(other, self.case.packet)
        response = other.with_name('analysis-response.json')
        self.case.state['review'].update(request_path=str(other), response_path=str(response))
        original = w.atomic_json
        def crash(path, value):
            if Path(path).name == 'research-ready.json':
                raise OSError('injected publication failure')
            return original(path, value)
        with patch.object(w, 'atomic_json', side_effect=crash):
            self.case.review()
        self.assertTrue(response.exists())
        with self.assertRaisesRegex(ValueError, 'uncommitted_research_response'):
            self.gate()
        self.assertEqual(self.entered, [])

    def test_changed_request_is_rejected_before_coordinator(self):
        self.case.review()
        atomic_json(self.case.request, {**self.case.packet, 'evidence': {'exit_code': 0}})
        with self.assertRaisesRegex(ValueError, 'approved_request_changed'):
            self.gate()
        self.assertEqual(self.entered, [])

    def test_duplicate_approval_ownership_is_rejected(self):
        self.case.review()
        other = self.record_path().parent.parent / 'other-claim' / 'state.json'
        atomic_json(other, json.loads(self.record_path().read_bytes()))
        with self.assertRaisesRegex(ValueError, 'uncommitted_research_response'):
            self.gate()
        self.assertEqual(self.entered, [])

    def test_readiness_cannot_substitute_a_changed_digest(self):
        self.case.review()
        path = self.case.root / 'research-ready.json'
        ready = json.loads(path.read_bytes())
        atomic_json(path, {**ready, 'response_digest': 'sha256:wrong'})
        with self.assertRaisesRegex(ValueError, 'research_ready_digest_changed'):
            self.gate()
        self.assertEqual(self.entered, [])

    def test_accepted_consumption_is_recorded_and_old_revision_does_not_block_new_work(self):
        self.case.review()
        self.assertEqual(self.gate()['status'], 'completed')
        self.assertTrue(json.loads(self.record_path().read_bytes())['consumed'])
        self.assertEqual(json.loads((self.case.root / 'research-ready.json').read_bytes())['status'], 'consumed')
        w.control(self.case.root, True)
        w.control(self.case.root, False)
        self.assertEqual(self.gate()['status'], 'completed')
        self.assertEqual(len(self.entered), 2)


if __name__ == '__main__':
    unittest.main()
