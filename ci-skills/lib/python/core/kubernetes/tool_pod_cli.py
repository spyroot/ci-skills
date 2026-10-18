"""Plan, prepare, reuse and remove a cluster Pod with caller-selected tools.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
from collections.abc import MutableMapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Final, Optional

from lib.python.access.credentials import bind_sources
from lib.python.core.abstract.callbacks import AbstractCallbacks, Callbacks
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.action_modes import (
    ActionMode,
    ActionModePolicy,
    validate_action_mode,
)
from lib.python.core.catalog import describe
from lib.python.core.cli import parser
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.executor import WorkflowExecutor
from lib.python.core.json_types import JSONObject, JSONValue, json_object
from lib.python.core.kubernetes.kubernetes import K8SAuth, KubernetesError
from lib.python.core.kubernetes.run_scope import validate_run_id
from lib.python.core.kubernetes.tool_pod import (
    PrepareToolPodAction,
    RemoveScope,
    RemoveToolPod,
    RemoveToolPodAction,
    ResolveRunScopeAction,
    ResolveRunScopes,
    RunScope,
    ToolPodSpec,
    ToolPodState,
)
from lib.python.core.kubernetes.tool_pod_workflow import prepare_tool_pod_callbacks
from lib.python.core.kubernetes.workflow_report import workflow_result
from lib.python.core.paths import SKILL_ROOT
from lib.python.core.project_binding import resolve_target
from lib.python.core.provenance import functional_digest
from lib.python.core.report import emit_result, plan_digest
from lib.python.core.runtime import sanitize
from lib.python.core.target import Target, TargetError
from lib.python.sys.status import BLOCKED, DRY_RUN, PASS, PLANNED

TOOL_POD_KIND: Final = "tool_pod"
TOOL_POD_COMMAND: Final = "prepare_tool_pod.py"
DEFAULT_TIMEOUT: Final = 300
MODE_POLICY: Final = ActionModePolicy(
    default_mode=ActionMode.OFFLINE,
    receipt_modes=frozenset((ActionMode.APPLY,)),
    allow_offline_output=True,
)


class ToolPodOperation(StrEnum):
    """Installed operations for preparing and removing a reusable tool Pod.

    .. attribute :: PREPARE

        Create or reuse a Pod and verify its declared tools.

    .. attribute :: REMOVE

        Remove only the selected run's owned resources and verify absence.
    """

    PREPARE = "prepare"
    REMOVE = "remove"


def build_parser() -> argparse.ArgumentParser:
    """Expose the shared output/logging flags and tool-Pod operations.

    :returns: Parser whose default execution mode is a nonmutating offline plan.
    """
    result = parser("Prepare tools inside a reusable cluster Pod.", kind=TOOL_POD_KIND)
    result.set_defaults(kind=TOOL_POD_KIND)
    result.add_argument("action", nargs="?", choices=tuple(ToolPodOperation))
    result.add_argument(
        "--spec", metavar="FILE", help="tool-pod JSON: image and dependencies"
    )
    result.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="positive operation timeout in seconds (default: 300)",
    )
    result.add_argument(
        "--live-plan",
        action="store_true",
        help="verify Kubernetes access without creating resources",
    )
    result.add_argument(
        "--apply", action="store_true", help="perform the confirmed operation"
    )
    result.add_argument("--confirm-plan", metavar="SHA256", help="exact plan digest")
    result.add_argument(
        "--receipt-out", metavar="PATH", help="write a portable operation receipt"
    )
    return result


def execution_mode(args: argparse.Namespace) -> ActionMode:
    """Reject incompatible modes before resolving credentials or writing output.

    :param args: Parsed shared and tool-Pod arguments.
    :returns: Offline plan, live plan, or confirmed apply mode.
    :raises ValueError: Mode flags, receipt selection or timeout are invalid.
    """  # noqa: DOC502 - The shared mode validator rejects incompatible controls.
    return validate_action_mode(
        apply=args.apply,
        dry_run=args.dry_run,
        live_plan=args.live_plan,
        confirmed=bool(args.confirm_plan),
        receipt_requested=bool(args.receipt_out),
        output_requested=bool(args.output_dir or args.log_file),
        timeout=args.timeout,
        policy=MODE_POLICY,
    )


def operation_plan(args: argparse.Namespace, target: Target) -> JSONObject:
    """Bind target, functional code, selected specification and supplied run identity.

    A new run identifier is generated only during apply. An explicitly supplied
    identifier is part of confirmation and never changes between plan and apply.

    :param args: Validated operation and execution arguments.
    :param target: Kubernetes-only target selected through the shared resolver.
    :returns: Nonsecret confirmation inputs; generated scope names are not invented.
    :raises TargetError: The selected target has no Kubernetes configuration.
    :raises ValueError: The operation or its required spec/run ID is missing.
    :raises OSError: Specification or functional package files cannot be read.
    """  # noqa: DOC503 - Spec parsing and functional hashing propagate file errors.
    selected = target.kubernetes
    if selected is None:
        raise TargetError("kubernetes_target_missing")
    operation = ToolPodOperation(args.action)
    if operation == ToolPodOperation.PREPARE and not args.spec:
        raise ValueError("prepare_requires_spec")
    if operation == ToolPodOperation.REMOVE and (not args.run_id or args.spec):
        raise ValueError("remove_requires_run_id_without_spec")
    if args.run_id:
        validate_run_id(args.run_id)
    return {
        "kind": TOOL_POD_KIND,
        "operation": operation.value,
        "target": {"context": selected.context, "server": selected.server},
        "functional_digest": functional_digest(SKILL_ROOT),
        "run_id": args.run_id or "",
        "timeout_seconds": args.timeout,
        "spec": ToolPodSpec.load(Path(args.spec)).as_dict() if args.spec else {},
    }


def workflow(
    args: argparse.Namespace,
    target: Target,
    state: ToolPodState,
    plan: JSONObject,
    mode: ActionMode,
) -> WorkflowExecutor[DeliveryState]:
    """Compose one shared executor without doing I/O during callback construction.

    :param args: Selected operation, run identity and timeout.
    :param target: Credential-bound Kubernetes target.
    :param state: Concrete state shared by all callbacks in order.
    :param plan: Exact inputs already checked against the supplied confirmation.
    :param mode: Live plan or apply; an offline plan has no executor.
    :returns: Authentication-only plan or the prepare/remove callback sequence.
    :raises TypeError: Specification shape or callback binding is invalid.
    :raises ValueError: A required operation or configuration is unavailable.
    """  # noqa: DOC502,DOC503 - Typed conversion and callback binding propagate failures.
    callbacks: AbstractCallbacks[DeliveryState, SkillAction] = [
        K8SAuth(target, args.timeout)
    ]
    if mode == ActionMode.APPLY:
        if args.action == ToolPodOperation.PREPARE:
            spec = ToolPodSpec.from_document(json_object(plan["spec"]))
            action = PrepareToolPodAction(
                spec, run_id=args.run_id, timeout=args.timeout
            )
            scope = RunScope.new(run_id=action.run_id, namespace=action.namespace)
            callbacks.extend(prepare_tool_pod_callbacks(action, scope))
        else:
            lookup = ResolveRunScopeAction(args.run_id)
            callbacks.extend(
                (
                    ResolveRunScopes(lookup),
                    RemoveToolPod(RemoveToolPodAction(args.run_id)),
                    RemoveScope(lookup),
                )
            )
    return WorkflowExecutor(context=state, callbacks=Callbacks(callbacks))


def invocation_report(
    args: argparse.Namespace,
    target: Target,
    state: ToolPodState,
    plan: JSONObject,
    status: str,
    errors: Sequence[JSONObject],
) -> MutableMapping[str, JSONValue]:
    """Combine operation proof and cleanup with the existing report envelope.

    :param args: Operation and output settings selected by the caller.
    :param target: Selected target including installed source provenance.
    :param state: Actual callback and cleanup observations for this invocation.
    :param plan: Confirmation inputs or an empty mapping after preparation failure.
    :param status: Observed command outcome using existing status values.
    :param errors: Sanitized operation failures, empty after success.
    :returns: Report ready for shared rendering and portable receipt persistence.
    :raises TypeError: A callback or provenance field is not valid JSON.
    """  # noqa: DOC502 - Shared JSON conversion and records validate report content.
    return workflow_result(
        TOOL_POD_KIND, args.action or "", state, plan, status, errors, target
    )


def execute(args: argparse.Namespace) -> int:
    """Run one planned invocation and report provider failures without losing cleanup.

    :param args: Parser output with shared command settings.
    :returns: Canonical result exit status, including blocked configuration or execution.
    :raises OSError: A requested final output destination cannot be written.
    """  # noqa: DOC502,DOC503 - ValueError is caught; output I/O errors propagate.
    if args.describe:
        return emit_result(describe(TOOL_POD_COMMAND), args)
    state = ToolPodState.from_args(args)
    plan: JSONObject = {}
    target: Optional[Target] = None
    try:
        mode = execution_mode(args)
        target = resolve_target(
            args.target,
            args.binding,
            dry_run=mode == ActionMode.OFFLINE,
            required_surfaces=("kubernetes",),
        )
        if mode != ActionMode.OFFLINE:
            target = bind_sources(target, revision=args.revision)
        plan = operation_plan(args, target)
        if mode == ActionMode.APPLY and args.confirm_plan != plan_digest(plan):
            raise ValueError("confirm_plan_mismatch_rerun_plan")
        if mode == ActionMode.OFFLINE:
            status = DRY_RUN
        else:
            workflow(args, target, state, plan, mode).run()
            if state.failed:
                raise KubernetesError("callback_incomplete")
            status = PLANNED if mode == ActionMode.LIVE_PLAN else PASS
        output = invocation_report(args, target, state, plan, status, ())
    except (KeyboardInterrupt, SystemExit):
        output = failure_report(args, state, plan, "interrupted", target)
    except (TargetError, KubernetesError, OSError, TypeError, ValueError) as exc:
        output = failure_report(args, state, plan, sanitize(str(exc)), target)
    return emit_result(output, args)


def failure_report(
    args: argparse.Namespace,
    state: ToolPodState,
    plan: JSONObject,
    reason: str,
    target: Optional[Target] = None,
) -> MutableMapping[str, JSONValue]:
    """Keep partial callback and cleanup observations when an invocation fails.

    :param args: Selected operation and output settings.
    :param state: Shared state, including any completed and cleaned callbacks.
    :param plan: Resolved confirmation inputs, or empty before resolution.
    :param reason: Sanitized failure reason supplied by the exception boundary.
    :param target: Resolved target when setup reached that point; otherwise absent.
    :returns: A blocked result; a failed cleanup cannot be presented as successful.
    :raises TypeError: A callback's evidence cannot be represented as JSON.
    """  # noqa: DOC502 - Callback results are narrowed by result_records.
    errors: Sequence[JSONObject] = [{"source": TOOL_POD_KIND, "reason": reason}]
    return workflow_result(
        TOOL_POD_KIND, args.action or "", state, plan, BLOCKED, errors, target
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse installed tool-Pod arguments and execute their selected mode.

    :param argv: Explicit arguments for a caller, or process arguments when absent.
    :returns: Canonical command exit status.
    """
    return execute(build_parser().parse_args(argv))
