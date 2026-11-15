from __future__ import annotations

import copy
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import handoffs
import workspace as workspace_module
from canonical import canonical_bytes, digest
from ledger import replay
from producer_signing import producer_trust_fields, seal_platform_result
from workspace import WorkspaceError, create_workspace, mutate, publish_artifact, snapshot_tree, validate_workspace


def _raw_snapshot(root: Path) -> dict[str, tuple]:
    entries: dict[str, tuple] = {}

    def walk(directory: Path, prefix: str = "") -> None:
        with os.scandir(directory) as scan:
            children = sorted(scan, key=lambda item: item.name)
        for child in children:
            relative = f"{prefix}/{child.name}" if prefix else child.name
            info = child.stat(follow_symlinks=False)
            payload = Path(child.path).read_bytes() if stat.S_ISREG(info.st_mode) else None
            target = os.readlink(child.path) if stat.S_ISLNK(info.st_mode) else None
            entries[relative] = (
                info.st_mode,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
                info.st_dev,
                info.st_ino,
                payload,
                target,
            )
            if stat.S_ISDIR(info.st_mode):
                walk(Path(child.path), relative)

    walk(root)
    return entries


def _assert_validation_rejects_without_writes(workspace: Path, code: str) -> None:
    before = _raw_snapshot(workspace)
    with pytest.raises(WorkspaceError) as caught:
        validate_workspace(workspace)
    assert caught.value.code == code
    assert _raw_snapshot(workspace) == before


def test_validate_snapshot_unchanged(workspace, tmp_path, valid_artifact, monkeypatch) -> None:
    before = snapshot_tree(workspace)
    first = validate_workspace(workspace)
    middle = snapshot_tree(workspace)
    second = validate_workspace(workspace)
    after = snapshot_tree(workspace)
    assert first["valid"] is True
    assert second["valid"] is True
    assert first["snapshot_digest"] == second["snapshot_digest"]
    assert before == middle == after

    selected_before = snapshot_tree(workspace)
    selected = validate_workspace(workspace, artifact_path=workspace / "state.json")
    assert selected["valid"] is True
    assert snapshot_tree(workspace) == selected_before

    base = replay(workspace)
    artifact = valid_artifact("G01", "io08-tamper")
    publish_artifact(
        workspace,
        artifact_id="G01",
        instance_key="io08-tamper",
        value=artifact,
        expected_revision=base.revision,
        operation_id="publish-io08-tamper",
    )
    tampered_path = workspace / "artifacts/G01/io08-tamper.json"
    tampered = {**artifact, "created_by": "tamperer"}
    tampered_path.write_bytes(canonical_bytes(tampered))
    tampered_before = snapshot_tree(workspace)
    reported = validate_workspace(workspace, artifact_path=tampered_path)
    assert reported["valid"] is False
    assert any(item["code"] == "artifact_digest_mismatch" for item in reported["findings"])
    assert snapshot_tree(workspace) == tampered_before
    symlink = workspace / "unsafe-symlink"
    symlink.symlink_to(tmp_path)
    _assert_validation_rejects_without_writes(workspace, "symlink_escape")
    symlink.unlink()

    hardlink = workspace / "unsafe-hardlink"
    os.link(workspace / "_lock", hardlink)
    _assert_validation_rejects_without_writes(workspace, "hardlink_escape")
    hardlink.unlink()

    fifo = workspace / "unsafe-fifo"
    os.mkfifo(fifo)
    _assert_validation_rejects_without_writes(workspace, "special_node")
    fifo.unlink()

    mount_like = workspace / "mount-like"
    mount_like.mkdir()
    real_lstat = workspace_module.Path.lstat

    def cross_device_lstat(path: Path):
        info = real_lstat(path)
        if path == mount_like:
            return SimpleNamespace(
                st_dev=info.st_dev + 1,
                st_ino=info.st_ino,
                st_mode=info.st_mode,
                st_mtime_ns=info.st_mtime_ns,
                st_ctime_ns=info.st_ctime_ns,
            )
        return info

    mount_before = _raw_snapshot(workspace)
    with monkeypatch.context() as scoped:
        scoped.setattr(workspace_module.Path, "lstat", cross_device_lstat)
        with pytest.raises(WorkspaceError) as mounted:
            validate_workspace(workspace)
        assert mounted.value.code == "mount_escape"
    assert _raw_snapshot(workspace) == mount_before

    real_fstat = workspace_module.os.fstat
    identity_changed = False

    def changed_identity(fd: int):
        nonlocal identity_changed
        info = real_fstat(fd)
        if not identity_changed and stat.S_ISREG(info.st_mode):
            identity_changed = True
            return SimpleNamespace(
                st_dev=info.st_dev,
                st_ino=info.st_ino + 1,
                st_mode=info.st_mode,
                st_size=info.st_size,
                st_mtime_ns=info.st_mtime_ns,
                st_ctime_ns=info.st_ctime_ns,
            )
        return info

    identity_before = _raw_snapshot(workspace)
    with monkeypatch.context() as scoped:
        scoped.setattr(workspace_module.os, "fstat", changed_identity)
        with pytest.raises(WorkspaceError) as unstable:
            validate_workspace(workspace)
        assert unstable.value.code == "concurrent_mutation"
    assert identity_changed is True
    assert _raw_snapshot(workspace) == identity_before

    real_snapshot_tree = workspace_module.snapshot_tree
    snapshot_calls = 0

    def concurrent_snapshot(root: Path):
        nonlocal snapshot_calls
        snapshot_calls += 1
        value = real_snapshot_tree(root)
        if snapshot_calls == 2:
            value = copy.deepcopy(value)
            value["entries"]["state.json"]["size"] += 1
        return value

    concurrent_before = _raw_snapshot(workspace)
    with monkeypatch.context() as scoped:
        scoped.setattr(workspace_module, "snapshot_tree", concurrent_snapshot)
        with pytest.raises(WorkspaceError) as concurrent:
            validate_workspace(workspace)
        assert concurrent.value.code == "concurrent_mutation"
        assert concurrent.value.exit_code == 4
    assert snapshot_calls == 2
    assert _raw_snapshot(workspace) == concurrent_before


def test_validate_and_resume_both_reject_materialized_lineage_findings(
    workspace, valid_artifact,
) -> None:
    base = replay(workspace)
    artifact = valid_artifact("G01", "dangling-lineage")
    artifact["subjects"] = {"workspace_id": base.genesis["workspace_id"]}
    publish_artifact(
        workspace,
        artifact_id="G01",
        instance_key="dangling-lineage",
        value=artifact,
        parents=["G01:missing-parent"],
        expected_revision=base.revision,
        operation_id="publish-dangling-lineage",
    )
    before = snapshot_tree(workspace)
    checked = validate_workspace(workspace)
    assert checked["valid"] is False
    assert "lineage_dangling_endpoint" in {
        item["code"] for item in checked["findings"]
    }
    with pytest.raises(WorkspaceError) as caught:
        workspace_module.resume_workspace(workspace)
    assert caught.value.code == "lineage_dangling_endpoint"
    assert "lineage_dangling_endpoint" in {
        item["code"] for item in caught.value.findings
    }
    assert snapshot_tree(workspace) == before


def test_validation_refuses_to_recover_unfinished_mutation(workspace) -> None:
    journal = workspace / "_journal.json"
    journal.write_bytes(canonical_bytes({"schema": "sdm-journal/v1", "operation_id": "unfinished", "phase": "prepared", "writes": []}))
    before = snapshot_tree(workspace)
    with pytest.raises(WorkspaceError) as caught:
        validate_workspace(workspace)
    assert caught.value.code == "recovery_required"
    assert caught.value.exit_code == 3
    assert snapshot_tree(workspace) == before
    assert journal.exists()


def test_validation_reports_tampered_artifact_without_repair(workspace, valid_artifact) -> None:
    base = replay(workspace)
    artifact = valid_artifact("G01", "governance")
    published = publish_artifact(
        workspace,
        artifact_id="G01",
        instance_key="governance",
        value=artifact,
        expected_revision=base.revision,
        operation_id="publish-governance",
    )
    path = workspace / "artifacts/G01/governance.json"
    changed = dict(artifact)
    changed["created_by"] = "tamperer"
    path.write_bytes(canonical_bytes(changed))
    before = snapshot_tree(workspace)
    result = validate_workspace(workspace)
    assert result["valid"] is False
    assert any(item["code"] == "artifact_digest_mismatch" for item in result["findings"])
    assert snapshot_tree(workspace) == before
    assert path.read_bytes() == canonical_bytes(changed)


def test_validate_replay_rejects_owner_schema_invalid_persisted_artifact_without_writes(
    workspace,
    valid_artifact,
) -> None:
    base = replay(workspace)
    artifact = valid_artifact("G01", "owner-invalid")
    publish_artifact(
        workspace,
        artifact_id="G01",
        instance_key="owner-invalid",
        value=artifact,
        expected_revision=base.revision,
        operation_id="publish-owner-valid",
    )
    path = workspace / "artifacts/G01/owner-invalid.json"
    artifact["content"].pop("principles")
    artifact["content_digest"] = digest(artifact["content"])
    path.write_bytes(canonical_bytes(artifact))
    before = snapshot_tree(workspace)
    result = validate_workspace(workspace)
    assert result["valid"] is False
    assert any(
        item["code"] == "schema_composition"
        and item["governing_asset"]
        == "sdm-create-ml-contract/assets/contract-record.schema.json"
        for item in result["findings"]
    )
    assert snapshot_tree(workspace) == before


def test_validate_and_resume_reverify_every_persisted_s13_producer_signature(
    workspace,
    request_payload,
    result_packet,
) -> None:
    created = handoffs.create(
        workspace,
        request_payload(request_id="REQ-replay-signature"),
        expected_revision=replay(workspace).revision,
        operation_id="create-replay-signature",
    )
    correlation = "dispatch-replay-signature"
    dispatched = handoffs.mark_dispatched(
        workspace,
        request_id=created.request_id,
        dispatch_correlation=correlation,
        expected_revision=created.revision,
        operation_id="dispatch-replay-signature",
    )
    result = result_packet(
        created.packet,
        dispatch_correlation=correlation,
    )
    ingested = handoffs.ingest(
        workspace,
        result,
        expected_revision=dispatched.revision,
        operation_id="ingest-replay-signature",
    )
    assert ingested.disposition == "accepted"

    path = workspace / "packets" / "S13" / f"{created.request_id}.json"
    forged = json.loads(path.read_bytes())
    signature = forged["producer_attestation"]["signature"]
    forged["producer_attestation"]["signature"] = (
        ("A" if signature[0] != "A" else "B") + signature[1:]
    )
    unsigned = dict(forged)
    unsigned.pop("receipt_digest")
    forged["receipt_digest"] = digest(unsigned)
    path.write_bytes(canonical_bytes(forged))
    before = snapshot_tree(workspace)

    validated = validate_workspace(workspace)
    assert validated["valid"] is False
    assert any(item["code"] == "signature_invalid" for item in validated["findings"])
    assert snapshot_tree(workspace) == before

    with pytest.raises(WorkspaceError) as caught:
        workspace_module.resume_workspace(workspace)
    assert any(item["code"] == "signature_invalid" for item in caught.value.findings)
    assert snapshot_tree(workspace) == before


def test_formal_anchor_requires_fresh_external_read_result(tmp_path, asset_bundle) -> None:
    workspace = tmp_path / "formal-anchor"
    workspace_id = "formal-anchor"
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
                "binding_skill_digests": {
                    "source_control_platform_skill": binding_digest,
                },
                **producer_trust_fields(),
            },
        expected_revision=0,
        operation_id="create-formal-anchor",
        assets_root=asset_bundle.root,
    )

    def ingest_result(
        operation: str,
        request_id: str,
        result_id: str,
        evidence_values: dict[str, object],
    ) -> tuple[dict, dict, Path]:
        _, operation_contract, _ = asset_bundle.operation_contract(operation)
        created = handoffs.create(
            workspace,
            {
                "request_id": request_id,
                "operation": operation,
                "binding_identity": binding_identity,
                "binding_skill_digest": binding_digest,
                "adapter_contract_digest": digest(operation_contract),
                "gate": "formal-anchor-current",
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
        correlation = f"dispatch:{request_id}"
        dispatched = handoffs.mark_dispatched(
            workspace,
            request_id=request_id,
            dispatch_correlation=correlation,
            expected_revision=created.revision,
            operation_id=f"dispatch-{request_id}",
        )
        request = created.packet
        assert request is not None
        assert dispatched.event is not None
        completed_at = dispatched.event["occurred_at"]
        evidence_values = copy.deepcopy(evidence_values)
        if operation == "git.read-current-ledger-anchor":
            evidence_values["observed_at"] = completed_at
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
            "completed_at": completed_at,
        }
        seal_platform_result(request, result)
        ingested = handoffs.ingest(
            workspace,
            result,
            expected_revision=dispatched.revision,
            operation_id=f"ingest-{request_id}",
        )
        assert ingested.disposition == "accepted"
        return request, result, workspace / "packets" / "S13" / f"{request_id}.json"

    base = replay(workspace)
    anchor_operation_id = "formal-anchor-operation"
    provisional = mutate(
        workspace,
        expected_revision=base.revision,
        operation_id="provisional",
        event_type="decision.provisional",
        subjects={"workspace_id": base.genesis["workspace_id"]},
        payload={
            "outcome": "inconclusive",
            "anchor_operation_id": anchor_operation_id,
        },
    )
    _, anchor_result, _ = ingest_result(
        "git.anchor-ledger-head",
        "REQ-anchor01",
        "RES-anchor01",
        {
            "repository_identity": "repository:formal-anchor",
            "workspace_identity": workspace_id,
            "operation_id": anchor_operation_id,
            "bound_provisional_ledger_head": provisional.event["event_hash"],
            "prior_trusted_anchor": digest(base.genesis),
            "external_head_identity": provisional.event["event_hash"],
            "operation_receipt_uri": "receipt://anchor/formal-anchor",
            "operation_receipt_digest": digest({"receipt": "anchor"}),
            "terminal_status": "successful",
        },
    )
    accepted = mutate(
        workspace,
        expected_revision=replay(workspace).revision,
        operation_id="accept-anchor",
        event_type="anchor.accepted",
        subjects={"workspace_id": base.genesis["workspace_id"]},
        payload={
            "provisional_hash": provisional.event["event_hash"],
            "anchored_head": provisional.event["event_hash"],
            "receipt_digest": digest(anchor_result),
            "request_id": "REQ-anchor01",
            "result_digest": digest(anchor_result),
        },
    )
    missing = validate_workspace(workspace)
    assert missing["valid"] is False
    assert any(item["code"] == "trusted_anchor_required" for item in missing["findings"])
    assert all(item["gate_impact"] == "block" for item in missing["findings"])

    expected_anchor = accepted.state["trusted_anchor"]["event_hash"]

    def read_anchor(
        suffix: str,
        *,
        repository_identity: str = "repository:formal-anchor",
        external_head_identity: str = expected_anchor,
        current_ledger_anchor: str = expected_anchor,
    ) -> tuple[dict, Path]:
        _, result, path = ingest_result(
            "git.read-current-ledger-anchor",
            f"REQ-read{suffix}",
            f"RES-read{suffix}",
            {
                "repository_identity": repository_identity,
                "workspace_identity": workspace_id,
                "external_head_identity": external_head_identity,
                "current_ledger_anchor": current_ledger_anchor,
                "operation_receipt_uri": f"receipt://read/{suffix}",
                "operation_receipt_digest": digest({"receipt": suffix}),
                "terminal_status": "successful",
            },
        )
        return result, path

    fresh_result, result_path = read_anchor("0001")
    before = snapshot_tree(workspace)
    checked = validate_workspace(workspace, trusted_anchor_path=result_path)
    assert checked["valid"] is True
    assert checked["findings"] == []
    assert snapshot_tree(workspace) == before

    copied = tmp_path / "copied-valid-S13.json"
    copied.write_bytes(canonical_bytes(fresh_result))
    unbound = validate_workspace(workspace, trusted_anchor_path=copied)
    assert unbound["valid"] is False
    assert any(item["code"] == "trusted_anchor_unbound" for item in unbound["findings"])

    mutate(
        workspace,
        expected_revision=replay(workspace).revision,
        operation_id="advance-after-observation",
        event_type="substate.recorded",
        subjects={"workspace_id": workspace_id},
        payload={"name": "after-observation", "value": True},
    )
    stale = validate_workspace(workspace, trusted_anchor_path=result_path)
    assert stale["valid"] is False
    assert any(item["code"] == "trusted_anchor_stale" for item in stale["findings"])

    _, shorter_path = read_anchor(
        "0002",
        external_head_identity=provisional.event["previous_hash"],
        current_ledger_anchor=provisional.event["previous_hash"],
    )
    shorter = validate_workspace(workspace, trusted_anchor_path=shorter_path)
    assert shorter["valid"] is False
    assert any(item["code"] == "trusted_anchor_mismatch" for item in shorter["findings"])

    _, different_path = read_anchor(
        "0003",
        repository_identity="repository:different-history",
    )
    different = validate_workspace(workspace, trusted_anchor_path=different_path)
    assert different["valid"] is False
    assert any(item["code"] == "trusted_anchor_mismatch" for item in different["findings"])

    exact_result, exact_path = read_anchor("0004")
    mismatched_result = copy.deepcopy(exact_result)
    mismatched_result["subjects"]["artifact_id"] = "anchor:mismatched-subject"
    exact_path.write_bytes(canonical_bytes(mismatched_result))
    mismatch_before = snapshot_tree(workspace)
    mismatched = validate_workspace(workspace, trusted_anchor_path=exact_path)
    assert mismatched["valid"] is False
    assert any(item["code"] == "trusted_anchor_invalid" for item in mismatched["findings"])
    assert all(item["gate_impact"] == "block" for item in mismatched["findings"])
    assert snapshot_tree(workspace) == mismatch_before


def test_selected_artifact_must_be_inside_workspace(workspace, tmp_path) -> None:
    selected_before = snapshot_tree(workspace)
    selected = validate_workspace(workspace, artifact_path=workspace / "state.json")
    assert selected["valid"] is True
    assert snapshot_tree(workspace) == selected_before

    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    before = snapshot_tree(workspace)
    with pytest.raises(WorkspaceError) as caught:
        validate_workspace(workspace, artifact_path=outside)
    assert caught.value.code == "artifact_escape"
    assert snapshot_tree(workspace) == before
