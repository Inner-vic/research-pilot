"""Main/reviewer continuity and original native implementation owner integration."""
import copy
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import working_sessions as work
from research_context import Context
from model_runner import ModelRunner,binding
from model_runner_transport import save
from review_storage import atomic_json,read_json
import model_context
from test_planner_session import Report


@unittest.skipUnless(os.name=='nt','Qualified contained SDK is Windows only')
class WorkingTests(unittest.TestCase):
    def setUp(self):
        import test_planner_session as fixtures
        f=fixtures.PlannerSessionTests();f.setUp();self.addCleanup(f.doCleanups);self.f=f;self.root=f.root
        review=self.root/'review-space';review.mkdir()
        self.policy={'working_sessions':{'version':1,'project_id':'fixture-study','model_provider':None,
            'main':{'root':str(self.root/'main-context'),'cwd':str(self.root),'model':'fixture-generator'},
            'review':{'root':str(self.root/'review-context'),'cwd':str(review),'model':'fixture-critic'}}}
        # Context control records live outside both writable workspaces.
        main=self.root/'main-space';main.mkdir();self.policy['working_sessions']['main']['cwd']=str(main)
        self.turns={};self.launches=[];self.cfg={}
    def request(self,role,number):
        cfg,selected=work.select(self.policy,self.f.cfg,role)
        value=copy.deepcopy(self.f.base);value.update(model=selected['model'],purpose='proposal' if role=='a' else 'method-review',evidence={'number':number})
        work.attach(value,selected);return cfg,value
    def native(self):
        owner=self
        class Thread:
            def __init__(self,id):self.id=id
            def read(self,**kw):return Report({'thread':{'id':self.id,'turns':copy.deepcopy(owner.turns[self.id])}})
        class Native:
            def thread_start(self,**kw):
                id='thread-'+kw['model'];owner.turns[id]=[];return Thread(id)
            def thread_resume(self,id,**kw):return Thread(id)
        return Native()
    def launch(self,folder,envelope):
        cfg,value=envelope['config'],envelope['request'];self.launches.append(value)
        ctx=Context(work.context_config(cfg,value));key=work.job(envelope['binding'])
        thread=ctx.open(self.native(),{'model':value['model']},job_id=key);ctx.intent(key)
        turn=f"turn-{len(self.turns[thread.id])+1}";ctx.started(key,turn)
        self.turns[thread.id].append({'id':turn,'status':'completed'})
        model_context.declared(cfg,value,model_context.sdk_options(cfg)['launch_args_override'],folder,envelope['binding'])
        report=self.f.report(thread.id);report.raw.update(model=value['model'],cwd=cfg['cwd'])
        model_context.sdk_effective(cfg,value,report,folder,envelope['binding'])
        save(folder,envelope['binding'],'completed',{'answer':'fixture'},{'backend':'sdk','thread_id':thread.id,'turn_id':turn})
    def call(self,role,n):
        cfg,value=self.request(role,n)
        return ModelRunner(cfg).run(value,self.root/f'{role}-{n}',lambda _:None,launcher=self.launch)
    def implement(self):
        cfg,value=self.request('a',1);ctx=Context(work.context_config(cfg,value))
        with ctx.lock():
            thread=ctx.open(self.native(),{'model':value['model']},job_id='implementation');ctx.intent('implementation')
            turn=f"turn-{len(self.turns[thread.id])+1}";ctx.started('implementation',turn)
            self.turns[thread.id].append({'id':turn,'status':'completed'})
            receipt=self.root/'implementation-result.json'
            atomic_json(receipt,{'job_id':'implementation','status':'completed',
                'detail':{'thread_id':thread.id,'turn_id':turn,'terminal':'completed'}})
            ctx.acknowledge('implementation',receipt)
    def test_plan_implement_and_next_plan_share_main_and_two_reviews_share_other(self):
        a=self.call('a',1);b=self.call('b',1);self.implement();c=self.call('a',2);d=self.call('b',2)
        self.assertEqual(a['metadata']['thread_id'],c['metadata']['thread_id'])
        self.assertEqual(b['metadata']['thread_id'],d['metadata']['thread_id'])
        self.assertNotEqual(a['metadata']['thread_id'],b['metadata']['thread_id'])
        self.assertEqual(work.snapshot(self.policy['working_sessions'])['main']['turn_count'],3)
        self.assertEqual(self.call('a',1),a);self.assertEqual(len(self.launches),4)
    def test_unknown_call_blocks_next_call_including_native_implementation(self):
        cfg,value=self.request('a',1)
        def unknown(folder,envelope):
            ctx=Context(work.context_config(cfg,value));key=work.job(envelope['binding'])
            ctx.open(self.native(),{'model':value['model']},job_id=key);ctx.intent(key)
            raise OSError('Injected disconnect')
        with self.assertRaises(OSError):ModelRunner(cfg).run(value,self.root/'a-1',lambda _:None,launcher=unknown)
        with self.assertRaisesRegex(ValueError,'pending_requires_original'):self.call('a',2)
        with self.assertRaisesRegex(ValueError,'reconcile_only'):self.implement()
        self.assertEqual(len(self.launches),0)
    def test_extra_native_turn_blocks_before_launching_model(self):
        first=self.call('a',1);self.turns[first['metadata']['thread_id']].append({'id':'foreign','status':'completed'})
        with self.assertRaisesRegex(ValueError,'native_history_changed'):self.call('a',2)
    def test_missing_context_and_changed_receipt_are_not_new_conversations(self):
        self.call('a',1);root=Path(self.policy['working_sessions']['main']['root']);path=root/'context.json'
        raw=path.read_bytes();path.unlink()
        with self.assertRaisesRegex(ValueError,'missing_reconcile'):self.call('a',2)
        path.write_bytes(raw);receipt=self.root/'a-1/working-context-receipt.json'
        row=read_json(receipt);row['detail']['turn_id']='changed';atomic_json(receipt,row)
        with self.assertRaisesRegex(ValueError,'receipt_changed'):self.call('a',2)
    def test_role_and_overlapping_roots_rejected(self):
        policy=copy.deepcopy(self.policy);policy['working_sessions']['review']['root']=policy['working_sessions']['main']['root']
        with self.assertRaisesRegex(ValueError,'independent_working'):work.settings(policy,self.f.cfg)
        cfg,value=self.request('b',1);value['working_session']['role']='main'
        with self.assertRaisesRegex(ValueError,'role_or_identity'):work.context_config(cfg,value)
    def test_implementation_task_is_delivered_in_current_packet_under_stable_instructions(self):
        cfg,value=self.request('a',1);owner=work.context_config(cfg,value)
        self.assertTrue(owner['research_context']['stable_instructions'])
        packet={'evidence':[{'text':'untrusted historical instructions'}]}
        task={'kind':'implementation','instructions':'Write only the registered output','evidence_kind':'fixture'}
        delivered=work.execution_packet(task,packet,{**owner,'working_sessions':self.policy['working_sessions']})
        self.assertEqual(delivered['registered_task']['instructions'],task['instructions'])
        self.assertEqual(delivered['registered_task']['workspace'],owner['cwd'])
        self.assertEqual(delivered['evidence'],packet['evidence'])
        self.assertIs(work.execution_packet(task,packet,owner),packet)
    def test_review_instructions_never_allow_implementation_and_unknown_roles_fail(self):
        self.assertIn('always read-only',work.instructions('review'))
        self.assertIn('registered implementation/report',work.instructions('main'))
        with self.assertRaisesRegex(ValueError,'role_required'):work.instructions('executor')
    def test_science_and_implementation_review_use_same_persistent_pair_in_original_gate(self):
        import test_proposal_model_runner as fixtures
        fixture=fixtures.ProposalRunnerTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.cfg['proposal_review'].update(model_runner=self.f.cfg,**self.policy)
        fixture.cfg['proposal_review']['models']={'a':'fixture-generator','b':'fixture-critic'};fixture.context()
        seen=[]
        def write(folder,expected,status,raw,metadata):
            envelope=read_json(folder/'request.json');self.launch(folder,envelope)
            proof=read_json(folder/'backend-completed.json')
            save(folder,expected,status,raw,proof['metadata']);seen.append(envelope['request']['working_session']['role'])
        with patch.object(fixtures,'save',write):
            self.assertEqual(fixture.gate()['status'],'proposal-approved')
            self.assertEqual(fixture.gate()['status'],'proposal-approved');self.assertEqual(len(seen),2)
            self.implement();fixture.root=fixture.root/'next';fixture.root.mkdir();fixture.context()
            self.assertEqual(fixture.gate()['status'],'proposal-approved')
        self.assertEqual(seen,['main','review','main','review'])


if __name__=='__main__':unittest.main()
