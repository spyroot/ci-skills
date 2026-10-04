"""Live proof gate refuses absent output, wrong order, and missing commands."""

from __future__ import annotations

import sys
import subprocess
from datetime import datetime, timedelta, timezone

from conftest import REPO_ROOT, SCRIPT_ROOT, import_script_module, load_module

sys.path.insert(0, str(REPO_ROOT / "tools"))
PROOFS = load_module(
    "command_live_proofs", REPO_ROOT / "tools" / "command_live_proofs.py"
)
PROVENANCE = import_script_module("core.provenance")
CATALOG = import_script_module("core.catalog")
NOW = datetime(2026, 10, 4, 15, tzinfo=timezone.utc)
HOST = "operator.example.test"
SCRIPT = "gitlab_access.py"


def _proof() -> dict:
    digest = PROVENANCE.tree_digest(SCRIPT_ROOT.parent)["digest"]
    output = {
        "schema_version": "1.0",
        "kind": "gitlab_access",
        "status": "PASS",
        "execution_host": HOST,
        "skill": {"digest": digest},
        "target": {"kind": "project", "id": 42, "full_path": "team/test"},
        "identity": {"username": "operator"},
    }
    start = NOW - timedelta(minutes=4)
    steps = [
        {
            "phase": phase,
            "operation": None,
            "captured_at": (start + timedelta(minutes=index)).isoformat(),
            "command": [
                "python",
                f"skills/ci-skills/scripts/{SCRIPT}",
                "check",
                "--revision",
                "a" * 40,
                "--json",
            ],
            "exit_code": 0,
            "stdout": output.copy(),
        }
        for index, phase in enumerate(("before", "action", "after"))
    ]
    return {
        "schema_version": "1.0",
        "kind": "command_live_proof",
        "command": SCRIPT,
        "status": "PASS",
        "captured_at": (start + timedelta(minutes=3)).isoformat(),
        "execution_host": HOST,
        "identity": {"gitlab": "operator"},
        "credential_sources": {"gitlab": "file:path:unit"},
        "target": {"gitlab": "https://gitlab.example.test/team/test"},
        "skill": {
            "digest": digest,
            "revision": {"value": "a" * 40, "source": "git_head", "verified": False},
        },
        "tested_revision": "a" * 40,
        "steps": steps,
    }


def _check(proof: dict) -> list[str]:
    expected = {
        "executors": [{"host": HOST, "identities": {"gitlab": "operator"}}],
        "max_receipt_age_days": 30,
        "targets": {"gitlab": "https://gitlab.example.test"},
    }
    return PROOFS._check_one(
        "gitlab_access.json",
        proof,
        SCRIPT,
        CATALOG,
        proof["skill"]["digest"],
        expected,
        {HOST: {"gitlab": "file:path:unit"}},
        NOW,
        lambda value: value,
    )


def test_actual_ordered_read_bodies_are_accepted():
    assert _check(_proof()) == []


def test_before_after_order_is_required():
    proof = _proof()
    proof["steps"][0]["captured_at"] = proof["steps"][1]["captured_at"]
    assert any("time_order_invalid" in problem for problem in _check(proof))


def test_digest_or_boolean_cannot_replace_live_output():
    proof = _proof()
    proof["steps"][2]["stdout"] = {"verified": True, "readback_sha256": "f" * 64}
    assert any("live_output_missing" in problem for problem in _check(proof))


def test_host_credential_path_cannot_be_committed_as_proof():
    proof = _proof()
    proof["credential_sources"]["gitlab"] = "file:/Users/operator/.token"
    assert "gitlab_access.json:private_host_path_exposed" in _check(proof)


def test_cleanup_compares_actual_before_and_final_get_bodies():
    before = {"http_status": 200, "body": {"id": 42, "title": "before"}}
    restored = {"http_status": 200, "body": {"id": 42, "title": "before"}}
    drifted = {"http_status": 200, "body": {"id": 42, "title": "after"}}
    assert PROOFS._observed_state(before) == PROOFS._observed_state(restored)
    assert PROOFS._observed_state(before) != PROOFS._observed_state(drifted)


def test_catalog_drives_the_full_mutating_phase_order():
    phases = PROOFS._steps_for("gitlab_wiki.py", CATALOG)
    assert phases[:3] == [("create", "before"), ("create", "plan"), ("create", "apply")]
    assert ("update", "final_read") in phases
    assert ("apply", "repeat_apply") in PROOFS._steps_for(
        "k8s_verify_mtu_consistency.py", CATALOG
    )


def test_absent_command_proofs_block_the_entire_closed_world(tmp_path):
    result = PROOFS.evaluate(
        tmp_path,
        {"executors": [], "max_receipt_age_days": 30},
        {},
        SCRIPT_ROOT.parent,
        now=NOW,
        redact=lambda value: value,
    )
    required = set(CATALOG.COMMANDS) | set(CATALOG.NODE_LOCAL_COMMANDS)
    assert result["status"] == "BLOCKED"
    assert len(result["problems"]) == len(required) == 15


def test_tested_commit_bytes_must_match_current_skill_bytes(tmp_path):
    skill = tmp_path / "skills" / "ci-skills"
    skill.mkdir(parents=True)
    script = skill / "SKILL.md"
    script.write_text("# Candidate\n", encoding="utf-8")
    for args in (
        ["init", "-q"],
        ["config", "user.name", "Unit"],
        ["config", "user.email", "unit@example.test"],
        ["add", "skills/ci-skills/SKILL.md"],
        ["commit", "-qm", "fixture"],
    ):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True)
    revision = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    committed = PROOFS._committed_skill_digest(skill, revision)
    assert committed == PROVENANCE.tree_digest(skill)["digest"]
    script.write_text("# Changed\n", encoding="utf-8")
    assert committed != PROVENANCE.tree_digest(skill)["digest"]
