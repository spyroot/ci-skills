"""Command workflow for live planning and running Podman readiness evidence.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
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
from lib.python.core.cli import _failure, parser
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.executor import WorkflowExecutor
from lib.python.core.json_types import JSONObject, JSONValue, copy_json, is_json_value
from lib.python.core.kubernetes.kubernetes import (
    DEFAULT_COMMAND_TIMEOUT,
    K8SAuth,
    KubernetesError,
)
from lib.python.core.kubernetes.podman_callbacks import (
    ConfirmPodmanProbe,
    OpenProbeNamespace,
    PodmanProbeState,
    RunPodmanProbe,
    SelectReadyWorker,
)
from lib.python.core.kubernetes.podman_probe import (
    DEFAULT_CPU_LIMIT,
    DEFAULT_CPU_REQUEST,
    DEFAULT_INNER_IMAGE,
    DEFAULT_MEMORY_LIMIT,
    DEFAULT_MEMORY_REQUEST,
    DEFAULT_PROBE_IMAGE,
    DEFAULT_PROBE_TIMEOUT,
    DEFAULT_STORAGE_SIZE,
    PROBE_KIND,
    PodmanProbeAction,
    ProbeMode,
    ProbeResources,
)
from lib.python.core.project_binding import resolve_target
from lib.python.core.report import emit_result, report
from lib.python.core.runtime import TRACE_TEXT_LIMIT, redact_tree, sanitize
from lib.python.core.target import Target, TargetError
from lib.python.sys.status import BLOCKED, DRY_RUN, PARTIAL, PASS, PLANNED

PROBE_MODE_POLICY: Final = ActionModePolicy(
    default_mode=ActionMode.LIVE_PLAN,
    receipt_modes=frozenset((ActionMode.APPLY,)),
    allow_offline_output=True,
)
PROBE_EXECUTION_MODES: Final[Mapping[ActionMode, ProbeMode]] = {
    ActionMode.OFFLINE: ProbeMode.DRY_RUN,
    ActionMode.LIVE_PLAN: ProbeMode.LIVE_PLAN,
    ActionMode.APPLY: ProbeMode.APPLY,
}


def build_parser() -> argparse.ArgumentParser:
    """Create the agent-facing parser with shared target, output, and log flags.

    :returns: Parser for offline plan, live plan, and confirmed apply.
    """
    result = parser(
        "Prove nested Podman runtime readiness in one disposable Kubernetes Job.",
        kind=PROBE_KIND,
    )
    result.set_defaults(kind=PROBE_KIND)
    result.add_argument("action", nargs="?", choices=("probe",), default="probe")
    result.add_argument("--node", metavar="NAME", help="Ready worker; default discover")
    result.add_argument(
        "--probe-image",
        default=DEFAULT_PROBE_IMAGE,
        metavar="IMAGE@SHA256",
        help="digest-pinned Podman image",
    )
    result.add_argument(
        "--inner-image",
        default=DEFAULT_INNER_IMAGE,
        metavar="IMAGE@SHA256",
        help="digest-pinned nested test image",
    )
    result.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_PROBE_TIMEOUT,
        metavar="SECONDS",
        help="positive Job deadline in seconds (default: 300)",
    )
    result.add_argument(
        "--cpu-request", default=DEFAULT_CPU_REQUEST, metavar="QUANTITY"
    )
    result.add_argument("--cpu-limit", default=DEFAULT_CPU_LIMIT, metavar="QUANTITY")
    result.add_argument(
        "--memory-request", default=DEFAULT_MEMORY_REQUEST, metavar="QUANTITY"
    )
    result.add_argument(
        "--memory-limit", default=DEFAULT_MEMORY_LIMIT, metavar="QUANTITY"
    )
    result.add_argument(
        "--storage-size", default=DEFAULT_STORAGE_SIZE, metavar="QUANTITY"
    )
    result.add_argument(
        "--live-plan",
        action="store_true",
        help="read the concrete Ready worker and print an apply-ready digest",
    )
    result.add_argument("--apply", action="store_true", help="run the confirmed probe")
    result.add_argument(
        "--confirm-plan", metavar="SHA256", help="exact digest from the live plan"
    )
    result.add_argument(
        "--receipt-out", metavar="PATH", help="write one portable live receipt"
    )
    result.epilog = (result.epilog or "") + (
        " Default mode is the live plan. --dry-run contacts no API and uses "
        "'auto' when --node is omitted. Only --apply creates resources."
    )
    return result


def _mode(args: argparse.Namespace) -> ProbeMode:
    """Validate mutually exclusive execution flags and choose one mode.

    :param args: Parsed shared and probe-specific command arguments.
    :returns: Closed probe execution mode.
    :raises ValueError: If modes or confirmation flags conflict, the timeout is
        nonpositive, or a receipt is requested outside apply.
    """
    mode = validate_action_mode(
        apply=bool(args.apply),
        dry_run=bool(args.dry_run),
        live_plan=bool(args.live_plan),
        confirmed=bool(args.confirm_plan),
        receipt_requested=bool(args.receipt_out),
        output_requested=bool(args.output_dir or args.log_file),
        timeout=args.timeout,
        policy=PROBE_MODE_POLICY,
    )
    return PROBE_EXECUTION_MODES[mode]


def _resources(args: argparse.Namespace) -> ProbeResources:
    """Build validated resource settings from parsed command arguments.

    :param args: Parser namespace containing the five quantity options.
    :returns: Immutable bounded resource settings.
    """
    return ProbeResources(
        cpu_request=args.cpu_request,
        cpu_limit=args.cpu_limit,
        memory_request=args.memory_request,
        memory_limit=args.memory_limit,
        storage_size=args.storage_size,
    )


def workflow(
    action: PodmanProbeAction,
    state: PodmanProbeState,
) -> WorkflowExecutor[DeliveryState]:
    """Compose authentication, planning, and optional mutation callbacks.

    :param action: Validated Kubernetes-only probe operation.
    :param state: One shared typed state instance retained for this invocation.
    :returns: Executor whose reverse cleanup removes Job before Namespace.
    :raises TypeError: If callback construction violates the shared contracts.
    :raises ValueError: If callbacks are already bound to another state.
    """  # noqa: DOC502 - Callback construction enforces binding contracts.
    callbacks: AbstractCallbacks[DeliveryState, SkillAction] = [
        K8SAuth(action.target, min(action.timeout, DEFAULT_COMMAND_TIMEOUT)),
        SelectReadyWorker(action),
        ConfirmPodmanProbe(action),
    ]
    if action.mode == ProbeMode.APPLY:
        callbacks.extend((OpenProbeNamespace(action), RunPodmanProbe(action)))
    return WorkflowExecutor(context=state, callbacks=Callbacks(callbacks))


class ImageBuildReadiness:
    """Prepare, execute, and emit one image-build readiness invocation."""

    def __init__(self, args: argparse.Namespace):
        """Retain parsed inputs and create the one shared execution state.

        :param args: Shared target/output flags and probe operation inputs.
        """
        self.args = args
        self.state = PodmanProbeState.from_args(args)
        self.target: Target
        self.action: PodmanProbeAction

    def _prepare(self, mode: ProbeMode) -> ProbeMode:
        """Resolve only Kubernetes and construct the typed operation action.

        :param mode: Execution mode already validated at the argument boundary.
        :returns: Selected operation mode.
        :raises TargetError: If the target lacks Kubernetes configuration or credentials.
        :raises ValueError: If modes, images, resource quantities, or timeout are invalid.
        :raises OSError: If configured target or credential files cannot be read.
        """
        target = resolve_target(
            self.args.target,
            self.args.binding,
            dry_run=mode == ProbeMode.DRY_RUN,
            required_surfaces=("kubernetes",),
        )
        if mode != ProbeMode.DRY_RUN:
            target = bind_sources(target, revision=self.args.revision)
        self.target = target
        self.action = PodmanProbeAction(
            target=target,
            node=self.args.node,
            probe_image=self.args.probe_image,
            inner_image=self.args.inner_image,
            timeout=self.args.timeout,
            resources=_resources(self.args),
            mode=mode,
            confirm_plan=self.args.confirm_plan,
        )
        return mode

    def _records(self) -> Sequence[JSONObject]:
        """Select finite JSON run results from the generic callback state.

        :returns: Callback-named records in workflow order.
        :raises TypeError: If a callback returns data outside the JSON model.
        """
        records: MutableSequence[JSONObject] = []
        for callback, value in self.state.results.items():
            if "verified" not in value:
                continue
            if not is_json_value(value):
                raise TypeError("workflow_record_requires_json")
            records.append({"callback": type(callback).__name__, **dict(value)})
        return records

    def _base_output(self) -> MutableMapping[str, JSONValue]:
        """Create the existing versioned report envelope for this command.

        :returns: Mutable report with target and operation identity.
        """
        kubernetes = self.target.kubernetes
        target_label = (
            f"{kubernetes.context}@{kubernetes.server}" if kubernetes else "kubernetes"
        )
        output = report(PROBE_KIND, target_label, {}, [], [])
        output["operation"] = "probe"
        output["tested_revision"] = self.target.tested_revision
        output["target_file"] = (
            str(self.target.source_file) if self.target.source_file is not None else ""
        )
        output["target_source"] = self.target.source_kind or ""
        if self.target.skill is not None:
            output["skill"] = copy_json(self.target.skill)
        return output

    def _probe_artifacts(self) -> JSONObject:
        """Persist bounded probe output only when an output directory was selected.

        :returns: Probe log and marker filenames within the selected directory, or
            an empty mapping when the caller selected no directory or no log exists.
        :raises OSError: If the selected directory or artifact writes fail.
        :raises TypeError: If parsed marker evidence cannot be encoded as JSON.
        """
        if not self.args.output_dir or not hasattr(self.state, "probe_log"):
            return {}
        destination = Path(self.args.output_dir).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        stem = f"{PROBE_KIND}-{self.state.run_scope.run_id}"
        log_path = destination / f"{stem}.probe.log"
        markers_path = destination / f"{stem}.markers.json"
        log_path.write_text(
            sanitize(self.state.probe_log, TRACE_TEXT_LIMIT).rstrip() + "\n",
            encoding="utf-8",
        )
        artifacts: JSONObject = {"log": log_path.name}
        if not hasattr(self.state, "marker_evidence"):
            return artifacts
        marker_document: JSONObject = {
            "schema_version": "1.0",
            "kind": "image_build_readiness_markers",
            "run_id": self.state.run_scope.run_id,
            "rows": list(self.state.marker_evidence.rows),
        }
        markers_path.write_text(
            json.dumps(redact_tree(marker_document), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        artifacts["markers"] = markers_path.name
        return artifacts

    def _state_evidence(
        self, output: MutableMapping[str, JSONValue]
    ) -> MutableMapping[str, JSONValue]:
        """Attach available shared-state evidence without inventing missing values.

        :param output: Report created for the selected target or a setup failure.
        :returns: The same report with available plan, scope, access, and cleanup data.
        """
        output["access"] = self.state.access_evidence
        if hasattr(self.state, "selected_node"):
            output["selected_node"] = self.state.selected_node
        if hasattr(self.state, "plan"):
            output["plan"] = self.state.plan
            output["plan_digest"] = self.state.plan_digest
        if hasattr(self.state, "run_scope"):
            output["run_id"] = self.state.run_scope.run_id
            output["namespace"] = self.state.run_scope.namespace
        if (
            self.state.results
            or self.state.cleanup_resources
            or self.state.cleanup_failures
        ):
            output["cleanup"] = {
                "status": BLOCKED if self.state.cleanup_failures else PASS,
                "resources": copy_json(self.state.cleanup_resources),
                "failures": copy_json(self.state.cleanup_failures),
            }
        artifacts = self._probe_artifacts()
        if artifacts:
            output["probe_artifacts"] = artifacts
        return output

    def _apply_status(self) -> str:
        """Classify a confirmed apply from runtime and cleanup evidence.

        :returns: ``PASS`` for complete evidence, ``PARTIAL`` for a completed Pod
            with failed checks, or ``BLOCKED`` when the probe could not run or
            cleanup failed.
        """
        if self.state.cleanup_failures or not hasattr(self.state, "probe_record"):
            return BLOCKED
        if not hasattr(self.state, "termination"):
            return BLOCKED
        return PASS if self.state.probe_record["verified"] else PARTIAL

    def execute(self) -> int:
        """Run the selected mode and emit one stable report or classified failure.

        :returns: Canonical exit status from the final report status.
        :raises OSError: If final output or a requested report cannot be written.
        """  # noqa: DOC502 - Shared result emitters propagate requested I/O failures.
        if self.args.describe:
            return emit_result(describe("image_build_readiness.py"), self.args)
        try:
            mode = _mode(self.args)
        except ValueError as exc:
            return _failure(self.args, PROBE_KIND, "arguments", str(exc))
        errors: MutableSequence[JSONObject] = []
        try:
            mode = self._prepare(mode)
            output = self._base_output()
            if mode == ProbeMode.DRY_RUN:
                selected = self.action.node or "auto"
                output.update(
                    status=DRY_RUN,
                    plan=self.action.plan(selected),
                    plan_digest=self.action.fingerprint(selected),
                    confirmable=False,
                    access={},
                )
            else:
                executor = workflow(self.action, self.state)
                executor.run()
                output.update(
                    status=(
                        PLANNED if mode == ProbeMode.LIVE_PLAN else self._apply_status()
                    ),
                    plan=self.state.plan,
                    plan_digest=self.state.plan_digest,
                    confirmable=True,
                    selected_node=self.state.selected_node,
                    access=self.state.access_evidence,
                )
                if (
                    hasattr(self.state, "probe_record")
                    and not self.state.probe_record["verified"]
                ):
                    errors.append(
                        {
                            "source": PROBE_KIND,
                            "reason": str(self.state.probe_record["outcome"]),
                        }
                    )
                self._state_evidence(output)
            records = self._records()
        except (KeyboardInterrupt, SystemExit):
            output = (
                self._base_output()
                if hasattr(self, "target")
                else report(PROBE_KIND, "kubernetes", {}, [], [])
            )
            output.update(operation="probe", status=BLOCKED)
            errors.append({"source": PROBE_KIND, "reason": "interrupted"})
            records = self._records()
            self._state_evidence(output)
        except (TargetError, KubernetesError, OSError, TypeError, ValueError) as exc:
            output = (
                self._base_output()
                if hasattr(self, "target")
                else report(PROBE_KIND, "kubernetes", {}, [], [])
            )
            output.update(operation="probe", status=BLOCKED)
            errors.append({"source": PROBE_KIND, "reason": str(exc)})
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
    """Parse arguments and execute the installed readiness command.

    :param argv: Optional argument sequence for callers and focused tests.
    :returns: Canonical command exit status.
    """
    args = build_parser().parse_args(argv)
    return ImageBuildReadiness(args).execute()
