"""Offline parser, intent, and shipped JSON report coverage for the GitLab Agent."""

from __future__ import annotations

import json
from argparse import ArgumentParser
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final

import pytest
from conftest import import_script_module

CLI = import_script_module("lib.python.core.gitlab.cluster_agent.cli")
CATALOG = import_script_module("lib.python.core.catalog")
MODES = import_script_module("lib.python.core.action_modes")
BINDING = import_script_module("lib.python.core.project_binding")
IDENTITY = import_script_module("lib.python.core.runner_identity")
SCHEMA = import_script_module("lib.python.core.schema")
SELECTION = import_script_module("lib.python.core.gitlab.cluster_agent.selection")

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
