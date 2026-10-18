"""Plan, create, and independently verify one GitLab pipeline.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import re
from collections.abc import MutableSequence
from dataclasses import replace
from typing import Optional
from urllib.parse import quote

from lib.python.core.abstract.callback import AbstractCallback
from lib.python.core.abstract.callbacks import Callbacks
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.action_modes import ActionMode, validate_action_mode
from lib.python.core.gitlab.callback import GitlabCallback, GitLabState
from lib.python.core.gitlab.cli import resolve_gitlab_target
from lib.python.core.gitlab.gitlab_action import (
    GITLAB_MODE_POLICY,
    ConfirmGitLabAction,
    ConfirmGitLabOperation,
    GitLabAuth,
    GitLabAuthAction,
    GitLabWorkflow,
)
from lib.python.core.gitlab.gitlab_actions import (
    ActionError,
    ActionPlan,
    _result,
    _verified_skill_revision,
)
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    copy_json,
    copy_json_object,
    json_object,
)
from lib.python.core.target import GitLabOperationTarget
from lib.python.sys.status import DRY_RUN, PLANNED

PIPELINE_KIND = "gitlab_pipeline"
DEFAULT_REQUEST_TIMEOUT = 25
_FULL_SHA = re.compile(r"[0-9a-f]{40}")
_VARIABLE_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _variables(values: list[str]) -> list[JSONObject]:
    """Parse repeatable GitLab pipeline variables without exposing them in reports.

    :param values: Caller-supplied ``KEY=VALUE`` strings.
    :returns: Ordered GitLab variable objects retained only in the private plan body.
    :raises ActionError: A key is malformed or repeated, or a value lacks ``=``.
    """
    selected: list[JSONObject] = []
    names: set[str] = set()
    for value in values:
        key, separator, content = value.partition("=")
        if not separator or not _VARIABLE_KEY.fullmatch(key):
            raise ActionError("pipeline_variable_must_be_key_value")
        if key in names:
            raise ActionError("pipeline_variable_key_repeated")
        names.add(key)
        selected.append({"key": key, "value": content})
    return selected


def _project_target(
    target: GitLabOperationTarget,
    project: Optional[str],
    target_source: str,
) -> tuple[str, str]:
    """Select the exact project and record whether argv or target supplied it.

    :param target: Resolved GitLab runtime target.
    :param project: Optional explicit project path or numeric identifier.
    :param target_source: Source that selected the target file.
    :returns: Project reference and reportable target-source chain.
    :raises ActionError: Neither argv nor the target declares a project.
    """
    selected = project or target.gitlab.project
    if not selected:
        raise ActionError("project_target_required")
    selector = "argv:--project" if project else "target:gitlab.project"
    return selected, f"{target_source};{selector}"


def prepare_start_plan(
    args: argparse.Namespace,
    target: GitLabOperationTarget,
    target_source: str,
) -> ActionPlan:
    """Create a non-contacting pipeline plan awaiting live ref resolution.

    :param args: Parsed start inputs and output controls.
    :param target: GitLab target selected without credential binding.
    :param target_source: Source that selected the target file.
    :returns: Pipeline action plan whose digest changes after live SHA resolution.
    :raises ActionError: Project, variables, or expected SHA are invalid.
    """
    project, source = _project_target(target, args.project, target_source)
    expected_sha = args.expected_sha
    if expected_sha is not None and not _FULL_SHA.fullmatch(expected_sha):
        raise ActionError("expected_sha_must_be_full_lowercase_commit_sha")
    body: dict[str, JSONValue] = {
        "requested_ref": args.ref,
        "variables": _variables(args.variable),
        "require_protected_ref": args.require_protected_ref,
        "expected_sha": expected_sha,
    }
    return ActionPlan(
        kind=PIPELINE_KIND,
        operation="start",
        origin=target.gitlab.url,
        target_kind="project",
        target_reference=project,
        target_file=str(target.source_file),
        target_source=source,
        body=body,
        revision=args.revision,
    )


def _provider_text(value: JSONValue, field: str) -> str:
    """Validate a required provider string.

    :param value: Decoded provider field.
    :param field: Field name used in the stable failure reason.
    :returns: Nonblank provider text without changing its value.
    :raises ActionError: The field is absent, nontext, or blank.
    """
    if not isinstance(value, str) or not value.strip():
        raise ActionError(f"pipeline_{field}_invalid")
    return value


def _positive_id(value: JSONValue, field: str) -> int:
    """Validate a positive provider identity without accepting booleans.

    :param value: Decoded provider identifier.
    :param field: Field name used in the stable failure reason.
    :returns: Positive provider identifier.
    :raises ActionError: The field is not a positive built-in integer.
    """
    if type(value) is not int or value <= 0:
        raise ActionError(f"pipeline_{field}_invalid")
    return value


def _resolved_start_plan(state: GitLabState) -> ActionPlan:
    """Resolve the default branch and exact commit through the bound provider.

    :param state: Access-verified GitLab workflow state.
    :returns: Replacement plan whose digest covers the exact ref and commit SHA.
    :raises ActionError: Provider project, branch, commit, protection, or expected
        SHA evidence is malformed or does not satisfy the request.
    :raises GitLabAPIError: A required GitLab read fails.
    """  # noqa: DOC503 - provider transport errors propagate unchanged.
    service, plan = state.gitlab, state.plan
    project = json_object(
        service.api.get_json(service.session, f"projects/{service.target_id}")
    )
    if _positive_id(project.get("id"), "project_id") != service.target_id:
        raise ActionError("pipeline_project_identity_mismatch")
    requested_ref = plan.body.get("requested_ref")
    ref = (
        _provider_text(requested_ref, "requested_ref")
        if requested_ref is not None
        else _provider_text(project.get("default_branch"), "default_branch")
    )
    encoded_ref = quote(ref, safe="")
    commit = json_object(
        service.api.get_json(
            service.session,
            f"projects/{service.target_id}/repository/commits/{encoded_ref}",
        )
    )
    sha = _provider_text(commit.get("id"), "resolved_sha")
    if not _FULL_SHA.fullmatch(sha):
        raise ActionError("pipeline_resolved_sha_invalid")
    expected_sha = plan.body.get("expected_sha")
    if expected_sha is not None and expected_sha != sha:
        raise ActionError("pipeline_expected_sha_mismatch")
    protected = False
    if plan.body.get("require_protected_ref") is True:
        branch = json_object(
            service.api.get_json(
                service.session,
                f"projects/{service.target_id}/repository/branches/{encoded_ref}",
            )
        )
        protected = branch.get("protected") is True
        if not protected:
            raise ActionError("pipeline_ref_not_protected")
    body = copy_json_object(plan.body)
    body.update({"ref": ref, "resolved_sha": sha, "protected_ref": protected})
    return replace(plan, body=body)


class ResolvePipelineStart(GitlabCallback[ActionPlan]):
    """Bind one pipeline start plan to current project and ref state."""

    def run(self) -> JSONObject:
        """Resolve the selected ref and replace the shared plan.

        :returns: Safe ref, SHA, protection, and target evidence.
        :raises ActionError: Live project or ref evidence violates the request.
        :raises GitLabAPIError: A provider read fails.
        """  # noqa: DOC502,DOC503 - delegated resolver propagates provider failures.
        self.state.failure_source = "pipeline.start.preflight"
        self.state.plan = _resolved_start_plan(self.state)
        body = self.state.plan.body
        return {
            "project_id": self.gitlab.target_id,
            "ref": body["ref"],
            "sha": body["resolved_sha"],
            "protected": body["protected_ref"],
        }


def _pipeline_readback(value: JSONValue, project_id: int) -> JSONObject:
    """Validate one created pipeline's provider identity and state.

    :param value: Decoded pipeline response.
    :param project_id: Access-verified project identifier.
    :returns: Provider object after required identity validation.
    :raises ActionError: The response omits or changes required fields.
    """
    item = json_object(value)
    _positive_id(item.get("id"), "id")
    if _positive_id(item.get("project_id"), "project_id") != project_id:
        raise ActionError("pipeline_project_identity_mismatch")
    for field in ("ref", "sha", "status"):
        _provider_text(item.get(field), field)
    return item


def start_pipeline(state: GitLabState) -> JSONObject:
    """Create one pipeline and independently read its exact identity back.

    :param state: Confirmed, access-verified pipeline workflow state.
    :returns: Mutation record with the new pipeline ID, SHA, and GET read-back.
    :raises ActionError: The plan is unresolved or provider identity differs.
    :raises GitLabAPIError: The POST or independent GET fails.
    """  # noqa: DOC503 - provider transport errors propagate unchanged.
    service, plan = state.gitlab, state.plan
    ref = _provider_text(plan.body.get("ref"), "ref")
    sha = _provider_text(plan.body.get("resolved_sha"), "resolved_sha")
    variables = plan.body.get("variables")
    if not isinstance(variables, list):
        raise ActionError("pipeline_variables_invalid")
    request: dict[str, JSONValue] = {"ref": ref, "variables": copy_json(variables)}
    state.mutated = None
    created = _pipeline_readback(
        service.api.post_json(
            service.session, f"projects/{service.target_id}/pipeline", request
        ),
        service.target_id,
    )
    state.mutated = True
    pipeline_id = _positive_id(created.get("id"), "id")
    observed = _pipeline_readback(
        service.api.get_json(
            service.session,
            f"projects/{service.target_id}/pipelines/{pipeline_id}",
        ),
        service.target_id,
    )
    if (
        observed.get("id") != pipeline_id
        or observed.get("ref") != ref
        or observed.get("sha") != sha
    ):
        raise ActionError("pipeline_readback_identity_mismatch")
    return {
        "action": "APPLIED",
        "mutated": True,
        "verified": True,
        "id": pipeline_id,
        "ref": ref,
        "sha": sha,
        "status": observed["status"],
        "after": {
            "id": pipeline_id,
            "project_id": service.target_id,
            "ref": observed["ref"],
            "sha": observed["sha"],
            "status": observed["status"],
        },
        "cleanup": {"status": "NOT_APPLICABLE"},
        "errors": [],
    }


class StartPipeline(GitlabCallback[ActionPlan]):
    """Create the live-resolved pipeline and retain mutation evidence."""

    def collect(self) -> JSONObject:
        """Require the resolver to have populated the current plan.

        :returns: Empty mapping after entering the apply boundary.
        :raises ActionError: The exact ref and commit were not resolved live.
        """
        if (
            "ref" not in self.state.plan.body
            or "resolved_sha" not in self.state.plan.body
        ):
            raise ActionError("pipeline_start_plan_not_live_resolved")
        self.state.phase = "APPLY"
        self.state.failure_source = "pipeline.start"
        self.state.mutated = None
        return {}

    def run(self) -> JSONObject:
        """Create and independently verify the selected pipeline.

        :returns: Provider mutation and read-back record.
        :raises ActionError: Provider identity differs from the confirmed plan.
        :raises GitLabAPIError: The provider mutation or read-back fails.
        """  # noqa: DOC502,DOC503 - delegated operation propagates provider failures.
        return start_pipeline(self.state)

    def update(self) -> JSONObject:
        """Retain the verified operation record in shared state.

        :returns: This callback's run result.
        """
        self.state.operation_result = self.result
        self.state.operation_completed = True
        self.state.mutated = True
        return self.result


class PipelineStartWorkflow(GitLabWorkflow):
    """Use the shared GitLab lifecycle for one pipeline start invocation."""

    def prepare(self) -> None:
        """Validate mode and build the unresolved non-contacting plan.

        :raises ActionError: Mode, output controls, or confirmation are invalid.
        :raises TargetError: GitLab target selection fails.
        """  # noqa: DOC503 - target resolver propagates selection failures.
        args = self.state.args
        self.state.failure_source = "arguments"
        request_timeout = (
            args.timeout if args.timeout is not None else DEFAULT_REQUEST_TIMEOUT
        )
        try:
            self.mode = validate_action_mode(
                apply=bool(args.apply),
                dry_run=bool(args.dry_run),
                live_plan=bool(args.live_plan),
                confirmed=bool(args.confirm_plan),
                receipt_requested=bool(args.receipt_out),
                output_requested=bool(args.output_dir or args.log_file),
                timeout=request_timeout,
                policy=GITLAB_MODE_POLICY,
            )
        except ValueError as exc:
            raise ActionError(str(exc)) from exc
        target, source = resolve_gitlab_target(
            args.target, args.binding, dry_run=self.mode == ActionMode.OFFLINE
        )
        self.target = target
        self.state.plan = prepare_start_plan(args, target, source)
        if self.mode == ActionMode.OFFLINE:
            self.state.report = _result(
                self.state.plan,
                DRY_RUN,
                phase="OFFLINE_PLAN",
                mutated=False,
                readback={"verified": False, "reason": "live_plan_required"},
            )
            public = copy_json_object(json_object(self.state.report["plan"]))
            public["confirmation_ready"] = False
            self.state.report["plan"] = public
        elif self.mode == ActionMode.APPLY and not args.confirm_plan:
            raise ActionError("confirm_plan_mismatch_rerun_live_plan")

    def callbacks(self) -> Callbacks[GitLabState]:
        """Construct access, resolution, confirmation, and creation callbacks.

        :returns: Ordered shared-state callback collection.
        :raises ActionError: Called for an offline invocation.
        """
        if self.mode == ActionMode.OFFLINE:
            raise ActionError("offline_workflow_has_no_provider_callbacks")
        args = self.state.args
        request_timeout = (
            args.timeout if args.timeout is not None else DEFAULT_REQUEST_TIMEOUT
        )
        ordered: MutableSequence[AbstractCallback[GitLabState, SkillAction]] = [
            GitLabAuth(GitLabAuthAction(self.target, request_timeout, args.revision)),
            ResolvePipelineStart(self.state.plan),
        ]
        if self.mode == ActionMode.APPLY:
            ordered.extend(
                (
                    ConfirmGitLabOperation(ConfirmGitLabAction(args.confirm_plan)),
                    StartPipeline(self.state.plan),
                )
            )
        return Callbacks(ordered)

    def finish_plan(self) -> None:
        """Publish the current ref and SHA as an apply-ready live plan."""
        plan, session = self.state.plan, self.state.gitlab.session
        self.state.phase = "LIVE_PLAN"
        body = plan.body
        self.state.report = _result(
            plan,
            PLANNED,
            phase="LIVE_PLAN",
            mutated=False,
            identity=self.state.access.get("identity"),
            verified_target=self.state.access.get("target"),
            credential_source=session.credential_source,
            credential_digest=session.credential_digest,
            skill=copy_json(session.skill),
            readback={
                "verified": True,
                "project_id": self.state.gitlab.target_id,
                "ref": body["ref"],
                "sha": body["resolved_sha"],
                "protected": body["protected_ref"],
            },
        )
        self.state.report["tested_revision"] = _verified_skill_revision(session)


def start_workflow(args: argparse.Namespace) -> PipelineStartWorkflow:
    """Create an unexecuted pipeline start workflow from parsed arguments.

    :param args: Validated command namespace.
    :returns: Workflow retaining a fresh GitLab shared state.
    """
    return PipelineStartWorkflow(GitLabState(args=args))
