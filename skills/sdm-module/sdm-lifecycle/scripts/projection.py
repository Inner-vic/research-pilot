#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Deterministic SDM projection, transition evaluation, and T01 graph replay."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any, Iterable

from canonical import canonical_bytes, digest, parse_document
from contracts import (
    AssetBundle, ContractError, PRIMARY_IDS, SUPPORTING_IDS, evaluate_gate,
    sort_findings, finding, validate_schema,
)
from journal import JournalError, ReadCapability
from ledger import ReplayResult


class ProjectionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _stored_projection_error(exc: BaseException) -> ProjectionError:
    code = (
        exc.code
        if isinstance(exc, JournalError) and exc.code in {
            "concurrent_mutation", "mount_escape",
            "mount_identity_unavailable",
        }
        else "stored_projection_invalid"
    )
    return ProjectionError(code, f"cannot read stored projection: {exc}")


_TYPED_RELATION_ENDPOINTS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "requirement-to-bmad": (frozenset({"S01"}), frozenset({"S03"})),
    "bmad-to-experiment": (frozenset({"S03"}), frozenset({"E01"})),
    "experiment-to-run": (frozenset({"E01"}), frozenset({"Run"})),
    "run-to-attempt": (frozenset({"Run"}), frozenset({"Attempt"})),
    "attempt-to-receipt": (frozenset({"Attempt"}), frozenset({"S13"})),
    "receipt-to-evidence": (
        frozenset({"S13"}), frozenset({"D01", "D02", "D03", "D04"}),
    ),
    "evidence-to-decision": (
        frozenset({"D01", "D02", "D03", "D04"}), frozenset({"D05"}),
    ),
}
_LINEAGE_RELATIONS = frozenset({
    "derived-from", "superseded-by", "supersedes",
    *_TYPED_RELATION_ENDPOINTS,
})
_LINEAGE_NODE_TYPES = frozenset({*PRIMARY_IDS, *SUPPORTING_IDS, "Run", "Attempt"})


def _put_lineage_node(
    state: dict[str, Any], node_id: str, node_type: str,
    event_hash: str, **facts: Any,
) -> dict[str, Any]:
    """Install or consistently enrich one replay-derived semantic node."""

    nodes = state["lineage"]["nodes"]
    current = nodes.get(node_id)
    if current is None:
        current = {"type": node_type, "event_hash": event_hash, **deepcopy(facts)}
        nodes[node_id] = current
        return current
    if not isinstance(current, dict) or current.get("type") != node_type:
        raise ProjectionError(
            "lineage_node_conflict",
            f"lineage node {node_id} is rebound to another endpoint type",
        )
    for key, value in facts.items():
        if key in current and current[key] != value:
            raise ProjectionError(
                "lineage_node_conflict",
                f"lineage node {node_id} has conflicting authenticated {key}",
            )
        current[key] = deepcopy(value)
    return current


def _append_projection_edge(
    graph: dict[str, Any], source: str, target: str, relation: str,
) -> None:
    """Append a deterministic projection-owned edge exactly once."""

    candidate = {"from": source, "to": target, "relation": relation}
    if candidate not in graph["edges"]:
        graph["edges"].append(candidate)


def _node_type(graph: dict[str, Any], node_id: Any) -> str | None:
    node = graph.get("nodes", {}).get(node_id)
    return node.get("type") if isinstance(node, dict) else None


def _record_execution_lineage(
    state: dict[str, Any], event: dict[str, Any],
) -> None:
    """Project Run/Attempt identities and receipt anchors from execution events."""

    if event.get("event_type") not in {
        "run.dispatched", "attempt.started", "attempt.terminal",
    }:
        return
    subjects = event.get("subjects", {})
    run_id = subjects.get("run_id")
    attempt_id = subjects.get("attempt_id")
    if not isinstance(run_id, str) or not isinstance(attempt_id, str):
        return  # _apply_attempt_event emits the authoritative shape error.
    run_node = f"Run:{run_id}"
    manifest_node = f"R01:{run_id}"
    if manifest_node not in state["lineage"]["nodes"]:
        raise ProjectionError(
            "run_manifest_lineage_missing",
            f"Run {run_id} lacks its authenticated R01 lineage node",
        )
    _put_lineage_node(
        state, run_node, "Run", event["event_hash"],
        run_id=run_id, manifest_node=manifest_node,
    )
    attempt_node = f"Attempt:{attempt_id}"
    current = state["lineage"]["nodes"].get(attempt_node)
    request_ids = event.get("payload", {}).get("request_ids", [])
    if not isinstance(request_ids, list) or any(
        not isinstance(request_id, str) or not request_id
        for request_id in request_ids
    ):
        raise ProjectionError(
            "attempt_receipt_reference_invalid",
            "execution receipt request_ids must be a string array",
        )
    if current is None:
        _put_lineage_node(
            state, attempt_node, "Attempt", event["event_hash"],
            attempt_id=attempt_id, run_id=run_id,
            receipt_request_ids=sorted(set(request_ids)),
        )
    else:
        if (
            not isinstance(current, dict)
            or current.get("type") != "Attempt"
            or current.get("run_id") != run_id
        ):
            raise ProjectionError(
                "attempt_lineage_conflict",
                f"Attempt {attempt_id} is rebound to a different Run",
            )
        current["receipt_request_ids"] = sorted({
            *current.get("receipt_request_ids", []), *request_ids,
        })


def _synthesize_typed_lineage(state: dict[str, Any]) -> None:
    """Derive the typed T01 spine solely from replay-authenticated facts."""

    graph = state["lineage"]

    # Direct governed-artifact adjacencies.  Generic derived-from remains in
    # the graph; the typed edge is an additional queryable contract edge.
    direct = {
        ("S01", "S03"): "requirement-to-bmad",
        ("S03", "E01"): "bmad-to-experiment",
        ("D01", "D05"): "evidence-to-decision",
        ("D02", "D05"): "evidence-to-decision",
        ("D03", "D05"): "evidence-to-decision",
        ("D04", "D05"): "evidence-to-decision",
    }
    for edge in list(graph["edges"]):
        if not isinstance(edge, dict) or edge.get("relation") != "derived-from":
            continue
        source = edge.get("from")
        target = edge.get("to")
        relation = direct.get((_node_type(graph, source), _node_type(graph, target)))
        if relation is not None:
            _append_projection_edge(graph, source, target, relation)

    # A reciprocal successor Experiment inherits the immutable BMAD planning
    # root of its predecessor.  This keeps the typed spine complete without
    # pretending the successor descriptor had a new planning input.
    changed = True
    while changed:
        changed = False
        typed_before = {
            (edge.get("from"), edge.get("to"), edge.get("relation"))
            for edge in graph["edges"] if isinstance(edge, dict)
        }
        for edge in list(graph["edges"]):
            if (
                not isinstance(edge, dict)
                or edge.get("relation") != "derived-from"
                or _node_type(graph, edge.get("from")) != "E01"
                or _node_type(graph, edge.get("to")) != "E01"
            ):
                continue
            predecessor = edge["from"]
            successor = edge["to"]
            for candidate in list(graph["edges"]):
                if (
                    isinstance(candidate, dict)
                    and candidate.get("relation") == "bmad-to-experiment"
                    and candidate.get("to") == predecessor
                ):
                    _append_projection_edge(
                        graph, candidate["from"], successor,
                        "bmad-to-experiment",
                    )
        typed_after = {
            (edge.get("from"), edge.get("to"), edge.get("relation"))
            for edge in graph["edges"] if isinstance(edge, dict)
        }
        changed = typed_after != typed_before

    # Each semantic Run is bound to exactly the Experiment parent of its R01.
    for run_node, run in sorted(graph["nodes"].items()):
        if not isinstance(run, dict) or run.get("type") != "Run":
            continue
        manifest_node = run.get("manifest_node")
        for edge in list(graph["edges"]):
            if (
                isinstance(edge, dict)
                and edge.get("relation") == "derived-from"
                and edge.get("to") == manifest_node
                and _node_type(graph, edge.get("from")) == "E01"
            ):
                _append_projection_edge(
                    graph, edge["from"], run_node, "experiment-to-run",
                )

    # Attempt identities are event subjects, never inferred from artifact
    # filenames.  Receipt links use exact request_ids carried by those events,
    # plus the same exact Run/Attempt subjects on the authenticated S13.
    for attempt_node, attempt in sorted(graph["nodes"].items()):
        if not isinstance(attempt, dict) or attempt.get("type") != "Attempt":
            continue
        run_node = f"Run:{attempt.get('run_id')}"
        if run_node in graph["nodes"]:
            _append_projection_edge(
                graph, run_node, attempt_node, "run-to-attempt",
            )
        request_ids = set(attempt.get("receipt_request_ids", []))
        for receipt_node, receipt in sorted(graph["nodes"].items()):
            if not isinstance(receipt, dict) or receipt.get("type") != "S13":
                continue
            subjects = receipt.get("subjects", {})
            subject_match = (
                isinstance(subjects, dict)
                and subjects.get("run_id") == attempt.get("run_id")
                and subjects.get("attempt_id") == attempt.get("attempt_id")
            )
            if receipt.get("request_id") in request_ids or subject_match:
                _append_projection_edge(
                    graph, attempt_node, receipt_node, "attempt-to-receipt",
                )

    # Persist the exact dynamic receipt cardinality on each receipt.  The
    # validator can consequently detect a same-type receipt/Attempt swap.
    for receipt_node, receipt in sorted(graph["nodes"].items()):
        if not isinstance(receipt, dict) or receipt.get("type") != "S13":
            continue
        attempt_ids = sorted({
            edge["from"] for edge in graph["edges"]
            if isinstance(edge, dict)
            and edge.get("relation") == "attempt-to-receipt"
            and edge.get("to") == receipt_node
        })
        if attempt_ids:
            receipt["trace_role"] = "attempt-receipt"
            receipt["attempt_nodes"] = attempt_ids

    # Evidence aggregates R07/R08 for accounted Attempts.  Follow those exact
    # Attempt identities to the terminal S13 nodes and freeze the resulting
    # dynamic cardinality into the derived node metadata.
    for evidence_node, evidence in sorted(graph["nodes"].items()):
        if not isinstance(evidence, dict) or evidence.get("type") not in {
            "D01", "D02", "D03", "D04",
        }:
            continue
        attempt_nodes = {
            f"Attempt:{str(edge.get('from')).split(':', 1)[1]}"
            for edge in graph["edges"]
            if isinstance(edge, dict)
            and edge.get("relation") == "derived-from"
            and edge.get("to") == evidence_node
            and _node_type(graph, edge.get("from")) in {"R07", "R08"}
            and ":" in str(edge.get("from"))
        }
        receipt_nodes = sorted({
            edge["to"] for edge in graph["edges"]
            if isinstance(edge, dict)
            and edge.get("relation") == "attempt-to-receipt"
            and edge.get("from") in attempt_nodes
        })
        evidence["receipt_nodes"] = receipt_nodes
        for receipt_node in receipt_nodes:
            _append_projection_edge(
                graph, receipt_node, evidence_node, "receipt-to-evidence",
            )

    # Freeze replay-derived identities alongside every typed downstream node.
    # The graph validator can then check the exact authenticated endpoint, not
    # merely its type and cardinality.  These fields are projection-owned.
    identity_fields = {
        "S03": ("requirement-to-bmad", "requirement_nodes"),
        "E01": ("bmad-to-experiment", "bmad_nodes"),
        "Run": ("experiment-to-run", "experiment_nodes"),
        "D05": ("evidence-to-decision", "evidence_nodes"),
    }
    for node_id, node in sorted(graph["nodes"].items()):
        if not isinstance(node, dict):
            continue
        binding = identity_fields.get(node.get("type"))
        if binding is None:
            continue
        relation, field = binding
        node[field] = sorted({
            edge["from"] for edge in graph["edges"]
            if isinstance(edge, dict)
            and edge.get("relation") == relation
            and edge.get("to") == node_id
            and isinstance(edge.get("from"), str)
        })


def initial_state(replayed: ReplayResult) -> dict[str, Any]:
    return {
        "schema": "sdm-state/v1",
        "workspace_id": replayed.genesis["workspace_id"],
        "revision": 0,
        "ledger_head": digest(replayed.genesis),
        "active_engine_digest": replayed.genesis["engine_contract_digest"],
        "lifecycle_step": 1,
        "dimensions": {
            "jira": "absent",
            "experiment": "absent",
            "contract_readiness": "open",
            "baseline": "absent",
            "protocol": "absent",
            "implementation": "absent",
            "preflight": "pending",
            "execution": "absent",
            "attempt": "planned",
            "validity": "not_evaluable",
            "evidence": "pending",
            "scientific": "pending",
            "robustness": "pending",
            "successor_experiment": "absent",
            "lifecycle": "active",
            "anchor": "none",
        },
        "artifacts": {},
        "attempts": {},
        "handoffs": {},
        "operations": {},
        "authorities": {},
        "lineage": {"nodes": {}, "edges": []},
        "substate_events": [],
        "findings": [],
        "binding_skill_digests": {},
    }


def _rows(document: Any, key: str) -> list[Any]:
    if not isinstance(document, dict) or not isinstance(document.get(key), list):
        raise ProjectionError("asset_shape_invalid", f"asset lacks {key} array")
    return document[key]


def _set_path(target: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    current = target
    for part in parts[:-1]:
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise ProjectionError("transition_effect_invalid", f"cannot set nested state through scalar: {dotted}")
        current = child
    current[parts[-1]] = value


def _transition_records(bundle: AssetBundle, event: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    requested = event.get("payload", {}).get("transition")
    # Event types can intentionally be shared by unrelated transitions (most
    # notably artifact.published).  Absence of an exact transition identity is
    # never permission to run every row with that event type.
    if not isinstance(requested, str) or not requested:
        return records
    for row in _rows(bundle.document("transition-requirements.json"), "transitions"):
        if row.get("event_type") == event["event_type"] and row.get("name") == requested:
            records.append(row)
    return records


def _apply_transition_effects(state: dict[str, Any], row: dict[str, Any], event: dict[str, Any]) -> None:
    # A lifecycle-row completion carries the same transition in the row data;
    # _apply_lifecycle_step validates and applies it exactly once.
    if event.get("payload", {}).get("lifecycle_step_completion") is True:
        return
    # Provisional events deliberately have no domain-state gate effect.  Their
    # exact current state is retained in pending_anchor and is enacted only by
    # the bound anchor/result/final event sequence.
    if row.get("name") in {
        "protocol.freeze-provisional", "decision.provisional",
        "lifecycle.archive-provisional", "lifecycle.close-provisional",
    }:
        return
    required_from = row.get("from")
    if isinstance(required_from, dict):
        for dotted, expected in required_from.items():
            actual: Any = state
            for part in dotted.split("."):
                actual = actual.get(part) if isinstance(actual, dict) else None
            allowed = expected if isinstance(expected, list) else [expected]
            if actual not in allowed:
                raise ProjectionError("transition_source_invalid", f"transition {row.get('name')} cannot start from {dotted}={actual}")
    effects = row.get("effects", row.get("to", {}))
    if isinstance(effects, dict):
        for dotted, value in effects.items():
            if isinstance(value, str) and value.startswith("$payload."):
                actual: Any = event.get("payload", {})
                for part in value[9:].split("."):
                    actual = actual.get(part) if isinstance(actual, dict) else None
                value = actual
            _set_path(state, dotted, deepcopy(value))
    elif isinstance(required_from, str) and isinstance(effects, str):
        transition_name = row.get("name")
        # Per-Attempt and handoff state machines have subject-keyed projection
        # handlers; applying their legacy strings to one global dimension
        # would combine unrelated Attempts/requests.
        if transition_name in {
            "run.dispatch", "attempt.start", "attempt.terminal",
            "attempt.retry", "hpo.select", "confirmation.materialize",
            "handoff.create", "handoff.dispatch", "handoff.ingest",
            "handoff.retry", "handoff.supersede",
            "handoff.reconciliation-required", "handoff.reconcile",
        }:
            return
        dimension_by_name = {
            "contract.ready": "contract_readiness",
            "baseline.lock": "baseline", "baseline.deprecate": "baseline",
            "protocol.readiness": "protocol",
            "protocol.freeze-provisional": "protocol",
            "protocol.freeze": "protocol", "protocol.amend": "protocol",
            "protocol.deviate": "protocol",
            "experiment.supersede": "experiment",
            "implementation.start": "implementation",
            "implementation.review": "implementation",
            "implementation.accept": "implementation",
            "preflight.accept": "preflight",
            "run.materialize": "execution",
            "evaluation.accept": "execution",
            "robustness.assess": "robustness",
            "evidence.build": "evidence", "evidence.review": "evidence",
            "decision.provisional": "scientific",
            "decision.accept": "scientific",
            "decision.supersede": "scientific",
        }
        dimension = dimension_by_name.get(str(transition_name))
        if dimension is None:
            return
        actual = state.get("dimensions", {}).get(dimension)
        if required_from == "any-non-closed":
            source_ok = state.get("dimensions", {}).get("lifecycle") != "closed"
        elif required_from == "scientific-terminal":
            source_ok = actual in {
                "hypothesis_supported", "hypothesis_rejected",
                "inconclusive", "invalid",
            }
        else:
            source_ok = actual in required_from.split("|")
        if not source_ok:
            raise ProjectionError(
                "transition_source_invalid",
                f"transition {transition_name} cannot start from {dimension}={actual}",
            )
        choices = effects.split("|")
        selected = (
            event.get("payload", {}).get("transition_target")
            or event.get("payload", {}).get("outcome")
            or event.get("payload", {}).get("target")
            or event.get("payload", {}).get("terminal_status")
            or event.get("payload", {}).get("status")
        )
        if effects == "unchanged":
            selected = actual
        elif len(choices) == 1:
            selected = choices[0]
        elif selected not in choices:
            raise ProjectionError(
                "transition_target_required",
                f"transition {transition_name} requires an authenticated target in {choices}",
            )
        state["dimensions"][dimension] = selected


def _apply_attempt_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    kind = event["event_type"]
    if kind not in {
        "run.dispatched", "attempt.started", "attempt.terminal",
        "attempt.retry-created",
    }:
        return
    subjects = event.get("subjects", {})
    attempt_id = subjects.get("attempt_id")
    run_id = subjects.get("run_id")
    if not isinstance(attempt_id, str) or not isinstance(run_id, str):
        raise ProjectionError(
            "attempt_subject_invalid",
            f"{kind} requires exact Run and Attempt subjects",
        )
    attempts = state.setdefault("attempts", {})
    current = attempts.get(attempt_id)
    status = current.get("status") if isinstance(current, dict) else "planned"
    if kind == "run.dispatched":
        if status != "planned":
            raise ProjectionError(
                "run_dispatch_out_of_order",
                f"Attempt {attempt_id} cannot dispatch from {status}",
            )
        if run_id not in state.get("artifacts", {}).get("R01", {}):
            raise ProjectionError(
                "run_manifest_missing",
                "run.dispatched requires the materialized exact R01",
            )
        attempts[attempt_id] = {
            "run_id": run_id, "status": "dispatched",
            "dispatch_event": event["event_hash"],
        }
    elif kind == "attempt.started":
        if status != "dispatched" or current.get("run_id") != run_id:
            raise ProjectionError(
                "attempt_start_out_of_order",
                f"Attempt {attempt_id} must be dispatched before start",
            )
        current["status"] = "running"
        current["start_event"] = event["event_hash"]
    elif kind == "attempt.terminal":
        terminal = event.get("payload", {}).get(
            "terminal_status", event.get("payload", {}).get("status"),
        )
        if (
            status not in {"dispatched", "running"}
            or current.get("run_id") != run_id
            or terminal not in {"successful", "failed", "cancelled", "invalid"}
        ):
            raise ProjectionError(
                "attempt_terminal_out_of_order",
                f"Attempt {attempt_id} cannot become {terminal} from {status}",
            )
        current.update({
            "status": terminal,
            "terminal_event": event["event_hash"],
            "accounted": event.get("payload", {}).get("accounted") is True,
            "validity": event.get("payload", {}).get("validity"),
            "system_failure": event.get("payload", {}).get("system_failure"),
        })
    else:
        successor_id = event.get("payload", {}).get("successor_attempt_id")
        if (
            status not in {"failed", "cancelled", "invalid"}
            or not isinstance(successor_id, str)
            or not successor_id
            or successor_id == attempt_id
            or successor_id in attempts
        ):
            raise ProjectionError(
                "attempt_retry_out_of_order",
                "Attempt retry requires one terminal failed/cancelled/invalid predecessor and a new identity",
            )
        attempts[successor_id] = {
            "run_id": run_id, "status": "planned",
            "retry_of": attempt_id,
            "retry_event": event["event_hash"],
        }


def lifecycle_step_applicability(
    state: dict[str, Any], step: int,
) -> tuple[bool, dict[str, Any]]:
    """Scientific follow-up remains applicable for every canonical outcome."""
    if step == 21:
        basis = {
            "scientific": state.get("dimensions", {}).get("scientific"),
            "successor_experiment": state.get("dimensions", {}).get("successor_experiment", "absent"),
        }
        return basis["successor_experiment"] == "absent", basis
    return 1 <= step <= 21, {"step": step}


def _apply_lifecycle_step(state: dict[str, Any], event: dict[str, Any], bundle: AssetBundle) -> None:
    step_number = event.get("payload", {}).get("step")
    steps = _rows(bundle.document("lifecycle-steps.json"), "steps")
    if not isinstance(step_number, int) or step_number < 1 or step_number > 21:
        raise ProjectionError("lifecycle_step_invalid", "lifecycle advance event has invalid step")
    if step_number != state["lifecycle_step"]:
        raise ProjectionError(
            "lifecycle_step_out_of_order",
            "only the earliest current lifecycle row may advance",
        )
    row = steps[step_number - 1]
    if event.get("event_type") == "lifecycle.step-skipped":
        applicable, basis = lifecycle_step_applicability(state, step_number)
        if (
            applicable
            or event.get("payload", {}).get("applicability")
            != "not_applicable"
            or event.get("payload", {}).get("basis") != basis
            or event.get("payload", {}).get("applicability_digest")
            != digest({"step": step_number, "basis": basis})
            or event.get("payload", {}).get("outputs") not in (None, [])
        ):
            raise ProjectionError(
                "lifecycle_skip_invalid",
                "lifecycle row skip does not equal replay-derived applicability",
            )
        if step_number == 21 and event.get("payload", {}).get(
            "skip_reason"
        ) != "STEP21.no-successor":
            raise ProjectionError(
                "lifecycle_skip_invalid",
                "STEP21 skip must persist its exact no-successor reason",
            )
        state["substate_events"].append({
            "name": (
                "STEP21.no-successor" if step_number == 21
                else f"STEP{step_number:02d}.not-applicable"
            ),
            "event_hash": event["event_hash"],
        })
        state["lifecycle_step"] = min(22, step_number + 1)
        return
    transition = row.get("state_transition", row.get("transition"))
    if isinstance(transition, dict) and {"dimension", "to"}.issubset(transition):
        dimension = transition["dimension"]
        current = state["dimensions"].get(dimension)
        allowed_from = transition.get("from")
        allowed = allowed_from if isinstance(allowed_from, list) else (allowed_from.split("|") if isinstance(allowed_from, str) else [allowed_from])
        if allowed_from is not None and current not in allowed:
            raise ProjectionError("transition_source_invalid", f"step {step_number} expects {dimension} in {allowed}, got {current}")
        declared_to = transition["to"]
        choices = declared_to.split("|") if isinstance(declared_to, str) else list(declared_to) if isinstance(declared_to, list) else [declared_to]
        selected = event.get("payload", {}).get("transition_target") or event.get("payload", {}).get("outcome") or event.get("payload", {}).get("target")
        if len(choices) > 1:
            if selected not in choices:
                raise ProjectionError("transition_target_required", f"step {step_number} requires one explicit transition_target from {choices}")
        else:
            selected = choices[0]
        state["dimensions"][dimension] = selected
        if dimension == "execution" and selected == "planned":
            state["dimensions"]["attempt"] = "planned"
        if dimension == "attempt" and selected == "terminal":
            state["dimensions"]["execution"] = "terminal"
    elif isinstance(transition, dict):
        for key, value in transition.items():
            dotted = key if "." in key else f"dimensions.{key}"
            _set_path(state, dotted, value)
    elif isinstance(transition, str) and "=" in transition:
        key, value = transition.split("=", 1)
        _set_path(state, f"dimensions.{key.strip()}", value.strip())
    outputs = event.get("payload", {}).get("outputs", [])
    declared_outputs = row.get("outputs", []) + row.get("governed_outputs", []) + row.get("supporting_outputs", [])
    if any(output not in declared_outputs for output in outputs):
        raise ProjectionError("undeclared_step_output", f"step {step_number} event claims an output not declared by pinned lifecycle assets")
    for name in event.get("payload", {}).get("substate_events", []):
        state["substate_events"].append({"name": name, "event_hash": event["event_hash"]})
    state["lifecycle_step"] = min(22, max(state["lifecycle_step"], step_number + 1))


def apply_event(state: dict[str, Any], event: dict[str, Any], bundle: AssetBundle) -> None:
    kind = event["event_type"]
    payload = event.get("payload", {})
    if event["operation_id"] in state["operations"]:
        raise ProjectionError("duplicate_operation", f"operation ID appears in more than one ledger event: {event['operation_id']}")
    state["operations"][event["operation_id"]] = {"event_hash": event["event_hash"], "event_type": kind}
    _apply_attempt_event(state, event)
    _record_execution_lineage(state, event)
    if kind == "workspace.created":
        state["bindings"] = deepcopy(payload.get("bindings", {}))
        state["binding_skill_digests"] = deepcopy(
            payload.get("binding_skill_digests", {})
        )
        state["protocol"] = payload.get("protocol", "exploratory")
        state["intent"] = {
            key: deepcopy(payload.get(key))
            for key in ("intent", "constraints", "non_goals", "impact", "owner", "jira", "hypothesis", "mechanism", "uncertainty", "planning_refs")
        }
        # Presence of free-form create inputs is not proof that a semantic
        # lifecycle check passed.  Checks are advanced only by explicit
        # structured facts/events supplied to the gate evaluator.
        state["checks"] = {}
    elif kind == "lifecycle.advanced" or payload.get("lifecycle_step_completion") is True:
        _apply_lifecycle_step(state, event, bundle)
    for row in _transition_records(bundle, event):
        _apply_transition_effects(state, row, event)
    published = payload.get("published_artifacts", [])
    if kind == "artifact.published" and isinstance(payload.get("artifact_id"), str):
        published = [payload]
    for artifact_payload in published:
        artifact_id = artifact_payload.get("artifact_id")
        instance_key = artifact_payload.get("instance_key", "default")
        if not isinstance(artifact_id, str) or not isinstance(instance_key, str):
            raise ProjectionError("artifact_event_invalid", "artifact event lacks artifact ID or instance key")
        bundle.artifact_contract(artifact_id)
        instances = state["artifacts"].setdefault(artifact_id, {})
        if instance_key in instances:
            raise ProjectionError("artifact_overwrite", f"immutable artifact instance already exists: {artifact_id}/{instance_key}")
        instances[instance_key] = {
            "digest": artifact_payload.get("digest"),
            "path": artifact_payload.get("path"),
            "event_hash": event["event_hash"],
            "status": artifact_payload.get("status", "published"),
        }
        node_id = f"{artifact_id}:{instance_key}"
        _put_lineage_node(
            state, node_id, artifact_id, event["event_hash"],
            artifact_digest=artifact_payload.get("digest"),
        )
        for parent in artifact_payload.get("parents", []):
            state["lineage"]["edges"].append({"from": parent, "to": node_id, "relation": "derived-from"})
        if artifact_id == "R01":
            run_node = f"Run:{instance_key}"
            _put_lineage_node(
                state, run_node, "Run", event["event_hash"],
                run_id=instance_key, manifest_node=node_id,
            )
            _append_projection_edge(
                state["lineage"], node_id, run_node, "derived-from",
            )
        elif artifact_id in {"R07", "R08"}:
            attempt_node = f"Attempt:{instance_key}"
            if attempt_node in state["lineage"]["nodes"]:
                _append_projection_edge(
                    state["lineage"], attempt_node, node_id, "derived-from",
                )
    if kind.startswith("handoff."):
        request_id = payload.get("request_id")
        if not isinstance(request_id, str):
            raise ProjectionError("handoff_event_invalid", "handoff event lacks request_id")
        record = state["handoffs"].setdefault(request_id, {"request_id": request_id, "history": []})
        record["history"].append({"event_type": kind, "event_hash": event["event_hash"]})
        status_map = {
            "handoff.configuration-required": "configuration_required",
            "handoff.created": "created",
            "handoff.dispatched": "dispatched",
            "handoff.result-ingested": payload.get("terminal_status", "terminal"),
            "handoff.cancelled": "cancelled",
            "handoff.retry-created": "superseded_by_retry",
            "handoff.superseded": "superseded",
            "handoff.reconciliation-required": "reconciliation_required",
            "handoff.reconciled": payload.get("disposition", "reconciled"),
        }
        record["status"] = status_map[kind]
        projected_keys = (
            "operation", "domain", "request_digest", "result_digest",
            "binding_identity", "idempotency_key", "retry_request_id",
            "superseded_by", "dispatch_correlation",
            "coordinator_disposition", "binding_key", "step",
            "request_intent_digest", "binding_skill_digest",
            "adapter_contract_digest", "gate", "subjects", "reason",
            "terminal_status", "governance_node", "prior_status",
            "producer_identity", "producer_principal_id",
            "producer_attestation_consumed_at",
        )
        if kind == "handoff.retry-created":
            # The retry payload describes the new S12.  Only its link belongs
            # on the immutable predecessor; all other anchors are projected on
            # the successor record below.
            record["retry_request_id"] = payload.get("retry_request_id")
        else:
            for key in projected_keys:
                if key not in payload:
                    continue
                if kind == "handoff.reconciliation-required" and key == "result_digest":
                    # A stale, conflicting, or late packet is untrusted.  Keep
                    # its digest for reconciliation without overwriting a
                    # previously accepted terminal S13 identity.
                    record["reconciliation_result_digest"] = payload[key]
                else:
                    record[key] = deepcopy(payload[key])
        if kind == "handoff.retry-created":
            retry_id = payload.get("retry_request_id")
            if not isinstance(retry_id, str) or retry_id in state["handoffs"]:
                raise ProjectionError("handoff_retry_invalid", "retry event must create one new request identity")
            state["handoffs"][retry_id] = {
                "request_id": retry_id,
                "status": "created",
                "retry_of": request_id,
                "operation": payload.get("operation"),
                "domain": payload.get("domain"),
                "binding_identity": payload.get("binding_identity"),
                "binding_skill_digest": payload.get("binding_skill_digest"),
                "adapter_contract_digest": payload.get("adapter_contract_digest"),
                "binding_key": payload.get("binding_key"),
                "gate": payload.get("gate"),
                "step": payload.get("step"),
                "subjects": deepcopy(payload.get("subjects")),
                "idempotency_key": payload.get("idempotency_key"),
                "request_digest": payload.get("request_digest"),
                "history": [{"event_type": kind, "event_hash": event["event_hash"]}],
            }
        if kind == "handoff.result-ingested" and payload.get("terminal_status") == "successful":
            operation = record.get("operation")
            state.setdefault("platform_results", {})[operation] = "successful"
        if kind == "handoff.reconciled" and payload.get("terminal_status") == "successful":
            operation = record.get("operation")
            state.setdefault("platform_results", {})[operation] = "successful"
        if kind == "handoff.created":
            _put_lineage_node(
                state, f"S12:{request_id}", "S12", event["event_hash"],
                request_id=request_id,
                request_digest=payload.get("request_digest"),
                subjects=deepcopy(payload.get("subjects", {})),
            )
        elif kind == "handoff.retry-created":
            retry_id = payload.get("retry_request_id")
            if isinstance(retry_id, str):
                _put_lineage_node(
                    state, f"S12:{retry_id}", "S12", event["event_hash"],
                    request_id=retry_id,
                    request_digest=payload.get("request_digest"),
                    subjects=deepcopy(payload.get("subjects", {})),
                )
                if f"S12:{request_id}" in state["lineage"]["nodes"]:
                    _append_projection_edge(
                        state["lineage"], f"S12:{request_id}",
                        f"S12:{retry_id}", "derived-from",
                    )
        if kind in {"handoff.result-ingested", "handoff.reconciled"}:
            result_digest = payload.get("result_digest")
            if isinstance(result_digest, str) and result_digest:
                receipt_node = f"S13:{request_id}"
                _put_lineage_node(
                    state, receipt_node, "S13", event["event_hash"],
                    request_id=request_id, result_digest=result_digest,
                    terminal_status=payload.get("terminal_status"),
                    subjects=deepcopy(record.get("subjects", {})),
                )
                request_node = f"S12:{request_id}"
                if request_node not in state["lineage"]["nodes"]:
                    raise ProjectionError(
                        "handoff_request_lineage_missing",
                        f"S13 {request_id} has no authenticated S12 predecessor",
                    )
                _append_projection_edge(
                    state["lineage"], request_node, receipt_node,
                    "derived-from",
                )
        if kind == "handoff.superseded":
            successor_id = payload.get("superseded_by")
            predecessor_node = f"S12:{request_id}"
            successor_node = f"S12:{successor_id}"
            if (
                not isinstance(successor_id, str)
                or predecessor_node not in state["lineage"]["nodes"]
            ):
                raise ProjectionError(
                    "handoff_supersession_lineage_missing",
                    "handoff supersession requires an authenticated predecessor S12 identity",
                )
            if successor_node not in state["lineage"]["nodes"]:
                # Handoff supersession may reserve the successor identity
                # before its immutable packet is created.  The authenticated
                # event is sufficient to project a reference node; a later
                # handoff.created event may consistently enrich it with the
                # request digest and exact subjects.
                _put_lineage_node(
                    state, successor_node, "S12", event["event_hash"],
                    request_id=successor_id,
                    status="referenced-successor",
                )
            _append_projection_edge(
                state["lineage"], predecessor_node, successor_node,
                "superseded-by",
            )
            _append_projection_edge(
                state["lineage"], successor_node, predecessor_node,
                "supersedes",
            )
    elif kind.endswith(".provisional") or kind.endswith("-provisional") or kind == "anchor.provisional":
        state["dimensions"]["anchor"] = "pending"
        state["pending_anchor"] = {"provisional_hash": event["event_hash"], "domain_event": kind, "domain_payload": deepcopy(payload)}
        state.setdefault("checks", {})[kind] = True
    elif kind == "anchor.accepted":
        pending = state.get("pending_anchor")
        if not pending or payload.get("provisional_hash") != pending.get("provisional_hash"):
            raise ProjectionError("anchor_mismatch", "accepted anchor does not match the pending provisional event")
        state["dimensions"]["anchor"] = "accepted"
        state["trusted_anchor"] = {"event_hash": payload.get("anchored_head"), "receipt_digest": payload.get("receipt_digest")}
        state.setdefault("checks", {}).update({
            "anchor.accepted": True,
            "current-trusted-anchor": True,
            "git.anchor-ledger-head.successful": True,
        })
        # Anchor acceptance authenticates a boundary but does not enact the
        # domain transition.  The explicit terminal event that follows
        # (experiment.frozen, decision.accepted, lifecycle.archived/closed) is the sole replayed
        # source of the terminal domain effect.
        state.pop("pending_anchor", None)
    elif kind in {"experiment.superseded", "decision.superseded"}:
        old_id = payload.get("old_id")
        new_id = payload.get("new_id")
        if not isinstance(old_id, str) or not isinstance(new_id, str) or old_id == new_id:
            raise ProjectionError("supersession_invalid", "supersession requires distinct reciprocal object IDs")
        expected_type = "E01" if kind == "experiment.superseded" else "D05"

        def materialized(identity: str) -> bool:
            artifact_type, separator, instance_key = identity.partition(":")
            return (
                separator == ":"
                and artifact_type == expected_type
                and bool(instance_key)
                and identity in state["lineage"]["nodes"]
                and instance_key in state.get("artifacts", {}).get(artifact_type, {})
                and state["lineage"]["nodes"][identity].get("type") == artifact_type
            )

        if not materialized(old_id):
            raise ProjectionError("superseded_subject_missing", "superseded identity must name a materialized governed artifact")
        if not materialized(new_id):
            raise ProjectionError("successor_missing", "successor identity must name a materialized governed artifact before reciprocal supersession")
        proposed_edges = {
            (old_id, new_id, "superseded-by"),
            (new_id, old_id, "supersedes"),
        }
        existing_edges = {
            (row.get("from"), row.get("to"), row.get("relation"))
            for row in state["lineage"]["edges"]
        }
        if proposed_edges & existing_edges:
            raise ProjectionError("supersession_invalid", "reciprocal supersession link already exists")
        state["lineage"]["edges"].extend(
            [
                {"from": old_id, "to": new_id, "relation": "superseded-by"},
                {"from": new_id, "to": old_id, "relation": "supersedes"},
            ]
        )
    elif kind == "substate.recorded":
        name = payload.get("name")
        value = payload.get("value", True)
        if not isinstance(name, str) or not name or not isinstance(value, bool):
            raise ProjectionError(
                "substate_invalid",
                "substate.recorded requires a non-empty name and boolean value",
            )
        state["substate_events"].append({
            "name": name, "value": value, "event_hash": event["event_hash"]
        })
        state.setdefault("checks", {})[name] = value
    if kind == "experiment.frozen":
        experiment_id = event.get("subjects", {}).get("experiment_id")
        if isinstance(experiment_id, str):
            record = state.get("artifacts", {}).get("E01", {}).get(
                f"{experiment_id}.frozen"
            )
            if isinstance(record, dict):
                record["status"] = "frozen"
        state["dimensions"]["protocol"] = "frozen"
    elif kind == "decision.accepted":
        outcome = payload.get("outcome")
        if outcome not in {
            "hypothesis_supported", "hypothesis_rejected",
            "inconclusive", "invalid",
        }:
            raise ProjectionError(
                "scientific_outcome_invalid",
                "decision.accepted requires one canonical scientific outcome",
            )
        state["dimensions"]["scientific"] = outcome
    elif kind == "lifecycle.cancelled":
        state["dimensions"]["lifecycle"] = "cancelled"
    elif kind == "lifecycle.archived":
        state["dimensions"]["lifecycle"] = "archived"
    elif kind == "lifecycle.closed":
        state["dimensions"]["lifecycle"] = "closed"
    state["revision"] = event["sequence"]
    state["ledger_head"] = event["event_hash"]
    state["active_engine_digest"] = event["engine_contract_digest"]


def materialize(replayed: ReplayResult) -> dict[str, Any]:
    state = initial_state(replayed)
    active = replayed.genesis["engine_contract_digest"]
    for event in replayed.events:
        bundle = replayed.bundles[active]
        apply_event(state, event, bundle)
        if event["event_type"] == "engine.migrated":
            active = event["payload"]["new_engine_contract_digest"]
    state["active_engine_digest"] = replayed.active_engine_digest
    state["revision"] = replayed.revision
    state["ledger_head"] = replayed.head_hash
    _synthesize_typed_lineage(state)
    state["traceability"] = build_traceability(state)
    findings = validate_graph(state["lineage"])
    for request_id, record in state.get("handoffs", {}).items():
        if record.get("status") == "configuration_required":
            binding_key = record.get("binding_key", "unknown-binding")
            findings.append(finding(
                "configuration_required",
                f"runtime binding is empty: {binding_key}",
                subject=request_id,
                governing_asset="platform-operations.json",
            ))
    state["findings"] = sort_findings(findings)
    bundle = replayed.bundles[replayed.active_engine_digest]
    schema_findings = validate_schema(state, bundle.document("schemas/state.schema.json"), subject="state", governing_asset="schemas/state.schema.json")
    if schema_findings:
        raise ProjectionError("projection_schema_invalid", schema_findings[0]["message"])
    return state


def build_traceability(state: dict[str, Any]) -> dict[str, Any]:
    nodes = [{"id": key, **value} for key, value in sorted(state["lineage"]["nodes"].items())]
    edges = sorted(state["lineage"]["edges"], key=lambda row: (row["from"], row["to"], row["relation"]))
    return {"schema": "sdm-traceability/v1", "nodes": nodes, "edges": edges, "derived_from_revision": state["revision"]}


def validate_graph(graph: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not isinstance(graph, dict):
        return [finding(
            "lineage_graph_shape_invalid",
            "traceability graph must be an object",
            subject="T01", governing_asset="artifact-contracts.json",
        )]
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, dict) or not isinstance(edges, list):
        return [finding(
            "lineage_graph_shape_invalid",
            "traceability graph requires object nodes and array edges",
            subject="T01", governing_asset="artifact-contracts.json",
        )]

    node_types: dict[str, str] = {}
    for node_id, node in nodes.items():
        node_type = node.get("type") if isinstance(node, dict) else None
        prefix, separator, instance = node_id.partition(":") \
            if isinstance(node_id, str) else ("", "", "")
        if (
            not isinstance(node_id, str) or not node_id
            or not isinstance(node, dict)
            or node_type not in _LINEAGE_NODE_TYPES
            or separator != ":" or prefix != node_type or not instance
        ):
            findings.append(finding(
                "lineage_node_invalid",
                "traceability node requires a governed type-consistent identity",
                subject=str(node_id), governing_asset="artifact-contracts.json",
            ))
            continue
        node_types[node_id] = node_type

    tuples: list[tuple[str, str, str]] = []
    for index, row in enumerate(edges):
        if (
            not isinstance(row, dict)
            or set(row) != {"from", "to", "relation"}
            or any(not isinstance(row.get(key), str) or not row.get(key)
                   for key in ("from", "to", "relation"))
        ):
            findings.append(finding(
                "lineage_edge_invalid",
                "traceability edge requires exact from/to/relation strings",
                subject=f"T01:edge:{index}",
                governing_asset="artifact-contracts.json",
            ))
            continue
        edge_tuple = (row["from"], row["to"], row["relation"])
        tuples.append(edge_tuple)
        if row["relation"] not in _LINEAGE_RELATIONS:
            findings.append(finding(
                "lineage_relation_invalid",
                f"traceability relation is not governed: {row['relation']}",
                subject="T01", governing_asset="artifact-contracts.json",
            ))

    if len(tuples) != len(set(tuples)):
        findings.append(finding(
            "lineage_duplicate_edge",
            "traceability graph contains a duplicate edge",
            subject="T01", governing_asset="artifact-contracts.json",
        ))
    reciprocal = {"superseded-by": "supersedes", "supersedes": "superseded-by"}
    edge_set = set(tuples)
    for source, target, relation in tuples:
        if source not in nodes or target not in nodes:
            findings.append(finding(
                "lineage_dangling_endpoint",
                f"traceability edge {relation} references an unmodeled endpoint",
                subject="T01", governing_asset="artifact-contracts.json",
            ))
            continue
        endpoint_contract = _TYPED_RELATION_ENDPOINTS.get(relation)
        if endpoint_contract is not None:
            allowed_sources, allowed_targets = endpoint_contract
            if (
                node_types.get(source) not in allowed_sources
                or node_types.get(target) not in allowed_targets
            ):
                findings.append(finding(
                    "lineage_typed_endpoint_invalid",
                    f"{relation} has the wrong endpoint type pair: "
                    f"{node_types.get(source)}->{node_types.get(target)}",
                    subject=f"{source}->{target}",
                    governing_asset="artifact-contracts.json",
                ))
        if relation in reciprocal and (
            node_types.get(source) != node_types.get(target)
            or node_types.get(source) not in {"E01", "D05", "S12"}
        ):
            findings.append(finding(
                "lineage_supersession_endpoint_invalid",
                "supersession must link two nodes of the same governed supersedable type",
                subject=f"{source}->{target}",
                governing_asset="artifact-contracts.json",
            ))
    for source, target, relation in tuples:
        inverse = reciprocal.get(relation)
        if inverse and (target, source, inverse) not in edge_set:
            findings.append(finding(
                "lineage_asymmetric", "reciprocal lineage edge is missing",
                subject="T01", governing_asset="artifact-contracts.json",
            ))

    incoming: dict[tuple[str, str], list[str]] = {}
    for source, target, relation in tuples:
        if source in nodes and target in nodes:
            incoming.setdefault((target, relation), []).append(source)

    def exact_incoming(
        node_id: str, relation: str, *,
        minimum: int, maximum: int | None,
        expected: set[str] | None = None,
    ) -> None:
        sources = incoming.get((node_id, relation), [])
        if len(sources) < minimum:
            findings.append(finding(
                "lineage_required_incoming_missing",
                f"{node_id} requires {relation} from its authenticated upstream endpoint",
                subject=node_id, governing_asset="artifact-contracts.json",
            ))
        if maximum is not None and len(sources) > maximum:
            findings.append(finding(
                "lineage_incoming_cardinality",
                f"{node_id} has {len(sources)} {relation} edges; maximum is {maximum}",
                subject=node_id, governing_asset="artifact-contracts.json",
            ))
        if expected is not None and set(sources) != expected:
            findings.append(finding(
                "lineage_incoming_identity_mismatch",
                f"{node_id} {relation} endpoints differ from replay-authenticated identities",
                subject=node_id, governing_asset="artifact-contracts.json",
            ))

    def authenticated_identities(
        node_id: str, field: str, *, cardinality: int,
    ) -> set[str]:
        node = nodes.get(node_id)
        rows = node.get(field) if isinstance(node, dict) else None
        if (
            not isinstance(rows, list)
            or len(rows) != cardinality
            or any(not isinstance(value, str) or not value for value in rows)
            or rows != sorted(set(rows))
        ):
            findings.append(finding(
                "lineage_node_invalid",
                f"{node_id} lacks its exact replay-derived {field}",
                subject=node_id, governing_asset="artifact-contracts.json",
            ))
            return set()
        return set(rows)

    # Only nodes that have appeared impose their downstream contract.  This
    # permits an early workspace to stop at any lifecycle boundary, while a
    # prematurely materialized downstream node fails closed immediately.
    for node_id, node_type in sorted(node_types.items()):
        node = nodes[node_id]
        if node_type == "S03":
            expected = authenticated_identities(
                node_id, "requirement_nodes", cardinality=1,
            )
            exact_incoming(
                node_id, "requirement-to-bmad", minimum=1, maximum=1,
                expected=expected,
            )
        elif node_type == "E01":
            expected = authenticated_identities(
                node_id, "bmad_nodes", cardinality=1,
            )
            exact_incoming(
                node_id, "bmad-to-experiment", minimum=1, maximum=1,
                expected=expected,
            )
        elif node_type == "Run":
            expected = authenticated_identities(
                node_id, "experiment_nodes", cardinality=1,
            )
            exact_incoming(
                node_id, "experiment-to-run", minimum=1, maximum=1,
                expected=expected,
            )
        elif node_type == "Attempt":
            run_id = node.get("run_id") if isinstance(node, dict) else None
            expected = {f"Run:{run_id}"} if isinstance(run_id, str) else set()
            exact_incoming(
                node_id, "run-to-attempt", minimum=1, maximum=1,
                expected=expected,
            )
        elif node_type == "S13" and node.get("trace_role") == "attempt-receipt":
            expected_rows = node.get("attempt_nodes")
            if (
                not isinstance(expected_rows, list) or not expected_rows
                or any(not isinstance(value, str) for value in expected_rows)
                or expected_rows != sorted(set(expected_rows))
            ):
                findings.append(finding(
                    "lineage_node_invalid",
                    "attempt receipt has invalid replay-derived Attempt identities",
                    subject=node_id, governing_asset="artifact-contracts.json",
                ))
                expected_rows = []
            exact_incoming(
                node_id, "attempt-to-receipt", minimum=1,
                maximum=len(expected_rows), expected=set(expected_rows),
            )
        elif node_type in {"D01", "D02", "D03", "D04"}:
            expected_rows = node.get("receipt_nodes", [])
            if (
                not isinstance(expected_rows, list)
                or any(not isinstance(value, str) for value in expected_rows)
                or expected_rows != sorted(set(expected_rows))
            ):
                findings.append(finding(
                    "lineage_node_invalid",
                    "evidence node has invalid replay-derived receipt identities",
                    subject=node_id, governing_asset="artifact-contracts.json",
                ))
                expected_rows = []
            exact_incoming(
                node_id, "receipt-to-evidence", minimum=1,
                maximum=len(expected_rows), expected=set(expected_rows),
            )
        elif node_type == "D05":
            sources = incoming.get((node_id, "evidence-to-decision"), [])
            expected = authenticated_identities(
                node_id, "evidence_nodes", cardinality=4,
            )
            exact_incoming(
                node_id, "evidence-to-decision", minimum=4, maximum=4,
                expected=expected,
            )
            source_types = {node_types.get(source) for source in sources}
            instances = {
                source.split(":", 1)[1] for source in sources
                if ":" in source
            }
            if (
                source_types != {"D01", "D02", "D03", "D04"}
                or len(instances) != 1
            ):
                findings.append(finding(
                    "lineage_incoming_identity_mismatch",
                    "D05 requires one common D01-D04 evidence bundle identity",
                    subject=node_id, governing_asset="artifact-contracts.json",
                ))

    adjacency: dict[str, list[str]] = {}
    for source, target, relation in tuples:
        if (
            source in nodes and target in nodes
            and relation not in {"supersedes", "superseded-by"}
        ):
            adjacency.setdefault(source, []).append(target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for child in adjacency.get(node, []):
            if visit(child):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    if any(visit(node) for node in list(adjacency)):
        findings.append(finding(
            "lineage_cycle", "traceability derivation graph contains a cycle",
            subject="T01", governing_asset="artifact-contracts.json",
        ))
    return findings


def stored_projection(
    workspace: str | Path,
    *,
    capability: ReadCapability | None = None,
) -> dict[str, Any]:
    if capability is None:
        try:
            with ReadCapability(workspace) as reader:
                return stored_projection(workspace, capability=reader)
        except (OSError, ValueError, JournalError) as exc:
            raise _stored_projection_error(exc) from exc
    try:
        capability.assert_root(workspace)
        value = parse_document(capability.read_bytes("state.json"))
    except (OSError, ValueError, JournalError) as exc:
        raise _stored_projection_error(exc) from exc
    if not isinstance(value, dict):
        raise ProjectionError("stored_projection_invalid", "stored projection is not an object")
    return value


def verify_stored_projection(
    workspace: str | Path,
    replayed: ReplayResult,
    *,
    capability: ReadCapability | None = None,
) -> dict[str, Any]:
    computed = materialize(replayed)
    stored = stored_projection(workspace, capability=capability)
    if canonical_bytes(computed) != canonical_bytes(stored):
        raise ProjectionError("projection_mismatch", "stored projection contradicts full ledger replay")
    return computed


@dataclass(frozen=True)
class Action:
    step: int
    subject_type: str
    instance_key: str
    disposition: str
    owner: str
    missing: tuple[str, ...]
    platform_operations: tuple[str, ...]
    request_id: str | None = None
    operation: str | None = None
    binding: str | None = None
    binding_key: str | None = None
    dispatch_correlation: str | None = None

    def as_dict(self) -> dict[str, Any]:
        value = {
            "step": self.step,
            "subject_type": self.subject_type,
            "instance_key": self.instance_key,
            "disposition": self.disposition,
            "owner": self.owner,
            "missing": list(self.missing),
            "platform_operations": list(self.platform_operations),
        }
        if self.subject_type == "handoff" or self.request_id is not None:
            value.update({
                "request_id": self.request_id,
                "operation": self.operation,
                "binding": self.binding,
                "binding_key": self.binding_key,
                "dispatch_correlation": self.dispatch_correlation,
            })
        return value


def _handoff_step(record: dict[str, Any], state: dict[str, Any]) -> int:
    """Return an exact persisted S12 step, with a legacy-history fallback."""

    step = record.get("step")
    if isinstance(step, int) and not isinstance(step, bool) and step > 0:
        return step
    lifecycle_step = state.get("lifecycle_step", 1)
    if isinstance(lifecycle_step, int) and not isinstance(lifecycle_step, bool) and lifecycle_step > 0:
        return lifecycle_step
    return 1


def _handoff_actions(
    state: dict[str, Any], bindings: dict[str, str] | None,
) -> list[Action]:
    """Project durable handoff records into deterministic public Resume actions.

    Open handoffs are resolved before lifecycle rows so a blocked row can never
    hide an immutable S12 awaiting dispatch, a producer result, a governed
    retry/supersession choice, or exact-correlation reconciliation.  Terminal
    success and superseded predecessor records do not block; any live successor
    is represented by its own record and therefore its own action.
    """

    actions: list[Action] = []
    for request_id, record in state.get("handoffs", {}).items():
        if not isinstance(request_id, str) or not isinstance(record, dict):
            continue
        status = record.get("status")
        if status in {"successful", "superseded", "superseded_by_retry"}:
            continue
        if status == "cancelled" and isinstance(
            record.get("governance_node"), str,
        ):
            # A cancellation appended atomically with S16/Experiment
            # supersession is a terminal governed child disposition.  A
            # standalone handoff.cancelled remains retry/supersede-able.
            continue
        operation_value = record.get("operation")
        operation = operation_value if isinstance(operation_value, str) else "unknown-operation"
        binding_key_value = record.get("binding_key")
        binding_key = binding_key_value if isinstance(binding_key_value, str) else None
        binding_value = record.get("binding_identity")
        if not isinstance(binding_value, str) or not binding_value:
            configured = bindings.get(binding_key) if bindings and binding_key else None
            binding_value = configured if isinstance(configured, str) and configured else None
        correlation_value = record.get("dispatch_correlation")
        correlation = correlation_value if isinstance(correlation_value, str) and correlation_value else None

        disposition: str | None
        owner = "sdm-lifecycle"
        missing: tuple[str, ...]
        if status == "configuration_required":
            disposition = "configuration_required"
            missing = (f"binding:{binding_key or 'unknown-binding'}",)
        elif status == "created":
            disposition = "handoff"
            owner = binding_value or owner
            missing = (f"platform_result:{operation}",)
        elif status == "dispatched":
            disposition = "wait_for_result"
            owner = binding_value or owner
            missing = (f"platform_result:{operation}",)
        elif status in {"failed", "cancelled", "reconciled_no_effect", "reconciled_effect"}:
            if status == "reconciled_effect" and state.get("platform_results", {}).get(operation) == "successful":
                continue
            disposition = "retry_or_supersede"
            missing = (f"platform_result:{operation}",)
        elif status == "reconciliation_required":
            disposition = "reconcile"
            required = [f"platform_result:{operation}"]
            if correlation is None:
                required.append("dispatch_correlation")
            missing = tuple(required)
        else:
            # Unknown statuses fail closed as lifecycle blocks rather than being
            # silently interpreted as success or made externally actionable.
            disposition = "blocked"
            missing = (f"handoff_status:{status or 'missing'}",)

        actions.append(Action(
            _handoff_step(record, state),
            "handoff",
            request_id,
            disposition,
            owner,
            missing,
            (operation,),
            request_id=request_id,
            operation=operation,
            binding=binding_value,
            binding_key=binding_key,
            dispatch_correlation=correlation,
        ))
    return sorted(actions, key=lambda action: (action.step, action.subject_type, action.instance_key))


def _exact_step_platform_results(
    state: dict[str, Any], row: dict[str, Any], operations: tuple[str, ...],
) -> dict[str, str]:
    """Return only S13 successes bound to this exact row and subject set.

    The legacy operation-name summary is diagnostic only.  It cannot authorize
    a later row (or another Experiment/Run) that happens to reuse an operation
    name.
    """

    step = row.get("step")
    gate = f"lifecycle-step-{step}"
    grouped: dict[str, set[str]] = {}
    for record in state.get("handoffs", {}).values():
        if not isinstance(record, dict):
            continue
        if (
            record.get("status") not in {"successful", "reconciled_effect"}
            or record.get("step") != step
            or record.get("gate") != gate
            or record.get("operation") not in operations
            or not isinstance(record.get("request_digest"), str)
            or not isinstance(record.get("result_digest"), str)
            or not isinstance(record.get("subjects"), dict)
        ):
            continue
        subject_key = digest(record["subjects"])
        grouped.setdefault(subject_key, set()).add(record["operation"])
    complete = [key for key, values in grouped.items() if values == set(operations)]
    if not complete:
        return {}
    # Multiple independently complete subjects are alternatives; choosing the
    # canonical subject digest keeps Resume deterministic without combining
    # receipts across subjects.
    selected = min(complete)
    return {name: "successful" for name in grouped[selected]}


def admissible_actions(state: dict[str, Any], bundle: AssetBundle, bindings: dict[str, str] | None = None) -> list[Action]:
    pending_handoffs = _handoff_actions(state, bindings)
    if pending_handoffs:
        return pending_handoffs
    if state.get("dimensions", {}).get("lifecycle") == "cancelled":
        return [Action(
            state.get("lifecycle_step", 1), "lifecycle", "archive",
            "archive", "sdm-lifecycle", (), (),
        )]
    lifecycle = state.get("dimensions", {}).get("lifecycle")
    if lifecycle == "closed":
        return []
    if lifecycle == "archived":
        return [Action(state.get("lifecycle_step", 22), "lifecycle", "close", "close", "sdm-lifecycle", (), ())]
    if state.get("lifecycle_step") in {21, 22}:
        actions = [Action(state["lifecycle_step"], "lifecycle", "archive", "archive", "sdm-lifecycle", (), ())]
        if state["lifecycle_step"] == 21:
            actions.insert(0, Action(21, "experiment", "successor", "supersede", "sdm-create-experiment-spec", (), ()))
        return actions
    context = deepcopy(state)
    context["checks"] = state.get("checks", {})
    actions: list[Action] = []
    for row in _rows(bundle.document("lifecycle-steps.json"), "steps"):
        step = row["step"]
        if step < state.get("lifecycle_step", 1):
            continue
        operations = tuple(item["name"] if isinstance(item, dict) else item for item in row.get("platform_operations", []))
        exact_results = _exact_step_platform_results(state, row, operations)
        context["platform_results"] = exact_results
        passed, missing, _ = evaluate_gate(row["gate"], context)
        missing_bindings: list[str] = []
        for operation in operations:
            _, _, binding_key = bundle.operation_contract(operation)
            if not bindings or not bindings.get(binding_key):
                missing_bindings.append(binding_key)
        if missing_bindings:
            disposition = "configuration_required"
            missing.extend(f"binding:{key}" for key in missing_bindings)
        elif operations and any(exact_results.get(name) != "successful" for name in operations):
            disposition = "handoff"
        elif passed:
            disposition = "advance"
        elif row.get("human_authority"):
            disposition = "human_wait"
        else:
            disposition = "blocked"
        owner = row.get("owning_skill", "sdm-lifecycle")
        if isinstance(owner, dict):
            owner = owner.get("skill", owner.get("actor", "lifecycle"))
        actions.append(Action(step, row.get("subject_type", "workspace"), row.get("instance_key", "default"), disposition, str(owner), tuple(sorted(set(missing))), operations))
        # Later steps may be alternatives for independent subjects, but the first
        # workspace-scoped blocked row prevents a false linear advance.
        if row.get("subject_type", "workspace") == "workspace":
            break
    return sorted(actions, key=lambda action: (action.step, action.subject_type, action.instance_key))


def _diagnostic_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect the SDM deterministic-projection library")
    parser.parse_args(argv)
    sys.stdout.write(json.dumps({"status": "pass", "command": "projection.help"}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(_diagnostic_main())
