"""Official API mapping contracts with injected CPU clients; no SDK import."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from lpai_operation_adapter import LpaiAdapter, operation_binding


class LpaiOperationAdapterTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        cfg={'name':'cpu-component','queue_code':'cpu-queue','cmd':'CPU-ONLY','code':[],'image':'cpu-image',
             'task_role_specs':[{'rolename':'master','replica':'1','resource':{'id':41,'replica':1}}]}
        p=self.root/'config.json';p.write_text(json.dumps(cfg))
        self.config={'namespace':'cpu-only','owner':'fixture','cluster':'cpu-cluster','component_uid':'component',
                     'submission_registry':str(self.root/'shared-requests'),
                     'configuration_ref':{'path':str(p),'digest':'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()},
                     'total_gpus':8,'task_name':'cpu-task','task_info':{'problem_statement':'CPU test',
                        'solution_approach':'fake','expected_result':'contract','expected_duration':10,
                        'expected_duration_desc':'CPU metadata only'}}
        self.project=Mock();self.task=Mock();self.pods=Mock(return_value=[])
        self.project.get.return_value={'namespace':'cpu-only','owner':'fixture','cluster':'cpu-cluster','uid':'component',
            'name':'cpu-component','queue_code':'cpu-queue','data':{'lpai_task':{'cmd':'CPU-ONLY','roles':[
                {'cmd':'CPU-ONLY','image_info':{'image':'cpu-image'},'role':'master','replica':1,'resource':{'Id':41,'Gpu':8,'replica':1}}]}}}
        self.task.get.return_value={'namespace':'cpu-only','owner':'fixture','uid':'old','status':'stopped'}
        self.adapter=LpaiAdapter(self.config,self.project,self.task,self.pods)

    def test_sdk_update_error_return_never_means_stopped(self):
        self.task.update.return_value=500
        with self.assertRaisesRegex(ValueError,'stop_not_acknowledged'):self.adapter.stop('old')

    def test_scope_failure_prevents_stop(self):
        self.task.get.return_value['owner']='other'
        with self.assertRaisesRegex(ValueError,'scope_mismatch'):self.adapter.stop('old')
        self.task.update.assert_not_called()

    def test_prepare_checks_real_command_and_allocation_after_update(self):
        self.assertTrue(self.adapter.prepare()['configuration_verified'])
        self.project.get.return_value['data']['lpai_task']['roles'][0]['resource']['Gpu']=16
        with self.assertRaisesRegex(ValueError,'configuration_readback_mismatch'):self.adapter.prepare()

    def test_stopped_task_with_active_pod_is_not_released(self):
        self.pods.return_value=[{'namespace':'cpu-only','task_uid':'old','uid':'pod','status':'Running'}]
        self.assertFalse(self.adapter.read('old')['released'])

    def test_incomplete_lookup_cannot_authorize_submission(self):
        self.task.list.return_value={'items':[],'total':2}
        with self.assertRaisesRegex(ValueError,'pagination_adapter'):self.adapter.lookup('request')

    def test_submit_passes_five_metadata_fields_and_marker(self):
        self.task.submit_project_task.return_value='new'
        self.assertEqual(self.adapter.submit('request'),{'uid':'new'})
        kwargs=self.task.submit_project_task.call_args.kwargs
        self.assertEqual(kwargs['describe'],'sdm-request:request')
        for k,v in self.config['task_info'].items():self.assertEqual(kwargs[k],v)

    def test_sdk_pending_approval_is_not_discarded_as_missing_uid(self):
        from submission_guard import PendingApproval
        self.task.submit_project_task.return_value={'status':'pending_approval','approval_id':'CPU-only','mode':'self'}
        with self.assertRaises(PendingApproval) as caught:self.adapter.submit('request')
        self.assertEqual(caught.exception.approval['approval_id'],'CPU-only')

    def test_binding_builder_pins_adapter_and_configuration_without_sdk(self):
        p=self.root/'adapter.json';p.write_text(json.dumps(self.config))
        binding=operation_binding(p,'/cpu-only/python','switch','request',{'path':'fixture','digest':'fixture'},'old')
        self.assertEqual(binding['old_uid'],'old');self.assertEqual(binding['routes'][0]['kind'],'official-sdk')
        self.assertEqual(len(binding['routes'][0]['adapter_refs']),6)
        self.assertEqual(binding['submission_registry'],str(self.root/'shared-requests'))
        self.task.submit_project_task.assert_not_called();self.project.update.assert_not_called()


if __name__=='__main__':unittest.main()
