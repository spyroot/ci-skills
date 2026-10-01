"""Tests that the declared interface is the real one, and that it is consistent.

Routing only works if an agent can trust the declaration. Two failures would
break that: a command accepting different options than the catalog says, and
`tools.json` drifting from the module it is rendered from. Both are test
failures here rather than a surprise at run time.

Consistency is the other half. Options are declared in capability tiers, so
`--json` means the same thing in all five commands and a command that filters
records always spells it `--search`. These tests assert the tiers hold, which is
what lets a caller learn one interface instead of five.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest
from conftest import REPO_ROOT, SCRIPT_ROOT, import_script_module

CATALOG = import_script_module("core.catalog")
MANIFEST_PATH = SCRIPT_ROOT.parent / "tools.json"
OPTION_PATTERN = re.compile(r"(--[a-z][a-z0-9-]*)")


def _actual_options(script: str) -> set[str]:
    """Read the options a command really accepts, from its own --help."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_ROOT / script), "--help"],
        capture_output=True,
        check=True,
        text=True,
        timeout=30,
    )
    found = set(OPTION_PATTERN.findall(result.stdout))
    # argparse always offers --help; the epilog mentions flags as prose.
    epilog = result.stdout.split("options:", 1)[-1]
    return {option for option in found if option != "--help"} & set(
        OPTION_PATTERN.findall(epilog)
    )


@pytest.mark.parametrize("script", sorted(CATALOG.COMMANDS))
def test_every_command_accepts_the_universal_tier(script):
    """`--json` and its siblings mean the same thing in every command."""
    actual = _actual_options(script)

    missing = sorted(set(CATALOG.UNIVERSAL_OPTIONS) - actual)

    assert missing == [], f"{script} is missing universal options {missing}"


@pytest.mark.parametrize("script", sorted(CATALOG.COMMANDS))
def test_declared_options_are_the_options_the_command_accepts(script):
    """The catalog is the interface, so it may not drift from argparse."""
    declared = set(CATALOG.options_for(script))
    actual = _actual_options(script)

    assert sorted(declared - actual) == [], f"{script} declares options it lacks"
    assert sorted(actual - declared) == [], f"{script} accepts undeclared options"


@pytest.mark.parametrize("script", sorted(CATALOG.COMMANDS))
def test_each_capability_brings_its_whole_tier(script):
    """A command claiming a capability accepts every option in that tier."""
    actual = _actual_options(script)

    for capability in CATALOG.COMMANDS[script]["capabilities"]:
        expected = set(CATALOG.CAPABILITY_OPTIONS[capability])
        assert expected <= actual, (
            f"{script} claims {capability} without {expected - actual}"
        )


def test_a_command_without_a_capability_does_not_offer_its_options():
    """Otherwise the tiers carry no information."""
    for script, entry in CATALOG.COMMANDS.items():
        actual = _actual_options(script)
        for capability, options in CATALOG.CAPABILITY_OPTIONS.items():
            if capability in entry["capabilities"]:
                continue
            leaked = sorted(set(options) & actual)
            assert leaked == [], (
                f"{script} offers {leaked} without claiming {capability}"
            )


def test_every_command_describes_itself_without_credentials():
    """`--describe` must answer before any authority is contacted."""
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


def test_the_rendered_manifest_matches_the_module():
    """`tools.json` is generated, so a stale copy must fail rather than mislead."""
    rendered = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert rendered == json.loads(json.dumps(CATALOG.manifest())), (
        "tools.json is stale; re-render it from core.catalog"
    )


def test_the_manifest_routes_every_command_and_nothing_else():
    """Routing that omits a command sends an agent to read prose instead."""
    manifest = CATALOG.manifest()

    assert set(manifest["commands"]) == set(CATALOG.COMMANDS)
    assert set(manifest["routing"].values()) == set(CATALOG.COMMANDS)


def test_each_command_declares_the_authorities_it_needs_and_their_protocol():
    """An agent must know what access a command needs before running it."""
    for script, entry in CATALOG.COMMANDS.items():
        assert entry["requires"], f"{script} declares no authority"
        for authority in entry["requires"]:
            assert authority in CATALOG.AUTHORITIES
            assert authority in CATALOG.ACCESS_PROTOCOL, (
                f"{authority} has no declared resolution chain"
            )
        contract = CATALOG.describe(script)
        assert set(contract["access_protocol"]) == set(entry["requires"])


def test_the_kubernetes_chain_prefers_the_target_over_the_ambient_default():
    """The declared path must win, or a cold run aims at another cluster."""
    chain = [step["source"] for step in CATALOG.ACCESS_PROTOCOL["kubernetes"]]

    assert chain[0] == "target:kubernetes.kubeconfigs"
    assert chain.index("env:KUBECONFIG") < chain.index("kubectl-default:~/.kube/config")
    assert "first match wins" in CATALOG.ACCESS_PROTOCOL["rule"]


def test_the_manifest_is_committed_and_valid_json():
    assert MANIFEST_PATH.is_file(), "tools.json must ship with the skill"
    assert MANIFEST_PATH.relative_to(REPO_ROOT)
