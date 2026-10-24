"""One callback contract and its default lifecycle implementation.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic

from lib.python.core.abstract.skill_action import ActionT_co, SkillAction
from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.abstract.skill_result import SkillResult
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

    @abstractmethod
    def whoami(self) -> bool:
        """Require the provider identity and access established in invocation state.

        Provider authentication callbacks establish their runtime observation once;
        subsequent callbacks validate that retained observation without remote reads.

        :returns: True only when the declared provider requirements are satisfied.
        :raises NotImplementedError: A provider base omits its identity/access hook.
        """
        raise NotImplementedError("callbacks must implement whoami")

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

    def whoami(self) -> bool:
        """Accept an action whose execution requires no provider identity.

        :returns: True because this callback performs no external provider operation.
        """
        return True
