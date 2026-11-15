"""CPU fakes exercise runtime observation; never imports the real torch/GPU."""
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import pytest

import roadmodel_process_observer as observer
import roadmodel_component_gate as gate


def test_update_health_calibration_recovery_and_unchanged_startup_limit():
    h=observer.UpdateHealth();scale=65536
    for _ in range(10):
        row=h.feed(False,True,scale,scale/2,0);scale/=2
        assert row['classification']=='amp_scale_backoff' and not row['stop']
    row=h.feed(True,True,scale,scale,1)
    assert row['successful_updates']==1 and row['consecutive_bad_updates']==0
    for i in range(5):
        row=h.feed(False,True,scale,scale/2,0);scale/=2
        assert row['stop']==(i==4)
    assert row['classification']=='persistent_update_failure'
    h=observer.UpdateHealth();scale=65536
    for i in range(20):
        row=h.feed(False,True,scale,scale/2,0);scale/=2
        assert row['stop']==(i==19)
    assert row['classification']=='amp_calibration_exhausted'


@pytest.mark.parametrize('args,reason',[
    ((False,True,64,64,0),'amp_skip_without_scale_backoff'),
    ((False,True,64,32,1),'nonfinite_optimizer_update'),
    ((True,False,64,64,1),'nonfinite_loss'),
    ((True,True,64,64,0),'finite_gradient_without_update'),
    ((False,True,1,0,0),'invalid_amp_scale')])
def test_update_health_rejects_inconsistent_or_corrupt_state(args,reason):
    row=observer.UpdateHealth().feed(*args)
    assert row['stop'] and row['classification']==reason


def test_sampled_gradient_cannot_override_real_boundary_recovery():
    h=gate.TrainingHealth()
    event={'schema':'normal-update-health/v1','attempt':1,'rank':0,'stop':False,'classification':'amp_scale_backoff'}
    assert h.feed('NORMAL_UPDATE_HEALTH '+json.dumps(event)) is None
    for i in (50,100,150,200,250):
        assert h.feed(f'[TRAIN] Epoch 0 Iter {i}/27796: Loss: 1.0 (1), grad_norm: nan, lr: 1e-6') is None
    assert h.feed('[TRAIN] Epoch 0 Iter 300/27796: Loss: nan (1), grad_norm: nan, lr: 1e-6')
    event.update(stop=True,classification='amp_calibration_exhausted')
    assert h.feed('NORMAL_UPDATE_HEALTH '+json.dumps(event))['reason']=='amp_calibration_exhausted'


@pytest.mark.parametrize('bad_count,good_first,expected',[(10,False,None),(20,False,'amp_calibration_exhausted'),(5,True,'persistent_update_failure')])
def test_observer_actual_scaler_skips_steps_and_logs_every_boundary(tmp_path,monkeypatch,capsys,bad_count,good_first,expected):
    import math
    class Tensor:
        device='cpu'
        def __init__(self,v):self.value=v;self.grad=None
    class Optimizer:
        def __init__(self,p):self.param_groups=[{'params':[p]}];self.state={};self.pre=[];self.post=[]
        def register_step_post_hook(self,h):self.post.append(h)
        def register_step_pre_hook(self,h):self.pre.append(h)
        def step(self):
            for h in self.pre:h(self,(),{})
            for h in self.post:h(self,(),{})
    class Scaler:
        def __init__(self):self.value=65536;self.bad=False
        def get_scale(self):return self.value
        def state_dict(self):return {'scale':self.value}
        def step(self,opt):
            self.bad=not math.isfinite(opt.param_groups[0]['params'][0].grad.value)
            if not self.bad:opt.step()
        def update(self):
            if self.bad:self.value/=2
    class CudaScaler(Scaler):pass
    def isfinite(t):return SimpleNamespace(all=lambda:SimpleNamespace(item=lambda:math.isfinite(t.value)))
    fake=SimpleNamespace(optim=SimpleNamespace(Optimizer=Optimizer),save=lambda *_:None,
        amp=SimpleNamespace(GradScaler=Scaler),cuda=SimpleNamespace(amp=SimpleNamespace(GradScaler=CudaScaler)),
        is_tensor=lambda t:isinstance(t,Tensor),isfinite=isfinite,tensor=lambda v,**_:Tensor(v),
        nn=SimpleNamespace(utils=SimpleNamespace(clip_grad_norm_=lambda *a,**k:Tensor(1))))
    monkeypatch.setitem(sys.modules,'torch',fake);monkeypatch.setenv('RANK','0')
    control=tmp_path/'control';control.mkdir();output=tmp_path/'output';output.mkdir()
    sequence=([1.] if good_first else [])+[float('nan')]*bad_count+[1.]
    text='import torch\np=torch.tensor(1.)\noptimizer=torch.optim.Optimizer(p)\nscaler=torch.cuda.amp.GradScaler()\nloss=torch.tensor(1.)\nepoch=0\n'
    text+='for i_iter,v in enumerate('+repr(sequence).replace('nan','float("nan")')+'):\n'
    text+=' global_iter=(i_iter+1)*24-1\n p.grad=torch.tensor(v)\n torch.nn.utils.clip_grad_norm_([p],35)\n scaler.step(optimizer)\n scaler.update()\n'
    entry=tmp_path/'train.py';entry.write_text(text)
    if expected:
        with pytest.raises(FloatingPointError,match=expected):observer.observe(entry,[],control,output,'run')
    else:observer.observe(entry,[],control,output,'run')
    rows=[json.loads(s.split(' ',1)[1]) for s in capsys.readouterr().out.splitlines() if s.startswith('NORMAL_UPDATE_HEALTH ')]
    assert len(rows)==bad_count+(1 if good_first or not expected else 0)
    assert all(r['optimizer_update_delta']==0 for r in rows if not r['grad_finite'])
    assert rows[-1]['optimizer_updates']==(1 if good_first or not expected else 0)
    assert rows[-1]['stop']==bool(expected)
    assert Scaler.update.__name__=='update' and CudaScaler.update is Scaler.update


def test_health_distinguishes_transient_amp_and_persistent_failure():
    health=gate.TrainingHealth()
    def line(i,grad='nan',loss='2.0'):
        return f'[TRAIN] Epoch 41 Iter {i}/1158: Loss: {loss} (2), grad_norm: {grad}, lr: 0.00004'
    assert health.feed('echo '+repr('non-finite grad_norm')) is None
    assert health.feed(line(0)) is None
    assert health.feed(line(0)) is None  # duplicated record cannot trip guard
    assert health.feed(line(50,'0.2')) is None
    for i in (100,150,200,250):assert health.feed(line(i)) is None
    assert health.feed(line(300))['consecutive_nonfinite_records']==5
    assert gate.TrainingHealth().feed(line(0,loss='nan'))['loss_finite'] is False


def test_live_process_health_stop_preserves_log_and_reaps_child(tmp_path):
    script='import time\nfor i in range(5):\n print(f"[TRAIN] Epoch 1 Iter {i}/10: Loss: 2.0 (2), grad_norm: nan, lr: .1",flush=True)\ntime.sleep(30)\n'
    log=tmp_path/'training.log';started=time.monotonic()
    assert gate.process([sys.executable,'-B','-c',script],str(tmp_path),log,None)==65
    assert time.monotonic()-started<15 and log.exists()
    assert json.loads(log.with_suffix('.health.json').read_bytes())['reason']=='persistent_nonfinite_training'


def test_checkpoint_finiteness_rejects_nested_nan():
    fake=SimpleNamespace()
    assert observer.finite_state(fake,{'optimizer':{'a':[1.0,2.0]}})
    assert not observer.finite_state(fake,{'state_dict':{'a':float('nan')}})


def test_checkpoint_metric_sentinel_is_not_training_state_corruption():
    fake=SimpleNamespace()
    value={'state_dict':{'x':1.},'optimizer':{},'scheduler':{},'epoch':1,'global_iter':24,
        'best_traj':{'epoch':-1,'feasible':False,'z_float_mae_cm':float('inf')}}
    assert not observer.finite_state(fake,value)
    assert observer.finite_checkpoint_training_state(fake,value)
    value['optimizer']={'momentum':float('nan')}
    assert not observer.finite_checkpoint_training_state(fake,value)


@pytest.mark.parametrize('bad',[False,True])
def test_checkpoint_atomic_save_scaler_and_nan_preserve_last_healthy(tmp_path,monkeypatch,bad):
    class Optimizer:
        def __init__(self):pass
        def register_step_post_hook(self,hook):pass
    class Scaler:
        def __init__(self):self.scale=1024
        def state_dict(self):return {'scale':self.scale}
        def load_state_dict(self,state):self.scale=state['scale']
    saved=[]
    def save(value,path):
        saved.append(value);Path(path).write_text('new healthy')
    fake=SimpleNamespace(optim=SimpleNamespace(Optimizer=Optimizer),save=save,amp=SimpleNamespace(GradScaler=Scaler))
    monkeypatch.setitem(sys.modules,'torch',fake);monkeypatch.setenv('RANK','0')
    control=tmp_path/'control';control.mkdir();output=tmp_path/'output';output.mkdir()
    checkpoint=output/'epoch.pth';checkpoint.write_text('last healthy')
    entry=tmp_path/'train.py';entry.write_text('import torch\ns=torch.amp.GradScaler()\n'
        'torch.save({"state_dict":{"x":'+('float("nan")' if bad else '1.0')+'},"optimizer":{},"scheduler":{},"epoch":1,"global_iter":1},'+repr(str(checkpoint))+')\n')
    if bad:
        with pytest.raises(ValueError,match='nonfinite_state'):observer.observe(entry,[],control,output,'run')
        assert checkpoint.read_text()=='last healthy' and not saved
    else:
        observer.observe(entry,[],control,output,'run')
        assert saved[0]['amp_scaler']=={'scale':1024} and saved[0]['normal_health']['finite_state']
    assert Scaler.__init__.__name__=='__init__' and fake.save is save


@pytest.mark.parametrize('take_step,expected',[(True,True),(False,False)])
def test_observer_counts_real_step_calls_and_requires_completed_checkpoint(tmp_path,monkeypatch,take_step,expected):
    class Optimizer:
        def __init__(self):self.hooks=[]
        def register_step_post_hook(self,hook):self.hooks.append(hook)
        def step(self):
            for hook in self.hooks:hook(self,(),{})
    def save(value,path,*args,**kwargs):Path(path).write_bytes(b'CPU-TEXT-NOT-PICKLE')
    fake=SimpleNamespace(optim=SimpleNamespace(Optimizer=Optimizer),save=save)
    monkeypatch.setitem(sys.modules,'torch',fake);monkeypatch.setenv('RANK','0')
    control=tmp_path/'control';control.mkdir();output=tmp_path/'output';output.mkdir()
    entry=tmp_path/'train.py'
    entry.write_text('import torch\nopt=torch.optim.Optimizer()\n'+('opt.step()\n' if take_step else '')+
        'torch.save({"state_dict":{},"optimizer":{},"scheduler":{},"epoch":300,"global_iter":10},'+repr(str(output/'epoch.pth'))+')\n')
    observer.observe(entry,[],control,output,'run')
    rank=json.loads((control/'run-rank-0.json').read_bytes())
    assert rank['optimizer_updates']==int(take_step) and rank['completed']
    assert Optimizer.__init__.__name__=='__init__' and fake.save is save
    plan={'runtime_output_root':str(output),'identity':{'study':'CPU','trial':'normal','attempt':'1'},'world_size':1,'training':{'epochs':300}}
    report=gate.training_report({'plan':plan},control,0,{'request_id':'CPU-REQUEST'})
    assert report['completed'] is expected and report['completed_epochs']==300
    assert report['full_resume_qualified'] is False


def test_gate_requires_scope_bound_s12_before_model_start(tmp_path):
    from canonical import digest
    bundle={'plan_digest':'sha256:'+'1'*64,'authorization_digest':'sha256:'+'2'*64,'component_uid':'CPU',
        'plan':{'identity':{'attempt':'CPU'}}}
    (tmp_path/'bundle.json').write_text(json.dumps(bundle))
    subjects={'plan_digest':bundle['plan_digest'],'authorization_digest':bundle['authorization_digest'],
        'execution_scope':'normal-training','component_uid':'CPU','execution_kind':'run','bundle_digest':gate.sha(tmp_path/'bundle.json')}
    request=tmp_path/'REQ-CPU-S12.S12.json';request.write_text(json.dumps({'operation':'ml.normal-execution','subjects':subjects}))
    permit={'plan_digest':bundle['plan_digest'],'identity':bundle['plan']['identity'],'node':'15',
        'authorization_digest':bundle['authorization_digest'],'ready_digest':'ready','request_id':'REQ-CPU-S12','s12_digest':gate.sha(request)}
    (tmp_path/'start-permit.json').write_text(json.dumps(permit))
    assert gate.normal_permit(bundle,tmp_path,'15','ready')==permit
    subjects['execution_scope']='branch-diagnostic';request.write_text(json.dumps({'operation':'ml.normal-execution','subjects':subjects}))
    permit['s12_digest']=gate.sha(request);(tmp_path/'start-permit.json').write_text(json.dumps(permit))
    with pytest.raises(ValueError,match='scope_mismatch'):gate.normal_permit(bundle,tmp_path,'15','ready')


def test_component_budget_covers_preflight_without_starting_model(tmp_path,monkeypatch):
    def slow(*_):time.sleep(2)
    monkeypatch.setattr(gate,'preflight',slow)
    bundle={'allocation_wall_seconds':.01,'plan':{'distributed':{'nodes':1}},'runtime_assets':[{}]}
    with pytest.raises(TimeoutError,match='budget_exhausted'):gate.run(bundle,tmp_path)
    assert not (tmp_path/'started.json').exists()


def test_local_process_timeout_reaps_child(tmp_path):
    code=gate.process([sys.executable,'-B','-c','import time; time.sleep(20)'],str(tmp_path),tmp_path/'CPU.log',.1)
    assert code==-9
