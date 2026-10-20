"""Adapt GitLab pipeline watch actions to the shared callback workflow.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from math import isfinite

from lib.python.core.abstract.callbacks import Callbacks
from lib.python.core.action_modes import (
    ActionMode,
    ActionModePolicy,
    validate_action_mode,
)
from lib.python.core.executor import WorkflowExecutor
from lib.python.core.gitlab.callback import GitlabCallback, GitLabState
from lib.python.core.gitlab.gitlab_action import GitLabAuth, GitLabAuthAction
from lib.python.core.gitlab.gitlab_actions import ActionError, ActionPlan
from lib.python.core.gitlab.gitlab_api import GitLabAPIError
from lib.python.core.gitlab.gitlab_pipeline_watch_runtime import watch_pipeline
from lib.python.core.gitlab.gitlab_pipeline_watch_types import (
    DEFAULT_WATCH_INTERVAL,
    DEFAULT_WATCH_TIMEOUT,
    PIPELINE_WATCH_KIND,
    PipelineWatchAction,
)
from lib.python.core.gitlab.gitlab_pipelines import attach_pipeline_provenance
from lib.python.core.json_types import JSONObject, json_object, json_objects
from lib.python.core.report import emit_result, report
from lib.python.core.runtime import sanitize
from lib.python.core.target import GitLabOperationTarget
from lib.python.sys.status import BLOCKED, DRY_RUN, PARTIAL, PASS

WATCH_MODE_POLICY = ActionModePolicy(
    default_mode=ActionMode.READ,
    receipt_modes=frozenset((ActionMode.READ,)),
    allow_offline_output=False,
)


def _watch_action(args: argparse.Namespace) -> PipelineWatchAction:
    """Validate and normalize one watch selection before provider contact.

    :param args: Parsed watch identifiers and optional timing controls.
    :returns: Concrete root, selector, interval, and deadline.
    :raises ActionError: The pipeline ID, interval, or deadline is invalid.
    """
    pipeline_id = args.pipeline_id
    interval = args.interval if args.interval is not None else DEFAULT_WATCH_INTERVAL
    timeout = args.timeout if args.timeout is not None else DEFAULT_WATCH_TIMEOUT
    if pipeline_id is None or pipeline_id <= 0:
        raise ActionError("pipeline_id_must_be_positive")
    invalid_timing = (
        not isfinite(interval) or not isfinite(timeout) or interval <= 0 or timeout <= 0
    )
    if invalid_timing:
        raise ActionError("pipeline_watch_interval_and_timeout_must_be_positive")
    return PipelineWatchAction(
        pipeline_id=pipeline_id,
        related_name=args.related_name,
        interval=interval,
        timeout=timeout,
    )


def _watch_mode(args: argparse.Namespace, action: PipelineWatchAction) -> ActionMode:
    """Select offline planning or the actual read-only watch.

    :param args: Parsed output, persistence, and dry-run controls.
    :param action: Validated watch action supplying its positive deadline.
    :returns: Offline or read execution mode.
    :raises ActionError: Output persistence or the selected deadline conflicts
        with the shared action-mode contract.
    """
    try:
        return validate_action_mode(
            apply=False,
            dry_run=bool(args.dry_run),
            live_plan=False,
            confirmed=False,
            receipt_requested=bool(args.receipt_out),
            output_requested=bool(args.output_dir or args.log_file),
            timeout=int(action.timeout),
            policy=WATCH_MODE_POLICY,
            read_only=True,
        )
    except ValueError as exc:
        raise ActionError(str(exc)) from exc


def _watch_plan(
    args: argparse.Namespace,
    action: PipelineWatchAction,
    target: GitLabOperationTarget,
    target_source: str,
    reference: str,
) -> ActionPlan:
    """Create the read-only action used by shared GitLab authentication.

    :param args: Watch arguments and revision claim.
    :param action: Validated watch selection and timing controls.
    :param target: Selected GitLab runtime target.
    :param target_source: Source that selected the target file.
    :param reference: Selected project path or numeric identifier.
    :returns: Read plan carried through the shared GitLab state.
    """
    source = f"{target_source};argv:--project" if args.project else target_source
    return ActionPlan(
        kind=PIPELINE_WATCH_KIND,
        operation="watch",
        origin=target.gitlab.url,
        target_kind="project",
        target_reference=reference,
        target_file=str(target.source_file),
        target_source=source,
        body={
            "pipeline_id": action.pipeline_id,
            "related_name": action.related_name,
            "interval": action.interval,
            "timeout": action.timeout,
        },
        revision=args.revision,
    )


def _watch_filters(reference: str, action: PipelineWatchAction) -> JSONObject:
    """Render the public root and optional related selector.

    :param reference: Selected project path or numeric identifier.
    :param action: Validated pipeline watch selection.
    :returns: Public report filters without timing implementation details.
    """
    filters: JSONObject = {
        "project": reference,
        "pipeline_id": action.pipeline_id,
    }
    if action.related_name is not None:
        filters["related_name"] = action.related_name
    return filters


class WatchPipeline(GitlabCallback[PipelineWatchAction]):
    """Execute the bounded pipeline watch through shared GitLab state."""

    def __init__(
        self,
        action: PipelineWatchAction,
        *,
        settled: Callable[[str], None],
    ) -> None:
        """Retain the action and caller-owned settled-event sink.

        :param action: Bounded root pipeline watch selection.
        :param settled: Sink for nonsecret one-line settled events.
        """
        super().__init__(action)
        self._settled = settled

    def run(self) -> JSONObject:
        """Watch the selected pipeline graph to settlement or timeout.

        :returns: Bounded pipeline watch evidence.
        :raises ActionError: Inputs or provider response fields are invalid.
        :raises GitLabAPIError: The root pipeline cannot be read.
        """  # noqa: DOC502,DOC503 - delegated watch propagates root read failures.
        self.state.phase = "READ"
        self.state.failure_source = "pipeline.watch"
        return watch_pipeline(
            self.gitlab.api,
            self.gitlab.session,
            self.gitlab.target_id,
            self.action,
            settled=self._settled,
        )


def _empty_watch_report(
    target: str,
    filters: JSONObject,
    status: str,
    errors: Sequence[JSONObject],
) -> JSONObject:
    """Render a watch outcome before any pipeline observation is available.

    :param target: Selected GitLab URL, or ``unresolved`` before target selection.
    :param filters: Valid selectors known at the failure or planning boundary.
    :param status: Blocked or dry-run outcome.
    :param errors: Classified failures, empty for non-contacting planning.
    :returns: Watch envelope with no fabricated observation or completion.
    """
    data = report(PIPELINE_WATCH_KIND, target, filters, [], errors)
    data.update(
        status=status,
        complete=False,
        success=False,
        polls=0,
        elapsed_seconds=0,
        changes=[],
        choices=[],
        continuation=None,
    )
    return data


def watch_failure_report(args: argparse.Namespace, reason: str) -> JSONObject:
    """Describe a failure before watch target and action resolution complete.

    :param args: Parsed arguments whose selection values may still be invalid.
    :param reason: Sanitized preflight failure reason.
    :returns: BLOCKED watch report retaining only valid requested selectors.
    """
    filters: JSONObject = {}
    if isinstance(args.project, str) and args.project.strip():
        filters["project"] = args.project
    if type(args.pipeline_id) is int and args.pipeline_id > 0:
        filters["pipeline_id"] = args.pipeline_id
    if isinstance(args.related_name, str) and args.related_name.strip():
        filters["related_name"] = args.related_name
    return _empty_watch_report(
        "unresolved",
        filters,
        BLOCKED,
        [{"source": "pipeline.watch", "reason": reason}],
    )


def _offline_watch_report(
    target: GitLabOperationTarget,
    target_source: str,
    reference: str,
    filters: JSONObject,
) -> JSONObject:
    """Describe the bounded provider reads without contacting GitLab.

    :param target: Selected GitLab runtime target.
    :param target_source: Source that selected the target file.
    :param reference: Selected project path or numeric identifier.
    :param filters: Public root and related-pipeline selectors.
    :returns: Complete DRY_RUN report for the watch result schema.
    """
    data = _empty_watch_report(target.gitlab.url, filters, DRY_RUN, [])
    data.update(
        target_source=target_source,
        access={
            "status": DRY_RUN,
            "target": {"kind": "project", "reference": reference},
            "observed_capability": [],
        },
        collection_probes=[
            "exact root pipeline, jobs, bridges and downstream pipelines",
            "optional newer same-ref related pipelines",
            "bounded polling until completion or timeout",
        ],
    )
    return data


def _blocked_watch_report(
    state: GitLabState,
    plan: ActionPlan,
    target: GitLabOperationTarget,
    filters: JSONObject,
    exc: Exception,
) -> JSONObject:
    """Retain authentication or root-read failure as watch-shaped evidence.

    :param state: Partially executed shared workflow state.
    :param plan: Watch plan supplying target-source evidence.
    :param target: Selected GitLab runtime target.
    :param filters: Public root and related-pipeline selectors.
    :param exc: Classified provider or validation failure.
    :returns: BLOCKED report without discarding available access provenance.
    """
    errors: list[JSONObject] = [
        {"source": state.failure_source, "reason": sanitize(str(exc), 240)}
    ]
    data = _empty_watch_report(target.gitlab.url, filters, BLOCKED, errors)
    if state.access:
        data["access"] = state.access
    if state.session is not None:
        attach_pipeline_provenance(data, state.session, state.access)
    else:
        data["target_source"] = plan.target_source
    return data


def _completed_watch_report(
    state: GitLabState,
    watcher: WatchPipeline,
    target: GitLabOperationTarget,
    filters: JSONObject,
) -> JSONObject:
    """Render one successful executor run through the watch result contract.

    :param state: Access-verified shared workflow state.
    :param watcher: Executed callback owning the final result mapping.
    :param target: Selected GitLab runtime target.
    :param filters: Public root and related-pipeline selectors.
    :returns: PASS or PARTIAL watch report with provider provenance.
    :raises TypeError: Callback evidence is not a JSON object or object arrays.
    """
    observed = json_object(state.results[watcher])
    records = json_objects(observed["records"])
    errors = json_objects(observed["errors"])
    complete = observed.get("complete") is True
    success = observed.get("success") is True
    data = report(PIPELINE_WATCH_KIND, target.gitlab.url, filters, records, errors)
    data.update(
        status=PASS if complete and success else PARTIAL,
        complete=complete,
        success=success,
        polls=observed["polls"],
        elapsed_seconds=observed["elapsed_seconds"],
        changes=observed["changes"],
        choices=observed["choices"],
        continuation=None,
    )
    attach_pipeline_provenance(data, state.gitlab.session, state.access)
    return data


def execute_pipeline_watch(
    args: argparse.Namespace,
    target: GitLabOperationTarget,
    target_source: str,
    reference: str,
    *,
    request_timeout: int,
    settled: Callable[[str], None],
) -> int:
    """Execute one watch through shared auth, callback and report facilities.

    :param args: Parsed watch and shared output arguments.
    :param target: Selected GitLab runtime target.
    :param target_source: Source that selected the target file.
    :param reference: Selected project path or numeric identifier.
    :param request_timeout: Positive timeout for each authentication request.
    :param settled: Sink for nonsecret one-line settled events.
    :returns: Status-derived report exit code.
    :raises ActionError: Watch arguments violate the shared mode contract.
    :raises OSError: Requested report or receipt output cannot be written.
    """  # noqa: DOC502 - Callback and output failures propagate from helpers.
    action = _watch_action(args)
    mode = _watch_mode(args, action)
    filters = _watch_filters(reference, action)
    if mode == ActionMode.OFFLINE:
        return emit_result(
            _offline_watch_report(target, target_source, reference, filters), args
        )
    plan = _watch_plan(args, action, target, target_source, reference)
    state = GitLabState(args=args)
    state.plan = plan
    watcher = WatchPipeline(action, settled=settled)
    callbacks = Callbacks(
        [
            GitLabAuth(GitLabAuthAction(target, request_timeout, args.revision)),
            watcher,
        ]
    )
    try:
        WorkflowExecutor(state, callbacks).run()
    except (ActionError, GitLabAPIError, RuntimeError, ValueError) as exc:
        data = _blocked_watch_report(state, plan, target, filters, exc)
    else:
        data = _completed_watch_report(state, watcher, target, filters)
    return emit_result(data, args)
