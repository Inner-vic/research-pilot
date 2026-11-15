from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from typing import Any

import handoffs
import pytest
import specctl
from canonical import canonical_bytes, digest, digest_bytes
from contracts import (
    canonical_run_fingerprint, resolved_configuration_trace_digest,
    runtime_environment_digest, runtime_environment_plan_digest,
    runtime_profile_from_manifest,
)
from ledger import replay
from producer_signing import producer_trust_fields, verifier_record
from projection import materialize
from workspace import (
    WorkspaceError, create_workspace, mutate, publish_artifact, resume_workspace,
    snapshot_tree,
)


_BINDINGS = {
    "data_platform_skill": "fake-data-platform",
    "source_control_platform_skill": "fake-source-control-platform",
    "ci_platform_skill": "fake-ci-platform",
    "ml_execution_platform_skill": "fake-ml-execution-platform",
}


def _runtime_environment(
    instance_identity: str, image_digest: str,
) -> dict[str, Any]:
    return {
        "schema": "sdm-runtime-environment/v1",
        "environment_instance_identity": instance_identity,
        "container_image_digest": image_digest,
        "container_image_signature_digest": digest({"signature": "hpo-runtime"}),
        "operating_system": {
            "name": "linux", "version": "24.04", "architecture": "x86_64",
        },
        "driver": {"vendor": "nvidia", "version": "555.42"},
        "cuda": {"toolkit_version": "12.4", "runtime_version": "12.4"},
        "libraries": [{
            "name": "torch", "version": "2.5.0",
            "artifact_digest": digest({"library": "torch-2.5.0"}),
        }],
        "gpu": {
            "vendor": "nvidia", "model": "L4", "count": 1,
            "memory_bytes": 24_000_000_000, "compute_capability": "8.9",
        },
        "cpu": {
            "architecture": "x86_64", "model": "fixture-cpu",
            "logical_cores": 8,
        },
        "kernel": {"name": "linux", "release": "6.8.0"},
        "distributed_topology": {
            "backend": "nccl", "node_count": 1, "world_size": 1,
            "processes_per_node": 1, "interconnect": "pcie",
        },
        "dependency_lock_digest": digest({"lock": "hpo-runtime"}),
        "environment_variables": [{
            "name": "CUBLAS_WORKSPACE_CONFIG",
            "value_digest": digest({"value": ":4096:8"}),
        }],
        "power_mode": "maximum-performance",
    }


def _runtime_plan(
    instance_identity: str, image_digest: str, profile: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    plan = {
        "schema": "sdm-runtime-environment-plan/v1",
        "environment_instance_identity": instance_identity,
        "expected_runtime_image_digest": image_digest,
        "expected_runtime_profile": profile,
        "expected_runtime_profile_digest": digest(profile),
    }
    return plan, runtime_environment_plan_digest(plan)


def _workspace(
    tmp_path: Path, asset_bundle, name: str,
    *, authority_trust: dict[str, Any] | None = None,
) -> Path:
    root = tmp_path / name
    create_workspace(
        root,
        {
            "workspace_id": name,
            "intent": "exercise exact HPO and Attempt accounting",
            "constraints": ["offline"],
            "non_goals": ["production"],
            "protocol": "formal",
            "bindings": _BINDINGS,
            "binding_skill_digests": {
                key: digest({"skill": identity})
                for key, identity in _BINDINGS.items()
            },
            **(authority_trust or producer_trust_fields()),
        },
        expected_revision=0,
        operation_id=f"create-{name}",
        assets_root=asset_bundle.root,
    )
    return root


def _publish(
    workspace: Path,
    artifact_type: str,
    instance_key: str,
    content: dict,
    subjects: dict[str, str],
    *,
    parents: list[str] | None = None,
    status: str = "published",
) -> tuple[dict, str]:
    value = specctl._envelope(
        artifact_type, instance_key, instance_key, content, subjects,
        status=status,
    )
    mutation = publish_artifact(
        workspace,
        artifact_id=artifact_type,
        instance_key=instance_key,
        value=value,
        expected_revision=replay(workspace).revision,
        operation_id=f"publish-{artifact_type.lower()}-{instance_key}",
        parents=parents or [],
    )
    return value, mutation.state["artifacts"][artifact_type][instance_key][
        "digest"
    ]


def _advance_to_step(workspace: Path, target: int) -> None:
    workspace_id = replay(workspace).genesis["workspace_id"]
    for step in range(1, target):
        transition_targets = {
            18: "ready",
            20: "hypothesis_supported",
        }
        event_payload = {
            "step": step,
            "lifecycle_step_completion": True,
            "outputs": [],
            "published_artifacts": [],
        }
        if step in transition_targets:
            event_payload["transition_target"] = transition_targets[step]
        mutate(
            workspace,
            expected_revision=replay(workspace).revision,
            operation_id=f"fixture-complete-step-{step}",
            event_type="lifecycle.advanced",
            subjects={"workspace_id": workspace_id},
            payload=event_payload,
        )
    assert materialize(replay(workspace))["lifecycle_step"] == target


def _advance_to_step_15(workspace: Path) -> None:
    _advance_to_step(workspace, 15)


def _s12_packets(workspace: Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((workspace / "packets" / "S12").glob("*.json"))
    ]


def _request_for_operation(workspace: Path, operation: str) -> dict:
    matches = [
        packet for packet in _s12_packets(workspace)
        if packet["operation"] == operation
    ]
    assert len(matches) == 1, (operation, matches)
    return matches[0]


def _accept_existing_result(
    workspace: Path,
    result_packet,
    operation: str,
    evidence_values: dict,
    index: int,
) -> None:
    replayed = replay(workspace)
    _, operation_contract, _ = replayed.bundles[
        replayed.active_engine_digest
    ].operation_contract(operation)
    request = _request_for_operation(workspace, operation)
    record = materialize(replayed)["handoffs"][request["request_id"]]
    assert record["status"] == "created"
    dispatched = handoffs.mark_dispatched(
        workspace, request_id=request["request_id"],
        dispatch_correlation=f"dispatch-hpo-{index}",
        expected_revision=replayed.revision,
        operation_id=f"dispatch-hpo-request-{index}",
    )
    evidence = [
        {"type": name, "value": evidence_values[name]}
        for name in operation_contract["required_evidence"]
    ]
    result = result_packet(
        request,
        result_id=f"RES-hpo{index:03d}", evidence=evidence,
        dispatch_correlation=f"dispatch-hpo-{index}",
    )
    handoffs.ingest(
        workspace, result, expected_revision=dispatched.revision,
        operation_id=f"ingest-hpo-result-{index}",
    )


def _hpo_workspace(
    tmp_path: Path, asset_bundle,
    *, omit_hpo_trial: bool = False,
    hpo_enabled: bool = True,
    confirmation_retry_attempt: str | None = None,
    negative_case: str | None = None,
) -> tuple[Path, dict[str, str], dict[str, dict]]:
    workspace = _workspace(
        tmp_path, asset_bundle,
        "public-hpo" if negative_case is None else f"public-hpo-{negative_case}",
    )
    workspace_id = replay(workspace).genesis["workspace_id"]
    experiment_id = "experiment"
    confirmation_run = "confirmation-run"
    confirmation_attempt = "confirmation-attempt"
    subjects = {
        "workspace_id": workspace_id,
        "experiment_id": experiment_id,
    }
    _, requirement_digest = _publish(
        workspace, "S01", "hpo-requirement",
        {
            "requirement_id": "hpo-requirement",
            "uri": "jira://HPO-REQ-001",
            "owner": "hpo-product-owner",
            "impact": "Bounded search must improve the governed objective",
            "non_goals": ["unbounded or test-partition search"],
        },
        subjects,
    )
    bmad_links: dict[str, dict[str, str]] = {}
    for field, kind in (
        ("product_brief", "product-brief"),
        ("prd", "prd"),
        ("architecture", "architecture"),
    ):
        bmad_links[field] = {
            "kind": kind,
            "uri": f"bmad://{kind}/hpo-workflow-v1",
            "digest": digest({"kind": kind, "revision": "rev-001"}),
            "revision": "rev-001",
        }
    _publish(
        workspace, "S03", "hpo-planning",
        {
            **bmad_links,
            "source_refs": {"S01:hpo-requirement": requirement_digest},
        },
        subjects, parents=["S01:hpo-requirement"],
    )
    configuration_a = {"learning_rate": "0.01", "depth": 4}
    configuration_b = {"learning_rate": "0.02", "depth": 4}
    selected_configuration = configuration_a
    selected_digest = digest(selected_configuration)
    partition_digests = {
        "hpo-search": digest({"partition": "hpo-search"}),
        "hpo-recheck": digest({"partition": "hpo-recheck"}),
        "hpo-confirmation": digest({"partition": "hpo-confirmation"}),
        "test": digest({"partition": "formal-test"}),
    }
    _, split_manifest_digest = _publish(
        workspace, "R05", "split",
        {
            "split_identity": "split", "version": "v1",
            "instance_key": "split", "immutable_uri": "artifact://split/v1",
            "dataset_content_digest": digest({"dataset": "frozen"}),
            "membership_digest": digest(partition_digests),
            "partition_digests": partition_digests,
            "grouping_key": "scene_id",
            "hidden_access_policy": {"test": "locked-evaluator-only"},
            "approver_principal_ids": ["split-approver"],
            "receipt": {
                "request_id": "REQ-split-manifest",
                "request_digest": digest({"request": "split-manifest"}),
                "result_id": "RES-split-manifest",
                "result_digest": digest({"result": "split-manifest"}),
                "receipt_digest": digest({"receipt": "split-manifest"}),
                "producer_identity": "fake-data-platform",
                "producer_principal_id": "fake-data-platform-principal",
                "producer_attestation_digest": digest({
                    "attestation": "split-manifest",
                }),
            },
        },
        subjects,
    )
    scientific_source_refs = {
        "G02:contracts": digest({"G02": "contracts"}),
        "G03:data": digest({"G03": "data"}),
        "G10:evaluator": digest({"G10": "evaluator"}),
        "B02:baseline": digest({"B02": "baseline"}),
        "R04:dataset": digest({"R04": "dataset"}),
        "R05:split": split_manifest_digest,
    }
    frozen, frozen_digest = _publish(
        workspace, "E01", f"{experiment_id}.frozen",
        {
            "experiment_id": experiment_id,
            "protocol_channel": "formal", "promotable": False,
            "author_principal_ids": ["experiment-author"],
            "hypothesis": "bounded HPO improves the frozen objective",
            "variables": {"independent": "configuration"},
            "change_only": {
                "allowed_code_paths": ["src/models/**"],
                "allowed_config_paths": ["/model/hpo"],
                "required_equal_refs": [{
                    "ref": "G10:evaluator",
                    "digest": scientific_source_refs["G10:evaluator"],
                }],
                "required_diff_paths": ["code:src/models/model.py"],
                "automatically_derived_paths": [],
            },
            "frozen_variables": [{
                "path": "/variables/independent",
                "digest": digest("configuration"),
                "reason": "HPO is the sole independent variable",
            }],
            "forbidden_changes": ["code:src/labels/**"],
            "contracts": {"source_digests": {
                key: value for key, value in scientific_source_refs.items()
                if key.startswith("G")
            }},
            "baseline": {
                "lock_ref": "B02:baseline",
                "lock_digest": scientific_source_refs["B02:baseline"],
                "baseline_digest": digest({"baseline": "model"}),
                "data_digest": digest({"data": "frozen"}),
                "evaluator_digest": scientific_source_refs["G10:evaluator"],
            },
            "data": {
                "dataset_manifest_ref": "R04:dataset",
                "dataset_manifest_digest": scientific_source_refs[
                    "R04:dataset"
                ],
                "dataset_digest": digest({"data": "frozen"}),
                "split_manifest_ref": "R05:split",
                "split_manifest_digest": split_manifest_digest,
                "split_digest": partition_digests["hpo-confirmation"],
                "calibration_applicable": False,
            },
            "evaluator": {
                "contract_ref": "G10:evaluator",
                "contract_digest": scientific_source_refs["G10:evaluator"],
                "evaluator_identity": "frozen-score",
            },
            "budget": {"attempts": 3},
            "decision_rule": {
                "schema": "sdm-scientific-rule/v1",
                "support_predicate": "hpo_improves_frozen_objective",
                "rejection_predicate": "hpo_does_not_improve_objective",
                "inconclusive_rule": "underpowered_or_no_decisive_result",
                "invalid_rule": "protocol_invalid",
            },
            "run_plan_id": "hpo-run-plan",
            "source_refs": scientific_source_refs,
            "search": (
                {"mode": "hpo", "search_spec_instance_key": "search"}
                if hpo_enabled else {"mode": "none"}
            ),
        },
        subjects, parents=["S03:hpo-planning"], status="frozen",
    )
    search_partition_name = (
        "test" if negative_case == "objective-test" else "hpo-search"
    )
    search_partition_digest = partition_digests[search_partition_name]
    expected_runtime_image_digest = digest({"image": "hpo-runtime-v1"})
    expected_runtime_profile = runtime_profile_from_manifest(
        _runtime_environment(
            "hpo-expected-profile", expected_runtime_image_digest,
        ),
    )
    trial_environment_plans = [
        _runtime_plan(
            f"hpo-trial-environment-{number}",
            expected_runtime_image_digest, expected_runtime_profile,
        )
        for number in range(1, 5)
    ]
    confirmation_environment_plan, confirmation_environment_plan_digest = \
        _runtime_plan(
            "hpo-confirmation-environment",
            expected_runtime_image_digest, expected_runtime_profile,
        )
    trial_plan = [
        {
            "phase": "search", "run_id": "trial-run-1",
            "attempt_id": "trial-attempt-1", "configuration": configuration_a,
            "configuration_digest": digest(configuration_a),
            "partition_digest": search_partition_digest,
            "seed": "seed-search-a",
            "environment_plan": trial_environment_plans[0][0],
            "environment_plan_digest": trial_environment_plans[0][1],
        },
        {
            "phase": "search", "run_id": "trial-run-2",
            "attempt_id": "trial-attempt-2", "configuration": configuration_b,
            "configuration_digest": digest(configuration_b),
            "partition_digest": search_partition_digest,
            "seed": "seed-search-b",
            "environment_plan": trial_environment_plans[1][0],
            "environment_plan_digest": trial_environment_plans[1][1],
        },
        {
            "phase": "recheck", "run_id": "trial-run-3",
            "attempt_id": "trial-attempt-3", "configuration": configuration_a,
            "configuration_digest": digest(configuration_a),
            "partition_digest": partition_digests["hpo-recheck"],
            "seed": "seed-recheck-a",
            "environment_plan": trial_environment_plans[2][0],
            "environment_plan_digest": trial_environment_plans[2][1],
            "recheck_of": "trial-attempt-1",
        },
        {
            "phase": "recheck", "run_id": "trial-run-4",
            "attempt_id": "trial-attempt-4", "configuration": configuration_b,
            "configuration_digest": digest(configuration_b),
            "partition_digest": partition_digests["hpo-recheck"],
            "seed": "seed-recheck-b",
            "environment_plan": trial_environment_plans[3][0],
            "environment_plan_digest": trial_environment_plans[3][1],
            "recheck_of": "trial-attempt-2",
        },
    ]
    confirmation_seed = (
        "seed-search-a" if negative_case == "seed-overlap"
        else "seed-confirmation"
    )
    selection_rule = {
        "direction": "maximize", "aggregation": "mean",
        "tie_breaker": {
            "field": "configuration_digest", "direction": "ascending",
        },
    }
    search, search_digest = _publish(
        workspace, "E03", "search",
        {
            "space": {"parameters": {
                "learning_rate": {
                    "type": "categorical", "values": ["0.01", "0.02"],
                },
                "depth": {"type": "integer", "values": [4]},
            }},
            "algorithm": {
                "name": "grid", "deterministic": True,
                "seed": "seed-search-algorithm",
            },
            "budget": {"trial_count": 2, "recheck_count": 2},
            "objective": {
                "metric": "frozen-score", "direction": "maximize",
                "search_partition_name": search_partition_name,
                "search_partition_digest": search_partition_digest,
                "recheck_partition_name": "hpo-recheck",
                "recheck_partition_digest": partition_digests["hpo-recheck"],
                "formal_test_partition_name": "test",
                "formal_test_partition_digest": partition_digests["test"],
            },
            "selection_rule": selection_rule,
            "seed_policy": {
                "trial_seeds_unique": True,
                "recheck_seeds_unique": True,
                "confirmation_seed_distinct": True,
            },
            "trial_plan": trial_plan,
            "confirmation": {
                "run_id": confirmation_run,
                "attempt_id": confirmation_attempt,
                "independent_seed": confirmation_seed,
                "independent_environment_plan": confirmation_environment_plan,
                "independent_environment_plan_digest":
                    confirmation_environment_plan_digest,
                "partition_name": "hpo-confirmation",
                "partition_digest": partition_digests["hpo-confirmation"],
            },
            "split_manifest_ref": "R05:split",
            "split_manifest_digest": split_manifest_digest,
            "run_plan_id": "hpo-run-plan",
        },
        subjects, parents=[f"E01:{experiment_id}.frozen"],
    )
    common_run = {
        "data_digest": digest({"data": "frozen"}),
        "evaluator_digest": scientific_source_refs["G10:evaluator"],
        "dataset_manifest_digest": scientific_source_refs["R04:dataset"],
        "split_manifest_digest": split_manifest_digest,
        "calibration_applicable": False,
        "environment_requirements": {"runtime": "isolated"},
        "expected_runtime_image_digest": expected_runtime_image_digest,
        "expected_runtime_profile": expected_runtime_profile,
        "budget": {"resources": "bounded"},
    }
    run_rows = [
        {
            **common_run,
            "run_id": row["run_id"],
            "required_attempt_ids": [row["attempt_id"]],
            "split_digest": row["partition_digest"],
            "hpo_phase": row["phase"], "seed": row["seed"],
            "environment_plan_digest": row["environment_plan_digest"],
            "configuration_digest": row["configuration_digest"],
        }
        for row in trial_plan
    ] + [{
        **common_run,
        "run_id": confirmation_run,
        "required_attempt_ids": [
            confirmation_attempt,
            *([confirmation_retry_attempt]
              if confirmation_retry_attempt is not None else []),
        ],
        "split_digest": partition_digests["hpo-confirmation"],
        "hpo_phase": "confirmation", "seed": confirmation_seed,
        "environment_plan_digest": confirmation_environment_plan_digest,
    }]
    _, run_plan_digest = _publish(
        workspace, "E04", "hpo-run-plan",
        {
            "run_plan_id": "hpo-run-plan", "experiment_id": experiment_id,
            "candidate_spec_digest": digest({"candidate": experiment_id}),
            "channel": "formal", "runs": run_rows,
            "attempt_policy": {
                "max_attempts": 1, "retry_on": ["failed", "cancelled"],
            },
            "evidence_requirements": ["hpo-search", "confirmation"],
            "source_refs": {
                "E03:search": search_digest,
                "R05:split": split_manifest_digest,
            },
            "author_principal_ids": ["experiment-author"],
        },
        subjects, parents=[f"E03:search", "R05:split"],
    )
    common = {
        "experiment_id": experiment_id,
        "attempt_policy": {
            "max_attempts": 2,
            "retry_on": ["failed", "cancelled", "invalid"],
        },
        "experiment_digest": frozen_digest,
        "frozen_spec_digest": frozen_digest,
        "run_plan_digest": run_plan_digest,
        "data_digest": digest({"data": "frozen"}),
        "evaluator_digest": digest({"evaluator": "locked"}),
        "dataset_manifest_ref": "R04:dataset",
        "dataset_manifest_digest": scientific_source_refs["R04:dataset"],
        "split_manifest_ref": "R05:split",
        "split_manifest_digest": split_manifest_digest,
        "evaluator_contract_ref": "G10:evaluator",
        "evaluator_contract_digest": scientific_source_refs["G10:evaluator"],
        "calibration_applicable": False,
        "environment_requirements": {"runtime": "isolated"},
        "budget": {"resources": "bounded"},
    }
    engineering_provenance = {
        "schema": "sdm-engineering-provenance/v1",
        "repository_identity": "repo://hpo-fixture",
        "change_request_identity": "change-request-hpo",
        "change_revision": "change-revision-hpo",
        "change_tree_digest": digest({"tree": "change"}),
        "frozen_experiment_digest": frozen_digest,
        "diff_evidence_digest": digest({"diff": "hpo"}),
        "pipeline_identity": "pipeline-hpo",
        "pipeline_definition_digest": digest({"pipeline": "hpo"}),
        "merged_revision": "merged-revision-hpo",
        "merged_tree_digest": digest({"tree": "merged"}),
        "source_results": {
            operation: {
                "result_digest": digest({"operation": operation}),
                "producer_principal_id": "fixture-platform-principal",
            }
            for operation in (
                "git.branch-change-request", "git.change-review-status",
                "jenkins.implementation-checks", "git.merge-status",
                "jenkins.deterministic-acceptance",
                "jenkins.post-implementation-preflight",
            )
        },
    }
    manifest_digests: dict[str, str] = {}
    resolved_config_digests: dict[str, str] = {}
    resolved_value_digests: dict[str, str] = {}
    for run_row in run_rows:
        run_id = run_row["run_id"]
        attempt_id = run_row["required_attempt_ids"][0]
        planned_trial = next(
            (row for row in trial_plan if row["run_id"] == run_id), None,
        )
        selected_parameters = (
            planned_trial["configuration"]
            if planned_trial is not None else configuration_a
        )
        resolved_values = {
            "model": {"depth": selected_parameters["depth"]},
            "optimizer": {
                "learning_rate": selected_parameters["learning_rate"],
            },
            "runtime": {"deterministic": True},
        }
        resolved_content = {
            "run_id": run_id, "attempt_id": attempt_id,
            "source_run_plan_id": "hpo-run-plan",
            "resolved_values": resolved_values,
            "defaults": [], "override_origins": [],
            "structured_config_schema_digest": digest({
                "schema": "hpo-resolved-configuration-v1",
            }),
            "unknown_keys": [], "forbidden_overrides": [],
            "config_digest": digest(resolved_values),
            "frozen_sources": {
                "experiment": frozen_digest, "search": search_digest,
                "run_plan": run_plan_digest,
            },
        }
        resolved_content["resolution_trace_digest"] = \
            resolved_configuration_trace_digest(resolved_content)
        resolved_value_digests[run_id] = resolved_content["config_digest"]
        _, resolved_config_digests[run_id] = _publish(
            workspace, "R02", run_id, resolved_content,
            {**subjects, "run_id": run_id},
            parents=[f"E01:{experiment_id}.frozen", "E03:search"],
        )
        fingerprint_bindings = {
            "run_id": run_id, "attempt_id": attempt_id,
            "frozen_experiment_ref": f"E01:{experiment_id}.frozen",
            "frozen_experiment_digest": frozen_digest,
            "run_plan_ref": "E04:hpo-run-plan",
            "run_plan_digest": run_plan_digest,
            **{
                name: engineering_provenance[name]
                for name in (
                    "repository_identity", "change_request_identity",
                    "change_revision", "change_tree_digest",
                    "frozen_experiment_digest", "diff_evidence_digest",
                    "pipeline_identity", "pipeline_definition_digest",
                    "merged_revision", "merged_tree_digest",
                )
            },
            "baseline_lock_ref": "B02:baseline",
            "baseline_lock_digest": scientific_source_refs["B02:baseline"],
            "resolved_configuration_ref": f"R02:{run_id}",
            "resolved_configuration_digest": resolved_config_digests[run_id],
            "config_digest": resolved_content["config_digest"],
            "dataset_manifest_ref": "R04:dataset",
            "dataset_manifest_digest": scientific_source_refs["R04:dataset"],
            "data_digest": common["data_digest"],
            "split_manifest_ref": "R05:split",
            "split_manifest_digest": split_manifest_digest,
            "split_digest": run_row["split_digest"],
            "evaluator_contract_ref": "G10:evaluator",
            "evaluator_contract_digest": scientific_source_refs["G10:evaluator"],
            "evaluator_digest": common["evaluator_digest"],
            "calibration_applicable": False,
            "expected_runtime_image_digest": expected_runtime_image_digest,
            "expected_runtime_profile": expected_runtime_profile,
            "expected_runtime_profile_digest": digest(expected_runtime_profile),
            "seed": run_row["seed"], "seed_digest": digest(run_row["seed"]),
            "budget": common["budget"], "budget_digest": digest(common["budget"]),
            "allowed_diff": {"hpo": "frozen"},
            "allowed_diff_digest": digest({"hpo": "frozen"}),
        }
        run_fingerprint, run_fingerprint_digest = canonical_run_fingerprint(
            fingerprint_bindings,
        )
        _, manifest_digests[run_id] = _publish(
            workspace, "R01", run_id,
            {
                **fingerprint_bindings,
                "experiment_id": experiment_id,
                "frozen_spec_digest": frozen_digest,
                "required_attempt_ids": run_row["required_attempt_ids"],
                "attempt_policy": common["attempt_policy"],
                "experiment_digest": frozen_digest,
                "engineering_provenance": engineering_provenance,
                "environment_requirements": common[
                    "environment_requirements"
                ],
                "run_fingerprint": run_fingerprint,
                "run_fingerprint_digest": run_fingerprint_digest,
            },
            {**subjects, "run_id": run_id},
            parents=[
                f"E01:{experiment_id}.frozen", "E03:search",
                f"R02:{run_id}",
            ],
        )
    del frozen, search
    _advance_to_step_15(workspace)
    command_subjects = {
        "experiment_id": experiment_id, "run_id": confirmation_run,
        "attempt_id": confirmation_attempt,
    }
    tie = negative_case == "tie-break"
    terminals = (
        ["successful"] * 4
        if tie else ["successful", "successful", "successful", "failed"]
    )
    objective_values = [0.8] * 4 if tie else [0.9, 0.7, 0.8, 0.6]
    trial_records = []
    for number, (planned, terminal, objective_value) in enumerate(
        zip(trial_plan, terminals, objective_values, strict=True), start=1,
    ):
        configuration = planned["configuration"]
        if negative_case == "config-substitution" and number == 1:
            configuration = configuration_b
        trial_environment_manifest = _runtime_environment(
            planned["environment_plan"]["environment_instance_identity"],
            expected_runtime_image_digest,
        )
        if number == 1 and negative_case == "environment-missing-field":
            trial_environment_manifest.pop("gpu")
        if number == 1 and negative_case == "environment-unknown-field":
            trial_environment_manifest["unexpected"] = "forbidden"
        if number == 1 and negative_case == "environment-profile-substitution":
            trial_environment_manifest["gpu"]["model"] = "A100"
        if number == 1 and negative_case == "environment-instance-substitution":
            trial_environment_manifest["environment_instance_identity"] = \
                "substituted-trial-environment"
        trial_environment_digest = (
            digest({"forged": "trial-environment"})
            if number == 1 and negative_case == "environment-digest-forgery"
            else runtime_environment_digest(trial_environment_manifest)
            if negative_case not in {
                "environment-missing-field", "environment-unknown-field",
            } or number != 1
            else digest({"invalid": "trial-environment"})
        )
        trial_records.append({
            "run_id": planned["run_id"],
            "attempt_id": planned["attempt_id"],
            "run_manifest_digest": manifest_digests[planned["run_id"]],
            "frozen_spec_digest": frozen_digest,
            "data_digest": common["data_digest"],
            "split_digest": planned["partition_digest"],
            "environment_plan": planned["environment_plan"],
            "environment_plan_digest": planned[
                "environment_plan_digest"
            ],
            "environment_manifest": trial_environment_manifest,
            "environment_digest": trial_environment_digest,
            "phase": planned["phase"],
            "configuration": configuration,
            "configuration_digest": planned["configuration_digest"],
            "objective_metric": "frozen-score",
            "objective_value": objective_value,
            "partition_digest": planned["partition_digest"],
            "seed": planned["seed"],
            "terminal_status": terminal,
            "validity": "valid",
            "system_failure": terminal == "failed",
            "started_at": f"2026-01-02T03:0{number}:00Z",
            "completed_at": f"2026-01-02T03:1{number}:00Z",
            "resource_use": {"gpu_seconds": number},
            "artifacts": [{
                "type": "checkpoint",
                "uri": f"artifact://checkpoint/hpo-trial-{number}",
                "digest": digest({"trial": number}),
            }],
            "artifact_completeness": True,
        })
    if negative_case == "wrong-winner":
        selected_configuration = configuration_b
        selected_digest = digest(configuration_b)
        selected_objective_value = 0.7
    elif tie:
        # The frozen ascending-digest tie-break selects B; the platform's A
        # claim must be rejected by the coordinator recomputation.
        selected_configuration = configuration_a
        selected_digest = digest(configuration_a)
        selected_objective_value = 0.8
    else:
        selected_configuration = configuration_a
        selected_digest = digest(configuration_a)
        selected_objective_value = 0.85
    selection_rule_digest = digest(selection_rule)
    trial_run_manifest_set_digest = digest({
        "schema": "sdm-hpo-trial-run-manifest-set/v1",
        "items": sorted(
            ({
                "run_id": row["run_id"],
                "attempt_id": row["attempt_id"],
                "run_manifest_digest": manifest_digests[row["run_id"]],
            } for row in trial_plan),
            key=lambda row: (row["run_id"], row["attempt_id"]),
        ),
    })
    evidence_by_operation = {
        "ml.hpo-search": {
            "experiment_id": experiment_id,
            "search_spec_digest": search_digest,
            "trial_run_manifest_set_digest": trial_run_manifest_set_digest,
            "every_trial_run_id": [row["run_id"] for row in trial_plan],
            "every_attempt_id": [row["attempt_id"] for row in trial_plan],
            "failed_cancelled_attempts": (
                [] if tie else ["trial-attempt-4"]
            ),
            "trial_records": (
                trial_records[:-1] if omit_hpo_trial else trial_records
            ),
            "selected_configuration": selected_configuration,
            "selected_configuration_digest": selected_digest,
            "selected_objective_value": selected_objective_value,
            "selection_rule_digest": selection_rule_digest,
            "selection_receipt": {
                "selected_configuration_digest": selected_digest,
                "selected_objective_value": selected_objective_value,
                "selection_rule_digest": selection_rule_digest,
            },
            "terminal_status": "successful",
            "resource_use": {"gpu_seconds": 10},
            "artifact_uri": "artifact://hpo/search",
            "artifact_digest": digest({"hpo": "search"}),
        },
        "jenkins.execution-orchestration": {
            "controller_identity": "jenkins", "job_identity": "job-hpo",
            "build_identity": "build-hpo",
            "pipeline_definition_digest": digest({"pipeline": "hpo"}),
            "run_id": confirmation_run, "attempt_id": confirmation_attempt,
            "run_manifest_digest": manifest_digests[confirmation_run],
            "terminal_status": "successful",
            "artifact_receipt_uri": "artifact://jenkins/hpo",
            "artifact_receipt_digest": digest({"jenkins": "hpo"}),
        },
    }
    confirmation_environment_manifest = _runtime_environment(
        confirmation_environment_plan["environment_instance_identity"],
        expected_runtime_image_digest,
    )
    confirmation_environment = runtime_environment_digest(
        confirmation_environment_manifest,
    )
    evidence_by_operation["ml.training"] = {
            "run_id": confirmation_run, "attempt_id": confirmation_attempt,
            "frozen_spec_digest": frozen_digest,
            "run_manifest_digest": manifest_digests[confirmation_run],
            "data_digest": common["data_digest"],
            "split_digest": partition_digests["hpo-confirmation"],
            "environment_manifest": confirmation_environment_manifest,
            "environment_digest": confirmation_environment,
            "checkpoint_uri": "artifact://checkpoint/confirmation",
            "checkpoint_digest": digest({"checkpoint": "confirmation"}),
            "model_artifact_digest": digest({"model": "confirmation"}),
            "started_at": "2026-01-02T04:00:00Z",
            "completed_at": "2026-01-02T04:10:00Z",
            "attempt_terminal_status": "successful",
            "attempt_validity": "valid", "system_failure": False,
            "resolved_config_digest": resolved_value_digests[
                confirmation_run
            ],
            "selected_configuration_digest": selected_digest,
            "selected_configuration_replayed": True,
            "confirmation_seed": confirmation_seed,
            "confirmation_environment_plan_digest":
                confirmation_environment_plan_digest,
            "independent_seed_environment": True,
            "evidence_summary": {"confirmation": True},
            "statistical_analysis": {"confirmation": True},
            "failure_case_index": {"confirmation": True},
            "scientific_facts": {"confirmation": True},
            "artifacts": [
                {
                    "type": "model", "model_id": "model-confirmation",
                    "uri": "artifact://model/confirmation",
                    "digest": digest({"model": "confirmation"}),
                },
                {
                    "type": "checkpoint",
                    "uri": "artifact://checkpoint/confirmation",
                    "digest": digest({"checkpoint": "confirmation"}),
                },
            ],
            "artifact_completeness": True,
            "terminal_status": "successful",
            "resource_use": {"gpu_seconds": 10},
        }
    return workspace, command_subjects, evidence_by_operation


def _invoke_advance(
    workspace: Path, tmp_path: Path, capsys, subjects: dict[str, str],
) -> tuple[int, dict]:
    return _invoke_step_advance(
        workspace, tmp_path, capsys, step=15, subjects=subjects,
    )


def _invoke_step_advance(
    workspace: Path, tmp_path: Path, capsys, *, step: int,
    subjects: dict[str, str],
) -> tuple[int, dict]:
    payload = tmp_path / f"advance-{workspace.name}-step-{step}.json"
    payload.write_text(
        json.dumps({"step": step, "subjects": subjects}), encoding="utf-8",
    )
    code = specctl.main([
        "lifecycle", "advance", "--workspace", str(workspace),
        "--payload", str(payload), "--expected-revision",
        str(replay(workspace).revision), "--operation-id",
        f"advance-{workspace.name}-step-{step}",
    ])
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("step", [8, 20, 21])
def test_generic_advance_never_precreates_requests_for_dedicated_steps(
    tmp_path, capsys, asset_bundle, step: int,
) -> None:
    workspace = _workspace(tmp_path, asset_bundle, f"dedicated-step-{step}")
    _advance_to_step(workspace, step)
    before = snapshot_tree(workspace)
    revision = replay(workspace).revision
    code, output = _invoke_step_advance(
        workspace,
        tmp_path,
        capsys,
        step=step,
        subjects={
            "experiment_id": "experiment",
            "run_id": "run",
            "attempt_id": "attempt",
        },
    )
    assert code == 3 and output["status"] == "blocked", output
    assert output["result"]["error"] == "specialized_executor_required"
    assert replay(workspace).revision == revision
    assert snapshot_tree(workspace) == before
    assert _s12_packets(workspace) == []


def test_public_hpo_retains_exact_trial_attempt_set_and_confirmation(
    tmp_path, capsys, asset_bundle, result_packet,
) -> None:
    workspace, subjects, evidence = _hpo_workspace(
        tmp_path, asset_bundle,
    )

    code, output = _invoke_advance(workspace, tmp_path, capsys, subjects)
    assert code == 3 and output["status"] == "blocked", output
    assert output["result"].get("phase") == "hpo-search", json.dumps(
        output, indent=2,
    )
    packets = _s12_packets(workspace)
    assert [packet["operation"] for packet in packets] == ["ml.hpo-search"]
    assert packets[0]["subjects"] == {
        **subjects,
        "search_spec_digest": evidence["ml.hpo-search"][
            "search_spec_digest"
        ],
        "trial_run_manifest_set_digest": evidence["ml.hpo-search"][
            "trial_run_manifest_set_digest"
        ],
    }
    assert packets[0]["evidence_cardinality"] == {
        "default": {"min": 1, "max": 1},
    }
    before_repeat = snapshot_tree(workspace)
    revision_before_repeat = replay(workspace).revision
    code, output = _invoke_advance(workspace, tmp_path, capsys, subjects)
    assert code == 3 and output["status"] == "blocked", output
    assert replay(workspace).revision == revision_before_repeat
    assert snapshot_tree(workspace) == before_repeat

    _accept_existing_result(
        workspace, result_packet, "ml.hpo-search",
        evidence["ml.hpo-search"], 1,
    )
    code, output = _invoke_advance(workspace, tmp_path, capsys, subjects)
    assert code == 3 and output["status"] == "blocked", output
    assert output["result"].get("phase") == "hpo-confirmation", output

    replayed = replay(workspace)
    state = materialize(replayed)
    assert state["lifecycle_step"] == 15
    assert set(state["artifacts"]["R07"]) == {
        "trial-attempt-1", "trial-attempt-2",
        "trial-attempt-3", "trial-attempt-4",
    }
    assert "confirmation-attempt" not in state["artifacts"]["R07"]
    events = replayed.events
    selection_event = next(
        event for event in events
        if event["event_type"] == "hpo.selection-recorded"
    )
    confirmation_event = next(
        event for event in events
        if event["event_type"] == "confirmation.materialized"
    )
    downstream_created = [
        event for event in events
        if event["event_type"] == "handoff.created"
        and event["payload"]["operation"] in {
            "jenkins.execution-orchestration", "ml.training",
        }
    ]
    assert len(downstream_created) == 2
    assert selection_event["sequence"] < confirmation_event["sequence"]
    assert confirmation_event["sequence"] < min(
        event["sequence"] for event in downstream_created
    )
    expected_subject_keys = {
        "experiment_id", "run_id", "attempt_id",
        "selected_configuration_digest", "search_spec_digest",
        "selection_result_digest", "confirmation_seed",
        "confirmation_environment_plan_digest", "selection_event_hash",
        "confirmation_partition_digest", "formal_test_partition_digest",
        "split_manifest_digest", "confirmation_event_hash",
        "run_manifest_digest", "expected_runtime_image_digest",
        "expected_runtime_profile_digest",
    }
    downstream_packets = [
        packet for packet in _s12_packets(workspace)
        if packet["operation"] != "ml.hpo-search"
    ]
    assert len(downstream_packets) == 2
    assert all(
        packet["evidence_cardinality"]
        == {"default": {"min": 1, "max": 1}}
        for packet in downstream_packets
    )
    assert downstream_packets[0]["subjects"] == downstream_packets[1][
        "subjects"
    ]
    downstream_subjects = downstream_packets[0]["subjects"]
    assert set(downstream_subjects) == expected_subject_keys
    assert downstream_subjects["selection_event_hash"] == \
        selection_event["event_hash"]
    assert downstream_subjects["confirmation_event_hash"] == \
        confirmation_event["event_hash"]
    assert downstream_subjects["selected_configuration_digest"] == \
        evidence["ml.training"]["selected_configuration_digest"]
    assert downstream_subjects["confirmation_seed"] == \
        evidence["ml.training"]["confirmation_seed"]
    assert downstream_subjects["confirmation_environment_plan_digest"] == \
        evidence["ml.training"]["confirmation_environment_plan_digest"]
    assert downstream_subjects["confirmation_partition_digest"] == \
        evidence["ml.training"]["split_digest"]
    assert downstream_subjects["formal_test_partition_digest"] != \
        downstream_subjects["confirmation_partition_digest"]

    before_confirmation_repeat = snapshot_tree(workspace)
    revision_before_confirmation_repeat = replay(workspace).revision
    code, output = _invoke_advance(workspace, tmp_path, capsys, subjects)
    assert code == 3 and output["status"] == "blocked", output
    assert replay(workspace).revision == revision_before_confirmation_repeat
    assert snapshot_tree(workspace) == before_confirmation_repeat
    repeated_kinds = [event["event_type"] for event in replay(workspace).events]
    assert repeated_kinds.count("hpo.trial-recorded") == 4
    assert repeated_kinds.count("hpo.selection-recorded") == 1
    assert repeated_kinds.count("confirmation.materialized") == 1

    _accept_existing_result(
        workspace, result_packet, "jenkins.execution-orchestration",
        evidence["jenkins.execution-orchestration"], 2,
    )
    _accept_existing_result(
        workspace, result_packet, "ml.training",
        evidence["ml.training"], 3,
    )
    code, output = _invoke_advance(workspace, tmp_path, capsys, subjects)
    assert code == 0, output
    state = materialize(replay(workspace))
    assert state["lifecycle_step"] == 16
    assert {
        key for key in state["artifacts"]["R07"]
    } == {
        "trial-attempt-1", "trial-attempt-2", "trial-attempt-3",
        "trial-attempt-4", "confirmation-attempt",
    }
    assert state["attempts"]["trial-attempt-1"]["status"] == "successful"
    assert state["attempts"]["trial-attempt-2"]["status"] == "successful"
    assert state["attempts"]["trial-attempt-3"]["status"] == "successful"
    assert state["attempts"]["trial-attempt-4"]["status"] == "failed"
    assert state["attempts"]["confirmation-attempt"]["status"] == "successful"
    kinds = [event["event_type"] for event in replay(workspace).events]
    assert kinds.count("hpo.trial-recorded") == 4
    assert "hpo.selection-recorded" in kinds
    assert "confirmation.materialized" in kinds


def test_public_hpo_missing_trial_is_blocked_without_derived_writes(
    tmp_path, capsys, asset_bundle, result_packet,
) -> None:
    workspace, subjects, evidence = _hpo_workspace(
        tmp_path, asset_bundle,
        omit_hpo_trial=True,
    )
    code, output = _invoke_advance(workspace, tmp_path, capsys, subjects)
    assert code == 3 and output["result"]["phase"] == "hpo-search", output
    assert [packet["operation"] for packet in _s12_packets(workspace)] == [
        "ml.hpo-search",
    ]
    _accept_existing_result(
        workspace, result_packet, "ml.hpo-search",
        evidence["ml.hpo-search"], 1,
    )
    before = snapshot_tree(workspace)
    revision = replay(workspace).revision
    code, output = _invoke_advance(workspace, tmp_path, capsys, subjects)
    assert code == 3 and output["status"] == "blocked"
    assert output["result"]["error"] == "hpo_trial_set_mismatch"
    assert replay(workspace).revision == revision
    assert snapshot_tree(workspace) == before
    assert [packet["operation"] for packet in _s12_packets(workspace)] == [
        "ml.hpo-search",
    ]
    state = materialize(replay(workspace))
    assert not state.get("artifacts", {}).get("R07")
    derived_kinds = {
        "hpo.trial-recorded", "hpo.selection-recorded",
        "confirmation.materialized",
    }
    assert not derived_kinds.intersection(
        event["event_type"] for event in replay(workspace).events
    )

    # A complete signed platform result is still only a claim.  Recompute the
    # winner from the frozen trial set and reject both an ordinary wrong winner
    # and a winner that violates the deterministic digest tie-break.  Config
    # bytes may not be substituted while retaining the planned digest.
    for index, (negative_case, expected_error) in enumerate((
        ("wrong-winner", "hpo_selection_mismatch"),
        ("tie-break", "hpo_selection_mismatch"),
        ("config-substitution", "hpo_trial_record_binding_mismatch"),
        ("environment-missing-field", "runtime_environment_shape_invalid"),
        ("environment-unknown-field", "runtime_environment_shape_invalid"),
        ("environment-digest-forgery", "runtime_environment_digest_mismatch"),
        (
            "environment-profile-substitution",
            "runtime_environment_profile_mismatch",
        ),
        (
            "environment-instance-substitution",
            "hpo_trial_record_binding_mismatch",
        ),
    ), start=10):
        bad_workspace, bad_subjects, bad_evidence = _hpo_workspace(
            tmp_path, asset_bundle, negative_case=negative_case,
        )
        code, output = _invoke_advance(
            bad_workspace, tmp_path, capsys, bad_subjects,
        )
        assert code == 3 and output["result"]["phase"] == "hpo-search", output
        _accept_existing_result(
            bad_workspace, result_packet, "ml.hpo-search",
            bad_evidence["ml.hpo-search"], index,
        )
        before = snapshot_tree(bad_workspace)
        revision = replay(bad_workspace).revision
        code, output = _invoke_advance(
            bad_workspace, tmp_path, capsys, bad_subjects,
        )
        assert code == 3 and output["status"] == "blocked", output
        assert output["result"]["error"] == expected_error
        assert replay(bad_workspace).revision == revision
        assert snapshot_tree(bad_workspace) == before
        assert not derived_kinds.intersection(
            event["event_type"] for event in replay(bad_workspace).events
        )

    # Formal-test isolation and the confirmation seed policy are derived from
    # frozen E03/R05 digests.  Invalid specifications fail at the production
    # publication boundary, before E03 or an S12 can exist; no platform-supplied
    # boolean can waive either invariant.
    for negative_case, semantic_code in (
        ("objective-test", "hpo_objective_invalid"),
        ("seed-overlap", "hpo_confirmation_isolation_invalid"),
    ):
        with pytest.raises(WorkspaceError) as raised:
            _hpo_workspace(
                tmp_path, asset_bundle, negative_case=negative_case,
            )
        assert raised.value.code == "artifact_invalid"
        assert semantic_code in {
            item["code"] for item in raised.value.findings
        }
        bad_workspace = tmp_path / f"public-hpo-{negative_case}"
        state = materialize(replay(bad_workspace))
        assert not state.get("artifacts", {}).get("E03")
        assert _s12_packets(bad_workspace) == []
        assert not derived_kinds.intersection(
            event["event_type"] for event in replay(bad_workspace).events
        )


def test_public_non_hpo_requests_controller_and_training_together(
    tmp_path, capsys, asset_bundle,
) -> None:
    workspace, subjects, _ = _hpo_workspace(
        tmp_path, asset_bundle, hpo_enabled=False,
    )
    code, output = _invoke_advance(workspace, tmp_path, capsys, subjects)
    assert code == 3 and output["status"] == "blocked", output
    packets = _s12_packets(workspace)
    assert {packet["operation"] for packet in packets} == {
        "jenkins.execution-orchestration", "ml.training",
    }
    packet_subjects = [packet["subjects"] for packet in packets]
    assert packet_subjects[0] == packet_subjects[1]
    assert set(packet_subjects[0]) == {
        "experiment_id", "run_id", "attempt_id", "run_manifest_digest",
        "expected_runtime_image_digest", "expected_runtime_profile_digest",
    }
    assert packet_subjects[0]["experiment_id"] == subjects["experiment_id"]
    assert packet_subjects[0]["run_id"] == "confirmation-run"
    assert packet_subjects[0]["attempt_id"] == "confirmation-attempt"
    assert all(
        packet["evidence_cardinality"]
        == {"default": {"min": 1, "max": 1}}
        for packet in packets
    )
    assert not any(
        event["event_type"] in {
            "hpo.selection-recorded", "confirmation.materialized",
        }
        for event in replay(workspace).events
    )


def test_public_retry_uses_frozen_attempt_policy_and_preserves_predecessor(
    tmp_path, capsys, asset_bundle,
) -> None:
    workspace, _, _ = _hpo_workspace(
        tmp_path, asset_bundle, hpo_enabled=False,
        confirmation_retry_attempt="attempt-retry",
    )
    workspace_id = replay(workspace).genesis["workspace_id"]
    experiment_id, run_id = "experiment", "confirmation-run"
    subjects = {
        "workspace_id": workspace_id, "experiment_id": experiment_id,
        "run_id": run_id, "attempt_id": "confirmation-attempt",
    }
    dispatched = mutate(
        workspace, expected_revision=replay(workspace).revision,
        operation_id="retry-fixture-dispatch", event_type="run.dispatched",
        subjects=subjects, payload={"transition": "run.dispatch"},
    )
    started = mutate(
        workspace, expected_revision=dispatched.revision,
        operation_id="retry-fixture-start", event_type="attempt.started",
        subjects=subjects, payload={"transition": "attempt.start"},
    )
    terminal = mutate(
        workspace, expected_revision=started.revision,
        operation_id="retry-fixture-terminal", event_type="attempt.terminal",
        subjects=subjects,
        payload={
            "transition": "attempt.terminal", "terminal_status": "failed",
            "accounted": True, "validity": "valid", "system_failure": True,
        },
    )
    payload = tmp_path / "retry.json"
    payload.write_text(json.dumps({
        "subjects": {
            "experiment_id": experiment_id, "run_id": run_id,
        },
        "predecessor_attempt_id": "confirmation-attempt",
        "successor_attempt_id": "attempt-retry",
    }), encoding="utf-8")
    code = specctl.main([
        "lifecycle", "retry", "--workspace", str(workspace),
        "--payload", str(payload), "--expected-revision",
        str(terminal.revision), "--operation-id", "retry-failed-attempt",
    ])
    output = json.loads(capsys.readouterr().out)
    assert code == 0, output
    state = materialize(replay(workspace))
    assert state["attempts"]["confirmation-attempt"]["status"] == "failed"
    assert state["attempts"]["attempt-retry"] == {
        "run_id": run_id, "status": "planned",
        "retry_of": "confirmation-attempt",
        "retry_event": state["attempts"]["attempt-retry"]["retry_event"],
    }
    assert state["lifecycle_step"] == 15


def _created_child_handoff(
    workspace: Path, asset_bundle, request_payload, *, uncertain: bool = False,
) -> str:
    operation = "data.quality-check"
    _, operation_contract, binding_key = asset_bundle.operation_contract(
        operation,
    )
    payload = request_payload(
        "REQ-cancel01", operation=operation,
        binding_identity=_BINDINGS[binding_key],
        subjects={"artifact_id": "dataset"},
        expected_evidence=list(operation_contract["required_evidence"]),
    )
    payload.update({
        "binding_skill_digest": materialize(replay(workspace))[
            "binding_skill_digests"
        ][binding_key],
        "adapter_contract_digest": digest(operation_contract),
        "gate": "lifecycle-step-3", "step": 3,
        "portable_paths": [],
    })
    created = handoffs.create(
        workspace, payload, expected_revision=replay(workspace).revision,
        operation_id="create-cancellable-child",
    )
    if uncertain:
        handoffs.mark_dispatched(
            workspace, request_id=created.request_id,
            dispatch_correlation="dispatch-cancel-uncertain",
            coordinator_disposition="dispatch_unknown",
            expected_revision=created.revision,
            operation_id="mark-cancel-child-uncertain",
        )
    return created.request_id


def _cancellation_trust(
    tmp_path: Path,
) -> tuple[
    dict[str, Any], dict[str, tuple[str, Path, str]], dict[str, Any],
]:
    """Create and pin an isolated two-principal cancellation trust root."""

    signers: dict[str, tuple[str, Path, str]] = {}
    trusted_keys = []
    for role, principal in (
        ("product-owner", "cancellation-product-owner"),
        ("research-owner", "cancellation-research-owner"),
    ):
        private_key = tmp_path / f"{role}.private.pem"
        public_key = tmp_path / f"{role}.public.pem"
        subprocess.run(
            [
                "openssl", "genpkey", "-algorithm", "ED25519",
                "-out", str(private_key),
            ],
            check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            [
                "openssl", "pkey", "-in", str(private_key), "-pubout",
                "-out", str(public_key),
            ],
            check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        public_key_pem = public_key.read_text(encoding="ascii")
        key_digest = digest_bytes(public_key_pem.encode("ascii"))
        signers[role] = (principal, private_key, public_key_pem)
        trusted_keys.append({
            "principal_id": principal,
            "key_digest": key_digest,
            "public_key_pem": public_key_pem,
            "roles": [role],
            "valid_from": "2024-01-01T00:00:00Z",
            "valid_until": "2031-01-01T00:00:00Z",
        })

    snapshot = {
        "schema": "sdm-trust-snapshot/v1",
        "verifier": verifier_record(),
        "keys": trusted_keys,
        "revocations": [],
    }
    trust_root = tmp_path / "cancellation-trust-root"
    trust_root.mkdir()
    (trust_root / "trust-snapshot.json").write_bytes(
        canonical_bytes(snapshot),
    )
    trusted_snapshot, trusted_digest, trust_root_identity = \
        specctl._trusted_snapshot_from_root(trust_root)
    assert trusted_snapshot == snapshot
    return ({
        "authority_trust_root": str(trust_root.resolve(strict=True)),
        "authority_trust_root_identity": trust_root_identity,
        "authority_trust_snapshot": trusted_snapshot,
        "authority_trust_snapshot_digest": trusted_digest,
    }, signers, trusted_snapshot)


def _signed_cancellation_authorities(
    tmp_path: Path,
    *, workspace: Path, record: dict[str, Any],
    signers: dict[str, tuple[str, Path, str]],
    trust_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    replayed = replay(workspace)
    governed_subject = {
        "governed_ids": {
            "workspace_id": replayed.genesis["workspace_id"],
            "artifact_id": "workspace-governance",
        },
        "digests": {"S16_candidate_digest": digest(record)},
    }
    attestations = []
    for role in ("product-owner", "research-owner"):
        principal, private_key, public_key_pem = signers[role]
        subject = {
            "action": "lifecycle.cancel",
            "role": role,
            "principal_id": principal,
            "key_digest": digest_bytes(public_key_pem.encode("ascii")),
            "issued_at": "2025-01-01T00:00:00Z",
            "expires_at": "2030-01-01T00:00:00Z",
            "nonce": f"cancel-{role}-authority",
            "expected_revision": replayed.revision,
            **governed_subject,
        }
        subject_path = tmp_path / f"cancel-{role}.subject.json"
        subject_path.write_bytes(canonical_bytes(subject))
        signed = subprocess.run(
            [
                "openssl", "pkeyutl", "-sign", "-inkey",
                str(private_key), "-rawin", "-in", str(subject_path),
            ],
            check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        attestations.append({
            "schema": "sdm-authority-attestation/v1",
            "subject": subject,
            "signature": base64.b64encode(signed.stdout).decode(
                "ascii",
            ).rstrip("="),
            "public_key_pem": public_key_pem,
            "trust_snapshot": trust_snapshot,
            "trust_snapshot_digest": digest(trust_snapshot),
        })
    return attestations


def _cancel_payload(
    workspace: Path, request_id: str, tmp_path: Path,
    *, signers: dict[str, tuple[str, Path, str]],
    trust_snapshot: dict[str, Any],
) -> Path:
    workspace_id = replay(workspace).genesis["workspace_id"]
    subjects = {"artifact_id": "workspace-governance"}
    envelope_subjects = {"workspace_id": workspace_id, **subjects}
    record = specctl._envelope(
        "S16", "workspace.cancellation", "workspace.cancellation",
        {
            "cancellation_id": "workspace-cancellation",
            "scope": "workspace", "reason": "authorized stop",
            "subjects": envelope_subjects,
            "authority": [],
            "child_dispositions": {request_id: "cancelled"},
        },
        envelope_subjects,
    )
    attestations = _signed_cancellation_authorities(
        tmp_path, workspace=workspace, record=record, signers=signers,
        trust_snapshot=trust_snapshot,
    )
    path = tmp_path / f"cancel-{workspace.name}.json"
    path.write_text(json.dumps({
        "record": record, "subjects": subjects,
        "attestations": attestations,
    }), encoding="utf-8")
    return path


def _invoke_cancel(
    workspace: Path, payload: Path, capsys,
) -> tuple[int, dict]:
    code = specctl.main([
        "lifecycle", "cancel", "--workspace", str(workspace),
        "--payload", str(payload), "--expected-revision",
        str(replay(workspace).revision), "--operation-id",
        f"cancel-{workspace.name}",
    ])
    return code, json.loads(capsys.readouterr().out)


def test_public_cancel_atomically_disposes_live_child_and_resume_routes_archive(
    tmp_path, capsys, asset_bundle, request_payload,
) -> None:
    authority_trust, signers, trust_snapshot = _cancellation_trust(tmp_path)
    workspace = _workspace(
        tmp_path, asset_bundle, "cancel-live-child",
        authority_trust=authority_trust,
    )
    request_id = _created_child_handoff(
        workspace, asset_bundle, request_payload,
    )
    before_revision = replay(workspace).revision
    code, output = _invoke_cancel(
        workspace,
        _cancel_payload(
            workspace, request_id, tmp_path, signers=signers,
            trust_snapshot=trust_snapshot,
        ),
        capsys,
    )
    assert code == 0, output
    replayed = replay(workspace)
    state = materialize(replayed)
    assert replayed.revision == before_revision + 2
    assert [event["event_type"] for event in replayed.events[-2:]] == [
        "handoff.cancelled", "lifecycle.cancelled",
    ]
    assert state["handoffs"][request_id]["status"] == "cancelled"
    assert state["handoffs"][request_id]["governance_node"] == \
        "S16:workspace.cancellation"
    resumed = resume_workspace(workspace)
    assert resumed["action"] == {
        "step": 1, "subject_type": "lifecycle", "instance_key": "archive",
        "disposition": "archive", "owner": "sdm-lifecycle",
        "missing": [], "platform_operations": [],
    }


def test_public_cancel_refuses_uncertain_child_without_reconciliation_or_writes(
    tmp_path, capsys, asset_bundle, request_payload,
) -> None:
    authority_trust, signers, trust_snapshot = _cancellation_trust(tmp_path)
    workspace = _workspace(
        tmp_path, asset_bundle, "cancel-uncertain-child",
        authority_trust=authority_trust,
    )
    request_id = _created_child_handoff(
        workspace, asset_bundle, request_payload, uncertain=True,
    )
    before = snapshot_tree(workspace)
    revision = replay(workspace).revision
    code, output = _invoke_cancel(
        workspace,
        _cancel_payload(
            workspace, request_id, tmp_path, signers=signers,
            trust_snapshot=trust_snapshot,
        ),
        capsys,
    )
    assert code == 3 and output["status"] == "blocked"
    assert output["result"]["error"] == \
        "uncertain_child_requires_reconciliation"
    assert replay(workspace).revision == revision
    assert snapshot_tree(workspace) == before
