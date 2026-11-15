import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from evaluation_metrics import classification_metrics,reported_scalar_metrics

class EvaluationMetricsTests(unittest.TestCase):
 def result(self,matrix):
  return {'protocol':'fixed','source_sha256':'data','split_sha256':'split','selection':'fixed-checkpoint','validation':{'confusion_matrix':matrix,'macro_f1':.5}}
 def test_asymmetric_counts_and_real_base(self):
  old=self.result([[8,2],[4,6]]);new=self.result([[9,1],[3,7]])
  report=classification_metrics(new,old);row=report['classes'][0]
  self.assertAlmostEqual(row['precision'],9/12);self.assertAlmostEqual(row['recall'],9/10)
  self.assertAlmostEqual(row['f1'],18/22);self.assertAlmostEqual(row['accuracy_ovr'],16/20)
  self.assertTrue(report['baseline_comparable']);self.assertAlmostEqual(row['delta']['recall'],.1)
  new['split_sha256']='different';self.assertFalse(classification_metrics(new,old)['baseline_comparable'])
 def test_undefined_support_and_invalid_matrix(self):
  rows=classification_metrics(self.result([[4,0],[0,0]]))['classes']
  self.assertIsNone(rows[1]['precision']);self.assertIsNone(rows[1]['recall']);self.assertIsNone(rows[1]['f1'])
  for matrix in ([[1,2]],[[1,-1],[2,3]],[[True,0],[0,2]],[[0,0],[0,0]]):self.assertIsNone(classification_metrics(self.result(matrix)))
 def test_multiclass_formatting_does_not_invent_iou_or_names(self):
  result=self.result([[3,1,0],[0,2,1],[1,0,2]])
  self.assertEqual(len(classification_metrics(result)['classes']),3)
  self.assertNotIn('iou',{m['key'] for m in reported_scalar_metrics(result)})
  result['validation']['mean_iou']=.62
  self.assertEqual(next(m['value'] for m in reported_scalar_metrics(result) if m['key']=='mean_iou'),.62)

if __name__=='__main__':unittest.main()
