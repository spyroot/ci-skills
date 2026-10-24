"""Own shared state, bind callbacks, and execute their ordered lifecycle.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, MutableSequence, Sequence
from typing import Generic, Literal

from lib.python.core.abstract.callback import AbstractCallback
from lib.python.core.abstract.callbacks import AbstractCallbacks, Callbacks
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.abstract.skill_result import SkillResult, require_result
from lib.python.core.abstract.skill_state import SkillState, StateT
from lib.python.core.abstract.spec import Spec, load_spec, require_spec
from lib.python.core.runtime import execution_trace, sanitize, trace_event
from lib.python.sys.signals import recoverable_signals

LifecycleHook = Literal["collect", "run", "update", "cleanup"]


class SkillExecutor(ABC, Generic[StateT]):
    """Create one concrete state and bind it through the callback collection."""

    def __init__(self, callbacks: Callbacks[StateT], spec: Spec | None = None):
        """Create and bind shared state before any lifecycle hook runs.

        :param callbacks: Ordered callback collection owned by this workflow.
        :param spec: Caller declaration; otherwise retain shared state or load the installed file.
        :raises TypeError: If create_state does not return a concrete SkillState,
            the specification has invalid shape, or a configuration hook is not a mapping.
        :raises ValueError: If a callback belongs to another state or its required
            plugin configuration is unavailable, or an explicit spec changes shared state.
        :raises OSError: The required installed specification cannot be read.
        """  # noqa: DOC503 - callback binding can reject existing ownership.
        state = self.create_state()
        if not isinstance(state, SkillState):
            raise TypeError("create_state must return a concrete SkillState")
        self.state: StateT = state
        if spec is not None:
            declared = require_spec(spec)
            if state.spec is not None and state.spec != declared:
                raise ValueError("shared invocation specification cannot change")
            if state.spec is None:
                state.spec = declared
        elif state.spec is None:
            state.spec = load_spec()
        self.callbacks = callbacks.bind(self.state)

    @abstractmethod
    def create_state(self) -> StateT:
        """Construct the concrete state used by this executor.

        :returns: One fresh state instance for the entire callback collection.
        :raises NotImplementedError: If a concrete executor calls this abstract body.
        """
        raise NotImplementedError("concrete executors must create their shared state")

    def _invoke(
        self, callback: AbstractCallback[StateT, SkillAction], hook: LifecycleHook
    ) -> SkillResult:
        """Call one hook and retain its result in the shared execution history.

        :param callback: Bound callback whose lifecycle method will run.
        :param hook: Name of the lifecycle method to invoke and trace.
        :returns: The hook's exact result mapping, including an empty mapping.
        :raises TypeError: The hook returns a value that is not a mapping.
        :raises BaseException: The callback hook fails; its exception is propagated.
        """  # noqa: DOC503 - require_result validates the shared mapping contract.
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
            result = require_result(operations[hook]())
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

    def _whoami(self, callback: AbstractCallback[StateT, SkillAction]) -> None:
        """Require the provider's identity hook before any operational lifecycle hook.

        :param callback: Bound callback that will consume its provider runtime state.
        :raises TypeError: The identity hook does not return a boolean.
        :raises ValueError: The identity hook does not confirm its prerequisites.
        :raises BaseException: The provider rejects its identity, role, or binding.
        """  # noqa: DOC503 - provider hooks propagate their concrete access failures.
        detail = {"callback": type(callback).__name__, "hook": "whoami"}
        started = time.monotonic()
        trace_event("callback.started", {**detail, "status": "STARTED"})
        try:
            verified = callback.whoami()
            if not isinstance(verified, bool):
                raise TypeError("whoami must return a boolean")
            if not verified:
                raise ValueError(f"{type(callback).__name__}_identity_preflight_failed")
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

    def _execute(self, entered: AbstractCallbacks[StateT, SkillAction]) -> None:
        """Check identity, then run collect, run, and update in declared order.

        :param entered: Mutable callback list extended before each collect call so
            failed collection still receives cleanup.
        :raises TypeError: An identity or lifecycle hook returns an incompatible value.
        :raises BaseException: A callback hook or completion check fails.
        """  # noqa: DOC502 - _invoke and completion hooks propagate callback failures.
        for callback in self.callbacks:
            self._whoami(callback)
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
                result = self._invoke(callback, "cleanup")
                if result:
                    self.state.cleanup_results.append(
                        {
                            **result,
                            "callback": type(callback).__name__,
                            "hook": "cleanup",
                        }
                    )
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
        self.state.cleanup_results.clear()
        with recoverable_signals(), execution_trace(self.state.trace):
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
