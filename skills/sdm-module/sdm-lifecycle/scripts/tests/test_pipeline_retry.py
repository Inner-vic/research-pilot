"""Provider retries must respect uncertain effects and cleanup outcomes."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parents[1]))
import run_project_pipeline as pipeline


class RetryTests(unittest.TestCase):
    def run_case(self,uncertain=False,cleanup='completed'):
        calls=[]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); driver=root/'driver.py'; driver.write_text('# isolated adapter binding\n')
            config={'supported_graph_revisions':[3],'provider':'isolated-cpu-fixture','run_id':'retry-test','max_attempts':1,
                    'driver_runtime':{'mode':'native','python':sys.executable},
                    'project_driver':{'path':str(driver),'digest':'sha256:'+hashlib.sha256(driver.read_bytes()).hexdigest()}}
            def coordinate(task,attempt):
                calls.append(attempt.name)
                (attempt/'decision.json').write_text(json.dumps({'action':'retry-resource'}))
                return {'nodes':{'15':{'status':'failed','uncertain_effect':uncertain}},
                        'cleanup':{'status':cleanup}}
            with patch.object(pipeline,'coordinate',side_effect=coordinate):
                result=pipeline.run(config,root/'run')
        return calls,result

    def test_unknown_effect_blocks_resource_retry_even_after_cleanup(self):
        calls,result=self.run_case(uncertain=True)
        self.assertEqual(calls,['attempt-01'])
        self.assertTrue(result['attempts'][0]['nodes']['15']['uncertain_effect'])

    def test_known_failure_does_not_automatically_start_another_attempt(self):
        calls,_=self.run_case()
        self.assertEqual(calls,['attempt-01'])

    def test_failed_cleanup_blocks_successor_attempt(self):
        calls,_=self.run_case(cleanup='failed')
        self.assertEqual(calls,['attempt-01'])


if __name__=='__main__': unittest.main()
