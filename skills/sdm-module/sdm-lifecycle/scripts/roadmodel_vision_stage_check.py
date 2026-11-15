"""Exercise the packaged stage runner and orchestration with real small children.

CUDA and Linux OwnedTree are not simulated as qualified. The Windows fixture
only replaces child creation/containment, while executing the unchanged runner
and exact preflight-to-training statements from the generated launcher.
"""
import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def exercise(launcher, directory, fail=False, duplicate=False):
    tree = ast.parse(launcher)
    runner = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
    def stage(statement):
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            return None
        call = statement.value
        if not isinstance(call.func, ast.Name) or call.func.id != 'run' or not call.args:
            return None
        value = call.args[-1]
        return value.value if isinstance(value, ast.Constant) else None
    block = next(n.body for n in ast.walk(tree) if isinstance(n, ast.Try)
                 and any(stage(s) == 'dino-gpu-preflight' for s in n.body))
    first = next(i for i, s in enumerate(block) if stage(s) == 'dino-gpu-preflight')
    last = next(i for i, s in enumerate(block) if stage(s) == 'training')
    statements = block[first:last + 1]
    if duplicate:
        statements = [statements[0], *statements]
    code = compile(ast.fix_missing_locations(ast.Module(body=[runner], type_ignores=[])), 'actual-runner', 'exec')
    sequence = compile(ast.fix_missing_locations(ast.Module(body=statements, type_ignores=[])), 'actual-stage-sequence', 'exec')
    directory.mkdir()
    output = directory / 'node-0'
    output.mkdir()
    checkpoint = directory / 'fixture-checkpoint'
    checkpoint.write_text('fixture only')
    launches, cleaned, processes, events = [], [], [], []
    receipt, boundary = output / 'dino-gpu-preflight.json', directory / 'training-boundary.json'
    def launch(argv, source, env):
        kind = 'training' if '-m' in argv else 'dino-gpu-preflight'
        launches.append(kind)
        target = boundary if kind == 'training' else receipt
        child = subprocess.Popen([sys.executable, '-B', '-c',
            "import json,pathlib,sys;pathlib.Path(sys.argv[1]).write_text(json.dumps({'fixture':True,'gpu_executed':False}));print('LIGHTWEIGHT_REAL_CHILD',flush=True);sys.exit(int(sys.argv[2]))",
            str(target), str(17 if fail and kind != 'training' else 0)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        processes.append(child)
        return child
    def cleanup(child):
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=10)
        cleaned.append(child.pid)
        return {'verified': True, 'scope': 'actual Windows fixture child only; not Linux ownership qualification'}
    def atomic(path, value):
        Path(path).write_text(json.dumps(value))
    namespace = dict(RUN=directory, OUT=output, WATCHDOG=None, STAGE_LOG_BYTES=1024 ** 2,
        abort_reason=lambda _: None, event=lambda name, **kw: events.append((name, kw)),
        launch_owned=launch, terminate_group=cleanup, child=None, LAST_CLEANUP=None,
        training_started=False, oom_observed=False, nonfinite_warning_count=0,
        WORLD=32, BATCH_PER_GPU=8, ACCUMULATION=1, QUALIFICATION=False,
        sys=sys, pathlib=__import__('pathlib'), __file__=str(directory / 'packaged-launcher.py'),
        source=directory, effective=directory / 'fixture-effective-config.py',
        env={'MASTER_ADDR': 'fixture.invalid', 'MASTER_PORT': '12345'},
        checkpoint=checkpoint, sha=lambda _: 'fixture-sha-not-model-evidence', NODE_RANK=0,
        NODES=4, GPUS=8, EXPERIMENT='fixture-stage-progression', SOURCE_COMMIT='fixture', INDEX_SHA='fixture',
        END_EPOCHS=80, SCHEDULER_EPOCHS=80, ENV={'LR': '2e-5'}, atomic_record=atomic,
        wait_ready=lambda *args: None, json=json)
    caught = None
    try:
        exec(code, namespace)
        exec(sequence, namespace)
    except Exception as error:
        caught = error
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
    if duplicate:
        assert isinstance(caught, FileExistsError), repr(caught)
        assert launches == ['dino-gpu-preflight', 'dino-gpu-preflight'] and not boundary.exists()
        return {'negative_control_duplicate_reproduces_log_collision': True, 'all_fixture_children_reaped': True}
    if fail:
        assert isinstance(caught, RuntimeError) and 'subprocess_failed:dino-gpu-preflight:17' in str(caught), repr(caught)
        assert launches == ['dino-gpu-preflight'] and not boundary.exists()
        assert len(cleaned) == 1 and all(p.poll() is not None for p in processes)
        assert not (directory / 'ready-node-0.json').exists()
    else:
        assert caught is None, repr(caught)
        assert launches == ['dino-gpu-preflight', 'training']
        assert receipt.exists() and boundary.exists() and (directory / 'ready-node-0.json').exists()
        assert len(list(output.glob('dino-gpu-preflight.log'))) == 1 and len(cleaned) == 2
    return {'preflight_launches': launches.count('dino-gpu-preflight'), 'training_boundary_reached': boundary.exists(),
            'preflight_log_count': len(list(output.glob('dino-gpu-preflight.log'))),
            'preflight_receipt_exists': receipt.exists(), 'failed_preflight_stops_before_ready_and_training': fail,
            'all_fixture_children_reaped': all(p.poll() is not None for p in processes),
            'cleanup_calls': len(cleaned), 'gpu_executed': False}


def check_stage(directory):
    directory = Path(directory)
    launcher = (directory / 'dinov3/runtime/normal_training_a800_fixed.py').read_text(encoding='utf-8')
    root = Path(tempfile.mkdtemp(prefix='stage-regression-', dir=directory))
    result = {'passed': True, 'success': exercise(launcher, root / 'success'),
              'preflight_failure': exercise(launcher, root / 'failure', fail=True),
              'negative_control': exercise(launcher, root / 'negative', duplicate=True),
              'scope': 'exact packaged runner and preflight-to-training orchestration with real lightweight Windows children; no CUDA/NCCL/Linux OwnedTree qualification'}
    (directory / 'stage-tests.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result
