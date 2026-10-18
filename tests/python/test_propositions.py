"""Regression coverage for one-answer-per-node capability discovery.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
import shlex
from collections import deque
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
from referencing import Registry, Resource

CATALOG = import_script_module("lib.python.core.catalog")
PROPOSITION_SCHEMA = json.loads(
    (REPO_ROOT / "schemas" / "reference-next.schema.json").read_text(encoding="utf-8")
)
COMMAND_CONTRACT_SCHEMA = json.loads(
    (REPO_ROOT / "schemas" / "command-contract.schema.json").read_text(encoding="utf-8")
)
SECTION_SCHEMA = json.loads(
    (REPO_ROOT / "schemas" / "reference-section.schema.json").read_text(
        encoding="utf-8"
    )
)
WATCH_SCHEMA = json.loads(
    (REPO_ROOT / "schemas" / "gitlab-pipeline-watch.schema.json").read_text(
        encoding="utf-8"
    )
)
REGISTRY = Registry().with_resource(
    PROPOSITION_SCHEMA["$id"], Resource.from_contents(PROPOSITION_SCHEMA)
)
SECTION_VALIDATOR = Draft202012Validator(SECTION_SCHEMA, registry=REGISTRY)
FAKE_TOOL = '#!/bin/sh\nprintf \'{"tool": "%s"}\\n\' "$0" >> "$FAKE_JOURNAL"\nexit 99\n'

if TYPE_CHECKING:
    from types import ModuleType

    import pytest
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
    """Validate one discovery answer against its schema.

    :param payload: Root, node or reference-section answer.

    :returns: None; the helper records behavior through its side effects.
    """
    if payload.get("kind") == "reference_section":
        SECTION_VALIDATOR.validate(payload)
        assert len(payload["text"].encode("utf-8")) <= 8192
    else:
        Draft202012Validator(PROPOSITION_SCHEMA).validate(payload)


def _entries(payload: JSONObject) -> dict[str, JSONObject]:
    """Index the read entries of one answer by name.

    :param payload: One node or section answer.
    :returns: Entries indexed by their display names, which must be unique.
    """
    entries = payload["reference"]
    assert len({item["name"] for item in entries}) == len(entries)
    return {item["name"]: item for item in entries}


def _registered_file(name: str) -> Path:
    """Resolve the file behind one registered reference.

    :param name: Registry key.
    :returns: Installed file the pointer's reference node reads from.
    """
    return SCRIPT_ROOT.parent / CATALOG.PROPOSITION_REFERENCES[name]["pointer"]


def _reference_cli(
    capsys: pytest.CaptureFixture[str], *args: str, expected_exit: int = 0
) -> JSONObject:
    """Invoke the public parser and emitter with isolated filesystem inputs.

    :param capsys: Captures the command's real stdout and stderr streams.
    :param args: Complete public reference command arguments.
    :param expected_exit: Expected successful or lookup-error process status.
    :returns: One bounded, schema-valid machine-readable command answer.
    """
    command = import_script_module("reference")
    status = command.main(list(args))
    captured = capsys.readouterr()
    assert status == expected_exit, captured.err
    assert len(captured.out.strip().splitlines()) == 1
    payload = json.loads(captured.out)
    _validate(payload)
    if payload.get("kind") != "reference_section":
        assert len(captured.out.strip().encode("utf-8")) <= 4096
    return payload


def _local_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> ModuleType:
    """Expose a temporary Markdown reference through the existing registry.

    :param tmp_path: Isolated skill root containing test-only reference text.
    :param monkeypatch: Restores the catalogue and reference root after this test.
    :param content: Native Markdown bytes represented as a UTF-8 string.
    :returns: Reader module using the real parser and temporary filesystem.
    """
    references = tmp_path / "references"
    references.mkdir()
    (references / "unit-guide.md").write_text(content, encoding="utf-8")
    reader = import_script_module("lib.python.core.navigate_references")
    monkeypatch.setattr(reader, "SKILL_ROOT", tmp_path)
    monkeypatch.setattr(
        CATALOG,
        "PROPOSITION_REFERENCES",
        {
            "Unit guide": {
                "summary": "Isolated section reading.",
                "pointer": "references/unit-guide.md",
            }
        },
    )
    return reader


def _page_of(continuation: str) -> int:
    """Read the page number from a continuation call.

    :param continuation: The next call a bounded answer offers.
    :returns: Explicit page number in that call.
    """
    args = shlex.split(continuation)
    return int(args[args.index("--page") + 1])


def test_root_clusters_offer_every_manifest_command_by_declared_authority() -> None:
    """Require root discovery to retain every installed command registry.

    :returns: None; assertions prove each manifest command is offered once or more.
    """
    root = CATALOG.proposition()
    commands = _commands()

    _validate(root)
    assert root["schema_version"] == "3.0"
    assert set(root) == {"schema_version", *CATALOG.PROPOSITION_CLUSTERS, "offline"}

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
        for command in node["tools"]:
            assert _installed_command_path(command).is_file()
        for item in node["reference"]:
            assert item["pointer"] == f"get {CATALOG.reference_slug(item['name'])}"
            assert _registered_file(item["name"]).is_file()

    offline = root["offline"]
    expected_offline = sorted(
        command for command in commands if not CATALOG.command_authorities(command)
    )
    assert set(offline) == {"tools", "reference"}
    assert offline["tools"] == {command: {} for command in expected_offline}
    offered.update(offline["tools"])
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
    assert cluster == {"schema_version": "3.0", **root["glab"]}
    assert selected["tools"] == {
        script: {}
        for script in [command, *CATALOG.COMMANDS[command].get("related", ())]
    }
    entries = _entries(selected)
    assert entries["mcp"]["pointer"] == "get mcp"
    assert entries["mcp"]["summary"] == CATALOG.PROPOSITION_REFERENCES["mcp"]["summary"]
    assert list(entries) == [item["name"] for item in selected["reference"]]
    compact = json.dumps(selected, separators=(",", ":"), ensure_ascii=False)
    assert len(compact.encode("utf-8")) <= 4096


def test_tool_pod_proposition_offers_installed_usage() -> None:
    """Offer this installed procedure within the immediate proposition node.

    :returns: None; assertions check task references and node-local discovery.
    """
    selected = CATALOG.proposition("prepare_tool_pod.py")
    expected = {
        "name": "Tool Pod preparation",
        "summary": "Tool input, preparation, reuse and removal examples.",
        "pointer": "get tool-pod-preparation",
    }

    assert _entries(selected)["Tool Pod preparation"] == expected
    cluster = CATALOG.proposition()["kubernetes"]
    assert _entries(cluster)["Tool Pod preparation"] == expected
    assert "create_selfhosted_runner.py" not in selected["tools"]
    unrelated = CATALOG.proposition("gitlab_runner.py")
    assert "Tool Pod preparation" not in _entries(unrelated)


def test_podman_proposition_offers_installed_readiness_procedure() -> None:
    """Offer this installed procedure within the immediate proposition node.

    :returns: None; assertions check task references and node-local discovery.
    """
    selected = CATALOG.proposition("image_build_readiness.py")
    expected = {
        "Nested Podman readiness": {
            "summary": "Podman probe inputs, planning, execution and evidence.",
            "pointer": "get nested-podman-readiness",
        },
        "OpenShift SCC access": {
            "summary": "Discover SCCs, inspect permission and read the admitted constraint.",
            "pointer": "get openshift-scc-access",
        },
    }

    root = CATALOG.proposition()
    cluster = root["kubernetes"]
    unrelated = CATALOG.proposition("gitlab_runner.py")
    for payload in (root, selected, unrelated):
        _validate(payload)
    for name, record in expected.items():
        assert _entries(selected)[name] == {"name": name, **record}
        assert _entries(cluster)[name] == {"name": name, **record}
        assert name not in _entries(unrelated)
    assert "create_selfhosted_runner.py" not in selected["tools"]


def test_iso_proposition_offers_native_input_and_destination_guidance() -> None:
    """Offer this installed procedure within the immediate proposition node.

    :returns: None; assertions check task references and node-local discovery.
    """
    selected = CATALOG.proposition("ocp_node_onboard.py")
    expected = {
        "Native OCP YAML example": {
            "summary": "Node MAC/NIC, disk and network inputs passed unchanged to OpenShift.",
            "pointer": "get native-ocp-yaml-example",
        },
        "Native OCP command flags": {
            "summary": "OpenShift flags, ci-skills input mapping and upstream procedure.",
            "pointer": "get native-ocp-command-flags",
        },
    }

    root = CATALOG.proposition()
    cluster = root["kubernetes"]
    unrelated = CATALOG.proposition("gitlab_runner.py")
    for payload in (root, selected, unrelated):
        _validate(payload)
    for name, reference in expected.items():
        assert _entries(selected)[name] == {"name": name, **reference}
        assert _entries(cluster)[name] == {"name": name, **reference}
        assert _registered_file(name).is_file()
        assert name not in _entries(unrelated)
    assert "toolbox.py" not in selected["tools"]


def test_reference_cli_expands_nodes_without_fetching_optional_references(
    fake_bin: Path, call_journal: Path, tmp_path: Path
) -> None:
    """Require node answers to expose pointers without provider calls.

    :param fake_bin: Directory of fail-fast curl and provider command fakes.
    :param call_journal: Path recording any accidental external command.
    :param tmp_path: Directory for a log path that dry-run must not create.

    :returns: None; assertions in the body validate the regression.
    """
    for tool in ("curl", "gh", "git", "glab"):
        install_executable(fake_bin, tool, FAKE_TOOL)
    environment = {"FAKE_JOURNAL": str(call_journal)}

    root = run_script("reference.py", "--json", fake_bin=fake_bin, env=environment)
    selected = run_script(
        "reference.py", "gitlab_runner.py", "--yaml", fake_bin=fake_bin, env=environment
    )
    piped = run_script("reference.py", fake_bin=fake_bin, env=environment)
    human = run_script("reference.py", "glab", "--human", fake_bin=fake_bin)
    help_result = run_script("reference.py", "--help", fake_bin=fake_bin)
    log_path = tmp_path / "reference.log"
    dry_run = run_script(
        "reference.py",
        "--dry-run",
        "--log-file",
        log_path,
        fake_bin=fake_bin,
        env=environment,
    )

    assert root.returncode == 0, root.stderr
    assert len(root.stdout.strip().splitlines()) == 1
    assert parse_json_output(root) == CATALOG.proposition()
    assert selected.returncode == 0, selected.stderr
    assert yaml.safe_load(selected.stdout) == CATALOG.proposition("gitlab_runner.py")
    assert piped.returncode == 0, piped.stderr
    assert parse_json_output(piped) == CATALOG.proposition()
    assert human.returncode == 0, human.stderr
    assert "Run" in human.stdout and "Read" in human.stdout
    assert "gitlab_runner.py" in human.stdout
    assert "reference.py get mcp" in human.stdout
    assert not human.stdout.lstrip().startswith("{")
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
    for tool in ("curl", "gh", "git", "glab"):
        install_executable(fake_bin, tool, FAKE_TOOL)

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


def test_reference_schema_has_one_response_shape_and_keeps_shared_pointers() -> None:
    """The schema offers only the 3.0 shapes; the pointer definitions stay shared.

    :returns: None; assertions prove the retired shapes are gone and the owner remains.
    """
    definitions = PROPOSITION_SCHEMA["$defs"]
    branches = PROPOSITION_SCHEMA["oneOf"]

    assert len(branches) == 2
    assert not any("choices" in branch.get("properties", {}) for branch in branches)
    assert definitions["node"]["properties"]["schema_version"] == {"const": "3.0"}
    assert definitions["node"]["properties"]["reference"]["type"] == "array"
    assert definitions["entry"]["required"] == ["name", "summary", "pointer"]
    assert {"choice", "pointer", "input"} <= set(definitions)
    watch = json.dumps(WATCH_SCHEMA)
    assert "reference-next.schema.json#/$defs/choice" in watch
    assert "reference-next.schema.json#/$defs/pointer" in watch
    assert SECTION_SCHEMA["properties"]["schema_version"] == {"const": "3.0"}
    assert "choices" not in SECTION_SCHEMA["properties"]


def test_next_verb_is_retired() -> None:
    """The forced walk through next, run and read no longer exists.

    :returns: None; assertions prove the old verb answers a bounded lookup error.
    """
    result = run_script("reference.py", "next", "--json")

    assert result.returncode == 2
    payload = parse_json_output(result)
    _validate(payload)
    assert payload["error"] == {"reason": "path_unknown", "input": "next"}
    assert payload["tools"] == {} and payload["reference"] == []


def test_get_lists_sections_then_reads_own_text_and_subsections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reference is a node: its sections, then one section's own text.

    :param tmp_path: Owns the isolated local Markdown reference.
    :param monkeypatch: Restores the temporary registry and reference root.
    :param capsys: Captures real public command output.
    :returns: None; assertions prove listing, own-text, duplicates and direct reads.
    """
    parent = "## Parent\nOwn text.\n```yaml\n# fenced, not a heading\n```\n\n"
    child = "### Child\nChild-only detail.\n"
    content = (
        "# Guide\nIntro.\n"
        + parent
        + child
        + "#### Grandchild\nDeeper detail.\n## Repeated\nFirst.\n## Repeated\nSecond.\n"
    )
    reader = _local_reference(tmp_path, monkeypatch, content)

    listing = _reference_cli(capsys, "get", "unit-guide", "--json")
    assert listing["tools"] == {}
    assert listing["continuation"] is None
    top = _entries(listing)
    assert list(top) == ["Guide"]
    assert top["Guide"]["pointer"] == "get unit-guide guide"
    assert top["Guide"]["summary"] == "Read when you need Guide."

    guide = _reference_cli(capsys, *shlex.split(top["Guide"]["pointer"]), "--json")
    assert guide["kind"] == "reference_section"
    assert guide["document"] == "unit-guide"
    assert guide["text"] == "# Guide\nIntro.\n"
    assert guide["pinned"] == f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"
    assert [item["name"] for item in guide["reference"]] == [
        "Parent",
        "Repeated",
        "Repeated",
    ]

    revision = "a" * 40
    monkeypatch.setattr(
        reader,
        "skill_identity",
        lambda _root: {"revision": {"verified": True, "value": revision}},
    )
    section = _reference_cli(capsys, "get", "unit-guide", "parent", "--json")
    assert section["text"] == parent
    assert section["pinned"] == revision
    children = _entries(section)
    assert set(children) == {"Child"}
    assert children["Child"]["pointer"] == "get unit-guide child"
    assert (
        _reference_cli(capsys, "get", "unit-guide", "child", "--json")["text"] == child
    )
    assert _reference_cli(capsys, "get", "unit-guide", "repeated-1", "--json")[
        "text"
    ] == ("## Repeated\nSecond.\n")


def test_section_continuation_preserves_every_utf8_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A long selected section is paged rather than silently truncated.

    :param tmp_path: Owns a reference longer than one allowed text page.
    :param monkeypatch: Restores the temporary registry and reference root.
    :param capsys: Captures section bytes from the public command.
    :returns: None; assertions prove bounded text and exact reconstruction.
    """
    content = "# Large\n" + "".join(
        f"{index:03d}: " + "é" * 70 + "\n" for index in range(160)
    )
    _local_reference(tmp_path, monkeypatch, content)
    chunks = []
    page = 1
    for _ in range(32):
        result = _reference_cli(
            capsys, "get", "unit-guide", "large", "--page", str(page), "--json"
        )
        assert len(result["text"].encode("utf-8")) <= 8192
        assert result["text"].endswith("\n")
        chunks.append(result["text"])
        if result["continuation"] is None:
            break
        assert result["continuation"].startswith("get unit-guide large --page ")
        following = _page_of(result["continuation"])
        assert following == page + 1
        page = following
    else:
        raise AssertionError("section continuation did not terminate")
    assert len(chunks) > 1
    assert "".join(chunks) == content


def test_reference_entries_page_without_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Many sections retain every entry across bounded answers.

    :param tmp_path: Owns an isolated guide with thirty top-level sections.
    :param monkeypatch: Restores the temporary registry and reference root.
    :returns: None; assertions prove byte, entry and continuation limits.
    """
    content = "".join(
        f"# Topic {index:02d}\nDetail {index:02d}.\n" for index in range(30)
    )
    reader = _local_reference(tmp_path, monkeypatch, content)
    seen = []
    page = 1
    for _ in range(32):
        result = reader.reference_document("unit-guide", page=page)
        rendered = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        assert len(rendered.encode("utf-8")) <= 4096
        _validate(json.loads(rendered))
        assert len(result["reference"]) <= 12
        seen.extend(item["name"] for item in result["reference"])
        if result["continuation"] is None:
            break
        assert result["continuation"].startswith("get unit-guide --page ")
        following = _page_of(result["continuation"])
        assert following == page + 1
        page = following
    else:
        raise AssertionError("entry continuation did not terminate")
    assert seen == [f"Topic {index:02d}" for index in range(30)]


def test_search_preserves_literal_query_order_and_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Boundary-length literal searches retain all matches without dumping text.

    :param tmp_path: Owns one reference with enough search hits to require pages.
    :param monkeypatch: Restores the temporary reference registration.
    :param capsys: Captures machine answers produced by the public parser and emitter.
    :returns: None; assertions prove query preservation, order and summary fallback.
    """
    query = "q" * 98 + "[]"
    content = (
        "# Guide\nIntro.\n"
        + "".join(f"## Topic {index:02d}\n{query.upper()}\n" for index in range(20))
        + "## Decoy\n"
        + "q" * 100
        + "\n"
    )
    _local_reference(tmp_path, monkeypatch, content)
    args = ["get", "unit-guide", "--search", query, "--json"]
    seen = []
    for _ in range(32):
        result = _reference_cli(capsys, *args)
        assert result["query"] == query
        assert result["tools"] == {}
        assert "text" not in result
        for item in result["reference"]:
            assert item["summary"] == "Read for the passage matching the query."
        seen.extend(item["name"] for item in result["reference"])
        if result["continuation"] is None:
            break
        args = [*shlex.split(result["continuation"]), "--json"]
        assert args[args.index("--search") + 1] == query
    else:
        raise AssertionError("search continuation did not terminate")
    assert seen == [f"Topic {index:02d}" for index in range(20)]


def test_discovery_does_not_call_providers(fake_bin: Path, call_journal: Path) -> None:
    """Node and reference reads remain offline even when provider executables exist.

    :param fake_bin: Holds fail-fast fake external commands.
    :param call_journal: Records any accidental provider command execution.
    :returns: None; assertions prove discovery performs no external operations.
    """
    for tool in ("curl", "gh", "git", "glab", "kubectl", "oc"):
        install_executable(fake_bin, tool, FAKE_TOOL)
    for args in ((), ("glab",), ("gitlab_runner.py",), ("get", "mcp")):
        result = run_script(
            "reference.py",
            *args,
            "--json",
            fake_bin=fake_bin,
            env={"FAKE_JOURNAL": str(call_journal)},
        )
        assert result.returncode == 0, result.stderr
        _validate(parse_json_output(result))
    assert read_journal(call_journal) == []


def test_oversized_lookup_errors_are_bounded_json() -> None:
    """Rejected caller input cannot overflow the answer or escape its schema.

    :returns: None; assertions prove node and reference errors remain usable.
    """
    oversized = "é" * 2500
    for args, reason in (
        ((oversized,), "path_unknown"),
        (("get", oversized), "reference_unknown"),
        (("get", oversized, "heading"), "reference_unknown"),
    ):
        result = run_script("reference.py", *args, "--json")
        assert result.returncode == 2, result.stderr
        assert len(result.stdout.strip().splitlines()) == 1
        assert len(result.stdout.strip().encode("utf-8")) <= 4096
        payload = parse_json_output(result)
        _validate(payload)
        assert payload["error"]["reason"] == reason
        assert "Traceback" not in result.stderr


def test_get_reports_lookup_page_and_tier_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Public get failures preserve the documented machine-readable error classes.

    :param tmp_path: Owns the known reference and anchor.
    :param monkeypatch: Restores temporary filesystem registration.
    :param capsys: Captures public command error envelopes.
    :returns: None; assertions prove reference, anchor, page and tier failure output.
    """
    _local_reference(tmp_path, monkeypatch, "# Known\nOwn text.\n")
    cases = (
        (("get", "absent"), "reference_unknown"),
        (("get", "absent", "known"), "reference_unknown"),
        (("get", "unit-guide", "absent"), "anchor_unknown"),
        (("get", "unit-guide", "known", "--page", "0"), "page_out_of_range"),
        (("get", "unit-guide", "known", "--page", "99"), "page_out_of_range"),
        (("get", "unit-guide", "--page", "2"), "page_out_of_range"),
        (("get", "unit-guide", "known", "--card"), "tier_unavailable"),
        (("get", "unit-guide", "known", "--part", "body"), "tier_unavailable"),
        (("get", "unit-guide", "--card"), "tier_unavailable"),
        (("get", "unit-guide", "known", "--search", "own"), "path_unknown"),
        (("get", "unit-guide", "known", "extra"), "path_unknown"),
    )
    for args, reason in cases:
        payload = _reference_cli(capsys, *args, "--json", expected_exit=2)
        assert payload["error"]["reason"] == reason


def test_every_registered_reference_is_a_readable_node(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every registered reference, Markdown or not, answers get as a node.

    :param capsys: Captures each offline answer.
    :returns: None; assertions prove each pointer resolves to sections with text.
    """
    for name in CATALOG.PROPOSITION_REFERENCES:
        slug = CATALOG.reference_slug(name)
        listing = _reference_cli(capsys, "get", slug, "--json")
        assert listing["reference"], name
        first = listing["reference"][0]["pointer"]
        assert first.startswith(f"get {slug} ")
        section = _reference_cli(capsys, *shlex.split(first), "--json")
        assert section["document"] == slug
        assert section["text"]


def test_catalogue_walk_resolves_every_pointer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """From the root, every tool is installed and every pointer answers.

    :param capsys: Captures each offline answer without executing providers.
    :returns: None; assertions prove closed-world reachability and termination.
    """
    pending = deque([("--json",)])
    for cluster in (*CATALOG.PROPOSITION_CLUSTERS, "offline"):
        pending.append((cluster, "--json"))
    for command in _commands():
        pending.append((command, "--json"))
    visited: set[tuple[str, ...]] = set()
    tools: set[str] = set()
    for _ in range(2000):
        if not pending:
            break
        args = pending.popleft()
        if args in visited:
            continue
        visited.add(args)
        payload = _reference_cli(capsys, *args)
        nodes = (
            [payload]
            if "tools" in payload or payload.get("kind") == "reference_section"
            else [value for key, value in payload.items() if key != "schema_version"]
        )
        for node in nodes:
            for script in node.get("tools", {}):
                assert _installed_command_path(script).is_file()
                tools.add(script)
            for item in node["reference"]:
                pending.append((*shlex.split(item["pointer"]), "--json"))
            if node.get("continuation"):
                pending.append((*shlex.split(node["continuation"]), "--json"))
    else:
        raise AssertionError("catalogue walk did not terminate")
    assert not pending
    assert tools == set(_commands())
