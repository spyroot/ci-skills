"""Offline tests for live receipt acceptance checks."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from conftest import import_script_module

REVISION = "a" * 40
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _skill_tree(tmp_path: Path) -> Path:
    """Create a tiny synthetic skill tree for deterministic digest checks."""
    root = tmp_path / "skill"
    scripts = root / "scripts"
    references = root / "references"
    scripts.mkdir(parents=True)
    references.mkdir()
    (root / "SKILL.md").write_text("name: unit\n", encoding="utf-8")
    (scripts / "access_check.py").write_text("print('unit')\n", encoding="utf-8")
    (references / "access.md").write_text("unit reference\n", encoding="utf-8")
    return root


def _expected() -> dict[str, Any]:
    return {
        "execution_host": "unit-runner.example.test",
        "github_host": "github.example.test",
        "gitlab_url": "https://gitlab.example.test",
        "kubernetes_context": "unit-context",
        "kubernetes_server": "https://api.cluster.example.test:6443",
        "required_checks": ["validate"],
        "job_url": "https://gitlab.example.test/unit/repo/-/jobs/123",
    }


def _live_check(status: str = "PASS", *, proven: bool = True) -> dict[str, Any]:
    return {
        "status": status,
        "record_count": 1,
        "error_count": 0,
        "errors": [],
        "readback_sha256": "0" * 64,
        "access_proven": proven,
    }


def _receipt(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    """Return a complete synthetic acceptance receipt and matching skill root."""
    credentials = import_script_module("core.credentials")
    skill_root = _skill_tree(tmp_path)
    receipt = {
        "schema_version": "1.0",
        "kind": "access_check",
        "status": "PASS",
        "publication": True,
        "captured_at": NOW.isoformat(),
        "execution_host": "unit-runner.example.test",
        "tested_revision": REVISION,
        "skill_sha256": credentials.skill_digest(skill_root),
        "blocking_live_checks": [],
        "requested_job_url": "https://gitlab.example.test/unit/repo/-/jobs/123",
        "targets": {
            "github": "github.example.test/unit/repo",
            "gitlab": "https://gitlab.example.test",
            "kubernetes": {
                "context": "unit-context",
                "server": "https://api.cluster.example.test:6443",
            },
        },
        "credential_sources": {
            "github": "env:GH_ENTERPRISE_TOKEN",
            "gitlab": "env:GITLAB_TOKEN",
            "kubernetes": "env:KUBECONFIG",
        },
        "surfaces": {
            "github": {
                "status": "PASS",
                "identity": "unit-gh",
                "target": "github.example.test/unit/repo",
                "credential_source": "env:GH_ENTERPRISE_TOKEN",
                "details": {"required_checks": ["validate", "lint"]},
            },
            "gitlab": {
                "status": "PASS",
                "identity": "unit-gl",
                "target": "https://gitlab.example.test",
                "credential_source": "env:GITLAB_TOKEN",
            },
            "kubernetes": {
                "status": "PASS",
                "identity": "unit-admin",
                "target": "unit-context -> https://api.cluster.example.test:6443",
                "credential_source": "env:KUBECONFIG",
                "details": {
                    "context": "unit-context",
                    "user_entry": "unit-user",
                    "api_server": "https://api.cluster.example.test:6443",
                    "kubeconfig_files": ["/tmp/unit.kubeconfig"],
                    "auth_mechanism": {
                        "type": "exec-provider",
                        "source": "unit-auth",
                    },
                },
            },
        },
        "live_checks": {
            "storage_report": _live_check(),
            "event_trace": _live_check(),
            "cilium_status": {
                **_live_check("PARTIAL"),
                "errors": [{"source": "cilium-a", "reason": "agent_not_ready"}],
                "error_count": 1,
                "agent_health": {"passed": 1, "total": 2},
            },
            "gitlab_job": {
                **_live_check(),
                "job_readback": {
                    "job_id": 123,
                    "pipeline_id": 77,
                    "runner_id": 9,
                    "trace_line_count": 4,
                },
            },
        },
    }
    return receipt, skill_root


def _validate(receipt: Any, expected: Any, skill_root: Path) -> list[str]:
    acceptance = import_script_module("core.acceptance")
    return acceptance.validate_live_receipt(
        receipt,
        expected,
        revision=REVISION,
        github_repository="unit/repo",
        skill_root=skill_root,
        now=NOW,
    )


def _external_status_payload() -> dict[str, Any]:
    return {
        "sha": REVISION,
        "repository": {"full_name": "unit/repo"},
        "statuses": [
            {
                "context": "gal19/live-receipt",
                "state": "success",
                "creator": {"login": "trusted-bot"},
                "updated_at": NOW.isoformat(),
            }
        ],
    }


def _validate_status(
    payload: Any,
    *,
    revision: str = REVISION,
    repository: str = "unit/repo",
    trusted_actor: str = "trusted-bot",
) -> list[str]:
    acceptance = import_script_module("core.acceptance")
    return acceptance.validate_external_status(
        payload,
        revision=revision,
        repository=repository,
        trusted_actor=trusted_actor,
        now=NOW,
    )


def test_validate_live_receipt_accepts_exact_synthetic_receipt(tmp_path):
    """A complete receipt tied to the expected revision, target, and skill passes."""
    receipt, skill_root = _receipt(tmp_path)

    assert _validate(receipt, _expected(), skill_root) == []


def test_validate_external_status_accepts_exact_head_trusted_success():
    """The exact commit needs a fresh success from the trusted verifier context."""
    assert _validate_status(_external_status_payload()) == []


def test_validate_external_status_rejects_untrusted_or_wrong_target_payloads():
    """External status evidence fails closed when target, actor, or freshness drift."""
    wrong_revision = copy.deepcopy(_external_status_payload())
    wrong_revision["sha"] = "b" * 40
    assert _validate_status(wrong_revision) == ["status_target_mismatch"]

    wrong_repository = copy.deepcopy(_external_status_payload())
    wrong_repository["repository"]["full_name"] = "other/repo"
    assert _validate_status(wrong_repository) == ["status_target_mismatch"]

    missing_context = copy.deepcopy(_external_status_payload())
    missing_context["statuses"][0]["context"] = "validate"
    assert _validate_status(missing_context) == ["live_receipt_status_missing"]

    failed_status = copy.deepcopy(_external_status_payload())
    failed_status["statuses"][0]["state"] = "failure"
    assert _validate_status(failed_status) == [
        "live_receipt_status_untrusted_or_failed"
    ]

    untrusted_actor = copy.deepcopy(_external_status_payload())
    untrusted_actor["statuses"][0]["creator"]["login"] = "other-bot"
    assert _validate_status(untrusted_actor) == [
        "live_receipt_status_untrusted_or_failed"
    ]

    stale_status = copy.deepcopy(_external_status_payload())
    stale_status["statuses"][0]["updated_at"] = (NOW - timedelta(days=2)).isoformat()
    assert _validate_status(stale_status) == ["live_receipt_status_stale"]

    invalid_time = copy.deepcopy(_external_status_payload())
    invalid_time["statuses"][0]["updated_at"] = "not-a-time"
    assert _validate_status(invalid_time) == ["live_receipt_status_time_invalid"]


def test_validate_external_status_fails_closed_for_malformed_payloads():
    """Malformed combined-status input cannot stand in for trusted evidence."""
    assert _validate_status(["not", "a", "status"]) == ["status_response_invalid"]
    assert _validate_status(_external_status_payload(), trusted_actor="") == [
        "trusted_actor_unconfigured"
    ]

    missing_statuses = copy.deepcopy(_external_status_payload())
    del missing_statuses["statuses"]
    assert _validate_status(missing_statuses) == ["status_list_invalid"]

    non_list_statuses = copy.deepcopy(_external_status_payload())
    non_list_statuses["statuses"] = {}
    assert _validate_status(non_list_statuses) == ["status_list_invalid"]


def test_skill_digest_changes_when_executable_skill_content_changes(tmp_path):
    """The skill fingerprint is content sensitive and deterministic."""
    credentials = import_script_module("core.credentials")
    skill_root = _skill_tree(tmp_path)
    before = credentials.skill_digest(skill_root)

    (skill_root / "scripts" / "access_check.py").write_text(
        "print('changed')\n", encoding="utf-8"
    )

    assert credentials.skill_digest(skill_root) != before


def test_validate_live_receipt_rejects_wrong_revision_content_host_and_target(
    tmp_path,
):
    """Receipt identity mismatches produce explicit failure codes."""
    receipt, skill_root = _receipt(tmp_path)
    changed_root = _skill_tree(tmp_path / "changed")
    (changed_root / "scripts" / "access_check.py").write_text(
        "print('changed')\n", encoding="utf-8"
    )
    receipt["tested_revision"] = "b" * 40
    receipt["skill_sha256"] = import_script_module("core.credentials").skill_digest(
        changed_root
    )
    receipt["execution_host"] = "other-runner.example.test"
    receipt["targets"]["github"] = "github.example.test/other/repo"
    receipt["targets"]["gitlab"] = "https://other-gitlab.example.test"
    receipt["targets"]["kubernetes"]["server"] = "https://api.other.example.test:6443"
    receipt["surfaces"]["github"]["details"]["required_checks"] = ["unrelated"]
    receipt["requested_job_url"] = "https://gitlab.example.test/unit/repo/-/jobs/999"

    failures = set(_validate(receipt, _expected(), skill_root))

    assert {
        "revision_mismatch",
        "skill_content_mismatch",
        "execution_host_mismatch",
        "github_target_mismatch",
        "gitlab_target_mismatch",
        "kubernetes_target_mismatch",
        "required_checks_mismatch",
        "job_target_mismatch",
    } <= failures


def test_validate_live_receipt_rejects_stale_and_missing_live_result(tmp_path):
    """Stale receipts and absent live read-back cannot be acceptance evidence."""
    receipt, skill_root = _receipt(tmp_path)
    receipt["captured_at"] = (NOW - timedelta(days=2)).isoformat()
    del receipt["live_checks"]["event_trace"]

    failures = set(_validate(receipt, _expected(), skill_root))

    assert "receipt_stale" in failures
    assert "event_trace_missing" in failures


def test_validate_live_receipt_rejects_unproven_or_malformed_live_result(tmp_path):
    """Live checks need a digest and access proof, not only a status field."""
    receipt, skill_root = _receipt(tmp_path)
    receipt["live_checks"]["storage_report"]["access_proven"] = False
    receipt["live_checks"]["storage_report"]["readback_sha256"] = "not-a-digest"
    receipt["live_checks"]["gitlab_job"]["job_readback"] = {"job_id": 123}

    failures = set(_validate(receipt, _expected(), skill_root))

    assert "storage_report_access_unproven" in failures
    assert "storage_report_readback_missing" in failures
    assert "job_readback_missing" in failures


def test_validate_live_receipt_fails_closed_for_json_inputs(tmp_path):
    """Malformed JSON payloads become invalid input instead of partial success."""
    receipt, skill_root = _receipt(tmp_path)
    payload = json.loads(json.dumps(receipt))

    assert _validate(payload, _expected(), skill_root) == []
    assert _validate(["not", "a", "receipt"], _expected(), skill_root) == [
        "invalid_receipt_or_target"
    ]
    assert _validate(payload, "not a target", skill_root) == [
        "invalid_receipt_or_target"
    ]

    broken = copy.deepcopy(payload)
    broken["surfaces"] = []
    failures = _validate(broken, _expected(), skill_root)

    assert "credential_or_surface_evidence_missing" in failures
