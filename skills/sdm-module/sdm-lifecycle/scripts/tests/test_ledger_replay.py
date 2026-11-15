from __future__ import annotations

from copy import deepcopy
import os

import pytest

from canonical import canonical_bytes
from contracts import AssetBundle
import journal as journal_module
from journal import JournalError, ReadCapability
from ledger import LedgerError, ReplayResult, build_event, genesis_document, genesis_hash, replay
from projection import ProjectionError, materialize, verify_stored_projection
import workspace as workspace_module
from workspace import WorkspaceError, mutate


def test_replay_reconstructs_contiguous_hash_chain(workspace) -> None:
    first = replay(workspace)
    mutation = mutate(
        workspace,
        expected_revision=first.revision,
        operation_id="append-substate",
        event_type="substate.recorded",
        subjects={"workspace_id": first.genesis["workspace_id"]},
        payload={"name": "preflight.complete"},
    )
    replayed = replay(workspace)
    assert replayed.revision == 2
    assert replayed.events[1]["previous_hash"] == replayed.events[0]["event_hash"]
    assert replayed.head_hash == mutation.event["event_hash"]
    assert materialize(replayed)["substate_events"] == [
        {
            "name": "preflight.complete",
            "value": True,
            "event_hash": mutation.event["event_hash"],
        }
    ]


def test_replay_rejects_tampered_event_bytes(workspace) -> None:
    event_path = workspace / "_ledger/events/00000000000000000001.json"
    import json

    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["payload"]["intent"] = "tampered after authentication"
    event_path.write_bytes(canonical_bytes(event))
    with pytest.raises(LedgerError) as caught:
        replay(workspace)
    assert caught.value.code == "event_hash_mismatch"
    assert caught.value.sequence == 1


def test_replay_rejects_gap_and_unexpected_ledger_nodes(
    workspace, monkeypatch,
) -> None:
    event_path = workspace / "_ledger/events/00000000000000000001.json"
    second = event_path.with_name("00000000000000000002.json")
    event_path.rename(second)
    with pytest.raises(LedgerError) as gap:
        replay(workspace)
    assert gap.value.code == "ledger_gap"
    second.rename(event_path)

    unexpected = event_path.with_name("unexpected.json")
    unexpected.write_bytes(b"{}")
    with pytest.raises(LedgerError) as extra:
        replay(workspace)
    assert extra.value.code == "unexpected_ledger_file"
    unexpected.unlink()

    original_event = event_path.read_bytes()
    event_path.unlink()
    os.mkfifo(event_path)
    with pytest.raises(LedgerError) as fifo:
        replay(workspace)
    assert fifo.value.code == "unsafe_ledger_node"
    event_path.unlink()
    event_path.write_bytes(original_event)

    second.write_bytes(event_path.read_bytes())
    with pytest.raises(JournalError) as node_limit:
        with ReadCapability(workspace, max_nodes=4) as reader:
            directory_fd = reader.open_directory("_ledger/events")
            try:
                reader.scan_regular_files(directory_fd, "_ledger/events")
            finally:
                os.close(directory_fd)
    assert node_limit.value.code == "document_resource_limit"
    with pytest.raises(JournalError) as byte_limit:
        with ReadCapability(
            workspace, max_bytes=event_path.stat().st_size,
        ) as reader:
            directory_fd = reader.open_directory("_ledger/events")
            try:
                reader.scan_regular_files(directory_fd, "_ledger/events")
            finally:
                os.close(directory_fd)
    assert byte_limit.value.code == "document_resource_limit"
    second.unlink()

    real_mount_identity = journal_module._mount_identity
    event_identity = (event_path.stat().st_dev, event_path.stat().st_ino)

    def injected_mount(descriptor: int) -> str:
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == event_identity:
            return "mnt_id:999999999"
        return real_mount_identity(descriptor)

    with monkeypatch.context() as mounted:
        mounted.setattr(journal_module, "_mount_identity", injected_mount)
        with pytest.raises(LedgerError) as mount_escape:
            replay(workspace)
    assert mount_escape.value.code == "mount_escape"

    protected_reads = (
        workspace / "_ledger/genesis.json",
        workspace / "_engine" / replay(workspace).active_engine_digest
        / "authority-policy.json",
    )
    for protected_path in protected_reads:
        protected_identity = (
            protected_path.stat().st_dev, protected_path.stat().st_ino,
        )

        def injected_protected_mount(
            descriptor: int, *, selected=protected_identity,
        ) -> str:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) == selected:
                return "mnt_id:999999998"
            return real_mount_identity(descriptor)

        with monkeypatch.context() as mounted:
            mounted.setattr(
                journal_module, "_mount_identity", injected_protected_mount,
            )
            with pytest.raises(LedgerError) as protected_mount_escape:
                replay(workspace)
        assert protected_mount_escape.value.code == "mount_escape"

    with monkeypatch.context() as unavailable:
        unavailable.setattr(
            journal_module,
            "_mount_identity",
            lambda _descriptor: (_ for _ in ()).throw(JournalError(
                "mount_identity_unavailable", "fdinfo unavailable",
            )),
        )
        with pytest.raises(LedgerError) as no_mount_identity:
            replay(workspace)
    assert no_mount_identity.value.code == "mount_identity_unavailable"

    real_open = journal_module.os.open
    observed_nonblocking = False

    def inspect_open(path, flags, *args, **kwargs):
        nonlocal observed_nonblocking
        if path == event_path.name and kwargs.get("dir_fd") is not None:
            observed_nonblocking = True
            assert flags & getattr(os, "O_NONBLOCK", 0)
        return real_open(path, flags, *args, **kwargs)

    with monkeypatch.context() as nonblocking:
        nonblocking.setattr(journal_module.os, "open", inspect_open)
        assert replay(workspace).revision == 1
    assert observed_nonblocking is True

    changed = False
    rebound = event_path.with_name("rebound-original.json")

    def substitute_after_inventory(path, flags, *args, **kwargs):
        nonlocal changed
        if (
            not changed and path == event_path.name
            and kwargs.get("dir_fd") is not None
        ):
            changed = True
            original = event_path.read_bytes()
            event_path.rename(rebound)
            event_path.write_bytes(original)
        return real_open(path, flags, *args, **kwargs)

    with monkeypatch.context() as substituted:
        substituted.setattr(
            journal_module.os, "open", substitute_after_inventory,
        )
        with pytest.raises(LedgerError) as concurrent:
            replay(workspace)
    assert changed is True
    assert concurrent.value.code == "ledger_concurrent_mutation"


def test_pinned_engine_bytes_cannot_drift(workspace) -> None:
    replayed = replay(workspace)
    assert replayed.bundles[replayed.active_engine_digest].root != (
        workspace / "_engine" / replayed.active_engine_digest
    )
    engine_asset = workspace / "_engine" / replayed.active_engine_digest / "authority-policy.json"
    import json

    value = json.loads(engine_asset.read_text(encoding="utf-8"))
    value["schema_version"] = "tampered"
    engine_asset.write_bytes(canonical_bytes(value))
    with pytest.raises(LedgerError) as caught:
        replay(workspace)
    assert caught.value.code == "pinned_engine_invalid"


def test_caller_origin_ignored(asset_bundle: AssetBundle) -> None:
    genesis = genesis_document("workspace", "2026-01-01T00:00:00Z", asset_bundle)
    head = genesis_hash(genesis)
    common = {
        "sequence": 1,
        "event_type": "workspace.created",
        "previous_hash": head,
        "engine_contract_digest": asset_bundle.engine_contract_digest,
        "subjects": {"workspace_id": "workspace"},
        "payload": {"protocol": "exploratory", "bindings": {}},
        "operation_id": "origin-test",
        "occurred_at": "2026-01-01T00:00:00Z",
    }
    coordinator_event = build_event(actor={"type": "coordinator", "id": "sdm-lifecycle"}, **common)
    caller_event = build_event(actor={"type": "caller-claimed-origin", "id": "untrusted"}, **common)
    coordinator = materialize(
        ReplayResult(genesis, (coordinator_event,), 1, coordinator_event["event_hash"], asset_bundle.engine_contract_digest, {asset_bundle.engine_contract_digest: asset_bundle})
    )
    caller = materialize(
        ReplayResult(genesis, (caller_event,), 1, caller_event["event_hash"], asset_bundle.engine_contract_digest, {asset_bundle.engine_contract_digest: asset_bundle})
    )
    # Actor/origin bytes remain authenticated in the ledger, but they cannot
    # select transition semantics or alter projected scientific state.
    for key in ("dimensions", "lifecycle_step", "protocol", "intent"):
        assert caller[key] == coordinator[key]
    assert caller["ledger_head"] != coordinator["ledger_head"]


def test_partial_history_projection_not_trusted(workspace) -> None:
    replayed = replay(workspace)
    authentic = materialize(replayed)
    forged = deepcopy(authentic)
    forged["revision"] = 0
    forged["operations"] = {}
    (workspace / "state.json").write_bytes(canonical_bytes(forged))
    with pytest.raises(ProjectionError) as caught:
        verify_stored_projection(workspace, replayed)
    assert caught.value.code == "projection_mismatch"


def test_operation_id_is_idempotent_only_for_identical_input(
    workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = replay(workspace)
    first = mutate(
        workspace,
        expected_revision=base.revision,
        operation_id="stable-operation",
        event_type="substate.recorded",
        subjects={"workspace_id": base.genesis["workspace_id"]},
        payload={"name": "one"},
    )
    duplicate = mutate(
        workspace,
        expected_revision=base.revision,
        operation_id="stable-operation",
        event_type="substate.recorded",
        subjects={"workspace_id": base.genesis["workspace_id"]},
        payload={"name": "one"},
    )
    assert duplicate.idempotent is True
    assert duplicate.revision == first.revision

    # The no-write idempotent branch still has to authenticate the live root
    # name against the directory retained by the active workspace lock.
    authenticate = workspace_module._authenticate_mutation_exit
    retained = workspace.with_name(workspace.name + "-retained")

    def rebind_before_exit(lock, root):
        root.rename(retained)
        root.mkdir()
        try:
            return authenticate(lock, root)
        finally:
            root.rmdir()
            retained.rename(root)

    monkeypatch.setattr(
        workspace_module, "_authenticate_mutation_exit", rebind_before_exit,
    )
    with pytest.raises(WorkspaceError) as rebound:
        mutate(
            workspace,
            expected_revision=base.revision,
            operation_id="stable-operation",
            event_type="substate.recorded",
            subjects={"workspace_id": base.genesis["workspace_id"]},
            payload={"name": "one"},
        )
    assert rebound.value.code == "workspace_rebound"
    assert rebound.value.exit_code == 4
    monkeypatch.setattr(
        workspace_module, "_authenticate_mutation_exit", authenticate,
    )

    with pytest.raises(WorkspaceError) as conflict:
        mutate(
            workspace,
            expected_revision=first.revision,
            operation_id="stable-operation",
            event_type="substate.recorded",
            subjects={"workspace_id": base.genesis["workspace_id"]},
            payload={"name": "different"},
        )
    assert conflict.value.code == "idempotency_conflict"
