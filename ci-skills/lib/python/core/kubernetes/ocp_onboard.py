"""Expose native OpenShift ISO build, verification and owned-resource removal.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
from collections.abc import MutableMapping, Sequence
from typing import Optional

from lib.python.access.credentials import bind_sources
from lib.python.core.action_modes import ActionMode
from lib.python.core.catalog import describe
from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.ocp_artifact_run import OCPArtifactState
from lib.python.core.kubernetes.ocp_build_workflow import (
    build_callbacks,
    execute_callbacks,
    resolve_build_tools,
)
from lib.python.core.kubernetes.ocp_invocation import (
    NODE_IMAGE_COMMAND,
    NODE_IMAGE_KIND,
    NodeBuildInputs,
    NodeOperation,
    build_parser,
    execution_mode,
    operation_plan,
)
from lib.python.core.kubernetes.ocp_lifecycle_workflow import (
    artifact_callbacks,
    resolve_artifact,
)
from lib.python.core.kubernetes.workflow_report import workflow_result
from lib.python.core.project_binding import resolve_target
from lib.python.core.report import emit_result, plan_digest
from lib.python.core.runtime import sanitize
from lib.python.core.target import Target, TargetError
from lib.python.sys.status import BLOCKED, DRY_RUN, PASS, PLANNED


class NodeInvocation:
    """Retain one invocation's inputs, live observations and failure evidence."""

    def __init__(self, args: argparse.Namespace):
        """Create empty shared state before resolving any provider configuration.

        :param args: Parsed invocation and existing output/logging controls.
        """
        self.args = args
        self.state = OCPArtifactState.from_args(args)
        self.plan: JSONObject = {}
        self.target: Optional[Target] = None

    def _confirm(self, mode: ActionMode) -> None:
        """Compare the confirmed digest before constructing mutating callbacks.

        :param mode: Selected execution mode.
        :raises ValueError: The current exact inputs differ from the confirmed plan.
        """
        if mode == ActionMode.APPLY and self.args.confirm_plan != plan_digest(
            self.plan
        ):
            raise ValueError("confirm_plan_mismatch_rerun_plan")

    def _build(self, mode: ActionMode, target: Target) -> None:
        """Capture native inputs, resolve tools, confirm, then run the composition.

        :param mode: Offline plan, live plan or confirmed apply.
        :param target: Resolved Kubernetes-only target.
        :raises BaseException: Input reading, discovery, confirmation or execution fails.
        """  # noqa: DOC502 - shared helpers preserve input/provider failures.
        inputs = NodeBuildInputs.read(self.args)
        evidence = inputs.evidence()
        if mode != ActionMode.OFFLINE:
            resolve_build_tools(self.state, target, inputs, self.args.timeout)
            evidence.update(
                tool_pod=self.state.resolved_tools.as_dict(),
                release=self.state.release_evidence,
                node_selector=dict(self.state.node_selector),
            )
        self.plan = operation_plan(self.args, target, evidence)
        self._confirm(mode)
        if mode == ActionMode.APPLY:
            callbacks = build_callbacks(
                self.state, inputs, run_id=self.args.run_id, timeout=self.args.timeout
            )
            execute_callbacks(self.state, callbacks)

    def _existing(
        self, mode: ActionMode, target: Target, operation: NodeOperation
    ) -> None:
        """Resolve an existing run before confirming removal or reading its ISO.

        :param mode: Offline plan, live plan, read or confirmed apply.
        :param target: Resolved Kubernetes-only target.
        :param operation: Verify or remove.
        :raises BaseException: Run lookup, confirmation or artifact operations fail.
        """  # noqa: DOC502 - shared helpers preserve provider failures.
        self.plan = operation_plan(self.args, target, {})
        if mode == ActionMode.OFFLINE:
            return
        resolve_artifact(
            self.state, target, operation, self.args.run_id, self.args.timeout
        )
        evidence: JSONObject = {"present": self.state.artifact_record_present}
        if self.state.artifact_record_present:
            evidence["artifact_record"] = self.state.artifact_record.as_dict()
        self.plan = operation_plan(self.args, target, evidence)
        self._confirm(mode)
        if mode in (ActionMode.APPLY, ActionMode.READ):
            execute_callbacks(
                self.state,
                artifact_callbacks(
                    self.state, operation, self.args.run_id, self.args.timeout
                ),
            )

    def run(self) -> MutableMapping[str, JSONValue]:
        """Resolve the selected target and execute only the requested operation.

        :returns: Shared workflow report including any native and cleanup evidence.
        :raises BaseException: Invalid input, configuration or operation fails.
        """  # noqa: DOC502 - the command boundary handles these failures.
        args = self.args
        mode = execution_mode(args)
        target = resolve_target(
            args.target,
            args.binding,
            dry_run=mode == ActionMode.OFFLINE,
            required_surfaces=("kubernetes",),
        )
        if mode != ActionMode.OFFLINE:
            target = bind_sources(target, revision=args.revision)
        self.target = target
        operation = NodeOperation(args.action)
        if operation == NodeOperation.BUILD:
            self._build(mode, target)
        else:
            self._existing(mode, target, operation)
        status = {ActionMode.OFFLINE: DRY_RUN, ActionMode.LIVE_PLAN: PLANNED}.get(
            mode, PASS
        )
        return self.report(status, ())

    def report(
        self, status: str, errors: Sequence[JSONObject]
    ) -> MutableMapping[str, JSONValue]:
        """Render only captured identities, never credential files or native input bytes.

        :param status: Observed invocation status.
        :param errors: Sanitized errors, empty after successful completion.
        :returns: Shared report with the final location and actual native evidence.
        """
        output = workflow_result(
            NODE_IMAGE_KIND,
            self.args.action or "",
            self.state,
            self.plan,
            status,
            errors,
            self.target,
        )
        if hasattr(self.state, "artifact_record"):
            output["run_id"] = self.state.artifact_record.run_id
            output["artifact"] = self.state.artifact_record.artifact.as_dict()
            output["artifact_lifetime"] = self.state.artifact_record.lifetime.value
        if self.state.native_exit_code >= 0:
            output["native_exit_code"] = self.state.native_exit_code
        if self.state.native_completion_unknown:
            output["native_completion_unknown"] = True
        return output


def execute(args: argparse.Namespace) -> int:
    """Run one installed command and retain partial observations after failure.

    :param args: Parsed command and shared report options.
    :returns: Existing report writer's canonical command exit status.
    :raises OSError: A caller-selected report destination cannot be written.
    """  # noqa: DOC502,DOC503 - report I/O propagates, invocation failures are captured.
    if args.describe:
        return emit_result(describe(NODE_IMAGE_COMMAND), args)
    invocation = NodeInvocation(args)
    try:
        output = invocation.run()
    except (KeyboardInterrupt, SystemExit):
        output = invocation.report(
            BLOCKED, [{"source": NODE_IMAGE_KIND, "reason": "interrupted"}]
        )
    except (TargetError, KubernetesError, OSError, TypeError, ValueError) as exc:
        output = invocation.report(
            BLOCKED, [{"source": NODE_IMAGE_KIND, "reason": sanitize(str(exc))}]
        )
    return emit_result(output, args)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse installed command arguments and execute one requested operation.

    :param argv: Explicit arguments, or process arguments when absent.
    :returns: Canonical command exit status.
    """
    return execute(build_parser().parse_args(argv))
