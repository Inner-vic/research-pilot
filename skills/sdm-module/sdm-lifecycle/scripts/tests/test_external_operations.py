"""CPU adapter processes and public coordinator; never imports a platform SDK."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import external_operations as e
import task_pipeline as pipeline
import test_orchestration as fixtures

ADAPTER = '''import json,sys,time
from pathlib import Path
p=Path(sys.argv[1]); cfg=json.loads(p.read_text()); name=sys.argv[2]; route=sys.argv[3]
with (p.parent/'calls.jsonl').open('a') as f:f.write(json.dumps([route,name])+'\\n')
identity={'provider':'cpu-fixture','namespace':'cpu-only','owner':'fixture','uid':'old' if name=='read-old' else 'new','request_key':'request','component_uid':'component','observed_at':time.time()}
if name=='probe':data={'available':True,'source_ref':'CPU fixture only','version':'fixture-v1','operations':cfg.get(route, ['prepare','stop','read-old','submit','lookup','read-new','read','diagnose'])}
elif name=='prepare':data={'configuration_verified':True,'component_uid':'component','configuration_digest':'config'}
elif name=='stop':data={'requested':True}
elif name=='read-old':data={**identity,'status':cfg.get('old_status','stopped'),'released':cfg.get('released',True),'pods':[{'status':cfg.get('pod_status','Succeeded')}]}
elif name=='lookup':data={'complete':True,'matches':[identity]*cfg.get('matches',0)}
elif name=='submit':data={'uid':'new'}
elif name=='diagnose':data=cfg['evidence']
else:data={**identity,'status':'pending','uid':cfg.get('new_uid','new')}
if name==cfg.get('error_command'):print(json.dumps(cfg['error']))
else:print(json.dumps({'status':'ok','data':data}))
'''


class ExternalOperationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.adapter=self.root/'cpu_adapter.py';self.adapter.write_text(ADAPTER)
        self.cfg=self.root/'fixture.json';self.cfg.write_text('{}')
        self.spec={'schema':'sdm-external-operation/v1','provider':'cpu-fixture','namespace':'cpu-only',
                   'owner':'fixture','request_key':'request','action':'switch','old_uid':'old',
                   'component_uid':'component','configuration_digest':'config','routes':[]}
        self.spec['submission_registry']=str(self.root/'shared-requests')
        for kind in ('browser','official-cli','official-sdk'):
            self.spec['routes'].append({'id':kind,'kind':kind,'adapter_refs':[{'path':str(self.adapter),
                'digest':'sha256:'+hashlib.sha256(self.adapter.read_bytes()).hexdigest()}],
                'commands':{name:[sys.executable,'-B',str(self.adapter),str(self.cfg),name,kind]
                            for name in ('probe','prepare','stop','read-old','submit','lookup','read-new','read','diagnose')}})
        self.authorize()

    def authorize(self):
        auth={k:self.spec[k] for k in ('provider','namespace','owner','request_key','action','old_uid','component_uid','configuration_digest')}
        auth.update(direct_user_confirmation=True,source_ref='synthetic CPU fixture only')
        p=self.root/'authorization.json';p.write_text(json.dumps(auth))
        self.spec['authorization_ref']={'path':str(p),'digest':'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()}

    def fixture(self,**values):self.cfg.write_text(json.dumps(values))
    def operation(self):return e.Operation(self.spec,self.root/'operation.json',20)
    def calls(self):return [json.loads(v) for v in (self.root/'calls.jsonl').read_text().splitlines()]

    def test_utf8_provider_response_preserves_non_ascii_evidence(self):
        self.adapter.write_text(ADAPTER.replace("json.dumps({'status':'ok','data':data})", "json.dumps({'status':'ok','data':data},ensure_ascii=False)"), encoding="utf-8")
        for route in self.spec['routes']:
            route['adapter_refs'][0]['digest']='sha256:'+hashlib.sha256(self.adapter.read_bytes()).hexdigest()
        self.fixture(evidence={'lines':[{'kind':'runtime-exception','text':'训练前检查'}]})
        op=self.operation();op.state['route']='official-cli'
        self.assertEqual(op.command('diagnose')['lines'][0]['text'],'训练前检查')

    def test_official_route_precedes_browser_and_switch_order(self):
        result=self.operation().run();self.assertEqual(result['uid'],'new')
        self.assertTrue(all(r=='official-cli' for r,_ in self.calls()))
        self.assertEqual([n for _,n in self.calls()],['probe','prepare','stop','read-old','lookup','submit','read-new'])

    def test_missing_cli_capability_checks_sdk_before_browser(self):
        self.fixture(**{'official-cli':['read']})
        self.operation().run()
        self.assertEqual(self.calls()[:2],[['official-cli','probe'],['official-sdk','probe']])
        self.assertNotIn(['browser','probe'],self.calls())

    def test_missing_both_official_routes_uses_browser(self):
        self.fixture(**{'official-cli':[],'official-sdk':[]});self.operation().run()
        self.assertIn(['browser','submit'],self.calls())

    def test_exit_zero_http500_does_not_fallback_or_submit(self):
        self.fixture(error_command='stop',error={'status':'ok','http_status':500,'error':'role lookup failed'})
        with self.assertRaisesRegex(ValueError,'provider_response_failed'):self.operation().run()
        self.assertNotIn(['browser','probe'],self.calls());self.assertNotIn(['official-cli','submit'],self.calls())

    def test_approval_does_not_fallback(self):
        self.fixture(error_command='stop',error={'status':'pending_approval'})
        with self.assertRaisesRegex(ValueError,'provider_approval_pending'):self.operation().run()
        self.assertNotIn(['browser','probe'],self.calls())

    def test_pending_stop_recovery_reads_release_without_repeating_stop(self):
        self.fixture(error_command='stop',error={'status':'pending_approval','approval_id':'CPU-stop'})
        with self.assertRaisesRegex(ValueError,'provider_approval_pending'):self.operation().run()
        self.fixture(old_status='stopped',released=True)
        self.assertEqual(self.operation().run()['uid'],'new')
        self.assertEqual(sum(name=='stop' for _,name in self.calls()),1)
        self.assertEqual(sum(name=='submit' for _,name in self.calls()),1)

    def test_pending_old_resource_blocks_new_submit(self):
        self.fixture(old_status='pending')
        with self.assertRaisesRegex(ValueError,'release_not_verified'):self.operation().run()
        self.assertNotIn(['official-cli','submit'],self.calls())

    def test_live_pod_blocks_new_submit(self):
        self.fixture(pod_status='Running')
        with self.assertRaisesRegex(ValueError,'release_not_verified'):self.operation().run()

    def test_wrong_new_uid_is_rejected(self):
        self.fixture(new_uid='other')
        with self.assertRaisesRegex(ValueError,'uid_mismatch'):self.operation().run()

    def test_resume_unknown_submit_reconciles_without_duplicate(self):
        op=self.operation();op.route();op.state.update(intents=['prepare','stop','submit'],responses={
            'prepare':{'configuration_verified':True,'component_uid':'component','configuration_digest':'config'}})
        e.save(op.path,op.state);self.fixture(matches=1)
        self.assertEqual(self.operation().run()['uid'],'new')
        self.assertFalse(any(n in {'stop','submit'} for _,n in self.calls()))

    def test_unknown_submit_empty_lookup_does_not_retry(self):
        op=self.operation();op.route();op.state.update(intents=['prepare','stop','submit'],responses={
            'prepare':{'configuration_verified':True,'component_uid':'component','configuration_digest':'config'}})
        e.save(op.path,op.state)
        with self.assertRaisesRegex(ValueError,'do_not_resubmit'):self.operation().run()

    def test_adapter_change_and_auth_scope_block_mutation(self):
        self.adapter.write_text(ADAPTER+'\n# changed')
        with self.assertRaisesRegex(ValueError,'adapter_changed'):self.operation().run()
        self.assertFalse((self.root/'calls.jsonl').exists())

    def test_stale_readback_is_not_release_proof(self):
        value={'provider':'cpu-fixture','namespace':'cpu-only','owner':'fixture','uid':'old','observed_at':time.time()-500}
        with self.assertRaisesRegex(ValueError,'fresh_object'):self.operation().identity(value,'old')

    def test_authorization_configuration_drift_blocks_mutation(self):
        self.spec['configuration_digest']='different'
        with self.assertRaisesRegex(ValueError,'authorization_scope'):self.operation().run()
        self.assertFalse(any(n in {'prepare','stop','submit'} for _,n in self.calls()))

    def test_uncertain_effect_in_failed_receipt_reaches_coordinator(self):
        self.fixture(error_command='stop',error={'status':'ok','code':500})
        root=self.root/'run';root.mkdir()
        result=pipeline.execute({'external_operation':self.spec,'receipt':str(root/'receipt.json')},root,'14',20)
        self.assertEqual(result['status'],'failed');self.assertTrue(result['uncertain_effect'])

    def test_mutating_operation_cannot_enter_read_only_parallel_dispatch(self):
        with self.assertRaisesRegex(ValueError,'cannot_be_read_only'):
            pipeline.execute({'read_only':True,'external_operation':self.spec,'receipt':str(self.root/'receipt.json')},self.root,'14',20)
        self.assertFalse((self.root/'calls.jsonl').exists())

    def test_project_entry_propagates_external_binding(self):
        from unittest.mock import patch
        import run_project_pipeline as entry
        driver=self.root/'driver.py';driver.write_text('# CPU only')
        config={'supported_graph_revisions':[3],'provider':'cpu-fixture','run_id':'external-test',
                'driver_runtime':{'mode':'native','python':sys.executable},
                'project_driver':{'path':str(driver),'digest':'sha256:'+hashlib.sha256(driver.read_bytes()).hexdigest()},
                'external_operations':{'14':self.spec,'cleanup':{**self.spec,'action':'stop'}}}
        captured=[]
        def coordinate(task,root):captured.append(task);return {'status':'needs-driver'}
        with patch.object(entry,'coordinate',side_effect=coordinate):entry.run(config,self.root/'project')
        self.assertEqual(captured[0]['drivers']['14']['external_operation'],self.spec)
        self.assertFalse(captured[0]['drivers']['14']['read_only'])
        self.assertEqual(captured[0]['cleanup_driver']['external_operation']['action'],'stop')

    def test_echoed_oom_does_not_support_parameter_change(self):
        evidence={'training_started':False,'exception_type':'AssertionError','lines':[
            {'kind':'command-echo','text':'CUDA out of memory'}]}
        self.assertEqual(e.attribution(evidence)['category'],'preflight-assertion')
        self.assertFalse(e.attribution(evidence)['parameter_change_supported'])
        evidence.update(training_started=True,exception_type='OutOfMemoryError',lines=[
            {'kind':'runtime-exception','text':'torch.OutOfMemoryError: CUDA out of memory'}])
        self.assertTrue(e.attribution(evidence)['parameter_change_supported'])

    def test_public_coordinator_dispatch_and_resume_are_idempotent(self):
        f=fixtures.OrchestrationTests();f.setUp();self.addCleanup(f.doCleanups)
        root=f.root/'attempt';root.mkdir()
        generic=self.root/'node.py';generic.write_text('''import json,sys
from pathlib import Path
node=sys.argv[1];Path(sys.argv[2]).write_text(json.dumps({'task_node':node,'status':'failed' if node=='15' else 'completed'}))
''')
        task=f.task;task['drivers'].pop('5');task['timeout_seconds']=60
        for node in task['drivers']:
            task['drivers'][node]={'argv':[sys.executable,'-B',str(generic),node,str(root/(node+'.json'))],
                                   'receipt':str(root/(node+'.json'))}
        task['drivers']['14']['external_operation']=self.spec
        task['cleanup_driver']={'argv':[sys.executable,'-B',str(generic),'cleanup',str(root/'release.json')],
                                'receipt':str(root/'release.json')}
        path=self.root/'task.json';path.write_text(json.dumps(task))
        command=[sys.executable,'-B',str(Path(pipeline.__file__)),'--task',str(path),'--output',str(root)]
        first=subprocess.run(command,capture_output=True,text=True,timeout=60)
        self.assertEqual(first.returncode,0,first.stderr)
        summary=json.loads(first.stdout);self.assertEqual(summary['nodes']['14']['status'],'completed')
        self.assertEqual(len(pipeline.graph(task)),34)
        calls=self.calls();second=subprocess.run(command,capture_output=True,text=True,timeout=60)
        self.assertEqual(second.returncode,0,second.stderr);self.assertEqual(self.calls(),calls)
        self.assertEqual(summary['nodes']['15']['status'],'failed')  # No fabricated S13/training success.


if __name__=='__main__':unittest.main()
