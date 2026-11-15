"""Exercise revision 3 ownership, acquisition ordering and purpose-bound reviews."""
from copy import deepcopy
import unittest

from test_orchestration import OrchestrationTests
import task_pipeline as p
import orchestration as o
from node_registry import registry, capability_summary, mermaid
from review_collaboration import exchange


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=OrchestrationTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task=self.fixture.task

    def test_business_and_capability_denominators_are_disjoint(self):
        nodes=p.graph(self.task);reg=registry()
        self.assertEqual(len({n['display_id'] for n in reg.values()}),34)
        stages=o.phase_summary(nodes,{})
        capabilities=capability_summary(nodes,{})
        self.assertEqual([len(s['nodes']) for s in stages],[4,5,8,5,3])
        self.assertEqual([len(s['nodes']) for s in capabilities],[5,2,2])
        self.assertEqual(sum(len(s['nodes']) for s in stages+capabilities),34)
        self.assertNotIn('phase',nodes['28'])
        self.assertEqual(nodes['28']['display_id'],'R.3')
        self.assertIn('R.3',mermaid(nodes))

    def test_all_objectives_request_once_before_environment_and_preflight(self):
        for objective in ('execute-and-learn','evaluate-only','baseline-reproduction'):
            task={**self.task,'objective':objective}
            seen=[]
            result=p.coordinate(task,self.fixture.root/objective,self.fixture.driver(seen))
            self.assertEqual(result['status'],'complete')
            self.assertEqual(seen.count('14'),1)
            self.assertLess(seen.index('14'),seen.index('7'))
            self.assertLess(seen.index('7'),seen.index('13'))
            if objective=='baseline-reproduction':
                self.assertLess(seen.index('13'),seen.index('5'))
                self.assertNotIn('15',seen)
            self.assertEqual(sum(len(s['nodes']) for s in result['capabilities']),9)

    def test_background_can_align_before_model_selection(self):
        seen=[]
        self.task['drivers'].pop('4')
        result=p.coordinate(self.task,self.fixture.root/'no-model',self.fixture.driver(seen))
        self.assertEqual(result['nodes']['22']['status'],'completed')
        self.assertNotIn('4',result['nodes'])
        self.assertFalse(set(seen)&o.COMPUTE_NODES)

    def test_plan_questions_wait_before_implementation_and_allocation(self):
        self.fixture.update('plan_review',status='questions',questions=[{'id':'context-gap'}])
        self.task['code_change']=True
        self.task['drivers']['23']={'argv':['fixture'],'receipt':'unused'}
        seen=[];result=p.coordinate(self.task,self.fixture.root/'questions',self.fixture.driver(seen))
        self.assertEqual(result['status'],'awaiting-review')
        self.assertNotIn('10',seen)
        self.assertFalse(set(seen)&o.COMPUTE_NODES)

    def test_plan_context_binds_background_protocol_and_resource_envelope(self):
        plan=exchange(self.task,'plan');result=exchange(self.task,'result')
        self.assertNotEqual(plan['context_digest'],result['context_digest'])
        self.assertEqual(plan['context']['background']['user_agreement_ref'],'fixture-user-message')
        old=plan['context_digest']
        self.fixture.update('background',scope='changed scope')
        self.assertNotEqual(exchange(self.task,'plan')['context_digest'],old)
        with self.assertRaisesRegex(ValueError,'context_digest'):
            o.gate(self.task,'34')

    def test_final_review_cannot_hide_unresolved_questions(self):
        self.fixture.update('review_response',status='final',questions=[{'id':'q1','status':'open'}])
        with self.assertRaisesRegex(ValueError,'unresolved_questions'):
            o.gate(self.task,'31')

    def test_adapter_revision_mismatch_stops_before_launch(self):
        seen=[]
        with self.assertRaisesRegex(ValueError,'graph_revision_3'):
            p.coordinate({**self.task,'driver_graph_revision':2},self.fixture.root/'wrong',self.fixture.driver(seen))
        self.assertEqual(seen,[])

    def test_failure_after_acquisition_keeps_release_before_review(self):
        def run(d,r,k,b):
            return {'status':'failed' if k=='7' else 'completed'}
        result=p.coordinate(self.task,self.fixture.root/'environment-failed',run)
        self.assertEqual(result['nodes']['7']['status'],'failed')
        self.assertEqual(result['cleanup']['status'],'completed')
        self.assertEqual(result['nodes']['30']['status'],'completed')
        self.assertEqual(result['nodes']['20']['status'],'completed')


if __name__=='__main__':unittest.main()
