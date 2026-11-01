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
from urllib.parse import quote, urlencode

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
    _array,
    _pages,
    _result,
    _verified_skill_revision,
)
from lib.python.core.gitlab.gitlab_api import (
    GitLabAPIError,
    create_guard,
    uncertain_write,
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
PIPELINE_PLAN_VARIABLE = "CI_SKILLS_PIPELINE_PLAN_DIGEST"
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
        if key == PIPELINE_PLAN_VARIABLE:
            raise ActionError("pipeline_variable_reserved_for_plan_identity")
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
        "run_id": args.run_id,
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


def _pipeline_has_plan(state: GitLabState, pipeline_id: int) -> bool:
    """Read the managed variable that associates a pipeline with one plan.

    :param state: Access-verified state holding the confirmed plan.
    :param pipeline_id: Exact candidate pipeline ID in the selected project.
    :returns: Whether this pipeline carries the confirmed plan digest.
    :raises ActionError: Variable evidence is malformed or repeats the managed key.
    :raises GitLabAPIError: Provider variable read-back fails.
    """  # noqa: DOC503 - provider errors propagate unchanged.
    service = state.gitlab
    variables = _array(
        service.api.get_json(
            service.session,
            f"projects/{service.target_id}/pipelines/{pipeline_id}/variables",
        ),
        "pipeline_variables",
    )
    markers = [item for item in variables if item.get("key") == PIPELINE_PLAN_VARIABLE]
    if len(markers) > 1:
        raise ActionError("pipeline_plan_marker_repeated")
    if markers and not isinstance(markers[0].get("value"), str):
        raise ActionError("pipeline_plan_marker_invalid")
    return bool(markers) and markers[0].get("value") == state.plan.digest


def _find_started_pipeline(state: GitLabState) -> Optional[int]:
    """Find one exact plan marker after completely reading the bounded candidates.

    :param state: Access-verified state with live-resolved ref and SHA.
    :returns: Matching pipeline ID, or None after a complete unmatched read.
    :raises ActionError: Candidate identities are malformed or multiple IDs match.
    :raises GitLabAPIError: Candidate or variable reads cannot prove the result.
    """  # noqa: DOC502,DOC503 - shared validators and provider errors propagate.
    service, plan = state.gitlab, state.plan
    ref = _provider_text(plan.body.get("ref"), "ref")
    sha = _provider_text(plan.body.get("resolved_sha"), "resolved_sha")
    query = urlencode({"ref": ref, "sha": sha, "source": "api"})
    candidates = _pages(
        service.api, service.session, f"projects/{service.target_id}/pipelines?{query}"
    )
    matches: set[int] = set()
    seen: set[int] = set()
    for candidate in candidates:
        pipeline_id = _positive_id(candidate.get("id"), "id")
        if pipeline_id in seen:
            continue
        seen.add(pipeline_id)
        if (
            candidate.get("ref") != ref
            or candidate.get("sha") != sha
            or candidate.get("project_id", service.target_id) != service.target_id
        ):
            raise ActionError("pipeline_list_reference_mismatch")
        if _pipeline_has_plan(state, pipeline_id):
            matches.add(pipeline_id)
    if len(matches) > 1:
        raise ActionError("pipeline_plan_identity_ambiguous")
    return next(iter(matches)) if matches else None


def _started_pipeline_result(
    state: GitLabState, pipeline_id: int, *, applied: bool
) -> JSONObject:
    """Independently verify one exact pipeline before reporting apply or replay.

    :param state: Access-verified workflow state and confirmed operation plan.
    :param pipeline_id: Positive pipeline identity from creation or reconciliation.
    :param applied: Whether this invocation dispatched the creating POST.
    :returns: Verified pipeline record with truthful mutation evidence.
    :raises ActionError: The observed ID, project, ref, SHA or plan marker differs.
    :raises GitLabAPIError: Independent provider read-back fails.
    """  # noqa: DOC503 - provider errors propagate unchanged.
    service, plan = state.gitlab, state.plan
    observed = _pipeline_readback(
        service.api.get_json(
            service.session, f"projects/{service.target_id}/pipelines/{pipeline_id}"
        ),
        service.target_id,
    )
    if (
        observed.get("id") != pipeline_id
        or observed.get("ref") != plan.body.get("ref")
        or observed.get("sha") != plan.body.get("resolved_sha")
        or not _pipeline_has_plan(state, pipeline_id)
    ):
        raise ActionError("pipeline_readback_identity_mismatch")
    state.mutated = applied
    return {
        "action": "APPLIED" if applied else "NO_OP",
        "mutated": applied,
        "verified": True,
        "id": pipeline_id,
        "ref": observed["ref"],
        "sha": observed["sha"],
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


def _start_request(plan: ActionPlan) -> JSONObject:
    """Add the plan marker without changing caller variables or hashing itself.

    :param plan: Confirmed pipeline inputs before adding provider correlation.
    :returns: Native create-pipeline request with one managed plan variable.
    :raises ActionError: Variables are malformed or collide with the managed key.
    """  # noqa: DOC502 - shared validators propagate malformed input failures.
    variables = _array(plan.body.get("variables"), "pipeline_variables")
    if any(item.get("key") == PIPELINE_PLAN_VARIABLE for item in variables):
        raise ActionError("pipeline_variable_reserved_for_plan_identity")
    return {
        "ref": _provider_text(plan.body.get("ref"), "ref"),
        "variables": [
            *variables,
            {
                "key": PIPELINE_PLAN_VARIABLE,
                "value": plan.digest,
                "variable_type": "env_var",
            },
        ],
    }


def start_pipeline(state: GitLabState) -> JSONObject:
    """Create or reconcile one confirmed pipeline without replaying a POST.

    :param state: Confirmed, access-verified pipeline workflow state.
    :returns: Verified APPLIED or NO_OP record for the exact confirmed plan.
    :raises ActionError: Inputs, ambiguous matches or provider identities are invalid.
    :raises GitLabAPIError: Provider reads fail or an uncertain create remains unresolved.
    :raises OSError: The shared create guard or pending marker cannot be accessed.
    """  # noqa: DOC503 - shared transport and guard I/O failures propagate.
    service, plan = state.gitlab, state.plan
    request = _start_request(plan)
    state.mutated = False
    with create_guard(
        plan.origin, plan.target_kind, service.target_id, PIPELINE_KIND, plan.digest
    ) as guard:
        existing_id = _find_started_pipeline(state)
        if existing_id is not None:
            result = _started_pipeline_result(state, existing_id, applied=False)
            guard.clear()
            return result
        guard.reconcile_absent()
        guard.mark_pending()
        state.mutated = None
        try:
            response = service.api.post_json(
                service.session, f"projects/{service.target_id}/pipeline", request
            )
        except GitLabAPIError as exc:
            if not uncertain_write(exc):
                guard.clear()
                state.mutated = False
                raise
            reconciled_id = _find_started_pipeline(state)
            if reconciled_id is None:
                raise GitLabAPIError("pipeline_start_outcome_uncertain") from exc
            result = _started_pipeline_result(state, reconciled_id, applied=True)
        else:
            state.mutated = True
            created = _pipeline_readback(response, service.target_id)
            pipeline_id = _positive_id(created.get("id"), "id")
            result = _started_pipeline_result(state, pipeline_id, applied=True)
        guard.clear()
        return result


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

        :returns: Provider creation or replay record with independent read-back.
        :raises ActionError: Provider identity differs from the confirmed plan.
        :raises GitLabAPIError: The provider mutation or read-back fails.
        :raises OSError: The shared create guard cannot be acquired or updated.
        """  # noqa: DOC502,DOC503 - delegated operation propagates provider failures.
        return start_pipeline(self.state)

    def update(self) -> JSONObject:
        """Retain the verified operation record in shared state.

        :returns: This callback's run result.
        """
        self.state.operation_result = self.result
        self.state.operation_completed = True
        self.state.mutated = self.result.get("mutated") is True
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
            args.target,
            args.binding,
            dry_run=self.mode == ActionMode.OFFLINE,
            gitlab_profile=getattr(args, "gitlab_profile", None),
        )
        self.target = target
        self.state.plan = prepare_start_plan(args, target, source)
        if self.mode == ActionMode.OFFLINE:
            self.state.report = _result(
                self.state.plan,
                DRY_RUN,
                phase="OFFLINE_PLAN",
                mutated=False,
                readback={"verified": False},
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
