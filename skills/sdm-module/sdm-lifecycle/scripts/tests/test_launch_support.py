import importlib.util
import json
import os
from pathlib import Path
import pytest
from roadmodel_launch_support import FrozenPathResolver, topology, wait_ready, atomic_record, abort_reason


def test_payload_bound_counts_every_role():
    from roadmodel_launch_support import validate_native_payload
    small={'cmd':'x'*20000,'roles':[{'cmd':'x'*20000}]}
    assert validate_native_payload(small)<60000
    with pytest.raises(ValueError):validate_native_payload({**small,'roles':[{'cmd':'x'*20000}]*2})


def test_positive_cache_keeps_original_priority_and_no_negative_cache(tmp_path):
    a, b = tmp_path/'a', tmp_path/'b'
    a.mkdir(); b.mkdir()
    calls = []
    def resolve(ref, primary, sub=None, roots=None):
        calls.append(ref)
        paths = [Path(r)/ref for r in (roots or [])] + [Path(primary)/ref]
        return str(next((p for p in paths if p.exists()), paths[0]))
    cached = FrozenPathResolver(resolve, 'dataset-version-and-index-sha')
    (a/'same').write_text('A'); (b/'same').write_text('B')
    assert cached('same', str(a), search_roots=[str(b)]) == str(b/'same')
    assert cached('same', str(a), search_roots=[str(b)]) == str(b/'same')
    assert len(calls) == 1
    assert cached('same', str(a), search_roots=[]) == str(a/'same')
    cached('late', str(a), search_roots=[str(b)])
    (a/'late').write_text('late')
    assert cached('late', str(a), search_roots=[str(b)]) == str(a/'late')
    assert calls.count('late') == 2


def test_bounded_cache_and_identity(tmp_path):
    def resolver(ref, *_): return str(tmp_path/ref) if ref else None
    with pytest.raises(ValueError): FrozenPathResolver(resolver, '')
    for name in ('one', 'two'): (tmp_path/name).touch()
    cache = FrozenPathResolver(resolver, 'frozen', 1)
    cache('one', 'root'); cache('two', 'root')
    assert len(cache.cache) == 1
    assert cache(None, 'root') is None
    assert FrozenPathResolver(resolver, 'new-version').cache == {}


@pytest.mark.parametrize('nodes', [1, 4, 5])
def test_topology(nodes):
    for rank in range(nodes):
        env = dict(NODE_NUM=str(nodes), GPU_NUM='8', RANK=str(rank), MASTER_ADDR='master', MASTER_PORT='29500')
        assert topology(env,nodes) == (nodes,8,rank)


@pytest.mark.parametrize('change', [dict(RANK='4'), dict(NODE_NUM='1'), dict(GPU_NUM='4'), dict(MASTER_ADDR='localhost'), dict(MASTER_PORT='0')])
def test_bad_topology(change):
    env = dict(NODE_NUM='4', GPU_NUM='8', RANK='0', MASTER_ADDR='master', MASTER_PORT='29500')
    with pytest.raises(ValueError): topology({**env, **change},4)
    with pytest.raises(ValueError): topology({**env,'NODE_NUM':'6'},6)


def test_ready_contract_mismatch_abort_and_missing_rank(tmp_path):
    contract = {'world_size':32,'batch':3,'index':'fixed-sha'}
    for rank in range(4):
        atomic_record(tmp_path/f'ready-node-{rank}.json',{'node_rank':rank,'contract':contract})
    assert len(wait_ready(tmp_path,4,contract,timeout=0)) == 4
    with pytest.raises(ValueError): wait_ready(tmp_path,4,{'world_size':40},timeout=0)
    with pytest.raises(TimeoutError): wait_ready(tmp_path,5,contract,timeout=0)
    atomic_record(tmp_path/'abort-node-2.json',{'reason':'simulated_preflight_failure'})
    assert abort_reason(tmp_path)['reason'] == 'simulated_preflight_failure'
    with pytest.raises(RuntimeError): wait_ready(tmp_path,4,contract,timeout=0)
