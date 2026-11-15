"""Recover exact completed calls; never repeat uncertain calls. Control only."""
import copy
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import test_research_worker as fixtures
import research_worker as worker
import research_decision_store as store
import research_model_receipts as receipts
from review_storage import atomic_json


class ModelReceiptTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ResearchWorkerTests()
        self.case.setUp()
        self.case.config['research_worker']['version'] = 3
        atomic_json(self.case.config['research_worker']['project_path'], self.case.config)

    def tearDown(self):
        self.case.tearDown()

    def files(self, name):
        return list(self.case.root.glob('research-decisions/*/*/' + name))

    def never_call(self, *args):
        self.fail('Completed or unknown call was repeated')

    def break_final_commit(self):
        original = store.atomic_json
        def fail(path, value):
            if Path(path).name == 'commit.json':
                raise OSError('injected before final approval commit')
            original(path, value)
        with patch.object(store, 'atomic_json', side_effect=fail):
            return self.case.review()

    def test_completed_ab_recover_before_approval_without_more_calls(self):
        self.assertEqual(self.break_final_commit()['status'], 'blocked')
        self.assertEqual(len(self.files('result.json')), 2)
        self.assertFalse(self.case.response.exists())
        result = self.case.review(self.never_call)
        self.assertEqual(result['status'], 'approved')
        self.assertEqual(len(result['model_receipts']), 2)
        self.assertEqual(self.case.calls, ['a', 'b'])

    def test_completed_a_reused_and_only_unstarted_b_called(self):
        original = receipts.atomic_json
        def fail(path, value):
            original(path, value)
            if Path(path).name == 'result.json' and Path(path).parent.name.endswith('-a'):
                raise OSError('injected after A result committed')
        with patch.object(receipts, 'atomic_json', side_effect=fail):
            self.assertEqual(self.case.review()['status'], 'blocked')
        self.assertEqual(self.case.calls, ['a'])
        self.assertEqual(self.case.review()['status'], 'approved')
        self.assertEqual(self.case.calls, ['a', 'b'])

    def test_uncertain_b_stays_owned_across_replay(self):
        def unknown(cfg, role, prompt, folder):
            if role == 'b':
                self.case.calls.append(role)
                raise subprocess.TimeoutExpired('control', 30)
            return self.case.caller(cfg, role, prompt, folder)
        self.assertEqual(self.case.review(unknown)['status'], 'blocked')
        self.assertEqual(self.case.review(self.never_call)['status'], 'blocked')
        self.assertEqual(self.case.calls, ['a', 'b'])
        self.assertFalse(self.case.response.exists())

    def test_return_without_completion_receipt_stays_unknown_not_retried(self):
        with patch.object(receipts, 'complete', side_effect=OSError('not completed')):
            self.assertEqual(self.case.review()['status'], 'blocked')
        self.assertEqual(self.case.review(self.never_call)['status'], 'blocked')
        self.assertEqual(self.case.calls, ['a'])

    def test_replay_keeps_original_context_when_snapshot_changes(self):
        self.break_final_commit()
        self.case.state['elapsed_seconds'] = 4321
        self.assertEqual(self.case.review(self.never_call)['status'], 'approved')
        self.assertEqual(self.case.calls, ['a', 'b'])

    def test_invalid_completed_a_replays_validation_feedback(self):
        valid = copy.deepcopy(self.case.draft)
        def correct(cfg, role, prompt, folder):
            value = self.case.caller(cfg, role, prompt, folder)
            if Path(folder).name == '001-a':
                value['facts'][0]['value'] = 0
            return value
        original = store.atomic_json
        def fail(path, value):
            if Path(path).name == 'commit.json':
                raise OSError('before final approval')
            original(path, value)
        with patch.object(store, 'atomic_json', side_effect=fail):
            self.assertEqual(self.case.review(correct)['status'], 'blocked')
        self.assertEqual(self.case.review(self.never_call)['status'], 'approved')
        self.assertEqual(json.loads(self.case.response.read_bytes()), valid)
        self.assertEqual(self.case.calls, ['a', 'a', 'b'])

    def test_b_revise_then_replay_exact_previous_challenge(self):
        def revise(cfg, role, prompt, folder):
            value = self.case.caller(cfg, role, prompt, folder)
            if Path(folder).name == '001-b':
                value.update(verdict='revise', challenges=[{'id':'missing-limit','basis':'Explain fixture limits',
                    'resolution_criterion':'Explicit control-only rationale','severity':'major','status':'open'}])
            if Path(folder).name == '002-a':
                value['reason'] += '; control fixture only'
            if Path(folder).name == '002-b':
                from task_pipeline import digest
                approved = copy.deepcopy(self.case.draft)
                approved['reason'] += '; control fixture only'
                value['draft_digest'] = digest(approved)
            return value
        original = store.atomic_json
        def fail(path, value):
            if Path(path).name == 'commit.json':
                raise OSError('before final approval')
            original(path, value)
        with patch.object(store, 'atomic_json', side_effect=fail):
            self.assertEqual(self.case.review(revise)['status'], 'blocked')
        self.assertEqual(self.case.review(self.never_call)['status'], 'approved')
        self.assertEqual(self.case.calls, ['a', 'b', 'a', 'b'])

    def test_changed_result_blocks_replay_before_new_call(self):
        self.break_final_commit()
        path = self.files('result.json')[0]
        value = json.loads(path.read_bytes())
        value['response']['reason'] = 'Changed'
        atomic_json(path, value)
        self.assertEqual(self.case.review(self.never_call)['status'], 'blocked')
        self.assertEqual(self.case.calls, ['a', 'b'])

    def test_changed_prompt_or_intent_cannot_charge_again(self):
        self.break_final_commit()
        prompt = self.files('prompt.txt')[0]
        prompt.write_text('Different prompt', encoding='utf-8')
        self.assertEqual(self.case.review(self.never_call)['status'], 'blocked')
        self.assertEqual(self.case.calls, ['a', 'b'])

    def test_unknown_call_cannot_be_replaced_after_pause_resume(self):
        def unknown(*args):
            raise OSError('unknown')
        self.case.review(unknown)
        worker.control(self.case.root, True)
        worker.control(self.case.root, False)
        with self.assertRaisesRegex(ValueError, 'unreconciled_decision_claim'):
            self.case.review(self.never_call)

    def test_cli_completion_reconciles_before_outer_result(self):
        def fake_cli(argv, **kwargs):
            output = Path(argv[argv.index('--output-last-message') + 1])
            atomic_json(output, self.case.draft)
            kwargs['stdout'].write(b'{"type":"turn.completed"}\n')
            return subprocess.CompletedProcess(argv, 0)
        def cli_a(cfg, role, prompt, folder):
            if role == 'a':
                self.case.calls.append(role)
                return worker.model_call(cfg, role, prompt, folder)
            return self.case.caller(cfg, role, prompt, folder)
        with patch.object(worker.subprocess, 'run', side_effect=fake_cli), patch.object(receipts, 'complete', side_effect=OSError('before outer receipt')):
            self.assertEqual(self.case.review(cli_a)['status'], 'blocked')
        self.assertEqual(len(self.files('completed.json')), 1)
        self.assertFalse(self.files('result.json'))
        def only_b(cfg, role, prompt, folder):
            self.assertEqual(role, 'b')
            return self.case.caller(cfg, role, prompt, folder)
        self.assertEqual(self.case.review(only_b)['status'], 'approved')
        self.assertEqual(self.case.calls, ['a', 'b'])

    def test_raw_output_without_completion_is_not_success(self):
        def raw_only(cfg, role, prompt, folder):
            atomic_json(Path(folder) / 'response.json', self.case.draft)
            raise OSError('transport unknown')
        self.assertEqual(self.case.review(raw_only)['status'], 'blocked')
        self.assertEqual(self.case.review(self.never_call)['status'], 'blocked')
        self.assertFalse(self.case.response.exists())

    def test_receipt_changes_after_approval_block_coordinator(self):
        self.case.review()
        path = self.files('result.json')[0]
        value = json.loads(path.read_bytes())
        value['completed_at'] += 1
        atomic_json(path, receipts.seal(value))
        entered = []
        with self.assertRaisesRegex(ValueError, 'approved_model_receipt_changed'):
            worker.advance_checked(self.case.config, self.case.root, lambda *a, **k: entered.append(True))
        self.assertEqual(entered, [])

    def test_existing_v2_attempt_cannot_be_hot_migrated(self):
        self.case.config['research_worker']['version'] = 2
        atomic_json(self.case.config['research_worker']['project_path'], self.case.config)
        self.case.review()
        self.case.config['research_worker']['version'] = 3
        atomic_json(self.case.config['research_worker']['project_path'], self.case.config)
        with self.assertRaises(ValueError):
            self.case.review(self.never_call)

    @unittest.skipUnless(os.name == 'nt', 'Windows MAX_PATH boundary')
    def test_atomic_temp_can_exceed_max_path_while_destination_fits(self):
        parent = self.case.root / ('p' * (220 - len(str(self.case.root))))
        path = parent / 'value.json'
        self.assertLess(len(str(path)), 260)
        self.assertGreater(len(str(path)) + 38, 260)
        atomic_json(path, {'observed_windows_boundary': True})
        self.assertTrue(json.loads(path.read_bytes())['observed_windows_boundary'])


if __name__ == '__main__':
    unittest.main()
