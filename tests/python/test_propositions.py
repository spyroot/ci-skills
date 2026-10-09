"""Regression coverage for compact, graph-shaped capability discovery."""

from __future__ import annotations

import json

import yaml
from conftest import (
    REPO_ROOT,
    SCRIPT_ROOT,
    import_script_module,
    install_executable,
    parse_json_output,
    read_journal,
    run_script,
)
from jsonschema import Draft202012Validator

CATALOG = import_script_module("core.catalog")
PROPOSITION_SCHEMA = json.loads(
    (REPO_ROOT / "schemas" / "reference-next.schema.json").read_text(encoding="utf-8")
)
COMMAND_CONTRACT_SCHEMA = json.loads(
    (REPO_ROOT / "schemas" / "command-contract.schema.json").read_text(encoding="utf-8")
)


def _commands() -> dict[str, dict]:
    """Return the installed Python commands proposition nodes may offer."""
    return {**CATALOG.COMMANDS, **CATALOG.NODE_LOCAL_COMMANDS}


def _validate(payload: dict) -> None:
    """Validate one root or selected node against the versioned proposition schema."""
    Draft202012Validator(PROPOSITION_SCHEMA).validate(payload)


def test_root_clusters_are_derived_from_declared_command_authorities():
    """Discovery offers only actual installed filenames for each authority cluster."""
    root = CATALOG.proposition()
    commands = _commands()

    _validate(root)
    assert root["schema_version"] == "2.0"
    assert set(root) == {"schema_version", *CATALOG.PROPOSITION_CLUSTERS}
    for cluster, authority in CATALOG.PROPOSITION_CLUSTERS.items():
        node = root[cluster]
        expected = sorted(
            script
            for script, entry in commands.items()
            if authority in entry["requires"]
        )
        assert set(node) == {"tools", "reference"}
        assert node["tools"] == {script: {} for script in expected}
        for script, metadata in node["tools"].items():
            assert metadata == {}
            assert (SCRIPT_ROOT / script).is_file()
        for reference in node["reference"].values():
            assert (SCRIPT_ROOT.parent / reference["pointer"]).is_file()


def test_selected_cluster_and_command_are_small_schema_valid_propositions():
    """A selection expands one node while preserving related installed peers."""
    root = CATALOG.proposition()
    cluster = CATALOG.proposition("glab")
    command = "gitlab_runner.py"
    selected = CATALOG.proposition(command)

    _validate(cluster)
    _validate(selected)
    assert cluster == {"schema_version": "2.0", **root["glab"]}
    assert selected["tools"] == {
        script: {}
        for script in [command, *CATALOG.COMMANDS[command].get("related", ())]
    }
    assert "mcp" in selected["reference"]
    assert selected["reference"]["mcp"]["pointer"] == "references/mcp.md"


def test_reference_cli_expands_nodes_without_fetching_optional_references(
    fake_bin, call_journal
):
    """The navigator returns pointers and never invokes a provider or fetch tool."""
    body = '#!/bin/sh\nprintf \'{"tool": "%s"}\\n\' "$0" >> "$FAKE_JOURNAL"\nexit 99\n'
    for tool in ("curl", "gh", "git", "glab"):
        install_executable(fake_bin, tool, body)

    root = run_script(
        "reference.py",
        "--json",
        fake_bin=fake_bin,
        env={"FAKE_JOURNAL": str(call_journal)},
    )
    selected = run_script(
        "reference.py",
        "gitlab_runner.py",
        "--yaml",
        fake_bin=fake_bin,
        env={"FAKE_JOURNAL": str(call_journal)},
    )
    help_result = run_script("reference.py", "--help", fake_bin=fake_bin)

    assert root.returncode == 0
    assert parse_json_output(root) == CATALOG.proposition()
    assert selected.returncode == 0
    assert yaml.safe_load(selected.stdout) == CATALOG.proposition("gitlab_runner.py")
    assert help_result.returncode == 0
    assert "node" in help_result.stdout
    assert "--json" in help_result.stdout
    assert "--yaml" in help_result.stdout
    assert read_journal(call_journal) == []


def test_reference_describe_is_a_schema_valid_offline_command_contract(
    fake_bin, call_journal
):
    """Describe returns the normal contract without resolving a provider or reference."""
    body = '#!/bin/sh\nprintf \'{"tool": "%s"}\\n\' "$0" >> "$FAKE_JOURNAL"\nexit 99\n'
    for tool in ("curl", "gh", "git", "glab"):
        install_executable(fake_bin, tool, body)

    result = run_script(
        "reference.py",
        "--describe",
        fake_bin=fake_bin,
        env={"FAKE_JOURNAL": str(call_journal)},
    )
    contract = parse_json_output(result)

    assert result.returncode == 0
    Draft202012Validator(COMMAND_CONTRACT_SCHEMA).validate(contract)
    assert contract["command"] == "reference.py"
    assert contract["report_kind"] == "reference_next"
    assert contract["requires_authorities"] == []
    assert read_journal(call_journal) == []


def test_reference_schema_keeps_legacy_pointer_definitions_available():
    """The proposition addition does not replace the existing 1.x pointer owners."""
    definitions = PROPOSITION_SCHEMA["$defs"]

    assert {"choice", "pointer"} <= set(definitions)
    assert definitions["pointer"]["required"] == ["action", "entrypoint", "args"]
