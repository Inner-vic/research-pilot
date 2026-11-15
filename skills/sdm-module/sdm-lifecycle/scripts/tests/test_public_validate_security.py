"""Public Validate security and complete-inventory regressions."""

from __future__ import annotations

import copy
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import handoffs
import workspace as workspace_module
from canonical import canonical_bytes
from ledger import replay
from workspace import (
    WorkspaceError,
    publish_artifact,
    snapshot_tree,
    validate_workspace,
)


def _node(info: os.stat_result, *, inode_delta: int = 0, device_delta: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        st_mode=info.st_mode,
        st_dev=info.st_dev + device_delta,
        st_ino=info.st_ino + inode_delta,
        st_nlink=info.st_nlink,
        st_uid=info.st_uid,
        st_gid=info.st_gid,
        st_size=info.st_size,
        st_mtime_ns=info.st_mtime_ns,
        st_ctime_ns=info.st_ctime_ns,
    )


def _full_snapshot(root: Path) -> dict[str, tuple[object, ...]]:
    """Snapshot every promised field, deliberately excluding atime."""
    captured: dict[str, tuple[object, ...]] = {}

    def visit(path: Path, relative: str) -> None:
        info = path.lstat()
        payload = path.read_bytes() if stat.S_ISREG(info.st_mode) else None
        target = os.readlink(path) if stat.S_ISLNK(info.st_mode) else None
        captured[relative] = (
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_dev,
            info.st_ino,
            info.st_nlink,
            payload,
            target,
        )
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as scan:
                children = sorted(scan, key=lambda item: item.name)
            for child in children:
                child_relative = child.name if relative == "." else f"{relative}/{child.name}"
                visit(Path(child.path), child_relative)

    visit(root, ".")
    return captured


def _assert_blocked_without_write(workspace: Path, expected_code: str) -> None:
    before = _full_snapshot(workspace)
    result = validate_workspace(workspace)
    assert result["valid"] is False
    assert any(item["code"] == expected_code for item in result["findings"])
    assert result["findings"]
    assert all(item["gate_impact"] == "block" for item in result["findings"])
    assert _full_snapshot(workspace) == before


def test_public_validate_clean_pass_is_complete_and_strictly_read_only(workspace) -> None:
    before = _full_snapshot(workspace)
    result = validate_workspace(workspace)
    assert result["valid"] is True
    assert result["findings"] == []
    assert result["snapshot_unchanged"] is True
    assert result["before_snapshot_digest"] == result["after_snapshot_digest"]
    assert result["snapshot_digest"] == result["before_snapshot_digest"]
    assert _full_snapshot(workspace) == before


@pytest.mark.parametrize("node_kind", ["symlink", "hardlink", "fifo"])
def test_public_validate_rejects_escape_and_special_nodes_without_write(
    workspace, tmp_path, node_kind
) -> None:
    unsafe = workspace / f"unsafe-{node_kind}"
    if node_kind == "symlink":
        unsafe.symlink_to(tmp_path)
        expected = "symlink_escape"
    elif node_kind == "hardlink":
        os.link(workspace / "_lock", unsafe)
        expected = "hardlink_escape"
    else:
        os.mkfifo(unsafe)
        expected = "special_node"
    before = _full_snapshot(workspace)
    with pytest.raises(WorkspaceError) as caught:
        validate_workspace(workspace)
    assert caught.value.code == expected
    assert _full_snapshot(workspace) == before


def test_public_validate_rejects_mount_device_escape_without_write(
    workspace, monkeypatch
) -> None:
    mounted = workspace / "artifacts"
    real_lstat = workspace_module.Path.lstat

    def cross_device(path: Path):
        info = real_lstat(path)
        return _node(info, device_delta=1) if path == mounted else info

    before = _full_snapshot(workspace)
    with monkeypatch.context() as scoped:
        scoped.setattr(workspace_module.Path, "lstat", cross_device)
        with pytest.raises(WorkspaceError) as caught:
            validate_workspace(workspace)
    assert caught.value.code == "mount_escape"
    assert _full_snapshot(workspace) == before


@pytest.mark.parametrize("target_relative", [".", "artifacts"])
def test_public_validate_rejects_root_and_subtree_identity_rebind(
    workspace, monkeypatch, target_relative
) -> None:
    target = workspace if target_relative == "." else workspace / target_relative
    identity = target.lstat()
    real_fstat = workspace_module.os.fstat
    changed = False

    def rebound(descriptor: int):
        nonlocal changed
        info = real_fstat(descriptor)
        if not changed and info.st_dev == identity.st_dev and info.st_ino == identity.st_ino:
            changed = True
            return _node(info, inode_delta=1)
        return info

    before = _full_snapshot(workspace)
    with monkeypatch.context() as scoped:
        scoped.setattr(workspace_module.os, "fstat", rebound)
        with pytest.raises(WorkspaceError) as caught:
            validate_workspace(workspace)
    assert changed is True
    assert caught.value.code == "concurrent_mutation"
    assert caught.value.exit_code == 4
    assert _full_snapshot(workspace) == before


def test_public_validate_rejects_concurrent_snapshot_change_without_write(
    workspace, monkeypatch
) -> None:
    real_snapshot = workspace_module.snapshot_tree
    calls = 0

    def changed_snapshot(root: Path):
        nonlocal calls
        calls += 1
        value = real_snapshot(root)
        if calls == 2:
            value = copy.deepcopy(value)
            value["entries"]["state.json"]["ctime_ns"] += 1
        return value

    before = _full_snapshot(workspace)
    with monkeypatch.context() as scoped:
        scoped.setattr(workspace_module, "snapshot_tree", changed_snapshot)
        with pytest.raises(WorkspaceError) as caught:
            validate_workspace(workspace)
    assert calls == 2
    assert caught.value.code == "concurrent_mutation"
    assert caught.value.exit_code == 4
    assert _full_snapshot(workspace) == before


@pytest.mark.parametrize(
    ("missing_kind", "expected_code"),
    [
        ("directory", "workspace_required_directory_missing"),
        ("state", "workspace_required_file_missing"),
        ("memlog", "workspace_required_file_missing"),
        ("artifact", "workspace_required_file_missing"),
        ("packet", "workspace_required_file_missing"),
    ],
)
def test_public_validate_detects_incomplete_governed_inventory(
    workspace,
    valid_artifact,
    request_payload,
    missing_kind,
    expected_code,
) -> None:
    if missing_kind == "directory":
        os.rmdir(workspace / "artifacts" / "G01")
    elif missing_kind == "state":
        (workspace / "state.json").unlink()
    elif missing_kind == "memlog":
        (workspace / "_memlog" / "00000000000000000001.json").unlink()
    elif missing_kind == "artifact":
        current = replay(workspace)
        value = valid_artifact(
            "G01",
            "missing-record",
            subjects={"workspace_id": current.genesis["workspace_id"]},
        )
        publish_artifact(
            workspace,
            artifact_id="G01",
            instance_key="missing-record",
            value=value,
            expected_revision=current.revision,
            operation_id="publish-missing-record",
        )
        (workspace / "artifacts" / "G01" / "missing-record.json").unlink()
    else:
        outcome = handoffs.create(
            workspace,
            request_payload("REQ-missing01"),
            expected_revision=replay(workspace).revision,
            operation_id="create-missing-packet",
        )
        assert outcome.packet is not None
        (workspace / "packets" / "S12" / "REQ-missing01.json").unlink()
    _assert_blocked_without_write(workspace, expected_code)


def test_public_validate_detects_orphan_and_untracked_governed_nodes(workspace) -> None:
    orphan = workspace / "artifacts" / "orphan-type"
    orphan.mkdir()
    untracked = workspace / "artifacts" / "G01" / "untracked.json"
    untracked.write_bytes(canonical_bytes({"not": "ledger-bound"}))
    before = _full_snapshot(workspace)
    result = validate_workspace(workspace)
    codes = {item["code"] for item in result["findings"]}
    assert result["valid"] is False
    assert "workspace_orphan_directory" in codes
    assert "workspace_untracked_file" in codes
    assert all(item["gate_impact"] == "block" for item in result["findings"])
    assert _full_snapshot(workspace) == before


def test_public_selected_artifact_requires_exact_authenticated_state_record(
    workspace, valid_artifact
) -> None:
    current = replay(workspace)
    value = valid_artifact(
        "G01",
        "selected",
        subjects={"workspace_id": current.genesis["workspace_id"]},
    )
    publish_artifact(
        workspace,
        artifact_id="G01",
        instance_key="selected",
        value=value,
        expected_revision=current.revision,
        operation_id="publish-selected",
    )
    selected = workspace / "artifacts" / "G01" / "selected.json"
    tampered = dict(value)
    tampered["created_by"] = "different-subject"
    selected.write_bytes(canonical_bytes(tampered))
    before = _full_snapshot(workspace)
    result = validate_workspace(workspace, artifact_path=selected)
    codes = {item["code"] for item in result["findings"]}
    assert result["valid"] is False
    assert "artifact_digest_mismatch" in codes
    assert "selected_artifact_binding_mismatch" in codes
    assert all(item["gate_impact"] == "block" for item in result["findings"])
    assert _full_snapshot(workspace) == before


def test_public_selected_artifact_rejects_untracked_governed_file(workspace) -> None:
    selected = workspace / "artifacts" / "G01" / "untracked-selected.json"
    selected.write_bytes(canonical_bytes({"schema": "untracked"}))
    before = _full_snapshot(workspace)
    result = validate_workspace(workspace, artifact_path=selected)
    codes = {item["code"] for item in result["findings"]}
    assert result["valid"] is False
    assert "workspace_untracked_file" in codes
    assert "selected_artifact_untracked" in codes
    assert all(item["gate_impact"] == "block" for item in result["findings"])
    assert _full_snapshot(workspace) == before
