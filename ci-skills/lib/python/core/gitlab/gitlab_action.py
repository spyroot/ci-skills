"""Compose concrete GitLab actions against typed, executor-owned state.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from dataclasses import dataclass, replace
from typing import Literal, Optional

from lib.python.access.gitlab import check_gitlab_operation_access
from lib.python.access.gitlab_credentials import bind_gitlab_session
from lib.python.core.abstract.callback import AbstractCallback
from lib.python.core.abstract.callbacks import Callbacks
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.cli import _failure
from lib.python.core.executor import WorkflowExecutor
from lib.python.core.gitlab.callback import GitlabCallback, GitLabState
from lib.python.core.gitlab.cli import resolve_gitlab_target
from lib.python.core.gitlab.gitlab_actions import (
    ActionError,
    ActionPlan,
    _access_failure,
    _id,
    _object,
    _planned_failure,
    _required_text,
    _result,
    _verified_skill_revision,
    apply_plan,
    make_plan,
)
from lib.python.core.gitlab.gitlab_api import GitLabAPIError, GlabAPIClient
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession, GitLabService
from lib.python.core.json_types import JSONObject, JSONValue, copy_json
from lib.python.core.report import emit_result
from lib.python.core.runtime import sanitize
from lib.python.core.target import GitLabOperationTarget, TargetError
from lib.python.sys.status import DRY_RUN, PARTIAL, PASS, PLANNED

WorkflowMode = Literal["describe", "offline", "live-plan", "read", "apply"]


def _runner_read(plan: ActionPlan) -> bool:
    """Identify the runner operations that perform reads only.

    :param plan: Concrete operation selected during planning.
    :returns: Whether the operation gets or lists scoped runners.
    """
    return plan.kind == "gitlab_runner" and plan.operation in {"get", "list"}


def _requires_snapshot(plan: ActionPlan) -> bool:
    """Identify plans whose confirmation must include live before-state.

    :param plan: Prepared operation before authentication.
    :returns: Whether deletion or group assignment needs a live snapshot.
    """
    return plan.kind == "gitlab_runner" and (
        plan.operation == "delete"
        or (plan.operation == "assign" and plan.target_kind == "group")
    )


def _workflow_mode(args: argparse.Namespace) -> WorkflowMode:
    """Validate command modes before constructing the callback sequence.

    :param args: Parsed action command, including shared output controls.
    :returns: Offline plan, live plan, read, or confirmed apply mode.
    :raises ActionError: The action, timeout, mode combination, or output controls
        are invalid for the requested operation.
    """
    if not args.action:
        raise ActionError("action_required")
    runner_read = args.kind == "gitlab_runner" and args.action in {"get", "list"}
    live_plan = getattr(args, "live_plan", False)
    if runner_read and (args.apply or args.confirm_plan or live_plan):
        raise ActionError("runner_read_rejects_apply_options")
    if args.apply and args.dry_run:
        raise ActionError("apply_and_dry_run_conflict")
    if live_plan and (args.apply or args.dry_run):
        raise ActionError("live_plan_mode_conflict")
    if not 0 < args.timeout <= 300:
        raise ActionError("timeout_out_of_range")
    if args.confirm_plan and not args.apply:
        raise ActionError("confirm_plan_requires_apply")
    if args.receipt_out and not (args.apply or (runner_read and not args.dry_run)):
        raise ActionError("receipt_out_requires_live_run")
    offline = (runner_read and args.dry_run) or (
        not args.apply and not live_plan and not runner_read
    )
    if offline and (args.output_dir or args.log_file):
        raise ActionError("dry_run_cannot_write_output")
    if offline:
        return "offline"
    if live_plan:
        return "live-plan"
    return "read" if runner_read else "apply"


@dataclass(frozen=True)
class GitLabAuthAction(SkillAction):
    """Select credentials and verify one already planned provider target.

    :param target: Runtime target resolved from the existing configuration.
    :param timeout: Bound in seconds for each provider request.
    :param revision: Optional source revision claim to verify during binding.

    .. attribute :: target
        :type: GitLabOperationTarget

        Runtime configuration supplying the selected host and credential source.

    .. attribute :: timeout
        :type: int

        Per-request provider timeout in seconds.

    .. attribute :: revision
        :type: Optional[str]

        Caller-supplied revision claim, absent when none was requested.
    """

    target: GitLabOperationTarget
    timeout: int
    revision: Optional[str]


@dataclass(frozen=True)
class ConfirmGitLabAction(SkillAction):
    """Bind authorization to the exact plan digest presented by the caller.

    :param digest: Required confirmation digest from the apply command.

    .. attribute :: digest
        :type: str

        Exact digest to compare after live snapshot collection.
    """

    digest: str


class GitLabAuth(GitlabCallback[GitLabAuthAction]):
    """Authenticate and collect live snapshots before an operation begins."""

    def _bind(self) -> BoundGitLabSession:
        """Resolve the plan's credential and verify required source identity.

        :returns: Bound session retained for access and failure evidence.
        :raises TargetError: The credential or target source cannot be resolved.
        :raises ActionError: A revision-sensitive runner operation has no verified
            skill revision.
        """  # noqa: DOC503 - bind_gitlab_session propagates TargetError.
        plan = self.state.plan
        self.state.phase = "ACCESS"
        session = bind_gitlab_session(
            self.action.target,
            target_kind=plan.target_kind,
            target_reference=plan.target_reference,
            target_source=plan.target_source,
            revision=self.action.revision,
        )
        self.state.session = session
        if (
            plan.kind == "gitlab_runner"
            and plan.operation in {"tag", "get", "list", "delete"}
            and _verified_skill_revision(session) is None
        ):
            raise ActionError("skill_revision_unverified")
        return session

    def read_config(self) -> JSONObject:
        """Resolve credentials once before any workflow lifecycle hook runs.

        :returns: Empty setup evidence; private credentials remain in shared state.
        :raises TargetError: The selected credential or target cannot be resolved.
        :raises ActionError: Required skill revision evidence is unavailable.
        """  # noqa: DOC502 - _bind validates credentials and revision provenance.
        self.state.configured_session = self._bind()
        return {}

    def _snapshot(self, service: GitLabService) -> ActionPlan:
        """Add live target membership or runner before-state to the plan.

        :param service: Access-verified API, session, and numeric target.
        :returns: Original plan or its replacement containing the required snapshot.
        :raises ActionError: The selected group or runner state is invalid.
        :raises GitLabAPIError: Snapshot reads fail at the selected provider.
        """  # noqa: DOC502 - Snapshot readers propagate provider and validation errors.
        plan = self.state.plan
        if not _requires_snapshot(plan):
            return plan
        if plan.operation == "assign":
            from lib.python.core.gitlab.gitlab_runners import project_ids

            return replace(
                plan,
                project_ids=project_ids(
                    service.api, service.session, service.target_id
                ),
            )
        from lib.python.core.gitlab.gitlab_runners import delete_snapshot

        return replace(
            plan,
            runner_snapshot=delete_snapshot(
                service.api, service.session, plan, service.target_id
            ),
        )

    def run(self) -> JSONObject:
        """Verify access and retain a typed service and refreshed plan in state.

        :returns: Bounded access evidence without the private provider client.
        :raises TargetError: The selected target or credential cannot be resolved.
        :raises ActionError: Revision, access, target binding, or snapshot checks fail.
        :raises GitLabAPIError: Live preflight cannot read selected provider resources.
        """  # noqa: DOC503 - binding and snapshot reads propagate their domain errors.
        session = self.state.configured_session
        api = GlabAPIClient(timeout=self.action.timeout)
        access = check_gitlab_operation_access(session, api_client=api)
        self.state.access = access
        if access.get("status") != PASS:
            self.state.failure_source, reason = _access_failure(access)
            raise ActionError(reason)
        target_id = self.state.plan.verified_target_id(session, access)
        service = GitLabService(api, session, target_id, access)
        self.state.gitlab = service
        self.state.plan = self._snapshot(service)
        return access


class ConfirmGitLabOperation(GitlabCallback[ConfirmGitLabAction]):
    """Check authorization after authentication has refreshed the plan."""

    def run(self) -> JSONObject:
        """Require the caller's exact digest and access-verified target.

        :returns: Empty mapping because confirmation adds no provider record.
        :raises ActionError: Confirmation is absent, stale, or bound to another target.
        """
        if not self.action.digest or self.action.digest != self.state.plan.digest:
            raise ActionError("confirm_plan_mismatch_rerun_live_plan")
        self.state.plan.verified_target_id(self.gitlab.session, self.state.access)
        return {}


def _observed_mutation(record: JSONObject) -> Optional[bool]:
    """Read a provider's explicit write outcome or its established action token.

    :param record: Completed provider operation evidence.
    :returns: Known mutation flag, or None when the provider reports uncertainty.
    :raises ActionError: The mutation field is neither a boolean nor unknown.
    """
    mutated = record.get("mutated", record.get("action") == "APPLIED")
    if mutated is not None and not isinstance(mutated, bool):
        raise ActionError("operation_mutated_invalid")
    return mutated


class GitLabOperation(GitlabCallback[ActionPlan]):
    """Execute one concrete plan using the provider service in typed state."""

    @property
    def plan(self) -> ActionPlan:
        """Read the execution plan including any authenticated live snapshot.

        :returns: Concrete plan initialized before this callback runs.
        :raises AttributeError: The caller has not initialized the workflow plan.
        """  # noqa: DOC502 - reading the uninitialized state field raises AttributeError.
        return self.state.plan

    def collect(self) -> JSONObject:
        """Mark the read or write boundary before invoking the provider.

        :returns: Empty mapping because phase bookkeeping adds no operation data.
        :raises ActionError: Shared planning changed the declared action beyond its
            authenticated membership or deletion snapshot.
        """
        declared = replace(
            self.state.plan,
            project_ids=self.action.project_ids,
            runner_snapshot=self.action.runner_snapshot,
        )
        if declared != self.action:
            raise ActionError("callback_plan_mismatch")
        self.state.phase = "READ" if _runner_read(self.plan) else "APPLY"
        if self.state.phase == "APPLY":
            self.state.mutated = None
        return {}

    def update(self) -> JSONObject:
        """Retain operation evidence before completion checks and cleanup.

        :returns: This callback's own operation result mapping.
        :raises ActionError: If the callback result is not a finite JSON object
            or its mutation flag is neither a boolean nor an unknown outcome.
        :raises RecursionError: If a custom operation supplies cyclic or excessively
            nested evidence at the provider-report boundary.
        """  # noqa: DOC502 - JSON evidence and mutation validation propagate failures.
        record = _object(self.result, "operation_result")
        self.state.operation_result = record
        self.state.operation_completed = True
        self.state.mutated = (
            False if self.state.phase == "READ" else _observed_mutation(record)
        )
        return record

    def run(self) -> JSONObject:
        """Dispatch issue and wiki operations through their existing provider code.

        :returns: Provider action record and its independent read-back evidence.
        :raises ActionError: The target, operation, or provider response is invalid.
        :raises GitLabAPIError: The selected provider request fails.
        """  # noqa: DOC502 - apply_plan propagates provider and response validation errors.
        service = self.gitlab
        return apply_plan(service.api, service.session, service.access, self.plan)

    def execute(
        self, api: GlabAPIClient, session: BoundGitLabSession, target_id: int
    ) -> JSONObject:
        """Run a direct ActionPlan callback through the same shared lifecycle.

        :param api: Existing GitLab API adapter.
        :param session: Authenticated session supplied by the library caller.
        :param target_id: Verified project or group identifier.
        :returns: This callback's state-owned operation result mapping.
        :raises ActionError: The operation cannot be independently verified.
        :raises GitLabAPIError: A provider read or write fails.
        :raises BaseException: A lifecycle or cleanup hook fails and the executor
            propagates the original exception after cleanup.
        """  # noqa: DOC502 - WorkflowExecutor propagates callback and cleanup failures.
        state = GitLabState(session=session)
        state.plan = self.action
        state.gitlab = GitLabService(api, session, target_id)
        WorkflowExecutor(state, Callbacks(self)).run()
        return state.operation_result


def _operation_errors(record: JSONObject) -> Sequence[Mapping[str, str]]:
    """Translate validated project failures into the existing report error shape.

    :param record: Completed operation with an optional errors array.
    :returns: Error source and reason mappings for affected projects.
    :raises ActionError: Errors are not an array of valid project failure objects.
    """
    errors = record.get("errors", [])
    if not isinstance(errors, list):
        raise ActionError("operation_errors_invalid_shape")
    result: MutableSequence[Mapping[str, str]] = []
    for value in errors:
        error = _object(value, "operation_error", "project_id", "reason")
        result.append(
            {
                "source": f"project:{_id(error['project_id'], 'project')}",
                "reason": _required_text(error["reason"], "operation_error_reason"),
            }
        )
    return result


def _read_fields(record: JSONObject) -> JSONObject:
    """Validate the public runner observation used by a read report.

    :param record: Runner callback's records, truncation flag, and job relation.
    :returns: Validated records, read-back counts, and optional job relation.
    :raises ActionError: Records, truncation, or the job relation has an invalid shape.
    """
    records = record.get("records")
    truncated = record.get("truncated")
    if not isinstance(records, list) or not isinstance(truncated, bool):
        raise ActionError("runner_observation_invalid_shape")
    selected = [_object(value, "runner_record") for value in records]
    related_job = record.get("related_job")
    if related_job is not None:
        related_job = _object(related_job, "runner_related_job")
    return {
        "records": selected,
        "readback": {
            "verified": True,
            "record_count": len(selected),
            "truncated": truncated,
        },
        "related_job": related_job,
    }


def _operation_report(state: GitLabState) -> MutableMapping[str, JSONValue]:
    """Build one report from the retained operation and authenticated identity.

    :param state: Invocation with completed operation evidence and a bound service.
    :returns: Existing read or mutation report, including partial verification.
    :raises ActionError: Operation evidence has an invalid public report shape.
    """  # noqa: DOC502 - Report field validators propagate ActionError.
    record, session = state.operation_result, state.gitlab.session
    fields: MutableMapping[str, JSONValue] = {
        "phase": state.phase,
        "mutated": state.mutated,
        "identity": state.access.get("identity"),
        "verified_target": state.access.get("target"),
        "credential_source": session.credential_source,
        "credential_digest": session.credential_digest,
        "skill": copy_json(session.skill),
    }
    status = PASS
    if state.phase == "READ":
        fields.update(_read_fields(record))
    else:
        errors = _operation_errors(record)
        cleanup = _object(
            record.get("cleanup", {"status": "NOT_APPLICABLE"}), "operation_cleanup"
        )
        complete = (
            record.get("verified") is True
            and not errors
            and cleanup.get("status") not in {"BLOCKED", "PARTIAL"}
        )
        status = PASS if complete else PARTIAL
        fields.update(
            result_action=record.get("action"),
            records=[record],
            errors=errors,
            readback=record,
            cleanup=cleanup,
        )
    data = _result(state.plan, status, **fields)
    data["tested_revision"] = _verified_skill_revision(session)
    return data


class GitLabWorkflow:
    """Construct a known callback order and render one command invocation."""

    def __init__(self, state: GitLabState):
        """Retain the invocation state without using it as a callback action.

        :param state: Concrete mutable state owned by this command invocation.
        """
        self.state = state
        self.mode: WorkflowMode = "offline"

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> GitLabWorkflow:
        """Create workflow state from the command's parsed arguments.

        :param args: Arguments from the existing action parser.
        :returns: Unexecuted workflow with no provider requests performed.
        """
        return cls(GitLabState(args=args))

    def prepare(self) -> None:
        """Validate inputs and establish the plan before callbacks are constructed.

        :raises ActionError: Arguments, live-plan selection, or offline confirmation
            are invalid for the requested operation.
        :raises TargetError: The selected target or binding cannot be resolved.
        :raises KeyError: A describe request names an unregistered command kind.
        """  # noqa: DOC503 - target resolution and command lookup propagate their errors.
        args = self.state.args
        if args.describe:
            from lib.python.core.catalog import COMMAND_BY_KIND, describe

            self.mode = "describe"
            self.state.report = describe(COMMAND_BY_KIND[args.kind])
            return
        self.mode = _workflow_mode(args)
        self.state.failure_source = "gitlab_action"
        target, source = resolve_gitlab_target(
            args.target, args.binding, dry_run=self.mode == "offline"
        )
        self.target: GitLabOperationTarget = target
        self.state.plan = make_plan(args.kind, args, target, source)
        plan = self.state.plan
        if self.mode == "live-plan" and not _requires_snapshot(plan):
            raise ActionError("live_plan_requires_group_assignment_or_runner_delete")
        if self.mode == "offline":
            self.state.report = _result(
                plan, DRY_RUN, phase="OFFLINE_PLAN", mutated=False
            )
        if self.mode == "apply":
            self.state.phase = "ACCESS"
            if _requires_snapshot(plan):
                if not args.confirm_plan:
                    raise ActionError("confirm_plan_mismatch_rerun_live_plan")
            elif not args.confirm_plan or args.confirm_plan != plan.digest:
                raise ActionError("confirm_plan_mismatch_rerun_dry_run")

    def callbacks(self) -> Callbacks[GitLabState]:
        """Construct the authenticated sequence selected during preparation.

        :returns: Authentication alone for a live plan, followed by an operation
            for reads, or confirmation and an operation for applies.
        :raises ActionError: Callback construction is attempted for an offline mode.
        """
        if self.mode in {"describe", "offline"}:
            raise ActionError("offline_workflow_has_no_provider_callbacks")
        args = self.state.args
        ordered: MutableSequence[AbstractCallback[GitLabState, SkillAction]] = [
            GitLabAuth(GitLabAuthAction(self.target, args.timeout, args.revision))
        ]
        if self.mode == "apply":
            ordered.append(
                ConfirmGitLabOperation(ConfirmGitLabAction(args.confirm_plan))
            )
        if self.mode in {"read", "apply"}:
            ordered.append(operation_callback(self.state.plan))
        return Callbacks(ordered)

    def finish_plan(self) -> None:
        """Retain a successful live-plan report after access and snapshot collection."""
        state = self.state
        plan, session = state.plan, state.gitlab.session
        state.phase = "LIVE_PLAN"
        snapshot = (
            {"runner_snapshot": plan.runner_snapshot}
            if plan.operation == "delete"
            else {"project_ids": list(plan.project_ids or ())}
        )
        state.report = _result(
            plan,
            PLANNED,
            phase="LIVE_PLAN",
            mutated=False,
            identity=state.access.get("identity"),
            verified_target=state.access.get("target"),
            credential_source=session.credential_source,
            credential_digest=session.credential_digest,
            skill=copy_json(session.skill),
            readback={"verified": True, **snapshot},
        )
        state.report["tested_revision"] = _verified_skill_revision(session)

    def failure(self, exc: Exception) -> int:
        """Render a blocked report without losing observed writes or cleanup errors.

        :param exc: Primary execution or output exception after cleanup has run.
        :returns: Existing blocked exit code after structured report emission.
        """
        state, args = self.state, self.state.args
        reason = (
            sanitize(str(exc), 240)
            if isinstance(
                exc, (ActionError, TargetError, GitLabAPIError, OSError, RuntimeError)
            )
            else "unexpected_runtime_failure"
        )
        cleanup_errors = [
            {"source": f"cleanup.{failure['callback']}", "reason": failure["reason"]}
            for failure in state.cleanup_failures
        ]
        if not hasattr(state, "plan"):
            return _failure(
                args,
                args.kind,
                state.failure_source,
                reason,
                cleanup_errors=cleanup_errors,
            )
        if state.operation_completed and not state.report:
            try:
                state.report = _operation_report(state)
            except ActionError:
                # Malformed operation evidence cannot replace the primary failure.
                pass
        return _planned_failure(
            args,
            args.kind,
            state.plan,
            state.failure_source,
            reason,
            phase=state.phase,
            mutated=state.mutated,
            session=state.session,
            access=state.access,
            result=state.report,
            cleanup_errors=cleanup_errors,
            started=state.started,
        )

    def execute(self) -> int:
        """Run the prepared sequence through the shared structured CLI boundary.

        :returns: Report exit code, including a structured failure for invalid input,
            provider failures, incomplete verification, or cleanup failure.
        """
        try:
            self.prepare()
            if self.mode not in {"describe", "offline"}:
                WorkflowExecutor(self.state, self.callbacks()).run()
                if self.mode == "live-plan":
                    self.finish_plan()
                else:
                    self.state.report = _operation_report(self.state)
            return emit_result(self.state.report, self.state.args)
        except Exception as exc:  # noqa: BLE001 - structured command boundary
            return self.failure(exc)


def operation_callback(plan: ActionPlan) -> GitLabOperation:
    """Choose the concrete operation declared by an already prepared plan.

    :param plan: Required operation inputs, independent of workflow state.
    :returns: Selected milestone, runner, issue, or wiki callback.
    """
    from lib.python.core.gitlab.gitlab_milestones import (
        CreateMilestone,
        UpdateMilestone,
    )
    from lib.python.core.gitlab.gitlab_runners import (
        AttachToProject,
        CreateRunner,
        DeleteRunner,
        ReadRunner,
        SetTag,
    )

    callbacks: Mapping[str, Mapping[str, type[GitLabOperation]]] = {
        "gitlab_runner": {
            "assign": AttachToProject,
            "create": CreateRunner,
            "delete": DeleteRunner,
            "get": ReadRunner,
            "list": ReadRunner,
            "tag": SetTag,
        },
        "gitlab_milestone": {
            "create": CreateMilestone,
            "update": UpdateMilestone,
            "adjust-time": UpdateMilestone,
        },
    }
    selected = callbacks.get(plan.kind, {}).get(plan.operation, GitLabOperation)
    return selected(action=plan)
