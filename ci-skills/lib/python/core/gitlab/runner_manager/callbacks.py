"""GitLab authentication, registration, and exact runner read-back callbacks.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import time
from collections.abc import MutableMapping
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Final, Optional

from lib.python.access.gitlab import check_gitlab_operation_access
from lib.python.access.gitlab_credentials import bind_gitlab_session
from lib.python.core.abstract.callback import Callback
from lib.python.core.abstract.skill_action import ActionT_co
from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.delivery_state import ProviderAuthAction
from lib.python.core.gitlab.gitlab_actions import ActionError
from lib.python.core.gitlab.gitlab_api import GlabAPIClient, runner_update_guard
from lib.python.core.gitlab.gitlab_runners import read_runner_record
from lib.python.core.gitlab.gitlab_session import GitLabService
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    copy_json,
    json_integer,
    json_object,
)
from lib.python.core.kubernetes.resources import ResourceSet
from lib.python.core.kubernetes.run_scope import resource_identity
from lib.python.core.kubernetes.runner import REGISTRY_SECRET_SUFFIX
from lib.python.core.kubernetes.scc_access import role_binding_document
from lib.python.core.paths import SKILL_ROOT
from lib.python.core.runner import (
    REGISTRATION_SECRET_SUFFIX,
    RUNTIME_ROOT,
    RunnerRuntime,
)
from lib.python.core.target import (
    GitLabOperationTarget,
    TargetError,
    select_gitlab_reference,
)
from lib.python.sys.status import PASS

from .action import (
    GitLabRunnerManagerAction,
    GitLabRunnerManagerState,
    runner_evidence,
)
from .token import BoundRunnerToken, read_runner_token

GITLAB_INIT_SCRIPT: Final = SKILL_ROOT / "lib/bash/runner/gitlab-init.bash"
REGISTRATION_KEY: Final = "gitlab-runner-token"
RUNNER_CONFIG: Final = f"{RUNTIME_ROOT}/config.toml"
GITLAB_READINESS_COMMAND: Final = (
    "/bin/sh",
    "-c",
    f"gitlab-runner verify --config {RUNNER_CONFIG}",
)
RUNNER_STATUS_POLL_SECONDS: Final = 2


@dataclass(frozen=True)
class GitLabManagerAuthAction(ProviderAuthAction):
    """Select one project or group for runner-manager authentication.

    :param target: Runtime target resolved by the parent workflow.
    :param timeout: Positive maximum seconds for provider requests.
    :param operation_target: GitLab-only view of the selected target.
    :param target_kind: Exact ``project`` or ``group`` selector.
    :param target_reference: Project or group path or numeric ID.
    :param target_source: Reportable source for the selected target.
    :param revision: Optional installed source revision override.

    .. attribute :: operation_target
        :type: GitLabOperationTarget

        GitLab-only view of the selected runtime target.

    .. attribute :: target_kind
        :type: str

        Project or group selector used for authentication.

    .. attribute :: target_reference
        :type: str

        Selected project or group path or numeric ID.

    .. attribute :: target_source
        :type: str

        Reportable origin of the selected target.

    .. attribute :: revision
        :type: Optional[str]

        Explicit source revision override, when supplied.
    """

    operation_target: GitLabOperationTarget
    target_kind: str
    target_reference: str
    target_source: str
    revision: Optional[str]


class GitLabRunnerManagerCallback(Callback[GitLabRunnerManagerState, ActionT_co]):
    """Share the GitLab service retained by manager authentication."""

    @property
    def plugin_type(self) -> SkillPluginType:
        """Identify GitLab as this callback's configuration owner.

        :returns: GitLab plugin identity.
        """
        return SkillPluginType.GITLAB

    @property
    def manager_state(self) -> GitLabRunnerManagerState:
        """Read the manager's concrete shared state.

        :returns: State containing the bound GitLab service and run scope.
        :raises TypeError: The callback was composed with another state type.
        """
        if not isinstance(self.state, GitLabRunnerManagerState):
            raise TypeError("gitlab_runner_manager_state_required")
        return self.state

    def read_config(self) -> JSONObject:
        """Reuse a caller-bound GitLab service for direct composition.

        :returns: Empty setup evidence after retaining the existing session.
        :raises TargetError: A direct caller supplied no GitLab service.
        """
        if not hasattr(self.manager_state, "gitlab"):
            raise TargetError("gitlab_configuration_missing")
        self.manager_state.configured_session = self.manager_state.gitlab.session
        return {}

    def is_configured(self) -> bool:
        """Check that configuration retained one private session.

        :returns: Whether GitLab credential selection completed.
        """
        return hasattr(self.manager_state, "configured_session")

    @property
    def gitlab(self) -> GitLabService:
        """Read the service established by authentication.

        :returns: Bound client, session, target ID, and access evidence.
        :raises AttributeError: Authentication has not assigned the service.
        """  # noqa: DOC502 - Ordered composition owns assignment.
        return self.manager_state.gitlab


class GitLabManagerAuth(GitLabRunnerManagerCallback[GitLabManagerAuthAction]):
    """Bind and independently verify one GitLab runner target."""

    def __init__(self, workflow: GitLabRunnerManagerAction):
        """Resolve the target selector without reading credentials.

        :param workflow: Prepared runner-manager inputs and target.
        :raises TargetError: Target source or project/group selection is invalid.
        """
        target = workflow.target
        if target.gitlab is None or target.source_file is None:
            raise TargetError("gitlab_target_unresolved")
        operation_target = GitLabOperationTarget(target.gitlab, target.source_file)
        target_kind, reference = select_gitlab_reference(
            operation_target,
            project=getattr(workflow.args, "project", None),
            group=getattr(workflow.args, "group", None),
        )
        source = target.source_kind or "target"
        if getattr(workflow.args, target_kind, None):
            source = f"{source};argv:--{target_kind}"
        super().__init__(
            GitLabManagerAuthAction(
                target,
                workflow.args.timeout,
                operation_target,
                target_kind,
                reference,
                source,
                workflow.args.revision,
            )
        )

    @property
    def auth_action(self) -> GitLabManagerAuthAction:
        """Read the concrete authentication selection.

        :returns: Concrete GitLab authentication action.
        :raises TypeError: A direct caller supplied another action type.
        """
        if not isinstance(self.action, GitLabManagerAuthAction):
            raise TypeError("gitlab_manager_auth_action_required")
        return self.action

    def read_config(self) -> JSONObject:
        """Resolve one private GitLab session during configuration binding.

        :returns: Empty evidence; credentials stay in shared state.
        :raises TargetError: Target or credential selection is invalid.
        """  # noqa: DOC502 - Shared credential binding owns validation.
        action = self.auth_action
        self.manager_state.configured_session = bind_gitlab_session(
            action.operation_target,
            target_kind=action.target_kind,
            target_reference=action.target_reference,
            target_source=action.target_source,
            revision=action.revision,
        )
        return {}

    def run(self) -> JSONObject:
        """Verify identity and scope, then retain the numeric target.

        :returns: Existing safe GitLab access receipt.
        :raises ActionError: Access is blocked or lacks a numeric target ID.
        """
        session = self.manager_state.configured_session
        api = GlabAPIClient()
        access = check_gitlab_operation_access(session, api_client=api)
        if access.get("status") != PASS:
            raise ActionError("gitlab_access_blocked")
        target_id = json_integer(json_object(access["target"])["id"])
        self.manager_state.gitlab = GitLabService(api, session, target_id, access)
        self.state.access_evidence[SkillPluginType.GITLAB.value] = access
        return access


class GitLabManagerComponent(GitLabRunnerManagerCallback[GitLabRunnerManagerAction]):
    """Share typed manager inputs without mixing them with authentication inputs."""

    def __init__(self, workflow: GitLabRunnerManagerAction):
        """Retain prepared runner identity and scope selection.

        :param workflow: Prepared runner-manager operation.
        """
        super().__init__(workflow)

    @property
    def workflow(self) -> GitLabRunnerManagerAction:
        """Read the concrete runner-manager action.

        :returns: Prepared runner-manager action.
        :raises TypeError: A direct caller supplied another action type.
        """
        if not isinstance(self.action, GitLabRunnerManagerAction):
            raise TypeError("gitlab_runner_manager_action_required")
        return self.action


class GuardGitLabRunnerManager(GitLabManagerComponent):
    """Hold the shared runner lock across lookup, mutation, and reverse cleanup."""

    def __init__(self, workflow: GitLabRunnerManagerAction):
        """Retain manager inputs and an initially inactive resource guard.

        :param workflow: Prepared operation whose runner identity is serialized.
        """
        super().__init__(workflow)
        self._guard: ExitStack = ExitStack()

    def run(self) -> JSONObject:
        """Acquire the existing same-host runner guard before scope observations.

        :returns: Empty evidence; the lock changes no provider resource.
        :raises GitLabAPIError: The shared runner lock cannot be acquired safely.
        :raises OSError: Opening or locking the shared runner descriptor fails.
        """  # noqa: DOC502 - Shared context management owns lock failures.
        self._guard.enter_context(
            runner_update_guard(self.gitlab.session.origin, self.workflow.runner_id)
        )
        return {}

    def cleanup(self) -> JSONObject:
        """Release the guard after all later callbacks have finished cleanup.

        :returns: Empty evidence after releasing any acquired lock.
        :raises OSError: Releasing the shared runner descriptor fails.
        """  # noqa: DOC502 - Closing the shared guard releases its descriptor.
        self._guard.close()
        return {}


class ObserveGitLabRunner(GitLabManagerComponent):
    """Bind exact runner and manager ownership into the live plan."""

    def _require_owned_online_manager(self) -> None:
        """Require one exact manager in the resolved run scope.

        :returns: None after verifying resource ownership.
        :raises ActionError: Scope or manager ownership is absent or ambiguous.
        """
        scopes = tuple(self.manager_state.found_scopes)
        if len(scopes) != 1:
            raise ActionError("runner_online_outside_selected_manager")
        scope = scopes[0]
        runner = self.workflow.workload().runner(scope)
        current = self.state.kubernetes.read(
            "deployment", runner.name, runner.namespace
        )
        resources = ResourceSet(self.state.kubernetes, runner.namespace, scope.run_id)
        if current is None or not resources.owns(current):
            raise ActionError("runner_online_outside_selected_manager")

    def run(self) -> JSONObject:
        """Read exact scope, runner identity, and tags before mutation.

        Deploy rejects an already-online runner outside the selected manager.
        Delete relies on the selected run scope and shared resource ownership;
        GitLab can retain cached online status after the workload is absent.

        :returns: Verified public runner observation.
        :raises ActionError: Runner identity, scope, tags, or ownership differs.
        :raises GitLabAPIError: Exact runner or membership reads fail.
        :raises KubernetesError: Online manager ownership cannot be read.
        """  # noqa: DOC502,DOC503 - Provider readers propagate typed failures.
        workflow, service = self.workflow, self.gitlab
        expected_tags = workflow.labels or None
        record = read_runner_record(
            service.api,
            service.session,
            service.session.target_kind,
            service.target_id,
            workflow.runner_id,
            description=workflow.description,
            expected_tags=expected_tags,
        )
        if record["online"] is True and workflow.args.action == "deploy":
            self._require_owned_online_manager()
        self.manager_state.gitlab_runner = record
        scope, _ = workflow.scope_plan()
        return {
            "verified": True,
            "run_id": workflow.scope_id,
            "scope": scope,
            **runner_evidence(record),
        }


class GitLabRunnerRuntime(GitLabManagerComponent):
    """Prepare shell-executor bootstrap for the shared runner Deployment."""

    def run(self) -> JSONObject:
        """Recheck token identity and retain private provider runtime.

        :returns: Empty evidence; runtime credentials remain private.
        :raises TypeError: Deploy did not receive a bound token.
        :raises ValueError: The token file changed after planning.
        :raises TargetError: The token file is unavailable or invalid.
        :raises OSError: The installed bootstrap script cannot be read.
        :raises UnicodeError: The installed bootstrap script is not UTF-8 text.
        """  # noqa: DOC503 - Native readers propagate declared errors.
        workflow = self.workflow
        selection = workflow.runner_token
        if not isinstance(selection, BoundRunnerToken):
            raise TypeError("bound_runner_token_required")
        current = read_runner_token(selection.path)
        if current.digest != selection.digest:
            raise ValueError("runner_token_changed_rerun_plan")
        scope = self.manager_state.run_scope
        runner = workflow.workload().runner(scope)
        values = (
            {"name": "RUNNER_RUNTIME_ROOT", "value": RUNTIME_ROOT},
            {"name": "RUNNER_URL", "value": self.gitlab.session.origin},
            {"name": "RUNNER_NAME", "value": runner.name},
        )
        secret: JSONObject = {
            "name": "RUNNER_TOKEN",
            "valueFrom": {
                "secretKeyRef": {
                    "name": runner.name + REGISTRATION_SECRET_SUFFIX,
                    "key": REGISTRATION_KEY,
                }
            },
        }
        self.state.runner_runtime = RunnerRuntime(
            init_command=(
                "/bin/bash",
                "-c",
                GITLAB_INIT_SCRIPT.read_text(encoding="utf-8"),
            ),
            command=(
                "gitlab-runner",
                "run",
                "--working-directory",
                RUNTIME_ROOT,
                "--config",
                RUNNER_CONFIG,
            ),
            environment=(*values, secret),
            readiness_command=GITLAB_READINESS_COMMAND,
            credential={REGISTRATION_KEY: current.value},
        )
        return {}


def retain_manager_resources(
    state: GitLabRunnerManagerState, workflow: GitLabRunnerManagerAction
) -> None:
    """Retain resources needed by one successfully delivered manager.

    :param state: Shared manager state containing the exact run scope.
    :param workflow: Deploy action whose owned resources must remain available.
    :returns: None after recording retained resource identities.
    """
    scope = state.run_scope
    runner = workflow.workload().runner(scope)
    retained = state.retained_resources
    if scope.owns_namespace:
        retained.add(resource_identity("Namespace", scope.namespace, ""))
    retained.update(
        {
            resource_identity("Deployment", runner.name, scope.namespace),
            resource_identity(
                "Secret",
                runner.name + REGISTRATION_SECRET_SUFFIX,
                scope.namespace,
            ),
            resource_identity(
                "Secret",
                runner.name + REGISTRY_SECRET_SUFFIX,
                scope.namespace,
            ),
        }
    )
    binding = role_binding_document(workflow.scc_access(), scope)
    metadata = json_object(binding["metadata"])
    name = metadata.get("name")
    if isinstance(name, str):
        observed = state.kubernetes.read("RoleBinding", name, scope.namespace)
        if observed is not None and scope.resources(state.kubernetes).owns(observed):
            retained.add(resource_identity("RoleBinding", name, scope.namespace))


class VerifyGitLabRunnerOnline(GitLabManagerComponent):
    """Poll the exact runner until GitLab confirms online contact."""

    def run(self) -> JSONObject:
        """Require online and contact evidence after owned workload rollout.

        :returns: Verified runner, run scope, and owned workload evidence.
        :raises ActionError: GitLab does not report online contact before timeout.
        :raises GitLabAPIError: Exact runner or membership reads fail.
        """  # noqa: DOC503 - Bounded provider reads propagate typed failures.
        workflow, service = self.workflow, self.gitlab
        deadline = time.monotonic() + workflow.args.timeout
        expected_tags = workflow.labels or None
        while True:
            record = read_runner_record(
                service.api,
                service.session,
                service.session.target_kind,
                service.target_id,
                workflow.runner_id,
                description=workflow.description,
                expected_tags=expected_tags,
            )
            ready = (
                record["online"] is True
                and record["status"] == "online"
                and bool(record["contacted_at"])
            )
            if ready:
                evidence: MutableMapping[str, JSONValue] = {
                    "verified": True,
                    "run_id": self.manager_state.run_scope.run_id,
                    **runner_evidence(record),
                }
                retain_manager_resources(self.manager_state, workflow)
                evidence["workload"] = copy_json(self.state.deployment_result)
                return dict(evidence)
            if time.monotonic() >= deadline:
                raise ActionError("runner_online_readback_timeout")
            time.sleep(RUNNER_STATUS_POLL_SECONDS)


class VerifyGitLabRunnerPreserved(GitLabManagerComponent):
    """Prove owned workload deletion preserved the provider runner record."""

    def run(self) -> JSONObject:
        """Read the exact runner after shared callbacks delete owned resources.

        GitLab can retain a runner's aggregate online status after a manager stops.
        The current status is evidence only; provider-record deletion remains the
        separate ``gitlab_runner.py delete`` operation required by block 7.

        :returns: Preserved runner identity and current provider status.
        :raises ActionError: Runner identity, scope, or tags changed.
        :raises GitLabAPIError: Exact runner or membership reads fail.
        """  # noqa: DOC502,DOC503 - The shared reader propagates typed failures.
        workflow, service = self.workflow, self.gitlab
        record = read_runner_record(
            service.api,
            service.session,
            service.session.target_kind,
            service.target_id,
            workflow.runner_id,
            description=workflow.description,
            expected_tags=workflow.labels or None,
        )
        self.manager_state.gitlab_runner = record
        return {
            "verified": True,
            "run_id": self.manager_state.run_scope.run_id,
            "runner_preserved": True,
            "current_status": runner_evidence(record),
        }
