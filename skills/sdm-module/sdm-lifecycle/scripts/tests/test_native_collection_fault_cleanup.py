"""Fault injection in original cleanup; generated CPU capsules, no real platform."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import time
import types
from unittest.mock import patch
import pytest
import devspace_execution as executor
import devspace_artifacts as stream
from test_devspace_execution import native_case, pin, action_receipt, UID

@pytest.mark.parametrize('fault', ['manifest', 'checkpoint'])
def test_collection_failure_still_requests_and_verifies_native_stop(tmp_path, fault):
    config, root = native_case(tmp_path)
    plan = config['devspace_execution']['plan']
    local = tmp_path / 'remote'
    plan.update(remote_root='/lpai/control-fixture', artifact_collection_mode='scp-sha256-v1',
                max_artifact_bytes=1000000, max_total_bytes=2000000)
    plan['artifacts'].append('last.pt')
    source = Path(plan['inputs'][0]['path'])
    source.write_text(source.read_text() + "\nPath('last.pt').write_bytes(b'control bytes; not a model')\n")
    plan['inputs'][0]['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    pin(config)
    phase = ['stopped']
    actions = []
    real = executor.transport
    def resource(plan, action):
        assert action == 'get'
        return {'uid': UID, 'phase': phase[0], 'observed_at': time.time()}
    def transport(plan, packet):
        actions.append(packet['action'])
        if fault == 'manifest' and packet['action'] == 'artifact-manifest-v1':
            raise ValueError('artifact_collection_budget_exceeded')
        return real({**plan, 'transport': 'local-cpu', 'remote_root': str(local)}, packet)
    def copy(argv, **kwargs):
        remote = argv[-2].split(':', 1)[1]
        name = PurePosixPath(remote).relative_to(PurePosixPath(plan['remote_root']))
        if fault == 'checkpoint' and name.as_posix() == 'last.pt':
            raise OSError('injected checkpoint transfer failure')
        Path(argv[-1]).write_bytes((local / str(name)).read_bytes())
    fake = types.SimpleNamespace(run=copy, SubprocessError=subprocess.SubprocessError, CREATE_NO_WINDOW=0)
    with patch.object(executor, 'resource_call', side_effect=resource), patch.object(executor, 'transport', side_effect=transport), patch.object(stream, 'subprocess', fake):
        executor.execution(config, root, allow_live=True)
        phase[0] = 'ready'
        executor.execution(config, root, action_receipt(root, 'start'), True)
        until = time.monotonic() + 15
        while True:
            state = executor.execution(config, root, allow_live=True)
            if (root / 'stop-request.json').exists() or time.monotonic() > until: break
            time.sleep(.1)
        assert (root / 'stop-request.json').is_file(), state
        assert not state['stop_verified']
        assert actions.count('launch') == 1
        if fault == 'manifest':
            assert state['artifact_retry_exhausted'] and not state.get('artifacts_collected')
            assert state['artifact_error'] == 'artifact_collection_budget_exceeded'
        else:
            assert (root / 'artifacts/stdout.log').is_file()
            assert 'last.pt' in state['missing_artifacts']
        phase[0] = 'stopped'
        state = executor.execution(config, root, action_receipt(root, 'stop'), True)
        assert state['stop_verified']
        assert actions.count('launch') == 1
        if fault == 'manifest':
            assert state['status'] == 'released-evidence-incomplete'
