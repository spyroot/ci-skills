"""Command workflow for enabling an OLM operator and applying its resources (CI16).

``ocp_operator.py`` answers, from one installed command, whether an OLM package
such as the NVIDIA Network Operator is enabled, what the cluster states it
needs, and — from CI16-M2/M3 onward — creates the Subscription and applies the
resource the package ships. CI16-M1 delivers the read-only resolve chain and
``--live-plan`` for ``install`` and ``apply``; confirmed apply is rejected until
the writer chains land, so no report can claim a write that no callback owns.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
from collections.abc import MutableMapping, MutableSequence, Sequence
from typing import Final, Optional

from lib.python.access.credentials import bind_sources
from lib.python.core.abstract.callbacks import AbstractCallbacks, Callbacks
from lib.python.core.action_modes import (
    ActionMode,
    ActionModePolicy,
    validate_action_mode,
)
from lib.python.core.cli import _failure, parser
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.endpoints import KUBERNETES_AUTHORITY, REGISTRY_AUTHORITY
from lib.python.core.executor import WorkflowExecutor
from lib.python.core.json_types import JSONObject, JSONValue, copy_json, is_json_value
from lib.python.core.kubernetes.kubernetes import (
    DEFAULT_COMMAND_TIMEOUT,
    K8SAuth,
    KubernetesError,
)
from lib.python.core.kubernetes.olm_callbacks import (
    ConfirmOperatorPlan,
    ResolveOperatorPackage,
    plan_evidence,
)
from lib.python.core.kubernetes.olm_models import (
    OLMAction,
    OLMState,
    OperatorOverrides,
)
from lib.python.core.kubernetes.olm_resources import ResolveShippedResource
from lib.python.core.project_binding import resolve_target
from lib.python.core.report import emit_result, plan_digest, report
from lib.python.core.runner_options import SingleValue
from lib.python.core.target import Target, TargetError
from lib.python.sys.status import BLOCKED, DRY_RUN, PASS, PLANNED

OPERATOR_KIND: Final = "olm_operator"
DEFAULT_OPERATOR_TIMEOUT: Final = 600
APPLY_NOT_DELIVERED: Final = "olm_apply_not_delivered_until_ci16_m2"

MODE_POLICY: Final = ActionModePolicy(
    default_mode=ActionMode.LIVE_PLAN,
    receipt_modes=frozenset({ActionMode.LIVE_PLAN, ActionMode.APPLY}),
    allow_offline_output=False,
    maximum_timeout=3600,
)


def build_parser() -> argparse.ArgumentParser:
    """Create the agent-facing parser with shared target, output, and log flags.

    :returns: Parser for offline plan, live plan, and confirmed apply.
    """
    result = parser(
        "Enable an OLM operator on OpenShift and apply its shipped resources.",
        kind=OPERATOR_KIND,
    )
    result.set_defaults(kind=OPERATOR_KIND)
    result.add_argument(
        "action",
        nargs="?",
        choices=("install", "apply", "remove"),
        metavar="ACTION",
    )
    result.add_argument(
        "--package",
        action=SingleValue,
        metavar="NAME",
        help="OLM package name, e.g. nvidia-network-operator",
    )
    result.add_argument(
        "--namespace",
        action=SingleValue,
        metavar="NAME",
        help="namespace the package enables into",
    )
    result.add_argument("--channel", action=SingleValue, metavar="NAME")
    result.add_argument("--source", action=SingleValue, metavar="NAME")
    result.add_argument("--source-namespace", action=SingleValue, metavar="NAME")
    result.add_argument("--install-mode", action=SingleValue, metavar="MODE")
    result.add_argument(
        "--approval", choices=("Automatic", "Manual"), action=SingleValue
    )
    result.add_argument("--starting-csv", action=SingleValue, metavar="NAME")
    result.add_argument(
        "--kind",
        dest="resource_kind",
        action=SingleValue,
        metavar="KIND",
        help="shipped custom resource kind to apply",
    )
    result.add_argument(
        "--shipped-default",
        action="store_true",
        help="apply the example document the package ships",
    )
    result.add_argument(
        "--spec", metavar="PATH", help="caller document with site values"
    )
    result.add_argument(
        "--wait-for",
        action=SingleValue,
        metavar="FIELD=VALUE",
        help="status field and value that mean the applied resource is done",
    )
    result.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_OPERATOR_TIMEOUT,
        metavar="SECONDS",
        help="positive operation deadline in seconds (default: 600)",
    )
    result.add_argument(
        "--live-plan",
        action="store_true",
        help="read cluster state and print an apply-ready digest",
    )
    result.add_argument("--apply", action="store_true", help="run the confirmed action")
    result.add_argument(
        "--confirm-plan", metavar="SHA256", help="exact digest from the live plan"
    )
    result.add_argument(
        "--receipt-out", metavar="PATH", help="write one portable live receipt"
    )
    result.epilog = (result.epilog or "") + (
        " Default mode is the live plan. install resolves the PackageManifest,"
        " channel, catalog source and existing Subscription without writing."
        " apply additionally resolves the shipped example or --spec document."
        " Confirmed apply is delivered with CI16-M2/M3 and rejected before then."
    )
    return result


def resolve_operator_mode(args: argparse.Namespace) -> ActionMode:
    """Select the execution mode under this command's persistence policy.

    ``validate_action_mode`` raises ``ValueError`` when flags conflict or violate
    the mode policy.

    :param args: Parsed command arguments.
    :returns: The validated execution mode.
    """
    return validate_action_mode(
        apply=args.apply,
        dry_run=args.dry_run,
        live_plan=args.live_plan,
        confirmed=args.confirm_plan is not None,
        receipt_requested=args.receipt_out is not None,
        output_requested=False,
        timeout=args.timeout,
        policy=MODE_POLICY,
    )


def install_callbacks(action: OLMAction) -> AbstractCallbacks[DeliveryState, OLMAction]:
    """Compose the enable chain for reuse by other commands (CI16-M2 writers land later).

    :param action: Prepared OLM operation.
    :returns: Authentication, resolution, and confirmation callbacks.
    """
    callbacks: AbstractCallbacks[DeliveryState, OLMAction] = [
        K8SAuth(action.target, min(action.args.timeout, DEFAULT_COMMAND_TIMEOUT)),
        ResolveOperatorPackage(action),
        ConfirmOperatorPlan(action),
    ]
    return callbacks


def apply_callbacks(action: OLMAction) -> AbstractCallbacks[DeliveryState, OLMAction]:
    """Compose the apply resolve chain for reuse by other commands.

    :param action: Prepared OLM operation.
    :returns: The install chain plus the shipped-resource resolver.
    """
    callbacks: AbstractCallbacks[DeliveryState, OLMAction] = [
        *install_callbacks(action),
        ResolveShippedResource(action),
    ]
    return callbacks


def workflow(action: OLMAction) -> WorkflowExecutor[DeliveryState]:
    """Compose the read-only chain for the selected verb.

    :param action: Prepared OLM operation.
    :returns: Executor whose callbacks resolve plan state without writes.
    """
    callbacks = (
        apply_callbacks(action)
        if action.args.action == "apply"
        else install_callbacks(action)
    )
    return WorkflowExecutor(context=action.state, callbacks=Callbacks(callbacks))


class OCPOperator:
    """Prepare, execute, and emit one OLM operator invocation."""

    def __init__(self, args: argparse.Namespace):
        """Retain parsed inputs and create the one shared execution state.

        :param args: Shared target/output flags and operator operation inputs.
        """
        self.args = args
        self.state = OLMState.from_args(args)
        self.state.package = args.package
        self.state.namespace = args.namespace
        self.target: Target
        self.action: OLMAction

    def _prepare(self, mode: ActionMode) -> ActionMode:
        """Resolve Kubernetes and optional registry profiles; build the action.

        ``resolve_target`` and ``bind_sources`` propagate ``TargetError`` when the
        target lacks Kubernetes configuration and ``OSError`` when target or
        credential files cannot be read.

        :param mode: Execution mode already validated at the argument boundary.
        :returns: Selected operation mode.
        :raises ValueError: If the action, package, namespace, kind or
            ``--wait-for`` value is missing or invalid, or apply is selected.
        """
        if not self.args.action or not self.args.package or not self.args.namespace:
            raise ValueError("operator_package_namespace_required")
        if mode == ActionMode.APPLY:
            raise ValueError(APPLY_NOT_DELIVERED)
        if self.args.action == "apply" and not self.args.resource_kind:
            raise ValueError("operator_kind_required")
        wait_for = None
        if self.args.wait_for:
            field, separator, value = self.args.wait_for.partition("=")
            if not separator or not field:
                raise ValueError("operator_wait_for_invalid")
            wait_for = (field, value)
        target = resolve_target(
            self.args.target,
            self.args.binding,
            dry_run=mode == ActionMode.OFFLINE,
            required_surfaces=(KUBERNETES_AUTHORITY,),
            optional_surfaces=(REGISTRY_AUTHORITY,),
        )
        if mode != ActionMode.OFFLINE:
            target = bind_sources(target, revision=self.args.revision)
        self.target = target
        overrides = OperatorOverrides(
            channel=self.args.channel,
            source=self.args.source,
            source_namespace=self.args.source_namespace,
            install_mode=self.args.install_mode,
            approval=self.args.approval,
            starting_csv=self.args.starting_csv,
        )
        self.state.overrides = overrides
        self.state.wait_for = wait_for
        self.action = OLMAction(
            args=self.args,
            state=self.state,
            target=target,
            package=self.args.package,
            namespace=self.args.namespace,
            overrides=overrides,
        )
        return mode

    def _records(self) -> Sequence[JSONObject]:
        """Select finite JSON run results from the generic callback state.

        :returns: Callback-named records in workflow order.
        :raises TypeError: If a callback returns data outside the JSON model.
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
        """Create the versioned report envelope for this command.

        :returns: Mutable report with target and operation identity.
        """
        kubernetes = self.target.kubernetes
        target_label = (
            f"{kubernetes.context}@{kubernetes.server}"
            if kubernetes
            else KUBERNETES_AUTHORITY
        )
        output = report(OPERATOR_KIND, target_label, {}, [], [])
        output["operation"] = self.args.action
        output["tested_revision"] = self.target.tested_revision
        output["target_file"] = (
            str(self.target.source_file) if self.target.source_file is not None else ""
        )
        output["target_source"] = self.target.source_kind or ""
        if self.target.skill is not None:
            output["skill"] = copy_json(self.target.skill)
        return output

    def _state_evidence(
        self, output: MutableMapping[str, JSONValue]
    ) -> MutableMapping[str, JSONValue]:
        """Attach available shared-state evidence without inventing missing values.

        :param output: Report created for the selected target or a setup failure.
        :returns: The same report with plan and access data attached.
        """
        output["access"] = self.state.access_evidence
        if self.state.plan is not None:
            output["plan"] = plan_evidence(self.state.plan)
            output["plan_digest"] = plan_digest(plan_evidence(self.state.plan))
            output["confirmable"] = True
        if self.state.needs:
            output["needs"] = [
                {"need": item.need, "evidence": item.evidence, "supply": item.supply}
                for item in self.state.needs
            ]
        if self.state.shipped_document is not None:
            output["document"] = {
                "kind": self.state.shipped_document.get("kind"),
                "name": (self.state.shipped_document.get("metadata") or {}).get("name"),
            }
        if (
            self.state.results
            or self.state.cleanup_results
            or self.state.cleanup_failures
        ):
            output["cleanup"] = {
                "status": BLOCKED if self.state.cleanup_failures else PASS,
                "resources": copy_json(self.state.cleanup_results),
                "failures": copy_json(self.state.cleanup_failures),
            }
        return output

    def execute(self) -> int:
        """Run the selected mode and emit one stable report or classified failure.

        :returns: Canonical exit status from the final report status.
        :raises OSError: If final output or a requested receipt cannot be written.
        """  # noqa: DOC502 - Shared result emitters propagate requested I/O failures.
        try:
            mode = resolve_operator_mode(self.args)
        except ValueError as exc:
            return _failure(self.args, OPERATOR_KIND, "arguments", str(exc))
        errors: MutableSequence[JSONObject] = []
        try:
            mode = self._prepare(mode)
            output = self._base_output()
            if mode == ActionMode.OFFLINE:
                output.update(
                    status=DRY_RUN,
                    plan={
                        "package": self.args.package,
                        "namespace": self.args.namespace,
                        "action": self.args.action,
                    },
                    confirmable=False,
                    access={},
                )
            else:
                executor = workflow(self.action)
                executor.run()
                output.update(status=PLANNED)
                self._state_evidence(output)
            records = self._records()
        except (KeyboardInterrupt, SystemExit):
            output = (
                self._base_output()
                if hasattr(self, "target")
                else report(OPERATOR_KIND, KUBERNETES_AUTHORITY, {}, [], [])
            )
            output.update(operation=self.args.action, status=BLOCKED)
            errors.append({"source": OPERATOR_KIND, "reason": "interrupted"})
            records = self._records()
            self._state_evidence(output)
        except (TargetError, KubernetesError, OSError, TypeError, ValueError) as exc:
            output = (
                self._base_output()
                if hasattr(self, "target")
                else report(OPERATOR_KIND, KUBERNETES_AUTHORITY, {}, [], [])
            )
            output.update(operation=self.args.action, status=BLOCKED)
            errors.append({"source": OPERATOR_KIND, "reason": str(exc)})
            records = self._records()
            self._state_evidence(output)
        output["records"] = list(records)
        output["errors"] = list(errors)
        output["summary"] = {
            "record_count": len(records),
            "error_count": len(errors),
        }
        return emit_result(output, self.args)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse arguments and execute the installed OLM operator command.

    :param argv: Optional argument sequence for callers and focused tests.
    :returns: Canonical command exit status.
    """
    args = build_parser().parse_args(argv)
    if args.describe:
        from lib.python.core.catalog import describe

        return emit_result(describe("ocp_operator.py"), args)
    return OCPOperator(args).execute()
