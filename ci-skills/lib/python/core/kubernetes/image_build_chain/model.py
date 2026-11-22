"""Typed state and stable plan inputs for the CI13 image-build chain.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Optional

from lib.python.access.credentials import bind_sources
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.endpoints import (
    GITLAB_AUTHORITY,
    HARBOR_AUTHORITY,
    KUBERNETES_AUTHORITY,
)
from lib.python.core.gitlab.gitlab_actions import ActionPlan
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession, GitLabService
from lib.python.core.gitlab.runner_manager import (
    GitLabRunnerManagerAction,
    GitLabRunnerManagerState,
)
from lib.python.core.gitlab.runner_manager.action import MANAGER_ROLE
from lib.python.core.harbor.registry_mirror import is_registry_digest
from lib.python.core.json_types import JSONObject, copy_json, json_object
from lib.python.core.kubernetes.podman_probe import PROBE_KIND, ProbeMode
from lib.python.core.kubernetes.run_scope import RUN_ID_LIMIT, RunScope
from lib.python.core.kubernetes.runner_scope import scope_key
from lib.python.core.portable import portable
from lib.python.core.project_binding import resolve_target
from lib.python.core.report import plan_digest
from lib.python.core.runner_identity import (
    NameSource,
    RepositoryIdentity,
    RepositorySource,
    ResolvedName,
    resolve_name,
    resolve_repository,
    validate_project_name,
)
from lib.python.core.runner_options import (
    RUNNER_NAME_LIMIT,
    parse_protected,
    runner_tags,
)
from lib.python.core.runner_scope import ScopeObservation, ScopePurpose, ScopeSource
from lib.python.core.runtime import redact_tree
from lib.python.core.target import (
    GitLabOperationTarget,
    GitLabSelection,
    Target,
    TargetError,
    select_gitlab_reference,
)
from lib.python.core.toolbox import ToolboxSpec
from lib.python.core.toolbox_actions import WorkflowAction

CHAIN_OPERATION: Final = "chain"
RUNNER_TOKEN_NAME: Final = "runner-token"
REGISTRY_COMPONENT: Final = re.compile(r"[a-z0-9]+(?:(?:[._]|__|[-]+)[a-z0-9]+)*")
REGISTRY_TAG: Final = re.compile(r"[\w][\w.-]{0,127}", re.ASCII)


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

    .. attribute :: selected_node
        :type: str

        Ready worker selected for the runner workload.

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

        Existing Harbor copy action for the prepared toolbox.

    .. attribute :: toolbox_spec
        :type: ToolboxSpec | None

        Explicit specification retained as publication inputs and plan metadata.

    .. attribute :: naming_base
        :type: ResolvedName

        Consumer name used for defaults at each provider.

    .. attribute :: destination_digest
        :type: str | None

        Exact destination tag digest observed before confirmation, or absence.

    .. attribute :: gitlab_target
        :type: GitLabOperationTarget

        GitLab authority and target-file identity used by shared adapters.

    .. attribute :: target_source
        :type: str

        Reportable source that resolved the selected target.

    .. attribute :: mode
        :type: ProbeMode

        Validated dry-run, live-plan, or confirmed-apply mode.

    .. attribute :: gitlab_selection
        :type: tuple[str, str]

        Effective provider kind and reference selected once from arguments and target.

    .. attribute :: repository_identity
        :type: RepositoryIdentity

        Consumer repository used for name defaults.

    .. attribute :: resolved_description
        :type: ResolvedName

        Exact selected runner description and provenance.

    .. attribute :: explicit_description
        :type: str | None

        Original caller description before consumer defaults.

    .. attribute :: explicit_tags
        :type: tuple[str, ...]

        Original caller labels before consumer defaults.

    .. attribute :: manager_scope
        :type: RunScope | None

        Candidate scope, absent while native repository identity is unresolved.

    .. attribute :: resolved_runner_name
        :type: ResolvedName | None

        Planned or independently observed manager workload name.
    """

    args: argparse.Namespace
    state: ImageBuildChainState = field(default_factory=ImageBuildChainState)
    target: Target = field(init=False)
    toolbox: WorkflowAction = field(init=False)
    toolbox_spec: ToolboxSpec | None = field(init=False, default=None)
    naming_base: ResolvedName = field(init=False)
    destination_digest: str | None = field(init=False, default=None)
    gitlab_target: GitLabOperationTarget = field(init=False, repr=False)
    target_source: str = field(init=False)
    mode: ProbeMode = field(init=False)
    gitlab_selection: tuple[str, str] = field(init=False)
    repository_identity: RepositoryIdentity = field(init=False)
    resolved_description: ResolvedName = field(init=False)
    explicit_description: str | None = field(init=False, default=None)
    explicit_tags: tuple[str, ...] = field(init=False, default=())
    manager_scope: RunScope | None = field(init=False)
    resolved_runner_name: ResolvedName | None = field(init=False, default=None)

    @property
    def project_name(self) -> str | None:
        """Read the validated consumer override independently of provider identity.

        :returns: Trimmed optional consumer naming input.
        :raises ValueError: A present override is invalid.
        """  # noqa: DOC502 - Shared input validation propagates ValueError.
        return validate_project_name(getattr(self.args, "project_name", None))

    @property
    def runner_kind(self) -> str:
        """Read the effective GitLab runner scope after target selection.

        :returns: Selected project or group kind.
        :raises AttributeError: Preparation has not resolved provider selection.
        """  # noqa: DOC502 - Accessing selection requires completed preparation.
        kind, _ = self.gitlab_selection
        return kind

    @property
    def runner_reference(self) -> str:
        """Read the effective provider reference selected during preparation.

        :returns: Exact project or group path or numeric selector.
        :raises AttributeError: Preparation has not resolved provider selection.
        """  # noqa: DOC502 - Accessing selection requires completed preparation.
        _, reference = self.gitlab_selection
        return reference

    def _validate_chain_inputs(self) -> None:
        """Reject missing or ambiguous chain selections before provider access.

        :returns: None after validating caller-owned inputs.
        :raises ValueError: A required selection is absent or invalid.
        """
        parse_protected(getattr(self.args, "protected", None))
        if self.args.project is not None and self.args.group is not None:
            raise ValueError("chain_requires_exactly_one_project_or_group")

    def _reference_image(self) -> str | None:
        """Build a native image reference within the selected Harbor project.

        :returns: Configured tag or digest reference, or pending configuration.
        :raises ValueError: The artifact selector is invalid.
        """
        harbor = self.target.harbor
        if harbor is None or harbor.reference_toolbox is None:
            return None
        repository, separator, reference = harbor.reference_toolbox.partition("@")
        if not separator:
            repository, separator, reference = repository.rpartition(":")
        valid_reference = (
            is_registry_digest(reference)
            if separator == "@"
            else REGISTRY_TAG.fullmatch(reference) is not None
        )
        if (
            not all(
                REGISTRY_COMPONENT.fullmatch(part) for part in repository.split("/")
            )
            or not valid_reference
        ):
            raise ValueError(
                "harbor_reference_toolbox_invalid: provide repository:tag or repository@sha256:digest"
            )
        return f"{harbor.host}/{harbor.project}/{repository}{separator}{reference}"

    def _select_names(self) -> None:
        """Resolve one consumer name and pass it through each default.

        :raises ValueError: The caller's explicit name or scheduling tags are invalid.
        """  # noqa: DOC502 - existing name and tag validators propagate failures.
        base = resolve_name(
            None,
            self.repository_identity,
            argument="--project-name",
            project_name=self.project_name,
            pending=True,
        )
        self.naming_base = base
        self.resolved_description = (
            resolve_name(
                self.explicit_description,
                self.repository_identity,
                argument="--description",
            )
            if self.explicit_description is not None
            else base
        )
        self.args.runner_description = self.resolved_description.value or None
        defaults = (base.value,) if base.value else ()
        self.args.runner_label = list(
            runner_tags(self.explicit_tags or defaults, self.repository_identity)
        )
        spec = self.toolbox_spec
        values = vars(self.args).copy()
        values.update(
            kind="harbor_push",
            action="push",
            apply=False,
            confirm_plan=None,
            source_image=self._reference_image(),
            repository=spec.image_repository if spec else base.value or None,
            tag=spec.image_tag if spec else base.value or None,
        )
        self.toolbox = WorkflowAction(
            argparse.Namespace(**values), context=self.state, naming_base=base
        )
        self.toolbox.target = self.target
        self.toolbox.repository_identity = self.repository_identity

    def naming_conflicts(self) -> list[JSONObject]:
        """List explicit differences from the requested consumer defaults.

        :returns: Each conflicting field with its explicit and expected values.
        """
        expected = self.naming_base.value
        if not expected:
            return []
        conflicts: list[JSONObject] = []
        if (
            self.explicit_description is not None
            and self.resolved_description.value != expected
        ):
            conflicts.append(
                {
                    "field": "runner.description",
                    "explicit": self.explicit_description,
                    "expected": expected,
                }
            )
        if self.explicit_tags and expected not in self.args.runner_label:
            conflicts.append(
                {
                    "field": "runner.labels",
                    "explicit": list(self.explicit_tags),
                    "expected": expected,
                }
            )
        if (
            self.toolbox_spec is not None
            and self.toolbox_spec.image_repository != expected
        ):
            conflicts.append(
                {
                    "field": "toolbox.repository",
                    "explicit": self.toolbox_spec.image_repository,
                    "expected": expected,
                }
            )
        return conflicts

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
        validate_project_name(getattr(self.args, "project_name", None))
        self.explicit_description = self.args.runner_description
        self.explicit_tags = tuple(self.args.runner_label)
        target = resolve_target(
            self.args.target,
            self.args.binding,
            dry_run=mode == ProbeMode.DRY_RUN,
            required_surfaces=(
                GITLAB_AUTHORITY,
                KUBERNETES_AUTHORITY,
                HARBOR_AUTHORITY,
            ),
            gitlab_selection=GitLabSelection.from_args(self.args),
        )
        if mode != ProbeMode.DRY_RUN:
            target = bind_sources(target, revision=self.args.revision)
        if target.gitlab is None or target.source_file is None:
            raise TargetError("gitlab_target_unresolved")
        self.target = target
        self.target_source = target.gitlab.selection_source(
            target.source_kind or "target"
        )
        self.gitlab_target = GitLabOperationTarget(target.gitlab, target.source_file)
        self.gitlab_selection = select_gitlab_reference(
            self.gitlab_target, project=self.args.project, group=self.args.group
        )
        self.repository_identity = (
            resolve_repository(self.runner_reference)
            if self.runner_kind == "project"
            else RepositoryIdentity(None, RepositorySource.UNKNOWN)
        )
        if self.args.toolbox_spec:
            self.toolbox_spec = ToolboxSpec.from_path(Path(self.args.toolbox_spec))
        self._select_names()
        if self.runner_kind == "group" and not self.naming_base.value:
            raise ValueError("group_runner_chain_requires_project_name")
        self._prepare_scope()
        self.state.args = self.args
        self.state.runner_auth_plan = self._runner_auth_plan()

    def _prepare_scope(self) -> None:
        """Bind one candidate from resolved inputs without inventing pending identity.

        :returns: None after retaining a candidate and its initial scope observation.
        :raises ValueError: Namespace or workload identity is invalid.
        """  # noqa: DOC502 - Shared scope generation validates caller input.
        scope_id = plan_digest(
            {
                "target": str(self.target.source_file),
                "repository": self.repository_identity.reference,
                "description": self.resolved_description.value,
                "tags": list(self.args.runner_label),
                "toolbox": self.toolbox_spec.fingerprint if self.toolbox_spec else None,
                "namespace": self.args.namespace,
            }
        )[:RUN_ID_LIMIT]
        self.manager_scope = (
            RunScope.new(
                namespace=self.args.namespace,
                run_id=scope_id,
                namespace_prefix=self.naming_base.value or None,
            )
            if self.naming_base.value or self.args.namespace
            else None
        )
        self.resolved_runner_name = (
            ResolvedName(
                self.manager_scope.bounded_name(
                    MANAGER_ROLE,
                    limit=RUNNER_NAME_LIMIT,
                    prefix=self.naming_base.value or None,
                ),
                self.naming_base.source,
            )
            if self.manager_scope is not None
            else None
        )
        if self.manager_scope is not None:
            scope = ScopeObservation(
                ScopePurpose.MANAGER,
                self.manager_scope.namespace,
                ScopeSource.EXPLICIT
                if self.args.namespace
                else ScopeSource.OPERATION_CREATED,
            )
            self.state.runner_scopes[scope_key(scope.purpose, scope.namespace)] = scope

    def bind_repository(self, access: JSONObject) -> None:
        """Bind numeric project defaults from the existing authenticated receipt.

        :param access: Verified GitLab project access evidence.
        :returns: None after resolving repository-derived inputs and candidate scope.
        :raises ValueError: Receipt identity is inconsistent or input defaults remain absent.
        """  # noqa: DOC502 - Shared identity validators propagate input failures.
        if self.runner_kind == "project" and not self.repository_identity.name:
            self.repository_identity = RepositoryIdentity.from_access(
                access, self.runner_reference
            )
            self._select_names()
            self._prepare_scope()

    def _runner_auth_plan(self) -> ActionPlan:
        """Create the stable runner-scope plan used only for access preflight.

        :returns: Nonmutating plan carrying the exact project or group selection.
        """
        reference = self.runner_reference
        return ActionPlan(
            kind="gitlab_runner",
            operation="create",
            origin=self.gitlab_target.gitlab.url,
            target_kind=self.runner_kind,
            target_reference=reference,
            target_file=str(self.gitlab_target.source_file),
            target_source=(
                f"{self.target_source};argv:--{self.runner_kind}"
                if getattr(self.args, self.runner_kind, None) is not None
                else self.target_source
            ),
            body={
                **(
                    {
                        "access_level": parse_protected(
                            getattr(self.args, "protected", None)
                        )
                    }
                    if getattr(self.args, "protected", None) is not None
                    else {}
                ),
                "runner_type": f"{self.runner_kind}_type",
                "description": self.args.runner_description,
                "tag_list": ",".join(self.args.runner_label),
            },
            revision=self.args.revision,
            project_name=self.project_name,
        )

    def plan(self, selected_node: str) -> JSONObject:
        """Render one stable three-step delivery plan.

        :param selected_node: Exact worker selected for the runner workload.
        :returns: Stable, portable toolbox publication, naming, and runner inputs.
        :raises ValueError: A live plan contains inputs that would be redacted.
        """
        spec = self.toolbox_spec
        harbor = self.target.harbor
        kubernetes = self.target.kubernetes
        document: JSONObject = {
            "kind": PROBE_KIND,
            "operation": CHAIN_OPERATION,
            "target_file": str(self.target.source_file),
            "target_source": self.target_source,
            "tested_revision": self.target.tested_revision,
            "skill": copy_json(self.target.skill),
            "consumer_name": self.naming_base.value or None,
            "project_name": self.project_name,
            "naming_conflicts": self.naming_conflicts(),
            "toolbox": {
                "mode": "copy",
                "source_image": self.toolbox.resolved_source
                or self.toolbox.args.source_image,
                "specification": spec.fingerprint if spec else None,
                "repository": self.toolbox.args.repository,
                "tag": self.toolbox.args.tag,
                "namespace": spec.namespace if spec else None,
                "app": spec.app if spec else None,
                "destination_digest": self.destination_digest,
                "origin": harbor.url if harbor else None,
                "project": harbor.project if harbor else None,
            },
            "kubernetes": {"server": kubernetes.server, "context": kubernetes.context}
            if kubernetes
            else None,
            "runner": {
                "name": (
                    self.resolved_runner_name.value
                    if self.resolved_runner_name is not None
                    else None
                ),
                "name_source": (
                    self.resolved_runner_name.source
                    if self.resolved_runner_name is not None
                    else NameSource.REPOSITORY
                    if self.repository_identity.name
                    else NameSource.UNKNOWN
                ),
                "scope": self.runner_kind,
                "reference": self.runner_reference,
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
                "run_scope": self.manager_scope.as_dict()
                if self.manager_scope is not None
                else None,
                "scope_observations": [
                    scope.document() for scope in self.state.runner_scopes.values()
                ],
                "repository": self.repository_identity.reference,
                "repository_source": self.repository_identity.source,
                "description_source": self.resolved_description.source,
                "protected": parse_protected(getattr(self.args, "protected", None)),
            },
        }
        public_plan = redact_tree(document)
        if self.mode != ProbeMode.DRY_RUN and public_plan != document:
            raise ValueError("chain_plan_contains_sensitive_values")
        return json_object(portable(public_plan))

    def fingerprint(self, selected_node: str) -> str:
        """Hash the stable delivery plan used by live plan and apply.

        :param selected_node: Exact worker selected for the runner workload.
        :returns: SHA-256 plan fingerprint.
        """
        return plan_digest(self.plan(selected_node))
