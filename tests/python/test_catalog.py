"""Tests that the declared interface is the real one, and that it is consistent.

Routing only works if an agent can trust the declaration. Two failures would
break that: a command accepting different options than the catalog says, and
`tools.json` drifting from the module it is rendered from. Both are test
failures here rather than a surprise at run time.

Consistency is the other half. Options are declared in capability tiers, so
`--json` means the same thing in all API commands and a command that filters
records always spells it `--search`. These tests assert the tiers hold, which is
what lets a caller learn one interface instead of five.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import MutableSet
from pathlib import Path

import pytest
from conftest import REPO_ROOT, SCRIPT_ROOT, import_script_module, load_module

CATALOG = import_script_module("lib.python.core.catalog")
CLI = import_script_module("lib.python.core.cli")
PROJECT_BINDING = import_script_module("lib.python.core.project_binding")
RENDER = load_module("render_manifest", REPO_ROOT / "tools" / "render_manifest.py")
MANIFEST_PATH = SCRIPT_ROOT.parent / "tools.json"
PYTHON_COMMANDS = tuple(
    sorted(
        {
            *CATALOG.COMMANDS,
            *CATALOG.NODE_LOCAL_COMMANDS,
            *CATALOG.DISCOVERY_COMMANDS,
        }
    )
)


def test_runner_actions_declare_mutation_and_readback() -> None:
    """Expose each runner write and its read-back in the machine contract.
    :returns: None; assertions in the body validate the regression.
    """
    contract = CATALOG.describe("gitlab_runner.py")
    assert contract["mutates"] is True
    assert contract["side_effects"] != "none"
    for action in ("assign", "create", "delete", "tag"):
        verb = contract["subcommands"][action]
        assert verb["mutates"] is True
        assert verb["side_effects"] != "none"
        assert verb["purpose"]
        assert verb["read_back"]
    for action in ("get", "list"):
        verb = contract["subcommands"][action]
        assert verb["mutates"] is False
        assert verb["read_back"]
        assert verb["result_schema"] == "schemas/results/gitlab-runner-read.schema.json"


def _parser_for(script: str) -> argparse.ArgumentParser:
    """Load the actual installed-command or node-local parser.

    :param script: Catalogue command path, including its ``.py`` suffix.
    :returns: Parser constructed by the matching entrypoint or node adapter.
    """
    if script in CATALOG.NODE_LOCAL_COMMANDS:
        node_cli = import_script_module("lib.python.core.kubernetes.node_local_cli")
        return node_cli.parser(script.removesuffix(".py"))
    module = load_module(
        f"entrypoint_{script.removesuffix('.py')}", SCRIPT_ROOT / script
    )
    return module.build_parser()


def _actual_options(script: str) -> MutableSet[str]:
    """Read accepted long options from a command's parser, excluding prose.

    :param script: Catalogue command path to inspect.
    :returns: Long option names accepted by the real parser.
    """
    found: MutableSet[str] = set()

    def visit(parser: argparse.ArgumentParser) -> None:
        """Collect accepted long options from one parser and its subcommands.

        :param parser: Argument parser whose option actions are traversed recursively.

        :returns: None; the helper records behavior through its side effects.
        """
        for action in parser._actions:
            found.update(
                option for option in action.option_strings if option.startswith("--")
            )
            if isinstance(action, argparse._SubParsersAction):
                for subparser in action.choices.values():
                    visit(subparser)

    visit(_parser_for(script))
    return found - {"--help"}


@pytest.mark.parametrize("script", sorted(CATALOG.COMMANDS))
def test_every_command_accepts_the_universal_tier(script: Path) -> None:
    """Require all catalogue-wide flags on every Python command.

    :param script: Catalogued command whose parser is inspected.

    :returns: None; assertions in the body validate the regression.
    """
    actual = _actual_options(script)

    missing = sorted(set(CATALOG.UNIVERSAL_OPTIONS) - actual)

    assert missing == [], f"{script} is missing universal options {missing}"


def test_logging_options_have_one_canonical_spelling() -> None:
    """The catalog owns exactly the three supported diagnostic option names.
    :returns: None; assertions in the body validate the regression.
    """
    logging = {
        option for option in CATALOG.UNIVERSAL_OPTIONS if option.startswith("--log-")
    }

    assert logging == {"--log-file", "--log-format", "--log-level"}
    assert "--logging-level" not in CATALOG.UNIVERSAL_OPTIONS


@pytest.mark.parametrize("script", PYTHON_COMMANDS)
def test_every_python_command_parser_and_manifest_match_its_catalog_contract(
    script: Path,
) -> None:
    """Require parser, catalogue, and rendered manifest option parity.

    :param script: Python command tested across all three contracts.

    :returns: None; assertions in the body validate the regression.
    """
    if script in CATALOG.COMMANDS:
        contract = CATALOG.describe(script)
    elif script in CATALOG.NODE_LOCAL_COMMANDS:
        contract = CATALOG.describe_node(script)
    else:
        contract = CATALOG.describe(script)
    declared = set(contract["options"])
    actual = _actual_options(script)

    assert sorted(declared - actual) == [], f"{script} declares options it lacks"
    assert sorted(actual - declared) == [], f"{script} accepts undeclared options"
    assert set(CATALOG.manifest()["commands"][script]["options"]) == declared


@pytest.mark.parametrize("script", sorted(CATALOG.COMMANDS))
def test_each_capability_brings_its_whole_tier(script: Path) -> None:
    """Require every claimed capability to expose all of its flags.

    :param script: Catalogued command whose capability tiers are checked.

    :returns: None; assertions in the body validate the regression.
    """
    actual = _actual_options(script)

    for capability in CATALOG.COMMANDS[script]["capabilities"]:
        expected = set(CATALOG.CAPABILITY_OPTIONS[capability])
        assert expected <= actual, (
            f"{script} claims {capability} without {expected - actual}"
        )


def test_a_command_without_a_capability_does_not_offer_its_options() -> None:
    """Otherwise the tiers carry no information.
    :returns: None; assertions in the body validate the regression.
    """
    for script, entry in CATALOG.COMMANDS.items():
        actual = _actual_options(script)
        for capability, options in CATALOG.CAPABILITY_OPTIONS.items():
            if capability in entry["capabilities"]:
                continue
            leaked = sorted(set(options) & actual)
            assert leaked == [], (
                f"{script} offers {leaked} without claiming {capability}"
            )


def test_every_command_describes_itself_without_credentials() -> None:
    """`--describe` must answer before any authority is contacted.
    :returns: None; assertions in the body validate the regression.
    """
    for script in sorted(CATALOG.COMMANDS):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_ROOT / script), "--describe"],
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
            env={"PATH": "/nonexistent", "HOME": "/nonexistent"},
        )
        contract = json.loads(result.stdout)
        assert contract["command"] == script
        assert contract["kind"] == "command_contract"
        assert contract["report_kind"] == CATALOG.COMMANDS[script]["kind"]
        assert set(contract["options"]) == set(CATALOG.options_for(script))


def test_the_rendered_manifest_matches_the_module() -> None:
    """`tools.json` is generated, so a stale copy must fail rather than mislead.

        Byte equality, matching `tools/render_manifest.py --check`. Comparing parsed
        JSON here while the tool compared bytes let a hand-reindented manifest be
        current to one authority and stale to the other.

    :returns: None; assertions in the body validate the regression.
    """
    assert MANIFEST_PATH.read_text(encoding="utf-8") == RENDER.render(REPO_ROOT), (
        "tools.json is stale; run tools/render_manifest.py"
    )


def test_the_manifest_routes_every_command_and_nothing_else() -> None:
    """Routing that omits a command sends an agent to read prose instead.
    :returns: None; assertions in the body validate the regression.
    """
    manifest = CATALOG.manifest()

    declared = (
        set(CATALOG.COMMANDS)
        | set(CATALOG.NODE_LOCAL_COMMANDS)
        | set(CATALOG.READ_COMMANDS)
        | set(CATALOG.DISCOVERY_COMMANDS)
    )
    assert set(manifest["commands"]) == declared
    assert set(manifest["routing"].values()) == declared
    for command in CATALOG.READ_COMMANDS:
        contract = manifest["commands"][command]
        assert (SCRIPT_ROOT.parent / command).is_file()
        assert contract["options"] and contract["required_tools"]
        assert contract["read_only"] is True


@pytest.mark.parametrize("script", PYTHON_COMMANDS)
@pytest.mark.parametrize("secret_option", ("--token", "--password"))
def test_python_parsers_reject_direct_credential_values_before_resolution(
    script: Path, secret_option: str
) -> None:
    """Reject direct secret flags before target credential resolution.

    :param script: Python command parser under test.
    :param secret_option: Direct token or password flag that must be rejected.

    :returns: None; assertions in the body validate the regression.
    """
    with pytest.raises(SystemExit) as rejected:
        _parser_for(script).parse_args([secret_option, "private-value"])

    assert rejected.value.code == 2


@pytest.mark.parametrize("script", sorted(CATALOG.NODE_LOCAL_COMMANDS))
def test_node_local_commands_publish_their_distinct_interface(script: Path) -> None:
    """Require node-local parser options and access route to match the contract.

    :param script: Node-local command whose parser and proposition are compared.

    :returns: None; assertions in the body validate the regression.
    """
    node_cli = import_script_module("lib.python.core.kubernetes.node_local_cli")
    kind = script.removesuffix(".py")
    actual = {
        option
        for action in node_cli.parser(kind)._actions
        for option in action.option_strings
        if option.startswith("--")
    } - {"--help"}
    contract = CATALOG.describe_node(script)
    declared = set(contract["options"])

    assert actual == declared
    assert "--target" not in actual
    assert contract["requires_authorities"] == ["kubernetes"]
    assert set(contract["access_protocol"]) == {"kubernetes"}
    assert contract["execution_surface"] == (
        "Kubernetes API and one existing Pod on the selected node"
    )
    assert set(contract["options"]) == declared


def test_each_command_declares_the_authorities_it_needs_and_their_protocol() -> None:
    """An agent must know what access a command needs before running it.
    :returns: None; assertions in the body validate the regression.
    """
    for script, entry in CATALOG.COMMANDS.items():
        assert entry["requires"], f"{script} declares no authority"
        for authority in entry["requires"]:
            assert authority in CATALOG.AUTHORITIES
            assert authority in CATALOG.ACCESS_PROTOCOL, (
                f"{authority} has no declared resolution chain"
            )
        contract = CATALOG.describe(script)
        assert set(contract["access_protocol"]) == set(entry["requires"])


def test_the_kubernetes_chain_declares_ambient_fallback_after_selected_sources() -> (
    None
):
    """The published order matches the effective kubeconfig source binder.
    :returns: None; assertions in the body validate the regression.
    """
    chain = [step["source"] for step in CATALOG.ACCESS_PROTOCOL["kubernetes"]]

    assert chain[0] == "target:kubernetes.kubeconfigs"
    assert "target:kubernetes.kubeconfig" in chain
    assert "binding:kubernetes.sources" in chain
    assert chain[-2:] == ["env:KUBECONFIG", "kubectl-default:~/.kube/config"]
    assert "first match wins" in CATALOG.ACCESS_PROTOCOL["rule"]


def test_the_manifest_is_committed_and_valid_json() -> None:
    """Require the shipped skill manifest to exist and identify its kind.
    :returns: None; assertions in the body validate the regression.
    """
    assert MANIFEST_PATH.is_file(), "tools.json must ship with the skill"
    assert json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["kind"] == (
        "skill_manifest"
    )


def test_every_report_kind_the_cli_can_produce_is_a_declared_command() -> None:
    """`--describe` and required options are looked up by kind, so each must exist.

        The lookup tolerates an undeclared kind rather than raising, which is right
        for a caller-supplied collector but would hide a real command falling out of
        the catalog. This is what closes that.

    :returns: None; assertions in the body validate the regression.
    """
    produced = {CLI.ACCESS_CHECK_KIND, *CLI.COLLECTOR_KINDS.values()}

    assert produced <= set(CATALOG.COMMAND_BY_KIND)
    assert len(CATALOG.COMMAND_BY_KIND) == len(CATALOG.COMMANDS)
    for kind in produced:
        assert CATALOG.COMMAND_BY_KIND[kind] in CATALOG.COMMANDS


def test_mutating_commands_are_identified_in_the_manifest() -> None:
    """An agent must see the write boundary before it chooses a command.
    :returns: None; assertions in the body validate the regression.
    """
    manifest = CATALOG.manifest()
    assert manifest["read_only"] is False
    for script, entry in CATALOG.COMMANDS.items():
        assert manifest["commands"][script]["read_only"] is not entry.get(
            "mutates", False
        )
        if entry.get("mutates", False):
            command = manifest["commands"][script]
            assert command["subcommands"] or {
                "--apply",
                "--confirm-plan",
            } <= set(command["options"])


def test_the_declared_github_chain_is_the_chain_the_code_resolves(
    target_file: Path,
) -> None:
    """Require enterprise and dotcom credential chains to match the binder.

    :param target_file: Fixture target selecting an Enterprise GitHub host.

    :returns: None; assertions in the body validate the regression.
    """
    target_mod = import_script_module("lib.python.core.target")
    target = target_mod.load_target(target_file)  # host: github.example.test

    declared = {
        step["source"]: step["when"] for step in CATALOG.ACCESS_PROTOCOL["github"]
    }
    enterprise = "env:" + " or ".join(CATALOG.GITHUB_ENTERPRISE_VARIABLES)
    dotcom = "env:" + " or ".join(CATALOG.GITHUB_CLOUD_VARIABLES)

    assert enterprise in declared
    assert CATALOG.GITHUB_DOTCOM_HOST in declared[dotcom]
    # The declaration says a non-dotcom host uses the Enterprise variables.
    assert CATALOG.github_variables(target.github.host) == (
        CATALOG.GITHUB_ENTERPRISE_VARIABLES
    )
    assert CATALOG.github_variables(CATALOG.GITHUB_DOTCOM_HOST) == (
        CATALOG.GITHUB_CLOUD_VARIABLES
    )


def test_the_declared_gitlab_variables_are_the_ones_resolved() -> None:
    """One list, consumed by the binder and published by the manifest.
    :returns: None; assertions in the body validate the regression.
    """
    credentials = import_script_module("lib.python.access.gitlab_credentials")
    access = import_script_module("lib.python.access.gitlab")
    declared = [step["source"] for step in CATALOG.ACCESS_PROTOCOL["gitlab"]]

    for name in CATALOG.GITLAB_VARIABLES:
        assert any(name in source for source in declared), name
    assert credentials.GITLAB_VARIABLES is CATALOG.GITLAB_VARIABLES
    assert access.GITLAB_VARIABLES is CATALOG.GITLAB_VARIABLES


def test_the_declared_target_protocol_is_the_chain_the_code_searches() -> None:
    """`TARGET_PROTOCOL` is published, so reordering it must not be free.
    :returns: None; assertions in the body validate the regression.
    """
    declared = [step["source"] for step in CATALOG.TARGET_PROTOCOL]
    searched = [source for source, _path in PROJECT_BINDING.target_candidates()]

    # No CI_SKILLS_TARGET in this environment, so that tier is absent from the
    # live chain; the remaining order must be exactly the declared one.
    assert [step for step in declared if step != "env:CI_SKILLS_TARGET"] == searched
    assert [step["location"] for step in CATALOG.TARGET_PROTOCOL][1:] == [
        f"./{CATALOG.PROJECT_DIR}/{CATALOG.TARGET_FILENAME}",
        f"~/{CATALOG.PROJECT_DIR}/{CATALOG.TARGET_FILENAME}",
    ]


def test_a_declared_required_option_is_enforced_before_any_authority() -> None:
    """`required_options` is published, so it has to be what actually blocks.
    :returns: None; assertions in the body validate the regression.
    """
    import argparse

    args = argparse.Namespace(job_url=None)

    assert CATALOG.missing_required_options("gitlab_job.py", args) == ["--job-url"]
    assert CATALOG.missing_required_options("storage_report.py", args) == []
    args.job_url = "https://gitlab.example.test/g/p/-/jobs/1"
    assert CATALOG.missing_required_options("gitlab_job.py", args) == []
