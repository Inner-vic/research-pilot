"""Secure Resume authentication for formally anchored workspaces."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

import pytest

import handoffs
import workspace as workspace_module
from canonical import digest
from journal import WorkspaceLock
from ledger import replay
from producer_signing import producer_trust_fields, seal_platform_result
from workspace import (
    WorkspaceError,
    create_workspace,
    mutate,
    resume_workspace,
    snapshot_tree,
)


def _persist_success(
    workspace: Path,
    asset_bundle: Any,
    *,
    binding_identity: str,
    binding_digest: str,
    operation: str,
    request_id: str,
    result_id: str,
    evidence: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], Path]:
    _, operation_contract, _ = asset_bundle.operation_contract(operation)
    created = handoffs.create(
        workspace,
        {
            "request_id": request_id,
            "operation": operation,
            "binding_identity": binding_identity,
            "binding_skill_digest": binding_digest,
            "adapter_contract_digest": digest(operation_contract),
            "gate": "resume-current-anchor",
            "step": 21,
            "subjects": {"artifact_id": f"anchor:{request_id}"},
            "expected_evidence": list(operation_contract["required_evidence"]),
            "evidence_cardinality": copy.deepcopy(
                operation_contract["evidence_cardinality"],
            ),
            "portable_paths": [],
        },
        expected_revision=replay(workspace).revision,
        operation_id=f"create-{request_id}",
    )
    request = created.packet
    assert request is not None
    correlation = f"dispatch:{request_id}"
    dispatched = handoffs.mark_dispatched(
        workspace,
        request_id=request_id,
        dispatch_correlation=correlation,
        expected_revision=created.revision,
        operation_id=f"dispatch-{request_id}",
    )
    evidence_values = evidence(request)
    result = {
        "schema": "sdm-handoff-result/v1",
        "result_id": result_id,
        "request_id": request["request_id"],
        "operation": request["operation"],
        "binding_identity": request["binding_identity"],
        "binding_skill_digest": request["binding_skill_digest"],
        "adapter_contract_digest": request["adapter_contract_digest"],
        "workspace_id": request["workspace_id"],
        "workspace_revision": request["workspace_revision"],
        "gate": request["gate"],
        "step": request["step"],
        "subjects": request["subjects"],
        "idempotency_key": request["idempotency_key"],
        "dispatch_correlation": correlation,
        "producer_identity": binding_identity,
        "terminal_status": "successful",
        "evidence": [
            {"type": name, "value": evidence_values[name]}
            for name in request["expected_evidence"]
        ],
        "completed_at": request["created_at"],
    }
    seal_platform_result(request, result)
    ingested = handoffs.ingest(
        workspace,
        result,
        expected_revision=dispatched.revision,
        operation_id=f"ingest-{request_id}",
    )
    assert ingested.disposition == "accepted"
    return result, workspace / "packets" / "S13" / f"{request_id}.json"


def _formal_workspace(tmp_path: Path, asset_bundle: Any) -> tuple[Path, Path, Path]:
    workspace = tmp_path / "secure-resume"
    workspace_id = "secure-resume"
    binding_identity = "fake-source-control-platform"
    binding_digest = digest({"skill": binding_identity})
    create_workspace(
        workspace,
        {
            "workspace_id": workspace_id,
            "protocol": "formal",
            "bindings": {
                "source_control_platform_skill": binding_identity,
            },
            **producer_trust_fields(),
            "binding_skill_digests": {
                "source_control_platform_skill": binding_digest,
            },
        },
        expected_revision=0,
        operation_id="create-secure-resume",
        assets_root=asset_bundle.root,
    )
    base = replay(workspace)
    anchor_operation_id = "secure-resume-anchor-operation"
    provisional = mutate(
        workspace,
        expected_revision=base.revision,
        operation_id="secure-resume-provisional",
        event_type="decision.provisional",
        subjects={"workspace_id": workspace_id},
        payload={
            "outcome": "inconclusive",
            "anchor_operation_id": anchor_operation_id,
        },
    )
    anchor_result, anchor_path = _persist_success(
        workspace,
        asset_bundle,
        binding_identity=binding_identity,
        binding_digest=binding_digest,
        operation="git.anchor-ledger-head",
        request_id="REQ-resumeanchor01",
        result_id="RES-resumeanchor01",
        evidence=lambda _request: {
            "repository_identity": "repository:secure-resume",
            "workspace_identity": workspace_id,
            "operation_id": anchor_operation_id,
            "bound_provisional_ledger_head": provisional.event["event_hash"],
            "prior_trusted_anchor": digest(base.genesis),
            "external_head_identity": provisional.event["event_hash"],
            "operation_receipt_uri": "receipt://secure-resume/anchor",
            "operation_receipt_digest": digest({"receipt": "resume-anchor"}),
            "terminal_status": "successful",
        },
    )
    accepted = mutate(
        workspace,
        expected_revision=replay(workspace).revision,
        operation_id="secure-resume-accept-anchor",
        event_type="anchor.accepted",
        subjects={"workspace_id": workspace_id},
        payload={
            "provisional_hash": provisional.event["event_hash"],
            "anchored_head": provisional.event["event_hash"],
            "receipt_digest": digest(anchor_result),
            "request_id": "REQ-resumeanchor01",
            "result_digest": digest(anchor_result),
        },
    )
    expected_anchor = accepted.state["trusted_anchor"]["event_hash"]
    _, current_path = _persist_success(
        workspace,
        asset_bundle,
        binding_identity=binding_identity,
        binding_digest=binding_digest,
        operation="git.read-current-ledger-anchor",
        request_id="REQ-resumecurrent01",
        result_id="RES-resumecurrent01",
        evidence=lambda request: {
            "repository_identity": "repository:secure-resume",
            "workspace_identity": workspace_id,
            "external_head_identity": expected_anchor,
            "current_ledger_anchor": expected_anchor,
            "observed_at": request["created_at"],
            "operation_receipt_uri": "receipt://secure-resume/current",
            "operation_receipt_digest": digest({"receipt": "resume-current"}),
            "terminal_status": "successful",
        },
    )
    return workspace, current_path, anchor_path


def _resume_error(workspace: Path, trusted: Path, code: str) -> WorkspaceError:
    before = snapshot_tree(workspace)
    with pytest.raises(WorkspaceError) as caught:
        resume_workspace(workspace, trusted)
    assert caught.value.code == code
    assert caught.value.exit_code == 3
    assert caught.value.findings
    assert all(item["gate_impact"] == "block" for item in caught.value.findings)
    assert snapshot_tree(workspace) == before
    return caught.value


def test_secure_resume_accepts_only_exact_current_ledger_bound_s13(
    tmp_path, asset_bundle
) -> None:
    workspace, current_path, _ = _formal_workspace(tmp_path, asset_bundle)
    before = snapshot_tree(workspace)
    resumed = resume_workspace(workspace, current_path)
    assert resumed["revision"] == replay(workspace).revision
    assert resumed["state"]["ledger_head"] == replay(workspace).head_hash
    assert snapshot_tree(workspace) == before


def test_secure_resume_rejects_forged_external_and_substituted_packet_paths(
    tmp_path, asset_bundle
) -> None:
    workspace, current_path, anchor_path = _formal_workspace(tmp_path, asset_bundle)
    forged = tmp_path / "forged-current-S13.json"
    forged.write_bytes(current_path.read_bytes())
    _resume_error(workspace, forged, "trusted_anchor_unbound")
    _resume_error(workspace, anchor_path, "trusted_anchor_operation_mismatch")


def test_secure_resume_rejects_stale_but_previously_valid_s13(
    tmp_path, asset_bundle
) -> None:
    workspace, current_path, _ = _formal_workspace(tmp_path, asset_bundle)
    current = replay(workspace)
    mutate(
        workspace,
        expected_revision=current.revision,
        operation_id="advance-after-resume-observation",
        event_type="substate.recorded",
        subjects={"workspace_id": current.genesis["workspace_id"]},
        payload={"name": "after-resume-observation", "value": True},
    )
    _resume_error(workspace, current_path, "trusted_anchor_stale")


def test_secure_resume_uses_shared_lock_and_rejects_concurrent_change(
    tmp_path, asset_bundle, monkeypatch
) -> None:
    workspace, current_path, _ = _formal_workspace(tmp_path, asset_bundle)
    with WorkspaceLock(workspace, exclusive=True):
        with pytest.raises(WorkspaceError) as locked:
            resume_workspace(workspace, current_path)
    assert locked.value.code == "lock_conflict"
    assert locked.value.exit_code == 4

    real_snapshot = workspace_module.snapshot_tree
    calls = 0

    def changed_snapshot(root: Path):
        nonlocal calls
        calls += 1
        value = real_snapshot(root)
        if calls == 2:
            value = copy.deepcopy(value)
            value["entries"]["state.json"]["mtime_ns"] += 1
        return value

    before = snapshot_tree(workspace)
    with monkeypatch.context() as scoped:
        scoped.setattr(workspace_module, "snapshot_tree", changed_snapshot)
        with pytest.raises(WorkspaceError) as concurrent:
            resume_workspace(workspace, current_path)
    assert calls == 2
    assert concurrent.value.code == "concurrent_mutation"
    assert concurrent.value.exit_code == 4
    assert snapshot_tree(workspace) == before
