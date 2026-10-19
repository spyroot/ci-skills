"""Late-bound component adapters for the CI13 image-build chain.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from typing import Optional

from lib.python.core.abstract.callback import AbstractCallback, LocalCallback
from lib.python.core.abstract.skill_result import require_result
from lib.python.core.gitlab.gitlab_action import GitLabAuth, GitLabAuthAction
from lib.python.core.gitlab.gitlab_actions import ActionError, ActionPlan, make_plan
from lib.python.core.gitlab.gitlab_pipeline_start import StartPipeline
from lib.python.core.gitlab.gitlab_pipeline_watch import WatchPipeline
from lib.python.core.gitlab.gitlab_pipeline_watch_types import PipelineWatchAction
from lib.python.core.gitlab.gitlab_runners import CreateRunner
from lib.python.core.gitlab.runner_manager import (
    GitLabRunnerManagerAction,
)
from lib.python.core.json_types import JSONObject, JSONValue, copy_json
from lib.python.core.kubernetes.kubernetes import DEFAULT_COMMAND_TIMEOUT
from lib.python.core.kubernetes.podman_probe import ProbeMode
from lib.python.core.report import plan_digest

from .model import RUNNER_TOKEN_NAME, ImageBuildChainAction, ImageBuildChainState


class ProviderTransition(StrEnum):
    """Closed provider-service transitions used by the shared chain state.

    .. attribute :: RETAIN_TOOLBOX_HARBOR

        Retain the authenticated toolbox Harbor service.

    .. attribute :: USE_TOOLBOX_HARBOR

        Restore the retained toolbox Harbor service.

    .. attribute :: RETAIN_RUNNER_GITLAB

        Retain the authenticated runner-scope GitLab service.

    .. attribute :: RETAIN_PIPELINE_GITLAB

        Retain the authenticated pipeline-project GitLab service.

    .. attribute :: USE_RUNNER_GITLAB

        Restore the retained runner-scope GitLab service.

    .. attribute :: USE_PIPELINE_GITLAB

        Restore the retained pipeline-project GitLab service.
    """

    RETAIN_TOOLBOX_HARBOR = "retain_toolbox_harbor"
    USE_TOOLBOX_HARBOR = "use_toolbox_harbor"
    RETAIN_RUNNER_GITLAB = "retain_runner_gitlab"
    RETAIN_PIPELINE_GITLAB = "retain_pipeline_gitlab"
    USE_RUNNER_GITLAB = "use_runner_gitlab"
    USE_PIPELINE_GITLAB = "use_pipeline_gitlab"


class DeferredComponent(LocalCallback[ImageBuildChainState, ImageBuildChainAction]):
    """Delegate one late-bound component callback into the shared lifecycle."""

    def __init__(
        self,
        action: ImageBuildChainAction,
        factory: Callable[[ImageBuildChainState], AbstractCallback],
    ):
        """Retain a component factory until its upstream IDs exist.

        :param action: Parent chain inputs.
        :param factory: Constructs one existing callback against current state.
        """
        super().__init__(action)
        self.factory = factory
        self.child: Optional[AbstractCallback] = None

    def collect(self) -> Mapping[str, JSONValue]:
        """Construct and bind the existing callback to this exact state.

        :returns: Child collection evidence.
        :raises ValueError: The required provider configuration is unavailable.
        """
        child = self.factory(self.state)
        child.bind(self.state)
        require_result(child.read_config())
        if not child.is_configured():
            raise ValueError("deferred_component_configuration_missing")
        self.child = child
        return child.collect()

    def run(self) -> Mapping[str, JSONValue]:
        """Run the existing callback after collection.

        :returns: Child run evidence.
        :raises ValueError: Collection did not construct the callback.
        """
        if self.child is None:
            raise ValueError("deferred_component_collection_required")
        result = require_result(self.child.run())
        self.state.results[self.child] = result
        return result

    def update(self) -> Mapping[str, JSONValue]:
        """Delegate post-run state retention.

        :returns: Child update evidence.
        :raises ValueError: Collection did not construct the callback.
        """
        if self.child is None:
            raise ValueError("deferred_component_collection_required")
        result = require_result(self.child.update())
        self.state.results[self.child] = result
        return result

    def cleanup(self) -> Mapping[str, JSONValue]:
        """Delegate component cleanup when construction reached collection.

        :returns: Child cleanup evidence or an empty mapping.
        """
        if self.child is None:
            return {}
        try:
            return self.child.cleanup()
        finally:
            self.state.results.pop(self.child, None)

    @property
    def complete(self) -> bool:
        """Preserve the child callback's continuation contract.

        :returns: Child completeness, or false before collection.
        """
        return self.child is not None and self.child.complete


class ScopedGitLabAuth(DeferredComponent):
    """Authenticate one retained GitLab plan without cross-scope state drift."""

    def __init__(self, action: ImageBuildChainAction, plan: ActionPlan):
        """Retain the exact project or group plan until callback collection.

        :param action: Parent chain target and timeout.
        :param plan: Runner or pipeline scope selected for access verification.
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
        elif self.role == ProviderTransition.RETAIN_PIPELINE_GITLAB:
            state.pipeline_gitlab = state.gitlab
            state.pipeline_plan = state.plan
            state.access_evidence["gitlab_pipeline"] = copy_json(state.access)
        elif self.role == ProviderTransition.USE_RUNNER_GITLAB:
            state.gitlab = state.runner_gitlab
            state.configured_session = state.runner_gitlab.session
        elif self.role == ProviderTransition.USE_PIPELINE_GITLAB:
            state.gitlab = state.pipeline_gitlab
            state.configured_session = state.pipeline_gitlab.session
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
        if not hasattr(state, "selected_node") or not hasattr(state, "pipeline_plan"):
            raise ValueError("chain_live_plan_inputs_missing")
        document = action.plan(state.selected_node, state.pipeline_plan)
        digest = plan_digest(document)
        state.chain_plan = document
        state.chain_plan_digest = digest
        if action.mode == ProbeMode.APPLY and action.args.confirm_plan != digest:
            raise ValueError("confirm_plan_mismatch_rerun_live_plan")
        return {"verified": True, "plan_digest": digest}


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
        """Construct manager inputs using the probe node and toolbox digest.

        :returns: Verified runner, node, image, and run-scope identity.
        :raises ValueError: A required upstream identity is missing.
        :raises TargetError: Existing manager preparation cannot bind its target.
        """  # noqa: DOC503 - manager preparation propagates existing errors.
        state = self.state
        if not hasattr(state, "published_image") or not hasattr(state, "run_scope"):
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
            "run_id": state.run_scope.run_id,
        }


class StartBuildPipeline(DeferredComponent):
    """Start the live-resolved caller-selected build pipeline."""

    def __init__(self, action: ImageBuildChainAction):
        """Retain chain inputs until the manager is online.

        :param action: Parent chain inputs.
        """
        super().__init__(action, self._factory)

    @staticmethod
    def _factory(state: ImageBuildChainState) -> StartPipeline:
        """Bind the live pipeline plan to this run and reuse its callback.

        :param state: Shared state containing the exact-ref plan and run scope.
        :returns: Existing start callback bound to the current run identity.
        """
        state.plan = replace(
            state.pipeline_plan,
            body={**state.pipeline_plan.body, "run_id": state.run_scope.run_id},
        )
        return StartPipeline(state.plan)

    def update(self) -> Mapping[str, JSONValue]:
        """Retain the new pipeline ID after existing read-back succeeds.

        :returns: Child update evidence.
        :raises ValueError: Start result lacks verified pipeline identity.
        """
        evidence = super().update()
        result = self.result
        identifier = result.get("id")
        if (
            result.get("verified") is not True
            or type(identifier) is not int
            or identifier <= 0
        ):
            raise ValueError("pipeline_start_not_verified")
        self.state.pipeline_id = identifier
        return evidence


class WatchBuildPipeline(DeferredComponent):
    """Require the selected pipeline graph to settle successfully."""

    def __init__(self, action: ImageBuildChainAction):
        """Retain chain watch bounds until the pipeline exists.

        :param action: Parent chain inputs.
        """
        super().__init__(action, self._factory)

    def _factory(self, state: ImageBuildChainState) -> WatchPipeline:
        """Construct the existing bounded watch for the created pipeline.

        :param state: Shared state containing the created pipeline ID.
        :returns: Existing pipeline watch callback.
        """
        watch = PipelineWatchAction(
            pipeline_id=state.pipeline_id,
            related_name=None,
            interval=self.action.args.watch_interval,
            timeout=self.action.args.watch_timeout,
        )
        return WatchPipeline(watch, settled=lambda _: None)

    def run(self) -> Mapping[str, JSONValue]:
        """Run the watch and reject complete-but-unsuccessful evidence.

        :returns: Successful bounded watch evidence.
        :raises ActionError: The graph is incomplete or any pipeline failed.
        """
        result = super().run()
        if result.get("complete") is not True or result.get("success") is not True:
            raise ActionError("pipeline_watch_not_successful")
        self.state.pipeline_watch = dict(result)
        return result


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
        project=args.project if action.runner_kind == "project" else None,
        group=args.group if action.runner_kind == "group" else None,
        description=args.runner_description,
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
    :param state: Shared state containing runner, scope, image, and node identity.
    :returns: Namespace accepted by ``GitLabRunnerManagerAction``.
    """
    values = vars(action.args).copy()
    values.update(
        kind="gitlab_runner_manager",
        action="deploy",
        project=action.args.project if action.runner_kind == "project" else None,
        group=action.args.group if action.runner_kind == "group" else None,
        runner_id=state.created_runner_id,
        description=action.args.runner_description,
        label=list(action.args.runner_label),
        node=state.selected_node,
        image=state.published_image,
        token_file=str(state.token_path),
        run_id=state.run_scope.run_id,
        namespace=state.run_scope.namespace,
        apply=True,
        dry_run=False,
        live_plan=False,
        confirm_plan=None,
    )
    return argparse.Namespace(**values)
