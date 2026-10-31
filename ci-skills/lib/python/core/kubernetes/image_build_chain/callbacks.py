"""Late-bound component adapters for the CI13 image-build chain.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import tempfile
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Final

from lib.python.core.abstract.callback import DeferredCallback, LocalCallback
from lib.python.core.gitlab.gitlab_action import GitLabAuth, GitLabAuthAction
from lib.python.core.gitlab.gitlab_actions import ActionPlan, make_plan
from lib.python.core.gitlab.gitlab_runners import CreateRunner
from lib.python.core.gitlab.runner_manager import (
    DeployManagerRunner,
    GitLabRunnerManagerAction,
    retain_manager_resources,
)
from lib.python.core.gitlab.runner_manager.action import MANAGER_ROLE
from lib.python.core.gitlab.runner_manager.workload import resolve_manager_name
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    copy_json,
    json_object,
    json_objects,
)
from lib.python.core.kubernetes.kubernetes import DEFAULT_COMMAND_TIMEOUT
from lib.python.core.kubernetes.podman_probe import ProbeMode, select_ready_worker
from lib.python.core.kubernetes.run_scope import RunScope
from lib.python.core.kubernetes.runner_scope import observe_namespace
from lib.python.core.report import plan_digest
from lib.python.core.runner_identity import NameSource, ResolvedName
from lib.python.core.runner_scope import ScopePurpose, ScopeSource

from .model import RUNNER_TOKEN_NAME, ImageBuildChainAction, ImageBuildChainState

CHAIN_MANAGER_CHANGED: Final = "chain_manager_identity_changed_rerun_plan"


class ProviderTransition(StrEnum):
    """Closed provider-service transitions used by the shared chain state.

    .. attribute :: RETAIN_TOOLBOX_HARBOR

        Retain the authenticated toolbox Harbor service.

    .. attribute :: USE_TOOLBOX_HARBOR

        Restore the retained toolbox Harbor service.

    .. attribute :: RETAIN_RUNNER_GITLAB

        Retain the authenticated runner-scope GitLab service.

    .. attribute :: USE_RUNNER_GITLAB

        Restore the retained runner-scope GitLab service.
    """

    RETAIN_TOOLBOX_HARBOR = "retain_toolbox_harbor"
    USE_TOOLBOX_HARBOR = "use_toolbox_harbor"
    RETAIN_RUNNER_GITLAB = "retain_runner_gitlab"
    USE_RUNNER_GITLAB = "use_runner_gitlab"


class DeferredComponent(DeferredCallback[ImageBuildChainState, ImageBuildChainAction]):
    """Keep chain-specific retention atop the shared deferred lifecycle."""

    def update(self) -> Mapping[str, object]:
        """Retain manager resources after the shared child update succeeds.

        :returns: Exact child post-run evidence.
        :raises ValueError: Collection did not construct a callback.
        """  # noqa: DOC502 - Shared deferred lifecycle validates collection order.
        result = super().update()
        if isinstance(self.child, DeployManagerRunner):
            retain_manager_resources(self.state, self.state.manager_action)
        return result


class ScopedGitLabAuth(DeferredComponent):
    """Authenticate one retained GitLab plan without cross-scope state drift."""

    def __init__(self, action: ImageBuildChainAction, plan: ActionPlan):
        """Retain the exact project or group plan until callback collection.

        :param action: Parent chain target and timeout.
        :param plan: Runner scope selected for access verification.
        """
        self.plan = plan
        super().__init__(action, self._factory)

    def _factory(self, state: ImageBuildChainState) -> GitLabAuth:
        """Install this scope's plan immediately before existing authentication.

        :param state: Shared chain state used by the existing callback.
        :returns: Existing GitLab authentication callback.
        """
        state.plan = self.plan
        return GitLabAuth(
            GitLabAuthAction(
                self.action.gitlab_target,
                min(self.action.args.timeout, DEFAULT_COMMAND_TIMEOUT),
                self.action.args.revision,
            )
        )


class RetainProvider(LocalCallback[ImageBuildChainState, ImageBuildChainAction]):
    """Retain or restore one provider service inside the shared state."""

    def __init__(self, action: ImageBuildChainAction, role: ProviderTransition):
        """Select one known provider role.

        :param action: Parent chain inputs.
        :param role: Closed service transition owned by this callback.
        :raises TypeError: The transition is not a ``ProviderTransition``.
        """
        super().__init__(action)
        if not isinstance(role, ProviderTransition):
            raise TypeError("provider_transition_required")
        self.role = role

    def run(self) -> JSONObject:
        """Retain the active service or restore a previously retained service.

        :returns: Verified role transition without credential material.
        :raises ValueError: The role is unknown or its producer did not run.
        """
        state = self.state
        if self.role == ProviderTransition.RETAIN_TOOLBOX_HARBOR:
            state.toolbox_harbor = state.harbor
        elif self.role == ProviderTransition.USE_TOOLBOX_HARBOR:
            state.harbor = state.toolbox_harbor
        elif self.role == ProviderTransition.RETAIN_RUNNER_GITLAB:
            state.runner_gitlab = state.gitlab
            state.access_evidence["gitlab_runner"] = copy_json(state.access)
        elif self.role == ProviderTransition.USE_RUNNER_GITLAB:
            state.gitlab = state.runner_gitlab
            state.configured_session = state.runner_gitlab.session
        else:
            raise ValueError("chain_provider_role_invalid")
        return {"verified": True, "role": self.role.value}


class ResolveChainPlan(LocalCallback[ImageBuildChainState, ImageBuildChainAction]):
    """Bind stable live inputs and enforce the outer chain confirmation."""

    def run(self) -> JSONObject:
        """Create the live plan and compare the caller's confirmation on apply.

        :returns: Stable plan and digest.
        :raises ValueError: Live producers are missing or confirmation differs.
        """
        state, action = self.state, self.action
        if not action.repository_identity.name and action.runner_kind == "project":
            action.bind_repository(state.gitlab.access)
        if action.manager_scope is None:
            raise ValueError("repository_identity_required: provide --namespace")
        nodes = state.kubernetes.json("get", "nodes", "-o", "json")
        selected = select_ready_worker(nodes, action.args.node)
        state.selected_node = selected.name
        scopes = RunScope.find_all(state.kubernetes, action.manager_scope.run_id)
        if len(scopes) > 1:
            raise ValueError("run_scope_namespace_ambiguous")
        if scopes:
            observed = scopes[0]
            if action.args.namespace and action.args.namespace != observed.namespace:
                raise ValueError("run_scope_namespace_mismatch")
            action.manager_scope = observed
        preferred = ResolvedName(
            action.manager_scope.name(
                MANAGER_ROLE, prefix=action.repository_identity.name
            ),
            NameSource.REPOSITORY
            if action.repository_identity.name
            else NameSource.UNKNOWN,
        )
        action.resolved_runner_name = resolve_manager_name(
            state.kubernetes, action.manager_scope, preferred
        )
        state.runner_scopes.clear()
        observe_namespace(
            state,
            state.kubernetes,
            namespace=action.manager_scope.namespace,
            purpose=ScopePurpose.MANAGER,
            source=ScopeSource.EXPLICIT
            if action.args.namespace
            else ScopeSource.EXISTING_RUN_SCOPE
            if scopes
            else ScopeSource.OPERATION_CREATED,
            operation=action.manager_scope.run_id,
            required=not action.manager_scope.owns_namespace,
        )
        if action.toolbox.spec is not None:
            observe_namespace(
                state,
                state.kubernetes,
                namespace=action.toolbox.spec.namespace,
                purpose=ScopePurpose.BUILD,
                source=ScopeSource.TOOLBOX_DEFAULT,
                operation=action.toolbox.runner.operation,
            )
        document = action.plan(state.selected_node)
        digest = plan_digest(document)
        state.chain_plan = document
        state.chain_plan_digest = digest
        if action.mode == ProbeMode.APPLY and action.args.confirm_plan != digest:
            raise ValueError("confirm_plan_mismatch_rerun_live_plan")
        return {
            "verified": True,
            "plan_digest": digest,
            "selected_node": selected.name,
            "selection": "requested" if action.args.node is not None else "discovered",
            "node": selected.evidence(),
        }


class ConfirmChainManager(LocalCallback[ImageBuildChainState, ImageBuildChainAction]):
    """Recheck the confirmed manager identity after build and provider creation."""

    def run(self) -> JSONObject:
        """Reject a changed namespace UID, ownership, or workload name before deployment.

        :returns: Verified stable manager selection.
        :raises ValueError: Current selection differs from the confirmed outer plan.
        """
        state = self.state
        planned = self.action.plan(state.selected_node)
        expected = state.chain_plan
        planned_runner = json_object(planned["runner"])
        expected_runner = json_object(expected["runner"])
        current_scopes = json_objects(planned_runner["scope_observations"])
        expected_scopes = json_objects(expected_runner["scope_observations"])
        current_manager_scopes = [
            item for item in current_scopes if item["purpose"] == ScopePurpose.MANAGER
        ]
        expected_manager_scopes = [
            item for item in expected_scopes if item["purpose"] == ScopePurpose.MANAGER
        ]
        manager = state.manager_action
        scope = state.run_scope
        if (
            current_manager_scopes != expected_manager_scopes
            or manager.workload().runner(scope).name != expected_runner["name"]
            or scope.namespace != self.action.manager_scope.namespace
            or scope.owns_namespace != self.action.manager_scope.owns_namespace
        ):
            raise ValueError(CHAIN_MANAGER_CHANGED)
        return {"verified": True, "namespace": scope.namespace}


class CreateRunnerRecord(DeferredComponent):
    """Create the run-owned runner record and private ephemeral token sink."""

    def __init__(self, action: ImageBuildChainAction):
        """Retain chain inputs until the confirmed run scope exists.

        :param action: Parent chain inputs.
        """
        super().__init__(action, self._factory)

    def _factory(self, state: ImageBuildChainState) -> CreateRunner:
        """Construct the existing runner-create callback after confirmation.

        :param state: Shared state containing the generated run identifier.
        :returns: Existing runner creation callback.
        """
        directory = Path(tempfile.mkdtemp(prefix="ci-skills-ci13-"))
        state.token_directory = directory
        state.token_path = directory / RUNNER_TOKEN_NAME
        args = _runner_args(self.action, state.token_path)
        plan = make_plan(
            "gitlab_runner",
            args,
            self.action.gitlab_target,
            self.action.target_source,
        )
        state.runner_create_plan = plan
        state.plan = plan
        return CreateRunner(action=plan)

    def update(self) -> Mapping[str, JSONValue]:
        """Retain the verified runner ID for manager deployment and cleanup.

        :returns: Child update evidence.
        :raises ValueError: Creation did not return one verified positive ID.
        """
        result = self.result
        identifier = result.get("id")
        if type(identifier) is int and identifier > 0:
            self.state.created_runner_id = identifier
        evidence = super().update()
        if result.get("verified") is not True or not hasattr(
            self.state, "created_runner_id"
        ):
            raise ValueError("runner_creation_not_verified")
        return evidence


class PrepareRunnerManager(LocalCallback[ImageBuildChainState, ImageBuildChainAction]):
    """Prepare the existing manager action from linked chain outputs."""

    def run(self) -> JSONObject:
        """Construct manager inputs using the selected node and toolbox digest.

        :returns: Verified runner, node, and image identity.
        :raises ValueError: A required upstream identity is missing.
        :raises TargetError: Existing manager preparation cannot bind its target.
        """  # noqa: DOC503 - manager preparation propagates existing errors.
        state = self.state
        if not all(
            hasattr(state, name)
            for name in ("created_runner_id", "published_image", "selected_node")
        ):
            raise ValueError("manager_upstream_identity_missing")
        args = _manager_args(self.action, state)
        manager = GitLabRunnerManagerAction(args, context=state)
        manager.prepare()
        state.manager_action = manager
        return {
            "verified": True,
            "runner_id": state.created_runner_id,
            "node": state.selected_node,
            "image": state.published_image,
        }


def _runner_args(action: ImageBuildChainAction, token_path: Path) -> argparse.Namespace:
    """Build existing runner-create inputs for the confirmed chain.

    :param action: Parent chain inputs.
    :param token_path: Fresh private token sink created after confirmation.
    :returns: Namespace accepted by ``make_plan``.
    """
    args = action.args
    return argparse.Namespace(
        action="create",
        runner_type=action.runner_kind,
        project=action.runner_reference if action.runner_kind == "project" else None,
        group=action.runner_reference if action.runner_kind == "group" else None,
        description=args.runner_description,
        protected=getattr(args, "protected", None),
        tag=list(args.runner_label),
        runner_id=None,
        job_id=None,
        token_out=str(token_path),
        limit=10,
        filter=[],
        revision=args.revision,
    )


def _manager_args(
    action: ImageBuildChainAction, state: ImageBuildChainState
) -> argparse.Namespace:
    """Build existing manager inputs from linked verified outputs.

    :param action: Parent chain inputs.
    :param state: Shared state containing runner, image, and node identity.
    :returns: Namespace accepted by ``GitLabRunnerManagerAction``.
    """
    values = vars(action.args).copy()
    values.update(
        kind="gitlab_runner_manager",
        action="deploy",
        project=action.runner_reference if action.runner_kind == "project" else None,
        group=action.runner_reference if action.runner_kind == "group" else None,
        runner_id=state.created_runner_id,
        description=action.args.runner_description,
        tag=list(action.args.runner_label),
        node=state.selected_node,
        image=state.published_image,
        token_file=str(state.token_path),
        run_id=action.manager_scope.run_id,
        namespace=action.args.namespace,
        apply=True,
        dry_run=False,
        live_plan=False,
        confirm_plan=None,
    )
    return argparse.Namespace(**values)
