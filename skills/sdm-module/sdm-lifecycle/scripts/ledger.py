#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Canonical fixed-genesis, hash-chained SDM event ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from canonical import (
    CanonicalError, canonical_bytes, digest, digest_bytes, parse_document,
)
from contracts import (
    ASSET_FILES, SCHEMA_FILES, AssetBundle, ContractError,
    _owned_schema_bindings, require_supported_engine, validate_schema,
)
from journal import JournalError, ReadCapability, Transaction


_EVENT_FILE = re.compile(r"^(\d{20})\.json$")
_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")
MAX_LEDGER_NODES = 100_000
MAX_LEDGER_BYTES = 134_217_728

_INHERITED_AUTHORITY_TRANSITIONS = {
    "protocol.freeze": "protocol.freeze-provisional",
    "decision.accept": "decision.provisional",
    "lifecycle.archive": "lifecycle.archive-provisional",
    "lifecycle.close": "lifecycle.close-provisional",
}


class LedgerError(RuntimeError):
    def __init__(self, code: str, message: str, *, sequence: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.sequence = sequence


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def genesis_document(workspace_id: str, created_at: str, bundle: AssetBundle) -> dict[str, Any]:
    return {
        "asset_digests": dict(sorted(bundle.asset_digests.items())),
        "created_at": created_at,
        "engine_contract_digest": bundle.engine_contract_digest,
        "schema": "sdm-ledger/v1",
        "workspace_id": workspace_id,
    }


def genesis_hash(genesis: dict[str, Any]) -> str:
    return digest(genesis)


def _raw_digest(value: str) -> bytes:
    match = _DIGEST.fullmatch(value)
    if not match:
        raise LedgerError("bad_digest", f"invalid canonical digest: {value}")
    return bytes.fromhex(match.group(1))


def event_hash(previous_hash: str, event_without_hash: dict[str, Any]) -> str:
    value = hashlib.sha256(b"sdm-event-v1\0" + _raw_digest(previous_hash) + canonical_bytes(event_without_hash)).hexdigest()
    return "sha256:" + value


def build_event(
    *,
    sequence: int,
    event_type: str,
    previous_hash: str,
    engine_contract_digest: str,
    actor: dict[str, Any],
    subjects: dict[str, Any],
    payload: dict[str, Any],
    operation_id: str,
    occurred_at: str | None = None,
    authority: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "schema": "sdm-event/v1",
        "sequence": sequence,
        "event_type": event_type,
        "occurred_at": occurred_at or utc_now(),
        "actor": actor,
        "subjects": subjects,
        "payload": payload,
        "operation_id": operation_id,
        "engine_contract_digest": engine_contract_digest,
        "previous_hash": previous_hash,
    }
    if authority:
        event["authority"] = authority
    event["event_hash"] = event_hash(previous_hash, event)
    return event


@dataclass(frozen=True)
class ReplayResult:
    genesis: dict[str, Any]
    events: tuple[dict[str, Any], ...]
    revision: int
    head_hash: str
    active_engine_digest: str
    bundles: dict[str, AssetBundle]


def _require_capability_root(
    workspace: str | os.PathLike[str], capability: ReadCapability,
) -> None:
    selected = Path(os.path.abspath(os.fspath(workspace)))
    if selected != capability.root_path:
        raise JournalError(
            "unsafe_workspace", "governed read capability belongs to another root",
        )


def _ledger_error(exc: JournalError, *, subject: str) -> LedgerError:
    if exc.code == "document_resource_limit":
        code = "ledger_resource_limit"
    elif exc.code == "concurrent_mutation":
        code = "ledger_concurrent_mutation"
    elif exc.code == "mount_escape":
        code = "mount_escape"
    elif exc.code == "mount_identity_unavailable":
        code = "mount_identity_unavailable"
    else:
        code = "unsafe_ledger_node"
    return LedgerError(code, f"unsafe governed ledger input {subject}: {exc}")


def load_genesis(
    workspace: str | os.PathLike[str],
    *,
    capability: ReadCapability | None = None,
) -> dict[str, Any]:
    if capability is None:
        try:
            with ReadCapability(
                workspace,
                max_nodes=MAX_LEDGER_NODES,
                max_bytes=MAX_LEDGER_BYTES,
            ) as reader:
                return load_genesis(workspace, capability=reader)
        except JournalError as exc:
            raise LedgerError(
                "genesis_invalid", f"cannot load canonical genesis: {exc}",
            ) from exc
    _require_capability_root(workspace, capability)
    try:
        value = parse_document(capability.read_bytes("_ledger/genesis.json"))
    except JournalError as exc:
        raise _ledger_error(exc, subject="_ledger/genesis.json") from exc
    except (OSError, ValueError) as exc:
        raise LedgerError("genesis_invalid", f"cannot load canonical genesis: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != "sdm-ledger/v1":
        raise LedgerError("genesis_invalid", "genesis schema is missing or invalid")
    required = {"asset_digests", "created_at", "engine_contract_digest", "schema", "workspace_id"}
    if set(value) != required:
        raise LedgerError("genesis_invalid", "genesis has missing or unknown fields")
    return value


def _load_pinned_bundle_capability(
    capability: ReadCapability, engine_digest: str,
) -> AssetBundle:
    prefix = f"_engine/{engine_digest}"
    documents: dict[str, Any] = {}
    canonical_assets: dict[str, bytes] = {}
    for name in ASSET_FILES:
        relative = f"{prefix}/{name}"
        try:
            source = capability.read_bytes(relative)
            value = parse_document(source)
            canonical = canonical_bytes(value)
        except JournalError:
            raise
        except (OSError, CanonicalError) as exc:
            raise ContractError(
                "asset_load_failed",
                f"cannot load pinned engine asset {name}: {exc}",
                subject=name,
            ) from exc
        if source != canonical:
            raise ContractError(
                "asset_noncanonical",
                "pinned engine asset bytes are not canonical",
                subject=name,
            )
        documents[name] = value
        canonical_assets[name] = canonical
    for name in SCHEMA_FILES:
        name = f"schemas/{name}"
        relative = f"{prefix}/{name}"
        try:
            source = capability.read_bytes(relative)
            value = parse_document(source)
            canonical = canonical_bytes(value)
        except JournalError:
            raise
        except (OSError, CanonicalError) as exc:
            raise ContractError(
                "asset_load_failed",
                f"cannot load pinned engine schema {name}: {exc}",
                subject=name,
            ) from exc
        if source != canonical:
            raise ContractError(
                "asset_noncanonical",
                "pinned engine schema bytes are not canonical",
                subject=name,
            )
        documents[name] = value
        canonical_assets[name] = canonical
    require_supported_engine(documents)
    for binding in _owned_schema_bindings(documents):
        name = binding["path"]
        if name in documents:
            continue
        relative = f"{prefix}/{name}"
        try:
            source = capability.read_bytes(relative)
            value = parse_document(source)
            canonical = canonical_bytes(value)
        except JournalError:
            raise
        except (OSError, CanonicalError) as exc:
            raise ContractError(
                "owned_schema_load_failed",
                f"cannot load pinned owner schema {name}: {exc}",
                subject=name,
            ) from exc
        if source != canonical:
            raise ContractError(
                "asset_noncanonical",
                "pinned owner schema bytes are not canonical",
                subject=name,
            )
        if digest_bytes(canonical) != binding["digest"]:
            raise ContractError(
                "owned_schema_digest_mismatch",
                "owner schema canonical bytes do not match the catalog binding digest",
                subject=name,
            )
        documents[name] = value
        canonical_assets[name] = canonical
    asset_digests = {
        name: digest_bytes(value)
        for name, value in sorted(canonical_assets.items())
    }
    calculated = digest({
        "schema": "sdm-engine-contract/v1",
        "asset_digests": asset_digests,
    })
    bundle = AssetBundle(
        Path("/__sdm_retained_bundle__") / engine_digest,
        documents,
        canonical_assets,
        asset_digests,
        calculated,
    )
    bundle.self_validate()
    if calculated != engine_digest:
        raise ContractError(
            "engine_digest_mismatch",
            "pinned engine bundle digest does not match genesis",
            subject=engine_digest,
        )
    return bundle


def load_pinned_bundle(
    root: Path,
    engine_digest: str,
    *,
    capability: ReadCapability | None = None,
) -> AssetBundle:
    if capability is None:
        try:
            with ReadCapability(
                root,
                max_nodes=MAX_LEDGER_NODES,
                max_bytes=MAX_LEDGER_BYTES,
            ) as reader:
                return load_pinned_bundle(
                    root, engine_digest, capability=reader,
                )
        except JournalError as exc:
            raise _ledger_error(
                exc, subject=f"_engine/{engine_digest}",
            ) from exc
    _require_capability_root(root, capability)
    try:
        return _load_pinned_bundle_capability(capability, engine_digest)
    except JournalError as exc:
        raise _ledger_error(
            exc, subject=f"_engine/{engine_digest}",
        ) from exc
    except (ContractError, OSError) as exc:
        raise LedgerError("pinned_engine_invalid", f"cannot load pinned engine {engine_digest}: {exc}") from exc


def _validate_event_authority(
    event: dict[str, Any], bundle: AssetBundle,
    prior_events: list[dict[str, Any]],
) -> None:
    payload = event.get("payload", {})
    transition_name = payload.get("transition")
    records = event.get("authority")
    transition: dict[str, Any] | None = None
    if transition_name is not None:
        if not isinstance(transition_name, str) or not transition_name:
            raise LedgerError(
                "transition_binding_invalid",
                "event transition must be one exact pinned transition name",
                sequence=event.get("sequence"),
            )
        try:
            transition = bundle.transition(transition_name)
        except ContractError as exc:
            raise LedgerError(
                "transition_unknown", str(exc), sequence=event.get("sequence"),
            ) from exc
        if transition.get("event_type") != event.get("event_type"):
            raise LedgerError(
                "transition_event_mismatch",
                "event type does not equal the pinned transition event type",
                sequence=event.get("sequence"),
            )

    required_action = (
        transition.get("authority_action")
        if isinstance(transition, dict) else None
    )
    supplied_action = payload.get("authority_action")

    def governed_identity_value(key: str, action: Any) -> Any:
        subjects = event.get("subjects", {})
        if isinstance(subjects, dict) and key in subjects:
            return subjects[key]
        if (
            action in {"experiment.supersede", "decision.supersede"}
            and isinstance(subjects, dict)
        ):
            aliases = {
                "predecessor_experiment_id": "experiment_id",
                "predecessor_decision_id": "decision_id",
            }
            alias = aliases.get(key)
            if alias in subjects:
                return subjects[alias]
        # These are the only governed identities whose canonical location is
        # the transition payload rather than event.subjects.
        if action == "decision.D05" and key == "outcome":
            return payload.get("outcome")
        if action == "engine.migrate" and key == "migration_sequence":
            return str(event.get("sequence"))
        if action == "readiness.approve" and key == "step":
            step = payload.get("step")
            return str(step) if isinstance(step, int) and not isinstance(step, bool) else object()
        if action == "lifecycle.close" and key in {
            "closure_id", "closure_instance_key",
        }:
            return payload.get(key, object())
        if action == "baseline.deprecate" and key in {
            "deprecation_id", "replacement_baseline_id",
        }:
            return payload.get(key, object())
        return object()
    if required_action:
        if supplied_action != required_action:
            raise LedgerError(
                "authority_action_mismatch",
                "authority-gated transition lacks its exact pinned authority action",
                sequence=event.get("sequence"),
            )
        if not isinstance(records, list) or not records:
            provisional_transition = _INHERITED_AUTHORITY_TRANSITIONS.get(
                transition_name,
            )
            if provisional_transition is None:
                raise LedgerError(
                    "authority_required",
                    "authority-gated transition has no consumed authority records",
                    sequence=event.get("sequence"),
                )
            provisional_hash = payload.get("authority_inherited_from")
            provisional = next((
                row for row in prior_events
                if row.get("event_hash") == provisional_hash
            ), None)
            accepted_hash = payload.get("anchor_accepted_event")
            accepted = next((
                row for row in prior_events
                if row.get("event_hash") == accepted_hash
            ), None)
            if (
                not isinstance(provisional, dict)
                or provisional.get("payload", {}).get("transition")
                != provisional_transition
                or provisional.get("payload", {}).get("authority_action")
                != required_action
                or not provisional.get("authority")
                or payload.get("provisional_hash") != provisional_hash
                or not isinstance(accepted, dict)
                or accepted.get("event_type") != "anchor.accepted"
                or accepted.get("sequence", 0) <= provisional.get("sequence", 0)
                or accepted.get("payload", {}).get("provisional_hash")
                != provisional_hash
                or accepted.get("payload", {}).get("anchored_head")
                != provisional_hash
                or (
                    provisional.get("payload", {}).get("logical_operation_id")
                    is not None
                    and payload.get("logical_operation_id")
                    != provisional.get("payload", {}).get(
                        "logical_operation_id"
                    )
                )
                or any(
                    event.get("subjects", {}).get(key) != value
                    for key, value in provisional.get("subjects", {}).items()
                )
            ):
                raise LedgerError(
                    "inherited_authority_invalid",
                    "terminal transition lacks an exact signed provisional and accepted-anchor chain",
                    sequence=event.get("sequence"),
                )
            provisional_payload = provisional.get("payload", {})
            terminal_fields = {
                "protocol.freeze": (
                    "candidate_digest", "frozen_digest", "intent_digest",
                ),
                "decision.accept": ("outcome", "intent_digest"),
                "lifecycle.archive": (
                    "intent_digest", "authority_bound_artifacts_digest",
                ),
                "lifecycle.close": (
                    "intent_digest", "authority_bound_artifacts_digest",
                ),
            }[transition_name]
            if any(
                payload.get(name) != provisional_payload.get(name)
                for name in terminal_fields
            ):
                raise LedgerError(
                    "inherited_authority_effect_mismatch",
                    "terminal effect differs from the signed and externally anchored provisional",
                    sequence=event.get("sequence"),
                )
            signed_subject = provisional_payload.get("authority_subject", {})
            signed_ids = signed_subject.get("governed_ids", {}) \
                if isinstance(signed_subject, dict) else {}
            if (
                not isinstance(signed_ids, dict)
                or any(
                    governed_identity_value(key, required_action) != value
                    for key, value in signed_ids.items()
                )
                or (
                    transition_name == "decision.accept"
                    and signed_ids.get("outcome") != payload.get("outcome")
                )
            ):
                raise LedgerError(
                    "inherited_authority_subject_mismatch",
                    "terminal subjects/effect differ from the signed provisional subject",
                    sequence=event.get("sequence"),
                )
            return
    elif records:
        raise LedgerError(
            "authority_transition_mismatch",
            "authority records are forbidden without an authority-gated pinned transition",
            sequence=event.get("sequence"),
        )
    elif supplied_action not in (None, ""):
        raise LedgerError(
            "authority_action_unexpected",
            "actionless transition/event cannot claim an authority action",
            sequence=event.get("sequence"),
        )

    if not records:
        return
    from authority import AuthorityError, replay_authorities

    subject = payload.get("authority_subject")
    governed_ids = subject.get("governed_ids", {}) \
        if isinstance(subject, dict) else {}
    signed_digests = subject.get("digests", {}) \
        if isinstance(subject, dict) else {}
    if (
        not isinstance(governed_ids, dict)
        or not isinstance(signed_digests, dict)
        or any(
            governed_identity_value(key, supplied_action) != value
            for key, value in governed_ids.items()
        )
    ):
        raise LedgerError(
            "authority_event_subject_mismatch",
            "signed governed identities differ from the event subjects",
            sequence=event.get("sequence"),
        )
    published = payload.get("published_artifacts", [])
    published = published if isinstance(published, list) else []

    def exact_descriptor(row: Any, artifact_id: str) -> bool:
        if not isinstance(row, dict):
            return False
        instance_key = row.get("instance_key")
        return (
            row.get("artifact_id") == artifact_id
            and isinstance(instance_key, str)
            and bool(instance_key)
            and row.get("path")
            == f"artifacts/{artifact_id}/{instance_key}.json"
        )

    action_binding_ok = True
    if supplied_action == "freeze.approve":
        published = payload.get("published_artifacts", [])
        frozen = [
            row for row in published
            if isinstance(row, dict) and row.get("artifact_id") == "E01"
        ]
        action_binding_ok = (
            signed_digests.get("candidate_digest")
            == payload.get("candidate_digest")
            and len(frozen) == 1
            and frozen[0].get("digest") == payload.get("frozen_digest")
        )
    elif supplied_action == "decision.D05":
        decision_id = governed_ids.get("decision_id")
        experiment_id = governed_ids.get("experiment_id")
        expected_instance = (
            f"{experiment_id}.{decision_id}"
            if isinstance(experiment_id, str)
            and isinstance(decision_id, str) else None
        )
        action_binding_ok = (
            set(governed_ids) == {
                "workspace_id", "experiment_id", "evidence_id",
                "decision_id", "outcome",
            }
            and set(signed_digests) == {
                "frozen_spec_digest", "evidence_bundle_digest",
                "rule_digest",
            }
            and governed_ids.get("outcome") == payload.get("outcome")
            and len(published) == 1
            and exact_descriptor(published[0], "D05")
            and published[0].get("instance_key") == expected_instance
            and event.get("subjects", {}).get("artifact_id")
            == f"D05:{expected_instance}"
        )
    elif supplied_action in {
        "amendment.approve", "deviation.accept", "lifecycle.cancel",
        "exception.grant",
    }:
        artifact_id, candidate_key = {
            "amendment.approve": ("E06", "E06_candidate_digest"),
            "deviation.accept": ("E07", "E07_candidate_digest"),
            "lifecycle.cancel": ("S16", "S16_candidate_digest"),
            "exception.grant": ("S15", "S15_candidate_digest"),
        }[supplied_action]
        action_binding_ok = (
            set(signed_digests) == {candidate_key}
            and signed_digests.get(candidate_key)
            == payload.get("authority_candidate_digest")
            and len(published) == 1
            and exact_descriptor(published[0], artifact_id)
            and published[0].get("digest")
            == payload.get("final_record_digest")
        )
    elif supplied_action == "readiness.approve":
        expected_artifact_ids = {
            "baseline.lock": {"B02", "B03"},
            "protocol.readiness": {"E02", "S04"},
            "evidence.review": {"S10"},
        }.get(transition_name)
        descriptor_nodes = [
            (row.get("artifact_id"), row.get("instance_key"))
            for row in published if isinstance(row, dict)
        ]
        action_binding_ok = (
            expected_artifact_ids is not None
            and set(signed_digests) == {"candidate_records_digest"}
            and signed_digests.get("candidate_records_digest")
            == payload.get("authority_candidate_records_digest")
            and len(descriptor_nodes) == len(published)
            and len(published) == len(expected_artifact_ids)
            and all(
                exact_descriptor(row, str(row.get("artifact_id")))
                for row in published
            )
            and len(descriptor_nodes) == len(set(descriptor_nodes))
            and {artifact_id for artifact_id, _ in descriptor_nodes}
            == expected_artifact_ids
        )
    elif supplied_action == "lifecycle.close":
        signed_record_digests = [
            value for key, value in signed_digests.items()
            if key in {"S17_candidate_digest", "S17_digest"}
        ]
        action_binding_ok = (
            len(signed_record_digests) == 1
            and signed_record_digests[0]
            == payload.get("authority_bound_record_digest")
        )
        if "S17_candidate_digest" in signed_digests:
            published = payload.get("published_artifacts", [])
            action_binding_ok = (
                action_binding_ok
                and len(published) == 1
                and published[0].get("artifact_id") == "S17"
                and published[0].get("digest")
                == payload.get("final_record_digest")
            )
    elif supplied_action == "experiment.supersede":
        published = {
            row.get("artifact_id"): row
            for row in payload.get("published_artifacts", [])
            if isinstance(row, dict)
        }
        action_binding_ok = (
            payload.get("authority_bound_digests") == signed_digests
            and set(published) == {"E01", "S02"}
            and published["E01"].get("digest")
            == signed_digests.get("successor_digest")
            and published["S02"].get("digest")
            == signed_digests.get("successor_brief_digest")
            and payload.get("old_id")
            == "E01:" + str(payload.get("predecessor_instance_key", ""))
            and payload.get("new_id")
            == "E01:" + str(published["E01"].get("instance_key", ""))
        )
    elif supplied_action == "decision.supersede":
        published = payload.get("published_artifacts", [])
        action_binding_ok = (
            bool(signed_digests)
            and payload.get("authority_bound_digests") == signed_digests
            and len(published) == 1
            and published[0].get("artifact_id") == "D05"
            and published[0].get("digest")
            == payload.get("final_record_digest")
            and payload.get("old_id") == "D05:" + str(
                payload.get("predecessor_decision_instance_key", "")
            )
            and payload.get("new_id") == "D05:" + str(
                payload.get("successor_decision_instance_key", "")
            )
            and payload.get("authority_candidate_digest")
            == signed_digests.get("D05_candidate_digest")
        )
    elif supplied_action == "baseline.deprecate":
        published = payload.get("published_artifacts", [])
        action_binding_ok = (
            bool(signed_digests)
            and payload.get("authority_bound_digests") == signed_digests
            and len(published) == 1
            and published[0].get("artifact_id") == "B04"
            and published[0].get("digest")
            == payload.get("final_record_digest")
            and payload.get("authority_candidate_digest")
            == signed_digests.get("B04_candidate_digest")
            and digest(payload.get("active_dependents"))
            == signed_digests.get("active_dependents_digest")
        )
    if not action_binding_ok:
        raise LedgerError(
            "authority_event_effect_mismatch",
            "signed authority subject does not bind the governed event effect",
            sequence=event.get("sequence"),
        )

    expected_revision = payload.get("authority_expected_revision")
    domain = payload.get("authority_revision_domain", "lifecycle")
    if domain == "lifecycle":
        if not isinstance(expected_revision, int) or isinstance(
            expected_revision, bool,
        ):
            raise LedgerError(
                "authority_revision_boundary_missing",
                "authority event lacks its signed lifecycle revision boundary",
                sequence=event.get("sequence"),
            )
        sequence = event["sequence"]
        if expected_revision != sequence - 1:
            published_nodes = {
                f"{row.get('artifact_id')}:{row.get('instance_key')}"
                for row in payload.get("published_artifacts", [])
                if isinstance(row, dict)
            }
            intervening = prior_events[expected_revision: sequence - 1]
            if (
                expected_revision < 0
                or expected_revision >= sequence - 1
                or not intervening
                or any(
                    row.get("event_type") != "handoff.cancelled"
                    or row.get("payload", {}).get("governance_node")
                    not in published_nodes
                    for row in intervening
                )
            ):
                raise LedgerError(
                    "authority_revision_boundary_invalid",
                    "authority revision does not equal the event boundary or one exact same-transaction child-disposition boundary",
                    sequence=sequence,
                )
    else:
        raise LedgerError(
            "authority_revision_domain_invalid",
            "authority revision domain must be lifecycle",
            sequence=event.get("sequence"),
        )
    try:
        replay_authorities(event, bundle)
    except AuthorityError as exc:
        # A missing secure verifier or equivalent host capability is a
        # runtime-configuration failure, not corrupt historical evidence.
        # Preserve that distinction through replay so every public caller can
        # return its declared configuration-required outcome.
        if exc.configuration:
            raise
        raise LedgerError(
            exc.code, str(exc), sequence=event.get("sequence"),
        ) from exc


def _validate_migration_payload(
    event: dict[str, Any],
    *,
    active_digest: str,
    new_bundle: AssetBundle,
    workspace_id: str,
) -> None:
    """Bind a migration event to the exact replay boundary and bundle bytes."""

    payload = event.get("payload", {})
    sequence = event.get("sequence")
    new_digest = new_bundle.engine_contract_digest
    asset_digests = dict(sorted(new_bundle.asset_digests.items()))
    inventory_digest = digest(asset_digests)
    expected_subject = {
        "governed_ids": {
            "workspace_id": workspace_id,
            "migration_sequence": str(sequence),
        },
        "digests": {
            "old_engine_contract_digest": active_digest,
            "new_engine_contract_digest": new_digest,
            "migration_boundary_head": event.get("previous_hash"),
            "new_asset_inventory_digest": inventory_digest,
        },
    }
    checks = (
        payload.get("transition") == "engine.migrate",
        payload.get("old_engine_contract_digest") == active_digest,
        payload.get("new_engine_contract_digest") == new_digest,
        active_digest != new_digest,
        payload.get("migration_boundary_revision") == sequence - 1,
        payload.get("migration_boundary_head") == event.get("previous_hash"),
        payload.get("new_asset_digests") == asset_digests,
        payload.get("new_asset_inventory_digest") == inventory_digest,
        payload.get("authority_subject") == expected_subject,
        event.get("subjects", {}).get("workspace_id") == workspace_id,
        "engine.migrated" in new_bundle.event_types(),
    )
    if not all(checks):
        raise LedgerError(
            "migration_binding_invalid",
            "engine migration does not bind the exact old/new bundle inventory and replay boundary",
            sequence=sequence,
        )


def _replay_capability(
    root: Path, capability: ReadCapability,
) -> ReplayResult:
    genesis = load_genesis(root, capability=capability)
    active_digest = genesis["engine_contract_digest"]
    bundle = load_pinned_bundle(
        root, active_digest, capability=capability,
    )
    if bundle.asset_digests != genesis["asset_digests"]:
        raise LedgerError("genesis_asset_mismatch", "genesis asset digests do not match its pinned engine bundle")
    bundles = {active_digest: bundle}
    previous = genesis_hash(genesis)
    events: list[dict[str, Any]] = []
    try:
        events_fd = capability.open_directory("_ledger/events")
        try:
            entries = capability.scan_regular_files(
                events_fd, "_ledger/events",
            )
            for entry in entries:
                match = _EVENT_FILE.fullmatch(entry.name)
                if match is None:
                    raise LedgerError(
                        "unexpected_ledger_file",
                        f"event ledger contains an unexpected file: {entry.name}",
                    )
            for expected, entry in enumerate(entries, 1):
                match = _EVENT_FILE.fullmatch(entry.name)
                assert match is not None
                filename_sequence = int(match.group(1))
                if filename_sequence != expected:
                    raise LedgerError("ledger_gap", f"event sequence is missing, duplicated, or reordered at {expected}", sequence=expected)
                try:
                    event = parse_document(
                        capability.read_entry(events_fd, entry),
                    )
                except JournalError as exc:
                    raise _ledger_error(
                        exc, subject=entry.relative,
                    ) from exc
                except (OSError, ValueError) as exc:
                    raise LedgerError("event_invalid", f"cannot parse event {expected}: {exc}", sequence=expected) from exc
                if not isinstance(event, dict):
                    raise LedgerError("event_invalid", "event must be an object", sequence=expected)
                if event.get("sequence") != expected:
                    raise LedgerError("event_sequence_mismatch", "event body sequence differs from filename/order", sequence=expected)
                if event.get("previous_hash") != previous:
                    raise LedgerError("event_chain_mismatch", "event previous_hash does not match reconstructed head", sequence=expected)
                if event.get("engine_contract_digest") != active_digest:
                    raise LedgerError("event_engine_mismatch", "event was not interpreted with the active pinned engine", sequence=expected)
                without_hash = dict(event)
                claimed = without_hash.pop("event_hash", None)
                computed = event_hash(previous, without_hash)
                if claimed != computed:
                    raise LedgerError("event_hash_mismatch", "event hash does not match canonical chained bytes", sequence=expected)
                schema_findings = validate_schema(event, bundle.document("schemas/event.schema.json"), subject=f"event:{expected}", governing_asset="schemas/event.schema.json")
                if schema_findings:
                    raise LedgerError("event_schema_invalid", schema_findings[0]["message"], sequence=expected)
                if event.get("event_type") not in bundle.event_types():
                    raise LedgerError("event_type_unknown", f"event type is not declared by pinned engine: {event.get('event_type')}", sequence=expected)
                _validate_event_authority(event, bundle, events)
                events.append(event)
                previous = claimed
                if event.get("event_type") == "engine.migrated":
                    next_digest = event.get("payload", {}).get("new_engine_contract_digest")
                    if not isinstance(next_digest, str):
                        raise LedgerError("migration_invalid", "engine migration lacks new_engine_contract_digest", sequence=expected)
                    if next_digest not in bundles:
                        bundles[next_digest] = load_pinned_bundle(
                            root, next_digest, capability=capability,
                        )
                    _validate_migration_payload(
                        event,
                        active_digest=active_digest,
                        new_bundle=bundles[next_digest],
                        workspace_id=genesis["workspace_id"],
                    )
                    active_digest = next_digest
                    bundle = bundles[next_digest]
        finally:
            os.close(events_fd)
    except JournalError as exc:
        raise _ledger_error(exc, subject="_ledger/events") from exc
    return ReplayResult(genesis, tuple(events), len(events), previous, active_digest, bundles)


def replay(
    workspace: str | os.PathLike[str],
    *,
    capability: ReadCapability | None = None,
) -> ReplayResult:
    root = Path(os.path.abspath(os.fspath(workspace)))
    if capability is not None:
        try:
            _require_capability_root(root, capability)
            return _replay_capability(root, capability)
        except JournalError as exc:
            raise _ledger_error(exc, subject="workspace") from exc
    try:
        with ReadCapability(
            root,
            max_nodes=MAX_LEDGER_NODES,
            max_bytes=MAX_LEDGER_BYTES,
        ) as reader:
            return _replay_capability(root, reader)
    except JournalError as exc:
        raise _ledger_error(exc, subject="workspace") from exc


def plan_append(
    transaction: Transaction,
    replayed: ReplayResult,
    *,
    event_type: str,
    actor: dict[str, Any],
    subjects: dict[str, Any],
    payload: dict[str, Any],
    operation_id: str,
    authority: list[dict[str, Any]] | None = None,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    bundle = replayed.bundles[replayed.active_engine_digest]
    if event_type not in bundle.event_types():
        raise LedgerError("event_type_unknown", f"event type is not declared by pinned engine: {event_type}")
    event = build_event(
        sequence=replayed.revision + 1,
        event_type=event_type,
        previous_hash=replayed.head_hash,
        engine_contract_digest=replayed.active_engine_digest,
        actor=actor,
        subjects=subjects,
        payload=payload,
        operation_id=operation_id,
        authority=authority,
        occurred_at=occurred_at,
    )
    schema_findings = validate_schema(event, bundle.document("schemas/event.schema.json"), subject=f"event:{event['sequence']}", governing_asset="schemas/event.schema.json")
    if schema_findings:
        raise LedgerError("event_schema_invalid", schema_findings[0]["message"], sequence=event["sequence"])
    transaction.add(f"_ledger/events/{event['sequence']:020d}.json", event, exclusive=True)
    return event


def replay_with(events: Iterable[dict[str, Any]], base: ReplayResult) -> ReplayResult:
    """Create an in-memory replay result extended by already validated new events."""
    combined = list(base.events)
    previous = base.head_hash
    revision = base.revision
    active = base.active_engine_digest
    bundles = dict(base.bundles)
    for event in events:
        revision += 1
        if event.get("sequence") != revision or event.get("previous_hash") != previous:
            raise LedgerError("event_chain_mismatch", "in-memory appended event is not contiguous")
        bundle = bundles.get(active)
        if bundle is None:
            raise LedgerError(
                "pinned_engine_invalid",
                "in-memory replay lacks the active pinned engine bundle",
                sequence=revision,
            )
        _validate_event_authority(event, bundle, combined)
        combined.append(event)
        previous = event["event_hash"]
        if event.get("event_type") == "engine.migrated":
            next_digest = event.get("payload", {}).get(
                "new_engine_contract_digest",
            )
            next_bundle = bundles.get(next_digest)
            if next_bundle is None:
                raise LedgerError(
                    "pinned_engine_invalid",
                    "in-memory migration lacks its complete successor bundle",
                    sequence=revision,
                )
            _validate_migration_payload(
                event,
                active_digest=active,
                new_bundle=next_bundle,
                workspace_id=base.genesis["workspace_id"],
            )
            active = next_digest
    return ReplayResult(base.genesis, tuple(combined), revision, previous, active, bundles)


def _diagnostic_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect the SDM event-ledger library")
    parser.parse_args(argv)
    sys.stdout.write(json.dumps({"status": "pass", "command": "ledger.help"}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(_diagnostic_main())
