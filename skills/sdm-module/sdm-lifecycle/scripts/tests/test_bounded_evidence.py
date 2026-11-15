import hashlib,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bounded_evidence import supplement,select,TOTAL_BYTES
from task_pipeline import digest

class BoundedEvidenceTests(unittest.TestCase):
 def test_exact_fields_and_huge_line_are_explicit(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'history.jsonl'
   rows=[{'uid':str(i),'result':{'config':{'epochs':30},'validation':{'macro_f1':.8},'history':['huge'*40000]}} for i in range(34)]
   raw='\n'.join(json.dumps(row) for row in rows).encode();p.write_bytes(raw)
   context={'sources':[{'id':'history','path':str(p),'sha256':hashlib.sha256(raw).hexdigest()}]}
   packet={'request':'fixture'};query={'request_digest':digest(packet),'queries':[{'source_id':'history',
    'start_line':1,'line_count':200,'question':'actual recipes','json_pointers':['/uid','/result/config','/result/validation']}]}
   value=supplement(packet,query,context);result=value['results'][0]
   self.assertEqual(result['delivered_line_count'],34);self.assertFalse(result['truncated'])
   self.assertLess(len(json.dumps(value).encode()),TOTAL_BYTES)
   actual=[json.loads(line)['fields'] for line in result['text'].splitlines()]
   self.assertEqual(actual,[{pointer:select(row,pointer) for pointer in query['queries'][0]['json_pointers']} for row in rows])
   query['queries'][0]['json_pointers']=['/absent']
   absent=supplement(packet,query,context)['results'][0]
   self.assertEqual(json.loads(absent['text'].splitlines()[0])['missing_pointers'],['/absent'])
   query['queries'][0]['json_pointers']=[]
   blocked=supplement(packet,query,context)['results'][0]
   self.assertEqual(blocked['delivered_line_count'],0);self.assertTrue(blocked['truncated']);self.assertEqual(blocked['next_start_line'],1)
   self.assertIn('specific-json-pointers',blocked['reason'])
   p.write_bytes(b'changed')
   with self.assertRaisesRegex(ValueError,'review_source_changed'):supplement(packet,query,context)
 def test_pointers_preserve_types_and_reject_missing(self):
  value={'a/b':[{'x~y':False},{'x~y':None}]}
  self.assertEqual(select(value,'/a~1b/*/x~0y'),[False,None])
  with self.assertRaisesRegex(ValueError,'pointer_not_found'):select(value,'/missing')

 def test_partial_query_repetition_requires_cursor_or_distinct_fields(self):
  from unittest.mock import patch
  from proposal_evidence import auto_supply
  from review_storage import atomic_json
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);source=root/'history.jsonl';source.write_text('one\ntwo\nthree\nfour')
   query={'source_id':'history','start_line':1,'line_count':4,'question':'same need','json_pointers':[]}
   packet={'shared_context':{'value':{'sources':[{'id':'history','path':str(source),'sha256':'fixed'}]}}}
   for i in (1,2):
    saved=root/str(i);saved.mkdir();atomic_json(saved/'request.json',packet)
    atomic_json(saved/'accepted.json',{'response':{'verdict':'needs-evidence','evidence_queries':[{**query,'question':str(i)}]}})
   atomic_json(root/'1/query-old.json',{'queries':[query]})
   supplied=[{'value':{'results':[{'source_id':'history','start_line':1,'json_pointers':[],'delivered_line_count':2}]}}]
   rows=[{'directory':'1'},{'directory':'2'}]
   with patch('proposal_review.supplied',return_value=supplied),patch('proposal_review.supply') as send:
    self.assertEqual(auto_supply(root,rows)['status'],'proposal-review-stalled');send.assert_not_called()
    accepted={'response':{'verdict':'needs-evidence','evidence_queries':[{**query,'start_line':3,'line_count':2}]}}
    atomic_json(root/'2/accepted.json',accepted)
    send.return_value={'status':'review-evidence-supplied'}
    self.assertEqual(auto_supply(root,rows)['status'],'review-evidence-supplied');send.assert_called_once()
    send.reset_mock()
    atomic_json(root/'2/accepted.json',{'response':{'verdict':'needs-evidence','evidence_queries':[{**query,'start_line':2,'line_count':3}]}})
    self.assertEqual(auto_supply(root,rows)['status'],'review-evidence-supplied')
    actual=send.call_args.args[2]['queries']
    self.assertEqual([(q['start_line'],q['line_count']) for q in actual],[(3,2)])


if __name__=='__main__':unittest.main()
