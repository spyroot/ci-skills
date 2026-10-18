"""Regression coverage for compact, graph-shaped capability discovery.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

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

CATALOG = import_script_module("lib.python.core.catalog")
PROPOSITION_SCHEMA = json.loads(
    (REPO_ROOT / "schemas" / "reference-next.schema.json").read_text(encoding="utf-8")
)
COMMAND_CONTRACT_SCHEMA = json.loads(
    (REPO_ROOT / "schemas" / "command-contract.schema.json").read_text(encoding="utf-8")
)


if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject


def _commands() -> Mapping[str, JSONObject]:
    """Combine every command registry exposed through the proposition graph.

    :returns: Mapping of every installed command name to catalogue data.
    """
    return {
        **CATALOG.COMMANDS,
        **CATALOG.NODE_LOCAL_COMMANDS,
        **CATALOG.READ_COMMANDS,
        **CATALOG.DISCOVERY_COMMANDS,
    }


def _installed_command_path(command: str) -> Path:
    """Resolve one manifest command name within the installed skill layout.

    :param command: Catalogue key, either a bin-prefixed path or bin filename.
    :returns: Installed command path beneath the skill root.
    """
    if command.startswith("bin/"):
        return SCRIPT_ROOT.parent / command
    return SCRIPT_ROOT / command


def _validate(payload: JSONObject) -> None:
    """Validate one discovery node against the versioned proposition schema.

    :param payload: Root, selected cluster, or selected command proposition.

    :returns: None; the helper records behavior through its side effects.
    """
    Draft202012Validator(PROPOSITION_SCHEMA).validate(payload)


def test_root_clusters_offer_every_manifest_command_by_declared_authority() -> None:
    """Require root discovery to retain every installed command registry.

    :returns: None; assertions prove each manifest command is offered once or more.
    """
    root = CATALOG.proposition()
    commands = _commands()

    _validate(root)
    assert root["schema_version"] == "2.0"
    assert set(root) == {
        "schema_version",
        *CATALOG.PROPOSITION_CLUSTERS,
        "offline",
    }

    offered = set()
    for cluster, authority in CATALOG.PROPOSITION_CLUSTERS.items():
        node = root[cluster]
        expected = sorted(
            command
            for command in commands
            if authority in CATALOG.command_authorities(command)
        )
        assert set(node) == {"tools", "reference"}
        assert node["tools"] == {command: {} for command in expected}
        offered.update(node["tools"])
        for command, metadata in node["tools"].items():
            assert metadata == {}
            assert _installed_command_path(command).is_file()
        for reference in node["reference"].values():
            assert (SCRIPT_ROOT.parent / reference["pointer"]).is_file()

    offline = root["offline"]
    expected_offline = sorted(
        command for command in commands if not CATALOG.command_authorities(command)
    )
    assert set(offline) == {"tools", "reference"}
    assert offline["tools"] == {command: {} for command in expected_offline}
    offered.update(offline["tools"])
    for reference in offline["reference"].values():
        assert (SCRIPT_ROOT.parent / reference["pointer"]).is_file()
    assert offered == set(commands)


def test_every_manifest_command_expands_to_a_reachable_proposition() -> None:
    """Require each manifest key to expand from the root proposition graph.

    :returns: None; assertions prove no manifest command is unreachable.
    """
    manifest_commands = CATALOG.manifest()["commands"]
    assert set(manifest_commands) == set(_commands())

    for command in manifest_commands:
        selected = CATALOG.proposition(command)
        _validate(selected)
        assert command in selected["tools"]


def test_selected_cluster_and_command_are_small_schema_valid_propositions() -> None:
    """A selection expands one node while preserving related installed peers.
    :returns: None; assertions in the body validate the regression.
    """
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


def test_tool_pod_proposition_offers_installed_usage() -> None:
    """Offer this installed procedure within the immediate proposition node.

    :returns: None; assertions check task references and node-local discovery.
    """
    selected = CATALOG.proposition("prepare_tool_pod.py")
    expected = {
        "summary": "Tool input, preparation, reuse and removal examples.",
        "pointer": "references/tool-pod.md",
    }

    assert selected["reference"]["Tool Pod preparation"] == expected
    cluster = CATALOG.proposition()["kubernetes"]
    assert cluster["reference"]["Tool Pod preparation"] == expected
    assert "create_selfhosted_runner.py" not in selected["tools"]
    unrelated = CATALOG.proposition("gitlab_runner.py")
    assert "Tool Pod preparation" not in unrelated["reference"]


def test_iso_proposition_offers_native_input_and_destination_guidance() -> None:
    """Offer this installed procedure within the immediate proposition node.

    :returns: None; assertions check task references and node-local discovery.
    """
    selected = CATALOG.proposition("ocp_node_onboard.py")
    expected = {
        "summary": "Native OCP YAML, cluster destination and ISO lifecycle examples.",
        "pointer": "references/ocp-node-image.md",
    }

    assert selected["reference"]["OpenShift node image"] == expected
    cluster = CATALOG.proposition()["kubernetes"]
    assert cluster["reference"]["OpenShift node image"] == expected
    assert "toolbox.py" not in selected["tools"]
    unrelated = CATALOG.proposition("gitlab_runner.py")
    assert "OpenShift node image" not in unrelated["reference"]


def test_reference_cli_expands_nodes_without_fetching_optional_references(
    fake_bin: Path, call_journal: Path, tmp_path: Path
) -> None:
    """Require reference navigation to expose pointers without provider calls.

    :param fake_bin: Directory of fail-fast curl and provider command fakes.
    :param call_journal: Path recording any accidental external command.
    :param tmp_path: Directory for a log path that dry-run must not create.

    :returns: None; assertions in the body validate the regression.
    """
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
    log_path = tmp_path / "reference.log"
    dry_run = run_script(
        "reference.py",
        "--dry-run",
        "--log-file",
        log_path,
        fake_bin=fake_bin,
        env={"FAKE_JOURNAL": str(call_journal)},
    )

    assert root.returncode == 0
    assert parse_json_output(root) == CATALOG.proposition()
    assert selected.returncode == 0
    assert yaml.safe_load(selected.stdout) == CATALOG.proposition("gitlab_runner.py")
    assert help_result.returncode == 0
    assert "node" in help_result.stdout
    for option in CATALOG.describe("reference.py")["options"]:
        assert option in help_result.stdout
    headings = (
        "Summary:",
        "Description:",
        "Examples:",
        "Options:",
        "Output modes:",
        "Usage:",
    )
    assert [help_result.stdout.index(heading) for heading in headings] == sorted(
        help_result.stdout.index(heading) for heading in headings
    )
    assert dry_run.returncode == 0
    assert not log_path.exists()
    assert read_journal(call_journal) == []


def test_reference_describe_is_a_schema_valid_offline_command_contract(
    fake_bin: Path, call_journal: Path
) -> None:
    """Require describe output to validate without external command calls.

    :param fake_bin: Directory of fail-fast curl and provider command fakes.
    :param call_journal: Path recording any accidental external command.

    :returns: None; assertions in the body validate the regression.
    """
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


def test_reference_schema_keeps_legacy_pointer_definitions_available() -> None:
    """The proposition addition does not replace the existing 1.x pointer owners.
    :returns: None; assertions in the body validate the regression.
    """
    definitions = PROPOSITION_SCHEMA["$defs"]

    assert {"choice", "pointer"} <= set(definitions)
    assert definitions["pointer"]["required"] == ["action", "entrypoint", "args"]
