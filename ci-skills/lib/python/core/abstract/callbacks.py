"""Normalize the plural, ordered callback interface.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from collections.abc import Iterable, Iterator
from typing import Generic, Self, TypeAlias

from .callback import AbstractCallback
from .skill_action import ActionT_co, SkillAction
from .skill_state import StateT

AbstractCallbacks: TypeAlias = list[AbstractCallback[StateT, ActionT_co]]


def listify(
    value: AbstractCallback[StateT, ActionT_co]
    | Iterable[AbstractCallback[StateT, ActionT_co]]
    | None,
) -> AbstractCallbacks[StateT, ActionT_co]:
    """Normalize one callback or an iterable without losing its type parameters.

    :param value: One callback, an ordered iterable, or an empty collection.
    :returns: A fresh list retaining the original callback objects and order.
    :raises TypeError: If an element does not implement AbstractCallback.
    """
    if value is None:
        return []
    selected = [value] if isinstance(value, AbstractCallback) else list(value)
    if any(not isinstance(callback, AbstractCallback) for callback in selected):
        raise TypeError("callbacks must implement AbstractCallback")
    return selected


class Callbacks(Generic[StateT]):
    """Own an ordered collection and bind every callback to the same state."""

    def __init__(
        self,
        callbacks: AbstractCallback[StateT, SkillAction]
        | Iterable[AbstractCallback[StateT, SkillAction]],
    ) -> None:
        """Normalize the supplied callback instances once.

        :param callbacks: One callback or the workflow's ordered callbacks.
        """
        self.callbacks: AbstractCallbacks[StateT, SkillAction] = listify(callbacks)

    def bind(self, state: StateT) -> Self:
        """Pass one shared state reference to every contained callback.

        :param state: The state created and owned by the executor.
        :returns: This bound collection, retaining its callback order.
        :raises ValueError: If a callback already belongs to a different state.
        """
        for callback in self.callbacks:
            callback.bind(state)

        return self

    def __iter__(self) -> Iterator[AbstractCallback[StateT, SkillAction]]:
        """Iterate over the contained callbacks.

        :returns: An iterator in the original workflow order.
        """
        return iter(self.callbacks)
