"""Local configuration/count/checkpoint checks with fake MMEngine, no Torch/GPU."""
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

ASSETS=Path(__file__).resolve().parents[2]/'assets/roadmodel'


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module);return module


@pytest.fixture
def assets(tmp_path,monkeypatch):
    helper=load('epoch_test_helper',ASSETS/'prepare_roadmodel_minimal.py')
    monkeypatch.setitem(sys.modules,'prepare_roadmodel_minimal',helper)
    monkeypatch.chdir(tmp_path)
    (tmp_path/'roadmodel-spec.json').write_text(json.dumps({'source_root':'/fixture','commit':'a'*40,
        'source_url':'https://example.invalid/fixture.git','train_samples':2,'validation_samples':1,'epochs':20}))
    return helper,load('epoch_test_workload',ASSETS/'workload.py')


@pytest.mark.parametrize('epochs,workers',[(1,None),(10,None),(20,None),(30,None),(3,4)])
def test_epoch_target_reaches_resolved_training_config_and_preflight(assets,tmp_path,monkeypatch,epochs,workers):
    helper,_=assets
    class Config(dict):
        def __getattr__(self,k):return self[k]
        def __setattr__(self,k,v):self[k]=v
        def to_dict(self):return dict(self)
        def dump(self,path):Path(path).write_text(json.dumps(self))
        @classmethod
        def fromfile(cls,path,**kwargs):
            if Path(path).name=='minimal.py':return cls(json.loads(Path(path).read_text()))
            return cls(max_epochs=int(os.environ['MAX_EPOCH']),amp=False,
                optimizer={'optimizer':{'lr':float(os.environ['LR'])}},
                train_loader={'batch_size':1},val_loader={'batch_size':1},
                train_dataset_config={'num_frames':1,'single_frame_mode':True},
                val_dataset_config={'num_frames':1,'single_frame_mode':True})
    monkeypatch.setitem(sys.modules,'mmengine',SimpleNamespace(Config=Config,__version__='synthetic-fixture'))
    repo=tmp_path/'repo';(repo/'config').mkdir(parents=True);(repo/'config/2-1-0.py').write_text('# fixture')
    (repo/'eval.py').write_text('# synthetic evaluator identity; never executed')
    index=tmp_path/'index.json';index.write_text('{}');baseline=tmp_path/'baseline';baseline.write_text('fake-not-torch')
    subset={'joint_train':['train'],'joint_eval':['eval']};fixed=tmp_path/'fixed.json';fixed.write_text(json.dumps(subset))
    monkeypatch.setattr(helper,'git',lambda root,*args:'a'*40 if args[0]=='rev-parse' else '' if args[0]=='status' else 'https://example.invalid/fixture.git')
    monkeypatch.setattr(helper,'pins',lambda path:{'INDEX_SHA256':helper.digest(index).removeprefix('sha256:')})
    monkeypatch.setattr(helper,'minimal_index',lambda *args:subset)
    args=SimpleNamespace(repo_root=repo,expected_commit='a'*40,expected_source_url='https://example.invalid/fixture.git',
        source_index=index,dataset_root=tmp_path,baseline=baseline,output_root=tmp_path/'prepared',train_frames=2,val_frames=1,
        loop_parameters={'amp':False,'learning_rate':2e-5},fixed_subset_path=fixed,epochs=epochs,loader_workers=workers)
    report=helper.prepare(args)
    assert report['epochs']==epochs and report['environment']['MAX_EPOCH']==str(epochs)
    assert json.loads((args.output_root/'minimal.py').read_text())['max_epochs']==epochs
    assert not report['gpu_started'] and not report['model_run_completed']
    if workers is not None:
        config=json.loads((args.output_root/'minimal.py').read_text())
        assert config['train_loader']['num_workers']==4
        assert config['train_loader']['persistent_workers'] is True
        assert config['train_loader']['prefetch_factor']==2
        assert config['val_loader']['num_workers']==0
        assert report['loader_configuration']['train']['num_workers']==4


def test_completion_requires_each_epoch_actual_updates_and_target_checkpoint(assets,tmp_path):
    _,workload=assets;epochs=20
    train=[(str(i),'0','2','1.0') for i in range(epochs) for _ in range(2)]
    val=[(str(i),'0','1.0') for i in range(epochs)]
    workload.completion_counts(train,val,40,epochs,2,1)
    for broken_train,broken_val,updates in [(train[:-1],val,40),(train,val[:-1],40),(train,val,2),
            ([('0',*r[1:]) for r in train],val,40)]:
        with pytest.raises(ValueError):workload.completion_counts(broken_train,broken_val,updates,epochs,2,1)
    (tmp_path/'epoch_1.pth').write_text('old-fixture')
    with pytest.raises(ValueError):workload.target_checkpoint(tmp_path,20)
    (tmp_path/'epoch_20.pth').write_text('target-fixture')
    assert workload.target_checkpoint(tmp_path,20).name=='epoch_20.pth'
    workload.checkpoint_progress({'epoch':20,'global_iter':40},20,2)
    with pytest.raises(ValueError):workload.checkpoint_progress({'epoch':1,'global_iter':2},20,2)
