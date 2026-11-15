from pathlib import Path
import hashlib
import sys
import pytest
sys.path.insert(0, str(Path(__file__).parents[1]))
from project_driver import resolve


def test_changed_adapter_cannot_execute_under_old_binding(tmp_path):
    path = tmp_path / 'driver.py'
    path.write_text('print("fixture")')
    config = {'provider': 'project-fixture', 'project_driver': {
        'path': str(path), 'digest': 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()}}
    assert resolve(config, tmp_path) == path
    path.write_text('print("changed")')
    with pytest.raises(ValueError, match='project_driver_changed'):
        resolve(config, tmp_path)


def test_old_run_uses_original_installed_driver(tmp_path):
    path = tmp_path / '.agents/skills/lpai-s12-platform/scripts/roadmodel_task_driver.py'
    path.parent.mkdir(parents=True)
    path.write_text('# old immutable driver')
    assert resolve({'provider': 'roadmodel-devspace-v1'}, tmp_path) == path
    with pytest.raises(ValueError, match='binding_required'):
        resolve({'provider': 'other-project'}, tmp_path)
