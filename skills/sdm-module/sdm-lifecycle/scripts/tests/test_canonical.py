from __future__ import annotations

from decimal import Decimal

import pytest

from canonical import (
    CanonicalError,
    MAX_DOCUMENT_DEPTH,
    MAX_NUMBER_TOKEN_BYTES,
    candidate_digest,
    canonical_bytes,
    digest,
    parse_document,
    read_document,
)


def test_json_and_yaml_subset_have_identical_canonical_bytes() -> None:
    json_value = parse_document('{"z":1.2300,"a":[true,null,"café"]}')
    yaml_value = parse_document('z: 1.2300\na:\n  - true\n  - null\n  - "café"\n')
    assert json_value == yaml_value
    assert canonical_bytes(json_value) == b'{"a":[true,null,"caf\xc3\xa9"],"z":1.23}'
    assert digest(json_value) == digest(yaml_value)


def test_canonical_numbers_and_sorted_keys_are_stable() -> None:
    assert canonical_bytes({"b": Decimal("-0.000"), "a": Decimal("1E+3")}) == b'{"a":1000,"b":0}'
    assert canonical_bytes({"x": 1.0}) == canonical_bytes({"x": 1})


def test_duplicate_keys_non_finite_and_i64_overflow_are_rejected() -> None:
    matrix = [
        ('{"x":1,"x":2}', "duplicate_key"),
        ('{"x":NaN}', "non_finite_number"),
        ('{"x":9223372036854775808}', "integer_out_of_range"),
    ]
    for document, expected in matrix:
        with pytest.raises(CanonicalError) as caught:
            parse_document(document)
        assert caught.value.code == expected


def test_yaml_ambiguity_and_power_features_are_rejected() -> None:
    matrix = [
        ("enabled: yes\n", "yaml_implicit_type"),
        ("date: 2026-08-24\n", "implicit_timestamp"),
        ("base: &base value\n", "yaml_feature_forbidden"),
        ("value: |\n  multiline\n", "yaml_feature_forbidden"),
        ("---\nvalue: 1\n", "yaml_feature_forbidden"),
    ]
    for document, expected in matrix:
        with pytest.raises(CanonicalError) as caught:
            parse_document(document)
        assert caught.value.code == expected


def test_utf8_bom_and_non_nfc_text_are_rejected() -> None:
    for document, expected in [
        (b"\xff", "invalid_utf8"),
        (b"\xef\xbb\xbf{}", "bom_forbidden"),
        ('{"name":"e\u0301"}', "non_nfc_string"),
    ]:
        with pytest.raises(CanonicalError) as caught:
            parse_document(document)
        assert caught.value.code == expected


def test_read_document_refuses_symlinks(tmp_path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(CanonicalError) as caught:
        read_document(link)
    assert caught.value.code == "input_open_failed"


def test_candidate_digest_excludes_only_freeze_metadata() -> None:
    draft = {"hypothesis": "h", "budget": 3, "status": "draft"}
    frozen = {
        **draft,
        "status": "frozen",
        "candidate_digest": "ignored",
        "frozen_digest": "ignored",
        "frozen_at": "ignored",
        "freeze_attestations": ["ignored"],
        "event_revision": 9,
    }
    assert candidate_digest(draft) == candidate_digest(frozen)
    assert candidate_digest({**frozen, "budget": 4}) != candidate_digest(frozen)


def test_decimal_spelling_is_context_free_and_distinct_values_do_not_collapse() -> None:
    import decimal

    prior = decimal.getcontext().copy()
    try:
        decimal.getcontext().prec = 2
        decimal.getcontext().rounding = decimal.ROUND_UP
        first = Decimal("1.234567890123456789")
        second = Decimal("1.234567890123456788")
        assert canonical_bytes(first) == b"1.234567890123456789"
        assert canonical_bytes(second) == b"1.234567890123456788"
        assert digest(first) != digest(second)
    finally:
        decimal.setcontext(prior)


def test_fixed_numeric_and_depth_limits_return_governed_errors() -> None:
    with pytest.raises(CanonicalError) as caught:
        parse_document('{"value":' + ("1" * (MAX_NUMBER_TOKEN_BYTES + 1)) + "}")
    assert caught.value.code in {"number_token_too_long", "integer_out_of_range"}

    with pytest.raises(CanonicalError) as caught:
        parse_document("[" * (MAX_DOCUMENT_DEPTH + 1) + "0" + "]" * (MAX_DOCUMENT_DEPTH + 1))
    assert caught.value.code == "document_depth_exceeded"


def test_complete_json_scalars_are_parsed_before_yaml_fallback() -> None:
    assert parse_document("true") is True
    assert parse_document("null") is None
    assert parse_document('"scalar"') == "scalar"
    assert parse_document("1.25") == Decimal("1.25")
    with pytest.raises(CanonicalError) as caught:
        parse_document("plain-scalar")
    assert caught.value.code == "malformed_json"


def test_yaml_list_mapping_column_and_single_quote_lexer_are_strict() -> None:
    assert parse_document("- name: 'it''s safe'\n  value: 1\n") == [
        {"name": "it's safe", "value": 1},
    ]
    for document in (
        "- name: valid\n   value: 1\n",
        "name: 'it'is not safe'\n",
    ):
        with pytest.raises(CanonicalError) as caught:
            parse_document(document)
        assert caught.value.code == "malformed_yaml"
