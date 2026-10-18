"""Poll a bounded GitLab pipeline graph and assemble watch evidence.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, MutableMapping, MutableSequence, Sequence
from dataclasses import dataclass, field

from lib.python.core.cli_api import REQUEST_DEADLINE_EXCEEDED, RequestDeadline
from lib.python.core.gitlab.gitlab_actions import ActionError
from lib.python.core.gitlab.gitlab_api import GlabAPIClient
from lib.python.core.gitlab.gitlab_pipeline_graph import _choices, _scope
from lib.python.core.gitlab.gitlab_pipeline_watch_types import (
    MAX_CHANGES,
    SETTLED_JOB_STATUSES,
    SETTLED_PIPELINE_STATUSES,
    PipelineKey,
    PipelineObservation,
    PipelineRoute,
    PipelineWatchAction,
)
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
from lib.python.core.json_types import JSONObject


def _discard_settled(_: str) -> None:
    """Accept a settled event when a library caller does not need streaming output.

    :param _: Nonsecret settled-event line.
    :returns: None after intentionally discarding the line.
    """


@dataclass
class WatchProgress:
    """Retain bounded transitions and settled events across watch polls.

    .. attribute :: previous
        :type: MutableMapping[PipelineKey, str]

        Last status observed for every pipeline identity.

    .. attribute :: changes
        :type: MutableSequence[JSONObject]

        First fifty reportable pipeline transitions.

    .. attribute :: change_count
        :type: int

        Total reportable transitions, including truncated items.

    .. attribute :: settled_pipelines
        :type: set[PipelineKey]

        Pipeline identities already written to the event sink.

    .. attribute :: settled_jobs
        :type: set[tuple[PipelineKey, int]]

        Job identities already written to the event sink.
    """

    previous: MutableMapping[PipelineKey, str] = field(default_factory=dict)
    changes: MutableSequence[JSONObject] = field(default_factory=list)
    change_count: int = 0
    settled_pipelines: set[PipelineKey] = field(default_factory=set)
    settled_jobs: set[tuple[PipelineKey, int]] = field(default_factory=set)


def _emit_settled(
    progress: WatchProgress,
    observation: PipelineObservation,
    settled: Callable[[str], None],
) -> None:
    """Emit each settled pipeline and job identity exactly once.

    :param progress: Cross-poll identities already emitted.
    :param observation: Current pipeline and job statuses.
    :param settled: Sink for bounded nonsecret event lines.
    :returns: None after updating the retained identity sets.
    """
    key = observation.route.key
    if (
        observation.status in SETTLED_PIPELINE_STATUSES
        and key not in progress.settled_pipelines
    ):
        settled(
            f"pipeline project={key.project_id} id={key.pipeline_id} "
            f"status={observation.status}"
        )
        progress.settled_pipelines.add(key)
    for job in observation.jobs:
        job_key = (key, job.id)
        if job.status in SETTLED_JOB_STATUSES and job_key not in progress.settled_jobs:
            settled(
                f"job project={key.project_id} pipeline={key.pipeline_id} "
                f"id={job.id} status={job.status}"
            )
            progress.settled_jobs.add(job_key)


def _record_transition(
    progress: WatchProgress,
    observation: PipelineObservation,
    elapsed: float,
) -> None:
    """Retain one pipeline transition within the bounded public change list.

    :param progress: Cross-poll status and transition state.
    :param observation: Current pipeline status.
    :param elapsed: Seconds since watch start.
    :returns: None after updating retained state.
    """
    key, status = observation.route.key, observation.status
    previous = progress.previous.get(key)
    changed = (previous is not None and previous != status) or (
        previous is None and status in SETTLED_PIPELINE_STATUSES
    )
    if changed:
        progress.change_count += 1
        if len(progress.changes) < MAX_CHANGES:
            progress.changes.append(
                {
                    "at_seconds": round(elapsed, 3),
                    "pipeline_id": key.pipeline_id,
                    "status": status,
                }
            )
    progress.previous[key] = status


def _capture_poll(
    progress: WatchProgress,
    observations: Sequence[PipelineObservation],
    elapsed: float,
    settled: Callable[[str], None],
) -> None:
    """Record settlement and status changes from one provider poll.

    :param progress: Cross-poll state to update.
    :param observations: Current bounded pipeline graph.
    :param elapsed: Seconds since watch start.
    :param settled: Sink for bounded nonsecret event lines.
    :returns: None after processing each observation once.
    """
    for observation in observations:
        _emit_settled(progress, observation, settled)
        _record_transition(progress, observation, elapsed)


def _complete(
    observations: Sequence[PipelineObservation], errors: Sequence[JSONObject]
) -> bool:
    """Decide whether the observed graph is complete and settled.

    :param observations: Final bounded graph observations.
    :param errors: Collection errors, including optional output-cap warnings.
    :returns: Whether every pipeline settled and no evidence error remains.
    """
    warnings = {"changes_limit_exceeded", "choices_limit_exceeded"}
    evidence_errors = [item for item in errors if item.get("reason") not in warnings]
    return (
        bool(observations)
        and not evidence_errors
        and all(item.status in SETTLED_PIPELINE_STATUSES for item in observations)
    )


def _watch_result(
    progress: WatchProgress,
    observations: Sequence[PipelineObservation],
    errors: Sequence[JSONObject],
    polls: int,
    elapsed: float,
) -> JSONObject:
    """Assemble final bounded watch evidence after polling stops.

    :param progress: Retained transitions and emitted identities.
    :param observations: Final bounded pipeline graph.
    :param errors: Final graph collection errors.
    :param polls: Number of completed graph reads.
    :param elapsed: Total watch duration in seconds.
    :returns: Completion, success, records, changes, choices and errors.
    """
    retained_errors = list(errors)
    if progress.change_count > MAX_CHANGES:
        retained_errors.append(
            {
                "source": "changes",
                "reason": "changes_limit_exceeded",
                "count": progress.change_count,
            }
        )
    choices, choice_errors = _choices(observations)
    retained_errors.extend(choice_errors)
    complete = _complete(observations, retained_errors)
    return {
        "complete": complete,
        "success": complete and all(item.status == "success" for item in observations),
        "polls": polls,
        "elapsed_seconds": round(max(0.0, elapsed), 3),
        "records": [item.record() for item in observations],
        "changes": list(progress.changes),
        "choices": list(choices),
        "continuation": None,
        "errors": retained_errors,
    }


def watch_pipeline(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    project_id: int,
    action: PipelineWatchAction,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    settled: Callable[[str], None] = _discard_settled,
) -> JSONObject:
    """Poll one pipeline graph until settled or the caller's deadline expires.

    :param api: Access-verified GitLab transport.
    :param session: Exact-host credential session.
    :param project_id: Access-verified root project ID.
    :param action: Root pipeline, related selector, and bounded wait controls.
    :param monotonic: Injectable monotonic clock used by deterministic tests.
    :param sleep: Injectable bounded sleep used by deterministic tests.
    :param settled: Sink for one bounded line when a pipeline or job settles.
    :returns: Completion, success, poll, changes, choices, records, and errors.
    :raises ActionError: Inputs, regex, or provider evidence are invalid.
    :raises GitLabAPIError: The root pipeline cannot be read.
    """  # noqa: DOC503 - root provider failures remain blocking.
    if project_id <= 0 or action.pipeline_id <= 0:
        raise ActionError("project_and_pipeline_ids_must_be_positive")
    if action.interval <= 0 or action.timeout <= 0:
        raise ActionError("pipeline_watch_interval_and_timeout_must_be_positive")
    try:
        if action.related_name is not None:
            re.compile(action.related_name)
    except re.error as exc:
        raise ActionError("pipeline_related_name_invalid_regex") from exc
    started = monotonic()
    deadline = RequestDeadline(started + action.timeout, monotonic)
    progress = WatchProgress()
    polls = 0
    final: Sequence[PipelineObservation] = ()
    final_errors: Sequence[JSONObject] = ()
    elapsed = 0.0
    while True:
        polls += 1
        observed, errors = _scope(
            api,
            session,
            PipelineRoute(PipelineKey(project_id, action.pipeline_id), "root", 0),
            action.related_name,
            deadline=deadline,
        )
        expired = any(
            item.get("reason") == REQUEST_DEADLINE_EXCEEDED for item in errors
        )
        if observed or not expired:
            final = observed
        final_errors = errors
        now = monotonic()
        elapsed = max(0.0, now - started)
        _capture_poll(progress, final, elapsed, settled)
        if _complete(final, final_errors) or now >= deadline.expires_at:
            break
        sleep(min(action.interval, max(0.0, deadline.expires_at - now)))
    return _watch_result(progress, final, final_errors, polls, elapsed)
