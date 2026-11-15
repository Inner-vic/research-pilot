#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Pinned Ed25519 authority attestations with historical replay semantics."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from canonical import canonical_bytes, digest, digest_bytes
from contracts import AssetBundle, ContractError, finding, sort_findings, validate_schema


_B64 = re.compile(r"^[A-Za-z0-9+/]+$")
_PEM = re.compile(r"^-----BEGIN PUBLIC KEY-----\n(?:[A-Za-z0-9+/]{1,64}|[A-Za-z0-9+/]{1,63}=|[A-Za-z0-9+/]{1,62}==)\n(?:[A-Za-z0-9+/]{1,64}\n)*-----END PUBLIC KEY-----\n$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_RFC3339 = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
_ARTIFACT_ID = re.compile(r"^[A-Z][0-9]{2}$")
_SENSITIVE_KEY_PARTS = (
    "authorization", "proxyauthorization", "cookie", "setcookie",
    "apikey", "accesskey", "secretkey", "sessionkey", "token",
    "password", "passwd", "privatekey", "credential", "signedurl",
    "hiddenlabel", "evaluatorsource", "evaluatorcontent",
)
PLATFORM_RESULT_ACTION = "platform.result"
PLATFORM_RESULT_ROLE = "platform-producer"

AUTHORITY_PROVENANCE_EXTENSION = "dev.bmad.sdm.provenance"
AUTHORITY_PROVENANCE_SCHEMA = "sdm-authority-provenance/v1"
_PROVENANCE_DOMAINS = frozenset({
    "experiment", "implementation", "evidence", "evaluator-owner",
    "data-split-approver",
})
_PROVENANCE_ARTIFACTS = {
    "experiment": frozenset({"E01", "E02", "E03", "E04", "E06", "E07"}),
    "implementation": frozenset({
        "E05", "S05", "S06", "S07", "S08", "S09",
    }),
    "evidence": frozenset({
        *(f"R{index:02d}" for index in range(1, 9) if index != 5),
        *(f"D{index:02d}" for index in range(1, 5)),
        "S10",
    }),
    "evaluator-owner": frozenset({"G10"}),
    "data-split-approver": frozenset({"R05"}),
}

# Policy constraints are a closed set.  Authority-owned constraints are
# evaluated here from verified signatures and coordinator-derived provenance.
# The remaining constraints are evaluated by their named lifecycle
# transition before this signature gate is invoked; declaring that ownership
# in the pinned policy prevents an unknown spelling from becoming a no-op.
_AUTHORITY_CONSTRAINTS = frozenset({
    "distinct-principal-ids",
    "principal-not-experiment-author",
    "reviewer-not-experiment-author",
    "principal-not-implementation-author",
    "principal-not-sole-evidence-author",
    "signer-not-implementation-author",
    "signer-not-data-split-approver",
    "evaluator-owner-disjoint-from-implementation-authors",
})
_COORDINATOR_CONSTRAINTS = frozenset({
    "all-children-accounted",
    "child-dispositions-complete",
    "complete-accounting",
    "external-anchor-required",
    "no-active-dependency-or-explicit-pinned-policy",
    "no-never-rule-bypass",
    "non-material-only",
    "observed-facts-only",
    "old-and-new-engine-digests-bound",
    "pre-run-only",
    "reciprocal-links",
    "scope-exact",
})
_KNOWN_CONSTRAINTS = _AUTHORITY_CONSTRAINTS | _COORDINATOR_CONSTRAINTS

_PROVENANCE_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "readiness.approve": {
        "by_step": {
            "7": ("experiment",),
            "19": (
                "experiment", "implementation", "evidence",
                "evaluator-owner", "data-split-approver",
            ),
        },
    },
    "freeze.approve": {
        "always": ("experiment",),
    },
    "decision.D05": {
        "always": (
            "experiment", "implementation", "evidence",
            "evaluator-owner", "data-split-approver",
        ),
    },
}


class AuthorityError(RuntimeError):
    def __init__(self, code: str, message: str, *, configuration: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.configuration = configuration


def authority_provenance_subject(
    artifacts: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Build the canonical signed-subject fragment for authorship provenance.

    Callers must derive these rows from authenticated artifact/event history;
    this helper only canonicalizes that already-verified closure.  Returning a
    complete ``extensions`` value makes it difficult for a caller to
    accidentally sign provenance outside the subject that is compared by
    :func:`verify_attestation`.
    """
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(artifacts):
        if not isinstance(row, dict):
            raise AuthorityError(
                "authority_provenance_invalid",
                f"provenance artifact row {index} must be an object",
            )
        authors = row.get("authors")
        normalized.append({
            "domain": row.get("domain"),
            "artifact_id": row.get("artifact_id"),
            "instance_key": row.get("instance_key"),
            "artifact_digest": row.get("artifact_digest"),
            "authors": (
                sorted(authors)
                if isinstance(authors, list)
                and all(isinstance(author, str) for author in authors)
                else authors
            ),
        })
    normalized.sort(key=lambda row: (
        str(row.get("domain")), str(row.get("artifact_id")),
        str(row.get("instance_key")),
    ))
    value = {
        "schema": AUTHORITY_PROVENANCE_SCHEMA,
        "artifacts": normalized,
    }
    _parse_provenance_value(value, required_domains=None)
    return {"extensions": {AUTHORITY_PROVENANCE_EXTENSION: value}}


def _parse_provenance_value(
    value: Any,
    *,
    required_domains: frozenset[str] | None,
) -> dict[str, frozenset[str]]:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "artifacts"}
        or value.get("schema") != AUTHORITY_PROVENANCE_SCHEMA
        or not isinstance(value.get("artifacts"), list)
        or not value["artifacts"]
    ):
        raise AuthorityError(
            "authority_provenance_invalid",
            "signed authority provenance must be one non-empty closed-world v1 artifact closure",
        )
    authors_by_domain: dict[str, set[str]] = {}
    identities: list[tuple[str, str, str]] = []
    ordering: list[tuple[str, str, str]] = []
    for index, row in enumerate(value["artifacts"]):
        if not isinstance(row, dict) or set(row) != {
            "domain", "artifact_id", "instance_key", "artifact_digest",
            "authors",
        }:
            raise AuthorityError(
                "authority_provenance_invalid",
                f"provenance artifact row {index} has an open or incomplete shape",
            )
        domain = row.get("domain")
        artifact_id = row.get("artifact_id")
        instance_key = row.get("instance_key")
        artifact_digest = row.get("artifact_digest")
        authors = row.get("authors")
        if (
            domain not in _PROVENANCE_DOMAINS
            or not isinstance(artifact_id, str)
            or not _ARTIFACT_ID.fullmatch(artifact_id)
            or artifact_id not in _PROVENANCE_ARTIFACTS.get(domain, ())
            or not isinstance(instance_key, str)
            or not instance_key
            or not isinstance(artifact_digest, str)
            or not _DIGEST.fullmatch(artifact_digest)
            or not isinstance(authors, list)
            or not authors
            or any(
                not isinstance(author, str) or not author
                for author in authors
            )
            or authors != sorted(authors)
            or len(authors) != len(set(authors))
        ):
            raise AuthorityError(
                "authority_provenance_invalid",
                f"provenance artifact row {index} has invalid domain, identity, digest, or authors",
            )
        identity = (domain, artifact_id, instance_key)
        identities.append(identity)
        ordering.append(identity)
        authors_by_domain.setdefault(domain, set()).update(authors)
    if len(identities) != len(set(identities)) or ordering != sorted(ordering):
        raise AuthorityError(
            "authority_provenance_invalid",
            "provenance artifacts must be unique and canonically ordered",
        )
    actual_domains = frozenset(authors_by_domain)
    if required_domains is not None and actual_domains != required_domains:
        raise AuthorityError(
            "authority_provenance_invalid",
            "signed provenance domains do not equal the action's exact required closure",
        )
    return {
        domain: frozenset(authors)
        for domain, authors in authors_by_domain.items()
    }


def _policy_error(message: str) -> AuthorityError:
    return AuthorityError(
        "authority_policy_invalid", message, configuration=True,
    )


def _validate_policy(document: Any) -> list[dict[str, Any]]:
    if not isinstance(document, dict) or document.get("schema") != \
            "sdm-authority-policy/v1":
        raise _policy_error("authority policy must be a v1 object")
    if document.get("business_profile", "strict") not in ("lightweight", "strict"):
        raise _policy_error("unknown business governance profile")
    execution = document.get("constraint_execution")
    if (
        not isinstance(execution, dict)
        or set(execution) != {"mode", "authority", "coordinator"}
        or execution.get("mode") != "closed-world"
        or execution.get("authority") != sorted(_AUTHORITY_CONSTRAINTS)
        or execution.get("coordinator") != sorted(_COORDINATOR_CONSTRAINTS)
    ):
        raise _policy_error(
            "authority policy constraint ownership must equal the executable closed world",
        )
    provenance = document.get("provenance")
    serialized_requirements = {
        action: {
            key: {
                step: list(domains) for step, domains in value.items()
            } if key == "by_step" else list(value)
            for key, value in requirement.items()
        }
        for action, requirement in _PROVENANCE_REQUIREMENTS.items()
    }
    if provenance != {
        "extension": AUTHORITY_PROVENANCE_EXTENSION,
        "schema": AUTHORITY_PROVENANCE_SCHEMA,
        "record_fields": [
            "domain", "artifact_id", "instance_key", "artifact_digest",
            "authors",
        ],
        "required_domains": serialized_requirements,
    }:
        raise _policy_error(
            "authority policy provenance contract differs from the executable contract",
        )
    rows = document.get("actions")
    if not isinstance(rows, list) or not rows:
        raise _policy_error("authority policy actions must be a non-empty array")
    actions: list[str] = []
    used_constraints: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not {
            "action", "roles", "minimum", "constraints",
        }.issubset(row):
            raise _policy_error(
                f"authority policy action row {index} is incomplete",
            )
        action = row.get("action")
        roles = row.get("roles")
        minimum = row.get("minimum")
        constraints = row.get("constraints")
        if (
            not isinstance(action, str) or not action
            or not isinstance(roles, list) or not roles
            or any(not isinstance(role, str) or not role for role in roles)
            or roles != list(dict.fromkeys(roles))
            or not isinstance(minimum, int) or isinstance(minimum, bool)
            or minimum < len(roles)
            or not isinstance(constraints, list)
            or any(
                not isinstance(constraint, str) or not constraint
                for constraint in constraints
            )
            or constraints != list(dict.fromkeys(constraints))
        ):
            raise _policy_error(
                f"authority policy action row {index} has invalid identity or cardinality fields",
            )
        unknown = set(constraints) - _KNOWN_CONSTRAINTS
        if unknown:
            raise AuthorityError(
                "authority_constraint_unknown",
                "authority policy declares unknown constraint(s): "
                + ", ".join(sorted(unknown)),
                configuration=True,
            )
        if minimum > 1 and "distinct-principal-ids" not in constraints:
            raise _policy_error(
                f"multi-authority action lacks distinct-principal-ids: {action}",
            )
        actions.append(action)
        used_constraints.update(constraints)
    if len(actions) != len(set(actions)):
        raise _policy_error("authority policy action names must be unique")
    if used_constraints != _KNOWN_CONSTRAINTS:
        raise _policy_error(
            "authority policy actions must use every declared constraint and no others",
        )
    return rows


def _time(value: str) -> datetime:
    if not isinstance(value, str) or _RFC3339.fullmatch(value) is None:
        raise AuthorityError("authority_time_invalid", "authority timestamp is not strict RFC 3339")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except (AttributeError, ValueError) as exc:
        raise AuthorityError("authority_time_invalid", "authority timestamp is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise AuthorityError("authority_time_invalid", "authority timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _policy(bundle: AssetBundle, action: str) -> dict[str, Any]:
    document = bundle.document("authority-policy.json")
    for row in _validate_policy(document):
        if row["action"] == action:
            if document.get("business_profile", "strict") == "lightweight":
                # Owner authentication records intent and provenance, without
                # demanding a separate reviewer for a personal experiment.
                # Coordinator constraints still enforce exact scope, pre-run
                # limits, complete accounting and no prohibited-rule bypass.
                return {**row, "roles": ["research-owner"], "minimum": 1,
                        "constraints": [constraint for constraint in row["constraints"]
                                        if constraint in _COORDINATOR_CONSTRAINTS]}
            return row
    raise AuthorityError("authority_action_unknown", f"authority action is not declared: {action}")


def _decode_signature(value: str) -> bytes:
    if not isinstance(value, str) or "=" in value or not _B64.fullmatch(value):
        raise AuthorityError("signature_encoding_invalid", "signature must be strict unpadded standard base64")
    try:
        result = base64.b64decode(value + ("=" * ((4 - len(value) % 4) % 4)), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AuthorityError("signature_encoding_invalid", "signature is not valid base64") from exc
    if len(result) != 64:
        raise AuthorityError("signature_encoding_invalid", "Ed25519 signature must be 64 bytes")
    return result


def _stable_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _open_pinned_verifier(snapshot: dict[str, Any]) -> int:
    verifier = snapshot.get("verifier")
    if not isinstance(verifier, dict) or set(verifier) != {"path", "digest"}:
        raise AuthorityError(
            "secure_verifier_unavailable",
            "trust snapshot lacks one closed pinned verifier identity",
            configuration=True,
        )
    path = verifier.get("path")
    expected = verifier.get("digest")
    if (
        not isinstance(path, str) or not os.path.isabs(path)
        or os.path.normpath(path) != path
        or not isinstance(expected, str) or _DIGEST.fullmatch(expected) is None
    ):
        raise AuthorityError(
            "secure_verifier_unavailable",
            "trust snapshot verifier path/digest is invalid",
            configuration=True,
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise AuthorityError(
            "openssl_unavailable",
            "the trust-pinned OpenSSL verifier is unavailable",
            configuration=True,
        ) from exc
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_mode & 0o111 == 0
        ):
            raise AuthorityError(
                "secure_verifier_unavailable",
                "the trust-pinned verifier is not one executable single-link regular file",
                configuration=True,
            )
        hasher = __import__("hashlib").sha256()
        while True:
            chunk = os.read(fd, 1_048_576)
            if not chunk:
                break
            hasher.update(chunk)
        after = os.fstat(fd)
        if _stable_identity(before) != _stable_identity(after):
            raise AuthorityError("verifier_identity_changed", "the trust-pinned verifier changed while it was hashed")
        if "sha256:" + hasher.hexdigest() != expected:
            raise AuthorityError("verifier_digest_mismatch", "the trust-pinned verifier digest does not match its bytes")
        current = os.stat(path, follow_symlinks=False)
        if _stable_identity(current) != _stable_identity(after):
            raise AuthorityError("verifier_identity_changed", "the trust-pinned verifier path changed after it was opened")
        os.lseek(fd, 0, os.SEEK_SET)
        return fd
    except Exception:
        os.close(fd)
        raise


def _verify_openssl(
    public_key: bytes,
    subject: bytes,
    signature: bytes,
    snapshot: dict[str, Any],
) -> None:
    if not hasattr(os, "memfd_create"):
        raise AuthorityError("secure_verifier_unavailable", "in-memory OpenSSL file descriptors are unavailable", configuration=True)
    verifier_fd = _open_pinned_verifier(snapshot)
    descriptors: list[int] = [verifier_fd]
    try:
        for name, value in (("sdm-public", public_key), ("sdm-subject", subject), ("sdm-signature", signature)):
            fd = os.memfd_create(name, getattr(os, "MFD_CLOEXEC", 0))
            descriptors.append(fd)
            view = memoryview(value)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.lseek(fd, 0, os.SEEK_SET)
        command = [
            f"/proc/self/fd/{verifier_fd}",
            "pkeyutl",
            "-verify",
            "-pubin",
            "-inkey",
            f"/proc/self/fd/{descriptors[1]}",
            "-rawin",
            "-in",
            f"/proc/self/fd/{descriptors[2]}",
            "-sigfile",
            f"/proc/self/fd/{descriptors[3]}",
        ]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=tuple(descriptors),
                timeout=10,
                check=False,
             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as exc:
            raise AuthorityError("verifier_timeout", "Ed25519 verifier exceeded its 10-second deadline") from exc
        except OSError as exc:
            raise AuthorityError("secure_verifier_unavailable", "the trust-pinned verifier could not start", configuration=True) from exc
        if completed.returncode != 0:
            raise AuthorityError("signature_invalid", "Ed25519 signature verification failed")
    finally:
        for fd in descriptors:
            os.close(fd)


def _trust_key(snapshot: dict[str, Any], principal_id: str, key_digest: str) -> dict[str, Any]:
    keys = snapshot.get("keys")
    if not isinstance(keys, list):
        raise AuthorityError("trust_snapshot_invalid", "trust snapshot lacks a keys array")
    assignments: dict[str, str] = {}
    identities: set[tuple[str, str]] = set()
    for row in keys:
        if not isinstance(row, dict):
            raise AuthorityError("trust_snapshot_invalid", "trust snapshot key rows must be objects")
        principal = row.get("principal_id")
        digest_value = row.get("key_digest")
        identity = (str(principal), str(digest_value))
        if identity in identities:
            raise AuthorityError("trust_snapshot_invalid", "trust snapshot contains a duplicate key identity")
        identities.add(identity)
        prior = assignments.get(str(digest_value))
        if prior is not None and prior != principal:
            raise AuthorityError("trust_snapshot_invalid", "one active key digest is assigned to multiple principals")
        assignments[str(digest_value)] = str(principal)
    matches = [row for row in keys if row.get("principal_id") == principal_id and row.get("key_digest") == key_digest]
    if len(matches) != 1:
        raise AuthorityError("key_substitution", "attested principal/key is not unique in the pinned trust snapshot")
    return matches[0]


def verify_attestation(
    attestation: dict[str, Any],
    *,
    action: str,
    expected_subject: dict[str, Any],
    expected_revision: int,
    bundle: AssetBundle,
    consumption_time: str | None = None,
    trusted_snapshot: dict[str, Any] | None = None,
    trusted_snapshot_digest: str | None = None,
) -> dict[str, Any]:
    schema_findings = validate_schema(attestation, bundle.document("schemas/authority-attestation.schema.json"), subject=action, governing_asset="schemas/authority-attestation.schema.json")
    if schema_findings:
        raise AuthorityError("attestation_schema_invalid", schema_findings[0]["message"])
    subject = attestation.get("subject")
    if not isinstance(subject, dict):
        raise AuthorityError("attestation_subject_invalid", "attestation subject must be an object")
    mandatory = {"action", "role", "principal_id", "key_digest", "issued_at", "expires_at", "nonce", "expected_revision"}
    if not mandatory.issubset(subject):
        raise AuthorityError("attestation_subject_invalid", "attestation subject lacks mandatory identity/time/revision fields")
    if subject["action"] != action or subject["expected_revision"] != expected_revision:
        raise AuthorityError("attestation_subject_mismatch", "authority action or expected revision does not match the transition")
    for key, value in expected_subject.items():
        if subject.get(key) != value:
            raise AuthorityError("attestation_subject_mismatch", f"signed subject does not exactly bind {key}")
    public_key = attestation.get("public_key_pem")
    if not isinstance(public_key, str) or not _PEM.fullmatch(public_key) or "PRIVATE KEY" in public_key:
        raise AuthorityError("public_key_invalid", "public key must be canonical PEM with one PUBLIC KEY block")
    public_bytes = public_key.encode("ascii")
    actual_key_digest = digest_bytes(public_bytes)
    if subject["key_digest"] != actual_key_digest:
        raise AuthorityError("key_digest_mismatch", "signed key digest does not match the supplied public key bytes")
    snapshot = attestation.get("trust_snapshot")
    if not isinstance(snapshot, dict) or attestation.get("trust_snapshot_digest") != digest(snapshot):
        raise AuthorityError("trust_snapshot_invalid", "trust snapshot bytes are not pinned by their canonical digest")
    if not isinstance(trusted_snapshot, dict):
        raise AuthorityError(
            "trusted_root_required",
            "authority verification requires a coordinator-selected trust snapshot",
            configuration=True,
        )
    expected_trust_digest = digest(trusted_snapshot)
    if (
        trusted_snapshot_digest is not None
        and trusted_snapshot_digest != expected_trust_digest
    ):
        raise AuthorityError(
            "trusted_root_invalid",
            "coordinator trust snapshot digest does not match its exact bytes",
            configuration=True,
        )
    if snapshot != trusted_snapshot or attestation["trust_snapshot_digest"] != expected_trust_digest:
        raise AuthorityError(
            "untrusted_snapshot",
            "attestation snapshot is not the exact current coordinator trust snapshot",
        )
    trusted = _trust_key(snapshot, subject["principal_id"], subject["key_digest"])
    if trusted.get("public_key_pem") != public_key or trusted.get("key_digest") != actual_key_digest:
        raise AuthorityError("key_substitution", "supplied public key bytes differ from the pinned trust snapshot")
    roles = trusted.get("roles", [])
    if subject["role"] not in roles:
        raise AuthorityError("role_not_authorized", "principal is not trusted for the signed role")
    consumed_at = consumption_time or attestation.get("consumed_at")
    if not isinstance(consumed_at, str):
        consumed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    consumed = _time(consumed_at)
    issued = _time(subject["issued_at"])
    expires = _time(subject["expires_at"])
    if issued > consumed or consumed >= expires:
        raise AuthorityError("attestation_time_invalid", "attestation is not valid at its recorded consumption time")
    if trusted.get("valid_from") and _time(trusted["valid_from"]) > consumed:
        raise AuthorityError("key_not_yet_valid", "trusted key was not valid at consumption")
    if trusted.get("valid_until") and consumed >= _time(trusted["valid_until"]):
        raise AuthorityError("key_expired", "trusted key had expired at consumption")
    for revocation in snapshot.get("revocations", []):
        if revocation.get("key_digest") == actual_key_digest and _time(revocation["effective_at"]) <= consumed:
            raise AuthorityError("key_revoked", "trusted key was revoked at or before consumption")
    signature = _decode_signature(attestation.get("signature"))
    _verify_openssl(public_bytes, canonical_bytes(subject), signature, snapshot)
    return {
        "action": action,
        "role": subject["role"],
        "principal_id": subject["principal_id"],
        "key_digest": actual_key_digest,
        "subject_digest": digest(subject),
        "attestation_digest": digest(attestation),
        "trust_snapshot_digest": attestation["trust_snapshot_digest"],
        "trusted_snapshot": trusted_snapshot,
        "consumed_at": consumed_at,
        "attestation": attestation,
    }


def platform_result_payload_digest(result: dict[str, Any]) -> str:
    """Digest the producer-owned S13 payload without circular seal fields."""
    payload = dict(result)
    payload.pop("receipt_digest", None)
    payload.pop("producer_attestation", None)
    return digest(payload)


def platform_result_subject(
    request: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Return the exact producer-signed identity/digest portion of an S13 subject."""
    producer_principal_id = result.get("producer_principal_id")
    if not isinstance(producer_principal_id, str) or not producer_principal_id:
        raise AuthorityError(
            "producer_principal_required",
            "S13 must name the stable platform principal that signs the result",
        )
    return {
        "role": PLATFORM_RESULT_ROLE,
        "principal_id": producer_principal_id,
        "governed_ids": {
            "workspace_id": request["workspace_id"],
            "request_id": request["request_id"],
            "result_id": result["result_id"],
            "operation": request["operation"],
            "binding_identity": request["binding_identity"],
            "producer_principal_id": producer_principal_id,
            "dispatch_correlation": result["dispatch_correlation"],
        },
        "digests": {
            "result_payload_digest": platform_result_payload_digest(result),
            "request_packet_digest": digest(request),
            "binding_skill_digest": request["binding_skill_digest"],
            "adapter_contract_digest": request["adapter_contract_digest"],
            "idempotency_key": request["idempotency_key"],
        },
    }


def verify_platform_result_attestation(
    request: dict[str, Any],
    result: dict[str, Any],
    *,
    bundle: AssetBundle,
    trusted_snapshot: dict[str, Any] | None,
    trusted_snapshot_digest: str | None,
    consumption_time: str,
) -> dict[str, Any]:
    """Verify one configured platform producer over the complete S13 payload.

    The receipt digest binds the embedded attestation to the packet, while the
    Ed25519 subject binds all producer-owned bytes plus the immutable S12,
    configured Skill tree, adapter contract, idempotency key, and delivery
    correlation.  The producer may not choose its own trust root or historical
    consumption instant.
    """
    if result.get("producer_identity") != request.get("binding_identity"):
        raise AuthorityError(
            "producer_identity_mismatch",
            "S13 producer identity must equal the immutable configured binding",
        )
    producer_principal_id = result.get("producer_principal_id")
    if not isinstance(producer_principal_id, str) or not producer_principal_id:
        raise AuthorityError(
            "producer_principal_required",
            "S13 must name the stable platform principal that signs the result",
        )
    if producer_principal_id == request.get("binding_identity"):
        raise AuthorityError(
            "producer_principal_not_independent",
            "S13 producer principal must be independent from the configured Skill binding identity",
        )
    attestation = result.get("producer_attestation")
    if not isinstance(attestation, dict):
        raise AuthorityError(
            "producer_attestation_required",
            "S13 requires an Ed25519 producer attestation",
        )
    if "consumed_at" in attestation:
        raise AuthorityError(
            "producer_consumption_time_forbidden",
            "the producer may not assert the coordinator-owned consumption time",
        )
    verified = verify_attestation(
        attestation,
        action=PLATFORM_RESULT_ACTION,
        expected_subject=platform_result_subject(request, result),
        expected_revision=request["workspace_revision"],
        bundle=bundle,
        consumption_time=consumption_time,
        trusted_snapshot=trusted_snapshot,
        trusted_snapshot_digest=trusted_snapshot_digest,
    )
    return {
        **verified,
        "producer_identity": request["binding_identity"],
        "producer_principal_id": verified["principal_id"],
    }


def _required_provenance_domains(
    action: str,
    expected_subject: dict[str, Any],
) -> frozenset[str]:
    requirement = _PROVENANCE_REQUIREMENTS.get(action)
    if requirement is None:
        return frozenset()
    if "always" in requirement:
        return frozenset(requirement["always"])
    governed_ids = expected_subject.get("governed_ids")
    step = governed_ids.get("step") if isinstance(governed_ids, dict) else None
    by_step = requirement.get("by_step", {})
    return frozenset(by_step.get(step, ()))


def _expected_provenance(
    expected_subject: dict[str, Any],
    *,
    required_domains: frozenset[str],
) -> dict[str, frozenset[str]]:
    extensions = expected_subject.get("extensions")
    if (
        not isinstance(extensions, dict)
        or AUTHORITY_PROVENANCE_EXTENSION not in extensions
    ):
        raise AuthorityError(
            "authority_provenance_required",
            "authority action requires coordinator-derived authorship provenance in the signed subject",
        )
    return _parse_provenance_value(
        extensions[AUTHORITY_PROVENANCE_EXTENSION],
        required_domains=required_domains,
    )


def _execute_authority_constraints(
    constraints: list[str],
    *,
    action: str,
    expected_subject: dict[str, Any],
    verified: list[dict[str, Any]],
) -> None:
    required_domains = _required_provenance_domains(action, expected_subject)
    authors_by_domain = (
        _expected_provenance(
            expected_subject, required_domains=required_domains,
        )
        if required_domains else {}
    )
    principals = [item["principal_id"] for item in verified]
    for constraint in constraints:
        # Lifecycle-owned constraints have already been evaluated from the
        # authenticated transition state by the coordinator.  They remain in
        # this closed dispatcher so a new policy spelling can never disappear
        # into an implicit/default branch.
        if constraint in _COORDINATOR_CONSTRAINTS:
            continue
        if constraint == "distinct-principal-ids":
            if len(principals) != len(set(principals)):
                raise AuthorityError(
                    "authority_not_independent",
                    "authority constraint distinct-principal-ids requires distinct stable principal IDs",
                )
            continue
        if constraint == "principal-not-experiment-author":
            if "experiment" not in required_domains:
                continue
            authors = authors_by_domain["experiment"]
            if any(principal in authors for principal in principals):
                raise AuthorityError(
                    "principal_is_experiment_author",
                    "signed authority principal is an author of the bound Experiment closure",
                )
            continue
        if constraint == "reviewer-not-experiment-author":
            if "experiment" not in required_domains:
                continue
            authors = authors_by_domain["experiment"]
            if any(
                item["role"] == "independent-protocol-reviewer"
                and item["principal_id"] in authors
                for item in verified
            ):
                raise AuthorityError(
                    "reviewer_is_experiment_author",
                    "signed independent reviewer principal is an author of the bound Experiment closure",
                )
            continue
        if constraint in {
            "principal-not-implementation-author",
            "signer-not-implementation-author",
        }:
            authors = authors_by_domain.get("implementation", frozenset())
            if any(principal in authors for principal in principals):
                raise AuthorityError(
                    "principal_is_implementation_author",
                    "signed authority principal is an author of the bound implementation closure",
                )
            continue
        if constraint == "principal-not-sole-evidence-author":
            authors = authors_by_domain.get("evidence", frozenset())
            if any(authors == {principal} for principal in principals):
                raise AuthorityError(
                    "principal_is_sole_evidence_author",
                    "signed scientific reviewer principal is the sole author of the bound evidence closure",
                )
            continue
        if constraint == "signer-not-data-split-approver":
            authors = authors_by_domain.get(
                "data-split-approver", frozenset(),
            )
            if any(principal in authors for principal in principals):
                raise AuthorityError(
                    "signer_is_data_split_approver",
                    "signed result approver principal is an approver of the bound data split",
                )
            continue
        if constraint == \
                "evaluator-owner-disjoint-from-implementation-authors":
            evaluator_owners = authors_by_domain.get(
                "evaluator-owner", frozenset(),
            )
            implementation_authors = authors_by_domain.get(
                "implementation", frozenset(),
            )
            if evaluator_owners & implementation_authors:
                raise AuthorityError(
                    "evaluator_owner_is_implementation_author",
                    "bound evaluator-owner and implementation author principal sets overlap",
                )
            continue
        # _validate_policy makes this unreachable for pinned policy bytes, but
        # keep an explicit terminal branch for defense in depth.
        raise AuthorityError(
            "authority_constraint_unknown",
            f"authority constraint has no executable handler: {constraint}",
            configuration=True,
        )


def verify_authorities(
    attestations: Iterable[dict[str, Any]],
    *,
    action: str,
    expected_subject: dict[str, Any],
    expected_revision: int,
    bundle: AssetBundle,
    consumption_time: str | None = None,
    trusted_snapshot: dict[str, Any] | None = None,
    trusted_snapshot_digest: str | None = None,
) -> list[dict[str, Any]]:
    policy = _policy(bundle, action)
    verified = [
        verify_attestation(
            item,
            action=action,
            expected_subject=expected_subject,
            expected_revision=expected_revision,
            bundle=bundle,
            consumption_time=consumption_time,
            trusted_snapshot=trusted_snapshot,
            trusted_snapshot_digest=trusted_snapshot_digest,
        )
        for item in attestations
    ]
    required_roles = policy.get("roles", policy.get("required_roles", []))
    for role in required_roles:
        if not any(item["role"] == role for item in verified):
            raise AuthorityError("required_role_missing", f"authority gate requires role: {role}")
    minimum = policy.get("minimum", len(required_roles) or 1)
    if len(verified) < minimum:
        raise AuthorityError("authority_count_insufficient", f"authority gate requires at least {minimum} attestations")
    principals = [item["principal_id"] for item in verified]
    if policy.get("distinct_principals", minimum > 1) and len(principals) != len(set(principals)):
        raise AuthorityError("authority_not_independent", "two-authority gate requires distinct stable principal IDs")
    key_digests = [item["key_digest"] for item in verified]
    if minimum > 1 and len(key_digests) != len(set(key_digests)):
        raise AuthorityError("authority_not_independent", "two-authority gate requires distinct public-key digests")
    prohibited_pairs = policy.get("prohibited_role_pairs", [])
    by_principal: dict[str, set[str]] = {}
    for item in verified:
        by_principal.setdefault(item["principal_id"], set()).add(item["role"])
    for roles in by_principal.values():
        for left, right in prohibited_pairs:
            if left in roles and right in roles:
                raise AuthorityError("role_conflict", f"one principal may not satisfy both {left} and {right}")
    _execute_authority_constraints(
        policy["constraints"],
        action=action,
        expected_subject=expected_subject,
        verified=verified,
    )
    return verified


def replay_authorities(event: dict[str, Any], bundle: AssetBundle) -> list[dict[str, Any]]:
    records = event.get("authority", [])
    if not records:
        return []
    action = event.get("payload", {}).get("authority_action")
    subject = event.get("payload", {}).get("authority_subject", {})
    consumed_at = event.get("payload", {}).get("consumed_at")
    expected_revision = event.get("payload", {}).get(
        "authority_expected_revision",
    )
    if (
        not isinstance(action, str) or not action
        or not isinstance(subject, dict) or not subject
        or not isinstance(consumed_at, str)
        or not isinstance(expected_revision, int)
        or isinstance(expected_revision, bool)
        or expected_revision < 0
    ):
        raise AuthorityError(
            "historical_authority_binding_missing",
            "authority event lacks exact action, subject, consumption time, or signed revision boundary",
        )
    attestations = [record.get("attestation", record) for record in records]
    snapshots = [record.get("trusted_snapshot") for record in records]
    if (
        not snapshots or not isinstance(snapshots[0], dict)
        or any(snapshot != snapshots[0] for snapshot in snapshots)
    ):
        raise AuthorityError(
            "historical_trust_snapshot_missing",
            "authority event lacks one exact consumption-time trust snapshot",
        )
    verified = verify_authorities(
        attestations,
        action=action,
        expected_subject=subject,
        expected_revision=expected_revision,
        bundle=bundle,
        consumption_time=consumed_at,
        trusted_snapshot=snapshots[0],
        trusted_snapshot_digest=digest(snapshots[0]),
    )
    if canonical_bytes(verified) != canonical_bytes(records):
        raise AuthorityError(
            "historical_authority_metadata_mismatch",
            "stored authority metadata differs from deterministic signature verification",
        )
    return verified


def _sensitive_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _safe_redaction_digest(value: Any) -> str:
    try:
        return digest(value)
    except Exception:
        return digest_bytes(type(value).__name__.encode("utf-8"))


def _sanitize_uri(value: str) -> str:
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value) is None:
        return value
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parts.port}" if parts.port is not None else ""
        netloc = host + port
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    except (ValueError, UnicodeError):
        return "[redacted-uri]"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _sensitive_key(str(key)):
                result[key] = {"redacted": True, "digest": _safe_redaction_digest(item)}
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _sanitize_uri(value)
    return value


def _diagnostic_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect the SDM authority library")
    parser.parse_args(argv)
    sys.stdout.write(json.dumps({"status": "pass", "command": "authority.help"}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(_diagnostic_main())
