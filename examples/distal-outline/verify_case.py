"""Read-only case arithmetic/provenance checks. No ML, network or process launch."""
import hashlib,json,re
from pathlib import Path


def verify(folder=None):
 if not __debug__:raise ValueError('run_case_verification_without_optimization')
 root=Path(folder or Path(__file__).resolve().parent);manifest=json.loads((root/'MANIFEST.json').read_bytes())
 for name,expected in manifest['files'].items():
  path=Path(name)
  if path.is_absolute() or path.drive or ':' in name or '..' in path.parts or not (root/path).resolve().is_relative_to(root.resolve()):raise ValueError('relative_case_file_required')
  raw=(root/path).read_bytes()
  if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('case_file_changed:'+name)
  text=raw.decode('utf-8')
  if re.search(r'[A-Z]:[\\/]|/(?:home|Users)/|(?:lpak_|ou_|oc_)[A-Za-z0-9_-]{12,}',text):raise ValueError('private_identity_in_case:'+name)
 case=json.loads((root/'case.json').read_bytes());brief=json.loads((root/'research-brief.json').read_bytes());provenance=json.loads((root/'provenance.json').read_bytes())
 assert case['schema']=='sdm-public-case/v1' and brief['dataset']==case['dataset'] and brief['comparison']==case['evaluation']
 assert case['dataset']['samples']==case['evaluation']['train_samples']+case['evaluation']['validation_samples']
 assert case['evaluation']['test_used'] is False and case['execution']['maximum_trial_count'] is None
 assert case['preparation_event']['valid_training_round'] is False and case['preparation_event']['score'] is None
 rows=case['rounds'];assert [r['round'] for r in rows]==list(range(1,len(rows)+1));best=None
 for r in rows:
  cm=r['validation']['confusion_matrix'];assert len(cm)==2 and all(len(row)==2 for row in cm)
  assert all(type(v) is int and v>=0 for row in cm for v in row)
  assert list(map(sum,cm))==case['evaluation']['validation_class_support']
  f1=sum(2*cm[i][i]/max(1,2*cm[i][i]+cm[i][1-i]+cm[1-i][i]) for i in range(2))/2
  accuracy=(cm[0][0]+cm[1][1])/sum(map(sum,cm))
  assert abs(f1-r['macro_f1'])<1e-12 and abs(f1-r['validation']['macro_f1'])<1e-12 and abs(accuracy-r['validation']['accuracy'])<1e-12
  if r['base_round'] is not None:
   assert 1<=r['base_round']<r['round'];assert abs(r['delta_from_base']-(f1-rows[r['base_round']-1]['macro_f1']))<1e-12
  else:assert r['round']==1 and r['delta_from_base'] is None
  if best is None or f1>best['macro_f1']:best=r
  assert r['incumbent_after']==best['round'] and 10<=r['training']['epochs']<=30 and 1<=r['selected_epoch']<=r['training']['epochs']
 assert best['round']==case['best_round'] and best['macro_f1']>=case['evaluation']['target']
 chain=[];current=best
 while current:
  chain.append(current['round']);current=rows[current['base_round']-1] if current['base_round'] else None
 assert list(reversed(chain))==case['mainline']
 assert [r['id'] for r in rows]==[p['id'] for p in provenance['result_evidence']]
 assert all(re.fullmatch('[0-9a-f]{64}',p[k]) for p in provenance['result_evidence'] for k in ('original_result_sha256','original_ready_sha256'))
 return {'校验':'通过','有效实验':len(rows),'初始macro-F1':rows[0]['macro_f1'],'最优macro-F1':best['macro_f1'],'实际主线':case['mainline'],'训练或平台调用':0}

if __name__=='__main__':print(json.dumps(verify(),ensure_ascii=False,indent=2))
