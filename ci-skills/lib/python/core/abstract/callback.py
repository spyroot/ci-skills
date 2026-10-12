"""One callback contract and its default lifecycle implementation.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic

from .skill_action import ActionT_co, SkillAction
from .skill_result import SkillResult
from .skill_state import SkillState, StateT


class AbstractCallback(ABC, Generic[StateT, ActionT_co]):
    """Require an action and bind to the executor-owned state."""

    @abstractmethod
    def __init__(self, skill_action: ActionT_co) -> None:
        """Retain the required action before the executor binds shared state.

        :param skill_action: Required read, authentication, or mutation inputs.
        :raises TypeError: If the supplied value is not a SkillAction.
        """
        if not isinstance(skill_action, SkillAction):
            raise TypeError("skill_action must implement SkillAction")
        self._action: ActionT_co = skill_action

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
        """Bind the state supplied by the callback collection.

        :param state: The one mutable state owned by the executor.
        :raises ValueError: If this callback already belongs to a different state.
        :raises NotImplementedError: If a subclass invokes this abstract body.
        """
        raise NotImplementedError("concrete callbacks must implement this hook")

    @property
    def result(self) -> SkillResult:
        """Read the result owned by the shared state.

        :returns: The shared JSON result, including an empty dictionary.
        """
        return self.state.result

    @property
    def complete(self) -> bool:
        """Report whether the next callback may start.

        :returns: True unless a concrete callback reports incomplete work.
        """
        return True

    @abstractmethod
    def collect(self) -> SkillResult:
        """Collect the action inputs.

        :returns: JSON evidence from collection.
        :raises NotImplementedError: If a subclass invokes this abstract body.
        """
        raise NotImplementedError("concrete callbacks must implement this hook")

    @abstractmethod
    def run(self) -> SkillResult:
        """Perform the required action.

        :returns: The JSON result to retain in the shared state.
        :raises NotImplementedError: If a subclass invokes this abstract body.
        """
        raise NotImplementedError("concrete callbacks must implement this hook")

    @abstractmethod
    def update(self) -> SkillResult:
        """Apply the action's post-run work.

        :returns: JSON evidence from the update.
        :raises NotImplementedError: If a subclass invokes this abstract body.
        """
        raise NotImplementedError("concrete callbacks must implement this hook")

    @abstractmethod
    def cleanup(self) -> SkillResult:
        """Release resources owned by this callback.

        :returns: JSON evidence from cleanup.
        :raises NotImplementedError: If a subclass invokes this abstract body.
        """
        raise NotImplementedError("concrete callbacks must implement this hook")


class Callback(AbstractCallback[StateT, ActionT_co]):
    """Provide state access and defaults for hooks a callback does not need."""

    def __init__(self, skill_action: ActionT_co) -> None:
        """Retain the required action; the collection supplies state through bind.

        :param skill_action: Required inputs for this callback's operation.
        """
        super().__init__(skill_action)

    @property
    def state(self) -> StateT:
        """Read the retained state reference.

        :returns: The same object assigned through the state setter.
        """
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

    def bind(self, state: StateT) -> None:
        """Retain the collection's shared state before any hook runs.

        :param state: The one mutable state owned by the executor.
        :raises ValueError: If this callback already belongs to a different state.
        """
        self.state = state

    def collect(self) -> SkillResult:
        """Keep the state result when collection is unnecessary.

        :returns: The existing state-owned JSON dictionary.
        """
        return self.result

    def run(self) -> SkillResult:
        """Keep the state result when this callback uses other hooks only.

        :returns: The existing state-owned JSON dictionary.
        """
        return self.result

    def update(self) -> SkillResult:
        """Keep the state result when post-run work is unnecessary.

        :returns: The existing state-owned JSON dictionary.
        """
        return self.result

    def cleanup(self) -> SkillResult:
        """Keep the state result when no resources need releasing.

        :returns: The existing state-owned JSON dictionary.
        """
        return self.result
