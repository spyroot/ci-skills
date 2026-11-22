"""CLI entrypoint workflow for NVIDIA GPU Operator operations on OpenShift (CI17).

CI17-M1 delivers the read-only ``NvidiaHealthCheck`` (``health-check``) and the
symmetric ``NvidiaPauseOperator`` (``pause`` / ``resume``) atomic operations
with server-side dry-run, digest confirmation, and independent read-back.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
from collections.abc import MutableMapping, MutableSequence, Sequence
from typing import Final, Optional

from lib.python.access.credentials import bind_sources
from lib.python.core.abstract.callbacks import Callbacks
from lib.python.core.action_modes import ActionMode, validate_action_mode
from lib.python.core.cli import _failure, parser
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.endpoints import KUBERNETES_AUTHORITY
from lib.python.core.executor import WorkflowExecutor
from lib.python.core.json_types import JSONObject, JSONValue, copy_json, is_json_value
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.nvidia_gpu.chains import (
    health_check_callbacks,
    pause_operator_callbacks,
)
from lib.python.core.kubernetes.nvidia_gpu.models import (
    DEFAULT_GPU_NAMESPACE,
    DEFAULT_GPU_TIMEOUT,
    GPU_OPERATOR_KIND,
    GPU_OPERATOR_MODE_POLICY,
    PAUSE_SCOPES,
    GPUOperatorAction,
    GPUOperatorState,
)
from lib.python.core.kubernetes.nvidia_gpu.preflight import pause_plan_evidence
from lib.python.core.project_binding import resolve_target
from lib.python.core.report import emit_result, plan_digest, report
from lib.python.core.runner_options import SingleValue
from lib.python.core.target import Target, TargetError
from lib.python.sys.status import APPLIED, BLOCKED, DRY_RUN, NO_OP, PASS, PLANNED

READ_OPERATIONS: Final[frozenset[str]] = frozenset({"health-check"})
MUTATING_OPERATIONS: Final[frozenset[str]] = frozenset({"pause", "resume"})
ALL_OPERATIONS: Final[tuple[str, ...]] = ("health-check", "pause", "resume")


def build_parser() -> argparse.ArgumentParser:
    """Create the agent-facing CLI parser for ``nvidia_gpu_operator.py``.

    :returns: Argument parser supporting ``health-check``, ``pause``, and ``resume``.
    """
    result = parser(
        "Inspect and manage NVIDIA GPU Operator resources on OpenShift.",
        kind=GPU_OPERATOR_KIND,
    )
    result.set_defaults(kind=GPU_OPERATOR_KIND)
    result.add_argument(
        "operation",
        nargs="?",
        choices=ALL_OPERATIONS,
        metavar="OPERATION",
    )
    result.add_argument(
        "--namespace",
        action=SingleValue,
        default=DEFAULT_GPU_NAMESPACE,
        metavar="NAME",
        help="NVIDIA GPU Operator namespace (default: nvidia-gpu-operator)",
    )
    result.add_argument(
        "--node",
        action=SingleValue,
        default="*",
        metavar="NODE",
        help="Target node name or '*' for all GPU nodes (default: '*')",
    )
    result.add_argument(
        "--scope",
        action=SingleValue,
        choices=PAUSE_SCOPES,
        metavar="SCOPE",
        help="Pause/resume scope: upgrade-skip, operands, driver, auto-upgrade, or mcp",
    )
    result.add_argument(
        "--nvidia-driver",
        action=SingleValue,
        metavar="NAME",
        help="NVIDIADriver CR name for auto-upgrade pause/resume",
    )
    result.add_argument(
        "--mcp",
        action=SingleValue,
        metavar="NAME",
        help="OpenShift MachineConfigPool name for mcp pause/resume",
    )
    result.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_GPU_TIMEOUT,
        metavar="SECONDS",
        help="Positive operation deadline in seconds (default: 600)",
    )
    result.add_argument(
        "--live-plan",
        action="store_true",
        help="Read cluster state, server-dry-run, and emit an apply-ready digest",
    )
    result.add_argument(
        "--apply",
        action="store_true",
        help="Execute a confirmed pause or resume mutation",
    )
    result.add_argument(
        "--confirm-plan",
        metavar="SHA256",
        help="Exact plan_digest returned by --live-plan",
    )
    result.add_argument(
        "--receipt-out",
        metavar="PATH",
        help="Write one portable live receipt",
    )
    return result


def resolve_gpu_mode(args: argparse.Namespace) -> ActionMode:
    """Validate execution mode flags under ``GPU_OPERATOR_MODE_POLICY``.

    :param args: Parsed command arguments.
    :returns: Selected ``ActionMode``.
    """
    read_only = args.operation in READ_OPERATIONS
    return validate_action_mode(
        apply=args.apply,
        dry_run=args.dry_run,
        live_plan=args.live_plan,
        confirmed=args.confirm_plan is not None,
        receipt_requested=args.receipt_out is not None,
        output_requested=False,
        timeout=args.timeout,
        policy=GPU_OPERATOR_MODE_POLICY,
        read_only=read_only,
    )


def workflow(action: GPUOperatorAction) -> WorkflowExecutor[DeliveryState]:
    """Build the ``WorkflowExecutor`` for the selected atomic verb.

    :param action: Prepared GPU operator action.
    :returns: Configured workflow executor.
    """
    callbacks = (
        health_check_callbacks(action)
        if action.operation in READ_OPERATIONS
        else pause_operator_callbacks(action)
    )
    return WorkflowExecutor(context=action.state, callbacks=Callbacks(callbacks))


class NvidiaGPUOperator:
    """Prepare, execute, and emit one NVIDIA GPU Operator invocation."""

    def __init__(self, args: argparse.Namespace) -> None:
        """Retain parsed inputs and initialize typed workflow state.

        :param args: Parsed command arguments.
        """
        self.args = args
        self.state = GPUOperatorState.from_args(args)
        self.state.namespace = args.namespace
        self.target: Target
        self.action: GPUOperatorAction

    def _prepare(self, mode: ActionMode) -> ActionMode:
        """Validate operation inputs, resolve Kubernetes target, and build action.

        :param mode: Validated execution mode.
        :returns: Execution mode retained on the action.
        :raises ValueError: If operation is missing or apply is requested on read-only.
        """
        if not self.args.operation:
            raise ValueError("gpu_operator_operation_required")
        if self.args.operation in READ_OPERATIONS and mode == ActionMode.APPLY:
            raise ValueError("health_check_is_read_only")
        target = resolve_target(
            self.args.target,
            self.args.binding,
            dry_run=mode == ActionMode.OFFLINE,
            required_surfaces=(KUBERNETES_AUTHORITY,),
        )
        if mode != ActionMode.OFFLINE:
            target = bind_sources(target, revision=self.args.revision)
        self.target = target
        self.args._resolved_mode = mode
        self.action = GPUOperatorAction(
            args=self.args,
            state=self.state,
            target=target,
            operation=self.args.operation,
            namespace=self.args.namespace,
            node=self.args.node,
            scope=self.args.scope,
            nvidia_driver=self.args.nvidia_driver,
            mcp=self.args.mcp,
        )
        return mode

    def _records(self) -> Sequence[JSONObject]:
        """Collect JSON-safe callback records in execution order.

        :returns: Callback-named records.
        :raises TypeError: If a callback returns non-JSON data.
        """
        records: MutableSequence[JSONObject] = []
        for callback, value in self.state.results.items():
            if "verified" not in value and "needs" not in value:
                continue
            if not is_json_value(value):
                raise TypeError("workflow_record_requires_json")
            records.append({"callback": type(callback).__name__, **dict(value)})
        return records

    def _base_output(self) -> MutableMapping[str, JSONValue]:
        """Build the versioned report envelope for this command.

        :returns: Mutable report mapping.
        """
        kubernetes = self.target.kubernetes
        target_label = (
            f"{kubernetes.context}@{kubernetes.server}"
            if kubernetes
            else KUBERNETES_AUTHORITY
        )
        output = report(GPU_OPERATOR_KIND, target_label, {}, [], [])
        output["operation"] = self.args.operation
        output["tested_revision"] = self.target.tested_revision
        output["target_file"] = (
            str(self.target.source_file) if self.target.source_file is not None else ""
        )
        output["target_source"] = self.target.source_kind or ""
        output["mutated"] = self.state.mutated
        if self.target.skill is not None:
            output["skill"] = copy_json(self.target.skill)
        return output

    def _attach_state(
        self, output: MutableMapping[str, JSONValue]
    ) -> MutableMapping[str, JSONValue]:
        """Attach typed health, pause plan, and needs evidence to the report.

        :param output: Report envelope.
        :returns: Updated report envelope.
        """
        output["access"] = self.state.access_evidence
        output["mutated"] = self.state.mutated
        if self.state.health is not None:
            output["health"] = copy_json(self.state.health)
        if self.state.pause_plan is not None:
            evidence = pause_plan_evidence(self.state.pause_plan)
            output["plan"] = evidence
            output["plan_digest"] = plan_digest(evidence)
            output["confirmable"] = not self.state.pause_plan.no_op
        if self.state.needs:
            output["needs"] = [
                {"need": item.need, "evidence": item.evidence, "supply": item.supply}
                for item in self.state.needs
            ]
        return output

    def _final_status(self, mode: ActionMode) -> str:
        """Determine canonical status from mode, needs, and mutation state.

        :param mode: Executed action mode.
        :returns: Canonical status token.
        """
        if self.state.needs:
            return BLOCKED
        if self.args.operation in READ_OPERATIONS:
            return PASS
        plan = self.state.pause_plan
        if plan is not None and plan.no_op:
            return NO_OP
        if mode == ActionMode.APPLY:
            return APPLIED if self.state.mutated else NO_OP
        return PLANNED

    def execute(self) -> int:
        """Run the selected verb and emit one structured report.

        :returns: Canonical process exit code.
        """
        try:
            mode = resolve_gpu_mode(self.args)
        except ValueError as exc:
            return _failure(self.args, GPU_OPERATOR_KIND, "arguments", str(exc))
        errors: MutableSequence[JSONObject] = []
        try:
            mode = self._prepare(mode)
            output = self._base_output()
            if mode == ActionMode.OFFLINE:
                output.update(
                    status=DRY_RUN,
                    plan={
                        "operation": self.args.operation,
                        "namespace": self.args.namespace,
                        "node": self.args.node,
                        "scope": self.args.scope,
                    },
                    confirmable=False,
                    access={},
                )
            else:
                workflow(self.action).run()
                output.update(status=self._final_status(mode))
                self._attach_state(output)
            records = self._records()
        except (KeyboardInterrupt, SystemExit):
            output = (
                self._base_output()
                if hasattr(self, "target")
                else report(GPU_OPERATOR_KIND, KUBERNETES_AUTHORITY, {}, [], [])
            )
            output.update(operation=self.args.operation, status=BLOCKED)
            errors.append({"source": GPU_OPERATOR_KIND, "reason": "interrupted"})
            records = self._records()
            self._attach_state(output)
        except (TargetError, KubernetesError, OSError, TypeError, ValueError) as exc:
            output = (
                self._base_output()
                if hasattr(self, "target")
                else report(GPU_OPERATOR_KIND, KUBERNETES_AUTHORITY, {}, [], [])
            )
            output.update(operation=self.args.operation, status=BLOCKED)
            errors.append({"source": GPU_OPERATOR_KIND, "reason": str(exc)})
            records = self._records()
            self._attach_state(output)
        output["records"] = list(records)
        output["errors"] = list(errors)
        output["summary"] = {
            "record_count": len(records),
            "error_count": len(errors),
        }
        return emit_result(output, self.args)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse arguments and execute the NVIDIA GPU Operator command.

    :param argv: Optional argument sequence for callers and focused tests.
    :returns: Canonical process exit code.
    """
    args = build_parser().parse_args(argv)
    if args.describe:
        from lib.python.core.catalog import describe

        return emit_result(describe("nvidia_gpu_operator.py"), args)
    return NvidiaGPUOperator(args).execute()
