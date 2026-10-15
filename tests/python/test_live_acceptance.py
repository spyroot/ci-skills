"""Regression coverage for runtime-bound live acceptance receipts.

The initial access receipts establish the selected host, identity, target and
credential context.  Later GitLab operation receipts must prove that they ran
against that setup; an expectations file cannot supply a second set of values.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, MutableMapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final, Optional

import pytest
from conftest import (
    REPO_ROOT,
    SCRIPT_ROOT,
    import_script_module,
    load_module,
)
from jsonschema import Draft202012Validator

ACCEPTANCE: Final = load_module(
    "proposed_check_live_acceptance", REPO_ROOT / "tools" / "check_live_acceptance.py"
)
PORTABLE: Final = import_script_module("lib.python.core.portable")
SKILL_ROOT: Final = SCRIPT_ROOT.parent
NOW: Final = datetime(2026, 6, 1, tzinfo=UTC)
POLICY_FILE: Final = REPO_ROOT / ACCEPTANCE.POLICY_FILE
POLICY_SCHEMA: Final = REPO_ROOT / ACCEPTANCE.POLICY_SCHEMA_FILE
POLICY: Final = ACCEPTANCE.load_policy(POLICY_FILE, POLICY_SCHEMA)
MAX_AGE_DAYS: Final = POLICY["max_receipt_age_days"]


if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue


def _digest() -> str:
    """Read the current skill tree digest used in synthetic receipts.

    :returns: SHA-256 digest of the checkout's ci-skills tree.
    """
    from lib.python.core.provenance import tree_digest

    return tree_digest(SKILL_ROOT)["digest"]


def _functional_digest() -> str:
    """Read the current command-input digest used in synthetic receipts.

    :returns: SHA-256 digest of the checkout's bin, lib, and schemas inputs.
    """
    from lib.python.core.provenance import functional_digest

    return functional_digest(SKILL_ROOT)


def _access_setup() -> Mapping[str, JSONObject]:
    """Create matching full access and GitLab access receipts for one runtime.

    :returns: Synthetic setup receipts keyed by their fixture file names.
    """
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
        "execution_host": execution_host,
        "captured_at": (NOW - timedelta(days=1)).isoformat(),
        "tested_revision": "a" * 40,
        "skill": {
            "digest": _digest(),
            "functional_digest": _functional_digest(),
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
            "functional_digest": _functional_digest(),
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


def _context(setup: Mapping[str, JSONObject]) -> JSONObject:
    """Derive expected GitLab operation identity from the access receipt.

    :param setup: Fixture setup receipts containing gitlab-access.json.
    :returns: Host, origin, identity, credential, and target context.
    """
    return ACCEPTANCE._gitlab_context(setup["gitlab-access.json"])


def _base_operation(
    setup: Mapping[str, JSONObject],
    kind: str,
    operation: str,
    action: Optional[str],
) -> MutableMapping[str, JSONValue]:
    """Build one GitLab read or action receipt from initial access context.

    :param setup: Fixture setup receipts supplying the bound GitLab context.
    :param kind: GitLab receipt kind selected by the required operation set.
    :param operation: Read or mutation operation represented by the receipt.
    :param action: Applied or no-op action, or None for a read.
    :returns: Synthetic receipt with matching target, identity, and read-back.
    """
    context = _context(setup)
    revision = deepcopy(setup["gitlab-access.json"]["skill"]["revision"])
    receipt = {
        "schema_version": "1.0",
        "kind": kind,
        "status": "PASS",
        "execution_host": context["execution_host"],
        "origin": context["origin"],
        "captured_at": (NOW - timedelta(days=1)).isoformat(),
        "tested_revision": revision["value"],
        "skill": {
            "digest": _digest(),
            "functional_digest": _functional_digest(),
            "revision": revision,
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


def _runner_create(setup: Mapping[str, JSONObject]) -> MutableMapping[str, JSONValue]:
    """Attach independent cleanup evidence to a runner creation receipt.

    :param setup: Fixture setup receipts supplying host and project identity.
    :returns: Applied runner creation receipt with delete and absence read-back.
    """
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
        "tested_revision": receipt["tested_revision"],
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


def _runner_delete(
    setup: Mapping[str, JSONObject], action: str
) -> MutableMapping[str, JSONValue]:
    """Attach before/action/absence proof to a runner deletion receipt.

    :param setup: Fixture setup receipts supplying the bound GitLab context.
    :param action: APPLIED or NO_OP deletion outcome to represent.
    :returns: Deletion receipt with matching plan, record, and read-back.
    """
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


def _complete_receipts(
    setup: Mapping[str, JSONObject],
) -> MutableMapping[str, JSONObject]:
    """Build receipts for every required GitLab operation from setup context.

    :param setup: Fixture full access and GitLab access receipts.
    :returns: Setup plus all required read, applied, and no-op operation receipts.
    """
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
    setup: Optional[Mapping[str, JSONObject]] = None,
    receipts: Optional[Mapping[str, JSONObject]] = None,
    max_age_days: int = MAX_AGE_DAYS,
) -> JSONObject:
    """Evaluate synthetic receipts against the checkout skill digest.

    :param setup: Optional setup fixture; generated when omitted.
    :param receipts: Optional operation set; generated from setup when omitted.
    :param max_age_days: Freshness window; the committed policy's value by default.
    :returns: Live-acceptance status, accepted names, and problem reasons.
    """
    initial = setup or _access_setup()
    return ACCEPTANCE.evaluate(
        initial,
        receipts or _complete_receipts(initial),
        SKILL_ROOT,
        now=NOW,
        max_age_days=max_age_days,
    )


def _write_policy(
    root: Path,
    max_age_days: int,
    name: str = "",
    directories: Optional[Sequence[str]] = None,
) -> Path:
    """Copy the committed policy and schema into a fixture root with changed values.

    :param root: Fixture repository root receiving the policy and its schema.
    :param max_age_days: Freshness window written into the copied policy.
    :param name: Optional policy file name outside the default location.
    :param directories: Optional receipt directories replacing the committed list.
    :returns: Path of the written policy file.
    """
    schema = root / ACCEPTANCE.POLICY_SCHEMA_FILE
    schema.parent.mkdir(parents=True, exist_ok=True)
    schema.write_text(POLICY_SCHEMA.read_text(encoding="utf-8"), encoding="utf-8")
    document = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    document["maxReceiptAgeDays"] = max_age_days
    if directories is not None:
        document["receiptDirectories"] = list(directories)
    policy = root / name if name else root / ACCEPTANCE.POLICY_FILE
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(json.dumps(document), encoding="utf-8")
    return policy


def test_full_required_operation_set_is_bound_to_initial_runtime_setup() -> None:
    """Accept the complete declared operation set bound to setup receipts.
    :returns: None; assertions in the body validate the regression.
    """
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


def test_operation_identity_must_match_the_initial_synthetic_setup() -> None:
    """Block an operation whose GitLab user differs from initial access.
    :returns: None; assertions in the body validate the regression.
    """
    setup = _access_setup()
    receipts = _complete_receipts(setup)
    operation = receipts["gitlab_issue-open-bug-APPLIED.json"]
    operation["identity"] = {"username": "different-user", "id": 99}

    result = _evaluate(setup, receipts)

    assert result["status"] == "BLOCKED"
    assert "gitlab_issue-open-bug-APPLIED.json:identity_mismatch" in result["problems"]


def test_operation_target_cannot_switch_away_from_initial_synthetic_setup() -> None:
    """Block an operation that changes project identity after access setup.
    :returns: None; assertions in the body validate the regression.
    """
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
            lambda setup: setup["access.json"]["surfaces"]["kubernetes"].update(
                status="BLOCKED"
            ),
            "surface_not_pass:kubernetes",
        ),
        (
            lambda setup: setup["access.json"]["surfaces"].pop("gitlab"),
            "identity_missing:gitlab",
        ),
        (
            lambda setup: setup["gitlab-access.json"].update(credential_digest=""),
            "credential_source_unresolved",
        ),
    ),
)
def test_setup_receipts_preserve_freshness_and_shape_checks(
    mutate: bool, reason: str
) -> None:
    """Retain setup status, time, live-read, surface, and credential checks.

    :param mutate: Case-specific change that invalidates a setup receipt.
    :param reason: Expected acceptance problem suffix for that change.

    :returns: None; assertions in the body validate the regression.
    """
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


def test_repeat_operations_require_matching_plan_and_resource() -> None:
    """Block a no-op receipt whose plan differs from the applied receipt.
    :returns: None; assertions in the body validate the regression.
    """
    setup = _access_setup()
    receipts = _complete_receipts(setup)
    receipts["gitlab_issue-open-bug-NO_OP.json"]["plan_digest"] = "changed"

    result = _evaluate(setup, receipts)

    assert (
        "gitlab_issue-open-bug-APPLIED.json:gitlab_issue-open-bug-NO_OP.json:repeat_readback_mismatch"
        in result["problems"]
    )


def test_runner_cleanup_and_delete_readback_remain_required() -> None:
    """Block missing created-runner cleanup and deleted-runner absence proof.
    :returns: None; assertions in the body validate the regression.
    """
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


def test_missing_or_undeclared_operation_receipt_blocks_without_local_allowlist() -> (
    None
):
    """Use the required operation set to reject missing and unknown receipts.
    :returns: None; assertions in the body validate the regression.
    """
    setup = _access_setup()
    receipts = _complete_receipts(setup)
    del receipts["gitlab_runner-list-read.json"]
    receipts["unexpected.json"] = _base_operation(
        setup, "gitlab_issue", "other", "APPLIED"
    )

    result = _evaluate(setup, receipts)

    assert "gitlab_runner:list:check:receipt_missing" in result["problems"]
    assert "unexpected.json:receipt_not_declared" in result["problems"]


def test_access_setup_receipts_cannot_be_replaced_in_the_operation_set() -> None:
    """Reject an operation set that rewrites an initial access receipt.
    :returns: None; assertions in the body validate the regression.
    """
    setup = _access_setup()
    receipts = _complete_receipts(setup)
    receipts["gitlab-access.json"]["identity"]["id"] = 99

    result = _evaluate(setup, receipts)

    assert "gitlab-access.json:setup_receipt_mismatch" in result["problems"]


def test_real_operation_envelope_keeps_cleanup_shape(tmp_path: Path) -> None:
    """Preserve cleanup evidence shape through portable receipt serialization.

    :param tmp_path: Pytest directory receiving a serialized runner receipt.

    :returns: None; assertions in the body validate the regression.
    """
    actions = import_script_module("lib.python.core.gitlab.gitlab_actions")
    portable = import_script_module("lib.python.core.portable")
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
        skill={
            "digest": _digest(),
            "functional_digest": _functional_digest(),
            "revision": {"value": "a" * 40, "verified": True},
        },
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
        path.name,
        receipt,
        required,
        _functional_digest(),
        datetime.now(UTC),
        MAX_AGE_DAYS,
    ) == ["runner.json:runner_smoke_cleanup_unproven"]


def _commit_current_index(repository: Path, message: str) -> str:
    """Create a fixture commit from the current Git index tree.

    :param repository: Temporary Git repository holding the staged skill tree.
    :param message: Commit message distinguishing fixture source revisions.
    :returns: SHA of the created commit, without changing the checked-out tree.
    """
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


def _candidate_identity_receipt(revision: str, skill_root: Path) -> JSONObject:
    """Build verified candidate identity evidence for a selected skill tree.

    :param revision: Tested source commit recorded in the receipt.
    :param skill_root: Skill directory whose tree digest is recorded.
    :returns: Receipt fragment containing commit and skill digest identity.
    """
    from lib.python.core.provenance import functional_digest, tree_digest

    return {
        "tested_revision": revision,
        "skill": {
            **tree_digest(skill_root),
            "functional_digest": functional_digest(skill_root),
            "revision": {"value": revision, "verified": True},
        },
    }


def _sibling_skill_commits(tmp_path: Path) -> tuple[Path, Path, str, str]:
    """Create nonancestor fixture commits sharing one ci-skills tree.

    :param tmp_path: Pytest directory receiving the temporary Git repository.
    :returns: Repository, skill path, tested commit, and candidate commit.
    """
    repository = tmp_path / "repository"
    skill_root = repository / "ci-skills"
    bin_root = skill_root / "bin"
    lib_root = skill_root / "lib" / "python"
    schema_root = skill_root / "schemas"
    bin_root.mkdir(parents=True)
    lib_root.mkdir(parents=True)
    schema_root.mkdir()
    (skill_root / "SKILL.md").write_text("fixture skill\n", encoding="utf-8")
    (bin_root / "fixture.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (lib_root / "fixture.py").write_text("VALUE = 1\n", encoding="utf-8")
    (schema_root / "fixture.json").write_text('{"type":"object"}\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "ci-skills"], check=True)
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


def test_candidate_identity_accepts_document_change_from_nonancestor_commit(
    tmp_path: Path,
) -> None:
    """Accept a documentation-only sibling commit with equal functional inputs.

    :param tmp_path: Pytest directory holding sibling fixture commits.

    :returns: None; assertions in the body validate the regression.
    """
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
    assert ACCEPTANCE.check_candidate_identity(receipt, skill_root) == []
    (skill_root / "SKILL.md").write_text("different\n", encoding="utf-8")
    test_file = skill_root / "tests" / "python" / "test_fixture.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_fixture() -> None:\n    pass\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "ci-skills"], check=True)
    candidate = _commit_current_index(repository, "documentation and test updates")
    subprocess.run(
        ["git", "-C", str(repository), "update-ref", "refs/heads/main", candidate],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "checkout", "-q", candidate], check=True
    )
    assert ACCEPTANCE.check_candidate_identity(receipt, skill_root) == []


def test_candidate_identity_rejects_runtime_change(
    tmp_path: Path,
) -> None:
    """Reject changed functional input even with valid recorded provenance.

    :param tmp_path: Pytest directory holding divergent fixture commits.

    :returns: None; assertions in the body validate the regression.
    """
    repository, skill_root, tested, _candidate = _sibling_skill_commits(tmp_path)
    receipt = _candidate_identity_receipt(tested, skill_root)
    (skill_root / "bin" / "fixture.sh").write_text(
        "#!/bin/sh\necho different\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "-C", str(repository), "add", "ci-skills/bin/fixture.sh"], check=True
    )
    candidate = _commit_current_index(repository, "different skill")
    subprocess.run(
        ["git", "-C", str(repository), "update-ref", "refs/heads/main", candidate],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "checkout", "-q", candidate], check=True
    )
    assert ACCEPTANCE.check_candidate_identity(receipt, skill_root) == [
        "candidate_functional_digest_mismatch"
    ]


def test_candidate_identity_accepts_source_absent_from_fresh_clone(
    tmp_path: Path,
) -> None:
    """Keep equal functional evidence usable after squash source refs disappear.

    :param tmp_path: Pytest directory holding source and fresh candidate clones.
    :returns: None; assertions validate absent source and accepted evidence.
    """
    repository, skill_root, tested, _candidate = _sibling_skill_commits(tmp_path)
    receipt = _candidate_identity_receipt(tested, skill_root)
    candidate_repository = tmp_path / "candidate"
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-local",
            "--single-branch",
            "--branch",
            "main",
            str(repository),
            str(candidate_repository),
        ],
        check=True,
        capture_output=True,
    )
    assert (
        subprocess.run(
            [
                "git",
                "-C",
                str(candidate_repository),
                "cat-file",
                "-e",
                f"{tested}^{{commit}}",
            ],
            capture_output=True,
            check=False,
        ).returncode
        != 0
    )

    assert (
        ACCEPTANCE.check_candidate_identity(receipt, candidate_repository / "ci-skills")
        == []
    )


@pytest.mark.parametrize(
    "revision_fields",
    [{"value": "not-a-full-sha"}, {"verified": False}, {"value": "0" * 40}],
    ids=["malformed", "unverified", "inconsistent"],
)
def test_candidate_identity_rejects_invalid_recorded_revision(
    tmp_path: Path,
    revision_fields: JSONObject,
) -> None:
    """Reject malformed, unverified or inconsistent revision provenance.

    :param tmp_path: Pytest directory containing the candidate skill tree.
    :param revision_fields: Invalid fields overriding valid source provenance.
    :returns: None; assertions validate rejection without changing runtime bytes.
    """
    _repository, skill_root, tested, _candidate = _sibling_skill_commits(tmp_path)
    receipt = _candidate_identity_receipt(tested, skill_root)
    receipt["skill"]["revision"].update(revision_fields)

    assert ACCEPTANCE.check_candidate_identity(receipt, skill_root) == [
        "candidate_revision_unverified"
    ]


def test_acceptance_requires_recorded_functional_digest() -> None:
    """Block a live receipt without a measured functional digest.

    :returns: None; assertions in the body validate the regression.
    """
    setup = _access_setup()
    receipts = _complete_receipts(setup)
    del setup["access.json"]["skill"]["functional_digest"]

    result = _evaluate(setup, receipts)

    assert "access.json:functional_digest_absent" in result["problems"]


def test_gate_never_reads_repository_merge_policy_from_the_skill_target() -> None:
    """Accept the full access receipt without publication or required-checks proof.

    The skill's target file belongs to whoever installs the skill and never
    carries this repository's merge policy. The repository gate binds GitLab and
    Kubernetes proof only: it never reads the receipt's GitHub surface, so a
    receipt without one, and one that still carries publication fields with a
    blocked GitHub surface, are both accepted. The installed `access_check.py`
    itself still needs a `[github]` table with some readable repository to run;
    that is the producer's contract, not this gate's.

    :returns: None; assertions in the body validate the boundary.
    """
    setup = _access_setup()
    access = setup["access.json"]
    del access["surfaces"]["github"]
    del access["targets"]["github"]
    del access["credential_sources"]["github"]

    without_github = _evaluate(setup, _complete_receipts(setup))

    assert without_github["status"] == "PASS", without_github["problems"]
    assert not [
        problem
        for problem in without_github["problems"]
        if "github" in problem or "publication" in problem
    ]

    access["publication"] = True
    access["surfaces"]["github"] = {
        "status": "BLOCKED",
        "observed_capability": ["required_checks_read"],
        "details": {"required_checks": []},
    }

    with_publication_fields = _evaluate(setup, _complete_receipts(setup))

    assert with_publication_fields["status"] == "PASS", with_publication_fields[
        "problems"
    ]
    assert ACCEPTANCE.GATE_SURFACES == ("gitlab", "kubernetes")


def test_cli_blocks_without_the_tracked_policy_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Emit a blocked JSON result when the acceptance policy is absent.

    :param tmp_path: Empty fixture repository root supplied to the CLI.
    :param monkeypatch: Pytest fixture replacing process arguments.
    :param capsys: Pytest fixture capturing the CLI's JSON output.

    :returns: None; assertions in the body validate the regression.
    """
    monkeypatch.setattr(
        sys, "argv", ["check_live_acceptance.py", "--root", str(tmp_path), "--json"]
    )

    assert ACCEPTANCE.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "BLOCKED"
    assert report["kind"] == "live_acceptance"
    assert report["problems"] == [
        f"policy_missing:{tmp_path / 'inventory/ci/live-acceptance.json'}"
    ]


def test_cli_reports_receipt_loading_shape_without_an_expectations_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Emit a blocked JSON result when the policy's first receipt directory is absent.

    :param tmp_path: Fixture repository root holding only the policy file.
    :param monkeypatch: Pytest fixture replacing process arguments.
    :param capsys: Pytest fixture capturing the CLI's JSON output.

    :returns: None; assertions in the body validate the regression.
    """
    _write_policy(tmp_path, MAX_AGE_DAYS)
    monkeypatch.setattr(
        sys, "argv", ["check_live_acceptance.py", "--root", str(tmp_path), "--json"]
    )

    assert ACCEPTANCE.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "BLOCKED"
    assert report["kind"] == "live_acceptance"
    assert report["problems"] == [
        f"receipt_directory_missing:{tmp_path / POLICY['receipt_directories'][0]}"
    ]


def test_every_declared_receipt_directory_must_exist(tmp_path: Path) -> None:
    """Block when a declared directory other than the first one is absent.

    :param tmp_path: Pytest directory holding one existing receipt directory.

    :returns: None; assertions in the body validate the regression.
    """
    present = tmp_path / "present"
    present.mkdir()
    (present / "receipt.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ACCEPTANCE.AcceptanceError, match="receipt_directory_missing"):
        ACCEPTANCE._load_receipt_dirs([present, tmp_path / "absent"])


def test_committed_policy_matches_its_schema_and_repository_layout() -> None:
    """The tracked policy validates against its schema and names real locations.

    :returns: None; assertions in the body validate the regression.
    """
    schema = json.loads(POLICY_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(
        json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    )

    document = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    assert (POLICY_FILE.parent / document["$schema"]).resolve() == POLICY_SCHEMA
    for directory in POLICY["receipt_directories"]:
        assert (REPO_ROOT / directory).is_dir(), directory
    assert (REPO_ROOT / POLICY["historical_directory"]).is_dir()
    assert POLICY["historical_directory"] not in POLICY["receipt_directories"]
    assert (REPO_ROOT / POLICY["scheduled_workflow"]).is_file()
    assert POLICY["max_receipt_age_days"] >= 1


@pytest.mark.parametrize(
    ("field", "value", "reason", "schema_rejects"),
    [
        ("schemaVersion", "2.0", "policy_invalid:schemaVersion", True),
        ("maxReceiptAgeDays", 0, "policy_invalid:maxReceiptAgeDays", True),
        ("maxReceiptAgeDays", True, "policy_invalid:maxReceiptAgeDays", True),
        ("maxReceiptAgeDays", 30.5, "policy_invalid:maxReceiptAgeDays", True),
        ("receiptDirectories", [], "policy_invalid:receiptDirectories", True),
        ("receiptDirectories", ["a", "a"], "policy_invalid:receiptDirectories", True),
        ("receiptDirectories", ["../x"], "policy_invalid:receiptDirectories", True),
        ("receiptDirectories", ["a b"], "policy_invalid:receiptDirectories", True),
        ("historicalDirectory", "/abs", "policy_invalid:historicalDirectory", True),
        (
            "historicalDirectory",
            "tests/acceptance/receipts",
            "policy_invalid:historicalDirectory",
            False,
        ),
        (
            "scheduledCheck",
            {"workflow": "w.yml"},
            "policy_invalid:scheduledCheck",
            True,
        ),
        (
            "scheduledCheck",
            {"workflow": "README.md", "cron": "0 6 * * 1"},
            "policy_invalid:scheduledCheck.workflow",
            True,
        ),
        (
            "scheduledCheck",
            {"workflow": ".github/workflows/validate.yml", "cron": "0 6 * *"},
            "policy_invalid:scheduledCheck.cron",
            True,
        ),
        (
            "scheduledCheck",
            {"workflow": ".github/workflows/validate.yml", "cron": "0  6 * * 1"},
            "policy_invalid:scheduledCheck.cron",
            True,
        ),
        ("rerunProcedure", "", "policy_invalid:rerunProcedure", True),
        ("rerunProcedure", " ", "policy_invalid:rerunProcedure", True),
        ("extra", True, "policy_invalid:keys", True),
        ("$schema", 5, r"policy_invalid:\$schema", True),
    ],
)
def test_policy_rejects_undeclared_or_unsafe_values(
    tmp_path: Path, field: str, value: JSONValue, reason: str, schema_rejects: bool
) -> None:
    """Block an invalid policy, with the schema as the only source of field rules.

    :param tmp_path: Pytest directory receiving the altered policy copy.
    :param field: Policy key overwritten or added for this case.
    :param value: Invalid replacement value.
    :param reason: Stable classified failure expected from the loader.
    :param schema_rejects: Whether the JSON Schema alone rejects this document.

    :returns: None; assertions in the body validate the regression.
    """
    document = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    document[field] = value
    altered = tmp_path / "live-acceptance.json"
    altered.write_text(json.dumps(document), encoding="utf-8")
    schema = json.loads(POLICY_SCHEMA.read_text(encoding="utf-8"))

    assert Draft202012Validator(schema).is_valid(document) is not schema_rejects
    with pytest.raises(ACCEPTANCE.AcceptanceError, match=reason):
        ACCEPTANCE.load_policy(altered, POLICY_SCHEMA)


def test_policy_schema_must_exist_and_be_valid(tmp_path: Path) -> None:
    """Block when the policy schema is absent or is not a valid JSON Schema.

    :param tmp_path: Pytest directory holding a policy copy and a broken schema.

    :returns: None; assertions in the body validate the regression.
    """
    policy = tmp_path / "live-acceptance.json"
    policy.write_text(POLICY_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    missing = tmp_path / "missing.schema.json"
    with pytest.raises(ACCEPTANCE.AcceptanceError, match="policy_schema_missing"):
        ACCEPTANCE.load_policy(policy, missing)

    broken = tmp_path / "broken.schema.json"
    broken.write_text(json.dumps({"type": "not-a-type"}), encoding="utf-8")
    with pytest.raises(ACCEPTANCE.AcceptanceError, match="policy_schema_invalid"):
        ACCEPTANCE.load_policy(policy, broken)

    malformed = tmp_path / "malformed.schema.json"
    malformed.write_text("{", encoding="utf-8")
    with pytest.raises(
        ACCEPTANCE.AcceptanceError, match=f"policy_schema_unreadable:{malformed}"
    ):
        ACCEPTANCE.load_policy(policy, malformed)


def test_cli_dry_run_lists_policy_and_receipts_without_evaluating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report the policy and selected receipts as DRY_RUN without any evaluation.

    :param tmp_path: Fixture repository root holding the policy and one receipt.
    :param monkeypatch: Pytest fixture replacing process arguments.
    :param capsys: Pytest fixture capturing the CLI's JSON output.

    :returns: None; assertions in the body validate the regression.
    """
    receipts = tmp_path / POLICY["receipt_directories"][0]
    _write_policy(tmp_path, MAX_AGE_DAYS, directories=POLICY["receipt_directories"][:1])
    receipts.mkdir(parents=True)
    (receipts / "stale.json").write_text(json.dumps({"kind": "gitlab_access"}))
    monkeypatch.setattr(
        sys,
        "argv",
        ["check_live_acceptance.py", "--root", str(tmp_path), "--json", "--dry-run"],
    )

    assert ACCEPTANCE.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "DRY_RUN"
    assert report["receipts"] == ["stale.json"]
    assert report["policy"]["max_receipt_age_days"] == MAX_AGE_DAYS
    assert report["problems"] == []


def test_cli_dry_run_text_mode_lists_window_directories_and_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Show a human the window, directories and receipt names in a text dry run.

    :param tmp_path: Fixture repository root holding the policy and one receipt.
    :param monkeypatch: Pytest fixture replacing process arguments.
    :param capsys: Pytest fixture capturing the CLI's text output.

    :returns: None; assertions in the body validate the regression.
    """
    receipts = tmp_path / POLICY["receipt_directories"][0]
    _write_policy(tmp_path, 7, directories=POLICY["receipt_directories"][:1])
    receipts.mkdir(parents=True)
    (receipts / "one.json").write_text(json.dumps({"kind": "gitlab_access"}))
    monkeypatch.setattr(
        sys, "argv", ["check_live_acceptance.py", "--root", str(tmp_path), "--dry-run"]
    )

    assert ACCEPTANCE.main() == 0
    text = capsys.readouterr().out
    assert "Live acceptance: DRY_RUN" in text
    assert "window    7 days" in text
    assert f"directory {POLICY['receipt_directories'][0]}" in text
    assert "receipt   one.json" in text


@pytest.mark.parametrize(("window", "stale"), [(1, True), (3, False)])
def test_cli_reads_the_freshness_window_from_the_selected_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    window: int,
    stale: bool,
) -> None:
    """Classify a two-day-old setup receipt by the window the --policy file declares.

    :param tmp_path: Fixture repository root holding the policy, schema and receipt.
    :param monkeypatch: Pytest fixture replacing process arguments.
    :param capsys: Pytest fixture capturing the CLI's JSON output.
    :param window: Freshness window written into the selected policy.
    :param stale: Whether the receipt must be reported stale under that window.

    :returns: None; assertions in the body validate the regression.
    """
    receipts = tmp_path / POLICY["receipt_directories"][0]
    policy = _write_policy(
        tmp_path,
        window,
        name="custom-policy.json",
        directories=POLICY["receipt_directories"][:1],
    )
    receipts.mkdir(parents=True)
    receipt = _access_setup()["access.json"]
    receipt["captured_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    (receipts / "access.json").write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_live_acceptance.py",
            "--root",
            str(tmp_path),
            "--skill",
            str(SKILL_ROOT),
            "--policy",
            str(policy),
            "--json",
        ],
    )

    assert ACCEPTANCE.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert ("access.json:receipt_stale" in report["problems"]) is stale


def test_receipt_older_than_the_policy_window_is_stale() -> None:
    """Block a setup receipt captured outside the declared freshness window.

    :returns: None; assertions in the body validate the regression.
    """
    setup = _access_setup()
    setup["access.json"]["captured_at"] = (
        NOW - timedelta(days=MAX_AGE_DAYS + 1)
    ).isoformat()
    receipts = _complete_receipts(setup)

    result = _evaluate(setup, receipts)

    assert result["status"] == "BLOCKED"
    assert "access.json:receipt_stale" in result["problems"]
    assert "access.json:setup_receipt_mismatch" not in result["problems"]


def test_window_applies_to_every_setup_receipt() -> None:
    """A zero-day window stales the one-day-old access and GitLab setup receipts.

    :returns: None; assertions in the body validate the regression.
    """
    setup = _access_setup()
    receipts = _complete_receipts(setup)

    narrow = _evaluate(setup, receipts, max_age_days=0)
    wide = _evaluate(setup, receipts, max_age_days=1)

    assert "access.json:receipt_stale" in narrow["problems"]
    assert "gitlab-access.json:receipt_stale" in narrow["problems"]
    assert not [problem for problem in wide["problems"] if "stale" in problem]


def test_window_applies_to_operation_receipts_and_the_cleanup_proof() -> None:
    """The GitLab receipt check and the runner cleanup proof use the same window.

    :returns: None; assertions in the body validate the regression.
    """
    setup = _access_setup()
    receipt = _runner_create(setup)
    required = {
        **_context(setup),
        "kind": "gitlab_runner",
        "operation": "create",
        "result_action": "APPLIED",
    }

    narrow = ACCEPTANCE._check_gitlab_receipt(
        "runner.json", receipt, required, _functional_digest(), NOW, 0
    )
    wide = ACCEPTANCE._check_gitlab_receipt(
        "runner.json", receipt, required, _functional_digest(), NOW, 1
    )

    assert "runner.json:receipt_stale" in narrow
    assert "runner.json:runner_smoke_cleanup_unproven" in narrow
    assert "runner.json:receipt_stale" not in wide
    assert "runner.json:runner_smoke_cleanup_unproven" not in wide


def test_receipt_inside_the_policy_window_is_fresh() -> None:
    """Accept a setup receipt captured exactly at the declared window.

    :returns: None; assertions in the body validate the regression.
    """
    setup = _access_setup()
    setup["access.json"]["captured_at"] = (
        NOW - timedelta(days=MAX_AGE_DAYS)
    ).isoformat()
    receipts = _complete_receipts(setup)

    result = _evaluate(setup, receipts)

    assert result["status"] == "PASS", result["problems"]


def test_no_committed_receipt_carries_a_host_path() -> None:
    """Require every committed acceptance JSON receipt to be portable.
    :returns: None; assertions in the body validate the regression.
    """
    for path in (REPO_ROOT / "tests" / "acceptance").rglob("*.json"):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        assert PORTABLE.portable(receipt) == receipt, path.name
