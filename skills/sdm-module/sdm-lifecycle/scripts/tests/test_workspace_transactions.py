from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from canonical import canonical_bytes, digest, digest_bytes, read_document
from journal import ReadCapability, WorkspaceLock
from ledger import replay
import workspace as workspace_module
from workspace import (
    WorkspaceError,
    create_workspace,
    mutate,
    publish_artifact,
    snapshot_tree,
    validate_workspace,
)


def _raw_snapshot(root: Path) -> dict[str, tuple]:
    entries: dict[str, tuple] = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        entries[relative] = (
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_dev,
            info.st_ino,
            path.read_bytes() if stat.S_ISREG(info.st_mode) else None,
            os.readlink(path) if stat.S_ISLNK(info.st_mode) else None,
        )
    return entries


def test_create_pins_engine_genesis_and_initial_projection(tmp_path, asset_bundle) -> None:
    path = tmp_path / "fresh"
    result = create_workspace(
        path,
        {"workspace_id": "fresh", "protocol": "exploratory", "bindings": {}},
        expected_revision=0,
        operation_id="create",
        assets_root=asset_bundle.root,
    )
    assert result.revision == 1
    assert result.state["protocol"] == "exploratory"
    assert "promotion_eligible" not in result.state
    replayed = replay(path)
    assert replayed.genesis["engine_contract_digest"] == asset_bundle.engine_contract_digest
    assert (path / "_engine" / asset_bundle.engine_contract_digest / "schemas/state.schema.json").is_file()
    assert validate_workspace(path)["valid"] is True

    # Creation is mkdirat-bound to a retained, no-follow parent. A symlinked
    # parent is never treated as authority to create in its target.
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    alias_parent = tmp_path / "alias-parent"
    alias_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(WorkspaceError) as symlink_parent:
        create_workspace(
            alias_parent / "escaped",
            {
                "workspace_id": "escaped", "protocol": "exploratory",
            },
            expected_revision=0,
            operation_id="create-through-link",
            assets_root=asset_bundle.root,
        )
    assert symlink_parent.value.code == "unsafe_workspace_parent"
    assert not (real_parent / "escaped").exists()

    # A live-name replacement after mkdir cannot make the retained bootstrap
    # descriptor authorize a different directory at the selected path.
    bootstrap_path = tmp_path / "bootstrap-capability"
    bootstrap = workspace_module._mkdir_new_workspace(bootstrap_path)
    retained = tmp_path / "bootstrap-capability-retained"
    bootstrap_path.rename(retained)
    bootstrap_path.mkdir()
    try:
        with pytest.raises(WorkspaceError) as rebound:
            bootstrap.authenticate()
        assert rebound.value.code == "workspace_rebound"
    finally:
        bootstrap.close()
        bootstrap_path.rmdir()
        retained.rmdir()


def test_mutation_cas_conflict_leaves_tree_byte_and_metadata_identical(workspace) -> None:
    before = snapshot_tree(workspace)
    base = replay(workspace)
    with pytest.raises(WorkspaceError) as caught:
        mutate(
            workspace,
            expected_revision=base.revision + 1,
            operation_id="wrong-revision",
            event_type="substate.recorded",
            subjects={"workspace_id": base.genesis["workspace_id"]},
            payload={"name": "must-not-commit"},
        )
    assert caught.value.code == "cas_conflict"
    assert caught.value.exit_code == 4
    assert snapshot_tree(workspace) == before

    # Raw mutation remains available for replay/security fixtures, but every
    # public caller can opt into a fail-closed pre/post T01 boundary.
    base = replay(workspace)
    mutate(
        workspace,
        expected_revision=base.revision,
        operation_id="fixture-invalid-typed-graph",
        event_type="artifact.published",
        subjects={"workspace_id": base.genesis["workspace_id"]},
        payload={
            "artifact_id": "S03", "instance_key": "orphan-planning",
            "path": "artifacts/S03/orphan-planning.json",
            "digest": digest({"orphan": "planning"}),
            "parents": [], "status": "published",
        },
    )
    guard_before = snapshot_tree(workspace)
    with pytest.raises(WorkspaceError) as guarded:
        mutate(
            workspace,
            expected_revision=replay(workspace).revision,
            operation_id="guarded-write-after-invalid-graph",
            event_type="substate.recorded",
            subjects={"workspace_id": base.genesis["workspace_id"]},
            payload={"name": "must-not-commit"},
            enforce_graph=True,
        )
    assert guarded.value.code == "lineage_node_invalid"
    assert guarded.value.exit_code == 3
    assert snapshot_tree(workspace) == guard_before

    assert not (workspace / "_promotion").exists()


def test_publish_artifact_is_validated_and_immutable(workspace, valid_artifact) -> None:
    base = replay(workspace)
    artifact = valid_artifact("G01", "constitution-v1")
    published = publish_artifact(
        workspace,
        artifact_id="G01",
        instance_key="constitution-v1",
        value=artifact,
        expected_revision=base.revision,
        operation_id="publish-g01",
    )
    stored_path = workspace / "artifacts/G01/constitution-v1.json"
    assert digest(read_document(stored_path)) == digest(artifact)
    changed = valid_artifact("G01", "constitution-v1")
    changed["content"]["principles"] = ["rewritten governance principle"]
    changed["content_digest"] = digest(changed["content"])
    with pytest.raises(WorkspaceError) as caught:
        publish_artifact(
            workspace,
            artifact_id="G01",
            instance_key="constitution-v1",
            value=changed,
            expected_revision=published.revision,
            operation_id="rewrite-g01",
        )
    assert caught.value.code == "artifact_overwrite"
    assert read_document(stored_path) == artifact

    # Baseline replacement context consumes authenticated ledger paths only
    # through the retained descriptor reader, with the ledger digest checked
    # against the exact bytes returned by that same read.
    baseline = {"content": {"baseline_id": "descriptor-baseline"}}
    baseline_path = workspace / "artifacts/B01/descriptor-baseline.json"
    baseline_path.write_bytes(canonical_bytes(baseline))
    try:
        state = {
            "artifacts": {
                "B01": {
                    "descriptor-baseline": {
                        "path": "artifacts/B01/descriptor-baseline.json",
                        "digest": digest(baseline),
                    },
                },
            },
        }
        with ReadCapability(workspace) as reader:
            identities, deprecations = (
                workspace_module._stored_baseline_replacement_context(
                    reader, state, strict_digests=True,
                )
            )
        assert identities == {"descriptor-baseline"}
        assert deprecations == []
    finally:
        baseline_path.unlink()


def test_invalid_artifact_never_reaches_ledger(workspace, valid_artifact) -> None:
    base = replay(workspace)
    invalid = valid_artifact("G03")
    invalid["content"] = {}
    with pytest.raises(WorkspaceError) as caught:
        publish_artifact(
            workspace,
            artifact_id="G03",
            instance_key="dataset-v1",
            value=invalid,
            expected_revision=base.revision,
            operation_id="invalid-artifact",
        )
    assert caught.value.code == "artifact_invalid"
    assert replay(workspace).revision == base.revision
    assert not (workspace / "artifacts/G03/dataset-v1.json").exists()


def test_snapshot_tree_rejects_symlink_and_hardlink_aliases(workspace, tmp_path) -> None:
    (workspace / "unsafe-link").symlink_to(tmp_path)
    symlink_before = _raw_snapshot(workspace)
    with pytest.raises(WorkspaceError) as symlink:
        validate_workspace(workspace)
    assert symlink.value.code == "symlink_escape"
    assert _raw_snapshot(workspace) == symlink_before
    (workspace / "unsafe-link").unlink()

    source = workspace / "_lock"
    alias = workspace / "unsafe-hardlink"
    os.link(source, alias)
    hardlink_before = _raw_snapshot(workspace)
    with pytest.raises(WorkspaceError) as hardlink:
        validate_workspace(workspace)
    assert hardlink.value.code == "hardlink_escape"
    assert _raw_snapshot(workspace) == hardlink_before


def test_validate_rejects_fifo_without_touching_workspace(workspace) -> None:
    fifo = workspace / "unsafe-fifo"
    os.mkfifo(fifo)
    before = _raw_snapshot(workspace)
    with pytest.raises(WorkspaceError) as special:
        validate_workspace(workspace)
    assert special.value.code == "special_node"
    assert _raw_snapshot(workspace) == before


def test_mutation_refuses_concurrent_workspace_lock(workspace) -> None:
    base = replay(workspace)
    with WorkspaceLock(workspace, exclusive=True):
        with pytest.raises(WorkspaceError) as caught:
            mutate(
                workspace,
                expected_revision=base.revision,
                operation_id="locked-operation",
                event_type="substate.recorded",
                subjects={"workspace_id": base.genesis["workspace_id"]},
                payload={"name": "blocked-by-lock"},
            )
    assert caught.value.code == "lock_conflict"
    assert caught.value.exit_code == 4
