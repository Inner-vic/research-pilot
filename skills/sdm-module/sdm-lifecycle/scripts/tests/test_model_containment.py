"""Real Windows kernel/process-tree faults; no SDK, model, ML or platform calls."""
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SCRIPTS))
from model_runner import ModelRunner
from model_containment import WindowsJob,kernel
from research_model_receipts import checked
from review_storage import atomic_json,read_json

NATIVE_POPEN=subprocess.Popen


def owner(config,value,folder):
    try:ModelRunner(config).run(value,folder,lambda result:None)
    except ValueError as error:atomic_json(Path(folder)/'owner-result.json',{'error':str(error)})


@unittest.skipUnless(os.name=='nt','Windows-only containment qualification; other routes remain explicit')
class WindowsContainmentTests(unittest.TestCase):
    def setUp(self):
        self.name=self.id().rsplit('.',1)[-1]
        evidence=os.environ.get('SDM_CONTAINMENT_EVIDENCE')
        if evidence:
            short={'test_forced_helper_death_reclaims_grandchildren':'helper',
                'test_startup_timeout_reclaims_unready_tree':'startup',
                'test_owner_abrupt_death_last_handle_kills_owned_tree':'owner',
                'test_foreign_readiness_cannot_extend_startup_or_complete_request':'readiness',
                'test_guard_cannot_start_target_after_job_owner_disappears':'enrollment'}
            self.root=Path(evidence)/short[self.name]
            self.root.mkdir(parents=True,exist_ok=False)
        else:
            temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        cli=self.root/'codex.exe';cli.write_bytes(b'control sentinel - must never be executed')
        self.config={'version':3,'backend':'cli','cli_path':str(cli),
            'cli_sha256':hashlib.sha256(cli.read_bytes()).hexdigest(),'cwd':str(self.root),
            'codex_home':str(self.root),'timeout_seconds':30,'startup_timeout_seconds':12,
            'control_probe':True,'startup_probe':'tree-block'}
        self.value={'schema':'sdm-model-request/v1','purpose':'result-analysis','model':'no-model-control',
            'contract_id':'process-tree-control/v1','prompt':'No inference; local containment fixture only.',
            'evidence':{},'output_schema':{'type':'object'}}
        self.folder=self.root/'call';self.folder.mkdir()
        atomic_json(self.root/'config.json',self.config);atomic_json(self.root/'value.json',self.value)
        self.api=kernel();self.handles=[];self.addCleanup(self.close_handles)
        self.unrelated=NATIVE_POPEN([sys.executable,'-I','-S','-c','import time;time.sleep(60)'],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(self.stop_control,self.unrelated)

    @staticmethod
    def stop_control(process):
        if process.poll() is None:process.kill()
        process.wait(timeout=10)

    def close_handles(self):
        for _,handle in self.handles:self.api.CloseHandle(handle)

    def wait_for(self,path,timeout=8):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if path.exists():return read_json(path)
            time.sleep(.025)
        logs=self.folder/'helper-stderr.log'
        self.fail('Timed out waiting for '+path.name+'; '+(logs.read_text(errors='replace') if logs.exists() else ''))

    def observe_live_tree(self):
        tree=self.wait_for(self.folder/'probe-tree.json')
        joined=checked(self.folder/'guard-joined.json','sdm-model-guard-joined/v1')
        job=self.api.OpenJobObjectW(4,False,joined['job_name'])
        self.assertTrue(job)
        try:
            # Query only this live owned job. Hold process handles before any
            # failure; never terminate using a stale PID or enumerate the host.
            view=type('JobView',(),{'api':self.api,'handle':job})()
            members=WindowsJob.pids(view)
            # A venv Python redirector can create another native child per
            # invocation. Qualify ALL members, not only the three fixture PIDs.
            self.assertTrue({joined['helper_pid'],tree['branch_pid'],tree['leaf_pid']}.issubset(set(members)))
            for pid in members:
                handle=self.api.OpenProcess(0x100000|0x1000,False,pid)  # synchronize/query only
                self.assertTrue(handle);self.handles.append((pid,handle))
                present=w.BOOL()
                self.assertTrue(self.api.IsProcessInJob(handle,job,ctypes.byref(present)))
                self.assertTrue(present.value)
        finally:
            # A query handle would keep the job alive. Close it BEFORE killing
            # the owner so last-handle closure is genuinely exercised.
            self.api.CloseHandle(job)
        return {'job_name':joined['job_name'],'members_before_fault':members,
                'fixture_pids':[joined['helper_pid'],tree['branch_pid'],tree['leaf_pid']],
                'query_job_handle_closed_before_fault':True,'models_started':tree['models_started']}

    def assert_dead(self,proof):
        exits=[]
        for pid,handle in self.handles:
            self.assertEqual(self.api.WaitForSingleObject(handle,5000),0,'owned child still alive')
            code=w.DWORD();self.assertTrue(self.api.GetExitCodeProcess(handle,ctypes.byref(code)))
            exits.append({'pid':pid,'exit_code':code.value,'exact_handle_exit_observed':True})
        self.assertIsNone(self.unrelated.poll(),'unrelated process affected')
        proof.update({'exits':exits,'unrelated_owned_control_alive':True,
            'provider_terminal_verified':False,'ml_or_platform_actions':0})
        self.assertFalse((self.folder/'backend-completed.json').exists())
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir() if p.is_file()}
        with self.assertRaisesRegex(ValueError,'unknown_requires_reconciliation'):
            ModelRunner(self.config).run(self.value,self.folder,lambda result:None,
                launcher=lambda *args:self.fail('Unknown intent relaunched'))
        after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir() if p.is_file()}
        self.assertEqual(before,after)
        proof.update({'replay':'unknown-held-no-launch','receipt_bytes_unchanged_on_replay':True})
        atomic_json(self.root/'fault-proof.json',proof)

    def test_forced_helper_death_reclaims_grandchildren(self):
        processes=[]
        def launch(*args,**kwargs):
            process=NATIVE_POPEN(*args,**kwargs);processes.append(process);return process
        thread=threading.Thread(target=owner,args=(self.config,self.value,self.folder),daemon=True)
        with patch('model_runner.subprocess.Popen',side_effect=launch):
            thread.start()
            try:
                proof=self.observe_live_tree();self.assertEqual(len(processes),1)
                processes[0].kill();processes[0].wait(timeout=5)
            finally:
                thread.join(timeout=15)
        self.assertFalse(thread.is_alive())
        self.assertIn('unknown_requires_reconciliation',read_json(self.folder/'owner-result.json')['error'])
        receipt=checked(self.folder/'containment.json','sdm-model-containment/v1')
        self.assertEqual(receipt['state'],'stopped-empty');self.assertTrue(receipt['local_empty_verified'])
        self.assert_dead({**proof,'fault':'exact-helper-handle-kill','owner_finally_receipt':receipt})

    def test_startup_timeout_reclaims_unready_tree(self):
        self.config['startup_timeout_seconds']=4
        atomic_json(self.root/'config.json',self.config)
        thread=threading.Thread(target=owner,args=(self.config,self.value,self.folder),daemon=True)
        thread.start();proof=self.observe_live_tree();thread.join(timeout=15)
        self.assertFalse(thread.is_alive())
        self.assertIn('unknown_requires_reconciliation',read_json(self.folder/'owner-result.json')['error'])
        receipt=checked(self.folder/'containment.json','sdm-model-containment/v1')
        self.assertEqual(receipt['cause'],'startup-timeout');self.assertTrue(receipt['local_empty_verified'])
        self.assert_dead({**proof,'fault':'startup-deadline','owner_finally_receipt':receipt})

    def test_owner_abrupt_death_last_handle_kills_owned_tree(self):
        with (self.root/'owner-stderr.log').open('wb') as stderr:
            process=NATIVE_POPEN([sys.executable,'-I','-S','-X','utf8','-B',str(Path(__file__).resolve()),
                'owner',str(self.root)],stdout=subprocess.DEVNULL,stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.addCleanup(self.stop_control,process)
            identity=self.wait_for(self.root/'owner-identity.json')
            self.assertEqual(identity['pid'],process.pid,'must fault the actual job-handle owner')
            proof=self.observe_live_tree()
            proof['actual_owner_pid_matches_created_process_handle']=True
            proof['owner_identity']=identity
            process.kill();process.wait(timeout=5)
        # No normal-finally receipt is invented for a controller that died.
        receipt=checked(self.folder/'containment.json','sdm-model-containment/v1')
        self.assertEqual(receipt['state'],'created')
        self.assert_dead({**proof,'fault':'exact-owner-handle-kill','owner_finally_ran':False,
            'stale_pre_fault_receipt_preserved':True})

    def test_foreign_readiness_cannot_extend_startup_or_complete_request(self):
        from research_model_receipts import seal
        thread=threading.Thread(target=owner,args=(self.config,self.value,self.folder),daemon=True)
        thread.start();proof=self.observe_live_tree()
        joined=read_json(self.folder/'guard-joined.json')
        atomic_json(self.folder/'backend-ready.json',seal({'schema':'sdm-model-backend-ready/v1',
            'binding':{'wrong_request':True},'helper_pid':joined['helper_pid'],'job_name':joined['job_name']}))
        thread.join(timeout=15);self.assertFalse(thread.is_alive())
        self.assertEqual(read_json(self.folder/'owner-result.json')['error'],'contained_model_readiness_binding_changed')
        self.assertTrue(read_json(self.folder/'containment.json')['local_empty_verified'])
        self.assert_dead({**proof,'fault':'foreign-ready-binding-rejected'})

    def test_guard_cannot_start_target_after_job_owner_disappears(self):
        job=WindowsJob(self.folder,{'control':'enrollment-barrier'})
        name=job.name;job.close()
        marker=self.root/'target-ran.json';target=self.root/'target.py'
        target.write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("bad")\n',encoding='utf-8')
        result=subprocess.run([sys.executable,'-I','-S','-X','utf8','-B',str(SCRIPTS/'model_process_guard.py'),
            name,str(target),str(self.folder)],capture_output=True,timeout=5,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertNotEqual(result.returncode,0);self.assertFalse(marker.exists())
        self.assertFalse((self.folder/'guard-joined.json').exists());self.assertIsNone(self.unrelated.poll())
        atomic_json(self.root/'fault-proof.json',{'fault':'owner-gone-before-enrollment',
            'target_executed':False,'guard_returncode':result.returncode,'models_started':0,
            'unrelated_owned_control_alive':True})


class ContainmentConfigurationTests(unittest.TestCase):
    def test_explicit_windows_only_route_no_fallback(self):
        config={'version':3,'backend':'cli','timeout_seconds':30}
        with patch('model_runner.os.name','posix'):
            with self.assertRaisesRegex(ValueError,'requires_windows_host'):ModelRunner(config)

    def test_startup_fault_requires_explicit_new_control_configuration(self):
        for version,enabled in ((1,False),(2,True),(3,False)):
            with patch('model_runner.os.name','nt'):
                with self.assertRaisesRegex(ValueError,'explicit_contained_startup_probe_required'):
                    ModelRunner({'version':version,'backend':'cli','timeout_seconds':30,
                        'control_probe':enabled,'startup_probe':'tree-block'})

    def test_startup_deadline_is_never_silently_ignored_by_old_routes(self):
        for version in (1,2):
            with self.assertRaisesRegex(ValueError,'explicit_contained_startup_timeout_required'):
                ModelRunner({'version':version,'backend':'cli','timeout_seconds':30,'startup_timeout_seconds':4})


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='owner':
        root=Path(sys.argv[2]);atomic_json(root/'owner-identity.json',{'pid':os.getpid(),'executable':sys.executable})
        owner(read_json(root/'config.json'),read_json(root/'value.json'),root/'call')
    else:unittest.main()
