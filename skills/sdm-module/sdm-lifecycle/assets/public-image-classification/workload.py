"""CIFAR-100 research workload. ML runs only in a Linux LPAI development space.

The shared SDM executor owns launch, observation, artifact collection and cleanup.
This asset owns data/model/evaluation evidence, never platform operations.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys
import time


def write(path, value):
    target = Path(path)
    tmp = target.with_suffix(target.suffix + '.tmp')
    tmp.write_text(json.dumps(value, sort_keys=True, allow_nan=False), encoding='utf-8')
    os.replace(tmp, target)


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def verify_selected_checkpoint(metadata, curves, selected_epoch, replay=None):
    """Pure record check; replay itself is performed only by the remote evaluator."""
    if not curves or any(not math.isfinite(row[key]) for row in curves
                         for key in ('validation_accuracy','validation_loss')):
        raise RuntimeError('selected_checkpoint_invalid_curves')
    highest=max(row['validation_accuracy'] for row in curves)
    selected=min((row for row in curves if row['validation_accuracy']==highest),key=lambda row:row['epoch'])
    saved=metadata.get('validation',{})
    if (selected_epoch!=selected['epoch'] or metadata.get('epoch')!=selected['epoch']
            or saved.get('accuracy')!=highest or type(saved.get('loss')) not in (int,float)
            or not math.isfinite(saved['loss']) or abs(saved['loss']-selected['validation_loss'])>1e-6):
        raise RuntimeError('selected_checkpoint_metadata_mismatch')
    if replay is not None and (replay.get('samples')!=5000 or replay.get('accuracy')!=highest
            or type(replay.get('loss')) not in (int,float) or not math.isfinite(replay['loss'])
            or abs(replay['loss']-selected['validation_loss'])>1e-6):
        raise RuntimeError('selected_checkpoint_replay_mismatch')
    return {'epoch':selected['epoch'],'metadata_verified':True,'replay_verified':replay is not None,
            'accuracy_tolerance':0,'loss_absolute_tolerance':1e-6,'validation_samples':5000,
            'selection_rule':'earliest strict maximum in complete epoch history'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--learning-rate', type=float, required=True)
    parser.add_argument('--spec', default='experiment-spec.json')
    parser.add_argument('--mixup-alpha', type=float, default=0.)
    parser.add_argument('--initialization', choices=('random','imagenet-cifar-center'), default=None)
    parser.add_argument('--weights-cache', default='/lpai/sdm-public-weights')
    args = parser.parse_args()
    if not sys.platform.startswith('linux') or not Path('/lpai').is_dir():
        raise RuntimeError('development_space_only_no_local_ml')
    if not math.isfinite(args.mixup_alpha) or not 0 <= args.mixup_alpha <= 1:
        raise ValueError('invalid_mixup_alpha')
    from gpu_runtime import restrict
    gpu_binding=restrict()
    write('gpu-binding.json',gpu_binding)
    import numpy as np
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, Subset
    import torchvision
    from torchvision import datasets, transforms

    if not torch.cuda.is_available():
        raise RuntimeError('cuda_required_no_cpu_fallback')
    spec = json.loads(Path(args.spec).read_bytes())
    seed, epochs, batch = spec['seed'], spec['epochs'], spec['batch_size']
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed); torch.cuda.manual_seed_all(seed)
    torch.set_num_threads(4)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    os.environ['CUDA_DEVICE_ORDER'] = 'PCI_BUS_ID'
    # Visibility was narrowed by UUID before torch import; never touch busy peers.
    if torch.cuda.device_count()!=1:raise RuntimeError('single_selected_cuda_device_required')
    index=0
    torch.cuda.set_device(index)
    device = torch.device('cuda', index)
    if torch.cuda.mem_get_info(index)[0] < 6 * 1024**3:
        raise RuntimeError('insufficient_shared_gpu_memory')
    root = Path(spec['data_cache'])
    root.mkdir(parents=True, exist_ok=True)
    normalize = transforms.Normalize((.5071, .4867, .4408), (.2675, .2565, .2761))
    clean = transforms.Compose([transforms.ToTensor(), normalize])
    aug = transforms.Compose([transforms.RandomCrop(32, padding=4), transforms.RandomHorizontalFlip(),
                              transforms.ToTensor(), normalize])
    # torchvision verifies published archive/member MD5 before decoding the training partition.
    train_data = datasets.CIFAR100(str(root), train=True, download=True, transform=aug)
    clean_data = datasets.CIFAR100(str(root), train=True, download=False, transform=clean)
    labels = np.asarray(train_data.targets)
    rng = np.random.default_rng(seed)
    train_ids, val_ids, diagnostic_ids = [], [], []
    for cls in range(100):
        ids = np.flatnonzero(labels == cls)
        if len(ids) != 500:
            raise RuntimeError('official_class_support_mismatch')
        ids = rng.permutation(ids)
        val_ids.extend(ids[:50].tolist()); train_ids.extend(ids[50:].tolist())
        diagnostic_ids.extend(ids[50:95].tolist())
    if set(train_ids) & set(val_ids) or len(train_ids) != 45000 or len(val_ids) != 5000:
        raise RuntimeError('invalid_stratified_partition')
    split = {'seed': seed, 'train': train_ids, 'validation': val_ids, 'clean_train_diagnostic': diagnostic_ids,
             'test_examples_decoded': False, 'test_integrity_checks': True,
             'test_policy': 'reserved; member checksums/extraction may occur, no test examples decoded or used'}
    write('split.json', split)
    members = root / 'cifar-100-python'
    data_digest = sha(members / 'train')
    provenance = {'homepage': 'https://cave.cs.toronto.edu/kriz/cifar.html',
                  'archive_url': train_data.url, 'archive_md5_expected': train_data.tgz_md5,
                  'archive_present': (root / train_data.filename).is_file(),
                  'archive_sha256': sha(root / train_data.filename) if (root / train_data.filename).is_file() else None,
                  'member_md5_verified_by_torchvision': True,
                  'train_sha256': data_digest, 'meta_sha256': sha(members / 'meta'),
                  'split_sha256': sha('split.json'), 'train_samples': 45000, 'validation_samples': 5000,
                  'diagnostic_samples': 4500, 'classes': train_data.classes,
                  'test_examples_decoded': False, 'test_integrity_checks': True}
    write('data-manifest.json', provenance)
    generator = torch.Generator().manual_seed(seed)
    def worker_seed(_):
        value = torch.initial_seed() % 2**32
        random.seed(value); np.random.seed(value)
    def loader(ids, dataset, shuffle=False):
        return DataLoader(Subset(dataset, ids), batch_size=batch, shuffle=shuffle,
                          num_workers=4, pin_memory=True, drop_last=False,
                          worker_init_fn=worker_seed, generator=generator if shuffle else None)
    training = loader(train_ids, train_data, True)
    validation = loader(val_ids, clean_data)
    diagnostic = loader(diagnostic_ids, clean_data)
    model = torchvision.models.resnet18(weights=None, num_classes=100)
    model.conv1 = nn.Conv2d(3, 64, 3, stride=1, padding=1, bias=False)
    model.maxpool = nn.Identity()
    initialization={'kind':'random','imported_data':False}
    if args.initialization=='imagenet-cifar-center':
        from pretrained_runtime import apply
        initialization={'kind':args.initialization,'imported_data':True,**apply(model,args.weights_cache,torch)}
    write('initialization-manifest.json',initialization)
    model.to(device)
    optimizer = torch.optim.SGD(model.parameters(), lr=args.learning_rate, momentum=.9, weight_decay=5e-4)
    schedule_horizon = spec.get('schedule_horizon', epochs)
    if type(schedule_horizon) is not int or schedule_horizon < epochs:
        raise RuntimeError('schedule_horizon_must_cover_training')
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=schedule_horizon)
    criterion = nn.CrossEntropyLoss()
    mixing = None
    if args.mixup_alpha:
        from mixup_runtime import BatchMixup
        mixing = BatchMixup(args.mixup_alpha, seed + 104729, torch, np)
    method = {'method': 'batch-mixup' if mixing else 'standard', 'alpha': args.mixup_alpha,
              'application': 'every minibatch' if mixing else 'disabled',
              'lambda_distribution': 'Beta(alpha,alpha)' if mixing else None,
              'label_loss': 'paired weighted cross entropy' if mixing else 'hard-label cross entropy',
              'randomness': 'independent NumPy Beta and CPU torch permutation generators' if mixing else 'baseline',
              'mixup_seed': seed + 104729 if mixing else None,
              'module_sha256': sha('mixup_runtime.py') if mixing else None,
              'validation': 'unchanged clean hard-label cross entropy and top1; earliest strict maximum',
              'train_metric_kind': 'weighted mixed-label top1' if mixing else 'hard-label top1'}
    write('method-manifest.json', method)
    count = {'updates': 0}
    def post_step(*_): count['updates'] += 1
    if not hasattr(optimizer, 'register_step_post_hook'):
        raise RuntimeError('optimizer_post_hook_support_required')
    optimizer.register_step_post_hook(post_step)
    def evaluate(dl, keep=False):
        model.eval(); loss_sum = correct = total = 0; pred = []; truth = []
        with torch.inference_mode():
            for images, target in dl:
                images, target = images.to(device, non_blocking=True), target.to(device, non_blocking=True)
                logits = model(images)
                if not torch.isfinite(logits).all(): raise RuntimeError('nonfinite_evaluation_logits')
                loss_sum += float(criterion(logits, target)) * len(target)
                chosen = logits.argmax(1); correct += int((chosen == target).sum()); total += len(target)
                if keep: pred.extend(chosen.cpu().tolist()); truth.extend(target.cpu().tolist())
        return {'loss': loss_sum / total, 'accuracy': correct / total, 'samples': total,
                **({'predictions': pred, 'targets': truth} if keep else {})}
    started = time.time(); curves = []; best = -1.; best_epoch = 0
    for epoch in range(1, epochs + 1):
        tick = time.time(); model.train(); loss_sum = correct = total = 0
        lr = optimizer.param_groups[0]['lr']
        for images, target in training:
            images, target = images.to(device, non_blocking=True), target.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            if mixing:
                images, paired, weight = mixing.batch(images, target)
            logits = model(images)
            loss = mixing.loss(criterion, logits, target, paired, weight) if mixing else criterion(logits, target)
            if not torch.isfinite(loss): raise RuntimeError('nonfinite_training_loss')
            loss.backward()
            # Infinite max_norm leaves finite gradients unchanged; one norm checks all parameters.
            grad_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), float('inf'), error_if_nonfinite=True)
            optimizer.step()
            chosen = logits.argmax(1)
            correct += (weight * int((chosen == target).sum()) + (1 - weight) * int((chosen == paired).sum())
                        if mixing else int((chosen == target).sum()))
            loss_sum += float(loss) * len(target); total += len(target)
            if count['updates'] % 10 == 0:
                write('progress.json', {'optimizer_updates': count['updates'], 'epoch': epoch,
                                        'train_samples_this_epoch': total, 'device': 'cuda', 'time': time.time()})
        score = evaluate(validation)
        if total != 45000 or score['samples'] != 5000:
            raise RuntimeError('epoch_sample_support_mismatch')
        scheduler.step()
        row = {'epoch': epoch, 'learning_rate': lr, 'train_aug_loss': loss_sum / total,
               'train_aug_accuracy': correct / total, 'validation_loss': score['loss'],
               'validation_accuracy': score['accuracy'], 'optimizer_updates': count['updates'],
               'seconds': time.time() - tick}
        curves.append(row); write('curves.json', curves); print(json.dumps(row), flush=True)
        if score['accuracy'] > best:
            best, best_epoch = score['accuracy'], epoch
            torch.save({'model': model.state_dict(), 'epoch': epoch, 'validation': score}, 'best.pt.tmp')
            os.replace('best.pt.tmp', 'best.pt')
        checkpoint = {'model': model.state_dict(), 'optimizer': optimizer.state_dict(),
                      'scheduler': scheduler.state_dict(), 'epoch': epoch, 'optimizer_updates': count['updates'],
                      'python_rng': random.getstate(), 'numpy_rng': np.random.get_state(),
                      'torch_rng': torch.get_rng_state(), 'cuda_rng': torch.cuda.get_rng_state_all(),
                      'loader_generator': generator.get_state(), 'spec': spec, 'learning_rate': args.learning_rate,
                      'data_digest': data_digest, 'split_digest': sha('split.json')}
        if mixing: checkpoint['mixup'] = mixing.state()
        torch.save(checkpoint, 'last.pt.tmp'); os.replace('last.pt.tmp', 'last.pt')
    expected = math.ceil(45000 / batch) * epochs
    if count['updates'] != expected: raise RuntimeError('optimizer_update_count_mismatch')
    if mixing and mixing.batches != expected: raise RuntimeError('mixup_application_count_mismatch')
    import inspect
    load_options = {'weights_only': False} if 'weights_only' in inspect.signature(torch.load).parameters else {}
    # Only load our own generated files; no downloaded checkpoint/pickle is accepted here.
    restored = torch.load('last.pt', map_location='cpu', **load_options)
    verified = restored['epoch'] == epochs and restored['optimizer_updates'] == expected
    if not verified or not restored['optimizer']['state']: raise RuntimeError('checkpoint_progress_mismatch')
    best_state = torch.load('best.pt', map_location='cpu', **load_options)
    verify_selected_checkpoint(best_state,curves,best_epoch)
    model.load_state_dict(best_state['model'])
    final = evaluate(validation, True); train_clean = evaluate(diagnostic)
    selected_verification=verify_selected_checkpoint(best_state,curves,best_epoch,final)
    confusion = np.zeros((100, 100), dtype=np.int64)
    for target, chosen in zip(final['targets'], final['predictions']): confusion[target, chosen] += 1
    write('predictions.json', {'indices': val_ids, 'targets': final['targets'], 'predictions': final['predictions'],
                               'checkpoint': 'best.pt', 'epoch': best_epoch, 'test_examples_decoded': False})
    write('errors.json', {'confusion': confusion.tolist(), 'class_support': confusion.sum(1).tolist(),
                         'per_class_accuracy': (confusion.diagonal() / confusion.sum(1)).tolist(),
                         'class_names': train_data.classes})
    write('checkpoint-manifest.json', {k: {'sha256': sha(k), 'bytes': Path(k).stat().st_size}
                                       for k in ('last.pt', 'best.pt')})
    parameters = {'learning_rate': args.learning_rate}
    if mixing: parameters['mixup_alpha'] = args.mixup_alpha
    if args.initialization is not None:parameters['initialization']=args.initialization
    result = {'parameters': parameters, 'architecture': 'cifar-resnet18' if args.initialization is not None else 'cifar-resnet18-scratch',
              'initialization':initialization['kind'],
              'epochs': epochs, 'schedule_horizon': schedule_horizon,
              'batch_size': batch, 'seed': seed, 'execution_device': 'cuda',
              'data_kind': 'public-cifar100-stratified-training-partition', 'train_samples': 45000,
              'validation_samples': 5000, 'data_digest': data_digest, 'split_digest': sha('split.json'),
              'source_digest': sha(__file__), 'spec_digest': sha(args.spec), 'checkpoint_verified': verified,
              'selected_checkpoint_verified':True,
              'selected_checkpoint_verification':selected_verification,
              'checkpoint_verification_scope': 'own-file deserialize, final epoch/update metadata and nonempty optimizer state',
              'resume_implemented': False, 'resume_validated': False, 'gradient_clipping': False,
              'optimizer_updates': count['updates'], 'validation_loss': final['loss'],
              'validation_accuracy': final['accuracy'], 'last_validation_accuracy': curves[-1]['validation_accuracy'],
              'last_validation_loss': curves[-1]['validation_loss'],
              'training_method': method['method'], 'mixup_batches': mixing.batches if mixing else 0,
              'train_metric_kind': method['train_metric_kind'],
              'clean_train_accuracy': train_clean['accuracy'], 'clean_train_loss': train_clean['loss'],
              'best_epoch': best_epoch, 'test_examples_decoded': False, 'test_integrity_checks': True,
              'tf32_matmul': False, 'tf32_cudnn': False, 'seconds': time.time() - started,
              'torch': torch.__version__, 'torchvision': torchvision.__version__, 'gpu': torch.cuda.get_device_name(index),
              'gpu_index': index, 'cuda_peak_bytes': torch.cuda.max_memory_allocated(index),
              'resource_uid': spec['resource_uid'], 'cuda_visible_devices': os.environ.get('CUDA_VISIBLE_DEVICES'),
              'gpu_uuid': gpu_binding['selected']['uuid'],
              'qualification': 'one seed, fixed short recipe, shared GPU; no architecture ceiling or test/SOTA claim'}
    write('progress.json', {'optimizer_updates': count['updates'], 'epoch': epochs, 'device': 'cuda', 'complete': True})
    write('result.json', result); print(json.dumps(result), flush=True)


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        write('failure.json', {'type': type(error).__name__, 'message': str(error), 'time': time.time()})
        raise
