import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_diagnostics import PreflightError, RuntimeDiagnostics


class DiagnosticsTests(unittest.TestCase):
    def session(self):
        stream = io.StringIO()
        return RuntimeDiagnostics(stream), stream

    def test_failed_check_retains_identity_expected_actual_before_any_mount(self):
        d, stream = self.session()
        with self.assertRaisesRegex(PreflightError, 'topology.gpus'):
            d.check('topology.gpus', 8, lambda: 4)
        d.finish('failed', reason='preflight_failed:topology.gpus')
        rows = [json.loads(line.split(' ', 1)[1]) for line in stream.getvalue().splitlines()]
        self.assertEqual(rows[1]['actual'], 4)
        self.assertEqual(rows[1]['expected'], 8)
        self.assertEqual(rows[-1]['failed_check'], 'topology.gpus')
        self.assertFalse(rows[-1]['training_started'])
        self.assertFalse(rows[-1]['platform_release_verified'])

    def test_observer_exception_retains_safe_traceback_and_errno(self):
        d, stream = self.session()
        def missing():
            raise FileNotFoundError(2, 'secret raw exception text')
        with self.assertRaises(PreflightError):
            d.check('inputs.index_readable', True, missing)
        failure = next(r for r in d.records if r['event'] == 'exception')
        self.assertEqual(failure['exception_type'], 'FileNotFoundError')
        self.assertEqual(failure['errno'], 2)
        self.assertEqual(failure['traceback'][-1]['function'], 'missing')
        self.assertNotIn('secret raw exception text', stream.getvalue())

    def test_attach_replays_early_checks_and_retains_terminal(self):
        d, _ = self.session()
        d.check('source.commit', 'frozen', lambda: 'frozen')
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(d.attach(directory))
            d.finish('failed')
            rows = [json.loads(line) for line in (Path(directory) / 'runtime-diagnostics.jsonl').read_text().splitlines()]
            self.assertEqual(rows[0]['event'], 'check_started')
            self.assertEqual(rows[-1]['event'], 'terminal')

    def test_existing_journal_is_not_overwritten(self):
        d, _ = self.session()
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / 'runtime-diagnostics.jsonl'
            journal.write_text('original')
            self.assertFalse(d.attach(directory))
            self.assertEqual(journal.read_text(), 'original')

    def test_mount_failure_and_later_write_failure_preserve_stdout(self):
        d, stream = self.session()
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(d.attach(Path(directory) / 'missing'))
            self.assertTrue(d.attach(directory))
            with patch.object(Path, 'open', side_effect=PermissionError(13, 'mount unavailable')):
                d.finish('failed', reason='original_failure')
        rows = [json.loads(line.split(' ', 1)[1]) for line in stream.getvalue().splitlines()]
        self.assertTrue(any(r.get('reason') == 'original_failure' for r in rows))
        self.assertEqual(d.sinks, [])

    def test_explicit_values_are_redacted_and_raw_environment_is_never_read(self):
        d, stream = self.session()
        d.event('fixture', token='do-not-print', url='https://u:p@example.com/x?api_key=do-not-print', count=8)
        self.assertNotIn('do-not-print', stream.getvalue())
        self.assertNotIn('u:p@', stream.getvalue())
        self.assertEqual(d.records[-1]['count'], 8)


if __name__ == '__main__':
    unittest.main()
