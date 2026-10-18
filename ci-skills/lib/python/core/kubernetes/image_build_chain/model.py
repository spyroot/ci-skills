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
from lib.python.core.gitlab.gitlab_pipeline_start import prepare_start_plan
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession, GitLabService
from lib.python.core.gitlab.runner_manager import (
    GitLabRunnerManagerAction,
    GitLabRunnerManagerState,
)
from lib.python.core.harbor.registry_mirror import HarborService
from lib.python.core.json_types import JSONObject, copy_json
from lib.python.core.kubernetes.podman_callbacks import PodmanProbeState
from lib.python.core.kubernetes.podman_probe import (
    PROBE_KIND,
    PodmanProbeAction,
    ProbeMode,
    probe_resources_from_args,
)
from lib.python.core.project_binding import resolve_target
from lib.python.core.report import plan_digest
from lib.python.core.target import GitLabOperationTarget, Target, TargetError
from lib.python.core.toolbox_actions import WorkflowAction

CHAIN_OPERATION: Final = "chain"
RUNNER_TOKEN_NAME: Final = "runner-token"
DEFAULT_STUCK_AFTER: Final = 900


@dataclass
class ImageBuildChainState(PodmanProbeState, GitLabRunnerManagerState):
    """Retain every linked CI13 result in one executor-owned state.

    The state deliberately satisfies the existing probe and runner-manager state
    contracts. Pipeline callbacks use the same object and replace ``plan`` only
    after probe planning is complete.

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

    .. attribute :: pipeline_plan
        :type: ActionPlan

        Live-resolved pipeline-start plan.

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

    .. attribute :: pipeline_gitlab
        :type: GitLabService

        Authenticated pipeline-project GitLab service.

    .. attribute :: toolbox_harbor
        :type: HarborService

        Authenticated Harbor service for the toolbox image.

    .. attribute :: build_harbor
        :type: HarborService

        Authenticated Harbor service for build-image readback.

    .. attribute :: created_runner_id
        :type: int

        Exact runner ID returned by the create operation.

    .. attribute :: pipeline_id
        :type: int

        Exact pipeline ID returned by the start operation.

    .. attribute :: pipeline_watch
        :type: JSONObject

        Terminal pipeline-graph watch evidence.

    .. attribute :: job_readback
        :type: JSONObject

        Linked job, runner, artifact, and Harbor evidence.

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
    pipeline_plan: ActionPlan = field(init=False, repr=False)
    runner_auth_plan: ActionPlan = field(init=False, repr=False)
    runner_create_plan: ActionPlan = field(init=False, repr=False)
    manager_action: GitLabRunnerManagerAction = field(init=False, repr=False)
    runner_gitlab: GitLabService = field(init=False, repr=False)
    pipeline_gitlab: GitLabService = field(init=False, repr=False)
    toolbox_harbor: HarborService = field(init=False, repr=False)
    build_harbor: HarborService = field(init=False, repr=False)
    created_runner_id: int = field(init=False)
    pipeline_id: int = field(init=False)
    pipeline_watch: JSONObject = field(init=False)
    job_readback: JSONObject = field(init=False)
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

    .. attribute :: probe
        :type: PodmanProbeAction

        Existing Podman readiness action bound to the selected node.

    .. attribute :: toolbox
        :type: WorkflowAction

        Existing toolbox build-and-push action.

    .. attribute :: pipeline
        :type: ActionPlan

        Prepared pipeline-start plan.

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
    probe: PodmanProbeAction = field(init=False)
    toolbox: WorkflowAction = field(init=False)
    pipeline: ActionPlan = field(init=False, repr=False)
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

    @property
    def pipeline_project(self) -> str:
        """Select the project that owns the actual build pipeline.

        :returns: Explicit pipeline project for group runners, otherwise project.
        :raises ValueError: Group mode omitted the pipeline project or project mode
            supplied a different second project.
        """
        selected = self.args.pipeline_project or self.args.project
        if not selected:
            raise ValueError("pipeline_project_required_for_group_runner")
        if self.args.project and self.args.pipeline_project not in (
            None,
            self.args.project,
        ):
            raise ValueError("pipeline_project_conflicts_with_project_runner")
        return selected

    def _validate_chain_inputs(self) -> None:
        """Reject missing or ambiguous chain selections before provider access.

        :returns: None after validating caller-owned inputs.
        :raises ValueError: A required selection is absent or invalid.
        """
        required = {
            "toolbox_spec": self.args.toolbox_spec,
            "ref": self.args.ref,
            "job": self.args.job,
            "job_digest_artifact": self.args.job_digest_artifact,
            "runner_description": self.args.runner_description,
            "build_repository": self.args.build_repository,
            "build_tag": self.args.build_tag,
        }
        missing = sorted(name for name, value in required.items() if not value)
        if missing:
            raise ValueError("chain_missing_inputs:" + ",".join(missing))
        if not self.args.runner_label:
            raise ValueError("chain_runner_label_required")
        if len(set(self.args.runner_label)) != len(self.args.runner_label):
            raise ValueError("chain_runner_labels_must_be_unique")
        if self.args.watch_interval <= 0 or self.args.watch_timeout <= 0:
            raise ValueError("chain_watch_bounds_must_be_positive")
        if not (self.runner_kind and self.pipeline_project):
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

    def _pipeline_args(self) -> argparse.Namespace:
        """Build the existing pipeline-start plan inputs.

        :returns: Namespace consumed by ``prepare_start_plan``.
        """
        values = vars(self.args).copy()
        values.update(
            project=self.pipeline_project,
            variable=list(self.args.pipeline_variable),
            require_protected_ref=False,
        )
        return argparse.Namespace(**values)

    def prepare(self, mode: ProbeMode) -> None:
        """Resolve targets and construct mutation-free component actions.

        :param mode: Validated offline, live-plan, or confirmed-apply mode.
        :returns: None after retaining component actions and one shared state.
        :raises ValueError: Chain, probe, toolbox, or pipeline inputs are invalid.
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
        probe_mode = (
            ProbeMode.DRY_RUN if mode == ProbeMode.DRY_RUN else ProbeMode.LIVE_PLAN
        )
        self.probe = PodmanProbeAction(
            target=target,
            node=self.args.node,
            probe_image=self.args.probe_image,
            inner_image=self.args.inner_image,
            timeout=self.args.timeout,
            resources=probe_resources_from_args(self.args),
            mode=probe_mode,
            confirm_plan=None,
            namespace=self.args.namespace,
        )
        self.toolbox = WorkflowAction(self._toolbox_args(), context=self.state)
        self.toolbox.prepare()
        self.pipeline = prepare_start_plan(
            self._pipeline_args(), self.gitlab_target, self.target_source
        )
        self.state.args = self.args
        self.state.pipeline_plan = self.pipeline
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

    def plan(self, selected_node: str, pipeline: ActionPlan) -> JSONObject:
        """Render one stable chain plan without volatile provider observations.

        :param selected_node: Caller-selected or live-resolved worker name.
        :param pipeline: Unresolved offline or exact-ref live pipeline plan.
        :returns: Stable component inputs and linked proof expectations.
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
            "probe": self.probe.plan(selected_node),
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
            "pipeline": {
                "project": self.pipeline_project,
                "plan": pipeline.public(),
                "job": self.args.job,
                "digest_artifact": self.args.job_digest_artifact,
            },
            "publication_readback": {
                "repository": self.args.build_repository,
                "tag": self.args.build_tag,
            },
            "cleanup": {
                "manager_workload": "required",
                "runner_record": "required",
                "runner_token": "required",
                "run_scope": "required_when_owned",
            },
        }

    def fingerprint(self, selected_node: str, pipeline: ActionPlan) -> str:
        """Hash the stable chain plan used by live plan and apply.

        :param selected_node: Live-resolved worker name.
        :param pipeline: Live-resolved pipeline plan with exact commit SHA.
        :returns: SHA-256 plan fingerprint.
        """
        return plan_digest(self.plan(selected_node, pipeline))
