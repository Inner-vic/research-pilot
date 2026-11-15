from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import replace
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from authority import (
    AuthorityError, authority_provenance_subject, redact,
    verify_attestation, verify_authorities,
)
from canonical import canonical_bytes, digest, digest_bytes
from contracts import AssetBundle


def _verifier_record() -> dict[str, str]:
    path = Path(shutil.which("openssl") or "").resolve(strict=True)
    return {"path": str(path), "digest": digest_bytes(path.read_bytes())}


def _keypair(root: Path, name: str) -> tuple[Path, str]:
    private_key = root / f"{name}.private.pem"
    public_key = root / f"{name}.public.pem"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private_key)],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return private_key, public_key.read_text(encoding="ascii")


def _scientific_provenance(
    *,
    experiment_authors: list[str] | None = None,
    implementation_authors: list[str] | None = None,
    evidence_authors: list[str] | None = None,
    evaluator_owner_authors: list[str] | None = None,
    data_split_approver_authors: list[str] | None = None,
) -> list[dict[str, Any]]:
    return [
        {
            "domain": "experiment", "artifact_id": "E01",
            "instance_key": "experiment-1.frozen",
            "artifact_digest": digest({"artifact": "experiment"}),
            "authors": experiment_authors or ["experiment-author"],
        },
        {
            "domain": "implementation", "artifact_id": "E05",
            "instance_key": "implementation-1",
            "artifact_digest": digest({"artifact": "implementation"}),
            "authors": implementation_authors or ["implementation-author"],
        },
        {
            "domain": "evidence", "artifact_id": "D01",
            "instance_key": "evidence-1",
            "artifact_digest": digest({"artifact": "evidence"}),
            "authors": evidence_authors or ["evidence-author"],
        },
        {
            "domain": "evaluator-owner", "artifact_id": "G10",
            "instance_key": "evaluator-1",
            "artifact_digest": digest({"artifact": "evaluator"}),
            "authors": evaluator_owner_authors or ["evaluator-owner"],
        },
        {
            "domain": "data-split-approver", "artifact_id": "R05",
            "instance_key": "split-1",
            "artifact_digest": digest({"artifact": "split"}),
            "authors": data_split_approver_authors or ["split-approver"],
        },
    ]


def _attestation(
    root: Path,
    private_key: Path,
    public_key_pem: str,
    *,
    action: str,
    role: str,
    principal_id: str,
    revision: int,
    trust_roles: list[str] | None = None,
    revocations: list[dict[str, str]] | None = None,
    trust_public_key_pem: str | None = None,
    nonce_suffix: str = "1",
    governed_ids: dict[str, str] | None = None,
    digests: dict[str, str] | None = None,
    provenance: list[dict[str, Any]] | None = None,
    omit_default_provenance: bool = False,
) -> dict[str, Any]:
    key_digest = digest_bytes(public_key_pem.encode("ascii"))
    governed_ids = governed_ids or {"workspace_id": "workspace-1"}
    digests = digests or {"candidate": digest({"candidate": "one"})}
    subject = {
        "action": action,
        "role": role,
        "principal_id": principal_id,
        "key_digest": key_digest,
        "issued_at": "2025-01-01T00:00:00Z",
        "expires_at": "2028-01-01T00:00:00Z",
        "nonce": ("authority-nonce-" + nonce_suffix).ljust(16, "0"),
        "expected_revision": revision,
        "governed_ids": governed_ids,
        "digests": digests,
    }
    if provenance is None and not omit_default_provenance:
        if action == "decision.D05":
            provenance = _scientific_provenance()
        elif action == "freeze.approve":
            provenance = [{
                "domain": "experiment", "artifact_id": "E01",
                "instance_key": "experiment-1",
                "artifact_digest": digest({"artifact": "experiment"}),
                "authors": ["experiment-author"],
            }]
        elif action == "readiness.approve" and governed_ids.get("step") in {
            "7", "19",
        }:
            provenance = [{
                "domain": "experiment", "artifact_id": "E01",
                "instance_key": "experiment-1",
                "artifact_digest": digest({"artifact": "experiment"}),
                "authors": ["experiment-author"],
            }]
            if governed_ids["step"] == "19":
                provenance = _scientific_provenance()
    if provenance is not None:
        subject.update(authority_provenance_subject(provenance))
    subject_path = root / f"subject-{role}-{nonce_suffix}.json"
    signature_path = root / f"signature-{role}-{nonce_suffix}.bin"
    subject_path.write_bytes(canonical_bytes(subject))
    subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-sign",
            "-inkey",
            str(private_key),
            "-rawin",
            "-in",
            str(subject_path),
            "-out",
            str(signature_path),
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    snapshot = {
        "schema": "sdm-trust-snapshot/v1",
        "verifier": _verifier_record(),
        "keys": [
            {
                "principal_id": principal_id,
                "key_digest": key_digest,
                "public_key_pem": trust_public_key_pem or public_key_pem,
                "roles": trust_roles or [role],
                "valid_from": "2024-01-01T00:00:00Z",
                "valid_until": "2029-01-01T00:00:00Z",
            }
        ],
        "revocations": revocations or [],
    }
    return {
        "schema": "sdm-authority-attestation/v1",
        "subject": subject,
        "signature": base64.b64encode(signature_path.read_bytes()).decode("ascii").rstrip("="),
        "public_key_pem": public_key_pem,
        "trust_snapshot": snapshot,
        "trust_snapshot_digest": digest(snapshot),
        "consumed_at": "2026-01-01T00:00:00Z",
    }


def _expected(attestation: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "governed_ids": attestation["subject"]["governed_ids"],
        "digests": attestation["subject"]["digests"],
    }
    if "extensions" in attestation["subject"]:
        expected["extensions"] = attestation["subject"]["extensions"]
    return expected


def _trusted(attestation: dict[str, Any]) -> dict[str, Any]:
    snapshot = attestation["trust_snapshot"]
    return {
        "trusted_snapshot": snapshot,
        "trusted_snapshot_digest": digest(snapshot),
    }


def _share_snapshot(*attestations: dict[str, Any]) -> dict[str, Any]:
    snapshot = {
        "schema": "sdm-trust-snapshot/v1",
        "verifier": attestations[0]["trust_snapshot"]["verifier"],
        "keys": [
            attestation["trust_snapshot"]["keys"][0]
            for attestation in attestations
        ],
        "revocations": [],
    }
    for attestation in attestations:
        attestation["trust_snapshot"] = snapshot
        attestation["trust_snapshot_digest"] = digest(snapshot)
    return snapshot


def test_valid_attestation_is_bound_to_canonical_subject(asset_bundle: AssetBundle, tmp_path: Path) -> None:
    private, public = _keypair(tmp_path, "valid")
    attestation = _attestation(
        tmp_path,
        private,
        public,
        action="decision.D05",
        role="scientific-reviewer",
        principal_id="reviewer-1",
        revision=4,
    )
    verified = verify_attestation(
        attestation,
        action="decision.D05",
        expected_subject=_expected(attestation),
        expected_revision=4,
        bundle=asset_bundle,
        **_trusted(attestation),
    )
    assert verified["principal_id"] == "reviewer-1"
    assert verified["subject_digest"] == digest(attestation["subject"])

    attestation["subject"]["governed_ids"]["workspace_id"] = "tampered"
    with pytest.raises(AuthorityError) as caught:
        verify_attestation(
            attestation,
            action="decision.D05",
            expected_subject=_expected({**attestation, "subject": {**attestation["subject"], "governed_ids": {"workspace_id": "workspace-1"}}}),
            expected_revision=4,
            bundle=asset_bundle,
            **_trusted(attestation),
        )
    assert caught.value.code in {"attestation_subject_mismatch", "signature_invalid"}


def test_lightweight_owner_records_decision_without_independent_reviewer(asset_bundle: AssetBundle, tmp_path: Path) -> None:
    documents = deepcopy(asset_bundle.documents)
    documents["authority-policy.json"]["business_profile"] = "lightweight"
    lightweight = replace(asset_bundle, documents=documents)
    private, public = _keypair(tmp_path, "personal-owner")
    attestation = _attestation(tmp_path, private, public, action="decision.D05",
                               role="research-owner", principal_id="owner", revision=4)
    verified = verify_authorities([attestation], action="decision.D05",
                                  expected_subject=_expected(attestation), expected_revision=4,
                                  bundle=lightweight, **_trusted(attestation))
    assert len(verified) == 1
    assert verified[0]["principal_id"] == "owner"
    with pytest.raises(AuthorityError):
        verify_authorities([attestation], action="decision.D05",
                           expected_subject=_expected(attestation), expected_revision=4,
                           bundle=asset_bundle, **_trusted(attestation))
    attestation["subject"]["digests"]["candidate"] = digest({"tampered": True})
    with pytest.raises(AuthorityError):
        verify_authorities([attestation], action="decision.D05",
                           expected_subject=_expected(attestation), expected_revision=4,
                           bundle=lightweight, **_trusted(attestation))


def test_unknown_governance_profile_is_rejected(asset_bundle: AssetBundle) -> None:
    from authority import _policy
    documents = deepcopy(asset_bundle.documents)
    documents["authority-policy.json"]["business_profile"] = "unchecked"
    with pytest.raises(AuthorityError):
        _policy(replace(asset_bundle, documents=documents), "decision.D05")


def test_action_role_mismatch(asset_bundle: AssetBundle, tmp_path: Path) -> None:
    private, public = _keypair(tmp_path, "role")
    attestation = _attestation(
        tmp_path,
        private,
        public,
        action="readiness.approve",
        role="experiment-author",
        principal_id="author-1",
        revision=2,
    )
    with pytest.raises(AuthorityError) as caught:
        verify_authorities(
            [attestation],
            action="readiness.approve",
            expected_subject=_expected(attestation),
            expected_revision=2,
            bundle=asset_bundle,
            **_trusted(attestation),
        )
    assert caught.value.code == "required_role_missing"

    def case_dir(name: str) -> Path:
        path = tmp_path / name
        path.mkdir()
        return path

    _check_policy_constraints_are_closed_world(
        asset_bundle, case_dir("policy-constraints"),
    )
    _check_readiness_principal_cannot_review_own_bound_experiment(
        asset_bundle, case_dir("readiness-self-review"),
    )
    _check_step7_skips_non_applicable_evidence_author_constraint(
        asset_bundle, case_dir("step7-non-applicable"),
    )
    _check_step19_rejects_reviewer_as_sole_bound_evidence_author(
        asset_bundle, case_dir("step19-sole-author"),
    )
    _check_step19_accepts_different_signed_reviewer_principal(
        asset_bundle, case_dir("step19-independent"),
    )
    for index, (implementation, evaluator, code) in enumerate((
        (
            ["reviewer-principal"], ["evaluator-owner"],
            "principal_is_implementation_author",
        ),
        (
            ["implementation-author", "shared-principal"],
            ["shared-principal"],
            "evaluator_owner_is_implementation_author",
        ),
    )):
        _check_step19_rejects_signer_or_evaluator_implementation_overlap(
            asset_bundle, case_dir(f"step19-overlap-{index}"),
            implementation, evaluator, code,
        )
    _check_freeze_uses_signed_reviewer_principal_for_experiment_separation(
        asset_bundle, case_dir("freeze-separation"),
    )
    for index, (implementation, evidence, code) in enumerate((
        (
            ["reviewer-principal"], ["evidence-author"],
            "principal_is_implementation_author",
        ),
        (
            ["implementation-author"], ["reviewer-principal"],
            "principal_is_sole_evidence_author",
        ),
    )):
        _check_d05_enforces_signed_principal_against_bound_scientific_authors(
            asset_bundle, case_dir(f"d05-author-{index}"),
            implementation, evidence, code,
        )
    _check_d05_provenance_binds_exact_artifact_digest_and_allows_coauthored_evidence(
        asset_bundle, case_dir("d05-provenance"),
    )
    for index, (implementation, evaluator, approvers, code) in enumerate((
        (
            ["implementation-author"], ["evaluator-owner"],
            ["reviewer-principal", "split-approver"],
            "signer_is_data_split_approver",
        ),
        (
            ["implementation-author", "shared-principal"],
            ["shared-principal"], ["split-approver"],
            "evaluator_owner_is_implementation_author",
        ),
    )):
        _check_d05_rejects_split_approval_or_evaluator_implementation_overlap(
            asset_bundle, case_dir(f"d05-overlap-{index}"),
            implementation, evaluator, approvers, code,
        )
    _check_d05_rejects_signed_provenance_domain_substitution(
        asset_bundle, case_dir("d05-domain-substitution"),
    )


def _check_policy_constraints_are_closed_world(
    asset_bundle: AssetBundle, tmp_path: Path,
) -> None:
    private, public = _keypair(tmp_path, "unknown-constraint")
    attestation = _attestation(
        tmp_path, private, public,
        action="decision.D05", role="scientific-reviewer",
        principal_id="reviewer-1", revision=1,
    )
    documents = deepcopy(asset_bundle.documents)
    decision = next(
        row for row in documents["authority-policy.json"]["actions"]
        if row["action"] == "decision.D05"
    )
    decision["constraints"].append("unknown-separation-rule")
    malformed = replace(asset_bundle, documents=documents)
    with pytest.raises(AuthorityError) as caught:
        verify_authorities(
            [attestation], action="decision.D05",
            expected_subject=_expected(attestation), expected_revision=1,
            bundle=malformed, **_trusted(attestation),
        )
    assert caught.value.code == "authority_constraint_unknown"
    assert caught.value.configuration is True


def _check_readiness_principal_cannot_review_own_bound_experiment(
    asset_bundle: AssetBundle, tmp_path: Path,
) -> None:
    private, public = _keypair(tmp_path, "experiment-self-review")
    attestation = _attestation(
        tmp_path, private, public,
        action="readiness.approve", role="independent-protocol-reviewer",
        principal_id="experiment-author", revision=2,
        governed_ids={
            "workspace_id": "workspace-1", "experiment_id": "experiment-1",
            "step": "7",
        },
    )
    with pytest.raises(AuthorityError) as caught:
        verify_authorities(
            [attestation], action="readiness.approve",
            expected_subject=_expected(attestation), expected_revision=2,
            bundle=asset_bundle, **_trusted(attestation),
        )
    assert caught.value.code == "principal_is_experiment_author"


def _check_step7_skips_non_applicable_evidence_author_constraint(
    asset_bundle: AssetBundle, tmp_path: Path,
) -> None:
    private, public = _keypair(tmp_path, "step7-independent")
    attestation = _attestation(
        tmp_path, private, public,
        action="readiness.approve", role="independent-protocol-reviewer",
        principal_id="independent-reviewer", revision=7,
        governed_ids={
            "workspace_id": "workspace-1", "experiment_id": "experiment-1",
            "step": "7",
        },
    )
    verified = verify_authorities(
        [attestation], action="readiness.approve",
        expected_subject=_expected(attestation), expected_revision=7,
        bundle=asset_bundle, **_trusted(attestation),
    )
    assert [item["principal_id"] for item in verified] == [
        "independent-reviewer",
    ]


def _step19_attestation(
    tmp_path: Path,
    *,
    principal_id: str,
    evidence_authors: list[str],
    name: str,
    implementation_authors: list[str] | None = None,
    evaluator_owner_authors: list[str] | None = None,
    data_split_approver_authors: list[str] | None = None,
) -> dict[str, Any]:
    private, public = _keypair(tmp_path, name)
    return _attestation(
        tmp_path, private, public,
        action="readiness.approve", role="independent-protocol-reviewer",
        principal_id=principal_id, revision=19,
        governed_ids={
            "workspace_id": "workspace-1", "experiment_id": "experiment-1",
            "evidence_id": "evidence-1", "step": "19",
        },
        provenance=_scientific_provenance(
            evidence_authors=evidence_authors,
            implementation_authors=implementation_authors,
            evaluator_owner_authors=evaluator_owner_authors,
            data_split_approver_authors=data_split_approver_authors,
        ),
    )


def _check_step19_rejects_reviewer_as_sole_bound_evidence_author(
    asset_bundle: AssetBundle, tmp_path: Path,
) -> None:
    attestation = _step19_attestation(
        tmp_path, principal_id="reviewer-principal",
        evidence_authors=["reviewer-principal"], name="step19-self-review",
    )
    with pytest.raises(AuthorityError) as caught:
        verify_authorities(
            [attestation], action="readiness.approve",
            expected_subject=_expected(attestation), expected_revision=19,
            bundle=asset_bundle, **_trusted(attestation),
        )
    assert caught.value.code == "principal_is_sole_evidence_author"


def _check_step19_accepts_different_signed_reviewer_principal(
    asset_bundle: AssetBundle, tmp_path: Path,
) -> None:
    attestation = _step19_attestation(
        tmp_path, principal_id="independent-reviewer",
        evidence_authors=["evidence-author"], name="step19-independent",
    )
    verified = verify_authorities(
        [attestation], action="readiness.approve",
        expected_subject=_expected(attestation), expected_revision=19,
        bundle=asset_bundle, **_trusted(attestation),
    )
    assert [(item["principal_id"], item["role"]) for item in verified] == [
        ("independent-reviewer", "independent-protocol-reviewer"),
    ]

    split_conflict = _step19_attestation(
        tmp_path, principal_id="independent-reviewer",
        evidence_authors=["evidence-author"],
        data_split_approver_authors=["independent-reviewer"],
        name="step19-split-approver-conflict",
    )
    with pytest.raises(AuthorityError) as caught:
        verify_authorities(
            [split_conflict], action="readiness.approve",
            expected_subject=_expected(split_conflict), expected_revision=19,
            bundle=asset_bundle, **_trusted(split_conflict),
        )
    assert caught.value.code == "signer_is_data_split_approver"


def _check_step19_rejects_signer_or_evaluator_implementation_overlap(
    asset_bundle: AssetBundle,
    tmp_path: Path,
    implementation_authors: list[str],
    evaluator_owner_authors: list[str],
    expected_code: str,
) -> None:
    attestation = _step19_attestation(
        tmp_path,
        principal_id="reviewer-principal",
        evidence_authors=["evidence-author"],
        implementation_authors=implementation_authors,
        evaluator_owner_authors=evaluator_owner_authors,
        name=expected_code,
    )
    with pytest.raises(AuthorityError) as caught:
        verify_authorities(
            [attestation], action="readiness.approve",
            expected_subject=_expected(attestation), expected_revision=19,
            bundle=asset_bundle, **_trusted(attestation),
        )
    assert caught.value.code == expected_code


def _check_freeze_uses_signed_reviewer_principal_for_experiment_separation(
    asset_bundle: AssetBundle, tmp_path: Path,
) -> None:
    owner_private, owner_public = _keypair(tmp_path, "freeze-owner-author")
    reviewer_private, reviewer_public = _keypair(tmp_path, "freeze-reviewer-author")
    provenance = [{
        "domain": "experiment", "artifact_id": "E01",
        "instance_key": "experiment-1",
        "artifact_digest": digest({"artifact": "experiment"}),
        "authors": ["reviewer-principal"],
    }]
    owner = _attestation(
        tmp_path, owner_private, owner_public,
        action="freeze.approve", role="research-owner",
        principal_id="owner-principal", revision=3,
        nonce_suffix="freeze-owner", provenance=provenance,
    )
    reviewer = _attestation(
        tmp_path, reviewer_private, reviewer_public,
        action="freeze.approve", role="independent-protocol-reviewer",
        principal_id="reviewer-principal", revision=3,
        nonce_suffix="freeze-reviewer", provenance=provenance,
    )
    snapshot = _share_snapshot(owner, reviewer)
    with pytest.raises(AuthorityError) as caught:
        verify_authorities(
            [owner, reviewer], action="freeze.approve",
            expected_subject=_expected(owner), expected_revision=3,
            bundle=asset_bundle, trusted_snapshot=snapshot,
            trusted_snapshot_digest=digest(snapshot),
        )
    assert caught.value.code == "reviewer_is_experiment_author"


def _check_d05_enforces_signed_principal_against_bound_scientific_authors(
    asset_bundle: AssetBundle, tmp_path: Path,
    implementation_authors: list[str], evidence_authors: list[str],
    expected_code: str,
) -> None:
    private, public = _keypair(tmp_path, expected_code)
    provenance = _scientific_provenance(
        implementation_authors=implementation_authors,
        evidence_authors=evidence_authors,
    )
    attestation = _attestation(
        tmp_path, private, public,
        action="decision.D05", role="scientific-reviewer",
        principal_id="reviewer-principal", revision=4,
        provenance=provenance,
    )
    with pytest.raises(AuthorityError) as caught:
        verify_authorities(
            [attestation], action="decision.D05",
            expected_subject=_expected(attestation), expected_revision=4,
            bundle=asset_bundle, **_trusted(attestation),
        )
    assert caught.value.code == expected_code


def _check_d05_provenance_binds_exact_artifact_digest_and_allows_coauthored_evidence(
    asset_bundle: AssetBundle, tmp_path: Path,
) -> None:
    private, public = _keypair(tmp_path, "d05-provenance-binding")
    attestation = _attestation(
        tmp_path, private, public,
        action="decision.D05", role="scientific-reviewer",
        principal_id="reviewer-principal", revision=5,
        provenance=_scientific_provenance(
            evidence_authors=["evidence-coauthor", "reviewer-principal"],
        ),
    )
    verified = verify_authorities(
        [attestation], action="decision.D05",
        expected_subject=_expected(attestation), expected_revision=5,
        bundle=asset_bundle, **_trusted(attestation),
    )
    assert [item["principal_id"] for item in verified] == [
        "reviewer-principal",
    ]

    substituted = deepcopy(_expected(attestation))
    evaluator_row = next(
        row for row in substituted["extensions"]
        ["dev.bmad.sdm.provenance"]["artifacts"]
        if row["domain"] == "evaluator-owner"
    )
    evaluator_row["artifact_digest"] = digest({"artifact": "substituted"})
    with pytest.raises(AuthorityError) as caught:
        verify_authorities(
            [attestation], action="decision.D05",
            expected_subject=substituted, expected_revision=5,
            bundle=asset_bundle, **_trusted(attestation),
        )
    assert caught.value.code == "attestation_subject_mismatch"


def _check_d05_rejects_split_approval_or_evaluator_implementation_overlap(
    asset_bundle: AssetBundle,
    tmp_path: Path,
    implementation_authors: list[str],
    evaluator_owner_authors: list[str],
    data_split_approver_authors: list[str],
    expected_code: str,
) -> None:
    private, public = _keypair(tmp_path, expected_code)
    attestation = _attestation(
        tmp_path, private, public,
        action="decision.D05", role="scientific-reviewer",
        principal_id="reviewer-principal", revision=6,
        provenance=_scientific_provenance(
            implementation_authors=implementation_authors,
            evaluator_owner_authors=evaluator_owner_authors,
            data_split_approver_authors=data_split_approver_authors,
        ),
    )
    with pytest.raises(AuthorityError) as caught:
        verify_authorities(
            [attestation], action="decision.D05",
            expected_subject=_expected(attestation), expected_revision=6,
            bundle=asset_bundle, **_trusted(attestation),
        )
    assert caught.value.code == expected_code


def _check_d05_rejects_signed_provenance_domain_substitution(
    asset_bundle: AssetBundle, tmp_path: Path,
) -> None:
    private, public = _keypair(tmp_path, "d05-domain-substitution")
    provenance = _scientific_provenance()
    evaluator_row = next(
        row for row in provenance if row["domain"] == "evaluator-owner"
    )
    evaluator_row.update({"domain": "evidence", "artifact_id": "R02"})
    attestation = _attestation(
        tmp_path, private, public,
        action="decision.D05", role="scientific-reviewer",
        principal_id="reviewer-principal", revision=7,
        provenance=provenance,
    )
    with pytest.raises(AuthorityError) as caught:
        verify_authorities(
            [attestation], action="decision.D05",
            expected_subject=_expected(attestation), expected_revision=7,
            bundle=asset_bundle, **_trusted(attestation),
        )
    assert caught.value.code == "authority_provenance_invalid"

    misclassified = _scientific_provenance()
    split_row = next(
        row for row in misclassified
        if row["domain"] == "data-split-approver"
    )
    split_row["domain"] = "evidence"
    with pytest.raises(AuthorityError) as direct:
        authority_provenance_subject(misclassified)
    assert direct.value.code == "authority_provenance_invalid"


def test_key_substitution_rejected(asset_bundle: AssetBundle, tmp_path: Path) -> None:
    private, public = _keypair(tmp_path, "signed")
    _, substituted_public = _keypair(tmp_path, "substituted")
    attestation = _attestation(
        tmp_path,
        private,
        public,
        action="decision.D05",
        role="scientific-reviewer",
        principal_id="reviewer-1",
        revision=1,
        trust_public_key_pem=substituted_public,
    )
    with pytest.raises(AuthorityError) as caught:
        verify_attestation(
            attestation,
            action="decision.D05",
            expected_subject=_expected(attestation),
            expected_revision=1,
            bundle=asset_bundle,
            **_trusted(attestation),
        )
    assert caught.value.code == "key_substitution"


def test_historical_consumption_time(asset_bundle: AssetBundle, tmp_path: Path) -> None:
    private, public = _keypair(tmp_path, "historical")
    key_digest = digest_bytes(public.encode("ascii"))
    attestation = _attestation(
        tmp_path,
        private,
        public,
        action="decision.D05",
        role="scientific-reviewer",
        principal_id="reviewer-1",
        revision=8,
        revocations=[{"key_digest": key_digest, "effective_at": "2027-01-01T00:00:00Z", "reason": "rotated"}],
    )
    verified = verify_attestation(
        attestation,
        action="decision.D05",
        expected_subject=_expected(attestation),
        expected_revision=8,
        bundle=asset_bundle,
        consumption_time="2026-06-01T00:00:00Z",
        **_trusted(attestation),
    )
    assert verified["consumed_at"] == "2026-06-01T00:00:00Z"
    with pytest.raises(AuthorityError) as caught:
        verify_attestation(
            attestation,
            action="decision.D05",
            expected_subject=_expected(attestation),
            expected_revision=8,
            bundle=asset_bundle,
            consumption_time="2027-06-01T00:00:00Z",
            **_trusted(attestation),
        )
    assert caught.value.code == "key_revoked"


def test_two_authority_same_principal_rejected(asset_bundle: AssetBundle, tmp_path: Path) -> None:
    private, public = _keypair(tmp_path, "shared")
    roles = ["research-owner", "independent-protocol-reviewer"]
    owner = _attestation(
        tmp_path,
        private,
        public,
        action="freeze.approve",
        role=roles[0],
        principal_id="same-person",
        revision=7,
        trust_roles=roles,
        nonce_suffix="owner",
    )
    reviewer = _attestation(
        tmp_path,
        private,
        public,
        action="freeze.approve",
        role=roles[1],
        principal_id="same-person",
        revision=7,
        trust_roles=roles,
        nonce_suffix="reviewer",
    )
    with pytest.raises(AuthorityError) as caught:
        verify_authorities(
            [owner, reviewer],
            action="freeze.approve",
            expected_subject=_expected(owner),
            expected_revision=7,
            bundle=asset_bundle,
            **_trusted(owner),
        )
    assert caught.value.code == "authority_not_independent"


def test_scientific_supersession_authorities_use_real_signatures_and_distinct_policy_roles(
    asset_bundle: AssetBundle,
    tmp_path: Path,
) -> None:
    owner_private, owner_public = _keypair(tmp_path, "scientific-reviewer")
    risk_private, risk_public = _keypair(tmp_path, "governance")
    owner = _attestation(
        tmp_path, owner_private, owner_public,
        action="decision.supersede", role="scientific-reviewer",
        principal_id="owner-1", revision=11, nonce_suffix="science-reviewer",
    )
    risk = _attestation(
        tmp_path, risk_private, risk_public,
        action="decision.supersede", role="governance-authority",
        principal_id="risk-1", revision=11, nonce_suffix="governance",
    )
    trusted_snapshot = {
        "schema": "sdm-trust-snapshot/v1",
        "verifier": _verifier_record(),
        "keys": [
            owner["trust_snapshot"]["keys"][0],
            risk["trust_snapshot"]["keys"][0],
        ],
        "revocations": [],
    }
    for attestation in (owner, risk):
        attestation["trust_snapshot"] = trusted_snapshot
        attestation["trust_snapshot_digest"] = digest(trusted_snapshot)
    verified = verify_authorities(
        [owner, risk], action="decision.supersede",
        expected_subject=_expected(owner), expected_revision=11,
        bundle=asset_bundle,
        trusted_snapshot=trusted_snapshot,
        trusted_snapshot_digest=digest(trusted_snapshot),
    )
    assert {(item["role"], item["principal_id"]) for item in verified} == {
        ("scientific-reviewer", "owner-1"),
        ("governance-authority", "risk-1"),
    }
    assert all(item["subject_digest"].startswith("sha256:") for item in verified)

    with pytest.raises(AuthorityError) as caught:
        verify_authorities(
            [owner, risk], action="decision.supersede",
            expected_subject={
                "governed_ids": {"workspace_id": "workspace-substituted"},
                "digests": owner["subject"]["digests"],
            },
            expected_revision=11, bundle=asset_bundle,
            trusted_snapshot=trusted_snapshot,
            trusted_snapshot_digest=digest(trusted_snapshot),
        )
    assert caught.value.code == "attestation_subject_mismatch"


def test_redaction_removes_secrets_and_signed_query_values() -> None:
    value = {
        "token": "top-secret",
        "metrics_uri": "https://example.invalid/report?signature=secret",
        "nested": {"private_key": "pem"},
    }
    redacted = redact(value)
    assert redacted["token"]["redacted"] is True
    assert redacted["nested"]["private_key"]["redacted"] is True
    assert redacted["metrics_uri"] == "https://example.invalid/report"


def test_trust_pinned_verifier_ignores_path_substitution(
    asset_bundle: AssetBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private, public = _keypair(tmp_path, "pinned-path")
    attestation = _attestation(
        tmp_path, private, public, action="decision.D05",
        role="scientific-reviewer", principal_id="reviewer-pinned",
        revision=3,
    )
    fake = tmp_path / "openssl"
    marker = tmp_path / "marker"
    fake.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert verify_attestation(
        attestation, action="decision.D05",
        expected_subject=_expected(attestation), expected_revision=3,
        bundle=asset_bundle, **_trusted(attestation),
    )["principal_id"] == "reviewer-pinned"
    assert not marker.exists()


def test_verifier_timeout_is_a_governed_authority_failure(
    asset_bundle: AssetBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private, public = _keypair(tmp_path, "timeout")
    attestation = _attestation(
        tmp_path, private, public, action="decision.D05",
        role="scientific-reviewer", principal_id="reviewer-timeout",
        revision=4,
    )

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0] if args else "openssl", 10)

    monkeypatch.setattr("authority.subprocess.run", timeout)
    with pytest.raises(AuthorityError) as caught:
        verify_attestation(
            attestation, action="decision.D05",
            expected_subject=_expected(attestation), expected_revision=4,
            bundle=asset_bundle, **_trusted(attestation),
        )
    assert caught.value.code == "verifier_timeout"


def test_trust_snapshot_rejects_one_key_for_multiple_principals(
    asset_bundle: AssetBundle, tmp_path: Path,
) -> None:
    private, public = _keypair(tmp_path, "duplicate-key")
    attestation = _attestation(
        tmp_path, private, public, action="decision.D05",
        role="scientific-reviewer", principal_id="reviewer-one",
        revision=5,
    )
    snapshot = deepcopy(attestation["trust_snapshot"])
    duplicate = deepcopy(snapshot["keys"][0])
    duplicate["principal_id"] = "reviewer-two"
    snapshot["keys"].append(duplicate)
    attestation["trust_snapshot"] = snapshot
    attestation["trust_snapshot_digest"] = digest(snapshot)
    with pytest.raises(AuthorityError) as caught:
        verify_attestation(
            attestation, action="decision.D05",
            expected_subject=_expected(attestation), expected_revision=5,
            bundle=asset_bundle,
            trusted_snapshot=snapshot,
            trusted_snapshot_digest=digest(snapshot),
        )
    assert caught.value.code == "trust_snapshot_invalid"


def test_redactor_normalizes_credential_names_and_every_uri_variant() -> None:
    value = {
        "Proxy-Authorization": "Basic secret",
        "x.API_KEY": "secret",
        "Session.Key": "secret",
        "ordinary": "https://user:pass@example.invalid/path?q=secret#fragment",
        "nested": [{"Set_Cookie": "session=secret"}],
    }
    redacted = redact(value)
    assert redacted["Proxy-Authorization"]["redacted"] is True
    assert redacted["x.API_KEY"]["redacted"] is True
    assert redacted["Session.Key"]["redacted"] is True
    assert redacted["ordinary"] == "https://example.invalid/path"
    assert redacted["nested"][0]["Set_Cookie"]["redacted"] is True
