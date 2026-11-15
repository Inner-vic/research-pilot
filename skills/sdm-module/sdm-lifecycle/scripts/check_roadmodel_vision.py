"""Real CPU tests of pretrained loading, geometry, nested freeze and gradients."""
import argparse
import ast
import base64
import hashlib
import importlib.util
import json
import lzma
import os
from pathlib import Path
import sys


def run(directory, source):
    print('CPU_VISION_CHECK importing_torch', flush=True)
    import torch
    print('CPU_VISION_CHECK torch_imported', flush=True)
    from mmengine.config import Config
    from roadmodel_vision_backbone import DINOv3ImageBackbone, fixed_eight_score
    torch.set_num_threads(4)
    results = []
    checkpoint = directory / 'dinov3-vitb16.bin'
    print('CPU_VISION_CHECK loading_pretrained_encoder', flush=True)
    model = DINOv3ImageBackbone(str(checkpoint))
    print('CPU_VISION_CHECK pretrained_encoder_loaded', flush=True)
    model.requires_grad_(True)
    model.train(True)  # Mirrors BEVSegmentor's unfreeze -> train order.
    assert not model.encoder.training
    assert all(not p.requires_grad for p in model.encoder.parameters())
    assert all(p.requires_grad for p in model.projections.parameters())
    images = torch.randn(2, 3, 64, 96)
    outputs = model(images)
    assert [tuple(t.shape) for t in outputs] == [(2, 128, 8, 12), (2, 128, 4, 6), (2, 128, 2, 3)]
    loss = sum(t.float().square().mean() for t in outputs)
    loss.backward()
    assert all(p.grad is None for p in model.encoder.parameters())
    assert all(p.grad is not None and bool(torch.isfinite(p.grad).all()) for p in model.projections.parameters())
    assert any(bool((p.grad != 0).any()) for p in model.projections.parameters())
    results.append({'name': 'real_pretrained_dense_forward_backward_and_nested_freeze', 'passed': True,
                    'loaded_tensors': model.pretrained_tensor_count, 'input_shape': list(images.shape),
                    'output_shapes': [list(t.shape) for t in outputs], 'cuda_executed': False})
    surface = {'class_indices': list(range(27)), 'total_seen': [10.] * 27,
               'total_correct': [5.] * 27, 'total_positive': [10.] * 27,
               'miou': 30., 'drivable_height_mae_cm': .6, 'drivable_count': 100}
    assert abs(fixed_eight_score(surface) - 100 / 3) < 1e-8
    surface['total_positive'][11] = 1e6
    assert abs(fixed_eight_score(surface) - 100 / 3) < 1e-8
    results.append({'name': 'fixed8_denominator_resists_non_target_false_positive_class_entry', 'passed': True})
    train = (source / 'train.py').read_text(encoding='utf-8')
    train = train.replace('\r\n', '\n')
    assert hashlib.sha256(train.encode()).hexdigest() == 'a879b980901dba38f4622d5f39f30b00030c5a486388750ba480669a4963db5b'
    for arm in ('cbb1', 'dinov3'):
        folder = directory / arm
        runtime = folder / 'runtime'
        component = json.loads((folder / 'component-config.json').read_bytes())
        body = json.loads((folder / 'create-body.json').read_bytes())
        files = json.loads(lzma.decompress(base64.b64decode(component['envs']['A800_recovery_packed'])))
        assert files == json.loads(lzma.decompress(base64.b64decode(body['data']['lpai_envs'][0]['value'])))
        for filename, text in files.items():
            assert (runtime / filename).read_text(encoding='utf-8') == text
            compile(text, filename, 'exec')
        adapter_tree = ast.parse(files['roadmodel_vision_backbone.py'])
        assert sum(isinstance(n, ast.FunctionDef) and n.name == 'gpu_preflight' for n in adapter_tree.body) == 1
        launch_tree = ast.parse(files['normal_training_a800_fixed.py'])
        probes = [n for n in ast.walk(launch_tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == 'run' and n.args and isinstance(n.args[-1], ast.Constant)
                  and n.args[-1].value == 'dino-gpu-preflight']
        assert len(probes) == (1 if arm == 'dinov3' else 0), 'one_owned_preflight_log_per_node'
        # Exercise the exact generated entry replacement against actual pinned
        # train.py, rather than only testing syntactically valid wrapper strings.
        tree = ast.parse(files['fresh_entry.py'])
        substitutions = {}
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name) and statement.targets[0].id in {'old', 'new'}:
                substitutions[statement.targets[0].id] = ast.literal_eval(statement.value)
        assert train.count(substitutions['old']) == 1
        rewritten = train.replace(substitutions['old'], substitutions['new'])
        compile(rewritten, 'actual-selector-overlay.py', 'exec')
        spec = importlib.util.spec_from_file_location('tested_fresh_optimization', runtime / 'fresh_optimization.py')
        sys.path.insert(0, str(runtime))
        try:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            compile(module.instrument(rewritten), 'actual-accumulation-overlay.py', 'exec')
        finally:
            sys.path.pop(0)
        materialize_tree = ast.parse(files['normal_training_a800_fixed.py'])
        materialize = next(ast.literal_eval(n.value) for n in ast.walk(materialize_tree)
                           if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'materialize' for t in n.targets)
                           and isinstance(n.value, ast.Constant))
        compile(materialize, 'materialize-config', 'exec')
        assert "['lr_mult']=1.0" in materialize
        assert ('DINOv3ImageBackbone' in materialize) == (arm == 'dinov3')
        old_env, old_argv = os.environ.copy(), sys.argv[:]
        try:
            os.environ.update(BATCH_SIZE='8', NUM_WORKERS='4', PREFETCH_FACTOR='1', MAX_EPOCH='80',
                              LR='2e-5', WARMUP_EPOCHS='3', FRESH_BACKBONE=str(checkpoint))
            effective_path = folder / 'cpu-effective-config.py'
            sys.argv = ['materialize', str(source / 'config/2-1-0.py'), str(effective_path)]
            exec(compile(materialize, 'materialize-config', 'exec'), {})
            effective = Config.fromfile(str(effective_path), lazy_import=False)
            assert effective.optimizer.optimizer.lr == 2e-5
            assert effective.max_epochs == 80 and effective.train_mode == 'occ'
            assert effective.model.img_backbone.type == ('DINOv3ImageBackbone' if arm == 'dinov3' else 'CBB1ImageBackbone')
        finally:
            os.environ.clear()
            os.environ.update(old_env)
            sys.argv = old_argv
        roles = body['data']['lpai_task']['roles']
        assert [(r['replica'], r['resource']['Gpu']) for r in roles] == [(1, 8), (3, 8)]
        assert component['retry'] == 0 and '172800s' in component['cmd']
        results.append({'name': arm + '_compiled_payload_pinned_source_and_materialization', 'passed': True})
    from roadmodel_vision_stage_check import check_stage
    stage_result = check_stage(directory)
    results.append({'name': 'actual_stage_runner_success_failure_and_duplicate_negative_control', **stage_result})
    result = {'passed': True, 'tests': results, 'evidence_scope': 'real CPU model tests plus exact generated payload/source checks and actual runner with real lightweight children; no GPU fit or NCCL qualification',
              'torch': torch.__version__, 'cuda_available': torch.cuda.is_available()}
    (directory / 'tests.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--source', type=Path, required=True)
    a = p.parse_args()
    run(a.directory.resolve(), a.source.resolve())
