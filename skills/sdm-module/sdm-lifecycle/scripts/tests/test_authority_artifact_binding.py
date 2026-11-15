"""Persisted authority subjects must reconstruct exact final artifact bytes."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys

import pytest


MODULE = Path(__file__).resolve().parents[3]
MODULE_TESTS = MODULE / "tests"
SCRIPTS = MODULE / "sdm-lifecycle" / "scripts"
for root in (MODULE_TESTS, SCRIPTS):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import test_matrix_integration_contracts as matrix  # noqa: E402
import handoffs  # noqa: E402
from authority import AuthorityError  # noqa: E402
from canonical import (  # noqa: E402
    candidate_digest, canonical_bytes, digest, read_document,
)
from contracts import AssetBundle  # noqa: E402
from ledger import (  # noqa: E402
    LedgerError, _validate_event_authority, event_hash, replay,
)
from projection import materialize  # noqa: E402
from producer_signing import seal_platform_result, verifier_record  # noqa: E402
from workspace import (  # noqa: E402
    WorkspaceError, _memlog, mutate, publish_artifact, resume_workspace,
    snapshot_tree, validate_workspace,
)


def _authority_shape_event(
    *, transition: str, event_type: str, action: str,
    artifact_ids: list[str], governed_ids: dict[str, str],
    signed_digests: dict[str, str], payload_extra: dict | None = None,
) -> dict:
    descriptors = [
        {
            "artifact_id": artifact_id,
            "instance_key": f"{artifact_id.lower()}-instance",
            "path": f"artifacts/{artifact_id}/{artifact_id.lower()}-instance.json",
            "digest": digest({"final": artifact_id}),
            "parents": [], "status": "published",
        }
        for artifact_id in artifact_ids
    ]
    subjects = {
        key: value for key, value in governed_ids.items()
        if key not in {"step", "outcome"}
    }
    payload = {
        "transition": transition,
        "authority_action": action,
        "authority_subject": {
            "governed_ids": governed_ids,
            "digests": signed_digests,
        },
        "authority_expected_revision": 0,
        "consumed_at": "2026-01-01T00:00:00Z",
        "published_artifacts": descriptors,
    }
    payload.update(payload_extra or {})
    return {
        "sequence": 1,
        "event_type": event_type,
        "subjects": subjects,
        "payload": payload,
        "authority": [{}],
    }


@pytest.mark.parametrize(
    ("transition", "event_type", "step", "artifact_ids"),
    [
        ("baseline.lock", "baseline.locked", "5", ["B02", "B03"]),
        ("protocol.readiness", "readiness.approved", "7", ["E02", "S04"]),
        ("evidence.review", "evidence.reviewed", "19", ["S10"]),
    ],
)
def test_readiness_authority_shape_requires_exact_key_and_artifact_set(
    asset_bundle: AssetBundle, monkeypatch: pytest.MonkeyPatch,
    transition: str, event_type: str, step: str,
    artifact_ids: list[str],
) -> None:
    monkeypatch.setattr("authority.replay_authorities", lambda *_args: [])
    signed = {"candidate_records_digest": digest({"records": artifact_ids})}
    event = _authority_shape_event(
        transition=transition, event_type=event_type,
        action="readiness.approve", artifact_ids=artifact_ids,
        governed_ids={"workspace_id": "workspace-1", "step": step},
        signed_digests=signed,
        payload_extra={
            "step": int(step),
            "authority_candidate_records_digest":
                signed["candidate_records_digest"],
        },
    )
    _validate_event_authority(event, asset_bundle, [])

    substituted_key = deepcopy(event)
    substituted_key["payload"]["authority_subject"]["digests"] = {
        "substituted_records_digest": signed["candidate_records_digest"],
    }
    with pytest.raises(LedgerError) as rejected_key:
        _validate_event_authority(substituted_key, asset_bundle, [])
    assert rejected_key.value.code == "authority_event_effect_mismatch"

    cross_type = deepcopy(event)
    replacement = "E02" if "E02" not in artifact_ids else "B02"
    replaced = cross_type["payload"]["published_artifacts"][-1]
    replaced.update({
        "artifact_id": replacement,
        "instance_key": f"{replacement.lower()}-substitute",
        "path": f"artifacts/{replacement}/{replacement.lower()}-substitute.json",
    })
    with pytest.raises(LedgerError) as rejected_type:
        _validate_event_authority(cross_type, asset_bundle, [])
    assert rejected_type.value.code == "authority_event_effect_mismatch"

    duplicate_type = deepcopy(event)
    duplicate = deepcopy(
        duplicate_type["payload"]["published_artifacts"][0],
    )
    duplicate["instance_key"] += "-duplicate"
    duplicate["path"] = (
        f"artifacts/{duplicate['artifact_id']}/{duplicate['instance_key']}.json"
    )
    duplicate_type["payload"]["published_artifacts"].append(duplicate)
    with pytest.raises(LedgerError) as rejected_duplicate:
        _validate_event_authority(duplicate_type, asset_bundle, [])
    assert rejected_duplicate.value.code == "authority_event_effect_mismatch"


def test_historical_replay_preserves_authority_configuration_failure(
    asset_bundle: AssetBundle, monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed = {"candidate_records_digest": digest({"records": ["E02", "S04"]})}
    event = _authority_shape_event(
        transition="protocol.readiness",
        event_type="readiness.approved",
        action="readiness.approve",
        artifact_ids=["E02", "S04"],
        governed_ids={"workspace_id": "workspace-1", "step": "7"},
        signed_digests=signed,
        payload_extra={
            "step": 7,
            "authority_candidate_records_digest":
                signed["candidate_records_digest"],
        },
    )

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise AuthorityError(
            "openssl_unavailable",
            "OpenSSL is required for authority-gated actions",
            configuration=True,
        )

    monkeypatch.setattr("authority.replay_authorities", unavailable)
    with pytest.raises(AuthorityError) as raised:
        _validate_event_authority(event, asset_bundle, [])
    assert raised.value.code == "openssl_unavailable"
    assert raised.value.configuration is True


@pytest.mark.parametrize(
    ("transition", "event_type", "action", "artifact_id", "candidate_key"),
    [
        (
            "protocol.amend", "amendment.accepted", "amendment.approve",
            "E06", "E06_candidate_digest",
        ),
        (
            "protocol.deviate", "deviation.observed", "deviation.accept",
            "E07", "E07_candidate_digest",
        ),
        (
            "lifecycle.cancel", "lifecycle.cancelled", "lifecycle.cancel",
            "S16", "S16_candidate_digest",
        ),
        (
            "lifecycle.exception", "exception.granted", "exception.grant",
            "S15", "S15_candidate_digest",
        ),
    ],
)
def test_interrupt_authority_shape_binds_action_specific_key_type_and_instance(
    asset_bundle: AssetBundle, monkeypatch: pytest.MonkeyPatch,
    transition: str, event_type: str, action: str,
    artifact_id: str, candidate_key: str,
) -> None:
    monkeypatch.setattr("authority.replay_authorities", lambda *_args: [])
    candidate = digest({"candidate": artifact_id})
    event = _authority_shape_event(
        transition=transition, event_type=event_type, action=action,
        artifact_ids=[artifact_id],
        governed_ids={"workspace_id": "workspace-1"},
        signed_digests={candidate_key: candidate},
        payload_extra={
            "authority_candidate_digest": candidate,
            "final_record_digest": digest({"final": artifact_id}),
        },
    )
    _validate_event_authority(event, asset_bundle, [])

    substituted_key = deepcopy(event)
    substituted_key["payload"]["authority_subject"]["digests"] = {
        "substituted_candidate_digest": candidate,
    }
    with pytest.raises(LedgerError) as rejected_key:
        _validate_event_authority(substituted_key, asset_bundle, [])
    assert rejected_key.value.code == "authority_event_effect_mismatch"

    cross_type = deepcopy(event)
    replacement = "S15" if artifact_id != "S15" else "E06"
    descriptor = cross_type["payload"]["published_artifacts"][0]
    descriptor.update({
        "artifact_id": replacement,
        "instance_key": f"{replacement.lower()}-substitute",
        "path": f"artifacts/{replacement}/{replacement.lower()}-substitute.json",
    })
    with pytest.raises(LedgerError) as rejected_type:
        _validate_event_authority(cross_type, asset_bundle, [])
    assert rejected_type.value.code == "authority_event_effect_mismatch"


def test_d05_authority_shape_binds_exact_keys_type_and_instance(
    asset_bundle: AssetBundle, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("authority.replay_authorities", lambda *_args: [])
    signed_ids = {
        "workspace_id": "workspace-1", "experiment_id": "experiment-1",
        "evidence_id": "evidence-1", "decision_id": "decision-1",
        "outcome": "hypothesis_supported",
    }
    signed_digests = {
        "frozen_spec_digest": digest({"frozen": 1}),
        "evidence_bundle_digest": digest({"evidence": 1}),
        "rule_digest": digest({"rule": 1}),
    }
    event = _authority_shape_event(
        transition="decision.provisional", event_type="decision.provisional",
        action="decision.D05", artifact_ids=["D05"],
        governed_ids=signed_ids, signed_digests=signed_digests,
        payload_extra={"outcome": "hypothesis_supported"},
    )
    descriptor = event["payload"]["published_artifacts"][0]
    descriptor.update({
        "instance_key": "experiment-1.decision-1",
        "path": "artifacts/D05/experiment-1.decision-1.json",
    })
    event["subjects"]["artifact_id"] = "D05:experiment-1.decision-1"
    _validate_event_authority(event, asset_bundle, [])

    substituted_key = deepcopy(event)
    digests = substituted_key["payload"]["authority_subject"]["digests"]
    digests["substituted_rule_digest"] = digests.pop("rule_digest")
    with pytest.raises(LedgerError) as rejected_key:
        _validate_event_authority(substituted_key, asset_bundle, [])
    assert rejected_key.value.code == "authority_event_effect_mismatch"

    cross_type = deepcopy(event)
    cross_type["payload"]["published_artifacts"][0].update({
        "artifact_id": "E06", "instance_key": "experiment-1.decision-1",
        "path": "artifacts/E06/experiment-1.decision-1.json",
    })
    with pytest.raises(LedgerError) as rejected_type:
        _validate_event_authority(cross_type, asset_bundle, [])
    assert rejected_type.value.code == "authority_event_effect_mismatch"


def _frozen_experiment_content(
    *, hypothesis: str,
    experiment_id: str = "experiment-1",
    baseline_id: str = "baseline-1",
    decision_rule: dict | None = None,
) -> dict:
    baseline_ref = f"B02:{baseline_id}"
    source_refs = {
        baseline_ref: digest({"source": "baseline-lock", "id": baseline_id}),
        "G02:contracts": digest({"source": "contracts"}),
        "G03:data-contract": digest({"source": "data-contract"}),
        "G10:evaluator-1": digest({"source": "evaluator"}),
        "R04:dataset-1": digest({"source": "dataset-manifest"}),
        "R05:split-1": digest({"source": "split-manifest"}),
    }
    rules = decision_rule or {
        "schema": "sdm-scientific-rule/v1",
        "support_predicate": "supports",
        "rejection_predicate": "rejects",
        "inconclusive_rule": "underpowered_or_no_decisive_result",
        "invalid_rule": "protocol_invalid",
    }
    return {
        "experiment_id": experiment_id,
        "protocol_channel": "formal",
        "promotable": True,
        "author_principal_ids": ["experiment-author"],
        "hypothesis": hypothesis,
        "variables": {
            "dependent": "target", "independent": "intervention",
        },
        "change_only": {
            "allowed_code_paths": ["src/models/**"],
            "allowed_config_paths": ["/model/intervention"],
            "required_equal_refs": [{
                "ref": "G10:evaluator-1",
                "digest": source_refs["G10:evaluator-1"],
            }],
            "required_diff_paths": ["code:src/models/model.py"],
            "automatically_derived_paths": [],
        },
        "frozen_variables": [{
            "path": "/variables/independent",
            "digest": digest("intervention"),
            "reason": "the isolated intervention is protocol-frozen",
        }],
        "forbidden_changes": ["code:src/labels/**"],
        "contracts": {"source_digests": {
            ref: bound for ref, bound in source_refs.items()
            if ref.startswith("G")
        }},
        "baseline": {
            "lock_ref": baseline_ref,
            "lock_digest": source_refs[baseline_ref],
            "baseline_digest": digest({"baseline": "model-v1"}),
            "data_digest": digest({"data": "dataset-1"}),
            "evaluator_digest": source_refs["G10:evaluator-1"],
        },
        "data": {
            "dataset_manifest_ref": "R04:dataset-1",
            "dataset_manifest_digest": source_refs["R04:dataset-1"],
            "dataset_digest": digest({"data": "dataset-1"}),
            "split_manifest_ref": "R05:split-1",
            "split_manifest_digest": source_refs["R05:split-1"],
            "split_digest": digest({"split": "split-1"}),
            "calibration_applicable": False,
        },
        "evaluator": {
            "contract_ref": "G10:evaluator-1",
            "contract_digest": source_refs["G10:evaluator-1"],
            "evaluator_identity": "evaluator-1",
        },
        "budget": {"attempts": 1, "compute_hours": 1},
        "decision_rule": rules,
        "search": {"mode": "none"},
        "run_plan_id": "plan-1",
        "source_refs": source_refs,
    }


def _publish_experiment_with_lineage(
    workspace: Path, workspace_id: str, experiment: dict,
) -> None:
    requirement = matrix._artifact(
        workspace_id, "S01", "requirement-lineage", {
            "requirement_id": "requirement-lineage",
            "uri": "jira://ML-1",
            "owner": "product-owner",
            "impact": "bind the governed authority fixture to planning",
            "non_goals": ["unbounded external effects"],
        },
    )
    matrix._publish(workspace, "S01", requirement)
    planning = matrix._artifact(
        workspace_id, "S03", "planning-lineage", {
            "product_brief": {
                "kind": "product-brief",
                "uri": "bmad://product-brief/ML-1",
                "digest": digest({"bmad": "product-brief/ML-1"}),
                "revision": "planning-v1",
            },
            "prd": {
                "kind": "prd", "uri": "bmad://prd/ML-1",
                "digest": digest({"bmad": "prd/ML-1"}),
                "revision": "planning-v1",
            },
            "architecture": {
                "kind": "architecture",
                "uri": "bmad://architecture/ML-1",
                "digest": digest({"bmad": "architecture/ML-1"}),
                "revision": "planning-v1",
            },
            "source_refs": {
                "S01:requirement-lineage": digest(requirement),
            },
        },
    )
    publish_artifact(
        workspace,
        artifact_id="S03",
        instance_key=planning["instance_key"],
        value=planning,
        expected_revision=replay(workspace).revision,
        operation_id="publish-s03-planning-lineage",
        parents=["S01:requirement-lineage"],
    )
    publish_artifact(
        workspace,
        artifact_id="E01",
        instance_key=experiment["instance_key"],
        value=experiment,
        expected_revision=replay(workspace).revision,
        operation_id=f"publish-e01-{experiment['instance_key']}",
        parents=["S03:planning-lineage"],
    )


def _real_amendment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], workspace: Path,
) -> None:
    workspace_id = "authority-artifact-binding"
    specctl = matrix._load(
        "sdm_authority_artifact_specctl", SCRIPTS / "specctl.py",
    )
    authority_helpers = matrix._load(
        "sdm_authority_artifact_helpers",
        SCRIPTS / "tests" / "test_authority.py",
    )
    frozen = matrix._artifact(
        workspace_id, "E01", "experiment-1.frozen",
        _frozen_experiment_content(
            hypothesis="the governed intervention improves the target",
        ),
        status="frozen",
    )
    _publish_experiment_with_lineage(workspace, workspace_id, frozen)
    subjects = {"experiment_id": "experiment-1"}
    content = {
        "amendment_id": "amendment-authority-binding",
        "experiment_id": "experiment-1",
        "timing_revision": replay(workspace).revision,
        "classification": "non-material",
        "old_digest": digest(frozen),
        "candidate_digest": digest({"candidate": "original"}),
        "authority": [],
    }
    candidate = matrix._artifact(
        workspace_id, "E06", "experiment-1.authority-binding", content,
    )
    candidate["subjects"] = {"workspace_id": workspace_id, **subjects}
    signed_digests = {"E06_candidate_digest": digest(candidate)}
    attestations = []
    for index, role in enumerate(("research-owner", "protocol-authority")):
        private, public = authority_helpers._keypair(
            tmp_path, f"artifact-binding-{role}",
        )
        attestations.append(authority_helpers._attestation(
            tmp_path, private, public,
            action="amendment.approve", role=role,
            principal_id=f"artifact-binding-{index}",
            revision=replay(workspace).revision,
            nonce_suffix=f"artifact-binding-{index}",
            governed_ids={"workspace_id": workspace_id, **subjects},
            digests=signed_digests,
        ))
    snapshot = {
        "schema": "sdm-trust-snapshot/v1",
        "verifier": verifier_record(),
        "keys": [item["trust_snapshot"]["keys"][0] for item in attestations],
        "revocations": [],
    }
    for item in attestations:
        item["trust_snapshot"] = snapshot
        item["trust_snapshot_digest"] = digest(snapshot)
    (tmp_path / f"{workspace_id}-trust" / "trust-snapshot.json").write_text(
        json.dumps(snapshot), encoding="utf-8",
    )
    payload = tmp_path / "authority-artifact-amendment.json"
    payload.write_text(json.dumps({
        "record": candidate, "subjects": subjects,
        "attestations": attestations,
    }), encoding="utf-8")
    code = specctl.main([
        "lifecycle", "amend", "--workspace", str(workspace),
        "--payload", str(payload), "--expected-revision",
        str(replay(workspace).revision), "--operation-id",
        "authority-artifact-amendment",
    ])
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output["status"] == "pass", output


def _rehash_final_artifact_substitution(workspace: Path) -> None:
    replayed = replay(workspace)
    event = deepcopy(replayed.events[-1])
    assert event["event_type"] == "amendment.accepted"
    descriptor = event["payload"]["published_artifacts"][0]
    artifact_path = workspace / descriptor["path"]
    artifact = read_document(artifact_path)
    artifact["content"]["candidate_digest"] = digest({
        "candidate": "substituted-after-signing",
    })
    artifact["content_digest"] = digest(artifact["content"])
    artifact_path.write_bytes(canonical_bytes(artifact))
    descriptor["digest"] = digest(artifact)
    event["payload"]["final_record_digest"] = descriptor["digest"]
    previous_hash = event["previous_hash"]
    event.pop("event_hash")
    event["event_hash"] = event_hash(previous_hash, event)
    event_path = workspace / "_ledger" / "events" / f"{event['sequence']:020d}.json"
    event_path.write_bytes(canonical_bytes(event))
    memlog_path = workspace / "_memlog" / f"{event['sequence']:020d}.json"
    memlog_path.write_bytes(canonical_bytes(_memlog(event)))
    tampered_replay = replay(workspace)
    (workspace / "state.json").write_bytes(
        canonical_bytes(materialize(tampered_replay)),
    )


def _rehash_last_artifact_field(
    workspace: Path, *, field: str, replacement: object,
) -> None:
    replayed = replay(workspace)
    event = deepcopy(replayed.events[-1])
    descriptor = event["payload"]["published_artifacts"][0]
    artifact_path = workspace / descriptor["path"]
    artifact = read_document(artifact_path)
    artifact["content"][field] = replacement
    artifact["content_digest"] = digest(artifact["content"])
    artifact_path.write_bytes(canonical_bytes(artifact))
    descriptor["digest"] = digest(artifact)
    event["payload"]["final_record_digest"] = descriptor["digest"]
    previous_hash = event["previous_hash"]
    event.pop("event_hash")
    event["event_hash"] = event_hash(previous_hash, event)
    event_path = workspace / "_ledger" / "events" / \
        f"{event['sequence']:020d}.json"
    event_path.write_bytes(canonical_bytes(event))
    memlog_path = workspace / "_memlog" / f"{event['sequence']:020d}.json"
    memlog_path.write_bytes(canonical_bytes(_memlog(event)))
    tampered_replay = replay(workspace)
    (workspace / "state.json").write_bytes(
        canonical_bytes(materialize(tampered_replay)),
    )


def test_validate_and_resume_reject_rehashed_final_artifact_substitution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = matrix._workspace(tmp_path, "authority-artifact-binding")
    matrix._advance_fixture_to(workspace, 8)
    _real_amendment(tmp_path, capsys, workspace)
    assert validate_workspace(workspace)["valid"] is True

    tampered = tmp_path / "authority-artifact-binding-tampered"
    shutil.copytree(workspace, tampered)
    _rehash_final_artifact_substitution(tampered)
    checked = validate_workspace(tampered)
    assert checked["valid"] is False
    assert "authority_artifact_binding_mismatch" in {
        finding["code"] for finding in checked["findings"]
    }
    with pytest.raises(WorkspaceError) as blocked:
        resume_workspace(tampered)
    assert blocked.value.code == "authority_artifact_binding_mismatch"


def _real_d05_provisional(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    workspace, _ = matrix._execute_public_prefix(tmp_path, 19)
    workspace_id = replay(workspace).genesis["workspace_id"]
    specctl = matrix._load(
        "sdm_authority_d05_specctl", SCRIPTS / "specctl.py",
    )
    created = next(
        event for event in replay(workspace).events
        if event["event_type"] == "workspace.created"
    )
    trust_root = Path(created["payload"]["authority_trust_root"])
    (tmp_path / f"{workspace_id}-trust").symlink_to(
        trust_root, target_is_directory=True,
    )
    matrix._use_real_authorities(
        specctl, tmp_path, workspace_id, monkeypatch,
        {"decision.D05": ("scientific-reviewer",)},
    )
    attestation = tmp_path / "authority-d05-attestation.json"
    attestation.write_text("{}", encoding="utf-8")
    code = specctl.main([
        "scientific-decision", "--workspace", str(workspace),
        "--experiment-id", "experiment", "--evidence-id", "evidence",
        "--decision-id", "decision-binding",
        "--attestation", str(attestation),
        "--expected-revision", str(replay(workspace).revision),
        "--operation-id", "authority-d05-decision",
    ])
    output = json.loads(capsys.readouterr().out)
    assert code == 3 and output["status"] == "blocked", output
    assert output["findings"][0]["code"] == "external_anchor_required"
    return workspace


def _rehash_d05_signed_field_substitution(workspace: Path) -> None:
    replayed = replay(workspace)
    events = deepcopy(replayed.events)
    provisional_index = next(
        index for index, event in enumerate(events)
        if event.get("event_type") == "decision.provisional"
    )
    provisional = events[provisional_index]
    descriptor = provisional["payload"]["published_artifacts"][0]
    artifact_path = workspace / descriptor["path"]
    decision = read_document(artifact_path)
    decision["content"]["frozen_spec_digest"] = digest({
        "frozen": "substituted-after-signing",
    })
    decision["content_digest"] = digest(decision["content"])
    artifact_path.write_bytes(canonical_bytes(decision))
    descriptor["digest"] = digest(decision)
    for index in range(provisional_index, len(events)):
        event = events[index]
        if index > provisional_index:
            event["previous_hash"] = events[index - 1]["event_hash"]
        previous_hash = event["previous_hash"]
        event.pop("event_hash", None)
        event["event_hash"] = event_hash(previous_hash, event)
        event_path = workspace / "_ledger" / "events" / \
            f"{event['sequence']:020d}.json"
        event_path.write_bytes(canonical_bytes(event))
        (workspace / "_memlog" / f"{event['sequence']:020d}.json").write_bytes(
            canonical_bytes(_memlog(event)),
        )
    (workspace / "state.json").write_bytes(
        canonical_bytes(materialize(replay(workspace))),
    )


def test_validate_and_resume_rebuild_d05_signed_source_closure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _real_d05_provisional(tmp_path, capsys, monkeypatch)
    assert validate_workspace(workspace)["valid"] is True
    resume_workspace(workspace)

    tampered = tmp_path / "authority-d05-binding-tampered"
    shutil.copytree(workspace, tampered)
    _rehash_d05_signed_field_substitution(tampered)
    checked = validate_workspace(tampered)
    assert checked["valid"] is False
    assert "authority_artifact_binding_mismatch" in {
        finding["code"] for finding in checked["findings"]
    }
    with pytest.raises(WorkspaceError) as blocked:
        resume_workspace(tampered)
    assert blocked.value.code == "authority_artifact_binding_mismatch"


def _decision_content(
    *, decision_id: str, outcome: str, underpowered: bool,
    authority: list[str], supersession_reason: str | None = None,
) -> dict:
    rules = {
        "schema": "sdm-scientific-rule/v1",
        "support_predicate": "supports_hypothesis",
        "rejection_predicate": "rejects_hypothesis",
        "inconclusive_rule": "underpowered_or_no_decisive_result",
        "invalid_rule": "protocol_invalid",
    }
    context = {
        "frozen_rules": rules,
        "planned_runs": [{
            "run_id": "run-1", "required_attempt_ids": ["attempt-1"],
        }],
        "attempts": [{
            "attempt_id": "attempt-1", "run_id": "run-1",
            "terminal_status": "successful", "accounted": True,
            "validity": "valid", "system_failure": False,
        }],
        "evidence": {
            "protocol_invalid": False, "underpowered": underpowered,
            "predicates": {
                "supports_hypothesis": not underpowered,
                "rejects_hypothesis": False,
            },
        },
    }
    content = {
        "decision_id": decision_id,
        "experiment_id": "experiment-1",
        "frozen_spec_digest": digest({"experiment": "frozen"}),
        "evidence_bundle_digest": digest({"evidence": "complete"}),
        "rule_digest": digest(rules),
        "outcome": outcome,
        "reviewers": ["independent-reviewer"],
        "authority": authority,
        "decision_context": context,
    }
    if supersession_reason is not None:
        content["supersession_reason"] = supersession_reason
    return content


def _publish_baseline_pair(workspace: Path, workspace_id: str) -> None:
    for baseline_id in ("baseline-v1", "baseline-v2"):
        baseline = matrix._artifact(workspace_id, "B01", baseline_id, {
            "baseline_id": baseline_id,
            "scope": "governed comparison",
            "model": f"model-{baseline_id}",
            "data": "G03:dataset-locked",
            "evaluator": "G10:evaluator-locked",
            "tolerances": {"score_delta": 0.01},
        })
        matrix._publish(workspace, "B01", baseline)


def test_public_baseline_deprecation_binds_final_B04_and_dependencies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = "public-baseline-deprecation"
    workspace = matrix._workspace(tmp_path, workspace_id)
    matrix._advance_fixture_to(workspace, 5)
    specctl = matrix._load(
        "sdm_public_baseline_deprecation", SCRIPTS / "specctl.py",
    )
    matrix._use_real_authorities(
        specctl, tmp_path, workspace_id, monkeypatch,
        {"baseline.deprecate": ("baseline-governance-authority",)},
    )
    _publish_baseline_pair(workspace, workspace_id)
    subjects = {"baseline_id": "baseline-v1"}
    candidate = matrix._artifact(
        workspace_id, "B04", "baseline-v1.deprecation-1", {
            "deprecation_id": "deprecation-1",
            "baseline_id": "baseline-v1",
            "reason": "baseline-v2 is the governed replacement",
            "active_dependents": [],
            "replacement": "baseline-v2",
            "authority": [],
        }, status="deprecated",
    )
    candidate["subjects"] = {"workspace_id": workspace_id, **subjects}
    payload = tmp_path / "baseline-deprecation.json"
    payload.write_text(json.dumps({
        "record": candidate, "subjects": subjects,
        "attestations": [{}],
    }), encoding="utf-8")
    code = specctl.main([
        "lifecycle", "deprecate", "--workspace", str(workspace),
        "--payload", str(payload), "--expected-revision",
        str(replay(workspace).revision), "--operation-id",
        "public-baseline-deprecation",
    ])
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output["status"] == "pass", output
    event = replay(workspace).events[-1]
    assert event["event_type"] == "baseline.deprecated"
    assert event["payload"]["active_dependents"] == []
    final = read_document(
        workspace / "artifacts/B04/baseline-v1.deprecation-1.json",
    )
    assert final["content"]["authority"]
    assert validate_workspace(workspace)["valid"] is True
    tampered = tmp_path / "public-baseline-deprecation-tampered"
    shutil.copytree(workspace, tampered)
    _rehash_last_artifact_field(
        tampered, field="reason", replacement="substituted after signing",
    )
    checked = validate_workspace(tampered)
    assert checked["valid"] is False
    assert "authority_artifact_binding_mismatch" in {
        item["code"] for item in checked["findings"]
    }
    with pytest.raises(WorkspaceError) as blocked:
        resume_workspace(tampered)
    assert blocked.value.code == "authority_artifact_binding_mismatch"


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("active-dependent-mismatch", "baseline_active_dependents_mismatch"),
        ("replacement-cycle", "baseline_replacement_cycle"),
    ],
)
def test_public_baseline_deprecation_rejects_unbound_dependency_or_cycle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    case: str, expected_code: str,
) -> None:
    workspace_id = f"public-baseline-deprecation-{case}"
    workspace = matrix._workspace(tmp_path, workspace_id)
    matrix._advance_fixture_to(workspace, 5)
    specctl = matrix._load(
        f"sdm_public_baseline_deprecation_{case}", SCRIPTS / "specctl.py",
    )
    _publish_baseline_pair(workspace, workspace_id)

    baseline_id = "baseline-v1"
    replacement = "baseline-v2"
    if case == "active-dependent-mismatch":
        experiment = matrix._artifact(
            workspace_id, "E01", "experiment-active",
            _frozen_experiment_content(
                experiment_id="experiment-active",
                baseline_id="baseline-v1",
                hypothesis=(
                    "the active governed experiment depends on baseline-v1"
                ),
            ),
        )
        _publish_experiment_with_lineage(workspace, workspace_id, experiment)
    else:
        existing = matrix._artifact(
            workspace_id, "B04", "baseline-v1.deprecation-existing", {
                "deprecation_id": "deprecation-existing",
                "baseline_id": "baseline-v1",
                "reason": "baseline-v2 is the governed replacement",
                "active_dependents": [], "replacement": "baseline-v2",
                "authority": [digest({"authority": "fixture-authority"})],
            }, status="deprecated",
        )
        matrix._publish(workspace, "B04", existing)
        baseline_id, replacement = replacement, baseline_id

    candidate = matrix._artifact(
        workspace_id, "B04", f"{baseline_id}.deprecation-invalid", {
            "deprecation_id": "deprecation-invalid",
            "baseline_id": baseline_id,
            "reason": "invalid governed replacement",
            "active_dependents": [], "replacement": replacement,
            "authority": [],
        }, status="deprecated",
    )
    candidate["subjects"] = {
        "workspace_id": workspace_id, "baseline_id": baseline_id,
    }
    payload = tmp_path / f"baseline-deprecation-{case}.json"
    payload.write_text(json.dumps({
        "record": candidate, "subjects": {"baseline_id": baseline_id},
        "attestations": [{}],
    }), encoding="utf-8")
    before = snapshot_tree(workspace)
    code = specctl.main([
        "lifecycle", "deprecate", "--workspace", str(workspace),
        "--payload", str(payload), "--expected-revision",
        str(replay(workspace).revision), "--operation-id",
        f"public-baseline-deprecation-{case}",
    ])
    output = json.loads(capsys.readouterr().out)
    assert code == 3 and output["status"] == "blocked", output
    assert output["findings"][0]["code"] == expected_code
    assert snapshot_tree(workspace) == before


@pytest.fixture(scope="module")
def _decision_dependency_template(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    root = tmp_path_factory.mktemp("authority-decision-lineage")
    workspace, _ = matrix._execute_public_prefix(root, 21)
    return workspace


def _decision_successor(
    workspace_id: str, predecessor_key: str, predecessor: dict, reason: str,
) -> dict:
    content = deepcopy(predecessor["content"])
    content.update({
        "decision_id": "decision-2",
        "authority": [],
        "supersession_reason": reason,
    })
    experiment_id = content["experiment_id"]
    successor = matrix._artifact(
        workspace_id, "D05", f"{experiment_id}.decision-2", content,
    )
    successor["subjects"] = {
        "workspace_id": workspace_id, "experiment_id": experiment_id,
    }
    successor["supersedes"] = f"D05:{predecessor_key}"
    return successor


def _decision_workspace(
    template: Path, tmp_path: Path, name: str,
) -> tuple[Path, str, str, dict]:
    workspace = tmp_path / name
    shutil.copytree(template, workspace)
    replayed = replay(workspace)
    state = materialize(replayed)
    predecessor_key, predecessor_record = next(iter(
        state["artifacts"]["D05"].items()
    ))
    predecessor = read_document(workspace / predecessor_record["path"])
    return workspace, replayed.genesis["workspace_id"], predecessor_key, predecessor


def _fresh_trusted_anchor(workspace: Path, suffix: str) -> Path:
    replayed = replay(workspace)
    state = materialize(replayed)
    expected_anchor = state["trusted_anchor"]["event_hash"]
    accepted = next(
        event for event in reversed(replayed.events)
        if event["event_type"] == "anchor.accepted"
        and event["payload"]["anchored_head"] == expected_anchor
    )
    prior_result = read_document(
        workspace / "packets" / "S13" /
        f"{accepted['payload']['request_id']}.json",
    )
    prior_evidence = {
        row["type"]: row["value"] for row in prior_result["evidence"]
    }
    operation = "git.read-current-ledger-anchor"
    _, contract, _ = replayed.bundles[
        replayed.active_engine_digest
    ].operation_contract(operation)
    request_id = f"REQ-{suffix}"
    created_event = next(
        event for event in replayed.events
        if event["event_type"] == "workspace.created"
    )
    binding_identity = created_event["payload"]["bindings"][
        "source_control_platform_skill"
    ]
    binding_digest = created_event["payload"]["binding_skill_digests"][
        "source_control_platform_skill"
    ]
    created = handoffs.create(
        workspace,
        {
            "request_id": request_id,
            "operation": operation,
            "binding_identity": binding_identity,
            "binding_skill_digest": binding_digest,
            "adapter_contract_digest": digest(contract),
            "gate": "resume-current-anchor",
            "step": 21,
            "subjects": {"artifact_id": f"anchor:{request_id}"},
            "expected_evidence": list(contract["required_evidence"]),
            "evidence_cardinality": deepcopy(
                contract["evidence_cardinality"],
            ),
            "portable_paths": [],
        },
        expected_revision=replayed.revision,
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
    evidence = {
        "repository_identity": prior_evidence["repository_identity"],
        "workspace_identity": state["workspace_id"],
        "external_head_identity": expected_anchor,
        "current_ledger_anchor": expected_anchor,
        "observed_at": request["created_at"],
        "operation_receipt_uri": f"receipt://authority-binding/{suffix}",
        "operation_receipt_digest": digest({"receipt": suffix}),
        "terminal_status": "successful",
    }
    result = {
        "schema": "sdm-handoff-result/v1",
        "result_id": f"RES-{suffix}",
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
            {"type": name, "value": evidence[name]}
            for name in request["expected_evidence"]
        ],
        "completed_at": request["created_at"],
    }
    seal_platform_result(request, result)
    trusted_snapshot = created_event["payload"]["authority_trust_snapshot"]
    result["producer_attestation"]["trust_snapshot"] = deepcopy(
        trusted_snapshot,
    )
    result["producer_attestation"]["trust_snapshot_digest"] = digest(
        trusted_snapshot,
    )
    result.pop("receipt_digest", None)
    result["receipt_digest"] = digest(result)
    ingested = handoffs.ingest(
        workspace,
        result,
        expected_revision=dispatched.revision,
        operation_id=f"ingest-{request_id}",
    )
    assert ingested.disposition == "accepted"
    return workspace / "packets" / "S13" / f"{request_id}.json"


def test_public_decision_supersession_is_reciprocal_and_immutable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    _decision_dependency_template: Path,
) -> None:
    template_state = materialize(replay(_decision_dependency_template))
    frozen_record = template_state["artifacts"]["E01"][
        "experiment.frozen"
    ]
    frozen = read_document(
        _decision_dependency_template / frozen_record["path"],
    )
    freeze_attestations = frozen["extensions"]["dev.bmad.sdm.freeze"][
        "freeze_attestations"
    ]
    assert freeze_attestations == sorted(freeze_attestations)
    template_anchor = _fresh_trusted_anchor(
        _decision_dependency_template, "decision-template-current",
    )
    assert validate_workspace(
        _decision_dependency_template,
        trusted_anchor_path=template_anchor,
    )["valid"] is True
    workspace, workspace_id, predecessor_key, predecessor = \
        _decision_workspace(
            _decision_dependency_template, tmp_path,
            "public-decision-supersession",
        )
    specctl = matrix._load(
        "sdm_public_decision_supersession", SCRIPTS / "specctl.py",
    )
    created = next(
        event for event in replay(workspace).events
        if event["event_type"] == "workspace.created"
    )
    trust_root = Path(created["payload"]["authority_trust_root"])
    (tmp_path / f"{workspace_id}-trust").symlink_to(
        trust_root, target_is_directory=True,
    )
    matrix._use_real_authorities(
        specctl, tmp_path, workspace_id, monkeypatch,
        {
            "decision.supersede": (
                "scientific-reviewer", "governance-authority",
            ),
        },
    )
    reason = "new governed review supersedes the prior decision record"
    successor = _decision_successor(
        workspace_id, predecessor_key, predecessor, reason,
    )
    experiment_id = predecessor["content"]["experiment_id"]
    payload = tmp_path / "decision-supersession.json"

    def payload_value(record: dict) -> dict:
        return {
            "supersession_kind": "decision",
            "predecessor_decision_instance_key": predecessor_key,
            "record": record,
            "supersession_reason": reason,
            "subjects": {"experiment_id": experiment_id},
            "attestations": [{}, {}],
        }

    before = snapshot_tree(workspace)
    with pytest.raises(specctl.CLIError) as ambiguous:
        specctl._unique_decision_evidence_match([
            {"evidence_id": "evidence-a"},
            {"evidence_id": "evidence-b"},
        ])
    assert ambiguous.value.code == "decision_evidence_binding_ambiguous"
    assert snapshot_tree(workspace) == before

    mismatched_evidence = deepcopy(successor)
    mismatched_evidence["content"]["evidence_bundle_digest"] = digest({
        "evidence": "unreviewed-substitute",
    })
    mismatched_evidence["content_digest"] = digest(
        mismatched_evidence["content"],
    )
    payload.write_text(
        json.dumps(payload_value(mismatched_evidence)), encoding="utf-8",
    )
    code = specctl.main([
        "lifecycle", "supersede", "--workspace", str(workspace),
        "--payload", str(payload), "--expected-revision",
        str(replay(workspace).revision), "--operation-id",
        "public-decision-supersession-evidence-mismatch",
    ])
    evidence_output = json.loads(capsys.readouterr().out)
    assert code == 3 and evidence_output["status"] == "blocked", \
        evidence_output
    assert evidence_output["findings"][0]["code"] == \
        "decision_evidence_binding_invalid"
    assert snapshot_tree(workspace) == before

    mismatched = deepcopy(successor)
    mismatched["content"]["reviewers"] = [
        "forged-reviewer-a", "forged-reviewer-b",
    ]
    mismatched["content_digest"] = digest(mismatched["content"])
    payload.write_text(
        json.dumps(payload_value(mismatched)), encoding="utf-8",
    )
    code = specctl.main([
        "lifecycle", "supersede", "--workspace", str(workspace),
        "--payload", str(payload), "--expected-revision",
        str(replay(workspace).revision), "--operation-id",
        "public-decision-supersession-reviewer-mismatch",
    ])
    mismatch_output = json.loads(capsys.readouterr().out)
    assert code == 3 and mismatch_output["status"] == "blocked", \
        mismatch_output
    assert mismatch_output["findings"][0]["code"] == \
        "scientific_decision_reviewer_mismatch"
    assert snapshot_tree(workspace) == before

    expected_reviewers = [
        "governance-authority-2-1", "scientific-reviewer-2-0",
    ]
    successor["content"]["reviewers"] = expected_reviewers
    successor["content_digest"] = digest(successor["content"])
    payload.write_text(
        json.dumps(payload_value(successor)), encoding="utf-8",
    )
    code = specctl.main([
        "lifecycle", "supersede", "--workspace", str(workspace),
        "--payload", str(payload), "--expected-revision",
        str(replay(workspace).revision), "--operation-id",
        "public-decision-supersession",
    ])
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output["status"] == "pass", output
    state = materialize(replay(workspace))
    assert state["dimensions"]["scientific"] == "hypothesis_supported"
    assert "model" not in state["dimensions"]
    final_decision = read_document(
        workspace / state["artifacts"]["D05"][
            f"{experiment_id}.decision-2"
        ]["path"]
    )
    assert final_decision["content"]["reviewers"] == expected_reviewers
    edges = {
        (row["from"], row["to"], row["relation"])
        for row in state["lineage"]["edges"]
    }
    assert {
        (
            f"D05:{predecessor_key}",
            f"D05:{experiment_id}.decision-2", "superseded-by",
        ),
        (
            f"D05:{experiment_id}.decision-2",
            f"D05:{predecessor_key}", "supersedes",
        ),
    }.issubset(edges)
    tampered = tmp_path / "public-decision-supersession-tampered"
    shutil.copytree(workspace, tampered)
    fresh_anchor = _fresh_trusted_anchor(
        workspace, "decision-supersession-current",
    )
    assert validate_workspace(
        workspace, trusted_anchor_path=fresh_anchor,
    )["valid"] is True
    _rehash_last_artifact_field(
        tampered, field="supersession_reason",
        replacement="substituted after signing",
    )
    checked = validate_workspace(tampered)
    assert checked["valid"] is False
    assert "authority_artifact_binding_mismatch" in {
        item["code"] for item in checked["findings"]
    }
    with pytest.raises(WorkspaceError) as blocked:
        resume_workspace(tampered)
    assert blocked.value.code == "authority_artifact_binding_mismatch"

