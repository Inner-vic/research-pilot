from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

import specctl
from canonical import candidate_digest, digest
from contracts import evaluate_gate
from ledger import replay
from projection import materialize
from workspace import WorkspaceError, mutate, publish_artifact


def _seed_planning_lineage(workspace: Path) -> None:
    current = replay(workspace)
    workspace_id = current.genesis["workspace_id"]
    requirement_content = {
        "requirement_id": "requirement",
        "uri": "jira://project/requirement-v1",
        "owner": "product-owner",
        "impact": "define the governed experiment objective",
        "non_goals": ["perform model training"],
    }
    requirement = {
        "schema": "sdm-artifact/v1",
        "artifact_type": "S01",
        "artifact_id": "s01-requirement",
        "instance_key": "requirement",
        "version": 1,
        "status": "published",
        "subjects": {"workspace_id": workspace_id},
        "content": requirement_content,
        "content_digest": digest(requirement_content),
        "uri": "artifact://S01/requirement",
        "created_at": "2026-08-25T00:00:00Z",
        "created_by": "product-owner",
    }
    published = publish_artifact(
        workspace,
        artifact_id="S01",
        instance_key="requirement",
        value=requirement,
        expected_revision=current.revision,
        operation_id="seed-requirement-lineage",
    )
    planning_content = {
        field: {
            "kind": kind,
            "uri": f"bmad://{kind}/{kind}-v1",
            "digest": digest({"fixture": kind}),
            "revision": "rev-1",
        }
        for field, kind in (
            ("product_brief", "product-brief"),
            ("prd", "prd"),
            ("architecture", "architecture"),
        )
    }
    planning_content["source_refs"] = {
        "S01:requirement": digest(requirement),
    }
    planning = {
        "schema": "sdm-artifact/v1",
        "artifact_type": "S03",
        "artifact_id": "s03-planning",
        "instance_key": "planning",
        "version": 1,
        "status": "published",
        "subjects": {"workspace_id": workspace_id},
        "content": planning_content,
        "content_digest": digest(planning_content),
        "uri": "artifact://S03/planning",
        "created_at": "2026-08-25T00:00:00Z",
        "created_by": "bmad-planner",
    }
    publish_artifact(
        workspace,
        artifact_id="S03",
        instance_key="planning",
        value=planning,
        parents=["S01:requirement"],
        expected_revision=published.revision,
        operation_id="seed-planning-lineage",
    )


def _provisionally_freeze(workspace: Path) -> tuple[dict, object]:
    _seed_planning_lineage(workspace)
    base = replay(workspace)
    evaluator_digest = digest({"contract": "evaluator-v1"})
    baseline_digest = digest({"baseline": "baseline-v1"})
    dataset_manifest_digest = digest({"dataset": "dataset-v1"})
    split_manifest_digest = digest({"split": "split-v1"})
    variables = {"model": {"width": 8}}
    content = {
        "experiment_id": "experiment",
        "protocol_channel": "formal",
        "promotable": True,
        "hypothesis": "frozen hypothesis remains falsifiable",
        "variables": variables,
        "change_only": {
            "allowed_code_paths": ["src/models/**"],
            "allowed_config_paths": ["/optimizer/lr"],
            "required_equal_refs": [{
                "ref": "G10:evaluator-v1", "digest": evaluator_digest,
            }],
            "required_diff_paths": ["code:src/models/model.py"],
            "automatically_derived_paths": [],
        },
        "frozen_variables": [{
            "path": "/variables/model/width",
            "digest": digest(variables["model"]["width"]),
            "reason": "model width is frozen by the protocol",
        }],
        "forbidden_changes": ["code:src/labels/**"],
        "contracts": {"source_digests": {
            "G10:evaluator-v1": evaluator_digest,
        }},
        "baseline": {
            "lock_ref": "B02:baseline-v1",
            "lock_digest": baseline_digest,
            "baseline_digest": digest({"baseline-spec": "baseline-v1"}),
        },
        "data": {
            "dataset_manifest_ref": "R04:dataset-v1",
            "dataset_manifest_digest": dataset_manifest_digest,
            "dataset_digest": digest({"dataset-content": "dataset-v1"}),
            "split_manifest_ref": "R05:split-v1",
            "split_manifest_digest": split_manifest_digest,
            "split_digest": digest({"split-membership": "split-v1"}),
            "calibration_applicable": False,
        },
        "evaluator": {
            "contract_ref": "G10:evaluator-v1",
            "contract_digest": evaluator_digest,
            "evaluator_identity": "evaluator-v1",
        },
        "budget": {"attempts": 1},
        "decision_rule": {
            "schema": "sdm-scientific-rule/v1",
            "support_predicate": "supports_hypothesis",
            "rejection_predicate": "rejects_hypothesis",
            "inconclusive_rule": "underpowered_or_no_decisive_result",
            "invalid_rule": "protocol_invalid",
        },
        "author_principal_ids": ["research-author"],
        "source_refs": {
            "B02:baseline-v1": baseline_digest,
            "G10:evaluator-v1": evaluator_digest,
            "R04:dataset-v1": dataset_manifest_digest,
            "R05:split-v1": split_manifest_digest,
        },
    }
    frozen = {
        "schema": "sdm-artifact/v1",
        "artifact_type": "E01",
        "artifact_id": "e01-experiment-frozen",
        "instance_key": "experiment.frozen",
        "version": 1,
        "status": "frozen",
        "subjects": {
            "workspace_id": base.genesis["workspace_id"],
            "experiment_id": "experiment",
        },
        "content": content,
        "content_digest": digest(content),
        "uri": "artifact://E01/experiment.frozen",
        "created_at": "2026-08-25T00:00:00Z",
        "created_by": "research-author",
    }
    record = {
        "artifact_id": "E01",
        "instance_key": "experiment.frozen",
        "path": "artifacts/E01/experiment.frozen.json",
        "digest": digest(frozen),
        "parents": ["S03:planning"],
        "status": "provisional",
    }
    mutation = mutate(
        workspace,
        expected_revision=base.revision,
        operation_id="freeze-provisional",
        event_type="protocol.freeze-provisional",
        subjects={"workspace_id": base.genesis["workspace_id"], "experiment_id": "experiment"},
        payload={
            "experiment_id": "experiment",
            "candidate_digest": candidate_digest(frozen["content"]),
            "published_artifacts": [record],
        },
        artifact_writes=((record["path"], frozen, True),),
    )
    return frozen, mutation


def _accept_anchor(workspace: Path, provisional) -> object:
    accepted = mutate(
        workspace,
        expected_revision=provisional.revision,
        operation_id="accept-freeze-anchor",
        event_type="anchor.accepted",
        subjects={"workspace_id": provisional.state["workspace_id"]},
        payload={
            "provisional_hash": provisional.event["event_hash"],
            "anchored_head": provisional.event["event_hash"],
            "receipt_digest": digest({"anchor": provisional.event["event_hash"]}),
        },
    )
    return mutate(
        workspace,
        expected_revision=accepted.revision,
        operation_id="finish-freeze-after-anchor",
        event_type="experiment.frozen",
        subjects={"workspace_id": provisional.state["workspace_id"], "experiment_id": "experiment"},
        payload={
            "provisional_hash": provisional.event["event_hash"],
            "anchor_accepted_event": accepted.event["event_hash"],
        },
    )


def _check_diff(capsys, workspace: Path, tmp_path: Path, name: str, candidate: dict) -> dict:
    path = tmp_path / f"{name}.json"
    frozen = json.loads(
        (workspace / "artifacts/E01/experiment.frozen.json").read_text(
            encoding="utf-8",
        )
    )
    supplied = {
        "candidate": candidate,
        "git_tree_before": {
            "src/models/model.py": digest({"source": "before"}),
        },
        "git_tree_after": {
            "src/models/model.py": digest({"source": "after"}),
        },
        "resolved_config_before": frozen["content"]["variables"],
        "resolved_config_after": candidate["content"]["variables"],
    }
    path.write_text(json.dumps(supplied), encoding="utf-8")
    code = specctl.main(["check-diff", "--workspace", str(workspace), "--experiment-id", "experiment", "--candidate", str(path)])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["status"] == "pass"
    return output["result"]


def _candidate(frozen: dict, **content_changes: object) -> dict:
    candidate = deepcopy(frozen)
    candidate["content"].update(content_changes)
    candidate["content_digest"] = digest(candidate["content"])
    return candidate


def test_formal_freeze_and_change(workspace) -> None:
    frozen, provisional = _provisionally_freeze(workspace)
    provisional_state = materialize(replay(workspace))
    assert provisional_state["dimensions"]["anchor"] == "pending"
    assert provisional_state["dimensions"]["protocol"] != "frozen"
    assert provisional_state["pending_anchor"]["provisional_hash"] == provisional.event["event_hash"]

    accepted = _accept_anchor(workspace, provisional)
    assert accepted.state["dimensions"]["anchor"] == "accepted"
    assert accepted.state["dimensions"]["protocol"] == "frozen"
    assert accepted.state["trusted_anchor"]["event_hash"] == provisional.event["event_hash"]
    assert json.loads((workspace / "artifacts/E01/experiment.frozen.json").read_text(encoding="utf-8")) == frozen


def test_frozen_change_classification(workspace, tmp_path, capsys) -> None:
    frozen, provisional = _provisionally_freeze(workspace)
    _accept_anchor(workspace, provisional)
    frozen_path = workspace / "artifacts/E01/experiment.frozen.json"
    frozen_bytes = frozen_path.read_bytes()
    assert _check_diff(capsys, workspace, tmp_path, "same", dict(frozen))["classification"] == "unchanged"
    assert _check_diff(
        capsys,
        workspace,
        tmp_path,
        "pre-run",
        _candidate(frozen, budget={"attempts": 2}),
    )["classification"] == "E06"
    assert frozen_path.read_bytes() == frozen_bytes

    base = replay(workspace)
    mutate(
        workspace,
        expected_revision=base.revision,
        operation_id="first-run",
        event_type="run.materialized",
        subjects={"workspace_id": base.genesis["workspace_id"], "experiment_id": "experiment", "run_id": "run-1", "attempt_id": "attempt-1"},
        payload={"run_id": "run-1", "attempt_id": "attempt-1", "published_artifacts": []},
    )
    material = _check_diff(
        capsys,
        workspace,
        tmp_path,
        "material",
        _candidate(frozen, hypothesis="rewritten after results"),
    )
    mixed = _check_diff(
        capsys, workspace, tmp_path, "mixed",
        _candidate(
            frozen,
            hypothesis="rewritten after results",
            author_principal_ids=["research-author", "review-observer"],
        ),
    )
    observational = _check_diff(
        capsys,
        workspace,
        tmp_path,
        "observational",
        _candidate(
            frozen,
            author_principal_ids=["research-author", "review-observer"],
        ),
    )
    assert material["classification"] == "successor-required"
    assert mixed["classification"] == "successor-required"
    assert observational["classification"] == "E07"
    assert frozen_path.read_bytes() == frozen_bytes


def test_append_only_change_families_materialize_real_records_and_successor(
    workspace,
    valid_artifact,
) -> None:
    _, provisional = _provisionally_freeze(workspace)
    accepted = _accept_anchor(workspace, provisional)
    frozen_path = workspace / "artifacts/E01/experiment.frozen.json"
    frozen_bytes = frozen_path.read_bytes()

    amendment = valid_artifact("E06", "amendment-1")
    amendment_record = {
        "artifact_id": "E06", "instance_key": "amendment-1",
        "path": "artifacts/E06/amendment-1.json", "digest": digest(amendment),
        "parents": ["E01:experiment.frozen"], "status": "published",
    }
    current = mutate(
        workspace, expected_revision=accepted.revision, operation_id="amendment-1",
        event_type="amendment.accepted",
        subjects={"workspace_id": accepted.state["workspace_id"], "experiment_id": "experiment"},
        payload={
            "classification": "E06", "reason": "pre-run non-material",
            "published_artifacts": [amendment_record],
        },
        artifact_writes=((amendment_record["path"], amendment, True),),
    )
    assert "amendment-1" in current.state["artifacts"]["E06"]

    current = mutate(
        workspace, expected_revision=current.revision, operation_id="first-run",
        event_type="run.materialized",
        subjects={
            "workspace_id": current.state["workspace_id"], "experiment_id": "experiment",
            "run_id": "run-1", "attempt_id": "attempt-1",
        },
        payload={"run_id": "run-1", "attempt_id": "attempt-1", "published_artifacts": []},
    )
    deviation = valid_artifact("E07", "deviation-1")
    deviation_record = {
        "artifact_id": "E07", "instance_key": "deviation-1",
        "path": "artifacts/E07/deviation-1.json", "digest": digest(deviation),
        "parents": ["E01:experiment.frozen"], "status": "published",
    }
    current = mutate(
        workspace, expected_revision=current.revision, operation_id="deviation-1",
        event_type="deviation.observed",
        subjects={"workspace_id": current.state["workspace_id"], "experiment_id": "experiment"},
        payload={
            "classification": "E07", "observed_facts": ["worker preempted"],
            "published_artifacts": [deviation_record],
        },
        artifact_writes=((deviation_record["path"], deviation, True),),
    )
    assert "deviation-1" in current.state["artifacts"]["E07"]

    successor = valid_artifact("E01", "experiment-v2")
    successor_record = {
        "artifact_id": "E01", "instance_key": "experiment-v2",
        "path": "artifacts/E01/experiment-v2.json", "digest": digest(successor),
        "parents": ["E01:experiment.frozen", "E07:deviation-1"], "status": "published",
    }
    current = mutate(
        workspace, expected_revision=current.revision, operation_id="publish-successor",
        event_type="artifact.published",
        subjects={
            "workspace_id": current.state["workspace_id"], "artifact_id": "E01",
            "instance_key": "experiment-v2",
        },
        payload=successor_record,
        artifact_writes=((successor_record["path"], successor, True),),
    )
    current = mutate(
        workspace, expected_revision=current.revision, operation_id="successor-1",
        event_type="experiment.superseded",
        subjects={"workspace_id": current.state["workspace_id"], "experiment_id": "experiment"},
        payload={"old_id": "E01:experiment.frozen", "new_id": "E01:experiment-v2"},
    )
    assert frozen_path.read_bytes() == frozen_bytes
    event_types = [event["event_type"] for event in replay(workspace).events]
    assert event_types[-5:] == [
        "amendment.accepted", "run.materialized", "deviation.observed",
        "artifact.published", "experiment.superseded",
    ]
    edges = {(edge["from"], edge["to"], edge["relation"]) for edge in current.state["lineage"]["edges"]}
    assert ("E01:experiment.frozen", "E01:experiment-v2", "superseded-by") in edges
    assert ("E01:experiment-v2", "E01:experiment.frozen", "supersedes") in edges
    assert ("E07:deviation-1", "E01:experiment-v2", "derived-from") in edges


def test_supersession_cannot_invent_an_unmaterialized_successor(workspace) -> None:
    _, provisional = _provisionally_freeze(workspace)
    accepted = _accept_anchor(workspace, provisional)
    with pytest.raises(WorkspaceError) as caught:
        mutate(
            workspace, expected_revision=accepted.revision,
            operation_id="invent-successor", event_type="experiment.superseded",
            subjects={"workspace_id": accepted.state["workspace_id"], "experiment_id": "experiment"},
            payload={"old_id": "E01:experiment.frozen", "new_id": "E01:not-materialized"},
        )
    assert caught.value.code in {"successor_missing", "lineage_dangling_endpoint", "supersession_invalid"}


def test_decision_supersession_uses_real_immutable_records(
    workspace,
    valid_artifact,
) -> None:
    current = replay(workspace)
    decision_bytes: dict[str, bytes] = {}
    for key, outcome in (("decision-v1", "hypothesis_supported"), ("decision-v2", "inconclusive")):
        decision = valid_artifact("D05", key)
        decision["content"]["outcome"] = outcome
        decision["content_digest"] = digest(decision["content"])
        record = {
            "artifact_id": "D05", "instance_key": key,
            "path": f"artifacts/D05/{key}.json", "digest": digest(decision),
            "parents": [], "status": "published",
        }
        mutation = mutate(
            workspace, expected_revision=current.revision,
            operation_id=f"publish-{key}", event_type="artifact.published",
            subjects={
                "workspace_id": current.genesis["workspace_id"],
                "artifact_id": "D05", "instance_key": key,
            },
            payload=record,
            artifact_writes=((record["path"], decision, True),),
        )
        current = replay(workspace)
        decision_bytes[key] = (workspace / record["path"]).read_bytes()
        assert mutation.state["artifacts"]["D05"][key]["digest"] == record["digest"]

    mutation = mutate(
        workspace, expected_revision=current.revision,
        operation_id="supersede-decision", event_type="decision.superseded",
        subjects={"workspace_id": current.genesis["workspace_id"]},
        payload={"old_id": "D05:decision-v1", "new_id": "D05:decision-v2"},
    )
    assert "model" not in mutation.state["dimensions"]
    edges = {
        (edge["from"], edge["to"], edge["relation"])
        for edge in mutation.state["lineage"]["edges"]
    }
    assert {
        ("D05:decision-v1", "D05:decision-v2", "superseded-by"),
        ("D05:decision-v2", "D05:decision-v1", "supersedes"),
    }.issubset(edges)
    for key, original in decision_bytes.items():
        assert (workspace / f"artifacts/D05/{key}.json").read_bytes() == original


def test_D05_rule_complete(asset_bundle, valid_artifact) -> None:
    decision_rule = asset_bundle.document("artifact-contracts.json")["decision_rule"]
    assert decision_rule["outcomes"] == ["invalid", "inconclusive", "hypothesis_rejected", "hypothesis_supported"]
    assert decision_rule["precedence"] == ["invalid", "inconclusive", "hypothesis_rejected", "hypothesis_supported"]
    assert {"mutually-exclusive", "exhaustive", "frozen", "complete-attempt-accounting"} == set(decision_rule["requirements"])

    valid = valid_artifact("D05", "decision-1")
    assert asset_bundle.validate_artifact("D05", valid) == []
    invalid = valid_artifact("D05", "decision-2")
    invalid["content"]["outcome"] = "best-run-wins"
    invalid["content_digest"] = digest(invalid["content"])
    findings = asset_bundle.validate_artifact("D05", invalid)
    assert any(item["code"] == "invalid_scientific_outcome" for item in findings)


def test_lifecycle_transition_uses_declared_dimension(workspace) -> None:
    base = replay(workspace)
    advanced = mutate(
        workspace,
        expected_revision=base.revision,
        operation_id="advance-step-1",
        event_type="lifecycle.advanced",
        subjects={"workspace_id": base.genesis["workspace_id"]},
        payload={"step": 1, "outputs": ["S01"], "substate_events": []},
    )
    assert advanced.state["dimensions"]["jira"] == "open"
    assert advanced.state["lifecycle_step"] == 2
    assert "dimension" not in advanced.state["dimensions"]
    assert "from" not in advanced.state["dimensions"]
    assert "to" not in advanced.state["dimensions"]


def test_lifecycle_rows_must_advance_in_order(workspace) -> None:
    base = replay(workspace)
    with pytest.raises(WorkspaceError) as caught:
        mutate(
            workspace,
            expected_revision=base.revision,
            operation_id="skip-to-step-2",
            event_type="lifecycle.advanced",
            subjects={"workspace_id": base.genesis["workspace_id"]},
            payload={"step": 2, "outputs": ["S02"], "substate_events": []},
        )
    assert caught.value.code == "lifecycle_step_out_of_order"


@pytest.mark.parametrize(
    "transition_name",
    [
        "baseline.deprecate", "lifecycle.cancel",
        "lifecycle.exception", "lifecycle.archive", "lifecycle.close",
    ],
)
def test_terminal_governance_transition_gates_execute_and_block_shortcuts(
    asset_bundle,
    transition_name: str,
) -> None:
    transition = asset_bundle.transition(transition_name)

    def leaves(node: dict) -> list[tuple[str, str]]:
        kind, value = next(iter(node.items()))
        if kind in {"all_of", "sequence", "any_of"}:
            return [leaf for child in value for leaf in leaves(child)]
        if kind == "when":
            return leaves(value["then"])
        if kind == "substate_event":
            return []
        if kind in {"check", "operation", "platform_result", "artifact", "authority"}:
            name = value if isinstance(value, str) else value.get("name") or value.get("operation") or value.get("id") or value.get("action")
            return [(kind, name)]
        raise AssertionError(f"unexpected transition gate operator: {kind}")

    required = leaves(transition["gate"])
    context = {"checks": {}, "platform_results": {}, "artifacts": {}, "authorities": {}}
    for kind, name in required:
        if kind == "check":
            context["checks"][name] = True
        elif kind in {"operation", "platform_result"}:
            context["platform_results"][name] = "successful"
        elif kind == "artifact":
            context["artifacts"][name] = {
                "fixture": {
                    "path": f"artifacts/{name}/fixture.json",
                    "digest": "sha256:" + "a" * 64,
                    "status": "published",
                }
            }
        elif kind == "authority":
            context["authorities"][name] = ["attestation"]
    assert evaluate_gate(transition["gate"], context)[0] is True

    first_kind, first_name = required[0]
    if first_kind == "check":
        context["checks"][first_name] = False
    elif first_kind in {"operation", "platform_result"}:
        context["platform_results"].pop(first_name)
    elif first_kind == "artifact":
        context["artifacts"].pop(first_name)
    else:
        context["authorities"].pop(first_name)
    passed, missing, _ = evaluate_gate(transition["gate"], context)
    assert passed is False
    assert missing
