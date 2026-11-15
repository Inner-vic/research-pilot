from __future__ import annotations

import json
from pathlib import Path

import specctl
from canonical import digest, read_document
from ledger import replay
from projection import materialize
from workspace import mutate as raw_mutate, snapshot_tree


def _invoke(
    workspace: Path, tmp_path: Path, capsys, payload: dict, name: str,
) -> tuple[int, dict]:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({
        "subjects": {"artifact_id": "planning-root"}, **payload,
    }), encoding="utf-8")
    code = specctl.main([
        "lifecycle", "advance", "--workspace", str(workspace),
        "--payload", str(path), "--expected-revision",
        str(replay(workspace).revision), "--operation-id", name,
    ])
    return code, json.loads(capsys.readouterr().out)


def _envelope(
    workspace_id: str, artifact_type: str, key: str, content: dict,
) -> dict:
    return specctl._envelope(
        artifact_type, key, key, content,
        {"workspace_id": workspace_id, "artifact_id": "planning-root"},
    )


def test_public_required_inputs_and_t01_reject_missing_stale_and_lineage_substitution(
    workspace: Path, tmp_path: Path, capsys,
) -> None:
    workspace_id = replay(workspace).genesis["workspace_id"]
    requirement = _envelope(workspace_id, "S01", "requirement", {
        "requirement_id": "requirement", "uri": "jira://REQ-001",
        "owner": "product-owner", "impact": "bounded measurable impact",
        "non_goals": ["unbounded platform changes"],
    })
    planning = _envelope(workspace_id, "S03", "planning", {
        "product_brief": {
            "kind": "product-brief",
            "uri": "bmad://product-brief/REQ-001",
            "digest": digest({"bmad_object": "product-brief/REQ-001"}),
            "revision": "planning-v1",
        },
        "prd": {
            "kind": "prd", "uri": "bmad://prd/REQ-001",
            "digest": digest({"bmad_object": "prd/REQ-001"}),
            "revision": "planning-v1",
        },
        "architecture": {
            "kind": "architecture",
            "uri": "bmad://architecture/REQ-001",
            "digest": digest({"bmad_object": "architecture/REQ-001"}),
            "revision": "planning-v1",
        },
        "source_refs": {"S01:requirement": digest(requirement)},
    })
    code, output = _invoke(workspace, tmp_path, capsys, {
        "step": 1, "records": [requirement], "input_records": [planning],
    }, "public-step1-with-planning-input")
    assert code == 0, output
    state = materialize(replay(workspace))
    assert state["lifecycle_step"] == 2
    requirement_digest = state["artifacts"]["S01"]["requirement"]["digest"]
    planning_digest = state["artifacts"]["S03"]["planning"]["digest"]
    exact_refs = {
        "S01": [{
            "instance_key": "requirement", "digest": requirement_digest,
        }],
        "S03": [{
            "instance_key": "planning", "digest": planning_digest,
        }],
    }
    exact_sources = {
        "S01:requirement": requirement_digest,
        "S03:planning": planning_digest,
    }

    def research(source_refs: dict[str, str]) -> dict:
        return _envelope(workspace_id, "S02", "research", {
            "brief_id": "research", "requirement_id": "requirement",
            "hypothesis": "a frozen mechanism changes the measured outcome",
            "mechanism": "deterministic causal mechanism",
            "uncertainty": {"effect_size": "not yet bounded"},
            "history": ["bmad://history/REQ-001"],
            "target_as_must": False,
            "source_refs": source_refs,
        })

    adversarial = [
        (
            "missing-required-planning",
            {"S01": exact_refs["S01"]},
            exact_sources,
            "step_required_input_missing",
        ),
        (
            "stale-required-input-digest",
            {
                **exact_refs,
                "S03": [{
                    "instance_key": "planning",
                    "digest": digest({"substituted": "planning"}),
                }],
            },
            exact_sources,
            "step_input_digest_mismatch",
        ),
        (
            "lineage-source-ref-omitted",
            exact_refs,
            {"S01:requirement": requirement_digest},
            "step_output_lineage_mismatch",
        ),
    ]
    for name, input_refs, source_refs, expected_error in adversarial:
        before = snapshot_tree(workspace)
        revision = replay(workspace).revision
        code, output = _invoke(workspace, tmp_path, capsys, {
            "step": 2, "input_refs": input_refs,
            "records": [research(source_refs)],
        }, name)
        assert code == 3 and output["status"] == "blocked", output
        assert output["result"]["error"] == expected_error
        assert replay(workspace).revision == revision
        assert snapshot_tree(workspace) == before

    accepted = research(exact_sources)
    code, output = _invoke(workspace, tmp_path, capsys, {
        "step": 2, "input_refs": exact_refs, "records": [accepted],
    }, "accept-exact-research-lineage")
    assert code == 0, output
    state = materialize(replay(workspace))
    assert state["lifecycle_step"] == 3
    edges = {
        (row["from"], row["to"], row["relation"])
        for row in state["lineage"]["edges"]
    }
    assert edges.issuperset({
        ("S01:requirement", "S03:planning", "derived-from"),
        ("S01:requirement", "S02:research", "derived-from"),
        ("S03:planning", "S02:research", "derived-from"),
    })
    assert read_document(
        workspace / "artifacts" / "S02" / "research.json",
    ) == accepted

    # A pre-existing replay-authenticated T01 defect blocks the public
    # coordinator before it evaluates inputs or creates platform handoffs.
    base = replay(workspace)
    raw_mutate(
        workspace,
        expected_revision=base.revision,
        operation_id="fixture-orphan-experiment",
        event_type="artifact.published",
        subjects={"workspace_id": workspace_id},
        payload={
            "artifact_id": "E01", "instance_key": "orphan-experiment",
            "path": "artifacts/E01/orphan-experiment.json",
            "digest": digest({"orphan": "experiment"}),
            "parents": [], "status": "published",
        },
    )
    before_guard = snapshot_tree(workspace)
    code, output = _invoke(workspace, tmp_path, capsys, {
        "step": 3,
        "input_refs": {"S02": {
            "instance_key": "research", "digest": digest(accepted),
        }},
        "records": [],
    }, "public-blocks-invalid-typed-graph")
    assert code == 3 and output["status"] == "blocked", output
    assert output["result"]["error"] == "lineage_node_invalid"
    assert snapshot_tree(workspace) == before_guard
