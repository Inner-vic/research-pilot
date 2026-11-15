"""Public binary fixed-length TS baseline template. Render with prepare_public_ts; remote execution only."""
import os
for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS'):
    os.environ[key] = '1'
import base64
import copy
import gzip
import hashlib
import io
import json
import random
import time
from pathlib import Path
if __name__ == '__main__' and 'LPAI_INPUT_DATA_1' not in os.environ:
    raise RuntimeError('This baseline runs only in an authorized LPAI task, never locally')
import numpy as np
import torch
from torch import nn

CONFIG = {'round': 1, 'model': 'fcn-bn-gap', 'channels': 16, 'epochs': 20,
          'lr': .001, 'batch_size': 64, 'seed': 42, 'threads': 1}
SOURCE = ''
DATA_SHA = ''
PROTOCOL = ''
REMOTE_ROOT = ''
INPUT_FILE = ''
SAMPLES = 0
LENGTH = 0
CLASS_LABELS = []
TRAIN_SAMPLES = 0
VAL_SAMPLES = 0
ROOT = Path(REMOTE_ROOT)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def emit(kind, value):
    print('SDM_JSON ' + json.dumps({'kind': kind, **value}, sort_keys=True, allow_nan=False), flush=True)


def metrics(labels, predictions):
    cm = [[int(((labels == i) & (predictions == j)).sum()) for j in (0, 1)] for i in (0, 1)]
    f1 = [2 * cm[i][i] / max(1, 2 * cm[i][i] + cm[i][1-i] + cm[1-i][i]) for i in (0, 1)]
    recall = [cm[i][i] / max(1, sum(cm[i])) for i in (0, 1)]
    return {'macro_f1': sum(f1)/2, 'accuracy': float((labels == predictions).mean()),
            'balanced_accuracy': sum(recall)/2, 'confusion_matrix': cm, 'class_recall': recall}


def publish(path):
    raw = path.read_bytes()
    packed = base64.b64encode(gzip.compress(raw, mtime=0)).decode('ascii')
    chunks = [packed[i:i+1400] for i in range(0, len(packed), 1400)]
    emit('artifact', {'name': path.name, 'sha256': sha(raw), 'bytes': len(raw), 'chunks': len(chunks), 'encoding': 'base64+gzip'})
    for i, value in enumerate(chunks):
        emit('artifact-chunk', {'name': path.name, 'index': i, 'value': value})




def main():
    started = time.monotonic()
    code_sha = sha(Path(__file__).read_bytes())
    out = ROOT / ('r%02d-' % CONFIG['round'] + code_sha[:12])
    out.mkdir(parents=True, exist_ok=False)  # no accidental overwrite/retry
    torch.set_num_threads(CONFIG['threads'])
    torch.set_num_interop_threads(1)
    torch.manual_seed(CONFIG['seed'])
    random.seed(CONFIG['seed'])
    np.random.seed(CONFIG['seed'])
    torch.use_deterministic_algorithms(True)
    raw=(Path(os.environ['LPAI_INPUT_DATA_1'])/INPUT_FILE).read_bytes()
    if sha(raw)!=DATA_SHA: raise ValueError('mounted public input hash mismatch; no fit')
    records=[]; in_data=False
    for line in raw.decode('utf-8-sig').splitlines():
        if not in_data:
            if line.strip().lower() == '@data': in_data=True
            continue
        if not line.strip() or line.startswith('#'): continue
        values,label=line.rsplit(':',1)
        records.append([float(label),*[float(v) for v in values.split(',')]])
    all_data=np.asarray(records,dtype=np.float64)
    assert all_data.shape == (SAMPLES,LENGTH+1) and np.isfinite(all_data).all()
    labels = (all_data[:, 0] == CLASS_LABELS[1]).astype('int64')
    assert set(all_data[:, 0]) == set(CLASS_LABELS)
    train_ids, val_ids = [], []
    for label in (0, 1):
        ids = np.flatnonzero(labels == label).tolist()
        ids.sort(key=lambda i: sha((PROTOCOL+':'+str(i)).encode()))
        n = int(np.ceil(len(ids)*.25))
        val_ids.extend(ids[:n]); train_ids.extend(ids[n:])
    train_ids.sort(); val_ids.sort()
    assert len(train_ids) == TRAIN_SAMPLES and len(val_ids) == VAL_SAMPLES and not set(train_ids)&set(val_ids)
    signals = all_data[:, 1:].astype('<f4')
    train_hashes = {sha(signals[i].tobytes()) for i in train_ids}
    assert not train_hashes & {sha(signals[i].tobytes()) for i in val_ids}
    x = torch.from_numpy(signals[train_ids, None, :].copy())
    vx = torch.from_numpy(signals[val_ids, None, :].copy())
    y = torch.from_numpy(labels[train_ids].copy())
    vy = torch.from_numpy(labels[val_ids].copy())
    split = {'protocol': PROTOCOL, 'source_sha256': DATA_SHA,
             'train_ids': train_ids, 'validation_ids': val_ids, 'test_used': False}
    (out/'split.json').write_text(json.dumps(split, sort_keys=True), encoding='utf-8')
    emit('preflight', {'code_sha256': code_sha, 'source_sha256': DATA_SHA,
         'split_sha256': sha((out/'split.json').read_bytes()), 'torch': torch.__version__,
         'numpy': np.__version__, 'device': 'cpu', 'config': CONFIG, 'output': str(out),
         'train_samples': len(x), 'validation_samples': len(vx), 'test_used': False})
    c = CONFIG['channels']
    model = nn.Sequential(nn.Conv1d(1,c,7,padding=3),nn.BatchNorm1d(c),nn.ReLU(),
                          nn.Conv1d(c,2*c,5,padding=2),nn.BatchNorm1d(2*c),nn.ReLU(),
                          nn.Conv1d(2*c,2*c,3,padding=1),nn.BatchNorm1d(2*c),nn.ReLU(),
                          nn.AdaptiveAvgPool1d(1),nn.Flatten(),nn.Linear(2*c,2))
    optimizer = torch.optim.Adam(model.parameters(), lr=CONFIG['lr'])
    criterion = nn.CrossEntropyLoss()
    def evaluate(tx, ty):
        model.eval(); logits=[]
        with torch.no_grad():
            for batch in tx.split(CONFIG['batch_size']):
                logits.append(model(batch))
        logits=torch.cat(logits)
        return {**metrics(ty.numpy(), logits.argmax(1).numpy()),
                'loss': float(criterion(logits,ty))}, logits.softmax(1).numpy()
    history=[]; best_key=None; best_weights=None
    for epoch in range(1, CONFIG['epochs']+1):
        model.train(); total=0
        for batch in torch.randperm(len(x)).split(CONFIG['batch_size']):
            optimizer.zero_grad(set_to_none=True)
            loss=criterion(model(x[batch]),y[batch]); loss.backward(); optimizer.step()
            total += float(loss.detach())*len(batch)
        val,_=evaluate(vx,vy)
        record={'epoch':epoch,'train_loss':total/len(x),'validation':val}
        history.append(record); emit('epoch',record)
        key=(val['macro_f1'],-val['loss'])
        if best_key is None or key>best_key:
            best_key=key; best_weights=copy.deepcopy(model.state_dict()); best_epoch=epoch
    model.load_state_dict(best_weights)
    final_val, probabilities=evaluate(vx,vy)
    train_metrics,_=evaluate(x,y)
    predictions={'row_ids':val_ids,'y_true':vy.tolist(),'probability_positive':probabilities[:,1].tolist(),
                 'y_pred':probabilities.argmax(1).tolist()}
    result={'schema':'sdm-component-result/v1','config':CONFIG,'protocol':PROTOCOL,
            'code_sha256':code_sha,'source_sha256':DATA_SHA,'split_sha256':sha((out/'split.json').read_bytes()),
            'selection':'max validation macro_f1 then min validation loss; earliest tie',
            'selected_epoch':best_epoch,'history':history,'train':train_metrics,'validation':final_val,
            'elapsed_seconds':time.monotonic()-started,'test_used':False,
            'limitations':['single seed','validation-guided selection, not untouched test generalization',
                           'no device or chronological grouping available']}
    torch.save({'state_dict':best_weights,'config':CONFIG,'code_sha256':code_sha,
                'split_sha256':result['split_sha256']},out/'checkpoint.pt')
    (out/'predictions.json').write_text(json.dumps(predictions,sort_keys=True),encoding='utf-8')
    (out/'result.json').write_text(json.dumps(result,sort_keys=True,allow_nan=False),encoding='utf-8')
    (out/'train.py').write_bytes(Path(__file__).read_bytes())
    for name in ('split.json','predictions.json','result.json','checkpoint.pt'):
        publish(out/name)
    emit('completed',{'output':str(out),'validation':final_val,'selected_epoch':best_epoch,
                      'result_sha256':sha((out/'result.json').read_bytes())})


if __name__ == '__main__':
    main()
