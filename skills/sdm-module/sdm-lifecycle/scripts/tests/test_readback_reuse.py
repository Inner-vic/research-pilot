"""Same-task artifact recollection must not count as another experiment."""
import copy,hashlib,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from review_storage import read_json,atomic_json
from component_pipeline import ComponentPipeline
from component_research import round_evidence
from test_component_research_continuation import result,ref

class ReadbackTests(unittest.TestCase):
 def test_same_uid_equal_artifacts_reuse_row_but_changed_artifacts_block(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);row=result(root,1,[0,1,1,0]);ready=read_json(row['result']['path']);ready['original_plan_sha256']='a'*64
   atomic_json(row['result']['path'],ready);row['result']=ref(Path(row['result']['path']))
   recollected=root/'same-task.json';atomic_json(recollected,ready);new=ref(recollected)
   actual=round_evidence(new);owner=ComponentPipeline.__new__(ComponentPipeline);owner.spec={'comparison_identity':actual['fair_comparison']};owner.inbox=SimpleNamespace(cfg={})
   class StopBeforeAnyPlanning(Exception):pass
   owner.base_project=lambda:(_ for _ in ()).throw(StopBeforeAnyPlanning())
   state={'rounds':[copy.deepcopy(row)]};current={'result':new}
   with self.assertRaises(StopBeforeAnyPlanning):owner.replan(state,current,root)
   self.assertEqual(len(state['rounds']),1);self.assertEqual(state['rounds'][0]['result'],new)
   changed=result(root,2,[0,0,1,1]);other=read_json(changed['result']['path']);other.update(uid=ready['uid'],original_plan_sha256='a'*64)
   atomic_json(changed['result']['path'],other);changed['result']=ref(Path(changed['result']['path']))
   with self.assertRaisesRegex(ValueError,'same_task_readback_artifacts_changed'):owner.replan({'rounds':[row]},{'result':changed['result']},root)

if __name__=='__main__':unittest.main()
