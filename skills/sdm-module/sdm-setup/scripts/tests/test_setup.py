from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).resolve().parents[1] / "setup.py"
    spec = importlib.util.spec_from_file_location("sdm_setup_script", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_contract_inventory_and_canonical_output():
    module = _module()
    assert len(module.SKILLS) == 14
    assert len(module.ANSWER_KEYS) == 7
    assert module.canonical_json({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    assert module.parser().parse_args([
        "recover", "--project-root", "/tmp/project"]).command == "recover"


def test_setup_rejects_retired_and_invalid_answers():
    module = _module()
    answers = {key: "" for key in module.ANSWER_KEYS}
    answers.update(sdm_output_root="output", sdm_trust_root="trust")
    assert module.load_answers(__import__("json").dumps(answers)) == answers
    for changes in ({"promotion_state_root": "retired"},
                    {"sdm_output_root": ""},
                    {"data_platform_skill": "../platform"}):
        with pytest.raises(module.SetupError):
            module.load_answers(__import__("json").dumps({**answers, **changes}))
