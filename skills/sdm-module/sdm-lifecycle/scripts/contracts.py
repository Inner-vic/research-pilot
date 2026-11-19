#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Executable SDM schemas, semantic rules, gate DSL, and pinned assets."""

from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from canonical import (
    MAX_DOCUMENT_BYTES, CanonicalError, canonical_bytes, digest, digest_bytes,
    parse_document, validate_data_model,
)


ASSET_FILES = (
    "artifact-catalog.json",
    "supporting-artifact-catalog.json",
    "lifecycle-steps.json",
    "platform-operations.json",
    "artifact-contracts.json",
    "transition-requirements.json",
    "authority-policy.json",
    "event-types.json",
    "command-contracts.json",
    "verification-manifest.json",
    "bmad-dependencies.json",
)
SCHEMA_FILES = (
    "finding.schema.json",
    "result.schema.json",
    "event.schema.json",
    "state.schema.json",
    "artifact.schema.json",
    "authority-attestation.schema.json",
    "handoff-request.schema.json",
    "handoff-result.schema.json",
)
PRIMARY_IDS = tuple(
    [f"G{i:02d}" for i in range(1, 14)]
    + [f"B{i:02d}" for i in range(1, 5)]
    + [f"E{i:02d}" for i in range(1, 8)]
    + [f"R{i:02d}" for i in range(1, 9)]
    + [f"D{i:02d}" for i in range(1, 6)]
    + ["T01"]
)
SUPPORTING_IDS = tuple(f"S{i:02d}" for i in range(1, 18) if i != 11)
_ALL_ARTIFACT_IDS = frozenset((*PRIMARY_IDS, *SUPPORTING_IDS))
_V1_OWNER_SCHEMA_CONTRACTS: dict[str, tuple[str, frozenset[str]]] = {
    "sdm-create-ml-contract/assets/contract-record.schema.json": (
        "sdm-create-ml-contract",
        frozenset(f"G{i:02d}" for i in range(1, 14)),
    ),
    "sdm-lock-baseline/assets/baseline.schema.json": (
        "sdm-lock-baseline",
        frozenset(f"B{i:02d}" for i in range(1, 5)),
    ),
    "sdm-create-experiment-spec/assets/experiment-spec.schema.json": (
        "sdm-create-experiment-spec",
        frozenset(("E01", "E03", "E04", "E06", "E07")),
    ),
    "sdm-ml-readiness/assets/readiness-report.schema.json": (
        "sdm-ml-readiness",
        frozenset(("E02", "S04")),
    ),
    "sdm-build-context-pack/assets/context-pack.schema.json": (
        "sdm-build-context-pack",
        frozenset(("E05", "S05", "S06", "S07", "S08", "S09")),
    ),
    "sdm-evidence-review/assets/evidence-bundle.schema.json": (
        "sdm-evidence-review",
        frozenset(("D01", "D02", "D03", "D04", "S10")),
    ),
    "sdm-scientific-decision/assets/decision-record.schema.json": (
        "sdm-scientific-decision",
        frozenset(("D05",)),
    ),

}
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_INSTANCE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BMAD_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_JSON_POINTER = re.compile(r"^/(?:[^~/]|~0|~1)+(?:/(?:[^~/]|~0|~1)+)*$")
_ARTIFACT_REF = re.compile(r"^[A-Z][A-Z0-9]*:[A-Za-z0-9._-]+$")
_CHANGE_TOKEN = re.compile(
    r"^(?:code:[^?\x00]+|config:/[^?\x00]+|"
    r"ref:[A-Z][A-Z0-9]*:[A-Za-z0-9._-]+|"
    r"artifact:[A-Z][A-Z0-9]*:[A-Za-z0-9._-]+)$"
)
_OWNER_SKILL = re.compile(r"^sdm-[a-z0-9]+(?:-[a-z0-9]+)*$")
_EXTENSION_KEY = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$"
)
_SECRET_FIELD = re.compile(
    r"^(?:password|passwd|secret|private[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|api[_-]?key|credential|credentials|signed[_-]?url)$",
    re.I,
)
_SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_RFC3339 = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
_SCHEMA_KEYWORDS = frozenset({
    "$schema", "$id", "$ref", "$defs", "definitions",
    "title", "description", "default", "examples",
    "type", "const", "enum", "allOf", "anyOf", "oneOf", "not",
    "if", "then", "else", "properties", "patternProperties",
    "additionalProperties", "propertyNames", "required",
    "dependentRequired", "minProperties", "maxProperties",
    "items", "minItems", "maxItems", "uniqueItems",
    "minLength", "maxLength", "pattern", "format",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
})

ENGINEERING_PROVENANCE_SCHEMA = "sdm-engineering-provenance/v1"
ENGINEERING_PROVENANCE_COMMON_FIELDS = (
    "repository_identity", "change_request_identity", "change_revision",
    "change_tree_digest", "frozen_experiment_digest",
    "diff_evidence_digest", "pipeline_identity",
    "pipeline_definition_digest",
)
ENGINEERING_PROVENANCE_MERGED_FIELDS = (
    "merged_revision", "merged_tree_digest",
)
ENGINEERING_SOURCE_OPERATIONS: dict[str, tuple[str, ...]] = {
    "E05": ("git.branch-change-request",),
    "S07": (
        "git.branch-change-request", "git.change-review-status",
        "ml.implementation-checks",
    ),
    "S08": (
        "git.branch-change-request", "git.change-review-status",
        "ml.implementation-checks", "git.merge-status",
        "ml.deterministic-acceptance",
    ),
    "S09": (
        "git.branch-change-request", "git.change-review-status",
        "ml.implementation-checks", "git.merge-status",
        "ml.deterministic-acceptance",
        "ml.post-implementation-preflight",
    ),
}

RUN_FINGERPRINT_SCHEMA = "sdm-run-fingerprint/v1"
RUNTIME_ENVIRONMENT_SCHEMA = "sdm-runtime-environment/v1"
RUNTIME_ENVIRONMENT_PLAN_SCHEMA = "sdm-runtime-environment-plan/v1"
RUNTIME_PROFILE_SCHEMA = "sdm-runtime-profile/v1"
DEVSPACE_RUNTIME_SCHEMA = "sdm-devspace-runtime-environment/v1"
DEVSPACE_PROFILE_SCHEMA = "sdm-devspace-runtime-profile/v1"


def _devspace_image_provenance(value: Any) -> dict[str, Any]:
    fields = {'backend', 'purpose', 'namespace', 'space_uid', 'image_reference',
              'platform_spec_digest', 'content_status', 'signature_status'}
    if not isinstance(value, dict) or set(value) != fields:
        raise _runtime_contract_error('devspace_image_provenance_invalid', 'exact native devspace image provenance required')
    if (value['backend'] != 'lpai-devspace' or value['purpose'] != 'diagnostic-only'
        or value['content_status'] not in {'resolved', 'not-provided'}
        or value['signature_status'] not in {'verified', 'not-provided'}
        or any(not isinstance(value[key], str) or not value[key] for key in fields)
        or _DIGEST.fullmatch(value['platform_spec_digest']) is None):
        raise _runtime_contract_error('devspace_image_provenance_invalid', 'actual namespace, UID, image reference and native spec digest required')
    return value


def runtime_image_binding_valid(image: Any, profile: Any) -> bool:
    if isinstance(image, str) and _DIGEST.fullmatch(image):
        return True
    if image != 'not-provided' or not isinstance(profile, dict) or profile.get('schema') != DEVSPACE_PROFILE_SCHEMA:
        return False
    try:
        provenance = _devspace_image_provenance(profile.get('image_provenance'))
        return provenance['content_status'] == 'not-provided'
    except ContractError:
        return False
_RUNTIME_PROFILE_FIELDS = (
    "container_image_signature_digest", "operating_system", "driver",
    "cuda", "libraries", "gpu", "cpu", "kernel",
    "distributed_topology", "dependency_lock_digest",
)
_RUN_FINGERPRINT_BASE_FIELDS = (
    "run_id", "attempt_id", "frozen_experiment_ref",
    "frozen_experiment_digest", "run_plan_ref", "run_plan_digest",
    *ENGINEERING_PROVENANCE_COMMON_FIELDS,
    *ENGINEERING_PROVENANCE_MERGED_FIELDS,
    "baseline_lock_ref", "baseline_lock_digest",
    "resolved_configuration_ref", "resolved_configuration_digest",
    "config_digest", "dataset_manifest_ref", "dataset_manifest_digest",
    "data_digest", "split_manifest_ref", "split_manifest_digest",
    "split_digest", "evaluator_contract_ref", "evaluator_contract_digest",
    "evaluator_digest",
    "calibration_applicable", "expected_runtime_image_digest",
    "expected_runtime_profile", "expected_runtime_profile_digest",
    "seed", "seed_digest", "budget", "budget_digest",
    "allowed_diff", "allowed_diff_digest",
)


def resolved_configuration_trace_digest(configuration: dict[str, Any]) -> str:
    """Return the sole canonical digest for authenticated config resolution."""

    names = (
        "resolved_values", "defaults", "override_origins",
        "structured_config_schema_digest", "unknown_keys",
        "forbidden_overrides",
    )
    return digest({
        "schema": "sdm-config-resolution-trace/v1",
        **{name: configuration.get(name) for name in names},
    })


def _runtime_contract_error(code: str, message: str) -> ContractError:
    return ContractError(code, message, subject="R03")


def _exact_runtime_object(
    value: Any, *, name: str, fields: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise _runtime_contract_error(
            "runtime_environment_shape_invalid",
            f"{name} must contain exactly: {', '.join(fields)}",
        )
    if any(item in (None, "", [], {}) for item in value.values()):
        raise _runtime_contract_error(
            "runtime_environment_value_invalid",
            f"{name} contains an empty runtime fact",
        )
    return value


def canonical_runtime_environment_manifest(value: Any) -> dict[str, Any]:
    """Validate and return the sole closed actual-runtime representation."""

    required = {
        "schema", "environment_instance_identity", "container_image_digest",
        "container_image_signature_digest", "operating_system", "driver",
        "cuda", "libraries", "gpu", "cpu", "kernel",
        "distributed_topology", "dependency_lock_digest",
        "environment_variables",
    }
    devspace = isinstance(value, dict) and value.get('schema') == DEVSPACE_RUNTIME_SCHEMA
    if devspace:
        required.add('image_provenance')
    allowed = required | {"power_mode"}
    if not isinstance(value, dict):
        raise _runtime_contract_error(
            "runtime_environment_shape_invalid",
            "runtime manifest must be an object",
        )
    keys = set(value)
    if keys != required and keys != allowed:
        raise _runtime_contract_error(
            "runtime_environment_shape_invalid",
            "runtime manifest must contain the exact versioned field set",
        )
    if value.get("schema") not in (RUNTIME_ENVIRONMENT_SCHEMA, DEVSPACE_RUNTIME_SCHEMA):
        raise _runtime_contract_error(
            "runtime_environment_schema_invalid",
            f"runtime manifest schema must be {RUNTIME_ENVIRONMENT_SCHEMA}",
        )
    identity = value.get("environment_instance_identity")
    if not isinstance(identity, str) or not identity or _ID.fullmatch(identity) is None:
        raise _runtime_contract_error(
            "runtime_environment_identity_invalid",
            "environment_instance_identity must be a portable stable identity",
        )
    for name in (
        "container_image_digest", "container_image_signature_digest",
        "dependency_lock_digest",
    ):
        if devspace and name in ('container_image_digest', 'container_image_signature_digest'):
            provenance = _devspace_image_provenance(value['image_provenance'])
            status = provenance['content_status' if name == 'container_image_digest' else 'signature_status']
            if value.get(name) == 'not-provided' and status == 'not-provided':
                continue
            if status == 'not-provided':
                raise _runtime_contract_error('devspace_image_provenance_invalid', 'unavailable image evidence cannot contain a substituted digest')
        if not isinstance(value.get(name), str) or _DIGEST.fullmatch(value[name]) is None:
            raise _runtime_contract_error(
                "runtime_environment_digest_invalid",
                f"{name} must be a canonical sha256 digest",
            )
    for name, fields in (
        ("operating_system", ("name", "version", "architecture")),
        ("driver", ("vendor", "version")),
        ("cuda", ("toolkit_version", "runtime_version")),
        ("kernel", ("name", "release")),
    ):
        item = _exact_runtime_object(value.get(name), name=name, fields=fields)
        if any(not isinstance(bound, str) or not bound for bound in item.values()):
            raise _runtime_contract_error(
                "runtime_environment_value_invalid",
                f"{name} values must be non-empty strings",
            )
    gpu = _exact_runtime_object(
        value.get("gpu"), name="gpu",
        fields=("vendor", "model", "count", "memory_bytes", "compute_capability"),
    )
    if (
        type(gpu.get("count")) is not int or gpu["count"] < 0
        or type(gpu.get("memory_bytes")) is not int or gpu["memory_bytes"] < 0
        or any(not isinstance(gpu.get(name), str) or not gpu[name] for name in (
            "vendor", "model", "compute_capability",
        ))
        or gpu["count"] == 0 and (
            gpu["vendor"] != "none" or gpu["model"] != "none"
            or gpu["compute_capability"] != "none" or gpu["memory_bytes"] != 0
        )
        or gpu["count"] > 0 and (
            "none" in {gpu["vendor"], gpu["model"], gpu["compute_capability"]}
            or gpu["memory_bytes"] == 0
        )
    ):
        raise _runtime_contract_error(
            "runtime_environment_gpu_invalid",
            "gpu must use one consistent GPU or CPU-only inventory",
        )
    cpu = _exact_runtime_object(
        value.get("cpu"), name="cpu",
        fields=("architecture", "model", "logical_cores"),
    )
    if (
        not isinstance(cpu.get("architecture"), str) or not cpu["architecture"]
        or not isinstance(cpu.get("model"), str) or not cpu["model"]
        or type(cpu.get("logical_cores")) is not int or cpu["logical_cores"] < 1
    ):
        raise _runtime_contract_error(
            "runtime_environment_cpu_invalid",
            "cpu must bind architecture, model, and positive logical core count",
        )
    topology = _exact_runtime_object(
        value.get("distributed_topology"), name="distributed_topology",
        fields=(
            "backend", "node_count", "world_size", "processes_per_node",
            "interconnect",
        ),
    )
    if (
        any(not isinstance(topology.get(name), str) or not topology[name]
            for name in ("backend", "interconnect"))
        or any(type(topology.get(name)) is not int or topology[name] < 1
               for name in ("node_count", "world_size", "processes_per_node"))
        or topology["world_size"]
        != topology["node_count"] * topology["processes_per_node"]
    ):
        raise _runtime_contract_error(
            "runtime_environment_topology_invalid",
            "distributed topology must be positive and satisfy world_size = node_count * processes_per_node",
        )
    libraries = value.get("libraries")
    if not isinstance(libraries, list) or not libraries:
        raise _runtime_contract_error(
            "runtime_environment_libraries_invalid",
            "libraries must be a non-empty canonical inventory",
        )
    library_names: list[str] = []
    for item in libraries:
        row = _exact_runtime_object(
            item, name="library",
            fields=("name", "version", "artifact_digest"),
        )
        if (
            not isinstance(row.get("name"), str) or not row["name"]
            or not isinstance(row.get("version"), str) or not row["version"]
            or not isinstance(row.get("artifact_digest"), str)
            or _DIGEST.fullmatch(row["artifact_digest"]) is None
        ):
            raise _runtime_contract_error(
                "runtime_environment_libraries_invalid",
                "each library must bind name, version, and artifact digest",
            )
        library_names.append(row["name"])
    if library_names != sorted(set(library_names)):
        raise _runtime_contract_error(
            "runtime_environment_libraries_invalid",
            "libraries must be sorted by unique name",
        )
    variables = value.get("environment_variables")
    if not isinstance(variables, list):
        raise _runtime_contract_error(
            "runtime_environment_variables_invalid",
            "environment_variables must be a canonical whitelist inventory",
        )
    variable_names: list[str] = []
    for item in variables:
        row = _exact_runtime_object(
            item, name="environment_variable", fields=("name", "value_digest"),
        )
        if (
            not isinstance(row.get("name"), str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", row["name"]) is None
            or not isinstance(row.get("value_digest"), str)
            or _DIGEST.fullmatch(row["value_digest"]) is None
        ):
            raise _runtime_contract_error(
                "runtime_environment_variables_invalid",
                "whitelisted environment variables require a safe name and value_digest only",
            )
        variable_names.append(row["name"])
    if variable_names != sorted(set(variable_names)):
        raise _runtime_contract_error(
            "runtime_environment_variables_invalid",
            "environment_variables must be sorted by unique name",
        )
    if "power_mode" in value and (
        not isinstance(value["power_mode"], str) or not value["power_mode"]
    ):
        raise _runtime_contract_error(
            "runtime_environment_power_mode_invalid",
            "power_mode, when present, must be a non-empty string",
        )
    return json.loads(canonical_bytes(value))


def runtime_profile_from_manifest(value: Any) -> dict[str, Any]:
    manifest = canonical_runtime_environment_manifest(value)
    return {
        "schema": DEVSPACE_PROFILE_SCHEMA if manifest['schema'] == DEVSPACE_RUNTIME_SCHEMA else RUNTIME_PROFILE_SCHEMA,
        **{name: manifest[name] for name in _RUNTIME_PROFILE_FIELDS},
        **({'image_provenance': manifest['image_provenance']} if manifest['schema'] == DEVSPACE_RUNTIME_SCHEMA else {}),
    }


def _canonical_runtime_profile(value: Any) -> dict[str, Any]:
    devspace = isinstance(value, dict) and value.get('schema') == DEVSPACE_PROFILE_SCHEMA
    fields = {"schema", *_RUNTIME_PROFILE_FIELDS} | ({'image_provenance'} if devspace else set())
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") not in (RUNTIME_PROFILE_SCHEMA, DEVSPACE_PROFILE_SCHEMA)
    ):
        raise _runtime_contract_error(
            "runtime_profile_invalid",
            "expected runtime profile must contain the exact static field set",
        )
    synthetic = {
        "schema": DEVSPACE_RUNTIME_SCHEMA if devspace else RUNTIME_ENVIRONMENT_SCHEMA,
        "environment_instance_identity": "profile-validation",
        "container_image_digest": ('not-provided' if devspace and value['image_provenance']['content_status'] == 'not-provided' else "sha256:" + "0" * 64),
        **{name: value[name] for name in _RUNTIME_PROFILE_FIELDS},
        "environment_variables": [],
        **({'image_provenance': value['image_provenance']} if devspace else {}),
    }
    canonical = runtime_profile_from_manifest(synthetic)
    if canonical != value:
        raise _runtime_contract_error(
            "runtime_profile_invalid",
            "expected runtime profile is not canonical",
        )
    return canonical


def runtime_environment_digest(value: Any) -> str:
    manifest = canonical_runtime_environment_manifest(value)
    return digest({
        "schema": "sdm-runtime-environment-digest/v1",
        "manifest": manifest,
    })


def canonical_runtime_environment_plan(value: Any) -> dict[str, Any]:
    fields = {
        "schema", "environment_instance_identity",
        "expected_runtime_image_digest", "expected_runtime_profile",
        "expected_runtime_profile_digest",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise _runtime_contract_error(
            "runtime_environment_plan_invalid",
            "environment plan must contain the exact versioned expected-runtime fields",
        )
    if value.get("schema") != RUNTIME_ENVIRONMENT_PLAN_SCHEMA:
        raise _runtime_contract_error(
            "runtime_environment_plan_invalid",
            f"environment plan schema must be {RUNTIME_ENVIRONMENT_PLAN_SCHEMA}",
        )
    identity = value.get("environment_instance_identity")
    if not isinstance(identity, str) or not identity or _ID.fullmatch(identity) is None:
        raise _runtime_contract_error(
            "runtime_environment_plan_invalid",
            "environment plan requires a portable instance identity",
        )
    image = value.get("expected_runtime_image_digest")
    profile = value.get("expected_runtime_profile")
    profile_digest = value.get("expected_runtime_profile_digest")
    if (
        not runtime_image_binding_valid(image, profile)
        or not isinstance(profile, dict)
        or profile_digest != digest(profile)
    ):
        raise _runtime_contract_error(
            "runtime_environment_plan_invalid",
            "environment plan image/profile/digest is not canonical",
        )
    _canonical_runtime_profile(profile)
    return json.loads(canonical_bytes(value))


def runtime_environment_plan_digest(value: Any) -> str:
    plan = canonical_runtime_environment_plan(value)
    return digest({
        "schema": "sdm-runtime-environment-plan-digest/v1",
        "plan": plan,
    })


def validate_runtime_environment_against_r01(
    manifest: Any, r01: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    canonical = canonical_runtime_environment_manifest(manifest)
    content = r01.get("content", r01) if isinstance(r01, dict) else None
    if not isinstance(content, dict):
        raise _runtime_contract_error(
            "runtime_environment_r01_invalid", "R01 content is unavailable",
        )
    profile = runtime_profile_from_manifest(canonical)
    if canonical["container_image_digest"] != content.get(
        "expected_runtime_image_digest",
    ):
        raise _runtime_contract_error(
            "runtime_environment_image_mismatch",
            "actual container image differs from R01 expected image",
        )
    if (
        profile != content.get("expected_runtime_profile")
        or digest(profile) != content.get("expected_runtime_profile_digest")
    ):
        raise _runtime_contract_error(
            "runtime_environment_profile_mismatch",
            "actual runtime profile differs from the exact R01 expected profile",
        )
    return canonical, runtime_environment_digest(canonical)


def canonical_run_fingerprint(bindings: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Build and digest the versioned, closed R01 execution fingerprint.

    Materialization and every verifier call this function, so a field cannot
    be silently omitted from one path while remaining accepted by another.
    """

    if not isinstance(bindings, dict):
        raise ContractError(
            "run_fingerprint_invalid", "run fingerprint bindings must be an object",
            subject="R01",
        )
    applicable = bindings.get("calibration_applicable")
    calibration_fields = (
        "calibration_manifest_ref", "calibration_manifest_digest",
        "calibration_digest",
    ) if applicable is True else ()
    expected = set((*_RUN_FINGERPRINT_BASE_FIELDS, *calibration_fields))
    if set(bindings) != expected:
        raise ContractError(
            "run_fingerprint_fields_invalid",
            "run fingerprint bindings must contain the exact versioned field set",
            subject="R01",
        )
    if type(applicable) is not bool:
        raise ContractError(
            "run_fingerprint_calibration_invalid",
            "calibration_applicable must be boolean", subject="R01",
        )
    for name in ("run_id", "attempt_id"):
        value = bindings.get(name)
        if not isinstance(value, str) or _ID.fullmatch(value) is None:
            raise ContractError(
                "run_fingerprint_identity_invalid",
                f"{name} must be a portable exact identity", subject="R01",
            )
    exact_refs = {
        "frozen_experiment_ref": "E01",
        "run_plan_ref": "E04",
        "baseline_lock_ref": "B02",
        "resolved_configuration_ref": "R02",
        "dataset_manifest_ref": "R04",
        "split_manifest_ref": "R05",
        "evaluator_contract_ref": "G10",
        **({"calibration_manifest_ref": "R06"} if applicable else {}),
    }
    for name, artifact_id in exact_refs.items():
        value = bindings.get(name)
        if (
            not isinstance(value, str)
            or _ARTIFACT_REF.fullmatch(value) is None
            or not value.startswith(f"{artifact_id}:")
        ):
            raise ContractError(
                "run_fingerprint_reference_invalid",
                f"{name} must identify one exact {artifact_id} instance",
                subject="R01",
            )
    for name in (
        "repository_identity", "change_request_identity", "change_revision",
        "merged_revision", "pipeline_identity",
    ):
        if not isinstance(bindings.get(name), str) or not bindings[name]:
            raise ContractError(
                "run_fingerprint_provenance_invalid",
                f"{name} must be a non-empty authenticated provenance identity",
                subject="R01",
            )
    if (
        not isinstance(bindings.get("budget"), dict) or not bindings["budget"]
        or not isinstance(bindings.get("allowed_diff"), (dict, list))
        or not bindings["allowed_diff"]
        or bindings.get("seed") in (None, "", [], {})
    ):
        raise ContractError(
            "run_fingerprint_execution_fact_invalid",
            "seed, budget, and allowed_diff must carry concrete execution facts",
            subject="R01",
        )
    _canonical_runtime_profile(bindings.get("expected_runtime_profile"))
    for name, value in bindings.items():
        if name == 'expected_runtime_image_digest' and runtime_image_binding_valid(value, bindings['expected_runtime_profile']):
            continue
        if name.endswith("_digest") and (
            not isinstance(value, str) or _DIGEST.fullmatch(value) is None
        ):
            raise ContractError(
                "run_fingerprint_digest_invalid",
                f"{name} must be a canonical sha256 digest", subject="R01",
            )
    derived = {
        "expected_runtime_profile_digest": digest(
            bindings["expected_runtime_profile"],
        ),
        "seed_digest": digest(bindings["seed"]),
        "budget_digest": digest(bindings["budget"]),
        "allowed_diff_digest": digest(bindings["allowed_diff"]),
    }
    mismatched = sorted(
        name for name, expected_value in derived.items()
        if bindings.get(name) != expected_value
    )
    if mismatched:
        raise ContractError(
            "run_fingerprint_component_digest_mismatch",
            "run fingerprint component digests do not bind canonical values: "
            + ", ".join(mismatched), subject="R01",
        )
    payload = {"schema": RUN_FINGERPRINT_SCHEMA, **bindings}
    return payload, digest(payload)


class ContractError(ValueError):
    def __init__(self, code: str, message: str, *, pointer: str = "", subject: str = "", gate_impact: str = "block") -> None:
        super().__init__(message)
        self.code = code
        self.pointer = pointer
        self.subject = subject
        self.gate_impact = gate_impact


def finding(
    code: str,
    message: str,
    *,
    subject: str = "",
    pointer: str = "",
    governing_asset: str = "",
    rule_digest: str = "",
    gate_impact: str = "block",
    severity: str = "high",
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "subject": subject,
        "json_pointer": pointer,
        "governing_asset": governing_asset,
        "rule_digest": rule_digest or digest({"code": code, "governing_asset": governing_asset}),
        "gate_impact": gate_impact,
    }


def sort_findings(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (item.get("severity", ""), item.get("code", ""), item.get("subject", ""), item.get("json_pointer", "")))


def validate_baseline_replacements(
    deprecation_records: Iterable[dict[str, Any]],
    materialized_baseline_ids: Iterable[str],
) -> list[dict[str, Any]]:
    """Validate the directed replacement graph carried by immutable B04 records.

    ``materialized_baseline_ids`` is deliberately supplied by the workspace
    boundary, which authenticates B01/B02 bytes against the ledger before
    deriving their semantic identities.  This function is otherwise pure and
    can be reused by both pre-publication and read-only validation paths.
    """
    known = {
        identity
        for identity in materialized_baseline_ids
        if isinstance(identity, str) and identity
    }
    findings: list[dict[str, Any]] = []
    replacements: dict[str, str] = {}
    subjects: dict[tuple[str, str], str] = {}

    for index, record in enumerate(deprecation_records):
        envelope = isinstance(record, dict) and isinstance(record.get("content"), dict)
        content = record.get("content") if envelope else record
        content = content if isinstance(content, dict) else {}
        prefix = "/content" if envelope else ""
        subject = str(
            content.get("deprecation_id")
            or (record.get("artifact_id") if isinstance(record, dict) else "")
            or f"B04:{index}"
        )
        source = content.get("baseline_id")
        replacement = content.get("replacement")

        if not isinstance(source, str) or not source:
            findings.append(finding(
                "baseline_replacement_source_missing",
                "B04 requires a non-empty source baseline_id",
                subject=subject,
                pointer=f"{prefix}/baseline_id",
                governing_asset="artifact-contracts.json",
            ))
            continue
        if not isinstance(replacement, str) or not replacement:
            findings.append(finding(
                "baseline_replacement_target_missing",
                "B04 requires a non-empty replacement baseline identity",
                subject=subject,
                pointer=f"{prefix}/replacement",
                governing_asset="artifact-contracts.json",
            ))
            continue
        if source not in known:
            findings.append(finding(
                "baseline_replacement_source_unknown",
                f"B04 source baseline is not materialized by B01/B02: {source}",
                subject=subject,
                pointer=f"{prefix}/baseline_id",
                governing_asset="artifact-contracts.json",
            ))
        if replacement not in known:
            findings.append(finding(
                "baseline_replacement_target_unknown",
                f"B04 replacement baseline is not materialized by B01/B02: {replacement}",
                subject=subject,
                pointer=f"{prefix}/replacement",
                governing_asset="artifact-contracts.json",
            ))

        prior = replacements.get(source)
        if prior is not None and prior != replacement:
            findings.append(finding(
                "baseline_replacement_conflict",
                f"baseline {source} already has a different replacement: {prior}",
                subject=subject,
                pointer=f"{prefix}/replacement",
                governing_asset="artifact-contracts.json",
            ))
            continue
        replacements[source] = replacement
        subjects[(source, replacement)] = subject

    reported_cycles: set[tuple[str, ...]] = set()
    for start in sorted(replacements):
        path: list[str] = []
        positions: dict[str, int] = {}
        current = start
        while current in replacements:
            if current in positions:
                members = path[positions[current]:]
                canonical_cycle = tuple(sorted(members))
                if canonical_cycle not in reported_cycles:
                    reported_cycles.add(canonical_cycle)
                    closed = [*members, current]
                    edge = (members[-1], current)
                    findings.append(finding(
                        "baseline_replacement_cycle",
                        "baseline replacement graph contains a directed cycle: "
                        + " -> ".join(closed),
                        subject=subjects.get(edge, "B04"),
                        pointer="/content/replacement",
                        governing_asset="artifact-contracts.json",
                    ))
                break
            positions[current] = len(path)
            path.append(current)
            current = replacements[current]

    return sort_findings(findings)


def _portable_asset_parts(relative: str) -> tuple[str, ...]:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ContractError("owned_schema_path_invalid", "owner schema path must be a non-empty POSIX path")
    path = PurePosixPath(relative)
    if path.is_absolute() or path.as_posix() != relative or any(part in {"", ".", ".."} for part in path.parts):
        raise ContractError("owned_schema_path_invalid", f"owner schema path is not portable: {relative}", subject=relative)
    return path.parts


def _read_relative_regular(root: Path, relative: str) -> bytes:
    """Read a contained regular file without following any path component."""
    parts = _portable_asset_parts(relative)
    if os.name == "nt":
        from windows_assets import read_regular
        return read_regular(root, parts, MAX_DOCUMENT_BYTES)
    directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    root_fd = os.open(root, directory_flags)
    current_fd = root_fd
    opened_directory = False
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=current_fd)
            if opened_directory:
                os.close(current_fd)
            current_fd = next_fd
            opened_directory = True
        file_fd = os.open(parts[-1], file_flags, dir_fd=current_fd)
        try:
            info = os.fstat(file_fd)
            if (
                not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size > MAX_DOCUMENT_BYTES
            ):
                raise ContractError("unsafe_asset", f"engine asset is not a regular file: {relative}", subject=relative)
            data = bytearray()
            while len(data) < info.st_size:
                chunk = os.read(
                    file_fd, min(1024 * 1024, info.st_size - len(data)),
                )
                if not chunk:
                    break
                data.extend(chunk)
            after = os.fstat(file_fd)
            before_identity = (
                info.st_mode, info.st_dev, info.st_ino, info.st_nlink,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            )
            after_identity = (
                after.st_mode, after.st_dev, after.st_ino, after.st_nlink,
                after.st_size, after.st_mtime_ns, after.st_ctime_ns,
            )
            if len(data) != info.st_size or before_identity != after_identity:
                raise ContractError(
                    "asset_concurrent_mutation",
                    f"engine asset changed while reading: {relative}",
                    subject=relative,
                )
            return bytes(data)
        finally:
            os.close(file_fd)
    finally:
        if opened_directory:
            os.close(current_fd)
        os.close(root_fd)


def require_supported_engine(documents: dict[str, Any]) -> None:
    """Reject retired engines before resolving their removed owner schemas."""

    lifecycle = documents.get("lifecycle-steps.json")
    if not isinstance(lifecycle, dict):
        raise ContractError("lifecycle_steps_invalid", "lifecycle steps must be an object")
    count = lifecycle.get("step_count")
    rows = lifecycle.get("steps")
    if (type(count) is int and count > 21) or (isinstance(rows, list) and len(rows) > 21):
        raise ContractError(
            "unsupported_engine_bundle",
            "this runtime cannot execute a retired release engine; preserve its pinned workspace unchanged",
        )
    if type(count) is not int or count != 21 or not isinstance(rows, list) or len(rows) != 21:
        raise ContractError("lifecycle_steps_invalid", "the research engine must declare exactly 21 lifecycle steps")


def _owned_schema_bindings(documents: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate and normalize every catalog-owned schema declaration."""
    result: list[dict[str, Any]] = []
    seen_artifacts: set[str] = set()
    path_contracts: dict[str, tuple[str, str]] = {}
    artifacts_by_path: dict[str, set[str]] = {}
    for catalog_name in ("artifact-catalog.json", "supporting-artifact-catalog.json"):
        catalog = documents.get(catalog_name)
        rows = _rows(catalog, "artifacts")
        catalog_ids = {row.get("id") for row in rows if isinstance(row, dict)}
        bindings = catalog.get("owned_schema_bindings") if isinstance(catalog, dict) else None
        if not isinstance(bindings, list):
            raise ContractError(
                "owned_schema_bindings_invalid",
                f"{catalog_name} must declare owned_schema_bindings as an array",
                subject=catalog_name,
            )
        for index, binding in enumerate(bindings):
            subject = f"{catalog_name}#/owned_schema_bindings/{index}"
            if not isinstance(binding, dict) or set(binding) != {"artifact_ids", "owner_skill", "path", "digest"}:
                raise ContractError(
                    "owned_schema_binding_invalid",
                    "owner schema binding has missing or unknown fields",
                    subject=subject,
                )
            owner = binding.get("owner_skill")
            relative = binding.get("path")
            expected_digest = binding.get("digest")
            artifact_ids = binding.get("artifact_ids")
            if not isinstance(owner, str) or not _OWNER_SKILL.fullmatch(owner):
                raise ContractError("owned_schema_owner_invalid", "owner_skill is not a portable SDM Skill identity", subject=subject)
            if not isinstance(relative, str):
                raise ContractError("owned_schema_path_invalid", "owner schema path must be a string", subject=subject)
            parts = _portable_asset_parts(relative)
            if len(parts) != 3 or parts[0] != owner or parts[1] != "assets" or not parts[2].endswith(".schema.json"):
                raise ContractError(
                    "owned_schema_path_invalid",
                    "owner schema path must be <owner_skill>/assets/<name>.schema.json",
                    subject=relative,
                )
            if not isinstance(expected_digest, str) or not _DIGEST.fullmatch(expected_digest):
                raise ContractError("owned_schema_digest_invalid", "owner schema digest must be canonical sha256", subject=relative)
            if (
                not isinstance(artifact_ids, list) or not artifact_ids
                or any(not isinstance(item, str) or item not in catalog_ids for item in artifact_ids)
                or len(set(artifact_ids)) != len(artifact_ids)
            ):
                raise ContractError(
                    "owned_schema_artifact_coverage_invalid",
                    "owner schema artifact_ids must be a non-empty unique subset of its catalog",
                    subject=subject,
                )
            overlap = seen_artifacts.intersection(artifact_ids)
            if overlap:
                raise ContractError(
                    "owned_schema_artifact_coverage_invalid",
                    f"artifact has more than one owner schema binding: {sorted(overlap)}",
                    subject=subject,
                )
            seen_artifacts.update(artifact_ids)
            prior = path_contracts.get(relative)
            current = (owner, expected_digest)
            if prior is not None and prior != current:
                raise ContractError(
                    "owned_schema_binding_conflict",
                    "the same owner schema path has conflicting owner or digest declarations",
                    subject=relative,
                )
            path_contracts[relative] = current
            artifacts_by_path.setdefault(relative, set()).update(artifact_ids)
            by_id = {row["id"]: row for row in rows}
            if any(by_id[item].get("schema") != "schemas/artifact.schema.json" for item in artifact_ids):
                raise ContractError(
                    "owned_schema_central_contract_invalid",
                    "owner-bound artifacts must also use the central persisted-envelope schema",
                    subject=subject,
                )
            result.append({
                "catalog": catalog_name,
                "artifact_ids": tuple(artifact_ids),
                "owner_skill": owner,
                "path": relative,
                "digest": expected_digest,
            })
    actual_contracts = {
        path: (path_contracts[path][0], frozenset(artifact_ids))
        for path, artifact_ids in artifacts_by_path.items()
    }
    if actual_contracts != _V1_OWNER_SCHEMA_CONTRACTS:
        missing_paths = sorted(
            set(_V1_OWNER_SCHEMA_CONTRACTS).difference(actual_contracts)
        )
        unexpected_paths = sorted(
            set(actual_contracts).difference(_V1_OWNER_SCHEMA_CONTRACTS)
        )
        changed_paths = sorted(
            path for path in set(actual_contracts).intersection(
                _V1_OWNER_SCHEMA_CONTRACTS
            )
            if actual_contracts[path] != _V1_OWNER_SCHEMA_CONTRACTS[path]
        )
        raise ContractError(
            "owned_schema_v1_mismatch",
            "v1 owner schema bindings must exactly preserve all eight "
            "path/owner/artifact coverage contracts; "
            f"missing={missing_paths}, unexpected={unexpected_paths}, "
            f"changed={changed_paths}",
            subject="owned_schema_bindings",
        )
    return result


def _declarative_string_list(
    row: dict[str, Any],
    field: str,
    *,
    code: str,
) -> list[str]:
    """Load one optional declarative list without truthiness coercion."""

    if field not in row:
        return []
    value = row[field]
    if (
        not isinstance(value, list)
        or any(
            not isinstance(item, str) or not item.strip()
            for item in value
        )
        or len(value) != len(set(value))
    ):
        raise ContractError(
            code,
            f"semantic validator {field} must be an array of unique "
            "non-empty strings",
            subject=str(row.get("id", "")),
        )
    return value


_COMMAND_ROW_FIELDS = frozenset({
    "name", "emitted_events", "stdout_schema", "handled_exit_codes",
    "idempotency", "reconciliation",
})
_EXECUTION_CONTRACT_FIELDS = frozenset({
    "entrypoint", "command_path", "parser", "input_bindings",
    "read_scopes", "write_scopes", "locks", "journals",
    "external_call_allowlist", "mutation_boundaries",
})
_PARSER_CONTRACT_FIELDS = frozenset({
    "required", "optional", "semantic_required", "semantic_forbidden",
    "constants", "argument_kinds",
})
_ARGUMENT_KINDS = frozenset({
    "boolean-flag", "digest-or-absence", "enum", "identity", "integer",
    "path", "sha256-digest", "strict-json-inline-or-path", "string",
})
_LIVE_HOST_BOUNDARIES = frozenset({
    "forbidden", "governed-workspace-only",
    "module-owned-install-paths-only", "external-run-only",
})


def _unique_string_array(
    value: Any,
    *,
    code: str,
    subject: str,
    option_names: bool = False,
) -> list[str]:
    if (
        not isinstance(value, list)
        or any(
            not isinstance(item, str)
            or not item
            or option_names and not re.fullmatch(r"--[a-z][a-z0-9-]*", item)
            for item in value
        )
        or len(value) != len(set(value))
    ):
        raise ContractError(
            code,
            "contract field must be an array of unique canonical strings",
            subject=subject,
        )
    return value


def _portable_contract_path(
    value: Any, *, subject: str, scope: str | None = None,
) -> str:
    project_marker = "{project-root}/"
    if not isinstance(value, str):
        relative = value
    elif scope == "project-host":
        if not value.startswith(project_marker) \
                or value.count("{project-root}") != 1:
            raise ContractError(
                "command_io_contract_invalid",
                "project-host lock and journal paths require one leading {project-root}/ marker",
                subject=subject,
            )
        relative = value.removeprefix(project_marker)
    else:
        if "{project-root}" in value:
            raise ContractError(
                "command_io_contract_invalid",
                "only project-host paths may use the {project-root} marker",
                subject=subject,
            )
        relative = value
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or relative.startswith("/")
        or "{" in relative
        or "}" in relative
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ContractError(
            "command_io_contract_invalid",
            "lock and journal paths must be contained portable paths",
            subject=subject,
        )
    return relative


def _validate_command_contract_document(
    document: Any,
    documents: dict[str, Any],
    event_names: set[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Validate the complete machine-executable public command boundary.

    Descriptive prose is intentionally excluded from the v2 execution
    authority.  Parser ingress, root scopes, locks, journals, subprocess or
    adapter allowlists, and mutation boundaries are all exact-closure records.
    Unknown keys therefore fail closed instead of silently becoming comments.
    """

    top_fields = {
        "schema", "schema_version", "stdout_schema_default", "exit_meanings",
        "signal_exception", "scope_registry", "input_contract_registry",
        "external_call_registry", "authoritative_behavior_audit_exceptions",
        "execution_contracts", "commands",
    }
    if not isinstance(document, dict) or set(document) != top_fields:
        raise ContractError(
            "command_contract_shape_invalid",
            "command-contracts v2 has missing or unknown top-level fields",
            subject="command-contracts.json",
        )
    if (
        document.get("schema") != "sdm-command-contracts/v2"
        or document.get("schema_version") != "2.0.0"
        or document.get("stdout_schema_default") != "schemas/result.schema.json"
        or document["stdout_schema_default"] not in documents
        or not isinstance(document.get("signal_exception"), str)
        or not document["signal_exception"]
    ):
        raise ContractError(
            "command_contract_shape_invalid",
            "command-contracts v2 identity/default schema/signal exception is invalid",
            subject="command-contracts.json",
        )
    exit_meanings = document.get("exit_meanings")
    if (
        not isinstance(exit_meanings, dict)
        or set(exit_meanings) != {"0", "2", "3", "4", "5", "70"}
        or any(not isinstance(item, str) or not item for item in exit_meanings.values())
    ):
        raise ContractError(
            "command_exit_contract_invalid",
            "exit meanings must exactly cover public handled exit codes",
            subject="command-contracts.json#/exit_meanings",
        )

    scopes = document.get("scope_registry")
    if not isinstance(scopes, list) or not scopes:
        raise ContractError(
            "command_scope_registry_invalid", "scope registry must be non-empty",
        )
    scope_ids: list[str] = []
    scope_resolvers: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(scopes):
        subject = f"command-contracts.json#/scope_registry/{index}"
        if (
            not isinstance(row, dict)
            or set(row) != {"id", "resolver"}
            or not isinstance(row.get("id"), str)
            or not re.fullmatch(r"[a-z][a-z0-9-]*", row["id"])
            or not isinstance(row.get("resolver"), dict)
        ):
            raise ContractError(
                "command_scope_registry_invalid",
                "scope rows require an exact canonical id and resolver",
                subject=subject,
            )
        resolver = row["resolver"]
        kind = resolver.get("kind")
        expected = {
            "entrypoint-module-root": {"kind"},
            "argument": {"kind", "argument"},
            "argument-relative": {"kind", "argument", "path"},
            "input-bindings": {"kind"},
            "input-document-field": {
                "kind", "argument", "field", "value_kind",
            },
            "workspace-genesis": {"kind", "field"},
            "argument-first": {"kind", "arguments"},
            "environment": {"kind", "variables"},
        }.get(kind)
        if (
            kind == "argument"
            and type(resolver.get("read_identity_ancestor_depth")) is int
        ):
            expected = {"kind", "argument", "read_identity_ancestor_depth"}
        if (
            kind == "workspace-genesis"
            and type(resolver.get("read_identity_ancestor_depth")) is int
        ):
            expected = {"kind", "field", "read_identity_ancestor_depth"}
        if expected is None or set(resolver) != expected:
            raise ContractError(
                "command_scope_registry_invalid",
                "scope resolver kind has missing or unknown fields",
                subject=subject,
            )
        depth = resolver.get("read_identity_ancestor_depth", 0)
        if type(depth) is not int or depth < 0 or depth > 8:
            raise ContractError(
                "command_scope_registry_invalid",
                "identity ancestor depth must be an integer in 0..8",
                subject=subject,
            )
        if kind in {
            "argument", "argument-relative", "input-document-field",
        } and (
            not isinstance(resolver["argument"], str)
            or not resolver["argument"].startswith("--")
        ):
            raise ContractError(
                "command_scope_registry_invalid", "argument resolver is invalid",
                subject=subject,
            )
        if kind == "argument-relative":
            _portable_contract_path(resolver["path"], subject=subject)
        if kind in {"argument-first", "environment"}:
            key = "arguments" if kind == "argument-first" else "variables"
            _unique_string_array(
                resolver[key], code="command_scope_registry_invalid",
                subject=subject, option_names=kind == "argument-first",
            )
        if kind == "workspace-genesis" and (
            not isinstance(resolver["field"], str) or not resolver["field"]
        ):
            raise ContractError(
                "command_scope_registry_invalid", "genesis field is invalid",
                subject=subject,
            )
        if kind == "input-document-field" and (
            not isinstance(resolver["field"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_]*", resolver["field"])
            or resolver["value_kind"] != "absolute-directory"
        ):
            raise ContractError(
                "command_scope_registry_invalid",
                "input-document-field resolver is invalid",
                subject=subject,
            )
        scope_ids.append(row["id"])
        scope_resolvers[row["id"]] = resolver
    if len(scope_ids) != len(set(scope_ids)):
        raise ContractError(
            "command_scope_registry_invalid", "scope IDs must be unique",
        )
    scope_set = set(scope_ids)

    input_rows = document.get("input_contract_registry")
    if not isinstance(input_rows, list) or not input_rows:
        raise ContractError(
            "command_input_contract_invalid",
            "input contract registry must be non-empty",
        )
    input_ids: list[str] = []
    for index, row in enumerate(input_rows):
        subject = f"command-contracts.json#/input_contract_registry/{index}"
        if not isinstance(row, dict) or row.get("kind") not in {
            "json-schema", "handler-semantic",
        }:
            raise ContractError(
                "command_input_contract_invalid", "input contract kind is invalid",
                subject=subject,
            )
        expected = {"id", "kind", "shape", "accepted_transports"}
        if row["kind"] == "json-schema":
            expected.add("schema")
        if (
            set(row) != expected
            or not isinstance(row.get("id"), str)
            or not re.fullmatch(r"[a-z][a-z0-9-]*(?::[a-z][a-z0-9-]*)*/v[0-9]+", row["id"])
            or row.get("shape") not in {"any", "object", "array-or-object"}
        ):
            raise ContractError(
                "command_input_contract_invalid",
                "input contracts require exact id/kind/shape/transport fields",
                subject=subject,
            )
        transports = _unique_string_array(
            row["accepted_transports"], code="command_input_contract_invalid",
            subject=subject,
        )
        if not transports or not set(transports).issubset({
            "strict-document-file", "strict-json-file", "strict-json-inline",
        }):
            raise ContractError(
                "command_input_contract_invalid", "input transport is unknown",
                subject=subject,
            )
        if row["kind"] == "json-schema" and (
            not isinstance(row.get("schema"), str)
            or row["schema"] not in documents
        ):
            raise ContractError(
                "command_input_contract_invalid",
                "JSON-Schema input contract has a dangling schema reference",
                subject=subject,
            )
        input_ids.append(row["id"])
    if len(input_ids) != len(set(input_ids)):
        raise ContractError(
            "command_input_contract_invalid", "input contract IDs must be unique",
        )
    input_set = set(input_ids)

    external_rows = document.get("external_call_registry")
    if not isinstance(external_rows, list) or not external_rows:
        raise ContractError(
            "command_external_contract_invalid",
            "external call registry must be non-empty",
        )
    external_ids: list[str] = []
    for index, row in enumerate(external_rows):
        subject = f"command-contracts.json#/external_call_registry/{index}"
        if (
            not isinstance(row, dict)
            or set(row) != {"id", "kind", "executables"}
            or not isinstance(row.get("id"), str)
            or not re.fullmatch(r"[a-z][a-z0-9-]*", row["id"])
            or row.get("kind") not in {
                "subprocess", "configured-subprocess", "python-subprocess",
            }
        ):
            raise ContractError(
                "command_external_contract_invalid",
                "external call rows require exact canonical fields",
                subject=subject,
            )
        _unique_string_array(
            row["executables"], code="command_external_contract_invalid",
            subject=subject,
        )
        external_ids.append(row["id"])
    if len(external_ids) != len(set(external_ids)):
        raise ContractError(
            "command_external_contract_invalid", "external call IDs must be unique",
        )
    external_set = set(external_ids)

    commands = _rows(document, "commands")
    names: list[str] = []
    command_event_names: set[str] = set()
    known_exits = {int(item) for item in exit_meanings}
    for index, row in enumerate(commands):
        subject = f"command-contracts.json#/commands/{index}"
        if (
            not isinstance(row, dict)
            or set(row) != _COMMAND_ROW_FIELDS
            or not isinstance(row.get("name"), str)
            or not row["name"]
            or not isinstance(row.get("stdout_schema"), str)
            or row["stdout_schema"] not in documents
            or not isinstance(row.get("idempotency"), str)
            or not row["idempotency"]
            or not isinstance(row.get("reconciliation"), str)
            or not row["reconciliation"]
        ):
            raise ContractError(
                "command_contract_shape_invalid",
                "command rows have missing, unknown, or invalid fields",
                subject=subject,
            )
        emitted = _unique_string_array(
            row["emitted_events"], code="command_emitted_events_invalid",
            subject=subject,
        )
        unknown = sorted(set(emitted).difference(event_names))
        if unknown:
            raise ContractError(
                "command_event_unknown",
                f"command emits undeclared event types: {unknown}",
                subject=row["name"],
            )
        exits = row.get("handled_exit_codes")
        if (
            not isinstance(exits, list)
            or any(type(item) is not int or item not in known_exits for item in exits)
            or exits != sorted(set(exits))
        ):
            raise ContractError(
                "command_exit_contract_invalid",
                "handled exits must be a sorted unique subset of exit meanings",
                subject=row["name"],
            )
        names.append(row["name"])
        command_event_names.update(emitted)
    if len(names) != len(set(names)) or any(not name.strip() for name in names):
        raise ContractError(
            "command_contract_mismatch", "each command contract must have one unique name",
        )
    name_set = set(names)

    executions = document.get("execution_contracts")
    if not isinstance(executions, dict) or set(executions) != name_set:
        raise ContractError(
            "command_execution_closure_invalid",
            "execution contract keys must exactly equal public command names",
            subject="command-contracts.json#/execution_contracts",
        )
    used_scopes: set[str] = set()
    used_inputs: set[str] = set()
    used_external: set[str] = set()
    for name in names:
        execution = executions[name]
        subject = f"command-contracts.json#/execution_contracts/{name}"
        if not isinstance(execution, dict) or set(execution) != _EXECUTION_CONTRACT_FIELDS:
            raise ContractError(
                "command_execution_contract_invalid",
                "execution contract has missing or unknown fields",
                subject=subject,
            )
        if (
            not isinstance(execution.get("entrypoint"), str)
            or not execution["entrypoint"]
        ):
            raise ContractError(
                "command_parser_contract_invalid", "entrypoint is invalid",
                subject=subject,
            )
        _unique_string_array(
            execution["command_path"], code="command_parser_contract_invalid",
            subject=subject,
        )
        parser = execution.get("parser")
        if not isinstance(parser, dict) or set(parser) != _PARSER_CONTRACT_FIELDS:
            raise ContractError(
                "command_parser_contract_invalid",
                "parser contract has missing or unknown fields",
                subject=subject,
            )
        required = _unique_string_array(
            parser["required"], code="command_parser_contract_invalid",
            subject=subject, option_names=True,
        )
        optional = _unique_string_array(
            parser["optional"], code="command_parser_contract_invalid",
            subject=subject, option_names=True,
        )
        roster = set(required) | set(optional)
        if set(required).intersection(optional):
            raise ContractError(
                "command_parser_contract_invalid",
                "required and optional parser arguments must be disjoint",
                subject=subject,
            )
        semantic_required = _unique_string_array(
            parser["semantic_required"], code="command_parser_contract_invalid",
            subject=subject, option_names=True,
        )
        semantic_forbidden = _unique_string_array(
            parser["semantic_forbidden"], code="command_parser_contract_invalid",
            subject=subject, option_names=True,
        )
        if (
            not set(semantic_required).issubset(roster)
            or not set(semantic_forbidden).issubset(roster)
            or set(semantic_required).intersection(semantic_forbidden)
        ):
            raise ContractError(
                "command_parser_contract_invalid",
                "semantic argument constraints must be disjoint roster subsets",
                subject=subject,
            )
        kinds = parser.get("argument_kinds")
        constants = parser.get("constants")
        if (
            not isinstance(kinds, dict)
            or set(kinds) != roster
            or any(kind not in _ARGUMENT_KINDS for kind in kinds.values())
            or not isinstance(constants, dict)
            or not set(constants).issubset(roster)
            or any(not isinstance(value, (str, int, bool)) for value in constants.values())
        ):
            raise ContractError(
                "command_parser_contract_invalid",
                "argument kinds/constants must exactly bind the parser roster",
                subject=subject,
            )

        input_bindings = execution.get("input_bindings")
        if not isinstance(input_bindings, list):
            raise ContractError(
                "command_input_binding_invalid", "input_bindings must be an array",
                subject=subject,
            )
        bound_arguments: list[str] = []
        for index, binding in enumerate(input_bindings):
            if (
                not isinstance(binding, dict)
                or set(binding) != {"argument", "contract"}
                or binding.get("argument") not in roster
                or binding.get("contract") not in input_set
            ):
                raise ContractError(
                    "command_input_binding_invalid",
                    "input binding must exactly name one parser argument and registered contract",
                    subject=f"{subject}/input_bindings/{index}",
                )
            bound_arguments.append(binding["argument"])
            used_inputs.add(binding["contract"])
        if len(bound_arguments) != len(set(bound_arguments)):
            raise ContractError(
                "command_input_binding_invalid",
                "one parser argument cannot have multiple ingress contracts",
                subject=subject,
            )

        read_scopes = _unique_string_array(
            execution["read_scopes"], code="command_io_contract_invalid",
            subject=subject,
        )
        write_scopes = _unique_string_array(
            execution["write_scopes"], code="command_io_contract_invalid",
            subject=subject,
        )
        if (
            not set(read_scopes).issubset(scope_set)
            or not set(write_scopes).issubset(scope_set)
            or "module-source" not in read_scopes
            or "module-source" in write_scopes
            or input_bindings and "declared-inputs" not in read_scopes
        ):
            raise ContractError(
                "command_io_contract_invalid",
                "read/write scopes are unknown or violate source/input boundaries",
                subject=subject,
            )
        for scope in set(read_scopes) | set(write_scopes):
            resolver = scope_resolvers[scope]
            resolver_kind = resolver["kind"]
            if resolver_kind in {"argument", "argument-relative"} \
                    and resolver["argument"] not in roster:
                raise ContractError(
                    "command_io_contract_invalid",
                    "argument-backed scope is not resolvable by this parser",
                    subject=subject,
                )
            if resolver_kind == "argument-first" and not set(
                resolver["arguments"]
            ).intersection(roster):
                raise ContractError(
                    "command_io_contract_invalid",
                    "argument-first scope has no parser argument for this command",
                    subject=subject,
                )
            if resolver_kind == "workspace-genesis" and "--workspace" not in roster:
                raise ContractError(
                    "command_io_contract_invalid",
                    "workspace-genesis scope requires a workspace parser argument",
                    subject=subject,
                )
            if resolver_kind == "input-document-field" and (
                resolver["argument"] not in bound_arguments
                or kinds.get(resolver["argument"]) != "path"
            ):
                raise ContractError(
                    "command_io_contract_invalid",
                    "input-document-field scope requires a bound document path",
                    subject=subject,
                )
        used_scopes.update(read_scopes)
        used_scopes.update(write_scopes)

        locks = execution.get("locks")
        journals = execution.get("journals")
        if not isinstance(locks, list) or not isinstance(journals, list):
            raise ContractError(
                "command_io_contract_invalid", "locks and journals must be arrays",
                subject=subject,
            )
        lock_keys: list[tuple[str, str, str]] = []
        for index, lock in enumerate(locks):
            if (
                not isinstance(lock, dict)
                or set(lock) != {"scope", "path", "mode"}
                or lock.get("scope") not in scope_set
                or lock.get("mode") not in {"shared", "exclusive"}
                or lock.get("scope") not in (
                    set(read_scopes) | set(write_scopes)
                )
            ):
                raise ContractError(
                    "command_lock_contract_invalid", "lock contract is malformed",
                    subject=f"{subject}/locks/{index}",
                )
            _portable_contract_path(
                lock["path"], subject=subject, scope=lock["scope"],
            )
            lock_keys.append((lock["scope"], lock["path"], lock["mode"]))
            used_scopes.add(lock["scope"])
        if len(lock_keys) != len(set(lock_keys)):
            raise ContractError(
                "command_lock_contract_invalid", "lock contracts must be unique",
                subject=subject,
            )
        journal_keys: list[tuple[str, str, str]] = []
        for index, journal in enumerate(journals):
            journal_fields = (
                frozenset(journal) if isinstance(journal, dict) else frozenset()
            )
            if (
                not isinstance(journal, dict)
                or journal_fields not in {
                    frozenset({"scope", "path", "protocol"}),
                    frozenset({"scope", "path", "protocol", "when"}),
                }
                or journal.get("scope") not in scope_set
                or journal.get("protocol") not in {
                    "roll-forward", "rollback-capable",
                }
                or "when" in journal and (
                    not isinstance(journal["when"], str)
                    or re.fullmatch(r"[a-z][a-z0-9-]{2,127}", journal["when"])
                    is None
                )
                or journal["scope"] not in write_scopes
            ):
                raise ContractError(
                    "command_journal_contract_invalid", "journal contract is malformed",
                    subject=f"{subject}/journals/{index}",
                )
            _portable_contract_path(
                journal["path"], subject=subject, scope=journal["scope"],
            )
            journal_keys.append((
                journal["scope"], journal["path"], journal["protocol"],
            ))
            used_scopes.add(journal["scope"])
        if len(journal_keys) != len(set(journal_keys)):
            raise ContractError(
                "command_journal_contract_invalid", "journal contracts must be unique",
                subject=subject,
            )

        allowlist = _unique_string_array(
            execution["external_call_allowlist"],
            code="command_external_contract_invalid", subject=subject,
        )
        if not set(allowlist).issubset(external_set):
            raise ContractError(
                "command_external_contract_invalid",
                "external call allowlist names an unknown call class",
                subject=subject,
            )
        used_external.update(allowlist)
        boundaries = execution.get("mutation_boundaries")
        if (
            not isinstance(boundaries, dict)
            or set(boundaries) != {"module_source", "live_host"}
            or boundaries.get("module_source") != "forbidden"
            or boundaries.get("live_host") not in _LIVE_HOST_BOUNDARIES
        ):
            raise ContractError(
                "command_mutation_boundary_invalid",
                "mutation boundaries must explicitly forbid source and constrain host writes",
                subject=subject,
            )
        live = boundaries["live_host"]
        expected_writes = {
            "forbidden": set(),
            "governed-workspace-only": {"workspace"},
            "module-owned-install-paths-only": {"project-host"},
            "external-run-only": {"run-directory", "temporary-runtime"},
        }[live]
        if set(write_scopes) != expected_writes:
            raise ContractError(
                "command_mutation_boundary_invalid",
                "write scopes must exactly match the declared live-host boundary",
                subject=subject,
            )

    if used_scopes != scope_set:
        raise ContractError(
            "command_scope_registry_invalid",
            "scope registry must be exact-closure over execution contracts",
            subject="command-contracts.json#/scope_registry",
        )
    if used_inputs != input_set:
        raise ContractError(
            "command_input_contract_invalid",
            "input contract registry must be exact-closure over input bindings",
            subject="command-contracts.json#/input_contract_registry",
        )
    if used_external != external_set:
        raise ContractError(
            "command_external_contract_invalid",
            "external call registry must be exact-closure over allowlists",
            subject="command-contracts.json#/external_call_registry",
        )

    exceptions = document.get("authoritative_behavior_audit_exceptions")
    if not isinstance(exceptions, list):
        raise ContractError(
            "command_audit_exception_invalid", "audit exceptions must be an array",
        )
    exception_commands: list[str] = []
    for index, row in enumerate(exceptions):
        subject = (
            "command-contracts.json#/authoritative_behavior_audit_exceptions/"
            f"{index}"
        )
        if (
            not isinstance(row, dict)
            or set(row) != {"command", "reason", "evidence_kind"}
            or row.get("command") not in name_set
            or not isinstance(row.get("reason"), str)
            or not row["reason"]
            or not isinstance(row.get("evidence_kind"), str)
            or not row["evidence_kind"]
        ):
            raise ContractError(
                "command_audit_exception_invalid",
                "audit exceptions require an exact command/reason/evidence kind",
                subject=subject,
            )
        exception_commands.append(row["command"])
    if len(exception_commands) != len(set(exception_commands)):
        raise ContractError(
            "command_audit_exception_invalid", "audit exception commands must be unique",
        )
    return commands, command_event_names


def _validate_schema_definition(schema: Any, *, subject: str, pointer: str = "") -> None:
    """Fail closed on schema syntax the runtime does not execute."""

    if not isinstance(schema, dict):
        raise ContractError(
            "schema_definition_invalid",
            "runtime schemas must use object-form schema definitions",
            subject=subject,
            pointer=pointer,
        )
    unknown = set(schema) - _SCHEMA_KEYWORDS
    if unknown:
        key = sorted(unknown)[0]
        raise ContractError(
            "schema_keyword_unsupported",
            f"schema keyword is unsupported: {key}",
            subject=subject,
            pointer=f"{pointer}/{_ptr(key)}",
        )
    fmt = schema.get("format")
    if fmt is not None and fmt not in {"sha256", "date-time", "uri"}:
        raise ContractError(
            "schema_format_unsupported",
            f"schema format is unsupported: {fmt}",
            subject=subject,
            pointer=f"{pointer}/format",
        )
    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(keyword)
        if branches is not None:
            if not isinstance(branches, list) or not branches:
                raise ContractError(
                    "schema_definition_invalid",
                    f"{keyword} must be a non-empty schema array",
                    subject=subject,
                    pointer=f"{pointer}/{keyword}",
                )
            for index, branch in enumerate(branches):
                _validate_schema_definition(
                    branch, subject=subject,
                    pointer=f"{pointer}/{keyword}/{index}",
                )
    for keyword in ("not", "if", "then", "else", "items", "propertyNames"):
        branch = schema.get(keyword)
        if branch is not None:
            _validate_schema_definition(
                branch, subject=subject, pointer=f"{pointer}/{keyword}",
            )
    for keyword in ("properties", "patternProperties", "$defs", "definitions"):
        mapping = schema.get(keyword)
        if mapping is not None:
            if not isinstance(mapping, dict):
                raise ContractError(
                    "schema_definition_invalid",
                    f"{keyword} must be a schema mapping",
                    subject=subject,
                    pointer=f"{pointer}/{keyword}",
                )
            for key, branch in mapping.items():
                _validate_schema_definition(
                    branch, subject=subject,
                    pointer=f"{pointer}/{keyword}/{_ptr(str(key))}",
                )
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        _validate_schema_definition(
            additional, subject=subject,
            pointer=f"{pointer}/additionalProperties",
        )
    elif additional is not None and type(additional) is not bool:
        raise ContractError(
            "schema_definition_invalid",
            "additionalProperties must be a boolean or schema",
            subject=subject,
            pointer=f"{pointer}/additionalProperties",
        )


def _validate_closed_evidence_schema(
    schema: dict[str, Any], *, subject: str, pointer: str = "",
) -> None:
    """Require every object branch in an evidence value schema to be closed."""

    reference = schema.get("$ref")
    if isinstance(reference, str) and not reference.startswith("#/"):
        raise ContractError(
            "platform_evidence_schema_invalid",
            "platform evidence value schemas must be self-contained and may not reference another asset",
            subject=subject,
            pointer=f"{pointer}/$ref",
        )
    value_type = schema.get("type")
    object_typed = value_type == "object" or (
        isinstance(value_type, list) and "object" in value_type
    )
    object_shaped = object_typed or any(
        key in schema for key in ("properties", "patternProperties")
    )
    if object_shaped and schema.get("additionalProperties") is not False:
        raise ContractError(
            "platform_evidence_schema_open",
            "every object branch in a platform evidence value schema must set additionalProperties=false",
            subject=subject,
            pointer=pointer or "/",
        )
    for keyword in ("allOf", "anyOf", "oneOf"):
        for index, branch in enumerate(schema.get(keyword, [])):
            _validate_closed_evidence_schema(
                branch, subject=subject,
                pointer=f"{pointer}/{keyword}/{index}",
            )
    for keyword in ("not", "if", "then", "else", "items", "propertyNames"):
        branch = schema.get(keyword)
        if isinstance(branch, dict):
            _validate_closed_evidence_schema(
                branch, subject=subject, pointer=f"{pointer}/{keyword}",
            )
    for keyword in ("properties", "patternProperties", "$defs", "definitions"):
        mapping = schema.get(keyword, {})
        if isinstance(mapping, dict):
            for key, branch in mapping.items():
                _validate_closed_evidence_schema(
                    branch, subject=subject,
                    pointer=f"{pointer}/{keyword}/{_ptr(str(key))}",
                )


@dataclass(frozen=True)
class AssetBundle:
    root: Path
    documents: dict[str, Any]
    canonical_assets: dict[str, bytes]
    asset_digests: dict[str, str]
    engine_contract_digest: str

    @classmethod
    def load(cls, root: str | os.PathLike[str] | None = None) -> "AssetBundle":
        base = Path(root) if root else Path(__file__).resolve().parent.parent / "assets"
        owner_root = base.parent.parent if base.name == "assets" and base.parent.name == "sdm-lifecycle" else base
        return cls._load_from_roots(base, owner_root, strict_canonical=False)

    @classmethod
    def _load_from_roots(
        cls,
        base: Path,
        owner_root: Path,
        *,
        strict_canonical: bool,
    ) -> "AssetBundle":
        documents: dict[str, Any] = {}
        raw: dict[str, bytes] = {}
        for name in ASSET_FILES:
            try:
                source = _read_relative_regular(base, name)
                value = parse_document(source)
            except (OSError, CanonicalError, ContractError) as exc:
                raise ContractError("asset_load_failed", f"cannot load engine asset {name}: {exc}", subject=name) from exc
            documents[name] = value
            raw[name] = canonical_bytes(value)
            if strict_canonical and source != raw[name]:
                raise ContractError("asset_noncanonical", "pinned engine asset bytes are not canonical", subject=name)
        for name in SCHEMA_FILES:
            relative = f"schemas/{name}"
            try:
                source = _read_relative_regular(base, relative)
                value = parse_document(source)
            except (OSError, CanonicalError, ContractError) as exc:
                raise ContractError("asset_load_failed", f"cannot load engine schema {relative}: {exc}", subject=relative) from exc
            documents[relative] = value
            raw[relative] = canonical_bytes(value)
            if strict_canonical and source != raw[relative]:
                raise ContractError("asset_noncanonical", "pinned engine schema bytes are not canonical", subject=relative)
        require_supported_engine(documents)
        for binding in _owned_schema_bindings(documents):
            relative = binding["path"]
            if relative in documents:
                continue
            try:
                source = _read_relative_regular(owner_root, relative)
                value = parse_document(source)
            except (OSError, CanonicalError, ContractError) as exc:
                raise ContractError(
                    "owned_schema_load_failed",
                    f"cannot load owner schema {relative}: {exc}",
                    subject=relative,
                ) from exc
            canonical = canonical_bytes(value)
            if strict_canonical and source != canonical:
                raise ContractError("asset_noncanonical", "pinned owner schema bytes are not canonical", subject=relative)
            if digest_bytes(canonical) != binding["digest"]:
                raise ContractError(
                    "owned_schema_digest_mismatch",
                    "owner schema canonical bytes do not match the catalog binding digest",
                    subject=relative,
                )
            documents[relative] = value
            raw[relative] = canonical
        digests = {name: digest_bytes(value) for name, value in sorted(raw.items())}
        engine_digest = digest({"schema": "sdm-engine-contract/v1", "asset_digests": digests})
        bundle = cls(base, documents, raw, digests, engine_digest)
        bundle.self_validate()
        return bundle

    @classmethod
    def from_pinned(cls, bundle_root: str | os.PathLike[str], expected_digest: str) -> "AssetBundle":
        base = Path(bundle_root)
        # A pinned bundle is deliberately self-contained. Never fall back to
        # whichever sibling owner Skill happens to be installed now.
        bundle = cls._load_from_roots(base, base, strict_canonical=True)
        if bundle.engine_contract_digest != expected_digest:
            raise ContractError("engine_digest_mismatch", "pinned engine bundle digest does not match genesis", subject=str(bundle_root))
        return bundle

    def document(self, name: str) -> Any:
        try:
            return self.documents[name]
        except KeyError as exc:
            raise ContractError("unknown_asset", f"unknown engine asset: {name}", subject=name) from exc

    def asset_rule_digest(self, name: str) -> str:
        return self.asset_digests[name]

    def self_validate(self) -> None:
        require_supported_engine(self.documents)
        for name, schema in self.documents.items():
            if name.endswith(".schema.json"):
                _validate_schema_definition(schema, subject=name)
        primary = self.document("artifact-catalog.json")
        supporting = self.document("supporting-artifact-catalog.json")
        primary_rows = _rows(primary, "artifacts")
        supporting_rows = _rows(supporting, "artifacts")
        if tuple(row.get("id") for row in primary_rows) != PRIMARY_IDS:
            raise ContractError("artifact_catalog_mismatch", "primary catalog must contain G01-T01 exactly and in order", subject="artifact-catalog.json")
        if tuple(row.get("id") for row in supporting_rows) != SUPPORTING_IDS:
            raise ContractError("supporting_catalog_mismatch", "supporting catalog must contain the 16 retained IDs exactly and in order", subject="supporting-artifact-catalog.json")
        contracts = self.document("artifact-contracts.json")
        expected_validation_order = (
            "envelope-schema",
            "owner-schema",
            "catalog-entry",
            "identity-cardinality",
            "uri-digest-signature",
            "semantic-validator",
            "graph-edges",
            "transition-gate",
        )
        if tuple(contracts.get("validation_order", ())) != expected_validation_order:
            raise ContractError(
                "artifact_validation_order_invalid",
                "artifact validation_order must match the executable central/owner/common/semantic path",
            )
        common_rows = _rows(contracts, "common_rules")
        common_ids = [row.get("id") for row in common_rows if isinstance(row, dict)]
        if len(common_ids) != len(common_rows) or len(set(common_ids)) != len(common_ids) or any(not isinstance(item, str) for item in common_ids):
            raise ContractError("common_rule_mismatch", "common rule IDs must be present and unique")
        for row in common_rows:
            executor = row.get("executor") if isinstance(row, dict) else None
            if not isinstance(executor, str) or executor not in COMMON_RULE_EXECUTORS:
                raise ContractError("unknown_common_executor", f"common rule declares an unknown or unimplemented executor: {executor}")
        validator_rows = _rows(contracts, "semantic_validators")
        if any(not isinstance(row, dict) for row in validator_rows):
            raise ContractError(
                "semantic_validator_mismatch",
                "semantic validators must be object records",
            )
        validator_ids = [row.get("id") for row in validator_rows]
        if (
            any(not isinstance(item, str) or not item for item in validator_ids)
            or len(validator_ids) != len(set(validator_ids))
        ):
            raise ContractError("semantic_validator_mismatch", "semantic validator IDs must be present and unique")
        validators = set(validator_ids)
        for row in validator_rows:
            executor = row.get("executor")
            if not isinstance(executor, str) or executor not in SEMANTIC_EXECUTORS:
                raise ContractError("unknown_semantic_executor", f"semantic validator declares an unknown or unimplemented executor: {executor}")
            if SEMANTIC_EXECUTORS[executor] is _semantic_generic:
                raise ContractError(
                    "generic_semantic_executor_forbidden",
                    "declared artifact validators must resolve to a dedicated "
                    f"semantic executor, not the generic data-model check: {executor}",
                    subject=str(row.get("id", "")),
                )
            required = _declarative_string_list(
                row, "required", code="semantic_required_contract_invalid",
            )
            allow_empty = _declarative_string_list(
                row, "allow_empty",
                code="semantic_required_contract_invalid",
            )
            if not set(allow_empty).issubset(required):
                raise ContractError(
                    "semantic_required_contract_invalid",
                    "semantic required/allow_empty fields must be unique strings and allow_empty must be a subset of required",
                )
            required_ids = _declarative_string_list(
                row, "required_ids",
                code="semantic_required_ids_contract_invalid",
            )
            if any(item not in _ALL_ARTIFACT_IDS for item in required_ids):
                raise ContractError(
                    "semantic_required_ids_contract_invalid",
                    "semantic required_ids must name exact v1 artifact IDs",
                    subject=str(row.get("id", "")),
                )
            _declarative_string_list(
                row, "required_edges",
                code="semantic_required_edges_contract_invalid",
            )
        for row in primary_rows + supporting_rows:
            validator = row.get("semantic_validator")
            if validator not in validators:
                raise ContractError("unknown_semantic_validator", f"artifact {row.get('id')} declares unknown validator {validator}")
            schema = row.get("schema")
            if not isinstance(schema, str) or schema not in self.documents:
                raise ContractError("dangling_schema", f"artifact {row.get('id')} has dangling schema {schema}")
        bindings = _owned_schema_bindings(self.documents)
        by_path: dict[str, set[str]] = {}
        for binding in bindings:
            by_path.setdefault(binding["path"], set()).update(binding["artifact_ids"])
            if self.asset_digests.get(binding["path"]) != binding["digest"]:
                raise ContractError(
                    "owned_schema_digest_mismatch",
                    "loaded owner schema digest differs from its catalog declaration",
                    subject=binding["path"],
                )
        for path, artifact_ids in by_path.items():
            schema = self.document(path)
            properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
            discriminator = properties.get("artifact_type", {}) if isinstance(properties, dict) else {}
            declared = discriminator.get("enum") if isinstance(discriminator, dict) else None
            branches = schema.get("oneOf") if isinstance(schema, dict) else None
            branch_ids: list[str] = []
            if isinstance(branches, list):
                for branch in branches:
                    branch_properties = branch.get("properties", {}) if isinstance(branch, dict) else {}
                    type_rule = branch_properties.get("artifact_type", {}) if isinstance(branch_properties, dict) else {}
                    content_rule = branch_properties.get("content", {}) if isinstance(branch_properties, dict) else {}
                    artifact_type = type_rule.get("const") if isinstance(type_rule, dict) else None
                    required_content = content_rule.get("required") if isinstance(content_rule, dict) else None
                    if not isinstance(artifact_type, str) or not isinstance(required_content, list) or not required_content or any(not isinstance(item, str) for item in required_content):
                        raise ContractError(
                            "owned_schema_branch_invalid",
                            "every owner schema discriminator branch must constrain non-empty persisted content fields",
                            subject=path,
                        )
                    branch_ids.append(artifact_type)
            if (
                not isinstance(declared, list)
                or len(declared) != len(set(declared))
                or set(declared) != artifact_ids
                or len(branch_ids) != len(set(branch_ids))
                or set(branch_ids) != artifact_ids
            ):
                raise ContractError(
                    "owned_schema_artifact_coverage_invalid",
                    "owner schema discriminators must exactly cover their catalog-bound artifact IDs",
                    subject=path,
                )
        steps = _rows(self.document("lifecycle-steps.json"), "steps")
        if len(steps) != 21 or [row.get("step") for row in steps] != list(range(1, 22)):
            raise ContractError("lifecycle_rows_mismatch", "lifecycle steps must be exactly 1 through 21 in order")
        for row in steps:
            if not isinstance(row.get("gate"), dict):
                raise ContractError("invalid_gate_tree", f"step {row.get('step')} must contain an executable gate tree")
            validate_gate_shape(row["gate"])
        event_rows = _rows(self.document("event-types.json"), "event_types")
        event_name_rows: list[str] = []
        internal_event_names: set[str] = set()
        for index, row in enumerate(event_rows):
            subject = f"event-types.json#/event_types/{index}"
            if (
                not isinstance(row, dict)
                or not {"name", "effect"}.issubset(row)
                or not set(row).issubset({"name", "effect", "scope"})
                or not isinstance(row.get("name"), str)
                or not row["name"].strip()
                or not isinstance(row.get("effect"), str)
                or not row["effect"].strip()
                or (
                    "scope" in row
                    and row["scope"] != "internal-control-plane"
                )
            ):
                raise ContractError(
                    "event_type_contract_invalid",
                    "event rows require exact non-empty name/effect strings "
                    "and may only declare scope=internal-control-plane",
                    subject=subject,
                )
            event_name_rows.append(row["name"])
            if row.get("scope") == "internal-control-plane":
                internal_event_names.add(row["name"])
        if len(event_name_rows) != len(set(event_name_rows)):
            raise ContractError(
                "event_type_duplicate",
                "event registry names must be unique",
                subject="event-types.json",
            )
        event_names = set(event_name_rows)
        if not event_names:
            raise ContractError(
                "event_types_empty",
                "event-types.json must declare executable event types",
            )

        transition_rows = _rows(
            self.document("transition-requirements.json"), "transitions",
        )
        transition_names: list[str] = []
        transition_event_names: set[str] = set()
        for index, row in enumerate(transition_rows):
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("name"), str)
                or not row["name"].strip()
                or not isinstance(row.get("event_type"), str)
                or not row["event_type"].strip()
            ):
                raise ContractError(
                    "transition_contract_invalid",
                    "every transition requires non-empty name and event_type strings",
                    subject=(
                        "transition-requirements.json#/transitions/"
                        f"{index}"
                    ),
                )
            transition_names.append(row["name"])
            transition_event_names.add(row["event_type"])
            if row["event_type"] not in event_names:
                raise ContractError("transition_event_unknown", f"transition event type is not declared: {row['event_type']}")
            validate_gate_shape(normalize_gate(row.get("gate", {"check": "always"}), self))
        if len(transition_names) != len(set(transition_names)):
            raise ContractError(
                "transition_name_duplicate",
                "transition names must be unique; event types may be shared",
                subject="transition-requirements.json",
            )
        platform_document = self.document("platform-operations.json")
        if (
            not isinstance(platform_document, dict)
            or set(platform_document) != {
                "schema", "schema_version", "dispatch_semantics",
                "binding_resolution", "manual_evidence_bypass",
                "coordinator_executes_binding", "evidence_schema_profiles",
                "domains",
            }
            or platform_document.get("schema") != "sdm-platform-operations/v1"
            or platform_document.get("schema_version") != "1.0.0"
            or platform_document.get("dispatch_semantics") != "at-least-once"
            or platform_document.get("binding_resolution")
            != ".agents/skills/<configured-identity>/SKILL.md"
            or platform_document.get("manual_evidence_bypass") is not False
            or platform_document.get("coordinator_executes_binding") is not False
        ):
            raise ContractError(
                "platform_operation_contract_invalid",
                "platform operation registry metadata and top-level fields must be exact",
                subject="platform-operations.json",
            )
        evidence_profiles = platform_document.get("evidence_schema_profiles")
        if (
            not isinstance(evidence_profiles, dict)
            or not evidence_profiles
            or any(
                not isinstance(name, str)
                or re.fullmatch(r"[a-z][a-z0-9_]*", name) is None
                or not isinstance(schema, dict)
                for name, schema in evidence_profiles.items()
            )
        ):
            raise ContractError(
                "platform_evidence_schema_invalid",
                "evidence schema profiles must be a non-empty canonical name-to-schema mapping",
                subject="platform-operations.json#/evidence_schema_profiles",
            )
        for profile_name, profile_schema in evidence_profiles.items():
            profile_subject = (
                "platform-operations.json#/evidence_schema_profiles/"
                f"{profile_name}"
            )
            _validate_schema_definition(profile_schema, subject=profile_subject)
            _validate_closed_evidence_schema(
                profile_schema, subject=profile_subject,
            )

        domains = _rows(platform_document, "domains")
        if [row.get("domain") for row in domains] != ["data-platform", "source-control-platform", "ci-platform", "ml-execution-platform"]:
            raise ContractError("platform_domain_mismatch", "platform operation domains are missing, extra, or reordered")
        seen: set[str] = set()
        used_evidence_profiles: set[str] = set()
        for domain_index, domain in enumerate(domains):
            if (
                not isinstance(domain, dict)
                or set(domain) != {"domain", "binding_key", "operations"}
                or not isinstance(domain.get("binding_key"), str)
                or not domain["binding_key"]
                or not isinstance(domain.get("operations"), list)
                or not domain["operations"]
            ):
                raise ContractError(
                    "platform_operation_contract_invalid",
                    "platform domains require exactly domain, binding_key, and a non-empty operations array",
                    subject=f"platform-operations.json#/domains/{domain_index}",
                )
            for operation_index, operation in enumerate(domain["operations"]):
                subject = (
                    "platform-operations.json#/domains/"
                    f"{domain_index}/operations/{operation_index}"
                )
                if (
                    not isinstance(operation, dict)
                    or set(operation) != {
                        "name", "required_evidence", "evidence_cardinality",
                        "subject_requirements", "evidence_value_profiles",
                    }
                ):
                    raise ContractError(
                        "platform_operation_contract_invalid",
                        "platform operations require exact identity, evidence, cardinality, subject, and evidence-value profile fields",
                        subject=subject,
                    )
                name = operation.get("name")
                required_evidence = operation.get("required_evidence")
                cardinality = operation.get("evidence_cardinality")
                requirements = operation.get("subject_requirements")
                value_profiles = operation.get("evidence_value_profiles")
                if (
                    not isinstance(name, str)
                    or not re.fullmatch(
                        r"(?:data|git|jenkins|ml)\.[a-z][a-z0-9-]*",
                        name,
                    )
                    or name in seen
                    or not isinstance(required_evidence, list)
                    or not required_evidence
                    or len(required_evidence) != len(set(required_evidence))
                    or any(
                        not isinstance(item, str) or not item
                        for item in required_evidence
                    )
                    or not isinstance(cardinality, dict)
                    or set(cardinality) != {"default"}
                ):
                    raise ContractError(
                        "platform_operation_contract_invalid",
                        "platform operation identity, evidence list, and cardinality coverage must be exact",
                        subject=subject,
                    )
                if (
                    not isinstance(value_profiles, dict)
                    or not value_profiles
                    or any(
                        profile_name not in evidence_profiles
                        or not isinstance(names, list)
                        or not names
                        or any(
                            not isinstance(item, str) or not item
                            for item in names
                        )
                        for profile_name, names in value_profiles.items()
                    )
                ):
                    raise ContractError(
                        "platform_evidence_schema_invalid",
                        "every operation must assign evidence names to known non-empty schema profiles",
                        subject=f"{subject}/evidence_value_profiles",
                    )
                used_evidence_profiles.update(value_profiles)
                assigned_names = [
                    item
                    for names in value_profiles.values()
                    for item in names
                ]
                if (
                    len(assigned_names) != len(set(assigned_names))
                    or set(assigned_names) != set(required_evidence)
                ):
                    raise ContractError(
                        "platform_evidence_schema_invalid",
                        "operation evidence schema assignments must exactly and uniquely cover required_evidence",
                        subject=f"{subject}/evidence_value_profiles",
                    )
                limits = cardinality["default"]
                if (
                    not isinstance(limits, dict)
                    or set(limits) != {"min", "max"}
                    or type(limits.get("min")) is not int
                    or type(limits.get("max")) is not int
                    or limits["min"] < 1
                    or limits["max"] < limits["min"]
                ):
                    raise ContractError(
                        "platform_operation_contract_invalid",
                        "required evidence needs default integer min/max cardinality with 1 <= min <= max",
                        subject=f"{subject}/evidence_cardinality/default",
                    )
                if (
                    not isinstance(requirements, dict)
                    or not requirements
                    or not set(requirements).issubset({"default", "by_step"})
                    or "by_step" in requirements
                    and not isinstance(requirements["by_step"], dict)
                ):
                    raise ContractError(
                        "platform_operation_contract_invalid",
                        "subject_requirements must declare default and/or by_step rules",
                        subject=f"{subject}/subject_requirements",
                    )
                rules: list[tuple[str, Any]] = []
                if "default" in requirements:
                    rules.append(("default", requirements["default"]))
                for step, rule in requirements.get("by_step", {}).items():
                    if (
                        not isinstance(step, str)
                        or not step.isdigit()
                        or not 1 <= int(step) <= 21
                    ):
                        raise ContractError(
                            "platform_operation_contract_invalid",
                            "subject requirement step keys must be canonical lifecycle steps 1..21",
                            subject=f"{subject}/subject_requirements/by_step/{step}",
                        )
                    rules.append((f"by_step/{step}", rule))
                if not rules:
                    raise ContractError(
                        "platform_operation_contract_invalid",
                        "subject_requirements must contain at least one executable rule",
                        subject=f"{subject}/subject_requirements",
                    )
                for rule_name, rule in rules:
                    if (
                        not isinstance(rule, dict)
                        or set(rule) != {"required", "required_any"}
                        or any(
                            not isinstance(values, list)
                            or len(values) != len(set(values))
                            or any(
                                not isinstance(key, str)
                                or not re.fullmatch(r"[a-z][a-z0-9_]*", key)
                                for key in values
                            )
                            for values in (
                                rule.get("required"), rule.get("required_any"),
                            )
                        )
                        or not rule["required"] and not rule["required_any"]
                    ):
                        raise ContractError(
                            "platform_operation_contract_invalid",
                            "subject rules require exact required/required_any key arrays and at least one anchor",
                            subject=f"{subject}/subject_requirements/{rule_name}",
                        )
                seen.add(name)
        if used_evidence_profiles != set(evidence_profiles):
            raise ContractError(
                "platform_evidence_schema_invalid",
                "evidence schema profiles must all be used by at least one operation",
                subject="platform-operations.json#/evidence_schema_profiles",
            )
        commands, command_event_names = _validate_command_contract_document(
            self.document("command-contracts.json"), self.documents, event_names,
        )
        reachable_event_names = (
            transition_event_names
            | command_event_names
            | internal_event_names
        )
        if event_names != reachable_event_names:
            raise ContractError(
                "event_registry_closure_mismatch",
                "event registry must exactly equal transition events, "
                "command emitted events, and internal-control-plane events; "
                f"unreachable={sorted(event_names - reachable_event_names)}, "
                f"missing={sorted(reachable_event_names - event_names)}",
                subject="event-types.json",
            )

    def artifact_contract(self, artifact_id: str) -> dict[str, Any]:
        source = "supporting-artifact-catalog.json" if artifact_id.startswith("S") else "artifact-catalog.json"
        for row in _rows(self.document(source), "artifacts"):
            if row.get("id") == artifact_id:
                return row
        raise ContractError("unknown_artifact", f"unknown governed artifact ID: {artifact_id}", subject=artifact_id)

    def validate_artifact(self, artifact_id: str, value: Any) -> list[dict[str, Any]]:
        contract = self.artifact_contract(artifact_id)
        schema_name = contract["schema"]
        structural_findings = validate_schema(
            value, self.document(schema_name), subject=artifact_id,
            governing_asset=schema_name, registry=self.documents,
        )
        owner_schema = self.owner_schema_for_artifact(artifact_id)
        if owner_schema is not None:
            structural_findings.extend(validate_schema(
                value,
                self.document(owner_schema),
                subject=artifact_id,
                governing_asset=owner_schema,
                registry=self.documents,
            ))
        findings = list(structural_findings)
        for rule in _rows(self.document("artifact-contracts.json"), "common_rules"):
            findings.extend(COMMON_RULE_EXECUTORS[rule["executor"]](value, contract, rule, self))
        # Semantic executors operate on the closed shapes established by the
        # central and owner schemas.  Running them after a structural failure
        # can turn an ordinary malformed value into a TypeError (for example,
        # a mapping where a canonical string set is required).  The schema
        # findings already fail the artifact closed, so do not interpret an
        # invalid shape further.
        if structural_findings:
            return sort_findings(findings)
        validator_id = contract["semantic_validator"]
        validator = next(row for row in _rows(self.document("artifact-contracts.json"), "semantic_validators") if row.get("id") == validator_id)
        try:
            findings.extend(_execute_semantic_validator(value, contract, validator, self))
        except ContractError as exc:
            findings.append(finding(
                exc.code, str(exc), subject=artifact_id,
                pointer=exc.pointer or "/content",
                governing_asset="artifact-contracts.json",
                gate_impact=exc.gate_impact,
            ))
        except (CanonicalError, KeyError, TypeError, ValueError) as exc:
            # Owner schemas intentionally leave some artifact-specific nested
            # values to semantic executors.  Those executors must still be a
            # total, fail-closed function over untrusted JSON values.
            findings.append(finding(
                "semantic_input_invalid",
                f"artifact semantic input is invalid: {type(exc).__name__}",
                subject=artifact_id, pointer="/content",
                governing_asset="artifact-contracts.json",
            ))
        return sort_findings(findings)

    def owner_schema_for_artifact(self, artifact_id: str) -> str | None:
        matches = [
            binding["path"]
            for binding in _owned_schema_bindings(self.documents)
            if artifact_id in binding["artifact_ids"]
        ]
        if len(matches) > 1:
            raise ContractError(
                "owned_schema_artifact_coverage_invalid",
                "artifact resolves to more than one owner schema",
                subject=artifact_id,
            )
        return matches[0] if matches else None

    def event_types(self) -> set[str]:
        result: set[str] = set()
        for row in _rows(self.document("event-types.json"), "event_types"):
            result.add(row if isinstance(row, str) else row.get("name", ""))
        result.discard("")
        return result

    def operation_contract(self, operation_name: str) -> tuple[str, dict[str, Any], str]:
        platform_document = self.document("platform-operations.json")
        profiles = platform_document["evidence_schema_profiles"]
        for domain in _rows(platform_document, "domains"):
            for operation in domain.get("operations", []):
                record = {"name": operation} if isinstance(operation, str) else operation
                if record.get("name") == operation_name:
                    # Profiles are only an authoring compression in the sole
                    # normative asset.  Runtime callers receive an
                    # operation-owned, fully materialized schema map, and the
                    # S12 adapter_contract_digest therefore binds the exact
                    # schema bytes rather than an ambient profile lookup.
                    materialized = deepcopy(record)
                    assignments = materialized["evidence_value_profiles"]
                    by_name = {
                        evidence_name: profile_name
                        for profile_name, names in assignments.items()
                        for evidence_name in names
                    }
                    materialized["evidence_value_schemas"] = {
                        evidence_name: deepcopy(profiles[by_name[evidence_name]])
                        for evidence_name in materialized["required_evidence"]
                    }
                    return domain["domain"], materialized, domain.get("binding_key", domain["domain"].replace("-", "_") + "_skill")
        raise ContractError("operation_not_allowed", f"operation is not in the pinned platform allowlist: {operation_name}", subject=operation_name)

    def transition(self, name: str) -> dict[str, Any]:
        for row in _rows(self.document("transition-requirements.json"), "transitions"):
            if row.get("name") == name:
                result = dict(row)
                result["gate"] = normalize_gate(row.get("gate", {"check": "always"}), self)
                return result
        raise ContractError("unknown_transition", f"transition is not declared: {name}", subject=name)

    def command(self, name: str) -> dict[str, Any]:
        for row in _rows(self.document("command-contracts.json"), "commands"):
            if row.get("name") == name:
                return row
        raise ContractError("unknown_command_contract", f"command has no declared contract: {name}", subject=name)


def _rows(value: Any, key: str) -> list[Any]:
    if not isinstance(value, dict) or not isinstance(value.get(key), list):
        raise ContractError("invalid_asset_shape", f"asset must contain an array named {key}")
    return value[key]


def _type_ok(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float, Decimal)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    if isinstance(value, float) and not isinstance(value, bool):
        return Decimal(str(value))
    raise TypeError("value is not a JSON number")


def _strict_rfc3339(value: str) -> bool:
    if _RFC3339.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _strict_absolute_uri(value: str) -> bool:
    if any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return bool(
        re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*", parsed.scheme)
        and (parsed.netloc or parsed.path)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    *,
    subject: str = "",
    governing_asset: str = "schema",
    pointer: str = "",
    registry: dict[str, Any] | None = None,
    root_schema: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate the JSON-Schema subset used by the module's central schemas."""
    findings: list[dict[str, Any]] = []
    root_schema = root_schema or schema
    reference = schema.get("$ref")
    if isinstance(reference, str):
        if reference.startswith("#/"):
            resolved: Any = root_schema
            try:
                for part in reference[2:].split("/"):
                    resolved = resolved[part.replace("~1", "/").replace("~0", "~")]
            except (KeyError, TypeError) as exc:
                return [finding("schema_ref_unresolved", f"schema reference is unresolved: {reference}", subject=subject, pointer=pointer, governing_asset=governing_asset)]
        elif registry is not None:
            candidates = (reference, f"schemas/{reference}")
            resolved = next((registry[name] for name in candidates if name in registry), None)
            if resolved is None:
                return [finding("schema_ref_unresolved", f"schema reference is unresolved: {reference}", subject=subject, pointer=pointer, governing_asset=governing_asset)]
        else:
            return [finding("schema_ref_unresolved", f"schema reference is unresolved: {reference}", subject=subject, pointer=pointer, governing_asset=governing_asset)]
        return validate_schema(value, resolved, subject=subject, governing_asset=governing_asset, pointer=pointer, registry=registry, root_schema=resolved if not reference.startswith("#/") else root_schema)
    rule_digest = digest(schema)

    def add(code: str, message: str, at: str = pointer) -> None:
        findings.append(finding(code, message, subject=subject, pointer=at, governing_asset=governing_asset, rule_digest=rule_digest))

    expected = schema.get("type")
    expected_types = [expected] if isinstance(expected, str) else expected
    if isinstance(expected_types, list) and expected_types and not any(_type_ok(value, item) for item in expected_types):
        add("schema_type", f"expected type {expected_types}, got {type(value).__name__}")
        return findings
    if "const" in schema and value != schema["const"]:
        add("schema_const", "value does not equal the required constant")
    if "enum" in schema and value not in schema["enum"]:
        add("schema_enum", "value is not one of the allowed enum values")
    if "not" in schema:
        negated = validate_schema(
            value, schema["not"], subject=subject,
            governing_asset=governing_asset, pointer=pointer,
            registry=registry, root_schema=root_schema,
        )
        if not negated:
            add("schema_not", "not constraint is not satisfied")
    if "if" in schema:
        condition = validate_schema(
            value, schema["if"], subject=subject,
            governing_asset=governing_asset, pointer=pointer,
            registry=registry, root_schema=root_schema,
        )
        branch_name = "then" if not condition else "else"
        if branch_name in schema:
            findings.extend(validate_schema(
                value, schema[branch_name], subject=subject,
                governing_asset=governing_asset, pointer=pointer,
                registry=registry, root_schema=root_schema,
            ))
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword in schema:
            branches = [validate_schema(value, item, subject=subject, governing_asset=governing_asset, pointer=pointer, registry=registry, root_schema=root_schema) for item in schema[keyword]]
            passes = sum(not branch for branch in branches)
            required = len(branches) if keyword == "allOf" else 1
            okay = passes == required if keyword in ("allOf", "oneOf") else passes >= 1
            if not okay:
                add("schema_composition", f"{keyword} constraint is not satisfied")
    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                add("schema_required", f"required property is missing: {key}", f"{pointer}/{_ptr(key)}")
        properties = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        for key, item in value.items():
            child = f"{pointer}/{_ptr(key)}"
            matched = False
            if key in properties:
                findings.extend(validate_schema(item, properties[key], subject=subject, governing_asset=governing_asset, pointer=child, registry=registry, root_schema=root_schema))
                matched = True
            for pattern, subschema in patterns.items():
                if re.search(pattern, key):
                    findings.extend(validate_schema(item, subschema, subject=subject, governing_asset=governing_asset, pointer=child, registry=registry, root_schema=root_schema))
                    matched = True
            if not matched and schema.get("additionalProperties") is False:
                add("schema_unknown_field", f"unknown property: {key}", child)
            elif not matched and isinstance(schema.get("additionalProperties"), dict):
                findings.extend(validate_schema(item, schema["additionalProperties"], subject=subject, governing_asset=governing_asset, pointer=child, registry=registry, root_schema=root_schema))
        property_names = schema.get("propertyNames")
        if isinstance(property_names, dict):
            for key in value:
                findings.extend(validate_schema(key, property_names, subject=subject, governing_asset=governing_asset, pointer=f"{pointer}/{_ptr(key)}", registry=registry, root_schema=root_schema))
        for key, dependents in schema.get("dependentRequired", {}).items():
            if key in value:
                for dependent in dependents:
                    if dependent not in value:
                        add("schema_dependent_required", f"{dependent} is required when {key} is present", f"{pointer}/{_ptr(dependent)}")
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            add("schema_min_properties", "object has too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            add("schema_max_properties", "object has too many properties")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            add("schema_min_items", "array has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            add("schema_max_items", "array has too many items")
        if schema.get("uniqueItems") and len({canonical_bytes(item) for item in value}) != len(value):
            add("schema_unique_items", "array items must be unique")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                findings.extend(validate_schema(item, schema["items"], subject=subject, governing_asset=governing_asset, pointer=f"{pointer}/{index}", registry=registry, root_schema=root_schema))
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            add("schema_min_length", "string is shorter than allowed")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            add("schema_max_length", "string is longer than allowed")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            add("schema_pattern", "string does not match required pattern")
        fmt = schema.get("format")
        if fmt == "sha256" and not _DIGEST.fullmatch(value):
            add("bad_digest", "value is not a canonical sha256 digest")
        elif fmt == "date-time":
            if not _strict_rfc3339(value):
                add("bad_timestamp", "value is not a strict timezone-bearing RFC 3339 date-time")
        elif fmt == "uri" and not _strict_absolute_uri(value):
            add("bad_uri", "value is not a credential-free absolute URI without query or fragment")
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        number = _as_decimal(value)
        if "minimum" in schema and number < _as_decimal(schema["minimum"]):
            add("schema_minimum", "number is below the minimum")
        if "maximum" in schema and number > _as_decimal(schema["maximum"]):
            add("schema_maximum", "number is above the maximum")
        if "exclusiveMinimum" in schema and number <= _as_decimal(schema["exclusiveMinimum"]):
            add("schema_exclusive_minimum", "number is not above the exclusive minimum")
        if "exclusiveMaximum" in schema and number >= _as_decimal(schema["exclusiveMaximum"]):
            add("schema_exclusive_maximum", "number is not below the exclusive maximum")
    return findings


def _ptr(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _nested_fields(value: Any, pointer: str = "") -> Iterable[tuple[str, Any, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            safe_key = key if isinstance(key, str) else "<non-string-key>"
            child = f"{pointer}/{_ptr(safe_key)}"
            yield safe_key, item, child
            yield from _nested_fields(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _nested_fields(item, f"{pointer}/{index}")


def _common_finding(
    code: str,
    message: str,
    contract: dict[str, Any],
    rule: dict[str, Any],
    *,
    pointer: str = "",
) -> dict[str, Any]:
    return finding(
        code,
        message,
        subject=contract["id"],
        pointer=pointer,
        governing_asset="artifact-contracts.json",
        rule_digest=digest(rule),
        gate_impact=str(rule.get("gate_impact", "block")),
    )


def _common_require_catalog_entry(
    value: Any,
    contract: dict[str, Any],
    rule: dict[str, Any],
    bundle: "AssetBundle",
) -> list[dict[str, Any]]:
    resolved = bundle.artifact_contract(contract["id"])
    if resolved != contract:
        return [_common_finding("catalog_entry_mismatch", "artifact contract does not equal the pinned catalog entry", contract, rule)]
    if contract.get("schema") == "schemas/artifact.schema.json" and (
        not isinstance(value, dict) or value.get("artifact_type") != contract["id"]
    ):
        return [_common_finding("artifact_type_mismatch", "persisted artifact_type does not match the selected catalog entry", contract, rule, pointer="/artifact_type")]
    return []


def _common_validate_instance_key(
    value: Any,
    contract: dict[str, Any],
    rule: dict[str, Any],
    bundle: "AssetBundle",
) -> list[dict[str, Any]]:
    del bundle
    if contract.get("schema") != "schemas/artifact.schema.json":
        return []
    instance_key = value.get("instance_key") if isinstance(value, dict) else None
    if not isinstance(instance_key, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", instance_key) is None:
        return [_common_finding("instance_key_invalid", "instance_key must be a portable, non-path identity", contract, rule, pointer="/instance_key")]
    return []


def _common_validate_cardinality(
    value: Any,
    contract: dict[str, Any],
    rule: dict[str, Any],
    bundle: "AssetBundle",
) -> list[dict[str, Any]]:
    del bundle
    cardinality = contract.get("cardinality")
    if not isinstance(cardinality, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", cardinality) is None:
        return [_common_finding("cardinality_contract_invalid", "catalog cardinality is not executable", contract, rule)]
    if contract.get("schema") == "schemas/artifact.schema.json":
        version = value.get("version") if isinstance(value, dict) else None
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            return [_common_finding("cardinality_version_invalid", "governed artifact cardinality requires a positive version", contract, rule, pointer="/version")]
    return []


def _common_validate_immutability(
    value: Any,
    contract: dict[str, Any],
    rule: dict[str, Any],
    bundle: "AssetBundle",
) -> list[dict[str, Any]]:
    del bundle
    boundary = contract.get("immutable_boundary")
    if not isinstance(boundary, str) or not boundary:
        return [_common_finding("immutable_boundary_invalid", "catalog immutable_boundary must be explicit", contract, rule)]
    if isinstance(value, dict):
        identity = value.get("artifact_id")
        for key in ("supersedes", "superseded_by"):
            if identity is not None and value.get(key) == identity:
                return [_common_finding("immutable_self_supersession", "an immutable artifact cannot supersede itself", contract, rule, pointer=f"/{key}")]
    return []


def _common_validate_uri(
    value: Any,
    contract: dict[str, Any],
    rule: dict[str, Any],
    bundle: "AssetBundle",
) -> list[dict[str, Any]]:
    del bundle
    findings: list[dict[str, Any]] = []
    if contract.get("schema") == "schemas/artifact.schema.json" and contract.get("uri_rules", {}).get("required") is True:
        if not isinstance(value, dict) or not _immutable_uri_value(value.get("uri")):
            findings.append(_common_finding("artifact_uri_required", "catalog requires an immutable artifact URI", contract, rule, pointer="/uri"))
    for key, item, pointer in _nested_fields(value):
        if key != "uri" and not key.endswith("_uri"):
            continue
        if not _immutable_uri_value(item):
            findings.append(_common_finding("unsafe_uri", f"{key} must be immutable and query-free", contract, rule, pointer=pointer))
    return findings


def _immutable_uri_value(value: Any) -> bool:
    """Return whether a URI is absolute, stable, and contains an identity.

    Merely containing a colon is not sufficient: values such as ``":"`` or
    ``"x:"`` have no referent.  Query/fragment material is intentionally
    excluded because signed URLs and floating selectors are not immutable
    artifact identities.
    """

    if (
        not isinstance(value, str) or not value or value != value.strip()
        or any(character.isspace() or ord(character) < 0x20 for character in value)
        or "\\" in value
    ):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if (
        re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*", parsed.scheme or "") is None
        or parsed.query or parsed.fragment
        or parsed.username is not None or parsed.password is not None
        or not (parsed.netloc or parsed.path)
    ):
        return False
    path = PurePosixPath(parsed.path) if parsed.path else None
    if path is not None and any(part in {".", ".."} for part in path.parts):
        return False
    return True


def _declared_devspace_marker(root: Any, pointer: str, key: str, item: Any) -> bool:
    if item != 'not-provided' or key not in {'container_image_digest', 'container_image_signature_digest', 'expected_runtime_image_digest'}:
        return False
    parent = root
    try:
        for token in pointer.split('/')[1:-1]:
            token = token.replace('~1', '/').replace('~0', '~')
            parent = parent[int(token)] if isinstance(parent, list) else parent[token]
        if not isinstance(parent, dict): return False
        if parent.get('schema') == DEVSPACE_RUNTIME_SCHEMA:
            canonical_runtime_environment_manifest(parent)
            return True
        if parent.get('schema') == DEVSPACE_PROFILE_SCHEMA and key == 'container_image_signature_digest':
            _canonical_runtime_profile(parent)
            return True
        if key == 'expected_runtime_image_digest':
            profile = parent.get('expected_runtime_profile')
            _canonical_runtime_profile(profile)
            return runtime_image_binding_valid(item, profile)
    except (ContractError, KeyError, IndexError, TypeError, ValueError):
        return False
    return False


def _common_validate_digest(
    value: Any,
    contract: dict[str, Any],
    rule: dict[str, Any],
    bundle: "AssetBundle",
) -> list[dict[str, Any]]:
    del bundle
    findings: list[dict[str, Any]] = []
    if isinstance(value, dict) and "content" in value and "content_digest" in value:
        try:
            actual = digest(value["content"])
        except CanonicalError as exc:
            findings.append(_common_finding(exc.code, str(exc), contract, rule, pointer="/content"))
        else:
            if value["content_digest"] != actual:
                findings.append(_common_finding("content_digest_mismatch", "artifact content_digest does not bind canonical content bytes", contract, rule, pointer="/content_digest"))
    for key, item, pointer in _nested_fields(value):
        if _declared_devspace_marker(value, pointer, key, item):
            continue
        if (key == "digest" or key.endswith("_digest")) and (
            not isinstance(item, str) or _DIGEST.fullmatch(item) is None
        ):
            findings.append(_common_finding("bad_digest", f"{key} is not a canonical sha256 digest", contract, rule, pointer=pointer))
    return findings


def _common_validate_signature(
    value: Any,
    contract: dict[str, Any],
    rule: dict[str, Any],
    bundle: "AssetBundle",
) -> list[dict[str, Any]]:
    del bundle
    findings: list[dict[str, Any]] = []
    for key, item, pointer in _nested_fields(value):
        if key != "signature" and not key.endswith("_signature"):
            continue
        if not isinstance(item, str) or not item or re.fullmatch(r"[A-Za-z0-9+/]+", item) is None:
            findings.append(_common_finding("signature_encoding_invalid", "signature must be strict unpadded base64", contract, rule, pointer=pointer))
    return findings


def _common_validate_extensions(
    value: Any,
    contract: dict[str, Any],
    rule: dict[str, Any],
    bundle: "AssetBundle",
) -> list[dict[str, Any]]:
    del bundle
    extensions = value.get("extensions") if isinstance(value, dict) else None
    if extensions is None:
        return []
    if not isinstance(extensions, dict):
        return [_common_finding("extensions_invalid", "extensions must be an object", contract, rule, pointer="/extensions")]
    return [
        _common_finding("extension_key_invalid", "extension keys must be reverse-DNS names", contract, rule, pointer=f"/extensions/{_ptr(key)}")
        for key in extensions
        if not isinstance(key, str) or _EXTENSION_KEY.fullmatch(key) is None
    ]


def _common_reject_secret_fields(
    value: Any,
    contract: dict[str, Any],
    rule: dict[str, Any],
    bundle: "AssetBundle",
) -> list[dict[str, Any]]:
    del bundle
    return [
        _common_finding("secret_field_forbidden", "governed artifacts may not persist secret-bearing fields", contract, rule, pointer=pointer)
        for key, _item, pointer in _nested_fields(value)
        if _SECRET_FIELD.fullmatch(key)
    ]


COMMON_RULE_EXECUTORS: dict[
    str,
    Callable[[Any, dict[str, Any], dict[str, Any], "AssetBundle"], list[dict[str, Any]]],
] = {
    "contracts.require_catalog_entry": _common_require_catalog_entry,
    "contracts.validate_instance_key": _common_validate_instance_key,
    "contracts.validate_cardinality": _common_validate_cardinality,
    "contracts.validate_immutability": _common_validate_immutability,
    "contracts.validate_contained_or_immutable_uri": _common_validate_uri,
    "contracts.validate_digest": _common_validate_digest,
    "authority.validate_when_required": _common_validate_signature,
    "contracts.validate_reverse_dns_extensions": _common_validate_extensions,
    "contracts.reject_secret_fields": _common_reject_secret_fields,
}


def _semantic_generic(value: Any, contract: dict[str, Any], bundle: AssetBundle) -> list[dict[str, Any]]:
    try:
        validate_data_model(value)
    except CanonicalError as exc:
        return [finding(exc.code, str(exc), subject=contract["id"], pointer=exc.pointer, governing_asset="artifact-contracts.json")]
    return []


def _semantic_content(value: Any) -> Any:
    return value.get("content", value) if isinstance(value, dict) else value


def _semantic_problem(
    contract: dict[str, Any], code: str, message: str, pointer: str,
) -> dict[str, Any]:
    return finding(
        code, message, subject=contract["id"], pointer=pointer,
        governing_asset="artifact-contracts.json",
    )


def _semantic_digest_fields(
    content: dict[str, Any], contract: dict[str, Any], fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    return [
        _semantic_problem(
            contract, "bad_digest", f"{name} must be a canonical sha256 digest",
            f"/content/{_ptr(name)}",
        )
        for name in fields
        if not isinstance(content.get(name), str)
        or _DIGEST.fullmatch(content[name]) is None
    ]


def _semantic_source_refs(
    content: dict[str, Any], contract: dict[str, Any], *, required: bool = True,
) -> list[dict[str, Any]]:
    refs = content.get("source_refs")
    if refs is None and not required:
        return []
    if (
        not isinstance(refs, dict) or not refs
        or any(
            not isinstance(node, str) or _ARTIFACT_REF.fullmatch(node) is None
            or node.split(":", 1)[0] not in _ALL_ARTIFACT_IDS
            or not isinstance(bound, str) or _DIGEST.fullmatch(bound) is None
            for node, bound in refs.items()
        )
    ):
        return [_semantic_problem(
            contract, "source_refs_invalid",
            "source_refs must bind exact artifact instance identities to canonical digests",
            "/content/source_refs",
        )]
    return []


def _semantic_contract_bundle(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_source_refs(content, contract))
    references = content.get("references")
    if not isinstance(references, list) or any(
        not isinstance(item, str) or _ARTIFACT_REF.fullmatch(item) is None
        for item in references
    ):
        findings.append(_semantic_problem(
            contract, "reference_identity_not_exact",
            "G02 references must name immutable artifact instances, not directory or type summaries",
            "/content/references",
        ))
    elif references != sorted(references) or len(references) != len(set(references)):
        findings.append(_semantic_problem(
            contract, "reference_set_not_canonical",
            "G02 references must be a canonical duplicate-free exact identity set",
            "/content/references",
        ))
    return findings


def _semantic_author_principals(
    content: dict[str, Any], contract: dict[str, Any],
) -> list[dict[str, Any]]:
    authors = content.get("author_principal_ids")
    if (
        not isinstance(authors, list)
        or not authors
        or any(not isinstance(author, str) or not author for author in authors)
        or authors != sorted(set(authors))
    ):
        return [_semantic_problem(
            contract, "artifact_authors_invalid",
            "artifact must bind a non-empty canonical set of stable author principal IDs",
            "/content/author_principal_ids",
        )]
    return []


def _semantic_principal_set(
    content: dict[str, Any], contract: dict[str, Any], field: str,
    role: str,
) -> list[dict[str, Any]]:
    principals = content.get(field)
    if (
        not isinstance(principals, list)
        or not principals
        or principals != sorted(set(principals))
        or any(
            not isinstance(principal, str) or not principal
            for principal in principals
        )
    ):
        return [_semantic_problem(
            contract, f"{role}_principals_invalid",
            f"{field} must be a non-empty canonical set of stable principal IDs",
            f"/content/{_ptr(field)}",
        )]
    return []


def _semantic_data_contract(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    if any(
        not isinstance(content.get(name), str) or not content[name]
        for name in ("dataset_identity", "version")
    ) or any(
        not isinstance(content.get(name), (dict, list)) or not content[name]
        for name in ("schema", "quality_policy", "lineage")
    ):
        findings.append(_semantic_problem(
            contract, "data_contract_not_executable",
            "G03 must carry concrete dataset/version/schema/quality/lineage facts",
            "/content",
        ))
    findings.extend(_semantic_source_refs(content, contract))
    return findings


def _semantic_evaluation_contract(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    metrics = content.get("metric_definitions")
    slices = content.get("slices")
    tolerances = content.get("tolerances")
    inconclusive = content.get("inconclusive_rule")
    if (
        not _meaningful_text(content.get("evaluator_identity"))
        or not isinstance(metrics, dict) or not metrics
        or any(
            not _meaningful_text(name)
            or not _semantic_value_present(definition)
            or isinstance(definition, bool)
            for name, definition in metrics.items()
        )
        or not _unique_texts(slices)
        or not _nonnegative_numeric_leaf(tolerances)
        or not _meaningful_text(inconclusive, minimum=8)
    ):
        findings.append(_semantic_problem(
            contract, "evaluation_contract_not_executable",
            "G10 must carry a concrete evaluator identity and complete metric/slice/tolerance/inconclusive semantics",
            "/content",
        ))
    findings.extend(_semantic_source_refs(content, contract))
    findings.extend(_semantic_principal_set(
        content, contract, "owner_principal_ids", "evaluator_owner",
    ))
    return findings


def _semantic_baseline_lock(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_digest_fields(content, contract, (
        "baseline_digest", "data_digest", "evaluator_digest",
        "baseline_fingerprint_digest",
    )))
    fingerprint = content.get("baseline_fingerprint")
    if (
        not isinstance(fingerprint, dict)
        or not fingerprint
        or content.get("baseline_fingerprint_digest") != digest(fingerprint)
    ):
        findings.append(_semantic_problem(
            contract, "baseline_fingerprint_invalid",
            "B02 must bind the canonical digest of its complete baseline fingerprint",
            "/content/baseline_fingerprint",
        ))
    findings.extend(_semantic_baseline_common(content, contract))
    return findings


def _semantic_baseline_common(
    content: dict[str, Any], contract: dict[str, Any],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    receipts = content.get("receipts")
    expected_receipts = {
        "git.snapshot", "ml.baseline-reproduction",
        "ml.baseline-locked-evaluation",
    }
    if not isinstance(receipts, dict) or set(receipts) != expected_receipts:
        findings.append(_semantic_problem(
            contract, "baseline_receipts_invalid",
            "baseline evidence must retain exactly one Git, execution, and ML receipt",
            "/content/receipts",
        ))
    elif any(
        not isinstance(receipt, dict)
        or any(receipt.get(field) in (None, "") for field in (
            "request_id", "request_digest", "result_id", "result_digest",
            "receipt_digest", "producer_identity", "producer_principal_id",
            "producer_attestation_digest",
            "provider_receipt_uri",
            "provider_receipt_digest",
        ))
        or any(
            not isinstance(receipt.get(field), str)
            or _DIGEST.fullmatch(receipt[field]) is None
            for field in (
                "request_digest", "result_digest", "receipt_digest",
                "provider_receipt_digest", "producer_attestation_digest",
            )
        )
        for receipt in receipts.values()
    ):
        findings.append(_semantic_problem(
            contract, "baseline_receipts_invalid",
            "each baseline receipt must bind exact request/result/provider identities and canonical digests",
            "/content/receipts",
        ))
    timestamps = content.get("timestamps")
    if not isinstance(timestamps, dict) or set(timestamps) != {
        "git_snapshot_at", "jenkins_started_at", "jenkins_completed_at",
        "ml_started_at", "ml_completed_at",
    } or any(not isinstance(item, str) or not item for item in timestamps.values()):
        findings.append(_semantic_problem(
            contract, "baseline_timestamps_invalid",
            "baseline evidence must retain the exact snapshot, CI, and evaluation timestamps",
            "/content/timestamps",
        ))
    authority = content.get("authority")
    if not isinstance(authority, list) or not authority:
        findings.append(_semantic_problem(
            contract, "semantic_required",
            "baseline evidence requires consumed authority attestations",
            "/content/authority",
        ))
    elif authority != sorted(set(authority)) or any(
        not isinstance(item, str) or _DIGEST.fullmatch(item) is None
        for item in authority
    ):
        findings.append(_semantic_problem(
            contract, "baseline_authority_invalid",
            "baseline authority must be a canonical set of attestation digests",
            "/content/authority",
        ))
    return findings


def _semantic_baseline_reproduction(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_digest_fields(content, contract, (
        "baseline_digest", "baseline_fingerprint_digest", "metrics_digest",
    )))
    fingerprint = content.get("baseline_fingerprint")
    metrics = content.get("metrics")
    if (
        not isinstance(fingerprint, dict)
        or not fingerprint
        or content.get("baseline_fingerprint_digest") != digest(fingerprint)
        or not isinstance(metrics, dict)
        or not metrics
        or content.get("metrics_digest") != digest(metrics)
    ):
        findings.append(_semantic_problem(
            contract, "baseline_reproduction_binding_invalid",
            "B03 must bind the canonical baseline fingerprint and observed metrics bytes",
            "/content",
        ))
    run = content.get("run")
    tolerance = content.get("tolerance_result")
    if (
        not isinstance(run, dict)
        or set(run) != {"run_id", "attempt_id"}
        or any(not isinstance(item, str) or not item for item in run.values())
        or not isinstance(tolerance, dict)
        or set(tolerance) != {"passed", "summary"}
        or tolerance.get("passed") is not True
        or not isinstance(tolerance.get("summary"), str)
        or not tolerance["summary"]
    ):
        findings.append(_semantic_problem(
            contract, "baseline_reproduction_not_passing",
            "B03 requires one exact Run/Attempt and a passing structured tolerance result",
            "/content",
        ))
    findings.extend(_semantic_baseline_common(content, contract))
    return findings


def validate_change_policy(content: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Validate E01's closed, non-contradictory formal change protocol."""

    problems: list[tuple[str, str, str]] = []
    protocol = content.get("protocol_channel")
    # Retained E01 labels classify research rigor.  They do not grant a release
    # capability or alter the scientific follow-up/closure path.
    formal = protocol in {"formal", "regression", "promotion"} \
        or content.get("promotable") is True
    change = content.get("change_only")
    frozen = content.get("frozen_variables")
    forbidden = content.get("forbidden_changes")
    if not formal and change is None and frozen is None and forbidden is None:
        return problems
    if not isinstance(change, dict) or set(change) - {
        "allowed_code_paths", "allowed_config_paths", "required_equal_refs",
        "required_diff_paths", "automatically_derived_paths",
    }:
        return [(
            "change_policy_invalid",
            "formal E01 change_only must be one closed executable policy object",
            "/content/change_only",
        )]

    def canonical_nonempty_list(name: str) -> list[Any]:
        value = change.get(name)
        if (
            not isinstance(value, list)
            or not value
            or value != sorted(value, key=lambda item: canonical_bytes(item))
            or len({digest(item) for item in value}) != len(value)
        ):
            problems.append((
                "change_policy_list_invalid",
                f"change_only.{name} must be a non-empty canonical duplicate-free list",
                f"/content/change_only/{name}",
            ))
            return []
        return value

    allowed_code = canonical_nonempty_list("allowed_code_paths")
    allowed_config = canonical_nonempty_list("allowed_config_paths")
    required_equal = canonical_nonempty_list("required_equal_refs")
    required_diff = canonical_nonempty_list("required_diff_paths")
    automatically_derived = change.get("automatically_derived_paths", [])
    if (
        not isinstance(automatically_derived, list)
        or automatically_derived != sorted(automatically_derived)
        or len(automatically_derived) != len(set(automatically_derived))
    ):
        problems.append((
            "change_policy_list_invalid",
            "automatically_derived_paths must be a canonical duplicate-free list",
            "/content/change_only/automatically_derived_paths",
        ))
        automatically_derived = []
    if any(not _valid_code_pattern(path) for path in allowed_code):
        problems.append((
            "allowed_code_path_invalid",
            "allowed_code_paths must contain canonical relative repository paths",
            "/content/change_only/allowed_code_paths",
        ))
    if any(
        not isinstance(path, str) or _JSON_POINTER.fullmatch(path) is None
        for path in allowed_config
    ):
        problems.append((
            "allowed_config_path_invalid",
            "allowed_config_paths must contain canonical JSON pointers",
            "/content/change_only/allowed_config_paths",
        ))
    equal_map: dict[str, str] = {}
    for index, item in enumerate(required_equal):
        if (
            not isinstance(item, dict)
            or set(item) != {"ref", "digest"}
            or not isinstance(item.get("ref"), str)
            or _ARTIFACT_REF.fullmatch(item["ref"]) is None
            or not isinstance(item.get("digest"), str)
            or _DIGEST.fullmatch(item["digest"]) is None
            or item["ref"] in equal_map
        ):
            problems.append((
                "required_equal_ref_invalid",
                "required_equal_refs must uniquely bind exact artifact refs to canonical digests",
                f"/content/change_only/required_equal_refs/{index}",
            ))
            continue
        equal_map[item["ref"]] = item["digest"]
    if any(
        not isinstance(token, str) or _CHANGE_TOKEN.fullmatch(token) is None
        for token in [*required_diff, *automatically_derived]
    ):
        problems.append((
            "change_token_invalid",
            "required/automatically-derived diff paths must use code/config/ref/artifact tokens",
            "/content/change_only",
        ))
    if (
        not isinstance(frozen, list)
        or not frozen
        or frozen != sorted(
            frozen,
            key=lambda item: item.get("path", "") if isinstance(item, dict) else "",
        )
    ):
        problems.append((
            "frozen_variables_invalid",
            "formal E01 frozen_variables must be a non-empty canonical list",
            "/content/frozen_variables",
        ))
        frozen = []
    frozen_paths: set[str] = set()
    for index, item in enumerate(frozen):
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "digest", "reason"}
            or not isinstance(item.get("path"), str)
            or _JSON_POINTER.fullmatch(item["path"]) is None
            or item["path"] in frozen_paths
            or not isinstance(item.get("digest"), str)
            or _DIGEST.fullmatch(item["digest"]) is None
            or not isinstance(item.get("reason"), str)
            or not item["reason"]
        ):
            problems.append((
                "frozen_variable_invalid",
                "each frozen variable requires a unique JSON pointer, canonical digest, and reason",
                f"/content/frozen_variables/{index}",
            ))
        elif item["path"].startswith("/frozen_variables"):
            problems.append((
                "frozen_variable_self_reference",
                "frozen variables cannot bind their own declaration",
                f"/content/frozen_variables/{index}/path",
            ))
        else:
            frozen_paths.add(item["path"])
            current: Any = content
            try:
                for segment in item["path"].split("/")[1:]:
                    key = segment.replace("~1", "/").replace("~0", "~")
                    if isinstance(current, list):
                        current = current[int(key)]
                    else:
                        current = current[key]
            except (KeyError, IndexError, TypeError, ValueError):
                problems.append((
                    "frozen_variable_path_missing",
                    "frozen variable path does not resolve in the E01 candidate",
                    f"/content/frozen_variables/{index}/path",
                ))
            else:
                if digest(current) != item["digest"]:
                    problems.append((
                        "frozen_variable_digest_mismatch",
                        "frozen variable digest does not bind the value at its declared path",
                        f"/content/frozen_variables/{index}/digest",
                    ))
    if (
        not isinstance(forbidden, list)
        or not forbidden
        or forbidden != sorted(forbidden)
        or len(forbidden) != len(set(forbidden))
        or any(
            not isinstance(token, str) or _CHANGE_TOKEN.fullmatch(token) is None
            for token in forbidden
        )
    ):
        problems.append((
            "forbidden_changes_invalid",
            "formal E01 forbidden_changes must be a non-empty canonical token list",
            "/content/forbidden_changes",
        ))
        forbidden = []

    allowed = {
        *(f"code:{path}" for path in allowed_code),
        *(f"config:{path}" for path in allowed_config),
    }
    required_set = set(required_diff)
    automatic_set = set(automatically_derived)
    forbidden_set = set(forbidden)
    frozen_set = {f"config:{path}" for path in frozen_paths}
    equal_set = {f"ref:{ref}" for ref in equal_map}
    conflicts = (
        (allowed & forbidden_set)
        | (required_set & forbidden_set)
        | (automatic_set & forbidden_set)
        | (required_set & automatic_set)
        | (allowed & frozen_set)
        | (required_set & frozen_set)
        | (automatic_set & frozen_set)
        | (required_set & equal_set)
        | (automatic_set & equal_set)
    )
    for mutable in allowed | required_set | automatic_set:
        for protected in forbidden_set | frozen_set | equal_set:
            if _change_token_overlaps(mutable, protected):
                conflicts.update((mutable, protected))
    if conflicts:
        problems.append((
            "change_policy_contradiction",
            "change policy assigns incompatible meanings to: "
            + ", ".join(sorted(conflicts)),
            "/content/change_only",
        ))

    source_refs: dict[str, Any] = {}
    contracts = content.get("contracts")
    if isinstance(contracts, dict) and isinstance(
        contracts.get("source_digests"), dict,
    ):
        source_refs.update(contracts["source_digests"])
    if isinstance(content.get("source_refs"), dict):
        source_refs.update(content["source_refs"])
    substituted_equal = sorted(
        ref for ref, bound in equal_map.items() if source_refs.get(ref) != bound
    )
    if substituted_equal:
        problems.append((
            "required_equal_ref_substituted",
            "required_equal_refs do not equal E01's immutable source closure: "
            + ", ".join(substituted_equal),
            "/content/change_only/required_equal_refs",
        ))
    return problems


def _valid_code_pattern(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or any(character in value for character in ("?", "[", "]", "\\", "\x00"))
    ):
        return False
    parts = value.split("/")
    return all(
        part not in {"", ".", ".."}
        and ("**" not in part or part == "**")
        for part in parts
    )


def _code_pattern_matches(pattern: str, path: str) -> bool:
    if not _valid_code_pattern(pattern) or not _valid_code_pattern(path) \
            or "*" in path:
        return False
    pattern_parts = pattern.split("/")
    path_parts = path.split("/")

    def match(pattern_index: int, path_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        segment = pattern_parts[pattern_index]
        if segment == "**":
            return match(pattern_index + 1, path_index) or (
                path_index < len(path_parts)
                and match(pattern_index, path_index + 1)
            )
        return (
            path_index < len(path_parts)
            and fnmatchcase(path_parts[path_index], segment)
            and match(pattern_index + 1, path_index + 1)
        )

    return match(0, 0)


def _change_token_matches(declared: str, observed: str) -> bool:
    declared_kind, _, declared_value = declared.partition(":")
    observed_kind, _, observed_value = observed.partition(":")
    if declared_kind != observed_kind:
        return False
    if declared_kind == "code":
        return _code_pattern_matches(declared_value, observed_value)
    if declared_kind == "config":
        return observed_value == declared_value or observed_value.startswith(
            declared_value + "/",
        )
    return declared_value == observed_value


def _change_token_overlaps(left: str, right: str) -> bool:
    left_kind, _, left_value = left.partition(":")
    right_kind, _, right_value = right.partition(":")
    if left_kind != right_kind:
        return False
    if left_kind == "config":
        return (
            left_value == right_value
            or left_value.startswith(right_value + "/")
            or right_value.startswith(left_value + "/")
        )
    if left_kind == "code":
        if "*" not in left_value:
            return _code_pattern_matches(right_value, left_value)
        if "*" not in right_value:
            return _code_pattern_matches(left_value, right_value)
        left_prefix = left_value.split("*", 1)[0].rstrip("/")
        right_prefix = right_value.split("*", 1)[0].rstrip("/")
        return (
            left_prefix == right_prefix
            or left_prefix.startswith(right_prefix + "/")
            or right_prefix.startswith(left_prefix + "/")
        )
    return left_value == right_value


def validate_change_diff_evidence(
    content: dict[str, Any], evidence: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """Fail closed unless all four exact diff domains satisfy E01 policy."""

    problems = validate_change_policy(content)
    if problems:
        return problems
    required_keys = {
        "diff_scope_complete", "git_path_diff", "resolved_config_diff",
        "contract_ref_diff", "artifact_digest_diff",
        "required_equal_ref_digests", "frozen_variable_digests",
        "diff_evidence_digest",
    }
    if not isinstance(evidence, dict) or set(evidence) != required_keys:
        return [(
            "diff_evidence_shape_invalid",
            "diff evidence must contain the exact four domains, equality/frozen bindings, completeness, and self-digest",
            "/diff_evidence",
        )]
    if evidence.get("diff_scope_complete") is not True:
        problems.append((
            "diff_scope_incomplete", "Git adapter did not inspect the complete change scope",
            "/diff_evidence/diff_scope_complete",
        ))

    observed: set[str] = set()
    for field, prefix, identity in (
        ("git_path_diff", "code", "path"),
        ("resolved_config_diff", "config", "path"),
        ("contract_ref_diff", "ref", "ref"),
        ("artifact_digest_diff", "artifact", "ref"),
    ):
        rows = evidence.get(field)
        if not isinstance(rows, list) or rows != sorted(
            rows,
            key=lambda item: item.get(identity, "") if isinstance(item, dict) else "",
        ):
            problems.append((
                "diff_domain_invalid", f"{field} must be a canonical array",
                f"/diff_evidence/{field}",
            ))
            continue
        seen: set[str] = set()
        for index, row in enumerate(rows):
            name = row.get(identity) if isinstance(row, dict) else None
            if (
                not isinstance(row, dict)
                or set(row) != {identity, "old_digest", "new_digest"}
                or not isinstance(name, str)
                or not name
                or name in seen
                or not isinstance(row.get("old_digest"), str)
                or _DIGEST.fullmatch(row["old_digest"]) is None
                or not isinstance(row.get("new_digest"), str)
                or _DIGEST.fullmatch(row["new_digest"]) is None
                or row["old_digest"] == row["new_digest"]
                or prefix == "code" and (
                    not _valid_code_pattern(name) or "*" in name
                )
                or prefix == "config" and _JSON_POINTER.fullmatch(name) is None
                or prefix in {"ref", "artifact"}
                and _ARTIFACT_REF.fullmatch(name) is None
            ):
                problems.append((
                    "diff_row_invalid", f"{field} contains a malformed exact digest transition",
                    f"/diff_evidence/{field}/{index}",
                ))
                continue
            seen.add(name)
            observed.add(f"{prefix}:{name}")

    change = content["change_only"]
    allowed = {
        *(f"code:{path}" for path in change["allowed_code_paths"]),
        *(f"config:{path}" for path in change["allowed_config_paths"]),
    }
    required = set(change["required_diff_paths"])
    automatic = set(change.get("automatically_derived_paths", []))
    declared_change = allowed | required | automatic
    undeclared = {
        token for token in observed
        if not any(_change_token_matches(rule, token) for rule in declared_change)
    }
    missing = {
        rule for rule in required
        if not any(_change_token_matches(rule, token) for token in observed)
    }
    forbidden = {
        token for token in observed
        if any(
            _change_token_matches(rule, token)
            for rule in content["forbidden_changes"]
        )
    }
    if undeclared:
        problems.append((
            "diff_path_undeclared", "diff changes undeclared paths: "
            + ", ".join(sorted(undeclared)), "/diff_evidence",
        ))
    if missing:
        problems.append((
            "required_diff_missing", "required differences are absent: "
            + ", ".join(sorted(missing)), "/diff_evidence",
        ))
    if forbidden:
        problems.append((
            "forbidden_change_observed", "forbidden differences were observed: "
            + ", ".join(sorted(forbidden)), "/diff_evidence",
        ))

    equal_expected = {
        row["ref"]: row["digest"]
        for row in change["required_equal_refs"]
    }
    frozen_expected = {
        row["path"]: row["digest"] for row in content["frozen_variables"]
    }
    if evidence.get("required_equal_ref_digests") != equal_expected:
        problems.append((
            "required_equal_ref_mismatch",
            "diff evidence substitutes or omits required-equal ref digests",
            "/diff_evidence/required_equal_ref_digests",
        ))
    if evidence.get("frozen_variable_digests") != frozen_expected:
        problems.append((
            "frozen_variable_mismatch",
            "diff evidence substitutes or omits frozen variable digests",
            "/diff_evidence/frozen_variable_digests",
        ))
    frozen_changed = sorted(
        token for token in observed
        if token.startswith("config:") and any(
            token == f"config:{path}"
            or token.startswith(f"config:{path}/")
            or f"config:{path}".startswith(token + "/")
            for path in frozen_expected
        )
    )
    equal_changed = sorted(
        f"ref:{ref}" for ref in equal_expected if f"ref:{ref}" in observed
    )
    if frozen_changed:
        problems.append((
            "frozen_variable_changed", "frozen variables changed: "
            + ", ".join(frozen_changed), "/diff_evidence/resolved_config_diff",
        ))
    if equal_changed:
        problems.append((
            "required_equal_ref_changed", "required-equal refs changed: "
            + ", ".join(equal_changed), "/diff_evidence/contract_ref_diff",
        ))
    digest_payload = {
        key: evidence[key] for key in sorted(required_keys - {"diff_evidence_digest"})
    }
    if evidence.get("diff_evidence_digest") != digest(digest_payload):
        problems.append((
            "diff_evidence_digest_mismatch",
            "diff_evidence_digest does not bind the canonical observed diff bytes",
            "/diff_evidence/diff_evidence_digest",
        ))
    return problems


def experiment_scientific_kernel_findings(content, contract):
    """Original E01 scientific fields/change semantics shared by ordinary adapters."""
    findings = []
    if (
        not isinstance(content.get("experiment_id"), str)
        or _ID.fullmatch(content["experiment_id"]) is None
        or type(content.get("promotable")) is not bool
        or not _meaningful_text(content.get("hypothesis"), minimum=8)
        or not isinstance(content.get("variables"), dict)
        or not content["variables"]
        or not _semantic_value_present(content["variables"])
        or not isinstance(content.get("budget"), dict)
        or not content["budget"]
        or not _semantic_value_present(content["budget"])
    ):
        findings.append(_semantic_problem(
            contract, "experiment_scientific_kernel_invalid",
            "E01 requires a portable identity, explicit promotability, falsifiable hypothesis, variables, and concrete budget",
            "/content",
        ))
    findings.extend(
        _semantic_problem(contract, code, message, pointer)
        for code, message, pointer in validate_change_policy(content)
    )
    return findings


def _semantic_experiment_strict(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_experiment(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_author_principals(content, contract))
    findings.extend(_semantic_source_refs(content, contract))
    findings.extend(experiment_scientific_kernel_findings(content, contract))
    anchors = {
        "contracts": ("source_digests",),
        "baseline": ("lock_ref", "lock_digest", "baseline_digest"),
        "data": (
            "dataset_manifest_ref", "dataset_manifest_digest", "dataset_digest",
            "split_manifest_ref", "split_manifest_digest", "split_digest",
            "calibration_applicable",
        ),
        "evaluator": ("contract_ref", "contract_digest", "evaluator_identity"),
    }
    for section, names in anchors.items():
        section_value = content.get(section)
        if not isinstance(section_value, dict) or any(
            name not in section_value or section_value[name] in (None, "", [], {})
            for name in names
            if name != "calibration_applicable"
        ) or section == "data" and not isinstance(
            section_value.get("calibration_applicable") if isinstance(section_value, dict) else None,
            bool,
        ):
            findings.append(_semantic_problem(
                contract, "experiment_anchor_missing",
                f"E01 {section} must carry exact immutable source identities and digests",
                f"/content/{_ptr(section)}",
            ))
    contracts = content.get("contracts")
    source_refs = content.get("source_refs")
    contract_sources = contracts.get("source_digests") \
        if isinstance(contracts, dict) else None
    if (
        not isinstance(contract_sources, dict) or not contract_sources
        or any(
            not isinstance(ref, str) or _ARTIFACT_REF.fullmatch(ref) is None
            or not ref.startswith("G")
            or not isinstance(bound, str) or _DIGEST.fullmatch(bound) is None
            or not isinstance(source_refs, dict) or source_refs.get(ref) != bound
            for ref, bound in (
                contract_sources.items() if isinstance(contract_sources, dict) else ()
            )
        )
    ):
        findings.append(_semantic_problem(
            contract, "experiment_contract_source_invalid",
            "E01 contracts.source_digests must be exact G-artifact bindings repeated in source_refs",
            "/content/contracts/source_digests",
        ))
    for section, fields in (
        ("baseline", ("lock_digest", "baseline_digest")),
        ("data", (
            "dataset_manifest_digest", "dataset_digest",
            "split_manifest_digest", "split_digest",
        )),
        ("evaluator", ("contract_digest",)),
    ):
        section_value = content.get(section)
        if isinstance(section_value, dict):
            findings.extend(_semantic_digest_fields(section_value, contract, fields))
    data = content.get("data")
    if isinstance(data, dict) and data.get("calibration_applicable") is True:
        if any(data.get(name) in (None, "", [], {}) for name in (
            "calibration_manifest_ref", "calibration_manifest_digest",
            "calibration_digest",
        )):
            findings.append(_semantic_problem(
                contract, "calibration_anchor_missing",
                "calibrated E01 data must bind the exact R06 identity and content digest",
                "/content/data",
            ))
        else:
            findings.extend(_semantic_digest_fields(
                data, contract, ("calibration_manifest_digest", "calibration_digest"),
            ))
    baseline = content.get("baseline")
    evaluator = content.get("evaluator")
    if (
        not isinstance(baseline, dict)
        or not isinstance(baseline.get("lock_ref"), str)
        or not baseline["lock_ref"].startswith("B02:")
        or not isinstance(data, dict)
        or not isinstance(data.get("dataset_manifest_ref"), str)
        or not data["dataset_manifest_ref"].startswith("R04:")
        or not isinstance(data.get("split_manifest_ref"), str)
        or not data["split_manifest_ref"].startswith("R05:")
        or not isinstance(evaluator, dict)
        or not isinstance(evaluator.get("contract_ref"), str)
        or not evaluator["contract_ref"].startswith("G10:")
    ):
        findings.append(_semantic_problem(
            contract, "experiment_anchor_identity_invalid",
            "E01 baseline/data/evaluator anchors must identify exact B02/R04/R05/G10 instances",
            "/content",
        ))
    rule = content.get("decision_rule")
    if (
        not isinstance(rule, dict)
        or set(rule) != {
            "schema", "support_predicate", "rejection_predicate",
            "inconclusive_rule", "invalid_rule",
        }
        or rule.get("schema") != "sdm-scientific-rule/v1"
        or any(
            not _meaningful_text(rule.get(name))
            for name in (
                "support_predicate", "rejection_predicate",
                "inconclusive_rule", "invalid_rule",
            )
        )
        or rule.get("support_predicate") == rule.get("rejection_predicate")
    ):
        findings.append(_semantic_problem(
            contract, "experiment_decision_rule_invalid",
            "E01 decision_rule must be a closed executable and mutually exclusive scientific rule",
            "/content/decision_rule",
        ))
    return findings


def _semantic_run_plan(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_author_principals(content, contract))
    findings.extend(_semantic_source_refs(content, contract))
    if (
        any(
            not isinstance(content.get(name), str)
            or _ID.fullmatch(content[name]) is None
            for name in ("run_plan_id", "experiment_id")
        )
        or not isinstance(content.get("candidate_spec_digest"), str)
        or _DIGEST.fullmatch(content["candidate_spec_digest"]) is None
        or content.get("channel") not in {
            "exploratory", "formal", "regression", "promotion",
        }
        or not isinstance(content.get("attempt_policy"), dict)
        or not content["attempt_policy"]
        or not _unique_texts(content.get("evidence_requirements"))
    ):
        findings.append(_semantic_problem(
            contract, "run_plan_header_invalid",
            "E04 must bind exact plan/Experiment identities, candidate digest, channel, Attempt policy, and evidence requirements",
            "/content",
        ))
    rows = content.get("runs")
    if not isinstance(rows, list) or not rows:
        return findings + [_semantic_problem(
            contract, "run_plan_empty", "E04 must enumerate concrete Runs",
            "/content/runs",
        )]
    seen_runs: set[str] = set()
    seen_attempts: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            findings.append(_semantic_problem(
                contract, "run_plan_row_invalid", "E04 Run rows must be objects",
                f"/content/runs/{index}",
            ))
            continue
        run_id = row.get("run_id")
        attempts = row.get("required_attempt_ids")
        if (
            not isinstance(run_id, str) or not run_id or run_id in seen_runs
            or not isinstance(attempts, list) or not attempts
            or any(not isinstance(item, str) or not item or item in seen_attempts for item in attempts)
            or len(attempts) != len(set(attempts))
        ):
            findings.append(_semantic_problem(
                contract, "run_plan_identity_invalid",
                "E04 must uniquely bind every Run and Attempt identity",
                f"/content/runs/{index}",
            ))
        else:
            seen_runs.add(run_id)
            seen_attempts.update(attempts)
        findings.extend(_semantic_digest_fields(
            row, contract, (
                "data_digest", "split_digest", "evaluator_digest",
                "dataset_manifest_digest", "split_manifest_digest",
            ),
        ))
        image_digest = row.get("expected_runtime_image_digest")
        profile = row.get("expected_runtime_profile")
        try:
            _canonical_runtime_profile(profile)
        except ContractError as exc:
            findings.append(_semantic_problem(
                contract, exc.code, str(exc),
                f"/content/runs/{index}/expected_runtime_profile",
            ))
        if not runtime_image_binding_valid(image_digest, profile):
            findings.append(_semantic_problem(
                contract, "run_plan_runtime_image_invalid",
                "E04 Run must bind one immutable expected runtime image digest",
                f"/content/runs/{index}/expected_runtime_image_digest",
            ))
        calibration_applicable = row.get("calibration_applicable")
        if type(calibration_applicable) is not bool:
            findings.append(_semantic_problem(
                contract, "run_plan_calibration_applicability_invalid",
                "E04 calibration_applicable must be an explicit boolean",
                f"/content/runs/{index}/calibration_applicable",
            ))
        elif calibration_applicable:
            findings.extend(_semantic_digest_fields(
                row, contract, (
                    "calibration_manifest_digest", "calibration_digest",
                ),
            ))
        elif any(name in row for name in (
            "calibration_manifest_digest", "calibration_digest",
        )):
            findings.append(_semantic_problem(
                contract, "run_plan_calibration_substitute_invalid",
                "non-calibrated E04 Runs cannot retain calibration digest substitutes",
                f"/content/runs/{index}",
            ))
        if (
            not isinstance(row.get("environment_requirements"), dict)
            or not row["environment_requirements"]
            or row.get("seed") in (None, "", [], {})
            or not isinstance(row.get("budget"), dict)
            or not row["budget"]
        ):
            findings.append(_semantic_problem(
                contract, "run_plan_execution_facts_invalid",
                "each E04 Run must bind environment requirements, seed, and concrete budget",
                f"/content/runs/{index}",
            ))
    return findings


def _semantic_search_spec(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings

    def problem(code: str, message: str, pointer: str) -> None:
        findings.append(_semantic_problem(
            contract, code, message, pointer,
        ))

    def typed_choice(kind: Any, choice: Any) -> bool:
        if kind == "integer":
            return type(choice) is int
        if kind == "number":
            return not isinstance(choice, bool) and isinstance(
                choice, (int, float, Decimal),
            )
        if kind == "categorical":
            return isinstance(choice, (str, bool, int, float, Decimal)) \
                and choice is not None
        return False

    space = content.get("space")
    parameters = space.get("parameters") if isinstance(space, dict) else None
    if (
        not isinstance(parameters, dict)
        or not parameters
        or any(
            not isinstance(name, str) or not name
            or not isinstance(specification, dict)
            or set(specification) != {"type", "values"}
            or specification.get("type") not in {
                "categorical", "integer", "number",
            }
            or not isinstance(specification.get("values"), list)
            or not specification["values"]
            or any(
                not typed_choice(specification.get("type"), choice)
                for choice in specification["values"]
            )
            or len({digest(item) for item in specification["values"]})
            != len(specification["values"])
            for name, specification in (
                parameters.items() if isinstance(parameters, dict) else ()
            )
        )
    ):
        problem(
            "hpo_space_invalid",
            "E03 space must define non-empty typed, duplicate-free parameter choices",
            "/content/space",
        )

    algorithm = content.get("algorithm")
    if (
        not isinstance(algorithm, dict)
        or set(algorithm) != {"name", "deterministic", "seed"}
        or algorithm.get("name") not in {"grid", "random", "bayesian"}
        or algorithm.get("deterministic") is not True
        or not isinstance(algorithm.get("seed"), str)
        or not algorithm["seed"]
    ):
        problem(
            "hpo_algorithm_invalid",
            "E03 algorithm must name a supported deterministic algorithm and frozen seed",
            "/content/algorithm",
        )

    objective = content.get("objective")
    objective_fields = {
        "metric", "direction",
        "search_partition_name", "search_partition_digest",
        "recheck_partition_name", "recheck_partition_digest",
        "formal_test_partition_name", "formal_test_partition_digest",
    }
    if (
        not isinstance(objective, dict)
        or set(objective) != objective_fields
        or not isinstance(objective.get("metric"), str)
        or not objective["metric"]
        or objective.get("direction") not in {"maximize", "minimize"}
        or any(
            not isinstance(objective.get(name), str)
            or not objective[name]
            for name in (
                "search_partition_name", "recheck_partition_name",
                "formal_test_partition_name",
            )
        )
        or any(
            not isinstance(objective.get(name), str)
            or _DIGEST.fullmatch(objective[name]) is None
            for name in (
                "search_partition_digest", "recheck_partition_digest",
                "formal_test_partition_digest",
            )
        )
        or len({
            objective.get("search_partition_digest"),
            objective.get("recheck_partition_digest"),
            objective.get("formal_test_partition_digest"),
        }) != 3
    ):
        problem(
            "hpo_objective_invalid",
            "E03 objective must freeze metric/direction and distinct search, recheck, and formal-test partitions",
            "/content/objective",
        )

    selection_rule = content.get("selection_rule")
    if (
        not isinstance(selection_rule, dict)
        or set(selection_rule) != {"direction", "aggregation", "tie_breaker"}
        or selection_rule.get("direction")
        != (objective.get("direction") if isinstance(objective, dict) else None)
        or selection_rule.get("aggregation") != "mean"
        or selection_rule.get("tie_breaker") != {
            "field": "configuration_digest", "direction": "ascending",
        }
    ):
        problem(
            "hpo_selection_rule_invalid",
            "E03 selection_rule must repeat objective direction, use mean aggregation, and deterministically break ties by ascending configuration digest",
            "/content/selection_rule",
        )

    budget = content.get("budget")
    if (
        not isinstance(budget, dict)
        or set(budget) != {"trial_count", "recheck_count"}
        or type(budget.get("trial_count")) is not int
        or budget["trial_count"] < 1
        or type(budget.get("recheck_count")) is not int
        or budget["recheck_count"] < 0
    ):
        problem(
            "hpo_budget_invalid",
            "E03 budget must freeze positive search and non-negative recheck counts",
            "/content/budget",
        )

    if content.get("seed_policy") != {
        "trial_seeds_unique": True,
        "recheck_seeds_unique": True,
        "confirmation_seed_distinct": True,
    }:
        problem(
            "hpo_seed_policy_invalid",
            "E03 must require unique search/recheck seeds and a distinct confirmation seed",
            "/content/seed_policy",
        )

    split_ref = content.get("split_manifest_ref")
    split_digest = content.get("split_manifest_digest")
    if (
        not isinstance(split_ref, str) or not split_ref.startswith("R05:")
        or not isinstance(split_digest, str)
        or _DIGEST.fullmatch(split_digest) is None
        or not isinstance(content.get("run_plan_id"), str)
        or not content["run_plan_id"]
    ):
        problem(
            "hpo_anchor_invalid",
            "E03 must bind one exact R05 split manifest and E04 run-plan identity",
            "/content",
        )

    plan = content.get("trial_plan")
    plan_rows = plan if isinstance(plan, list) else []
    seen_runs: set[str] = set()
    seen_attempts: set[str] = set()
    seen_seeds: set[str] = set()
    seen_environment_instances: set[str] = set()
    search_rows: dict[str, dict[str, Any]] = {}
    phase_counts = {"search": 0, "recheck": 0}
    objective_digests = {
        "search": objective.get("search_partition_digest")
        if isinstance(objective, dict) else None,
        "recheck": objective.get("recheck_partition_digest")
        if isinstance(objective, dict) else None,
    }
    for index, row in enumerate(plan_rows):
        pointer = f"/content/trial_plan/{index}"
        phase = row.get("phase") if isinstance(row, dict) else None
        required = {
            "phase", "run_id", "attempt_id", "configuration",
            "configuration_digest", "partition_digest", "seed",
            "environment_plan", "environment_plan_digest",
        }
        allowed = required | ({"recheck_of"} if phase == "recheck" else set())
        configuration = row.get("configuration") if isinstance(row, dict) else None
        configuration_digest = row.get("configuration_digest") \
            if isinstance(row, dict) else None
        run_id = row.get("run_id") if isinstance(row, dict) else None
        attempt_id = row.get("attempt_id") if isinstance(row, dict) else None
        seed = row.get("seed") if isinstance(row, dict) else None
        configuration_valid = (
            isinstance(configuration, dict)
            and isinstance(parameters, dict)
            and set(configuration) == set(parameters)
            and all(
                typed_choice(parameters[name].get("type"), value)
                and any(
                    digest(value) == digest(choice)
                    for choice in parameters[name]["values"]
                )
                for name, value in configuration.items()
                if isinstance(parameters.get(name), dict)
                and isinstance(parameters[name].get("values"), list)
            )
        )
        if (
            not isinstance(row, dict)
            or set(row) != allowed
            or phase not in {"search", "recheck"}
            or not isinstance(run_id, str) or not run_id
            or run_id in seen_runs
            or not isinstance(attempt_id, str) or not attempt_id
            or attempt_id in seen_attempts
            or not isinstance(seed, str) or not seed or seed in seen_seeds
            or not configuration_valid
            or configuration_digest != digest(configuration)
            or row.get("partition_digest") != objective_digests.get(phase)
        ):
            problem(
                "hpo_trial_plan_invalid",
                "every E03 trial/recheck must uniquely bind phase, Run/Attempt, in-space configuration+digest, partition, seed, and environment",
                pointer,
            )
            continue
        try:
            plan_value = canonical_runtime_environment_plan(
                row.get("environment_plan"),
            )
            plan_digest = runtime_environment_plan_digest(plan_value)
        except ContractError:
            problem(
                "hpo_environment_plan_invalid",
                "every HPO trial must bind one closed expected runtime plan",
                pointer + "/environment_plan",
            )
            continue
        if row.get("environment_plan_digest") != plan_digest:
            problem(
                "hpo_environment_plan_invalid",
                "HPO environment_plan_digest does not bind the canonical plan",
                pointer + "/environment_plan_digest",
            )
            continue
        environment_instance = plan_value["environment_instance_identity"]
        if environment_instance in seen_environment_instances:
            problem(
                "hpo_environment_plan_invalid",
                "every HPO trial/recheck must use a distinct environment instance identity",
                pointer + "/environment_plan/environment_instance_identity",
            )
            continue
        seen_runs.add(run_id)
        seen_attempts.add(attempt_id)
        seen_seeds.add(seed)
        seen_environment_instances.add(environment_instance)
        phase_counts[phase] += 1
        if phase == "search":
            search_rows[attempt_id] = row

    for index, row in enumerate(plan_rows):
        if not isinstance(row, dict) or row.get("phase") != "recheck":
            continue
        source = search_rows.get(row.get("recheck_of"))
        if (
            source is None
            or source.get("configuration_digest")
            != row.get("configuration_digest")
        ):
            problem(
                "hpo_recheck_binding_invalid",
                "each recheck must reference a frozen search Attempt with the identical configuration digest",
                f"/content/trial_plan/{index}/recheck_of",
            )
    if not isinstance(plan, list) or not plan or (
        isinstance(budget, dict)
        and phase_counts != {
            "search": budget.get("trial_count"),
            "recheck": budget.get("recheck_count"),
        }
    ):
        problem(
            "hpo_budget_plan_mismatch",
            "E03 budget must exactly equal its enumerated search/recheck plan",
            "/content/trial_plan",
        )

    confirmation = content.get("confirmation")
    confirmation_required = {
        "run_id", "attempt_id", "independent_seed",
        "independent_environment_plan", "independent_environment_plan_digest",
        "partition_name",
        "partition_digest",
    }
    if (
        not isinstance(confirmation, dict)
        or set(confirmation) != confirmation_required
        or not isinstance(confirmation.get("run_id"), str)
        or not confirmation["run_id"]
        or confirmation.get("run_id") in seen_runs
        or not isinstance(confirmation.get("attempt_id"), str)
        or not confirmation["attempt_id"]
        or confirmation.get("attempt_id") in seen_attempts
        or not isinstance(confirmation.get("independent_seed"), str)
        or not confirmation["independent_seed"]
        or confirmation.get("independent_seed") in seen_seeds
        or not isinstance(confirmation.get("partition_name"), str)
        or not confirmation["partition_name"]
        or not isinstance(confirmation.get("partition_digest"), str)
        or _DIGEST.fullmatch(confirmation["partition_digest"]) is None
        or confirmation.get("partition_digest") in {
            objective.get("search_partition_digest")
            if isinstance(objective, dict) else None,
            objective.get("recheck_partition_digest")
            if isinstance(objective, dict) else None,
            objective.get("formal_test_partition_digest")
            if isinstance(objective, dict) else None,
        }
    ):
        problem(
            "hpo_confirmation_isolation_invalid",
            "E03 confirmation must use distinct Run/Attempt, seed, environment, and non-search/non-recheck/non-test partition",
            "/content/confirmation",
        )
    else:
        try:
            confirmation_plan = canonical_runtime_environment_plan(
                confirmation["independent_environment_plan"],
            )
            confirmation_plan_digest = runtime_environment_plan_digest(
                confirmation_plan,
            )
        except ContractError:
            problem(
                "hpo_confirmation_isolation_invalid",
                "confirmation must bind one closed independent environment plan",
                "/content/confirmation/independent_environment_plan",
            )
        else:
            trial_plans = [
                row.get("environment_plan") for row in plan_rows
                if isinstance(row, dict)
            ]
            if (
                confirmation.get("independent_environment_plan_digest")
                != confirmation_plan_digest
                or confirmation_plan["environment_instance_identity"] in {
                    plan.get("environment_instance_identity")
                    for plan in trial_plans if isinstance(plan, dict)
                }
            ):
                problem(
                    "hpo_confirmation_isolation_invalid",
                    "confirmation environment plan digest and instance identity must be independent of every trial",
                    "/content/confirmation",
                )
    return findings


def _semantic_platform_manifest_strict(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    required_by_id = {
        "R04": (
            "dataset_identity", "version", "instance_key", "immutable_uri",
            "content_digest", "schema_digest", "shard_digests", "record_count", "receipt",
        ),
        "R06": (
            "calibration_identity", "version", "instance_key", "immutable_uri",
            "content_digest", "validity", "receipt",
        ),
    }
    required = required_by_id.get(contract.get("id"))
    if required and any(content.get(name) in (None, "", [], {}) for name in required):
        findings.append(_semantic_problem(
            contract, "platform_manifest_incomplete",
            f"{contract['id']} lacks immutable platform manifest facts",
            "/content",
        ))
    digest_names = {
        "R04": ("content_digest", "schema_digest"),
        "R06": ("content_digest",),
    }.get(contract.get("id"), ())
    findings.extend(_semantic_digest_fields(content, contract, digest_names))
    if contract.get("id") == "R04":
        shards = content.get("shard_digests")
        if not isinstance(shards, list) or not shards or any(
            not isinstance(item, str) or _DIGEST.fullmatch(item) is None for item in shards
        ) or len(shards) != len(set(shards)) or type(content.get("record_count")) is not int or content["record_count"] < 0:
            findings.append(_semantic_problem(
                contract, "dataset_manifest_inventory_invalid",
                "R04 must bind unique shard digests and a non-negative record count",
                "/content",
            ))
    return findings


def _semantic_runtime_environment_strict(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    if content.get("attempt_kind") not in {
        "hpo_trial", "training", "locked_evaluation",
    }:
        findings.append(_semantic_problem(
            contract, "runtime_environment_attempt_kind_invalid",
            "R03 must identify its producing Attempt kind",
            "/content/attempt_kind",
        ))
    try:
        manifest = canonical_runtime_environment_manifest(
            content.get("environment_manifest"),
        )
        expected_digest = runtime_environment_digest(manifest)
    except ContractError as exc:
        findings.append(_semantic_problem(
            contract, exc.code, str(exc), "/content/environment_manifest",
        ))
    else:
        if content.get("environment_manifest") != manifest:
            findings.append(_semantic_problem(
                contract, "runtime_environment_not_canonical",
                "R03 environment_manifest bytes are not canonical",
                "/content/environment_manifest",
            ))
        if content.get("environment_digest") != expected_digest:
            findings.append(_semantic_problem(
                contract, "runtime_environment_digest_mismatch",
                "R03 environment_digest does not bind its canonical manifest",
                "/content/environment_digest",
            ))
    if any(
        not isinstance(content.get(name), str)
        or _ID.fullmatch(content[name]) is None
        for name in ("run_id", "attempt_id")
    ):
        findings.append(_semantic_problem(
            contract, "runtime_environment_identity_invalid",
            "R03 must bind exact portable Run and Attempt identities",
            "/content",
        ))
    findings.extend(_semantic_platform_receipt(content, contract))
    return findings


def _semantic_split_manifest_strict(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    required = (
        "split_identity", "version", "instance_key", "immutable_uri",
        "dataset_content_digest", "membership_digest", "partition_digests",
        "grouping_key", "hidden_access_policy", "receipt",
    )
    if any(content.get(name) in (None, "", [], {}) for name in required):
        findings.append(_semantic_problem(
            contract, "split_manifest_incomplete",
            "R05 must bind exact membership, grouping, partitions, dataset, and hidden-access policy",
            "/content",
        ))
    findings.extend(_semantic_digest_fields(
        content, contract, ("dataset_content_digest", "membership_digest"),
    ))
    # Approval is optional governance metadata; reproducible membership is the
    # scientific requirement. Validate supplied principal metadata truthfully.
    for field, label in (("approver_principal_ids", "split_approver"),
                         ("author_principal_ids", "split_author")):
        if field in content:
            findings.extend(_semantic_principal_set(content, contract, field, label))
    if any(not _meaningful_text(content.get(field)) for field in (
        "split_identity", "version", "instance_key", "immutable_uri",
        "grouping_key",
    )) or not isinstance(content.get("hidden_access_policy"), dict) \
            or not content["hidden_access_policy"]:
        findings.append(_semantic_problem(
            contract, "split_manifest_identity_invalid",
            "R05 must bind stable split identity, grouping, hidden policy, and immutable URI",
            "/content",
        ))
    partitions = content.get("partition_digests")
    if not isinstance(partitions, dict) or not partitions or any(
        not isinstance(name, str) or not name
        or not isinstance(bound, str) or _DIGEST.fullmatch(bound) is None
        for name, bound in partitions.items()
    ):
        findings.append(_semantic_problem(
            contract, "split_partition_digest_invalid",
            "R05 partition_digests must exactly bind named membership partitions",
            "/content/partition_digests",
        ))
    findings.extend(_semantic_platform_receipt(content, contract))
    return findings


def _engineering_provenance_findings(
    content: dict[str, Any], contract: dict[str, Any], artifact_id: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    provenance = content.get("engineering_provenance")
    if not isinstance(provenance, dict):
        return [_semantic_problem(
            contract, "engineering_provenance_missing",
            f"{artifact_id} must bind versioned Git/Jenkins provenance",
            "/content/engineering_provenance",
        )]
    merged_required = artifact_id in {"S08", "S09"}
    expected = {
        "schema", *ENGINEERING_PROVENANCE_COMMON_FIELDS, "source_results",
    }
    if merged_required:
        expected.update(ENGINEERING_PROVENANCE_MERGED_FIELDS)
    if set(provenance) != expected:
        findings.append(_semantic_problem(
            contract, "engineering_provenance_fields_invalid",
            f"{artifact_id} engineering provenance is not the exact closed field set",
            "/content/engineering_provenance",
        ))
    if provenance.get("schema") != ENGINEERING_PROVENANCE_SCHEMA:
        findings.append(_semantic_problem(
            contract, "engineering_provenance_version_invalid",
            "engineering provenance schema version is unsupported",
            "/content/engineering_provenance/schema",
        ))
    provenance_fields = (
        *ENGINEERING_PROVENANCE_COMMON_FIELDS,
        *(ENGINEERING_PROVENANCE_MERGED_FIELDS if merged_required else ()),
    )
    for name in provenance_fields:
        value = provenance.get(name)
        if name.endswith("_digest"):
            valid = isinstance(value, str) and _DIGEST.fullmatch(value) is not None
        else:
            valid = isinstance(value, str) and bool(value)
        if not valid:
            findings.append(_semantic_problem(
                contract, "engineering_provenance_value_invalid",
                f"engineering provenance {name} is invalid",
                f"/content/engineering_provenance/{_ptr(name)}",
            ))
    source_results = provenance.get("source_results")
    expected_operations = set(ENGINEERING_SOURCE_OPERATIONS[artifact_id])
    if not isinstance(source_results, dict) or set(source_results) != expected_operations:
        findings.append(_semantic_problem(
            contract, "engineering_source_results_invalid",
            f"{artifact_id} must retain the exact cumulative S13 result set",
            "/content/engineering_provenance/source_results",
        ))
    else:
        for operation, source in source_results.items():
            if (
                not isinstance(source, dict)
                or set(source) != {"result_digest", "producer_principal_id"}
                or not isinstance(source.get("result_digest"), str)
                or _DIGEST.fullmatch(source["result_digest"]) is None
                or not isinstance(source.get("producer_principal_id"), str)
                or not source["producer_principal_id"]
            ):
                findings.append(_semantic_problem(
                    contract, "engineering_source_result_invalid",
                    f"{operation} source result identity is malformed",
                    "/content/engineering_provenance/source_results/"
                    + _ptr(operation),
                ))
    legacy_equal = {
        "E05": {
            "pipeline_identity": "pipeline_identity",
            "definition_digest": "pipeline_definition_digest",
            "revision": "change_revision",
            "frozen_experiment_digest": "frozen_experiment_digest",
            "diff_evidence_digest": "diff_evidence_digest",
            "source_control_revision": "change_revision",
        },
        "S07": {
            "change_request": "change_request_identity",
            "source_control_revision": "change_revision",
        },
        "S08": {
            "revision": "merged_revision", "tree_digest": "merged_tree_digest",
            "source_control_revision": "merged_revision",
        },
        "S09": {
            "experiment_digest": "frozen_experiment_digest",
            "revision": "merged_revision",
        },
    }[artifact_id]
    for legacy, bound in legacy_equal.items():
        if content.get(legacy) != provenance.get(bound):
            findings.append(_semantic_problem(
                contract, "engineering_provenance_binding_mismatch",
                f"{legacy} does not equal engineering provenance {bound}",
                f"/content/{_ptr(legacy)}",
            ))
    git_operation = {
        "E05": "git.branch-change-request",
        "S07": "git.change-review-status",
        "S08": "git.merge-status",
    }.get(artifact_id)
    if git_operation and isinstance(source_results, dict):
        source = source_results.get(git_operation)
        if isinstance(source, dict) and content.get(
            "source_control_result_digest",
        ) != source.get("result_digest"):
            findings.append(_semantic_problem(
                contract, "source_control_result_binding_mismatch",
                "legacy source-control result digest does not bind its exact Git S13",
                "/content/source_control_result_digest",
            ))
    return findings


def _resolved_configuration_findings(
    content: dict[str, Any], contract: dict[str, Any], *, pointer: str = "/content",
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    required = {
        "run_id", "resolved_values", "defaults", "override_origins",
        "structured_config_schema_digest", "unknown_keys",
        "forbidden_overrides", "resolution_trace_digest", "config_digest",
    }
    allowed = {
        *required, "schema", "id", "attempt_id", "source_run_plan_id",
        "frozen_sources", "selected_configuration_digest",
    }
    if not required.issubset(content) or not set(content).issubset(allowed):
        findings.append(_semantic_problem(
            contract, "resolved_configuration_fields_invalid",
            "resolved configuration is missing required facts or contains an undeclared fact",
            pointer,
        ))
    values = content.get("resolved_values")
    if not isinstance(values, dict) or not values:
        findings.append(_semantic_problem(
            contract, "resolved_values_invalid",
            "resolved_values must be a complete non-empty object",
            pointer + "/resolved_values",
        ))
    else:
        try:
            expected_config_digest = digest(values)
        except CanonicalError:
            expected_config_digest = None
        if content.get("config_digest") != expected_config_digest:
            findings.append(_semantic_problem(
                contract, "resolved_config_digest_mismatch",
                "config_digest must bind canonical resolved_values",
                pointer + "/config_digest",
            ))
    defaults = content.get("defaults")
    if not isinstance(defaults, list) or any(
        not isinstance(row, dict) or set(row) != {"ref", "digest"}
        or not isinstance(row.get("ref"), str) or not row["ref"]
        or not isinstance(row.get("digest"), str)
        or _DIGEST.fullmatch(row["digest"]) is None
        for row in defaults
    ) or len({row["ref"] for row in defaults if isinstance(row, dict) and "ref" in row}) != len(defaults):
        findings.append(_semantic_problem(
            contract, "resolved_config_defaults_invalid",
            "defaults must be an ordered list of unique exact ref/digest rows",
            pointer + "/defaults",
        ))
    overrides = content.get("override_origins")
    valid_overrides = isinstance(overrides, list) and all(
        isinstance(row, dict)
        and set(row) == {"path", "origin", "value_digest"}
        and isinstance(row.get("path"), str)
        and _JSON_POINTER.fullmatch(row["path"]) is not None
        and isinstance(row.get("origin"), str) and bool(row["origin"])
        and isinstance(row.get("value_digest"), str)
        and _DIGEST.fullmatch(row["value_digest"]) is not None
        for row in overrides
    )
    if valid_overrides:
        paths = [row["path"] for row in overrides]
        valid_overrides = paths == sorted(set(paths))
        if valid_overrides and isinstance(values, dict):
            for row in overrides:
                current: Any = values
                try:
                    for token in row["path"].split("/")[1:]:
                        token = token.replace("~1", "/").replace("~0", "~")
                        if isinstance(current, list):
                            current = current[int(token)]
                        else:
                            current = current[token]
                except (KeyError, IndexError, TypeError, ValueError):
                    valid_overrides = False
                    break
                if digest(current) != row["value_digest"]:
                    valid_overrides = False
                    break
    if not valid_overrides:
        findings.append(_semantic_problem(
            contract, "resolved_config_overrides_invalid",
            "override_origins must be canonical unique paths binding resolved values",
            pointer + "/override_origins",
        ))
    if content.get("unknown_keys") != []:
        findings.append(_semantic_problem(
            contract, "resolved_config_unknown_keys",
            "unknown configuration keys must be empty",
            pointer + "/unknown_keys",
        ))
    if content.get("forbidden_overrides") != []:
        findings.append(_semantic_problem(
            contract, "resolved_config_forbidden_overrides",
            "forbidden override findings must be empty",
            pointer + "/forbidden_overrides",
        ))
    findings.extend(_semantic_digest_fields(content, contract, (
        "structured_config_schema_digest", "resolution_trace_digest",
        "config_digest",
    )))
    try:
        expected_trace = resolved_configuration_trace_digest(content)
    except CanonicalError:
        expected_trace = None
    if content.get("resolution_trace_digest") != expected_trace:
        findings.append(_semantic_problem(
            contract, "resolution_trace_digest_mismatch",
            "resolution_trace_digest does not bind the complete resolution facts",
            pointer + "/resolution_trace_digest",
        ))
    selected = content.get("selected_configuration_digest")
    if selected is not None and (
        not isinstance(selected, str) or _DIGEST.fullmatch(selected) is None
    ):
        findings.append(_semantic_problem(
            contract, "selected_configuration_digest_invalid",
            "HPO selected_configuration_digest must be a canonical subset digest",
            pointer + "/selected_configuration_digest",
        ))
    if contract.get("id") == "R02":
        identities = ("run_id", "attempt_id", "source_run_plan_id")
        if any(
            not isinstance(content.get(name), str)
            or _ID.fullmatch(content[name]) is None
            for name in identities
        ) or "id" in content and content.get("id") != content.get("run_id"):
            findings.append(_semantic_problem(
                contract, "resolved_configuration_identity_invalid",
                "R02 must bind exact portable Run, Attempt, and Run Plan identities",
                pointer,
            ))
        if "schema" in content and content.get("schema") != "sdm-resolved-config/v1":
            findings.append(_semantic_problem(
                contract, "resolved_configuration_schema_invalid",
                "R02 schema, when present, must be sdm-resolved-config/v1",
                pointer + "/schema",
            ))
        frozen = content.get("frozen_sources")
        source_names: set[str] = set()
        frozen_valid = False
        if isinstance(frozen, dict) and frozen:
            source_names = set(frozen)
            frozen_valid = all(
                isinstance(name, str) and bool(name)
                and isinstance(bound, str) and _DIGEST.fullmatch(bound) is not None
                for name, bound in frozen.items()
            )
        elif isinstance(frozen, list) and frozen:
            frozen_valid = all(
                isinstance(row, dict) and set(row) == {"name", "digest"}
                and isinstance(row.get("name"), str) and bool(row["name"])
                and isinstance(row.get("digest"), str)
                and _DIGEST.fullmatch(row["digest"]) is not None
                for row in frozen
            )
            if frozen_valid:
                names = [row["name"] for row in frozen]
                frozen_valid = names == sorted(set(names))
                source_names = set(names)
        has_experiment = bool(source_names & {
            "experiment", "experiment_digest", "frozen_experiment_digest",
        })
        has_run_plan = bool(source_names & {"run_plan", "run_plan_digest"})
        if not frozen_valid or not has_experiment or not has_run_plan:
            findings.append(_semantic_problem(
                contract, "resolved_configuration_frozen_sources_invalid",
                "R02 frozen_sources must be a non-empty canonical digest inventory including the frozen Experiment and Run Plan",
                pointer + "/frozen_sources",
            ))
    return findings


def _semantic_resolved_configuration_strict(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if isinstance(content, dict):
        findings.extend(_resolved_configuration_findings(content, contract))
    return findings


def _semantic_run_manifest_strict(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_digest_fields(content, contract, (
        "experiment_digest", "frozen_experiment_digest", "run_plan_digest",
        "data_digest", "split_digest", "evaluator_digest",
        "dataset_manifest_digest", "split_manifest_digest",
        "evaluator_contract_digest", "baseline_lock_digest",
        "resolved_configuration_digest", "config_digest",
        "expected_runtime_profile_digest",
        "seed_digest", "budget_digest", "allowed_diff_digest",
        "run_fingerprint_digest",
    )))
    for name in (
        "frozen_experiment_ref", "run_plan_ref", "baseline_lock_ref",
        "resolved_configuration_ref", "dataset_manifest_ref",
        "split_manifest_ref", "evaluator_contract_ref",
    ):
        if not isinstance(content.get(name), str) or ":" not in content[name]:
            findings.append(_semantic_problem(
                contract, "run_manifest_anchor_missing",
                f"R01 must bind exact {name}", f"/content/{_ptr(name)}",
            ))
    if content.get("calibration_applicable") is True:
        findings.extend(_semantic_digest_fields(content, contract, (
            "calibration_manifest_digest", "calibration_digest",
        )))
        if not isinstance(content.get("calibration_manifest_ref"), str):
            findings.append(_semantic_problem(
                contract, "run_manifest_anchor_missing",
                "calibrated R01 must bind exact R06", "/content/calibration_manifest_ref",
            ))
    provenance = content.get("engineering_provenance")
    if isinstance(provenance, dict):
        synthetic = {
            "engineering_provenance": provenance,
            "experiment_digest": content.get("frozen_experiment_digest"),
            "revision": provenance.get("merged_revision"),
        }
        findings.extend(_engineering_provenance_findings(
            synthetic, contract, "S09",
        ))
        for name in (
            *ENGINEERING_PROVENANCE_COMMON_FIELDS,
            *ENGINEERING_PROVENANCE_MERGED_FIELDS,
        ):
            if content.get(name) != provenance.get(name):
                findings.append(_semantic_problem(
                    contract, "run_manifest_engineering_binding_mismatch",
                    f"R01 {name} does not equal S09 engineering provenance",
                    f"/content/{_ptr(name)}",
                ))
    else:
        findings.append(_semantic_problem(
            contract, "engineering_provenance_missing",
            "R01 must retain exact cumulative STEP10-13 provenance",
            "/content/engineering_provenance",
        ))
    fingerprint = content.get("run_fingerprint")
    if not isinstance(fingerprint, dict) or fingerprint.get("schema") != RUN_FINGERPRINT_SCHEMA:
        findings.append(_semantic_problem(
            contract, "run_fingerprint_invalid",
            "R01 run_fingerprint must be a versioned object",
            "/content/run_fingerprint",
        ))
    else:
        bindings = {key: value for key, value in fingerprint.items() if key != "schema"}
        try:
            expected_payload, expected_digest = canonical_run_fingerprint(bindings)
        except ContractError as exc:
            findings.append(_semantic_problem(
                contract, exc.code, str(exc), "/content/run_fingerprint",
            ))
        else:
            if fingerprint != expected_payload or content.get(
                "run_fingerprint_digest",
            ) != expected_digest:
                findings.append(_semantic_problem(
                    contract, "run_fingerprint_digest_mismatch",
                    "R01 fingerprint bytes/digest do not match the canonical helper",
                    "/content/run_fingerprint_digest",
                ))
            mismatched = sorted(
                name for name, expected_value in bindings.items()
                if content.get(name) != expected_value
            )
            if mismatched:
                findings.append(_semantic_problem(
                    contract, "run_fingerprint_binding_mismatch",
                    "R01 flat facts differ from its fingerprint: "
                    + ", ".join(mismatched),
                    "/content/run_fingerprint",
                ))
    return findings


def _semantic_actual_run_strict(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_author_principals(content, contract))
    if any(
        not isinstance(content.get(name), str)
        or _ID.fullmatch(content[name]) is None
        for name in ("run_id", "attempt_id")
    ):
        findings.append(_semantic_problem(
            contract, "attempt_identity_invalid",
            "R07 must bind exact portable Run and Attempt identities",
            "/content",
        ))
    # Authenticity is independent of the attempt branch.  Validate the
    # receipt before interpreting attempt_kind so malformed branch facts
    # cannot mask a missing or opaque platform receipt.
    findings.extend(_semantic_platform_receipt(content, contract))
    kind = content.get("attempt_kind")
    if kind not in {"training", "hpo_trial", "locked_evaluation"}:
        findings.append(_semantic_problem(
            contract, "attempt_kind_invalid",
            "R07 must distinguish training/HPO run facts from locked evaluation scientific facts",
            "/content/attempt_kind",
        ))
        return findings
    status = content.get("status")
    validity = content.get("validity")
    system_failure = content.get("system_failure")
    started = _parse_semantic_time(content.get("started_at"))
    completed = _parse_semantic_time(content.get("completed_at"))
    if (
        status not in {"successful", "failed", "cancelled", "invalid"}
        or validity not in {"valid", "invalid"}
        or type(system_failure) is not bool
        or status == "invalid" and validity != "invalid"
        or status == "successful" and (
            validity != "valid" or system_failure is not False
        )
    ):
        findings.append(_semantic_problem(
            contract, "attempt_terminal_facts_invalid",
            "R07 requires a consistent terminal status, validity, and system-failure tuple",
            "/content",
        ))
    if started is None or completed is None or completed < started:
        findings.append(_semantic_problem(
            contract, "attempt_timestamps_invalid",
            "R07 started_at/completed_at must be ordered ISO-8601 instants",
            "/content",
        ))
    if not _nonnegative_numeric_leaf(content.get("resource_use")):
        findings.append(_semantic_problem(
            contract, "attempt_resource_use_invalid",
            "R07 resource_use must be a non-empty non-negative numeric inventory",
            "/content/resource_use",
        ))
    scientific = (
        "evidence_summary", "statistical_analysis", "failure_case_index", "scientific_facts",
    )
    if kind in {"training", "hpo_trial"}:
        if any(name in content for name in scientific):
            findings.append(_semantic_problem(
                contract, "training_scientific_fact_forbidden",
                "training/HPO R07 may retain run/model facts but cannot publish metric aggregation scientific facts",
                "/content",
            ))
        if kind == "training":
            findings.extend(_semantic_digest_fields(
                content, contract, ("checkpoint_digest", "model_artifact_digest"),
            ))
            if not _immutable_uri_value(content.get("checkpoint_uri")):
                findings.append(_semantic_problem(
                    contract, "training_checkpoint_uri_invalid",
                    "training R07 must bind one immutable checkpoint URI",
                    "/content/checkpoint_uri",
                ))
        else:
            if (
                content.get("phase") not in {"search", "recheck"}
                or any(not _meaningful_text(content.get(name)) for name in (
                    "environment_instance_identity", "objective_metric",
                ))
                or any(
                    not isinstance(content.get(name), str)
                    or _DIGEST.fullmatch(content[name]) is None
                    for name in (
                        "environment_plan_digest", "environment_digest",
                        "configuration_digest", "partition_digest",
                    )
                )
                or isinstance(content.get("objective_value"), bool)
                or not isinstance(content.get("objective_value"), (int, float, Decimal))
                or content.get("seed") in (None, "", [], {})
            ):
                findings.append(_semantic_problem(
                    contract, "hpo_trial_facts_invalid",
                    "HPO R07 must bind phase, configuration, objective, partition, seed, and environment facts",
                    "/content",
                ))
    else:
        if any(not isinstance(content.get(name), dict) for name in scientific):
            findings.append(_semantic_problem(
                contract, "evaluation_scientific_fact_missing",
                "locked evaluation R07 must retain all platform-produced scientific fact objects",
                "/content",
            ))
        findings.extend(_semantic_digest_fields(content, contract, (
            "evaluated_checkpoint_digest", "evaluated_model_artifact_digest",
            "training_r07_digest", "training_r08_digest", "metrics_digest",
        )))
        if (
            not _meaningful_text(content.get("selected_checkpoint_attempt_id"))
            or not _immutable_uri_value(content.get("metrics_uri"))
        ):
            findings.append(_semantic_problem(
                contract, "evaluation_binding_invalid",
                "locked-evaluation R07 must bind its selected training Attempt and immutable metrics URI",
                "/content",
            ))
    return findings


def _semantic_artifact_index_strict(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_author_principals(content, contract))
    if any(
        not isinstance(content.get(name), str)
        or _ID.fullmatch(content[name]) is None
        for name in ("run_id", "attempt_id")
    ):
        findings.append(_semantic_problem(
            contract, "artifact_index_identity_invalid",
            "R08 must bind exact portable Run and Attempt identities",
            "/content",
        ))
    if content.get("attempt_kind") not in {"training", "hpo_trial", "locked_evaluation"}:
        findings.append(_semantic_problem(
            contract, "attempt_kind_invalid", "R08 must bind its producing Attempt kind",
            "/content/attempt_kind",
        ))
    artifacts = content.get("artifacts")
    valid_artifacts = isinstance(artifacts, list) and bool(artifacts) and all(
        isinstance(item, dict)
        and {"type", "digest"}.issubset(item)
        and set(item).issubset({"type", "digest", "uri", "model_id"})
        and _meaningful_text(item.get("type"))
        and ("uri" not in item or _immutable_uri_value(item.get("uri")))
        and (
            "model_id" not in item
            or item.get("type") == "model" and _meaningful_text(item.get("model_id"))
        )
        and isinstance(item.get("digest"), str)
        and _DIGEST.fullmatch(item["digest"]) is not None
        for item in artifacts
    )
    if valid_artifacts:
        identities = [(item["type"], item["digest"]) for item in artifacts]
        valid_artifacts = len(identities) == len(set(identities))
    if not valid_artifacts:
        findings.append(_semantic_problem(
            contract, "artifact_index_digest_invalid",
            "R08 must enumerate a canonical unique type/URI/digest inventory",
            "/content/artifacts",
        ))
    if content.get("completeness") not in {
        True, "complete", "complete-and-verified",
    }:
        findings.append(_semantic_problem(
            contract, "artifact_index_completeness_invalid",
            "R08 completeness must explicitly assert a complete artifact inventory",
            "/content/completeness",
        ))
    findings.extend(_semantic_platform_receipt(content, contract))
    if content.get("attempt_kind") == "training":
        findings.extend(_semantic_digest_fields(
            content, contract, ("checkpoint_digest", "model_artifact_digest"),
        ))
        if valid_artifacts:
            checkpoints = [
                item for item in artifacts if item.get("type") == "checkpoint"
            ]
            models = [item for item in artifacts if item.get("type") == "model"]
            if (
                len(checkpoints) != 1 or len(models) != 1
                or checkpoints[0].get("digest") != content.get("checkpoint_digest")
                or models[0].get("digest") != content.get("model_artifact_digest")
                or not _meaningful_text(models[0].get("model_id"))
            ):
                findings.append(_semantic_problem(
                    contract, "training_artifact_binding_invalid",
                    "training R08 checkpoint/model entries must bind the exact model identity and equal its flat immutable digests",
                    "/content/artifacts",
                ))
    if content.get("attempt_kind") == "locked_evaluation":
        findings.extend(_semantic_digest_fields(content, contract, (
            "evaluated_checkpoint_digest", "evaluated_model_artifact_digest",
            "training_r07_digest", "training_r08_digest",
        )))
        if not _meaningful_text(content.get("selected_checkpoint_attempt_id")):
            findings.append(_semantic_problem(
                contract, "evaluation_artifact_binding_invalid",
                "locked-evaluation R08 must identify its exact selected training Attempt",
                "/content/selected_checkpoint_attempt_id",
            ))
    return findings


def _semantic_evidence_index_strict(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_author_principals(content, contract))
    if any(
        not isinstance(content.get(name), str)
        or _ID.fullmatch(content[name]) is None
        for name in ("evidence_id", "experiment_id")
    ):
        findings.append(_semantic_problem(
            contract, "evidence_identity_invalid",
            "D01 must bind exact portable Evidence and Experiment identities",
            "/content",
        ))
    planned = content.get("planned_runs")
    planned_attempts: set[str] = set()
    planned_valid = isinstance(planned, list) and bool(planned)
    run_ids: list[str] = []
    if planned_valid:
        for row in planned:
            if (
                not isinstance(row, dict)
                or set(row) != {"run_id", "required_attempt_ids"}
                or not isinstance(row.get("run_id"), str)
                or _ID.fullmatch(row["run_id"]) is None
                or not isinstance(row.get("required_attempt_ids"), list)
                or not row["required_attempt_ids"]
                or any(
                    not isinstance(item, str) or _ID.fullmatch(item) is None
                    for item in row["required_attempt_ids"]
                )
                or row["required_attempt_ids"]
                != sorted(set(row["required_attempt_ids"]))
                or planned_attempts.intersection(row["required_attempt_ids"])
            ):
                planned_valid = False
                break
            run_ids.append(row["run_id"])
            planned_attempts.update(row["required_attempt_ids"])
        planned_valid = planned_valid and run_ids == sorted(set(run_ids))
    accounted = content.get("accounted_attempts")
    accounted_ids: list[str] = []
    accounted_valid = isinstance(accounted, list) and bool(accounted)
    if accounted_valid:
        planned_by_attempt = {
            attempt_id: row["run_id"]
            for row in planned
            for attempt_id in row["required_attempt_ids"]
        } if planned_valid else {}
        for row in accounted:
            if (
                not isinstance(row, dict)
                or set(row) != {
                    "attempt_id", "run_id", "terminal_status", "accounted",
                    "validity", "system_failure",
                }
                or not isinstance(row.get("attempt_id"), str)
                or _ID.fullmatch(row["attempt_id"]) is None
                or not isinstance(row.get("run_id"), str)
                or planned_by_attempt.get(row["attempt_id"]) != row["run_id"]
                or row.get("terminal_status") not in {
                    "successful", "failed", "cancelled", "invalid",
                }
                or row.get("accounted") is not True
                or row.get("validity") not in {"valid", "invalid"}
                or type(row.get("system_failure")) is not bool
            ):
                accounted_valid = False
                break
            accounted_ids.append(row["attempt_id"])
        accounted_valid = (
            accounted_valid
            and accounted_ids == sorted(set(accounted_ids))
            and set(accounted_ids) == planned_attempts
        )
    if not planned_valid or not accounted_valid:
        findings.append(_semantic_problem(
            contract, "evidence_attempt_accounting_invalid",
            "D01 planned_runs and accounted_attempts must form one exact canonical Run/Attempt partition",
            "/content",
        ))
    scientific_ids = content.get("scientific_attempt_ids")
    source_digests = content.get("source_digests")
    items = content.get("items")
    scientific_ids_valid = (
        isinstance(scientific_ids, list) and bool(scientific_ids)
        and all(
            isinstance(item, str) and _ID.fullmatch(item) is not None
            for item in scientific_ids
        )
    )
    if scientific_ids_valid:
        scientific_ids_valid = (
            scientific_ids == sorted(set(scientific_ids))
            and set(scientific_ids).issubset(set(accounted_ids))
        )
    source_digests_valid = (
        isinstance(source_digests, dict) and bool(source_digests)
        and not any(
            not isinstance(ref, str) or _ARTIFACT_REF.fullmatch(ref) is None
            or ref.split(":", 1)[0] not in _ALL_ARTIFACT_IDS
            or not isinstance(bound, str) or _DIGEST.fullmatch(bound) is None
            for ref, bound in source_digests.items()
        )
    )
    items_valid = (
        isinstance(items, list)
        and all(
            isinstance(item, str) and _ARTIFACT_REF.fullmatch(item) is not None
            for item in items
        )
    )
    if items_valid:
        items_valid = items == sorted(set(items))
    bundle_valid = False
    if source_digests_valid:
        bundle_valid = content.get("bundle_digest") == digest(source_digests)
    if not (
        scientific_ids_valid and source_digests_valid and items_valid
        and set(source_digests) == set(items) and bundle_valid
    ):
        findings.append(_semantic_problem(
            contract, "evidence_source_closure_invalid",
            "D01 must bind its exact immutable source digest map and locked-evaluation scientific Attempt set",
            "/content",
        ))
    if isinstance(source_digests, dict):
        expected_anchors = {
            f"E01:{content.get('experiment_id')}.frozen",
            *(f"R01:{run_id}" for run_id in run_ids),
            *(f"R07:{attempt_id}" for attempt_id in accounted_ids),
            *(f"R08:{attempt_id}" for attempt_id in accounted_ids),
        }
        run_plan_anchors = [
            ref for ref in source_digests if ref.startswith("E04:")
        ]
        if (
            not expected_anchors.issubset(source_digests)
            or len(run_plan_anchors) != 1
        ):
            findings.append(_semantic_problem(
                contract, "evidence_governed_anchor_closure_invalid",
                "D01 source_digests must include its frozen E01, one E04, every R01, and every R07/R08 Attempt pair",
                "/content/source_digests",
            ))
    return findings


def _semantic_evidence_section_sources(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if isinstance(content, dict) and contract.get("id") in {"D02", "D03", "D04"}:
        findings.extend(_semantic_author_principals(content, contract))
        sources = content.get("source_attempt_ids")
        if (
            not isinstance(sources, list)
            or not sources
            or any(not isinstance(source, str) or not source for source in sources)
            or sources != sorted(set(sources))
        ):
            findings.append(_semantic_problem(
                contract, "scientific_source_attempts_invalid",
                f"{contract['id']} must name the exact locked-evaluation Attempts that produced its scientific facts",
                "/content/source_attempt_ids",
            ))
            sources = []

        def semantically_nonempty(item: Any) -> bool:
            if item is None:
                return False
            if isinstance(item, str):
                return bool(item.strip())
            if isinstance(item, list):
                return bool(item) and all(semantically_nonempty(child) for child in item)
            if isinstance(item, dict):
                return bool(item) and all(
                    isinstance(key, str) and bool(key.strip())
                    and semantically_nonempty(child)
                    for key, child in item.items()
                )
            return isinstance(item, (bool, int, float, Decimal))

        def exact_attempt_aggregation(section: Any) -> bool:
            if not isinstance(section, list) or not section:
                return False
            attempt_ids: list[str] = []
            for row in section:
                if not isinstance(row, dict) or set(row) != {"attempt_id", "value"}:
                    return False
                attempt_id = row.get("attempt_id")
                if (
                    not isinstance(attempt_id, str)
                    or not attempt_id
                    or not semantically_nonempty(row.get("value"))
                ):
                    return False
                attempt_ids.append(attempt_id)
            return attempt_ids == sorted(set(sources))

        required_sections = {
            "D02": ("results", "limitations", "missingness", "deviations"),
            "D03": (
                "comparisons", "effect_sizes", "uncertainty", "power",
                "multiplicity",
            ),
            "D04": ("taxonomy", "cases", "artifact_digests", "coverage"),
        }[contract["id"]]
        for section_name in required_sections:
            if not exact_attempt_aggregation(content.get(section_name)):
                findings.append(_semantic_problem(
                    contract, "evidence_section_empty",
                    f"{contract['id']} {section_name} must contain one non-empty value for every exact locked-evaluation Attempt",
                    f"/content/{section_name}",
                ))
        if contract["id"] == "D02" and not semantically_nonempty(content.get("hypothesis")):
            findings.append(_semantic_problem(
                contract, "evidence_hypothesis_missing",
                "D02 must retain the non-empty governed hypothesis it summarizes",
                "/content/hypothesis",
            ))
        if contract.get("id") == "D03" and not semantically_nonempty(content.get("scientific_facts")):
            findings.append(_semantic_problem(
                contract, "scientific_evidence_missing",
                "D03 must retain non-empty exact locked-evaluation scientific facts",
                "/content/scientific_facts",
            ))
        if contract.get("id") == "D04":
            artifact_rows = content.get("artifact_digests")
            if not isinstance(artifact_rows, list) or any(
                not isinstance(row, dict)
                or set(row) != {"attempt_id", "value"}
                or not _meaningful_text(row.get("attempt_id"))
                or not _semantic_digest_inventory(row.get("value"))
                for row in artifact_rows
            ):
                findings.append(_semantic_problem(
                    contract, "failure_artifact_digest_invalid",
                    "D04 artifact_digests must retain canonical digest inventories for every source Attempt",
                    "/content/artifact_digests",
                ))
    return findings


def _semantic_digest_inventory(value: Any) -> bool:
    if isinstance(value, str):
        return _DIGEST.fullmatch(value) is not None
    if isinstance(value, list):
        return bool(value) and all(_semantic_digest_inventory(item) for item in value)
    if isinstance(value, dict):
        return bool(value) and all(
            _meaningful_text(key) and _semantic_digest_inventory(item)
            for key, item in value.items()
        )
    return False


def _semantic_source_control_authorship(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_author_principals(content, contract))
    if (
        not isinstance(content.get("source_control_revision"), str)
        or not content["source_control_revision"]
    ):
        findings.append(_semantic_problem(
            contract, "source_control_revision_missing",
            "implementation artifact must bind the authenticated source-control revision evidence",
            "/content/source_control_revision",
        ))
    findings.extend(_semantic_digest_fields(
        content, contract, ("source_control_result_digest",),
    ))
    if contract.get("id") == "E05":
        findings.extend(_semantic_digest_fields(
            content, contract,
            ("frozen_experiment_digest", "diff_evidence_digest"),
        ))
    if contract.get("id") in {"E05", "S07", "S08"}:
        findings.extend(_engineering_provenance_findings(
            content, contract, contract["id"],
        ))
    return findings


def _semantic_preflight_strict(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_engineering_provenance_findings(content, contract, "S09"))
    configurations = content.get("resolved_configurations")
    if (
        not isinstance(configurations, list) or not configurations
        or any(not isinstance(row, dict) for row in configurations)
    ):
        findings.append(_semantic_problem(
            contract, "resolved_configurations_invalid",
            "S09 must retain one or more complete authenticated configurations",
            "/content/resolved_configurations",
        ))
        return findings
    run_ids = [row.get("run_id") for row in configurations]
    if (
        any(not isinstance(run_id, str) or not run_id for run_id in run_ids)
        or run_ids != sorted(set(run_ids))
    ):
        findings.append(_semantic_problem(
            contract, "resolved_configuration_run_set_invalid",
            "S09 resolved configurations must be a canonical unique run set",
            "/content/resolved_configurations",
        ))
    for index, row in enumerate(configurations):
        findings.extend(_resolved_configuration_findings(
            row, contract, pointer=f"/content/resolved_configurations/{index}",
        ))
    if content.get("resolved_configurations_digest") != digest(configurations):
        findings.append(_semantic_problem(
            contract, "resolved_configurations_digest_mismatch",
            "S09 configuration-set digest does not bind exact ordered facts",
            "/content/resolved_configurations_digest",
        ))
    return findings


def _semantic_identity(value: Any, contract: dict[str, Any], bundle: AssetBundle) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    if isinstance(value, dict):
        identifier = value.get("id") or value.get("artifact_id")
        if identifier is not None and (not isinstance(identifier, str) or not _ID.fullmatch(identifier)):
            findings.append(finding("invalid_identity", "artifact identity is malformed", subject=contract["id"], pointer="/id", governing_asset="artifact-contracts.json"))
    return findings


def _semantic_uri_digest(value: Any, contract: dict[str, Any], bundle: AssetBundle) -> list[dict[str, Any]]:
    findings = _semantic_identity(value, contract, bundle)
    if isinstance(value, dict):
        if "content" in value and "content_digest" in value:
            try:
                actual_content_digest = digest(value["content"])
            except CanonicalError as exc:
                findings.append(finding(exc.code, str(exc), subject=contract["id"], pointer="/content", governing_asset="artifact-contracts.json"))
            else:
                if value["content_digest"] != actual_content_digest:
                    findings.append(finding("content_digest_mismatch", "artifact content_digest does not bind the canonical content bytes", subject=contract["id"], pointer="/content_digest", governing_asset="artifact-contracts.json"))
        for key, item in value.items():
            if key.endswith("_digest") and (not isinstance(item, str) or not _DIGEST.fullmatch(item)):
                findings.append(finding("bad_digest", f"{key} is not a canonical sha256 digest", subject=contract["id"], pointer=f"/{_ptr(key)}", governing_asset="artifact-contracts.json"))
            if key.endswith("_uri") and not _immutable_uri_value(item):
                findings.append(finding("unsafe_uri", f"{key} must be immutable and must not contain a query string", subject=contract["id"], pointer=f"/{_ptr(key)}", governing_asset="artifact-contracts.json"))
    return findings


def _semantic_experiment(value: Any, contract: dict[str, Any], bundle: AssetBundle) -> list[dict[str, Any]]:
    findings = _semantic_uri_digest(value, contract, bundle)
    content = value.get("content", value) if isinstance(value, dict) else value
    if isinstance(content, dict):
        protocol = content.get("protocol_channel", content.get("protocol", content.get("channel", "exploratory")))
        if protocol not in {"exploratory", "formal", "regression", "promotion"}:
            findings.append(finding("invalid_protocol", "protocol must be exploratory, formal, regression, or promotion", subject=contract["id"], pointer="/protocol", governing_asset="artifact-contracts.json"))
        if protocol == "exploratory" and content.get("promotable") is True:
            findings.append(finding("exploratory_not_promotable", "exploratory research requires the promotable classification to be false", subject=contract["id"], pointer="/content/promotable", governing_asset="artifact-contracts.json"))
    return findings


def _semantic_geometry_contract(
    value: Any,
    contract: dict[str, Any],
    bundle: AssetBundle,
) -> list[dict[str, Any]]:
    """Require explicit frame, transform-direction, and application semantics for G04."""
    del bundle
    findings: list[dict[str, Any]] = []
    content = value.get("content", value) if isinstance(value, dict) else value
    if not isinstance(content, dict):
        return findings

    frames = content.get("coordinate_frames")
    frame_ids: set[str] = set()
    if not isinstance(frames, list) or not frames:
        findings.append(finding(
            "geometry_frame_semantics_incomplete",
            "coordinate_frames must be a non-empty array of explicitly defined frames",
            subject=contract["id"], pointer="/content/coordinate_frames",
            governing_asset="artifact-contracts.json",
        ))
    else:
        for index, frame in enumerate(frames):
            pointer = f"/content/coordinate_frames/{index}"
            frame_id = frame.get("frame_id") if isinstance(frame, dict) else None
            required = ("axes", "handedness")
            global_units = content.get("units")
            complete = (
                isinstance(frame_id, str) and bool(frame_id)
                and isinstance(frame, dict)
                and all(isinstance(frame.get(key), str) and bool(frame[key]) for key in required)
                and (
                    isinstance(frame.get("units"), str) and bool(frame["units"])
                    or isinstance(global_units, (str, dict)) and bool(global_units)
                )
            )
            if not complete or frame_id in frame_ids:
                findings.append(finding(
                    "geometry_frame_semantics_incomplete",
                    "each frame requires a unique frame_id plus explicit axes, handedness, and units",
                    subject=contract["id"], pointer=pointer,
                    governing_asset="artifact-contracts.json",
                ))
            elif isinstance(frame_id, str):
                frame_ids.add(frame_id)

    transforms = content.get("transforms")
    if not isinstance(transforms, list) or not transforms:
        findings.append(finding(
            "geometry_transform_direction_ambiguous",
            "transforms must explicitly identify source and target frames",
            subject=contract["id"], pointer="/content/transforms",
            governing_asset="artifact-contracts.json",
        ))
        return findings

    for index, transform in enumerate(transforms):
        pointer = f"/content/transforms/{index}"
        if not isinstance(transform, dict):
            findings.append(finding(
                "geometry_transform_direction_ambiguous",
                "each transform must be an object with source_frame and target_frame",
                subject=contract["id"], pointer=pointer,
                governing_asset="artifact-contracts.json",
            ))
            continue
        source = transform.get("source_frame")
        target = transform.get("target_frame")
        if (
            not isinstance(source, str) or not source
            or not isinstance(target, str) or not target
            or source == target
            or source not in frame_ids or target not in frame_ids
        ):
            findings.append(finding(
                "geometry_transform_direction_ambiguous",
                "transform source_frame and target_frame must be distinct declared frames",
                subject=contract["id"], pointer=pointer,
                governing_asset="artifact-contracts.json",
            ))
        if (
            transform.get("notation") != "T_target_from_source"
            or not isinstance(transform.get("vector_convention"), str)
            or not transform.get("vector_convention")
            or not isinstance(transform.get("application"), str)
            or not transform.get("application")
        ):
            findings.append(finding(
                "geometry_transform_application_ambiguous",
                "transform notation, vector convention, and application must define T_target_from_source semantics",
                subject=contract["id"], pointer=pointer,
                governing_asset="artifact-contracts.json",
            ))
    return findings


def _semantic_augmentation_contract(
    value: Any,
    contract: dict[str, Any],
    bundle: AssetBundle,
) -> list[dict[str, Any]]:
    """Require crop operations to update pixels, intrinsics, and cross-modal geometry."""
    del bundle
    findings: list[dict[str, Any]] = []
    content = value.get("content", value) if isinstance(value, dict) else value
    if not isinstance(content, dict):
        return findings
    operations = content.get("operations")
    effects_by_operation = content.get("geometry_effects")
    if not isinstance(operations, list) or not operations:
        findings.append(finding(
            "augmentation_operations_invalid",
            "augmentation operations must be a non-empty array of explicit operations",
            subject=contract["id"], pointer="/content/operations",
            governing_asset="artifact-contracts.json",
        ))
        return findings
    operation_ids: list[str] = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            findings.append(finding(
                "augmentation_operation_invalid",
                "each augmentation operation must be an explicit object",
                subject=contract["id"], pointer=f"/content/operations/{index}",
                governing_asset="artifact-contracts.json",
            ))
            continue
        operation_kind = operation.get("type", operation.get("operation", ""))
        operation_id = operation.get("operation_id", operation.get("id"))
        if (
            not _meaningful_text(operation_id)
            or not _meaningful_text(operation_kind)
            or operation_id in operation_ids
            or not set(operation).issubset({
                "operation_id", "id", "type", "operation", "geometry_effects",
            })
        ):
            findings.append(finding(
                "augmentation_operation_invalid",
                "each augmentation operation requires one unique identity and concrete type",
                subject=contract["id"], pointer=f"/content/operations/{index}",
                governing_asset="artifact-contracts.json",
            ))
            continue
        operation_ids.append(operation_id)
        if not isinstance(operation_kind, str) or "crop" not in operation_kind.lower():
            continue
        effects = operation.get("geometry_effects")
        if effects is None and isinstance(effects_by_operation, dict) and isinstance(operation_id, str):
            effects = effects_by_operation.get(operation_id)
        intrinsics = effects.get("camera_intrinsics") if isinstance(effects, dict) else None
        complete = (
            isinstance(effects, dict)
            and effects.get("image_pixels_updated") is True
            and isinstance(intrinsics, dict)
            and intrinsics.get("principal_point_updated") is True
            and intrinsics.get("image_size_updated") is True
            and effects.get("cross_modal_geometry_updated") is True
        )
        if not complete:
            findings.append(finding(
                "augmentation_crop_geometry_incomplete",
                "image crop must update pixels, image size/principal point intrinsics, and cross-modal geometry together",
                subject=contract["id"], pointer=f"/content/operations/{index}",
                governing_asset="artifact-contracts.json",
            ))
    probabilities = content.get("probabilities")
    ordering = content.get("ordering")
    if (
        not isinstance(probabilities, dict)
        or set(probabilities) != set(operation_ids)
        or any(
            isinstance(probability, bool)
            or not isinstance(probability, (int, float, Decimal))
            or probability < 0 or probability > 1
            for probability in probabilities.values()
        )
        or not isinstance(ordering, list)
        or ordering != operation_ids
        or not isinstance(effects_by_operation, dict)
        or set(effects_by_operation) != set(operation_ids)
        or any(
            not _semantic_value_present(effect) or isinstance(effect, bool)
            for effect in effects_by_operation.values()
        )
    ):
        findings.append(finding(
            "augmentation_execution_contract_invalid",
            "G08 probabilities, ordering, and geometry_effects must exactly bind every unique operation",
            subject=contract["id"], pointer="/content",
            governing_asset="artifact-contracts.json",
        ))
    findings.extend(_semantic_source_refs(content, contract))
    return findings


_CONTEXT_SENSITIVE = re.compile(
    r"hidden[ _-]?labels?|production[ _-]?(?:token|tokens|credential|credentials|secret|secrets)|private[ _-]?key",
    re.I,
)


def _sensitive_context_pointers(value: Any, pointer: str = "") -> list[str]:
    pointers: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{pointer}/{_ptr(key)}"
            if _CONTEXT_SENSITIVE.search(key):
                pointers.append(child)
            pointers.extend(_sensitive_context_pointers(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            pointers.extend(_sensitive_context_pointers(item, f"{pointer}/{index}"))
    elif isinstance(value, str) and _CONTEXT_SENSITIVE.search(value):
        pointers.append(pointer)
    return pointers


def _semantic_context_pack(
    value: Any,
    contract: dict[str, Any],
    bundle: AssetBundle,
) -> list[dict[str, Any]]:
    """Enforce S06 least-context inclusions and explicit sensitive exclusions."""
    del bundle
    findings: list[dict[str, Any]] = []
    content = value.get("content", value) if isinstance(value, dict) else value
    if not isinstance(content, dict):
        return findings
    allowed = content.get("allowed_context")
    required_context = ("frozen_constraints", "tensor_semantics", "tests")
    if not isinstance(allowed, dict):
        findings.append(finding(
            "context_pack_required_context_missing",
            "allowed_context must explicitly contain frozen constraints, tensor semantics, and tests",
            subject=contract["id"], pointer="/content/allowed_context",
            governing_asset="artifact-contracts.json",
        ))
    else:
        for key in required_context:
            if allowed.get(key) in (None, "", [], {}):
                findings.append(finding(
                    "context_pack_required_context_missing",
                    f"least-context pack is missing required context: {key}",
                    subject=contract["id"], pointer=f"/content/allowed_context/{_ptr(key)}",
                    governing_asset="artifact-contracts.json",
                ))
        for pointer in sorted(set(_sensitive_context_pointers(allowed, "/content/allowed_context"))):
            findings.append(finding(
                "context_pack_sensitive_context_included",
                "allowed context contains hidden-label or production-credential material",
                subject=contract["id"], pointer=pointer,
                governing_asset="artifact-contracts.json",
            ))
    forbidden = content.get("forbidden_context")
    serialized = " ".join(str(item) for item in forbidden).lower() if isinstance(forbidden, list) else ""
    if not re.search(r"hidden[ _-]?labels?", serialized) or not re.search(
        r"production[ _-]?(?:tokens?|credentials?|secrets?)", serialized,
    ):
        findings.append(finding(
            "context_pack_forbidden_boundary_missing",
            "forbidden_context must explicitly exclude hidden labels and production credentials",
            subject=contract["id"], pointer="/content/forbidden_context",
            governing_asset="artifact-contracts.json",
        ))
    allowed_paths = content.get("allowed_paths")
    if not isinstance(allowed_paths, list) or not allowed_paths or any(
        not isinstance(path, str) or not path for path in allowed_paths
    ):
        findings.append(finding(
            "context_pack_allowed_paths_invalid",
            "allowed_paths must be a non-empty array of explicit paths",
            subject=contract["id"], pointer="/content/allowed_paths",
            governing_asset="artifact-contracts.json",
        ))
    return findings


_SEMANTIC_PLACEHOLDER = re.compile(
    r"^(?:tbd|todo|unknown|unspecified|not[- _]?specified|placeholder|n/?a)$",
    re.I,
)


def _meaningful_text(value: Any, *, minimum: int = 1) -> bool:
    return (
        isinstance(value, str)
        and len(value.strip()) >= minimum
        and _SEMANTIC_PLACEHOLDER.fullmatch(value.strip()) is None
    )


def _unique_texts(value: Any, *, minimum: int = 1) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= minimum
        and all(_meaningful_text(item) for item in value)
        and len(value) == len(set(value))
    )


def _semantic_value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return _meaningful_text(value)
    if isinstance(value, list):
        return bool(value) and all(_semantic_value_present(item) for item in value)
    if isinstance(value, dict):
        return bool(value) and all(
            _meaningful_text(key) and _semantic_value_present(item)
            for key, item in value.items()
        )
    return isinstance(value, (bool, int, float, Decimal))


def _contains_digest_binding(value: Any) -> bool:
    if isinstance(value, str):
        return _DIGEST.fullmatch(value) is not None
    if isinstance(value, list):
        return any(_contains_digest_binding(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_digest_binding(item) for item in value.values())
    return False


def _explicit_uncertainty_present(value: Any) -> bool:
    """An explicit ``unknown`` is valid uncertainty, while TODO text is not."""
    if isinstance(value, str):
        return bool(value.strip()) and re.fullmatch(
            r"(?:tbd|todo|placeholder|not[- _]?specified|n/?a)",
            value.strip(), re.I,
        ) is None
    if isinstance(value, list):
        return bool(value) and all(_explicit_uncertainty_present(item) for item in value)
    if isinstance(value, dict):
        return bool(value) and all(
            _meaningful_text(key) and _explicit_uncertainty_present(item)
            for key, item in value.items()
        )
    return isinstance(value, (bool, int, float, Decimal))


def _semantic_exact_content(
    content: dict[str, Any], contract: dict[str, Any], *,
    required: tuple[str, ...], optional: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    allowed = set(required) | set(optional)
    unknown = sorted(set(content) - allowed)
    missing = sorted(set(required) - set(content))
    findings: list[dict[str, Any]] = []
    if unknown:
        findings.append(_semantic_problem(
            contract, "semantic_unknown_field",
            "artifact content contains undeclared semantic fields: "
            + ", ".join(unknown),
            "/content",
        ))
    if missing:
        findings.append(_semantic_problem(
            contract, "semantic_required",
            "artifact content lacks required semantic fields: "
            + ", ".join(missing),
            "/content",
        ))
    return findings


def _semantic_authority_values(
    content: dict[str, Any], contract: dict[str, Any], field: str,
) -> list[dict[str, Any]]:
    values = content.get(field)
    # Public authority-gated commands first validate an exact unsigned
    # candidate with ``authority=[]``.  The declarative required-field rule
    # emits the single filterable absence finding; after signature
    # verification the coordinator injects consumed bindings and revalidates.
    if values == []:
        return []
    if not isinstance(values, list) or not values:
        return [_semantic_problem(
            contract, "authority_binding_invalid",
            f"{field} must be a non-empty unique list of consumed authority bindings",
            f"/content/{_ptr(field)}",
        )]

    if all(isinstance(item, str) for item in values):
        if (
            any(_DIGEST.fullmatch(item) is None for item in values)
            or len(values) != len(set(values))
        ):
            return [_semantic_problem(
                contract, "authority_binding_invalid",
                f"{field} digest bindings must be canonical and unique",
                f"/content/{_ptr(field)}",
            )]
        return []

    consumed_fields = {
        "action", "role", "principal_id", "key_digest", "subject_digest",
        "attestation_digest", "trust_snapshot_digest", "trusted_snapshot",
        "consumed_at", "attestation",
    }
    if not all(isinstance(item, dict) for item in values):
        return [_semantic_problem(
            contract, "authority_binding_invalid",
            f"{field} cannot mix digest and consumed-record authority bindings",
            f"/content/{_ptr(field)}",
        )]
    principals: list[str] = []
    attestations: list[str] = []
    for item in values:
        if (
            set(item) != consumed_fields
            or any(not _meaningful_text(item.get(name)) for name in (
                "action", "role", "principal_id",
            ))
            or any(
                not isinstance(item.get(name), str)
                or _DIGEST.fullmatch(item[name]) is None
                for name in (
                    "key_digest", "subject_digest", "attestation_digest",
                    "trust_snapshot_digest",
                )
            )
            or _parse_semantic_time(item.get("consumed_at")) is None
            or not isinstance(item.get("attestation"), dict)
            or not isinstance(item.get("trusted_snapshot"), dict)
            or digest(item["attestation"]) != item["attestation_digest"]
            or digest(item["attestation"].get("subject")) != item["subject_digest"]
            or digest(item["trusted_snapshot"]) != item["trust_snapshot_digest"]
        ):
            return [_semantic_problem(
                contract, "authority_binding_invalid",
                f"{field} contains an inexact or unauthenticated consumed authority record",
                f"/content/{_ptr(field)}",
            )]
        principals.append(item["principal_id"])
        attestations.append(item["attestation_digest"])
    if len(principals) != len(set(principals)) or len(attestations) != len(set(attestations)):
        return [_semantic_problem(
            contract, "authority_binding_invalid",
            f"{field} consumed authority principals and attestations must be unique",
            f"/content/{_ptr(field)}",
        )]
    return []


def _nonnegative_numeric_leaf(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float, Decimal)):
        return value >= 0
    if isinstance(value, dict):
        return bool(value) and all(
            _meaningful_text(key) and _nonnegative_numeric_leaf(item)
            for key, item in value.items()
        )
    return False


def _semantic_governance_principles(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    principles = content.get("principles")
    separation = content.get("separation_of_duties")
    freeze = content.get("freeze_policy")
    exception = content.get("exception_policy")
    if not _unique_texts(principles):
        findings.append(_semantic_problem(
            contract, "governance_principles_invalid",
            "G01 principles must be a non-empty unique set of executable governance statements",
            "/content/principles",
        ))
    if not isinstance(separation, dict) or separation.get("required") is not True:
        findings.append(_semantic_problem(
            contract, "separation_of_duties_not_required",
            "G01 must explicitly require separation of duties",
            "/content/separation_of_duties",
        ))
    if not isinstance(freeze, dict) or freeze.get("immutable") is not True:
        findings.append(_semantic_problem(
            contract, "freeze_policy_not_immutable",
            "G01 must make frozen protocol bytes immutable",
            "/content/freeze_policy",
        ))
    if not isinstance(exception, dict) or exception.get("authority") != "required":
        findings.append(_semantic_problem(
            contract, "exception_policy_not_governed",
            "G01 exceptions must explicitly require authority",
            "/content/exception_policy",
        ))
    findings.extend(_semantic_source_refs(content, contract))
    return findings


def _calibration_validity_valid(validity: Any) -> bool:
    """Historical calibration applies to exact bytes, without an invented expiry."""
    if not isinstance(validity, dict):
        return False
    if set(validity) == {"from", "to"}:
        return all(_meaningful_text(validity.get(k)) for k in ("from", "to")) and validity["from"] < validity["to"]
    keys = {"mode", "dataset_content_digest", "membership_digest", "calibration_content_digest"}
    return (
        set(validity) == keys and validity.get("mode") == "snapshot"
        and all(isinstance(validity.get(k), str) and _DIGEST.fullmatch(validity[k])
                for k in keys - {"mode"})
    )


def _semantic_calibration_contract(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    validity = content.get("validity")
    uncertainty = content.get("uncertainty")
    frames = content.get("frames")
    if not _meaningful_text(content.get("calibration_identity")) or not _unique_texts(frames, minimum=2):
        findings.append(_semantic_problem(
            contract, "calibration_frames_invalid",
            "G05 must identify a calibration and at least two unique governed frames",
            "/content/frames",
        ))
    if not _calibration_validity_valid(validity):
        findings.append(_semantic_problem(
            contract, "calibration_validity_invalid",
            "G05 validity must bind an exact data/calibration snapshot or an ordered source interval",
            "/content/validity",
        ))
    if not _calibration_uncertainty_valid(uncertainty, validity):
        findings.append(_semantic_problem(
            contract, "calibration_uncertainty_invalid",
            "G05 uncertainty must be a non-negative numeric bound or an explicit unmeasured diagnostic snapshot",
            "/content/uncertainty",
        ))
    findings.extend(_semantic_source_refs(content, contract))
    return findings


def _calibration_uncertainty_valid(uncertainty: Any, validity: Any) -> bool:
    if _nonnegative_numeric_leaf(uncertainty):
        return True
    return (
        isinstance(uncertainty, dict)
        and set(uncertainty) == {"status", "scope", "reason"}
        and uncertainty.get("status") == "not-measured"
        and uncertainty.get("scope") == "diagnostic-only"
        and _meaningful_text(uncertainty.get("reason"), minimum=16)
        and _calibration_validity_valid(validity)
        and validity.get("mode") == "snapshot"
    )


def _semantic_synchronization_contract(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    if not _unique_texts(content.get("clock_domains"), minimum=2):
        findings.append(_semantic_problem(
            contract, "clock_domains_invalid",
            "G06 must name at least two unique clock domains",
            "/content/clock_domains",
        ))
    if not _semantic_value_present(content.get("alignment")):
        findings.append(_semantic_problem(
            contract, "clock_alignment_invalid",
            "G06 alignment must define a concrete alignment method",
            "/content/alignment",
        ))
    if not _nonnegative_numeric_leaf(content.get("tolerance")):
        findings.append(_semantic_problem(
            contract, "clock_tolerance_invalid",
            "G06 tolerance must be an explicit non-negative numeric bound",
            "/content/tolerance",
        ))
    if not _meaningful_text(content.get("missing_policy")):
        findings.append(_semantic_problem(
            contract, "clock_missing_policy_invalid",
            "G06 missing_policy must state a deterministic missing-data disposition",
            "/content/missing_policy",
        ))
    findings.extend(_semantic_source_refs(content, contract))
    return findings


def _semantic_label_ontology(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    classes = content.get("classes")
    semantics = content.get("semantics")
    # G07 owns label semantics for both discrete and continuous targets. The
    # existing discrete branch below is unchanged; regression does not invent
    # categories or omit G07 from the original G02 reference closure.
    if "value_space" in content:
        import math
        space=content["value_space"]
        valid=isinstance(space,dict) and set(space)=={"kind","targets","units","ranges"}
        if valid:
            targets=space["targets"];units=space["units"];ranges=space["ranges"]
            valid=(space["kind"]=="continuous" and "classes" not in content
                and _meaningful_text(content.get("ontology_identity")) and _meaningful_text(content.get("version"))
                and _unique_texts(targets) and isinstance(units,dict) and set(units)==set(targets)
                and all(_meaningful_text(v) for v in units.values())
                and isinstance(ranges,dict) and set(ranges)==set(targets)
                and isinstance(semantics,dict) and set(semantics)==set(targets)
                and all(_semantic_value_present(v) for v in semantics.values()))
            if valid:
                for bounds in ranges.values():
                    if not isinstance(bounds,dict) or set(bounds)!={"min","max"}:
                        valid=False;break
                    lo,hi=bounds["min"],bounds["max"]
                    if (any(v is not None and (type(v) not in (int,float) or not math.isfinite(v)) for v in (lo,hi))
                            or lo is not None and hi is not None and lo>hi):
                        valid=False;break
        if not valid:
            findings.append(_semantic_problem(contract,"continuous_label_space_not_closed",
                "G07 continuous targets require explicit names, units, valid ranges and matching semantics; discrete classes cannot be mixed in",
                "/content/value_space"))
        findings.extend(_semantic_source_refs(content,contract))
        return findings
    if (
        not _meaningful_text(content.get("ontology_identity"))
        or not _meaningful_text(content.get("version"))
        or not _unique_texts(classes)
        or not isinstance(semantics, dict)
        or set(semantics) != set(classes if isinstance(classes, list) else [])
        or any(not _semantic_value_present(item) for item in semantics.values())
    ):
        findings.append(_semantic_problem(
            contract, "label_ontology_not_closed",
            "G07 must bind a versioned ontology whose class set exactly equals its semantic definitions",
            "/content",
        ))
    findings.extend(_semantic_source_refs(content, contract))
    return findings


def _semantic_tensor_contract(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    inputs = content.get("inputs")
    outputs = content.get("outputs")
    if (
        not _unique_texts(inputs) or not _unique_texts(outputs)
        or not set(inputs).isdisjoint(outputs)
    ):
        findings.append(_semantic_problem(
            contract, "tensor_endpoints_invalid",
            "G09 inputs and outputs must be non-empty, unique, and disjoint",
            "/content",
        ))
    shape = content.get("shape")
    if (
        not isinstance(shape, dict) or not shape
        or any(
            not _meaningful_text(name)
            or not isinstance(dimensions, list) or not dimensions
            or any(type(dimension) is not int or dimension <= 0 for dimension in dimensions)
            for name, dimensions in shape.items()
        )
    ):
        findings.append(_semantic_problem(
            contract, "tensor_shape_invalid",
            "G09 shape must provide positive integer dimensions for every declared shape entry",
            "/content/shape",
        ))
    if content.get("dtype") not in {
        "bool", "uint8", "int8", "int16", "int32", "int64",
        "float16", "bfloat16", "float32", "float64",
    }:
        findings.append(_semantic_problem(
            contract, "tensor_dtype_invalid",
            "G09 dtype must be one explicit supported tensor scalar type",
            "/content/dtype",
        ))
    if not _semantic_value_present(content.get("units")):
        findings.append(_semantic_problem(
            contract, "tensor_units_invalid",
            "G09 units must be explicit",
            "/content/units",
        ))
    findings.extend(_semantic_digest_fields(
        content, contract, ("runtime_validator_digest",),
    ))
    findings.extend(_semantic_source_refs(content, contract))
    return findings


def _semantic_robustness_contract(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    if (
        not _meaningful_text(content.get("suite_identity"))
        or not _unique_texts(content.get("stressors"))
        or not _nonnegative_numeric_leaf(content.get("thresholds"))
        or not _unique_texts(content.get("artifacts"))
    ):
        findings.append(_semantic_problem(
            contract, "robustness_contract_not_executable",
            "G11 must bind a suite, unique stressors, numeric thresholds, and retained artifact names",
            "/content",
        ))
    findings.extend(_semantic_source_refs(content, contract))
    return findings


def _semantic_deployment_contract(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    if content.get("target") not in {"staging", "shadow", "canary", "production"}:
        findings.append(_semantic_problem(
            contract, "deployment_target_invalid",
            "G12 target must be staging, shadow, canary, or production",
            "/content/target",
        ))
    if not isinstance(content.get("interface"), dict) or not content["interface"]:
        findings.append(_semantic_problem(
            contract, "deployment_interface_invalid",
            "G12 interface must be a concrete non-empty contract",
            "/content/interface",
        ))
    parity = content.get("parity")
    health = content.get("health")
    latency = content.get("latency")
    memory = content.get("memory")
    if not isinstance(parity, dict) or parity.get("required") is not True:
        findings.append(_semantic_problem(
            contract, "deployment_parity_not_required",
            "G12 must require measured deployment parity",
            "/content/parity",
        ))
    if not isinstance(health, dict) or health.get("required") is not True:
        findings.append(_semantic_problem(
            contract, "deployment_health_not_required",
            "G12 must require measured health evidence during research review",
            "/content/health",
        ))
    if not _nonnegative_numeric_leaf(latency):
        findings.append(_semantic_problem(
            contract, "deployment_latency_invalid",
            "G12 latency must contain executable non-negative thresholds",
            "/content/latency",
        ))
    if not _nonnegative_numeric_leaf(memory):
        findings.append(_semantic_problem(
            contract, "deployment_memory_invalid",
            "G12 memory must contain executable non-negative thresholds",
            "/content/memory",
        ))
    findings.extend(_semantic_source_refs(content, contract))
    return findings


def _semantic_traceability_contract(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    nodes = content.get("node_types")
    edges = content.get("edge_types")
    reciprocity = content.get("reciprocity")
    if not _unique_texts(nodes) or not _unique_texts(edges):
        findings.append(_semantic_problem(
            contract, "traceability_types_invalid",
            "G13 node_types and edge_types must be non-empty unique type sets",
            "/content",
        ))
    if (
        not isinstance(reciprocity, dict) or not reciprocity
        or any(
            not _meaningful_text(source) or not _meaningful_text(target)
            or source == target
            for source, target in reciprocity.items()
        )
    ):
        findings.append(_semantic_problem(
            contract, "traceability_reciprocity_invalid",
            "G13 reciprocity must map each declared relation to a distinct inverse relation",
            "/content/reciprocity",
        ))
    closure = content.get("closure")
    if not isinstance(closure, dict) or closure.get("required") is not True:
        findings.append(_semantic_problem(
            contract, "traceability_closure_not_required",
            "G13 must explicitly require lineage closure",
            "/content/closure",
        ))
    findings.extend(_semantic_source_refs(content, contract))
    return findings


def _semantic_baseline_spec(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    if any(not _meaningful_text(content.get(field)) for field in (
        "baseline_id", "scope", "model", "data", "evaluator",
    )):
        findings.append(_semantic_problem(
            contract, "baseline_identity_invalid",
            "B01 must bind concrete baseline, scope, model, data, and evaluator identities",
            "/content",
        ))
    if (
        not isinstance(content.get("data"), str)
        or not content["data"].startswith("G03:")
        or not isinstance(content.get("evaluator"), str)
        or not content["evaluator"].startswith("G10:")
    ):
        findings.append(_semantic_problem(
            contract, "baseline_contract_reference_invalid",
            "B01 data and evaluator must name exact G03 and G10 artifact instances",
            "/content",
        ))
    if not _nonnegative_numeric_leaf(content.get("tolerances")):
        findings.append(_semantic_problem(
            contract, "baseline_tolerances_invalid",
            "B01 tolerances must be executable non-negative numeric bounds",
            "/content/tolerances",
        ))
    return findings


def _semantic_baseline_deprecation(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    source = content.get("baseline_id")
    replacement = content.get("replacement")
    if (
        not _meaningful_text(content.get("deprecation_id"))
        or not _meaningful_text(source) or not _meaningful_text(replacement)
        or source == replacement or not _meaningful_text(content.get("reason"), minimum=8)
    ):
        findings.append(_semantic_problem(
            contract, "baseline_deprecation_invalid",
            "B04 must name a distinct replacement baseline and a concrete reason",
            "/content",
        ))
    dependents = content.get("active_dependents")
    if not isinstance(dependents, list) or any(
        not _meaningful_text(item) for item in dependents
    ) or len(dependents) != len(set(dependents)):
        findings.append(_semantic_problem(
            contract, "baseline_dependents_invalid",
            "B04 active_dependents must be an explicit unique identity list",
            "/content/active_dependents",
        ))
    findings.extend(_semantic_authority_values(content, contract, "authority"))
    return findings


def _semantic_protocol_readiness(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_digest_fields(content, contract, ("candidate_digest",)))
    gates = content.get("blocking_gates")
    expected_gate_names = [
        "baseline-reproduction", "budget", "candidate",
        "contract-preflight", "data-quality", "dataset", "evaluator",
        "split",
    ]
    if (
        not isinstance(gates, list) or not gates
        or any(
            not isinstance(gate, dict) or set(gate) != {"name", "status"}
            or not _meaningful_text(gate.get("name"))
            or gate.get("status") != "pass"
            for gate in gates
        )
        or len({gate["name"] for gate in gates if isinstance(gate, dict) and "name" in gate}) != len(gates)
    ):
        findings.append(_semantic_problem(
            contract, "blocking_gates_not_passing",
            "E02 must contain one unique, passing record for every blocking gate",
            "/content/blocking_gates",
        ))
    elif [gate["name"] for gate in gates] != expected_gate_names:
        findings.append(_semantic_problem(
            contract, "readiness_gate_closure_mismatch",
            "E02 blocking_gates must be the complete canonical candidate/baseline/data/evaluator/budget/platform gate set",
            "/content/blocking_gates",
        ))
    if any(not _meaningful_text(content.get(field)) for field in (
        "experiment_id", "independent_reviewer",
    )):
        findings.append(_semantic_problem(
            contract, "protocol_reviewer_invalid",
            "E02 must bind the experiment and stable independent reviewer identity",
            "/content",
        ))
    findings.extend(_semantic_authority_values(content, contract, "authority"))
    return findings


def _semantic_amendment(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_digest_fields(
        content, contract, ("old_digest", "candidate_digest"),
    ))
    if (
        any(not _meaningful_text(content.get(field)) for field in (
            "amendment_id", "experiment_id",
        ))
        or type(content.get("timing_revision")) is not int
        or content["timing_revision"] < 0
        or content.get("classification") != "non-material"
        or content.get("old_digest") == content.get("candidate_digest")
    ):
        findings.append(_semantic_problem(
            contract, "amendment_semantics_invalid",
            "E06 must be a revision-bound non-material change between distinct immutable bytes",
            "/content",
        ))
    findings.extend(_semantic_authority_values(content, contract, "authority"))
    return findings


def _semantic_deviation(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    if (
        any(not _meaningful_text(content.get(field)) for field in (
            "deviation_id", "experiment_id",
        ))
        or not isinstance(content.get("first_affected_event"), str)
        or _DIGEST.fullmatch(content["first_affected_event"]) is None
        or not isinstance(content.get("observed_facts"), dict)
        or not content["observed_facts"]
        or not _meaningful_text(content.get("impact"), minimum=8)
    ):
        findings.append(_semantic_problem(
            contract, "protocol_deviation_invalid",
            "E07 must bind the first affected event, observed facts, and assessed impact",
            "/content",
        ))
    observed = content.get("observed_facts")
    if (
        not isinstance(observed, dict)
        or not all(
            _meaningful_text(name)
            and _semantic_value_present(fact)
            and not isinstance(fact, bool)
            for name, fact in observed.items()
        )
        or not _contains_digest_binding(observed)
    ):
        findings.append(_semantic_problem(
            contract, "deviation_fact_binding_invalid",
            "E07 observed_facts must be concrete and include an immutable authenticated digest binding",
            "/content/observed_facts",
        ))
    findings.extend(_semantic_authority_values(content, contract, "authority"))
    return findings


def _semantic_platform_receipt(
    content: dict[str, Any], contract: dict[str, Any],
) -> list[dict[str, Any]]:
    receipt = content.get("receipt")
    expected = {
        "request_id", "request_digest", "result_id", "result_digest",
        "receipt_digest", "producer_identity", "producer_principal_id",
        "producer_attestation_digest",
    }
    if (
        not isinstance(receipt, dict) or set(receipt) != expected
        or any(
            not _meaningful_text(receipt.get(field))
            for field in (
                "request_id", "result_id", "producer_identity",
                "producer_principal_id",
            )
        )
        or any(
            not isinstance(receipt.get(field), str)
            or _DIGEST.fullmatch(receipt[field]) is None
            for field in (
                "request_digest", "result_digest", "receipt_digest",
                "producer_attestation_digest",
            )
        )
    ):
        return [_semantic_problem(
            contract, "platform_receipt_invalid",
            "platform manifest receipt must close exact request, result, producer, and attestation identities",
            "/content/receipt",
        )]
    return []


def _semantic_dataset_manifest(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_platform_manifest_strict(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    if any(not _meaningful_text(content.get(field)) for field in (
        "dataset_identity", "version", "instance_key", "immutable_uri",
    )):
        findings.append(_semantic_problem(
            contract, "dataset_manifest_identity_invalid",
            "R04 must bind a stable versioned dataset instance and immutable URI",
            "/content",
        ))
    findings.extend(_semantic_platform_receipt(content, contract))
    return findings


def _semantic_calibration_manifest(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_platform_manifest_strict(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    if any(not _meaningful_text(content.get(field)) for field in (
        "calibration_identity", "version", "instance_key", "immutable_uri",
    )):
        findings.append(_semantic_problem(
            contract, "calibration_manifest_identity_invalid",
            "R06 must bind a stable versioned calibration instance and immutable URI",
            "/content",
        ))
    validity = content.get("validity")
    if not _calibration_validity_valid(validity):
        findings.append(_semantic_problem(
            contract, "calibration_manifest_validity_invalid",
            "R06 validity must bind an exact data/calibration snapshot or an ordered source interval",
            "/content/validity",
        ))
    if isinstance(validity, dict) and validity.get("mode") == "snapshot" and validity.get("calibration_content_digest") != content.get("content_digest"):
        findings.append(_semantic_problem(contract, "calibration_snapshot_mismatch",
            "R06 snapshot must bind its actual calibration content digest", "/content/validity"))
    findings.extend(_semantic_platform_receipt(content, contract))
    return findings


def _semantic_evidence_summary(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    """D02-specific entry point over the shared exact-Attempt aggregation kernel."""
    return _semantic_evidence_section_sources(value, contract, bundle)










def _semantic_product_requirement(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_exact_content(
        content, contract,
        required=("requirement_id", "uri", "owner", "impact", "non_goals"),
    ))
    if (
        not isinstance(content.get("requirement_id"), str)
        or _ID.fullmatch(content["requirement_id"]) is None
        or not _meaningful_text(content.get("owner"))
        or not _meaningful_text(content.get("impact"), minimum=8)
    ):
        findings.append(_semantic_problem(
            contract, "product_requirement_not_executable",
            "S01 must bind a stable requirement, owner, and concrete impact statement",
            "/content",
        ))
    uri = content.get("uri")
    if (
        not isinstance(uri, str)
        or re.fullmatch(r"[a-z][a-z0-9+.-]*://[^?#\\]+", uri, re.I) is None
        or "/latest" in uri.lower() or uri.lower().endswith("/head")
    ):
        findings.append(_semantic_problem(
            contract, "product_requirement_uri_invalid",
            "S01 uri must be an absolute query-free Product Requirement/JIRA reference without a floating revision",
            "/content/uri",
        ))
    if not _unique_texts(content.get("non_goals")):
        findings.append(_semantic_problem(
            contract, "product_requirement_non_goals_invalid",
            "S01 non_goals must explicitly bound at least one excluded outcome",
            "/content/non_goals",
        ))
    return findings


def _research_statement_units(value: Any, words: int, ideographs: int) -> bool:
    """Retain word checks while supporting Chinese text without inserted spaces."""
    return isinstance(value, str) and (
        len(value.split()) >= words
        or len(re.findall(r"[\u3400-\u9fff]", value)) >= ideographs
    )


def _semantic_research_brief(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_exact_content(
        content, contract,
        required=(
            "brief_id", "requirement_id", "hypothesis", "mechanism",
            "uncertainty", "history", "source_refs",
        ),
        optional=("target_as_must", "ml_contract_applicability"),
    ))
    if any(
        not isinstance(content.get(field), str)
        or _ID.fullmatch(content[field]) is None
        for field in ("brief_id", "requirement_id")
    ):
        findings.append(_semantic_problem(
            contract, "research_brief_identity_invalid",
            "S02 must bind stable brief and requirement identities",
            "/content",
        ))
    hypothesis = content.get("hypothesis")
    if (
        not _meaningful_text(hypothesis, minimum=16)
        or not _research_statement_units(hypothesis, 3, 12)
    ):
        findings.append(_semantic_problem(
            contract, "research_hypothesis_not_falsifiable",
            "S02 hypothesis must be a concrete, non-placeholder falsifiable statement",
            "/content/hypothesis",
        ))
    if (
        not _meaningful_text(content.get("mechanism"), minimum=12)
        or not _research_statement_units(content["mechanism"], 2, 8)
        if isinstance(content.get("mechanism"), str) else True
    ):
        findings.append(_semantic_problem(
            contract, "research_mechanism_invalid",
            "S02 mechanism must state a concrete causal mechanism",
            "/content/mechanism",
        ))
    if not _explicit_uncertainty_present(content.get("uncertainty")):
        findings.append(_semantic_problem(
            contract, "research_uncertainty_invalid",
            "S02 uncertainty must retain explicit unknowns or bounds",
            "/content/uncertainty",
        ))
    if not _unique_texts(content.get("history")):
        findings.append(_semantic_problem(
            contract, "research_history_invalid",
            "S02 history must retain at least one exact prior-work reference or new-experiment statement",
            "/content/history",
        ))
    if "target_as_must" in content and not isinstance(content["target_as_must"], bool):
        findings.append(_semantic_problem(
            contract, "research_target_flag_invalid",
            "S02 target_as_must must be an explicit boolean when present",
            "/content/target_as_must",
        ))
    applicability = content.get("ml_contract_applicability")
    if applicability is not None:
        required_ids = applicability.get("required_contract_ids") \
            if isinstance(applicability, dict) else None
        if (
            not isinstance(applicability, dict)
            or set(applicability) != {"required_contract_ids"}
            or not isinstance(required_ids, list) or not required_ids
            or any(
                not isinstance(item, str)
                or item not in {f"G{index:02d}" for index in range(1, 14)}
                for item in required_ids
            )
            or len(required_ids) != len(set(required_ids))
        ):
            findings.append(_semantic_problem(
                contract, "research_contract_applicability_invalid",
                "S02 ML-contract applicability must be an exact unique G01-G13 identity set",
                "/content/ml_contract_applicability",
            ))
    findings.extend(_semantic_source_refs(content, contract))
    return findings


def _semantic_planning_readiness(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    fields = (
        "planning_readiness", "immutable_experiment_readiness",
        "post_implementation_preflight_applicability",
    )
    statuses = {
        field: content.get(field, {}).get("status")
        if isinstance(content.get(field), dict) else None
        for field in fields
    }
    if (
        statuses["planning_readiness"] != "pass"
        or statuses["immutable_experiment_readiness"] != "pass"
        or statuses["post_implementation_preflight_applicability"]
        not in {"required", "not-applicable"}
    ):
        findings.append(_semantic_problem(
            contract, "planning_readiness_trio_invalid",
            "S04 must independently pass planning and immutable-experiment readiness and explicitly classify preflight applicability",
            "/content",
        ))
    return findings


def _portable_story_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or "?" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and path.as_posix() == value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _semantic_engineering_stories(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    if not _meaningful_text(content.get("story_id")) or not _unique_texts(
        content.get("acceptance_ids"),
    ):
        findings.append(_semantic_problem(
            contract, "engineering_story_acceptance_invalid",
            "S05 must bind a stable story and unique acceptance IDs",
            "/content",
        ))
    paths = content.get("allowed_paths")
    if (
        not isinstance(paths, list) or not paths
        or any(not _portable_story_path(path) for path in paths)
        or len(paths) != len(set(paths))
    ):
        findings.append(_semantic_problem(
            contract, "engineering_story_paths_invalid",
            "S05 allowed_paths must be unique normalized relative POSIX paths",
            "/content/allowed_paths",
        ))
    dependencies = content.get("dependencies")
    if (
        not isinstance(dependencies, list) or not dependencies
        or any(
            not isinstance(item, str) or _ARTIFACT_REF.fullmatch(item) is None
            for item in dependencies
        )
        or len(dependencies) != len(set(dependencies))
    ):
        findings.append(_semantic_problem(
            contract, "engineering_story_dependencies_invalid",
            "S05 dependencies must be exact unique artifact-instance references",
            "/content/dependencies",
        ))
    return findings


def _semantic_independent_review(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_digest_fields(content, contract, ("evidence_digest",)))
    reviewers = content.get("reviewers")
    roles = content.get("roles")
    if (
        not _unique_texts(reviewers) or not _unique_texts(roles)
        or len(reviewers) != len(roles)
    ):
        findings.append(_semantic_problem(
            contract, "independent_review_principals_invalid",
            "S10 reviewers and roles must be non-empty unique one-to-one identity sets",
            "/content",
        ))
    lenses = content.get("five_lenses")
    expected_lenses = {
        "protocol", "statistics", "failure_cases", "reproducibility",
        "deployment",
    }
    if (
        not isinstance(lenses, dict) or set(lenses) != expected_lenses
        or any(value not in {"pass", "fail", "inconclusive"} for value in lenses.values())
    ):
        findings.append(_semantic_problem(
            contract, "independent_review_lenses_invalid",
            "S10 must record exactly the five independent review lenses with canonical dispositions",
            "/content/five_lenses",
        ))
    if not _meaningful_text(content.get("deviation_disposition")):
        findings.append(_semantic_problem(
            contract, "independent_review_deviation_invalid",
            "S10 must explicitly dispose every protocol deviation",
            "/content/deviation_disposition",
        ))
    return findings


def _parse_semantic_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None




def _semantic_exception(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_exact_content(
        content, contract,
        required=(
            "exception_id", "scope", "reason", "subjects",
            "expires_or_terminal", "authority", "child_dispositions",
        ),
    ))
    if any(not _meaningful_text(content.get(field)) for field in (
        "exception_id", "scope", "reason", "expires_or_terminal",
    )) or not isinstance(content.get("subjects"), dict) or not content["subjects"]:
        findings.append(_semantic_problem(
            contract, "governed_exception_invalid",
            "S15 must bind a bounded scope, reason, subjects, and expiry/terminal condition",
            "/content",
        ))
    if not isinstance(content.get("child_dispositions"), dict):
        findings.append(_semantic_problem(
            contract, "exception_child_dispositions_invalid",
            "S15 child_dispositions must explicitly account affected children",
            "/content/child_dispositions",
        ))
    findings.extend(_semantic_authority_values(content, contract, "authority"))
    return findings


def _semantic_cancellation(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_exact_content(
        content, contract,
        required=(
            "cancellation_id", "scope", "reason", "subjects", "authority",
            "child_dispositions",
        ),
    ))
    if any(not _meaningful_text(content.get(field)) for field in (
        "cancellation_id", "scope", "reason",
    )) or not isinstance(content.get("subjects"), dict) or not content["subjects"]:
        findings.append(_semantic_problem(
            contract, "cancellation_record_invalid",
            "S16 must bind a stable cancellation, scope, reason, and subject set",
            "/content",
        ))
    if not isinstance(content.get("child_dispositions"), dict):
        findings.append(_semantic_problem(
            contract, "cancellation_child_dispositions_invalid",
            "S16 child_dispositions must explicitly account affected children",
            "/content/child_dispositions",
        ))
    findings.extend(_semantic_authority_values(content, contract, "authority"))
    return findings


def _semantic_closure(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_exact_content(
        content, contract,
        required=(
            "closure_id", "terminal_subjects", "child_accounting",
            "open_handoffs", "authority", "anchor",
        ),
        optional=("source_refs",),
    ))
    if not _meaningful_text(content.get("closure_id")) or not _unique_texts(
        content.get("terminal_subjects"),
    ) or not _unique_texts(content.get("child_accounting")):
        findings.append(_semantic_problem(
            contract, "closure_accounting_invalid",
            "S17 must bind terminal subjects and explicit child accounting",
            "/content",
        ))
    if content.get("open_handoffs") != ["none"]:
        findings.append(_semantic_problem(
            contract, "closure_open_handoffs_invalid",
            "S17 cannot close while any handoff is open",
            "/content/open_handoffs",
        ))
    anchor = content.get("anchor")
    if not isinstance(anchor, str) or (
        not anchor.startswith("provisional:sha256:")
        and _DIGEST.fullmatch(anchor) is None
    ):
        findings.append(_semantic_problem(
            contract, "closure_anchor_invalid",
            "S17 anchor must bind a canonical provisional or accepted ledger head",
            "/content/anchor",
        ))
    findings.extend(_semantic_authority_values(content, contract, "authority"))
    source_refs = content.get("source_refs")
    if source_refs is not None and not isinstance(source_refs, dict):
        findings.append(_semantic_problem(
            contract, "closure_source_refs_invalid",
            "S17 source_refs must be an object when present",
            "/content/source_refs",
        ))
    return findings






_SCIENTIFIC_OUTCOMES = {
    "hypothesis_supported", "hypothesis_rejected", "inconclusive", "invalid",
}
_ATTEMPT_TERMINAL = {"successful", "failed", "cancelled", "invalid"}


def validate_scientific_rule(frozen_rules):
    """Validate the original closed scientific rule without making a decision."""
    if not isinstance(frozen_rules, dict):
        raise ContractError("scientific_rule_invalid", "frozen_rules must be an object")
    expected_rule = {
        "schema": "sdm-scientific-rule/v1",
        "inconclusive_rule": "underpowered_or_no_decisive_result",
        "invalid_rule": "protocol_invalid",
    }
    for key, expected in expected_rule.items():
        if frozen_rules.get(key) != expected:
            raise ContractError(
                "scientific_rule_invalid",
                f"frozen rule {key} must equal {expected}",
                pointer=f"/frozen_rules/{_ptr(key)}",
            )
    support_name = frozen_rules.get("support_predicate")
    rejection_name = frozen_rules.get("rejection_predicate")
    if (
        not isinstance(support_name, str) or not _ID.fullmatch(support_name)
        or not isinstance(rejection_name, str) or not _ID.fullmatch(rejection_name)
        or support_name == rejection_name
    ):
        raise ContractError(
            "scientific_rule_invalid",
            "support_predicate and rejection_predicate must be distinct portable fact names",
            pointer="/frozen_rules",
        )
    return support_name, rejection_name


def evaluate_scientific_decision(
    frozen_rules: dict[str, Any],
    planned_runs: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate the frozen D05 rule from fully accounted terminal facts.

    This function deliberately consumes already-governed facts rather than
    computing platform metrics.  The frozen rule names two boolean predicates
    in ``evidence.predicates``; it never supplies their values.  Consequently
    the coordinator can validate a scientific decision without fabricating or
    recomputing a platform-owned observation.
    """
    support_name, rejection_name = validate_scientific_rule(frozen_rules)
    if not isinstance(planned_runs, list) or not planned_runs:
        raise ContractError("scientific_accounting_incomplete", "planned_runs must be a non-empty array")
    if not isinstance(attempts, list) or not attempts:
        raise ContractError("scientific_accounting_incomplete", "attempts must be a non-empty array")
    if not isinstance(evidence, dict):
        raise ContractError("scientific_evidence_invalid", "evidence must be an object")

    planned_by_attempt: dict[str, str] = {}
    run_ids: list[str] = []
    for run_index, run in enumerate(planned_runs):
        if not isinstance(run, dict):
            raise ContractError("scientific_plan_invalid", "each planned run must be an object", pointer=f"/planned_runs/{run_index}")
        run_id = run.get("run_id")
        required_attempt_ids = run.get("required_attempt_ids")
        if not isinstance(run_id, str) or not _ID.fullmatch(run_id) or run_id in run_ids:
            raise ContractError("scientific_plan_invalid", "planned run IDs must be unique portable identities", pointer=f"/planned_runs/{run_index}/run_id")
        if not isinstance(required_attempt_ids, list) or not required_attempt_ids:
            raise ContractError("scientific_accounting_incomplete", "each planned run requires at least one Attempt", pointer=f"/planned_runs/{run_index}/required_attempt_ids")
        run_ids.append(run_id)
        for attempt_id in required_attempt_ids:
            if not isinstance(attempt_id, str) or not _ID.fullmatch(attempt_id) or attempt_id in planned_by_attempt:
                raise ContractError("scientific_plan_invalid", "required Attempt IDs must be unique portable identities", pointer=f"/planned_runs/{run_index}/required_attempt_ids")
            planned_by_attempt[attempt_id] = run_id

    seen_attempts: dict[str, dict[str, Any]] = {}
    by_status = {status: [] for status in sorted(_ATTEMPT_TERMINAL)}
    invalid_attempt_ids: list[str] = []
    system_failure_attempt_ids: list[str] = []
    for attempt_index, attempt in enumerate(attempts):
        if not isinstance(attempt, dict):
            raise ContractError("scientific_accounting_incomplete", "each Attempt must be an object", pointer=f"/attempts/{attempt_index}")
        attempt_id = attempt.get("attempt_id")
        run_id = attempt.get("run_id")
        status = attempt.get("terminal_status")
        validity = attempt.get("validity")
        system_failure = attempt.get("system_failure")
        if not isinstance(attempt_id, str) or attempt_id not in planned_by_attempt or attempt_id in seen_attempts:
            raise ContractError("scientific_accounting_incomplete", "Attempt is duplicate or absent from the frozen plan", pointer=f"/attempts/{attempt_index}/attempt_id")
        if run_id != planned_by_attempt[attempt_id]:
            raise ContractError("scientific_accounting_incomplete", "Attempt is bound to the wrong planned Run", pointer=f"/attempts/{attempt_index}/run_id")
        if attempt.get("accounted") is not True or status not in _ATTEMPT_TERMINAL:
            raise ContractError("scientific_accounting_incomplete", "every Attempt must be explicitly accounted and terminal", pointer=f"/attempts/{attempt_index}")
        if validity not in {"valid", "invalid"} or not isinstance(system_failure, bool):
            raise ContractError("scientific_attempt_invalid", "Attempt validity and system_failure must be explicit", pointer=f"/attempts/{attempt_index}")
        if status == "invalid" and validity != "invalid":
            raise ContractError("scientific_attempt_invalid", "an invalid terminal Attempt must carry invalid validity", pointer=f"/attempts/{attempt_index}/validity")
        seen_attempts[attempt_id] = attempt
        by_status[status].append(attempt_id)
        if validity == "invalid":
            invalid_attempt_ids.append(attempt_id)
        if system_failure:
            system_failure_attempt_ids.append(attempt_id)

    missing_attempts = sorted(set(planned_by_attempt) - set(seen_attempts))
    if missing_attempts:
        missing_runs = sorted({planned_by_attempt[attempt_id] for attempt_id in missing_attempts})
        raise ContractError(
            "scientific_accounting_incomplete",
            "required Runs/Attempts are not fully accounted: "
            f"runs={missing_runs}, attempts={missing_attempts}",
            pointer="/attempts",
        )
    if set(seen_attempts) != set(planned_by_attempt):
        raise ContractError("scientific_accounting_incomplete", "the Attempt set does not exactly match the frozen plan", pointer="/attempts")

    protocol_invalid = evidence.get("protocol_invalid")
    underpowered = evidence.get("underpowered")
    predicates = evidence.get("predicates")
    if not isinstance(protocol_invalid, bool) or not isinstance(underpowered, bool) or not isinstance(predicates, dict):
        raise ContractError("scientific_evidence_invalid", "protocol_invalid, underpowered, and predicates must be explicit", pointer="/evidence")
    support = predicates.get(support_name)
    rejected = predicates.get(rejection_name)
    if not isinstance(support, bool) or not isinstance(rejected, bool):
        raise ContractError("scientific_evidence_invalid", "both frozen predicate facts must be boolean", pointer="/evidence/predicates")
    if support and rejected:
        raise ContractError(
            "scientific_rule_not_mutually_exclusive",
            "support and rejection predicates cannot both be true",
            pointer="/evidence/predicates",
        )

    # Protocol/Attempt validity is a scientific fact.  A failed or cancelled
    # system Attempt, by itself, remains a visible accounting fact and does not
    # turn the protocol invalid.
    if protocol_invalid:
        outcome = "invalid"
    elif underpowered:
        outcome = "inconclusive"
    elif rejected:
        outcome = "hypothesis_rejected"
    elif support:
        outcome = "hypothesis_supported"
    else:
        outcome = "inconclusive"
    if outcome not in _SCIENTIFIC_OUTCOMES:  # defensive proof of exhaustiveness
        raise ContractError("scientific_outcome_internal", "frozen decision rule was not exhaustive")
    accounting_attempts = [
        {
            "attempt_id": attempt_id,
            "run_id": seen_attempts[attempt_id]["run_id"],
            "terminal_status": seen_attempts[attempt_id]["terminal_status"],
            "accounted": True,
            "validity": seen_attempts[attempt_id]["validity"],
            "system_failure": seen_attempts[attempt_id]["system_failure"],
        }
        for attempt_id in sorted(seen_attempts)
    ]
    return {
        "schema": "sdm-scientific-evaluation/v1",
        "outcome": outcome,
        "rule_digest": digest(frozen_rules),
        "planned_run_ids": sorted(run_ids),
        "accounted_attempt_ids": sorted(seen_attempts),
        "accounting": {
            "planned_runs": [
                {
                    "run_id": run["run_id"],
                    "required_attempt_ids": sorted(run["required_attempt_ids"]),
                }
                for run in sorted(planned_runs, key=lambda item: item["run_id"])
            ],
            "attempts": accounting_attempts,
        },
        "attempts_by_terminal_status": {
            status: sorted(values) for status, values in sorted(by_status.items())
        },
        "invalid_attempt_ids": sorted(invalid_attempt_ids),
        "system_failure_attempt_ids": sorted(system_failure_attempt_ids),
        "underpowered": underpowered,
        "predicate_results": {
            support_name: support,
            rejection_name: rejected,
        },
    }


def _semantic_scientific(value: Any, contract: dict[str, Any], bundle: AssetBundle) -> list[dict[str, Any]]:
    findings = _semantic_uri_digest(value, contract, bundle)
    content = value.get("content", value) if isinstance(value, dict) else value
    if isinstance(content, dict) and "outcome" in content and content["outcome"] not in _SCIENTIFIC_OUTCOMES:
        findings.append(finding("invalid_scientific_outcome", "scientific outcome is not canonical", subject=contract["id"], pointer="/outcome", governing_asset="artifact-contracts.json"))
    if contract.get("id") == "D05" and isinstance(content, dict):
        findings.extend(_semantic_exact_content(
            content, contract,
            required=(
                "decision_id", "experiment_id", "frozen_spec_digest",
                "evidence_bundle_digest", "rule_digest", "outcome",
                "reviewers", "authority", "decision_context",
            ),
            optional=("supersession_reason",),
        ))
        if any(
            not isinstance(content.get(name), str)
            or _ID.fullmatch(content[name]) is None
            for name in ("decision_id", "experiment_id")
        ):
            findings.append(_semantic_problem(
                contract, "scientific_decision_identity_invalid",
                "D05 must bind exact portable Decision and Experiment identities",
                "/content",
            ))
        findings.extend(_semantic_digest_fields(content, contract, (
            "frozen_spec_digest", "evidence_bundle_digest", "rule_digest",
        )))
        reviewers = content.get("reviewers")
        authority = content.get("authority")
        reviewers_valid = (
            isinstance(reviewers, list) and bool(reviewers)
            and all(
                isinstance(principal, str)
                and _ID.fullmatch(principal) is not None
                for principal in reviewers
            )
        )
        if reviewers_valid:
            reviewers_valid = reviewers == sorted(set(reviewers))
        authority_valid = (
            isinstance(authority, list) and bool(authority)
            and all(
                isinstance(attestation, str)
                and _DIGEST.fullmatch(attestation) is not None
                for attestation in authority
            )
        )
        if authority_valid:
            authority_valid = authority == sorted(set(authority))
        if not (
            reviewers_valid and authority_valid
            and len(reviewers) == len(authority)
        ):
            findings.append(_semantic_problem(
                contract, "scientific_decision_authority_invalid",
                "D05 reviewers and authority must be equal-cardinality canonical principal/attestation sets",
                "/content",
            ))
        context = content.get("decision_context")
        if not isinstance(context, dict) or set(context) != {
            "frozen_rules", "planned_runs", "attempts", "evidence",
        }:
            findings.append(finding("scientific_decision_context_required", "D05 requires executable frozen-rule and complete-attempt context", subject="D05", pointer="/content/decision_context", governing_asset="artifact-contracts.json"))
            return findings
        try:
            evaluated = evaluate_scientific_decision(
                context.get("frozen_rules"),
                context.get("planned_runs"),
                context.get("attempts"),
                context.get("evidence"),
            )
        except ContractError as exc:
            findings.append(finding(exc.code, str(exc), subject="D05", pointer=exc.pointer or "/content/decision_context", governing_asset="artifact-contracts.json"))
            return findings
        if content.get("outcome") != evaluated["outcome"]:
            findings.append(finding("scientific_outcome_mismatch", "D05 outcome does not equal the executable frozen-rule result", subject="D05", pointer="/content/outcome", governing_asset="artifact-contracts.json"))
        if content.get("rule_digest") != evaluated["rule_digest"]:
            findings.append(finding("scientific_rule_digest_mismatch", "D05 rule_digest does not bind the canonical frozen rule", subject="D05", pointer="/content/rule_digest", governing_asset="artifact-contracts.json"))
    return findings


def _semantic_lineage(value: Any, contract: dict[str, Any], bundle: AssetBundle) -> list[dict[str, Any]]:
    findings = _semantic_uri_digest(value, contract, bundle)
    if isinstance(value, dict) and value.get("parent_id") == value.get("id"):
        findings.append(finding("lineage_cycle", "an object cannot be its own parent", subject=contract["id"], pointer="/parent_id", governing_asset="artifact-contracts.json"))
    return findings


def _semantic_handoff(value: Any, contract: dict[str, Any], bundle: AssetBundle) -> list[dict[str, Any]]:
    findings = _semantic_uri_digest(value, contract, bundle)
    if isinstance(value, dict) and "operation" in value:
        try:
            bundle.operation_contract(value["operation"])
        except ContractError as exc:
            findings.append(finding(exc.code, str(exc), subject=contract["id"], pointer="/operation", governing_asset="platform-operations.json"))
    return findings


def _semantic_authority_attestation(
    value: Any,
    contract: dict[str, Any],
    bundle: AssetBundle,
) -> list[dict[str, Any]]:
    findings = _semantic_generic(value, contract, bundle)
    if not isinstance(value, dict):
        return findings
    snapshot = value.get("trust_snapshot")
    if isinstance(snapshot, dict) and value.get("trust_snapshot_digest") != digest(snapshot):
        findings.append(finding(
            "trust_snapshot_digest_mismatch",
            "authority attestation does not bind its canonical trust snapshot",
            subject=contract["id"], pointer="/trust_snapshot_digest",
            governing_asset="artifact-contracts.json",
        ))
    public_key = value.get("public_key_pem")
    subject = value.get("subject")
    if isinstance(public_key, str) and isinstance(subject, dict):
        if subject.get("key_digest") != digest_bytes(public_key.encode("utf-8")):
            findings.append(finding(
                "authority_key_digest_mismatch",
                "signed authority subject does not bind the embedded public key bytes",
                subject=contract["id"], pointer="/subject/key_digest",
                governing_asset="artifact-contracts.json",
            ))
    signature = value.get("signature")
    if isinstance(signature, str):
        try:
            decoded = base64.b64decode(signature + "=" * (-len(signature) % 4), validate=True)
        except (ValueError, base64.binascii.Error):
            decoded = b""
        if len(decoded) != 64:
            findings.append(finding(
                "signature_encoding_invalid",
                "Ed25519 signature must decode to exactly 64 bytes",
                subject=contract["id"], pointer="/signature",
                governing_asset="artifact-contracts.json",
            ))
    return findings


def _semantic_bmad_links(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    """Require immutable, versioned BMAD planning-object references for S03."""

    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    fields = {
        "product_brief": "product-brief",
        "prd": "prd",
        "architecture": "architecture",
    }
    findings.extend(_semantic_exact_content(
        content, contract, required=(*fields, "source_refs"),
    ))
    uris: list[str] = []
    for field, expected_kind in fields.items():
        pointer = f"/content/{_ptr(field)}"
        link = content.get(field)
        if not isinstance(link, dict) or set(link) != {
            "kind", "uri", "digest", "revision",
        }:
            findings.append(_semantic_problem(
                contract, "bmad_link_shape_invalid",
                f"{field} must be an exact kind/uri/digest/revision object",
                pointer,
            ))
            continue
        if link.get("kind") != expected_kind:
            findings.append(_semantic_problem(
                contract, "bmad_link_kind_mismatch",
                f"{field} must identify BMAD kind {expected_kind}",
                f"{pointer}/kind",
            ))
        uri = link.get("uri")
        try:
            parsed = urlsplit(uri) if isinstance(uri, str) else None
        except ValueError:
            parsed = None
        path = PurePosixPath(parsed.path) if parsed is not None else None
        if (
            parsed is None
            or uri != uri.strip()
            or parsed.scheme != "bmad"
            or parsed.netloc != expected_kind
            or not parsed.path or parsed.path == "/"
            or parsed.query or parsed.fragment
            or parsed.username is not None or parsed.password is not None
            or "\\" in uri or "%" in uri
            or "//" in parsed.path
            or path is None or path.as_posix() != parsed.path
            or any(part in {"", ".", ".."} for part in path.parts[1:])
            or any(
                part.lower() in {
                    "current", "head", "latest", "main", "master", "tip",
                    "trunk",
                }
                for part in path.parts[1:]
            )
        ):
            findings.append(_semantic_problem(
                contract, "bmad_link_uri_invalid",
                f"{field} must use an immutable normalized bmad://{expected_kind}/... URI without query or fragment",
                f"{pointer}/uri",
            ))
        else:
            uris.append(uri)
        if (
            not isinstance(link.get("digest"), str)
            or _DIGEST.fullmatch(link["digest"]) is None
        ):
            findings.append(_semantic_problem(
                contract, "bmad_link_digest_invalid",
                f"{field} must bind the canonical digest of the BMAD object",
                f"{pointer}/digest",
            ))
        revision = link.get("revision")
        if (
            not isinstance(revision, str)
            or _BMAD_REVISION.fullmatch(revision) is None
            or revision.lower() in {
                "current", "head", "latest", "main", "master", "tip",
                "trunk",
            }
        ):
            findings.append(_semantic_problem(
                contract, "bmad_link_revision_invalid",
                f"{field} must bind a stable non-floating BMAD revision",
                f"{pointer}/revision",
            ))
    if len(uris) != len(set(uris)):
        findings.append(_semantic_problem(
            contract, "bmad_link_identity_duplicate",
            "product brief, PRD, and architecture must be distinct BMAD objects",
            "/content",
        ))
    source_refs = content.get("source_refs")
    if (
        not isinstance(source_refs, dict)
        or len(source_refs) != 1
        or any(
            not isinstance(node, str)
            or _ARTIFACT_REF.fullmatch(node) is None
            or not node.startswith("S01:")
            or not isinstance(bound, str)
            or _DIGEST.fullmatch(bound) is None
            for node, bound in source_refs.items()
        )
    ):
        findings.append(_semantic_problem(
            contract, "bmad_requirement_binding_invalid",
            "S03 source_refs must bind exactly one immutable S01 instance digest",
            "/content/source_refs",
        ))
    return findings


def _semantic_traceability_graph(
    value: Any, contract: dict[str, Any], bundle: AssetBundle,
) -> list[dict[str, Any]]:
    """Validate a conditional, replay-derived T01 graph at any lifecycle row."""

    findings = _semantic_generic(value, contract, bundle)
    content = _semantic_content(value)
    if not isinstance(content, dict):
        return findings
    findings.extend(_semantic_exact_content(
        content, contract,
        required=("schema", "nodes", "edges", "derived_from_revision"),
    ))
    if content.get("schema") != "sdm-traceability/v1":
        findings.append(_semantic_problem(
            contract, "traceability_schema_invalid",
            "T01 must declare sdm-traceability/v1",
            "/content/schema",
        ))
    revision = content.get("derived_from_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        findings.append(_semantic_problem(
            contract, "traceability_revision_invalid",
            "T01 must bind a non-negative replay revision",
            "/content/derived_from_revision",
        ))
    if isinstance(value, dict) and isinstance(revision, int) and not isinstance(revision, bool):
        expected_instance = f"revision-{revision}"
        subjects = value.get("subjects")
        if (
            value.get("instance_key") != expected_instance
            or not isinstance(subjects, dict)
            or not _meaningful_text(subjects.get("workspace_id"))
        ):
            findings.append(_semantic_problem(
                contract, "traceability_export_binding_invalid",
                "T01 export identity must be revision-N and bind one workspace_id",
                "/instance_key",
            ))
    rows = content.get("nodes")
    edges = content.get("edges")
    graph_nodes: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        findings.append(_semantic_problem(
            contract, "traceability_nodes_invalid",
            "T01 nodes must be a canonical array",
            "/content/nodes",
        ))
    else:
        identities: list[str] = []
        for index, row in enumerate(rows):
            pointer = f"/content/nodes/{index}"
            if not isinstance(row, dict):
                findings.append(_semantic_problem(
                    contract, "traceability_node_invalid",
                    "T01 node rows must be objects", pointer,
                ))
                continue
            node_id = row.get("id")
            node_type = row.get("type")
            event_hash = row.get("event_hash")
            prefix, separator, instance = node_id.partition(":") \
                if isinstance(node_id, str) else ("", "", "")
            if (
                not isinstance(node_id, str) or not node_id
                or node_type not in {*_ALL_ARTIFACT_IDS, "Run", "Attempt"}
                or separator != ":" or prefix != node_type
                or not instance or _INSTANCE_KEY.fullmatch(instance) is None
                or not isinstance(event_hash, str)
                or _DIGEST.fullmatch(event_hash) is None
            ):
                findings.append(_semantic_problem(
                    contract, "traceability_node_invalid",
                    "T01 nodes require type-consistent identities and authenticated event hashes",
                    pointer,
                ))
                continue
            identities.append(node_id)
            graph_nodes[node_id] = {
                key: item for key, item in row.items() if key != "id"
            }
        if identities != sorted(identities) or len(identities) != len(set(identities)):
            findings.append(_semantic_problem(
                contract, "traceability_node_order_invalid",
                "T01 nodes must be unique and sorted by identity",
                "/content/nodes",
            ))
    if not isinstance(edges, list):
        findings.append(_semantic_problem(
            contract, "traceability_edges_invalid",
            "T01 edges must be a canonical array",
            "/content/edges",
        ))
    else:
        sortable = [
            (row.get("from"), row.get("to"), row.get("relation"))
            for row in edges if isinstance(row, dict)
            and all(isinstance(row.get(key), str) for key in (
                "from", "to", "relation",
            ))
        ]
        if len(sortable) != len(edges) or sortable != sorted(sortable):
            findings.append(_semantic_problem(
                contract, "traceability_edge_order_invalid",
                "T01 edges must be exact objects sorted by from/to/relation",
                "/content/edges",
            ))
    if isinstance(rows, list) and isinstance(edges, list):
        # Lazy import avoids the contracts -> projection module cycle while
        # keeping one normative typed-graph validator for replay and ingress.
        from projection import validate_graph

        findings.extend(validate_graph({"nodes": graph_nodes, "edges": edges}))
    return findings


def _exact_reference_ids(
    references: Any,
    *,
    field: str,
    subject: str,
) -> tuple[set[str], list[dict[str, Any]]]:
    """Parse v1 artifact references as exact IDs or ID:instance tokens."""

    if not isinstance(references, list):
        return set(), [finding(
            "reference_shape_invalid",
            f"{field} must be an array of exact artifact references",
            subject=subject,
            pointer=f"/content/{_ptr(field)}",
            governing_asset="artifact-contracts.json",
        )]
    resolved: set[str] = set()
    seen_references: set[str] = set()
    reference_by_artifact: dict[str, str] = {}
    findings: list[dict[str, Any]] = []
    for index, reference in enumerate(references):
        pointer = f"/content/{_ptr(field)}/{index}"
        if not isinstance(reference, str) or not reference:
            findings.append(finding(
                "reference_shape_invalid",
                "artifact reference entries must be non-empty strings",
                subject=subject,
                pointer=pointer,
                governing_asset="artifact-contracts.json",
            ))
            continue
        if reference in seen_references:
            findings.append(finding(
                "reference_duplicate",
                f"artifact reference token is duplicated: {reference}",
                subject=subject,
                pointer=pointer,
                governing_asset="artifact-contracts.json",
            ))
            continue
        seen_references.add(reference)
        artifact_id, separator, instance_key = reference.partition(":")
        if (
            artifact_id not in _ALL_ARTIFACT_IDS
            or (separator and (
                not instance_key or not _ID.fullmatch(instance_key)
            ))
        ):
            findings.append(finding(
                "reference_id_invalid",
                "artifact reference must be an exact v1 artifact ID or "
                "ID:portable-instance token",
                subject=subject,
                pointer=pointer,
                governing_asset="artifact-contracts.json",
            ))
            continue
        if artifact_id in reference_by_artifact:
            findings.append(finding(
                "reference_artifact_duplicate",
                "reference closure must select at most one token for each "
                f"artifact ID: {artifact_id}",
                subject=subject,
                pointer=pointer,
                governing_asset="artifact-contracts.json",
            ))
            continue
        reference_by_artifact[artifact_id] = reference
        resolved.add(artifact_id)
    return resolved, findings


def _execute_semantic_validator(
    value: Any,
    contract: dict[str, Any],
    validator: dict[str, Any],
    bundle: AssetBundle,
) -> list[dict[str, Any]]:
    """Execute both the declarative row and its strictly registered executor."""
    findings: list[dict[str, Any]] = []
    content = value.get("content", value) if isinstance(value, dict) else value
    if "schema" in validator:
        schema_name = validator["schema"]
        if schema_name not in bundle.documents:
            findings.append(finding("semantic_schema_unresolved", f"semantic validator schema is unresolved: {schema_name}", subject=contract["id"], governing_asset="artifact-contracts.json"))
        else:
            findings.extend(validate_schema(value, bundle.document(schema_name), subject=contract["id"], governing_asset=schema_name, registry=bundle.documents))
    if isinstance(validator.get("required"), list):
        allow_empty = set(validator.get("allow_empty", []))
        if not isinstance(content, dict):
            findings.append(finding("semantic_type", "semantic validator requires object content", subject=contract["id"], governing_asset="artifact-contracts.json"))
        else:
            for key in validator["required"]:
                if key not in content or (
                    key not in allow_empty and content[key] in (None, "", [], {})
                ):
                    findings.append(finding("semantic_required", f"semantic field is required: {key}", subject=contract["id"], pointer=f"/content/{_ptr(key)}", governing_asset="artifact-contracts.json"))
    if "required_ids" in validator:
        if isinstance(content, dict) and "references" in content:
            reference_field = "references"
            references = content[reference_field]
        elif isinstance(content, dict) and "artifact_refs" in content:
            reference_field = "artifact_refs"
            references = content[reference_field]
        else:
            reference_field = "references"
            references = []
        reference_ids, reference_findings = _exact_reference_ids(
            references,
            field=reference_field,
            subject=contract["id"],
        )
        findings.extend(reference_findings)
        for artifact_id in validator["required_ids"]:
            if artifact_id not in reference_ids:
                findings.append(finding("reference_closure", f"required artifact reference is absent: {artifact_id}", subject=contract["id"], governing_asset="artifact-contracts.json"))
    if isinstance(validator.get("required_edges"), list):
        edges = content.get("edges", []) if isinstance(content, dict) else []
        relations = {row.get("relation") for row in edges if isinstance(row, dict)}
        for relation in validator["required_edges"]:
            if relation not in relations:
                findings.append(finding("traceability_edge_missing", f"required T01 edge type is absent: {relation}", subject=contract["id"], governing_asset="artifact-contracts.json"))
    executor_name = validator.get("executor")
    executor = SEMANTIC_EXECUTORS.get(executor_name) if isinstance(executor_name, str) else None
    if executor is None:
        raise ContractError(
            "unknown_semantic_executor",
            f"semantic validator declares an unknown or unimplemented executor: {executor_name}",
            subject=str(validator.get("id", "")),
        )
    findings.extend(executor(value, contract, bundle))
    return findings


_DECLARATIVE_SEMANTIC_EXECUTOR_NAMES = (
    "contracts.validate_actual_run",
    "contracts.validate_amendment",
    "contracts.validate_artifact_index",
    "contracts.validate_augmentation_contract",
    "contracts.validate_baseline_deprecation",
    "contracts.validate_baseline_lock",
    "contracts.validate_baseline_reproduction",
    "contracts.validate_baseline_spec",
    "contracts.validate_bmad_links",
    "contracts.validate_calibration_contract",
    "contracts.validate_cancellation",
    "contracts.validate_closure",
    "contracts.validate_code_review",
    "contracts.validate_context_pack",
    "contracts.validate_data_contract",
    "contracts.validate_deployment_contract",
    "contracts.validate_deviation",
    "contracts.validate_engineering_stories",
    "contracts.validate_evaluation_contract",
    "contracts.validate_evidence_index",
    "contracts.validate_exception",
    "contracts.validate_experiment_spec",
    "contracts.validate_failure_index",
    "contracts.validate_geometry_contract",
    "contracts.validate_implementation_acceptance",
    "contracts.validate_independent_review",
    "contracts.validate_label_ontology",
    "contracts.validate_pipeline_spec",
    "contracts.validate_planning_readiness",
    "contracts.validate_platform_manifest",
    "contracts.validate_preflight",
    "contracts.validate_product_requirement",
    "contracts.validate_protocol_readiness",
    "contracts.validate_reference_closure",
    "contracts.validate_required_sections",
    "contracts.validate_research_brief",
    "contracts.validate_resolved_configuration",
    "contracts.validate_robustness_contract",
    "contracts.validate_runtime_environment",
    "contracts.validate_run_manifest",
    "contracts.validate_run_plan",
    "contracts.validate_scientific_decision",
    "contracts.validate_search_spec",
    "contracts.validate_split_manifest",
    "contracts.validate_statistical_analysis",
    "contracts.validate_synchronization_contract",
    "contracts.validate_tensor_contract",
    "contracts.validate_traceability_contract",
    "contracts.validate_traceability_graph",
)

SEMANTIC_EXECUTORS: dict[
    str, Callable[[Any, dict[str, Any], AssetBundle], list[dict[str, Any]]]
] = {
    name: _semantic_generic for name in _DECLARATIVE_SEMANTIC_EXECUTOR_NAMES
}
SEMANTIC_EXECUTORS.update({
    "contracts.validate_governance_principles": _semantic_governance_principles,
    "contracts.validate_reference_closure": _semantic_contract_bundle,
    "contracts.validate_data_contract": _semantic_data_contract,
    "contracts.validate_calibration_contract": _semantic_calibration_contract,
    "contracts.validate_synchronization_contract": _semantic_synchronization_contract,
    "contracts.validate_label_ontology": _semantic_label_ontology,
    "contracts.validate_tensor_contract": _semantic_tensor_contract,
    "contracts.validate_evaluation_contract": _semantic_evaluation_contract,
    "contracts.validate_robustness_contract": _semantic_robustness_contract,
    "contracts.validate_deployment_contract": _semantic_deployment_contract,
    "contracts.validate_traceability_contract": _semantic_traceability_contract,
    "contracts.validate_baseline_spec": _semantic_baseline_spec,
    "contracts.validate_baseline_lock": _semantic_baseline_lock,
    "contracts.validate_baseline_reproduction": _semantic_baseline_reproduction,
    "contracts.validate_baseline_deprecation": _semantic_baseline_deprecation,
    "contracts.validate_experiment_spec": _semantic_experiment_strict,
    "contracts.validate_protocol_readiness": _semantic_protocol_readiness,
    "contracts.validate_search_spec": _semantic_search_spec,
    "contracts.validate_run_plan": _semantic_run_plan,
    "contracts.validate_amendment": _semantic_amendment,
    "contracts.validate_deviation": _semantic_deviation,
    "contracts.validate_run_manifest": _semantic_run_manifest_strict,
    "contracts.validate_resolved_configuration": _semantic_resolved_configuration_strict,
    "contracts.validate_runtime_environment": _semantic_runtime_environment_strict,
    "contracts.validate_platform_manifest": _semantic_platform_manifest_strict,
    "contracts.validate_dataset_manifest": _semantic_dataset_manifest,
    "contracts.validate_split_manifest": _semantic_split_manifest_strict,
    "contracts.validate_calibration_manifest": _semantic_calibration_manifest,
    "contracts.validate_actual_run": _semantic_actual_run_strict,
    "contracts.validate_artifact_index": _semantic_artifact_index_strict,
    "contracts.validate_pipeline_spec": _semantic_source_control_authorship,
    "contracts.validate_code_review": _semantic_source_control_authorship,
    "contracts.validate_implementation_acceptance": _semantic_source_control_authorship,
    "contracts.validate_preflight": _semantic_preflight_strict,
    "contracts.validate_evidence_index": _semantic_evidence_index_strict,
    "contracts.validate_required_sections": _semantic_evidence_section_sources,
    "contracts.validate_evidence_summary": _semantic_evidence_summary,
    "contracts.validate_statistical_analysis": _semantic_evidence_section_sources,
    "contracts.validate_failure_index": _semantic_evidence_section_sources,
    "contracts.validate_geometry_contract": _semantic_geometry_contract,
    "contracts.validate_augmentation_contract": _semantic_augmentation_contract,
    "contracts.validate_traceability_graph": _semantic_traceability_graph,
    "contracts.validate_product_requirement": _semantic_product_requirement,
    "contracts.validate_research_brief": _semantic_research_brief,
    "contracts.validate_bmad_links": _semantic_bmad_links,
    "contracts.validate_planning_readiness": _semantic_planning_readiness,
    "contracts.validate_engineering_stories": _semantic_engineering_stories,
    "contracts.validate_context_pack": _semantic_context_pack,
    "contracts.validate_independent_review": _semantic_independent_review,
    "contracts.validate_exception": _semantic_exception,
    "contracts.validate_cancellation": _semantic_cancellation,
    "contracts.validate_closure": _semantic_closure,
    "contracts.validate_scientific_decision": _semantic_scientific,
    "handoffs.validate_request": _semantic_handoff,
    "handoffs.validate_result": _semantic_handoff,
    "authority.validate_attestation": _semantic_authority_attestation,
})


def normalize_gate(node: Any, bundle: AssetBundle | None = None) -> dict[str, Any]:
    """Compile concise transition-asset gate notation into the gate DSL."""
    if isinstance(node, str):
        if bundle is not None:
            try:
                bundle.operation_contract(node)
                return {"operation": node}
            except ContractError:
                pass
            try:
                bundle.artifact_contract(node)
                return {"artifact": node}
            except ContractError:
                pass
        return {"check": node}
    if not isinstance(node, dict):
        raise ContractError("invalid_gate_tree", "gate node must be a string or object")
    operators = [key for key in ("all_of", "sequence", "any_of", "when", "check", "platform_result", "operation", "artifact", "state", "substate_event", "authority") if key in node]
    if len(operators) != 1:
        raise ContractError("invalid_gate_tree", "gate object must contain exactly one executable operator")
    kind = operators[0]
    value = node[kind]
    if kind in {"all_of", "sequence", "any_of"}:
        return {kind: [normalize_gate(item, bundle) for item in value]}
    if kind == "when":
        result = dict(value)
        result["then"] = normalize_gate(value["then"], bundle)
        if "else" in value:
            result["else"] = normalize_gate(value["else"], bundle)
        return {kind: result}
    return {kind: value}


def validate_gate_shape(node: Any) -> None:
    if not isinstance(node, dict) or len(node) != 1:
        raise ContractError("invalid_gate_tree", "each gate node must be a one-key object")
    kind, value = next(iter(node.items()))
    if kind in {"all_of", "sequence", "any_of"}:
        if not isinstance(value, list) or not value:
            raise ContractError("invalid_gate_tree", f"{kind} requires a non-empty array")
        for child in value:
            validate_gate_shape(child)
    elif kind == "when":
        if not isinstance(value, dict) or "condition" not in value or "then" not in value:
            raise ContractError("invalid_gate_tree", "when requires condition and then")
        validate_gate_shape(value["then"])
        if "else" in value:
            validate_gate_shape(value["else"])
    elif kind in {"check", "platform_result", "operation", "artifact", "state", "substate_event", "authority"}:
        if not isinstance(value, (str, dict)):
            raise ContractError("invalid_gate_tree", f"{kind} requires a string or object")
    else:
        raise ContractError("invalid_gate_tree", f"unknown gate operator: {kind}")


def evaluate_gate(node: dict[str, Any], context: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    """Return (passed, missing checks, emitted substate events)."""
    validate_gate_shape(node)
    kind, value = next(iter(node.items()))
    if kind in {"all_of", "sequence"}:
        missing: list[str] = []
        events: list[str] = []
        for child in value:
            passed, child_missing, child_events = evaluate_gate(child, context)
            missing.extend(child_missing)
            events.extend(child_events)
            if kind == "sequence" and not passed:
                return False, missing, events
        return not missing, missing, events
    if kind == "any_of":
        results = [evaluate_gate(child, context) for child in value]
        for passed, missing, events in results:
            if passed:
                return True, [], events
        return False, [item for _, missing, _ in results for item in missing], []
    if kind == "when":
        condition = value["condition"]
        active = _lookup(context, condition) if isinstance(condition, str) else bool(condition)
        branch = value["then"] if active else value.get("else")
        return (True, [], []) if branch is None else evaluate_gate(branch, context)
    if kind == "substate_event":
        name = value if isinstance(value, str) else value["name"]
        return True, [], [name]
    if kind == "check":
        name = value if isinstance(value, str) else value["name"]
        # A derived check is affirmative evidence, but a stale/false cached
        # value must not hide a more recent structured fact supplied by the
        # current gate context.
        passed = bool(context.get("checks", {}).get(name)) or bool(_lookup(context, name))
        return passed, [] if passed else [name], []
    if kind == "artifact":
        name = value if isinstance(value, str) else value["id"]
        instances = context.get("artifacts", {}).get(name)
        passed = (
            isinstance(instances, dict)
            and bool(instances)
            and all(
                isinstance(instance_key, str)
                and isinstance(record, dict)
                and isinstance(record.get("path"), str)
                and isinstance(record.get("digest"), str)
                and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", record["digest"]))
                and record.get("status") not in {"invalid", "superseded"}
                for instance_key, record in instances.items()
            )
        )
        return passed, [] if passed else [f"artifact:{name}"], []
    if kind in {"platform_result", "operation"}:
        name = value if isinstance(value, str) else value["operation"]
        passed = context.get("platform_results", {}).get(name) == "successful"
        return passed, [] if passed else [f"platform_result:{name}"], []
    if kind == "authority":
        name = value if isinstance(value, str) else value["action"]
        passed = bool(context.get("authorities", {}).get(name))
        return passed, [] if passed else [f"authority:{name}"], []
    if kind == "state":
        if isinstance(value, str):
            passed = bool(_lookup(context, value))
            return passed, [] if passed else [f"state:{value}"], []
        actual = _lookup(context, value["path"])
        passed = actual == value.get("equals")
        return passed, [] if passed else [f"state:{value['path']}"], []
    raise ContractError("invalid_gate_tree", f"unhandled gate operator: {kind}")


def _lookup(context: dict[str, Any], dotted: str) -> Any:
    current: Any = context
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _diagnostic_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect the SDM executable-contract library")
    parser.parse_args(argv)
    sys.stdout.write(json.dumps({"status": "pass", "command": "contracts.help"}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(_diagnostic_main())
