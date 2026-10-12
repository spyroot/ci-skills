"""GitHub registration callbacks for the shared linear runner workflow.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, Optional

from lib.python.core.catalog import github_variables
from lib.python.core.cli_api import APIError
from lib.python.core.gitlab.github import GitHubCallback
from lib.python.core.gitlab.gitlab_runner import (
    REGISTRATION_SECRET_SUFFIX,
    RUNTIME_ROOT,
    RunnerAction,
    RunnerRuntime,
)
from lib.python.core.json_types import JSONObject
from lib.python.core.paths import SKILL_ROOT
from lib.python.sys.status import APPLIED, NO_OP

GITHUB_INIT_SCRIPT: Final = SKILL_ROOT / "lib/bash/runner/github-init.bash"
REGISTRATION_KEY: Final = "github-token"
GITHUB_READINESS_COMMAND: Final = (
    "/bin/sh",
    "-c",
    "grep -q 'Listening for Jobs' _diag/Runner_*.log",
)


def runner_labels(record: JSONObject) -> Sequence[str]:
    """Read concrete label names from a validated provider runner record.

    :param record: Runner object from independent GitHub read-back.
    :returns: Sorted label names without mutating the provider record.
    :raises APIError: If labels are not objects carrying nonempty text names.
    """
    labels = record.get("labels", [])
    if not isinstance(labels, list):
        raise APIError("runner_labels_invalid")
    names = []
    for label in labels:
        name = label.get("name") if isinstance(label, Mapping) else None
        if not isinstance(name, str) or not name:
            raise APIError("runner_labels_invalid")
        names.append(name)
    return sorted(names)


class GitHubRunnerRuntime(GitHubCallback[RunnerAction]):
    """Prepare private registration state before the shared workload deploys."""

    def __init__(self, action: RunnerAction):
        """Retain required registration inputs and initialize recovery tracking.

        :param action: Runtime workload name, labels, source path, and deadline.
        """
        super().__init__(action)
        self._previous_runner: Optional[JSONObject] = None
        self._snapshot_collected = False

    def collect(self) -> JSONObject:
        """Read the exact registration before this workflow can replace it.

        :returns: Empty evidence; the prior registration remains internal.
        :raises APIError: If the native registration read cannot be completed.
        :raises AttributeError: If GitHub authentication did not populate the workflow state.
        :raises OSError: If the native provider process cannot start or its output cannot be read.
        """  # noqa: DOC502 - Runner lookup and state access propagate their errors.
        self._previous_runner = self.github.runner(self.action.name)
        self._snapshot_collected = True
        return {}

    def cleanup(self) -> JSONObject:
        """Remove a new registration or independently prove prior state recovered.

        :returns: Empty evidence after required recovery verification.
        :raises APIError: If registration cleanup or prior identity recovery fails.
        """
        if not self.state.failed or not self._snapshot_collected:
            return {}
        previous = self._previous_runner
        if previous is None:
            self.github.delete_runner(self.action.name)
            return {}
        try:
            recovered = self.github.wait_online(self.action.name, self.action.timeout)
        except APIError as exc:
            raise APIError("runner_recovery_incomplete") from exc
        expected = {label.casefold() for label in runner_labels(previous)}
        observed = {label.casefold() for label in runner_labels(recovered)}
        if (
            not isinstance(previous.get("id"), int)
            or recovered.get("id") != previous["id"]
            or observed != expected
        ):
            raise APIError("runner_recovery_incomplete")
        return {}

    def run(self) -> JSONObject:
        """Retain registration commands and credentials for the workload producer.

        :returns: Empty report evidence; all private runtime inputs remain in state.
        :raises OSError: If the installed native bootstrap cannot be read.
        :raises TargetError: If the verified service did not retain its credential.
        :raises AttributeError: If GitHub authentication did not populate the workflow state.
        :raises UnicodeError: If the installed bootstrap is not valid UTF-8.
        """  # noqa: DOC502 - Bootstrap reads and credential access propagate failures.
        action, service = self.action, self.github
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
        secret = {
            "name": github_variables(service.host)[0],
            "valueFrom": {
                "secretKeyRef": {
                    "name": action.name + REGISTRATION_SECRET_SUFFIX,
                    "key": REGISTRATION_KEY,
                }
            },
        }
        self.state.runner_runtime = RunnerRuntime(
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
        return {}


class CreateGitHubRunner(GitHubCallback[RunnerAction]):
    """Verify registration after the preceding workload callback deploys it."""

    def run(self) -> JSONObject:
        """Read online status and labels against the preceding deployment evidence.

        :returns: Safe GitHub registration and independently verified workload fields.
        :raises AttributeError: If composition omitted the deployment producer.
        :raises APIError: If online status or requested labels cannot be verified.
        :raises KeyError: If the online runner response omits its required identifier.
        :raises OSError: If the native provider process cannot start or its output cannot be read.
        """  # noqa: DOC503 - State access and provider reads propagate failures.
        action = self.action
        runner = self.github.wait_online(action.name, action.timeout)
        labels = runner_labels(runner)
        if not {label.casefold() for label in action.labels}.issubset(
            label.casefold() for label in labels
        ):
            raise APIError("runner_labels_mismatch")
        return {
            **self.state.deployment_result,
            "repository": self.github.repository,
            "repository_id": self.github.repository_id,
            "runner_id": runner["id"],
            "status": runner["status"],
            "busy": runner.get("busy", False),
            "labels": list(labels),
        }


class DeleteGitHubRunner(GitHubCallback[RunnerAction]):
    """Delete native registration after the shared workload has stopped."""

    def run(self) -> JSONObject:
        """Remove the exact selected registration and independently prove absence.

        :returns: Verified applied/no-op registration deletion evidence.
        :raises APIError: If deletion or independent absence read-back fails.
        :raises AttributeError: If GitHub authentication did not populate the workflow state.
        :raises KeyError: If the discovered runner response omits its required identifier.
        :raises OSError: If the native provider process cannot start or its output cannot be read.
        """  # noqa: DOC502 - Provider deletion and absence checks propagate failures.
        changed = self.github.delete_runner(self.action.name)
        return {
            "action": APPLIED if changed else NO_OP,
            "verified": True,
            "name": self.action.name,
            "repository": self.github.repository,
        }
