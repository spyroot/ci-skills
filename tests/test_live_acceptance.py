"""Tests that acceptance actually refuses a receipt it should refuse.

Mocked CI proves code behavior; it cannot prove access, because the fake
clients answer whatever the fixtures say. So acceptance consumes the receipt a
real host produced. Each test below is one mutation of a good receipt, and each
asserts the specific reason it is refused -- an acceptance step that only
checked "file exists and says PASS" would pass every one of them.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

import pytest
from conftest import REPO_ROOT, SCRIPT_ROOT, import_script_module, load_module

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


def test_real_operation_envelope_round_trips_through_portable_receipt(tmp_path):
    actions = import_script_module("core.gitlab_actions")
    portable = import_script_module("core.portable")
    plan = actions.ActionPlan(
        kind="gitlab_runner",
        operation="create",
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/repo",
        target_file="/selected/target.toml",
        target_source="argv:--target;target:gitlab.project",
        body={"runner_type": "project_type", "description": "unit-runner"},
        token_out="/selected/runner-secret",
    )
    target = {"kind": "project", "id": 42, "full_path": "team/repo"}
    record = {"action": "APPLIED", "id": 9, "verified": True, "sink_persisted": True}
    report = actions._result(
        plan,
        "PASS",
        phase="APPLY",
        mutated=True,
        result_action="APPLIED",
        skill={"digest": _digest()},
        identity={"username": "unit"},
        verified_target=target,
        credential_source="env:GITLAB_TOKEN",
        credential_digest="a" * 64,
        readback=record,
        records=[record],
        cleanup={"status": "NOT_PERFORMED"},
    )
    path = tmp_path / "runner.json"
    portable.write_portable_receipt(report, str(path))
    receipt = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "kind": "gitlab_runner",
        "operation": "create",
        "result_action": "APPLIED",
        "execution_host": report["execution_host"],
        "origin": plan.origin,
        "username": "unit",
        "target_kind": "project",
        "target_id": 42,
        "target_path": "team/repo",
    }

    assert receipt["plan"]["one_time_sink_required"] is True
    assert receipt["readback"]["sink_persisted"] is True
    assert (
        ACCEPTANCE._check_gitlab_receipt(
            path.name,
            receipt,
            required,
            {"max_receipt_age_days": 30},
            _digest(),
            datetime.now(timezone.utc),
        )
        == []
    )


def test_an_undeclared_extra_receipt_is_refused():
    """A receipt nobody declared cannot quietly satisfy acceptance."""
    result = ACCEPTANCE.evaluate(
        _expected(),
        {"declared.json": _receipt(), "stranger.json": {"execution_host": "stranger"}},
        SKILL_ROOT,
        now=NOW,
    )

    assert "stranger.json:executor_not_declared" in result["problems"]


def _gitlab_expectations() -> dict:
    expected = _expected()
    shared = {
        "execution_host": HOST,
        "origin": "https://gitlab.example.test",
        "username": "unit-gl",
        "target_kind": "project",
        "target_id": 12,
        "target_path": "unit/repo",
    }
    expected["gitlab_receipts"] = [
        {"label": "operation-access", "kind": "gitlab_access", **shared},
        {
            "label": "bug-create",
            "kind": "gitlab_issue",
            "operation": "open-bug",
            "result_action": "APPLIED",
            **shared,
        },
        {
            "label": "bug-repeat",
            "kind": "gitlab_issue",
            "operation": "open-bug",
            "result_action": "NO_OP",
            **shared,
        },
    ]
    return expected


def _gitlab_receipts() -> dict[str, dict]:
    common = {
        "schema_version": "1.0",
        "status": "PASS",
        "execution_host": HOST,
        "captured_at": (NOW - timedelta(days=1)).isoformat(),
        "skill": {"digest": _digest()},
        "origin": "https://gitlab.example.test",
        "identity": {"username": "unit-gl"},
        "credential_source": "env:GITLAB_TOKEN",
        "credential_digest": "sha256:unit-digest",
        "target_source": "argv:--target;argv:--project",
    }
    target = {"kind": "project", "id": 12, "full_path": "unit/repo"}
    return {
        "access.json": {
            **common,
            "kind": "gitlab_access",
            "target": target,
            "observed_capability": ["identity_read", "target_read"],
        },
        "bug.json": {
            **common,
            "kind": "gitlab_issue",
            "operation": "open-bug",
            "phase": "APPLY",
            "mutated": True,
            "result_action": "APPLIED",
            "plan_digest": "a" * 64,
            "verified_target": target,
            "readback": {"iid": 4, "action": "APPLIED", "verified": True},
            "errors": [],
        },
        "bug-repeat.json": {
            **common,
            "kind": "gitlab_issue",
            "operation": "open-bug",
            "phase": "APPLY",
            "mutated": False,
            "result_action": "NO_OP",
            "plan_digest": "a" * 64,
            "verified_target": target,
            "readback": {"iid": 4, "action": "NO_OP", "verified": True},
            "errors": [],
        },
    }


def test_same_host_diagnostic_and_gitlab_operation_receipts_are_all_required():
    receipts = {"declared.json": _receipt(), **_gitlab_receipts()}
    result = ACCEPTANCE.evaluate(_gitlab_expectations(), receipts, SKILL_ROOT, now=NOW)

    assert result["status"] == "PASS", result["problems"]
    assert result["accepted"] == [
        "access.json",
        "bug-repeat.json",
        "bug.json",
        "declared.json",
    ]


def test_missing_or_wrong_target_operation_receipt_blocks():
    receipts = {"declared.json": _receipt(), **_gitlab_receipts()}
    receipts["bug.json"]["verified_target"]["id"] = 99
    result = ACCEPTANCE.evaluate(_gitlab_expectations(), receipts, SKILL_ROOT, now=NOW)
    assert "bug.json:target_mismatch" in result["problems"]

    del receipts["bug.json"]
    result = ACCEPTANCE.evaluate(_gitlab_expectations(), receipts, SKILL_ROOT, now=NOW)
    assert "bug-create:receipt_missing" in result["problems"]


def test_same_operation_on_two_exact_targets_uses_two_receipts():
    expected = _gitlab_expectations()
    for item in expected["gitlab_receipts"][1:]:
        second = dict(item)
        second.update(label="bug-other", target_id=24, target_path="unit/other")
        expected["gitlab_receipts"].append(second)
    receipts = {"declared.json": _receipt(), **_gitlab_receipts()}
    another = json.loads(json.dumps(receipts["bug.json"]))
    another["verified_target"].update(id=24, full_path="unit/other")
    receipts["bug-other.json"] = another
    another_repeat = json.loads(json.dumps(receipts["bug-repeat.json"]))
    another_repeat["verified_target"].update(id=24, full_path="unit/other")
    receipts["bug-other-repeat.json"] = another_repeat

    result = ACCEPTANCE.evaluate(expected, receipts, SKILL_ROOT, now=NOW)

    assert result["status"] == "PASS", result["problems"]
    assert len(result["accepted"]) == 6


def test_repeated_operation_must_bind_same_plan_and_resource():
    receipts = {"declared.json": _receipt(), **_gitlab_receipts()}
    receipts["bug-repeat.json"]["plan_digest"] = "different"

    result = ACCEPTANCE.evaluate(_gitlab_expectations(), receipts, SKILL_ROOT, now=NOW)

    assert result["status"] == "BLOCKED"
    assert "bug.json:bug-repeat.json:repeat_readback_mismatch" in result["problems"]


def test_read_only_live_plan_cannot_satisfy_mutation_acceptance():
    receipts = {"declared.json": _receipt(), **_gitlab_receipts()}
    receipts["bug.json"]["phase"] = "LIVE_PLAN"
    receipts["bug.json"]["mutated"] = False

    result = ACCEPTANCE.evaluate(_gitlab_expectations(), receipts, SKILL_ROOT, now=NOW)

    assert result["status"] == "BLOCKED"
    assert "bug.json:operation_readback_missing" in result["problems"]


def test_unexpected_same_host_operation_receipt_blocks():
    result = ACCEPTANCE.evaluate(
        _expected(),
        {"declared.json": _receipt(), **_gitlab_receipts()},
        SKILL_ROOT,
        now=NOW,
    )
    assert result["status"] == "BLOCKED"
    assert "access.json:receipt_not_declared" in result["problems"]


def test_expectations_without_an_executor_block_rather_than_pass(tmp_path):
    path = tmp_path / "expected.toml"
    path.write_text("max_receipt_age_days = 30\n", encoding="utf-8")

    with pytest.raises(ACCEPTANCE.AcceptanceError, match="no_executors_declared"):
        ACCEPTANCE._load_expected(path)


def test_gitlab_operation_receipts_are_mandatory_in_acceptance_inputs(tmp_path):
    path = tmp_path / "expected.toml"
    path.write_text(
        "max_receipt_age_days = 30\n"
        'required_live_checks = ["storage_report"]\n'
        'required_checks = ["validate"]\n'
        "[targets]\n"
        'github = "github.com/unit/repo"\n'
        "[[executors]]\n"
        'host = "unit-host"\n',
        encoding="utf-8",
    )

    with pytest.raises(
        ACCEPTANCE.AcceptanceError,
        match="expectation_not_declared:gitlab_receipts",
    ):
        ACCEPTANCE._load_expected(path)

    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            "[[gitlab_receipts]]\n"
            'kind = "gitlab_access"\n'
            'execution_host = "unit-host"\n'
            'origin = "https://gitlab.example.test"\n'
            'username = "unit"\n'
            'target_kind = "project"\n'
            "target_id = 1\n"
            'target_path = "unit/repo"\n'
        )

    with pytest.raises(
        ACCEPTANCE.AcceptanceError,
        match="gitlab_receipt_expectation_missing:",
    ):
        ACCEPTANCE._load_expected(path)


def test_an_empty_receipt_directory_blocks(tmp_path):
    with pytest.raises(ACCEPTANCE.AcceptanceError, match="receipts_missing"):
        ACCEPTANCE._load_receipts(tmp_path)


def test_missing_expectations_emits_structured_json_failure(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        sys, "argv", ["check_live_acceptance.py", "--root", str(tmp_path), "--json"]
    )

    assert ACCEPTANCE.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "BLOCKED"
    assert report["kind"] == "live_acceptance"
    assert report["problems"][0].startswith("expectations_unreadable:")


def test_no_committed_receipt_carries_a_host_path():
    """The committable form is what makes a real receipt publishable at all."""
    for path in (REPO_ROOT / "acceptance" / "receipts").glob("*.json"):
        body = json.dumps(json.loads(path.read_text(encoding="utf-8")))
        assert "/Users/" not in body, path.name
        assert "/home/" not in body, path.name
