"""One bounded qualification of an installed mirror command for candidate receipts.

`docs/2026-10-31/live-smoke-inventory.md` (Reusable pairs, `test-mirror-push`) allows
one bounded qualification when candidate evidence requires renewal. This module runs
it against an installed copy of the skill:

1. live plan; the fixture must hold no destination repository and no mirror;
2. a wrong confirmation digest, which must block without mutation;
3. the confirmed apply, written as the APPLIED receipt;
4. after a settle interval, a second live plan and confirmed apply, written as the
   NO_OP receipt;
5. owned cleanup: the run-created GitLab mirror, then the run-created GitHub
   repository, each matched by native ID and removed once;
6. independent absence read-back, a settle interval, and a repeat read-back that
   deletes nothing; the result is the `gitlab_mirror_smoke_cleanup` record that
   `tools/check_live_acceptance.py` verifies on the NO_OP receipt.

An allocation ledger in the work directory records each step. A run stops when the
ledger already exists, so an interrupted run is reconciled before another starts.
Provider calls sit behind `MirrorProvider`; `NativeMirrorProvider` binds the
installed skill's own GitLab and GitHub services.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import abc
import contextlib
import json
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Final, Optional

ALLOCATION_FILE: Final = "mirror-live-allocation.json"
APPLIED_RECEIPT: Final = "mirror-applied-receipt.json"
NOOP_RECEIPT: Final = "mirror-noop-receipt.json"
NOOP_WITH_CLEANUP: Final = "mirror-noop-with-cleanup-receipt.json"
CLEANUP_RECORD: Final = "mirror-smoke-cleanup.json"
CLEANUP_KIND: Final = "gitlab_mirror_smoke_cleanup"
INSTALLATION_FILE: Final = "INSTALLATION.json"
MIRROR_SCRIPT: Final = Path("bin") / "gitlab_mirror.py"
WRONG_DIGEST: Final = "0" * 64
STATUS_PLANNED: Final = "PLANNED"
STATUS_PASS: Final = "PASS"
STATUS_BLOCKED: Final = "BLOCKED"
ACTION_APPLIED: Final = "APPLIED"
ACTION_NO_OP: Final = "NO_OP"
EXIT_SUCCESS: Final = 0
EXIT_BLOCKED: Final = 2
SEED_REF_KINDS: Final = ("branches", "tags")

JSONMap = Mapping[str, object]


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
    :param gitlab: ``--gitlab`` selector, or ``None`` for the routing default.
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

    .. attribute :: gitlab
        :type: Optional[str]

        ``--gitlab`` selector, or ``None`` for the routing default.

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
    gitlab: Optional[str] = None
    timeout: int = 300
    settle_seconds: int = 30

    def command_selection(self) -> tuple[str, ...]:
        """Build the fixed ``gitlab_mirror.py sync`` arguments of this run.

        :returns: Operation, fixture, timeout, output and selector arguments.
        """
        selector = ("--gitlab", self.gitlab) if self.gitlab else ()
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
    """Run ``bin/gitlab_mirror.py`` of the installed copy in a subprocess."""

    def __init__(self, inputs: QualificationInputs):
        """Bind the run inputs.

        :param inputs: Fixture identity and run controls.
        """
        self.inputs = inputs

    def run(self, label: str, options: Sequence[str]) -> tuple[int, JSONMap]:
        """Run one mode and keep its stdout and stderr under the work directory.

        :param label: Output file stem for this step.
        :param options: Mode options appended to the fixed selection.
        :returns: Exit code and decoded JSON result.
        :raises QualificationError: The output is not one JSON object.
        """
        inputs = self.inputs
        command = [
            sys.executable,
            str(inputs.skill / MIRROR_SCRIPT),
            *inputs.command_selection(),
            *options,
        ]
        output = inputs.work_dir / f"{label}.json"
        with (
            output.open("w", encoding="utf-8") as stdout,
            (inputs.work_dir / f"{label}.stderr").open("w", encoding="utf-8") as stderr,
        ):
            result = subprocess.run(
                command,
                cwd=inputs.work_dir,
                stdout=stdout,
                stderr=stderr,
                timeout=inputs.timeout + 60,
                check=False,
            )
        try:
            data = json.loads(output.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise QualificationError(f"{label}_output_not_json") from exc
        if not isinstance(data, dict):
            raise QualificationError(f"{label}_output_not_object")
        return result.returncode, data


class MirrorProvider(abc.ABC):
    """Native reads and owned deletes used by cleanup, by native ID."""

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
    def delete_destination(self, path: str) -> int:
        """Delete the destination repository.

        :param path: Destination repository path.
        :returns: HTTP status of the deletion.
        """


class MirrorQualification:
    """Run the bounded qualification and write its receipts and cleanup record.

    The cleanup provider is built only after both confirmed applies.
    """

    def __init__(
        self,
        inputs: QualificationInputs,
        runner: CommandRunner,
        provider: Callable[[], MirrorProvider],
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        """Bind the run inputs, command runner and provider factory.

        :param inputs: Fixture identity and run controls.
        :param runner: Installed mirror command runner.
        :param provider: Factory for the cleanup provider.
        :param sleep: Pause function.
        :param clock: Current UTC time.
        """
        self.inputs = inputs
        self.runner = runner
        self.provider = provider
        self.sleep = sleep
        self.clock = clock
        self.ledger: MutableMapping[str, object] = {}

    def run(self) -> JSONMap:
        """Execute every step once and return the final ledger.

        :returns: Allocation ledger ending in ``PASS_INDEPENDENT_ABSENCE_AND_REPEAT``.
        :raises QualificationError: A precondition, result or read-back failed.
        """
        inputs = self.inputs
        ledger_path = inputs.work_dir / ALLOCATION_FILE
        if ledger_path.exists():
            raise QualificationError("allocation_exists_reconcile_first")
        candidate = inputs.candidate()
        plan = self._expect("mirror-live-plan", ["--live-plan"], STATUS_PLANNED)
        record = _record(plan)
        source = _object(record.get("source"))
        if (
            source.get("id") != inputs.source_id
            or _object(record.get("destination")).get("id") is not None
            or record.get("mirror") is not None
        ):
            raise QualificationError("fixture_not_clean")
        rejection = self._expect(
            "mirror-rejection",
            ["--apply", "--confirm-plan", WRONG_DIGEST],
            STATUS_BLOCKED,
            EXIT_BLOCKED,
        )
        if rejection.get("mutated") is not False:
            raise QualificationError("rejection_mutated")
        self._save(
            run=inputs.run_id,
            candidate=candidate,
            source=source,
            destination=record.get("destination"),
            mirror=None,
            cleanup="APPLY_PENDING",
        )
        applied = self._expect(
            "mirror-applied",
            self._apply(plan, APPLIED_RECEIPT),
            STATUS_PASS,
        )
        created = _record(applied)
        if (
            created.get("action") != ACTION_APPLIED
            or created.get("created") != {"mirror": True, "repository": True}
            or created.get("source_refs") != created.get("destination_refs")
        ):
            raise QualificationError("applied_result_unexpected")
        self._save(
            destination=created.get("destination"),
            mirror=created.get("mirror"),
            created=created.get("created"),
            cleanup="PENDING",
        )
        self.sleep(inputs.settle_seconds)
        repeat_plan = self._expect("mirror-noop-plan", ["--live-plan"], STATUS_PLANNED)
        noop = self._expect(
            "mirror-noop", self._apply(repeat_plan, NOOP_RECEIPT), STATUS_PASS
        )
        repeated = _record(noop)
        if (
            repeated.get("action") != ACTION_NO_OP
            or noop.get("mutated") is not False
            or repeated.get("destination") != created.get("destination")
            or _object(repeated.get("mirror")).get("id")
            != _object(created.get("mirror")).get("id")
        ):
            raise QualificationError("noop_result_unexpected")
        receipt = _read(inputs.work_dir / NOOP_RECEIPT)
        if receipt.get("tested_revision") != candidate:
            raise QualificationError("receipt_candidate_mismatch")
        cleanup = self._cleanup(self.provider(), created, receipt)
        _write(inputs.work_dir / CLEANUP_RECORD, cleanup)
        _write(
            inputs.work_dir / NOOP_WITH_CLEANUP, {**receipt, "smoke_cleanup": cleanup}
        )
        self._save(cleanup="PASS_INDEPENDENT_ABSENCE_AND_REPEAT")
        return dict(self.ledger)

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
            "--apply",
            "--confirm-plan",
            digest,
            "--receipt-out",
            str(self.inputs.work_dir / receipt),
        ]

    def _expect(
        self,
        label: str,
        options: Sequence[str],
        status: str,
        exit_code: int = EXIT_SUCCESS,
    ) -> JSONMap:
        """Run one step and require its exit code and status.

        :param label: Output file stem.
        :param options: Mode options.
        :param status: Required result status.
        :param exit_code: Required exit code.
        :returns: Decoded result.
        :raises QualificationError: Exit code or status differs.
        """
        code, data = self.runner.run(label, options)
        if code != exit_code or data.get("status") != status:
            raise QualificationError(f"{label}_unexpected:{code}:{data.get('status')}")
        return data

    def _cleanup(
        self, provider: MirrorProvider, created: JSONMap, receipt: JSONMap
    ) -> JSONMap:
        """Remove the run-created mirror and repository and prove their absence.

        :param provider: Native reads and owned deletes.
        :param created: APPLIED record holding the created native IDs.
        :param receipt: NO_OP receipt whose identity binds the cleanup record.
        :returns: ``gitlab_mirror_smoke_cleanup`` record.
        :raises QualificationError: An identity differs or a deletion is unproven.
        """
        destination = str(self.inputs.destination)
        mirror_id = _object(created.get("mirror")).get("id")
        repository_id = _object(created.get("destination")).get("id")
        if type(mirror_id) is not int or type(repository_id) is not int:
            raise QualificationError("created_ids_missing")
        source = provider.source()
        if source != created.get("source"):
            raise QualificationError("source_identity_changed")
        if provider.destination(destination) != created.get("destination"):
            raise QualificationError("destination_identity_changed")
        if provider.mirror_targets(mirror_id, destination) is not True:
            raise QualificationError("mirror_identity_changed")
        before = {
            "source": source,
            "destination": created.get("destination"),
            "mirror_id": mirror_id,
        }
        self._save(cleanup="MIRROR_DELETE_PENDING")
        mirror_status = provider.delete_mirror(mirror_id, destination)
        if mirror_status != HTTPStatus.NO_CONTENT or (
            provider.mirror_targets(mirror_id, destination) is not None
        ):
            raise QualificationError("mirror_deletion_unverified")
        self._save(cleanup="MIRROR_ABSENT_DESTINATION_DELETE_PENDING")
        if provider.destination(destination) != created.get("destination"):
            raise QualificationError("destination_identity_changed")
        destination_status = provider.delete_destination(destination)
        if destination_status != HTTPStatus.NO_CONTENT:
            raise QualificationError("destination_deletion_unverified")
        self._save(cleanup="DELETES_RETURNED_204_VERIFY_PENDING")
        after = self._observe(provider, source, mirror_id, repository_id)
        self.sleep(self.inputs.settle_seconds)
        repeat = {
            **self._observe(provider, source, mirror_id, repository_id),
            "mutated": False,
        }
        skill = _object(receipt.get("skill"))
        return {
            "kind": CLEANUP_KIND,
            "status": STATUS_PASS,
            "captured_at": self.clock().isoformat(),
            "execution_host": receipt.get("execution_host"),
            "skill_digest": skill.get("digest"),
            "tested_revision": receipt.get("tested_revision"),
            "before": before,
            "delete": {
                "mirror_http_status": int(mirror_status),
                "destination_http_status": int(destination_status),
            },
            "after": after,
            "repeat": repeat,
        }

    def _observe(
        self,
        provider: MirrorProvider,
        source: JSONMap,
        mirror_id: int,
        repository_id: int,
    ) -> JSONMap:
        """Read the retained source and the absence of both removed objects.

        :param provider: Native reads.
        :param source: Source evidence read before cleanup.
        :param mirror_id: Removed mirror ID.
        :param repository_id: Removed destination repository ID.
        :returns: Read-back record with HTTP absence statuses.
        :raises QualificationError: The source changed, a seed ref moved, or a
            removed object is still present.
        """
        if provider.source() != source:
            raise QualificationError("source_identity_changed")
        for ref in self.inputs.seed_refs:
            if provider.ref_commit(ref) != ref.commit:
                raise QualificationError(f"seed_ref_moved:{ref.kind}/{ref.name}")
        if provider.mirror_targets(mirror_id, self.inputs.destination) is not None:
            raise QualificationError("mirror_not_absent")
        if provider.destination(self.inputs.destination) is not None:
            raise QualificationError("destination_not_absent")
        if provider.destination_id_present(repository_id):
            raise QualificationError("destination_id_not_absent")
        return {
            "source": source,
            "mirror_absent": True,
            "destination_http_status": int(HTTPStatus.NOT_FOUND),
            "destination_id_http_status": int(HTTPStatus.NOT_FOUND),
        }

    def _save(self, **fields: object) -> None:
        """Update and write the allocation ledger.

        :param **fields: Ledger fields to set.
        """
        self.ledger.update(fields)
        _write(self.inputs.work_dir / ALLOCATION_FILE, self.ledger)


class NativeMirrorProvider(MirrorProvider):
    """Bind the installed skill's GitLab and GitHub services through one live plan.

    The installed ``lib`` package is imported from ``inputs.skill``; the caller
    must not have imported another copy of it.
    """

    def __init__(self, inputs: QualificationInputs):
        """Run one in-process live plan and keep its bound provider services.

        :param inputs: Fixture identity and run controls.
        :raises QualificationError: The live plan does not succeed.
        """
        if str(inputs.skill) not in sys.path:
            sys.path.insert(0, str(inputs.skill))
        from lib.python.core.cli_api import APIError
        from lib.python.core.gitlab.gitlab_api import create_guard
        from lib.python.core.gitlab.mirror.cli import MirrorCommand, build_parser

        self._api_error = APIError
        self._create_guard = create_guard
        args = build_parser().parse_args([*inputs.command_selection(), "--live-plan"])
        command = MirrorCommand(args)
        output = inputs.work_dir / "mirror-cleanup-before-plan.json"
        with (
            output.open("w", encoding="utf-8") as stream,
            contextlib.redirect_stdout(stream),
        ):
            code = command.execute()
        engine = command.state.engine
        if code != EXIT_SUCCESS or engine is None:
            raise QualificationError("cleanup_live_plan_failed")
        self.inputs = inputs
        self._source = engine.source
        self._destination = engine.destination

    def source(self) -> JSONMap:
        """Read the source project evidence.

        :returns: ``id``, ``host`` and ``path`` of the source project.
        """
        return self._source.repository(self.inputs.project).evidence()

    def ref_commit(self, ref: SeedRef) -> str:
        """Read the commit a source ref points at.

        :param ref: Retained source ref.
        :returns: Commit SHA.
        """
        service = self._source.service
        data = service.api.get_json(
            service.session,
            f"projects/{self.inputs.source_id}/repository/{ref.kind}/{ref.name}",
        )
        return str(data["commit"]["id"])

    def mirror_targets(self, mirror_id: int, destination: str) -> Optional[bool]:
        """Read whether a mirror exists and pushes to the destination.

        :param mirror_id: Native mirror ID.
        :param destination: Destination repository path.
        :returns: ``None`` when absent, otherwise whether it targets the destination.
        :raises APIError: A provider error other than not-found.
        """  # noqa: DOC503 - The bare raise re-raises the provider APIError.
        try:
            mirror = self._source.mirror(mirror_id)
        except self._api_error as exc:
            if exc.reason != "provider_404":
                raise
            return None
        repository = self._destination.repository(destination)
        return repository is not None and mirror.url == repository.clone_url

    def delete_mirror(self, mirror_id: int, destination: str) -> int:
        """Delete one mirror by native ID under the existing pending-write guard.

        :param mirror_id: Native mirror ID.
        :param destination: Destination repository path the mirror must target.
        :returns: HTTP status of the deletion.
        :raises QualificationError: The mirror no longer targets the destination.
        """
        repository = self._destination.repository(destination)
        if repository is None:
            raise QualificationError("destination_identity_changed")
        service = self._source.service
        with self._create_guard(
            service.session.host,
            "project",
            service.target_id,
            "remote_mirror",
            repository.clone_url,
        ) as guard:
            guard.require_ready()
            if self.mirror_targets(mirror_id, destination) is not True:
                raise QualificationError("mirror_identity_changed")
            guard.mark_pending()
            service.api.request_no_content(
                service.session,
                "DELETE",
                f"projects/{self.inputs.source_id}/remote_mirrors/{mirror_id}",
            )
            guard.clear()
        return int(HTTPStatus.NO_CONTENT)

    def destination(self, path: str) -> Optional[JSONMap]:
        """Read destination repository evidence by path.

        :param path: Destination repository path.
        :returns: ``id``, ``host`` and ``path``, or ``None`` when absent.
        """
        repository = self._destination.repository(path)
        return repository.evidence() if repository is not None else None

    def destination_id_present(self, repository_id: int) -> bool:
        """Read whether a repository with a native ID exists.

        :param repository_id: Native GitHub repository ID.
        :returns: Whether the provider returns the repository.
        :raises APIError: A provider error other than not-found.
        """  # noqa: DOC503 - The bare raise re-raises the provider APIError.
        try:
            self._destination.session.request("GET", f"repositories/{repository_id}")
        except self._api_error as exc:
            if exc.reason != "provider_404":
                raise
            return False
        return True

    def delete_destination(self, path: str) -> int:
        """Delete the destination repository.

        :param path: Destination repository path.
        :returns: HTTP status of the deletion.
        """
        session = self._destination.session
        session.api.request_no_content(session, "DELETE", f"repos/{path}")
        return int(HTTPStatus.NO_CONTENT)


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

    :param path: File written by the mirror command.
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


def _write(path: Path, data: JSONMap) -> None:
    """Write one JSON object with a trailing newline.

    :param path: Destination file.
    :param data: Object to write.
    """
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
