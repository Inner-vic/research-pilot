"""Real local subprocess crash/recovery; no platform, GPU or signed S13 claims."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
import shutil

import capability_interfaces as interfaces
import task_pipeline as pipeline
import test_orchestration as fixtures


class CapabilityRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.OrchestrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_module_ownership_matches_registry(self):
        from node_registry import registry
        nodes = registry()
        self.assertEqual(sum(interfaces.for_node(k) is not None for k in nodes), 9)
        for key, node in nodes.items():
            contract = interfaces.for_node(key)
            if contract:
                self.assertEqual(contract['module'], node['owner'])
                self.assertEqual(contract['acceptance_owner'], 'task_pipeline')

    def test_hard_kill_after_acquisition_receipt_recovers_without_redispatch(self):
        f = self.fixture
        root = f.root/'attempt'; root.mkdir()
        driver = f.root/'local_driver.py'
        driver.write_text('''import json,os,sys,time
from pathlib import Path
root=Path(sys.argv[1]); key=sys.argv[2]
with (root/'dispatches.jsonl').open('a') as stream:
    stream.write(json.dumps({'node':key,'pid':os.getpid()})+'\\n'); stream.flush(); os.fsync(stream.fileno())
if key=='14': (root/'local-allocation').write_text('owned-local-cpu-diagnostic')
if key=='cleanup': (root/'local-allocation').unlink(missing_ok=True)
status='completed'
if key=='15':
    # Actual bounded numeric work, deliberately not represented as a platform result.
    x=10.0
    losses=[]
    for _ in range(20):
        x-=0.1*2*x; losses.append(x*x)
    (root/'local-compute.json').write_text(json.dumps({'updates':20,'losses':losses,'platform':False}))
    status='failed'
payload={'task_node':key,'status':status,'reason':'local-process-only; no signed platform result'}
with (root/(key+'-receipt.json')).open('x') as stream:
    json.dump(payload,stream); stream.flush(); os.fsync(stream.fileno())
if key=='14':
    (root/'receipt-ready').write_text('ready')
    until=time.monotonic()+20
    while not (root/'continue-driver').exists() and time.monotonic()<until: time.sleep(0.02)
    (root/'driver-finished').write_text('finished')
''', encoding='utf-8')
        task = f.task
        task['drivers'].pop('5')  # Baseline reproduction is outside this local diagnostic.
        for key in task['drivers']:
            task['drivers'][key] = {'argv':[sys.executable,'-B',str(driver),str(root),key],
                                    'receipt':str(root/(key+'-receipt.json'))}
        task['cleanup_driver'] = {'argv':[sys.executable,'-B',str(driver),str(root),'cleanup'],
                                  'receipt':str(root/'cleanup-receipt.json')}
        task['wall_time_seconds'] = 120
        task_path = f.root/'task.json'; task_path.write_text(json.dumps(task))
        command = [sys.executable,'-B',str(Path(pipeline.__file__)),
                   '--task',str(task_path),'--output',str(root)]
        process = subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                                   env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        try:
            until=time.monotonic()+15
            while not (root/'receipt-ready').exists() and process.poll() is None and time.monotonic()<until:
                time.sleep(0.02)
            self.assertTrue((root/'receipt-ready').exists())
            with self.assertRaisesRegex(ValueError,'task_coordinator_busy'):
                pipeline.coordinate(task,root)
            deadline=(root/'execution-deadline.json').read_bytes()
            receipt=(root/'14-receipt.json').read_bytes()
            process.kill(); process.wait(timeout=5)
        finally:
            if process.poll() is None: process.kill(); process.wait(timeout=5)
            process.stderr.close()
            (root/'continue-driver').write_text('continue')
        until=time.monotonic()+5
        while not (root/'driver-finished').exists() and time.monotonic()<until: time.sleep(0.02)
        self.assertTrue((root/'driver-finished').exists())
        result=pipeline.coordinate(task,root)
        calls=[json.loads(line)['node'] for line in (root/'dispatches.jsonl').read_text().splitlines()]
        self.assertEqual(calls.count('14'),1)
        self.assertEqual(calls.count('cleanup'),1)
        self.assertEqual(calls.count('15'),1, json.dumps(result))
        self.assertFalse((root/'local-allocation').exists())
        self.assertEqual(result['cleanup']['status'],'completed')
        self.assertEqual(result['nodes']['15']['status'],'failed')
        self.assertEqual(json.loads((root/'local-compute.json').read_bytes())['updates'],20)
        self.assertEqual((root/'execution-deadline.json').read_bytes(),deadline)
        self.assertEqual((root/'14-receipt.json').read_bytes(),receipt)
        before=(root/'dispatches.jsonl').read_bytes()
        pipeline.coordinate(task,root)
        self.assertEqual((root/'dispatches.jsonl').read_bytes(),before)
        contract=json.loads((root/'14-capability-call.json').read_bytes())
        self.assertEqual(contract['operation'],'acquire')
        self.assertFalse(contract['execution_authority_created'])
        if os.environ.get('SDM_P0_EVIDENCE_DIR'):
            destination=Path(os.environ['SDM_P0_EVIDENCE_DIR'])/'local-hard-kill'
            destination.mkdir(parents=True,exist_ok=False)
            for name in ('events.jsonl','summary.json','dispatches.jsonl','local-compute.json',
                         'execution-deadline.json','14-receipt.json','14-started.json',
                         '14-capability-call.json','cleanup.json','cleanup-receipt.json'):
                shutil.copyfile(root/name,destination/name)
            (destination/'verification.json').write_text(json.dumps({
                'coordinator_force_killed':True,'concurrent_writer_rejected':True,
                'acquire_dispatches':calls.count('14'),'cleanup_dispatches':calls.count('cleanup'),
                'compute_dispatches':calls.count('15'),'updates':20,
                'repeat_resume_new_dispatches':0,'deadline_preserved':True,
                'platform_execution':False,'review_records':'synthetic fixture',
                'allocation':'local file marker only; not platform compute'}),encoding='utf-8')

    def test_release_prevents_fresh_compute_on_resume(self):
        f=self.fixture; root=f.root/'released'; seen=[]
        task=pipeline.current_task(f.task,root)
        root.mkdir()
        # A durable accepted acquisition followed by independent cleanup.
        (root/'events.jsonl').write_text(json.dumps({'task_digest':pipeline.digest(task),
            'node':'14','result':{'status':'completed'}})+'\n')
        (root/'cleanup.json').write_text(json.dumps({'status':'completed'}))
        result=pipeline.coordinate(task,root,f.driver(seen))
        self.assertFalse(set(seen)&{'7','5','15','16','17'})
        self.assertEqual(result['nodes']['7']['reason'],'released_allocation_requires_new_attempt')

    def test_changed_capability_call_is_rejected_before_receipt_recovery(self):
        root=self.fixture.root/'call-drift';root.mkdir()
        receipt=root/'receipt.json'
        code="import json,sys;json.dump({'task_node':'14','status':'completed'},open(sys.argv[1],'w'))"
        driver={'argv':[sys.executable,'-B','-c',code,str(receipt)],'receipt':str(receipt)}
        self.assertEqual(pipeline.execute(driver,root,'14',2.5)['status'],'completed')
        path=root/'14-capability-call.json'
        value=json.loads(path.read_bytes());value['deadline']+=100
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'capability_call_changed'):
            pipeline.execute(driver,root,'14',2.5)


if __name__=='__main__': unittest.main()
