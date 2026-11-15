"""Whole-task bounds and real descendant shutdown; all tests are CPU-only."""
import json
import os
from pathlib import Path
import signal
import sys
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import task_pipeline as pipeline
import run_project_pipeline as entry


def test_fractional_remaining_time_can_start_a_bounded_driver(tmp_path):
    ticks=iter([1000.0]);calls=[]
    def clock():
        return next(ticks,1000.2)
    def driver(config,root,node,budget):
        calls.append((node,budget));return {'status':'completed'}
    task={'objective':'execute-and-learn','wall_time_seconds':1,
          'drivers':{'1':{'argv':['fixture'],'receipt':'unused'}}}
    with patch.object(pipeline.time,'time',side_effect=clock):
        pipeline.coordinate(task,tmp_path,driver)
    assert calls and calls[0][0]=='1'
    assert 0<calls[0][1]<1


def test_wall_budget_stops_dispatch_but_cleanup_has_its_own_budget(tmp_path):
    clock = [1000.0]
    calls = []
    def driver(config, root, node, budget):
        calls.append((node, budget))
        if node == '1':
            clock[0] += 4
        return {'status': 'completed'}
    task = {'objective': 'execute-and-learn', 'timeout_seconds': 10,
            'wall_time_seconds': 3, 'cleanup_timeout_seconds': 7,
            'drivers': {str(n): {'argv': ['fixture'], 'receipt': 'fixture'}
                        for n in pipeline.STEPS},
            'cleanup_driver': {'argv': ['fixture'], 'receipt': 'fixture'}}
    with patch.object(pipeline.time, 'time', side_effect=lambda: clock[0]):
        result = pipeline.coordinate(task, tmp_path, driver)
        deadline = (tmp_path / 'execution-deadline.json').read_bytes()
        first = list(calls)
        pipeline.coordinate(task, tmp_path, driver)
    assert calls == first
    assert [node for node, _ in calls] == ['1']
    assert result['stop_reason'] == 'task_wall_time_exhausted'
    assert result['nodes']['15']['status'] == 'skipped'
    assert result['nodes']['30']['reason'] == 'no_allocation_acquired'
    assert (tmp_path / 'execution-deadline.json').read_bytes() == deadline


@pytest.mark.parametrize('value', [True, 0, -1, '3'])
def test_invalid_wall_budget_has_no_driver_side_effect(tmp_path, value):
    calls = []
    with pytest.raises(ValueError, match='bounded_wall_time_required'):
        pipeline.coordinate({'objective': 'execute-and-learn', 'wall_time_seconds': value},
                            tmp_path / 'run', lambda *args: calls.append(args))
    assert calls == []
    assert not (tmp_path / 'run').exists()


def test_provider_rejects_timeout_config_before_loading_or_launching_driver(tmp_path):
    with patch.object(entry, 'resolve_driver') as resolver:
        with pytest.raises(ValueError, match='configured_node_budget_required'):
            entry.run({'provider': 'isolated-cpu', 'node_timeout_seconds': 0}, tmp_path)
    resolver.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process-group regression')
def test_timeout_kills_real_descendant_and_retains_invocation(tmp_path):
    child_file = tmp_path / 'child.pid'
    driver = tmp_path / 'driver.py'
    driver.write_text('''import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'])
pathlib.Path(sys.argv[1]).write_text(str(child.pid))
print('descendant-started', flush=True)
time.sleep(20)
''')
    started = time.monotonic()
    try:
        result = pipeline.execute({'argv': [sys.executable, '-B', str(driver), str(child_file)],
                                   'receipt': str(tmp_path / 'missing.json'), 'timeout_seconds': 1},
                                  tmp_path, '3', 5)
        assert time.monotonic() - started < 6
        child = int(child_file.read_text())
        stat = Path(f'/proc/{child}/stat')
        for _ in range(20):
            if not stat.exists() or stat.read_text().split(') ')[1][0] in {'Z', 'X'}:
                break
            time.sleep(0.02)
        else:
            pytest.fail('Driver descendant survived timeout')
        assert result['reason'] == 'driver_timeout'
        assert result['uncertain_effect'] is True
        invocation = json.loads((tmp_path / '3-invocation.json').read_bytes())
        assert invocation['timed_out'] is True
        assert invocation['launcher_reaped'] is True
        assert 'descendant-started' in invocation['stdout']
    finally:
        if child_file.exists():
            try:
                os.kill(int(child_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
