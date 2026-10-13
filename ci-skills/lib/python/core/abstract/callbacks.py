"""Normalize the plural, ordered callback interface.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from collections.abc import Iterable, Iterator, MutableSequence
from typing import Generic, Self, TypeAlias, Union

from lib.python.core.abstract.callback import AbstractCallback
from lib.python.core.abstract.skill_action import ActionT_co, SkillAction
from lib.python.core.abstract.skill_result import require_result
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
        """Bind and configure callbacks before any lifecycle hook can run.

        Read each plugin once, then check every callback against retained state.

        :param state: Concrete state created and owned by the workflow executor.
        :returns: This collection with the same callback instances and order.
        :raises TypeError: The supplied state does not implement SkillState, or
            read_config returns a value that is not a mapping.
        :raises ValueError: A callback belongs to another state or its plugin is unconfigured.
        """  # noqa: DOC503 - each callback validates its state binding and configuration.
        for callback in self.callbacks:
            callback.bind(state)
            plugin_type = callback.plugin_type
            if not state.is_config_checked(plugin_type):
                require_result(callback.read_config())
            if not callback.is_configured():
                raise ValueError(f"{plugin_type.value}_configuration_missing")
            state.plugin_ready[plugin_type] = True

        return self

    def __iter__(self) -> Iterator[AbstractCallback[StateT, SkillAction]]:
        """Iterate over the contained callbacks.

        :returns: An iterator in the original workflow order.
        """
        return iter(self.callbacks)
