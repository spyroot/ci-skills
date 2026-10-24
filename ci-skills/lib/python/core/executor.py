"""Construct command workflows using the provider-independent executor.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from lib.python.core.abstract.callbacks import Callbacks
from lib.python.core.abstract.context import ExecutionContext
from lib.python.core.abstract.executor import SkillExecutor
from lib.python.core.abstract.skill_state import StateT
from lib.python.core.abstract.spec import Spec


class Executor(SkillExecutor[ExecutionContext]):
    """Use the standard command context for callbacks that need no custom state."""

    def create_state(self) -> ExecutionContext:
        """Create the context before binding the callback collection.

        :returns: A fresh command context with an empty result dictionary.
        """
        return ExecutionContext()


class WorkflowExecutor(SkillExecutor[StateT]):
    """Bind a command's explicit context through the shared executor lifecycle."""

    def __init__(
        self, context: StateT, callbacks: Callbacks[StateT], spec: Spec | None = None
    ):
        """Bind the command's existing state to its declared callback sequence.

        :param context: Concrete state owned by this workflow invocation.
        :param callbacks: Ordered callbacks sharing this exact state instance.
        :param spec: Optional provider declaration; otherwise retain or load shared state.
        :raises TypeError: The supplied context does not implement SkillState,
            or specification/configuration inputs have an invalid shape.
        :raises ValueError: A callback belongs to another state or its required
            plugin configuration is unavailable, or the shared declaration would change.
        :raises OSError: The required installed specification cannot be read.
        """  # noqa: DOC502 - SkillExecutor validates state and callback ownership.
        self._context = context
        super().__init__(callbacks, spec=spec)

    def create_state(self) -> StateT:
        """Supply the concrete context selected by the workflow.

        :returns: The invocation's one shared context.
        """
        return self._context
