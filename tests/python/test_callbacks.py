"""Callback lifecycle regression coverage for composed skill operations."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
from argparse import Namespace
from copy import deepcopy

import pytest

from tests.python.conftest import LIB_ROOT, import_script_module

ACTION = import_script_module("core.gitlab_actions")
CALLBACKS = import_script_module("core.action")
KUBERNETES = import_script_module("core.kubernetes")
REGISTRY = import_script_module("core.registry_mirror")
RESOURCES = import_script_module("core.resources")
RUNTIME = import_script_module("core.runtime")
RUNNERS = import_script_module("core.gitlab_runners")
TARGET = import_script_module("core.target")
TOOLBOX = import_script_module("core.toolbox")


class Recorded(CALLBACKS.Callback[object, object]):
    """Record lifecycle calls and optionally return, stop, or fail."""

    def __init__(
        self,
        events: list[str],
        name: str,
        *,
        value: object = None,
        collect_error: Exception | None = None,
        cleanup_error: Exception | None = None,
        complete: bool = True,
    ) -> None:
        self.events = events
        self.name = name
        self.value = value
        self.collect_error = collect_error
        self.cleanup_error = cleanup_error
        self._complete = complete

    @property
    def complete(self) -> bool:
        return self._complete

    def collect(self) -> None:
        self.events.append(f"{self.name}.collect")
        if self.collect_error is not None:
            raise self.collect_error

    def run(self) -> object:
        self.events.append(f"{self.name}.run")
        return self.value

    def update(self) -> None:
        self.events.append(f"{self.name}.update")

    def cleanup(self) -> None:
        self.events.append(f"{self.name}.cleanup")
        if self.cleanup_error is not None:
            raise self.cleanup_error


class OpenShiftReadBack:
    """Minimal native API double that can omit ``omitempty`` BuildConfig fields."""

    def __init__(self, observed_spec: dict[str, object] | None = None) -> None:
        self.applies = 0
        self.observed_spec = observed_spec
        self.resource: dict[str, object] | None = None

    def read(self, _kind: str, _name: str, _namespace: str) -> dict[str, object] | None:
        return deepcopy(self.resource)

    def apply(self, document: dict[str, object]) -> None:
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


def _build_config(spec: dict[str, object]) -> dict[str, object]:
    """Return a generated BuildConfig shape suitable for resource read-back."""
    return {
        "apiVersion": "build.openshift.io/v1",
        "kind": "BuildConfig",
        "metadata": {"name": "toolbox"},
        "spec": spec,
    }


def test_resource_set_omitted_empty_buildconfig_triggers_is_applied_then_no_op():
    """OpenShift omits an empty BuildConfig ``spec.triggers`` on read-back."""
    service = OpenShiftReadBack()
    resources = RESOURCES.ResourceSet(service, "toolbox-ns", "toolbox-build")
    spec = {"runPolicy": "Serial", "triggers": []}

    applied = resources.apply(_build_config(spec))
    repeated = resources.apply(_build_config(spec))

    assert applied["action"] == "APPLIED"
    assert repeated["action"] == "NO_OP"
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
    spec, observed_spec
):
    """Only an absent empty optional list is accepted as an OpenShift omission."""
    resources = RESOURCES.ResourceSet(
        OpenShiftReadBack(observed_spec), "toolbox-ns", "toolbox-build"
    )

    with pytest.raises(RESOURCES.KubernetesError, match="resource_readback_mismatch"):
        resources.apply(_build_config(spec))


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
def test_linux_installer_preserves_url_path_suffix(url, suffix):
    """Installer artifacts retain the URL path suffix, excluding its query."""
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


def test_conda_render_scopes_channels_without_unsupported_create_options():
    """Conda channels use its supported process environment, not create args."""
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


def test_toolbox_build_starts_by_name_then_reads_build_as_json():
    """The native build command returns a name that is read back independently."""
    events: list[object] = []
    digest = "sha256:" + "b" * 64
    reference = "build.build.openshift.io/toolbox-1"

    class Spec:
        namespace = "toolbox-ns"
        app = "toolbox"
        fingerprint = "a" * 64

        def render(self, directory, resolved_base_image):
            events.append(("render", directory.is_dir(), resolved_base_image))

        def _node_selector(self):
            return None

    class KubernetesService:
        def read(self, kind, name, namespace):
            events.append(("read", kind, name, namespace))

        def command(self, *args, openshift=False):
            events.append(("command", args, openshift))
            return RUNTIME.CommandResult(tuple(args), 0, reference + "\n", "")

        def json(self, *args, openshift=False):
            events.append(("json", args, openshift))
            return {"metadata": {"name": "toolbox-1"}}

        def wait_build(self, name, namespace):
            events.append(("wait_build", name, namespace))
            return {"status": {"output": {"to": {"imageDigest": digest}}}}

    class Resources:
        def apply(self, document):
            events.append(("apply", document["kind"]))
            return document

    spec = Spec()
    service = KubernetesService()
    action = TOOLBOX.BuildAction(
        spec=spec,
        resolved_base_image="registry.example.test/base@sha256:" + "c" * 64,
        candidate_repository="image-registry.example.test/unit/toolbox",
        candidate_reference="candidate",
        dockerconfig=b'{"auths":{"registry.example.test":{}}}',
        digest_lookup=lambda _repository, _reference: digest,
    )
    callback = TOOLBOX.BuildToolbox(action=action)
    callback.bind(CALLBACKS.ExecutionContext(services={"kubernetes": service}))
    callback._resource_set = Resources()

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
    assert result.build_name == "toolbox-1"
    assert result.digest == digest
    assert result.verified is True


def test_abstract_hooks_are_required_but_callback_defaults_are_optional():
    """Concrete atoms can opt into only the hooks their operation needs."""
    with pytest.raises(TypeError):
        CALLBACKS.AbstractCallback()

    result = CALLBACKS.Executor(callbacks=CALLBACKS.Callback()).run()

    assert result.value is None


def test_executor_orders_hooks_and_passes_typed_callback_results():
    """A later atom reads the earlier result without another workflow helper."""
    events: list[str] = []
    producer = Recorded(events, "producer", value=7)

    class Consumer(Recorded):
        def run(self) -> object:
            self.events.append("consumer.run")
            return producer.result + 1

    consumer = Consumer(events, "consumer")
    result = CALLBACKS.Executor(callbacks=[producer, consumer]).run()

    assert result.value == 8
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


def test_incomplete_callback_stops_later_callbacks_and_cleans_up():
    """Incomplete read-back never allows dependent work to start."""
    events: list[str] = []
    incomplete = Recorded(
        events, "incomplete", value={"verified": False}, complete=False
    )
    later = Recorded(events, "later")

    CALLBACKS.Executor(callbacks=[incomplete, later]).run()

    assert events == [
        "incomplete.collect",
        "incomplete.run",
        "incomplete.update",
        "incomplete.cleanup",
    ]
    assert later.result is None


@pytest.mark.parametrize(
    "record",
    (
        {"verified": True, "errors": [{"reason": "readback failed"}]},
        {"verified": True, "cleanup": {"status": "BLOCKED"}},
    ),
)
def test_incomplete_gitlab_result_stops_later_callbacks(record):
    """Verified provider records with errors or blocked cleanup are incomplete."""
    events: list[str] = []

    class ResultCallback(CALLBACKS.GitlabCallback[object, dict]):
        def run(self) -> dict:
            events.append("result.run")
            return record

        def cleanup(self) -> None:
            events.append("result.cleanup")

    later = Recorded(events, "later")
    CALLBACKS.Executor(callbacks=[ResultCallback(), later]).run()

    assert events == ["result.run", "result.cleanup"]
    assert later.result is None


def test_failure_stops_callbacks_and_retains_all_reverse_cleanup_evidence():
    """A failing collect still cleans itself and every earlier entered callback."""
    events: list[str] = []
    first = Recorded(events, "first", cleanup_error=RuntimeError("cleanup first"))
    primary = ValueError("collect failed")
    failing = Recorded(
        events,
        "failing",
        collect_error=primary,
        cleanup_error=RuntimeError("cleanup failing"),
    )
    later = Recorded(events, "later")
    executor = CALLBACKS.Executor(callbacks=[first, failing, later])

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
    assert [failure.callback for failure in executor.cleanup_failures] == [
        "Recorded",
        "Recorded",
    ]
    assert [failure.reason for failure in executor.cleanup_failures] == [
        "cleanup failing",
        "cleanup first",
    ]
    assert "cleanup Recorded: cleanup failing" in raised.value.__notes__
    assert "cleanup Recorded: cleanup first" in raised.value.__notes__
    assert tuple(executor.cleanup_failures) == raised.value.cleanup_failures
    assert "later.collect" not in events


def test_debug_trace_records_failure_and_reverse_cleanup(tmp_path):
    """Debug traces retain compact outcomes while failed workflows clean up."""
    lifecycle: list[str] = []
    trace_file = tmp_path / "trace.jsonl"
    first = Recorded(lifecycle, "first", value={"status": "PASS"})
    failing = Recorded(
        lifecycle,
        "failing",
        collect_error=RuntimeError("collect failed"),
    )
    context = CALLBACKS.ExecutionContext(
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
        CALLBACKS.Executor(context=context, callbacks=[first, failing]).run()

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


def test_executor_disables_default_trace_and_restores_the_caller_context():
    """A normal workflow is quiet and restores an outer trace after it exits."""
    observed: list[tuple[str, dict[str, object]]] = []
    lifecycle: list[str] = []

    with RUNTIME.execution_trace(
        lambda event, details: observed.append((event, details))
    ):
        CALLBACKS.Executor(callbacks=Recorded(lifecycle, "quiet")).run()
        RUNTIME.trace_event("after.workflow", {"status": "PASS"})

    assert observed == [("after.workflow", {"status": "PASS"})]
    assert RUNTIME.trace_enabled() is False


def test_command_trace_redacts_secret_argument_and_omits_provider_stdout(monkeypatch):
    """Command traces expose status and size without provider output or secrets."""
    monkeypatch.setattr(
        RUNTIME.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 23, "provider-output", "token=hidden"
        ),
    )
    observed: list[tuple[str, dict[str, object]]] = []

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


def test_wait_build_fails_terminal_phase_without_sleep_and_traces_logs(monkeypatch):
    """A terminal Build failure captures bounded logs without waiting to time out."""
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
    log_limits: dict[str, int] = {}

    def capture_logs(*args, **kwargs):
        log_limits.update(max_bytes=kwargs["max_bytes"], max_lines=kwargs["max_lines"])
        return RUNTIME.CommandResult(tuple(args[0]), 0, "compile failed\n", "")

    monkeypatch.setattr(KUBERNETES, "run_command_tail", capture_logs)
    observed: list[tuple[str, dict[str, object]]] = []

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


def test_wait_build_complete_does_not_capture_logs(monkeypatch):
    """A successful Build returns its read-back without collecting failure logs."""
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
    observed: list[tuple[str, dict[str, object]]] = []

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
    ("existing_digest", "expected_deletes"),
    (
        (None, [("toolbox", "smoke")]),
        ("sha256:preexisting", []),
    ),
)
def test_harbor_push_failure_cleanup_deletes_only_a_new_tag(
    existing_digest, expected_deletes
):
    """A downstream failure removes a new tag without deleting an existing one."""
    published_digest = "sha256:published"

    class HarborService:
        def __init__(self) -> None:
            self.current = existing_digest
            self.deleted: list[tuple[str, str]] = []

        def inspect(self, _image: str) -> str:
            return published_digest

        def digest(self, _repository: str, _tag: str) -> str | None:
            return self.current

        def image(self, repository: str, reference: str) -> str:
            separator = "@" if reference.startswith("sha256:") else ":"
            return f"registry.example.test/unit/{repository}{separator}{reference}"

        def copy(self, _source: str, _destination: str) -> str:
            self.current = published_digest
            return published_digest

        def delete_tag(self, repository: str, tag: str) -> bool:
            self.deleted.append((repository, tag))
            self.current = None
            return True

    events: list[str] = []
    service = HarborService()
    context = CALLBACKS.ExecutionContext(services={"harbor": service})
    push = REGISTRY.HarborPush(
        action=REGISTRY.PushAction("toolbox", "smoke", "source.example/image:tag")
    )
    downstream = Recorded(
        events, "downstream", collect_error=RuntimeError("downstream failed")
    )

    with pytest.raises(RuntimeError, match="downstream failed"):
        CALLBACKS.Executor(context=context, callbacks=[push, downstream]).run()

    assert context.failed is True
    assert service.deleted == expected_deletes
    assert service.current == (None if existing_digest is None else published_digest)
    assert events == ["downstream.collect", "downstream.cleanup"]


@pytest.mark.parametrize("signal_name", ("SIGINT", "SIGTERM", "SIGHUP"))
def test_executor_cleans_up_before_exiting_for_each_recoverable_signal(signal_name):
    """A real process signal reaches callback cleanup before its standard exit."""
    script = """
import os
import signal
import sys
from core.action import Callback, Executor

class Interrupting(Callback[object, object]):
    def run(self):
        os.kill(os.getpid(), getattr(signal, sys.argv[1]))

    def cleanup(self):
        print("cleanup", flush=True)

try:
    Executor(callbacks=Interrupting()).run()
except SystemExit as exc:
    print(f"exit:{exc.code}", flush=True)
    raise
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(LIB_ROOT), environment.get("PYTHONPATH")))
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


def test_read_runner_uses_the_real_composed_callback_operation():
    """Existing runner reads execute through ``ReadRunner`` and ``Executor``."""
    plan = ACTION.ActionPlan(
        kind="gitlab_runner",
        operation="list",
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/repo",
        target_file="/selected/target.toml",
        target_source="argv:--target;target:gitlab.project",
        body={"description": None, "limit": 10, "filters": []},
    )

    class API:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_json(self, _session: object, endpoint: str) -> object:
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
    result = RUNNERS.ReadRunner(action=plan).execute(api, object(), 42)

    assert [record["id"] for record in result.records] == [23]
    assert api.calls == ["projects/42/runners?per_page=100&page=1", "runners/23"]
