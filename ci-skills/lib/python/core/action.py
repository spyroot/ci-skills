"""Compose provider operations through one ordered callback lifecycle.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from argparse import Namespace
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, cast

from .gitlab_api import GlabAPIClient
from .gitlab_session import BoundGitLabSession, GitLabService
from .runtime import sanitize
from .signals import recoverable_signals

Action = TypeVar("Action")
Result = TypeVar("Result")


@dataclass
class ExecutionContext:
    """Arguments and authenticated services shared by one workflow.

    Services are established by authentication callbacks using the existing
    target and credential resolvers, then consumed by operation callbacks.

    .. attribute :: args
        Workflow arguments.

    .. attribute :: services
        Bound provider services, excluded from representation.

    .. attribute :: report
        Existing output report supplied by the workflow.

    """

    args: Namespace = field(default_factory=Namespace)
    services: dict[str, object] = field(default_factory=dict, repr=False)
    report: dict[str, Any] | None = None

    @classmethod
    def from_args(cls, args: Namespace) -> ExecutionContext:
        """Keep this workflow's parsed inputs without contacting a provider.

        :param args: Arguments belonging to the calling workflow.
        :returns: A fresh context with no authenticated services.
        """
        return cls(args=args)


class AbstractCallback(ABC, Generic[Result]):
    """One operation with explicit inputs, result, and lifecycle hooks.

    .. attribute :: context
        Shared workflow inputs and provider services.

    .. attribute :: result
        Value available to later dependent callbacks.

    """

    context: ExecutionContext
    result: Result | None = None

    @property
    def complete(self) -> bool:
        """Whether dependent callbacks may start.

        :returns: True unless an operation declares an incomplete result.
        """
        return True

    def bind(self, context: ExecutionContext) -> None:
        """Bind the workflow context before entering this operation.

        :param context: Shared arguments and provider services.
        """
        self.context = context
        self.result = None

    @abstractmethod
    def collect(self) -> None:
        """Collect the inputs required by this operation."""

    @abstractmethod
    def run(self) -> Result | None:
        """Perform the operation and return its result.

        :returns: The operation's typed value, when applicable.
        """

    @abstractmethod
    def update(self) -> None:
        """Perform the operation's declared post-run work."""

    @abstractmethod
    def cleanup(self) -> None:
        """Release temporary resources owned by this operation."""


class Callback(AbstractCallback[Result], Generic[Action, Result]):
    """Shared defaults let concrete operations implement only needed hooks."""

    def __init__(self, *, action: Action | None = None) -> None:
        """Keep the typed action selected by the workflow.

        :param action: Operation inputs or an existing confirmed action plan.
        """
        self.action: Action | None = action

    def collect(self) -> None:
        """Leave input collection to callbacks that need it."""

    def run(self) -> Result | None:
        """Leave execution to callbacks that need it.

        :returns: No result unless the callback implements this hook.
        """
        return None

    def update(self) -> None:
        """Leave post-run work to callbacks that need it."""

    def cleanup(self) -> None:
        """Leave resource cleanup to callbacks that own resources."""


class GitlabCallback(Callback[Action, Result]):
    """Base for operations using the workflow's bound GitLab service."""

    @property
    def gitlab(self) -> GitLabService:
        """Return the service established by GitLabAuth.

        :returns: Bound GitLab session, client, and verified access state.
        """
        return cast(GitLabService, self.context.services["gitlab"])

    @property
    def complete(self) -> bool:
        """Stop a combination when an operation's read-back is incomplete.

        :returns: Whether the provider operation reports complete read-back.
        """
        return not isinstance(self.result, dict) or (
            self.result.get("verified", True) is True
            and not self.result.get("errors")
            and self.result.get("cleanup", {}).get("status")
            not in {"BLOCKED", "PARTIAL"}
        )

    def execute(
        self, api: GlabAPIClient, session: BoundGitLabSession, target_id: int
    ) -> Result | None:
        """Adapt existing callers to the same callback executor.

        :param api: Existing GitLab API client.
        :param session: Existing authenticated session.
        :param target_id: Verified project or group identifier.
        :returns: The operation result.
        """
        context = ExecutionContext(
            services={"gitlab": GitLabService(api, session, target_id)}
        )
        Executor(context=context, callbacks=self).run()
        return self.result


class K8SCallback(Callback[Action, Result]):
    """Base for operations using the selected Kubernetes access state."""

    @property
    def kubernetes(self) -> object:
        """Return the service established by K8SAuth.

        :returns: Bound Kubernetes target and access state.
        """
        return self.context.services["kubernetes"]


class HarborCallback(Callback[Action, Result]):
    """Base for operations using the selected Harbor access state."""

    @property
    def harbor(self) -> object:
        """Return the service established by HarborAuth.

        :returns: Bound Harbor endpoint and credential service.
        """
        return self.context.services["harbor"]


# The executor accepts heterogeneous result types; each callback keeps its type.
AbstractCallbacks = list[AbstractCallback[Any]]


def listify(
    callbacks: AbstractCallback[Any] | Sequence[AbstractCallback[Any]],
) -> AbstractCallbacks:
    """Normalize one callback or a sequence without flattening operations.

    :param callbacks: Operation or ordered operations supplied by the workflow.
    :returns: A separate ordered list of callback instances.
    :raises TypeError: If an item does not implement the callback interface.
    """
    result = [callbacks] if isinstance(callbacks, AbstractCallback) else list(callbacks)
    if any(not isinstance(callback, AbstractCallback) for callback in result):
        raise TypeError("callbacks must implement AbstractCallback")
    return result


@dataclass(frozen=True)
class CleanupFailure:
    """Cleanup evidence retained independently of the primary failure.

    .. attribute :: callback
        Callback class that failed cleanup.

    .. attribute :: reason
        Sanitized failure detail.

    """

    callback: str
    reason: str


@dataclass(frozen=True)
class ExecutionResult:
    """The last typed value and the workflow's existing report, when present.

    .. attribute :: value
        Last entered callback result.

    .. attribute :: report
        Workflow output report.

    """

    value: object
    report: dict[str, Any] | None


class Executor:
    """Run each callback to completion, then clean up in reverse entry order."""

    def __init__(
        self,
        *,
        callbacks: AbstractCallback[Any] | Sequence[AbstractCallback[Any]],
        context: ExecutionContext | None = None,
    ) -> None:
        """Construct a workflow without invoking any callback.

        :param callbacks: Operation or ordered operations to execute.
        :param context: Shared inputs and services, or a fresh empty context.
        """
        self.callbacks = listify(callbacks)
        self.context = context if context is not None else ExecutionContext()
        self.cleanup_failures: list[CleanupFailure] = []

    def run(self) -> ExecutionResult:
        """Execute hooks in order and retain the last operation result.

        Dependencies consume the earlier callback's ``result`` directly.
        A failed hook stops subsequent callbacks. All entered callbacks get
        cleanup, including one whose collect hook fails. A primary exception
        is re-raised with cleanup evidence; cleanup alone cannot hide failure.
        On the main thread, signal handlers are owned only for this run and
        restored afterward; worker threads retain the caller's handlers.

        :returns: Last callback value and the report selected by the workflow.
        :raises BaseException: The primary hook or cleanup failure.
        """  # noqa: DOC503 - re-raise the original exception instance

        entered: AbstractCallbacks = []
        primary: BaseException | None = None
        output: object = None
        self.cleanup_failures = []
        with recoverable_signals():
            try:
                for callback in self.callbacks:
                    callback.bind(self.context)
                    entered.append(callback)
                    callback.collect()
                    callback.result = callback.run()
                    callback.update()
                    output = callback.result
                    if not callback.complete:
                        break
            except BaseException as exc:  # noqa: BLE001 - re-raised after all cleanup
                primary = exc
            finally:
                for callback in reversed(entered):
                    try:
                        callback.cleanup()
                    except BaseException as exc:  # noqa: BLE001 - finish cleanup, then re-raise
                        failure = CleanupFailure(
                            type(callback).__name__, sanitize(str(exc), 240)
                        )
                        self.cleanup_failures.append(failure)
                        if primary is None:
                            primary = exc
                        else:
                            primary.add_note(
                                f"cleanup {failure.callback}: {failure.reason}"
                            )
            if primary is not None:
                primary.cleanup_failures = (
                    *getattr(primary, "cleanup_failures", ()),
                    *self.cleanup_failures,
                )
                raise primary
            return ExecutionResult(output, self.context.report)


def emit_result(result: ExecutionResult, args: Namespace) -> int:
    """Emit the workflow report through the existing output facilities.

    :param result: Executor outcome with an operation report.
    :param args: Calling workflow's output, logging, and receipt arguments.
    :returns: Canonical report exit code.
    :raises ValueError: If the workflow did not produce a report.
    """
    import json
    import sys

    from .cli import log_event, output_mode
    from .portable import write_portable_receipt
    from .report import emit
    from .status import exit_code

    data = result.report
    if data is None:
        raise ValueError("workflow_report_missing")
    if getattr(args, "describe", False):
        sys.stdout.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        return 0
    if getattr(args, "receipt_out", None):
        write_portable_receipt(data, args.receipt_out)
    rendered = emit(data, output_mode(args), getattr(args, "output_dir", None))
    log_event(args, data["kind"], "result", data["status"])
    sys.stdout.write(rendered)
    return exit_code(data["status"])
