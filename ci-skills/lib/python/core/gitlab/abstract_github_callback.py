"""GitLab operations bound to the generic callback lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from lib.python.core.abstract.callback import Callback
from lib.python.core.abstract.callbacks import Callbacks
from lib.python.core.abstract.context import ExecutionContext
from lib.python.core.abstract.skill_action import ActionT_co
from lib.python.core.abstract.skill_exector import WorkflowExecutor
from lib.python.core.abstract.skill_result import SkillResult
from lib.python.core.gitlab.gitlab_api import GlabAPIClient
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession, GitLabService


class GitlabCallback(Callback[ExecutionContext, ActionT_co]):
    """Use the GitLab service established in this workflow's shared state."""

    @property
    def gitlab(self) -> GitLabService:
        """Read the authenticated provider service.

        :returns: GitLab client, session, and verified target.
        """
        return cast(GitLabService, self.state.services["gitlab"])

    @property
    def complete(self) -> bool:
        """Stop dependent actions after incomplete read-back.

        :returns: Whether this operation's result permits continuation.
        """
        cleanup = cast(Mapping[str, object], self.result.get("cleanup", {}))
        return (self.result.get("verified", True) is True
                and not self.result.get("errors")
                and cleanup.get("status") not in {"BLOCKED", "PARTIAL"})

    def execute(self, api: GlabAPIClient, session: BoundGitLabSession, target_id: int) -> SkillResult:
        """Run a direct provider operation through the same shared lifecycle.

        :param api: Existing GitLab API adapter.
        :param session: Authenticated session supplied by the caller.
        :param target_id: Verified project or group identifier.
        :returns: This callback's state-owned operation result.
        """
        context = ExecutionContext(services={"gitlab": GitLabService(api, session, target_id)})
        WorkflowExecutor(context=context, callbacks=Callbacks(self)).run()
        return self.result
