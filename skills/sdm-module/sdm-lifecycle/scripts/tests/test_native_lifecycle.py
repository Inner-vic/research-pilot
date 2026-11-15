"""Native contract/readback distinctions, synthetic control only; no platform/UI."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import native_lifecycle as native
import devspace_execution as execution
from review_storage import atomic_json,read_json
from task_pipeline import digest


class NativeContractTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.now=time.time();self.uid='00000000-0000-4000-8000-000000000002';self.bound='sha256:fixture'
        self.request={'schema':'sdm-devspace-action/v1','binding':self.bound,'uid':self.uid,'action':'start',
            'route':'native-ui','requested_at':self.now-1}
        self.receipt={'request_digest':digest(self.request),'uid':self.uid,'action':'start','performed':True,
            'at':self.now,'source_ref':'synthetic UI receipt, no actual platform action'}

    def test_valid_request_and_receipt_remain_accepted(self):
        self.assertEqual(native.request(self.request,self.bound,self.uid,'start','native-ui',self.now),self.request)
        self.assertEqual(native.receipt(self.receipt,self.request,self.bound,self.uid,'start','native-ui',self.now),self.receipt)

    def test_recomputed_receipt_digest_does_not_legitimize_wrong_request_identity(self):
        for key,value in [('binding','sha256:other'),('uid','other'),('action','stop'),('route','official-cli'),('schema','other')]:
            changed={**self.request,key:value};receipt={**self.receipt,'request_digest':digest(changed)}
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'bound_native_action_request'):
                native.receipt(receipt,changed,self.bound,self.uid,'start','native-ui',self.now)

    def test_request_timestamp_cannot_be_missing_nonfinite_boolean_or_future(self):
        for value in (None,True,float('nan'),float('inf'),0,self.now+1):
            with self.subTest(value=value),self.assertRaises(ValueError):
                native.request({**self.request,'requested_at':value},self.bound,self.uid,'start','native-ui',self.now)

    def test_receipt_cannot_claim_performed_with_bad_identity_time_or_source(self):
        for key,value in [('uid','other'),('action','stop'),('performed',False),('source_ref',' '),('source_ref',123),
            ('at',True),('at',float('nan')),('at',self.now+1),('at',self.now-2)]:
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                native.receipt({**self.receipt,key:value},self.request,self.bound,self.uid,'start','native-ui',self.now)

    def test_prepare_only_reads_exact_bound_execution_and_never_claims_action(self):
        plan={'resource':{'uid':self.uid,'name':'owned','namespace':'fixture','cluster':'fixture','route':'native-ui'}}
        atomic_json(self.root/'execution.json',{'binding':self.bound,'uid':self.uid})
        atomic_json(self.root/'start-request.json',self.request)
        before={path.name:path.read_bytes() for path in self.root.iterdir()}
        result=native.prepare(plan,self.bound,self.root,'start')
        self.assertEqual(result['request_digest'],digest(self.request));self.assertFalse(result['execution_authority'])
        self.assertFalse(result['native_action_performed']);self.assertFalse(result['platform_queried'])
        self.assertEqual(result['operator_mode'],'agent-assisted')
        self.assertEqual(before,{path.name:path.read_bytes() for path in self.root.iterdir()})
        with self.assertRaisesRegex(ValueError,'execution_binding_changed'):native.prepare(plan,'other',self.root,'start')
        with self.assertRaisesRegex(ValueError,'start_or_stop'):native.prepare(plan,self.bound,self.root,'../stop')

    def test_capability_metadata_never_qualifies_independent_native_background(self):
        value=native.capabilities({'resource':{'route':'native-ui'}})
        self.assertTrue(value['lifecycle']['active_agent_required'])
        self.assertFalse(value['lifecycle']['independent_native_verified'])
        self.assertFalse(value['lifecycle']['independent_background_native_handoff'])
        self.assertIn('Separate qualification workspace',value['formal_qualification_ledger']['scope'])

    def test_wrong_request_binding_rejected_before_resource_read_or_state_mutation(self):
        from test_devspace_execution import native_case,action_receipt,pin
        cfg,root=native_case(self.root)
        cfg['devspace_execution']['plan']['remote_root']='/lpai/synthetic-native-case';pin(cfg)
        with patch.object(execution,'resource_call',return_value={'uid':self.uid,'phase':'stopped','observed_at':time.time()}):
            execution.execution(cfg,root,allow_live=True)
        path=root/'start-request.json';changed=read_json(path);changed['binding']='sha256:wrong';atomic_json(path,changed)
        proof=action_receipt(root,'start');before=(root/'execution.json').read_bytes()
        with patch.object(execution,'resource_call',side_effect=AssertionError('Read before receipt validation')):
            with self.assertRaisesRegex(ValueError,'bound_native_action_request'):
                execution.execution(cfg,root,proof,True)
        self.assertEqual((root/'execution.json').read_bytes(),before)

    def test_inspect_exposes_pending_native_request_without_platform_access(self):
        from test_devspace_execution import native_case,pin
        cfg,root=native_case(self.root)
        cfg['devspace_execution']['plan']['remote_root']='/lpai/synthetic-native-case';pin(cfg)
        with patch.object(execution,'resource_call',return_value={'uid':self.uid,'phase':'stopped','observed_at':time.time()}):
            execution.execution(cfg,root,allow_live=True)
        with patch.object(execution,'resource_call',side_effect=AssertionError('Inspection called platform')):
            result=execution.inspect(cfg,root)
        self.assertFalse(result['execution_authorized']);self.assertFalse(result['platform_queried'])
        self.assertTrue(result['authorization_record_matches_plan'])
        self.assertIn('existing bound authorization',result['next_action'])
        self.assertEqual(result['native_action_requests']['start']['resource']['uid'],self.uid)
        self.assertFalse(result['native_action_requests']['start']['native_action_performed'])


if __name__=='__main__':unittest.main()
