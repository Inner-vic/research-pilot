from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

import specctl
from contracts import AssetBundle, validate_schema
from ledger import replay
from workspace import snapshot_tree


SCRIPT = Path(__file__).resolve().parents[1] / "specctl.py"


def _run(*args: str, extra_env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess[str], dict]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra_env:
        environment.update(extra_env)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env=environment,
    )
    lines = completed.stdout.splitlines()
    assert len(lines) == 1, (completed.returncode, completed.stdout, completed.stderr)
    return completed, json.loads(lines[0])


def _assert_result_schema(output: dict, asset_bundle: AssetBundle) -> None:
    assert validate_schema(
        output,
        asset_bundle.document("schemas/result.schema.json"),
        registry=asset_bundle.documents,
    ) == []


def _configured_project(root: Path) -> Path:
    config = root / "_bmad" / "custom"
    skills = root / ".agents" / "skills"
    config.mkdir(parents=True)
    skills.mkdir(parents=True)
    trust = root / "trust"
    trust.mkdir()
    (trust / "trust-snapshot.json").write_text(
        json.dumps({
            "schema": "sdm-trust-snapshot/v1",
            "keys": [], "revocations": [],
        }),
        encoding="utf-8",
    )
    identities = {
        "data_platform_skill": "fake-data-platform",
        "source_control_platform_skill": "fake-source-control-platform",
        "ci_platform_skill": "fake-ci-platform",
        "ml_execution_platform_skill": "fake-ml-execution-platform",
    }
    for identity in (*identities.values(), "sdm-lifecycle"):
        skill = skills / identity
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            f"---\nname: {identity}\n---\n# fixture\n", encoding="utf-8",
        )
    rows = ["[modules.sdm]"] + [
        f'{key} = "{value}"' for key, value in identities.items()
    ] + [
        'sdm_output_root = "{project-root}/_bmad-output/sdm"',
        'sdm_trust_root = "{project-root}/trust"',
    ]
    (config / "config.toml").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (config / "config.user.toml").write_text(
        '[modules.sdm]\nsdm_active_workspace = ""\n', encoding="utf-8",
    )
    return root


def test_hash_command_is_json_only_and_canonical(tmp_path, asset_bundle) -> None:
    source = tmp_path / "input.yaml"
    source.write_text("z: 1.00\na: value\n", encoding="utf-8")
    completed, output = _run("hash", "--input", str(source))
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert output["status"] == "pass"
    assert output["command"] == "specctl.hash"
    assert output["result"]["canonical_size"] == len(b'{"a":"value","z":1}')
    _assert_result_schema(output, asset_bundle)
    assert specctl._render_result({"b": "value", "a": 1}) == \
        '{"a":1,"b":"value"}'
    assert specctl._render_result({"value": Decimal("1E+3")}) == \
        '{"value":1000}'


def test_malformed_cli_and_document_are_structured_exit_2(tmp_path, asset_bundle) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"x":1,"x":2}', encoding="utf-8")
    document, document_output = _run("hash", "--input", str(malformed))
    cli, cli_output = _run("hash")
    unknown, unknown_output = _run("not-a-command")
    for completed, output in ((document, document_output), (cli, cli_output), (unknown, unknown_output)):
        assert completed.returncode == 2
        assert output["status"] == "fail"
        assert output["findings"]
        assert {item["gate_impact"] for item in output["findings"]} == {"block"}
        _assert_result_schema(output, asset_bundle)


def test_lifecycle_create_and_resume_report_durable_revision(tmp_path, asset_bundle) -> None:
    project = _configured_project(tmp_path / "project")
    payload = tmp_path / "create.json"
    payload.write_text(
        json.dumps({"workspace_id": "cli-workspace", "protocol": "exploratory"}),
        encoding="utf-8",
    )
    workspace = project / "isolated" / "workspace"
    created, create_output = _run(
        "lifecycle",
        "create",
        "--project-root",
        str(project),
        "--workspace",
        str(workspace),
        "--payload",
        str(payload),
        "--expected-revision",
        "0",
        "--operation-id",
        "cli-create",
    )
    assert created.returncode == 0
    assert create_output["revision"] == 1
    assert replay(workspace).revision == 1
    resumed, resume_output = _run("lifecycle", "resume", "--workspace", str(workspace))
    assert resumed.returncode == 3
    assert resume_output["status"] == "blocked"
    assert resume_output["result"]["action"]["step"] == 1
    _assert_result_schema(create_output, asset_bundle)
    _assert_result_schema(resume_output, asset_bundle)


def test_lifecycle_create_uses_configured_output_root_when_workspace_omitted(
    tmp_path, asset_bundle,
) -> None:
    project = _configured_project(tmp_path / "project")
    payload = tmp_path / "create-default.json"
    payload.write_text(
        json.dumps({
            "workspace_id": "default-workspace",
            "protocol": "exploratory",
        }),
        encoding="utf-8",
    )
    completed, output = _run(
        "lifecycle", "create", "--project-root", str(project),
        "--payload", str(payload), "--expected-revision", "0",
        "--operation-id", "default-create",
    )
    expected = project / "_bmad-output" / "sdm"
    workspace = expected / "workspaces" / "default-workspace"
    assert completed.returncode == 0
    assert workspace.is_dir()
    assert output["result"] == {
        **{
            "workspace_id": "default-workspace",
            "workspace": str(workspace),
            "workspace_defaulted": True,
            "sdm_output_root": str(expected),
            "reports_root": str(expected / "reports"),
        },
        "ledger_head": output["result"]["ledger_head"],
        "protocol": "exploratory",
    }
    (project / "_bmad" / "custom" / "config.user.toml").write_text(
        f'[modules.sdm]\nsdm_active_workspace = "{workspace}"\n',
        encoding="utf-8",
    )
    active = specctl.resolve_active_workspace(str(project))
    assert active["workspace"] == str(workspace)
    assert active["workspace_id"] == "default-workspace"
    _assert_result_schema(output, asset_bundle)


def test_workspace_override_and_active_workspace_are_project_bounded(
    tmp_path, asset_bundle,
) -> None:
    project = _configured_project(tmp_path / "project")
    payload = tmp_path / "create-escape.json"
    payload.write_text(
        json.dumps({"workspace_id": "escape", "protocol": "exploratory"}),
        encoding="utf-8",
    )
    escaped = tmp_path / "outside-workspace"
    completed, output = _run(
        "lifecycle", "create", "--project-root", str(project),
        "--workspace", str(escaped), "--payload", str(payload),
        "--expected-revision", "0", "--operation-id", "escape-create",
    )
    assert completed.returncode == 5
    assert output["findings"][0]["code"] == "runtime_path_escape"
    assert not escaped.exists()
    _assert_result_schema(output, asset_bundle)

    personal = project / "_bmad" / "custom" / "config.user.toml"
    personal.write_text(
        '[modules.sdm]\nsdm_active_workspace = "../outside"\n',
        encoding="utf-8",
    )
    with pytest.raises(specctl.CLIError, match="normalized") as caught:
        specctl.resolve_active_workspace(str(project))
    assert caught.value.code == "runtime_path_ambiguous"

    target = project / "_bmad-output" / "sdm" / "workspaces" / "linked"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(tmp_path)
    personal.write_text(
        f'[modules.sdm]\nsdm_active_workspace = "{target}"\n',
        encoding="utf-8",
    )
    with pytest.raises(specctl.CLIError) as linked:
        specctl.resolve_active_workspace(str(project))
    assert linked.value.code == "runtime_path_unsafe"




def test_create_payload_rejects_retired_adapter_configuration(
    tmp_path, asset_bundle,
) -> None:
    project = _configured_project(tmp_path / "project")
    payload = tmp_path / "create-caller-adapter.json"
    payload.write_text(
        json.dumps({
            "workspace_id": "caller-adapter",
            "protocol": "formal",
            "promotion_adapter_module_path": str(tmp_path / "caller.py"),
        }),
        encoding="utf-8",
    )
    workspace = project / "isolated" / "caller-adapter-workspace"
    completed, output = _run(
        "lifecycle", "create", "--project-root", str(project),
        "--workspace", str(workspace), "--payload", str(payload),
        "--expected-revision", "0", "--operation-id", "caller-adapter-create",
    )
    assert completed.returncode == 2
    assert output["status"] == "fail"
    assert output["findings"][0]["code"] == "runtime_config_override"
    assert not workspace.exists()
    assert not (project / "promotion-state").exists()
    _assert_result_schema(output, asset_bundle)






def test_configuration_and_conflict_statuses_are_unambiguous(
    unbound_workspace,
    workspace,
    request_payload,
    tmp_path,
    asset_bundle,
) -> None:
    payload = tmp_path / "handoff.json"
    payload.write_text(json.dumps(request_payload()), encoding="utf-8")
    missing, missing_output = _run(
        "handoff",
        "create",
        "--workspace",
        str(unbound_workspace),
        "--payload",
        str(payload),
        "--expected-revision",
        str(replay(unbound_workspace).revision),
        "--operation-id",
        "missing-config",
    )
    assert missing.returncode == 5
    assert missing_output["status"] == "configuration_required"

    conflict, conflict_output = _run(
        "handoff",
        "create",
        "--workspace",
        str(workspace),
        "--payload",
        str(payload),
        "--expected-revision",
        str(replay(workspace).revision + 1),
        "--operation-id",
        "cas-conflict",
    )
    assert conflict.returncode == 4
    assert conflict_output["status"] == "conflict"
    _assert_result_schema(missing_output, asset_bundle)
    _assert_result_schema(conflict_output, asset_bundle)


def test_internal_failure_is_caught_as_single_json_exit_70(tmp_path, asset_bundle) -> None:
    source = tmp_path / "valid.json"
    source.write_text("{}", encoding="utf-8")
    completed, output = _run(
        "hash",
        "--input",
        str(source),
        extra_env={"SDM_INJECT_INTERNAL_FAILURE": "1"},
    )
    assert completed.returncode == 70
    assert output["status"] == "internal_error"
    assert output["result"] == {"error": "internal_error"}
    _assert_result_schema(output, asset_bundle)


def test_read_only_commands_leave_workspace_snapshot_identical(workspace) -> None:
    before = snapshot_tree(workspace)
    validate, validate_output = _run("validate", "--workspace", str(workspace))
    assert validate.returncode == 0
    assert validate_output["status"] == "pass"
    resumed, resume_output = _run("lifecycle", "resume", "--workspace", str(workspace))
    assert resumed.returncode == 3
    assert resume_output["status"] == "blocked"
    assert snapshot_tree(workspace) == before
