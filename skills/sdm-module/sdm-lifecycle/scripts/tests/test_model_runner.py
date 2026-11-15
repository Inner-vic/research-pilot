"""ModelRunner persistence and selection controls. No actual model/ML/platform."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_runner import ModelRunner, InvalidModelReply, binding, request
from model_runner_transport import save, cancelled
from research_model_receipts import seal
from review_storage import atomic_json, read_json


class ModelRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        cli = self.root/'codex.exe';cli.write_bytes(b'control fixture, never executed')
        self.config = {'version':1,'backend':'cli','cli_path':str(cli),
            'cli_sha256':hashlib.sha256(cli.read_bytes()).hexdigest(),'codex_home':str(self.root),
            'cwd':str(self.root),'timeout_seconds':30}
        self.value = {'schema':'sdm-model-request/v1','purpose':'result-analysis','model':'fixture-model',
            'contract_id':'sum/v1','prompt':'Compute 2+2','evidence':{'numbers':[2,2]},
            'output_schema':{'type':'object','properties':{'sum':{'type':'integer'}},
                             'required':['sum'],'additionalProperties':False}}
        self.runner = ModelRunner(self.config)
        self.folder = self.root/'call'
        self.calls = []

    def validator(self,value):
        if value != {'sum':4}:raise ValueError('sum evidence mismatch')

    def caller(self,folder,envelope):
        self.calls.append(True)
        save(folder,envelope['binding'],'completed',{'sum':4},{'backend':'fixture'})

    def never(self,*args):
        self.fail('Repeated a model invocation')

    def run_call(self,launcher=None,validator=None):
        return self.runner.run(self.value,self.folder,validator or self.validator,launcher=launcher or self.caller)

    def test_completed_reply_replayed_without_invocation(self):
        first = self.run_call()
        second = self.run_call(self.never)
        self.assertEqual(first,second)
        self.assertEqual(len(self.calls),1)

    def test_completed_receipt_survives_caller_failure(self):
        def crash(folder,envelope):
            self.caller(folder,envelope)
            raise OSError('after backend completion')
        with self.assertRaises(OSError):self.run_call(crash)
        self.assertEqual(self.run_call(self.never)['status'],'completed')
        self.assertEqual(len(self.calls),1)

    def test_unknown_intent_is_never_automatically_retried(self):
        def unknown(*args):raise OSError('transport outcome unknown')
        with self.assertRaises(OSError):self.run_call(unknown)
        with self.assertRaisesRegex(ValueError,'unknown_requires_reconciliation'):
            self.run_call(self.never)

    def test_exit_without_completion_is_unknown(self):
        with self.assertRaisesRegex(ValueError,'unknown_requires_reconciliation'):
            self.run_call(lambda *args:None)
        with self.assertRaisesRegex(ValueError,'unknown_requires_reconciliation'):
            self.run_call(self.never)

    def test_changed_request_does_not_reuse_or_invoke(self):
        self.run_call()
        self.value['evidence']['numbers']=[3,3]
        with self.assertRaisesRegex(ValueError,'immutable_model_runner_request_changed'):
            self.run_call(self.never)

    def test_backend_switch_cannot_replace_same_call(self):
        self.run_call()
        other=copy.deepcopy(self.config)
        other['timeout_seconds']=31
        with self.assertRaisesRegex(ValueError,'immutable_model_runner_request_changed'):
            ModelRunner(other).run(self.value,self.folder,self.validator,launcher=self.never)

    def test_tampered_completion_never_reaches_validator(self):
        self.run_call()
        path=self.folder/'backend-completed.json';value=read_json(path)
        value['response']['sum']=999;atomic_json(path,value)
        with self.assertRaisesRegex(ValueError,'model_receipt_integrity_failed'):
            self.run_call(self.never,self.never)

    def test_completion_requires_intent(self):
        self.run_call()
        (self.folder/'intent.json').unlink()
        with self.assertRaisesRegex(ValueError,'completion_without_intent'):
            self.run_call(self.never)

    def test_known_invalid_reply_preserved_without_repeat(self):
        def invalid(folder,envelope):save(folder,envelope['binding'],'completed',{'sum':5},{})
        with self.assertRaisesRegex(InvalidModelReply,'sum evidence mismatch') as error:self.run_call(invalid)
        self.assertEqual(error.exception.response,{'sum':5})
        self.assertEqual(error.exception.contract_id,'sum/v1')
        with self.assertRaisesRegex(ValueError,'sum evidence mismatch'):self.run_call(self.never)
        self.assertEqual(read_json(self.folder/'backend-completed.json')['response'],{'sum':5})

    def test_validator_cannot_mutate_retained_or_returned_raw_reply(self):
        def normalize(value):value['sum']=999
        result=self.run_call(validator=normalize)
        self.assertEqual(result['response'],{'sum':4})
        self.assertEqual(read_json(self.folder/'backend-completed.json')['response'],{'sum':4})

    def test_terminal_interrupted_does_not_produce_a_result_or_retry(self):
        def stop(folder,envelope):save(folder,envelope['binding'],'interrupted',None,{'backend':'sdk'})
        with self.assertRaisesRegex(ValueError,'terminal_without_result:interrupted'):self.run_call(stop)
        with self.assertRaisesRegex(ValueError,'terminal_without_result:interrupted'):self.run_call(self.never)

    def test_invalid_purpose_rejected_before_intent(self):
        self.value['purpose']='execute-platform'
        with self.assertRaisesRegex(ValueError,'typed_model_request_required'):self.run_call(self.never)
        self.assertFalse((self.folder/'intent.json').exists())

    def test_invalid_input_authority_rejected_before_intent(self):
        self.value['input_kind']='platform-authority'
        with self.assertRaisesRegex(ValueError,'typed_model_request_required'):self.run_call(self.never)
        self.assertFalse((self.folder/'intent.json').exists())

    def test_changed_cli_pin_rejected_before_call(self):
        Path(self.config['cli_path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'model_runner_cli_changed'):self.run_call(self.never)

    def test_cancel_binds_exact_request(self):
        self.run_call()
        self.runner.cancel(self.folder)
        expected=binding(self.config,self.value)
        self.assertTrue(cancelled(self.folder,expected))
        with self.assertRaisesRegex(ValueError,'cancel_binding_changed'):
            cancelled(self.folder,{**expected,'request_digest':'different'})

    def test_sdk_dependency_missing_never_falls_back(self):
        config={**self.config,'backend':'sdk','sdk_version':'0.163.0','sdk_root':str(self.root/'missing')}
        with self.assertRaisesRegex(ValueError,'pinned_model_runner_sdk_required'):ModelRunner(config)

    def test_shared_request_and_schema_independent_of_backend(self):
        request(self.value)
        left=binding(self.config,self.value)
        right=binding({**self.config,'backend':'sdk'},self.value)
        for key in ('request_digest','schema_digest','prompt_digest'):
            self.assertEqual(left[key],right[key])
        self.assertNotEqual(left['config_digest'],right['config_digest'])


if __name__=='__main__':unittest.main()
