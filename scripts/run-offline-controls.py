"""Run the qualified governance controls without models, platform writes or ML."""
import argparse
import os
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
L=ROOT/'skills/sdm-module/sdm-lifecycle'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--junit',default='build/offline-controls.xml')
    args=parser.parse_args()
    sys.dont_write_bytecode=True
    for path in (L/'scripts',L/'scripts/tests',L/'tests',ROOT/'skills/sdm-module/tests'):
        sys.path.insert(0,str(path))
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    import pytest
    class OfflineOnly:
        @pytest.fixture(autouse=True)
        def no_live_transports(self,monkeypatch):
            from lpai_manager_backend import OfficialBackend
            def denied(*args,**kwargs):raise AssertionError('Offline controls cannot contact real models or platforms')
            for name in ('cli','read','mutate'):monkeypatch.setattr(OfficialBackend,name,denied)
            from research_execution import CommandBackend
            monkeypatch.setattr(CommandBackend,'call',denied)
            from model_runner import ModelRunner
            monkeypatch.setattr(ModelRunner,'_launch',denied)
    report=Path(args.junit).resolve();report.parent.mkdir(parents=True,exist_ok=True)
    tests=['test_research_acceptance.py','test_prepared_receipt_recovery.py',
           'test_component_submission_gate.py','test_lifecycle_view.py','test_failed_task_repair.py',
           'test_project_integration.py','test_project_evaluation_controls.py','test_research_launch.py','test_comparison_reference.py']
    # Keep diagnostic files in the OS temporary area. Windows can retain handles
    # to frozen replay subprocess evidence briefly after a passing test; deleting
    # that tree here made successful verification exit with a cleanup exception.
    temp=Path(tempfile.mkdtemp(prefix='rp-controls-'))
    return pytest.main(['-p','no:cacheprovider','-q','--tb=short','--confcutdir',str(L/'tests'),
        '--basetemp',str(temp/'cases'),'--junitxml',str(report),
        *[str(L/'tests'/name) for name in tests]],plugins=[OfflineOnly()])


if __name__=='__main__':raise SystemExit(main())
