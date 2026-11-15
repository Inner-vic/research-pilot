"""Worker/ModelRunner result integration. All model outputs are control fixtures."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_research_worker as fixtures
import research_worker as worker
import research_decision_store as store
import research_model_runner as bridge
from review_storage import atomic_json,read_json
from task_pipeline import digest


class WorkerModelRunnerTests(unittest.TestCase):
    def test_critic_digest_lock_does_not_constrain_reason_or_challenges(self):
        candidate={'finding':'actual retained evidence'}
        schema=bridge.output_schema('b',{}, {},{},candidate)
        self.assertEqual(schema['properties']['draft_digest']['enum'],[digest(candidate)])
        self.assertEqual(schema['properties']['reason'],{'type':'string'})
        challenge=schema['properties']['challenges']['items']['properties']
        for key in ('id','basis','resolution_criterion'):
            self.assertEqual(challenge[key],{'type':'string'})
    def setUp(self):
        self.case=fixtures.ResearchWorkerTests();self.case.setUp()
        cfg=self.case.config['research_worker'];cfg['version']=5
        cfg['model_runner']={'version':1,'backend':'cli','cli_path':cfg['cli_path'],
            'cli_sha256':hashlib.sha256(Path(cfg['cli_path']).read_bytes()).hexdigest(),
            'cwd':str(self.case.root),'codex_home':cfg['codex_home'],'timeout_seconds':30}
        atomic_json(cfg['project_path'],self.case.config)

    def tearDown(self):self.case.tearDown()

    def never(self,*args):self.fail('Repeated completed or unknown model invocation')

    def test_strategy_dialogue_v2_reaches_both_result_requests(self):
        self.case.config['research_worker']['dialogue_version']=2
        atomic_json(self.case.config['research_worker']['project_path'],self.case.config)
        self.assertEqual(self.case.review()['status'],'approved')
        requests=[read_json(p)['request'] for p in self.case.root.glob('model-calls/*/request.json')]
        self.assertEqual(len(requests),2)
        for request in requests:
            self.assertIn('Stability is not learning',request['prompt'])
            self.assertIn('different level',request['prompt'])
        self.assertEqual(self.case.review(self.never)['status'],'approved')

    def test_distinct_models_share_exact_original_packet_and_raw_sources(self):
        result=self.case.review()
        self.assertEqual(result['status'],'approved')
        requests=[read_json(p)['request'] for p in self.case.root.glob('model-calls/*/request.json')]
        self.assertEqual(len(requests),2)
        self.assertEqual({x['model'] for x in requests},{'model-a','model-b'})
        self.assertEqual({x['purpose'] for x in requests},{'result-analysis','result-critique'})
        self.assertEqual(requests[0]['evidence']['shared'],requests[1]['evidence']['shared'])
        shared=requests[0]['evidence']['shared']
        self.assertEqual(shared['packet'],self.case.packet)
        source=next(x for x in shared['raw_sources'] if x['path']==str(self.case.source))
        self.assertEqual(source['text'],self.case.source.read_text())
        self.assertEqual(self.case.review(self.never)['status'],'approved')
        self.assertEqual(self.case.calls,['a','b'])

    def test_wire_nulls_removed_without_changing_null_fact(self):
        self.case.packet['evidence']['exit_code']=None
        atomic_json(self.case.request,self.case.packet)
        self.case.draft['request_digest']=digest(self.case.packet)
        self.case.draft['facts'][0]['value']=None
        def wire(cfg,role,prompt,folder):
            result=self.case.caller(cfg,role,prompt,folder)
            if role=='a':
                result['workflow_assessment']=None
                result['next_step']['evidence_paths']=None
                result['next_step']['iteration']=None
            return result
        self.assertEqual(self.case.review(wire)['status'],'approved')
        response=read_json(self.case.response)
        self.assertIsNone(response['facts'][0]['value'])
        self.assertNotIn('workflow_assessment',response)
        self.assertNotIn('evidence_paths',response['next_step'])

    def test_unknown_transport_survives_replay_without_new_call(self):
        def unknown(*args):
            self.case.calls.append('unknown');raise OSError('unknown backend outcome')
        self.assertEqual(self.case.review(unknown)['status'],'blocked')
        self.assertEqual(self.case.review(self.never)['status'],'blocked')
        self.assertEqual(self.case.calls,['unknown'])

    def test_missing_claimed_transport_never_becomes_a_new_call(self):
        def unknown(*args):raise OSError('unknown backend')
        self.case.review(unknown)
        request=next(self.case.root.glob('model-calls/*/request.json'))
        request.unlink()  # generated fixture only: simulate lost transport state
        self.assertEqual(self.case.review(self.never)['status'],'blocked')

    def test_backend_completion_before_parent_receipt_can_be_recovered(self):
        original=bridge.complete
        def crash(*args,**kwargs):raise OSError('after backend completion before parent receipt')
        with patch.object(bridge,'complete',side_effect=crash):
            self.assertEqual(self.case.review()['status'],'blocked')
        self.assertEqual(self.case.calls,['a'])
        self.assertEqual(self.case.review()['status'],'approved')
        self.assertEqual(self.case.calls,['a','b'])

    def test_invalid_known_completed_reply_gets_original_feedback(self):
        def correct(cfg,role,prompt,folder):
            first=not self.case.calls
            result=self.case.caller(cfg,role,prompt,folder)
            if first:result['facts'][0]['value']=999
            elif role=='a':self.assertIn('validation_error',prompt)
            return result
        self.assertEqual(self.case.review(correct)['status'],'approved')
        self.assertEqual(self.case.calls,['a','a','b'])
        self.assertEqual(len(list(self.case.root.glob('model-calls/*/backend-completed.json'))),3)

    def test_missing_evidence_stops_without_publication_or_repeat(self):
        def wait(cfg,role,prompt,folder):
            result=self.case.caller(cfg,role,prompt,folder)
            if role=='b':result.update(verdict='needs-evidence',reason='Actual model result absent')
            return result
        self.assertEqual(self.case.review(wait)['status'],'needs-evidence')
        self.assertEqual(self.case.review(self.never)['status'],'needs-evidence')
        self.assertFalse(self.case.response.exists())
        self.assertEqual(self.case.calls,['a','b'])

    def test_repeated_revision_stalls_without_fixed_debate_count(self):
        def revise(cfg,role,prompt,folder):
            result=self.case.caller(cfg,role,prompt,folder)
            if role=='b':result.update(verdict='revise',reason='Need an evidence-grounded change',
                challenges=[{'id':'evidence','basis':'No valid model result','resolution_criterion':'Explain uncertainty',
                             'severity':'major','status':'open'}])
            return result
        self.assertEqual(self.case.review(revise)['status'],'stalled')
        self.assertFalse(self.case.response.exists())
        self.assertEqual(self.case.calls,['a','b','a'])

    def test_tampered_raw_completion_blocks_coordinator_before_entry(self):
        self.case.review()
        path=next(self.case.root.glob('model-calls/*/backend-completed.json'))
        value=read_json(path);value['metadata']['changed']=True;atomic_json(path,value)
        entered=[]
        with self.assertRaisesRegex(ValueError,'model_receipt_integrity_failed'):
            worker.advance_checked(self.case.config,self.case.root,lambda *a,**k:entered.append(True))
        self.assertEqual(entered,[])

    def test_large_raw_text_is_explicitly_bounded(self):
        self.case.source.write_text('head'+('x'*20000)+'tail',encoding='utf-8')
        self.case.state['review']['verified_material'][0]['digest']='sha256:'+hashlib.sha256(self.case.source.read_bytes()).hexdigest()
        self.case.review()
        shared=read_json(next(self.case.root.glob('research-decisions/*/shared-evidence.json')))['evidence']
        source=next(x for x in shared['raw_sources'] if x['path']==str(self.case.source))
        self.assertGreater(source['omitted_bytes'],0)
        self.assertTrue(source['head'].startswith('head'))
        self.assertTrue(source['tail'].endswith('tail'))

    def test_strict_schema_requires_all_object_properties(self):
        schema=bridge.output_schema('a',self.case.config,self.case.packet,self.case.state)
        def check(value):
            if isinstance(value,dict):
                if value.get('type')=='object':
                    self.assertFalse(value['additionalProperties'])
                    self.assertEqual(set(value['required']),set(value['properties']))
                for child in value.values():check(child)
            elif isinstance(value,list):
                for child in value:check(child)
        check(schema)
        self.assertEqual(schema['type'],'object')


if __name__=='__main__':unittest.main()


def test_public_v5_result_backend_keeps_main_study_consumption(tmp_path):
    import os
    if os.name=='nt':
        import pytest
        pytest.skip('Shared main journal control integration uses Linux fcntl')
    from test_research_consumption import integrated_case,step,until_wait,approve,public
    from test_devspace_main_workflow import plan_reply
    import devspace_study as study
    for is_study in (False,True):
        folder=tmp_path/('study' if is_study else 'main');folder.mkdir()
        project,root=integrated_case(folder,is_study)
        cfg=project['research_worker'];cfg['version']=5
        cfg['model_runner']={'version':1,'backend':'cli','cli_path':cfg['cli_path'],
            'cli_sha256':hashlib.sha256(Path(cfg['cli_path']).read_bytes()).hexdigest(),
            'cwd':str(folder),'codex_home':cfg['codex_home'],'timeout_seconds':30}
        if is_study:project['devspace_study']['authorization']['study_digest']=study.binding(project)
        atomic_json(cfg['project_path'],project)
        first=step(project,root);plan_reply(first);first=until_wait(project,root)
        assert first['status']=='awaiting-review'
        approve(project,root,first,'run-next-round' if is_study else 'stop')
        next_state=until_wait(project,root)
        assert all(read_json(p)['consumed'] for p in root.glob('research-decisions/*/commit.json'))
        if is_study:
            assert next_state['status']=='awaiting-main-plan-review' and next_state['active_round']==2
            plan_reply(next_state);second=until_wait(project,root)
            approve(project,root,second,'stop')
            assert until_wait(project,root)['status']=='completed'
        else:assert next_state['status']=='completed'
        before={str(p):p.read_bytes() for p in (folder/'remote').rglob('process.json')}
        assert public(project,root)['status']=='completed'
        assert before=={str(p):p.read_bytes() for p in (folder/'remote').rglob('process.json')}
