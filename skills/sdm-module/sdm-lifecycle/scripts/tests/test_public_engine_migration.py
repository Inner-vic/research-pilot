from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from canonical import canonical_bytes, digest, digest_bytes
from contracts import AssetBundle
from ledger import LedgerError, event_hash, replay
from producer_signing import verifier_record
from specctl import _trusted_snapshot_from_root, main
from workspace import (
    WorkspaceError, create_workspace, mutate, resume_workspace,
    validate_workspace,
)


def _keypair(root: Path, name: str) -> tuple[Path, str]:
    private = root / f"{name}.private.pem"
    public = root / f"{name}.public.pem"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)],
        check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)],
        check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return private, public.read_text(encoding="ascii")


def _sign(
    root: Path,
    *,
    private: Path,
    public: str,
    snapshot: dict[str, Any],
    role: str,
    principal: str,
    revision: int,
    subject: dict[str, Any],
) -> dict[str, Any]:
    signed = {
        "action": "engine.migrate",
        "role": role,
        "principal_id": principal,
        "key_digest": digest_bytes(public.encode("ascii")),
        "issued_at": "2025-01-01T00:00:00Z",
        "expires_at": "2030-01-01T00:00:00Z",
        "nonce": f"migration-{role}-nonce",
        "expected_revision": revision,
        **subject,
    }
    subject_path = root / f"{role}.subject.json"
    signature_path = root / f"{role}.signature"
    subject_path.write_bytes(canonical_bytes(signed))
    subprocess.run(
        [
            "openssl", "pkeyutl", "-sign", "-inkey", str(private),
            "-rawin", "-in", str(subject_path), "-out", str(signature_path),
        ],
        check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return {
        "schema": "sdm-authority-attestation/v1",
        "subject": signed,
        "signature": base64.b64encode(signature_path.read_bytes()).decode(
            "ascii"
        ).rstrip("="),
        "public_key_pem": public,
        "trust_snapshot": snapshot,
        "trust_snapshot_digest": digest(snapshot),
    }


def _successor_bundle(asset_bundle: AssetBundle, root: Path) -> AssetBundle:
    target = root / "successor-assets"
    target.mkdir()
    for relative, data in asset_bundle.canonical_assets.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    path = target / "event-types.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["schema_version"] = "1.0.1"
    path.write_bytes(canonical_bytes(value))
    return AssetBundle.load(target)


def _workspace_with_migration_authority(
    asset_bundle: AssetBundle, root: Path,
) -> tuple[Path, dict[str, tuple[Path, str]], dict[str, Any]]:
    trust_root = root / "trust"
    trust_root.mkdir()
    keys = {
        "governance-authority": _keypair(root, "governance"),
        "control-plane-owner": _keypair(root, "control-plane"),
    }
    snapshot = {
        "schema": "sdm-trust-snapshot/v1",
        "verifier": verifier_record(),
        "keys": [
            {
                "principal_id": f"{role}-principal",
                "key_digest": digest_bytes(public.encode("ascii")),
                "public_key_pem": public,
                "roles": [role],
                "valid_from": "2024-01-01T00:00:00Z",
                "valid_until": "2031-01-01T00:00:00Z",
            }
            for role, (_, public) in keys.items()
        ],
        "revocations": [],
    }
    (trust_root / "trust-snapshot.json").write_bytes(canonical_bytes(snapshot))
    trusted, trusted_digest, identity = _trusted_snapshot_from_root(trust_root)
    workspace = root / "workspace"
    create_workspace(
        workspace,
        {
            "workspace_id": "migration-workspace",
            "intent": "prove immutable engine migration",
            "constraints": [],
            "non_goals": [],
            "protocol": "exploratory",
            "authority_trust_root": str(trust_root),
            "authority_trust_root_identity": identity,
            "authority_trust_snapshot": trusted,
            "authority_trust_snapshot_digest": trusted_digest,
        },
        expected_revision=0,
        operation_id="create-migration-workspace",
        assets_root=asset_bundle.root,
    )
    return workspace, keys, snapshot


def _migration_payload(
    workspace: Path,
    successor: AssetBundle,
    keys: dict[str, tuple[Path, str]],
    snapshot: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    current = replay(workspace)
    subject = {
        "governed_ids": {
            "workspace_id": current.genesis["workspace_id"],
            "migration_sequence": str(current.revision + 1),
        },
        "digests": {
            "old_engine_contract_digest": current.active_engine_digest,
            "new_engine_contract_digest": successor.engine_contract_digest,
            "migration_boundary_head": current.head_hash,
            "new_asset_inventory_digest": digest(successor.asset_digests),
        },
    }
    return {
        "new_engine_assets": str(successor.root),
        "new_engine_contract_digest": successor.engine_contract_digest,
        "authority_subject": subject,
        "attestations": [
            _sign(
                root,
                private=private,
                public=public,
                snapshot=snapshot,
                role=role,
                principal=f"{role}-principal",
                revision=current.revision,
                subject=subject,
            )
            for role, (private, public) in keys.items()
        ],
    }


def _write_payload(root: Path, value: dict[str, Any], name: str) -> Path:
    path = root / name
    path.write_bytes(canonical_bytes(value))
    return path


def test_public_engine_migration_is_signed_atomic_and_idempotent(
    asset_bundle: AssetBundle, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, keys, snapshot = _workspace_with_migration_authority(
        asset_bundle, tmp_path,
    )
    successor = _successor_bundle(asset_bundle, tmp_path)
    before = replay(workspace)
    payload = _migration_payload(
        workspace, successor, keys, snapshot, tmp_path,
    )
    payload_path = _write_payload(tmp_path, payload, "migration.json")

    argv = [
        "lifecycle", "migrate", "--workspace", str(workspace),
        "--payload", str(payload_path), "--expected-revision",
        str(before.revision), "--operation-id", "migrate-engine-v2",
    ]
    assert main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "pass"
    migrated = replay(workspace)
    assert migrated.revision == before.revision + 1
    assert migrated.active_engine_digest == successor.engine_contract_digest
    assert set(migrated.bundles) == {
        before.active_engine_digest, successor.engine_contract_digest,
    }
    migration_event = migrated.events[-1]
    assert migration_event["engine_contract_digest"] == before.active_engine_digest
    assert migration_event["payload"]["migration_boundary_head"] == before.head_hash

    # The exact same operation is a replay-only success even though the
    # original signed revision is now stale under the successor engine.
    assert main(argv) == 0
    retried = json.loads(capsys.readouterr().out)
    assert retried["result"]["idempotent"] is True
    assert replay(workspace).revision == migrated.revision

    appended = mutate(
        workspace,
        expected_revision=migrated.revision,
        operation_id="after-engine-migration",
        event_type="substate.recorded",
        subjects={"workspace_id": "migration-workspace"},
        payload={"name": "after-engine-migration", "value": True},
    )
    assert appended.event["engine_contract_digest"] == successor.engine_contract_digest
    assert canonical_bytes(migrated.events[0]) == canonical_bytes(
        replay(workspace).events[0]
    )


def test_engine_migration_rejects_unsigned_and_tampered_boundaries(
    asset_bundle: AssetBundle, tmp_path: Path,
) -> None:
    workspace, keys, snapshot = _workspace_with_migration_authority(
        asset_bundle, tmp_path,
    )
    successor = _successor_bundle(asset_bundle, tmp_path)
    base = replay(workspace)

    with pytest.raises(WorkspaceError) as unsigned:
        mutate(
            workspace,
            expected_revision=base.revision,
            operation_id="unsigned-migration",
            event_type="engine.migrated",
            subjects={"workspace_id": base.genesis["workspace_id"]},
            payload={
                "transition": "engine.migrate",
                "authority_action": "engine.migrate",
                "new_engine_contract_digest": successor.engine_contract_digest,
            },
        )
    assert unsigned.value.code == "authority_required"
    assert replay(workspace).revision == base.revision

    payload = _migration_payload(
        workspace, successor, keys, snapshot, tmp_path,
    )
    path = _write_payload(tmp_path, payload, "valid-migration.json")
    assert main([
        "lifecycle", "migrate", "--workspace", str(workspace),
        "--payload", str(path), "--expected-revision", str(base.revision),
        "--operation-id", "valid-migration",
    ]) == 0
    migrated = replay(workspace)
    assert validate_workspace(workspace)["valid"] is True
    event_path = workspace / "_ledger" / "events" / f"{migrated.revision:020d}.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["payload"]["new_asset_inventory_digest"] = digest({"tampered": True})
    unsigned_event = dict(event)
    unsigned_event.pop("event_hash")
    event["event_hash"] = event_hash(event["previous_hash"], unsigned_event)
    event_path.write_bytes(canonical_bytes(event))
    with pytest.raises(LedgerError) as tampered:
        replay(workspace)
    assert tampered.value.code == "migration_binding_invalid"
    checked = validate_workspace(workspace)
    assert checked["valid"] is False
    assert any(
        item["code"] == "migration_binding_invalid"
        for item in checked["findings"]
    )
    with pytest.raises((WorkspaceError, LedgerError)) as resume_error:
        resume_workspace(workspace)
    assert resume_error.value.code == "migration_binding_invalid"
