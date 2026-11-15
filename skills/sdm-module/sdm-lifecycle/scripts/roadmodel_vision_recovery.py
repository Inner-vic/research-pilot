"""Prepare and admit one user-requested replacement; preserve its existing peer."""
import argparse
import base64
import contextlib
import copy
import hashlib
import io
import json
import lzma
from pathlib import Path
import shutil

from lpai_manager import Manager, digest_file
from review_storage import atomic_json, locked
from roadmodel_vision_dispatch import verify_frozen
from roadmodel_vision_plan import replace, outcome_sibling
from roadmodel_vision_prepare import WHEEL_NAME


def prepare(parent, directory, arm, asset):
    if arm != 'dinov3':
        raise ValueError('offline_dependency_repair_is_dinov3_only')
    if (directory / arm / 'component-config.json').exists():
        raise ValueError('prepared_attempt_immutable')
    other = 'cbb1'
    oldstate = json.loads((parent / 'dispatch-state.json').read_bytes())
    metadata = json.loads(asset.read_bytes())
    if metadata['metadata']['labels']['status'] != 'AVAILABLE':
        raise ValueError('offline_asset_not_available')
    if not any(p['clusterName'] == 'cnhb01-algorithm-bidu-p-03' and p['status'] == 'AVAILABLE'
               and p['fsType'] == 'ofs' for p in metadata['metadata']['preHeats']):
        raise ValueError('offline_asset_target_unavailable')
    if metadata['metadata']['pName'] != 'autoopt-vision-runtime':
        raise ValueError('unexpected_dependency_asset')
    if metadata['metadata']['annotations']['number'] != '1' or metadata['metadata']['annotations']['size'] != '2530238':
        raise ValueError('dependency_asset_contents_mismatch')
    config = json.loads((parent / 'manager.json').read_bytes())
    config['state_root'] = str(directory / 'state')
    atomic_json(directory / 'manager.json', config)
    # The healthy arm is a reference, not another submission candidate. Its
    # verified UID is imported only after a fresh exact-task read in initialize.
    (directory / other).mkdir(exist_ok=True)
    for name in ('plan.json', 'component-config.json', 'create-body.json'):
        shutil.copyfile(parent / other / name, directory / other / name)
    shutil.copytree(parent / other / 'runtime', directory / other / 'runtime')
    folder = directory / arm
    shutil.copytree(parent / arm / 'runtime', folder / 'runtime')
    runtime = folder / 'runtime'
    oldplan = json.loads((parent / arm / 'plan.json').read_bytes())
    name = 'autoopt-vision-dinov3-off1-80e-32a800-20261102'
    profile = (runtime / 'run_profile.py').read_text(encoding='utf-8')
    profile = replace(profile, 'EXPERIMENT = ' + repr(oldplan['name']), 'EXPERIMENT = ' + repr(name))
    (runtime / 'run_profile.py').write_text(profile, encoding='utf-8', newline='\n')
    shutil.copyfile(Path(__file__).with_name('roadmodel_vision_prepare.py'), runtime / 'roadmodel_vision_prepare.py')
    launch = (runtime / 'normal_training_a800_fixed.py').read_text(encoding='utf-8')
    launch = replace(launch,
        "prepare(pathlib.Path(tempfile.mkdtemp(prefix='sdm-vision-inputs-')), pathlib.Path(os.environ['LPAI_INPUT_MODEL_5']) / 'pytorch_model.bin')",
        "prepare(pathlib.Path(tempfile.mkdtemp(prefix='sdm-vision-inputs-')), pathlib.Path(os.environ['LPAI_INPUT_MODEL_5']) / 'pytorch_model.bin', pathlib.Path(os.environ['LPAI_INPUT_MODEL_6']))")
    (runtime / 'normal_training_a800_fixed.py').write_text(launch, encoding='utf-8', newline='\n')
    files = {p.name: p.read_text(encoding='utf-8') for p in sorted(runtime.glob('*.py'))}
    for key, text in files.items():
        compile(text, key, 'exec')
    raw = json.dumps(files, separators=(',', ':'), ensure_ascii=True).encode()
    payload_sha = hashlib.sha256(raw).hexdigest()
    component = json.loads((parent / arm / 'component-config.json').read_bytes())
    component['name'] = name
    component['describe'] = 'DINOv3 offline dependency repair;32A80080epochs batch8acc1global256;48hcap retry0'
    component['cmd'] = replace(component['cmd'], oldplan['payload_sha256'], payload_sha)
    component['envs']['A800_recovery_packed'] = base64.b64encode(lzma.compress(raw)).decode()
    component['input_model'].append({'env_key': 'LPAI_INPUT_MODEL_6',
        'env_value': '/lpai/inputs/models/autoopt-vision-runtime-timm-1-0-22',
        'model_files': metadata['spec']['dir']['path'] + 'dependency/' + WHEEL_NAME,
        'model_set': metadata['metadata']['pName'], 'model_version': metadata['metadata']['name']})
    atomic_json(folder / 'component-config.json', component)
    info = json.loads((parent / arm / 'task-info.json').read_bytes())
    info['name'] = name
    info['solution_approach'] += ' 修复启动阶段外网HTTP403：通过平台TOS输入离线wheel，严格校验后no-index/no-deps安装；不改变科学实验参数。'
    atomic_json(folder / 'task-info.json', info)
    manager = Manager(config)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        manager.backend.sdk()
        plan = manager.plan({'action': 'component.create', 'request_key': name + '-component',
            'name': name, 'transport': 'official-sdk', 'sdk_config': component})
    assert plan['preview']['requested_gpus'] == 32
    atomic_json(folder / 'create-plan.json', plan)
    atomic_json(folder / 'create-body.json', plan['request']['body'])
    updated = {**oldplan, 'name': name, 'payload_sha256': payload_sha,
        'runtime_sha256': {k: hashlib.sha256(v.encode()).hexdigest() for k, v in files.items()},
        'outcome_path': outcome_sibling(oldplan['outcome_path'], name),
        'create_plan_sha256': digest_file(folder / 'create-plan.json'),
        'replaces_failed_task_uid': oldstate['arms'][arm]['task_uid'], 'offline_dependency': component['input_model'][-1]}
    atomic_json(folder / 'plan.json', updated)
    group = json.loads((parent / 'group-plan.json').read_bytes())
    group['arms'][arm] = updated
    group['replacement'] = {'failed_arm': arm, 'failed_task_uid': oldstate['arms'][arm]['task_uid'],
        'healthy_arm': other, 'healthy_task_uid': oldstate['arms'][other]['task_uid'],
        'failed_component_uid': oldstate['arms'][arm]['component_uid'],
        'healthy_component_uid': oldstate['arms'][other]['component_uid'],
        'parent_case': str(parent), 'new_task_allowance': 1,
        'authority': 'Current user explicitly requests analyze failed task and resubmit; no automatic retries'}
    group['arms'][other]['existing_only_no_resubmit'] = True
    atomic_json(directory / 'group-plan.json', group)
    changed = [n for n in files if files[n] != (parent / arm / 'runtime' / n).read_text(encoding='utf-8')]
    assert set(changed) == {'roadmodel_vision_prepare.py', 'normal_training_a800_fixed.py', 'run_profile.py'}
    atomic_json(directory / 'change-manifest.json', {'parent': str(parent), 'changed_runtime_files': changed,
        'other_runtime_files_byte_equivalent_after_LF_normalization': True,
        'scientific_recipe_unchanged': ['source', 'data', 'model', 'loss', 'optimizer', 'epochs', 'batch', 'seed', 'GPUcount'],
        'healthy_arm_untouched': oldstate['arms'][other]['task_uid'], 'new_component_name': name})
    print(json.dumps({'status': 'prepared', 'new_tasks': 1, 'arm': arm, 'gpus': 32, 'changed': changed}))


def initialize(directory):
    config = json.loads((directory / 'manager.json').read_bytes())
    spec = json.loads((directory / 'group-plan.json').read_bytes())['replacement']
    parent = Path(spec['parent_case'])
    previous = json.loads((parent / 'dispatch-state.json').read_bytes())
    manager = Manager(config)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        manager.backend.sdk()
    registry = Path(config['submission_registry'])
    statepath = registry / ('vision-comparison-' + directory.name + '.json')
    with locked(registry, 'a800-e40-engineering-once.lock', 'a800_comparison_dispatch_busy'):
        review = verify_frozen(directory)
        if statepath.exists():
            raise ValueError('replacement_state_exists_reconcile_only')
        failed = manager.checked('task', spec['failed_task_uid'])
        healthy = manager.checked('task', spec['healthy_task_uid'])
        assert failed['status'] == 'failed'
        for arm, task in ((spec['failed_arm'], failed), (spec['healthy_arm'], healthy)):
            assert task['uid'] == previous['arms'][arm]['task_uid']
            assert task['project_uid'] == previous['arms'][arm]['component_uid']
            assert task['owner'] == config['owner'] and task['namespace'] == config['namespace']
        pods = manager.backend.cli(['tasks', 'pods', '--uid', failed['uid']])
        roles = [p for p in pods if p.get('step') == 'task' and p.get('pod_type') == 'user']
        assert len(roles) == 4 and all(p['status'].lower() in {'failed', 'deleted', 'stopped', 'succeeded'} for p in roles)
        assert len({p['name'] for p in roles}) == 4 and all(p.get('task_uid') == failed['uid'] for p in roles)
        assert spec['new_task_allowance'] == 1 and spec['failed_arm'] != spec['healthy_arm']
        assert failed['project_uid'] == spec['failed_component_uid'] and healthy['project_uid'] == spec['healthy_component_uid']
        binding = {'directory': str(directory), 'group_plan_sha256': digest_file(directory / 'group-plan.json'),
            'review_subject_digest': review['subject_digest'], 'failed_arm': spec['failed_arm'],
            'healthy_arm': spec['healthy_arm'], 'failed_task_uid': failed['uid'], 'healthy_task_uid': healthy['uid'],
            'failed_component_uid': failed['project_uid'], 'healthy_component_uid': healthy['project_uid'],
            'new_task_allowance': 1}
        state = {'schema': 'roadmodel-vision-group-dispatch/v1', 'directory': str(directory),
            'authorized_arms': ['cbb1', 'dinov3'], 'peak_gpus': 64,
            'arms': {spec['healthy_arm']: copy.deepcopy(previous['arms'][spec['healthy_arm']]),
                     spec['failed_arm']: {'phase': 'new', 'replaces_failed_task_uid': failed['uid'], 'review': review}}}
        atomic_json(directory / 'replacement-admission.json', {'failed_task_uid': failed['uid'],
            'binding': binding,
            'failed_status': failed['status'], 'all_four_failed_training_roles_terminal': True,
            'healthy_task_uid': healthy['uid'], 'healthy_status': healthy['status'], 'healthy_task_mutated': False,
            'training_role_statuses': [{'name': p['name'], 'status': p['status'], 'task_uid': failed['uid']} for p in roles]})
        state['replacement_admission'] = binding
        state['replacement_admission_sha256'] = digest_file(directory / 'replacement-admission.json')
        atomic_json(statepath, state)
        atomic_json(directory / 'dispatch-state.json', state)
        print(json.dumps({'status': 'initialized', 'new_task_allowance': 1, 'healthy_task_uid': healthy['uid']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--parent', type=Path)
    parser.add_argument('--arm', default='dinov3')
    parser.add_argument('--asset', type=Path)
    parser.add_argument('--initialize', action='store_true')
    args = parser.parse_args()
    if args.initialize:
        initialize(args.directory.resolve())
    else:
        prepare(args.parent.resolve(), args.directory.resolve(), args.arm, args.asset.resolve())
