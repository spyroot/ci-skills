"""Typed state and stable plan inputs for the CI13 image-build chain.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Optional

from lib.python.access.credentials import bind_sources
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.gitlab.gitlab_actions import ActionPlan
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession, GitLabService
from lib.python.core.gitlab.runner_manager import (
    GitLabRunnerManagerAction,
    GitLabRunnerManagerState,
)
from lib.python.core.harbor.registry_mirror import HarborService
from lib.python.core.json_types import JSONObject, copy_json
from lib.python.core.kubernetes.podman_probe import PROBE_KIND, ProbeMode
from lib.python.core.project_binding import resolve_target
from lib.python.core.report import plan_digest
from lib.python.core.target import GitLabOperationTarget, Target, TargetError
from lib.python.core.toolbox_actions import WorkflowAction

CHAIN_OPERATION: Final = "chain"
RUNNER_TOKEN_NAME: Final = "runner-token"


@dataclass
class ImageBuildChainState(GitLabRunnerManagerState):
    """Retain every linked CI13 result in one executor-owned state.

    The state satisfies the existing toolbox and runner-manager contracts while
    retaining the immutable publication and durable runner delivery read-backs.

    :param session: Active GitLab session for the currently selected scope.
    :param access: Safe GitLab access evidence retained by shared authentication.
    :param phase: Current GitLab operation phase.
    :param mutated: Known mutation outcome for the active GitLab operation.
    :param failure_source: Stable failure boundary for provider callbacks.
    :param started: Monotonic start used by existing GitLab diagnostics.
    :param operation_result: Most recent GitLab operation record.
    :param operation_completed: Whether that operation reached its update hook.

    .. attribute :: session
        :type: Optional[BoundGitLabSession]

        Active GitLab session for the currently selected scope.

    .. attribute :: access
        :type: JSONObject

        Safe GitLab access evidence retained by shared authentication.

    .. attribute :: phase
        :type: str

        Current GitLab operation phase.

    .. attribute :: mutated
        :type: Optional[bool]

        Known mutation outcome for the active GitLab operation.

    .. attribute :: failure_source
        :type: str

        Stable failure boundary for provider callbacks.

    .. attribute :: started
        :type: float

        Monotonic start used by existing GitLab diagnostics.

    .. attribute :: operation_result
        :type: JSONObject

        Most recent GitLab operation record.

    .. attribute :: operation_completed
        :type: bool

        Whether the active GitLab operation reached its update hook.

    .. attribute :: chain_plan
        :type: JSONObject

        Stable linked plan confirmed by the caller.

    .. attribute :: chain_plan_digest
        :type: str

        SHA-256 fingerprint of ``chain_plan``.

    .. attribute :: runner_auth_plan
        :type: ActionPlan

        Runner-scope access plan.

    .. attribute :: runner_create_plan
        :type: ActionPlan

        Exact plan used to create and later remove the runner.

    .. attribute :: manager_action
        :type: GitLabRunnerManagerAction

        Prepared same-node runner-manager action.

    .. attribute :: runner_gitlab
        :type: GitLabService

        Authenticated runner-scope GitLab service.

    .. attribute :: toolbox_harbor
        :type: HarborService

        Authenticated Harbor service for the toolbox image.

    .. attribute :: created_runner_id
        :type: int

        Exact runner ID returned by the create operation.

    .. attribute :: token_directory
        :type: Path

        Private temporary directory containing the runner token sink.

    .. attribute :: token_path
        :type: Path

        Private runner token file consumed by the manager workload.

    .. attribute :: chain_cleanup
        :type: JSONObject

        Final owned-resource cleanup envelope.
    """

    session: Optional[BoundGitLabSession] = field(default=None, repr=False)
    access: JSONObject = field(default_factory=dict)
    phase: str = "LIVE_PLAN"
    mutated: Optional[bool] = False
    failure_source: str = CHAIN_OPERATION
    started: float = field(default_factory=time.monotonic)
    operation_result: JSONObject = field(default_factory=dict)
    operation_completed: bool = False
    chain_plan: JSONObject = field(init=False)
    chain_plan_digest: str = field(init=False)
    runner_auth_plan: ActionPlan = field(init=False, repr=False)
    runner_create_plan: ActionPlan = field(init=False, repr=False)
    manager_action: GitLabRunnerManagerAction = field(init=False, repr=False)
    runner_gitlab: GitLabService = field(init=False, repr=False)
    toolbox_harbor: HarborService = field(init=False, repr=False)
    selected_node: str = field(init=False)
    created_runner_id: int = field(init=False)
    token_directory: Path = field(init=False, repr=False)
    token_path: Path = field(init=False, repr=False)
    chain_cleanup: JSONObject = field(default_factory=dict)


@dataclass
class ImageBuildChainAction(SkillAction):
    """Prepare stable inputs for one linked CI13 chain.

    :param args: Shared command arguments and chain-specific selections.
    :param state: One typed state used by every callback.

    .. attribute :: args
        :type: argparse.Namespace

        Shared command arguments and chain-specific selections.

    .. attribute :: state
        :type: ImageBuildChainState

        One typed state used by every callback.

    .. attribute :: target
        :type: Target

        Resolved GitLab, Kubernetes, and Harbor target.

    .. attribute :: toolbox
        :type: WorkflowAction

        Existing toolbox build-and-push action.

    .. attribute :: gitlab_target
        :type: GitLabOperationTarget

        GitLab authority and target-file identity used by shared adapters.

    .. attribute :: target_source
        :type: str

        Reportable source that resolved the selected target.

    .. attribute :: mode
        :type: ProbeMode

        Validated dry-run, live-plan, or confirmed-apply mode.
    """

    args: argparse.Namespace
    state: ImageBuildChainState = field(default_factory=ImageBuildChainState)
    target: Target = field(init=False)
    toolbox: WorkflowAction = field(init=False)
    gitlab_target: GitLabOperationTarget = field(init=False, repr=False)
    target_source: str = field(init=False)
    mode: ProbeMode = field(init=False)

    @property
    def runner_kind(self) -> str:
        """Select the caller-requested GitLab runner scope.

        :returns: ``project`` or ``group``.
        :raises ValueError: Both or neither scope selectors were supplied.
        """
        if bool(self.args.project) == bool(self.args.group):
            raise ValueError("chain_requires_exactly_one_project_or_group")
        return "group" if self.args.group else "project"

    def _validate_chain_inputs(self) -> None:
        """Reject missing or ambiguous chain selections before provider access.

        :returns: None after validating caller-owned inputs.
        :raises ValueError: A required selection is absent or invalid.
        """
        required = {
            "toolbox_spec": self.args.toolbox_spec,
            "runner_description": self.args.runner_description,
        }
        missing = sorted(name for name, value in required.items() if not value)
        if missing:
            raise ValueError("chain_missing_inputs:" + ",".join(missing))
        if not self.args.runner_label:
            raise ValueError("chain_runner_label_required")
        if len(set(self.args.runner_label)) != len(self.args.runner_label):
            raise ValueError("chain_runner_labels_must_be_unique")
        if not self.runner_kind:
            raise ValueError("chain_scope_required")

    def _toolbox_args(self) -> argparse.Namespace:
        """Build the existing toolbox action inputs without a second confirmation.

        :returns: Namespace consumed by ``WorkflowAction``.
        """
        values = vars(self.args).copy()
        values.update(
            kind="toolbox",
            action="create",
            spec=self.args.toolbox_spec,
            apply=False,
            dry_run=self.mode == ProbeMode.DRY_RUN,
            live_plan=False,
            confirm_plan=None,
        )
        return argparse.Namespace(**values)

    def prepare(self, mode: ProbeMode) -> None:
        """Resolve targets and construct mutation-free component actions.

        :param mode: Validated offline, live-plan, or confirmed-apply mode.
        :returns: None after retaining component actions and one shared state.
        :raises ValueError: Chain or toolbox inputs are invalid.
        :raises TargetError: Required target surfaces cannot be resolved.
        :raises OSError: A selected target, credential, or toolbox file cannot be read.
        """  # noqa: DOC503 - existing component preparation propagates errors.
        self.mode = mode
        self._validate_chain_inputs()
        target = resolve_target(
            self.args.target,
            self.args.binding,
            dry_run=mode == ProbeMode.DRY_RUN,
            required_surfaces=("gitlab", "kubernetes", "harbor"),
        )
        if mode != ProbeMode.DRY_RUN:
            target = bind_sources(target, revision=self.args.revision)
        if target.gitlab is None or target.source_file is None:
            raise TargetError("gitlab_target_unresolved")
        self.target = target
        self.target_source = target.source_kind or "target"
        self.gitlab_target = GitLabOperationTarget(target.gitlab, target.source_file)
        self.toolbox = WorkflowAction(self._toolbox_args(), context=self.state)
        self.toolbox.prepare()
        self.state.args = self.args
        self.state.runner_auth_plan = self._runner_auth_plan()

    def _runner_auth_plan(self) -> ActionPlan:
        """Create the stable runner-scope plan used only for access preflight.

        :returns: Nonmutating plan carrying the exact project or group selection.
        """
        reference = (
            self.args.group if self.runner_kind == "group" else self.args.project
        )
        return ActionPlan(
            kind="gitlab_runner",
            operation="create",
            origin=self.gitlab_target.gitlab.url,
            target_kind=self.runner_kind,
            target_reference=reference,
            target_file=str(self.gitlab_target.source_file),
            target_source=f"{self.target_source};argv:--{self.runner_kind}",
            body={
                "runner_type": f"{self.runner_kind}_type",
                "description": self.args.runner_description,
                "tag_list": ",".join(self.args.runner_label),
            },
            revision=self.args.revision,
        )

    def plan(self, selected_node: str) -> JSONObject:
        """Render one stable three-step delivery plan.

        :param selected_node: Exact worker selected for the runner workload.
        :returns: Stable toolbox build, Harbor publication, and runner inputs.
        :raises ValueError: The prepared toolbox specification is absent.
        """
        toolbox = self.toolbox.spec
        if toolbox is None:
            raise ValueError("toolbox_spec_required")
        return {
            "kind": PROBE_KIND,
            "operation": CHAIN_OPERATION,
            "target_file": str(self.target.source_file),
            "target_source": self.target.source_kind or "",
            "tested_revision": self.target.tested_revision,
            "skill": copy_json(self.target.skill),
            "toolbox": {
                "specification": toolbox.fingerprint,
                "base_image": self.toolbox.resolved_base or toolbox.base_image,
                "repository": toolbox.image_repository,
                "tag": toolbox.image_tag,
                "namespace": toolbox.namespace,
                "app": toolbox.app,
            },
            "runner": {
                "scope": self.runner_kind,
                "reference": self.args.group or self.args.project,
                "description": self.args.runner_description,
                "labels": list(self.args.runner_label),
                "node": selected_node,
                "podman_resources": {
                    "cpu_request": self.args.cpu_request,
                    "cpu_limit": self.args.cpu_limit,
                    "memory_request": self.args.memory_request,
                    "memory_limit": self.args.memory_limit,
                    "storage_size": self.args.storage_size,
                },
                "run_scope": "generated_after_confirmation",
                "token_sink": "ephemeral",
            },
        }

    def fingerprint(self, selected_node: str) -> str:
        """Hash the stable delivery plan used by live plan and apply.

        :param selected_node: Exact worker selected for the runner workload.
        :returns: SHA-256 plan fingerprint.
        """
        return plan_digest(self.plan(selected_node))
