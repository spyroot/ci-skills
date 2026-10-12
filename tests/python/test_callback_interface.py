"""Focused contracts for state-bound callback composition.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

import pytest
from conftest import import_script_module

CALLBACK = import_script_module("core.abstract.callback")
CALLBACKS = import_script_module("core.abstract.callbacks")
CONTEXT = import_script_module("core.abstract.context")
EXECUTOR = import_script_module("core.abstract.skill_exector")
SKILL_ACTION = import_script_module("core.abstract.skill_action")
SKILL_STATE = import_script_module("core.abstract.skill_state")


@dataclass(frozen=True)
class UnitAction(SKILL_ACTION.SkillAction):
    """One required action for callback-interface tests."""

    name: str
    delta: int = 0


@dataclass
class CounterState(SKILL_STATE.SkillState):
    """Shared state used to prove cross-callback mutation visibility."""

    x: int = 0


class CounterExecutor(EXECUTOR.SkillExecutor[CounterState]):
    """Create one observable custom state for the bound callback collection."""

    def __init__(self, callbacks: CALLBACKS.Callbacks[CounterState]) -> None:
        self.created = 0
        super().__init__(callbacks)

    def create_state(self) -> CounterState:
        """Construct exactly one state for this executor."""
        self.created += 1
        return CounterState()


class EventCallback(CALLBACK.Callback[CONTEXT.ExecutionContext, UnitAction]):
    """Record the standard lifecycle and optionally fail its run or cleanup."""

    def __init__(
        self,
        action: UnitAction,
        events: list[str],
        *,
        run_error: BaseException | None = None,
        cleanup_error: BaseException | None = None,
    ) -> None:
        super().__init__(action)
        self.events = events
        self.run_error = run_error
        self.cleanup_error = cleanup_error

    def collect(self) -> dict[str, object]:
        self.events.append(f"{self.action.name}.collect")
        return {}

    def run(self) -> dict[str, object]:
        self.events.append(f"{self.action.name}.run")
        if self.run_error is not None:
            raise self.run_error
        return {"name": self.action.name}

    def update(self) -> dict[str, object]:
        self.events.append(f"{self.action.name}.update")
        return self.result

    def cleanup(self) -> dict[str, object]:
        self.events.append(f"{self.action.name}.cleanup")
        if self.cleanup_error is not None:
            raise self.cleanup_error
        return self.result


class Increment(CALLBACK.Callback[CounterState, UnitAction]):
    """Read the prior result and publish one state-derived result."""

    def __init__(self, action: UnitAction, observed: list[dict[str, object]]) -> None:
        super().__init__(action)
        self.observed = observed

    def run(self) -> dict[str, object]:
        self.observed.append(dict(self.result))
        self.state.x += self.action.delta
        return {"name": self.action.name, "x": self.state.x}


def test_executor_creates_one_state_and_binds_every_callback_before_collect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The collection fans one executor-created state out before lifecycle work."""

    created = 0

    class TrackedContext(CONTEXT.ExecutionContext):
        def __init__(self) -> None:
            nonlocal created
            created += 1
            super().__init__()

    monkeypatch.setattr(EXECUTOR, "ExecutionContext", TrackedContext)
    events: list[str] = []
    first = EventCallback(UnitAction("first"), events)
    second = EventCallback(UnitAction("second"), events)

    executor = EXECUTOR.Executor(callbacks=CALLBACKS.Callbacks([first, second]))

    assert created == 1
    assert first.state is executor.state
    assert second.state is executor.state
    assert events == []


def test_callback_requires_a_skill_action_and_listify_preserves_order() -> None:
    """Callbacks require actions while collections retain single and iterable order."""
    with pytest.raises(TypeError):
        CALLBACK.Callback()
    with pytest.raises(TypeError, match="SkillAction"):
        CALLBACK.Callback(None)

    first = CALLBACK.Callback(UnitAction("first"))
    second = CALLBACK.Callback(UnitAction("second"))

    assert CALLBACKS.listify(first) == [first]
    assert CALLBACKS.listify(item for item in (first, second)) == [first, second]
    assert list(CALLBACKS.Callbacks(item for item in (first, second))) == [
        first,
        second,
    ]


def test_default_callback_returns_the_shared_empty_json_result() -> None:
    """Unused hooks return the executor-owned empty JSON dictionary."""
    callback = CALLBACK.Callback(UnitAction("empty"))
    executor = EXECUTOR.Executor(callbacks=CALLBACKS.Callbacks(callback))

    result = executor.run()

    assert result == {}
    assert result is executor.state.result
    assert result is callback.result


def test_callbacks_share_state_and_publish_one_state_owned_result() -> None:
    """Dependent callbacks see updates through one state pointer and result view."""
    observed: list[dict[str, object]] = []
    first = Increment(UnitAction("first", 1), observed)
    second = Increment(UnitAction("second", 2), observed)
    executor = CounterExecutor(CALLBACKS.Callbacks([first, second]))

    result = executor.run()

    assert executor.created == 1
    assert first.state is executor.state
    assert second.state is executor.state
    assert observed == [{}, {"name": "first", "x": 1}]
    assert executor.state.x == 3
    assert result == {"name": "second", "x": 3}
    assert result is executor.state.result
    assert first.result is executor.state.result
    assert second.result is executor.state.result


def test_failure_preserves_primary_error_and_cleans_up_in_reverse_order() -> None:
    """A run failure survives cleanup failures after reverse-order unwinding."""
    events: list[str] = []
    primary = RuntimeError("run failed")
    first = EventCallback(
        UnitAction("first"),
        events,
        cleanup_error=RuntimeError("first cleanup failed"),
    )
    failing = EventCallback(UnitAction("failing"), events, run_error=primary)
    executor = EXECUTOR.Executor(callbacks=CALLBACKS.Callbacks([first, failing]))

    with pytest.raises(RuntimeError, match="run failed") as raised:
        executor.run()

    assert raised.value is primary
    assert executor.state.failed is True
    assert events == [
        "first.collect",
        "first.run",
        "first.update",
        "failing.collect",
        "failing.run",
        "failing.cleanup",
        "first.cleanup",
    ]
    assert "cleanup failed: RuntimeError" in raised.value.__notes__


@pytest.mark.parametrize("invalid_state", [None, {}])
def test_executor_rejects_invalid_state_before_binding(invalid_state: object) -> None:
    """Even an empty callback collection cannot conceal an invalid state factory."""

    class InvalidExecutor(EXECUTOR.SkillExecutor):
        def create_state(self) -> object:
            return invalid_state

    with pytest.raises(TypeError, match="concrete SkillState"):
        InvalidExecutor(CALLBACKS.Callbacks([]))


@dataclass(frozen=True)
class ReadAction(SKILL_ACTION.SkillAction):
    """Read the counter without accepting increment inputs."""

    label: str


class ReadCounter(CALLBACK.Callback[CounterState, ReadAction]):
    """Read shared state through a distinct concrete action type."""

    def run(self) -> Mapping[str, object]:
        """Return an immutable view of the current counter.

        :returns: A mapping with the selected label and observed counter.
        """
        return MappingProxyType({"label": self.action.label, "x": self.state.x})


def test_workflow_combines_distinct_action_types_and_mapping_results() -> None:
    """Each callback retains its action type while sharing one concrete state."""
    increment = Increment(UnitAction("increment", 2), [])
    read = ReadCounter(ReadAction("observed"))
    callbacks: CALLBACKS.Callbacks[CounterState] = CALLBACKS.Callbacks(
        [increment, read]
    )
    executor = CounterExecutor(callbacks)

    result = executor.run()

    assert result == {"label": "observed", "x": 2}
    assert result is executor.state.result
    assert read.state is increment.state is executor.state
    assert json.loads(executor.state.to_json())["result"] == dict(result)


def test_callback_binding_cannot_transfer_an_executors_state() -> None:
    """Rebinding the same state is harmless; another owner is rejected."""
    callback = CALLBACK.Callback(UnitAction("owned"))
    callbacks = CALLBACKS.Callbacks(callback)
    first = EXECUTOR.Executor(callbacks)

    assert callbacks.bind(first.state) is callbacks
    with pytest.raises(ValueError, match="different state"):
        EXECUTOR.Executor(callbacks)

    assert callback.state is first.state
    assert first.run() is first.state.result


def test_every_hook_result_is_retained_in_the_shared_sequence() -> None:
    """The result tracks the last hook and records retain the complete order."""

    class EvidenceCallback(CALLBACK.Callback[CONTEXT.ExecutionContext, UnitAction]):
        def collect(self) -> Mapping[str, object]:
            return {"phase": "collect"}

        def run(self) -> Mapping[str, object]:
            return {"phase": "run"}

        def update(self) -> Mapping[str, object]:
            return {"phase": "update"}

        def cleanup(self) -> Mapping[str, object]:
            return {"phase": "cleanup"}

    executor = EXECUTOR.Executor(
        CALLBACKS.Callbacks(EvidenceCallback(UnitAction("proof")))
    )
    executor.state.records = ({"phase": "initial"},)

    result = executor.run()

    assert isinstance(executor.state.records, Sequence)
    assert [record["phase"] for record in executor.state.records] == [
        "initial",
        "collect",
        "run",
        "update",
        "cleanup",
    ]
    assert result is executor.state.result is executor.state.records[-1]
    assert result == {"phase": "cleanup"}


def test_state_reports_unsupported_values_at_serialization() -> None:
    """Mappings can hold objects; the declared encoder decides JSON support."""
    state = SKILL_STATE.SkillState(result={"unsupported": object()})

    with pytest.raises(TypeError, match="not JSON serializable"):
        state.to_json()
