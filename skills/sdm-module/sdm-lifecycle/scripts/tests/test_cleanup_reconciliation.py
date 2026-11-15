"""Read-only CPU readbacks through the public coordinator; no platform access."""
from copy import deepcopy
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import unittest

import cleanup_reconciliation as rec
import task_pipeline as pipeline


class CleanupReconciliationTests(unittest.TestCase):
    def setUp(self):
        from test_orchestration import OrchestrationTests
        self.f=OrchestrationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.root=self.f.root/'attempt';self.seen=[]
        self.identity={'provider':'cpu-fixture','namespace':'local','uid':'allocation-1'}
        def driver(spec,root,key,budget):
            self.seen.append(key)
            if key=='cleanup':return {'status':'failed','reason':'CPU simulated failed release readback'}
            return {'status':'completed',**({'observation':{'resource_identity':self.identity}} if key=='14' else {})}
        self.driver=driver
        result=pipeline.coordinate(self.f.task,self.root,driver)
        self.assertEqual(result['status'],'cleanup-required')
        self.original=(self.root/'cleanup.json').read_bytes()
        self.payload={'task_node':'cleanup-readback','status':'completed','resource_identity':self.identity,
            'source_ref':'CPU readback fixture, not platform evidence',
            'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'resource':{'status':'stopped','released':True,'pods_complete':True,'pods':[{'status':'Succeeded'}]}}
        self.payload_path=self.f.root/'readback-source.json'
        self.script=self.f.root/'readback.py'
        self.counter=self.f.root/'read-count.txt'
        self.script.write_text('import json,sys\nfrom pathlib import Path\n'
            'source,receipt,counter=map(Path,sys.argv[1:])\n'
            'counter.write_text(counter.read_text()+"read\\n" if counter.exists() else "read\\n")\n'
            'receipt.write_text(source.read_text())\n')

    def request(self,key='check-1'):
        self.payload_path.write_text(json.dumps(self.payload))
        return {'schema':'sdm-cleanup-reconciliation/v1','reconciliation_id':key,
            'original_cleanup_digest':pipeline.digest_bytes(self.original),
            'ownership_node':'14','identity_field':'observation.resource_identity',
            'driver':{'read_only':True,'timeout_seconds':10,
                'argv':[sys.executable,'-B',str(self.script),str(self.payload_path),
                        str(self.root/'cleanup-reconciliation'/key/'readback.json'),str(self.counter)],
                'receipt':str(self.root/'cleanup-reconciliation'/key/'readback.json')}}

    def test_success_preserves_failure_and_resume_never_replays_compute_or_cleanup(self):
        request=self.request()
        result=rec.reconcile(self.f.task,self.root,request)
        self.assertEqual(result['status'],'completed')
        self.assertTrue(result['original_failure_retained'])
        self.assertEqual((self.root/'cleanup.json').read_bytes(),self.original)
        before=list(self.seen)
        result=pipeline.coordinate(self.f.task,self.root,self.driver)
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['cleanup']['reason'],'independent_release_reconciled')
        self.assertEqual(self.seen[:len(before)],before)
        self.assertEqual(self.seen[len(before):],['21'])  # Only the previously blocked archive step.
        self.assertEqual((self.root/'cleanup.json').read_bytes(),self.original)

    def test_repeated_request_recovers_readback_without_redispatch(self):
        request=self.request()
        rec.reconcile(self.f.task,self.root,request)
        rec.reconcile(self.f.task,self.root,request)
        self.assertEqual(self.counter.read_text(),'read\n')

    def test_wrong_uid_is_rejected(self):
        self.payload['resource_identity']={**self.identity,'uid':'another-allocation'}
        with self.assertRaisesRegex(ValueError,'identity_mismatch'):rec.reconcile(self.f.task,self.root,self.request())
        self.assertFalse((self.root/'cleanup-reconciled.json').exists())

    def test_incomplete_pods_and_running_pods_are_rejected(self):
        for index,resource in enumerate(({'status':'stopped','released':True,'pods_complete':False,'pods':[]},
              {'status':'stopped','released':True,'pods_complete':True,'pods':[{'status':'Running'}]})):
            self.payload['resource']=resource
            with self.assertRaisesRegex(ValueError,'complete_terminal_pods'):rec.reconcile(self.f.task,self.root,self.request(str(index)))

    def test_old_or_future_observation_cannot_close_cleanup(self):
        for index,seconds in enumerate((-301,60)):
            self.payload['observed_at']=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=seconds)).isoformat()
            with self.assertRaisesRegex(ValueError,'stale'):rec.reconcile(self.f.task,self.root,self.request(str(index)))

    def test_non_read_only_driver_is_rejected_before_process(self):
        request=self.request();request['driver']['read_only']=False
        with self.assertRaisesRegex(ValueError,'read_only'):rec.reconcile(self.f.task,self.root,request)
        self.assertFalse(self.counter.exists())

    def test_original_failure_or_accepted_readback_changes_invalidate_acceptance(self):
        request=self.request();rec.reconcile(self.f.task,self.root,request)
        receipt=Path(request['driver']['receipt']);raw=receipt.read_bytes();receipt.write_text('{}')
        with self.assertRaisesRegex(ValueError,'receipt_changed'):rec.effective(self.root,{})
        receipt.write_bytes(raw)
        (self.root/'cleanup.json').write_text(json.dumps({'status':'failed','reason':'changed'}))
        with self.assertRaisesRegex(ValueError,'original_cleanup_changed'):rec.effective(self.root,{})

    def test_unrelated_task_cannot_reconcile(self):
        task=deepcopy(self.f.task);task['code_change']=True
        with self.assertRaisesRegex(ValueError,'task_mismatch'):rec.reconcile(task,self.root,self.request())
        self.assertFalse(self.counter.exists())

    def test_public_cli_only_dispatches_readback(self):
        request=self.request();path=self.f.root/'request.json';path.write_text(json.dumps(request))
        task=self.f.root/'task.json';task.write_text(json.dumps(self.f.task))
        before=list(self.seen)
        result=subprocess.run([sys.executable,'-B',pipeline.__file__,'--task',str(task),
            '--output',str(self.root),'--reconcile-cleanup',str(path)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'completed')
        self.assertEqual(before,self.seen)
        self.assertEqual(self.counter.read_text(),'read\n')

    def test_release_reconciliation_does_not_clear_uncertain_training_effect(self):
        def driver(spec,root,key,budget):
            if key=='15':return {'status':'failed','uncertain_effect':True,'reason':'CPU unknown execution fixture'}
            return self.driver(spec,root,key,budget)
        self.root=self.f.root/'uncertain-attempt'
        pipeline.coordinate(self.f.task,self.root,driver)
        self.original=(self.root/'cleanup.json').read_bytes()
        rec.reconcile(self.f.task,self.root,self.request())
        result=pipeline.coordinate(self.f.task,self.root,driver)
        self.assertIn('15',result['coverage']['reconciliation_required'])
        self.assertTrue(result['nodes']['15']['uncertain_effect'])


if __name__=='__main__':unittest.main()
