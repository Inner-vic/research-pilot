#!/usr/bin/env python3
"""Authoritative read-only SDM source verifier."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import math
import os
import pwd
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple
from urllib.parse import urlsplit, urlunsplit

sys.dont_write_bytecode = True

SKILL_NAMES = (
    "sdm-setup", "sdm-lifecycle", "sdm-ml-readiness",
    "sdm-create-ml-contract", "sdm-lock-baseline",
    "sdm-create-experiment-spec", "sdm-freeze-experiment",
    "sdm-build-context-pack", "sdm-evidence-review",
    "sdm-scientific-decision",
    "sdm-agent-research-planner", "sdm-agent-experiment-spec",
    "sdm-agent-geometry-contract", "sdm-agent-evidence-review",
)
AGENT_NAMES = tuple(name for name in SKILL_NAMES if name.startswith("sdm-agent-"))

# These command exits cannot honestly be exercised by the ordinary parametrized
# command-contract test: doing so would recurse into this verifier.  Keep the
# exceptional surface closed and bind every exception to one concrete,
# non-recursive evidence path.
AUTHORITATIVE_TARGET_OVERRIDES = {
    ("native-eval.run", 0): ("same-run:native-eval.run:0",),
    ("native-eval.run", 4): (
        "pytest:tests/test_authoritative_command_paths.py::"
        "test_native_protected_root_conflict_returns_exit_4",
    ),
    ("verify-orchestrator.run", 0): ("outer:verify-orchestrator.run:0",),
    ("verify-orchestrator.run", 3): (
        "pytest:tests/test_authoritative_command_paths.py::"
        "test_orchestrator_native_unavailable_maps_to_exit_3",
    ),
    ("verify-orchestrator.run", 4): (
        "pytest:tests/test_authoritative_command_paths.py::"
        "test_orchestrator_protected_root_change_maps_to_exit_4",
    ),
    ("verify-launcher.run", 0): ("outer:verify-launcher.run:0",),
    ("verify-launcher.run", 4): (
        "pytest:tests/test_authoritative_command_paths.py::"
        "test_launcher_wraps_child_conflict_as_exit_4",
    ),
    ("verify-launcher.run", 70): (
        "pytest:tests/test_authoritative_command_paths.py::"
        "test_launcher_protocol_failure_returns_exit_70",
    ),
}

DEFERRED_OUTER_COVERAGE = {
    "CLI-verify-launcher-run-0": ["outer:verify-launcher.run:0"],
    "CLI-verify-orchestrator-run-0": ["outer:verify-orchestrator.run:0"],
}

RESULT_STATUS_BY_EXIT = {
    0: "pass", 2: "fail", 3: "blocked", 4: "conflict",
    5: "configuration_required", 70: "internal_error",
}
MAX_CHILD_OUTPUT_BYTES = 16_777_216
MAX_DOCUMENT_BYTES = 16_777_216
MAX_CANONICAL_BYTES = 16_777_216
MAX_NUMBER_TOKEN_BYTES = 256
MAX_SIGNIFICANT_DIGITS = 128
MAX_ABS_DECIMAL_EXPONENT = 4096
MAX_DOCUMENT_DEPTH = 128
MAX_DOCUMENT_NODES = 1_000_000
MAX_TREE_NODES = 100_000
MAX_TREE_BYTES = 134_217_728
MAX_TREE_FILE_BYTES = 16_777_216
MAX_TREE_RETAINED_FDS = 32
MIN_I64 = -(2**63)
MAX_I64 = 2**63 - 1
VALIDATOR_TIMEOUT_SECONDS = 900
PYTEST_TIMEOUT_SECONDS = 10_800
NATIVE_TIMEOUT_SECONDS = 7_200
TERMINATION_GRACE_SECONDS = 5
UV_CANDIDATE_TEMPLATES = (
    "{passwd-home}/.local/bin/uv",
    "/usr/local/bin/uv",
    "/usr/bin/uv",
    "/sdm-tools/uv",
)
SENSITIVE_KEY_PARTS = (
    "authorization", "proxyauthorization", "cookie", "setcookie",
    "apikey", "accesskey", "secretkey", "sessionkey", "token",
    "password", "passwd", "privatekey", "credential", "signedurl",
    "hiddenlabel", "evaluatorsource", "evaluatorcontent",
)


class PinnedUV(NamedTuple):
    path: Path
    digest: str
    version: str
    fd: int
    identity: tuple[int, ...]


class VerifyError(Exception):
    def __init__(self, message: str, *, code: str = "VERIFY_FAILED",
                 exit_code: int = 2, subject: str = "verification") -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.subject = subject


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise VerifyError(message, code="CLI_USAGE", exit_code=2,
                          subject="verify-orchestrator")


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _parse_int(value: str) -> int:
    if len(value.encode("ascii", "strict")) > MAX_NUMBER_TOKEN_BYTES:
        raise VerifyError("number token exceeds the fixed byte limit",
                          code="NUMBER_TOKEN_TOO_LONG")
    digits = value.lstrip("+-")
    if len(digits) > 19:
        raise VerifyError("integer is outside signed 64-bit range",
                          code="INTEGER_OUT_OF_RANGE")
    try:
        parsed = int(value, 10)
    except (ValueError, OverflowError) as exc:
        raise VerifyError("invalid integer token", code="INVALID_NUMBER") from exc
    if parsed < MIN_I64 or parsed > MAX_I64:
        raise VerifyError("integer is outside signed 64-bit range",
                          code="INTEGER_OUT_OF_RANGE")
    return parsed


def _parse_float(value: str) -> Decimal:
    if len(value.encode("ascii", "strict")) > MAX_NUMBER_TOKEN_BYTES:
        raise VerifyError("number token exceeds the fixed byte limit",
                          code="NUMBER_TOKEN_TOO_LONG")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise VerifyError("invalid decimal token", code="INVALID_NUMBER") from exc
    if not parsed.is_finite():
        raise VerifyError("non-finite number is forbidden", code="NON_FINITE")
    _, digits, exponent = parsed.as_tuple()
    if len(digits) > MAX_SIGNIFICANT_DIGITS:
        raise VerifyError("number exceeds the fixed significant-digit limit",
                          code="NUMBER_PRECISION_EXCEEDED")
    adjusted = parsed.adjusted() if parsed else 0
    if (abs(exponent) > MAX_ABS_DECIMAL_EXPONENT
            or abs(adjusted) > MAX_ABS_DECIMAL_EXPONENT):
        raise VerifyError("number exceeds the fixed decimal-exponent limit",
                          code="NUMBER_EXPONENT_EXCEEDED")
    return parsed


def _preflight_json(text: str) -> None:
    depth = 0
    structural_tokens = 1
    quote = False
    escaped = False
    for character in text:
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quote = False
            continue
        if character == '"':
            quote = True
        elif character in "[{":
            depth += 1
            structural_tokens += 1
            if depth > MAX_DOCUMENT_DEPTH:
                raise VerifyError("JSON exceeds the fixed nesting-depth limit",
                                  code="DOCUMENT_DEPTH_EXCEEDED")
        elif character in "]}":
            depth = max(0, depth - 1)
        elif character in ",:":
            structural_tokens += 1
            if structural_tokens > MAX_DOCUMENT_NODES * 2:
                raise VerifyError("JSON exceeds the fixed node limit",
                                  code="DOCUMENT_NODES_EXCEEDED")


def _validate_json_model(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_DOCUMENT_NODES:
            raise VerifyError("JSON exceeds the fixed node limit",
                              code="DOCUMENT_NODES_EXCEEDED")
        if depth > MAX_DOCUMENT_DEPTH:
            raise VerifyError("JSON exceeds the fixed nesting-depth limit",
                              code="DOCUMENT_DEPTH_EXCEEDED")
        if item is None or isinstance(item, bool):
            continue
        if isinstance(item, int):
            if item < MIN_I64 or item > MAX_I64:
                raise VerifyError("integer is outside signed 64-bit range",
                                  code="INTEGER_OUT_OF_RANGE")
            continue
        if isinstance(item, Decimal):
            _parse_float(str(item))
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                raise VerifyError("non-finite number is forbidden",
                                  code="NON_FINITE")
            _parse_float(str(item))
            continue
        if isinstance(item, str):
            if unicodedata.normalize("NFC", item) != item:
                raise VerifyError("JSON strings must already be NFC normalized",
                                  code="NON_NFC_STRING")
            continue
        if isinstance(item, list):
            stack.extend((child, depth + 1) for child in reversed(item))
            continue
        if isinstance(item, dict):
            children: list[tuple[Any, int]] = []
            for key, child in item.items():
                if not isinstance(key, str):
                    raise VerifyError("JSON object keys must be strings",
                                      code="NON_STRING_KEY")
                children.extend(((key, depth + 1), (child, depth + 1)))
            stack.extend(reversed(children))
            continue
        raise VerifyError("value is outside the JSON data model",
                          code="UNSUPPORTED_JSON_TYPE")


def parse_json_bytes(data: bytes, subject: str) -> Any:
    """Parse one bounded strict-JSON document without binary floats."""

    if len(data) > MAX_DOCUMENT_BYTES:
        raise VerifyError("JSON exceeds the fixed document byte limit",
                          code="DOCUMENT_TOO_LARGE", subject=subject)
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise VerifyError("JSON is not strict UTF-8", code="INVALID_UTF8",
                          subject=subject) from exc
    except MemoryError as exc:
        raise VerifyError("JSON parsing exceeded a fixed resource limit",
                          code="DOCUMENT_RESOURCE_LIMIT",
                          subject=subject) from exc
    if text.startswith("\ufeff"):
        raise VerifyError("UTF-8 BOM is forbidden", code="BOM_FORBIDDEN",
                          subject=subject)

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise VerifyError("JSON contains a duplicate object key",
                                  code="DUPLICATE_KEY", subject=subject)
            result[key] = item
        return result

    def reject_constant(_value: str) -> None:
        raise VerifyError("non-finite number is forbidden", code="NON_FINITE",
                          subject=subject)

    try:
        _preflight_json(text)
        value = json.loads(
            text, object_pairs_hook=pairs, parse_int=_parse_int,
            parse_float=_parse_float, parse_constant=reject_constant,
        )
        _validate_json_model(value)
        return value
    except VerifyError as exc:
        if exc.subject == "verification":
            exc.subject = subject
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise VerifyError("malformed JSON document", code="JSON_INVALID",
                          subject=subject) from exc
    except (RecursionError, MemoryError, ValueError, OverflowError) as exc:
        raise VerifyError("JSON parsing exceeded a fixed resource limit",
                          code="DOCUMENT_RESOURCE_LIMIT",
                          subject=subject) from exc


def _stable_bounded_file(path: Path, limit: int = MAX_DOCUMENT_BYTES) -> bytes:
    try:
        path_before = path.lstat()
    except OSError as exc:
        raise VerifyError("cannot inspect JSON input", code="JSON_OPEN_FAILED",
                          subject=str(path)) from exc
    if (stat.S_ISLNK(path_before.st_mode)
            or not stat.S_ISREG(path_before.st_mode)
            or path_before.st_nlink != 1):
        raise VerifyError("JSON input is not one regular file",
                          code="UNSAFE_JSON_INPUT", subject=str(path))
    if path_before.st_size > limit:
        raise VerifyError("JSON exceeds the fixed document byte limit",
                          code="DOCUMENT_TOO_LARGE", subject=str(path))
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise VerifyError("cannot safely open JSON input",
                          code="JSON_OPEN_FAILED", subject=str(path)) from exc
    try:
        before = os.fstat(fd)
        if (_file_identity(before) != _file_identity(path_before)
                or not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1):
            raise VerifyError("JSON input identity changed before read",
                              code="JSON_INPUT_RACE", subject=str(path))
        chunks: list[bytes] = []
        length = 0
        while True:
            remaining = limit + 1 - length
            block = os.read(fd, min(1_048_576, remaining))
            if not block:
                break
            chunks.append(block)
            length += len(block)
            if length > limit:
                raise VerifyError("JSON exceeds the fixed document byte limit",
                                  code="DOCUMENT_TOO_LARGE", subject=str(path))
        if (_file_identity(os.fstat(fd)) != _file_identity(before)
                or _file_identity(path.lstat()) != _file_identity(before)):
            raise VerifyError("JSON input changed while read",
                              code="JSON_INPUT_RACE", subject=str(path))
        return b"".join(chunks)
    except OSError as exc:
        raise VerifyError("JSON input read failed", code="JSON_READ_FAILED",
                          subject=str(path)) from exc
    except MemoryError as exc:
        raise VerifyError("JSON input exceeded a fixed resource limit",
                          code="DOCUMENT_RESOURCE_LIMIT",
                          subject=str(path)) from exc
    finally:
        os.close(fd)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise VerifyError("non-finite number is forbidden", code="NON_FINITE")
    if not value:
        return "0"
    sign, digits_tuple, exponent = value.as_tuple()
    digits = list(digits_tuple)
    while len(digits) > 1 and exponent < 0 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    raw = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        rendered = raw + ("0" * exponent)
    else:
        point = len(raw) + exponent
        rendered = (raw[:point] + "." + raw[point:]) if point > 0 \
            else ("0." + ("0" * -point) + raw)
    return ("-" if sign else "") + rendered


def _encode_json(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        _parse_int(str(value))
        return str(value)
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise VerifyError("non-finite number is forbidden",
                              code="NON_FINITE")
        return _decimal_text(Decimal(str(value)))
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_encode_json(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            _encode_json(key) + ":" + _encode_json(value[key])
            for key in sorted(value)
        ) + "}"
    raise VerifyError("value is outside the JSON data model",
                      code="UNSUPPORTED_JSON_TYPE")


def canonical_json_bytes(value: Any) -> bytes:
    try:
        _validate_json_model(value)
        encoded = _encode_json(value).encode("utf-8")
    except (RecursionError, MemoryError) as exc:
        raise VerifyError("JSON encoding exceeded a fixed resource limit",
                          code="DOCUMENT_RESOURCE_LIMIT") from exc
    if len(encoded) > MAX_CANONICAL_BYTES:
        raise VerifyError("canonical JSON exceeds the fixed byte limit",
                          code="CANONICAL_TOO_LARGE")
    return encoded


def _sensitive_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _safe_redaction_digest(value: Any) -> str:
    try:
        return digest_bytes(canonical_json_bytes(value))
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
        return urlunsplit((parts.scheme, host + port, parts.path, "", ""))
    except (ValueError, UnicodeError):
        return "[redacted-uri]"


def redact(value: Any) -> Any:
    """Verifier-local exact authority redactor; never imports mutable runtime."""

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _sensitive_key(str(key)):
                result[key] = {
                    "redacted": True, "digest": _safe_redaction_digest(item),
                }
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _sanitize_uri(value)
    return value


def _redacted_stream(value: str) -> Any:
    """Preserve structured diagnostics safely; digest arbitrary prose."""

    stripped = value.strip()
    if stripped:
        try:
            return redact(parse_json_bytes(stripped.encode("utf-8"),
                                           "child-output"))
        except VerifyError:
            pass
    return {"redacted": True, "digest": digest_bytes(value.encode("utf-8"))}


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode),
        stat.S_IMODE(info.st_mode), info.st_nlink, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    )


def _fd_digest(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    hasher = hashlib.sha256()
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block:
            break
        hasher.update(block)
    return "sha256:" + hasher.hexdigest()


def open_pinned_uv(module: Path, environment: dict[str, str]) -> PinnedUV:
    """Reopen the launcher's selected UV and revalidate lock, bytes, and identity."""

    toolchain = strict_json(module / "verification-toolchain.json")
    if (not isinstance(toolchain, dict)
            or set(toolchain) != {"schema", "uv"}
            or toolchain.get("schema") != "sdm-verification-toolchain/v1"):
        raise VerifyError("verification toolchain lock is malformed",
                          code="TOOLCHAIN_INVALID")
    uv = toolchain.get("uv")
    if (not isinstance(uv, dict)
            or set(uv) != {"version", "digest", "candidates"}
            or not isinstance(uv.get("version"), str)
            or not isinstance(uv.get("digest"), str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", uv["digest"])
            or not isinstance(uv.get("candidates"), list)
            or tuple(uv["candidates"]) != UV_CANDIDATE_TEMPLATES):
        raise VerifyError("verification toolchain UV entry is malformed",
                          code="TOOLCHAIN_INVALID")
    raw_path = environment.get("SDM_PINNED_UV_PATH")
    raw_digest = environment.get("SDM_PINNED_UV_DIGEST")
    raw_version = environment.get("SDM_PINNED_UV_VERSION")
    if (not raw_path or raw_digest != uv["digest"]
            or raw_version != uv["version"]):
        raise VerifyError("launcher UV attestation is absent or mismatched",
                          code="TOOLCHAIN_INVALID")
    passwd_home = pwd.getpwuid(os.getuid()).pw_dir
    candidates = {
        candidate.replace("{passwd-home}", passwd_home)
        for candidate in uv["candidates"]
        if isinstance(candidate, str)
    }
    if raw_path not in candidates or not Path(raw_path).is_absolute():
        raise VerifyError("launcher selected an unlisted UV path",
                          code="TOOLCHAIN_INVALID")
    path = Path(raw_path)
    try:
        path_before = path.lstat()
    except OSError as exc:
        raise VerifyError("pinned UV executable is unavailable",
                          code="TOOLCHAIN_INVALID") from exc
    if (stat.S_ISLNK(path_before.st_mode)
            or not stat.S_ISREG(path_before.st_mode)
            or path_before.st_nlink != 1
            or not path_before.st_mode & 0o111):
        raise VerifyError("pinned UV executable has an unsafe identity",
                          code="TOOLCHAIN_INVALID")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        identity = _file_identity(before)
        if (identity != _file_identity(path_before)
                or _fd_digest(fd) != uv["digest"]
                or _file_identity(os.fstat(fd)) != identity
                or _file_identity(path.lstat()) != identity):
            raise VerifyError("pinned UV executable changed identity or bytes",
                              code="TOOLCHAIN_INVALID")
        return PinnedUV(path, uv["digest"], uv["version"], fd, identity)
    except Exception:
        os.close(fd)
        raise


def strict_json(path: Path) -> Any:
    return parse_json_bytes(_stable_bounded_file(path), str(path))


def _mount_id(fd: int, subject: str) -> int:
    try:
        with open(f"/proc/self/fdinfo/{fd}", "rb", buffering=0) as handle:
            data = handle.read(65_537)
    except OSError as exc:
        raise VerifyError("trustworthy mount identity is unavailable",
                          code="SOURCE_MOUNT_ID", subject=subject) from exc
    if len(data) > 65_536:
        raise VerifyError("mount identity metadata is oversized",
                          code="SOURCE_MOUNT_ID", subject=subject)
    for line in data.splitlines():
        if line.startswith(b"mnt_id:\t"):
            raw = line.removeprefix(b"mnt_id:\t")
            if raw.isdigit():
                return int(raw)
    raise VerifyError("trustworthy mount identity is unavailable",
                      code="SOURCE_MOUNT_ID", subject=subject)


def _snapshot_identity(info: os.stat_result, mount_id: int) -> tuple[int, ...]:
    return (
        info.st_dev, mount_id, info.st_ino, stat.S_IFMT(info.st_mode),
        stat.S_IMODE(info.st_mode), info.st_nlink, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    )


def tree_snapshot(root: Path) -> dict[str, tuple]:
    """Take a bounded, no-follow, descriptor-relative protected-tree snapshot."""

    try:
        root_path = root.lstat()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise VerifyError("cannot inspect protected root", code="SOURCE_READ",
                          subject=str(root)) from exc
    if stat.S_ISLNK(root_path.st_mode) or not stat.S_ISDIR(root_path.st_mode):
        raise VerifyError("protected root is not a real directory",
                          code="SOURCE_SPECIAL_NODE", subject=str(root))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC \
        | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(root, flags)
    except OSError as exc:
        raise VerifyError("cannot safely open protected root",
                          code="SOURCE_READ", subject=str(root)) from exc
    result: dict[str, tuple] = {}
    nodes = 0
    aggregate_bytes = 0
    try:
        root_mount = _mount_id(root_fd, str(root))
        root_identity = _snapshot_identity(os.fstat(root_fd), root_mount)
    except Exception:
        os.close(root_fd)
        raise
    if root_identity != _snapshot_identity(root_path, root_mount):
        os.close(root_fd)
        raise VerifyError("protected root identity changed before snapshot",
                          code="SOURCE_RACE", subject=str(root))

    def walk(directory_fd: int, parts: tuple[str, ...], depth: int) -> None:
        nonlocal nodes, aggregate_bytes
        # One slot is reserved for the fdinfo descriptor used to obtain the
        # kernel mount identity; a regular-file child consumes one more slot.
        if depth >= MAX_TREE_RETAINED_FDS - 1:
            raise VerifyError("protected tree exceeds retained-FD depth",
                              code="SOURCE_FD_LIMIT", subject="/".join(parts))
        directory_before = os.fstat(directory_fd)
        directory_mount = _mount_id(directory_fd, "/".join(parts) or str(root))
        directory_identity = _snapshot_identity(directory_before,
                                                directory_mount)
        if directory_mount != root_mount:
            raise VerifyError("protected tree crosses a mount boundary",
                              code="SOURCE_MOUNT_BOUNDARY",
                              subject="/".join(parts))
        try:
            names = os.listdir(directory_fd)
        except OSError as exc:
            raise VerifyError("cannot enumerate protected directory",
                              code="SOURCE_READ",
                              subject="/".join(parts)) from exc
        for name in sorted(names, key=lambda item: os.fsencode(item)):
            if not name or name in {".", ".."} or "/" in name or "\x00" in name:
                raise VerifyError("protected tree contains an unsafe name",
                                  code="SOURCE_PATH", subject="/".join(parts))
            try:
                name.encode("utf-8", "strict")
            except UnicodeError as exc:
                raise VerifyError("protected tree name is not strict UTF-8",
                                  code="SOURCE_PATH",
                                  subject="/".join(parts)) from exc
            if unicodedata.normalize("NFC", name) != name:
                raise VerifyError("protected tree name is not NFC",
                                  code="SOURCE_PATH",
                                  subject="/".join(parts))
            relative_parts = (*parts, name)
            relative = "/".join(relative_parts)
            nodes += 1
            if nodes > MAX_TREE_NODES:
                raise VerifyError("protected tree exceeds the fixed node limit",
                                  code="SOURCE_NODE_LIMIT", subject=relative)
            try:
                path_info = os.stat(name, dir_fd=directory_fd,
                                    follow_symlinks=False)
            except OSError as exc:
                raise VerifyError("cannot inspect protected node",
                                  code="SOURCE_READ", subject=relative) from exc
            if stat.S_ISLNK(path_info.st_mode):
                raise VerifyError("symlink rejected in source",
                                  code="SOURCE_SYMLINK", subject=relative)
            if stat.S_ISDIR(path_info.st_mode):
                try:
                    child_fd = os.open(name, flags, dir_fd=directory_fd)
                except OSError as exc:
                    raise VerifyError("cannot safely open protected directory",
                                      code="SOURCE_READ", subject=relative) from exc
                try:
                    child_mount = _mount_id(child_fd, relative)
                    child_identity = _snapshot_identity(os.fstat(child_fd),
                                                        child_mount)
                    if (child_mount != root_mount
                            or child_identity
                            != _snapshot_identity(path_info, child_mount)):
                        raise VerifyError("protected directory identity raced",
                                          code="SOURCE_RACE", subject=relative)
                    result[relative] = (
                        stat.S_IMODE(path_info.st_mode), path_info.st_size,
                        path_info.st_mtime_ns, path_info.st_ctime_ns,
                        path_info.st_dev, path_info.st_ino, None,
                    )
                    walk(child_fd, relative_parts, depth + 1)
                    after_path = os.stat(name, dir_fd=directory_fd,
                                         follow_symlinks=False)
                    if (_snapshot_identity(os.fstat(child_fd), child_mount)
                            != child_identity
                            or _snapshot_identity(after_path, child_mount)
                            != child_identity):
                        raise VerifyError("protected directory changed during snapshot",
                                          code="SOURCE_RACE", subject=relative)
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(path_info.st_mode):
                raise VerifyError("special node rejected in source",
                                  code="SOURCE_SPECIAL_NODE", subject=relative)
            if path_info.st_nlink != 1:
                raise VerifyError("hardlink rejected in source",
                                  code="SOURCE_HARDLINK", subject=relative)
            if path_info.st_size > MAX_TREE_FILE_BYTES:
                raise VerifyError("source file exceeds the fixed byte limit",
                                  code="SOURCE_FILE_LIMIT", subject=relative)
            aggregate_bytes += path_info.st_size
            if aggregate_bytes > MAX_TREE_BYTES:
                raise VerifyError("protected tree exceeds the aggregate byte limit",
                                  code="SOURCE_BYTE_LIMIT", subject=relative)
            file_flags = os.O_RDONLY | os.O_CLOEXEC \
                | getattr(os, "O_NOFOLLOW", 0)
            try:
                file_fd = os.open(name, file_flags, dir_fd=directory_fd)
            except OSError as exc:
                raise VerifyError("cannot safely open protected file",
                                  code="SOURCE_READ", subject=relative) from exc
            try:
                file_mount = _mount_id(file_fd, relative)
                file_before = os.fstat(file_fd)
                file_identity = _snapshot_identity(file_before, file_mount)
                if (file_mount != root_mount
                        or file_identity != _snapshot_identity(path_info, file_mount)):
                    raise VerifyError("protected file identity raced",
                                      code="SOURCE_RACE", subject=relative)
                hasher = hashlib.sha256()
                observed = 0
                while True:
                    block = os.read(file_fd, 1_048_576)
                    if not block:
                        break
                    observed += len(block)
                    if observed > MAX_TREE_FILE_BYTES:
                        raise VerifyError("source file exceeds the fixed byte limit",
                                          code="SOURCE_FILE_LIMIT", subject=relative)
                    if aggregate_bytes - file_before.st_size + observed \
                            > MAX_TREE_BYTES:
                        raise VerifyError(
                            "protected tree exceeds the aggregate byte limit",
                            code="SOURCE_BYTE_LIMIT", subject=relative,
                        )
                    hasher.update(block)
                after_path = os.stat(name, dir_fd=directory_fd,
                                     follow_symlinks=False)
                if (observed != file_before.st_size
                        or _snapshot_identity(os.fstat(file_fd), file_mount)
                        != file_identity
                        or _snapshot_identity(after_path, file_mount)
                        != file_identity):
                    raise VerifyError("protected file changed during snapshot",
                                      code="SOURCE_RACE", subject=relative)
                result[relative] = (
                    stat.S_IMODE(path_info.st_mode), path_info.st_size,
                    path_info.st_mtime_ns, path_info.st_ctime_ns,
                    path_info.st_dev, path_info.st_ino,
                    "sha256:" + hasher.hexdigest(),
                )
            finally:
                os.close(file_fd)
        if _snapshot_identity(os.fstat(directory_fd), directory_mount) \
                != directory_identity:
            raise VerifyError("protected directory changed during snapshot",
                              code="SOURCE_RACE",
                              subject="/".join(parts) or str(root))

    try:
        walk(root_fd, (), 1)
        if (_snapshot_identity(os.fstat(root_fd), root_mount) != root_identity
                or _snapshot_identity(root.lstat(), root_mount) != root_identity):
            raise VerifyError("protected root changed during snapshot",
                              code="SOURCE_RACE", subject=str(root))
        return result
    except OSError as exc:
        raise VerifyError("protected tree snapshot failed", code="SOURCE_READ",
                          subject=str(root)) from exc
    finally:
        os.close(root_fd)


def source_digest(root: Path) -> str:
    hasher = hashlib.sha256(b"sdm-source-v1\0")
    for relative, values in tree_snapshot(root).items():
        hasher.update(relative.encode("utf-8") + b"\0")
        hasher.update(int(values[0]).to_bytes(4, "big"))
        if values[-1]:
            hasher.update(values[-1].encode("ascii"))
    return "sha256:" + hasher.hexdigest()


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    process.poll()
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=TERMINATION_GRACE_SECONDS)
        return
    deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
    while time.monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=TERMINATION_GRACE_SECONDS)


def run(command: list[str], *, cwd: Path, env: dict[str, str], log: Path,
        expected=(0,), timeout: int = VALIDATOR_TIMEOUT_SECONDS,
        pinned_uv: PinnedUV | None = None) -> subprocess.CompletedProcess[str]:
    """Run a verifier child with bounded output and whole-group teardown."""

    logical_command = list(command)
    pass_fds: tuple[int, ...] = ()
    if command and command[0] == "uv":
        if pinned_uv is None:
            raise VerifyError("UV child lacks a pinned executable",
                              code="TOOLCHAIN_INVALID", subject="uv")
        if (_file_identity(os.fstat(pinned_uv.fd)) != pinned_uv.identity
                or _file_identity(pinned_uv.path.lstat()) != pinned_uv.identity
                or _fd_digest(pinned_uv.fd) != pinned_uv.digest):
            raise VerifyError("pinned UV changed before child execution",
                              code="TOOLCHAIN_INVALID", subject="uv")
        command = [f"/proc/self/fd/{pinned_uv.fd}", *command[1:]]
        pass_fds = (pinned_uv.fd,)
    started = time.monotonic()
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, pass_fds=pass_fds,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    observed = 0
    timed_out = False
    output_limited = False
    deadline = started + timeout
    try:
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _terminate_process_group(process)
                break
            events = selector.select(min(0.25, remaining)) \
                if selector.get_map() else []
            if not events and not selector.get_map():
                time.sleep(min(0.05, remaining))
            if not events and process.poll() is not None:
                events = [(key, selectors.EVENT_READ)
                          for key in list(selector.get_map().values())]
            for key, _ in events:
                try:
                    block = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                observed += len(block)
                retained = sum(len(value) for value in captured.values())
                room = max(0, MAX_CHILD_OUTPUT_BYTES - retained)
                captured[key.data].extend(block[:room])
                if observed > MAX_CHILD_OUTPUT_BYTES:
                    output_limited = True
                    _terminate_process_group(process)
                    break
            if output_limited:
                break
        if process.poll() is None:
            process.wait(timeout=TERMINATION_GRACE_SECONDS)
        _terminate_process_group(process)
    finally:
        selector.close()
        if process.poll() is None:
            _terminate_process_group(process)
        process.stdout.close()
        process.stderr.close()
    stdout_bytes = bytes(captured["stdout"])
    stderr_bytes = bytes(captured["stderr"])
    invalid_utf8 = False
    try:
        stdout = stdout_bytes.decode("utf-8", errors="strict")
        stderr = stderr_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        invalid_utf8 = True
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
    completed = subprocess.CompletedProcess(
        logical_command, process.returncode, stdout, stderr,
    )
    artifact = {
        "argv_digest": digest_bytes(canonical_json_bytes(logical_command)),
        "argv_count": len(logical_command),
        "executable": redact(logical_command[0] if logical_command else ""),
        "return_code": completed.returncode,
        "stdout": _redacted_stream(stdout),
        "stderr": _redacted_stream(stderr),
        "stdout_digest": digest_bytes(stdout_bytes),
        "stderr_digest": digest_bytes(stderr_bytes),
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "timeout_seconds": timeout, "timed_out": timed_out,
        "output_limited": output_limited,
        "output_bytes_observed": observed,
    }
    log.write_bytes(canonical_json_bytes(redact(artifact)) + b"\n")
    if timed_out:
        raise VerifyError(
            f"command exceeded its {timeout}s deadline",
            code="SUBPROCESS_TIMEOUT", subject=logical_command[0],
        )
    if output_limited:
        raise VerifyError(
            "command exceeded its fixed output budget",
            code="SUBPROCESS_OUTPUT_LIMIT", subject=logical_command[0],
        )
    if invalid_utf8:
        raise VerifyError(
            "command emitted non-UTF-8 output",
            code="SUBPROCESS_UTF8", subject=logical_command[0],
        )
    if completed.returncode not in expected:
        raise VerifyError(
            f"command failed with exit {completed.returncode}",
            code="SUBPROCESS_FAILED", subject=logical_command[0],
        )
    return completed


def parse_one_json(completed: subprocess.CompletedProcess, subject: str) -> dict[str, Any]:
    try:
        value = parse_json_bytes(completed.stdout.strip().encode("utf-8"),
                                 subject)
    except VerifyError as exc:
        raise VerifyError(f"{subject} emitted invalid JSON",
                          code="SUBPROCESS_PROTOCOL", subject=subject) from exc
    if not isinstance(value, dict):
        raise VerifyError(f"{subject} result is not an object",
                          code="SUBPROCESS_PROTOCOL", subject=subject)
    return value


def require_native_success(completed: subprocess.CompletedProcess,
                           payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the real native child envelope and return its summary.

    This helper is deliberately shared by the orchestrator and its bounded
    exit-path tests, so those tests exercise the production classification
    without recursively launching the verifier.
    """
    return_code = completed.returncode
    expected_status = RESULT_STATUS_BY_EXIT.get(return_code)
    required_keys = {"status", "command", "revision", "result", "findings"}
    allowed_keys = required_keys | {"extensions"}
    findings = payload.get("findings")
    if (expected_status is None or not required_keys.issubset(payload)
            or set(payload) - allowed_keys
            or payload.get("command") != "native-eval.run"
            or payload.get("status") != expected_status
            or payload.get("revision") is not None
            or not isinstance(payload.get("result"), dict)
            or not isinstance(findings, list)):
        raise VerifyError("native evaluator emitted an invalid result envelope",
                          code="NATIVE_PROTOCOL", exit_code=70,
                          subject="native-eval")
    if return_code == 0:
        if findings:
            raise VerifyError("passing native evaluator emitted findings",
                              code="NATIVE_PROTOCOL", exit_code=70,
                              subject="native-eval")
        return payload["result"]
    if not findings or not isinstance(findings[0], dict):
        raise VerifyError("failing native evaluator omitted its finding",
                          code="NATIVE_PROTOCOL", exit_code=70,
                          subject="native-eval")
    first = findings[0]
    finding_code = first.get("code")
    if not isinstance(finding_code, str) or not finding_code:
        finding_code = "NATIVE_GATE"
    propagated_exit = return_code if return_code in {3, 4, 70} else 2
    raise VerifyError("native evaluation gate failed", code=finding_code,
                      exit_code=propagated_exit,
                      subject="native-eval")


def require_protected_roots_unchanged(
    before: dict[str, dict[str, tuple]],
    after: dict[str, dict[str, tuple]],
) -> None:
    """Map a real protected-tree snapshot conflict to orchestrator exit 4."""
    if before != after:
        raise VerifyError("verification changed protected source/project roots",
                          code="READ_ONLY_CONFLICT", exit_code=4)


def counted_findings(value: Any) -> int:
    total = 0
    if isinstance(value, dict):
        summary = value.get("summary")
        if isinstance(summary, dict) and isinstance(summary.get("by_severity"), dict):
            total += sum(int(summary["by_severity"].get(key, 0) or 0)
                         for key in ("critical", "high", "medium", "low"))
        elif isinstance(value.get("findings"), list):
            total += sum(1 for item in value["findings"] if isinstance(item, dict)
                         and item.get("severity") in {"critical", "high", "medium", "low"})
    return total


def validate_owned_asset_links(module: Path) -> dict[str, Any]:
    module = Path(module)
    try:
        lexical_module = Path(os.path.abspath(os.fspath(module)))
        resolved_module = module.resolve(strict=True)
        module_info = module.lstat()
    except (OSError, RuntimeError) as exc:
        raise VerifyError(f"cannot resolve owned-asset module root: {exc}",
                          code="OWNED_ASSET_PATH") from exc
    if (stat.S_ISLNK(module_info.st_mode)
            or not stat.S_ISDIR(module_info.st_mode)
            or lexical_module != resolved_module):
        raise VerifyError("owned-asset module root must be a real directory",
                          code="OWNED_ASSET_PATH")
    module = resolved_module
    # This helper is also called directly by integration validation, outside
    # main()'s protected-root snapshot.  Apply the same fail-closed source-node
    # policy before executing the module-local canonicalizer or reading catalogs.
    tree_snapshot(module)
    canonical_path = module / "sdm-lifecycle" / "scripts" / "canonical.py"
    canonical_spec = importlib.util.spec_from_file_location(
        "_sdm_verify_owned_asset_canonical", canonical_path)
    if canonical_spec is None or canonical_spec.loader is None:
        raise VerifyError("cannot load owned-asset canonicalizer",
                          code="OWNED_ASSET_DIGEST")
    canonical_module = importlib.util.module_from_spec(canonical_spec)
    try:
        canonical_spec.loader.exec_module(canonical_module)
    except Exception as exc:
        raise VerifyError(f"cannot load owned-asset canonicalizer: {exc}",
                          code="OWNED_ASSET_DIGEST") from exc
    declared_schemas: set[str] = set()
    declared_templates: set[str] = set()
    checked = 0
    for catalog_name in ("artifact-catalog.json",
                         "supporting-artifact-catalog.json"):
        catalog = strict_json(module / "sdm-lifecycle" / "assets" /
                              catalog_name)
        known_ids = {row["id"] for row in catalog["artifacts"]}
        for key, destination in (("owned_schema_bindings", declared_schemas),
                                 ("owned_template_bindings", declared_templates)):
            for row in catalog.get(key, []):
                relative = PurePosixPath(row["path"])
                if (relative.is_absolute() or not relative.parts or
                        any(part in ("", ".", "..") for part in relative.parts)):
                    raise VerifyError(f"unsafe owned asset path: {row['path']}",
                                      code="OWNED_ASSET_PATH")
                if relative.parts[0] != row["owner_skill"]:
                    raise VerifyError(f"owned asset crosses declared Skill: {row['path']}",
                                      code="OWNED_ASSET_PATH")
                if not set(row["artifact_ids"]).issubset(known_ids):
                    raise VerifyError(f"owned asset names unknown artifacts: {row['path']}",
                                      code="OWNED_ASSET_IDS")
                target = module.joinpath(*relative.parts)
                resolved = target.resolve(strict=True)
                if not resolved.is_relative_to(module) or not resolved.is_file():
                    raise VerifyError(f"owned asset escapes module: {row['path']}",
                                      code="OWNED_ASSET_PATH")
                source = _stable_bounded_file(resolved)
                if key == "owned_schema_bindings":
                    try:
                        source = canonical_module.canonical_bytes(
                            canonical_module.parse_document(source))
                    except Exception as exc:
                        raise VerifyError(
                            f"owned schema cannot be canonicalized: {row['path']}",
                            code="OWNED_ASSET_DIGEST",
                            subject=row["path"],
                        ) from exc
                if digest_bytes(source) != row["digest"]:
                    raise VerifyError(f"owned asset digest mismatch: {row['path']}",
                                      code="OWNED_ASSET_DIGEST")
                destination.add(relative.as_posix())
                checked += 1
    discovered_schemas = {
        path.relative_to(module).as_posix()
        for path in module.glob("sdm-*/assets/*.schema.json")
        if path.parts[-3] != "sdm-lifecycle"
    }
    discovered_templates = {
        path.relative_to(module).as_posix()
        for path in module.glob("sdm-*/assets/*.template.json")
    }
    if declared_schemas != discovered_schemas:
        raise VerifyError("workflow schema bindings are incomplete or extra",
                          code="OWNED_SCHEMA_INVENTORY")
    if declared_templates != discovered_templates:
        raise VerifyError("workflow template bindings are incomplete or extra",
                          code="OWNED_TEMPLATE_INVENTORY")
    return {"status": "pass", "checked": checked, "findings": [],
            "summary": {"by_severity": {
                "critical": 0, "high": 0, "medium": 0, "low": 0}}}


def _pinned_relative(value: str) -> Path:
    path = PurePosixPath(value.removeprefix("{project-root}/"))
    if (path.is_absolute() or not path.parts or
            any(part in ("", ".", "..") for part in path.parts)):
        raise VerifyError(f"unsafe pinned dependency path: {value}",
                          code="DEPENDENCY_PATH")
    return Path(*path.parts)


def _manifest_digest(root: Path) -> tuple[str, int]:
    snapshot = tree_snapshot(root)
    files = [(relative, values[-1]) for relative, values in snapshot.items()
             if values[-1] is not None]
    lines = bytearray()
    for relative, payload in sorted(files, key=lambda item: item[0].encode()):
        lines.extend(payload.removeprefix("sha256:").encode("ascii"))
        lines.extend(b"  ./" + relative.encode("utf-8") + b"\n")
    return "sha256:" + hashlib.sha256(lines).hexdigest(), len(files)


def validate_bmad_dependencies(project: Path, module: Path) -> dict[str, Any]:
    document = strict_json(module / "sdm-lifecycle" / "assets" /
                           "bmad-dependencies.json")
    required = {
        "bmad-product-brief", "bmad-prd", "bmad-architecture",
        "bmad-create-epics-and-stories", "bmad-sprint-planning", "bmad-build",
        "bmad-code-review", "bmad-qa-generate-e2e-tests", "bmad-retrospective",
    }
    if {row["name"] for row in document["skills"]} != required:
        raise VerifyError("pinned BMad Skill inventory mismatch",
                          code="DEPENDENCY_INVENTORY")
    checked = 0
    for row in [*document["skills"], *document["runtime_support_roots"]]:
        value = row.get("root")
        root = project / _pinned_relative(value)
        actual_digest, actual_count = _manifest_digest(root)
        if (actual_digest, actual_count) != (row["tree_digest"], row["file_count"]):
            raise VerifyError(f"pinned dependency tree mismatch: {value}",
                              code="DEPENDENCY_DIGEST")
        checked += 1
    for row in document["runtime_support_files"]:
        path = project / _pinned_relative(row["path"])
        if (not path.is_file() or path.is_symlink() or
                digest_bytes(_stable_bounded_file(path)) != row["digest"]):
            raise VerifyError(f"pinned runtime file mismatch: {row['path']}",
                              code="DEPENDENCY_DIGEST")
        if row.get("stage_alias"):
            _pinned_relative(row["stage_alias"])
        checked += 1
    return {"status": "pass", "checked": checked, "findings": [],
            "summary": {"by_severity": {
                "critical": 0, "high": 0, "medium": 0, "low": 0}}}


def collect_cases(module: Path) -> tuple[dict[str, dict[str, Any]], str]:
    cases: dict[str, dict[str, Any]] = {}
    canonical = []
    for skill in SKILL_NAMES:
        path = module / skill / "evals" / "cases.json"
        if not path.exists():
            if skill == "sdm-setup":
                continue
            raise VerifyError(f"missing eval cases: {skill}", code="EVAL_MISSING",
                              subject=skill)
        data = strict_json(path)
        rows = data["cases"] if isinstance(data, dict) else data
        for case in rows:
            case_id = case["id"]
            stable = case_id if case_id.startswith("EVAL-") else f"EVAL-{skill}-{case_id}"
            if stable in cases:
                raise VerifyError(f"duplicate eval ID {stable}", code="EVAL_DUPLICATE")
            behavior_targets = case.get("behavior_targets")
            if (not isinstance(behavior_targets, list) or not behavior_targets
                    or any(not isinstance(target, str) or not target.startswith(("pytest:", "native:"))
                           for target in behavior_targets)):
                raise VerifyError(
                    f"eval case {stable} requires explicit pytest/native behavior targets",
                    code="EVAL_BEHAVIOR_TARGET", subject=stable)
            if any("tests/test_eval_contracts.py::" in target for target in behavior_targets):
                raise VerifyError(
                    f"eval case {stable} cannot bind its structure check as behavior",
                    code="EVAL_BEHAVIOR_TARGET", subject=stable)
            cases[stable] = {
                "skill": skill,
                "case_id": case_id,
                "behavior_targets": tuple(behavior_targets),
            }
            canonical.append({"id": stable, "case": case})
    native = strict_json(module / "tests" / "native" / "trigger-queries.json")
    canonical.append({"native": native})
    return cases, digest_bytes(canonical_json_bytes(canonical))


def validate_token_result(payload: dict[str, Any], skill: str) -> None:
    if payload.get("tokens", 999999) >= 3000 or payload.get("method") != "tiktoken":
        raise VerifyError(f"{skill} SKILL.md must be under 3000 tiktoken tokens",
                          code="SKILL_TOKEN_LIMIT", subject=skill)


def run_validators(project: Path, module: Path, run_dir: Path,
                   env: dict[str, str], pinned_uv: PinnedUV) -> list[dict[str, Any]]:
    results = [validate_owned_asset_links(module),
               validate_bmad_dependencies(project, module)]
    builder = project / ".agents" / "skills"
    module_validator = builder / "bmad-module-builder" / "scripts" / "validate-module.py"
    completed = run([sys.executable, "-B", str(module_validator), str(module)],
                    cwd=project, env=env, log=run_dir / "validator-module.json",
                    pinned_uv=pinned_uv)
    payload = parse_one_json(completed, "module-validator")
    if payload.get("status") != "pass" or counted_findings(payload):
        raise VerifyError("module validator reported findings",
                          code="MODULE_VALIDATOR_FINDINGS")
    results.append(payload)

    schema_script = module / "tests" / "validate_schemas.py"
    completed = run(["uv", "run", "--with", "jsonschema==4.26.0",
                     str(schema_script), "--module-root", str(module)],
                    cwd=project, env=env, log=run_dir / "validator-schemas.json",
                    pinned_uv=pinned_uv)
    payload = parse_one_json(completed, "schema-validator")
    if payload.get("status") != "pass" or payload.get("findings"):
        raise VerifyError("schema validator reported findings",
                          code="SCHEMA_VALIDATOR_FINDINGS")
    results.append(payload)

    workflow_scripts = builder / "bmad-workflow-builder" / "scripts"
    agent_scripts = builder / "bmad-agent-builder" / "scripts"
    for skill in SKILL_NAMES:
        skill_dir = module / skill
        is_agent = skill in AGENT_NAMES
        scripts = agent_scripts if is_agent else workflow_scripts
        commands = []
        if is_agent:
            commands.append(["uv", "run", "--with", "tiktoken==0.12.0",
                             str(scripts / "prepass.py"), str(skill_dir)])
        else:
            commands.extend([
                [sys.executable, "-B", str(scripts / "quick_validate.py"), str(skill_dir)],
                [sys.executable, "-B", str(scripts / "prepass-prompt-metrics.py"), str(skill_dir)],
                [sys.executable, "-B", str(scripts / "prepass-workflow-integrity.py"), str(skill_dir)],
            ])
        commands.extend([
            [sys.executable, "-B", str(scripts / "scan-path-standards.py"), str(skill_dir)],
            [sys.executable, "-B", str(scripts / "scan-scripts.py"), str(skill_dir)],
        ])
        for index, command in enumerate(commands):
            completed = run(command, cwd=project, env=env,
                            log=run_dir / f"validator-{skill}-{index}.json",
                            pinned_uv=pinned_uv)
            payload = parse_one_json(completed, f"{skill}-validator-{index}")
            if counted_findings(payload):
                raise VerifyError(f"validator findings for {skill}",
                                  code="SKILL_VALIDATOR_FINDINGS", subject=skill)
            if is_agent and index == 0:
                if payload.get("agent_type") != "stateless" or not payload.get("skill_md_tokens"):
                    raise VerifyError(f"invalid Agent prepass for {skill}",
                                      code="AGENT_PREPASS", subject=skill)
            results.append(payload)
        count_script = scripts / "count_tokens.py"
        completed = run(["uv", "run", "--with", "tiktoken==0.12.0",
                         str(count_script), str(skill_dir / "SKILL.md")],
                        cwd=project, env=env,
                        log=run_dir / f"tokens-{skill}.json",
                        pinned_uv=pinned_uv)
        payload = parse_one_json(completed, f"{skill}-tokens")
        validate_token_result(payload, skill)
        results.append(payload)
    return results


def expected_coverage(module: Path, eval_cases: dict[str, dict[str, Any]]) -> set[str]:
    expected = {f"IO{i:02d}" for i in range(1, 9)}
    expected |= {"IO03A", "IO03B", "IO03C"}
    expected |= {f"IO04{suffix}" for suffix in "ABCDEFGHIJ"}
    expected |= {f"AC{i:02d}" for i in range(1, 20)}
    expected |= {f"STEP{i:02d}" for i in range(1, 22)}
    expected |= {f"NEG{i:02d}" for i in range(1, 19)}
    expected -= {"IO07", "AC07", "AC14", "NEG14", "NEG16"}
    expected |= {f"SCOPE{i:02d}" for i in range(1, 11)}
    native = strict_json(module / "tests" / "native" / "trigger-queries.json")
    expected |= {case["id"] for case in native["cases"]}
    expected |= set(eval_cases)
    expected |= {"CLI-native-3", "CLI-runtime-config-5"}
    contracts = strict_json(module / "sdm-lifecycle" / "assets" /
                            "command-contracts.json")
    rows = contracts.get("commands", contracts)
    if isinstance(rows, dict):
        rows = list(rows.values())
    for row in rows:
        normalized = row["name"].replace(".", "-")
        expected.update(f"CLI-{normalized}-{code}"
                        for code in row["handled_exit_codes"])
    return expected


def expanded_coverage(manifest: dict[str, Any]) -> dict[str, list[str]]:
    raw = manifest.get("coverage")
    if isinstance(raw, dict):
        coverage = {
            stable: (value.get("targets", []) if isinstance(value, dict) else value)
            for stable, value in raw.items()
        }
    elif isinstance(raw, list):
        coverage = {row["id"]: row["targets"] for row in raw}
    else:
        raise VerifyError("verification manifest requires coverage rows",
                          code="COVERAGE_MANIFEST")
    for row in manifest.get("lifecycle_rows", []):
        if row["id"] in coverage:
            raise VerifyError(f"duplicate coverage ID {row['id']}",
                              code="COVERAGE_MANIFEST")
        coverage[row["id"]] = [row["target"]]
    command_rows = manifest.get("command_exit_coverage", [])
    if not isinstance(command_rows, list):
        raise VerifyError("command_exit_coverage must be a list",
                          code="COVERAGE_MANIFEST")
    actual_overrides: dict[tuple[str, int], tuple[str, ...]] = {}
    for row in command_rows:
        if not isinstance(row, dict):
            raise VerifyError("command exit coverage row must be an object",
                              code="COVERAGE_MANIFEST")
        command = row.get("command")
        exits = row.get("exits")
        template = row.get("target_template")
        overrides = row.get("target_overrides", {})
        if not isinstance(command, str) or not command.strip():
            raise VerifyError("command exit coverage requires a command",
                              code="COVERAGE_MANIFEST")
        if (not isinstance(exits, list) or not exits
                or any(not isinstance(code, int) or isinstance(code, bool)
                       for code in exits)
                or len(exits) != len(set(exits))):
            raise VerifyError(f"invalid exit set for {command}",
                              code="COVERAGE_MANIFEST")
        if not isinstance(template, str) or template.count("{exit}") != 1:
            raise VerifyError(f"invalid target template for {command}",
                              code="COVERAGE_MANIFEST")
        if not isinstance(overrides, dict):
            raise VerifyError(f"target_overrides must be an object for {command}",
                              code="COVERAGE_MANIFEST")
        declared_keys = {str(code) for code in exits}
        for raw_code, raw_targets in overrides.items():
            if (not isinstance(raw_code, str) or raw_code not in declared_keys
                    or not raw_code.isascii() or not raw_code.isdecimal()
                    or str(int(raw_code)) != raw_code):
                raise VerifyError(
                    f"target override exit {raw_code!r} is not declared by {command}",
                    code="COVERAGE_MANIFEST")
            if (not isinstance(raw_targets, list) or not raw_targets
                    or any(not isinstance(target, str) or not target.strip()
                           for target in raw_targets)
                    or len(raw_targets) != len(set(raw_targets))):
                raise VerifyError(
                    f"target override {command}:{raw_code} must contain unique targets",
                    code="COVERAGE_MANIFEST")
            pair = (command, int(raw_code))
            if pair in actual_overrides:
                raise VerifyError(f"duplicate target override {command}:{raw_code}",
                                  code="COVERAGE_MANIFEST")
            actual_overrides[pair] = tuple(raw_targets)
        for code in exits:
            stable = f"CLI-{command.replace('.', '-')}-{code}"
            if stable in coverage:
                raise VerifyError(f"duplicate coverage ID {stable}",
                                  code="COVERAGE_MANIFEST")
            coverage[stable] = list(actual_overrides.get(
                (command, code), (template.replace("{exit}", str(code)),)))
    if command_rows and actual_overrides != AUTHORITATIVE_TARGET_OVERRIDES:
        missing = sorted(set(AUTHORITATIVE_TARGET_OVERRIDES) - set(actual_overrides))
        extra = sorted(set(actual_overrides) - set(AUTHORITATIVE_TARGET_OVERRIDES))
        changed = sorted(
            pair for pair in set(actual_overrides) & set(AUTHORITATIVE_TARGET_OVERRIDES)
            if actual_overrides[pair] != AUTHORITATIVE_TARGET_OVERRIDES[pair]
        )
        raise VerifyError(
            "authoritative target override closure mismatch "
            f"missing={missing} extra={extra} changed={changed}",
            code="COVERAGE_MANIFEST")
    return coverage


def coverage_target_passed(
    target: str,
    passed_nodes: set[str],
    native_passed: set[str],
    eval_cases: dict[str, dict[str, Any]],
    same_run_passed: set[str] | None = None,
) -> bool:
    if target.startswith("pytest:"):
        return target.removeprefix("pytest:") in passed_nodes
    if "::" in target:
        return target in passed_nodes
    if target.startswith("native:"):
        return target.removeprefix("native:") in native_passed
    if target.startswith("same-run:"):
        return target in (same_run_passed or set())
    if target.startswith("eval:"):
        eval_id = target.removeprefix("eval:")
        case = eval_cases.get(eval_id)
        if case is None:
            return False
        structure_node = (
            "tests/test_eval_contracts.py::"
            "test_declared_eval_case_is_bound_to_executable_policy"
            f"[{eval_id}]"
        )
        return structure_node in passed_nodes and all(
            coverage_target_passed(
                behavior_target, passed_nodes, native_passed, eval_cases,
                same_run_passed)
            for behavior_target in case["behavior_targets"]
        )
    return False


def verify_coverage(module: Path, manifest: dict[str, Any], outcome: dict[str, Any],
                    native: dict[str, Any], eval_cases: dict[str, dict[str, Any]],
                    same_run_passed: set[str] | None = None
                    ) -> dict[str, list[str]]:
    coverage = expanded_coverage(manifest)
    expected = expected_coverage(module, eval_cases)
    if set(coverage) != expected:
        missing = sorted(expected - set(coverage))
        extra = sorted(set(coverage) - expected)
        raise VerifyError(f"coverage ID mismatch missing={missing} extra={extra}",
                          code="COVERAGE_MANIFEST")
    passed_nodes = set(outcome["passed"])
    native_passed = {case["id"] for case in native["cases"]
                     if case.get("status") == "pass"}
    deferred: dict[str, list[str]] = {}
    for stable_id, targets in coverage.items():
        if not isinstance(targets, list) or not targets:
            raise VerifyError(f"coverage ID {stable_id} has no targets",
                              code="COVERAGE_TARGET", subject=stable_id)
        outer_targets = [target for target in targets
                         if isinstance(target, str) and target.startswith("outer:")]
        if outer_targets:
            expected_targets = DEFERRED_OUTER_COVERAGE.get(stable_id)
            if targets != expected_targets:
                raise VerifyError(
                    f"coverage ID {stable_id} has unauthorized deferred targets",
                    code="COVERAGE_MANIFEST", subject=stable_id)
            deferred[stable_id] = list(targets)
            continue
        failed_targets = [
            target for target in targets
            if not coverage_target_passed(
                target, passed_nodes, native_passed, eval_cases,
                same_run_passed)
        ]
        if failed_targets:
            raise VerifyError(
                f"coverage ID {stable_id} targets did not all execute and pass: {failed_targets}",
                code="COVERAGE_NOT_EXECUTED", subject=stable_id)
    if deferred != DEFERRED_OUTER_COVERAGE:
        raise VerifyError(
            f"deferred outer coverage mismatch expected={DEFERRED_OUTER_COVERAGE} "
            f"actual={deferred}", code="COVERAGE_MANIFEST")
    return deferred


def verify_pytest_inventory(manifest: dict[str, Any], collected: list[str]) -> None:
    expected = manifest.get("pytest_inventory")
    if not isinstance(expected, dict):
        raise VerifyError("verification manifest requires a pinned pytest inventory",
                          code="PYTEST_INVENTORY")
    if len(collected) != len(set(collected)):
        raise VerifyError("pytest collected duplicate node IDs", code="PYTEST_INVENTORY")
    canonical = canonical_json_bytes(sorted(collected))
    actual = {
        "algorithm": "sha256-sorted-nodeids-v1",
        "count": len(collected),
        "digest": digest_bytes(canonical),
    }
    if expected != actual:
        raise VerifyError(
            f"pytest inventory mismatch expected={expected} actual={actual}",
            code="PYTEST_INVENTORY")


def orchestrator_success_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "pass", "command": "verify-orchestrator.run",
        "revision": None, "result": result, "findings": [],
    }


def main(argv: list[str] | None = None) -> int:
    started = now()
    run_nonce = ""
    pinned_uv: PinnedUV | None = None
    try:
        parser = Parser()
        parser.add_argument("--project-root", required=True)
        parser.add_argument("--module-root", required=True)
        parser.add_argument("--native-adapter", required=True)
        parser.add_argument("--run-dir", required=True)
        parser.add_argument("--run-nonce", required=True)
        args = parser.parse_args(argv)
        run_nonce = args.run_nonce
        if os.environ.get("SDM_INJECT_INTERNAL_FAILURE") == "1":
            raise RuntimeError("injected internal failure")
        project = Path(args.project_root).resolve(strict=True)
        module = Path(args.module_root).resolve(strict=True)
        run_dir = Path(args.run_dir).resolve(strict=True)
        adapter = Path(args.native_adapter).resolve(strict=True)
        if run_dir == module or module in run_dir.parents:
            raise VerifyError("run directory must be external to module source",
                              code="RUN_DIR_BOUNDARY")
        initial_module_snapshot = tree_snapshot(module)
        cache_nodes = [
            relative for relative in initial_module_snapshot
            if ("__pycache__" in PurePosixPath(relative).parts
                or ".pytest_cache" in PurePosixPath(relative).parts
                or relative.endswith(".pyc"))
        ]
        if cache_nodes:
            raise VerifyError(f"generated cache nodes in source: {cache_nodes}",
                              code="SOURCE_CACHE")
        protected_roots = [module, project / "_bmad", project / ".agents" / "skills"]
        before = {
            str(path): (initial_module_snapshot if path == module
                        else tree_snapshot(path))
            for path in protected_roots
        }
        digest = source_digest(module)
        adapter_raw = _stable_bounded_file(adapter)
        adapter_digest = digest_bytes(adapter_raw)
        manifest_path = module / "sdm-lifecycle" / "assets" / "verification-manifest.json"
        manifest_raw = _stable_bounded_file(manifest_path)
        manifest = parse_json_bytes(manifest_raw, str(manifest_path))
        manifest_digest = digest_bytes(manifest_raw)
        eval_cases, case_digest = collect_cases(module)
        env = dict(os.environ)
        env.update({
            "PYTHONDONTWRITEBYTECODE": "1", "SDM_RUN_NONCE": run_nonce,
            "SDM_SOURCE_DIGEST": digest, "SDM_ADAPTER_DIGEST": adapter_digest,
            "SDM_VERIFICATION_MANIFEST_DIGEST": manifest_digest,
            "SDM_CASE_MANIFEST_DIGEST": case_digest,
            "SDM_OUTCOME_PATH": str(run_dir / "pytest-outcome.json"),
            "PYTHONPATH": str(module),
        })
        pinned_uv = open_pinned_uv(module, env)
        validators = run_validators(project, module, run_dir, env, pinned_uv)
        pytest_command = [
            "uv", "run", "--with", "pytest==9.1.1",
            "--with", "pyyaml==6.0.3", "--with", "jsonschema==4.26.0",
            "--with", "cryptography==50.0.0", "--", "python", "-m",
            "pytest", "-s", "-p", "no:cacheprovider", "-p", "tests.outcome_plugin",
            "--junitxml", str(run_dir / "pytest.xml"), "-q", "tests",
            "sdm-lifecycle/scripts/tests",
            "sdm-setup/scripts/tests",
        ]
        run(pytest_command, cwd=module, env=env,
            log=run_dir / "pytest-command.json",
            timeout=PYTEST_TIMEOUT_SECONDS, pinned_uv=pinned_uv)
        outcome = strict_json(run_dir / "pytest-outcome.json")
        for key, expected_value in (
            ("run_nonce", run_nonce), ("complete_source_tree_digest", digest),
            ("adapter_digest", adapter_digest),
            ("verification_manifest_digest", manifest_digest),
            ("case_manifest_digest", case_digest)):
            if outcome.get(key) != expected_value:
                raise VerifyError(f"stale pytest summary field {key}",
                                  code="STALE_SUMMARY")
        for key in ("failed", "errors", "skipped", "xfailed", "xpassed", "deselected"):
            if outcome.get(key):
                raise VerifyError(f"pytest outcome has {key}: {outcome[key]}",
                                  code="PYTEST_OUTCOME")
        if set(outcome["executed"]) != set(outcome["collected"]):
            raise VerifyError("not every collected test executed", code="PYTEST_OUTCOME")
        verify_pytest_inventory(manifest, outcome["collected"])

        native_script = module / "tests" / "native" / "run_native_evals.py"
        completed = run([
            sys.executable, "-B", str(native_script), "--project-root", str(project),
            "--module-root", str(module), "--adapter", str(adapter),
            "--run-dir", str(run_dir / "native"), "--run-nonce", run_nonce,
            "--source-digest", digest, "--verification-manifest-digest", manifest_digest,
            "--case-manifest-digest", case_digest,
        ], cwd=project, env=env, log=run_dir / "native-command.json",
           expected=(0, 2, 3, 4, 70), timeout=NATIVE_TIMEOUT_SECONDS,
           pinned_uv=pinned_uv)
        native_payload = parse_one_json(completed, "native-eval")
        native = require_native_success(completed, native_payload)
        for key, expected_value in (
            ("run_nonce", run_nonce), ("complete_source_tree_digest", digest),
            ("adapter_digest", adapter_digest),
            ("verification_manifest_digest", manifest_digest),
            ("case_manifest_digest", case_digest)):
            if native.get(key) != expected_value:
                raise VerifyError(f"stale native summary field {key}",
                                  code="STALE_SUMMARY")
        if native["executed"] <= 0 or native["skipped"] or native["failures"]:
            raise VerifyError("native summary does not satisfy strict gate",
                              code="NATIVE_GATE")
        deferred_coverage = verify_coverage(
            module, manifest, outcome, native, eval_cases,
            same_run_passed={"same-run:native-eval.run:0"})
        after = {str(path): tree_snapshot(path) for path in protected_roots}
        require_protected_roots_unchanged(before, after)
        result = {
            "schema": "sdm-verification-summary/v1", "run_nonce": run_nonce,
            "complete_source_tree_digest": digest,
            "adapter_digest": adapter_digest,
            "verification_manifest_digest": manifest_digest,
            "case_manifest_digest": case_digest, "started_at": started,
            "ended_at": now(), "tool_version": "sdm-verify/1",
            "uv_executable_path": str(pinned_uv.path),
            "uv_executable_digest": pinned_uv.digest,
            "uv_version": pinned_uv.version,
            "executed": len(outcome["executed"]) + native["executed"],
            "skipped": 0, "xfailed": 0, "xpassed": 0, "deselected": 0,
            "failures": 0, "skills": len(SKILL_NAMES), "agents": len(AGENT_NAMES),
            "lifecycle_steps": 21, "validators": len(validators),
            "coverage_ids": len(expanded_coverage(manifest)),
            "coverage_complete": False,
            "deferred_coverage": deferred_coverage,
            "native_evidence": {
                "return_code": completed.returncode,
                "command": native_payload["command"],
                "status": native_payload["status"],
                "revision": native_payload["revision"],
                "run_nonce": native["run_nonce"],
                "payload_digest": digest_bytes(canonical_json_bytes(
                    native_payload)),
            },
        }
        print(canonical_json_bytes(redact(
            orchestrator_success_payload(result))).decode("utf-8"))
        return 0
    except VerifyError as exc:
        status = {2: "fail", 3: "blocked", 4: "conflict",
                  5: "configuration_required"}.get(exc.exit_code,
                                                     "internal_error")
        payload = {
            "status": status, "command": "verify-orchestrator.run",
            "revision": None, "result": {
                "run_nonce": run_nonce, "started_at": started,
                "ended_at": now(),
            },
            "findings": [{
                "severity": "high", "code": exc.code,
                "subject": exc.subject, "json_pointer": "",
                "message": str(exc), "gate_impact": "block",
                "governing_asset": "verify.py",
                "rule_digest": digest_bytes(exc.code.encode()),
            }],
        }
        print(canonical_json_bytes(redact(payload)).decode("utf-8"))
        return exc.exit_code
    except Exception as exc:
        payload = {
            "status": "internal_error", "command": "verify-orchestrator.run",
            "revision": None, "result": {
                "run_nonce": run_nonce, "started_at": started,
                "ended_at": now(),
            }, "findings": [{
                "severity": "high", "code": "VERIFY_INTERNAL",
                "subject": "verification", "json_pointer": "",
                "message": "unexpected verifier failure",
                "gate_impact": "block", "governing_asset": "verify.py",
                "rule_digest": digest_bytes(b"VERIFY_INTERNAL"),
            }],
        }
        print(canonical_json_bytes(redact(payload)).decode("utf-8"))
        return 70
    finally:
        if pinned_uv is not None:
            try:
                os.close(pinned_uv.fd)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
