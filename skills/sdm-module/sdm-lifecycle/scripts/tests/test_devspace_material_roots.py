"""A retained baseline/comparison must reach the actual review producer."""
import json
from pathlib import Path

import pytest

from devspace_execution import review_material_roots
from project_workspace import execute


def test_explicit_baseline_and_comparison_reach_review(tmp_path):
    paths = [tmp_path / name for name in ('artifacts', 'execution', 'baseline', 'comparison')]
    roles = []
    for index, path in enumerate(paths):
        path.mkdir()
        (path / 'result.json').write_text(json.dumps({'score': index / 10}))
        roles.append({'id': 'r' + str(index), 'root': str(path), 'json_globs': ['result.json'],
                      'json_fields': {'score': {'field': 'score', 'type': 'float'}}})
    material = {'schema': 'sdm-material-preparation/v1', 'objective': 'Compare actual retained results',
                'project_metrics': ['evaluation.score'], 'roles': roles}
    config = {'schema': 'sdm-project-workspace/v1', 'project_id': 'devspace-retained-roles',
              'material_roots': review_material_roots(material, paths[0], paths[1]),
              'material_preparation': material}
    result = execute(config, tmp_path / 'review')
    assert result['status'] == 'awaiting-review', result
    packet = json.loads(Path(result['request_path']).read_bytes())
    assert {row['case_role'] for row in packet['history']} == {'r0', 'r1', 'r2', 'r3'}
    assert len(result['verified_material']) == 4


@pytest.mark.parametrize('value', ['relative', '$unknown', '', None])
def test_invalid_role_roots_fail_before_discovery(tmp_path, value):
    with pytest.raises(ValueError):
        review_material_roots({'roles': [{'root': value}]}, tmp_path / 'a', tmp_path / 'b')


def test_explicit_root_budget_is_preserved(tmp_path):
    with pytest.raises(ValueError, match='bounded_material_root_list_required'):
        review_material_roots({'roles': [{'root': str(tmp_path / str(i))} for i in range(15)]},
                              tmp_path / 'a', tmp_path / 'b')
