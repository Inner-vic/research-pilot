import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1]))
import round_reporting as r


class RoundReportingTests(unittest.TestCase):
    def fixture(self, base):
        root = base / 'run/round-01'; root.mkdir(parents=True)
        r.save_json(root / 'round-plan.json', {'round': 1, 'parameters': {'x': 2}, 'hypothesis': 'fixture'})
        r.save_json(root / 'round-observation.json', {'round': 1, 'training': {'status': 'completed',
                    'actual': {'validation_loss': 3.2}}, 'evaluation': {'status': 'failed'}})
        cfg = {'run_id': 'fixture', 'project_metrics': ['training.val_loss', 'evaluation.arbitrary_score'],
               'report_metric_mapping': {'training.val_loss': 'training.validation_loss'},
               'result_records': {'repository': str(base / 'repo')}}
        return cfg, root

    def test_project_metric_mapping_and_failed_result_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, root = self.fixture(Path(tmp))
            record = r.build(cfg, root, error='fixture exception')
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['metrics']['training.val_loss']['value'], 3.2)
            self.assertIsNone(record['metrics']['evaluation.arbitrary_score']['value'])
            self.assertEqual(record['retention']['status'], 'not-verified')
            self.assertTrue(any('fixture exception' in x for x in record['limitations']))

    def test_export_reentry_does_not_modify_unchanged_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); cfg, root = self.fixture(base)
            result = r.export_round(cfg, root)
            paths = [base / 'repo' / x for x in result['files']]
            before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
            r.export_round(cfg, root)
            self.assertEqual(before, [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths])
            self.assertIn('未取得', paths[1].read_text(encoding='utf-8'))

    def test_opt_in_catalog_is_exported_and_reentry_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,root=self.fixture(Path(tmp))
            cfg['result_records']['navigation']=True
            result=r.export_round(cfg,root)
            self.assertEqual(len(result['files']),4)
            path=Path(cfg['result_records']['repository'])/'docs/sdm/runs/catalog.md'
            before=(path.read_bytes(),path.stat().st_mtime_ns)
            r.export_round(cfg,root)
            self.assertEqual(before,(path.read_bytes(),path.stat().st_mtime_ns))
            self.assertIn('failed',path.read_text(encoding='utf-8'))

    def test_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, root = self.fixture(Path(tmp))
            cfg['result_records']['directory'] = '../outside'
            with self.assertRaises(ValueError): r.export_round(cfg, root)

    def test_failed_sync_is_visible_without_discarding_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, root = self.fixture(Path(tmp))
            cfg['result_records'].update(sync=True, sync_script='missing')
            result = r.deliver(cfg, root)
            self.assertEqual(result['status'], 'delivery-problem')
            self.assertTrue((Path(cfg['result_records']['repository']) / 'docs/sdm/runs/fixture/round-01/result.json').exists())

    def test_interrupted_round_exports_observed_task_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, root = self.fixture(Path(tmp)); (root / 'round-observation.json').unlink()
            r.save_json(root / 'attempt-01/summary.json', {'nodes': {'15': {'status': 'failed',
                'observation': {'status': 'failed', 'actual': {'exit_code': 1}}}}})
            record = r.build(cfg, root)
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['stages']['training']['exit_code'], 1)

    def test_windows_retained_config_is_verified_before_embedding(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            cfg, root = self.fixture(Path(tmp))
            cfg['project_driver'] = {'path': '/project/driver.py', 'digest': 'sha256:fixture'}
            config = root / 'complete-config.py'; config.write_bytes(b'actual_parameter = 3\n')
            checksum = 'sha256:' + hashlib.sha256(config.read_bytes()).hexdigest()
            r.save_json(root / 'artifacts/retention.json', {
                'status': 'verified', 'observation_digest': r.digest(r.read(root / 'round-observation.json')),
                'files': [{'kind': 'configuration', 'status': 'verified-local-copy',
                    'retained_path': 'D:/project/config.py', 'digest': checksum}]})
            with patch.object(r, 'retained_copy', return_value=config):
                record = r.build(cfg, root)
                self.assertEqual(record['provenance']['resolved_configuration_contents'][checksum], 'actual_parameter = 3\n')
                self.assertEqual(record['provenance']['project_adapter'], cfg['project_driver'])
                config.write_bytes(b'changed_parameter = 99\n')
                self.assertEqual(r.build(cfg, root)['provenance']['resolved_configuration_contents'], {})


if __name__ == '__main__': unittest.main()
