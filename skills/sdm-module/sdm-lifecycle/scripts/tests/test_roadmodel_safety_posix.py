"""Real POSIX process groups with independent surviving-control process."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from roadmodel_runtime_safety import guarded_training,TrainingProcessError
import roadmodel_runtime_safety as safety


class Sampler:
    def start(self):pass
    def finish(self):return {}


@unittest.skipUnless(os.name=='posix','real POSIX process-group test')
class ProcessGroups(unittest.TestCase):
    def case(self,exit_code,wait=False):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pidfile=root/'descendant.pid'
            code="import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path("+repr(str(pidfile))+").write_text(str(os.getpid()));time.sleep(30)"
            leader="import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c',"+repr(code)+"]);\nwhile not Path("+repr(str(pidfile))+").exists():time.sleep(.01)\n"
            leader+=("time.sleep(30)" if wait else 'raise SystemExit('+str(exit_code)+')')
            unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
            try:
                if exit_code or wait:
                    with self.assertRaises(TrainingProcessError):
                        guarded_training([sys.executable,'-c',leader],root,os.environ.copy(),root/'log',root/'progress',
                            Sampler(),lambda value:None,maximum_seconds=10,stall_seconds=4 if wait else 5)
                else:
                    guarded_training([sys.executable,'-c',leader],root,os.environ.copy(),root/'log',root/'progress',
                        Sampler(),lambda value:None,maximum_seconds=8,stall_seconds=5)
                pid=int(pidfile.read_text());status=Path('/proc')/str(pid)/'stat'
                for _ in range(30):
                    if not status.exists() or status.read_text().split()[2]=='Z':break
                    time.sleep(.05)
                self.assertTrue(not status.exists() or status.read_text().split()[2]=='Z','descendant still running')
                self.assertIsNone(unrelated.poll(),'cleanup killed unrelated allocation')
            finally:
                unrelated.terminate();unrelated.wait(timeout=5)

    def test_normal_leader_exit_reaps_ignoring_descendant(self):self.case(0)
    def test_failed_leader_exit_reaps_ignoring_descendant(self):self.case(7)
    def test_timeout_reaps_group_even_when_descendant_ignores_sigterm(self):self.case(0,wait=True)

    def test_unconfirmed_group_exit_cannot_report_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(safety,'live_process_group_members',return_value=[999999]):
                with self.assertRaisesRegex(RuntimeError,'exit_unconfirmed'):
                    guarded_training([sys.executable,'-c','pass'],root,os.environ.copy(),root/'log',root/'progress',
                        Sampler(),lambda value:None,maximum_seconds=8,stall_seconds=5)

    def test_unconfirmed_group_exit_preserves_primary_training_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(safety,'live_process_group_members',return_value=[999999]):
                with self.assertRaises(TrainingProcessError) as caught:
                    guarded_training([sys.executable,'-c','raise SystemExit(7)'],root,os.environ.copy(),root/'log',root/'progress',
                        Sampler(),lambda value:None,maximum_seconds=8,stall_seconds=5)
            self.assertEqual(caught.exception.exit_code,7)


if __name__=='__main__':unittest.main()
