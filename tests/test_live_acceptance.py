"""Tests that acceptance actually refuses a receipt it should refuse.

Mocked CI proves code behavior; it cannot prove access, because the fake
clients answer whatever the fixtures say. So acceptance consumes the receipt a
real host produced. Each test below is one mutation of a good receipt, and each
asserts the specific reason it is refused -- an acceptance step that only
checked "file exists and says PASS" would pass every one of them.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from conftest import REPO_ROOT, SCRIPT_ROOT, load_module

ACCEPTANCE = load_module(
    "check_live_acceptance", REPO_ROOT / "tools" / "check_live_acceptance.py"
)
SKILL_ROOT = SCRIPT_ROOT.parent
HOST = "declared-host.example.test"
NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _digest() -> str:
    ACCEPTANCE._redactor()
    from core.provenance import tree_digest

    return tree_digest(SKILL_ROOT)["digest"]


def _expected() -> dict:
    return {
        "max_receipt_age_days": 30,
        "required_live_checks": [
            "storage_report",
            "event_trace",
            "cilium_status",
            "ceph_cluster",
        ],
        "ceph_namespace": "selected-ceph",
        "required_checks": ["validate"],
        "targets": {
            "github": "github.com/owner/repository",
            "gitlab": "https://gitlab.example.test",
            "kubernetes": {
                "context": "unit-context",
                "server": "https://api.cluster.example.test:6443",
            },
        },
        "executors": [
            {
                "label": "declared",
                "host": HOST,
                "identities": {
                    "github": "unit-gh",
                    "gitlab": "unit-gl",
                    "kubernetes": "unit-admin",
                },
            }
        ],
    }


def _receipt() -> dict:
    checks = {
        name: {"status": "PASS", "access_proven": True}
        for name in ("storage_report", "event_trace", "cilium_status", "ceph_cluster")
    }
    checks["ceph_cluster"]["namespace"] = "selected-ceph"
    return {
        "schema_version": "1.0",
        "kind": "access_check",
        "status": "PASS",
        "publication": True,
        "execution_host": HOST,
        "captured_at": (NOW - timedelta(days=1)).isoformat(),
        "skill": {"digest": _digest()},
        "targets": _expected()["targets"],
        "surfaces": {
            "github": {
                "identity": "unit-gh",
                "details": {"required_checks": ["validate"]},
            },
            "gitlab": {"identity": "unit-gl"},
            "kubernetes": {"identity": "unit-admin"},
        },
        "live_checks": checks,
        "blocking_live_checks": [],
    }


def _evaluate(receipt: dict, expected: dict | None = None) -> dict:
    return ACCEPTANCE.evaluate(
        expected or _expected(),
        {"declared.json": receipt},
        SKILL_ROOT,
        now=NOW,
    )


def test_a_good_receipt_is_accepted():
    result = _evaluate(_receipt())

    assert result["status"] == "PASS", result["problems"]
    assert result["accepted"] == ["declared.json"]


def test_ceph_namespace_must_match_operator_selection():
    receipt = _receipt()
    receipt["live_checks"]["ceph_cluster"]["namespace"] = "another-cluster"

    result = _evaluate(receipt)

    assert result["status"] == "BLOCKED"
    assert "declared.json:ceph_namespace_mismatch" in result["problems"]


@pytest.mark.parametrize(
    ("mutate", "reason"),
    (
        (
            lambda r: r.update({"execution_host": "someone-else"}),
            "executor_not_declared",
        ),
        (lambda r: r.update({"status": "BLOCKED"}), "status_not_pass"),
        (lambda r: r.update({"publication": False}), "not_a_publication_receipt"),
        (lambda r: r.update({"kind": "storage_report"}), "kind_unexpected"),
        (lambda r: r.update({"schema_version": "2.0"}), "schema_version_unexpected"),
        (lambda r: r["skill"].update({"digest": "0" * 64}), "skill_digest_mismatch"),
        (lambda r: r.pop("skill"), "skill_digest_absent"),
        (
            lambda r: r["surfaces"]["kubernetes"].update({"identity": "someone"}),
            "identity_mismatch:kubernetes",
        ),
        (
            lambda r: r["targets"].update({"gitlab": "https://elsewhere.test"}),
            "targets_mismatch",
        ),
        (
            lambda r: r["live_checks"]["cilium_status"].update(
                {"access_proven": False}
            ),
            "live_check_unproven:cilium_status",
        ),
        (
            lambda r: r["live_checks"].pop("event_trace"),
            "live_check_missing:event_trace",
        ),
        (lambda r: r.update({"blocking_live_checks": ["x"]}), "live_checks_blocking"),
        (
            lambda r: r["surfaces"]["github"]["details"].update(
                {"required_checks": ["unrelated"]}
            ),
            "required_check_absent:validate",
        ),
        (
            lambda r: r.update({"captured_at": "2020-01-01T00:00:00+00:00"}),
            "receipt_stale",
        ),
        (lambda r: r.update({"captured_at": "nonsense"}), "captured_at_unreadable"),
        (
            lambda r: r.update({"captured_at": "2030-01-01T00:00:00+00:00"}),
            "captured_in_the_future",
        ),
    ),
)
def test_each_defect_is_refused_with_its_own_reason(mutate, reason):
    receipt = _receipt()
    mutate(receipt)

    result = _evaluate(receipt)

    assert result["status"] == "BLOCKED"
    assert f"declared.json:{reason}" in result["problems"]


def test_a_declared_executor_with_no_receipt_is_refused():
    result = ACCEPTANCE.evaluate(_expected(), {}, SKILL_ROOT, now=NOW)

    assert result["status"] == "BLOCKED"
    assert "declared:receipt_missing" in result["problems"]


def test_a_receipt_still_carrying_a_credential_is_refused():
    """A receipt that was never sanitized must not become acceptance evidence."""
    receipt = _receipt()
    receipt["surfaces"]["gitlab"]["note"] = "GITLAB_TOKEN=leaked-value"

    result = _evaluate(receipt)

    assert "declared.json:receipt_not_sanitized" in result["problems"]


def test_an_undeclared_extra_receipt_is_refused():
    """A receipt nobody declared cannot quietly satisfy acceptance."""
    result = ACCEPTANCE.evaluate(
        _expected(),
        {"declared.json": _receipt(), "stranger.json": {"execution_host": "stranger"}},
        SKILL_ROOT,
        now=NOW,
    )

    assert "stranger.json:executor_not_declared" in result["problems"]


def test_expectations_without_an_executor_block_rather_than_pass(tmp_path):
    path = tmp_path / "expected.toml"
    path.write_text("max_receipt_age_days = 30\n", encoding="utf-8")

    with pytest.raises(ACCEPTANCE.AcceptanceError, match="no_executors_declared"):
        ACCEPTANCE._load_expected(path)


def test_an_empty_receipt_directory_blocks(tmp_path):
    with pytest.raises(ACCEPTANCE.AcceptanceError, match="receipts_missing"):
        ACCEPTANCE._load_receipts(tmp_path)


def test_the_committed_receipt_set_accepts_this_revision():
    """The repository's own acceptance inputs must hold for this commit."""
    expected = ACCEPTANCE._load_expected(REPO_ROOT / "acceptance" / "expected.toml")
    receipts = ACCEPTANCE._load_receipts(REPO_ROOT / "acceptance" / "receipts")

    result = ACCEPTANCE.evaluate(expected, receipts, SKILL_ROOT)

    assert result["status"] == "PASS", result["problems"]


def test_no_committed_receipt_carries_a_host_path():
    """The committable form is what makes a real receipt publishable at all."""
    for path in (REPO_ROOT / "acceptance" / "receipts").glob("*.json"):
        body = json.dumps(json.loads(path.read_text(encoding="utf-8")))
        assert "/Users/" not in body, path.name
        assert "/home/" not in body, path.name
