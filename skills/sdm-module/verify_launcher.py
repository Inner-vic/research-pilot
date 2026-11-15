#!/usr/bin/env python3
"""Stdlib-only isolation launcher for the authoritative SDM verifier."""

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
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlsplit, urlunsplit

sys.dont_write_bytecode = True


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


STATUS_BY_EXIT = {
    0: "pass", 2: "fail", 3: "blocked", 4: "conflict",
    5: "configuration_required", 70: "internal_error",
}
LAUNCHER_CHILD_EXITS = frozenset({0, 2, 3, 4, 70})
MAX_CHILD_OUTPUT_BYTES = 16_777_216
MAX_DOCUMENT_BYTES = 16_777_216
MAX_CANONICAL_BYTES = 16_777_216
MAX_NUMBER_TOKEN_BYTES = 256
MAX_SIGNIFICANT_DIGITS = 128
MAX_ABS_DECIMAL_EXPONENT = 4096
MAX_DOCUMENT_DEPTH = 128
MAX_DOCUMENT_NODES = 1_000_000
MIN_I64 = -(2**63)
MAX_I64 = 2**63 - 1
ORCHESTRATOR_TIMEOUT_SECONDS = 21_600
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

DEFERRED_OUTER_COVERAGE = {
    "CLI-verify-launcher-run-0": ["outer:verify-launcher.run:0"],
    "CLI-verify-orchestrator-run-0": ["outer:verify-orchestrator.run:0"],
}


class PinnedUV(NamedTuple):
    path: Path
    digest: str
    version: str
    fd: int
    identity: tuple[int, ...]


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode),
        stat.S_IMODE(info.st_mode), info.st_nlink, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    )


def _directory_identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))


def _digest_fd(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    hasher = hashlib.sha256()
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block:
            break
        hasher.update(block)
    return "sha256:" + hasher.hexdigest()


def _parse_int(value: str) -> int:
    if len(value.encode("ascii", "strict")) > MAX_NUMBER_TOKEN_BYTES:
        raise ValueError("number token exceeds the fixed byte limit")
    digits = value.lstrip("+-")
    if len(digits) > 19:
        raise ValueError("integer is outside signed 64-bit range")
    try:
        parsed = int(value, 10)
    except (ValueError, OverflowError) as exc:
        raise ValueError("invalid integer token") from exc
    if parsed < MIN_I64 or parsed > MAX_I64:
        raise ValueError("integer is outside signed 64-bit range")
    return parsed


def _parse_float(value: str) -> Decimal:
    if len(value.encode("ascii", "strict")) > MAX_NUMBER_TOKEN_BYTES:
        raise ValueError("number token exceeds the fixed byte limit")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise ValueError("invalid decimal token") from exc
    if not parsed.is_finite():
        raise ValueError("non-finite number is forbidden")
    _, digits, exponent = parsed.as_tuple()
    if len(digits) > MAX_SIGNIFICANT_DIGITS:
        raise ValueError("number exceeds the fixed significant-digit limit")
    adjusted = parsed.adjusted() if parsed else 0
    if (abs(exponent) > MAX_ABS_DECIMAL_EXPONENT
            or abs(adjusted) > MAX_ABS_DECIMAL_EXPONENT):
        raise ValueError("number exceeds the fixed decimal-exponent limit")
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
                raise ValueError("JSON exceeds the fixed nesting-depth limit")
        elif character in "]}":
            depth = max(0, depth - 1)
        elif character in ",:":
            structural_tokens += 1
            if structural_tokens > MAX_DOCUMENT_NODES * 2:
                raise ValueError("JSON exceeds the fixed node limit")


def _validate_json_model(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_DOCUMENT_NODES:
            raise ValueError("JSON exceeds the fixed node limit")
        if depth > MAX_DOCUMENT_DEPTH:
            raise ValueError("JSON exceeds the fixed nesting-depth limit")
        if item is None or isinstance(item, bool):
            continue
        if isinstance(item, int):
            if item < MIN_I64 or item > MAX_I64:
                raise ValueError("integer is outside signed 64-bit range")
            continue
        if isinstance(item, Decimal):
            _parse_float(str(item))
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("non-finite number is forbidden")
            _parse_float(str(item))
            continue
        if isinstance(item, str):
            if unicodedata.normalize("NFC", item) != item:
                raise ValueError("JSON strings must already be NFC normalized")
            continue
        if isinstance(item, list):
            stack.extend((child, depth + 1) for child in reversed(item))
            continue
        if isinstance(item, dict):
            children: list[tuple[Any, int]] = []
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("JSON object keys must be strings")
                children.extend(((key, depth + 1), (child, depth + 1)))
            stack.extend(reversed(children))
            continue
        raise ValueError("value is outside the JSON data model")


def parse_json_bytes(data: bytes, subject: str) -> Any:
    """Parse one bounded strict-JSON document without binary floats."""

    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"{subject} exceeds the fixed document byte limit")
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{subject} is not strict UTF-8") from exc
    except MemoryError as exc:
        raise ValueError(f"{subject} exceeded a fixed JSON resource limit") from exc
    if text.startswith("\ufeff"):
        raise ValueError(f"{subject} contains a forbidden UTF-8 BOM")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise ValueError(f"{subject} contains a duplicate object key")
            result[key] = item
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError(f"{subject} contains a non-finite number")

    try:
        _preflight_json(text)
        value = json.loads(
            text, object_pairs_hook=pairs, parse_int=_parse_int,
            parse_float=_parse_float, parse_constant=reject_constant,
        )
        _validate_json_model(value)
        return value
    except ValueError:
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"{subject} is malformed JSON") from exc
    except (RecursionError, MemoryError, OverflowError) as exc:
        raise ValueError(f"{subject} exceeded a fixed JSON resource limit") from exc


def _stable_bounded_file(path: Path, limit: int = MAX_DOCUMENT_BYTES) -> bytes:
    before_path = path.lstat()
    if (stat.S_ISLNK(before_path.st_mode)
            or not stat.S_ISREG(before_path.st_mode)
            or before_path.st_nlink != 1):
        raise ValueError("JSON input is not one regular file")
    if before_path.st_size > limit:
        raise ValueError("JSON input exceeds the fixed document byte limit")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if _identity(before) != _identity(before_path):
            raise ValueError("JSON input identity changed before read")
        chunks: list[bytes] = []
        length = 0
        while True:
            block = os.read(fd, min(1_048_576, limit + 1 - length))
            if not block:
                break
            chunks.append(block)
            length += len(block)
            if length > limit:
                raise ValueError("JSON input exceeds the fixed document byte limit")
        if (_identity(os.fstat(fd)) != _identity(before)
                or _identity(path.lstat()) != _identity(before)):
            raise ValueError("JSON input changed while read")
        try:
            return b"".join(chunks)
        except MemoryError as exc:
            raise ValueError("JSON input exceeded a fixed resource limit") from exc
    finally:
        os.close(fd)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("non-finite number is forbidden")
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
            raise ValueError("non-finite number is forbidden")
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
    raise ValueError("value is outside the JSON data model")


def canonical_json_bytes(value: Any) -> bytes:
    try:
        _validate_json_model(value)
        encoded = _encode_json(value).encode("utf-8")
    except (RecursionError, MemoryError) as exc:
        raise ValueError("JSON encoding exceeded a fixed resource limit") from exc
    if len(encoded) > MAX_CANONICAL_BYTES:
        raise ValueError("canonical JSON exceeds the fixed byte limit")
    return encoded


def _sensitive_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


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
    """Launcher-local exact authority redactor; never imports mutable runtime."""

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _sensitive_key(str(key)):
                try:
                    item_digest = digest_bytes(canonical_json_bytes(item))
                except Exception:
                    item_digest = digest_bytes(type(item).__name__.encode("utf-8"))
                result[key] = {"redacted": True, "digest": item_digest}
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _sanitize_uri(value)
    return value


def select_pinned_uv(module: Path) -> PinnedUV:
    """Open and pin UV from the module lock without consulting ``PATH``."""

    lock_path = module / "verification-toolchain.json"
    raw = _stable_bounded_file(lock_path)
    try:
        document = parse_json_bytes(raw, "verification toolchain lock")
    except ValueError as exc:
        raise ValueError("verification toolchain lock is malformed") from exc
    if (not isinstance(document, dict)
            or set(document) != {"schema", "uv"}
            or document.get("schema") != "sdm-verification-toolchain/v1"):
        raise ValueError("verification toolchain lock has an invalid shape")
    uv = document.get("uv")
    if (not isinstance(uv, dict)
            or set(uv) != {"version", "digest", "candidates"}
            or not isinstance(uv.get("version"), str)
            or not uv["version"].startswith("uv ")
            or not is_sha256(uv.get("digest"))
            or not isinstance(uv.get("candidates"), list)
            or tuple(uv["candidates"]) != UV_CANDIDATE_TEMPLATES):
        raise ValueError("verification toolchain UV entry is malformed")
    passwd_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    matches: list[PinnedUV] = []
    for raw_candidate in uv["candidates"]:
        if not isinstance(raw_candidate, str) or not raw_candidate:
            raise ValueError("verification toolchain candidate is malformed")
        rendered = raw_candidate.replace("{passwd-home}", str(passwd_home))
        candidate = Path(rendered)
        if not candidate.is_absolute():
            raise ValueError("verification toolchain candidates must be absolute")
        try:
            path_before = candidate.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(path_before.st_mode)
                or not stat.S_ISREG(path_before.st_mode)
                or path_before.st_nlink != 1
                or not path_before.st_mode & 0o111):
            continue
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(candidate, flags)
        except OSError:
            continue
        try:
            before = os.fstat(fd)
            identity = _identity(before)
            if (identity != _identity(path_before)
                    or _digest_fd(fd) != uv["digest"]):
                continue
            after = os.fstat(fd)
            path_after = candidate.lstat()
            if (_identity(after) != identity
                    or _identity(path_after) != identity):
                continue
            matches.append(PinnedUV(
                candidate, uv["digest"], uv["version"], fd, identity,
            ))
            fd = -1
        finally:
            if fd >= 0:
                os.close(fd)
    if len(matches) != 1:
        for match in matches:
            os.close(match.fd)
        raise FileNotFoundError(
            "exactly one digest-pinned UV executable must be available"
        )
    return matches[0]


def _path_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _resolve_proposed_path(path: Path) -> tuple[Path, Path, tuple[int, ...]]:
    """Resolve a possibly absent path without creating any component."""

    if not path.is_absolute():
        raise ValueError("run root must be absolute; cwd is not a boundary")
    lexical = Path(os.path.normpath(os.fspath(path)))
    current = Path(lexical.anchor)
    missing: list[str] = []
    for part in lexical.parts[1:]:
        if missing:
            missing.append(part)
            continue
        probe = current / part
        try:
            info = probe.lstat()
        except FileNotFoundError:
            missing.append(part)
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("run-root ancestry must contain only real directories")
        current = probe
    existing = current.resolve(strict=True)
    if existing != current:
        raise ValueError("run-root ancestry contains an alias")
    resolved = existing.joinpath(*missing)
    return resolved, existing, _directory_identity(existing.lstat())


def validate_run_parent(proposed: Path, protected: list[Path]) -> tuple[Path, bool]:
    resolved, existing, existing_identity = _resolve_proposed_path(proposed)
    for protected_path in protected:
        try:
            protected_resolved = protected_path.resolve(strict=True)
        except FileNotFoundError:
            continue
        if _path_overlap(resolved, protected_resolved):
            raise ValueError(
                f"verification run root overlaps protected path: {protected_resolved}"
            )
    if _directory_identity(existing.lstat()) != existing_identity:
        raise ValueError("run-root ancestry changed during validation")
    return resolved, resolved.exists()


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
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


def run_bounded_group(
    command: list[str], *, cwd: Path, env: dict[str, str], timeout: int,
    pass_fds: tuple[int, ...] = (),
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    """Run one child in its own process group with a hard output/deadline cap."""

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
    total = 0
    timed_out = False
    output_limited = False
    deadline = started + timeout
    try:
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _terminate_group(process)
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
                total += len(block)
                room = max(0, MAX_CHILD_OUTPUT_BYTES - sum(
                    len(value) for value in captured.values()
                ))
                captured[key.data].extend(block[:room])
                if total > MAX_CHILD_OUTPUT_BYTES:
                    output_limited = True
                    _terminate_group(process)
                    break
            if output_limited:
                break
        if process.poll() is None:
            process.wait(timeout=TERMINATION_GRACE_SECONDS)
        _terminate_group(process)
    finally:
        selector.close()
        if process.poll() is None:
            _terminate_group(process)
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
        command, process.returncode, stdout, stderr,
    )
    return completed, {
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "timeout_seconds": timeout, "timed_out": timed_out,
        "output_limited": output_limited,
        "output_bytes_observed": total, "invalid_utf8": invalid_utf8,
    }


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_digest(value: Any) -> str:
    return digest_bytes(canonical_json_bytes(value))


def is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 71
            and value.startswith("sha256:")
            and all(character in "0123456789abcdef" for character in value[7:]))


def load_orchestrator(module: Path):
    spec = importlib.util.spec_from_file_location(
        "_sdm_verify_launcher_orchestrator", module / "verify.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load verifier digest helpers")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    return verifier


def expected_child_digests(module: Path, adapter: Path) -> dict[str, str]:
    verifier = load_orchestrator(module)
    manifest_path = (module / "sdm-lifecycle" / "assets" /
                     "verification-manifest.json")
    _, case_digest = verifier.collect_cases(module)
    return {
        "complete_source_tree_digest": verifier.source_digest(module),
        "adapter_digest": digest_bytes(_stable_bounded_file(adapter)),
        "verification_manifest_digest": digest_bytes(
            _stable_bounded_file(manifest_path)),
        "case_manifest_digest": case_digest,
    }


def child_evidence(completed: subprocess.CompletedProcess,
                   payload: Any = None) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "return_code": completed.returncode,
        "stdout_digest": digest_bytes(completed.stdout.encode("utf-8")),
        "stderr_digest": digest_bytes(completed.stderr.encode("utf-8")),
    }
    if payload is not None:
        evidence["payload"] = payload
        evidence["payload_digest"] = canonical_digest(payload)
    return evidence


def response(status: str, message: str, *, code: str, exit_code: int,
             run_dir: str | None = None,
             orchestrator_evidence: dict[str, Any] | None = None) -> int:
    result: dict[str, Any] = {}
    if run_dir:
        result["run_dir"] = run_dir
    if orchestrator_evidence is not None:
        result["orchestrator_evidence"] = orchestrator_evidence
    payload = {
        "status": status, "command": "verify-launcher.run", "revision": None,
        "result": result,
        "findings": [{
            "severity": "high", "code": code, "subject": "verify-launcher",
            "json_pointer": "", "message": message, "gate_impact": "block",
            "governing_asset": "verify_launcher.py", "rule_digest": "sha256:" + "0" * 64,
        }],
    }
    print(canonical_json_bytes(redact(payload)).decode("utf-8"))
    return exit_code


def validate_child_result(
    completed: subprocess.CompletedProcess,
    payload: Any,
    *,
    run_nonce: str,
    expected_digests: dict[str, str],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("verifier result is not an object")
    return_code = completed.returncode
    expected_status = STATUS_BY_EXIT.get(return_code)
    required_keys = {"status", "command", "revision", "result", "findings"}
    if (return_code not in LAUNCHER_CHILD_EXITS or expected_status is None
            or not required_keys.issubset(payload)
            or set(payload) - (required_keys | {"extensions"})
            or payload.get("command") != "verify-orchestrator.run"
            or payload.get("status") != expected_status
            or payload.get("revision") is not None
            or not isinstance(payload.get("result"), dict)
            or not isinstance(payload.get("findings"), list)):
        raise ValueError("verifier result envelope does not match its return code")
    result = payload["result"]
    if result.get("run_nonce") != run_nonce:
        raise ValueError("verifier result run nonce is stale or substituted")
    if return_code == 0:
        if payload["findings"]:
            raise ValueError("passing verifier result contains findings")
        for key, expected in expected_digests.items():
            if result.get(key) != expected:
                raise ValueError(f"verifier result {key} is stale or substituted")
        if (result.get("coverage_complete") is not False
                or result.get("deferred_coverage") != DEFERRED_OUTER_COVERAGE):
            raise ValueError("verifier result has an invalid deferred coverage set")
        native_evidence = result.get("native_evidence")
        if (not isinstance(native_evidence, dict)
                or native_evidence.get("return_code") != 0
                or native_evidence.get("command") != "native-eval.run"
                or native_evidence.get("status") != "pass"
                or native_evidence.get("revision") is not None
                or native_evidence.get("run_nonce") != run_nonce
                or not is_sha256(native_evidence.get("payload_digest"))):
            raise ValueError("verifier result omitted valid same-run native evidence")
    elif not payload["findings"]:
        raise ValueError("failing verifier result omitted findings")
    return result


def launcher_result(
    completed: subprocess.CompletedProcess,
    child_payload: dict[str, Any],
    *,
    run_dir: Path,
    run_nonce: str,
    expected_digests: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    child_result = validate_child_result(
        completed, child_payload, run_nonce=run_nonce,
        expected_digests=expected_digests)
    result = dict(child_result)
    result.update({
        "run_dir": str(run_dir),
        "orchestrator_evidence": child_evidence(completed, child_payload),
    })
    if completed.returncode == 0:
        result.update({
            "coverage_complete": True,
            "deferred_coverage": {},
            "completed_outer_coverage": DEFERRED_OUTER_COVERAGE,
        })
    payload = {
        "status": STATUS_BY_EXIT[completed.returncode],
        "command": "verify-launcher.run", "revision": None,
        "result": result, "findings": list(child_payload["findings"]),
    }
    return completed.returncode, payload


def main(argv: list[str] | None = None) -> int:
    run_dir: Path | None = None
    pinned_uv: PinnedUV | None = None
    try:
        parser = Parser(description="Run isolated SDM verification")
        parser.add_argument("--project-root", required=True)
        parser.add_argument("--native-adapter", required=True)
        parser.add_argument("--run-root")
        args = parser.parse_args(argv)
        project = Path(args.project_root).resolve(strict=True)
        module = Path(__file__).resolve().parent
        adapter = Path(args.native_adapter)
        if not adapter.is_absolute():
            adapter = (project / adapter).resolve(strict=True)
        else:
            adapter = adapter.resolve(strict=True)
        expected_digests = expected_child_digests(module, adapter)
        pinned_uv = select_pinned_uv(module)
        real_codex_home = Path(os.environ.get(
            "CODEX_HOME", str(Path.home() / ".codex")
        )).resolve()
        auth_source = Path(os.environ.get(
            "SDM_NATIVE_AUTH_SOURCE", str(real_codex_home / "auth.json")
        )).resolve()
        protected = [
            module, project, project / "_bmad", project / ".agents",
            project / ".agents" / "skills", real_codex_home,
            auth_source.parent,
        ]
        for key in (
            "HOME", "CODEX_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
            "XDG_STATE_HOME", "UV_CACHE_DIR",
        ):
            raw = os.environ.get(key)
            if raw:
                protected.append(Path(raw))
        proposed_parent = (
            Path(args.run_root) if args.run_root
            else Path(os.environ.get("TMPDIR", "/tmp"))
        )
        run_parent, parent_existed = validate_run_parent(
            proposed_parent, protected,
        )
        if not parent_existed:
            parent = run_parent.parent.resolve(strict=True)
            parent_identity = _directory_identity(parent.lstat())
            directory_fd = os.open(
                parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                if _directory_identity(os.fstat(directory_fd)) != parent_identity:
                    raise ValueError("run-root parent changed before creation")
                os.mkdir(run_parent.name, 0o700, dir_fd=directory_fd)
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        run_parent_info = run_parent.lstat()
        if (not stat.S_ISDIR(run_parent_info.st_mode)
                or stat.S_ISLNK(run_parent_info.st_mode)):
            raise ValueError("verification run root is not a real directory")
        run_parent_identity = _directory_identity(run_parent_info)
        run_parent_fd = os.open(
            run_parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            if _directory_identity(os.fstat(run_parent_fd)) != run_parent_identity:
                raise ValueError("verification run root changed before use")
            run_name = "sdm-verify-" + uuid.uuid4().hex
            os.mkdir(run_name, 0o700, dir_fd=run_parent_fd)
            os.fsync(run_parent_fd)
            run_fd = os.open(
                run_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=run_parent_fd,
            )
            try:
                run_dir = run_parent / run_name
                if (_directory_identity(run_parent.lstat()) != run_parent_identity
                        or _directory_identity(run_dir.lstat())
                        != _directory_identity(os.fstat(run_fd))):
                    raise ValueError("verification run directory identity raced")
            finally:
                os.close(run_fd)
        finally:
            os.close(run_parent_fd)
        isolated = run_dir / "environment"
        paths = {
            "HOME": isolated / "home", "CODEX_HOME": isolated / "codex",
            "XDG_CACHE_HOME": isolated / "xdg-cache",
            "XDG_CONFIG_HOME": isolated / "xdg-config",
            "XDG_STATE_HOME": isolated / "xdg-state",
            "UV_CACHE_DIR": isolated / "uv-cache", "TMP": isolated / "tmp",
            "TEMP": isolated / "tmp", "TMPDIR": isolated / "tmp",
        }
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "SDM_RUN_NONCE": uuid.uuid4().hex,
            "SDM_REAL_CODEX_HOME": str(real_codex_home),
            "SDM_NATIVE_AUTH_SOURCE": str(auth_source),
            "SDM_PINNED_UV_PATH": str(pinned_uv.path),
            "SDM_PINNED_UV_DIGEST": pinned_uv.digest,
            "SDM_PINNED_UV_VERSION": pinned_uv.version,
            **{key: str(value) for key, value in paths.items()},
        }
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE"):
            if key in os.environ:
                env[key] = os.environ[key]
        uv_version, uv_version_meta = run_bounded_group(
            [f"/proc/self/fd/{pinned_uv.fd}", "--version"],
            cwd=project, env=env, timeout=10, pass_fds=(pinned_uv.fd,),
        )
        (run_dir / "uv-version-command.json").write_bytes(
            canonical_json_bytes(redact({
            "argv": [str(pinned_uv.path), "--version"],
            "return_code": uv_version.returncode,
            "stdout": uv_version.stdout,
            "stderr": {
                "redacted": True,
                "digest": digest_bytes(uv_version.stderr.encode("utf-8")),
            },
            **uv_version_meta,
        })) + b"\n")
        if (uv_version.returncode != 0 or uv_version_meta["timed_out"]
                or uv_version_meta["output_limited"]
                or uv_version_meta.get("invalid_utf8")
                or uv_version.stdout.strip() != pinned_uv.version):
            raise ValueError("digest-pinned UV version attestation failed")
        expected_digests.update({
            "uv_executable_path": str(pinned_uv.path),
            "uv_executable_digest": pinned_uv.digest,
            "uv_version": pinned_uv.version,
        })
        command = [sys.executable, "-B", str(module / "verify.py"),
                   "--project-root", str(project), "--module-root", str(module),
                   "--native-adapter", str(adapter), "--run-dir", str(run_dir),
                   "--run-nonce", env["SDM_RUN_NONCE"]]
        completed, command_meta = run_bounded_group(
            command, cwd=project, env=env,
            timeout=ORCHESTRATOR_TIMEOUT_SECONDS,
        )
        (run_dir / "launcher-orchestrator-command.json").write_bytes(
            canonical_json_bytes({
                "argv_digest": digest_bytes(canonical_json_bytes(command)),
                "argv_count": len(command),
                "executable": command[0],
                "return_code": completed.returncode,
                "stdout_digest": digest_bytes(completed.stdout.encode()),
                "stderr_digest": digest_bytes(completed.stderr.encode()),
                **command_meta,
            }) + b"\n")
        if (command_meta["timed_out"] or command_meta["output_limited"]
                or command_meta.get("invalid_utf8")):
            return response(
                "internal_error",
                "verifier exceeded its monotonic deadline or output budget",
                code="VERIFIER_DEADLINE", exit_code=70,
                run_dir=str(run_dir),
                orchestrator_evidence=child_evidence(completed),
            )
        if completed.stderr:
            (run_dir / "verifier.stderr.log").write_bytes(
                canonical_json_bytes({
                    "redacted": True,
                    "stderr_digest": digest_bytes(
                        completed.stderr.encode("utf-8")),
                }) + b"\n")
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            return response("internal_error", "verifier did not emit exactly one JSON object",
                            code="VERIFIER_PROTOCOL", exit_code=70,
                            run_dir=str(run_dir),
                            orchestrator_evidence=child_evidence(completed))
        try:
            payload = parse_json_bytes(lines[0].encode("utf-8"),
                                       "verifier result")
        except ValueError:
            return response("internal_error", "invalid verifier JSON",
                            code="VERIFIER_PROTOCOL", exit_code=70,
                            run_dir=str(run_dir),
                            orchestrator_evidence=child_evidence(completed))
        try:
            exit_code, outer_payload = launcher_result(
                completed, payload, run_dir=run_dir,
                run_nonce=env["SDM_RUN_NONCE"],
                expected_digests=expected_digests)
        except ValueError as exc:
            return response(
                "internal_error", str(exc), code="VERIFIER_PROTOCOL", exit_code=70,
                run_dir=str(run_dir),
                orchestrator_evidence=child_evidence(completed, payload))
        print(canonical_json_bytes(redact(outer_payload)).decode("utf-8"))
        return exit_code
    except FileNotFoundError as exc:
        return response("blocked", str(exc), code="NATIVE_UNAVAILABLE", exit_code=3,
                        run_dir=str(run_dir) if run_dir else None)
    except (ValueError, OSError) as exc:
        return response("fail", str(exc), code="CLI_VERIFY_LAUNCHER", exit_code=2,
                        run_dir=str(run_dir) if run_dir else None)
    except Exception as exc:
        return response("internal_error", "unexpected verifier launcher failure",
                        code="VERIFY_LAUNCHER_INTERNAL", exit_code=70,
                        run_dir=str(run_dir) if run_dir else None)
    finally:
        if pinned_uv is not None:
            try:
                os.close(pinned_uv.fd)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
