"""Explicit official ImageNet initialization for the existing CIFAR ResNet18.

No local ML: prepare/apply are called only inside the authorized LPAI space.
Compatibility establishes a runnable hypothesis, not useful transferred features.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.request

URL = 'https://download.pytorch.org/models/resnet18-f37072fd.pth'
PREFIX = 'f37072fd'
NAME = 'resnet18-f37072fd.pth'


def remote_only():
    if not sys.platform.startswith('linux') or not Path('/lpai').is_dir():
        raise RuntimeError('development_space_only_no_local_ml')


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f, 'sha256').hexdigest()


def prepare(cache, torch):
    remote_only()
    folder=Path(cache);folder.mkdir(parents=True,exist_ok=True)
    path=folder/NAME
    downloaded=False
    if not path.exists():
        tmp=folder/(NAME+'.partial-'+str(os.getpid()))
        try:
            with urllib.request.urlopen(URL,timeout=60) as response,tmp.open('xb') as out:
                size=0
                for block in iter(lambda:response.read(1024*1024),b''):
                    size+=len(block)
                    if size>64*1024*1024:raise ValueError('official_checkpoint_size_bound_exceeded')
                    out.write(block)
            if not sha(tmp).startswith(PREFIX):raise ValueError('official_checkpoint_hash_prefix_mismatch')
            os.replace(tmp,path);downloaded=True
        finally:
            if tmp.exists():tmp.unlink()
    checksum=sha(path)
    if not checksum.startswith(PREFIX):raise ValueError('official_checkpoint_hash_prefix_mismatch')
    # weights_only prevents arbitrary object unpickling; require this actual runtime feature.
    state=torch.load(path,map_location='cpu',weights_only=True)
    assert state['conv1.weight'].shape==(64,3,7,7) and state['fc.weight'].shape==(1000,512)
    assert all(torch.isfinite(value).all().item() for value in state.values())
    report={'url':URL,'published_hash_prefix':PREFIX,'sha256':checksum,'bytes':path.stat().st_size,
        'downloaded_now':downloaded,'source_dataset':'ImageNet-1K; torchvision IMAGENET1K_V1',
        'upstream_train_validation_overlap':'Not audited for exact/near duplicates; no claim of zero overlap',
        'test_policy':'No CIFAR test example decoded or used; upstream metadata does not establish overlap absence',
        'stem_mapping':'conv1 7x7 centre[2:5,2:5] to existing CIFAR3x3, no rescaling; same stride1/padding1 and no maxpool',
        'backbone_mapping':'All non-fc tensors including BN affine/running statistics copied; train all layers',
        'head_mapping':'Discard ImageNet fc; preserve the existing seeded100-class head',
        'normalization':'Unchanged CIFAR mean/std in both arms; differs from official ImageNet inference transforms',
        'qualification':'Concrete initialization hypothesis; no claimed retained usefulness or measured accuracy'}
    manifest=folder/'resnet18-preparation.json'
    temporary=manifest.with_suffix('.tmp')
    temporary.write_text(json.dumps(report,sort_keys=True),encoding='utf-8');os.replace(temporary,manifest)
    return report


def apply(model, cache, torch):
    remote_only()
    folder=Path(cache);report=json.loads((folder/'resnet18-preparation.json').read_bytes())
    path=folder/NAME
    if sha(path)!=report['sha256'] or not report['sha256'].startswith(PREFIX):
        raise ValueError('prepared_checkpoint_full_sha256_changed')
    before=torch.get_rng_state().clone()
    head={key:value.detach().clone() for key,value in model.fc.state_dict().items()}
    state=torch.load(path,map_location='cpu',weights_only=True)
    state={key:value for key,value in state.items() if not key.startswith('fc.')}
    state['conv1.weight']=state['conv1.weight'][:,:,2:5,2:5].clone()
    target=model.state_dict()
    if any(key not in target or value.shape!=target[key].shape for key,value in state.items()):
        raise ValueError('pretrained_backbone_shape_mismatch')
    missing,unexpected=model.load_state_dict(state,strict=False)
    if set(missing)!={'fc.weight','fc.bias'} or unexpected:raise ValueError('unreviewed_pretrained_tensor_mapping')
    assert all(torch.equal(head[key],value) for key,value in model.fc.state_dict().items())
    assert torch.equal(before,torch.get_rng_state())
    assert all(torch.equal(value,model.state_dict()[key]) for key,value in state.items())
    return {**report,'loaded_tensors':len(state),'head_preserved':True,'global_rng_preserved':True,
        'all_backbone_tensors_equal_mapped_source':True}


def remote_controls(cache,torch,torchvision):
    """CPU-only mapping checks, restoring RNG after temporary model construction."""
    remote_only();report=prepare(cache,torch)
    before=torch.get_rng_state().clone()
    try:
        model=torchvision.models.resnet18(weights=None,num_classes=100)
        model.conv1=torch.nn.Conv2d(3,64,3,stride=1,padding=1,bias=False)
        model.maxpool=torch.nn.Identity()
        loaded=apply(model,cache,torch)
    finally:torch.set_rng_state(before)
    return {**loaded,'prepare_report':report,'control_device':'cpu','gpu_compute_invoked':False}
