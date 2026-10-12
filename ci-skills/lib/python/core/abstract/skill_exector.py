"""Own shared state, bind callbacks, and execute their ordered lifecycle.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, MutableSequence, Sequence
from typing import Generic, Literal

from lib.python.core.abstract.callback import AbstractCallback
from lib.python.core.abstract.callbacks import AbstractCallbacks, Callbacks
from lib.python.core.abstract.context import ExecutionContext
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.abstract.skill_result import SkillResult
from lib.python.core.abstract.skill_state import SkillState, StateT
from lib.python.core.runtime import execution_trace, sanitize, trace_event
from lib.python.sys.signals import recoverable_signals

LifecycleHook = Literal["collect", "run", "update", "cleanup"]


class SkillExecutor(ABC, Generic[StateT]):
    """Create one concrete state and bind it through the callback collection."""

    def __init__(self, callbacks: Callbacks[StateT]):
        """Create and bind shared state before any lifecycle hook runs.

        :param callbacks: Ordered callback collection owned by this workflow.
        :raises TypeError: If create_state does not return a concrete SkillState.
        :raises ValueError: If a callback already belongs to a different state.
        """  # noqa: DOC503 - callback binding can reject existing ownership.
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

    def _trace(self, event: str, details: Mapping[str, object]) -> None:
        """Write CLI workflow diagnostics through the existing shared logger.

        :param event: Lifecycle or transport event selected by its producer.
        :param details: Bounded event metadata, redacted by the shared logger.
        """
        from lib.python.core.cli import log_event

        if not isinstance(self.state, ExecutionContext):
            return
        args = self.state.args
        try:
            log_event(
                args,
                getattr(args, "kind", "workflow"),
                event,
                str(details.get("status", "OBSERVED")),
                level="debug",
                details=details,
            )
        except OSError:
            # Diagnostic failures must not interrupt owned-resource cleanup.
            sys.stderr.write("workflow trace_output_unavailable\n")

    def _invoke(
        self, callback: AbstractCallback[StateT, SkillAction], hook: LifecycleHook
    ) -> SkillResult:
        """Call one hook and retain its result in the shared execution history.

        :param callback: Bound callback whose lifecycle method will run.
        :param hook: Name of the lifecycle method to invoke and trace.
        :returns: The hook's exact result mapping, including an empty mapping.
        :raises TypeError: The hook returns a value that is not a mapping.
        :raises BaseException: The callback hook fails; its exception is propagated.
        """
        operations: Mapping[LifecycleHook, Callable[[], SkillResult]] = {
            "collect": callback.collect,
            "run": callback.run,
            "update": callback.update,
            "cleanup": callback.cleanup,
        }
        detail = {"callback": type(callback).__name__, "hook": hook}
        started = time.monotonic()
        trace_event("callback.started", {**detail, "status": "STARTED"})
        try:
            result = operations[hook]()
            if not isinstance(result, Mapping):
                raise TypeError("callback hooks must return a result mapping")
        except BaseException as exc:
            trace_event(
                "callback.failed",
                {**detail, "status": "BLOCKED", "reason": sanitize(str(exc))},
            )
            raise
        trace_event(
            "callback.completed",
            {
                **detail,
                "status": "PASS",
                "elapsed_time_ms": round((time.monotonic() - started) * 1000),
            },
        )
        self.state.result = result
        self.state.records.append(result)
        return result

    def _execute(self, entered: AbstractCallbacks[StateT, SkillAction]) -> None:
        """Run each callback's collect, run, and update hooks in declared order.

        :param entered: Mutable callback list extended before each collect call so
            failed collection still receives cleanup.
        :raises TypeError: A lifecycle hook returns a non-mapping value.
        :raises BaseException: A callback hook or completion check fails.
        """  # noqa: DOC502 - _invoke and completion hooks propagate callback failures.
        for callback in self.callbacks:
            entered.append(callback)
            self._invoke(callback, "collect")
            self.state.results[callback] = self._invoke(callback, "run")
            self._invoke(callback, "update")
            if not callback.complete:
                self.state.failed = True
                break

    def _cleanup(
        self, entered: AbstractCallbacks[StateT, SkillAction]
    ) -> Sequence[BaseException]:
        """Attempt cleanup for all entered callbacks in reverse order.

        :param entered: Callbacks whose execution began, including a failed collect.
        :returns: Cleanup exceptions in occurrence order. Their callback names and
            sanitized reasons are also retained in state.cleanup_failures.
        """
        failures: MutableSequence[BaseException] = []
        for callback in reversed(entered):
            try:
                self._invoke(callback, "cleanup")
            except BaseException as exc:  # noqa: BLE001 - finish cleanup before raising
                self.state.failed = True
                self.state.cleanup_failures.append(
                    {"callback": type(callback).__name__, "reason": sanitize(str(exc))}
                )
                failures.append(exc)
        return failures

    def run(self) -> SkillResult:
        """Run the declared callback sequence and clean up every entered callback.

        Every successful hook becomes the current state result and appends its mapping
        to state.records. Each run result is also retained under its owning callback.
        An incomplete callback stops the remaining sequence and marks the state failed.

        :returns: Last successful hook's result mapping after cleanup.
        :raises BaseException: Execution fails, or cleanup fails after successful
            execution. The primary exception is preserved; later cleanup failures
            are recorded in state.cleanup_failures and attached as exception notes.
        """  # noqa: DOC503 - preserve the original exception
        entered: AbstractCallbacks[StateT, SkillAction] = []
        self.state.failed = False
        self.state.cleanup_failures.clear()
        tracing = (
            isinstance(self.state, ExecutionContext)
            and getattr(self.state.args, "log_level", "info") == "debug"
        )
        with recoverable_signals(), execution_trace(self._trace if tracing else None):
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


class WorkflowExecutor(SkillExecutor[StateT]):
    """Bind a command's explicit context through the shared executor lifecycle."""

    def __init__(self, context: StateT, callbacks: Callbacks[StateT]):
        """Bind the command's existing state to its declared callback sequence.

        :param context: Concrete state owned by this workflow invocation.
        :param callbacks: Ordered callbacks sharing this exact state instance.
        :raises TypeError: The supplied context does not implement SkillState.
        :raises ValueError: A callback already belongs to another state.
        """  # noqa: DOC502 - SkillExecutor validates state and callback ownership.
        self._context = context
        super().__init__(callbacks)

    def create_state(self) -> StateT:
        """Supply the concrete context selected by the workflow.

        :returns: The invocation's one shared context.
        """
        return self._context
