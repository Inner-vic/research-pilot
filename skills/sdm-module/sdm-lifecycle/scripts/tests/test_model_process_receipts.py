"""Exact-child process ownership and unknown-outcome controls, no real models."""
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import model_process_receipts as processes
from model_runner import ModelRunner
from review_storage import read_json


class Child:
    def __init__(self, *, already_done=False, force_kill=False):
        self.pid=32123;self.returncode=0 if already_done else None
        self.actions=[];self.force_kill=force_kill
    def poll(self):return self.returncode
    def terminate(self):
        self.actions.append('terminate')
        if not self.force_kill:self.returncode=-15
    def kill(self):self.actions.append('kill');self.returncode=-9
    def wait(self,timeout=None):
        self.actions.append('wait')
        if self.returncode is None:raise subprocess.TimeoutExpired('owned control child',timeout)
        return self.returncode


class ProcessReceiptTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.bound={'request_digest':'sha256:fixture'}

    def test_only_registered_exact_child_is_stopped(self):
        child=Child();unrelated=Child()
        owned=processes.OwnedProcesses(self.root,self.bound,'sdk')
        owned.add(child,['codex','app-server']);owned.stop_all()
        self.assertEqual(child.actions,['terminate','wait'])
        self.assertEqual(unrelated.actions,[])
        row=read_json(self.root/'sdk-processes.json')['processes'][0]
        self.assertTrue(row['local_exit_observed'])
        self.assertEqual(row['returncode'],-15)

    def test_completed_child_is_not_terminated_again(self):
        child=Child(already_done=True)
        owned=processes.OwnedProcesses(self.root,self.bound,'helper')
        owned.add(child,['python','helper']);owned.stop_all()
        self.assertEqual(child.actions,[])
        self.assertEqual(read_json(self.root/'helper-processes.json')['binding'],self.bound)

    def test_hung_exact_child_uses_kill_and_wait(self):
        child=Child(force_kill=True)
        owned=processes.OwnedProcesses(self.root,self.bound,'cli')
        owned.add(child,['codex','exec']);owned.stop_all()
        self.assertEqual(child.actions,['terminate','wait','kill','wait'])
        self.assertEqual(read_json(self.root/'cli-processes.json')['processes'][0]['returncode'],-9)

    def test_failed_ownership_receipt_still_stops_child(self):
        child=Child();owned=processes.OwnedProcesses(self.root,self.bound,'sdk')
        original=processes.atomic_json;count=[0]
        def once(*args):
            count[0]+=1
            if count[0]==1:raise OSError('injected receipt failure')
            return original(*args)
        with patch.object(processes,'atomic_json',side_effect=once):
            with self.assertRaises(OSError):owned.add(child,['codex','app-server'])
        self.assertIsNotNone(child.poll())

    def test_invalid_process_kind_does_not_create_files(self):
        with self.assertRaises(ValueError):processes.OwnedProcesses(self.root,self.bound,'scan-all-pids')
        self.assertEqual(list(self.root.iterdir()),[])

    def test_local_helper_exit_alone_is_not_a_completed_model_result(self):
        cli=self.root/'codex.exe';cli.write_bytes(b'fixture')
        cfg={'version':2,'backend':'cli','cli_path':str(cli),'cli_sha256':hashlib.sha256(cli.read_bytes()).hexdigest(),
             'cwd':str(self.root),'codex_home':str(self.root),'timeout_seconds':30}
        value={'schema':'sdm-model-request/v1','purpose':'result-analysis','model':'fixture-model',
            'contract_id':'fixture/v1','prompt':'No model execution','evidence':{},'output_schema':{'type':'object'}}
        folder=self.root/'call';runner=ModelRunner(cfg)
        with patch('model_runner.subprocess.Popen',return_value=Child(already_done=True)) as launch:
            with self.assertRaisesRegex(ValueError,'unknown_requires_reconciliation'):
                runner.run(value,folder,lambda result:None)
            self.assertEqual(launch.call_count,1)
        self.assertTrue(read_json(folder/'helper-processes.json')['processes'][0]['local_exit_observed'])
        def never(*args):self.fail('Unknown request repeated')
        with self.assertRaisesRegex(ValueError,'unknown_requires_reconciliation'):
            runner.run(value,folder,lambda result:None,launcher=never)

    def test_control_probe_not_silently_enabled_for_old_transport(self):
        cli=self.root/'codex.exe';cli.write_bytes(b'fixture')
        cfg={'version':1,'backend':'cli','control_probe':True,'cli_path':str(cli),
            'cli_sha256':hashlib.sha256(cli.read_bytes()).hexdigest(),'cwd':str(self.root),
            'codex_home':str(self.root),'timeout_seconds':30}
        with self.assertRaisesRegex(ValueError,'explicit_owned_transport_probe_required'):ModelRunner(cfg)


if __name__=='__main__':unittest.main()
