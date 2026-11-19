"""Protected project training entry. Independent of the ResearchPilot engine."""
import json
from pathlib import Path
from project_data import np,load,initial,step,metrics,digest

config=json.loads(Path('experiment.json').read_bytes())['training'];x,y,train,validation=load()
w=initial(x.shape[1],config['seed']);preflight=json.loads(Path('preflight.json').read_bytes())
assert digest(w)==preflight['initial_weights_sha256']
rng=np.random.default_rng(config['seed']);history=[]
for epoch in range(1,config['epochs']+1):
    order=rng.permutation(train);losses=[]
    for start in range(0,len(order),config['batch_size']):
        ids=order[start:start+config['batch_size']];w,loss=step(w,x[ids],y[ids],config['lr']);losses.append(loss)
    history.append({'epoch':epoch,'train_loss':sum(losses)/len(losses),'validation':metrics(y[validation],(x[validation]@w).argmax(axis=1))})
np.savez('weights.npz',weights=w)
Path('training-history.json').write_text(json.dumps(history,sort_keys=True))
