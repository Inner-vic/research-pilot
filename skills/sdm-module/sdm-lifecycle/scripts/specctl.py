#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""JSON-only command surface for the SDM lifecycle control plane."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tomllib
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from authority import (
    AUTHORITY_PROVENANCE_EXTENSION,
    AuthorityError,
    authority_provenance_subject,
    redact,
    verify_authorities,
)
from canonical import (
    MAX_DOCUMENT_BYTES, CanonicalError, candidate_content, candidate_digest,
    canonical_bytes, digest, parse_document, read_document,
)
from contracts import (
    AssetBundle, ContractError, ENGINEERING_PROVENANCE_COMMON_FIELDS,
    ENGINEERING_PROVENANCE_MERGED_FIELDS, ENGINEERING_PROVENANCE_SCHEMA,
    ENGINEERING_SOURCE_OPERATIONS, canonical_run_fingerprint,
    canonical_runtime_environment_manifest,
    canonical_runtime_environment_plan, evaluate_gate,
    evaluate_scientific_decision, finding, sort_findings,
    resolved_configuration_trace_digest,
    runtime_environment_digest, runtime_environment_plan_digest,
    runtime_profile_from_manifest, validate_runtime_environment_against_r01,
    runtime_image_binding_valid,
    validate_baseline_replacements, validate_change_diff_evidence,
    validate_change_policy,
)
from handoffs import HandoffError
import handoffs
from journal import JournalError
from ledger import LedgerError, replay, utc_now
from projection import (
    ProjectionError, lifecycle_step_applicability, materialize,
    validate_graph,
)
from workspace import (
    MutationResult, WorkspaceError, create_workspace, migrate_engine,
    mutate as _workspace_mutate, mutate_batch as _workspace_mutate_batch,
    resume_workspace, validate_workspace,
)


_PORTABLE_INSTANCE = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_MAX_TREE_NODES = 100_000
_MAX_TREE_BYTES = 134_217_728


class CLIError(RuntimeError):
    def __init__(self, code: str, message: str, *, exit_code: int = 2, status: str | None = None, result: Any = None, findings: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.status = status or ({3: "blocked", 4: "conflict",
                                  5: "configuration_required",
                                  70: "internal_error"}.get(exit_code, "fail"))
        self.result = result
        self.findings = findings or []


def mutate(*args: Any, **kwargs: Any) -> MutationResult:
    """Route every public specctl write through the typed-graph gate."""

    kwargs["enforce_graph"] = True
    return _workspace_mutate(*args, **kwargs)


def mutate_batch(*args: Any, **kwargs: Any) -> MutationResult:
    """Route every public atomic specctl batch through the typed-graph gate."""

    kwargs["enforce_graph"] = True
    return _workspace_mutate_batch(*args, **kwargs)


class JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CLIError("malformed_cli", message, exit_code=2)


def _add_workspace(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", required=True)


def _add_mutation(parser: argparse.ArgumentParser, *, payload: bool = False) -> None:
    _add_workspace(parser)
    if payload:
        parser.add_argument("--payload", required=True)
    parser.add_argument("--expected-revision", type=int, required=True)
    parser.add_argument("--operation-id")


def build_parser() -> argparse.ArgumentParser:
    parser = JSONArgumentParser(prog="specctl.py", description="Specification-Driven ML lifecycle control plane")
    commands = parser.add_subparsers(dest="top_command", required=True)

    validate = commands.add_parser("validate", help="read-only workspace/artifact validation")
    _add_workspace(validate)
    validate.add_argument("--artifact")
    validate.add_argument("--trusted-anchor-result")

    hashing = commands.add_parser("hash", help="canonicalize and hash strict JSON/YAML")
    hashing.add_argument("--input", required=True)

    freeze = commands.add_parser("freeze", help="freeze E01 provisionally and request an external head anchor")
    _add_workspace(freeze)
    freeze.add_argument("--experiment-id", required=True)
    freeze.add_argument("--attestation", required=True)
    freeze.add_argument("--expected-revision", type=int, required=True)
    freeze.add_argument("--operation-id")

    diff = commands.add_parser("check-diff", help="classify changes to frozen Experiment semantics")
    _add_workspace(diff)
    diff.add_argument("--experiment-id", required=True)
    diff.add_argument("--candidate", required=True)

    run = commands.add_parser("materialize-run", help="materialize immutable R01/R02")
    _add_workspace(run)
    for name in ("experiment-id", "run-plan-id", "run-id", "attempt-id"):
        run.add_argument(f"--{name}", required=True)
    run.add_argument("--expected-revision", type=int, required=True)
    run.add_argument("--operation-id")

    verify_run = commands.add_parser("verify-run-manifest", help="read-only verification of R01")
    _add_workspace(verify_run)
    verify_run.add_argument("--manifest", required=True)

    evidence = commands.add_parser("build-evidence", help="account for attempts and build D01-D04")
    _add_workspace(evidence)
    evidence.add_argument("--experiment-id", required=True)
    evidence.add_argument("--evidence-id", required=True)
    evidence.add_argument("--expected-revision", type=int, required=True)
    evidence.add_argument("--operation-id")

    verify_evidence = commands.add_parser("verify-evidence", help="read-only verification of D01-D04")
    _add_workspace(verify_evidence)
    verify_evidence.add_argument("--evidence-id", required=True)

    decision = commands.add_parser(
        "scientific-decision",
        help="evaluate governed frozen rules and persist immutable D05",
    )
    _add_workspace(decision)
    decision.add_argument("--experiment-id", required=True)
    decision.add_argument("--evidence-id", required=True)
    decision.add_argument("--decision-id", required=True)
    decision.add_argument("--attestation", required=True)
    decision.add_argument("--expected-revision", type=int, required=True)
    decision.add_argument("--operation-id")

    transition = commands.add_parser("validate-transition", help="read-only transition/gate evaluation")
    _add_workspace(transition)
    transition.add_argument("--transition", required=True)
    transition.add_argument("--payload", required=True)

    lifecycle = commands.add_parser("lifecycle", help="Create, Resume, Advance, and governed interrupt flows")
    lifecycle_actions = lifecycle.add_subparsers(dest="lifecycle_action", required=True)
    create = lifecycle_actions.add_parser("create")
    create.add_argument("--workspace")
    create.add_argument("--payload", required=True)
    create.add_argument("--expected-revision", type=int, required=True)
    create.add_argument("--operation-id")
    create.add_argument("--project-root", required=True)
    resume = lifecycle_actions.add_parser("resume")
    _add_workspace(resume)
    resume.add_argument("--trusted-anchor-result")
    advance = lifecycle_actions.add_parser("advance")
    _add_mutation(advance, payload=True)
    for name in (
        "amend", "deviate", "supersede", "deprecate", "retry", "cancel",
        "exception", "archive", "close", "migrate",
    ):
        action = lifecycle_actions.add_parser(name)
        _add_mutation(action, payload=True)
        if name in {"archive", "close"}:
            action.add_argument("--trusted-anchor-result")

    handoff = commands.add_parser("handoff", help="immutable platform Skill handoff packets")
    handoff_actions = handoff.add_subparsers(dest="handoff_action", required=True)
    create_handoff = handoff_actions.add_parser("create")
    _add_mutation(create_handoff, payload=True)
    dispatched = handoff_actions.add_parser("mark-dispatched")
    _add_mutation(dispatched)
    dispatched.add_argument("--request-id", required=True)
    dispatched.add_argument("--dispatch-correlation", required=True)
    dispatched.add_argument(
        "--coordinator-disposition",
        choices=("dispatched", "invoke_error", "dispatch_unknown"),
        default="dispatched",
    )
    dispatched.add_argument(
        "--uncertain", action="store_const", const=True, default=None,
        help="deprecated compatibility spelling for --coordinator-disposition dispatch_unknown",
    )
    ingest = handoff_actions.add_parser("ingest")
    _add_mutation(ingest)
    ingest.add_argument("--result", required=True)
    cancel_handoff = handoff_actions.add_parser("cancel")
    _add_mutation(cancel_handoff)
    cancel_handoff.add_argument("--request-id", required=True)
    cancel_handoff.add_argument("--reason", required=True)
    retry = handoff_actions.add_parser("retry")
    _add_mutation(retry, payload=True)
    retry.add_argument("--prior-request-id", required=True)
    supersede = handoff_actions.add_parser("supersede")
    _add_mutation(supersede)
    supersede.add_argument("--request-id", required=True)
    supersede.add_argument("--successor-request-id", required=True)
    supersede.add_argument("--reason", required=True)
    reconcile = handoff_actions.add_parser("reconcile")
    _add_mutation(reconcile, payload=True)
    reconcile.add_argument("--request-id", required=True)
    return parser


def _command(args: argparse.Namespace) -> str:
    if args.top_command == "lifecycle":
        return f"specctl.lifecycle.{args.lifecycle_action}"
    if args.top_command == "handoff":
        return f"specctl.handoff.{args.handoff_action}"
    return f"specctl.{args.top_command}"


def _operation_id(args: argparse.Namespace, payload: Any = None) -> str:
    supplied = getattr(args, "operation_id", None)
    if supplied:
        return supplied
    seed = {"command": _command(args), "expected_revision": getattr(args, "expected_revision", None), "payload": payload}
    return "specctl-" + digest(seed).split(":", 1)[1][:32]


def _payload(path: str) -> dict[str, Any]:
    value = read_document(path)
    if not isinstance(value, dict):
        raise CLIError("payload_type", "payload must be an object")
    return value


_SECURE_DIR_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
)
_SECURE_FILE_FLAGS = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def _open_absolute_directory(path: Path) -> tuple[int, str]:
    if not path.is_absolute() or ".." in path.parts:
        raise CLIError(
            "configured_path_unsafe", "configured path must be absolute",
            exit_code=5,
        )
    descriptor = os.open("/", _SECURE_DIR_FLAGS)
    try:
        for component in path.parts[1:]:
            child = os.open(component, _SECURE_DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        identity = digest({
            "resolved_path": os.path.normpath(str(path)),
            "device": info.st_dev, "inode": info.st_ino,
        })
        return descriptor, identity
    except Exception:
        os.close(descriptor)
        raise


def _read_fd_bytes(descriptor: int, *, subject: str) -> bytes:
    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
        or before.st_size > MAX_DOCUMENT_BYTES
    ):
        raise CLIError(
            "configured_file_unsafe",
            f"configured file is not a bounded single-link regular file: {subject}",
            exit_code=5,
        )
    data = bytearray()
    while len(data) < before.st_size:
        chunk = os.read(
            descriptor, min(1024 * 1024, before.st_size - len(data)),
        )
        if not chunk:
            break
        data.extend(chunk)
    after = os.fstat(descriptor)
    before_identity = (
        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if (
        len(data) != before.st_size
        or before_identity != after_identity
    ):
        raise CLIError(
            "configured_file_changed",
            f"configured file changed while captured: {subject}",
            exit_code=4,
        )
    return bytes(data)


def _secure_tree_files(root: Path) -> tuple[dict[str, bytes], str]:
    root_fd, identity = _open_absolute_directory(root)
    files: dict[str, bytes] = {}
    node_count = 0
    total_bytes = 0

    def capture(directory_fd: int, prefix: str) -> None:
        nonlocal node_count, total_bytes
        for name in sorted(os.listdir(directory_fd)):
            if name in {".", ".."} or "/" in name:
                raise CLIError(
                    "configured_tree_unsafe", "unsafe configured tree entry",
                    exit_code=5,
                )
            relative = f"{prefix}/{name}" if prefix else name
            before = os.stat(
                name, dir_fd=directory_fd, follow_symlinks=False,
            )
            node_count += 1
            if node_count > _MAX_TREE_NODES:
                raise CLIError(
                    "configured_tree_resource_limit",
                    "configured tree exceeds the fixed node limit",
                    exit_code=5,
                )
            if stat.S_ISDIR(before.st_mode):
                child_fd = os.open(name, _SECURE_DIR_FLAGS, dir_fd=directory_fd)
                try:
                    opened = os.fstat(child_fd)
                    if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                        raise CLIError(
                            "configured_tree_changed",
                            f"configured directory changed while captured: {relative}",
                            exit_code=4,
                        )
                    capture(child_fd, relative)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(before.st_mode):
                total_bytes += before.st_size
                if total_bytes > _MAX_TREE_BYTES:
                    raise CLIError(
                        "configured_tree_resource_limit",
                        "configured tree exceeds the fixed aggregate byte limit",
                        exit_code=5,
                    )
                file_fd = os.open(name, _SECURE_FILE_FLAGS, dir_fd=directory_fd)
                try:
                    opened = os.fstat(file_fd)
                    if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                        raise CLIError(
                            "configured_tree_changed",
                            f"configured file changed while captured: {relative}",
                            exit_code=4,
                        )
                    files[relative] = _read_fd_bytes(file_fd, subject=relative)
                finally:
                    os.close(file_fd)
            else:
                raise CLIError(
                    "configured_tree_unsafe",
                    f"configured tree contains a symlink or special node: {relative}",
                    exit_code=5,
                )

    try:
        capture(root_fd, "")
    finally:
        os.close(root_fd)
    return files, identity


def _secure_read_relative(root: Path, relative: tuple[str, ...]) -> bytes:
    descriptor, _ = _open_absolute_directory(root)
    try:
        return _secure_read_from_open_root(descriptor, relative)
    finally:
        os.close(descriptor)


def _secure_read_from_open_root(
    root_descriptor: int, relative: tuple[str, ...],
) -> bytes:
    """Read below one retained root descriptor without reopening its path."""

    if (
        not relative
        or any(component in {"", ".", ".."} or "/" in component for component in relative)
    ):
        raise CLIError(
            "configured_path_unsafe", "configured relative path is unsafe",
            exit_code=5,
        )
    descriptor = os.dup(root_descriptor)
    try:
        for component in relative[:-1]:
            child = os.open(component, _SECURE_DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_fd = os.open(relative[-1], _SECURE_FILE_FLAGS, dir_fd=descriptor)
        try:
            return _read_fd_bytes(file_fd, subject="/".join(relative))
        finally:
            os.close(file_fd)
    finally:
        os.close(descriptor)


def _secure_packet_document(
    workspace: str, relative: tuple[str, ...], *, packet_kind: str,
) -> dict[str, Any]:
    """Capture one canonical, single-link packet without following links.

    Platform receipts are an authority boundary.  Opening them through a
    pathname would let a symlink, hardlink, or directory replacement make
    bytes outside the workspace look governed.  The retained directory chain
    and file descriptor checks here match the formal-Validate boundary.
    """

    try:
        raw = _secure_read_relative(Path(workspace).absolute(), relative)
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        raise CLIError(
            "platform_packet_unsafe",
            f"{packet_kind} is missing, linked, or unsafe: {'/'.join(relative)}",
            exit_code=3,
        ) from exc
    try:
        value = parse_document(raw)
    except (CanonicalError, ValueError) as exc:
        raise CLIError(
            "platform_packet_invalid",
            f"{packet_kind} is not strict canonical JSON: {'/'.join(relative)}",
            exit_code=3,
        ) from exc
    if not isinstance(value, dict) or raw != canonical_bytes(value):
        raise CLIError(
            "platform_packet_noncanonical",
            f"{packet_kind} must be an exact canonical JSON object",
            exit_code=3,
        )
    return value


def _installed_skill_tree_digest(skill_path: Path) -> str:
    try:
        files, _ = _secure_tree_files(skill_path)
    except FileNotFoundError as exc:
        raise CLIError(
            "configured_skill_unavailable",
            f"configured platform Skill is unavailable: {skill_path.name}",
            exit_code=5,
        ) from exc
    entries = [
        {
            "path": relative,
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        }
        for relative, data in sorted(files.items())
    ]
    if not entries or not any(item["path"] == "SKILL.md" for item in entries):
        raise CLIError(
            "configured_skill_incomplete",
            f"configured platform Skill lacks exact SKILL.md bytes: {skill_path.name}",
            exit_code=5,
        )
    return digest({"algorithm": "sha256-skill-tree-v1", "files": entries})


def _trusted_snapshot_from_root(root: Path) -> tuple[dict[str, Any], str, str]:
    try:
        files, root_identity = _secure_tree_files(root)
    except FileNotFoundError as exc:
        raise CLIError(
            "trust_root_unavailable",
            f"configured authority trust root is unavailable: {root}",
            exit_code=5,
        ) from exc
    raw = files.get("trust-snapshot.json")
    if raw is None:
        raise CLIError(
            "trust_snapshot_missing",
            "configured trust root lacks trust-snapshot.json",
            exit_code=5,
        )
    try:
        snapshot = parse_document(raw)
    except (CanonicalError, UnicodeDecodeError, ValueError) as exc:
        raise CLIError(
            "trust_snapshot_invalid",
            f"trust-snapshot.json is not strict JSON: {exc}",
            exit_code=5,
        ) from exc
    if not isinstance(snapshot, dict):
        raise CLIError(
            "trust_snapshot_invalid", "trust snapshot must be an object",
            exit_code=5,
        )
    return snapshot, digest(snapshot), root_identity




def _project_bounded_directory(
    project_root: Path,
    configured_value: str,
    *,
    label: str,
    require_exists: bool,
) -> Path:
    """Resolve one runtime directory without consulting the ambient cwd.

    Configured workspace roots are authority-bearing paths.  They are
    interpreted only relative to the explicitly supplied project boundary,
    are required to have a single lexical spelling, and are inspected with
    ``lstat`` before any caller can create a descendant.
    """

    if (
        not isinstance(configured_value, str)
        or not configured_value
        or configured_value != configured_value.strip()
        or "\x00" in configured_value
    ):
        raise CLIError(
            "runtime_path_invalid",
            f"{label} must be a non-empty path without surrounding whitespace",
            exit_code=5,
        )
    expanded = configured_value.replace("{project-root}", str(project_root))
    if "{project-root}" in expanded or expanded.startswith("//"):
        raise CLIError(
            "runtime_path_ambiguous",
            f"{label} has an ambiguous project-root expansion",
            exit_code=5,
        )
    raw_components = expanded.split("/")
    if expanded.startswith("/"):
        raw_components = raw_components[1:]
    if any(component in {"", ".", ".."} for component in raw_components):
        raise CLIError(
            "runtime_path_ambiguous",
            f"{label} must have one normalized, traversal-free spelling",
            exit_code=5,
        )
    selected = Path(expanded)
    if not selected.is_absolute():
        selected = project_root / selected
    selected = Path(os.path.normpath(str(selected)))
    if selected == project_root or not selected.is_relative_to(project_root):
        raise CLIError(
            "runtime_path_escape",
            f"{label} escapes the explicit project boundary",
            exit_code=5,
        )
    for protected in (project_root / "_bmad", project_root / ".agents"):
        if selected == protected or selected.is_relative_to(protected):
            raise CLIError(
                "runtime_path_protected",
                f"{label} overlaps installed configuration or Skills",
                exit_code=5,
            )

    current = project_root
    missing = False
    for component in selected.relative_to(project_root).parts:
        current = current / component
        if missing:
            continue
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            missing = True
            continue
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise CLIError(
                "runtime_path_unsafe",
                f"{label} crosses a symlink or special node: {current}",
                exit_code=5,
            )
    if require_exists and missing:
        raise CLIError(
            "runtime_path_missing",
            f"{label} does not identify an existing workspace",
            exit_code=5,
        )
    return selected


def _resolved_sdm_runtime_paths(
    project_root: Path,
    module_config: dict[str, Any],
    *,
    workspace_id: str | None,
    workspace_override: str | None,
    require_workspace: bool = False,
) -> dict[str, Any]:
    configured_output = module_config.get("sdm_output_root")
    if not isinstance(configured_output, str) or not configured_output:
        raise CLIError(
            "output_root_missing",
            "modules.sdm.sdm_output_root is required",
            exit_code=5,
        )
    output_root = _project_bounded_directory(
        project_root,
        configured_output,
        label="sdm_output_root",
        require_exists=False,
    )
    reports_root = _project_bounded_directory(
        project_root,
        str(output_root / "reports"),
        label="sdm reports root",
        require_exists=False,
    )
    defaulted = workspace_override is None
    if defaulted:
        if not isinstance(workspace_id, str) or not _PORTABLE_INSTANCE.fullmatch(
            workspace_id
        ):
            raise CLIError(
                "workspace_id_invalid",
                "an omitted workspace requires a portable workspace_id",
            )
        workspace_value = str(output_root / "workspaces" / workspace_id)
    else:
        workspace_value = workspace_override
    workspace = _project_bounded_directory(
        project_root,
        workspace_value,
        label="workspace",
        require_exists=require_workspace,
    )
    return {
        "project_root": str(project_root),
        "sdm_output_root": str(output_root),
        "reports_root": str(reports_root),
        "workspace": str(workspace),
        "workspace_defaulted": defaulted,
    }


def _ensure_workspace_parent(project_root: Path, workspace: Path) -> None:
    """Create only the validated workspace parent through retained dirfds."""

    relative_parent = workspace.parent.relative_to(project_root)
    descriptor = os.open(project_root, _SECURE_DIR_FLAGS)
    try:
        for component in relative_parent.parts:
            try:
                child = os.open(component, _SECURE_DIR_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                os.mkdir(component, mode=0o700, dir_fd=descriptor)
                os.fsync(descriptor)
                child = os.open(component, _SECURE_DIR_FLAGS, dir_fd=descriptor)
            info = os.fstat(child)
            if not stat.S_ISDIR(info.st_mode):
                os.close(child)
                raise CLIError(
                    "runtime_path_unsafe",
                    "workspace parent contains a non-directory node",
                    exit_code=5,
                )
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def resolve_active_workspace(project_root_value: str) -> dict[str, Any]:
    """Resolve the personal sprint-hook workspace through the same boundary."""

    supplied_root = Path(project_root_value)
    if not supplied_root.is_absolute():
        raise CLIError(
            "project_root_invalid",
            "project root must be absolute; the ambient cwd is never a boundary",
            exit_code=5,
        )
    project_root = supplied_root.resolve(strict=True)
    try:
        shared_raw = _secure_read_relative(
            project_root, ("_bmad", "custom", "config.toml"),
        )
        personal_raw = _secure_read_relative(
            project_root, ("_bmad", "custom", "config.user.toml"),
        )
        shared = tomllib.loads(shared_raw.decode("utf-8"))
        personal = tomllib.loads(personal_raw.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise CLIError(
            "runtime_config_unavailable",
            f"cannot load installed BMad configuration: {exc}",
            exit_code=5,
        ) from exc
    module = shared.get("modules", {}).get("sdm")
    personal_module = personal.get("modules", {}).get("sdm")
    if not isinstance(module, dict) or not isinstance(personal_module, dict):
        raise CLIError(
            "runtime_config_unavailable",
            "installed configuration lacks [modules.sdm]",
            exit_code=5,
        )
    active = personal_module.get("sdm_active_workspace")
    if not isinstance(active, str) or not active:
        raise CLIError(
            "active_workspace_unset",
            "modules.sdm.sdm_active_workspace is empty",
            exit_code=5,
        )
    paths = _resolved_sdm_runtime_paths(
        project_root,
        module,
        workspace_id=None,
        workspace_override=active,
        require_workspace=True,
    )
    try:
        replayed = replay(paths["workspace"])
    except (LedgerError, WorkspaceError, OSError, ValueError) as exc:
        raise CLIError(
            "active_workspace_invalid",
            "sdm_active_workspace is not one canonical governed workspace",
            exit_code=5,
        ) from exc
    paths["workspace_id"] = replayed.genesis["workspace_id"]
    return paths


def _configured_creation_payload(
    raw_payload: dict[str, Any], project_root_value: str,
    workspace_override: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    forbidden = {
        "bindings", "binding_skill_digests", "promotion_eligible", "promotion_adapter_identity",
        "promotion_adapter_module_root", "promotion_adapter_module_path",
        "promotion_adapter_module_digest",
        "promotion_state_root", "promotion_state_root_identity",
        "authority_trust_root", "authority_trust_root_identity",
        "authority_trust_snapshot", "authority_trust_snapshot_digest",
        "lifecycle_skill_root", "lifecycle_skill_digest",
        "sdm_output_root", "sdm_reports_root",
    }
    supplied = sorted(forbidden.intersection(raw_payload))
    if supplied:
        raise CLIError(
            "runtime_config_override",
            "create payload cannot override setup-owned runtime configuration: "
            + ", ".join(supplied),
        )
    supplied_project_root = Path(project_root_value)
    if not supplied_project_root.is_absolute():
        raise CLIError(
            "project_root_invalid",
            "project root must be absolute; the ambient cwd is never a boundary",
            exit_code=5,
        )
    project_root = supplied_project_root.resolve(strict=True)
    try:
        raw_config = _secure_read_relative(
            project_root, ("_bmad", "custom", "config.toml"),
        )
        parsed = tomllib.loads(raw_config.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise CLIError(
            "runtime_config_unavailable",
            f"cannot load installed BMad configuration: {exc}",
            exit_code=5,
        ) from exc
    module = parsed.get("modules", {}).get("sdm")
    if not isinstance(module, dict):
        raise CLIError(
            "runtime_config_unavailable",
            "installed configuration lacks [modules.sdm]",
            exit_code=5,
        )
    runtime_paths = _resolved_sdm_runtime_paths(
        project_root,
        module,
        workspace_id=raw_payload.get("workspace_id"),
        workspace_override=workspace_override,
    )
    configured_trust_root = module.get("sdm_trust_root")
    if not isinstance(configured_trust_root, str) or not configured_trust_root:
        raise CLIError(
            "trust_root_missing", "modules.sdm.sdm_trust_root is required",
            exit_code=5,
        )
    trust_root_text = configured_trust_root.replace(
        "{project-root}", str(project_root),
    )
    trust_root = Path(trust_root_text)
    if not trust_root.is_absolute():
        trust_root = project_root / trust_root
    trust_snapshot, trust_snapshot_digest, trust_root_identity = \
        _trusted_snapshot_from_root(trust_root)
    binding_keys = (
        "data_platform_skill", "source_control_platform_skill",
        "ci_platform_skill", "ml_execution_platform_skill",
    )
    bindings: dict[str, str] = {}
    binding_digests: dict[str, str] = {}
    installed_skills = project_root / ".agents" / "skills"
    lifecycle_skill_root = installed_skills / "sdm-lifecycle"
    lifecycle_skill_digest = _installed_skill_tree_digest(
        lifecycle_skill_root,
    )
    for key in binding_keys:
        identity = module.get(key, "")
        if not isinstance(identity, str) or (
            identity and (identity in {".", ".."} or Path(identity).name != identity)
        ):
            raise CLIError(
                "runtime_config_invalid",
                f"modules.sdm.{key} is not an exact Skill directory identity",
                exit_code=5,
            )
        if not identity:
            continue
        bindings[key] = identity
        binding_digests[key] = _installed_skill_tree_digest(
            installed_skills / identity,
        )
    return {
        **raw_payload,
        "bindings": bindings,
        "binding_skill_digests": binding_digests,
        "authority_trust_root": os.path.normpath(str(trust_root)),
        "authority_trust_root_identity": trust_root_identity,
        "authority_trust_snapshot": trust_snapshot,
        "authority_trust_snapshot_digest": trust_snapshot_digest,
        "lifecycle_skill_root": os.path.normpath(str(lifecycle_skill_root)),
        "lifecycle_skill_digest": lifecycle_skill_digest,
    }, runtime_paths


def _result(command: str, revision: int | None, result: Any, findings: list[dict[str, Any]] | None = None, *, status: str = "pass") -> dict[str, Any]:
    return {
        "status": status,
        "command": command,
        "revision": revision,
        "result": redact(result),
        "findings": sort_findings(findings or []),
    }


def _workspace_state(path: str) -> tuple[Any, dict[str, Any], AssetBundle]:
    replayed = replay(path)
    state = materialize(replayed)
    graph_findings = validate_graph(state.get("lineage"))
    if graph_findings:
        raise CLIError(
            graph_findings[0]["code"], graph_findings[0]["message"],
            exit_code=3, findings=graph_findings,
        )
    return replayed, state, replayed.bundles[replayed.active_engine_digest]


_GOVERNED_PAYLOAD_NAMESPACES = {
    "artifacts",
    "authorities",
    "bindings",
    "checks",
    "context",
    "dimensions",
    "handoffs",
    "pending_anchor",
    "platform_results",
    "published_artifacts",
    "substate_events",
    "trusted_anchor",
}


def _reject_governed_payload_claims(
    payload: dict[str, Any], *, allowed: set[str] | None = None,
) -> None:
    forbidden = sorted(_GOVERNED_PAYLOAD_NAMESPACES.intersection(payload))
    if forbidden:
        raise CLIError(
            "untrusted_gate_context",
            "lifecycle payload cannot assert replay-governed facts: "
            + ", ".join(forbidden),
        )
    if allowed is not None:
        unknown = sorted(set(payload).difference(allowed))
        if unknown:
            raise CLIError(
                "payload_unknown_field",
                "lifecycle payload contains unsupported fields: "
                + ", ".join(unknown),
            )


def _subjects(payload: dict[str, Any], workspace_id: str) -> dict[str, str]:
    supplied = payload.get("subjects", {})
    if not isinstance(supplied, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) or not value
        for key, value in supplied.items()
    ):
        raise CLIError("subjects_invalid", "subjects must map string identities to non-empty strings")
    result = dict(supplied)
    if not any(
        key in result
        for key in (
            "baseline_id", "experiment_id", "decision_id", "run_id",
            "attempt_id", "artifact_id",
        )
    ):
        result["artifact_id"] = f"workspace-{workspace_id}"
    return result


def _authority_subject(
    payload: dict[str, Any], workspace_id: str,
) -> dict[str, Any]:
    governed_ids = {"workspace_id": workspace_id, **payload.get("subjects", {})}
    digests = payload.get("authority_digests", {})
    extra = payload.get("authority_subject", {})
    if not isinstance(digests, dict) or not isinstance(extra, dict):
        raise CLIError(
            "authority_subject_invalid",
            "authority_digests and authority_subject must be objects",
        )
    if {"governed_ids", "digests"}.intersection(extra):
        raise CLIError(
            "authority_subject_override",
            "authority_subject cannot replace governed IDs or digests",
        )
    return {"governed_ids": governed_ids, "digests": digests, **extra}


def _phase_operation_id(logical_operation_id: str, phase: str) -> str:
    return "specctl-" + digest({
        "logical_operation_id": logical_operation_id,
        "phase": phase,
    }).split(":", 1)[1][:40]


def _stable_lifecycle_operation_id(
    args: argparse.Namespace, payload: dict[str, Any],
) -> str:
    supplied = getattr(args, "operation_id", None)
    if supplied:
        return supplied
    return "specctl-" + digest({
        "command": _command(args), "payload": payload,
    }).split(":", 1)[1][:40]


def _platform_request_payload(
    *,
    bundle: AssetBundle,
    state: dict[str, Any],
    workspace_id: str,
    operation: str,
    gate: str,
    step: int,
    subjects: dict[str, str],
    request_seed: dict[str, Any],
) -> dict[str, Any]:
    _, operation_contract, binding_key = bundle.operation_contract(operation)
    binding_identity = state.get("bindings", {}).get(binding_key)
    if not isinstance(binding_identity, str) or not binding_identity:
        # handoffs.create persists the durable configuration-required event.
        binding_identity = "unconfigured-binding"
        binding_skill_digest = digest({"unconfigured": binding_key})
    else:
        binding_skill_digest = state.get("binding_skill_digests", {}).get(binding_key)
    if not isinstance(binding_skill_digest, str):
        raise CLIError(
            "binding_skill_digest_required",
            f"runtime binding lacks a genesis-pinned Skill-tree digest: {binding_key}",
            exit_code=5,
        )
    request_id = "REQ-" + digest(request_seed).split(":", 1)[1][:32]
    return {
        "request_id": request_id,
        "operation": operation,
        "binding_identity": binding_identity,
        # The digest is pinned immutably in genesis by setup/runtime discovery;
        # native load telemetry must later prove that these exact bytes loaded.
        "binding_skill_digest": binding_skill_digest,
        "adapter_contract_digest": digest(operation_contract),
        "gate": gate,
        "step": step,
        "subjects": subjects,
        "expected_evidence": list(operation_contract["required_evidence"]),
        "evidence_cardinality": deepcopy(
            operation_contract["evidence_cardinality"],
        ),
        "portable_paths": [],
    }


def _ensure_platform_requests(
    workspace: str,
    *,
    replayed: Any,
    state: dict[str, Any],
    bundle: AssetBundle,
    operations: list[str],
    gate: str,
    step: int,
    subjects: dict[str, str],
    expected_revision: int,
    seed: dict[str, Any],
) -> tuple[int, list[dict[str, Any]]]:
    revision = expected_revision
    requests: list[dict[str, Any]] = []
    for operation in operations:
        payload = _platform_request_payload(
            bundle=bundle,
            state=state,
            workspace_id=replayed.genesis["workspace_id"],
            operation=operation,
            gate=gate,
            step=step,
            subjects=subjects,
            request_seed={**seed, "operation": operation},
        )
        outcome = handoffs.create(
            workspace,
            payload,
            expected_revision=revision,
            operation_id=_phase_operation_id(payload["request_id"], "create"),
        )
        revision = outcome.revision
        packet = outcome.packet or payload
        requests.append({
            "request_id": outcome.request_id,
            "operation": operation,
            "outer_skill_binding": packet["binding_identity"],
            "binding_skill_digest": packet["binding_skill_digest"],
            "adapter_contract_digest": packet["adapter_contract_digest"],
            "packet_path": f"packets/S12/{outcome.request_id}.json",
            "subjects": packet["subjects"],
            "disposition": outcome.disposition,
        })
    return revision, requests


def _exact_platform_results(
    state: dict[str, Any], *, operations: list[str], gate: str, step: int,
    subjects: dict[str, str],
) -> dict[str, str]:
    """Authenticate platform success at exact S12 scope.

    Operation-name summaries are intentionally ignored: the same operation can
    appear at several lifecycle rows and for several governed subjects.
    """

    successful: dict[str, str] = {}
    for record in state.get("handoffs", {}).values():
        if not isinstance(record, dict):
            continue
        operation = record.get("operation")
        if (
            operation not in operations
            or record.get("status") not in {"successful", "reconciled_effect"}
            or record.get("gate") != gate
            or record.get("step") != step
            or record.get("subjects") != subjects
            or not isinstance(record.get("request_digest"), str)
            or not isinstance(record.get("result_digest"), str)
            or not isinstance(record.get("binding_identity"), str)
            or not isinstance(record.get("binding_skill_digest"), str)
        ):
            continue
        successful[operation] = "successful"
    return successful


def _exact_handoff_packets(
    workspace: str,
    *,
    state: dict[str, Any],
    bundle: AssetBundle,
    operations: list[str],
    gate: str,
    step: int,
    subjects: dict[str, str],
) -> dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """Load and authenticate the unique S12/S13 pair for each exact gate.

    Projection summaries are useful for UI routing but are not an authority
    boundary.  A coordinator that derives governed records re-opens the packet
    bytes, checks their ledger-pinned digests and exact anchors, and rejects
    duplicate successful packets instead of silently selecting one.
    """

    matches: dict[
        str, list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]
    ] = {operation: [] for operation in operations}
    trusted_snapshot, trusted_snapshot_digest = handoffs._producer_trust(
        replay(workspace),
    )
    for request_id, record in sorted(state.get("handoffs", {}).items()):
        if not isinstance(record, dict):
            continue
        operation = record.get("operation")
        if (
            operation not in matches
            or record.get("status") not in {
                "successful", "reconciled_effect",
            }
            or record.get("gate") != gate
            or record.get("step") != step
            or record.get("subjects") != subjects
        ):
            continue
        request = _secure_packet_document(
            workspace, ("packets", "S12", f"{request_id}.json"),
            packet_kind="S12",
        )
        result = _secure_packet_document(
            workspace, ("packets", "S13", f"{request_id}.json"),
            packet_kind="S13",
        )
        if (
            digest(request) != record.get("request_digest")
            or digest(result) != record.get("result_digest")
        ):
            raise CLIError(
                "platform_packet_digest_mismatch",
                f"S12/S13 bytes no longer match the ledger for {request_id}",
                exit_code=3,
            )
        _, operation_contract, binding_key = bundle.operation_contract(
            operation,
        )
        configured_identity = state.get("bindings", {}).get(binding_key)
        configured_skill_digest = state.get(
            "binding_skill_digests", {},
        ).get(binding_key)
        if (
            not isinstance(configured_identity, str)
            or request.get("binding_identity") != configured_identity
            or not isinstance(configured_skill_digest, str)
            or request.get("binding_skill_digest")
            != configured_skill_digest
            or request.get("adapter_contract_digest")
            != digest(operation_contract)
            or request.get("expected_evidence")
            != operation_contract.get("required_evidence")
        ):
            raise CLIError(
                "platform_request_binding_mismatch",
                "S12 does not equal the genesis-pinned Skill identity/digest and exact operation contract",
                exit_code=3,
            )
        consumed_at = record.get("producer_attestation_consumed_at")
        if not isinstance(consumed_at, str) or not consumed_at:
            raise CLIError(
                "platform_result_consumption_time_missing",
                "accepted S13 requires its coordinator-recorded consumption time",
                exit_code=3,
            )
        try:
            handoffs._validate_result(
                request, result, bundle,
                trusted_snapshot=trusted_snapshot,
                trusted_snapshot_digest=trusted_snapshot_digest,
                consumption_time=consumed_at,
            )
        except HandoffError as exc:
            raise CLIError(exc.code, str(exc), exit_code=3) from exc
        if (
            result.get("terminal_status") != "successful"
            or result.get("producer_identity") != request.get("binding_identity")
        ):
            raise CLIError(
                "platform_producer_mismatch",
                "a derived governed record requires one exact successful result "
                "from the configured binding identity",
                exit_code=3,
            )
        matches[operation].append((request, result, record))
    missing = [name for name, values in matches.items() if not values]
    duplicate = [name for name, values in matches.items() if len(values) != 1]
    if missing:
        raise CLIError(
            "platform_results_required",
            "exact successful S13 packets are missing: " + ", ".join(missing),
            exit_code=3,
        )
    if duplicate:
        raise CLIError(
            "platform_result_ambiguous",
            "more than one successful logical S13 exists for: "
            + ", ".join(duplicate),
            exit_code=3,
        )
    return {name: values[0] for name, values in matches.items()}


def _evidence_values(
    operation: str, result: dict[str, Any], required: set[str],
) -> dict[str, Any]:
    grouped: dict[str, list[Any]] = {}
    for item in result.get("evidence", []):
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            continue
        value = item.get("value")
        if "value" not in item:
            value = {key: val for key, val in item.items() if key != "type"}
        grouped.setdefault(item["type"], []).append(value)
    missing = sorted(name for name in required if len(grouped.get(name, [])) != 1)
    if missing:
        raise CLIError(
            "platform_evidence_cardinality_invalid",
            f"{operation} must return exactly one value for: "
            + ", ".join(missing),
            exit_code=3,
        )
    return {name: grouped[name][0] for name in required}


def _required_string(
    values: dict[str, Any], name: str, operation: str,
) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value:
        raise CLIError(
            "platform_evidence_value_invalid",
            f"{operation} evidence {name} must be a non-empty string",
            exit_code=3,
        )
    return value


def _required_digest(
    values: dict[str, Any], name: str, operation: str,
) -> str:
    value = _required_string(values, name, operation)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise CLIError(
            "platform_evidence_digest_invalid",
            f"{operation} evidence {name} must be a canonical sha256 digest",
            exit_code=3,
        )
    return value


def _validated_runtime_environment(
    *, values: dict[str, Any], operation: str,
    r01: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    try:
        if r01 is None:
            manifest = canonical_runtime_environment_manifest(
                values.get("environment_manifest"),
            )
            expected_digest = runtime_environment_digest(manifest)
        else:
            manifest, expected_digest = validate_runtime_environment_against_r01(
                values.get("environment_manifest"), r01,
            )
    except ContractError as exc:
        raise CLIError(exc.code, str(exc), exit_code=3) from exc
    if values.get("environment_digest") != expected_digest:
        raise CLIError(
            "runtime_environment_digest_mismatch",
            f"{operation} environment_digest does not bind its canonical manifest",
            exit_code=3,
        )
    return manifest, expected_digest


def _receipt_reference(
    request: dict[str, Any], result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "request_id": request["request_id"],
        "request_digest": digest(request),
        "result_id": result["result_id"],
        "result_digest": digest(result),
        "receipt_digest": result["receipt_digest"],
        "producer_identity": result["producer_identity"],
        "producer_principal_id": result["producer_principal_id"],
        "producer_attestation_digest": digest(result["producer_attestation"]),
    }


def _data_platform_manifest_records(
    *, workspace: str, state: dict[str, Any], bundle: AssetBundle,
    subjects: dict[str, str], workspace_id: str,
    contract_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Derive R04/R05/R06 only from authenticated STEP03 S13 bytes.

    The caller may author contract candidates, but cannot author data-platform
    manifests.  Exact receipts and producer identity are injected from the
    unique ledger-bound S12/S13 pairs after their dataset/split/calibration and
    G10 bindings have been checked.
    """

    packets = _exact_handoff_packets(
        workspace, state=state, bundle=bundle,
        operations=["data.quality-check", "data.labeling-status"],
        gate="lifecycle-step-3", step=3, subjects=subjects,
    )
    quality_request, quality_result, _ = packets["data.quality-check"]
    label_request, label_result, _ = packets["data.labeling-status"]
    quality_required = set(bundle.operation_contract(
        "data.quality-check",
    )[1]["required_evidence"])
    label_required = set(bundle.operation_contract(
        "data.labeling-status",
    )[1]["required_evidence"])
    quality = _evidence_values(
        "data.quality-check", quality_result, quality_required,
    )
    labeling = _evidence_values(
        "data.labeling-status", label_result, label_required,
    )
    split_approvers = quality.get("split_approver_principal_ids")
    if (
        split_approvers is not None and (not isinstance(split_approvers, list)
        or not split_approvers
        or split_approvers != sorted(set(split_approvers))
        or any(
            not isinstance(principal, str) or not principal
            for principal in split_approvers
        ))
    ):
        raise CLIError(
            "split_approver_principals_invalid",
            "optional split approver metadata must be a non-empty canonical principal set",
            exit_code=3,
        )
    dataset = quality.get("dataset_manifest")
    split = quality.get("split_manifest")
    calibration = quality.get("calibration_manifest_when_applicable")
    if not isinstance(dataset, dict) or not isinstance(split, dict):
        raise CLIError(
            "data_manifest_evidence_invalid",
            "STEP03 data.quality-check must return concrete dataset_manifest and split_manifest objects",
            exit_code=3,
        )

    def canonical_manifest(
        manifest: dict[str, Any], required: tuple[str, ...], name: str,
    ) -> dict[str, Any]:
        unknown = sorted(set(manifest) - set(required))
        if unknown or any(manifest.get(field) in (None, "", [], {}) for field in required):
            raise CLIError(
                "data_manifest_evidence_invalid",
                f"{name} must contain exactly immutable fields: {', '.join(required)}",
                exit_code=3,
            )
        return {field: manifest[field] for field in required}

    dataset = canonical_manifest(dataset, (
        "dataset_identity", "version", "instance_key", "immutable_uri",
        "content_digest", "schema_digest", "shard_digests", "record_count",
    ), "dataset_manifest")
    split = canonical_manifest(split, (
        "split_identity", "version", "instance_key", "immutable_uri",
        "dataset_content_digest", "membership_digest", "partition_digests",
        "grouping_key", "hidden_access_policy",
    ), "split_manifest")
    for manifest_name, manifest, digest_fields in (
        ("dataset_manifest", dataset, ("content_digest", "schema_digest")),
        ("split_manifest", split, ("dataset_content_digest", "membership_digest")),
    ):
        if (
            not isinstance(manifest["instance_key"], str)
            or _PORTABLE_INSTANCE.fullmatch(manifest["instance_key"]) is None
            or not isinstance(manifest["immutable_uri"], str)
            or ":" not in manifest["immutable_uri"]
            or "?" in manifest["immutable_uri"]
        ):
            raise CLIError(
                "data_manifest_identity_invalid",
                f"{manifest_name} lacks a portable identity or immutable URI",
                exit_code=3,
            )
        for field in digest_fields:
            _required_digest(manifest, field, "data.quality-check")
    if split["dataset_content_digest"] != dataset["content_digest"]:
        raise CLIError(
            "split_dataset_binding_mismatch",
            "split_manifest does not bind the exact dataset_manifest content digest",
            exit_code=3,
        )
    if (
        not isinstance(dataset["shard_digests"], list)
        or not dataset["shard_digests"]
        or len(dataset["shard_digests"]) != len(set(dataset["shard_digests"]))
        or any(
            not isinstance(item, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", item) is None
            for item in dataset["shard_digests"]
        )
        or type(dataset["record_count"]) is not int
        or dataset["record_count"] < 0
        or not isinstance(split["partition_digests"], dict)
        or not split["partition_digests"]
        or any(
            not isinstance(item, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", item) is None
            for item in split["partition_digests"].values()
        )
    ):
        raise CLIError(
            "data_manifest_inventory_invalid",
            "dataset/split manifests must bind exact shard, count, and partition membership inventories",
            exit_code=3,
        )
    if (
        quality.get("dataset_identity") != dataset["dataset_identity"]
        or quality.get("split_identity") != split["split_identity"]
        or quality.get("immutable_uri") != dataset["immutable_uri"]
        or quality.get("content_digest") != dataset["content_digest"]
        or quality.get("dataset_manifest_digest") != digest(dataset)
        or quality.get("split_manifest_digest") != digest(split)
        or labeling.get("dataset_identity") != dataset["dataset_identity"]
        or labeling.get("immutable_uri") != dataset["immutable_uri"]
        or labeling.get("content_digest") != dataset["content_digest"]
    ):
        raise CLIError(
            "data_receipt_summary_mismatch",
            "STEP03 S13 summaries do not exactly repeat their concrete immutable manifests",
            exit_code=3,
        )
    contract_by_type = {
        value.get("artifact_type"): value for value in contract_records
        if isinstance(value, dict)
    }
    evaluator = contract_by_type.get("G10")
    if not isinstance(evaluator, dict) or quality.get("evaluator_digest") != digest(evaluator):
        raise CLIError(
            "data_evaluator_binding_mismatch",
            "STEP03 data receipt must bind the exact published G10 candidate bytes",
            exit_code=3,
        )
    if quality.get("baseline_lock_digest") != "not-applicable":
        raise CLIError(
            "data_baseline_binding_invalid",
            "STEP03 precedes B02 and must explicitly mark baseline_lock_digest not-applicable",
            exit_code=3,
        )
    calibration_required = "G05" in contract_by_type
    calibration_digest = quality.get(
        "calibration_manifest_digest_when_applicable",
    )
    if calibration_required:
        if not isinstance(calibration, dict):
            raise CLIError(
                "calibration_manifest_required",
                "applicable G05 requires one concrete calibration_manifest in STEP03 S13",
                exit_code=3,
            )
        calibration = canonical_manifest(calibration, (
            "calibration_identity", "version", "instance_key", "immutable_uri",
            "content_digest", "validity",
        ), "calibration_manifest")
        _required_digest(calibration, "content_digest", "data.quality-check")
        validity = calibration.get("validity")
        if isinstance(validity, dict) and validity.get("mode") == "snapshot":
            if (
                validity.get("dataset_content_digest") != dataset["content_digest"]
                or validity.get("membership_digest") != split["membership_digest"]
                or validity.get("calibration_content_digest") != calibration["content_digest"]
                or contract_by_type["G05"].get("content", {}).get("validity") != validity
            ):
                raise CLIError("calibration_snapshot_binding_mismatch",
                    "STEP03 calibration snapshot must match G05 and the exact data/split/calibration bytes",
                    exit_code=3)
        if (
            quality.get("calibration_identity_when_applicable")
            != calibration["calibration_identity"]
            or calibration_digest != digest(calibration)
        ):
            raise CLIError(
                "calibration_receipt_binding_mismatch",
                "STEP03 calibration summary does not bind the concrete R06 source",
                exit_code=3,
            )
    elif calibration is not None or calibration_digest != "not-applicable" \
            or quality.get("calibration_identity_when_applicable") != "not-applicable":
        raise CLIError(
            "calibration_applicability_mismatch",
            "non-calibrated STEP03 must explicitly return not-applicable calibration evidence",
            exit_code=3,
        )

    receipt = _receipt_reference(quality_request, quality_result)
    envelope_subjects = {"workspace_id": workspace_id, **subjects}
    derived: list[dict[str, Any]] = []
    for artifact_id, manifest in (("R04", dataset), ("R05", split)):
        content = {**manifest, "receipt": receipt}
        if artifact_id == "R05":
            # Record authenticated production authorship without inventing an
            # approval. Legacy approval metadata remains readable when present.
            content["author_principal_ids"] = [quality_result["producer_principal_id"]]
            if split_approvers is not None:
                content["approver_principal_ids"] = split_approvers
        value = _envelope(
            artifact_id, manifest["instance_key"], manifest["instance_key"],
            content, envelope_subjects,
        )
        value["created_by"] = quality_result["producer_identity"]
        derived.append(value)
    if calibration_required:
        assert isinstance(calibration, dict)
        value = _envelope(
            "R06", calibration["instance_key"], calibration["instance_key"],
            {**calibration, "receipt": receipt}, envelope_subjects,
        )
        value["created_by"] = quality_result["producer_identity"]
        derived.append(value)
    # Keep the labeling packet in the immutable production provenance even
    # though R04-R06 are owned by the quality result.
    for value in derived:
        value.setdefault("extensions", {})["dev.bmad.sdm.label-receipt"] = \
            _receipt_reference(label_request, label_result)
    return derived


def _baseline_platform_records(
    *, workspace: str, state: dict[str, Any], bundle: AssetBundle,
    subjects: dict[str, str], workspace_id: str, payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Derive B02/B03 from the one authenticated STEP05 evidence closure.

    The caller selects immutable B01/R04/R05/(R06)/G10 inputs but never
    supplies either output.  Every scientific and implementation fact below
    is re-opened from a ledger-bound S13 and cross-checked before the two
    deterministic unsigned authority candidates are constructed.
    """

    def selected(
        artifact_id: str, *, required: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any], str] | None:
        selectors = payload.get("input_refs", {}).get(artifact_id)
        if isinstance(selectors, dict):
            selectors = [selectors]
        if selectors is None and not required:
            return None
        if (
            not isinstance(selectors, list)
            or len(selectors) != 1
            or not isinstance(selectors[0], dict)
            or set(selectors[0]) != {"instance_key", "digest"}
        ):
            raise CLIError(
                "baseline_input_identity_invalid",
                f"STEP05 requires exactly one immutable {artifact_id} selector",
                exit_code=3,
            )
        selector = selectors[0]
        value, record = _validated_stored_artifact(
            state, workspace, bundle, artifact_id, selector["instance_key"],
        )
        if selector["digest"] != record["digest"]:
            raise CLIError(
                "baseline_input_digest_mismatch",
                f"STEP05 {artifact_id} selector is stale or substituted",
                exit_code=3,
            )
        return value, record, f"{artifact_id}:{selector['instance_key']}"

    b01_value, b01_record, _ = selected("B01")  # type: ignore[misc]
    r04_value, r04_record, r04_ref = selected("R04")  # type: ignore[misc]
    r05_value, r05_record, r05_ref = selected("R05")  # type: ignore[misc]
    g10_value, g10_record, g10_ref = selected("G10")  # type: ignore[misc]
    r06_selected = selected("R06", required=False)
    b01 = b01_value.get("content", b01_value)
    r04 = r04_value.get("content", r04_value)
    r05 = r05_value.get("content", r05_value)
    g10 = g10_value.get("content", g10_value)
    if not all(isinstance(item, dict) for item in (b01, r04, r05, g10)):
        raise CLIError(
            "baseline_input_content_invalid",
            "B01/R04/R05/G10 must expose concrete immutable content objects",
            exit_code=3,
        )
    assert isinstance(b01, dict) and isinstance(r04, dict)
    assert isinstance(r05, dict) and isinstance(g10, dict)
    baseline_id = b01.get("baseline_id")
    if (
        not isinstance(baseline_id, str)
        or not baseline_id
        or subjects.get("baseline_id") != baseline_id
    ):
        raise CLIError(
            "baseline_subject_mismatch",
            "STEP05 baseline subject must equal the exact B01 baseline_id",
            exit_code=3,
        )
    owners = g10.get("owner_principal_ids")
    if (
        not isinstance(owners, list)
        or not owners
        or owners != sorted(set(owners))
        or any(not isinstance(owner, str) or not owner for owner in owners)
    ):
        raise CLIError(
            "evaluator_owner_principals_invalid",
            "G10 must bind a non-empty canonical evaluator owner principal set",
            exit_code=3,
        )

    packets = _exact_handoff_packets(
        workspace, state=state, bundle=bundle,
        operations=[
            "git.snapshot", "ml.baseline-reproduction",
            "ml.baseline-locked-evaluation",
        ],
        gate="lifecycle-step-5", step=5, subjects=subjects,
    )
    evidence: dict[str, dict[str, Any]] = {}
    for operation, (_, result, _) in packets.items():
        required_evidence = set(
            bundle.operation_contract(operation)[1]["required_evidence"],
        )
        evidence[operation] = _evidence_values(
            operation, result, required_evidence,
        )
    git = evidence["git.snapshot"]
    ci = evidence["ml.baseline-reproduction"]
    ml = evidence["ml.baseline-locked-evaluation"]

    for operation, values, names in (
        ("git.snapshot", git, (
            "tree_digest", "resolved_config_digest",
            "operation_receipt_digest",
        )),
        ("ml.baseline-reproduction", ci, (
            "pipeline_definition_digest", "tree_digest",
            "resolved_config_digest", "dataset_manifest_digest",
            "split_manifest_digest", "evaluator_digest",
            "runtime_environment_digest", "checkpoint_digest",
            "model_artifact_digest", "metrics_digest",
            "artifact_receipt_digest",
        )),
        ("ml.baseline-locked-evaluation", ml, (
            "checkpoint_digest", "model_artifact_digest",
            "frozen_spec_digest",
            "evaluator_digest", "data_digest", "split_digest",
            "environment_digest", "metrics_digest",
        )),
    ):
        for name in names:
            _required_digest(values, name, operation)
    for operation, values, names in (
        ("git.snapshot", git, (
            "repository_identity", "revision", "snapshot_at",
            "operation_receipt_uri",
        )),
        ("ml.baseline-reproduction", ci, (
            "controller_identity", "job_identity", "build_identity",
            "baseline_identity", "revision", "run_id", "attempt_id",
            "checkpoint_uri", "started_at", "completed_at",
            "artifact_receipt_uri",
        )),
        ("ml.baseline-locked-evaluation", ml, (
            "run_id", "attempt_id", "selected_checkpoint_attempt_id",
            "checkpoint_uri", "metrics_uri", "started_at", "completed_at",
        )),
    ):
        for name in names:
            _required_string(values, name, operation)

    runtime_manifest = ci.get("runtime_environment_manifest")
    metrics = ml.get("scientific_facts")
    tolerance = ci.get("tolerance_result")
    try:
        runtime_manifest = canonical_runtime_environment_manifest(
            runtime_manifest,
        )
        runtime_digest = runtime_environment_digest(runtime_manifest)
    except ContractError as exc:
        raise CLIError(exc.code, str(exc), exit_code=3) from exc
    if ci.get("runtime_environment_digest") != runtime_digest:
        raise CLIError(
            "runtime_environment_digest_mismatch",
            "jenkins baseline runtime digest does not bind its canonical manifest",
            exit_code=3,
        )
    ml_runtime_manifest, ml_runtime_digest = _validated_runtime_environment(
        values=ml, operation="ml.baseline-locked-evaluation",
    )
    if (
        ml_runtime_manifest != runtime_manifest
        or ml_runtime_digest != runtime_digest
    ):
        raise CLIError(
            "baseline_runtime_environment_mismatch",
            "Jenkins and ML baseline results do not attest the same actual runtime environment",
            exit_code=3,
        )
    if not isinstance(metrics, dict) or not metrics:
        raise CLIError(
            "baseline_metrics_invalid",
            "locked evaluation must carry non-empty observed scientific facts",
            exit_code=3,
        )
    if (
        not isinstance(tolerance, dict)
        or set(tolerance) != {"passed", "summary"}
        or tolerance.get("passed") is not True
        or not isinstance(tolerance.get("summary"), str)
        or not tolerance["summary"]
    ):
        raise CLIError(
            "baseline_tolerance_failed",
            "baseline reproduction must return one passing structured tolerance result",
            exit_code=3,
        )
    for name, expected in (
        ("attempt_terminal_status", "successful"),
        ("terminal_status", "successful"),
    ):
        if ml.get(name) != expected:
            raise CLIError(
                "baseline_evaluation_not_successful",
                f"ml.baseline-locked-evaluation {name} must be successful",
                exit_code=3,
            )
    if (
        ml.get("attempt_validity") is not True
        or ml.get("system_failure") is not False
        or ml.get("artifact_completeness") is not True
    ):
        raise CLIError(
            "baseline_evaluation_invalid",
            "locked baseline evaluation must be valid, complete, and free of system failure",
            exit_code=3,
        )

    r06_record: dict[str, Any] | None = None
    calibration_digest: str = "not-applicable"
    if r06_selected is not None:
        _, r06_record, _ = r06_selected
        calibration_digest = r06_record["digest"]
    expected = {
        "baseline_identity": baseline_id,
        "revision": git["revision"],
        "tree_digest": git["tree_digest"],
        "resolved_config_digest": git["resolved_config_digest"],
        "dataset_manifest_digest": r04_record["digest"],
        "split_manifest_digest": r05_record["digest"],
        "calibration_manifest_digest_when_applicable": calibration_digest,
        "evaluator_digest": g10_record["digest"],
    }
    mismatched = sorted(
        name for name, bound in expected.items() if ci.get(name) != bound
    )
    if mismatched:
        raise CLIError(
            "baseline_ci_binding_mismatch",
            "jenkins baseline evidence substitutes immutable inputs: "
            + ", ".join(mismatched),
            exit_code=3,
        )
    ml_expected = {
        "run_id": ci["run_id"],
        "attempt_id": ci["attempt_id"],
        "selected_checkpoint_attempt_id": ci["attempt_id"],
        "checkpoint_uri": ci["checkpoint_uri"],
        "checkpoint_digest": ci["checkpoint_digest"],
        "model_artifact_digest": ci["model_artifact_digest"],
        "frozen_spec_digest": b01_record["digest"],
        "evaluator_digest": g10_record["digest"],
        "data_digest": r04["content_digest"],
        "split_digest": r05["membership_digest"],
        "environment_digest": runtime_digest,
        "metrics_digest": ci["metrics_digest"],
    }
    mismatched = sorted(
        name for name, bound in ml_expected.items() if ml.get(name) != bound
    )
    if mismatched or ml["metrics_digest"] != digest(metrics):
        raise CLIError(
            "baseline_evaluation_binding_mismatch",
            "locked evaluation substitutes CI/input/runtime/metric facts: "
            + ", ".join(mismatched or ["canonical content digest"]),
            exit_code=3,
        )

    timestamps = {
        "git_snapshot_at": git["snapshot_at"],
        "jenkins_started_at": ci["started_at"],
        "jenkins_completed_at": ci["completed_at"],
        "ml_started_at": ml["started_at"],
        "ml_completed_at": ml["completed_at"],
    }
    timestamp_pattern = re.compile(
        r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:"
        r"[0-9]{2}(?:\.[0-9]{1,9})?Z$",
    )
    try:
        if any(
            not isinstance(value, str)
            or timestamp_pattern.fullmatch(value) is None
            for value in timestamps.values()
        ):
            raise ValueError("timestamp is not canonical UTC")
        parsed = {
            name: datetime.fromisoformat(value.replace("Z", "+00:00"))
            for name, value in timestamps.items()
        }
    except (AttributeError, TypeError, ValueError) as exc:
        raise CLIError(
            "baseline_timestamp_invalid",
            "baseline evidence timestamps must be canonical UTC instants",
            exit_code=3,
        ) from exc
    try:
        ordered = (
            parsed["jenkins_started_at"] <= parsed["jenkins_completed_at"]
            and parsed["ml_started_at"] <= parsed["ml_completed_at"]
            and parsed["git_snapshot_at"] <= parsed["jenkins_started_at"]
        )
    except TypeError as exc:
        raise CLIError(
            "baseline_timestamp_invalid",
            "baseline evidence timestamps must be timezone-aware UTC instants",
            exit_code=3,
        ) from exc
    if not ordered:
        raise CLIError(
            "baseline_timestamp_order_invalid",
            "baseline snapshot/CI/evaluation timestamps are not causally ordered",
            exit_code=3,
        )

    def receipt(
        operation: str, provider_uri: str, provider_digest: str,
    ) -> dict[str, Any]:
        request, result, _ = packets[operation]
        return {
            **_receipt_reference(request, result),
            "provider_receipt_uri": provider_uri,
            "provider_receipt_digest": provider_digest,
        }

    receipts = {
        "git.snapshot": receipt(
            "git.snapshot", git["operation_receipt_uri"],
            git["operation_receipt_digest"],
        ),
        "ml.baseline-reproduction": receipt(
            "ml.baseline-reproduction", ci["artifact_receipt_uri"],
            ci["artifact_receipt_digest"],
        ),
        "ml.baseline-locked-evaluation": receipt(
            "ml.baseline-locked-evaluation", ml["metrics_uri"],
            ml["metrics_digest"],
        ),
    }
    fingerprint = {
        "baseline_id": baseline_id,
        "baseline_spec_digest": b01_record["digest"],
        "repository_identity": git["repository_identity"],
        "code_revision": git["revision"],
        "tree_digest": git["tree_digest"],
        "resolved_config_digest": git["resolved_config_digest"],
        "dataset_manifest_ref": r04_ref,
        "dataset_manifest_digest": r04_record["digest"],
        "split_manifest_ref": r05_ref,
        "split_manifest_digest": r05_record["digest"],
        "calibration_applicable": r06_record is not None,
        "calibration_manifest_digest": calibration_digest,
        "evaluator_contract_ref": g10_ref,
        "evaluator_contract_digest": g10_record["digest"],
        "evaluator_identity": g10["evaluator_identity"],
        "evaluator_owner_principal_ids": owners,
        "runtime_environment_manifest": runtime_manifest,
        "runtime_environment_digest": runtime_digest,
        "run_id": ci["run_id"],
        "attempt_id": ci["attempt_id"],
        "checkpoint_uri": ci["checkpoint_uri"],
        "checkpoint_digest": ci["checkpoint_digest"],
        "model_artifact_digest": ci["model_artifact_digest"],
    }
    fingerprint_digest = digest(fingerprint)
    lock_content = {
        "baseline_lock_id": f"{baseline_id}-lock",
        "baseline_id": baseline_id,
        "baseline_digest": b01_record["digest"],
        "data_digest": r04["content_digest"],
        "evaluator_digest": g10_record["digest"],
        "baseline_fingerprint": fingerprint,
        "baseline_fingerprint_digest": fingerprint_digest,
        "artifact_uri": ci["checkpoint_uri"],
        "timestamps": timestamps,
        "receipts": receipts,
        "reviewer_role": "independent-protocol-reviewer",
        "authority": [],
    }
    reproduction_content = {
        "reproduction_id": f"{baseline_id}-reproduction",
        "baseline_id": baseline_id,
        "baseline_digest": b01_record["digest"],
        "baseline_fingerprint": fingerprint,
        "baseline_fingerprint_digest": fingerprint_digest,
        "run": {"run_id": ci["run_id"], "attempt_id": ci["attempt_id"]},
        "metrics": metrics,
        "metrics_digest": ml["metrics_digest"],
        "tolerance_result": tolerance,
        "timestamps": timestamps,
        "receipts": receipts,
        "authority": [],
    }

    envelope_subjects = {"workspace_id": workspace_id, **subjects}

    def envelope(artifact_type: str, instance_key: str, content: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": "sdm-artifact/v1",
            "artifact_type": artifact_type,
            "artifact_id": instance_key,
            "instance_key": instance_key,
            "version": 1,
            "status": "published",
            "subjects": envelope_subjects,
            "content": content,
            "content_digest": digest(content),
            "uri": f"artifact://{artifact_type}/{instance_key}",
            "created_at": ml["completed_at"],
            "created_by": "sdm-lifecycle",
        }

    return [
        envelope("B02", f"{baseline_id}-lock", lock_content),
        envelope("B03", f"{baseline_id}-reproduction", reproduction_content),
    ]


def _validated_run_scope(
    *, state: dict[str, Any], workspace: str, bundle: AssetBundle,
    subjects: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    run_id = subjects.get("run_id")
    attempt_id = subjects.get("attempt_id")
    experiment_id = subjects.get("experiment_id")
    if not all(isinstance(value, str) and value for value in (
        run_id, attempt_id, experiment_id,
    )):
        raise CLIError(
            "run_subjects_required",
            "platform execution rows require exact experiment_id, run_id, and attempt_id subjects",
            exit_code=3,
        )
    manifest, record = _validated_stored_artifact(
        state, workspace, bundle, "R01", run_id,
    )
    content = manifest.get("content", manifest)
    if (
        content.get("run_id") != run_id
        or content.get("experiment_id") != experiment_id
        or attempt_id not in content.get("required_attempt_ids", [])
    ):
        raise CLIError(
            "run_subject_binding_mismatch",
            "R01 does not bind the exact selected Experiment/Run/Attempt",
            exit_code=3,
        )
    return manifest, record


def _runtime_bound_subjects(
    *, subjects: dict[str, str], state: dict[str, Any], workspace: str,
    bundle: AssetBundle,
) -> dict[str, str]:
    manifest, record = _validated_run_scope(
        state=state, workspace=workspace, bundle=bundle, subjects=subjects,
    )
    content = manifest.get("content", manifest)
    derived = {
        "run_manifest_digest": record["digest"],
        "expected_runtime_image_digest": content.get(
            "expected_runtime_image_digest",
        ),
        "expected_runtime_profile_digest": content.get(
            "expected_runtime_profile_digest",
        ),
    }
    if any(
        not isinstance(value, str)
        or (name.endswith("_digest") and re.fullmatch(
            r"sha256:[0-9a-f]{64}", value,
        ) is None)
        for name, value in derived.items()
    ):
        raise CLIError(
            "run_runtime_contract_invalid",
            "R01 lacks exact immutable runtime image/profile bindings",
            exit_code=3,
        )
    conflicts = sorted(
        name for name, value in derived.items()
        if name in subjects and subjects[name] != value
    )
    if conflicts:
        raise CLIError(
            "run_runtime_subject_substitution",
            "caller substitutes R01 runtime subjects: " + ", ".join(conflicts),
            exit_code=3,
        )
    return {**subjects, **derived}


def _locked_evaluation_subjects(
    *, subjects: dict[str, str], state: dict[str, Any], workspace: str,
    bundle: AssetBundle,
) -> dict[str, str]:
    training_attempt = subjects.get("selected_checkpoint_attempt_id")
    run_id = subjects.get("run_id")
    if not isinstance(training_attempt, str) or not training_attempt:
        raise CLIError(
            "evaluation_checkpoint_subject_required",
            "STEP16 subjects must select one exact training checkpoint Attempt",
            exit_code=3,
        )
    actual, actual_record = _validated_stored_artifact(
        state, workspace, bundle, "R07", training_attempt,
    )
    index, index_record = _validated_stored_artifact(
        state, workspace, bundle, "R08", training_attempt,
    )
    actual_content = actual.get("content", actual)
    index_content = index.get("content", index)
    checkpoint_digest = actual_content.get("checkpoint_digest")
    model_digest = actual_content.get("model_artifact_digest")
    if (
        actual_content.get("attempt_kind") != "training"
        or index_content.get("attempt_kind") != "training"
        or actual_content.get("run_id") != run_id
        or index_content.get("run_id") != run_id
        or actual_content.get("attempt_id") != training_attempt
        or index_content.get("attempt_id") != training_attempt
        or index_content.get("checkpoint_digest") != checkpoint_digest
        or index_content.get("model_artifact_digest") != model_digest
        or not isinstance(checkpoint_digest, str)
        or not isinstance(model_digest, str)
    ):
        raise CLIError(
            "evaluation_checkpoint_binding_invalid",
            "selected STEP16 checkpoint/model does not exactly derive from one training R07/R08 pair",
            exit_code=3,
        )
    derived = {
        "checkpoint_digest": checkpoint_digest,
        "model_artifact_digest": model_digest,
        "training_r07_digest": actual_record["digest"],
        "training_r08_digest": index_record["digest"],
    }
    conflicts = sorted(
        name for name, value in derived.items()
        if name in subjects and subjects[name] != value
    )
    if conflicts:
        raise CLIError(
            "evaluation_checkpoint_subject_mismatch",
            "caller STEP16 subjects substitute training model anchors: "
            + ", ".join(conflicts),
            exit_code=3,
        )
    return {**subjects, **derived}


def _hpo_configuration(
    *, state: dict[str, Any], workspace: str, bundle: AssetBundle,
    subjects: dict[str, str],
) -> tuple[bool, tuple[dict[str, Any], dict[str, Any]] | None]:
    experiment_id = subjects.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise CLIError(
            "experiment_subject_required",
            "HPO applicability requires an exact Experiment identity",
            exit_code=3,
        )
    frozen, _ = _validated_stored_artifact(
        state, workspace, bundle, "E01", f"{experiment_id}.frozen",
    )
    content = frozen.get("content", frozen)
    search = content.get("search")
    if isinstance(search, dict):
        mode = search.get("mode")
        instance_key = search.get("search_spec_instance_key")
        if mode not in {"none", "hpo"}:
            raise CLIError(
                "hpo_applicability_missing",
                "frozen E01 search.mode must be exactly none or hpo",
                exit_code=3,
            )
        enabled = mode == "hpo"
    elif isinstance(content.get("hpo_enabled"), bool):
        enabled = content["hpo_enabled"]
        instance_key = content.get("search_spec_instance_key")
    else:
        raise CLIError(
            "hpo_applicability_missing",
            "frozen E01 must explicitly pin whether HPO is applicable",
            exit_code=3,
        )
    if not enabled:
        if instance_key not in (None, ""):
            raise CLIError(
                "hpo_applicability_conflict",
                "non-HPO E01 cannot pin an E03 search specification",
                exit_code=3,
            )
        return False, None
    if not isinstance(instance_key, str) or not instance_key:
        raise CLIError(
            "search_spec_binding_missing",
            "HPO E01 must pin one exact E03 instance key",
            exit_code=3,
        )
    search_spec, search_record = _validated_stored_artifact(
        state, workspace, bundle, "E03", instance_key,
    )
    if search_spec.get("subjects", {}).get("experiment_id") != experiment_id:
        raise CLIError(
            "search_spec_binding_mismatch",
            "E03 does not bind the frozen Experiment subject",
            exit_code=3,
        )
    return True, (search_spec, search_record)


def _validated_hpo_frozen_context(
    *, state: dict[str, Any], workspace: str, bundle: AssetBundle,
    experiment_id: str, search_spec: dict[str, Any],
) -> dict[str, Any]:
    """Re-open the exact R05/E04 anchors named by frozen E03."""

    search_content = search_spec.get("content", search_spec)
    split_ref = search_content.get("split_manifest_ref")
    if not isinstance(split_ref, str) or split_ref.count(":") != 1:
        raise CLIError(
            "hpo_split_manifest_binding_invalid",
            "frozen E03 must name one exact R05 split manifest",
            exit_code=3,
        )
    split_type, split_key = split_ref.split(":", 1)
    if split_type != "R05":
        raise CLIError(
            "hpo_split_manifest_binding_invalid",
            "frozen E03 split manifest reference must be an R05 identity",
            exit_code=3,
        )
    split_manifest, split_record = _validated_stored_artifact(
        state, workspace, bundle, "R05", split_key,
    )
    split_content = split_manifest.get("content", split_manifest)
    partitions = split_content.get("partition_digests")
    objective = search_content.get("objective")
    confirmation = search_content.get("confirmation")
    partition_bindings = (
        (
            objective.get("search_partition_name"),
            objective.get("search_partition_digest"),
        ),
        (
            objective.get("recheck_partition_name"),
            objective.get("recheck_partition_digest"),
        ),
        (
            objective.get("formal_test_partition_name"),
            objective.get("formal_test_partition_digest"),
        ),
        (
            confirmation.get("partition_name"),
            confirmation.get("partition_digest"),
        ),
    ) if isinstance(objective, dict) and isinstance(confirmation, dict) else ()
    if (
        search_content.get("split_manifest_digest") != split_record["digest"]
        or not isinstance(partitions, dict)
        or len(partition_bindings) != 4
        or any(
            not isinstance(name, str)
            or not isinstance(bound, str)
            or partitions.get(name) != bound
            for name, bound in partition_bindings
        )
        or len({bound for _, bound in partition_bindings}) != 4
    ):
        raise CLIError(
            "hpo_partition_binding_mismatch",
            "E03 search/recheck/confirmation/formal-test partitions must be four distinct exact R05 partition digests",
            exit_code=3,
        )

    run_plan_id = search_content.get("run_plan_id")
    run_plan, run_plan_record = _validated_stored_artifact(
        state, workspace, bundle, "E04", run_plan_id,
    )
    run_plan_content = run_plan.get("content", run_plan)
    frozen, _ = _validated_stored_artifact(
        state, workspace, bundle, "E01", f"{experiment_id}.frozen",
    )
    frozen_content = frozen.get("content", frozen)
    rows = run_plan_content.get("runs")
    rows_by_run = {
        row.get("run_id"): row for row in rows
        if isinstance(row, dict) and isinstance(row.get("run_id"), str)
    } if isinstance(rows, list) else {}
    trial_plan = search_content.get("trial_plan")
    confirmation = search_content.get("confirmation")
    expected_runs = {
        row.get("run_id") for row in trial_plan
        if isinstance(row, dict)
    } if isinstance(trial_plan, list) else set()
    if isinstance(confirmation, dict):
        expected_runs.add(confirmation.get("run_id"))
    if (
        run_plan_content.get("experiment_id") != experiment_id
        or frozen_content.get("run_plan_id") != run_plan_id
        or set(rows_by_run) != expected_runs
    ):
        raise CLIError(
            "hpo_run_plan_binding_mismatch",
            "E03 and frozen E01 must select one E04 containing exactly every HPO search/recheck/confirmation Run",
            exit_code=3,
        )
    assert isinstance(trial_plan, list) and isinstance(confirmation, dict)
    for planned in trial_plan:
        e04_row = rows_by_run.get(planned["run_id"])
        if (
            not isinstance(e04_row, dict)
            or e04_row.get("required_attempt_ids") != [planned["attempt_id"]]
            or e04_row.get("hpo_phase") != planned["phase"]
            or e04_row.get("split_digest") != planned["partition_digest"]
            or e04_row.get("seed") != planned["seed"]
            or e04_row.get("environment_plan_digest")
            != planned["environment_plan_digest"]
            or e04_row.get("expected_runtime_image_digest")
            != planned["environment_plan"]["expected_runtime_image_digest"]
            or e04_row.get("expected_runtime_profile")
            != planned["environment_plan"]["expected_runtime_profile"]
            or e04_row.get("configuration_digest")
            != planned["configuration_digest"]
        ):
            raise CLIError(
                "hpo_run_plan_binding_mismatch",
                f"E04 does not exactly repeat frozen E03 trial {planned['attempt_id']}",
                exit_code=3,
            )
    confirmation_row = rows_by_run.get(confirmation["run_id"])
    if (
        not isinstance(confirmation_row, dict)
        or confirmation_row.get("required_attempt_ids")
        != [confirmation["attempt_id"]]
        or confirmation_row.get("hpo_phase") != "confirmation"
        or confirmation_row.get("split_digest")
        != confirmation["partition_digest"]
        or confirmation_row.get("seed")
        != confirmation["independent_seed"]
        or confirmation_row.get("environment_plan_digest")
        != confirmation["independent_environment_plan_digest"]
        or confirmation_row.get("expected_runtime_image_digest")
        != confirmation["independent_environment_plan"][
            "expected_runtime_image_digest"
        ]
        or confirmation_row.get("expected_runtime_profile")
        != confirmation["independent_environment_plan"][
            "expected_runtime_profile"
        ]
    ):
        raise CLIError(
            "hpo_confirmation_binding_mismatch",
            "E04 does not exactly repeat frozen E03 confirmation seed/partition/environment",
            exit_code=3,
        )
    return {
        "split_record": split_record,
        "partitions": partitions,
        "run_plan_record": run_plan_record,
        "run_rows": rows_by_run,
    }


def _hpo_search_subjects(
    *, subjects: dict[str, str], state: dict[str, Any], workspace: str,
    bundle: AssetBundle,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    configured = _hpo_configuration(
        state=state, workspace=workspace, bundle=bundle, subjects=subjects,
    )[1]
    assert configured is not None
    search_spec, search_record = configured
    content = search_spec.get("content", search_spec)
    trial_rows = content.get("trial_plan")
    if not isinstance(trial_rows, list) or not trial_rows:
        raise CLIError(
            "hpo_trial_plan_missing", "HPO search has no frozen trial plan",
            exit_code=3,
        )
    manifest_rows: list[dict[str, str]] = []
    for row in sorted(trial_rows, key=lambda item: (
        item.get("run_id", "") if isinstance(item, dict) else "",
        item.get("attempt_id", "") if isinstance(item, dict) else "",
    )):
        run_id = row.get("run_id") if isinstance(row, dict) else None
        attempt_id = row.get("attempt_id") if isinstance(row, dict) else None
        if not isinstance(run_id, str) or not isinstance(attempt_id, str):
            raise CLIError(
                "hpo_trial_plan_invalid", "HPO trial identity is malformed",
                exit_code=3,
            )
        manifest, manifest_record = _validated_stored_artifact(
            state, workspace, bundle, "R01", run_id,
        )
        manifest_content = manifest.get("content", manifest)
        if attempt_id not in manifest_content.get("required_attempt_ids", []):
            raise CLIError(
                "hpo_trial_run_manifest_mismatch",
                "trial R01 does not bind its exact planned Attempt",
                exit_code=3,
            )
        manifest_rows.append({
            "run_id": run_id, "attempt_id": attempt_id,
            "run_manifest_digest": manifest_record["digest"],
        })
    set_digest = digest({
        "schema": "sdm-hpo-trial-run-manifest-set/v1",
        "items": manifest_rows,
    })
    derived = {
        "search_spec_digest": search_record["digest"],
        "trial_run_manifest_set_digest": set_digest,
    }
    conflicts = sorted(
        name for name, value in derived.items()
        if name in subjects and subjects[name] != value
    )
    if conflicts:
        raise CLIError(
            "hpo_search_subject_substitution",
            "caller substitutes frozen HPO request anchors: "
            + ", ".join(conflicts), exit_code=3,
        )
    return {**subjects, **derived}, manifest_rows


def _validated_hpo_search_phase(
    *,
    state: dict[str, Any],
    workspace: str,
    bundle: AssetBundle,
    subjects: dict[str, str],
    packet: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> dict[str, Any]:
    """Validate one HPO search result against the complete frozen search plan.

    This is the sole semantic validator for both the initial selection commit
    and later idempotent confirmation dispatch.  It is intentionally pure: no
    trial artifact or selection event is written until every returned Attempt,
    the failed/cancelled set, formal-test isolation, and the selected
    configuration have been checked against E01/E03/R01/R02.
    """

    experiment_id = subjects.get("experiment_id")
    run_id = subjects.get("run_id")
    attempt_id = subjects.get("attempt_id")
    if not all(isinstance(value, str) and value for value in (
        experiment_id, run_id, attempt_id,
    )):
        raise CLIError(
            "run_subjects_required",
            "HPO selection requires exact experiment_id, confirmation run_id, and confirmation attempt_id",
            exit_code=3,
        )
    search_spec_pair = _hpo_configuration(
        state=state, workspace=workspace, bundle=bundle, subjects=subjects,
    )[1]
    assert search_spec_pair is not None
    search_spec, search_record = search_spec_pair
    search_request, search_result, _ = packet
    operation = "ml.hpo-search"
    search_required = set(bundle.operation_contract(operation)[1][
        "required_evidence"
    ])
    search_values = _evidence_values(
        operation, search_result, search_required,
    )
    search_content = search_spec.get("content", search_spec)
    frozen_context = _validated_hpo_frozen_context(
        state=state, workspace=workspace, bundle=bundle,
        experiment_id=experiment_id, search_spec=search_spec,
    )
    trial_plan = search_content.get("trial_plan")
    confirmation = search_content.get("confirmation")
    budget = search_content.get("budget")
    objective = search_content.get("objective")
    selection_rule = search_content.get("selection_rule")
    if (
        not isinstance(trial_plan, list)
        or not trial_plan
        or not isinstance(confirmation, dict)
    ):
        raise CLIError(
            "hpo_trial_plan_missing",
            "frozen E03 must explicitly enumerate trial_plan and independent confirmation identities",
            exit_code=3,
        )
    confirmation_seed = confirmation.get("independent_seed")
    confirmation_environment_plan = confirmation.get(
        "independent_environment_plan",
    )
    confirmation_environment_plan_digest = confirmation.get(
        "independent_environment_plan_digest",
    )
    try:
        confirmation_environment_plan = canonical_runtime_environment_plan(
            confirmation_environment_plan,
        )
        expected_confirmation_plan_digest = runtime_environment_plan_digest(
            confirmation_environment_plan,
        )
    except ContractError as exc:
        raise CLIError(exc.code, str(exc), exit_code=3) from exc
    if (
        confirmation.get("run_id") != run_id
        or confirmation.get("attempt_id") != attempt_id
        or not isinstance(confirmation_seed, str)
        or not confirmation_seed
        or confirmation_environment_plan_digest
        != expected_confirmation_plan_digest
    ):
        raise CLIError(
            "hpo_confirmation_binding_mismatch",
            "frozen E03 must bind the exact independently seeded confirmation Run/Attempt/environment plan",
            exit_code=3,
        )

    planned_pairs: set[tuple[str, str]] = set()
    planned_run_ids: set[str] = set()
    planned_attempt_ids: set[str] = set()
    planned_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for trial in trial_plan:
        trial_run_id = trial.get("run_id") if isinstance(trial, dict) else None
        trial_attempt_id = trial.get("attempt_id") \
            if isinstance(trial, dict) else None
        if (
            not isinstance(trial_run_id, str)
            or trial_run_id in planned_run_ids
            or not isinstance(trial_attempt_id, str)
            or not trial_attempt_id
            or trial_attempt_id in planned_attempt_ids
        ):
            raise CLIError(
                "hpo_trial_plan_invalid",
                "E03 trial_plan must uniquely enumerate every trial Run/Attempt",
                exit_code=3,
            )
        planned_run_ids.add(trial_run_id)
        pair = (trial_run_id, trial_attempt_id)
        planned_pairs.add(pair)
        planned_attempt_ids.add(trial_attempt_id)
        planned_by_pair[pair] = trial
    if attempt_id in planned_attempt_ids:
        raise CLIError(
            "hpo_confirmation_binding_mismatch",
            "the confirmation Attempt must be distinct from every search Attempt",
            exit_code=3,
        )
    phase_counts = {
        phase: sum(
            1 for row in trial_plan
            if isinstance(row, dict) and row.get("phase") == phase
        )
        for phase in ("search", "recheck")
    }
    if not isinstance(budget, dict) or phase_counts != {
        "search": budget.get("trial_count"),
        "recheck": budget.get("recheck_count"),
    }:
        raise CLIError(
            "hpo_budget_plan_mismatch",
            "E03 budget must equal the exact enumerated search/recheck Attempt set",
            exit_code=3,
        )
    raw_trial_records = search_values.get("trial_records")
    if not isinstance(raw_trial_records, list):
        raise CLIError(
            "hpo_trial_records_missing",
            "HPO S13 must return one terminal record for every planned trial Attempt",
            exit_code=3,
        )
    records_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for trial_record in raw_trial_records:
        pair = (
            trial_record.get("run_id"), trial_record.get("attempt_id"),
        ) if isinstance(trial_record, dict) else (None, None)
        if (
            not all(isinstance(value, str) and value for value in pair)
            or pair in records_by_pair
        ):
            raise CLIError(
                "hpo_trial_record_invalid",
                "HPO trial records require unique Run/Attempt identities",
                exit_code=3,
            )
        records_by_pair[pair] = trial_record
    if set(records_by_pair) != planned_pairs:
        raise CLIError(
            "hpo_trial_set_mismatch",
            "HPO S13 trial records do not exactly equal frozen E03 trial_plan",
            exit_code=3,
        )
    if (
        sorted(search_values.get("every_trial_run_id", []))
        != sorted(planned_run_ids)
        or sorted(search_values.get("every_attempt_id", []))
        != sorted(attempt for _, attempt in planned_pairs)
    ):
        raise CLIError(
            "hpo_trial_set_mismatch",
            "HPO S13 trial identity summaries do not equal frozen E03 trial_plan",
            exit_code=3,
        )
    _, trial_manifest_rows = _hpo_search_subjects(
        subjects=subjects, state=state, workspace=workspace, bundle=bundle,
    )
    trial_manifest_set_digest = digest({
        "schema": "sdm-hpo-trial-run-manifest-set/v1",
        "items": trial_manifest_rows,
    })
    if (
        search_values.get("trial_run_manifest_set_digest")
        != trial_manifest_set_digest
        or search_request.get("subjects", {}).get(
            "trial_run_manifest_set_digest",
        ) != trial_manifest_set_digest
    ):
        raise CLIError(
            "hpo_trial_run_manifest_set_mismatch",
            "HPO request/result does not bind the exact sorted trial R01 set",
            exit_code=3,
        )

    failed_cancelled: list[str] = []
    validated_trials: list[dict[str, Any]] = []
    for (trial_run_id, trial_attempt_id), trial_record in sorted(
        records_by_pair.items(),
    ):
        planned = planned_by_pair[(trial_run_id, trial_attempt_id)]
        trial_manifest, trial_manifest_record = _validated_stored_artifact(
            state, workspace, bundle, "R01", trial_run_id,
        )
        trial_manifest_content = trial_manifest.get("content", trial_manifest)
        try:
            environment_plan = canonical_runtime_environment_plan(
                planned.get("environment_plan"),
            )
            environment_plan_digest = runtime_environment_plan_digest(
                environment_plan,
            )
        except ContractError as exc:
            raise CLIError(exc.code, str(exc), exit_code=3) from exc
        environment_manifest, trial_environment_digest = \
            _validated_runtime_environment(
                values=trial_record, operation=operation, r01=trial_manifest,
            )
        terminal = trial_record.get("terminal_status")
        trial_validity = trial_record.get("validity")
        trial_system_failure = trial_record.get("system_failure")
        objective_value = trial_record.get("objective_value")
        try:
            objective_decimal = Decimal(str(objective_value))
        except (InvalidOperation, ValueError):
            objective_decimal = Decimal("NaN")
        if (
            trial_manifest_content.get("experiment_id") != experiment_id
            or trial_attempt_id not in trial_manifest_content.get(
                "required_attempt_ids", [],
            )
            or trial_manifest_content.get("run_plan_digest")
            != frozen_context["run_plan_record"]["digest"]
            or trial_manifest_content.get("split_manifest_digest")
            != frozen_context["split_record"]["digest"]
            or environment_plan_digest != planned.get(
                "environment_plan_digest",
            )
            or environment_plan["expected_runtime_image_digest"]
            != trial_manifest_content.get("expected_runtime_image_digest")
            or environment_plan["expected_runtime_profile"]
            != trial_manifest_content.get("expected_runtime_profile")
            or environment_manifest["environment_instance_identity"]
            != environment_plan["environment_instance_identity"]
            or trial_record.get("run_manifest_digest")
            != trial_manifest_record["digest"]
            or trial_record.get("frozen_spec_digest")
            != trial_manifest_content.get("frozen_spec_digest")
            or trial_record.get("data_digest")
            != trial_manifest_content.get("data_digest")
            or trial_record.get("split_digest")
            != planned.get("partition_digest")
            or trial_manifest_content.get("split_digest")
            != planned.get("partition_digest")
            or trial_record.get("phase") != planned.get("phase")
            or trial_record.get("configuration")
            != planned.get("configuration")
            or trial_record.get("configuration_digest")
            != planned.get("configuration_digest")
            or trial_record.get("configuration_digest")
            != digest(trial_record.get("configuration"))
            or trial_record.get("partition_digest")
            != planned.get("partition_digest")
            or trial_record.get("seed") != planned.get("seed")
            or trial_record.get("objective_metric")
            != objective.get("metric")
            or not objective_decimal.is_finite()
            or terminal not in {
                "successful", "failed", "cancelled", "invalid",
            }
            or trial_validity not in {"valid", "invalid"}
            or not isinstance(trial_system_failure, bool)
            or (terminal == "invalid" and trial_validity != "invalid")
            or (
                terminal == "successful"
                and (trial_validity != "valid" or trial_system_failure is not False)
            )
            or not isinstance(trial_record.get("artifacts"), list)
            or not trial_record["artifacts"]
            or trial_record.get("artifact_completeness") is not True
            or any(name in trial_record for name in (
                "evidence_summary", "statistical_analysis",
                "failure_case_index", "scientific_facts",
            ))
        ):
            raise CLIError(
                "hpo_trial_record_binding_mismatch",
                f"HPO trial {trial_run_id}/{trial_attempt_id} does not exactly bind frozen config/objective/partition/seed/terminal facts",
                exit_code=3,
            )
        if terminal in {"failed", "cancelled"}:
            failed_cancelled.append(trial_attempt_id)
        validated_trials.append({
            "run_id": trial_run_id,
            "attempt_id": trial_attempt_id,
            "manifest_record": trial_manifest_record,
            "record": trial_record,
            "terminal": terminal,
            "validity": trial_validity,
            "system_failure": trial_system_failure,
            "environment_manifest": environment_manifest,
            "environment_digest": trial_environment_digest,
            "environment_plan": environment_plan,
            "environment_plan_digest": environment_plan_digest,
            "environment_instance_identity": environment_manifest[
                "environment_instance_identity"
            ],
            "phase": planned["phase"],
            "configuration": planned["configuration"],
            "configuration_digest": planned["configuration_digest"],
            "partition_digest": planned["partition_digest"],
            "seed": planned["seed"],
            "objective_metric": objective["metric"],
            "objective_value": objective_decimal,
        })

    scores_by_configuration: dict[str, list[Decimal]] = {}
    configurations: dict[str, dict[str, Any]] = {}
    for trial in validated_trials:
        if (
            trial["terminal"] == "successful"
            and trial["validity"] == "valid"
            and trial["system_failure"] is False
        ):
            configuration_digest = trial["configuration_digest"]
            configurations[configuration_digest] = trial["configuration"]
            scores_by_configuration.setdefault(
                configuration_digest, [],
            ).append(trial["objective_value"])
    if not scores_by_configuration:
        raise CLIError(
            "hpo_winner_unavailable",
            "coordinator cannot select without at least one successful valid frozen trial",
            exit_code=3,
        )
    aggregate_scores = {
        configuration_digest: sum(values, Decimal(0)) / len(values)
        for configuration_digest, values in scores_by_configuration.items()
    }
    direction = selection_rule["direction"]
    winning_score = (
        max(aggregate_scores.values())
        if direction == "maximize" else min(aggregate_scores.values())
    )
    winning_configuration_digest = min(
        configuration_digest
        for configuration_digest, score in aggregate_scores.items()
        if score == winning_score
    )
    winning_configuration = configurations[winning_configuration_digest]
    selected_configuration = search_values.get("selected_configuration")
    selected_configuration_digest = search_values.get(
        "selected_configuration_digest",
    )
    selected_objective_value = search_values.get("selected_objective_value")
    try:
        selected_objective_decimal = Decimal(str(selected_objective_value))
    except (InvalidOperation, ValueError):
        selected_objective_decimal = Decimal("NaN")
    selection_rule_digest = digest(selection_rule)
    expected_receipt = {
        "selected_configuration_digest": winning_configuration_digest,
        "selected_objective_value": selected_objective_value,
        "selection_rule_digest": selection_rule_digest,
    }
    if (
        search_values.get("experiment_id") != experiment_id
        or search_values.get("search_spec_digest") != search_record["digest"]
        or sorted(search_values.get("failed_cancelled_attempts", []))
        != sorted(failed_cancelled)
        or search_values.get("terminal_status") != "successful"
    ):
        raise CLIError(
            "hpo_receipt_binding_mismatch",
            "HPO S13 lacks the exact frozen search and complete terminal Attempt accounting",
            exit_code=3,
        )
    if (
        selected_configuration != winning_configuration
        or selected_configuration_digest != winning_configuration_digest
        or not selected_objective_decimal.is_finite()
        or selected_objective_decimal != winning_score
        or search_values.get("selection_rule_digest")
        != selection_rule_digest
        or search_values.get("selection_receipt") != expected_receipt
    ):
        raise CLIError(
            "hpo_selection_mismatch",
            "platform-selected configuration differs from the coordinator-recomputed frozen objective/direction/tie-break winner",
            exit_code=3,
        )
    trial_seeds = {trial["seed"] for trial in validated_trials}
    trial_partitions = {trial["partition_digest"] for trial in validated_trials}
    trial_environment_instances = {
        trial["environment_instance_identity"] for trial in validated_trials
    }
    confirmation_manifest, _ = _validated_stored_artifact(
        state, workspace, bundle, "R01", run_id,
    )
    confirmation_manifest_content = confirmation_manifest.get(
        "content", confirmation_manifest,
    )
    confirmation_partition_digest = confirmation["partition_digest"]
    formal_test_partition_digest = objective["formal_test_partition_digest"]
    if (
        confirmation_seed in trial_seeds
        or confirmation_environment_plan["environment_instance_identity"]
        in trial_environment_instances
        or confirmation_environment_plan["expected_runtime_image_digest"]
        != confirmation_manifest_content.get("expected_runtime_image_digest")
        or confirmation_environment_plan["expected_runtime_profile"]
        != confirmation_manifest_content.get("expected_runtime_profile")
        or attempt_id not in confirmation_manifest_content.get(
            "required_attempt_ids", [],
        )
        or confirmation_partition_digest in trial_partitions
        or formal_test_partition_digest in trial_partitions
        or formal_test_partition_digest == confirmation_partition_digest
    ):
        raise CLIError(
            "hpo_confirmation_isolation_invalid",
            "confirmation seed/partition/environment and formal-test partition must be disjoint from every search/recheck Attempt",
            exit_code=3,
        )
    resolved_config, resolved_record = _validated_stored_artifact(
        state, workspace, bundle, "R02", run_id,
    )
    resolved_content = resolved_config.get("content", resolved_config)
    if (
        resolved_content.get("config_digest")
        != digest(resolved_content.get("resolved_values"))
        or resolved_content.get("selected_configuration_digest") is not None
    ):
        raise CLIError(
            "hpo_resolved_configuration_invalid",
            "R02 must retain the full resolved configuration and cannot impersonate the HPO-selected parameter subset",
            exit_code=3,
        )
    return {
        "request": search_request,
        "result": search_result,
        "producer_principal_id": packet[2]["producer_principal_id"],
        "search_spec_digest": search_record["digest"],
        "selected_configuration": winning_configuration,
        "selected_configuration_digest": winning_configuration_digest,
        "selected_objective_value": winning_score,
        "selection_rule_digest": selection_rule_digest,
        "selection_result_digest": digest(search_result),
        "confirmation_run_id": run_id,
        "confirmation_attempt_id": attempt_id,
        "confirmation_seed": confirmation_seed,
        "confirmation_environment_plan": confirmation_environment_plan,
        "confirmation_environment_plan_digest":
            confirmation_environment_plan_digest,
        "confirmation_partition_digest": confirmation_partition_digest,
        "formal_test_partition_digest": formal_test_partition_digest,
        "split_manifest_digest": frozen_context["split_record"]["digest"],
        "resolved_record": resolved_record,
        "trials": validated_trials,
    }


def _persisted_hpo_selection(
    *, replayed: Any, state: dict[str, Any], workspace: str,
    bundle: AssetBundle, subjects: dict[str, str], selection: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the unique exact committed selection, or fail closed."""

    event_subjects = {
        "workspace_id": replayed.genesis["workspace_id"], **subjects,
    }
    selection_matches = [
        event for event in replayed.events
        if event.get("event_type") == "hpo.selection-recorded"
        and event.get("subjects") == event_subjects
    ]
    confirmation_matches = [
        event for event in replayed.events
        if event.get("event_type") == "confirmation.materialized"
        and event.get("subjects") == event_subjects
    ]
    if not selection_matches and not confirmation_matches:
        return None
    if len(selection_matches) != 1 or len(confirmation_matches) != 1:
        raise CLIError(
            "hpo_selection_phase_incomplete",
            "exactly one persisted HPO selection and confirmation materialization must bind the Run/Attempt",
            exit_code=3,
        )
    selection_event = selection_matches[0]
    confirmation_event = confirmation_matches[0]
    payload = selection_event.get("payload", {})
    expected = {
        "transition": "hpo.select",
        "request_id": selection["request"]["request_id"],
        "search_spec_digest": selection["search_spec_digest"],
        "selected_configuration_digest":
            selection["selected_configuration_digest"],
        "selected_objective_value": selection["selected_objective_value"],
        "selection_rule_digest": selection["selection_rule_digest"],
        "selection_result_digest": selection["selection_result_digest"],
        "confirmation_run_id": selection["confirmation_run_id"],
        "confirmation_attempt_id": selection["confirmation_attempt_id"],
        "confirmation_seed": selection["confirmation_seed"],
        "confirmation_environment_plan_digest":
            selection["confirmation_environment_plan_digest"],
        "confirmation_partition_digest":
            selection["confirmation_partition_digest"],
        "formal_test_partition_digest":
            selection["formal_test_partition_digest"],
        "split_manifest_digest": selection["split_manifest_digest"],
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise CLIError(
            "hpo_selection_binding_mismatch",
            "persisted HPO selection does not equal the authenticated search result and frozen confirmation contract",
            exit_code=3,
        )
    confirmation_payload = confirmation_event.get("payload", {})
    expected_confirmation = {
        "transition": "confirmation.materialize",
        "selection_operation_id": selection_event["operation_id"],
        "R02_digest": selection["resolved_record"]["digest"],
        "selected_configuration_digest":
            selection["selected_configuration_digest"],
        "confirmation_seed": selection["confirmation_seed"],
        "confirmation_environment_plan_digest":
            selection["confirmation_environment_plan_digest"],
        "confirmation_partition_digest":
            selection["confirmation_partition_digest"],
        "formal_test_partition_digest":
            selection["formal_test_partition_digest"],
        "independent": True,
    }
    if any(
        confirmation_payload.get(key) != value
        for key, value in expected_confirmation.items()
    ):
        raise CLIError(
            "hpo_confirmation_binding_mismatch",
            "persisted confirmation materialization does not equal the selection and frozen R02 contract",
            exit_code=3,
        )
    hpo_receipt = _receipt_reference(
        selection["request"], selection["result"],
    )
    for trial in selection["trials"]:
        attempt_id = trial["attempt_id"]
        attempt = state.get("attempts", {}).get(attempt_id)
        trial_subjects = {
            "workspace_id": replayed.genesis["workspace_id"],
            "experiment_id": subjects["experiment_id"],
            "run_id": trial["run_id"],
            "attempt_id": attempt_id,
        }
        recorded = [
            event for event in replayed.events
            if event.get("event_type") == "hpo.trial-recorded"
            and event.get("subjects") == trial_subjects
        ]
        trial_record = trial["record"]
        expected_contents = {
            "R03": {
                "run_id": trial["run_id"],
                "attempt_id": attempt_id,
                "attempt_kind": "hpo_trial",
                "environment_manifest": trial["environment_manifest"],
                "environment_digest": trial["environment_digest"],
                "receipt": hpo_receipt,
            },
            "R07": {
                "run_id": trial["run_id"],
                "attempt_id": attempt_id,
                "attempt_kind": "hpo_trial",
                "status": trial["terminal"],
                "started_at": trial_record.get("started_at"),
                "completed_at": trial_record.get("completed_at"),
                "resource_use": trial_record.get("resource_use"),
                "receipt": hpo_receipt,
                "validity": trial["validity"],
                "system_failure": trial["system_failure"],
                "phase": trial["phase"],
                "environment_plan_digest":
                    trial["environment_plan_digest"],
                "environment_instance_identity":
                    trial["environment_instance_identity"],
                "environment_digest": trial["environment_digest"],
                "configuration_digest": trial["configuration_digest"],
                "objective_metric": trial["objective_metric"],
                "objective_value": trial["objective_value"],
                "partition_digest": trial["partition_digest"],
                "seed": trial["seed"],
                "author_principal_ids": [
                    selection["producer_principal_id"]
                ],
            },
            "R08": {
                "run_id": trial["run_id"],
                "attempt_id": attempt_id,
                "attempt_kind": "hpo_trial",
                "artifacts": trial_record["artifacts"],
                "completeness": trial_record.get("artifact_completeness"),
                "receipt": hpo_receipt,
                "author_principal_ids": [
                    selection["producer_principal_id"]
                ],
            },
        }
        retained_artifacts: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for artifact_id in ("R03", "R07", "R08"):
            retained_artifacts[artifact_id] = _validated_stored_artifact(
                state, workspace, bundle, artifact_id, attempt_id,
            )
        if (
            not isinstance(attempt, dict)
            or attempt.get("run_id") != trial["run_id"]
            or attempt.get("status") != trial["terminal"]
            or len(recorded) != 1
            or recorded[0].get("payload", {}).get("search_spec_digest")
            != selection["search_spec_digest"]
            or recorded[0].get("payload", {}).get(
                "configuration_digest"
            ) != trial["configuration_digest"]
            or recorded[0].get("payload", {}).get("objective_metric")
            != trial["objective_metric"]
            or recorded[0].get("payload", {}).get("objective_value")
            != trial["objective_value"]
            or recorded[0].get("payload", {}).get("partition_digest")
            != trial["partition_digest"]
            or recorded[0].get("payload", {}).get("seed") != trial["seed"]
            or recorded[0].get("payload", {}).get("phase")
            != trial["phase"]
            or recorded[0].get("payload", {}).get(
                "environment_plan_digest"
            ) != trial["environment_plan_digest"]
            or recorded[0].get("payload", {}).get(
                "environment_instance_identity"
            ) != trial["environment_instance_identity"]
            or recorded[0].get("payload", {}).get("environment_digest")
            != trial["environment_digest"]
            or any(
                value.get("subjects") != trial_subjects
                or value.get("created_by")
                != selection["result"]["producer_identity"]
                or value.get("content", value) != expected_contents[artifact_id]
                for artifact_id, (value, _) in retained_artifacts.items()
            )
        ):
            raise CLIError(
                "hpo_selection_phase_incomplete",
                "persisted selection is missing an exact retained trial Attempt or artifact",
                exit_code=3,
            )
    return {
        **selection,
        "event": selection_event,
        "selection_event": selection_event,
        "confirmation_event": confirmation_event,
    }


def _hpo_confirmation_subjects(
    subjects: dict[str, str], selection: dict[str, Any], *,
    state: dict[str, Any], workspace: str, bundle: AssetBundle,
) -> dict[str, str]:
    """Derive downstream S12 anchors only from already-committed events."""

    selection_event = selection.get("selection_event")
    confirmation_event = selection.get("confirmation_event")
    if (
        not isinstance(selection_event, dict)
        or not isinstance(selection_event.get("event_hash"), str)
        or not isinstance(confirmation_event, dict)
        or not isinstance(confirmation_event.get("event_hash"), str)
    ):
        raise CLIError(
            "hpo_selection_not_persisted",
            "HPO confirmation S12 subjects require committed selection and confirmation event hashes",
            exit_code=3,
        )
    derived = {
        **subjects,
        "selected_configuration_digest":
            selection["selected_configuration_digest"],
        "search_spec_digest": selection["search_spec_digest"],
        "selection_result_digest": selection["selection_result_digest"],
        "confirmation_seed": selection["confirmation_seed"],
        "confirmation_environment_plan_digest":
            selection["confirmation_environment_plan_digest"],
        "confirmation_partition_digest":
            selection["confirmation_partition_digest"],
        "formal_test_partition_digest":
            selection["formal_test_partition_digest"],
        "split_manifest_digest": selection["split_manifest_digest"],
        "selection_event_hash": selection_event["event_hash"],
        "confirmation_event_hash": confirmation_event["event_hash"],
    }
    return _runtime_bound_subjects(
        subjects=derived, state=state, workspace=workspace, bundle=bundle,
    )


def _platform_execution_completion(
    *,
    step: int,
    workspace: str,
    replayed: Any,
    state: dict[str, Any],
    bundle: AssetBundle,
    subjects: dict[str, str],
    packets: dict[
        str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
    ],
    hpo_enabled: bool,
    command_operation_id: str,
    phase: str = "completion",
    hpo_selection: dict[str, Any] | None = None,
) -> tuple[
    list[dict[str, Any]], list[tuple[str, Any, bool]], dict[str, Any],
    list[dict[str, Any]],
]:
    """Derive Step 15--17 facts only from authenticated exact S13 bytes."""

    workspace_id = replayed.genesis["workspace_id"]
    manifest, manifest_record = _validated_run_scope(
        state=state, workspace=workspace, bundle=bundle, subjects=subjects,
    )
    manifest_content = manifest.get("content", manifest)
    run_id = subjects["run_id"]
    attempt_id = subjects["attempt_id"]
    experiment_id = subjects["experiment_id"]
    envelope_subjects = {"workspace_id": workspace_id, **subjects}
    descriptors: list[dict[str, Any]] = []
    writes: list[tuple[str, Any, bool]] = []
    virtual = deepcopy(state)
    verified_platform_principals: dict[str, str] = {}
    for _, result, packet_record in packets.values():
        binding_identity = result.get("producer_identity")
        principal_id = packet_record.get("producer_principal_id")
        if (
            not isinstance(binding_identity, str)
            or not isinstance(principal_id, str)
            or not principal_id
            or binding_identity in verified_platform_principals
            and verified_platform_principals[binding_identity] != principal_id
        ):
            raise CLIError(
                "platform_producer_principal_invalid",
                "authenticated S13 producer principal is missing or ambiguous",
                exit_code=3,
            )
        verified_platform_principals[binding_identity] = principal_id
    logical = digest({
        "command_operation_id": command_operation_id,
        "step": step,
        "subjects": subjects,
        "results": {
            operation: digest(result)
            for operation, (_, result, _) in sorted(packets.items())
        },
    })

    def publish(
        artifact_id: str, instance_key: str, content: dict[str, Any],
        parents: list[str], producer: str,
        *, subjects_override: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if artifact_id in {"R07", "R08"}:
            verified_producer = verified_platform_principals.get(producer)
            if not isinstance(verified_producer, str):
                raise CLIError(
                    "evidence_producer_principal_missing",
                    f"{artifact_id} cannot derive authorship from an unverified Skill identity",
                    exit_code=3,
                )
            supplied_authors = content.get("author_principal_ids")
            expected_authors = [verified_producer]
            if supplied_authors is not None and supplied_authors != expected_authors:
                raise CLIError(
                    "evidence_authorship_substitution",
                    f"{artifact_id} authors must equal the verified platform producer principal",
                    exit_code=3,
                )
            content = {**content, "author_principal_ids": expected_authors}
        value = _envelope(
            artifact_id, instance_key, instance_key, content,
            subjects_override or envelope_subjects,
        )
        value["created_by"] = producer
        findings = bundle.validate_artifact(artifact_id, value)
        candidate_content = value.get("content", value)
        if (
            step in {5, 7, 19}
            and isinstance(candidate_content, dict)
            and candidate_content.get("authority") == []
        ):
            findings = [
                item for item in findings
                if not (
                    item.get("code") == "semantic_required"
                    and item.get("json_pointer") == "/content/authority"
                )
            ]
        if findings:
            raise CLIError(
                "platform_derived_artifact_invalid", findings[0]["message"],
                exit_code=3, findings=findings,
            )
        if state.get("artifacts", {}).get(artifact_id, {}).get(instance_key):
            raise CLIError(
                "platform_derived_artifact_exists",
                f"immutable {artifact_id}/{instance_key} already exists",
                exit_code=4,
            )
        relative = f"artifacts/{artifact_id}/{instance_key}.json"
        descriptor = {
            "artifact_id": artifact_id,
            "instance_key": instance_key,
            "path": relative,
            "digest": digest(value),
            "parents": sorted(set(parents)),
            "status": "published",
        }
        descriptors.append(descriptor)
        writes.append((relative, value, True))
        virtual.setdefault("artifacts", {}).setdefault(artifact_id, {})[
            instance_key
        ] = {
            "path": relative, "digest": descriptor["digest"],
            "status": "published",
        }
        return descriptor

    event_subjects = {"workspace_id": workspace_id, **subjects}
    events: list[dict[str, Any]] = []
    if step == 15 and phase == "hpo-selection":
        if not hpo_enabled or set(packets) != {"ml.hpo-search"}:
            raise CLIError(
                "hpo_selection_phase_invalid",
                "the HPO selection phase consumes exactly one authenticated ml.hpo-search result",
                exit_code=3,
            )
        selection = _validated_hpo_search_phase(
            state=state, workspace=workspace, bundle=bundle,
            subjects=subjects, packet=packets["ml.hpo-search"],
        )
        hpo_receipt = _receipt_reference(
            selection["request"], selection["result"],
        )
        for trial in selection["trials"]:
            trial_run_id = trial["run_id"]
            trial_attempt_id = trial["attempt_id"]
            trial_record = trial["record"]
            trial_subjects = {
                "workspace_id": workspace_id,
                "experiment_id": experiment_id,
                "run_id": trial_run_id,
                "attempt_id": trial_attempt_id,
            }
            trial_descriptors = [
                publish(
                    "R03", trial_attempt_id,
                    {
                        "run_id": trial_run_id,
                        "attempt_id": trial_attempt_id,
                        "attempt_kind": "hpo_trial",
                        "environment_manifest": trial[
                            "environment_manifest"
                        ],
                        "environment_digest": trial["environment_digest"],
                        "receipt": hpo_receipt,
                    },
                    [f"R01:{trial_run_id}"],
                    selection["result"]["producer_identity"],
                    subjects_override=trial_subjects,
                ),
                publish(
                    "R07", trial_attempt_id,
                    {
                        "run_id": trial_run_id,
                        "attempt_id": trial_attempt_id,
                        "attempt_kind": "hpo_trial",
                        "status": trial["terminal"],
                        "started_at": trial_record.get("started_at"),
                        "completed_at": trial_record.get("completed_at"),
                        "resource_use": trial_record.get("resource_use"),
                        "receipt": hpo_receipt,
                        "validity": trial["validity"],
                        "system_failure": trial["system_failure"],
                        "phase": trial["phase"],
                        "environment_plan_digest": trial[
                            "environment_plan_digest"
                        ],
                        "environment_instance_identity": trial[
                            "environment_instance_identity"
                        ],
                        "environment_digest": trial["environment_digest"],
                        "configuration_digest":
                            trial["configuration_digest"],
                        "objective_metric": trial["objective_metric"],
                        "objective_value": trial["objective_value"],
                        "partition_digest": trial["partition_digest"],
                        "seed": trial["seed"],
                    },
                    [f"R01:{trial_run_id}", f"R03:{trial_attempt_id}"],
                    selection["result"]["producer_identity"],
                    subjects_override=trial_subjects,
                ),
                publish(
                    "R08", trial_attempt_id,
                    {
                        "run_id": trial_run_id,
                        "attempt_id": trial_attempt_id,
                        "attempt_kind": "hpo_trial",
                        "artifacts": trial_record["artifacts"],
                        "completeness": trial_record.get(
                            "artifact_completeness",
                        ),
                        "receipt": hpo_receipt,
                    },
                    [f"R01:{trial_run_id}", f"R07:{trial_attempt_id}"],
                    selection["result"]["producer_identity"],
                    subjects_override=trial_subjects,
                ),
            ]
            trial_source = {
                "request_ids": [selection["request"]["request_id"]],
                "result_digests": [selection["selection_result_digest"]],
            }
            trial_logical = digest({
                "hpo_result": selection["selection_result_digest"],
                "run_id": trial_run_id,
                "attempt_id": trial_attempt_id,
            })
            events.extend([
                {
                    "operation_id": _phase_operation_id(
                        trial_logical, "dispatch",
                    ),
                    "event_type": "run.dispatched",
                    "subjects": trial_subjects,
                    "payload": {"transition": "run.dispatch", **trial_source},
                },
                {
                    "operation_id": _phase_operation_id(
                        trial_logical, "start",
                    ),
                    "event_type": "attempt.started",
                    "subjects": trial_subjects,
                    "payload": {
                        "transition": "attempt.start", **trial_source,
                        "environment_digest": trial["environment_digest"],
                    },
                },
                {
                    "operation_id": _phase_operation_id(
                        trial_logical, "terminal",
                    ),
                    "event_type": "attempt.terminal",
                    "subjects": trial_subjects,
                    "payload": {
                        "transition": "attempt.terminal", **trial_source,
                        "terminal_status": trial["terminal"],
                        "status": trial["terminal"],
                        "accounted": True,
                        "validity": trial["validity"],
                        "system_failure": trial["system_failure"],
                        "published_artifacts": trial_descriptors,
                    },
                },
                {
                    "operation_id": _phase_operation_id(
                        trial_logical, "retained",
                    ),
                    "event_type": "hpo.trial-recorded",
                    "subjects": trial_subjects,
                    "payload": {
                        "search_spec_digest":
                            selection["search_spec_digest"],
                        "configuration_digest":
                            trial["configuration_digest"],
                        "objective_metric": trial["objective_metric"],
                        "objective_value": trial["objective_value"],
                        "partition_digest": trial["partition_digest"],
                        "seed": trial["seed"],
                        "phase": trial["phase"],
                        "environment_plan_digest":
                            trial["environment_plan_digest"],
                        "environment_instance_identity":
                            trial["environment_instance_identity"],
                        "environment_digest": trial["environment_digest"],
                        "terminal_event_operation_id": _phase_operation_id(
                            trial_logical, "terminal",
                        ),
                    },
                },
            ])
        selection_logical = digest({
            "step": 15,
            "subjects": subjects,
            "hpo_request": selection["request"]["request_id"],
            "hpo_result": selection["selection_result_digest"],
        })
        selection_operation_id = _phase_operation_id(
            selection_logical, "hpo-selection",
        )
        events.extend([
            {
                "operation_id": selection_operation_id,
                "event_type": "hpo.selection-recorded",
                "subjects": event_subjects,
                "payload": {
                    "transition": "hpo.select",
                    "request_id": selection["request"]["request_id"],
                    "search_spec_digest": selection["search_spec_digest"],
                    "selected_configuration_digest":
                        selection["selected_configuration_digest"],
                    "selected_objective_value":
                        selection["selected_objective_value"],
                    "selection_rule_digest":
                        selection["selection_rule_digest"],
                    "selection_result_digest":
                        selection["selection_result_digest"],
                    "confirmation_run_id": selection["confirmation_run_id"],
                    "confirmation_attempt_id":
                        selection["confirmation_attempt_id"],
                    "confirmation_seed": selection["confirmation_seed"],
                    "confirmation_environment_plan_digest":
                        selection["confirmation_environment_plan_digest"],
                    "confirmation_partition_digest":
                        selection["confirmation_partition_digest"],
                    "formal_test_partition_digest":
                        selection["formal_test_partition_digest"],
                    "split_manifest_digest":
                        selection["split_manifest_digest"],
                },
            },
            {
                "operation_id": _phase_operation_id(
                    selection_logical, "confirmation-materialized",
                ),
                "event_type": "confirmation.materialized",
                "subjects": event_subjects,
                "payload": {
                    "transition": "confirmation.materialize",
                    "selection_operation_id": selection_operation_id,
                    "R02_digest": selection["resolved_record"]["digest"],
                    "selected_configuration_digest":
                        selection["selected_configuration_digest"],
                    "confirmation_seed": selection["confirmation_seed"],
                    "confirmation_environment_plan_digest":
                        selection["confirmation_environment_plan_digest"],
                    "confirmation_partition_digest":
                        selection["confirmation_partition_digest"],
                    "formal_test_partition_digest":
                        selection["formal_test_partition_digest"],
                    "independent": True,
                },
            },
        ])
        virtual.setdefault("checks", {}).update({
            "hpo.every_trial_retained": True,
            "hpo.selection_replayable": True,
            "hpo.formal_test_isolated": True,
            "hpo.not_applicable": False,
        })
        virtual["hpo_enabled"] = True
        return descriptors, writes, virtual, events
    if phase != "completion":
        raise CLIError(
            "platform_execution_phase_invalid",
            f"unsupported STEP{step:02d} execution phase: {phase}",
            exit_code=3,
        )
    if step == 15:
        request, result, _ = packets["ml.training"]
        operation = "ml.training"
        required = set(bundle.operation_contract(operation)[1][
            "required_evidence"
        ])
        values = _evidence_values(operation, result, required)
        resolved_config, resolved_record = _validated_stored_artifact(
            state, workspace, bundle, "R02", run_id,
        )
        resolved_content = resolved_config.get("content", resolved_config)
        if (
            values["run_id"] != run_id
            or values["attempt_id"] != attempt_id
            or values["run_manifest_digest"] != manifest_record["digest"]
            or values["frozen_spec_digest"]
            != manifest_content.get("frozen_spec_digest")
            or values["data_digest"] != manifest_content.get("data_digest")
            or values["split_digest"] != manifest_content.get("split_digest")
            or values["resolved_config_digest"]
            != resolved_content.get("config_digest")
            or not isinstance(values["confirmation_seed"], str)
            or not values["confirmation_seed"]
        ):
            raise CLIError(
                "training_receipt_binding_mismatch",
                "ml.training evidence does not repeat exact frozen R01 bindings",
                exit_code=3,
            )
        environment_manifest, environment_digest = \
            _validated_runtime_environment(
                values=values, operation=operation, r01=manifest,
            )
        _required_digest(values, "checkpoint_digest", operation)
        _required_digest(values, "model_artifact_digest", operation)
        terminal_status = values["attempt_terminal_status"]
        validity = values["attempt_validity"]
        system_failure = values["system_failure"]
        if (
            terminal_status not in {"successful", "failed", "cancelled", "invalid"}
            or validity not in {"valid", "invalid"}
            or not isinstance(system_failure, bool)
            or (terminal_status == "invalid" and validity != "invalid")
            or (
                terminal_status == "successful"
                and (validity != "valid" or system_failure is not False)
            )
            or values["terminal_status"] != terminal_status
            or values["selected_configuration_replayed"] is not True
            or values["independent_seed_environment"] is not True
        ):
            raise CLIError(
                "attempt_terminal_fact_invalid",
                "training receipt must carry one canonical terminal Attempt fact",
                exit_code=3,
            )
        controller_request, controller_result, _ = packets[
            "ml.execution-orchestration"
        ]
        del controller_request
        controller_required = set(bundle.operation_contract(
            "ml.execution-orchestration",
        )[1]["required_evidence"])
        controller_values = _evidence_values(
            "ml.execution-orchestration", controller_result,
            controller_required,
        )
        if (
            controller_values.get("run_id") != run_id
            or controller_values.get("attempt_id") != attempt_id
            or controller_values.get("run_manifest_digest")
            != manifest_record["digest"]
            or controller_values.get("terminal_status") != "successful"
        ):
            raise CLIError(
                "execution_orchestration_binding_mismatch",
                "Jenkins orchestration S13 does not bind the exact R01/Attempt",
                exit_code=3,
            )
        hpo_checks = {
            "hpo.every_trial_retained": False,
            "hpo.selection_replayable": False,
            "hpo.formal_test_isolated": False,
            "hpo.not_applicable": not hpo_enabled,
        }
        selected_configuration_digest = values["selected_configuration_digest"]
        _required_digest(
            values, "selected_configuration_digest", operation,
        )
        if hpo_enabled:
            if hpo_selection is None:
                raise CLIError(
                    "hpo_selection_not_persisted",
                    "confirmation execution requires a prior exact persisted HPO selection",
                    exit_code=3,
                )
            if (
                selected_configuration_digest
                != hpo_selection["selected_configuration_digest"]
                or values["confirmation_seed"]
                != hpo_selection["confirmation_seed"]
                or values["confirmation_environment_plan_digest"]
                != hpo_selection["confirmation_environment_plan_digest"]
                or environment_manifest["environment_instance_identity"]
                != hpo_selection["confirmation_environment_plan"][
                    "environment_instance_identity"
                ]
                or values["split_digest"]
                != hpo_selection["confirmation_partition_digest"]
                or run_id != hpo_selection["confirmation_run_id"]
                or attempt_id != hpo_selection["confirmation_attempt_id"]
                or values["confirmation_seed"] in {
                    trial["seed"] for trial in hpo_selection["trials"]
                }
                or environment_manifest["environment_instance_identity"] in {
                    trial["environment_instance_identity"]
                    for trial in hpo_selection["trials"]
                }
                or values["split_digest"] in {
                    trial["partition_digest"]
                    for trial in hpo_selection["trials"]
                }
                or values["split_digest"]
                == hpo_selection["formal_test_partition_digest"]
            ):
                raise CLIError(
                    "hpo_confirmation_binding_mismatch",
                    "training result does not repeat the committed HPO selection and frozen confirmation contract",
                    exit_code=3,
                )
            hpo_checks.update({
                "hpo.every_trial_retained": True,
                "hpo.selection_replayable": True,
                "hpo.formal_test_isolated": True,
            })
        elif (
            hpo_selection is not None
            or selected_configuration_digest
            != resolved_content.get("config_digest")
            or values["confirmation_environment_plan_digest"]
            != "not-applicable"
        ):
            raise CLIError(
                "training_configuration_binding_mismatch",
                "non-HPO training must bind the exact frozen R02 configuration",
                exit_code=3,
            )
        if not isinstance(values["artifacts"], list) or not values["artifacts"]:
            raise CLIError(
                "training_artifact_index_invalid",
                "terminal training evidence must enumerate checkpoints/logs and other immutable outputs",
                exit_code=3,
            )
        artifact_digests = {
            item.get("type"): item.get("digest")
            for item in values["artifacts"] if isinstance(item, dict)
        }
        if (
            artifact_digests.get("checkpoint") != values["checkpoint_digest"]
            or artifact_digests.get("model") != values["model_artifact_digest"]
        ):
            raise CLIError(
                "training_artifact_binding_mismatch",
                "training R08 must enumerate the exact checkpoint and model digests returned as run/model facts",
                exit_code=3,
            )
        receipt = _receipt_reference(request, result)
        current_descriptor_start = len(descriptors)
        runtime_content = {
            "run_id": run_id,
            "attempt_id": attempt_id,
            "attempt_kind": "training",
            "environment_manifest": environment_manifest,
            "environment_digest": environment_digest,
            "receipt": receipt,
        }
        publish(
            "R03", attempt_id, runtime_content,
            [f"R01:{run_id}"], result["producer_identity"],
        )
        actual_content = {
            "run_id": run_id, "attempt_id": attempt_id,
            "attempt_kind": "training",
            "status": terminal_status,
            "started_at": _required_string(values, "started_at", operation),
            "completed_at": _required_string(values, "completed_at", operation),
            "resource_use": values["resource_use"],
            "receipt": receipt,
            "validity": validity,
            "system_failure": system_failure,
            "checkpoint_uri": _required_string(
                values, "checkpoint_uri", operation,
            ),
            "checkpoint_digest": values["checkpoint_digest"],
            "model_artifact_digest": values["model_artifact_digest"],
        }
        index_content = {
            "run_id": run_id, "attempt_id": attempt_id,
            "attempt_kind": "training",
            "artifacts": values["artifacts"],
            "completeness": values["artifact_completeness"],
            "checkpoint_digest": values["checkpoint_digest"],
            "model_artifact_digest": values["model_artifact_digest"],
            "receipt": receipt,
        }
        publish(
            "R07", attempt_id, actual_content,
            [f"R01:{run_id}", f"R03:{attempt_id}"],
            result["producer_identity"],
        )
        publish(
            "R08", attempt_id, index_content,
            [f"R01:{run_id}", f"R07:{attempt_id}"],
            result["producer_identity"],
        )
        source = {
            "request_ids": sorted(packet[0]["request_id"] for packet in packets.values()),
            "result_digests": sorted(digest(packet[1]) for packet in packets.values()),
            "logical_receipt_digest": logical,
        }
        training_completed = (
            terminal_status == "successful"
            and validity == "valid"
            and system_failure is False
        )
        terminal_payload = {
            "transition": "attempt.terminal", **source,
            "terminal_status": terminal_status,
            "status": terminal_status,
            "accounted": True, "validity": validity,
            "system_failure": system_failure,
            "hpo_enabled": hpo_enabled,
            "published_artifacts": descriptors[
                current_descriptor_start:
            ],
            "outputs": ["R03", "R07", "R08"],
        }
        if training_completed:
            terminal_payload.update({
                "step": 15, "lifecycle_step_completion": True,
            })
        events.extend([
            *([{
                "operation_id": _phase_operation_id(logical, "confirmation-materialized"),
                "event_type": "confirmation.materialized",
                "subjects": event_subjects,
                "payload": {
                    "transition": "confirmation.materialize",
                    "R02_digest": resolved_record["digest"],
                    "resolved_config_digest": values["resolved_config_digest"],
                    "selected_configuration_digest": selected_configuration_digest,
                    "confirmation_seed": values["confirmation_seed"],
                    "confirmation_environment_plan_digest": values[
                        "confirmation_environment_plan_digest"
                    ],
                    "independent": values["independent_seed_environment"],
                },
            }] if not hpo_enabled else []),
            {
                "operation_id": _phase_operation_id(logical, "run-dispatched"),
                "event_type": "run.dispatched",
                "subjects": event_subjects,
                "payload": {"transition": "run.dispatch", **source},
            },
            {
                "operation_id": _phase_operation_id(logical, "attempt-started"),
                "event_type": "attempt.started",
                "subjects": event_subjects,
                "payload": {
                    "transition": "attempt.start", **source,
                    "environment_digest": environment_digest,
                },
            },
            {
                "operation_id": _phase_operation_id(logical, "attempt-terminal"),
                "event_type": "attempt.terminal",
                "subjects": event_subjects,
                "payload": terminal_payload,
            },
        ])
        virtual.setdefault("checks", {}).update({
            **hpo_checks,
            "confirmation.selected_config_replayed": True,
            "confirmation.independent_seed_environment": True,
            "attempt.environment_matches_R01": True,
            "attempt.terminal": True,
        })
        virtual["hpo_enabled"] = hpo_enabled
    elif step == 16:
        request, result, _ = packets["ml.locked-evaluation"]
        operation = "ml.locked-evaluation"
        required = set(bundle.operation_contract(operation)[1][
            "required_evidence"
        ])
        values = _evidence_values(operation, result, required)
        if (
            values["run_id"] != run_id
            or values["attempt_id"] != attempt_id
            or values["run_manifest_digest"] != manifest_record["digest"]
            or values["frozen_spec_digest"] != manifest_content.get("frozen_spec_digest")
            or values["evaluator_digest"] != manifest_content.get("evaluator_digest")
            or values["data_digest"] != manifest_content.get("data_digest")
            or values["split_digest"] != manifest_content.get("split_digest")
            or any(
                values.get(name) != subjects.get(name)
                for name in (
                    "selected_checkpoint_attempt_id", "checkpoint_digest",
                    "model_artifact_digest", "training_r07_digest",
                    "training_r08_digest",
                )
            )
        ):
            raise CLIError(
                "evaluation_receipt_binding_mismatch",
                "ml.locked-evaluation evidence does not repeat exact frozen R01 bindings",
                exit_code=3,
            )
        prior_terminal_events = [
            event for event in replayed.events
            if event.get("event_type") == "attempt.terminal"
            and all(event.get("subjects", {}).get(key) == value for key, value in subjects.items())
        ]
        if prior_terminal_events:
            raise CLIError(
                "evaluation_attempt_identity_reused",
                "STEP16 requires its own planned evaluation Attempt identity; terminal snapshots are immutable",
                exit_code=4,
            )
        controller_request, controller_result, _ = packets[
            "ml.evaluation-orchestration"
        ]
        del controller_request
        controller_required = set(bundle.operation_contract(
            "ml.evaluation-orchestration",
        )[1]["required_evidence"])
        controller_values = _evidence_values(
            "ml.evaluation-orchestration", controller_result,
            controller_required,
        )
        if (
            controller_values.get("run_id") != run_id
            or controller_values.get("attempt_id") != attempt_id
            or controller_values.get("evaluator_digest")
            != manifest_content.get("evaluator_digest")
            or any(
                controller_values.get(name) != subjects.get(name)
                for name in (
                    "selected_checkpoint_attempt_id", "checkpoint_digest",
                    "model_artifact_digest", "training_r07_digest",
                    "training_r08_digest",
                )
            )
            or controller_values.get("terminal_status") != "successful"
        ):
            raise CLIError(
                "evaluation_orchestration_binding_mismatch",
                "Jenkins evaluation S13 does not bind the exact R01 evaluator/Run/Attempt",
                exit_code=3,
            )
        terminal_status = values["attempt_terminal_status"]
        validity = values["attempt_validity"]
        system_failure = values["system_failure"]
        if (
            terminal_status not in {"successful", "failed", "cancelled", "invalid"}
            or validity not in {"valid", "invalid"}
            or not isinstance(system_failure, bool)
            or (terminal_status == "invalid" and validity != "invalid")
            or (
                terminal_status == "successful"
                and (validity != "valid" or system_failure is not False)
            )
            or values["terminal_status"] != terminal_status
        ):
            raise CLIError(
                "evaluation_terminal_fact_invalid",
                "evaluation receipt must carry one canonical terminal Attempt fact",
                exit_code=3,
            )
        for name in (
            "evidence_summary", "statistical_analysis",
            "failure_case_index", "scientific_facts",
        ):
            if not isinstance(values[name], dict):
                raise CLIError(
                    "evaluation_scientific_fact_invalid",
                    f"evaluation evidence {name} must be a platform-produced object",
                    exit_code=3,
                )
        if not isinstance(values["artifacts"], list) or not values["artifacts"]:
            raise CLIError(
                "evaluation_artifact_index_invalid",
                "evaluation evidence must identify every immutable output artifact",
                exit_code=3,
            )
        receipt = _receipt_reference(request, result)
        environment_manifest, environment_digest = \
            _validated_runtime_environment(
                values=values, operation=operation, r01=manifest,
            )
        runtime_content = {
            "run_id": run_id, "attempt_id": attempt_id,
            "attempt_kind": "locked_evaluation",
            "environment_manifest": environment_manifest,
            "environment_digest": environment_digest,
            "receipt": receipt,
        }
        publish(
            "R03", attempt_id, runtime_content,
            [f"R01:{run_id}"], result["producer_identity"],
        )
        actual_content = {
            "run_id": run_id, "attempt_id": attempt_id,
            "attempt_kind": "locked_evaluation",
            "status": terminal_status,
            "started_at": _required_string(values, "started_at", operation),
            "completed_at": _required_string(values, "completed_at", operation),
            "resource_use": values["resource_use"],
            "receipt": receipt,
            "validity": validity,
            "system_failure": system_failure,
            "evidence_summary": values["evidence_summary"],
            "statistical_analysis": values["statistical_analysis"],
            "failure_case_index": values["failure_case_index"],
            "scientific_facts": values["scientific_facts"],
            "metrics_uri": _required_string(values, "metrics_uri", operation),
            "metrics_digest": _required_digest(values, "metrics_digest", operation),
            "selected_checkpoint_attempt_id": values[
                "selected_checkpoint_attempt_id"
            ],
            "evaluated_checkpoint_digest": values["checkpoint_digest"],
            "evaluated_model_artifact_digest": values[
                "model_artifact_digest"
            ],
            "training_r07_digest": values["training_r07_digest"],
            "training_r08_digest": values["training_r08_digest"],
        }
        index_content = {
            "run_id": run_id, "attempt_id": attempt_id,
            "attempt_kind": "locked_evaluation",
            "artifacts": values["artifacts"],
            "completeness": values["artifact_completeness"],
            "selected_checkpoint_attempt_id": values[
                "selected_checkpoint_attempt_id"
            ],
            "evaluated_checkpoint_digest": values["checkpoint_digest"],
            "evaluated_model_artifact_digest": values[
                "model_artifact_digest"
            ],
            "training_r07_digest": values["training_r07_digest"],
            "training_r08_digest": values["training_r08_digest"],
            "receipt": receipt,
        }
        publish(
            "R07", attempt_id, actual_content,
            [
                f"R01:{run_id}", f"R03:{attempt_id}",
                f"R07:{values['selected_checkpoint_attempt_id']}",
                f"R08:{values['selected_checkpoint_attempt_id']}",
            ],
            result["producer_identity"],
        )
        publish(
            "R08", attempt_id, index_content,
            [f"R01:{run_id}", f"R07:{attempt_id}"],
            result["producer_identity"],
        )
        source = {
            "request_ids": sorted(packet[0]["request_id"] for packet in packets.values()),
            "result_digests": sorted(digest(packet[1]) for packet in packets.values()),
            "logical_receipt_digest": logical,
        }
        evaluation_events = [
            {
                "operation_id": _phase_operation_id(logical, "evaluation-dispatched"),
                "event_type": "run.dispatched",
                "subjects": event_subjects,
                "payload": {"transition": "run.dispatch", **source},
            },
            {
                "operation_id": _phase_operation_id(logical, "evaluation-started"),
                "event_type": "attempt.started",
                "subjects": event_subjects,
                "payload": {
                    "transition": "attempt.start", **source,
                    "environment_digest": environment_digest,
                },
            },
            {
                "operation_id": _phase_operation_id(logical, "evaluation-terminal"),
                "event_type": "attempt.terminal",
                "subjects": event_subjects,
                "payload": {
                    "transition": "attempt.terminal", **source,
                    "terminal_status": terminal_status,
                    "status": terminal_status, "accounted": True,
                    "validity": validity, "system_failure": system_failure,
                    "published_artifacts": descriptors,
                },
            },
        ]
        if (
            terminal_status == "successful"
            and validity == "valid"
            and system_failure is False
        ):
            evaluation_events.append({
                "operation_id": _phase_operation_id(logical, "evaluation-accepted"),
                "event_type": "evaluation.accepted",
                "subjects": event_subjects,
                "payload": {
                    "transition": "evaluation.accept",
                    "step": 16, "lifecycle_step_completion": True,
                    "request_id": request["request_id"],
                    "result_digest": digest(result),
                    "published_artifacts": [],
                    "outputs": ["R07", "R08"],
                },
            })
        events.extend(evaluation_events)
        virtual.setdefault("checks", {}).update({
            "evaluator.digest_exact": True,
            "attempt.environment_matches_R01": True,
        })
    elif step == 17:
        request, result, _ = packets["ml.robustness-evaluation"]
        operation = "ml.robustness-evaluation"
        required = {
            "run_id", "attempt_id", "model_artifact_digest",
            "robustness_contract_digest", "environment_digest",
            "metrics_uri", "metrics_digest", "robustness_outcome",
            "artifact_completeness", "terminal_status", "resource_use",
        }
        values = _evidence_values(operation, result, required)
        if values["run_id"] != run_id or values["attempt_id"] != attempt_id:
            raise CLIError(
                "robustness_receipt_binding_mismatch",
                "robustness evidence does not bind the exact Run/Attempt",
                exit_code=3,
            )
        for name in (
            "model_artifact_digest", "robustness_contract_digest",
            "environment_digest", "metrics_digest",
        ):
            _required_digest(values, name, operation)
        selected_attempt_id = subjects.get("selected_checkpoint_attempt_id")
        contract_instance_key = subjects.get("robustness_contract_id")
        if (
            not isinstance(selected_attempt_id, str)
            or not selected_attempt_id
            or not isinstance(contract_instance_key, str)
            or not contract_instance_key
        ):
            raise CLIError(
                "robustness_source_subjects_required",
                "STEP17 subjects must pin selected_checkpoint_attempt_id and robustness_contract_id",
                exit_code=3,
            )
        selected_index, _ = _validated_stored_artifact(
            state, workspace, bundle, "R08", selected_attempt_id,
        )
        index_content = selected_index.get("content", selected_index)
        indexed_models = [
            item for item in index_content.get("artifacts", [])
            if isinstance(item, dict) and item.get("type") in {
                "model", "model_artifact", "exported_model",
            }
        ]
        indexed_checkpoints = [
            item for item in index_content.get("artifacts", [])
            if isinstance(item, dict) and item.get("type") in {
                "checkpoint", "selected_checkpoint",
            }
        ]
        if (
            index_content.get("run_id") != run_id
            or index_content.get("attempt_id") != selected_attempt_id
            or len(indexed_models) != 1
            or len(indexed_checkpoints) != 1
            or values["model_artifact_digest"] != indexed_models[0].get("digest")
        ):
            raise CLIError(
                "robustness_model_binding_mismatch",
                "robustness S13 must bind the exact model and selected checkpoint retained by R08",
                exit_code=3,
            )
        runtime_environment, _ = _validated_stored_artifact(
            state, workspace, bundle, "R03", selected_attempt_id,
        )
        runtime_content = runtime_environment.get(
            "content", runtime_environment,
        )
        if (
            runtime_content.get("run_id") != run_id
            or runtime_content.get("attempt_id") != selected_attempt_id
            or values["environment_digest"]
            != runtime_content.get("environment_digest")
        ):
            raise CLIError(
                "robustness_environment_binding_mismatch",
                "robustness S13 environment differs from the exact governed R03",
                exit_code=3,
            )
        _, robustness_contract_record = _validated_stored_artifact(
            state, workspace, bundle, "G11", contract_instance_key,
        )
        if (
            values["robustness_contract_digest"]
            != robustness_contract_record["digest"]
        ):
            raise CLIError(
                "robustness_contract_binding_mismatch",
                "robustness S13 differs from the exact governed G11",
                exit_code=3,
            )
        outcome = values["robustness_outcome"]
        if (
            not isinstance(outcome, dict)
            or set(outcome) != {"status", "scientific_facts"}
            or outcome.get("status") not in {"passed", "failed"}
            or not isinstance(outcome.get("scientific_facts"), dict)
            or not outcome["scientific_facts"]
        ):
            raise CLIError(
                "robustness_outcome_invalid",
                "robustness_outcome must retain the exact status and scientific facts",
                exit_code=3,
            )
        events.append({
            "operation_id": _phase_operation_id(logical, "robustness-assessed"),
            "event_type": "robustness.assessed",
            "subjects": event_subjects,
            "payload": {
                "transition": "robustness.assess",
                "step": 17, "lifecycle_step_completion": True,
                "request_id": request["request_id"],
                "result_digest": digest(result),
                "selected_checkpoint_attempt_id": selected_attempt_id,
                "selected_checkpoint_digest": indexed_checkpoints[0]["digest"],
                "model_artifact_digest": values["model_artifact_digest"],
                "robustness_contract_id": contract_instance_key,
                "robustness_contract_digest": robustness_contract_record["digest"],
                "environment_digest": values["environment_digest"],
                "robustness_outcome": deepcopy(outcome),
                "metrics_digest": values["metrics_digest"],
                "artifact_completeness": values["artifact_completeness"],
                "published_artifacts": [], "outputs": [],
            },
        })
        virtual.setdefault("checks", {}).update({
            "robustness.pass_or_fail_retained": True,
            "robustness.no_result_editing": True,
        })
    else:  # pragma: no cover - caller guard
        raise CLIError(
            "platform_execution_step_invalid",
            "platform execution materialization is limited to STEP15-17",
            exit_code=70,
        )
    return descriptors, writes, virtual, events


def _transition_source_missing(
    name: str, transition: dict[str, Any], state: dict[str, Any],
) -> list[str]:
    required = transition.get("from")
    if not isinstance(required, str):
        return []
    lifecycle = state.get("dimensions", {}).get("lifecycle")
    if required == "any-non-closed":
        return [] if lifecycle != "closed" else ["state:dimensions.lifecycle"]
    if name in {"lifecycle.archive-provisional", "lifecycle.archive"}:
        scientific = state.get("dimensions", {}).get("scientific")
        if lifecycle == "cancelled" or scientific in {
            "hypothesis_supported", "hypothesis_rejected", "inconclusive", "invalid",
        }:
            return []
        return ["state:scientific-terminal-or-cancelled"]
    dimension = {
        "protocol": "protocol",
        "experiment": "experiment",
        "lifecycle": "lifecycle",
    }.get(name.split(".", 1)[0])
    if not dimension:
        return []
    actual = state.get("dimensions", {}).get(dimension)
    allowed = required.split("|")
    return [] if actual in allowed else [f"state:dimensions.{dimension}"]


def _transition_gate(
    name: str, transition: dict[str, Any], state: dict[str, Any],
) -> tuple[bool, list[str], list[str]]:
    gate = transition.get("gate")
    passed, missing, events = (
        (True, [], []) if not isinstance(gate, dict)
        else evaluate_gate(gate, state)
    )
    missing.extend(_transition_source_missing(name, transition, state))
    return passed and not missing, sorted(set(missing)), events


def _gate_operations(node: dict[str, Any]) -> list[str]:
    kind, value = next(iter(node.items()))
    if kind in {"operation", "platform_result"}:
        return [value if isinstance(value, str) else value["operation"]]
    if kind in {"all_of", "sequence", "any_of"}:
        return list(dict.fromkeys(
            operation
            for child in value
            for operation in _gate_operations(child)
        ))
    if kind == "when":
        operations = _gate_operations(value["then"])
        if "else" in value:
            operations.extend(_gate_operations(value["else"]))
        return list(dict.fromkeys(operations))
    return []


def _gate_checks(node: dict[str, Any]) -> list[str]:
    kind, value = next(iter(node.items()))
    if kind == "check":
        return [value if isinstance(value, str) else value["name"]]
    if kind in {"all_of", "sequence", "any_of"}:
        return list(dict.fromkeys(
            check for child in value for check in _gate_checks(child)
        ))
    if kind == "when":
        checks = _gate_checks(value["then"])
        if "else" in value:
            checks.extend(_gate_checks(value["else"]))
        return list(dict.fromkeys(checks))
    return []


def _matching_stored_artifacts(
    *, state: dict[str, Any], workspace: str, bundle: AssetBundle,
    artifact_id: str, subjects: dict[str, str],
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    matches: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    instances = state.get("artifacts", {}).get(artifact_id, {})
    if not isinstance(instances, dict):
        return matches
    for instance_key in sorted(instances):
        value, record = _validated_stored_artifact(
            state, workspace, bundle, artifact_id, instance_key,
        )
        envelope_subjects = value.get("subjects", {})
        if isinstance(envelope_subjects, dict) and all(
            envelope_subjects.get(key) == expected
            for key, expected in subjects.items()
        ):
            matches.append((instance_key, value, record))
    return matches


def _step_required_outputs(
    *,
    row: dict[str, Any],
    declared: list[str],
    raw_records: list[dict[str, Any]],
    state: dict[str, Any],
    workspace: str,
    bundle: AssetBundle,
    command_subjects: dict[str, str],
) -> set[str]:
    """Resolve conditional output applicability from governed input bytes."""

    step = row["step"]
    required = set(declared)
    if step == 3:
        brief_key = command_subjects.get("research_brief_id")
        if not isinstance(brief_key, str) or not brief_key:
            raise CLIError(
                "contract_applicability_subject_missing",
                "STEP03 requires the exact governed S02 research_brief_id",
                exit_code=3,
            )
        brief, _ = _validated_stored_artifact(
            state, workspace, bundle, "S02", brief_key,
        )
        profile = brief.get("content", brief).get(
            "ml_contract_applicability",
        )
        contract_ids = (
            profile.get("required_contract_ids")
            if isinstance(profile, dict) else None
        )
        always = {"G01", "G02", "G03", "G07", "G08", "G09", "G10", "G13"}
        universe = {f"G{index:02d}" for index in range(1, 14)}
        if (
            not isinstance(contract_ids, list)
            or any(not isinstance(value, str) for value in contract_ids)
            or len(set(contract_ids)) != len(contract_ids)
            or not always.issubset(contract_ids)
            or not set(contract_ids).issubset(universe)
        ):
            raise CLIError(
                "contract_applicability_invalid",
                "S02 must pin a unique complete required_contract_ids subset with all always-applicable contracts",
                exit_code=3,
            )
        platform_manifests = {"R04", "R05"}
        if "G05" in contract_ids:
            platform_manifests.add("R06")
        required = set(contract_ids) | platform_manifests
        supplied = {
            value.get("artifact_type") for value in raw_records
            if isinstance(value.get("artifact_type"), str)
        }
        unexpected = sorted(supplied - required)
        if unexpected:
            raise CLIError(
                "contract_not_applicable",
                "STEP03 received records declared not applicable by S02: "
                + ", ".join(unexpected),
                exit_code=3,
            )
    elif step == 6:
        experiment_records = [
            value for value in raw_records
            if value.get("artifact_type") == "E01"
        ]
        if len(experiment_records) != 1:
            raise CLIError(
                "experiment_spec_required",
                "STEP06 requires exactly one prospective E01 record",
                exit_code=3,
            )
        content = experiment_records[0].get("content", {})
        search = content.get("search") if isinstance(content, dict) else None
        if isinstance(search, dict):
            mode = search.get("mode")
            if mode not in {"none", "hpo"}:
                raise CLIError(
                    "hpo_applicability_missing",
                    "E01 search.mode must be exactly none or hpo",
                    exit_code=3,
                )
            hpo_enabled = mode == "hpo"
        elif isinstance(content.get("hpo_enabled"), bool):
            hpo_enabled = content["hpo_enabled"]
        else:
            raise CLIError(
                "hpo_applicability_missing",
                "E01 must explicitly pin HPO applicability",
                exit_code=3,
            )
        required = {"E01", "E04"}
        supplied_e03 = any(
            value.get("artifact_type") == "E03" for value in raw_records
        )
        if hpo_enabled:
            required.add("E03")
            search_key = (
                search.get("search_spec_instance_key")
                if isinstance(search, dict)
                else content.get("search_spec_instance_key")
            )
            if (
                not isinstance(search_key, str)
                or not any(
                    value.get("artifact_type") == "E03"
                    and value.get("instance_key") == search_key
                    for value in raw_records
                )
            ):
                raise CLIError(
                    "search_spec_binding_missing",
                    "HPO E01 must pin the exact E03 record published in the same transaction",
                    exit_code=3,
                )
        elif supplied_e03:
            raise CLIError(
                "hpo_applicability_conflict",
                "non-HPO E01 cannot publish an E03 search specification",
                exit_code=3,
            )
    elif step == 21:
        # Continue creates a reciprocal successor Experiment and its research
        # brief.  Archive is a separate anchored S17 public transition and is
        # never required alongside the successor branch.
        required = {"E01", "S02"}
    return required


_STEP_REQUIRED_ARTIFACT_INPUTS: dict[int, tuple[str, ...]] = {
    2: ("S01", "S03"),
    3: ("S02",),
    4: ("G02", "G03", "G09", "G10"),
    5: ("B01", "G02", "G09", "G10", "R04", "R05"),
    6: ("S01", "S02", "S03", "G01", "B02", "B03"),
    7: (
        "E01", "E04", "B02", "B03", "G02", "G05", "G06", "G07",
    ),
    9: ("E01", "S03"),
    10: ("S06",),
    11: ("E05",),
    12: ("S07",),
    13: ("S08", "E01", "E04"),
    19: ("E01", "D01", "D02", "D03", "D04"),
}


def _validated_input_refs(
    *,
    payload: dict[str, Any],
    row: dict[str, Any],
    state: dict[str, Any],
    workspace: str,
    bundle: AssetBundle,
) -> tuple[list[str], dict[str, str]]:
    required = _STEP_REQUIRED_ARTIFACT_INPUTS.get(row["step"], ())
    raw = payload.get("input_refs", {})
    if not isinstance(raw, dict):
        raise CLIError(
            "step_input_refs_invalid",
            "input_refs must map artifact IDs to exact instance/digest selectors",
            exit_code=3,
        )
    if row["step"] == 5 and state.get("artifacts", {}).get("R06"):
        required = (*required, "R06")
    if row["step"] in {6, 7}:
        seed_type = "S02" if row["step"] == 6 else "E01"
        seed_selectors = raw.get(seed_type)
        if isinstance(seed_selectors, dict):
            seed_selectors = [seed_selectors]
        if not isinstance(seed_selectors, list) or len(seed_selectors) != 1 \
                or not isinstance(seed_selectors[0], dict) \
                or set(seed_selectors[0]) != {"instance_key", "digest"}:
            raise CLIError(
                "step_required_input_missing",
                f"STEP{row['step']:02d} requires one exact {seed_type} selector before its source closure can be derived",
                exit_code=3,
            )
        seed, seed_record = _validated_stored_artifact(
            state, workspace, bundle, seed_type,
            seed_selectors[0]["instance_key"],
        )
        if seed_selectors[0]["digest"] != seed_record["digest"]:
            raise CLIError(
                "step_input_digest_mismatch",
                f"{seed_type} source-closure selector is stale or substituted",
                exit_code=3,
            )
        if row["step"] == 6:
            seed_content = seed.get("content", seed)
            profile = seed_content.get("ml_contract_applicability") \
                if isinstance(seed_content, dict) else None
            contract_ids = profile.get("required_contract_ids") \
                if isinstance(profile, dict) else None
            if not isinstance(contract_ids, list) or not contract_ids:
                raise CLIError(
                    "contract_applicability_invalid",
                    "STEP06 requires S02's exact applicable G-contract closure",
                    exit_code=3,
                )
            planning_sources = seed_content.get("source_refs") \
                if isinstance(seed_content, dict) else None
            planning_types = [
                node.split(":", 1)[0] for node in planning_sources
            ] if isinstance(planning_sources, dict) else []
            if (
                not isinstance(planning_sources, dict)
                or len(planning_sources) != 2
                or sorted(planning_types) != ["S01", "S03"]
                or any(
                    not isinstance(bound, str)
                    for bound in planning_sources.values()
                )
            ):
                raise CLIError(
                    "planning_source_closure_invalid",
                    "STEP06 requires S02's exact singleton S01 and S03 planning source closure",
                    exit_code=3,
                )
            dynamic = [
                "S01", "S02", "S03", *contract_ids,
                "B02", "B03", "R04", "R05",
            ]
            if "G05" in contract_ids:
                dynamic.append("R06")
        else:
            source_refs = seed.get("content", seed).get("source_refs")
            if not isinstance(source_refs, dict) or not source_refs:
                raise CLIError(
                    "experiment_source_refs_missing",
                    "STEP07 E01 lacks an exact immutable source closure",
                    exit_code=3,
                )
            dynamic = [
                "E01", "E04",
                *sorted({node.split(":", 1)[0] for node in source_refs}),
            ]
        required = tuple(dict.fromkeys(dynamic))
    unknown = sorted(set(raw) - set(required))
    if unknown:
        raise CLIError(
            "step_input_ref_undeclared",
            "input_refs contains artifacts not required by this row: "
            + ", ".join(unknown),
            exit_code=3,
        )
    nodes: list[str] = []
    digests: dict[str, str] = {}
    missing: list[str] = []
    for artifact_id in required:
        selectors = raw.get(artifact_id)
        if isinstance(selectors, dict):
            selectors = [selectors]
        if not isinstance(selectors, list) or not selectors:
            missing.append(artifact_id)
            continue
        seen: set[str] = set()
        for selector in selectors:
            if not isinstance(selector, dict) or set(selector) != {
                "instance_key", "digest",
            }:
                raise CLIError(
                    "step_input_ref_invalid",
                    f"{artifact_id} selectors require exactly instance_key and digest",
                    exit_code=3,
                )
            instance_key = selector["instance_key"]
            if not isinstance(instance_key, str) or instance_key in seen:
                raise CLIError(
                    "step_input_ref_invalid",
                    f"{artifact_id} selectors must have unique portable instance keys",
                    exit_code=3,
                )
            seen.add(instance_key)
            _, record = _validated_stored_artifact(
                state, workspace, bundle, artifact_id, instance_key,
            )
            if selector["digest"] != record["digest"]:
                raise CLIError(
                    "step_input_digest_mismatch",
                    f"{artifact_id}:{instance_key} selector digest is stale or substituted",
                    exit_code=3,
                )
            node = f"{artifact_id}:{instance_key}"
            nodes.append(node)
            digests[node] = record["digest"]
    if missing:
        raise CLIError(
            "step_required_input_missing",
            "earliest lifecycle row lacks exact required inputs: "
            + ", ".join(missing),
            exit_code=3,
            findings=[
                finding(
                    "step_required_input_missing",
                    f"exact required input selector is absent: {artifact_id}",
                    subject=f"STEP{row['step']:02d}:{artifact_id}",
                    governing_asset="lifecycle-steps.json",
                )
                for artifact_id in missing
            ],
        )
    if row["step"] == 7:
        e01_node = next(node for node in nodes if node.startswith("E01:"))
        e01, _ = _validated_stored_artifact(
            state, workspace, bundle, "E01", e01_node.split(":", 1)[1],
        )
        exact_sources = e01.get("content", e01).get("source_refs")
        selected_sources = {
            node: bound for node, bound in digests.items()
            if not node.startswith("E01:") and not node.startswith("E04:")
        }
        if selected_sources != exact_sources:
            raise CLIError(
                "readiness_source_closure_mismatch",
                "STEP07 selectors do not exactly equal E01's immutable source_refs",
                exit_code=3,
            )
    if row["step"] == 6:
        s02_node = next(node for node in nodes if node.startswith("S02:"))
        s02, _ = _validated_stored_artifact(
            state, workspace, bundle, "S02", s02_node.split(":", 1)[1],
        )
        exact_sources = s02.get("content", s02).get("source_refs")
        selected_sources = {
            node: bound for node, bound in digests.items()
            if node.startswith("S01:") or node.startswith("S03:")
        }
        if selected_sources != exact_sources:
            raise CLIError(
                "planning_source_closure_mismatch",
                "STEP06 S01/S03 selectors do not exactly equal S02's immutable planning source_refs",
                exit_code=3,
            )
    return sorted(nodes), digests


def _validated_experiment_anchor_context(
    *, content: dict[str, Any], state: dict[str, Any], workspace: str,
    bundle: AssetBundle,
) -> dict[str, Any]:
    """Re-open every immutable E01 scientific anchor and verify its meaning."""

    source_refs = content.get("source_refs")
    if not isinstance(source_refs, dict) or not source_refs:
        raise CLIError(
            "experiment_source_refs_missing",
            "E01 must bind exact immutable source artifact identities and digests",
            exit_code=3,
        )

    def exact(ref: Any, expected_type: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(ref, str) or ref.count(":") != 1:
            raise CLIError(
                "experiment_anchor_identity_invalid",
                f"E01 must name one exact {expected_type}:instance identity",
                exit_code=3,
            )
        artifact_id, instance_key = ref.split(":", 1)
        if artifact_id != expected_type or source_refs.get(ref) is None:
            raise CLIError(
                "experiment_anchor_identity_invalid",
                f"E01 {ref} is absent from the exact source_refs closure",
                exit_code=3,
            )
        value, record = _validated_stored_artifact(
            state, workspace, bundle, artifact_id, instance_key,
        )
        if record["digest"] != source_refs[ref]:
            raise CLIError(
                "experiment_anchor_digest_mismatch",
                f"E01 source_refs substitutes {ref}", exit_code=3,
            )
        return value, record

    contracts = content.get("contracts")
    baseline = content.get("baseline")
    data = content.get("data")
    evaluator = content.get("evaluator")
    if not all(isinstance(item, dict) for item in (
        contracts, baseline, data, evaluator,
    )):
        raise CLIError(
            "experiment_anchor_shape_invalid",
            "E01 contracts/baseline/data/evaluator sections must be executable anchor objects",
            exit_code=3,
        )
    assert isinstance(contracts, dict)
    assert isinstance(baseline, dict)
    assert isinstance(data, dict)
    assert isinstance(evaluator, dict)
    contract_sources = {
        node: bound for node, bound in source_refs.items()
        if node.startswith("G") and node.split(":", 1)[0][1:].isdigit()
    }
    if contracts.get("source_digests") != contract_sources:
        raise CLIError(
            "experiment_contract_closure_mismatch",
            "E01 contracts.source_digests must exactly equal every selected G contract source",
            exit_code=3,
        )
    for node in contract_sources:
        artifact_id, instance_key = node.split(":", 1)
        _, record = _validated_stored_artifact(
            state, workspace, bundle, artifact_id, instance_key,
        )
        if record["digest"] != contract_sources[node]:
            raise CLIError(
                "experiment_contract_digest_mismatch",
                f"E01 contract source is stale or substituted: {node}", exit_code=3,
            )
    lock, lock_record = exact(baseline.get("lock_ref"), "B02")
    lock_content = lock.get("content", lock)
    if (
        baseline.get("lock_digest") != lock_record["digest"]
        or baseline.get("baseline_digest") != lock_content.get("baseline_digest")
        or baseline.get("data_digest") != lock_content.get("data_digest")
        or baseline.get("evaluator_digest") != lock_content.get("evaluator_digest")
    ):
        raise CLIError(
            "experiment_baseline_binding_mismatch",
            "E01 baseline does not exactly derive from B02", exit_code=3,
        )
    dataset, dataset_record = exact(data.get("dataset_manifest_ref"), "R04")
    split, split_record = exact(data.get("split_manifest_ref"), "R05")
    dataset_content = dataset.get("content", dataset)
    split_content = split.get("content", split)
    if (
        data.get("dataset_manifest_digest") != dataset_record["digest"]
        or data.get("dataset_digest") != dataset_content.get("content_digest")
        or data.get("split_manifest_digest") != split_record["digest"]
        or data.get("split_digest") != split_content.get("membership_digest")
        or split_content.get("dataset_content_digest")
        != dataset_content.get("content_digest")
    ):
        raise CLIError(
            "experiment_data_binding_mismatch",
            "E01 dataset/split anchors do not exactly derive from R04/R05",
            exit_code=3,
        )
    data_contract_refs = [
        node for node in contract_sources if node.startswith("G03:")
    ]
    if len(data_contract_refs) != 1:
        raise CLIError(
            "experiment_data_contract_ambiguous",
            "E01 must select exactly one G03 data contract", exit_code=3,
        )
    data_contract, _ = exact(data_contract_refs[0], "G03")
    if data_contract.get("content", data_contract).get("dataset_identity") \
            != dataset_content.get("dataset_identity"):
        raise CLIError(
            "experiment_data_contract_mismatch",
            "R04 dataset identity differs from exact G03", exit_code=3,
        )
    evaluator_contract, evaluator_record = exact(
        evaluator.get("contract_ref"), "G10",
    )
    evaluator_content = evaluator_contract.get("content", evaluator_contract)
    if (
        evaluator.get("contract_digest") != evaluator_record["digest"]
        or evaluator.get("evaluator_identity")
        != evaluator_content.get("evaluator_identity")
    ):
        raise CLIError(
            "experiment_evaluator_binding_mismatch",
            "E01 evaluator does not exactly derive from G10", exit_code=3,
        )
    calibration_applicable = data.get("calibration_applicable")
    calibration_record = calibration_content = None
    if not isinstance(calibration_applicable, bool):
        raise CLIError(
            "calibration_applicability_missing",
            "E01 must explicitly pin calibration applicability", exit_code=3,
        )
    if calibration_applicable:
        calibration, calibration_record = exact(
            data.get("calibration_manifest_ref"), "R06",
        )
        calibration_content = calibration.get("content", calibration)
        if (
            data.get("calibration_manifest_digest")
            != calibration_record["digest"]
            or data.get("calibration_digest")
            != calibration_content.get("content_digest")
        ):
            raise CLIError(
                "experiment_calibration_binding_mismatch",
                "E01 calibration does not exactly derive from R06", exit_code=3,
            )
    elif any(name in data for name in (
        "calibration_manifest_ref", "calibration_manifest_digest",
        "calibration_digest",
    )):
        raise CLIError(
            "experiment_calibration_binding_mismatch",
            "non-calibrated E01 cannot retain an R06 substitute", exit_code=3,
        )
    return {
        "source_refs": source_refs,
        "contracts": contract_sources,
        "baseline_lock_ref": baseline["lock_ref"],
        "baseline_lock_digest": lock_record["digest"],
        "dataset_manifest_ref": data["dataset_manifest_ref"],
        "dataset_manifest_digest": dataset_record["digest"],
        "dataset_digest": dataset_content["content_digest"],
        # E01 names the immutable dataset payload ``dataset_digest`` while
        # E04/R01 retain the established ``data_digest`` field.  Return the
        # same authenticated value under both vocabulary names so every
        # downstream comparison is exact rather than caller-derived.
        "data_digest": dataset_content["content_digest"],
        "split_manifest_ref": data["split_manifest_ref"],
        "split_manifest_digest": split_record["digest"],
        "split_digest": split_content["membership_digest"],
        "evaluator_contract_ref": evaluator["contract_ref"],
        "evaluator_contract_digest": evaluator_record["digest"],
        "evaluator_digest": evaluator_record["digest"],
        "calibration_applicable": calibration_applicable,
        "calibration_manifest_ref": data.get("calibration_manifest_ref"),
        "calibration_manifest_digest": (
            calibration_record["digest"] if calibration_record else None
        ),
        "calibration_digest": (
            calibration_content["content_digest"] if calibration_content else None
        ),
    }


def _step07_bound_subjects(
    *, payload: dict[str, Any], subjects: dict[str, str], state: dict[str, Any],
    workspace: str, bundle: AssetBundle,
) -> dict[str, str]:
    selectors = payload.get("input_refs", {}).get("E01")
    if isinstance(selectors, dict):
        selectors = [selectors]
    if not isinstance(selectors, list) or len(selectors) != 1 \
            or not isinstance(selectors[0], dict):
        raise CLIError(
            "freeze_input_identity_ambiguous",
            "STEP07 requires one exact E01 before data-platform S12 can be derived",
            exit_code=3,
        )
    experiment, record = _validated_stored_artifact(
        state, workspace, bundle, "E01", selectors[0].get("instance_key"),
    )
    if selectors[0].get("digest") != record["digest"]:
        raise CLIError(
            "freeze_input_digest_mismatch",
            "STEP07 E01 selector is stale or substituted", exit_code=3,
        )
    anchors = _validated_experiment_anchor_context(
        content=experiment.get("content", experiment), state=state,
        workspace=workspace, bundle=bundle,
    )
    derived = {
        "dataset_manifest_digest": anchors["dataset_manifest_digest"],
        "split_manifest_digest": anchors["split_manifest_digest"],
        "calibration_manifest_digest_when_applicable": (
            anchors["calibration_manifest_digest"]
            if anchors["calibration_applicable"] else "not-applicable"
        ),
        "evaluator_digest": anchors["evaluator_digest"],
        "baseline_lock_digest": anchors["baseline_lock_digest"],
    }
    conflicts = sorted(
        name for name, value in derived.items()
        if name in subjects and subjects[name] != value
    )
    if conflicts:
        raise CLIError(
            "readiness_subject_anchor_mismatch",
            "caller STEP07 subjects substitute derived immutable anchors: "
            + ", ".join(conflicts),
            exit_code=3,
        )
    return {**subjects, **derived}


def _exact_step_input_artifact(
    *, payload: dict[str, Any], artifact_id: str, state: dict[str, Any],
    workspace: str, bundle: AssetBundle,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    selectors = payload.get("input_refs", {}).get(artifact_id)
    if isinstance(selectors, dict):
        selectors = [selectors]
    if (
        not isinstance(selectors, list) or len(selectors) != 1
        or not isinstance(selectors[0], dict)
        or set(selectors[0]) != {"instance_key", "digest"}
    ):
        raise CLIError(
            "engineering_input_identity_ambiguous",
            f"engineering gate requires one exact {artifact_id} selector",
            exit_code=3,
        )
    instance_key = selectors[0]["instance_key"]
    value, record = _validated_stored_artifact(
        state, workspace, bundle, artifact_id, instance_key,
    )
    if selectors[0]["digest"] != record["digest"]:
        raise CLIError(
            "engineering_input_digest_mismatch",
            f"{artifact_id} engineering selector is stale or substituted",
            exit_code=3,
        )
    return value, record, instance_key


def _engineering_step_subjects(
    *, step: int, payload: dict[str, Any], subjects: dict[str, str],
    state: dict[str, Any], workspace: str, bundle: AssetBundle,
) -> dict[str, str]:
    """Derive STEP10-13 platform scope from governed predecessor bytes."""

    if step not in {10, 11, 12, 13}:
        return subjects
    derived: dict[str, str]
    if step == 10:
        experiment_id = subjects.get("experiment_id")
        if not isinstance(experiment_id, str) or not experiment_id:
            raise CLIError(
                "engineering_experiment_subject_missing",
                "STEP10 requires the frozen experiment identity", exit_code=3,
            )
        _, frozen_record = _validated_stored_artifact(
            state, workspace, bundle, "E01", f"{experiment_id}.frozen",
        )
        forbidden = sorted(
            set(subjects).intersection({
                *ENGINEERING_PROVENANCE_COMMON_FIELDS,
                *ENGINEERING_PROVENANCE_MERGED_FIELDS,
            }) - {"frozen_experiment_digest"}
        )
        if forbidden:
            raise CLIError(
                "engineering_subject_caller_forbidden",
                "STEP10 caller cannot assign repository/change/pipeline identity: "
                + ", ".join(forbidden), exit_code=3,
            )
        derived = {"frozen_experiment_digest": frozen_record["digest"]}
    else:
        predecessor = {11: "E05", 12: "S07", 13: "S08"}[step]
        value, _, _ = _exact_step_input_artifact(
            payload=payload, artifact_id=predecessor, state=state,
            workspace=workspace, bundle=bundle,
        )
        content = value.get("content", value)
        provenance = content.get("engineering_provenance") \
            if isinstance(content, dict) else None
        needed = [*ENGINEERING_PROVENANCE_COMMON_FIELDS]
        if step == 13:
            needed.extend(ENGINEERING_PROVENANCE_MERGED_FIELDS)
        if (
            not isinstance(provenance, dict)
            or provenance.get("schema") != ENGINEERING_PROVENANCE_SCHEMA
            or any(not isinstance(provenance.get(name), str)
                   or not provenance[name] for name in needed)
        ):
            raise CLIError(
                "engineering_predecessor_provenance_invalid",
                f"{predecessor} lacks exact versioned engineering provenance",
                exit_code=3,
            )
        derived = {name: provenance[name] for name in needed}
    conflicts = sorted(
        name for name, expected in derived.items()
        if name in subjects and subjects[name] != expected
    )
    if conflicts:
        raise CLIError(
            "engineering_subject_substitution",
            "caller engineering subjects differ from governed predecessor facts: "
            + ", ".join(conflicts), exit_code=3,
        )
    return {**subjects, **derived}


def _content_lineage_binding_required(
    bundle: AssetBundle, artifact_id: str,
) -> bool:
    """Return whether this artifact's pinned content contract owns source_refs.

    T01 parents are always persisted in the event descriptor.  Some owner
    schemas additionally expose ``content.source_refs`` and therefore require
    the same exact map in the artifact bytes.  Closed owner schemas that do
    not expose that field must not be made impossible to publish merely to
    duplicate T01.
    """

    if artifact_id in {"S02", "S03"}:
        return True
    for catalog_name in (
        "artifact-catalog.json", "supporting-artifact-catalog.json",
    ):
        catalog = bundle.document(catalog_name)
        for binding in catalog.get("owned_schema_bindings", []):
            if artifact_id not in binding.get("artifact_ids", []):
                continue
            schema = bundle.document(binding["path"])
            for variant in schema.get("oneOf", []):
                properties = variant.get("properties", {})
                if properties.get("artifact_type", {}).get("const") != artifact_id:
                    continue
                content = properties.get("content", {})
                return "source_refs" in content.get("properties", {})
            return False
    return False


def _normalized_preflight_configurations(
    raw: Any, *, signed_digest: Any, expected_run_ids: list[str],
) -> list[dict[str, Any]]:
    required = {
        "run_id", "resolved_values", "defaults", "override_origins",
        "structured_config_schema_digest", "unknown_keys",
        "forbidden_overrides", "resolution_trace_digest",
    }
    if (
        not isinstance(raw, list) or not raw
        or digest(raw) != signed_digest
        or any(not isinstance(row, dict) or set(row) != required for row in raw)
    ):
        raise CLIError(
            "preflight_resolved_configurations_invalid",
            "Jenkins configuration facts/digest are incomplete or malformed",
            exit_code=3,
        )
    run_ids = [row.get("run_id") for row in raw]
    if run_ids != sorted(set(expected_run_ids)) or run_ids != sorted(set(run_ids)):
        raise CLIError(
            "preflight_resolved_configuration_run_set_mismatch",
            "Jenkins configurations do not exactly equal the governed E04 run set",
            exit_code=3,
        )
    normalized: list[dict[str, Any]] = []
    for row in raw:
        resolved = row["resolved_values"]
        defaults = row["defaults"]
        overrides = row["override_origins"]
        if (
            not isinstance(resolved, dict) or not resolved
            or not isinstance(defaults, list)
            or any(
                not isinstance(item, dict) or set(item) != {"ref", "digest"}
                or not isinstance(item.get("ref"), str) or not item["ref"]
                or not isinstance(item.get("digest"), str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", item["digest"]) is None
                for item in defaults
            )
            or len({item["ref"] for item in defaults}) != len(defaults)
            or not isinstance(overrides, list)
            or any(
                not isinstance(item, dict)
                or set(item) != {"path", "origin", "value_digest"}
                or not isinstance(item.get("path"), str)
                or not item["path"].startswith("/")
                or not isinstance(item.get("origin"), str) or not item["origin"]
                or not isinstance(item.get("value_digest"), str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", item["value_digest"]) is None
                for item in overrides
            )
            or [item["path"] for item in overrides]
            != sorted({item["path"] for item in overrides})
            or not isinstance(row["structured_config_schema_digest"], str)
            or re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                row["structured_config_schema_digest"],
            ) is None
            or row["unknown_keys"] != []
            or row["forbidden_overrides"] != []
            or row["resolution_trace_digest"]
            != resolved_configuration_trace_digest(row)
        ):
            raise CLIError(
                "preflight_resolved_configuration_invalid",
                f"Jenkins configuration facts are invalid for {row.get('run_id')}",
                exit_code=3,
            )
        for item in overrides:
            current: Any = resolved
            try:
                for token in item["path"].split("/")[1:]:
                    token = token.replace("~1", "/").replace("~0", "~")
                    current = current[int(token)] if isinstance(current, list) \
                        else current[token]
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise CLIError(
                    "preflight_override_path_invalid",
                    f"override path does not resolve: {item['path']}", exit_code=3,
                ) from exc
            if digest(current) != item["value_digest"]:
                raise CLIError(
                    "preflight_override_digest_mismatch",
                    f"override value digest is substituted: {item['path']}",
                    exit_code=3,
                )
        normalized.append({**deepcopy(row), "config_digest": digest(resolved)})
    return normalized


def _source_control_authored_records(
    *, records: list[dict[str, Any]], step: int, workspace: str,
    state: dict[str, Any], bundle: AssetBundle, subjects: dict[str, str],
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Inject the cumulative exact STEP10-13 Git/Jenkins evidence chain."""

    operations_by_step = {
        10: ("git.branch-change-request",),
        11: ("git.change-review-status", "ml.implementation-checks"),
        12: ("git.merge-status", "ml.deterministic-acceptance"),
        13: ("ml.post-implementation-preflight",),
    }
    operations = operations_by_step.get(step)
    if operations is None:
        return records
    packets = _exact_handoff_packets(
        workspace, state=state, bundle=bundle, operations=list(operations),
        gate=f"lifecycle-step-{step}", step=step, subjects=subjects,
    )
    evidence: dict[str, dict[str, Any]] = {}
    current_sources: dict[str, dict[str, str]] = {}
    for operation in operations:
        _, result, _ = packets[operation]
        required = set(
            bundle.operation_contract(operation)[1]["required_evidence"],
        )
        evidence[operation] = _evidence_values(operation, result, required)
        principal = result.get("producer_principal_id")
        if not isinstance(principal, str) or not principal:
            raise CLIError(
                "engineering_producer_principal_missing",
                f"{operation} lacks authenticated producer_principal_id",
                exit_code=3,
            )
        current_sources[operation] = {
            "result_digest": digest(result),
            "producer_principal_id": principal,
        }

    if step == 10:
        branch_values = evidence["git.branch-change-request"]
        common = {
            name: (
                _required_digest(branch_values, name, operations[0])
                if name.endswith("_digest")
                else _required_string(branch_values, name, operations[0])
            ) for name in ENGINEERING_PROVENANCE_COMMON_FIELDS
        }
        experiment_id = subjects.get("experiment_id")
        frozen, frozen_record = _validated_stored_artifact(
            state, workspace, bundle, "E01", f"{experiment_id}.frozen",
        )
        frozen_content = frozen.get("content", frozen)
        if common["frozen_experiment_digest"] != frozen_record["digest"]:
            raise CLIError(
                "diff_frozen_experiment_mismatch",
                "Git branch evidence does not bind the exact frozen E01",
                exit_code=3,
            )
        diff_evidence = {
            name: branch_values[name] for name in (
                "diff_scope_complete", "git_path_diff",
                "resolved_config_diff", "contract_ref_diff",
                "artifact_digest_diff", "required_equal_ref_digests",
                "frozen_variable_digests", "diff_evidence_digest",
            )
        }
        problems = validate_change_diff_evidence(frozen_content, diff_evidence)
        if problems:
            code, message, pointer = problems[0]
            raise CLIError(
                code, message, exit_code=3,
                findings=[finding(
                    code, message, subject="STEP10:git.branch-change-request",
                    pointer=pointer, governing_asset="platform-operations.json",
                )],
            )
        merged: dict[str, str] = {}
        previous_sources: dict[str, dict[str, str]] = {}
    else:
        predecessor = {11: "E05", 12: "S07", 13: "S08"}[step]
        previous, _, _ = _exact_step_input_artifact(
            payload=payload, artifact_id=predecessor, state=state,
            workspace=workspace, bundle=bundle,
        )
        previous_content = previous.get("content", previous)
        prior = previous_content.get("engineering_provenance") \
            if isinstance(previous_content, dict) else None
        if not isinstance(prior, dict):
            raise CLIError(
                "engineering_predecessor_provenance_invalid",
                f"{predecessor} engineering provenance is absent", exit_code=3,
            )
        common = {name: prior.get(name) for name in ENGINEERING_PROVENANCE_COMMON_FIELDS}
        merged = {
            name: prior.get(name) for name in ENGINEERING_PROVENANCE_MERGED_FIELDS
        } if step == 13 else {}
        previous_sources = deepcopy(prior.get("source_results", {}))

    for operation, values in evidence.items():
        for name, expected in common.items():
            observed = (
                _required_digest(values, name, operation)
                if name.endswith("_digest") else _required_string(values, name, operation)
            )
            if observed != expected:
                raise CLIError(
                    "engineering_platform_disagreement",
                    f"{operation} disagrees on {name}", exit_code=3,
                )
    if step == 12:
        git_values = evidence["git.merge-status"]
        merged = {
            name: (
                _required_digest(git_values, name, "git.merge-status")
                if name.endswith("_digest")
                else _required_string(git_values, name, "git.merge-status")
            ) for name in ENGINEERING_PROVENANCE_MERGED_FIELDS
        }
        if git_values.get("merge_status") != "merged":
            raise CLIError(
                "engineering_merge_not_terminal",
                "Git merge evidence must report merge_status=merged", exit_code=3,
            )
        for name, expected in merged.items():
            observed = evidence["ml.deterministic-acceptance"].get(name)
            if observed != expected:
                raise CLIError(
                    "engineering_platform_disagreement",
                    f"Git/Jenkins disagree on {name}", exit_code=3,
                )
    elif step == 13:
        values = evidence["ml.post-implementation-preflight"]
        for name, expected in merged.items():
            observed = (
                _required_digest(values, name, operations[0])
                if name.endswith("_digest") else _required_string(values, name, operations[0])
            )
            if observed != expected:
                raise CLIError(
                    "engineering_platform_disagreement",
                    f"preflight disagrees on {name}", exit_code=3,
                )
    if step == 11 and evidence["git.change-review-status"].get(
        "review_status",
    ) not in {"approved", "accepted"}:
        raise CLIError(
            "engineering_review_not_approved",
            "Git review evidence must be approved", exit_code=3,
        )

    duplicate_sources = set(previous_sources).intersection(current_sources)
    if duplicate_sources:
        raise CLIError(
            "engineering_source_result_duplicate",
            "engineering provenance attempts to replace an earlier S13 result",
            exit_code=3,
        )
    provenance: dict[str, Any] = {
        "schema": ENGINEERING_PROVENANCE_SCHEMA,
        **common, **merged,
        "source_results": {**previous_sources, **current_sources},
    }
    target = {10: "E05", 11: "S07", 12: "S08", 13: "S09"}[step]
    derived: dict[str, Any] = {"engineering_provenance": provenance}
    git_operation = {10: operations[0], 11: operations[0], 12: operations[0]}.get(step)
    if git_operation:
        authors = evidence[git_operation].get("commit_author_principal_ids")
        if (
            not isinstance(authors, list) or not authors
            or authors != sorted(set(authors))
            or any(not isinstance(author, str) or not author for author in authors)
        ):
            raise CLIError(
                "source_control_authorship_invalid",
                f"{git_operation} authors are not a canonical principal set",
                exit_code=3,
            )
        source_revision = merged.get("merged_revision", common["change_revision"])
        derived.update({
            "author_principal_ids": authors,
            "source_control_revision": source_revision,
            "source_control_result_digest": current_sources[git_operation]["result_digest"],
        })
    if step == 10:
        derived.update({
            "pipeline_identity": common["pipeline_identity"],
            "definition_digest": common["pipeline_definition_digest"],
            "revision": common["change_revision"],
            "frozen_experiment_digest": common["frozen_experiment_digest"],
            "diff_evidence_digest": common["diff_evidence_digest"],
        })
    elif step == 11:
        derived["change_request"] = common["change_request_identity"]
    elif step == 12:
        derived.update({
            "revision": merged["merged_revision"],
            "tree_digest": merged["merged_tree_digest"],
        })
    else:
        values = evidence[operations[0]]
        checks = values.get("geometry_tensor_finite_tiny_resume_results")
        if (
            not isinstance(checks, dict)
            or set(checks) != {"geometry", "tensor", "finite", "tiny", "resume"}
            or any(value != "pass" for value in checks.values())
        ):
            raise CLIError(
                "preflight_check_set_invalid",
                "Jenkins preflight must return the exact passing check set",
                exit_code=3,
            )
        run_plan, _, _ = _exact_step_input_artifact(
            payload=payload, artifact_id="E04", state=state,
            workspace=workspace, bundle=bundle,
        )
        run_plan_content = run_plan.get("content", run_plan)
        runs = run_plan_content.get("runs") if isinstance(run_plan_content, dict) else None
        expected_run_ids = [
            row.get("run_id") for row in runs if isinstance(row, dict)
        ] if isinstance(runs, list) else []
        configurations = _normalized_preflight_configurations(
            values.get("resolved_configurations"),
            signed_digest=values.get("resolved_configurations_digest"),
            expected_run_ids=expected_run_ids,
        )
        derived.update({
            "experiment_digest": common["frozen_experiment_digest"],
            "revision": merged["merged_revision"],
            **checks,
            "receipt": _required_string(values, "artifact_receipt_uri", operations[0]),
            "resolved_configurations": configurations,
            "resolved_configurations_digest": digest(configurations),
        })

    enriched: list[dict[str, Any]] = []
    matched = 0
    for candidate in records:
        value = deepcopy(candidate)
        if value.get("artifact_type") == target:
            matched += 1
            content = value.get("content")
            if not isinstance(content, dict):
                raise CLIError(
                    "engineering_target_invalid",
                    f"{target} content must be an object", exit_code=3,
                )
            conflicts = sorted(
                name for name, expected in derived.items()
                if name in content and content[name] != expected
            )
            if conflicts:
                raise CLIError(
                    "source_control_authorship_substitution",
                    f"{target} substitutes authenticated engineering facts: "
                    + ", ".join(conflicts), exit_code=3,
                )
            value["content"] = {**content, **derived}
            value["content_digest"] = digest(value["content"])
        enriched.append(value)
    if matched != 1:
        raise CLIError(
            "source_control_authorship_target_invalid",
            f"STEP{step:02d} requires exactly one {target} governed output",
            exit_code=3,
        )
    return enriched


def _step_artifact_subjects(step: int, workspace_id: str, command_subjects: dict[str, str]) -> dict[str, str]:
    """Keep STEP03 request selectors out of contract identity, avoiding G10 self-hashes.

    Exact S12/S13 subject equality and manifest/G10 digest checks remain in the
    data boundary.  Contracts bind logical planning subjects; R04-R06 additionally
    retain the complete platform selectors through their authenticated receipts.
    """
    subjects = {"workspace_id": workspace_id, **command_subjects}
    if step == 3:
        for name in (
            "dataset_namespace", "dataset_name", "dataset_version", "dataset_version_uid",
            "dataset_scope_digest", "calibration_applicability", "calibration_validity_mode",
            "evaluator_digest",
        ):
            subjects.pop(name, None)
    return subjects


def _validated_step_records(
    *, payload: dict[str, Any], row: dict[str, Any], state: dict[str, Any],
    workspace: str, workspace_id: str, bundle: AssetBundle,
    command_subjects: dict[str, str], platform_subjects: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[str, Any, bool]], dict[str, Any]]:
    declared_labels = list(dict.fromkeys(
        list(row.get("governed_outputs", []))
        + list(row.get("supporting_outputs", []))
        + list(row.get("outputs", []))
    ))
    declared: list[str] = []
    for label in declared_labels:
        artifact_id = label.split(".", 1)[0] if isinstance(label, str) else label
        if artifact_id not in declared:
            declared.append(artifact_id)
    # S12/S13 are accepted only through the handoff state machine; S14 is a
    # consumed authority attestation, never a caller-published artifact.
    record_outputs = [name for name in declared if name not in {"S12", "S13", "S14"}]
    raw_records = payload.get("records", [])
    if not isinstance(raw_records, list) or any(
        not isinstance(value, dict) for value in raw_records
    ):
        raise CLIError(
            "step_records_invalid",
            "records must be an array of exact governed artifact envelopes",
        )
    raw_records = _source_control_authored_records(
        records=list(raw_records), step=row["step"], workspace=workspace,
        state=state, bundle=bundle,
        subjects=platform_subjects or command_subjects, payload=payload,
    )
    if row["step"] == 3:
        if any(
            value.get("artifact_type") in {"R04", "R05", "R06"}
            for value in raw_records
        ):
            raise CLIError(
                "platform_manifest_caller_forbidden",
                "STEP03 R04/R05/R06 are derived only from authenticated data-platform S13 bytes",
                exit_code=3,
            )
        raw_records.extend(_data_platform_manifest_records(
            workspace=workspace, state=state, bundle=bundle,
            subjects=command_subjects, workspace_id=workspace_id,
            contract_records=raw_records,
        ))
    if row["step"] == 5:
        if any(
            value.get("artifact_type") in {"B02", "B03"}
            for value in raw_records
        ):
            raise CLIError(
                "baseline_evidence_caller_forbidden",
                "STEP05 B02/B03 are derived only from authenticated Git/Jenkins/ML S13 bytes",
                exit_code=3,
            )
        if raw_records:
            raise CLIError(
                "undeclared_step_output",
                "STEP05 accepts no caller-authored governed outputs",
                exit_code=3,
            )
        raw_records.extend(_baseline_platform_records(
            workspace=workspace, state=state, bundle=bundle,
            subjects=command_subjects, workspace_id=workspace_id,
            payload=payload,
        ))
    raw_input_records = payload.get("input_records", [])
    if not isinstance(raw_input_records, list) or any(
        not isinstance(value, dict) for value in raw_input_records
    ):
        raise CLIError(
            "step_input_records_invalid",
            "input_records must be an array of exact governed external-input envelopes",
        )
    import_allowed = {"S03"} if row["step"] == 1 else set()
    if any(value.get("artifact_type") not in import_allowed for value in raw_input_records):
        raise CLIError(
            "step_input_record_undeclared",
            "only STEP01 may atomically import one governed S03 planning link record",
            exit_code=3,
        )
    if len(raw_input_records) != len({
        (value.get("artifact_type"), value.get("instance_key"))
        for value in raw_input_records
    }):
        raise CLIError(
            "step_input_record_duplicate",
            "input_records contains a duplicate governed identity",
            exit_code=3,
        )
    required_outputs = _step_required_outputs(
        row=row, declared=record_outputs, raw_records=raw_records,
        state=state, workspace=workspace, bundle=bundle,
        command_subjects=command_subjects,
    )
    descriptors: list[dict[str, Any]] = []
    writes: list[tuple[str, Any, bool]] = []
    virtual = deepcopy(state)
    seen_nodes: set[str] = set()
    envelope_subjects = _step_artifact_subjects(row["step"], workspace_id, command_subjects)
    parent_nodes, parent_digests = _validated_input_refs(
        payload=payload, row=row, state=state, workspace=workspace,
        bundle=bundle,
    )
    output_object_ids = {id(value) for value in raw_records}
    for value in [*raw_records, *raw_input_records]:
        artifact_id = value.get("artifact_type")
        instance_key = value.get("instance_key")
        if (
            id(value) in output_object_ids and artifact_id not in record_outputs
        ) or (
            id(value) not in output_object_ids and artifact_id not in import_allowed
        ):
            raise CLIError(
                "undeclared_step_output",
                f"step {row['step']} cannot publish {artifact_id}",
                exit_code=3,
            )
        if not isinstance(instance_key, str) or not _PORTABLE_INSTANCE.fullmatch(instance_key):
            raise CLIError(
                "governed_record_identity_invalid",
                f"{artifact_id} requires a portable instance identity",
            )
        node = f"{artifact_id}:{instance_key}"
        if node in seen_nodes or state.get("artifacts", {}).get(artifact_id, {}).get(instance_key):
            raise CLIError(
                "artifact_overwrite",
                f"immutable step output already exists: {node}",
                exit_code=4,
            )
        actual_subjects = value.get("subjects")
        if not isinstance(actual_subjects, dict) or any(
            actual_subjects.get(key) != expected
            for key, expected in envelope_subjects.items()
        ):
            raise CLIError(
                "governed_record_subject_mismatch",
                f"{node} does not bind the exact command subjects",
                exit_code=3,
            )
        findings = bundle.validate_artifact(artifact_id, value)
        content = value.get("content", value)
        if (
            (
                (
                row["step"] == 7
                and artifact_id == "E02"
                ) or (
                row["step"] == 5
                and artifact_id in {"B02", "B03"}
                )
            )
            and isinstance(content, dict)
            and content.get("authority") == []
        ):
            # Like amendment/deviation candidates, E02 has an explicit
            # two-object authority contract: validate the unsigned candidate
            # with only the deliberately empty authority field deferred, then
            # perform full validation after consumed attestation digests are
            # injected below.
            findings = [
                item for item in findings
                if not (
                    item.get("code") == "semantic_required"
                    and item.get("json_pointer") == "/content/authority"
                )
            ]
        if findings:
            raise CLIError(
                "governed_record_invalid", findings[0]["message"],
                findings=findings,
            )
        relative = f"artifacts/{artifact_id}/{instance_key}.json"
        descriptor = {
            "artifact_id": artifact_id, "instance_key": instance_key,
            "path": relative, "digest": digest(value), "parents": [],
            "status": value.get("status", "published"),
        }
        descriptors.append(descriptor)
        writes.append((relative, value, True))
        virtual.setdefault("artifacts", {}).setdefault(artifact_id, {})[
            instance_key
        ] = {
            "path": relative, "digest": descriptor["digest"],
            "status": descriptor["status"],
        }
        seen_nodes.add(node)

    missing_outputs: list[str] = []
    for artifact_id in sorted(required_outputs):
        new_present = any(
            item["artifact_id"] == artifact_id for item in descriptors
        )
        stored_present = bool(_matching_stored_artifacts(
            state=state, workspace=workspace, bundle=bundle,
            artifact_id=artifact_id, subjects=envelope_subjects,
        ))
        if not new_present and not stored_present:
            missing_outputs.append(artifact_id)
    if missing_outputs:
        raise CLIError(
            "step_output_unmaterialized",
            "earliest lifecycle row lacks exact validated outputs: "
            + ", ".join(sorted(missing_outputs)),
            exit_code=3,
            findings=[
                finding(
                    "step_output_unmaterialized",
                    f"exact declared output is absent: {artifact_id}",
                    subject=f"STEP{row['step']:02d}:{artifact_id}",
                    governing_asset="lifecycle-steps.json",
                )
                for artifact_id in sorted(missing_outputs)
            ],
        )
    descriptor_digests = {
        f"{item['artifact_id']}:{item['instance_key']}": item["digest"]
        for item in descriptors
    }
    if row["step"] == 1:
        s01_nodes = sorted(
            node for node in descriptor_digests if node.startswith("S01:")
        )
        if len(s01_nodes) != 1:
            raise CLIError(
                "product_requirement_identity_invalid",
                "STEP01 must materialize exactly one S01 root",
                exit_code=3,
            )
    for descriptor in descriptors:
        node = f"{descriptor['artifact_id']}:{descriptor['instance_key']}"
        if row["step"] == 1 and descriptor["artifact_id"] == "S01":
            exact_parents: list[str] = []
            exact_parent_digests: dict[str, str] = {}
        elif row["step"] == 1 and descriptor["artifact_id"] == "S03":
            exact_parents = s01_nodes
            exact_parent_digests = {
                parent: descriptor_digests[parent] for parent in exact_parents
            }
        else:
            exact_parents = sorted(set(parent_nodes) - {node})
            exact_parent_digests = {
                parent: parent_digests[parent] for parent in exact_parents
            }
        descriptor["parents"] = exact_parents
        value = next(
            data for relative, data, _ in writes
            if relative == descriptor["path"]
        )
        content = value.get("content", value)
        if (
            exact_parents
            and _content_lineage_binding_required(bundle, descriptor["artifact_id"])
            and (
                not isinstance(content, dict)
                or content.get("source_refs") != exact_parent_digests
            )
        ):
            raise CLIError(
                "step_output_lineage_mismatch",
                f"{node} content.source_refs must bind every exact required input identity and digest",
                exit_code=3,
            )
    values_by_type: dict[str, list[dict[str, Any]]] = {}
    for _, value, _ in writes:
        values_by_type.setdefault(value["artifact_type"], []).append(value)
    if row["step"] == 3:
        nodes_by_type = {
            artifact_id: [
                f"{artifact_id}:{value['instance_key']}" for value in rows
            ]
            for artifact_id, rows in values_by_type.items()
        }
        manifest_parents = {
            "R04": ("G02", "G03"),
            "R05": ("G02", "G03", "R04"),
            "R06": ("G02", "G05", "R04"),
        }
        for descriptor in descriptors:
            parent_types = manifest_parents.get(descriptor["artifact_id"])
            if parent_types is None:
                continue
            descriptor["parents"] = sorted(set(descriptor["parents"]) | {
                node for artifact_id in parent_types
                for node in nodes_by_type.get(artifact_id, [])
            })
    if row["step"] == 6:
        experiment_rows = values_by_type.get("E01", [])
        run_plan_rows = values_by_type.get("E04", [])
        if len(experiment_rows) != 1 or len(run_plan_rows) != 1:
            raise CLIError(
                "experiment_run_plan_binding_missing",
                "STEP06 requires exactly one E01 candidate and one E04 Run plan",
                exit_code=3,
            )
        experiment_content = experiment_rows[0].get(
            "content", experiment_rows[0],
        )
        run_plan_content = run_plan_rows[0].get(
            "content", run_plan_rows[0],
        )
        candidate_spec_digest = candidate_digest(experiment_content)
        if (
            not isinstance(experiment_content, dict)
            or not isinstance(run_plan_content, dict)
            or run_plan_content.get("candidate_spec_digest")
            != candidate_spec_digest
            or run_plan_content.get("frozen_spec_digest") is not None
            or run_plan_content.get("experiment_id")
            != experiment_content.get("experiment_id")
            or (
                experiment_content.get("run_plan_id") is not None
                and experiment_content.get("run_plan_id")
                != run_plan_content.get("run_plan_id")
            )
        ):
            raise CLIError(
                "run_plan_candidate_binding_mismatch",
                "E04 must bind the stable E01 candidate_spec_digest and must not pre-bind a future frozen envelope digest",
                exit_code=3,
            )
        anchors = _validated_experiment_anchor_context(
            content=experiment_content, state=state, workspace=workspace,
            bundle=bundle,
        )
        runs = run_plan_content.get("runs")
        if not isinstance(runs, list) or not runs:
            raise CLIError(
                "run_plan_empty", "E04 must enumerate exact immutable Runs",
                exit_code=3,
            )
        for run in runs:
            if not isinstance(run, dict) or any(
                run.get(name) != anchors[name] for name in (
                    "dataset_manifest_digest", "split_manifest_digest",
                    "data_digest", "split_digest", "evaluator_digest",
                    "calibration_applicable",
                )
            ) or anchors["calibration_applicable"] and any(
                run.get(name) != anchors[name] for name in (
                    "calibration_manifest_digest", "calibration_digest",
                )
            ):
                raise CLIError(
                    "run_plan_scientific_anchor_mismatch",
                    "every E04 Run must exactly repeat E01's R04/R05/R06/G10 immutable anchors",
                    exit_code=3,
                )
    virtual.setdefault("checks", {}).update(
        _semantic_step_checks(row["step"], values_by_type)
    )
    return descriptors, writes, virtual


_FREEZE_READINESS_ARTIFACTS = ("E01", "E02", "E04", "S04")
_FREEZE_READINESS_OPERATIONS = (
    "data.quality-check", "ml.contract-preflight",
)


def _single_step_selector(
    payload: dict[str, Any], artifact_id: str,
) -> tuple[str, str]:
    selectors = payload.get("input_refs", {}).get(artifact_id)
    if isinstance(selectors, dict):
        selectors = [selectors]
    if not isinstance(selectors, list) or len(selectors) != 1:
        raise CLIError(
            "freeze_input_identity_ambiguous",
            f"STEP07 requires exactly one {artifact_id} input identity",
            exit_code=3,
        )
    selector = selectors[0]
    if not isinstance(selector, dict) or set(selector) != {
        "instance_key", "digest",
    } or not isinstance(selector.get("instance_key"), str) \
            or not isinstance(selector.get("digest"), str):
        raise CLIError(
            "freeze_input_identity_invalid",
            f"STEP07 {artifact_id} selector is not exact",
            exit_code=3,
        )
    return selector["instance_key"], selector["digest"]


def _single_finalized_step_output(
    artifact_id: str,
    descriptors: list[dict[str, Any]],
    writes: list[tuple[str, Any, bool]],
) -> tuple[str, dict[str, Any], str]:
    matched = [
        descriptor for descriptor in descriptors
        if descriptor.get("artifact_id") == artifact_id
    ]
    if len(matched) != 1:
        raise CLIError(
            "freeze_output_identity_ambiguous",
            f"STEP07 must finalize exactly one {artifact_id}",
            exit_code=3,
        )
    descriptor = matched[0]
    values = [
        value for relative, value, _ in writes
        if relative == descriptor.get("path")
    ]
    if len(values) != 1 or digest(values[0]) != descriptor.get("digest"):
        raise CLIError(
            "freeze_output_digest_mismatch",
            f"STEP07 finalized {artifact_id} bytes do not equal its descriptor",
            exit_code=3,
        )
    return descriptor["instance_key"], values[0], descriptor["digest"]


def _step07_freeze_readiness_payload(
    *,
    workspace: str,
    payload: dict[str, Any],
    state: dict[str, Any],
    bundle: AssetBundle,
    subjects: dict[str, str],
    descriptors: list[dict[str, Any]],
    writes: list[tuple[str, Any, bool]],
) -> dict[str, Any]:
    """Persist the exact byte closure STEP08 must independently re-open."""

    artifact_instances: dict[str, str] = {}
    input_values: dict[str, dict[str, Any]] = {}
    input_digests: dict[str, str] = {}
    selected_inputs = payload.get("input_refs", {})
    if not isinstance(selected_inputs, dict):
        raise CLIError(
            "freeze_input_identity_invalid",
            "STEP07 input_refs must be one exact source-closure map",
            exit_code=3,
        )
    for artifact_id in sorted(selected_inputs):
        instance_key, selected_digest = _single_step_selector(
            payload, artifact_id,
        )
        value, record = _validated_stored_artifact(
            state, workspace, bundle, artifact_id, instance_key,
        )
        if record.get("digest") != selected_digest:
            raise CLIError(
                "freeze_input_digest_mismatch",
                f"STEP07 {artifact_id}:{instance_key} no longer equals its selector",
                exit_code=3,
            )
        artifact_instances[artifact_id] = instance_key
        input_values[artifact_id] = value
        input_digests[f"{artifact_id}:{instance_key}"] = selected_digest

    output_values: dict[str, dict[str, Any]] = {}
    for artifact_id in ("E02", "S04"):
        instance_key, value, output_digest = _single_finalized_step_output(
            artifact_id, descriptors, writes,
        )
        artifact_instances[artifact_id] = instance_key
        output_values[artifact_id] = value
        input_digests[f"{artifact_id}:{instance_key}"] = output_digest

    experiment_content = input_values["E01"].get(
        "content", input_values["E01"],
    )
    run_plan_content = input_values["E04"].get(
        "content", input_values["E04"],
    )
    readiness_content = output_values["E02"].get(
        "content", output_values["E02"],
    )
    candidate_spec_digest = candidate_digest(experiment_content)
    budget = experiment_content.get("budget") \
        if isinstance(experiment_content, dict) else None
    if (
        budget in (None, [], {})
        or not isinstance(run_plan_content, dict)
        or run_plan_content.get("candidate_spec_digest")
        != candidate_spec_digest
        or not isinstance(readiness_content, dict)
        or readiness_content.get("candidate_digest")
        != candidate_spec_digest
    ):
        raise CLIError(
            "freeze_candidate_binding_mismatch",
            "STEP07 E01, E02, E04, and the frozen budget do not share one exact candidate binding",
            exit_code=3,
        )
    budget_digest = digest(budget)
    input_digests[
        f"E01:{artifact_instances['E01']}#candidate_spec"
    ] = candidate_spec_digest
    input_digests[
        f"E01:{artifact_instances['E01']}#budget"
    ] = budget_digest

    anchors = _validated_experiment_anchor_context(
        content=experiment_content, state=state, workspace=workspace,
        bundle=bundle,
    )
    if run_plan_content.get("source_refs") != anchors["source_refs"]:
        raise CLIError(
            "freeze_run_plan_source_mismatch",
            "STEP07 E04 source_refs differ from the exact E01 source closure",
            exit_code=3,
        )

    packets = _exact_handoff_packets(
        workspace, state=state, bundle=bundle,
        operations=list(_FREEZE_READINESS_OPERATIONS),
        gate="lifecycle-step-7", step=7, subjects=subjects,
    )
    platform_requests: dict[str, str] = {}
    for operation in _FREEZE_READINESS_OPERATIONS:
        request, result, record = packets[operation]
        request_id = request["request_id"]
        if record.get("request_digest") != digest(request) \
                or record.get("result_digest") != digest(result):
            raise CLIError(
                "freeze_platform_receipt_mismatch",
                f"STEP07 {operation} packets do not equal replayed handoff bytes",
                exit_code=3,
            )
        platform_requests[operation] = request_id
        input_digests[f"S12:{request_id}"] = digest(request)
        input_digests[f"S13:{request_id}"] = digest(result)

    data_result = packets["data.quality-check"][1]
    data_required = set(bundle.operation_contract(
        "data.quality-check",
    )[1]["required_evidence"])
    data_values = _evidence_values(
        "data.quality-check", data_result, data_required,
    )
    expected_calibration = (
        anchors["calibration_manifest_digest"]
        if anchors["calibration_applicable"] else "not-applicable"
    )
    if (
        data_values.get("dataset_manifest_digest")
        != anchors["dataset_manifest_digest"]
        or data_values.get("split_manifest_digest")
        != anchors["split_manifest_digest"]
        or data_values.get("calibration_manifest_digest_when_applicable")
        != expected_calibration
        or data_values.get("evaluator_digest") != anchors["evaluator_digest"]
        or data_values.get("baseline_lock_digest")
        != anchors["baseline_lock_digest"]
        or data_values.get("content_digest") != anchors["dataset_digest"]
    ):
        raise CLIError(
            "freeze_data_receipt_binding_mismatch",
            "STEP07 data-platform S13 does not exactly repeat E01's R04/R05/R06/G10/B02 anchors",
            exit_code=3,
        )
    jenkins_result = packets["ml.contract-preflight"][1]
    jenkins_required = set(bundle.operation_contract(
        "ml.contract-preflight",
    )[1]["required_evidence"])
    jenkins_values = _evidence_values(
        "ml.contract-preflight", jenkins_result, jenkins_required,
    )
    if jenkins_values.get("contract_digests") != anchors["contracts"]:
        raise CLIError(
            "freeze_contract_preflight_binding_mismatch",
            "STEP07 Jenkins S13 contract_digests must equal E01's exact selected G contract closure",
            exit_code=3,
        )

    return {
        "candidate_spec_digest": candidate_spec_digest,
        "budget_digest": budget_digest,
        "freeze_input_instances": artifact_instances,
        "freeze_platform_requests": platform_requests,
        "freeze_input_digests": dict(sorted(input_digests.items())),
        "freeze_inputs_digest": digest(input_digests),
    }


def _verified_freeze_readiness(
    *, workspace: str, replayed: Any, state: dict[str, Any],
    bundle: AssetBundle, experiment_id: str,
) -> dict[str, Any]:
    """Re-open and recompute the persisted STEP07 freeze-input closure."""

    events = [
        event for event in replayed.events
        if event.get("event_type") == "readiness.approved"
        and event.get("subjects", {}).get("experiment_id") == experiment_id
        and event.get("payload", {}).get("step") == 7
        and event.get("payload", {}).get("lifecycle_step_completion") is True
    ]
    if len(events) != 1:
        raise CLIError(
            "freeze_readiness_event_missing",
            "STEP08 requires exactly one terminal STEP07 readiness.approved event",
            exit_code=3,
        )
    event = events[0]
    persisted = event.get("payload", {})
    instances = persisted.get("freeze_input_instances")
    platform_requests = persisted.get("freeze_platform_requests")
    expected_digests = persisted.get("freeze_input_digests")
    if (
        not isinstance(instances, dict)
        or not set(_FREEZE_READINESS_ARTIFACTS).issubset(instances)
        or not isinstance(platform_requests, dict)
        or set(platform_requests) != set(_FREEZE_READINESS_OPERATIONS)
        or not isinstance(expected_digests, dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in expected_digests.items()
        )
    ):
        raise CLIError(
            "freeze_readiness_binding_invalid",
            "STEP07 did not persist the exact freeze input identity map",
            exit_code=3,
        )

    selected_e01, _ = _validated_stored_artifact(
        state, workspace, bundle, "E01", instances["E01"],
    )
    selected_source_refs = selected_e01.get("content", selected_e01).get(
        "source_refs",
    )
    expected_artifacts = set(_FREEZE_READINESS_ARTIFACTS)
    if isinstance(selected_source_refs, dict):
        expected_artifacts.update(
            node.split(":", 1)[0] for node in selected_source_refs
        )
    if not isinstance(selected_source_refs, dict) or set(instances) != expected_artifacts:
        raise CLIError(
            "freeze_readiness_binding_invalid",
            "STEP07 freeze identity map does not exactly equal E01's immutable source closure",
            exit_code=3,
        )

    values: dict[str, dict[str, Any]] = {}
    recomputed: dict[str, str] = {}
    for artifact_id in sorted(instances):
        instance_key = instances[artifact_id]
        if not isinstance(instance_key, str):
            raise CLIError(
                "freeze_readiness_binding_invalid",
                f"STEP07 {artifact_id} identity is not portable",
                exit_code=3,
            )
        value, record = _validated_stored_artifact(
            state, workspace, bundle, artifact_id, instance_key,
        )
        values[artifact_id] = value
        recomputed[f"{artifact_id}:{instance_key}"] = record["digest"]

    experiment_content = values["E01"].get("content", values["E01"])
    run_plan_content = values["E04"].get("content", values["E04"])
    readiness_content = values["E02"].get("content", values["E02"])
    anchors = _validated_experiment_anchor_context(
        content=experiment_content, state=state, workspace=workspace,
        bundle=bundle,
    )
    if run_plan_content.get("source_refs") != anchors["source_refs"]:
        raise CLIError(
            "freeze_readiness_digest_mismatch",
            "STEP08 observed E04 source closure differing from E01",
            exit_code=3,
        )
    candidate_spec_digest = candidate_digest(experiment_content)
    budget = experiment_content.get("budget") \
        if isinstance(experiment_content, dict) else None
    budget_digest = digest(budget)
    recomputed[f"E01:{instances['E01']}#candidate_spec"] = \
        candidate_spec_digest
    recomputed[f"E01:{instances['E01']}#budget"] = budget_digest

    command_subjects = {
        key: value for key, value in event.get("subjects", {}).items()
        if key != "workspace_id"
    }
    # STEP07 persists only lifecycle subjects on the terminal event, while its
    # S12 packets additionally bind the immutable scientific anchors derived
    # from E01.  Reconstruct that exact coordinator-owned subject rather than
    # weakening packet lookup to an experiment-only summary.
    command_subjects.update({
        "dataset_manifest_digest": anchors["dataset_manifest_digest"],
        "split_manifest_digest": anchors["split_manifest_digest"],
        "calibration_manifest_digest_when_applicable": (
            anchors["calibration_manifest_digest"]
            if anchors["calibration_applicable"] else "not-applicable"
        ),
        "evaluator_digest": anchors["evaluator_digest"],
        "baseline_lock_digest": anchors["baseline_lock_digest"],
    })
    packets = _exact_handoff_packets(
        workspace, state=state, bundle=bundle,
        operations=list(_FREEZE_READINESS_OPERATIONS),
        gate="lifecycle-step-7", step=7, subjects=command_subjects,
    )
    for operation in _FREEZE_READINESS_OPERATIONS:
        request, result, _ = packets[operation]
        request_id = request["request_id"]
        if platform_requests.get(operation) != request_id:
            raise CLIError(
                "freeze_platform_receipt_substituted",
                f"STEP08 observed a substituted {operation} request",
                exit_code=3,
            )
        recomputed[f"S12:{request_id}"] = digest(request)
        recomputed[f"S13:{request_id}"] = digest(result)
    data_required = set(bundle.operation_contract(
        "data.quality-check",
    )[1]["required_evidence"])
    data_values = _evidence_values(
        "data.quality-check", packets["data.quality-check"][1], data_required,
    )
    expected_calibration = anchors["calibration_manifest_digest"] \
        if anchors["calibration_applicable"] else "not-applicable"
    if any((
        data_values.get("dataset_manifest_digest")
        != anchors["dataset_manifest_digest"],
        data_values.get("split_manifest_digest")
        != anchors["split_manifest_digest"],
        data_values.get("calibration_manifest_digest_when_applicable")
        != expected_calibration,
        data_values.get("evaluator_digest") != anchors["evaluator_digest"],
        data_values.get("baseline_lock_digest")
        != anchors["baseline_lock_digest"],
    )):
        raise CLIError(
            "freeze_data_receipt_binding_mismatch",
            "STEP08 re-opened data S13 differs from exact frozen scientific anchors",
            exit_code=3,
        )

    recomputed = dict(sorted(recomputed.items()))
    if (
        expected_digests != recomputed
        or persisted.get("freeze_inputs_digest") != digest(recomputed)
        or persisted.get("candidate_spec_digest") != candidate_spec_digest
        or persisted.get("budget_digest") != budget_digest
        or not isinstance(run_plan_content, dict)
        or run_plan_content.get("candidate_spec_digest")
        != candidate_spec_digest
        or not isinstance(readiness_content, dict)
        or readiness_content.get("candidate_digest")
        != candidate_spec_digest
    ):
        raise CLIError(
            "freeze_readiness_digest_mismatch",
            "STEP08 recomputed bytes do not equal the terminal STEP07 freeze-input closure",
            exit_code=3,
        )
    return {
        "candidate_spec_digest": candidate_spec_digest,
        "freeze_inputs_digest": digest(recomputed),
        "provenance_nodes": sorted(
            (artifact_id, instance_key)
            for artifact_id, instance_key in instances.items()
            if artifact_id in {"E01", "E03", "E04", "E06", "E07"}
        ),
        "checks": {
            "candidate-digest-exact": True,
            "B03-current": True,
            "data-split-evaluator-budget-exact": True,
        },
    }


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


_STEP_DERIVED_CHECKS: dict[int, tuple[str, ...]] = {
    1: ("problem.clear", "non_goals.explicit"),
    2: (
        "hypothesis.falsifiable", "target_not_must", "mechanism.explicit",
        "uncertainty.explicit",
    ),
    3: (
        "contracts.all_applicable_semantics_closed",
        "all-applicable-G-contracts", "exact-successful-platform-results",
    ),
    4: ("baseline.scope_exact", "baseline.version_selected"),
    5: (
        "baseline.receipts_exact", "baseline.tolerance_pass",
        "baseline.runtime_environment_exact", "authority.baseline_lock",
        "tolerance-pass",
    ),
    6: ("experiment.schema", "variables.isolated", "E03.complete", "true"),
    7: (
        "planning_readiness.trio_complete",
        "protocol_readiness.all_blocking_gates_pass", "authority.readiness",
        "E01-candidate-digest",
    ),
    9: ("stories.deterministic_acceptance", "context.allowed_paths"),
    10: ("coding.allowed_paths_only", "S06-contained-context", "allowed-paths"),
    11: ("review.owner_approvals",),
    12: ("deterministic_acceptance.all_pass", "all-deterministic-ACs"),
    13: (
        "preflight.geometry_tensor_finite_tiny_resume", "S08-exact-revision",
    ),
    19: (
        "authority.reviewer_independent", "deviation.none_unresolved",
        "five_lenses.complete", "independent-reviewer", "five-lenses",
        "no-unresolved-deviation",
    ),
    8: ("freeze.content_digest_and_current_receipts", "anchor.result_exact"),
    14: (
        "run.fingerprints_exact", "run.references_exact", "run.budget_exact",
        "attempt.planned",
    ),
    15: (
        "hpo.every_trial_retained", "hpo.selection_replayable",
        "hpo.formal_test_isolated", "hpo.not_applicable",
        "confirmation.selected_config_replayed",
        "confirmation.independent_seed_environment",
        "attempt.environment_matches_R01", "attempt.terminal",
    ),
    16: ("evaluator.digest_exact", "attempt.environment_matches_R01"),
    17: ("robustness.pass_or_fail_retained", "robustness.no_result_editing"),
    18: (
        "evidence.complete_attempt_accounting", "evidence.deviations_accounted",
        "evidence.receipts_exact",
    ),
    20: (
        "decision.all_attempts_accounted",
        "decision.rule_exhaustive_mutually_exclusive",
        "decision.precedence_invalid_inconclusive_rejected_supported",
        "authority.D05", "anchor.result_exact",
    ),
    21: ("successor.reciprocal_links", "predecessor.protocol_unchanged"),

}


def _derived_step_checks(
    *, step: int, row: dict[str, Any], exact_results: dict[str, str],
    authority_action: str | None, authority: list[dict[str, Any]],
) -> dict[str, bool]:
    if step in {1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 19}:
        authority_checks = {
            5: "authority.baseline_lock",
            7: "authority.readiness",
            19: "authority.reviewer_independent",
        }
        name = authority_checks.get(step)
        return {name: True} if name and authority_action and authority else {}
    declared = _STEP_DERIVED_CHECKS.get(step)
    if declared is None:
        return {}
    operations = [
        item["name"] if isinstance(item, dict) else item
        for item in row.get("platform_operations", [])
    ]
    if any(exact_results.get(name) != "successful" for name in operations):
        return {}
    if authority_action and not authority:
        return {}
    # Reaching this function means every non-handoff declared output and every
    # matched prerequisite was read from the ledger and passed its pinned JSON
    # schema plus semantic validator in _validated_step_records.
    return {name: True for name in declared}


def _anchor_evidence(result: dict[str, Any]) -> dict[str, Any]:
    evidence = result.get("evidence")
    if not isinstance(evidence, list):
        return {}
    values: dict[str, Any] = {}
    for item in evidence:
        if isinstance(item, dict) and isinstance(item.get("type"), str):
            values[item["type"]] = item.get("value")
    return values


def _validated_persisted_anchor_result(
    workspace: str,
    path: str | None,
    *,
    state: dict[str, Any],
    request_id: str,
    provisional_hash: str,
    anchor_operation_id: str,
) -> dict[str, Any]:
    if not path:
        raise CLIError(
            "external_anchor_required",
            "the exact persisted git.anchor-ledger-head S13 is required",
            exit_code=3,
        )
    expected_path = os.path.abspath(os.path.join(
        workspace, "packets", "S13", f"{request_id}.json",
    ))
    if os.path.abspath(path) != expected_path:
        raise CLIError(
            "anchor_result_unbound",
            "trusted anchor result must be the exact accepted workspace S13",
            exit_code=3,
        )
    result = _secure_packet_document(
        workspace, ("packets", "S13", f"{request_id}.json"),
        packet_kind="anchor S13",
    )
    record = state.get("handoffs", {}).get(request_id, {})
    if (
        not isinstance(result, dict)
        or record.get("status") not in {"successful", "reconciled_effect"}
        or record.get("result_digest") != digest(result)
    ):
        raise CLIError(
            "anchor_result_unaccepted",
            "anchor S13 is not the accepted terminal result for its request",
            exit_code=3,
        )
    evidence = _anchor_evidence(result)
    if (
        result.get("request_id") != request_id
        or result.get("operation") != "git.anchor-ledger-head"
        or result.get("terminal_status") != "successful"
        or evidence.get("bound_provisional_ledger_head") != provisional_hash
        or evidence.get("operation_id") != anchor_operation_id
    ):
        raise CLIError(
            "anchor_result_mismatch",
            "anchor acceptance requires the exact successful S13 over the provisional head and operation",
            exit_code=3,
        )
    return result


def _find_anchor_phase(
    replayed: Any,
    *,
    provisional_type: str,
    terminal_type: str,
    logical_operation_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    provisional = next((
        event for event in reversed(replayed.events)
        if event["event_type"] == provisional_type
        and event.get("payload", {}).get("logical_operation_id")
        == logical_operation_id
    ), None)
    if provisional is None:
        return None, None, None
    accepted = next((
        event for event in replayed.events
        if event["sequence"] > provisional["sequence"]
        and event["event_type"] == "anchor.accepted"
        and event.get("payload", {}).get("provisional_hash")
        == provisional["event_hash"]
    ), None)
    terminal = next((
        event for event in replayed.events
        if event["sequence"] > provisional["sequence"]
        and event["event_type"] == terminal_type
        and event.get("payload", {}).get("provisional_hash")
        == provisional["event_hash"]
        and event.get("payload", {}).get("logical_operation_id")
        == logical_operation_id
    ), None)
    return provisional, accepted, terminal


def _anchor_wait_result(
    command: str,
    revision: int,
    provisional: dict[str, Any],
    requests: list[dict[str, Any]],
) -> tuple[dict[str, Any], int]:
    blocked = finding(
        "external_anchor_required",
        "transition remains provisional until the exact external head-anchor S13 is accepted",
        subject=provisional["subjects"].get("workspace_id", "workspace"),
        governing_asset="transition-requirements.json",
    )
    return _result(
        command,
        revision,
        {
            "provisional_head": provisional["event_hash"],
            "anchor_request_id": provisional["payload"]["anchor_request_id"],
            "handoffs": requests,
            "disposition": "anchor_required",
        },
        [blocked],
        status="blocked",
    ), 3


def _do_anchored_lifecycle(
    args: argparse.Namespace,
    *,
    payload: dict[str, Any],
    replayed: Any,
    state: dict[str, Any],
    bundle: AssetBundle,
    action: str,
) -> tuple[dict[str, Any], int]:
    command = _command(args)
    workspace_id = replayed.genesis["workspace_id"]
    logical_operation_id = _stable_lifecycle_operation_id(args, payload)
    intent_digest = digest(payload)
    authority_extra = payload.get("authority_subject", {})
    if (
        not isinstance(authority_extra, dict)
        or {"governed_ids", "digests"}.intersection(authority_extra)
    ):
        raise CLIError(
            "authority_subject_override",
            "authority_subject cannot replace governed IDs or digests",
            exit_code=2,
        )
    if action == "archive":
        provisional_type = "lifecycle.archive-provisional"
        terminal_type = "lifecycle.archived"
        prerequisite_name = "lifecycle.archive-provisional"
        terminal_transition_name = "lifecycle.archive"
    else:
        provisional_type = "closure.provisional"
        terminal_type = "lifecycle.closed"
        prerequisite_name = "lifecycle.close-provisional"
        terminal_transition_name = "lifecycle.close"

    provisional, accepted, terminal = _find_anchor_phase(
        replayed,
        provisional_type=provisional_type,
        terminal_type=terminal_type,
        logical_operation_id=logical_operation_id,
    )
    if terminal is not None:
        return _result(command, replayed.revision, {
            "event_hash": terminal["event_hash"],
            "event_type": terminal_type,
            "idempotent": True,
        }), 0

    if provisional is None:
        if getattr(args, "trusted_anchor_result", None):
            raise CLIError(
                "anchor_result_without_provisional",
                "an anchor result cannot be accepted before a matching provisional event",
                exit_code=3,
            )
        subjects = _subjects(payload, workspace_id)
        context = deepcopy(state)
        artifact_writes: tuple[tuple[str, Any, bool], ...] = ()
        published: list[dict[str, Any]] = []
        closure_id: str | None = None
        closure_instance_key: str | None = None
        if action == "archive":
            _reject_governed_payload_claims(
                payload,
                allowed={
                    "record", "subjects", "attestations",
                    "authority_subject",
                },
            )
            if (
                state.get("dimensions", {}).get("lifecycle") != "cancelled"
                and (
                    state.get("lifecycle_step") not in {21, 22}
                    or state.get("dimensions", {}).get("scientific") not in {
                        "hypothesis_supported", "hypothesis_rejected",
                        "inconclusive", "invalid",
                    }
                )
            ):
                raise CLIError(
                    "archive_source_invalid",
                    "archive is available only at governed STEP21/terminal scientific disposition or after cancellation",
                    exit_code=3,
                )
            record = payload.get("record")
            if not isinstance(record, dict):
                raise CLIError(
                    "closure_record_required",
                    "archive requires exact unsigned S17 bytes",
                    exit_code=3,
                )
            content = record.get("content")
            if (
                record.get("artifact_type") != "S17"
                or not isinstance(content, dict)
                or content.get("authority") != []
                or record.get("subjects")
                != {"workspace_id": workspace_id, **subjects}
            ):
                raise CLIError(
                    "closure_record_invalid",
                    "S17 must be an exact unsigned candidate bound to command subjects",
                    exit_code=3,
                )
            candidate_findings = bundle.validate_artifact("S17", record)
            candidate_findings = [
                item for item in candidate_findings
                if not (
                    item.get("code") == "semantic_required"
                    and item.get("json_pointer") == "/content/authority"
                )
            ]
            if candidate_findings:
                raise CLIError(
                    "closure_record_invalid",
                    candidate_findings[0]["message"],
                    findings=candidate_findings,
                )
            open_statuses = {
                "created", "dispatched", "failed",
                "reconciliation_required", "configuration_required",
            }
            open_handoffs = sorted(
                request_id for request_id, handoff
                in state.get("handoffs", {}).items()
                if handoff.get("status") in open_statuses
            )
            exact_child_accounting = sorted(
                f"{request_id}:{handoff.get('status')}"
                for request_id, handoff in state.get("handoffs", {}).items()
            ) or ["none"]
            exact_open = open_handoffs or ["none"]
            exact_terminal_subjects = sorted(subjects.values()) or [workspace_id]
            source_refs: dict[str, str] = {}
            for artifact_id, instances in sorted(
                state.get("artifacts", {}).items(),
            ):
                for instance_key in sorted(instances):
                    _, stored_record = _validated_stored_artifact(
                        state, args.workspace, bundle,
                        artifact_id, instance_key,
                    )
                    source_refs[f"{artifact_id}:{instance_key}"] = \
                        stored_record["digest"]
            expected_anchor = f"provisional:{replayed.head_hash}"
            if (
                content.get("terminal_subjects") != exact_terminal_subjects
                or content.get("child_accounting") != exact_child_accounting
                or content.get("open_handoffs") != exact_open
                or content.get("anchor") != expected_anchor
                or content.get("source_refs") != source_refs
                or open_handoffs
            ):
                raise CLIError(
                    "closure_accounting_mismatch",
                    "S17 must exactly account every child, source artifact, and provisional head",
                    exit_code=3,
                )
            graph_findings = validate_graph(state.get("lineage", {}))
            if graph_findings:
                raise CLIError(
                    graph_findings[0]["code"], graph_findings[0]["message"],
                    findings=graph_findings, exit_code=3,
                )
            instance_key = record.get("instance_key")
            if (
                not isinstance(instance_key, str)
                or not _PORTABLE_INSTANCE.fullmatch(instance_key)
                or state.get("artifacts", {}).get("S17", {}).get(instance_key)
            ):
                raise CLIError(
                    "closure_record_identity_invalid",
                    "S17 requires one new portable immutable identity",
                    exit_code=3,
                )
            closure_id = str(content.get("closure_id"))
            closure_instance_key = instance_key
            candidate_digest_value = digest(record)
            subject = {
                "governed_ids": {
                    "workspace_id": workspace_id,
                    **subjects,
                    "closure_id": str(content.get("closure_id")),
                },
                "digests": {"S17_candidate_digest": candidate_digest_value},
                **authority_extra,
            }
            authority = _authority(
                payload, action="lifecycle.close", subject=subject,
                revision=replayed.revision, bundle=bundle,
                replayed=replayed,
            )
            final_record = deepcopy(record)
            final_content = deepcopy(content)
            final_content["authority"] = sorted(
                item["attestation_digest"] for item in authority
            )
            final_record["content"] = final_content
            final_record["content_digest"] = digest(final_content)
            final_findings = bundle.validate_artifact("S17", final_record)
            if final_findings:
                raise CLIError(
                    "closure_record_invalid", final_findings[0]["message"],
                    findings=final_findings,
                )
            relative = f"artifacts/S17/{instance_key}.json"
            descriptor = {
                "artifact_id": "S17", "instance_key": instance_key,
                "path": relative, "digest": digest(final_record),
                "parents": sorted(source_refs), "status": "published",
            }
            published = [descriptor]
            artifact_writes = ((relative, final_record, True),)
            context = _virtual_artifact_context(
                state, "S17", instance_key, final_record,
                {
                    "all-children-accounted": True,
                    "T01-complete": True,
                    "no-open-reconciliation": True,
                },
            )
        else:
            _reject_governed_payload_claims(
                payload,
                allowed={
                    "closure_instance_key", "subjects", "attestations",
                    "authority_subject",
                },
            )
            if state.get("dimensions", {}).get("lifecycle") != "archived":
                raise CLIError(
                    "close_source_invalid",
                    "close requires an exact prior lifecycle.archived state",
                    exit_code=3,
                )
            closure_key = payload.get("closure_instance_key")
            if not isinstance(closure_key, str):
                raise CLIError(
                    "closure_record_required",
                    "close must pin the exact archived S17 identity",
                    exit_code=3,
                )
            _, closure_record = _validated_stored_artifact(
                state, args.workspace, bundle, "S17", closure_key,
            )
            closure_instance_key = closure_key
            context.setdefault("checks", {}).update({
                "current-trusted-anchor": bool(state.get("trusted_anchor")),
                "terminal-history-replayable": True,
            })
            subject = {
                "governed_ids": {
                    "workspace_id": workspace_id, **subjects,
                    "closure_instance_key": closure_key,
                },
                "digests": {"S17_digest": closure_record["digest"]},
                **authority_extra,
            }
            authority = _authority(
                payload, action="lifecycle.close", subject=subject,
                revision=replayed.revision, bundle=bundle,
                replayed=replayed,
            )
        prerequisite = bundle.transition(prerequisite_name)
        passed, missing, _ = _transition_gate(
            prerequisite_name, prerequisite, context,
        )
        if not passed:
            blocked = finding(
                "transition_unsatisfied",
                "pinned transition prerequisites are unsatisfied",
                subject=prerequisite_name,
                governing_asset="transition-requirements.json",
            )
            return _result(
                command, replayed.revision,
                {"valid": False, "missing": missing}, [blocked],
                status="blocked",
            ), 3
        anchor_operation_id = _phase_operation_id(
            logical_operation_id, f"{action}-anchor",
        )
        anchor_request_seed = {
            "workspace_id": workspace_id,
            "action": action,
            "logical_operation_id": logical_operation_id,
            "intent_digest": intent_digest,
        }
        request_id = "REQ-" + digest({
            **anchor_request_seed,
            "operation": "git.anchor-ledger-head",
        }).split(":", 1)[1][:32]
        provisional_mutation = mutate(
            args.workspace,
            expected_revision=args.expected_revision,
            operation_id=logical_operation_id,
            event_type=provisional_type,
            subjects={"workspace_id": workspace_id, **payload.get("subjects", {})},
            payload={
                "transition": prerequisite_name,
                "logical_operation_id": logical_operation_id,
                "intent_digest": intent_digest,
                "anchor_operation_id": anchor_operation_id,
                "anchor_request_id": request_id,
                "authority_action": "lifecycle.close",
                "authority_subject": subject,
                "authority_expected_revision": replayed.revision,
                "consumed_at": authority[0]["consumed_at"],
                "published_artifacts": published,
                "authority_bound_artifacts_digest": digest(published),
                "closure_id": closure_id,
                "closure_instance_key": closure_instance_key,
                "final_record_digest": (
                    published[0]["digest"] if published else None
                ),
                "authority_bound_record_digest": next(
                    (
                        value for key, value
                        in subject.get("digests", {}).items()
                        if key in {"S17_candidate_digest", "S17_digest"}
                    ),
                    None,
                ),
            },
            authority=authority,
            artifact_writes=artifact_writes,
        )
        _, requests = _ensure_platform_requests(
            args.workspace,
            replayed=replayed,
            state=provisional_mutation.state,
            bundle=bundle,
            operations=["git.anchor-ledger-head"],
            gate=terminal_transition_name,
            step=(
                state.get("lifecycle_step")
                if isinstance(state.get("lifecycle_step"), int)
                and 1 <= state["lifecycle_step"] <= 21
                else 21
            ),
            subjects={"artifact_id": provisional_mutation.event["event_hash"]},
            expected_revision=provisional_mutation.revision,
            seed=anchor_request_seed,
        )
        # The seed above deterministically produces request_id; assert this at
        # the boundary so a future request-ID algorithm cannot orphan a pending
        # anchor phase.
        if requests[0]["request_id"] != request_id:
            raise CLIError(
                "anchor_request_identity_mismatch",
                "provisional event and S12 request identities diverged",
                exit_code=70,
            )
        return _anchor_wait_result(
            command, replay(args.workspace).revision,
            provisional_mutation.event, requests,
        )

    if provisional["payload"].get("intent_digest") != intent_digest:
        raise CLIError(
            "idempotency_conflict",
            "logical closure operation was already used with different payload bytes",
            exit_code=4,
        )
    request_id = provisional["payload"]["anchor_request_id"]
    request_packet = _secure_packet_document(
        args.workspace, ("packets", "S12", f"{request_id}.json"),
        packet_kind="anchor S12",
    )
    requests = [{
        "request_id": request_id,
        "operation": "git.anchor-ledger-head",
        "outer_skill_binding": request_packet["binding_identity"],
        "binding_skill_digest": request_packet["binding_skill_digest"],
        "adapter_contract_digest": request_packet["adapter_contract_digest"],
        "packet_path": f"packets/S12/{request_id}.json",
        "subjects": request_packet["subjects"],
        "disposition": state.get("handoffs", {}).get(request_id, {}).get("status", "created"),
    }]
    if accepted is None:
        result = _validated_persisted_anchor_result(
            args.workspace,
            getattr(args, "trusted_anchor_result", None),
            state=state,
            request_id=request_id,
            provisional_hash=provisional["event_hash"],
            anchor_operation_id=provisional["payload"]["anchor_operation_id"],
        )
        accepted_mutation = mutate(
            args.workspace,
            expected_revision=args.expected_revision,
            operation_id=_phase_operation_id(
                logical_operation_id, f"{action}-accepted",
            ),
            event_type="anchor.accepted",
            subjects={"workspace_id": workspace_id},
            payload={
                "provisional_hash": provisional["event_hash"],
                "anchored_head": provisional["event_hash"],
                "receipt_digest": digest(result),
                "request_id": request_id,
                "result_digest": digest(result),
            },
        )
        accepted = accepted_mutation.event
        state = accepted_mutation.state
        revision = accepted_mutation.revision
    else:
        # A crash after anchor acceptance is resumed only with the same exact
        # persisted S13, never from prose or a different valid receipt.
        _validated_persisted_anchor_result(
            args.workspace,
            getattr(args, "trusted_anchor_result", None),
            state=state,
            request_id=request_id,
            provisional_hash=provisional["event_hash"],
            anchor_operation_id=provisional["payload"]["anchor_operation_id"],
        )
        revision = replayed.revision

    if action == "archive":
        terminal_transition = bundle.transition(terminal_transition_name)
        passed, missing, _ = _transition_gate(
            terminal_transition_name, terminal_transition, state,
        )
        if not passed:
            raise CLIError(
                "anchor_transition_unsatisfied",
                "anchor acceptance did not satisfy the pinned archive transition: "
                + ", ".join(missing),
                exit_code=3,
            )
    terminal_mutation = mutate(
        args.workspace,
        expected_revision=revision,
        operation_id=_phase_operation_id(
            logical_operation_id, f"{action}-terminal",
        ),
        event_type=terminal_type,
        subjects={"workspace_id": workspace_id, **payload.get("subjects", {})},
        payload={
            "transition": terminal_transition_name,
            "authority_action": "lifecycle.close",
            "authority_inherited_from": provisional["event_hash"],
            "logical_operation_id": logical_operation_id,
            "provisional_hash": provisional["event_hash"],
            "anchor_accepted_event": accepted["event_hash"],
            "anchor_request_id": request_id,
            "intent_digest": intent_digest,
            "closure_id": provisional["payload"].get("closure_id"),
            "closure_instance_key": provisional["payload"].get(
                "closure_instance_key",
            ),
            "authority_bound_artifacts_digest": provisional["payload"].get(
                "authority_bound_artifacts_digest", digest([]),
            ),
        },
    )
    return _result(command, terminal_mutation.revision, {
        "event_hash": terminal_mutation.event["event_hash"],
        "event_type": terminal_type,
        "provisional_head": provisional["event_hash"],
        "anchor_request_id": request_id,
    }), 0


def _find_artifact(state: dict[str, Any], workspace: str, artifact_id: str, instance_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    instances = state.get("artifacts", {}).get(artifact_id, {})
    record = instances.get(instance_key)
    if not record:
        raise CLIError("artifact_missing", f"required artifact is absent: {artifact_id}/{instance_key}", exit_code=3)
    value = read_document(Path(workspace) / record["path"])
    if not isinstance(value, dict) or digest(value) != record["digest"]:
        raise CLIError("artifact_digest_mismatch", f"artifact does not match ledger: {artifact_id}/{instance_key}")
    return value, record


def _envelope(artifact_type: str, artifact_id: str, instance_key: str, content: dict[str, Any], subjects: dict[str, str], *, status: str = "published") -> dict[str, Any]:
    return {
        "schema": "sdm-artifact/v1",
        "artifact_type": artifact_type,
        "artifact_id": artifact_id,
        "instance_key": instance_key,
        "version": 1,
        "status": status,
        "subjects": subjects,
        "content": content,
        "content_digest": digest(content),
        "uri": f"artifact://{artifact_type}/{instance_key}",
        "created_at": utc_now(),
        "created_by": "sdm-lifecycle",
    }


def _authority(payload: dict[str, Any], *, action: str, subject: dict[str, Any], revision: int, bundle: AssetBundle, replayed: Any, attestation_value: Any | None = None) -> list[dict[str, Any]]:
    attestations = attestation_value if attestation_value is not None else payload.get("attestations")
    if isinstance(attestations, dict):
        attestations = [attestations]
    if not isinstance(attestations, list) or not attestations:
        raise CLIError("authority_required", f"{action} requires authority attestations", exit_code=3)
    created = next((
        event for event in replayed.events
        if event.get("event_type") == "workspace.created"
    ), None)
    created_payload = created.get("payload", {}) if isinstance(created, dict) else {}
    trust_root = created_payload.get("authority_trust_root")
    trust_root_identity = created_payload.get("authority_trust_root_identity")
    if not isinstance(trust_root, str) or not isinstance(trust_root_identity, str):
        raise CLIError(
            "trusted_root_required",
            "workspace genesis lacks setup-bound authority trust configuration",
            exit_code=5,
        )
    trusted_snapshot, trusted_digest, current_identity = \
        _trusted_snapshot_from_root(Path(trust_root))
    if current_identity != trust_root_identity:
        raise CLIError(
            "trust_root_identity_mismatch",
            "configured authority trust root identity changed after genesis",
            exit_code=5,
        )
    try:
        return verify_authorities(
            attestations, action=action, expected_subject=subject,
            expected_revision=revision, bundle=bundle,
            consumption_time=utc_now(),
            trusted_snapshot=trusted_snapshot,
            trusted_snapshot_digest=trusted_digest,
        )
    except AuthorityError as exc:
        raise CLIError(exc.code, str(exc), exit_code=5 if exc.configuration else 2) from exc


def _do_validate(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    checked = validate_workspace(args.workspace, artifact_path=args.artifact, trusted_anchor_path=args.trusted_anchor_result)
    status = "pass" if checked["valid"] else "fail"
    return _result(_command(args), checked["revision"], checked, checked["findings"], status=status), 0 if checked["valid"] else 2


def _do_hash(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    value = read_document(args.input)
    return _result(_command(args), None, {"digest": digest(value), "canonical_size": len(canonical_bytes(value))}), 0


def _require_current_lifecycle_step(
    state: dict[str, Any], expected: int, command: str,
) -> None:
    """Keep every dedicated executor on the same ordered public state machine.

    Artifact presence or a compatible dimension is not sufficient: imported
    history may contain those facts while earlier governed rows are still
    incomplete.  Dedicated commands therefore share the exact earliest-row
    rule used by ``lifecycle advance``.
    """

    actual = state.get("lifecycle_step")
    if actual != expected:
        raise CLIError(
            "lifecycle_step_out_of_order",
            f"{command} is executable only at STEP{expected:02d}; "
            f"the earliest current row is STEP{actual:02d}"
            if isinstance(actual, int) and actual <= 21
            else f"{command} is executable only at STEP{expected:02d}; "
                 "the lifecycle is already terminal",
            exit_code=3,
        )


def _do_freeze(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    replayed, state, bundle = _workspace_state(args.workspace)
    draft, _ = _find_artifact(state, args.workspace, "E01", args.experiment_id)
    semantic_draft = draft.get("content", draft)
    content_digest = candidate_digest(semantic_draft)
    logical_operation_id = (
        args.operation_id or "specctl-" + digest({
            "command": "specctl.freeze",
            "experiment_id": args.experiment_id,
            "candidate_digest": content_digest,
        }).split(":", 1)[1][:40]
    )
    intent_digest = digest({
        "experiment_id": args.experiment_id,
        "candidate_digest": content_digest,
    })
    provisional, accepted, terminal = _find_anchor_phase(
        replayed,
        provisional_type="protocol.freeze-provisional",
        terminal_type="experiment.frozen",
        logical_operation_id=logical_operation_id,
    )
    if terminal is not None:
        return _result(_command(args), replayed.revision, {
            "candidate_digest": content_digest,
            "event_hash": terminal["event_hash"],
            "event_type": "experiment.frozen",
            "idempotent": True,
        }), 0
    _require_current_lifecycle_step(state, 8, "freeze")
    if provisional is not None:
        if provisional["payload"].get("intent_digest") != intent_digest:
            raise CLIError(
                "idempotency_conflict",
                "freeze operation was already used for different candidate bytes",
                exit_code=4,
            )
        request_id = provisional["payload"]["anchor_request_id"]
        packet = read_document(
            Path(args.workspace) / "packets" / "S12" / f"{request_id}.json"
        )
        request = [{
            "request_id": request_id,
            "operation": "git.anchor-ledger-head",
            "outer_skill_binding": packet["binding_identity"],
            "binding_skill_digest": packet["binding_skill_digest"],
            "adapter_contract_digest": packet["adapter_contract_digest"],
            "packet_path": f"packets/S12/{request_id}.json",
            "subjects": packet["subjects"],
            "disposition": state.get("handoffs", {}).get(request_id, {}).get(
                "status", "created"
            ),
        }]
        if accepted is None:
            record = state.get("handoffs", {}).get(request_id, {})
            if record.get("status") not in {"successful", "reconciled_effect"}:
                return _anchor_wait_result(
                    _command(args), replayed.revision, provisional, request,
                )
            result = _validated_persisted_anchor_result(
                args.workspace,
                str(Path(args.workspace) / "packets" / "S13" / f"{request_id}.json"),
                state=state,
                request_id=request_id,
                provisional_hash=provisional["event_hash"],
                anchor_operation_id=provisional["payload"]["anchor_operation_id"],
            )
            accepted_mutation = mutate(
                args.workspace,
                expected_revision=args.expected_revision,
                operation_id=_phase_operation_id(
                    logical_operation_id, "freeze-accepted",
                ),
                event_type="anchor.accepted",
                subjects={
                    "workspace_id": replayed.genesis["workspace_id"],
                    "experiment_id": args.experiment_id,
                },
                payload={
                    "provisional_hash": provisional["event_hash"],
                    "anchored_head": provisional["event_hash"],
                    "receipt_digest": digest(result),
                    "request_id": request_id,
                    "result_digest": digest(result),
                },
            )
            accepted = accepted_mutation.event
            revision = accepted_mutation.revision
        else:
            revision = replayed.revision
        frozen_mutation = mutate(
            args.workspace,
            expected_revision=revision,
            operation_id=_phase_operation_id(
                logical_operation_id, "freeze-terminal",
            ),
            event_type="experiment.frozen",
            subjects={
                "workspace_id": replayed.genesis["workspace_id"],
                "experiment_id": args.experiment_id,
            },
            payload={
                "transition": "protocol.freeze",
                "authority_action": "freeze.approve",
                "authority_inherited_from": provisional["event_hash"],
                "candidate_digest": provisional["payload"][
                    "candidate_digest"
                ],
                "frozen_digest": provisional["payload"]["frozen_digest"],
                "candidate_spec_digest": provisional["payload"].get(
                    "candidate_spec_digest",
                    provisional["payload"]["candidate_digest"],
                ),
                "freeze_inputs_digest": provisional["payload"].get(
                    "freeze_inputs_digest",
                ),
                "candidate_to_frozen": {
                    "candidate_spec_digest": provisional["payload"].get(
                        "candidate_spec_digest",
                        provisional["payload"]["candidate_digest"],
                    ),
                    "frozen_envelope_digest": provisional["payload"][
                        "frozen_digest"
                    ],
                },
                "step": 8,
                "lifecycle_step_completion": True,
                "outputs": ["E01.frozen"],
                "logical_operation_id": logical_operation_id,
                "provisional_hash": provisional["event_hash"],
                "anchor_accepted_event": accepted["event_hash"],
                "anchor_request_id": request_id,
                "intent_digest": intent_digest,
            },
        )
        return _result(_command(args), frozen_mutation.revision, {
            "candidate_digest": content_digest,
            "frozen_digest": provisional["payload"]["frozen_digest"],
            "event_hash": frozen_mutation.event["event_hash"],
            "event_type": "experiment.frozen",
        }), 0
    if state.get("pending_anchor"):
        raise CLIError(
            "anchor_phase_conflict",
            "another formal transition already has a pending anchor",
            exit_code=3,
        )
    freeze_transition = bundle.transition("protocol.freeze-provisional")
    source_missing = _transition_source_missing(
        "protocol.freeze-provisional", freeze_transition, state,
    )
    if source_missing:
        blocked = finding(
            "transition_unsatisfied",
            "freeze cannot start outside readiness-review",
            subject="protocol.freeze-provisional",
            governing_asset="transition-requirements.json",
        )
        return _result(
            _command(args), replayed.revision,
            {"valid": False, "missing": source_missing}, [blocked],
            status="blocked",
        ), 3
    freeze_readiness = _verified_freeze_readiness(
        workspace=args.workspace, replayed=replayed, state=state,
        bundle=bundle, experiment_id=args.experiment_id,
    )
    if freeze_readiness["candidate_spec_digest"] != content_digest:
        raise CLIError(
            "freeze_candidate_digest_mismatch",
            "current E01 candidate bytes differ from STEP07 readiness",
            exit_code=3,
        )
    freeze_operations = [
        operation for operation in _gate_operations(freeze_transition["gate"])
        if state.get("platform_results", {}).get(operation) != "successful"
    ]
    if freeze_operations:
        _, requests = _ensure_platform_requests(
            args.workspace,
            replayed=replayed,
            state=state,
            bundle=bundle,
            operations=freeze_operations,
            gate="protocol.freeze-provisional",
            step=8,
            subjects={
                "experiment_id": args.experiment_id,
                "artifact_id": f"E01:{args.experiment_id}",
            },
            expected_revision=args.expected_revision,
            seed={
                "workspace_id": replayed.genesis["workspace_id"],
                "experiment_id": args.experiment_id,
                "candidate_digest": content_digest,
                "gate": "protocol.freeze-provisional",
            },
        )
        blocked = finding(
            "platform_results_required",
            "freeze prerequisites require exact outer Git/Jenkins results",
            subject=args.experiment_id,
            governing_asset="transition-requirements.json",
        )
        return _result(
            _command(args), replay(args.workspace).revision,
            {"handoffs": requests, "missing": [
                f"platform_result:{operation}" for operation in freeze_operations
            ]},
            [blocked], status="blocked",
        ), 3
    freeze_gate_context = deepcopy(state)
    freeze_gate_context.setdefault("checks", {}).update(
        freeze_readiness["checks"],
    )
    passed, missing, _ = _transition_gate(
        "protocol.freeze-provisional", freeze_transition,
        freeze_gate_context,
    )
    if not passed:
        blocked = finding(
            "transition_unsatisfied",
            "freeze prerequisites are unsatisfied",
            subject="protocol.freeze-provisional",
            governing_asset="transition-requirements.json",
        )
        return _result(
            _command(args), replayed.revision,
            {"valid": False, "missing": missing}, [blocked],
            status="blocked",
        ), 3
    attestations = read_document(args.attestation)
    provenance = _authority_provenance_extension(
        state=state, workspace=args.workspace, bundle=bundle,
        replayed=replayed,
        nodes=set(freeze_readiness["provenance_nodes"]),
        required_domains={"experiment"},
    )
    subject = {
        "governed_ids": {
            "workspace_id": replayed.genesis["workspace_id"],
            "experiment_id": args.experiment_id,
        },
        "digests": {"candidate_digest": content_digest},
        **provenance,
    }
    authority = _authority({}, action="freeze.approve", subject=subject, revision=replayed.revision, bundle=bundle, replayed=replayed, attestation_value=attestations)
    frozen = deepcopy(draft) if isinstance(draft, dict) else draft
    if frozen.get("schema") != "sdm-artifact/v1":
        frozen = _envelope("E01", args.experiment_id, f"{args.experiment_id}.frozen", semantic_draft, {"workspace_id": replayed.genesis["workspace_id"], "experiment_id": args.experiment_id}, status="frozen")
    else:
        frozen["status"] = "frozen"
        frozen["instance_key"] = f"{args.experiment_id}.frozen"
    frozen["extensions"] = {
        **frozen.get("extensions", {}),
        "dev.bmad.sdm.freeze": {
            "candidate_digest": content_digest,
            "candidate_spec_digest": content_digest,
            "freeze_inputs_digest": freeze_readiness[
                "freeze_inputs_digest"
            ],
            "frozen_at": utc_now(),
            "freeze_attestations": sorted(
                item["attestation_digest"] for item in authority
            ),
            "event_revision": replayed.revision + 1,
        },
    }
    frozen_findings = bundle.validate_artifact("E01", frozen)
    if frozen_findings:
        raise CLIError(
            "frozen_experiment_invalid",
            frozen_findings[0]["message"], findings=frozen_findings,
        )
    frozen_digest = digest(frozen)
    instance_key = f"{args.experiment_id}.frozen"
    relative = f"artifacts/E01/{instance_key}.json"
    published = {"artifact_id": "E01", "instance_key": instance_key, "path": relative, "digest": digest(frozen), "parents": [f"E01:{args.experiment_id}"], "status": "provisional"}
    anchor_operation_id = _phase_operation_id(
        logical_operation_id, "freeze-anchor",
    )
    request_seed = {
        "workspace_id": replayed.genesis["workspace_id"],
        "experiment_id": args.experiment_id,
        "logical_operation_id": logical_operation_id,
        "intent_digest": intent_digest,
    }
    request_id = "REQ-" + digest({
        **request_seed, "operation": "git.anchor-ledger-head",
    }).split(":", 1)[1][:32]
    mutation = mutate(
        args.workspace,
        expected_revision=args.expected_revision,
        operation_id=logical_operation_id,
        event_type="protocol.freeze-provisional",
        subjects={"workspace_id": replayed.genesis["workspace_id"], "experiment_id": args.experiment_id},
        payload={
            "transition": "protocol.freeze-provisional",
            "experiment_id": args.experiment_id,
            "candidate_digest": content_digest,
            "candidate_spec_digest": content_digest,
            "freeze_inputs_digest": freeze_readiness[
                "freeze_inputs_digest"
            ],
            "frozen_digest": frozen_digest,
            "candidate_to_frozen": {
                "candidate_spec_digest": content_digest,
                "frozen_envelope_digest": frozen_digest,
            },
            "logical_operation_id": logical_operation_id,
            "intent_digest": intent_digest,
            "anchor_operation_id": anchor_operation_id,
            "anchor_request_id": request_id,
            "authority_action": "freeze.approve",
            "authority_subject": subject,
            "authority_expected_revision": replayed.revision,
            "consumed_at": authority[0]["consumed_at"],
            "published_artifacts": [published],
        },
        authority=authority,
        artifact_writes=((relative, frozen, True),),
    )
    _, requests = _ensure_platform_requests(
        args.workspace,
        replayed=replayed,
        state=mutation.state,
        bundle=bundle,
        operations=["git.anchor-ledger-head"],
        gate="protocol.freeze",
        step=8,
        subjects={
            "experiment_id": args.experiment_id,
            "artifact_id": f"E01:{instance_key}",
        },
        expected_revision=mutation.revision,
        seed=request_seed,
    )
    if requests[0]["request_id"] != request_id:
        raise CLIError(
            "anchor_request_identity_mismatch",
            "freeze provisional and S12 identities diverged",
            exit_code=70,
        )
    blocked = finding("external_anchor_required", "frozen bytes are provisional until git.anchor-ledger-head is accepted", subject=args.experiment_id, governing_asset="transition-requirements.json")
    return _result(_command(args), replay(args.workspace).revision, {
        "candidate_digest": content_digest,
        "frozen_digest": frozen_digest,
        "provisional_head": mutation.event["event_hash"],
        "anchor_request_id": request_id,
        "handoffs": requests,
        "disposition": "anchor_required",
    }, [blocked], status="blocked"), 3


def _resolved_config_diff(
    before: Any, after: Any, pointer: str = "",
) -> list[dict[str, str]]:
    if isinstance(before, dict) and isinstance(after, dict):
        rows: list[dict[str, str]] = []
        for key in sorted(set(before) | set(after)):
            escaped = key.replace("~", "~0").replace("/", "~1")
            child = f"{pointer}/{escaped}"
            if key not in before:
                if isinstance(after[key], dict):
                    rows.extend(_resolved_config_diff({}, after[key], child))
                else:
                    rows.append({
                        "path": child,
                        "old_digest": digest({"state": "absent"}),
                        "new_digest": digest(after[key]),
                    })
            elif key not in after:
                if isinstance(before[key], dict):
                    rows.extend(_resolved_config_diff(before[key], {}, child))
                else:
                    rows.append({
                        "path": child,
                        "old_digest": digest(before[key]),
                        "new_digest": digest({"state": "absent"}),
                    })
            else:
                rows.extend(_resolved_config_diff(
                    before[key], after[key], child,
                ))
        return rows
    if before == after:
        return []
    if not pointer:
        raise CLIError(
            "resolved_config_root_invalid",
            "resolved configuration roots must be objects", exit_code=2,
        )
    return [{
        "path": pointer,
        "old_digest": digest(before),
        "new_digest": digest(after),
    }]


def _reference_digest_diff(
    before: dict[str, Any], after: dict[str, Any],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    absent = digest({"state": "absent"})
    for ref in sorted(set(before) | set(after)):
        old = before.get(ref, absent)
        new = after.get(ref, absent)
        if old != new:
            rows.append({"ref": ref, "old_digest": old, "new_digest": new})
    return rows


def _git_tree_diff(
    before: dict[str, Any], after: dict[str, Any],
) -> list[dict[str, str]]:
    for name, inventory in (("git_tree_before", before), ("git_tree_after", after)):
        invalid = sorted(
            path for path, bound in inventory.items()
            if (
                not isinstance(path, str)
                or not path
                or path.startswith("/")
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or any(character in path for character in ("?", "*", "[", "]", "\\", "\x00"))
                or not isinstance(bound, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", bound) is None
            )
        )
        if invalid:
            raise CLIError(
                "git_tree_inventory_invalid",
                f"{name} contains unsafe paths or non-canonical digests: "
                + ", ".join(invalid),
                exit_code=2,
            )
    return [
        {"path": row["ref"], "old_digest": row["old_digest"],
         "new_digest": row["new_digest"]}
        for row in _reference_digest_diff(before, after)
    ]


def _pointer_digest(value: dict[str, Any], pointer: str) -> str:
    current: Any = value
    try:
        for segment in pointer.split("/")[1:]:
            key = segment.replace("~1", "/").replace("~0", "~")
            current = current[int(key)] if isinstance(current, list) else current[key]
    except (KeyError, IndexError, TypeError, ValueError):
        return digest({"state": "absent"})
    return digest(current)


def _do_check_diff(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    replayed, state, bundle = _workspace_state(args.workspace)
    frozen, frozen_record = _validated_stored_artifact(
        state, args.workspace, bundle, "E01", f"{args.experiment_id}.frozen",
    )
    supplied = _payload(args.candidate)
    expected_wrapper = {
        "candidate", "git_tree_before", "git_tree_after",
        "resolved_config_before", "resolved_config_after",
    }
    if not isinstance(supplied, dict) or set(supplied) != expected_wrapper:
        raise CLIError(
            "diff_input_invalid",
            "check-diff requires candidate plus complete before/after Git-tree and resolved-config inventories",
            exit_code=2,
        )
    candidate = supplied["candidate"]
    if not isinstance(candidate, dict):
        raise CLIError("diff_candidate_invalid", "candidate must be an E01 object")
    candidate_findings = bundle.validate_artifact("E01", candidate)
    if candidate_findings:
        raise CLIError(
            "diff_candidate_invalid", candidate_findings[0]["message"],
            exit_code=3, findings=candidate_findings,
        )
    frozen_content = frozen.get("content", frozen)
    candidate_value = candidate.get("content", candidate)
    if not isinstance(frozen_content, dict) or not isinstance(candidate_value, dict):
        raise CLIError(
            "diff_candidate_invalid", "frozen and candidate E01 content must be objects",
        )
    if any(
        candidate_value.get(field) != frozen_content.get(field)
        for field in ("change_only", "frozen_variables", "forbidden_changes")
    ):
        raise CLIError(
            "change_policy_mutated",
            "candidate cannot alter its frozen change-only policy",
            exit_code=3,
        )
    policy_problems = validate_change_policy(frozen_content)
    if policy_problems:
        code, message, pointer = policy_problems[0]
        raise CLIError(
            code, message, exit_code=3,
            findings=[finding(
                code, message, subject=f"E01:{args.experiment_id}.frozen",
                pointer=pointer, governing_asset="artifact-contracts.json",
            )],
        )
    old_contracts = frozen_content.get("contracts", {}).get(
        "source_digests", {},
    )
    new_contracts = candidate_value.get("contracts", {}).get(
        "source_digests", {},
    )
    old_sources = frozen_content.get("source_refs", {})
    new_sources = candidate_value.get("source_refs", {})
    if not all(isinstance(item, dict) for item in (
        old_contracts, new_contracts, old_sources, new_sources,
        supplied["git_tree_before"], supplied["git_tree_after"],
        supplied["resolved_config_before"], supplied["resolved_config_after"],
    )):
        raise CLIError(
            "diff_source_invalid",
            "resolved configurations and immutable source maps must be objects",
        )
    if (
        supplied["resolved_config_before"] != frozen_content.get("variables")
        or supplied["resolved_config_after"] != candidate_value.get("variables")
    ):
        raise CLIError(
            "resolved_config_candidate_mismatch",
            "resolved-config inventories must exactly equal frozen/candidate E01 variables",
            exit_code=3,
        )
    old_artifacts = {
        ref: bound for ref, bound in old_sources.items()
        if ref not in old_contracts
    }
    new_artifacts = {
        ref: bound for ref, bound in new_sources.items()
        if ref not in new_contracts
    }
    change = frozen_content["change_only"]
    equal_actual = {
        item["ref"]: new_contracts.get(item["ref"], new_sources.get(item["ref"]))
        for item in change["required_equal_refs"]
    }
    frozen_actual = {
        item["path"]: _pointer_digest(candidate_value, item["path"])
        for item in frozen_content["frozen_variables"]
    }
    diff_evidence = {
        "diff_scope_complete": True,
        "git_path_diff": _git_tree_diff(
            supplied["git_tree_before"], supplied["git_tree_after"],
        ),
        "resolved_config_diff": _resolved_config_diff(
            supplied["resolved_config_before"], supplied["resolved_config_after"],
        ),
        "contract_ref_diff": _reference_digest_diff(
            old_contracts, new_contracts,
        ),
        "artifact_digest_diff": _reference_digest_diff(
            old_artifacts, new_artifacts,
        ),
        "required_equal_ref_digests": equal_actual,
        "frozen_variable_digests": frozen_actual,
    }
    diff_evidence["diff_evidence_digest"] = digest(diff_evidence)
    diff_problems = validate_change_diff_evidence(
        frozen_content, diff_evidence,
    )
    if diff_problems:
        findings = [
            finding(
                code, message, subject=f"E01:{args.experiment_id}.frozen",
                pointer=pointer, governing_asset="artifact-contracts.json",
            )
            for code, message, pointer in diff_problems
        ]
        raise CLIError(
            findings[0]["code"], findings[0]["message"], exit_code=3,
            findings=findings,
        )
    old_digest = candidate_digest(frozen)
    new_digest = candidate_digest(candidate)
    if old_digest == new_digest:
        classification = "unchanged"
    else:
        first_run = next((event["sequence"] for event in replayed.events if event["event_type"] in {"run.materialized", "run.dispatched", "run.started"} and event.get("subjects", {}).get("experiment_id") == args.experiment_id), None)
        material_fields = {"hypothesis", "decision_rule", "primary_metrics", "baseline", "data", "split", "evaluator", "protocol", "channel", "budget"}
        changed = sorted(
            key for key in set(frozen_content) | set(candidate_value)
            if frozen_content.get(key) != candidate_value.get(key)
        )
        if first_run is None:
            classification = "E06"
        elif material_fields.intersection(changed):
            classification = "successor-required"
        else:
            classification = "E07"
    return _result(_command(args), replayed.revision, {
        "classification": classification,
        "current_digest": old_digest,
        "candidate_digest": new_digest,
        "frozen_experiment_digest": frozen_record["digest"],
        "diff_evidence": diff_evidence,
    }), 0


def _do_materialize_run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    replayed, state, bundle = _workspace_state(args.workspace)
    if args.expected_revision != replayed.revision:
        raise WorkspaceError(
            "cas_conflict",
            f"expected revision {args.expected_revision}, actual {replayed.revision}",
            exit_code=4,
        )
    _require_current_lifecycle_step(state, 14, "materialize-run")
    if state.get("dimensions", {}).get("protocol") != "frozen":
        raise CLIError("protocol_not_frozen", "Run materialization requires anchor-accepted frozen E01", exit_code=3)
    frozen, frozen_record = _validated_stored_artifact(
        state, args.workspace, bundle,
        "E01", f"{args.experiment_id}.frozen",
    )
    run_plan, run_plan_record = _validated_stored_artifact(
        state, args.workspace, bundle, "E04", args.run_plan_id,
    )
    plan = run_plan.get("content", run_plan)
    anchor_context = _validated_experiment_anchor_context(
        content=frozen.get("content", frozen), state=state,
        workspace=args.workspace, bundle=bundle,
    )
    frozen_candidate_digest = candidate_digest(
        frozen.get("content", frozen),
    )
    if (
        plan.get("run_plan_id") != args.run_plan_id
        or plan.get("experiment_id") != args.experiment_id
        or plan.get("candidate_spec_digest")
        != frozen_candidate_digest
        or plan.get("source_refs") != anchor_context["source_refs"]
    ):
        raise CLIError(
            "run_plan_binding_mismatch",
            "E04 does not bind the exact selected Experiment candidate bytes",
            exit_code=3,
        )
    frozen_events = [
        event for event in replayed.events
        if event.get("event_type") == "experiment.frozen"
        and event.get("subjects", {}).get("experiment_id")
        == args.experiment_id
        and event.get("payload", {}).get("candidate_to_frozen") == {
            "candidate_spec_digest": frozen_candidate_digest,
            "frozen_envelope_digest": frozen_record["digest"],
        }
    ]
    freeze_extension = frozen.get("extensions", {}).get(
        "dev.bmad.sdm.freeze", {},
    )
    if (
        len(frozen_events) != 1
        or freeze_extension.get("candidate_spec_digest")
        != frozen_candidate_digest
        or not isinstance(freeze_extension.get("freeze_inputs_digest"), str)
    ):
        raise CLIError(
            "frozen_candidate_mapping_missing",
            "materialize-run requires the terminal candidate-to-frozen digest mapping",
            exit_code=3,
        )
    run_rows = [
        row for row in plan.get("runs", [])
        if isinstance(row, dict) and row.get("run_id") == args.run_id
    ]
    if len(run_rows) != 1:
        raise CLIError(
            "run_plan_subject_missing",
            "E04 must contain exactly one selected Run identity",
            exit_code=3,
        )
    selected_run = run_rows[0]
    required_attempt_ids = selected_run.get("required_attempt_ids")
    if (
        not isinstance(required_attempt_ids, list)
        or not required_attempt_ids
        or args.attempt_id not in required_attempt_ids
        or any(not isinstance(item, str) for item in required_attempt_ids)
        or len(set(required_attempt_ids)) != len(required_attempt_ids)
    ):
        raise CLIError(
            "run_attempt_not_planned",
            "selected Attempt is not in the exact E04 Run attempt plan",
            exit_code=3,
        )
    preflight_instance_key = plan.get("preflight_instance_key")
    implementation_revision = plan.get("implementation_revision")
    if (
        not isinstance(preflight_instance_key, str)
        or not preflight_instance_key
        or not isinstance(implementation_revision, str)
        or not implementation_revision
    ):
        raise CLIError(
            "preflight_binding_missing",
            "E04 must pin one S09 instance and implementation revision",
            exit_code=3,
        )
    preflight, preflight_record = _validated_stored_artifact(
        state, args.workspace, bundle, "S09", preflight_instance_key,
    )
    preflight_content = preflight.get("content", preflight)
    if (
        preflight_content.get("experiment_digest") != frozen_record["digest"]
        or preflight_content.get("revision") != implementation_revision
    ):
        raise CLIError(
            "preflight_binding_mismatch",
            "S09 does not bind the exact frozen E01 and implementation revision",
            exit_code=3,
        )
    digests: dict[str, str] = {}
    for name in ("data_digest", "split_digest", "evaluator_digest"):
        value = selected_run.get(name)
        if not isinstance(value, str) or not value.startswith("sha256:"):
            raise CLIError(
                "run_plan_digest_missing",
                f"selected E04 Run lacks canonical {name}",
                exit_code=3,
            )
        digests[name] = value
    if any(
        selected_run.get(name) != anchor_context[name] for name in (
            "data_digest", "split_digest", "evaluator_digest",
            "dataset_manifest_digest", "split_manifest_digest",
            "calibration_applicable",
        )
    ) or anchor_context["calibration_applicable"] and any(
        selected_run.get(name) != anchor_context[name] for name in (
            "calibration_manifest_digest", "calibration_digest",
        )
    ):
        raise CLIError(
            "run_plan_scientific_anchor_mismatch",
            "selected E04 Run does not exactly repeat R04/R05/R06/G10 anchors",
            exit_code=3,
        )
    environment_requirements = selected_run.get("environment_requirements")
    expected_runtime_image_digest = selected_run.get(
        "expected_runtime_image_digest",
    )
    expected_runtime_profile = selected_run.get("expected_runtime_profile")
    seed = selected_run.get("seed")
    budget = selected_run.get("budget")
    if (
        environment_requirements in (None, [], {})
        or not runtime_image_binding_valid(expected_runtime_image_digest, expected_runtime_profile)
        or not isinstance(expected_runtime_profile, dict)
        or not expected_runtime_profile
        or seed in (None, "", [], {})
        or budget in (None, [], {})
    ):
        raise CLIError(
            "run_plan_execution_contract_missing",
            "selected E04 Run lacks explicit runtime image/profile, seed, environment requirements, or budget",
            exit_code=3,
        )
    provenance = preflight_content.get("engineering_provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("schema") != ENGINEERING_PROVENANCE_SCHEMA
        or any(
            not isinstance(provenance.get(name), str) or not provenance[name]
            for name in (
                *ENGINEERING_PROVENANCE_COMMON_FIELDS,
                *ENGINEERING_PROVENANCE_MERGED_FIELDS,
            )
        )
        or implementation_revision != provenance.get("merged_revision")
    ):
        raise CLIError(
            "preflight_engineering_provenance_invalid",
            "S09 does not bind the exact merged engineering provenance selected by E04",
            exit_code=3,
        )
    configuration_rows = preflight_content.get("resolved_configurations")
    selected_configurations = [
        row for row in configuration_rows
        if isinstance(row, dict) and row.get("run_id") == args.run_id
    ] if isinstance(configuration_rows, list) else []
    if len(selected_configurations) != 1:
        raise CLIError(
            "resolved_configuration_missing",
            "S09 must contain exactly one authenticated configuration for the selected Run",
            exit_code=3,
        )
    signed_configuration = selected_configurations[0]
    resolved_sources = {
        "experiment_digest": frozen_record["digest"],
        "run_plan_digest": run_plan_record["digest"],
        "preflight_digest": preflight_record["digest"],
        "baseline_lock_digest": anchor_context["baseline_lock_digest"],
        **digests,
        "dataset_manifest_digest": anchor_context["dataset_manifest_digest"],
        "split_manifest_digest": anchor_context["split_manifest_digest"],
        "evaluator_contract_digest": anchor_context["evaluator_digest"],
        **({
            "calibration_manifest_digest": anchor_context[
                "calibration_manifest_digest"
            ],
            "calibration_digest": anchor_context["calibration_digest"],
        } if anchor_context["calibration_applicable"] else {}),
    }
    resolved_content = {
        "schema": "sdm-resolved-config/v1", "id": args.run_id,
        "run_id": args.run_id, "attempt_id": args.attempt_id,
        "source_run_plan_id": args.run_plan_id,
        "resolved_values": deepcopy(signed_configuration["resolved_values"]),
        "defaults": deepcopy(signed_configuration["defaults"]),
        "override_origins": deepcopy(signed_configuration["override_origins"]),
        "structured_config_schema_digest": signed_configuration[
            "structured_config_schema_digest"
        ],
        "unknown_keys": [], "forbidden_overrides": [],
        "resolution_trace_digest": signed_configuration[
            "resolution_trace_digest"
        ],
        "config_digest": signed_configuration["config_digest"],
        "frozen_sources": [
            {"name": name, "digest": value}
            for name, value in sorted(resolved_sources.items())
        ],
    }
    subjects = {"workspace_id": replayed.genesis["workspace_id"], "experiment_id": args.experiment_id, "run_id": args.run_id, "attempt_id": args.attempt_id}
    resolved = _envelope("R02", args.run_id, args.run_id, resolved_content, subjects)
    resolved_digest = digest(resolved)
    fingerprint_bindings: dict[str, Any] = {
        "run_id": args.run_id,
        "attempt_id": args.attempt_id,
        "frozen_experiment_ref": f"E01:{args.experiment_id}.frozen",
        "frozen_experiment_digest": frozen_record["digest"],
        "run_plan_ref": f"E04:{args.run_plan_id}",
        "run_plan_digest": run_plan_record["digest"],
        **{
            name: provenance[name] for name in (
                *ENGINEERING_PROVENANCE_COMMON_FIELDS,
                *ENGINEERING_PROVENANCE_MERGED_FIELDS,
            )
        },
        "baseline_lock_ref": anchor_context["baseline_lock_ref"],
        "baseline_lock_digest": anchor_context["baseline_lock_digest"],
        "resolved_configuration_ref": f"R02:{args.run_id}",
        "resolved_configuration_digest": resolved_digest,
        "config_digest": resolved_content["config_digest"],
        "dataset_manifest_ref": anchor_context["dataset_manifest_ref"],
        "dataset_manifest_digest": anchor_context["dataset_manifest_digest"],
        "data_digest": anchor_context["data_digest"],
        "split_manifest_ref": anchor_context["split_manifest_ref"],
        "split_manifest_digest": anchor_context["split_manifest_digest"],
        "split_digest": anchor_context["split_digest"],
        "evaluator_contract_ref": anchor_context["evaluator_contract_ref"],
        "evaluator_contract_digest": anchor_context["evaluator_contract_digest"],
        "evaluator_digest": anchor_context["evaluator_digest"],
        "calibration_applicable": anchor_context["calibration_applicable"],
        **({
            "calibration_manifest_ref": anchor_context["calibration_manifest_ref"],
            "calibration_manifest_digest": anchor_context[
                "calibration_manifest_digest"
            ],
            "calibration_digest": anchor_context["calibration_digest"],
        } if anchor_context["calibration_applicable"] else {}),
        "expected_runtime_image_digest": expected_runtime_image_digest,
        "expected_runtime_profile": deepcopy(expected_runtime_profile),
        "expected_runtime_profile_digest": digest(expected_runtime_profile),
        "seed": deepcopy(seed), "seed_digest": digest(seed),
        "budget": deepcopy(budget), "budget_digest": digest(budget),
        "allowed_diff": deepcopy(frozen.get("content", frozen)["change_only"]),
        "allowed_diff_digest": digest(
            frozen.get("content", frozen)["change_only"],
        ),
    }
    try:
        run_fingerprint, run_fingerprint_digest = canonical_run_fingerprint(
            fingerprint_bindings,
        )
    except ContractError as exc:
        raise CLIError(exc.code, str(exc), exit_code=3) from exc
    manifest_content = {
        "schema": "sdm-run-manifest/v1", "id": args.run_id,
        "run_id": args.run_id, "attempt_id": args.attempt_id,
        "required_attempt_ids": sorted(required_attempt_ids),
        "experiment_id": args.experiment_id, "run_plan_id": args.run_plan_id,
        "protocol": state.get("protocol", "exploratory"),
        "frozen_spec_digest": frozen_record["digest"],
        "experiment_digest": frozen_record["digest"],
        "attempt_policy": plan["attempt_policy"],
        **fingerprint_bindings,
        "engineering_provenance": deepcopy(provenance),
        "environment_requirements": deepcopy(environment_requirements),
        "preflight_instance_key": preflight_instance_key,
        "implementation_revision": implementation_revision,
        "preflight_digest": preflight_record["digest"],
        "run_fingerprint": run_fingerprint,
        "run_fingerprint_digest": run_fingerprint_digest,
        "workspace_revision": replayed.revision,
        "ledger_head": replayed.head_hash,
        "materialized_at": utc_now(),
    }
    manifest = _envelope("R01", args.run_id, args.run_id, manifest_content, subjects)
    artifacts = [
        {"artifact_id": "R02", "instance_key": args.run_id, "path": f"artifacts/R02/{args.run_id}.json", "digest": resolved_digest, "parents": [f"E01:{args.experiment_id}.frozen", f"E04:{args.run_plan_id}", f"S09:{preflight_instance_key}"], "status": "published"},
        {"artifact_id": "R01", "instance_key": args.run_id, "path": f"artifacts/R01/{args.run_id}.json", "digest": digest(manifest), "parents": [f"E01:{args.experiment_id}.frozen", f"E04:{args.run_plan_id}", f"S09:{preflight_instance_key}", f"R02:{args.run_id}", anchor_context["baseline_lock_ref"], anchor_context["dataset_manifest_ref"], anchor_context["split_manifest_ref"], anchor_context["evaluator_contract_ref"], *([anchor_context["calibration_manifest_ref"]] if anchor_context["calibration_applicable"] else [])], "status": "published"},
    ]
    for artifact_id, value in (("R02", resolved), ("R01", manifest)):
        findings = bundle.validate_artifact(artifact_id, value)
        if findings:
            raise CLIError("run_artifact_invalid", findings[0]["message"], findings=findings)
    mutation = mutate(
        args.workspace,
        expected_revision=args.expected_revision,
        operation_id=_operation_id(args, artifacts),
        event_type="run.materialized",
        subjects={"workspace_id": replayed.genesis["workspace_id"], "experiment_id": args.experiment_id, "run_id": args.run_id, "attempt_id": args.attempt_id},
        payload={"published_artifacts": artifacts, "run_id": args.run_id, "attempt_id": args.attempt_id},
        artifact_writes=((artifacts[0]["path"], resolved, True), (artifacts[1]["path"], manifest, True)),
    )
    return _result(_command(args), mutation.revision, {"run_manifest": artifacts[1], "resolved_config": artifacts[0]}), 0


def _validated_run_fingerprint_context(
    *, manifest: dict[str, Any], state: dict[str, Any], workspace: str,
    bundle: AssetBundle,
) -> dict[str, Any]:
    """Re-open every R01 fingerprint source and re-run the canonical helper."""

    content = manifest.get("content", manifest)
    fingerprint = content.get("run_fingerprint") \
        if isinstance(content, dict) else None
    if not isinstance(fingerprint, dict) or fingerprint.get(
        "schema",
    ) != "sdm-run-fingerprint/v1":
        raise CLIError(
            "run_fingerprint_invalid", "R01 lacks its versioned fingerprint",
            exit_code=3,
        )
    bindings = {key: value for key, value in fingerprint.items() if key != "schema"}
    try:
        expected, expected_digest = canonical_run_fingerprint(bindings)
    except ContractError as exc:
        raise CLIError(exc.code, str(exc), exit_code=3) from exc
    if (
        fingerprint != expected
        or content.get("run_fingerprint_digest") != expected_digest
        or any(content.get(name) != value for name, value in bindings.items())
    ):
        raise CLIError(
            "run_fingerprint_binding_mismatch",
            "R01 fingerprint, flat bindings, and digest disagree", exit_code=3,
        )

    ref_bindings = [
        ("frozen_experiment_ref", "frozen_experiment_digest"),
        ("run_plan_ref", "run_plan_digest"),
        ("baseline_lock_ref", "baseline_lock_digest"),
        ("resolved_configuration_ref", "resolved_configuration_digest"),
        ("dataset_manifest_ref", "dataset_manifest_digest"),
        ("split_manifest_ref", "split_manifest_digest"),
        ("evaluator_contract_ref", "evaluator_contract_digest"),
    ]
    if content.get("calibration_applicable") is True:
        ref_bindings.append((
            "calibration_manifest_ref", "calibration_manifest_digest",
        ))
    reopened: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for ref_name, digest_name in ref_bindings:
        ref = content.get(ref_name)
        if not isinstance(ref, str) or ref.count(":") != 1:
            raise CLIError(
                "run_fingerprint_reference_invalid",
                f"R01 {ref_name} is not an exact artifact ref", exit_code=3,
            )
        artifact_id, instance_key = ref.split(":", 1)
        value, record = _validated_stored_artifact(
            state, workspace, bundle, artifact_id, instance_key,
        )
        if record["digest"] != content.get(digest_name):
            raise CLIError(
                "run_fingerprint_reference_mismatch",
                f"R01 substitutes {ref_name}/{digest_name}", exit_code=3,
            )
        reopened[ref_name] = (value, record)

    resolved = reopened["resolved_configuration_ref"][0]
    resolved_content = resolved.get("content", resolved)
    if (
        resolved_content.get("run_id") != content.get("run_id")
        or resolved_content.get("attempt_id") != content.get("attempt_id")
        or resolved_content.get("config_digest") != content.get("config_digest")
        or resolved_content.get("config_digest")
        != digest(resolved_content.get("resolved_values"))
    ):
        raise CLIError(
            "run_resolved_configuration_mismatch",
            "R01 does not bind the exact complete R02 configuration",
            exit_code=3,
        )
    frozen = reopened["frozen_experiment_ref"][0]
    frozen_content = frozen.get("content", frozen)
    if content.get("allowed_diff") != frozen_content.get("change_only"):
        raise CLIError(
            "run_allowed_diff_mismatch",
            "R01 allowed diff differs from frozen E01", exit_code=3,
        )
    plan = reopened["run_plan_ref"][0].get(
        "content", reopened["run_plan_ref"][0],
    )
    run_rows = [
        row for row in plan.get("runs", [])
        if isinstance(row, dict) and row.get("run_id") == content.get("run_id")
    ]
    if len(run_rows) != 1:
        raise CLIError(
            "run_fingerprint_plan_row_missing",
            "R01 run identity is not unique in E04", exit_code=3,
        )
    run_row = run_rows[0]
    for name in (
        "expected_runtime_image_digest", "expected_runtime_profile", "seed",
        "budget", "data_digest", "split_digest", "evaluator_digest",
    ):
        if content.get(name) != run_row.get(name):
            raise CLIError(
                "run_fingerprint_plan_binding_mismatch",
                f"R01 {name} differs from exact E04", exit_code=3,
            )
    preflight_key = content.get("preflight_instance_key")
    preflight, preflight_record = _validated_stored_artifact(
        state, workspace, bundle, "S09", preflight_key,
    )
    preflight_content = preflight.get("content", preflight)
    config_rows = [
        row for row in preflight_content.get("resolved_configurations", [])
        if isinstance(row, dict) and row.get("run_id") == content.get("run_id")
    ]
    if (
        preflight_record["digest"] != content.get("preflight_digest")
        or preflight_content.get("engineering_provenance")
        != content.get("engineering_provenance")
        or len(config_rows) != 1
        or any(
            resolved_content.get(name) != config_rows[0].get(name)
            for name in (
                "resolved_values", "defaults", "override_origins",
                "structured_config_schema_digest", "unknown_keys",
                "forbidden_overrides", "resolution_trace_digest",
                "config_digest",
            )
        )
    ):
        raise CLIError(
            "run_preflight_binding_mismatch",
            "R01/R02 do not derive from the exact S09 Jenkins configuration facts",
            exit_code=3,
        )
    return {
        "content": content, "resolved_content": resolved_content,
        "fingerprint_digest": expected_digest,
    }


def _materialized_run_plan_context(
    *, workspace: str, replayed: Any, state: dict[str, Any],
    bundle: AssetBundle, subjects: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Prove STEP14 has materialized the *entire* exact frozen E04.

    `materialize-run` may be invoked once per planned Run while STEP14 is
    current.  The lifecycle row cannot advance after merely one such event: it
    authenticates exact R01/R02 bytes and one unique materialization event for
    every E04 Run, and rejects extra Run identities for the Experiment.
    """

    experiment_id = subjects.get("experiment_id")
    run_plan_id = subjects.get("run_plan_id")
    if not isinstance(experiment_id, str) or not isinstance(run_plan_id, str):
        raise CLIError(
            "run_plan_subjects_required",
            "STEP14 advance subjects must pin experiment_id and run_plan_id",
            exit_code=3,
        )
    frozen, frozen_record = _validated_stored_artifact(
        state, workspace, bundle, "E01", f"{experiment_id}.frozen",
    )
    run_plan, run_plan_record = _validated_stored_artifact(
        state, workspace, bundle, "E04", run_plan_id,
    )
    plan = run_plan.get("content", run_plan)
    anchors = _validated_experiment_anchor_context(
        content=frozen.get("content", frozen), state=state,
        workspace=workspace, bundle=bundle,
    )
    rows = plan.get("runs")
    if (
        plan.get("run_plan_id") != run_plan_id
        or plan.get("experiment_id") != experiment_id
        or plan.get("candidate_spec_digest")
        != candidate_digest(frozen.get("content", frozen))
        or plan.get("source_refs") != anchors["source_refs"]
        or not isinstance(rows, list)
        or not rows
    ):
        raise CLIError(
            "run_plan_binding_mismatch",
            "STEP14 requires the exact frozen-E01-bound, non-empty E04",
            exit_code=3,
        )
    planned: dict[str, dict[str, Any]] = {}
    for row in rows:
        run_id = row.get("run_id") if isinstance(row, dict) else None
        attempts = row.get("required_attempt_ids") if isinstance(row, dict) else None
        if (
            not isinstance(run_id, str)
            or run_id in planned
            or not isinstance(attempts, list)
            or not attempts
            or any(not isinstance(value, str) or not value for value in attempts)
            or len(set(attempts)) != len(attempts)
        ):
            raise CLIError(
                "run_plan_invalid",
                "E04 must uniquely enumerate every Run and required Attempt",
                exit_code=3,
            )
        planned[run_id] = row

    exact_events: list[dict[str, Any]] = []
    for run_id, plan_row in sorted(planned.items()):
        manifest, manifest_record = _validated_stored_artifact(
            state, workspace, bundle, "R01", run_id,
        )
        resolved, resolved_record = _validated_stored_artifact(
            state, workspace, bundle, "R02", run_id,
        )
        manifest_content = manifest.get("content", manifest)
        resolved_content = resolved.get("content", resolved)
        _validated_run_fingerprint_context(
            manifest=manifest, state=state, workspace=workspace, bundle=bundle,
        )
        if (
            manifest_content.get("run_id") != run_id
            or manifest_content.get("experiment_id") != experiment_id
            or manifest_content.get("run_plan_id") != run_plan_id
            or manifest_content.get("run_plan_digest") != run_plan_record["digest"]
            or manifest_content.get("frozen_spec_digest") != frozen_record["digest"]
            or any(
                manifest_content.get(name) != anchors[name]
                for name in (
                    "data_digest", "split_digest", "evaluator_digest",
                    "dataset_manifest_digest", "split_manifest_digest",
                    "calibration_applicable",
                )
            )
            or anchors["calibration_applicable"] and any(
                manifest_content.get(name) != anchors[name]
                for name in (
                    "calibration_manifest_digest", "calibration_digest",
                )
            )
            or sorted(manifest_content.get("required_attempt_ids", []))
            != sorted(plan_row["required_attempt_ids"])
            or any(
                manifest_content.get(name) != plan_row.get(name)
                for name in (
                    "data_digest", "split_digest", "evaluator_digest",
                    "environment_requirements", "budget",
                )
            )
            or resolved_content.get("run_id") != run_id
            or resolved_content.get("source_run_plan_id") != run_plan_id
            or manifest_content.get("resolved_configuration_ref")
            != f"R02:{run_id}"
            or manifest_content.get("resolved_configuration_digest")
            != resolved_record["digest"]
        ):
            raise CLIError(
                "run_materialization_binding_mismatch",
                f"R01/R02 for {run_id} do not exactly derive from E04",
                exit_code=3,
            )
        matching_events = [
            event for event in replayed.events
            if event.get("event_type") == "run.materialized"
            and event.get("subjects", {}).get("experiment_id") == experiment_id
            and event.get("subjects", {}).get("run_id") == run_id
            and {
                item.get("artifact_id"): item.get("digest")
                for item in event.get("payload", {}).get(
                    "published_artifacts", [],
                )
                if isinstance(item, dict)
            } == {
                "R01": manifest_record["digest"],
                "R02": resolved_record["digest"],
            }
        ]
        if len(matching_events) != 1:
            raise CLIError(
                "run_materialization_event_invalid",
                f"Run {run_id} requires exactly one digest-bound materialization event",
                exit_code=3,
            )
        exact_events.append(matching_events[0])

    extra_runs: list[str] = []
    for run_id in state.get("artifacts", {}).get("R01", {}):
        if run_id in planned:
            continue
        value, _ = _validated_stored_artifact(
            state, workspace, bundle, "R01", run_id,
        )
        if value.get("content", value).get("experiment_id") == experiment_id:
            extra_runs.append(run_id)
    if extra_runs:
        raise CLIError(
            "run_materialization_set_mismatch",
            "R01 inventory contains Runs outside exact E04: "
            + ", ".join(sorted(extra_runs)),
            exit_code=3,
        )
    context = deepcopy(state)
    context.setdefault("checks", {}).update({
        "run.fingerprints_exact": True,
        "run.references_exact": True,
        "run.budget_exact": True,
        "attempt.planned": True,
    })
    return exact_events, context


def _do_verify_run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    replayed, state, bundle = _workspace_state(args.workspace)
    manifest = _payload(args.manifest)
    findings = bundle.validate_artifact("R01", manifest)
    run_id = manifest.get("content", manifest).get("run_id")
    record = state.get("artifacts", {}).get("R01", {}).get(run_id)
    if not record or record.get("digest") != digest(manifest):
        findings.append(finding("run_manifest_unbound", "Run manifest is not the exact ledger-bound R01", subject=str(run_id or ""), governing_asset="artifact-catalog.json"))
    else:
        try:
            _validated_run_fingerprint_context(
                manifest=manifest, state=state, workspace=args.workspace,
                bundle=bundle,
            )
        except CLIError as exc:
            findings.append(finding(
                exc.code, str(exc), subject=str(run_id or ""),
                governing_asset="artifact-contracts.json",
            ))
    status = "pass" if not findings else "fail"
    return _result(_command(args), replayed.revision, {"valid": not findings, "digest": digest(manifest)}, findings, status=status), 0 if not findings else 2


def _do_build_evidence(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    replayed, state, bundle = _workspace_state(args.workspace)
    if args.expected_revision != replayed.revision:
        raise WorkspaceError(
            "cas_conflict",
            f"expected revision {args.expected_revision}, actual {replayed.revision}",
            exit_code=4,
        )
    _require_current_lifecycle_step(state, 18, "build-evidence")
    frozen, frozen_record = _validated_stored_artifact(
        state, args.workspace, bundle,
        "E01", f"{args.experiment_id}.frozen",
    )
    frozen_content = frozen.get("content", frozen)
    scientific_anchors = _validated_experiment_anchor_context(
        content=frozen_content, state=state, workspace=args.workspace,
        bundle=bundle,
    )
    run_plan_id = frozen_content.get("run_plan_id")
    pinned_run_plan_digest = frozen_content.get("run_plan_digest")
    if not isinstance(run_plan_id, str) or not run_plan_id:
        raise CLIError(
            "frozen_run_plan_binding_missing",
            "frozen E01 must pin the exact E04 identity",
            exit_code=3,
        )
    run_plan, run_plan_record = _validated_stored_artifact(
        state, args.workspace, bundle, "E04", run_plan_id,
    )
    plan = run_plan.get("content", run_plan)
    if (
        (
            pinned_run_plan_digest is not None
            and run_plan_record["digest"] != pinned_run_plan_digest
        )
        or plan.get("run_plan_id") != run_plan_id
        or plan.get("experiment_id") != args.experiment_id
        or plan.get("candidate_spec_digest")
        != candidate_digest(frozen.get("content", frozen))
        or plan.get("source_refs") != scientific_anchors["source_refs"]
    ):
        raise CLIError(
            "run_plan_binding_mismatch",
            "E04 is not the exact frozen-E01-bound Run plan",
            exit_code=3,
        )
    plan_rows = plan.get("runs")
    if not isinstance(plan_rows, list) or not plan_rows:
        raise CLIError(
            "run_plan_empty", "E04 must enumerate every planned Run",
            exit_code=3,
        )
    planned_by_id: dict[str, dict[str, Any]] = {}
    for row in plan_rows:
        if not isinstance(row, dict) or not isinstance(row.get("run_id"), str):
            raise CLIError(
                "run_plan_invalid", "each E04 Run requires an exact run_id",
                exit_code=3,
            )
        run_id = row["run_id"]
        required_attempt_ids = row.get("required_attempt_ids")
        if (
            run_id in planned_by_id
            or not isinstance(required_attempt_ids, list)
            or not required_attempt_ids
            or any(not isinstance(item, str) for item in required_attempt_ids)
            or len(set(required_attempt_ids)) != len(required_attempt_ids)
        ):
            raise CLIError(
                "evidence_attempt_plan_missing",
                f"E04 Run {run_id} must uniquely enumerate every required Attempt",
                exit_code=3,
            )
        planned_by_id[run_id] = row

    # Evidence completeness is a set property, not a fail-fast property.  Keep
    # every omitted or substituted governed identity visible to the caller so
    # Resume can repair the whole plan without discovering one gap per run.
    evidence_findings: list[dict[str, Any]] = []

    def evidence_gap(code: str, message: str, subject: str) -> None:
        evidence_findings.append(finding(
            code, message, subject=subject,
            governing_asset="artifact-contracts.json",
        ))

    manifests: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    r01_instances = state.get("artifacts", {}).get("R01", {})
    if not isinstance(r01_instances, dict):
        r01_instances = {}
    missing_runs = sorted(set(planned_by_id) - set(r01_instances))
    extra_runs: list[str] = []
    for run_id in sorted(set(r01_instances) - set(planned_by_id)):
        try:
            value, _ = _find_artifact(
                state, args.workspace, "R01", run_id,
            )
        except CLIError as exc:
            evidence_gap(
                exc.code, str(exc), f"R01:{run_id}",
            )
            continue
        content = value.get("content", value)
        if content.get("experiment_id") == args.experiment_id:
            extra_runs.append(run_id)
    if missing_runs or extra_runs:
        evidence_gap(
            "evidence_run_set_mismatch",
            f"R01 inventory does not exactly equal E04: missing={missing_runs}, extra={extra_runs}",
            f"E04:{run_plan_id}",
        )
    for run_id in missing_runs:
        evidence_gap(
            "run_manifest_missing",
            f"planned Run {run_id} has no exact R01",
            f"R01:{run_id}",
        )
    for run_id in extra_runs:
        evidence_gap(
            "run_manifest_extra",
            f"R01/{run_id} is not present in the exact E04 Run plan",
            f"R01:{run_id}",
        )

    for run_id in sorted(set(planned_by_id) & set(r01_instances)):
        try:
            value, record = _validated_stored_artifact(
                state, args.workspace, bundle, "R01", run_id,
            )
        except CLIError as exc:
            if exc.findings:
                for item in exc.findings:
                    evidence_findings.append({
                        **item, "subject": f"R01:{run_id}",
                    })
            else:
                evidence_gap(exc.code, str(exc), f"R01:{run_id}")
            continue
        content = value.get("content", value)
        try:
            _validated_run_fingerprint_context(
                manifest=value, state=state, workspace=args.workspace,
                bundle=bundle,
            )
        except CLIError as exc:
            evidence_gap(exc.code, str(exc), f"R01:{run_id}")
            continue
        if content.get("run_id") != run_id:
            evidence_gap(
                "run_manifest_subject_mismatch",
                f"R01/{run_id} does not bind its instance identity",
                f"R01:{run_id}",
            )
            continue
        if content.get("experiment_id") != args.experiment_id:
            evidence_gap(
                "run_manifest_subject_mismatch",
                f"R01/{run_id} substitutes the experiment identity",
                f"R01:{run_id}",
            )
            continue
        if (
            content.get("experiment_digest") != frozen_record["digest"]
            or content.get("frozen_spec_digest") != frozen_record["digest"]
            or content.get("run_plan_id") != run_plan_id
            or content.get("run_plan_digest") != run_plan_record["digest"]
        ):
            evidence_gap(
                "run_manifest_plan_mismatch",
                f"R01/{run_id} is not bound to the exact frozen E01 and E04",
                f"R01:{run_id}",
            )
            continue
        required_attempt_ids = content.get("required_attempt_ids")
        if (
            not isinstance(required_attempt_ids, list)
            or not required_attempt_ids
            or any(not isinstance(item, str) for item in required_attempt_ids)
            or len(set(required_attempt_ids)) != len(required_attempt_ids)
        ):
            evidence_gap(
                "evidence_attempt_plan_missing",
                f"R01/{run_id} must enumerate every required Attempt identity",
                f"R01:{run_id}",
            )
            continue
        planned = planned_by_id[run_id]
        if sorted(required_attempt_ids) != sorted(
            planned["required_attempt_ids"]
        ):
            evidence_gap(
                "run_manifest_plan_mismatch",
                f"R01/{run_id} substitutes the E04 Attempt plan",
                f"R01:{run_id}",
            )
            continue
        preflight_instance_key = content.get("preflight_instance_key")
        implementation_revision = content.get("implementation_revision")
        preflight_digest = content.get("preflight_digest")
        if (
            not isinstance(preflight_instance_key, str)
            or not isinstance(implementation_revision, str)
            or not isinstance(preflight_digest, str)
        ):
            evidence_gap(
                "run_manifest_preflight_binding_missing",
                f"R01/{run_id} lacks its exact S09 binding",
                f"R01:{run_id}",
            )
            continue
        try:
            preflight, preflight_record = _validated_stored_artifact(
                state, args.workspace, bundle, "S09", preflight_instance_key,
            )
        except CLIError as exc:
            evidence_gap(
                "run_manifest_preflight_missing",
                f"R01/{run_id} names unavailable S09/{preflight_instance_key}: {exc}",
                f"S09:{preflight_instance_key}",
            )
            continue
        preflight_content = preflight.get("content", preflight)
        if (
            preflight_record["digest"] != preflight_digest
            or preflight_content.get("experiment_digest")
            != frozen_record["digest"]
            or preflight_content.get("revision") != implementation_revision
        ):
            evidence_gap(
                "run_manifest_preflight_mismatch",
                f"R01/{run_id} does not bind the exact applicable S09",
                f"S09:{preflight_instance_key}",
            )
            continue
        manifests[run_id] = (content, record)
    runs = [
        {
            "run_id": run_id,
            "required_attempt_ids": sorted(
                planned_by_id[run_id]["required_attempt_ids"]
            ),
        }
        for run_id in sorted(planned_by_id)
    ]

    selected_run_ids = {row["run_id"] for row in runs}
    terminal_by_attempt: dict[str, dict[str, Any]] = {}
    for event in replayed.events:
        if event["event_type"] != "attempt.terminal":
            continue
        payload = event.get("payload", {})
        subjects = event.get("subjects", {})
        attempt_id = payload.get("attempt_id", subjects.get("attempt_id"))
        run_id = payload.get("run_id", subjects.get("run_id"))
        if not isinstance(attempt_id, str) or not isinstance(run_id, str):
            evidence_gap(
                "attempt_terminal_invalid",
                "attempt.terminal must bind exact Run and Attempt identities",
                f"event:{event['event_hash']}",
            )
            continue
        if run_id not in selected_run_ids:
            continue
        if attempt_id in terminal_by_attempt:
            evidence_gap(
                "attempt_terminal_duplicate",
                f"Attempt {attempt_id} has more than one terminal ledger event",
                f"Attempt:{attempt_id}",
            )
            continue
        terminal_by_attempt[attempt_id] = {
            "run_id": run_id,
            "terminal_status": payload.get(
                "terminal_status", payload.get("status"),
            ),
            "accounted": payload.get("accounted"),
            "validity": payload.get("validity"),
            "system_failure": payload.get("system_failure"),
            "event_hash": event["event_hash"],
        }

    accounted_attempts: list[dict[str, Any]] = []
    source_records: dict[str, str] = {
        f"E01:{args.experiment_id}.frozen": frozen_record["digest"],
        f"E04:{run_plan_id}": run_plan_record["digest"],
        **scientific_anchors["source_refs"],
    }
    governed_sources: list[tuple[str, dict[str, Any]]] = []
    scientific_author_ids: set[str] = set()
    required_attempt_set: set[str] = set()
    terminal_statuses = {"successful", "failed", "cancelled", "invalid"}
    for run in runs:
        run_id = run["run_id"]
        manifest_pair = manifests.get(run_id)
        if manifest_pair is not None:
            manifest_content, manifest_record = manifest_pair
            source_records[f"R01:{run_id}"] = manifest_record["digest"]
            source_records[
                f"S09:{manifest_content['preflight_instance_key']}"
            ] = manifest_content["preflight_digest"]
        for attempt_id in run["required_attempt_ids"]:
            if attempt_id in required_attempt_set:
                evidence_gap(
                    "evidence_attempt_plan_invalid",
                    f"Attempt {attempt_id} appears under more than one Run",
                    f"Attempt:{attempt_id}",
                )
                continue
            required_attempt_set.add(attempt_id)
            terminal = terminal_by_attempt.get(attempt_id)
            if terminal is None or terminal.get("run_id") != run_id:
                evidence_gap(
                    "evidence_incomplete",
                    f"Attempt {attempt_id} lacks its exact terminal ledger fact",
                    f"Attempt:{attempt_id}",
                )
            elif (
                terminal.get("terminal_status") not in terminal_statuses
                or terminal.get("accounted") is not True
                or terminal.get("validity") not in {"valid", "invalid"}
                or not isinstance(terminal.get("system_failure"), bool)
            ):
                evidence_gap(
                    "evidence_incomplete",
                    f"Attempt {attempt_id} is not canonically terminal and accounted",
                    f"Attempt:{attempt_id}",
                )
            r07 = r07_record = r08 = r08_record = None
            for artifact_id in ("R07", "R08"):
                try:
                    value, record = _validated_stored_artifact(
                        state, args.workspace, bundle, artifact_id, attempt_id,
                    )
                except CLIError as exc:
                    evidence_gap(
                        "platform_evidence_missing",
                        f"{artifact_id}/{attempt_id} is unavailable or invalid: {exc}",
                        f"{artifact_id}:{attempt_id}",
                    )
                    continue
                if artifact_id == "R07":
                    r07, r07_record = value, record
                else:
                    r08, r08_record = value, record
            if r07 is None or r08 is None:
                continue
            actual = r07.get("content", r07)
            index = r08.get("content", r08)
            if (
                actual.get("run_id") != run_id
                or actual.get("attempt_id") != attempt_id
                or index.get("run_id") != run_id
                or index.get("attempt_id") != attempt_id
            ):
                evidence_gap(
                    "attempt_subject_mismatch",
                    f"R07/R08 do not bind exact Run/Attempt {run_id}/{attempt_id}",
                    f"Attempt:{attempt_id}",
                )
                continue
            exact_facts = {
                "terminal_status": actual.get("status"),
                "validity": actual.get("validity"),
                "system_failure": actual.get("system_failure"),
            }
            if terminal is None or any(
                terminal.get(key) != value
                for key, value in exact_facts.items()
            ):
                evidence_gap(
                    "attempt_fact_mismatch",
                    f"R07 and attempt.terminal disagree for {attempt_id}",
                    f"Attempt:{attempt_id}",
                )
                continue
            accounted_attempts.append({
                "attempt_id": attempt_id,
                "run_id": run_id,
                "terminal_status": terminal["terminal_status"],
                "accounted": True,
                "validity": terminal["validity"],
                "system_failure": terminal["system_failure"],
            })
            source_records[f"R07:{attempt_id}"] = r07_record["digest"]
            source_records[f"R08:{attempt_id}"] = r08_record["digest"]
            attempt_kind = actual.get("attempt_kind")
            if attempt_kind in {"training", "hpo_trial"}:
                if any(name in actual for name in (
                    "evidence_summary", "statistical_analysis",
                    "failure_case_index", "scientific_facts",
                )):
                    evidence_gap(
                        "training_scientific_fact_forbidden",
                        f"R07/{attempt_id} training/HPO facts contaminate scientific aggregation",
                        f"R07:{attempt_id}",
                    )
                continue
            if attempt_kind != "locked_evaluation":
                evidence_gap(
                    "attempt_kind_invalid",
                    f"R07/{attempt_id} has no governed Attempt kind",
                    f"R07:{attempt_id}",
                )
                continue
            platform_complete = True
            for required in (
                "evidence_summary", "statistical_analysis",
                "failure_case_index", "scientific_facts",
            ):
                if not isinstance(actual.get(required), dict):
                    evidence_gap(
                        "platform_evidence_missing",
                        f"locked-evaluation R07/{attempt_id} lacks governed {required}",
                        f"R07:{attempt_id}",
                    )
                    platform_complete = False
            training_attempt = actual.get("selected_checkpoint_attempt_id")
            training_actual_record = state.get("artifacts", {}).get(
                "R07", {},
            ).get(training_attempt)
            training_index_record = state.get("artifacts", {}).get(
                "R08", {},
            ).get(training_attempt)
            if (
                not isinstance(training_attempt, str)
                or not isinstance(training_actual_record, dict)
                or not isinstance(training_index_record, dict)
                or actual.get("training_r07_digest")
                != training_actual_record.get("digest")
                or actual.get("training_r08_digest")
                != training_index_record.get("digest")
                or index.get("training_r07_digest")
                != training_actual_record.get("digest")
                or index.get("training_r08_digest")
                != training_index_record.get("digest")
                or index.get("evaluated_checkpoint_digest")
                != actual.get("evaluated_checkpoint_digest")
                or index.get("evaluated_model_artifact_digest")
                != actual.get("evaluated_model_artifact_digest")
            ):
                evidence_gap(
                    "evaluation_model_binding_mismatch",
                    f"locked-evaluation R07/R08 {attempt_id} does not bind one exact training checkpoint/model pair",
                    f"R07:{attempt_id}",
                )
                platform_complete = False
            authors = actual.get("author_principal_ids")
            if (
                not isinstance(authors, list)
                or not authors
                or authors != sorted(set(authors))
                or index.get("author_principal_ids") != authors
            ):
                evidence_gap(
                    "evaluation_authorship_invalid",
                    f"locked-evaluation R07/R08 {attempt_id} lack one exact platform-producer author set",
                    f"R07:{attempt_id}",
                )
                platform_complete = False
            if platform_complete:
                governed_sources.append((attempt_id, actual))
                scientific_author_ids.update(authors)
    extra_terminals = sorted(
        set(terminal_by_attempt) - required_attempt_set
    )
    if extra_terminals:
        evidence_gap(
            "evidence_attempt_set_mismatch",
            f"terminal Attempt facts are not in the selected Run plan: {extra_terminals}",
            f"E04:{run_plan_id}",
        )

    if evidence_findings:
        ordered_findings = sort_findings(evidence_findings)
        raise CLIError(
            "evidence_incomplete",
            "governed evidence is incomplete or substituted",
            exit_code=3,
            findings=ordered_findings,
            result={
                "complete": False,
                "gap_subjects": sorted({
                    item.get("subject", "") for item in ordered_findings
                    if item.get("subject")
                }),
            },
        )

    if not governed_sources:
        raise CLIError(
            "locked_evaluation_evidence_missing",
            "scientific evidence requires at least one exact locked-evaluation Attempt",
            exit_code=3,
        )
    if not scientific_author_ids:
        raise CLIError(
            "evidence_authorship_missing",
            "scientific evidence requires stable author principals derived from locked-evaluation S13 producers",
            exit_code=3,
        )
    governed_sources.sort(key=lambda row: row[0])
    evidence_authors = sorted(scientific_author_ids)

    scientific_facts = governed_sources[0][1]["scientific_facts"]
    if any(
        digest(actual["scientific_facts"]) != digest(scientific_facts)
        for _, actual in governed_sources[1:]
    ):
        raise CLIError(
            "scientific_fact_conflict",
            "platform-produced scientific facts conflict across accounted Attempts",
            exit_code=3,
        )
    summary = {
        "author_principal_ids": evidence_authors,
        "hypothesis": frozen_content.get("hypothesis"),
        "results": [
            {"attempt_id": attempt_id, "value": actual["evidence_summary"].get("results")}
            for attempt_id, actual in governed_sources
        ],
        "limitations": [
            {"attempt_id": attempt_id, "value": actual["evidence_summary"].get("limitations")}
            for attempt_id, actual in governed_sources
        ],
        "missingness": [
            {"attempt_id": attempt_id, "value": actual["evidence_summary"].get("missingness")}
            for attempt_id, actual in governed_sources
        ],
        "deviations": [
            {"attempt_id": attempt_id, "value": actual["evidence_summary"].get("deviations")}
            for attempt_id, actual in governed_sources
        ],
        "source_attempt_ids": sorted(
            attempt_id for attempt_id, _ in governed_sources
        ),
    }
    statistical = {
        key: [
            {"attempt_id": attempt_id, "value": actual["statistical_analysis"].get(key)}
            for attempt_id, actual in governed_sources
        ]
        for key in (
            "comparisons", "effect_sizes", "uncertainty", "power",
            "multiplicity",
        )
    }
    statistical["author_principal_ids"] = evidence_authors
    statistical["scientific_facts"] = scientific_facts
    statistical["source_attempt_ids"] = sorted(
        attempt_id for attempt_id, _ in governed_sources
    )
    failure_index = {
        key: [
            {"attempt_id": attempt_id, "value": actual["failure_case_index"].get(key)}
            for attempt_id, actual in governed_sources
        ]
        for key in ("taxonomy", "cases", "artifact_digests", "coverage")
    }
    failure_index["author_principal_ids"] = evidence_authors
    failure_index["source_attempt_ids"] = sorted(
        attempt_id for attempt_id, _ in governed_sources
    )
    base = {
        "author_principal_ids": evidence_authors,
        "evidence_id": args.evidence_id,
        "experiment_id": args.experiment_id,
        "planned_runs": runs,
        "accounted_attempts": sorted(
            accounted_attempts, key=lambda row: row["attempt_id"],
        ),
        "scientific_attempt_ids": sorted(
            attempt_id for attempt_id, _ in governed_sources
        ),
        "items": sorted(source_records),
        "source_digests": dict(sorted(source_records.items())),
        "bundle_digest": digest(source_records),
    }
    content_documents = {
        "D01": base,
        "D02": summary,
        "D03": statistical,
        "D04": failure_index,
    }
    documents = {artifact_id: _envelope(artifact_id, args.evidence_id, args.evidence_id, content, {"workspace_id": replayed.genesis["workspace_id"], "experiment_id": args.experiment_id, "evidence_id": args.evidence_id}) for artifact_id, content in content_documents.items()}
    artifacts = []
    writes = []
    for artifact_id, value in documents.items():
        key = args.evidence_id
        path = f"artifacts/{artifact_id}/{key}.json"
        artifacts.append({"artifact_id": artifact_id, "instance_key": key, "path": path, "digest": digest(value), "parents": sorted(source_records), "status": "published"})
        writes.append((path, value, True))
        findings = bundle.validate_artifact(artifact_id, value)
        if findings:
            raise CLIError("evidence_artifact_invalid", findings[0]["message"], findings=findings)
    mutation = mutate(
        args.workspace,
        expected_revision=args.expected_revision,
        operation_id=_operation_id(args, artifacts),
        event_type="evidence.built",
        subjects={"workspace_id": replayed.genesis["workspace_id"], "experiment_id": args.experiment_id, "evidence_id": args.evidence_id},
        payload={
            "published_artifacts": artifacts,
            "complete": True,
            "accounted_runs": runs,
            "accounted_attempts": base["accounted_attempts"],
            "source_digests": source_records,
        },
        artifact_writes=writes,
    )
    return _result(
        _command(args), mutation.revision,
        {"artifacts": artifacts, "complete": True},
    ), 0


def _do_verify_evidence(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    replayed, state, bundle = _workspace_state(args.workspace)
    findings = []
    for artifact_id in ("D01", "D02", "D03", "D04"):
        try:
            value, record = _find_artifact(state, args.workspace, artifact_id, args.evidence_id)
            findings.extend(bundle.validate_artifact(artifact_id, value))
        except CLIError as exc:
            findings.append(finding(exc.code, str(exc), subject=args.evidence_id, governing_asset="artifact-catalog.json"))
    status = "pass" if not findings else "fail"
    return _result(_command(args), replayed.revision, {"valid": not findings}, findings, status=status), 0 if not findings else 2


def _validated_stored_artifact(
    state: dict[str, Any], workspace: str, bundle: AssetBundle,
    artifact_id: str, instance_key: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    value, record = _find_artifact(
        state, workspace, artifact_id, instance_key,
    )
    findings = bundle.validate_artifact(artifact_id, value)
    if findings:
        raise CLIError(
            "stored_artifact_invalid",
            findings[0]["message"],
            findings=findings,
        )
    return value, record


_AUTHORITY_PROVENANCE_ARTIFACT_DOMAINS: dict[str, str] = {
    **{
        artifact_id: "experiment"
        for artifact_id in ("E01", "E02", "E03", "E04", "E06", "E07")
    },
    **{
        artifact_id: "implementation"
        for artifact_id in ("E05", "S05", "S06", "S07", "S08", "S09")
    },
    **{
        artifact_id: "evidence"
        for artifact_id in (
            "R01", "R02", "R03", "R04", "R06", "R07", "R08",
            "D01", "D02", "D03", "D04", "S10",
        )
    },
    "G10": "evaluator-owner",
    "R05": "data-split-approver",
}


def _verified_provenance_row(
    *, state: dict[str, Any], workspace: str, bundle: AssetBundle,
    replayed: Any, artifact_id: str, instance_key: str,
) -> dict[str, Any]:
    """Derive one authority-provenance row from authenticated ledger bytes.

    The caller never supplies authorship.  The envelope author and descriptor
    digest are reopened from the replay-validated workspace, and the exact
    publication event must contain that descriptor before either value can
    enter a signed authority subject.
    """

    domain = _AUTHORITY_PROVENANCE_ARTIFACT_DOMAINS.get(artifact_id)
    if domain is None:
        raise CLIError(
            "authority_provenance_artifact_invalid",
            f"{artifact_id} is not part of an authority provenance domain",
            exit_code=3,
        )
    value, record = _validated_stored_artifact(
        state, workspace, bundle, artifact_id, instance_key,
    )
    content = value.get("content", value)
    principal_field = {
        "G10": "owner_principal_ids",
    }.get(artifact_id, "author_principal_ids")
    authors = content.get(principal_field) \
        if isinstance(content, dict) else None
    if artifact_id == "R05" and authors is None and isinstance(content, dict):
        authors = content.get("approver_principal_ids")
    event_hash = record.get("event_hash")
    publication_events = [
        event for event in replayed.events
        if event.get("event_hash") == event_hash
    ]
    if (
        not isinstance(authors, list)
        or not authors
        or authors != sorted(set(authors))
        or any(not isinstance(author, str) or not author for author in authors)
        or len(publication_events) != 1
        or not any(
            isinstance(descriptor, dict)
            and descriptor.get("artifact_id") == artifact_id
            and descriptor.get("instance_key") == instance_key
            and descriptor.get("digest") == record.get("digest")
            and descriptor.get("path") == record.get("path")
            for descriptor in publication_events[0].get(
                "payload", {},
            ).get("published_artifacts", [])
        )
    ):
        raise CLIError(
            "authority_provenance_unverified",
            f"{artifact_id}:{instance_key} lacks one digest-bound publication principal set",
            exit_code=3,
        )
    return {
        "domain": domain,
        "artifact_id": artifact_id,
        "instance_key": instance_key,
        "artifact_digest": record["digest"],
        "authors": authors,
    }


def _authority_provenance_extension(
    *, state: dict[str, Any], workspace: str, bundle: AssetBundle,
    replayed: Any, nodes: set[tuple[str, str]],
    required_domains: set[str],
) -> dict[str, Any]:
    rows = [
        _verified_provenance_row(
            state=state, workspace=workspace, bundle=bundle,
            replayed=replayed, artifact_id=artifact_id,
            instance_key=instance_key,
        )
        for artifact_id, instance_key in sorted(nodes)
        if _AUTHORITY_PROVENANCE_ARTIFACT_DOMAINS.get(artifact_id)
        in required_domains
    ]
    actual_domains = {row["domain"] for row in rows}
    if actual_domains != required_domains:
        raise CLIError(
            "authority_provenance_incomplete",
            "coordinator-derived provenance domains differ from the exact authority closure",
            exit_code=3,
        )
    try:
        return authority_provenance_subject(rows)
    except AuthorityError as exc:
        raise CLIError(exc.code, str(exc), exit_code=3) from exc


def _implementation_authority_nodes(
    *, state: dict[str, Any], workspace: str, bundle: AssetBundle,
    experiment_id: str, frozen_experiment_digest: str,
) -> set[tuple[str, str]]:
    """Return the exact source-control-authored implementation closure.

    These artifacts are admitted only when their owner envelope names the
    selected Experiment and their coordinator-derived engineering provenance
    repeats the exact frozen E01 digest.  Caller labels and role strings never
    select an implementation author.
    """

    required_types = {"E05", "S07", "S08"}
    found_types: set[str] = set()
    nodes: set[tuple[str, str]] = set()
    for artifact_id in sorted(required_types):
        for instance_key in sorted(
            state.get("artifacts", {}).get(artifact_id, {})
        ):
            value, _ = _validated_stored_artifact(
                state, workspace, bundle, artifact_id, instance_key,
            )
            if value.get("subjects", {}).get("experiment_id") != experiment_id:
                continue
            content = value.get("content", value)
            provenance = content.get("engineering_provenance") \
                if isinstance(content, dict) else None
            if (
                not isinstance(provenance, dict)
                or provenance.get("frozen_experiment_digest")
                != frozen_experiment_digest
            ):
                raise CLIError(
                    "authority_implementation_provenance_mismatch",
                    f"{artifact_id}:{instance_key} does not bind the exact frozen Experiment",
                    exit_code=3,
                )
            found_types.add(artifact_id)
            nodes.add((artifact_id, instance_key))
    if found_types != required_types:
        raise CLIError(
            "authority_implementation_provenance_incomplete",
            "authority provenance requires the exact E05/S07/S08 implementation chain",
            exit_code=3,
        )
    return nodes


def _step_authority_provenance_extension(
    *, step: int, payload: dict[str, Any], state: dict[str, Any],
    workspace: str, bundle: AssetBundle, replayed: Any,
) -> dict[str, Any]:
    required_domains = {
        7: {"experiment"},
        19: {
            "experiment", "implementation", "evidence",
            "evaluator-owner", "data-split-approver",
        },
    }.get(step)
    if required_domains is None:
        return {}
    selected = payload.get("input_refs")
    if not isinstance(selected, dict):
        raise CLIError(
            "authority_provenance_inputs_missing",
            f"STEP{step:02d} requires exact input_refs before authority provenance can be derived",
            exit_code=3,
        )
    nodes: set[tuple[str, str]] = set()
    for artifact_id, selectors in selected.items():
        if isinstance(selectors, dict):
            selectors = [selectors]
        if not isinstance(selectors, list):
            continue
        for selector in selectors:
            if isinstance(selector, dict) and isinstance(
                selector.get("instance_key"), str,
            ):
                nodes.add((artifact_id, selector["instance_key"]))

    # STEP19's evidence authority is over the exact governed source closure,
    # not merely a D01 summary.  Expand only the digest-bound D01 source map;
    # arbitrary caller summaries and unrelated workspace inventory never enter
    # the signed provenance subject.
    if step == 19:
        for artifact_id, instance_key in tuple(nodes):
            if artifact_id != "E01":
                continue
            experiment, experiment_record = _validated_stored_artifact(
                state, workspace, bundle, artifact_id, instance_key,
            )
            experiment_content = experiment.get("content", experiment)
            run_plan_id = experiment_content.get("run_plan_id")
            if not isinstance(run_plan_id, str):
                raise CLIError(
                    "authority_provenance_source_missing",
                    "STEP19 frozen E01 lacks exact E04 provenance identity",
                    exit_code=3,
                )
            nodes.add(("E04", run_plan_id))
            experiment_id = experiment_content.get("experiment_id")
            if not isinstance(experiment_id, str) or not experiment_id:
                raise CLIError(
                    "authority_provenance_source_missing",
                    "STEP19 frozen E01 lacks its stable Experiment identity",
                    exit_code=3,
                )
            anchors = _validated_experiment_anchor_context(
                content=experiment_content, state=state, workspace=workspace,
                bundle=bundle,
            )
            for reference, expected_type in (
                (anchors["evaluator_contract_ref"], "G10"),
                (anchors["split_manifest_ref"], "R05"),
            ):
                artifact_type, anchor_key = reference.split(":", 1)
                if artifact_type != expected_type:
                    raise CLIError(
                        "authority_provenance_source_mismatch",
                        f"STEP19 substitutes its exact {expected_type} authority anchor",
                        exit_code=3,
                    )
                nodes.add((artifact_type, anchor_key))
            nodes.update(_implementation_authority_nodes(
                state=state, workspace=workspace, bundle=bundle,
                experiment_id=experiment_id,
                frozen_experiment_digest=experiment_record["digest"],
            ))
        for artifact_id, instance_key in tuple(nodes):
            if artifact_id != "D01":
                continue
            value, _ = _validated_stored_artifact(
                state, workspace, bundle, artifact_id, instance_key,
            )
            d01_content = value.get("content", value)
            source_digests = d01_content.get("source_digests")
            scientific_attempt_ids = d01_content.get("scientific_attempt_ids")
            if (
                not isinstance(source_digests, dict)
                or not isinstance(scientific_attempt_ids, list)
                or not scientific_attempt_ids
            ):
                raise CLIError(
                    "authority_provenance_source_missing",
                    "STEP19 D01 lacks its exact scientific Attempt/source digest closure",
                    exit_code=3,
                )
            for source_id in ("R07", "R08"):
                for source_key in scientific_attempt_ids:
                    node = f"{source_id}:{source_key}"
                    source_digest = source_digests.get(node)
                    if not isinstance(source_key, str) \
                            or not isinstance(source_digest, str):
                        raise CLIError(
                            "authority_provenance_source_missing",
                            f"D01 does not bind scientific provenance node {node}",
                            exit_code=3,
                        )
                    _, source_record = _validated_stored_artifact(
                        state, workspace, bundle, source_id, source_key,
                    )
                    if source_record["digest"] != source_digest:
                        raise CLIError(
                            "authority_provenance_source_mismatch",
                            f"D01 substitutes provenance digest for {node}",
                            exit_code=3,
                        )
                    nodes.add((source_id, source_key))
    return _authority_provenance_extension(
        state=state, workspace=workspace, bundle=bundle,
        replayed=replayed, nodes=nodes,
        required_domains=required_domains,
    )


def _decision_authority_provenance_extension(
    *, state: dict[str, Any], workspace: str, bundle: AssetBundle,
    replayed: Any, experiment_id: str, evidence_id: str,
) -> dict[str, Any]:
    frozen, frozen_record = _validated_stored_artifact(
        state, workspace, bundle, "E01", f"{experiment_id}.frozen",
    )
    run_plan_id = frozen.get("content", frozen).get("run_plan_id")
    if not isinstance(run_plan_id, str):
        raise CLIError(
            "authority_provenance_incomplete",
            "D05 provenance requires the exact frozen E04 identity",
            exit_code=3,
        )
    nodes: set[tuple[str, str]] = {
        ("E01", f"{experiment_id}.frozen"),
        ("E04", run_plan_id),
        *((artifact_id, evidence_id) for artifact_id in (
            "D01", "D02", "D03", "D04",
        )),
    }
    anchors = _validated_experiment_anchor_context(
        content=frozen.get("content", frozen), state=state,
        workspace=workspace, bundle=bundle,
    )
    for reference, expected_type in (
        (anchors["evaluator_contract_ref"], "G10"),
        (anchors["split_manifest_ref"], "R05"),
    ):
        artifact_type, anchor_key = reference.split(":", 1)
        if artifact_type != expected_type:
            raise CLIError(
                "authority_provenance_source_mismatch",
                f"D05 substitutes its exact {expected_type} authority anchor",
                exit_code=3,
            )
        nodes.add((artifact_type, anchor_key))
    d01, _ = _validated_stored_artifact(
        state, workspace, bundle, "D01", evidence_id,
    )
    d01_content = d01.get("content", d01)
    scientific_attempt_ids = d01_content.get("scientific_attempt_ids")
    source_digests = d01_content.get("source_digests")
    if (
        not isinstance(scientific_attempt_ids, list)
        or not scientific_attempt_ids
        or not isinstance(source_digests, dict)
    ):
        raise CLIError(
            "authority_provenance_incomplete",
            "D05 provenance requires D01's exact scientific source closure",
            exit_code=3,
        )
    for source_id in ("R07", "R08"):
        for attempt_id in scientific_attempt_ids:
            node = f"{source_id}:{attempt_id}"
            _, record = _validated_stored_artifact(
                state, workspace, bundle, source_id, attempt_id,
            )
            if source_digests.get(node) != record["digest"]:
                raise CLIError(
                    "authority_provenance_source_mismatch",
                    f"D05 D01 substitutes scientific provenance node {node}",
                    exit_code=3,
                )
            nodes.add((source_id, attempt_id))

    nodes.update(_implementation_authority_nodes(
        state=state, workspace=workspace, bundle=bundle,
        experiment_id=experiment_id,
        frozen_experiment_digest=frozen_record["digest"],
    ))
    return _authority_provenance_extension(
        state=state, workspace=workspace, bundle=bundle,
        replayed=replayed, nodes=nodes,
        required_domains={
            "experiment", "implementation", "evidence",
            "evaluator-owner", "data-split-approver",
        },
    )


def _require_diagnostic_calibration_facts(calibrations: list[dict[str, Any]], evidence: dict[str, Any]) -> None:
    diagnostic = any(
        isinstance(content.get("uncertainty"), dict)
        and content["uncertainty"].get("status") == "not-measured"
        for content in calibrations
    )
    if diagnostic and evidence.get("underpowered") is not True:
        raise CLIError("diagnostic_calibration_requires_inconclusive",
            "Unmeasured calibration permits diagnostic execution only; scientific evidence must remain underpowered",
            exit_code=3)


def _governed_decision_context(
    args: argparse.Namespace,
    *,
    state: dict[str, Any],
    bundle: AssetBundle,
    replayed: Any,
) -> tuple[dict[str, Any], dict[str, str], str]:
    frozen, frozen_record = _validated_stored_artifact(
        state, args.workspace, bundle,
        "E01", f"{args.experiment_id}.frozen",
    )
    frozen_content = frozen.get("content", frozen)
    scientific_anchors = _validated_experiment_anchor_context(
        content=frozen_content, state=state, workspace=args.workspace,
        bundle=bundle,
    )
    frozen_rules = frozen_content.get("decision_rule")
    if not isinstance(frozen_rules, dict):
        raise CLIError(
            "frozen_decision_rule_missing",
            "the exact frozen E01 lacks an executable decision_rule",
            exit_code=3,
        )
    run_plan_id = frozen_content.get("run_plan_id")
    if not isinstance(run_plan_id, str) or not run_plan_id:
        raise CLIError(
            "frozen_run_plan_binding_missing",
            "frozen E01 must pin the exact E04 identity",
            exit_code=3,
        )
    run_plan, run_plan_record = _validated_stored_artifact(
        state, args.workspace, bundle, "E04", run_plan_id,
    )
    plan = run_plan.get("content", run_plan)
    if (
        plan.get("run_plan_id") != run_plan_id
        or plan.get("experiment_id") != args.experiment_id
        or plan.get("candidate_spec_digest")
        != candidate_digest(frozen.get("content", frozen))
        or plan.get("source_refs") != scientific_anchors["source_refs"]
    ):
        raise CLIError(
            "run_plan_binding_mismatch",
            "E04 is not bound to the exact frozen Experiment",
            exit_code=3,
        )
    planned_rows = plan.get("runs")
    if not isinstance(planned_rows, list) or not planned_rows:
        raise CLIError(
            "run_plan_empty", "E04 must enumerate every planned Run",
            exit_code=3,
        )
    plan_by_id = {
        row.get("run_id"): row for row in planned_rows
        if isinstance(row, dict) and isinstance(row.get("run_id"), str)
    }
    if len(plan_by_id) != len(planned_rows):
        raise CLIError(
            "run_plan_invalid", "E04 Run identities must be exact and unique",
            exit_code=3,
        )

    evidence_values: dict[str, dict[str, Any]] = {}
    evidence_records: dict[str, dict[str, Any]] = {}
    for artifact_id in ("D01", "D02", "D03", "D04"):
        value, record = _validated_stored_artifact(
            state, args.workspace, bundle, artifact_id, args.evidence_id,
        )
        evidence_values[artifact_id] = value.get("content", value)
        evidence_records[artifact_id] = record
    d01 = evidence_values["D01"]
    if (
        d01.get("evidence_id") != args.evidence_id
        or d01.get("experiment_id") != args.experiment_id
    ):
        raise CLIError(
            "evidence_subject_mismatch",
            "D01 does not bind the selected Experiment and evidence identity",
            exit_code=3,
        )
    planned_runs = d01.get("planned_runs")
    declared_attempts = d01.get("accounted_attempts")
    if not isinstance(planned_runs, list) or not isinstance(declared_attempts, list):
        raise CLIError(
            "scientific_accounting_incomplete",
            "D01 must enumerate every planned Run and accounted Attempt",
            exit_code=3,
        )
    declared_by_id: dict[str, dict[str, Any]] = {}
    for row in declared_attempts:
        if not isinstance(row, dict) or not isinstance(row.get("attempt_id"), str):
            raise CLIError(
                "scientific_accounting_incomplete",
                "each D01 accounted_attempts row requires an attempt_id",
                exit_code=3,
            )
        attempt_id = row["attempt_id"]
        if attempt_id in declared_by_id:
            raise CLIError(
                "scientific_accounting_incomplete",
                "D01 contains a duplicate Attempt identity",
                exit_code=3,
            )
        declared_by_id[attempt_id] = row

    exact_runs: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    required_attempts: set[str] = set()
    scientific_attempt_ids: list[str] = []
    scientific_author_ids: set[str] = set()
    source_digests: dict[str, str] = {
        "E01": frozen_record["digest"],
        "E04": run_plan_record["digest"],
        **{
            artifact_id: record["digest"]
            for artifact_id, record in evidence_records.items()
        },
    }
    for run in planned_runs:
        if not isinstance(run, dict) or not isinstance(run.get("run_id"), str):
            raise CLIError(
                "scientific_plan_invalid",
                "each D01 planned_runs row requires an exact run_id",
                exit_code=3,
            )
        run_id = run["run_id"]
        attempt_ids = run.get("required_attempt_ids")
        if not isinstance(attempt_ids, list) or not attempt_ids or any(
            not isinstance(item, str) for item in attempt_ids
        ):
            raise CLIError(
                "scientific_accounting_incomplete",
                f"Run {run_id} lacks its complete required Attempt identities",
                exit_code=3,
            )
        r01, r01_record = _validated_stored_artifact(
            state, args.workspace, bundle, "R01", run_id,
        )
        r01_content = r01.get("content", r01)
        planned = plan_by_id.get(run_id)
        if (
            r01_content.get("run_id") != run_id
            or planned is None
            or r01_content.get("experiment_id") != args.experiment_id
            or r01_content.get("experiment_digest") != frozen_record["digest"]
            or r01_content.get("frozen_spec_digest") != frozen_record["digest"]
            or r01_content.get("run_plan_id") != run_plan_id
            or r01_content.get("run_plan_digest") != run_plan_record["digest"]
        ):
            raise CLIError(
                "run_manifest_plan_mismatch",
                f"R01/{run_id} does not bind its exact frozen E01/E04 plan",
                exit_code=3,
            )
        if sorted(attempt_ids) != sorted(
            planned.get("required_attempt_ids", [])
        ) or sorted(attempt_ids) != sorted(
            r01_content.get("required_attempt_ids", [])
        ):
            raise CLIError(
                "run_manifest_plan_mismatch",
                f"D01/R01 substitute the E04 Attempt plan for {run_id}",
                exit_code=3,
            )
        preflight_instance_key = r01_content.get("preflight_instance_key")
        preflight, preflight_record = _validated_stored_artifact(
            state, args.workspace, bundle, "S09", preflight_instance_key,
        )
        preflight_content = preflight.get("content", preflight)
        if (
            r01_content.get("preflight_digest") != preflight_record["digest"]
            or preflight_content.get("experiment_digest")
            != frozen_record["digest"]
            or preflight_content.get("revision")
            != r01_content.get("implementation_revision")
        ):
            raise CLIError(
                "run_manifest_preflight_mismatch",
                f"R01/{run_id} does not bind its exact S09",
                exit_code=3,
            )
        source_digests[f"R01:{run_id}"] = r01_record["digest"]
        source_digests[
            f"S09:{preflight_instance_key}"
        ] = preflight_record["digest"]
        exact_runs.append({
            "run_id": run_id,
            "required_attempt_ids": sorted(attempt_ids),
        })
        for attempt_id in attempt_ids:
            if attempt_id in required_attempts:
                raise CLIError(
                    "scientific_plan_invalid",
                    f"Attempt {attempt_id} appears under multiple Runs",
                    exit_code=3,
                )
            required_attempts.add(attempt_id)
            declared = declared_by_id.get(attempt_id)
            if declared is None or declared.get("run_id") != run_id:
                raise CLIError(
                    "scientific_accounting_incomplete",
                    f"Attempt {attempt_id} is absent or bound to the wrong Run in D01",
                    exit_code=3,
                )
            r07, r07_record = _validated_stored_artifact(
                state, args.workspace, bundle, "R07", attempt_id,
            )
            r08, r08_record = _validated_stored_artifact(
                state, args.workspace, bundle, "R08", attempt_id,
            )
            actual = r07.get("content", r07)
            index = r08.get("content", r08)
            if (
                actual.get("run_id") != run_id
                or actual.get("attempt_id") != attempt_id
                or index.get("run_id") != run_id
                or index.get("attempt_id") != attempt_id
            ):
                raise CLIError(
                    "attempt_subject_mismatch",
                    f"R07/R08 do not bind exact Run/Attempt {run_id}/{attempt_id}",
                    exit_code=3,
                )
            attempt_kind = actual.get("attempt_kind")
            if index.get("attempt_kind") != attempt_kind:
                raise CLIError(
                    "attempt_kind_mismatch",
                    f"R07/R08 disagree on Attempt kind for {attempt_id}",
                    exit_code=3,
                )
            if attempt_kind in {"training", "hpo_trial"}:
                if any(name in actual for name in (
                    "evidence_summary", "statistical_analysis",
                    "failure_case_index", "scientific_facts",
                )):
                    raise CLIError(
                        "training_scientific_fact_forbidden",
                        f"training/HPO Attempt {attempt_id} contaminates D03/D05 scientific facts",
                        exit_code=3,
                    )
            elif attempt_kind == "locked_evaluation":
                selected_attempt = actual.get(
                    "selected_checkpoint_attempt_id",
                )
                training_actual, training_actual_record = \
                    _validated_stored_artifact(
                        state, args.workspace, bundle, "R07",
                        selected_attempt,
                    )
                training_index, training_index_record = \
                    _validated_stored_artifact(
                        state, args.workspace, bundle, "R08",
                        selected_attempt,
                    )
                training_actual_content = training_actual.get(
                    "content", training_actual,
                )
                training_index_content = training_index.get(
                    "content", training_index,
                )
                if (
                    training_actual_content.get("attempt_kind") != "training"
                    or training_index_content.get("attempt_kind") != "training"
                    or actual.get("training_r07_digest")
                    != training_actual_record["digest"]
                    or actual.get("training_r08_digest")
                    != training_index_record["digest"]
                    or index.get("training_r07_digest")
                    != training_actual_record["digest"]
                    or index.get("training_r08_digest")
                    != training_index_record["digest"]
                    or actual.get("evaluated_checkpoint_digest")
                    != training_actual_content.get("checkpoint_digest")
                    or actual.get("evaluated_model_artifact_digest")
                    != training_actual_content.get("model_artifact_digest")
                    or index.get("evaluated_checkpoint_digest")
                    != training_index_content.get("checkpoint_digest")
                    or index.get("evaluated_model_artifact_digest")
                    != training_index_content.get("model_artifact_digest")
                ):
                    raise CLIError(
                        "evaluation_model_binding_mismatch",
                        f"locked evaluation {attempt_id} does not bind its exact training checkpoint/model",
                        exit_code=3,
                    )
                locked_authors = actual.get("author_principal_ids")
                if (
                    not isinstance(locked_authors, list)
                    or not locked_authors
                    or index.get("author_principal_ids") != locked_authors
                ):
                    raise CLIError(
                        "evaluation_authorship_invalid",
                        f"locked evaluation {attempt_id} lacks exact verified producer principals",
                        exit_code=3,
                    )
                scientific_author_ids.update(locked_authors)
                scientific_attempt_ids.append(attempt_id)
            else:
                raise CLIError(
                    "attempt_kind_invalid",
                    f"Attempt {attempt_id} has no governed training/evaluation kind",
                    exit_code=3,
                )
            terminal_status = actual.get("status")
            validity = actual.get("validity")
            system_failure = actual.get("system_failure")
            if (
                terminal_status not in {"successful", "failed", "cancelled", "invalid"}
                or validity not in {"valid", "invalid"}
                or not isinstance(system_failure, bool)
                or declared.get("accounted") is not True
            ):
                raise CLIError(
                    "scientific_accounting_incomplete",
                    f"Attempt {attempt_id} is not canonically terminal and accounted",
                    exit_code=3,
                )
            if any(
                declared.get(key) != value
                for key, value in (
                    ("terminal_status", terminal_status),
                    ("validity", validity),
                    ("system_failure", system_failure),
                )
            ):
                raise CLIError(
                    "attempt_fact_mismatch",
                    f"D01 substitutes terminal facts for Attempt {attempt_id}",
                    exit_code=3,
                )
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
    if set(declared_by_id) != required_attempts:
        raise CLIError(
            "scientific_accounting_incomplete",
            "D01 Attempt set does not exactly equal the frozen Run plan",
            exit_code=3,
        )
    if set(plan_by_id) != {row["run_id"] for row in exact_runs}:
        raise CLIError(
            "evidence_run_set_mismatch",
            "D01 Run inventory does not exactly equal frozen E04",
            exit_code=3,
        )
    exact_scientific_attempts = sorted(scientific_attempt_ids)
    if (
        not exact_scientific_attempts
        or d01.get("scientific_attempt_ids") != exact_scientific_attempts
        or any(
            evidence_values[artifact_id].get("source_attempt_ids")
            != exact_scientific_attempts
            for artifact_id in ("D02", "D03", "D04")
        )
    ):
        raise CLIError(
            "scientific_source_attempt_mismatch",
            "D01-D04 must name exactly the locked-evaluation Attempts that produced scientific facts",
            exit_code=3,
        )
    exact_scientific_authors = sorted(scientific_author_ids)
    if (
        not exact_scientific_authors
        or any(
            evidence_values[artifact_id].get("author_principal_ids")
            != exact_scientific_authors
            for artifact_id in ("D01", "D02", "D03", "D04")
        )
    ):
        raise CLIError(
            "evidence_authorship_mismatch",
            "D01-D04 authors must exactly equal verified locked-evaluation producer principals",
            exit_code=3,
        )
    build_sources = {
        f"E01:{args.experiment_id}.frozen": frozen_record["digest"],
        f"E04:{run_plan_id}": run_plan_record["digest"],
        **scientific_anchors["source_refs"],
        **{
            key: value for key, value in source_digests.items()
            if ":" in key
        },
    }
    if (
        sorted(d01.get("items", [])) != sorted(build_sources)
        or d01.get("source_digests") != build_sources
        or d01.get("bundle_digest") != digest(build_sources)
    ):
        raise CLIError(
            "evidence_bundle_binding_mismatch",
            "D01 does not bind the exact governed Run/Attempt source set",
            exit_code=3,
        )
    evidence_event_hash = evidence_records["D01"].get("event_hash")
    if any(
        record.get("event_hash") != evidence_event_hash
        for record in evidence_records.values()
    ):
        raise CLIError(
            "evidence_transaction_mismatch",
            "D01-D04 were not committed by one atomic evidence event",
            exit_code=3,
        )
    evidence_events = [
        event for event in replayed.events
        if event.get("event_hash") == evidence_event_hash
    ]
    if (
        len(evidence_events) != 1
        or evidence_events[0].get("event_type") != "evidence.built"
        or evidence_events[0].get("payload", {}).get("complete") is not True
        or evidence_events[0].get("payload", {}).get("source_digests")
        != build_sources
    ):
        raise CLIError(
            "evidence_provenance_invalid",
            "D01-D04 lack one exact complete evidence.built provenance event",
            exit_code=3,
        )
    evidence = evidence_values["D03"].get("scientific_facts")
    if not isinstance(evidence, dict):
        raise CLIError(
            "scientific_evidence_missing",
            "governed D03 must carry exact platform-produced scientific_facts",
            exit_code=3,
        )
    calibrations = []
    for node, expected in frozen_content.get("contracts", {}).get("source_digests", {}).items():
        if node.startswith("G05:"):
            value, record = _validated_stored_artifact(state, args.workspace, bundle, "G05", node.split(":", 1)[1])
            if record["digest"] != expected:
                raise CLIError("calibration_frozen_binding_mismatch", "Scientific decision must use the exact frozen G05", exit_code=3)
            calibrations.append(value.get("content", value))
    _require_diagnostic_calibration_facts(calibrations, evidence)
    return {
        "frozen_rules": frozen_rules,
        "planned_runs": exact_runs,
        "attempts": sorted(attempts, key=lambda item: item["attempt_id"]),
        "evidence": evidence,
    }, source_digests, frozen_record["digest"]


def _do_scientific_decision(
    args: argparse.Namespace,
) -> tuple[dict[str, Any], int]:
    replayed, state, bundle = _workspace_state(args.workspace)
    if args.expected_revision != replayed.revision:
        raise WorkspaceError(
            "cas_conflict",
            f"expected revision {args.expected_revision}, actual {replayed.revision}",
            exit_code=4,
        )
    _require_current_lifecycle_step(state, 20, "scientific-decision")
    context, source_digests, frozen_spec_digest = _governed_decision_context(
        args, state=state, bundle=bundle, replayed=replayed,
    )
    try:
        evaluated = evaluate_scientific_decision(
            context["frozen_rules"], context["planned_runs"],
            context["attempts"], context["evidence"],
        )
    except ContractError as exc:
        exit_code = 3 if exc.code in {
            "scientific_accounting_incomplete", "scientific_attempt_invalid",
        } else 2
        raise CLIError(exc.code, str(exc), exit_code=exit_code) from exc
    logical_operation_id = (
        args.operation_id or "specctl-" + digest({
            "command": "specctl.scientific-decision",
            "experiment_id": args.experiment_id,
            "evidence_id": args.evidence_id,
            "decision_id": args.decision_id,
        }).split(":", 1)[1][:40]
    )
    intent_digest = digest({
        "experiment_id": args.experiment_id,
        "evidence_id": args.evidence_id,
        "decision_id": args.decision_id,
        "source_digests": source_digests,
        "rule_digest": evaluated["rule_digest"],
        "outcome": evaluated["outcome"],
    })
    provisional, accepted, terminal = _find_anchor_phase(
        replayed,
        provisional_type="decision.provisional",
        terminal_type="decision.accepted",
        logical_operation_id=logical_operation_id,
    )
    if terminal is not None:
        return _result(_command(args), replayed.revision, {
            "decision_id": args.decision_id,
            "outcome": evaluated["outcome"],
            "event_hash": terminal["event_hash"],
            "idempotent": True,
        }), 0
    if provisional is not None:
        if provisional["payload"].get("intent_digest") != intent_digest:
            raise CLIError(
                "idempotency_conflict",
                "decision operation was already used with different governed facts",
                exit_code=4,
            )
        request_id = provisional["payload"]["anchor_request_id"]
        record = state.get("handoffs", {}).get(request_id, {})
        if accepted is None:
            if record.get("status") not in {"successful", "reconciled_effect"}:
                packet = read_document(
                    Path(args.workspace) / "packets" / "S12" / f"{request_id}.json"
                )
                return _anchor_wait_result(
                    _command(args), replayed.revision, provisional, [{
                        "request_id": request_id,
                        "operation": packet["operation"],
                        "outer_skill_binding": packet["binding_identity"],
                        "binding_skill_digest": packet["binding_skill_digest"],
                        "adapter_contract_digest": packet["adapter_contract_digest"],
                        "packet_path": f"packets/S12/{request_id}.json",
                        "subjects": packet["subjects"],
                        "disposition": record.get("status", "created"),
                    }],
                )
            result = _validated_persisted_anchor_result(
                args.workspace,
                str(Path(args.workspace) / "packets" / "S13" / f"{request_id}.json"),
                state=state,
                request_id=request_id,
                provisional_hash=provisional["event_hash"],
                anchor_operation_id=provisional["payload"]["anchor_operation_id"],
            )
            accepted_mutation = mutate(
                args.workspace,
                expected_revision=args.expected_revision,
                operation_id=_phase_operation_id(
                    logical_operation_id, "decision-accepted-anchor",
                ),
                event_type="anchor.accepted",
                subjects={
                    "workspace_id": replayed.genesis["workspace_id"],
                    "experiment_id": args.experiment_id,
                    "artifact_id": f"D05:{args.experiment_id}.{args.decision_id}",
                },
                payload={
                    "provisional_hash": provisional["event_hash"],
                    "anchored_head": provisional["event_hash"],
                    "receipt_digest": digest(result),
                    "request_id": request_id,
                    "result_digest": digest(result),
                },
            )
            accepted = accepted_mutation.event
            revision = accepted_mutation.revision
        else:
            revision = replayed.revision
        terminal_mutation = mutate(
            args.workspace,
            expected_revision=revision,
            operation_id=_phase_operation_id(
                logical_operation_id, "decision-terminal",
            ),
            event_type="decision.accepted",
            subjects={
                "workspace_id": replayed.genesis["workspace_id"],
                "experiment_id": args.experiment_id,
                "evidence_id": args.evidence_id,
                "decision_id": args.decision_id,
                "artifact_id": f"D05:{args.experiment_id}.{args.decision_id}",
            },
            payload={
                "transition": "decision.accept",
                "authority_action": "decision.D05",
                "authority_inherited_from": provisional["event_hash"],
                "outcome": evaluated["outcome"],
                "transition_target": evaluated["outcome"],
                "step": 20,
                "lifecycle_step_completion": True,
                "outputs": ["D05"],
                "logical_operation_id": logical_operation_id,
                "intent_digest": intent_digest,
                "provisional_hash": provisional["event_hash"],
                "anchor_accepted_event": accepted["event_hash"],
                "anchor_request_id": request_id,
            },
        )
        return _result(_command(args), terminal_mutation.revision, {
            "decision_id": args.decision_id,
            "outcome": evaluated["outcome"],
            "event_hash": terminal_mutation.event["event_hash"],
            "artifact": f"artifacts/D05/{args.experiment_id}.{args.decision_id}.json",
        }), 0
    if state.get("pending_anchor"):
        raise CLIError(
            "anchor_phase_conflict",
            "another formal transition already has a pending anchor",
            exit_code=3,
        )

    evidence_bundle_digest = digest({
        key: source_digests[key] for key in sorted(source_digests)
        if key.startswith("D") or key.startswith("R")
    })
    provenance = _decision_authority_provenance_extension(
        state=state, workspace=args.workspace, bundle=bundle,
        replayed=replayed, experiment_id=args.experiment_id,
        evidence_id=args.evidence_id,
    )
    subject = {
        "governed_ids": {
            "workspace_id": replayed.genesis["workspace_id"],
            "experiment_id": args.experiment_id,
            "evidence_id": args.evidence_id,
            "decision_id": args.decision_id,
            "outcome": evaluated["outcome"],
        },
        "digests": {
            "frozen_spec_digest": frozen_spec_digest,
            "evidence_bundle_digest": evidence_bundle_digest,
            "rule_digest": evaluated["rule_digest"],
        },
        **provenance,
    }
    authority = _authority(
        {},
        action="decision.D05",
        subject=subject,
        revision=replayed.revision,
        bundle=bundle,
        replayed=replayed,
        attestation_value=read_document(args.attestation),
    )
    reviewers = sorted({item["principal_id"] for item in authority})
    authority_ids = sorted({
        str(item.get("attestation_digest") or item.get("principal_id"))
        for item in authority
    })
    decision_content = {
        "decision_id": args.decision_id,
        "experiment_id": args.experiment_id,
        "frozen_spec_digest": frozen_spec_digest,
        "evidence_bundle_digest": evidence_bundle_digest,
        "rule_digest": evaluated["rule_digest"],
        "outcome": evaluated["outcome"],
        "reviewers": reviewers,
        "authority": authority_ids,
        "decision_context": context,
    }
    instance_key = f"{args.experiment_id}.{args.decision_id}"
    decision = _envelope(
        "D05", args.decision_id, instance_key, decision_content,
        {
            "workspace_id": replayed.genesis["workspace_id"],
            "experiment_id": args.experiment_id,
        },
        # D05 bytes are immutable at creation.  The ledger remains
        # provisional until its exact external anchor is accepted; the
        # artifact envelope itself therefore uses the canonical published
        # status rather than inventing a schema-external status value.
        status="published",
    )
    findings = bundle.validate_artifact("D05", decision)
    if findings:
        raise CLIError(
            "scientific_decision_invalid",
            findings[0]["message"], findings=findings,
        )
    relative = f"artifacts/D05/{instance_key}.json"
    anchor_operation_id = _phase_operation_id(
        logical_operation_id, "decision-anchor",
    )
    request_seed = {
        "workspace_id": replayed.genesis["workspace_id"],
        "experiment_id": args.experiment_id,
        "decision_id": args.decision_id,
        "logical_operation_id": logical_operation_id,
        "intent_digest": intent_digest,
    }
    request_id = "REQ-" + digest({
        **request_seed, "operation": "git.anchor-ledger-head",
    }).split(":", 1)[1][:32]
    artifact_record = {
        "artifact_id": "D05",
        "instance_key": instance_key,
        "path": relative,
        "digest": digest(decision),
        "parents": [
            f"E01:{args.experiment_id}.frozen",
            *[f"{artifact_id}:{args.evidence_id}" for artifact_id in (
                "D01", "D02", "D03", "D04",
            )],
        ],
        "status": "published",
    }
    provisional_mutation = mutate(
        args.workspace,
        expected_revision=args.expected_revision,
        operation_id=logical_operation_id,
        event_type="decision.provisional",
        subjects={
            "workspace_id": replayed.genesis["workspace_id"],
            "experiment_id": args.experiment_id,
            "evidence_id": args.evidence_id,
            "decision_id": args.decision_id,
            "artifact_id": f"D05:{instance_key}",
        },
        payload={
            "transition": "decision.provisional",
            "outcome": evaluated["outcome"],
            "logical_operation_id": logical_operation_id,
            "intent_digest": intent_digest,
            "anchor_operation_id": anchor_operation_id,
            "anchor_request_id": request_id,
            "authority_action": "decision.D05",
            "authority_subject": subject,
            "authority_expected_revision": replayed.revision,
            "consumed_at": authority[0]["consumed_at"],
            "published_artifacts": [artifact_record],
        },
        authority=authority,
        artifact_writes=((relative, decision, True),),
    )
    _, requests = _ensure_platform_requests(
        args.workspace,
        replayed=replayed,
        state=provisional_mutation.state,
        bundle=bundle,
        operations=["git.anchor-ledger-head"],
        gate="decision.accept",
        step=20,
        subjects={
            "experiment_id": args.experiment_id,
            "artifact_id": f"D05:{instance_key}",
        },
        expected_revision=provisional_mutation.revision,
        seed=request_seed,
    )
    if requests[0]["request_id"] != request_id:
        raise CLIError(
            "anchor_request_identity_mismatch",
            "D05 provisional and S12 identities diverged",
            exit_code=70,
        )
    blocked = finding(
        "external_anchor_required",
        "D05 remains provisional until its exact external head anchor is accepted",
        subject=args.decision_id,
        governing_asset="transition-requirements.json",
    )
    return _result(
        _command(args), replay(args.workspace).revision,
        {
            "decision_id": args.decision_id,
            "outcome": evaluated["outcome"],
            "artifact": relative,
            "provisional_head": provisional_mutation.event["event_hash"],
            "anchor_request_id": request_id,
            "handoffs": requests,
        },
        [blocked], status="blocked",
    ), 3


def _do_validate_transition(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    replayed, state, bundle = _workspace_state(args.workspace)
    payload = _payload(args.payload)
    _reject_governed_payload_claims(payload)
    transition = bundle.transition(args.transition)
    passed, missing, events = _transition_gate(
        args.transition, transition, state,
    )
    status = "pass" if passed else "blocked"
    findings = [] if passed else [finding("transition_unsatisfied", "transition prerequisites are unsatisfied", subject=args.transition, governing_asset="transition-requirements.json")]
    return _result(_command(args), replayed.revision, {"valid": passed, "missing": sorted(set(missing)), "substate_events": events}, findings, status=status), 0 if passed else 3


def _validated_interrupt_artifact(
    *,
    payload: dict[str, Any],
    artifact_id: str,
    workspace_id: str,
    bundle: AssetBundle,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    value = payload.get("record")
    if not isinstance(value, dict):
        raise CLIError(
            "governed_record_required",
            f"public transition requires exact {artifact_id} record bytes",
            exit_code=3,
        )
    content = value.get("content", value)
    findings = bundle.validate_artifact(artifact_id, value)
    if isinstance(content, dict) and content.get("authority") == []:
        # Authority-gated records use a two-object contract.  The caller signs
        # the exact unsigned candidate (authority=[]); after verification the
        # coordinator inserts consumed attestation digests, recomputes the
        # content/envelope digest, and performs full validation again.  The
        # deliberately absent authority field, including D05's temporary
        # reviewer/authority cardinality mismatch, is not a malformed candidate.
        findings = [
            item for item in findings
            if not (
                (
                    item.get("code") == "semantic_required"
                    and item.get("json_pointer") == "/content/authority"
                )
                or (
                    artifact_id == "D05"
                    and item.get("code")
                    == "scientific_decision_authority_invalid"
                )
            )
        ]
    if findings:
        raise CLIError(
            "governed_record_invalid", findings[0]["message"],
            findings=findings,
        )
    instance_key = value.get("instance_key")
    if (
        value.get("artifact_type") != artifact_id
        or not isinstance(instance_key, str)
        or not _PORTABLE_INSTANCE.fullmatch(instance_key)
    ):
        raise CLIError(
            "governed_record_identity_invalid",
            f"{artifact_id} requires one portable exact instance identity",
        )
    subjects = _subjects(payload, workspace_id)
    envelope_subjects = {"workspace_id": workspace_id, **subjects}
    if value.get("subjects") != envelope_subjects:
        raise CLIError(
            "governed_record_subject_mismatch",
            f"{artifact_id} envelope subjects do not equal command subjects",
            exit_code=3,
        )
    if not isinstance(content, dict) or content.get("authority") != []:
        raise CLIError(
            "unsigned_governance_candidate_required",
            f"{artifact_id} input must carry authority=[]; the coordinator binds verified attestations into the final immutable record",
            exit_code=3,
        )
    return value, content, subjects


def _stored_baseline_governance_context(
    *,
    state: dict[str, Any],
    workspace: str,
    bundle: AssetBundle,
) -> tuple[set[str], list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Return authenticated baseline identities, B04 bytes, and node digests."""

    identities: set[str] = set()
    deprecations: list[dict[str, Any]] = []
    records_by_identity: dict[str, dict[str, str]] = {}
    for artifact_id in ("B01", "B02", "B04"):
        instances = state.get("artifacts", {}).get(artifact_id, {})
        for instance_key in sorted(instances):
            value, record = _validated_stored_artifact(
                state, workspace, bundle, artifact_id, instance_key,
            )
            content = value.get("content", value)
            subjects = value.get("subjects", {})
            if artifact_id in {"B01", "B02"}:
                candidates = {
                    instance_key,
                    content.get("baseline_id") if isinstance(content, dict)
                    else None,
                    subjects.get("baseline_id")
                    if isinstance(subjects, dict) else None,
                }
                for identity in sorted(
                    item for item in candidates
                    if isinstance(item, str) and item
                ):
                    identities.add(identity)
                    records_by_identity.setdefault(identity, {})[
                        f"{artifact_id}:{instance_key}"
                    ] = record["digest"]
            elif isinstance(value, dict):
                deprecations.append(value)
    return identities, deprecations, records_by_identity


def _active_baseline_dependents(
    *,
    state: dict[str, Any],
    workspace: str,
    bundle: AssetBundle,
    baseline_id: str,
) -> list[str]:
    superseded = {
        edge.get("from")
        for edge in state.get("lineage", {}).get("edges", [])
        if isinstance(edge, dict) and edge.get("relation") == "superseded-by"
    }
    dependents: list[str] = []
    for instance_key in sorted(state.get("artifacts", {}).get("E01", {})):
        if f"E01:{instance_key}" in superseded:
            continue
        value, _ = _validated_stored_artifact(
            state, workspace, bundle, "E01", instance_key,
        )
        content = value.get("content", value)
        reference = content.get("baseline") if isinstance(content, dict) else None
        if isinstance(reference, dict):
            reference = reference.get("lock_ref")
        referenced_identity = (
            reference.rsplit(":", 1)[-1]
            if isinstance(reference, str) else None
        )
        if referenced_identity == baseline_id:
            dependents.append(instance_key)
    return dependents




def _unique_decision_evidence_match(
    matches: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(matches) != 1:
        code = "decision_evidence_binding_ambiguous" \
            if len(matches) > 1 else "decision_evidence_binding_invalid"
        raise CLIError(
            code,
            "successor D05 must bind exactly one replay-authenticated common D01-D04 evidence bundle",
            exit_code=3,
        )
    return matches[0]


def _resolved_decision_evidence_match(
    *,
    args: argparse.Namespace,
    state: dict[str, Any],
    bundle: AssetBundle,
    replayed: Any,
    experiment_id: str,
    decision_content: dict[str, Any],
) -> dict[str, Any]:
    instances = [
        set(state.get("artifacts", {}).get(artifact_id, {}))
        for artifact_id in ("D01", "D02", "D03", "D04")
    ]
    common = set.intersection(*instances) if instances else set()
    matches: list[dict[str, Any]] = []
    for evidence_id in sorted(common):
        d01, _ = _validated_stored_artifact(
            state, args.workspace, bundle, "D01", evidence_id,
        )
        d01_content = d01.get("content", d01)
        if (
            not isinstance(d01_content, dict)
            or d01_content.get("experiment_id") != experiment_id
            or d01_content.get("evidence_id") != evidence_id
        ):
            continue
        context, source_digests, frozen_digest = \
            _governed_decision_context(
                argparse.Namespace(
                    workspace=args.workspace,
                    experiment_id=experiment_id,
                    evidence_id=evidence_id,
                ),
                state=state, bundle=bundle, replayed=replayed,
            )
        evidence_digest = digest({
            key: source_digests[key] for key in sorted(source_digests)
            if key.startswith("D") or key.startswith("R")
        })
        if (
            decision_content.get("decision_context") == context
            and decision_content.get("frozen_spec_digest") == frozen_digest
            and decision_content.get("evidence_bundle_digest")
            == evidence_digest
            and decision_content.get("rule_digest")
            == digest(context["frozen_rules"])
        ):
            matches.append({
                "evidence_id": evidence_id,
                "parents": [
                    f"{artifact_id}:{evidence_id}"
                    for artifact_id in ("D01", "D02", "D03", "D04")
                ],
            })
    return _unique_decision_evidence_match(matches)




def _virtual_artifact_context(
    state: dict[str, Any], artifact_id: str, instance_key: str,
    value: dict[str, Any], checks: dict[str, bool],
) -> dict[str, Any]:
    context = deepcopy(state)
    context.setdefault("artifacts", {}).setdefault(artifact_id, {})[
        instance_key
    ] = {
        "digest": digest(value),
        "path": f"artifacts/{artifact_id}/{instance_key}.json",
        "status": value.get("status", "published"),
    }
    checks = {artifact_id: True, **checks}
    context.setdefault("checks", {}).update(checks)
    return context


_TERMINAL_HANDOFF_DISPOSITIONS = {
    "successful", "cancelled", "superseded", "superseded_by_retry",
    "reconciled_no_effect", "reconciled_effect",
}
_UNCERTAIN_HANDOFF_DISPOSITIONS = {
    "dispatched", "reconciliation_required",
}


def _governed_child_disposition_events(
    *,
    state: dict[str, Any],
    dispositions: Any,
    workspace_id: str,
    logical_operation_id: str,
    governance_node: str,
    reason: str,
) -> list[dict[str, Any]]:
    """Compile exact, safe child cancellation into the parent transaction.

    A dispatched or reconciliation-required request may already have caused an
    external effect.  A lifecycle cancellation/supersession therefore cannot
    relabel it locally; the exact correlation must first be reconciled.  A
    request that never dispatched, is configuration-required, or has an exact
    producer failure/cancellation may be closed by appending a distinct
    ``handoff.cancelled`` event.  The prior S12/S13 bytes remain immutable.
    """

    if not isinstance(dispositions, dict):
        raise CLIError(
            "child_dispositions_incomplete",
            "governance action must map every live handoff to an exact disposition",
            exit_code=3,
        )
    handoffs = state.get("handoffs", {})
    live = {
        request_id: record
        for request_id, record in handoffs.items()
        if isinstance(record, dict)
        and record.get("status") not in _TERMINAL_HANDOFF_DISPOSITIONS
    }
    declared_children = set(dispositions)
    if declared_children != set(live):
        raise CLIError(
            "child_dispositions_incomplete",
            "child dispositions must equal the complete live handoff identity set",
            exit_code=3,
        )
    events: list[dict[str, Any]] = []
    for request_id in sorted(live):
        record = live[request_id]
        status = record.get("status")
        if status in _UNCERTAIN_HANDOFF_DISPOSITIONS:
            raise CLIError(
                "uncertain_child_requires_reconciliation",
                "a dispatched or uncertain handoff must be reconciled before lifecycle disposition",
                exit_code=3,
            )
        disposition = dispositions[request_id]
        if disposition != "cancelled":
            raise CLIError(
                "child_disposition_invalid",
                "a live child may be closed here only by an exact cancelled disposition",
                exit_code=3,
            )
        events.append({
            "operation_id": _phase_operation_id(
                logical_operation_id, f"cancel-child-{request_id}",
            ),
            "event_type": "handoff.cancelled",
            "subjects": {
                "workspace_id": workspace_id,
                "request_id": request_id,
            },
            "payload": {
                "request_id": request_id,
                "reason": reason,
                "governance_node": governance_node,
                "prior_status": status,
            },
        })
    return events


def _do_attempt_retry(
    args: argparse.Namespace,
    *, payload: dict[str, Any], replayed: Any, state: dict[str, Any],
    bundle: AssetBundle,
) -> tuple[dict[str, Any], int]:
    """Create a new planned Attempt only under the frozen retry policy."""

    _reject_governed_payload_claims(
        payload,
        allowed={"subjects", "predecessor_attempt_id", "successor_attempt_id"},
    )
    if state.get("lifecycle_step") not in {15, 16}:
        raise CLIError(
            "attempt_retry_step_invalid",
            "Attempt retry is available only while STEP15/16 awaits a successful Attempt",
            exit_code=3,
        )
    subjects = _subjects(payload, replayed.genesis["workspace_id"])
    run_id = subjects.get("run_id")
    experiment_id = subjects.get("experiment_id")
    predecessor_id = payload.get("predecessor_attempt_id")
    successor_id = payload.get("successor_attempt_id")
    if not all(
        isinstance(value, str) and value
        for value in (run_id, experiment_id, predecessor_id, successor_id)
    ):
        raise CLIError(
            "attempt_retry_subjects_invalid",
            "retry requires exact Experiment/Run and predecessor/successor Attempt identities",
            exit_code=3,
        )
    prior = state.get("attempts", {}).get(predecessor_id)
    if (
        not isinstance(prior, dict)
        or prior.get("run_id") != run_id
        or prior.get("status") not in {"failed", "cancelled", "invalid"}
        or successor_id in state.get("attempts", {})
    ):
        raise CLIError(
            "attempt_retry_source_invalid",
            "retry requires one retained failed/cancelled/invalid predecessor and a new identity",
            exit_code=3,
        )
    manifest, _ = _validated_stored_artifact(
        state, args.workspace, bundle, "R01", run_id,
    )
    manifest_content = manifest.get("content", manifest)
    policy = manifest_content.get("attempt_policy")
    planned_attempts = manifest_content.get("required_attempt_ids")
    if (
        manifest_content.get("experiment_id") != experiment_id
        or not isinstance(policy, dict)
        or not isinstance(planned_attempts, list)
        or predecessor_id not in planned_attempts
        or successor_id not in planned_attempts
    ):
        raise CLIError(
            "frozen_retry_policy_mismatch",
            "retry identities are not both present in the exact frozen R01/E04 policy",
            exit_code=3,
        )
    retry_on = policy.get("retry_on")
    maximum = policy.get("max_attempts")
    used = [
        attempt for attempt in state.get("attempts", {}).values()
        if isinstance(attempt, dict) and attempt.get("run_id") == run_id
    ]
    if (
        not isinstance(retry_on, list)
        or prior["status"] not in retry_on
        or not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or maximum < 1
        or len(used) >= maximum
    ):
        raise CLIError(
            "frozen_retry_policy_denied",
            "the exact frozen retry policy does not permit another Attempt for this terminal status",
            exit_code=3,
        )
    event_subjects = {
        "workspace_id": replayed.genesis["workspace_id"],
        "experiment_id": experiment_id,
        "run_id": run_id,
        "attempt_id": predecessor_id,
    }
    mutation = mutate(
        args.workspace,
        expected_revision=args.expected_revision,
        operation_id=_operation_id(args, {
            "run_id": run_id, "predecessor": predecessor_id,
            "successor": successor_id,
        }),
        event_type="attempt.retry-created",
        subjects=event_subjects,
        payload={
            "transition": "attempt.retry",
            "successor_attempt_id": successor_id,
            "frozen_attempt_policy_digest": digest(policy),
            "prior_terminal_event": prior.get("terminal_event"),
        },
    )
    return _result(
        _command(args), mutation.revision,
        {
            "run_id": run_id,
            "predecessor_attempt_id": predecessor_id,
            "successor_attempt_id": successor_id,
            "disposition": "planned",
        },
    ), 0


def _do_governed_interrupt(
    args: argparse.Namespace,
    *,
    action: str,
    payload: dict[str, Any],
    replayed: Any,
    state: dict[str, Any],
    bundle: AssetBundle,
) -> tuple[dict[str, Any], int]:
    command = _command(args)
    workspace_id = replayed.genesis["workspace_id"]
    child_events: list[dict[str, Any]] = []
    supersession_kind = (
        payload.get("supersession_kind", "experiment")
        if action == "supersede" else None
    )
    if supersession_kind not in {None, "experiment", "decision"}:
        raise CLIError(
            "supersession_kind_invalid",
            "supersession_kind must be experiment or decision",
            exit_code=3,
        )
    transition_names = {
        "amend": "protocol.amend",
        "deviate": "protocol.deviate",
        "deprecate": "baseline.deprecate",
        "cancel": "lifecycle.cancel",
        "exception": "lifecycle.exception",
    }
    transition_name = (
        f"{supersession_kind}.supersede"
        if action == "supersede" else transition_names[action]
    )
    transition = bundle.transition(transition_name)
    authority_action = transition.get("authority_action")
    authority_extra = payload.get("authority_subject", {})
    if (
        not isinstance(authority_extra, dict)
        or {"governed_ids", "digests"}.intersection(authority_extra)
    ):
        raise CLIError(
            "authority_subject_override",
            "authority_subject cannot replace governed IDs or digests",
        )

    if action == "supersede" and supersession_kind == "experiment":
        _reject_governed_payload_claims(
            payload,
            allowed={
                "successor", "successor_brief",
                "predecessor_instance_key",
                "predecessor_decision_instance_key", "subjects",
                "attestations", "authority_subject",
                "child_dispositions",
                "supersession_kind",
            },
        )
        successor = payload.get("successor")
        successor_brief = payload.get("successor_brief")
        predecessor_key = payload.get("predecessor_instance_key")
        predecessor_decision_key = payload.get(
            "predecessor_decision_instance_key",
        )
        if (
            not isinstance(successor, dict)
            or not isinstance(successor_brief, dict)
            or not isinstance(predecessor_key, str)
            or not isinstance(predecessor_decision_key, str)
        ):
            raise CLIError(
                "successor_record_required",
                "supersede requires exact successor E01/S02 bytes and predecessor E01/D05 identities",
                exit_code=3,
            )
        successor_findings = bundle.validate_artifact("E01", successor)
        if successor_findings:
            raise CLIError(
                "successor_record_invalid", successor_findings[0]["message"],
                findings=successor_findings,
            )
        brief_findings = bundle.validate_artifact("S02", successor_brief)
        if brief_findings:
            raise CLIError(
                "successor_brief_invalid", brief_findings[0]["message"],
                findings=brief_findings,
            )
        successor_key = successor.get("instance_key")
        brief_key = successor_brief.get("instance_key")
        if (
            not _PORTABLE_INSTANCE.fullmatch(predecessor_key)
            or not isinstance(successor_key, str)
            or not _PORTABLE_INSTANCE.fullmatch(successor_key)
            or not isinstance(brief_key, str)
            or not _PORTABLE_INSTANCE.fullmatch(brief_key)
            or not _PORTABLE_INSTANCE.fullmatch(predecessor_decision_key)
            or successor_key == predecessor_key
        ):
            raise CLIError(
                "successor_identity_invalid",
                "predecessor and successor require distinct portable identities",
                exit_code=3,
            )
        predecessor, predecessor_record = _validated_stored_artifact(
            state, args.workspace, bundle, "E01", predecessor_key,
        )
        predecessor_decision, predecessor_decision_record = \
            _validated_stored_artifact(
                state, args.workspace, bundle, "D05",
                predecessor_decision_key,
            )
        subjects = _subjects(payload, workspace_id)
        successor_content = successor.get("content", successor)
        brief_content = successor_brief.get("content", successor_brief)
        decision_content = predecessor_decision.get(
            "content", predecessor_decision,
        )
        successor_anchors = _validated_experiment_anchor_context(
            content=successor_content, state=state,
            workspace=args.workspace, bundle=bundle,
        )
        intrinsic_refs = {
            **successor_anchors["contracts"],
            successor_anchors["baseline_lock_ref"]:
                successor_anchors["baseline_lock_digest"],
            successor_anchors["dataset_manifest_ref"]:
                successor_anchors["dataset_manifest_digest"],
            successor_anchors["split_manifest_ref"]:
                successor_anchors["split_manifest_digest"],
            successor_anchors["evaluator_contract_ref"]:
                successor_anchors["evaluator_contract_digest"],
        }
        if successor_anchors["calibration_applicable"]:
            intrinsic_refs[
                successor_anchors["calibration_manifest_ref"]
            ] = successor_anchors["calibration_manifest_digest"]
        successor_experiment_id = successor_content.get("experiment_id")
        decision_node = f"D05:{predecessor_decision_key}"
        brief_node = f"S02:{brief_key}"
        decision_ref = {
            decision_node: predecessor_decision_record["digest"],
        }
        successor_refs = {
            **intrinsic_refs,
            **decision_ref,
            brief_node: digest(successor_brief),
        }
        if (
            successor.get("subjects") != {
                "workspace_id": workspace_id,
                "experiment_id": successor_experiment_id,
            }
            or successor_brief.get("subjects") != {
                "workspace_id": workspace_id,
                "experiment_id": successor_experiment_id,
            }
            or subjects.get("experiment_id")
            != predecessor.get("content", predecessor).get("experiment_id")
            or decision_content.get("experiment_id")
            != subjects.get("experiment_id")
            or decision_content.get("outcome") not in {
                "hypothesis_supported", "hypothesis_rejected",
                "inconclusive", "invalid",
            }
            or brief_content.get("source_refs") != decision_ref
            or successor_content.get("source_refs") != successor_refs
            or not isinstance(successor_experiment_id, str)
            or successor_experiment_id == subjects.get("experiment_id")
            or candidate_digest(successor) == candidate_digest(predecessor)
            or state.get("artifacts", {}).get("E01", {}).get(successor_key)
        ):
            raise CLIError(
                "successor_binding_invalid",
                "successor must be new, material, and exactly bind old/new Experiment subjects",
                exit_code=3,
            )
        relative = f"artifacts/E01/{successor_key}.json"
        brief_relative = f"artifacts/S02/{brief_key}.json"
        descriptor = {
            "artifact_id": "E01", "instance_key": successor_key,
            "path": relative, "digest": digest(successor),
            "parents": [
                f"E01:{predecessor_key}", decision_node, brief_node,
                *sorted(intrinsic_refs),
            ],
            "status": successor.get("status", "draft"),
        }
        brief_descriptor = {
            "artifact_id": "S02", "instance_key": brief_key,
            "path": brief_relative, "digest": digest(successor_brief),
            "parents": [decision_node],
            "status": successor_brief.get("status", "published"),
        }
        context = _virtual_artifact_context(
            state, "E01", successor_key, successor, {
                "material-change": True,
                "reciprocal-successor-links": True,
                "successor-new-id": True,
                "predecessor-byte-identical": True,
            },
        )
        record_content: dict[str, Any] | None = None
        authority_subject = {
            "governed_ids": {
                "workspace_id": workspace_id,
                "predecessor_experiment_id": subjects["experiment_id"],
                "successor_experiment_id": successor_experiment_id,
            },
            "digests": {
                "predecessor_digest": predecessor_record["digest"],
                "successor_digest": digest(successor),
                "predecessor_decision_digest":
                    predecessor_decision_record["digest"],
                "successor_brief_digest": digest(successor_brief),
            },
            **authority_extra,
        }
        event_payload = {
            "old_id": f"E01:{predecessor_key}",
            "new_id": f"E01:{successor_key}",
            "predecessor_instance_key": predecessor_key,
            "predecessor_decision_instance_key": predecessor_decision_key,
            "authority_bound_digests": deepcopy(
                authority_subject["digests"],
            ),
            "published_artifacts": [brief_descriptor, descriptor],
        }
        if state.get("lifecycle_step") == 21:
            event_payload.update({
                "step": 21,
                "lifecycle_step_completion": True,
                "outputs": ["E01", "S02"],
            })
        artifact_writes = (
            (brief_relative, successor_brief, True),
            (relative, successor, True),
        )
        event_subjects = {
            "workspace_id": workspace_id,
            "experiment_id": subjects["experiment_id"],
            "successor_experiment_id": successor_experiment_id,
        }
        child_events = _governed_child_disposition_events(
            state=state,
            dispositions=payload.get("child_dispositions", {}),
            workspace_id=workspace_id,
            logical_operation_id=_stable_lifecycle_operation_id(args, payload),
            governance_node=f"E01:{successor_key}",
            reason=f"experiment superseded by {successor_experiment_id}",
        )
    elif action == "supersede":
        _reject_governed_payload_claims(
            payload,
            allowed={
                "supersession_kind", "predecessor_decision_instance_key",
                "record", "supersession_reason",
                "subjects",
                "attestations", "authority_subject",
            },
        )
        artifact_id = "D05"
        record, record_content, subjects = _validated_interrupt_artifact(
            payload=payload, artifact_id=artifact_id,
            workspace_id=workspace_id, bundle=bundle,
        )
        predecessor_key = payload.get("predecessor_decision_instance_key")
        reason = payload.get("supersession_reason")
        instance_key = record["instance_key"]
        if (
            not isinstance(predecessor_key, str)
            or not _PORTABLE_INSTANCE.fullmatch(predecessor_key)
            or predecessor_key == instance_key
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise CLIError(
                "decision_supersession_identity_invalid",
                "decision supersession requires distinct portable old/new identities and a reason",
                exit_code=3,
            )
        predecessor, predecessor_record = _validated_stored_artifact(
            state, args.workspace, bundle, "D05", predecessor_key,
        )
        predecessor_content = predecessor.get("content", predecessor)
        experiment_id = subjects.get("experiment_id")
        predecessor_id = predecessor_content.get("decision_id") \
            if isinstance(predecessor_content, dict) else None
        successor_id = record_content.get("decision_id")
        predecessor_node = f"D05:{predecessor_key}"
        successor_node = f"D05:{instance_key}"
        already_superseded = any(
            edge.get("from") == predecessor_node
            and edge.get("relation") == "superseded-by"
            for edge in state.get("lineage", {}).get("edges", [])
            if isinstance(edge, dict)
        )
        outcomes = {
            "hypothesis_supported", "hypothesis_rejected",
            "inconclusive", "invalid",
        }
        if (
            not isinstance(predecessor_content, dict)
            or predecessor_content.get("experiment_id") != experiment_id
            or record_content.get("experiment_id") != experiment_id
            or predecessor_content.get("outcome") not in outcomes
            or record_content.get("outcome") not in outcomes
            or not isinstance(predecessor_id, str)
            or not isinstance(successor_id, str)
            or predecessor_id == successor_id
            or record.get("supersedes") != predecessor_node
            or record_content.get("supersession_reason") != reason
            or state.get("artifacts", {}).get("D05", {}).get(instance_key)
            or already_superseded
        ):
            raise CLIError(
                "decision_supersession_binding_invalid",
                "successor D05 must be new, reciprocal, terminal, and bound to the same Experiment and exact reason",
                exit_code=3,
            )
        evidence_match = _resolved_decision_evidence_match(
            args=args, state=state, bundle=bundle, replayed=replayed,
            experiment_id=experiment_id,
            decision_content=record_content,
        )
        relative = f"artifacts/D05/{instance_key}.json"
        descriptor = {
            "artifact_id": "D05", "instance_key": instance_key,
            "path": relative, "digest": digest(record),
            "parents": sorted({
                predecessor_node, *evidence_match["parents"],
            }),
            "status": record.get("status", "published"),
        }
        context = _virtual_artifact_context(
            state, "D05", instance_key, record, {
                "new-D05-id": True,
                "old-D05-unchanged": True,
                "supersession-reason": True,
            },
        )
        candidate_digest_value = digest(record)
        authority_subject = {
            "governed_ids": {
                "workspace_id": workspace_id,
                "experiment_id": experiment_id,
                "predecessor_decision_id": predecessor_id,
                "successor_decision_id": successor_id,
            },
            "digests": {
                "predecessor_D05_digest": predecessor_record["digest"],
                "D05_candidate_digest": candidate_digest_value,
            },
            **authority_extra,
        }
        event_payload = {
            "old_id": predecessor_node,
            "new_id": successor_node,
            "predecessor_decision_instance_key": predecessor_key,
            "successor_decision_instance_key": instance_key,
            "supersession_reason": reason,
            "transition_target": record_content["outcome"],
            "outcome": record_content["outcome"],
            "authority_bound_digests": deepcopy(
                authority_subject["digests"],
            ),
            "published_artifacts": [descriptor],
        }
        artifact_writes = ((relative, record, True),)
        event_subjects = {
            "workspace_id": workspace_id,
            "experiment_id": experiment_id,
            "decision_id": predecessor_id,
            "successor_decision_id": successor_id,
        }
    else:
        _reject_governed_payload_claims(
            payload,
            allowed={"record", "subjects", "attestations", "authority_subject"},
        )
        artifact_id = {
            "amend": "E06", "deviate": "E07",
            "deprecate": "B04",
            "cancel": "S16", "exception": "S15",
        }[action]
        record, record_content, subjects = _validated_interrupt_artifact(
            payload=payload, artifact_id=artifact_id,
            workspace_id=workspace_id, bundle=bundle,
        )
        instance_key = record["instance_key"]
        checks: dict[str, bool] = {}
        parents: list[str] = []
        if action in {"amend", "deviate"}:
            experiment_id = subjects.get("experiment_id")
            if record_content.get("experiment_id") != experiment_id:
                raise CLIError(
                    "governed_record_subject_mismatch",
                    f"{artifact_id} content does not bind command Experiment",
                    exit_code=3,
                )
            frozen, frozen_record = _validated_stored_artifact(
                state, args.workspace, bundle,
                "E01", f"{experiment_id}.frozen",
            )
            del frozen
            parents.append(f"E01:{experiment_id}.frozen")
            if action == "amend":
                first_run = next((
                    event for event in replayed.events
                    if event["event_type"] in {
                        "run.dispatched", "run.started", "attempt.started",
                    }
                    and event.get("subjects", {}).get("experiment_id")
                    == experiment_id
                ), None)
                if (
                    first_run is not None
                    or record_content.get("classification") != "non-material"
                    or record_content.get("old_digest")
                    != frozen_record["digest"]
                    or not isinstance(record_content.get("candidate_digest"), str)
                ):
                    raise CLIError(
                        "amendment_binding_invalid",
                        "E06 must be non-material, pre-dispatch, and reciprocal to exact frozen E01",
                        exit_code=3,
                    )
                checks = {
                    "before-first-affected-run-dispatch": True,
                    "non-material": True,
                    "E06-reciprocal-digests": True,
                    "frozen-bytes-unchanged": True,
                }
            else:
                affected = [
                    event for event in replayed.events
                    if event["event_type"] in {
                        "run.dispatched", "run.started", "attempt.started",
                    }
                    and event.get("subjects", {}).get("experiment_id")
                    == experiment_id
                ]
                first = affected[0] if affected else None
                if (
                    first is None
                    or record_content.get("first_affected_event")
                    != first["event_hash"]
                    or record_content.get("observed_facts") in (None, [], {})
                    or record_content.get("impact") in (None, [], {})
                ):
                    raise CLIError(
                        "deviation_binding_invalid",
                        "E07 must bind the first affected dispatch/start and exact observed facts",
                        exit_code=3,
                    )
                checks = {
                    "at-or-after-first-affected-run-dispatch": True,
                    "E07-observed-facts": True,
                    "impact-classified": True,
                    "frozen-bytes-unchanged": True,
                }
        elif action == "deprecate":
            baseline_id = subjects.get("baseline_id")
            replacement = record_content.get("replacement")
            deprecation_id = record_content.get("deprecation_id")
            if (
                not isinstance(baseline_id, str)
                or record_content.get("baseline_id") != baseline_id
                or not isinstance(replacement, str)
                or not replacement
                or replacement == baseline_id
                or not isinstance(deprecation_id, str)
                or not deprecation_id
                or record.get("status") != "deprecated"
                or state.get("artifacts", {}).get("B04", {}).get(instance_key)
            ):
                raise CLIError(
                    "baseline_deprecation_binding_invalid",
                    "B04 must bind one distinct source/replacement baseline and a new deprecation identity",
                    exit_code=3,
                )
            baseline_ids, existing_deprecations, baseline_records = \
                _stored_baseline_governance_context(
                    state=state, workspace=args.workspace, bundle=bundle,
                )
            replacement_findings = validate_baseline_replacements(
                [*existing_deprecations, record], baseline_ids,
            )
            if replacement_findings:
                raise CLIError(
                    replacement_findings[0]["code"],
                    replacement_findings[0]["message"],
                    exit_code=3, findings=replacement_findings,
                )
            active_dependents = _active_baseline_dependents(
                state=state, workspace=args.workspace, bundle=bundle,
                baseline_id=baseline_id,
            )
            if record_content.get("active_dependents") != active_dependents:
                raise CLIError(
                    "baseline_active_dependents_mismatch",
                    "B04 active_dependents must equal the authenticated current Experiment dependency set",
                    exit_code=3,
                )
            source_records = baseline_records.get(baseline_id, {})
            replacement_records = baseline_records.get(replacement, {})
            if not source_records or not replacement_records:
                raise CLIError(
                    "baseline_replacement_unknown",
                    "B04 source and replacement must each resolve to authenticated B01/B02 records",
                    exit_code=3,
                )
            parents.extend(sorted({*source_records, *replacement_records}))
            checks = {
                "no-orphan-active-dependent": True,
                "pinned-experiment-policy-preserved": True,
            }
        else:
            if record_content.get("subjects") != {
                "workspace_id": workspace_id, **subjects,
            }:
                raise CLIError(
                    "governed_record_subject_mismatch",
                    f"{artifact_id} content subjects do not equal command subjects",
                    exit_code=3,
                )
            dispositions = record_content.get("child_dispositions")
            if not isinstance(dispositions, dict):
                raise CLIError(
                    "child_dispositions_incomplete",
                    f"{artifact_id} must map every child to an exact disposition",
                    exit_code=3,
                )
            open_handoffs = {
                request_id for request_id, handoff in state.get("handoffs", {}).items()
                if handoff.get("status") not in {
                    "successful", "cancelled", "superseded",
                    "superseded_by_retry", "reconciled_no_effect",
                    "reconciled_effect",
                }
            }
            if not open_handoffs.issubset(dispositions):
                raise CLIError(
                    "child_dispositions_incomplete",
                    "governance record omits open handoff dispositions",
                    exit_code=3,
                )
            if action == "cancel":
                checks = {
                    "every-run-wait-handoff-disposed": True,
                }
            else:
                protected_scopes = {
                    "fabricate-platform-success", "bypass-authority",
                    "rewrite-immutable-history", "blind-redispatch",
                    "expose-protected-context",
                }
                if record_content.get("scope") in protected_scopes:
                    raise CLIError(
                        "never_rule_bypass",
                        "S15 cannot waive a module Never rule",
                        exit_code=3,
                    )
                checks = {
                    "scope-exact": True, "subjects-exact": True,
                    "child-dispositions": True, "not-never-rule": True,
                }
        relative = f"artifacts/{artifact_id}/{instance_key}.json"
        descriptor = {
            "artifact_id": artifact_id, "instance_key": instance_key,
            "path": relative, "digest": digest(record), "parents": parents,
            "status": record.get("status", "published"),
        }
        context = _virtual_artifact_context(
            state, artifact_id, instance_key, record, checks,
        )
        candidate_digest_value = digest(record)
        authority_digests = {
            f"{artifact_id}_candidate_digest": candidate_digest_value,
        }
        governed_ids = {"workspace_id": workspace_id, **subjects}
        if action == "deprecate":
            authority_digests.update({
                "source_baseline_records_digest": digest(source_records),
                "replacement_baseline_records_digest": digest(
                    replacement_records,
                ),
                "active_dependents_digest": digest(active_dependents),
            })
            governed_ids.update({
                "deprecation_id": deprecation_id,
                "replacement_baseline_id": replacement,
            })
        authority_subject = {
            "governed_ids": governed_ids,
            "digests": authority_digests,
            **authority_extra,
        }
        event_payload = {"published_artifacts": [descriptor]}
        if action == "deprecate":
            event_payload.update({
                "baseline_id": baseline_id,
                "replacement_baseline_id": replacement,
                "deprecation_id": deprecation_id,
                "active_dependents": active_dependents,
                "authority_bound_digests": deepcopy(authority_digests),
            })
        artifact_writes = ((relative, record, True),)
        event_subjects = {"workspace_id": workspace_id, **subjects}

    passed, missing, _ = _transition_gate(
        transition_name, transition, context,
    )
    if not passed:
        blocked = finding(
            "transition_unsatisfied",
            "pinned transition prerequisites are unsatisfied",
            subject=transition_name,
            governing_asset="transition-requirements.json",
        )
        return _result(
            command, replayed.revision,
            {"valid": False, "missing": missing}, [blocked],
            status="blocked",
        ), 3
    authority = _authority(
        payload, action=authority_action, subject=authority_subject,
        revision=replayed.revision, bundle=bundle, replayed=replayed,
    ) if authority_action else []
    if record_content is not None:
        if artifact_id == "D05":
            consumed_reviewers = sorted({
                item["principal_id"] for item in authority
                if isinstance(item.get("principal_id"), str)
            })
            if record_content.get("reviewers") != consumed_reviewers:
                raise CLIError(
                    "scientific_decision_reviewer_mismatch",
                    "D05 reviewers must equal the canonical principals of its consumed authority attestations",
                    exit_code=3,
                )
        exact_authority = sorted(
            item["attestation_digest"] for item in authority
            if isinstance(item.get("attestation_digest"), str)
        )
        if not exact_authority:
            raise CLIError(
                "governed_record_authority_mismatch",
                "governance record requires at least one consumed attestation",
                exit_code=3,
            )
        final_record = deepcopy(record)
        final_content = deepcopy(record_content)
        final_content["authority"] = exact_authority
        final_record["content"] = final_content
        final_record["content_digest"] = digest(final_content)
        final_findings = bundle.validate_artifact(artifact_id, final_record)
        if final_findings:
            raise CLIError(
                "governed_record_invalid", final_findings[0]["message"],
                findings=final_findings,
            )
        final_digest = digest(final_record)
        descriptor["digest"] = final_digest
        artifact_writes = ((relative, final_record, True),)
        event_payload["published_artifacts"] = [descriptor]
        event_payload["authority_candidate_digest"] = candidate_digest_value
        event_payload["final_record_digest"] = final_digest
    event_payload.update({
        "transition": transition_name,
        "authority_action": authority_action,
        "authority_subject": authority_subject,
        "authority_expected_revision": replayed.revision,
        "consumed_at": authority[0]["consumed_at"] if authority else None,
    })
    logical_operation_id = _operation_id(args, {
        "transition": transition_name,
        "published_artifacts": event_payload.get("published_artifacts"),
    })
    if action == "cancel":
        child_events = _governed_child_disposition_events(
            state=state,
            dispositions=record_content.get("child_dispositions"),
            workspace_id=workspace_id,
            logical_operation_id=logical_operation_id,
            governance_node=f"S16:{instance_key}",
            reason=str(record_content.get("reason")),
        )
    if action in {"cancel", "supersede"}:
        mutation = mutate_batch(
            args.workspace,
            expected_revision=args.expected_revision,
            events=[
                *child_events,
                {
                    "operation_id": _phase_operation_id(
                        logical_operation_id, "governance-terminal",
                    ),
                    "event_type": transition["event_type"],
                    "subjects": event_subjects,
                    "payload": event_payload,
                    "authority": authority,
                },
            ],
            artifact_writes=artifact_writes,
        )
    else:
        mutation = mutate(
            args.workspace,
            expected_revision=args.expected_revision,
            operation_id=logical_operation_id,
            event_type=transition["event_type"],
            subjects=event_subjects,
            payload=event_payload,
            authority=authority,
            artifact_writes=artifact_writes,
        )
    return _result(command, mutation.revision, {
        "event_hash": mutation.event["event_hash"],
        "event_type": transition["event_type"],
        "artifacts": event_payload["published_artifacts"],
    }), 0


def _do_engine_migration(
    args: argparse.Namespace,
    *,
    payload: dict[str, Any],
    replayed: Any,
    state: dict[str, Any],
    bundle: AssetBundle,
) -> tuple[dict[str, Any], int]:
    """Copy and activate one complete, signed successor engine bundle."""

    _reject_governed_payload_claims(
        payload,
        allowed={
            "new_engine_assets", "new_engine_contract_digest",
            "attestations", "authority_subject",
        },
    )
    source_value = payload.get("new_engine_assets")
    claimed_digest = payload.get("new_engine_contract_digest")
    if not isinstance(source_value, str) or not source_value:
        raise CLIError(
            "successor_engine_source_required",
            "engine migration requires an absolute successor asset directory",
            exit_code=5,
        )
    source = Path(source_value)
    if (
        not source.is_absolute()
        or any(part in {"", ".", ".."} for part in source.parts[1:])
    ):
        raise CLIError(
            "successor_engine_source_invalid",
            "successor engine source must be an absolute normalized path",
            exit_code=5,
        )
    try:
        source_info = source.lstat()
        resolved_source = source.resolve(strict=True)
    except OSError as exc:
        raise CLIError(
            "successor_engine_source_invalid",
            f"successor engine source is unavailable: {exc}",
            exit_code=5,
        ) from exc
    if (
        stat.S_ISLNK(source_info.st_mode)
        or not stat.S_ISDIR(source_info.st_mode)
        or resolved_source != source
    ):
        raise CLIError(
            "successor_engine_source_unsafe",
            "successor engine source may not be a symlink or rebound path",
            exit_code=5,
        )
    new_bundle = AssetBundle.load(resolved_source)
    if claimed_digest != new_bundle.engine_contract_digest:
        raise CLIError(
            "successor_engine_digest_mismatch",
            "claimed successor engine digest does not equal the exact loaded asset inventory",
            exit_code=3,
        )
    operation_id = _stable_lifecycle_operation_id(args, payload)
    existing_operation = state.get("operations", {}).get(operation_id)
    if existing_operation is not None:
        prior = next((
            event for event in replayed.events
            if event.get("event_hash") == existing_operation.get("event_hash")
        ), None)
        if (
            not isinstance(prior, dict)
            or prior.get("event_type") != "engine.migrated"
            or prior.get("payload", {}).get("new_engine_contract_digest")
            != new_bundle.engine_contract_digest
        ):
            raise CLIError(
                "idempotency_conflict",
                "migration operation ID is bound to different governed bytes",
                exit_code=4,
            )
        return _result(
            _command(args), replayed.revision,
            {
                "event_hash": prior["event_hash"],
                "old_engine_contract_digest": prior["payload"][
                    "old_engine_contract_digest"
                ],
                "new_engine_contract_digest": new_bundle.engine_contract_digest,
                "migration_boundary_revision": prior["payload"][
                    "migration_boundary_revision"
                ],
                "migration_boundary_head": prior["payload"][
                    "migration_boundary_head"
                ],
                "idempotent": True,
            },
        ), 0
    open_statuses = {
        "created", "dispatched", "failed", "reconciliation_required",
        "configuration_required",
    }
    outstanding = sorted(
        request_id for request_id, handoff in state.get("handoffs", {}).items()
        if handoff.get("status") in open_statuses
    )
    if outstanding:
        raise CLIError(
            "engine_migration_handoffs_open",
            "engine migration cannot change contracts while a handoff is unresolved",
            exit_code=3,
            result={"open_handoffs": outstanding},
        )
    subject = {
        "governed_ids": {
            "workspace_id": replayed.genesis["workspace_id"],
            "migration_sequence": str(replayed.revision + 1),
        },
        "digests": {
            "old_engine_contract_digest": bundle.engine_contract_digest,
            "new_engine_contract_digest": new_bundle.engine_contract_digest,
            "migration_boundary_head": replayed.head_hash,
            "new_asset_inventory_digest": digest(new_bundle.asset_digests),
        },
    }
    supplied_subject = payload.get("authority_subject")
    if supplied_subject is not None and supplied_subject != subject:
        raise CLIError(
            "engine_migration_subject_mismatch",
            "caller authority subject differs from the exact loaded migration boundary",
            exit_code=3,
        )
    authority = _authority(
        payload,
        action="engine.migrate",
        subject=subject,
        revision=replayed.revision,
        bundle=bundle,
        replayed=replayed,
    )
    mutation = migrate_engine(
        args.workspace,
        new_bundle=new_bundle,
        expected_revision=args.expected_revision,
        operation_id=operation_id,
        authority=authority,
        authority_subject=subject,
        consumed_at=authority[0]["consumed_at"],
    )
    return _result(
        _command(args), mutation.revision,
        {
            "event_hash": mutation.event["event_hash"],
            "old_engine_contract_digest": bundle.engine_contract_digest,
            "new_engine_contract_digest": new_bundle.engine_contract_digest,
            "migration_boundary_revision": replayed.revision,
            "migration_boundary_head": replayed.head_hash,
            "idempotent": mutation.idempotent,
        },
    ), 0


def _advance_hpo_step15(
    args: argparse.Namespace,
    *,
    command: str,
    payload: dict[str, Any],
    replayed: Any,
    state: dict[str, Any],
    bundle: AssetBundle,
    row: dict[str, Any],
    subjects: dict[str, str],
) -> tuple[dict[str, Any], int]:
    """Execute STEP15 HPO as a durable search then confirmation sequence."""

    gate_name = "lifecycle-step-15"
    search_operation = "ml.hpo-search"
    downstream_operations = [
        "ml.execution-orchestration", "ml.training",
    ]
    search_subjects, _ = _hpo_search_subjects(
        subjects=subjects, state=state, workspace=args.workspace,
        bundle=bundle,
    )
    search_results = _exact_platform_results(
        state, operations=[search_operation], gate=gate_name, step=15,
        subjects=search_subjects,
    )
    if search_results.get(search_operation) != "successful":
        _, requests = _ensure_platform_requests(
            args.workspace,
            replayed=replayed,
            state=state,
            bundle=bundle,
            operations=[search_operation],
            gate=gate_name,
            step=15,
            subjects=search_subjects,
            expected_revision=replayed.revision,
            seed={
                "workspace_id": replayed.genesis["workspace_id"],
                "step": 15,
                "phase": "hpo-search",
                "subjects": search_subjects,
            },
        )
        refreshed = replay(args.workspace)
        blocked = finding(
            "platform_results_required",
            "the authenticated HPO search result is required before confirmation execution can be requested",
            subject="STEP15",
            governing_asset="lifecycle-steps.json",
        )
        return _result(
            command,
            refreshed.revision,
            {
                "step": 15,
                "phase": "hpo-search",
                "handoffs": requests,
                "missing": ["platform_result:ml.hpo-search"],
                "failure_disposition": row.get("failure_disposition"),
            },
            [blocked],
            status="blocked",
        ), 3

    search_packets = _exact_handoff_packets(
        args.workspace,
        state=state,
        bundle=bundle,
        operations=[search_operation],
        gate=gate_name,
        step=15,
        subjects=search_subjects,
    )
    selection = _validated_hpo_search_phase(
        state=state,
        workspace=args.workspace,
        bundle=bundle,
        subjects=subjects,
        packet=search_packets[search_operation],
    )
    persisted = _persisted_hpo_selection(
        replayed=replayed, state=state, workspace=args.workspace,
        bundle=bundle, subjects=subjects,
        selection=selection,
    )
    current_replayed = replayed
    current_state = state
    current_bundle = bundle
    if persisted is None:
        _, selection_writes, _, selection_events = \
            _platform_execution_completion(
                step=15,
                workspace=args.workspace,
                replayed=replayed,
                state=state,
                bundle=bundle,
                subjects=subjects,
                packets=search_packets,
                hpo_enabled=True,
                command_operation_id=_stable_lifecycle_operation_id(
                    args, payload,
                ),
                phase="hpo-selection",
            )
        mutate_batch(
            args.workspace,
            expected_revision=replayed.revision,
            events=selection_events,
            artifact_writes=selection_writes,
        )
        current_replayed = replay(args.workspace)
        current_state = materialize(current_replayed)
        current_bundle = current_replayed.bundles[
            current_replayed.active_engine_digest
        ]
        search_packets = _exact_handoff_packets(
            args.workspace,
            state=current_state,
            bundle=current_bundle,
            operations=[search_operation],
            gate=gate_name,
            step=15,
            subjects=search_subjects,
        )
        selection = _validated_hpo_search_phase(
            state=current_state,
            workspace=args.workspace,
            bundle=current_bundle,
            subjects=subjects,
            packet=search_packets[search_operation],
        )
        persisted = _persisted_hpo_selection(
            replayed=current_replayed,
            state=current_state,
            workspace=args.workspace,
            bundle=current_bundle,
            subjects=subjects,
            selection=selection,
        )
        if persisted is None:
            raise CLIError(
                "hpo_selection_phase_incomplete",
                "the atomic HPO selection phase did not persist its governed facts",
                exit_code=3,
            )

    downstream_subjects = _hpo_confirmation_subjects(
        subjects, persisted, state=current_state, workspace=args.workspace,
        bundle=current_bundle,
    )
    downstream_results = _exact_platform_results(
        current_state,
        operations=downstream_operations,
        gate=gate_name,
        step=15,
        subjects=downstream_subjects,
    )
    missing_operations = [
        operation for operation in downstream_operations
        if downstream_results.get(operation) != "successful"
    ]
    if missing_operations:
        _, requests = _ensure_platform_requests(
            args.workspace,
            replayed=current_replayed,
            state=current_state,
            bundle=current_bundle,
            operations=missing_operations,
            gate=gate_name,
            step=15,
            subjects=downstream_subjects,
            expected_revision=current_replayed.revision,
            seed={
                "workspace_id": current_replayed.genesis["workspace_id"],
                "step": 15,
                "phase": "hpo-confirmation",
                "subjects": downstream_subjects,
            },
        )
        refreshed = replay(args.workspace)
        blocked = finding(
            "platform_results_required",
            "independent confirmation execution results are required after the persisted HPO selection",
            subject="STEP15",
            governing_asset="lifecycle-steps.json",
        )
        return _result(
            command,
            refreshed.revision,
            {
                "step": 15,
                "phase": "hpo-confirmation",
                "handoffs": requests,
                "missing": [
                    f"platform_result:{name}" for name in missing_operations
                ],
                "selection_event_hash":
                    downstream_subjects["selection_event_hash"],
                "confirmation_event_hash":
                    downstream_subjects["confirmation_event_hash"],
                "failure_disposition": row.get("failure_disposition"),
            },
            [blocked],
            status="blocked",
        ), 3

    downstream_packets = _exact_handoff_packets(
        args.workspace,
        state=current_state,
        bundle=current_bundle,
        operations=downstream_operations,
        gate=gate_name,
        step=15,
        subjects=downstream_subjects,
    )
    descriptors, artifact_writes, gate_context, batch_events = \
        _platform_execution_completion(
            step=15,
            workspace=args.workspace,
            replayed=current_replayed,
            state=current_state,
            bundle=current_bundle,
            subjects=subjects,
            packets=downstream_packets,
            hpo_enabled=True,
            hpo_selection=persisted,
            command_operation_id=_stable_lifecycle_operation_id(
                args, payload,
            ),
        )
    exact_results = {
        search_operation: "successful", **downstream_results,
    }
    gate_context["platform_results"] = exact_results
    passed, missing, substate_events = evaluate_gate(
        row["gate"], gate_context,
    )
    if not passed:
        blocked = finding(
            "gate_unsatisfied",
            "authenticated platform evidence does not satisfy the lifecycle row",
            subject="STEP15",
            governing_asset="lifecycle-steps.json",
        )
        return _result(
            command,
            current_replayed.revision,
            {"step": 15, "missing": missing},
            [blocked],
            status="blocked",
        ), 3
    batch_events[-1]["payload"]["substate_events"] = substate_events
    mutation = mutate_batch(
        args.workspace,
        expected_revision=current_replayed.revision,
        events=batch_events,
        artifact_writes=artifact_writes,
    )
    completed = mutation.state.get("lifecycle_step") == 16
    if not completed:
        blocked = finding(
            "attempt_terminal_requires_disposition",
            "the platform Attempt was retained exactly but its non-success terminal status requires frozen retry/deviation disposition",
            subject="STEP15",
            governing_asset="transition-requirements.json",
        )
        return _result(
            command,
            mutation.revision,
            {
                "step": 15,
                "event_type": mutation.event["event_type"],
                "event_hash": mutation.event["event_hash"],
                "artifacts": descriptors,
                "state": mutation.state["dimensions"],
                "failure_disposition": row.get("failure_disposition"),
            },
            [blocked],
            status="blocked",
        ), 3
    return _result(
        command,
        mutation.revision,
        {
            "step": 15,
            "event_type": mutation.event["event_type"],
            "event_hash": mutation.event["event_hash"],
            "artifacts": descriptors,
            "state": mutation.state["dimensions"],
        },
    ), 0


def _do_lifecycle(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    action = args.lifecycle_action
    command = _command(args)
    if action == "create":
        payload, runtime_paths = _configured_creation_payload(
            _payload(args.payload), args.project_root, args.workspace,
        )
        workspace = Path(runtime_paths["workspace"])
        _ensure_workspace_parent(Path(runtime_paths["project_root"]), workspace)
        mutation = create_workspace(
            str(workspace), payload,
            expected_revision=args.expected_revision,
            operation_id=_operation_id(args, payload),
        )
        return _result(command, mutation.revision, {
            "workspace_id": mutation.state["workspace_id"],
            "workspace": str(workspace),
            "workspace_defaulted": runtime_paths["workspace_defaulted"],
            "sdm_output_root": runtime_paths["sdm_output_root"],
            "reports_root": runtime_paths["reports_root"],
            "ledger_head": mutation.state["ledger_head"],
            "protocol": mutation.state.get("protocol"),
        }), 0
    if action == "resume":
        resumed = resume_workspace(args.workspace, args.trusted_anchor_result)
        disposition = resumed["action"].get("disposition")
        if disposition in {
            "blocked", "human_wait", "handoff", "wait_for_result",
            "retry_or_supersede", "reconcile",
        }:
            status, code = "blocked", 3
        elif disposition == "configuration_required":
            status, code = "configuration_required", 5
        else:
            status, code = "pass", 0
        findings = [] if code == 0 else [finding(disposition, "earliest admissible lifecycle action cannot advance synchronously", subject="workspace", governing_asset="lifecycle-steps.json")]
        return _result(command, resumed["revision"], resumed, findings, status=status), code
    payload = _payload(args.payload)
    replayed, state, bundle = _workspace_state(args.workspace)
    if action == "migrate":
        return _do_engine_migration(
            args, payload=payload, replayed=replayed,
            state=state, bundle=bundle,
        )
    if args.expected_revision != replayed.revision:
        raise WorkspaceError(
            "cas_conflict",
            f"expected revision {args.expected_revision}, actual {replayed.revision}",
            exit_code=4,
        )
    if action == "advance":
        _reject_governed_payload_claims(
            payload,
            allowed={
                "step", "subjects", "records", "input_refs",
                "input_records", "transition_target",
                "authority_subject", "attestations",
            },
        )
        step = payload.get("step")
        steps = bundle.document("lifecycle-steps.json")["steps"]
        if not isinstance(step, int) or step < 1 or step > 21:
            raise CLIError("lifecycle_step_invalid", "advance payload requires step 1..21")
        if step != state.get("lifecycle_step"):
            raise CLIError(
                "lifecycle_step_out_of_order",
                "advance may execute only the earliest current lifecycle row",
                exit_code=3,
            )
        row = steps[step - 1]
        dedicated_executors = {
            8: "sdm-freeze-experiment freeze",
            20: "sdm-scientific-decision scientific-decision",
        }
        if step in dedicated_executors:
            raise CLIError(
                "specialized_executor_required",
                f"STEP{step:02d} is owned only by {dedicated_executors[step]}; generic lifecycle advance cannot create platform requests or terminal facts",
                exit_code=3,
            )
        specialized_terminal = {
            14: "run.materialized", 18: "evidence.built",
        }
        operations = [
            item["name"] if isinstance(item, dict) else item
            for item in row.get("platform_operations", [])
        ]
        command_subjects = _subjects(
            payload, replayed.genesis["workspace_id"],
        )
        platform_subjects = command_subjects
        if step == 7:
            platform_subjects = _step07_bound_subjects(
                payload=payload, subjects=command_subjects, state=state,
                workspace=args.workspace, bundle=bundle,
            )
        elif step in {10, 11, 12, 13}:
            platform_subjects = _engineering_step_subjects(
                step=step, payload=payload, subjects=command_subjects,
                state=state, workspace=args.workspace, bundle=bundle,
            )
        elif step == 16:
            platform_subjects = _locked_evaluation_subjects(
                subjects=command_subjects, state=state,
                workspace=args.workspace, bundle=bundle,
            )
            platform_subjects = _runtime_bound_subjects(
                subjects=platform_subjects, state=state,
                workspace=args.workspace, bundle=bundle,
            )
        if step == 21:
            applicable, applicability_basis = lifecycle_step_applicability(
                state, step,
            )
            if step == 21 and applicable:
                raise CLIError(
                    "specialized_executor_required",
                    "applicable STEP21 is resolved only by governed lifecycle supersede (continue) or anchored archive",
                    exit_code=3,
                )
            if not applicable:
                if any(payload.get(name) not in (None, [], {}) for name in (
                    "records", "input_refs", "input_records",
                    "transition_target", "attestations",
                    "authority_subject",
                )):
                    raise CLIError(
                        "inapplicable_step_payload",
                        "a replay-derived non-applicable row cannot publish records, consume authority, or select a target",
                        exit_code=3,
                    )
                applicability_digest = digest({
                    "step": step, "basis": applicability_basis,
                })
                mutation = mutate(
                    args.workspace,
                    expected_revision=args.expected_revision,
                    operation_id=_operation_id(args, {
                        "step": step,
                        "applicability_digest": applicability_digest,
                    }),
                    event_type="lifecycle.step-skipped",
                    subjects={
                        "workspace_id": replayed.genesis["workspace_id"],
                        **payload.get("subjects", {}),
                    },
                    payload={
                        "step": step,
                        "lifecycle_step_completion": True,
                        "applicability": "not_applicable",
                        "basis": applicability_basis,
                        "applicability_digest": applicability_digest,
                        "skip_reason": (
                            "STEP21.no-successor"
                            if step == 21 else None
                        ),
                        "outputs": [],
                        "published_artifacts": [],
                    },
                )
                return _result(
                    command, mutation.revision,
                    {
                        "step": step,
                        "disposition": (
                            "no-successor" if step == 21
                            else "not_applicable"
                        ),
                        "basis": applicability_basis,
                        "state": mutation.state["dimensions"],
                    },
                ), 0
        run_plan_context: dict[str, Any] | None = None
        hpo_enabled = False
        if step == 15:
            hpo_enabled, _ = _hpo_configuration(
                state=state, workspace=args.workspace, bundle=bundle,
                subjects=command_subjects,
            )
            if not hpo_enabled:
                operations = [
                    operation for operation in operations
                    if operation != "ml.hpo-search"
                ]
                platform_subjects = _runtime_bound_subjects(
                    subjects=command_subjects, state=state,
                    workspace=args.workspace, bundle=bundle,
                )
            else:
                return _advance_hpo_step15(
                    args,
                    command=command,
                    payload=payload,
                    replayed=replayed,
                    state=state,
                    bundle=bundle,
                    row=row,
                    subjects=command_subjects,
                )
        exact_terminal = specialized_terminal.get(step)
        if step == 14:
            terminal_events, run_plan_context = \
                _materialized_run_plan_context(
                    workspace=args.workspace, replayed=replayed,
                    state=state, bundle=bundle,
                    subjects=command_subjects,
                )
        else:
            terminal_events = [
                event for event in replayed.events
                if event.get("event_type") == exact_terminal
                and all(
                    event.get("subjects", {}).get(key) == value
                    for key, value in command_subjects.items()
                )
            ] if exact_terminal else []
        derived_transition_target: str | None = None
        if terminal_events:
            terminal_payload = terminal_events[-1].get("payload", {})
            if step == 18:
                derived_transition_target = (
                    "ready" if terminal_payload.get("complete") is True
                    else "incomplete"
                )
        claimed_target = payload.get("transition_target")
        if (
            claimed_target is not None
            and claimed_target != derived_transition_target
        ):
            raise CLIError(
                "transition_target_substitution",
                "caller transition_target does not equal the authenticated terminal fact",
                exit_code=3,
            )
        gate_name = f"lifecycle-step-{step}"
        exact_results = (
            {operation: "successful" for operation in operations}
            if terminal_events else _exact_platform_results(
                state, operations=operations, gate=gate_name, step=step,
                subjects=platform_subjects,
            )
        )
        missing_operations = [
            operation for operation in operations
            if exact_results.get(operation) != "successful"
        ]
        requests: list[dict[str, Any]] = []
        if missing_operations:
            _, requests = _ensure_platform_requests(
                args.workspace,
                replayed=replayed,
                state=state,
                bundle=bundle,
                operations=missing_operations,
                gate=gate_name,
                step=step,
                subjects=platform_subjects,
                expected_revision=args.expected_revision,
                seed={
                    "workspace_id": replayed.genesis["workspace_id"],
                    "step": step,
                    "subjects": payload.get("subjects", {}),
                },
            )
            refreshed = replay(args.workspace)
            blocked = finding(
                "platform_results_required",
                "outer platform Skill results are required before this gate can advance",
                subject=f"STEP{step:02d}",
                governing_asset="lifecycle-steps.json",
            )
            return _result(
                command,
                refreshed.revision,
                {
                    "step": step,
                    "handoffs": requests,
                    "missing": [
                        f"platform_result:{name}" for name in missing_operations
                    ],
                    "failure_disposition": row.get("failure_disposition"),
                },
                [blocked],
                status="blocked",
            ), 3
        if step in {15, 16, 17}:
            packets = _exact_handoff_packets(
                args.workspace, state=state, bundle=bundle,
                operations=operations, gate=gate_name, step=step,
                subjects=platform_subjects,
            )
            descriptors, artifact_writes, gate_context, batch_events = \
                _platform_execution_completion(
                    step=step, workspace=args.workspace,
                    replayed=replayed, state=state, bundle=bundle,
                    subjects=platform_subjects, packets=packets,
                    hpo_enabled=hpo_enabled,
                    command_operation_id=_stable_lifecycle_operation_id(
                        args, payload,
                    ),
                )
            gate_context["platform_results"] = exact_results
            passed, missing, substate_events = evaluate_gate(
                row["gate"], gate_context,
            )
            if not passed:
                blocked = finding(
                    "gate_unsatisfied",
                    "authenticated platform evidence does not satisfy the lifecycle row",
                    subject=f"STEP{step:02d}",
                    governing_asset="lifecycle-steps.json",
                )
                return _result(
                    command, replayed.revision,
                    {"step": step, "missing": missing}, [blocked],
                    status="blocked",
                ), 3
            batch_events[-1]["payload"]["substate_events"] = substate_events
            mutation = mutate_batch(
                args.workspace,
                expected_revision=args.expected_revision,
                events=batch_events,
                artifact_writes=artifact_writes,
            )
            completed = mutation.state.get("lifecycle_step") == step + 1
            if not completed:
                blocked = finding(
                    "attempt_terminal_requires_disposition",
                    "the platform Attempt was retained exactly but its non-success terminal status requires frozen retry/deviation disposition",
                    subject=f"STEP{step:02d}",
                    governing_asset="transition-requirements.json",
                )
                return _result(
                    command, mutation.revision,
                    {
                        "step": step,
                        "event_type": mutation.event["event_type"],
                        "event_hash": mutation.event["event_hash"],
                        "artifacts": descriptors,
                        "state": mutation.state["dimensions"],
                        "failure_disposition": row.get(
                            "failure_disposition",
                        ),
                    },
                    [blocked], status="blocked",
                ), 3
            return _result(
                command, mutation.revision,
                {
                    "step": step,
                    "event_type": mutation.event["event_type"],
                    "event_hash": mutation.event["event_hash"],
                    "artifacts": descriptors,
                    "state": mutation.state["dimensions"],
                },
            ), 0
        transition_by_step = {
            3: "contract.ready", 5: "baseline.lock",
            7: "protocol.readiness", 10: "implementation.start",
            11: "implementation.review", 12: "implementation.accept",
            13: "preflight.accept", 19: "evidence.review",
        }
        if exact_terminal is not None:
            if payload.get("records") not in (None, []):
                raise CLIError(
                    "specialized_executor_required",
                    f"STEP{step:02d} artifacts may be written only by its dedicated public executor",
                    exit_code=3,
                )
            if not terminal_events:
                raise CLIError(
                    "specialized_terminal_missing",
                    f"STEP{step:02d} requires exact prior {exact_terminal} from its dedicated executor",
                    exit_code=3,
                )
            if step == 14:
                descriptors, artifact_writes = [], []
                assert run_plan_context is not None
                gate_context = run_plan_context
            else:
                descriptors, artifact_writes, gate_context = \
                    _validated_step_records(
                        payload={**payload, "records": []}, row=row,
                        state=state, workspace=args.workspace,
                        workspace_id=replayed.genesis["workspace_id"],
                        bundle=bundle, command_subjects=command_subjects,
                        platform_subjects=platform_subjects,
                    )
        else:
            descriptors, artifact_writes, gate_context = \
                _validated_step_records(
                    payload=payload, row=row, state=state,
                    workspace=args.workspace,
                    workspace_id=replayed.genesis["workspace_id"],
                    bundle=bundle, command_subjects=command_subjects,
                    platform_subjects=platform_subjects,
                )

        gate_context["platform_results"] = exact_results
        transition_name = transition_by_step.get(step)
        transition = (
            bundle.transition(transition_name) if transition_name else None
        )
        authority_action = (
            transition.get("authority_action")
            if isinstance(transition, dict) else None
        )
        authority: list[dict[str, Any]] = []
        authority_subject: dict[str, Any] = {}
        if authority_action:
            authority_extra = payload.get("authority_subject", {})
            if (
                not isinstance(authority_extra, dict)
                or {"governed_ids", "digests"}.intersection(authority_extra)
            ):
                raise CLIError(
                    "authority_subject_override",
                    "authority_subject cannot replace coordinator-derived governed IDs or digests",
                    exit_code=2,
                )
            provenance = _step_authority_provenance_extension(
                step=step, payload=payload, state=state,
                workspace=args.workspace, bundle=bundle, replayed=replayed,
            )
            supplied_extensions = authority_extra.get("extensions", {})
            if not isinstance(supplied_extensions, dict):
                raise CLIError(
                    "authority_subject_invalid",
                    "authority_subject.extensions must be an object",
                    exit_code=2,
                )
            if (
                provenance
                and AUTHORITY_PROVENANCE_EXTENSION in supplied_extensions
            ):
                raise CLIError(
                    "authority_provenance_override",
                    "caller cannot supply coordinator-derived authority provenance",
                    exit_code=2,
                )
            merged_extra = {
                key: value for key, value in authority_extra.items()
                if key != "extensions"
            }
            merged_extensions = {
                **supplied_extensions,
                **provenance.get("extensions", {}),
            }
            if merged_extensions:
                merged_extra["extensions"] = merged_extensions
            candidate_digests = {
                f"{item['artifact_id']}:{item['instance_key']}": item["digest"]
                for item in descriptors
            }
            for _, value, _ in artifact_writes:
                content = value.get("content", value)
                if isinstance(content, dict) and "authority" in content \
                        and content.get("authority") != []:
                    raise CLIError(
                        "unsigned_governance_candidate_required",
                        "authority-gated step records must carry authority=[] until attestations are consumed",
                        exit_code=3,
                    )
            authority_subject = {
                "governed_ids": {
                    "workspace_id": replayed.genesis["workspace_id"],
                    **command_subjects, "step": str(step),
                },
                "digests": {"candidate_records_digest": digest(candidate_digests)},
                **merged_extra,
            }
            authority = _authority(
                payload, action=authority_action, subject=authority_subject,
                revision=replayed.revision, bundle=bundle,
                replayed=replayed,
            )
            if step == 19:
                exact_reviewers = sorted({
                    item["principal_id"] for item in authority
                })
                exact_roles = sorted({item["role"] for item in authority})
                review_candidates = [
                    value for _, value, _ in artifact_writes
                    if value.get("artifact_type") == "S10"
                ]
                if len(review_candidates) != 1:
                    raise CLIError(
                        "review_authority_binding_missing",
                        "STEP19 requires exactly one signed S10 review candidate",
                        exit_code=3,
                    )
                review_content = review_candidates[0].get("content")
                if (
                    not isinstance(review_content, dict)
                    or review_content.get("reviewers") != exact_reviewers
                    or review_content.get("roles") != exact_roles
                ):
                    raise CLIError(
                        "review_authority_identity_mismatch",
                        "S10 reviewer principals and roles must exactly equal the verified attestations",
                        exit_code=3,
                    )
            exact_authority = sorted(
                item["attestation_digest"] for item in authority
            )
            finalized_writes: list[tuple[str, Any, bool]] = []
            descriptor_by_path = {
                item["path"]: item for item in descriptors
            }
            for relative, value, exclusive in artifact_writes:
                final_value = deepcopy(value)
                content = final_value.get("content", final_value)
                if isinstance(content, dict) and "authority" in content:
                    content = deepcopy(content)
                    content["authority"] = exact_authority
                    final_value["content"] = content
                    final_value["content_digest"] = digest(content)
                    artifact_id = final_value["artifact_type"]
                    findings = bundle.validate_artifact(artifact_id, final_value)
                    if findings:
                        raise CLIError(
                            "governed_record_invalid", findings[0]["message"],
                            findings=findings,
                        )
                descriptor_by_path[relative]["digest"] = digest(final_value)
                finalized_writes.append((relative, final_value, exclusive))
            artifact_writes = finalized_writes
            gate_context.setdefault("authorities", {})[
                authority_action
            ] = True
        gate_context.setdefault("checks", {}).update(_derived_step_checks(
            step=step, row=row, exact_results=exact_results,
            authority_action=authority_action, authority=authority,
        ))
        freeze_readiness_payload: dict[str, Any] = {}
        if step == 7:
            freeze_readiness_payload = _step07_freeze_readiness_payload(
                workspace=args.workspace, payload=payload, state=state,
                bundle=bundle, subjects=platform_subjects,
                descriptors=descriptors, writes=artifact_writes,
            )
        passed, missing, substate_events = evaluate_gate(
            row["gate"], gate_context,
        )
        if not passed:
            blocked = finding("gate_unsatisfied", "lifecycle row gate is unsatisfied", subject=f"STEP{step:02d}", governing_asset="lifecycle-steps.json")
            return _result(command, replayed.revision, {"step": step, "missing": missing, "failure_disposition": row.get("failure_disposition")}, [blocked], status="blocked"), 3
        if isinstance(transition, dict):
            transition_passed, transition_missing, _ = _transition_gate(
                transition_name, transition, gate_context,
            )
            if not transition_passed:
                blocked = finding(
                    "transition_unsatisfied",
                    "named pinned transition is unsatisfied",
                    subject=transition_name,
                    governing_asset="transition-requirements.json",
                )
                return _result(
                    command, replayed.revision,
                    {"step": step, "missing": transition_missing},
                    [blocked], status="blocked",
                ), 3
        event_type = (
            transition["event_type"]
            if isinstance(transition, dict) else "lifecycle.advanced"
        )
        mutation = mutate(
            args.workspace,
            expected_revision=args.expected_revision,
            operation_id=_operation_id(args, payload),
            event_type=event_type,
            subjects={"workspace_id": replayed.genesis["workspace_id"], **payload.get("subjects", {})},
            payload={
                "step": step,
                "lifecycle_step_completion": True,
                "transition": transition_name,
                "outputs": (
                    ["R01", "R02"] if step == 14
                    else ["D01", "D02", "D03", "D04"]
                    if step == 18
                    else sorted({
                        item["artifact_id"] for item in descriptors
                        if item["artifact_id"] in {
                            label.split(".", 1)[0]
                            for label in (
                                list(row.get("governed_outputs", []))
                                + list(row.get("supporting_outputs", []))
                                + list(row.get("outputs", []))
                            )
                            if isinstance(label, str)
                        }
                    })
                ),
                "published_artifacts": descriptors,
                "transition_target": derived_transition_target,
                "substate_events": substate_events,
                "authority_action": authority_action,
                "authority_subject": authority_subject,
                "authority_expected_revision": replayed.revision,
                "authority_candidate_records_digest":
                    authority_subject.get("digests", {}).get(
                        "candidate_records_digest"
                    ),
                "consumed_at": authority[0]["consumed_at"] if authority else None,
                **freeze_readiness_payload,
            },
            authority=authority,
            artifact_writes=artifact_writes,
        )
        return _result(command, mutation.revision, {"step": step, "state": mutation.state["dimensions"]}), 0
    _reject_governed_payload_claims(payload)
    if action in {"archive", "close"}:
        return _do_anchored_lifecycle(
            args,
            payload=payload,
            replayed=replayed,
            state=state,
            bundle=bundle,
            action=action,
        )
    if action == "retry":
        return _do_attempt_retry(
            args, payload=payload, replayed=replayed,
            state=state, bundle=bundle,
        )
    return _do_governed_interrupt(
        args,
        action=action,
        payload=payload,
        replayed=replayed,
        state=state,
        bundle=bundle,
    )


def _handoff_result(command: str, outcome: handoffs.HandoffOutcome) -> tuple[dict[str, Any], int]:
    # A requested cancellation has achieved its own command contract even
    # though the cancelled child can no longer satisfy the parent gate.
    # Cancellation received incidentally while ingesting a platform result is
    # still a blocked dependent-gate outcome.
    requested_cancellation = (
        command == "specctl.handoff.cancel"
        and outcome.disposition == "cancelled"
    )
    blocked = (
        outcome.disposition
        in {"reconciliation_required", "late_result", "failed", "cancelled"}
        and not requested_cancellation
    )
    status = "blocked" if blocked else "pass"
    code = 3 if blocked else 0
    findings = [] if not blocked else [finding(outcome.disposition, "handoff has not satisfied its dependent gate", subject=outcome.request_id, governing_asset="platform-operations.json")]
    return _result(command, outcome.revision, {"request_id": outcome.request_id, "disposition": outcome.disposition, "packet": outcome.packet, "event_hash": outcome.event.get("event_hash") if outcome.event else None, "idempotent": outcome.idempotent}, findings, status=status), code


def _do_handoff(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    action = args.handoff_action
    command = _command(args)
    expected = args.expected_revision
    if action == "create":
        payload = _payload(args.payload)
        outcome = handoffs.create(args.workspace, payload, expected_revision=expected, operation_id=_operation_id(args, payload))
    elif action == "mark-dispatched":
        outcome = handoffs.mark_dispatched(
            args.workspace,
            request_id=args.request_id,
            dispatch_correlation=args.dispatch_correlation,
            coordinator_disposition=args.coordinator_disposition,
            uncertain=args.uncertain,
            expected_revision=expected,
            operation_id=_operation_id(args, vars(args)),
        )
    elif action == "ingest":
        packet = _payload(args.result)
        outcome = handoffs.ingest(args.workspace, packet, expected_revision=expected, operation_id=_operation_id(args, packet))
    elif action == "cancel":
        outcome = handoffs.cancel(args.workspace, request_id=args.request_id, reason=args.reason, expected_revision=expected, operation_id=_operation_id(args, vars(args)))
    elif action == "retry":
        payload = _payload(args.payload)
        outcome = handoffs.retry(args.workspace, payload, prior_request_id=args.prior_request_id, expected_revision=expected, operation_id=_operation_id(args, payload))
    elif action == "supersede":
        outcome = handoffs.supersede(args.workspace, request_id=args.request_id, successor_request_id=args.successor_request_id, reason=args.reason, expected_revision=expected, operation_id=_operation_id(args, vars(args)))
    elif action == "reconcile":
        payload = _payload(args.payload)
        outcome = handoffs.reconcile(args.workspace, payload, request_id=args.request_id, expected_revision=expected, operation_id=_operation_id(args, payload))
    else:
        raise CLIError("malformed_cli", f"unknown handoff action: {action}")
    return _handoff_result(command, outcome)


HANDLERS: dict[str, Callable[[argparse.Namespace], tuple[dict[str, Any], int]]] = {
    "validate": _do_validate,
    "hash": _do_hash,
    "freeze": _do_freeze,
    "check-diff": _do_check_diff,
    "materialize-run": _do_materialize_run,
    "verify-run-manifest": _do_verify_run,
    "build-evidence": _do_build_evidence,
    "verify-evidence": _do_verify_evidence,
    "scientific-decision": _do_scientific_decision,
    "validate-transition": _do_validate_transition,
    "lifecycle": _do_lifecycle,
    "handoff": _do_handoff,
}


def _failure(command: str, exc: Exception, exit_code: int, status: str | None = None, findings: list[dict[str, Any]] | None = None, result: Any = None) -> dict[str, Any]:
    code = getattr(exc, "code", "internal_error")
    current = findings or getattr(exc, "findings", None) or [finding(code, str(exc), subject=command, governing_asset="command-contracts.json")]
    handled_status = {
        2: "fail", 3: "blocked", 4: "conflict",
        5: "configuration_required", 70: "internal_error",
    }.get(exit_code, "internal_error")
    return _result(command, None, result if result is not None else {"error": code},
                   current, status=status or handled_status)


def _render_result(value: Any) -> str:
    """Emit standard JSON only when it is byte-identical to canonical JSON."""

    canonical = canonical_bytes(value).decode("utf-8")
    try:
        standard = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return canonical
    return standard if standard == canonical else canonical


def main(argv: list[str] | None = None) -> int:
    command = "specctl"
    try:
        args = build_parser().parse_args(argv)
        command = _command(args)
        bundle = AssetBundle.load()
        bundle.command(command)
        if os.environ.get("SDM_INJECT_INTERNAL_FAILURE") == "1":
            raise RuntimeError("injected internal failure")
        output, exit_code = HANDLERS[args.top_command](args)
    except CLIError as exc:
        exit_code = exc.exit_code
        output = _failure(command, exc, exit_code, exc.status, exc.findings, exc.result)
    except HandoffError as exc:
        exit_code = exc.exit_code
        # Exit-code semantics are part of every command contract.  A domain
        # disposition may explain a blocked exit, but must not relabel a CAS
        # conflict, missing configuration, or internal error.
        handoff_status = {
            2: "fail",
            3: "blocked",
            4: "conflict",
            5: "configuration_required",
            70: "internal_error",
        }.get(exit_code)
        output = _failure(command, exc, exit_code, handoff_status)
    except WorkspaceError as exc:
        exit_code = exc.exit_code
        output = _failure(command, exc, exit_code, findings=exc.findings)
    except AuthorityError as exc:
        exit_code = 5 if exc.configuration else 2
        output = _failure(command, exc, exit_code)
    except (CanonicalError, ContractError, LedgerError, ProjectionError, JournalError) as exc:
        exit_code = 2
        output = _failure(command, exc, exit_code)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        exit_code = 70
        output = _failure(command, exc, exit_code)
    # Parsed canonical JSON may contain Decimal values.  Emit through the same
    # canonical encoder used by the authority/digest boundary so a valid
    # platform result can never crash the public CLI's final serialization.
    sys.stdout.write(_render_result(output) + "\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
