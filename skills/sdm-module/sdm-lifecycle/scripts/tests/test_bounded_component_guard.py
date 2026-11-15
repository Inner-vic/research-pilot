"""Real CPU subprocesses on the Linux component execution host; no GPU or platform calls."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from bounded_component_guard import guard


@pytest.mark.skipif(os.name != 'posix', reason='component process-group guard is POSIX-only')
def test_normal_cpu_process_completes_with_no_allocation_release_claim(tmp_path):
    result = guard([sys.executable, '-B', '-c', 'print("CPU fixture")'], time.time() + 5, .2, tmp_path / 'receipt.json')
    assert result['returncode'] == 0 and result['stop_reason'] is None and not result['allocation_release_verified']


@pytest.mark.skipif(os.name != 'posix', reason='component process-group guard is POSIX-only')
def test_deadline_kills_term_ignoring_cpu_process(tmp_path):
    started = time.monotonic()
    result = guard([sys.executable, '-B', '-c', 'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(20)'],
                   time.time() + .4, .2, tmp_path / 'receipt.json')
    assert result['stop_reason'] == 'deadline' and result['returncode'] == -signal.SIGKILL
    assert time.monotonic() - started < 3 and not result['allocation_release_verified']


@pytest.mark.skipif(os.name != 'posix', reason='component process-group guard is POSIX-only')
def test_platform_term_signal_stops_owned_process_group(tmp_path):
    script = Path(__file__).parents[1] / 'bounded_component_guard.py'; receipt = tmp_path / 'receipt.json'
    wrapper = subprocess.Popen([sys.executable, '-B', str(script), '--deadline', str(time.time() + 10),
                               '--terminate-grace', '.2', '--receipt', str(receipt), '--', sys.executable,
                               '-B', '-c', 'import time;time.sleep(20)'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        until = time.monotonic() + 3
        while not receipt.exists() and time.monotonic() < until: time.sleep(.03)
        assert receipt.exists(); wrapper.send_signal(signal.SIGTERM)
        stdout, stderr = wrapper.communicate(timeout=3)
        result = json.loads(receipt.read_bytes())
        assert wrapper.returncode == 2 and result['stop_reason'] == 'external-stop'
        assert not result['allocation_release_verified']
    finally:
        if wrapper.poll() is None: wrapper.kill(); wrapper.wait(timeout=2)


def test_expired_deadline_never_starts_process(tmp_path):
    with pytest.raises(ValueError): guard([sys.executable, '-c', 'raise RuntimeError()'], time.time()-1, .2, tmp_path/'receipt.json')
    assert not (tmp_path/'receipt.json').exists()
