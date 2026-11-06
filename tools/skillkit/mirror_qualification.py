"""One bounded qualification of an installed mirror command for candidate receipts.

`docs/2026-10-31/live-smoke-inventory.md` (Reusable pairs, `test-mirror-push`) allows
one bounded qualification when candidate evidence requires renewal. This module runs
it against an installed copy of the skill. The installed command subprocess and the
in-process provider both run in the caller's working directory, so both resolve the
same target file; receipts and outputs go to absolute paths under the work directory.

1. live plan; the fixture must hold the input source ID, no destination repository
   and no mirror;
2. an in-process live plan of the same installed copy; its ``target_source`` must
   equal the subprocess plan's, the source identity must match, and every seed ref
   must point at its input commit;
3. a wrong confirmation digest, which must block without mutation; ``plan()`` stops
   here and writes no ledger;
4. the confirmed apply, written as the APPLIED receipt; the ledger is created
   exclusively before it and records the created native IDs after it;
5. after a settle interval, a second live plan and confirmed apply, written as the
   NO_OP receipt;
6. owned cleanup: the run-created GitLab mirror, then the run-created GitHub
   repository, each matched by native ID and removed once;
7. independent absence and seed-ref read-back, a settle interval, and a repeat
   read-back that deletes nothing; the result is the `gitlab_mirror_smoke_cleanup`
   record that `tools/check_live_acceptance.py` verifies on the NO_OP receipt.

The allocation ledger in the work directory records each step. A run stops when the
ledger already exists. ``reconcile()`` finishes the cleanup a failed or interrupted
run left pending, for the native IDs that ledger recorded only. Provider calls sit
behind `MirrorProvider`; `NativeMirrorProvider` binds the installed skill's own
GitLab and GitHub services. Status tokens, record actions and exit codes compared
with the installed command's output are bound from the installed copy
(`InstalledStatus`), not copied here.

The cleanup record has ``kind``, ``status``, ``captured_at``, ``execution_host``,
``skill_digest``, ``tested_revision``, ``before`` (``source``, ``destination``,
``mirror_id``), ``delete`` (``mirror_http_status``, ``destination_http_status``),
and ``after`` and ``repeat`` (``source``, ``mirror_absent``,
``destination_http_status``, ``destination_id_http_status``; ``repeat`` adds
``mutated: false``). ``run()`` writes it to ``mirror-smoke-cleanup.json`` and as
``smoke_cleanup`` of ``mirror-noop-with-cleanup-receipt.json``. ``reconcile()``
writes it only to ``mirror-smoke-cleanup.json``, with ``reconciled: true`` added;
there an object the run did not create has ``null`` ID, statuses and
``mirror_absent``, and an owned object already absent has delete status 404. That
record is never merged into a NO_OP receipt: the receipt schema's
``smoke_cleanup`` has no ``reconciled`` field and integer-only statuses, and
`tools/check_live_acceptance.py` requires two 204 deletes.

SIGINT, and SIGTERM inside ``terminate_as_interrupt()``, stop a mode without
starting a deletion. With a ledger held, the ledger is written again with the last
state reached and the stopping reason is ``interrupted:<state>:next:--reconcile``;
without one it is ``interrupted:no_ledger``. The ledger and the cleanup files are
written to a temporary file in the same directory and moved with ``os.replace``;
the ledger is created with ``os.link``, which never replaces an existing ledger.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import abc
import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPMethod, HTTPStatus
from importlib import import_module
from pathlib import Path
from types import FrameType, ModuleType
from typing import Any, Final, Optional, TypeVar
from urllib.parse import quote

ALLOCATION_FILE: Final = "mirror-live-allocation.json"
APPLIED_RECEIPT: Final = "mirror-applied-receipt.json"
NOOP_RECEIPT: Final = "mirror-noop-receipt.json"
NOOP_WITH_CLEANUP: Final = "mirror-noop-with-cleanup-receipt.json"
CLEANUP_RECORD: Final = "mirror-smoke-cleanup.json"
# Defined once here; tools/check_live_acceptance.py imports it.
CLEANUP_KIND: Final = "gitlab_mirror_smoke_cleanup"
INSTALLATION_FILE: Final = "INSTALLATION.json"
MIRROR_SCRIPT: Final = Path("bin") / "gitlab_mirror.py"
WRONG_DIGEST: Final = "0" * 64
DEFAULT_TIMEOUT_SECONDS: Final = 300
DEFAULT_SETTLE_SECONDS: Final = 30
COMMAND_TIMEOUT_MARGIN_SECONDS: Final = 60
OUTPUT_SUFFIX: Final = ".json"
STDERR_SUFFIX: Final = ".stderr"
STAGED_SUFFIX: Final = ".partial"
WRITE_MODE: Final = "w"
LIVE_PLAN_OPTION: Final = "--live-plan"
APPLY_OPTION: Final = "--apply"
CONFIRM_OPTION: Final = "--confirm-plan"
RECEIPT_OPTION: Final = "--receipt-out"
# Option of tools/qualify_mirror.py named as the next action after an interrupt.
RECONCILE_OPTION: Final = "--reconcile"
TARGET_SOURCE_FIELD: Final = "target_source"
LEDGER_STATE_FIELD: Final = "cleanup"
SEED_REF_KINDS: Final = ("branches", "tags")

# Cleanup record fields; tools/check_live_acceptance.py imports them.
SMOKE_CLEANUP_FIELD: Final = "smoke_cleanup"
KIND_FIELD: Final = "kind"
STATUS_FIELD: Final = "status"
CAPTURED_AT_FIELD: Final = "captured_at"
EXECUTION_HOST_FIELD: Final = "execution_host"
SKILL_DIGEST_FIELD: Final = "skill_digest"
TESTED_REVISION_FIELD: Final = "tested_revision"
BEFORE_FIELD: Final = "before"
DELETE_FIELD: Final = "delete"
AFTER_FIELD: Final = "after"
REPEAT_FIELD: Final = "repeat"
SOURCE_FIELD: Final = "source"
DESTINATION_FIELD: Final = "destination"
MIRROR_ID_FIELD: Final = "mirror_id"
MIRROR_HTTP_STATUS_FIELD: Final = "mirror_http_status"
DESTINATION_HTTP_STATUS_FIELD: Final = "destination_http_status"
MIRROR_ABSENT_FIELD: Final = "mirror_absent"
DESTINATION_ID_HTTP_STATUS_FIELD: Final = "destination_id_http_status"
MUTATED_FIELD: Final = "mutated"
RECONCILED_FIELD: Final = "reconciled"

LIVE_PLAN_STEP: Final = "mirror-live-plan"
BASELINE_PLAN_STEP: Final = "mirror-baseline-plan"
REJECTION_STEP: Final = "mirror-rejection"
APPLIED_STEP: Final = "mirror-applied"
NOOP_PLAN_STEP: Final = "mirror-noop-plan"
NOOP_STEP: Final = "mirror-noop"
CLEANUP_PLAN_STEP: Final = "mirror-cleanup-plan"
RECONCILE_PLAN_STEP: Final = "mirror-reconcile-plan"

APPLY_PENDING: Final = "APPLY_PENDING"
CLEANUP_PENDING: Final = "PENDING"
MIRROR_DELETE_PENDING: Final = "MIRROR_DELETE_PENDING"
DESTINATION_DELETE_PENDING: Final = "MIRROR_ABSENT_DESTINATION_DELETE_PENDING"
VERIFY_PENDING: Final = "DELETES_RETURNED_204_VERIFY_PENDING"
CLEANUP_PASS: Final = "PASS_INDEPENDENT_ABSENCE_AND_REPEAT"
PENDING_STATES: Final = frozenset(
    {
        APPLY_PENDING,
        CLEANUP_PENDING,
        MIRROR_DELETE_PENDING,
        DESTINATION_DELETE_PENDING,
        VERIFY_PENDING,
    }
)

ALLOCATION_EXISTS: Final = "allocation_exists_reconcile_first"
NOTHING_TO_RECONCILE: Final = "nothing_to_reconcile"
LEDGER_IDS_MISSING: Final = "ledger_ids_missing"
# The ledger has no created ID to delete by; nothing is looked up by path. The
# reason names what to read back by hand before the ledger is removed.
MANUAL_READ_BACK: Final = (
    "{reason}:manual_read_back:destination_repository={destination}"
    ":source_project_push_mirrors={source}"
)
EVIDENCE_LOCATION: Final = "{host}/{path}"
INTERRUPTED: Final = "interrupted:{state}:next:{option}"
INTERRUPTED_WITHOUT_LEDGER: Final = "interrupted:no_ledger"
SOURCE_IDENTITY_CHANGED: Final = "source_identity_changed"
DESTINATION_IDENTITY_CHANGED: Final = "destination_identity_changed"
MIRROR_IDENTITY_CHANGED: Final = "mirror_identity_changed"
SEED_REF_BASELINE_DIFFERS: Final = "seed_ref_baseline_differs"
SEED_REF_MOVED: Final = "seed_ref_moved"

SOURCE_READ: Final = "source_read"
REF_READ: Final = "ref_read"
MIRROR_READ: Final = "mirror_read"
MIRROR_DELETE: Final = "mirror_delete"
DESTINATION_READ: Final = "destination_read"
DESTINATION_DELETE: Final = "destination_delete"
PROVIDER_FAILURE: Final = "provider_error:{step}:{reason}"
# Attribute of the installed APIError holding its classified reason.
REASON_ATTRIBUTE: Final = "reason"
# Neither route has a constant in the installed lib.
REF_ENDPOINT: Final = "projects/{project}/repository/{kind}/{name}"
REPOSITORY_ID_ENDPOINT: Final = "repositories/{repository_id}"

CATALOG_MODULE: Final = "lib.python.core.catalog"
CLI_API_MODULE: Final = "lib.python.core.cli_api"
GITLAB_API_MODULE: Final = "lib.python.core.gitlab.gitlab_api"
MIRROR_CLI_MODULE: Final = "lib.python.core.gitlab.mirror.cli"
MIRROR_CONSTANTS_MODULE: Final = "lib.python.core.gitlab.mirror.constants"
MIRROR_PROVIDER_MODULE: Final = "lib.python.core.gitlab.mirror.provider"
REPOSITORY_MODULE: Final = "lib.python.core.github.repository"
STATUS_MODULE: Final = "lib.python.sys.status"

JSONMap = Mapping[str, object]
ResultT = TypeVar("ResultT")


class QualificationError(RuntimeError):
    """A precondition or read-back failed; the run stops at that step."""


@dataclass(frozen=True, slots=True)
class SeedRef:
    """A retained source ref and the commit it must keep.

    :param kind: ``branches`` or ``tags``.
    :param name: Ref name, such as ``main``.
    :param commit: Full commit SHA the ref must point at.

    .. attribute :: kind
        :type: str

        ``branches`` or ``tags``.

    .. attribute :: name
        :type: str

        Ref name, such as ``main``.

    .. attribute :: commit
        :type: str

        Full commit SHA the ref must point at.
    """

    kind: str
    name: str
    commit: str

    @classmethod
    def parse(cls, text: str) -> SeedRef:
        """Parse ``KIND:NAME:COMMIT``.

        :param text: Ref declaration from the caller.
        :returns: Validated seed ref.
        :raises QualificationError: The declaration is malformed.
        """
        parts = text.split(":")
        if len(parts) != 3 or parts[0] not in SEED_REF_KINDS or not all(parts):
            raise QualificationError(f"seed_ref_invalid:{text}")
        return cls(*parts)

    @property
    def key(self) -> str:
        """Name the ref as ``KIND/NAME`` in reasons and the ledger.

        :returns: ``KIND/NAME``.
        """
        return f"{self.kind}/{self.name}"


@dataclass(frozen=True, slots=True)
class QualificationInputs:
    """Fixture identity and run controls for one qualification.

    :param skill: Installed skill directory holding ``bin/gitlab_mirror.py``.
    :param work_dir: Directory receiving receipts, outputs and the ledger.
    :param project: Retained GitLab source project path.
    :param destination: Disposable GitHub destination repository path.
    :param source_id: Native ID the source project must have.
    :param seed_refs: Source refs the run must leave unchanged.
    :param run_id: Diagnostic run identifier passed to the command.
    :param gitlab_option: ``GITLAB_TARGET_OPTION`` of the installed catalog.
    :param gitlab: Selector passed with ``gitlab_option``, or ``None`` for the
        routing default.
    :param timeout: Per-command timeout in seconds.
    :param settle_seconds: Pause before the repeat apply and the repeat read-back.

    .. attribute :: skill
        :type: Path

        Installed skill directory.

    .. attribute :: work_dir
        :type: Path

        Directory receiving receipts, outputs and the ledger.

    .. attribute :: project
        :type: str

        Retained GitLab source project path.

    .. attribute :: destination
        :type: str

        Disposable GitHub destination repository path.

    .. attribute :: source_id
        :type: int

        Native ID the source project must have.

    .. attribute :: seed_refs
        :type: tuple[SeedRef, ...]

        Source refs the run must leave unchanged.

    .. attribute :: run_id
        :type: str

        Diagnostic run identifier.

    .. attribute :: gitlab_option
        :type: str

        ``GITLAB_TARGET_OPTION`` of the installed catalog.

    .. attribute :: gitlab
        :type: Optional[str]

        Selector passed with ``gitlab_option``, or ``None`` for the routing default.

    .. attribute :: timeout
        :type: int

        Per-command timeout in seconds.

    .. attribute :: settle_seconds
        :type: int

        Pause before the repeat apply and the repeat read-back.
    """

    skill: Path
    work_dir: Path
    project: str
    destination: str
    source_id: int
    seed_refs: tuple[SeedRef, ...]
    run_id: str
    gitlab_option: str
    gitlab: Optional[str] = None
    timeout: int = DEFAULT_TIMEOUT_SECONDS
    settle_seconds: int = DEFAULT_SETTLE_SECONDS

    def command_selection(self) -> tuple[str, ...]:
        """Build the fixed ``gitlab_mirror.py sync`` arguments of this run.

        :returns: Operation, fixture, timeout, output and selector arguments.
        """
        selector = (self.gitlab_option, self.gitlab) if self.gitlab else ()
        return (
            "sync",
            "--project",
            self.project,
            "--destination",
            self.destination,
            "--create-if-absent",
            "--timeout",
            str(self.timeout),
            "--run-id",
            self.run_id,
            "--json",
            *selector,
        )

    def candidate(self) -> str:
        """Read the verified source revision of the installed copy.

        :returns: Full commit SHA recorded in ``INSTALLATION.json``.
        :raises QualificationError: The identity is absent, unverified or empty.
        """
        path = self.skill / INSTALLATION_FILE
        try:
            revision = json.loads(path.read_text(encoding="utf-8"))["revision"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise QualificationError("installation_identity_unreadable") from exc
        value = revision.get("value") if isinstance(revision, dict) else None
        if (
            not isinstance(value, str)
            or not value
            or revision.get("verified") is not True
        ):
            raise QualificationError("installation_identity_unverified")
        return value


@dataclass(frozen=True, slots=True)
class InstalledStatus:
    """Tokens and exit mapping the installed mirror command reports with.

    :param passed: ``PASS`` from ``sys/status.py``.
    :param planned: ``PLANNED`` from ``sys/status.py``.
    :param blocked: ``BLOCKED`` from ``sys/status.py``.
    :param applied: ``Outcome.APPLIED`` from ``core/gitlab/mirror/constants.py``.
    :param no_op: ``Outcome.NO_OP`` from ``core/gitlab/mirror/constants.py``.
    :param exit_code: ``exit_code`` from ``sys/status.py``.

    .. attribute :: passed
        :type: str

        ``PASS`` from ``sys/status.py``.

    .. attribute :: planned
        :type: str

        ``PLANNED`` from ``sys/status.py``.

    .. attribute :: blocked
        :type: str

        ``BLOCKED`` from ``sys/status.py``.

    .. attribute :: applied
        :type: str

        Record action of a write, ``Outcome.APPLIED``.

    .. attribute :: no_op
        :type: str

        Record action of a repeat without a write, ``Outcome.NO_OP``.

    .. attribute :: exit_code
        :type: Callable[[str], int]

        Process exit code of a status token.
    """

    passed: str
    planned: str
    blocked: str
    applied: str
    no_op: str
    exit_code: Callable[[str], int]

    @classmethod
    def bind(cls, status: Any, outcome: Any) -> InstalledStatus:
        """Read the tokens from the installed status module and mirror outcomes.

        The record ``action`` is written from ``Outcome`` by the installed mirror
        command, so the actions come from there and the statuses from
        ``sys/status.py``.

        :param status: Installed ``lib.python.sys.status`` module.
        :param outcome: Installed ``Outcome`` enumeration.
        :returns: Bound tokens.
        """
        return cls(
            passed=status.PASS,
            planned=status.PLANNED,
            blocked=status.BLOCKED,
            applied=outcome.APPLIED,
            no_op=outcome.NO_OP,
            exit_code=status.exit_code,
        )


@dataclass(frozen=True, slots=True)
class InstalledBindings:
    """Names bound at run time from the installed copy's ``lib`` package.

    :param gitlab_option: ``GITLAB_TARGET_OPTION`` from ``core/catalog.py``.
    :param api_error: ``APIError`` from ``core/cli_api.py``; ``GitLabAPIError`` in
        ``core/gitlab/gitlab_api.py`` is the same class.
    :param create_guard: ``create_guard`` from ``core/gitlab/gitlab_api.py``.
    :param mirror_parser: ``build_parser`` from ``core/gitlab/mirror/cli.py``.
    :param mirror_command: ``MirrorCommand`` from ``core/gitlab/mirror/cli.py``.
    :param mirror_endpoint: ``MIRROR_ENDPOINT`` from ``core/gitlab/mirror/provider.py``.
    :param mirror_not_found: ``MIRROR_NOT_FOUND`` from the same module.
    :param project_scope: ``PROJECT_SCOPE`` from the same module.
    :param mirror_resource: ``MIRROR_RESOURCE`` from the same module.
    :param repository_endpoint: ``REPOSITORY_ENDPOINT`` from
        ``core/github/repository.py``.
    :param repository_not_found: ``NOT_FOUND`` from the same module.
    :param path_separator: ``PATH_SEPARATOR`` from the same module.
    :param status: Tokens from ``sys/status.py`` and mirror ``Outcome``.

    .. attribute :: gitlab_option
        :type: str

        ``GITLAB_TARGET_OPTION`` from ``core/catalog.py``.

    .. attribute :: api_error
        :type: type[Exception]

        ``APIError`` from ``core/cli_api.py``.

    .. attribute :: create_guard
        :type: Callable[..., contextlib.AbstractContextManager[Any]]

        ``create_guard`` from ``core/gitlab/gitlab_api.py``.

    .. attribute :: mirror_parser
        :type: Callable[[], argparse.ArgumentParser]

        ``build_parser`` from ``core/gitlab/mirror/cli.py``.

    .. attribute :: mirror_command
        :type: Callable[[argparse.Namespace], Any]

        ``MirrorCommand`` from ``core/gitlab/mirror/cli.py``.

    .. attribute :: mirror_endpoint
        :type: str

        ``MIRROR_ENDPOINT`` from ``core/gitlab/mirror/provider.py``.

    .. attribute :: mirror_not_found
        :type: str

        ``MIRROR_NOT_FOUND`` from ``core/gitlab/mirror/provider.py``.

    .. attribute :: project_scope
        :type: str

        ``PROJECT_SCOPE`` from ``core/gitlab/mirror/provider.py``.

    .. attribute :: mirror_resource
        :type: str

        ``MIRROR_RESOURCE`` from ``core/gitlab/mirror/provider.py``.

    .. attribute :: repository_endpoint
        :type: str

        ``REPOSITORY_ENDPOINT`` from ``core/github/repository.py``.

    .. attribute :: repository_not_found
        :type: str

        ``NOT_FOUND`` from ``core/github/repository.py``.

    .. attribute :: path_separator
        :type: str

        ``PATH_SEPARATOR`` from ``core/github/repository.py``.

    .. attribute :: status
        :type: InstalledStatus

        Tokens from ``sys/status.py`` and mirror ``Outcome``.
    """

    gitlab_option: str
    api_error: type[Exception]
    create_guard: Callable[..., contextlib.AbstractContextManager[Any]]
    mirror_parser: Callable[[], argparse.ArgumentParser]
    mirror_command: Callable[[argparse.Namespace], Any]
    mirror_endpoint: str
    mirror_not_found: str
    project_scope: str
    mirror_resource: str
    repository_endpoint: str
    repository_not_found: str
    path_separator: str
    status: InstalledStatus


def installed_bindings(skill: Path) -> InstalledBindings:
    """Import the installed copy's ``lib`` modules and bind the names this tool uses.

    ``skill`` goes first on ``sys.path``. Every module must load from ``skill``: a
    ``lib`` package imported earlier from another directory stops the run.

    :param skill: Installed skill directory.
    :returns: Bound names.
    :raises QualificationError: A module cannot be imported, loads from another
        directory, or lacks a bound name.
    """
    root = str(skill)
    if root not in sys.path:
        sys.path.insert(0, root)
    catalog = _installed_module(skill, CATALOG_MODULE)
    cli_api = _installed_module(skill, CLI_API_MODULE)
    gitlab_api = _installed_module(skill, GITLAB_API_MODULE)
    mirror_cli = _installed_module(skill, MIRROR_CLI_MODULE)
    mirror_constants = _installed_module(skill, MIRROR_CONSTANTS_MODULE)
    mirror_provider = _installed_module(skill, MIRROR_PROVIDER_MODULE)
    repository = _installed_module(skill, REPOSITORY_MODULE)
    status = _installed_module(skill, STATUS_MODULE)
    try:
        return InstalledBindings(
            gitlab_option=catalog.GITLAB_TARGET_OPTION,
            api_error=cli_api.APIError,
            create_guard=gitlab_api.create_guard,
            mirror_parser=mirror_cli.build_parser,
            mirror_command=mirror_cli.MirrorCommand,
            mirror_endpoint=mirror_provider.MIRROR_ENDPOINT,
            mirror_not_found=mirror_provider.MIRROR_NOT_FOUND,
            project_scope=mirror_provider.PROJECT_SCOPE,
            mirror_resource=mirror_provider.MIRROR_RESOURCE,
            repository_endpoint=repository.REPOSITORY_ENDPOINT,
            repository_not_found=repository.NOT_FOUND,
            path_separator=repository.PATH_SEPARATOR,
            status=InstalledStatus.bind(status, mirror_constants.Outcome),
        )
    except AttributeError as exc:
        raise QualificationError(f"installed_lib_incompatible:{exc.name}") from exc


@contextlib.contextmanager
def terminate_as_interrupt() -> Iterator[None]:
    """Raise ``KeyboardInterrupt`` on SIGTERM inside the block, as SIGINT does.

    The previous SIGTERM handler is restored on exit; a handler not installed
    from Python is restored as ``SIG_DFL``.

    :yields: Control for the guarded block.
    :ytype: None
    """
    previous = signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        yield
    finally:
        signal.signal(
            signal.SIGTERM, previous if previous is not None else signal.SIG_DFL
        )


def _raise_interrupt(signum: int, frame: Optional[FrameType]) -> None:
    """Raise ``KeyboardInterrupt`` from a signal handler.

    :param signum: Received signal number.
    :param frame: Interrupted frame.
    :raises KeyboardInterrupt: Always.
    """
    raise KeyboardInterrupt


class CommandRunner(abc.ABC):
    """Run the installed mirror command and return its exit code and JSON."""

    @abc.abstractmethod
    def run(self, label: str, options: Sequence[str]) -> tuple[int, JSONMap]:
        """Run one command mode.

        :param label: Output file stem for this step.
        :param options: Mode options appended to the fixed selection.
        :returns: Exit code and decoded JSON result.
        """


class InstalledMirrorCommand(CommandRunner):
    """Run ``bin/gitlab_mirror.py`` of the installed copy in a subprocess.

    The subprocess inherits the caller's working directory.
    """

    def __init__(
        self,
        inputs: QualificationInputs,
        execute: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    ):
        """Bind the run inputs and the process runner.

        :param inputs: Fixture identity and run controls.
        :param execute: ``subprocess.run`` or a replacement with its signature.
        """
        self.inputs = inputs
        self.execute = execute

    def run(self, label: str, options: Sequence[str]) -> tuple[int, JSONMap]:
        """Run one mode and keep its stdout and stderr under the work directory.

        :param label: Output file stem for this step.
        :param options: Mode options appended to the fixed selection.
        :returns: Exit code and decoded JSON result.
        :raises QualificationError: The process cannot start or times out, or its
            output is not one JSON object.
        """
        inputs = self.inputs
        command = [
            sys.executable,
            str(inputs.skill / MIRROR_SCRIPT),
            *inputs.command_selection(),
            *options,
        ]
        output = inputs.work_dir / f"{label}{OUTPUT_SUFFIX}"
        try:
            with (
                output.open(WRITE_MODE, encoding="utf-8") as stdout,
                (inputs.work_dir / f"{label}{STDERR_SUFFIX}").open(
                    WRITE_MODE, encoding="utf-8"
                ) as stderr,
            ):
                result = self.execute(
                    command,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=inputs.timeout + COMMAND_TIMEOUT_MARGIN_SECONDS,
                    check=False,
                )
            data = json.loads(output.read_text(encoding="utf-8"))
        except subprocess.TimeoutExpired as exc:
            raise QualificationError(f"{label}_timeout") from exc
        except OSError as exc:
            raise QualificationError(
                f"{label}_command_failed:{type(exc).__name__}"
            ) from exc
        except ValueError as exc:
            raise QualificationError(f"{label}_output_not_json") from exc
        if not isinstance(data, dict):
            raise QualificationError(f"{label}_output_not_object")
        return result.returncode, data


class MirrorProvider(abc.ABC):
    """Native reads and owned deletes used by the qualification, by native ID."""

    @abc.abstractmethod
    def target_source(self) -> str:
        """Report which target file the provider resolved.

        :returns: ``target_source`` of the provider's live plan.
        """

    @abc.abstractmethod
    def source(self) -> JSONMap:
        """Read the source project evidence.

        :returns: ``id``, ``host`` and ``path`` of the source project.
        """

    @abc.abstractmethod
    def ref_commit(self, ref: SeedRef) -> str:
        """Read the commit a source ref points at.

        :param ref: Retained source ref.
        :returns: Commit SHA.
        """

    @abc.abstractmethod
    def mirror_targets(self, mirror_id: int, destination: str) -> Optional[bool]:
        """Read whether a mirror exists and pushes to the destination.

        :param mirror_id: Native mirror ID.
        :param destination: Destination repository path.
        :returns: ``None`` when the mirror is absent, otherwise whether it
            targets the destination's clone URL.
        """

    @abc.abstractmethod
    def delete_mirror(self, mirror_id: int, destination: str) -> int:
        """Delete one mirror by native ID.

        :param mirror_id: Native mirror ID.
        :param destination: Destination repository path the mirror must target.
        :returns: HTTP status of the deletion.
        """

    @abc.abstractmethod
    def destination(self, path: str) -> Optional[JSONMap]:
        """Read destination repository evidence by path.

        :param path: Destination repository path.
        :returns: ``id``, ``host`` and ``path``, or ``None`` when absent.
        """

    @abc.abstractmethod
    def destination_id_present(self, repository_id: int) -> bool:
        """Read whether a repository with a native ID exists.

        :param repository_id: Native GitHub repository ID.
        :returns: Whether the provider returns the repository.
        """

    @abc.abstractmethod
    def delete_destination(self, path: str, repository_id: int) -> int:
        """Delete the destination repository when it still has the expected ID.

        :param path: Destination repository path.
        :param repository_id: Native ID the repository must have.
        :returns: HTTP status of the deletion.
        """


class MirrorQualification:
    """Run the bounded qualification and write its receipts and cleanup record.

    The provider factory takes an output label. ``run()`` calls it once before any
    write for the baseline and once after both applies for cleanup; ``plan()``
    calls it for the baseline; ``reconcile()`` calls it once.
    """

    def __init__(
        self,
        inputs: QualificationInputs,
        runner: CommandRunner,
        provider: Callable[[str], MirrorProvider],
        status: InstalledStatus,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        """Bind the run inputs, command runner, provider factory and status tokens.

        :param inputs: Fixture identity and run controls.
        :param runner: Installed mirror command runner.
        :param provider: Provider factory; its argument is the output file stem.
        :param status: Tokens and exit mapping bound from the installed copy.
        :param sleep: Pause function.
        :param clock: Current UTC time.
        """
        self.inputs = inputs
        self.runner = runner
        self.provider = provider
        self.status = status
        self.sleep = sleep
        self.clock = clock
        self.ledger: MutableMapping[str, object] = {}

    def plan(self) -> JSONMap:
        """Run the read-only steps and the wrong-digest rejection; write no ledger.

        :returns: Run, candidate, target source, source, destination, plan digest
            and seed-ref commits.
        :raises QualificationError: A precondition or result differs, or the run
            was interrupted.
        """  # noqa: DOC502 - _preflight and _interruptible raise the failure.
        with self._interruptible():
            _, summary = self._preflight()
        return summary

    def run(self) -> JSONMap:
        """Execute every step once and return the final ledger.

        :returns: Allocation ledger ending in ``PASS_INDEPENDENT_ABSENCE_AND_REPEAT``.
        :raises QualificationError: A precondition, result or read-back failed, or
            the run was interrupted.
        """  # noqa: DOC502 - _run and _interruptible raise the failure.
        with self._interruptible():
            return self._run()

    def reconcile(self) -> JSONMap:
        """Finish the cleanup a failed or interrupted run left pending.

        Only the native IDs the ledger records as created by that run are removed;
        an object already absent is not deleted again. Absence is proven twice and
        the cleanup record is written with ``reconciled`` set.

        :returns: Allocation ledger ending in ``PASS_INDEPENDENT_ABSENCE_AND_REPEAT``.
        :raises QualificationError: No ledger is pending, it names another fixture
            or no created ID, an identity, deletion or read-back fails, or the
            reconcile was interrupted.
        """  # noqa: DOC502 - _reconcile and _interruptible raise the failure.
        with self._interruptible():
            return self._reconcile()

    @contextlib.contextmanager
    def _interruptible(self) -> Iterator[None]:
        """Turn ``KeyboardInterrupt`` into a stopping reason; start no deletion.

        With a ledger held, the ledger is written again so its state is the last
        step reached, and the reason names ``--reconcile`` as the next action.

        :yields: Control for one mode.
        :raises QualificationError: The block was interrupted.
        """
        try:
            yield
        except KeyboardInterrupt as exc:
            if not self.ledger:
                raise QualificationError(INTERRUPTED_WITHOUT_LEDGER) from exc
            self._save()
            raise QualificationError(
                INTERRUPTED.format(
                    state=self.ledger.get(LEDGER_STATE_FIELD), option=RECONCILE_OPTION
                )
            ) from exc

    def _run(self) -> JSONMap:
        """Execute every step once and return the final ledger.

        :returns: Allocation ledger ending in ``PASS_INDEPENDENT_ABSENCE_AND_REPEAT``.
        :raises QualificationError: A precondition, result or read-back failed.
        """
        inputs = self.inputs
        status = self.status
        plan, summary = self._preflight()
        self._allocate(**summary, mirror=None, created=None, cleanup=APPLY_PENDING)
        code, applied = self.runner.run(
            APPLIED_STEP, self._apply(plan, APPLIED_RECEIPT)
        )
        self._record_created(applied)
        self._require(APPLIED_STEP, code, applied, status.passed)
        created = _record(applied)
        if (
            created.get("action") != status.applied
            or created.get("created") != {"mirror": True, "repository": True}
            or created.get("source_refs") != created.get("destination_refs")
        ):
            raise QualificationError("applied_result_unexpected")
        self.sleep(inputs.settle_seconds)
        repeat_plan = self._expect(NOOP_PLAN_STEP, [LIVE_PLAN_OPTION], status.planned)
        noop = self._expect(
            NOOP_STEP, self._apply(repeat_plan, NOOP_RECEIPT), status.passed
        )
        repeated = _record(noop)
        if (
            repeated.get("action") != status.no_op
            or noop.get(MUTATED_FIELD) is not False
            or repeated.get(DESTINATION_FIELD) != created.get(DESTINATION_FIELD)
            or _object(repeated.get("mirror")).get("id")
            != _object(created.get("mirror")).get("id")
        ):
            raise QualificationError("noop_result_unexpected")
        receipt = _read(inputs.work_dir / NOOP_RECEIPT)
        if receipt.get(TESTED_REVISION_FIELD) != summary["candidate"]:
            raise QualificationError("receipt_candidate_mismatch")
        source = _object(created.get(SOURCE_FIELD))
        destination = _object(created.get(DESTINATION_FIELD))
        mirror_id = _object(created.get("mirror")).get("id")
        repository_id = destination.get("id")
        if type(mirror_id) is not int or type(repository_id) is not int:
            raise QualificationError("created_ids_missing")
        provider = self._bind(CLEANUP_PLAN_STEP, summary[TARGET_SOURCE_FIELD], source)
        if provider.destination(inputs.destination) != destination:
            raise QualificationError(DESTINATION_IDENTITY_CHANGED)
        if provider.mirror_targets(mirror_id, inputs.destination) is not True:
            raise QualificationError(MIRROR_IDENTITY_CHANGED)
        cleanup = self._cleanup(
            provider, source, destination, mirror_id, repository_id, receipt, False
        )
        _write(inputs.work_dir / CLEANUP_RECORD, cleanup)
        _write(
            inputs.work_dir / NOOP_WITH_CLEANUP,
            {**receipt, SMOKE_CLEANUP_FIELD: cleanup},
        )
        self._save(cleanup=CLEANUP_PASS)
        return dict(self.ledger)

    def _reconcile(self) -> JSONMap:
        """Finish the cleanup recorded by the ledger in the work directory.

        :returns: Allocation ledger ending in ``PASS_INDEPENDENT_ABSENCE_AND_REPEAT``.
        :raises QualificationError: No ledger is pending, it names another fixture
            or no created ID, or an identity, deletion or read-back fails.
        """
        inputs = self.inputs
        path = inputs.work_dir / ALLOCATION_FILE
        if not path.exists():
            raise QualificationError(NOTHING_TO_RECONCILE)
        self.ledger = dict(_read(path))
        state = self.ledger.get(LEDGER_STATE_FIELD)
        if state == CLEANUP_PASS:
            raise QualificationError(NOTHING_TO_RECONCILE)
        if state not in PENDING_STATES:
            raise QualificationError(f"ledger_state_unknown:{state}")
        source = _object(self.ledger.get(SOURCE_FIELD))
        destination = _object(self.ledger.get(DESTINATION_FIELD))
        if (
            source.get("id") != inputs.source_id
            or str(destination.get("path")).casefold() != inputs.destination.casefold()
        ):
            raise QualificationError("ledger_fixture_mismatch")
        missing = MANUAL_READ_BACK.format(
            reason=LEDGER_IDS_MISSING,
            destination=_location(destination),
            source=_location(source),
        )
        created = _object(self.ledger.get("created"))
        mirror_id = _owned_id(created, "mirror", self.ledger.get("mirror"), missing)
        repository_id = _owned_id(created, "repository", destination, missing)
        if mirror_id is None and repository_id is None:
            raise QualificationError(missing)
        provider = self._bind(
            RECONCILE_PLAN_STEP, self.ledger.get(TARGET_SOURCE_FIELD), source
        )
        self._seed_refs(provider, SEED_REF_BASELINE_DIFFERS)
        receipt = {
            **self._existing_receipt(),
            TESTED_REVISION_FIELD: self.ledger.get("candidate"),
        }
        cleanup = self._cleanup(
            provider, source, destination, mirror_id, repository_id, receipt, True
        )
        _write(inputs.work_dir / CLEANUP_RECORD, {**cleanup, RECONCILED_FIELD: True})
        self._save(cleanup=CLEANUP_PASS, reconciled=True)
        return dict(self.ledger)

    def _preflight(self) -> tuple[JSONMap, dict[str, object]]:
        """Run every step that precedes the first write.

        :returns: The live-plan result and the plan summary.
        :raises QualificationError: A ledger exists, or the fixture, the target
            source, a seed ref or the rejection differs from what the run requires.
        """
        inputs = self.inputs
        if (inputs.work_dir / ALLOCATION_FILE).exists():
            raise QualificationError(ALLOCATION_EXISTS)
        candidate = inputs.candidate()
        plan = self._expect(LIVE_PLAN_STEP, [LIVE_PLAN_OPTION], self.status.planned)
        record = _record(plan)
        source = _object(record.get(SOURCE_FIELD))
        if source.get("id") != inputs.source_id:
            raise QualificationError("source_id_mismatch")
        if _object(record.get(DESTINATION_FIELD)).get("id") is not None:
            raise QualificationError("destination_present")
        if record.get("mirror") is not None:
            raise QualificationError("mirror_present")
        target_source = plan.get(TARGET_SOURCE_FIELD)
        provider = self._bind(BASELINE_PLAN_STEP, target_source, source)
        seed_refs = self._seed_refs(provider, SEED_REF_BASELINE_DIFFERS)
        rejection = self._expect(
            REJECTION_STEP,
            [APPLY_OPTION, CONFIRM_OPTION, WRONG_DIGEST],
            self.status.blocked,
        )
        if rejection.get(MUTATED_FIELD) is not False:
            raise QualificationError("rejection_mutated")
        summary = {
            "run": inputs.run_id,
            "candidate": candidate,
            TARGET_SOURCE_FIELD: target_source,
            SOURCE_FIELD: source,
            DESTINATION_FIELD: record.get(DESTINATION_FIELD),
            "plan_digest": plan.get("plan_digest"),
            "seed_refs": seed_refs,
        }
        return plan, summary

    def _bind(
        self, label: str, target_source: object, source: JSONMap
    ) -> MirrorProvider:
        """Build a provider and require the same target file and source identity.

        :param label: Output file stem of the provider's live plan.
        :param target_source: ``target_source`` the provider must report.
        :param source: Source evidence the provider must read.
        :returns: Bound provider.
        :raises QualificationError: The target source or source identity differs.
        """
        provider = self.provider(label)
        if (
            not isinstance(target_source, str)
            or not target_source
            or provider.target_source() != target_source
        ):
            raise QualificationError("target_source_mismatch")
        if provider.source() != source:
            raise QualificationError(SOURCE_IDENTITY_CHANGED)
        return provider

    def _seed_refs(self, provider: MirrorProvider, reason: str) -> dict[str, str]:
        """Read every seed ref and require its input commit.

        :param provider: Native reads.
        :param reason: Stopping reason prefix when a ref differs.
        :returns: Commit per ``KIND/NAME``.
        :raises QualificationError: A ref points at another commit.
        """
        observed: dict[str, str] = {}
        for ref in self.inputs.seed_refs:
            commit = provider.ref_commit(ref)
            if commit != ref.commit:
                raise QualificationError(f"{reason}:{ref.key}")
            observed[ref.key] = commit
        return observed

    def _apply(self, plan: JSONMap, receipt: str) -> list[str]:
        """Build confirmed apply options for a plan.

        :param plan: Live-plan result carrying ``plan_digest``.
        :param receipt: Receipt file name in the work directory.
        :returns: Apply, confirmation and receipt options.
        :raises QualificationError: The plan carries no digest.
        """
        digest = plan.get("plan_digest")
        if not isinstance(digest, str) or not digest:
            raise QualificationError("plan_digest_missing")
        return [
            APPLY_OPTION,
            CONFIRM_OPTION,
            digest,
            RECEIPT_OPTION,
            str(self.inputs.work_dir / receipt),
        ]

    def _expect(self, label: str, options: Sequence[str], status: str) -> JSONMap:
        """Run one step and require its status and the exit code of that status.

        :param label: Output file stem.
        :param options: Mode options.
        :param status: Required result status, a bound installed token.
        :returns: Decoded result.
        """
        code, data = self.runner.run(label, options)
        self._require(label, code, data, status)
        return data

    def _require(self, label: str, code: int, data: JSONMap, status: str) -> None:
        """Require a step's status and the installed exit code of that status.

        :param label: Output file stem.
        :param code: Observed exit code.
        :param data: Decoded result.
        :param status: Required result status, a bound installed token.
        :raises QualificationError: Exit code or status differs.
        """
        observed = data.get(STATUS_FIELD)
        if code != self.status.exit_code(status) or observed != status:
            raise QualificationError(f"{label}_unexpected:{code}:{observed}")

    def _record_created(self, result: JSONMap) -> None:
        """Record the apply result's native IDs and ownership in the ledger.

        A result without one record carrying ``created`` leaves the ledger at
        ``APPLY_PENDING``.

        :param result: Apply result, whatever its status.
        """
        records = result.get("records")
        if not isinstance(records, list) or len(records) != 1:
            return
        record = _object(records[0])
        if isinstance(record.get("created"), dict):
            self._save(
                destination=record.get("destination"),
                mirror=record.get("mirror"),
                created=record.get("created"),
                cleanup=CLEANUP_PENDING,
            )

    def _existing_receipt(self) -> JSONMap:
        """Read the NO_OP receipt, else the APPLIED receipt, of the ledger's run.

        :returns: The receipt, or ``{}`` when neither was written.
        """
        for name in (NOOP_RECEIPT, APPLIED_RECEIPT):
            path = self.inputs.work_dir / name
            if path.exists():
                return _read(path)
        return {}

    def _cleanup(
        self,
        provider: MirrorProvider,
        source: JSONMap,
        destination: JSONMap,
        mirror_id: Optional[int],
        repository_id: Optional[int],
        receipt: JSONMap,
        allow_absent: bool,
    ) -> dict[str, object]:
        """Remove the run-created mirror and repository and prove their absence.

        An ID of ``None`` is not owned: it is neither deleted nor read back and its
        status is ``None``. With ``allow_absent``, an owned object already absent
        is not deleted and its status is 404; without it, an absent owned object
        stops the cleanup.

        :param provider: Native reads and owned deletes.
        :param source: Source evidence recorded by the run.
        :param destination: Destination evidence recorded by the run.
        :param mirror_id: Run-created mirror ID, or ``None``.
        :param repository_id: Run-created repository ID, or ``None``.
        :param receipt: Receipt whose identity binds the cleanup record.
        :param allow_absent: Accept an owned object that is already absent.
        :returns: ``gitlab_mirror_smoke_cleanup`` record.
        :raises QualificationError: An identity differs or a deletion is unproven.
        """
        path = self.inputs.destination
        mirror_status: Optional[int] = None
        if mirror_id is not None:
            mirror_status = HTTPStatus.NOT_FOUND
            targets = provider.mirror_targets(mirror_id, path)
            if targets is False or (targets is None and not allow_absent):
                raise QualificationError(MIRROR_IDENTITY_CHANGED)
            if targets is True:
                self._save(cleanup=MIRROR_DELETE_PENDING)
                mirror_status = provider.delete_mirror(mirror_id, path)
                if mirror_status != HTTPStatus.NO_CONTENT or (
                    provider.mirror_targets(mirror_id, path) is not None
                ):
                    raise QualificationError("mirror_deletion_unverified")
        self._save(cleanup=DESTINATION_DELETE_PENDING)
        destination_status: Optional[int] = None
        if repository_id is not None:
            destination_status = HTTPStatus.NOT_FOUND
            observed = provider.destination(path)
            if observed is None:
                if not allow_absent or provider.destination_id_present(repository_id):
                    raise QualificationError(DESTINATION_IDENTITY_CHANGED)
            elif observed != destination:
                raise QualificationError(DESTINATION_IDENTITY_CHANGED)
            else:
                destination_status = provider.delete_destination(path, repository_id)
                if destination_status != HTTPStatus.NO_CONTENT:
                    raise QualificationError("destination_deletion_unverified")
        self._save(cleanup=VERIFY_PENDING)
        after = self._observe(provider, source, mirror_id, repository_id)
        self.sleep(self.inputs.settle_seconds)
        repeat = {
            **self._observe(provider, source, mirror_id, repository_id),
            MUTATED_FIELD: False,
        }
        skill = _object(receipt.get("skill"))
        return {
            KIND_FIELD: CLEANUP_KIND,
            STATUS_FIELD: self.status.passed,
            CAPTURED_AT_FIELD: self.clock().isoformat(),
            EXECUTION_HOST_FIELD: receipt.get(EXECUTION_HOST_FIELD),
            SKILL_DIGEST_FIELD: skill.get("digest"),
            TESTED_REVISION_FIELD: receipt.get(TESTED_REVISION_FIELD),
            BEFORE_FIELD: {
                SOURCE_FIELD: source,
                DESTINATION_FIELD: destination,
                MIRROR_ID_FIELD: mirror_id,
            },
            DELETE_FIELD: {
                MIRROR_HTTP_STATUS_FIELD: _status(mirror_status),
                DESTINATION_HTTP_STATUS_FIELD: _status(destination_status),
            },
            AFTER_FIELD: after,
            REPEAT_FIELD: repeat,
        }

    def _observe(
        self,
        provider: MirrorProvider,
        source: JSONMap,
        mirror_id: Optional[int],
        repository_id: Optional[int],
    ) -> dict[str, object]:
        """Read the retained source and seed refs and the absence of owned objects.

        :param provider: Native reads.
        :param source: Source evidence recorded by the run.
        :param mirror_id: Removed mirror ID, or ``None`` when not owned.
        :param repository_id: Removed repository ID, or ``None`` when not owned.
        :returns: Read-back record with HTTP absence statuses; ``None`` for an
            object not owned.
        :raises QualificationError: The source changed, a seed ref moved, or a
            removed object is still present.
        """
        if provider.source() != source:
            raise QualificationError(SOURCE_IDENTITY_CHANGED)
        self._seed_refs(provider, SEED_REF_MOVED)
        path = self.inputs.destination
        if (
            mirror_id is not None
            and provider.mirror_targets(mirror_id, path) is not None
        ):
            raise QualificationError("mirror_not_absent")
        if repository_id is not None:
            if provider.destination(path) is not None:
                raise QualificationError("destination_not_absent")
            if provider.destination_id_present(repository_id):
                raise QualificationError("destination_id_not_absent")
        absent = int(HTTPStatus.NOT_FOUND) if repository_id is not None else None
        return {
            SOURCE_FIELD: source,
            MIRROR_ABSENT_FIELD: True if mirror_id is not None else None,
            DESTINATION_HTTP_STATUS_FIELD: absent,
            DESTINATION_ID_HTTP_STATUS_FIELD: absent,
        }

    def _allocate(self, **fields: object) -> None:
        """Create the allocation ledger; it must not exist yet.

        The complete ledger is staged in a temporary file and published with
        ``os.link``, which fails when a ledger exists. The ledger is held only
        after the link succeeds.

        :param **fields: Ledger fields to set.
        :raises QualificationError: The ledger exists or cannot be written.
        """
        path = self.inputs.work_dir / ALLOCATION_FILE
        try:
            with _staged(path, fields) as temporary:
                os.link(temporary, path)
        except FileExistsError as exc:
            raise QualificationError(ALLOCATION_EXISTS) from exc
        except OSError as exc:
            raise QualificationError(f"write_failed:{path.name}") from exc
        self.ledger.update(fields)

    def _save(self, **fields: object) -> None:
        """Update the allocation ledger and replace its file atomically.

        :param **fields: Ledger fields to set; none rewrites the current ledger.
        """
        self.ledger.update(fields)
        _write(self.inputs.work_dir / ALLOCATION_FILE, self.ledger)


class NativeMirrorProvider(MirrorProvider):
    """Use the installed skill's GitLab and GitHub services of one live plan.

    ``bind`` runs the in-process live plan; the constructor keeps its parts.
    Installed-lib ``APIError`` and ``OSError`` become ``QualificationError``.
    """

    def __init__(
        self,
        inputs: QualificationInputs,
        bindings: InstalledBindings,
        source: Any,
        destination: Any,
        target_source: str,
    ):
        """Keep the bound provider services.

        :param inputs: Fixture identity and run controls.
        :param bindings: Names bound from the installed ``lib`` package.
        :param source: Installed ``GitLabPushMirrorService`` of the live plan.
        :param destination: Installed ``GitHubRepositoryService`` of the live plan.
        :param target_source: ``target_source`` of the live plan.
        """
        self.inputs = inputs
        self._bindings = bindings
        self._source = source
        self._destination = destination
        self._target_source = target_source

    @classmethod
    def bind(
        cls, inputs: QualificationInputs, bindings: InstalledBindings, label: str
    ) -> NativeMirrorProvider:
        """Run one in-process live plan of the installed command and keep its services.

        :param inputs: Fixture identity and run controls.
        :param bindings: Names bound from the installed ``lib`` package.
        :param label: Output file stem of the live plan.
        :returns: Provider bound to the live plan's services.
        :raises QualificationError: The live plan does not succeed.
        """
        output = inputs.work_dir / f"{label}{OUTPUT_SUFFIX}"
        with _provider_errors(bindings.api_error, label):
            args = bindings.mirror_parser().parse_args(
                [*inputs.command_selection(), LIVE_PLAN_OPTION]
            )
            command = bindings.mirror_command(args)
            with (
                output.open(WRITE_MODE, encoding="utf-8") as stream,
                contextlib.redirect_stdout(stream),
            ):
                code = command.execute()
        engine = command.state.engine
        session = command.state.session
        status = bindings.status
        if (
            code != status.exit_code(status.planned)
            or engine is None
            or session is None
        ):
            raise QualificationError(f"{label}_failed")
        return cls(
            inputs, bindings, engine.source, engine.destination, session.target_source
        )

    def target_source(self) -> str:
        """Report which target file the live plan resolved.

        :returns: ``target_source`` of the live plan.
        """
        return self._target_source

    def source(self) -> JSONMap:
        """Read the source project evidence.

        :returns: ``id``, ``host`` and ``path`` of the source project.
        :raises QualificationError: The read fails or returns no project.
        """
        with self._errors(SOURCE_READ):
            repository = self._source.repository(self.inputs.project)
        if repository is None:
            raise QualificationError("source_missing")
        return repository.evidence()

    def ref_commit(self, ref: SeedRef) -> str:
        """Read the commit a source ref points at; the ref name is URL-encoded.

        :param ref: Retained source ref.
        :returns: Commit SHA.
        :raises QualificationError: The read fails or returns no commit.
        """
        service = self._source.service
        endpoint = REF_ENDPOINT.format(
            project=service.target_id, kind=ref.kind, name=quote(ref.name, safe="")
        )
        with self._errors(REF_READ):
            data = service.api.get_json(service.session, endpoint)
        commit = _object(_object(data).get("commit")).get("id")
        if not isinstance(commit, str) or not commit:
            raise QualificationError(f"seed_ref_unreadable:{ref.key}")
        return commit

    def mirror_targets(self, mirror_id: int, destination: str) -> Optional[bool]:
        """Read whether a mirror exists and pushes to the destination.

        URLs are compared with ``casefold()``, as the installed provider does.

        :param mirror_id: Native mirror ID.
        :param destination: Destination repository path.
        :returns: ``None`` when absent, otherwise whether it targets the destination.
        :raises QualificationError: A read fails with other than not-found.
        """  # noqa: DOC502 - _provider_errors converts the provider failure.
        with self._errors(MIRROR_READ):
            mirror = self._unless_not_found(
                self._bindings.mirror_not_found,
                lambda: self._source.mirror(mirror_id),
            )
            if mirror is None:
                return None
            repository = self._destination.repository(destination)
        return (
            repository is not None
            and mirror.url.casefold() == repository.clone_url.casefold()
        )

    def delete_mirror(self, mirror_id: int, destination: str) -> int:
        """Delete one mirror by native ID under the installed pending-write guard.

        :param mirror_id: Native mirror ID.
        :param destination: Destination repository path the mirror must target.
        :returns: HTTP status of the deletion.
        :raises QualificationError: The destination is absent, the mirror no longer
            targets it, or a provider call fails.
        """
        bindings = self._bindings
        service = self._source.service
        with self._errors(MIRROR_DELETE):
            repository = self._destination.repository(destination)
            if repository is None:
                raise QualificationError(DESTINATION_IDENTITY_CHANGED)
            with bindings.create_guard(
                service.session.host,
                bindings.project_scope,
                service.target_id,
                bindings.mirror_resource,
                repository.clone_url,
            ) as guard:
                guard.require_ready()
                if self.mirror_targets(mirror_id, destination) is not True:
                    raise QualificationError(MIRROR_IDENTITY_CHANGED)
                guard.mark_pending()
                service.api.request_no_content(
                    service.session,
                    HTTPMethod.DELETE,
                    bindings.mirror_endpoint.format(
                        project=service.target_id, mirror=mirror_id
                    ),
                )
                guard.clear()
        return int(HTTPStatus.NO_CONTENT)

    def destination(self, path: str) -> Optional[JSONMap]:
        """Read destination repository evidence by path.

        :param path: Destination repository path.
        :returns: ``id``, ``host`` and ``path``, or ``None`` when absent.
        :raises QualificationError: The read fails.
        """  # noqa: DOC502 - _provider_errors converts the provider failure.
        with self._errors(DESTINATION_READ):
            repository = self._destination.repository(path)
        return repository.evidence() if repository is not None else None

    def destination_id_present(self, repository_id: int) -> bool:
        """Read whether a repository with a native ID exists.

        :param repository_id: Native GitHub repository ID.
        :returns: Whether the provider returns the repository.
        :raises QualificationError: The read fails with other than not-found.
        """  # noqa: DOC502 - _provider_errors converts the provider failure.
        session = self._destination.session
        endpoint = REPOSITORY_ID_ENDPOINT.format(repository_id=repository_id)
        with self._errors(DESTINATION_READ):
            found = self._unless_not_found(
                self._bindings.repository_not_found,
                lambda: session.request(HTTPMethod.GET, endpoint),
            )
        return found is not None

    def delete_destination(self, path: str, repository_id: int) -> int:
        """Re-read the destination, require the expected ID, then delete it by path.

        The installed lib has no ID-based repository route, so the delete uses
        ``REPOSITORY_ENDPOINT`` right after the ID match.

        :param path: Destination repository path.
        :param repository_id: Native ID the repository must have.
        :returns: HTTP status of the deletion.
        :raises QualificationError: The repository is absent or has another ID, or
            a provider call fails.
        """
        bindings = self._bindings
        session = self._destination.session
        with self._errors(DESTINATION_DELETE):
            repository = self._destination.repository(path)
            if repository is None or repository.id != repository_id:
                raise QualificationError(DESTINATION_IDENTITY_CHANGED)
            session.api.request_no_content(
                session,
                HTTPMethod.DELETE,
                bindings.repository_endpoint.format(
                    reference=quote(path, safe=bindings.path_separator)
                ),
            )
        return int(HTTPStatus.NO_CONTENT)

    def _errors(self, step: str) -> contextlib.AbstractContextManager[None]:
        """Convert installed-lib provider and OS errors of one step.

        :param step: Provider step named in the stopping reason.
        :returns: Context manager raising ``QualificationError``.
        """
        return _provider_errors(self._bindings.api_error, step)

    def _unless_not_found(
        self, reason: str, read: Callable[[], ResultT]
    ) -> Optional[ResultT]:
        """Run one read and map the installed not-found reason to ``None``.

        :param reason: Installed not-found reason of the read.
        :param read: Provider read.
        :returns: The read result, or ``None`` when the provider reports not-found.
        """
        try:
            return read()
        except self._bindings.api_error as exc:
            if getattr(exc, REASON_ATTRIBUTE, None) != reason:
                raise
            return None


@contextlib.contextmanager
def _provider_errors(api_error: type[Exception], step: str) -> Iterator[None]:
    """Convert installed-lib ``APIError`` and ``OSError`` into a stopping reason.

    :param api_error: Installed ``APIError`` class.
    :param step: Provider step named in the reason.
    :yields: Control for the provider call.
    :raises QualificationError: The call raised ``api_error`` or ``OSError``.
    """
    try:
        yield
    except api_error as exc:
        raise QualificationError(
            PROVIDER_FAILURE.format(
                step=step, reason=getattr(exc, REASON_ATTRIBUTE, type(exc).__name__)
            )
        ) from exc
    except OSError as exc:
        raise QualificationError(
            PROVIDER_FAILURE.format(step=step, reason=type(exc).__name__)
        ) from exc


def _installed_module(skill: Path, name: str) -> ModuleType:
    """Import one installed module and require that it loads from ``skill``.

    :param skill: Installed skill directory.
    :param name: Dotted module name under ``lib``.
    :returns: The module.
    :raises QualificationError: The module cannot be imported or loads from
        another directory.
    """
    try:
        module = import_module(name)
    except ImportError as exc:
        raise QualificationError(f"installed_lib_unavailable:{name}") from exc
    location = Path(module.__file__ or "").resolve()
    if not location.is_relative_to(skill.resolve()):
        raise QualificationError(f"installed_lib_shadowed:{name}")
    return module


def _owned_id(
    created: JSONMap, key: str, evidence: object, missing: str
) -> Optional[int]:
    """Read a recorded native ID the run created.

    :param created: Ledger ``created`` flags.
    :param key: ``mirror`` or ``repository``.
    :param evidence: Ledger evidence holding the ID.
    :param missing: Stopping reason when the ID is not recorded.
    :returns: The ID, or ``None`` when the run did not create the object.
    :raises QualificationError: The object was created but its ID is not recorded.
    """
    if created.get(key) is not True:
        return None
    identity = _object(evidence).get("id")
    if type(identity) is not int:
        raise QualificationError(missing)
    return identity


def _location(evidence: JSONMap) -> str:
    """Name a recorded repository as ``HOST/PATH`` for a manual read-back.

    :param evidence: Ledger ``source`` or ``destination`` evidence.
    :returns: ``HOST/PATH``.
    """
    return EVIDENCE_LOCATION.format(
        host=evidence.get("host"), path=evidence.get("path")
    )


def _status(status: Optional[int]) -> Optional[int]:
    """Render an HTTP status as a plain integer.

    :param status: ``HTTPStatus``, integer or ``None``.
    :returns: The integer, or ``None``.
    """
    return None if status is None else int(status)


def _record(result: JSONMap) -> JSONMap:
    """Read the single record of a mirror result.

    :param result: Mirror command result.
    :returns: Its one record.
    :raises QualificationError: The result does not carry exactly one record.
    """
    records = result.get("records")
    if not isinstance(records, list) or len(records) != 1:
        raise QualificationError("result_records_unexpected")
    return _object(records[0])


def _object(value: object) -> JSONMap:
    """Return a mapping value, or an empty mapping for anything else.

    :param value: Decoded JSON value.
    :returns: The mapping, or ``{}``.
    """
    return value if isinstance(value, dict) else {}


def _read(path: Path) -> JSONMap:
    """Read one JSON object file.

    :param path: File written by the mirror command or this module.
    :returns: Decoded object.
    :raises QualificationError: The file is absent or not one JSON object.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QualificationError(f"receipt_unreadable:{path.name}") from exc
    if not isinstance(data, dict):
        raise QualificationError(f"receipt_not_object:{path.name}")
    return data


def _json_text(data: JSONMap) -> str:
    """Render one JSON object with sorted keys and a trailing newline.

    :param data: Object to render.
    :returns: JSON text.
    """
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


@contextlib.contextmanager
def _staged(path: Path, data: JSONMap) -> Iterator[Path]:
    """Write one JSON object to a synced temporary file beside ``path``.

    The temporary file is removed on exit; after ``os.replace`` it no longer exists.

    :param path: Final file; the temporary file goes in its directory.
    :param data: Object to write.
    :yields: Temporary file path.
    :raises OSError: The temporary file cannot be created or written.
    """  # noqa: DOC502 - mkstemp, write and fsync raise OSError.
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=STAGED_SUFFIX, dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, WRITE_MODE, encoding="utf-8") as stream:
            stream.write(_json_text(data))
            stream.flush()
            os.fsync(stream.fileno())
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def _write(path: Path, data: JSONMap) -> None:
    """Write one JSON object atomically with ``os.replace``.

    :param path: Destination file.
    :param data: Object to write.
    :raises QualificationError: The file cannot be written.
    """
    try:
        with _staged(path, data) as temporary:
            os.replace(temporary, path)
    except OSError as exc:
        raise QualificationError(f"write_failed:{path.name}") from exc
