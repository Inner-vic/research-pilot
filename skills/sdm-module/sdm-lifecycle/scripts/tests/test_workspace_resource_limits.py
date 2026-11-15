from __future__ import annotations

import os
from pathlib import Path

import pytest

import workspace as workspace_module
from workspace import WorkspaceError, snapshot_tree


def _fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


def test_sparse_workspace_file_is_rejected_before_allocation_and_closes_fds(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sparse = workspace / "zz-oversized-sparse.bin"
    with sparse.open("wb") as stream:
        stream.truncate(workspace_module.MAX_TREE_FILE_BYTES + 1)
    before = _fd_count()

    with pytest.raises(WorkspaceError) as caught:
        snapshot_tree(workspace)

    assert caught.value.code == "workspace_resource_limit"
    assert sparse.stat().st_size == workspace_module.MAX_TREE_FILE_BYTES + 1
    assert _fd_count() == before

    # Directory enumeration itself is incremental and reaches the fixed node
    # boundary before building or sorting a complete attacker-sized name list.
    real_scandir = workspace_module.os.scandir
    scans = 0

    def observed_scandir(path):
        nonlocal scans
        scans += 1
        return real_scandir(path)

    monkeypatch.setattr(workspace_module.os, "scandir", observed_scandir)
    monkeypatch.setattr(workspace_module, "MAX_TREE_NODES", 2)
    with pytest.raises(WorkspaceError) as node_cap:
        snapshot_tree(workspace)
    assert node_cap.value.code == "workspace_resource_limit"
    assert scans == 1
    assert _fd_count() == before


def test_cumulative_workspace_byte_cap_is_checked_before_next_read(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = sum(
        path.stat().st_size
        for path in workspace.rglob("*")
        if path.is_file()
    )
    monkeypatch.setattr(
        workspace_module, "MAX_TREE_BYTES", baseline + 2048,
    )
    for index in range(3):
        with (workspace / f"zz-total-{index}.bin").open("wb") as stream:
            stream.truncate(1024)
    before = _fd_count()

    with pytest.raises(WorkspaceError) as caught:
        snapshot_tree(workspace)

    assert caught.value.code == "workspace_resource_limit"
    assert _fd_count() == before


def test_workspace_fd_budget_normalizes_exhaustion_and_releases_fds(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _fd_count()
    _, hard_limit = workspace_module.resource.getrlimit(
        workspace_module.resource.RLIMIT_NOFILE,
    )
    monkeypatch.setattr(
        workspace_module.resource,
        "getrlimit",
        lambda _resource: (before + 6, hard_limit),
    )

    with pytest.raises(WorkspaceError) as caught:
        snapshot_tree(workspace)

    assert caught.value.code == "workspace_resource_limit"
    assert _fd_count() == before


def test_workspace_mount_identity_is_fail_closed_without_fdinfo(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = workspace_module.os.open
    before = _fd_count()

    def fail_fdinfo(path, flags, *args, **kwargs):
        if os.fspath(path).startswith("/proc/self/fdinfo/"):
            raise OSError("fdinfo unavailable")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(workspace_module.os, "open", fail_fdinfo)
    with pytest.raises(WorkspaceError) as caught:
        snapshot_tree(workspace)

    assert caught.value.code == "mount_identity_unavailable"
    assert _fd_count() == before
