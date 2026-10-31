"""Failure recovery and token cleanup for the CI13 image-build chain.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import MutableSequence
from dataclasses import replace

from lib.python.core.abstract.callback import AbstractCallback, LocalCallback
from lib.python.core.gitlab.gitlab_runners import DeleteRunner, delete_snapshot
from lib.python.core.gitlab.runner_manager import (
    DeleteManagerRunner,
    workload_action,
)
from lib.python.core.gitlab.runner_manager.workload import ObserveManagerNamespace
from lib.python.core.json_types import JSONObject, is_json_value
from lib.python.core.kubernetes.run_scope import RemoveScope, ResolveRunScopeAction
from lib.python.core.kubernetes.scc_access import RemoveSCCAccess
from lib.python.core.runtime import sanitize
from lib.python.sys.status import BLOCKED, PASS

from .model import ImageBuildChainAction, ImageBuildChainState


class CleanupChain(LocalCallback[ImageBuildChainState, ImageBuildChainAction]):
    """Recover failed delivery resources and remove the ephemeral token sink."""

    def cleanup(self) -> JSONObject:
        """Rollback live resources only after failure and always remove the token.

        :returns: Per-resource cleanup evidence after all attempts.
        :raises RuntimeError: One or more owned cleanup operations fail.
        """
        state = self.state
        records: MutableSequence[JSONObject] = []
        failures: MutableSequence[JSONObject] = []
        if state.failed:
            manager = getattr(state, "manager_action", None)
            if manager is not None and hasattr(state, "run_scope"):
                cleanup_callbacks: tuple[AbstractCallback, ...] = (
                    DeleteManagerRunner(workload_action(manager)),
                    RemoveSCCAccess(manager.scc_access()),
                    RemoveScope(ResolveRunScopeAction(state.run_scope.run_id)),
                    ObserveManagerNamespace(workload_action(manager)),
                )
                for callback in cleanup_callbacks:
                    _attempt_cleanup_component(state, callback, records, failures)
            if hasattr(state, "created_runner_id") and hasattr(
                state, "runner_create_plan"
            ):
                _delete_runner_record(state, records, failures)
        _remove_token_sink(state, records, failures)
        result: JSONObject = {
            "status": PASS if not failures else BLOCKED,
            "resources": list(records),
            "failures": list(failures),
        }
        state.chain_cleanup = result
        if failures:
            reasons = ",".join(str(item["reason"]) for item in failures)
            raise RuntimeError("chain_cleanup_failed:" + reasons)
        return result


def _attempt_cleanup_component(
    state: ImageBuildChainState,
    callback: AbstractCallback,
    records: MutableSequence[JSONObject],
    failures: MutableSequence[JSONObject],
) -> None:
    """Run one existing cleanup callback and preserve later cleanup attempts.

    :param state: Shared chain state with authenticated provider services.
    :param callback: Existing manager or run-scope deletion callback.
    :param records: Successful cleanup evidence destination.
    :param failures: Sanitized failure evidence destination.
    :returns: None after recording success or failure.
    """
    try:
        callback.bind(state)
        result = callback.run()
        if is_json_value(result):
            records.append({"callback": type(callback).__name__, **dict(result)})
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        failures.append(
            {"callback": type(callback).__name__, "reason": sanitize(str(exc), 240)}
        )


def _delete_runner_record(
    state: ImageBuildChainState,
    records: MutableSequence[JSONObject],
    failures: MutableSequence[JSONObject],
) -> None:
    """Delete the exact chain-created GitLab runner through existing logic.

    :param state: Shared state with runner service, plan, and created ID.
    :param records: Successful cleanup evidence destination.
    :param failures: Sanitized failure evidence destination.
    :returns: None after verified deletion or recorded failure.
    """
    previous = state.gitlab
    previous_plan = state.plan
    state.gitlab = state.runner_gitlab
    state.configured_session = state.runner_gitlab.session
    try:
        request = replace(
            state.runner_create_plan,
            operation="delete",
            body={"description": state.runner_create_plan.body["description"]},
            resource_id=state.created_runner_id,
            token_out=None,
            runner_snapshot=None,
        )
        snapshot = delete_snapshot(
            state.runner_gitlab.api,
            state.runner_gitlab.session,
            request,
            state.runner_gitlab.target_id,
        )
        state.plan = replace(request, runner_snapshot=snapshot)
        callback = DeleteRunner(action=state.plan)
        _attempt_cleanup_component(state, callback, records, failures)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        failures.append({"callback": "DeleteRunner", "reason": sanitize(str(exc), 240)})
    finally:
        state.gitlab = previous
        state.configured_session = previous.session
        state.plan = previous_plan


def _remove_token_sink(
    state: ImageBuildChainState,
    records: MutableSequence[JSONObject],
    failures: MutableSequence[JSONObject],
) -> None:
    """Remove only the exact private token file and directory created here.

    :param state: Shared state that may retain the token sink identity.
    :param records: Successful cleanup evidence destination.
    :param failures: Sanitized failure evidence destination.
    :returns: None after verified absence or recorded failure.
    """
    path = getattr(state, "token_path", None)
    directory = getattr(state, "token_directory", None)
    if path is None or directory is None:
        return
    try:
        path.unlink(missing_ok=True)
        directory.rmdir()
        records.append(
            {
                "callback": "RemoveRunnerToken",
                "resource": "runner_token",
                "verified": not path.exists(),
            }
        )
    except OSError as exc:
        failures.append(
            {"callback": "RemoveRunnerToken", "reason": sanitize(str(exc), 240)}
        )
