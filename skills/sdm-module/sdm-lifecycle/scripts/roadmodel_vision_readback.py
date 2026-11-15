"""Verify retained official readbacks for both exact vision task UIDs; no writes to LPAI."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import lzma
from pathlib import Path


def verify(directory):
    manager = json.loads((directory / 'manager.json').read_bytes())
    if not all(isinstance(manager.get(k),str) and manager[k].strip() for k in ('owner','namespace')):
        raise ValueError('original_manager_identity_required')
    state = json.loads((directory / 'dispatch-state.json').read_bytes())
    arms = {}
    for arm in ('cbb1', 'dinov3'):
        folder = directory / arm
        item = state['arms'][arm]
        task = json.loads((folder / 'task-latest.private.json').read_bytes())
        plan = json.loads((folder / 'plan.json').read_bytes())
        body = json.loads((folder / 'create-body.json').read_bytes())
        if body['namespace'] != manager['namespace'] or body.get('owner',manager['owner']) != manager['owner']:
            raise ValueError('original_manager_body_identity_changed')
        assert task['uid'] == item['task_uid'] and task['project_uid'] == item['component_uid']
        assert task['owner'] == manager['owner'] and task['namespace'] == manager['namespace']
        assert task['max_retry'] == 0 and not task['retry']
        data = task['data']
        values = [e['value'] for e in data['lpai_envs'] if e['key'] == 'A800_recovery_packed']
        assert len(values) == 1
        raw = lzma.decompress(base64.b64decode(values[0], validate=True))
        assert hashlib.sha256(raw).hexdigest() == plan['payload_sha256']
        files = json.loads(raw)
        assert len(files) == 13 and set(files) == set(plan['runtime_sha256'])
        for name, text in files.items():
            assert hashlib.sha256(text.encode()).hexdigest() == plan['runtime_sha256'][name]
            assert text == (folder / 'runtime' / name).read_text(encoding='utf-8')
        assert "EXPERIMENT = '" + plan['name'] + "'" in files['run_profile.py']
        assert data['lpai_git'][0]['commit_id'] == plan['source_commit']
        for key in ('lpai_model_download', 'lpai_input_dataset', 'lpai_input_pvc'):
            assert len(data[key]) == len(body['data'][key])
            for actual, expected in zip(data[key], body['data'][key]):
                assert all(actual.get(k) == v for k, v in expected.items()), key
        roles = data['lpai_task']['roles']
        assert [(r['role'], r['replica'], r['resource']['Gpu']) for r in roles] == [('master', 1, 8), ('worker', 3, 8)]
        for role in roles:
            resource = role['resource']
            assert (resource['Cpu'], resource['Mem'], resource['Network']) == (104, 872, 3)
            assert resource['GpuType'] == 'nvidia.com/NVIDIA-A800-SXM4-80GB-RDMA-1T'
            assert role['cmd'] == body['data']['lpai_task']['cmd']
            assert role['image_info']['image'] == 'reg-ai.chehejia.com/vc-chassis/gaussianformer1:2.2'
        pods = json.loads((folder / 'pods.json').read_bytes())
        arms[arm] = {'task_uid': task['uid'], 'component_uid': task['project_uid'], 'status': task['status'],
            'verified': True, 'runtime_files_verified': len(files), 'total_gpus': 32,
            'epochs': 80, 'batch_per_gpu': 8, 'accumulation': 1, 'nominal_effective_batch': 256,
            'outcome_path': plan['outcome_path'], 'payload_sha256': plan['payload_sha256'],
            'local_source_comparison': 'UTF8 read_text normalizes Windows CRLF; deployed source and manifest use LF',
            'pods': [{'name': p['name'], 'step': p.get('step'), 'status': p['status'], 'reason': p.get('reason')} for p in pods]}
    assert arms['cbb1']['task_uid'] != arms['dinov3']['task_uid']
    assert arms['cbb1']['component_uid'] != arms['dinov3']['component_uid']
    assert arms['cbb1']['outcome_path'] != arms['dinov3']['outcome_path']
    result = {'schema': 'roadmodel-vision-submission-readback/v1', 'verified': True,
        'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'arms': arms,
        'peak_requested_gpus': 64, 'training_completed': False,
        'gpu_fit_verified': False, 'scope': 'Actual task configuration and current pod status; model training acceptance remains in-task evidence'}
    (directory / 'submission-readback-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--directory', type=Path, required=True)
    a = p.parse_args()
    verify(a.directory.resolve())
