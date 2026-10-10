"""Regression coverage for runtime-bound live acceptance receipts.

The initial access receipts establish the selected host, identity, target and
credential context.  Later GitLab operation receipts must prove that they ran
against that setup; an expectations file cannot supply a second set of values.
"""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.python.conftest import (
    REPO_ROOT,
    SCRIPT_ROOT,
    import_script_module,
    load_module,
)

ACCEPTANCE = load_module(
    "proposed_check_live_acceptance", REPO_ROOT / "tools" / "check_live_acceptance.py"
)
PORTABLE = import_script_module("core.portable")
SKILL_ROOT = SCRIPT_ROOT.parent
NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _digest() -> str:
    from core.provenance import tree_digest

    return tree_digest(SKILL_ROOT)["digest"]


def _access_setup() -> dict[str, dict]:
    """Create synthetic initial receipts; operation fixtures derive from them."""
    execution_host = "runtime-host.example.test"
    gitlab_target = {
        "kind": "project",
        "id": 712,
        "full_path": "runtime/team-repo",
    }
    access = {
        "schema_version": "1.0",
        "kind": "access_check",
        "status": "PASS",
        "publication": True,
        "execution_host": execution_host,
        "captured_at": (NOW - timedelta(days=1)).isoformat(),
        "tested_revision": "a" * 40,
        "skill": {
            "digest": _digest(),
            "revision": {"value": "a" * 40, "verified": True},
        },
        "targets": {
            "github": "github.example.test/runtime/repo",
            "gitlab": "https://gitlab.runtime.example.test",
            "kubernetes": {
                "context": "runtime-context",
                "server": "https://api.runtime.example.test:6443",
            },
        },
        "credential_sources": {
            "github": "gh-credential-store:github.example.test",
            "gitlab": "file:path:runtime",
            "kubernetes": "env:KUBECONFIG",
        },
        "surfaces": {
            "github": {
                "identity": "runtime-gh",
                "status": "PASS",
                "target": "github.example.test/runtime/repo",
                "credential_source": "gh-credential-store:github.example.test",
                "observed_capability": ["required_checks_read"],
                "details": {"required_checks": ["validate"]},
            },
            "gitlab": {
                "identity": "runtime-gl",
                "status": "PASS",
                "target": "https://gitlab.runtime.example.test",
                "credential_source": "file:path:runtime",
            },
            "kubernetes": {
                "identity": "runtime-k8s",
                "status": "PASS",
                "target": "runtime-context -> https://api.runtime.example.test:6443",
                "credential_source": "env:KUBECONFIG",
            },
        },
        "live_checks": {
            **{
                name: {
                    "status": "PASS",
                    "access_proven": True,
                    "readback_sha256": "f" * 64,
                }
                for name in ACCEPTANCE.REQUIRED_LIVE_CHECKS
            },
            "ceph_cluster": {
                "status": "PASS",
                "access_proven": True,
                "readback_sha256": "f" * 64,
                "namespace": "runtime-ceph",
            },
            "gitlab_job": {
                "status": "PASS",
                "access_proven": True,
                "readback_sha256": "e" * 64,
                "job_url": "https://gitlab.runtime.example.test/runtime/team-repo/-/jobs/42",
                "job_readback": {
                    "job_id": 42,
                    "pipeline_id": 7,
                    "runner_id": 3,
                    "trace_line_count": 0,
                },
            },
        },
        "blocking_live_checks": [],
    }
    gitlab = {
        "schema_version": "1.0",
        "kind": "gitlab_access",
        "status": "PASS",
        "execution_host": execution_host,
        "origin": "https://gitlab.runtime.example.test",
        "captured_at": (NOW - timedelta(days=1)).isoformat(),
        "skill": {
            "digest": _digest(),
            "revision": {"value": "a" * 40, "verified": True},
        },
        "identity": {"username": "runtime-gl", "id": 31},
        "target": gitlab_target,
        "credential_source": "file:path:runtime",
        "credential_digest": "sha256:" + "d" * 64,
        "target_source": "selected-runtime-target",
        "observed_capability": ["identity_read", "target_read"],
        "errors": [],
    }
    return {"access.json": access, "gitlab-access.json": gitlab}


def _context(setup: dict[str, dict]) -> dict:
    return ACCEPTANCE._gitlab_context(setup["gitlab-access.json"])


def _base_operation(
    setup: dict[str, dict], kind: str, operation: str, action: str | None
) -> dict:
    """Build a receipt solely from the synthetic initial GitLab access setup."""
    context = _context(setup)
    receipt = {
        "schema_version": "1.0",
        "kind": kind,
        "status": "PASS",
        "execution_host": context["execution_host"],
        "origin": context["origin"],
        "captured_at": (NOW - timedelta(days=1)).isoformat(),
        "skill": {
            "digest": _digest(),
            "revision": {"value": "a" * 40, "verified": True},
        },
        "identity": {"username": context["username"], "id": context["identity_id"]},
        "credential_source": context["credential_source"],
        "credential_digest": context["credential_digest"],
        "target_source": "selected-runtime-target",
        "operation": operation,
        "errors": [],
        "verified_target": {
            "kind": context["target_kind"],
            "id": context["target_id"],
            "full_path": context["target_path"],
        },
    }
    if (kind, operation) in ACCEPTANCE.READ_ONLY_GITLAB_OPERATIONS:
        records = [{"id": 7}]
        receipt.update(
            phase="READ",
            mutated=False,
            records=records,
            readback={"verified": True, "record_count": len(records)},
        )
        return receipt
    receipt.update(
        phase="APPLY",
        mutated=action == "APPLIED",
        result_action=action,
        plan_digest=f"digest:{kind}:{operation}",
        readback={"id": 7, "action": action, "verified": True},
    )
    return receipt


def _runner_create(setup: dict[str, dict]) -> dict:
    receipt = _base_operation(setup, "gitlab_runner", "create", "APPLIED")
    context = _context(setup)
    runner_id = receipt["readback"]["id"]
    receipt["smoke_cleanup"] = {
        "schema_version": "1.0",
        "kind": "gitlab_runner_smoke_cleanup",
        "status": "PASS",
        "captured_at": receipt["captured_at"],
        "execution_host": context["execution_host"],
        "origin": context["origin"],
        "credential_source": context["credential_source"],
        "skill_digest": _digest(),
        "tested_revision": "a" * 40,
        "runner_id": runner_id,
        "before": {"id": runner_id, "project_ids": [context["target_id"]]},
        "delete": {
            "method": "DELETE",
            "endpoint": f"runners/{runner_id}",
            "exit_code": 0,
        },
        "after": {
            "global_get_http_status": 404,
            "global_get_exit_code": 1,
            f"project_{context['target_id']}_absent": True,
        },
    }
    return receipt


def _runner_delete(setup: dict[str, dict], action: str) -> dict:
    receipt = _base_operation(setup, "gitlab_runner", "delete", action)
    runner_id = receipt["readback"]["id"]
    mutated = action == "APPLIED"
    record = {
        "id": runner_id,
        "action": action,
        "verified": True,
        "mutated": mutated,
        "before": {"id": runner_id} if mutated else None,
        "after": {"global_get_status": 404, "scoped_absent": True, "write_error": None},
    }
    receipt.update(
        plan={
            "resource_id": runner_id,
            "runner_snapshot": {"id": runner_id, "present": mutated},
        },
        records=[record],
        readback=record,
    )
    return receipt


def _complete_receipts(setup: dict[str, dict]) -> dict[str, dict]:
    """Produce every required operation; no test-local operation allowlist exists."""
    receipts = deepcopy(setup)
    for kind, operation, action in ACCEPTANCE.REQUIRED_GITLAB_OPERATIONS:
        if kind == "gitlab_access":
            continue
        name = f"{kind}-{operation}-{action or 'read'}.json"
        if kind == "gitlab_runner" and operation == "create":
            receipt = _runner_create(setup)
        elif kind == "gitlab_runner" and operation == "delete":
            receipt = _runner_delete(setup, action)
        else:
            receipt = _base_operation(setup, kind, operation, action)
        receipts[name] = receipt
    return receipts


def _evaluate(
    setup: dict[str, dict] | None = None, receipts: dict[str, dict] | None = None
) -> dict:
    initial = setup or _access_setup()
    return ACCEPTANCE.evaluate(
        initial,
        receipts or _complete_receipts(initial),
        SKILL_ROOT,
        now=NOW,
    )


def test_full_required_operation_set_is_bound_to_initial_runtime_setup():
    setup = _access_setup()
    receipts = _complete_receipts(setup)

    result = _evaluate(setup, receipts)

    assert result["status"] == "PASS", result["problems"]
    assert set(result["accepted"]) == set(receipts)
    assert {
        (receipt.get("kind"), receipt.get("operation"), receipt.get("result_action"))
        for receipt in receipts.values()
        if receipt.get("kind") != "access_check"
    } == ACCEPTANCE.REQUIRED_GITLAB_OPERATIONS


def test_operation_identity_must_match_the_initial_synthetic_setup():
    setup = _access_setup()
    receipts = _complete_receipts(setup)
    operation = receipts["gitlab_issue-open-bug-APPLIED.json"]
    operation["identity"] = {"username": "different-user", "id": 99}

    result = _evaluate(setup, receipts)

    assert result["status"] == "BLOCKED"
    assert "gitlab_issue-open-bug-APPLIED.json:identity_mismatch" in result["problems"]


def test_operation_target_cannot_switch_away_from_initial_synthetic_setup():
    setup = _access_setup()
    receipts = _complete_receipts(setup)
    operation = receipts["gitlab_issue-open-bug-APPLIED.json"]
    operation["verified_target"] = {
        "kind": "project",
        "id": 999,
        "full_path": "other/project",
    }

    result = _evaluate(setup, receipts)

    assert result["status"] == "BLOCKED"
    assert (
        "gitlab_issue-open-bug-APPLIED.json:setup_context_missing_or_ambiguous"
        in result["problems"]
    )


@pytest.mark.parametrize(
    ("mutate", "reason"),
    (
        (
            lambda setup: setup["access.json"].update(status="BLOCKED"),
            "status_not_pass",
        ),
        (
            lambda setup: setup["access.json"].update(captured_at="nonsense"),
            "captured_at_unreadable",
        ),
        (
            lambda setup: setup["access.json"]["live_checks"].pop("event_trace"),
            "live_check_missing:event_trace",
        ),
        (
            lambda setup: setup["access.json"].update(blocking_live_checks=["blocked"]),
            "live_checks_blocking",
        ),
        (
            lambda setup: setup["gitlab-access.json"].update(credential_digest=""),
            "credential_source_unresolved",
        ),
    ),
)
def test_setup_receipts_preserve_freshness_and_shape_checks(mutate, reason):
    setup = _access_setup()
    mutate(setup)

    result = _evaluate(setup)

    assert result["status"] == "BLOCKED"
    name = (
        "gitlab-access.json"
        if reason == "credential_source_unresolved"
        else "access.json"
    )
    assert f"{name}:{reason}" in result["problems"]


def test_repeat_operations_require_matching_plan_and_resource():
    setup = _access_setup()
    receipts = _complete_receipts(setup)
    receipts["gitlab_issue-open-bug-NO_OP.json"]["plan_digest"] = "changed"

    result = _evaluate(setup, receipts)

    assert (
        "gitlab_issue-open-bug-APPLIED.json:gitlab_issue-open-bug-NO_OP.json:repeat_readback_mismatch"
        in result["problems"]
    )


def test_runner_cleanup_and_delete_readback_remain_required():
    setup = _access_setup()
    receipts = _complete_receipts(setup)
    receipts["gitlab_runner-create-APPLIED.json"]["smoke_cleanup"]["after"][
        f"project_{_context(setup)['target_id']}_absent"
    ] = False
    receipts["gitlab_runner-delete-APPLIED.json"]["readback"]["after"][
        "scoped_absent"
    ] = False

    result = _evaluate(setup, receipts)

    assert (
        "gitlab_runner-create-APPLIED.json:runner_smoke_cleanup_unproven"
        in result["problems"]
    )
    assert (
        "gitlab_runner-delete-APPLIED.json:runner_delete_readback_unproven"
        in result["problems"]
    )


def test_missing_or_undeclared_operation_receipt_blocks_without_local_allowlist():
    setup = _access_setup()
    receipts = _complete_receipts(setup)
    del receipts["gitlab_runner-list-read.json"]
    receipts["unexpected.json"] = _base_operation(
        setup, "gitlab_issue", "other", "APPLIED"
    )

    result = _evaluate(setup, receipts)

    assert "gitlab_runner:list:check:receipt_missing" in result["problems"]
    assert "unexpected.json:receipt_not_declared" in result["problems"]


def test_access_setup_receipts_cannot_be_replaced_in_the_operation_set():
    setup = _access_setup()
    receipts = _complete_receipts(setup)
    receipts["gitlab-access.json"]["identity"]["id"] = 99

    result = _evaluate(setup, receipts)

    assert "gitlab-access.json:setup_receipt_mismatch" in result["problems"]


def test_real_operation_envelope_keeps_cleanup_shape(tmp_path):
    actions = import_script_module("core.gitlab_actions")
    portable = import_script_module("core.portable")
    plan = actions.ActionPlan(
        kind="gitlab_runner",
        operation="create",
        origin="https://gitlab.runtime.example.test",
        target_kind="project",
        target_reference="runtime/team-repo",
        target_file="/selected/target.toml",
        target_source="selected-runtime-target",
        body={"runner_type": "project_type"},
        token_out="/selected/runner-secret",
    )
    record = {"action": "APPLIED", "id": 9, "verified": True, "sink_persisted": True}
    report = actions._result(
        plan,
        "PASS",
        phase="APPLY",
        mutated=True,
        result_action="APPLIED",
        skill={"digest": _digest(), "revision": {"value": "a" * 40, "verified": True}},
        identity={"username": "runtime-gl", "id": 31},
        verified_target={
            "kind": "project",
            "id": 712,
            "full_path": "runtime/team-repo",
        },
        credential_source="env:GITLAB_TOKEN",
        credential_digest="sha256:" + "d" * 64,
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
        "username": report["identity"]["username"],
        "identity_id": report["identity"]["id"],
        "target_kind": report["verified_target"]["kind"],
        "target_id": report["verified_target"]["id"],
        "target_path": report["verified_target"]["full_path"],
        "credential_source": report["credential_source"],
        "credential_digest": report["credential_digest"],
    }

    assert receipt["plan"]["one_time_sink_required"] is True
    assert ACCEPTANCE._check_gitlab_receipt(
        path.name, receipt, required, _digest(), datetime.now(UTC)
    ) == ["runner.json:runner_smoke_cleanup_unproven"]


def _commit_current_index(repository: Path, message: str) -> str:
    tree = subprocess.check_output(
        ["git", "-C", str(repository), "write-tree"], text=True
    ).strip()
    return subprocess.check_output(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit-tree",
            tree,
            "-m",
            message,
        ],
        text=True,
    ).strip()


def _candidate_identity_receipt(revision: str, skill_root: Path) -> dict:
    from core.provenance import tree_digest

    return {
        "tested_revision": revision,
        "skill": {
            **tree_digest(skill_root),
            "revision": {"value": revision, "verified": True},
        },
    }


def _sibling_skill_commits(tmp_path) -> tuple[Path, Path, str, str]:
    repository = tmp_path / "repository"
    skill_root = repository / "ci-skills"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("fixture skill\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "add", "ci-skills/SKILL.md"], check=True
    )
    tested = _commit_current_index(repository, "tested skill")
    candidate = _commit_current_index(repository, "squash candidate")
    subprocess.run(
        ["git", "-C", str(repository), "update-ref", "refs/heads/main", candidate],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "checkout", "-q", candidate], check=True
    )
    return repository, skill_root, tested, candidate


def test_candidate_identity_accepts_same_skill_tree_from_nonancestor_commit(tmp_path):
    repository, skill_root, tested, candidate = _sibling_skill_commits(tmp_path)
    receipt = _candidate_identity_receipt(tested, skill_root)
    assert (
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "merge-base",
                "--is-ancestor",
                tested,
                candidate,
            ],
            check=False,
        ).returncode
        == 1
    )
    assert ACCEPTANCE.check_candidate_identity(receipt, repository, skill_root) == []
    (skill_root / "SKILL.md").write_text("different\n", encoding="utf-8")
    assert ACCEPTANCE.check_candidate_identity(receipt, repository, skill_root) == [
        "candidate_skill_digest_mismatch"
    ]


def test_candidate_identity_rejects_different_tree_and_unavailable_source(tmp_path):
    repository, skill_root, tested, _candidate = _sibling_skill_commits(tmp_path)
    receipt = _candidate_identity_receipt(tested, skill_root)
    (skill_root / "SKILL.md").write_text("different fixture skill\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "ci-skills/SKILL.md"], check=True
    )
    candidate = _commit_current_index(repository, "different skill")
    subprocess.run(
        ["git", "-C", str(repository), "update-ref", "refs/heads/main", candidate],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "checkout", "-q", candidate], check=True
    )
    assert ACCEPTANCE.check_candidate_identity(receipt, repository, skill_root) == [
        "candidate_skill_tree_mismatch"
    ]
    receipt["tested_revision"] = "0" * 40
    receipt["skill"]["revision"]["value"] = "0" * 40
    assert ACCEPTANCE.check_candidate_identity(receipt, repository, skill_root) == [
        "candidate_revision_unavailable"
    ]


def test_cli_reports_receipt_loading_shape_without_an_expectations_file(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        sys, "argv", ["check_live_acceptance.py", "--root", str(tmp_path), "--json"]
    )

    assert ACCEPTANCE.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "BLOCKED"
    assert report["kind"] == "live_acceptance"
    assert report["problems"] == [
        f"receipt_directory_missing:{tmp_path / 'tests/acceptance/receipts'}"
    ]


def test_no_committed_receipt_carries_a_host_path():
    for path in (REPO_ROOT / "tests" / "acceptance").rglob("*.json"):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        assert PORTABLE.portable(receipt) == receipt, path.name
