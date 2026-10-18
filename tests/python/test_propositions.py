"""Regression coverage for compact, graph-shaped capability discovery.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
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
SECTION_VALIDATOR = Draft202012Validator(
    SECTION_SCHEMA,
    registry=Registry().with_resource(
        PROPOSITION_SCHEMA["$id"], Resource.from_contents(PROPOSITION_SCHEMA)
    ),
)


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
        "Native OCP YAML example": {
            "summary": "Node MAC/NIC, disk and network inputs passed unchanged to OpenShift.",
            "pointer": "references/ocp-node-input.md",
        },
        "Native OCP command flags": {
            "summary": "OpenShift flags, ci-skills input mapping and upstream procedure.",
            "pointer": "references/ocp-node-image-flags.md",
        },
    }

    cluster = CATALOG.proposition()["kubernetes"]
    unrelated = CATALOG.proposition("gitlab_runner.py")
    for name, reference in expected.items():
        assert selected["reference"][name] == reference
        assert cluster["reference"][name] == reference
        assert (SCRIPT_ROOT.parent / reference["pointer"]).is_file()
        assert name not in unrelated["reference"]
    assert "toolbox.py" not in selected["tools"]


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


def _progressive_json(*segments: str) -> JSONObject:
    """Read one progressive node through the public offline command.

    :param segments: Navigator path following the ``next`` verb.
    :returns: Decoded successful CLI output with its complete byte bound checked.
    """
    result = run_script("reference.py", "next", *segments, "--json")
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.strip().splitlines()) == 1
    assert len(result.stdout.strip().encode("utf-8")) <= 4096
    payload = parse_json_output(result)
    _validate(payload)
    assert payload["kind"] == "reference_next"
    assert payload["path"] == list(segments)
    return payload


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
    if payload["kind"] == "reference_section":
        SECTION_VALIDATOR.validate(payload)
        assert len(payload["text"].encode("utf-8")) <= 8192
    else:
        _validate(payload)
        assert len(captured.out.strip().encode("utf-8")) <= 4096
    return payload


def _choices(payload: JSONObject) -> dict[str, JSONObject]:
    """Index the current response choices without flattening descendants.

    :param payload: One navigator answer containing its immediate choices.
    :returns: Choice records indexed by their public identifiers.
    """
    choices = payload["choices"]
    assert len({item["id"] for item in choices}) == len(choices)
    return {item["id"]: item for item in choices}


def _local_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> ModuleType:
    """Expose a temporary Markdown reference through the existing registry.

    :param tmp_path: Isolated skill root containing test-only reference text.
    :param monkeypatch: Restores the catalogue and reference root after this test.
    :param content: Native Markdown bytes represented as a UTF-8 string.
    :returns: Navigator module using the real parser and temporary filesystem.
    """
    references = tmp_path / "references"
    references.mkdir()
    (references / "unit-guide.md").write_text(content, encoding="utf-8")
    navigator = import_script_module("lib.python.core.navigate")
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
    return navigator


def _continuation_page(payload: JSONObject) -> int:
    """Read the next page from the actual continuation pointer.

    :param payload: A response with a non-null continuation pointer.
    :returns: Explicit page number offered to the caller.
    """
    pointer = payload["continuation"]
    assert pointer["entrypoint"] == "bin/reference.py"
    args = pointer["args"]
    return int(args[args.index("--page") + 1])


def test_progressive_root_and_group_do_not_dump_descendant_commands() -> None:
    """Root and group expansion expose only the caller's next selections.

    :returns: None; assertions prove the first three discovery levels.
    """
    captured = run_script("reference.py", "next")
    assert captured.returncode == 0, captured.stderr
    assert len(captured.stdout.strip().splitlines()) == 1
    assert len(captured.stdout.strip().encode("utf-8")) <= 4096
    _validate(parse_json_output(captured))
    assert parse_json_output(captured) == _progressive_json()
    human = run_script("reference.py", "next", "--human")
    assert human.returncode == 0, human.stderr
    assert "Available groups" in human.stdout
    assert "gitlab" in human.stdout
    assert "k8s" in human.stdout
    assert not human.stdout.lstrip().startswith("{")

    root = _choices(_progressive_json())
    assert {"gitlab", "k8s"} <= set(root)
    assert "pipeline" not in root
    assert "gitlab_pipeline.py" not in root
    assert all(choice["kind"] == "expand" for choice in root.values())
    assert root["gitlab"]["next"]["args"] == ["next", "gitlab", "--json"]

    group = _choices(_progressive_json("gitlab"))
    assert {"pipeline", "milestone", "runner", "job"} <= set(group)
    assert "create" not in group
    assert group["milestone"]["next"]["args"] == [
        "next",
        "gitlab",
        "milestone",
        "--json",
    ]
    resource = _choices(_progressive_json("gitlab", "milestone"))
    assert "run" in resource
    assert "create" not in resource


def test_progressive_capability_expands_required_then_optional_inputs() -> None:
    """Capability discovery first supplies required inputs and offers expansion.

    :returns: None; assertions prove executable arguments and optional disclosure.
    """
    node = _progressive_json("gitlab", "milestone", "run", "create")
    assert node["capability"]["command"] == "bin/gitlab_milestone.py"
    assert node["capability"]["verb"] == "create"
    choices = _choices(node)
    pointer = choices["create"]["next"]
    assert pointer["action"] == "execute"
    assert pointer["entrypoint"] == "bin/gitlab_milestone.py"
    assert pointer["args"] == ["create", "--title", "<title>", "--json"]
    assert [(item["name"], item["required"]) for item in pointer["inputs"]] == [
        ("title", True)
    ]
    assert choices["options"]["next"]["args"] == [
        "next",
        "gitlab",
        "milestone",
        "run",
        "create",
        "options",
        "--json",
    ]

    expanded = _choices(
        _progressive_json("gitlab", "milestone", "run", "create", "options")
    )["create"]["next"]
    inputs = {item["name"]: item["required"] for item in expanded["inputs"]}
    assert expanded["args"] == pointer["args"]
    assert inputs["title"] is True
    assert inputs["project"] is False
    assert inputs["description-file"] is False
    assert "apply" not in inputs
    assert "confirm-plan" not in inputs
    assert "log-file" not in inputs


def test_progressive_planned_verb_has_no_callable_pointer() -> None:
    """A planned milestone list remains visible without advertising execution.

    :returns: None; assertions prove unavailable choices cannot be followed.
    """
    choices = _choices(_progressive_json("gitlab", "milestone", "run"))
    assert {"create", "update", "adjust-time", "list"} <= set(choices)
    assert choices["create"]["next"]["action"] == "describe"
    assert choices["list"]["available"] is False
    assert choices["list"]["next"] is None
    result = run_script(
        "reference.py", "next", "gitlab", "milestone", "run", "list", "--json"
    )
    assert result.returncode == 2
    assert parse_json_output(result)["error"]["reason"] == "path_unknown"


def test_progressive_known_resource_is_directly_addressable() -> None:
    """An agent already knowing a node need not repeat the navigation sequence.

    :returns: None; assertions compare direct lookup with its canonical path.
    """
    direct = run_script("reference.py", "next", "milestone", "--json")
    assert direct.returncode == 0, direct.stderr
    assert parse_json_output(direct) == _progressive_json("gitlab", "milestone")


def test_progressive_section_returns_own_text_and_child_pointers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Read one section verbatim without loading any descendant's body.

    :param tmp_path: Owns the isolated local Markdown reference.
    :param monkeypatch: Restores the temporary registry and reference root.
    :param capsys: Captures real public command output.
    :returns: None; assertions prove own-text, duplicate anchors and direct reads.
    """
    parent = "## Parent\nOwn text.\n```yaml\n# fenced, not a heading\n```\n\n"
    child = "### Child\nChild-only detail.\n"
    content = (
        "# Guide\nIntro.\n"
        + parent
        + child
        + "#### Grandchild\nDeeper detail.\n## Repeated\nFirst.\n## Repeated\nSecond.\n"
    )
    _local_reference(tmp_path, monkeypatch, content)
    section = _reference_cli(capsys, "get", "unit-guide", "parent", "--json")
    assert section["kind"] == "reference_section"
    assert section["text"] == parent
    assert section["pinned"]
    pointers = _choices(section)
    assert set(pointers) == {"child"}
    assert pointers["child"]["next"]["args"] == ["get", "unit-guide", "child", "--json"]
    assert _reference_cli(capsys, *pointers["child"]["next"]["args"])["text"] == child
    assert _reference_cli(capsys, "get", "unit-guide", "repeated-1", "--json")[
        "text"
    ] == ("## Repeated\nSecond.\n")


def test_progressive_section_continuation_preserves_every_utf8_line(
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
        assert result["continuation"]["args"][:3] == ["get", "unit-guide", "large"]
        following = _continuation_page(result)
        assert following == page + 1
        page = following
    else:
        raise AssertionError("section continuation did not terminate")
    assert len(chunks) > 1
    assert "".join(chunks) == content


def test_progressive_choice_continuation_keeps_json_bounded_without_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Many reference sections retain every choice across bounded responses.

    :param tmp_path: Owns an isolated Markdown guide with thirty sections.
    :param monkeypatch: Restores the temporary registry and reference root.
    :returns: None; assertions prove byte, choice and continuation limits.
    """
    content = "# Guide\nIntro.\n" + "".join(
        f"## Topic {index:02d}\nDetail {index:02d}.\n" for index in range(30)
    )
    navigator = _local_reference(tmp_path, monkeypatch, content)
    seen = []
    page = 1
    for _ in range(32):
        result = navigator.next_document(["read", "unit-guide"], page=page)
        rendered = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        assert len(rendered.encode("utf-8")) <= 4096
        assert json.loads(rendered) == result
        assert len(result["choices"]) <= 12
        seen.extend(item["id"] for item in result["choices"])
        if result["continuation"] is None:
            break
        assert result["continuation"]["args"][:3] == ["next", "read", "unit-guide"]
        following = _continuation_page(result)
        assert following == page + 1
        page = following
    else:
        raise AssertionError("choice continuation did not terminate")
    assert seen == ["guide", *(f"topic-{index:02d}" for index in range(30))]


def test_progressive_discovery_does_not_call_providers(
    fake_bin: Path, call_journal: Path
) -> None:
    """Nested discovery remains offline even when provider executables exist.

    :param fake_bin: Holds fail-fast fake external commands.
    :param call_journal: Records any accidental provider command execution.
    :returns: None; assertions prove navigation performs no external operations.
    """
    body = '#!/bin/sh\nprintf \'{"tool": "%s"}\\n\' "$0" >> "$FAKE_JOURNAL"\nexit 99\n'
    for tool in ("curl", "gh", "git", "glab", "kubectl", "oc"):
        install_executable(fake_bin, tool, body)
    for segments in ((), ("gitlab",), ("gitlab", "milestone", "run", "create")):
        result = run_script(
            "reference.py",
            "next",
            *segments,
            "--json",
            fake_bin=fake_bin,
            env={"FAKE_JOURNAL": str(call_journal)},
        )
        assert result.returncode == 0, result.stderr
        assert parse_json_output(result)["kind"] == "reference_next"
    assert read_journal(call_journal) == []


def test_progressive_oversized_lookup_errors_are_bounded_json() -> None:
    """Rejected caller input cannot overflow the response or escape its schema.

    :returns: None; assertions prove both path and reference errors remain usable.
    """
    oversized = "é" * 2500
    for args, reason in (
        (("next", oversized), "path_unknown"),
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


def test_progressive_get_reports_lookup_page_and_tier_errors(
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
        (("get", "absent", "known"), "reference_unknown"),
        (("get", "unit-guide", "absent"), "anchor_unknown"),
        (("get", "unit-guide", "known", "--page", "0"), "page_out_of_range"),
        (("get", "unit-guide", "known", "--page", "99"), "page_out_of_range"),
        (("get", "unit-guide", "known", "--card"), "tier_unavailable"),
        (("get", "unit-guide", "known", "--part", "body"), "tier_unavailable"),
    )
    for args, reason in cases:
        payload = _reference_cli(capsys, *args, "--json", expected_exit=2)
        assert payload["error"]["reason"] == reason


def test_progressive_search_preserves_literal_query_order_and_continuation(
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
    args = ["next", "read", "unit-guide", "--search", query, "--json"]
    seen = []
    for _ in range(32):
        result = _reference_cli(capsys, *args)
        assert result["query"] == query
        assert result["summary"] == "20 matching sections."
        assert "text" not in result
        seen.extend(item["id"] for item in result["choices"])
        if result["continuation"] is None:
            break
        args = result["continuation"]["args"]
        assert args[args.index("--search") + 1] == query
    else:
        raise AssertionError("search continuation did not terminate")
    assert seen == [f"topic-{index:02d}" for index in range(20)]


def test_progressive_catalogue_walk_offers_only_installed_callable_entrypoints(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every progressive execution pointer belongs to the installed catalogue.

    :param capsys: Captures each offline next response without executing providers.
    :returns: None; assertions prove closed-world reachability and unavailable verbs.
    """
    commands = {
        name: entry
        for name, entry in _commands().items()
        if name not in CATALOG.DISCOVERY_COMMANDS
    }
    expected = {
        (f"bin/{Path(name).name}", verb)
        for name, entry in commands.items()
        for verb in entry.get("subcommands", {}) or {"": {}}
    }
    pending = deque([("next", "--json")])
    visited = set()
    offered = set()
    unavailable = set()
    for _ in range(1000):
        if not pending:
            break
        args = pending.popleft()
        if args in visited:
            continue
        visited.add(args)
        result = _reference_cli(capsys, *args)
        for item in result["choices"]:
            step = item["next"]
            if item.get("available") is False:
                assert step is None
                unavailable.add(item["id"])
                continue
            assert step is not None
            assert (SCRIPT_ROOT.parent / step["entrypoint"]).is_file()
            if step["action"] == "execute":
                prefix = step["args"][0] if step["args"] else ""
                verb = prefix if prefix and not prefix.startswith("--") else ""
                offered.add((step["entrypoint"], verb))
            elif step["args"][0] in ("next", "get"):
                assert step["entrypoint"] == "bin/reference.py"
                pending.append(tuple(step["args"]))
        if result["continuation"] is not None:
            pending.append(tuple(result["continuation"]["args"]))
    else:
        raise AssertionError("progressive catalogue walk did not terminate")
    assert not pending
    assert offered == expected
    assert "list" in unavailable
