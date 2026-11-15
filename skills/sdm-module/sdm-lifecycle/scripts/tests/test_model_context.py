"""Explicit context policy and completion-integrity controls; no actual models."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import model_context as context
from model_runner import ModelRunner,configuration,binding
from model_runner_transport import save
from research_model_receipts import seal
from review_storage import atomic_json,read_json
from task_pipeline import digest


class Start:
    def __init__(self,value):self.value=value
    def model_dump(self,**options):
        assert options=={'mode':'json','by_alias':True,'exclude_unset':True}
        return copy.deepcopy(self.value)


class ContextTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        cli=self.root/'codex.exe';cli.write_bytes(b'never executed')
        deps=self.root/'deps';(deps/'openai_codex').mkdir(parents=True)
        (deps/'openai_codex/client.py').write_text('# Never imported',encoding='utf-8')
        self.cfg={'version':4,'backend':'sdk','timeout_seconds':30,'cli_path':str(cli),
            'cli_sha256':hashlib.sha256(cli.read_bytes()).hexdigest(),'cwd':str(self.root),'codex_home':str(self.root),
            'sdk_version':'0.163.0','sdk_root':str(deps),'context_policy':{'version':1,'user_config':'inherit-with-overrides',
                'project_doc_max_bytes':0,'reasoning_effort':'medium','input_kind':'tool-output'}}
        self.value={'schema':'sdm-model-request/v1','purpose':'result-analysis','model':'fixture-model',
            'input_kind':'tool-output','contract_id':'fixture/v1','prompt':'No inference','evidence':{},
            'output_schema':{'type':'object'}}
        self.expected=binding(self.cfg,self.value)
        self.report={'model':'fixture-model','modelProvider':'fixture','cwd':str(self.root),
            'approvalPolicy':'never','approvalsReviewer':'user','reasoningEffort':'medium',
            'sandbox':{'type':'readOnly','networkAccess':False},'instructionSources':[],'serviceTier':None,
            'thread':{'id':'thread-fixture','private':'must not be saved'},'secret':'must not be saved'}

    def declared(self):
        return context.declared(self.cfg,self.value,context.sdk_options(self.cfg)['launch_args_override'],self.root,self.expected)

    def bundle(self):
        self.declared();context.sdk_effective(self.cfg,self.value,Start(self.report),self.root,self.expected)
        return context.bundle(self.root,self.expected)

    def test_policy_requires_all_fields_and_rejects_silent_extras(self):
        for change in ({'user_config':'inherit'},{'project_doc_max_bytes':True},{'version':True},
                       {'input_kind':'text'},{'reasoning_effort':'arbitrary'},{'extra':'ignored'}):
            cfg=copy.deepcopy(self.cfg);cfg['context_policy'].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):context.policy(cfg)

    def test_old_transports_cannot_silently_accept_new_policy(self):
        for version in (1,2,3):
            with self.subTest(version=version),self.assertRaisesRegex(ValueError,'explicit_context_transport_required'):
                configuration({**self.cfg,'version':version})

    def test_sdk_pinned_launch_flags_without_skipping_governance_rules(self):
        options=context.sdk_options(self.cfg);argv=options['launch_args_override']
        self.assertEqual(argv[0],self.cfg['cli_path']);self.assertNotIn('--ignore-user-config',argv)
        self.assertIn('project_doc_max_bytes=0',argv);self.assertIn('model_reasoning_effort="medium"',argv)
        self.assertNotIn('--ignore-rules',argv);self.assertFalse(options['experimental_api'])

    def test_v2_explicit_history_budget_is_bound_and_missing_override_rejected(self):
        cfg=copy.deepcopy(self.cfg);cfg['context_policy'].update(version=2,tool_output_token_limit=262144)
        argv=context.sdk_options(cfg)['launch_args_override']
        self.assertIn('tool_output_token_limit=262144',argv);context.check_command(cfg,argv)
        missing=[arg for arg in argv if arg!='tool_output_token_limit=262144']
        with self.assertRaisesRegex(ValueError,'launch_policy_changed'):context.check_command(cfg,missing)
        for bad in (True,0,262145):
            cfg['context_policy']['tool_output_token_limit']=bad
            with self.subTest(bad=bad),self.assertRaises(ValueError):context.policy(cfg)

    def test_history_budget_rejects_large_input_before_call_intent(self):
        from unittest.mock import patch
        cfg=copy.deepcopy(self.cfg);cfg['context_policy'].update(version=2,tool_output_token_limit=16384)
        value=copy.deepcopy(self.value);value['evidence']={'bounded':'x'*20000}
        folder=self.root/'must-not-launch'
        with patch('model_runner.configuration',return_value=cfg):
            runner=ModelRunner(cfg)
            with self.assertRaisesRegex(ValueError,'exceeds_explicit_history_budget'):
                runner.run(value,folder,lambda value:None)
        self.assertFalse(folder.exists())

    def test_wrong_input_kind_is_rejected_before_declared_receipt(self):
        with self.assertRaisesRegex(ValueError,'input_kind_changed'):
            context.declared(self.cfg,{**self.value,'input_kind':'text'},[],self.root,self.expected)
        self.assertFalse((self.root/'context-declared.json').exists())

    def test_missing_override_is_not_recorded_as_controlled_launch(self):
        argv=list(context.sdk_options(self.cfg)['launch_args_override']);argv.remove('project_doc_max_bytes=0')
        with self.assertRaisesRegex(ValueError,'launch_policy_changed'):
            context.declared(self.cfg,self.value,argv,self.root,self.expected)

    def test_effective_receipt_only_contains_safe_reported_fields(self):
        receipt=context.sdk_effective(self.cfg,self.value,Start(self.report),self.root,self.expected)
        self.assertTrue(receipt['checks_passed']);self.assertEqual(receipt['thread_id'],'thread-fixture')
        self.assertNotIn('secret',json.dumps(receipt));self.assertNotIn('private',json.dumps(receipt))

    def test_absent_instruction_sources_is_unknown_and_blocks_before_turn(self):
        report=copy.deepcopy(self.report);report.pop('instructionSources')
        with self.assertRaisesRegex(ValueError,'mismatch_before_turn'):
            context.sdk_effective(self.cfg,self.value,Start(report),self.root,self.expected)
        receipt=read_json(self.root/'context-effective.json')
        self.assertFalse(receipt['checks_passed']);self.assertNotIn('instructionSources',receipt['fields'])

    def test_unexpected_permissions_model_effort_or_instruction_sources_block(self):
        for key,value in [('model','other'),('approvalPolicy','on-request'),('reasoningEffort','high'),
            ('sandbox',{'type':'dangerFullAccess'}),('sandbox',{'type':'readOnly','networkAccess':True}),
            ('instructionSources',['unexpected.md'])]:
            report=copy.deepcopy(self.report);report[key]=value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'mismatch_before_turn'):
                context.sdk_effective(self.cfg,self.value,Start(report),self.root,self.expected)

    def test_completed_context_rechecks_exact_bundle(self):
        saved=self.bundle();result={'metadata':{'context_digest':digest(saved),'thread_id':'thread-fixture'}}
        self.assertEqual(context.verify(self.cfg,self.value,self.root,self.expected,result),saved)
        changed=copy.deepcopy(saved['effective']);changed['fields']['reasoningEffort']='high'
        atomic_json(self.root/'context-effective.json',seal(changed))
        with self.assertRaisesRegex(ValueError,'completion_changed'):
            context.verify(self.cfg,self.value,self.root,self.expected,result)

    def test_foreign_context_binding_is_not_reconciled(self):
        self.bundle()
        with self.assertRaisesRegex(ValueError,'receipt_binding_changed'):context.bundle(self.root,{'foreign':True})

    def test_cli_effective_settings_remain_unknown_and_cannot_be_invented(self):
        self.cfg['backend']='cli';self.cfg['context_policy'].update(input_kind='text',user_config='ignore');self.value['input_kind']='text'
        expected=binding(self.cfg,self.value)
        argv=[self.cfg['cli_path'],'exec','--ignore-user-config',*context.overrides(self.cfg)]
        context.declared(self.cfg,self.value,argv,self.root,expected);context.cli_effective(self.root,expected,123)
        saved=context.bundle(self.root,expected);result={'metadata':{'context_digest':digest(saved)}}
        self.assertIsNone(context.verify(self.cfg,self.value,self.root,expected,result)['effective']['fields'])
        changed=saved['effective'];changed['fields']={'approvalPolicy':'never'}
        atomic_json(self.root/'context-effective.json',seal(changed))
        result['metadata']['context_digest']=digest(context.bundle(self.root,expected))
        with self.assertRaisesRegex(ValueError,'cannot_be_invented'):context.verify(self.cfg,self.value,self.root,expected,result)

    @unittest.skipUnless(os.name=='nt','v4 owned transport requires Windows')
    def test_v4_completion_replays_without_model_and_context_tampering_blocks_validator(self):
        folder=self.root/'call';count=[];runner=ModelRunner(self.cfg)
        def launch(path,envelope):
            count.append(True)
            context.declared(self.cfg,self.value,context.sdk_options(self.cfg)['launch_args_override'],path,envelope['binding'])
            context.sdk_effective(self.cfg,self.value,Start(self.report),path,envelope['binding'])
            save(path,envelope['binding'],'completed',{'ok':True},{'thread_id':'thread-fixture'})
        first=runner.run(self.value,folder,lambda value:None,launcher=launch)
        def never(*args):self.fail('Repeated completed model invocation')
        self.assertEqual(runner.run(self.value,folder,lambda value:None,launcher=never),first)
        self.assertEqual(count,[True])
        path=folder/'context-effective.json';changed=read_json(path);changed['fields']['reasoningEffort']='high'
        atomic_json(path,seal(changed));entered=[]
        with self.assertRaisesRegex(ValueError,'completion_changed'):
            runner.run(self.value,folder,lambda value:entered.append(True),launcher=never)
        self.assertEqual(entered,[])

    @unittest.skipUnless(os.name=='nt','v4 owned transport requires Windows')
    def test_unsupported_sdk_ignore_never_creates_a_new_intent_but_preserves_unknown(self):
        self.cfg['context_policy']['user_config']='ignore';runner=ModelRunner(self.cfg);folder=self.root/'call'
        with self.assertRaisesRegex(ValueError,'sdk_ignore_user_config_not_supported'):
            runner.run(self.value,folder,lambda value:None)
        self.assertFalse((folder/'intent.json').exists())
        expected=binding(self.cfg,self.value)
        atomic_json(folder/'intent.json',seal({'schema':'sdm-model-runner-intent/v1','binding':expected}))
        with self.assertRaisesRegex(ValueError,'unknown_requires_reconciliation'):
            runner.run(self.value,folder,lambda value:None,launcher=lambda *a:self.fail('Unknown request repeated'))

    @unittest.skipUnless(os.name=='nt','v4 owned transport requires Windows')
    def test_unsupported_worker_policy_rejected_by_existing_pre_action_settings(self):
        from research_model_runner import runner_settings
        self.cfg['context_policy']['user_config']='ignore'
        cfg={'model_runner':self.cfg,'cli_path':self.cfg['cli_path'],'codex_home':self.cfg['codex_home']}
        with self.assertRaisesRegex(ValueError,'sdk_ignore_user_config_not_supported'):runner_settings(cfg)

    @unittest.skipUnless(os.name=='nt','v4 owned transport requires Windows')
    def test_v4_wrong_input_rejected_before_call_intent(self):
        with self.assertRaisesRegex(ValueError,'input_kind_changed'):
            ModelRunner(self.cfg).run({**self.value,'input_kind':'text'},self.root/'call',lambda value:None)
        self.assertFalse((self.root/'call/intent.json').exists())


if __name__=='__main__':unittest.main()
