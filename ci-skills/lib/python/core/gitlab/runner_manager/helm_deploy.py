"""Install or remove the kubernetes-executor manager through the official chart.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

import yaml

from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.binary_build import BuildConfigLoader
from lib.python.core.cli_api import MAX_RESPONSE_BYTES
from lib.python.core.gitlab.gitlab_actions import ActionError
from lib.python.core.gitlab.gitlab_runners import read_runner_record
from lib.python.core.gitlab.gitlab_session import GitLabService
from lib.python.core.helm.native import (
    HelmClient,
    HelmError,
    HelmRelease,
    pull_verified_chart,
)
from lib.python.core.helm.resources import RenderedResource, rendered_resources
from lib.python.core.helm.storage import read_helm_storage
from lib.python.core.json_types import (
    JSONDocument,
    JSONObject,
    json_object,
    json_string,
)
from lib.python.core.kubernetes.kinds import DEPLOYMENT_KIND
from lib.python.core.kubernetes.kubernetes import (
    K8SCallback,
    KubernetesError,
    KubernetesService,
)
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.kubernetes.resources import (
    MANAGER_LABEL,
    MANAGER_VALUE,
    OPERATION_LABEL,
    ResourceSet,
)
from lib.python.core.kubernetes.run_scope import RUN_ID_LABEL, RunScope
from lib.python.core.kubernetes.runner import REGISTRY_SECRET_SUFFIX, registry_secret
from lib.python.core.runner_options import EXECUTOR_KUBERNETES
from lib.python.core.runtime import execution_trace
from lib.python.core.target import TargetError
from lib.python.sys.status import APPLIED, NO_OP

from .action import (
    GitLabRunnerManagerAction,
    GitLabRunnerManagerState,
    ManagerWorkloadAction,
)
from .chart_selection import (
    CHART_NAME,
    ResolvedRunnerChart,
    chart_archive,
    select_chart_version,
)
from .helm_values import (
    RUNNER_CHART_REPOSITORY,
    RUNNER_TOKEN_SECRET_KEY,
    UBI_IMAGE_REGISTRY,
    UBI_MANAGER_IMAGE,
    runner_chart_values,
)
from .token import BoundRunnerToken

KUBERNETES_EXECUTOR_REQUIRED: Final = "runner_manager_kubernetes_executor_required"
EXECUTOR_KUBERNETES_MANAGER_NOT_FOUND: Final = "executor_kubernetes_manager_not_found"
RUNNER_JOB_POLL_SECONDS: Final = 2
_INDEX_PATH: Final = "index.yaml"
_SECRET_KIND: Final = "Secret"
_SECRET_TYPE: Final = "Opaque"
_DEPLOYED: Final = "deployed"
_HELM_MANAGER: Final = "app.kubernetes.io/managed-by"
_HELM_RELEASE_NAME: Final = "meta.helm.sh/release-name"
_HELM_RELEASE_NAMESPACE: Final = "meta.helm.sh/release-namespace"
_DIGEST_SEPARATOR: Final = "@sha256:"
# Chart 0.82 with rbac.create=true, rbac.clusterWideAccess=false and
# runners.jobNamespace unset renders exactly these namespaced objects.
_RUNNER_CHART_TYPES: Final = MappingProxyType(
    {
        ("apps/v1", DEPLOYMENT_KIND): True,
        ("v1", "ConfigMap"): True,
        ("v1", "ServiceAccount"): True,
        ("rbac.authorization.k8s.io/v1", "Role"): True,
        ("rbac.authorization.k8s.io/v1", "RoleBinding"): True,
    }
)
_SECRET_FIELDS: Final = (
    "{{.apiVersion}}",
    "{{.kind}}",
    "{{.metadata.namespace}}",
    "{{.metadata.name}}",
    "{{.metadata.uid}}",
    f'{{{{index .metadata.labels "{MANAGER_LABEL}"}}}}',
    f'{{{{index .metadata.labels "{OPERATION_LABEL}"}}}}',
    f'{{{{index .metadata.labels "{RUN_ID_LABEL}"}}}}',
    "{{.type}}",
    "{{.immutable}}",
    "{{if not .metadata.deletionTimestamp}}live{{end}}",
    (
        "{{if .data}}"
        f'{{{{if and (eq (len .data) 1) (index .data "{RUNNER_TOKEN_SECRET_KEY}")}}}}'
        f"{RUNNER_TOKEN_SECRET_KEY}{{{{end}}}}{{{{end}}}}"
    ),
)
_SECRET_OUTPUT: Final = "go-template=" + r'{{"\n"}}'.join((*_SECRET_FIELDS, ""))


def read_instance_version(gitlab: GitLabService) -> str:
    """Read the selected instance version through the bound GitLab API client.

    :param gitlab: Authenticated GitLab service for the selected target.
    :returns: Raw ``/api/v4/version`` value such as ``18.5.2-ee``.
    :raises ActionError: The response has no readable version string.
    :raises GitLabAPIError: The native version read fails.
    """
    document = gitlab.api.get_json(gitlab.session, "version")
    version = (
        json_string(document.get("version")) if isinstance(document, Mapping) else ""
    )
    if not version:
        raise ActionError("gitlab_version_readback_invalid")
    return version


def read_chart_index(
    repository: str, *, timeout: float
) -> Sequence[Mapping[str, object]]:
    """Read the official chart index entries for the runner chart.

    :param repository: Approved HTTPS chart repository origin.
    :param timeout: Positive request bound in seconds.
    :returns: Parsed ``gitlab-runner`` chart index entries.
    :raises HelmError: The repository, response or index shape is invalid.
    """
    try:
        parsed = urlsplit(repository)
    except ValueError:
        raise HelmError("chart_repository_invalid") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise HelmError("chart_repository_invalid")
    if not math.isfinite(timeout) or timeout <= 0:
        raise HelmError("chart_index_timeout_invalid")
    url = f"{repository.rstrip('/')}/{_INDEX_PATH}"
    try:
        with build_opener().open(Request(url), timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise HelmError("chart_index_unavailable") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise HelmError("chart_index_too_large")
    try:
        document = yaml.load(raw.decode("utf-8"), Loader=BuildConfigLoader)
    except (yaml.YAMLError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise HelmError("chart_index_invalid") from exc
    if not isinstance(document, Mapping):
        raise HelmError("chart_index_invalid")
    entries = document.get("entries")
    if not isinstance(entries, Mapping):
        raise HelmError("chart_index_entries_invalid")
    selected = entries.get(CHART_NAME)
    if not isinstance(selected, list) or any(
        not isinstance(entry, Mapping) for entry in selected
    ):
        raise HelmError("chart_index_entries_invalid")
    return tuple(selected)


def resolve_runner_chart(
    gitlab: GitLabService,
    *,
    requested: Optional[str],
    timeout: float,
    repository: str = RUNNER_CHART_REPOSITORY,
) -> ResolvedRunnerChart:
    """Bind the exact pinned chart archive for the selected instance.

    :param gitlab: Authenticated GitLab service for version read-back.
    :param requested: Optional exact chart version selected by the caller.
    :param timeout: Positive bound for the chart index request.
    :param repository: Approved HTTPS chart repository origin.
    :returns: Resolved selection plus the pinned archive URL and digest.
    :raises ActionError: The instance version response is unreadable.
    :raises ChartSelectionError: No chart satisfies the requested or derived rule.
    :raises HelmError: The index cannot be read or its archive entry is invalid.
    """
    version = read_instance_version(gitlab)
    entries = read_chart_index(repository, timeout=timeout)
    selection = select_chart_version(version, entries, requested)
    archive = chart_archive(entries, selection, repository)
    return ResolvedRunnerChart(selection, archive.url, archive.digest)


def chart_evidence(resolved: ResolvedRunnerChart) -> JSONObject:
    """Render one resolved chart as finite nonsecret confirmation evidence.

    :param resolved: Chart selection and pinned archive bound by the plan.
    :returns: Chart identity, versions, provenance and archive digest.
    """
    selection = resolved.selection
    return {
        "chart": CHART_NAME,
        "chart_version": selection.chart_version,
        "app_version": selection.app_version,
        "gitlab_version": selection.gitlab_version,
        "requested": selection.requested,
        "digest": resolved.digest,
    }


def runner_chart_inventory(
    manifest: str, namespace: str
) -> tuple[RenderedResource, ...]:
    """Read the pinned runner render without using names as ownership evidence.

    :param manifest: Native Helm YAML render from the verified chart.
    :param namespace: Exact selected release namespace.
    :returns: Exact object identities needed for absence and installation read-back.
    :raises HelmError: Any expected chart object kind is missing or repeated.
    """
    resources = rendered_resources(
        manifest, namespace=namespace, accepted=_RUNNER_CHART_TYPES
    )
    if len(resources) != len(_RUNNER_CHART_TYPES) or len(
        {item.kind for item in resources}
    ) != len(_RUNNER_CHART_TYPES):
        raise HelmError("runner_chart_inventory_mismatch")
    return resources


def _read_rendered(
    service: KubernetesService, resource: RenderedResource
) -> JSONObject | None:
    group, separator, version = resource.api_version.partition("/")
    if separator:
        kind = f"{resource.kind.lower()}.{version}.{group}"
    else:
        kind = resource.kind
    return service.read(kind, resource.name, resource.namespace)


def verify_runner_chart_absent(
    service: KubernetesService, resources: Sequence[RenderedResource]
) -> None:
    """Refuse installation over any object already present at a rendered identity.

    :param service: Existing credential-bound Kubernetes transport.
    :param resources: Nonempty parsed identity inventory from the exact render.
    :raises KubernetesError: An object already occupies an intended identity.
    """
    if not resources:
        raise KubernetesError("runner_chart_inventory_missing")
    for resource in resources:
        if _read_rendered(service, resource) is not None:
            raise KubernetesError("runner_chart_resource_collision")


@dataclass(frozen=True)
class ObservedRunnerChartResource:
    """Pair one Helm-rendered identity with an independent native UID.

    .. attribute :: identity
        :type: RenderedResource

        Exact resource selected by the chart render.

    .. attribute :: uid
        :type: str

        UID from the actual Helm-managed object.
    """

    identity: RenderedResource
    uid: str


def observe_runner_chart_resources(
    service: KubernetesService,
    release: str,
    namespace: str,
    resources: Sequence[RenderedResource],
) -> tuple[ObservedRunnerChartResource, ...]:
    """Bind installed resources to the exact native Helm ownership markers.

    :param service: Existing credential-bound Kubernetes transport.
    :param release: Exact release name selected before installation.
    :param namespace: Exact release namespace selected before installation.
    :param resources: Nonempty identity inventory independently verified absent.
    :returns: Immutable resource identities for subsequent read-back and cleanup.
    :raises KubernetesError: An expected object, UID, or Helm owner is unverified.
    """
    if not resources:
        raise KubernetesError("runner_chart_inventory_missing")
    result = []
    for identity in resources:
        document = _read_rendered(service, identity)
        if document is None:
            raise KubernetesError("runner_chart_resource_absent")
        item = KubernetesObject(document)
        try:
            metadata = item.metadata
            annotations = metadata.object("annotations")
            if (
                item.text("kind") != identity.kind
                or item.text("apiVersion") != identity.api_version
                or metadata.text("name") != identity.name
                or (metadata.text("namespace") or "") != identity.namespace
                or metadata.text("deletionTimestamp") is not None
                or metadata.object("labels").text(_HELM_MANAGER) != "Helm"
                or annotations.text(_HELM_RELEASE_NAME) != release
                or annotations.text(_HELM_RELEASE_NAMESPACE) != namespace
            ):
                raise KubernetesError("runner_chart_resource_owner_mismatch")
            result.append(
                ObservedRunnerChartResource(identity, metadata.required_text("uid"))
            )
        except TypeError:
            raise KubernetesError("runner_chart_resource_invalid") from None
    return tuple(result)


def runner_deployment_helm_owned(
    document: JSONObject, release: str, namespace: str
) -> bool:
    """Check one read-back Deployment against the exact Helm release markers.

    :param document: Native Deployment read-back.
    :param release: Exact release name recorded by the chart annotations.
    :param namespace: Exact release namespace recorded by the chart annotations.
    :returns: Whether every Helm ownership marker matches this manager release.
    :raises KubernetesError: Present metadata fields are malformed.
    """
    item = KubernetesObject(document)
    metadata = item.metadata
    try:
        owned = (
            item.text("kind") == DEPLOYMENT_KIND
            and metadata.text("name") == release
            and (metadata.text("namespace") or "") == namespace
            and metadata.text("deletionTimestamp") is None
            and metadata.object("labels").text(_HELM_MANAGER) == "Helm"
            and metadata.object("annotations").text(_HELM_RELEASE_NAME) == release
            and metadata.object("annotations").text(_HELM_RELEASE_NAMESPACE)
            == namespace
        )
    except TypeError as exc:
        raise KubernetesError("runner_deployment_metadata_invalid") from exc
    return owned


def runner_deployment_pod_selector(manifest: str, namespace: str) -> str:
    """Read the exact pod template labels rendered for the manager Deployment.

    :param manifest: Native Helm YAML render already validated as an inventory.
    :param namespace: Selected release namespace.
    :returns: One native label selector matching only chart-rendered manager Pods.
    :raises HelmError: The render has no manager Deployment or no pod labels.
    """
    try:
        for document in yaml.load_all(manifest, Loader=BuildConfigLoader):
            if not isinstance(document, Mapping):
                continue
            if document.get("kind") != DEPLOYMENT_KIND:
                continue
            spec = document.get("spec")
            template = spec.get("template") if isinstance(spec, Mapping) else None
            pod_metadata = (
                template.get("metadata") if isinstance(template, Mapping) else None
            )
            labels = (
                pod_metadata.get("labels")
                if isinstance(pod_metadata, Mapping)
                else None
            )
            if not isinstance(labels, Mapping) or not labels:
                raise HelmError("runner_deployment_pod_labels_missing")
            if any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in labels.items()
            ):
                raise HelmError("runner_deployment_pod_labels_invalid")
            return ",".join(f"{key}={value}" for key, value in labels.items())
    except (yaml.YAMLError, RecursionError, ValueError) as exc:
        raise HelmError("runner_deployment_render_invalid") from exc
    raise HelmError("runner_deployment_render_missing")


def manager_pod_rows(
    document: JSONDocument,
    expected_image: str,
    image_repository: str,
) -> Sequence[JSONObject]:
    """Read running manager Pods with the resolved immutable image digest.

    :param document: Native PodList read-back requiring shape validation.
    :param expected_image: Exact manager image reference selected by the chart.
    :param image_repository: Repository prefix the digest image ID must resolve to.
    :returns: Safe Pod names, UIDs, and observed image IDs for running managers.
    :raises KubernetesError: If the PodList is malformed or no running manager
        image resolves to the selected repository.
    """
    items = document.get("items") if isinstance(document, Mapping) else None
    if not isinstance(items, list):
        raise KubernetesError("pod_list_invalid")
    rows: list[JSONObject] = []
    for pod in items:
        if not isinstance(pod, Mapping):
            raise KubernetesError("pod_list_invalid")
        metadata, status = pod.get("metadata", {}), pod.get("status", {})
        if not isinstance(metadata, Mapping) or not isinstance(status, Mapping):
            raise KubernetesError("pod_list_invalid")
        if metadata.get("deletionTimestamp"):
            continue
        uid = metadata.get("uid")
        name = metadata.get("name")
        statuses = status.get("containerStatuses", [])
        if (
            not isinstance(statuses, list)
            or not isinstance(uid, str)
            or not isinstance(name, str)
        ):
            raise KubernetesError("pod_list_invalid")
        for container in statuses:
            if not isinstance(container, Mapping):
                raise KubernetesError("pod_list_invalid")
            state = container.get("state", {})
            running = isinstance(state, Mapping) and isinstance(
                state.get("running"), Mapping
            )
            image_id = container.get("imageID")
            resolved = (
                running
                and container.get("image") == expected_image
                and isinstance(image_id, str)
                and _DIGEST_SEPARATOR in image_id
                and image_repository in image_id.partition(_DIGEST_SEPARATOR)[0]
            )
            if resolved:
                rows.append({"name": name, "uid": uid, "image_id": image_id})
    if not rows:
        raise KubernetesError("manager_pod_readback_missing")
    return rows


def runner_token_secret_name(scope: RunScope, runner_name: str) -> str:
    """Derive the same exact Secret name for creation and logical absence.

    :param scope: Concrete run namespace and stable operation identity.
    :param runner_name: Exact manager workload identity used as the name base.
    :returns: Stable DNS-safe resource name including the run suffix.
    """
    return scope.name(RUNNER_TOKEN_SECRET_KEY, prefix=runner_name)


class RunnerTokenSecret:
    """Create the exact runner-token Secret and read back only safe metadata.

    The one-time registration token enters native storage exactly once; every
    read projects identity and ownership fields without the credential value.
    """

    def __init__(
        self, service: KubernetesService, scope: RunScope, runner_name: str
    ) -> None:
        """Bind the Secret name and ownership labels to the selected run scope.

        :param service: Kubernetes transport bound to the approved target.
        :param scope: Exact manager namespace and run identity.
        :param runner_name: Exact manager workload identity used as the name base.
        """
        self._service = service
        self._scope = scope
        self._run_id = scope.run_id
        self.name = runner_token_secret_name(scope, runner_name)
        owned = ResourceSet(service, scope.namespace, self._run_id).managed(
            {
                "metadata": {
                    "name": self.name,
                    "namespace": scope.namespace,
                    "labels": {RUN_ID_LABEL: self._run_id},
                }
            }
        )
        self._metadata = json_object(owned["metadata"])
        self.labels = json_object(self._metadata["labels"])

    def _uid(self, output: str) -> str:
        fields = output.splitlines()
        identity = ["v1", _SECRET_KIND, self._scope.namespace, self.name]
        ownership = [
            MANAGER_VALUE,
            self._run_id,
            self._run_id,
            _SECRET_TYPE,
            "true",
            "live",
            RUNNER_TOKEN_SECRET_KEY,
        ]
        if (
            len(fields) != len(_SECRET_FIELDS)
            or fields[:4] != identity
            or not fields[4].strip()
            or fields[5:] != ownership
        ):
            raise KubernetesError("runner_token_secret_identity_mismatch")
        return fields[4]

    def read(self) -> str | None:
        """Read the exact Secret without returning its value in command output.

        :returns: Verified Secret UID, or None when Kubernetes reports absence.
        :raises KubernetesError: A read failed or native ownership is inconsistent.
        """
        with execution_trace(None):
            result = self._service.command(
                "-n",
                self._scope.namespace,
                "get",
                f"secret/{self.name}",
                "--ignore-not-found",
                "-o",
                _SECRET_OUTPUT,
            )
        if result.returncode:
            raise KubernetesError("runner_token_secret_read_unconfirmed")
        return self._uid(result.stdout) if result.stdout else None

    def create(self, token: str) -> str:
        """Create the immutable Secret atomically and verify its identity.

        :param token: Private one-time registration token consumed once.
        :returns: UID from independently confirmed Kubernetes read-back.
        :raises KubernetesError: Creation, identity or independent read-back failed.
        """
        document = {
            "apiVersion": "v1",
            "kind": _SECRET_KIND,
            "type": _SECRET_TYPE,
            "immutable": True,
            "metadata": self._metadata,
            "stringData": {RUNNER_TOKEN_SECRET_KEY: token},
        }
        with execution_trace(None):
            result = self._service.command(
                "create",
                "-f",
                "-",
                "-o",
                _SECRET_OUTPUT,
                input_data=json.dumps(document).encode("utf-8"),
            )
        if result.returncode:
            raise KubernetesError("runner_token_secret_write_unconfirmed")
        uid = self._uid(result.stdout)
        if self.read() != uid:
            raise KubernetesError("runner_token_secret_readback_mismatch")
        return uid

    def delete(self) -> bool:
        """Delete only the exact operation-owned Secret and verify absence.

        :returns: Whether deletion was necessary.
        :raises KubernetesError: Ownership or absence verification failed.
        """
        return self._scope.resources(self._service).delete(_SECRET_KIND, self.name)


def pause_runner(gitlab: GitLabService, runner_id: int) -> None:
    """Pause one exact runner and verify the provider retained its record.

    :param gitlab: Authenticated GitLab service for the selected runner.
    :param runner_id: Positive runner ID selected by the caller.
    :raises ActionError: The runner is not paused on independent read-back.
    :raises GitLabAPIError: The native pause or read-back fails.
    """  # noqa: DOC503 - The shared provider reader propagates GitLabAPIError.
    gitlab.api.put_json(gitlab.session, f"runners/{runner_id}", {"active": False})
    record = read_runner_record(
        gitlab.api,
        gitlab.session,
        gitlab.session.target_kind,
        gitlab.target_id,
        runner_id,
    )
    if record["paused"] is not True:
        raise ActionError("runner_pause_readback_mismatch")


def wait_for_running_jobs(
    gitlab: GitLabService,
    runner_id: int,
    *,
    timeout: float,
) -> None:
    """Wait until the provider reports no running jobs for one exact runner.

    :param gitlab: Authenticated GitLab service for the selected runner.
    :param runner_id: Positive runner ID paused before the wait.
    :param timeout: Maximum seconds to wait for running jobs to finish.
    :raises ActionError: Running jobs remain when the deadline elapses, or a
        provider job response is malformed.
    :raises GitLabAPIError: The native job reads fail.
    """
    deadline = time.monotonic() + timeout
    while True:
        document = gitlab.api.get_json(
            gitlab.session, f"runners/{runner_id}/jobs?status=running"
        )
        if not isinstance(document, Sequence) or isinstance(document, (str, bytes)):
            raise ActionError("runner_jobs_response_invalid")
        items = list(document)
        if any(not isinstance(item, Mapping) for item in items):
            raise ActionError("runner_jobs_response_invalid")
        if not items:
            return
        if time.monotonic() >= deadline:
            raise ActionError("runner_running_jobs_timeout")
        time.sleep(RUNNER_JOB_POLL_SECONDS)


class _ManagerStateAccess:
    """Read the concrete manager state shared by the Helm callbacks."""

    @property
    def manager_state(self) -> GitLabRunnerManagerState:
        """Read the concrete manager run-scope state.

        :returns: Shared state populated by scope and provider callbacks.
        :raises TypeError: The callback was composed with another state type.
        """
        if not isinstance(self.state, GitLabRunnerManagerState):
            raise TypeError("gitlab_runner_manager_state_required")
        return self.state


def _require_kubernetes_executor(workflow: GitLabRunnerManagerAction) -> None:
    """Reject Helm callbacks composed for another runner executor.

    :param workflow: Prepared manager operation retained by the callback.
    :raises ActionError: The prepared operation selected the shell executor.
    """
    if getattr(workflow.args, "executor", None) != EXECUTOR_KUBERNETES:
        raise ActionError(KUBERNETES_EXECUTOR_REQUIRED)


class SelectManagerChart(_ManagerStateAccess, K8SCallback[GitLabRunnerManagerAction]):
    """Resolve and bind the exact official chart for the selected instance."""

    @property
    def plugin_type(self) -> SkillPluginType:
        """Identify GitLab as this callback's configuration owner.

        :returns: GitLab plugin identity.
        """
        return SkillPluginType.GITLAB

    @property
    def workflow(self) -> GitLabRunnerManagerAction:
        """Read the concrete runner-manager action.

        :returns: Prepared runner-manager action.
        :raises TypeError: A direct caller supplied another action type.
        """
        if not isinstance(self.action, GitLabRunnerManagerAction):
            raise TypeError("gitlab_runner_manager_action_required")
        return self.action

    def read_config(self) -> JSONObject:
        """Reuse the caller-bound GitLab service for direct composition.

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

    def run(self) -> JSONObject:
        """Resolve the pinned chart and bind it before confirmation.

        :returns: Verified chart identity, versions, provenance and digest.
        :raises ActionError: Another executor is selected.
        :raises ChartSelectionError: No chart satisfies the requested or derived rule.
        :raises HelmError: The chart index or archive entry is invalid.
        :raises GitLabAPIError: The instance version read fails.
        """  # noqa: DOC503 - Shared chart resolution propagates declared errors.
        workflow = self.workflow
        _require_kubernetes_executor(workflow)
        requested = getattr(workflow.args, "chart_version", None)
        resolved = resolve_runner_chart(
            self.manager_state.gitlab,
            requested=requested
            if isinstance(requested, str) and requested.strip()
            else None,
            timeout=workflow.args.timeout,
        )
        workflow.chart_selection = resolved
        return {
            "verified": True,
            "executor": EXECUTOR_KUBERNETES,
            **chart_evidence(resolved),
        }


class DeployKubernetesManagerChart(
    _ManagerStateAccess, K8SCallback[ManagerWorkloadAction]
):
    """Install the official runner chart with run-owned credential Secrets."""

    def __init__(self, action: ManagerWorkloadAction):
        """Retain deferred workload inputs and empty install ownership.

        :param action: Workflow whose runner action depends on resolved scope.
        """
        super().__init__(action)
        self._installed: Optional[HelmRelease] = None
        self._secrets: Optional[ResourceSet] = None
        self._token: Optional[RunnerTokenSecret] = None
        self._token_created = False

    @property
    def workflow(self) -> GitLabRunnerManagerAction:
        """Read the concrete runner-manager action.

        :returns: Prepared runner-manager action.
        :raises TypeError: A direct caller supplied another action type.
        """
        if not isinstance(self.action, ManagerWorkloadAction):
            raise TypeError("gitlab_manager_workload_action_required")
        return self.action.workflow

    def _existing_release_evidence(
        self,
        state: GitLabRunnerManagerState,
        release: HelmRelease,
        token: RunnerTokenSecret,
        token_uid: str,
        pull_name: str,
        namespace: str,
    ) -> JSONObject:
        """Build verified no-op evidence for an already deployed manager release."""
        storage = read_helm_storage(self.kubernetes, release)
        if storage is None:
            raise KubernetesError("runner_manager_release_storage_missing")
        result: JSONObject = {
            "action": NO_OP,
            "verified": True,
            "executor": EXECUTOR_KUBERNETES,
            "namespace": namespace,
            "name": release.name,
            "release": {
                "name": release.name,
                "namespace": namespace,
                "revision": release.revision,
                "status": release.status,
            },
            "storage": {"name": storage.name, "uid": storage.uid},
            "secrets": {"token": token_uid, "pull": pull_name},
        }
        state.deployment_result = result
        return result

    def run(self) -> JSONObject:
        """Render, verify absence, install and read back the manager release.

        :returns: Applied or no-op release evidence with Pod and image read-back.
        :raises ActionError: Another executor is selected or no chart was bound.
        :raises TypeError: The deploy did not receive a bound runner token.
        :raises HelmError: A native Helm read, render or install is unconfirmed.
        :raises KubernetesError: Ownership, collision or read-back is unverified.
        """  # noqa: DOC503 - Native Helm and Kubernetes stages propagate errors.
        _require_kubernetes_executor(self.workflow)
        workflow = self.workflow
        state = self.manager_state
        scope = state.run_scope
        runner = self.action.runner(scope)
        release_name = runner.name
        namespace = scope.namespace
        timeout = workflow.args.timeout
        token_selection = workflow.runner_token
        if not isinstance(token_selection, BoundRunnerToken):
            raise TypeError("bound_runner_token_required")
        resolved = workflow.chart_selection
        if resolved is None:
            raise ActionError("runner_chart_selection_required")
        helm = HelmClient(self.kubernetes)
        resources = scope.resources(self.kubernetes)
        self._secrets = resources
        token = RunnerTokenSecret(self.kubernetes, scope, runner.name)
        self._token = token
        pull_name = runner.name + REGISTRY_SECRET_SUFFIX
        existing = helm.status(release_name, namespace, timeout=timeout)
        if existing is not None:
            if existing.status != _DEPLOYED:
                raise KubernetesError("runner_manager_release_not_deployed")
            token_uid = token.read()
            pull = self.kubernetes.read(_SECRET_KIND, pull_name, namespace)
            if token_uid is None or pull is None or not resources.owns(pull):
                raise KubernetesError("runner_manager_release_incomplete")
            return self._existing_release_evidence(
                state, existing, token, token_uid, pull_name, namespace
            )
        if token.read() is not None:
            raise KubernetesError("runner_manager_secret_already_present")
        values = runner_chart_values(
            gitlab_url=state.gitlab.session.origin,
            secret_name=token.name,
            app_version=resolved.selection.app_version,
            pull_secrets=(pull_name,),
        )
        values["fullnameOverride"] = runner.name
        first_release = HelmRelease(release_name, namespace, 1, _DEPLOYED)
        inventory: tuple[RenderedResource, ...] = ()

        def _recheck(manifest: str) -> None:
            if token.read() != token_uid:
                raise KubernetesError("runner_token_secret_readback_mismatch")
            pull = self.kubernetes.read(_SECRET_KIND, pull_name, namespace)
            if pull is None or not resources.owns(pull):
                raise KubernetesError("runner_pull_secret_unverified")
            if runner_chart_inventory(manifest, namespace) != inventory:
                raise KubernetesError("runner_chart_render_mismatch")
            if read_helm_storage(self.kubernetes, first_release) is not None:
                raise KubernetesError("runner_release_storage_already_present")

        with pull_verified_chart(
            resolved.url, resolved.digest, timeout=timeout
        ) as chart:
            rendered = helm.render(
                release_name, namespace, chart, values, timeout=timeout
            )
            inventory = runner_chart_inventory(rendered, namespace)
            verify_runner_chart_absent(self.kubernetes, inventory)
            pull_evidence = resources.apply(
                registry_secret(
                    pull_name,
                    state.harbor.dockerconfig,
                    {RUN_ID_LABEL: scope.run_id},
                )
            )
            token_uid = token.create(token_selection.value)
            self._token_created = True
            observed = helm.install_fresh(
                release_name,
                namespace,
                chart,
                values,
                verify_absent=_recheck,
                timeout=timeout,
            )
        if observed != first_release:
            raise KubernetesError("runner_manager_release_unverified")
        self._installed = observed
        storage = read_helm_storage(self.kubernetes, observed)
        if storage is None:
            raise KubernetesError("runner_manager_release_storage_missing")
        installed = observe_runner_chart_resources(
            self.kubernetes, release_name, namespace, inventory
        )
        self.kubernetes.wait_rollout(DEPLOYMENT_KIND.lower(), runner.name, namespace)
        selector = runner_deployment_pod_selector(rendered, namespace)
        pods = self.kubernetes.json(
            "-n",
            namespace,
            "get",
            "pods",
            "-l",
            selector,
            "-o",
            "json",
        )
        expected_image = (
            f"{UBI_IMAGE_REGISTRY}/{UBI_MANAGER_IMAGE}"
            f":v{resolved.selection.app_version}"
        )
        rows = manager_pod_rows(
            pods, expected_image, UBI_IMAGE_REGISTRY + "/" + UBI_MANAGER_IMAGE
        )
        result: JSONObject = {
            "action": APPLIED,
            "verified": True,
            "executor": EXECUTOR_KUBERNETES,
            "namespace": namespace,
            "name": release_name,
            "chart": chart_evidence(resolved),
            "release": {
                "name": observed.name,
                "namespace": observed.namespace,
                "revision": observed.revision,
                "status": observed.status,
            },
            "storage": {"name": storage.name, "uid": storage.uid},
            "pods": list(rows),
            "secrets": {"token": token_uid, "pull": pull_name},
            "resources": [
                *(
                    {
                        "kind": item.identity.kind,
                        "name": item.identity.name,
                        "namespace": item.identity.namespace,
                        "uid": item.uid,
                        "created": True,
                    }
                    for item in installed
                ),
                pull_evidence,
            ],
        }
        state.deployment_result = result
        return result

    def cleanup(self) -> JSONObject:
        """Uninstall a release this callback installed when the workflow failed.

        :returns: Empty evidence after any compensation; nothing on success.
        :raises KubernetesError: A failed workflow's release or Secrets remain.
        """
        state = self.manager_state
        if not state.failed:
            return {}
        token = self._token
        if self._installed is not None:
            helm = HelmClient(self.kubernetes)
            removed = helm.uninstall(
                self._installed.name,
                self._installed.namespace,
                timeout=self.workflow.args.timeout,
            )
            if (
                removed
                and read_helm_storage(self.kubernetes, self._installed) is not None
            ):
                raise KubernetesError("runner_manager_release_storage_present")
        if token is not None and self._token_created and token.read() is not None:
            token.delete()
            if token.read() is not None:
                raise KubernetesError("runner_token_secret_absence_unverified")
        if self._secrets is not None:
            self._secrets.recover()
        return {}


class UninstallKubernetesManagerChart(
    _ManagerStateAccess, K8SCallback[ManagerWorkloadAction]
):
    """Pause the runner, uninstall the release, and remove run-owned Secrets."""

    @property
    def workflow(self) -> GitLabRunnerManagerAction:
        """Read the concrete runner-manager action.

        :returns: Prepared runner-manager action.
        :raises TypeError: A direct caller supplied another action type.
        """
        if not isinstance(self.action, ManagerWorkloadAction):
            raise TypeError("gitlab_manager_workload_action_required")
        return self.action.workflow

    def run(self) -> JSONObject:
        """Pause, drain, uninstall and independently verify manager absence.

        The exact Helm-owned manager Deployment is required before any
        mutation: pausing a shell-managed runner or deleting its Secrets
        would destroy a healthy manager owned by another executor.

        :returns: Applied or no-op deletion evidence; the runner record stays.
        :raises ActionError: Another executor is selected, no chart-owned
            manager exists at the resolved identity, or pause is unverified.
        :raises HelmError: The native release uninstall is unconfirmed.
        :raises KubernetesError: Owned Secrets or their absence are unverified.
        :raises GitLabAPIError: The runner pause or job reads fail.
        """  # noqa: DOC503 - Native GitLab, Helm and Kubernetes stages propagate errors.
        _require_kubernetes_executor(self.workflow)
        state = self.manager_state
        args = getattr(state, "args", None)
        if getattr(args, "action", None) == "delete" and not state.found_scopes:
            return {}
        workflow = self.workflow
        scope = state.run_scope
        runner = self.action.runner(scope)
        release_name = runner.name
        namespace = scope.namespace
        timeout = workflow.args.timeout
        current = self.kubernetes.read("deployment", runner.name, runner.namespace)
        if current is None or not runner_deployment_helm_owned(
            current, runner.name, runner.namespace
        ):
            raise ActionError(EXECUTOR_KUBERNETES_MANAGER_NOT_FOUND)
        pause_runner(state.gitlab, workflow.runner_id)
        wait_for_running_jobs(state.gitlab, workflow.runner_id, timeout=timeout)
        helm = HelmClient(self.kubernetes)
        removed = helm.uninstall(release_name, namespace, timeout=timeout)
        first_release = HelmRelease(release_name, namespace, 1, _DEPLOYED)
        if read_helm_storage(self.kubernetes, first_release) is not None:
            raise KubernetesError("runner_manager_release_storage_present")
        token = RunnerTokenSecret(self.kubernetes, scope, runner.name)
        token_removed = token.delete() if token.read() is not None else False
        pull_name = runner.name + REGISTRY_SECRET_SUFFIX
        resources = scope.resources(self.kubernetes)
        pull_removed = resources.delete(_SECRET_KIND, pull_name)
        if (
            token.read() is not None
            or self.kubernetes.read(_SECRET_KIND, pull_name, namespace) is not None
        ):
            raise KubernetesError("runner_manager_secret_absence_unverified")
        result: JSONObject = {
            "action": APPLIED if removed or token_removed or pull_removed else NO_OP,
            "verified": True,
            "executor": EXECUTOR_KUBERNETES,
            "namespace": namespace,
            "name": release_name,
            "runner_paused": True,
            "release_removed": removed,
            "secrets": {"token_removed": token_removed, "pull_removed": pull_removed},
        }
        return result
