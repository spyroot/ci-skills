"""GitLab operations bound to the generic callback lifecycle.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from lib.python.core.abstract.callback import Callback
from lib.python.core.abstract.context import ExecutionContext
from lib.python.core.abstract.skill_action import ActionT_co
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession, GitLabService
from lib.python.core.json_types import JSONObject

if TYPE_CHECKING:
    from lib.python.core.gitlab.gitlab_actions import ActionPlan
    from lib.python.core.gitlab.gitlab_runners import RunnerReadResult


@dataclass
class GitLabState(ExecutionContext):
    """Own one plan, private provider service, and observed operation evidence.

    Inherited arguments, result history, cleanup failures, and report fields are
    documented by ExecutionContext and SkillState. Planning sets plan before any
    callback runs; successful authentication sets gitlab before an operation.

    :param session: Bound session, absent until credential selection succeeds.
    :param access: Access evidence, empty before authentication.
    :param phase: Current planning, access, read, or apply boundary.
    :param mutated: Known write outcome; None means an attempted write is uncertain.
    :param failure_source: Boundary used to classify the next failure.
    :param started: Monotonic invocation start for elapsed diagnostics.
    :param operation_result: Last operation mapping, initially empty.
    :param operation_completed: Whether the operation reached its update hook.

    .. attribute :: plan
        :type: ActionPlan

        Prepared operation, replaced when live preflight adds a snapshot.

    .. attribute :: gitlab
        :type: GitLabService

        Private authenticated service established before provider operations.

    .. attribute :: session
        :type: Optional[BoundGitLabSession]

        Selected session retained even when access verification fails.

    .. attribute :: access
        :type: JSONObject

        Access receipt retained for success and failure reports.

    .. attribute :: phase
        :type: str

        Most recently entered planning, access, read, or apply boundary.

    .. attribute :: mutated
        :type: Optional[bool]

        False before writes, observed outcome afterward, or None when uncertain.

    .. attribute :: failure_source
        :type: str

        Report source for the current operation boundary.

    .. attribute :: started
        :type: float

        Monotonic start used by failure diagnostics.

    .. attribute :: operation_result
        :type: JSONObject

        Raw operation evidence retained before completion checks and cleanup.

    .. attribute :: operation_completed
        :type: bool

        Whether operation_result was produced, including a valid empty mapping.

    .. attribute :: runner_read
        :type: RunnerReadResult

        Typed runner observation retained by ReadRunner for direct library callers.
    """

    plan: ActionPlan = field(init=False, repr=False)
    gitlab: GitLabService = field(init=False, repr=False)
    session: Optional[BoundGitLabSession] = field(default=None, repr=False)
    access: JSONObject = field(default_factory=dict)
    phase: str = "OFFLINE_PLAN"
    mutated: Optional[bool] = False
    failure_source: str = "arguments"
    started: float = field(default_factory=time.monotonic)
    operation_result: JSONObject = field(default_factory=dict)
    operation_completed: bool = False
    runner_read: RunnerReadResult = field(init=False, repr=False)


class GitlabCallback(Callback[GitLabState, ActionT_co]):
    """Use the typed GitLab service established in shared invocation state."""

    @property
    def gitlab(self) -> GitLabService:
        """Read the provider service established by authentication.

        :returns: GitLab client, bound session, and verified numeric target.
        :raises AttributeError: Authentication has not populated the shared service.
        """  # noqa: DOC502 - reading the uninitialized state field raises AttributeError.
        return self.state.gitlab

    @property
    def complete(self) -> bool:
        """Stop dependent actions after incomplete operation read-back.

        :returns: Whether verification, errors, and cleanup permit continuation.
        """
        cleanup = self.result.get("cleanup", {})
        return (
            self.result.get("verified", True) is True
            and not self.result.get("errors")
            and isinstance(cleanup, Mapping)
            and cleanup.get("status") not in {"BLOCKED", "PARTIAL"}
        )
