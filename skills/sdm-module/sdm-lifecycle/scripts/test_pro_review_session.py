import json
import tempfile
import unittest
from pathlib import Path
from pro_review_session import bind,check,record,atomic
from model_review_bridge import prepare,emit
from task_pipeline import digest

URL='https://chatgpt.com/c/11111111-1111-1111-1111-111111111111'
OTHER='https://chatgpt.com/c/22222222-2222-2222-2222-222222222222'
class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'session'
        bind(self.root,'project','workflow',URL)
    def tearDown(self):self.temp.cleanup()
    def bundle(self,name,context=None):
        path=Path(self.temp.name)/name
        packet={'schema':'sdm-loop-analysis-request/v1','objective':'diagnose '+name,'remaining_rounds':0,'history':[], 'evidence':{'count':8}}
        inp=path/'input.json';atomic(inp,packet);prepare(inp,[],path)
        response={'request_digest':digest(packet),'facts':[{'evidence_id':'count','value':8}], 'hypotheses':[{'statement':'candidate','evidence_ids':['count'],'limitations':'tiny sample'}], 'previous_experiment_assessment':'unknown','reason':'no budget','action':'stop-budget'}
        if context:response.update(context)
        emit(path,'analysis-response.json',response)
        emit(path,'chat-receipt.json',{'url':URL,'model':'Pro','verified_visible':True})
        return path
    def test_binding_and_stale_revision(self):
        with self.assertRaisesRegex(ValueError,'binding_conflict'):bind(self.root,'project','workflow',OTHER)
        with self.assertRaisesRegex(ValueError,'wrong_conversation'):check(self.root,OTHER)
        with self.assertRaisesRegex(ValueError,'stale_session_revision'):check(self.root,URL,1)
    def test_bootstrap_and_context_echo(self):
        first=self.bundle('one');record(self.root,first,URL,0,bootstrap=True)
        state=check(self.root,URL,1)
        with self.assertRaisesRegex(ValueError,'bootstrap_only'):record(self.root,first,URL,1,bootstrap=True)
        self.assertEqual(record(self.root,first,URL,1)['revision'],1)
        with self.assertRaisesRegex(ValueError,'context_mismatch'):record(self.root,self.bundle('bad'),URL,1)
        valid=self.bundle('two',{'session_context_revision':1,'session_context_digest':state['context_digest']})
        record(self.root,valid,URL,1);self.assertEqual(check(self.root,URL,2)['revision'],2)
    def test_context_tamper_and_invented_fact(self):
        first=self.bundle('one');raw=json.loads((first/'analysis-response.json').read_bytes());raw['facts'][0]['value']=9;atomic(first/'analysis-response.json',raw)
        with self.assertRaisesRegex(ValueError,'invented_analysis_evidence'):record(self.root,first,URL,0,bootstrap=True)
        record(self.root,self.bundle('good'),URL,0,bootstrap=True)
        state=check(self.root,URL);p=self.root/state['context_ref'];ctx=json.loads(p.read_bytes());ctx['objective']='tampered';atomic(p,ctx)
        with self.assertRaisesRegex(ValueError,'context_integrity'):check(self.root,URL)
if __name__=='__main__':unittest.main()
