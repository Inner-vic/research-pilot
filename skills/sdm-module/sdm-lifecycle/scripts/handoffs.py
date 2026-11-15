#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Immutable outer-Skill request/result envelopes and delivery state machine.

This module never resolves a binding into executable code and never invokes a
Skill.  The native outer coordinator loads the configured Skill and carries S12
and S13; this module only creates, validates, deduplicates, and reconciles them.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from authority import (
    AuthorityError, PLATFORM_RESULT_ACTION, PLATFORM_RESULT_ROLE,
    verify_attestation, verify_platform_result_attestation,
)
from canonical import canonical_bytes, digest, read_document
from contracts import AssetBundle, ContractError, validate_schema
from ledger import replay, utc_now
from projection import materialize, validate_graph
from workspace import (
    MutationResult, WorkspaceError, mutate as _workspace_mutate,
)


_IDENTITY = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_PACKET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REQUEST_ID = re.compile(r"^REQ-[A-Za-z0-9][A-Za-z0-9._-]{5,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_RESULT_SCHEMA = "schemas/handoff-result.schema.json"
_TERMINAL = {"successful", "failed", "cancelled"}
_COORDINATOR_DISPOSITIONS = {"dispatched", "invoke_error", "dispatch_unknown"}


class HandoffError(RuntimeError):
    def __init__(self, code: str, message: str, *, exit_code: int = 2, disposition: str = "fail") -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.disposition = disposition


@dataclass(frozen=True)
class HandoffOutcome:
    revision: int
    disposition: str
    request_id: str
    packet: dict[str, Any] | None
    event: dict[str, Any] | None
    idempotent: bool = False


def mutate(*args: Any, **kwargs: Any) -> MutationResult:
    """Public handoff mutations always preserve the authenticated T01 graph."""

    kwargs["enforce_graph"] = True
    return _workspace_mutate(*args, **kwargs)


def _context(workspace: str | Path) -> tuple[Any, dict[str, Any], AssetBundle]:
    replayed = replay(workspace)
    state = materialize(replayed)
    graph_findings = validate_graph(state.get("lineage"))
    if graph_findings:
        raise HandoffError(
            graph_findings[0]["code"], graph_findings[0]["message"],
            exit_code=3,
        )
    return replayed, state, replayed.bundles[replayed.active_engine_digest]


def _producer_trust(replayed: Any) -> tuple[dict[str, Any], str]:
    created = next(
        (event for event in replayed.events if event.get("event_type") == "workspace.created"),
        None,
    )
    payload = created.get("payload", {}) if isinstance(created, dict) else {}
    snapshot = payload.get("authority_trust_snapshot")
    snapshot_digest = payload.get("authority_trust_snapshot_digest")
    if (
        not isinstance(snapshot, dict)
        or not isinstance(snapshot_digest, str)
        or snapshot_digest != digest(snapshot)
    ):
        raise HandoffError(
            "producer_trust_required",
            "platform result verification requires the coordinator-pinned public trust snapshot",
            exit_code=5,
            disposition="configuration_required",
        )
    return snapshot, snapshot_digest


def _portable_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(path.parts) and not path.is_absolute() and all(part not in ("", ".", "..") for part in path.parts)


def _request_id(payload: dict[str, Any]) -> str:
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise HandoffError("request_id_invalid", "handoff request_id does not satisfy the exact S12 grammar")
    return request_id


def _dispatch_correlation(value: Any) -> str:
    if not isinstance(value, str):
        raise HandoffError("dispatch_correlation_invalid", "dispatch correlation must be text")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise HandoffError("dispatch_correlation_invalid", "dispatch correlation is not strict UTF-8") from exc
    if not encoded or len(encoded) > 512:
        raise HandoffError(
            "dispatch_correlation_invalid",
            "dispatch correlation must contain 1 through 512 UTF-8 bytes",
        )
    return value


def _operation_request_contract(
    *,
    replayed: Any,
    domain: str,
    binding_key: str,
    operation: str,
    operation_contract: dict[str, Any],
    revision: int,
) -> dict[str, Any]:
    required_evidence = operation_contract.get("required_evidence")
    evidence_cardinality = operation_contract.get("evidence_cardinality")
    evidence_value_schemas = operation_contract.get("evidence_value_schemas")
    subject_requirements = operation_contract.get("subject_requirements")
    if (
        not isinstance(required_evidence, list)
        or not required_evidence
        or any(not isinstance(item, str) or not item for item in required_evidence)
        or not isinstance(evidence_cardinality, dict)
        or not evidence_cardinality
        or not isinstance(evidence_value_schemas, dict)
        or set(evidence_value_schemas) != set(required_evidence)
        or not isinstance(subject_requirements, dict)
        or not subject_requirements
    ):
        raise HandoffError(
            "operation_contract_invalid",
            "pinned platform operation lacks executable evidence/cardinality/value-schema/subject requirements",
            exit_code=5,
            disposition="configuration_required",
        )
    workspace_id = replayed.genesis.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise HandoffError(
            "workspace_identity_invalid",
            "replayed genesis lacks one immutable workspace identity",
            exit_code=5,
            disposition="configuration_required",
        )
    return {
        "operation": operation,
        "domain": domain,
        "binding_key": binding_key,
        "adapter_contract_digest": digest(operation_contract),
        "workspace_id": workspace_id,
        "workspace_revision": revision,
        "expected_evidence": deepcopy(required_evidence),
        "evidence_cardinality": deepcopy(evidence_cardinality),
        "expected_result_schema": _RESULT_SCHEMA,
        "subject_requirements": subject_requirements,
    }


def _configured_request_contract(
    base: dict[str, Any], state: dict[str, Any],
) -> dict[str, Any]:
    binding_key = base["binding_key"]
    identity = state.get("bindings", {}).get(binding_key)
    skill_digest = state.get("binding_skill_digests", {}).get(binding_key)
    if not isinstance(identity, str) or not _IDENTITY.fullmatch(identity):
        raise HandoffError(
            "configuration_required",
            f"runtime binding is empty or invalid: {binding_key}",
            exit_code=5,
            disposition="configuration_required",
        )
    if not isinstance(skill_digest, str) or not _DIGEST.fullmatch(skill_digest):
        raise HandoffError(
            "binding_skill_digest_required",
            f"runtime binding lacks a genesis-pinned Skill-tree digest: {binding_key}",
            exit_code=5,
            disposition="configuration_required",
        )
    return {
        **base,
        "binding_identity": identity,
        "binding_skill_digest": skill_digest,
    }


def _assert_exact_contract_claims(
    payload: dict[str, Any], derived: dict[str, Any],
) -> None:
    claims = {
        "binding_identity": "binding_identity",
        "binding_skill_digest": "binding_skill_digest",
        "adapter_contract_digest": "adapter_contract_digest",
        "domain": "domain",
        "binding_key": "binding_key",
        "workspace_id": "workspace_id",
        "workspace_revision": "workspace_revision",
        "expected_evidence": "expected_evidence",
        "required_evidence": "expected_evidence",
        "evidence_cardinality": "evidence_cardinality",
        "expected_result_schema": "expected_result_schema",
    }
    for claimed_name, derived_name in claims.items():
        if (
            claimed_name in payload
            and derived_name in derived
            and payload[claimed_name] != derived[derived_name]
        ):
            if claimed_name == "binding_identity":
                raise HandoffError(
                    "binding_changed",
                    "request binding identity differs from pinned workspace configuration",
                )
            raise HandoffError(
                "request_contract_mismatch",
                f"caller-supplied {claimed_name} differs from the replay-derived request contract",
            )


def _request_subjects(
    payload: dict[str, Any], operation_contract: dict[str, Any],
) -> dict[str, str]:
    subjects = payload.get("subjects")
    if (
        not isinstance(subjects, dict)
        or not subjects
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value.strip()
            for key, value in subjects.items()
        )
    ):
        raise HandoffError(
            "request_subjects_invalid",
            "handoff subjects must map extensible instance keys to non-empty identities",
        )
    step = payload.get("step")
    requirements = operation_contract["subject_requirements"]
    by_step = requirements.get("by_step", {})
    rule = by_step.get(str(step)) if type(step) is int else None
    if rule is None:
        rule = requirements.get("default")
    if rule is None:
        raise HandoffError(
            "operation_step_unsupported",
            "pinned platform operation has no subject contract for the requested lifecycle step",
        )
    missing = sorted(set(rule["required"]).difference(subjects))
    required_any = rule["required_any"]
    if missing or required_any and not any(key in subjects for key in required_any):
        details = []
        if missing:
            details.append(f"required={missing}")
        if required_any and not any(key in subjects for key in required_any):
            details.append(f"required_any={required_any}")
        raise HandoffError(
            "request_subjects_underbound",
            "handoff subjects omit applicable operation/step anchors: "
            + ", ".join(details),
        )
    # Instance keys are governed by the artifact catalogs and are intentionally
    # extensible (for example research_brief_id).  Dropping keys here would make
    # the immutable S12 differ from the caller's logical request and break exact
    # idempotent replay.
    return {key: subjects[key] for key in sorted(subjects)}


def _request_packet(
    *,
    payload: dict[str, Any],
    derived: dict[str, Any],
    claim_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _assert_exact_contract_claims(
        payload if claim_payload is None else claim_payload, derived,
    )
    request_id = _request_id(payload)
    paths = payload.get("portable_paths", [])
    if not isinstance(paths, list) or any(not isinstance(item, str) or not _portable_path(item) for item in paths):
        raise HandoffError("request_path_invalid", "handoff paths must be contained portable relative paths")
    anchors = _request_subjects(payload, derived)
    logical = {
        "request_id": request_id,
        "operation": derived["operation"],
        "domain": derived["domain"],
        "binding_key": derived["binding_key"],
        "binding_identity": derived["binding_identity"],
        "binding_skill_digest": derived["binding_skill_digest"],
        "adapter_contract_digest": derived["adapter_contract_digest"],
        "workspace_id": derived["workspace_id"],
        "workspace_revision": derived["workspace_revision"],
        "gate": payload.get("gate"),
        "step": payload.get("step"),
        "subjects": anchors,
        "expected_evidence": derived["expected_evidence"],
        "evidence_cardinality": derived["evidence_cardinality"],
        "portable_paths": paths,
        "expected_result_schema": derived["expected_result_schema"],
        "retry_of": payload.get("retry_of"),
    }
    idempotency_key = digest(logical)
    if payload.get("idempotency_key") not in (None, idempotency_key):
        raise HandoffError("idempotency_key_mismatch", "caller-supplied idempotency key does not match canonical logical request")
    return {
        "schema": "sdm-handoff-request/v1",
        "request_id": request_id,
        "operation": derived["operation"],
        "domain": derived["domain"],
        "binding_key": derived["binding_key"],
        "binding_identity": derived["binding_identity"],
        "binding_skill_digest": derived["binding_skill_digest"],
        "adapter_contract_digest": derived["adapter_contract_digest"],
        "workspace_id": derived["workspace_id"],
        "workspace_revision": derived["workspace_revision"],
        "gate": payload.get("gate"),
        "step": payload.get("step"),
        "subjects": anchors,
        "expected_evidence": derived["expected_evidence"],
        "evidence_cardinality": derived["evidence_cardinality"],
        "portable_paths": paths,
        "idempotency_key": idempotency_key,
        "created_at": utc_now(),
        "expected_result_schema": derived["expected_result_schema"],
        "retry_of": payload.get("retry_of"),
    }


def create(
    workspace: str | Path,
    payload: dict[str, Any],
    *,
    expected_revision: int,
    operation_id: str,
) -> HandoffOutcome:
    replayed, state, bundle = _context(workspace)
    request_id = _request_id(payload)
    operation = payload.get("operation")
    if not isinstance(operation, str):
        raise HandoffError("operation_missing", "handoff create payload requires operation")
    try:
        domain, operation_contract, binding_key = bundle.operation_contract(operation)
    except ContractError as exc:
        raise HandoffError(exc.code, str(exc)) from exc
    base_contract = _operation_request_contract(
        replayed=replayed,
        domain=domain,
        binding_key=binding_key,
        operation=operation,
        operation_contract=operation_contract,
        revision=replayed.revision,
    )
    _request_subjects(payload, base_contract)
    configured = state.get("bindings", {}).get(binding_key)
    existing_record = state.get("handoffs", {}).get(request_id)
    if existing_record and existing_record.get("status") == "configuration_required":
        if not configured:
            _assert_exact_contract_claims(payload, base_contract)
            raise HandoffError(
                "configuration_required",
                f"runtime binding is empty: {binding_key}",
                exit_code=5,
                disposition="configuration_required",
            )
    if not configured:
        # Claims derivable without an installed binding are checked before the
        # durable configuration-required path. A forged adapter/evidence or
        # workspace claim is invalid request data, not configuration telemetry.
        _assert_exact_contract_claims(payload, base_contract)
        mutation = mutate(
            workspace,
            expected_revision=expected_revision,
            operation_id=operation_id,
            event_type="handoff.configuration-required",
            subjects={
                "workspace_id": replayed.genesis["workspace_id"],
                "request_id": request_id,
            },
            payload={
                "request_id": request_id,
                "operation": operation,
                "domain": domain,
                "binding_key": binding_key,
                "step": payload.get("step"),
                "request_intent_digest": digest({
                    "request_id": request_id,
                    "operation": operation,
                    "gate": payload.get("gate"),
                    "step": payload.get("step"),
                    "subjects": payload.get("subjects"),
                }),
            },
        )
        raise HandoffError(
            "configuration_required",
            f"runtime binding is empty: {binding_key}; recorded at revision {mutation.revision}",
            exit_code=5,
            disposition="configuration_required",
        )
    derived = _configured_request_contract(base_contract, state)
    if existing_record and existing_record.get("status") != "configuration_required":
        persisted = _load_request(workspace, request_id)
        created = next(
            (
                event for event in replayed.events
                if event.get("event_type") == "handoff.created"
                and event.get("payload", {}).get("request_id") == request_id
            ),
            None,
        )
        if not isinstance(created, dict) or type(created.get("sequence")) is not int:
            raise HandoffError(
                "request_creation_event_missing",
                "persisted S12 lacks one exact handoff.created source event",
                exit_code=4,
            )
        at_creation = {
            **derived,
            "workspace_revision": created["sequence"] - 1,
        }
        candidate = _request_packet(payload=payload, derived=at_creation)
        findings = validate_schema(
            persisted,
            bundle.document("schemas/handoff-request.schema.json"),
            subject=request_id,
            governing_asset="schemas/handoff-request.schema.json",
        )
        immutable_keys = set(candidate).difference({"created_at"})
        if findings or any(
            persisted.get(key) != candidate[key] for key in immutable_keys
        ):
            raise HandoffError(
                "idempotency_conflict",
                "request_id is already bound to different immutable replay-derived anchors",
                exit_code=4,
            )
        return HandoffOutcome(
            replayed.revision, existing_record["status"], request_id,
            persisted, None, True,
        )
    _assert_exact_contract_claims(payload, derived)
    packet = _request_packet(
        payload=payload,
        derived=derived,
    )
    findings = validate_schema(packet, bundle.document("schemas/handoff-request.schema.json"), subject=packet["request_id"], governing_asset="schemas/handoff-request.schema.json")
    if findings:
        raise HandoffError("request_schema_invalid", findings[0]["message"])
    for current_id, record in state.get("handoffs", {}).items():
        if record.get("request_digest") == digest(packet):
            path = Path(workspace) / "packets" / "S12" / f"{current_id}.json"
            return HandoffOutcome(replayed.revision, record["status"], current_id, read_document(path), None, True)
        if record.get("idempotency_key") == packet["idempotency_key"] and current_id != packet["request_id"]:
            raise HandoffError("logical_request_duplicate", "logical request already exists under a different request_id", exit_code=4)
    request_digest = digest(packet)
    relative = f"packets/S12/{packet['request_id']}.json"
    result = mutate(
        workspace,
        expected_revision=expected_revision,
        operation_id=operation_id,
        event_type="handoff.created",
        subjects={"workspace_id": replayed.genesis["workspace_id"], "request_id": packet["request_id"]},
        payload={
            "request_id": packet["request_id"],
            "operation": operation,
            "domain": domain,
            "binding_identity": packet["binding_identity"],
            "binding_skill_digest": packet["binding_skill_digest"],
            "adapter_contract_digest": packet["adapter_contract_digest"],
            "binding_key": packet["binding_key"],
            "gate": packet["gate"],
            "step": packet["step"],
            "idempotency_key": packet["idempotency_key"],
            "request_digest": request_digest,
            "subjects": packet["subjects"],
        },
        artifact_writes=((relative, packet, True),),
    )
    return HandoffOutcome(result.revision, "created", packet["request_id"], packet, result.event, result.idempotent)


def mark_dispatched(
    workspace: str | Path,
    *,
    request_id: str,
    dispatch_correlation: str,
    expected_revision: int,
    operation_id: str,
    coordinator_disposition: str = "dispatched",
    uncertain: bool | None = None,
) -> HandoffOutcome:
    """Persist the coordinator's actual delivery observation.

    ``invoke_error`` and ``dispatch_unknown`` both mean that an external effect
    cannot safely be ruled in or out.  They therefore use the existing
    reconciliation-required event instead of manufacturing a producer S13 or
    allowing a blind re-dispatch.  ``uncertain`` is retained solely as a
    compatibility spelling for older callers and maps to ``dispatch_unknown``.
    """
    if uncertain is not None:
        legacy_disposition = "dispatch_unknown" if uncertain else "dispatched"
        if coordinator_disposition != "dispatched" and coordinator_disposition != legacy_disposition:
            raise HandoffError(
                "coordinator_disposition_conflict",
                "uncertain and coordinator_disposition describe different dispatch outcomes",
            )
        coordinator_disposition = legacy_disposition
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise HandoffError("request_id_invalid", "request ID does not satisfy the exact S12 grammar")
    dispatch_correlation = _dispatch_correlation(dispatch_correlation)
    if coordinator_disposition not in _COORDINATOR_DISPOSITIONS:
        raise HandoffError(
            "coordinator_disposition_invalid",
            "coordinator disposition must be dispatched, invoke_error, or dispatch_unknown",
        )
    replayed, state, _ = _context(workspace)
    record = state.get("handoffs", {}).get(request_id)
    if not record:
        raise HandoffError("request_unknown", "handoff request does not exist")
    status = record.get("status")
    if status == "dispatched":
        if (
            coordinator_disposition == "dispatched"
            and record.get("dispatch_correlation") == dispatch_correlation
        ):
            return HandoffOutcome(
                replayed.revision, "dispatched", request_id, None, None, True,
            )
        raise HandoffError(
            "dispatch_correlation_conflict",
            "the first dispatch correlation and disposition are immutable",
            exit_code=4,
        )
    if status != "created":
        raise HandoffError("dispatch_state_invalid", f"request cannot be dispatched from {record.get('status')}", exit_code=3, disposition="blocked")
    needs_reconciliation = coordinator_disposition != "dispatched"
    event_type = "handoff.reconciliation-required" if needs_reconciliation else "handoff.dispatched"
    result = mutate(
        workspace,
        expected_revision=expected_revision,
        operation_id=operation_id,
        event_type=event_type,
        subjects={"workspace_id": replayed.genesis["workspace_id"], "request_id": request_id},
        payload={
            "request_id": request_id,
            "dispatch_correlation": dispatch_correlation,
            "coordinator_disposition": coordinator_disposition,
            "reason": coordinator_disposition,
        },
    )
    disposition = "reconciliation_required" if needs_reconciliation else "dispatched"
    return HandoffOutcome(result.revision, disposition, request_id, None, result.event, result.idempotent)


def _load_request(workspace: str | Path, request_id: str) -> dict[str, Any]:
    try:
        value = read_document(Path(workspace) / "packets" / "S12" / f"{request_id}.json")
    except Exception as exc:
        raise HandoffError("request_packet_missing", f"immutable S12 packet is unavailable: {exc}") from exc
    if not isinstance(value, dict):
        raise HandoffError("request_packet_invalid", "S12 packet is not an object")
    return value


def _validate_result(
    request: dict[str, Any],
    result: dict[str, Any],
    bundle: AssetBundle,
    *,
    trusted_snapshot: dict[str, Any],
    trusted_snapshot_digest: str,
    consumption_time: str,
) -> dict[str, Any]:
    findings = validate_schema(
        result,
        bundle.document("schemas/handoff-result.schema.json"),
        subject=request["request_id"],
        governing_asset="schemas/handoff-result.schema.json",
        registry=bundle.documents,
    )
    if findings:
        raise HandoffError("result_schema_invalid", findings[0]["message"])
    unsigned_result = dict(result)
    claimed_receipt_digest = unsigned_result.pop("receipt_digest", None)
    if claimed_receipt_digest != digest(unsigned_result):
        raise HandoffError(
            "result_receipt_digest_mismatch",
            "S13 receipt_digest must bind the complete canonical result packet excluding only receipt_digest",
        )
    repeated = (
        "request_id",
        "operation",
        "binding_identity",
        "binding_skill_digest",
        "adapter_contract_digest",
        "workspace_id",
        "workspace_revision",
        "gate",
        "step",
        "subjects",
        "idempotency_key",
    )
    for key in repeated:
        if result.get(key) != request.get(key):
            raise HandoffError("result_anchor_mismatch", f"S13 does not exactly repeat S12 anchor: {key}", exit_code=3, disposition="reconciliation_required")
    status = result.get("terminal_status")
    if status not in _TERMINAL:
        raise HandoffError("producer_status_invalid", "S13 producer terminal status must be successful, failed, or cancelled")
    if status == "successful":
        evidence = result.get("evidence", [])
        if not isinstance(evidence, list):
            raise HandoffError("result_evidence_invalid", "successful S13 evidence must be an array")
        try:
            _, operation_contract, _ = bundle.operation_contract(
                request["operation"],
            )
        except ContractError as exc:
            raise HandoffError(
                "request_contract_invalid",
                "S12 operation is absent from the pinned platform contract",
                exit_code=5,
                disposition="configuration_required",
            ) from exc
        if digest(operation_contract) != request.get("adapter_contract_digest"):
            raise HandoffError(
                "request_contract_mismatch",
                "S12 adapter contract digest does not bind the active pinned evidence schemas",
                exit_code=3,
                disposition="blocked",
            )
        expected_order = request.get("expected_evidence", [])
        value_schemas = operation_contract.get("evidence_value_schemas")
        if (
            operation_contract.get("required_evidence") != expected_order
            or operation_contract.get("evidence_cardinality")
            != request.get("evidence_cardinality")
            or not isinstance(value_schemas, dict)
            or list(value_schemas) != expected_order
        ):
            raise HandoffError(
                "request_contract_mismatch",
                "S12 evidence names do not equal the pinned operation value-schema contract",
                exit_code=3,
                disposition="blocked",
            )
        expected = set(expected_order)
        available: list[str] = []
        for index, item in enumerate(evidence):
            if (
                not isinstance(item, dict)
                or set(item) != {"type", "value"}
                or not isinstance(item.get("type"), str)
                or item["type"] not in expected
            ):
                raise HandoffError(
                    "result_evidence_invalid",
                    "successful S13 evidence items require exactly a pinned type and value",
                    exit_code=3,
                    disposition="blocked",
                )
            available.append(item["type"])
        cardinality = request.get("evidence_cardinality", {})
        default_limits = cardinality.get("default", {}) \
            if isinstance(cardinality, dict) else {}
        for evidence_type in request.get("expected_evidence", []):
            limits = cardinality.get(evidence_type, default_limits) \
                if isinstance(cardinality, dict) else default_limits
            minimum = limits.get("min", 1) \
                if isinstance(limits, dict) else 1
            maximum = limits.get("max") if isinstance(limits, dict) else None
            count = available.count(evidence_type)
            if count < minimum or maximum is not None and count > maximum:
                raise HandoffError(
                    "result_incomplete",
                    "successful S13 violates required evidence cardinality: "
                    f"{evidence_type} count={count}, min={minimum}, max={maximum}",
                    exit_code=3,
                    disposition="blocked",
                )
        for index, item in enumerate(evidence):
            evidence_type = item["type"]
            value_findings = validate_schema(
                item["value"], value_schemas[evidence_type],
                subject=request["request_id"],
                governing_asset=(
                    "platform-operations.json#/operations/"
                    f"{request['operation']}/evidence_value_schemas/"
                    f"{evidence_type}"
                ),
                pointer=f"/evidence/{index}/value",
            )
            if value_findings:
                first = value_findings[0]
                raise HandoffError(
                    "result_evidence_value_invalid",
                    "successful S13 evidence value violates the pinned "
                    f"{evidence_type} schema: {first['message']}",
                    exit_code=3,
                    disposition="blocked",
                )
        # Any evidence field also pinned as an S12 subject is an immutable
        # operation anchor, not a producer-controlled summary.  Enforce this
        # at ingestion so a substituted checkpoint/model/data binding never
        # becomes an accepted S13 that later coordinators might materialize.
        subjects = request.get("subjects", {})
        if isinstance(subjects, dict):
            by_type: dict[str, list[Any]] = {}
            for item in evidence:
                if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                    continue
                item_value = item.get("value")
                if "value" not in item:
                    item_value = {
                        key: value for key, value in item.items() if key != "type"
                    }
                by_type.setdefault(item["type"], []).append(item_value)
            mismatched = sorted(
                name for name, bound in subjects.items()
                if name in expected
                and (len(by_type.get(name, [])) != 1 or by_type[name][0] != bound)
            )
            if mismatched:
                raise HandoffError(
                    "result_subject_evidence_mismatch",
                    "S13 evidence does not exactly repeat immutable S12 subjects: "
                    + ", ".join(mismatched),
                    exit_code=3,
                    disposition="blocked",
                )
    if request.get('operation') == 'ml.normal-execution':
        from normal_execution import validate_result as validate_normal_result
        try:
            validate_normal_result(request, result)
        except (ValueError, KeyError, TypeError) as exc:
            raise HandoffError('normal_result_binding_invalid', str(exc)) from exc
    try:
        return verify_platform_result_attestation(
            request,
            result,
            bundle=bundle,
            trusted_snapshot=trusted_snapshot,
            trusted_snapshot_digest=trusted_snapshot_digest,
            consumption_time=consumption_time,
        )
    except AuthorityError as exc:
        raise HandoffError(
            exc.code,
            str(exc),
            exit_code=5 if exc.configuration else 2,
            disposition="configuration_required" if exc.configuration else "fail",
        ) from exc


def _producer_event_fields(verified: dict[str, Any]) -> dict[str, Any]:
    return {
        "producer_attestation_digest": verified["attestation_digest"],
        "producer_subject_digest": verified["subject_digest"],
        "producer_key_digest": verified["key_digest"],
        "producer_trust_snapshot_digest": verified["trust_snapshot_digest"],
        "producer_attestation_consumed_at": verified["consumed_at"],
        # Keep the configured Skill binding and the independently signed
        # platform principal as separate authenticated event facts.
        "producer_identity": verified["producer_identity"],
        "producer_principal_id": verified["producer_principal_id"],
    }


def _validate_no_effect_proof(
    request: dict[str, Any], record: dict[str, Any], proof: Any,
    claimed_digest: Any, *, replayed: Any, bundle: AssetBundle,
    consumption_time: str,
) -> dict[str, Any]:
    keys = {
        "schema", "request_id", "operation", "binding_identity",
        "binding_skill_digest", "adapter_contract_digest", "workspace_id",
        "workspace_revision", "dispatch_correlation", "idempotency_key",
        "subjects", "observed_at", "producer_identity",
        "producer_principal_id", "producer_attestation",
    }
    if not isinstance(proof, dict) or set(proof) != keys:
        raise HandoffError(
            "no_effect_proof_invalid",
            "no-effect proof must be one closed canonical document",
        )
    if not isinstance(claimed_digest, str) or not _DIGEST.fullmatch(claimed_digest) or claimed_digest != digest(proof):
        raise HandoffError(
            "no_effect_proof_digest_mismatch",
            "no-effect proof digest does not bind the exact proof document",
        )
    repeated = (
        "request_id", "operation", "binding_identity", "binding_skill_digest",
        "adapter_contract_digest", "workspace_id", "workspace_revision",
        "idempotency_key", "subjects",
    )
    if proof.get("schema") != "sdm-no-effect-proof/v1" or any(
        proof.get(key) != request.get(key) for key in repeated
    ) or proof.get("dispatch_correlation") != record.get("dispatch_correlation"):
        raise HandoffError(
            "no_effect_proof_anchor_mismatch",
            "no-effect proof does not repeat every immutable request/dispatch anchor",
            exit_code=3,
            disposition="reconciliation_required",
        )
    if proof.get("producer_identity") != request.get("binding_identity"):
        raise HandoffError("producer_identity_mismatch", "no-effect producer must equal the configured binding")
    principal = proof.get("producer_principal_id")
    if not isinstance(principal, str) or not principal or principal == request.get("binding_identity"):
        raise HandoffError("producer_principal_not_independent", "no-effect proof requires an independent producer principal")
    time_findings = validate_schema(
        proof.get("observed_at"),
        {"type": "string", "format": "date-time"},
        subject=request["request_id"],
        governing_asset="sdm-no-effect-proof/v1",
    )
    if time_findings:
        raise HandoffError("no_effect_proof_time_invalid", time_findings[0]["message"])
    attestation = proof.get("producer_attestation")
    if not isinstance(attestation, dict):
        raise HandoffError("no_effect_proof_attestation_required", "no-effect proof lacks producer attestation")
    unsigned = dict(proof)
    unsigned.pop("producer_attestation")
    expected_subject = {
        "role": PLATFORM_RESULT_ROLE,
        "principal_id": principal,
        "governed_ids": {
            "workspace_id": request["workspace_id"],
            "request_id": request["request_id"],
            "operation": request["operation"],
            "binding_identity": request["binding_identity"],
            "producer_principal_id": principal,
            "dispatch_correlation": record["dispatch_correlation"],
        },
        "digests": {
            "no_effect_proof_payload_digest": digest(unsigned),
            "request_packet_digest": digest(request),
            "binding_skill_digest": request["binding_skill_digest"],
            "adapter_contract_digest": request["adapter_contract_digest"],
            "idempotency_key": request["idempotency_key"],
        },
    }
    trusted_snapshot, trusted_snapshot_digest = _producer_trust(replayed)
    try:
        verified = verify_attestation(
            attestation,
            action=PLATFORM_RESULT_ACTION,
            expected_subject=expected_subject,
            expected_revision=request["workspace_revision"],
            bundle=bundle,
            consumption_time=consumption_time,
            trusted_snapshot=trusted_snapshot,
            trusted_snapshot_digest=trusted_snapshot_digest,
        )
    except AuthorityError as exc:
        raise HandoffError(
            exc.code, str(exc), exit_code=5 if exc.configuration else 2,
            disposition="configuration_required" if exc.configuration else "fail",
        ) from exc
    return {
        **verified,
        "producer_identity": request["binding_identity"],
        "producer_principal_id": verified["principal_id"],
    }


def ingest(
    workspace: str | Path,
    result_packet: dict[str, Any],
    *,
    expected_revision: int,
    operation_id: str,
) -> HandoffOutcome:
    replayed, state, bundle = _context(workspace)
    request_id = result_packet.get("request_id")
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise HandoffError("request_id_invalid", "result request_id is missing or malformed")
    record = state.get("handoffs", {}).get(request_id)
    if not record:
        raise HandoffError("request_unknown", "result names an unknown request")
    status_before = record.get("status")
    if status_before == "created":
        raise HandoffError(
            "result_before_dispatch",
            "normal S13 ingestion is forbidden before immutable dispatch",
            exit_code=3,
            disposition="blocked",
        )
    if status_before == "reconciliation_required":
        raise HandoffError(
            "reconciliation_required",
            "uncertain dispatch must be reconciled before result ingestion",
            exit_code=3,
            disposition="reconciliation_required",
        )
    supplied_correlation = result_packet.get("dispatch_correlation")
    persisted_correlation = record.get("dispatch_correlation")
    if status_before in _TERMINAL | {"dispatched"} and (
        not isinstance(persisted_correlation, str)
        or supplied_correlation != persisted_correlation
    ):
        raise HandoffError(
            "result_dispatch_correlation_mismatch",
            "S13 does not repeat the immutable first-dispatch correlation",
            exit_code=3,
            disposition="blocked",
        )
    request = _load_request(workspace, request_id)
    if record.get("request_digest") != digest(request):
        raise HandoffError(
            "request_packet_digest_mismatch",
            "immutable S12 bytes differ from the request digest recorded in append-only history",
            exit_code=3,
            disposition="blocked",
        )
    request_findings = validate_schema(
        request,
        bundle.document("schemas/handoff-request.schema.json"),
        subject=request_id,
        governing_asset="schemas/handoff-request.schema.json",
        registry=bundle.documents,
    )
    if request_findings:
        raise HandoffError(
            "request_packet_invalid",
            request_findings[0]["message"],
            exit_code=3,
            disposition="blocked",
        )
    trusted_snapshot, trusted_snapshot_digest = _producer_trust(replayed)
    consumed_at = utc_now()
    try:
        verified_producer = _validate_result(
            request,
            result_packet,
            bundle,
            trusted_snapshot=trusted_snapshot,
            trusted_snapshot_digest=trusted_snapshot_digest,
            consumption_time=consumed_at,
        )
    except HandoffError as exc:
        if exc.disposition == "reconciliation_required":
            # A mismatched/stale S13 is itself a durable uncertainty fact. Keep
            # the untrusted packet out of S13 while recording the block before
            # returning the structured error to the coordinator.
            mutate(
                workspace,
                expected_revision=expected_revision,
                operation_id=operation_id,
                event_type="handoff.reconciliation-required",
                subjects={"workspace_id": replayed.genesis["workspace_id"], "request_id": request_id},
                payload={
                    "request_id": request_id,
                    "reason": exc.code,
                    "result_digest": digest(result_packet),
                },
            )
        raise
    result_digest = digest(result_packet)
    terminal_states = _TERMINAL
    if record.get("status") in {"cancelled", "superseded", "superseded_by_retry"} and not record.get("result_digest"):
        late_relative = f"packets/S13-late/{request_id}-{result_digest.split(':', 1)[1]}.json"
        late = mutate(
            workspace,
            expected_revision=expected_revision,
            operation_id=operation_id,
            event_type="handoff.reconciliation-required",
            subjects={"workspace_id": replayed.genesis["workspace_id"], "request_id": request_id},
            payload={
                "request_id": request_id,
                "reason": "late_result",
                "result_digest": result_digest,
                **_producer_event_fields(verified_producer),
            },
            artifact_writes=((late_relative, result_packet, True),),
        )
        return HandoffOutcome(late.revision, "late_result", request_id, result_packet, late.event)
    if record.get("status") in terminal_states and record.get("result_digest"):
        existing = read_document(Path(workspace) / "packets" / "S13" / f"{request_id}.json")
        if digest(existing) == result_digest:
            return HandoffOutcome(replayed.revision, record["status"], request_id, existing, None, True)
        conflict = mutate(
            workspace,
            expected_revision=expected_revision,
            operation_id=operation_id,
            event_type="handoff.reconciliation-required",
            subjects={"workspace_id": replayed.genesis["workspace_id"], "request_id": request_id},
            payload={
                "request_id": request_id,
                "reason": "conflicting_terminal_result",
                "result_digest": result_digest,
                **_producer_event_fields(verified_producer),
            },
        )
        return HandoffOutcome(conflict.revision, "reconciliation_required", request_id, None, conflict.event)
    relative = f"packets/S13/{request_id}.json"
    status = result_packet["terminal_status"]
    mutation = mutate(
        workspace,
        expected_revision=expected_revision,
        operation_id=operation_id,
        event_type="handoff.result-ingested",
        subjects={"workspace_id": replayed.genesis["workspace_id"], "request_id": request_id},
        payload={
            "request_id": request_id,
            "terminal_status": status,
            "result_digest": result_digest,
            "dispatch_correlation": result_packet.get("dispatch_correlation"),
            **_producer_event_fields(verified_producer),
        },
        artifact_writes=((relative, result_packet, True),),
    )
    disposition = "accepted" if status == "successful" else status
    return HandoffOutcome(mutation.revision, disposition, request_id, result_packet, mutation.event, mutation.idempotent)


def cancel(workspace: str | Path, *, request_id: str, reason: str, expected_revision: int, operation_id: str) -> HandoffOutcome:
    replayed, state, _ = _context(workspace)
    record = state.get("handoffs", {}).get(request_id)
    if record and record.get("status") == "reconciliation_required":
        raise HandoffError(
            "reconciliation_required",
            "cancellation is blocked while an external effect remains uncertain",
            exit_code=3,
            disposition="reconciliation_required",
        )
    if not record or record.get("status") in _TERMINAL | {"cancelled", "superseded", "superseded_by_retry"}:
        raise HandoffError("cancel_state_invalid", "request is absent or already terminal")
    if not isinstance(reason, str) or not reason.strip():
        raise HandoffError("cancel_reason_invalid", "cancellation reason must be non-empty text")
    result = mutate(
        workspace,
        expected_revision=expected_revision,
        operation_id=operation_id,
        event_type="handoff.cancelled",
        subjects={"workspace_id": replayed.genesis["workspace_id"], "request_id": request_id},
        payload={"request_id": request_id, "reason": reason},
    )
    return HandoffOutcome(result.revision, "cancelled", request_id, None, result.event, result.idempotent)


def retry(workspace: str | Path, payload: dict[str, Any], *, prior_request_id: str, expected_revision: int, operation_id: str) -> HandoffOutcome:
    replayed, state, bundle = _context(workspace)
    prior = state.get("handoffs", {}).get(prior_request_id)
    if not prior or prior.get("status") not in {"failed", "cancelled", "reconciled_no_effect"}:
        raise HandoffError("retry_state_invalid", "retry requires a failed, cancelled, or reconciled-no-effect request")
    prior_packet = _load_request(workspace, prior_request_id)
    combined = dict(prior_packet)
    combined.update(payload)
    combined["retry_of"] = prior_request_id
    combined.pop("created_at", None)
    combined.pop("idempotency_key", None)
    successor_id = _request_id(combined)
    if successor_id == prior_request_id:
        raise HandoffError("successor_request_id_self", "retry request must use a distinct S12 identity")
    if successor_id in state.get("handoffs", {}):
        raise HandoffError("successor_request_id_conflict", "retry request identity already exists", exit_code=4)
    for other in state.get("handoffs", {}).values():
        if successor_id in {other.get("superseded_by"), other.get("retry_request_id")}:
            raise HandoffError("successor_request_link_conflict", "retry request identity is already linked", exit_code=4)
    try:
        domain, operation_contract, binding_key = bundle.operation_contract(
            combined["operation"],
        )
    except ContractError as exc:
        raise HandoffError(exc.code, str(exc)) from exc
    base_contract = _operation_request_contract(
        replayed=replayed,
        domain=domain,
        binding_key=binding_key,
        operation=combined["operation"],
        operation_contract=operation_contract,
        revision=replayed.revision,
    )
    derived = _configured_request_contract(base_contract, state)
    packet = _request_packet(
        payload=combined,
        derived=derived,
        claim_payload=payload,
    )
    findings = validate_schema(
        packet,
        bundle.document("schemas/handoff-request.schema.json"),
        subject=packet["request_id"],
        governing_asset="schemas/handoff-request.schema.json",
    )
    if findings:
        raise HandoffError("request_schema_invalid", findings[0]["message"])
    relative = f"packets/S12/{packet['request_id']}.json"
    mutation = mutate(
        workspace,
        expected_revision=expected_revision,
        operation_id=operation_id,
        event_type="handoff.retry-created",
        subjects={"workspace_id": replayed.genesis["workspace_id"], "request_id": prior_request_id, "retry_request_id": packet["request_id"]},
        payload={
            "request_id": prior_request_id,
            "retry_request_id": packet["request_id"],
            "operation": packet["operation"],
            "domain": packet["domain"],
            "binding_identity": packet["binding_identity"],
            "binding_skill_digest": packet["binding_skill_digest"],
            "adapter_contract_digest": packet["adapter_contract_digest"],
            "binding_key": packet["binding_key"],
            "gate": packet["gate"],
            "step": packet["step"],
            "subjects": packet["subjects"],
            "idempotency_key": packet["idempotency_key"],
            "request_digest": digest(packet),
        },
        artifact_writes=((relative, packet, True),),
    )
    return HandoffOutcome(mutation.revision, "created", packet["request_id"], packet, mutation.event, mutation.idempotent)


def supersede(workspace: str | Path, *, request_id: str, successor_request_id: str, reason: str, expected_revision: int, operation_id: str) -> HandoffOutcome:
    replayed, state, _ = _context(workspace)
    record = state.get("handoffs", {}).get(request_id)
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise HandoffError("request_id_invalid", "predecessor request ID does not satisfy the exact S12 grammar")
    if not isinstance(successor_request_id, str) or not _REQUEST_ID.fullmatch(successor_request_id):
        raise HandoffError("successor_request_id_invalid", "successor request ID does not satisfy the exact S12 grammar")
    if successor_request_id == request_id:
        raise HandoffError("successor_request_id_self", "a handoff request cannot supersede itself")
    if not isinstance(reason, str) or not reason.strip():
        raise HandoffError("supersede_reason_invalid", "supersession reason must be non-empty text")
    if record and record.get("status") == "reconciliation_required":
        raise HandoffError(
            "reconciliation_required",
            "supersession is blocked while an external effect remains uncertain",
            exit_code=3,
            disposition="reconciliation_required",
        )
    eligible = {"created", "dispatched", "failed", "cancelled", "reconciled_no_effect"}
    if not record or record.get("status") not in eligible:
        raise HandoffError("supersede_state_invalid", "request state is not eligible for append-only supersession")
    if successor_request_id in state.get("handoffs", {}):
        raise HandoffError("successor_request_id_conflict", "successor request identity already exists", exit_code=4)
    for other in state.get("handoffs", {}).values():
        if successor_request_id in {other.get("superseded_by"), other.get("retry_request_id")}:
            raise HandoffError("successor_request_link_conflict", "successor request identity is already linked", exit_code=4)
    result = mutate(
        workspace,
        expected_revision=expected_revision,
        operation_id=operation_id,
        event_type="handoff.superseded",
        subjects={"workspace_id": replayed.genesis["workspace_id"], "request_id": request_id, "successor_request_id": successor_request_id},
        payload={"request_id": request_id, "superseded_by": successor_request_id, "reason": reason},
    )
    return HandoffOutcome(result.revision, "superseded", request_id, None, result.event, result.idempotent)


def reconcile(workspace: str | Path, payload: dict[str, Any], *, request_id: str, expected_revision: int, operation_id: str) -> HandoffOutcome:
    replayed, state, bundle = _context(workspace)
    record = state.get("handoffs", {}).get(request_id)
    if not record or record.get("status") != "reconciliation_required":
        raise HandoffError("reconcile_state_invalid", "only a reconciliation-required request may be reconciled")
    disposition = payload.get("disposition")
    if disposition not in {"effect_committed", "no_effect"}:
        raise HandoffError("reconcile_disposition_invalid", "reconcile disposition must be effect_committed or no_effect")
    persisted_correlation = record.get("dispatch_correlation")
    supplied_correlation = payload.get("dispatch_correlation")
    if (
        not isinstance(persisted_correlation, str)
        or supplied_correlation != persisted_correlation
    ):
        raise HandoffError(
            "reconcile_correlation_mismatch",
            "reconciliation must use the exact persisted dispatch correlation",
            exit_code=3,
            disposition="reconciliation_required",
        )
    result_packet = payload.get("result")
    writes: tuple[tuple[str, Any, bool], ...] = ()
    terminal_status = None
    result_digest = None
    verified_producer: dict[str, Any] | None = None
    if disposition == "effect_committed":
        if not isinstance(result_packet, dict):
            raise HandoffError("reconcile_result_required", "effect_committed requires the exact correlated S13")
        if result_packet.get("dispatch_correlation") != persisted_correlation:
            raise HandoffError(
                "reconcile_correlation_mismatch",
                "the correlated S13 does not repeat the persisted dispatch correlation",
                exit_code=3,
                disposition="reconciliation_required",
            )
        request = _load_request(workspace, request_id)
        trusted_snapshot, trusted_snapshot_digest = _producer_trust(replayed)
        verified_producer = _validate_result(
            request,
            result_packet,
            bundle,
            trusted_snapshot=trusted_snapshot,
            trusted_snapshot_digest=trusted_snapshot_digest,
            consumption_time=utc_now(),
        )
        terminal_status = result_packet["terminal_status"]
        result_digest = digest(result_packet)
        writes = ((f"packets/S13/{request_id}.json", result_packet, True),)
    else:
        request = _load_request(workspace, request_id)
        proof = payload.get("no_effect_proof")
        proof_digest = payload.get("no_effect_proof_digest")
        verified_producer = _validate_no_effect_proof(
            request, record, proof, proof_digest,
            replayed=replayed, bundle=bundle, consumption_time=utc_now(),
        )
        writes = ((f"packets/S13-no-effect/{request_id}.json", proof, True),)
    reconciliation_payload = {
        "request_id": request_id,
        "disposition": "reconciled_effect" if disposition == "effect_committed" else "reconciled_no_effect",
        "terminal_status": terminal_status,
        "result_digest": result_digest,
        "dispatch_correlation": supplied_correlation,
        "no_effect_proof_digest": payload.get("no_effect_proof_digest"),
    }
    if record.get("coordinator_disposition") in _COORDINATOR_DISPOSITIONS:
        reconciliation_payload["coordinator_disposition"] = record["coordinator_disposition"]
    if verified_producer is not None:
        reconciliation_payload.update(_producer_event_fields(verified_producer))
    mutation = mutate(
        workspace,
        expected_revision=expected_revision,
        operation_id=operation_id,
        event_type="handoff.reconciled",
        subjects={"workspace_id": replayed.genesis["workspace_id"], "request_id": request_id},
        payload=reconciliation_payload,
        artifact_writes=writes,
    )
    return HandoffOutcome(mutation.revision, disposition, request_id, result_packet, mutation.event, mutation.idempotent)


def _diagnostic_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect the SDM handoff-envelope library")
    parser.parse_args(argv)
    sys.stdout.write(json.dumps({"status": "pass", "command": "handoffs.help"}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(_diagnostic_main())
