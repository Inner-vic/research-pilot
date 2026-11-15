"""Bounded helpers for pinned immutable inputs and fixed-size DDP launches.

No platform calls or training on import. A dataset identity is an explicit
immutability contract, not a claim inferred from the presence of a directory.
"""
from collections import OrderedDict
import json
import os
from pathlib import Path
import time


def validate_native_payload(data, limit=60000):
    """Conservative local deployment bound, not a documented platform limit.

    Count every role command and the top-level command, not only one payload.
    """
    size = len(json.dumps(data, ensure_ascii=True).encode('utf-8'))
    if size > limit:
        raise ValueError('native_component_payload_too_large:' + str(size))
    return size


class FrozenPathResolver:
    def __init__(self, resolver, asset_identity, max_entries=32768):
        if not asset_identity or max_entries < 1:
            raise ValueError('frozen_asset_identity_and_positive_bound_required')
        self.resolver = resolver
        self.asset_identity = asset_identity
        self.max_entries = max_entries
        self.cache = OrderedDict()

    def __call__(self, file_ref, primary_root, fallback_subdir=None, search_roots=None):
        # Preserve the original absolute/empty-reference behavior without I/O.
        if not file_ref or os.path.isabs(str(file_ref)):
            return self.resolver(file_ref, primary_root, fallback_subdir, search_roots)
        roots = tuple(search_roots or ())
        key = (self.asset_identity, str(file_ref), primary_root, fallback_subdir, roots)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        result = self.resolver(file_ref, primary_root, fallback_subdir, list(roots))
        # Never cache missing candidates: late materialization must be retried.
        if result is not None and os.path.exists(result):
            self.cache[key] = result
            if len(self.cache) > self.max_entries:
                self.cache.popitem(last=False)
        return result


def topology(env, expected_nodes, expected_gpus=8, max_cards=40):
    nodes, gpus, rank = (int(env[k]) for k in ('NODE_NUM', 'GPU_NUM', 'RANK'))
    if nodes != expected_nodes or gpus != expected_gpus or not 0 <= rank < nodes:
        raise ValueError('platform_topology_mismatch')
    if nodes * gpus > max_cards:
        raise ValueError('authorized_gpu_cap_exceeded')
    address, port = env['MASTER_ADDR'], int(env['MASTER_PORT'])
    if not address or not 1 <= port <= 65535:
        raise ValueError('invalid_rendezvous')
    if nodes > 1 and address in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('multinode_loopback_rendezvous')
    return nodes, gpus, rank


def abort_reason(root):
    paths = sorted(Path(root).glob('abort-node-*.json'))
    return json.loads(paths[0].read_text()) if paths else None


def atomic_record(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp-' + str(os.getpid()))
    with temporary.open('x') as stream:
        json.dump(value, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def wait_ready(root, nodes, expected, timeout=2700, poll=2):
    """Bound initialization only; queue and normal training have no deadline."""
    root = Path(root)
    deadline = time.monotonic() + timeout
    while True:
        if abort_reason(root):
            raise RuntimeError('peer_initialization_failed')
        rows = []
        for rank in range(nodes):
            path = root / ('ready-node-' + str(rank) + '.json')
            if path.exists():
                row = json.loads(path.read_text())
                if row.get('node_rank') != rank or row.get('contract') != expected:
                    raise ValueError('peer_preflight_contract_mismatch')
                rows.append(row)
        if len(rows) == nodes:
            return rows
        if time.monotonic() >= deadline:
            raise TimeoutError('peer_preflight_timeout')
        time.sleep(poll)
