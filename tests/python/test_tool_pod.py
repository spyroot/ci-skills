"""Exercise prepared Pod identity, ordered tooling and scoped cleanup.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import MutableMapping, MutableSequence, Sequence
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

import pytest
from conftest import import_script_module

PODS = import_script_module("lib.python.core.kubernetes.tool_pod")
POD_RESOURCES = import_script_module("lib.python.core.kubernetes.tool_pod_resources")
CLI = import_script_module("lib.python.core.kubernetes.tool_pod_cli")
SCOPES = import_script_module("lib.python.core.kubernetes.run_scope")
RESOURCES = import_script_module("lib.python.core.kubernetes.resources")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")
EXECUTOR = import_script_module("lib.python.core.executor")
COLLECTIONS = import_script_module("lib.python.core.abstract.callbacks")
SCHEMA = import_script_module("lib.python.core.schema")
RUNTIME = import_script_module("lib.python.core.runtime")
TARGET = import_script_module("lib.python.core.target")
STATUS = import_script_module("lib.python.sys.status")

if TYPE_CHECKING:
    from lib.python.core.abstract.callback import AbstractCallback
    from lib.python.core.json_types import JSONObject, JSONValue


def _target() -> TARGET.Target:
    """Select Kubernetes without configuring unrelated providers."""
    return TARGET.Target(
        github=None,
        gitlab=None,
        kubernetes=TARGET.KubernetesTarget(
            "unit-context", "https://api.cluster.example.test:6443", None
        ),
        active_surfaces=("kubernetes",),
    )


class MemoryCluster:
    """Model native object storage and tool process outcomes without network access."""

    def __init__(self) -> None:
        """Initialize an empty cluster and an absent executable."""
        self.target = _target()
        self.objects: MutableMapping[tuple[str, str, str], JSONObject] = {}
        self.applied: MutableSequence[tuple[str, str, str]] = []
        self.deleted: MutableSequence[tuple[str, str, str]] = []
        self.executed: MutableSequence[tuple[str, ...]] = []
        self.present = False
        self.checksum_fails = False
        self.transfer_fails = False
        self.missing_command = ""
        self.write_denied = False
        self.manager = "apt-get"
        self.package_fails = False
        self.tool_directory = "/tmp/unit-tools"
        self.wait_failure: Optional[BaseException] = None
        self.probe_output = "unit-tool 1.0\n"
        self.delete_fails = False

    def read(self, kind: str, name: str, namespace: str) -> Optional[JSONObject]:
        """Read a detached native object, including provider-assigned fields."""
        return deepcopy(self.objects.get((kind.casefold(), namespace, name)))

    def apply(self, document: JSONObject) -> JSONObject:
        """Store the submitted document and assign a live UID and container status."""
        observed = deepcopy(document)
        metadata = observed["metadata"]
        kind = str(observed["kind"])
        namespace = str(metadata.get("namespace", ""))
        key = (kind.casefold(), namespace, str(metadata["name"]))
        metadata["uid"] = f"uid-{len(self.applied)}"
        if kind == "Pod":
            observed["status"] = {
                "containerStatuses": [{"name": "ci-skills-pod", "restartCount": 0}]
            }
        self.objects[key] = observed
        self.applied.append(key)
        return deepcopy(observed)

    def delete(self, kind: str, name: str, namespace: str) -> None:
        """Delete exactly one object or expose the configured provider refusal."""
        if self.delete_fails:
            raise KUBERNETES.KubernetesError("unit_delete_denied")
        key = (kind.casefold(), namespace, name)
        self.deleted.append(key)
        self.objects.pop(key, None)

    def json(self, *args: str) -> JSONObject:
        """List only objects matching every requested ownership label."""
        assert args[0] == "get"
        kind = {
            "pods": "pod",
            "namespaces": "namespace",
            "configmaps": "configmap",
            "deployments": "deployment",
            "rolebindings": "rolebinding",
        }[args[1]]
        selector = args[args.index("-l") + 1]
        selected = dict(item.split("=", 1) for item in selector.split(","))
        return {
            "items": [
                deepcopy(document)
                for (resource_kind, _, _), document in self.objects.items()
                if resource_kind == kind
                and all(
                    document["metadata"].get("labels", {}).get(key) == value
                    for key, value in selected.items()
                )
            ]
        }

    def wait_pod_ready(self, name: str, namespace: str) -> JSONObject:
        """Return the exact stored Pod instead of polling or sleeping."""
        if self.wait_failure is not None:
            raise self.wait_failure
        observed = self.read("Pod", name, namespace)
        assert observed is not None
        return observed

    def wait_pod_mount_ready(
        self,
        name: str,
        namespace: str,
        *,
        mount_failure_timeout: int = 60,
        timeout: Optional[float] = None,
    ) -> JSONObject:
        """Reuse deterministic readiness for mounted Pods without a mount failure.

        :param name: Exact run-owned Pod name.
        :param namespace: Selected namespace.
        :param mount_failure_timeout: Positive mount-failure observation bound.
        :param timeout: Optional remaining readiness budget shared across attempts.
        :returns: Stored native Pod ready for the existing fixture's operations.
        """
        assert mount_failure_timeout > 0
        assert timeout is None or timeout > 0
        return self.wait_pod_ready(name, namespace)

    def _preflight_output(self, arguments: Sequence[str]) -> str:
        """Return indexed native checks for the batched prerequisite operation."""
        count = int(arguments[0])
        assert 0 <= count <= len(arguments) - 1
        commands = arguments[1 : count + 1]
        paths = arguments[count + 1 :]
        assert all(
            command in {"curl", "sha256sum", "mkdir", "install", "rm", "wc"}
            for command in commands
        )
        rows = [
            f"command\t{index}\t{'missing' if command == self.missing_command else 'present'}"
            for index, command in enumerate(commands)
        ]
        rows.extend(
            f"path\t{index}\t{'-' if self.write_denied else '/tmp' if path.startswith('/tmp') else '/var'}"
            for index, path in enumerate(paths)
        )
        return "\n".join(rows) + ("\n" if rows else "")

    def pod_exec(
        self, namespace: str, name: str, container: str, *argv: str
    ) -> RUNTIME.CommandResult:
        """Record commands and simulate their finite declared provider outcomes."""
        assert ("pod", namespace, name) in self.objects
        assert container == "ci-skills-pod"
        self.executed.append(argv)
        script = argv[2] if argv[:2] == ("/bin/sh", "-ceu") else ""
        code, output = 0, ""
        if argv[-2:] == ("unit-tool", "--version"):
            visible = self.present and (
                not self.tool_directory
                or any(f"PATH={self.tool_directory}:" in arg for arg in argv)
            )
            code, output = (0, self.probe_output) if visible else (1, "")
        elif "command_count=$1; shift; index=0;" in script:
            assert argv[3] == "operation"
            output = self._preflight_output(argv[4:])
        elif "sha256sum -c -" in script:
            code = 22 if self.transfer_fails else 1 if self.checksum_fails else 0
        elif "install -m 0755" in script:
            self.present = True
        elif "command -v unit-tool" in script:
            visible = self.present and (
                not self.tool_directory or f"PATH={self.tool_directory}:" in script
            )
            code = 0 if visible else 1
        elif "wc -c" in script:
            output = "5\n"
        elif "apt-get install" in script:
            code = 1 if self.package_fails else 0
            if not code:
                self.present = True
        elif "apt-get" in script and "command -v" in script:
            output = self.manager + "\n"
        elif "id -u" in script:
            output = "uid=1000\ngid=1000\nLinux unit x86_64\n"
        elif "rm -f " in script and "test ! -e " in script:
            pass
        else:
            pytest.fail(f"unexpected Pod command: {argv!r}")
        return RUNTIME.CommandResult(tuple(argv), code, output, "")


@pytest.fixture
def specification() -> PODS.ToolPodSpec:
    """Provide the real parsed input model with an executable capability probe."""
    return PODS.ToolPodSpec.from_document(
        {
            "image": "registry.example.test/tool-pod:unit",
            "dependencies": [
                {
                    "name": "unit-tool",
                    "commands": ["unit-tool"],
                    "probe": ["unit-tool", "--version"],
                    "linux": {
                        "method": "download",
                        "url": "https://artifacts.example.test/unit-tool",
                        "sha256": "a" * 64,
                        "destination": "/tmp/unit-tools/unit-tool",
                    },
                }
            ],
        }
    )


def _state(cluster: MemoryCluster) -> PODS.ToolPodState:
    """Bind the fake transport as an already authenticated library consumer."""
    state = PODS.ToolPodState()
    state.kubernetes = cluster
    return state


@pytest.fixture
def placement_specification(specification: PODS.ToolPodSpec) -> PODS.ToolPodSpec:
    """Extend the existing fixture with native placement fields and OR terms.

    :param specification: Existing dependency fixture used by the real lifecycle.
    :returns: Native placement input whose hard constraints must survive retries.
    """
    document = specification.as_dict()
    document.update(
        {
            "nodeSelector": {"storage": "iso"},
            "affinity": {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {"key": "zone", "operator": "In", "values": [zone]}
                                ]
                            }
                            for zone in ("east", "west")
                        ]
                    }
                }
            },
            "tolerations": [
                {
                    "key": "storage",
                    "operator": "Equal",
                    "value": "iso",
                    "effect": "NoSchedule",
                }
            ],
        }
    )
    return PODS.ToolPodSpec.from_document(document)


def test_native_placement_and_observed_node_survive_prepare_and_reuse(
    placement_specification: PODS.ToolPodSpec,
) -> None:
    """Submit exact caller placement and retain the independently observed node.

    :param placement_specification: Existing tools with caller-selected placement.
    """

    class ScheduledCluster(MemoryCluster):
        """Assign a node only in the provider response."""

        def apply(self, document: JSONObject) -> JSONObject:
            """Add scheduler output after storing the actual submitted resource.

            :param document: Production-rendered resource.
            :returns: Native provider response containing the assigned Pod node.
            """
            observed = super().apply(document)
            if observed["kind"] == "Pod":
                key = (
                    "pod",
                    observed["metadata"]["namespace"],
                    observed["metadata"]["name"],
                )
                self.objects[key]["spec"]["nodeName"] = "scheduled-node"
                observed = deepcopy(self.objects[key])
            return observed

    cluster = ScheduledCluster()
    state = _state(cluster)
    original = placement_specification.as_dict()
    action = PODS.PrepareToolPodAction(placement_specification)
    _run(state, _preparation(action))
    pod = cluster.read("Pod", state.pod.name, state.pod.namespace)
    for field in ("nodeSelector", "affinity", "tolerations"):
        assert pod["spec"][field] == original[field]
    assert state.pod.node_name == "scheduled-node"
    assert state.readback["pod"]["node_name"] == "scheduled-node"
    applied = tuple(cluster.applied)
    repeated = _state(cluster)

    _run(
        repeated,
        _preparation(
            PODS.PrepareToolPodAction(
                placement_specification, run_id=state.run_scope.run_id
            )
        ),
    )

    assert repeated.pod == state.pod
    assert repeated.pod.node_name == "scheduled-node"
    assert tuple(cluster.applied) == applied
    assert placement_specification.as_dict() == original
    assert cluster.deleted == []


@pytest.mark.parametrize(
    "outcome",
    ("mount", "late-mount", "stale-event", "forbidden", "ready", "ready-after-event"),
)
def test_pending_mount_detection_is_bounded_and_requires_current_pod_uid(
    outcome: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Detect an assigned Pending Pod's mount failure without trusting stale events.

    :param outcome: Current or stale event, terminal provider refusal, or ready Pod.
    :param monkeypatch: Replace only provider reads and time with deterministic inputs.
    """
    service = KUBERNETES.KubernetesService(_target(), timeout=240)
    elapsed = 0.0
    reads: list[float] = []
    event_calls: list[tuple[str, ...]] = []
    pod = {
        "metadata": {"uid": "current-uid", "name": "reader", "namespace": "storage"},
        "spec": {"nodeName": "failed-node"},
        "status": {
            "phase": "Pending",
            "containerStatuses": [
                {"state": {"waiting": {"reason": "ContainerCreating"}}}
            ],
        },
    }
    if outcome == "ready":
        pod["status"]["phase"] = "Running"
        pod["status"]["conditions"] = [{"type": "Ready", "status": "True"}]
    event = {
        "reason": "FailedMount",
        "message": "fixture mount stalled",
        "involvedObject": {
            "uid": "deleted-pod-uid" if outcome == "stale-event" else "current-uid"
        },
    }

    def read_pod(kind: str, name: str, namespace: str, *, timeout: float) -> JSONObject:
        """Observe only the selected Pod within the remaining deadline.

        :param kind: Requested native object kind.
        :param name: Selected reader Pod name.
        :param namespace: Selected storage namespace.
        :param timeout: Remaining provider command bound.
        :returns: Assigned Pod still waiting for its storage mount.
        """
        assert (kind, name, namespace) == ("Pod", "reader", "storage")
        assert 0 < timeout <= 240 - elapsed
        reads.append(timeout)
        assert len(reads) <= 49
        observed = deepcopy(pod)
        if outcome == "ready-after-event" and elapsed >= 15:
            observed["status"]["phase"] = "Running"
            observed["status"]["conditions"] = [{"type": "Ready", "status": "True"}]
        return observed

    def events(*args: str, timeout: float) -> JSONObject:
        """Return events through the real service's UID correlation boundary.

        :param *args: Native event query arguments.
        :param timeout: Remaining operation bound.
        :returns: One FailedMount event for the chosen identity.
        :raises KubernetesError: The provider refuses the event query with HTTP 403.
        """
        assert 0 < timeout <= 240 - elapsed
        assert "involvedObject.uid=current-uid,reason=FailedMount" in args
        event_calls.append(args)
        if outcome == "forbidden":
            raise KUBERNETES.KubernetesError("403 Forbidden")
        if outcome == "late-mount" and elapsed < 120:
            return {"items": []}
        return {"items": [deepcopy(event)]}

    def advance(seconds: float) -> None:
        """Advance fake time, with no real sleeping.

        :param seconds: Positive delay limited to the remaining detection window.
        """
        nonlocal elapsed
        assert 0 < seconds <= 240 - elapsed
        elapsed += seconds

    def forbidden_wait(*_args: object, **_kwargs: object) -> None:
        """Reject an ordinary wait after this test's full deadline was consumed.

        :param *_args: Unexpected positional native wait inputs.
        :param **_kwargs: Unexpected keyword native wait inputs.
        """
        pytest.fail("mount detection exceeded its deadline through a second wait")

    monkeypatch.setattr(service, "read", read_pod)
    monkeypatch.setattr(service, "json", events)
    monkeypatch.setattr(KUBERNETES.KubernetesService, "wait_pod_ready", forbidden_wait)
    monkeypatch.setattr(KUBERNETES.time, "monotonic", lambda: elapsed)
    monkeypatch.setattr(KUBERNETES.time, "sleep", advance)
    if outcome in {"mount", "late-mount"}:
        with pytest.raises(KUBERNETES.PodMountFailure) as failure:
            service.wait_pod_mount_ready("reader", "storage")
        assert failure.value.pod == pod
        assert list(failure.value.events) == [event]
        assert len(reads) == len(event_calls)
        assert elapsed == (180 if outcome == "late-mount" else 60)
    elif outcome == "stale-event":
        with pytest.raises(KUBERNETES.KubernetesError, match="tool_pod_ready_timeout"):
            service.wait_pod_mount_ready("reader", "storage")
        assert 1 < len(reads) <= 49
        assert len(event_calls) == len(reads)
        assert elapsed == 240
    elif outcome == "forbidden":
        with pytest.raises(KUBERNETES.KubernetesError, match="403 Forbidden"):
            service.wait_pod_mount_ready("reader", "storage")
        assert len(reads) == len(event_calls) == 1
        assert elapsed == 0
    else:
        observed = service.wait_pod_mount_ready("reader", "storage")
        assert observed["status"]["conditions"] == [{"type": "Ready", "status": "True"}]
        assert elapsed == (15 if outcome == "ready-after-event" else 0)
        assert len(reads) == len(event_calls) + 1


@pytest.mark.parametrize("failed_attempts", (1, 3), ids=("recovery", "exhaustion"))
def test_mount_retry_preserves_constraints_and_cleans_only_owned_resources(
    placement_specification: PODS.ToolPodSpec,
    failed_attempts: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry on another node with unchanged scope, target, storage and hard affinity.

    :param placement_specification: Caller-selected native constraints and tools.
    :param failed_attempts: One recoverable failure or exhaustion of the three attempts.
    :param monkeypatch: Advance only fake time spent in provider readiness attempts.
    """
    submitted: list[JSONObject] = []
    attempted_nodes: list[str] = []
    budgets: list[float] = []
    elapsed = 0.0

    class MountFailureCluster(MemoryCluster):
        """Model external scheduling and FailedMount outcomes only."""

        def apply(self, document: JSONObject) -> JSONObject:
            """Journal each Pod and let the provider scheduler assign a distinct node.

            :param document: Resource submitted by the actual retry implementation.
            :returns: Detached native response with an observed scheduler assignment.
            """
            observed = super().apply(document)
            if observed["kind"] == "Pod":
                submitted.append(deepcopy(document))
                key = (
                    "pod",
                    observed["metadata"]["namespace"],
                    observed["metadata"]["name"],
                )
                self.objects[key]["spec"]["nodeName"] = f"mount-node-{len(submitted)}"
                observed = deepcopy(self.objects[key])
            return observed

        def wait_pod_mount_ready(
            self,
            name: str,
            namespace: str,
            *,
            mount_failure_timeout: int = 60,
            timeout: Optional[float] = None,
        ) -> JSONObject:
            """Fail selected attempts with the matching native Pending Pod and event.

            :param name: Exact run Pod selected by production.
            :param namespace: Same selected namespace on every attempt.
            :param mount_failure_timeout: Bounded mount observation interval.
            :param timeout: Remaining shared readiness budget.
            :returns: Native ready Pod after the configured failure count.
            :raises KubernetesError: Selected mount attempts fail before tools execute.
            """
            assert mount_failure_timeout > 0
            nonlocal elapsed
            assert timeout is not None and 0 < timeout <= 300 - elapsed
            budgets.append(timeout)
            pod = self.read("Pod", name, namespace)
            node = pod["spec"]["nodeName"]
            attempted_nodes.append(node)
            if len(attempted_nodes) <= failed_attempts:
                pod["status"].update(
                    {
                        "phase": "Pending",
                        "containerStatuses": [
                            {
                                "name": "ci-skills-pod",
                                "state": {"waiting": {"reason": "ContainerCreating"}},
                            }
                        ],
                    }
                )
                event = {
                    "reason": "FailedMount",
                    "message": f"fixture mount stalled on {node}",
                    "involvedObject": {"uid": pod["metadata"]["uid"]},
                }
                elapsed += 61
                raise KUBERNETES.PodMountFailure(pod, [event])
            return super().wait_pod_mount_ready(
                name,
                namespace,
                mount_failure_timeout=mount_failure_timeout,
                timeout=timeout,
            )

    cluster = MountFailureCluster()
    monkeypatch.setattr(KUBERNETES.time, "monotonic", lambda: elapsed)
    cluster.apply({"kind": "Namespace", "metadata": {"name": "caller-storage"}})
    cluster.apply(
        {
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": "caller-data", "namespace": "caller-storage"},
        }
    )
    cluster.apply(
        {
            "kind": "ConfigMap",
            "metadata": {"name": "neighbor", "namespace": "caller-storage"},
        }
    )
    state = _state(cluster)
    mount = PODS.ToolPodMount(
        "caller-volume",
        {"persistentVolumeClaim": {"claimName": "caller-data"}},
        "/shared",
    )
    action = PODS.PrepareToolPodAction(
        placement_specification,
        run_id="mount-recovery-run",
        namespace="caller-storage",
        mounts=(mount,),
        timeout=300,
    )
    callbacks = _preparation(action)
    if failed_attempts == 3:
        with pytest.raises(
            KUBERNETES.KubernetesError, match="tool_pod_mount_retry_exhausted"
        ) as failure:
            _run(state, callbacks)
        diagnostic = str(failure.value) + json.dumps(state.readback)
        for node in attempted_nodes:
            assert node in diagnostic
        assert "FailedMount" in diagnostic
        assert not state.retained_resources
        assert not cluster.executed
        assert not any(kind == "pod" for kind, _, _ in cluster.objects)
    else:
        _run(state, callbacks)
        assert state.pod.node_name == "mount-node-2"
        assert state.readback["tools"][0]["verified"] is True
    assert len(submitted) == (3 if failed_attempts == 3 else 2)
    assert len(attempted_nodes) == len(submitted)
    assert budgets == [300 - 61 * index for index in range(len(submitted))]
    assert cluster.target == _target()
    assert state.run_scope.run_id == "mount-recovery-run"
    for index, document in enumerate(submitted):
        assert document["metadata"]["namespace"] == "caller-storage"
        assert document["metadata"]["name"] == submitted[0]["metadata"]["name"]
        assert document["spec"]["nodeSelector"] == {"storage": "iso"}
        assert (
            document["spec"]["tolerations"]
            == placement_specification.as_dict()["tolerations"]
        )
        assert document["spec"]["volumes"] == submitted[0]["spec"]["volumes"]
        assert document["spec"]["containers"] == submitted[0]["spec"]["containers"]
        terms = document["spec"]["affinity"]["nodeAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]["nodeSelectorTerms"]
        assert len(terms) == 2
        for term, zone in zip(terms, ("east", "west")):
            assert term["matchExpressions"] == [
                {"key": "zone", "operator": "In", "values": [zone]}
            ]
            if index:
                assert any(
                    expression["key"] == "metadata.name"
                    and expression["operator"] == "NotIn"
                    and set(expression["values"]) == set(attempted_nodes[:index])
                    for expression in term["matchFields"]
                )
    assert cluster.read("Namespace", "caller-storage", "") is not None
    assert (
        cluster.read("PersistentVolumeClaim", "caller-data", "caller-storage")
        is not None
    )
    assert cluster.read("ConfigMap", "neighbor", "caller-storage") is not None
    assert all(kind in {"pod", "configmap"} for kind, _, _ in cluster.deleted)


def _run(state: PODS.ToolPodState, callbacks: Sequence[AbstractCallback]) -> JSONObject:
    """Run the actual lifecycle and reverse cleanup against a bound state."""
    return EXECUTOR.WorkflowExecutor(
        context=state, callbacks=COLLECTIONS.Callbacks(callbacks)
    ).run()


def _preparation(action: PODS.PrepareToolPodAction) -> Sequence[AbstractCallback]:
    """Compose the production preparation callbacks in their required order."""
    scope = SCOPES.RunScope.new(run_id=action.run_id, namespace=action.namespace)
    callbacks: list[AbstractCallback] = []
    if action.run_id is not None:
        callbacks.append(
            SCOPES.ResolveRunScopes(SCOPES.ResolveRunScopeAction(action.run_id))
        )
    callbacks.extend(
        (
            SCOPES.OpenRunScope(SCOPES.RunScopeAction(scope)),
            PODS.StageToolPod(action),
            PODS.ReadToolState(action),
            *PODS.tool_callbacks(action),
            PODS.VerifyTools(action),
        )
    )
    return tuple(callbacks)


def _removal(run_id: str) -> Sequence[AbstractCallback]:
    """Compose labeled lookup, tool-resource deletion and namespace ownership cleanup."""
    lookup = SCOPES.ResolveRunScopeAction(run_id)
    return (
        SCOPES.ResolveRunScopes(lookup),
        PODS.RemoveToolPod(PODS.RemoveToolPodAction(run_id)),
        SCOPES.RemoveScope(lookup),
    )


def test_prepare_retains_verified_pod_and_repeat_reads_live_identity(
    specification: PODS.ToolPodSpec,
) -> None:
    """A second invocation probes the retained Pod without installing again."""
    cluster = MemoryCluster()
    first = _state(cluster)
    _run(first, _preparation(PODS.PrepareToolPodAction(specification)))
    original = first.pod
    assert cluster.present
    assert cluster.deleted == []
    assert first.readback["tools"][0]["observed"] == "unit-tool 1.0\n"
    applied_count = len(cluster.applied)
    cluster.executed.clear()

    second = _state(cluster)
    action = PODS.PrepareToolPodAction(specification, run_id=first.run_scope.run_id)
    callbacks = _preparation(action)
    _run(second, callbacks)

    assert second.pod == original
    stage = next(
        callback for callback in callbacks if isinstance(callback, PODS.StageToolPod)
    )
    assert second.results[stage]["action"] == STATUS.NO_OP
    assert len(cluster.applied) == applied_count
    assert not any("curl " in " ".join(argv) for argv in cluster.executed)
    assert any(argv[-2:] == ("unit-tool", "--version") for argv in cluster.executed)
    assert cluster.deleted == []


def test_checksum_failure_stops_install_and_cleans_generated_scope(
    specification: PODS.ToolPodSpec,
) -> None:
    """A failed artifact check never reaches installation or successful retention."""
    cluster = MemoryCluster()
    cluster.checksum_fails = True
    state = _state(cluster)
    with pytest.raises(KUBERNETES.KubernetesError):
        _run(state, _preparation(PODS.PrepareToolPodAction(specification)))

    assert not cluster.present
    assert not any("install -m 0755" in " ".join(argv) for argv in cluster.executed)
    assert [kind for kind, _, _ in cluster.deleted] == ["pod", "configmap", "namespace"]
    assert cluster.objects == {}
    assert not state.retained_resources


def test_failed_download_cleans_staging_path_in_reused_pod(
    specification: PODS.ToolPodSpec,
) -> None:
    """A failed transfer removes its partial file without deleting retained resources."""
    cluster = MemoryCluster()
    prepared = _state(cluster)
    _run(prepared, _preparation(PODS.PrepareToolPodAction(specification)))
    cluster.present = False
    cluster.transfer_fails = True
    cluster.executed.clear()
    failed = _state(cluster)
    action = PODS.PrepareToolPodAction(specification, run_id=prepared.run_scope.run_id)
    with pytest.raises(KUBERNETES.KubernetesError):
        _run(failed, _preparation(action))

    cleanup = [
        argv[2] for argv in cluster.executed if len(argv) > 2 and "rm -f " in argv[2]
    ]
    assert len(cleanup) == 1
    assert "ci-skills-dependency-0" in cleanup[0]
    assert "test ! -e" in cleanup[0]
    assert cluster.deleted == []
    assert cluster.read("Pod", prepared.pod.name, prepared.pod.namespace) is not None


def test_failure_never_deletes_callers_existing_namespace(
    specification: PODS.ToolPodSpec,
) -> None:
    """Only the new Pod is removed when installation fails in a selected namespace."""
    cluster = MemoryCluster()
    cluster.apply({"kind": "Namespace", "metadata": {"name": "consumer-tools"}})
    cluster.checksum_fails = True
    state = _state(cluster)
    action = PODS.PrepareToolPodAction(specification, namespace="consumer-tools")
    with pytest.raises(KUBERNETES.KubernetesError):
        _run(state, _preparation(action))

    assert [kind for kind, _, _ in cluster.deleted] == ["pod", "configmap"]
    assert cluster.read("Namespace", "consumer-tools", "") is not None
    assert not state.run_scope.owns_namespace


def test_cleanup_failure_is_retained_as_failed_evidence(
    specification: PODS.ToolPodSpec,
) -> None:
    """Provider deletion failure cannot be reported as successful cleanup."""
    cluster = MemoryCluster()
    cluster.checksum_fails = True
    cluster.delete_fails = True
    state = _state(cluster)
    with pytest.raises(KUBERNETES.KubernetesError):
        _run(state, _preparation(PODS.PrepareToolPodAction(specification)))
    assert state.failed
    assert state.cleanup_failures
    assert "unit_delete_denied" in state.cleanup_failures[0]["reason"]


@pytest.mark.parametrize("mismatch", ("image", "fingerprint", "uid"))
def test_live_identity_mismatch_prevents_verified_retention(
    specification: PODS.ToolPodSpec, mismatch: str
) -> None:
    """A replaced or changed live Pod cannot inherit earlier successful evidence."""
    cluster = MemoryCluster()
    cluster.present = True
    state = _state(cluster)
    action = PODS.PrepareToolPodAction(specification)
    _run(state, _preparation(action))
    prepared = state
    state = _state(cluster)
    state.pod = prepared.pod
    state.run_scope = prepared.run_scope
    pod = cluster.objects[("pod", state.pod.namespace, state.pod.name)]
    if mismatch == "image":
        pod["spec"]["containers"][0]["image"] = "registry.example.test/other:unit"
    elif mismatch == "fingerprint":
        pod["metadata"]["annotations"]["ci-skills.spyroot.dev/spec-sha256"] = "changed"
    else:
        pod["metadata"]["uid"] = "replacement-pod"
    with pytest.raises(KUBERNETES.KubernetesError):
        _run(state, (PODS.VerifyTools(action),))
    assert not state.retained_resources


def test_remove_finds_observed_namespace_and_repeat_is_no_op(
    specification: PODS.ToolPodSpec,
) -> None:
    """Removal follows labels in a caller namespace and preserves unrelated objects."""
    cluster = MemoryCluster()
    cluster.apply({"kind": "Namespace", "metadata": {"name": "not-the-run-id"}})
    prepared = _state(cluster)
    _run(
        prepared,
        _preparation(
            PODS.PrepareToolPodAction(
                specification, run_id="unit-run", namespace="not-the-run-id"
            )
        ),
    )
    removed = _state(cluster)
    callbacks = _removal("unit-run")
    _run(removed, callbacks)
    assert removed.results[callbacks[1]]["action"] == STATUS.APPLIED
    assert removed.results[callbacks[1]]["scopes"][0]["namespace"] == "not-the-run-id"
    assert (
        removed.results[callbacks[2]]["preserved_scopes"][0]["namespace"]
        == "not-the-run-id"
    )
    assert cluster.read("Namespace", "not-the-run-id", "") is not None
    writes = len(cluster.deleted)

    repeated = _state(cluster)
    again = _removal("unit-run")
    _run(repeated, again)
    assert repeated.results[again[1]]["action"] == STATUS.NO_OP
    assert repeated.results[again[2]]["action"] == STATUS.NO_OP
    assert len(cluster.deleted) == writes


@pytest.mark.parametrize("kind", ("Pod", "Deployment", "RoleBinding"))
def test_run_lookup_rejects_two_live_namespaces_without_mutation(kind: str) -> None:
    """Reject ambiguous run ownership without choosing a namespace to delete.

    :param kind: Native resource kind carrying duplicate run ownership labels.
    """
    cluster = MemoryCluster()
    for namespace in ("first-space", "second-space"):
        scope = SCOPES.RunScope("unit-run", namespace, False)
        document = {
            "kind": kind,
            "metadata": {
                "name": f"resource-{namespace}",
                "namespace": namespace,
                "labels": dict(scope.labels),
            },
        }
        cluster.apply(
            RESOURCES.ResourceSet(cluster, namespace, "unit-run").managed(document)
        )
    with pytest.raises(KUBERNETES.KubernetesError, match="namespace_ambiguous"):
        SCOPES.RunScope.find(cluster, "unit-run")
    assert cluster.deleted == []


@pytest.mark.parametrize("flags", ((), ("--dry-run",)), ids=("default", "explicit"))
def test_default_plan_is_kubernetes_only_and_never_constructs_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    specification: PODS.ToolPodSpec,
    flags: tuple[str, ...],
) -> None:
    """A default invocation plans locally without auth or provider mutation."""
    source = tmp_path / "tools.json"
    source.write_text(json.dumps(specification.as_dict()), encoding="utf-8")
    args = CLI.build_parser().parse_args(("prepare", "--spec", str(source), *flags))
    monkeypatch.setattr(CLI, "resolve_target", lambda *_args, **_kwargs: _target())

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("dry-run constructed a live workflow")

    monkeypatch.setattr(CLI, "workflow", forbidden)
    reports: MutableSequence[JSONObject] = []

    def capture(output: JSONObject, _args: object) -> int:
        reports.append(output)
        return 0

    monkeypatch.setattr(CLI, "emit_result", capture)
    assert CLI.execute(args) == 0
    report = reports[0]
    assert report["status"] == STATUS.DRY_RUN
    assert report["plan"]["spec"] == specification.as_dict()
    assert report["plan"]["target"]["context"] == "unit-context"


def test_plan_digest_changes_with_supplied_run_id_or_input(
    tmp_path: Path, specification: PODS.ToolPodSpec
) -> None:
    """Plan confirmation binds both caller run selection and exact dependency values."""
    source = tmp_path / "tools.json"
    document = specification.as_dict()
    source.write_text(json.dumps(document), encoding="utf-8")
    parser = CLI.build_parser()
    first = parser.parse_args(("prepare", "--spec", str(source), "--run-id", "first"))
    second = parser.parse_args(("prepare", "--spec", str(source), "--run-id", "second"))
    first_plan = CLI.operation_plan(first, _target())
    assert CLI.plan_digest(first_plan) != CLI.plan_digest(
        CLI.operation_plan(second, _target())
    )
    document["dependencies"][0]["probe"] = ["unit-tool", "--help"]
    source.write_text(json.dumps(document), encoding="utf-8")
    assert CLI.plan_digest(first_plan) != CLI.plan_digest(
        CLI.operation_plan(first, _target())
    )


@pytest.mark.parametrize("confirmation", ("", "0" * 64), ids=("missing", "different"))
def test_apply_refuses_unconfirmed_plan_before_constructing_mutations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    specification: PODS.ToolPodSpec,
    confirmation: str,
) -> None:
    """Neither missing nor mismatched confirmation can enter the live workflow."""
    source = tmp_path / "tools.json"
    source.write_text(json.dumps(specification.as_dict()), encoding="utf-8")
    argv = ["prepare", "--spec", str(source), "--apply"]
    if confirmation:
        argv.extend(("--confirm-plan", confirmation))
    args = CLI.build_parser().parse_args(argv)
    monkeypatch.setattr(CLI, "resolve_target", lambda *_args, **_kwargs: _target())
    monkeypatch.setattr(CLI, "bind_sources", lambda target, **_kwargs: target)
    reports: MutableSequence[JSONObject] = []

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("unconfirmed apply constructed a mutation workflow")

    def capture(output: JSONObject, _args: object) -> int:
        reports.append(output)
        return 1

    monkeypatch.setattr(CLI, "workflow", forbidden)
    monkeypatch.setattr(CLI, "emit_result", capture)
    assert CLI.execute(args) == 1
    assert reports[0]["status"] == STATUS.BLOCKED
    assert reports[0]["errors"][0]["reason"] == "confirm_plan_mismatch_rerun_plan"


def _bind_cli_cluster(monkeypatch: pytest.MonkeyPatch, cluster: MemoryCluster) -> None:
    """Replace authentication/provider boundaries while retaining CLI composition."""

    def resolve(*_args: object, **kwargs: object) -> TARGET.Target:
        assert kwargs["required_surfaces"] == ("kubernetes",)
        return cluster.target

    def configure(callback: KUBERNETES.K8SAuth) -> JSONObject:
        callback.state.kubernetes_target = cluster.target
        return {}

    def authenticate(callback: KUBERNETES.K8SAuth) -> JSONObject:
        callback.state.kubernetes = cluster
        callback.state.access_evidence["kubernetes"] = {"identity": "unit-user"}
        return {"identity": "unit-user"}

    monkeypatch.setattr(CLI, "resolve_target", resolve)
    monkeypatch.setattr(CLI, "bind_sources", lambda target, **_kwargs: target)
    monkeypatch.setattr(KUBERNETES.K8SAuth, "read_config", configure)
    monkeypatch.setattr(KUBERNETES.K8SAuth, "run", authenticate)


def _confirmed_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    cluster: MemoryCluster,
    specification: PODS.ToolPodSpec,
) -> tuple[int, JSONObject, str]:
    """Execute a real confirmed plan and decode the actual emitted JSON report."""
    _bind_cli_cluster(monkeypatch, cluster)
    source = tmp_path / "tools.json"
    source.write_text(json.dumps(specification.as_dict()), encoding="utf-8")
    args = CLI.build_parser().parse_args(
        ("prepare", "--spec", str(source), "--apply", "--json")
    )
    args.confirm_plan = CLI.plan_digest(CLI.operation_plan(args, cluster.target))
    code = CLI.execute(args)
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured.out + captured.err


@pytest.mark.parametrize(
    "outcome",
    ("success", "transfer", "checksum", "timeout", "interrupt", "cleanup"),
)
def test_confirmed_cli_reports_real_lifecycle_outcome_and_result_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    specification: PODS.ToolPodSpec,
    outcome: str,
) -> None:
    """Confirmed apply emits observed success/failure with verified cleanup evidence."""
    cluster = MemoryCluster()
    cluster.transfer_fails = outcome == "transfer"
    cluster.checksum_fails = outcome in ("checksum", "cleanup")
    cluster.delete_fails = outcome == "cleanup"
    if outcome == "timeout":
        cluster.wait_failure = KUBERNETES.KubernetesError("timeout")
    elif outcome == "interrupt":
        cluster.wait_failure = KeyboardInterrupt()

    code, report, _ = _confirmed_cli(
        monkeypatch, tmp_path, capsys, cluster, specification
    )
    SCHEMA.validate_specification(report, "results/tool-pod.schema.json")
    assert report["plan_digest"] == CLI.plan_digest(report["plan"])
    assert report["access"]["kubernetes"]["identity"] == "unit-user"
    if outcome == "success":
        assert code == 0
        assert report["status"] == STATUS.PASS
        assert report["errors"] == []
        assert report["readback"]["tools"][0]["verified"]
        assert {item["kind"] for item in report["cleanup"]["retained"]} == {
            "pod",
            "configmap",
            "namespace",
        }
        assert cluster.deleted == []
    else:
        assert code == 2
        assert report["status"] == STATUS.BLOCKED
        assert report["errors"]
        assert not report["cleanup"]["retained"]
        if outcome == "cleanup":
            assert report["cleanup"]["status"] == STATUS.BLOCKED
            assert "unit_delete_denied" in report["cleanup"]["failures"][0]["reason"]
        else:
            assert report["cleanup"]["status"] == STATUS.PASS
            assert cluster.objects == {}
            assert [kind for kind, _, _ in cluster.deleted] == [
                "pod",
                "configmap",
                "namespace",
            ]
        if outcome in ("timeout", "interrupt"):
            assert report["errors"][0]["reason"] == (
                "timeout" if outcome == "timeout" else "interrupted"
            )
            assert cluster.executed == []
        else:
            assert not any(
                "install -m 0755" in " ".join(argv) for argv in cluster.executed
            )


@pytest.mark.parametrize(
    "fault", ("missing-executable", "not-writable", "invalid-manager")
)
def test_preflight_failure_stops_transfer_and_downstream_callback(
    specification: PODS.ToolPodSpec, fault: str
) -> None:
    """A provider prerequisite failure stops this linear combo before later actions."""
    cluster = MemoryCluster()
    cluster.missing_command = "curl" if fault == "missing-executable" else ""
    cluster.write_denied = fault == "not-writable"
    cluster.manager = "unsupported" if fault == "invalid-manager" else "apt-get"
    state = _state(cluster)
    action = PODS.PrepareToolPodAction(specification)
    reached: MutableSequence[str] = []

    class Consumer(PODS.ToolPodK8SCallback):
        """Record whether the later consumer was incorrectly entered."""

        def run(self) -> JSONObject:
            reached.append("consumer")
            return {"verified": True}

    with pytest.raises(KUBERNETES.KubernetesError):
        _run(state, (*_preparation(action), Consumer(action)))
    assert reached == []
    assert not any("curl --fail" in " ".join(argv) for argv in cluster.executed)
    assert cluster.objects == {}
    assert not state.retained_resources


def test_native_package_manager_failure_stops_verification_and_cleans_scope(
    specification: PODS.ToolPodSpec,
) -> None:
    """An available package manager may still reject a package; cleanup must follow."""
    document = specification.as_dict()
    dependency = document["dependencies"][0]
    dependency.pop("linux")
    dependency["apt"] = ["unit-tool"]
    package_spec = PODS.ToolPodSpec.from_document(document)
    cluster = MemoryCluster()
    cluster.tool_directory = ""
    cluster.package_fails = True
    state = _state(cluster)
    with pytest.raises(KUBERNETES.KubernetesError, match="command_failed"):
        _run(state, _preparation(PODS.PrepareToolPodAction(package_spec)))
    assert any("apt-get install" in " ".join(argv) for argv in cluster.executed)
    assert not any(argv[-2:] == ("unit-tool", "--version") for argv in cluster.executed)
    assert cluster.objects == {}
    assert not state.retained_resources


def test_same_uid_container_restart_reprobes_actual_tools(
    specification: PODS.ToolPodSpec,
) -> None:
    """A restarted container cannot reuse an earlier successful executable probe."""
    cluster = MemoryCluster()
    prepared = _state(cluster)
    _run(prepared, _preparation(PODS.PrepareToolPodAction(specification)))
    live = cluster.objects[("pod", prepared.pod.namespace, prepared.pod.name)]
    live["status"]["containerStatuses"][0]["restartCount"] = 1
    cluster.executed.clear()
    repeated = _state(cluster)
    callbacks = _preparation(
        PODS.PrepareToolPodAction(specification, run_id=prepared.run_scope.run_id)
    )
    _run(repeated, callbacks)
    assert repeated.pod.uid == prepared.pod.uid
    assert repeated.pod.restart_count == 1
    assert any(argv[-2:] == ("unit-tool", "--version") for argv in cluster.executed)
    assert not any("curl --fail" in " ".join(argv) for argv in cluster.executed)
    assert repeated.readback["tools"][0]["verified"]


@pytest.mark.parametrize("source", ("probe", "failure"))
def test_cli_redacts_provider_output_and_failure_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    specification: PODS.ToolPodSpec,
    source: str,
) -> None:
    """Actual CLI serialization never exposes credentials echoed by a provider."""
    cluster = MemoryCluster()
    secret = "unit-credential-value"
    if source == "probe":
        cluster.probe_output = f"token={secret}"
    else:
        cluster.wait_failure = KUBERNETES.KubernetesError(f"password={secret}")
    _, report, emitted = _confirmed_cli(
        monkeypatch, tmp_path, capsys, cluster, specification
    )
    assert secret not in emitted
    assert "[REDACTED]" in emitted
    SCHEMA.validate_specification(report, "results/tool-pod.schema.json")


@pytest.mark.parametrize("operation", ("open", "remove-pod", "remove-scope"))
def test_scope_callbacks_reject_lookup_from_different_run_without_mutation(
    operation: str,
) -> None:
    """Cached lookup evidence cannot authorize an operation for a different run ID."""
    cluster = MemoryCluster()
    state = _state(cluster)
    state.resolved_run_id = "stale-run"
    state.found_scopes = (SCOPES.RunScope("stale-run", "prior-namespace", True),)
    if operation == "open":
        callback = SCOPES.OpenRunScope(
            SCOPES.RunScopeAction(
                SCOPES.RunScope("selected-run", "new-candidate", True)
            )
        )
    elif operation == "remove-pod":
        callback = PODS.RemoveToolPod(PODS.RemoveToolPodAction("selected-run"))
    else:
        callback = SCOPES.RemoveScope(SCOPES.ResolveRunScopeAction("selected-run"))

    with pytest.raises(
        KUBERNETES.KubernetesError, match="run_scope_lookup_action_mismatch"
    ):
        _run(state, (callback,))
    assert cluster.applied == []
    assert cluster.deleted == []
    assert not hasattr(state, "run_scope")


def test_concurrent_namespace_open_stops_before_pod_and_cleans_only_its_creation(
    specification: PODS.ToolPodSpec,
) -> None:
    """A competing labeled create between apply and lookup never grants shared ownership."""
    selected = SCOPES.RunScope("concurrent-run", "selected-generated", True)
    competing = SCOPES.RunScope("concurrent-run", "other-generated", True)

    class ConcurrentCluster(MemoryCluster):
        """Inject one other creator at the actual namespace-apply boundary."""

        def apply(self, document: JSONObject) -> JSONObject:
            observed = super().apply(document)
            if (
                document["kind"] == "Namespace"
                and document["metadata"]["name"] == selected.namespace
            ):
                other = RESOURCES.ResourceSet(self, "", competing.run_id).managed(
                    SCOPES.namespace_document(competing)
                )
                super().apply(other)
            return observed

    cluster = ConcurrentCluster()
    state = _state(cluster)
    callbacks = (
        SCOPES.OpenRunScope(SCOPES.RunScopeAction(selected)),
        PODS.StageToolPod(
            PODS.PrepareToolPodAction(specification, run_id=selected.run_id)
        ),
    )
    with pytest.raises(
        KUBERNETES.KubernetesError, match="run_scope_post_create_not_unique"
    ):
        _run(state, callbacks)

    assert [kind for kind, _, _ in cluster.applied] == ["namespace", "namespace"]
    assert cluster.deleted == [("namespace", "", selected.namespace)]
    assert cluster.read("Namespace", selected.namespace, "") is None
    assert cluster.read("Namespace", competing.namespace, "") is not None
    assert cluster.executed == []
    assert state.cleanup_resources == [
        {
            "kind": "Namespace",
            "name": selected.namespace,
            "namespace": "",
            "status": STATUS.PASS,
            "absent": True,
        }
    ]


def test_remove_scope_preserves_selected_namespace_with_labeled_child() -> None:
    """A selected namespace is never adopted merely because its child carries run labels."""
    cluster = MemoryCluster()
    cluster.apply({"kind": "Namespace", "metadata": {"name": "consumer-owned"}})
    selected = SCOPES.RunScope("selected-run", "consumer-owned", False)
    child = RESOURCES.ResourceSet(cluster, selected.namespace, selected.run_id).managed(
        {
            "kind": "Pod",
            "metadata": {
                "name": "consumer-child",
                "namespace": selected.namespace,
                "labels": dict(selected.labels),
            },
        }
    )
    cluster.apply(child)
    before = deepcopy(cluster.objects)
    state = _state(cluster)
    lookup = SCOPES.ResolveRunScopeAction(selected.run_id)
    remove = SCOPES.RemoveScope(lookup)
    _run(state, (SCOPES.ResolveRunScopes(lookup), remove))
    assert state.results[remove]["action"] == STATUS.NO_OP
    assert state.results[remove]["preserved_scopes"] == [selected.as_dict()]
    assert cluster.objects == before
    assert cluster.deleted == []


@pytest.mark.parametrize("read_only", (False, True), ids=("writable", "read-only"))
def test_prepare_and_reuse_accept_api_omitted_writable_mount_default(
    specification: PODS.ToolPodSpec, read_only: bool
) -> None:
    """Native omission of false mount defaults must not reject an admitted Pod.

    :param specification: Existing tool fixture exercising the real lifecycle.
    :param read_only: Caller-selected mount flag; true remains explicit in read-back.
    """

    submitted: MutableSequence[JSONObject] = []

    class DefaultingCluster(MemoryCluster):
        """Model the observed API omission in both apply and independent reads."""

        def apply(self, document: JSONObject) -> JSONObject:
            """Store native default omissions while retaining exact submitted inputs.

            :param document: Generated resource submitted by the real ResourceSet.
            :returns: API response with false mount and PVC readOnly flags omitted.
            """
            if document["kind"] == "Pod":
                submitted.append(deepcopy(document))
            observed = super().apply(document)
            if observed["kind"] == "Pod":
                metadata = observed["metadata"]
                key = ("pod", metadata["namespace"], metadata["name"])
                stored = self.objects[key]
                for mount in stored["spec"]["containers"][0]["volumeMounts"]:
                    if mount.get("readOnly") is False:
                        del mount["readOnly"]
                for volume in stored["spec"]["volumes"]:
                    claim = volume.get("persistentVolumeClaim", {})
                    if claim.get("readOnly") is False:
                        del claim["readOnly"]
                return deepcopy(stored)
            return observed

    source = {"persistentVolumeClaim": {"claimName": "caller-data", "readOnly": False}}
    mount = PODS.ToolPodMount("caller-storage", source, "/shared", read_only=read_only)
    cluster = DefaultingCluster()
    first = _state(cluster)
    action = PODS.PrepareToolPodAction(specification, mounts=(mount,))

    _run(first, _preparation(action))

    original = first.pod
    observed = cluster.read("Pod", original.name, original.namespace)
    mounts = {
        item["name"]: item for item in observed["spec"]["containers"][0]["volumeMounts"]
    }
    assert mounts["ci-skills-scripts"]["readOnly"] is True
    assert mounts["caller-storage"].get("readOnly", False) is read_only
    assert mount.as_dict()["readOnly"] is read_only
    assert mount.as_dict()["volumeSource"] == source
    assert (
        submitted[0]["spec"]["containers"][0]["volumeMounts"][1]["readOnly"]
        is read_only
    )
    assert submitted[0]["spec"]["volumes"][1] == {"name": mount.name, **source}
    assert observed["spec"]["volumes"][1]["persistentVolumeClaim"] == {
        "claimName": "caller-data"
    }
    assert first.readback["tools"][0]["verified"] is True
    applied = tuple(cluster.applied)
    second = _state(cluster)
    repeated = PODS.PrepareToolPodAction(
        specification, run_id=first.run_scope.run_id, mounts=(mount,)
    )
    callbacks = _preparation(repeated)

    _run(second, callbacks)

    stage = next(
        callback for callback in callbacks if isinstance(callback, PODS.StageToolPod)
    )
    assert second.results[stage]["action"] == STATUS.NO_OP
    assert second.pod == original
    assert tuple(cluster.applied) == applied
    assert cluster.deleted == []


def test_render_preserves_caller_native_false_without_rewriting_volume_source(
    specification: PODS.ToolPodSpec,
) -> None:
    """Renderer and typed inputs retain explicit false values for native submission.

    :param specification: Existing parsed tool specification bound by the action.
    """
    source = {"persistentVolumeClaim": {"claimName": "caller-data", "readOnly": False}}
    mount = PODS.ToolPodMount("caller-storage", source, "/shared")
    action = PODS.PrepareToolPodAction(specification, mounts=(mount,))
    scope = SCOPES.RunScope("native-volume-run", "consumer-storage", False)

    document = POD_RESOURCES.tool_pod_document(scope, action)

    volume = next(
        item for item in document["spec"]["volumes"] if item["name"] == mount.name
    )
    assert volume == {"name": mount.name, **source}
    assert volume["persistentVolumeClaim"]["readOnly"] is False
    assert source == {
        "persistentVolumeClaim": {"claimName": "caller-data", "readOnly": False}
    }
    assert mount.as_dict() == {
        "name": "caller-storage",
        "volumeSource": source,
        "mountPath": "/shared",
        "readOnly": False,
    }


@pytest.mark.parametrize(
    ("kind", "field", "requested", "returned"),
    (
        ("Pod", "mount", False, True),
        ("Pod", "pvc", False, True),
        ("Pod", "claim", "caller-data", "different-data"),
        ("Pod", "claim", "caller-data", None),
        ("Pod", "mount", True, None),
        ("Pod", "pvc", True, None),
        ("Pod", "other", False, None),
        ("ConfigMap", "mount", False, None),
        ("ConfigMap", "pvc", False, None),
    ),
    ids=(
        "mount-true-drift",
        "pvc-true-drift",
        "claim-changed",
        "claim-missing",
        "required-read-only-mount-missing",
        "required-read-only-pvc-missing",
        "unrelated-false-missing",
        "non-pod-mount-false-missing",
        "non-pod-pvc-false-missing",
    ),
)
def test_native_default_matching_rejects_drift_and_unrelated_omissions(
    kind: str, field: str, requested: JSONValue, returned: JSONValue
) -> None:
    """Native omission equivalence must preserve requested access and resource identity.

    :param kind: Native Pod root or another root that must not gain Pod defaults.
    :param field: Requested access flag, claim identity, or unrelated boolean.
    :param requested: Exact caller value retained in the native desired document.
    :param returned: Provider value; None models an absent requested key.
    """
    expected = {
        "apiVersion": "v1",
        "kind": kind,
        "spec": {
            "automountServiceAccountToken": False,
            "containers": [{"volumeMounts": [{"readOnly": False}]}],
            "volumes": [
                {
                    "persistentVolumeClaim": {
                        "claimName": "caller-data",
                        "readOnly": False,
                    }
                }
            ],
        },
    }
    paths: dict[str, tuple[Union[str, int], ...]] = {
        "mount": ("spec", "containers", 0, "volumeMounts", 0, "readOnly"),
        "pvc": ("spec", "volumes", 0, "persistentVolumeClaim", "readOnly"),
        "claim": ("spec", "volumes", 0, "persistentVolumeClaim", "claimName"),
        "other": ("spec", "automountServiceAccountToken"),
    }
    observed = deepcopy(expected)
    requested_parent, observed_parent = expected, observed
    for key in paths[field][:-1]:
        requested_parent = requested_parent[key]
        observed_parent = observed_parent[key]
    final_key = paths[field][-1]
    requested_parent[final_key] = requested
    if returned is None:
        del observed_parent[final_key]
    else:
        observed_parent[final_key] = returned

    assert not RESOURCES.matches(expected, observed)
