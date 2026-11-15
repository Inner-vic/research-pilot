import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('next_execution_action',Path(__file__).parents[1]/'next_execution_action.py')
p=importlib.util.module_from_spec(spec); spec.loader.exec_module(p)

class DecisionTests(unittest.TestCase):
    def test_bad_scores_and_gradient_issues_do_not_trigger_more_gpu(self):
        summary={'outcomes':{'15':{'status':'completed','surface_miou':0.0},'16':{'status':'completed'}},
                 'issues':[{'nonfinite_gradient_count':7}]}
        d=p.decide(summary,1,2)
        self.assertEqual(d['action'],'stop-completed'); self.assertEqual(d['additional_gpu_runs'],0)
        self.assertEqual(d['issues'],summary['issues'])

    def test_confirmed_precompute_resource_failure_gets_one_fresh_attempt(self):
        s={'outcomes':{'15':{'status':'failed','reason':'selected_gpu_unavailable','gpu_compute_invoked':False}}}
        self.assertEqual(p.decide(s,1,2)['action'],'retry-resource')
        self.assertEqual(p.decide(s,2,2)['action'],'diagnose-and-stop')

    def test_failure_after_gpu_invocation_is_not_blindly_retried(self):
        s={'outcomes':{'15':{'status':'failed','reason':'selected_gpu_unavailable','gpu_compute_invoked':True}}}
        self.assertEqual(p.decide(s,1,2)['action'],'diagnose-and-stop')

    def test_permission_failure_and_uncertain_effect_are_kept(self):
        s={'outcomes':{'15':{'status':'failed','reason':'collection_interface_failed','uncertain_effect':True}}}
        self.assertEqual(p.decide(s,1,2)['action'],'reconcile-and-stop')
        s['outcomes']['15']={'status':'failed','reason':'permission_denied','gpu_compute_invoked':False}
        self.assertEqual(p.decide(s,1,2)['action'],'diagnose-and-stop')

    def test_unbounded_loop_is_rejected(self):
        with self.assertRaises(ValueError): p.decide({},1,99)

    def test_uncertain_resource_failure_cannot_authorize_retry(self):
        s={'outcomes':{'15':{'status':'failed','reason':'selected_gpu_unavailable',
                            'gpu_compute_invoked':False,'uncertain_effect':True}}}
        self.assertEqual(p.decide(s,1,2)['action'],'reconcile-and-stop')

if __name__=='__main__': unittest.main()
