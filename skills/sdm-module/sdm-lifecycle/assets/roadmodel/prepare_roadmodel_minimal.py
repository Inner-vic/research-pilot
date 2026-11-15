#!/usr/bin/env python3
"""Prepare a small real RoadModel configuration; no platform write or GPU run."""
import argparse
import ast
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


PROFILE = {
    'DATASET': 'SmartChassisDatasetV5', 'DATA_SRC': 'self', 'TRAIN_MODE': 'occ',
    'SURFACE_AGGREGATOR': 'meshaggregator', 'SURFACE_EMPTY_SEMANTIC_LOSS': 'True',
    'ANCHOR_OPACITY_MSE_LOSS_WEIGHT': '0.5', 'NUM_ANCHOR': '2400',
    'BATCH_SIZE': '1', 'NUM_WORKERS': '0', 'MAX_EPOCH': '1', 'NUM_FRAMES': '1',
    'FRAME_STRIDE': '1', 'SEQUENTIAL_TRAIN': 'False', 'BALANCED_SAMPLING': 'False',
    'PERSISTENT_WORKERS': 'False', 'SURFACE_LOCK_YAW': 'True',
    'SURFACE_SEM_LOSS_WEIGHT': '4.0', 'SURFACE_USE_LOVASZ': 'True',
    'SURFACE_LOVASZ_LOSS_WEIGHT': '0.5', 'LR': '4e-5', 'SAVE_CKPT': 'True',
    'KEEP_CKPT_NUM': '2', 'DEBUG_STATS': 'False',
}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return 'sha256:' + h.hexdigest()


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def pins(path):
    """Read committed data-selection constants without executing model code."""
    required = {'TRAIN_SCENES', 'EVAL_SCENES', 'INDEX_SHA256'}
    result = {}
    for node in ast.parse(Path(path).read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in required:
                result[target.id] = ast.literal_eval(node.value)
    if set(result) != required:
        raise ValueError('committed_selection_constants_required')
    if set(result['TRAIN_SCENES']) & set(result['EVAL_SCENES']):
        raise ValueError('committed_train_eval_overlap')
    return result


def select_frames(partition, scenes, count):
    """Round-robin across the existing scene selection, with stable frame order."""
    ordered = {}
    for scene in scenes:
        info = partition[scene]
        frames = sorted(info['frames'], key=lambda item: item['frame_index'])
        if len({item['frame_index'] for item in frames}) != len(frames):
            raise ValueError('duplicate_frame_identity')
        ordered[scene] = frames
    selected = {}
    offset = 0
    remaining = count
    while remaining:
        progress = False
        for scene in scenes:
            if offset < len(ordered[scene]):
                if scene not in selected:
                    selected[scene] = deepcopy(partition[scene])
                    selected[scene]['frames'] = []
                selected[scene]['frames'].append(deepcopy(ordered[scene][offset]))
                remaining -= 1
                progress = True
                if not remaining:
                    break
        if not progress:
            raise ValueError('insufficient_real_frames')
        offset += 1
    return selected


def minimal_index(data, selection, train_frames=8, val_frames=2):
    if train_frames < 1 or val_frames < 1:
        raise ValueError('positive_sample_counts_required')
    train = select_frames(data['joint_train'], selection['TRAIN_SCENES'], train_frames)
    val = select_frames(data['joint_eval'], selection['EVAL_SCENES'], val_frames)
    partitions = {'train', 'joint_train', 'eval', 'joint_eval', 'vision_train', 'vision_eval'}
    result = {key: deepcopy(value) for key, value in data.items() if key not in partitions}
    # occ mode merges train and joint_train by scene key. Both entries must
    # retain the same bounded frames, so neither can restore the full split.
    result['train'] = deepcopy(train)
    result['joint_train'] = train
    result['eval'] = deepcopy(val)
    result['joint_eval'] = val
    result['vision_train'] = {}
    result['vision_eval'] = {}
    return result


def config_env_names(path):
    source = Path(path).read_text(encoding='utf-8')
    return set(re.findall(r"(?:os\.environ\.get\(|os\.environ\[|_env_bool\()\s*['\"]([A-Z][A-Z0-9_]*)['\"]", source))


def loop_parameters(value):
    import math
    if not isinstance(value,dict) or set(value)!={'amp','learning_rate'} or type(value['amp']) is not bool:
        raise ValueError('exact_loop_parameters_required')
    rate=value['learning_rate']
    if type(rate) not in (int,float) or not math.isfinite(rate) or not 1e-7<=rate<=4e-5:
        raise ValueError('bounded_loop_learning_rate_required')
    return {'amp':value['amp'],'learning_rate':float(rate)}


def value_digest(value):
    return 'sha256:'+hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def prepare(args):
    root = args.repo_root.resolve()
    epochs=getattr(args,'epochs',1)
    if type(epochs) is not int or epochs<1:raise ValueError('positive_training_epochs_required')
    if not re.fullmatch(r'[0-9a-f]{40}', args.expected_commit):
        raise ValueError('exact_commit_required')
    if git(root, 'rev-parse', 'HEAD') != args.expected_commit or git(root, 'status', '--porcelain'):
        raise ValueError('clean_exact_source_required')
    if git(root, 'remote', 'get-url', 'origin') != args.expected_source_url:
        raise ValueError('personal_fork_origin_mismatch')
    selection = pins(root / 'tools/bmad_single_run/prepare.py')
    if digest(args.source_index) != 'sha256:' + selection['INDEX_SHA256']:
        raise ValueError('source_index_digest_mismatch')
    data = minimal_index(json.loads(args.source_index.read_text(encoding='utf-8')),
                         selection, args.train_frames, args.val_frames)
    # Import the actual installed MMEngine before creating any output.
    from mmengine import Config, __version__ as mmengine_version
    out = args.output_root.resolve()
    out.mkdir(parents=True, exist_ok=False)
    subset = out / 'subset.json'
    subset.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    env = {**PROFILE, 'MAX_EPOCH':str(epochs),'DATASET_PATH': str(args.dataset_root), 'DATA_LINK_FILE': str(subset)}
    loader_workers=getattr(args,'loader_workers',None)
    if loader_workers is not None:
        if type(loader_workers) is not int or not 0<=loader_workers<=16:
            raise ValueError('bounded_loader_workers_required')
        env.update(NUM_WORKERS=str(loader_workers),PERSISTENT_WORKERS=str(loader_workers>0),PREFETCH_FACTOR='2' if loader_workers else '1')
    controls=getattr(args,'loop_parameters',None)
    fixed_subset=getattr(args,'fixed_subset_path',None)
    if controls is not None:
        controls=loop_parameters(controls)
        fixed_subset=Path(fixed_subset)
        if fixed_subset.is_symlink() or not fixed_subset.is_file() or digest(fixed_subset)!=digest(subset):
            raise ValueError('fixed_loop_subset_changed')
        env.update(LR=str(controls['learning_rate']),DATA_LINK_FILE=str(fixed_subset))
    names = config_env_names(root / 'config/2-1-0.py') | set(env)
    saved = {key: os.environ.get(key) for key in names}
    try:
        for key in names:
            os.environ.pop(key, None)
        os.environ.update(env)
        cfg = Config.fromfile(str(root / 'config/2-1-0.py'), lazy_import=False)
        cfg.warmup_iters = 1
        cfg.warmup_epochs = 0.0
        cfg.warmup_lr_init = 1e-6
        cfg.min_lr_ratio = 0.1
        cfg.print_freq = 1
        if loader_workers is not None:
            cfg.train_loader.update(num_workers=loader_workers,persistent_workers=loader_workers>0,
                                    prefetch_factor=2 if loader_workers else 1)
            cfg.val_loader.update(num_workers=0,persistent_workers=False,prefetch_factor=1)
        for part in ('train_dataset_config', 'val_dataset_config'):
            if cfg[part]['num_frames'] != 1 or not cfg[part]['single_frame_mode']:
                raise ValueError('single_frame_configuration_required')
        if cfg.max_epochs != epochs or cfg.train_loader['batch_size'] != 1 or cfg.val_loader['batch_size'] != 1:
            raise ValueError('bounded_loader_configuration_required')
        config = out / 'minimal.py'
        cfg.dump(str(config))
        loaded = Config.fromfile(str(config), lazy_import=False)
        if loaded.to_dict() != cfg.to_dict():
            raise ValueError('resolved_config_roundtrip_mismatch')
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    argv = ['python', str(root / 'train.py'), '--py-config', str(config),
            '--work-dir', str(out / 'train'), '--amp', '--load-from', str(args.baseline),
            '--seed', '42', '--gradient-accumulation', '1']
    if controls is not None and not controls['amp']:
        if loaded.get('amp',False): raise ValueError('config_forces_amp')
        argv.remove('--amp')
    report = {'status': 'prepared', 'scope': 'standalone_local_config_preflight',
              'source_commit': args.expected_commit, 'source_url': args.expected_source_url,
              'source_index_digest': digest(args.source_index), 'subset_digest': digest(subset),
              'resolved_config_digest': digest(config), 'mmengine_version': mmengine_version,
              'epochs': epochs, 'batch_size': 1, 'train_frames': args.train_frames,
              'val_frames': args.val_frames, 'train_scenes': len(data['joint_train']),
              'val_scenes': len(data['joint_eval']), 'warmup_iters': 1,
              'environment': env, 'training_argv': argv,
              'baseline_file_exists': args.baseline.is_file(), 'gpu_started': False,
              'model_run_completed': False, 's13_emitted': False, 's13_accepted': False}
    if loader_workers is not None:
        report['loader_configuration']={'train':dict(loaded.train_loader),'validation':dict(loaded.val_loader)}
    if controls is not None:
        if float(loaded.optimizer['optimizer']['lr'])!=controls['learning_rate']:
            raise ValueError('loop_parameter_not_effective')
        fixed_config=deepcopy(loaded.to_dict())
        fixed_config.pop('OPT_LR',None)
        fixed_config['optimizer']['optimizer']['lr']='loop-controlled-learning-rate'
        report.update(loop_parameters=controls,loop_parameters_digest=value_digest(controls),
                      fixed_config_digest=value_digest(fixed_config),fixed_subset_path=str(fixed_subset),
                      evaluator_source_digest=digest(root/'eval.py'))
    (out / 'preflight.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('repo-root', 'source-index', 'dataset-root', 'baseline', 'output-root'):
        parser.add_argument('--' + key, type=Path, required=True)
    for key in ('expected-commit', 'expected-source-url'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--train-frames', type=int, default=8)
    parser.add_argument('--val-frames', type=int, default=2)
    parser.add_argument('--epochs',type=int,default=1)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args), sort_keys=True))
        return 0
    except (ValueError, KeyError, OSError, subprocess.SubprocessError, ImportError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': type(exc).__name__,
                          'gpu_started': False, 's13_emitted': False}))
        return 3


if __name__ == '__main__':
    sys.exit(main())
