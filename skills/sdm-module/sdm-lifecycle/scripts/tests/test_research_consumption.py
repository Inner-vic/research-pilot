"""Durable shared-executor acceptance. Control fixtures, no ML or platform."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import test_research_worker as fixtures
import research_worker as worker
import research_decision_store as store
import research_consumption as consumption
from review_storage import atomic_json, read_json
from task_pipeline import digest


class ConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ResearchWorkerTests()
        self.case.setUp()
        self.case.config['research_worker']['version'] = 4
        atomic_json(self.case.config['research_worker']['project_path'], self.case.config)
        self.case.review()
        self.commit = next(self.case.root.glob('research-decisions/*/commit.json'))

    def tearDown(self):
        self.case.tearDown()

    def state(self):
        from loop_analysis import validate
        plan = self.case.config['devspace_execution']['plan']
        return {'status':'completed','binding':digest(plan),'uid':plan['resource']['uid'],
            'review':{'status':'completed','case_directory':str(self.case.response.parent),
                      'decision':validate(self.case.draft,self.case.packet)}}

    def save_acceptance(self):
        state = self.state()
        consumption.stamp(self.case.config, self.case.root, state)
        atomic_json(self.case.root / 'execution.json', state)
        return state

    def recover(self):
        return store.before_advance(self.case.config, self.case.root)

    def test_completion_and_acceptance_share_atomic_executor_state(self):
        self.save_acceptance()
        self.recover()
        value = read_json(self.commit)
        self.assertTrue(value['consumed'])
        self.assertEqual(value['consumption_evidence']['state_path'], str(self.case.root/'execution.json'))
        self.assertEqual(self.case.calls, ['a','b'])

    def test_unpersisted_acceptance_is_not_completion(self):
        state = self.state()
        consumption.stamp(self.case.config, self.case.root, state)
        self.recover()
        self.assertFalse(read_json(self.commit)['consumed'])

    def test_completed_return_alone_cannot_consume(self):
        with self.assertRaisesRegex(ValueError,'durable_coordinator_acceptance_required'):
            store.consumed(self.case.config,self.case.root,[(self.commit,read_json(self.commit))],self.state())
        self.assertFalse(read_json(self.commit)['consumed'])

    def test_persisted_acceptance_reconciles_after_control_revision(self):
        self.save_acceptance()
        worker.control(self.case.root,True)
        worker.control(self.case.root,False)
        self.recover()
        self.assertTrue(read_json(self.commit)['consumed'])

    def test_stale_unaccepted_decision_remains_blocked(self):
        worker.control(self.case.root,True)
        worker.control(self.case.root,False)
        with self.assertRaisesRegex(ValueError,'stale_decision_requires_reconciliation'):
            self.recover()
        self.assertFalse(read_json(self.commit)['consumed'])

    def test_changed_acceptance_blocks_before_coordinator(self):
        state = self.save_acceptance()
        state['review']['research_acceptance']['control_revision'] += 1
        atomic_json(self.case.root/'execution.json',state)
        entered = []
        with self.assertRaisesRegex(ValueError,'durable_coordinator_acceptance_changed'):
            worker.advance_checked(self.case.config,self.case.root,lambda *a,**k:entered.append(True))
        self.assertEqual(entered,[])

    def test_uid_binding_case_and_response_cannot_be_substituted(self):
        original = self.save_acceptance()
        for field in ('uid','binding','case','response'):
            with self.subTest(field=field):
                state = copy.deepcopy(original)
                if field in {'uid','binding'}:state[field] = 'different'
                elif field == 'case':state['review']['case_directory'] = str(self.case.root/'another')
                else:state['review']['decision']['reason'] += '; changed'
                atomic_json(self.case.root/'execution.json',state)
                with self.assertRaisesRegex(ValueError,'coordinator_acceptance_binding_invalid'):
                    self.recover()
                self.assertFalse(read_json(self.commit)['consumed'])

    def test_consumption_write_then_failure_recovers_without_callback(self):
        self.save_acceptance()
        original = consumption.atomic_json
        def fail(path,value):
            original(path,value)
            raise OSError('after consumption commit')
        with patch.object(consumption,'atomic_json',side_effect=fail):
            with self.assertRaises(OSError):self.recover()
        self.recover()
        self.assertTrue(read_json(self.commit)['consumed'])

    def test_consumed_snapshot_survives_later_executor_state_updates(self):
        original = self.save_acceptance()['review']['research_acceptance']
        self.recover()
        state = self.state()
        state['review']['resumed'] = True
        consumption.stamp(self.case.config,self.case.root,state)
        atomic_json(self.case.root/'execution.json',state)
        self.recover()
        historical = read_json(self.commit)['consumption_evidence']
        self.assertEqual(historical['acceptance'],original)
        self.assertEqual(historical['acceptance_digest'],digest(original))
        self.assertNotEqual(state['review']['research_acceptance'],original)


def integrated_case(tmp_path, study_enabled):
    from test_devspace_main_workflow import fixture
    import devspace_study as study
    config, root = fixture(tmp_path)
    cli = tmp_path/'codex.exe'; cli.touch()
    config['research_worker'] = {'version':4,'enabled':True,'cli_path':str(cli),
        'codex_home':str(tmp_path),'project_path':str(tmp_path/'project.json'),
        'coordinator_path':str(Path(worker.__file__).with_name('run_project_pipeline.py')),
        'output':str(root),'models':{'a':'fixture-a','b':'fixture-b'}}
    if study_enabled:
        config['devspace_study']['authorization']['study_digest'] = study.binding(config)
    else:
        config.pop('devspace_study')
        config['devspace_execution']['plan']['argv'] += ['--learning-rate','.01']
        config['devspace_execution']['authorization']['plan_digest'] = digest(config['devspace_execution']['plan'])
    atomic_json(config['research_worker']['project_path'],config)
    return config, root


def step(config, root):
    import devspace_execution as single
    return single.project_execution(config,root)(config,root)


def until_wait(config,root):
    for _ in range(80):
        state = step(config,root)
        if state['status'] not in {'running','cleanup-required','launch-outcome-unknown'}:return state
        time.sleep(.05)
    raise AssertionError(state)


def approve(config,root,state,action):
    from test_devspace_main_workflow import result_reply
    result_reply(state,action)
    response = Path(state['review']['response_path'])
    draft = read_json(response)
    draft['workflow_assessment']['alternatives'] = [draft['workflow_assessment']['alternatives']]
    response.unlink()  # only a generated control-fixture draft, before publication
    def caller(cfg,role,prompt,folder):
        return copy.deepcopy(draft) if role=='a' else {'draft_digest':digest(draft),
            'verdict':'approve','reason':'Control acceptance only','challenges':[]}
    assert worker.review(config,root,state,caller)['status']=='approved'


def public(config,root):
    result = subprocess.run([sys.executable,'-B',str(Path(worker.__file__).with_name('run_project_pipeline.py')),
        '--project',config['research_worker']['project_path'],'--output',str(root),'--devspace-execution'],
        capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


def test_public_main_and_study_reconcile_after_coordinator_return(tmp_path):
    """Two independent fixtures, actual harmless subprocesses, no ML."""
    if os.name == 'nt':
        import pytest
        pytest.skip('Main journal control integration uses Linux fcntl')
    from test_devspace_main_workflow import plan_reply
    for study_enabled in (False,True):
        folder=tmp_path/('study' if study_enabled else 'main');folder.mkdir()
        config,root=integrated_case(folder,study_enabled)
        state=step(config,root)
        assert state['status']=='awaiting-main-plan-review'
        plan_reply(state);state=until_wait(config,root)
        assert state['status']=='awaiting-review'
        approve(config,root,state,'run-next-round' if study_enabled else 'stop')
        first_execution = root/'round-01' if study_enabled else root
        with patch.object(store,'consumed',side_effect=OSError('after coordinator returned before consume')):
            try:step(config,root)
            except OSError:pass
            else:raise AssertionError('Consumption fault not reached')
        assert read_json(first_execution/'execution.json')['review']['research_acceptance']
        commit=next(root.glob('research-decisions/*/commit.json'))
        assert not read_json(commit)['consumed']
        remote = folder/'remote/r01/process.json' if study_enabled else folder/'remote/process.json'
        first_process=remote.read_bytes()
        resumed=public(config,root)
        assert read_json(commit)['consumed'] and remote.read_bytes()==first_process
        if study_enabled:
            assert resumed['active_round']==2 and resumed['status']=='awaiting-main-plan-review'
            assert not (folder/'remote/r02/process.json').exists()
            plan_reply(resumed);second=until_wait(config,root)
            assert second['status']=='awaiting-review'
            approve(config,root,second,'stop')
            done=until_wait(config,root)
            assert done['status']=='completed' and len(done['history'])==2
            assert all(read_json(p)['consumed'] for p in root.glob('research-decisions/*/commit.json'))
        else:
            assert resumed['status']=='completed'
        before={str(p):p.read_bytes() for p in (folder/'remote').rglob('process.json')}
        assert public(config,root)['status']=='completed'
        assert before=={str(p):p.read_bytes() for p in (folder/'remote').rglob('process.json')}
