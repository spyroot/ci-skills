"""Own shared state, bind callbacks, and execute their ordered lifecycle.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from typing import Generic

from lib.python.sys.signals import recoverable_signals
from lib.python.core.abstract.callbacks import AbstractCallbacks, Callbacks
from lib.python.core.abstract.context import ExecutionContext
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.abstract.skill_result import SkillResult
from lib.python.core.abstract.skill_state import SkillState, StateT


class SkillExecutor(ABC, Generic[StateT]):
    """Create one concrete state and bind it through the callback collection."""

    def __init__(self, callbacks: Callbacks[StateT]) -> None:
        """Create and bind shared state before any lifecycle hook runs.

        :param callbacks: Ordered callback collection owned by this workflow.
        :raises TypeError: If create_state does not return a concrete SkillState.
        :raises ValueError: If a callback already belongs to a different state.
        """
        state = self.create_state()
        if not isinstance(state, SkillState):
            raise TypeError("create_state must return a concrete SkillState")
        self.state: StateT = state
        self.callbacks = callbacks.bind(self.state)

    @abstractmethod
    def create_state(self) -> StateT:
        """Construct the concrete state used by this executor.

        :returns: One fresh state instance for the entire callback collection.
        :raises NotImplementedError: If a concrete executor calls this abstract body.
        """
        raise NotImplementedError("concrete executors must create their shared state")

    def _invoke(self, hook: Callable[[], SkillResult]) -> SkillResult:
        """Invoke one lifecycle hook and preserve the mapping result contract.

        :param hook: Bound callback lifecycle method.
        :returns: The hook's result mapping.
        :raises TypeError: If the hook returns a non-mapping value.
        """
        result = hook()
        if not isinstance(result, Mapping):
            raise TypeError("callback hooks must return a result mapping")
        self.state.result = result
        self.state.records = (*self.state.records, result)
        return result

    def _execute(self, entered: AbstractCallbacks[StateT, SkillAction]) -> None:
        """Run each callback's collection, action, and post-run update.

        :param entered: Callbacks that need cleanup, including failed collection.
        """
        for callback in self.callbacks:
            entered.append(callback)
            self._invoke(callback.collect)
            self.state.results[callback] = self._invoke(callback.run)
            self._invoke(callback.update)
            if not callback.complete:
                self.state.failed = True
                break

    def _cleanup(
        self, entered: AbstractCallbacks[StateT, SkillAction]
    ) -> list[BaseException]:
        """Attempt every entered callback's cleanup in reverse order.

        :param entered: Callbacks entered during this execution.
        :returns: Cleanup failures in the order they occurred.
        """
        failures: list[BaseException] = []
        for callback in reversed(entered):
            try:
                self._invoke(callback.cleanup)
            except BaseException as exc:  # noqa: BLE001 - finish cleanup before raising
                self.state.failed = True
                failures.append(exc)
        return failures

    def run(self) -> SkillResult:
        """Run the bound callbacks and always clean up entered callbacks.

        Each successful hook updates the current result and appends its mapping
        to the state's record sequence, including cleanup hooks.

        :returns: The last successful hook's state-owned result mapping.
        :raises BaseException: The original operation or first cleanup failure.
        """  # noqa: DOC503 - preserve the original exception
        entered: AbstractCallbacks[StateT, SkillAction] = []
        self.state.failed = False
        with recoverable_signals():
            try:
                self._execute(entered)
            except BaseException as primary:
                self.state.failed = True
                for failure in self._cleanup(entered):
                    primary.add_note(f"cleanup failed: {type(failure).__name__}")
                raise
            failures = self._cleanup(entered)
            if failures:
                self.state.failed = True
                for failure in failures[1:]:
                    failures[0].add_note(f"cleanup failed: {type(failure).__name__}")
                raise failures[0]
        return self.state.result


class Executor(SkillExecutor[ExecutionContext]):
    """Use the standard command context for callbacks that need no custom state."""

    def create_state(self) -> ExecutionContext:
        """Create the context before binding the callback collection.

        :returns: A fresh command context with an empty result dictionary.
        """
        return ExecutionContext()


class WorkflowExecutor(SkillExecutor[ExecutionContext]):
    """Bind a command's explicit context through the shared executor lifecycle."""

    def __init__(self, *, context: ExecutionContext, callbacks: Callbacks[ExecutionContext]) -> None:
        """Retain the command context before the base binds every callback.

        :param context: Concrete state owned by this workflow invocation.
        :param callbacks: Ordered callbacks that share this context.
        """
        self._context = context
        super().__init__(callbacks)

    def create_state(self) -> ExecutionContext:
        """Supply the concrete context selected by the workflow.

        :returns: The invocation's one shared context.
        """
        return self._context
