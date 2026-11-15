from __future__ import annotations

from copy import deepcopy
import os

import pytest

from canonical import MAX_DOCUMENT_BYTES, canonical_bytes
import journal as journal_module
from ledger import build_event, replay, replay_with
from projection import ProjectionError, admissible_actions, apply_event, materialize, verify_stored_projection


def test_materialization_is_deterministic_full_replay(workspace) -> None:
    replayed = replay(workspace)
    first = materialize(replayed)
    second = materialize(replayed)
    assert canonical_bytes(first) == canonical_bytes(second)
    assert first["revision"] == replayed.revision
    assert first["ledger_head"] == replayed.head_hash
    assert first["traceability"]["derived_from_revision"] == replayed.revision


@pytest.mark.parametrize("event_type", ["handoff.result-ingested", "handoff.reconciled"])
def test_terminal_receipt_preserves_coordinator_consumption_time(workspace, asset_bundle, event_type):
    state = materialize(replay(workspace))
    state["handoffs"]["request"] = {"request_id": "request", "history": []}
    # No receipt bytes are published here: isolate the replay fact that the
    # derivation verifier must use, including a reconciled terminal event.
    apply_event(state, {
        "event_type": event_type, "event_hash": "sha256:" + "a" * 64,
        "operation_id": "historical-consumption", "sequence": state["revision"] + 1,
        "engine_contract_digest": state["active_engine_digest"],
        "subjects": {}, "payload": {
            "request_id": "request", "terminal_status": "failed",
            "producer_attestation_consumed_at": "2026-01-02T03:04:05Z",
        },
    }, asset_bundle)
    assert state["handoffs"]["request"]["producer_attestation_consumed_at"] == "2026-01-02T03:04:05Z"


def test_admissible_actions_distinguish_block_config_handoff_and_advance(workspace, asset_bundle) -> None:
    state = materialize(replay(workspace))
    blocked = admissible_actions(state, asset_bundle, state["bindings"])[0]
    assert blocked.step == 1
    assert blocked.disposition == "blocked"
    assert blocked.missing == ("non_goals.explicit", "problem.clear")

    ready = deepcopy(state)
    ready["problem"] = {"clear": True}
    ready["non_goals"] = {"explicit": True}
    advance = admissible_actions(ready, asset_bundle, ready["bindings"])[0]
    assert advance.disposition == "advance"

    contract = deepcopy(state)
    contract["lifecycle_step"] = 3
    contract["contracts"] = {"all_applicable_semantics_closed": True}
    unconfigured = admissible_actions(contract, asset_bundle, {})[0]
    assert unconfigured.disposition == "configuration_required"
    configured = admissible_actions(contract, asset_bundle, state["bindings"])[0]
    assert configured.disposition == "handoff"


def test_projection_rejects_duplicate_operation_ids(workspace) -> None:
    base = replay(workspace)
    event = build_event(
        sequence=base.revision + 1,
        event_type="substate.recorded",
        previous_hash=base.head_hash,
        engine_contract_digest=base.active_engine_digest,
        actor={"type": "coordinator", "id": "sdm-lifecycle"},
        subjects={"workspace_id": base.genesis["workspace_id"]},
        payload={"name": "duplicate"},
        operation_id=base.events[0]["operation_id"],
        occurred_at="2026-01-02T03:04:05Z",
    )
    with pytest.raises(ProjectionError) as caught:
        materialize(replay_with((event,), base))
    assert caught.value.code == "duplicate_operation"


def test_projection_rejects_out_of_order_lifecycle_row(workspace) -> None:
    base = replay(workspace)
    event = build_event(
        sequence=base.revision + 1,
        event_type="lifecycle.advanced",
        previous_hash=base.head_hash,
        engine_contract_digest=base.active_engine_digest,
        actor={"type": "coordinator", "id": "sdm-lifecycle"},
        subjects={"workspace_id": base.genesis["workspace_id"]},
        payload={"step": 5, "outputs": [], "substate_events": []},
        operation_id="out-of-order",
        occurred_at="2026-01-02T03:04:05Z",
    )
    with pytest.raises(ProjectionError) as caught:
        materialize(replay_with((event,), base))
    assert caught.value.code == "lifecycle_step_out_of_order"


def test_stored_projection_must_match_canonical_replay(
    workspace, monkeypatch,
) -> None:
    replayed = replay(workspace)
    assert verify_stored_projection(workspace, replayed) == materialize(replayed)
    authentic = materialize(replayed)
    stored = deepcopy(authentic)
    stored["dimensions"]["scientific"] = "fabricated"
    state_path = workspace / "state.json"
    state_path.write_bytes(canonical_bytes(stored))
    with pytest.raises(ProjectionError) as caught:
        verify_stored_projection(workspace, replayed)
    assert caught.value.code == "projection_mismatch"

    state_path.write_bytes(canonical_bytes(authentic))
    real_open = journal_module.os.open
    observed_nonblocking = False

    def inspect_open(path, flags, *args, **kwargs):
        nonlocal observed_nonblocking
        if path == "state.json" and kwargs.get("dir_fd") is not None:
            observed_nonblocking = True
            assert flags & getattr(os, "O_NONBLOCK", 0)
        return real_open(path, flags, *args, **kwargs)

    with monkeypatch.context() as nonblocking:
        nonblocking.setattr(journal_module.os, "open", inspect_open)
        assert verify_stored_projection(workspace, replayed) == authentic
    assert observed_nonblocking is True

    with state_path.open("wb") as stream:
        stream.truncate(MAX_DOCUMENT_BYTES + 1)
    with pytest.raises(ProjectionError) as oversized:
        verify_stored_projection(workspace, replayed)
    assert oversized.value.code == "stored_projection_invalid"
    assert state_path.stat().st_size == MAX_DOCUMENT_BYTES + 1

    state_path.unlink()
    os.mkfifo(state_path)
    with pytest.raises(ProjectionError) as fifo:
        verify_stored_projection(workspace, replayed)
    assert fifo.value.code == "stored_projection_invalid"
    state_path.unlink()
    state_path.write_bytes(canonical_bytes(authentic))

    retained = workspace.with_name(workspace.name + "-retained-original")
    changed = False

    def substitute_root(path, flags, *args, **kwargs):
        nonlocal changed
        if (
            not changed and path == "state.json"
            and kwargs.get("dir_fd") is not None
        ):
            changed = True
            workspace.rename(retained)
            workspace.mkdir()
            (workspace / "state.json").write_bytes(canonical_bytes({
                "schema": "attacker-replacement",
            }))
        return real_open(path, flags, *args, **kwargs)

    with monkeypatch.context() as rebound:
        rebound.setattr(journal_module.os, "open", substitute_root)
        with pytest.raises(ProjectionError) as concurrent:
            verify_stored_projection(workspace, replayed)
    assert changed is True
    assert concurrent.value.code == "concurrent_mutation"
