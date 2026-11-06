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
from enum import StrEnum
from http import HTTPMethod, HTTPStatus
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import ClassVar, Final, NoReturn, Optional
from urllib.parse import quote

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
MIRROR_CONSTANTS = import_script_module(QUALIFY.MIRROR_CONSTANTS_MODULE)
MIRROR_CLI = import_script_module(QUALIFY.MIRROR_CLI_MODULE)
MIRRORING = import_script_module(QUALIFY.MIRRORING_MODULE)
SCHEMA = import_script_module("lib.python.core.schema")
EVIDENCE = import_script_module("lib.python.core.gitlab.mirror.evidence")
PROVENANCE = import_script_module(QUALIFY.PROVENANCE_MODULE)
REMOTE = import_script_module(QUALIFY.REMOTE_MODULE)

# The repository's own lib stands in for the installed copy.
STATUS: Final = QUALIFY.InstalledStatus.bind(SYS_STATUS, MIRROR_CONSTANTS.Outcome)
FIELDS: Final = QUALIFY.InstalledFields.bind(
    MIRROR_CONSTANTS.Field,
    MIRROR_CLI.EnvelopeField,
    MIRRORING.MirrorField,
    PROVENANCE,
)
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
FAKE_PASS: Final[str] = "UNIT_PASS"
FAKE_PLANNED: Final[str] = "UNIT_PLANNED"
FAKE_BLOCKED: Final[str] = "UNIT_BLOCKED"
FAKE_APPLIED: Final[str] = "UNIT_APPLIED"
FAKE_NO_OP: Final[str] = "UNIT_NO_OP"
FAKE_EXIT_OK: Final[int] = 0
FAKE_EXIT_STOP: Final[int] = 7
# Prefix that gives every bound result and evidence key another value.
FAKE_KEY_PREFIX: Final[str] = "unit_"

CANDIDATE: Final[str] = "c" * 40
OTHER_CANDIDATE: Final[str] = "d" * 40
SOURCE_ID: Final[int] = 261
SOURCE_HOST: Final[str] = "gitlab.example.test"
SOURCE_PATH: Final[str] = "unit/mirror-source"
DESTINATION_HOST: Final[str] = "github.example.test"
DESTINATION_PATH: Final[str] = "unit/mirror-destination"
REPOSITORY_ID: Final[int] = 9001
OTHER_REPOSITORY_ID: Final[int] = 9002
CLONE_URL: Final[str] = "https://github.example.test/unit/mirror-destination.git"
# Mirror URLs GitLab can report for a mirror created with CLONE_URL.
MIRROR_URL_WITHOUT_SUFFIX: Final[str] = (
    "https://github.example.test/unit/mirror-destination"
)
MIRROR_URL_WITH_SLASH: Final[str] = (
    "https://github.example.test/unit/mirror-destination.git/"
)
# A mirror URL whose unclosed IPv6 host makes urlsplit raise ValueError.
MALFORMED_MIRROR_URL: Final[str] = "https://[::1/x"
MIRROR_ID: Final[int] = 15
EXECUTION_HOST: Final[str] = "unit-host"
SKILL_DIGEST: Final[str] = "unit-digest"


def _source(fields: object = FIELDS) -> dict[str, object]:
    """Build the source evidence the installed command reports.

    :param fields: Keys the evidence is written with.
    :returns: Source evidence.
    """
    return {fields.id: SOURCE_ID, fields.host: SOURCE_HOST, fields.path: SOURCE_PATH}


def _destination(
    fields: object = FIELDS, identity: Optional[int] = REPOSITORY_ID
) -> dict[str, object]:
    """Build the destination evidence the installed command reports.

    :param fields: Keys the evidence is written with.
    :param identity: Repository ID, or None for an absent destination.
    :returns: Destination evidence.
    """
    return {
        fields.id: identity,
        fields.host: DESTINATION_HOST,
        fields.path: DESTINATION_PATH,
    }


def _mirror(fields: object = FIELDS) -> dict[str, object]:
    """Build the mirror evidence the installed command reports.

    :param fields: Keys the evidence is written with.
    :returns: Mirror evidence.
    """
    return {fields.id: MIRROR_ID}


def _owned(mirror: bool, repository: bool, fields: object = FIELDS) -> dict[str, bool]:
    """Build the ``created`` flags of an APPLIED record.

    :param mirror: Whether the run created the mirror.
    :param repository: Whether the run created the repository.
    :param fields: Keys the flags are written with.
    :returns: Ownership flags.
    """
    return {fields.mirror: mirror, fields.repository: repository}


SOURCE: Final[dict[str, object]] = _source()
DESTINATION: Final[dict[str, object]] = _destination()
MIRROR: Final[dict[str, object]] = _mirror()
SEED: Final[str] = "a" * 40
MOVED: Final[str] = "e" * 40
REFS: Final[dict[str, object]] = {"count": 2, "digest": "unit"}
PLAN_DIGEST: Final[str] = "b" * 64
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
        project=SOURCE_PATH,
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


def _replaced(module: ModuleType, **names: object) -> SimpleNamespace:
    """Copy an installed module with some of its names replaced.

    :param module: Module of the repository's own ``lib``.
    :param **names: Replacement per module attribute.
    :returns: Namespace with the module's names and the replacements.
    """
    return SimpleNamespace(**{**vars(module), **names})


def _renamed(enumeration: type[StrEnum]) -> SimpleNamespace:
    """Give every member of an installed key enumeration another value.

    :param enumeration: ``Field``, ``EnvelopeField`` or ``MirrorField``.
    :returns: Namespace of the member names with ``FAKE_KEY_PREFIX`` values.
    """
    return SimpleNamespace(
        **{member.name: FAKE_KEY_PREFIX + member.value for member in enumeration}
    )


def _serve(monkeypatch: pytest.MonkeyPatch, fakes: Mapping[str, object]) -> None:
    """Serve fake installed modules by name and the real ones otherwise.

    :param monkeypatch: Restores the module loader and ``sys.path``.
    :param fakes: Fake module per dotted name.
    """
    loader = QUALIFY._installed_module
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(
        QUALIFY,
        "_installed_module",
        lambda skill, name: fakes[name] if name in fakes else loader(skill, name),
    )


@pytest.fixture
def fake_fields(monkeypatch: pytest.MonkeyPatch) -> object:
    """Bind keys from installed enumerations whose every value differs from the lib.

    :param monkeypatch: Serves the renamed ``Field``, ``EnvelopeField`` and
        ``MirrorField``, and a provenance module with a renamed
        ``DIGEST_VALUE_KEY``.
    :returns: Keys bound by ``installed_bindings``.
    """
    _serve(
        monkeypatch,
        {
            QUALIFY.MIRROR_CONSTANTS_MODULE: _replaced(
                MIRROR_CONSTANTS, Field=_renamed(MIRROR_CONSTANTS.Field)
            ),
            QUALIFY.MIRROR_CLI_MODULE: _replaced(
                MIRROR_CLI, EnvelopeField=_renamed(MIRROR_CLI.EnvelopeField)
            ),
            QUALIFY.MIRRORING_MODULE: _replaced(
                MIRRORING, MirrorField=_renamed(MIRRORING.MirrorField)
            ),
            QUALIFY.PROVENANCE_MODULE: _replaced(
                PROVENANCE,
                DIGEST_VALUE_KEY=FAKE_KEY_PREFIX + PROVENANCE.DIGEST_VALUE_KEY,
            ),
        },
    )
    return QUALIFY.installed_bindings(SKILL_ROOT).fields


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

    def __init__(
        self, mirror: bool = True, destination: bool = True, fields: object = FIELDS
    ) -> None:
        """Start with the run-created objects present or absent.

        :param mirror: Whether the mirror starts present.
        :param destination: Whether the destination starts present.
        :param fields: Keys the installed evidence is written with.
        """
        self.fields = fields
        self.mirror_present = mirror
        self.destination_present = destination
        self.mirror_deleted = False
        self.destination_deleted = False
        self.mirror_reads_after_delete = 0
        self.deletes: MutableSequence[str] = []
        self.labels: MutableSequence[str] = []
        self.mirror_delete_status = HTTPStatus.NO_CONTENT
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
        return self.source_override or _source(self.fields)

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
            return self.destination_override or _destination(self.fields)
        if self.destination_deleted and "destination" in self.reappear:
            return _destination(self.fields)
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
        return HTTPStatus.NO_CONTENT


def _planned(
    record: Optional[Mapping[str, object]] = None,
    top: Optional[Mapping[str, object]] = None,
    tokens: object = STATUS,
    fields: object = FIELDS,
) -> dict[str, object]:
    """Build a live-plan result of a clean fixture with optional changes.

    :param record: Record fields replacing the clean ones.
    :param top: Top-level fields replacing the clean ones.
    :param tokens: Status tokens the scripted command reports with.
    :param fields: Keys the scripted command writes with.
    :returns: Live-plan result.
    """
    clean = {
        fields.source: _source(fields),
        fields.destination: _destination(fields, None),
        fields.mirror: None,
    }
    return {
        fields.status: tokens.planned,
        fields.plan_digest: PLAN_DIGEST,
        fields.target_source: TARGET_SOURCE,
        fields.records: [{**clean, **(record or {})}],
        **(top or {}),
    }


def _created(
    changes: Optional[Mapping[str, object]] = None,
    tokens: object = STATUS,
    fields: object = FIELDS,
) -> dict[str, object]:
    """Build the APPLIED record of a run that created both objects.

    :param changes: Record fields replacing the defaults.
    :param tokens: Status tokens the scripted command reports with.
    :param fields: Keys the scripted command writes with.
    :returns: APPLIED record.
    """
    return {
        fields.action: tokens.applied,
        fields.source: _source(fields),
        fields.destination: _destination(fields),
        fields.mirror: _mirror(fields),
        fields.created: _owned(True, True, fields),
        fields.source_refs: REFS,
        fields.destination_refs: REFS,
        **(changes or {}),
    }


def _results(
    overrides: Optional[Mapping[str, tuple[int, dict[str, object]]]] = None,
    tokens: object = STATUS,
    fields: object = FIELDS,
) -> dict[str, tuple[int, dict[str, object]]]:
    """Build the scripted results of a successful run with optional overrides.

    :param overrides: Replacement result per step label.
    :param tokens: Status tokens and exit mapping the scripted command uses.
    :param fields: Keys the scripted command writes with.
    :returns: Exit code and result per step label.
    """
    passed = tokens.exit_code(tokens.passed)
    receipt = {
        fields.execution_host: EXECUTION_HOST,
        fields.tested_revision: CANDIDATE,
        fields.target_source: TARGET_SOURCE,
        fields.skill: {fields.digest: SKILL_DIGEST},
    }
    noop_record = _created({fields.action: tokens.no_op}, tokens, fields)
    results = {
        QUALIFY.LIVE_PLAN_STEP: (passed, _planned(tokens=tokens, fields=fields)),
        QUALIFY.REJECTION_STEP: (
            tokens.exit_code(tokens.blocked),
            {fields.status: tokens.blocked, fields.mutated: False},
        ),
        QUALIFY.APPLIED_STEP: (
            passed,
            {
                **receipt,
                fields.status: tokens.passed,
                fields.mutated: True,
                fields.records: [_created(tokens=tokens, fields=fields)],
            },
        ),
        QUALIFY.NOOP_PLAN_STEP: (
            passed,
            _planned(noop_record, tokens=tokens, fields=fields),
        ),
        QUALIFY.NOOP_STEP: (
            passed,
            {
                **receipt,
                fields.status: tokens.passed,
                fields.mutated: False,
                fields.records: [noop_record],
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
    return code, {**applied, FIELDS.records: [record]}


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
    fields: object = FIELDS,
    **controls: object,
) -> object:
    """Build a qualification bound to the given status tokens and keys.

    :param inputs: Synthetic fixture inputs.
    :param runner: Command runner.
    :param provider: Provider factory.
    :param tokens: Status tokens bound from the installed copy.
    :param fields: Result and receipt keys bound from the installed copy.
    :param **controls: ``sleep`` or ``clock`` replacement.
    :returns: Qualification under test.
    """
    return QUALIFY.MirrorQualification(
        inputs, runner, provider, tokens, fields, **controls
    )


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
    field = MIRROR_CONSTANTS.Field
    pair = json.loads(_mirror_pair_baseline())
    digest = PROVENANCE.functional_digest(SKILL_ROOT)
    for receipt in pair.values():
        skill = receipt[field.SKILL]
        receipt[field.TESTED_REVISION] = CANDIDATE
        skill[field.REVISION][field.VALUE] = CANDIDATE
        skill[field.FUNCTIONAL_DIGEST] = digest
    return pair[MIRROR_APPLIED_FILE], pair[MIRROR_NO_OP_FILE]


def test_qualification_removes_only_created_objects_and_records_cleanup(
    inputs: object,
) -> None:
    """Run every step once; the receipt passes its schema and the public checker.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify steps, deletions, ledger, schema and checker.
    """
    applied, noop = _engine_pair()
    record = applied[FIELDS.records][0]
    source = record[FIELDS.source]
    destination = record[FIELDS.destination]
    mirror_id = record[FIELDS.mirror][FIELDS.id]
    selected = dataclasses.replace(
        inputs,
        project=source[FIELDS.path],
        destination=destination[FIELDS.path],
        source_id=source[FIELDS.id],
    )
    absent = {**destination, FIELDS.id: None}
    runner = ScriptedRunner(
        _results(
            {
                QUALIFY.LIVE_PLAN_STEP: (
                    EXIT_OK,
                    _planned({FIELDS.source: source, FIELDS.destination: absent}),
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
        f"destination:{destination[FIELDS.path]}:{destination[FIELDS.id]}",
    ]
    assert ledger[QUALIFY.LEDGER_STATE_FIELD] == QUALIFY.CLEANUP_PASS
    assert ledger[QUALIFY.LEDGER_CANDIDATE_FIELD] == CANDIDATE
    assert ledger[QUALIFY.TARGET_SOURCE_FIELD] == TARGET_SOURCE
    assert ledger[QUALIFY.LEDGER_SEED_REFS_FIELD] == SEED_REFS
    cleanup = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert cleanup[QUALIFY.STATUS_FIELD] == STATUS.passed
    assert cleanup[QUALIFY.BEFORE_FIELD] == {
        QUALIFY.SOURCE_FIELD: source,
        QUALIFY.DESTINATION_FIELD: destination,
        QUALIFY.MIRROR_ID_FIELD: mirror_id,
    }
    assert cleanup[QUALIFY.DELETE_FIELD] == {
        QUALIFY.MIRROR_HTTP_STATUS_FIELD: HTTPStatus.NO_CONTENT,
        QUALIFY.DESTINATION_HTTP_STATUS_FIELD: HTTPStatus.NO_CONTENT,
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
    assert summary[QUALIFY.LEDGER_PLAN_DIGEST_FIELD] == PLAN_DIGEST
    assert summary[QUALIFY.LEDGER_CANDIDATE_FIELD] == CANDIDATE
    assert summary[QUALIFY.TARGET_SOURCE_FIELD] == TARGET_SOURCE
    assert summary[QUALIFY.LEDGER_SEED_REFS_FIELD] == SEED_REFS


@pytest.mark.parametrize(
    ("overrides", "change", "reason"),
    (
        (
            {
                QUALIFY.LIVE_PLAN_STEP: (
                    0,
                    _planned({FIELDS.source: {**SOURCE, FIELDS.id: SOURCE_ID + 1}}),
                )
            },
            None,
            "source_id_mismatch",
        ),
        (
            {QUALIFY.LIVE_PLAN_STEP: (0, _planned({FIELDS.destination: DESTINATION}))},
            None,
            "destination_present",
        ),
        (
            {QUALIFY.LIVE_PLAN_STEP: (0, _planned({FIELDS.mirror: MIRROR}))},
            None,
            "mirror_present",
        ),
        (
            {QUALIFY.LIVE_PLAN_STEP: (0, _planned(top={FIELDS.target_source: None}))},
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
                provider, "source_override", {**SOURCE, FIELDS.path: "unit/other"}
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
                    {FIELDS.status: STATUS.blocked, FIELDS.mutated: True},
                )
            },
            None,
            "rejection_mutated",
        ),
        (
            {
                QUALIFY.REJECTION_STEP: (
                    EXIT_OK,
                    {FIELDS.status: STATUS.passed, FIELDS.mutated: False},
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
                    {FIELDS.status: STATUS.blocked},
                )
            },
            f"{QUALIFY.APPLIED_STEP}_unexpected:{EXIT_STOP}:{STATUS.blocked}",
            QUALIFY.APPLY_PENDING,
            None,
        ),
        (
            {
                QUALIFY.APPLIED_STEP: _applied(
                    _created({FIELDS.created: _owned(True, False)})
                )
            },
            "applied_result_unexpected",
            QUALIFY.CLEANUP_PENDING,
            _owned(True, False),
        ),
        (
            {
                QUALIFY.NOOP_STEP: (
                    EXIT_OK,
                    {
                        FIELDS.status: STATUS.passed,
                        FIELDS.mutated: False,
                        FIELDS.records: [_created()],
                    },
                )
            },
            "noop_result_unexpected",
            QUALIFY.CLEANUP_PENDING,
            _owned(True, True),
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
    assert ledger[QUALIFY.LEDGER_STATE_FIELD] == state
    assert ledger[QUALIFY.LEDGER_CREATED_FIELD] == created
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
                provider,
                "destination_override",
                {**DESTINATION, FIELDS.id: OTHER_REPOSITORY_ID},
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
                {**DESTINATION, FIELDS.id: OTHER_REPOSITORY_ID},
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
    assert _ledger(inputs)[QUALIFY.LEDGER_STATE_FIELD] == state
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
    assert _ledger(inputs)[QUALIFY.LEDGER_STATE_FIELD] == QUALIFY.VERIFY_PENDING
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

    assert _ledger(inputs)[QUALIFY.LEDGER_STATE_FIELD] == QUALIFY.APPLY_PENDING
    assert _ledger(inputs)[QUALIFY.LEDGER_CREATED_FIELD] is None
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
    assert ledger[QUALIFY.LEDGER_STATE_FIELD] == QUALIFY.APPLY_PENDING
    assert ledger[QUALIFY.LEDGER_CREATED_FIELD] is None
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
            HTTPStatus.NO_CONTENT,
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
    assert ledger[QUALIFY.LEDGER_STATE_FIELD] == QUALIFY.MIRROR_DELETE_PENDING
    assert ledger[QUALIFY.LEDGER_CREATED_FIELD] == _owned(True, True)
    assert not (inputs.work_dir / QUALIFY.CLEANUP_RECORD).exists()
    assert not (inputs.work_dir / QUALIFY.NOOP_WITH_CLEANUP).exists()
    fresh = reconciler()

    reconciled = _qualification(inputs, ScriptedRunner(_results()), fresh).reconcile()

    assert fresh.deletes == reconcile_deletes
    assert reconciled[QUALIFY.LEDGER_STATE_FIELD] == QUALIFY.CLEANUP_PASS
    record = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert record[QUALIFY.DELETE_FIELD] == {
        QUALIFY.MIRROR_HTTP_STATUS_FIELD: mirror_status,
        QUALIFY.DESTINATION_HTTP_STATUS_FIELD: HTTPStatus.NO_CONTENT,
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
    _serve(
        monkeypatch,
        {
            QUALIFY.STATUS_MODULE: SimpleNamespace(
                PASS=FAKE_PASS,
                PLANNED=FAKE_PLANNED,
                BLOCKED=FAKE_BLOCKED,
                exit_code=_fake_exit_code,
            ),
            QUALIFY.MIRROR_CONSTANTS_MODULE: _replaced(
                MIRROR_CONSTANTS,
                Outcome=SimpleNamespace(APPLIED=FAKE_APPLIED, NO_OP=FAKE_NO_OP),
            ),
        },
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

    assert ledger[QUALIFY.LEDGER_STATE_FIELD] == QUALIFY.CLEANUP_PASS
    record = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert record[QUALIFY.STATUS_FIELD] == FAKE_PASS


def test_bound_fields_drive_every_read_of_a_run(
    inputs: object, fake_fields: object
) -> None:
    """Read the command result and receipt with the installed copy's keys.

    :param inputs: Synthetic fixture inputs.
    :param fake_fields: Keys bound from renamed installed enumerations.
    :returns: None; assertions verify the renamed keys pass, the lib's stop, and
        the cleanup record keeps this tool's own keys.
    """
    with pytest.raises(
        QUALIFY.QualificationError,
        match=f"^{QUALIFY.LIVE_PLAN_STEP}_unexpected:{EXIT_OK}:None$",
    ):
        _qualification(
            inputs, ScriptedRunner(_results()), FakeProvider(), fields=fake_fields
        ).plan()
    provider = FakeProvider(fields=fake_fields)

    ledger = _qualification(
        inputs,
        ScriptedRunner(_results(fields=fake_fields)),
        provider,
        fields=fake_fields,
    ).run()

    assert ledger[QUALIFY.LEDGER_STATE_FIELD] == QUALIFY.CLEANUP_PASS
    assert provider.deletes == [MIRROR_DELETED, DESTINATION_DELETED]
    record = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert record[QUALIFY.BEFORE_FIELD] == {
        QUALIFY.SOURCE_FIELD: _source(fake_fields),
        QUALIFY.DESTINATION_FIELD: _destination(fake_fields),
        QUALIFY.MIRROR_ID_FIELD: MIRROR_ID,
    }
    assert record[QUALIFY.EXECUTION_HOST_FIELD] == EXECUTION_HOST
    assert record[QUALIFY.SKILL_DIGEST_FIELD] == SKILL_DIGEST
    assert record[QUALIFY.TESTED_REVISION_FIELD] == CANDIDATE


def test_bound_fields_drive_every_read_of_a_reconcile(
    inputs: object, fake_fields: object
) -> None:
    """Reconcile a ledger and receipts written with the installed copy's keys.

    The run stops at the NO_OP receipt of another candidate, before any deletion;
    reconcile then binds the record to the ledger's candidate.

    :param inputs: Synthetic fixture inputs.
    :param fake_fields: Keys bound from renamed installed enumerations.
    :returns: None; assertions verify both deletions and the record identity.
    """
    results = _results(fields=fake_fields)
    code, noop = results[QUALIFY.NOOP_STEP]
    results[QUALIFY.NOOP_STEP] = (
        code,
        {**noop, fake_fields.tested_revision: OTHER_CANDIDATE},
    )
    with pytest.raises(
        QUALIFY.QualificationError, match="^receipt_candidate_mismatch$"
    ):
        _qualification(
            inputs,
            ScriptedRunner(results),
            FakeProvider(fields=fake_fields),
            fields=fake_fields,
        ).run()
    provider = FakeProvider(fields=fake_fields)

    ledger = _qualification(
        inputs, ScriptedRunner(results), provider, fields=fake_fields
    ).reconcile()

    assert ledger[QUALIFY.LEDGER_STATE_FIELD] == QUALIFY.CLEANUP_PASS
    assert provider.deletes == [MIRROR_DELETED, DESTINATION_DELETED]
    record = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert record[QUALIFY.TESTED_REVISION_FIELD] == CANDIDATE
    assert record[QUALIFY.EXECUTION_HOST_FIELD] == EXECUTION_HOST
    assert record[QUALIFY.SKILL_DIGEST_FIELD] == SKILL_DIGEST


def test_bound_fields_name_the_manual_read_back(
    inputs: object, fake_fields: object
) -> None:
    """Name the recorded host and path read with the installed copy's keys.

    :param inputs: Synthetic fixture inputs.
    :param fake_fields: Keys bound from renamed installed enumerations.
    :returns: None; assertions verify the reason and no provider was bound.
    """
    results = _results(
        {QUALIFY.APPLIED_STEP: (EXIT_STOP, {fake_fields.status: STATUS.blocked})},
        fields=fake_fields,
    )
    with pytest.raises(QUALIFY.QualificationError):
        _qualification(
            inputs,
            ScriptedRunner(results),
            FakeProvider(fields=fake_fields),
            fields=fake_fields,
        ).run()
    provider = FakeProvider(fields=fake_fields)

    with pytest.raises(QUALIFY.QualificationError) as raised:
        _qualification(
            inputs, ScriptedRunner(results), provider, fields=fake_fields
        ).reconcile()

    assert str(raised.value) == _manual_read_back()
    assert provider.labels == []


def test_receipt_from_another_candidate_is_rejected(inputs: object) -> None:
    """Bind the receipts to the installed copy's verified revision.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify no cleanup ran.
    """
    results = _results()
    code, noop = results[QUALIFY.NOOP_STEP]
    results[QUALIFY.NOOP_STEP] = (
        code,
        {**noop, FIELDS.tested_revision: OTHER_CANDIDATE},
    )
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
        SOURCE_PATH,
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
    assert ledger[QUALIFY.LEDGER_STATE_FIELD] == QUALIFY.CLEANUP_PASS
    assert ledger[QUALIFY.RECONCILED_FIELD] is True
    record = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert record[QUALIFY.RECONCILED_FIELD] is True
    assert record[QUALIFY.TESTED_REVISION_FIELD] == CANDIDATE
    assert record[QUALIFY.DELETE_FIELD] == {
        QUALIFY.MIRROR_HTTP_STATUS_FIELD: HTTPStatus.NO_CONTENT,
        QUALIFY.DESTINATION_HTTP_STATUS_FIELD: HTTPStatus.NO_CONTENT,
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
                _created({FIELDS.created: _owned(True, False)})
            )
        },
    )
    provider = FakeProvider()

    _qualification(inputs, ScriptedRunner(_results()), provider).reconcile()

    assert provider.deletes == [MIRROR_DELETED]
    assert provider.destination_present is True
    record = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert record[QUALIFY.DELETE_FIELD] == {
        QUALIFY.MIRROR_HTTP_STATUS_FIELD: HTTPStatus.NO_CONTENT,
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
    assert ledger[QUALIFY.LEDGER_STATE_FIELD] == QUALIFY.CLEANUP_PASS
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
            host=DESTINATION_HOST, path=DESTINATION_PATH
        ),
        source=QUALIFY.EVIDENCE_LOCATION.format(host=SOURCE_HOST, path=SOURCE_PATH),
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
                {FIELDS.status: STATUS.blocked},
            )
        },
    )
    provider = FakeProvider()

    with pytest.raises(QUALIFY.QualificationError) as raised:
        _qualification(inputs, ScriptedRunner(_results()), provider).reconcile()

    assert str(raised.value) == _manual_read_back()
    assert provider.labels == []
    assert provider.deletes == []
    assert _ledger(inputs)[QUALIFY.LEDGER_STATE_FIELD] == QUALIFY.APPLY_PENDING


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
                provider,
                "destination_override",
                {**DESTINATION, FIELDS.id: OTHER_REPOSITORY_ID},
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
    assert _ledger(inputs)[QUALIFY.LEDGER_STATE_FIELD] != QUALIFY.CLEANUP_PASS


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
        self.on_delete: Optional[Callable[[], None]] = None

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
        """Record a no-content request; a DELETE then runs ``on_delete``, if set.

        :param session: Provider session.
        :param method: HTTP method.
        :param endpoint: Relative route.
        :raises Exception: The configured error.
        """  # noqa: DOC503 - The configured fixture exception is raised verbatim.
        self.calls.append((str(method), endpoint))
        if self.error is not None:
            raise self.error
        if method == HTTPMethod.DELETE and self.on_delete is not None:
            self.on_delete()


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
            evidence=lambda: {**DESTINATION, FIELDS.id: self.repository_id},
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
    assert bindings.repository_url_key is REMOTE.repository_url_key
    assert bindings.status == STATUS
    assert bindings.fields == FIELDS


def test_installed_bindings_refuse_a_lib_from_another_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop when ``lib`` was already imported from outside the installed copy.

    :param tmp_path: Installed directory without the imported ``lib``.
    :param monkeypatch: Restores ``sys.path``.
    :returns: None; assertions verify the reason.
    """
    monkeypatch.setattr(sys, "path", list(sys.path))
    reason = QUALIFY.INSTALLED_LIB_SHADOWED.format(name=QUALIFY.CATALOG_MODULE)

    with pytest.raises(QUALIFY.QualificationError, match=f"^{reason}$"):
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
    # Attribute name that installed_bindings reads first from installed catalog.
    reason = QUALIFY.INSTALLED_LIB_INCOMPATIBLE.format(name="GITLAB_TARGET_OPTION")

    with pytest.raises(QUALIFY.QualificationError, match=f"^{reason}$"):
        QUALIFY.installed_bindings(tmp_path)


@pytest.mark.parametrize(
    ("module", "name"),
    (
        (QUALIFY.REMOTE_MODULE, REMOTE.repository_url_key.__name__),
        # Attribute name that InstalledFields.bind reads from installed provenance.
        (QUALIFY.PROVENANCE_MODULE, "DIGEST_VALUE_KEY"),
    ),
    ids=("remote-without-repository-url-key", "provenance-without-digest-key"),
)
def test_installed_bindings_refuse_an_older_lib_without_a_newer_name(
    monkeypatch: pytest.MonkeyPatch, module: str, name: str
) -> None:
    """Stop with the missing name when the installed module predates that name.

    :param monkeypatch: Serves the installed module without ``name``.
    :param module: Dotted installed module name.
    :param name: Name ``installed_bindings`` binds from ``module``.
    :returns: None; assertions verify the reason.
    """
    installed = vars(import_script_module(module))
    _serve(
        monkeypatch,
        {
            module: SimpleNamespace(
                **{key: value for key, value in installed.items() if key != name}
            )
        },
    )
    reason = QUALIFY.INSTALLED_LIB_INCOMPATIBLE.format(name=name)

    with pytest.raises(QUALIFY.QualificationError, match=f"^{reason}$"):
        QUALIFY.installed_bindings(SKILL_ROOT)


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
    api = FakeApi({ref_route: {QUALIFY.REF_COMMIT_KEY: {QUALIFY.COMMIT_ID_KEY: SEED}}})
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


@pytest.mark.parametrize(
    "mirror_url",
    (MIRROR_URL_WITHOUT_SUFFIX, MIRROR_URL_WITH_SLASH),
    ids=("without-git-suffix", "trailing-slash"),
)
def test_native_provider_matches_the_mirror_url_by_the_installed_key(
    inputs: object, bindings: object, mirror_url: str
) -> None:
    """Match a mirror URL that differs from the clone URL only in its URL key.

    GitLab can report ``https://host/o/r`` or ``https://host/o/r.git/`` for a
    mirror created with the clone URL ``https://host/o/r.git``. A replaced
    ``repository_url_key`` that keeps the URL unchanged stops the match, so the
    provider compares with the bound key and not with its own normalization.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :param mirror_url: URL GitLab reports for the mirror.
    :returns: None; assertions verify the match with the installed key and the
        mismatch with a key that keeps the URL unchanged.
    """
    source = FakeMirrorSource(FakeApi(), mirror_url=mirror_url)
    matched = _native(inputs, bindings, source)
    kept = _native(
        inputs, dataclasses.replace(bindings, repository_url_key=str), source
    )

    assert matched.mirror_targets(MIRROR_ID, DESTINATION_PATH) is True
    assert kept.mirror_targets(MIRROR_ID, DESTINATION_PATH) is False


def test_native_provider_stops_on_a_malformed_mirror_url(
    inputs: object, bindings: object
) -> None:
    """Stop with a named reason when the installed URL key rejects the mirror URL.

    ``mirror_targets`` converts the ``ValueError`` of ``repository_url_key`` into
    ``QualificationError``, which ``qualify_mirror.main`` reports as BLOCKED.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :returns: None; assertions verify the reason and the original ``ValueError``.
    """
    provider = _native(
        inputs, bindings, FakeMirrorSource(FakeApi(), mirror_url=MALFORMED_MIRROR_URL)
    )

    with pytest.raises(
        QUALIFY.QualificationError, match=f"^{QUALIFY.REPOSITORY_URL_MALFORMED}$"
    ) as stopped:
        provider.mirror_targets(MIRROR_ID, DESTINATION_PATH)

    assert isinstance(stopped.value.__cause__, ValueError)


def _ref_responses() -> dict[str, object]:
    """Build the GitLab ref responses of every seed ref.

    :returns: ``REF_ENDPOINT`` response per seed ref, holding its input commit.
    """
    return {
        QUALIFY.REF_ENDPOINT.format(
            project=SOURCE_ID, kind=ref.kind, name=quote(ref.name, safe="")
        ): {QUALIFY.REF_COMMIT_KEY: {QUALIFY.COMMIT_ID_KEY: ref.commit}}
        for ref in (MAIN_REF, TAG_REF)
    }


class NativeServices:
    """Hold fake installed services whose DELETE removes the deleted object.

    Calling the instance is the provider factory: it records the label and returns
    a ``NativeMirrorProvider`` over the same services.
    """

    def __init__(self, inputs: object, bindings: object, mirror_url: str) -> None:
        """Start with the mirror and the destination present.

        :param inputs: Synthetic fixture inputs.
        :param bindings: Installed names; ``create_guard`` is replaced.
        :param mirror_url: URL GitLab reports for the mirror.
        """
        self.inputs = inputs
        self.bindings = dataclasses.replace(bindings, create_guard=FakeGuard())
        self.gitlab = FakeApi(_ref_responses())
        self.github = FakeApi()
        self.source = FakeMirrorSource(self.gitlab, mirror_url=mirror_url)
        self.session = FakeGitHubSession(self.github)
        self.destination = FakeRepositoryService(self.session)
        self.gitlab.on_delete = self._remove_mirror
        self.github.on_delete = self._remove_destination
        self.labels: MutableSequence[str] = []

    def __call__(self, label: str) -> object:
        """Record the factory label and build a provider over these services.

        :param label: Output file stem of the provider's live plan.
        :returns: Native provider.
        """
        self.labels.append(label)
        return _native(self.inputs, self.bindings, self.source, self.destination)

    def _remove_mirror(self) -> None:
        """Make the next mirror read return provider not-found."""
        self.source.mirror_url = None

    def _remove_destination(self) -> None:
        """Make the next path and ID reads find no repository."""
        self.destination.repository_id = None
        self.session.present = False

    @property
    def deletes(self) -> tuple[tuple[str, str], ...]:
        """List the GitLab, then the GitHub, DELETE requests.

        :returns: Method and route of every DELETE.
        """
        return tuple(
            call
            for call in (*self.gitlab.calls, *self.github.calls)
            if call[0] == HTTPMethod.DELETE
        )


# The mirror delete by native ID, then the destination delete by path.
NATIVE_DELETES: Final[tuple[tuple[str, str], ...]] = (
    (
        HTTPMethod.DELETE,
        MIRROR_PROVIDER.MIRROR_ENDPOINT.format(project=SOURCE_ID, mirror=MIRROR_ID),
    ),
    (
        HTTPMethod.DELETE,
        REPOSITORY.REPOSITORY_ENDPOINT.format(reference=DESTINATION_PATH),
    ),
)


@pytest.mark.parametrize(
    "mirror_url",
    (MIRROR_URL_WITHOUT_SUFFIX, MIRROR_URL_WITH_SLASH),
    ids=("without-git-suffix", "trailing-slash"),
)
def test_run_cleanup_completes_when_gitlab_reports_another_url_form(
    inputs: object, bindings: object, mirror_url: str
) -> None:
    """Remove both objects when the mirror URL lacks ``.git`` or ends in ``/``.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :param mirror_url: URL GitLab reports for the run-created mirror.
    :returns: None; assertions verify the ledger, both deletes and the record.
    """
    services = NativeServices(inputs, bindings, mirror_url)

    ledger = _qualification(inputs, ScriptedRunner(_results()), services).run()

    assert ledger[QUALIFY.LEDGER_STATE_FIELD] == QUALIFY.CLEANUP_PASS
    assert services.labels == [QUALIFY.BASELINE_PLAN_STEP, QUALIFY.CLEANUP_PLAN_STEP]
    assert services.deletes == NATIVE_DELETES
    record = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert record[QUALIFY.DELETE_FIELD] == {
        QUALIFY.MIRROR_HTTP_STATUS_FIELD: HTTPStatus.NO_CONTENT,
        QUALIFY.DESTINATION_HTTP_STATUS_FIELD: HTTPStatus.NO_CONTENT,
    }


@pytest.mark.parametrize(
    "mirror_url",
    (MIRROR_URL_WITHOUT_SUFFIX, MIRROR_URL_WITH_SLASH),
    ids=("without-git-suffix", "trailing-slash"),
)
def test_reconcile_completes_when_gitlab_reports_another_url_form(
    inputs: object, bindings: object, mirror_url: str
) -> None:
    """Finish a pending cleanup when the mirror URL lacks ``.git`` or ends in ``/``.

    :param inputs: Synthetic fixture inputs.
    :param bindings: Installed names.
    :param mirror_url: URL GitLab reports for the run-created mirror.
    :returns: None; assertions verify the ledger, both deletes and the record.
    """
    _failed_run(inputs, lambda provider: setattr(provider, "keep_mirror", True))
    services = NativeServices(inputs, bindings, mirror_url)

    ledger = _qualification(inputs, ScriptedRunner(_results()), services).reconcile()

    assert ledger[QUALIFY.LEDGER_STATE_FIELD] == QUALIFY.CLEANUP_PASS
    assert ledger[QUALIFY.RECONCILED_FIELD] is True
    assert services.labels == [QUALIFY.RECONCILE_PLAN_STEP]
    assert services.deletes == NATIVE_DELETES
    record = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert record[QUALIFY.RECONCILED_FIELD] is True
    assert record[QUALIFY.DELETE_FIELD] == {
        QUALIFY.MIRROR_HTTP_STATUS_FIELD: HTTPStatus.NO_CONTENT,
        QUALIFY.DESTINATION_HTTP_STATUS_FIELD: HTTPStatus.NO_CONTENT,
    }


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

    assert status == HTTPStatus.NO_CONTENT
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

    assert status == HTTPStatus.NO_CONTENT
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
    assert output[QUALIFY.STATUS_FIELD] == STATUS.passed
    assert output[CLI.LEDGER_FIELD][QUALIFY.LEDGER_STATE_FIELD] == QUALIFY.CLEANUP_PASS
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
    assert output[QUALIFY.STATUS_FIELD] == STATUS.planned
    assert output[CLI.PLAN_FIELD][QUALIFY.LEDGER_PLAN_DIGEST_FIELD] == PLAN_DIGEST
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
        QUALIFY.STATUS_FIELD: STATUS.blocked,
        CLI.REASON_FIELD: _interrupted(QUALIFY.APPLY_PENDING),
    }
    assert _ledger(inputs)[QUALIFY.LEDGER_STATE_FIELD] == QUALIFY.APPLY_PENDING
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
            {QUALIFY.LIVE_PLAN_STEP: (0, _planned({FIELDS.destination: DESTINATION}))},
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
    assert _output(capsys) == {
        QUALIFY.STATUS_FIELD: STATUS.blocked,
        CLI.REASON_FIELD: reason,
    }
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
        QUALIFY.STATUS_FIELD: STATUS.blocked,
        CLI.REASON_FIELD: QUALIFY.NOTHING_TO_RECONCILE,
    }
    assert not missing.exists()


def test_main_reports_an_unavailable_installed_copy(
    inputs: object,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report BLOCKED from the repository's status module when ``lib`` cannot be bound.

    The first run reads ``ci-skills/lib/python/sys/status.py`` by path; the second
    replaces that module and the output follows it.

    :param inputs: Synthetic fixture inputs.
    :param monkeypatch: Replaces the adapter's bound names.
    :param capsys: Captured output.
    :returns: None; assertions verify exit code and output against each module.
    """
    reason = QUALIFY.INSTALLED_LIB_UNAVAILABLE.format(name=QUALIFY.CATALOG_MODULE)

    def unavailable(skill: Path) -> NoReturn:
        """Fail like a missing installed module.

        :param skill: Installed directory.
        :raises QUALIFY.QualificationError: Always.
        """
        raise QUALIFY.QualificationError(reason)

    monkeypatch.setattr(CLI, "installed_bindings", unavailable)

    code = CLI.main(_argv(inputs))

    assert code == SYS_STATUS.exit_code(SYS_STATUS.BLOCKED)
    assert _output(capsys) == {
        QUALIFY.STATUS_FIELD: SYS_STATUS.BLOCKED,
        CLI.REASON_FIELD: reason,
    }
    monkeypatch.setattr(
        CLI,
        "repository_status",
        lambda: SimpleNamespace(BLOCKED=FAKE_BLOCKED, exit_code=_fake_exit_code),
    )

    code = CLI.main(_argv(inputs))

    assert code == FAKE_EXIT_STOP
    assert _output(capsys) == {
        QUALIFY.STATUS_FIELD: FAKE_BLOCKED,
        CLI.REASON_FIELD: reason,
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
