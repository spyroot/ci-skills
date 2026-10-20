"""Regression tests for the required validation workflow gates.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest
import yaml
from conftest import (
    REPO_ROOT,
    install_executable,
    load_module,
    read_journal,
    selected_test_paths,
)

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject

WORKFLOW: Final = REPO_ROOT / ".github" / "workflows" / "validate.yml"
ACCEPTANCE: Final = load_module(
    "workflow_policy_check_live_acceptance",
    REPO_ROOT / "tools" / "check_live_acceptance.py",
)
POLICY: Final = ACCEPTANCE.load_policy(
    REPO_ROOT / ACCEPTANCE.POLICY_FILE, REPO_ROOT / ACCEPTANCE.POLICY_SCHEMA_FILE
)
CHECKER_COMMAND: Final = "python tools/check_live_acceptance.py"


def _workflow_text() -> str:
    """Read the committed GitHub validation workflow for policy assertions.

    :returns: Full UTF-8 workflow source text.
    :raises OSError: The workflow file cannot be read.
    """  # noqa: DOC502 - Path.read_text propagates filesystem errors.
    return WORKFLOW.read_text(encoding="utf-8")


def test_validate_workflow_runs_required_static_policy_gates() -> None:
    """The validation workflow calls the shared static entrypoint."""
    text = _workflow_text()

    assert 'scripts/ci/static.sh --base "$base_sha"' in text
    assert "git diff --check" in text


def test_validate_workflow_runs_installed_package_smoke() -> None:
    """The validation workflow runs the isolated installed-package smoke."""
    text = _workflow_text()

    assert "tests/python/test_installed_package.py" in text


def test_acceptance_evaluator_is_offline_and_unconditional() -> None:
    """GitHub runs the offline evaluator without invoking live publication."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["validate"]["steps"]

    matching = [
        step
        for step in steps
        if "tests/python/test_live_acceptance.py"
        in selected_test_paths(str(step.get("run", "")))
    ]
    assert len(matching) == 1, "exactly one offline acceptance evaluator must run"
    step = matching[0]
    assert "offline" in str(step.get("name", "")).lower()
    assert "if" not in step
    assert step.get("continue-on-error") in (None, False)
    assert "|| true" not in str(step.get("run"))


def test_validate_workflow_evaluates_committed_receipts_unconditionally() -> None:
    """GitHub runs the committed-receipt checker as one unconditional step."""
    workflow = yaml.safe_load(_workflow_text())
    steps = workflow["jobs"]["validate"]["steps"]

    matching = [step for step in steps if CHECKER_COMMAND in str(step.get("run", ""))]
    assert len(matching) == 1, "exactly one committed-receipt evaluation must run"
    step = matching[0]
    assert step.get("id") == "live_acceptance"
    assert "if" not in step
    assert step.get("continue-on-error") in (None, False)
    assert "|| true" not in str(step.get("run"))
    named = [step.get("id") for step in steps if step.get("id")]
    assert named.index("live_acceptance") > named.index("whitespace"), (
        "the live-acceptance step runs after every other check step"
    )


def test_validate_workflow_schedules_the_declared_freshness_check() -> None:
    """The workflow's schedule matches the tracked live-acceptance policy."""
    workflow = yaml.safe_load(_workflow_text())
    triggers = workflow["on"]

    assert POLICY["scheduled_workflow"] == ".github/workflows/validate.yml"
    assert [entry["cron"] for entry in triggers["schedule"]] == [
        POLICY["scheduled_cron"]
    ]
    assert triggers["workflow_dispatch"]["inputs"]["aggregate"]["default"] is False
    publication = next(
        item
        for item in workflow["jobs"]["validate"]["steps"]
        if item.get("id") == "live_acceptance"
    )
    assert "inputs.aggregate" in publication["env"]["AGGREGATE_ACCEPTANCE"]
    concurrency = workflow["concurrency"]
    assert concurrency["cancel-in-progress"] is False
    assert "github.sha" in concurrency["group"]


LIVE_SHA: Final = "a" * 40
LIVE_DIGEST: Final = "b" * 64
LIVE_TESTED_REVISION: Final = "c" * 40
LIVE_REPORT_URL: Final = "https://github.example.test/unit/repo/actions/runs/19"
FAKE_PUBLICATION_TOOLS: Final = r"""
import json
import os
import pathlib
import sys

if pathlib.Path(sys.argv[0]).name == "python":
    expected = ["tools/check_live_acceptance.py", "--root", ".", "--json"]
    aggregate = os.environ["AGGREGATE_ACCEPTANCE"] == "true"
    expected += ["--aggregate"] if aggregate else ["--no-aggregate", "--receipts", "tests/acceptance/candidate"]
    assert sys.argv[1:] == expected
    print(json.dumps({
        "status": os.environ["FAKE_ACCEPTANCE_STATUS"],
        "functional_digest": os.environ["FAKE_DIGEST"],
        "mode": "aggregate" if aggregate else "targeted",
        "tested_revision": os.environ["FAKE_TESTED_REVISION"],
    }))
    raise SystemExit(0 if os.environ["FAKE_ACCEPTANCE_STATUS"] == "PASS" else 1)

args = sys.argv[1:]
url = next(arg for arg in args if arg.startswith("https://"))
route = url.split("/repos/unit/repo/", 1)[1]
method = args[args.index("--request") + 1] if "--request" in args else "GET"
body = json.loads(args[args.index("--data") + 1]) if "--data" in args else {}
with pathlib.Path(os.environ["FAKE_JOURNAL"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"method": method, "route": route, "body": body}) + "\n")

mismatch = os.environ["FAKE_READBACK_MISMATCH"]
if route == "deployments" and method == "POST":
    response = {"id": 71}
elif route == "deployments/71" and method == "GET":
    response = {
        "sha": os.environ["CI_SOURCE_SHA"],
        "environment": os.environ["LIVE_ENVIRONMENT"],
        "payload": {
            "report_url": os.environ["REPORT_URL"],
            "functional_digest": os.environ["FAKE_DIGEST"],
            "mode": "aggregate" if os.environ["AGGREGATE_ACCEPTANCE"] == "true" else "targeted",
            "tested_revision": os.environ["FAKE_TESTED_REVISION"],
        },
    }
    if mismatch in {"sha", "environment"}:
        response[mismatch] = "different"
    elif mismatch == "report_url":
        response["payload"]["report_url"] = "https://example.test/different"
    elif mismatch in {"functional_digest", "mode", "tested_revision"}:
        response["payload"][mismatch] = "different"
elif route == "deployments/71/statuses":
    response = {
        "state": "success",
        "log_url": os.environ["REPORT_URL"],
        "environment_url": os.environ["REPORT_URL"],
    }
    if mismatch in response:
        response[mismatch] = "different"
    if method == "GET":
        response = [response]
else:
    raise SystemExit(98)
print(json.dumps(response))
"""


def _run_live_publication(
    fake_bin: Path,
    call_journal: Path,
    tmp_path: Path,
    *,
    checker_status: str = "PASS",
    mismatch: str = "",
    aggregate: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Sequence[JSONObject]]:
    """Execute workflow Bash with only checker and HTTP transport replaced.

    :param fake_bin: Fixture directory receiving offline command doubles.
    :param call_journal: Sanitized request journal owned by this test.
    :param tmp_path: Isolated workspace containing the generated report.
    :param checker_status: Receipt evaluator outcome and matching exit status.
    :param mismatch: Read-back field deliberately changed by the transport.
    :param aggregate: Manual aggregate mode instead of automatic targeted evaluation.
    :returns: Shell result and the recorded HTTP requests in execution order.
    """
    workflow = yaml.safe_load(_workflow_text())
    step = next(
        item
        for item in workflow["jobs"]["validate"]["steps"]
        if item.get("id") == "live_acceptance"
    )
    for tool in ("python", "curl"):
        install_executable(
            fake_bin, tool, f"#!{sys.executable}\n{FAKE_PUBLICATION_TOOLS}"
        )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "GH_TOKEN": "offline-test-token",
        "GITHUB_API_URL": "https://api.github.example.test",
        "GITHUB_REPOSITORY": "unit/repo",
        "CI_SOURCE_SHA": LIVE_SHA,
        "LIVE_ENVIRONMENT": step.get("env", {}).get("LIVE_ENVIRONMENT", ""),
        "REPORT_URL": LIVE_REPORT_URL,
        "FAKE_JOURNAL": str(call_journal),
        "FAKE_ACCEPTANCE_STATUS": checker_status,
        "FAKE_DIGEST": LIVE_DIGEST,
        "FAKE_TESTED_REVISION": LIVE_TESTED_REVISION,
        "AGGREGATE_ACCEPTANCE": str(aggregate).lower(),
        "FAKE_READBACK_MISMATCH": mismatch,
    }
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, read_journal(call_journal)


@pytest.mark.parametrize("aggregate", (False, True))
def test_live_acceptance_publishes_verified_candidate_and_report(
    fake_bin: Path, call_journal: Path, tmp_path: Path, aggregate: bool
) -> None:
    """Publish success only after candidate and report read-back agree."""
    result, calls = _run_live_publication(
        fake_bin, call_journal, tmp_path, aggregate=aggregate
    )

    assert result.returncode == 0, result.stderr
    assert [(item["method"], item["route"]) for item in calls] == [
        ("POST", "deployments"),
        ("GET", "deployments/71"),
        ("POST", "deployments/71/statuses"),
        ("GET", "deployments/71/statuses"),
    ]
    deployment = calls[0]["body"]
    assert isinstance(deployment, dict)
    assert deployment["ref"] == LIVE_SHA
    assert deployment["environment"] == "live-acceptance"
    assert deployment["auto_merge"] is False
    assert deployment["required_contexts"] == []
    assert deployment["production_environment"] is False
    assert deployment["payload"] == {
        "report_url": LIVE_REPORT_URL,
        "functional_digest": LIVE_DIGEST,
        "mode": "aggregate" if aggregate else "targeted",
        "tested_revision": LIVE_TESTED_REVISION,
    }
    status = calls[2]["body"]
    assert isinstance(status, dict)
    assert status["state"] == "success"
    assert status["auto_inactive"] is False
    assert status["log_url"] == status["environment_url"] == LIVE_REPORT_URL


def test_failed_live_acceptance_makes_no_deployment_requests(
    fake_bin: Path, call_journal: Path, tmp_path: Path
) -> None:
    """A failed receipt evaluation stops before any deployment mutation."""
    result, calls = _run_live_publication(
        fake_bin, call_journal, tmp_path, checker_status="BLOCKED"
    )

    assert result.returncode != 0
    assert calls == []


@pytest.mark.parametrize(
    "mismatch",
    (
        "sha",
        "environment",
        "report_url",
        "functional_digest",
        "mode",
        "tested_revision",
        "state",
        "log_url",
        "environment_url",
    ),
)
def test_live_acceptance_rejects_mismatched_deployment_readback(
    fake_bin: Path, call_journal: Path, tmp_path: Path, mismatch: str
) -> None:
    """Reject stale or unrelated deployment/status evidence from the API."""
    result, calls = _run_live_publication(
        fake_bin, call_journal, tmp_path, mismatch=mismatch
    )

    assert result.returncode != 0
    assert calls
    if mismatch in {
        "sha",
        "environment",
        "report_url",
        "functional_digest",
        "mode",
        "tested_revision",
    }:
        assert all(item["method"] != "POST" for item in calls[1:])


REQUIRED_REPORT_URL: Final = LIVE_REPORT_URL.replace(
    "github.example.test", "github.com"
)
REQUIRED_STATUS_MAX_ATTEMPTS: Final = 5
CURL_HTTP_FAILURE_EXIT: Final = 22
FAKE_REQUIRED_RESULT_TOOLS: Final = r"""
import json
import os
import pathlib
import sys

name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
journal = pathlib.Path(os.environ["FAKE_JOURNAL"])
if name == "python":
    assert args[0] == "tools/ci_required_result.py"
    operation = args[1]
    entry = {"operation": operation}
    if operation == "emit":
        entry["outcomes"] = json.loads(args[args.index("--step-results") + 1])
    else:
        assert operation == "aggregate"
elif name == "sleep":
    entry = {"operation": "sleep", "args": args}
else:
    assert name == "curl"
    method = args[args.index("-X") + 1] if "-X" in args else "GET"
    entry = {"operation": method}
    if method == "POST":
        entry["body"] = json.loads(args[args.index("-d") + 1])
with journal.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(entry) + "\n")
if name != "curl" or method != "GET":
    raise SystemExit(0)
get_exit_status = int(os.environ["FAKE_GET_EXIT_STATUS"])
if get_exit_status:
    raise SystemExit(get_exit_status)

reads = sum(
    json.loads(line)["operation"] == "GET"
    for line in journal.read_text().splitlines()
)
status = {
    "context": os.environ["STATUS_CONTEXT"],
    "state": "success",
    "target_url": os.environ["FAKE_TARGET_URL"],
}
response = {"sha": os.environ["CI_SOURCE_SHA"], "statuses": [status]}
mismatch = os.environ["FAKE_STATUS_MISMATCH"]
if reads == 1 and not mismatch:
    status["state"] = "pending"
elif mismatch == "sha":
    response["sha"] = "different"
elif mismatch:
    status[mismatch] = "different"
print(json.dumps(response))
"""


def _run_required_result(
    fake_bin: Path,
    call_journal: Path,
    tmp_path: Path,
    *,
    mismatch: str = "",
    get_exit_status: int = 0,
) -> tuple[subprocess.CompletedProcess[str], Sequence[JSONObject], int]:
    """Run the aggregate workflow against deterministic status visibility.

    :param fake_bin: Existing fixture directory for offline command doubles.
    :param call_journal: Request and emitter journal owned by this test.
    :param tmp_path: Isolated shell working directory.
    :param mismatch: Persistent wrong status field, or one initially pending read.
    :param get_exit_status: Curl GET exit status, including terminal HTTP failures.
    :returns: Shell outcome, ordered operations, and configured retry bound.
    """
    workflow = yaml.safe_load(_workflow_text())
    step = next(
        item
        for item in workflow["jobs"]["required-result"]["steps"]
        if "ci_required_result.py aggregate" in str(item.get("run", ""))
    )
    for tool in ("python", "curl", "sleep"):
        install_executable(
            fake_bin, tool, f"#!{sys.executable}\n{FAKE_REQUIRED_RESULT_TOOLS}"
        )
    environment = {
        **os.environ,
        **{key: str(value) for key, value in step["env"].items()},
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "GH_TOKEN": "offline-test-token",
        "GITHUB_REPOSITORY": "unit/repo",
        "GITHUB_RUN_ID": LIVE_REPORT_URL.rsplit("/", 1)[1],
        "CI_SOURCE_SHA": LIVE_SHA,
        "CI_RUNNER_IMAGE": f"unit/image@sha256:{LIVE_DIGEST}",
        "FAKE_JOURNAL": str(call_journal),
        "FAKE_TARGET_URL": REQUIRED_REPORT_URL,
        "FAKE_STATUS_MISMATCH": mismatch,
        "FAKE_GET_EXIT_STATUS": str(get_exit_status),
    }
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    attempts = int(step["env"].get("STATUS_READBACK_ATTEMPTS", 1))
    return result, read_journal(call_journal), attempts


@pytest.mark.parametrize("mismatch", ("", "sha", "context", "state", "target_url"))
def test_required_result_retries_readback_without_republishing(
    fake_bin: Path, call_journal: Path, tmp_path: Path, mismatch: str
) -> None:
    """Wait for visibility while rejecting persistent wrong commit or status.

    :param fake_bin: Isolated command-double directory.
    :param call_journal: Per-test operation journal.
    :param tmp_path: Isolated working directory.
    :param mismatch: Wrong field retained through every read, or initial delay only.
    """
    result, calls, attempts = _run_required_result(
        fake_bin, call_journal, tmp_path, mismatch=mismatch
    )
    assert 0 < attempts <= REQUIRED_STATUS_MAX_ATTEMPTS
    operations = [item["operation"] for item in calls]
    assert operations.count("POST") == 1
    post = next(item["body"] for item in calls if item["operation"] == "POST")
    assert post == {
        "state": "success",
        "context": "ci-skills/required",
        "description": "Required CI passed",
        "target_url": REQUIRED_REPORT_URL,
    }
    if mismatch:
        assert result.returncode != 0
        assert operations.count("GET") == attempts
        assert operations.count("sleep") == attempts - 1
    else:
        assert result.returncode == 0, result.stderr
        assert operations == ["aggregate", "POST", "GET", "sleep", "GET", "emit"]
    assert calls[-1] == {
        "operation": "emit",
        "outcomes": {
            "aggregate": "success",
            "status": "failure" if mismatch else "success",
        },
    }


def test_required_result_http_error_stops_readback_without_retry(
    fake_bin: Path, call_journal: Path, tmp_path: Path
) -> None:
    """Curl HTTP failures stop immediately and preserve failed status evidence.

    :param fake_bin: Isolated command-double directory.
    :param call_journal: Per-test operation journal.
    :param tmp_path: Isolated working directory.
    """
    result, calls, _attempts = _run_required_result(
        fake_bin, call_journal, tmp_path, get_exit_status=CURL_HTTP_FAILURE_EXIT
    )

    assert result.returncode != 0
    assert [item["operation"] for item in calls] == ["aggregate", "POST", "GET", "emit"]
    assert calls[-1] == {
        "operation": "emit",
        "outcomes": {"aggregate": "success", "status": "failure"},
    }


def test_required_result_download_excludes_its_previous_artifact() -> None:
    """Keep producer selection dynamic while excluding the reporting job itself."""
    workflow = yaml.safe_load(_workflow_text())
    job = workflow["jobs"]["required-result"]
    downloads = [
        step["with"]
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/download-artifact@")
    ]
    assert downloads == [
        {
            "pattern": "ci-evidence-!(${{ github.job }})",
            "path": "reports",
            "merge-multiple": True,
        }
    ]
    for producer in job["needs"]:
        uploads = [
            step["with"]["name"]
            for step in workflow["jobs"][producer]["steps"]
            if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        ]
        assert uploads == [f"ci-evidence-{producer}"]
