"""Compose GitHub registration inputs with the shared runner workload atoms.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from typing import Any, Final, cast

from .action import AbstractCallback, GitHubCallback
from .catalog import github_variables
from .cli_api import APIError
from .github import GitHubService
from .paths import SKILL_ROOT
from .runner import (
    REGISTRATION_SECRET_SUFFIX,
    RUNTIME_ROOT,
    RunnerAction,
    RunnerRuntime,
)
from .status import APPLIED, NO_OP

GITHUB_INIT_SCRIPT: Final = SKILL_ROOT / "lib/bash/runner/github-init.bash"
REGISTRATION_KEY: Final = "github-token"
GITHUB_READINESS_COMMAND: Final = (
    "/bin/sh",
    "-c",
    "grep -q 'Listening for Jobs' _diag/Runner_*.log",
)


class GitHubRunnerRuntime(GitHubCallback[RunnerAction, RunnerRuntime]):
    """Prepare native registration inputs for the shared deployment callback."""

    def collect(self) -> None:
        """Snapshot registration identity before this workflow can replace it.

        :raises ValueError: If runner inputs are missing.
        :raises APIError: If native registration read-back fails.
        """
        if self.action is None:
            raise ValueError("runner_action_missing")
        self._previous_runner_collected = False
        self._previous_runner = cast(GitHubService, self.github).runner(
            self.action.name
        )
        self._previous_runner_collected = True

    def cleanup(self) -> None:
        """Remove a new registration or verify the prior runner recovered.

        :raises ValueError: If runner inputs are unavailable during recovery.
        :raises APIError: If exact registration cleanup or recovery is incomplete.
        """
        if not self.context.failed:
            return
        action = self.action
        if action is None:
            raise ValueError("runner_action_missing")
        service = cast(GitHubService, self.github)
        if not getattr(self, "_previous_runner_collected", False):
            raise APIError("runner_recovery_incomplete")
        previous = getattr(self, "_previous_runner", None)
        if previous is None:
            service.delete_runner(action.name)
            return
        try:
            recovered = service.wait_online(action.name, action.timeout)
        except APIError as exc:
            raise APIError("runner_recovery_incomplete") from exc
        expected_labels = {
            str(label.get("name", "")).casefold()
            for label in previous.get("labels", [])
            if isinstance(label, dict)
        }
        observed_labels = {
            str(label.get("name", "")).casefold()
            for label in recovered.get("labels", [])
            if isinstance(label, dict)
        }
        if (
            not isinstance(previous.get("id"), int)
            or recovered.get("id") != previous["id"]
            or observed_labels != expected_labels
        ):
            raise APIError("runner_recovery_incomplete")

    def run(self) -> RunnerRuntime:
        """Bind registration to the authenticated repository and selected name.

        :returns: Runtime inputs with the token isolated to the init container.
        :raises ValueError: If caller-selected runner inputs are missing.
        :raises OSError: If the installed native bootstrap is unavailable.
        """
        action = self.action
        if action is None:
            raise ValueError("runner_action_missing")
        service = cast(GitHubService, self.github)
        environment = {
            "RUNNER_SOURCE_ROOT": action.source_root,
            "RUNNER_RUNTIME_ROOT": RUNTIME_ROOT,
            "RUNNER_HOST": service.host,
            "RUNNER_REPOSITORY": service.repository,
            "RUNNER_NAME": action.name,
            "RUNNER_LABELS": ",".join(action.labels),
        }
        values = tuple(
            {"name": key, "value": value} for key, value in environment.items()
        )
        variable = github_variables(service.host)[0]
        secret = {
            "name": variable,
            "valueFrom": {
                "secretKeyRef": {
                    "name": action.name + REGISTRATION_SECRET_SUFFIX,
                    "key": REGISTRATION_KEY,
                }
            },
        }
        return RunnerRuntime(
            init_command=(
                "/bin/bash",
                "-c",
                GITHUB_INIT_SCRIPT.read_text(encoding="utf-8"),
            ),
            command=("/bin/bash", "./run.sh"),
            environment=(*values, secret),
            readiness_command=GITHUB_READINESS_COMMAND,
            credential={REGISTRATION_KEY: service.token},
        )


class CreateGitHubRunner(GitHubCallback[RunnerAction, dict[str, Any]]):
    """Verify native registration after the shared runner workload is deployed."""

    def __init__(
        self, *, action: RunnerAction, deployment: AbstractCallback[Any]
    ) -> None:
        """Retain the exact workload that performs native GitHub registration.

        :param action: Selected runner name, labels, and readiness deadline.
        :param deployment: Shared workload callback whose result must be verified.
        """
        super().__init__(action=action)
        self.deployment = deployment

    def run(self) -> dict[str, Any]:
        """Read back online status, labels, and the actual deployed image.

        :returns: GitHub registration and workload evidence without credentials.
        :raises ValueError: If the workload result or operation inputs are missing.
        :raises APIError: If online status or requested labels are not verified.
        """
        action = self.action
        if action is None or not isinstance(self.deployment.result, dict):
            raise ValueError("runner_deployment_missing")
        service = cast(GitHubService, self.github)
        runner = service.wait_online(action.name, action.timeout)
        labels = sorted(label["name"] for label in runner.get("labels", []))
        if not {label.casefold() for label in action.labels}.issubset(
            label.casefold() for label in labels
        ):
            raise APIError("runner_labels_mismatch")
        return {
            **self.deployment.result,
            "repository": service.repository,
            "repository_id": service.repository_id,
            "runner_id": runner["id"],
            "status": runner["status"],
            "busy": runner.get("busy", False),
            "labels": labels,
        }


class DeleteGitHubRunner(GitHubCallback[RunnerAction, dict[str, Any]]):
    """Delete native registration after the shared workload has stopped."""

    def run(self) -> dict[str, Any]:
        """Remove an exact selected registration and prove its absence.

        :returns: Verified applied/no-op registration deletion result.
        :raises ValueError: If caller-selected runner inputs are missing.
        :raises APIError: If deletion or read-back fails.
        """
        if self.action is None:
            raise ValueError("runner_action_missing")
        service = cast(GitHubService, self.github)
        changed = service.delete_runner(self.action.name)
        return {
            "action": APPLIED if changed else NO_OP,
            "verified": True,
            "name": self.action.name,
            "repository": service.repository,
        }
