"""Normalize the plural, ordered callback interface.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from collections.abc import Iterable, Iterator, MutableSequence
from typing import Generic, Self, TypeAlias, Union

from lib.python.core.abstract.callback import AbstractCallback
from lib.python.core.abstract.skill_action import ActionT_co, SkillAction
from lib.python.core.abstract.skill_state import StateT

AbstractCallbacks: TypeAlias = MutableSequence[AbstractCallback[StateT, ActionT_co]]


def listify(
    value: Union[
        AbstractCallback[StateT, ActionT_co],
        Iterable[AbstractCallback[StateT, ActionT_co]],
    ],
) -> AbstractCallbacks[StateT, ActionT_co]:
    """Normalize one callback or an iterable without losing its type parameters.

    :param value: A callback or ordered iterable of callbacks; use an empty
        iterable for a workflow with no callbacks.
    :returns: A fresh list retaining the original callback objects and order.
    :raises TypeError: If value is neither a callback nor iterable, or any
        element does not implement AbstractCallback.
    """
    selected = [value] if isinstance(value, AbstractCallback) else list(value)
    if any(not isinstance(callback, AbstractCallback) for callback in selected):
        raise TypeError("callbacks must implement AbstractCallback")
    return selected


class Callbacks(Generic[StateT]):
    """Own an ordered collection and bind every callback to the same state."""

    def __init__(
        self,
        callbacks: Union[
            AbstractCallback[StateT, SkillAction],
            Iterable[AbstractCallback[StateT, SkillAction]],
        ],
    ):
        """Normalize the supplied callback instances once.

        :param callbacks: One callback or the workflow's ordered callbacks.
        :raises TypeError: If callbacks is not iterable or contains a value
            that does not implement AbstractCallback.
        """  # noqa: DOC502 - listify validates the callback collection.
        self.callbacks: AbstractCallbacks[StateT, SkillAction] = listify(callbacks)

    def bind(self, state: StateT) -> Self:
        """Bind every callback to the supplied shared state in declaration order.

        :param state: Concrete state created and owned by the workflow executor.
        :returns: This collection with the same callback instances and order.
        :raises TypeError: The supplied state does not implement SkillState.
        :raises ValueError: A callback already belongs to another state.
        """  # noqa: DOC502 - each callback validates its state binding.
        for callback in self.callbacks:
            callback.bind(state)

        return self

    def __iter__(self) -> Iterator[AbstractCallback[StateT, SkillAction]]:
        """Iterate over the contained callbacks.

        :returns: An iterator in the original workflow order.
        """
        return iter(self.callbacks)
