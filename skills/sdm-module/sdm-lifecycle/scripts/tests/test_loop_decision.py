import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('loop_decision',Path(__file__).parents[1]/'loop_decision.py')
p=importlib.util.module_from_spec(spec); spec.loader.exec_module(p)

def record(amp=True,rate=4e-5,nonfinite=7):
    return {'parameters':{'amp':amp,'learning_rate':rate},'changed_variable':None,
            'training':{'status':'completed','actual':{'exit_code':0,'iteration_evidence_complete':True,
                'gradient_record_count':8,'nonfinite_gradient_count':nonfinite}},
            'evaluation':{'status':'completed','actual':{'surface_miou':10.0}}}

class LoopDecisionTests(unittest.TestCase):
    def test_nonfinite_gradient_tests_precision_only(self):
        decision=p.analyze([record()],3)
        self.assertEqual(decision['next_parameters'],{'amp':False,'learning_rate':4e-5})
        self.assertEqual(decision['changed_variable'],'amp')

    def test_next_decision_uses_actual_precision_observation(self):
        first=record(); second=record(False,nonfinite=0); second['changed_variable']='amp'
        decision=p.analyze([first,second],3)
        self.assertEqual(decision['differences_from_previous']['nonfinite_gradients'],-7)
        self.assertEqual(decision['next_parameters'],{'amp':False,'learning_rate':2e-5})

    def test_persistent_numerical_failure_can_be_observed_without_claiming_success(self):
        second=record(False); second['training']['status']='failed'; second['evaluation']['status']='skipped'
        decision=p.analyze([record(),second],3)
        self.assertEqual(decision['action'],'run-next-round')
        self.assertEqual(decision['changed_variable'],'learning_rate')

    def test_three_rounds_stop_even_if_model_quality_is_poor(self):
        history=[record(),record(False),record(False,2e-5)]
        self.assertEqual(p.analyze(history,3)['action'],'stop-budget')

    def test_unknown_effect_never_starts_another_round(self):
        first=record(); first['training']['uncertain_effect']=True
        self.assertEqual(p.analyze([first],3)['action'],'stop-reconcile')

    def test_crash_timeout_or_missing_observation_stops(self):
        for key,value in [('exit_code',137),('timed_out',True),('gradient_record_count',0)]:
            first=record(); first['training']['actual'][key]=value
            self.assertTrue(p.analyze([first],3)['action'].startswith('stop-'))

    def test_failed_evaluation_cannot_trigger_more_training(self):
        first=record(); first['evaluation']['status']='failed'
        self.assertEqual(p.analyze([first],3)['action'],'stop-evaluation-failure')

    def test_healthy_baseline_selects_rate_instead_of_precision(self):
        decision=p.analyze([record(nonfinite=0)],3)
        self.assertEqual(decision['changed_variable'],'learning_rate')
        self.assertTrue(decision['next_parameters']['amp'])

    def test_no_repeated_or_unbounded_candidates(self):
        first=record(False,nonfinite=0); second=record(False,2e-5,0); second['changed_variable']='learning_rate'
        self.assertEqual(p.analyze([first,second],3)['action'],'stop-no-next-hypothesis')
        with self.assertRaises(ValueError): p.analyze([record()],4)
        with self.assertRaises(ValueError): p.changed({'amp':True},{'amp':False,'extra':1})

if __name__=='__main__': unittest.main()
