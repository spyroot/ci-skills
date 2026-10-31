"""One callback contract and its default lifecycle implementation.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Final, Generic

from lib.python.core.abstract.skill_action import ActionT_co, SkillAction
from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.abstract.skill_result import SkillResult, require_result
from lib.python.core.abstract.skill_state import SkillState, StateT


class AbstractCallback(ABC, Generic[StateT, ActionT_co]):
    """Define one concrete action executed against shared workflow state.

    The action is required at construction. The executor binds one state before
    calling hooks in the workflow's declared order; each callback retains its own
    run result in that state.
    """

    @abstractmethod
    def __init__(self, action: ActionT_co):
        """Retain the required action before the executor binds shared state.

        :param action: Required read, authentication, or mutation inputs.
        :raises TypeError: If the supplied value is not a SkillAction.
        """
        if not isinstance(action, SkillAction):
            raise TypeError("skill_action must implement SkillAction")
        self._action: ActionT_co = action

    @property
    @abstractmethod
    def plugin_type(self) -> SkillPluginType:
        """Identify the plugin that owns this callback's configuration.

        :returns: Enum key shared by callbacks using the same plugin.
        :raises NotImplementedError: A concrete callback omits its plugin identity.
        """
        raise NotImplementedError("callbacks must declare their plugin type")

    @abstractmethod
    def read_config(self) -> SkillResult:
        """Read required plugin inputs once and retain them in shared state.

        :returns: Safe setup evidence, including an empty mapping when appropriate.
        :raises NotImplementedError: A concrete plugin omits its configuration reader.
        """
        raise NotImplementedError("callbacks must implement read_config")

    @abstractmethod
    def is_configured(self) -> bool:
        """Validate the plugin configuration retained in shared state.

        :returns: Whether this callback's required configuration is available.
        :raises NotImplementedError: A concrete plugin omits its readiness check.
        """
        raise NotImplementedError("callbacks must implement is_configured")

    @property
    def action(self) -> ActionT_co:
        """Read the concrete action retained by this callback.

        :returns: The required operation inputs supplied at construction.
        """
        return self._action

    @property
    @abstractmethod
    def state(self) -> StateT:
        """Read the bound shared state.

        :returns: The exact state instance supplied by the caller.
        :raises NotImplementedError: If a subclass invokes this abstract body.
        """
        raise NotImplementedError("concrete callbacks must implement this hook")

    @state.setter
    @abstractmethod
    def state(self, value: StateT) -> None:
        """Assign the shared state reference.

        :param value: Concrete state shared by this workflow.
        :raises NotImplementedError: If a subclass invokes this abstract body.
        """
        raise NotImplementedError("concrete callbacks must implement this hook")

    @abstractmethod
    def bind(self, state: StateT) -> None:
        """Bind the executor's shared state before any lifecycle hook runs.

        :param state: One concrete mutable state owned by the workflow executor.
        :raises NotImplementedError: A subclass invokes the unimplemented abstract body.
        """
        raise NotImplementedError("concrete callbacks must implement this hook")

    @property
    def result(self) -> SkillResult:
        """Read this callback's result from the shared state.

        :returns: This callback's most recent run mapping, or its initial empty
            mapping before execution; another callback cannot replace this entry.
        :raises AttributeError: If the executor has not bound this callback yet.
        """  # noqa: DOC502 - state access raises AttributeError before binding.
        return self.state.results[self]

    @property
    def complete(self) -> bool:
        """Report whether the next callback may start.

        :returns: True unless a concrete callback reports incomplete work.
        """
        return True

    @abstractmethod
    def collect(self) -> SkillResult:
        """Collect inputs and observations needed by the action.

        :returns: Result mapping from this hook; an empty mapping represents no data.
        :raises NotImplementedError: A subclass invokes the unimplemented abstract body.
        """
        raise NotImplementedError("concrete callbacks must implement this hook")

    @abstractmethod
    def run(self) -> SkillResult:
        """Perform the concrete action and produce its reportable result.

        :returns: Result mapping from this hook; an empty mapping represents no data.
        :raises NotImplementedError: A subclass invokes the unimplemented abstract body.
        """
        raise NotImplementedError("concrete callbacks must implement this hook")

    @abstractmethod
    def update(self) -> SkillResult:
        """Perform the action's post-run work.

        :returns: Result mapping from this hook; an empty mapping represents no data.
        :raises NotImplementedError: A subclass invokes the unimplemented abstract body.
        """
        raise NotImplementedError("concrete callbacks must implement this hook")

    @abstractmethod
    def cleanup(self) -> SkillResult:
        """Release or recover resources owned by this callback.

        :returns: Result mapping from this hook; an empty mapping represents no data.
        :raises NotImplementedError: A subclass invokes the unimplemented abstract body.
        """
        raise NotImplementedError("concrete callbacks must implement this hook")


class Callback(AbstractCallback[StateT, ActionT_co]):
    """Provide state access and defaults for hooks a callback does not need."""

    def __init__(self, action: ActionT_co):
        """Retain the required action; the collection supplies state through bind.

        :param action: Required inputs for this callback's operation.
        :raises TypeError: If action does not implement SkillAction.
        """  # noqa: DOC502 - AbstractCallback validates the required action.
        super().__init__(action)

    @property
    def state(self) -> StateT:
        """Read the retained state reference.

        :returns: The same object assigned through the state setter.
        :raises AttributeError: If the executor has not bound this callback yet.
        """  # noqa: DOC502 - unbound state attribute access raises AttributeError.
        return self._state

    @state.setter
    def state(self, value: StateT) -> None:
        """Retain the supplied state without copying it.

        :param value: Concrete state shared by this workflow.
        :raises TypeError: If the supplied value is not a SkillState.
        :raises ValueError: If this callback already belongs to a different state.
        """
        if not isinstance(value, SkillState):
            raise TypeError("state must implement SkillState")
        if hasattr(self, "_state") and self._state is not value:
            raise ValueError("callback already belongs to a different state")
        self._state: StateT = value
        value.results.setdefault(self, {})

    def bind(self, state: StateT) -> None:
        """Retain the collection's shared state before any hook runs.

        :param state: The one mutable state owned by the executor.
        :raises TypeError: If state does not implement SkillState.
        :raises ValueError: If this callback already belongs to a different state.
        """  # noqa: DOC502 - the state setter validates ownership and type.
        self.state = state

    def collect(self) -> SkillResult:
        """Retain the current workflow result when no collection is needed.

        :returns: Existing state-owned result mapping, without copying it.
        :raises AttributeError: The executor has not bound this callback to a state.
        """  # noqa: DOC502 - state access requires an existing binding.
        return self.state.result

    def run(self) -> SkillResult:
        """Produce an empty result when the action has no reportable data.

        :returns: A new empty mapping owned by this callback's run.
        """
        return {}

    def update(self) -> SkillResult:
        """Retain the current workflow result when no post-run work is needed.

        :returns: Existing state-owned result mapping, without copying it.
        :raises AttributeError: The executor has not bound this callback to a state.
        """  # noqa: DOC502 - state access requires an existing binding.
        return self.state.result

    def cleanup(self) -> SkillResult:
        """Retain the current workflow result when no resource cleanup is needed.

        :returns: Existing state-owned result mapping, without copying it.
        :raises AttributeError: The executor has not bound this callback to a state.
        """  # noqa: DOC502 - state access requires an existing binding.
        return self.state.result


class LocalCallback(Callback[StateT, ActionT_co]):
    """Declare that an action uses no external provider configuration."""

    @property
    def plugin_type(self) -> SkillPluginType:
        """Identify the configuration shared by this plugin's callbacks.

        :returns: The local plugin's configuration cache key.
        """
        return SkillPluginType.LOCAL

    def read_config(self) -> SkillResult:
        """Keep all local action inputs in the supplied action and shared state.

        :returns: Empty evidence because no provider configuration is read.
        """
        return {}

    def is_configured(self) -> bool:
        """Accept a local callback whose required action was already constructed.

        :returns: True because this plugin has no external configuration.
        """
        return True


DEFERRED_CONFIGURATION_MISSING: Final = "deferred_component_configuration_missing"
DEFERRED_COLLECTION_REQUIRED: Final = "deferred_component_collection_required"


class DeferredCallback(LocalCallback[StateT, ActionT_co]):
    """Bind an existing callback after upstream observations resolve its inputs.

    .. attribute :: factory
        :type: Callable[[StateT], AbstractCallback]

        Factory receiving the same executor-owned state at collection time.

    .. attribute :: child
        :type: AbstractCallback | None

        Callback constructed during collection, absent before that lifecycle step.
    """

    factory: Callable[[StateT], AbstractCallback]
    child: AbstractCallback | None

    def __init__(
        self, action: ActionT_co, factory: Callable[[StateT], AbstractCallback]
    ):
        """Retain a callback factory until preceding observations complete.

        :param action: Parent inputs retaining the current operation identity.
        :param factory: Constructs one callback against the current shared state.
        :raises TypeError: Parent inputs do not implement SkillAction.
        """  # noqa: DOC502 - Shared callback initialization validates the action.
        super().__init__(action)
        self.factory = factory
        self.child = None

    def collect(self) -> SkillResult:
        """Construct and bind the child to the same executor-owned state.

        :returns: Child collection evidence.
        :raises ValueError: Required provider configuration is unavailable.
        """
        child = self.factory(self.state)
        child.bind(self.state)
        require_result(child.read_config())
        if not child.is_configured():
            raise ValueError(DEFERRED_CONFIGURATION_MISSING)
        self.child = child
        return child.collect()

    def run(self) -> SkillResult:
        """Run the child after collection and retain its own result slot.

        :returns: Child run evidence.
        :raises ValueError: Collection did not construct a callback.
        """
        if self.child is None:
            raise ValueError(DEFERRED_COLLECTION_REQUIRED)
        result = require_result(self.child.run())
        self.state.results[self.child] = result
        return result

    def update(self) -> SkillResult:
        """Delegate the child's post-run lifecycle without copying its result.

        :returns: Child update evidence.
        :raises ValueError: Collection did not construct a callback.
        """
        if self.child is None:
            raise ValueError(DEFERRED_COLLECTION_REQUIRED)
        result = require_result(self.child.update())
        self.state.results[self.child] = result
        return result

    def cleanup(self) -> SkillResult:
        """Delegate cleanup for the exact child constructed during collection.

        :returns: Child cleanup evidence, or no evidence before collection.
        """
        if self.child is None:
            return {}
        try:
            return self.child.cleanup()
        finally:
            self.state.results.pop(self.child, None)

    @property
    def complete(self) -> bool:
        """Retain the existing child's continuation contract.

        :returns: Child completeness, false before collection.
        """
        return self.child is not None and self.child.complete
