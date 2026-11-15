#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Governed SDM workspace creation, mutation, replay, and read-only audit."""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import re
import resource
import stat
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from authority import AuthorityError, verify_platform_result_attestation
from canonical import (
    MAX_DOCUMENT_BYTES, CanonicalError, candidate_digest, canonical_bytes,
    digest, digest_bytes, parse_document,
)
from contracts import (
    ASSET_FILES,
    SCHEMA_FILES,
    AssetBundle,
    ContractError,
    _owned_schema_bindings,
    require_supported_engine,
    evaluate_scientific_decision,
    finding,
    sort_findings,
    validate_baseline_replacements,
    validate_schema,
)
from journal import (
    JournalError, ReadCapability, Transaction, WorkspaceLock,
)
from ledger import (
    LedgerError, ReplayResult, _validate_event_authority,
    _validate_migration_payload, build_event, event_hash, genesis_document,
    genesis_hash, load_genesis, plan_append, replay, replay_with, utc_now,
)
from projection import (
    ProjectionError, admissible_actions, materialize, validate_graph,
    verify_stored_projection,
)


_PORTABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ARTIFACT_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class WorkspaceError(RuntimeError):
    def __init__(self, code: str, message: str, *, exit_code: int = 2, findings: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.findings = findings or []


@dataclass(frozen=True)
class MutationResult:
    revision: int
    event: dict[str, Any]
    state: dict[str, Any]
    idempotent: bool = False




_WORKSPACE_DIR_FLAGS = (
    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_WORKSPACE_FILE_CREATE_FLAGS = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL
    | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)


def _workspace_path(value: str | os.PathLike[str]) -> Path:
    path = Path(os.path.abspath(os.fspath(value)))
    if path == Path(os.sep):
        raise WorkspaceError(
            "unsafe_workspace", "filesystem root cannot be a governed workspace",
        )
    return path


def _open_absolute_directory(path: Path, *, code: str) -> int:
    """Open an absolute directory one authenticated, no-follow component at a time."""

    absolute = Path(os.path.abspath(os.fspath(path)))
    descriptor = os.open(os.sep, _WORKSPACE_DIR_FLAGS)
    try:
        for component in absolute.parts[1:]:
            try:
                named = os.stat(
                    component, dir_fd=descriptor, follow_symlinks=False,
                )
                child = os.open(
                    component, _WORKSPACE_DIR_FLAGS, dir_fd=descriptor,
                )
            except OSError as exc:
                raise WorkspaceError(
                    code, f"directory component is unavailable or unsafe: {absolute}",
                    exit_code=4,
                ) from exc
            opened = os.fstat(child)
            if (
                stat.S_ISLNK(named.st_mode)
                or not stat.S_ISDIR(named.st_mode)
                or (stat.S_IFMT(named.st_mode), named.st_dev, named.st_ino)
                != (stat.S_IFMT(opened.st_mode), opened.st_dev, opened.st_ino)
            ):
                os.close(child)
                raise WorkspaceError(
                    code, f"directory component changed while opening: {absolute}",
                    exit_code=4,
                )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@dataclass
class _BootstrapRoot:
    """Retained parent/root authority for one not-yet-locked workspace."""

    path: Path
    parent_fd: int
    root_fd: int
    parent_identity: tuple[int, int, int]
    parent_mount: str
    root_identity: tuple[int, int, int]
    root_mount: str

    def authenticate(self) -> None:
        parent = os.fstat(self.parent_fd)
        root = os.fstat(self.root_fd)
        if (
            (stat.S_IFMT(parent.st_mode), parent.st_dev, parent.st_ino)
            != self.parent_identity
            or _mount_identity(self.parent_fd) != self.parent_mount
            or (stat.S_IFMT(root.st_mode), root.st_dev, root.st_ino)
            != self.root_identity
            or _mount_identity(self.root_fd) != self.root_mount
        ):
            raise WorkspaceError(
                "workspace_rebound",
                "retained workspace parent or root changed identity",
                exit_code=4,
            )
        try:
            named = os.stat(
                self.path.name, dir_fd=self.parent_fd, follow_symlinks=False,
            )
            named_fd = os.open(
                self.path.name, _WORKSPACE_DIR_FLAGS, dir_fd=self.parent_fd,
            )
        except OSError as exc:
            raise WorkspaceError(
                "workspace_rebound", "new workspace name changed during bootstrap",
                exit_code=4,
            ) from exc
        try:
            opened_named = os.fstat(named_fd)
            if (
                stat.S_ISLNK(named.st_mode)
                or not stat.S_ISDIR(named.st_mode)
                or (stat.S_IFMT(named.st_mode), named.st_dev, named.st_ino)
                != self.root_identity
                or (stat.S_IFMT(opened_named.st_mode),
                    opened_named.st_dev, opened_named.st_ino)
                != self.root_identity
                or _mount_identity(named_fd) != self.root_mount
            ):
                raise WorkspaceError(
                    "workspace_rebound", "new workspace name changed identity",
                    exit_code=4,
                )
        finally:
            os.close(named_fd)
        live_fd = _open_absolute_directory(
            self.path, code="workspace_rebound",
        )
        try:
            live = os.fstat(live_fd)
            if (
                (stat.S_IFMT(live.st_mode), live.st_dev, live.st_ino)
                != self.root_identity
                or _mount_identity(live_fd) != self.root_mount
            ):
                raise WorkspaceError(
                    "workspace_rebound",
                    "workspace live name no longer identifies the retained root",
                    exit_code=4,
                )
        finally:
            os.close(live_fd)

    def close(self) -> None:
        for descriptor in (self.root_fd, self.parent_fd):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        self.root_fd = -1
        self.parent_fd = -1


def _mkdir_new_workspace(path: Path) -> _BootstrapRoot:
    absolute = _workspace_path(path)
    parent_fd = _open_absolute_directory(
        absolute.parent, code="unsafe_workspace_parent",
    )
    root_fd = -1
    try:
        parent_info = os.fstat(parent_fd)
        parent_mount = _mount_identity(parent_fd)
        try:
            os.stat(absolute.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise WorkspaceError(
                "workspace_exists", f"workspace already exists: {absolute}",
                exit_code=4,
            )
        try:
            os.mkdir(absolute.name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
            named = os.stat(
                absolute.name, dir_fd=parent_fd, follow_symlinks=False,
            )
            root_fd = os.open(
                absolute.name, _WORKSPACE_DIR_FLAGS, dir_fd=parent_fd,
            )
        except FileExistsError as exc:
            raise WorkspaceError(
                "workspace_exists", f"workspace already exists: {absolute}",
                exit_code=4,
            ) from exc
        except OSError as exc:
            raise WorkspaceError(
                "unsafe_workspace_parent",
                "workspace leaf cannot be created safely through its retained parent",
                exit_code=4,
            ) from exc
        opened = os.fstat(root_fd)
        root_mount = _mount_identity(root_fd)
        identity = (stat.S_IFMT(opened.st_mode), opened.st_dev, opened.st_ino)
        if (
            stat.S_ISLNK(named.st_mode)
            or not stat.S_ISDIR(named.st_mode)
            or (stat.S_IFMT(named.st_mode), named.st_dev, named.st_ino)
            != identity
            or opened.st_dev != parent_info.st_dev
            or root_mount != parent_mount
        ):
            raise WorkspaceError(
                "workspace_rebound",
                "new workspace changed identity or mount while opening",
                exit_code=4,
            )
        retained = _BootstrapRoot(
            absolute,
            parent_fd,
            root_fd,
            (stat.S_IFMT(parent_info.st_mode), parent_info.st_dev,
             parent_info.st_ino),
            parent_mount,
            identity,
            root_mount,
        )
        retained.authenticate()
        return retained
    except BaseException:
        if root_fd >= 0:
            os.close(root_fd)
        os.close(parent_fd)
        raise


def _bootstrap_open_directory(
    bootstrap: _BootstrapRoot, relative: str, *, create: bool,
) -> int:
    descriptor = os.dup(bootstrap.root_fd)
    traversed: list[str] = []
    try:
        for component in PurePosixPath(relative).parts:
            if component in {"", ".", ".."}:
                raise WorkspaceError(
                    "unsafe_path", f"unsafe bootstrap directory: {relative}",
                )
            traversed.append(component)
            try:
                named = os.stat(
                    component, dir_fd=descriptor, follow_symlinks=False,
                )
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, 0o700, dir_fd=descriptor)
                os.fsync(descriptor)
                named = os.stat(
                    component, dir_fd=descriptor, follow_symlinks=False,
                )
            child = os.open(
                component, _WORKSPACE_DIR_FLAGS, dir_fd=descriptor,
            )
            opened = os.fstat(child)
            if (
                stat.S_ISLNK(named.st_mode)
                or not stat.S_ISDIR(named.st_mode)
                or (stat.S_IFMT(named.st_mode), named.st_dev, named.st_ino)
                != (stat.S_IFMT(opened.st_mode), opened.st_dev, opened.st_ino)
                or opened.st_dev != bootstrap.root_identity[1]
                or _mount_identity(child) != bootstrap.root_mount
            ):
                os.close(child)
                raise WorkspaceError(
                    "workspace_rebound",
                    "bootstrap directory changed identity or mount: "
                    + "/".join(traversed),
                    exit_code=4,
                )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _bootstrap_directories(bootstrap: _BootstrapRoot, bundle: AssetBundle) -> None:
    artifact_ids = [
        row["id"]
        for asset in ("artifact-catalog.json", "supporting-artifact-catalog.json")
        for row in bundle.document(asset)["artifacts"]
    ]
    directories = [
        "_ledger", "_ledger/events", "_engine", "artifacts", "packets", "packets/S12", "packets/S13", "packets/S13-late", "_memlog",
    ]
    directories.extend(f"artifacts/{artifact_id}" for artifact_id in artifact_ids)
    for relative in directories:
        descriptor = _bootstrap_open_directory(
            bootstrap, relative, create=True,
        )
        os.close(descriptor)
    fd = os.open(
        "_lock", _WORKSPACE_FILE_CREATE_FLAGS, 0o600,
        dir_fd=bootstrap.root_fd,
    )
    try:
        info = os.fstat(fd)
        named = os.stat(
            "_lock", dir_fd=bootstrap.root_fd, follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or (stat.S_IFMT(info.st_mode), info.st_dev, info.st_ino)
            != (stat.S_IFMT(named.st_mode), named.st_dev, named.st_ino)
            or info.st_dev != bootstrap.root_identity[1]
            or _mount_identity(fd) != bootstrap.root_mount
        ):
            raise WorkspaceError(
                "workspace_rebound", "bootstrap lock changed identity or mount",
                exit_code=4,
            )
        os.fsync(fd)
        os.fsync(bootstrap.root_fd)
    finally:
        os.close(fd)
    bootstrap.authenticate()


def _read_capability_for_lock(
    lock: WorkspaceLock, root: Path,
) -> ReadCapability:
    """Bind a governed reader to the exact root retained by the active lock."""

    retained_fd = lock.duplicate_root(root)
    try:
        return ReadCapability(root, root_fd=retained_fd)
    finally:
        os.close(retained_fd)


def _verify_and_close_reader(reader: ReadCapability) -> None:
    try:
        reader.verify()
    finally:
        reader.close()


def _authenticate_mutation_exit(lock: WorkspaceLock, root: Path) -> None:
    """Reauthenticate the retained root immediately before a successful exit."""

    with _read_capability_for_lock(lock, root):
        pass


_JOURNAL_CONFLICT_CODES = {
    "lock_conflict", "cas_conflict", "target_exists", "recovery_conflict",
    "concurrent_mutation", "workspace_rebound", "lock_rebound",
    "lock_root_mismatch", "mount_escape", "mount_identity_unavailable",
}
_PROTECTED_READ_CONFLICT_CODES = _JOURNAL_CONFLICT_CODES | {
    "ledger_concurrent_mutation", "mount_escape",
    "mount_identity_unavailable",
}


def _journal_exit_code(code: str) -> int:
    return 4 if code in _JOURNAL_CONFLICT_CODES else 2


def _protected_read_exit_code(code: str) -> int:
    return 4 if code in _PROTECTED_READ_CONFLICT_CODES else 2








def create_workspace(
    path: str | os.PathLike[str],
    payload: dict[str, Any],
    *,
    expected_revision: int,
    operation_id: str,
    assets_root: str | os.PathLike[str] | None = None,
) -> MutationResult:
    if expected_revision != 0:
        raise WorkspaceError("cas_conflict", "a new workspace requires expected revision 0", exit_code=4)
    if not _PORTABLE_ID.fullmatch(operation_id):
        raise WorkspaceError("invalid_operation_id", "operation ID is not portable")
    workspace_id = payload.get("workspace_id")
    if not isinstance(workspace_id, str) or not _PORTABLE_ID.fullmatch(workspace_id):
        raise WorkspaceError("invalid_workspace_id", "create payload requires a portable workspace_id")
    protocol = payload.get("protocol", "exploratory")
    if protocol not in {"exploratory", "formal", "regression", "promotion"}:
        raise WorkspaceError("invalid_protocol", "protocol must be exploratory, formal, regression, or promotion")
    retired_configuration = [
        key for key in payload
        if key == "promotion_eligible"
        or key.startswith(("promotion_adapter", "promotion_state_root"))
    ]
    if retired_configuration:
        raise WorkspaceError("retired_configuration", "release configuration is no longer accepted")
    binding_skill_digests = payload.get("binding_skill_digests", {})
    if not isinstance(binding_skill_digests, dict) or any(
        not isinstance(key, str)
        or not isinstance(value, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", value)
        for key, value in binding_skill_digests.items()
    ):
        raise WorkspaceError(
            "binding_skill_digests_invalid",
            "binding_skill_digests must map binding keys to exact sha256 digests",
        )
    authority_trust_root = payload.get("authority_trust_root")
    authority_trust_root_identity = payload.get("authority_trust_root_identity")
    authority_trust_snapshot = payload.get("authority_trust_snapshot")
    authority_trust_snapshot_digest = payload.get("authority_trust_snapshot_digest")
    authority_values = (
        authority_trust_root,
        authority_trust_root_identity,
        authority_trust_snapshot,
        authority_trust_snapshot_digest,
    )
    if any(value is not None for value in authority_values):
        if not all(value is not None for value in authority_values):
            raise WorkspaceError(
                "authority_trust_config_incomplete",
                "authority trust root identity and snapshot must be pinned together",
            )
        if (
            not isinstance(authority_trust_root, str)
            or not isinstance(authority_trust_root_identity, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", authority_trust_root_identity)
            or not isinstance(authority_trust_snapshot, dict)
            or not isinstance(authority_trust_snapshot_digest, str)
            or authority_trust_snapshot_digest != digest(authority_trust_snapshot)
        ):
            raise WorkspaceError(
                "authority_trust_config_invalid",
                "authority trust configuration is malformed or not canonically hash-bound",
            )
        trust_path = Path(authority_trust_root)
        if not trust_path.is_absolute() or any(part in {"", ".", ".."} for part in trust_path.parts[1:]):
            raise WorkspaceError(
                "authority_trust_config_invalid",
                "authority trust root must be an absolute normalized path",
            )
    lifecycle_skill_root = payload.get("lifecycle_skill_root")
    lifecycle_skill_digest = payload.get("lifecycle_skill_digest")
    if (lifecycle_skill_root is None) != (lifecycle_skill_digest is None):
        raise WorkspaceError(
            "lifecycle_runtime_config_incomplete",
            "lifecycle Skill root and tree digest must be pinned together",
        )
    if lifecycle_skill_root is not None:
        if (
            not isinstance(lifecycle_skill_root, str)
            or not isinstance(lifecycle_skill_digest, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", lifecycle_skill_digest)
        ):
            raise WorkspaceError(
                "lifecycle_runtime_config_invalid",
                "lifecycle Skill root or tree digest is malformed",
            )
        lifecycle_path = Path(lifecycle_skill_root)
        if not lifecycle_path.is_absolute() or any(part in {"", ".", ".."} for part in lifecycle_path.parts[1:]):
            raise WorkspaceError(
                "lifecycle_runtime_config_invalid",
                "lifecycle Skill root must be an absolute normalized path",
            )
    bootstrap: _BootstrapRoot | None = None
    try:
        bundle = AssetBundle.load(assets_root)
        bootstrap = _mkdir_new_workspace(Path(path))
        root = bootstrap.path
        _bootstrap_directories(bootstrap, bundle)
        created_at = utc_now()
        genesis = genesis_document(workspace_id, created_at, bundle)
        base = ReplayResult(genesis, (), 0, genesis_hash(genesis), bundle.engine_contract_digest, {bundle.engine_contract_digest: bundle})
        event = build_event(
            sequence=1,
            event_type="workspace.created",
            previous_hash=base.head_hash,
            engine_contract_digest=bundle.engine_contract_digest,
            actor={"type": "coordinator", "id": "sdm-lifecycle"},
            subjects={"workspace_id": workspace_id},
            payload={
                "intent": payload.get("intent", ""),
                "constraints": payload.get("constraints", []),
                "non_goals": payload.get("non_goals", []),
                "impact": payload.get("impact", ""),
                "owner": payload.get("owner", ""),
                "jira": payload.get("jira", ""),
                "hypothesis": payload.get("hypothesis", ""),
                "mechanism": payload.get("mechanism", ""),
                "uncertainty": payload.get("uncertainty", ""),
                "planning_refs": payload.get("planning_refs", []),
                "protocol": protocol,
                "bindings": payload.get("bindings", {}),
                "binding_skill_digests": binding_skill_digests,
                "authority_trust_root": authority_trust_root,
                "authority_trust_root_identity": authority_trust_root_identity,
                "authority_trust_snapshot": authority_trust_snapshot,
                "authority_trust_snapshot_digest": authority_trust_snapshot_digest,
                "lifecycle_skill_root": lifecycle_skill_root,
                "lifecycle_skill_digest": lifecycle_skill_digest,
            },
            operation_id=operation_id,
            occurred_at=created_at,
        )
        extended = ReplayResult(genesis, (event,), 1, event["event_hash"], bundle.engine_contract_digest, {bundle.engine_contract_digest: bundle})
        state = materialize(extended)
        transaction = Transaction(root, operation_id)
        engine_prefix = f"_engine/{bundle.engine_contract_digest}"
        engine_fd = _bootstrap_open_directory(
            bootstrap, engine_prefix, create=True,
        )
        os.close(engine_fd)
        schemas_fd = _bootstrap_open_directory(
            bootstrap, f"{engine_prefix}/schemas", create=True,
        )
        os.close(schemas_fd)
        for relative, data in bundle.canonical_assets.items():
            transaction.add(f"{engine_prefix}/{relative}", data, exclusive=True)
        transaction.add("_ledger/genesis.json", genesis, exclusive=True)
        transaction.add("_ledger/events/00000000000000000001.json", event, exclusive=True)
        transaction.add("state.json", state, exclusive=True)
        transaction.add("_memlog/00000000000000000001.json", _memlog(event), exclusive=True)
        with WorkspaceLock(root, exclusive=True) as lock:
            bootstrap.authenticate()
            retained_fd = lock.duplicate_root(root)
            try:
                retained = os.fstat(retained_fd)
                if (
                    (stat.S_IFMT(retained.st_mode), retained.st_dev,
                     retained.st_ino) != bootstrap.root_identity
                    or _mount_identity(retained_fd) != bootstrap.root_mount
                ):
                    raise WorkspaceError(
                        "workspace_rebound",
                        "workspace lock does not retain the bootstrapped root",
                        exit_code=4,
                    )
            finally:
                os.close(retained_fd)
            transaction.commit(lock=lock)
            _authenticate_mutation_exit(lock, root)
        return MutationResult(1, event, state)
    except Exception:
        # A failed bootstrap is intentionally left recoverable when a journal
        # exists; otherwise remove only the empty files/directories we created
        # would be destructive and obscure evidence, so callers get the path.
        raise
    finally:
        if bootstrap is not None:
            bootstrap.close()


def _memlog(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "sdm-memlog/v1",
        "sequence": event["sequence"],
        "event_hash": event["event_hash"],
        "event_type": event["event_type"],
        "operation_id": event["operation_id"],
        "subjects": event["subjects"],
    }


def _enforce_typed_graph(state: dict[str, Any]) -> None:
    """Reject a replay-derived graph finding before a public mutation commits."""

    graph_findings = validate_graph(state.get("lineage"))
    if graph_findings:
        raise WorkspaceError(
            graph_findings[0]["code"], graph_findings[0]["message"],
            exit_code=3, findings=graph_findings,
        )


def mutate(
    workspace: str | os.PathLike[str],
    *,
    expected_revision: int,
    operation_id: str,
    event_type: str,
    subjects: dict[str, Any],
    payload: dict[str, Any],
    actor: dict[str, Any] | None = None,
    authority: list[dict[str, Any]] | None = None,
    artifact_writes: Iterable[tuple[str, bytes | dict[str, Any], bool]] = (),
    enforce_graph: bool = False,
    _locked_precondition: Callable[
        [ReadCapability, ReplayResult, dict[str, Any]], None
    ] | None = None,
) -> MutationResult:
    root = _workspace_path(workspace)
    try:
        with WorkspaceLock(root, exclusive=True) as lock:
            from journal import recover

            recover(root, lock=lock)
            reader: ReadCapability | None = _read_capability_for_lock(lock, root)
            try:
                base = replay(root, capability=reader)
                state_before = materialize(base)
                if enforce_graph:
                    _enforce_typed_graph(state_before)
                if _locked_precondition is not None:
                    _locked_precondition(reader, base, state_before)
                existing = state_before["operations"].get(operation_id)
                if existing:
                    prior = next(item for item in base.events if item["event_hash"] == existing["event_hash"])
                    comparable = {"event_type": event_type, "subjects": subjects, "payload": payload}
                    prior_comparable = {"event_type": prior["event_type"], "subjects": prior["subjects"], "payload": prior["payload"]}
                    if digest(comparable) != digest(prior_comparable):
                        raise WorkspaceError("idempotency_conflict", "operation ID was already used for different canonical input", exit_code=4)
                    closing = reader
                    reader = None
                    _verify_and_close_reader(closing)
                    _authenticate_mutation_exit(lock, root)
                    return MutationResult(base.revision, prior, state_before, idempotent=True)
                if base.revision != expected_revision:
                    raise WorkspaceError("cas_conflict", f"expected revision {expected_revision}, actual {base.revision}", exit_code=4)
                transaction = Transaction(root, operation_id)
                event = plan_append(
                    transaction,
                    base,
                    event_type=event_type,
                    actor=actor or {"type": "coordinator", "id": "sdm-lifecycle"},
                    subjects=subjects,
                    payload=payload,
                    operation_id=operation_id,
                    authority=authority,
                )
                for relative, data, exclusive in artifact_writes:
                    transaction.add(relative, data, exclusive=exclusive)
                extended = replay_with((event,), base)
                projected = materialize(extended)
                if enforce_graph:
                    _enforce_typed_graph(projected)
                _, state_digest = reader.read_bytes_with_digest("state.json")
                transaction.add(
                    "state.json", projected, expected_digest=state_digest,
                )
                transaction.add(f"_memlog/{event['sequence']:020d}.json", _memlog(event), exclusive=True)
                closing = reader
                reader = None
                _verify_and_close_reader(closing)
                transaction.commit(lock=lock)
                _authenticate_mutation_exit(lock, root)
                return MutationResult(event["sequence"], event, projected)
            finally:
                if reader is not None:
                    _verify_and_close_reader(reader)
    except WorkspaceError:
        raise
    except JournalError as exc:
        exit_code = _journal_exit_code(exc.code)
        raise WorkspaceError(exc.code, str(exc), exit_code=exit_code) from exc
    except (LedgerError, ProjectionError, ContractError) as exc:
        code = getattr(exc, "code", "workspace_invalid")
        raise WorkspaceError(
            code, str(exc), exit_code=_protected_read_exit_code(code),
        ) from exc


def mutate_batch(
    workspace: str | os.PathLike[str],
    *,
    expected_revision: int,
    events: Iterable[dict[str, Any]],
    artifact_writes: Iterable[tuple[str, bytes | dict[str, Any], bool]] = (),
    enforce_graph: bool = False,
) -> MutationResult:
    """Atomically append an ordered group of governed lifecycle events.

    Some public coordinator actions reconstruct several consecutive transition
    facts from one already-persisted external receipt (for example dispatch,
    start, and terminal Attempt facts).  Committing those facts with repeated
    ``mutate`` calls would expose crash-created half transitions.  This helper
    holds the workspace lock once and commits every ledger event, memlog,
    artifact, and the final projection through one journal transaction.

    Each event descriptor has the same named fields as :func:`mutate` and must
    carry its own globally unique ``operation_id``.  A fully repeated batch is
    idempotent; a partially repeated or byte-different batch conflicts.
    """

    planned = [deepcopy(item) for item in events]
    if not planned:
        raise WorkspaceError("event_batch_empty", "an atomic event batch cannot be empty")
    operation_ids = [item.get("operation_id") for item in planned]
    if (
        any(not isinstance(value, str) or not value for value in operation_ids)
        or len(set(operation_ids)) != len(operation_ids)
    ):
        raise WorkspaceError(
            "event_batch_operation_invalid",
            "every atomic batch event requires one unique operation ID",
        )
    root = _workspace_path(workspace)
    try:
        with WorkspaceLock(root, exclusive=True) as lock:
            from journal import recover

            recover(root, lock=lock)
            reader: ReadCapability | None = _read_capability_for_lock(lock, root)
            try:
                base = replay(root, capability=reader)
                state_before = materialize(base)
                if enforce_graph:
                    _enforce_typed_graph(state_before)
                existing = [
                    state_before["operations"].get(operation_id)
                    for operation_id in operation_ids
                ]
                if any(existing):
                    if not all(existing):
                        raise WorkspaceError(
                            "idempotency_conflict",
                            "only part of the requested atomic event batch exists",
                            exit_code=4,
                        )
                    prior_events = []
                    for descriptor, stored in zip(planned, existing, strict=True):
                        prior = next(
                            item for item in base.events
                            if item["event_hash"] == stored["event_hash"]
                        )
                        comparable = {
                            "event_type": descriptor.get("event_type"),
                            "subjects": descriptor.get("subjects"),
                            "payload": descriptor.get("payload"),
                        }
                        prior_comparable = {
                            "event_type": prior["event_type"],
                            "subjects": prior["subjects"],
                            "payload": prior["payload"],
                        }
                        if digest(comparable) != digest(prior_comparable):
                            raise WorkspaceError(
                                "idempotency_conflict",
                                "atomic event batch operation ID was reused for different input",
                                exit_code=4,
                            )
                        prior_events.append(prior)
                    closing = reader
                    reader = None
                    _verify_and_close_reader(closing)
                    _authenticate_mutation_exit(lock, root)
                    return MutationResult(
                        base.revision, prior_events[-1], state_before,
                        idempotent=True,
                    )
                if base.revision != expected_revision:
                    raise WorkspaceError(
                        "cas_conflict",
                        f"expected revision {expected_revision}, actual {base.revision}",
                        exit_code=4,
                    )

                transaction = Transaction(root, operation_ids[0])
                extended = base
                appended: list[dict[str, Any]] = []
                for descriptor in planned:
                    event = plan_append(
                        transaction,
                        extended,
                        event_type=descriptor["event_type"],
                        actor=descriptor.get("actor") or {
                            "type": "coordinator", "id": "sdm-lifecycle",
                        },
                        subjects=descriptor["subjects"],
                        payload=descriptor["payload"],
                        operation_id=descriptor["operation_id"],
                        authority=descriptor.get("authority"),
                        occurred_at=descriptor.get("occurred_at"),
                    )
                    appended.append(event)
                    extended = replay_with((event,), extended)
                for relative, data, exclusive in artifact_writes:
                    transaction.add(relative, data, exclusive=exclusive)
                projected = materialize(extended)
                if enforce_graph:
                    _enforce_typed_graph(projected)
                _, state_digest = reader.read_bytes_with_digest("state.json")
                transaction.add(
                    "state.json", projected, expected_digest=state_digest,
                )
                for event in appended:
                    transaction.add(
                        f"_memlog/{event['sequence']:020d}.json",
                        _memlog(event), exclusive=True,
                    )
                closing = reader
                reader = None
                _verify_and_close_reader(closing)
                transaction.commit(lock=lock)
                _authenticate_mutation_exit(lock, root)
                return MutationResult(
                    extended.revision, appended[-1], projected,
                )
            finally:
                if reader is not None:
                    _verify_and_close_reader(reader)
    except WorkspaceError:
        raise
    except JournalError as exc:
        exit_code = _journal_exit_code(exc.code)
        raise WorkspaceError(exc.code, str(exc), exit_code=exit_code) from exc
    except (LedgerError, ProjectionError, ContractError) as exc:
        code = getattr(exc, "code", "workspace_invalid")
        raise WorkspaceError(
            code, str(exc), exit_code=_protected_read_exit_code(code),
        ) from exc


def migrate_engine(
    workspace: str | os.PathLike[str],
    *,
    new_bundle: AssetBundle,
    expected_revision: int,
    operation_id: str,
    authority: list[dict[str, Any]],
    authority_subject: dict[str, Any],
    consumed_at: str,
) -> MutationResult:
    """Atomically pin and activate a complete successor engine bundle.

    The migration event is interpreted by the old engine.  Only subsequent
    events use the new bundle, so replay never reinterprets historical bytes.
    Bundle files, the signed boundary event, projection, and memlog share one
    journal commit.
    """

    if not _PORTABLE_ID.fullmatch(operation_id):
        raise WorkspaceError(
            "invalid_operation_id", "operation ID is not portable",
        )
    if not authority or not isinstance(authority_subject, dict) \
            or not authority_subject or not isinstance(consumed_at, str):
        raise WorkspaceError(
            "engine_migration_authority_required",
            "engine migration requires verified authority and its exact consumption boundary",
            exit_code=3,
        )
    root = _workspace_path(workspace)
    reader: ReadCapability | None = None
    try:
        with WorkspaceLock(root, exclusive=True) as lock:
            from journal import recover

            recover(root, lock=lock)
            reader = _read_capability_for_lock(lock, root)
            base = replay(root, capability=reader)
            state_before = verify_stored_projection(
                root, base, capability=reader,
            )
            _enforce_typed_graph(state_before)
            existing = state_before.get("operations", {}).get(operation_id)
            if existing:
                prior = next(
                    event for event in base.events
                    if event["event_hash"] == existing["event_hash"]
                )
                if (
                    prior.get("event_type") != "engine.migrated"
                    or prior.get("payload", {}).get(
                        "new_engine_contract_digest",
                    ) != new_bundle.engine_contract_digest
                ):
                    raise WorkspaceError(
                        "idempotency_conflict",
                        "migration operation ID is bound to different engine bytes",
                        exit_code=4,
                    )
                closing = reader
                reader = None
                _verify_and_close_reader(closing)
                _authenticate_mutation_exit(lock, root)
                return MutationResult(
                    base.revision, prior, state_before, idempotent=True,
                )
            if base.revision != expected_revision:
                raise WorkspaceError(
                    "cas_conflict",
                    f"expected revision {expected_revision}, actual {base.revision}",
                    exit_code=4,
                )
            old_digest = base.active_engine_digest
            new_digest = new_bundle.engine_contract_digest
            if new_digest == old_digest:
                raise WorkspaceError(
                    "engine_migration_noop",
                    "successor engine digest must differ from the active digest",
                    exit_code=3,
                )
            if "engine.migrated" not in new_bundle.event_types():
                raise WorkspaceError(
                    "successor_engine_incomplete",
                    "successor engine cannot replay the governed migration event family",
                )
            expected_subject = {
                "governed_ids": {
                    "workspace_id": base.genesis["workspace_id"],
                    "migration_sequence": str(base.revision + 1),
                },
                "digests": {
                    "old_engine_contract_digest": old_digest,
                    "new_engine_contract_digest": new_digest,
                    "migration_boundary_head": base.head_hash,
                    "new_asset_inventory_digest": digest(
                        new_bundle.asset_digests,
                    ),
                },
            }
            if authority_subject != expected_subject:
                raise WorkspaceError(
                    "engine_migration_subject_mismatch",
                    "verified migration authority does not bind the exact old/new engine boundary",
                    exit_code=3,
                )
            payload = {
                "transition": "engine.migrate",
                "old_engine_contract_digest": old_digest,
                "new_engine_contract_digest": new_digest,
                "new_asset_digests": dict(sorted(
                    new_bundle.asset_digests.items(),
                )),
                "new_asset_inventory_digest": digest(
                    new_bundle.asset_digests,
                ),
                "migration_boundary_revision": base.revision,
                "migration_boundary_head": base.head_hash,
                "authority_action": "engine.migrate",
                "authority_subject": authority_subject,
                "authority_expected_revision": base.revision,
                "consumed_at": consumed_at,
            }
            transaction = Transaction(root, operation_id)
            event = plan_append(
                transaction, base,
                event_type="engine.migrated",
                actor={"type": "coordinator", "id": "sdm-lifecycle"},
                subjects={"workspace_id": base.genesis["workspace_id"]},
                payload=payload,
                operation_id=operation_id,
                authority=authority,
            )
            prefix = f"_engine/{new_digest}"
            for relative, data in sorted(
                new_bundle.canonical_assets.items(),
            ):
                transaction.add(
                    f"{prefix}/{relative}", data, exclusive=True,
                )
            bundles = dict(base.bundles)
            bundles[new_digest] = new_bundle
            validation_base = ReplayResult(
                base.genesis, base.events, base.revision, base.head_hash,
                base.active_engine_digest, bundles,
            )
            # Validate the exact signature, signed revision boundary, and
            # migration/bundle invariants inside the mutation API before any
            # bytes are committed.  Public callers cannot make a transaction
            # durable merely by supplying pre-shaped authority records.
            extended = replay_with((event,), validation_base)
            projected = materialize(extended)
            _enforce_typed_graph(projected)
            _, state_digest = reader.read_bytes_with_digest("state.json")
            transaction.add(
                "state.json", projected, expected_digest=state_digest,
            )
            transaction.add(
                f"_memlog/{event['sequence']:020d}.json", _memlog(event),
                exclusive=True,
            )
            closing = reader
            reader = None
            _verify_and_close_reader(closing)
            transaction.commit(lock=lock)
            _authenticate_mutation_exit(lock, root)
            return MutationResult(event["sequence"], event, projected)
    except WorkspaceError:
        if reader is not None:
            closing = reader
            reader = None
            try:
                _verify_and_close_reader(closing)
            except JournalError as verify_exc:
                raise WorkspaceError(
                    verify_exc.code, str(verify_exc),
                    exit_code=_journal_exit_code(verify_exc.code),
                ) from verify_exc
        raise
    except JournalError as exc:
        if reader is not None:
            closing = reader
            reader = None
            try:
                _verify_and_close_reader(closing)
            except JournalError as verify_exc:
                exc = verify_exc
        exit_code = _journal_exit_code(exc.code)
        raise WorkspaceError(exc.code, str(exc), exit_code=exit_code) from exc
    except (LedgerError, ProjectionError, ContractError) as exc:
        if reader is not None:
            closing = reader
            reader = None
            try:
                _verify_and_close_reader(closing)
            except JournalError as verify_exc:
                raise WorkspaceError(
                    verify_exc.code, str(verify_exc),
                    exit_code=_journal_exit_code(verify_exc.code),
                ) from verify_exc
        code = getattr(exc, "code", "engine_migration_invalid")
        raise WorkspaceError(
            code, str(exc), exit_code=_protected_read_exit_code(code),
        ) from exc
    except BaseException:
        if reader is not None:
            closing = reader
            reader = None
            try:
                _verify_and_close_reader(closing)
            except JournalError as verify_exc:
                raise WorkspaceError(
                    verify_exc.code, str(verify_exc),
                    exit_code=_journal_exit_code(verify_exc.code),
                ) from verify_exc
        raise


def publish_artifact(
    workspace: str | os.PathLike[str],
    *,
    artifact_id: str,
    instance_key: str,
    value: dict[str, Any],
    expected_revision: int,
    operation_id: str,
    parents: list[str] | None = None,
) -> MutationResult:
    if not _ARTIFACT_KEY.fullmatch(instance_key):
        raise WorkspaceError("invalid_instance_key", "artifact instance key is not portable")
    root = _workspace_path(workspace)
    base = replay(root)
    bundle = base.bundles[base.active_engine_digest]
    findings = bundle.validate_artifact(artifact_id, value)
    if findings:
        raise WorkspaceError("artifact_invalid", findings[0]["message"], findings=findings)

    def locked_precondition(
        reader: ReadCapability,
        locked_base: ReplayResult,
        state: dict[str, Any],
    ) -> None:
        locked_bundle = locked_base.bundles[locked_base.active_engine_digest]
        locked_findings = locked_bundle.validate_artifact(artifact_id, value)
        if locked_findings:
            raise WorkspaceError(
                "artifact_invalid", locked_findings[0]["message"],
                findings=locked_findings,
            )
        if artifact_id != "B04":
            return
        baseline_ids, deprecations = _stored_baseline_replacement_context(
            reader, state, strict_digests=True,
        )
        replacement_findings = validate_baseline_replacements(
            [*deprecations, value], baseline_ids,
        )
        if replacement_findings:
            raise WorkspaceError(
                replacement_findings[0]["code"],
                replacement_findings[0]["message"],
                findings=replacement_findings,
            )
    relative = f"artifacts/{artifact_id}/{instance_key}.json"
    return mutate(
        root,
        expected_revision=expected_revision,
        operation_id=operation_id,
        event_type="artifact.published",
        subjects={"workspace_id": base.genesis["workspace_id"], "artifact_id": artifact_id, "instance_key": instance_key},
        payload={
            "artifact_id": artifact_id,
            "instance_key": instance_key,
            "path": relative,
            "digest": digest(value),
            "parents": parents or [],
            "status": "published",
        },
        artifact_writes=((relative, value, True),),
        _locked_precondition=locked_precondition,
    )


























def _baseline_identities(
    artifact_id: str,
    instance_key: str,
    value: Any,
) -> set[str]:
    if artifact_id not in {"B01", "B02"} or not isinstance(value, dict):
        return set()
    content = value.get("content")
    content = content if isinstance(content, dict) else value
    subjects = value.get("subjects")
    subjects = subjects if isinstance(subjects, dict) else {}
    candidates = (
        content.get("baseline_id"),
        subjects.get("baseline_id"),
        instance_key,
    )
    return {
        candidate
        for candidate in candidates
        if isinstance(candidate, str) and candidate
    }


def _stored_baseline_replacement_context(
    reader: ReadCapability,
    state: dict[str, Any],
    *,
    strict_digests: bool,
) -> tuple[set[str], list[dict[str, Any]]]:
    baseline_ids: set[str] = set()
    deprecations: list[dict[str, Any]] = []
    for artifact_id in ("B01", "B02", "B04"):
        for instance_key, record in state.get("artifacts", {}).get(artifact_id, {}).items():
            relative = record.get("path") if isinstance(record, dict) else None
            if not isinstance(relative, str):
                raise WorkspaceError(
                    "artifact_path_invalid",
                    f"artifact ledger path is malformed: {artifact_id}:{instance_key}",
                )
            try:
                value = parse_document(reader.read_bytes(relative))
            except (CanonicalError, JournalError, ValueError) as exc:
                raise WorkspaceError(
                    "artifact_read_invalid",
                    f"artifact bytes are unsafe or invalid: {artifact_id}:{instance_key}",
                ) from exc
            if digest(value) != record["digest"]:
                if strict_digests:
                    raise WorkspaceError(
                        "artifact_digest_mismatch",
                        f"artifact bytes differ from authenticated ledger digest: {artifact_id}:{instance_key}",
                    )
                continue
            baseline_ids.update(_baseline_identities(artifact_id, instance_key, value))
            if artifact_id == "B04" and isinstance(value, dict):
                deprecations.append(value)
    return baseline_ids, deprecations


_VALIDATION_DIR_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_VALIDATION_FILE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)
_LEDGER_EVENT_FILE = re.compile(r"^(\d{20})\.json$")
_CANONICAL_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
MAX_TREE_NODES = 100_000
MAX_TREE_BYTES = 134_217_728
MAX_TREE_FILE_BYTES = 16_777_216
MAX_RETAINED_FDS = 32


def _stat_fingerprint(info: os.stat_result) -> tuple[int, ...]:
    """Mutation-sensitive identity, deliberately excluding atime."""
    return tuple(
        int(getattr(info, name, -1))
        for name in (
            "st_mode",
            "st_dev",
            "st_ino",
            "st_nlink",
            "st_uid",
            "st_gid",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
    )


def _stat_identity(info: os.stat_result) -> tuple[int, int, int]:
    """Stable entry identity for ancestors outside the governed tree."""
    return (stat.S_IFMT(info.st_mode), int(info.st_dev), int(info.st_ino))


def _stat_snapshot(info: os.stat_result, node_type: str) -> dict[str, Any]:
    return {
        "type": node_type,
        "mode": info.st_mode,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
        "device": info.st_dev,
        "inode": info.st_ino,
        "nlink": info.st_nlink,
        "uid": info.st_uid,
        "gid": info.st_gid,
    }


def _mount_identity(descriptor: int) -> str:
    """Return a trustworthy Linux mount ID or fail closed."""
    try:
        info_fd = os.open(
            f"/proc/self/fdinfo/{descriptor}",
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            data = bytearray()
            while len(data) <= 65_536:
                chunk = os.read(info_fd, min(4096, 65_537 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
        finally:
            os.close(info_fd)
        if len(data) > 65_536:
            raise OSError("fdinfo is oversized")
        for line in bytes(data).splitlines():
            if line.startswith(b"mnt_id:"):
                value = line.split(b":", 1)[1].strip().decode("ascii")
                if value.isdigit():
                    return "mnt_id:" + value
    except (OSError, UnicodeError):
        pass
    raise WorkspaceError(
        "mount_identity_unavailable",
        "trustworthy mount identity is unavailable for governed traversal",
    )


class _ValidationCapture:
    """Bounded descriptor-relative capture with a complete second pass."""

    def __init__(self, root_value: str | os.PathLike[str]) -> None:
        self.root_path = Path(os.path.abspath(os.fspath(root_value)))
        if self.root_path == Path(os.sep):
            raise WorkspaceError("unsafe_workspace", "filesystem root cannot be a governed workspace")
        self._fds: list[int] = []
        self._handles: list[dict[str, Any]] = []
        self._scans: dict[str, tuple[tuple[str, ...], tuple[int, ...], str]] = {}
        self._fd_prefix: dict[int, str] = {}
        self._seen_entries: set[str] = set()
        self._scan_discovered: set[str] = set()
        self._node_count = 1
        self._total_bytes = 0
        try:
            soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
            baseline = len(os.listdir("/proc/self/fd"))
            available = (
                MAX_RETAINED_FDS
                if soft_limit == resource.RLIM_INFINITY
                else int(soft_limit) - baseline - 4
            )
            self._fd_budget = min(MAX_RETAINED_FDS, max(0, available))
        except (OSError, ValueError):
            self._fd_budget = MAX_RETAINED_FDS
        if self._fd_budget < 2:
            raise WorkspaceError(
                "workspace_resource_limit",
                "insufficient file-descriptor budget for governed traversal",
            )
        self.entries: dict[str, dict[str, Any]] = {}
        self.files: dict[str, bytes] = {}
        self._captured = False
        try:
            self.root_fd = self._open_absolute_root()
            root_info = os.fstat(self.root_fd)
            if not stat.S_ISDIR(root_info.st_mode):
                raise WorkspaceError("unsafe_workspace", "validation root must be a real directory")
            self.root_dev = root_info.st_dev
            self.root_mount = _mount_identity(self.root_fd)
            self._fd_prefix[self.root_fd] = ""
            self.root_snapshot = _stat_snapshot(root_info, "directory")
        except Exception:
            self.close()
            raise

    def __enter__(self) -> "_ValidationCapture":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        for descriptor in reversed(self._fds):
            try:
                os.close(descriptor)
            except OSError:
                pass
        self._fds.clear()
        self._fd_prefix.clear()

    def _track_fd(self, descriptor: int, subject: str) -> int:
        if len(self._fds) >= self._fd_budget:
            os.close(descriptor)
            raise WorkspaceError(
                "workspace_resource_limit",
                "governed traversal exceeded its retained descriptor budget",
            )
        self._fds.append(descriptor)
        return descriptor

    def _release_fd(self, descriptor: int) -> None:
        self._fd_prefix.pop(descriptor, None)
        if descriptor in self._fds:
            self._fds.remove(descriptor)
        try:
            os.close(descriptor)
        except OSError:
            pass

    def _open(
        self,
        path: str | os.PathLike[str],
        flags: int,
        *,
        subject: str,
        dir_fd: int | None = None,
    ) -> int:
        try:
            descriptor = os.open(path, flags, dir_fd=dir_fd)
        except OSError as exc:
            if exc.errno in (errno.EMFILE, errno.ENFILE):
                raise WorkspaceError(
                    "workspace_resource_limit",
                    "governed traversal exhausted file descriptors",
                ) from exc
            raise
        return self._track_fd(descriptor, subject)

    def _account_entry(self, info: os.stat_result, subject: str) -> None:
        if subject in self._seen_entries:
            return
        self._seen_entries.add(subject)
        self._node_count += 1
        if self._node_count > MAX_TREE_NODES:
            raise WorkspaceError(
                "workspace_resource_limit",
                "governed workspace exceeds the fixed node limit",
            )
        if stat.S_ISREG(info.st_mode):
            if info.st_size > MAX_TREE_FILE_BYTES:
                raise WorkspaceError(
                    "workspace_resource_limit",
                    f"governed file exceeds the fixed byte limit: {subject}",
                )
            self._total_bytes += info.st_size
            if self._total_bytes > MAX_TREE_BYTES:
                raise WorkspaceError(
                    "workspace_resource_limit",
                    "governed workspace exceeds the fixed aggregate byte limit",
                )

    def _open_absolute_root(self) -> int:
        parent_fd = self._open(
            os.sep, _VALIDATION_DIR_FLAGS, subject=os.sep,
        )
        current = Path(os.sep)
        components = self.root_path.parts[1:]
        for index, component in enumerate(components):
            current /= component
            try:
                before = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise WorkspaceError("workspace_missing", f"workspace is unavailable: {self.root_path}") from exc
            if stat.S_ISLNK(before.st_mode):
                raise WorkspaceError("symlink_escape", f"symlink is forbidden in workspace root: {current}")
            if not stat.S_ISDIR(before.st_mode):
                raise WorkspaceError("unsafe_workspace", f"workspace component is not a directory: {current}")
            try:
                descriptor = self._open(
                    component, _VALIDATION_DIR_FLAGS,
                    dir_fd=parent_fd, subject=str(current),
                )
            except OSError as exc:
                code = "symlink_escape" if exc.errno in (errno.ELOOP, errno.ENOTDIR) else "concurrent_mutation"
                raise WorkspaceError(code, f"workspace component changed while opening: {current}", exit_code=4 if code == "concurrent_mutation" else 2) from exc
            opened = os.fstat(descriptor)
            governed_root = index == len(components) - 1
            if (
                _stat_fingerprint(before) != _stat_fingerprint(opened)
                if governed_root
                else _stat_identity(before) != _stat_identity(opened)
            ):
                raise WorkspaceError("concurrent_mutation", f"workspace root identity changed: {current}", exit_code=4)
            self._handles.append(
                {
                    "fd": descriptor,
                    "parent_fd": parent_fd,
                    "name": component,
                    "fingerprint": _stat_fingerprint(opened),
                    "identity": _stat_identity(opened),
                    "identity_only": not governed_root,
                    "subject": str(current),
                    "mount": _mount_identity(descriptor),
                }
            )
            parent_fd = descriptor
        return parent_fd

    def acquire_shared_lock(self) -> None:
        subject = str(self.root_path / "_lock")
        expected = self._entry_stat(self.root_fd, "_lock", subject)
        if not stat.S_ISREG(expected.st_mode):
            raise WorkspaceError("unsafe_lock", "workspace lock must be a regular file")
        if expected.st_nlink != 1:
            raise WorkspaceError("hardlink_escape", "workspace lock must have exactly one link")
        try:
            descriptor = self._open(
                "_lock", _VALIDATION_FILE_FLAGS,
                dir_fd=self.root_fd, subject=subject,
            )
        except OSError as exc:
            raise WorkspaceError("lock_failed", f"cannot open workspace lock: {exc}") from exc
        opened = os.fstat(descriptor)
        if (
            _stat_fingerprint(expected) != _stat_fingerprint(opened)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_dev != self.root_dev
            or _mount_identity(descriptor) != self.root_mount
        ):
            raise WorkspaceError("concurrent_mutation", "workspace lock identity changed while opening", exit_code=4)
        self._handles.append(
            {
                "fd": descriptor,
                "parent_fd": self.root_fd,
                "name": "_lock",
                "fingerprint": _stat_fingerprint(opened),
                "subject": subject,
                "mount": self.root_mount,
            }
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkspaceError("lock_conflict", "workspace lock is held by another process", exit_code=4) from exc
        except OSError as exc:
            raise WorkspaceError("lock_failed", f"cannot acquire workspace lock: {exc}") from exc

    def _bounded_scan_names(
        self, descriptor: int, subject: str, prefix: str,
    ) -> tuple[str, ...]:
        """Account directory entries incrementally before collecting or sorting."""

        names: list[str] = []
        try:
            with os.scandir(descriptor) as scanner:
                for entry in scanner:
                    relative = f"{prefix}/{entry.name}".strip("/")
                    if relative not in self._scan_discovered:
                        if len(self._scan_discovered) >= MAX_TREE_NODES - 1:
                            raise WorkspaceError(
                                "workspace_resource_limit",
                                "governed workspace exceeds the fixed node limit",
                            )
                        self._scan_discovered.add(relative)
                    names.append(entry.name)
        except WorkspaceError:
            raise
        except (OSError, MemoryError) as exc:
            code = (
                "workspace_resource_limit"
                if isinstance(exc, MemoryError) else "concurrent_mutation"
            )
            raise WorkspaceError(
                code, f"cannot scan governed directory: {subject}",
                exit_code=4 if code == "concurrent_mutation" else 2,
            ) from exc
        try:
            names.sort()
        except MemoryError as exc:
            raise WorkspaceError(
                "workspace_resource_limit",
                "governed directory inventory exceeds available bounded memory",
            ) from exc
        return tuple(names)

    def _scan(self, descriptor: int, subject: str) -> tuple[str, ...]:
        before = os.fstat(descriptor)
        prefix = self._fd_prefix.get(descriptor, "")
        names = self._bounded_scan_names(descriptor, subject, prefix)
        after = os.fstat(descriptor)
        if _stat_fingerprint(before) != _stat_fingerprint(after):
            raise WorkspaceError("concurrent_mutation", f"directory changed while scanning: {subject}", exit_code=4)
        tracked_prefix = self._fd_prefix.get(descriptor)
        if tracked_prefix is not None:
            record = (names, _stat_fingerprint(before), subject)
            prior = self._scans.get(tracked_prefix)
            if prior is not None and prior[:2] != record[:2]:
                raise WorkspaceError(
                    "concurrent_mutation",
                    f"directory changed between scans: {subject}",
                    exit_code=4,
                )
            self._scans[tracked_prefix] = record
        return names

    def _entry_stat(self, parent_fd: int, name: str, subject: str) -> os.stat_result:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise WorkspaceError("concurrent_mutation", f"entry disappeared while validating: {subject}", exit_code=4) from exc
        if stat.S_ISLNK(info.st_mode):
            raise WorkspaceError("symlink_escape", f"symlink is forbidden: {subject}")
        if info.st_dev != self.root_dev:
            raise WorkspaceError("mount_escape", f"cross-device node is forbidden: {subject}")

        # This pathname probe is non-authoritative: descriptor identity below
        # is the security boundary.  It only detects a concurrent rebind (and
        # preserves the historical mount-injection regression); no bytes are
        # read through the pathname.
        try:
            named = Path(subject).lstat()
        except OSError as exc:
            raise WorkspaceError("concurrent_mutation", f"entry path changed while validating: {subject}", exit_code=4) from exc
        if named.st_dev != self.root_dev:
            raise WorkspaceError("mount_escape", f"mounted subtree is forbidden: {subject}")
        if _stat_fingerprint(named) != _stat_fingerprint(info):
            raise WorkspaceError("concurrent_mutation", f"entry identity changed while validating: {subject}", exit_code=4)
        self._account_entry(info, subject)
        return info

    def _open_dir(self, parent_fd: int, name: str, subject: str, expected: os.stat_result) -> int:
        if not stat.S_ISDIR(expected.st_mode):
            raise WorkspaceError("special_node", f"expected governed directory: {subject}")
        try:
            descriptor = self._open(
                name, _VALIDATION_DIR_FLAGS,
                dir_fd=parent_fd, subject=subject,
            )
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.ENOENT):
                raise WorkspaceError("concurrent_mutation", f"directory changed while opening: {subject}", exit_code=4) from exc
            raise WorkspaceError("unsafe_workspace", f"cannot open governed directory: {subject}: {exc}") from exc
        opened = os.fstat(descriptor)
        opened_mount = _mount_identity(descriptor)
        if opened.st_dev != self.root_dev or opened_mount != self.root_mount:
            raise WorkspaceError("mount_escape", f"mounted subtree is forbidden: {subject}")
        if _stat_fingerprint(expected) != _stat_fingerprint(opened):
            raise WorkspaceError("concurrent_mutation", f"directory identity changed while opening: {subject}", exit_code=4)
        parent_prefix = self._fd_prefix.get(parent_fd)
        if parent_prefix is not None:
            self._fd_prefix[descriptor] = (
                f"{parent_prefix}/{name}".strip("/")
            )
        return descriptor

    def _read_file(self, parent_fd: int, name: str, subject: str, expected: os.stat_result) -> tuple[bytes, os.stat_result]:
        if not stat.S_ISREG(expected.st_mode):
            raise WorkspaceError("special_node", f"special filesystem node is forbidden: {subject}")
        if expected.st_nlink != 1:
            raise WorkspaceError("hardlink_escape", f"hard-linked governed file is forbidden: {subject}")
        try:
            descriptor = self._open(
                name, _VALIDATION_FILE_FLAGS,
                dir_fd=parent_fd, subject=subject,
            )
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOENT, errno.ENXIO):
                raise WorkspaceError("concurrent_mutation", f"file changed while opening: {subject}", exit_code=4) from exc
            raise WorkspaceError("unsafe_workspace", f"cannot open governed file: {subject}: {exc}") from exc
        try:
            opened = os.fstat(descriptor)
            opened_mount = _mount_identity(descriptor)
            if opened.st_dev != self.root_dev or opened_mount != self.root_mount:
                raise WorkspaceError("mount_escape", f"mounted file is forbidden: {subject}")
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or _stat_fingerprint(expected) != _stat_fingerprint(opened)
            ):
                raise WorkspaceError("concurrent_mutation", f"file identity changed while opening: {subject}", exit_code=4)
            if opened.st_size > MAX_TREE_FILE_BYTES:
                raise WorkspaceError(
                    "workspace_resource_limit",
                    f"governed file exceeds the fixed byte limit: {subject}",
                )
            data = bytearray()
            while len(data) < opened.st_size:
                chunk = os.read(
                    descriptor,
                    min(1024 * 1024, opened.st_size - len(data)),
                )
                if not chunk:
                    break
                data.extend(chunk)
            after = os.fstat(descriptor)
            if (
                _stat_fingerprint(opened) != _stat_fingerprint(after)
                or len(data) != opened.st_size
            ):
                raise WorkspaceError("concurrent_mutation", f"file changed while reading: {subject}", exit_code=4)
            return bytes(data), opened
        finally:
            self._release_fd(descriptor)

    def capture(self) -> None:
        if self._captured:
            raise WorkspaceError("internal_error", "validation capture may run only once", exit_code=70)

        def visit(descriptor: int, prefix: str) -> None:
            subject = str(self.root_path / prefix) if prefix else str(self.root_path)
            for name in self._scan(descriptor, subject):
                relative = f"{prefix}/{name}" if prefix else name
                child_subject = str(self.root_path / relative)
                metadata = self._entry_stat(descriptor, name, child_subject)
                if stat.S_ISDIR(metadata.st_mode):
                    child_fd = self._open_dir(descriptor, name, child_subject, metadata)
                    self.entries[relative] = _stat_snapshot(metadata, "directory")
                    try:
                        visit(child_fd, relative)
                    finally:
                        self._release_fd(child_fd)
                elif stat.S_ISREG(metadata.st_mode):
                    data, opened = self._read_file(descriptor, name, child_subject, metadata)
                    snapshot = _stat_snapshot(opened, "file")
                    snapshot["digest"] = digest_bytes(data)
                    self.entries[relative] = snapshot
                    self.files[relative] = data
                else:
                    raise WorkspaceError("special_node", f"special filesystem node is forbidden: {relative}")

        visit(self.root_fd, "")
        self._captured = True

    def snapshot(self) -> dict[str, Any]:
        if not self._captured:
            raise WorkspaceError("internal_error", "workspace was not captured", exit_code=70)
        return {
            "root_device": self.root_dev,
            "root_mount": self.root_mount,
            "root": deepcopy(self.root_snapshot),
            "entries": deepcopy(self.entries),
        }

    def verify(self) -> None:
        for handle in self._handles:
            try:
                descriptor_info = os.fstat(handle["fd"])
                entry_info = os.stat(handle["name"], dir_fd=handle["parent_fd"], follow_symlinks=False)
            except (FileNotFoundError, OSError) as exc:
                raise WorkspaceError("concurrent_mutation", f"path anchor changed during validation: {handle['subject']}", exit_code=4) from exc
            expected_identity = handle.get("identity")
            identity_changed = expected_identity is not None and (
                _stat_identity(descriptor_info) != expected_identity
                or _stat_identity(entry_info) != expected_identity
            )
            metadata_changed = (
                not handle.get("identity_only", False)
                and (
                    _stat_fingerprint(descriptor_info) != handle["fingerprint"]
                    or _stat_fingerprint(entry_info) != handle["fingerprint"]
                )
            )
            if identity_changed or metadata_changed or _mount_identity(handle["fd"]) != handle["mount"]:
                raise WorkspaceError("concurrent_mutation", f"path identity changed during validation: {handle['subject']}", exit_code=4)

        def open_directory(relative: str) -> tuple[int, list[int]]:
            descriptor = self.root_fd
            opened: list[int] = []
            prefix: list[str] = []
            try:
                for component in PurePosixPath(relative).parts if relative else ():
                    prefix.append(component)
                    subject = str(self.root_path.joinpath(*prefix))
                    expected = os.stat(
                        component, dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                    if (
                        stat.S_ISLNK(expected.st_mode)
                        or not stat.S_ISDIR(expected.st_mode)
                    ):
                        raise WorkspaceError(
                            "concurrent_mutation",
                            f"path anchor changed during validation: {subject}",
                            exit_code=4,
                        )
                    child = self._open(
                        component, _VALIDATION_DIR_FLAGS,
                        dir_fd=descriptor, subject=subject,
                    )
                    opened.append(child)
                    current = os.fstat(child)
                    if (
                        current.st_dev != self.root_dev
                        or _mount_identity(child) != self.root_mount
                        or _stat_fingerprint(current)
                        != _stat_fingerprint(expected)
                    ):
                        raise WorkspaceError(
                            "concurrent_mutation",
                            f"path identity changed during validation: {subject}",
                            exit_code=4,
                        )
                    descriptor = child
                return descriptor, opened
            except Exception:
                for child in reversed(opened):
                    self._release_fd(child)
                raise

        for relative, (expected_names, fingerprint, subject) in self._scans.items():
            descriptor, opened = open_directory(relative)
            try:
                current = os.fstat(descriptor)
                if (
                    _stat_fingerprint(current) != fingerprint
                    or self._bounded_scan_names(
                        descriptor, subject, relative,
                    ) != expected_names
                ):
                    raise WorkspaceError(
                        "concurrent_mutation",
                        f"directory changed during validation: {subject}",
                        exit_code=4,
                    )
            finally:
                for child in reversed(opened):
                    self._release_fd(child)

        for relative, expected in self.entries.items():
            parts = PurePosixPath(relative).parts
            parent = "/".join(parts[:-1])
            descriptor, opened = open_directory(parent)
            try:
                try:
                    current = os.stat(
                        parts[-1], dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError as exc:
                    raise WorkspaceError(
                        "concurrent_mutation",
                        f"captured path disappeared: {relative}",
                        exit_code=4,
                    ) from exc
                if stat.S_ISLNK(current.st_mode) or current.st_dev != self.root_dev:
                    raise WorkspaceError(
                        "concurrent_mutation",
                        f"captured path became unsafe: {relative}",
                        exit_code=4,
                    )
                kind = (
                    "directory" if stat.S_ISDIR(current.st_mode)
                    else "file" if stat.S_ISREG(current.st_mode)
                    else "special"
                )
                current_snapshot = _stat_snapshot(current, kind)
                if any(
                    current_snapshot[key] != expected[key]
                    for key in current_snapshot
                ):
                    raise WorkspaceError(
                        "concurrent_mutation",
                        f"captured path identity changed: {relative}",
                        exit_code=4,
                    )
                if kind == "file":
                    if current.st_nlink != 1:
                        raise WorkspaceError(
                            "hardlink_escape",
                            f"captured file became hard-linked: {relative}",
                        )
                    file_fd = self._open(
                        parts[-1], _VALIDATION_FILE_FLAGS,
                        dir_fd=descriptor, subject=relative,
                    )
                    try:
                        opened_file = os.fstat(file_fd)
                        if (
                            _stat_fingerprint(opened_file)
                            != _stat_fingerprint(current)
                            or _mount_identity(file_fd) != self.root_mount
                        ):
                            raise WorkspaceError(
                                "concurrent_mutation",
                                f"captured file identity changed: {relative}",
                                exit_code=4,
                            )
                        hasher = __import__("hashlib").sha256()
                        read_size = 0
                        while True:
                            chunk = os.read(file_fd, 1024 * 1024)
                            if not chunk:
                                break
                            read_size += len(chunk)
                            hasher.update(chunk)
                        if (
                            read_size != opened_file.st_size
                            or _stat_fingerprint(os.fstat(file_fd))
                            != _stat_fingerprint(opened_file)
                            or "sha256:" + hasher.hexdigest()
                            != expected["digest"]
                        ):
                            raise WorkspaceError(
                                "concurrent_mutation",
                                f"file content changed during validation: {relative}",
                                exit_code=4,
                            )
                    finally:
                        self._release_fd(file_fd)
            finally:
                for child in reversed(opened):
                    self._release_fd(child)


def snapshot_tree(root_value: str | os.PathLike[str]) -> dict[str, Any]:
    with _ValidationCapture(root_value) as capture:
        capture.acquire_shared_lock()
        capture.capture()
        capture.verify()
        return capture.snapshot()


def _capture_document(
    capture: _ValidationCapture,
    relative: str,
    *,
    missing_code: str,
    invalid_code: str,
) -> tuple[Any, bytes]:
    raw = capture.files.get(relative)
    if raw is None:
        raise LedgerError(missing_code, f"required governed file is missing: {relative}")
    try:
        value = parse_document(raw)
    except ValueError as exc:
        raise LedgerError(invalid_code, f"cannot parse governed file {relative}: {exc}") from exc
    return value, raw


def _bundle_from_capture(capture: _ValidationCapture, expected_digest: str) -> AssetBundle:
    if not _CANONICAL_DIGEST.fullmatch(expected_digest):
        raise LedgerError("pinned_engine_invalid", f"invalid pinned engine digest: {expected_digest}")
    prefix = f"_engine/{expected_digest}"
    documents: dict[str, Any] = {}
    canonical_assets: dict[str, bytes] = {}
    for name in ASSET_FILES:
        value, _ = _capture_document(
            capture,
            f"{prefix}/{name}",
            missing_code="pinned_engine_invalid",
            invalid_code="pinned_engine_invalid",
        )
        documents[name] = value
        canonical_assets[name] = canonical_bytes(value)
    for name in SCHEMA_FILES:
        relative = f"schemas/{name}"
        value, _ = _capture_document(
            capture,
            f"{prefix}/{relative}",
            missing_code="pinned_engine_invalid",
            invalid_code="pinned_engine_invalid",
        )
        documents[relative] = value
        canonical_assets[relative] = canonical_bytes(value)
    # Resolve owner schemas exclusively from the already captured pinned
    # catalog and workspace bytes. Read-only replay must never consult the
    # currently installed sibling Skills.
    require_supported_engine(documents)
    for binding in _owned_schema_bindings(documents):
        relative = binding["path"]
        if relative in documents:
            continue
        value, _ = _capture_document(
            capture,
            f"{prefix}/{relative}",
            missing_code="pinned_engine_invalid",
            invalid_code="pinned_engine_invalid",
        )
        canonical = canonical_bytes(value)
        if digest_bytes(canonical) != binding["digest"]:
            raise LedgerError(
                "pinned_engine_invalid",
                f"pinned owner schema digest differs from its catalog binding: {relative}",
            )
        documents[relative] = value
        canonical_assets[relative] = canonical
    asset_digests = {
        name: digest_bytes(data)
        for name, data in sorted(canonical_assets.items())
    }
    computed_digest = digest(
        {"schema": "sdm-engine-contract/v1", "asset_digests": asset_digests}
    )
    if computed_digest != expected_digest:
        raise LedgerError(
            "pinned_engine_invalid",
            f"pinned engine bundle digest differs from its directory identity: {expected_digest}",
        )
    bundle = AssetBundle(
        capture.root_path / prefix,
        documents,
        canonical_assets,
        asset_digests,
        computed_digest,
    )
    bundle.self_validate()
    return bundle


def _replay_capture(capture: _ValidationCapture) -> ReplayResult:
    genesis, genesis_raw = _capture_document(
        capture,
        "_ledger/genesis.json",
        missing_code="genesis_invalid",
        invalid_code="genesis_invalid",
    )
    if genesis_raw != canonical_bytes(genesis):
        raise LedgerError("genesis_noncanonical", "genesis bytes are not canonical JSON")
    if not isinstance(genesis, dict) or genesis.get("schema") != "sdm-ledger/v1":
        raise LedgerError("genesis_invalid", "genesis schema is missing or invalid")
    required = {
        "asset_digests",
        "created_at",
        "engine_contract_digest",
        "schema",
        "workspace_id",
    }
    if set(genesis) != required:
        raise LedgerError("genesis_invalid", "genesis has missing or unknown fields")
    active_digest = genesis.get("engine_contract_digest")
    if not isinstance(active_digest, str):
        raise LedgerError("genesis_invalid", "genesis lacks an engine contract digest")
    bundle = _bundle_from_capture(capture, active_digest)
    if bundle.asset_digests != genesis.get("asset_digests"):
        raise LedgerError(
            "genesis_asset_mismatch",
            "genesis asset digests do not match its pinned engine bundle",
        )
    bundles = {active_digest: bundle}
    previous = genesis_hash(genesis)
    events: list[dict[str, Any]] = []
    event_names: list[tuple[int, str]] = []
    prefix = "_ledger/events/"
    for relative in capture.files:
        if not relative.startswith(prefix):
            continue
        name = relative[len(prefix):]
        if "/" in name:
            raise LedgerError("unexpected_ledger_file", f"event ledger contains nested content: {name}")
        match = _LEDGER_EVENT_FILE.fullmatch(name)
        if not match:
            raise LedgerError("unexpected_ledger_file", f"event ledger contains an unexpected file: {name}")
        event_names.append((int(match.group(1)), relative))
    event_names.sort()
    for expected_sequence, (filename_sequence, relative) in enumerate(event_names, 1):
        if filename_sequence != expected_sequence:
            raise LedgerError(
                "ledger_gap",
                f"event sequence is missing, duplicated, or reordered at {expected_sequence}",
                sequence=expected_sequence,
            )
        event, raw = _capture_document(
            capture,
            relative,
            missing_code="event_invalid",
            invalid_code="event_invalid",
        )
        if raw != canonical_bytes(event):
            raise LedgerError(
                "event_noncanonical",
                f"event {expected_sequence} bytes are not canonical JSON",
                sequence=expected_sequence,
            )
        if not isinstance(event, dict):
            raise LedgerError("event_invalid", "event must be an object", sequence=expected_sequence)
        if event.get("sequence") != expected_sequence:
            raise LedgerError(
                "event_sequence_mismatch",
                "event body sequence differs from filename/order",
                sequence=expected_sequence,
            )
        if event.get("previous_hash") != previous:
            raise LedgerError(
                "event_chain_mismatch",
                "event previous_hash does not match reconstructed head",
                sequence=expected_sequence,
            )
        if event.get("engine_contract_digest") != active_digest:
            raise LedgerError(
                "event_engine_mismatch",
                "event was not interpreted with the active pinned engine",
                sequence=expected_sequence,
            )
        without_hash = dict(event)
        claimed = without_hash.pop("event_hash", None)
        computed = event_hash(previous, without_hash)
        if claimed != computed:
            raise LedgerError(
                "event_hash_mismatch",
                "event hash does not match canonical chained bytes",
                sequence=expected_sequence,
            )
        schema_findings = validate_schema(
            event,
            bundle.document("schemas/event.schema.json"),
            subject=f"event:{expected_sequence}",
            governing_asset="schemas/event.schema.json",
        )
        if schema_findings:
            raise LedgerError(
                "event_schema_invalid",
                schema_findings[0]["message"],
                sequence=expected_sequence,
            )
        if event.get("event_type") not in bundle.event_types():
            raise LedgerError(
                "event_type_unknown",
                f"event type is not declared by pinned engine: {event.get('event_type')}",
                sequence=expected_sequence,
            )
        _validate_event_authority(event, bundle, events)
        if event.get("event_type") == "engine.migrated":
            next_digest = event.get("payload", {}).get("new_engine_contract_digest")
            if not isinstance(next_digest, str):
                raise LedgerError(
                    "migration_invalid",
                    "engine migration lacks new_engine_contract_digest",
                    sequence=expected_sequence,
                )
            if next_digest not in bundles:
                bundles[next_digest] = _bundle_from_capture(capture, next_digest)
            _validate_migration_payload(
                event,
                active_digest=active_digest,
                new_bundle=bundles[next_digest],
                workspace_id=genesis["workspace_id"],
            )
            active_digest = next_digest
            bundle = bundles[next_digest]
        events.append(event)
        previous = str(claimed)
    return ReplayResult(
        genesis,
        tuple(events),
        len(events),
        previous,
        active_digest,
        bundles,
    )


def _state_from_capture(capture: _ValidationCapture, replayed: ReplayResult) -> dict[str, Any]:
    stored, raw = _capture_document(
        capture,
        "state.json",
        missing_code="stored_projection_invalid",
        invalid_code="stored_projection_invalid",
    )
    if not isinstance(stored, dict):
        raise ProjectionError("stored_projection_invalid", "stored projection is not an object")
    if raw != canonical_bytes(stored):
        raise ProjectionError("stored_projection_noncanonical", "stored projection bytes are not canonical JSON")
    bundle = replayed.bundles[replayed.active_engine_digest]
    schema_findings = validate_schema(
        stored,
        bundle.document("schemas/state.schema.json"),
        subject="state.json",
        governing_asset="schemas/state.schema.json",
    )
    if schema_findings:
        raise ProjectionError(
            "stored_projection_schema_invalid",
            schema_findings[0]["message"],
        )
    computed = materialize(replayed)
    if canonical_bytes(computed) != canonical_bytes(stored):
        raise ProjectionError("projection_mismatch", "stored projection contradicts full ledger replay")
    return computed


def _inventory_finding(
    code: str,
    message: str,
    *,
    subject: str,
    governing_asset: str = "command-contracts.json",
) -> dict[str, Any]:
    return finding(
        code,
        message,
        subject=subject,
        governing_asset=governing_asset,
        gate_impact="block",
    )


def _parse_inventory_document(
    capture: _ValidationCapture,
    relative: str,
    findings: list[dict[str, Any]],
    *,
    code: str,
    governing_asset: str,
) -> Any | None:
    raw = capture.files.get(relative)
    if raw is None:
        findings.append(
            _inventory_finding(
                f"{code}_missing",
                f"required governed record is missing: {relative}",
                subject=relative,
                governing_asset=governing_asset,
            )
        )
        return None
    try:
        value = parse_document(raw)
    except ValueError as exc:
        findings.append(
            _inventory_finding(
                f"{code}_invalid",
                f"governed record cannot be parsed: {exc}",
                subject=relative,
                governing_asset=governing_asset,
            )
        )
        return None
    if raw != canonical_bytes(value):
        findings.append(
            _inventory_finding(
                f"{code}_noncanonical",
                "governed record bytes are not canonical JSON",
                subject=relative,
                governing_asset=governing_asset,
            )
        )
    return value


def _validate_artifact_inventory(
    capture: _ValidationCapture,
    replayed: ReplayResult,
    state: dict[str, Any],
) -> tuple[list[dict[str, Any]], set[str], dict[str, tuple[str, str, dict[str, Any], Any]]]:
    findings: list[dict[str, Any]] = []
    expected_files: set[str] = set()
    records_by_path: dict[str, tuple[str, str, dict[str, Any], Any]] = {}
    bundle = replayed.bundles[replayed.active_engine_digest]
    allowed_ids = {
        row["id"]
        for asset in ("artifact-catalog.json", "supporting-artifact-catalog.json")
        for row in bundle.document(asset)["artifacts"]
    }
    artifact_map = state.get("artifacts")
    if not isinstance(artifact_map, dict):
        return (
            [_inventory_finding("artifact_inventory_invalid", "state artifacts inventory is not an object", subject="state.json", governing_asset="schemas/state.schema.json")],
            expected_files,
            records_by_path,
        )
    baseline_ids: set[str] = set()
    deprecations: list[dict[str, Any]] = []
    for artifact_id, instances in sorted(artifact_map.items()):
        if artifact_id not in allowed_ids:
            findings.append(
                _inventory_finding(
                    "artifact_type_untracked",
                    f"state names an undeclared governed artifact type: {artifact_id}",
                    subject=str(artifact_id),
                    governing_asset="artifact-catalog.json",
                )
            )
            continue
        if not isinstance(instances, dict):
            findings.append(
                _inventory_finding(
                    "artifact_inventory_invalid",
                    "artifact instance inventory is not an object",
                    subject=artifact_id,
                    governing_asset="schemas/state.schema.json",
                )
            )
            continue
        for instance_key, record in sorted(instances.items()):
            subject = f"{artifact_id}:{instance_key}"
            if not isinstance(instance_key, str) or not _ARTIFACT_KEY.fullmatch(instance_key):
                findings.append(
                    _inventory_finding(
                        "artifact_instance_invalid",
                        "artifact instance key is not portable",
                        subject=subject,
                        governing_asset="artifact-catalog.json",
                    )
                )
                continue
            expected_path = f"artifacts/{artifact_id}/{instance_key}.json"
            expected_files.add(expected_path)
            if not isinstance(record, dict):
                findings.append(
                    _inventory_finding(
                        "artifact_record_invalid",
                        "authenticated artifact state record is not an object",
                        subject=subject,
                        governing_asset="schemas/state.schema.json",
                    )
                )
                continue
            if record.get("path") != expected_path:
                findings.append(
                    _inventory_finding(
                        "artifact_path_mismatch",
                        f"authenticated artifact path must be exact: {expected_path}",
                        subject=subject,
                        governing_asset="artifact-catalog.json",
                    )
                )
            value = _parse_inventory_document(
                capture,
                expected_path,
                findings,
                code="artifact",
                governing_asset="artifact-catalog.json",
            )
            if value is None:
                continue
            records_by_path[expected_path] = (artifact_id, instance_key, record, value)
            if digest(value) != record.get("digest"):
                findings.append(
                    _inventory_finding(
                        "artifact_digest_mismatch",
                        "artifact bytes differ from authenticated ledger digest",
                        subject=subject,
                        governing_asset="artifact-catalog.json",
                    )
                )
            if not isinstance(value, dict):
                findings.append(
                    _inventory_finding(
                        "artifact_envelope_invalid",
                        "governed artifact must be an object",
                        subject=subject,
                        governing_asset="schemas/artifact.schema.json",
                    )
                )
                continue
            if value.get("artifact_type") != artifact_id or value.get("instance_key") != instance_key:
                findings.append(
                    _inventory_finding(
                        "artifact_subject_mismatch",
                        "artifact envelope type/instance does not match its authenticated state subject",
                        subject=subject,
                        governing_asset="schemas/artifact.schema.json",
                    )
                )
            artifact_subjects = value.get("subjects")
            if not isinstance(artifact_subjects, dict) or artifact_subjects.get("workspace_id") != state.get("workspace_id"):
                findings.append(
                    _inventory_finding(
                        "artifact_subject_mismatch",
                        "artifact workspace subject does not match the authenticated workspace",
                        subject=subject,
                        governing_asset="schemas/artifact.schema.json",
                    )
                )
            findings.extend(bundle.validate_artifact(artifact_id, value))
            baseline_ids.update(_baseline_identities(artifact_id, instance_key, value))
            if artifact_id == "B04":
                deprecations.append(value)
    findings.extend(validate_baseline_replacements(deprecations, baseline_ids))
    return findings, expected_files, records_by_path


def _request_creation_context(
    replayed: ReplayResult,
    request: dict[str, Any],
) -> tuple[dict[str, Any] | None, AssetBundle | None, tuple[str, str] | None]:
    """Resolve an S12 to the exact ledger event and engine that created it."""

    workspace_revision = request.get("workspace_revision")
    request_id = request.get("request_id")
    if (
        not isinstance(workspace_revision, int)
        or isinstance(workspace_revision, bool)
        or workspace_revision < 0
        or not isinstance(request_id, str)
    ):
        return None, None, (
            "request_creation_boundary_invalid",
            "S12 workspace revision or request identity is invalid",
        )
    sequence = workspace_revision + 1
    if sequence < 1 or sequence > len(replayed.events):
        return None, None, (
            "request_creation_boundary_invalid",
            "S12 workspace revision does not name a ledger creation event",
        )
    created = replayed.events[sequence - 1]
    payload = created.get("payload", {})
    normal_creation = (
        created.get("event_type") == "handoff.created"
        and payload.get("request_id") == request_id
    )
    retry_creation = (
        created.get("event_type") == "handoff.retry-created"
        and payload.get("retry_request_id") == request_id
    )
    if (
        not (normal_creation or retry_creation)
        or payload.get("request_digest") != digest(request)
    ):
        return None, None, (
            "request_creation_boundary_invalid",
            "S12 digest is not bound by its claimed creation event",
        )
    engine_digest = created.get("engine_contract_digest")
    bundle = replayed.bundles.get(engine_digest)
    if bundle is None:
        return None, None, (
            "request_engine_unavailable",
            "S12 creation event names an unavailable pinned engine",
        )
    return created, bundle, None


def _authenticate_inventory_result(
    replayed: ReplayResult,
    request: dict[str, Any],
    result: dict[str, Any],
    *,
    authenticated_result_digest: str,
    event_types: set[str],
    event_reason: str | None = None,
) -> tuple[dict[str, Any] | None, tuple[str, str] | None]:
    """Replay the coordinator-owned producer-verification boundary for one S13."""

    request_id = request.get("request_id")
    matching_events = []
    for event in replayed.events:
        payload = event.get("payload", {})
        if (
            event.get("event_type") not in event_types
            or payload.get("request_id") != request_id
            or payload.get("result_digest") != authenticated_result_digest
        ):
            continue
        if event_reason is not None and payload.get("reason") != event_reason:
            continue
        matching_events.append(event)
    if len(matching_events) != 1:
        return None, (
            "producer_terminal_event_invalid",
            "S13 must be bound by exactly one matching producer-verification ledger event",
        )
    terminal = matching_events[0]
    terminal_payload = terminal.get("payload", {})
    if terminal.get("event_type") in {"handoff.result-ingested", "handoff.reconciled"} and (
        terminal_payload.get("terminal_status") != result.get("terminal_status")
    ):
        return None, (
            "result_packet_status_mismatch",
            "S13 terminal status differs from its producer-verification ledger event",
        )

    repeated = (
        "request_id",
        "operation",
        "binding_identity",
        "binding_skill_digest",
        "adapter_contract_digest",
        "workspace_id",
        "workspace_revision",
        "gate",
        "step",
        "subjects",
        "idempotency_key",
    )
    if any(result.get(key) != request.get(key) for key in repeated):
        return None, (
            "result_packet_subject_mismatch",
            "S13 does not exactly repeat every immutable S12 anchor",
        )
    unsigned_result = dict(result)
    claimed_receipt_digest = unsigned_result.pop("receipt_digest", None)
    if claimed_receipt_digest != digest(unsigned_result):
        return None, (
            "result_receipt_digest_mismatch",
            "S13 receipt_digest does not bind the complete packet excluding only receipt_digest",
        )

    created_events = [
        event
        for event in replayed.events
        if event.get("event_type") == "workspace.created"
    ]
    if len(created_events) != 1:
        return None, (
            "producer_trust_required",
            "producer verification requires exactly one workspace.created trust root",
        )
    trust_payload = created_events[0].get("payload", {})
    trusted_snapshot = trust_payload.get("authority_trust_snapshot")
    trusted_snapshot_digest = trust_payload.get("authority_trust_snapshot_digest")
    consumed_at = terminal_payload.get("producer_attestation_consumed_at")
    if (
        not isinstance(trusted_snapshot, dict)
        or not isinstance(trusted_snapshot_digest, str)
        or trusted_snapshot_digest != digest(trusted_snapshot)
        or not isinstance(consumed_at, str)
    ):
        return None, (
            "producer_verification_record_missing",
            "S13 lacks coordinator-pinned trust or consumption-time metadata",
        )
    _, bundle, creation_problem = _request_creation_context(replayed, request)
    if creation_problem is not None or bundle is None:
        return None, creation_problem or (
            "request_engine_unavailable",
            "S12 creation engine is unavailable",
        )
    try:
        verified = verify_platform_result_attestation(
            request,
            result,
            bundle=bundle,
            trusted_snapshot=trusted_snapshot,
            trusted_snapshot_digest=trusted_snapshot_digest,
            consumption_time=consumed_at,
        )
    except AuthorityError as exc:
        return None, (exc.code, f"S13 producer attestation is invalid: {exc}")
    except (KeyError, TypeError, ValueError) as exc:
        return None, (
            "producer_verification_input_invalid",
            f"S13 producer verification input is malformed: {exc}",
        )
    authenticated_fields = {
        "producer_attestation_digest": verified["attestation_digest"],
        "producer_subject_digest": verified["subject_digest"],
        "producer_key_digest": verified["key_digest"],
        "producer_trust_snapshot_digest": verified["trust_snapshot_digest"],
        "producer_attestation_consumed_at": verified["consumed_at"],
        "producer_identity": verified["producer_identity"],
        "producer_principal_id": verified["producer_principal_id"],
    }
    if any(terminal_payload.get(key) != value for key, value in authenticated_fields.items()):
        return None, (
            "producer_verification_record_mismatch",
            "S13 producer verification metadata differs from its authenticated ledger event",
        )
    return terminal, None


def _validate_packet_inventory(
    capture: _ValidationCapture,
    replayed: ReplayResult,
    state: dict[str, Any],
) -> tuple[list[dict[str, Any]], set[str]]:
    findings: list[dict[str, Any]] = []
    expected_files: set[str] = set()
    bundle = replayed.bundles[replayed.active_engine_digest]
    handoffs_map = state.get("handoffs", {})
    if not isinstance(handoffs_map, dict):
        return (
            [_inventory_finding("packet_inventory_invalid", "state handoff inventory is not an object", subject="state.json", governing_asset="schemas/state.schema.json")],
            expected_files,
        )
    requests: dict[str, dict[str, Any]] = {}
    request_bundles: dict[str, AssetBundle] = {}
    for request_id, record in sorted(handoffs_map.items()):
        subject = str(request_id)
        if not isinstance(request_id, str) or not _ARTIFACT_KEY.fullmatch(request_id) or not isinstance(record, dict):
            findings.append(
                _inventory_finding(
                    "packet_record_invalid",
                    "handoff state record has an invalid request identity or shape",
                    subject=subject,
                    governing_asset="schemas/state.schema.json",
                )
            )
            continue
        request_digest = record.get("request_digest")
        if request_digest is not None:
            request_path = f"packets/S12/{request_id}.json"
            expected_files.add(request_path)
            request = _parse_inventory_document(
                capture,
                request_path,
                findings,
                code="request_packet",
                governing_asset="schemas/handoff-request.schema.json",
            )
            if isinstance(request, dict):
                requests[request_id] = request
                _, historical_bundle, creation_problem = \
                    _request_creation_context(replayed, request)
                request_bundle = historical_bundle or bundle
                request_bundles[request_id] = request_bundle
                if creation_problem is not None:
                    code, message = creation_problem
                    findings.append(
                        _inventory_finding(
                            code,
                            message,
                            subject=request_id,
                            governing_asset="schemas/handoff-request.schema.json",
                        )
                    )
                if digest(request) != request_digest:
                    findings.append(
                        _inventory_finding(
                            "request_packet_digest_mismatch",
                            "S12 bytes differ from the authenticated request digest",
                            subject=request_id,
                            governing_asset="schemas/handoff-request.schema.json",
                        )
                    )
                findings.extend(
                    validate_schema(
                        request,
                        request_bundle.document("schemas/handoff-request.schema.json"),
                        subject=request_id,
                        governing_asset="schemas/handoff-request.schema.json",
                        registry=request_bundle.documents,
                    )
                )
                exact_fields = (
                    "request_id",
                    "operation",
                    "domain",
                    "binding_identity",
                    "binding_skill_digest",
                    "adapter_contract_digest",
                    "binding_key",
                    "gate",
                    "step",
                    "subjects",
                    "idempotency_key",
                )
                if request.get("workspace_id") != state.get("workspace_id") or any(
                    key in record and request.get(key) != record.get(key)
                    for key in exact_fields
                ):
                    findings.append(
                        _inventory_finding(
                            "request_packet_subject_mismatch",
                            "S12 anchors do not exactly match the authenticated handoff state",
                            subject=request_id,
                            governing_asset="schemas/handoff-request.schema.json",
                        )
                    )
        result_digest = record.get("result_digest")
        if result_digest is not None:
            result_path = f"packets/S13/{request_id}.json"
            expected_files.add(result_path)
            result = _parse_inventory_document(
                capture,
                result_path,
                findings,
                code="result_packet",
                governing_asset="schemas/handoff-result.schema.json",
            )
            if isinstance(result, dict):
                if digest(result) != result_digest:
                    findings.append(
                        _inventory_finding(
                            "result_packet_digest_mismatch",
                            "S13 bytes differ from the authenticated result digest",
                            subject=request_id,
                            governing_asset="schemas/handoff-result.schema.json",
                        )
                    )
                findings.extend(
                    validate_schema(
                        result,
                        request_bundles.get(request_id, bundle).document(
                            "schemas/handoff-result.schema.json"
                        ),
                        subject=request_id,
                        governing_asset="schemas/handoff-result.schema.json",
                        registry=request_bundles.get(
                            request_id, bundle
                        ).documents,
                    )
                )
                request = requests.get(request_id)
                repeated = (
                    "request_id",
                    "operation",
                    "binding_identity",
                    "binding_skill_digest",
                    "adapter_contract_digest",
                    "workspace_id",
                    "workspace_revision",
                    "gate",
                    "step",
                    "subjects",
                    "idempotency_key",
                )
                if request is None or any(result.get(key) != request.get(key) for key in repeated):
                    findings.append(
                        _inventory_finding(
                            "result_packet_subject_mismatch",
                            "S13 does not exactly repeat the authenticated S12 anchors",
                            subject=request_id,
                            governing_asset="schemas/handoff-result.schema.json",
                        )
                    )
                if request is not None:
                    _, producer_problem = _authenticate_inventory_result(
                        replayed,
                        request,
                        result,
                        authenticated_result_digest=result_digest,
                        event_types={"handoff.result-ingested", "handoff.reconciled"},
                    )
                    if producer_problem is not None:
                        code, message = producer_problem
                        findings.append(
                            _inventory_finding(
                                code,
                                message,
                                subject=request_id,
                                governing_asset="schemas/handoff-result.schema.json",
                            )
                        )
    for event in replayed.events:
        payload = event.get("payload", {})
        if (
            event.get("event_type") != "handoff.reconciliation-required"
            or payload.get("reason") != "late_result"
        ):
            continue
        request_id = payload.get("request_id")
        result_digest = payload.get("result_digest")
        if not isinstance(request_id, str) or not _ARTIFACT_KEY.fullmatch(request_id) or not isinstance(result_digest, str) or not _CANONICAL_DIGEST.fullmatch(result_digest):
            findings.append(
                _inventory_finding(
                    "late_packet_record_invalid",
                    "late-result event lacks a portable request ID or canonical digest",
                    subject=f"event:{event.get('sequence')}",
                    governing_asset="schemas/event.schema.json",
                )
            )
            continue
        late_path = f"packets/S13-late/{request_id}-{result_digest.split(':', 1)[1]}.json"
        expected_files.add(late_path)
        result = _parse_inventory_document(
            capture,
            late_path,
            findings,
            code="late_result_packet",
            governing_asset="schemas/handoff-result.schema.json",
        )
        if isinstance(result, dict):
            if digest(result) != result_digest:
                findings.append(
                    _inventory_finding(
                        "late_result_packet_digest_mismatch",
                        "late S13 bytes differ from the authenticated reconciliation digest",
                        subject=request_id,
                        governing_asset="schemas/handoff-result.schema.json",
                    )
                )
            findings.extend(
                validate_schema(
                    result,
                    request_bundles.get(request_id, bundle).document(
                        "schemas/handoff-result.schema.json"
                    ),
                    subject=request_id,
                    governing_asset="schemas/handoff-result.schema.json",
                    registry=request_bundles.get(request_id, bundle).documents,
                )
            )
            request = requests.get(request_id)
            if request is None:
                findings.append(
                    _inventory_finding(
                        "late_result_request_missing",
                        "late S13 has no authenticated S12 request",
                        subject=request_id,
                        governing_asset="schemas/handoff-request.schema.json",
                    )
                )
            else:
                _, producer_problem = _authenticate_inventory_result(
                    replayed,
                    request,
                    result,
                    authenticated_result_digest=result_digest,
                    event_types={"handoff.reconciliation-required"},
                    event_reason="late_result",
                )
                if producer_problem is not None:
                    code, message = producer_problem
                    findings.append(
                        _inventory_finding(
                            code,
                            message,
                            subject=request_id,
                            governing_asset="schemas/handoff-result.schema.json",
                        )
                    )
    return findings, expected_files




def _validate_authority_artifact_bindings(
    capture: _ValidationCapture,
    replayed: ReplayResult,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Reconstruct signed candidates from the exact persisted final bytes.

    Signature replay alone proves that an authority signed a subject.  This
    pass proves that the subject is the one which produced the immutable
    artifacts later projected by that event.
    """

    findings: list[dict[str, Any]] = []

    def reject(event: dict[str, Any], message: str) -> None:
        findings.append(_inventory_finding(
            "authority_artifact_binding_mismatch",
            message,
            subject=f"event:{event.get('sequence')}",
            governing_asset="authority-policy.json",
        ))

    def read_descriptor(
        event: dict[str, Any], descriptor: dict[str, Any],
    ) -> dict[str, Any] | None:
        relative = descriptor.get("path")
        if not isinstance(relative, str):
            reject(event, "authority-bound artifact descriptor lacks a path")
            return None
        value = _parse_inventory_document(
            capture, relative, findings,
            code="authority_artifact",
            governing_asset="authority-policy.json",
        )
        if not isinstance(value, dict) or digest(value) != descriptor.get("digest"):
            reject(event, "authority-bound artifact bytes differ from their final descriptor")
            return None
        return value

    def unsigned_candidate(
        event: dict[str, Any], value: dict[str, Any],
        expected_authority: list[str],
    ) -> dict[str, Any] | None:
        content = value.get("content")
        if not isinstance(content, dict) or "authority" not in content:
            return deepcopy(value)
        if content.get("authority") != expected_authority:
            reject(event, "final artifact authority field differs from consumed attestations")
            return None
        candidate = deepcopy(value)
        candidate_content = deepcopy(content)
        candidate_content["authority"] = []
        candidate["content"] = candidate_content
        candidate["content_digest"] = digest(candidate_content)
        return candidate

    def stored_digest(artifact_id: str, instance_key: Any) -> str | None:
        if not isinstance(instance_key, str):
            return None
        record = state.get("artifacts", {}).get(artifact_id, {}).get(instance_key)
        if not isinstance(record, dict):
            return None
        relative = record.get("path")
        if not isinstance(relative, str):
            return None
        value = _parse_inventory_document(
            capture, relative, findings,
            code="authority_artifact",
            governing_asset="authority-policy.json",
        )
        return digest(value) if isinstance(value, dict) else None

    def exact_descriptor_value(
        event: dict[str, Any], descriptor: dict[str, Any], artifact_id: str,
        *, instance_key: str | None = None,
    ) -> dict[str, Any] | None:
        actual_instance = descriptor.get("instance_key")
        expected_path = (
            f"artifacts/{artifact_id}/{actual_instance}.json"
            if isinstance(actual_instance, str) else None
        )
        if (
            descriptor.get("artifact_id") != artifact_id
            or not isinstance(actual_instance, str)
            or not _ARTIFACT_KEY.fullmatch(actual_instance)
            or (instance_key is not None and actual_instance != instance_key)
            or descriptor.get("path") != expected_path
        ):
            reject(
                event,
                f"authority action does not bind one exact {artifact_id} instance/path",
            )
            return None
        value = read_descriptor(event, descriptor)
        projected = state.get("artifacts", {}).get(artifact_id, {}).get(
            actual_instance,
        )
        if (
            not isinstance(value, dict)
            or value.get("artifact_type") != artifact_id
            or value.get("instance_key") != actual_instance
            or not isinstance(projected, dict)
            or projected.get("path") != expected_path
            or projected.get("digest") != descriptor.get("digest")
            or projected.get("event_hash") != event.get("event_hash")
        ):
            reject(
                event,
                f"persisted {artifact_id} bytes/projection substitute the authority-bound instance",
            )
            return None
        return value

    events_by_hash = {
        event.get("event_hash"): event for event in replayed.events
        if isinstance(event.get("event_hash"), str)
    }

    def stored_artifact(
        event: dict[str, Any], artifact_id: str, instance_key: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if (
            not isinstance(instance_key, str)
            or not _ARTIFACT_KEY.fullmatch(instance_key)
        ):
            reject(event, f"signed source {artifact_id} has an invalid identity")
            return None
        expected_path = f"artifacts/{artifact_id}/{instance_key}.json"
        record = state.get("artifacts", {}).get(artifact_id, {}).get(instance_key)
        if not isinstance(record, dict) or record.get("path") != expected_path:
            reject(event, f"signed source {artifact_id}:{instance_key} is absent")
            return None
        value = _parse_inventory_document(
            capture, expected_path, findings,
            code="authority_artifact",
            governing_asset="authority-policy.json",
        )
        if (
            not isinstance(value, dict)
            or digest(value) != record.get("digest")
            or value.get("artifact_type") != artifact_id
            or value.get("instance_key") != instance_key
            or not isinstance(events_by_hash.get(record.get("event_hash")), dict)
            or events_by_hash[record["event_hash"]].get("sequence", 0)
            >= event.get("sequence", 0)
        ):
            reject(
                event,
                f"signed source {artifact_id}:{instance_key} bytes do not match its authenticated record",
            )
            return None
        return value, record

    def decision_source_closure(
        event: dict[str, Any], signed_ids: dict[str, Any],
    ) -> dict[str, Any] | None:
        if set(signed_ids) != {
            "workspace_id", "experiment_id", "evidence_id", "decision_id",
            "outcome",
        } or any(
            not isinstance(signed_ids.get(key), str)
            or not signed_ids[key]
            for key in (
                "workspace_id", "experiment_id", "evidence_id",
                "decision_id", "outcome",
            )
        ):
            reject(event, "D05 signed governed identities are not exact")
            return None
        experiment_id = signed_ids["experiment_id"]
        evidence_id = signed_ids["evidence_id"]
        frozen_instance = f"{experiment_id}.frozen"
        frozen_source = stored_artifact(event, "E01", frozen_instance)
        if frozen_source is None:
            return None
        frozen, frozen_record = frozen_source
        frozen_content = frozen.get("content")
        frozen_rules = frozen_content.get("decision_rule") \
            if isinstance(frozen_content, dict) else None
        run_plan_id = frozen_content.get("run_plan_id") \
            if isinstance(frozen_content, dict) else None
        frozen_sources = frozen_content.get("source_refs") \
            if isinstance(frozen_content, dict) else None
        if (
            not isinstance(frozen_rules, dict)
            or not isinstance(run_plan_id, str)
            or not isinstance(frozen_sources, dict)
            or not frozen_sources
            or any(
                not isinstance(node, str)
                or node.count(":") != 1
                or not _ARTIFACT_KEY.fullmatch(node.split(":", 1)[1])
                or not isinstance(bound, str)
                or not _CANONICAL_DIGEST.fullmatch(bound)
                for node, bound in frozen_sources.items()
            )
        ):
            reject(event, "D05 frozen E01 lacks its exact rule/run-plan binding")
            return None
        run_plan_source = stored_artifact(event, "E04", run_plan_id)
        if run_plan_source is None:
            return None
        run_plan, run_plan_record = run_plan_source
        plan = run_plan.get("content")
        planned_rows = plan.get("runs") if isinstance(plan, dict) else None
        if (
            not isinstance(plan, dict)
            or plan.get("run_plan_id") != run_plan_id
            or plan.get("experiment_id") != experiment_id
            or plan.get("candidate_spec_digest")
            != candidate_digest(frozen.get("content", frozen))
            or not isinstance(planned_rows, list)
            or not planned_rows
        ):
            reject(event, "D05 E04 does not bind the exact frozen experiment")
            return None
        plan_by_id: dict[str, dict[str, Any]] = {}
        for row in planned_rows:
            run_id = row.get("run_id") if isinstance(row, dict) else None
            if (
                not isinstance(run_id, str)
                or run_id in plan_by_id
            ):
                reject(event, "D05 E04 Run identities are not exact and unique")
                return None
            plan_by_id[run_id] = row

        evidence_values: dict[str, dict[str, Any]] = {}
        evidence_records: dict[str, dict[str, Any]] = {}
        source_digests: dict[str, str] = {
            "E01": frozen_record["digest"],
            "E04": run_plan_record["digest"],
        }
        for artifact_id in ("D01", "D02", "D03", "D04"):
            source = stored_artifact(event, artifact_id, evidence_id)
            if source is None:
                return None
            value, record = source
            content = value.get("content")
            if not isinstance(content, dict):
                reject(event, f"D05 source {artifact_id} lacks content")
                return None
            evidence_values[artifact_id] = content
            evidence_records[artifact_id] = record
            source_digests[artifact_id] = record["digest"]
        d01 = evidence_values["D01"]
        planned_runs = d01.get("planned_runs")
        declared_attempts = d01.get("accounted_attempts")
        if (
            d01.get("evidence_id") != evidence_id
            or d01.get("experiment_id") != experiment_id
            or not isinstance(planned_runs, list)
            or not planned_runs
            or not isinstance(declared_attempts, list)
        ):
            reject(event, "D05 D01 does not bind its exact Experiment/evidence inventory")
            return None
        declared_by_id: dict[str, dict[str, Any]] = {}
        for row in declared_attempts:
            attempt_id = row.get("attempt_id") if isinstance(row, dict) else None
            if not isinstance(attempt_id, str) or attempt_id in declared_by_id:
                reject(event, "D05 D01 Attempt identities are not exact and unique")
                return None
            declared_by_id[attempt_id] = row

        exact_runs: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []
        required_attempts: set[str] = set()
        for run in planned_runs:
            run_id = run.get("run_id") if isinstance(run, dict) else None
            attempt_ids = run.get("required_attempt_ids") \
                if isinstance(run, dict) else None
            if (
                not isinstance(run_id, str)
                or not isinstance(attempt_ids, list)
                or not attempt_ids
                or any(not isinstance(item, str) for item in attempt_ids)
                or len(attempt_ids) != len(set(attempt_ids))
            ):
                reject(event, "D05 D01 Run/Attempt plan is not exact")
                return None
            planned = plan_by_id.get(run_id)
            r01_source = stored_artifact(event, "R01", run_id)
            if planned is None or r01_source is None:
                reject(event, f"D05 Run {run_id} is absent from its frozen plan")
                return None
            r01, r01_record = r01_source
            r01_content = r01.get("content")
            planned_attempt_ids = planned.get("required_attempt_ids")
            manifest_attempt_ids = r01_content.get("required_attempt_ids") \
                if isinstance(r01_content, dict) else None
            if (
                not isinstance(r01_content, dict)
                or not isinstance(planned_attempt_ids, list)
                or any(
                    not isinstance(item, str) for item in planned_attempt_ids
                )
                or not isinstance(manifest_attempt_ids, list)
                or any(
                    not isinstance(item, str) for item in manifest_attempt_ids
                )
                or r01_content.get("run_id") != run_id
                or r01_content.get("experiment_id") != experiment_id
                or r01_content.get("experiment_digest")
                != frozen_record["digest"]
                or r01_content.get("frozen_spec_digest")
                != frozen_record["digest"]
                or r01_content.get("run_plan_id") != run_plan_id
                or r01_content.get("run_plan_digest")
                != run_plan_record["digest"]
                or sorted(attempt_ids)
                != sorted(planned_attempt_ids)
                or sorted(attempt_ids)
                != sorted(manifest_attempt_ids)
            ):
                reject(event, f"D05 R01:{run_id} substitutes its frozen plan")
                return None
            preflight_instance = r01_content.get("preflight_instance_key")
            preflight_source = stored_artifact(
                event, "S09", preflight_instance,
            )
            if preflight_source is None:
                return None
            preflight, preflight_record = preflight_source
            preflight_content = preflight.get("content")
            if (
                not isinstance(preflight_content, dict)
                or r01_content.get("preflight_digest")
                != preflight_record["digest"]
                or preflight_content.get("experiment_digest")
                != frozen_record["digest"]
                or preflight_content.get("revision")
                != r01_content.get("implementation_revision")
            ):
                reject(event, f"D05 R01:{run_id} substitutes its exact S09")
                return None
            source_digests[f"R01:{run_id}"] = r01_record["digest"]
            source_digests[
                f"S09:{preflight_instance}"
            ] = preflight_record["digest"]
            exact_runs.append({
                "run_id": run_id,
                "required_attempt_ids": sorted(attempt_ids),
            })
            for attempt_id in attempt_ids:
                if attempt_id in required_attempts:
                    reject(event, "D05 Attempt occurs under multiple Runs")
                    return None
                required_attempts.add(attempt_id)
                declared = declared_by_id.get(attempt_id)
                r07_source = stored_artifact(event, "R07", attempt_id)
                r08_source = stored_artifact(event, "R08", attempt_id)
                if (
                    declared is None
                    or declared.get("run_id") != run_id
                    or r07_source is None
                    or r08_source is None
                ):
                    reject(event, f"D05 Attempt {attempt_id} is not exactly accounted")
                    return None
                r07, r07_record = r07_source
                r08, r08_record = r08_source
                actual = r07.get("content")
                index = r08.get("content")
                terminal_status = actual.get("status") \
                    if isinstance(actual, dict) else None
                validity = actual.get("validity") \
                    if isinstance(actual, dict) else None
                system_failure = actual.get("system_failure") \
                    if isinstance(actual, dict) else None
                if (
                    not isinstance(actual, dict)
                    or not isinstance(index, dict)
                    or actual.get("run_id") != run_id
                    or actual.get("attempt_id") != attempt_id
                    or index.get("run_id") != run_id
                    or index.get("attempt_id") != attempt_id
                    or terminal_status not in {
                        "successful", "failed", "cancelled", "invalid",
                    }
                    or validity not in {"valid", "invalid"}
                    or not isinstance(system_failure, bool)
                    or declared.get("accounted") is not True
                    or declared.get("terminal_status") != terminal_status
                    or declared.get("validity") != validity
                    or declared.get("system_failure") != system_failure
                ):
                    reject(event, f"D05 Attempt {attempt_id} substitutes terminal facts")
                    return None
                attempts.append({
                    "attempt_id": attempt_id,
                    "run_id": run_id,
                    "terminal_status": terminal_status,
                    "accounted": True,
                    "validity": validity,
                    "system_failure": system_failure,
                })
                source_digests[f"R07:{attempt_id}"] = r07_record["digest"]
                source_digests[f"R08:{attempt_id}"] = r08_record["digest"]
        if (
            set(plan_by_id) != {row["run_id"] for row in exact_runs}
            or set(declared_by_id) != required_attempts
        ):
            reject(event, "D05 source Run/Attempt sets are not closed")
            return None
        build_sources = {
            f"E01:{frozen_instance}": frozen_record["digest"],
            f"E04:{run_plan_id}": run_plan_record["digest"],
            **frozen_sources,
            **{
                key: value for key, value in source_digests.items()
                if ":" in key
            },
        }
        evidence_event_hashes = {
            record.get("event_hash") for record in evidence_records.values()
        }
        evidence_event = events_by_hash.get(next(iter(evidence_event_hashes))) \
            if len(evidence_event_hashes) == 1 else None
        evidence_items = d01.get("items")
        if (
            not isinstance(evidence_items, list)
            or any(not isinstance(item, str) for item in evidence_items)
            or sorted(evidence_items) != sorted(build_sources)
            or d01.get("bundle_digest") != digest(build_sources)
            or not isinstance(evidence_event, dict)
            or evidence_event.get("event_type") != "evidence.built"
            or evidence_event.get("payload", {}).get("complete") is not True
            or evidence_event.get("payload", {}).get("source_digests")
            != build_sources
        ):
            reject(event, "D05 evidence sources lack one exact complete build closure")
            return None
        evidence = evidence_values["D03"].get("scientific_facts")
        context = {
            "frozen_rules": frozen_rules,
            "planned_runs": exact_runs,
            "attempts": sorted(attempts, key=lambda item: item["attempt_id"]),
            "evidence": evidence,
        }
        try:
            evaluated = evaluate_scientific_decision(
                frozen_rules, exact_runs, context["attempts"], evidence,
            )
        except ContractError:
            reject(event, "D05 source facts do not execute under the frozen rule")
            return None
        evidence_bundle_digest = digest({
            key: source_digests[key] for key in sorted(source_digests)
            if key.startswith("D") or key.startswith("R")
        })
        return {
            "context": context,
            "frozen_spec_digest": frozen_record["digest"],
            "evidence_bundle_digest": evidence_bundle_digest,
            "rule_digest": evaluated["rule_digest"],
            "outcome": evaluated["outcome"],
            "parents": {
                f"E01:{frozen_instance}",
                *{
                    f"{artifact_id}:{evidence_id}"
                    for artifact_id in ("D01", "D02", "D03", "D04")
                },
            },
        }

    for event in replayed.events:
        payload = event.get("payload", {})
        action = payload.get("authority_action")
        if not isinstance(action, str) or not action:
            continue
        authority_records = event.get("authority", [])
        attestation_digests = sorted(
            row.get("attestation_digest")
            for row in authority_records
            if isinstance(row, dict)
            and isinstance(row.get("attestation_digest"), str)
        )
        subject = payload.get("authority_subject", {})
        signed_ids = subject.get("governed_ids", {}) \
            if isinstance(subject, dict) else {}
        signed_digests = subject.get("digests", {}) \
            if isinstance(subject, dict) else {}
        signed_ids = signed_ids if isinstance(signed_ids, dict) else {}
        signed_digests = signed_digests \
            if isinstance(signed_digests, dict) else {}
        raw_descriptors = payload.get("published_artifacts", [])
        descriptor_shape_valid = isinstance(raw_descriptors, list)
        descriptors = [
            row for row in raw_descriptors
            if isinstance(row, dict)
        ] if descriptor_shape_valid else []
        descriptor_shape_valid = descriptor_shape_valid \
            and len(descriptors) == len(raw_descriptors)

        if action == "freeze.approve" and event.get("event_type") \
                == "protocol.freeze-provisional":
            if len(descriptors) != 1:
                reject(event, "freeze authority must bind one exact frozen E01")
                continue
            frozen = read_descriptor(event, descriptors[0])
            extension = frozen.get("extensions", {}).get(
                "dev.bmad.sdm.freeze", {}
            ) if isinstance(frozen, dict) else {}
            content = frozen.get("content") if isinstance(frozen, dict) else None
            if (
                not isinstance(content, dict)
                or candidate_digest(content) != signed_digests.get("candidate_digest")
                or payload.get("candidate_digest")
                != signed_digests.get("candidate_digest")
                or payload.get("frozen_digest") != digest(frozen)
                or extension.get("candidate_digest")
                != signed_digests.get("candidate_digest")
                or extension.get("freeze_attestations") != attestation_digests
                or extension.get("event_revision") != event.get("sequence")
            ):
                reject(event, "frozen E01 bytes do not reconstruct the signed candidate")
            continue

        if action == "decision.D05" and event.get("event_type") \
                == "decision.provisional":
            experiment_id = signed_ids.get("experiment_id")
            evidence_id = signed_ids.get("evidence_id")
            decision_id = signed_ids.get("decision_id")
            expected_instance = (
                f"{experiment_id}.{decision_id}"
                if isinstance(experiment_id, str)
                and isinstance(decision_id, str) else None
            )
            if (
                not descriptor_shape_valid
                or len(descriptors) != 1
                or expected_instance is None
            ):
                reject(event, "decision.D05 must bind one exact final D05")
                continue
            final = exact_descriptor_value(
                event, descriptors[0], "D05", instance_key=expected_instance,
            )
            closure = decision_source_closure(event, signed_ids)
            final_content = final.get("content") \
                if isinstance(final, dict) else None
            expected_event_subjects = {
                "workspace_id": signed_ids.get("workspace_id"),
                "experiment_id": experiment_id,
                "evidence_id": evidence_id,
                "decision_id": decision_id,
                "artifact_id": f"D05:{expected_instance}",
            }
            expected_envelope_subjects = {
                "workspace_id": signed_ids.get("workspace_id"),
                "experiment_id": experiment_id,
            }
            expected_reviewers = sorted({
                str(row.get("principal_id") or row.get("role")
                    or "scientific-reviewer")
                for row in authority_records if isinstance(row, dict)
            })
            expected_authority = sorted(set(attestation_digests))
            descriptor_parents = descriptors[0].get("parents")
            if (
                final is None
                or closure is None
                or not isinstance(final_content, dict)
                or set(signed_digests) != {
                    "frozen_spec_digest", "evidence_bundle_digest",
                    "rule_digest",
                }
                or event.get("subjects") != expected_event_subjects
                or final.get("artifact_id") != decision_id
                or final.get("subjects") != expected_envelope_subjects
                or final.get("status") != "published"
                or descriptors[0].get("status") != "published"
                or not isinstance(descriptor_parents, list)
                or len(descriptor_parents) != len(closure["parents"])
                or set(descriptor_parents) != closure["parents"]
                or final_content.get("decision_id") != decision_id
                or final_content.get("experiment_id") != experiment_id
                or final_content.get("frozen_spec_digest")
                != signed_digests.get("frozen_spec_digest")
                or signed_digests.get("frozen_spec_digest")
                != closure["frozen_spec_digest"]
                or final_content.get("evidence_bundle_digest")
                != signed_digests.get("evidence_bundle_digest")
                or signed_digests.get("evidence_bundle_digest")
                != closure["evidence_bundle_digest"]
                or final_content.get("rule_digest")
                != signed_digests.get("rule_digest")
                or signed_digests.get("rule_digest")
                != closure["rule_digest"]
                or final_content.get("outcome") != signed_ids.get("outcome")
                or signed_ids.get("outcome") != closure["outcome"]
                or payload.get("outcome") != closure["outcome"]
                or final_content.get("decision_context") != closure["context"]
                or final_content.get("reviewers") != expected_reviewers
                or final_content.get("authority") != expected_authority
            ):
                reject(
                    event,
                    "final D05 does not reconstruct its signed frozen/evidence/rule/outcome source closure",
                )
            continue

        interrupt_binding = {
            "amendment.approve": ("E06", "E06_candidate_digest"),
            "deviation.accept": ("E07", "E07_candidate_digest"),
            "lifecycle.cancel": ("S16", "S16_candidate_digest"),
            "exception.grant": ("S15", "S15_candidate_digest"),
        }.get(action)
        if interrupt_binding is not None:
            artifact_id, candidate_key = interrupt_binding
            if not descriptor_shape_valid or len(descriptors) != 1:
                reject(
                    event,
                    f"{action} must bind one exact final {artifact_id}",
                )
                continue
            final = exact_descriptor_value(
                event, descriptors[0], artifact_id,
            )
            candidate = unsigned_candidate(
                event, final, attestation_digests,
            ) if final is not None else None
            final_content = final.get("content") \
                if isinstance(final, dict) else None
            if (
                candidate is None
                or set(signed_digests) != {candidate_key}
                or digest(candidate) != signed_digests.get(candidate_key)
                or payload.get("authority_candidate_digest")
                != signed_digests.get(candidate_key)
                or payload.get("final_record_digest") != digest(final)
                or final.get("subjects") != event.get("subjects")
                or (
                    artifact_id in {"E06", "E07"}
                    and (
                        not isinstance(final_content, dict)
                        or final_content.get("experiment_id")
                        != signed_ids.get("experiment_id")
                    )
                )
                or (
                    artifact_id in {"S15", "S16"}
                    and (
                        not isinstance(final_content, dict)
                        or final_content.get("subjects")
                        != event.get("subjects")
                    )
                )
            ):
                reject(
                    event,
                    f"final {artifact_id} does not reconstruct its action-specific signed candidate",
                )
            continue

        if action == "readiness.approve":
            expected_artifact_ids = {
                "baseline.lock": {"B02", "B03"},
                "protocol.readiness": {"E02", "S04"},
                "evidence.review": {"S10"},
            }.get(payload.get("transition"))
            candidates: dict[str, str] = {}
            valid = expected_artifact_ids is not None \
                and set(signed_digests) == {"candidate_records_digest"} \
                and descriptor_shape_valid \
                and len(descriptors) == len(expected_artifact_ids)
            descriptor_nodes: set[tuple[str, str]] = set()
            for descriptor in descriptors:
                artifact_id = descriptor.get("artifact_id")
                instance_key = descriptor.get("instance_key")
                if (
                    not isinstance(artifact_id, str)
                    or not isinstance(instance_key, str)
                    or (artifact_id, instance_key) in descriptor_nodes
                ):
                    valid = False
                    continue
                descriptor_nodes.add((artifact_id, instance_key))
                final = exact_descriptor_value(
                    event, descriptor, artifact_id,
                )
                candidate = unsigned_candidate(
                    event, final, attestation_digests,
                ) if final is not None else None
                if candidate is None:
                    valid = False
                    continue
                node = f"{artifact_id}:{instance_key}"
                candidates[node] = digest(candidate)
            if (
                not valid
                or {artifact_id for artifact_id, _ in descriptor_nodes}
                != expected_artifact_ids
                or digest(candidates)
                != signed_digests.get("candidate_records_digest")
                or payload.get("authority_candidate_records_digest")
                != signed_digests.get("candidate_records_digest")
            ):
                reject(
                    event,
                    "final step records do not reconstruct the transition-specific signed candidate set",
                )
            continue

        if action == "lifecycle.close":
            candidate_key = "S17_candidate_digest"
            if candidate_key in signed_digests:
                if len(descriptors) != 1:
                    reject(event, "archive authority must bind one exact final S17")
                    continue
                final = read_descriptor(event, descriptors[0])
                candidate = unsigned_candidate(
                    event, final, attestation_digests,
                ) if final is not None else None
                if (
                    candidate is None
                    or digest(candidate) != signed_digests[candidate_key]
                    or payload.get("final_record_digest") != digest(final)
                ):
                    reject(event, "final S17 does not reconstruct its signed archive candidate")
            elif "S17_digest" in signed_digests:
                actual = stored_digest(
                    "S17", payload.get("closure_instance_key"),
                )
                if actual != signed_digests["S17_digest"]:
                    reject(event, "close authority does not bind the exact stored S17")
            continue

        if action == "experiment.supersede":
            by_type = {row.get("artifact_id"): row for row in descriptors}
            successor = by_type.get("E01")
            brief = by_type.get("S02")
            successor_value = read_descriptor(event, successor) \
                if isinstance(successor, dict) else None
            brief_value = read_descriptor(event, brief) \
                if isinstance(brief, dict) else None
            predecessor = stored_digest(
                "E01", payload.get("predecessor_instance_key"),
            )
            predecessor_decision = stored_digest(
                "D05", payload.get("predecessor_decision_instance_key"),
            )
            if (
                not isinstance(successor_value, dict)
                or not isinstance(brief_value, dict)
                or digest(successor_value) != signed_digests.get("successor_digest")
                or digest(brief_value) != signed_digests.get("successor_brief_digest")
                or predecessor != signed_digests.get("predecessor_digest")
                or predecessor_decision
                != signed_digests.get("predecessor_decision_digest")
            ):
                reject(event, "experiment supersession records differ from the signed predecessor/successor closure")
            continue

        if action == "decision.supersede":
            if len(descriptors) != 1 or descriptors[0].get("artifact_id") != "D05":
                reject(event, "decision supersession must bind one exact successor D05")
                continue
            final = read_descriptor(event, descriptors[0])
            candidate = unsigned_candidate(
                event, final, attestation_digests,
            ) if final is not None else None
            predecessor = stored_digest(
                "D05", payload.get("predecessor_decision_instance_key"),
            )
            final_content = final.get("content") \
                if isinstance(final, dict) else None
            if (
                candidate is None
                or digest(candidate) != signed_digests.get("D05_candidate_digest")
                or predecessor
                != signed_digests.get("predecessor_D05_digest")
                or payload.get("authority_candidate_digest")
                != signed_digests.get("D05_candidate_digest")
                or payload.get("final_record_digest") != digest(final)
                or payload.get("authority_bound_digests") != signed_digests
                or not isinstance(final_content, dict)
                or final_content.get("supersession_reason")
                != payload.get("supersession_reason")
                or payload.get("outcome") != final_content.get("outcome")
            ):
                reject(event, "decision supersession effect differs from its signed old/new D05 evidence closure")
            continue

        if action == "baseline.deprecate":
            if len(descriptors) != 1 or descriptors[0].get("artifact_id") != "B04":
                reject(event, "baseline deprecation must bind one exact final B04")
                continue
            final = read_descriptor(event, descriptors[0])
            candidate = unsigned_candidate(
                event, final, attestation_digests,
            ) if final is not None else None
            subject_ids = subject.get("governed_ids", {}) \
                if isinstance(subject, dict) else {}
            baseline_id = subject_ids.get("baseline_id")
            replacement_id = subject_ids.get("replacement_baseline_id")
            source_records: dict[str, str] = {}
            replacement_records: dict[str, str] = {}
            for artifact_id in ("B01", "B02"):
                for instance_key, projected in state.get("artifacts", {}).get(
                    artifact_id, {},
                ).items():
                    stored = stored_digest(artifact_id, instance_key)
                    if stored is None:
                        continue
                    relative = projected.get("path") if isinstance(projected, dict) else None
                    value = _parse_inventory_document(
                        capture, relative, findings,
                        code="authority_artifact",
                        governing_asset="authority-policy.json",
                    ) if isinstance(relative, str) else None
                    content = value.get("content", value) \
                        if isinstance(value, dict) else {}
                    subjects = value.get("subjects", {}) \
                        if isinstance(value, dict) else {}
                    identities = {
                        instance_key,
                        content.get("baseline_id")
                        if isinstance(content, dict) else None,
                        subjects.get("baseline_id")
                        if isinstance(subjects, dict) else None,
                    }
                    node = f"{artifact_id}:{instance_key}"
                    if baseline_id in identities:
                        source_records[node] = stored
                    if replacement_id in identities:
                        replacement_records[node] = stored
            dependents = payload.get("active_dependents")
            final_content = final.get("content") \
                if isinstance(final, dict) else None
            if (
                candidate is None
                or digest(candidate) != signed_digests.get("B04_candidate_digest")
                or digest(source_records)
                != signed_digests.get("source_baseline_records_digest")
                or digest(replacement_records)
                != signed_digests.get("replacement_baseline_records_digest")
                or not isinstance(dependents, list)
                or digest(dependents)
                != signed_digests.get("active_dependents_digest")
                or payload.get("authority_candidate_digest")
                != signed_digests.get("B04_candidate_digest")
                or payload.get("final_record_digest") != digest(final)
                or payload.get("authority_bound_digests") != signed_digests
                or not isinstance(final_content, dict)
                or final_content.get("baseline_id") != baseline_id
                or final_content.get("replacement") != replacement_id
                or final_content.get("deprecation_id")
                != subject_ids.get("deprecation_id")
                or final_content.get("active_dependents") != dependents
            ):
                reject(event, "baseline deprecation effect differs from its signed B04 and baseline dependency closure")

    return findings


def _validate_complete_inventory(
    capture: _ValidationCapture,
    replayed: ReplayResult | None,
    state: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, tuple[str, str, dict[str, Any], Any]]]:
    findings: list[dict[str, Any]] = []
    records_by_path: dict[str, tuple[str, str, dict[str, Any], Any]] = {}
    expected_dirs = {
        "_ledger",
        "_ledger/events",
        "_engine",
        "artifacts",
        "packets",
        "packets/S12",
        "packets/S13",
        "packets/S13-late",
        "_memlog",
    }
    expected_files = {
        "_lock",
        "_ledger/genesis.json",
        "state.json",
    }
    if replayed is not None:
        bundle = replayed.bundles[replayed.active_engine_digest]
        artifact_ids = [
            row["id"]
            for asset in ("artifact-catalog.json", "supporting-artifact-catalog.json")
            for row in bundle.document(asset)["artifacts"]
        ]
        expected_dirs.update(f"artifacts/{artifact_id}" for artifact_id in artifact_ids)
        for engine_digest, engine_bundle in replayed.bundles.items():
            prefix = f"_engine/{engine_digest}"
            expected_dirs.update({prefix, f"{prefix}/schemas"})
            for relative, canonical in engine_bundle.canonical_assets.items():
                parent = PurePosixPath(relative).parent
                while parent != PurePosixPath("."):
                    expected_dirs.add(f"{prefix}/{parent.as_posix()}")
                    parent = parent.parent
                full_path = f"{prefix}/{relative}"
                expected_files.add(full_path)
                raw = capture.files.get(full_path)
                if raw is not None and raw != canonical:
                    findings.append(
                        _inventory_finding(
                            "pinned_engine_bytes_mismatch",
                            "pinned engine bytes differ from canonical authenticated assets",
                            subject=full_path,
                            governing_asset=relative,
                        )
                    )
        for event in replayed.events:
            event_path = f"_ledger/events/{event['sequence']:020d}.json"
            memlog_path = f"_memlog/{event['sequence']:020d}.json"
            expected_files.update({event_path, memlog_path})
            memlog = _parse_inventory_document(
                capture,
                memlog_path,
                findings,
                code="memlog",
                governing_asset="schemas/event.schema.json",
            )
            if memlog is not None and canonical_bytes(memlog) != canonical_bytes(_memlog(event)):
                findings.append(
                    _inventory_finding(
                        "memlog_mismatch",
                        "memlog does not exactly mirror its authenticated ledger event",
                        subject=memlog_path,
                        governing_asset="schemas/event.schema.json",
                    )
                )
        if state is not None:
            artifact_findings, artifact_files, records_by_path = _validate_artifact_inventory(
                capture, replayed, state
            )
            packet_findings, packet_files = _validate_packet_inventory(
                capture, replayed, state
            )
            findings.extend(artifact_findings)
            findings.extend(packet_findings)
            findings.extend(_validate_authority_artifact_bindings(
                capture, replayed, state,
            ))
            expected_files.update(artifact_files)
            expected_files.update(packet_files)

    actual_dirs = {
        relative
        for relative, metadata in capture.entries.items()
        if metadata.get("type") == "directory"
    }
    actual_files = set(capture.files)
    for relative in sorted(expected_dirs - actual_dirs):
        findings.append(
            _inventory_finding(
                "workspace_required_directory_missing",
                "required governed directory is missing",
                subject=relative,
            )
        )
    for relative in sorted(actual_dirs - expected_dirs):
        findings.append(
            _inventory_finding(
                "workspace_orphan_directory",
                "directory is not part of the authenticated governed inventory",
                subject=relative,
            )
        )
    for relative in sorted(expected_files - actual_files):
        findings.append(
            _inventory_finding(
                "workspace_required_file_missing",
                "required governed file is missing",
                subject=relative,
            )
        )
    for relative in sorted(actual_files - expected_files):
        findings.append(
            _inventory_finding(
                "workspace_untracked_file",
                "file is not bound by the authenticated governed inventory",
                subject=relative,
            )
        )
    return findings, records_by_path


def _selected_artifact_findings(
    capture: _ValidationCapture,
    state: dict[str, Any] | None,
    records_by_path: dict[str, tuple[str, str, dict[str, Any], Any]],
    artifact_path: str | os.PathLike[str] | None,
) -> list[dict[str, Any]]:
    if artifact_path is None:
        return []
    selected = Path(os.path.abspath(os.fspath(artifact_path)))
    try:
        relative_path = selected.relative_to(capture.root_path)
    except ValueError as exc:
        raise WorkspaceError("artifact_escape", "selected artifact must be contained in the workspace") from exc
    relative = relative_path.as_posix()
    if relative not in capture.files:
        raise WorkspaceError("artifact_missing", "selected artifact is not a captured regular workspace file")
    if relative == "state.json":
        if state is None:
            return [
                _inventory_finding(
                    "selected_artifact_unauthenticated",
                    "selected state cannot be authenticated because ledger replay failed",
                    subject=relative,
                    governing_asset="schemas/state.schema.json",
                )
            ]
        return []
    record = records_by_path.get(relative)
    if record is None:
        return [
            _inventory_finding(
                "selected_artifact_untracked",
                "selected --artifact does not name an authenticated governed artifact state record",
                subject=relative,
                governing_asset="artifact-catalog.json",
            )
        ]
    artifact_id, instance_key, authenticated, value = record
    findings: list[dict[str, Any]] = []
    if authenticated.get("path") != relative or digest(value) != authenticated.get("digest"):
        findings.append(
            _inventory_finding(
                "selected_artifact_binding_mismatch",
                "selected artifact path/digest differs from its authenticated state record",
                subject=f"{artifact_id}:{instance_key}",
                governing_asset="artifact-catalog.json",
            )
        )
    if (
        not isinstance(value, dict)
        or value.get("artifact_type") != artifact_id
        or value.get("instance_key") != instance_key
        or not isinstance(value.get("subjects"), dict)
        or value["subjects"].get("workspace_id") != state.get("workspace_id")
    ):
        findings.append(
            _inventory_finding(
                "selected_artifact_subject_mismatch",
                "selected artifact schema subject differs from its authenticated state subject",
                subject=f"{artifact_id}:{instance_key}",
                governing_asset="schemas/artifact.schema.json",
            )
        )
    return findings


def _force_blocking_findings(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        value = deepcopy(item)
        value["gate_impact"] = "block"
        normalized.append(value)
    return sort_findings(normalized)


def _formal_anchor_finding(code: str, message: str) -> dict[str, Any]:
    return _inventory_finding(
        code,
        message,
        subject="workspace",
        governing_asset="platform-operations.json",
    )


def _captured_handoff_authentication(
    capture: _ValidationCapture,
    replayed: ReplayResult,
    state: dict[str, Any],
    request_id: str,
) -> tuple[
    tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
    ] | None,
    str | None,
]:
    """Authenticate one exact S12/S13 pair against the captured ledger.

    A producer envelope is not trusted merely because it parses.  Its exact
    canonical bytes, request and result digests, configured Skill identity,
    pinned Skill/adapter digests, schemas, evidence contract, and creation /
    terminal ledger events must all agree.
    """
    request_relative = f"packets/S12/{request_id}.json"
    result_relative = f"packets/S13/{request_id}.json"
    request_raw = capture.files.get(request_relative)
    result_raw = capture.files.get(result_relative)
    if request_raw is None or result_raw is None:
        return None, "the ledger-bound S12/S13 pair is incomplete"
    try:
        request = parse_document(request_raw)
        result = parse_document(result_raw)
    except ValueError:
        return None, "the ledger-bound S12/S13 pair is not strict JSON"
    if not isinstance(request, dict) or not isinstance(result, dict):
        return None, "the ledger-bound S12/S13 pair is not object-shaped"
    if request_raw != canonical_bytes(request) or result_raw != canonical_bytes(result):
        return None, "the ledger-bound S12/S13 pair is not canonical"

    created, bundle, creation_problem = _request_creation_context(
        replayed, request
    )
    if creation_problem is not None or created is None or bundle is None:
        return None, (
            creation_problem[1]
            if creation_problem is not None
            else "the S12 creation engine is unavailable"
        )
    request_findings = validate_schema(
        request,
        bundle.document("schemas/handoff-request.schema.json"),
        subject=request_id,
        governing_asset="schemas/handoff-request.schema.json",
        registry=bundle.documents,
    )
    result_findings = validate_schema(
        result,
        bundle.document("schemas/handoff-result.schema.json"),
        subject=request_id,
        governing_asset="schemas/handoff-result.schema.json",
        registry=bundle.documents,
    )
    if request_findings or result_findings:
        return None, "the ledger-bound S12/S13 pair is not schema-valid"

    record = state.get("handoffs", {}).get(request_id)
    if (
        not isinstance(record, dict)
        or record.get("request_digest") != digest(request)
        or record.get("result_digest") != digest(result)
        or record.get("status") not in {"successful", "reconciled_effect"}
    ):
        return None, "the S12/S13 pair is not the accepted state-record result"
    repeated = (
        "request_id",
        "operation",
        "binding_identity",
        "binding_skill_digest",
        "adapter_contract_digest",
        "workspace_id",
        "workspace_revision",
        "gate",
        "step",
        "subjects",
        "idempotency_key",
    )
    if any(result.get(key) != request.get(key) for key in repeated):
        return None, "S13 does not exactly repeat every immutable S12 anchor"
    if (
        request.get("request_id") != request_id
        or request.get("workspace_id") != state.get("workspace_id")
        or result.get("terminal_status") != "successful"
        or result.get("producer_identity") != request.get("binding_identity")
        or not isinstance(result.get("producer_principal_id"), str)
        or not result["producer_principal_id"]
        or result.get("producer_principal_id")
        == request.get("binding_identity")
    ):
        return None, "the S13 producer, terminal status, or workspace anchor is invalid"

    try:
        domain, operation_contract, binding_key = bundle.operation_contract(
            str(request.get("operation"))
        )
    except ContractError:
        return None, "the S12 operation is not present in the pinned platform contract"
    expected_evidence = operation_contract.get("required_evidence", [])
    configured_identity = state.get("bindings", {}).get(binding_key)
    configured_digest = state.get("binding_skill_digests", {}).get(binding_key)
    if (
        request.get("domain") != domain
        or request.get("binding_key") != binding_key
        or request.get("binding_identity") != configured_identity
        or not isinstance(configured_digest, str)
        or request.get("binding_skill_digest") != configured_digest
        or request.get("adapter_contract_digest") != digest(operation_contract)
        or request.get("expected_evidence") != expected_evidence
    ):
        return None, "the S12 binding or evidence contract is not the genesis-pinned contract"

    evidence = result.get("evidence")
    if not isinstance(evidence, list) or len(evidence) != len(expected_evidence):
        return None, "the successful S13 evidence set is not exact"
    evidence_values: dict[str, Any] = {}
    for item in evidence:
        if (
            not isinstance(item, dict)
            or set(item) != {"type", "value"}
            or not isinstance(item.get("type"), str)
            or item["type"] in evidence_values
        ):
            return None, "the successful S13 evidence set is duplicated or malformed"
        evidence_values[item["type"]] = item.get("value")
    if set(evidence_values) != set(expected_evidence):
        return None, "the successful S13 evidence set differs from the pinned operation contract"
    if (
        not isinstance(evidence_values.get("operation_receipt_uri"), str)
        or not evidence_values["operation_receipt_uri"]
        or not isinstance(evidence_values.get("operation_receipt_digest"), str)
        or not _CANONICAL_DIGEST.fullmatch(evidence_values["operation_receipt_digest"])
        or evidence_values.get("terminal_status") != "successful"
    ):
        return None, "the S13 operation receipt evidence is incomplete or unsuccessful"

    terminal = next(
        (
            event
            for event in reversed(replayed.events)
            if event.get("event_type") in {"handoff.result-ingested", "handoff.reconciled"}
            and event.get("payload", {}).get("request_id") == request_id
            and event.get("payload", {}).get("result_digest") == digest(result)
        ),
        None,
    )
    if terminal is None or terminal.get("payload", {}).get("terminal_status") != "successful":
        return None, "the S13 digest is not bound by a successful terminal ledger event"
    workspace_created = next(
        (
            event for event in replayed.events
            if event.get("event_type") == "workspace.created"
        ),
        None,
    )
    trust_payload = (
        workspace_created.get("payload", {})
        if isinstance(workspace_created, dict)
        else {}
    )
    trusted_snapshot = trust_payload.get("authority_trust_snapshot")
    trusted_snapshot_digest = trust_payload.get("authority_trust_snapshot_digest")
    consumed_at = terminal.get("payload", {}).get(
        "producer_attestation_consumed_at"
    )
    if (
        not isinstance(trusted_snapshot, dict)
        or not isinstance(trusted_snapshot_digest, str)
        or trusted_snapshot_digest != digest(trusted_snapshot)
        or not isinstance(consumed_at, str)
    ):
        return None, "the accepted S13 lacks coordinator-pinned producer trust metadata"
    try:
        verified_producer = verify_platform_result_attestation(
            request,
            result,
            bundle=bundle,
            trusted_snapshot=trusted_snapshot,
            trusted_snapshot_digest=trusted_snapshot_digest,
            consumption_time=consumed_at,
        )
    except AuthorityError as exc:
        return None, f"the accepted S13 producer attestation is invalid: {exc.code}"
    terminal_payload = terminal.get("payload", {})
    authenticated_fields = {
        "producer_attestation_digest": verified_producer["attestation_digest"],
        "producer_subject_digest": verified_producer["subject_digest"],
        "producer_key_digest": verified_producer["key_digest"],
        "producer_trust_snapshot_digest": verified_producer["trust_snapshot_digest"],
        "producer_attestation_consumed_at": verified_producer["consumed_at"],
        "producer_identity": verified_producer["producer_identity"],
        "producer_principal_id": verified_producer[
            "producer_principal_id"
        ],
    }
    if any(
        terminal_payload.get(key) != value
        for key, value in authenticated_fields.items()
    ):
        return None, "the S13 producer verification record differs from its terminal ledger event"
    return (request, result, evidence_values, created, terminal), None


def _validate_formal_anchor(
    capture: _ValidationCapture,
    replayed: ReplayResult,
    state: dict[str, Any],
    trusted_anchor_path: str | os.PathLike[str],
) -> list[dict[str, Any]]:
    root = os.path.abspath(os.fspath(capture.root_path))
    supplied = os.path.abspath(os.fspath(trusted_anchor_path))
    try:
        contained_path = os.path.commonpath((root, supplied)) == root
    except ValueError:
        contained_path = False
    if not contained_path:
        return [
            _formal_anchor_finding(
                "trusted_anchor_unbound",
                "--trusted-anchor-result must name the exact captured workspace S13",
            )
        ]
    relative = os.path.relpath(supplied, root).replace(os.sep, "/")
    match = re.fullmatch(r"packets/S13/([A-Za-z0-9][A-Za-z0-9._-]{0,127})\.json", relative)
    if match is None or relative not in capture.files:
        return [
            _formal_anchor_finding(
                "trusted_anchor_unbound",
                "--trusted-anchor-result does not name an authenticated captured S13",
            )
        ]
    request_id = match.group(1)
    authenticated, problem = _captured_handoff_authentication(
        capture, replayed, state, request_id
    )
    if authenticated is None:
        return [
            _formal_anchor_finding(
                "trusted_anchor_invalid",
                problem or "trusted anchor S13 authentication failed",
            )
        ]
    request, result, evidence, created, terminal = authenticated
    if request.get("operation") != "git.read-current-ledger-anchor":
        return [
            _formal_anchor_finding(
                "trusted_anchor_operation_mismatch",
                "trusted anchor S13 is not git.read-current-ledger-anchor",
            )
        ]

    # Freshness is a ledger property, not a wall-clock guess: the request was
    # created at the then-current head, every subsequent event belongs to that
    # exact request, and its authenticated terminal result is the current head.
    allowed_tail = {
        "handoff.dispatched",
        "handoff.reconciliation-required",
        "handoff.reconciled",
        "handoff.result-ingested",
    }
    tail = replayed.events[created["sequence"]:]
    if (
        not tail
        or terminal is not replayed.events[-1]
        or any(
            event.get("event_type") not in allowed_tail
            or event.get("payload", {}).get("request_id") != request_id
            for event in tail
        )
    ):
        return [
            _formal_anchor_finding(
                "trusted_anchor_stale",
                "trusted anchor observation is older than the current lifecycle head",
            )
        ]
    dispatched = next(
        (
            event
            for event in tail
            if event.get("event_type")
            in {"handoff.dispatched", "handoff.reconciliation-required"}
            and event.get("payload", {}).get("dispatch_correlation")
            == result.get("dispatch_correlation")
        ),
        None,
    )
    if dispatched is None:
        return [
            _formal_anchor_finding(
                "trusted_anchor_dispatch_unbound",
                "trusted anchor S13 is not correlated to the persisted platform delivery observation",
            )
        ]

    expected_anchor = state.get("trusted_anchor", {}).get("event_hash")
    accepted = next(
        (
            event
            for event in reversed(replayed.events[: created["sequence"] - 1])
            if event.get("event_type") == "anchor.accepted"
            and event.get("payload", {}).get("anchored_head") == expected_anchor
        ),
        None,
    )
    if accepted is None:
        return [
            _formal_anchor_finding(
                "trusted_anchor_history_mismatch",
                "current trusted-anchor state has no matching accepted ledger event",
            )
        ]
    accepted_request_id = accepted.get("payload", {}).get("request_id")
    if not isinstance(accepted_request_id, str):
        return [
            _formal_anchor_finding(
                "trusted_anchor_history_mismatch",
                "prior trusted anchor is not bound to an authenticated anchor S13",
            )
        ]
    prior_authenticated, prior_problem = _captured_handoff_authentication(
        capture, replayed, state, accepted_request_id
    )
    if prior_authenticated is None:
        return [
            _formal_anchor_finding(
                "trusted_anchor_history_mismatch",
                prior_problem or "prior anchor S13 authentication failed",
            )
        ]
    prior_request, prior_result, prior_evidence, _, prior_terminal = prior_authenticated
    accepted_payload = accepted.get("payload", {})
    if (
        prior_request.get("operation") != "git.anchor-ledger-head"
        or prior_terminal["sequence"] >= accepted["sequence"]
        or accepted_payload.get("provisional_hash") != expected_anchor
        or accepted_payload.get("result_digest") != digest(prior_result)
        or accepted_payload.get("receipt_digest") != digest(prior_result)
        or prior_evidence.get("bound_provisional_ledger_head") != expected_anchor
        or prior_evidence.get("external_head_identity") != expected_anchor
        or prior_evidence.get("workspace_identity") != state.get("workspace_id")
    ):
        return [
            _formal_anchor_finding(
                "trusted_anchor_history_mismatch",
                "prior trusted anchor does not authenticate the accepted external history",
            )
        ]
    provisional = next(
        (
            event
            for event in replayed.events
            if event.get("event_hash") == expected_anchor
        ),
        None,
    )
    previous_accepted = next(
        (
            event
            for event in reversed(replayed.events[: (provisional or accepted)["sequence"] - 1])
            if event.get("event_type") == "anchor.accepted"
        ),
        None,
    )
    prior_expected = (
        previous_accepted.get("payload", {}).get("anchored_head")
        if previous_accepted is not None
        else genesis_hash(replayed.genesis)
    )
    if (
        provisional is None
        or prior_evidence.get("operation_id")
        != provisional.get("payload", {}).get("anchor_operation_id")
        or prior_evidence.get("prior_trusted_anchor") != prior_expected
    ):
        return [
            _formal_anchor_finding(
                "trusted_anchor_history_mismatch",
                "accepted anchor is not bound to its operation and prior trusted anchor",
            )
        ]

    repository_identity = prior_evidence.get("repository_identity")
    observed_at = evidence.get("observed_at")
    if (
        not isinstance(repository_identity, str)
        or not repository_identity
        or evidence.get("repository_identity") != repository_identity
        or evidence.get("workspace_identity") != state.get("workspace_id")
        or evidence.get("external_head_identity") != expected_anchor
        or evidence.get("current_ledger_anchor") != expected_anchor
        or not isinstance(observed_at, str)
        or not _UTC_TIMESTAMP.fullmatch(observed_at)
        or observed_at < request.get("created_at", "")
        or observed_at > result.get("completed_at", "")
    ):
        return [
            _formal_anchor_finding(
                "trusted_anchor_mismatch",
                "trusted anchor observation names a stale, shorter, or different external history",
            )
        ]
    return []


def _validate_anchor(
    state: dict[str, Any],
    trusted_anchor_path: str | os.PathLike[str] | None,
    *,
    capture: _ValidationCapture | None = None,
    replayed: ReplayResult | None = None,
) -> list[dict[str, Any]]:
    if state.get("dimensions", {}).get("anchor") != "accepted":
        return []
    if trusted_anchor_path is None:
        return [finding("trusted_anchor_required", "formally anchored workspace requires a fresh trusted anchor result", subject="workspace", governing_asset="transition-requirements.json")]
    if capture is None or replayed is None:
        return [
            _formal_anchor_finding(
                "trusted_anchor_unbound",
                "trusted anchor authentication requires a retained workspace capture",
            )
        ]
    return _validate_formal_anchor(
        capture, replayed, state, trusted_anchor_path
    )


def validate_workspace(
    workspace: str | os.PathLike[str],
    *,
    artifact_path: str | os.PathLike[str] | None = None,
    trusted_anchor_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    with _ValidationCapture(workspace) as capture:
        capture.acquire_shared_lock()

        # Keep these two public snapshots explicit: they are returned as
        # evidence and remain monkeypatchable for the concurrency regression.
        # The long-lived capture below is the actual authorization boundary.
        before = snapshot_tree(capture.root_path)
        capture.capture()
        if (
            "_journal.json" in capture.entries
            or any(
                key == "_transactions" or key.startswith("_transactions/")
                for key in capture.entries
            )
        ):
            raise WorkspaceError(
                "recovery_required",
                "workspace contains an unfinished mutation journal",
                exit_code=3,
            )

        findings: list[dict[str, Any]] = []
        replayed: ReplayResult | None = None
        state: dict[str, Any] | None = None
        unsupported_engine = False
        try:
            replayed = _replay_capture(capture)
            state = _state_from_capture(capture, replayed)
            findings.extend(state.get("findings", []))
            findings.extend(
                _validate_anchor(
                    state,
                    trusted_anchor_path,
                    capture=capture,
                    replayed=replayed,
                )
            )
        except (LedgerError, ProjectionError, ContractError) as exc:
            unsupported_engine = getattr(exc, "code", None) == "unsupported_engine_bundle"
            findings.append(
                finding(
                    getattr(exc, "code", "workspace_invalid"),
                    str(exc),
                    subject="workspace",
                    governing_asset="pinned-engine",
                    gate_impact="block",
                )
            )

        # An unsupported pinned engine is a compatibility boundary, not proof
        # that its historical artifacts are orphaned under the current roster.
        # Retain the read-only capture checks below without reinterpreting it.
        if not unsupported_engine:
            records_by_path: dict[
                str, tuple[str, str, dict[str, Any], Any]
            ] = {}
            try:
                inventory_findings, records_by_path = _validate_complete_inventory(
                    capture,
                    replayed,
                    state,
                )
                findings.extend(inventory_findings)
            except (LedgerError, ProjectionError, ContractError) as exc:
                findings.append(
                    finding(
                        getattr(exc, "code", "workspace_inventory_invalid"),
                        str(exc),
                        subject="workspace",
                        governing_asset="command-contracts.json",
                        gate_impact="block",
                    )
                )
            findings.extend(
                _selected_artifact_findings(
                    capture,
                    state,
                    records_by_path,
                    artifact_path,
                )
            )

        capture.verify()
        after = snapshot_tree(capture.root_path)
        capture.verify()
        if before != after:
            raise WorkspaceError(
                "concurrent_mutation",
                "workspace before/after snapshots differ",
                exit_code=4,
            )
        normalized = _force_blocking_findings(findings)
        before_digest = digest(before)
        after_digest = digest(after)
        return {
            "valid": not normalized,
            "snapshot_digest": before_digest,
            "before_snapshot_digest": before_digest,
            "after_snapshot_digest": after_digest,
            "snapshot_unchanged": before == after,
            "revision": state.get("revision") if state else None,
            "ledger_head": state.get("ledger_head") if state else None,
            "findings": normalized,
        }


def resume_workspace(workspace: str | os.PathLike[str], trusted_anchor_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    with _ValidationCapture(workspace) as capture:
        capture.acquire_shared_lock()
        before = snapshot_tree(capture.root_path)
        capture.capture()
        if (
            "_journal.json" in capture.entries
            or any(
                key == "_transactions" or key.startswith("_transactions/")
                for key in capture.entries
            )
        ):
            raise WorkspaceError(
                "recovery_required",
                "workspace contains an unfinished mutation journal",
                exit_code=3,
            )
        replayed = _replay_capture(capture)
        state = _state_from_capture(capture, replayed)
        # A durable missing-binding fact is an expected workflow disposition,
        # not corrupted history.  Keep it in the returned materialized state
        # so the coordinator can source the block, while allowing
        # admissible_actions() to return the exact configuration_required
        # action.  Integrity/lineage findings remain fatal just as in Validate.
        resume_findings: list[dict[str, Any]] = [
            item for item in state.get("findings", [])
            if item.get("code") != "configuration_required"
        ]
        try:
            inventory_findings, _ = _validate_complete_inventory(
                capture,
                replayed,
                state,
            )
            resume_findings.extend(inventory_findings)
        except (LedgerError, ProjectionError, ContractError) as exc:
            resume_findings.append(
                finding(
                    getattr(exc, "code", "workspace_inventory_invalid"),
                    str(exc),
                    subject="workspace",
                    governing_asset="command-contracts.json",
                    gate_impact="block",
                )
            )
        resume_findings.extend(_validate_anchor(
            state,
            trusted_anchor_path,
            capture=capture,
            replayed=replayed,
        ))
        bundle = replayed.bundles[replayed.active_engine_digest]
        actions = (
            []
            if resume_findings
            else admissible_actions(state, bundle, state.get("bindings", {}))
        )
        capture.verify()
        after = snapshot_tree(capture.root_path)
        capture.verify()
        if before != after:
            raise WorkspaceError(
                "concurrent_mutation",
                "workspace before/after snapshots differ during Resume",
                exit_code=4,
            )
        if resume_findings:
            normalized = _force_blocking_findings(resume_findings)
            raise WorkspaceError(
                normalized[0]["code"],
                normalized[0]["message"],
                exit_code=3,
                findings=normalized,
            )
        earliest = actions[0].as_dict() if actions else {"disposition": "complete"}
        return {"revision": replayed.revision, "state": state, "action": earliest, "alternatives": [item.as_dict() for item in actions[1:]]}


def _diagnostic_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect the SDM governed-workspace library")
    parser.parse_args(argv)
    sys.stdout.write(json.dumps({"status": "pass", "command": "workspace.help"}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(_diagnostic_main())
