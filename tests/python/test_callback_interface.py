"""Focused contracts for state-bound callback composition.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import MutableSequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Optional

import pytest
from conftest import import_script_module

CALLBACK = import_script_module("lib.python.core.abstract.callback")
CALLBACKS = import_script_module("lib.python.core.abstract.callbacks")
CONTEXT = import_script_module("lib.python.core.abstract.context")
ABSTRACT_EXECUTOR = import_script_module("lib.python.core.abstract.executor")
EXECUTOR = import_script_module("lib.python.core.executor")
SKILL_ACTION = import_script_module("lib.python.core.abstract.skill_action")
SKILL_PLUGIN_TYPE = import_script_module("lib.python.core.abstract.skill_plugin_type")
SKILL_STATE = import_script_module("lib.python.core.abstract.skill_state")


if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject


@dataclass(frozen=True)
class UnitAction(SKILL_ACTION.SkillAction):
    """Name one operation and its optional counter change for callback tests.

    :param name: Operation label included in lifecycle events and results.
    :param delta: Amount added to the shared counter by Increment.

    .. attribute :: name
        :type: str

        Operation label included in lifecycle events and results.

    .. attribute :: delta
        :type: int

        Amount added to the shared counter by Increment.
    """

    name: str
    delta: int = 0


@dataclass
class CounterState(SKILL_STATE.SkillState):
    """Keep a counter visible to every callback sharing this test state.

    :param x: Counter value before the next callback runs.

    .. attribute :: x
        :type: int

        Counter value before the next callback runs.
    """

    x: int = 0


class CounterExecutor(ABSTRACT_EXECUTOR.SkillExecutor[CounterState]):
    """Create one observable custom state for the bound callback collection."""

    def __init__(self, callbacks: CALLBACKS.Callbacks[CounterState]):
        """Count state creation while binding the supplied callbacks.

        :param callbacks: Ordered collection that receives this executor's state.
        """
        self.created = 0
        super().__init__(callbacks)

    def create_state(self) -> CounterState:
        """Construct and count one concrete state for this executor.

        :returns: Fresh CounterState shared by its callback collection.
        """
        self.created += 1
        return CounterState()


class EventCallback(CALLBACK.LocalCallback[CONTEXT.ExecutionContext, UnitAction]):
    """Record the standard lifecycle and optionally fail its run or cleanup."""

    def __init__(
        self,
        action: UnitAction,
        events: MutableSequence[str],
        *,
        run_error: Optional[BaseException] = None,
        cleanup_error: Optional[BaseException] = None,
    ):
        """Configure an event-recording callback and optional injected failures.

        :param action: Operation label used in recorded lifecycle events.
        :param events: Mutable event list shared with the test assertions.
        :param run_error: Exception raised by run, or ``None`` for successful execution.
        :param cleanup_error: Exception raised by cleanup, or ``None`` for successful cleanup.

        """
        super().__init__(action)
        self.events = events
        self.run_error = run_error
        self.cleanup_error = cleanup_error

    def collect(self) -> JSONObject:
        """Record entry to the collection hook.

        :returns: Empty collection-evidence mapping.
        """
        self.events.append(f"{self.action.name}.collect")
        return {}

    def run(self) -> JSONObject:
        """Record the operation and optionally raise its configured failure.

        :returns: Mapping containing this operation's name.
        :raises BaseException: Configured failure object supplied by the test.
        """  # noqa: DOC503 - The dynamic fixture exception is raised verbatim.
        self.events.append(f"{self.action.name}.run")
        if self.run_error is not None:
            raise self.run_error
        return {"name": self.action.name}

    def update(self) -> JSONObject:
        """Record entry to the update hook.

        :returns: Current shared workflow result.
        """
        self.events.append(f"{self.action.name}.update")
        return self.result

    def cleanup(self) -> JSONObject:
        """Record cleanup and optionally raise its configured failure.

        :returns: Current shared workflow result.
        :raises BaseException: Configured cleanup failure supplied by the test.
        """  # noqa: DOC503 - The dynamic fixture exception is raised verbatim.
        self.events.append(f"{self.action.name}.cleanup")
        if self.cleanup_error is not None:
            raise self.cleanup_error
        return self.state.result


class Increment(CALLBACK.LocalCallback[CounterState, UnitAction]):
    """Read the prior result and publish one state-derived result."""

    def __init__(self, action: UnitAction, observed: MutableSequence[JSONObject]):
        """Retain the counter action and the caller's observation list.

        :param action: Named increment with the amount to add to shared state.
        :param observed: Mutable list receiving this callback's pre-run result snapshots.

        """
        super().__init__(action)
        self.observed = observed

    def run(self) -> JSONObject:
        """Record the current callback result, then increment shared state.

        :returns: Mapping containing the action name and resulting counter value.
        """
        self.observed.append(dict(self.state.result))
        self.state.x += self.action.delta
        return {"name": self.action.name, "x": self.state.x}


class ProviderConfigurationCallback(CALLBACK.Callback[CounterState, UnitAction]):
    """Record provider setup and lifecycle calls without using a real provider."""

    def __init__(
        self,
        action: UnitAction,
        events: MutableSequence[str],
        plugin_type: SKILL_PLUGIN_TYPE.SkillPluginType,
        *,
        configured: bool = True,
        read_error: Optional[BaseException] = None,
    ):
        """Configure one deterministic provider callback test double.

        :param action: Named action retained by the callback.
        :param events: Ordered event journal owned by the test.
        :param plugin_type: Provider identity used for setup cache behavior.
        :param configured: Configuration readiness returned after a successful read.
        :param read_error: Failure raised while reading configuration, when supplied.
        """
        super().__init__(action)
        self.events = events
        self._plugin_type = plugin_type
        self.configured = configured
        self.read_error = read_error

    @property
    def plugin_type(self) -> SKILL_PLUGIN_TYPE.SkillPluginType:
        """Return the provider identity selected for this test callback."""
        return self._plugin_type

    def whoami(self) -> bool:
        """Satisfy identity for this provider-configuration-only test double."""
        return True

    def read_config(self) -> JSONObject:
        """Record one provider configuration read and optionally fail."""
        self.events.append(f"{self.action.name}.read_config")
        if self.read_error is not None:
            raise self.read_error
        return {}

    def is_configured(self) -> bool:
        """Record one readiness check and return the configured outcome."""
        self.events.append(f"{self.action.name}.is_configured")
        return self.configured

    def collect(self) -> JSONObject:
        """Record lifecycle collection after successful configuration."""
        self.events.append(f"{self.action.name}.collect")
        return {}

    def run(self) -> JSONObject:
        """Record lifecycle execution after successful configuration."""
        self.events.append(f"{self.action.name}.run")
        return {"name": self.action.name}

    def update(self) -> JSONObject:
        """Record lifecycle update after successful configuration."""
        self.events.append(f"{self.action.name}.update")
        return self.result

    def cleanup(self) -> JSONObject:
        """Record lifecycle cleanup after successful configuration."""
        self.events.append(f"{self.action.name}.cleanup")
        return self.state.result


def test_executor_creates_one_state_and_binds_every_callback_before_collect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bind one executor-created state to all callbacks before lifecycle work.

    :param monkeypatch: Fixture replacing the default context with a counted constructor.

    :returns: None; assertions in the body validate the regression.
    """

    created = 0

    class TrackedContext(CONTEXT.ExecutionContext):
        """Count construction of the standard command context."""

        def __init__(self):
            """Increment the test's construction count before normal context initialization."""
            nonlocal created
            created += 1
            super().__init__()

    monkeypatch.setattr(EXECUTOR, "ExecutionContext", TrackedContext)
    events: MutableSequence[str] = []
    first = EventCallback(UnitAction("first"), events)
    second = EventCallback(UnitAction("second"), events)

    executor = EXECUTOR.Executor(callbacks=CALLBACKS.Callbacks([first, second]))

    assert created == 1
    assert first.state is executor.state
    assert second.state is executor.state
    assert events == []


def test_callback_requires_a_skill_action_and_listify_preserves_order() -> None:
    """Callbacks require actions while collections retain single and iterable order.
    :returns: None; assertions in the body validate the regression.
    """
    with pytest.raises(TypeError, match="abstract"):
        CALLBACK.Callback(UnitAction("abstract"))
    with pytest.raises(TypeError, match="SkillAction"):
        CALLBACK.LocalCallback(None)

    first = CALLBACK.LocalCallback(UnitAction("first"))
    second = CALLBACK.LocalCallback(UnitAction("second"))

    assert CALLBACKS.listify(first) == [first]
    assert CALLBACKS.listify(item for item in (first, second)) == [first, second]
    assert list(CALLBACKS.Callbacks(item for item in (first, second))) == [
        first,
        second,
    ]


def test_default_callback_returns_the_shared_empty_json_result() -> None:
    """Unused hooks return the executor-owned empty JSON dictionary.
    :returns: None; assertions in the body validate the regression.
    """
    callback = CALLBACK.LocalCallback(UnitAction("empty"))
    executor = EXECUTOR.Executor(callbacks=CALLBACKS.Callbacks(callback))

    result = executor.run()

    assert result == {}
    assert result is executor.state.result
    assert result is callback.result
    assert executor.state.results[callback] is callback.result
    assert callback.plugin_type is SKILL_PLUGIN_TYPE.SkillPluginType.LOCAL
    assert executor.state.is_config_checked(SKILL_PLUGIN_TYPE.SkillPluginType.LOCAL)


def test_callbacks_share_state_and_publish_one_state_owned_result() -> None:
    """Dependent callbacks see updates through one state pointer and result view.
    :returns: None; assertions in the body validate the regression.
    """
    observed: MutableSequence[JSONObject] = []
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
    assert first.result == {"name": "first", "x": 1}
    assert second.result == {"name": "second", "x": 3}
    assert executor.state.results[first] is first.result
    assert executor.state.results[second] is second.result


def test_empty_run_results_are_owned_by_each_callback() -> None:
    """Store independent empty run mappings for every callback instance.
    :returns: None; assertions in the body validate the regression.
    """
    first = CALLBACK.LocalCallback(UnitAction("first"))
    second = CALLBACK.LocalCallback(UnitAction("second"))
    executor = EXECUTOR.Executor(callbacks=CALLBACKS.Callbacks([first, second]))

    result = executor.run()

    assert result == {}
    assert first.result == {}
    assert second.result == {}
    assert first.result is executor.state.results[first]
    assert second.result is executor.state.results[second]
    assert first.result is not second.result


def test_failure_preserves_primary_error_and_cleans_up_in_reverse_order() -> None:
    """A run failure survives cleanup failures after reverse-order unwinding.
    :returns: None; assertions in the body validate the regression.
    """
    events: MutableSequence[str] = []
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
    assert executor.state.cleanup_failures == [
        {"callback": "EventCallback", "reason": "first cleanup failed"}
    ]
    assert "cleanup failed: RuntimeError" in raised.value.__notes__


@pytest.mark.parametrize("invalid_state", [None, {}])
def test_executor_rejects_invalid_state_before_binding(invalid_state: object) -> None:
    """Reject an invalid state factory even when the callback collection is empty.

    :param invalid_state: Non-SkillState value returned by the test factory.

    :returns: None; assertions in the body validate the regression.
    """

    class InvalidExecutor(ABSTRACT_EXECUTOR.SkillExecutor):
        """Supply the parametrized invalid state for constructor validation."""

        def create_state(self) -> object:
            """Return the selected invalid state without binding callbacks.

            :returns: The parametrized non-SkillState test value.
            """
            return invalid_state

    with pytest.raises(TypeError, match="concrete SkillState"):
        InvalidExecutor(CALLBACKS.Callbacks([]))


@dataclass(frozen=True)
class ReadAction(SKILL_ACTION.SkillAction):
    """Name a read of shared state without accepting increment inputs.

    :param label: Label included beside the observed counter value.

    .. attribute :: label
        :type: str

        Label included beside the observed counter value.
    """

    label: str


class ReadCounter(CALLBACK.LocalCallback[CounterState, ReadAction]):
    """Read shared state through a distinct concrete action type."""

    def run(self) -> JSONObject:
        """Return an immutable view of the current counter.

        :returns: A mapping with the selected label and observed counter.
        """
        return MappingProxyType({"label": self.action.label, "x": self.state.x})


def test_workflow_combines_distinct_action_types_and_mapping_results() -> None:
    """Each callback retains its action type while sharing one concrete state.
    :returns: None; assertions in the body validate the regression.
    """
    increment = Increment(UnitAction("increment", 2), [])
    read = ReadCounter(ReadAction("observed"))
    callbacks: CALLBACKS.Callbacks[CounterState] = CALLBACKS.Callbacks(
        [increment, read]
    )
    executor = CounterExecutor(callbacks)

    result = executor.run()

    assert result == {"label": "observed", "x": 2}
    assert result is executor.state.result
    assert increment.result == {"name": "increment", "x": 2}
    assert read.result is result
    assert read.state is increment.state is executor.state
    assert json.loads(executor.state.to_json())["result"] == dict(result)


def test_callback_binding_cannot_transfer_an_executors_state() -> None:
    """Rebinding the same state is harmless; another owner is rejected.
    :returns: None; assertions in the body validate the regression.
    """
    callback = CALLBACK.LocalCallback(UnitAction("owned"))
    callbacks = CALLBACKS.Callbacks(callback)
    first = EXECUTOR.Executor(callbacks)

    assert callbacks.bind(first.state) is callbacks
    with pytest.raises(ValueError, match="different state"):
        EXECUTOR.Executor(callbacks)

    assert callback.state is first.state
    assert first.run() is first.state.result


def test_every_hook_result_is_retained_in_the_shared_sequence() -> None:
    """The result tracks the last hook and records retain the complete order.
    :returns: None; assertions in the body validate the regression.
    """

    class EvidenceCallback(
        CALLBACK.LocalCallback[CONTEXT.ExecutionContext, UnitAction]
    ):
        """Emit distinct evidence for each hook to expose their execution order."""

        def collect(self) -> JSONObject:
            """Record the collect phase as independent hook evidence.

            :returns: Mapping identifying the ``collect`` phase.
            """
            return {"phase": "collect"}

        def run(self) -> JSONObject:
            """Record the run phase as independent hook evidence.

            :returns: Mapping identifying the ``run`` phase.
            """
            return {"phase": "run"}

        def update(self) -> JSONObject:
            """Record the update phase as independent hook evidence.

            :returns: Mapping identifying the ``update`` phase.
            """
            return {"phase": "update"}

        def cleanup(self) -> JSONObject:
            """Record the cleanup phase as independent hook evidence.

            :returns: Mapping identifying the ``cleanup`` phase.
            """
            return {"phase": "cleanup"}

    executor = EXECUTOR.Executor(
        CALLBACKS.Callbacks(EvidenceCallback(UnitAction("proof")))
    )
    executor.state.records.append({"phase": "initial"})

    result = executor.run()

    assert isinstance(executor.state.records, MutableSequence)
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
    """Mappings can hold objects; the declared encoder decides JSON support.
    :returns: None; assertions in the body validate the regression.
    """
    state = SKILL_STATE.SkillState(result={"unsupported": object()})

    with pytest.raises(TypeError, match="not JSON serializable"):
        state.to_json()


def test_legacy_state_pickle_initializes_plugin_cache_before_callback_binding(
    tmp_path: Path,
) -> None:
    """Restore pre-cache state and configure its shared provider once.

    :param tmp_path: Isolated pickle destination for the legacy state shape.
    :returns: None; assertions prove state preservation and configuration setup.
    """
    persisted = CounterState(
        x=7,
        name="legacy",
        environment={"scope": "unit"},
        result={"phase": "persisted"},
        records=[{"phase": "persisted"}],
    )
    del persisted.plugin_ready
    state_path = tmp_path / "legacy-state.pkl"
    persisted.save_state_to_file(str(state_path))
    restored = CounterState.load_state_from_file(str(state_path))
    events: MutableSequence[str] = []
    callbacks = CALLBACKS.Callbacks(
        [
            ProviderConfigurationCallback(
                UnitAction("first"),
                events,
                SKILL_PLUGIN_TYPE.SkillPluginType.GITLAB,
            ),
            ProviderConfigurationCallback(
                UnitAction("second"),
                events,
                SKILL_PLUGIN_TYPE.SkillPluginType.GITLAB,
            ),
        ]
    )

    executor = EXECUTOR.WorkflowExecutor(restored, callbacks)

    assert restored.x == 7
    assert restored.name == "legacy"
    assert restored.environment == {"scope": "unit"}
    assert restored.result == {"phase": "persisted"}
    assert restored.records == [{"phase": "persisted"}]
    assert events == [
        "first.read_config",
        "first.is_configured",
        "second.is_configured",
    ]
    assert restored.is_config_checked(SKILL_PLUGIN_TYPE.SkillPluginType.GITLAB)

    assert executor.run() == {"name": "second"}
    assert restored.records[0] == {"phase": "persisted"}
    assert events[-2:] == ["second.cleanup", "first.cleanup"]


def test_callbacks_configure_one_plugin_once_before_lifecycle_and_keep_json_stable() -> (
    None
):
    """Configure a shared provider once before either callback enters its lifecycle.

    :returns: None; assertions prove setup caching and report serialization.
    """
    events: MutableSequence[str] = []
    first = ProviderConfigurationCallback(
        UnitAction("first"),
        events,
        SKILL_PLUGIN_TYPE.SkillPluginType.GITLAB,
    )
    second = ProviderConfigurationCallback(
        UnitAction("second"),
        events,
        SKILL_PLUGIN_TYPE.SkillPluginType.GITLAB,
    )
    expected_json = json.loads(CounterState().to_json())

    executor = CounterExecutor(CALLBACKS.Callbacks([first, second]))

    assert events == [
        "first.read_config",
        "first.is_configured",
        "second.is_configured",
    ]
    assert executor.state.is_config_checked(SKILL_PLUGIN_TYPE.SkillPluginType.GITLAB)
    assert json.loads(executor.state.to_json()) == expected_json

    executor.run()

    assert events == [
        "first.read_config",
        "first.is_configured",
        "second.is_configured",
        "first.collect",
        "first.run",
        "first.update",
        "second.collect",
        "second.run",
        "second.update",
        "second.cleanup",
        "first.cleanup",
    ]


def test_callback_configuration_requires_mapping_before_cache_or_later_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject invalid configuration output before marking a provider ready.

    :param monkeypatch: Pytest fixture replacing only the test callback output.
    :returns: None; assertions prove no cache, hooks, or later setup follows.
    """
    events: MutableSequence[str] = []
    state = CounterState()
    first = ProviderConfigurationCallback(
        UnitAction("first"),
        events,
        SKILL_PLUGIN_TYPE.SkillPluginType.GITLAB,
    )
    later = ProviderConfigurationCallback(
        UnitAction("later"),
        events,
        SKILL_PLUGIN_TYPE.SkillPluginType.KUBERNETES,
    )

    def invalid_read_config() -> None:
        """Record the configuration read while returning an invalid result."""
        events.append("first.read_config")

    monkeypatch.setattr(first, "read_config", invalid_read_config)

    with pytest.raises(TypeError, match="mapping"):
        CALLBACKS.Callbacks([first, later]).bind(state)

    assert events == ["first.read_config"]
    assert not state.is_config_checked(SKILL_PLUGIN_TYPE.SkillPluginType.GITLAB)
    assert not state.is_config_checked(SKILL_PLUGIN_TYPE.SkillPluginType.KUBERNETES)


@pytest.mark.parametrize(
    ("configured", "read_error", "expected_events", "error"),
    (
        (
            False,
            None,
            [
                "x.read_config",
                "x.is_configured",
                "y.read_config",
                "y.is_configured",
            ],
            ValueError,
        ),
        (
            True,
            RuntimeError("y read failed"),
            ["x.read_config", "x.is_configured", "y.read_config"],
            RuntimeError,
        ),
    ),
)
def test_callback_configuration_failure_stops_later_provider_and_is_not_cached(
    configured: bool,
    read_error: Optional[BaseException],
    expected_events: MutableSequence[str],
    error: type[BaseException],
) -> None:
    """Reject missing or unreadable setup before lifecycle work and later setup.

    :param configured: Readiness value for the failing Kubernetes callback.
    :param read_error: Read failure injected into that callback, if any.
    :param expected_events: Configuration-only journal expected before the failure.
    :param error: Exception type expected from configuration binding.
    :returns: None; assertions prove failed setup is never cached.
    """
    events: MutableSequence[str] = []
    state = CounterState()
    callbacks = CALLBACKS.Callbacks(
        [
            ProviderConfigurationCallback(
                UnitAction("x"), events, SKILL_PLUGIN_TYPE.SkillPluginType.GITLAB
            ),
            ProviderConfigurationCallback(
                UnitAction("y"),
                events,
                SKILL_PLUGIN_TYPE.SkillPluginType.KUBERNETES,
                configured=configured,
                read_error=read_error,
            ),
            ProviderConfigurationCallback(
                UnitAction("z"), events, SKILL_PLUGIN_TYPE.SkillPluginType.HARBOR
            ),
        ]
    )

    with pytest.raises(error):
        callbacks.bind(state)

    assert events == expected_events
    assert state.is_config_checked(SKILL_PLUGIN_TYPE.SkillPluginType.GITLAB)
    assert not state.is_config_checked(SKILL_PLUGIN_TYPE.SkillPluginType.KUBERNETES)
    assert not state.is_config_checked(SKILL_PLUGIN_TYPE.SkillPluginType.HARBOR)
    assert all(
        not event.endswith((".collect", ".run", ".update", ".cleanup"))
        for event in events
    )


def test_cached_plugin_rechecks_later_callback_before_lifecycle_or_later_config() -> (
    None
):
    """Reject a later unconfigured callback even when its plugin was read already.

    :returns: None; assertions prove every selected callback is checked.
    """
    events: MutableSequence[str] = []
    state = CounterState()
    callbacks = CALLBACKS.Callbacks(
        [
            ProviderConfigurationCallback(
                UnitAction("first"),
                events,
                SKILL_PLUGIN_TYPE.SkillPluginType.GITLAB,
            ),
            ProviderConfigurationCallback(
                UnitAction("second"),
                events,
                SKILL_PLUGIN_TYPE.SkillPluginType.GITLAB,
                configured=False,
            ),
            ProviderConfigurationCallback(
                UnitAction("later"),
                events,
                SKILL_PLUGIN_TYPE.SkillPluginType.KUBERNETES,
            ),
        ]
    )

    with pytest.raises(ValueError, match="gitlab_configuration_missing"):
        callbacks.bind(state)

    assert events == [
        "first.read_config",
        "first.is_configured",
        "second.is_configured",
    ]
    assert state.is_config_checked(SKILL_PLUGIN_TYPE.SkillPluginType.GITLAB)
    assert not state.is_config_checked(SKILL_PLUGIN_TYPE.SkillPluginType.KUBERNETES)
    assert all(
        not event.endswith((".collect", ".run", ".update", ".cleanup"))
        for event in events
    )


def test_plugin_configuration_cache_is_per_executor_and_state_rejects_strings() -> None:
    """Cache successful setup per state and require enum identities.

    :returns: None; assertions prove executor isolation and key validation.
    """
    events: MutableSequence[str] = []
    first = CounterExecutor(
        CALLBACKS.Callbacks(
            ProviderConfigurationCallback(
                UnitAction("first"),
                events,
                SKILL_PLUGIN_TYPE.SkillPluginType.GITHUB,
            )
        )
    )
    second = CounterExecutor(
        CALLBACKS.Callbacks(
            ProviderConfigurationCallback(
                UnitAction("second"),
                events,
                SKILL_PLUGIN_TYPE.SkillPluginType.GITHUB,
            )
        )
    )

    assert events == [
        "first.read_config",
        "first.is_configured",
        "second.read_config",
        "second.is_configured",
    ]
    assert first.state is not second.state
    with pytest.raises(TypeError, match="SkillPluginType"):
        first.state.is_config_checked("github")
