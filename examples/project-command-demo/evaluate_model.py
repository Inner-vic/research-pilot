"""Protected evaluator; reports the final model on the fixed validation rows."""
import json
from pathlib import Path
from project_data import np,load,metrics,LABELS

x,y,train,validation=load();w=np.load('weights.npz')['weights'];p=(x[validation]@w).argmax(axis=1)
history=json.loads(Path('training-history.json').read_bytes())
result=metrics(y[validation],p)
assert result==history[-1]['validation']
Path('evaluation.json').write_text(json.dumps({'metrics':result,'history':history,
 'selected_epoch':history[-1]['epoch'],'checkpoint_file':'weights.npz'},sort_keys=True))
Path('predictions.json').write_text(json.dumps({'row_ids':validation,
 'y_true':[LABELS[i] for i in y[validation]],'y_pred':[LABELS[i] for i in p]},sort_keys=True))
Path('split.json').write_bytes(Path('split-input.json').read_bytes())
