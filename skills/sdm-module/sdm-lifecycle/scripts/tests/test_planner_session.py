"""Continuity, critic isolation and failure recovery; synthetic transport only."""
import copy
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import planner_session as sessions
from model_runner import ModelRunner, InvalidModelReply, binding
from model_runner_transport import save
import model_context
from research_model_receipts import checked, seal
from review_storage import atomic_json, read_json, locked


class Report:
    def __init__(self, raw): self.raw = raw
    def model_dump(self, **kwargs): return copy.deepcopy(self.raw)


@unittest.skipUnless(os.name=='nt','Persistent planner uses the qualified Windows-contained SDK runner')
class PlannerSessionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup); self.root = Path(tmp.name)
        cli = self.root/'codex.exe'; cli.write_bytes(b'never executed')
        deps = self.root/'sdk'; (deps/'openai_codex').mkdir(parents=True)
        (deps/'openai_codex/client.py').write_text('# marker only')
        self.cfg = {'version':4,'backend':'sdk','timeout_seconds':30,'cli_path':str(cli),
            'cli_sha256':hashlib.sha256(cli.read_bytes()).hexdigest(),'cwd':str(self.root),
            'codex_home':str(self.root),'sdk_root':str(deps),'sdk_version':'0.163.0',
            'sdk_recovery':{'version':2},'context_policy':{'version':1,'user_config':'inherit-with-overrides',
                'project_doc_max_bytes':0,'reasoning_effort':'medium','input_kind':'tool-output'}}
        self.policy = {'planner_context':{'version':1,'root':str(self.root/'planner')}}
        self.base = {'schema':'sdm-model-request/v1','purpose':'proposal','contract_id':'fixture/v1',
            'model':'fixture-generator','input_kind':'tool-output','evidence':{},'prompt':'Fixture only',
            'output_schema':{'type':'object','properties':{'answer':{'type':'string'}},
                'required':['answer'],'additionalProperties':False}}
        self.launches = []

    def request(self, n=1):
        value = copy.deepcopy(self.base); value['evidence'] = {'result':n}
        return sessions.attach(value,self.policy,self.cfg)

    def report(self, thread, turns=()):
        return Report({'thread':{'id':thread,'turns':[{'id':t,'status':'completed'} for t in turns]},
            'model':'fixture-generator','modelProvider':'fixture','cwd':str(self.root),'approvalPolicy':'never',
            'reasoningEffort':'medium','sandbox':{'type':'readOnly','networkAccess':False},'instructionSources':[]})

    def launch(self, folder, envelope):
        plan = checked(folder/'planner-call.json','sdm-planner-call/v1')
        self.launches.append(plan)
        thread = plan['thread_id'] or 'native-science-thread'
        model_context.declared(self.cfg,envelope['request'],model_context.sdk_options(self.cfg)['launch_args_override'],folder,envelope['binding'])
        model_context.sdk_effective(self.cfg,envelope['request'],self.report(thread),folder,envelope['binding'])
        atomic_json(folder/'planner-thread.json',seal({'schema':'sdm-planner-thread/v1','call':plan['call'],
            'thread_id':thread,'operation':'resume' if plan['thread_id'] else 'start'}))
        save(folder,envelope['binding'],'completed',{'answer':'fixture'},
             {'backend':'sdk','thread_id':thread,'turn_id':f"turn-{plan['sequence']}"})

    def run_call(self, n=1, validator=lambda value:None, launcher=None):
        return ModelRunner(self.cfg).run(self.request(n),self.root/f'call-{n}',validator,launcher=launcher or self.launch)

    def test_two_science_subjects_continue_one_thread_and_replay_without_call(self):
        first = self.run_call(1); self.run_call(2)
        self.assertIsNone(self.launches[0]['thread_id'])
        self.assertEqual(self.launches[1]['thread_id'],'native-science-thread')
        self.assertEqual(self.launches[1]['turn_ids'],['turn-1'])
        self.assertEqual(self.run_call(1),first); self.assertEqual(len(self.launches),2)
        state = sessions.state(self.root/'planner',sessions.identity(self.cfg,self.request()))
        self.assertEqual(state['sequence'],2); self.assertIsNone(state['pending'])

    def test_unknown_call_blocks_next_subject_and_reconciles_same_receipt(self):
        def unknown(folder,envelope): raise OSError('Injected disconnect')
        with self.assertRaises(OSError): self.run_call(1,launcher=unknown)
        with self.assertRaisesRegex(ValueError,'original_reconciliation'): self.run_call(2)
        self.assertFalse((self.root/'call-2/intent.json').exists())
        envelope = read_json(self.root/'call-1/request.json'); self.launch(self.root/'call-1',envelope)
        self.run_call(1); self.run_call(2)
        self.assertEqual(len(self.launches),2)

    def test_invalid_completed_draft_is_retained_but_not_called_an_accepted_plan(self):
        def invalid(value): raise ValueError('Invalid scientific draft')
        with self.assertRaises(InvalidModelReply): self.run_call(1,validator=invalid)
        row = read_json(self.root/'planner/turns/000001.json')
        self.assertEqual(row['purpose_validation'],'failed'); self.assertFalse(row['execution_authority'])
        self.run_call(2); self.assertEqual(self.launches[-1]['turn_ids'],['turn-1'])

    def test_other_model_cannot_inherit_same_planner(self):
        self.run_call(1); value = self.request(2); value['model']='other-generator'
        with self.assertRaisesRegex(ValueError,'identity_changed'):
            ModelRunner(self.cfg).run(value,self.root/'other',lambda _:None,launcher=self.launch)
        self.assertEqual(len(self.launches),1)

    def test_critic_and_code_review_never_receive_planner_binding(self):
        for purpose in ('method-review','result-critique'):
            value = {**self.base,'purpose':purpose}
            self.assertNotIn('planner_context',sessions.attach(value,self.policy,self.cfg))
            value['planner_context']=self.policy['planner_context']
            with self.assertRaisesRegex(ValueError,'independent_reviewer'):
                ModelRunner(self.cfg).run(value,self.root/purpose,lambda _:None,launcher=self.launch)
        self.assertNotIn('planner_context',sessions.attach(dict(self.base),self.policy,self.cfg,implementation=True))

    def test_cli_and_ephemeral_sdk_are_not_silent_continuity_fallbacks(self):
        for changed in ({'backend':'cli'},{'sdk_recovery':None},{'version':3}):
            with self.subTest(changed=changed),self.assertRaisesRegex(ValueError,'durable_sdk'):
                sessions.selection(self.policy,{**self.cfg,**changed})

    def test_deleted_session_and_corrupt_chain_do_not_start_a_new_thread(self):
        self.run_call(1)
        path = self.root/'planner/session.json'; original = path.read_bytes(); path.unlink()
        with self.assertRaisesRegex(ValueError,'session_missing'): self.run_call(2)
        path.write_bytes(original)
        row = read_json(self.root/'planner/turns/000001.json'); row['turn_id']='rewritten'
        atomic_json(self.root/'planner/turns/000001.json',seal(row))
        with self.assertRaisesRegex(ValueError,'head_changed'): self.run_call(2)
        self.assertEqual(len(self.launches),1)

    def test_parallel_planner_is_blocked(self):
        with locked(self.root/'planner','.planner.lock'):
            with self.assertRaisesRegex(ValueError,'conversation_busy'): self.run_call(1)

    def test_resume_rejects_foreign_native_turn_before_sending(self):
        self.run_call(1)
        value=self.request(2); folder=self.root/'call-2'; expected=binding(self.cfg,value)
        class Client:
            def thread_resume(self,*args,**kwargs): return self_response
        client=Client()
        codex=types.SimpleNamespace(thread_resume=lambda *a,**k:client.thread_resume(*a,**k))
        modules={'openai_codex':types.SimpleNamespace(Sandbox=types.SimpleNamespace(read_only='read-only'),
                    ApprovalMode=types.SimpleNamespace(deny_all='deny-all')),
                 'openai_codex.client':types.SimpleNamespace(CodexClient=Client)}
        with sessions.using(self.cfg,value,folder):
            self_response=self.report('native-science-thread',['turn-1','unexpected-user-turn'])
            with patch.dict(sys.modules,modules),self.assertRaisesRegex(ValueError,'native_history_changed'):
                sessions.open_thread(codex,self.cfg,value,folder,expected,{},Client.thread_resume)
        self.assertFalse((folder/'sdk-turn-intent.json').exists())

    def test_legacy_requests_do_not_create_or_require_session_state(self):
        with sessions.using(self.cfg,self.base,self.root/'legacy'): pass
        self.assertFalse((self.root/'planner').exists())

    def test_original_proposal_gate_continues_planner_across_subjects_and_keeps_critics_separate(self):
        import test_proposal_model_runner as fixtures
        fixture=fixtures.ProposalRunnerTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.cfg['proposal_review'].update(model_runner=self.cfg,**self.policy)
        fixture.context()
        observed=[]
        def write(folder,expected,status,raw,metadata):
            request=read_json(folder/'request.json')['request']
            selected=request.get('planner_context')
            if selected:
                plan=checked(folder/'planner-call.json','sdm-planner-call/v1')
                thread=plan['thread_id'] or 'adapter-planner'
                turn=f"planner-{plan['sequence']}"
                atomic_json(folder/'planner-thread.json',seal({'schema':'sdm-planner-thread/v1',
                    'call':plan['call'],'thread_id':thread}))
            else:
                thread=f'critic-{len(observed)}'; turn=thread+'-turn'
            report=self.report(thread); report.raw['model']=request['model']
            model_context.declared(self.cfg,request,model_context.sdk_options(self.cfg)['launch_args_override'],folder,expected)
            model_context.sdk_effective(self.cfg,request,report,folder,expected)
            observed.append((request['purpose'],thread,selected))
            save(folder,expected,status,raw,{'backend':'sdk','thread_id':thread,'turn_id':turn})
        with patch.object(fixtures,'save',write):
            self.assertEqual(fixture.gate()['status'],'proposal-approved')
            self.assertEqual(fixture.gate()['status'],'proposal-approved')
            self.assertEqual(len(observed),2)
            fixture.root=fixture.root/'successor'; fixture.root.mkdir(); fixture.context()
            self.assertEqual(fixture.gate()['status'],'proposal-approved')
            self.assertEqual(fixture.gate()['status'],'proposal-approved')
        self.assertEqual(len(observed),4)
        self.assertEqual(observed[0][1],observed[2][1])
        self.assertNotEqual(observed[1][1],observed[3][1])
        self.assertIsNone(observed[1][2]); self.assertIsNone(observed[3][2])


if __name__=='__main__': unittest.main()
