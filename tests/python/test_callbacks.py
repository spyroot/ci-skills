"""Callback lifecycle regression coverage for composed skill operations.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
from argparse import Namespace
from collections.abc import Iterable, Mapping, MutableMapping, MutableSequence, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import TYPE_CHECKING, Optional, TypeAlias, Union

import pytest
from conftest import SKILL_ROOT, import_script_module

ACTION = import_script_module("lib.python.core.gitlab.gitlab_actions")
CALLBACKS = import_script_module("lib.python.core.abstract.callback")
CALLBACK_COLLECTIONS = import_script_module("lib.python.core.abstract.callbacks")
API_READ = import_script_module("lib.python.core.api_read")
READ_CLI = import_script_module("lib.python.core.read_cli")
BINARY_BUILD = import_script_module("lib.python.core.binary_build")
CONTEXT = import_script_module("lib.python.core.abstract.context")
DELIVERY_STATE = import_script_module("lib.python.core.delivery_state")
ABSTRACT_EXECUTOR = import_script_module("lib.python.core.abstract.executor")
EXECUTOR = import_script_module("lib.python.core.executor")
GITLAB_CALLBACK = import_script_module("lib.python.core.gitlab.callback")
GITHUB_RUNNER = import_script_module("lib.python.core.github.runner")
GITLAB_SESSION = import_script_module("lib.python.core.gitlab.gitlab_session")
JSON_TYPES = import_script_module("lib.python.core.json_types")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")
REGISTRY = import_script_module("lib.python.core.harbor.registry_mirror")
RESOURCES = import_script_module("lib.python.core.kubernetes.resources")
RUNTIME = import_script_module("lib.python.core.runtime")
RUNNER_CORE = import_script_module("lib.python.core.kubernetes.runner")
RUNNERS = import_script_module("lib.python.core.gitlab.gitlab_runners")
STATUS = import_script_module("lib.python.sys.status")
SKILL_ACTION = import_script_module("lib.python.core.abstract.skill_action")
SKILL_PLUGIN_TYPE = import_script_module("lib.python.core.abstract.skill_plugin_type")
SKILL_STATE = import_script_module("lib.python.core.abstract.skill_state")
TARGET = import_script_module("lib.python.core.target")
TOOLBOX = import_script_module("lib.python.core.toolbox")
TOOLBOX_ACTIONS = import_script_module("lib.python.core.toolbox_actions")


if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue


CallbackInput: TypeAlias = Union[
    CALLBACKS.AbstractCallback,
    Iterable[CALLBACKS.AbstractCallback],
]


ToolboxBuildEvent: TypeAlias = Union[
    tuple[str, bool, str],
    tuple[str, str],
    tuple[str, str, str],
    tuple[str, str, str, str],
    tuple[str, tuple[str, ...], bool],
]


@dataclass(frozen=True)
class RecordedAction(SKILL_ACTION.SkillAction):
    """Name one synthetic callback operation.

    :param name: Operation label recorded in lifecycle events.

    .. attribute :: name
        :type: str

        Operation label recorded in lifecycle events.
    """

    name: str


class Recorded(CALLBACKS.LocalCallback[CONTEXT.ExecutionContext, RecordedAction]):
    """Record lifecycle calls and optionally return, stop, or fail."""

    def __init__(
        self,
        events: MutableSequence[str],
        name: str,
        *,
        value: Optional[JSONObject] = None,
        collect_error: Optional[Exception] = None,
        cleanup_error: Optional[Exception] = None,
        complete: bool = True,
    ):
        """Configure a callback double that records each lifecycle hook.

        :param events: Shared list receiving hook names in execution order.
        :param name: Prefix identifying this callback in the event list.
        :param value: Result returned by ``run``.
        :param collect_error: Exception raised by ``collect`` when supplied.
        :param cleanup_error: Exception raised by ``cleanup`` when supplied.
        :param complete: Completion flag observed by the executor.
        """
        super().__init__(RecordedAction(name))
        self.events = events
        self.name = name
        self.value = MappingProxyType({}) if value is None else value
        self.collect_error = collect_error
        self.cleanup_error = cleanup_error
        self._complete = complete

    @property
    def complete(self) -> bool:
        """Expose the configured completion flag to the executor.

        :returns: Whether dependent callbacks may continue.
        """
        return self._complete

    def collect(self) -> JSONObject:
        """Record collection and optionally fail before ``run``.

        :returns: Empty collection evidence.
        :raises Exception: Configured collection failure supplied by the test.
        """  # noqa: DOC503 - The dynamic fixture exception is raised verbatim.
        self.events.append(f"{self.name}.collect")
        if self.collect_error is not None:
            raise self.collect_error
        return {}

    def run(self) -> JSONObject:
        """Record execution and return the configured fixture value.

        :returns: Mapping passed to the next callback through ``result``.
        """
        self.events.append(f"{self.name}.run")
        return self.value

    def update(self) -> JSONObject:
        """Record the post-run update hook in the shared event list.

        :returns: This callback's run result.
        """
        self.events.append(f"{self.name}.update")
        return self.result

    def cleanup(self) -> JSONObject:
        """Record cleanup and optionally expose a cleanup failure.

        :returns: Current shared workflow result when cleanup succeeds.
        :raises Exception: Configured cleanup failure supplied by the test.
        """  # noqa: DOC503 - The dynamic fixture exception is raised verbatim.
        self.events.append(f"{self.name}.cleanup")
        if self.cleanup_error is not None:
            raise self.cleanup_error
        return self.state.result


def _callback_executor(
    callbacks: CallbackInput, context: Optional[SKILL_STATE.SkillState] = None
) -> ABSTRACT_EXECUTOR.SkillExecutor:
    """Create an executor for the current callback abstraction.

    :param callbacks: One callback or an ordered iterable of callbacks.
    :param context: Optional shared execution context supplied by workflow tests.
    :returns: Standard or workflow executor with callbacks normalized.
    """
    collection = CALLBACK_COLLECTIONS.Callbacks(callbacks)
    if context is None:
        return EXECUTOR.Executor(callbacks=collection)
    return EXECUTOR.WorkflowExecutor(context=context, callbacks=collection)


def _run_callbacks(
    callbacks: CallbackInput, context: Optional[SKILL_STATE.SkillState] = None
) -> JSONObject:
    """Run callbacks through the new state-bound executor.

    :param callbacks: One callback or an ordered iterable of callbacks.
    :param context: Optional shared execution context supplied by workflow tests.
    :returns: Final executor result mapping.
    """
    return _callback_executor(callbacks, context=context).run()


class OpenShiftReadBack:
    """Minimal native API double that can omit ``omitempty`` BuildConfig fields."""

    def __init__(self, observed_spec: Optional[JSONObject] = None):
        """Set the BuildConfig spec returned after an apply.

        :param observed_spec: Replacement read-back spec; absent means omit the
            empty ``triggers`` field from the applied spec.
        """
        self.applies = 0
        self.observed_spec = observed_spec
        self.resource: Optional[MutableMapping[str, JSONValue]] = None

    def read(
        self, _kind: str, _name: str, _namespace: str
    ) -> Optional[MutableMapping[str, JSONValue]]:
        """Return a detached copy of the last applied BuildConfig.

        :param _kind: Resource kind accepted by the service double.
        :param _name: Resource name accepted by the service double.
        :param _namespace: Namespace accepted by the service double.
        :returns: Copied resource or ``None`` before the first apply.
        """
        return deepcopy(self.resource)

    def apply(self, document: JSONObject) -> None:
        """Store a server-style BuildConfig read-back with a UID.

        :param document: Applied BuildConfig whose spec is copied or replaced.

        :returns: None; the helper records behavior through its side effects.
        """
        self.applies += 1
        observed = deepcopy(document)
        metadata = observed["metadata"]
        assert isinstance(metadata, dict)
        metadata["uid"] = "buildconfig-uid"
        spec = observed["spec"]
        assert isinstance(spec, dict)
        if self.observed_spec is None:
            spec.pop("triggers", None)
        else:
            observed["spec"] = deepcopy(self.observed_spec)
        self.resource = observed


def _build_config(spec: JSONObject) -> MutableMapping[str, JSONValue]:
    """Wrap a test spec in a BuildConfig document for resource read-back.

    :param spec: Desired BuildConfig spec, including optional triggers.
    :returns: BuildConfig mapping with the fixed fixture name and API version.
    """
    return {
        "apiVersion": "build.openshift.io/v1",
        "kind": "BuildConfig",
        "metadata": {"name": "toolbox"},
        "spec": spec,
    }


def test_resource_set_omitted_empty_buildconfig_triggers_is_applied_then_no_op() -> (
    None
):
    """OpenShift omits an empty BuildConfig ``spec.triggers`` on read-back.
    :returns: None; assertions in the body validate the regression.
    """
    service = OpenShiftReadBack()
    resources = RESOURCES.ResourceSet(service, "toolbox-ns", "toolbox-build")
    spec = {"runPolicy": "Serial", "triggers": []}

    applied = resources.apply(_build_config(spec))
    repeated = resources.apply(_build_config(spec))

    assert applied["action"] == STATUS.APPLIED
    assert repeated["action"] == STATUS.NO_OP
    assert service.applies == 1


@pytest.mark.parametrize(
    ("spec", "observed_spec"),
    (
        ({"triggers": [{"type": "ConfigChange"}]}, {}),
        ({"triggers": []}, {"triggers": {}}),
        ({"runPolicy": "Serial"}, {}),
    ),
    ids=("missing-nonempty-list", "wrong-present-list-type", "missing-scalar"),
)
def test_resource_set_rejects_nonempty_or_wrong_buildconfig_readback(
    spec: JSONObject, observed_spec: Optional[JSONObject]
) -> None:
    """Reject missing required fields and a wrong-type triggers read-back.

    :param spec: Desired BuildConfig spec supplied to ``ResourceSet``.
    :param observed_spec: Server-style spec returned by the API double.

    :returns: None; assertions in the body validate the regression.
    """
    resources = RESOURCES.ResourceSet(
        OpenShiftReadBack(observed_spec), "toolbox-ns", "toolbox-build"
    )

    with pytest.raises(RESOURCES.KubernetesError, match="resource_readback_mismatch"):
        resources.apply(_build_config(spec))


def test_resource_set_apply_does_not_mutate_caller_owned_mapping() -> None:
    """Apply a copied desired resource while preserving nested read-only input.
    :returns: None; assertions in the body validate the regression.
    """
    labels = MappingProxyType({"caller": "owned"})
    metadata = MappingProxyType({"name": "runner", "labels": labels})
    spec = MappingProxyType({"replicas": 1})
    desired = MappingProxyType(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": metadata,
            "spec": spec,
        }
    )

    class KubernetesService:
        """Mock Kubernetes read/apply while exposing applied desired state."""

        def __init__(self):
            """Start with no existing resource and no recorded apply calls."""
            self.applied: MutableSequence[JSONObject] = []

        def read(self, kind: str, name: str, namespace: str) -> Optional[JSONObject]:
            """Return absence before apply and a provider read-back after apply.

            :param kind: Resource kind requested by ``ResourceSet``.
            :param name: Resource name requested by ``ResourceSet``.
            :param namespace: Namespace requested by ``ResourceSet``.
            :returns: ``None`` before apply, then the applied resource with a UID.
            """
            assert (kind, name, namespace) == ("Deployment", "runner", "unit-ns")
            if not self.applied:
                return
            observed = deepcopy(self.applied[-1])
            metadata = observed["metadata"]
            assert isinstance(metadata, dict)
            metadata["uid"] = "deployment-uid"
            return observed

        def apply(self, document: JSONObject) -> None:
            """Record the operation-owned document submitted to Kubernetes.

            :param document: Desired resource after ResourceSet ownership labeling.
            :returns: None after recording a detached copy.
            """
            self.applied.append(deepcopy(document))

    service = KubernetesService()
    resources = RESOURCES.ResourceSet(service, "unit-ns", "unit-operation")

    evidence = resources.apply(desired)

    assert metadata["labels"] == {"caller": "owned"}
    assert RESOURCES.MANAGER_LABEL not in labels
    assert spec == {"replicas": 1}
    assert evidence == {
        "kind": "Deployment",
        "name": "runner",
        "namespace": "unit-ns",
        "uid": "deployment-uid",
        "action": STATUS.APPLIED,
    }
    labels = service.applied[0]["metadata"]["labels"]
    assert labels == {
        "caller": "owned",
        RESOURCES.MANAGER_LABEL: RESOURCES.MANAGER_VALUE,
        RESOURCES.OPERATION_LABEL: "unit-operation",
    }


def test_json_helpers_copy_immutable_inputs_and_reject_wrong_shapes() -> None:
    """Shared JSON helpers copy read-only containers and reject wrong shapes.
    :returns: None; assertions in the body validate the regression.
    """
    nested = MappingProxyType(
        {"items": (MappingProxyType({"name": "unit"}),), "enabled": True}
    )

    copied = JSON_TYPES.copy_json_object(nested)
    assert copied == {"items": [{"name": "unit"}], "enabled": True}
    items = copied["items"]
    assert isinstance(items, list)
    item = items[0]
    assert isinstance(item, MutableMapping)
    item["name"] = "changed"

    assert nested["items"][0]["name"] == "unit"
    with pytest.raises(TypeError, match="JSON object required"):
        JSON_TYPES.json_object([])
    with pytest.raises(TypeError, match="JSON array required"):
        JSON_TYPES.json_array({})
    assert JSON_TYPES.json_string("unit") == "unit"
    assert JSON_TYPES.json_integer(7) == 7
    with pytest.raises(TypeError, match="JSON string required"):
        JSON_TYPES.json_string(7)
    with pytest.raises(TypeError, match="JSON integer required"):
        JSON_TYPES.json_integer(True)
    with pytest.raises(TypeError, match="JSON integer required"):
        JSON_TYPES.json_integer("7")
    with pytest.raises(TypeError, match="JSON string required"):
        JSON_TYPES.json_strings(("ok", 7))


@pytest.mark.parametrize(
    ("url", "suffix"),
    (
        (
            "https://repo.example.test/Miniconda3-py311_26.7.1-1-Linux-x86_64.sh?download=1",
            ".sh",
        ),
        ("https://repo.example.test/installers/toolbox.run?signature=unit", ".run"),
    ),
    ids=("miniconda-query", "generic-installer-query"),
)
def test_linux_installer_preserves_url_path_suffix(url: str, suffix: str) -> None:
    """Keep installer suffixes from URL paths while excluding query text.

    :param url: Installer URL, with a query that must remain only in the download.
    :param suffix: Path extension expected on the staged installer artifact.

    :returns: None; assertions in the body validate the regression.
    """
    install = TOOLBOX.LinuxInstall.from_document(
        {
            "method": "installer",
            "url": url,
            "sha256": "a" * 64,
            "interpreter": "bash",
            "args": ["-b", "-p", "/opt/conda"],
        }
    )

    operation = install._operation(7)
    artifact = f"/tmp/ci-skills-dependency-7{suffix}"

    assert operation.count(artifact) == 4
    assert f"--output {artifact} " in operation
    assert f"bash {artifact} -b -p /opt/conda" in operation
    assert f"{artifact}?" not in operation
    assert url in operation


def test_conda_render_scopes_channels_without_unsupported_create_options() -> None:
    """Conda channels use its supported process environment, not create args.
    :returns: None; assertions in the body validate the regression.
    """
    prefix = "/opt/conda/envs/ci-skills"
    conda = TOOLBOX.CondaInput(
        content=b"name: unit\n",
        prefix=prefix,
        channels=("conda-forge", "pytorch"),
    )

    _, instruction = conda._render()
    channels_environment, defaults_environment, *argv = shlex.split(
        instruction.splitlines()[-1].strip()
    )

    assert channels_environment == (f"{TOOLBOX.CONDA_CHANNELS_ENV}=conda-forge,pytorch")
    assert defaults_environment == (
        f"{TOOLBOX.CONDA_DEFAULT_CHANNELS_ENV}=conda-forge,pytorch"
    )
    assert argv == [
        "conda",
        "env",
        "create",
        "--file",
        TOOLBOX.CONDA_PATH,
        "--prefix",
        prefix,
    ]


def test_toolbox_build_starts_by_name_then_reads_build_as_json() -> None:
    """The native build command returns a name that is read back independently.
    :returns: None; assertions in the body validate the regression.
    """
    events: MutableSequence[ToolboxBuildEvent] = []
    digest = "sha256:" + "b" * 64
    reference = "build.build.openshift.io/toolbox-1"

    class Spec:
        """Supply a fixed toolbox build specification and record context rendering.

        .. attribute :: namespace
            :type: str

            Namespace used for generated BuildConfig resources.

        .. attribute :: app
            :type: str

            Application name rendered into generated resource labels.

        .. attribute :: fingerprint
            :type: str

            Build specification fingerprint expected by the callback.
        """

        namespace: str = "toolbox-ns"
        app: str = "toolbox"
        fingerprint: str = "a" * 64

        def render(self, directory: Path, resolved_base_image: str) -> None:
            """Record that build assets render in an existing directory.

            :param directory: Temporary build context passed by ``BuildToolbox``.
            :param resolved_base_image: Immutable base image selected by the action.

            :returns: None; the helper records behavior through its side effects.
            """
            events.append(("render", directory.is_dir(), resolved_base_image))

        def _node_selector(self) -> None:
            """Supply no node selector for this BuildConfig fixture.

            :returns: ``None`` so the build uses its default scheduling policy.
            """
            return

    class KubernetesService:
        """Record native build start, JSON read-back, and completed digest wait."""

        def __init__(self):
            """Start with no applied BuildConfig read-back state."""
            self.applied: Optional[JSONObject] = None

        def read(self, kind: str, name: str, namespace: str) -> Optional[JSONObject]:
            """Record an attempted resource read before the build starts.

            :param kind: Kubernetes resource kind requested by the callback.
            :param name: Resource name requested by the callback.
            :param namespace: Namespace requested by the callback.
            :returns: ``None`` before apply, then the applied object with a UID.
            """
            events.append(("read", kind, name, namespace))
            if self.applied is None:
                return
            observed = deepcopy(self.applied)
            metadata = observed["metadata"]
            assert isinstance(metadata, dict)
            metadata["uid"] = "buildconfig-uid"
            return observed

        def apply(self, document: JSONObject) -> None:
            """Record an applied BuildConfig for independent read-back.

            :param document: Resource document submitted by ``ResourceSet``.
            :returns: None after storing a detached copy.
            """
            events.append(("apply", document["kind"]))
            self.applied = deepcopy(document)

        def command(self, *args: str, openshift: bool = False) -> RUNTIME.CommandResult:
            """Record a native start-build command and return its resource name.

            :param *args: Positional arguments forwarded to the helper under test.
            :param openshift: Whether the callback selects the OpenShift CLI.
            :returns: Successful result containing the new Build resource name.

            """
            events.append(("command", args, openshift))
            return RUNTIME.CommandResult(tuple(args), 0, reference + "\n", "")

        def json(self, *args: str, openshift: bool = False) -> JSONObject:
            """Record the independent Build JSON read-back.

            :param *args: Positional arguments forwarded to the helper under test.
            :param openshift: Whether the callback selects the OpenShift CLI.
            :returns: Build metadata identifying the started resource.

            """
            events.append(("json", args, openshift))
            return {"metadata": {"name": "toolbox-1"}}

        def wait_build(self, name: str, namespace: str) -> JSONObject:
            """Record the wait and return a successful output digest.

            :param name: Started Build name selected from JSON read-back.
            :param namespace: Namespace in which the Build is awaited.
            :returns: Build status containing the published image digest.
            """
            events.append(("wait_build", name, namespace))
            return {"status": {"output": {"to": {"imageDigest": digest}}}}

    spec = Spec()
    service = KubernetesService()
    action = TOOLBOX.BuildAction(
        spec=spec,
        resolved_base_image="registry.example.test/base@sha256:" + "c" * 64,
        candidate_repository="image-registry.example.test/unit/toolbox",
        candidate_reference="candidate",
        dockerconfig=b'{"auths":{"registry.example.test":{}}}',
        digest_lookup=lambda _reference: digest,
    )
    callback = TOOLBOX.BuildToolbox(action=action)
    state = DELIVERY_STATE.DeliveryState()
    state.kubernetes = service
    callback.bind(state)

    result = callback.run()

    start = next(item for item in events if item[0] == "command")
    readback = next(item for item in events if item[0] == "json")
    assert start == (
        "command",
        (
            "--namespace",
            "toolbox-ns",
            "start-build",
            "buildconfig/toolbox",
            start[1][4],
            "-o",
            "name",
        ),
        True,
    )
    assert start[1][4].startswith("--from-dir=")
    assert readback == (
        "json",
        ("--namespace", "toolbox-ns", "get", reference, "-o", "json"),
        False,
    )
    assert events.index(start) < events.index(readback)
    assert result["build_name"] == "toolbox-1"
    assert result["digest"] == digest
    assert result["verified"] is True
    assert state.build_result.build_name == "toolbox-1"
    assert state.build_result.digest == digest
    assert state.build_result.verified is True


def test_abstract_hooks_are_required_but_callback_defaults_are_optional() -> None:
    """Concrete atoms can opt into only the hooks their operation needs.
    :returns: None; assertions in the body validate the regression.
    """
    with pytest.raises(TypeError):
        CALLBACKS.AbstractCallback(RecordedAction("abstract"))

    result = _run_callbacks(CALLBACKS.LocalCallback(RecordedAction("default")))

    assert result == {}


def test_executor_orders_hooks_and_passes_typed_callback_results() -> None:
    """A later atom reads the earlier result without another workflow helper.
    :returns: None; assertions in the body validate the regression.
    """
    events: MutableSequence[str] = []
    producer = Recorded(events, "producer", value={"value": 7})

    class Consumer(Recorded):
        """Consume the preceding typed callback value during ordered execution."""

        def run(self) -> JSONObject:
            """Read the producer result after its hooks and increment it.

            :returns: Producer's integer result plus one in a result mapping.
            """
            self.events.append("consumer.run")
            value = producer.result["value"]
            assert isinstance(value, int)
            return {"value": value + 1}

    consumer = Consumer(events, "consumer")
    result = _run_callbacks([producer, consumer])

    assert result == {"value": 8}
    assert events == [
        "producer.collect",
        "producer.run",
        "producer.update",
        "consumer.collect",
        "consumer.run",
        "consumer.update",
        "consumer.cleanup",
        "producer.cleanup",
    ]


def test_incomplete_callback_stops_later_callbacks_and_cleans_up() -> None:
    """Incomplete read-back never allows dependent work to start.
    :returns: None; assertions in the body validate the regression.
    """
    events: MutableSequence[str] = []
    incomplete = Recorded(
        events, "incomplete", value={"verified": False}, complete=False
    )
    later = Recorded(events, "later")

    _run_callbacks([incomplete, later])

    assert events == [
        "incomplete.collect",
        "incomplete.run",
        "incomplete.update",
        "incomplete.cleanup",
    ]
    assert later.result == {}


@pytest.mark.parametrize(
    "record",
    (
        {"verified": True, "errors": [{"reason": "readback failed"}]},
        {"verified": True, "cleanup": {"status": "BLOCKED"}},
    ),
)
def test_incomplete_gitlab_result_stops_later_callbacks(record: JSONObject) -> None:
    """Stop dependent work for provider errors or blocked cleanup.

    :param record: Verified-looking GitLab result with an incomplete field.

    :returns: None; assertions in the body validate the regression.
    """
    events: MutableSequence[str] = []

    class ResultCallback(GITLAB_CALLBACK.GitlabCallback[RecordedAction]):
        """Return incomplete GitLab evidence and record reverse cleanup."""

        def run(self) -> JSONObject:
            """Publish the parametrized provider result to the executor.

            :returns: Record containing an error or blocked cleanup status.
            """
            events.append("result.run")
            return record

        def cleanup(self) -> JSONObject:
            """Record cleanup after the incomplete result stops the chain.

            :returns: The shared provider result preserved for later report rendering.
            """
            events.append("result.cleanup")
            return self.state.result

    later = Recorded(events, "later")
    state = GITLAB_CALLBACK.GitLabState()
    state.gitlab = SimpleNamespace(session=object())
    _run_callbacks(
        [ResultCallback(RecordedAction("result")), later],
        context=state,
    )

    assert events == ["result.run", "result.cleanup"]
    assert later.result == {}


def test_provider_bases_use_prebound_gitlab_and_reject_missing_harbor_at_bind() -> None:
    """Require concrete provider configuration before either callback lifecycle.

    :returns: None; assertions prove real provider bases use shared state fields.
    """
    state = DELIVERY_STATE.DeliveryState()
    state.gitlab = SimpleNamespace(session=object())
    gitlab = GITLAB_CALLBACK.GitlabCallback(RecordedAction("gitlab"))
    harbor = REGISTRY.HarborCallback(RecordedAction("harbor"))

    with pytest.raises(TARGET.TargetError, match="harbor_configuration_missing"):
        CALLBACK_COLLECTIONS.Callbacks([gitlab, harbor]).bind(state)

    assert state.configured_session is state.gitlab.session
    assert state.is_config_checked(SKILL_PLUGIN_TYPE.SkillPluginType.GITLAB)
    assert not state.is_config_checked(SKILL_PLUGIN_TYPE.SkillPluginType.HARBOR)
    assert state.records == []
    assert state.results[gitlab] == {}
    assert state.results[harbor] == {}


def test_kubernetes_auth_rejects_non_kubernetes_bound_sources_before_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a prebound source record that lacks Kubernetes credentials.

    :param monkeypatch: Pytest fixture proving no fallback credential bind occurs.
    :returns: None; assertions prove configuration fails before provider lifecycle.
    """
    sources = import_script_module("lib.python.access.sources")
    target = TARGET.Target(
        github=None,
        gitlab=None,
        kubernetes=TARGET.KubernetesTarget(
            context="unit-context",
            server="https://api.example.test:6443",
            kubeconfig=None,
        ),
        active_surfaces=(SKILL_PLUGIN_TYPE.SkillPluginType.KUBERNETES.value,),
        sources=sources.Sources(
            github=sources.CredentialSource("env:GH_TOKEN", {}),
            gitlab=None,
            kubernetes=None,
            kubeconfig_files=(),
            execution_host="unit-host",
        ),
    )
    auth = KUBERNETES.K8SAuth(target)
    state = DELIVERY_STATE.DeliveryState()

    def unexpected_bind(*_args: object, **_kwargs: object) -> object:
        """Fail when K8SAuth incorrectly attempts to replace bound sources.

        :param _args: Positional arguments passed to the unexpected binder.
        :param _kwargs: Keyword arguments passed to the unexpected binder.
        :returns: This function never returns.
        :raises Failed: Always, because prebound sources must not be replaced.
        """
        pytest.fail("K8SAuth replaced prebound credential sources")

    monkeypatch.setattr(KUBERNETES, "bind_sources", unexpected_bind)

    with pytest.raises(ValueError, match="required_credential_source_missing"):
        CALLBACK_COLLECTIONS.Callbacks([auth]).bind(state)

    assert state.records == []
    assert state.results[auth] == {}
    assert not state.is_config_checked(SKILL_PLUGIN_TYPE.SkillPluginType.KUBERNETES)
    assert not hasattr(state, "kubernetes_target")
    assert not hasattr(state, "kubernetes")


def test_failure_stops_callbacks_and_retains_all_reverse_cleanup_evidence() -> None:
    """A failing collect still cleans itself and every earlier entered callback.
    :returns: None; assertions in the body validate the regression.
    """
    events: MutableSequence[str] = []
    first = Recorded(events, "first", cleanup_error=RuntimeError("cleanup first"))
    primary = ValueError("collect failed")
    failing = Recorded(
        events,
        "failing",
        collect_error=primary,
        cleanup_error=RuntimeError("cleanup failing"),
    )
    later = Recorded(events, "later")
    executor = _callback_executor([first, failing, later])

    with pytest.raises(ValueError, match="collect failed") as raised:
        executor.run()

    assert raised.value is primary
    assert events == [
        "first.collect",
        "first.run",
        "first.update",
        "failing.collect",
        "failing.cleanup",
        "first.cleanup",
    ]
    assert executor.state.cleanup_failures == [
        {"callback": "Recorded", "reason": "cleanup failing"},
        {"callback": "Recorded", "reason": "cleanup first"},
    ]
    assert "cleanup failed: RuntimeError" in raised.value.__notes__
    assert "later.collect" not in events


def test_debug_trace_records_failure_and_reverse_cleanup(tmp_path: Path) -> None:
    """Require compact failure trace records and reverse-order cleanup.

    :param tmp_path: Directory receiving the JSON-lines execution trace.

    :returns: None; assertions in the body validate the regression.
    """
    lifecycle: MutableSequence[str] = []
    trace_file = tmp_path / "trace.jsonl"
    first = Recorded(lifecycle, "first", value={"status": "PASS"})
    failing = Recorded(
        lifecycle,
        "failing",
        collect_error=RuntimeError("collect failed"),
    )
    context = CONTEXT.ExecutionContext(
        args=Namespace(
            action="run",
            apply=True,
            kind="unit-workflow",
            log_file=str(trace_file),
            log_format="json",
            log_level="debug",
            run_id="unit-trace",
        )
    )

    with pytest.raises(RuntimeError, match="collect failed"):
        _run_callbacks([first, failing], context=context)

    records = [json.loads(line) for line in trace_file.read_text().splitlines()]
    details = [(record["event"], record["details"]) for record in records]
    run_completed = next(
        detail
        for event, detail in details
        if event == "callback.completed" and detail["hook"] == "run"
    )
    failure = next(detail for event, detail in details if event == "callback.failed")

    assert lifecycle[-2:] == ["failing.cleanup", "first.cleanup"]
    assert failure == {
        "callback": "Recorded",
        "hook": "collect",
        "reason": "collect failed",
        "status": "BLOCKED",
    }
    assert 0 <= run_completed["elapsed_time_ms"]
    assert set(run_completed) == {
        "callback",
        "elapsed_time_ms",
        "hook",
        "status",
    }
    assert RUNTIME.trace_enabled() is False


def test_executor_disables_default_trace_and_restores_the_caller_context() -> None:
    """A normal workflow is quiet and restores an outer trace after it exits.
    :returns: None; assertions in the body validate the regression.
    """
    observed: MutableSequence[tuple[str, MutableMapping[str, JSONValue]]] = []
    lifecycle: MutableSequence[str] = []

    with RUNTIME.execution_trace(
        lambda event, details: observed.append((event, details))
    ):
        _run_callbacks(Recorded(lifecycle, "quiet"))
        RUNTIME.trace_event("after.workflow", {"status": "PASS"})

    assert observed == [("after.workflow", {"status": "PASS"})]
    assert RUNTIME.trace_enabled() is False


def test_command_trace_redacts_secret_argument_and_omits_provider_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require command traces to redact password and provider output.

    :param monkeypatch: Pytest fixture replacing the subprocess runner.

    :returns: None; assertions in the body validate the regression.
    """
    monkeypatch.setattr(
        RUNTIME.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 23, "provider-output", "token=hidden"
        ),
    )
    observed: MutableSequence[tuple[str, MutableMapping[str, JSONValue]]] = []

    with RUNTIME.execution_trace(
        lambda event, details: observed.append((event, details))
    ):
        result = RUNTIME.run_command(
            ["provider", "--password", "raw-secret", "--name", "unit"]
        )

    assert result.returncode == 23
    assert observed == [
        (
            "command.complete",
            {
                "argv": [
                    "provider",
                    "--password",
                    "[REDACTED]",
                    "--name",
                    "unit",
                ],
                "returncode": 23,
                "stdout_bytes": len(b"provider-output"),
                "stderr": "token=[REDACTED]",
            },
        )
    ]


def test_wait_build_fails_terminal_phase_without_sleep_and_traces_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail on terminal Build status and capture bounded logs immediately.

    :param monkeypatch: Pytest fixture replacing Build reads, sleep, and logs.

    :returns: None; assertions in the body validate the regression.
    """
    target = TARGET.Target(
        github=None,
        gitlab=None,
        kubernetes=TARGET.KubernetesTarget(
            context="unit-context",
            server="https://api.example.test:6443",
            kubeconfig=None,
        ),
    )
    service = KUBERNETES.KubernetesService(target=target, timeout=30)
    monkeypatch.setattr(
        service,
        "read",
        lambda _kind, _name, _namespace: {"status": {"phase": "Failed"}},
    )
    monkeypatch.setattr(
        KUBERNETES.time,
        "sleep",
        lambda _seconds: pytest.fail("terminal Build phase must not sleep"),
    )
    log_limits: MutableMapping[str, int] = {}

    def capture_logs(
        *args: str,
        **kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> RUNTIME.CommandResult:
        """Capture requested log limits and return a failed-build excerpt.

        :param *args: Positional arguments forwarded to the helper under test.
        :param **kwargs: Keyword arguments forwarded to the fake command boundary.
        :returns: Successful tail-command result containing the fixture error line.

        """
        log_limits.update(max_bytes=kwargs["max_bytes"], max_lines=kwargs["max_lines"])
        return RUNTIME.CommandResult(tuple(args[0]), 0, "compile failed\n", "")

    monkeypatch.setattr(KUBERNETES, "run_command_tail", capture_logs)
    observed: MutableSequence[tuple[str, MutableMapping[str, JSONValue]]] = []

    with (
        RUNTIME.execution_trace(
            lambda event, details: observed.append((event, details))
        ),
        pytest.raises(KUBERNETES.KubernetesError, match="build_phase_failed"),
    ):
        service.wait_build("toolbox-1", "toolbox-ns")

    assert observed[0] == (
        "build.phase",
        {"name": "toolbox-1", "namespace": "toolbox-ns", "phase": "Failed"},
    )
    assert observed[-1] == (
        "build.output",
        {
            "name": "toolbox-1",
            "namespace": "toolbox-ns",
            "returncode": 0,
            "stdout": "compile failed\n",
            "stderr": "",
        },
    )
    assert log_limits == {"max_bytes": 4096, "max_lines": 40}


def test_wait_build_complete_does_not_capture_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require complete Build read-back without a failure-log fetch.

    :param monkeypatch: Pytest fixture replacing Build reads and log collection.

    :returns: None; assertions in the body validate the regression.
    """
    target = TARGET.Target(
        github=None,
        gitlab=None,
        kubernetes=TARGET.KubernetesTarget(
            context="unit-context",
            server="https://api.example.test:6443",
            kubeconfig=None,
        ),
    )
    service = KUBERNETES.KubernetesService(target=target, timeout=30)
    build = {"status": {"phase": "Complete"}}
    monkeypatch.setattr(
        service,
        "read",
        lambda _kind, _name, _namespace: build,
    )
    monkeypatch.setattr(
        KUBERNETES,
        "run_command_tail",
        lambda *args, **kwargs: pytest.fail("successful Build must not read logs"),
    )
    observed: MutableSequence[tuple[str, MutableMapping[str, JSONValue]]] = []

    with RUNTIME.execution_trace(
        lambda event, details: observed.append((event, details))
    ):
        result = service.wait_build("toolbox-1", "toolbox-ns")

    assert result is build
    assert observed == [
        (
            "build.phase",
            {"name": "toolbox-1", "namespace": "toolbox-ns", "phase": "Complete"},
        )
    ]


@pytest.mark.parametrize(
    ("existing_digest", "expected_deletes", "expected_copies"),
    (
        (None, [("toolbox", "smoke")], 1),
        ("sha256:preexisting", [], 2),
    ),
)
def test_harbor_push_failure_cleanup_restores_prior_tag_or_removes_new_tag(
    existing_digest: Optional[str],
    expected_deletes: Sequence[tuple[str, ...]],
    expected_copies: int,
) -> None:
    """Restore an overwritten tag or remove one created before downstream failure.

    :param existing_digest: Digest originally assigned to the destination tag.
    :param expected_deletes: Tag deletions expected during cleanup.
    :param expected_copies: Number of push and possible restore copies.

    :returns: None; assertions in the body validate the regression.
    """
    published_digest = "sha256:published"

    class HarborService:
        """Track one destination tag through push, restoration, or removal."""

        def __init__(self):
            """Seed destination-tag state and operation journals for cleanup checks."""
            self.current = existing_digest
            self.deleted: MutableSequence[tuple[str, str]] = []
            self.copies: MutableSequence[tuple[str, str]] = []
            self.target = object()
            self.credential = object()

        def inspect(self, image: str) -> str:
            """Resolve a source digest or the published fixture digest.

            :param image: Image reference inspected by the push callback.
            :returns: Digest suffix or the fixture digest for a tag reference.
            """
            return image.rsplit("@", 1)[-1] if "@" in image else published_digest

        def digest(self, _repository: str, _tag: str) -> Optional[str]:
            """Report the destination tag's current digest.

            :param _repository: Repository argument accepted by the Harbor double.
            :param _tag: Tag argument accepted by the Harbor double.
            :returns: Current digest or ``None`` when no tag exists.
            """
            return self.current

        def image(self, repository: str, reference: str) -> str:
            """Build a fixture Harbor reference using tag or digest syntax.

            :param repository: Image repository under the fixture project.
            :param reference: Tag or ``sha256:`` digest to append.
            :returns: Fully qualified image reference for the copy operation.
            """
            separator = "@" if reference.startswith("sha256:") else ":"
            return f"registry.example.test/unit/{repository}{separator}{reference}"

        def copy(self, source: str, destination: str) -> str:
            """Record a copy and move the destination tag to the source digest.

            :param source: Source image reference being published or restored.
            :param destination: Destination reference recorded for verification.
            :returns: Digest assigned to the destination after the copy.
            """
            self.copies.append((source, destination))
            self.current = self.inspect(source)
            return self.current

        def delete_tag(self, repository: str, tag: str) -> bool:
            """Record removal of a newly created destination tag.

            :param repository: Repository containing the tag.
            :param tag: Tag removed during rollback.
            :returns: ``True`` after the fixture state is cleared.
            """
            self.deleted.append((repository, tag))
            self.current = None
            return True

    events: MutableSequence[str] = []
    service = HarborService()
    context = DELIVERY_STATE.DeliveryState()
    context.harbor = service
    context.source_image = "source.example/image@sha256:" + "a" * 64
    push = REGISTRY.HarborPush(action=REGISTRY.PushAction("toolbox", "smoke"))
    downstream = Recorded(
        events, "downstream", collect_error=RuntimeError("downstream failed")
    )

    with pytest.raises(RuntimeError, match="downstream failed"):
        _run_callbacks([push, downstream], context=context)

    assert context.failed is True
    assert service.deleted == expected_deletes
    assert service.current == existing_digest
    assert len(service.copies) == expected_copies
    assert events == ["downstream.collect", "downstream.cleanup"]


@pytest.mark.parametrize(
    ("prior_digest", "expected_copies", "expected_deletes"),
    (
        ("sha256:" + "c" * 64, 1, []),
        (None, 0, [("toolbox", "ci-skills-build-" + "a" * 24)]),
    ),
    ids=("restore-existing-candidate", "delete-new-candidate"),
)
def test_harbor_build_failure_cleanup_restores_candidate_tag(
    prior_digest: Optional[str],
    expected_copies: int,
    expected_deletes: Sequence[tuple[str, ...]],
) -> None:
    """Restore a prior build candidate digest or remove a new candidate.

    :param prior_digest: Candidate tag's digest before the failed workflow.
    :param expected_copies: Number of restoration copies expected.
    :param expected_deletes: Candidate tag deletions expected during cleanup.

    :returns: None; assertions in the body validate the regression.
    """
    produced_digest = "sha256:" + "d" * 64

    class Spec:
        """Supply a fixed candidate build specification without rendering assets.

        .. attribute :: namespace
            :type: str

            Namespace used for generated BuildConfig resources.

        .. attribute :: app
            :type: str

            Application name rendered into generated resource labels.

        .. attribute :: fingerprint
            :type: str

            Build specification fingerprint expected by the callback.

        .. attribute :: image_repository
            :type: str

            Harbor repository selected by the publication workflow.
        """

        namespace: str = "toolbox-ns"
        app: str = "toolbox"
        fingerprint: str = "a" * 64
        image_repository: str = "toolbox"

        def render(self, _directory: Path, _resolved_base_image: str) -> None:
            """Accept the temporary build context without creating more files.

            :param _directory: Build-context directory supplied by the callback.
            :param _resolved_base_image: Resolved image supplied by the callback.
            :returns: ``None``; the fake build has no rendered assets.
            """
            return

        def _node_selector(self) -> None:
            """Leave the fixture Build without node placement constraints.

            :returns: ``None`` so the generated BuildConfig has no selector.
            """
            return

    class HarborService:
        """Track candidate-tag digest lookups, copies, and cleanup deletion.

        .. attribute :: dockerconfig
            :type: bytes

            Synthetic Docker auth config supplied to build publication.

        .. attribute :: target
            :type: Namespace

            Harbor host and project selected by the workflow.
        """

        dockerconfig: bytes = b'{"auths":{"registry.example.test":{}}}'
        target: Namespace = Namespace(host="registry.example.test", project="unit")
        credential: object = object()

        def __init__(self):
            """Seed candidate digest and mutation journals for recovery checks."""
            self.current = prior_digest
            self.copies: MutableSequence[tuple[str, str]] = []
            self.deleted: MutableSequence[tuple[str, str]] = []
            self.lookups: MutableSequence[tuple[str, str]] = []

        def digest(self, repository: str, reference: str) -> Optional[str]:
            """Record a candidate digest lookup and return current state.

            :param repository: Repository requested by the build callback.
            :param reference: Candidate tag or digest requested by the callback.
            :returns: Current candidate digest or ``None`` before publication.
            """
            self.lookups.append((repository, reference))
            return self.current

        def image(self, repository: str, reference: str) -> str:
            """Build a fixture Harbor reference for tag or digest access.

            :param repository: Image repository under the fixture project.
            :param reference: Tag or ``sha256:`` digest to append.
            :returns: Fully qualified image reference.
            """
            separator = "@" if reference.startswith("sha256:") else ":"
            return f"registry.example.test/unit/{repository}{separator}{reference}"

        def copy(self, source: str, destination: str) -> str:
            """Record restoration of an overwritten candidate tag.

            :param source: Prior digest image copied back to the candidate tag.
            :param destination: Candidate reference receiving the restored digest.
            :returns: Digest assigned after the copy.
            """
            self.copies.append((source, destination))
            self.current = source.rsplit("@", 1)[-1]
            return self.current

        def delete_tag(self, repository: str, reference: str) -> bool:
            """Record removal of a candidate created by the failed build.

            :param repository: Repository containing the candidate tag.
            :param reference: Candidate tag removed during cleanup.
            :returns: ``True`` after the fixture state is cleared.
            """
            self.deleted.append((repository, reference))
            self.current = None
            return True

    harbor = HarborService()

    class KubernetesService:
        """Simulate absent build resources and a completed OpenShift build."""

        def __init__(self):
            """Start with no applied BuildConfig read-back state."""
            self.applied: Optional[JSONObject] = None
            self.target = object()

        def read(self, _kind: str, _name: str, _namespace: str) -> Optional[JSONObject]:
            """Report no BuildConfig before apply and read back applied state.

            :param _kind: Resource kind accepted by the Kubernetes double.
            :param _name: Resource name accepted by the Kubernetes double.
            :param _namespace: Namespace accepted by the Kubernetes double.
            :returns: ``None`` before apply, then the applied resource with a UID.
            """
            if self.applied is None:
                return
            observed = deepcopy(self.applied)
            metadata = observed["metadata"]
            assert isinstance(metadata, dict)
            metadata["uid"] = "buildconfig-uid"
            return observed

        def apply(self, document: JSONObject) -> None:
            """Store a generated resource for independent read-back.

            :param document: Resource document submitted by ``ResourceSet``.
            :returns: None after storing a detached copy.
            """
            self.applied = deepcopy(document)

        def command(
            self, *_args: str, openshift: bool = False
        ) -> RUNTIME.CommandResult:
            """Return the resource name from a simulated OpenShift build start.

            :param *_args: Ignored positional arguments accepted by the fake boundary.
            :param openshift: Required ``True`` for ``start-build``.
            :returns: Successful result naming the started Build.

            """
            assert openshift is True
            return RUNTIME.CommandResult(
                (), 0, "build.build.openshift.io/toolbox-1\n", ""
            )

        def json(self, *_args: str, openshift: bool = False) -> JSONObject:
            """Return Build JSON after the native start command.

            :param *_args: Ignored positional arguments accepted by the fake boundary.
            :param openshift: Required ``False`` for the JSON read-back.
            :returns: Metadata naming the started Build.

            """
            assert openshift is False
            return {"metadata": {"name": "toolbox-1"}}

        def wait_build(self, _name: str, _namespace: str) -> JSONObject:
            """Simulate publication and return the produced image digest.

            :param _name: Build name accepted by the wait double.
            :param _namespace: Namespace accepted by the wait double.
            :returns: Completed output status containing the published digest.
            """
            harbor.current = produced_digest
            return {"status": {"output": {"to": {"imageDigest": produced_digest}}}}

        def delete(self, *_args: str) -> None:
            """Accept deletion of a resource created for the simulated build.

            :param *_args: Ignored positional arguments accepted by the fake boundary.
            :returns: ``None`` after the fixture deletion.

            """
            return

    class Resources:
        """Accept BuildConfig apply, recovery, and deletion during failure cleanup."""

        def apply(self, document: JSONObject) -> JSONObject:
            """Accept a generated resource without changing fixture state.

            :param document: BuildConfig applied by the callback.
            :returns: The applied BuildConfig for the callback's next step.
            """
            return document

        def recover(self) -> None:
            """Accept recovery of BuildConfig resources in this fixture.

            :returns: ``None``; candidate-tag assertions are the focus here.
            """
            return

        def delete(self, _kind: str, _name: str) -> None:
            """Accept removal of a fixture-managed resource.

            :param _kind: Kind of resource requested for removal.
            :param _name: Name of resource requested for removal.
            :returns: ``None`` after the fixture deletion.
            """
            return

    workflow = TOOLBOX_ACTIONS.WorkflowAction(Namespace())
    workflow.spec = Spec()
    workflow.resolved_base = "registry.example.test/base@sha256:" + "b" * 64
    context = DELIVERY_STATE.DeliveryState()
    context.harbor = harbor
    context.kubernetes = KubernetesService()
    build = TOOLBOX_ACTIONS.HarborBuild(workflow=workflow)
    build._resource_set = Resources()
    downstream = Recorded(
        [], "downstream", collect_error=RuntimeError("downstream failed")
    )

    with pytest.raises(RuntimeError, match="downstream failed"):
        _run_callbacks([build, downstream], context=context)

    candidate_tag = (
        TOOLBOX_ACTIONS.BUILD_TAG_PREFIX
        + Spec.fingerprint[: TOOLBOX_ACTIONS.FINGERPRINT_TAG_LENGTH]
    )
    assert harbor.current == prior_digest
    assert harbor.deleted == expected_deletes
    assert len(harbor.copies) == expected_copies
    assert harbor.lookups[-1] == (Spec.image_repository, candidate_tag)


def test_resource_set_recovers_prior_owned_state_after_downstream_failure() -> None:
    """Recovery reapplies the first owned state after a later callback fails.
    :returns: None; assertions in the body validate the regression.
    """
    previous = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "runner",
            "namespace": "unit",
            "uid": "prior-uid",
            "labels": {
                RESOURCES.MANAGER_LABEL: RESOURCES.MANAGER_VALUE,
                RESOURCES.OPERATION_LABEL: "unit-runner",
            },
        },
        "spec": {"replicas": 1},
        "status": {"readyReplicas": 1},
    }

    class KubernetesService:
        """Track owned resource changes and restoration after downstream failure."""

        def __init__(self):
            """Seed a prior owned Deployment and journals for apply/delete operations."""
            self.current = deepcopy(previous)
            self.applied: MutableSequence[MutableMapping[str, JSONValue]] = []
            self.deleted: MutableSequence[tuple[str, str, str]] = []

        def read(self, _kind: str, _name: str, _namespace: str) -> Optional[JSONObject]:
            """Return a detached copy of the current Deployment.

            :param _kind: Resource kind accepted by the double.
            :param _name: Resource name accepted by the double.
            :param _namespace: Namespace accepted by the double.
            :returns: Copied Deployment or ``None`` after deletion.
            """
            return deepcopy(self.current)

        def apply(self, document: JSONObject) -> None:
            """Record application and preserve the original resource UID.

            :param document: Desired Deployment applied or restored by ``ResourceSet``.

            :returns: None; the helper records behavior through its side effects.
            """
            self.applied.append(deepcopy(document))
            self.current = deepcopy(document)
            metadata = self.current["metadata"]
            assert isinstance(metadata, dict)
            metadata.setdefault("uid", "prior-uid")

        def delete(self, kind: str, name: str, namespace: str) -> None:
            """Record deletion and clear the current Deployment.

            :param kind: Resource kind requested for deletion.
            :param name: Resource name requested for deletion.
            :param namespace: Namespace requested for deletion.

            :returns: None; the helper records behavior through its side effects.
            """
            self.deleted.append((kind, name, namespace))
            self.current = None

    service = KubernetesService()
    resources = RESOURCES.ResourceSet(service, "unit", "unit-runner")
    resources.apply(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "runner"},
            "spec": {"replicas": 2},
        }
    )

    resources.recover()

    assert service.current["spec"] == previous["spec"]
    assert service.current["metadata"]["labels"] == previous["metadata"]["labels"]
    assert len(service.applied) == 2

    applied_before = list(service.applied)
    deleted_before = list(service.deleted)
    resources.recover()

    assert service.applied == applied_before
    assert service.deleted == deleted_before


@pytest.mark.parametrize(
    ("recovered", "raises"),
    (
        (
            {"id": 42, "labels": [{"name": "self-hosted"}, {"name": "Linux"}]},
            False,
        ),
        ({"id": 43, "labels": [{"name": "self-hosted"}]}, True),
    ),
)
def test_github_runtime_cleanup_requires_the_original_runner_identity_and_labels(
    recovered: JSONObject, raises: bool
) -> None:
    """Preserve the original runner when replacement read-back is incomplete.

    :param recovered: Runner record returned after attempted recovery.
    :param raises: Whether mismatched identity or labels must fail cleanup.

    :returns: None; assertions in the body validate the regression.
    """
    previous = {
        "id": 42,
        "labels": [{"name": "self-hosted"}, {"name": "Linux"}],
    }

    class GitHubService:
        """Track created runner identity and labels through failure cleanup."""

        def __init__(self):
            """Start a journal that reveals accidental deletion of the prior runner."""
            self.deleted: MutableSequence[str] = []

        def runner(self, name: str) -> JSONObject:
            """Return the original runner before a failed replacement.

            :param name: Runner name, required to match the fixture identity.
            :returns: Original runner ID and labels.
            """
            assert name == "runner"
            return previous

        def wait_online(self, name: str, timeout: int) -> JSONObject:
            """Return the parametrized recovery read-back.

            :param name: Runner name expected from the runtime.
            :param timeout: Wait budget expected from the runner action.
            :returns: Recovered runner record, complete or mismatched.
            """
            assert (name, timeout) == ("runner", 30)
            return recovered

        def delete_runner(self, name: str) -> bool:
            """Record deletion so the test can require preserving the original.

            :param name: Runner name selected for deletion.
            :returns: ``True`` after recording the attempted deletion.
            """
            self.deleted.append(name)
            return True

    service = GitHubService()
    runtime = GITHUB_RUNNER.GitHubRunnerRuntime(
        action=RUNNER_CORE.RunnerAction("unit", "runner", "/runner", (), 30)
    )
    context = DELIVERY_STATE.DeliveryState(failed=True)
    context.github = service
    runtime.bind(context)
    runtime.collect()

    if raises:
        with pytest.raises(GITHUB_RUNNER.APIError, match="runner_recovery_incomplete"):
            runtime.cleanup()
    else:
        runtime.cleanup()

    assert service.deleted == []


def test_read_api_chains_nested_empty_object_payload() -> None:
    """ReadAPI keeps an empty selected object visible to later callbacks.
    :returns: None; assertions in the body validate the regression.
    """

    class Client:
        """Return one nested provider payload for a read command."""

        def __init__(self):
            """Start an empty request journal for endpoint assertions."""
            self.calls: MutableSequence[tuple[str, bool]] = []

        def get_text(
            self,
            _session: API_READ.ReadSession,
            endpoint: str,
            *,
            paginate: bool = False,
        ) -> str:
            """Return the nested response selected by the test action.

            :param _session: Bound API session accepted by the fake client.
            :param endpoint: Provider-relative endpoint requested by ``ReadAPI``.
            :param paginate: Whether native pagination was requested.
            :returns: JSON response containing an empty nested object.
            """
            self.calls.append((endpoint, paginate))
            return '{"metadata":{"labels":{"empty":{}}}}'

    observed: MutableSequence[JSONValue] = []

    class Dependent(CALLBACKS.LocalCallback[API_READ.ApiReadState, RecordedAction]):
        """Record the shared payload visible after ReadAPI completes."""

        def run(self) -> JSONObject:
            """Publish the current shared payload as dependent evidence.

            :returns: Mapping that echoes the upstream payload observed through state.
            """
            payload = self.state.payload
            observed.append(payload)
            return {"observed": payload}

    action = API_READ.ApiReadAction(
        provider=API_READ.GitProvider.GITHUB,
        endpoint="repos/unit/repo",
        field="metadata.labels.empty",
    )
    context = API_READ.ApiReadState()
    client = Client()
    context.service = API_READ.ApiReadService(
        client, API_READ.ReadSession("github.example.test", {}, "fixture")
    )
    read = API_READ.ReadAPI(action)
    dependent = Dependent(RecordedAction("dependent"))

    EXECUTOR.WorkflowExecutor(
        context=context, callbacks=CALLBACK_COLLECTIONS.Callbacks((read, dependent))
    ).run()

    assert client.calls == [("repos/unit/repo", False)]
    assert context.payload == {}
    assert read.result == {"value": {}}
    assert observed == [{}]
    assert dependent.result == {"observed": {}}


def test_read_api_preserves_false_scalar_and_rejects_null_selection() -> None:
    """False is a selected value, while null remains an invalid missing field.
    :returns: None; assertions in the body validate the regression.
    """

    class Client:
        """Return a scalar/null response for field-selection checks."""

        def get_text(
            self,
            _session: API_READ.ReadSession,
            _endpoint: str,
            *,
            paginate: bool = False,
        ) -> str:
            """Return one JSON document with false and null fields.

            :param _session: Bound API session accepted by the fake client.
            :param _endpoint: Provider endpoint accepted by the fake client.
            :param paginate: Pagination flag, expected to remain false.
            :returns: JSON body used to exercise field selection.
            """
            assert paginate is False
            return '{"enabled":false,"empty":null}'

    context = API_READ.ApiReadState()
    context.service = API_READ.ApiReadService(
        Client(), API_READ.ReadSession("github.example.test", {}, "fixture")
    )
    false_read = API_READ.ReadAPI(
        API_READ.ApiReadAction(
            provider=API_READ.GitProvider.GITHUB,
            endpoint="repos/unit/repo",
            field="enabled",
        )
    )

    EXECUTOR.WorkflowExecutor(
        context=context, callbacks=CALLBACK_COLLECTIONS.Callbacks(false_read)
    ).run()

    assert context.payload is False
    assert false_read.result == {"value": False}

    null_read = API_READ.ReadAPI(
        API_READ.ApiReadAction(
            provider=API_READ.GitProvider.GITHUB,
            endpoint="repos/unit/repo",
            field="empty",
        )
    )
    with pytest.raises(API_READ.ReadToolError, match="selected field is missing"):
        EXECUTOR.WorkflowExecutor(
            context=context, callbacks=CALLBACK_COLLECTIONS.Callbacks(null_read)
        ).run()


@pytest.mark.parametrize(
    ("action", "dry_run", "expected"),
    (
        ("check", False, ("SelectAPI", "BindAPI", "CheckAPI")),
        ("check", True, ("SelectAPI", "PlanAPICheck")),
        ("get", False, ("SelectAPI", "BindAPI", "ReadAPI")),
        ("get", True, ("SelectAPI", "PlanAPI")),
    ),
    ids=("check-live", "check-plan", "get-live", "get-plan"),
)
def test_api_callbacks_build_one_concrete_operation_order(
    action: str, dry_run: bool, expected: Sequence[str]
) -> None:
    """Select the exact callback chain for identity checks and endpoint reads.

    :param action: Public ci-api operation selected by parsed arguments.
    :param dry_run: Whether the CLI requested a plan instead of live access.
    :param expected: Callback class names expected in declaration order.
    :returns: None; assertions in the body validate the regression.
    """
    args = Namespace(
        action=action,
        provider="github",
        gitlab_profile=None,
        endpoint="repos/unit/repo" if action == "get" else None,
        field=None,
        lines=None,
        decode_base64=False,
        paginate=False,
        host=None,
        token_file=None,
        dry_run=dry_run,
    )

    callbacks = READ_CLI._api_callbacks(args, API_READ.ApiOutput.JSON)

    assert (
        tuple(type(callback).__name__ for callback in callbacks.callbacks) == expected
    )
    concrete = callbacks.callbacks[0].action
    if action == "check":
        assert isinstance(concrete, API_READ.ApiCheckAction)
        assert not hasattr(concrete, "endpoint")
    else:
        assert isinstance(concrete, API_READ.ApiReadAction)
        assert concrete.endpoint == "repos/unit/repo"


def test_api_check_rejects_transforms_before_action_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject identity-check transforms before constructing a credential action.

    :param monkeypatch: Pytest fixture replacing the action constructor with a trap.
    :returns: None; assertions in the body validate the regression.
    """
    monkeypatch.setattr(
        READ_CLI,
        "ApiCheckAction",
        lambda *args, **kwargs: pytest.fail("check action constructed after transform"),
    )
    args = Namespace(
        action="check",
        provider="github",
        gitlab_profile=None,
        endpoint=None,
        field="login",
        lines=None,
        decode_base64=False,
        paginate=False,
        host=None,
        token_file=None,
        dry_run=False,
    )

    with pytest.raises(API_READ.ReadToolError, match="check does not accept"):
        READ_CLI._api_callbacks(args, API_READ.ApiOutput.JSON)


@pytest.mark.parametrize(
    ("endpoint", "provider", "expected", "absent"),
    (
        (
            "/projects?membership=true",
            "gitlab",
            "without a leading slash is required, such as projects?membership=true",
            "no scheme",
        ),
        (
            "https://gitlab.example.test/api/v4/projects",
            "gitlab",
            "projects?membership=true; no scheme, host, fragment or dot segments",
            "leading slash",
        ),
        (
            "repos/../user",
            "github",
            "user/repos?per_page=100; no scheme, host, fragment or dot segments",
            "leading slash",
        ),
    ),
    ids=("leading-slash", "absolute-url", "dot-segment"),
)
def test_api_read_names_the_rejected_endpoint_form(
    endpoint: str, provider: str, expected: str, absent: str
) -> None:
    """Name the leading slash only when it is the fault, with a provider example.

    :param endpoint: Rejected endpoint value supplied by the caller.
    :param provider: Native provider selecting the example in the diagnostic.
    :param expected: Fragment the diagnostic must contain.
    :param absent: Fragment the diagnostic must not contain.
    :returns: None; assertions in the body validate the regression.
    """
    action = API_READ.ApiReadAction(
        provider=API_READ.GitProvider(provider), endpoint=endpoint
    )

    with pytest.raises(API_READ.ReadToolError) as failure:
        action.validate()

    assert failure.value.code == API_READ.ReadExit.USAGE
    assert expected in str(failure.value)
    assert absent not in str(failure.value)


def test_api_dry_run_plans_keep_legacy_blank_transform_fields() -> None:
    """Serialize absent selectors as the existing empty-string plan fields.

    :returns: None; assertions in the body validate the regression.
    """
    context = API_READ.ApiReadState()
    context.selection = API_READ.ApiProviderSelection(
        API_READ.GitProvider.GITHUB, "github.example.test"
    )
    read = API_READ.PlanAPI(
        API_READ.ApiReadAction(API_READ.GitProvider.GITHUB, "repos/unit/repo")
    )
    check = API_READ.PlanAPICheck(API_READ.ApiCheckAction(API_READ.GitProvider.GITHUB))

    EXECUTOR.WorkflowExecutor(
        context=context, callbacks=CALLBACK_COLLECTIONS.Callbacks(read)
    ).run()
    assert context.payload == {
        "mode": "dry-run",
        "operation": "get",
        "provider": "github",
        "host": "github.example.test",
        "endpoint": "repos/unit/repo",
        "raw": False,
        "field": "",
        "decodeBase64": False,
        "lines": "",
        "paginate": False,
    }

    EXECUTOR.WorkflowExecutor(
        context=context, callbacks=CALLBACK_COLLECTIONS.Callbacks(check)
    ).run()
    assert context.payload == {
        "mode": "dry-run",
        "operation": "check",
        "provider": "github",
        "host": "github.example.test",
        "endpoint": "user",
        "raw": False,
        "field": "",
        "decodeBase64": False,
        "lines": "",
        "paginate": False,
    }


@pytest.mark.parametrize(
    ("output", "expected"),
    (
        (
            API_READ.ApiOutput.JSON,
            {
                "provider": "github",
                "host": "github.example.test",
                "authenticated": True,
                "user": "octo",
                "id": 7,
            },
        ),
        (API_READ.ApiOutput.TEXT, "octo"),
    ),
    ids=("json", "text"),
)
def test_check_api_preserves_identity_json_and_text_outputs(
    output: API_READ.ApiOutput, expected: JSONValue
) -> None:
    """Read authenticated identity through the concrete check callback.

    :param output: Requested identity representation.
    :param expected: Native payload expected after identity selection.
    :returns: None; assertions in the body validate the regression.
    """

    class Client:
        """Return one successful provider identity document."""

        def __init__(self):
            """Start an empty endpoint journal for identity assertions.

            :ivar calls: Endpoints requested by the check callback.
            """
            self.calls: MutableSequence[str] = []

        def get_text(
            self,
            _session: API_READ.ReadSession,
            endpoint: str,
            *,
            paginate: bool = False,
        ) -> str:
            """Return a bounded GitHub identity response.

            :param _session: Bound read session supplied by the service.
            :param endpoint: Provider endpoint selected by the check callback.
            :param paginate: Pagination flag, expected to stay false.
            :returns: JSON text returned by the fake provider.
            """
            assert paginate is False
            self.calls.append(endpoint)
            return '{"login":"octo","id":7}'

    client = Client()
    context = API_READ.ApiReadState()
    context.service = API_READ.ApiReadService(
        client, API_READ.ReadSession("github.example.test", {}, "fixture")
    )
    callback = API_READ.CheckAPI(
        API_READ.ApiCheckAction(API_READ.GitProvider.GITHUB, output=output)
    )

    EXECUTOR.WorkflowExecutor(
        context=context, callbacks=CALLBACK_COLLECTIONS.Callbacks(callback)
    ).run()

    assert client.calls == ["user"]
    assert context.payload == expected
    assert callback.result == {"value": expected}


def test_binary_build_loader_accepts_finite_aliases_and_rejects_recursive_aliases(
    tmp_path: Path,
) -> None:
    """Bounded YAML aliases may be finite, but recursive aliases are rejected.

    :param tmp_path: Directory receiving finite and recursive BuildConfig YAML.

    :returns: None; assertions in the body validate the regression.
    """
    commit = "a" * 40
    finite = tmp_path / "finite.yaml"
    finite.write_text(
        "apiVersion: build.openshift.io/v1\n"
        "kind: BuildConfig\n"
        "metadata:\n"
        "  name: unit\n"
        "  namespace: unit\n"
        "  labels:\n"
        f"    example.invalid/source-commit: {commit}\n"
        "    copied: &copied\n"
        "      owner: unit\n"
        "spec:\n"
        "  source:\n"
        "    type: Binary\n"
        "    contextDir: null\n"
        "    extra:\n"
        "    - *copied\n"
        "  strategy:\n"
        "    type: Docker\n"
        "    dockerStrategy:\n"
        "      dockerfilePath: Dockerfile\n"
        "  output:\n"
        "    to:\n"
        "      kind: DockerImage\n"
        "      name: registry.example.invalid/unit:candidate\n",
        encoding="utf-8",
    )
    recursive = tmp_path / "recursive.yaml"
    recursive.write_text("root: &root\n  child: *root\n", encoding="utf-8")

    manifest = BINARY_BUILD.BinaryBuildPlanner.load_manifest(finite)

    assert manifest["metadata"]["labels"]["copied"] == {"owner": "unit"}
    assert manifest["spec"]["source"]["extra"] == [{"owner": "unit"}]
    with pytest.raises(BINARY_BUILD.ReadToolError, match="JSON-compatible"):
        BINARY_BUILD.BinaryBuildPlanner.load_manifest(recursive)


@pytest.mark.parametrize("signal_name", ("SIGINT", "SIGTERM", "SIGHUP"))
def test_executor_cleans_up_before_exiting_for_each_recoverable_signal(
    signal_name: str,
) -> None:
    """Require child-process cleanup before signal-derived exit status.

    :param signal_name: Recoverable signal sent by the callback to its process.

    :returns: None; assertions in the body validate the regression.
    """
    script = """
import os
import signal
import sys
from dataclasses import dataclass
from lib.python.core.abstract.callback import LocalCallback
from lib.python.core.abstract.callbacks import Callbacks
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.executor import Executor

@dataclass(frozen=True)
class UnitAction(SkillAction):
    name: str

class Interrupting(LocalCallback[object, UnitAction]):
    def run(self):
        os.kill(os.getpid(), getattr(signal, sys.argv[1]))
        return {}

    def cleanup(self):
        print("cleanup", flush=True)
        return {}

try:
    Executor(callbacks=Callbacks(Interrupting(UnitAction("interrupt")))).run()
except SystemExit as exc:
    print(f"exit:{exc.code}", flush=True)
    raise
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(SKILL_ROOT), environment.get("PYTHONPATH")))
    )
    signum = getattr(signal, signal_name)
    result = subprocess.run(
        [sys.executable, "-c", script, signal_name],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=10,
    )

    assert result.returncode == 128 + signum
    assert result.stdout.splitlines() == ["cleanup", f"exit:{128 + signum}"]


def test_read_runner_uses_the_real_composed_callback_operation() -> None:
    """Existing runner reads execute through ``ReadRunner`` and ``Executor``.
    :returns: None; assertions in the body validate the regression.
    """
    plan = ACTION.ActionPlan(
        kind="gitlab_runner",
        operation="list",
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/repo",
        target_file="/selected/target.toml",
        target_source="env:CI_SKILLS_TARGET;target:gitlab.project",
        body={"description": None, "limit": 10, "filters": []},
    )

    class API:
        """Return scripted GitLab API reads for a composed runner callback."""

        def __init__(self):
            """Start an endpoint journal for the runner list and detail reads."""
            self.calls: MutableSequence[str] = []

        def get_json(
            self, _session: GITLAB_SESSION.BoundGitLabSession, endpoint: str
        ) -> JSONValue:
            """Answer project-runner list and exact-runner detail requests.

            :param _session: Read session accepted by the API double.
            :param endpoint: Project list or runner detail endpoint requested.
            :returns: Fixture runner list or detailed runner record.
            """
            self.calls.append(endpoint)
            if endpoint == "projects/42/runners?per_page=100&page=1":
                return [{"id": 23, "description": "owned-runner"}]
            assert endpoint == "runners/23"
            return {
                "id": 23,
                "description": "owned-runner",
                "runner_type": "project_type",
                "tag_list": ["unit"],
                "status": "online",
                "online": True,
                "paused": False,
                "is_shared": False,
                "access_level": "ref_protected",
                "job_execution_status": "idle",
                "projects": [{"id": 42}],
                "contacted_at": None,
                "version": None,
                "platform": None,
                "architecture": None,
            }

    api = API()
    session = GITLAB_SESSION.BoundGitLabSession(
        origin=plan.origin,
        host="gitlab.example.test",
        target_kind=plan.target_kind,
        target_reference=plan.target_reference,
        target_source=plan.target_source,
        target_file=Path(plan.target_file),
        credential_source="env:GITLAB_TOKEN",
        credential_digest="sha256:unit",
        execution_host="unit-host",
        skill={"digest": "unit", "revision": {"value": "a" * 40, "verified": True}},
        environment={},
    )
    callback = RUNNERS.ReadRunner(action=plan)
    result = callback.execute(api, session, 42)

    assert [record["id"] for record in result["records"]] == [23]
    assert [record["id"] for record in callback.state.runner_read.records] == [23]
    assert api.calls == ["projects/42/runners?per_page=100&page=1", "runners/23"]
