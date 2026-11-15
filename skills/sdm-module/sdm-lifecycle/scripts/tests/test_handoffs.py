from __future__ import annotations

from copy import deepcopy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import handoffs
from canonical import canonical_bytes, digest, read_document
from contracts import evaluate_gate, validate_schema
from handoffs import HandoffError
from ledger import replay
from producer_signing import seal_platform_result, MODULE as TEST_MODULE_ROOT
from projection import materialize
from workspace import mutate as raw_mutate, resume_workspace, snapshot_tree


MODULE = TEST_MODULE_ROOT
FAKE_DATA_PRODUCER = (
    MODULE / "tests" / "fixtures" / "platform-skills" /
    "fake-data-platform" / "fixture_result.py"
)
FAKE_DATA_PRODUCER_PRINCIPAL_ID = "fake-data-platform-producer"


def _create(workspace: Path, payload: dict, operation_id: str = "handoff-create"):
    return handoffs.create(
        workspace,
        payload,
        expected_revision=replay(workspace).revision,
        operation_id=operation_id,
    )


def _ingest(workspace: Path, result: dict, operation_id: str = "handoff-ingest"):
    current = replay(workspace)
    record = materialize(current)["handoffs"].get(result.get("request_id"), {})
    if record.get("status") == "created":
        dispatched = handoffs.mark_dispatched(
            workspace,
            request_id=result["request_id"],
            dispatch_correlation=result["dispatch_correlation"],
            expected_revision=current.revision,
            operation_id=operation_id + "-dispatch",
        )
        expected_revision = dispatched.revision
    else:
        expected_revision = current.revision
    return handoffs.ingest(
        workspace,
        result,
        expected_revision=expected_revision,
        operation_id=operation_id,
    )


def _mark_for_result(workspace: Path, result: dict, operation_id: str):
    return handoffs.mark_dispatched(
        workspace,
        request_id=result["request_id"],
        dispatch_correlation=result["dispatch_correlation"],
        expected_revision=replay(workspace).revision,
        operation_id=operation_id,
    )


def _state(workspace: Path) -> dict:
    return materialize(replay(workspace))


def _resume_state(workspace: Path) -> dict:
    resumed = resume_workspace(workspace)
    assert resumed["revision"] == replay(workspace).revision
    return resumed["state"]


def _event_types(workspace: Path, request_id: str) -> list[str]:
    state = _state(workspace)
    return [row["event_type"] for row in state["handoffs"][request_id]["history"]]


def test_single_logical_request(workspace, request_payload, monkeypatch) -> None:
    monkeypatch.setattr(handoffs, "utc_now", lambda: "2026-01-02T03:04:05Z")
    payload = request_payload(subjects={
        "experiment_id": "experiment-1",
        "research_brief_id": "research-brief-1",
    })
    first = _create(workspace, payload, "create-once")
    second = handoffs.create(
        workspace,
        payload,
        expected_revision=first.revision,
        operation_id="create-retry",
    )
    assert second.idempotent is True
    assert second.request_id == first.request_id
    assert second.packet == first.packet
    assert replay(workspace).revision == first.revision
    assert len(list((workspace / "packets/S12").glob("*.json"))) == 1
    assert read_document(workspace / f"packets/S12/{first.request_id}.json") == first.packet
    assert first.packet["subjects"] == payload["subjects"]
    assert _event_types(workspace, first.request_id) == ["handoff.created"]
    assert _resume_state(workspace)["handoffs"][first.request_id]["idempotency_key"] == first.packet["idempotency_key"]

    # The handoff surface rejects a replay-authenticated graph defect before
    # attempting an idempotent request lookup or writing packet/history bytes.
    base = replay(workspace)
    raw_mutate(
        workspace,
        expected_revision=base.revision,
        operation_id="fixture-orphan-planning",
        event_type="artifact.published",
        subjects={"workspace_id": base.genesis["workspace_id"]},
        payload={
            "artifact_id": "S03", "instance_key": "orphan-planning",
            "path": "artifacts/S03/orphan-planning.json",
            "digest": digest({"orphan": "planning"}),
            "parents": [], "status": "published",
        },
    )
    before_guard = snapshot_tree(workspace)
    with pytest.raises(HandoffError) as graph_guard:
        _create(workspace, payload, "blocked-by-typed-graph")
    assert graph_guard.value.code == "lineage_node_invalid"
    assert graph_guard.value.exit_code == 3
    assert snapshot_tree(workspace) == before_guard


def test_create_derives_immutable_contract_fields_when_caller_omits_them(
    workspace, request_payload, asset_bundle,
) -> None:
    payload = request_payload()
    for field in (
        "binding_identity", "binding_skill_digest", "adapter_contract_digest",
        "expected_evidence", "evidence_cardinality",
    ):
        payload.pop(field)

    before = replay(workspace)
    created = _create(workspace, payload, "derive-request-contract")
    packet = created.packet
    assert packet is not None
    domain, operation_contract, binding_key = asset_bundle.operation_contract(
        payload["operation"],
    )
    state = materialize(before)
    assert packet["domain"] == domain
    assert packet["binding_key"] == binding_key
    assert packet["binding_identity"] == state["bindings"][binding_key]
    assert packet["binding_skill_digest"] == \
        state["binding_skill_digests"][binding_key]
    assert packet["adapter_contract_digest"] == digest(operation_contract)
    assert packet["workspace_id"] == before.genesis["workspace_id"]
    assert packet["workspace_revision"] == before.revision
    assert packet["expected_evidence"] == \
        operation_contract["required_evidence"]
    assert packet["evidence_cardinality"] == \
        operation_contract["evidence_cardinality"]
    assert packet["expected_result_schema"] == \
        "schemas/handoff-result.schema.json"


@pytest.mark.parametrize(
    ("field", "substitution", "code"),
    [
        ("binding_identity", "substituted-platform", "binding_changed"),
        ("binding_skill_digest", "sha256:" + "1" * 64, "request_contract_mismatch"),
        ("adapter_contract_digest", "sha256:" + "2" * 64, "request_contract_mismatch"),
        ("expected_evidence", ["terminal_status"], "request_contract_mismatch"),
        ("required_evidence", ["terminal_status"], "request_contract_mismatch"),
        ("evidence_cardinality", {}, "request_contract_mismatch"),
        ("domain", "source-control-platform", "request_contract_mismatch"),
        ("binding_key", "source_control_platform_skill", "request_contract_mismatch"),
        ("workspace_id", "substituted-workspace", "request_contract_mismatch"),
        ("workspace_revision", 999, "request_contract_mismatch"),
        ("expected_result_schema", "schemas/result.schema.json", "request_contract_mismatch"),
    ],
)
def test_create_rejects_caller_contract_downgrade_without_writes(
    workspace, request_payload, field, substitution, code,
) -> None:
    payload = request_payload()
    payload[field] = substitution
    before = snapshot_tree(workspace)

    with pytest.raises(HandoffError) as caught:
        _create(workspace, payload, f"reject-{field}")

    assert caught.value.code == code
    assert snapshot_tree(workspace) == before
    assert not list((workspace / "packets/S12").glob("*.json"))


def test_existing_request_idempotency_uses_derived_contract_not_caller_claims(
    workspace, request_payload,
) -> None:
    payload = request_payload()
    created = _create(workspace, payload, "create-derived-idempotent")
    before = snapshot_tree(workspace)
    downgraded = deepcopy(payload)
    downgraded["expected_evidence"] = ["terminal_status"]

    with pytest.raises(HandoffError) as caught:
        handoffs.create(
            workspace,
            downgraded,
            expected_revision=created.revision,
            operation_id="retry-with-downgraded-evidence",
        )

    assert caught.value.code == "request_contract_mismatch"
    assert snapshot_tree(workspace) == before
    assert read_document(
        workspace / f"packets/S12/{created.request_id}.json"
    ) == created.packet


@pytest.mark.parametrize("subjects", [{}, {"research_brief_id": "brief-1"}])
def test_subject_downgrade_is_rejected_without_writes(
    workspace, request_payload, subjects,
) -> None:
    before = snapshot_tree(workspace)
    with pytest.raises(HandoffError) as caught:
        _create(workspace, request_payload(subjects=subjects), "under-bound")
    assert caught.value.code in {
        "request_subjects_invalid", "request_subjects_underbound",
    }
    assert snapshot_tree(workspace) == before


def test_execution_subject_contract_requires_run_and_attempt_anchors(
    workspace, request_payload,
) -> None:
    underbound = request_payload(
        operation="ml.training",
        step=15,
        subjects={"experiment_id": "experiment-1", "run_id": "run-1"},
    )
    before = snapshot_tree(workspace)
    with pytest.raises(HandoffError) as caught:
        _create(workspace, underbound, "underbound-training")
    assert caught.value.code == "request_subjects_underbound"
    assert snapshot_tree(workspace) == before

    complete = request_payload(
        operation="ml.training",
        step=15,
        subjects={
            "experiment_id": "experiment-1",
            "run_id": "run-1",
            "attempt_id": "attempt-1",
            "run_manifest_digest": digest({"run": "run-1"}),
            "expected_runtime_image_digest": digest({"image": "runtime-1"}),
            "expected_runtime_profile_digest": digest({"profile": "runtime-1"}),
            "custom_instance_id": "extension-remains-bound",
        },
    )
    created = _create(workspace, complete, "bound-training")
    assert created.packet is not None
    assert created.packet["subjects"] == complete["subjects"]


def test_anchor_subject_contract_requires_provisional_artifact(
    workspace, request_payload,
) -> None:
    underbound = request_payload(
        operation="git.anchor-ledger-head",
        step=20,
        subjects={"experiment_id": "experiment-1"},
    )
    before = snapshot_tree(workspace)
    with pytest.raises(HandoffError) as caught:
        _create(workspace, underbound, "underbound-anchor")
    assert caught.value.code == "request_subjects_underbound"
    assert snapshot_tree(workspace) == before

    complete = request_payload(
        operation="git.anchor-ledger-head",
        step=20,
        subjects={
            "experiment_id": "experiment-1",
            "artifact_id": "D05:experiment-1.decision-1",
        },
    )
    created = _create(workspace, complete, "bound-anchor")
    assert created.packet is not None
    assert created.packet["subjects"] == complete["subjects"]


def test_composite_all_of(workspace, request_payload, result_packet) -> None:
    data = _create(workspace, request_payload("REQ-data001", operation="data.quality-check"), "create-data")
    _ingest(workspace, result_packet(data.packet), "ingest-data")
    gate = {"all_of": [{"platform_result": "data.quality-check"}, {"platform_result": "git.snapshot"}]}
    partial = _resume_state(workspace)
    assert evaluate_gate(gate, partial) == (False, ["platform_result:git.snapshot"], [])

    source = _create(workspace, request_payload("REQ-git0001", operation="git.snapshot"), "create-git")
    _ingest(workspace, result_packet(source.packet, result_id="RES-git0001"), "ingest-git")
    state = _resume_state(workspace)
    assert evaluate_gate(gate, state) == (True, [], [])
    assert state["platform_results"] == {"data.quality-check": "successful", "git.snapshot": "successful"}
    assert _event_types(workspace, data.request_id) == ["handoff.created", "handoff.dispatched", "handoff.result-ingested"]
    assert _event_types(workspace, source.request_id) == ["handoff.created", "handoff.dispatched", "handoff.result-ingested"]


def test_at_least_once_owner_dedup(
    fake_owner_workspace, request_payload, result_packet, tmp_path,
    asset_bundle,
) -> None:
    workspace = fake_owner_workspace
    created = _create(workspace, request_payload(), "create")
    dispatch_correlation = f"fixture:{created.packet['idempotency_key']}"
    dispatched = handoffs.mark_dispatched(
        workspace,
        request_id=created.request_id,
        dispatch_correlation=dispatch_correlation,
        expected_revision=created.revision,
        operation_id="dispatch-to-fake-owner",
    )

    owner_state = tmp_path / "fake-owner-state.json"
    first_owner_result = tmp_path / "owner-result-1.json"
    duplicate_owner_result = tmp_path / "owner-result-2.json"
    command = [
        sys.executable,
        "-B",
        str(FAKE_DATA_PRODUCER),
        "--request",
        str(workspace / f"packets/S12/{created.request_id}.json"),
        "--state",
        str(owner_state),
    ]
    first_delivery = subprocess.run(
        [*command, "--output", str(first_owner_result)],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    duplicate_delivery = subprocess.run(
        [*command, "--output", str(duplicate_owner_result)],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert first_delivery.returncode == duplicate_delivery.returncode == 0
    assert first_owner_result.read_bytes() == duplicate_owner_result.read_bytes()
    owner_records = json.loads(owner_state.read_text())
    assert list(owner_records) == [created.packet["idempotency_key"]]
    owner_packet = read_document(first_owner_result)
    assert validate_schema(
        owner_packet,
        asset_bundle.document("schemas/handoff-result.schema.json"),
        subject=created.request_id,
        governing_asset="schemas/handoff-result.schema.json",
        registry=asset_bundle.documents,
    ) == []
    for anchor in (
        "request_id", "operation", "binding_identity",
        "binding_skill_digest", "adapter_contract_digest", "workspace_id",
        "workspace_revision", "gate", "step", "subjects",
        "idempotency_key",
    ):
        assert owner_packet[anchor] == created.packet[anchor]
    assert owner_packet["schema"] == "sdm-handoff-result/v1"
    assert owner_packet["dispatch_correlation"] == dispatch_correlation
    assert owner_packet["producer_identity"] == created.packet["binding_identity"]
    assert owner_packet["producer_principal_id"] == \
        FAKE_DATA_PRODUCER_PRINCIPAL_ID
    assert owner_packet["producer_principal_id"] != \
        owner_packet["producer_identity"]
    assert [item["type"] for item in owner_packet["evidence"]] == \
        created.packet["expected_evidence"]

    accepted = _ingest(workspace, owner_packet, "ingest-first-owner-result")
    accepted_revision = accepted.revision
    accepted_bytes = (workspace / f"packets/S13/{created.request_id}.json").read_bytes()
    duplicate = handoffs.ingest(
        workspace,
        owner_packet,
        expected_revision=accepted.revision,
        operation_id="ingest-redelivery",
    )
    assert accepted.disposition == "accepted"
    assert accepted.revision == dispatched.revision + 1
    assert duplicate.idempotent is True
    assert duplicate.revision == accepted.revision
    assert replay(workspace).revision == accepted_revision
    resumed_record = _resume_state(workspace)["handoffs"][created.request_id]
    assert resumed_record["status"] == "successful"
    assert resumed_record["producer_identity"] == \
        created.packet["binding_identity"]
    assert resumed_record["producer_principal_id"] == \
        FAKE_DATA_PRODUCER_PRINCIPAL_ID
    assert accepted.event["payload"]["producer_identity"] == \
        created.packet["binding_identity"]
    assert accepted.event["payload"]["producer_principal_id"] == \
        FAKE_DATA_PRODUCER_PRINCIPAL_ID
    assert _event_types(workspace, created.request_id) == [
        "handoff.created", "handoff.dispatched", "handoff.result-ingested"
    ]
    assert len(list((workspace / "packets/S13").glob(f"{created.request_id}.json"))) == 1
    assert (workspace / f"packets/S13/{created.request_id}.json").read_bytes() == accepted_bytes
    assert accepted_bytes == canonical_bytes(owner_packet)


def test_missing_binding(unbound_workspace, request_payload) -> None:
    before = replay(unbound_workspace).revision
    with pytest.raises(HandoffError) as caught:
        _create(unbound_workspace, request_payload(), "missing-binding")
    assert caught.value.code == "configuration_required"
    assert caught.value.exit_code == 5
    assert caught.value.disposition == "configuration_required"
    assert replay(unbound_workspace).revision == before + 1
    resumed = resume_workspace(unbound_workspace)
    assert resumed["revision"] == before + 1
    assert resumed["state"]["bindings"] == {}
    assert resumed["action"]["disposition"] == "configuration_required"
    assert resumed["action"]["missing"] == ["binding:data_platform_skill"]
    assert resumed["state"]["handoffs"]["REQ-request01"]["status"] == \
        "configuration_required"
    assert any(
        item["code"] == "configuration_required"
        and item["subject"] == "REQ-request01"
        and item["governing_asset"] == "platform-operations.json"
        for item in resumed["state"]["findings"]
    )
    recorded_revision = resumed["revision"]
    with pytest.raises(HandoffError) as repeated:
        _create(unbound_workspace, request_payload(), "missing-binding-repeated")
    assert repeated.value.disposition == "configuration_required"
    assert replay(unbound_workspace).revision == recorded_revision


def test_no_result_wait(workspace, request_payload) -> None:
    created = _create(workspace, request_payload(), "create-wait")
    dispatched = handoffs.mark_dispatched(
        workspace,
        request_id=created.request_id,
        dispatch_correlation="dispatch-wait",
        expected_revision=created.revision,
        operation_id="dispatch-wait",
    )
    state = _resume_state(workspace)
    assert dispatched.disposition == "dispatched"
    assert dispatched.revision == created.revision + 1
    assert state["handoffs"][created.request_id]["status"] == "dispatched"
    assert _event_types(workspace, created.request_id) == ["handoff.created", "handoff.dispatched"]
    assert created.packet["operation"] not in state.get("platform_results", {})
    assert not (workspace / f"packets/S13/{created.request_id}.json").exists()


def test_producer_failed(workspace, request_payload, result_packet) -> None:
    created = _create(workspace, request_payload(), "create-failed")
    outcome = _ingest(
        workspace,
        result_packet(created.packet, terminal_status="failed", evidence=[]),
        "ingest-failed",
    )
    assert outcome.disposition == "failed"
    request_bytes = (workspace / f"packets/S12/{created.request_id}.json").read_bytes()
    result_bytes = (workspace / f"packets/S13/{created.request_id}.json").read_bytes()
    retried = handoffs.retry(
        workspace,
        {"request_id": "REQ-failed-retry"},
        prior_request_id=created.request_id,
        expected_revision=outcome.revision,
        operation_id="retry-failed",
    )
    state = _resume_state(workspace)
    assert retried.revision == outcome.revision + 1
    assert state["handoffs"][created.request_id]["status"] == "superseded_by_retry"
    assert state["handoffs"][retried.request_id]["retry_of"] == created.request_id
    assert _event_types(workspace, created.request_id) == [
        "handoff.created", "handoff.dispatched", "handoff.result-ingested",
        "handoff.retry-created"
    ]
    assert (workspace / f"packets/S12/{created.request_id}.json").read_bytes() == request_bytes
    assert (workspace / f"packets/S13/{created.request_id}.json").read_bytes() == result_bytes


def test_producer_cancelled(workspace, request_payload, result_packet) -> None:
    created = _create(workspace, request_payload(), "create-cancelled")
    outcome = _ingest(
        workspace,
        result_packet(created.packet, terminal_status="cancelled", evidence=[]),
        "ingest-cancelled",
    )
    assert outcome.disposition == "cancelled"
    request_bytes = (workspace / f"packets/S12/{created.request_id}.json").read_bytes()
    result_bytes = (workspace / f"packets/S13/{created.request_id}.json").read_bytes()
    retried = handoffs.retry(
        workspace,
        {"request_id": "REQ-cancel-retry"},
        prior_request_id=created.request_id,
        expected_revision=outcome.revision,
        operation_id="retry-cancelled",
    )
    state = _resume_state(workspace)
    assert state["handoffs"][created.request_id]["status"] == "superseded_by_retry"
    assert state["handoffs"][retried.request_id]["retry_of"] == created.request_id
    assert (workspace / f"packets/S12/{created.request_id}.json").read_bytes() == request_bytes
    assert (workspace / f"packets/S13/{created.request_id}.json").read_bytes() == result_bytes


def test_invoke_error(workspace, request_payload, result_packet) -> None:
    created = _create(workspace, request_payload(), "create-invoke-error")
    outcome = handoffs.mark_dispatched(
        workspace,
        request_id=created.request_id,
        dispatch_correlation="invoke-error:adapter-unavailable",
        expected_revision=created.revision,
        operation_id="record-invoke-error",
        coordinator_disposition="invoke_error",
    )
    state = _resume_state(workspace)
    record = state["handoffs"][created.request_id]
    assert outcome.disposition == "reconciliation_required"
    assert outcome.event["event_type"] == "handoff.reconciliation-required"
    assert outcome.event["payload"]["coordinator_disposition"] == "invoke_error"
    assert record["status"] == "reconciliation_required"
    assert record["coordinator_disposition"] == "invoke_error"
    assert _event_types(workspace, created.request_id) == [
        "handoff.created", "handoff.reconciliation-required"
    ]
    with pytest.raises(HandoffError) as direct_ingest:
        _ingest(workspace, result_packet(created.packet), "ingest-after-invoke-error")
    assert direct_ingest.value.code == "reconciliation_required"
    assert direct_ingest.value.disposition == "reconciliation_required"
    assert replay(workspace).revision == outcome.revision
    assert not (workspace / f"packets/S13/{created.request_id}.json").exists()


def test_dispatch_unknown(workspace, request_payload, result_packet) -> None:
    created = _create(workspace, request_payload(), "create-unknown")
    uncertain = handoffs.mark_dispatched(
        workspace,
        request_id=created.request_id,
        dispatch_correlation="dispatch-unknown",
        expected_revision=created.revision,
        operation_id="dispatch-unknown",
        coordinator_disposition="dispatch_unknown",
    )
    assert uncertain.disposition == "reconciliation_required"
    state = _resume_state(workspace)
    assert state["handoffs"][created.request_id]["status"] == "reconciliation_required"
    assert state["handoffs"][created.request_id]["coordinator_disposition"] == "dispatch_unknown"
    assert uncertain.event["payload"]["coordinator_disposition"] == "dispatch_unknown"
    with pytest.raises(HandoffError) as direct_ingest:
        _ingest(workspace, result_packet(created.packet), "ingest-before-reconcile")
    assert direct_ingest.value.code == "reconciliation_required"
    assert direct_ingest.value.disposition == "reconciliation_required"
    assert replay(workspace).revision == uncertain.revision
    assert not (workspace / f"packets/S13/{created.request_id}.json").exists()


def test_stale_or_binding_changed_result(workspace, request_payload, result_packet) -> None:
    for label, security_check in (
        ("missing-principal", _check_missing_producer_principal_is_rejected_without_writes),
        ("substituted-principal", _check_substituted_producer_principal_is_not_trusted),
        ("conflated-principal", _check_configured_skill_name_cannot_be_the_only_producer_principal),
    ):
        isolated = workspace.parent / f"{workspace.name}-{label}"
        shutil.copytree(workspace, isolated, copy_function=shutil.copy2)
        security_check(isolated, request_payload, result_packet)

    with pytest.raises(HandoffError) as changed_create:
        _create(
            workspace,
            request_payload("REQ-wrong01", binding_identity="different-data-platform"),
            "wrong-binding",
        )
    assert changed_create.value.code == "binding_changed"

    created = _create(workspace, request_payload(), "create-correct")
    stale = result_packet(created.packet)
    stale["binding_identity"] = "different-data-platform"
    seal_platform_result(created.packet, stale)
    with pytest.raises(HandoffError) as changed_result:
        _ingest(workspace, stale, "stale-result")
    assert changed_result.value.code == "result_anchor_mismatch"
    assert changed_result.value.disposition == "reconciliation_required"
    assert replay(workspace).revision == created.revision + 2
    state = _resume_state(workspace)
    assert state["handoffs"][created.request_id]["status"] == "reconciliation_required"
    assert _event_types(workspace, created.request_id) == [
        "handoff.created", "handoff.dispatched",
        "handoff.reconciliation-required"
    ]
    assert not (workspace / f"packets/S13/{created.request_id}.json").exists()


def _check_missing_producer_principal_is_rejected_without_writes(
    workspace, request_payload, result_packet,
) -> None:
    created = _create(workspace, request_payload(), "create-missing-principal")
    missing = result_packet(created.packet)
    missing.pop("producer_principal_id")
    _mark_for_result(workspace, result_packet(created.packet), "dispatch-missing-principal")
    before = snapshot_tree(workspace)

    with pytest.raises(HandoffError) as caught:
        _ingest(workspace, missing, "ingest-missing-principal")

    assert caught.value.code == "result_schema_invalid"
    assert snapshot_tree(workspace) == before
    assert not (workspace / f"packets/S13/{created.request_id}.json").exists()


def _check_substituted_producer_principal_is_not_trusted(
    workspace, request_payload, result_packet,
) -> None:
    created = _create(workspace, request_payload(), "create-substituted-principal")
    substituted = result_packet(created.packet)
    substituted["producer_principal_id"] = "substituted-platform-principal"
    seal_platform_result(created.packet, substituted)
    _mark_for_result(workspace, substituted, "dispatch-substituted-principal")
    before = snapshot_tree(workspace)

    with pytest.raises(HandoffError) as caught:
        _ingest(workspace, substituted, "ingest-substituted-principal")

    assert caught.value.code == "key_substitution"
    assert snapshot_tree(workspace) == before
    assert not (workspace / f"packets/S13/{created.request_id}.json").exists()


def _check_configured_skill_name_cannot_be_the_only_producer_principal(
    workspace, request_payload, result_packet,
) -> None:
    created = _create(workspace, request_payload(), "create-binding-as-principal")
    conflated = result_packet(created.packet)
    conflated["producer_principal_id"] = created.packet["binding_identity"]
    seal_platform_result(created.packet, conflated)
    _mark_for_result(workspace, conflated, "dispatch-conflated-principal")
    before = snapshot_tree(workspace)

    with pytest.raises(HandoffError) as caught:
        _ingest(workspace, conflated, "ingest-binding-as-principal")

    assert caught.value.code == "producer_principal_not_independent"
    assert snapshot_tree(workspace) == before
    assert not (workspace / f"packets/S13/{created.request_id}.json").exists()


def test_workspace_revision_is_an_exact_repeated_anchor(
    workspace, request_payload, result_packet,
) -> None:
    created = _create(
        workspace, request_payload("REQ-wrong-revision"),
        "create-revision-bound-request",
    )
    stale = result_packet(created.packet)
    stale["workspace_revision"] = created.packet["workspace_revision"] + 1
    # The packet is genuinely signed by the configured producer over the
    # altered payload. Producer authenticity is not permission to substitute
    # one of the immutable S12 anchors.
    seal_platform_result(created.packet, stale)

    with pytest.raises(HandoffError) as rejected:
        _ingest(workspace, stale, "ingest-wrong-workspace-revision")

    assert rejected.value.code == "result_anchor_mismatch"
    assert rejected.value.disposition == "reconciliation_required"
    state = _resume_state(workspace)
    assert state["handoffs"][created.request_id]["status"] == \
        "reconciliation_required"
    assert "data.quality-check" not in state.get("platform_results", {})
    assert not (workspace / f"packets/S13/{created.request_id}.json").exists()
    assert _event_types(workspace, created.request_id) == [
        "handoff.created", "handoff.dispatched",
        "handoff.reconciliation-required",
    ]


def test_duplicate_conflicting_result(workspace, request_payload, result_packet) -> None:
    created = _create(workspace, request_payload(), "create-conflict")
    accepted = result_packet(created.packet)
    first = _ingest(workspace, accepted, "accept-first")
    conflicting = result_packet(created.packet, result_id="RES-result02")
    conflict = handoffs.ingest(
        workspace,
        conflicting,
        expected_revision=first.revision,
        operation_id="accept-conflict",
    )
    assert conflict.disposition == "reconciliation_required"
    assert conflict.revision == first.revision + 1
    assert _resume_state(workspace)["handoffs"][created.request_id]["status"] == "reconciliation_required"
    assert _event_types(workspace, created.request_id) == [
        "handoff.created", "handoff.dispatched", "handoff.result-ingested",
        "handoff.reconciliation-required"
    ]
    assert read_document(workspace / f"packets/S13/{created.request_id}.json") == accepted


def test_late_result(workspace, request_payload, result_packet) -> None:
    created = _create(workspace, request_payload(), "create-late")
    late_packet = result_packet(created.packet)
    dispatched = _mark_for_result(workspace, late_packet, "dispatch-before-cancel")
    cancelled = handoffs.cancel(
        workspace,
        request_id=created.request_id,
        reason="caller cancelled before delivery",
        expected_revision=dispatched.revision,
        operation_id="cancel-before-result",
    )
    late = handoffs.ingest(
        workspace,
        late_packet,
        expected_revision=cancelled.revision,
        operation_id="ingest-late",
    )
    assert late.disposition == "late_result"
    late_files = list((workspace / "packets/S13-late").glob(f"{created.request_id}-*.json"))
    assert len(late_files) == 1
    assert read_document(late_files[0]) == late_packet
    state = _resume_state(workspace)
    assert state["handoffs"][created.request_id]["status"] == "reconciliation_required"
    assert _event_types(workspace, created.request_id) == [
        "handoff.created", "handoff.dispatched", "handoff.cancelled",
        "handoff.reconciliation-required"
    ]
    assert created.packet["operation"] not in state.get("platform_results", {})


def test_possible_external_success_reconcile(workspace, request_payload, result_packet) -> None:
    created = _create(workspace, request_payload(), "create-reconcile")
    uncertain = handoffs.mark_dispatched(
        workspace,
        request_id=created.request_id,
        dispatch_correlation="dispatch-reconcile",
        expected_revision=created.revision,
        operation_id="dispatch-reconcile",
        coordinator_disposition="dispatch_unknown",
    )
    result = result_packet(created.packet, dispatch_correlation="dispatch-reconcile")
    with pytest.raises(HandoffError) as wrong_correlation:
        handoffs.reconcile(
            workspace,
            {
                "disposition": "effect_committed",
                "dispatch_correlation": "different-dispatch",
                "result": result,
            },
            request_id=created.request_id,
            expected_revision=uncertain.revision,
            operation_id="reconcile-wrong-correlation",
        )
    assert wrong_correlation.value.disposition == "reconciliation_required"
    assert replay(workspace).revision == uncertain.revision

    reconciled = handoffs.reconcile(
        workspace,
        {"disposition": "effect_committed", "dispatch_correlation": "dispatch-reconcile", "result": result},
        request_id=created.request_id,
        expected_revision=uncertain.revision,
        operation_id="reconcile-effect",
    )
    state = _resume_state(workspace)
    assert reconciled.disposition == "effect_committed"
    assert reconciled.revision == uncertain.revision + 1
    assert state["handoffs"][created.request_id]["status"] == "reconciled_effect"
    assert state["handoffs"][created.request_id]["coordinator_disposition"] == "dispatch_unknown"
    assert state["platform_results"][created.packet["operation"]] == "successful"
    assert read_document(workspace / f"packets/S13/{created.request_id}.json") == result


def test_all_interrupted_dispositions(workspace, request_payload, result_packet) -> None:
    cancelled_request = _create(workspace, request_payload("REQ-cancel01"), "create-cancel")
    handoffs.cancel(
        workspace,
        request_id=cancelled_request.request_id,
        reason="cancel",
        expected_revision=replay(workspace).revision,
        operation_id="cancel-request",
    )
    failed_request = _create(workspace, request_payload("REQ-failed01"), "create-failure")
    _ingest(workspace, result_packet(failed_request.packet, terminal_status="failed", evidence=[]), "record-failure")
    uncertain_request = _create(workspace, request_payload("REQ-unknown1"), "create-uncertain")
    handoffs.mark_dispatched(
        workspace,
        request_id=uncertain_request.request_id,
        dispatch_correlation="unknown",
        expected_revision=replay(workspace).revision,
        operation_id="record-uncertain",
        uncertain=True,
    )
    superseded_request = _create(workspace, request_payload("REQ-super001"), "create-superseded")
    handoffs.supersede(
        workspace,
        request_id=superseded_request.request_id,
        successor_request_id="REQ-successor01",
        reason="replacement",
        expected_revision=replay(workspace).revision,
        operation_id="record-superseded",
    )
    statuses = {key: value["status"] for key, value in _state(workspace)["handoffs"].items()}
    assert statuses[cancelled_request.request_id] == "cancelled"
    assert statuses[failed_request.request_id] == "failed"
    assert statuses[uncertain_request.request_id] == "reconciliation_required"
    assert statuses[superseded_request.request_id] == "superseded"


def test_under_bound_subjects_rejected(workspace, request_payload) -> None:
    with pytest.raises(HandoffError) as caught:
        _create(workspace, request_payload(subjects={}), "under-bound")
    assert caught.value.code == "request_subjects_invalid"
    assert not list((workspace / "packets/S12").glob("*.json"))


def test_manual_evidence_bypass_rejected(workspace, request_payload, result_packet) -> None:
    created = _create(workspace, request_payload(), "create-evidence")
    bypass = result_packet(created.packet, evidence=[])
    dispatched = _mark_for_result(workspace, bypass, "dispatch-evidence-bypass")
    with pytest.raises(HandoffError) as caught:
        _ingest(workspace, bypass, "manual-bypass")
    assert caught.value.code == "result_incomplete"
    assert caught.value.disposition == "blocked"
    assert replay(workspace).revision == dispatched.revision
    assert _resume_state(workspace)["handoffs"][created.request_id]["status"] == "dispatched"
    assert not (workspace / f"packets/S13/{created.request_id}.json").exists()


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("wrong-value-type", "result_evidence_value_invalid"),
        ("invalid-digest-format", "result_evidence_value_invalid"),
        ("invalid-uri-format", "result_evidence_value_invalid"),
        ("extra-value-field", "result_evidence_value_invalid"),
        ("missing-value-field", "result_evidence_value_invalid"),
        ("extra-item-field", "result_schema_invalid"),
        ("missing-item-value", "result_schema_invalid"),
        ("unknown-evidence-type", "result_evidence_invalid"),
    ],
)
def test_signed_malformed_evidence_is_rejected_without_writes(
    workspace, request_payload, result_packet, mutation, expected_code,
) -> None:
    created = _create(workspace, request_payload(), f"create-{mutation}")
    malformed = deepcopy(result_packet(created.packet))
    if mutation == "wrong-value-type":
        target = next(
            item for item in malformed["evidence"]
            if item["type"] == "dataset_manifest_digest"
        )
        target["value"] = {"digest": target["value"]}
    elif mutation == "invalid-digest-format":
        target = next(
            item for item in malformed["evidence"]
            if item["type"] == "dataset_manifest_digest"
        )
        target["value"] = "sha256:" + "0" * 63
    elif mutation == "invalid-uri-format":
        target = next(
            item for item in malformed["evidence"]
            if item["type"] == "immutable_uri"
        )
        target["value"] = "relative/producer/path"
    elif mutation == "extra-value-field":
        target = next(
            item for item in malformed["evidence"]
            if item["type"] == "dataset_manifest"
        )
        target["value"]["producer_note"] = "signed but forbidden"
    elif mutation == "missing-value-field":
        target = next(
            item for item in malformed["evidence"]
            if item["type"] == "dataset_manifest"
        )
        target["value"].pop("content_digest")
    elif mutation == "extra-item-field":
        malformed["evidence"][0]["producer_note"] = "signed but forbidden"
    elif mutation == "missing-item-value":
        malformed["evidence"][0].pop("value")
    else:
        malformed["evidence"][-1]["type"] = "fabricated_evidence"
    seal_platform_result(created.packet, malformed)
    _mark_for_result(
        workspace, malformed, f"dispatch-{mutation}",
    )
    before = snapshot_tree(workspace)

    with pytest.raises(HandoffError) as caught:
        _ingest(workspace, malformed, f"ingest-{mutation}")

    assert caught.value.code == expected_code
    expected_disposition = "blocked" \
        if expected_code.startswith("result_evidence") else "fail"
    assert caught.value.disposition == expected_disposition
    assert snapshot_tree(workspace) == before
    assert not (workspace / f"packets/S13/{created.request_id}.json").exists()


def test_signed_subject_bound_evidence_substitution_is_blocked_without_writes(
    workspace, request_payload, result_packet,
) -> None:
    subjects = {
        "artifact_id": "dataset-v1",
        "dataset_manifest_digest": digest({"dataset_manifest": 1}),
        "split_manifest_digest": digest({"split_manifest": 1}),
        "calibration_manifest_digest_when_applicable": "not-applicable",
        "evaluator_digest": digest({"evaluator": 1}),
        "baseline_lock_digest": "not-applicable",
    }
    created = _create(
        workspace,
        request_payload(subjects=subjects, step=7),
        "create-subject-bound-evidence",
    )
    substituted = deepcopy(result_packet(created.packet))
    target = next(
        item for item in substituted["evidence"]
        if item["type"] == "evaluator_digest"
    )
    target["value"] = digest({"different_evaluator": 1})
    seal_platform_result(created.packet, substituted)
    _mark_for_result(
        workspace, substituted, "dispatch-subject-bound-evidence",
    )
    before = snapshot_tree(workspace)

    with pytest.raises(HandoffError) as caught:
        _ingest(
            workspace, substituted, "ingest-subject-bound-evidence",
        )

    assert caught.value.code == "result_subject_evidence_mismatch"
    assert caught.value.disposition == "blocked"
    assert snapshot_tree(workspace) == before
    assert not (workspace / f"packets/S13/{created.request_id}.json").exists()


def test_tampered_s12_cannot_downgrade_evidence_cardinality(
    workspace, request_payload, result_packet,
) -> None:
    created = _create(workspace, request_payload(), "create-s12-tamper")
    result = result_packet(created.packet)
    _mark_for_result(workspace, result, "dispatch-s12-tamper")
    request_path = workspace / f"packets/S12/{created.request_id}.json"
    tampered = deepcopy(created.packet)
    tampered["evidence_cardinality"]["default"]["max"] = 100
    request_path.write_bytes(canonical_bytes(tampered))
    before = snapshot_tree(workspace)

    with pytest.raises(HandoffError) as caught:
        _ingest(workspace, result, "ingest-s12-tamper")

    assert caught.value.code == "request_packet_digest_mismatch"
    assert caught.value.disposition == "blocked"
    assert snapshot_tree(workspace) == before
    assert not (workspace / f"packets/S13/{created.request_id}.json").exists()


def test_successful_result_cannot_exceed_pinned_evidence_cardinality(
    workspace, request_payload, result_packet,
) -> None:
    created = _create(workspace, request_payload(), "create-cardinality")
    evidence = deepcopy(result_packet(created.packet)["evidence"])
    evidence.append(dict(evidence[0]))
    duplicated = result_packet(created.packet, evidence=evidence)
    _mark_for_result(workspace, duplicated, "dispatch-duplicate-evidence")
    before = snapshot_tree(workspace)

    with pytest.raises(HandoffError) as caught:
        _ingest(workspace, duplicated, "ingest-duplicate-evidence")

    assert caught.value.code == "result_incomplete"
    assert caught.value.disposition == "blocked"
    assert snapshot_tree(workspace) == before


def test_manual_complete_result_with_recomputed_receipt_still_requires_producer_signature(
    workspace, request_payload, result_packet,
) -> None:
    created = _create(workspace, request_payload(), "create-signed-evidence")
    forged = deepcopy(result_packet(created.packet))
    forged["evidence"][0]["value"] = "manually-fabricated-complete-evidence"
    forged.pop("receipt_digest")
    forged["receipt_digest"] = digest(forged)
    _mark_for_result(workspace, forged, "dispatch-forged-evidence")

    before = replay(workspace).revision
    with pytest.raises(HandoffError) as caught:
        _ingest(workspace, forged, "ingest-manual-recomputed-receipt")
    assert caught.value.code == "attestation_subject_mismatch"
    assert replay(workspace).revision == before
    assert not (workspace / f"packets/S13/{created.request_id}.json").exists()


def test_retry_supersession_preserve_history(workspace, request_payload, result_packet) -> None:
    original = _create(workspace, request_payload(), "create-original")
    failed = _ingest(
        workspace,
        result_packet(original.packet, terminal_status="failed", evidence=[]),
        "fail-original",
    )
    original_bytes = (workspace / f"packets/S12/{original.request_id}.json").read_bytes()
    retried = handoffs.retry(
        workspace,
        {"request_id": "REQ-request02"},
        prior_request_id=original.request_id,
        expected_revision=failed.revision,
        operation_id="retry-new-identity",
    )
    state = _state(workspace)
    assert retried.request_id == "REQ-request02"
    assert retried.packet["retry_of"] == original.request_id
    assert state["handoffs"][original.request_id]["status"] == "superseded_by_retry"
    assert state["handoffs"][retried.request_id]["retry_of"] == original.request_id
    assert (workspace / f"packets/S12/{original.request_id}.json").read_bytes() == original_bytes


def test_blind_uncertain_redispatch_rejected(workspace, request_payload) -> None:
    created = _create(workspace, request_payload(), "create-blind")
    uncertain = handoffs.mark_dispatched(
        workspace,
        request_id=created.request_id,
        dispatch_correlation="unknown-first",
        expected_revision=created.revision,
        operation_id="unknown-first",
        coordinator_disposition="dispatch_unknown",
    )
    with pytest.raises(HandoffError) as caught:
        handoffs.mark_dispatched(
            workspace,
            request_id=created.request_id,
            dispatch_correlation="blind-redelivery",
            expected_revision=uncertain.revision,
            operation_id="blind-redelivery",
        )
    assert caught.value.code == "dispatch_state_invalid"
    assert caught.value.disposition == "blocked"
    assert replay(workspace).revision == uncertain.revision
    state = _resume_state(workspace)
    assert state["handoffs"][created.request_id]["status"] == "reconciliation_required"
    assert state["handoffs"][created.request_id]["coordinator_disposition"] == "dispatch_unknown"
