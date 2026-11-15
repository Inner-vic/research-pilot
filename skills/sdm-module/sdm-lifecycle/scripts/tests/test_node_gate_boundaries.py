"""Distinct node acceptance, shared observations, no model/platform calls."""
from copy import deepcopy
from pathlib import Path
import json
import unittest
import orchestration as o
import task_pipeline as pipeline


class NodeBoundaryTests(unittest.TestCase):
    def setUp(self):
        from test_orchestration import OrchestrationTests
        self.f=OrchestrationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_protocol_completion_does_not_require_method_assessment(self):
        f=self.f
        research=deepcopy(f.research);research.pop('method_checks')
        f.put('research',**{k:v for k,v in research.items() if k!='schema'})
        Path(f.records['method_review']['path']).unlink()
        self.assertEqual(o.gate(f.task,'24')['status'],'completed')
        self.assertEqual(o.gate(f.task,'25')['status'],'awaiting-evidence')

    def test_protocol_can_exist_while_method_rejects_schedule(self):
        f=self.f;f.update('research',schedule={'unit':'updates','horizon':10,'stop_at':11,'warmup':0})
        f.update('method_review',research_digest=o.value_digest(o.needed(f.task,'research')))
        self.assertEqual(o.gate(f.task,'24')['status'],'completed')
        with self.assertRaisesRegex(ValueError,'inconsistent_training_schedule'):o.gate(f.task,'25')

    def test_dialogue_completion_does_not_pass_inconsistent_evidence(self):
        f=self.f
        f.update('evidence_consistency',status='needs-revision',checks=[{
            'claim':'success','evidence_ref':'fixture-failure','finding':'raw evidence reports failure','consistent':False}])
        self.assertEqual(o.gate(f.task,'31')['status'],'completed')
        self.assertEqual(o.gate(f.task,'19')['status'],'awaiting-review')

    def test_contradiction_cannot_be_labelled_consistency_pass(self):
        f=self.f;checks=deepcopy(f.consistency['checks']);checks[0]['consistent']=False
        f.update('evidence_consistency',checks=checks)
        with self.assertRaisesRegex(ValueError,'contradiction_cannot_pass'):o.gate(f.task,'19')

    def test_consistent_evidence_does_not_imply_sufficient_conclusion(self):
        f=self.f;f.update('conclusion_quality',status='needs-revision',blocking_gaps=['comparison not established'])
        self.assertEqual(o.gate(f.task,'19')['status'],'completed')
        self.assertEqual(o.gate(f.task,'32')['status'],'awaiting-review')
        f.update('conclusion_quality',status='passed')
        with self.assertRaisesRegex(ValueError,'quality_gaps_prevent'):o.gate(f.task,'32')

    def test_review_bindings_cannot_be_reused_for_changed_response(self):
        f=self.f;f.update('review_response',reason='revised conclusion')
        with self.assertRaisesRegex(ValueError,'consistency_response_mismatch'):o.gate(f.task,'19')

    def test_recommendation_is_not_published_as_final_decision_and_resume_is_exact(self):
        f=self.f;transition=o.needed(f.task,'transition');Path(f.records['transition']['path']).unlink()
        root=f.root/'separate-decision';seen=[]
        waiting=pipeline.coordinate(f.task,root,f.driver(seen))
        self.assertEqual(waiting['waiting_node'],'33')
        self.assertTrue((root/'recommendation.json').exists())
        self.assertFalse((root/'decision.json').exists())
        f.put('transition',**{k:v for k,v in transition.items() if k!='schema'})
        done=pipeline.coordinate(f.task,root,f.driver(seen))
        self.assertEqual(done['status'],'complete')
        self.assertEqual(seen.count('15'),1);self.assertEqual(seen.count('cleanup'),1)
        self.assertEqual(json.loads((root/'decision.json').read_bytes())['action'],'stop')

    def test_decision_must_bind_recommendation_and_reviewed_option(self):
        f=self.f;f.update('transition',next_action_digest='wrong')
        with self.assertRaisesRegex(ValueError,'transition_recommendation_mismatch'):o.gate(f.task,'33')
        f.update('transition',next_action_digest=o.value_digest(f.next_action),action='revise-plan')
        with self.assertRaisesRegex(ValueError,'decision_outside_reviewed_options'):o.gate(f.task,'33')

    def test_prior_graph_policies_and_dependencies_remain_identical(self):
        old=json.loads(Path(__file__).with_name('graph_policy_revision3.json').read_bytes())
        profiles={'training_reuse':{'objective':'execute-and-learn','code_change':False},
                  'training_change':{'objective':'execute-and-learn','code_change':True},
                  'evaluation_only':{'objective':'evaluate-only'},'baseline_reproduction':{'objective':'baseline-reproduction'}}
        for name,task in profiles.items():
            current=pipeline.graph(task);self.assertEqual(len(current),34)
            for k,n in current.items():
                self.assertEqual(n['policy'],old[name][k]['policy'])
                self.assertEqual(n['depends'],old[name][k]['depends'])

    def test_old_explicit_contract_is_not_silently_relabelled(self):
        with self.assertRaisesRegex(ValueError,'historical_gate_contract'):
            pipeline.current_task({'objective':'evaluate-only','gate_contract_revision':1})

    def test_published_artifact_drift_is_rejected_without_redispatch(self):
        for name in ('recommendation','decision'):
            with self.subTest(name=name):
                root=self.f.root/('drift-'+name);seen=[]
                pipeline.coordinate(self.f.task,root,self.f.driver(seen));before=list(seen)
                path=root/(name+'.json');value=json.loads(path.read_bytes())
                value['reason']='changed after acceptance';path.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError,'accepted_artifact_changed:'+name):
                    pipeline.coordinate(self.f.task,root,self.f.driver(seen))
                self.assertEqual(seen,before)

    def test_missing_published_artifact_is_not_silently_accepted(self):
        for name in ('recommendation','decision'):
            with self.subTest(name=name):
                root=self.f.root/('missing-'+name);seen=[]
                pipeline.coordinate(self.f.task,root,self.f.driver(seen));before=list(seen)
                (root/(name+'.json')).unlink()
                with self.assertRaisesRegex(ValueError,'accepted_artifact_missing:'+name):
                    pipeline.coordinate(self.f.task,root,self.f.driver(seen))
                self.assertEqual(seen,before)


if __name__=='__main__':unittest.main()
