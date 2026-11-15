"""Incremental resource dialogue must not create authority or hide actual state."""
from copy import deepcopy
from pathlib import Path
import unittest
import task_pipeline as pipeline
import orchestration as o
from resource_planning import draft_questions, draft_progress, discovery_route, resource_totals, observed_stage


class ResourcePlanningTests(unittest.TestCase):
    def setUp(self):
        from test_orchestration import OrchestrationTests
        self.fixture=OrchestrationTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def restore(self,name,value):
        self.fixture.put(name,**{k:v for k,v in value.items() if k!='schema'})

    def test_multiple_dialogue_rounds_keep_drafts_and_reuse_preparation(self):
        f=self.fixture;task=f.task;seen=[];root=f.root/'dialogue'
        inventory=deepcopy(f.inventory);proposal=deepcopy(f.proposal)
        f.update('inventory',planning_status='collecting',options=[])
        first=pipeline.coordinate(task,root,f.driver(seen))
        self.assertEqual(first['status'],'needs-resource-investigation')
        self.assertIsNone(first['waiting']['next_question'])
        self.assertFalse(set(seen)&o.COMPUTE_NODES)
        self.assertNotIn('26',first['nodes'])
        before=list(seen)
        self.restore('inventory',inventory)
        f.update('proposal',planning_status='draft',request={})
        second=pipeline.coordinate(task,root,f.driver(seen))
        self.assertEqual(second['status'],'needs-resource-investigation')
        self.assertIn('request.provider',[q['field'] for q in second['waiting']['agent_work']])
        self.assertNotIn('27',second['nodes'])
        self.assertFalse(set(seen)&o.COMPUTE_NODES)
        self.restore('proposal',proposal)
        third=pipeline.coordinate(task,root,f.driver(seen))
        self.assertEqual(third['status'],'complete')
        for node in before:self.assertEqual(seen.count(node),1)
        self.assertEqual(seen.count('14'),1)

    def test_sdk_cli_first_then_only_missing_fields_go_to_ui(self):
        self.assertEqual(discovery_route({})['collector'],'sdk-cli')
        check={'source':'installed-cli-help','observed_at':'2026-10-15',
               'missing_fields':['queue_headroom','price']}
        route=discovery_route({'capability_check':check})
        self.assertEqual(route['collector'],'browser-or-computer-use')
        self.assertEqual(route['fields'],['queue_headroom','price'])
        check['missing_fields']=[]
        self.assertEqual(discovery_route({'capability_check':check})['collector'],'sdk-cli')

    def test_platform_facts_are_agent_work_and_never_authorize_start(self):
        result=draft_progress('inventory',{'account_ref':'known','options':[]})
        self.assertEqual(result['status'],'needs-resource-investigation')
        self.assertEqual(result['questions'],[])
        self.assertFalse(result['execution_allowed'])
        self.assertEqual(draft_progress('inventory',{})['status'],'awaiting-user-input')

    def test_complete_plan_still_requires_the_real_confirmation_gate(self):
        f=self.fixture
        Path(f.records['confirmation']['path']).unlink()
        seen=[];result=pipeline.coordinate(f.task,f.root/'no-confirmation',f.driver(seen))
        self.assertEqual(result['status'],'awaiting-user-confirmation')
        self.assertFalse(set(seen)&o.COMPUTE_NODES)

    def test_accepted_proposal_exposes_computed_resource_totals(self):
        result=o.gate(self.fixture.task,'27')
        self.assertEqual(result['resource_totals']['total_gpu_allocation'],1)

    def test_count_all_roles_and_reject_boolean_instance_count(self):
        request={'roles':[{'instances':1,'gpus_per_instance':1},
                          {'instances':2,'gpus_per_instance':4}]}
        self.assertEqual(resource_totals(request)['total_gpu_allocation'],9)
        request['roles'][0]['instances']=True
        with self.assertRaises(ValueError):resource_totals(request)

    def test_shared_process_limit_does_not_become_exclusive_card_count(self):
        result=resource_totals({'roles':[{'instances':1,'allocation_mode':'shared',
                               'gpus_per_instance':None,'process_device_limit':1}]})
        self.assertIsNone(result['total_gpu_allocation'])
        self.assertEqual(result['program_device_ceiling'],1)
        with self.assertRaises(ValueError):
            resource_totals({'roles':[{'instances':1,'gpus_per_instance':-1}]})

    def test_ready_is_not_model_progress_and_failure_is_not_release(self):
        self.assertEqual(observed_stage('Pending')['observed_stage'],'queueing')
        self.assertFalse(observed_stage('Running')['model_updates_observed'])
        self.assertTrue(observed_stage('Running',2)['model_updates_observed'])
        self.assertFalse(observed_stage('Failed')['release_verified'])
        self.assertFalse(observed_stage('stopped')['release_verified'])
        self.assertTrue(observed_stage('stopped',identity_verified=True)['release_verified'])

    def test_questions_only_ask_missing_choices_without_defaulting_gpu_count(self):
        questions=draft_questions('proposal',{'purpose':'case','request':{
            'provider':'development-space','queue_id':'known','roles':[{'instances':1,'gpu_type':'A10'}]}})
        fields=[q['field'] for q in questions]
        self.assertNotIn('request.queue_id',fields)
        self.assertIn('request.roles.0.gpus_per_instance',fields)


if __name__=='__main__':unittest.main()
