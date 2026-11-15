#!/usr/bin/env python3
"""Read-only S12 routing aid. This does not dispatch or produce S13."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from canonical import digest


ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = ROOT / "assets" / "platform-operations.json"

# These are candidate repo-local Skills. They are not production bindings until
# their real producers, trust material and complete S13 evidence are verified.
ROUTES = {
    "data-platform": ("data_platform_skill", "lpai-s12-platform", "lpai"),
    "source-control-platform": (
        "source_control_platform_skill", "gitlab-s12-platform", "gitlab"
    ),
    "ci-platform": ("ci_platform_skill", "jenkins-s12-platform", "jenkins"),
    "ml-execution-platform": (
        "ml_execution_platform_skill", "lpai-s12-platform", "lpai"
    ),
}

# Hints are discovery aids, not executable argv or evidence of capability.
TOOL_HINTS = {
    "data.labeling-status": ("lpai-agent assets datasets version; labeling evidence source still required", "read"),
    "data.quality-check": ("lpai-agent assets datasets version; quality/split evidence source still required", "read"),
    "git.snapshot": ("git rev-parse / git cat-file at an exact commit", "read"),
    "git.branch-change-request": ("git push to authorized destination branch; glab mr create", "write"),
    "git.change-review-status": ("glab mr view / approvals for exact MR revision", "read"),
    "git.merge-status": ("glab mr view for exact target and merged revision", "read"),
    "git.tag-frozen-spec": ("git tag and push an authorized immutable tag", "write"),
    "git.anchor-ledger-head": ("GitLab immutable external anchor implementation required", "write"),
    "git.read-current-ledger-anchor": ("read exact trusted GitLab anchor", "read"),
    "jenkins.baseline-reproduction": ("Jenkins job and build receipt not configured", "unavailable"),
    "jenkins.contract-preflight": ("Jenkins job and build receipt not configured", "unavailable"),
    "jenkins.freeze-receipt": ("Jenkins job and build receipt not configured", "unavailable"),
    "jenkins.implementation-checks": ("Jenkins job and build receipt not configured", "unavailable"),
    "jenkins.deterministic-acceptance": ("Jenkins job and build receipt not configured", "unavailable"),
    "jenkins.post-implementation-preflight": ("Jenkins job and build receipt not configured", "unavailable"),
    "jenkins.execution-orchestration": ("Jenkins job and build receipt not configured", "unavailable"),
    "jenkins.evaluation-orchestration": ("Jenkins job and build receipt not configured", "unavailable"),
    "ml.hpo-search": ("LPAI projects/pipelines/tasks; complete HPO trial evidence still required", "write"),
    "ml.training": ("lpai-agent projects start and tasks get, or authorized devspace execution", "write"),
    "ml.locked-evaluation": ("LPAI task and pinned evaluator artifacts required", "write"),
    "ml.baseline-locked-evaluation": ("LPAI baseline evaluation before experiment training; actual checkpoint, data, runtime and metrics required", "write"),
    "ml.robustness-evaluation": ("LPAI task and pinned robustness artifacts required", "write"),
}

# Provider-neutral execution gates use the configured ML platform Skill.
# The hint deliberately reports missing producer facts rather than success.
for _operation in (
    'baseline-reproduction', 'contract-preflight', 'freeze-receipt',
    'implementation-checks', 'deterministic-acceptance',
    'post-implementation-preflight', 'execution-orchestration',
    'evaluation-orchestration',
):
    TOOL_HINTS['ml.' + _operation] = (
        'Authorized LPAI development-space execution; exact source/config/data and actual result receipt required',
        'write',
    )


def catalog(document: dict) -> dict[str, dict]:
    profiles = document["evidence_schema_profiles"]
    result = {}
    for domain in document["domains"]:
        domain_name = domain["domain"]
        binding_key, candidate_skill, provider = ROUTES[domain_name]
        if domain["binding_key"] != binding_key:
            raise ValueError(f"binding key mismatch for {domain_name}")
        for operation in domain["operations"]:
            name = operation["name"]
            if name in result:
                raise ValueError(f"duplicate platform operation: {name}")
            if name not in TOOL_HINTS:
                raise ValueError(f"missing tool route for {name}")
            by_name = {
                evidence: profile
                for profile, names in operation["evidence_value_profiles"].items()
                for evidence in names
            }
            expanded = dict(operation)
            expanded["evidence_value_schemas"] = {
                evidence: profiles[by_name[evidence]]
                for evidence in operation["required_evidence"]
            }
            result[name] = {
                "domain": domain_name,
                "binding_key": binding_key,
                "candidate_skill": candidate_skill,
                "provider": provider,
                "adapter_contract_digest": digest(expanded),
                "expected_evidence": operation["required_evidence"],
                "candidate_tool": TOOL_HINTS[name][0],
                "effect": TOOL_HINTS[name][1],
            }
    if set(result) != set(TOOL_HINTS):
        raise ValueError("tool route catalog differs from the SDM operation allowlist")
    return result


def plan(request: dict, operations: dict[str, dict]) -> dict:
    operation = request.get("operation")
    if operation not in operations:
        raise ValueError("S12 operation is not in the SDM platform allowlist")
    route = operations[operation]
    for name in (
        "domain", "binding_key", "binding_identity",
        "adapter_contract_digest", "expected_evidence",
    ):
        expected = route["candidate_skill"] if name == "binding_identity" else route.get(name)
        if request.get(name) != expected:
            raise ValueError(f"S12 {name} does not match the pinned operation route")
    if request.get("schema") != "sdm-handoff-request/v1":
        raise ValueError("unsupported S12 schema")
    if request.get("expected_result_schema") != "schemas/handoff-result.schema.json":
        raise ValueError("unexpected S13 schema")
    if not isinstance(request.get("request_id"), str) or not request["request_id"].startswith("REQ-"):
        raise ValueError("S12 request identity is missing")
    if not isinstance(request.get("idempotency_key"), str) or not request["idempotency_key"].startswith("sha256:"):
        raise ValueError("S12 idempotency key is missing")
    return {
        "status": "integration_pending",
        "request_id": request["request_id"],
        "operation": operation,
        "provider": route["provider"],
        "candidate_skill": route["candidate_skill"],
        "binding_key": route["binding_key"],
        "candidate_tool": route["candidate_tool"],
        "effect": route["effect"],
        "required_evidence": route["expected_evidence"],
        "readiness_scope": "route-only; workspace trust and producer implementation not inspected",
        "missing": [
            "verified platform effect or read result with exact evidence",
            "producer authorized by the pinned workspace trust (company or approved personal scope)",
            "authenticated S13 and replay-safe idempotency/reconciliation",
        ] if route["provider"] != "jenkins" else [
            "Jenkins instance, jobs and access configuration",
            "verified platform result with exact evidence",
            "producer authorized by the pinned workspace trust (company or approved personal scope)",
            "authenticated S13 and replay-safe idempotency/reconciliation",
        ],
        "s13_emitted": False,
        "platform_called": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--catalog", action="store_true")
    group.add_argument("--request", type=Path)
    args = parser.parse_args()
    try:
        operations = catalog(json.loads(OPERATIONS.read_text(encoding="utf-8")))
        if args.catalog:
            output = {
                "status": "pass",
                "operation_count": len(operations),
                "operations": {
                    name: {
                        "provider": item["provider"],
                        "candidate_skill": item["candidate_skill"],
                        "binding_key": item["binding_key"],
                        "candidate_tool": item["candidate_tool"],
                        "effect": item["effect"],
                    }
                    for name, item in sorted(operations.items())
                },
            }
        else:
            output = plan(json.loads(args.request.read_text(encoding="utf-8")), operations)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False))
        return 3
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
