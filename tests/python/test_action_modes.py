"""Exercise shared mode validation through both existing command adapters.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Mapping

import pytest
from conftest import import_script_module

MODES = import_script_module("lib.python.core.action_modes")
POD_CLI = import_script_module("lib.python.core.kubernetes.tool_pod_cli")
GITLAB = import_script_module("lib.python.core.gitlab.gitlab_action")


def _arguments(changes: Mapping[str, object]) -> Namespace:
    """Build the common argument surface consumed by the two real adapters."""
    values: dict[str, object] = {
        "action": "create",
        "kind": "gitlab_milestone",
        "apply": False,
        "dry_run": False,
        "live_plan": False,
        "confirm_plan": "",
        "receipt_out": None,
        "output_dir": None,
        "log_file": None,
        "timeout": 30,
    }
    values.update(changes)
    return Namespace(**values)


def _mode(adapter: str, args: Namespace) -> MODES.ActionMode:
    """Invoke the actual command adapter rather than duplicating its policy."""
    return (
        POD_CLI.execution_mode(args)
        if adapter == "pod"
        else GITLAB._workflow_mode(args)
    )


@pytest.mark.parametrize("adapter", ("pod", "gitlab"))
@pytest.mark.parametrize(
    ("changes", "expected"),
    (
        ({}, MODES.ActionMode.OFFLINE),
        ({"dry_run": True}, MODES.ActionMode.OFFLINE),
        ({"live_plan": True}, MODES.ActionMode.LIVE_PLAN),
        ({"apply": True, "confirm_plan": "fixture"}, MODES.ActionMode.APPLY),
        ({"apply": True, "receipt_out": "receipt.json"}, MODES.ActionMode.APPLY),
    ),
)
def test_adapters_share_mode_selection_without_provider_access(
    adapter: str, changes: Mapping[str, object], expected: MODES.ActionMode
) -> None:
    """Both consumers preserve mode selection through the extracted validator."""
    assert _mode(adapter, _arguments(changes)) == expected


@pytest.mark.parametrize("adapter", ("pod", "gitlab"))
@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        ({"apply": True, "dry_run": True}, "apply_and_dry_run_conflict"),
        ({"live_plan": True, "apply": True}, "live_plan_mode_conflict"),
        ({"live_plan": True, "dry_run": True}, "live_plan_mode_conflict"),
        ({"confirm_plan": "fixture"}, "confirm_plan_requires_apply"),
        ({"receipt_out": "receipt.json"}, "receipt_out_requires_live_run"),
        (
            {"dry_run": True, "receipt_out": "receipt.json"},
            "receipt_out_requires_live_run",
        ),
    ),
)
def test_adapters_reject_conflicting_or_nonlive_persistence_modes(
    adapter: str, changes: Mapping[str, object], reason: str
) -> None:
    """The same invalid controls fail before either adapter touches a provider."""
    with pytest.raises((ValueError, GITLAB.ActionError), match=f"^{reason}$"):
        _mode(adapter, _arguments(changes))


@pytest.mark.parametrize("timeout", (0, -1))
@pytest.mark.parametrize("adapter", ("pod", "gitlab"))
def test_adapter_timeout_policy_is_preserved(adapter: str, timeout: int) -> None:
    """Both reject nonpositive timeouts while retaining their public error tokens."""
    reason = "timeout_must_be_positive" if adapter == "pod" else "timeout_out_of_range"
    with pytest.raises((ValueError, GITLAB.ActionError), match=f"^{reason}$"):
        _mode(adapter, _arguments({"timeout": timeout}))


@pytest.mark.parametrize("output_option", ("output_dir", "log_file"))
def test_policy_differences_remain_owned_by_their_adapters(
    output_option: str,
) -> None:
    """Preserve both offline Pod outputs without allowing GitLab persistence.

    :param output_option: Ordinary report or diagnostic path selected by the caller.
    """
    output = _arguments({output_option: "reports"})
    assert POD_CLI.execution_mode(output) == MODES.ActionMode.OFFLINE
    with pytest.raises(GITLAB.ActionError, match="^dry_run_cannot_write_output$"):
        GITLAB._workflow_mode(output)
    timeout = _arguments({"timeout": GITLAB.MAX_ACTION_TIMEOUT + 1})
    assert POD_CLI.execution_mode(timeout) == MODES.ActionMode.OFFLINE
    with pytest.raises(GITLAB.ActionError, match="^timeout_out_of_range$"):
        GITLAB._workflow_mode(timeout)


def test_gitlab_read_mode_allows_receipt_and_rejects_apply_controls() -> None:
    """Read-only runner operations retain their own existing mode semantics."""
    read = _arguments(
        {"kind": "gitlab_runner", "action": "list", "receipt_out": "read.json"}
    )
    assert GITLAB._workflow_mode(read) == MODES.ActionMode.READ
    read.apply = True
    with pytest.raises(GITLAB.ActionError, match="^runner_read_rejects_apply_options$"):
        GITLAB._workflow_mode(read)
