#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Strict SDM JSON/YAML-subset parsing and canonical JSON hashing.

The YAML accepted here is deliberately small.  It is indentation-based YAML
whose values are restricted to the JSON data model; aliases, tags, merge keys,
implicit dates and other YAML conveniences are rejected before construction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


MIN_I64 = -(2**63)
MAX_I64 = 2**63 - 1
MAX_DOCUMENT_BYTES = 16_777_216
MAX_CANONICAL_BYTES = 16_777_216
MAX_NUMBER_TOKEN_BYTES = 256
MAX_SIGNIFICANT_DIGITS = 128
MAX_ABS_DECIMAL_EXPONENT = 4096
MAX_DOCUMENT_DEPTH = 128
MAX_DOCUMENT_NODES = 1_000_000
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}:?\d{2})?)?$")
_INT = re.compile(r"^[+-]?(?:0|[1-9]\d*)$")
_NUMBER = re.compile(r"^[+-]?(?:(?:0|[1-9]\d*)\.\d+|(?:0|[1-9]\d*)(?:[eE][+-]?\d+)|(?:0|[1-9]\d*)\.\d+(?:[eE][+-]?\d+))$")
_DNS = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")


class CanonicalError(ValueError):
    """A deterministic input/canonicalization failure."""

    def __init__(self, code: str, message: str, pointer: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.pointer = pointer


def _reject_constant(value: str) -> None:
    raise CanonicalError("non_finite_number", f"non-finite number is forbidden: {value}")


def _pairs(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalError("duplicate_key", f"duplicate object key: {key}", f"/{_escape(key)}")
        result[key] = value
    return result


def _parse_int(value: str) -> int:
    if len(value.encode("ascii", "strict")) > MAX_NUMBER_TOKEN_BYTES:
        raise CanonicalError("number_token_too_long", "number token exceeds the fixed byte limit")
    digits = value.lstrip("+-")
    if len(digits) > 19:
        raise CanonicalError("integer_out_of_range", "integer is outside signed 64-bit range")
    try:
        parsed = int(value, 10)
    except (ValueError, OverflowError) as exc:
        raise CanonicalError("invalid_number", "invalid integer token") from exc
    if parsed < MIN_I64 or parsed > MAX_I64:
        raise CanonicalError("integer_out_of_range", "integer is outside signed 64-bit range")
    return parsed


def _parse_float(value: str) -> Decimal:
    if len(value.encode("ascii", "strict")) > MAX_NUMBER_TOKEN_BYTES:
        raise CanonicalError("number_token_too_long", "number token exceeds the fixed byte limit")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise CanonicalError("invalid_number", f"invalid JSON number: {value}") from exc
    if not parsed.is_finite():
        raise CanonicalError("non_finite_number", "non-finite number is forbidden")
    _, digits, exponent = parsed.as_tuple()
    if len(digits) > MAX_SIGNIFICANT_DIGITS:
        raise CanonicalError("number_precision_exceeded", "number exceeds the fixed significant-digit limit")
    adjusted = parsed.adjusted() if parsed else 0
    if abs(exponent) > MAX_ABS_DECIMAL_EXPONENT or abs(adjusted) > MAX_ABS_DECIMAL_EXPONENT:
        raise CanonicalError("number_exponent_exceeded", "number exceeds the fixed decimal-exponent limit")
    return parsed


def _document_text(data: bytes | str) -> str:
    if isinstance(data, bytes):
        if len(data) > MAX_DOCUMENT_BYTES:
            raise CanonicalError("document_too_large", "input exceeds the fixed document byte limit")
        try:
            text = data.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise CanonicalError("invalid_utf8", "input is not strict UTF-8") from exc
    else:
        try:
            encoded = data.encode("utf-8", "strict")
        except UnicodeError as exc:
            raise CanonicalError("invalid_utf8", "input is not strict UTF-8") from exc
        if len(encoded) > MAX_DOCUMENT_BYTES:
            raise CanonicalError("document_too_large", "input exceeds the fixed document byte limit")
        text = data
    if text.startswith("\ufeff"):
        raise CanonicalError("bom_forbidden", "UTF-8 BOM is forbidden")
    return text


def _preflight_json(text: str) -> None:
    """Bound JSON nesting before the stdlib decoder can recurse deeply."""

    depth = 0
    structural_tokens = 1
    quote = False
    escaped = False
    for char in text:
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quote = False
            continue
        if char == '"':
            quote = True
        elif char in "[{":
            depth += 1
            structural_tokens += 1
            if depth > MAX_DOCUMENT_DEPTH:
                raise CanonicalError("document_depth_exceeded", "document exceeds the fixed nesting-depth limit")
        elif char in "]}":
            depth = max(0, depth - 1)
        elif char in ",:":
            structural_tokens += 1
            if structural_tokens > MAX_DOCUMENT_NODES * 2:
                raise CanonicalError("document_nodes_exceeded", "document exceeds the fixed node limit")


def parse_json(text: str) -> Any:
    try:
        _preflight_json(text)
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_int=_parse_int,
            parse_float=_parse_float,
            parse_constant=_reject_constant,
        )
    except CanonicalError:
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CanonicalError("malformed_json", str(exc)) from exc
    except (RecursionError, MemoryError) as exc:
        raise CanonicalError("document_resource_limit", "JSON parsing exceeded a fixed resource limit") from exc
    validate_data_model(value)
    return value


def _strip_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if quote == '"' and escaped:
            escaped = False
            index += 1
            continue
        if quote == '"' and char == "\\":
            escaped = True
            index += 1
            continue
        if quote == "'" and char == "'" and index + 1 < len(line) and line[index + 1] == "'":
            index += 2
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            index += 1
            continue
        if char == "#" and quote is None and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
        index += 1
    return line.rstrip()


def _colon(text: str) -> int:
    quote: str | None = None
    escaped = False
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if quote == '"' and escaped:
            escaped = False
            index += 1
            continue
        if quote == '"' and char == "\\":
            escaped = True
            index += 1
            continue
        if quote == "'" and char == "'" and index + 1 < len(text) and text[index + 1] == "'":
            index += 2
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            index += 1
            continue
        if quote is None:
            if char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
            elif char == ":" and depth == 0:
                return index
        index += 1
    return -1


def _yaml_string(token: str) -> str:
    if token.startswith('"'):
        try:
            result = json.loads(token)
        except json.JSONDecodeError as exc:
            raise CanonicalError("malformed_yaml", f"invalid quoted string: {exc}") from exc
        if not isinstance(result, str):
            raise CanonicalError("malformed_yaml", "quoted YAML key/value must be a string")
        return result
    if token.startswith("'"):
        if len(token) < 2 or not token.endswith("'"):
            raise CanonicalError("malformed_yaml", "unterminated single-quoted string")
        inner = token[1:-1]
        result: list[str] = []
        index = 0
        while index < len(inner):
            if inner[index] != "'":
                result.append(inner[index])
                index += 1
                continue
            if index + 1 >= len(inner) or inner[index + 1] != "'":
                raise CanonicalError("malformed_yaml", "single quotes inside a single-quoted scalar must be doubled")
            result.append("'")
            index += 2
        return "".join(result)
    return token


def _yaml_scalar(token: str) -> Any:
    token = token.strip()
    if not token:
        raise CanonicalError("malformed_yaml", "missing scalar")
    if token in ("|", ">", "|-", ">-", "|+", ">+"):
        raise CanonicalError("yaml_feature_forbidden", "block scalars are outside the JSON-compatible subset")
    if token[0] in "[{":
        return parse_json(token)
    if token[0] in "\"'":
        return _yaml_string(token)
    lower = token.lower()
    if lower in ("null", "~"):
        return None
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in ("yes", "no", "on", "off"):
        raise CanonicalError("yaml_implicit_type", f"ambiguous YAML boolean is forbidden: {token}")
    if _DATE.fullmatch(token):
        raise CanonicalError("implicit_timestamp", "implicit YAML timestamps must be quoted")
    if _INT.fullmatch(token):
        return _parse_int(token.lstrip("+"))
    if _NUMBER.fullmatch(token):
        return _parse_float(token.lstrip("+"))
    if token.startswith(("&", "*", "!")) or token == "<<":
        raise CanonicalError("yaml_feature_forbidden", "YAML anchors, aliases, tags and merge keys are forbidden")
    return token


def parse_yaml_subset(text: str) -> Any:
    if text.startswith("---") or "\n---" in text or "\n..." in text:
        raise CanonicalError("yaml_feature_forbidden", "multi-document YAML markers are forbidden")
    logical: list[tuple[int, int, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise CanonicalError("malformed_yaml", "tabs are forbidden in indentation", f"/line/{number}")
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line[indent:]
        if re.search(r"(?:^|[\s\[\]{},:])(?:&|\*|!)[^\s]*", body) or re.match(r"^<<\s*:", body):
            raise CanonicalError("yaml_feature_forbidden", "YAML anchors, aliases, tags and merge keys are forbidden", f"/line/{number}")
        logical.append((indent, number, body))
        if len(logical) > MAX_DOCUMENT_NODES:
            raise CanonicalError("document_nodes_exceeded", "document exceeds the fixed node limit")
    if not logical:
        raise CanonicalError("empty_document", "input document is empty")
    if logical[0][0] != 0:
        raise CanonicalError("malformed_yaml", "root value must start at indentation zero")

    def parse_block(pos: int, indent: int, depth: int = 1) -> tuple[Any, int]:
        if depth > MAX_DOCUMENT_DEPTH:
            raise CanonicalError("document_depth_exceeded", "document exceeds the fixed nesting-depth limit")
        if pos >= len(logical) or logical[pos][0] != indent:
            raise CanonicalError("malformed_yaml", "invalid indentation")
        is_list = logical[pos][2] == "-" or logical[pos][2].startswith("- ")
        container: Any = [] if is_list else {}
        while pos < len(logical):
            current_indent, line_no, body = logical[pos]
            if current_indent < indent:
                break
            if current_indent > indent:
                raise CanonicalError("malformed_yaml", "unexpected indentation", f"/line/{line_no}")
            current_is_list = body == "-" or body.startswith("- ")
            if current_is_list != is_list:
                raise CanonicalError("malformed_yaml", "mapping/list kinds cannot be mixed at one indentation", f"/line/{line_no}")
            if is_list:
                rest = body[1:].strip()
                if not rest:
                    if pos + 1 >= len(logical) or logical[pos + 1][0] <= indent:
                        raise CanonicalError("malformed_yaml", "list item has no value", f"/line/{line_no}")
                    value, pos = parse_block(pos + 1, logical[pos + 1][0], depth + 1)
                    container.append(value)
                    continue
                split = _colon(rest)
                if split > 0:
                    key = _yaml_string(rest[:split].strip())
                    if not key:
                        raise CanonicalError("malformed_yaml", "empty mapping key", f"/line/{line_no}")
                    item: dict[str, Any] = {}
                    tail = rest[split + 1 :].strip()
                    if tail:
                        item[key] = _yaml_scalar(tail)
                        pos += 1
                    else:
                        if pos + 1 >= len(logical) or logical[pos + 1][0] <= indent:
                            raise CanonicalError("malformed_yaml", "mapping key has no value", f"/line/{line_no}")
                        item[key], pos = parse_block(pos + 1, logical[pos + 1][0], depth + 1)
                    # Additional mapping keys belonging to this list item.
                    while pos < len(logical) and logical[pos][0] > indent:
                        child_indent = logical[pos][0]
                        _, child_line, child_body = logical[pos]
                        if child_indent != indent + 2:
                            raise CanonicalError(
                                "malformed_yaml",
                                "list-item mapping continuation must use one exact column",
                                f"/line/{child_line}",
                            )
                        split2 = _colon(child_body)
                        if split2 <= 0:
                            raise CanonicalError("malformed_yaml", "expected list-item mapping key", f"/line/{child_line}")
                        child_key = _yaml_string(child_body[:split2].strip())
                        if child_key in item:
                            raise CanonicalError("duplicate_key", f"duplicate object key: {child_key}", f"/line/{child_line}")
                        tail2 = child_body[split2 + 1 :].strip()
                        if tail2:
                            item[child_key] = _yaml_scalar(tail2)
                            pos += 1
                        else:
                            if pos + 1 >= len(logical) or logical[pos + 1][0] <= child_indent:
                                raise CanonicalError("malformed_yaml", "mapping key has no value", f"/line/{child_line}")
                            item[child_key], pos = parse_block(pos + 1, logical[pos + 1][0], depth + 1)
                    container.append(item)
                    continue
                container.append(_yaml_scalar(rest))
                pos += 1
            else:
                split = _colon(body)
                if split <= 0:
                    raise CanonicalError("malformed_yaml", "mapping entry must contain a colon", f"/line/{line_no}")
                key = _yaml_string(body[:split].strip())
                if not key:
                    raise CanonicalError("malformed_yaml", "empty mapping key", f"/line/{line_no}")
                if key in container:
                    raise CanonicalError("duplicate_key", f"duplicate object key: {key}", f"/line/{line_no}")
                tail = body[split + 1 :].strip()
                if tail:
                    container[key] = _yaml_scalar(tail)
                    pos += 1
                else:
                    if pos + 1 >= len(logical) or logical[pos + 1][0] <= indent:
                        raise CanonicalError("malformed_yaml", "mapping key has no value", f"/line/{line_no}")
                    container[key], pos = parse_block(pos + 1, logical[pos + 1][0], depth + 1)
        return container, pos

    try:
        value, consumed = parse_block(0, 0)
    except (RecursionError, MemoryError) as exc:
        raise CanonicalError("document_resource_limit", "YAML parsing exceeded a fixed resource limit") from exc
    if consumed != len(logical):
        raise CanonicalError("malformed_yaml", "document was not consumed completely")
    validate_data_model(value)
    return value


def parse_document(data: bytes | str) -> Any:
    text = _document_text(data)
    stripped = text.lstrip()
    if not stripped:
        raise CanonicalError("empty_document", "input document is empty")
    if stripped.startswith("---") or stripped.startswith("..."):
        return parse_yaml_subset(text)
    try:
        return parse_json(text)
    except CanonicalError as json_error:
        if json_error.code != "malformed_json":
            raise
        first = next((line for line in text.splitlines() if _strip_comment(line).strip()), "")
        body = first.lstrip(" ")
        if not (body == "-" or body.startswith("- ") or _colon(body) > 0):
            raise json_error
        value = parse_yaml_subset(text)
        if not isinstance(value, (dict, list)):
            raise json_error
        return value


def read_document(path: str | os.PathLike[str]) -> Any:
    target = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags)
    except OSError as exc:
        raise CanonicalError("input_open_failed", f"cannot safely open input: {target}: {exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise CanonicalError("unsafe_input_type", f"input is not a regular file: {target}")
        if info.st_size > MAX_DOCUMENT_BYTES:
            raise CanonicalError("document_too_large", "input exceeds the fixed document byte limit")
        chunks: list[bytes] = []
        remaining = MAX_DOCUMENT_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(1_048_576, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise CanonicalError("document_too_large", "input exceeds the fixed document byte limit")
    finally:
        os.close(fd)
    return parse_document(data)


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def validate_data_model(value: Any, pointer: str = "") -> None:
    stack: list[tuple[Any, str, int]] = [(value, pointer, 1)]
    nodes = 0
    while stack:
        item, at, depth = stack.pop()
        nodes += 1
        if nodes > MAX_DOCUMENT_NODES:
            raise CanonicalError("document_nodes_exceeded", "document exceeds the fixed node limit", at)
        if depth > MAX_DOCUMENT_DEPTH:
            raise CanonicalError("document_depth_exceeded", "document exceeds the fixed nesting-depth limit", at)
        if item is None or isinstance(item, bool):
            continue
        if isinstance(item, int):
            if item < MIN_I64 or item > MAX_I64:
                raise CanonicalError("integer_out_of_range", "integer is outside signed 64-bit range", at)
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                raise CanonicalError("non_finite_number", "non-finite number is forbidden", at)
            _parse_float(str(item))
            continue
        if isinstance(item, Decimal):
            _parse_float(str(item))
            continue
        if isinstance(item, str):
            if unicodedata.normalize("NFC", item) != item:
                raise CanonicalError("non_nfc_string", "strings must already be NFC normalized", at)
            continue
        if isinstance(item, list):
            stack.extend(
                (child, f"{at}/{index}", depth + 1)
                for index, child in reversed(list(enumerate(item)))
            )
            continue
        if isinstance(item, dict):
            children: list[tuple[Any, str, int]] = []
            for key, child in item.items():
                if not isinstance(key, str):
                    raise CanonicalError("non_string_key", "object keys must be strings", at)
                child_at = f"{at}/{_escape(key)}"
                children.extend(((key, child_at, depth + 1), (child, child_at, depth + 1)))
            stack.extend(reversed(children))
            continue
        raise CanonicalError("unsupported_type", f"value type is outside JSON data model: {type(item).__name__}", at)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise CanonicalError("non_finite_number", "non-finite number is forbidden")
    if not value:
        return "0"
    sign, digits_tuple, exponent = value.as_tuple()
    digits = list(digits_tuple)
    while len(digits) > 1 and exponent < 0 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    raw = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        text = raw + ("0" * exponent)
    else:
        point = len(raw) + exponent
        text = (raw[:point] + "." + raw[point:]) if point > 0 else ("0." + ("0" * -point) + raw)
    return ("-" if sign else "") + text


def _encode(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        _parse_int(str(value))
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalError("non_finite_number", "non-finite number is forbidden")
        return _decimal_text(Decimal(str(value)))
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise CanonicalError("non_nfc_string", "strings must already be NFC normalized")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise CanonicalError("non_string_key", "object keys must be strings")
        return "{" + ",".join(_encode(key) + ":" + _encode(value[key]) for key in sorted(value)) + "}"
    raise CanonicalError("unsupported_type", f"value type is outside JSON data model: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    validate_data_model(value)
    try:
        result = _encode(value).encode("utf-8")
    except (RecursionError, MemoryError) as exc:
        raise CanonicalError("document_resource_limit", "canonicalization exceeded a fixed resource limit") from exc
    if len(result) > MAX_CANONICAL_BYTES:
        raise CanonicalError("canonical_too_large", "canonical output exceeds the fixed byte limit")
    return result


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def digest(value: Any) -> str:
    return digest_bytes(canonical_bytes(value))


def candidate_content(value: dict[str, Any]) -> dict[str, Any]:
    excluded = {
        "status",
        "candidate_digest",
        "frozen_digest",
        "frozen_at",
        "freeze_attestations",
        "event_revision",
    }
    return {key: item for key, item in value.items() if key not in excluded}


def candidate_digest(value: dict[str, Any]) -> str:
    return digest(candidate_content(value))


def canonical_file_digest(path: str | os.PathLike[str]) -> str:
    return digest(read_document(path))


def _diagnostic_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect the SDM canonical-data library")
    parser.parse_args(argv)
    sys.stdout.write(json.dumps({"status": "pass", "command": "canonical.help"}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(_diagnostic_main())
