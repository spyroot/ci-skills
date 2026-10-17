"""Validate common action modes before target access or output persistence.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Optional

APPLY_DRY_RUN_CONFLICT: Final = "apply_and_dry_run_conflict"
LIVE_PLAN_CONFLICT: Final = "live_plan_mode_conflict"
TIMEOUT_OUT_OF_RANGE: Final = "timeout_out_of_range"
TIMEOUT_MUST_BE_POSITIVE: Final = "timeout_must_be_positive"
CONFIRM_REQUIRES_APPLY: Final = "confirm_plan_requires_apply"
RECEIPT_REQUIRES_LIVE_RUN: Final = "receipt_out_requires_live_run"
OFFLINE_OUTPUT_FORBIDDEN: Final = "dry_run_cannot_write_output"


class ActionMode(StrEnum):
    """Execution modes shared by provider-independent command validation.

    .. attribute :: OFFLINE

        Plan without contacting a provider.

    .. attribute :: LIVE_PLAN

        Read current provider state without applying the operation.

    .. attribute :: READ

        Execute a read-only operation.

    .. attribute :: APPLY

        Execute an operation after its adapter verifies confirmation.
    """

    OFFLINE = "offline"
    LIVE_PLAN = "live-plan"
    READ = "read"
    APPLY = "apply"


@dataclass(frozen=True)
class ActionModePolicy:
    """Declare the existing mode and persistence rules of one command adapter.

    .. attribute :: default_mode
        :type: ActionMode

        Mode when no flag selects a mode and the operation is not read-only.

    .. attribute :: receipt_modes
        :type: frozenset[ActionMode]

        Modes allowed to persist a portable live receipt.

    .. attribute :: allow_offline_output
        :type: bool

        Whether offline plans may write ordinary reports or diagnostic logs.

    .. attribute :: maximum_timeout
        :type: Optional[int]

        Inclusive timeout ceiling, or absent when any positive timeout is accepted.
    """

    default_mode: ActionMode
    receipt_modes: frozenset[ActionMode]
    allow_offline_output: bool
    maximum_timeout: Optional[int] = None


def validate_action_mode(
    *,
    apply: bool,
    dry_run: bool,
    live_plan: bool,
    confirmed: bool,
    receipt_requested: bool,
    output_requested: bool,
    timeout: int,
    policy: ActionModePolicy,
    read_only: bool = False,
) -> ActionMode:
    """Select one execution mode and reject conflicting or disallowed controls.

    :param apply: Whether the caller selected a mutating execution.
    :param dry_run: Whether the caller selected an offline plan.
    :param live_plan: Whether the caller explicitly selected live planning.
    :param confirmed: Whether a confirmation digest was supplied; its adapter
        verifies the digest before mutation.
    :param receipt_requested: Whether a portable receipt destination was selected.
    :param output_requested: Whether ordinary report or log persistence was selected.
    :param timeout: Positive per-operation timeout, subject to the adapter's ceiling.
    :param policy: Adapter-owned defaults and allowed persistence modes.
    :param read_only: Whether an unflagged operation is an actual provider read.
    :returns: The selected execution mode, without performing I/O.
    :raises ValueError: Flags conflict, timeout is outside policy, confirmation is
        supplied without apply, or the selected mode forbids requested output.
    """
    if apply and dry_run:
        raise ValueError(APPLY_DRY_RUN_CONFLICT)
    if live_plan and (apply or dry_run):
        raise ValueError(LIVE_PLAN_CONFLICT)
    if policy.maximum_timeout is not None:
        if not 0 < timeout <= policy.maximum_timeout:
            raise ValueError(TIMEOUT_OUT_OF_RANGE)
    elif timeout <= 0:
        raise ValueError(TIMEOUT_MUST_BE_POSITIVE)
    if confirmed and not apply:
        raise ValueError(CONFIRM_REQUIRES_APPLY)
    if dry_run:
        mode = ActionMode.OFFLINE
    elif apply:
        mode = ActionMode.APPLY
    elif live_plan:
        mode = ActionMode.LIVE_PLAN
    elif read_only:
        mode = ActionMode.READ
    else:
        mode = policy.default_mode
    if receipt_requested and mode not in policy.receipt_modes:
        raise ValueError(RECEIPT_REQUIRES_LIVE_RUN)
    if (
        mode == ActionMode.OFFLINE
        and output_requested
        and not policy.allow_offline_output
    ):
        raise ValueError(OFFLINE_OUTPUT_FORBIDDEN)
    return mode
