"""Callback lifecycle regression coverage for composed skill operations."""

from __future__ import annotations

import os
import signal
import subprocess
import sys

import pytest

from tests.python.conftest import LIB_ROOT, import_script_module

ACTION = import_script_module("core.gitlab_actions")
CALLBACKS = import_script_module("core.action")
RUNNERS = import_script_module("core.gitlab_runners")


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
