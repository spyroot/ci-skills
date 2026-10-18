"""Typed state and inputs for one GitLab runner-manager workflow.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Optional

from lib.python.access.credentials import bind_sources
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.gitlab.gitlab_runners import RunnerRecord
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession, GitLabService
from lib.python.core.json_types import JSONObject, copy_json
from lib.python.core.kubernetes.podman_runtime import (
    DEFAULT_SERVICE_ACCOUNT,
    PRIVILEGED_CONSTRAINT,
    PodmanResources,
    PodmanRuntimeSettings,
)
from lib.python.core.kubernetes.run_scope import (
    RunScope,
    RunScopeState,
    validate_run_id,
)
from lib.python.core.kubernetes.scc_access import SCCAccessAction
from lib.python.core.project_binding import resolve_target
from lib.python.core.report import plan_digest
from lib.python.core.runner import RunnerAction
from lib.python.core.toolbox_actions import WorkflowAction

from .token import (
    DeferredRunnerToken,
    NoRunnerToken,
    RunnerTokenSelection,
    read_runner_token,
    token_path,
)

MANAGER_ROLE: Final = "gitlab-runner-manager"


def runner_evidence(record: RunnerRecord) -> JSONObject:
    """Copy one typed runner into finite public evidence.

    :param record: Validated runner record from exact scoped read-back.
    :returns: Safe identity, status, tags, contact, and runtime fields.
    """
    return {
        "id": record["id"],
        "description": record["description"],
        "runner_type": record["runner_type"],
        "tag_list": list(record["tag_list"]),
        "status": record["status"],
        "online": record["online"],
        "paused": record["paused"],
        "contacted_at": record["contacted_at"],
        "version": record["version"],
        "platform": record["platform"],
        "architecture": record["architecture"],
    }


@dataclass
class GitLabRunnerManagerState(RunScopeState):
    """Retain exact provider and run-scope state for one manager lifecycle.

    Authentication assigns the private session and service. Scope callbacks
    assign the concrete namespace before workload callbacks derive a runner action.

    .. attribute :: configured_session
        :type: BoundGitLabSession

        Authenticated session populated by the GitLab callback.

    .. attribute :: gitlab
        :type: GitLabService

        GitLab API service populated by authentication.

    .. attribute :: gitlab_runner
        :type: RunnerRecord

        Exact runner record retained by the observation callback.
    """

    configured_session: BoundGitLabSession = field(init=False, repr=False)
    gitlab: GitLabService = field(init=False, repr=False)
    gitlab_runner: RunnerRecord = field(init=False, repr=False)


@dataclass(frozen=True)
class ManagerWorkloadAction(SkillAction):
    """Select the exact long-lived manager workload from a resolved run scope.

    :param workflow: Prepared command inputs shared by all manager callbacks.

    .. attribute :: workflow
        :type: GitLabRunnerManagerAction

        Prepared command inputs shared by manager callbacks.
    """

    workflow: GitLabRunnerManagerAction

    def runner(self, scope: RunScope) -> RunnerAction:
        """Construct the shared runner action for the resolved scope.

        :param scope: Concrete run namespace and stable resource names.
        :returns: Provider-independent runner workload inputs.
        """
        return RunnerAction(
            namespace=scope.namespace,
            name=scope.name(MANAGER_ROLE),
            source_root="",
            labels=self.workflow.labels,
            timeout=self.workflow.args.timeout,
            ownership_id=scope.run_id,
            podman=self.workflow.podman_runtime,
        )

    def document(self, scope: RunScope) -> JSONObject:
        """Describe CI13 Podman placement inputs without rendering a manifest.

        :param scope: Concrete run namespace and resource identity.
        :returns: Exact node, account, privilege, storage, labels, and runner fields.
        """
        runner = self.runner(scope)
        return {
            "runner": {
                "namespace": runner.namespace,
                "name": runner.name,
                "source_root": runner.source_root,
                "labels": list(runner.labels),
                "timeout": runner.timeout,
                "ownership_id": runner.ownership_id,
            },
            "security_constraint": PRIVILEGED_CONSTRAINT,
            "podman": runner.podman.plan() if runner.podman is not None else None,
        }

    def plan_document(self) -> JSONObject:
        """Describe inputs before a generated run scope exists.

        :returns: Stable workload inputs bound by confirmation. A generated
            namespace and resource name remain absent until confirmed apply.
        """
        workflow = self.workflow
        candidate = workflow.candidate_scope
        run_id = workflow.requested_run_id
        namespace = getattr(workflow.args, "namespace", "") or None
        return {
            "runner": {
                "namespace": namespace,
                "name": candidate.name(MANAGER_ROLE) if candidate else None,
                "source_root": "",
                "labels": list(workflow.labels),
                "timeout": workflow.args.timeout,
                "ownership_id": run_id,
            },
            "security_constraint": PRIVILEGED_CONSTRAINT,
            "podman": (
                workflow.podman_runtime.plan()
                if workflow.podman_runtime is not None
                else None
            ),
        }


@dataclass
class GitLabRunnerManagerAction(WorkflowAction):
    """Prepare one confirmed GitLab manager deployment or deletion.

    :param args: Catalogue-backed command arguments.
    :param context: Shared provider and run-scope state.

    .. attribute :: context
        :type: GitLabRunnerManagerState

        Shared provider and run-scope state for this invocation.

    .. attribute :: runner_token
        :type: RunnerTokenSelection

        Absent, deferred, or privately bound registration token.

    .. attribute :: candidate_scope
        :type: Optional[RunScope]

        Scope prepared for an explicit deploy run ID, otherwise absent.

    .. attribute :: podman_runtime
        :type: Optional[PodmanRuntimeSettings]

        Exact deploy settings; deletion has no Podman runtime input.
    """

    context: GitLabRunnerManagerState = field(default_factory=GitLabRunnerManagerState)
    runner_token: RunnerTokenSelection = field(init=False, repr=False)
    candidate_scope: Optional[RunScope] = field(init=False, default=None)
    podman_runtime: Optional[PodmanRuntimeSettings] = field(
        init=False,
        default=None,
    )

    @property
    def runner_id(self) -> int:
        """Parse the required positive runner identifier.

        :returns: Positive GitLab runner ID distinct from booleans.
        :raises ValueError: The input is absent, nonnumeric, or nonpositive.
        """
        value = getattr(self.args, "runner_id", "")
        try:
            selected = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("runner_id_must_be_positive") from exc
        if isinstance(value, bool) or selected <= 0:
            raise ValueError("runner_id_must_be_positive")
        return selected

    @property
    def description(self) -> str:
        """Read the exact nonempty runner description.

        :returns: Trimmed GitLab runner description.
        :raises ValueError: The supplied description is absent or blank.
        """
        value = getattr(self.args, "description", "")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("runner_description_required")
        return value.strip()

    @property
    def labels(self) -> tuple[str, ...]:
        """Read the unique nonempty runner tags selected by the caller.

        :returns: Exact tags in caller order.
        :raises ValueError: A tag is blank or repeated.
        """
        labels = tuple(getattr(self.args, "label", ()))
        if self.args.action == "deploy" and not labels:
            raise ValueError("runner_label_required")
        if len(set(labels)) != len(labels):
            raise ValueError("runner_labels_must_be_unique")
        if any(not isinstance(label, str) or not label.strip() for label in labels):
            raise ValueError("runner_label_must_be_nonempty")
        return labels

    @property
    def node(self) -> str:
        """Read the exact CI13 worker selected for the manager.

        :returns: Trimmed Kubernetes node name.
        :raises ValueError: Deploy omitted the node selected by the probe.
        """
        value = getattr(self.args, "node", "")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("runner_manager_node_required")
        return value.strip()

    @property
    def run_id(self) -> str:
        """Read the exact selected or resolved scope identity.

        :returns: DNS-safe run identifier.
        :raises ValueError: The operation has not selected or opened a run scope.
        """
        if hasattr(self.context, "run_scope"):
            return self.context.run_scope.run_id
        if self.requested_run_id is not None:
            return self.requested_run_id
        raise ValueError("runner_manager_run_id_unresolved")

    @property
    def requested_run_id(self) -> Optional[str]:
        """Read the caller-selected run identity without generating one.

        :returns: Validated identifier, or ``None`` for deploy-time generation.
        :raises ValueError: Delete omitted the required run identifier or syntax
            is invalid.
        """
        value = getattr(self.args, "run_id", "") or None
        if self.args.action == "delete" and value is None:
            raise ValueError("runner_manager_run_id_required")
        return validate_run_id(value) if value is not None else None

    def workload(self) -> ManagerWorkloadAction:
        """Build the typed deferred manager workload action.

        :returns: Workload action that resolves names from shared scope state.
        """
        return ManagerWorkloadAction(self)

    def scc_access(self) -> SCCAccessAction:
        """Select the reusable OpenShift permission required by Podman.

        :returns: Existing privileged SCC and run-scoped default ServiceAccount.
        """
        return SCCAccessAction(PRIVILEGED_CONSTRAINT, DEFAULT_SERVICE_ACCOUNT)

    def _prepare_scope(self) -> None:
        """Prepare a deploy candidate or validate delete lookup identity.

        :returns: None after assigning a deploy candidate scope.
        :raises ValueError: Namespace or run identifier syntax is invalid.
        """
        namespace = getattr(self.args, "namespace", "") or None
        selected_run_id = self.requested_run_id
        if self.args.action == "deploy":
            if selected_run_id is not None:
                self.candidate_scope = RunScope.new(
                    namespace=namespace,
                    run_id=selected_run_id,
                )
            return

    def prepare(self) -> None:
        """Bind providers and typed manager inputs without mutating them.

        :returns: None after retaining all plan inputs.
        :raises ValueError: Modes, identity, node, labels, or token inputs are invalid.
        :raises TargetError: Target, credential source, or token file is invalid.
        """  # noqa: DOC503 - Shared readers propagate declared errors.
        self._validate()
        if self.args.action not in {"deploy", "delete"}:
            raise ValueError("runner_manager_action_invalid")
        self.context.args = self.args
        surfaces = (
            ("gitlab", "kubernetes", "harbor")
            if self.args.action == "deploy"
            else ("gitlab", "kubernetes")
        )
        self.target = resolve_target(
            self.args.target,
            self.args.binding,
            dry_run=self.args.dry_run,
            required_surfaces=surfaces,
        )
        if not self.args.dry_run:
            self.target = bind_sources(self.target, revision=self.args.revision)
        self.args.runner_id = self.runner_id
        self.args.description = self.description
        self.args.label = list(self.labels)
        self._prepare_scope()
        if self.args.action == "delete":
            self.runner_token = NoRunnerToken()
            return
        self.args.node = self.node
        defaults = PodmanResources()
        resources = PodmanResources(
            cpu_request=getattr(self.args, "cpu_request", None) or defaults.cpu_request,
            cpu_limit=getattr(self.args, "cpu_limit", None) or defaults.cpu_limit,
            memory_request=getattr(self.args, "memory_request", None)
            or defaults.memory_request,
            memory_limit=getattr(self.args, "memory_limit", None)
            or defaults.memory_limit,
            storage_size=getattr(self.args, "storage_size", None)
            or defaults.storage_size,
        )
        self.podman_runtime = PodmanRuntimeSettings(
            node_name=self.args.node,
            service_account=DEFAULT_SERVICE_ACCOUNT,
            resources=resources,
        )
        path = token_path(getattr(self.args, "token_file", ""))
        self.runner_token = (
            DeferredRunnerToken(path) if self.args.dry_run else read_runner_token(path)
        )

    def _session_evidence(self) -> JSONObject:
        """Render bound or deferred GitLab session identity.

        :returns: Nonsecret provider target and credential identity.
        """
        if hasattr(self.context, "configured_session"):
            session = self.context.configured_session
            return {
                "target_kind": session.target_kind,
                "target_reference": session.target_reference,
                "credential_digest": session.credential_digest,
            }
        return {
            "state": "deferred",
            "project": getattr(self.args, "project", None),
            "group": getattr(self.args, "group", None),
        }

    def _runner_evidence(self) -> JSONObject:
        """Render stable declared runner identity for confirmation.

        Volatile liveness, contact time and runtime version remain read-back
        evidence and cannot invalidate an otherwise unchanged confirmation.

        :returns: Declared identity and tags used by the plan fingerprint.
        """
        if hasattr(self.context, "gitlab_runner"):
            record = self.context.gitlab_runner
            return {
                "id": record["id"],
                "description": record["description"],
                "runner_type": record["runner_type"],
                "tag_list": list(record["tag_list"]),
            }
        return {
            "state": "deferred",
            "id": self.runner_id,
            "description": self.description,
            "tag_list": list(self.labels),
        }

    def scope_plan(self) -> tuple[JSONObject, JSONObject]:
        """Bind selected scope inputs without inventing generated apply state.

        :returns: Stable run-scope and workload plan documents.
        """
        found = tuple(self.context.found_scopes)
        if found and hasattr(self.context, "run_scope"):
            scope = self.context.run_scope
            return scope.as_dict(), self.workload().document(scope)
        namespace = getattr(self.args, "namespace", "") or None
        scope: JSONObject = {
            "run_id": self.requested_run_id or "",
            "namespace": namespace,
            "owns_namespace": namespace is None,
            "state": "generated_on_apply"
            if self.requested_run_id is None
            else "selected",
        }
        return scope, self.workload().plan_document()

    def fingerprint(self) -> str:
        """Bind provider, credential, image, scope, node, and before-state.

        :returns: Canonical nonsecret plan fingerprint.
        :raises ValueError: Preparation omitted required target or scope provenance.
        :raises TypeError: A selected input is not finite JSON.
        """  # noqa: DOC503 - Shared JSON copying propagates unsupported values.
        target_file = self.target.source_file
        if target_file is None or self.target.gitlab is None:
            raise ValueError("runner_manager_target_unresolved")
        scope_evidence, workload = self.scope_plan()
        return plan_digest(
            {
                "kind": self.args.kind,
                "operation": self.args.action,
                "skill": copy_json(self.target.skill),
                "tested_revision": self.target.tested_revision,
                "target_file": str(target_file),
                "target_source": self.target.source_kind,
                "gitlab": {
                    "origin": self.target.gitlab.url,
                    "session": self._session_evidence(),
                    "runner": self._runner_evidence(),
                },
                "kubernetes": {
                    "context": self.target.kubernetes.context,
                    "server": self.target.kubernetes.server,
                }
                if self.target.kubernetes
                else None,
                "harbor": {
                    "url": self.target.harbor.url,
                    "project": self.target.harbor.project,
                }
                if self.target.harbor
                else None,
                "image": getattr(self.args, "image", None),
                "registration": self.runner_token.document(),
                "run_scope": scope_evidence,
                "workload": workload,
                "authorization": self.scc_access().plan()
                if self.args.action == "deploy"
                else None,
            }
        )
