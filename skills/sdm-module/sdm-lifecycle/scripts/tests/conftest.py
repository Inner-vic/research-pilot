"""Shared, isolated fixtures for the executable lifecycle contract tests."""

from __future__ import annotations

from copy import deepcopy
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1]
ASSET_DIR = SCRIPT_DIR.parent / "assets"
MODULE_TESTS = SCRIPT_DIR.parents[1] / "tests"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(MODULE_TESTS) not in sys.path:
    sys.path.insert(0, str(MODULE_TESTS))

from canonical import digest, digest_bytes
from contracts import AssetBundle
# Load production modules from this installation before external test helpers
# adjust sys.path to locate their checked-in CPU-only signing fixtures.
import authority
import ledger
import projection
import workspace as workspace_module
import handoffs
from evidence_examples import evidence_for_request
import producer_signing as producer_examples
from producer_signing import (
    TEST_PRODUCER_PRINCIPAL_ID, producer_trust_fields,
    seal_platform_result, verifier_record,
)
from workspace import create_workspace

if not MODULE_TESTS.is_dir():
    MODULE_TESTS=Path(producer_examples.__file__).resolve().parent
if str(SCRIPT_DIR) in sys.path:sys.path.remove(str(SCRIPT_DIR))
sys.path.insert(0,str(SCRIPT_DIR))
for loaded in (authority,ledger,projection,workspace_module,handoffs):
    if not Path(loaded.__file__).resolve().is_relative_to(SCRIPT_DIR):
        raise RuntimeError('CPU validation imported a different lifecycle installation: '+loaded.__name__)


ALL_BINDINGS = {
    "data_platform_skill": "fake-data-platform",
    "source_control_platform_skill": "fake-source-control-platform",
    "ci_platform_skill": "fake-ci-platform",
    "ml_execution_platform_skill": "fake-ml-execution-platform",
}

FAKE_PLATFORM_PRODUCER_PRINCIPALS = (
    "fake-data-platform-producer",
    "fake-source-control-platform-producer",
    "fake-ci-platform-producer",
    "fake-ml-execution-platform-producer",
)

def _fake_platform_producer_trust_fields() -> dict[str, Any]:
    root = (MODULE_TESTS / "fixtures" / "platform-skills").resolve(
        strict=True,
    )
    keys = []
    for principal_id in FAKE_PLATFORM_PRODUCER_PRINCIPALS:
        identity = principal_id.removesuffix("-producer")
        public_key = (
            root / identity / "TEST_ONLY_PUBLIC_KEY.pem"
        ).read_text(encoding="ascii")
        keys.append({
            "principal_id": principal_id,
            "key_digest": digest_bytes(public_key.encode("ascii")),
            "public_key_pem": public_key,
            "roles": ["platform-producer"],
            "valid_from": "2020-01-01T00:00:00Z",
            "valid_until": "2035-01-01T00:00:00Z",
        })
    snapshot = {
        "schema": "sdm-trust-snapshot/v1",
        "verifier": verifier_record(),
        "keys": keys,
        "revocations": [],
    }
    info = root.stat()
    return {
        "authority_trust_root": str(root),
        "authority_trust_root_identity": digest({
            "resolved_path": str(root),
            "device": info.st_dev,
            "inode": info.st_ino,
        }),
        "authority_trust_snapshot": snapshot,
        "authority_trust_snapshot_digest": digest(snapshot),
    }


@pytest.fixture(scope="session")
def asset_bundle() -> AssetBundle:
    return AssetBundle.load(ASSET_DIR)


def _create_template(
    path: Path,
    bindings: dict[str, str],
    *,
    trust_fields: dict[str, Any] | None = None,
) -> Path:
    create_workspace(
        path,
        {
            "workspace_id": path.name,
            "intent": "exercise deterministic lifecycle contracts",
            "constraints": ["offline", "append-only"],
            "non_goals": ["perform production operations"],
            "protocol": "exploratory",
            "bindings": bindings,
            "binding_skill_digests": {
                key: digest({"skill": identity})
                for key, identity in bindings.items()
            },
            **(
                producer_trust_fields()
                if trust_fields is None else trust_fields
            ),
        },
        expected_revision=0,
        operation_id="create-workspace",
        assets_root=ASSET_DIR,
    )
    return path


@pytest.fixture(scope="session")
def workspace_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _create_template(tmp_path_factory.mktemp("sdm-template") / "bound-workspace", ALL_BINDINGS)


@pytest.fixture(scope="session")
def unbound_workspace_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _create_template(tmp_path_factory.mktemp("sdm-template-unbound") / "unbound-workspace", {})


@pytest.fixture(scope="session")
def fake_owner_workspace_template(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    return _create_template(
        tmp_path_factory.mktemp("sdm-template-fake-owner") /
        "fake-owner-workspace",
        ALL_BINDINGS,
        trust_fields=_fake_platform_producer_trust_fields(),
    )


@pytest.fixture
def workspace(tmp_path: Path, workspace_template: Path) -> Path:
    target = tmp_path / "bound-workspace-copy"
    shutil.copytree(workspace_template, target, copy_function=shutil.copy2)
    return target


@pytest.fixture
def unbound_workspace(tmp_path: Path, unbound_workspace_template: Path) -> Path:
    target = tmp_path / "unbound-workspace-copy"
    shutil.copytree(unbound_workspace_template, target, copy_function=shutil.copy2)
    return target


@pytest.fixture
def fake_owner_workspace(
    tmp_path: Path,
    fake_owner_workspace_template: Path,
) -> Path:
    target = tmp_path / "fake-owner-workspace-copy"
    shutil.copytree(
        fake_owner_workspace_template,
        target,
        copy_function=shutil.copy2,
    )
    return target


@pytest.fixture
def request_payload(asset_bundle: AssetBundle) -> Callable[..., dict[str, Any]]:
    def make(
        request_id: str = "REQ-request01",
        *,
        operation: str = "data.quality-check",
        binding_identity: str | None = None,
        subjects: dict[str, str] | None = None,
        expected_evidence: list[str] | None = None,
        step: int | None = None,
    ) -> dict[str, Any]:
        _, operation_contract, binding_key = asset_bundle.operation_contract(operation)
        identity = binding_identity or ALL_BINDINGS[binding_key]
        if step is None:
            declared_steps = operation_contract["subject_requirements"].get(
                "by_step", {},
            )
            step = min(int(value) for value in declared_steps) \
                if declared_steps else 1
        return {
            "request_id": request_id,
            "operation": operation,
            "binding_identity": identity,
            "binding_skill_digest": digest({"skill": identity}),
            "adapter_contract_digest": digest(operation_contract),
            "gate": "platform-result-required",
            "step": step,
            "subjects": subjects if subjects is not None else {"artifact_id": "dataset-v1"},
            "expected_evidence": expected_evidence
            if expected_evidence is not None
            else list(operation_contract["required_evidence"]),
            "evidence_cardinality": deepcopy(
                operation_contract["evidence_cardinality"],
            ),
            "portable_paths": ["inputs/dataset.json"],
        }

    return make


@pytest.fixture
def result_packet(asset_bundle: AssetBundle) -> Callable[..., dict[str, Any]]:
    def make(
        request: dict[str, Any],
        *,
        terminal_status: str = "successful",
        result_id: str = "RES-result01",
        evidence: list[dict[str, Any]] | None = None,
        dispatch_correlation: str = "dispatch-001",
    ) -> dict[str, Any]:
        if evidence is None:
            evidence = evidence_for_request(asset_bundle, request)
        result = {
            "schema": "sdm-handoff-result/v1",
            "result_id": result_id,
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
            "dispatch_correlation": dispatch_correlation,
            "producer_identity": request["binding_identity"],
            "producer_principal_id": TEST_PRODUCER_PRINCIPAL_ID,
            "terminal_status": terminal_status,
            "evidence": evidence,
            "completed_at": "2026-01-02T03:04:05Z",
        }
        return seal_platform_result(request, result)

    return make


@pytest.fixture
def valid_artifact(asset_bundle: AssetBundle) -> Callable[..., dict[str, Any]]:
    validators = {
        row["id"]: row
        for row in asset_bundle.document("artifact-contracts.json")["semantic_validators"]
    }

    def make(artifact_id: str, instance_key: str = "default", **overrides: Any) -> dict[str, Any]:
        contract = asset_bundle.artifact_contract(artifact_id)
        validator = validators[contract["semantic_validator"]]
        content: dict[str, Any] = {
            key: (
                digest({"fixture_field": key})
                if key == "digest" or key.endswith("_digest")
                else f"artifact://fixture/{key}"
                if key == "uri" or key.endswith("_uri")
                else f"value-{key}"
            )
            for key in validator.get("required", [])
        }
        if validator.get("required_ids"):
            content["references"] = list(validator["required_ids"])
        if validator.get("required_edges"):
            content["edges"] = [
                {"from": f"node-{index}", "to": f"node-{index + 1}", "relation": relation}
                for index, relation in enumerate(validator["required_edges"])
            ]
        if artifact_id == "G01":
            content = {
                "principles": [
                    "governed model changes require immutable evidence",
                ],
                "separation_of_duties": {"required": True},
                "freeze_policy": {"immutable": True},
                "exception_policy": {"authority": "required"},
                "source_refs": {
                    "S01:governance-requirement": digest({
                        "requirement": "governance",
                    }),
                },
            }
        if artifact_id == "D05":
            frozen_rules = {
                "schema": "sdm-scientific-rule/v1",
                "support_predicate": "fixture_supports_hypothesis",
                "rejection_predicate": "fixture_rejects_hypothesis",
                "inconclusive_rule": "underpowered_or_no_decisive_result",
                "invalid_rule": "protocol_invalid",
            }
            content["decision_context"] = {
                "frozen_rules": frozen_rules,
                "planned_runs": [{
                    "run_id": "fixture-run",
                    "required_attempt_ids": ["fixture-attempt"],
                }],
                "attempts": [{
                    "attempt_id": "fixture-attempt", "run_id": "fixture-run",
                    "terminal_status": "successful", "accounted": True,
                    "validity": "valid", "system_failure": False,
                }],
                "evidence": {
                    "protocol_invalid": False, "underpowered": False,
                    "predicates": {
                        "fixture_supports_hypothesis": True,
                        "fixture_rejects_hypothesis": False,
                    },
                },
            }
            content["rule_digest"] = digest(frozen_rules)
            content["outcome"] = "hypothesis_supported"
            content["reviewers"] = ["fixture-reviewer"]
            content["authority"] = [digest({"reviewer": "fixture-reviewer"})]
        envelope: dict[str, Any] = {
            "schema": "sdm-artifact/v1",
            "artifact_type": artifact_id,
            "artifact_id": f"{artifact_id.lower()}-artifact",
            "instance_key": instance_key,
            "version": 1,
            "status": "published",
            "subjects": {"workspace_id": "workspace"},
            "content": content,
            "content_digest": digest(content),
            "uri": f"artifact://{artifact_id}/{instance_key}",
            "created_at": "2026-01-02T03:04:05Z",
            "created_by": "test-authority",
        }
        envelope.update(overrides)
        if "content" in overrides and "content_digest" not in overrides:
            envelope["content_digest"] = digest(envelope["content"])
        return envelope

    return make
