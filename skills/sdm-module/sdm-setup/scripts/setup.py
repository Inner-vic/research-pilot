#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Crash-recoverable installer for the native SDM multi-Skill module."""

from __future__ import annotations

# Full governance relies on POSIX descriptor locks and resource accounting.
# Fail before importing those modules on Windows; ordinary research has a
# separate supported entry under this same Skill, not a weakened governance port.
import sys
if sys.platform == 'win32':
    import json
    print(json.dumps({'status':'unsupported_host','mode':'full-governed-host',
        'reason':'posix_file_locking_and_resource_accounting_required',
        'research_entry':'Install the native bmad-ml Skill, then select BMAD ML through / in the Codex client.',
        'governed_host':'Use Linux/WSL and prepare_linux_host.py for the full governed host.',
        'platform_writes':0}))
    raise SystemExit(3)

import argparse
import contextlib
import csv
import errno
import fcntl
import hashlib
import io
import json
import math
import os
import re
import resource
import shutil
import stat
import sys
import time
import tomllib
import uuid
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

sys.dont_write_bytecode = True

MODULE_CODE = "sdm"
MODULE_NAME = "Specification-Driven ML Development"
SKILLS = (
    "sdm-setup", "sdm-lifecycle", "sdm-ml-readiness",
    "sdm-create-ml-contract", "sdm-lock-baseline",
    "sdm-create-experiment-spec", "sdm-freeze-experiment",
    "sdm-build-context-pack", "sdm-evidence-review",
    "sdm-scientific-decision",
    "sdm-agent-research-planner", "sdm-agent-experiment-spec",
    "sdm-agent-geometry-contract", "sdm-agent-evidence-review",
)
# The complete legacy roster is accepted only for digest-bound update/uninstall.
# Retired directories remain fixed transaction targets so upgrades remove their
# previously owned executable files using the same journal and rollback rules.
RETIRED_SKILLS = ("sdm-model-promotion", "sdm-agent-promotion")
OWNED_SKILLS = SKILLS + RETIRED_SKILLS
LEGACY_SKILLS = SKILLS[:10] + (RETIRED_SKILLS[0],) + SKILLS[10:] + (RETIRED_SKILLS[1],)
ANSWER_KEYS = (
    "sdm_output_root", "sdm_trust_root", "sdm_active_workspace",
    "data_platform_skill", "source_control_platform_skill", "ci_platform_skill",
    "ml_execution_platform_skill",
)
SHARED_KEYS = tuple(key for key in ANSWER_KEYS if key != "sdm_active_workspace")
CSV_HEADER = (
    "module", "skill", "display-name", "menu-code", "description", "action",
    "args", "phase", "preceded-by", "followed-by", "required",
    "output-location", "outputs",
)
HOOK_TEXT = (
    "Before evaluating this hook, run uv run "
    "{project-root}/_bmad/scripts/resolve_config.py --project-root {project-root} "
    "and read modules.sdm.sdm_active_workspace from its merged JSON; "
    "a resolver failure or missing setting blocks this hook. "
    "When modules.sdm.sdm_active_workspace is non-empty, activate the installed "
    "sdm-ml-readiness Skill for that exact workspace before sprint planning; "
    "resolve it against the explicit project boundary by the installed "
    "sdm-lifecycle runtime rule and refuse an escape, symlink, special node, "
    "ambiguous spelling, or nonexistent workspace; "
    "require S04 planning readiness, E02 immutable Experiment readiness, and "
    "S09 post-implementation preflight only when each is applicable at the "
    "current lifecycle stage. With no active SDM workspace this step is a no-op."
)
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIR_OPEN_FLAGS = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
                   getattr(os, "O_DIRECTORY", 0) |
                   getattr(os, "O_NOFOLLOW", 0))
_FILE_OPEN_FLAGS = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
                    getattr(os, "O_NOFOLLOW", 0) |
                    getattr(os, "O_NONBLOCK", 0))
_VALIDATION_FILES = (
    "_bmad/custom/config.user.toml",
    "_bmad/_config/bmad-help.csv",
    "_bmad/module-help.csv",
    "_bmad/custom/bmad-sprint-planning.toml",
    "_bmad/custom/config.toml",
    "_bmad/.sdm-setup-journal.json",
)
MAX_TREE_NODES = 100_000
MAX_TREE_BYTES = 134_217_728
MAX_TREE_FILE_BYTES = 16_777_216
MAX_RETAINED_FDS = 32
MAX_NUMBER_TOKEN_BYTES = 256
MAX_SIGNIFICANT_DIGITS = 128
MAX_ABS_DECIMAL_EXPONENT = 4096
MAX_DOCUMENT_DEPTH = 128
MAX_DOCUMENT_NODES = 1_000_000
_TRANSACTION_ID = re.compile(r"^[0-9a-f]{32}$")
_JOURNAL_KEYS = {
    "schema", "transaction_id", "command", "project_identity", "stage",
    "backup_root", "source_digest",
    "operations", "marker_applied", "phase",
}
_PROJECT_IDENTITY_KEYS = {"device", "inode", "mount_id"}
_OPERATION_KEYS = {
    "target", "desired", "kind", "backup", "original", "started",
    "applied", "desired_digest", "backup_digest",
}
_ORIGINAL_KEYS = {"digest", "mode", "uid", "gid", "kind"}


class SetupError(Exception):
    def __init__(self, message: str, *, code: str = "SETUP_INVALID",
                 exit_code: int = 2, subject: str = "setup", pointer: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.subject = subject
        self.pointer = pointer


class _SetupHost:
    """Retained host anchors held for the complete setup transaction."""

    __slots__ = (
        "project_root", "project_fd", "bmad_fd", "project_identity",
        "bmad_fingerprint", "bmad_mount",
    )

    def __init__(
        self, *, project_root: Path, project_fd: int, bmad_fd: int,
        project_identity: dict[str, Any],
        bmad_fingerprint: tuple[int, ...], bmad_mount: str,
    ) -> None:
        self.project_root = project_root
        self.project_fd = project_fd
        self.bmad_fd = bmad_fd
        self.project_identity = project_identity
        self.bmad_fingerprint = bmad_fingerprint
        self.bmad_mount = bmad_mount


def resolve_sprint_planning_hook(active_workspace: str) -> dict[str, Any]:
    """Resolve the installed sprint hook without activating an unrelated plan."""
    if not isinstance(active_workspace, str):
        raise SetupError(
            "sdm_active_workspace must be a string",
            code="ANSWER_TYPE", subject="sdm_active_workspace",
            pointer="/modules/sdm/sdm_active_workspace",
        )
    if not active_workspace.strip():
        return {"disposition": "no_op", "activations": []}
    if active_workspace != active_workspace.strip():
        raise SetupError(
            "sdm_active_workspace must not contain surrounding whitespace",
            code="ANSWER_VALUE", subject="sdm_active_workspace",
            pointer="/modules/sdm/sdm_active_workspace",
        )
    return {
        "disposition": "activate",
        "activations": [{
            "skill": "sdm-ml-readiness",
            "workspace": active_workspace,
        }],
    }


class JsonParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise SetupError(message, code="CLI_USAGE", subject="cli")


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> bytes:
    def decimal_text(item: Decimal) -> str:
        _validate_decimal(item)
        if item == 0:
            return "0"
        sign, digit_tuple, exponent = item.as_tuple()
        digits = list(digit_tuple)
        while exponent < 0 and digits and digits[-1] == 0:
            digits.pop()
            exponent += 1
        raw = "".join(str(digit) for digit in digits)
        if exponent >= 0:
            rendered = raw + "0" * exponent
        else:
            point = len(raw) + exponent
            rendered = raw[:point] + "." + raw[point:] if point > 0 else "0." + "0" * -point + raw
        return ("-" if sign else "") + rendered

    def encode(item: Any, depth: int = 0) -> str:
        if depth > MAX_DOCUMENT_DEPTH:
            raise SetupError("canonical depth limit exceeded", code="DOCUMENT_RESOURCE_LIMIT")
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if isinstance(item, int) and not isinstance(item, bool):
            if not -(2**63) <= item <= 2**63 - 1:
                raise SetupError("integer outside signed 64-bit range", code="INTEGER_OUT_OF_RANGE")
            return str(item)
        if isinstance(item, Decimal):
            return decimal_text(item)
        if isinstance(item, float):
            if not math.isfinite(item):
                raise SetupError("non-finite number", code="NON_FINITE")
            return decimal_text(Decimal(str(item)))
        if isinstance(item, str):
            if unicodedata.normalize("NFC", item) != item:
                raise SetupError("string is not NFC", code="NON_NFC")
            return json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        if isinstance(item, list):
            return "[" + ",".join(encode(child, depth + 1) for child in item) + "]"
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise SetupError("JSON object key is not text", code="NON_STRING_KEY")
            return "{" + ",".join(encode(key, depth + 1) + ":" + encode(item[key], depth + 1) for key in sorted(item)) + "}"
        raise SetupError(f"unsupported canonical type: {type(item).__name__}", code="UNSUPPORTED_TYPE")

    encoded = encode(value).encode("utf-8")
    if len(encoded) > MAX_TREE_FILE_BYTES:
        raise SetupError("canonical bytes exceed fixed limit", code="DOCUMENT_TOO_LARGE")
    return encoded


def _validate_decimal(value: Decimal) -> None:
    if not value.is_finite():
        raise SetupError("non-finite JSON number", code="NON_FINITE")
    _, digits, exponent = value.as_tuple()
    adjusted = exponent + len(digits) - 1 if value else 0
    if len(digits) > MAX_SIGNIFICANT_DIGITS:
        raise SetupError("JSON number precision exceeds fixed limit", code="NUMBER_PRECISION")
    if abs(exponent) > MAX_ABS_DECIMAL_EXPONENT or abs(adjusted) > MAX_ABS_DECIMAL_EXPONENT:
        raise SetupError("JSON number exponent exceeds fixed limit", code="NUMBER_EXPONENT")


def _parse_setup_int(token: str) -> int:
    if len(token) > MAX_NUMBER_TOKEN_BYTES or len(token.lstrip("+-")) > 19:
        raise SetupError("JSON integer exceeds fixed limit", code="INTEGER_OUT_OF_RANGE")
    try:
        value = int(token, 10)
    except (ValueError, OverflowError) as exc:
        raise SetupError("invalid JSON integer", code="INVALID_NUMBER") from exc
    if not -(2**63) <= value <= 2**63 - 1:
        raise SetupError("JSON integer exceeds signed 64-bit range", code="INTEGER_OUT_OF_RANGE")
    return value


def _parse_setup_decimal(token: str) -> Decimal:
    if len(token) > MAX_NUMBER_TOKEN_BYTES:
        raise SetupError("JSON number token exceeds fixed limit", code="NUMBER_TOKEN_TOO_LONG")
    try:
        value = Decimal(token)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise SetupError("invalid JSON number", code="INVALID_NUMBER") from exc
    _validate_decimal(value)
    return value


def parse_setup_json(raw: bytes | str, label: str) -> Any:
    data = raw if isinstance(raw, bytes) else raw.encode("utf-8", "strict")
    if len(data) > MAX_TREE_FILE_BYTES:
        raise SetupError(f"{label} exceeds fixed document limit", code="DOCUMENT_TOO_LARGE")
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise SetupError(f"{label} is not strict UTF-8", code="INVALID_UTF8") from exc
    depth = 0
    nodes = 1
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            nodes += 1
            if depth > MAX_DOCUMENT_DEPTH:
                raise SetupError(f"{label} exceeds depth limit", code="DOCUMENT_RESOURCE_LIMIT")
        elif char in "}]":
            depth = max(0, depth - 1)
        elif char in ",:":
            nodes += 1
            if nodes > MAX_DOCUMENT_NODES * 2:
                raise SetupError(f"{label} exceeds node limit", code="DOCUMENT_RESOURCE_LIMIT")
    try:
        value = json.loads(
            text, object_pairs_hook=_strict_pairs,
            parse_int=_parse_setup_int, parse_float=_parse_setup_decimal,
            parse_constant=_reject_constant,
        )
    except SetupError:
        raise
    except (json.JSONDecodeError, RecursionError, MemoryError) as exc:
        raise SetupError(f"invalid {label}: {exc}", code="DOCUMENT_INVALID") from exc
    stack: list[tuple[Any, int]] = [(value, 0)]
    visited = 0
    while stack:
        item, item_depth = stack.pop()
        visited += 1
        if visited > MAX_DOCUMENT_NODES or item_depth > MAX_DOCUMENT_DEPTH:
            raise SetupError(f"{label} exceeds structural limits", code="DOCUMENT_RESOURCE_LIMIT")
        if isinstance(item, str) and unicodedata.normalize("NFC", item) != item:
            raise SetupError(f"{label} contains a non-NFC string", code="NON_NFC")
        if isinstance(item, list):
            stack.extend((child, item_depth + 1) for child in item)
        elif isinstance(item, dict):
            for key, child in item.items():
                stack.append((key, item_depth + 1))
                stack.append((child, item_depth + 1))
    return value


def _read_setup_json(path: Path, label: str) -> Any:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_TREE_FILE_BYTES:
            raise SetupError(f"{label} is unsafe or oversized", code="DOCUMENT_INVALID")
        data = bytearray()
        while len(data) < info.st_size:
            chunk = os.read(descriptor, min(1024 * 1024, info.st_size - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) != info.st_size or _stat_fingerprint(info) != _stat_fingerprint(os.fstat(descriptor)):
            raise SetupError(f"{label} changed while reading", code="TREE_CONFLICT", exit_code=4)
    finally:
        os.close(descriptor)
    return parse_setup_json(bytes(data), label)


def _read_setup_json_at(parent_fd: int, name: str, label: str) -> Any:
    """Read a setup document through a retained directory descriptor."""
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
            getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_fd,
        )
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOENT, errno.ENOTDIR):
            raise SetupError(
                f"{label} is unavailable or unsafe",
                code="DOCUMENT_INVALID", exit_code=3, subject=name,
            ) from exc
        raise
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > MAX_TREE_FILE_BYTES
        ):
            raise SetupError(
                f"{label} is unsafe or oversized",
                code="DOCUMENT_INVALID", exit_code=3, subject=name,
            )
        data = bytearray()
        while len(data) < info.st_size:
            chunk = os.read(
                descriptor, min(1024 * 1024, info.st_size - len(data)),
            )
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        try:
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise SetupError(
                f"{label} changed while reading", code="TREE_CONFLICT",
                exit_code=4, subject=name,
            ) from exc
        if (
            len(data) != info.st_size
            or os.read(descriptor, 1)
            or _stat_fingerprint(info) != _stat_fingerprint(after)
            or _stat_fingerprint(info) != _stat_fingerprint(named)
        ):
            raise SetupError(
                f"{label} changed while reading", code="TREE_CONFLICT",
                exit_code=4, subject=name,
            )
    finally:
        os.close(descriptor)
    return parse_setup_json(bytes(data), label)


def rule_digest(code: str) -> str:
    return digest_bytes(("sdm-setup/v1\0" + code).encode("utf-8"))


def finding(error: SetupError) -> dict[str, Any]:
    severity = "high" if error.exit_code in (2, 4, 5) else "medium"
    return {
        "severity": severity, "code": error.code, "subject": error.subject,
        "json_pointer": error.pointer, "message": str(error),
        "governing_asset": "sdm-setup/assets/module.yaml",
        "rule_digest": rule_digest(error.code), "gate_impact": "block",
    }


def emit(command: str, *, status: str, revision: int | None = None,
         result: Any = None, findings: list[dict[str, Any]] | None = None) -> None:
    payload = {
        "status": status, "command": command, "revision": revision,
        "result": result if result is not None else {},
        "findings": sorted(findings or [], key=lambda item: (
            item.get("severity", ""), item.get("code", ""),
            item.get("subject", ""), item.get("json_pointer", ""))),
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SetupError(f"duplicate JSON key: {key}", code="DUPLICATE_KEY",
                             pointer=f"/{key}")
        result[key] = value
    return result


def _reject_constant(token: str) -> Any:
    raise SetupError(f"non-finite JSON number: {token}", code="NON_FINITE")


def load_answers(raw: str | None) -> dict[str, str]:
    if raw is None:
        raise SetupError("--answers is required", code="ANSWERS_REQUIRED",
                         pointer="/answers")
    source = raw
    candidate = Path(raw)
    if not raw.lstrip().startswith("{") and candidate.is_file():
        descriptor = os.open(
            candidate,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_TREE_FILE_BYTES:
                raise SetupError("answers file is unsafe or oversized", code="ANSWERS_INVALID", pointer="/answers")
            data = bytearray()
            while len(data) < info.st_size:
                chunk = os.read(descriptor, min(1024 * 1024, info.st_size - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            source = bytes(data).decode("utf-8", "strict")
        finally:
            os.close(descriptor)
    try:
        value = parse_setup_json(source, "answers JSON")
    except SetupError:
        raise
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        raise SetupError(f"invalid answers JSON: {exc}", code="ANSWERS_INVALID",
                         pointer="/answers") from exc
    if not isinstance(value, dict):
        raise SetupError("answers must be one JSON object", code="ANSWERS_TYPE",
                         pointer="/answers")
    if set(value) != set(ANSWER_KEYS):
        missing = sorted(set(ANSWER_KEYS) - set(value))
        extra = sorted(set(value) - set(ANSWER_KEYS))
        raise SetupError(f"answers keys mismatch; missing={missing}, extra={extra}",
                         code="ANSWERS_SCHEMA", pointer="/answers")
    for key in ANSWER_KEYS:
        if (not isinstance(value[key], str) or "\x00" in value[key]
                or value[key] != value[key].strip()):
            raise SetupError(f"answer {key} must be a trimmed NUL-free string",
                             code="ANSWERS_SCHEMA", pointer=f"/answers/{key}")
    for key in ("sdm_output_root", "sdm_trust_root"):
        if not value[key]:
            raise SetupError(f"answer {key} must not be empty",
                             code="ANSWER_VALUE", pointer=f"/answers/{key}")
    for key in ("data_platform_skill", "source_control_platform_skill",
                "ci_platform_skill", "ml_execution_platform_skill"):
        identity = value[key]
        if identity and (identity in (".", "..") or "/" in identity or "\\" in identity):
            raise SetupError(f"answer {key} must be an exact Skill directory name",
                             code="ANSWER_VALUE", pointer=f"/answers/{key}")
    return {key: value[key] for key in ANSWER_KEYS}


def ensure_contained(root: Path, path: Path) -> None:
    root_real = root.resolve(strict=True)
    path_real = path.resolve(strict=False)
    try:
        path_real.relative_to(root_real)
    except ValueError as exc:
        raise SetupError(f"path escapes project root: {path}", code="PATH_ESCAPE",
                         subject=str(path)) from exc


def _stat_fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    """Identity and mutation-sensitive metadata, deliberately excluding atime."""
    return (
        metadata.st_mode, metadata.st_dev, metadata.st_ino,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _anchor_fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    """Stable identity for ancestors that may contain unrelated activity."""
    return (metadata.st_mode, metadata.st_dev, metadata.st_ino)


def _stat_view(metadata: os.stat_result, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "mode": stat.S_IMODE(metadata.st_mode),
        "dev": metadata.st_dev,
        "ino": metadata.st_ino,
        "nlink": metadata.st_nlink,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
    }


def _mount_identity(descriptor: int) -> str:
    """Return a trustworthy Linux mount identity or fail closed."""
    fdinfo_path = f"/proc/self/fdinfo/{descriptor}"
    try:
        info_fd = os.open(fdinfo_path, os.O_RDONLY |
                         getattr(os, "O_CLOEXEC", 0) |
                         getattr(os, "O_NOFOLLOW", 0))
        try:
            chunks: list[bytes] = []
            while True:
                chunk = os.read(info_fd, 4096)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(info_fd)
        for line in b"".join(chunks).splitlines():
            if line.startswith(b"mnt_id:"):
                return "mnt_id:" + line.split(b":", 1)[1].strip().decode("ascii")
    except (OSError, UnicodeError):
        pass
    raise SetupError(
        "trustworthy mount identity is unavailable",
        code="MOUNT_ID_UNAVAILABLE", subject=fdinfo_path,
    )


def _project_identity_from_fd(descriptor: int) -> dict[str, Any]:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise SetupError(
            "setup project root is not a directory",
            code="UNSAFE_NODE", subject="project-root",
        )
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mount_id": _mount_identity(descriptor),
    }


def _project_identity(path: Path) -> dict[str, Any]:
    descriptor = os.open(path, _DIR_OPEN_FLAGS)
    try:
        opened = os.fstat(descriptor)
        named = path.lstat()
        if (
            stat.S_ISLNK(named.st_mode)
            or not stat.S_ISDIR(named.st_mode)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise SetupError(
                "setup project root changed while it was opened",
                code="TREE_CONFLICT", exit_code=4, subject=str(path),
            )
        return _project_identity_from_fd(descriptor)
    finally:
        os.close(descriptor)


class _RetainedWalker:
    """Read a tree through retained descriptors and verify every path anchor.

    The validator must not make an authorization decision from a pathname that
    can be rebound after inspection.  This walker opens every component with
    ``O_NOFOLLOW``, retains all directory and file descriptors until the
    capture is verified, and compares both descriptor identity and the parent
    directory entry before releasing anything.
    """

    def __init__(self, root: Path) -> None:
        self.root_path = Path(os.path.abspath(os.fspath(root)))
        self._fds: list[int] = []
        self._handles: list[dict[str, Any]] = []
        self._fd_prefix: dict[int, str] = {}
        self._scans: dict[str, tuple[tuple[str, ...], tuple[int, ...], str]] = {}
        self._seen_subjects: set[str] = set()
        self._node_count = 1
        self._total_bytes = 0
        try:
            soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
            baseline = len(os.listdir("/proc/self/fd"))
            available = MAX_RETAINED_FDS if soft_limit == resource.RLIM_INFINITY else int(soft_limit) - baseline - 4
            self._fd_budget = min(MAX_RETAINED_FDS, max(0, available))
        except (OSError, ValueError):
            self._fd_budget = MAX_RETAINED_FDS
        if self._fd_budget < 2:
            raise SetupError(
                "insufficient file-descriptor budget for governed traversal",
                code="TREE_RESOURCE_LIMIT", subject=str(self.root_path),
            )
        self.nodes: dict[str, dict[str, Any] | None] = {}
        self.files: dict[str, bytes] = {}
        try:
            self.root_fd = self._open_absolute_root()
            root_stat = os.fstat(self.root_fd)
            self.root_dev = root_stat.st_dev
            self.root_mount = _mount_identity(self.root_fd)
            self._fd_prefix[self.root_fd] = ""
            self.nodes["."] = _stat_view(root_stat, "dir")
        except Exception:
            self.close()
            raise

    def __enter__(self) -> "_RetainedWalker":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        for descriptor in reversed(self._fds):
            with contextlib.suppress(OSError):
                os.close(descriptor)
        self._fds.clear()
        self._fd_prefix.clear()

    def _track_fd(self, descriptor: int, subject: str) -> int:
        if len(self._fds) >= self._fd_budget:
            os.close(descriptor)
            raise SetupError(
                "governed traversal exceeded its retained descriptor budget",
                code="TREE_RESOURCE_LIMIT", subject=subject,
            )
        self._fds.append(descriptor)
        return descriptor

    def _release_fd(self, descriptor: int) -> None:
        self._fd_prefix.pop(descriptor, None)
        if descriptor in self._fds:
            self._fds.remove(descriptor)
        with contextlib.suppress(OSError):
            os.close(descriptor)

    def _open(self, path: str | os.PathLike[str], flags: int, *, subject: str,
              dir_fd: int | None = None) -> int:
        try:
            descriptor = os.open(path, flags, dir_fd=dir_fd)
        except OSError as exc:
            if exc.errno in (errno.EMFILE, errno.ENFILE):
                raise SetupError(
                    "governed traversal exhausted file descriptors",
                    code="TREE_RESOURCE_LIMIT", subject=subject,
                ) from exc
            raise
        return self._track_fd(descriptor, subject)

    def _account_entry(self, metadata: os.stat_result, subject: str) -> None:
        if subject in self._seen_subjects:
            return
        self._seen_subjects.add(subject)
        self._node_count += 1
        if self._node_count > MAX_TREE_NODES:
            raise SetupError(
                "governed tree exceeds the fixed node limit",
                code="TREE_RESOURCE_LIMIT", subject=subject,
            )
        if stat.S_ISREG(metadata.st_mode):
            if metadata.st_size > MAX_TREE_FILE_BYTES:
                raise SetupError(
                    "governed file exceeds the fixed per-file byte limit",
                    code="TREE_RESOURCE_LIMIT", subject=subject,
                )
            self._total_bytes += metadata.st_size
            if self._total_bytes > MAX_TREE_BYTES:
                raise SetupError(
                    "governed tree exceeds the fixed aggregate byte limit",
                    code="TREE_RESOURCE_LIMIT", subject=subject,
                )

    def _open_absolute_root(self) -> int:
        parent_fd = self._open(os.sep, _DIR_OPEN_FLAGS, subject=os.sep)
        current_path = Path(os.sep)
        parts = self.root_path.parts[1:]
        if not parts:
            return parent_fd
        for component in parts:
            current_path /= component
            try:
                before = os.stat(component, dir_fd=parent_fd,
                                 follow_symlinks=False)
            except FileNotFoundError:
                raise
            if stat.S_ISLNK(before.st_mode):
                raise SetupError(
                    f"symlink rejected: {current_path}",
                    code="SYMLINK_REJECTED", subject=str(current_path),
                )
            if not stat.S_ISDIR(before.st_mode):
                raise SetupError(
                    f"tree root component is not a directory: {current_path}",
                    code="UNSAFE_NODE", subject=str(current_path),
                )
            try:
                descriptor = self._open(
                    component, _DIR_OPEN_FLAGS, dir_fd=parent_fd,
                    subject=str(current_path),
                )
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise SetupError(
                        f"unsafe directory component rejected: {current_path}",
                        code="SYMLINK_REJECTED", subject=str(current_path),
                    ) from exc
                raise
            opened = os.fstat(descriptor)
            if _stat_fingerprint(before) != _stat_fingerprint(opened):
                raise SetupError(
                    f"directory identity changed while opening: {current_path}",
                    code="TREE_CONFLICT", exit_code=4,
                    subject=str(current_path),
                )
            self._handles.append({
                "fd": descriptor, "parent_fd": parent_fd,
                "name": component, "fingerprint": _anchor_fingerprint(opened),
                "subject": str(current_path), "kind": "dir",
            })
            parent_fd = descriptor
        return parent_fd

    def scan(self, descriptor: int, subject: str) -> tuple[str, ...]:
        before = os.fstat(descriptor)
        if not stat.S_ISDIR(before.st_mode):
            raise SetupError(f"not a directory: {subject}", code="UNSAFE_NODE",
                             subject=subject)
        names = tuple(sorted(os.listdir(descriptor)))
        after = os.fstat(descriptor)
        if _stat_fingerprint(before) != _stat_fingerprint(after):
            raise SetupError(
                f"directory changed while scanning: {subject}",
                code="TREE_CONFLICT", exit_code=4, subject=subject,
            )
        prefix = self._fd_prefix.get(descriptor)
        if prefix is not None:
            record = (names, _stat_fingerprint(before), subject)
            prior = self._scans.get(prefix)
            if prior is not None and prior[:2] != record[:2]:
                raise SetupError(
                    f"directory changed between scans: {subject}",
                    code="TREE_CONFLICT", exit_code=4, subject=subject,
                )
            self._scans[prefix] = record
        return names

    def entry_stat(self, parent_fd: int, name: str, subject: str) -> os.stat_result:
        try:
            metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise SetupError(
                f"entry disappeared during traversal: {subject}",
                code="TREE_CONFLICT", exit_code=4, subject=subject,
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise SetupError(f"symlink rejected: {subject}",
                             code="SYMLINK_REJECTED", subject=subject)
        if metadata.st_dev != self.root_dev:
            raise SetupError(f"mount boundary rejected: {subject}",
                             code="MOUNT_ESCAPE", subject=subject)
        self._account_entry(metadata, subject)
        return metadata

    def open_dir(self, parent_fd: int, name: str, subject: str,
                 before: os.stat_result | None = None) -> int:
        expected = before or self.entry_stat(parent_fd, name, subject)
        if not stat.S_ISDIR(expected.st_mode):
            raise SetupError(f"expected directory: {subject}",
                             code="UNSAFE_NODE", subject=subject)
        try:
            descriptor = self._open(
                name, _DIR_OPEN_FLAGS, dir_fd=parent_fd, subject=subject,
            )
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.ENOENT):
                raise SetupError(
                    f"directory changed while opening: {subject}",
                    code="TREE_CONFLICT", exit_code=4, subject=subject,
                ) from exc
            raise
        opened = os.fstat(descriptor)
        opened_mount = _mount_identity(descriptor)
        if (opened.st_dev != self.root_dev or opened_mount != self.root_mount):
            raise SetupError(f"mount boundary rejected: {subject}",
                             code="MOUNT_ESCAPE", subject=subject)
        if _stat_fingerprint(expected) != _stat_fingerprint(opened):
            raise SetupError(
                f"directory identity changed while opening: {subject}",
                code="TREE_CONFLICT", exit_code=4, subject=subject,
            )
        parent_prefix = self._fd_prefix.get(parent_fd)
        if parent_prefix is not None:
            self._fd_prefix[descriptor] = f"{parent_prefix}/{name}".strip("/")
        return descriptor

    def read_file(self, parent_fd: int, name: str, subject: str,
                  before: os.stat_result | None = None) -> tuple[bytes, os.stat_result]:
        expected = before or self.entry_stat(parent_fd, name, subject)
        if not stat.S_ISREG(expected.st_mode):
            raise SetupError(f"special node rejected: {subject}",
                             code="SPECIAL_NODE", subject=subject)
        if expected.st_nlink != 1:
            raise SetupError(f"hardlinked file rejected: {subject}",
                             code="HARDLINK_REJECTED", subject=subject)
        try:
            descriptor = self._open(
                name, _FILE_OPEN_FLAGS, dir_fd=parent_fd, subject=subject,
            )
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOENT, errno.ENXIO):
                raise SetupError(
                    f"file changed while opening: {subject}",
                    code="TREE_CONFLICT", exit_code=4, subject=subject,
                ) from exc
            raise
        try:
            opened = os.fstat(descriptor)
            opened_mount = _mount_identity(descriptor)
            if opened.st_dev != self.root_dev or opened_mount != self.root_mount:
                raise SetupError(f"mount boundary rejected: {subject}",
                                 code="MOUNT_ESCAPE", subject=subject)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                    _stat_fingerprint(expected) != _stat_fingerprint(opened)):
                raise SetupError(
                    f"file identity changed while opening: {subject}",
                    code="TREE_CONFLICT", exit_code=4, subject=subject,
                )
            if opened.st_size > MAX_TREE_FILE_BYTES:
                raise SetupError(
                    f"governed file exceeds the fixed byte limit: {subject}",
                    code="TREE_RESOURCE_LIMIT", subject=subject,
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
            if (_stat_fingerprint(opened) != _stat_fingerprint(after) or
                    len(data) != opened.st_size):
                raise SetupError(
                    f"file changed while reading: {subject}",
                    code="TREE_CONFLICT", exit_code=4, subject=subject,
                )
            return bytes(data), opened
        finally:
            self._release_fd(descriptor)

    def capture_contents(
        self, descriptor: int, *, local_prefix: str, global_prefix: str,
        tree_hasher: Any,
    ) -> None:
        subject = str(self.root_path / global_prefix)
        for name in self.scan(descriptor, subject):
            local = f"{local_prefix}/{name}" if local_prefix else name
            global_name = f"{global_prefix}/{name}" if global_prefix else name
            child_subject = str(self.root_path / global_name)
            metadata = self.entry_stat(descriptor, name, child_subject)
            if stat.S_ISDIR(metadata.st_mode):
                child_fd = self.open_dir(descriptor, name, child_subject,
                                         metadata)
                self.nodes[global_name + "/"] = _stat_view(metadata, "dir")
                _hash_tree_record(
                    tree_hasher, local + "/", metadata.st_mode, None,
                )
                try:
                    self.capture_contents(
                        child_fd, local_prefix=local, global_prefix=global_name,
                        tree_hasher=tree_hasher,
                    )
                finally:
                    self._release_fd(child_fd)
            elif stat.S_ISREG(metadata.st_mode):
                data, opened = self.read_file(descriptor, name, child_subject,
                                              metadata)
                view = _stat_view(opened, "file")
                view["digest"] = digest_bytes(data)
                self.nodes[global_name] = view
                _hash_tree_record(
                    tree_hasher, local, opened.st_mode, data,
                )
                # Only a small, fixed semantic roster needs exact bytes after
                # the tree digest is streamed.  All other file bytes are
                # released immediately instead of accumulating a second copy
                # of the governed tree in memory.
                if (
                    global_name.endswith("/SKILL.md")
                    or global_name == "sdm-setup/assets/module-help.csv"
                ):
                    self.files[global_name] = data
            else:
                raise SetupError(f"special node rejected: {child_subject}",
                                 code="SPECIAL_NODE", subject=child_subject)

    def read_relative_file(self, relative: str) -> bytes | None:
        parts = Path(relative).parts
        if (not parts or Path(relative).is_absolute() or
                any(part in ("", ".", "..") for part in parts)):
            raise SetupError(f"unsafe validation target: {relative}",
                             code="PATH_ESCAPE", subject=relative)
        descriptor = self.root_fd
        opened_directories: list[int] = []
        prefix: list[str] = []
        try:
            for component in parts[:-1]:
                directory_subject = str(self.root_path.joinpath(*prefix))
                names = self.scan(descriptor, directory_subject)
                prefix.append(component)
                key = "/".join(prefix) + "/"
                if component not in names:
                    self.nodes[key] = None
                    self.nodes[relative] = None
                    return None
                child_subject = str(self.root_path.joinpath(*prefix))
                metadata = self.entry_stat(descriptor, component, child_subject)
                descriptor = self.open_dir(
                    descriptor, component, child_subject, metadata,
                )
                opened_directories.append(descriptor)
                self.nodes[key] = _stat_view(metadata, "dir")
            parent_subject = str(self.root_path.joinpath(*parts[:-1]))
            names = self.scan(descriptor, parent_subject)
            if parts[-1] not in names:
                self.nodes[relative] = None
                return None
            subject = str(self.root_path.joinpath(*parts))
            metadata = self.entry_stat(descriptor, parts[-1], subject)
            data, opened = self.read_file(
                descriptor, parts[-1], subject, metadata,
            )
            view = _stat_view(opened, "file")
            view["digest"] = digest_bytes(data)
            self.nodes[relative] = view
            self.files[relative] = data
            return data
        finally:
            for child_fd in reversed(opened_directories):
                self._release_fd(child_fd)

    def verify(self) -> None:
        def open_directory(relative: str) -> tuple[int, list[int]]:
            descriptor = self.root_fd
            opened: list[int] = []
            prefix: list[str] = []
            try:
                for component in Path(relative).parts if relative else ():
                    prefix.append(component)
                    subject = str(self.root_path.joinpath(*prefix))
                    expected = os.stat(
                        component, dir_fd=descriptor, follow_symlinks=False,
                    )
                    if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
                        raise SetupError(
                            f"path anchor changed during traversal: {subject}",
                            code="TREE_CONFLICT", exit_code=4, subject=subject,
                        )
                    child = self._open(
                        component, _DIR_OPEN_FLAGS, dir_fd=descriptor,
                        subject=subject,
                    )
                    opened.append(child)
                    current = os.fstat(child)
                    if (current.st_dev != self.root_dev or
                            _mount_identity(child) != self.root_mount or
                            _stat_fingerprint(expected) != _stat_fingerprint(current)):
                        raise SetupError(
                            f"path identity changed during traversal: {subject}",
                            code="TREE_CONFLICT", exit_code=4, subject=subject,
                        )
                    descriptor = child
                return descriptor, opened
            except Exception:
                for child in reversed(opened):
                    self._release_fd(child)
                raise

        for prefix, (names, fingerprint, subject) in self._scans.items():
            descriptor, opened = open_directory(prefix)
            try:
                current = os.fstat(descriptor)
                if (_stat_fingerprint(current) != fingerprint or
                        tuple(sorted(os.listdir(descriptor))) != names):
                    raise SetupError(
                        f"directory entries changed during traversal: {subject}",
                        code="TREE_CONFLICT", exit_code=4, subject=subject,
                    )
            finally:
                for child in reversed(opened):
                    self._release_fd(child)
        for handle in self._handles:
            try:
                descriptor_stat = os.fstat(handle["fd"])
                entry_stat = os.stat(
                    handle["name"], dir_fd=handle["parent_fd"],
                    follow_symlinks=False,
                )
            except (FileNotFoundError, OSError) as exc:
                raise SetupError(
                    f"path anchor changed during traversal: {handle['subject']}",
                    code="TREE_CONFLICT", exit_code=4,
                    subject=handle["subject"],
                ) from exc
            expected = handle["fingerprint"]
            if (_anchor_fingerprint(descriptor_stat) != expected or
                    _anchor_fingerprint(entry_stat) != expected or
                    ("mount" in handle and
                     _mount_identity(handle["fd"]) != handle["mount"])):
                raise SetupError(
                    f"path identity changed during traversal: {handle['subject']}",
                    code="TREE_CONFLICT", exit_code=4,
                    subject=handle["subject"],
                )
        for relative, expected in self.nodes.items():
            if relative == ".":
                continue
            portable = relative.removesuffix("/")
            parts = Path(portable).parts
            parent = "/".join(parts[:-1])
            try:
                descriptor, opened = open_directory(parent)
            except (FileNotFoundError, SetupError) as exc:
                if expected is None and isinstance(exc, FileNotFoundError):
                    continue
                raise SetupError(
                    f"captured path changed during traversal: {portable}",
                    code="TREE_CONFLICT", exit_code=4, subject=portable,
                ) from exc
            try:
                try:
                    current = os.stat(
                        parts[-1], dir_fd=descriptor, follow_symlinks=False,
                    )
                except FileNotFoundError:
                    if expected is None:
                        continue
                    raise SetupError(
                        f"captured path disappeared: {portable}",
                        code="TREE_CONFLICT", exit_code=4, subject=portable,
                    )
                if expected is None:
                    raise SetupError(
                        f"previously absent path appeared: {portable}",
                        code="TREE_CONFLICT", exit_code=4, subject=portable,
                    )
                kind = "dir" if stat.S_ISDIR(current.st_mode) else "file" if stat.S_ISREG(current.st_mode) else "special"
                current_view = _stat_view(current, kind)
                for key in ("kind", "mode", "dev", "ino", "nlink", "uid", "gid", "size", "mtime_ns", "ctime_ns"):
                    if current_view[key] != expected[key]:
                        raise SetupError(
                            f"captured path identity changed: {portable}",
                            code="TREE_CONFLICT", exit_code=4,
                            subject=portable,
                        )
                if kind == "file":
                    file_fd = self._open(
                        parts[-1], _FILE_OPEN_FLAGS, dir_fd=descriptor,
                        subject=portable,
                    )
                    try:
                        if _mount_identity(file_fd) != self.root_mount:
                            raise SetupError(
                                f"captured file mount changed: {portable}",
                                code="TREE_CONFLICT", exit_code=4,
                                subject=portable,
                            )
                        hasher = hashlib.sha256()
                        while chunk := os.read(file_fd, 1024 * 1024):
                            hasher.update(chunk)
                        if "sha256:" + hasher.hexdigest() != expected["digest"]:
                            raise SetupError(
                                f"file content changed during traversal: {portable}",
                                code="TREE_CONFLICT", exit_code=4,
                                subject=portable,
                            )
                    finally:
                        self._release_fd(file_fd)
            finally:
                for child in reversed(opened):
                    self._release_fd(child)


def _hash_tree_record(
    hasher: Any, relative: str, mode: int, data: bytes | None,
) -> None:
    encoded = relative.encode("utf-8")
    hasher.update(len(encoded).to_bytes(8, "big"))
    hasher.update(encoded)
    hasher.update((mode & 0o7777).to_bytes(4, "big"))
    if data is not None:
        hasher.update(len(data).to_bytes(8, "big"))
        hasher.update(data)


def _validate_skill_frontmatter(raw: bytes, expected_name: str) -> None:
    lines = raw.splitlines()
    if not lines or lines[0] != b"---":
        raise SetupError(
            f"SKILL frontmatter is not at byte zero: {expected_name}",
            code="SKILL_FRONTMATTER", subject=expected_name,
        )
    try:
        closing = lines[1:].index(b"---") + 1
    except ValueError as exc:
        raise SetupError(
            f"SKILL frontmatter is unterminated: {expected_name}",
            code="SKILL_FRONTMATTER", subject=expected_name,
        ) from exc
    names: list[str] = []
    for line in lines[1:closing]:
        match = re.fullmatch(br"name:\s*(.*?)\s*", line)
        if match:
            value = match.group(1).decode("utf-8", errors="strict")
            if (len(value) >= 2 and value[0] == value[-1] and
                    value[0] in ("'", '"')):
                value = value[1:-1]
            names.append(value)
    if names != [expected_name]:
        raise SetupError(
            f"SKILL name mismatch for {expected_name}: {names}",
            code="SKILL_FRONTMATTER", subject=expected_name,
        )


def _secure_module_capture(root: Path, *, source: bool) -> dict[str, Any]:
    inventory_code = "SOURCE_INVENTORY" if source else "PARTIAL_INSTALL"
    try:
        walker = _RetainedWalker(root)
    except FileNotFoundError:
        if source:
            raise SetupError(f"source directory does not exist: {root}",
                             code=inventory_code, subject=str(root))
        return {"exists": False, "digest": None, "skills": [],
                "nodes": {}, "files": {}}
    with walker:
        names = walker.scan(walker.root_fd, str(walker.root_path))
        roster = SKILLS
        expected = sorted(roster)
        found = [name for name in names if name.startswith("sdm-")]
        if source:
            # A source module may contain tests and top-level documentation,
            # but any other direct child that itself exposes SKILL.md is an
            # orphan/duplicate module capability and must fail the exact roster.
            for name in names:
                if name in found:
                    continue
                subject = str(walker.root_path / name)
                metadata = walker.entry_stat(walker.root_fd, name, subject)
                if not stat.S_ISDIR(metadata.st_mode):
                    continue
                child_fd = walker.open_dir(walker.root_fd, name, subject,
                                           metadata)
                try:
                    child_names = walker.scan(child_fd, subject)
                    if "SKILL.md" in child_names:
                        found.append(name)
                finally:
                    walker._release_fd(child_fd)
        found.sort()
        if not source and found == sorted(LEGACY_SKILLS):
            roster = LEGACY_SKILLS
            expected = sorted(roster)
        if not source and not found:
            walker.verify()
            return {
                "exists": True, "digest": None, "skills": [],
                "direct_sdm_entries": [], "nodes": walker.nodes,
                "files": walker.files, "skill_digests": {},
            }
        if found != expected:
            exit_code = 2 if source else 4
            raise SetupError(
                f"{'source' if source else 'installed'} Skill inventory "
                f"mismatch: {found}", code=inventory_code,
                exit_code=exit_code, subject=str(walker.root_path),
            )
        skill_digests: dict[str, str] = {}
        for name in roster:
            subject = str(walker.root_path / name)
            metadata = walker.entry_stat(walker.root_fd, name, subject)
            skill_fd = walker.open_dir(walker.root_fd, name, subject, metadata)
            walker.nodes[name + "/"] = _stat_view(metadata, "dir")
            tree_hasher = hashlib.sha256()
            tree_hasher.update(b"sdm-tree-v1\0")
            try:
                walker.capture_contents(
                    skill_fd, local_prefix="", global_prefix=name,
                    tree_hasher=tree_hasher,
                )
            finally:
                walker._release_fd(skill_fd)
            skill_md = walker.files.get(f"{name}/SKILL.md")
            if skill_md is None:
                raise SetupError(
                    f"Skill is missing SKILL.md: {name}",
                    code=inventory_code, subject=name,
                )
            _validate_skill_frontmatter(skill_md, name)
            skill_digests[name] = "sha256:" + tree_hasher.hexdigest()
        walker.verify()
        hasher = hashlib.sha256()
        hasher.update(b"sdm-module-v1\0")
        for name in roster:
            hasher.update(name.encode() + b"\0" +
                          skill_digests[name].encode() + b"\0")
        return {
            "exists": True,
            "digest": "sha256:" + hasher.hexdigest(),
            "skills": list(roster),
            "legacy": roster == LEGACY_SKILLS,
            "direct_sdm_entries": found,
            "nodes": walker.nodes,
            "files": walker.files,
            "skill_digests": skill_digests,
        }


def _secure_owned_capture(project_root: Path) -> dict[str, Any]:
    with _RetainedWalker(project_root) as walker:
        for relative in _VALIDATION_FILES:
            walker.read_relative_file(relative)
        walker.verify()
        return {"nodes": walker.nodes, "files": walker.files}


def tree_digest(root: Path) -> str:
    with _RetainedWalker(root) as walker:
        hasher = hashlib.sha256()
        hasher.update(b"sdm-tree-v1\0")
        walker.capture_contents(
            walker.root_fd,
            local_prefix="",
            global_prefix="",
            tree_hasher=hasher,
        )
        walker.verify()
        return "sha256:" + hasher.hexdigest()


def source_module_digest(source: Path) -> str:
    capture = _secure_module_capture(source, source=True)
    return str(capture["digest"])


def module_digest(skills_root: Path) -> str | None:
    capture = _secure_module_capture(skills_root, source=False)
    return capture["digest"]


def validate_source(source: Path) -> str:
    capture = _secure_module_capture(source, source=True)
    return str(capture["digest"])


_HOST_BYTES_UNSET = object()


def _read_bounded_path_bytes(
    path: Path, *, label: str, code: str,
) -> bytes | None:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
            getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SetupError(
            f"unsafe {label} at {path}: {exc}", code=code,
            subject=str(path),
        ) from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > MAX_TREE_FILE_BYTES
        ):
            raise SetupError(
                f"unsafe or oversized {label}: {path}", code=code,
                subject=str(path),
            )
        data = bytearray()
        while len(data) < info.st_size:
            chunk = os.read(
                descriptor, min(1024 * 1024, info.st_size - len(data)),
            )
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        named = path.lstat()
        if (
            len(data) != info.st_size
            or os.read(descriptor, 1)
            or _stat_fingerprint(info) != _stat_fingerprint(after)
            or _stat_fingerprint(info) != _stat_fingerprint(named)
        ):
            raise SetupError(
                f"{label} changed while reading: {path}",
                code="TREE_CONFLICT", exit_code=4, subject=str(path),
            )
        return bytes(data)
    except FileNotFoundError as exc:
        raise SetupError(
            f"{label} changed while reading: {path}",
            code="TREE_CONFLICT", exit_code=4, subject=str(path),
        ) from exc
    finally:
        os.close(descriptor)


def _load_toml_bytes(raw: bytes | None, path: Path) -> dict[str, Any]:
    if raw is None:
        return {}
    try:
        value = tomllib.loads(raw.decode("utf-8", "strict"))
    except (tomllib.TOMLDecodeError, UnicodeError) as exc:
        raise SetupError(
            f"invalid TOML at {path}: {exc}", code="HOST_TOML_INVALID",
            subject=str(path),
        ) from exc
    return value


def load_toml(path: Path) -> dict[str, Any]:
    return _load_toml_bytes(
        _read_bounded_path_bytes(
            path, label="host TOML", code="HOST_TOML_INVALID",
        ),
        path,
    )


_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def toml_key(value: str) -> str:
    return value if _BARE_KEY.fullmatch(value) else json.dumps(value,
                                                               ensure_ascii=False)


def toml_scalar(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return repr(value)
    if isinstance(value, list) and all(not isinstance(item, dict) for item in value):
        return "[" + ", ".join(toml_scalar(item) for item in value) + "]"
    raise SetupError(f"unsupported TOML value: {type(value).__name__}",
                     code="HOST_TOML_UNSUPPORTED")


def dump_toml(data: dict[str, Any]) -> bytes:
    lines: list[str] = []

    def emit_table(table: dict[str, Any], path: tuple[str, ...], header: bool) -> None:
        if header:
            lines.append("[" + ".".join(toml_key(part) for part in path) + "]")
        scalars = [(key, value) for key, value in table.items()
                   if not isinstance(value, dict)
                   and not (isinstance(value, list) and value and
                            isinstance(value[0], dict))]
        children = [(key, value) for key, value in table.items()
                    if isinstance(value, dict)]
        arrays = [(key, value) for key, value in table.items()
                  if isinstance(value, list) and value and
                  isinstance(value[0], dict)]
        for key, value in scalars:
            lines.append(f"{toml_key(str(key))} = {toml_scalar(value)}")
        if scalars and (children or arrays):
            lines.append("")
        for index, (key, child) in enumerate(children):
            emit_table(child, path + (str(key),), True)
            if index != len(children) - 1 or arrays:
                lines.append("")
        for array_index, (key, items) in enumerate(arrays):
            for item_index, item in enumerate(items):
                lines.append("[[" + ".".join(toml_key(part) for part in
                                               path + (str(key),)) + "]]")
                for item_key, item_value in item.items():
                    if isinstance(item_value, dict):
                        raise SetupError("nested table in array-of-table unsupported",
                                         code="HOST_TOML_UNSUPPORTED")
                    lines.append(f"{toml_key(str(item_key))} = {toml_scalar(item_value)}")
                if item_index != len(items) - 1:
                    lines.append("")
            if array_index != len(arrays) - 1:
                lines.append("")

    emit_table(data, (), False)
    while lines and lines[-1] == "":
        lines.pop()
    return ("\n".join(lines) + "\n").encode("utf-8")


def render_config(
    path: Path, answers: dict[str, str], install_digest: str,
    source_digest: str, *, personal: bool,
    existing: bytes | None | object = _HOST_BYTES_UNSET,
) -> bytes:
    data = load_toml(path) if existing is _HOST_BYTES_UNSET \
        else _load_toml_bytes(existing, path)
    modules = data.setdefault("modules", {})
    if not isinstance(modules, dict):
        raise SetupError("config modules key must be a table",
                         code="HOST_TOML_INVALID", subject=str(path),
                         pointer="/modules")
    if personal:
        hook = resolve_sprint_planning_hook(answers["sdm_active_workspace"])
        active_workspace = "" if hook["disposition"] == "no_op" \
            else hook["activations"][0]["workspace"]
        modules[MODULE_CODE] = {
            "sdm_active_workspace": active_workspace}
    else:
        module = {key: answers[key] for key in SHARED_KEYS}
        module.update({"name": MODULE_NAME, "version": "1.0.0",
                       "install_digest": install_digest,
                       "source_digest": source_digest})
        modules[MODULE_CODE] = module
    return dump_toml(data)


def render_config_without_sdm(
    path: Path, *, existing: bytes | None | object = _HOST_BYTES_UNSET,
) -> bytes:
    data = load_toml(path) if existing is _HOST_BYTES_UNSET \
        else _load_toml_bytes(existing, path)
    modules = data.get("modules")
    if isinstance(modules, dict):
        modules.pop(MODULE_CODE, None)
        if not modules:
            data.pop("modules", None)
    return dump_toml(data)


def render_hook(
    path: Path, *, remove: bool = False,
    existing: bytes | None | object = _HOST_BYTES_UNSET,
) -> bytes:
    data = load_toml(path) if existing is _HOST_BYTES_UNSET \
        else _load_toml_bytes(existing, path)
    workflow = data.setdefault("workflow", {})
    if not isinstance(workflow, dict):
        raise SetupError("hook workflow key must be a table",
                         code="HOST_TOML_INVALID", subject=str(path),
                         pointer="/workflow")
    current = workflow.get("activation_steps_prepend", [])
    if not isinstance(current, list) or not all(isinstance(item, str)
                                                for item in current):
        raise SetupError("activation_steps_prepend must be a string array",
                         code="HOST_TOML_INVALID", subject=str(path),
                         pointer="/workflow/activation_steps_prepend")
    kept = [item for item in current
            if "modules.sdm.sdm_active_workspace" not in item]
    if not remove:
        kept.append(HOOK_TEXT)
    workflow["activation_steps_prepend"] = kept
    return dump_toml(data)


def _parse_help_bytes(raw: bytes, subject: str, *, asset: bool,
                      roster: tuple[str, ...] = SKILLS) \
        -> tuple[list[str], list[list[str]]]:
    code = "HELP_ASSET_INVALID" if asset else "HOST_HELP_INVALID"
    try:
        text = raw.decode("utf-8")
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except (UnicodeError, csv.Error) as exc:
        raise SetupError(f"invalid help CSV at {subject}: {exc}", code=code,
                         subject=subject) from exc
    if not rows or tuple(rows[0]) != CSV_HEADER:
        raise SetupError(f"help CSV header mismatch: {subject}", code=code,
                         subject=subject)
    body = rows[1:]
    if any(len(row) != len(CSV_HEADER) for row in body):
        raise SetupError(f"help CSV row width mismatch: {subject}", code=code,
                         subject=subject)
    if asset:
        skills = [row[1] for row in body]
        menus = [row[3] for row in body]
        if (len(body) != len(roster) or skills != list(roster) or
                len(set(skills)) != len(skills) or
                len(set(menus)) != len(menus)):
            raise SetupError(
                f"module-help Skill roster/order is not canonical: {subject}",
                code=code, subject=subject,
            )
        required_fields = (0, 1, 2, 3, 4, 5, 6, 7, 10, 11, 12)
        for row in body:
            if (row[0] != MODULE_NAME or
                    any(not row[index].strip() for index in required_fields) or
                    row[10] not in ("true", "false")):
                raise SetupError(
                    f"module-help row is semantically invalid: {row[1]}",
                    code=code, subject=subject,
                )
    return rows[0], body


def read_help_asset(source: Path) -> tuple[list[str], list[list[str]]]:
    relative = "sdm-setup/assets/module-help.csv"
    with _RetainedWalker(source) as walker:
        raw = walker.read_relative_file(relative)
        walker.verify()
    path = source / relative
    if raw is None:
        raise SetupError("module-help asset is missing",
                         code="HELP_ASSET_INVALID", subject=str(path))
    return _parse_help_bytes(raw, str(path), asset=True)


def render_help(
    path: Path, source: Path | None, *, remove: bool = False,
    existing: bytes | None | object = _HOST_BYTES_UNSET,
) -> bytes:
    raw = _read_bounded_path_bytes(
        path, label="host help CSV", code="HOST_HELP_INVALID",
    ) if existing is _HOST_BYTES_UNSET else existing
    if raw:
        header_tuple, parsed_rows = _parse_help_bytes(
            raw, str(path), asset=False,
        )
        header = list(header_tuple)
        rows = parsed_rows
    else:
        header, rows = list(CSV_HEADER), []
    rows = [row for row in rows if len(row) > 1 and row[0] != MODULE_NAME
            and not row[1].startswith("sdm-")]
    if not remove:
        if source is None:
            raise SetupError("source required to install help rows",
                             code="SOURCE_REQUIRED")
        _, module_rows = read_help_asset(source)
        rows.extend(module_rows)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def fsync_path(path: Path) -> None:
    flags = os.O_RDONLY | (os.O_DIRECTORY if path.is_dir() and
                           hasattr(os, "O_DIRECTORY") else 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_tree(root: Path) -> None:
    with _RetainedWalker(root) as walker:
        def sync_directory(descriptor: int, prefix: str) -> None:
            subject = str(walker.root_path / prefix) \
                if prefix else str(walker.root_path)
            for name in walker.scan(descriptor, subject):
                relative = f"{prefix}/{name}" if prefix else name
                child_subject = str(walker.root_path / relative)
                metadata = walker.entry_stat(
                    descriptor, name, child_subject,
                )
                if stat.S_ISDIR(metadata.st_mode):
                    child = walker.open_dir(
                        descriptor, name, child_subject, metadata,
                    )
                    try:
                        sync_directory(child, relative)
                        os.fsync(child)
                    finally:
                        walker._release_fd(child)
                elif stat.S_ISREG(metadata.st_mode):
                    file_fd = walker._open(
                        name, _FILE_OPEN_FLAGS,
                        dir_fd=descriptor, subject=child_subject,
                    )
                    try:
                        opened = os.fstat(file_fd)
                        if (
                            _stat_fingerprint(opened)
                            != _stat_fingerprint(metadata)
                            or _mount_identity(file_fd) != walker.root_mount
                        ):
                            raise SetupError(
                                "staged file changed while syncing",
                                code="TREE_CONFLICT", exit_code=4,
                                subject=child_subject,
                            )
                        os.fsync(file_fd)
                    finally:
                        walker._release_fd(file_fd)
                else:
                    raise SetupError(
                        f"special staged node: {child_subject}",
                        code="SPECIAL_NODE", subject=child_subject,
                    )
            os.fsync(descriptor)

        sync_directory(walker.root_fd, "")
        walker.verify()


def write_bytes_sync(path: Path, content: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        view = memoryview(content)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError("short write while staging setup content")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    fsync_path(path.parent)


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        write_bytes_sync(temp, canonical_json(value) + b"\n", 0o600)
        os.replace(temp, path)
        os.chmod(path, 0o600)
        fsync_path(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError, OSError):
            temp.unlink()


def _write_bytes_sync_at(
    parent_fd: int, name: str, content: bytes, mode: int = 0o644,
) -> None:
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(name, flags, mode, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.EMFILE, errno.ENFILE):
            raise SetupError(
                "setup traversal exhausted file descriptors",
                code="TREE_RESOURCE_LIMIT", subject=name,
            ) from exc
        raise
    complete = False
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise SetupError(
                f"unsafe setup staging file: {name}", code="UNSAFE_NODE",
                subject=name,
            )
        os.fchmod(descriptor, mode)
        view = memoryview(content)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError("short write while staging setup content")
            written += count
        os.fsync(descriptor)
        os.fsync(parent_fd)
        complete = True
    finally:
        if not complete:
            try:
                current = os.fstat(descriptor)
                named = os.stat(
                    name, dir_fd=parent_fd, follow_symlinks=False,
                )
                if _anchor_fingerprint(named) == _anchor_fingerprint(current):
                    os.unlink(name, dir_fd=parent_fd)
                    with contextlib.suppress(OSError):
                        os.fsync(parent_fd)
            except (FileNotFoundError, OSError):
                pass
        os.close(descriptor)


def _write_json_atomic_at(
    host: _SetupHost, name: str, value: dict[str, Any], *,
    exclusive: bool = False,
) -> None:
    _require_host_anchors(host)
    encoded = canonical_json(value) + b"\n"
    temp_name = f"{name}.tmp-{uuid.uuid4().hex}"
    temp_fd = -1
    try:
        _write_bytes_sync_at(
            host.bmad_fd, temp_name, encoded, 0o600,
        )
        temp_fd = os.open(
            temp_name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0),
            dir_fd=host.bmad_fd,
        )
        temp_info = os.fstat(temp_fd)
        temp_named = os.stat(
            temp_name, dir_fd=host.bmad_fd, follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(temp_info.st_mode)
            or temp_info.st_nlink != 1
            or _stat_fingerprint(temp_info) != _stat_fingerprint(temp_named)
        ):
            raise SetupError(
                "setup journal temporary changed identity",
                code="TREE_CONFLICT", exit_code=4, subject=temp_name,
            )
        if exclusive:
            try:
                os.link(
                    temp_name, name, src_dir_fd=host.bmad_fd,
                    dst_dir_fd=host.bmad_fd, follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise SetupError(
                    "unfinished setup journal requires recover",
                    code="RECOVERY_REQUIRED", exit_code=3, subject=name,
                ) from exc
            installed = os.stat(
                name, dir_fd=host.bmad_fd, follow_symlinks=False,
            )
            if (installed.st_dev, installed.st_ino) != \
                    (temp_info.st_dev, temp_info.st_ino):
                raise SetupError(
                    "setup journal no-replace install changed identity",
                    code="TREE_CONFLICT", exit_code=4, subject=name,
                )
            os.unlink(temp_name, dir_fd=host.bmad_fd)
        else:
            current_temp = os.stat(
                temp_name, dir_fd=host.bmad_fd, follow_symlinks=False,
            )
            if _stat_fingerprint(current_temp) != _stat_fingerprint(temp_info):
                raise SetupError(
                    "setup journal temporary changed before install",
                    code="TREE_CONFLICT", exit_code=4, subject=temp_name,
                )
            os.replace(
                temp_name, name,
                src_dir_fd=host.bmad_fd, dst_dir_fd=host.bmad_fd,
            )
        try:
            descriptor = os.open(name, _FILE_OPEN_FLAGS, dir_fd=host.bmad_fd)
        except FileNotFoundError:
            # DrvFS can defer reopening the published destination until the
            # renamed source handle closes. Keep ordinary Linux pinning; on
            # this one failure only, release the moved handle and rebind once.
            # The saved identity and exact encoded bytes are checked below.
            os.close(temp_fd)
            temp_fd = -1
            descriptor = os.open(name, _FILE_OPEN_FLAGS, dir_fd=host.bmad_fd)
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or (info.st_dev, info.st_ino) !=
                (temp_info.st_dev, temp_info.st_ino)
            ):
                raise SetupError(
                    "setup journal installation produced an unsafe node",
                    code="JOURNAL_INVALID", exit_code=3, subject=name,
                )
            observed = bytearray()
            while len(observed) < len(encoded):
                chunk = os.read(
                    descriptor,
                    min(1024 * 1024, len(encoded) - len(observed)),
                )
                if not chunk:
                    break
                observed.extend(chunk)
            if (
                bytes(observed) != encoded
                or os.read(descriptor, 1)
                or _stat_fingerprint(info) !=
                _stat_fingerprint(os.fstat(descriptor))
            ):
                raise SetupError(
                    "setup journal changed during installation",
                    code="TREE_CONFLICT", exit_code=4, subject=name,
                )
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(host.bmad_fd)
        _require_host_anchors(host)
    finally:
        if temp_fd >= 0:
            with contextlib.suppress(OSError):
                os.close(temp_fd)
        with contextlib.suppress(FileNotFoundError, OSError):
            os.unlink(temp_name, dir_fd=host.bmad_fd)


def _require_host_anchors(host: _SetupHost) -> None:
    """Fail if the named host no longer denotes the retained descriptors."""
    named_root_fd = named_bmad_fd = -1
    try:
        named_root = host.project_root.lstat()
        named_root_fd = os.open(host.project_root, _DIR_OPEN_FLAGS)
        if (
            stat.S_ISLNK(named_root.st_mode)
            or not stat.S_ISDIR(named_root.st_mode)
            or named_root.st_dev != host.project_identity["device"]
            or named_root.st_ino != host.project_identity["inode"]
            or _project_identity_from_fd(named_root_fd) != host.project_identity
            or _project_identity_from_fd(host.project_fd) != host.project_identity
        ):
            raise SetupError(
                "setup project root identity changed",
                code="TREE_CONFLICT", exit_code=4,
                subject=str(host.project_root),
            )
        named_bmad = os.stat(
            "_bmad", dir_fd=host.project_fd, follow_symlinks=False,
        )
        named_bmad_fd = os.open(
            "_bmad", _DIR_OPEN_FLAGS, dir_fd=host.project_fd,
        )
        opened_bmad = os.fstat(host.bmad_fd)
        reopened_bmad = os.fstat(named_bmad_fd)
        if (
            stat.S_ISLNK(named_bmad.st_mode)
            or not stat.S_ISDIR(named_bmad.st_mode)
            or _anchor_fingerprint(named_bmad) != host.bmad_fingerprint
            or _anchor_fingerprint(opened_bmad) != host.bmad_fingerprint
            or _anchor_fingerprint(reopened_bmad) != host.bmad_fingerprint
            or _mount_identity(host.bmad_fd) != host.bmad_mount
            or _mount_identity(named_bmad_fd) != host.bmad_mount
        ):
            raise SetupError(
                "setup _bmad anchor identity changed",
                code="TREE_CONFLICT", exit_code=4,
                subject=str(host.project_root / "_bmad"),
            )
    except SetupError:
        raise
    except (FileNotFoundError, OSError) as exc:
        raise SetupError(
            "setup host anchor became unavailable",
            code="TREE_CONFLICT", exit_code=4,
            subject=str(host.project_root),
        ) from exc
    finally:
        if named_bmad_fd >= 0:
            with contextlib.suppress(OSError):
                os.close(named_bmad_fd)
        if named_root_fd >= 0:
            with contextlib.suppress(OSError):
                os.close(named_root_fd)


@contextlib.contextmanager
def host_lock(
    project_root: Path, timeout: float = 10.0,
) -> Iterator[_SetupHost]:
    project_root = Path(os.path.abspath(project_root))
    lock_path = project_root / "_bmad" / ".sdm-setup.lock"
    project_fd = os.open(project_root, _DIR_OPEN_FLAGS)
    bmad_fd = descriptor = -1
    deadline = time.monotonic() + timeout
    try:
        project_identity = _project_identity_from_fd(project_fd)
        named_root = project_root.lstat()
        if (
            stat.S_ISLNK(named_root.st_mode)
            or not stat.S_ISDIR(named_root.st_mode)
            or (named_root.st_dev, named_root.st_ino) !=
            (project_identity["device"], project_identity["inode"])
        ):
            raise SetupError(
                "setup project root changed while opening the lock",
                code="TREE_CONFLICT", exit_code=4, subject=str(project_root),
            )
        try:
            os.mkdir("_bmad", 0o700, dir_fd=project_fd)
            os.fsync(project_fd)
        except FileExistsError:
            pass
        bmad_fd = os.open("_bmad", _DIR_OPEN_FLAGS, dir_fd=project_fd)
        bmad_info = os.fstat(bmad_fd)
        bmad_mount = _mount_identity(bmad_fd)
        if (
            not stat.S_ISDIR(bmad_info.st_mode)
            or bmad_info.st_dev != project_identity["device"]
            or bmad_mount != project_identity["mount_id"]
        ):
            raise SetupError(
                "setup _bmad directory crosses the project mount",
                code="MOUNT_ESCAPE", subject=str(project_root / "_bmad"),
            )
        descriptor = os.open(
            ".sdm-setup.lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0),
            0o600, dir_fd=bmad_fd,
        )
        lock_info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lock_info.st_mode)
            or lock_info.st_nlink != 1
            or lock_info.st_dev != project_identity["device"]
            or _mount_identity(descriptor) != project_identity["mount_id"]
        ):
            raise SetupError(
                "setup lock is not a private regular file",
                code="UNSAFE_NODE", subject=str(lock_path),
            )
        os.fchmod(descriptor, 0o600)
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SetupError("setup lock is held by another process",
                                     code="SETUP_LOCK_CONFLICT", exit_code=4,
                                     subject=str(lock_path))
                time.sleep(0.05)
        host = _SetupHost(
            project_root=project_root,
            project_fd=project_fd,
            bmad_fd=bmad_fd,
            project_identity=project_identity,
            bmad_fingerprint=_anchor_fingerprint(bmad_info),
            bmad_mount=bmad_mount,
        )
        _require_host_anchors(host)
        yield host
    finally:
        if descriptor >= 0:
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(descriptor)
        if bmad_fd >= 0:
            with contextlib.suppress(OSError):
                os.close(bmad_fd)
        with contextlib.suppress(OSError):
            os.close(project_fd)


def target_relatives() -> list[str]:
    return [
        *(f".agents/skills/{name}" for name in OWNED_SKILLS),
        "_bmad/custom/config.user.toml",
        "_bmad/_config/bmad-help.csv",
        "_bmad/module-help.csv",
        "_bmad/custom/bmad-sprint-planning.toml",
        "_bmad/custom/config.toml",
    ]


def target_paths(project_root: Path) -> list[Path]:
    return [project_root.joinpath(*_portable_parts(relative))
            for relative in target_relatives()]


def _journal_invalid(journal_path: Path, message: str) -> SetupError:
    return SetupError(
        f"recovery journal is invalid: {message}", code="JOURNAL_INVALID",
        exit_code=3, subject=str(journal_path),
    )


def validate_setup_journal(
    project_root: Path, journal_path: Path, value: Any, *,
    project_fd: int | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _JOURNAL_KEYS:
        raise _journal_invalid(journal_path, "outer schema is not closed")
    if value.get("schema") != "sdm-setup-journal/v1":
        raise _journal_invalid(journal_path, "schema identifier differs")
    transaction_id = value.get("transaction_id")
    command = value.get("command")
    if not isinstance(transaction_id, str) or not _TRANSACTION_ID.fullmatch(transaction_id):
        raise _journal_invalid(journal_path, "transaction ID is malformed")
    if command not in {"install", "update", "uninstall"}:
        raise _journal_invalid(journal_path, "command is invalid")
    project_identity = value.get("project_identity")
    if (
        not isinstance(project_identity, dict)
        or set(project_identity) != _PROJECT_IDENTITY_KEYS
        or any(
            isinstance(project_identity.get(field), bool)
            or not isinstance(project_identity.get(field), int)
            or project_identity[field] < 0
            for field in ("device", "inode")
        )
        or not isinstance(project_identity.get("mount_id"), str)
        or re.fullmatch(r"mnt_id:[0-9]+", project_identity["mount_id"]) is None
    ):
        raise _journal_invalid(journal_path, "project identity is malformed")
    expected_stage = f".sdm-setup-stage-{transaction_id}"
    expected_backup = f".sdm-setup-backup-{transaction_id}"
    if value.get("stage") != expected_stage or value.get("backup_root") != expected_backup:
        raise _journal_invalid(journal_path, "transaction roots are not derived from the transaction ID")
    source_digest = value.get("source_digest")
    if command == "uninstall":
        if source_digest is not None:
            raise _journal_invalid(journal_path, "uninstall source digest must be null")
    elif not isinstance(source_digest, str) or not DIGEST_RE.fullmatch(source_digest):
        raise _journal_invalid(journal_path, "source digest is malformed")
    if not isinstance(value.get("marker_applied"), bool):
        raise _journal_invalid(journal_path, "marker_applied must be boolean")
    if value.get("phase") not in {"planned", "applying", "committed"}:
        raise _journal_invalid(journal_path, "phase is invalid")
    operations = value.get("operations")
    expected_targets = target_relatives()
    if not isinstance(operations, list) or len(operations) != len(expected_targets):
        raise _journal_invalid(journal_path, "operation roster length differs")
    applied_prefix = True
    any_started = False
    all_applied = True
    for index, (operation, target) in enumerate(zip(operations, expected_targets, strict=True)):
        if not isinstance(operation, dict) or set(operation) != _OPERATION_KEYS:
            raise _journal_invalid(journal_path, f"operation {index} schema is not closed")
        kind = "dir" if index < len(OWNED_SKILLS) else "file"
        expected_desired = (
            None
            if (index < len(OWNED_SKILLS) and
                (command == "uninstall" or index >= len(SKILLS)))
            else f"{expected_stage}/op-{index:03d}"
        )
        if (
            operation.get("target") != target
            or operation.get("backup") != f"{expected_backup}/op-{index:03d}"
            or operation.get("desired") != expected_desired
            or operation.get("kind") != kind
        ):
            raise _journal_invalid(journal_path, f"operation {index} paths or kind differ from the fixed roster")
        started = operation.get("started")
        applied = operation.get("applied")
        if not isinstance(started, bool) or not isinstance(applied, bool) or (applied and not started):
            raise _journal_invalid(journal_path, f"operation {index} phase flags are invalid")
        if started and not applied_prefix:
            raise _journal_invalid(journal_path, f"operation {index} violates prefix ordering")
        if not applied:
            applied_prefix = False
            all_applied = False
        any_started = any_started or started
        desired_digest = operation.get("desired_digest")
        if expected_desired is None:
            if desired_digest is not None:
                raise _journal_invalid(journal_path, f"operation {index} has an unexpected staged digest")
        elif not isinstance(desired_digest, str) or not DIGEST_RE.fullmatch(desired_digest):
            raise _journal_invalid(journal_path, f"operation {index} staged digest is malformed")
        original = operation.get("original")
        if original is None:
            if operation.get("backup_digest") is not None:
                raise _journal_invalid(journal_path, f"operation {index} has an unexpected backup digest")
        else:
            if not isinstance(original, dict) or set(original) != _ORIGINAL_KEYS:
                raise _journal_invalid(journal_path, f"operation {index} original metadata is malformed")
            if original.get("kind") != kind or not isinstance(original.get("digest"), str) or not DIGEST_RE.fullmatch(original["digest"]):
                raise _journal_invalid(journal_path, f"operation {index} original identity is malformed")
            mode = original.get("mode")
            if (
                isinstance(mode, bool)
                or not isinstance(mode, int)
                or not 0 <= mode <= 0o7777
            ):
                raise _journal_invalid(
                    journal_path,
                    f"operation {index} original mode is malformed",
                )
            for field in ("uid", "gid"):
                item = original.get(field)
                if (
                    isinstance(item, bool)
                    or not isinstance(item, int)
                    or not 0 <= item <= (2**32 - 2)
                ):
                    raise _journal_invalid(
                        journal_path,
                        f"operation {index} original {field} is malformed",
                    )
            if operation.get("backup_digest") != original["digest"]:
                raise _journal_invalid(journal_path, f"operation {index} backup digest is inconsistent")
    marker = value["marker_applied"]
    phase = value["phase"]
    if marker != all_applied:
        raise _journal_invalid(journal_path, "marker_applied must equal complete operation application")
    if phase == "planned" and (any_started or marker):
        raise _journal_invalid(journal_path, "planned phase contains applied work")
    if phase == "applying" and (not any_started or marker):
        raise _journal_invalid(journal_path, "applying phase flags are inconsistent")
    if phase == "committed" and not marker:
        raise _journal_invalid(journal_path, "committed phase is incomplete")
    try:
        observed_identity = (
            _project_identity_from_fd(project_fd)
            if project_fd is not None else _project_identity(project_root)
        )
    except (OSError, SetupError) as exc:
        raise _journal_invalid(
            journal_path, "project identity cannot be re-established",
        ) from exc
    if observed_identity != project_identity:
        raise _journal_invalid(
            journal_path, "journal project identity differs from the opened host",
        )
    return value


def metadata(path: Path) -> dict[str, Any] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode):
        raise SetupError(
            f"symlink rejected in setup target: {path}",
            code="SYMLINK_REJECTED", subject=str(path),
        )
    if stat.S_ISREG(info.st_mode):
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if _stat_fingerprint(info) != _stat_fingerprint(opened) or opened.st_nlink != 1:
                raise SetupError(
                    f"setup target changed while reading: {path}",
                    code="TREE_CONFLICT", exit_code=4, subject=str(path),
                )
            if opened.st_size > MAX_TREE_FILE_BYTES:
                raise SetupError(
                    f"setup target exceeds fixed byte limit: {path}",
                    code="TREE_RESOURCE_LIMIT", subject=str(path),
                )
            hasher = hashlib.sha256()
            while chunk := os.read(descriptor, 1024 * 1024):
                hasher.update(chunk)
            if _stat_fingerprint(opened) != _stat_fingerprint(os.fstat(descriptor)):
                raise SetupError(
                    f"setup target changed while reading: {path}",
                    code="TREE_CONFLICT", exit_code=4, subject=str(path),
                )
            content_digest = "sha256:" + hasher.hexdigest()
        finally:
            os.close(descriptor)
        kind = "file"
    elif stat.S_ISDIR(info.st_mode):
        content_digest = tree_digest(path)
        kind = "dir"
    else:
        raise SetupError(
            f"special setup target: {path}", code="SPECIAL_NODE",
            subject=str(path),
        )
    return {
        "digest": content_digest,
        "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid,
        "gid": info.st_gid, "kind": kind,
    }


def _read_host_regular_at(
    host: _SetupHost, relative: str,
) -> tuple[bytes, dict[str, Any]] | None:
    """Capture one optional host file through the retained project root."""
    _require_host_anchors(host)
    parts = _portable_parts(relative)
    root_info = os.fstat(host.project_fd)
    root_mount = host.project_identity["mount_id"]

    def capture() -> tuple[bytes, dict[str, Any]] | None:
        parent_fd = host.project_fd
        opened_parents: list[int] = []
        try:
            traversed: list[str] = []
            for component in parts[:-1]:
                traversed.append(component)
                subject = "/".join(traversed)
                try:
                    child_fd = os.open(
                        component, _DIR_OPEN_FLAGS, dir_fd=parent_fd,
                    )
                except FileNotFoundError:
                    return None
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise SetupError(
                            f"unsafe setup host ancestor: {subject}",
                            code="TREE_CONFLICT", exit_code=4,
                            subject=subject,
                        ) from exc
                    raise
                opened_parents.append(child_fd)
                child_info = os.fstat(child_fd)
                if (
                    not stat.S_ISDIR(child_info.st_mode)
                    or child_info.st_dev != root_info.st_dev
                    or _mount_identity(child_fd) != root_mount
                ):
                    raise SetupError(
                        f"setup host ancestor crosses a mount: {subject}",
                        code="MOUNT_ESCAPE", subject=subject,
                    )
                parent_fd = child_fd
            try:
                before = os.stat(
                    parts[-1], dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return None
            if before.st_size > MAX_TREE_FILE_BYTES:
                raise SetupError(
                    f"oversized setup host file: {relative}",
                    code="TREE_RESOURCE_LIMIT", subject=relative,
                )
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise SetupError(
                    f"unsafe setup host file: {relative}",
                    code="HOST_DOCUMENT_INVALID", subject=relative,
                )
            try:
                descriptor = os.open(
                    parts[-1], _FILE_OPEN_FLAGS, dir_fd=parent_fd,
                )
            except OSError as exc:
                if exc.errno in (
                    errno.ELOOP, errno.ENOENT, errno.ENOTDIR, errno.ENXIO,
                ):
                    raise SetupError(
                        f"setup host file changed before opening: {relative}",
                        code="TREE_CONFLICT", exit_code=4,
                        subject=relative,
                    ) from exc
                raise
            try:
                opened = os.fstat(descriptor)
                _validate_opened_recovery_entry(
                    before, opened, descriptor,
                    root_dev=root_info.st_dev, root_mount=root_mount,
                    subject=relative, kind="file",
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
                if (
                    len(data) != opened.st_size
                    or os.read(descriptor, 1)
                    or _stat_fingerprint(opened) !=
                    _stat_fingerprint(os.fstat(descriptor))
                ):
                    raise SetupError(
                        f"setup host file changed while reading: {relative}",
                        code="TREE_CONFLICT", exit_code=4,
                        subject=relative,
                    )
                _require_stable_named_entry(
                    parent_fd, parts[-1], opened, relative,
                )
                raw = bytes(data)
                return raw, {
                    "digest": digest_bytes(raw),
                    "mode": stat.S_IMODE(opened.st_mode),
                    "uid": opened.st_uid, "gid": opened.st_gid,
                    "kind": "file",
                }
            finally:
                os.close(descriptor)
        finally:
            for descriptor in reversed(opened_parents):
                os.close(descriptor)

    result = capture()
    _require_host_anchors(host)
    return result


def prepare_plan(project_root: Path, command: str, source: Path | None,
                 answers: dict[str, str] | None,
                 source_digest: str | None, *,
                 host: _SetupHost | None = None) -> tuple[dict[str, Any], dict[int, bytes]]:
    transaction_id = uuid.uuid4().hex
    stage = f".sdm-setup-stage-{transaction_id}"
    backup = f".sdm-setup-backup-{transaction_id}"
    targets = target_paths(project_root)
    target_names = target_relatives()
    files_start = len(OWNED_SKILLS)
    operations: list[dict[str, Any]] = []
    desired_files: dict[int, bytes] = {}
    host_files: dict[int, tuple[bytes, dict[str, Any]] | None] = {}
    if host is not None:
        for index in range(len(OWNED_SKILLS), len(target_names)):
            host_files[index] = _read_host_regular_at(
                host, target_names[index],
            )

    def existing(index: int) -> bytes | None | object:
        if host is None:
            return _HOST_BYTES_UNSET
        captured = host_files[index]
        return None if captured is None else captured[0]

    uninstall = command == "uninstall"
    if not uninstall:
        assert source is not None and answers is not None and source_digest is not None
        staged_hasher = hashlib.sha256()
        staged_hasher.update(b"sdm-module-v1\0")
        for index, name in enumerate(SKILLS):
            desired = f"{stage}/op-{index:03d}"
            desired_digest = tree_digest(source / name)
            staged_hasher.update(name.encode() + b"\0" +
                                 desired_digest.encode() + b"\0")
            operations.append({
                "target": target_names[index],
                "desired": desired, "kind": "dir",
                "desired_digest": desired_digest,
            })
        staged_digest = "sha256:" + staged_hasher.hexdigest()
        if staged_digest != source_digest:
            raise SetupError("source changed while staging module Skills",
                             code="TREE_CONFLICT", exit_code=4,
                             subject=str(source))
        rendered = (
            render_config(targets[files_start], answers, source_digest, source_digest,
                          personal=True, existing=existing(files_start)),
            render_help(
                targets[files_start + 1], source, existing=existing(files_start + 1),
            ),
            render_help(
                targets[files_start + 2], source, existing=existing(files_start + 2),
            ),
            render_hook(targets[files_start + 3], existing=existing(files_start + 3)),
            render_config(targets[files_start + 4], answers, source_digest, source_digest,
                          personal=False, existing=existing(files_start + 4)),
        )
    else:
        rendered = (
            render_config_without_sdm(
                targets[files_start], existing=existing(files_start),
            ),
            render_help(
                targets[files_start + 1], None, remove=True, existing=existing(files_start + 1),
            ),
            render_help(
                targets[files_start + 2], None, remove=True, existing=existing(files_start + 2),
            ),
            render_hook(
                targets[files_start + 3], remove=True, existing=existing(files_start + 3),
            ),
            render_config_without_sdm(
                targets[files_start + 4], existing=existing(files_start + 4),
            ),
        )
        for name in SKILLS:
            index = len(operations)
            operations.append({
                "target": target_names[index],
                "desired": None, "kind": "dir", "desired_digest": None,
            })
    for name in RETIRED_SKILLS:
        index = len(operations)
        operations.append({
            "target": target_names[index],
            "desired": None, "kind": "dir", "desired_digest": None,
        })
    for offset, (target, content) in enumerate(
        zip(targets[len(OWNED_SKILLS):], rendered), len(OWNED_SKILLS),
    ):
        desired = f"{stage}/op-{offset:03d}"
        desired_files[offset] = content
        operations.append({"target": target_names[offset], "desired": desired,
                           "kind": "file",
                           "desired_digest": digest_bytes(content)})
    for index, operation in enumerate(operations):
        target = project_root.joinpath(*_portable_parts(operation["target"]))
        ensure_contained(project_root, target)
        operation["backup"] = f"{backup}/op-{index:03d}"
        if host is None:
            operation["original"] = metadata(target)
        elif index in host_files:
            captured = host_files[index]
            operation["original"] = (
                None if captured is None else dict(captured[1])
            )
        else:
            parts = _portable_parts(operation["target"])
            parent_fd, opened = _open_relative_directory(
                host.project_fd, "/".join(parts[:-1]),
            )
            try:
                root_info = os.fstat(host.project_fd)
                operation["original"] = _entry_metadata_at(
                    parent_fd, parts[-1], root_dev=root_info.st_dev,
                    root_mount=host.project_identity["mount_id"],
                )
                if operation["original"] is not None:
                    operation["original"] = {
                        field: operation["original"][field]
                        for field in _ORIGINAL_KEYS
                    }
            finally:
                for descriptor in reversed(opened):
                    os.close(descriptor)
        operation["backup_digest"] = (
            operation["original"]["digest"]
            if operation["original"] is not None else None
        )
        operation["started"] = False
        operation["applied"] = False
    journal = {
        "schema": "sdm-setup-journal/v1", "transaction_id": transaction_id,
        "command": command,
        "project_identity": (
            dict(host.project_identity) if host is not None
            else _project_identity(project_root)
        ),
        "stage": stage, "backup_root": backup,
        "source_digest": source_digest, "operations": operations,
        "marker_applied": False, "phase": "planned",
    }
    return journal, desired_files


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    shutil.rmtree(path) if path.is_dir() else path.unlink()


def _portable_parts(relative: str) -> tuple[str, ...]:
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise SetupError(
            f"unsafe setup-relative path: {relative}", code="PATH_ESCAPE",
            subject=relative,
        )
    return path.parts


def _open_relative_directory(
    root_fd: int, relative: str, *, create: bool = False,
) -> tuple[int, list[int]]:
    root_info = os.fstat(root_fd)
    root_mount = _mount_identity(root_fd)
    descriptor = root_fd
    opened: list[int] = []
    traversed: list[str] = []
    try:
        for component in _portable_parts(relative):
            traversed.append(component)
            subject = "/".join(traversed)
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
            try:
                child = os.open(component, _DIR_OPEN_FLAGS, dir_fd=descriptor)
            except OSError as exc:
                code = "TREE_CONFLICT" if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.ENOENT) else "UNSAFE_NODE"
                raise SetupError(
                    f"setup ancestor is unavailable or unsafe: {subject}",
                    code=code, exit_code=4 if code == "TREE_CONFLICT" else 2,
                    subject=subject,
                ) from exc
            opened.append(child)
            info = os.fstat(child)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_dev != root_info.st_dev
                or _mount_identity(child) != root_mount
            ):
                raise SetupError(
                    f"setup ancestor crosses a mount or changes type: {subject}",
                    code="MOUNT_ESCAPE", subject=subject,
                )
            descriptor = child
        return descriptor, opened
    except Exception:
        for child in reversed(opened):
            with contextlib.suppress(OSError):
                os.close(child)
        raise


class _RecoveryTreeBudget:
    def __init__(self, subject: str) -> None:
        self.subject = subject
        self.nodes = 0
        self.total_bytes = 0
        self.active_fds = 0
        self.fd_limit = 0
        try:
            soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
            baseline = len(os.listdir("/proc/self/fd"))
            available = (
                MAX_RETAINED_FDS if soft_limit == resource.RLIM_INFINITY
                else int(soft_limit) - baseline - 4
            )
            self.fd_limit = min(MAX_RETAINED_FDS, max(0, available))
        except (OSError, ValueError):
            self.fd_limit = MAX_RETAINED_FDS
        if self.fd_limit < 1:
            raise SetupError(
                "insufficient descriptor budget for setup recovery",
                code="TREE_RESOURCE_LIMIT", subject=self.subject,
            )

    def account(self, info: os.stat_result, subject: str) -> None:
        self.nodes += 1
        if self.nodes > MAX_TREE_NODES:
            raise SetupError(
                "setup recovery tree exceeds the fixed node limit",
                code="TREE_RESOURCE_LIMIT", subject=subject,
            )
        if stat.S_ISREG(info.st_mode):
            if info.st_size > MAX_TREE_FILE_BYTES:
                raise SetupError(
                    "setup recovery file exceeds the fixed byte limit",
                    code="TREE_RESOURCE_LIMIT", subject=subject,
                )
            self.total_bytes += info.st_size
            if self.total_bytes > MAX_TREE_BYTES:
                raise SetupError(
                    "setup recovery tree exceeds the aggregate byte limit",
                    code="TREE_RESOURCE_LIMIT", subject=subject,
                )

    def open(self, parent_fd: int, name: str, flags: int, subject: str) -> int:
        if self.active_fds >= self.fd_limit:
            raise SetupError(
                "setup recovery exceeded its retained descriptor budget",
                code="TREE_RESOURCE_LIMIT", subject=subject,
            )
        try:
            descriptor = os.open(name, flags, dir_fd=parent_fd)
        except OSError as exc:
            if exc.errno in (errno.EMFILE, errno.ENFILE):
                raise SetupError(
                    "setup recovery exhausted file descriptors",
                    code="TREE_RESOURCE_LIMIT", subject=subject,
                ) from exc
            if exc.errno in (errno.ELOOP, errno.ENOENT, errno.ENOTDIR, errno.ENXIO):
                raise SetupError(
                    f"setup recovery entry changed while opening: {subject}",
                    code="TREE_CONFLICT", exit_code=4, subject=subject,
                ) from exc
            raise
        self.active_fds += 1
        return descriptor

    def close(self, descriptor: int) -> None:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        self.active_fds -= 1


def _require_stable_named_entry(
    parent_fd: int, name: str, expected: os.stat_result, subject: str,
) -> None:
    try:
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise SetupError(
            f"setup entry disappeared: {subject}", code="TREE_CONFLICT",
            exit_code=4, subject=subject,
        ) from exc
    if _stat_fingerprint(named) != _stat_fingerprint(expected):
        raise SetupError(
            f"setup entry identity changed: {subject}", code="TREE_CONFLICT",
            exit_code=4, subject=subject,
        )


def _validate_opened_recovery_entry(
    before: os.stat_result, opened: os.stat_result, descriptor: int, *,
    root_dev: int, root_mount: str, subject: str, kind: str,
) -> None:
    valid_kind = (
        stat.S_ISDIR(opened.st_mode) if kind == "dir"
        else stat.S_ISREG(opened.st_mode)
    )
    if (
        not valid_kind
        or opened.st_dev != root_dev
        or _mount_identity(descriptor) != root_mount
        or _stat_fingerprint(before) != _stat_fingerprint(opened)
        or (kind == "file" and opened.st_nlink != 1)
    ):
        raise SetupError(
            f"setup {kind} identity changed: {subject}",
            code="TREE_CONFLICT", exit_code=4, subject=subject,
        )


def _bounded_directory_names(
    descriptor: int, *, limit: int, subject: str,
) -> tuple[str, ...]:
    names: list[str] = []
    with os.scandir(descriptor) as entries:
        for entry in entries:
            if len(names) >= max(0, limit):
                raise SetupError(
                    "setup recovery directory exceeds the node budget",
                    code="TREE_RESOURCE_LIMIT", subject=subject,
                )
            names.append(entry.name)
    return tuple(sorted(names))


def _entry_metadata_at(
    parent_fd: int, name: str, *, root_dev: int, root_mount: str,
) -> dict[str, Any] | None:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    subject = name
    if stat.S_ISLNK(info.st_mode):
        raise SetupError(
            f"setup entry was replaced by a symlink: {subject}",
            code="TREE_CONFLICT", exit_code=4, subject=subject,
        )
    budget = _RecoveryTreeBudget(subject)
    budget.account(info, subject)
    if stat.S_ISREG(info.st_mode):
        descriptor = budget.open(parent_fd, name, _FILE_OPEN_FLAGS, subject)
        try:
            opened = os.fstat(descriptor)
            _validate_opened_recovery_entry(
                info, opened, descriptor, root_dev=root_dev,
                root_mount=root_mount, subject=subject, kind="file",
            )
            hasher = hashlib.sha256()
            remaining = opened.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                hasher.update(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            if (
                remaining
                or os.read(descriptor, 1)
                or _stat_fingerprint(opened) != _stat_fingerprint(after)
            ):
                raise SetupError(
                    f"setup file changed while hashing: {subject}",
                    code="TREE_CONFLICT", exit_code=4, subject=subject,
                )
            _require_stable_named_entry(parent_fd, name, opened, subject)
            content_digest = "sha256:" + hasher.hexdigest()
        finally:
            budget.close(descriptor)
        kind = "file"
        final_info = opened
        entry_fingerprints = {".": _stat_fingerprint(final_info)}
    elif stat.S_ISDIR(info.st_mode):
        directory_fd = budget.open(parent_fd, name, _DIR_OPEN_FLAGS, subject)
        try:
            opened = os.fstat(directory_fd)
            _validate_opened_recovery_entry(
                info, opened, directory_fd, root_dev=root_dev,
                root_mount=root_mount, subject=subject, kind="dir",
            )
            hasher = hashlib.sha256()
            hasher.update(b"sdm-tree-v1\0")
            entry_fingerprints = {".": _stat_fingerprint(opened)}

            def capture(current_fd: int, prefix: str) -> None:
                directory_before = os.fstat(current_fd)
                names = _bounded_directory_names(
                    current_fd,
                    limit=MAX_TREE_NODES - budget.nodes,
                    subject=prefix or subject,
                )
                if _stat_fingerprint(directory_before) != _stat_fingerprint(os.fstat(current_fd)):
                    raise SetupError(
                        f"setup directory changed while scanning: {prefix or subject}",
                        code="TREE_CONFLICT", exit_code=4,
                        subject=prefix or subject,
                    )
                for child_name in names:
                    relative = f"{prefix}/{child_name}" if prefix else child_name
                    try:
                        child = os.stat(
                            child_name, dir_fd=current_fd,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError as exc:
                        raise SetupError(
                            f"setup tree entry disappeared: {relative}",
                            code="TREE_CONFLICT", exit_code=4,
                            subject=relative,
                        ) from exc
                    if stat.S_ISLNK(child.st_mode):
                        raise SetupError(
                            f"setup recovery tree contains a symlink: {relative}",
                            code="TREE_CONFLICT", exit_code=4,
                            subject=relative,
                        )
                    budget.account(child, relative)
                    if stat.S_ISDIR(child.st_mode):
                        _hash_tree_record(hasher, relative + "/", child.st_mode, None)
                        child_fd = budget.open(
                            current_fd, child_name, _DIR_OPEN_FLAGS, relative,
                        )
                        try:
                            child_opened = os.fstat(child_fd)
                            _validate_opened_recovery_entry(
                                child, child_opened, child_fd,
                                root_dev=root_dev, root_mount=root_mount,
                                subject=relative, kind="dir",
                            )
                            entry_fingerprints[relative] = \
                                _stat_fingerprint(child_opened)
                            capture(child_fd, relative)
                            if _stat_fingerprint(child_opened) != _stat_fingerprint(os.fstat(child_fd)):
                                raise SetupError(
                                    f"setup directory changed while hashing: {relative}",
                                    code="TREE_CONFLICT", exit_code=4,
                                    subject=relative,
                                )
                            _require_stable_named_entry(
                                current_fd, child_name, child_opened, relative,
                            )
                        finally:
                            budget.close(child_fd)
                    elif stat.S_ISREG(child.st_mode):
                        if child.st_nlink != 1:
                            raise SetupError(
                                f"hardlinked setup recovery file: {relative}",
                                code="HARDLINK_REJECTED", subject=relative,
                            )
                        encoded = relative.encode("utf-8")
                        hasher.update(len(encoded).to_bytes(8, "big"))
                        hasher.update(encoded)
                        hasher.update((child.st_mode & 0o7777).to_bytes(4, "big"))
                        hasher.update(child.st_size.to_bytes(8, "big"))
                        file_fd = budget.open(
                            current_fd, child_name, _FILE_OPEN_FLAGS, relative,
                        )
                        try:
                            file_opened = os.fstat(file_fd)
                            _validate_opened_recovery_entry(
                                child, file_opened, file_fd,
                                root_dev=root_dev, root_mount=root_mount,
                                subject=relative, kind="file",
                            )
                            entry_fingerprints[relative] = \
                                _stat_fingerprint(file_opened)
                            remaining = file_opened.st_size
                            while remaining:
                                chunk = os.read(
                                    file_fd, min(1024 * 1024, remaining),
                                )
                                if not chunk:
                                    break
                                hasher.update(chunk)
                                remaining -= len(chunk)
                            if (
                                remaining
                                or os.read(file_fd, 1)
                                or _stat_fingerprint(file_opened) !=
                                _stat_fingerprint(os.fstat(file_fd))
                            ):
                                raise SetupError(
                                    f"setup file changed while hashing: {relative}",
                                    code="TREE_CONFLICT", exit_code=4,
                                    subject=relative,
                                )
                            _require_stable_named_entry(
                                current_fd, child_name, file_opened, relative,
                            )
                        finally:
                            budget.close(file_fd)
                    else:
                        raise SetupError(
                            f"unsafe setup recovery node: {relative}",
                            code="SPECIAL_NODE", subject=relative,
                        )
                if (
                    _bounded_directory_names(
                        current_fd, limit=len(names),
                        subject=prefix or subject,
                    ) != names
                    or _stat_fingerprint(directory_before) !=
                    _stat_fingerprint(os.fstat(current_fd))
                ):
                    raise SetupError(
                        f"setup directory changed while hashing: {prefix or subject}",
                        code="TREE_CONFLICT", exit_code=4,
                        subject=prefix or subject,
                    )

            capture(directory_fd, "")
            if _stat_fingerprint(opened) != _stat_fingerprint(os.fstat(directory_fd)):
                raise SetupError(
                    f"setup directory changed while hashing: {subject}",
                    code="TREE_CONFLICT", exit_code=4, subject=subject,
                )
            _require_stable_named_entry(parent_fd, name, opened, subject)
            content_digest = "sha256:" + hasher.hexdigest()
        finally:
            budget.close(directory_fd)
        kind = "dir"
        final_info = opened
    else:
        raise SetupError(
            f"special setup entry: {subject}", code="SPECIAL_NODE",
            subject=subject,
        )
    return {
        "digest": content_digest, "mode": stat.S_IMODE(final_info.st_mode),
        "uid": final_info.st_uid, "gid": final_info.st_gid, "kind": kind,
        "_fingerprint": _stat_fingerprint(final_info),
        "_entries": entry_fingerprints,
    }


def _open_bound_entry_at(
    parent_fd: int, name: str, captured: dict[str, Any], *,
    root_dev: int, root_mount: str, subject: str,
) -> int:
    flags = _DIR_OPEN_FLAGS if captured["kind"] == "dir" else _FILE_OPEN_FLAGS
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOENT, errno.ENOTDIR, errno.ENXIO):
            raise SetupError(
                f"setup entry changed before use: {subject}",
                code="TREE_CONFLICT", exit_code=4, subject=subject,
            ) from exc
        raise
    try:
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            tuple(captured["_fingerprint"]) != _stat_fingerprint(opened)
            or _stat_fingerprint(opened) != _stat_fingerprint(named)
            or opened.st_dev != root_dev
            or _mount_identity(descriptor) != root_mount
            or (captured["kind"] == "file" and opened.st_nlink != 1)
        ):
            raise SetupError(
                f"setup entry identity changed before use: {subject}",
                code="TREE_CONFLICT", exit_code=4, subject=subject,
            )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _reopen_published_entry_at(
    parent_fd: int, name: str, captured: dict[str, Any],
    published_identity: tuple[int, ...], *, root_dev: int,
    root_mount: str, subject: str,
) -> int:
    """Rebind a moved DrvFS handle only to its exact identity and content."""
    observed = _entry_metadata_at(
        parent_fd, name, root_dev=root_dev, root_mount=root_mount,
    )
    if observed is None or any(
        observed[field] != captured[field]
        for field in ("digest", "mode", "uid", "gid", "kind")
    ):
        raise SetupError(
            f"published setup content changed while rebinding: {subject}",
            code="TREE_CONFLICT", exit_code=4, subject=subject,
        )
    descriptor = _open_bound_entry_at(
        parent_fd, name, observed, root_dev=root_dev,
        root_mount=root_mount, subject=subject,
    )
    try:
        if _anchor_fingerprint(os.fstat(descriptor)) != published_identity:
            raise SetupError(
                f"published setup identity changed while rebinding: {subject}",
                code="TREE_CONFLICT", exit_code=4, subject=subject,
            )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _remove_entry_at(
    parent_fd: int, name: str, *, root_dev: int, root_mount: str,
    expected_digest: str | None = None,
    expected_kind: str | None = None,
    expected_tree: dict[str, tuple[int, ...]] | None = None,
) -> None:
    # Complete the bounded, identity-safe walk before the first unlink.  This
    # guarantees resource-limit failures leave the tree untouched.
    captured = _entry_metadata_at(
        parent_fd, name, root_dev=root_dev, root_mount=root_mount,
    )
    if captured is None:
        return
    if (
        expected_digest is not None
        and captured["digest"] != expected_digest
    ) or (
        expected_kind is not None
        and captured["kind"] != expected_kind
    ):
        raise SetupError(
            f"setup removal content changed before use: {name}",
            code="TREE_CONFLICT", exit_code=4, subject=name,
        )
    if (
        expected_tree is not None
        and captured["_entries"] != expected_tree
    ):
        raise SetupError(
            f"setup removal identity changed before use: {name}",
            code="TREE_CONFLICT", exit_code=4, subject=name,
        )
    expected_entries = captured["_entries"]
    budget = _RecoveryTreeBudget(name)

    def remove_one(
        current_parent: int, child_name: str, subject: str,
        relative: str,
    ) -> None:
        try:
            before = os.stat(
                child_name, dir_fd=current_parent, follow_symlinks=False,
            )
        except FileNotFoundError as exc:
            raise SetupError(
                f"setup removal entry disappeared: {subject}",
                code="TREE_CONFLICT", exit_code=4, subject=subject,
            ) from exc
        if stat.S_ISLNK(before.st_mode):
            raise SetupError(
                f"refusing to remove substituted setup symlink: {subject}",
                code="TREE_CONFLICT", exit_code=4, subject=subject,
            )
        if (
            relative not in expected_entries
            or _stat_fingerprint(before) != expected_entries[relative]
        ):
            raise SetupError(
                f"setup removal entry changed after preflight: {subject}",
                code="TREE_CONFLICT", exit_code=4, subject=subject,
            )
        budget.account(before, subject)
        if stat.S_ISREG(before.st_mode):
            if before.st_nlink != 1:
                raise SetupError(
                    f"refusing to remove hardlinked setup file: {subject}",
                    code="HARDLINK_REJECTED", subject=subject,
                )
            descriptor = budget.open(
                current_parent, child_name, _FILE_OPEN_FLAGS, subject,
            )
            try:
                opened = os.fstat(descriptor)
                _validate_opened_recovery_entry(
                    before, opened, descriptor, root_dev=root_dev,
                    root_mount=root_mount, subject=subject, kind="file",
                )
                _require_stable_named_entry(
                    current_parent, child_name, opened, subject,
                )
                os.unlink(child_name, dir_fd=current_parent)
                os.fsync(current_parent)
            finally:
                budget.close(descriptor)
            return
        if not stat.S_ISDIR(before.st_mode):
            raise SetupError(
                f"refusing to remove special setup node: {subject}",
                code="SPECIAL_NODE", subject=subject,
            )
        descriptor = budget.open(
            current_parent, child_name, _DIR_OPEN_FLAGS, subject,
        )
        try:
            opened = os.fstat(descriptor)
            _validate_opened_recovery_entry(
                before, opened, descriptor, root_dev=root_dev,
                root_mount=root_mount, subject=subject, kind="dir",
            )
            names = _bounded_directory_names(
                descriptor, limit=MAX_TREE_NODES - budget.nodes,
                subject=subject,
            )
            if _stat_fingerprint(opened) != _stat_fingerprint(os.fstat(descriptor)):
                raise SetupError(
                    f"setup directory changed before removal: {subject}",
                    code="TREE_CONFLICT", exit_code=4, subject=subject,
                )
            for nested in names:
                nested_relative = (
                    nested if relative == "." else f"{relative}/{nested}"
                )
                remove_one(
                    descriptor, nested,
                    f"{subject}/{nested}" if subject else nested,
                    nested_relative,
                )
            with os.scandir(descriptor) as remaining:
                if next(remaining, None) is not None:
                    raise SetupError(
                        f"setup directory gained entries during removal: {subject}",
                        code="TREE_CONFLICT", exit_code=4,
                        subject=subject,
                    )
            try:
                named = os.stat(
                    child_name, dir_fd=current_parent,
                    follow_symlinks=False,
                )
            except FileNotFoundError as exc:
                raise SetupError(
                    f"setup directory disappeared during removal: {subject}",
                    code="TREE_CONFLICT", exit_code=4, subject=subject,
                ) from exc
            if _anchor_fingerprint(named) != _anchor_fingerprint(opened):
                raise SetupError(
                    f"setup directory identity changed during removal: {subject}",
                    code="TREE_CONFLICT", exit_code=4, subject=subject,
                )
            try:
                os.rmdir(child_name, dir_fd=current_parent)
            except OSError as exc:
                if exc.errno in (errno.ENOTEMPTY, errno.ENOENT, errno.ENOTDIR):
                    raise SetupError(
                        f"setup directory changed during removal: {subject}",
                        code="TREE_CONFLICT", exit_code=4, subject=subject,
                    ) from exc
                raise
            os.fsync(current_parent)
        finally:
            budget.close(descriptor)

    remove_one(parent_fd, name, name, ".")


def _copy_tree_to_stage_at(
    source: Path, stage_fd: int, stage_name: str,
) -> str:
    """Copy a governed source tree into a retained staging directory."""
    with _RetainedWalker(source) as walker:
        root_info = os.fstat(walker.root_fd)
        stage_info = os.fstat(stage_fd)
        stage_mount = _mount_identity(stage_fd)
        os.mkdir(stage_name, stat.S_IMODE(root_info.st_mode), dir_fd=stage_fd)
        os.fsync(stage_fd)
        destination_budget = _RecoveryTreeBudget(stage_name)

        def open_destination(
            parent_fd: int, name: str, subject: str,
        ) -> int:
            if (
                len(walker._fds) + destination_budget.active_fds + 2
                > MAX_RETAINED_FDS
            ):
                raise SetupError(
                    "setup staging exceeded its combined descriptor budget",
                    code="TREE_RESOURCE_LIMIT", subject=subject,
                )
            before = os.stat(
                name, dir_fd=parent_fd, follow_symlinks=False,
            )
            descriptor = destination_budget.open(
                parent_fd, name, _DIR_OPEN_FLAGS, subject,
            )
            opened = os.fstat(descriptor)
            if (
                stat.S_ISLNK(before.st_mode)
                or not stat.S_ISDIR(before.st_mode)
                or _stat_fingerprint(before) != _stat_fingerprint(opened)
                or opened.st_dev != stage_info.st_dev
                or _mount_identity(descriptor) != stage_mount
            ):
                destination_budget.close(descriptor)
                raise SetupError(
                    f"setup staging directory changed identity: {subject}",
                    code="TREE_CONFLICT", exit_code=4, subject=subject,
                )
            return descriptor

        destination_root = open_destination(
            stage_fd, stage_name, stage_name,
        )
        try:
            os.fchmod(destination_root, stat.S_IMODE(root_info.st_mode))
            hasher = hashlib.sha256()
            hasher.update(b"sdm-tree-v1\0")

            def copy_directory(
                source_fd: int, destination_fd: int, prefix: str,
            ) -> None:
                source_subject = str(walker.root_path / prefix) \
                    if prefix else str(walker.root_path)
                for child_name in walker.scan(source_fd, source_subject):
                    relative = f"{prefix}/{child_name}" if prefix else child_name
                    child_subject = str(walker.root_path / relative)
                    child = walker.entry_stat(
                        source_fd, child_name, child_subject,
                    )
                    if stat.S_ISDIR(child.st_mode):
                        _hash_tree_record(
                            hasher, relative + "/", child.st_mode, None,
                        )
                        os.mkdir(
                            child_name, stat.S_IMODE(child.st_mode),
                            dir_fd=destination_fd,
                        )
                        os.fsync(destination_fd)
                        source_child = walker.open_dir(
                            source_fd, child_name, child_subject, child,
                        )
                        destination_child = -1
                        try:
                            destination_child = open_destination(
                                destination_fd, child_name, relative,
                            )
                            os.fchmod(
                                destination_child,
                                stat.S_IMODE(child.st_mode),
                            )
                            copy_directory(
                                source_child, destination_child, relative,
                            )
                            os.fsync(destination_child)
                        finally:
                            if destination_child >= 0:
                                destination_budget.close(destination_child)
                            walker._release_fd(source_child)
                    elif stat.S_ISREG(child.st_mode):
                        data, opened = walker.read_file(
                            source_fd, child_name, child_subject, child,
                        )
                        if (
                            len(walker._fds) + destination_budget.active_fds + 3
                            > MAX_RETAINED_FDS
                        ):
                            raise SetupError(
                                "setup staging exceeded its combined descriptor budget",
                                code="TREE_RESOURCE_LIMIT", subject=relative,
                            )
                        _write_bytes_sync_at(
                            destination_fd, child_name, data,
                            stat.S_IMODE(opened.st_mode),
                        )
                        _hash_tree_record(
                            hasher, relative, opened.st_mode, data,
                        )
                    else:
                        raise SetupError(
                            f"special source node while staging: {relative}",
                            code="SPECIAL_NODE", subject=relative,
                        )
                os.fsync(destination_fd)

            copy_directory(walker.root_fd, destination_root, "")
            os.fsync(destination_root)
            walker.verify()
            return "sha256:" + hasher.hexdigest()
        finally:
            destination_budget.close(destination_root)


def _operation_relative(operation: dict[str, Any]) -> str:
    try:
        target = operation["target"]
        if not isinstance(target, str):
            raise TypeError("target is not text")
        return "/".join(_portable_parts(target))
    except (ValueError, TypeError, SetupError) as exc:
        raise SetupError(
            "setup operation target is not project-relative",
            code="JOURNAL_INVALID", exit_code=3,
        ) from exc


def _require_journal_project_identity(
    project_fd: int, journal_path: Path, journal: dict[str, Any],
) -> None:
    try:
        current = _project_identity_from_fd(project_fd)
    except SetupError as exc:
        raise _journal_invalid(
            journal_path, "opened project identity is unavailable",
        ) from exc
    if current != journal.get("project_identity"):
        raise _journal_invalid(
            journal_path, "opened project differs from the journal host identity",
        )


def restore_journal(
    project_root: Path, journal_path: Path, journal: dict[str, Any], *,
    host: _SetupHost | None = None,
) -> None:
    if host is None:
        with host_lock(project_root) as acquired:
            restore_journal(
                project_root, journal_path, journal, host=acquired,
            )
        return
    project_fd = host.project_fd
    _require_journal_project_identity(project_fd, journal_path, journal)
    project_info = os.fstat(project_fd)
    project_mount = host.project_identity["mount_id"]
    backup_fd = -1
    backup_opened: list[int] = []
    backup_name = journal["backup_root"]
    try:
        try:
            os.stat(backup_name, dir_fd=project_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            backup_fd, backup_opened = _open_relative_directory(
                project_fd, backup_name,
            )

        def inspect(operation: dict[str, Any], slot: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
            relative = _operation_relative(operation)
            parts = _portable_parts(relative)
            parent_fd, opened = _open_relative_directory(
                project_fd, "/".join(parts[:-1]),
            )
            try:
                current = _entry_metadata_at(
                    parent_fd, parts[-1], root_dev=project_info.st_dev,
                    root_mount=project_mount,
                )
            finally:
                for descriptor in reversed(opened):
                    os.close(descriptor)
            backup = None if backup_fd < 0 else _entry_metadata_at(
                backup_fd, f"op-{slot:03d}", root_dev=project_info.st_dev,
                root_mount=project_mount,
            )
            return current, backup

        def matches(
            observed: dict[str, Any] | None,
            expected: dict[str, Any] | None,
        ) -> bool:
            return (
                observed is None if expected is None else
                observed is not None and all(
                    observed[field] == expected[field]
                    for field in ("digest", "mode", "uid", "gid", "kind")
                )
            )

        def matches_desired(
            observed: dict[str, Any] | None,
            operation: dict[str, Any],
        ) -> bool:
            return (
                observed is None
                or (
                    operation["desired"] is not None
                    and observed["kind"] == operation["kind"]
                    and observed["digest"] == operation["desired_digest"]
                )
            )

        def validate_state(
            operation: dict[str, Any], current: dict[str, Any] | None,
            backup: dict[str, Any] | None,
        ) -> None:
            original = operation["original"]
            if original is None:
                if backup is not None:
                    raise _journal_invalid(
                        journal_path, "unexpected backup exists",
                    )
                if not matches_desired(current, operation):
                    raise _journal_invalid(
                        journal_path,
                        "current target is neither absent nor authentic desired content",
                    )
                return
            if backup is None:
                if not matches(current, original):
                    raise _journal_invalid(
                        journal_path,
                        "required authentic backup is missing",
                    )
                return
            if (
                backup["kind"] != original["kind"]
                or backup["digest"] != operation["backup_digest"]
                or backup["uid"] != original["uid"]
                or backup["gid"] != original["gid"]
                or (
                    original["kind"] == "dir"
                    and backup["mode"] != original["mode"]
                )
                or (
                    original["kind"] == "file"
                    and backup["mode"] not in {original["mode"], 0o600}
                )
            ):
                raise _journal_invalid(
                    journal_path, "backup metadata differs from the journal binding",
                )
            if not matches_desired(current, operation):
                raise _journal_invalid(
                    journal_path,
                    "current target is neither absent nor authentic desired content",
                )

        # Validate every authentic backup and every current ancestor before
        # making the first rollback mutation.
        for slot, operation in enumerate(journal["operations"]):
            if not operation["started"] and not operation["applied"]:
                continue
            current, backup = inspect(operation, slot)
            validate_state(operation, current, backup)

        for slot in reversed(range(len(journal["operations"]))):
            operation = journal["operations"][slot]
            if not operation["started"] and not operation["applied"]:
                continue
            relative = _operation_relative(operation)
            parts = _portable_parts(relative)
            target_parent_fd, target_opened = _open_relative_directory(
                project_fd, "/".join(parts[:-1]),
            )
            try:
                original = operation["original"]
                backup_slot = f"op-{slot:03d}"
                current = _entry_metadata_at(
                    target_parent_fd, parts[-1],
                    root_dev=project_info.st_dev,
                    root_mount=project_mount,
                )
                backup = None if backup_fd < 0 else _entry_metadata_at(
                    backup_fd, backup_slot,
                    root_dev=project_info.st_dev,
                    root_mount=project_mount,
                )
                validate_state(operation, current, backup)
                if original is None:
                    if current is not None:
                        _remove_entry_at(
                            target_parent_fd, parts[-1],
                            root_dev=project_info.st_dev,
                            root_mount=project_mount,
                            expected_digest=current["digest"],
                            expected_kind=current["kind"],
                            expected_tree=current["_entries"],
                        )
                elif backup is not None:
                    restored_fd = _open_bound_entry_at(
                        backup_fd, backup_slot, backup,
                        root_dev=project_info.st_dev,
                        root_mount=project_mount, subject=backup_slot,
                    )
                    try:
                        restored_info = os.fstat(restored_fd)
                        if (
                            tuple(backup["_fingerprint"])
                            != _stat_fingerprint(restored_info)
                            or restored_info.st_dev != project_info.st_dev
                            or _mount_identity(restored_fd) != project_mount
                        ):
                            raise _journal_invalid(
                                journal_path,
                                "backup identity changed immediately before restoration",
                            )
                        if current is not None:
                            _remove_entry_at(
                                target_parent_fd, parts[-1],
                                root_dev=project_info.st_dev,
                                root_mount=project_mount,
                                expected_digest=current["digest"],
                                expected_kind=current["kind"],
                                expected_tree=current["_entries"],
                            )
                        os.fchmod(restored_fd, int(original["mode"]))
                        os.fsync(restored_fd)
                        named = os.stat(
                            backup_slot, dir_fd=backup_fd,
                            follow_symlinks=False,
                        )
                        if _stat_fingerprint(named) != _stat_fingerprint(os.fstat(restored_fd)):
                            raise _journal_invalid(
                                journal_path,
                                "backup name changed immediately before restoration",
                            )
                        os.replace(
                            backup_slot, parts[-1], src_dir_fd=backup_fd,
                            dst_dir_fd=target_parent_fd,
                        )
                    finally:
                        os.close(restored_fd)
                    os.fsync(backup_fd)
                    os.fsync(target_parent_fd)
            finally:
                for descriptor in reversed(target_opened):
                    os.close(descriptor)
        _require_host_anchors(host)
        _cleanup_transaction_at(host, journal_path.name, journal)
    finally:
        for descriptor in reversed(backup_opened):
            with contextlib.suppress(OSError):
                os.close(descriptor)


def cleanup_committed(
    project_root: Path, journal_path: Path, journal: dict[str, Any], *,
    host: _SetupHost | None = None,
) -> None:
    if host is None:
        with host_lock(project_root) as acquired:
            cleanup_committed(
                project_root, journal_path, journal, host=acquired,
            )
        return
    _require_journal_project_identity(host.project_fd, journal_path, journal)
    _require_host_anchors(host)
    _cleanup_transaction_at(host, journal_path.name, journal)


def _cleanup_transaction_at(
    host: _SetupHost, journal_name: str, journal: dict[str, Any],
) -> None:
    project_fd = host.project_fd
    project_info = os.fstat(project_fd)
    project_mount = host.project_identity["mount_id"]

    def cleanup_invalid(message: str, subject: str) -> SetupError:
        return SetupError(
            f"setup cleanup state is invalid: {message}",
            code="JOURNAL_INVALID", exit_code=3, subject=subject,
        )

    for key in ("stage", "backup_root"):
        root_name = journal[key]
        captured = _entry_metadata_at(
            project_fd, root_name,
            root_dev=project_info.st_dev, root_mount=project_mount,
        )
        if captured is None:
            continue
        if captured["kind"] != "dir":
            raise cleanup_invalid(
                f"{key} root is not a directory", root_name,
            )
        root_fd = _open_bound_entry_at(
            project_fd, root_name, captured,
            root_dev=project_info.st_dev, root_mount=project_mount,
            subject=root_name,
        )
        try:
            names = _bounded_directory_names(
                root_fd, limit=len(journal["operations"]) + 1,
                subject=root_name,
            )
            allowed: dict[str, tuple[dict[str, Any], str, str]] = {}
            for slot, operation in enumerate(journal["operations"]):
                slot_name = f"op-{slot:03d}"
                if key == "stage":
                    if operation["desired"] is not None:
                        allowed[slot_name] = (
                            operation, operation["kind"],
                            operation["desired_digest"],
                        )
                elif operation["original"] is not None:
                    allowed[slot_name] = (
                        operation, operation["kind"],
                        operation["backup_digest"],
                    )
            for entry_name in names:
                binding = allowed.get(entry_name)
                if binding is None:
                    raise cleanup_invalid(
                        f"{key} contains an unjournaled entry", entry_name,
                    )
                operation, expected_kind, expected_digest = binding
                observed = _entry_metadata_at(
                    root_fd, entry_name,
                    root_dev=project_info.st_dev,
                    root_mount=project_mount,
                )
                if (
                    observed is None
                    or observed["kind"] != expected_kind
                    or observed["digest"] != expected_digest
                ):
                    raise cleanup_invalid(
                        f"{key} entry differs from its digest binding",
                        entry_name,
                    )
                if key == "backup_root":
                    original = operation["original"]
                    if (
                        observed["uid"] != original["uid"]
                        or observed["gid"] != original["gid"]
                        or (
                            original["kind"] == "dir"
                            and observed["mode"] != original["mode"]
                        )
                        or (
                            original["kind"] == "file"
                            and observed["mode"] not in {
                                original["mode"], 0o600,
                            }
                        )
                    ):
                        raise cleanup_invalid(
                            "backup entry metadata differs from its binding",
                            entry_name,
                        )
        finally:
            os.close(root_fd)
        _require_host_anchors(host)
        _remove_entry_at(
            project_fd, root_name,
            root_dev=project_info.st_dev, root_mount=project_mount,
            expected_digest=captured["digest"], expected_kind="dir",
            expected_tree=captured["_entries"],
        )
    for key in ("stage", "backup_root"):
        try:
            os.stat(
                journal[key], dir_fd=project_fd, follow_symlinks=False,
            )
        except FileNotFoundError:
            continue
        raise SetupError(
            "setup transaction root reappeared during cleanup",
            code="TREE_CONFLICT", exit_code=4, subject=journal[key],
        )
    journal_info = _entry_metadata_at(
        host.bmad_fd, journal_name,
        root_dev=os.fstat(host.bmad_fd).st_dev,
        root_mount=host.bmad_mount,
    )
    if journal_info is None:
        return
    if journal_info["kind"] != "file":
        raise cleanup_invalid("journal is not a regular file", journal_name)
    observed_journal = _read_setup_json_at(
        host.bmad_fd, journal_name, "setup recovery journal",
    )
    if observed_journal != journal:
        raise cleanup_invalid(
            "journal changed before cleanup", journal_name,
        )
    _require_host_anchors(host)
    _remove_entry_at(
        host.bmad_fd, journal_name,
        root_dev=os.fstat(host.bmad_fd).st_dev,
        root_mount=host.bmad_mount,
        expected_digest=journal_info["digest"], expected_kind="file",
        expected_tree=journal_info["_entries"],
    )


def _validate_committed_set_at(
    host: _SetupHost, journal_path: Path, journal: dict[str, Any],
) -> None:
    """Verify every desired target before discarding rollback material."""
    project_info = os.fstat(host.project_fd)
    for operation in journal["operations"]:
        relative = _operation_relative(operation)
        parts = _portable_parts(relative)
        try:
            parent_fd, opened = _open_relative_directory(
                host.project_fd, "/".join(parts[:-1]),
            )
            try:
                current = _entry_metadata_at(
                    parent_fd, parts[-1], root_dev=project_info.st_dev,
                    root_mount=host.project_identity["mount_id"],
                )
            finally:
                for descriptor in reversed(opened):
                    os.close(descriptor)
        except (OSError, SetupError) as exc:
            raise SetupError(
                f"committed target cannot be verified: {relative}",
                code="RECONCILIATION_REQUIRED", exit_code=3,
                subject=str(journal_path),
            ) from exc
        desired = operation["desired"]
        valid = (
            current is None if desired is None
            else current is not None
            and current["kind"] == operation["kind"]
            and current["digest"] == operation["desired_digest"]
        )
        if not valid:
            raise SetupError(
                f"committed target differs from its desired binding: {relative}",
                code="RECONCILIATION_REQUIRED", exit_code=3,
                subject=str(journal_path),
            )


def apply_plan(
    project_root: Path, journal_path: Path, journal: dict[str, Any],
    source: Path | None, desired_files: dict[int, bytes], *,
    host: _SetupHost | None = None,
) -> list[str]:
    if host is None:
        with host_lock(project_root) as acquired:
            return apply_plan(
                project_root, journal_path, journal, source, desired_files,
                host=acquired,
            )
    validate_setup_journal(
        project_root, journal_path, journal, project_fd=host.project_fd,
    )
    _write_json_atomic_at(
        host, journal_path.name, journal, exclusive=True,
    )
    try:
        os.mkdir(journal["stage"], 0o700, dir_fd=host.project_fd)
        os.fsync(host.project_fd)
        os.mkdir(journal["backup_root"], 0o700, dir_fd=host.project_fd)
        os.fsync(host.project_fd)
    except FileExistsError as exc:
        raise SetupError(
            "setup transaction directory already exists",
            code="TREE_CONFLICT", exit_code=4,
            subject=journal["transaction_id"],
        ) from exc
    stage_fd, stage_opened = _open_relative_directory(
        host.project_fd, journal["stage"],
    )
    try:
        backup_fd, backup_opened = _open_relative_directory(
            host.project_fd, journal["backup_root"],
        )
    except Exception:
        for descriptor in reversed(stage_opened):
            with contextlib.suppress(OSError):
                os.close(descriptor)
        raise
    staged_hasher = hashlib.sha256()
    staged_hasher.update(b"sdm-module-v1\0")
    changed: list[str] = []
    raw_fault = os.environ.get("SDM_SETUP_FAULT_AFTER", "")
    fault_after = int(raw_fault) if raw_fault.isdigit() else -1
    project_info = os.fstat(host.project_fd)
    project_mount = host.project_identity["mount_id"]
    try:
        for index, operation in enumerate(journal["operations"]):
            if operation["desired"] is None:
                continue
            stage_name = f"op-{index:03d}"
            if operation["kind"] == "dir":
                if source is None:
                    raise SetupError(
                        "source is required while staging Skills",
                        code="SOURCE_REQUIRED",
                    )
                observed_digest = _copy_tree_to_stage_at(
                    source / SKILLS[index], stage_fd, stage_name,
                )
                staged_hasher.update(
                    SKILLS[index].encode() + b"\0" +
                    observed_digest.encode() + b"\0"
                )
            else:
                content = desired_files[index]
                _write_bytes_sync_at(stage_fd, stage_name, content)
                observed_digest = digest_bytes(content)
            if observed_digest != operation["desired_digest"]:
                raise SetupError(
                    "staged setup content differs from its journal binding",
                    code="TREE_CONFLICT", exit_code=4,
                    subject=f"{journal['stage']}/{stage_name}",
                )
        if journal["command"] != "uninstall":
            observed_module_digest = "sha256:" + staged_hasher.hexdigest()
            if observed_module_digest != journal["source_digest"]:
                raise SetupError(
                    "source changed while staging module Skills",
                    code="TREE_CONFLICT", exit_code=4,
                    subject=str(source),
                )
        _require_host_anchors(host)
        for index, operation in enumerate(journal["operations"], 1):
            slot = index - 1
            relative = _operation_relative(operation)
            parts = _portable_parts(relative)
            parent_relative = "/".join(parts[:-1])
            target_name = parts[-1]
            stage_name = f"op-{slot:03d}"
            target_parent_fd, target_opened = _open_relative_directory(
                host.project_fd, parent_relative, create=True,
            )
            current_guard = stage_guard = -1
            try:
                target_parent_identity = os.fstat(target_parent_fd)
                current = _entry_metadata_at(
                    target_parent_fd, target_name,
                    root_dev=project_info.st_dev, root_mount=project_mount,
                )
                original = operation["original"]
                if (current is None) != (original is None):
                    raise SetupError(
                        f"setup target existence changed before commit: {relative}",
                        code="TREE_CONFLICT", exit_code=4, subject=relative,
                    )
                if current is not None and any(
                    current[field] != original[field]
                    for field in ("digest", "mode", "uid", "gid", "kind")
                ):
                    raise SetupError(
                        f"setup target changed before commit: {relative}",
                        code="TREE_CONFLICT", exit_code=4, subject=relative,
                    )
                if _entry_metadata_at(
                    backup_fd, stage_name,
                    root_dev=project_info.st_dev, root_mount=project_mount,
                ) is not None:
                    raise SetupError(
                        f"setup backup slot already exists: {stage_name}",
                        code="TREE_CONFLICT", exit_code=4, subject=stage_name,
                    )
                if operation["desired"] is not None:
                    staged = _entry_metadata_at(
                        stage_fd, stage_name,
                        root_dev=project_info.st_dev, root_mount=project_mount,
                    )
                    if staged is None or staged["kind"] != operation["kind"] or staged["digest"] != operation["desired_digest"]:
                        raise SetupError(
                            f"staged setup content changed: {stage_name}",
                            code="TREE_CONFLICT", exit_code=4,
                            subject=stage_name,
                        )
                # Persist intent before the first destructive rename.
                operation["started"] = True
                journal["phase"] = "applying"
                _write_json_atomic_at(host, journal_path.name, journal)
                if current is not None:
                    current_now = _entry_metadata_at(
                        target_parent_fd, target_name,
                        root_dev=project_info.st_dev,
                        root_mount=project_mount,
                    )
                    if current_now is None or any(
                        current_now[field] != current[field]
                        for field in ("digest", "mode", "uid", "gid", "kind")
                    ):
                        raise SetupError(
                            f"setup target changed immediately before backup: {relative}",
                            code="TREE_CONFLICT", exit_code=4,
                            subject=relative,
                        )
                    current_guard = _open_bound_entry_at(
                        target_parent_fd, target_name, current_now,
                        root_dev=project_info.st_dev,
                        root_mount=project_mount, subject=relative,
                    )
                    current_identity = _anchor_fingerprint(os.fstat(current_guard))
                    os.replace(
                        target_name, stage_name,
                        src_dir_fd=target_parent_fd, dst_dir_fd=backup_fd,
                    )
                    try:
                        backup_named = os.stat(
                            stage_name, dir_fd=backup_fd, follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        os.close(current_guard)
                        current_guard = -1
                        current_guard = _reopen_published_entry_at(
                            backup_fd, stage_name, current_now, current_identity,
                            root_dev=project_info.st_dev,
                            root_mount=project_mount, subject=relative,
                        )
                        backup_named = os.stat(
                            stage_name, dir_fd=backup_fd, follow_symlinks=False,
                        )
                    if _anchor_fingerprint(backup_named) != \
                            _anchor_fingerprint(os.fstat(current_guard)):
                        raise SetupError(
                            f"setup backup name changed after install: {relative}",
                            code="TREE_CONFLICT", exit_code=4,
                            subject=relative,
                        )
                    if operation["kind"] == "file":
                        os.fchmod(current_guard, 0o600)
                        os.fsync(current_guard)
                    os.fsync(backup_fd)
                    os.fsync(target_parent_fd)
                if operation["desired"] is not None:
                    staged_now = _entry_metadata_at(
                        stage_fd, stage_name,
                        root_dev=project_info.st_dev,
                        root_mount=project_mount,
                    )
                    if (
                        staged_now is None
                        or staged_now["kind"] != operation["kind"]
                        or staged_now["digest"] != operation["desired_digest"]
                    ):
                        raise SetupError(
                            f"staged setup content changed immediately before install: {stage_name}",
                            code="TREE_CONFLICT", exit_code=4,
                            subject=stage_name,
                        )
                    stage_guard = _open_bound_entry_at(
                        stage_fd, stage_name, staged_now,
                        root_dev=project_info.st_dev,
                        root_mount=project_mount, subject=stage_name,
                    )
                    staged_identity = _anchor_fingerprint(os.fstat(stage_guard))
                    os.replace(
                        stage_name, target_name,
                        src_dir_fd=stage_fd, dst_dir_fd=target_parent_fd,
                    )
                    try:
                        installed_named = os.stat(
                            target_name, dir_fd=target_parent_fd,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        os.close(stage_guard)
                        stage_guard = -1
                        stage_guard = _reopen_published_entry_at(
                            target_parent_fd, target_name, staged_now,
                            staged_identity,
                            root_dev=project_info.st_dev,
                            root_mount=project_mount, subject=relative,
                        )
                        installed_named = os.stat(
                            target_name, dir_fd=target_parent_fd,
                            follow_symlinks=False,
                        )
                    if _anchor_fingerprint(installed_named) != \
                            _anchor_fingerprint(os.fstat(stage_guard)):
                        raise SetupError(
                            f"installed setup target changed identity: {relative}",
                            code="TREE_CONFLICT", exit_code=4,
                            subject=relative,
                        )
                    os.fsync(stage_fd)
                    os.fsync(target_parent_fd)
                rebound_fd, rebound_opened = _open_relative_directory(
                    host.project_fd, parent_relative,
                )
                try:
                    rebound = os.fstat(rebound_fd)
                    if (rebound.st_dev, rebound.st_ino) != (
                        target_parent_identity.st_dev,
                        target_parent_identity.st_ino,
                    ):
                        raise SetupError(
                            f"setup target ancestor changed during commit: {relative}",
                            code="TREE_CONFLICT", exit_code=4,
                            subject=relative,
                        )
                finally:
                    for descriptor in reversed(rebound_opened):
                        os.close(descriptor)
                operation["applied"] = True
                changed.append(str(project_root.joinpath(*parts)))
                if index == len(journal["operations"]):
                    journal["marker_applied"] = True
                    journal["phase"] = "committed"
                _write_json_atomic_at(host, journal_path.name, journal)
                if journal["marker_applied"]:
                    _validate_committed_set_at(host, journal_path, journal)
                if fault_after == index:
                    if os.environ.get("SDM_SETUP_FAULT_MODE") == "exception":
                        raise RuntimeError(f"injected setup failure after operation {index}")
                    os._exit(99)
            finally:
                for descriptor in (stage_guard, current_guard):
                    if descriptor >= 0:
                        with contextlib.suppress(OSError):
                            os.close(descriptor)
                for descriptor in reversed(target_opened):
                    os.close(descriptor)
    finally:
        for descriptors in (backup_opened, stage_opened):
            for descriptor in reversed(descriptors):
                with contextlib.suppress(OSError):
                    os.close(descriptor)
    cleanup_committed(
        project_root, journal_path, journal, host=host,
    )
    return changed


def validate_expected(command: str, expected: str | None,
                      current: str | None) -> None:
    if expected is None:
        raise SetupError("--expected-install-digest is required",
                         code="EXPECTED_DIGEST_REQUIRED", exit_code=4)
    if expected != "absent" and not DIGEST_RE.fullmatch(expected):
        raise SetupError("expected digest must be absent or sha256:<64hex>",
                         code="EXPECTED_DIGEST_INVALID", exit_code=4)
    actual = current or "absent"
    if command == "install" and expected != "absent":
        raise SetupError("install requires expected digest absent",
                         code="INSTALL_CAS", exit_code=4)
    if command in ("update", "uninstall") and expected == "absent":
        raise SetupError(f"{command} requires the current installed digest",
                         code="INSTALL_CAS", exit_code=4)
    if expected != actual:
        raise SetupError(f"install digest conflict: expected {expected}, found {actual}",
                         code="INSTALL_CAS", exit_code=4)


def command_mutation(args: argparse.Namespace,
                     project_root: Path, *,
                     host: _SetupHost | None = None) -> tuple[str, dict[str, Any]]:
    if host is None:
        if args.command in ("install", "update"):
            load_answers(args.answers)
        with host_lock(project_root) as acquired:
            return command_mutation(args, project_root, host=acquired)
    _require_host_anchors(host)
    skills_root = project_root / ".agents" / "skills"
    current = module_digest(skills_root)
    _require_host_anchors(host)
    validate_expected(args.command, args.expected_install_digest, current)
    source: Path | None = None
    answers: dict[str, str] | None = None
    source_digest: str | None = None
    if args.command in ("install", "update"):
        if not args.skills_source:
            raise SetupError("--skills-source is required", code="SOURCE_REQUIRED")
        source = Path(os.path.abspath(args.skills_source))
        source_digest = validate_source(source)
        answers = load_answers(args.answers)
    elif args.answers is not None or args.skills_source is not None:
        raise SetupError("uninstall rejects --answers and --skills-source",
                         code="CLI_USAGE")
    journal_path = project_root / "_bmad" / ".sdm-setup-journal.json"
    try:
        os.stat(
            journal_path.name, dir_fd=host.bmad_fd,
            follow_symlinks=False,
        )
        journal_exists = True
    except FileNotFoundError:
        journal_exists = False
    if journal_exists:
        raise SetupError("unfinished setup journal requires recover",
                         code="RECOVERY_REQUIRED", exit_code=3,
                         subject=str(journal_path))
    journal, desired_files = prepare_plan(
        project_root, args.command, source, answers, source_digest,
        host=host,
    )
    _require_host_anchors(host)
    try:
        changed = apply_plan(
            project_root, journal_path, journal, source, desired_files,
            host=host,
        )
    except Exception:
        try:
            os.stat(
                journal_path.name, dir_fd=host.bmad_fd,
                follow_symlinks=False,
            )
            journal_exists = True
        except FileNotFoundError:
            journal_exists = False
        if journal_exists:
            latest = _read_setup_json_at(
                host.bmad_fd, journal_path.name, "setup recovery journal",
            )
            latest = validate_setup_journal(
                project_root, journal_path, latest,
                project_fd=host.project_fd,
            )
            if latest["transaction_id"] != journal["transaction_id"]:
                raise SetupError(
                    "setup journal changed transaction identity",
                    code="RECOVERY_REQUIRED", exit_code=3,
                    subject=str(journal_path),
                )
            static_keys = (
                "schema", "transaction_id", "command", "project_identity",
                "stage", "backup_root", "source_digest",
            )
            operation_static_keys = (
                "target", "desired", "kind", "backup", "original",
                "desired_digest", "backup_digest",
            )
            if (
                any(latest[key] != journal[key] for key in static_keys)
                or any(
                    any(
                        observed[key] != expected[key]
                        for key in operation_static_keys
                    )
                    for observed, expected in zip(
                        latest["operations"], journal["operations"],
                        strict=True,
                    )
                )
            ):
                raise SetupError(
                    "setup journal plan binding changed during transaction",
                    code="JOURNAL_INVALID", exit_code=3,
                    subject=str(journal_path),
                )
            if not latest["marker_applied"]:
                restore_journal(
                    project_root, journal_path, latest, host=host,
                )
        raise
    _require_host_anchors(host)
    installed = module_digest(skills_root)
    _require_host_anchors(host)
    if args.command == "uninstall":
        if installed is not None:
            raise SetupError("uninstall left installed Skills",
                             code="UNINSTALL_INCOMPLETE", exit_code=70)
        revision = "absent"
    else:
        if installed != source_digest:
            raise SetupError("installed digest differs from staged source",
                             code="INSTALL_DIGEST_MISMATCH", exit_code=70)
        revision = installed or "absent"
    return revision, {"changed_targets": changed, "installed_digest": revision}


def command_recover(project_root: Path, *,
                    host: _SetupHost | None = None) -> tuple[str, dict[str, Any]]:
    if host is None:
        with host_lock(project_root) as acquired:
            return command_recover(project_root, host=acquired)
    _require_host_anchors(host)
    journal_path = project_root / "_bmad" / ".sdm-setup-journal.json"
    try:
        os.stat(
            journal_path.name, dir_fd=host.bmad_fd,
            follow_symlinks=False,
        )
        journal_exists = True
    except FileNotFoundError:
        journal_exists = False
    if not journal_exists:
        current = module_digest(project_root / ".agents" / "skills")
        _require_host_anchors(host)
        return current or "absent", {"recovered": False, "reason": "no journal"}
    try:
        journal = _read_setup_json_at(
            host.bmad_fd, journal_path.name, "setup recovery journal",
        )
    except Exception as exc:
        raise SetupError(f"recovery journal is malformed: {exc}",
                         code="JOURNAL_INVALID", exit_code=3,
                         subject=str(journal_path)) from exc
    journal = validate_setup_journal(
        project_root, journal_path, journal, project_fd=host.project_fd,
    )
    if journal["marker_applied"]:
        _validate_committed_set_at(host, journal_path, journal)
        current = module_digest(project_root / ".agents" / "skills")
        _require_host_anchors(host)
        cleanup_committed(
            project_root, journal_path, journal, host=host,
        )
        return current or "absent", {"recovered": True,
                                     "disposition": "committed"}
    restore_journal(project_root, journal_path, journal, host=host)
    current = module_digest(project_root / ".agents" / "skills")
    _require_host_anchors(host)
    return current or "absent", {"recovered": True,
                                 "disposition": "rolled_back"}


def _load_toml_bytes(raw: bytes | None, subject: str) -> dict[str, Any]:
    if raw is None:
        return {}
    try:
        value = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeError) as exc:
        raise SetupError(
            f"invalid TOML at {subject}: {exc}", code="HOST_TOML_INVALID",
            exit_code=3, subject=subject,
        ) from exc
    if not isinstance(value, dict):
        raise SetupError(f"TOML root is not a table: {subject}",
                         code="HOST_TOML_INVALID", exit_code=3,
                         subject=subject)
    return value


def _configured_module(raw: bytes | None, subject: str) -> dict[str, Any] | None:
    data = _load_toml_bytes(raw, subject)
    modules = data.get("modules")
    if modules is None:
        return None
    if not isinstance(modules, dict):
        raise SetupError("config modules key must be a table",
                         code="CONFIG_ROSTER_DRIFT", exit_code=3,
                         subject=subject, pointer="/modules")
    configured = modules.get(MODULE_CODE)
    if configured is None:
        return None
    if not isinstance(configured, dict):
        raise SetupError("modules.sdm must be a table",
                         code="CONFIG_ROSTER_DRIFT", exit_code=3,
                         subject=subject, pointer="/modules/sdm")
    return configured


def _validate_configs(files: dict[str, bytes], current: str | None,
                      source_digest: str | None, *, legacy: bool = False) -> None:
    shared_rel = "_bmad/custom/config.toml"
    personal_rel = "_bmad/custom/config.user.toml"
    shared = _configured_module(files.get(shared_rel), shared_rel)
    personal = _configured_module(files.get(personal_rel), personal_rel)
    if current is None:
        if shared is not None or personal is not None:
            raise SetupError(
                "orphan modules.sdm configuration without an installed module",
                code="CONFIG_ORPHAN", exit_code=3,
                subject="modules.sdm", pointer="/modules/sdm",
            )
        return
    expected_shared = {
        *SHARED_KEYS, "name", "version", "install_digest", "source_digest",
    }
    if legacy:
        expected_shared.add("promotion_state_root")
    if shared is None or set(shared) != expected_shared:
        actual = [] if shared is None else sorted(shared)
        raise SetupError(
            f"shared modules.sdm key roster mismatch: {actual}",
            code="CONFIG_ROSTER_DRIFT", exit_code=3,
            subject=shared_rel, pointer="/modules/sdm",
        )
    if personal is None or set(personal) != {"sdm_active_workspace"}:
        actual = [] if personal is None else sorted(personal)
        raise SetupError(
            f"personal modules.sdm key roster mismatch: {actual}",
            code="CONFIG_ROSTER_DRIFT", exit_code=3,
            subject=personal_rel, pointer="/modules/sdm",
        )
    if shared["name"] != MODULE_NAME or shared["version"] != "1.0.0":
        raise SetupError(
            "shared module identity/version drift",
            code="CONFIG_VALUE_DRIFT", exit_code=3,
            subject=shared_rel, pointer="/modules/sdm",
        )
    if (shared["install_digest"] != current or
            shared["source_digest"] != current or
            (source_digest is not None and
             shared["source_digest"] != source_digest)):
        raise SetupError(
            "shared install/source digest does not bind the exact Skill tree",
            code="CONFIG_DIGEST_DRIFT", exit_code=3,
            subject=shared_rel, pointer="/modules/sdm/install_digest",
        )
    for key in SHARED_KEYS:
        value = shared[key]
        if not isinstance(value, str) or "\x00" in value or value != value.strip():
            raise SetupError(
                f"invalid shared configuration value: {key}",
                code="CONFIG_VALUE_DRIFT", exit_code=3,
                subject=shared_rel, pointer=f"/modules/sdm/{key}",
            )
    for key in ("sdm_output_root", "sdm_trust_root"):
        if not shared[key]:
            raise SetupError(
                f"required shared path is empty: {key}",
                code="CONFIG_VALUE_DRIFT", exit_code=3,
                subject=shared_rel, pointer=f"/modules/sdm/{key}",
            )
    for key in ("data_platform_skill", "source_control_platform_skill",
                "ci_platform_skill", "ml_execution_platform_skill"):
        value = shared[key]
        if value and (value in (".", "..") or Path(value).name != value):
            raise SetupError(
                f"platform binding is not an exact Skill directory name: {key}",
                code="CONFIG_VALUE_DRIFT", exit_code=3,
                subject=shared_rel, pointer=f"/modules/sdm/{key}",
            )
    try:
        resolve_sprint_planning_hook(personal["sdm_active_workspace"])
    except SetupError as exc:
        raise SetupError(
            f"invalid personal active workspace: {exc}",
            code="CONFIG_VALUE_DRIFT", exit_code=3,
            subject=personal_rel,
            pointer="/modules/sdm/sdm_active_workspace",
        ) from exc


def _asset_rows(capture: dict[str, Any], label: str) -> list[list[str]]:
    relative = "sdm-setup/assets/module-help.csv"
    raw = capture["files"].get(relative)
    if raw is None:
        raise SetupError(
            f"installed/source module-help asset is missing: {label}",
            code="HELP_ASSET_INVALID", exit_code=3, subject=label,
        )
    _, rows = _parse_help_bytes(
        raw, label, asset=True, roster=tuple(capture["skills"]),
    )
    return rows


def _validate_help_registry(raw: bytes | None, subject: str,
                            expected_rows: list[list[str]] | None) -> None:
    if raw is None:
        if expected_rows is None:
            return
        raise SetupError(f"help registry is missing: {subject}",
                         code="HELP_REGISTRY_DRIFT", exit_code=3,
                         subject=subject)
    _, rows = _parse_help_bytes(raw, subject, asset=False)
    related = [row for row in rows
               if row[0] == MODULE_NAME or row[1].startswith("sdm-")]
    expected = expected_rows or []
    if related != expected:
        raise SetupError(
            f"SDM help row roster/order/content drift: {subject}",
            code="HELP_REGISTRY_DRIFT", exit_code=3, subject=subject,
        )


def _validate_hook(raw: bytes | None, current: str | None) -> None:
    subject = "_bmad/custom/bmad-sprint-planning.toml"
    data = _load_toml_bytes(raw, subject)
    workflow = data.get("workflow", {})
    if not isinstance(workflow, dict):
        raise SetupError("hook workflow key must be a table",
                         code="HOOK_DRIFT", exit_code=3, subject=subject,
                         pointer="/workflow")
    steps = workflow.get("activation_steps_prepend", [])
    if not isinstance(steps, list) or not all(isinstance(item, str)
                                               for item in steps):
        raise SetupError("activation_steps_prepend must be a string array",
                         code="HOOK_DRIFT", exit_code=3, subject=subject,
                         pointer="/workflow/activation_steps_prepend")
    related = [item for item in steps
               if re.search(
                   r"(?i)(?:modules\.sdm|sdm-[a-z]|specification-driven ml)",
                   item,
               )]
    expected = [HOOK_TEXT] if current is not None else []
    if related != expected:
        raise SetupError(
            "sprint-planning SDM hook roster or semantics drift",
            code="HOOK_DRIFT", exit_code=3, subject=subject,
            pointer="/workflow/activation_steps_prepend",
        )


def _capture_validation_state(project_root: Path,
                              source: Path | None) -> dict[str, Any]:
    installed = _secure_module_capture(
        project_root / ".agents" / "skills", source=False,
    )
    owned = _secure_owned_capture(project_root)
    source_capture = (_secure_module_capture(source, source=True)
                      if source is not None else None)
    return {"installed": installed, "owned": owned,
            "source": source_capture}


def _validate_captured_state(state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    installed = state["installed"]
    owned = state["owned"]
    source_capture = state["source"]
    current = installed["digest"]
    legacy = installed.get("legacy", False)
    source_digest = (source_capture["digest"]
                     if source_capture is not None else None)
    if (not legacy and source_capture is not None and current is not None and
            source_digest != current):
        raise SetupError(
            "installed Skill tree differs from the supplied source tree",
            code="INSTALL_SOURCE_DRIFT", exit_code=3,
            subject=".agents/skills",
        )
    if not legacy and source_capture is not None and current is not None:
        installed_root_modes = {
            name: installed["nodes"][name + "/"]["mode"] for name in SKILLS
        }
        source_root_modes = {
            name: source_capture["nodes"][name + "/"]["mode"]
            for name in SKILLS
        }
        if installed_root_modes != source_root_modes:
            raise SetupError(
                "installed Skill root modes differ from the supplied source tree",
                code="INSTALL_SOURCE_DRIFT", exit_code=3,
                subject=".agents/skills",
            )
    if owned["files"].get("_bmad/.sdm-setup-journal.json") is not None:
        raise SetupError(
            "unfinished setup journal requires recover",
            code="RECOVERY_REQUIRED", exit_code=3,
            subject="_bmad/.sdm-setup-journal.json",
        )

    installed_rows = _asset_rows(installed, "installed module-help asset") \
        if current is not None else None
    source_rows = _asset_rows(source_capture, "source module-help asset") \
        if source_capture is not None else None
    if (not legacy and installed_rows is not None and source_rows is not None and
            installed_rows != source_rows):
        raise SetupError(
            "installed and source module-help assets differ",
            code="HELP_ASSET_DRIFT", exit_code=3,
            subject="sdm-setup/assets/module-help.csv",
        )
    expected_rows = installed_rows
    files = owned["files"]
    _validate_help_registry(files.get("_bmad/_config/bmad-help.csv"),
                            "_bmad/_config/bmad-help.csv", expected_rows)
    _validate_help_registry(files.get("_bmad/module-help.csv"),
                            "_bmad/module-help.csv", expected_rows)
    _validate_hook(files.get("_bmad/custom/bmad-sprint-planning.toml"),
                   current)
    _validate_configs(files, current, None if legacy else source_digest, legacy=legacy)
    return current or "absent", {
        "installed_digest": current or "absent",
        "source_digest": source_digest,
        "skills": list(installed["skills"]) if current else list(SKILLS),
        "skill_count": len(installed["skills"]) if current else len(SKILLS),
        "upgrade_required": legacy,
        "snapshot_unchanged": True,
        "help_rows_validated": len(expected_rows or []),
        "hook_validated": current is not None,
        "config_validated": current is not None,
    }


def command_validate(args: argparse.Namespace,
                     project_root: Path) -> tuple[str, dict[str, Any]]:
    if args.answers is not None or args.expected_install_digest is not None:
        raise SetupError("validate rejects mutation options", code="READ_ONLY_OPTION")
    source = (Path(os.path.abspath(args.skills_source))
              if args.skills_source else None)
    before = _capture_validation_state(project_root, source)
    semantic_error: SetupError | None = None
    revision = "absent"
    result: dict[str, Any] = {}
    try:
        revision, result = _validate_captured_state(before)
    except SetupError as exc:
        semantic_error = exc
    after = _capture_validation_state(project_root, source)
    if before != after:
        raise SetupError("targets changed during read-only validation",
                         code="VALIDATE_CONFLICT", exit_code=4)
    if semantic_error is not None:
        raise semantic_error
    return revision, result


def parser() -> JsonParser:
    result = JsonParser(description="Install or validate the native SDM module")
    result.add_argument("command", choices=("install", "update", "uninstall",
                                            "recover", "validate"))
    result.add_argument("--project-root", required=True)
    result.add_argument("--answers")
    result.add_argument("--skills-source")
    result.add_argument("--expected-install-digest")
    return result


def main(argv: list[str] | None = None) -> int:
    command = "setup"
    try:
        args = parser().parse_args(argv)
        command = f"setup.{args.command}"
        project_root = Path(args.project_root).resolve(strict=True)
        if args.command == "validate":
            revision, result = command_validate(args, project_root)
        else:
            # Reject retired or malformed configuration before creating or
            # changing even the installer coordination lock.
            if args.command in ("install", "update"):
                load_answers(args.answers)
            with host_lock(project_root) as host:
                if args.command == "recover":
                    if any(value is not None for value in
                           (args.answers, args.skills_source,
                            args.expected_install_digest)):
                        raise SetupError("recover accepts only --project-root",
                                         code="CLI_USAGE")
                    revision, result = command_recover(
                        project_root, host=host,
                    )
                else:
                    revision, result = command_mutation(
                        args, project_root, host=host,
                    )
        result = {**result, "installed_digest": revision}
        emit(command, status="pass", revision=None, result=result)
        return 0
    except SetupError as exc:
        status = {2: "fail", 3: "blocked", 4: "conflict",
                  5: "configuration_required"}.get(exc.exit_code,
                                                     "internal_error")
        emit(command, status=status, findings=[finding(exc)])
        return exc.exit_code
    except Exception as exc:
        error = SetupError(f"internal setup failure: {type(exc).__name__}: {exc}",
                           code="SETUP_INTERNAL", exit_code=70)
        sys.stderr.write(str(exc) + "\n")
        emit(command, status="internal_error", findings=[finding(error)])
        return 70


if __name__ == "__main__":
    sys.exit(main())
