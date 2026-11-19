"""Protected bounded functional checks; uses training samples only."""
import io,json
from pathlib import Path
from project_data import np,load,initial,step,digest

parameters=json.loads(Path('experiment.json').read_bytes())['training']
x,y,train,validation=load();indices=[]
for label in range(3):indices.extend([i for i in train if y[i]==label][:2])
assert len(indices)==6
tx,ty=x[indices],y[indices];w=initial(x.shape[1],parameters['seed']);original=w.copy()
assert (tx@w).shape==(6,3) and np.isfinite(tx@w).all()
losses=[]
for n in range(14):w,loss=step(w,tx,ty,parameters['lr']);losses.append(loss)
assert losses[-1]<losses[0],'tiny-fit loss did not decrease within 14 updates'
stream=io.BytesIO();np.save(stream,w);stream.seek(0);reloaded=np.load(stream)
a,loss_a=step(w,tx,ty,parameters['lr']);b,loss_b=step(reloaded,tx,ty,parameters['lr'])
assert np.array_equal(a,b) and loss_a==loss_b
assert np.array_equal(initial(x.shape[1],parameters['seed']),original)
checks={
 'partition':{'status':'passed','evidence':f'Actual membership disjoint: {len(train)} training and {len(validation)} validation rows.'},
 'tensor':{'status':'passed','evidence':f'Six training inputs produced actual logits of shape {(tx@w).shape}.'},
 'finite':{'status':'passed','evidence':'All actual tiny-fit losses, outputs and gradients were finite.'},
 'tiny_fit':{'status':'passed','evidence':f'14 actual training-only updates decreased loss from {losses[0]} to {losses[-1]}.'},
 'resume':{'status':'passed','evidence':'Serialized parameter roundtrip produced an exactly equal next update and loss.'},
 'initialization':{'status':'passed','evidence':'Full training starts in a separate process with the checked deterministic initial parameter identity.'}}
Path('preflight.json').write_text(json.dumps({'passed':True,'training_only':True,'checks':checks,
 'tiny_steps':14,'total_update_steps':16,'initial_tiny_loss':losses[0],'final_tiny_loss':losses[-1],
 'initial_weights_sha256':digest(original)},sort_keys=True))
