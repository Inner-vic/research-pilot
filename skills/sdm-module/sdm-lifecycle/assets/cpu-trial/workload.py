"""Finite stdlib CPU regression demo. Synthetic data; no platform or GPU access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import time


def save(name, value):
    tmp = Path(name + '.tmp'); tmp.write_text(json.dumps(value, allow_nan=False), encoding='utf-8'); tmp.replace(name)


p = argparse.ArgumentParser()
p.add_argument('--learning-rate', type=float, required=True)
p.add_argument('--mode', choices=['normal', 'collapse'], default='normal')
a = p.parse_args()
if not .00001 <= a.learning_rate <= .1: raise ValueError('example_learning_rate_outside_domain')
rng = random.Random(20261020)
data = [(x, 2*x+.3) for x in [rng.uniform(-1, 1) for _ in range(192)]]
train, valid = data[:128], data[128:]
data_digest = hashlib.sha256(json.dumps(data).encode()).hexdigest()
w = b = 0.; updates = 0; history = []
for epoch in range(1, 61):
    for offset in range(0, 128, 16):
        batch = train[offset:offset+16]
        dw = sum(2*(w*x+b-y)*x for x,y in batch)/len(batch)
        db = sum(2*(w*x+b-y) for x,y in batch)/len(batch)
        w -= a.learning_rate*dw; b -= a.learning_rate*db; updates += 1
    mse = sum((w*x+b-y)**2 for x,y in valid)/len(valid); history.append(mse)
    save('checkpoint.json', {'model':{'weight':w,'bias':b}, 'optimizer':{'learning_rate':a.learning_rate,'updates':updates},
                            'epoch':epoch,'seed':20261020,'history':history})
    save('predictions.json', {'protocol':'cpu-regression-v1','sample_ids':[0,1,2,3],
                             'targets':[y for x,y in valid[:4]],'predictions':[w*x+b for x,y in valid[:4]]})
    save('progress.json', {'epoch':epoch,'optimizer_updates':updates,'validation_mse':mse})
    save('result.json', {'parameters':{'learning_rate':a.learning_rate}, 'score':1/(1+mse),
        'validation_mse':mse,'optimizer_updates':updates,'epochs':epoch,'data_digest':data_digest,
        'execution_device':'cpu','checkpoint_verified':json.loads(Path('checkpoint.json').read_bytes())['model']=={'weight':w,'bias':b},
        'case_kind':'synthetic-local-cpu','controlled_fault':a.mode})
    row = {'binding':os.environ['SDM_EXECUTION_BINDING'],'sequence':epoch,'optimizer_updates':updates,'update_applied':True,
        'loss':mse,'protocol':'cpu-regression-v1','metric_name':'validation_mse','metric_value':1000 if a.mode=='collapse' and epoch>=4 else mse,
        'valid_samples':64,'fault_kind':'injected-metric-collapse' if a.mode=='collapse' else 'none'}
    for field, name in [('checkpoint_file','checkpoint.json'),('predictions_file','predictions.json')]:
        row[field+'_sha256'] = hashlib.sha256(Path(name).read_bytes()).hexdigest()
    save('health-sample.json', row)
    time.sleep(.08)  # expose observable finite epochs; not a throughput benchmark
save('history.json', history)
