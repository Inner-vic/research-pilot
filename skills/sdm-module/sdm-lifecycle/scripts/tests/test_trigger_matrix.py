"""CPU coordinator journeys: producer visibility and pre-acquisition bindings."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

import task_pipeline as pipeline
from trigger_matrix import inspect


class TriggerMatrixTests(unittest.TestCase):
    def setUp(self):
        from test_orchestration import OrchestrationTests
        self.f = OrchestrationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_all_profiles_have_exactly_34_unique_trigger_entries(self):
        for objective in ('execute-and-learn', 'evaluate-only', 'baseline-reproduction'):
            for changed in (False, True):
                task = {**self.f.task, 'objective': objective, 'code_change': changed}
                rows = inspect(task, pipeline.graph(task))['nodes']
                self.assertEqual(len(rows), 34)
                self.assertEqual(len({r['display_id'] for r in rows}), 34)
                self.assertTrue(all(r['producer'] and r['trigger'] for r in rows))

    def test_gate_is_not_an_executor_and_ignored_binding_is_visible(self):
        task = self.f.task
        report = inspect(task, pipeline.graph(task))
        self.assertIn('19', report['ignored_builtin_drivers'])
        row = next(r for r in report['nodes'] if r['node'] == '19')
        self.assertEqual(row['executor_kind'], 'record-gate')
        self.assertEqual(row['binding'], 'record-present-not-validated')

    def test_missing_evaluation_driver_blocks_before_any_compute(self):
        f = self.f
        del f.task['drivers']['16']
        seen = []
        result = pipeline.coordinate(f.task, f.root/'missing-driver', f.driver(seen))
        self.assertFalse(set(seen) & __import__('orchestration').COMPUTE_NODES)
        self.assertNotIn('cleanup', seen)
        self.assertEqual(result['nodes']['14']['reason'], 'execution_bindings_incomplete_before_acquisition')
        self.assertIn('16', result['nodes']['14']['missing_drivers'])

    def test_missing_record_waits_with_producer_then_resumes_without_replay(self):
        f = self.f
        Path(f.records['confirmation']['path']).unlink()
        seen = []
        root = f.root/'wait'
        result = pipeline.coordinate(f.task, root, f.driver(seen))
        self.assertEqual(result['waiting']['next_action']['action'], 'obtain-direct-user-confirmation')
        self.assertFalse(result['waiting']['next_action']['automatic_producer_bound'])
        before = list(seen)
        f.put('confirmation', **{k:v for k,v in f.confirmation.items() if k != 'schema'})
        result = pipeline.coordinate(f.task, root, f.driver(seen))
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(all(seen.count(k) == 1 for k in before))

    def test_missing_cleanup_blocks_before_acquisition(self):
        f = self.f
        del f.task['cleanup_driver']
        seen = []
        result = pipeline.coordinate(f.task, f.root/'no-cleanup', f.driver(seen))
        self.assertEqual(result['nodes']['14']['missing_drivers'], ['cleanup'])
        self.assertFalse(set(seen) & __import__('orchestration').COMPUTE_NODES)

    def test_record_collision_is_an_acquisition_gap_not_a_duplicate_pass(self):
        from trigger_matrix import acquisition_gaps
        f = self.f
        f.records['conclusion_quality'] = dict(f.records['evidence_consistency'])
        self.assertIn('record-path-collision:19,32', acquisition_gaps(f.task,pipeline.graph(f.task),{}))

    def test_optional_not_selected_is_unmeasured_not_passed(self):
        f = self.f
        del f.task['drivers']['17']
        result = pipeline.coordinate(f.task, f.root/'optional', f.driver([]))
        self.assertIn('17', result['coverage']['unmeasured'])
        self.assertNotIn('17', result['coverage']['completed_project_drivers'])

    def test_shared_record_path_collision_is_visible_without_loading_records(self):
        f = self.f
        f.records['method_review'] = dict(f.records['research'])
        report = inspect(f.task, pipeline.graph(f.task))
        self.assertIn(['24', '25'], report['record_path_collisions'])

    def test_public_cli_inspection_does_not_invoke_any_driver_or_create_run(self):
        f = self.f
        source = f.root/'task.json'
        source.write_text(json.dumps(f.task), encoding='utf-8')
        output = f.root/'not-created'
        result = subprocess.run([sys.executable, '-B', str(Path(pipeline.__file__)),
            '--task', str(source), '--output', str(output), '--inspect-triggers'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)['nodes']), 34)
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
