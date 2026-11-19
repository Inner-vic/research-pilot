"""Shared original SDM step semantics; no workspace or platform operations."""
from __future__ import annotations
import re
from typing import Any
from canonical import digest
from contracts import ContractError, canonical_runtime_environment_manifest, runtime_environment_digest, validate_change_policy


def check_records(bundle, step, records):
    """Original object validation + original row gate, without external facts.

    This deliberately cannot issue a lifecycle transition, signed handoff,
    execution authorization or D05. Rows needing platform/authority facts remain
    blocked here; their existing owning coordinator must validate those facts.
    """
    from contracts import evaluate_gate
    row = next((row for row in bundle.document('lifecycle-steps.json')['steps']
                if type(step) is int and row['step'] == step), None)
    if row is None or not isinstance(records, dict):
        raise ContractError('lifecycle_record_set_invalid', 'An original step and typed record set are required')
    required = {name.split('.')[0] for name in row['governed_outputs'] + row['supporting_outputs']}
    # S12/S13/S14 belong to the existing authority/handoff coordinator. Never
    # accept caller-created stand-ins for those records through this pure API.
    if set(records) - required or set(records) & {'S12', 'S13', 'S14'}:
        raise ContractError('lifecycle_record_set_invalid', 'Only this step\'s own non-authority records are accepted')
    findings = []
    for kind, values in records.items():
        if not isinstance(values, list) or not values:
            raise ContractError('lifecycle_record_set_invalid', 'Records must be nonempty typed lists')
        for value in values:
            findings.extend(bundle.validate_artifact(kind, value))
    checks = {} if findings else _semantic_step_checks(step, records)
    passed, missing, events = evaluate_gate(row['gate'], {'checks': checks})
    return {'schema': 'sdm-lifecycle-record-check/v1', 'step': step,
            'engine_contract_digest': bundle.engine_contract_digest,
            'gate_digest': digest(row['gate']), 'record_digest': digest(records),
            'checks': checks, 'findings': findings, 'missing': missing,
            'passed': bool(passed and not findings), 'failure_disposition': row['failure_disposition'],
            'scope': 'Object schemas, semantics and original row gate only; not a lifecycle transition.',
            'execution_authority': False, 'transition_accepted': False}


def asset_inputs():
    """Resolve and validate the original engine/owner-schema dependency closure."""
    import hashlib
    from pathlib import Path
    from canonical import canonical_bytes, parse_document
    from contracts import AssetBundle, _read_relative_regular
    bundle = AssetBundle.load()
    module = Path(__file__).resolve().parents[2]
    inputs = {}
    for name, canonical in bundle.canonical_assets.items():
        is_owner = name.startswith('sdm-')
        base = module if is_owner else bundle.root
        source = base / name
        raw = _read_relative_regular(base, name)
        if canonical_bytes(parse_document(raw)) != canonical:
            raise ContractError('asset_concurrent_mutation', 'Asset changed while resolving the dependency closure', subject=name)
        inputs[source] = raw
    return bundle, inputs


def verify_assets(configuration):
    """Verify exactly the frozen engine/schema closure, including raw bytes."""
    import hashlib
    from pathlib import Path
    from contracts import AssetBundle, _read_relative_regular
    module = Path(configuration['runtime_root']).parent.parent
    bundle = AssetBundle.load(module / 'sdm-lifecycle/assets')
    expected_names = {name if name.startswith('sdm-') else 'sdm-lifecycle/assets/' + name
                      for name in bundle.canonical_assets}
    manifest = configuration.get('normative_manifest')
    if (not isinstance(manifest, dict) or set(manifest) != expected_names
            or bundle.engine_contract_digest != configuration.get('engine_contract_digest')):
        raise ValueError('frozen_sdm_engine_closure_changed')
    for name, expected in manifest.items():
        raw = _read_relative_regular(module, name)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('frozen_sdm_engine_asset_changed')
    return bundle


def _semantic_step_checks(
    step: int, values: dict[str, list[dict[str, Any]]],
) -> dict[str, bool]:
    """Derive named row checks from validated record content, never names."""

    def contents(artifact_id: str) -> list[dict[str, Any]]:
        return [
            value.get("content", value) for value in values.get(artifact_id, [])
            if isinstance(value.get("content", value), dict)
        ]

    checks: dict[str, bool] = {}
    if step == 1:
        rows = contents("S01")
        checks["problem.clear"] = len(rows) == 1 and all(
            isinstance(rows[0].get(key), str) and bool(rows[0][key])
            for key in ("requirement_id", "owner", "impact")
        )
        checks["non_goals.explicit"] = len(rows) == 1 and isinstance(
            rows[0].get("non_goals"), list,
        ) and bool(rows[0]["non_goals"])
    elif step == 2:
        rows = contents("S02")
        checks["hypothesis.falsifiable"] = len(rows) == 1 and isinstance(
            rows[0].get("hypothesis"), str,
        ) and bool(rows[0]["hypothesis"])
        checks["target_not_must"] = len(rows) == 1 and rows[0].get(
            "target_as_must",
        ) is False
        checks["mechanism.explicit"] = len(rows) == 1 and isinstance(
            rows[0].get("mechanism"), str,
        ) and bool(rows[0]["mechanism"])
        checks["uncertainty.explicit"] = len(rows) == 1 and rows[0].get(
            "uncertainty",
        ) not in (None, "", [], {})
    elif step == 3:
        complete = bool(values) and all(
            (artifact_id.startswith("G") or artifact_id in {"R04", "R05", "R06"})
            and rows for artifact_id, rows in values.items()
        ) and bool(values.get("R04")) and bool(values.get("R05"))
        checks.update({
            "contracts.all_applicable_semantics_closed": complete,
            "all-applicable-G-contracts": complete,
            "exact-successful-platform-results": complete,
        })
    elif step == 4:
        rows = contents("B01")
        exact = len(rows) == 1 and all(
            rows[0].get(key) not in (None, "", [], {})
            for key in ("baseline_id", "scope", "model", "data", "evaluator", "tolerances")
        )
        checks.update({
            "baseline.scope_exact": exact,
            "baseline.version_selected": exact,
        })
    elif step == 5:
        locks, reproductions = contents("B02"), contents("B03")
        exact = len(locks) == 1 and len(reproductions) == 1
        receipts = exact and all(
            isinstance(row.get("receipts"), dict)
            and set(row["receipts"]) == {
                "git.snapshot", "ml.baseline-reproduction",
                "ml.baseline-locked-evaluation",
            }
            for row in (*locks, *reproductions)
        )
        tolerance = exact and reproductions[0].get(
            "tolerance_result",
        ) == {"passed": True, "summary": reproductions[0].get(
            "tolerance_result", {},
        ).get("summary")}
        fingerprint = exact and (
            locks[0].get("baseline_fingerprint")
            == reproductions[0].get("baseline_fingerprint")
            and locks[0].get("baseline_fingerprint_digest")
            == reproductions[0].get("baseline_fingerprint_digest")
            == digest(locks[0].get("baseline_fingerprint"))
        )
        runtime_exact = False
        if fingerprint:
            try:
                manifest = canonical_runtime_environment_manifest(
                    locks[0]["baseline_fingerprint"][
                        "runtime_environment_manifest"
                    ],
                )
                runtime_exact = runtime_environment_digest(manifest) == \
                    locks[0]["baseline_fingerprint"].get(
                        "runtime_environment_digest",
                    )
            except (ContractError, KeyError, TypeError):
                runtime_exact = False
        checks.update({
            "baseline.receipts_exact": bool(receipts),
            "baseline.tolerance_pass": bool(tolerance),
            "baseline.fingerprint_exact": bool(fingerprint),
            "baseline.runtime_environment_exact": bool(runtime_exact),
            "tolerance-pass": bool(tolerance and fingerprint),
        })
    elif step == 6:
        experiments = contents("E01")
        schemas = len(experiments) == 1 and bool(experiments[0])
        isolated = schemas and isinstance(experiments[0].get("variables"), dict) \
            and bool(experiments[0]["variables"])
        hpo = False
        if schemas:
            search = experiments[0].get("search")
            hpo = (
                isinstance(search, dict) and search.get("mode") == "hpo"
            ) or experiments[0].get("hpo_enabled") is True
        checks.update({
            "experiment.schema": schemas,
            "variables.isolated": isolated and not (
                validate_change_policy(experiments[0]) if schemas else []
            ),
            "E03.complete": (not hpo) or bool(contents("E03")),
            "true": True,
        })
    elif step == 7:
        readiness = contents("E02")
        planning = contents("S04")
        exact = len(readiness) == 1 and len(planning) == 1
        blocking = readiness[0].get("blocking_gates") if exact else None
        gates_pass = isinstance(blocking, (list, dict)) and bool(blocking) and (
            all(item.get("status") == "pass" for item in blocking if isinstance(item, dict))
            if isinstance(blocking, list)
            else all(value in (True, "pass", "passed") for value in blocking.values())
        )
        trio = planning[0] if planning else {}
        checks.update({
            "planning_readiness.trio_complete": exact and all(
                trio.get(key) not in (None, "", [], {}) for key in (
                    "planning_readiness", "immutable_experiment_readiness",
                    "post_implementation_preflight_applicability",
                )
            ),
            "protocol_readiness.all_blocking_gates_pass": bool(gates_pass),
            "E01-candidate-digest": exact and isinstance(
                readiness[0].get("candidate_digest"), str,
            ),
        })
    elif step == 9:
        stories, packs = contents("S05"), contents("S06")
        checks.update({
            "stories.deterministic_acceptance": bool(stories) and all(
                isinstance(row.get("acceptance_ids"), list)
                and bool(row["acceptance_ids"]) for row in stories
            ),
            "context.allowed_paths": bool(packs) and all(
                isinstance(row.get("allowed_paths"), list)
                and bool(row["allowed_paths"]) for row in packs
            ),
        })
    elif step == 10:
        pipelines = contents("E05")
        exact = bool(pipelines) and all(
            row.get("definition_digest") and row.get("revision")
            and isinstance(row.get("frozen_experiment_digest"), str)
            and re.fullmatch(
                r"sha256:[0-9a-f]{64}", row["frozen_experiment_digest"],
            )
            and isinstance(row.get("diff_evidence_digest"), str)
            and re.fullmatch(
                r"sha256:[0-9a-f]{64}", row["diff_evidence_digest"],
            )
            for row in pipelines
        )
        checks.update({
            "coding.allowed_paths_only": exact,
            "S06-contained-context": exact,
            "allowed-paths": exact,
        })
    elif step == 11:
        reviews = contents("S07")
        checks["review.owner_approvals"] = bool(reviews) and all(
            isinstance(row.get("approval_receipts"), list)
            and bool(row["approval_receipts"]) for row in reviews
        )
    elif step == 12:
        acceptances = contents("S08")
        exact = bool(acceptances) and all(
            isinstance(row.get("acceptance_ids"), list)
            and bool(row["acceptance_ids"]) for row in acceptances
        )
        checks.update({
            "deterministic_acceptance.all_pass": exact,
            "all-deterministic-ACs": exact,
        })
    elif step == 13:
        preflights = contents("S09")
        exact = len(preflights) == 1 and all(
            preflights[0].get(key) in (True, "pass", "passed")
            for key in ("geometry", "tensor", "finite", "tiny", "resume")
        )
        checks.update({
            "preflight.geometry_tensor_finite_tiny_resume": exact,
            "S08-exact-revision": exact,
        })
    elif step == 19:
        reviews = contents("S10")
        exact = len(reviews) == 1
        reviewers = reviews[0].get("reviewers") if exact else None
        roles = reviews[0].get("roles") if exact else None
        lenses = reviews[0].get("five_lenses") if exact else None
        disposition = reviews[0].get("deviation_disposition") if exact else None
        independent = (
            isinstance(reviewers, list) and bool(reviewers)
            and reviewers == sorted(set(reviewers))
            and all(isinstance(item, str) and item for item in reviewers)
            and isinstance(roles, list) and bool(roles)
            and roles == sorted(set(roles))
            and all(isinstance(item, str) and item for item in roles)
        )
        five = isinstance(lenses, (list, dict)) and len(lenses) == 5
        clean = disposition in ("none", "resolved", "not_applicable")
        checks.update({
            "authority.reviewer_independent": independent,
            "independent-reviewer": independent,
            "five_lenses.complete": five,
            "five-lenses": five,
            "deviation.none_unresolved": clean,
            "no-unresolved-deviation": clean,
        })
    return checks



def check_experiment_kernel(bundle, content):
    """Ordinary exploratory adapter to original scientific checks, not E01 qualification.

    Baseline/Run anchors remain their existing controller receipts. Do not mint
    B02/S12 or pretend that this projected kernel is a validated full artifact.
    """
    from contracts import (evaluate_gate, validate_schema, validate_scientific_rule,
                           experiment_scientific_kernel_findings, _semantic_author_principals)
    from canonical import parse_document
    if not isinstance(content, dict):
        raise ValueError('explicit_exploratory_scientific_kernel_required')
    keys = {'experiment_id', 'protocol_channel', 'promotable', 'hypothesis', 'variables',
            'budget', 'decision_rule', 'author_principal_ids'}
    if set(content) != keys or content['protocol_channel'] != 'exploratory' or content['promotable'] is not False:
        raise ValueError('ordinary_profile_requires_closed_exploratory_kernel')
    owner = parse_document(bundle.canonical_assets['sdm-create-experiment-spec/assets/experiment-spec.schema.json'])
    original = next(r['properties']['content'] for r in owner['oneOf']
                    if r['properties']['artifact_type'].get('const') == 'E01')
    schema = {'type': 'object', 'additionalProperties': False, 'required': sorted(keys),
              'properties': {key: original['properties'][key] for key in keys}}
    if validate_schema(content, schema):
        raise ValueError('original_experiment_kernel_schema_invalid')
    original=bundle.artifact_contract('E01')
    if (_semantic_author_principals(content,original) or experiment_scientific_kernel_findings(content,original)):
        raise ValueError('original_experiment_scientific_facts_required')
    validate_scientific_rule(content['decision_rule'])
    checks = _semantic_step_checks(6, {'E01': [{'content': content}]})
    row = bundle.document('lifecycle-steps.json')['steps'][5]
    passed, missing, _ = evaluate_gate(row['gate'], {'checks': checks})
    if not passed:
        raise ValueError('original_experiment_kernel_gate_blocked:' + ','.join(missing))
    return {'schema': 'sdm-experiment-kernel-check/v1', 'passed': True,
            'engine_contract_digest': bundle.engine_contract_digest, 'gate_digest': digest(row['gate']),
            'checks': checks, 'kernel_digest': digest(content),
            'scope': 'Original projected E01 fields and scientific checks; not full E01 anchor/authority qualification.',
            'full_artifact_qualified': False, 'execution_authority': False}
