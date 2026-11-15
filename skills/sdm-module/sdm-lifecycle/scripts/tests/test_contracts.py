from __future__ import annotations

from copy import deepcopy
import json
from decimal import Decimal
import pytest

import contracts as contracts_module
from canonical import CanonicalError, canonical_bytes, digest, parse_document
from contracts import (
    PRIMARY_IDS,
    SUPPORTING_IDS,
    AssetBundle,
    ContractError,
    evaluate_gate,
    evaluate_scientific_decision,
    validate_schema,
)
from workspace import WorkspaceError, publish_artifact, snapshot_tree


def _materialize_engine_bundle(bundle: AssetBundle, target) -> None:
    target.mkdir(parents=True)
    for relative, data in bundle.canonical_assets.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _owner_schema_example(
    rule: dict, owner_schema: dict, *, label: str,
):
    """Build one deterministic value admitted by a persisted owner schema."""
    if "$ref" in rule:
        reference = rule["$ref"]
        assert reference.startswith("#/")
        resolved = owner_schema
        for token in reference[2:].split("/"):
            resolved = resolved[
                token.replace("~1", "/").replace("~0", "~")
            ]
        return _owner_schema_example(
            resolved, owner_schema, label=label,
        )
    if "const" in rule:
        return rule["const"]
    if "enum" in rule:
        return next(
            (value for value in rule["enum"] if value is not None),
            None,
        )
    for composition in ("oneOf", "anyOf"):
        if composition in rule and not any(
            key in rule for key in ("type", "properties", "required")
        ):
            return _owner_schema_example(
                rule[composition][0], owner_schema, label=label,
            )

    value_type = rule.get("type")
    if isinstance(value_type, list):
        value_type = next(
            (candidate for candidate in value_type if candidate != "null"),
            "null",
        )
    if value_type == "null":
        return None
    if value_type == "boolean":
        return True
    if value_type in {"integer", "number"}:
        minimum = rule.get("minimum", 0)
        if "exclusiveMinimum" in rule:
            minimum = rule["exclusiveMinimum"] + 1
        return int(minimum) if value_type == "integer" else float(minimum)
    if value_type == "array":
        if rule.get("maxItems") == 0:
            return []
        size = max(1, rule.get("minItems", 0))
        return [
            _owner_schema_example(
                rule.get("items", {}), owner_schema,
                label=f"{label}-{index}",
            )
            for index in range(size)
        ]
    if value_type == "object" or "properties" in rule:
        properties = rule.get("properties", {})
        value = {
            key: _owner_schema_example(
                properties[key], owner_schema, label=f"{label}-{key}",
            )
            for key in rule.get("required", [])
        }
        minimum = rule.get("minProperties", 0)
        for key in properties:
            if len(value) >= minimum:
                break
            value[key] = _owner_schema_example(
                properties[key], owner_schema, label=f"{label}-{key}",
            )
        if len(value) < minimum:
            additional = rule.get("additionalProperties", {})
            assert additional is not False
            value["fixture_key"] = _owner_schema_example(
                additional if isinstance(additional, dict) else {},
                owner_schema, label=f"{label}-fixture-value",
            )
        if "oneOf" in rule:
            selected = rule["oneOf"][0]
            for key, selected_rule in selected.get("properties", {}).items():
                value[key] = _owner_schema_example(
                    selected_rule, owner_schema, label=f"{label}-{key}",
                )
        return value
    if value_type == "string":
        if rule.get("format") == "sha256" or "sha256" in rule.get(
            "pattern", "",
        ):
            return digest({"owner_schema_fixture": label})
        if rule.get("format") == "date-time":
            return "2026-01-02T03:04:05Z"
        pattern = rule.get("pattern", "")
        for prefix in ("R04:", "R05:", "G10:"):
            if prefix in pattern:
                return f"{prefix}{label}"
        if pattern.startswith("^[A-Z][A-Z0-9]*:"):
            return f"G01:{label}"
        if "code:" in pattern and "config:/" in pattern:
            return f"code:{label}.py"
        if "[^?]+:[^?]+" in pattern:
            return f"artifact://fixture/{label}"
        if pattern.startswith("^/"):
            return f"/{label}"
        if pattern.startswith("^[A-Z][A-Z0-9_]"):
            return "FIXTURE"
        return f"value-{label}"
    return f"value-{label}"


def test_asset_bundle_is_complete_and_content_addressed(asset_bundle: AssetBundle) -> None:
    assert tuple(row["id"] for row in asset_bundle.document("artifact-catalog.json")["artifacts"]) == PRIMARY_IDS
    assert tuple(row["id"] for row in asset_bundle.document("supporting-artifact-catalog.json")["artifacts"]) == SUPPORTING_IDS
    assert len(asset_bundle.document("lifecycle-steps.json")["steps"]) == 21
    assert len(asset_bundle.asset_digests) == 26
    assert asset_bundle.document("artifact-contracts.json")["validation_order"][:3] == [
        "envelope-schema", "owner-schema", "catalog-entry",
    ]
    assert asset_bundle.engine_contract_digest.startswith("sha256:")
    assert len(asset_bundle.event_types()) == 49


def test_schema_subset_executes_required_unknown_ref_and_unique_rules(asset_bundle: AssetBundle) -> None:
    schema = {
        "type": "object",
        "required": ["id", "items"],
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "pattern": "^[a-z]+$"},
            "items": {"type": "array", "minItems": 2, "uniqueItems": True},
        },
    }
    codes = {item["code"] for item in validate_schema({"id": "BAD", "items": [1, 1], "extra": True}, schema)}
    assert {"schema_pattern", "schema_unique_items", "schema_unknown_field"}.issubset(codes)
    unresolved = validate_schema({}, {"$ref": "missing.schema.json"}, registry=asset_bundle.documents)
    assert [item["code"] for item in unresolved] == ["schema_ref_unresolved"]


def test_gate_dsl_is_executable_and_sequence_preserves_substates() -> None:
    gate = {
        "sequence": [
            {"check": "checks.ready"},
            {"substate_event": "gate.checked"},
            {"any_of": [{"artifact": "G01"}, {"platform_result": "git.snapshot"}]},
        ]
    }
    passed, missing, events = evaluate_gate(gate, {
        "checks": {"ready": True},
        "artifacts": {
            "G01": {
                "governance-v1": {
                    "path": "artifacts/G01/governance-v1.json",
                    "digest": digest({"governance": "v1"}),
                    "status": "published",
                },
            },
        },
    })
    assert passed is True
    assert missing == []
    assert events == ["gate.checked"]
    blocked, missing, events = evaluate_gate(gate, {"checks": {"ready": False}})
    assert blocked is False
    assert missing == ["checks.ready"]
    assert events == []


def test_strict_malformed_ingress_matrix(asset_bundle: AssetBundle, valid_artifact) -> None:
    malformed = [
        ('{"duplicate":1,"duplicate":2}', "duplicate_key"),
        ('{"value":NaN}', "non_finite_number"),
        ('value: yes\n', "yaml_implicit_type"),
        ('value: 2026-08-24\n', "implicit_timestamp"),
        ('value: &anchor x\n', "yaml_feature_forbidden"),
        ('{"huge":9223372036854775808}', "integer_out_of_range"),
    ]
    for document, code in malformed:
        with pytest.raises(CanonicalError) as caught:
            parse_document(document)
        assert caught.value.code == code

    artifact = valid_artifact("G01")
    artifact["unknown"] = "forbidden"
    findings = asset_bundle.validate_artifact("G01", artifact)
    assert any(item["code"] == "schema_unknown_field" and item["json_pointer"] == "/unknown" for item in findings)

    wrong_type = valid_artifact("G01")
    wrong_type["version"] = "one"
    findings = asset_bundle.validate_artifact("G01", wrong_type)
    assert any(item["code"] == "schema_type" and item["json_pointer"] == "/version" for item in findings)

    # Common ingress remains a total, fail-closed function even when nested
    # values have shapes that artifact-specific semantic code cannot consume.
    for artifact_id in (
        "G08", "G10", "B02", "E01", "E02", "E03", "E04", "E07",
        "R02", "R03", "R05", "R07", "R08", "D01", "D04", "D05",
        "T01",
    ):
        nested_type_attack = valid_artifact(
            artifact_id, f"malformed-nested-{artifact_id.lower()}",
        )
        nested_type_attack["content"] = {
            "nested": [
                {"artifact_uri": []},
                {"artifact_digest": {"unexpected": [True]}},
            ],
        }
        nested_type_attack["content_digest"] = digest(
            nested_type_attack["content"],
        )
        assert asset_bundle.validate_artifact(
            artifact_id, nested_type_attack,
        ), artifact_id

    for degenerate_uri in (":", "x:"):
        uri_attack = valid_artifact(
            "G01", f"degenerate-uri-{len(degenerate_uri)}",
        )
        uri_attack["uri"] = degenerate_uri
        uri_attack["content_digest"] = digest(uri_attack["content"])
        codes = {
            item["code"]
            for item in asset_bundle.validate_artifact("G01", uri_attack)
        }
        assert {"artifact_uri_required", "unsafe_uri"}.issubset(codes)

    bad_digest = valid_artifact("G01")
    bad_digest["content"]["mutated_after_digest"] = True
    findings = asset_bundle.validate_artifact("G01", bad_digest)
    assert any(item["code"] == "content_digest_mismatch" for item in findings)

    bmad_content = {
        "product_brief": {
            "kind": "product-brief", "uri": "bmad://product-brief/REQ-1",
            "digest": digest({"bmad": "product-brief"}),
            "revision": "planning-v1",
        },
        "prd": {
            "kind": "prd", "uri": "bmad://prd/REQ-1",
            "digest": digest({"bmad": "prd"}),
            "revision": "planning-v1",
        },
        "architecture": {
            "kind": "architecture", "uri": "bmad://architecture/REQ-1",
            "digest": digest({"bmad": "architecture"}),
            "revision": "planning-v1",
        },
        "source_refs": {"S01:requirement": digest({"requirement": 1})},
    }
    bmad = valid_artifact("S03", "planning-v1", content=bmad_content)
    assert asset_bundle.validate_artifact("S03", bmad) == []
    bmad_attacks = (
        ("product_brief", "uri", "bmad://product-brief/REQ-1?latest=1", "bmad_link_uri_invalid"),
        ("prd", "revision", "latest", "bmad_link_revision_invalid"),
        ("architecture", "digest", "sha256:bad", "bmad_link_digest_invalid"),
    )
    for field, key, substitution, expected in bmad_attacks:
        attacked = json.loads(json.dumps(bmad))
        attacked["content"][field][key] = substitution
        attacked["content_digest"] = digest(attacked["content"])
        assert expected in {
            item["code"]
            for item in asset_bundle.validate_artifact("S03", attacked)
        }
    wrong_requirement = json.loads(json.dumps(bmad))
    wrong_requirement["content"]["source_refs"] = {
        "S02:research": digest({"research": 1}),
    }
    wrong_requirement["content_digest"] = digest(
        wrong_requirement["content"],
    )
    assert "bmad_requirement_binding_invalid" in {
        item["code"]
        for item in asset_bundle.validate_artifact("S03", wrong_requirement)
    }


def test_D05_artifact_recomputes_frozen_decision_context(asset_bundle: AssetBundle, valid_artifact) -> None:
    frozen_rules = {
        "schema": "sdm-scientific-rule/v1",
        "support_predicate": "frozen_support",
        "rejection_predicate": "frozen_reject",
        "inconclusive_rule": "underpowered_or_no_decisive_result",
        "invalid_rule": "protocol_invalid",
    }
    planned_runs = [{"run_id": "run-1", "required_attempt_ids": ["attempt-1"]}]
    attempts = [{
        "attempt_id": "attempt-1", "run_id": "run-1",
        "terminal_status": "successful", "accounted": True,
        "validity": "valid", "system_failure": False,
    }]
    evidence = {
        "protocol_invalid": False, "underpowered": False,
        "predicates": {"frozen_support": True, "frozen_reject": False},
    }
    evaluated = evaluate_scientific_decision(frozen_rules, planned_runs, attempts, evidence)
    decision = valid_artifact("D05", "decision-executable")
    decision["content"].update({
        "outcome": evaluated["outcome"],
        "rule_digest": evaluated["rule_digest"],
        "decision_context": {
            "frozen_rules": frozen_rules, "planned_runs": planned_runs,
            "attempts": attempts, "evidence": evidence,
        },
    })
    decision["content_digest"] = digest(decision["content"])
    assert asset_bundle.validate_artifact("D05", decision) == []

    tampered = json.loads(json.dumps(decision))
    tampered["content"]["outcome"] = "hypothesis_rejected"
    tampered["content_digest"] = digest(tampered["content"])
    findings = asset_bundle.validate_artifact("D05", tampered)
    assert any(item["code"] == "scientific_outcome_mismatch" for item in findings)


def test_invalid_artifact_ingress_makes_no_workspace_change(workspace, valid_artifact) -> None:
    invalid = valid_artifact("G01", "bad-ingress")
    invalid["unknown"] = "forbidden"
    before = snapshot_tree(workspace)
    with pytest.raises(WorkspaceError) as caught:
        publish_artifact(
            workspace,
            artifact_id="G01",
            instance_key="bad-ingress",
            value=invalid,
            expected_revision=0,
            operation_id="publish-invalid-artifact",
        )
    assert caught.value.code == "artifact_invalid"
    assert snapshot_tree(workspace) == before


def test_documentation_only_schema_rejected(asset_bundle: AssetBundle, valid_artifact) -> None:
    artifact = valid_artifact("G03")
    artifact["content"] = {"dataset_identity": "dataset-v1"}
    artifact["content_digest"] = digest(artifact["content"])
    # The common envelope is structurally valid, while the pinned owner
    # schema rejects the documentation-only G03 shape before semantic code
    # interprets its nested values.
    assert validate_schema(
        artifact,
        asset_bundle.document("schemas/artifact.schema.json"),
        registry=asset_bundle.documents,
    ) == []
    findings = asset_bundle.validate_artifact("G03", artifact)
    assert "schema_composition" in {item["code"] for item in findings}

    rows = asset_bundle.document("artifact-contracts.json")[
        "semantic_validators"
    ]
    assert all(
        contracts_module.SEMANTIC_EXECUTORS[row["executor"]]
        is not contracts_module._semantic_generic
        for row in rows
    )
    by_id = {row["id"]: row["executor"] for row in rows}
    assert len({
        by_id["artifact.governance-principles"],
        by_id["artifact.evidence-summary"],
        by_id["artifact.scientific-decision"],
    }) == 3
    assert by_id["artifact.dataset-manifest"] != \
        by_id["artifact.calibration-manifest"]

    requirement = valid_artifact("S01", "semantic-requirement")
    requirement["content"] = {
        "requirement_id": "REQ-001",
        "uri": "jira://REQ-001",
        "owner": "product-owner",
        "impact": "measurably reduce governed error",
        "non_goals": ["unbounded platform changes"],
    }
    requirement["content_digest"] = digest(requirement["content"])
    assert asset_bundle.validate_artifact("S01", requirement) == []
    requirement["content"]["uri"] = "jira://REQ-001?floating=latest"
    requirement["content_digest"] = digest(requirement["content"])
    assert "product_requirement_uri_invalid" in {
        item["code"]
        for item in asset_bundle.validate_artifact("S01", requirement)
    }

    brief = valid_artifact("S02", "semantic-brief")
    brief["content"] = {
        "brief_id": "brief-001", "requirement_id": "REQ-001",
        "hypothesis": "the governed intervention changes measured error",
        "mechanism": "isolate the intervention against the locked baseline",
        "uncertainty": {"effect_size": "unknown"},
        "history": ["new experiment"],
        "source_refs": {"S01:semantic-requirement": digest(requirement)},
    }
    brief["content_digest"] = digest(brief["content"])
    assert asset_bundle.validate_artifact("S02", brief) == []
    brief["content"]["hypothesis"] = "TBD"
    brief["content_digest"] = digest(brief["content"])
    assert "research_hypothesis_not_falsifiable" in {
        item["code"]
        for item in asset_bundle.validate_artifact("S02", brief)
    }

    platform_receipt = {
        "request_id": "REQ-platform-1",
        "request_digest": digest({"request": 1}),
        "result_id": "RES-platform-1",
        "result_digest": digest({"result": 1}),
        "receipt_digest": digest({"receipt": 1}),
        "producer_identity": "ml-platform",
        "producer_principal_id": "ml-platform-principal",
        "producer_attestation_digest": digest({"attestation": 1}),
    }
    for artifact_id in ("R03", "R05", "R07", "R08"):
        receipt_bound = valid_artifact(
            artifact_id, f"receipt-bound-{artifact_id.lower()}",
        )
        receipt_bound["created_by"] = platform_receipt["producer_identity"]
        receipt_bound["content"]["receipt"] = platform_receipt
        receipt_bound["content_digest"] = digest(receipt_bound["content"])
        for receipt_attack in ("missing", "opaque"):
            attacked = json.loads(json.dumps(receipt_bound))
            if receipt_attack == "missing":
                attacked["content"].pop("receipt")
            else:
                attacked["content"]["receipt"] = "opaque-receipt"
            attacked["content_digest"] = digest(attacked["content"])
            assert "platform_receipt_invalid" in {
                item["code"]
                for item in asset_bundle.validate_artifact(
                    artifact_id, attacked,
                )
            }, (artifact_id, receipt_attack)

    resolved_configuration = valid_artifact(
        "R02", "empty-frozen-sources",
    )
    resolved_configuration["content"].update({
        "run_id": "run-1",
        "attempt_id": "attempt-1",
        "source_run_plan_id": "run-plan-1",
        "resolved_values": {"learning_rate": 0.01},
        "defaults": [],
        "override_origins": [],
        "structured_config_schema_digest": digest({"schema": 1}),
        "unknown_keys": [],
        "forbidden_overrides": [],
        "resolution_trace_digest": digest({"trace": 1}),
        "config_digest": digest({"learning_rate": 0.01}),
        "frozen_sources": [],
    })
    resolved_configuration["content_digest"] = digest(
        resolved_configuration["content"],
    )
    assert "resolved_configuration_frozen_sources_invalid" in {
        item["code"]
        for item in asset_bundle.validate_artifact(
            "R02", resolved_configuration,
        )
    }

    hpo_run = valid_artifact("R07", "hpo-empty-fields")
    hpo_run["created_by"] = platform_receipt["producer_identity"]
    hpo_run["content"].update({
        "run_id": "run-1",
        "attempt_id": "attempt-hpo-1",
        "attempt_kind": "hpo_trial",
        "status": "successful",
        "validity": "valid",
        "system_failure": False,
        "started_at": "2026-01-02T03:04:05Z",
        "completed_at": "2026-01-02T03:05:05Z",
        "resource_use": {"gpu_seconds": 60},
        "receipt": platform_receipt,
        "author_principal_ids": ["ml-platform-principal"],
        "phase": "search",
        "environment_instance_identity": "environment-1",
        "environment_plan_digest": digest({"environment_plan": 1}),
        "environment_digest": digest({"environment": 1}),
        "configuration_digest": digest({"configuration": 1}),
        "objective_metric": "validation-loss",
        "objective_value": 0.5,
        "partition_digest": digest({"partition": 1}),
        "seed": "seed-1",
    })
    hpo_run["content_digest"] = digest(hpo_run["content"])
    assert "hpo_trial_facts_invalid" not in {
        item["code"]
        for item in asset_bundle.validate_artifact("R07", hpo_run)
    }
    for field, empty_value in (
        ("phase", ""),
        ("environment_instance_identity", ""),
        ("environment_plan_digest", ""),
        ("environment_digest", ""),
        ("configuration_digest", ""),
        ("objective_metric", ""),
        ("objective_value", None),
        ("partition_digest", ""),
        ("seed", ""),
    ):
        attacked = json.loads(json.dumps(hpo_run))
        attacked["content"][field] = empty_value
        attacked["content_digest"] = digest(attacked["content"])
        assert "hpo_trial_facts_invalid" in {
            item["code"]
            for item in asset_bundle.validate_artifact("R07", attacked)
        }, field

    empty_hpo_index = valid_artifact("R08", "hpo-empty-artifacts")
    empty_hpo_index["created_by"] = platform_receipt["producer_identity"]
    empty_hpo_index["content"].update({
        "run_id": "run-1",
        "attempt_id": "attempt-hpo-1",
        "attempt_kind": "hpo_trial",
        "artifacts": [],
        "completeness": True,
        "receipt": platform_receipt,
        "author_principal_ids": ["ml-platform-principal"],
    })
    empty_hpo_index["content_digest"] = digest(
        empty_hpo_index["content"],
    )
    assert "artifact_index_digest_invalid" in {
        item["code"]
        for item in asset_bundle.validate_artifact("R08", empty_hpo_index)
    }

    evidence_sources = {
        "E01:experiment-1.frozen": digest({"E01": 1}),
        "E04:run-plan-1": digest({"E04": 1}),
        "R01:run-1": digest({"R01": 1}),
        "R07:attempt-1": digest({"R07": 1}),
        "R08:attempt-1": digest({"R08": 1}),
    }
    evidence_index = valid_artifact(
        "D01", "semantic-evidence-index", content={
            "evidence_id": "evidence-1",
            "experiment_id": "experiment-1",
            "planned_runs": [{
                "run_id": "run-1",
                "required_attempt_ids": ["attempt-1"],
            }],
            "accounted_attempts": [{
                "attempt_id": "attempt-1",
                "run_id": "run-1",
                "terminal_status": "successful",
                "accounted": True,
                "validity": "valid",
                "system_failure": False,
            }],
            "scientific_attempt_ids": ["attempt-1"],
            "items": sorted(evidence_sources),
            "source_digests": evidence_sources,
            "bundle_digest": digest(evidence_sources),
            "author_principal_ids": ["evidence-author"],
        },
    )
    assert asset_bundle.validate_artifact("D01", evidence_index) == []
    for field, malformed_value, expected_code in (
        (
            "accounted_attempts", [{"attempt_id": "attempt-1"}],
            "evidence_attempt_accounting_invalid",
        ),
        ("items", [{}], "evidence_source_closure_invalid"),
        (
            "source_digests", {"R07:attempt-1": "opaque-digest"},
            "evidence_source_closure_invalid",
        ),
        ("bundle_digest", "opaque-digest", "evidence_source_closure_invalid"),
    ):
        attacked = json.loads(json.dumps(evidence_index))
        attacked["content"][field] = malformed_value
        attacked["content_digest"] = digest(attacked["content"])
        assert expected_code in {
            item["code"]
            for item in asset_bundle.validate_artifact("D01", attacked)
        }, field

    failure_index = valid_artifact(
        "D04", "semantic-failure-index", content={
            "taxonomy": [{
                "attempt_id": "attempt-1", "value": {"class": "occlusion"},
            }],
            "cases": [{
                "attempt_id": "attempt-1", "value": ["case-1"],
            }],
            "artifact_digests": [{
                "attempt_id": "attempt-1",
                "value": {"saliency": digest({"saliency": 1})},
            }],
            "coverage": [{
                "attempt_id": "attempt-1", "value": {"count": 1},
            }],
            "source_attempt_ids": ["attempt-1"],
            "author_principal_ids": ["evidence-author"],
        },
    )
    assert asset_bundle.validate_artifact("D04", failure_index) == []
    failure_index["content"]["artifact_digests"][0]["value"] = {
        "saliency": "artifact://not-a-digest",
    }
    failure_index["content_digest"] = digest(failure_index["content"])
    assert "failure_artifact_digest_invalid" in {
        item["code"]
        for item in asset_bundle.validate_artifact("D04", failure_index)
    }

    scientific_decision = valid_artifact(
        "D05", "semantic-decision-malformed-types",
    )
    scientific_decision["content"].update({
        "decision_id": "decision-1",
        "experiment_id": "experiment-1",
        "reviewers": ["reviewer-1"],
        "authority": [digest({"reviewer": "reviewer-1"})],
    })
    scientific_decision["content_digest"] = digest(
        scientific_decision["content"],
    )
    assert asset_bundle.validate_artifact("D05", scientific_decision) == []
    for field, malformed_value, expected_code in (
        (
            "decision_id", {"nested": "decision-1"},
            "scientific_decision_identity_invalid",
        ),
        (
            "reviewers", [{"principal": "reviewer-1"}],
            "scientific_decision_authority_invalid",
        ),
        (
            "authority", [{"attestation": "opaque"}],
            "scientific_decision_authority_invalid",
        ),
    ):
        attacked = json.loads(json.dumps(scientific_decision))
        attacked["content"][field] = malformed_value
        attacked["content_digest"] = digest(attacked["content"])
        assert expected_code in {
            item["code"]
            for item in asset_bundle.validate_artifact("D05", attacked)
        }, field

    traceability = valid_artifact(
        "T01", "revision-7", content={
            "schema": "sdm-traceability/v1",
            "nodes": [{
                "id": "G01:governance-1", "type": "G01",
                "event_hash": digest({"event": 1}),
            }],
            "edges": [],
            "derived_from_revision": 7,
        },
        subjects={"workspace_id": "workspace"},
    )
    assert asset_bundle.validate_artifact("T01", traceability) == []
    unknown_node = json.loads(json.dumps(traceability))
    unknown_node["content"]["nodes"][0].update({
        "id": "Unknown:node-1", "type": "Unknown",
    })
    unknown_node["content_digest"] = digest(unknown_node["content"])
    assert "traceability_node_invalid" in {
        item["code"]
        for item in asset_bundle.validate_artifact("T01", unknown_node)
    }
    revision_mismatch = json.loads(json.dumps(traceability))
    revision_mismatch["content"]["derived_from_revision"] = 8
    revision_mismatch["content_digest"] = digest(
        revision_mismatch["content"],
    )
    assert "traceability_export_binding_invalid" in {
        item["code"]
        for item in asset_bundle.validate_artifact("T01", revision_mismatch)
    }

    augmentation = valid_artifact(
        "G08", "semantic-augmentation", content={
            "operations": ["crop"],
            "probabilities": {"crop": 1.0},
            "ordering": ["crop"],
            "geometry_effects": {"crop": {"policy": "preserve"}},
            "source_refs": {"G03:data": digest({"G03": 1})},
        },
    )
    assert "augmentation_operation_invalid" in {
        item["code"]
        for item in asset_bundle.validate_artifact("G08", augmentation)
    }

    evaluation_contract = valid_artifact(
        "G10", "semantic-evaluation-contract", content={
            "evaluator_identity": "evaluator-1",
            "metric_definitions": {"accuracy": {"direction": "maximize"}},
            "slices": ["all"],
            "tolerances": {"accuracy": 0.01},
            "inconclusive_rule": "insufficient sample size blocks decision",
            "owner_principal_ids": ["evaluation-owner"],
            "source_refs": {"G03:data": digest({"G03": 1})},
        },
    )
    assert asset_bundle.validate_artifact(
        "G10", evaluation_contract,
    ) == []
    for field in ("metric_definitions", "tolerances"):
        attacked = json.loads(json.dumps(evaluation_contract))
        attacked["content"][field] = True
        attacked["content_digest"] = digest(attacked["content"])
        assert "evaluation_contract_not_executable" in {
            item["code"]
            for item in asset_bundle.validate_artifact("G10", attacked)
        }, field

    baseline_spec = valid_artifact("B01", "boolean-tolerances")
    baseline_spec["content"]["tolerances"] = True
    baseline_spec["content_digest"] = digest(baseline_spec["content"])
    assert "baseline_tolerances_invalid" in {
        item["code"]
        for item in asset_bundle.validate_artifact("B01", baseline_spec)
    }
    baseline_lock = valid_artifact("B02", "malformed-receipts")
    baseline_lock["content"]["receipts"] = [{"opaque": True}]
    baseline_lock["content_digest"] = digest(baseline_lock["content"])
    assert "schema_composition" in {
        item["code"]
        for item in asset_bundle.validate_artifact("B02", baseline_lock)
    }

    experiment_digest = digest({"experiment": 1})
    experiment = valid_artifact(
        "E01", "semantic-exploratory-experiment", content={
            "experiment_id": "experiment-1",
            "protocol_channel": "exploratory",
            "promotable": False,
            "hypothesis": "Regularization reduces validation error",
            "variables": {
                "independent": "regularization",
                "dependent": "validation error",
            },
            "contracts": {
                "source_digests": {"G10:evaluator": experiment_digest},
            },
            "baseline": {
                "lock_ref": "B02:baseline-lock",
                "lock_digest": digest({"lock": 1}),
                "baseline_digest": digest({"baseline": 1}),
            },
            "data": {
                "dataset_manifest_ref": "R04:dataset-1",
                "dataset_manifest_digest": digest({"dataset_manifest": 1}),
                "dataset_digest": digest({"dataset": 1}),
                "split_manifest_ref": "R05:split-1",
                "split_manifest_digest": digest({"split_manifest": 1}),
                "split_digest": digest({"split": 1}),
                "calibration_applicable": False,
            },
            "evaluator": {
                "contract_ref": "G10:evaluator",
                "contract_digest": experiment_digest,
                "evaluator_identity": "evaluator-1",
            },
            "budget": {"compute_hours": 1},
            "decision_rule": {
                "schema": "sdm-scientific-rule/v1",
                "support_predicate": "validation_error_reduced",
                "rejection_predicate": "validation_error_not_reduced",
                "inconclusive_rule": "underpowered_or_no_decisive_result",
                "invalid_rule": "protocol_invalid",
            },
            "author_principal_ids": ["research-owner"],
            "source_refs": {
                "G10:evaluator": experiment_digest,
                "B02:baseline-lock": digest({"lock": 1}),
                "R04:dataset-1": digest({"dataset_manifest": 1}),
                "R05:split-1": digest({"split_manifest": 1}),
            },
        },
    )
    assert asset_bundle.validate_artifact("E01", experiment) == []
    retired_eligibility = deepcopy(experiment)
    retired_eligibility["content"]["promotion_eligible"] = True
    retired_eligibility["content_digest"] = digest(retired_eligibility["content"])
    assert any(
        item["code"] == "schema_composition"
        for item in asset_bundle.validate_artifact("E01", retired_eligibility)
    )
    classified = deepcopy(experiment)
    classified["content"]["promotable"] = True
    classified["content_digest"] = digest(classified["content"])
    classifier_findings = contracts_module._semantic_experiment(
        classified, asset_bundle.artifact_contract("E01"), asset_bundle,
    )
    assert any(
        item["code"] == "exploratory_not_promotable"
        and item["json_pointer"] == "/content/promotable"
        for item in classifier_findings
    )
    for field, meaningless in (
        ("hypothesis", "TBD"),
        ("variables", {}),
        ("budget", {}),
    ):
        attacked = json.loads(json.dumps(experiment))
        attacked["content"][field] = meaningless
        attacked["content_digest"] = digest(attacked["content"])
        assert "experiment_scientific_kernel_invalid" in {
            item["code"]
            for item in asset_bundle.validate_artifact("E01", attacked)
        }, field

    bogus_gate = valid_artifact(
        "E02", "bogus-readiness-gate", content={
            "experiment_id": "experiment-1",
            "candidate_digest": digest({"candidate": 1}),
            "blocking_gates": [{
                "name": "invented-gate", "status": "pass",
            }],
            "independent_reviewer": "reviewer-1",
            "authority": [],
        },
    )
    assert "readiness_gate_closure_mismatch" in {
        item["code"]
        for item in asset_bundle.validate_artifact("E02", bogus_gate)
    }

    hpo_schema_name = asset_bundle.owner_schema_for_artifact("E03")
    assert hpo_schema_name is not None
    hpo_schema = asset_bundle.document(hpo_schema_name)
    hpo_branch = next(
        branch for branch in hpo_schema["oneOf"]
        if branch["properties"]["artifact_type"].get("const") == "E03"
    )
    hpo_content_rule = hpo_branch["properties"]["content"]
    hpo_spec = valid_artifact(
        "E03", "bool-is-not-integer", content=_owner_schema_example(
            hpo_content_rule, hpo_schema, label="e03",
        ),
    )
    hpo_spec["content"]["space"] = {
        "parameters": {
            "layers": {"type": "integer", "values": [True, 1]},
        },
    }
    hpo_spec["content_digest"] = digest(hpo_spec["content"])
    assert "hpo_space_invalid" in {
        item["code"]
        for item in asset_bundle.validate_artifact("E03", hpo_spec)
    }

    run_plan = valid_artifact(
        "E04", "non-boolean-calibration", content={
            "run_plan_id": "run-plan-1",
            "experiment_id": "experiment-1",
            "candidate_spec_digest": digest({"candidate": 1}),
            "channel": "exploratory",
            "runs": [{
                "run_id": "run-1",
                "required_attempt_ids": ["attempt-1"],
                "data_digest": digest({"data": 1}),
                "split_digest": digest({"split": 1}),
                "evaluator_digest": digest({"evaluator": 1}),
                "dataset_manifest_digest": digest({"dataset_manifest": 1}),
                "split_manifest_digest": digest({"split_manifest": 1}),
                "expected_runtime_image_digest": digest({"image": 1}),
                "expected_runtime_profile": {},
                "calibration_applicable": 1,
                "environment_requirements": {"gpu": "required"},
                "seed": "seed-1",
                "budget": {"gpu_hours": 1},
            }],
            "attempt_policy": {"maximum_attempts": 1},
            "evidence_requirements": ["locked-evaluation"],
            "source_refs": {"E01:experiment-1": digest({"E01": 1})},
            "author_principal_ids": ["research-owner"],
        },
    )
    assert "run_plan_calibration_applicability_invalid" in {
        item["code"]
        for item in asset_bundle.validate_artifact("E04", run_plan)
    }

    deviation = valid_artifact(
        "E07", "null-observed-fact", content={
            "deviation_id": "deviation-1",
            "experiment_id": "experiment-1",
            "first_affected_event": digest({"event": 1}),
            "observed_facts": {"sensor_offset": None},
            "impact": "May affect the governed evaluation slices",
            "authority": [],
        },
    )
    assert "deviation_fact_binding_invalid" in {
        item["code"]
        for item in asset_bundle.validate_artifact("E07", deviation)
    }


@pytest.mark.parametrize(
    ("artifact_id", "required_field"),
    [
        ("G01", "principles"),
        ("B01", "baseline_id"),
        ("E01", "experiment_id"),
        ("E02", "candidate_digest"),
        ("E05", "pipeline_identity"),
        ("D01", "evidence_id"),
        ("D05", "decision_id"),
    ],
)
def test_each_owner_schema_rejects_a_central_valid_persisted_envelope(
    asset_bundle: AssetBundle,
    valid_artifact,
    artifact_id: str,
    required_field: str,
) -> None:
    artifact = valid_artifact(artifact_id, f"owner-{artifact_id.lower()}")
    artifact["content"].pop(required_field)
    artifact["content_digest"] = digest(artifact["content"])
    assert validate_schema(
        artifact,
        asset_bundle.document("schemas/artifact.schema.json"),
        registry=asset_bundle.documents,
    ) == []
    owner_schema = asset_bundle.owner_schema_for_artifact(artifact_id)
    assert owner_schema is not None
    owner_findings = validate_schema(
        artifact,
        asset_bundle.document(owner_schema),
        governing_asset=owner_schema,
        registry=asset_bundle.documents,
    )
    assert any(item["code"] == "schema_composition" for item in owner_findings)
    ingress = asset_bundle.validate_artifact(artifact_id, artifact)
    assert any(
        item["code"] == "schema_composition"
        and item["governing_asset"] == owner_schema
        for item in ingress
    )


OWNER_SCHEMA_ARTIFACT_IDS = (
    "G01", "G02", "G03", "G04", "G05", "G06", "G07", "G08",
    "G09", "G10", "G11", "G12", "G13",
    "B01", "B02", "B03", "B04",
    "E01", "E02", "E03", "E04", "E05", "E06", "E07",
    "D01", "D02", "D03", "D04", "D05",
    "S04", "S05", "S06", "S07", "S08", "S09", "S10",
)

OWNER_CONTENT_OPTIONAL_FIELDS = {
    **{
        artifact_id: {"source_refs"}
        for artifact_id in (
            "G01", "G02", "G03", "G04", "G05", "G06", "G07",
            "G08", "G09", "G10", "G11", "G12", "G13",
        )
    },
    "E01": {
        "change_only", "forbidden_changes", "frozen_variables",
        "hpo_enabled", "run_plan_digest", "run_plan_id", "search",
        "search_spec_instance_key", "source_refs",
    },
    "E04": {"implementation_revision", "preflight_instance_key"},
    "D05": {"supersession_reason"},
}


@pytest.mark.parametrize("artifact_id", OWNER_SCHEMA_ARTIFACT_IDS)
def test_every_owner_schema_rejects_unknown_content_after_digest_recompute(
    asset_bundle: AssetBundle, valid_artifact, artifact_id: str,
) -> None:
    artifact = valid_artifact(
        artifact_id, f"owner-unknown-{artifact_id.lower()}",
    )
    owner_schema_name = asset_bundle.owner_schema_for_artifact(artifact_id)
    assert owner_schema_name is not None
    owner_schema = asset_bundle.document(owner_schema_name)
    branches = [
        branch for branch in owner_schema["oneOf"]
        if branch["properties"]["artifact_type"].get("const") == artifact_id
    ]
    assert len(branches) == 1
    content_rule = branches[0]["properties"]["content"]
    assert content_rule["additionalProperties"] is False
    assert set(content_rule["required"]).issubset(content_rule["properties"])
    assert set(content_rule["properties"]) == (
        set(content_rule["required"])
        | OWNER_CONTENT_OPTIONAL_FIELDS.get(artifact_id, set())
    )
    artifact["content"] = _owner_schema_example(
        content_rule, owner_schema, label=artifact_id.lower(),
    )
    artifact["content_digest"] = digest(artifact["content"])
    assert validate_schema(
        artifact, owner_schema,
        governing_asset=owner_schema_name,
        registry=asset_bundle.documents,
    ) == []

    artifact["content"]["undeclared_owner_content_key"] = "forbidden"
    artifact["content_digest"] = digest(artifact["content"])

    content_findings = validate_schema(
        artifact["content"], content_rule,
        governing_asset=owner_schema_name,
        pointer="/content",
        registry=asset_bundle.documents,
        root_schema=owner_schema,
    )
    assert any(
        item["code"] == "schema_unknown_field"
        and item["json_pointer"]
        == "/content/undeclared_owner_content_key"
        for item in content_findings
    )
    owner_findings = validate_schema(
        artifact, owner_schema,
        governing_asset=owner_schema_name,
        registry=asset_bundle.documents,
    )
    assert any(item["code"] == "schema_composition" for item in owner_findings)
    ingress = asset_bundle.validate_artifact(artifact_id, artifact)
    assert any(
        item["code"] == "schema_composition"
        and item["governing_asset"] == owner_schema_name
        for item in ingress
    )


def test_research_deployment_contract_requires_no_release_state(
    asset_bundle: AssetBundle, valid_artifact,
) -> None:
    content = {
        "target": "production", "interface": {"protocol": "v1"},
        "parity": {"required": True}, "latency": {"max_ms": 100},
        "memory": {"max_mb": 1024}, "health": {"required": True},
        "source_refs": {"S02:research-brief": digest({"research": "brief"})},
    }
    artifact = valid_artifact("G12", "research-target", content=content)
    assert asset_bundle.validate_artifact("G12", artifact) == []

    retired = deepcopy(artifact)
    retired["content"]["rollback"] = {"model_id": "retired-model"}
    retired["content_digest"] = digest(retired["content"])
    assert any(
        item["code"] == "schema_composition"
        for item in asset_bundle.validate_artifact("G12", retired)
    )

    for field, invalid, error_code in (
        ("target", "unknown", "deployment_target_invalid"),
        ("interface", {}, "deployment_interface_invalid"),
        ("parity", {"required": False}, "deployment_parity_not_required"),
        ("health", {"required": False}, "deployment_health_not_required"),
        ("latency", {"max_ms": -1}, "deployment_latency_invalid"),
        ("memory", {"max_mb": -1}, "deployment_memory_invalid"),
    ):
        invalid_artifact = deepcopy(artifact)
        invalid_artifact["content"][field] = invalid
        invalid_artifact["content_digest"] = digest(invalid_artifact["content"])
        assert error_code in {
            item["code"]
            for item in asset_bundle.validate_artifact("G12", invalid_artifact)
        }, field


def test_common_rule_executor_is_not_provenance_only(asset_bundle: AssetBundle, valid_artifact) -> None:
    artifact = valid_artifact("G01", "common-rule")
    artifact["content"]["secret"] = "must-not-persist"
    artifact["content_digest"] = digest(artifact["content"])
    findings = asset_bundle.validate_artifact("G01", artifact)
    assert any(
        item["code"] == "secret_field_forbidden"
        and item["governing_asset"] == "artifact-contracts.json"
        for item in findings
    )


@pytest.mark.parametrize(
    ("collection", "expected_code"),
    [
        ("common_rules", "unknown_common_executor"),
        ("semantic_validators", "unknown_semantic_executor"),
    ],
)
def test_unknown_declared_executor_fails_bundle_load(
    asset_bundle: AssetBundle,
    tmp_path,
    collection: str,
    expected_code: str,
) -> None:
    copied = tmp_path / collection
    _materialize_engine_bundle(asset_bundle, copied)
    contracts_path = copied / "artifact-contracts.json"
    contracts = json.loads(contracts_path.read_text(encoding="utf-8"))
    contracts[collection][0]["executor"] = "contracts.unimplemented_executor"
    contracts_path.write_bytes(canonical_bytes(contracts))
    with pytest.raises(ContractError) as caught:
        AssetBundle.load(copied)
    assert caught.value.code == expected_code


def test_semantic_allow_empty_must_be_a_subset_of_required(
    asset_bundle: AssetBundle, tmp_path,
) -> None:
    copied = tmp_path / "invalid-semantic-allow-empty"
    _materialize_engine_bundle(asset_bundle, copied)
    contracts_path = copied / "artifact-contracts.json"
    contracts = json.loads(contracts_path.read_text(encoding="utf-8"))
    validator = next(
        row for row in contracts["semantic_validators"]
        if row["id"] == "artifact.baseline-deprecation"
    )
    validator["allow_empty"] = ["not-required"]
    contracts_path.write_bytes(canonical_bytes(contracts))
    with pytest.raises(ContractError) as caught:
        AssetBundle.load(copied)
    assert caught.value.code == "semantic_required_contract_invalid"


@pytest.mark.parametrize(
    ("validator_id", "field", "replacement", "expected_code"),
    [
        (
            "artifact.governance-principles", "required", "principles",
            "semantic_required_contract_invalid",
        ),
        (
            "artifact.governance-principles", "required", [""],
            "semantic_required_contract_invalid",
        ),
        (
            "artifact.governance-principles", "required",
            ["principles", "principles"],
            "semantic_required_contract_invalid",
        ),
        (
            "artifact.baseline-deprecation", "allow_empty",
            "active_dependents", "semantic_required_contract_invalid",
        ),
        (
            "artifact.baseline-deprecation", "allow_empty", [""],
            "semantic_required_contract_invalid",
        ),
        (
            "artifact.baseline-deprecation", "allow_empty",
            ["active_dependents", "active_dependents"],
            "semantic_required_contract_invalid",
        ),
        (
            "artifact.contract-bundle", "required_ids", "G03",
            "semantic_required_ids_contract_invalid",
        ),
        (
            "artifact.contract-bundle", "required_ids", [""],
            "semantic_required_ids_contract_invalid",
        ),
        (
            "artifact.contract-bundle", "required_ids", ["G03", "G03"],
            "semantic_required_ids_contract_invalid",
        ),
        (
            "artifact.contract-bundle", "required_ids", ["G030"],
            "semantic_required_ids_contract_invalid",
        ),
        (
            "artifact.traceability-graph", "required_edges", "edge",
            "semantic_required_edges_contract_invalid",
        ),
        (
            "artifact.traceability-graph", "required_edges", [""],
            "semantic_required_edges_contract_invalid",
        ),
        (
            "artifact.traceability-graph", "required_edges",
            ["requirement-to-bmad", "requirement-to-bmad"],
            "semantic_required_edges_contract_invalid",
        ),
    ],
)
def test_declarative_semantic_fields_fail_closed_at_bundle_load(
    asset_bundle: AssetBundle,
    tmp_path,
    validator_id: str,
    field: str,
    replacement,
    expected_code: str,
) -> None:
    copied = tmp_path / f"invalid-{field}-{validator_id}"
    _materialize_engine_bundle(asset_bundle, copied)
    contracts_path = copied / "artifact-contracts.json"
    contracts = json.loads(contracts_path.read_text(encoding="utf-8"))
    validator = next(
        row for row in contracts["semantic_validators"]
        if row["id"] == validator_id
    )
    validator[field] = replacement
    contracts_path.write_bytes(canonical_bytes(contracts))
    with pytest.raises(ContractError) as caught:
        AssetBundle.load(copied)
    assert caught.value.code == expected_code


def test_required_ids_use_exact_reference_set_closure(
    asset_bundle: AssetBundle, valid_artifact,
) -> None:
    artifact = valid_artifact("G02", "exact-reference-closure")
    required = artifact["content"]["references"]
    artifact["content"]["references"] = [
        f"{artifact_id}:contract-{index}"
        for index, artifact_id in enumerate(required, start=1)
    ]
    artifact["content"]["source_refs"] = {
        reference: digest({"source_reference": reference})
        for reference in artifact["content"]["references"]
    }
    artifact["content_digest"] = digest(artifact["content"])
    assert asset_bundle.validate_artifact("G02", artifact) == []

    forged = json.loads(json.dumps(artifact))
    forged["content"]["references"][0] = (
        "forged-" + required[0] + "-substring"
    )
    forged["content_digest"] = digest(forged["content"])
    findings = asset_bundle.validate_artifact("G02", forged)
    assert "reference_id_invalid" in {
        item["code"] for item in findings
    }
    assert any(
        item["code"] == "reference_closure"
        and required[0] in item["message"]
        for item in findings
    )


@pytest.mark.parametrize(
    "malformed",
    [
        "G03",
        {"artifact_id": "G03"},
        [{"artifact_id": "G03"}],
        [1],
        [""],
    ],
)
def test_required_ids_reject_malformed_reference_shapes(
    asset_bundle: AssetBundle, valid_artifact, malformed,
) -> None:
    artifact = valid_artifact("G02", "malformed-reference-shape")
    artifact["content"]["references"] = malformed
    artifact["content_digest"] = digest(artifact["content"])
    findings = asset_bundle.validate_artifact("G02", artifact)
    assert "reference_shape_invalid" in {
        item["code"] for item in findings
    }


@pytest.mark.parametrize(
    ("duplicate", "expected_code"),
    [
        ("same-token", "reference_duplicate"),
        ("same-artifact", "reference_artifact_duplicate"),
    ],
)
def test_required_ids_reject_duplicate_reference_identity(
    asset_bundle: AssetBundle,
    valid_artifact,
    duplicate: str,
    expected_code: str,
) -> None:
    artifact = valid_artifact("G02", "duplicate-reference")
    first = artifact["content"]["references"][0]
    if duplicate == "same-token":
        artifact["content"]["references"].append(first)
    else:
        artifact["content"]["references"][0] = f"{first}:instance-one"
        artifact["content"]["references"].append(
            f"{first}:instance-two"
        )
    artifact["content_digest"] = digest(artifact["content"])
    findings = asset_bundle.validate_artifact("G02", artifact)
    assert expected_code in {item["code"] for item in findings}


def test_each_v1_owner_schema_binding_is_mandatory_and_irreplaceable(
    asset_bundle: AssetBundle, tmp_path,
) -> None:
    catalogs = (
        "artifact-catalog.json", "supporting-artifact-catalog.json",
    )
    for catalog_name in catalogs:
        original = asset_bundle.document(catalog_name)[
            "owned_schema_bindings"
        ]
        for index, binding in enumerate(original):
            for mutation in ("delete", "replace"):
                copied = (
                    tmp_path
                    / f"{catalog_name}-{index}-{mutation}"
                )
                _materialize_engine_bundle(asset_bundle, copied)
                catalog_path = copied / catalog_name
                catalog = json.loads(
                    catalog_path.read_text(encoding="utf-8")
                )
                if mutation == "delete":
                    del catalog["owned_schema_bindings"][index]
                else:
                    changed = catalog["owned_schema_bindings"][index]
                    owner = changed["owner_skill"]
                    changed["path"] = (
                        f"{owner}/assets/replaced-{index}.schema.json"
                    )
                catalog_path.write_bytes(canonical_bytes(catalog))
                with pytest.raises(ContractError) as caught:
                    AssetBundle.load(copied)
                assert caught.value.code == "owned_schema_v1_mismatch", (
                    catalog_name, index, binding["path"], mutation,
                    caught.value.code,
                )


def test_each_v1_owner_schema_binding_rejects_coverage_shrink(
    asset_bundle: AssetBundle, tmp_path,
) -> None:
    for catalog_name in (
        "artifact-catalog.json", "supporting-artifact-catalog.json",
    ):
        original = asset_bundle.document(catalog_name)[
            "owned_schema_bindings"
        ]
        for index, binding in enumerate(original):
            copied = tmp_path / f"{catalog_name}-{index}-shrink"
            _materialize_engine_bundle(asset_bundle, copied)
            catalog_path = copied / catalog_name
            catalog = json.loads(
                catalog_path.read_text(encoding="utf-8")
            )
            catalog["owned_schema_bindings"][index]["artifact_ids"].pop()
            catalog_path.write_bytes(canonical_bytes(catalog))
            with pytest.raises(ContractError) as caught:
                AssetBundle.load(copied)
            assert caught.value.code in {
                "owned_schema_artifact_coverage_invalid",
                "owned_schema_v1_mismatch",
            }, (
                catalog_name, index, binding["path"], caught.value.code,
            )


def test_pinned_owner_schema_bytes_and_binding_digest_are_authenticated(
    asset_bundle: AssetBundle,
    tmp_path,
) -> None:
    pinned = tmp_path / "pinned"
    _materialize_engine_bundle(asset_bundle, pinned)
    assert AssetBundle.from_pinned(
        pinned, asset_bundle.engine_contract_digest,
    ).engine_contract_digest == asset_bundle.engine_contract_digest

    relative = "sdm-create-ml-contract/assets/contract-record.schema.json"
    owner_path = pinned / relative
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    owner["title"] = "tampered owner contract"
    owner_path.write_bytes(canonical_bytes(owner))
    with pytest.raises(ContractError) as stale_binding:
        AssetBundle.from_pinned(pinned, asset_bundle.engine_contract_digest)
    assert stale_binding.value.code == "owned_schema_digest_mismatch"

    catalog_path = pinned / "artifact-catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["owned_schema_bindings"][0]["digest"] = digest(owner)
    catalog_path.write_bytes(canonical_bytes(catalog))
    with pytest.raises(ContractError) as changed_bundle:
        AssetBundle.from_pinned(pinned, asset_bundle.engine_contract_digest)
    assert changed_bundle.value.code == "engine_digest_mismatch"


def test_unexecuted_semantic_validator_rejected(asset_bundle: AssetBundle, tmp_path) -> None:
    copied = tmp_path / "assets"
    _materialize_engine_bundle(asset_bundle, copied)
    catalog_path = copied / "artifact-catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["artifacts"][0]["semantic_validator"] = "documentation.only"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(ContractError) as caught:
        AssetBundle.load(copied)
    assert caught.value.code == "unknown_semantic_validator"

    generic = tmp_path / "generic-semantic"
    _materialize_engine_bundle(asset_bundle, generic)
    executor = "contracts.validate_governance_principles"
    original = contracts_module.SEMANTIC_EXECUTORS[executor]
    try:
        contracts_module.SEMANTIC_EXECUTORS[executor] = \
            contracts_module._semantic_generic
        with pytest.raises(ContractError) as generic_caught:
            AssetBundle.load(generic)
        assert generic_caught.value.code == \
            "generic_semantic_executor_forbidden"
    finally:
        contracts_module.SEMANTIC_EXECUTORS[executor] = original


def test_allowlist_transition_drift_rejected(asset_bundle: AssetBundle, tmp_path) -> None:
    with pytest.raises(ContractError) as operation:
        asset_bundle.operation_contract("git.unreviewed-side-effect")
    assert operation.value.code == "operation_not_allowed"
    with pytest.raises(ContractError) as transition:
        asset_bundle.transition("deployment.unreviewed")
    assert transition.value.code == "unknown_transition"

    copied = tmp_path / "assets"
    _materialize_engine_bundle(asset_bundle, copied)
    allowlist_path = copied / "platform-operations.json"
    allowlist = json.loads(allowlist_path.read_text(encoding="utf-8"))
    allowlist["domains"][1]["operations"].append({
        "name": "git.unreviewed-side-effect",
        "required_evidence": ["terminal_status"],
        "evidence_cardinality": {"default": {"min": 1, "max": 1}},
        "evidence_value_profiles": {
            "terminal_success": ["terminal_status"],
        },
        "subject_requirements": {
            "default": {"required": ["artifact_id"], "required_any": []},
        },
    })
    allowlist_path.write_bytes(canonical_bytes(allowlist))
    with pytest.raises(ContractError) as pinned:
        AssetBundle.from_pinned(copied, asset_bundle.engine_contract_digest)
    assert pinned.value.code == "engine_digest_mismatch"


def test_platform_operation_contracts_declare_executable_request_constraints(
    asset_bundle: AssetBundle,
) -> None:
    operations = [
        operation
        for domain in asset_bundle.document("platform-operations.json")["domains"]
        for operation in domain["operations"]
    ]
    # Operation contracts evolve independently of the retained 34 task nodes.
    # Exercise every declared operation rather than an obsolete fixed count.
    assert {'ml.diagnostic-execution','ml.normal-execution'} <= {row['name'] for row in operations}
    for operation in operations:
        assert operation["evidence_cardinality"] == {
            "default": {"min": 1, "max": 1},
        }
        assigned = [
            evidence_name
            for names in operation["evidence_value_profiles"].values()
            for evidence_name in names
        ]
        assert len(assigned) == len(set(assigned))
        assert set(assigned) == set(operation["required_evidence"])
        requirements = operation["subject_requirements"]
        rules = [
            *([requirements["default"]] if "default" in requirements else []),
            *requirements.get("by_step", {}).values(),
        ]
        assert rules
        assert all(set(rule) == {"required", "required_any"} for rule in rules)
        assert all(rule["required"] or rule["required_any"] for rule in rules)

    _, hpo, _ = asset_bundle.operation_contract("ml.hpo-search")
    _, execution, _ = asset_bundle.operation_contract(
        "jenkins.execution-orchestration",
    )
    _, training, _ = asset_bundle.operation_contract("ml.training")
    _, evaluation, _ = asset_bundle.operation_contract(
        "jenkins.evaluation-orchestration",
    )
    _, locked, _ = asset_bundle.operation_contract("ml.locked-evaluation")
    _, robustness, _ = asset_bundle.operation_contract(
        "ml.robustness-evaluation",
    )
    execution_keys = ["experiment_id", "run_id", "attempt_id"]
    run_binding_keys = [
        *execution_keys, "run_manifest_digest",
        "expected_runtime_image_digest", "expected_runtime_profile_digest",
    ]
    evaluation_binding_keys = [
        *run_binding_keys, "selected_checkpoint_attempt_id",
        "checkpoint_digest", "model_artifact_digest", "training_r07_digest",
        "training_r08_digest",
    ]
    assert hpo["subject_requirements"]["by_step"]["15"]["required"] == \
        [
            *execution_keys, "search_spec_digest",
            "trial_run_manifest_set_digest",
        ]
    assert execution["subject_requirements"]["by_step"]["15"]["required"] == \
        run_binding_keys
    assert training["subject_requirements"]["by_step"]["15"]["required"] == \
        run_binding_keys
    assert evaluation["subject_requirements"]["by_step"]["16"]["required"] == \
        evaluation_binding_keys
    assert locked["subject_requirements"]["by_step"]["16"]["required"] == \
        evaluation_binding_keys
    assert robustness["subject_requirements"]["by_step"]["17"]["required"] == \
        execution_keys
    for contract in (hpo, execution, training, evaluation, locked, robustness):
        assert list(contract["evidence_value_schemas"]) == \
            contract["required_evidence"]
        assert all(
            isinstance(schema, dict) and schema
            for schema in contract["evidence_value_schemas"].values()
        )


def test_adapter_contract_digest_binds_materialized_evidence_value_schemas(
    asset_bundle: AssetBundle, tmp_path,
) -> None:
    _, original, _ = asset_bundle.operation_contract("data.quality-check")
    copied = tmp_path / "changed-evidence-schema"
    _materialize_engine_bundle(asset_bundle, copied)
    path = copied / "platform-operations.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["evidence_schema_profiles"]["identity"]["maxLength"] = 511
    path.write_bytes(canonical_bytes(document))
    changed_bundle = AssetBundle.load(copied)
    _, changed, _ = changed_bundle.operation_contract("data.quality-check")

    assert digest(changed) != digest(original)
    assert changed["evidence_value_schemas"]["dataset_identity"][
        "maxLength"
    ] == 511


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown-field",
        "missing-cardinality",
        "zero-cardinality",
        "missing-subject-requirements",
        "empty-subject-rule",
        "invalid-step",
        "missing-evidence-schema",
        "duplicate-evidence-schema",
        "unused-evidence-schema",
        "external-evidence-schema",
        "open-evidence-schema",
    ],
)
def test_malformed_platform_operation_contract_fails_bundle_load(
    asset_bundle: AssetBundle, tmp_path, mutation: str,
) -> None:
    copied = tmp_path / mutation
    _materialize_engine_bundle(asset_bundle, copied)
    path = copied / "platform-operations.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    operation = document["domains"][0]["operations"][0]
    if mutation == "unknown-field":
        operation["documentation_only"] = True
    elif mutation == "missing-cardinality":
        operation.pop("evidence_cardinality")
    elif mutation == "zero-cardinality":
        operation["evidence_cardinality"]["default"]["min"] = 0
    elif mutation == "missing-subject-requirements":
        operation.pop("subject_requirements")
    elif mutation == "empty-subject-rule":
        operation["subject_requirements"]["by_step"]["3"] = {
            "required": [], "required_any": [],
        }
    else:
        if mutation == "invalid-step":
            operation["subject_requirements"]["by_step"]["0"] = \
                operation["subject_requirements"]["by_step"].pop("3")
        elif mutation == "missing-evidence-schema":
            profile_name = next(iter(operation["evidence_value_profiles"]))
            operation["evidence_value_profiles"][profile_name].pop()
        elif mutation == "duplicate-evidence-schema":
            profiles = list(operation["evidence_value_profiles"])
            operation["evidence_value_profiles"][profiles[1]].append(
                operation["evidence_value_profiles"][profiles[0]][0],
            )
        elif mutation == "unused-evidence-schema":
            document["evidence_schema_profiles"]["unused_identity"] = {
                "type": "string", "minLength": 1, "maxLength": 512,
            }
        elif mutation == "external-evidence-schema":
            document["evidence_schema_profiles"]["identity"] = {
                "$ref": "finding.schema.json",
            }
        else:
            document["evidence_schema_profiles"]["open_object"] = {
                "type": "object", "additionalProperties": True,
            }
    path.write_bytes(canonical_bytes(document))

    with pytest.raises(ContractError) as caught:
        AssetBundle.load(copied)
    assert caught.value.code in {
        "platform_operation_contract_invalid",
        "platform_evidence_schema_invalid",
        "platform_evidence_schema_open",
    }


def test_duplicate_transition_name_fails_bundle_load(
    asset_bundle: AssetBundle, tmp_path,
) -> None:
    copied = tmp_path / "duplicate-transition-name"
    _materialize_engine_bundle(asset_bundle, copied)
    path = copied / "transition-requirements.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["transitions"][1]["name"] = document["transitions"][0][
        "name"
    ]
    path.write_bytes(canonical_bytes(document))
    with pytest.raises(ContractError) as caught:
        AssetBundle.load(copied)
    assert caught.value.code == "transition_name_duplicate"


def test_duplicate_event_registry_name_fails_bundle_load(
    asset_bundle: AssetBundle, tmp_path,
) -> None:
    copied = tmp_path / "duplicate-event-name"
    _materialize_engine_bundle(asset_bundle, copied)
    path = copied / "event-types.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["event_types"].append(dict(document["event_types"][0]))
    path.write_bytes(canonical_bytes(document))
    with pytest.raises(ContractError) as caught:
        AssetBundle.load(copied)
    assert caught.value.code == "event_type_duplicate"


@pytest.mark.parametrize(
    "mutation",
    ["non-object", "missing-effect", "empty-name", "bad-scope", "unknown-field"],
)
def test_malformed_event_registry_row_fails_bundle_load(
    asset_bundle: AssetBundle, tmp_path, mutation: str,
) -> None:
    copied = tmp_path / f"malformed-event-{mutation}"
    _materialize_engine_bundle(asset_bundle, copied)
    path = copied / "event-types.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "non-object":
        document["event_types"][0] = "workspace.created"
    elif mutation == "missing-effect":
        document["event_types"][0].pop("effect")
    elif mutation == "empty-name":
        document["event_types"][0]["name"] = ""
    elif mutation == "bad-scope":
        document["event_types"][0]["scope"] = "external"
    else:
        document["event_types"][0]["documentation_only"] = True
    path.write_bytes(canonical_bytes(document))
    with pytest.raises(ContractError) as caught:
        AssetBundle.load(copied)
    assert caught.value.code == "event_type_contract_invalid"


@pytest.mark.parametrize(
    "emitted_events",
    [
        "workspace.created",
        [""],
        ["workspace.created", "workspace.created"],
    ],
)
def test_malformed_command_emitted_events_fail_bundle_load(
    asset_bundle: AssetBundle, tmp_path, emitted_events,
) -> None:
    copied = tmp_path / "malformed-command-emitted-events"
    _materialize_engine_bundle(asset_bundle, copied)
    path = copied / "command-contracts.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["commands"][0]["emitted_events"] = emitted_events
    path.write_bytes(canonical_bytes(document))
    with pytest.raises(ContractError) as caught:
        AssetBundle.load(copied)
    assert caught.value.code == "command_emitted_events_invalid"


def test_command_cannot_emit_an_unregistered_event(
    asset_bundle: AssetBundle, tmp_path,
) -> None:
    copied = tmp_path / "unknown-command-event"
    _materialize_engine_bundle(asset_bundle, copied)
    path = copied / "command-contracts.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["commands"][0]["emitted_events"] = ["unknown.event"]
    path.write_bytes(canonical_bytes(document))
    with pytest.raises(ContractError) as caught:
        AssetBundle.load(copied)
    assert caught.value.code == "command_event_unknown"


def test_unreachable_unscoped_event_fails_registry_closure(
    asset_bundle: AssetBundle, tmp_path,
) -> None:
    copied = tmp_path / "unreachable-event"
    _materialize_engine_bundle(asset_bundle, copied)
    path = copied / "event-types.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["event_types"].append({
        "name": "unreachable.event",
        "effect": "must not survive executable registry closure",
    })
    path.write_bytes(canonical_bytes(document))
    with pytest.raises(ContractError) as caught:
        AssetBundle.load(copied)
    assert caught.value.code == "event_registry_closure_mismatch"


def test_internal_control_plane_scope_is_required_for_internal_event(
    asset_bundle: AssetBundle, tmp_path,
) -> None:
    copied = tmp_path / "internal-event-scope-removed"
    _materialize_engine_bundle(asset_bundle, copied)
    path = copied / "event-types.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    internal = next(
        row for row in document["event_types"]
        if row.get("scope") == "internal-control-plane"
    )
    internal.pop("scope")
    path.write_bytes(canonical_bytes(document))
    with pytest.raises(ContractError) as caught:
        AssetBundle.load(copied)
    assert caught.value.code == "event_registry_closure_mismatch"


def test_schema_conditionals_not_and_decimal_bounds_are_executable() -> None:
    schema = {
        "type": "object",
        "properties": {
            "mode": {"type": "string"},
            "value": {"type": "number", "minimum": Decimal("0.1")},
        },
        "required": ["mode", "value"],
        "if": {"properties": {"mode": {"const": "formal"}}},
        "then": {"properties": {"value": {"minimum": Decimal("1.000000000000000001")}}},
        "else": {"properties": {"value": {"maximum": Decimal("0.9")}}},
        "not": {"properties": {"mode": {"const": "forbidden"}}},
    }
    assert validate_schema({"mode": "formal", "value": Decimal("1.000000000000000001")}, schema) == []
    assert {item["code"] for item in validate_schema(
        {"mode": "formal", "value": Decimal("1.000000000000000000")}, schema,
    )} == {"schema_minimum"}
    assert {item["code"] for item in validate_schema(
        {"mode": "forbidden", "value": Decimal("0.5")}, schema,
    )} == {"schema_not"}


@pytest.mark.parametrize("value", [
    "2026-08-26 12:30:00Z",
    "20260826T123000Z",
    "2026-08-26T12:30:00",
    "2026-08-26T12:30:00+0800",
    "2026-02-30T12:30:00Z",
])
def test_date_time_format_is_strict_rfc3339(value: str) -> None:
    findings = validate_schema(value, {"type": "string", "format": "date-time"})
    assert [item["code"] for item in findings] == ["bad_timestamp"]
    assert validate_schema(
        "2026-08-26T12:30:00.123+08:00",
        {"type": "string", "format": "date-time"},
    ) == []


def test_unknown_schema_keyword_is_rejected_recursively(
    asset_bundle: AssetBundle, tmp_path,
) -> None:
    copied = tmp_path / "unknown-schema-keyword"
    _materialize_engine_bundle(asset_bundle, copied)
    path = copied / "schemas" / "finding.schema.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    first_property = next(iter(document["properties"].values()))
    first_property["unimplementedKeyword"] = True
    path.write_bytes(canonical_bytes(document))
    with pytest.raises(ContractError) as caught:
        AssetBundle.load(copied)
    assert caught.value.code == "schema_keyword_unsupported"
