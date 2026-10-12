"""Compose GitLab authentication, actions, and reports using shared state."""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field as dataclass_field, replace
from typing import cast

from lib.python.access.access import check_gitlab_operation_access
from lib.python.access.credentials import bind_gitlab_session
from lib.python.core.abstract.context import ExecutionContext
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.abstract.skill_exector import WorkflowExecutor
from lib.python.core.abstract.skill_result import SkillResult
from lib.python.core.cli import _failure, resolve_gitlab_target
from lib.python.core.gitlab.abstract_github_callback import GitlabCallback
from lib.python.core.gitlab.gitlab_actions import (
    ActionError, ActionPlan, _access_failure, _id, _object, _planned_failure,
    _result, _verified_skill_revision, apply_plan, make_plan,
)
from lib.python.core.gitlab.gitlab_api import GitLabAPIError, GlabAPIClient
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession, GitLabService
from lib.python.core.report import emit_result
from lib.python.core.runtime import sanitize
from lib.python.core.target import TargetError
from lib.python.sys.status import DRY_RUN, PARTIAL, PASS, PLANNED


@dataclass
class GitLabAction(ExecutionContext, SkillAction):
    """Parsed workflow inputs and the existing plan/report state.

    .. attribute :: args
        This workflow's parsed arguments.

    .. attribute :: plan
        Prepared provider operation.

    .. attribute :: session
        Bound credential and target identity.

    .. attribute :: access
        Existing access-check evidence.

    .. attribute :: report
        Existing operation report.

    .. attribute :: phase
        Last entered operation phase.

    .. attribute :: mutated
        Known write state, or None for an uncertain outcome.

    .. attribute :: failure_source
        Existing failure classification.

    .. attribute :: started
        Monotonic start time for diagnostics.

    """

    plan: ActionPlan = dataclass_field(init=False)
    session: BoundGitLabSession = dataclass_field(init=False)
    access: dict[str, object] = dataclass_field(default_factory=dict)
    phase: str = "OFFLINE_PLAN"
    mutated: bool | None = False
    failure_source: str = "arguments"
    started: float = dataclass_field(default_factory=time.monotonic)

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> GitLabAction:
        """Bind a workflow's own arguments without provider access.

        :param args: Arguments from the existing action parser.
        :returns: Unexecuted action state.
        """
        return cls(args=args)

    def prepare(self) -> None:
        """Validate and plan using the existing target and operation adapters.

        :raises ActionError: If arguments or the selected operation are invalid.
        """
        args, kind = self.args, self.args.kind
        if args.describe:
            from lib.python.core.catalog import COMMAND_BY_KIND, describe

            self.report = describe(COMMAND_BY_KIND[kind])
            return
        if not args.action:
            raise ActionError("action_required")
        runner_read = kind == "gitlab_runner" and args.action in {"get", "list"}
        if runner_read and (args.apply or args.confirm_plan or args.live_plan):
            raise ActionError("runner_read_rejects_apply_options")
        if runner_read and args.dry_run and (args.output_dir or args.log_file):
            raise ActionError("dry_run_cannot_write_output")
        if args.apply and args.dry_run:
            raise ActionError("apply_and_dry_run_conflict")
        live_plan = getattr(args, "live_plan", False)
        if live_plan and (args.apply or args.dry_run):
            raise ActionError("live_plan_mode_conflict")
        if args.timeout <= 0 or args.timeout > 300:
            raise ActionError("timeout_out_of_range")
        if args.confirm_plan and not args.apply:
            raise ActionError("confirm_plan_requires_apply")
        if args.receipt_out and not (args.apply or (runner_read and not args.dry_run)):
            raise ActionError("receipt_out_requires_live_run")
        if (
            not args.apply
            and not live_plan
            and not runner_read
            and (args.output_dir or args.log_file)
        ):
            raise ActionError("dry_run_cannot_write_output")
        self.failure_source = "gitlab_action"
        runner_delete = kind == "gitlab_runner" and args.action == "delete"
        target, target_source = resolve_gitlab_target(
            args.target,
            args.binding,
            dry_run=not args.apply
            and not live_plan
            and (not runner_read or args.dry_run),
        )
        plan = make_plan(kind, args, target, target_source)
        self.plan = plan
        group_assignment = (
            plan.kind == "gitlab_runner"
            and plan.operation == "assign"
            and plan.target_kind == "group"
        )
        if live_plan and not (group_assignment or runner_delete):
            raise ActionError("live_plan_requires_group_assignment_or_runner_delete")
        self.plan = plan
        self.target = target
        if (runner_read and args.dry_run) or (
            not args.apply and not live_plan and not runner_read
        ):
            self.report = _result(plan, DRY_RUN, phase="OFFLINE_PLAN", mutated=False)

    def authenticate(self) -> SkillResult:
        """Bind credentials and verify the target using the existing access path.

        :returns: Verified provider service, or None for offline modes.
        :raises ActionError: If confirmation, identity, or access is invalid.
        """
        if self.report:
            return self.report
        args, plan, target = self.args, self.plan, self.target
        group_assignment = (
            plan.kind == "gitlab_runner"
            and plan.operation == "assign"
            and plan.target_kind == "group"
        )
        runner_delete = plan.kind == "gitlab_runner" and plan.operation == "delete"
        self.phase = "ACCESS"
        if (
            args.apply
            and not (group_assignment or runner_delete)
            and (not args.confirm_plan or args.confirm_plan != plan.digest)
        ):
            raise ActionError("confirm_plan_mismatch_rerun_dry_run")
        session = bind_gitlab_session(
            target,
            target_kind=plan.target_kind,
            target_reference=plan.target_reference,
            target_source=plan.target_source,
            revision=args.revision,
        )
        self.session = session
        if (
            plan.kind == "gitlab_runner"
            and plan.operation in {"tag", "get", "list", "delete"}
            and _verified_skill_revision(session) is None
        ):
            raise ActionError("skill_revision_unverified")
        api = GlabAPIClient(timeout=args.timeout)
        access = check_gitlab_operation_access(session, api_client=api)
        self.access = access
        if access.get("status") != PASS:
            self.failure_source, reason = _access_failure(access)
            self.session = session
            raise ActionError(reason)
        if group_assignment:
            from lib.python.core.gitlab.gitlab_runners import project_ids

            selected = _object(access.get("target"), "access_target", "kind", "id")
            if selected["kind"] != "group":
                raise ActionError("access_target_kind_mismatch")
            plan = replace(
                plan,
                project_ids=project_ids(api, session, _id(selected["id"], "target")),
            )
        if runner_delete:
            from lib.python.core.gitlab.gitlab_runners import delete_snapshot

            selected = _object(access.get("target"), "access_target", "kind", "id")
            plan = replace(
                plan,
                runner_snapshot=delete_snapshot(
                    api, session, plan, _id(selected["id"], "target")
                ),
            )
        self.session, self.access, self.plan = session, access, plan
        if getattr(args, "live_plan", False):
            self.phase = "LIVE_PLAN"
            data = _result(
                plan,
                PLANNED,
                phase="LIVE_PLAN",
                mutated=False,
                identity=access.get("identity"),
                verified_target=access.get("target"),
                credential_source=session.credential_source,
                credential_digest=session.credential_digest,
                skill=session.skill,
                readback={
                    "verified": True,
                    **(
                        {"runner_snapshot": plan.runner_snapshot}
                        if runner_delete
                        else {"project_ids": list(plan.project_ids or ())}
                    ),
                },
            )
            self.report = data
            self.report["tested_revision"] = _verified_skill_revision(session)
        selected = _object(access.get("target"), "access_target", "kind", "id")
        self.services["gitlab"] = GitLabService(api, session, _id(selected["id"], "target"), access)
        return self.report or access

    def begin(self) -> None:
        """Preserve confirmation and bound-target guards before an operation.

        :raises ActionError: If confirmation or bound target does not match.
        """
        plan, args = self.plan, self.args
        if plan.kind == "gitlab_runner" and plan.operation in {"get", "list"}:
            self.phase = "READ"
            return
        if not args.confirm_plan or args.confirm_plan != plan.digest:
            raise ActionError("confirm_plan_mismatch_rerun_live_plan")
        self.phase, self.mutated = "APPLY", None
        plan.verified_target_id(self.session, self.access)

    def finish(self, record: SkillResult) -> None:
        """Use the existing result contract for a read or mutation.

        :param record: Typed observation or provider action record.
        """
        plan, session, access = self.plan, self.session, self.access
        if self.phase == "READ":
            from lib.python.core.gitlab.gitlab_runners import RunnerReadResult

            observation = RunnerReadResult(**record)
            data = _result(
                plan,
                PASS,
                phase="READ",
                mutated=False,
                records=observation.records,
                identity=access.get("identity"),
                verified_target=access.get("target"),
                credential_source=session.credential_source,
                credential_digest=session.credential_digest,
                skill=session.skill,
                readback={
                    "verified": True,
                    "record_count": len(observation.records),
                    "truncated": observation.truncated,
                },
                related_job=observation.related_job,
            )
        else:
            if isinstance(record, dict):
                self.mutated = record.get("mutated", record.get("action") == "APPLIED")
            record = cast(dict[str, object], record)
            errors = [
                {
                    "source": f"project:{error['project_id']}",
                    "reason": error["reason"],
                }
                for error in record.get("errors", [])
            ]
            cleanup = record.get("cleanup", {"status": "NOT_APPLICABLE"})
            complete = (
                record.get("verified") is True
                and not errors
                and cleanup.get("status") not in {"BLOCKED", "PARTIAL"}
            )
            data = _result(
                plan,
                PASS if complete else PARTIAL,
                phase="APPLY",
                mutated=record.get("mutated", record.get("action") == "APPLIED"),
                result_action=record.get("action"),
                records=[record],
                errors=errors,
                identity=access.get("identity"),
                verified_target=access.get("target"),
                readback=record,
                credential_source=session.credential_source,
                credential_digest=session.credential_digest,
                skill=session.skill,
                cleanup=cleanup,
            )
            mutated = data["mutated"]
            self.mutated = mutated
        data["tested_revision"] = _verified_skill_revision(session)
        self.report = data

    def failure(self, exc: Exception) -> int:
        """Retain the existing structured CLI failure and unknown-write evidence.

        :param exc: Failure raised by execution or output handling.
        :returns: Existing blocked exit code after emitting the failure report.
        """
        reason = (
            sanitize(str(exc), 240)
            if isinstance(
                exc, (ActionError, TargetError, GitLabAPIError, OSError, RuntimeError)
            )
            else "unexpected_runtime_failure"
        )
        cleanup_errors = [
            {"source": f"cleanup.{failure.callback}", "reason": failure.reason}
            for failure in getattr(exc, "cleanup_failures", ())
        ]
        if not hasattr(self, "plan"):
            return _failure(
                self.args,
                self.args.kind,
                self.failure_source,
                reason,
                cleanup_errors=cleanup_errors,
            )
        return _planned_failure(
            self.args,
            self.args.kind,
            self.plan,
            self.failure_source,
            reason,
            phase=self.phase,
            mutated=self.mutated,
            session=getattr(self, "session", None),
            access=self.access,
            result=self.report,
            cleanup_errors=cleanup_errors,
            started=self.started,
        )

    def execute(self, workflow: Callable[[], WorkflowExecutor]) -> int:
        """Run the composed workflow through the shared structured CLI boundary.

        :param workflow: Build the callback composition declared by this command.
        :returns: Existing report or structured failure exit code.
        """
        try:
            return emit_result(workflow().run(), self.args)
        except Exception as exc:  # noqa: BLE001 - structured command boundary
            return self.failure(exc)



class GitLabAuth(GitlabCallback[GitLabAction]):
    """Prepare an action and establish its existing authenticated service."""

    def __init__(self, *, action: GitLabAction) -> None:
        """Require the concrete workflow inputs before authentication.

        :param action: Parsed action state owned by this workflow.
        """
        super().__init__(action=action)

    def collect(self) -> SkillResult:
        """Validate arguments before provider access."""
        self.action.prepare()
        return self.state.result

    def run(self) -> SkillResult:
        """Bind the requested service or produce the offline response.

        :returns: Verified service when live execution was selected.
        """
        return self.action.authenticate()

    def update(self) -> SkillResult:
        """Keep the prepared report in the shared workflow state.

        :returns: Offline report or authenticated access evidence.
        """
        return self.action.report or self.state.result

    @property
    def complete(self) -> bool:
        """Continue only when the action needs a live operation.

        :returns: False for describe, offline plan, or completed live plan.
        """
        return not self.action.report


class GitLabOperation(
    GitlabCallback[ActionPlan | GitLabAction]
):
    """Shared plan and report hooks for concrete GitLab operations."""

    @property
    def plan(self) -> ActionPlan:
        """Resolve typed CLI inputs or an existing direct-call plan.

        :returns: The operation's prepared plan.
        :raises ActionError: If no action has been prepared.
        """
        plan = (
            self.action.plan if isinstance(self.action, GitLabAction) else self.action
        )
        if plan is None:
            raise ActionError("action_required")
        return plan

    def collect(self) -> SkillResult:
        """Apply the existing workflow guards at the operation boundary."""
        if isinstance(self.action, GitLabAction):
            self.action.begin()
        return self.state.result

    def update(self) -> SkillResult:
        """Adapt typed results to the existing CLI output contract."""
        if isinstance(self.action, GitLabAction):
            self.action.finish(self.result)
            return self.action.report
        return self.state.result

    def run(self) -> SkillResult:
        """Dispatch operations not yet moved from the existing adapter.

        :returns: Existing provider action record.
        """
        service = self.gitlab
        return apply_plan(service.api, service.session, service.access, self.plan)


def operation_callback(action: GitLabAction) -> GitLabOperation:
    """Choose a concrete existing operation without executing it.

    :param action: Arguments and plan state owned by this workflow.
    :returns: The selected operation callback.
    """
    from lib.python.core.gitlab.gitlab_milestones import CreateMilestone, UpdateMilestone
    from lib.python.core.gitlab.gitlab_runners import (
        AttachToProject,
        CreateRunner,
        DeleteRunner,
        ReadRunner,
        SetTag,
    )

    callbacks = {
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
    callback = callbacks.get(action.args.kind, {}).get(
        action.args.action, GitLabOperation
    )
    return callback(action=action)
