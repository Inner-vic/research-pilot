import importlib.util
from pathlib import Path
import tempfile
import unittest
from threading import Event
import sys
import json
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('task_pipeline',Path(__file__).parents[1]/'task_pipeline.py')
p=importlib.util.module_from_spec(spec); spec.loader.exec_module(p)

class PipelineTests(unittest.TestCase):
    def setUp(self):
        from test_orchestration import OrchestrationTests
        self.fixture=OrchestrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def task(self):
        from copy import deepcopy
        return deepcopy(self.fixture.task)

    def test_baseline_failure_does_not_block_training_and_cleanup_runs(self):
        seen=[]
        def driver(d,r,k,b):
            seen.append(k); return {'status':'failed' if k=='5' else 'completed'}
        with tempfile.TemporaryDirectory() as root:
            result=p.coordinate(self.task(),root,driver)
        self.assertIn('15',seen); self.assertIn('16',seen); self.assertLess(seen.index('cleanup'),seen.index('21'))
        self.assertEqual(result['nodes']['5']['status'],'failed')
        self.assertFalse(result['numbered_gates_advanced'])

    def test_training_failure_still_summarizes_and_decides(self):
        seen=[]
        def driver(d,r,k,b):
            seen.append(k); return {'status':'failed' if k=='15' else 'completed'}
        with tempfile.TemporaryDirectory() as root:
            result=p.coordinate(self.task(),root,driver)
        self.assertNotIn('16',seen)
        for k in ('18','21','cleanup'): self.assertIn(k,seen)
        self.assertEqual(result['nodes']['20']['status'],'completed')
        self.assertEqual(result['nodes']['16']['status'],'skipped')

    def test_resume_does_not_repeat_finished_external_actions(self):
        seen=[]
        def driver(d,r,k,b): seen.append(k); return {'status':'completed'}
        with tempfile.TemporaryDirectory() as root:
            task=self.task(); p.coordinate(task,root,driver); first=list(seen)
            p.coordinate(task,root,driver)
            self.assertEqual(seen,first)

    def test_data_failure_blocks_gpu_but_keeps_failure_summary(self):
        seen=[]
        def driver(d,r,k,b): seen.append(k); return {'status':'failed' if k=='3' else 'completed'}
        with tempfile.TemporaryDirectory() as root:
            p.coordinate(self.task(),root,driver)
        self.assertNotIn('15',seen); self.assertIn('18',seen); self.assertNotIn('cleanup',seen)

    def test_missing_driver_remains_visible(self):
        with tempfile.TemporaryDirectory() as root:
            result=p.coordinate({'objective':'execute-and-learn'},root)
        self.assertEqual(result['status'],'needs-driver')
        self.assertIn('15',result['remaining'])

    def test_merge_is_not_implicitly_authorized(self):
        with self.assertRaisesRegex(ValueError,'merge_or_publish'):
            p.graph({'objective':'execute-and-learn','merge':True})

    def test_code_changes_require_engineering_check_before_execution(self):
        nodes=p.graph({'objective':'execute-and-learn','code_change':True})
        self.assertIn('12',nodes['13']['depends'])
        self.assertNotIn('11',nodes['13']['depends'])
        self.assertNotIn('5',nodes['15']['depends'])

    def test_baseline_only_blocks_when_explicitly_required(self):
        default=p.graph({'objective':'execute-and-learn'})
        required=p.graph({'objective':'execute-and-learn','baseline_required':True})
        self.assertEqual(default['5']['policy'],'record')
        self.assertNotIn('5',default['13']['depends'])
        self.assertEqual(required['5']['policy'],'required')
        self.assertIn('5',required['15']['depends'])
        self.assertNotIn('5',required['13']['depends'])

    def test_evaluate_only_never_dispatches_training(self):
        seen=[]
        def driver(d,r,k,b): seen.append(k); return {'status':'completed'}
        task=self.task();task['objective']='evaluate-only'
        with tempfile.TemporaryDirectory() as root: result=p.coordinate(task,root,driver)
        self.assertNotIn('15',seen)
        self.assertIn('16',seen)
        self.assertIn('18',seen)
        self.assertEqual(result['nodes']['15']['reason'],'outside_requested_objective')

    def test_baseline_goal_summarizes_failure_without_training(self):
        seen=[]
        def driver(d,r,k,b): seen.append(k); return {'status':'failed' if k=='5' else 'completed'}
        task=self.task();task['objective']='baseline-reproduction'
        with tempfile.TemporaryDirectory() as root: result=p.coordinate(task,root,driver)
        self.assertNotIn('15',seen);self.assertNotIn('16',seen)
        for k in ('5','18','21','cleanup'): self.assertIn(k,seen)
        self.assertEqual(result['nodes']['5']['status'],'failed')

    def test_resource_capable_baseline_is_serialized_even_when_mislabelled_read_only(self):
        seen=[]
        def driver(d,r,k,b):
            seen.append(k)
            return {'status':'completed'}
        task=self.task(); task['drivers']['5']['read_only']=True
        with tempfile.TemporaryDirectory() as root: p.coordinate(task,root,driver)
        self.assertLess(seen.index('5'),seen.index('15'))

    def test_real_driver_nonzero_exit_preserves_failed_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); receipt=root/'failed.json'
            code="import json,sys;json.dump({'task_node':'3','status':'failed','reason':'actual-check-failed'},open(sys.argv[1],'w'));sys.exit(3)"
            result=p.execute({'argv':[sys.executable,'-c',code,str(receipt)],'receipt':str(receipt)},root,'3',10)
            self.assertEqual(result['status'],'failed'); self.assertEqual(result['driver_exit_code'],3)
            self.assertEqual(result['observation']['reason'],'actual-check-failed')

    def test_interrupted_driver_does_not_blindly_repeat_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); driver={'argv':['must-not-run'],'receipt':str(root/'missing.json')}
            (root/'3-started.json').write_text(json.dumps({'driver_digest':p.digest(driver)}))
            result=p.execute(driver,root,'3',10)
            self.assertEqual(result['reason'],'uncertain_effect_requires_platform_reconciliation')

    def test_boolean_is_not_an_accepted_platform_receipt(self):
        self.assertFalse(p.verified_s13(True))

    def test_baseline_exit_zero_without_real_result_is_not_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);receipt=root/'baseline.json'
            code="import json,sys;json.dump({'task_node':'5','status':'completed'},open(sys.argv[1],'w'))"
            result=p.execute({'argv':[sys.executable,'-c',code,str(receipt)],'receipt':str(receipt)},root,'5',10)
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['reason'],'completed_execution_requires_accepted_s13')

    def test_pause_recovers_durable_receipt_before_journal_without_redispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); count=root/'dispatch-count'; receipt=root/'train.json'
            code="import json,sys;from pathlib import Path;Path(sys.argv[1]).write_text('one');json.dump({'task_node':'15','status':'failed','reason':'confirmed-failure'},open(sys.argv[2],'w'));sys.exit(3)"
            task=self.task(); task['pause_after_receipt']='15'
            task['drivers']['15']={'argv':[sys.executable,'-c',code,str(count),str(receipt)],'receipt':str(receipt)}
            calls=[]
            def driver(d,r,k,b):
                if k=='15': return p.execute(d,r,k,b)
                calls.append(k); return {'status':'completed'}
            first=p.coordinate(task,root,driver)
            self.assertEqual(first['status'],'paused'); self.assertNotIn('15',first['nodes'])
            self.assertEqual(first['cleanup']['status'],'completed')
            before=count.stat().st_mtime_ns
            second=p.coordinate(task,root,driver)
            self.assertEqual(second['nodes']['15']['status'],'failed')
            self.assertEqual(second['nodes']['15']['driver_exit_code'],3)
            self.assertEqual(count.stat().st_mtime_ns,before)
            self.assertIn('21',calls)

    def test_unaccepted_failed_s13_cannot_authorize_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); receipt=root/'failed.json'
            code="import json,sys;json.dump({'task_node':'15','status':'failed','signed_result':{'request_id':'unaccepted'}},open(sys.argv[1],'w'));sys.exit(3)"
            with patch.object(p,'verified_s13',return_value=False):
                result=p.execute({'argv':[sys.executable,'-c',code,str(receipt)],'receipt':str(receipt)},root,'15',10)
            self.assertEqual(result['reason'],'failed_execution_feedback_not_verified')
            self.assertTrue(result['uncertain_effect'])

    def test_lightweight_progress_distinguishes_completed_from_optional_and_reused(self):
        task=self.task()
        for k in ('5','17','19'): task['drivers'].pop(k)
        with tempfile.TemporaryDirectory() as tmp:
            result=p.coordinate(task,tmp,lambda *args: {'status':'completed'})
        coverage=result['coverage']
        self.assertEqual(coverage['total_nodes'],34)
        self.assertEqual(len(coverage['completed']),27)
        self.assertEqual(coverage['not_applicable'],['9','10','11','12','23'])
        self.assertEqual(coverage['recorded'],['5','17'])
        self.assertEqual(coverage['required_completed_count'],27)
        self.assertFalse(result['numbered_gates_advanced'])

    def test_driver_exception_retains_failure_summary_cleanup_and_does_not_repeat(self):
        calls=[]
        def driver(d,r,k,b):
            calls.append(k)
            if k=='15': raise RuntimeError('isolated driver fault')
            return {'status':'completed'}
        with tempfile.TemporaryDirectory() as tmp:
            task=self.task()
            result=p.coordinate(task,tmp,driver)
            first=list(calls)
            p.coordinate(task,tmp,driver)
            self.assertEqual(calls,first)
            self.assertTrue((Path(tmp)/'summary.json').exists())
        self.assertEqual(result['nodes']['15']['status'],'failed')
        self.assertTrue(result['nodes']['15']['uncertain_effect'])
        self.assertNotIn('16',calls)
        for k in ('18','21','cleanup'): self.assertIn(k,calls)

    def test_json_array_receipt_is_retained_as_failure_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); receipt=root/'array.json'
            code="import sys;open(sys.argv[1],'w').write('[]')"
            result=p.execute({'argv':[sys.executable,'-c',code,str(receipt)],'receipt':str(receipt)},root,'3',10)
            self.assertEqual(result['status'],'failed')
            self.assertTrue(result['uncertain_effect'])
            self.assertEqual(result['receipt_digest'],p.digest_bytes(receipt.read_bytes()))
            self.assertTrue((root/'3-invocation.json').exists())

    def test_nonzero_child_cannot_report_completed_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); receipt=root/'contradiction.json'
            code="import json,sys;json.dump({'task_node':'3','status':'completed'},open(sys.argv[1],'w'));sys.exit(7)"
            result=p.execute({'argv':[sys.executable,'-c',code,str(receipt)],'receipt':str(receipt)},root,'3',10)
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['exit_code'],7)
            self.assertTrue(result['uncertain_effect'])

    def test_cleanup_exception_is_visible_and_cannot_be_reported_as_stopped(self):
        def driver(d,r,k,b):
            if k=='cleanup': raise OSError('isolated stop fault')
            return {'status':'completed'}
        with tempfile.TemporaryDirectory() as tmp:
            result=p.coordinate(self.task(),tmp,driver)
            stored=json.loads((Path(tmp)/'summary.json').read_bytes())
            self.assertFalse((Path(tmp)/'coordinator.lock').exists())
        self.assertEqual(result['status'],'cleanup-required')
        self.assertEqual(stored['cleanup']['status'],'failed')
        self.assertTrue(stored['cleanup']['uncertain_effect'])

    def test_invalid_budget_is_rejected_before_any_driver_or_cleanup(self):
        for budget in (True,0,-1,'bad'):
            task=self.task(); task['timeout_seconds']=budget; calls=[]
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError,'bounded_task_budget_required'):
                    p.coordinate(task,tmp,lambda d,r,k,b: calls.append(k))
            self.assertEqual(calls,[])

if __name__=='__main__': unittest.main()
