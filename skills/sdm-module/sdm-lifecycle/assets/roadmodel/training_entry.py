"""Instrument actual RoadModel optimizer steps without editing scientific source."""
import json, os, pathlib, runpy, sys, torch
from torch.optim.optimizer import register_optimizer_step_pre_hook, register_optimizer_step_post_hook
root = pathlib.Path(os.environ['SDM_TELEMETRY_ROOT'])
updates = 0
def atomic(name, value):
    p=root/name; t=p.with_suffix('.tmp');t.write_text(json.dumps(value),encoding='utf-8');t.replace(p)
def before(optimizer, args, kwargs):
    bad=[]
    for group in optimizer.param_groups:
        for p in group['params']:
            if p.grad is not None and not torch.isfinite(p.grad).all(): bad.append(list(p.shape))
    if bad:
        atomic('numerical-failure.json',{'nonfinite_gradients':bad,'optimizer_updates':updates,'prevented_optimizer_step':True})
        raise FloatingPointError('nonfinite gradient prevented before optimizer update')
def after(optimizer,args,kwargs):
    global updates
    updates+=1
    atomic('progress.json',{'optimizer_updates':updates,'execution_device':'cuda','actual_optimizer_post_hook':True})
register_optimizer_step_pre_hook(before)
register_optimizer_step_post_hook(after)
target=sys.argv[1];sys.argv=sys.argv[1:];sys.path.insert(0,str(pathlib.Path(target).parent))
runpy.run_path(target,run_name='__main__')
