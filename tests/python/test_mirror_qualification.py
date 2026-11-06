"""Offline tests for the bounded mirror qualification tool.

`tools/skillkit/mirror_qualification.py` runs the installed mirror command and
removes only the objects that run created. These tests drive it with a scripted
command runner and a fake provider, so no provider or process is contacted.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, MutableSequence, Sequence
from pathlib import Path
from typing import Final, Optional

import pytest
from conftest import REPO_ROOT, load_module

QUALIFY = load_module(
    "skillkit_mirror_qualification",
    REPO_ROOT / "tools" / "skillkit" / "mirror_qualification.py",
)
ACCEPTANCE = load_module(
    "check_live_acceptance_for_mirror_tool",
    REPO_ROOT / "tools" / "check_live_acceptance.py",
)

CANDIDATE: Final[str] = "c" * 40
SOURCE: Final[dict[str, object]] = {
    "id": 261,
    "host": "gitlab.example.test",
    "path": "unit/mirror-source",
}
DESTINATION_PATH: Final[str] = "unit/mirror-destination"
DESTINATION: Final[dict[str, object]] = {
    "id": 9001,
    "host": "github.example.test",
    "path": DESTINATION_PATH,
}
MIRROR: Final[dict[str, object]] = {"id": 15}
SEED: Final[str] = "a" * 40
REFS: Final[dict[str, object]] = {"count": 2, "digest": "unit"}
PLAN_DIGEST: Final[str] = "b" * 64
CAPTURED: Final[str] = "2026-11-06T00:00:00+00:00"


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
        source_id=int(SOURCE["id"]),
        seed_refs=(
            QUALIFY.SeedRef("branches", "main", SEED),
            QUALIFY.SeedRef("tags", "smoke-seed", SEED),
        ),
        run_id="unit-run",
        settle_seconds=0,
    )


class ScriptedRunner(QUALIFY.CommandRunner):
    """Return one scripted result per step and write receipts like the command."""

    def __init__(self, results: Mapping[str, tuple[int, dict[str, object]]]):
        """Keep the scripted results.

        :param results: Exit code and result per step label.
        """
        self.results = results
        self.calls: MutableSequence[tuple[str, tuple[str, ...]]] = []

    def run(self, label: str, options: Sequence[str]) -> tuple[int, object]:
        """Record the call, write a requested receipt and return the scripted result.

        :param label: Step label.
        :param options: Mode options.
        :returns: Scripted exit code and result.
        """
        self.calls.append((label, tuple(options)))
        code, data = self.results[label]
        if "--receipt-out" in options:
            path = Path(options[options.index("--receipt-out") + 1])
            path.write_text(json.dumps(data), encoding="utf-8")
        return code, data


class FakeProvider(QUALIFY.MirrorProvider):
    """Hold one mirror and one destination and record every deletion."""

    def __init__(self) -> None:
        """Start with both run-created objects present."""
        self.mirror_present = True
        self.destination_present = True
        self.deletes: MutableSequence[str] = []
        self.mirror_delete_status = 204
        self.keep_mirror = False
        self.destination_override: Optional[dict[str, object]] = None

    def source(self) -> object:
        """Return the retained source evidence.

        :returns: Source evidence.
        """
        return dict(SOURCE)

    def ref_commit(self, ref: object) -> str:
        """Return the seed commit for every retained ref.

        :param ref: Retained source ref.
        :returns: Seed commit.
        """
        return SEED

    def mirror_targets(self, mirror_id: int, destination: str) -> Optional[bool]:
        """Report the mirror while present.

        :param mirror_id: Native mirror ID.
        :param destination: Destination path.
        :returns: True while present, otherwise None.
        """
        return True if self.mirror_present else None

    def delete_mirror(self, mirror_id: int, destination: str) -> int:
        """Record and apply one mirror deletion.

        :param mirror_id: Native mirror ID.
        :param destination: Destination path.
        :returns: Configured HTTP status.
        """
        self.deletes.append(f"mirror:{mirror_id}")
        if not self.keep_mirror:
            self.mirror_present = False
        return self.mirror_delete_status

    def destination(self, path: str) -> Optional[object]:
        """Return the destination evidence while present.

        :param path: Destination path.
        :returns: Evidence, or None when absent.
        """
        if not self.destination_present:
            return None
        return self.destination_override or dict(DESTINATION)

    def destination_id_present(self, repository_id: int) -> bool:
        """Report the destination ID while present.

        :param repository_id: Native repository ID.
        :returns: Whether present.
        """
        return self.destination_present

    def delete_destination(self, path: str) -> int:
        """Record and apply one destination deletion.

        :param path: Destination path.
        :returns: HTTP 204.
        """
        self.deletes.append(f"destination:{path}")
        self.destination_present = False
        return 204


def _results(
    **overrides: tuple[int, dict[str, object]],
) -> dict[str, tuple[int, dict[str, object]]]:
    """Build the scripted results of a successful run with optional overrides.

    :param **overrides: Replacement result per step label.
    :returns: Exit code and result per step label.
    """
    planned = {
        "status": "PLANNED",
        "plan_digest": PLAN_DIGEST,
        "records": [
            {
                "source": SOURCE,
                "destination": {"id": None, "path": DESTINATION_PATH},
                "mirror": None,
            }
        ],
    }
    created = {
        "action": "APPLIED",
        "source": SOURCE,
        "destination": DESTINATION,
        "mirror": MIRROR,
        "created": {"mirror": True, "repository": True},
        "before": {"mirror": None},
        "replacement_required": False,
        "source_refs": REFS,
        "destination_refs": REFS,
    }
    receipt = {
        "captured_at": CAPTURED,
        "execution_host": "unit-host",
        "tested_revision": CANDIDATE,
        "skill": {
            "digest": "unit-digest",
            "revision": {"value": CANDIDATE, "verified": True},
        },
    }
    results = {
        "mirror-live-plan": (0, planned),
        "mirror-rejection": (2, {"status": "BLOCKED", "mutated": False}),
        "mirror-applied": (
            0,
            {**receipt, "status": "PASS", "mutated": True, "records": [created]},
        ),
        "mirror-noop-plan": (
            0,
            {**planned, "records": [{**created, "action": "NO_OP"}]},
        ),
        "mirror-noop": (
            0,
            {
                **receipt,
                "status": "PASS",
                "mutated": False,
                "records": [{**created, "action": "NO_OP"}],
            },
        ),
    }
    results.update(overrides)
    return results


def test_qualification_removes_only_created_objects_and_records_cleanup(
    inputs: object,
) -> None:
    """Run every step once and write a cleanup record the checker accepts.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify steps, deletions, ledger and record.
    """
    runner = ScriptedRunner(_results())
    provider = FakeProvider()

    ledger = QUALIFY.MirrorQualification(inputs, runner, lambda: provider).run()

    assert [label for label, _ in runner.calls] == [
        "mirror-live-plan",
        "mirror-rejection",
        "mirror-applied",
        "mirror-noop-plan",
        "mirror-noop",
    ]
    assert runner.calls[2][1][:3] == ("--apply", "--confirm-plan", PLAN_DIGEST)
    assert provider.deletes == ["mirror:15", f"destination:{DESTINATION_PATH}"]
    assert ledger["cleanup"] == "PASS_INDEPENDENT_ABSENCE_AND_REPEAT"
    assert ledger["candidate"] == CANDIDATE
    cleanup = json.loads((inputs.work_dir / QUALIFY.CLEANUP_RECORD).read_text())
    assert cleanup["before"] == {
        "source": SOURCE,
        "destination": DESTINATION,
        "mirror_id": 15,
    }
    assert cleanup["repeat"]["mutated"] is False
    applied = json.loads((inputs.work_dir / QUALIFY.APPLIED_RECEIPT).read_text())
    noop = json.loads((inputs.work_dir / QUALIFY.NOOP_WITH_CLEANUP).read_text())
    now = ACCEPTANCE._parse_time(cleanup["captured_at"])
    assert ACCEPTANCE._mirror_smoke_cleanup_verified(applied, noop, now, 30) is True


@pytest.mark.parametrize(
    ("overrides", "reason"),
    (
        (
            {
                "mirror-live-plan": (
                    0,
                    {
                        "status": "PLANNED",
                        "plan_digest": PLAN_DIGEST,
                        "records": [
                            {
                                "source": SOURCE,
                                "destination": DESTINATION,
                                "mirror": None,
                            }
                        ],
                    },
                )
            },
            "fixture_not_clean",
        ),
        (
            {"mirror-rejection": (2, {"status": "BLOCKED", "mutated": True})},
            "rejection_mutated",
        ),
        (
            {"mirror-applied": (2, {"status": "BLOCKED"})},
            "mirror-applied_unexpected:2:BLOCKED",
        ),
    ),
    ids=("destination-already-present", "rejection-mutated", "apply-blocked"),
)
def test_qualification_stops_before_cleanup_on_an_unexpected_step(
    inputs: object, overrides: dict[str, tuple[int, dict[str, object]]], reason: str
) -> None:
    """Stop at the first unexpected result and delete nothing.

    :param inputs: Synthetic fixture inputs.
    :param overrides: Step results that break one expectation.
    :param reason: Exact stopping reason.
    :returns: None; assertions verify the reason and no deletion.
    """
    provider = FakeProvider()

    with pytest.raises(QUALIFY.QualificationError, match=f"^{reason}$"):
        QUALIFY.MirrorQualification(
            inputs, ScriptedRunner(_results(**overrides)), lambda: provider
        ).run()

    assert provider.deletes == []


def test_existing_ledger_blocks_a_second_run(inputs: object) -> None:
    """Refuse to start while an earlier run's ledger is unreconciled.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify no command ran.
    """
    (inputs.work_dir / QUALIFY.ALLOCATION_FILE).write_text("{}", encoding="utf-8")
    runner = ScriptedRunner(_results())

    with pytest.raises(QUALIFY.QualificationError, match="allocation_exists"):
        QUALIFY.MirrorQualification(inputs, runner, FakeProvider).run()

    assert runner.calls == []


@pytest.mark.parametrize(
    ("change", "reason", "deletes"),
    (
        ("keep_mirror", "mirror_deletion_unverified", ["mirror:15"]),
        ("destination_override", "destination_identity_changed", []),
    ),
    ids=("mirror-still-present", "destination-identity-changed"),
)
def test_cleanup_stops_when_an_identity_or_deletion_is_unproven(
    inputs: object, change: str, reason: str, deletes: list[str]
) -> None:
    """Never delete the destination unless the mirror is gone and identities match.

    :param inputs: Synthetic fixture inputs.
    :param change: Provider state that breaks one cleanup invariant.
    :param reason: Exact stopping reason.
    :param deletes: Deletions expected before the stop.
    :returns: None; assertions verify the reason and the deletions made.
    """
    provider = FakeProvider()
    if change == "keep_mirror":
        provider.keep_mirror = True
    else:
        provider.destination_override = {**DESTINATION, "id": 9002}

    with pytest.raises(QUALIFY.QualificationError, match=f"^{reason}$"):
        QUALIFY.MirrorQualification(
            inputs, ScriptedRunner(_results()), lambda: provider
        ).run()

    assert provider.deletes == deletes
    ledger = json.loads((inputs.work_dir / QUALIFY.ALLOCATION_FILE).read_text())
    assert ledger["cleanup"] != "PASS_INDEPENDENT_ABSENCE_AND_REPEAT"


def test_receipt_from_another_candidate_is_rejected(inputs: object) -> None:
    """Bind the receipts to the installed copy's verified revision.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify no cleanup ran.
    """
    results = _results()
    code, noop = results["mirror-noop"]
    results["mirror-noop"] = (code, {**noop, "tested_revision": "d" * 40})
    provider = FakeProvider()

    with pytest.raises(QUALIFY.QualificationError, match="receipt_candidate_mismatch"):
        QUALIFY.MirrorQualification(
            inputs, ScriptedRunner(results), lambda: provider
        ).run()

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
    """Pass ``--gitlab`` through to the installed command when selected.

    :param inputs: Synthetic fixture inputs.
    :returns: None; assertions verify the fixed selection.
    """
    selected = dataclasses.replace(inputs, gitlab="private")

    assert inputs.command_selection()[:5] == (
        "sync",
        "--project",
        str(SOURCE["path"]),
        "--destination",
        DESTINATION_PATH,
    )
    assert "--gitlab" not in inputs.command_selection()
    assert selected.command_selection()[-2:] == ("--gitlab", "private")
