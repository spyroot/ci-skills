"""Focused unit contracts for the kubernetes-executor manager Helm path.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
from argparse import Namespace
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Final, Optional

import pytest
import yaml
from conftest import import_script_module
from lib.python.core.endpoints import (
    GITLAB_AUTHORITY,
    HARBOR_AUTHORITY,
    KUBERNETES_AUTHORITY,
)
from test_tool_pod import MemoryCluster

ABSTRACT_CALLBACK = import_script_module("lib.python.core.abstract.callback")
ACTION = import_script_module("lib.python.core.gitlab.runner_manager.action")
CALLBACKS = import_script_module("lib.python.core.gitlab.runner_manager.callbacks")
GITLAB_API = import_script_module("lib.python.core.gitlab.gitlab_api")
HELM_DEPLOY = import_script_module("lib.python.core.gitlab.runner_manager.helm_deploy")
HELM_NATIVE = import_script_module("lib.python.core.helm.native")
HELM_STORAGE = import_script_module("lib.python.core.helm.storage")
HELM_VALUES = import_script_module("lib.python.core.gitlab.runner_manager.helm_values")
K8S_RUNNER = import_script_module("lib.python.core.kubernetes.runner")
RESOURCES = import_script_module("lib.python.core.kubernetes.resources")
RUNNER_OPTIONS = import_script_module("lib.python.core.runner_options")
RUN_SCOPE = import_script_module("lib.python.core.kubernetes.run_scope")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")
RUNTIME = import_script_module("lib.python.core.runtime")
SCHEMA = import_script_module("lib.python.core.schema")
SCRIPT = import_script_module("gitlab_runner_manager")
STATUS = import_script_module("lib.python.sys.status")
TARGET = import_script_module("lib.python.core.target")
TOKEN = import_script_module("lib.python.core.gitlab.runner_manager.token")

PRIVATE_TOKEN = "glrt-private-unit-value"
TOKEN_DIGEST = "sha256:" + hashlib.sha256(PRIVATE_TOKEN.encode()).hexdigest()
RUN_ID = "manager-helm-unit-run"
NAMESPACE = "manager-helm-unit-namespace"
RUNNER_ID = 73
RUNNER_DESCRIPTION = "unit-manager-runner"
RUNNER_LABELS = ("ci13", "unit")
GITLAB_VERSION = "18.5.2-ee"
APP_VERSION = "18.5.0"
CHART_VERSION = "0.82.0"
LEGACY_CHART_VERSION = "0.81.0"
LEGACY_APP_VERSION = "18.4.0"
CHART_DIGEST = "a" * 64
LEGACY_CHART_DIGEST = "b" * 64
CHART_URL = f"https://charts.gitlab.io/gitlab-runner-{CHART_VERSION}.tgz"
LEGACY_CHART_URL = f"https://charts.gitlab.io/gitlab-runner-{LEGACY_CHART_VERSION}.tgz"
IMAGE_DIGEST = "e" * 64
OBSERVED_PROTECTION: Final = "not_protected"
SECRET_METADATA_COLUMNS: Final = (
    "custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,UID:.metadata.uid"
)
INDEX_ENTRIES: Final = (
    {
        "version": CHART_VERSION,
        "appVersion": APP_VERSION,
        "urls": [CHART_URL],
        "digest": "sha256:" + CHART_DIGEST,
    },
    {
        "version": LEGACY_CHART_VERSION,
        "appVersion": LEGACY_APP_VERSION,
        "urls": [LEGACY_CHART_URL],
        "digest": "sha256:" + LEGACY_CHART_DIGEST,
    },
)
MANAGER_IMAGE: Final = (
    f"{HELM_VALUES.UBI_IMAGE_REGISTRY}/{HELM_VALUES.UBI_MANAGER_IMAGE}:v{APP_VERSION}"
)
MANAGER_REPOSITORY: Final = (
    f"{HELM_VALUES.UBI_IMAGE_REGISTRY}/{HELM_VALUES.UBI_MANAGER_IMAGE}"
)
NAMESPACE_PLACEHOLDER: Final = "{namespace}"
EXECUTOR_KUBERNETES: Final = RUNNER_OPTIONS.EXECUTOR_KUBERNETES
EXECUTOR_SHELL: Final = RUNNER_OPTIONS.EXECUTOR_SHELL
KUBERNETES_REQUIRED: Final = HELM_DEPLOY.KUBERNETES_EXECUTOR_REQUIRED
FIELD_RECORDS: Final = "records"
FIELD_STATUS: Final = "status"
FIELD_PLAN_DIGEST: Final = "plan_digest"
FIELD_CALLBACK: Final = "callback"


def _args(
    action: str = "deploy",
    *,
    apply: bool = False,
    executor: str = EXECUTOR_KUBERNETES,
    chart_version: Optional[str] = None,
    namespace: Optional[str] = NAMESPACE,
) -> Namespace:
    """Build complete manager arguments for the kubernetes executor.

    :param action: Manager ``deploy`` or ``delete`` action.
    :param apply: Whether mutation callbacks should be composed.
    :param executor: Runner job executor selected by the caller.
    :param chart_version: Optional exact chart version override.
    :param namespace: Selected namespace, or absence for owned generation.
    :returns: Namespace containing the manager fields used by production code.
    """
    return Namespace(
        action=action,
        apply=apply,
        binding=None,
        chart_version=chart_version,
        confirm_plan="unit-confirmation" if apply else None,
        description=RUNNER_DESCRIPTION,
        dry_run=False,
        executor=executor,
        gitlab=None,
        gitlab_profile=None,
        group=None,
        image=None,
        kind="gitlab_runner_manager",
        tag=list(RUNNER_LABELS),
        namespace=namespace,
        node=None,
        project="team/project",
        project_name=None,
        repository=None,
        revision="b" * 40,
        run_id=RUN_ID,
        runner_id=RUNNER_ID,
        target="/unit/target.toml",
        timeout=30,
        token_file="/unit/runner.token" if action == "deploy" else None,
    )


def _target() -> TARGET.Target:
    """Build one finite target for offline callback composition.

    :returns: GitLab, Kubernetes, and Harbor target identity with no credentials.
    """
    return TARGET.Target(
        github=None,
        gitlab=TARGET.GitLabTarget(
            url="https://gitlab.example.test",
            host="gitlab.example.test",
            project="team/project",
        ),
        kubernetes=TARGET.KubernetesTarget(
            context="unit-context",
            server="https://api.cluster.example.test:6443",
            kubeconfig=None,
        ),
        active_surfaces=(GITLAB_AUTHORITY, KUBERNETES_AUTHORITY, HARBOR_AUTHORITY),
        tested_revision="b" * 40,
        source_file=Path("/unit/target.toml"),
        source_kind="unit-target",
        skill={"digest": "c" * 64},
        harbor=TARGET.HarborTarget(
            url="https://harbor.example.test",
            host="harbor.example.test",
            project="toolbox",
        ),
    )


def _action(
    action: str = "deploy",
    *,
    apply: bool = False,
    chart_version: Optional[str] = None,
    namespace: Optional[str] = NAMESPACE,
) -> ACTION.GitLabRunnerManagerAction:
    """Build a prepared-enough kubernetes manager action for callback tests.

    :param action: Manager ``deploy`` or ``delete`` action.
    :param apply: Whether the public workflow should include mutations.
    :param chart_version: Optional exact chart version override.
    :param namespace: Selected namespace, or absence for owned generation.
    :returns: Manager action with exact target, scope, and token selection.
    """
    selected = ACTION.GitLabRunnerManagerAction(
        _args(action, apply=apply, chart_version=chart_version, namespace=namespace)
    )
    selected.target = _target()
    selected.gitlab_selection = ACTION.select_gitlab_reference(
        TARGET.GitLabOperationTarget(
            selected.target.gitlab, selected.target.source_file
        ),
        project=selected.args.project,
        group=selected.args.group,
    )
    selected.explicit_tags = tuple(selected.args.tag)
    selected.resolved_description = ACTION.ResolvedName(
        selected.args.description, ACTION.NameSource.EXPLICIT
    )
    selected.repository_identity = ACTION.resolve_repository(selected.args.project)
    selected.candidate_scope = RUN_SCOPE.RunScope(
        RUN_ID, namespace or "manager-helm-generated", True
    )
    selected.runner_token = (
        TOKEN.BoundRunnerToken(Path("/unit/runner.token"), TOKEN_DIGEST, PRIVATE_TOKEN)
        if action == "deploy"
        else TOKEN.NoRunnerToken()
    )
    return selected


def _runner_record(*, online: bool, paused: bool = False) -> dict:
    """Build one validated exact runner read-back.

    :param online: Provider online flag and associated status/contact values.
    :param paused: Provider paused flag observed after a pause request.
    :returns: Public runner identity accepted by manager evidence helpers.
    """
    return {
        "id": RUNNER_ID,
        "description": RUNNER_DESCRIPTION,
        "runner_type": "project_type",
        "tag_list": list(RUNNER_LABELS),
        "status": "online" if online else "offline",
        "access_level": OBSERVED_PROTECTION,
        "online": online,
        "paused": paused,
        "contacted_at": "2026-11-25T00:00:00Z" if online else None,
        "version": "18.5.0",
        "platform": "linux",
        "architecture": "amd64",
    }


class FakeGitLabAPI:
    """Answer the exact GitLab reads the Helm callbacks issue."""

    def __init__(self, cluster: HelmCluster) -> None:
        """Retain the cluster and start with no runner mutation.

        :param cluster: In-memory cluster owning the running-jobs sequence.
        """
        self.cluster = cluster
        self.puts: list[tuple[str, dict]] = []
        self.paused = False

    def get_json(self, session: object, endpoint: str, **kwargs: object) -> object:
        """Return one bounded provider response for a known read.

        :param session: Bound private session identity.
        :param endpoint: Validated relative provider endpoint.
        :param kwargs: Unused shared client options.
        :returns: Decoded version or runner job collection.
        :raises AssertionError: An unexpected endpoint crosses the boundary.
        """
        assert session is not None
        if endpoint == "version":
            return {"version": GITLAB_VERSION}
        if endpoint == f"runners/{RUNNER_ID}/jobs?status=running":
            if self.cluster.job_pages:
                return list(self.cluster.job_pages.pop(0))
            return []
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    def put_json(self, session: object, endpoint: str, body: dict) -> dict:
        """Record one provider pause and report the accepted empty body.

        :param session: Bound private session identity.
        :param endpoint: Validated relative provider endpoint.
        :param body: Exact JSON fields accepted by the provider.
        :returns: An empty decoded response object.
        :raises AssertionError: An unexpected endpoint crosses the boundary.
        """
        assert session is not None
        assert endpoint == f"runners/{RUNNER_ID}"
        assert body == {"active": False}
        self.puts.append((endpoint, body))
        self.paused = True
        return {}


class FakeHelmClient:
    """Model the native Helm boundary against the in-memory cluster."""

    def __init__(self, cluster: HelmCluster, storage: dict, manifest: str) -> None:
        """Retain the cluster, storage projection, and pinned render.

        :param cluster: In-memory cluster receiving installed objects.
        :param storage: Shared Helm release storage identity projection.
        :param manifest: Pinned chart render with a namespace placeholder.
        """
        self.cluster = cluster
        self.storage = storage
        self.manifest = manifest
        self.releases: dict = {}
        self.installed: list[tuple[str, str]] = []
        self.uninstalled: list[tuple[str, str]] = []
        self.values: Optional[dict] = None
        self.rechecks = 0

    def status(self, release: str, namespace: str, *, timeout: float = 0) -> object:
        """Return the stored release, or None on native release absence."""
        return self.releases.get((release, namespace))

    def render(
        self,
        release: str,
        namespace: str,
        chart: object,
        values: dict,
        *,
        timeout: float = 0,
    ) -> str:
        """Return the pinned render with the exact selected namespace."""
        self.values = deepcopy(values)
        return self.manifest.replace(NAMESPACE_PLACEHOLDER, namespace)

    def install_fresh(
        self,
        release: str,
        namespace: str,
        chart: object,
        values: dict,
        *,
        verify_absent: object,
        timeout: float = 0,
    ) -> object:
        """Verify absence, create the rendered objects, and record the release."""
        assert callable(verify_absent)
        manifest = self.render(release, namespace, chart, values, timeout=timeout)
        verify_absent(manifest)
        self.rechecks += 1
        for document in yaml.safe_load_all(manifest):
            if document is None:
                continue
            document = deepcopy(document)
            metadata = document.setdefault("metadata", {})
            metadata.setdefault("labels", {})["app.kubernetes.io/managed-by"] = "Helm"
            annotations = metadata.setdefault("annotations", {})
            annotations["meta.helm.sh/release-name"] = release
            annotations["meta.helm.sh/release-namespace"] = namespace
            self.cluster.apply(document)
        release_object = HELM_NATIVE.HelmRelease(release, namespace, 1, "deployed")
        self.releases[(release, namespace)] = release_object
        self.storage[(release, namespace)] = HELM_STORAGE.HelmStorageIdentity(
            f"sh.helm.release.v1.{release}.v1", f"storage-{release}-uid"
        )
        self.installed.append((release, namespace))
        return release_object

    def uninstall(self, release: str, namespace: str, *, timeout: float = 0) -> bool:
        """Remove the exact release objects and storage, or report absence."""
        if (release, namespace) not in self.releases:
            return False
        for key, document in list(self.cluster.objects.items()):
            metadata = document.get("metadata", {})
            annotations = metadata.get("annotations", {})
            if (
                metadata.get("namespace") == namespace
                and annotations.get("meta.helm.sh/release-name") == release
            ):
                del self.cluster.objects[key]
        del self.releases[(release, namespace)]
        self.storage.pop((release, namespace), None)
        self.uninstalled.append((release, namespace))
        return True

    def values_match(
        self, release: object, values: dict, *, timeout: float = 0
    ) -> bool:
        """Report that the release retains its installation values."""
        return True


def _secret_projection(document: dict) -> str:
    """Render the exact go-template projection the owner verifies.

    :param document: Stored native Secret document.
    :returns: Newline-separated projection lines without credential values.
    """
    metadata = document["metadata"]
    labels = metadata.get("labels", {})
    data = document.get("data", {})
    key_line = next(iter(data)) if isinstance(data, Mapping) and len(data) == 1 else ""
    lines = [
        document.get("apiVersion", "v1"),
        document["kind"],
        metadata.get("namespace", ""),
        metadata["name"],
        metadata["uid"],
        labels.get(RESOURCES.MANAGER_LABEL, ""),
        labels.get(RESOURCES.OPERATION_LABEL, ""),
        labels.get(RUN_SCOPE.RUN_ID_LABEL, ""),
        document.get("type", ""),
        "true" if document.get("immutable") is True else "<unset>",
        "live",
        key_line,
    ]
    return "\n".join(lines) + "\n"


class HelmCluster(MemoryCluster):
    """Extend the in-memory transport with Helm manager workload behavior."""

    def __init__(self) -> None:
        """Initialize an empty cluster with an offline idle runner."""
        super().__init__()
        self.target = replace(_target(), skill=None)
        self.runner_online = False
        self.job_pages: list[list[dict]] = []
        self.gitlab_api = FakeGitLabAPI(self)

    def read(self, kind: str, name: str, namespace: str) -> Optional[dict]:
        """Read one object, accepting dotted native resource names."""
        base = kind.split(".", 1)[0] if "." in kind else kind
        return super().read(base, name, namespace)

    def command(
        self,
        *args: str,
        openshift: bool = False,
        input_data: Optional[bytes] = None,
        timeout: Optional[float] = None,
    ) -> RUNTIME.CommandResult:
        """Answer the exact Secret create, read and remnant list projections."""
        args = tuple(args)
        if args[:1] == ("create",):
            assert input_data is not None
            document = json.loads(input_data.decode("utf-8"))
            document = deepcopy(document)
            metadata = document.setdefault("metadata", {})
            metadata["uid"] = f"uid-secret-{len(self.applied)}"
            document["data"] = dict(document.pop("stringData", {}))
            key = ("secret", metadata.get("namespace", ""), metadata["name"])
            self.objects[key] = document
            self.applied.append(key)
            return RUNTIME.CommandResult(args, 0, _secret_projection(document), "")
        if args[:2] == ("get", "secrets"):
            selector = dict(
                item.split("=", 1) for item in args[args.index("-l") + 1].split(",")
            )
            rows = [
                f"{namespace} {name} {document['metadata']['uid']}\n"
                for (kind, namespace, name), document in self.objects.items()
                if kind == "secret"
                and all(
                    document["metadata"].get("labels", {}).get(key) == value
                    for key, value in selector.items()
                )
            ]
            return RUNTIME.CommandResult(args, 0, "".join(rows), "")
        if (
            args[:1] == ("-n",)
            and args[2:3] == ("get",)
            and args[3].startswith("secret/")
        ):
            name = args[3][len("secret/") :]
            document = self.read("secret", name, args[1])
            if document is None:
                return RUNTIME.CommandResult(args, 0, "", "")
            return RUNTIME.CommandResult(args, 0, _secret_projection(document), "")
        raise AssertionError(f"unexpected command: {args}")

    def json(self, *args: str) -> dict:
        """List owned objects or synthesize the running manager Pod."""
        if args[0] == "get":
            return super().json(*args)
        assert args[:4] == ("-n", args[1], "get", "pods")
        namespace = args[1]
        selector = args[args.index("-l") + 1]
        labels = dict(item.split("=", 1) for item in selector.split(","))
        workload = self.read("deployment", labels["app"], namespace)
        assert workload is not None
        pod = deepcopy(workload["spec"]["template"])
        pod["metadata"]["name"] = labels["app"] + "-pod"
        pod["metadata"]["uid"] = "manager-pod-uid"
        pod["status"] = {
            "containerStatuses": [
                {
                    "name": "manager",
                    "image": MANAGER_IMAGE,
                    "imageID": (
                        f"docker-pullable://{MANAGER_IMAGE}@sha256:{IMAGE_DIGEST}"
                    ),
                    "state": {"running": {"startedAt": "2026-11-25T00:00:00Z"}},
                }
            ]
        }
        return {"items": [pod]}

    def wait_rollout(self, kind: str, name: str, namespace: str) -> None:
        """Require the manager Deployment and mark the runner contactable."""
        assert kind == "deployment"
        assert self.read("deployment", name, namespace) is not None
        self.runner_online = True


def _bind_gitlab(
    action: ACTION.GitLabRunnerManagerAction, cluster: HelmCluster
) -> ACTION.GitLabRunnerManagerState:
    """Bind finite GitLab service and scope state without provider access.

    :param action: Manager action whose state receives exact provider identity.
    :param cluster: In-memory cluster bound as the Kubernetes transport.
    :returns: The action's shared manager state.
    """
    state = action.context
    session = SimpleNamespace(
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/project",
        credential_digest="sha256:" + "d" * 64,
    )
    state.gitlab = SimpleNamespace(
        api=cluster.gitlab_api, session=session, target_id=42
    )
    state.configured_session = session
    state.kubernetes = cluster
    state.harbor = SimpleNamespace(dockerconfig=b'{"auths":{}}')
    state.args = action.args
    state.run_scope = action.candidate_scope
    return state


def _bind_state_gitlab(state: ACTION.GitLabRunnerManagerState) -> None:
    """Bind one finite GitLab service to a bare manager state.

    :param state: Shared state whose kubernetes transport is already bound.
    """
    session = SimpleNamespace(
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/project",
        credential_digest="sha256:" + "d" * 64,
    )
    state.gitlab = SimpleNamespace(
        api=state.kubernetes.gitlab_api, session=session, target_id=42
    )
    state.configured_session = session


def _manifest(manager: str) -> str:
    """Render the pinned chart manifest with one namespace placeholder.

    :param manager: Exact manager workload identity used for every object.
    :returns: Multi-document YAML accepted by the strict render inventory.
    """
    return (
        "---\n"
        "apiVersion: v1\n"
        "kind: ServiceAccount\n"
        "metadata:\n"
        f"  name: {manager}\n"
        f"  namespace: {NAMESPACE_PLACEHOLDER}\n"
        "---\n"
        "apiVersion: rbac.authorization.k8s.io/v1\n"
        "kind: Role\n"
        "metadata:\n"
        f"  name: {manager}\n"
        f"  namespace: {NAMESPACE_PLACEHOLDER}\n"
        "---\n"
        "apiVersion: rbac.authorization.k8s.io/v1\n"
        "kind: RoleBinding\n"
        "metadata:\n"
        f"  name: {manager}\n"
        f"  namespace: {NAMESPACE_PLACEHOLDER}\n"
        "---\n"
        "apiVersion: v1\n"
        "kind: ConfigMap\n"
        "metadata:\n"
        f"  name: {manager}\n"
        f"  namespace: {NAMESPACE_PLACEHOLDER}\n"
        "---\n"
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        f"  name: {manager}\n"
        f"  namespace: {NAMESPACE_PLACEHOLDER}\n"
        "spec:\n"
        "  template:\n"
        "    metadata:\n"
        "      labels:\n"
        f"        app: {manager}\n"
        "    spec:\n"
        "      containers:\n"
        "        - name: manager\n"
        f"          image: {MANAGER_IMAGE}\n"
    )


def _patch_chart_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind the public chart index to the exact pinned unit entries.

    :param monkeypatch: Pytest patch helper for the network boundary.
    """
    monkeypatch.setattr(
        HELM_DEPLOY,
        "read_chart_index",
        lambda repository, *, timeout: INDEX_ENTRIES,
    )


def _helm_harness(
    monkeypatch: pytest.MonkeyPatch,
    action: ACTION.GitLabRunnerManagerAction,
    cluster: HelmCluster,
) -> FakeHelmClient:
    """Bind fake native Helm boundaries below the real callback logic.

    :param monkeypatch: Pytest patch helper for the native boundaries.
    :param action: Manager action whose candidate scope selects the workload.
    :param cluster: In-memory cluster receiving installed objects.
    :returns: The fake Helm client recording every native call.
    """
    manager = action.workload().runner(action.candidate_scope).name
    storage: dict = {}
    helm = FakeHelmClient(cluster, storage, _manifest(manager))

    def make_helm(kubernetes: object) -> FakeHelmClient:
        assert kubernetes is cluster
        return helm

    def fake_storage(kubernetes: object, release: object) -> object:
        assert kubernetes is cluster
        return storage.get((release.name, release.namespace))

    @contextmanager
    def fake_pull(url: str, digest: str, *, timeout: float) -> Iterator[object]:
        assert url in {CHART_URL, LEGACY_CHART_URL}
        assert digest in {CHART_DIGEST, LEGACY_CHART_DIGEST}
        yield HELM_NATIVE.VerifiedChart(Path("/unit/chart.tgz"), "a" * 64)

    monkeypatch.setattr(HELM_DEPLOY, "HelmClient", make_helm)
    monkeypatch.setattr(HELM_DEPLOY, "read_helm_storage", fake_storage)
    monkeypatch.setattr(HELM_DEPLOY, "pull_verified_chart", fake_pull)
    _patch_chart_index(monkeypatch)
    return helm


def _select_chart(
    action: ACTION.GitLabRunnerManagerAction,
    state: ACTION.GitLabRunnerManagerState,
) -> dict:
    """Run the plan-time chart callback against the fake native boundaries.

    :param action: Prepared kubernetes manager deploy action.
    :param state: Shared manager state with a bound GitLab service.
    :returns: Verified chart selection evidence.
    """
    callback = HELM_DEPLOY.SelectManagerChart(action)
    callback.bind(state)
    return callback.run()


def _deploy_callback(
    action: ACTION.GitLabRunnerManagerAction,
    state: ACTION.GitLabRunnerManagerState,
) -> HELM_DEPLOY.DeployKubernetesManagerChart:
    """Construct the deploy callback against shared manager state.

    :param action: Prepared kubernetes manager deploy action.
    :param state: Shared manager state with bound providers and scope.
    :returns: Bound deploy callback ready to run.
    """
    callback = HELM_DEPLOY.DeployKubernetesManagerChart(action.workload())
    callback.bind(state)
    return callback


def test_select_manager_chart_binds_highest_matching_chart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve the highest chart matching the instance MAJOR.MINOR version."""
    action = _action()
    cluster = HelmCluster()
    state = _bind_gitlab(action, cluster)
    _patch_chart_index(monkeypatch)

    result = _select_chart(action, state)

    assert result == {
        "verified": True,
        "executor": EXECUTOR_KUBERNETES,
        "chart": "gitlab-runner",
        "chart_version": CHART_VERSION,
        "app_version": APP_VERSION,
        "gitlab_version": GITLAB_VERSION,
        "requested": None,
        "digest": CHART_DIGEST,
    }
    assert action.chart_selection is not None
    assert action.chart_selection.selection.requested is None
    assert PRIVATE_TOKEN not in json.dumps(result)


def test_select_manager_chart_override_wins_and_binds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bind the caller override even when a higher chart matches the instance."""
    action = _action(chart_version=LEGACY_CHART_VERSION)
    cluster = HelmCluster()
    state = _bind_gitlab(action, cluster)
    _patch_chart_index(monkeypatch)

    result = _select_chart(action, state)

    assert result["chart_version"] == LEGACY_CHART_VERSION
    assert result["app_version"] == LEGACY_APP_VERSION
    assert result["requested"] == LEGACY_CHART_VERSION
    assert result["digest"] == LEGACY_CHART_DIGEST
    assert action.chart_selection is not None
    assert action.chart_selection.url == LEGACY_CHART_URL


@pytest.mark.parametrize(
    ("chart_version", "expected"),
    (
        (None, CHART_VERSION),
        (LEGACY_CHART_VERSION, LEGACY_CHART_VERSION),
    ),
    ids=("derived", "override"),
)
def test_fingerprint_binds_the_resolved_chart_selection(
    monkeypatch: pytest.MonkeyPatch,
    chart_version: Optional[str],
    expected: str,
) -> None:
    """Include the resolved chart identity in the confirmation fingerprint."""
    action = _action(chart_version=chart_version)
    cluster = HelmCluster()
    state = _bind_gitlab(action, cluster)
    _helm_harness(monkeypatch, action, cluster)
    _select_chart(action, state)
    captured: list[Mapping[str, object]] = []

    def capture(document: Mapping[str, object]) -> str:
        captured.append(document)
        return "sha256:captured-plan"

    monkeypatch.setattr(ACTION, "plan_digest", capture)

    assert action.fingerprint() == "sha256:captured-plan"
    assert len(captured) == 1
    chart = captured[0]["chart"]
    assert chart is not None
    assert chart["chart"] == "gitlab-runner"
    assert chart["chart_version"] == expected
    assert chart["requested"] == chart_version
    assert chart["digest"] in {CHART_DIGEST, LEGACY_CHART_DIGEST}
    assert PRIVATE_TOKEN not in json.dumps(captured[0])


def test_fingerprint_omits_chart_and_privilege_for_kubernetes_deploy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep SCC authorization and chart binding out of the kubernetes plan."""
    action = _action()
    state = _bind_gitlab(action, HelmCluster())
    state.gitlab_runner = _runner_record(online=False)
    captured: list[Mapping[str, object]] = []
    monkeypatch.setattr(
        ACTION,
        "plan_digest",
        lambda document: captured.append(document) or "sha256:captured-plan",
    )

    action.fingerprint()

    assert captured[0]["chart"] is None
    assert captured[0]["authorization"] is None
    assert captured[0]["workload"]["security_constraint"] is None


def test_deploy_installs_release_with_run_owned_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Install the pinned chart and read back release, Pods and image digest."""
    action = _action()
    cluster = HelmCluster()
    state = _bind_gitlab(action, cluster)
    helm = _helm_harness(monkeypatch, action, cluster)
    _select_chart(action, state)
    scope = state.run_scope
    manager = action.workload().runner(scope).name
    token_name = HELM_DEPLOY.runner_token_secret_name(scope, manager)
    pull_name = manager + K8S_RUNNER.REGISTRY_SECRET_SUFFIX

    callback = _deploy_callback(action, state)
    result = callback.run()

    assert result["action"] == STATUS.APPLIED
    assert result["verified"] is True
    assert result["executor"] == EXECUTOR_KUBERNETES
    assert result["namespace"] == NAMESPACE
    assert result["name"] == manager
    assert result["chart"]["chart_version"] == CHART_VERSION
    assert result["release"] == {
        "name": manager,
        "namespace": NAMESPACE,
        "revision": 1,
        "status": "deployed",
    }
    assert result["storage"]["uid"] == f"storage-{manager}-uid"
    assert result["pods"] == [
        {
            "name": manager + "-pod",
            "uid": "manager-pod-uid",
            "image_id": f"docker-pullable://{MANAGER_IMAGE}@sha256:{IMAGE_DIGEST}",
        }
    ]
    assert result["secrets"] == {"token": result["secrets"]["token"], "pull": pull_name}
    assert result["secrets"]["token"]
    assert {row["kind"] for row in result["resources"]} == {
        "Deployment",
        "ConfigMap",
        "ServiceAccount",
        "Role",
        "RoleBinding",
        "Secret",
    }
    assert helm.installed == [(manager, NAMESPACE)]
    assert helm.rechecks == 1
    values = helm.values
    assert values is not None
    assert values["fullnameOverride"] == manager
    assert values["gitlabUrl"] == "https://gitlab.example.test"
    assert values["concurrent"] == 1
    assert values["rbac"]["clusterWideAccess"] is False
    assert values["runners"]["secret"] == token_name
    assert "jobNamespace" not in values["runners"]
    assert pull_name in values["runners"]["config"]
    assert values["image"]["tag"] == f"v{APP_VERSION}"
    stored_token = cluster.read("secret", token_name, NAMESPACE)
    assert stored_token is not None
    assert stored_token["immutable"] is True
    assert list(stored_token["data"]) == [HELM_VALUES.RUNNER_TOKEN_SECRET_KEY]
    stored_pull = cluster.read("secret", pull_name, NAMESPACE)
    assert stored_pull is not None
    pull_labels = stored_pull["metadata"]["labels"]
    assert pull_labels[RESOURCES.MANAGER_LABEL] == RESOURCES.MANAGER_VALUE
    assert pull_labels[RESOURCES.OPERATION_LABEL] == RUN_ID
    assert pull_labels[RUN_SCOPE.RUN_ID_LABEL] == RUN_ID
    deployment = cluster.objects[("deployment", NAMESPACE, manager)]
    assert deployment["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "Helm"
    assert deployment["metadata"]["annotations"]["meta.helm.sh/release-name"] == manager
    assert PRIVATE_TOKEN not in json.dumps(result)
    assert state.deployment_result == result


def test_deploy_repeat_is_no_op_without_new_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second confirmed deploy keeps the release and writes nothing new."""
    action = _action()
    cluster = HelmCluster()
    state = _bind_gitlab(action, cluster)
    helm = _helm_harness(monkeypatch, action, cluster)
    _select_chart(action, state)
    callback = _deploy_callback(action, state)
    first = callback.run()
    assert first["action"] == STATUS.APPLIED
    before = deepcopy(cluster.objects)
    applied = list(cluster.applied)

    repeat = callback.run()

    assert repeat["action"] == STATUS.NO_OP
    assert repeat["name"] == first["name"]
    assert repeat["release"]["revision"] == 1
    assert repeat["secrets"]["token"] == first["secrets"]["token"]
    assert repeat["storage"] == first["storage"]
    assert helm.installed == [(first["name"], NAMESPACE)]
    assert cluster.objects == before
    assert cluster.applied == applied


def test_deploy_blocks_when_release_exists_without_owned_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse a no-op when run-owned Secrets disappeared under a live release."""
    action = _action()
    cluster = HelmCluster()
    state = _bind_gitlab(action, cluster)
    _helm_harness(monkeypatch, action, cluster)
    _select_chart(action, state)
    callback = _deploy_callback(action, state)
    callback.run()
    manager = action.workload().runner(state.run_scope).name
    cluster.objects.pop(
        ("secret", NAMESPACE, manager + K8S_RUNNER.REGISTRY_SECRET_SUFFIX)
    )

    with pytest.raises(
        KUBERNETES.KubernetesError, match="runner_manager_release_incomplete"
    ):
        callback.run()


def test_deploy_refuses_existing_token_secret_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Block install when the exact token Secret identity is already present."""
    action = _action()
    cluster = HelmCluster()
    state = _bind_gitlab(action, cluster)
    _helm_harness(monkeypatch, action, cluster)
    _select_chart(action, state)
    manager = action.workload().runner(state.run_scope).name
    owner = HELM_DEPLOY.RunnerTokenSecret(cluster, state.run_scope, manager)
    owner.create(PRIVATE_TOKEN)
    callback = _deploy_callback(action, state)

    with pytest.raises(
        KUBERNETES.KubernetesError, match="runner_manager_secret_already_present"
    ):
        callback.run()

    assert cluster.gitlab_api.puts == []


@pytest.mark.parametrize(
    "callback_type",
    (
        HELM_DEPLOY.SelectManagerChart,
        HELM_DEPLOY.DeployKubernetesManagerChart,
        HELM_DEPLOY.UninstallKubernetesManagerChart,
    ),
    ids=("select", "deploy", "uninstall"),
)
def test_helm_callbacks_reject_the_shell_executor(
    callback_type: type,
) -> None:
    """Refuse Helm callbacks composed for the shell executor."""
    action = _action()
    action.args.executor = EXECUTOR_SHELL
    state = _bind_gitlab(action, HelmCluster())
    if callback_type is HELM_DEPLOY.SelectManagerChart:
        callback = callback_type(action)
    else:
        callback = callback_type(action.workload())
    callback.bind(state)

    with pytest.raises(CALLBACKS.ActionError, match=KUBERNETES_REQUIRED):
        callback.run()


def test_deploy_requires_plan_time_chart_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse apply when confirmation did not bind a chart selection."""
    action = _action()
    cluster = HelmCluster()
    state = _bind_gitlab(action, cluster)
    _helm_harness(monkeypatch, action, cluster)
    callback = _deploy_callback(action, state)

    with pytest.raises(CALLBACKS.ActionError, match="runner_chart_selection_required"):
        callback.run()


def test_deploy_compensates_installed_release_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uninstall the release and remove Secrets when a later callback fails."""
    action = _action()
    cluster = HelmCluster()
    state = _bind_gitlab(action, cluster)
    helm = _helm_harness(monkeypatch, action, cluster)
    _select_chart(action, state)
    callback = _deploy_callback(action, state)
    callback.run()
    assert cluster.objects
    state.failed = True

    callback.cleanup()

    manager = action.workload().runner(state.run_scope).name
    assert helm.uninstalled == [(manager, NAMESPACE)]
    assert helm.storage == {}
    assert cluster.objects == {}


def _uninstall_callback(
    action: ACTION.GitLabRunnerManagerAction,
    state: ACTION.GitLabRunnerManagerState,
) -> HELM_DEPLOY.UninstallKubernetesManagerChart:
    """Construct the uninstall callback against shared manager state.

    :param action: Prepared kubernetes manager delete action.
    :param state: Shared manager state with bound providers and scope.
    :returns: Bound uninstall callback ready to run.
    """
    callback = HELM_DEPLOY.UninstallKubernetesManagerChart(action.workload())
    callback.bind(state)
    return callback


def _bind_auth_harness(monkeypatch: pytest.MonkeyPatch, cluster: HelmCluster) -> None:
    """Bind finite provider configuration for executor composition binding.

    :param monkeypatch: Pytest patch helper for the authentication callbacks.
    :param cluster: In-memory cluster selected as the Kubernetes target.
    """

    def k8s_config(callback: object) -> dict:
        callback.state.kubernetes_target = cluster.target
        return {}

    def k8s_run(callback: object) -> dict:
        callback.state.kubernetes = cluster
        return {}

    def gitlab_config(callback: object) -> dict:
        callback.state.configured_session = object()
        return {}

    def gitlab_run(callback: object) -> dict:
        callback.state.gitlab = SimpleNamespace(
            api=object(), session=object(), target_id=42
        )
        return {}

    def harbor_config(callback: object) -> dict:
        callback.state.harbor_target = cluster.target.harbor
        callback.state.harbor_credential = object()
        return {}

    def harbor_run(callback: object) -> dict:
        callback.state.harbor = SimpleNamespace(dockerconfig=b"{}")
        return {}

    monkeypatch.setattr(KUBERNETES.K8SAuth, "read_config", k8s_config)
    monkeypatch.setattr(KUBERNETES.K8SAuth, "run", k8s_run)
    monkeypatch.setattr(CALLBACKS.GitLabManagerAuth, "read_config", gitlab_config)
    monkeypatch.setattr(CALLBACKS.GitLabManagerAuth, "run", gitlab_run)
    monkeypatch.setattr(SCRIPT.HarborAuth, "read_config", harbor_config)
    monkeypatch.setattr(SCRIPT.HarborAuth, "run", harbor_run)


def _kubernetes_main_harness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> HelmCluster:
    """Bind every native boundary below the real command lifecycle.

    :param monkeypatch: Pytest patch helper for the native boundaries.
    :param tmp_path: Isolated runner-token and lock directory for the harness.
    :returns: In-memory cluster recording every provider mutation.
    """
    cluster = HelmCluster()
    cluster.token_path = tmp_path / "runner.token"
    cluster.token_path.write_text(PRIVATE_TOKEN, encoding="utf-8")
    cluster.token_path.chmod(0o600)
    session = SimpleNamespace(
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/project",
        credential_digest="sha256:" + "d" * 64,
    )
    monkeypatch.setattr(ACTION, "resolve_target", lambda *_a, **_k: cluster.target)
    monkeypatch.setattr(ACTION, "bind_sources", lambda target, **_k: target)

    def k8s_config(callback: object) -> dict:
        callback.state.kubernetes_target = cluster.target
        return {}

    def k8s_run(callback: object) -> dict:
        callback.state.kubernetes = cluster
        callback.state.access_evidence[KUBERNETES_AUTHORITY] = {"identity": "unit-user"}
        return {"identity": "unit-user"}

    def gitlab_config(callback: object) -> dict:
        callback.state.configured_session = session
        return {}

    def gitlab_run(callback: object) -> dict:
        callback.state.gitlab = SimpleNamespace(
            api=cluster.gitlab_api, session=session, target_id=42
        )
        callback.state.access_evidence[GITLAB_AUTHORITY] = {"identity": "unit-user"}
        return {"identity": "unit-user"}

    def harbor_config(callback: object) -> dict:
        callback.state.harbor_target = cluster.target.harbor
        callback.state.harbor_credential = object()
        return {}

    def harbor_run(callback: object) -> dict:
        callback.state.harbor = SimpleNamespace(dockerconfig=b'{"auths":{}}')
        callback.state.access_evidence[HARBOR_AUTHORITY] = {"identity": "unit-user"}
        return {"identity": "unit-user"}

    def runner_read(
        api: object,
        bound: object,
        target_kind: str,
        target_id: int,
        runner_id: int,
        *,
        description: str,
        expected_tags: Sequence[str],
        expected_protection: Optional[str] = None,
    ) -> dict:
        assert (bound, target_kind, target_id, runner_id) == (
            session,
            "project",
            42,
            RUNNER_ID,
        )
        return _runner_record(online=cluster.runner_online)

    def helm_runner_read(*args: object, **kwargs: object) -> dict:
        return _runner_record(
            online=cluster.runner_online, paused=cluster.gitlab_api.paused
        )

    @contextmanager
    def guard(origin: str, runner_id: int) -> Iterator[None]:
        with GITLAB_API.runner_update_guard(origin, runner_id):
            yield

    monkeypatch.setattr(KUBERNETES.K8SAuth, "read_config", k8s_config)
    monkeypatch.setattr(KUBERNETES.K8SAuth, "run", k8s_run)
    monkeypatch.setattr(CALLBACKS.GitLabManagerAuth, "read_config", gitlab_config)
    monkeypatch.setattr(CALLBACKS.GitLabManagerAuth, "run", gitlab_run)
    monkeypatch.setattr(SCRIPT.HarborAuth, "read_config", harbor_config)
    monkeypatch.setattr(SCRIPT.HarborAuth, "run", harbor_run)
    monkeypatch.setattr(CALLBACKS, "read_runner_record", runner_read)
    monkeypatch.setattr(HELM_DEPLOY, "read_runner_record", helm_runner_read)
    monkeypatch.setattr(CALLBACKS, "runner_update_guard", guard, raising=False)
    monkeypatch.setattr(GITLAB_API, "_lock_root", lambda: tmp_path / "locks")

    consumer = ACTION.resolve_name(
        None,
        ACTION.resolve_repository("team/project"),
        argument="--project-name",
        pending=True,
    )
    manager = RUN_SCOPE.RunScope(RUN_ID, "unit", True).bounded_name(
        ACTION.MANAGER_ROLE,
        limit=ACTION.RUNNER_NAME_LIMIT,
        prefix=consumer.value or None,
    )
    storage: dict = {}
    holders: dict = {}

    def make_helm(kubernetes: object) -> FakeHelmClient:
        assert kubernetes is cluster
        if "helm" not in holders:
            holders["helm"] = FakeHelmClient(cluster, storage, _manifest(manager))
        return holders["helm"]

    def fake_storage(kubernetes: object, release: object) -> object:
        assert kubernetes is cluster
        return storage.get((release.name, release.namespace))

    @contextmanager
    def fake_pull(url: str, digest: str, *, timeout: float) -> Iterator[object]:
        yield HELM_NATIVE.VerifiedChart(Path("/unit/chart.tgz"), "a" * 64)

    monkeypatch.setattr(HELM_DEPLOY, "HelmClient", make_helm)
    monkeypatch.setattr(HELM_DEPLOY, "read_helm_storage", fake_storage)
    monkeypatch.setattr(HELM_DEPLOY, "pull_verified_chart", fake_pull)
    _patch_chart_index(monkeypatch)
    return cluster


def _kubernetes_cli_arguments(
    cluster: HelmCluster, action: str = "deploy", flags: Sequence[str] = ()
) -> list[str]:
    """Build public arguments without bypassing command preparation.

    :param cluster: Harness cluster owning the private token file.
    :param action: Manager ``deploy`` or ``delete`` action.
    :param flags: Additional caller-selected flags.
    :returns: Argument vector accepted by the catalogue-backed parser.
    """
    args = [
        action,
        "--runner-id",
        str(RUNNER_ID),
        "--description",
        RUNNER_DESCRIPTION,
        "--project",
        "team/project",
        "--binding",
        "/unit/target.toml",
        "--json",
        "--tag",
        RUNNER_LABELS[0],
        "--tag",
        RUNNER_LABELS[1],
        "--timeout",
        "30",
        "--executor",
        "kubernetes",
        "--run-id",
        RUN_ID,
    ]
    if action == "deploy":
        args.extend(("--token-file", str(cluster.token_path)))
    args.extend(flags)
    return args


def _kubernetes_cli_report(
    cluster: HelmCluster,
    capsys: pytest.CaptureFixture[str],
    action: str = "deploy",
    flags: Sequence[str] = (),
) -> tuple[int, dict]:
    """Decode and validate the actual command report using the result schema.

    :param cluster: Harness cluster passed to the public command.
    :param capsys: Capture the real command's machine-readable report.
    :param action: Manager ``deploy`` or ``delete`` action.
    :param flags: Additional caller-selected flags.
    :returns: Exit code and schema-validated result envelope.
    """
    code = SCRIPT.main(_kubernetes_cli_arguments(cluster, action, flags))
    captured = capsys.readouterr()
    assert PRIVATE_TOKEN not in captured.out + captured.err
    result = json.loads(captured.out)
    SCHEMA.validate_specification(result, "results/toolbox-workflow.schema.json")
    return code, result


def test_manager_main_kubernetes_deploy_and_delete_round_trip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Plan, apply, and delete one kubernetes manager through the real command."""
    cluster = _kubernetes_main_harness(monkeypatch, tmp_path)
    code, plan = _kubernetes_cli_report(cluster, capsys)
    assert (code, plan[FIELD_STATUS]) == (
        STATUS.exit_code(STATUS.PLANNED),
        STATUS.PLANNED,
    )
    chart_plan = next(
        item
        for item in plan[FIELD_RECORDS]
        if item[FIELD_CALLBACK] == "SelectManagerChart"
    )
    assert chart_plan["chart_version"] == CHART_VERSION

    code, deployed = _kubernetes_cli_report(
        cluster,
        capsys,
        flags=("--apply", "--confirm-plan", plan[FIELD_PLAN_DIGEST]),
    )
    assert (code, deployed[FIELD_STATUS]) == (
        STATUS.exit_code(STATUS.PASS),
        STATUS.PASS,
    )
    deploy_record = next(
        item
        for item in deployed[FIELD_RECORDS]
        if item[FIELD_CALLBACK] == "DeployKubernetesManagerChart"
    )
    assert deploy_record["action"] == STATUS.APPLIED
    namespace = deploy_record["namespace"]
    manager = deploy_record["name"]
    assert ("namespace", "", namespace) in cluster.objects
    assert ("deployment", namespace, manager) in cluster.objects

    code, delete_plan = _kubernetes_cli_report(cluster, capsys, action="delete")
    assert (code, delete_plan[FIELD_STATUS]) == (
        STATUS.exit_code(STATUS.PLANNED),
        STATUS.PLANNED,
    )
    code, removed = _kubernetes_cli_report(
        cluster,
        capsys,
        action="delete",
        flags=("--apply", "--confirm-plan", delete_plan[FIELD_PLAN_DIGEST]),
    )
    assert (code, removed[FIELD_STATUS]) == (
        STATUS.exit_code(STATUS.PASS),
        STATUS.PASS,
    )
    uninstall_record = next(
        item
        for item in removed[FIELD_RECORDS]
        if item[FIELD_CALLBACK] == "UninstallKubernetesManagerChart"
    )
    assert uninstall_record["release_removed"] is True
    assert uninstall_record["runner_paused"] is True
    preserved = next(
        item
        for item in removed[FIELD_RECORDS]
        if item[FIELD_CALLBACK] == "VerifyGitLabRunnerPreserved"
    )
    assert preserved["runner_preserved"] is True
    assert preserved["current_status"]["id"] == RUNNER_ID
    assert cluster.gitlab_api.puts == [(f"runners/{RUNNER_ID}", {"active": False})]
    assert cluster.objects == {}
    assert removed["cleanup"] == {"status": STATUS.PASS, "failures": []}


def test_delete_pauses_waits_uninstalls_and_verifies_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pause, drain, uninstall and remove the release with owned Secrets."""
    deploy_action = _action()
    cluster = HelmCluster()
    deploy_state = _bind_gitlab(deploy_action, cluster)
    helm = _helm_harness(monkeypatch, deploy_action, cluster)
    _select_chart(deploy_action, deploy_state)
    deploy = _deploy_callback(deploy_action, deploy_state)
    deploy.run()
    manager = deploy_action.workload().runner(deploy_state.run_scope).name

    delete_action = _action("delete")
    delete_state = delete_action.context
    delete_state.args = delete_action.args
    scope = RUN_SCOPE.RunScope(RUN_ID, NAMESPACE, True)
    delete_state.run_scope = scope
    delete_state.found_scopes = (scope,)
    delete_state.kubernetes = cluster
    delete_state.gitlab = deploy_state.gitlab
    delete_state.configured_session = deploy_state.configured_session

    def paused_record(*args: object, **kwargs: object) -> dict:
        assert cluster.gitlab_api.paused
        return _runner_record(online=False, paused=True)

    monkeypatch.setattr(HELM_DEPLOY, "read_runner_record", paused_record)
    uninstall = _uninstall_callback(delete_action, delete_state)
    result = uninstall.run()

    assert delete_action.workload().runner(scope).name == manager
    assert result == {
        "action": STATUS.APPLIED,
        "verified": True,
        "executor": EXECUTOR_KUBERNETES,
        "namespace": NAMESPACE,
        "name": manager,
        "runner_paused": True,
        "release_removed": True,
        "secrets": {"token_removed": True, "pull_removed": True},
    }
    assert cluster.gitlab_api.puts == [(f"runners/{RUNNER_ID}", {"active": False})]
    assert helm.uninstalled == [(manager, NAMESPACE)]
    assert cluster.objects == {}
    assert cluster.deleted == [
        ("secret", NAMESPACE, HELM_DEPLOY.runner_token_secret_name(scope, manager)),
        ("secret", NAMESPACE, manager + K8S_RUNNER.REGISTRY_SECRET_SUFFIX),
    ]


def test_delete_waits_for_running_jobs_to_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Poll running jobs until the provider reports the runner drained."""
    action = _action()
    state = _bind_gitlab(action, HelmCluster())
    cluster = state.kubernetes
    assert isinstance(cluster, HelmCluster)
    cluster.job_pages = [[{"id": 9001, "status": "running"}], []]
    monkeypatch.setattr(HELM_DEPLOY.time, "sleep", lambda seconds: None)
    times = iter((0.0, 1.0, 2.0))
    monkeypatch.setattr(HELM_DEPLOY.time, "monotonic", lambda: next(times))

    HELM_DEPLOY.wait_for_running_jobs(
        state.gitlab, RUNNER_ID, timeout=action.args.timeout
    )

    assert cluster.job_pages == []


def test_delete_times_out_when_jobs_keep_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Block deletion when running jobs outlive the selected timeout."""
    action = _action()
    state = _bind_gitlab(action, HelmCluster())
    cluster = state.kubernetes
    assert isinstance(cluster, HelmCluster)
    cluster.job_pages = [[{"id": 9001}], [{"id": 9002}]]
    monkeypatch.setattr(HELM_DEPLOY.time, "sleep", lambda seconds: None)
    times = iter((0.0, 100.0))
    monkeypatch.setattr(HELM_DEPLOY.time, "monotonic", lambda: next(times))

    with pytest.raises(CALLBACKS.ActionError, match="runner_running_jobs_timeout"):
        HELM_DEPLOY.wait_for_running_jobs(
            state.gitlab, RUNNER_ID, timeout=action.args.timeout
        )


def test_pause_runner_requires_paused_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the provider read-back reports the runner paused."""
    action = _action()
    state = _bind_gitlab(action, HelmCluster())
    monkeypatch.setattr(
        HELM_DEPLOY,
        "read_runner_record",
        lambda *args, **kwargs: _runner_record(online=True, paused=False),
    )

    with pytest.raises(CALLBACKS.ActionError, match="runner_pause_readback_mismatch"):
        HELM_DEPLOY.pause_runner(state.gitlab, RUNNER_ID)

    assert state.kubernetes.gitlab_api.puts == [
        (f"runners/{RUNNER_ID}", {"active": False})
    ]


def test_uninstall_skips_mutation_for_confirmed_absent_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep an absent confirmed delete a no-op before any provider mutation."""
    action = _action("delete")
    cluster = HelmCluster()
    state = action.context
    state.args = action.args
    state.found_scopes = ()
    state.kubernetes = cluster
    _bind_state_gitlab(state)
    _helm_harness(monkeypatch, action, cluster)
    monkeypatch.setattr(
        HELM_DEPLOY,
        "read_runner_record",
        lambda *args, **kwargs: _runner_record(online=False, paused=False),
    )
    uninstall = _uninstall_callback(action, state)

    assert uninstall.run() == {}
    assert cluster.gitlab_api.puts == []
    assert cluster.objects == {}


def _chart_owned_deployment(manager: str) -> dict:
    """Build one native Deployment carrying the exact Helm release markers.

    :param manager: Exact manager workload identity selected by the run.
    :returns: Deployment document accepted by the chart-ownership check.
    """
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": manager,
            "namespace": NAMESPACE,
            "uid": "manager-deployment-uid",
            "labels": {"app.kubernetes.io/managed-by": "Helm"},
            "annotations": {
                "meta.helm.sh/release-name": manager,
                "meta.helm.sh/release-namespace": NAMESPACE,
            },
        },
    }


def test_uninstall_is_no_op_when_release_and_secrets_are_already_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report a verified no-op when the release and Secrets are already gone."""
    action = _action("delete")
    cluster = HelmCluster()
    state = action.context
    state.args = action.args
    scope = RUN_SCOPE.RunScope(RUN_ID, NAMESPACE, True)
    state.run_scope = scope
    state.found_scopes = (scope,)
    state.kubernetes = cluster
    _bind_state_gitlab(state)
    _helm_harness(monkeypatch, action, cluster)
    manager = action.workload().runner(scope).name
    cluster.objects[("deployment", NAMESPACE, manager)] = _chart_owned_deployment(
        manager
    )
    monkeypatch.setattr(
        HELM_DEPLOY,
        "read_runner_record",
        lambda *args, **kwargs: _runner_record(online=False, paused=True),
    )
    uninstall = _uninstall_callback(action, state)

    result = uninstall.run()

    assert result["action"] == STATUS.NO_OP
    assert result["release_removed"] is False
    assert result["secrets"] == {"token_removed": False, "pull_removed": False}


@pytest.mark.parametrize(
    "deployment",
    ("absent", "shell-owned"),
    ids=("absent", "shell-owned"),
)
def test_uninstall_requires_chart_owned_manager_before_any_mutation(
    monkeypatch: pytest.MonkeyPatch,
    deployment: str,
) -> None:
    """Block kubernetes delete on any manager the chart does not own.

    A shell-managed runner must not be paused, its pull Secret must not be
    deleted, and its namespace must survive a kubernetes-executor delete.
    """
    action = _action("delete")
    cluster = HelmCluster()
    state = action.context
    state.args = action.args
    scope = RUN_SCOPE.RunScope(RUN_ID, NAMESPACE, True)
    state.run_scope = scope
    state.found_scopes = (scope,)
    state.kubernetes = cluster
    _bind_state_gitlab(state)
    _helm_harness(monkeypatch, action, cluster)
    manager = action.workload().runner(scope).name
    survivors: dict = {}
    if deployment == "shell-owned":
        survivors[("deployment", NAMESPACE, manager)] = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": manager,
                "namespace": NAMESPACE,
                "uid": "shell-manager-uid",
                "labels": {
                    RESOURCES.MANAGER_LABEL: RESOURCES.MANAGER_VALUE,
                    RESOURCES.OPERATION_LABEL: RUN_ID,
                },
            },
        }
        cluster.objects.update(survivors)

    def unexpected_provider_read(*args: object, **kwargs: object) -> dict:
        pytest.fail("provider read crossed the mutation boundary")

    monkeypatch.setattr(HELM_DEPLOY, "read_runner_record", unexpected_provider_read)
    uninstall = _uninstall_callback(action, state)

    with pytest.raises(
        CALLBACKS.ActionError, match="executor_kubernetes_manager_not_found"
    ):
        uninstall.run()

    assert cluster.gitlab_api.puts == []
    assert cluster.objects == survivors
    assert cluster.deleted == []


def test_runner_token_secret_roundtrip_without_value_leak() -> None:
    """Create, read and delete the exact Secret while keeping the token private."""
    cluster = HelmCluster()
    scope = RUN_SCOPE.RunScope(RUN_ID, NAMESPACE, True)
    owner = HELM_DEPLOY.RunnerTokenSecret(cluster, scope, "unit-manager")

    uid = owner.create(PRIVATE_TOKEN)

    assert uid
    assert owner.read() == uid
    assert owner.labels[RESOURCES.OPERATION_LABEL] == RUN_ID
    document = cluster.objects[("secret", NAMESPACE, owner.name)]
    assert document["data"] == {HELM_VALUES.RUNNER_TOKEN_SECRET_KEY: PRIVATE_TOKEN}
    assert PRIVATE_TOKEN not in _secret_projection(document)
    assert owner.delete() is True
    assert owner.read() is None


def test_verify_online_retains_only_kubernetes_owned_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain the namespace and credential Secrets, not chart workload objects."""
    action = _action()
    state = _bind_gitlab(action, HelmCluster())
    scope = RUN_SCOPE.RunScope(RUN_ID, NAMESPACE, True)
    state.run_scope = scope
    state.deployment_result = {"verified": True}
    monkeypatch.setattr(
        CALLBACKS,
        "read_runner_record",
        lambda *args, **kwargs: _runner_record(online=True),
    )
    callback = CALLBACKS.VerifyGitLabRunnerOnline(action)
    callback.bind(state)

    result = callback.run()

    runner = action.workload().runner(scope)
    assert state.retained_resources == {
        RUN_SCOPE.resource_identity("Namespace", NAMESPACE, ""),
        RUN_SCOPE.resource_identity(
            "Secret",
            HELM_DEPLOY.runner_token_secret_name(scope, runner.name),
            NAMESPACE,
        ),
        RUN_SCOPE.resource_identity(
            "Secret",
            runner.name + K8S_RUNNER.REGISTRY_SECRET_SUFFIX,
            NAMESPACE,
        ),
    }
    assert result["workload"] == state.deployment_result


def test_observe_rejects_online_runner_outside_helm_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Block deploy when an online runner lacks the exact Helm release markers."""
    action = _action()
    cluster = HelmCluster()
    state = _bind_gitlab(action, cluster)
    scope = RUN_SCOPE.RunScope(RUN_ID, NAMESPACE, True)
    state.run_scope = scope
    state.found_scopes = (scope,)
    manager = action.workload().runner(scope).name
    cluster.objects[("deployment", NAMESPACE, manager)] = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": manager,
            "namespace": NAMESPACE,
            "uid": "foreign-manager",
            "labels": {"app.kubernetes.io/managed-by": "ci-skills"},
        },
    }
    monkeypatch.setattr(
        CALLBACKS,
        "read_runner_record",
        lambda *args, **kwargs: _runner_record(online=True),
    )
    callback = CALLBACKS.ObserveGitLabRunner(action)
    callback.bind(state)

    with pytest.raises(
        CALLBACKS.ActionError, match="runner_online_outside_selected_manager"
    ):
        callback.run()


def test_manager_pod_rows_require_running_manager_image() -> None:
    """Read only running manager containers resolving to the selected repository."""
    rows = HELM_DEPLOY.manager_pod_rows(
        {
            "items": [
                {
                    "metadata": {"name": "pod-a", "uid": "uid-a"},
                    "status": {
                        "containerStatuses": [
                            {
                                "image": MANAGER_IMAGE,
                                "imageID": (
                                    f"docker-pullable://{MANAGER_IMAGE}"
                                    f"@sha256:{IMAGE_DIGEST}"
                                ),
                                "state": {"running": {}},
                            }
                        ]
                    },
                }
            ]
        },
        MANAGER_IMAGE,
        MANAGER_REPOSITORY,
    )
    assert rows == [
        {
            "name": "pod-a",
            "uid": "uid-a",
            "image_id": f"docker-pullable://{MANAGER_IMAGE}@sha256:{IMAGE_DIGEST}",
        }
    ]
    with pytest.raises(
        KUBERNETES.KubernetesError, match="manager_pod_readback_missing"
    ):
        HELM_DEPLOY.manager_pod_rows(
            {
                "items": [
                    {
                        "metadata": {"name": "pod-b", "uid": "uid-b"},
                        "status": {
                            "containerStatuses": [
                                {
                                    "image": MANAGER_IMAGE,
                                    "imageID": "registry.other.test/img@sha256:"
                                    + "0" * 64,
                                    "state": {"running": {}},
                                }
                            ]
                        },
                    }
                ]
            },
            MANAGER_IMAGE,
            MANAGER_REPOSITORY,
        )


def test_runner_chart_inventory_requires_exact_chart_objects() -> None:
    """Reject a render missing any expected chart object kind."""
    service_account = (
        "---\n"
        "apiVersion: v1\n"
        "kind: ServiceAccount\n"
        "metadata:\n"
        "  name: unit-manager\n"
        f"  namespace: {NAMESPACE_PLACEHOLDER}\n"
    )
    manifest = _manifest("unit-manager").replace(service_account, "")
    manifest = manifest.replace(NAMESPACE_PLACEHOLDER, NAMESPACE)
    with pytest.raises(HELM_NATIVE.HelmError, match="runner_chart_inventory_mismatch"):
        HELM_DEPLOY.runner_chart_inventory(manifest, NAMESPACE)


@pytest.mark.parametrize(
    ("action_name", "apply", "expected"),
    (
        (
            "deploy",
            False,
            (
                "K8SAuth",
                "GitLabManagerAuth",
                "HarborAuth",
                "GuardGitLabRunnerManager",
                "ResolveRunScopes",
                "SelectManagerPlanScope",
                "ObserveGitLabRunner",
                "SelectManagerChart",
                "ConfirmWorkflow",
            ),
        ),
        (
            "deploy",
            True,
            (
                "K8SAuth",
                "GitLabManagerAuth",
                "HarborAuth",
                "GuardGitLabRunnerManager",
                "ResolveRunScopes",
                "SelectManagerPlanScope",
                "ObserveGitLabRunner",
                "SelectManagerChart",
                "ConfirmWorkflow",
                "OpenManagerRunScope",
                "DeployKubernetesManagerChart",
                "VerifyGitLabRunnerOnline",
            ),
        ),
        (
            "delete",
            True,
            (
                "K8SAuth",
                "GitLabManagerAuth",
                "GuardGitLabRunnerManager",
                "ResolveRunScopes",
                "SelectResolvedManagerScope",
                "ObserveGitLabRunner",
                "ConfirmWorkflow",
                "UninstallKubernetesManagerChart",
                "VerifyGitLabRunnerPreserved",
                "DeferredCallback",
                "ObserveManagerNamespace",
            ),
        ),
    ),
)
def test_workflow_composes_kubernetes_executor_paths(
    monkeypatch: pytest.MonkeyPatch,
    action_name: str,
    apply: bool,
    expected: Sequence[str],
) -> None:
    """Compose the ordered kubernetes lifecycle without shell-only callbacks."""
    _bind_auth_harness(monkeypatch, HelmCluster())
    action = _action(action_name, apply=apply)
    executor = SCRIPT.workflow(action)
    callbacks = tuple(type(callback).__name__ for callback in executor.callbacks)

    assert callbacks == tuple(expected)
    confirm_index = callbacks.index("ConfirmWorkflow")
    if action_name == "deploy":
        assert callbacks.index("SelectManagerChart") < confirm_index
    shell_only = {
        "EnsureSCCAccess",
        "PullManagerImage",
        "GitLabRunnerRuntime",
        "DeployManagerRunner",
        "DeleteManagerRunner",
    }
    assert not any(name in shell_only for name in callbacks)
    if action_name == "delete" and apply:
        deferred = [
            callback
            for callback in executor.callbacks
            if type(callback) is ABSTRACT_CALLBACK.DeferredCallback
        ]
        assert len(deferred) == 1
        present = replace(
            action.context,
            found_scopes=(RUN_SCOPE.RunScope(RUN_ID, NAMESPACE, True),),
        )
        absent = replace(action.context, found_scopes=())
        assert type(deferred[0].factory(present)) is RUN_SCOPE.RemoveScope
        assert type(deferred[0].factory(absent)) is ABSTRACT_CALLBACK.LocalCallback
