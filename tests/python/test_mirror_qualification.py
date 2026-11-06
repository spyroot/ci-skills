"""Offline tests for the bounded mirror qualification tool.

`tools/skillkit/mirror_qualification.py` runs the installed mirror command and
removes only the objects that run created; `tools/qualify_mirror.py` is its thin
adapter. These tests drive it with a scripted command runner, a fake provider and
fake installed provider services, so no provider or process is contacted.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import signal
import subprocess
import sys
from collections.abc import Callable, Mapping, MutableSequence, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, Final, NoReturn, Optional

import pytest
from conftest import REPO_ROOT, SKILL_ROOT, import_script_module, load_module
from test_gitlab_mirror import (
    MIRROR_APPLIED_FILE,
    MIRROR_NO_OP_FILE,
    _mirror_pair_baseline,
)

CLI = load_module("qualify_mirror_cli", REPO_ROOT / "tools" / "qualify_mirror.py")
# The module object the adapter imported, so exception classes are shared.
QUALIFY = sys.modules[CLI.MirrorQualification.__module__]
ACCEPTANCE = load_module(
    "check_live_acceptance_for_mirror_tool",
    REPO_ROOT / "tools" / "check_live_acceptance.py",
)
CATALOG = import_script_module("lib.python.core.catalog")
ENDPOINTS = import_script_module("lib.python.core.endpoints")
CLI_API = import_script_module("lib.python.core.cli_api")
MIRROR_PROVIDER = import_script_module("lib.python.core.gitlab.mirror.provider")
REPOSITORY = import_script_module("lib.python.core.github.repository")
SYS_STATUS = import_script_module(QUALIFY.STATUS_MODULE)
OUTCOME = import_script_module(QUALIFY.MIRROR_CONSTANTS_MODULE).Outcome
SCHEMA = import_script_module("lib.python.core.schema")
EVIDENCE = import_script_module("lib.python.core.gitlab.mirror.evidence")
PROVENANCE = import_script_module("lib.python.core.provenance")

# The repository's own lib stands in for the installed copy.
STATUS: Final = QUALIFY.InstalledStatus.bind(SYS_STATUS, OUTCOME)
EXIT_OK: Final[int] = STATUS.exit_code(STATUS.passed)
EXIT_STOP: Final[int] = STATUS.exit_code(STATUS.blocked)
MAX_AGE_DAYS: Final[int] = ACCEPTANCE.load_policy(
    REPO_ROOT / ACCEPTANCE.POLICY_FILE, REPO_ROOT / ACCEPTANCE.POLICY_SCHEMA_FILE
)["max_receipt_age_days"]
MIRROR_OPERATIONS: Final = tuple(
    (ACCEPTANCE.MIRROR_KIND, ACCEPTANCE.MIRROR_OPERATION, action)
    for action in (STATUS.applied, STATUS.no_op)
)
CLEANUP_DELAY: Final = timedelta(seconds=1)
CLEANUP_FIELDS: Final = (
    "SMOKE_CLEANUP_FIELD",
    "KIND_FIELD",
    "BEFORE_FIELD",
    "DELETE_FIELD",
    "AFTER_FIELD",
    "REPEAT_FIELD",
    "SOURCE_FIELD",
    "DESTINATION_FIELD",
    "MIRROR_ID_FIELD",
    "MIRROR_HTTP_STATUS_FIELD",
    "DESTINATION_HTTP_STATUS_FIELD",
    "MIRROR_ABSENT_FIELD",
    "DESTINATION_ID_HTTP_STATUS_FIELD",
    "MUTATED_FIELD",
)
FAKE_PASS: Final[str] = "UNIT_PASS"
FAKE_PLANNED: Final[str] = "UNIT_PLANNED"
FAKE_BLOCKED: Final[str] = "UNIT_BLOCKED"
FAKE_APPLIED: Final[str] = "UNIT_APPLIED"
FAKE_NO_OP: Final[str] = "UNIT_NO_OP"
FAKE_EXIT_OK: Final[int] = 0
FAKE_EXIT_STOP: Final[int] = 7

CANDIDATE: Final[str] = "c" * 40
SOURCE_HOST: Final[str] = "gitlab.example.test"
SOURCE: Final[dict[str, object]] = {
    "id": 261,
    "host": SOURCE_HOST,
    "path": "unit/mirror-source",
}
SOURCE_ID: Final[int] = 261
DESTINATION_PATH: Final[str] = "unit/mirror-destination"
REPOSITORY_ID: Final[int] = 9001
DESTINATION: Final[dict[str, object]] = {
    "id": REPOSITORY_ID,
    "host": "github.example.test",
    "path": DESTINATION_PATH,
}
CLONE_URL: Final[str] = "https://github.example.test/unit/mirror-destination.git"
MIRROR_ID: Final[int] = 15
MIRROR: Final[dict[str, object]] = {"id": MIRROR_ID}
SEED: Final[str] = "a" * 40
MOVED: Final[str] = "e" * 40
REFS: Final[dict[str, object]] = {"count": 2, "digest": "unit"}
PLAN_DIGEST: Final[str] = "b" * 64
CAPTURED: Final[str] = "2026-11-06T00:00:00+00:00"
TARGET_SOURCE: Final[str] = "project"
OTHER_TARGET_SOURCE: Final[str] = "user"
PROVIDER_REASON: Final[str] = "unit_provider_failure"
DESTINATION_DELETED: Final[str] = f"destination:{DESTINATION_PATH}:{REPOSITORY_ID}"
MIRROR_DELETED: Final[str] = f"mirror:{MIRROR_ID}"
BRANCHES, TAGS = QUALIFY.SEED_REF_KINDS
MAIN_REF: Final = QUALIFY.SeedRef(BRANCHES, "main", SEED)
TAG_REF: Final = QUALIFY.SeedRef(TAGS, "smoke-seed", SEED)
SEED_REFS: Final[dict[str, str]] = {MAIN_REF.key: SEED, TAG_REF.key: SEED}
BEFORE_DELETE: Final[str] = "before-delete"
AFTER_DELETE: Final[str] = "after-delete"


@pytest.fixture
def inputs(tmp_path: Path) -> object:
    """Create an installed-copy identity and a work directory.

    :param tmp_path: Directory owned by the test.
    :returns: Qualification inputs for the synthetic fixture.
    """
    skill = tmp_path / "installed"
    skill.mkdir()
    (skill / QUALIFY.INSTALLATION_FILE).write_text(
        json.dumps({"revision": {"value": CANDIDATE, "verified": True}}),
        encoding="utf-8",
    )
    work = tmp_path / "work"
    work.mkdir()
    return QUALIFY.QualificationInputs(
        skill=skill,
        work_dir=work,
        project=str(SOURCE["path"]),
        destination=DESTINATION_PATH,
        source_id=SOURCE_ID,
        seed_refs=(MAIN_REF, TAG_REF),
        run_id="unit-run",
        gitlab_option=CATALOG.GITLAB_TARGET_OPTION,
        settle_seconds=0,
    )


@pytest.fixture
def bindings() -> object:
    """Bind the repository's own ``lib`` package as the installed copy.

    :returns: Names bound from ``ci-skills/lib``.
    """
    return QUALIFY.installed_bindings(SKILL_ROOT)


class ScriptedRunner(QUALIFY.CommandRunner):
    """Return one scripted result per step and write receipts like the command."""

    def __init__(
        self,
        results: Mapping[str, tuple[int, dict[str, object]]],
        hooks: Optional[Mapping[str, Callable[[], None]]] = None,
    ):
        """Keep the scripted results.

        :param results: Exit code and result per step label.
        :param hooks: Side effect run before a step returns, per step label.
        """
        self.results = results
        self.hooks = hooks or {}
        self.calls: MutableSequence[tuple[str, tuple[str, ...]]] = []

    def run(self, label: str, options: Sequence[str]) -> tuple[int, object]:
        """Record the call, write a requested receipt and return the scripted result.

        :param label: Step label.
        :param options: Mode options.
        :returns: Scripted exit code and result.
        """
        self.calls.append((label, tuple(options)))
        code, data = self.results[label]
        if QUALIFY.RECEIPT_OPTION in options:
            path = Path(options[options.index(QUALIFY.RECEIPT_OPTION) + 1])
            path.write_text(json.dumps(data), encoding="utf-8")
        if label in self.hooks:
            self.hooks[label]()
        return code, data

    @property
    def labels(self) -> list[str]:
        """List the step labels run so far.

        :returns: Labels in call order.
        """
        return [label for label, _ in self.calls]


class FakeProvider(QUALIFY.MirrorProvider):
    """Hold one mirror and one destination and record every deletion.

    Calling the instance is the provider factory: it records the label and returns
    the instance.
    """

    def __init__(self, mirror: bool = True, destination: bool = True) -> None:
        """Start with the run-created objects present or absent.

        :param mirror: Whether the mirror starts present.
        :param destination: Whether the destination starts present.
        """
        self.mirror_present = mirror
        self.destination_present = destination
        self.mirror_deleted = False
        self.destination_deleted = False
        self.mirror_reads_after_delete = 0
        self.deletes: MutableSequence[str] = []
        self.labels: MutableSequence[str] = []
        self.mirror_delete_status = 204
        self.keep_mirror = False
        self.mirror_matches = True
        self.destination_override: Optional[dict[str, object]] = None
        self.destination_after_mirror_delete: Optional[dict[str, object]] = None
        self.vanish_after_mirror_delete = False
        self.id_elsewhere = False
        self.reappear: frozenset[str] = frozenset()
        self.commits: dict[str, str] = {}
        self.moved_after_delete: frozenset[str] = frozenset()
        self.target = TARGET_SOURCE
        self.target_for_label: dict[str, str] = {}
        self.source_override: Optional[dict[str, object]] = None
        self.interrupt_mirror_delete: Optional[str] = None

    def __call__(self, label: str) -> FakeProvider:
        """Record the factory label and return this provider.

        :param label: Output file stem of the provider's live plan.
        :returns: This provider.
        """
        self.labels.append(label)
        self.target = self.target_for_label.get(label, self.target)
        return self

    def target_source(self) -> str:
        """Return the configured target source.

        :returns: Target source label.
        """
        return self.target

    def source(self) -> object:
        """Return the retained source evidence.

        :returns: Source evidence.
        """
        return self.source_override or dict(SOURCE)

    def ref_commit(self, ref: object) -> str:
        """Return the configured commit of a retained ref.

        :param ref: Retained source ref.
        :returns: Seed commit unless configured otherwise.
        """
        if self.mirror_deleted and ref.key in self.moved_after_delete:
            return MOVED
        return self.commits.get(ref.key, SEED)

    def mirror_targets(self, mirror_id: int, destination: str) -> Optional[bool]:
        """Report the mirror while present.

        :param mirror_id: Native mirror ID.
        :param destination: Destination path.
        :returns: Whether it targets the destination while present, otherwise None;
            with ``reappear`` holding ``mirror``, True after the first absent read.
        """
        if self.mirror_present:
            return self.mirror_matches
        if self.mirror_deleted:
            self.mirror_reads_after_delete += 1
            if self.mirror_reads_after_delete > 1 and "mirror" in self.reappear:
                return True
        return None

    def delete_mirror(self, mirror_id: int, destination: str) -> int:
        """Record and apply one mirror deletion.

        :param mirror_id: Native mirror ID.
        :param destination: Destination path.
        :returns: Configured HTTP status.
        :raises KeyboardInterrupt: ``interrupt_mirror_delete`` names this point.
        """
        if self.interrupt_mirror_delete == BEFORE_DELETE:
            raise KeyboardInterrupt
        self.deletes.append(f"mirror:{mirror_id}")
        self.mirror_deleted = True
        if not self.keep_mirror:
            self.mirror_present = False
        if self.destination_after_mirror_delete is not None:
            self.destination_override = self.destination_after_mirror_delete
        if self.vanish_after_mirror_delete:
            self.destination_present = False
        if self.interrupt_mirror_delete == AFTER_DELETE:
            raise KeyboardInterrupt
        return self.mirror_delete_status

    def destination(self, path: str) -> Optional[object]:
        """Return the destination evidence while present.

        :param path: Destination path.
        :returns: Evidence, or None when absent.
        """
        if self.destination_present:
            return self.destination_override or dict(DESTINATION)
        if self.destination_deleted and "destination" in self.reappear:
            return dict(DESTINATION)
        return None

    def destination_id_present(self, repository_id: int) -> bool:
        """Report the destination ID while present.

        :param repository_id: Native repository ID.
        :returns: Whether present.
        """
        return (
            self.destination_present
            or self.id_elsewhere
            or (self.destination_deleted and "destination_id" in self.reappear)
        )

    def delete_destination(self, path: str, repository_id: int) -> int:
        """Record and apply one destination deletion.

        :param path: Destination path.
        :param repository_id: Expected native repository ID.
        :returns: HTTP 204.
        """
        self.deletes.append(f"destination:{path}:{repository_id}")
        self.destination_present = False
        self.destination_deleted = True
        return 204


def _planned(
    record: Optional[Mapping[str, object]] = None,
    tokens: object = STATUS,
    **top: object,
) -> dict[str, object]:
    """Build a live-plan result of a clean fixture with optional changes.

    :param record: Record fields replacing the clean ones.
    :param tokens: Status tokens the scripted command reports with.
    :param **top: Top-level fields replacing the clean ones.
    :returns: Live-plan result.
    """
    clean = {
        "source": SOURCE,
        "destination": {
            "id": None,
            "host": DESTINATION["host"],
            "path": DESTINATION_PATH,
        },
        "mirror": None,
    }
    return {
        "status": tokens.planned,
        "plan_digest": PLAN_DIGEST,
        QUALIFY.TARGET_SOURCE_FIELD: TARGET_SOURCE,
        "records": [{**clean, **(record or {})}],
        **top,
    }


def _created(tokens: object = STATUS, **changes: object) -> dict[str, object]:
    """Build the APPLIED record of a run that created both objects.

    :param tokens: Status tokens the scripted command reports with.
    :param **changes: Record fields replacing the defaults.
    :returns: APPLIED record.
    """
    return {
        "action": tokens.applied,
        "source": SOURCE,
        "destination": DESTINATION,
        "mirror": MIRROR,
        "created": {"mirror": True, "repository": True},
        "before": {"mirror": None},
        "replacement_required": False,
        "source_refs": REFS,
        "destination_refs": REFS,
        **changes,
    }


def _results(
    overrides: Optional[Mapping[str, tuple[int, dict[str, object]]]] = None,
    tokens: object = STATUS,
) -> dict[str, tuple[int, dict[str, object]]]:
    """Build the scripted results of a successful run with optional overrides.

    :param overrides: Replacement result per step label.
    :param tokens: Status tokens and exit mapping the scripted command uses.
    :returns: Exit code and result per step label.
    """
    passed = tokens.exit_code(tokens.passed)
    receipt = {
        "captured_at": CAPTURED,
        "execution_host": "unit-host",
        "tested_revision": CANDIDATE,
        QUALIFY.TARGET_SOURCE_FIELD: TARGET_SOURCE,
        "skill": {
            "digest": "unit-digest",
            "revision": {"value": CANDIDATE, "verified": True},
        },
    }
    noop_record = _created(tokens, action=tokens.no_op)
    results = {
        QUALIFY.LIVE_PLAN_STEP: (passed, _planned(tokens=tokens)),
        QUALIFY.REJECTION_STEP: (
            tokens.exit_code(tokens.blocked),
            {"status": tokens.blocked, "mutated": False},
        ),
        QUALIFY.APPLIED_STEP: (
            passed,
            {
                **receipt,
                "status": tokens.passed,
                "mutated": True,
                "records": [_created(tokens)],
            },
        ),
        QUALIFY.NOOP_PLAN_STEP: (passed, _planned(noop_record, tokens)),
        QUALIFY.NOOP_STEP: (
            passed,
            {
                **receipt,
                "status": tokens.passed,
                "mutated": False,
                "records": [noop_record],
            },
        ),
    }
    results.update(overrides or {})
    return results


def _applied(record: dict[str, object]) -> tuple[int, dict[str, object]]:
    """Build a PASS apply result holding one record.

    :param record: APPLIED record.
    :returns: Exit code and result.
    """
    code, applied = _results()[QUALIFY.APPLIED_STEP]
    return code, {**applied, "records": [record]}


def _ledger(inputs: object) -> dict[str, object]:
    """Read the allocation ledger.

    :param inputs: Synthetic fixture inputs.
    :returns: Ledger content.
    """
    return json.loads((inputs.work_dir / QUALIFY.ALLOCATION_FILE).read_text())


def _qualification(
    inputs: object,
    runner: object,
    provider: Callable[[str], object],
    tokens: object = STATUS,
    **controls: object,
) -> object:
    """Build a qualification bound to the given status tokens.

    :param inputs: Synthetic fixture inputs.
    :param runner: Command runner.
    :param provider: Provider factory.
    :param tokens: Status tokens bound from the installed copy.
    :param **controls: ``sleep`` or ``clock`` replacement.
    :returns: Qualification under test.
    """
    return QUALIFY.MirrorQualification(inputs, runner, provider, tokens, **controls)


def _interrupt() -> None:
    """Stand in for SIGINT arriving inside a step.

    :raises KeyboardInterrupt: Always.
    """
    raise KeyboardInterrupt


def _run(
    inputs: object,
    provider: FakeProvider,
    overrides: Optional[Mapping[str, tuple[int, dict[str, object]]]] = None,
) -> ScriptedRunner:
    """Run a qualification expected to stop with ``QualificationError``.

    :param inputs: Synthetic fixture inputs.
    :param provider: Fake provider and factory.
    :param overrides: Replacement result per step label.
    :returns: The runner, for call assertions.
    """
    runner = ScriptedRunner(_results(overrides))
    with pytest.raises(QUALIFY.QualificationError):
        _qualification(inputs, runner, provider).run()
    return runner


def _engine_pair() -> tuple[dict[str, object], dict[str, object]]:
    """Bind the real-engine APPLIED and NO_OP receipts to this fixture's candidate.

    `test_gitlab_mirror._mirror_pair_baseline` runs the installed mirror engine
    offline; only the revision and the functional digest are replaced, as
    `test_live_acceptance` does, so the checker can match the candidate.

    :returns: APPLIED and NO_OP receipts.
    """
    pair = json.loads(_mirror_pair_baseline())
    digest = PROVENANCE.functional_digest(SKILL_ROOT)
    for receipt in pair.values():
        receipt["tested_revision"] = CANDIDATE
        receipt["skill"]["revision"]["value"] = CANDIDATE
        receipt["skill"]["functional_digest"] = digest
    return pair[MIRROR_APPLIED_FILE], pair[MIRROR_NO_OP_FILE]


def test_qualification_removes_only_created_objects_and_records_cleanup(
    inputs: object,
) -> None:
    """Run every step once; the receipt passes its schema and the public checker.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify steps, deletions, ledger, schema and checker.
    """
    applied, noop = _engine_pair()
    record = applied["records"][0]
    source = record[QUALIFY.SOURCE_FIELD]
    destination = record[QUALIFY.DESTINATION_FIELD]
    mirror_id = record["mirror"]["id"]
    selected = dataclasses.replace(
        inputs,
        project=source["path"],
        destination=destination["path"],
        source_id=source["id"],
    )
    absent = {**destination, "id": None}
    runner = ScriptedRunner(
        _results(
            {
                QUALIFY.LIVE_PLAN_STEP: (
                    EXIT_OK,
                    _planned(
                        {
                            QUALIFY.SOURCE_FIELD: source,
                            QUALIFY.DESTINATION_FIELD: absent,
                        }
                    ),
                ),
                QUALIFY.APPLIED_STEP: (EXIT_OK, applied),
                QUALIFY.NOOP_STEP: (EXIT_OK, noop),
            }
        )
    )
    provider = FakeProvider()
    provider.source_override = source
    provider.destination_override = destination
    captured = datetime.fromisoformat(noop["captured_at"]) + CLEANUP_DELAY

    ledger = _qualification(selected, runner, provider, clock=lambda: captured).run()

    assert runner.labels == [
        QUALIFY.LIVE_PLAN_STEP,
        QUALIFY.REJECTION_STEP,
        QUALIFY.APPLIED_STEP,
        QUALIFY.NOOP_PLAN_STEP,
        QUALIFY.NOOP_STEP,
    ]
    assert provider.labels == [QUALIFY.BASELINE_PLAN_STEP, QUALIFY.CLEANUP_PLAN_STEP]
    assert runner.calls[2][1][:3] == (
        QUALIFY.APPLY_OPTION,
        QUALIFY.CONFIRM_OPTION,
        PLAN_DIGEST,
    )
    assert provider.deletes == [
        f"mirror:{mirror_id}",
        f"destination:{destination['path']}:{destination['id']}",
    ]
    assert ledger["cleanup"] == QUALIFY.CLEANUP_PASS
    assert ledger["candidate"] == CANDIDATE
    assert ledger[QUALIFY.TARGET_SOURCE_FIELD] == TARGET_SOURCE
    assert ledger["seed_refs"] == SEED_REFS
    cleanup = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert cleanup[QUALIFY.STATUS_FIELD] == STATUS.passed
    assert cleanup[QUALIFY.BEFORE_FIELD] == {
        QUALIFY.SOURCE_FIELD: source,
        QUALIFY.DESTINATION_FIELD: destination,
        QUALIFY.MIRROR_ID_FIELD: mirror_id,
    }
    assert cleanup[QUALIFY.DELETE_FIELD] == {
        QUALIFY.MIRROR_HTTP_STATUS_FIELD: 204,
        QUALIFY.DESTINATION_HTTP_STATUS_FIELD: 204,
    }
    assert cleanup[QUALIFY.REPEAT_FIELD][QUALIFY.MUTATED_FIELD] is False
    assert QUALIFY.RECONCILED_FIELD not in cleanup
    applied_receipt = json.loads(
        (inputs.work_dir / QUALIFY.APPLIED_RECEIPT).read_text()
    )
    receipt = json.loads((inputs.work_dir / QUALIFY.NOOP_WITH_CLEANUP).read_text())
    assert receipt == {**noop, QUALIFY.SMOKE_CLEANUP_FIELD: cleanup}
    SCHEMA.validate_specification(receipt, EVIDENCE.RESULT_SCHEMA)

    result = ACCEPTANCE.evaluate(
        {},
        {
            QUALIFY.APPLIED_RECEIPT: applied_receipt,
            QUALIFY.NOOP_WITH_CLEANUP: receipt,
        },
        SKILL_ROOT,
        captured,
        max_age_days=MAX_AGE_DAYS,
        aggregate=False,
        required_operations=MIRROR_OPERATIONS,
    )

    assert result["problems"] == []
    assert result["status"] == SYS_STATUS.PASS
    assert result["accepted"] == sorted(
        (QUALIFY.APPLIED_RECEIPT, QUALIFY.NOOP_WITH_CLEANUP)
    )
    assert result["tested_revision"] == CANDIDATE


@pytest.mark.parametrize("name", CLEANUP_FIELDS)
def test_acceptance_checker_reads_the_shared_cleanup_fields(name: str) -> None:
    """Keep one definition of each cleanup record field the checker reads.

    :param name: Field constant name in `mirror_qualification`.
    :returns: None; assertions verify the checker binds the tool's constant.
    """
    assert getattr(ACCEPTANCE, name) is getattr(QUALIFY, name)
    assert ACCEPTANCE.MIRROR_SMOKE_CLEANUP_KIND is QUALIFY.CLEANUP_KIND


def test_plan_only_stops_after_the_rejection_and_writes_no_ledger(
    inputs: object,
) -> None:
    """Run the read-only steps and the wrong-digest rejection, then stop.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify steps, summary and no ledger.
    """
    runner = ScriptedRunner(_results())
    provider = FakeProvider()

    summary = _qualification(inputs, runner, provider).plan()

    assert runner.labels == [QUALIFY.LIVE_PLAN_STEP, QUALIFY.REJECTION_STEP]
    assert runner.calls[1][1] == (
        QUALIFY.APPLY_OPTION,
        QUALIFY.CONFIRM_OPTION,
        QUALIFY.WRONG_DIGEST,
    )
    assert provider.labels == [QUALIFY.BASELINE_PLAN_STEP]
    assert provider.deletes == []
    assert not (inputs.work_dir / QUALIFY.ALLOCATION_FILE).exists()
    assert summary["plan_digest"] == PLAN_DIGEST
    assert summary["candidate"] == CANDIDATE
    assert summary[QUALIFY.TARGET_SOURCE_FIELD] == TARGET_SOURCE
    assert summary["seed_refs"] == SEED_REFS


@pytest.mark.parametrize(
    ("overrides", "change", "reason"),
    (
        (
            {QUALIFY.LIVE_PLAN_STEP: (0, _planned({"source": {**SOURCE, "id": 262}}))},
            None,
            "source_id_mismatch",
        ),
        (
            {QUALIFY.LIVE_PLAN_STEP: (0, _planned({"destination": DESTINATION}))},
            None,
            "destination_present",
        ),
        (
            {QUALIFY.LIVE_PLAN_STEP: (0, _planned({"mirror": MIRROR}))},
            None,
            "mirror_present",
        ),
        (
            {QUALIFY.LIVE_PLAN_STEP: (0, _planned(target_source=None))},
            None,
            "target_source_mismatch",
        ),
        (
            None,
            lambda provider: setattr(provider, "target", OTHER_TARGET_SOURCE),
            "target_source_mismatch",
        ),
        (
            None,
            lambda provider: setattr(
                provider, "source_override", {**SOURCE, "path": "unit/other"}
            ),
            QUALIFY.SOURCE_IDENTITY_CHANGED,
        ),
        (
            None,
            lambda provider: provider.commits.update({TAG_REF.key: MOVED}),
            f"{QUALIFY.SEED_REF_BASELINE_DIFFERS}:{TAG_REF.key}",
        ),
        (
            {
                QUALIFY.REJECTION_STEP: (
                    EXIT_STOP,
                    {"status": STATUS.blocked, "mutated": True},
                )
            },
            None,
            "rejection_mutated",
        ),
        (
            {
                QUALIFY.REJECTION_STEP: (
                    EXIT_OK,
                    {"status": STATUS.passed, "mutated": False},
                )
            },
            None,
            f"{QUALIFY.REJECTION_STEP}_unexpected:{EXIT_OK}:{STATUS.passed}",
        ),
    ),
    ids=(
        "source-id-differs",
        "destination-already-present",
        "mirror-already-present",
        "plan-without-target-source",
        "provider-target-source-differs",
        "provider-source-differs",
        "seed-ref-baseline-differs",
        "rejection-mutated",
        "rejection-not-blocked",
    ),
)
def test_qualification_stops_before_any_write(
    inputs: object,
    overrides: Optional[dict[str, tuple[int, dict[str, object]]]],
    change: Optional[Callable[[FakeProvider], None]],
    reason: str,
) -> None:
    """Stop before the ledger and the apply when the fixture or baseline differs.

    :param inputs: Synthetic fixture inputs.
    :param overrides: Step results that break one expectation.
    :param change: Provider state that breaks one expectation.
    :param reason: Exact stopping reason.
    :returns: None; assertions verify the reason, no apply, no ledger, no delete.
    """
    provider = FakeProvider()
    if change is not None:
        change(provider)
    runner = ScriptedRunner(_results(overrides))

    with pytest.raises(QUALIFY.QualificationError) as raised:
        _qualification(inputs, runner, provider).run()

    assert str(raised.value) == reason
    assert QUALIFY.APPLIED_STEP not in runner.labels
    assert not (inputs.work_dir / QUALIFY.ALLOCATION_FILE).exists()
    assert provider.deletes == []


@pytest.mark.parametrize(
    ("overrides", "reason", "state", "created"),
    (
        (
            {
                QUALIFY.APPLIED_STEP: (
                    EXIT_STOP,
                    {"status": STATUS.blocked},
                )
            },
            f"{QUALIFY.APPLIED_STEP}_unexpected:{EXIT_STOP}:{STATUS.blocked}",
            QUALIFY.APPLY_PENDING,
            None,
        ),
        (
            {
                QUALIFY.APPLIED_STEP: _applied(
                    _created(created={"mirror": True, "repository": False})
                )
            },
            "applied_result_unexpected",
            QUALIFY.CLEANUP_PENDING,
            {"mirror": True, "repository": False},
        ),
        (
            {
                QUALIFY.NOOP_STEP: (
                    EXIT_OK,
                    {
                        "status": STATUS.passed,
                        "mutated": False,
                        "records": [_created()],
                    },
                )
            },
            "noop_result_unexpected",
            QUALIFY.CLEANUP_PENDING,
            {"mirror": True, "repository": True},
        ),
    ),
    ids=("apply-blocked", "repository-adopted-not-created", "repeat-not-no-op"),
)
def test_apply_outcome_is_recorded_before_the_run_stops(
    inputs: object,
    overrides: dict[str, tuple[int, dict[str, object]]],
    reason: str,
    state: str,
    created: Optional[dict[str, bool]],
) -> None:
    """Record the created IDs the apply reported, then stop without cleanup.

    :param inputs: Synthetic fixture inputs.
    :param overrides: Step results that break one expectation.
    :param reason: Exact stopping reason.
    :param state: Ledger cleanup state left for reconcile.
    :param created: Ledger ownership flags.
    :returns: None; assertions verify the reason, ledger and no deletion.
    """
    provider = FakeProvider()

    with pytest.raises(QUALIFY.QualificationError, match=f"^{reason}$"):
        _qualification(inputs, ScriptedRunner(_results(overrides)), provider).run()

    ledger = _ledger(inputs)
    assert ledger["cleanup"] == state
    assert ledger["created"] == created
    assert provider.deletes == []
    assert provider.labels == [QUALIFY.BASELINE_PLAN_STEP]


@pytest.mark.parametrize(
    ("change", "reason", "deletes", "state"),
    (
        (
            lambda provider: setattr(provider, "keep_mirror", True),
            "mirror_deletion_unverified",
            [MIRROR_DELETED],
            QUALIFY.MIRROR_DELETE_PENDING,
        ),
        (
            lambda provider: setattr(provider, "mirror_delete_status", 500),
            "mirror_deletion_unverified",
            [MIRROR_DELETED],
            QUALIFY.MIRROR_DELETE_PENDING,
        ),
        (
            lambda provider: setattr(
                provider, "destination_override", {**DESTINATION, "id": 9002}
            ),
            QUALIFY.DESTINATION_IDENTITY_CHANGED,
            [],
            QUALIFY.CLEANUP_PENDING,
        ),
        (
            lambda provider: setattr(provider, "mirror_matches", False),
            QUALIFY.MIRROR_IDENTITY_CHANGED,
            [],
            QUALIFY.CLEANUP_PENDING,
        ),
        (
            lambda provider: setattr(provider, "mirror_present", False),
            QUALIFY.MIRROR_IDENTITY_CHANGED,
            [],
            QUALIFY.CLEANUP_PENDING,
        ),
        (
            lambda provider: setattr(
                provider,
                "destination_after_mirror_delete",
                {**DESTINATION, "id": 9002},
            ),
            QUALIFY.DESTINATION_IDENTITY_CHANGED,
            [MIRROR_DELETED],
            QUALIFY.DESTINATION_DELETE_PENDING,
        ),
        (
            lambda provider: setattr(provider, "vanish_after_mirror_delete", True),
            QUALIFY.DESTINATION_IDENTITY_CHANGED,
            [MIRROR_DELETED],
            QUALIFY.DESTINATION_DELETE_PENDING,
        ),
        (
            lambda provider: provider.target_for_label.update(
                {QUALIFY.CLEANUP_PLAN_STEP: OTHER_TARGET_SOURCE}
            ),
            "target_source_mismatch",
            [],
            QUALIFY.CLEANUP_PENDING,
        ),
    ),
    ids=(
        "mirror-still-present",
        "mirror-delete-not-204",
        "destination-identity-changed",
        "mirror-targets-another-url",
        "mirror-already-absent",
        "destination-changed-after-mirror-delete",
        "destination-vanished-after-mirror-delete",
        "cleanup-target-source-differs",
    ),
)
def test_cleanup_stops_when_an_identity_or_deletion_is_unproven(
    inputs: object,
    change: Callable[[FakeProvider], None],
    reason: str,
    deletes: list[str],
    state: str,
) -> None:
    """Never delete the destination unless the mirror is gone and identities match.

    :param inputs: Synthetic fixture inputs.
    :param change: Provider state that breaks one cleanup invariant.
    :param reason: Exact stopping reason.
    :param deletes: Deletions expected before the stop.
    :param state: Ledger cleanup state left for reconcile.
    :returns: None; assertions verify the reason, the deletions and the ledger.
    """
    provider = FakeProvider()
    change(provider)

    with pytest.raises(QUALIFY.QualificationError, match=f"^{reason}$"):
        _qualification(inputs, ScriptedRunner(_results()), provider).run()

    assert provider.deletes == deletes
    assert _ledger(inputs)["cleanup"] == state
    assert not (inputs.work_dir / QUALIFY.CLEANUP_RECORD).exists()


@pytest.mark.parametrize(
    ("change", "reason"),
    (
        (
            lambda provider: setattr(provider, "reappear", frozenset({"mirror"})),
            "mirror_not_absent",
        ),
        (
            lambda provider: setattr(provider, "reappear", frozenset({"destination"})),
            "destination_not_absent",
        ),
        (
            lambda provider: setattr(
                provider, "reappear", frozenset({"destination_id"})
            ),
            "destination_id_not_absent",
        ),
        (
            lambda provider: setattr(
                provider, "moved_after_delete", frozenset({MAIN_REF.key})
            ),
            f"{QUALIFY.SEED_REF_MOVED}:{MAIN_REF.key}",
        ),
    ),
    ids=(
        "mirror-not-absent",
        "destination-not-absent",
        "destination-id-not-absent",
        "seed-ref-moved",
    ),
)
def test_absence_read_back_stops_on_a_present_object_or_moved_ref(
    inputs: object, change: Callable[[FakeProvider], None], reason: str
) -> None:
    """Stop after both deletes when the independent read-back is not clean.

    :param inputs: Synthetic fixture inputs.
    :param change: Provider state after the deletes.
    :param reason: Exact stopping reason.
    :returns: None; assertions verify the reason, ledger and no cleanup record.
    """
    provider = FakeProvider()
    change(provider)

    with pytest.raises(QUALIFY.QualificationError, match=f"^{reason}$"):
        _qualification(inputs, ScriptedRunner(_results()), provider).run()

    assert provider.deletes == [MIRROR_DELETED, DESTINATION_DELETED]
    assert _ledger(inputs)["cleanup"] == QUALIFY.VERIFY_PENDING
    assert not (inputs.work_dir / QUALIFY.CLEANUP_RECORD).exists()
    assert not (inputs.work_dir / QUALIFY.NOOP_WITH_CLEANUP).exists()


@pytest.mark.parametrize("mode", ("run", "plan"))
def test_existing_ledger_blocks_a_second_run(inputs: object, mode: str) -> None:
    """Refuse to start while an earlier run's ledger is unreconciled.

    :param inputs: Synthetic fixture inputs.
    :param mode: ``run`` or ``plan``.
    :returns: None; assertions verify no command ran.
    """
    (inputs.work_dir / QUALIFY.ALLOCATION_FILE).write_text("{}", encoding="utf-8")
    runner = ScriptedRunner(_results())
    qualification = _qualification(inputs, runner, FakeProvider())

    with pytest.raises(QUALIFY.QualificationError, match=QUALIFY.ALLOCATION_EXISTS):
        getattr(qualification, mode)()

    assert runner.calls == []


def test_ledger_is_created_exclusively(inputs: object) -> None:
    """Stop when another process creates the ledger before this run allocates.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify no apply and the other ledger is kept.
    """
    ledger = inputs.work_dir / QUALIFY.ALLOCATION_FILE
    runner = ScriptedRunner(
        _results(),
        hooks={
            QUALIFY.REJECTION_STEP: lambda: ledger.write_text("{}", encoding="utf-8")
        },
    )

    with pytest.raises(QUALIFY.QualificationError, match=QUALIFY.ALLOCATION_EXISTS):
        _qualification(inputs, runner, FakeProvider()).run()

    assert QUALIFY.APPLIED_STEP not in runner.labels
    assert ledger.read_text(encoding="utf-8") == "{}"
    assert not list(inputs.work_dir.glob(f"*{QUALIFY.STAGED_SUFFIX}"))


def test_ledger_write_failure_keeps_the_previous_ledger(
    inputs: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leave the last complete ledger in place when a replacement fails.

    :param inputs: Synthetic fixture inputs.
    :param monkeypatch: Makes ``os.replace`` fail.
    :returns: None; assertions verify the reason, the ledger and no staged file.
    """

    def refuse(*_args: object) -> None:
        """Fail like a full disk.

        :param *_args: Ignored.
        :raises OSError: Always.
        """
        raise OSError

    monkeypatch.setattr(QUALIFY.os, "replace", refuse)
    provider = FakeProvider()

    with pytest.raises(
        QUALIFY.QualificationError, match=f"^write_failed:{QUALIFY.ALLOCATION_FILE}$"
    ):
        _qualification(inputs, ScriptedRunner(_results()), provider).run()

    assert _ledger(inputs)["cleanup"] == QUALIFY.APPLY_PENDING
    assert _ledger(inputs)["created"] is None
    assert not list(inputs.work_dir.glob(f"*{QUALIFY.STAGED_SUFFIX}"))
    assert provider.deletes == []


def _interrupted(state: str) -> str:
    """Build the stopping reason of an interrupt with a ledger held.

    :param state: Ledger cleanup state when the interrupt arrived.
    :returns: Reason naming the state and ``--reconcile``.
    """
    return QUALIFY.INTERRUPTED.format(state=state, option=QUALIFY.RECONCILE_OPTION)


def test_interrupt_at_the_apply_records_the_ledger_and_deletes_nothing(
    inputs: object,
) -> None:
    """Stop at ``APPLY_PENDING``; reconcile then names the manual read-back.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify reason, ledger, no delete and reconcile.
    """
    runner = ScriptedRunner(_results(), hooks={QUALIFY.APPLIED_STEP: _interrupt})
    provider = FakeProvider()

    with pytest.raises(QUALIFY.QualificationError) as raised:
        _qualification(inputs, runner, provider).run()

    assert str(raised.value) == _interrupted(QUALIFY.APPLY_PENDING)
    assert runner.labels[-1] == QUALIFY.APPLIED_STEP
    assert provider.deletes == []
    ledger = _ledger(inputs)
    assert ledger["cleanup"] == QUALIFY.APPLY_PENDING
    assert ledger["created"] is None
    assert not list(inputs.work_dir.glob(f"*{QUALIFY.STAGED_SUFFIX}"))
    reconciler = FakeProvider()

    with pytest.raises(QUALIFY.QualificationError) as stopped:
        _qualification(inputs, ScriptedRunner(_results()), reconciler).reconcile()

    assert str(stopped.value) == _manual_read_back()
    assert reconciler.labels == []
    assert reconciler.deletes == []
    assert _ledger(inputs) == ledger


@pytest.mark.parametrize(
    ("point", "reconciler", "run_deletes", "reconcile_deletes", "mirror_status"),
    (
        (
            BEFORE_DELETE,
            FakeProvider,
            [],
            [MIRROR_DELETED, DESTINATION_DELETED],
            204,
        ),
        (
            AFTER_DELETE,
            lambda: FakeProvider(mirror=False),
            [MIRROR_DELETED],
            [DESTINATION_DELETED],
            404,
        ),
    ),
    ids=("before-mirror-delete", "after-mirror-delete"),
)
def test_interrupt_at_the_mirror_delete_leaves_the_ledger_for_reconcile(
    inputs: object,
    point: str,
    reconciler: Callable[[], FakeProvider],
    run_deletes: list[str],
    reconcile_deletes: list[str],
    mirror_status: int,
) -> None:
    """Stop at ``MIRROR_DELETE_PENDING`` without the destination delete.

    :param inputs: Synthetic fixture inputs.
    :param point: Whether the interrupt precedes or follows the mirror delete.
    :param reconciler: Provider state at reconcile time.
    :param run_deletes: Deletions the interrupted run made.
    :param reconcile_deletes: Deletions reconcile makes.
    :param mirror_status: Mirror delete status reconcile records.
    :returns: None; assertions verify reason, ledger, deletions and the record.
    """
    provider = FakeProvider()
    provider.interrupt_mirror_delete = point

    with pytest.raises(QUALIFY.QualificationError) as raised:
        _qualification(inputs, ScriptedRunner(_results()), provider).run()

    assert str(raised.value) == _interrupted(QUALIFY.MIRROR_DELETE_PENDING)
    assert provider.deletes == run_deletes
    ledger = _ledger(inputs)
    assert ledger["cleanup"] == QUALIFY.MIRROR_DELETE_PENDING
    assert ledger["created"] == {"mirror": True, "repository": True}
    assert not (inputs.work_dir / QUALIFY.CLEANUP_RECORD).exists()
    assert not (inputs.work_dir / QUALIFY.NOOP_WITH_CLEANUP).exists()
    fresh = reconciler()

    reconciled = _qualification(inputs, ScriptedRunner(_results()), fresh).reconcile()

    assert fresh.deletes == reconcile_deletes
    assert reconciled["cleanup"] == QUALIFY.CLEANUP_PASS
    record = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert record[QUALIFY.DELETE_FIELD] == {
        QUALIFY.MIRROR_HTTP_STATUS_FIELD: mirror_status,
        QUALIFY.DESTINATION_HTTP_STATUS_FIELD: 204,
    }


def test_interrupt_before_the_ledger_writes_nothing(inputs: object) -> None:
    """Stop a dry run without a ledger to write.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify the reason and no ledger.
    """
    runner = ScriptedRunner(_results(), hooks={QUALIFY.REJECTION_STEP: _interrupt})

    with pytest.raises(
        QUALIFY.QualificationError, match=f"^{QUALIFY.INTERRUPTED_WITHOUT_LEDGER}$"
    ):
        _qualification(inputs, runner, FakeProvider()).plan()

    assert not (inputs.work_dir / QUALIFY.ALLOCATION_FILE).exists()


def test_sigterm_inside_the_guard_raises_keyboard_interrupt() -> None:
    """Turn SIGTERM into ``KeyboardInterrupt`` and restore the previous handler.

    :returns: None; assertions verify the raised interrupt and the handler.
    """
    previous = signal.getsignal(signal.SIGTERM)

    with pytest.raises(KeyboardInterrupt), QUALIFY.terminate_as_interrupt():
        signal.raise_signal(signal.SIGTERM)

    assert signal.getsignal(signal.SIGTERM) == previous


def _fake_exit_code(token: str) -> int:
    """Map the fake installed tokens to exit codes other than the repository's.

    :param token: Fake status token.
    :returns: ``FAKE_EXIT_OK`` for pass or planned, otherwise ``FAKE_EXIT_STOP``.
    """
    return FAKE_EXIT_OK if token in {FAKE_PASS, FAKE_PLANNED} else FAKE_EXIT_STOP


def test_bound_status_tokens_drive_every_comparison(
    inputs: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compare the command output with the installed copy's tokens, not copies.

    :param inputs: Synthetic fixture inputs.
    :param monkeypatch: Serves a fake installed status module and mirror outcomes.
    :returns: None; assertions verify the fake tokens pass and the real ones stop.
    """
    loader = QUALIFY._installed_module
    fakes = {
        QUALIFY.STATUS_MODULE: SimpleNamespace(
            PASS=FAKE_PASS,
            PLANNED=FAKE_PLANNED,
            BLOCKED=FAKE_BLOCKED,
            exit_code=_fake_exit_code,
        ),
        QUALIFY.MIRROR_CONSTANTS_MODULE: SimpleNamespace(
            Outcome=SimpleNamespace(APPLIED=FAKE_APPLIED, NO_OP=FAKE_NO_OP)
        ),
    }
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(
        QUALIFY,
        "_installed_module",
        lambda skill, name: fakes[name] if name in fakes else loader(skill, name),
    )
    tokens = QUALIFY.installed_bindings(SKILL_ROOT).status

    with pytest.raises(
        QUALIFY.QualificationError,
        match=f"^{QUALIFY.LIVE_PLAN_STEP}_unexpected:{EXIT_OK}:{STATUS.planned}$",
    ):
        _qualification(
            inputs, ScriptedRunner(_results()), FakeProvider(), tokens
        ).plan()
    ledger = _qualification(
        inputs, ScriptedRunner(_results(tokens=tokens)), FakeProvider(), tokens
    ).run()

    assert ledger["cleanup"] == QUALIFY.CLEANUP_PASS
    record = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert record[QUALIFY.STATUS_FIELD] == FAKE_PASS


def test_receipt_from_another_candidate_is_rejected(inputs: object) -> None:
    """Bind the receipts to the installed copy's verified revision.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify no cleanup ran.
    """
    results = _results()
    code, noop = results[QUALIFY.NOOP_STEP]
    results[QUALIFY.NOOP_STEP] = (code, {**noop, "tested_revision": "d" * 40})
    provider = FakeProvider()

    with pytest.raises(QUALIFY.QualificationError, match="receipt_candidate_mismatch"):
        _qualification(inputs, ScriptedRunner(results), provider).run()

    assert provider.deletes == []


@pytest.mark.parametrize(
    "text", ("branches:main", "commits:main:abc", "tags::abc", "branches:main:")
)
def test_seed_ref_declaration_is_validated(text: str) -> None:
    """Accept only ``KIND:NAME:COMMIT`` with a known kind and no empty part.

    :param text: Malformed declaration.
    :returns: None; assertions verify rejection.
    """
    with pytest.raises(QUALIFY.QualificationError, match="seed_ref_invalid"):
        QUALIFY.SeedRef.parse(text)


def test_command_selection_carries_the_gitlab_selector(inputs: object) -> None:
    """Pass the installed GitLab option through when a selector is given.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify the fixed selection.
    """
    selected = dataclasses.replace(inputs, gitlab=ENDPOINTS.GITLAB_PRIVATE_SELECTOR)

    assert inputs.command_selection()[:5] == (
        "sync",
        "--project",
        str(SOURCE["path"]),
        "--destination",
        DESTINATION_PATH,
    )
    assert CATALOG.GITLAB_TARGET_OPTION not in inputs.command_selection()
    assert selected.command_selection()[-2:] == (
        CATALOG.GITLAB_TARGET_OPTION,
        ENDPOINTS.GITLAB_PRIVATE_SELECTOR,
    )


def _failed_run(inputs: object, change: Callable[[FakeProvider], None]) -> None:
    """Leave a pending ledger from a run that stopped during cleanup.

    :param inputs: Synthetic fixture inputs.
    :param change: Provider state that stops the run.
    """
    provider = FakeProvider()
    change(provider)
    _run(inputs, provider)


def test_reconcile_without_a_ledger_is_blocked(inputs: object) -> None:
    """Refuse to reconcile when no ledger exists.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify no provider was bound.
    """
    provider = FakeProvider()

    with pytest.raises(
        QUALIFY.QualificationError, match=f"^{QUALIFY.NOTHING_TO_RECONCILE}$"
    ):
        _qualification(inputs, ScriptedRunner(_results()), provider).reconcile()

    assert provider.labels == []


def test_reconcile_of_a_completed_ledger_is_blocked(inputs: object) -> None:
    """Refuse to reconcile a ledger whose cleanup already passed.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify nothing else was deleted.
    """
    provider = FakeProvider()
    _qualification(inputs, ScriptedRunner(_results()), provider).run()
    reconciler = FakeProvider()

    with pytest.raises(
        QUALIFY.QualificationError, match=f"^{QUALIFY.NOTHING_TO_RECONCILE}$"
    ):
        _qualification(inputs, ScriptedRunner(_results()), reconciler).reconcile()

    assert reconciler.labels == []
    assert reconciler.deletes == []


def test_reconcile_removes_the_recorded_ids_after_a_failed_cleanup(
    inputs: object,
) -> None:
    """Finish a cleanup stopped at the mirror delete, then repeat the read-back.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify deletions, ledger and labelled record.
    """
    _failed_run(inputs, lambda provider: setattr(provider, "keep_mirror", True))
    runner = ScriptedRunner(_results())
    provider = FakeProvider()

    ledger = _qualification(inputs, runner, provider).reconcile()

    assert runner.calls == []
    assert provider.labels == [QUALIFY.RECONCILE_PLAN_STEP]
    assert provider.deletes == [MIRROR_DELETED, DESTINATION_DELETED]
    assert ledger["cleanup"] == QUALIFY.CLEANUP_PASS
    assert ledger["reconciled"] is True
    record = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert record[QUALIFY.RECONCILED_FIELD] is True
    assert record[QUALIFY.TESTED_REVISION_FIELD] == CANDIDATE
    assert record[QUALIFY.DELETE_FIELD] == {
        QUALIFY.MIRROR_HTTP_STATUS_FIELD: 204,
        QUALIFY.DESTINATION_HTTP_STATUS_FIELD: 204,
    }
    assert record[QUALIFY.REPEAT_FIELD][QUALIFY.MUTATED_FIELD] is False


def test_reconcile_deletes_only_the_objects_the_run_created(inputs: object) -> None:
    """Leave an adopted repository in place and remove only the created mirror.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify one deletion and no destination read-back.
    """
    _run(
        inputs,
        FakeProvider(),
        {
            QUALIFY.APPLIED_STEP: _applied(
                _created(created={"mirror": True, "repository": False})
            )
        },
    )
    provider = FakeProvider()

    _qualification(inputs, ScriptedRunner(_results()), provider).reconcile()

    assert provider.deletes == [MIRROR_DELETED]
    assert provider.destination_present is True
    record = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert record[QUALIFY.DELETE_FIELD] == {
        QUALIFY.MIRROR_HTTP_STATUS_FIELD: 204,
        QUALIFY.DESTINATION_HTTP_STATUS_FIELD: None,
    }
    assert record[QUALIFY.AFTER_FIELD][QUALIFY.DESTINATION_HTTP_STATUS_FIELD] is None


def test_reconcile_does_not_delete_objects_already_absent(inputs: object) -> None:
    """Prove absence again without another delete when both objects are gone.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify no deletion and 404 statuses.
    """
    _failed_run(
        inputs,
        lambda provider: setattr(provider, "reappear", frozenset({"destination_id"})),
    )
    provider = FakeProvider(mirror=False, destination=False)

    ledger = _qualification(inputs, ScriptedRunner(_results()), provider).reconcile()

    assert provider.deletes == []
    assert ledger["cleanup"] == QUALIFY.CLEANUP_PASS
    record = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert record[QUALIFY.DELETE_FIELD] == {
        QUALIFY.MIRROR_HTTP_STATUS_FIELD: 404,
        QUALIFY.DESTINATION_HTTP_STATUS_FIELD: 404,
    }


def _manual_read_back() -> str:
    """Build the stopping reason of a ledger without created IDs.

    :returns: ``ledger_ids_missing`` naming the destination and the source mirrors.
    """
    return QUALIFY.MANUAL_READ_BACK.format(
        reason=QUALIFY.LEDGER_IDS_MISSING,
        destination=QUALIFY.EVIDENCE_LOCATION.format(
            host=DESTINATION["host"], path=DESTINATION_PATH
        ),
        source=QUALIFY.EVIDENCE_LOCATION.format(host=SOURCE_HOST, path=SOURCE["path"]),
    )


def test_reconcile_without_recorded_ids_is_blocked(inputs: object) -> None:
    """Refuse to guess IDs and name the manual read-back instead.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify the reason, no provider and the ledger stays.
    """
    _run(
        inputs,
        FakeProvider(),
        {
            QUALIFY.APPLIED_STEP: (
                EXIT_STOP,
                {"status": STATUS.blocked},
            )
        },
    )
    provider = FakeProvider()

    with pytest.raises(QUALIFY.QualificationError) as raised:
        _qualification(inputs, ScriptedRunner(_results()), provider).reconcile()

    assert str(raised.value) == _manual_read_back()
    assert provider.labels == []
    assert provider.deletes == []
    assert _ledger(inputs)["cleanup"] == QUALIFY.APPLY_PENDING


def test_reconcile_record_is_written_only_to_the_cleanup_file(inputs: object) -> None:
    """Keep the ``reconciled`` record out of every NO_OP receipt.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify the receipts are unchanged and the record.
    """
    _failed_run(inputs, lambda provider: setattr(provider, "keep_mirror", True))
    noop = (inputs.work_dir / QUALIFY.NOOP_RECEIPT).read_bytes()

    _qualification(inputs, ScriptedRunner(_results()), FakeProvider()).reconcile()

    assert (inputs.work_dir / QUALIFY.NOOP_RECEIPT).read_bytes() == noop
    assert QUALIFY.SMOKE_CLEANUP_FIELD not in json.loads(noop)
    assert not (inputs.work_dir / QUALIFY.NOOP_WITH_CLEANUP).exists()
    record = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert record[QUALIFY.RECONCILED_FIELD] is True


@pytest.mark.parametrize(
    ("change", "inputs_change", "reason", "deletes"),
    (
        (
            None,
            {"source_id": SOURCE_ID + 1},
            "ledger_fixture_mismatch",
            [],
        ),
        (
            None,
            {"destination": "unit/other-destination"},
            "ledger_fixture_mismatch",
            [],
        ),
        (
            lambda provider: setattr(provider, "target", OTHER_TARGET_SOURCE),
            None,
            "target_source_mismatch",
            [],
        ),
        (
            lambda provider: setattr(
                provider, "destination_override", {**DESTINATION, "id": 9002}
            ),
            None,
            QUALIFY.DESTINATION_IDENTITY_CHANGED,
            [MIRROR_DELETED],
        ),
        (
            lambda provider: (
                setattr(provider, "destination_present", False),
                setattr(provider, "id_elsewhere", True),
            ),
            None,
            QUALIFY.DESTINATION_IDENTITY_CHANGED,
            [MIRROR_DELETED],
        ),
        (
            lambda provider: setattr(provider, "mirror_matches", False),
            None,
            QUALIFY.MIRROR_IDENTITY_CHANGED,
            [],
        ),
    ),
    ids=(
        "other-source-id",
        "other-destination-path",
        "other-target-source",
        "destination-has-another-id",
        "destination-id-moved-to-another-path",
        "mirror-targets-another-url",
    ),
)
def test_reconcile_stops_when_ownership_is_unproven(
    inputs: object,
    change: Optional[Callable[[FakeProvider], None]],
    inputs_change: Optional[dict[str, object]],
    reason: str,
    deletes: list[str],
) -> None:
    """Delete nothing whose recorded identity no longer matches.

    :param inputs: Synthetic fixture inputs.
    :param change: Provider state at reconcile time.
    :param inputs_change: Inputs that differ from the ledger.
    :param reason: Exact stopping reason.
    :param deletes: Deletions expected before the stop.
    :returns: None; assertions verify the reason, deletions and pending ledger.
    """
    _failed_run(inputs, lambda provider: setattr(provider, "keep_mirror", True))
    provider = FakeProvider()
    if change is not None:
        change(provider)
    selected = dataclasses.replace(inputs, **(inputs_change or {}))

    with pytest.raises(QUALIFY.QualificationError, match=f"^{reason}$"):
        _qualification(selected, ScriptedRunner(_results()), provider).reconcile()

    assert provider.deletes == deletes
    assert _ledger(inputs)["cleanup"] != QUALIFY.CLEANUP_PASS


def _completed(
    command: Sequence[str], *, stdout: object, **_: object
) -> subprocess.CompletedProcess[str]:
    """Write a live-plan result and exit 0, as the installed command does.

    :param command: Process arguments.
    :param stdout: Open stdout file.
    :param **_: Other ``subprocess.run`` arguments.
    :returns: Completed process with exit code 0.
    """
    stdout.write(json.dumps(_planned()))
    return subprocess.CompletedProcess(command, EXIT_OK)


def test_installed_command_runs_in_the_caller_directory(inputs: object) -> None:
    """Run the installed script with the bounded timeout and no ``cwd`` override.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify the process arguments and result.
    """
    seen: list[tuple[Sequence[str], dict[str, object]]] = []

    def execute(command: Sequence[str], **kwargs: object) -> object:
        """Record the call and complete it.

        :param command: Process arguments.
        :param **kwargs: ``subprocess.run`` arguments.
        :returns: Completed process.
        """
        seen.append((command, kwargs))
        return _completed(command, **kwargs)

    code, data = QUALIFY.InstalledMirrorCommand(inputs, execute).run(
        QUALIFY.LIVE_PLAN_STEP, [QUALIFY.LIVE_PLAN_OPTION]
    )

    command, kwargs = seen[0]
    assert "cwd" not in kwargs
    assert kwargs["timeout"] == inputs.timeout + QUALIFY.COMMAND_TIMEOUT_MARGIN_SECONDS
    assert command[1:] == [
        str(inputs.skill / QUALIFY.MIRROR_SCRIPT),
        *inputs.command_selection(),
        QUALIFY.LIVE_PLAN_OPTION,
    ]
    assert code == EXIT_OK
    assert data == _planned()


def _raise(error: BaseException) -> Callable[..., object]:
    """Build a process runner that raises.

    :param error: Exception to raise.
    :returns: Runner with the ``subprocess.run`` signature.
    """

    def execute(*_args: object, **_kwargs: object) -> object:
        """Raise the configured error.

        :param *_args: Ignored.
        :param **_kwargs: Ignored.
        :raises BaseException: Always.
        """
        raise error

    return execute


def _writes(text: str) -> Callable[..., object]:
    """Build a process runner that writes fixed stdout and exits 0.

    :param text: Stdout content.
    :returns: Runner with the ``subprocess.run`` signature.
    """

    def execute(command: Sequence[str], *, stdout: object, **_kwargs: object) -> object:
        """Write the content and complete.

        :param command: Process arguments.
        :param stdout: Open stdout file.
        :param **_kwargs: Ignored.
        :returns: Completed process.
        """
        stdout.write(text)
        return subprocess.CompletedProcess(command, EXIT_OK)

    return execute


@pytest.mark.parametrize(
    ("execute", "reason"),
    (
        (
            _raise(subprocess.TimeoutExpired(["unit"], 1)),
            f"{QUALIFY.LIVE_PLAN_STEP}_timeout",
        ),
        (
            _raise(FileNotFoundError()),
            f"{QUALIFY.LIVE_PLAN_STEP}_command_failed:FileNotFoundError",
        ),
        (_writes("not json"), f"{QUALIFY.LIVE_PLAN_STEP}_output_not_json"),
        (_writes("[]"), f"{QUALIFY.LIVE_PLAN_STEP}_output_not_object"),
    ),
    ids=("timeout", "os-error", "not-json", "not-object"),
)
def test_installed_command_failures_become_stopping_reasons(
    inputs: object, execute: Callable[..., object], reason: str
) -> None:
    """Convert process failures into ``QualificationError``.

    :param inputs: Synthetic fixture inputs.
    :param execute: Failing process runner.
    :param reason: Exact stopping reason.
    :returns: None; assertions verify the reason.
    """
    with pytest.raises(QUALIFY.QualificationError, match=f"^{reason}$"):
        QUALIFY.InstalledMirrorCommand(inputs, execute).run(
            QUALIFY.LIVE_PLAN_STEP, [QUALIFY.LIVE_PLAN_OPTION]
        )


class FakeGuard:
    """Stand in for the installed ``create_guard`` and record guard calls."""

    def __init__(self, error: Optional[Exception] = None) -> None:
        """Start with no recorded call.

        :param error: Error raised by ``require_ready``, if any.
        """
        self.args: tuple[object, ...] = ()
        self.calls: MutableSequence[str] = []
        self.error = error

    def __call__(self, *args: object) -> contextlib.AbstractContextManager[FakeGuard]:
        """Record the guard key and enter this guard.

        :param *args: Guard key.
        :returns: Context yielding this guard.
        """
        self.args = args
        return contextlib.nullcontext(self)

    def require_ready(self) -> None:
        """Record the readiness check.

        :raises Exception: The configured error.
        """  # noqa: DOC503 - The configured fixture exception is raised verbatim.
        self.calls.append("require_ready")
        if self.error is not None:
            raise self.error

    def mark_pending(self) -> None:
        """Record the pending mark."""
        self.calls.append("mark_pending")

    def clear(self) -> None:
        """Record the clear."""
        self.calls.append("clear")


class FakeApi:
    """Record provider requests and answer from a table."""

    def __init__(
        self,
        responses: Optional[Mapping[str, object]] = None,
        error: Optional[Exception] = None,
    ) -> None:
        """Keep the response table.

        :param responses: Response per endpoint.
        :param error: Error raised by every request, if any.
        """
        self.responses = responses or {}
        self.error = error
        self.calls: MutableSequence[tuple[str, str]] = []

    def get_json(self, session: object, endpoint: str) -> object:
        """Record a GET and answer it.

        :param session: Provider session.
        :param endpoint: Relative route.
        :returns: Table response.
        :raises Exception: The configured error.
        """  # noqa: DOC503 - The configured fixture exception is raised verbatim.
        self.calls.append(("GET", endpoint))
        if self.error is not None:
            raise self.error
        return self.responses[endpoint]

    def request_no_content(self, session: object, method: str, endpoint: str) -> None:
        """Record a no-content request.

        :param session: Provider session.
        :param method: HTTP method.
        :param endpoint: Relative route.
        :raises Exception: The configured error.
        """  # noqa: DOC503 - The configured fixture exception is raised verbatim.
        self.calls.append((str(method), endpoint))
        if self.error is not None:
            raise self.error


class FakeGitHubSession:
    """Answer GitHub reads by repository ID and record requests."""

    def __init__(self, api: FakeApi, present: bool = True) -> None:
        """Keep the transport and the ID read answer.

        :param api: Transport for no-content requests.
        :param present: Whether the ID read finds the repository.
        """
        self.api = api
        self.present = present
        self.calls: MutableSequence[tuple[str, str]] = []

    def request(self, method: str, endpoint: str) -> object:
        """Record a request and answer it.

        :param method: HTTP method.
        :param endpoint: Relative route.
        :returns: Repository object.
        :raises Exception: Provider not-found, or the transport's configured error.
        """  # noqa: DOC503 - The configured fixture exception is raised verbatim.
        self.calls.append((str(method), endpoint))
        if self.api.error is not None:
            raise self.api.error
        if not self.present:
            raise CLI_API.APIError(REPOSITORY.NOT_FOUND)
        return {"id": REPOSITORY_ID}


class FakeMirrorSource:
    """Stand in for the installed ``GitLabPushMirrorService``."""

    def __init__(
        self,
        api: FakeApi,
        mirror_url: Optional[str] = CLONE_URL,
        error: Optional[Exception] = None,
    ) -> None:
        """Keep the mirror state.

        :param api: Source transport.
        :param mirror_url: URL of the mirror, or None when absent.
        :param error: Error raised by source reads, if any.
        """
        self.service = SimpleNamespace(
            api=api, session=SimpleNamespace(host=SOURCE_HOST), target_id=SOURCE_ID
        )
        self.mirror_url = mirror_url
        self.error = error

    def repository(self, reference: str) -> object:
        """Return the source repository.

        :param reference: Source path.
        :returns: Repository with evidence.
        :raises Exception: The configured error.
        """  # noqa: DOC503 - The configured fixture exception is raised verbatim.
        if self.error is not None:
            raise self.error
        return SimpleNamespace(evidence=lambda: dict(SOURCE))

    def mirror(self, mirror_id: int) -> object:
        """Return the mirror or raise provider not-found.

        :param mirror_id: Native mirror ID.
        :returns: Mirror with its URL.
        :raises Exception: Provider not-found, or the configured error.
        """  # noqa: DOC503 - The configured fixture exception is raised verbatim.
        if self.error is not None:
            raise self.error
        if self.mirror_url is None:
            raise CLI_API.APIError(MIRROR_PROVIDER.MIRROR_NOT_FOUND)
        return SimpleNamespace(id=mirror_id, url=self.mirror_url)


class FakeRepositoryService:
    """Stand in for the installed ``GitHubRepositoryService``."""

    def __init__(
        self,
        session: FakeGitHubSession,
        repository_id: Optional[int] = REPOSITORY_ID,
        error: Optional[Exception] = None,
    ) -> None:
        """Keep the repository state.

        :param session: GitHub session.
        :param repository_id: Repository ID, or None when absent.
        :param error: Error raised by repository reads, if any.
        """
        self.session = session
        self.repository_id = repository_id
        self.error = error

    def repository(self, reference: str) -> Optional[object]:
        """Return the repository by path.

        :param reference: Repository path.
        :returns: Repository, or None when absent.
        :raises Exception: The configured error.
        """  # noqa: DOC503 - The configured fixture exception is raised verbatim.
        if self.error is not None:
            raise self.error
        if self.repository_id is None:
            return None
        return SimpleNamespace(
            id=self.repository_id,
            clone_url=CLONE_URL,
            evidence=lambda: {**DESTINATION, "id": self.repository_id},
        )


def _native(
    inputs: object,
    bindings: object,
    source: Optional[FakeMirrorSource] = None,
    destination: Optional[FakeRepositoryService] = None,
) -> object:
    """Build a native provider over fake installed services.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :param source: Fake source service.
    :param destination: Fake destination service.
    :returns: Native provider.
    """
    return QUALIFY.NativeMirrorProvider(
        inputs,
        bindings,
        source or FakeMirrorSource(FakeApi()),
        destination or FakeRepositoryService(FakeGitHubSession(FakeApi())),
        TARGET_SOURCE,
    )


def test_installed_bindings_read_the_installed_lib(bindings: object) -> None:
    """Bind the installed constants instead of copies of their values.

    :param bindings: Names bound from ``ci-skills/lib``.
    :returns: None; assertions verify each bound name.
    """
    assert bindings.gitlab_option == CATALOG.GITLAB_TARGET_OPTION
    assert bindings.api_error is CLI_API.APIError
    assert bindings.mirror_endpoint == MIRROR_PROVIDER.MIRROR_ENDPOINT
    assert bindings.mirror_not_found == MIRROR_PROVIDER.MIRROR_NOT_FOUND
    assert bindings.project_scope == MIRROR_PROVIDER.PROJECT_SCOPE
    assert bindings.mirror_resource == MIRROR_PROVIDER.MIRROR_RESOURCE
    assert bindings.repository_endpoint == REPOSITORY.REPOSITORY_ENDPOINT
    assert bindings.repository_not_found == REPOSITORY.NOT_FOUND
    assert bindings.path_separator == REPOSITORY.PATH_SEPARATOR
    assert bindings.status == STATUS


def test_installed_bindings_refuse_a_lib_from_another_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop when ``lib`` was already imported from outside the installed copy.

    :param tmp_path: Installed directory without the imported ``lib``.
    :param monkeypatch: Restores ``sys.path``.
    :returns: None; assertions verify the reason.
    """
    monkeypatch.setattr(sys, "path", list(sys.path))

    with pytest.raises(
        QUALIFY.QualificationError,
        match=f"^installed_lib_shadowed:{QUALIFY.CATALOG_MODULE}$",
    ):
        QUALIFY.installed_bindings(tmp_path)


def test_installed_bindings_refuse_a_lib_without_a_bound_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop with the missing name when an older installed copy lacks a constant.

    :param tmp_path: Installed directory.
    :param monkeypatch: Replaces the module loader and restores ``sys.path``.
    :returns: None; assertions verify the reason.
    """
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(
        QUALIFY, "_installed_module", lambda skill, name: SimpleNamespace()
    )

    with pytest.raises(
        QUALIFY.QualificationError,
        match="^installed_lib_incompatible:GITLAB_TARGET_OPTION$",
    ):
        QUALIFY.installed_bindings(tmp_path)


def test_native_provider_reads_through_the_installed_routes(
    inputs: object, bindings: object
) -> None:
    """Read refs, mirrors and repositories with the installed routes and reasons.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :returns: None; assertions verify routes, URL comparison and not-found.
    """
    ref_route = QUALIFY.REF_ENDPOINT.format(
        project=SOURCE_ID, kind=BRANCHES, name="release%2F1.0"
    )
    api = FakeApi({ref_route: {"commit": {"id": SEED}}})
    session = FakeGitHubSession(FakeApi())
    provider = _native(
        inputs,
        bindings,
        FakeMirrorSource(api, mirror_url=CLONE_URL.replace("unit", "Unit")),
        FakeRepositoryService(session),
    )

    assert provider.target_source() == TARGET_SOURCE
    assert provider.source() == SOURCE
    assert provider.ref_commit(QUALIFY.SeedRef(BRANCHES, "release/1.0", SEED)) == SEED
    assert api.calls == [("GET", ref_route)]
    assert provider.mirror_targets(MIRROR_ID, DESTINATION_PATH) is True
    assert provider.destination(DESTINATION_PATH) == DESTINATION
    assert provider.destination_id_present(REPOSITORY_ID) is True
    assert session.calls == [
        ("GET", QUALIFY.REPOSITORY_ID_ENDPOINT.format(repository_id=REPOSITORY_ID))
    ]
    other = _native(inputs, bindings, FakeMirrorSource(api, mirror_url=CLONE_URL + "x"))
    assert other.mirror_targets(MIRROR_ID, DESTINATION_PATH) is False
    absent = _native(
        inputs,
        bindings,
        FakeMirrorSource(api, mirror_url=None),
        FakeRepositoryService(FakeGitHubSession(FakeApi(), present=False), None),
    )
    assert absent.mirror_targets(MIRROR_ID, DESTINATION_PATH) is None
    assert absent.destination(DESTINATION_PATH) is None
    assert absent.destination_id_present(REPOSITORY_ID) is False


def test_native_provider_deletes_the_mirror_inside_the_installed_guard(
    inputs: object, bindings: object
) -> None:
    """Mark the guard pending, delete by mirror ID, then clear the guard.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :returns: None; assertions verify the guard key, calls and route.
    """
    guard = FakeGuard()
    api = FakeApi()
    provider = _native(
        inputs, dataclasses.replace(bindings, create_guard=guard), FakeMirrorSource(api)
    )

    status = provider.delete_mirror(MIRROR_ID, DESTINATION_PATH)

    assert status == 204
    assert guard.args == (
        SOURCE_HOST,
        MIRROR_PROVIDER.PROJECT_SCOPE,
        SOURCE_ID,
        MIRROR_PROVIDER.MIRROR_RESOURCE,
        CLONE_URL,
    )
    assert guard.calls == ["require_ready", "mark_pending", "clear"]
    assert api.calls == [
        (
            "DELETE",
            MIRROR_PROVIDER.MIRROR_ENDPOINT.format(project=SOURCE_ID, mirror=MIRROR_ID),
        )
    ]


def test_native_provider_keeps_a_mirror_that_targets_another_repository(
    inputs: object, bindings: object
) -> None:
    """Refuse the mirror delete when the mirror URL no longer matches.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :returns: None; assertions verify no pending mark and no delete.
    """
    guard = FakeGuard()
    api = FakeApi()
    provider = _native(
        inputs,
        dataclasses.replace(bindings, create_guard=guard),
        FakeMirrorSource(api, mirror_url=CLONE_URL + "x"),
    )

    with pytest.raises(
        QUALIFY.QualificationError, match=f"^{QUALIFY.MIRROR_IDENTITY_CHANGED}$"
    ):
        provider.delete_mirror(MIRROR_ID, DESTINATION_PATH)

    assert guard.calls == ["require_ready"]
    assert api.calls == []


def test_native_provider_deletes_the_destination_after_an_id_match(
    inputs: object, bindings: object
) -> None:
    """Re-read the repository, match its ID, then delete it by path.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :returns: None; assertions verify the delete route.
    """
    api = FakeApi()
    provider = _native(
        inputs,
        bindings,
        destination=FakeRepositoryService(FakeGitHubSession(api)),
    )

    status = provider.delete_destination(DESTINATION_PATH, REPOSITORY_ID)

    assert status == 204
    assert api.calls == [
        ("DELETE", REPOSITORY.REPOSITORY_ENDPOINT.format(reference=DESTINATION_PATH))
    ]


@pytest.mark.parametrize("observed", (REPOSITORY_ID + 1, None), ids=("other", "gone"))
def test_native_provider_keeps_a_destination_with_another_id(
    inputs: object, bindings: object, observed: Optional[int]
) -> None:
    """Refuse the destination delete when the path holds another or no repository.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :param observed: Repository ID found at the path, or None.
    :returns: None; assertions verify no delete.
    """
    api = FakeApi()
    provider = _native(
        inputs,
        bindings,
        destination=FakeRepositoryService(FakeGitHubSession(api), observed),
    )

    with pytest.raises(
        QUALIFY.QualificationError,
        match=f"^{QUALIFY.DESTINATION_IDENTITY_CHANGED}$",
    ):
        provider.delete_destination(DESTINATION_PATH, REPOSITORY_ID)

    assert api.calls == []


@pytest.mark.parametrize(
    ("build", "call", "reason"),
    (
        (
            lambda error: {"source": FakeMirrorSource(FakeApi(), error=error)},
            lambda provider: provider.source(),
            QUALIFY.SOURCE_READ,
        ),
        (
            lambda error: {"source": FakeMirrorSource(FakeApi(error=error))},
            lambda provider: provider.ref_commit(TAG_REF),
            QUALIFY.REF_READ,
        ),
        (
            lambda error: {"source": FakeMirrorSource(FakeApi(), error=error)},
            lambda provider: provider.mirror_targets(MIRROR_ID, DESTINATION_PATH),
            QUALIFY.MIRROR_READ,
        ),
        (
            lambda error: {
                "destination": FakeRepositoryService(
                    FakeGitHubSession(FakeApi()), error=error
                )
            },
            lambda provider: provider.destination(DESTINATION_PATH),
            QUALIFY.DESTINATION_READ,
        ),
        (
            lambda error: {
                "destination": FakeRepositoryService(
                    FakeGitHubSession(FakeApi(error=error))
                )
            },
            lambda provider: provider.destination_id_present(REPOSITORY_ID),
            QUALIFY.DESTINATION_READ,
        ),
        (
            lambda error: {
                "destination": FakeRepositoryService(
                    FakeGitHubSession(FakeApi(error=error))
                )
            },
            lambda provider: provider.delete_destination(
                DESTINATION_PATH, REPOSITORY_ID
            ),
            QUALIFY.DESTINATION_DELETE,
        ),
        (
            lambda error: {"source": FakeMirrorSource(FakeApi(error=error))},
            lambda provider: provider.delete_mirror(MIRROR_ID, DESTINATION_PATH),
            QUALIFY.MIRROR_DELETE,
        ),
    ),
    ids=(
        "source-read",
        "ref-read",
        "mirror-read",
        "destination-read",
        "destination-id-read",
        "destination-delete",
        "mirror-delete",
    ),
)
@pytest.mark.parametrize(
    ("error", "detail"),
    (
        (CLI_API.APIError(PROVIDER_REASON), PROVIDER_REASON),
        (OSError(), "OSError"),
    ),
    ids=("api-error", "os-error"),
)
def test_native_provider_converts_provider_errors(
    inputs: object,
    bindings: object,
    build: Callable[[Exception], dict[str, object]],
    call: Callable[[object], object],
    reason: str,
    error: Exception,
    detail: str,
) -> None:
    """Turn installed ``APIError`` and ``OSError`` into a stopping reason.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :param build: Fake services raising ``error``.
    :param call: Provider method under test.
    :param reason: Provider step named in the reason.
    :param error: Raised provider error.
    :param detail: Reason detail.
    :returns: None; assertions verify the reason.
    """
    provider = _native(
        inputs, dataclasses.replace(bindings, create_guard=FakeGuard()), **build(error)
    )

    with pytest.raises(QUALIFY.QualificationError) as raised:
        call(provider)

    assert str(raised.value) == QUALIFY.PROVIDER_FAILURE.format(
        step=reason, reason=detail
    )


def test_native_provider_converts_a_pending_guard(
    inputs: object, bindings: object
) -> None:
    """Stop with the guard's reason when an earlier write is still unreconciled.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :returns: None; assertions verify the reason and no delete.
    """
    api = FakeApi()
    guard = FakeGuard(CLI_API.APIError(PROVIDER_REASON))
    provider = _native(
        inputs, dataclasses.replace(bindings, create_guard=guard), FakeMirrorSource(api)
    )

    with pytest.raises(
        QUALIFY.QualificationError,
        match=QUALIFY.PROVIDER_FAILURE.format(
            step=QUALIFY.MIRROR_DELETE, reason=PROVIDER_REASON
        ),
    ):
        provider.delete_mirror(MIRROR_ID, DESTINATION_PATH)

    assert api.calls == []


class FakeParser:
    """Stand in for the installed mirror ``build_parser`` result."""

    def parse_args(self, argv: Sequence[str]) -> object:
        """Keep the arguments.

        :param argv: Command arguments.
        :returns: Namespace holding them.
        """
        return SimpleNamespace(argv=tuple(argv))


def _command(
    code: int = EXIT_OK,
    engine: bool = True,
    error: Optional[Exception] = None,
) -> type:
    """Build a stand-in for the installed ``MirrorCommand``.

    :param code: Exit code of ``execute``.
    :param engine: Whether ``execute`` leaves a bound engine.
    :param error: Error raised by ``execute``, if any.
    :returns: Command class.
    """

    class FakeCommand:
        """Print a plan and expose bound services."""

        seen: ClassVar[MutableSequence[object]] = []

        def __init__(self, args: object) -> None:
            """Keep the parsed arguments and the bound state.

            :param args: Parsed arguments.
            """
            FakeCommand.seen.append(args)
            self.state = SimpleNamespace(
                engine=SimpleNamespace(source="source", destination="destination")
                if engine
                else None,
                session=SimpleNamespace(target_source=TARGET_SOURCE),
            )

        def execute(self) -> int:
            """Print a plan result.

            :returns: Configured exit code.
            :raises Exception: The configured error.
            """
            if error is not None:
                raise error
            print(json.dumps(_planned()))
            return code

    return FakeCommand


def test_native_provider_bind_runs_one_in_process_live_plan(
    inputs: object, bindings: object
) -> None:
    """Parse the same selection plus the live-plan option and keep its services.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :returns: None; assertions verify arguments, output and target source.
    """
    command = _command()
    selected = dataclasses.replace(
        bindings, mirror_parser=FakeParser, mirror_command=command
    )

    provider = QUALIFY.NativeMirrorProvider.bind(
        inputs, selected, QUALIFY.BASELINE_PLAN_STEP
    )

    assert command.seen[0].argv == (
        *inputs.command_selection(),
        QUALIFY.LIVE_PLAN_OPTION,
    )
    assert provider.target_source() == TARGET_SOURCE
    output = inputs.work_dir / f"{QUALIFY.BASELINE_PLAN_STEP}{QUALIFY.OUTPUT_SUFFIX}"
    assert json.loads(output.read_text()) == _planned()


@pytest.mark.parametrize(
    ("command", "reason"),
    (
        (_command(code=EXIT_STOP), f"{QUALIFY.BASELINE_PLAN_STEP}_failed"),
        (_command(engine=False), f"{QUALIFY.BASELINE_PLAN_STEP}_failed"),
        (
            _command(error=CLI_API.APIError(PROVIDER_REASON)),
            QUALIFY.PROVIDER_FAILURE.format(
                step=QUALIFY.BASELINE_PLAN_STEP, reason=PROVIDER_REASON
            ),
        ),
    ),
    ids=("plan-blocked", "no-engine", "api-error"),
)
def test_native_provider_bind_stops_when_the_live_plan_fails(
    inputs: object, bindings: object, command: type, reason: str
) -> None:
    """Stop when the in-process live plan does not succeed.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :param command: Failing command class.
    :param reason: Exact stopping reason.
    :returns: None; assertions verify the reason.
    """
    selected = dataclasses.replace(
        bindings, mirror_parser=FakeParser, mirror_command=command
    )

    with pytest.raises(QUALIFY.QualificationError, match=f"^{reason}$"):
        QUALIFY.NativeMirrorProvider.bind(inputs, selected, QUALIFY.BASELINE_PLAN_STEP)


def _argv(inputs: object, *extra: str) -> list[str]:
    """Build ``qualify_mirror.py`` arguments for the synthetic fixture.

    :param inputs: Synthetic fixture inputs.
    :param *extra: Additional arguments.
    :returns: Argument list.
    """
    seed_refs = [
        part
        for ref in inputs.seed_refs
        for part in ("--seed-ref", f"{ref.kind}:{ref.name}:{ref.commit}")
    ]
    return [
        "--skill",
        str(inputs.skill),
        "--work-dir",
        str(inputs.work_dir),
        "--project",
        inputs.project,
        "--destination",
        inputs.destination,
        "--source-id",
        str(inputs.source_id),
        *seed_refs,
        "--run-id",
        inputs.run_id,
        "--settle-seconds",
        "0",
        *extra,
    ]


def _patch_cli(
    monkeypatch: pytest.MonkeyPatch,
    bindings: object,
    runner: Callable[[object], object],
    provider: Callable[[object, object, str], object],
) -> None:
    """Replace the installed bindings, command runner and provider in the adapter.

    :param monkeypatch: Restores the adapter.
    :param bindings: Installed names.
    :param runner: Runner factory taking the inputs.
    :param provider: ``NativeMirrorProvider.bind`` replacement.
    """
    monkeypatch.setattr(CLI, "installed_bindings", lambda skill: bindings)
    monkeypatch.setattr(CLI, "InstalledMirrorCommand", runner)
    monkeypatch.setattr(CLI, "NativeMirrorProvider", SimpleNamespace(bind=provider))


def _output(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    """Decode the adapter's one JSON line.

    :param capsys: Captured output.
    :returns: Printed object.
    """
    return json.loads(capsys.readouterr().out)


def test_main_prints_the_ledger_and_exits_zero_after_pass(
    inputs: object,
    bindings: object,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit 0 with the final ledger.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :param monkeypatch: Replaces the adapter's bound names.
    :param capsys: Captured output.
    :returns: None; assertions verify exit code and output.
    """
    provider = FakeProvider()
    _patch_cli(
        monkeypatch,
        bindings,
        lambda selected: ScriptedRunner(_results()),
        lambda selected, bound, label: provider(label),
    )

    code = CLI.main(_argv(inputs, CLI.APPLY_MODE_OPTION))

    output = _output(capsys)
    assert code == EXIT_OK
    assert output["status"] == STATUS.passed
    assert output["ledger"]["cleanup"] == QUALIFY.CLEANUP_PASS
    assert provider.deletes == [MIRROR_DELETED, DESTINATION_DELETED]


@pytest.mark.parametrize(
    "mode", ((), (CLI.PLAN_ONLY_OPTION,)), ids=("default", "explicit")
)
def test_main_dry_run_is_the_default_and_writes_no_ledger(
    inputs: object,
    bindings: object,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: tuple[str, ...],
) -> None:
    """Exit 0 with the plan, without ``--apply``: no apply, no delete, no ledger.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :param monkeypatch: Replaces the adapter's bound names.
    :param capsys: Captured output.
    :param mode: No mode option, or ``--plan-only``.
    :returns: None; assertions verify exit code, output and zero mutation.
    """
    runner = ScriptedRunner(_results())
    provider = FakeProvider()
    _patch_cli(
        monkeypatch,
        bindings,
        lambda selected: runner,
        lambda selected, bound, label: provider(label),
    )

    code = CLI.main(_argv(inputs, *mode))

    output = _output(capsys)
    assert code == EXIT_OK
    assert output["status"] == STATUS.planned
    assert output["plan"]["plan_digest"] == PLAN_DIGEST
    assert runner.labels == [QUALIFY.LIVE_PLAN_STEP, QUALIFY.REJECTION_STEP]
    assert provider.deletes == []
    assert not (inputs.work_dir / QUALIFY.ALLOCATION_FILE).exists()


@pytest.mark.parametrize("option", sorted(CLI.HELP_OPTIONS))
def test_main_help_needs_no_skill_and_no_installed_copy(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    option: str,
) -> None:
    """Print the help without ``--skill`` and without importing the installed copy.

    :param monkeypatch: Records calls to the installed-copy binding.
    :param capsys: Captured output.
    :param option: ``-h`` or ``--help``.
    :returns: None; assertions verify exit 0, the help text and no binding.
    """
    bound: list[Path] = []
    monkeypatch.setattr(CLI, "installed_bindings", bound.append)

    with pytest.raises(SystemExit) as raised:
        CLI.main([option])

    help_text = capsys.readouterr().out
    assert raised.value.code == 0
    assert bound == []
    assert CLI.GITLAB_OPTION_PLACEHOLDER in help_text
    assert CLI.APPLY_MODE_OPTION in help_text
    assert QUALIFY.RECONCILE_OPTION in help_text


def test_main_sigterm_during_apply_exits_two_and_names_reconcile(
    inputs: object,
    bindings: object,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stop on SIGTERM at the apply, keep the ledger and restore the handler.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :param monkeypatch: Replaces the adapter's bound names.
    :param capsys: Captured output.
    :returns: None; assertions verify exit code, reason, ledger and no delete.
    """
    previous = signal.getsignal(signal.SIGTERM)
    provider = FakeProvider()
    _patch_cli(
        monkeypatch,
        bindings,
        lambda selected: ScriptedRunner(
            _results(),
            hooks={QUALIFY.APPLIED_STEP: lambda: signal.raise_signal(signal.SIGTERM)},
        ),
        lambda selected, bound, label: provider(label),
    )

    code = CLI.main(_argv(inputs, CLI.APPLY_MODE_OPTION))

    assert code == EXIT_STOP
    assert _output(capsys) == {
        "status": STATUS.blocked,
        "reason": _interrupted(QUALIFY.APPLY_PENDING),
    }
    assert _ledger(inputs)["cleanup"] == QUALIFY.APPLY_PENDING
    assert provider.deletes == []
    assert signal.getsignal(signal.SIGTERM) == previous


def test_main_passes_the_installed_gitlab_option(
    inputs: object,
    bindings: object,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Parse the installed option name and pass the selector to the command.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :param monkeypatch: Replaces the adapter's bound names.
    :param capsys: Captured output.
    :returns: None; assertions verify the selection.
    """
    seen: list[object] = []

    def runner(selected: object) -> object:
        """Record the inputs and return the scripted runner.

        :param selected: Inputs built by the adapter.
        :returns: Scripted runner.
        """
        seen.append(selected)
        return ScriptedRunner(_results())

    _patch_cli(
        monkeypatch,
        bindings,
        runner,
        lambda selected, bound, label: FakeProvider()(label),
    )

    code = CLI.main(
        _argv(
            inputs,
            CLI.PLAN_ONLY_OPTION,
            CATALOG.GITLAB_TARGET_OPTION,
            ENDPOINTS.GITLAB_PRIVATE_SELECTOR,
        )
    )

    capsys.readouterr()
    assert code == EXIT_OK
    assert seen[0].command_selection()[-2:] == (
        CATALOG.GITLAB_TARGET_OPTION,
        ENDPOINTS.GITLAB_PRIVATE_SELECTOR,
    )


def _timeout_runner(selected: object) -> object:
    """Build the real command runner over a process that times out.

    :param selected: Inputs built by the adapter.
    :returns: Command runner.
    """
    return QUALIFY.InstalledMirrorCommand(
        selected, _raise(subprocess.TimeoutExpired(["unit"], 1))
    )


def _failing_provider(selected: object, bound: object, label: str) -> object:
    """Build the real native provider over a source that fails.

    :param selected: Inputs built by the adapter.
    :param bound: Installed names.
    :param label: Output file stem.
    :returns: Native provider.
    """
    return _native(
        selected,
        bound,
        FakeMirrorSource(FakeApi(), error=CLI_API.APIError(PROVIDER_REASON)),
    )


@pytest.mark.parametrize(
    ("results", "runner", "provider", "reason"),
    (
        (
            {QUALIFY.LIVE_PLAN_STEP: (0, _planned({"destination": DESTINATION}))},
            None,
            None,
            "destination_present",
        ),
        (None, _timeout_runner, None, f"{QUALIFY.LIVE_PLAN_STEP}_timeout"),
        (
            None,
            None,
            _failing_provider,
            QUALIFY.PROVIDER_FAILURE.format(
                step=QUALIFY.SOURCE_READ, reason=PROVIDER_REASON
            ),
        ),
    ),
    ids=("fixture-not-clean", "command-timeout", "provider-error"),
)
def test_main_prints_the_reason_and_exits_two_when_blocked(
    inputs: object,
    bindings: object,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    results: Optional[dict[str, tuple[int, dict[str, object]]]],
    runner: Optional[Callable[[object], object]],
    provider: Optional[Callable[[object, object, str], object]],
    reason: str,
) -> None:
    """Exit 2 with a JSON reason for a stop, a timeout and a provider error.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :param monkeypatch: Replaces the adapter's bound names.
    :param capsys: Captured output.
    :param results: Scripted step results.
    :param runner: Runner factory, or None for the scripted runner.
    :param provider: Provider factory, or None for the fake provider.
    :param reason: Exact stopping reason.
    :returns: None; assertions verify exit code and output.
    """
    fake = FakeProvider()
    _patch_cli(
        monkeypatch,
        bindings,
        runner or (lambda selected: ScriptedRunner(_results(results))),
        provider or (lambda selected, bound, label: fake(label)),
    )

    code = CLI.main(_argv(inputs, CLI.APPLY_MODE_OPTION))

    assert code == EXIT_STOP
    assert _output(capsys) == {"status": STATUS.blocked, "reason": reason}
    assert fake.deletes == []


def test_main_reconcile_without_a_ledger_exits_two(
    inputs: object,
    bindings: object,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit 2 with ``nothing_to_reconcile`` and create no work directory.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :param monkeypatch: Replaces the adapter's bound names.
    :param capsys: Captured output.
    :returns: None; assertions verify exit code, output and no directory.
    """
    missing = inputs.work_dir / "missing"
    _patch_cli(
        monkeypatch,
        bindings,
        lambda selected: ScriptedRunner(_results()),
        lambda selected, bound, label: FakeProvider()(label),
    )

    code = CLI.main(
        [
            *_argv(dataclasses.replace(inputs, work_dir=missing)),
            QUALIFY.RECONCILE_OPTION,
        ]
    )

    assert code == EXIT_STOP
    assert _output(capsys) == {
        "status": STATUS.blocked,
        "reason": QUALIFY.NOTHING_TO_RECONCILE,
    }
    assert not missing.exists()


def test_main_reports_an_unavailable_installed_copy(
    inputs: object,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit 2 when the installed ``lib`` cannot be bound.

    :param inputs: Synthetic fixture inputs.
    :param monkeypatch: Replaces the adapter's bound names.
    :param capsys: Captured output.
    :returns: None; assertions verify exit code and output.
    """

    def unavailable(skill: Path) -> NoReturn:
        """Fail like a missing installed module.

        :param skill: Installed directory.
        :raises QUALIFY.QualificationError: Always.
        """
        raise QUALIFY.QualificationError(
            f"installed_lib_unavailable:{QUALIFY.CATALOG_MODULE}"
        )

    monkeypatch.setattr(CLI, "installed_bindings", unavailable)

    code = CLI.main(_argv(inputs))

    assert code == CLI.PRE_BINDING_EXIT
    assert _output(capsys) == {
        "status": CLI.PRE_BINDING_BLOCKED,
        "reason": f"installed_lib_unavailable:{QUALIFY.CATALOG_MODULE}",
    }


@pytest.mark.parametrize(
    "modes",
    (
        (CLI.PLAN_ONLY_OPTION, QUALIFY.RECONCILE_OPTION),
        (CLI.APPLY_MODE_OPTION, QUALIFY.RECONCILE_OPTION),
        (CLI.PLAN_ONLY_OPTION, CLI.APPLY_MODE_OPTION),
    ),
    ids=("plan-only-reconcile", "apply-reconcile", "plan-only-apply"),
)
def test_main_rejects_two_modes(
    inputs: object,
    bindings: object,
    monkeypatch: pytest.MonkeyPatch,
    modes: tuple[str, str],
) -> None:
    """Allow one mode at a time.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :param monkeypatch: Replaces the adapter's bound names.
    :param modes: Two mode options given together.
    :returns: None; assertions verify the argparse exit.
    """
    monkeypatch.setattr(CLI, "installed_bindings", lambda skill: bindings)

    with pytest.raises(SystemExit) as raised:
        CLI.main(_argv(inputs, *modes))

    # argparse exits 2 on a usage error.
    assert raised.value.code == 2
