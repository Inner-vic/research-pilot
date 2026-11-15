from __future__ import annotations

from copy import deepcopy

from canonical import digest
from ledger import build_event, replay, replay_with
from projection import build_traceability, materialize, validate_graph


TYPED_RELATIONS = {
    "requirement-to-bmad", "bmad-to-experiment", "experiment-to-run",
    "run-to-attempt", "attempt-to-receipt", "receipt-to-evidence",
    "evidence-to-decision",
}


def _append(base, event_type, payload, operation_id, *, subjects=None):
    subjects = subjects or {"workspace_id": base.genesis["workspace_id"]}
    event = build_event(
        sequence=base.revision + 1,
        event_type=event_type,
        previous_hash=base.head_hash,
        engine_contract_digest=base.active_engine_digest,
        actor={"type": "coordinator", "id": "sdm-lifecycle"},
        subjects=subjects,
        payload=payload,
        operation_id=operation_id,
        occurred_at="2026-01-02T03:04:05Z",
    )
    return replay_with((event,), base)


def _publish_event(base, records, operation_id="publish-lineage"):
    return build_event(
        sequence=base.revision + 1,
        event_type="artifact.published",
        previous_hash=base.head_hash,
        engine_contract_digest=base.active_engine_digest,
        actor={"type": "coordinator", "id": "sdm-lifecycle"},
        subjects={"workspace_id": base.genesis["workspace_id"]},
        payload={"published_artifacts": records},
        operation_id=operation_id,
        occurred_at="2026-01-02T03:04:05Z",
    )


def _record(artifact_id, instance_key, parents=()):
    node = f"{artifact_id}:{instance_key}"
    return {
        "artifact_id": artifact_id,
        "instance_key": instance_key,
        "path": f"artifacts/{artifact_id}/{instance_key}.json",
        "digest": digest({"node": node}),
        "parents": list(parents),
        "status": "published",
    }


def _full_traceability_state(workspace):
    base = replay(workspace)
    current = _append(
        base, "artifact.published", {
            "published_artifacts": [
                _record("S01", "requirement"),
                _record("S03", "bmad", ["S01:requirement"]),
                _record("E01", "experiment", ["S03:bmad"]),
                _record("R01", "run", ["E01:experiment"]),
            ],
        }, "publish-foundation",
    )
    execution_subjects = {
        "workspace_id": base.genesis["workspace_id"],
        "experiment_id": "experiment", "run_id": "run",
        "attempt_id": "attempt",
    }
    current = _append(
        current, "handoff.created", {
            "request_id": "REQ-evaluation", "operation": "ml.locked-evaluation",
            "domain": "ml-execution-platform", "request_digest": digest({"request": 1}),
            "subjects": {key: value for key, value in execution_subjects.items()
                         if key != "workspace_id"},
        }, "create-evaluation-request",
        subjects={"workspace_id": base.genesis["workspace_id"],
                  "request_id": "REQ-evaluation"},
    )
    current = _append(
        current, "handoff.result-ingested", {
            "request_id": "REQ-evaluation", "terminal_status": "successful",
            "result_digest": digest({"result": 1}),
            "producer_principal_id": "platform-principal",
        }, "ingest-evaluation-result",
        subjects={"workspace_id": base.genesis["workspace_id"],
                  "request_id": "REQ-evaluation"},
    )
    current = _append(
        current, "run.dispatched", {
            "request_ids": ["REQ-evaluation"],
            "result_digests": [digest({"result": 1})],
        }, "dispatch-attempt", subjects=execution_subjects,
    )
    current = _append(
        current, "attempt.terminal", {
            "terminal_status": "successful", "accounted": True,
            "validity": "valid", "system_failure": False,
            "request_ids": ["REQ-evaluation"],
            "published_artifacts": [
                _record("R07", "attempt", ["R01:run"]),
                _record("R08", "attempt", ["R01:run", "R07:attempt"]),
            ],
        }, "complete-attempt", subjects=execution_subjects,
    )
    current = _append(
        current, "evidence.built", {
            "published_artifacts": [
                _record(kind, "evidence", ["R07:attempt", "R08:attempt"])
                for kind in ("D01", "D02", "D03", "D04")
            ],
        }, "build-evidence",
    )
    evidence_nodes = [f"{kind}:evidence" for kind in ("D01", "D02", "D03", "D04")]
    current = _append(
        current, "artifact.published", {
            "published_artifacts": [_record("D05", "decision", evidence_nodes)],
        }, "publish-decision",
    )
    return materialize(current)


def test_full_T01_reconstruction(workspace) -> None:
    state = _full_traceability_state(workspace)
    traceability = state["traceability"]
    assert traceability == build_traceability(state)
    assert traceability["derived_from_revision"] == replay(workspace).revision + 7
    node_ids = {node["id"] for node in traceability["nodes"]}
    assert {
        "S12:REQ-evaluation", "S13:REQ-evaluation", "Run:run",
        "Attempt:attempt", "R01:run", "R07:attempt", "D01:evidence",
        "D05:decision",
    }.issubset(node_ids)
    edges = {(edge["from"], edge["to"], edge["relation"]) for edge in traceability["edges"]}
    assert {relation for _, _, relation in edges if relation in TYPED_RELATIONS} == TYPED_RELATIONS
    assert ("S12:REQ-evaluation", "S13:REQ-evaluation", "derived-from") in edges
    assert ("R01:run", "Run:run", "derived-from") in edges
    assert ("Attempt:attempt", "R07:attempt", "derived-from") in edges
    assert validate_graph(state["lineage"]) == []

    bundle = replay(workspace).bundles[replay(workspace).active_engine_digest]
    traceability_envelope = {
        "schema": "sdm-artifact/v1", "artifact_type": "T01",
        "artifact_id": "traceability-export", "instance_key": f"revision-{traceability['derived_from_revision']}",
        "version": 1, "status": "published",
        "subjects": {"workspace_id": replay(workspace).genesis["workspace_id"]},
        "content": traceability, "content_digest": digest(traceability),
        "created_at": "2026-01-02T03:04:05Z",
        "created_by": "sdm-lifecycle",
    }
    assert bundle.validate_artifact("T01", traceability_envelope) == []
    tampered_export = deepcopy(traceability_envelope)
    tampered_export["content"]["edges"] = [
        row for row in tampered_export["content"]["edges"]
        if row["relation"] != "requirement-to-bmad"
    ]
    tampered_export["content_digest"] = digest(tampered_export["content"])
    assert "lineage_required_incoming_missing" in {
        item["code"]
        for item in bundle.validate_artifact("T01", tampered_export)
    }

    # An early workspace requires only the upstream edge for a downstream
    # node that already exists; it does not require future lifecycle nodes.
    root_only = {
        "nodes": {"S01:req": {"type": "S01"}}, "edges": [],
    }
    assert validate_graph(root_only) == []
    bmad_complete = deepcopy(root_only)
    bmad_complete["nodes"]["S03:plan"] = {
        "type": "S03", "requirement_nodes": ["S01:req"],
    }
    bmad_complete["edges"].append({
        "from": "S01:req", "to": "S03:plan",
        "relation": "requirement-to-bmad",
    })
    assert validate_graph(bmad_complete) == []
    bmad_missing = deepcopy(bmad_complete)
    bmad_missing["edges"] = []
    assert "lineage_required_incoming_missing" in {
        item["code"] for item in validate_graph(bmad_missing)
    }
    bmad_substituted = deepcopy(bmad_complete)
    bmad_substituted["nodes"]["S01:attacker"] = {"type": "S01"}
    bmad_substituted["edges"][0]["from"] = "S01:attacker"
    assert "lineage_incoming_identity_mismatch" in {
        item["code"] for item in validate_graph(bmad_substituted)
    }

    pristine = state["lineage"]
    for relation in sorted(TYPED_RELATIONS):
        attacked = deepcopy(pristine)
        victim = next(
            edge for edge in attacked["edges"] if edge["relation"] == relation
        )
        attacked["edges"].remove(victim)
        assert "lineage_required_incoming_missing" in {
            item["code"] for item in validate_graph(attacked)
        }, relation

    wrong = deepcopy(pristine)
    victim = next(
        edge for edge in wrong["edges"]
        if edge["relation"] == "attempt-to-receipt"
    )
    victim["relation"] = "evidence-to-decision"
    assert "lineage_typed_endpoint_invalid" in {
        item["code"] for item in validate_graph(wrong)
    }

    swapped = deepcopy(pristine)
    swapped["nodes"]["Attempt:attacker"] = {
        "type": "Attempt", "run_id": "run", "attempt_id": "attacker",
        "receipt_request_ids": [],
    }
    swapped["edges"].append({
        "from": "Run:run", "to": "Attempt:attacker",
        "relation": "run-to-attempt",
    })
    receipt_edge = next(
        edge for edge in swapped["edges"]
        if edge["relation"] == "attempt-to-receipt"
    )
    receipt_edge["from"] = "Attempt:attacker"
    assert "lineage_incoming_identity_mismatch" in {
        item["code"] for item in validate_graph(swapped)
    }

    excess = deepcopy(pristine)
    excess["nodes"]["Attempt:attacker"] = swapped["nodes"]["Attempt:attacker"]
    excess["edges"].extend([
        {"from": "Run:run", "to": "Attempt:attacker", "relation": "run-to-attempt"},
        {"from": "Attempt:attacker", "to": "S13:REQ-evaluation", "relation": "attempt-to-receipt"},
    ])
    assert {"lineage_incoming_cardinality", "lineage_incoming_identity_mismatch"}.issubset({
        item["code"] for item in validate_graph(excess)
    })


def test_successor_closure_lineage(workspace) -> None:
    base = replay(workspace)
    published = _publish_event(
        base,
        [
            _record("S01", "requirement"),
            _record("S03", "bmad", ["S01:requirement"]),
            _record("E01", "old", ["S03:bmad"]),
            _record("E01", "new", ["E01:old"]),
        ],
        "publish-successors",
    )
    after_publish = replay_with((published,), base)
    superseded = build_event(
        sequence=after_publish.revision + 1,
        event_type="experiment.superseded",
        previous_hash=after_publish.head_hash,
        engine_contract_digest=after_publish.active_engine_digest,
        actor={"type": "coordinator", "id": "sdm-lifecycle"},
        subjects={"workspace_id": base.genesis["workspace_id"]},
        payload={"old_id": "E01:old", "new_id": "E01:new"},
        operation_id="supersede-experiment",
        occurred_at="2026-01-02T03:05:05Z",
    )
    state = materialize(replay_with((superseded,), after_publish))
    edges = state["lineage"]["edges"]
    assert {("E01:old", "E01:new", "superseded-by"), ("E01:new", "E01:old", "supersedes")} == {
        (edge["from"], edge["to"], edge["relation"]) for edge in edges
        if edge["relation"] in {"superseded-by", "supersedes"}
    }
    assert ("S03:bmad", "E01:new", "bmad-to-experiment") in {
        (edge["from"], edge["to"], edge["relation"]) for edge in edges
    }
    assert validate_graph(state["lineage"]) == []

    missing_inverse = deepcopy(state["lineage"])
    missing_inverse["edges"] = [
        edge for edge in missing_inverse["edges"]
        if edge != {"from": "E01:new", "to": "E01:old", "relation": "supersedes"}
    ]
    assert any(item["code"] == "lineage_asymmetric" for item in validate_graph(missing_inverse))
    dangling = deepcopy(state["lineage"])
    dangling["edges"].append(
        {"from": "E01:missing", "to": "E01:new", "relation": "derived-from"},
    )
    assert any(item["code"] == "lineage_dangling_endpoint" for item in validate_graph(dangling))


def test_graph_rejects_duplicate_and_derivation_cycle() -> None:
    edge = {"from": "A", "to": "B", "relation": "derived-from"}
    graph = {
        "nodes": {"A": {"type": "E01"}, "B": {"type": "R01"}},
        "edges": [edge, dict(edge), {"from": "B", "to": "A", "relation": "derived-from"}],
    }
    codes = {item["code"] for item in validate_graph(graph)}
    assert {"lineage_duplicate_edge", "lineage_cycle"}.issubset(codes)

    malformed = {
        "nodes": {
            "S12:old": {"type": "S12"},
            "S12:new": {"type": "S12"},
        },
        "edges": [
            {"from": "S12:old", "to": "S12:new", "relation": "superseded-by"},
            {"from": "S12:old", "to": "S12:new", "relation": "invented"},
            {"from": "S12:old", "to": "S12:new"},
        ],
    }
    assert {
        "lineage_asymmetric", "lineage_relation_invalid", "lineage_edge_invalid",
    }.issubset({item["code"] for item in validate_graph(malformed)})
