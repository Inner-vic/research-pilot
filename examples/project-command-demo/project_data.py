"""Protected project data, optimization primitive and evaluation arithmetic."""
import os
os.environ.setdefault('OMP_NUM_THREADS','1');os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
import hashlib,json
from pathlib import Path
import numpy as np
from model_impl import features

LABELS=['inner','middle','outer']
def load():
    data=json.loads(Path('data.json').read_bytes());split=json.loads(Path('split-input.json').read_bytes())
    x=np.asarray(data['x'],dtype=np.float64);y=np.asarray(data['y'],dtype=np.int64)
    train=split['train_ids'];validation=split['validation_ids']
    assert not set(train)&set(validation) and len(train)+len(validation)==len(y)
    return features(x),y,train,validation
def initial(d,seed):return np.random.default_rng(seed).normal(0,.01,(d,3))
def step(w,x,y,lr):
    logits=x@w;logits-=logits.max(axis=1,keepdims=True)
    p=np.exp(logits);p/=p.sum(axis=1,keepdims=True)
    loss=-np.log(np.maximum(p[np.arange(len(y)),y],1e-12)).mean()
    delta=p.copy();delta[np.arange(len(y)),y]-=1
    gradient=x.T@delta/len(y)
    assert np.isfinite(loss) and np.isfinite(gradient).all()
    return w-lr*gradient,float(loss)
def metrics(y,p):
    cm=[[int(((y==i)&(p==j)).sum()) for j in range(3)] for i in range(3)]
    f1=[];iou=[]
    for i in range(3):
        tp=cm[i][i];fp=sum(row[i] for row in cm)-tp;fn=sum(cm[i])-tp
        f1.append(2*tp/max(1,2*tp+fp+fn));iou.append(tp/max(1,tp+fp+fn))
    return {'accuracy':float((y==p).mean()),'macro_f1':sum(f1)/3,'mean_iou':sum(iou)/3,'confusion_matrix':cm}
def digest(value):return hashlib.sha256(value.tobytes()).hexdigest()
