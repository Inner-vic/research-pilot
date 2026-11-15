import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import project_navigation as n
import run_project_pipeline as pipeline


class GenericProjectTests(unittest.TestCase):
    def test_project_catalog_keeps_failed_rounds_without_cross_project_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('project-a', 'project-b'):
                repo = root / name
                folder = repo / 'experiments' / name / 'round-01'
                folder.mkdir(parents=True)
                (repo / 'experiments/README.md').write_text('custom navigation')
                (folder / 'result.json').write_text(json.dumps({'run_id': name, 'round': 1,
                    'status': 'failed', 'analysis': None}))
                (folder / 'report.md').write_text('real failure observation')
                target, content = n.build(repo, 'experiments', {'branch': 'feature/custom',
                    'remote_url': f'https://git.example.invalid/person/{name}.git'})
                self.assertEqual(target, repo / 'experiments/catalog.md')
                text = content.decode()
                self.assertIn('failed', text)
                self.assertIn(name, text)
                self.assertNotIn('project-b' if name == 'project-a' else 'project-a', text)
                self.assertIn('feature%2Fcustom', text)
                self.assertEqual((repo / 'experiments/README.md').read_text(), 'custom navigation')

    def test_continuous_provider_respects_project_requested_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); observed = []
            def coordinate(task, output):
                observed.append(task)
                return {'status': 'complete', 'cleanup': {'status': 'completed'}}
            cfg = {'provider': 'another-project-v1', 'run_id': 'generic',
                   'windows_python_wsl': 'unused', 'code_change': True,
                   'robustness_required': True, 'baseline_required': True}
            with patch.object(pipeline, 'resolve_driver', return_value=root / 'driver.py'), \
                    patch.object(pipeline, 'windows', side_effect=str), \
                    patch.object(pipeline, 'coordinate', side_effect=coordinate):
                pipeline.run(cfg, root / 'run')
            task = observed[0]
            for key in ('5', '9', '10', '12', '17'):
                self.assertIn(key, task['drivers'])
            self.assertNotIn('11', task['drivers'])
            self.assertTrue(task['robustness_required'])
            self.assertFalse(any(d.get('read_only') for d in task['drivers'].values()))

    def test_default_execution_does_not_force_baseline_or_robustness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); observed = []
            def coordinate(task, output):
                observed.append(task)
                return {'status': 'complete', 'cleanup': {'status': 'completed'}}
            with patch.object(pipeline, 'resolve_driver', return_value=root / 'driver.py'), \
                    patch.object(pipeline, 'windows', side_effect=str), \
                    patch.object(pipeline, 'coordinate', side_effect=coordinate):
                pipeline.run({'provider': 'another-project-v1', 'run_id': 'generic',
                              'windows_python_wsl': 'unused'}, root / 'run')
            self.assertNotIn('5', observed[0]['drivers'])
            self.assertNotIn('17', observed[0]['drivers'])

    def test_navigation_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError): n.build(tmp, '../outside', {})
