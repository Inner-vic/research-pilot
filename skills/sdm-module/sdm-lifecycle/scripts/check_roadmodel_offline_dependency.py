"""Actual offline pip installation and exact launcher binding regression."""
import argparse
import ast
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
from unittest.mock import patch
import urllib.request

from roadmodel_vision_stage_check import check_stage


def check(directory, parent):
    runtime = directory / 'dinov3/runtime'
    spec = importlib.util.spec_from_file_location('tested_offline_prepare', runtime / 'roadmodel_vision_prepare.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = Path(tempfile.mkdtemp(prefix='offline-check-', dir=directory))
    model = root / 'model'
    model.mkdir()
    os.link(parent / 'dinov3-vitb16.bin', model / 'pytorch_model.bin')
    launch = (runtime / 'normal_training_a800_fixed.py').read_text(encoding='utf-8')
    tree = ast.parse(launch)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
             and isinstance(n.value.func, ast.Name) and n.value.func.id == 'prepare']
    assert len(calls) == 1 and len(calls[0].value.args) == 3
    statement = compile(ast.fix_missing_locations(ast.Module(body=calls, type_ignores=[])), 'actual-prepare-binding', 'exec')
    positive = []
    original_env = os.environ.copy()
    try:
        os.environ.update(HTTP_PROXY='http://127.0.0.1:9', HTTPS_PROXY='http://127.0.0.1:9',
                          PIP_INDEX_URL='http://127.0.0.1:9/unavailable', PIP_NO_INDEX='1',
                          LPAI_INPUT_MODEL_5=str(model))
        for layout in ('asset-roundtrip', 'dependency'):
            os.environ['LPAI_INPUT_MODEL_6'] = str(directory / layout)
            namespace = {'prepare': module.prepare, 'pathlib': __import__('pathlib'),
                         'tempfile': tempfile, 'os': os}
            with patch.object(urllib.request, 'urlopen', side_effect=AssertionError('network_forbidden')), \
                 patch.object(socket, 'create_connection', side_effect=AssertionError('network_forbidden')):
                exec(statement, namespace)
            vendor = Path(namespace['vendor'])
            sys.path.insert(0, str(vendor))
            try:
                installed = importlib.metadata.distribution('timm')
                assert installed.version == '1.0.22'
                assert Path(installed.locate_file('')).resolve() == vendor.resolve()
            finally:
                sys.path.pop(0)
            assert Path(namespace['weight_path']) == (model / 'pytorch_model.bin').resolve()
            positive.append({'layout': layout, 'pip_installed_offline': True, 'version': installed.version})
        rejected = []
        for label in ('missing', 'corrupt', 'ambiguous'):
            bad = root / label
            bad.mkdir()
            if label == 'corrupt':
                # Same byte count but altered content exercises SHA validation.
                content = bytearray((directory / 'dependency' / module.WHEEL_NAME).read_bytes())
                content[100] ^= 1
                (bad / module.WHEEL_NAME).write_bytes(content)
            if label == 'ambiguous':
                (bad / 'dependency').mkdir()
                for dest in (bad / module.WHEEL_NAME, bad / 'dependency' / module.WHEEL_NAME):
                    shutil.copyfile(directory / 'dependency' / module.WHEEL_NAME, dest)
            with patch.object(module.subprocess, 'run') as pip:
                try:
                    module.prepare(root / ('output-' + label), model / 'pytorch_model.bin', bad)
                except (ValueError, FileNotFoundError):
                    pass
                else:
                    raise AssertionError('bad_wheel_accepted:' + label)
                assert not pip.called
            rejected.append({'case': label, 'rejected_before_pip': True})
    finally:
        os.environ.clear()
        os.environ.update(original_env)
    parent_runtime = parent / 'dinov3/runtime'
    unchanged = [p.name for p in runtime.glob('*.py') if p.read_text(encoding='utf-8') ==
                 (parent_runtime / p.name).read_text(encoding='utf-8')]
    assert len(unchanged) == 10
    component = json.loads((directory / 'dinov3/component-config.json').read_bytes())
    dependencies = [m for m in component['input_model'] if m['env_key'] == 'LPAI_INPUT_MODEL_6']
    assert len(dependencies) == 1 and dependencies[0]['model_files'].endswith('/dependency/' + module.WHEEL_NAME)
    stage = check_stage(directory)
    result = {'passed': True, 'actual_offline_install': positive, 'fail_closed': rejected,
        'actual_generated_launcher_binding_executed': True, 'network_disabled_during_prepare': True,
        'pip_subprocess_network_policy': '--isolated --no-index --no-deps --no-cache-dir --disable-pip-version-check; unusable proxies configured',
        'unchanged_runtime_files': unchanged, 'stage_runner': stage,
        'model_test_reuse': {'origin': str(parent / 'tests.json'),
            'scope': 'DINO architecture/forward/freeze/optimizer/metrics unchanged; prior real CPU pretrained-model tests remain applicable. No new GPU execution claimed.'},
        'gpu_executed': False}
    (directory / 'tests.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--parent', type=Path, required=True)
    args = parser.parse_args()
    check(args.directory.resolve(), args.parent.resolve())
