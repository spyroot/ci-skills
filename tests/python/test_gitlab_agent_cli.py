"""Offline parser, intent, and shipped JSON report coverage for the GitLab Agent."""

from __future__ import annotations

import json
from argparse import ArgumentParser
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Final

import pytest
from conftest import import_script_module, parse_json_output, run_script
from jsonschema import Draft202012Validator
from test_propositions import COMMAND_CONTRACT_SCHEMA

CLI = import_script_module("lib.python.core.gitlab.cluster_agent.cli")
CATALOG = import_script_module("lib.python.core.catalog")
MODES = import_script_module("lib.python.core.action_modes")
BINDING = import_script_module("lib.python.core.project_binding")
IDENTITY = import_script_module("lib.python.core.runner_identity")
SCHEMA = import_script_module("lib.python.core.schema")
SELECTION = import_script_module("lib.python.core.gitlab.cluster_agent.selection")
CI_ACCESS = import_script_module("lib.python.core.gitlab.cluster_agent.ci_access")

CONNECT: Final = "connect"
VERIFY: Final = "verify"
DELETE: Final = "delete"
DEFAULT: Final = "default"
LIVE_PLAN: Final = "live-plan"
CONFIRMED: Final = "confirmed"
OFFLINE: Final = "dry-run"
PROJECT_ARG: Final = "--project"
PROJECT_NAME_ARG: Final = "--project-name"
AGENT_NAME_ARG: Final = "--agent-name"
NAMESPACE_ARG: Final = "--namespace"
RUN_ARG: Final = "--run-id"
JOB_ARG: Final = "--job-url"
PLAN_ARG: Final = "--live-plan"
APPLY_ARG: Final = "--apply"
CONFIRM_ARG: Final = "--confirm-plan"
DRY_RUN_ARG: Final = "--dry-run"
JSON_ARG: Final = "--json"
TIMEOUT_ARG: Final = "--timeout"
RECEIPT_ARG: Final = "--receipt-out"
PROJECT: Final = "consumer/project"
PROJECT_SELECTOR: Final = "42"
PROJECT_NAME: Final = "consumer"
AGENT_NAME: Final = "consumer-agent"
NAMESPACE: Final = "consumer-agent-system"
RUN_ID: Final = "run-001"
JOB_URL: Final = f"https://gitlab.example.test/{PROJECT}/-/jobs/42"
CONFIRMATION: Final = "a" * 64
RECEIPT: Final = "agent-receipt.json"
MODE_REJECTION: Final = "agent_verify_requires_read_only_mode"
CONFIRMATION_REQUIRED: Final = "agent_apply_requires_plan_digest_and_run_id"
HELP_ARG: Final = "--help"
DESCRIBE_ARG: Final = "--describe"
MIRROR_COMMAND: Final = "gitlab_mirror.py"
CREDENTIALS_FIELD: Final = "credentials"
CREDENTIALS_HEADING: Final = "Credentials:"
DESCRIPTION_HEADING: Final = "Description:"
EXAMPLES_HEADING: Final = "Examples:"
PASS_STATUS: Final = "PASS"
BLOCKED_STATUS: Final = "BLOCKED"
UNAVAILABLE_KIT: Final = "agent_ci_installed_producer_unavailable"

# Each valid invocation is immutable. Mutation cases change exactly one flag
# on their own copy, with the same selected provider project in all cases.
BASELINE: Final = MappingProxyType(
    {
        DEFAULT: (CONNECT, (), MODES.ActionMode.OFFLINE),
        OFFLINE: (CONNECT, ((DRY_RUN_ARG, None),), MODES.ActionMode.OFFLINE),
        LIVE_PLAN: (CONNECT, ((PLAN_ARG, None),), MODES.ActionMode.LIVE_PLAN),
        CONFIRMED: (
            CONNECT,
            (
                (PROJECT_ARG, PROJECT_SELECTOR),
                (PROJECT_NAME_ARG, f" {PROJECT_NAME} "),
                (AGENT_NAME_ARG, AGENT_NAME),
                (NAMESPACE_ARG, NAMESPACE),
                (RUN_ARG, RUN_ID),
                (APPLY_ARG, None),
                (CONFIRM_ARG, CONFIRMATION),
                (RECEIPT_ARG, RECEIPT),
            ),
            MODES.ActionMode.APPLY,
        ),
        VERIFY: (
            VERIFY,
            ((RUN_ARG, RUN_ID), (JOB_ARG, JOB_URL), (RECEIPT_ARG, RECEIPT)),
            MODES.ActionMode.READ,
        ),
        DELETE: (
            DELETE,
            ((RUN_ARG, RUN_ID), (AGENT_NAME_ARG, AGENT_NAME), (PLAN_ARG, None)),
            MODES.ActionMode.LIVE_PLAN,
        ),
    }
)


def _argv(operation: str, flags: Mapping[str, str | None]) -> list[str]:
    """Render a positional operation followed by the selected option tokens."""
    return [
        operation,
        *(
            token
            for option, value in flags.items()
            for token in ((option,) if value is None else (option, value))
        ),
    ]


def test_agent_parser_and_manifest_expose_the_catalog_contract() -> None:
    """Check the accepted long options and operation names against discovery."""
    parser = CLI.build_parser()
    options = {
        option
        for action in parser._actions
        for option in action.option_strings
        if option.startswith("--") and option != "--help"
    }
    contract = CATALOG.describe(CLI.COMMAND)
    manifest = CATALOG.manifest()["commands"][CLI.COMMAND]
    action = next(action for action in parser._actions if action.dest == "action")

    assert options == set(contract["options"]) == set(manifest["options"])
    assert set(action.choices) == set(contract["subcommands"])


@pytest.fixture(scope="module")
def agent_parser() -> ArgumentParser:
    """Use the actual Agent parser across all independent mode cases."""
    return CLI.build_parser()


@pytest.mark.parametrize("invocation", tuple(BASELINE))
def test_agent_modes_return_the_validated_selected_project_intent(
    agent_parser: ArgumentParser, invocation: str
) -> None:
    """Check defaults, confirmation replay inputs, and explicit names."""
    operation, arguments, expected_mode = BASELINE[invocation]
    flags = dict(arguments)
    args = agent_parser.parse_args(_argv(operation, flags))

    assert CLI.execution_mode(args) is expected_mode
    assert args.timeout == MODES.MAX_ACTION_TIMEOUT
    assert args.project == flags.get(PROJECT_ARG)
    assert args.receipt_out == flags.get(RECEIPT_ARG)
    assert CLI.normalized_intent(args, PROJECT) == {
        "operation": operation,
        "project": PROJECT,
        "project_name": PROJECT_NAME if PROJECT_NAME_ARG in flags else None,
        "agent_name": flags.get(AGENT_NAME_ARG),
        "namespace": flags.get(NAMESPACE_ARG),
        "run_id": flags.get(RUN_ARG),
        "job_url": flags.get(JOB_ARG),
    }


@pytest.mark.parametrize(
    ("invocation", "flag", "value", "reason"),
    (
        (VERIFY, APPLY_ARG, None, MODE_REJECTION),
        (VERIFY, CONFIRM_ARG, CONFIRMATION, MODE_REJECTION),
        (VERIFY, PLAN_ARG, None, MODE_REJECTION),
        (CONFIRMED, RUN_ARG, None, CONFIRMATION_REQUIRED),
        (CONFIRMED, CONFIRM_ARG, None, CONFIRMATION_REQUIRED),
        (CONFIRMED, DRY_RUN_ARG, None, MODES.APPLY_DRY_RUN_CONFLICT),
        (LIVE_PLAN, DRY_RUN_ARG, None, MODES.LIVE_PLAN_CONFLICT),
        (DEFAULT, CONFIRM_ARG, CONFIRMATION, MODES.CONFIRM_REQUIRES_APPLY),
        (DEFAULT, TIMEOUT_ARG, "0", MODES.TIMEOUT_OUT_OF_RANGE),
        (
            DEFAULT,
            TIMEOUT_ARG,
            str(MODES.MAX_ACTION_TIMEOUT + 1),
            MODES.TIMEOUT_OUT_OF_RANGE,
        ),
        (DEFAULT, RECEIPT_ARG, RECEIPT, MODES.RECEIPT_REQUIRES_LIVE_RUN),
        (LIVE_PLAN, RECEIPT_ARG, RECEIPT, MODES.RECEIPT_REQUIRES_LIVE_RUN),
        (VERIFY, DRY_RUN_ARG, None, MODES.RECEIPT_REQUIRES_LIVE_RUN),
        (OFFLINE, "--output-dir", "reports", MODES.OFFLINE_OUTPUT_FORBIDDEN),
        (OFFLINE, "--log-file", "agent.log", MODES.OFFLINE_OUTPUT_FORBIDDEN),
        (VERIFY, RUN_ARG, None, "invalid_specification:run_id:type"),
        (VERIFY, JOB_ARG, None, "invalid_specification:job_url:type"),
        (VERIFY, AGENT_NAME_ARG, AGENT_NAME, "invalid_specification:agent_name:type"),
        (DELETE, RUN_ARG, None, "invalid_specification:run_id:type"),
        (DELETE, AGENT_NAME_ARG, None, "invalid_specification:agent_name:type"),
        (DEFAULT, JOB_ARG, JOB_URL, "invalid_specification:job_url:type"),
        (
            CONFIRMED,
            PROJECT_NAME_ARG,
            " ",
            IDENTITY.IDENTITY_REQUIRED + PROJECT_NAME_ARG,
        ),
    ),
    ids=(
        "verify-apply",
        "verify-confirm",
        "verify-live-plan",
        "apply-no-run",
        "apply-no-confirm",
        "apply-dry-run",
        "plan-dry-run",
        "offline-confirm",
        "zero-timeout",
        "above-maximum-timeout",
        "offline-receipt",
        "live-plan-receipt",
        "verify-offline-receipt",
        "offline-output-directory",
        "offline-log-file",
        "verify-no-run",
        "verify-no-job",
        "verify-agent-name",
        "delete-no-run",
        "delete-no-agent",
        "connect-job",
        "blank-project-name-with-explicit-fields",
    ),
)
def test_agent_rejects_one_invalid_mode_control_or_intent_selector(
    agent_parser: ArgumentParser,
    invocation: str,
    flag: str,
    value: str | None,
    reason: str,
) -> None:
    """Require the specific refusal for each single option change."""
    operation, arguments, _ = BASELINE[invocation]
    flags = dict(arguments)
    if value is None and flag in flags:
        del flags[flag]
    else:
        flags[flag] = value
    args = agent_parser.parse_args(_argv(operation, flags))

    with pytest.raises(ValueError) as rejected:
        CLI.execution_mode(args)
        CLI.normalized_intent(args, PROJECT)

    assert str(rejected.value) == reason
    if reason.startswith("invalid_specification:"):
        assert isinstance(rejected.value, SCHEMA.SpecificationError)


@pytest.mark.parametrize(
    ("project_id", "project_path", "consumer", "slug"),
    (
        (42, PROJECT, "Primary Consumer", "primary-consumer"),
        (43, "another/actual-native-repo", "Secondary Consumer", "secondary-consumer"),
    ),
)
def test_connect_naming_preserves_native_identity_and_replays_the_planned_scope(
    project_id: int, project_path: str, consumer: str, slug: str
) -> None:
    """Keep explicit names and replay the returned run ID for derived names."""
    project = SELECTION.AgentProject(project_id, project_path)
    explicit = SELECTION.select_connect(
        project,
        project_name=consumer,
        agent_name=AGENT_NAME,
        namespace=NAMESPACE,
        run_id=RUN_ID,
    )
    assert explicit.project == project
    assert explicit.agent_name == AGENT_NAME
    assert explicit.scope.namespace == NAMESPACE
    assert explicit.scope.owns_namespace is False

    planned = SELECTION.select_connect(
        project, project_name=consumer, agent_name=None, namespace=None, run_id=None
    )
    assert planned.project == project
    assert planned.agent_name == slug
    assert planned.scope.namespace.startswith(f"{slug}-")
    assert planned.scope.owns_namespace is True

    replay = SELECTION.select_connect(
        project,
        project_name=consumer,
        agent_name=None,
        namespace=None,
        run_id=planned.scope.run_id,
    )
    assert replay.release == planned.release
    assert replay.scope == planned.scope

    default = SELECTION.select_connect(
        project,
        project_name=None,
        agent_name=None,
        namespace=None,
        run_id=planned.scope.run_id,
    )
    assert default.project == project
    assert default.agent_name == project_path.rsplit("/", 1)[-1]
    assert default.scope.namespace.startswith(f"{default.agent_name}-")
    assert default.release != planned.release


def test_agent_entrypoint_emits_schema_valid_offline_json(
    target_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exercise result validation before the entrypoint renders empty arrays."""
    monkeypatch.chdir(target_file.parent)
    monkeypatch.delenv(BINDING.BINDING_ENV, raising=False)
    monkeypatch.setenv(BINDING.TARGET_ENV, str(target_file))
    entrypoint = import_script_module("gitlab_agent")

    code = entrypoint.main(
        _argv(
            CONNECT,
            {
                DRY_RUN_ARG: None,
                JSON_ARG: None,
                PROJECT_ARG: PROJECT,
                PROJECT_NAME_ARG: PROJECT_NAME,
            },
        )
    )

    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == CLI.DRY_RUN
    assert result["mutated"] is False
    assert result["records"] == result["errors"] == result["cleanup"]["failures"] == []
    assert result["filters"]["project"] == PROJECT
    assert result["filters"]["project_name"] == PROJECT_NAME


def _help_and_contract(command: str) -> tuple[str, Mapping[str, object]]:
    """Run one installed command's ``--help`` and ``--describe``.

    :param command: Script name under the skill's ``bin`` directory.
    :returns: Help text with whitespace collapsed, and the decoded command contract.
    """
    rendered = run_script(command, HELP_ARG)
    described = run_script(command, DESCRIBE_ARG)
    assert rendered.returncode == described.returncode == 0
    contract = parse_json_output(described)
    assert isinstance(contract, Mapping)
    return " ".join(rendered.stdout.split()), contract


def test_agent_help_and_describe_carry_the_catalog_credential_lines() -> None:
    """Show each catalog credential line in ``--describe`` and under one help heading.

    :returns: None; assertions compare help and contract with the catalog entry.
    """
    declared = list(CATALOG.COMMANDS[CLI.COMMAND][CREDENTIALS_FIELD])

    help_text, contract = _help_and_contract(CLI.COMMAND)

    Draft202012Validator(COMMAND_CONTRACT_SCHEMA).validate(contract)
    assert contract[CREDENTIALS_FIELD] == declared
    assert help_text.count(CREDENTIALS_HEADING) == 1
    positions = [
        help_text.index(" ".join(text.split()))
        for text in (DESCRIPTION_HEADING, CREDENTIALS_HEADING, *declared)
    ]
    positions.append(help_text.index(EXAMPLES_HEADING))
    assert positions == sorted(positions)


def test_command_without_catalog_credentials_has_no_credentials_block() -> None:
    """Omit the ``credentials`` key and the help heading when the catalog has none.

    :returns: None; assertions inspect the mirror command's help and contract.
    """
    assert CREDENTIALS_FIELD not in CATALOG.COMMANDS[MIRROR_COMMAND]

    help_text, contract = _help_and_contract(MIRROR_COMMAND)

    assert DESCRIPTION_HEADING in help_text
    assert CREDENTIALS_HEADING not in help_text
    assert CREDENTIALS_FIELD not in contract


def test_live_verify_hands_the_installed_producers_to_the_workflow(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify trusts the installed kit, in read mode, for the selected run and job."""
    observed: dict[str, object] = {}
    authorities = object()
    outcome = object()

    def workflow(*args: object, **kwargs: object) -> object:
        """Record what the command hands to the shared Agent workflow."""
        observed.update(args=args, kwargs=kwargs)
        return outcome

    monkeypatch.setattr(
        CLI,
        "resolve_agent_target",
        lambda *_args, **_kwargs: SimpleNamespace(project=PROJECT),
    )
    monkeypatch.setattr(CLI, "observe_authorities", lambda *_a, **_k: authorities)
    monkeypatch.setattr(CLI, "run_agent_workflow", workflow)
    monkeypatch.setattr(
        CLI,
        "agent_result",
        lambda *args: {"kind": CLI.KIND, "status": PASS_STATUS, "inputs": len(args)},
    )

    code = CLI.main(
        _argv(VERIFY, {PROJECT_ARG: PROJECT, RUN_ARG: RUN_ID, JOB_ARG: JOB_URL})
        + [JSON_ARG]
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out)["status"] == PASS_STATUS
    assert observed["args"][0] is authorities
    assert observed["args"][1]["job_url"] == JOB_URL
    assert observed["args"][2] == MODES.ActionMode.READ
    assert dict(observed["kwargs"]["producers"]) == dict(CI_ACCESS.staged_producers())


def test_live_verify_without_the_installed_kit_stops_before_provider_reads(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A copy missing a staged file reports one reason and opens no provider session."""

    def missing() -> None:
        """Fail the way an install without the CI access kit does."""
        raise CI_ACCESS.CiAccessError(UNAVAILABLE_KIT)

    def unreachable(*_args: object, **_kwargs: object) -> None:
        """Fail the test if target resolution starts."""
        raise AssertionError("provider target resolved")

    monkeypatch.setattr(CLI, "staged_producers", missing)
    monkeypatch.setattr(CLI, "resolve_agent_target", unreachable)

    code = CLI.main(
        _argv(VERIFY, {PROJECT_ARG: PROJECT, RUN_ARG: RUN_ID, JOB_ARG: JOB_URL})
        + [JSON_ARG]
    )

    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["status"] == BLOCKED_STATUS
    assert [error["reason"] for error in report["errors"]] == [UNAVAILABLE_KIT]
