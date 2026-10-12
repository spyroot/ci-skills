"""CLI tests for publication-mode access receipts.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping, MutableSequence, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import install_executable, parse_json_output, run_script

TEST_REVISION = "a" * 40


if TYPE_CHECKING:
    from lib.python.core.json_types import JSONValue


@pytest.fixture(autouse=True)
def _select_live_target_from_environment(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expose the selected target through the public environment contract.

    :param request: Pytest request used to detect the optional target fixture.
    :param monkeypatch: Fixture setting ``CI_SKILLS_TARGET`` for child commands.

    :returns: None; the helper records behavior through its side effects.
    """
    if "live_target_file" in request.fixturenames:
        monkeypatch.setenv(
            "CI_SKILLS_TARGET", str(request.getfixturevalue("live_target_file"))
        )


FAKE_ACCESS_TOOLS = """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

tool = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]

def emit(value):
    print(json.dumps(value))
    raise SystemExit(0)

if tool == "gh":
    if args[:2] == ["auth", "status"]:
        raise SystemExit(0)
    if args[:3] == ["api", "--hostname", "github.example.test"]:
        endpoint = args[3]
        if endpoint == "user":
            emit({"login": "unit-gh"})
        if endpoint == "repos/unit/repo/branches/main/protection":
            if os.environ.get("FAKE_GH_PROTECTION_FAIL") == "1":
                print("not found", file=sys.stderr)
                raise SystemExit(1)
            malformed = os.environ.get("FAKE_GH_PROTECTION_JSON")
            if malformed is not None:
                emit(json.loads(malformed))
            contexts = [
                value
                for value in os.environ.get(
                    "FAKE_GH_REQUIRED_CONTEXTS",
                    "",
                ).split(",")
                if value
            ]
            emit({"required_status_checks": {"contexts": contexts}})
        if endpoint == "repos/unit/repo" and "--jq" in args:
            print(os.environ.get("FAKE_GH_ADMIN", "false"))
            raise SystemExit(0)
        if endpoint == "repos/unit/repo":
            malformed = os.environ.get("FAKE_GH_REPOSITORY_JSON")
            if malformed is not None:
                emit(json.loads(malformed))
            emit({"full_name": "unit/repo"})
    raise SystemExit(98)

if tool == "glab":
    if args[:2] == ["auth", "status"]:
        raise SystemExit(0)
    if args[-1] == "user":
        emit({
            "username": "unit-gl",
            "is_admin": True,
            "web_url": "https://gitlab.example.test/unit-gl",
        })
    if args[-1] == "runners/all?per_page=1":
        emit([{"id": 1}])
    raise SystemExit(98)

if tool == "kubectl":
    context_index = args.index("--context")
    kargs = args[context_index + 2:]
    if kargs in (
        ["config", "view", "-o", "json"],
        ["config", "view", "--raw", "-o", "json"],
    ):
        emit({
            "contexts": [{
                "name": "unit-context",
                "context": {"cluster": "cluster-a", "user": "admin-user"},
            }],
            "clusters": [{
                "name": "cluster-a",
                "cluster": {"server": "https://api.cluster.example.test:6443"},
            }],
            "users": [{
                "name": "admin-user",
                "user": {"exec": {"command": "unit-auth", "args": ["token"]}},
            }],
        })
    if kargs == ["get", "--raw=/version"]:
        emit({"major": "1", "minor": "32"})
    if kargs == ["auth", "whoami", "-o", "json"]:
        emit({"status": {"userInfo": {"username": "unit-admin"}}})
    if kargs[:2] == ["auth", "can-i"]:
        print("yes")
        raise SystemExit(0)
    if "exec" in kargs:
        if "-t" in kargs or "-i" in kargs or "-ti" in kargs or "-it" in kargs:
            raise SystemExit(97)
        emit({"local": {"status": "reachable"}})
    if "get" in kargs:
        resource = kargs[kargs.index("get") + 1]
        resources = {
            "nodes": [{"metadata": {"name": "worker-a"}}],
            "pods": [{
                "metadata": {
                    "namespace": "kube-system",
                    "name": "cilium-ready",
                    "uid": "pod-cilium",
                    "labels": {"k8s-app": "cilium"},
                },
                "spec": {"nodeName": "worker-a"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            }],
            "persistentvolumeclaims": [],
            "persistentvolumes": [],
            "storageclasses": [],
            "csidrivers": [],
            "csinodes": [],
            "volumeattachments": [],
            "deployments": [],
            "statefulsets": [],
            "daemonsets": [{
                "metadata": {"namespace": "kube-system", "name": "cilium"},
                "spec": {"selector": {"matchLabels": {"k8s-app": "cilium"}}},
                "status": {"desiredNumberScheduled": 1, "numberReady": 1},
            }],
            "replicasets": [],
            "events": [],
            "events.events.k8s.io": [],
            "ciliumnodes.cilium.io": [],
        }
        if resource in resources:
            emit({"items": resources[resource]})
    raise SystemExit(98)

raise SystemExit(127)
"""


@pytest.fixture
def fake_access_tools(fake_bin: Path) -> Path:
    """Install fake gh, glab, and kubectl for publication checks.

    :param fake_bin: Pytest directory receiving native command doubles.
    :returns: Directory prepended to the child command path.
    """
    for tool in ("gh", "glab", "kubectl"):
        install_executable(fake_bin, tool, FAKE_ACCESS_TOOLS)
    return fake_bin


@pytest.fixture
def live_target_file(tmp_path: Path) -> Path:
    """Write a target declaring one required check and a kubeconfig.

    :param tmp_path: Scratch directory receiving target and kubeconfig files.
    :returns: Path to the fixture target.
    """
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    target = tmp_path / "target.toml"
    target.write_text(
        (
            "[github]\n"
            'host = "github.example.test"\n'
            'repository = "unit/repo"\n'
            'required_checks = ["portable-required-check"]\n'
            "\n"
            "[gitlab]\n"
            'url = "https://gitlab.example.test"\n'
            "\n"
            "[kubernetes]\n"
            'context = "unit-context"\n'
            'server = "https://api.cluster.example.test:6443"\n'
            f'kubeconfig = "{kubeconfig}"\n'
        ),
        encoding="utf-8",
    )
    return target


def test_access_check_publication_pass_reports_admin_and_required_check_receipt(
    fake_access_tools: Path,
    live_target_file: Path,
) -> None:
    """Require publication PASS to prove admin and declared branch check.

    :param fake_access_tools: Directory of native provider and cluster doubles.
    :param live_target_file: Target declaring the required check and kubeconfig.

    :returns: None; assertions in the body validate the regression.
    """
    result = run_script(
        "access_check.py",
        "--revision",
        TEST_REVISION,
        "--publication",
        "--json",
        fake_bin=fake_access_tools,
        env={
            "FAKE_GH_ADMIN": "true",
            "FAKE_GH_REQUIRED_CONTEXTS": "portable-required-check",
        },
    )
    data = parse_json_output(result)

    assert result.returncode == 0
    assert data["status"] == "PASS"
    assert data["publication"] is True
    assert "repository_admin" in data["surfaces"]["github"]["observed_capability"]
    assert "protection_read" in data["surfaces"]["github"]["observed_capability"]
    assert "required_checks_read" in data["surfaces"]["github"]["observed_capability"]
    assert data["surfaces"]["github"]["details"]["required_checks"] == [
        "portable-required-check"
    ]


def test_access_check_publication_denies_non_admin_identity(
    fake_access_tools: Path,
    live_target_file: Path,
) -> None:
    """Block publication when the selected GitHub identity lacks admin rights.

    :param fake_access_tools: Directory of native command doubles.
    :param live_target_file: Target declaring the required branch check.

    :returns: None; assertions in the body validate the regression.
    """
    result = run_script(
        "access_check.py",
        "--revision",
        TEST_REVISION,
        "--publication",
        "--json",
        fake_bin=fake_access_tools,
        env={"FAKE_GH_ADMIN": "false"},
    )
    data = parse_json_output(result)

    assert result.returncode == 2
    assert data["status"] == "BLOCKED"
    assert data["publication"] is True
    assert data["surfaces"]["github"]["status"] == "BLOCKED"
    assert data["surfaces"]["github"]["reason"] == "admin_required"


def test_access_check_publication_blocks_when_protection_read_fails(
    fake_access_tools: Path,
    live_target_file: Path,
) -> None:
    """Block publication if branch protection cannot be read back.

    :param fake_access_tools: Directory of native command doubles.
    :param live_target_file: Target declaring the protected branch check.

    :returns: None; assertions in the body validate the regression.
    """
    result = run_script(
        "access_check.py",
        "--revision",
        TEST_REVISION,
        "--publication",
        "--json",
        fake_bin=fake_access_tools,
        env={"FAKE_GH_ADMIN": "true", "FAKE_GH_PROTECTION_FAIL": "1"},
    )
    data = parse_json_output(result)

    assert result.returncode == 2
    assert data["status"] == "BLOCKED"
    assert data["surfaces"]["github"]["status"] == "BLOCKED"
    assert data["surfaces"]["github"]["reason"] == "command_failed"


def test_access_check_publication_blocks_when_required_checks_are_empty(
    fake_access_tools: Path,
    live_target_file: Path,
) -> None:
    """Block publication when protection read-back has no required checks.

    :param fake_access_tools: Directory of native command doubles.
    :param live_target_file: Target requiring a named branch check.

    :returns: None; assertions in the body validate the regression.
    """
    result = run_script(
        "access_check.py",
        "--revision",
        TEST_REVISION,
        "--publication",
        "--json",
        fake_bin=fake_access_tools,
        env={"FAKE_GH_ADMIN": "true", "FAKE_GH_REQUIRED_CONTEXTS": ""},
    )
    data = parse_json_output(result)

    assert result.returncode == 2
    assert data["status"] == "BLOCKED"
    assert data["surfaces"]["github"]["status"] == "BLOCKED"
    assert data["surfaces"]["github"]["reason"] == "required_checks_missing"


def test_access_check_blocks_when_github_repository_name_is_not_text(
    fake_access_tools: Path,
    live_target_file: Path,
) -> None:
    """Classify malformed repository identity before publication probes.

    :param fake_access_tools: Directory of native command doubles.
    :param live_target_file: Target selecting the repository under test.

    :returns: None; assertions in the body validate the regression.
    """
    result = run_script(
        "access_check.py",
        "--revision",
        TEST_REVISION,
        "--publication",
        "--json",
        fake_bin=fake_access_tools,
        env={
            "FAKE_GH_ADMIN": "true",
            "FAKE_GH_REPOSITORY_JSON": json.dumps({"full_name": False}),
            "FAKE_GH_REQUIRED_CONTEXTS": "portable-required-check",
        },
    )
    data = parse_json_output(result)

    assert result.returncode == 2
    assert data["status"] == "BLOCKED"
    assert data["surfaces"]["github"]["status"] == "BLOCKED"
    assert data["surfaces"]["github"]["reason"] == "JSON string required"


@pytest.mark.parametrize(
    "protection",
    (
        {"required_status_checks": None},
        {"required_status_checks": "enabled"},
        {"required_status_checks": []},
    ),
    ids=("null", "scalar", "array"),
)
def test_access_check_publication_blocks_malformed_required_status_checks(
    fake_access_tools: Path,
    live_target_file: Path,
    protection: Mapping[str, JSONValue],
) -> None:
    """Classify malformed branch-protection shapes as missing required checks.

    :param fake_access_tools: Directory of native command doubles.
    :param live_target_file: Target requiring a named branch check.
    :param protection: Malformed branch-protection JSON emitted by the fake CLI.

    :returns: None; assertions in the body validate the regression.
    """
    result = run_script(
        "access_check.py",
        "--revision",
        TEST_REVISION,
        "--publication",
        "--json",
        fake_bin=fake_access_tools,
        env={
            "FAKE_GH_ADMIN": "true",
            "FAKE_GH_PROTECTION_JSON": json.dumps(protection),
        },
    )
    data = parse_json_output(result)

    assert result.returncode == 2
    assert data["status"] == "BLOCKED"
    assert data["surfaces"]["github"]["status"] == "BLOCKED"
    assert data["surfaces"]["github"]["reason"] == "required_checks_missing"


def test_publication_blocks_when_only_an_unrelated_check_is_required(
    fake_access_tools: Path,
    live_target_file: Path,
) -> None:
    """Block a nonempty protection set missing the declared check.

    :param fake_access_tools: Directory of native command doubles.
    :param live_target_file: Target naming the check that must be present.

    :returns: None; assertions in the body validate the regression.
    """
    result = run_script(
        "access_check.py",
        "--revision",
        TEST_REVISION,
        "--publication",
        "--json",
        fake_bin=fake_access_tools,
        env={
            "FAKE_GH_ADMIN": "true",
            "FAKE_GH_REQUIRED_CONTEXTS": "some-unrelated-check",
        },
    )
    data = parse_json_output(result)

    assert result.returncode == 2
    assert data["status"] == "BLOCKED"
    assert data["surfaces"]["github"]["reason"] == (
        "required_checks_incomplete:portable-required-check"
    )


def test_publication_accepts_extra_checks_beyond_the_declared_set(
    fake_access_tools: Path,
    live_target_file: Path,
) -> None:
    """Accept protection that contains the required check plus extras.

    :param fake_access_tools: Directory of native command doubles.
    :param live_target_file: Target naming the minimum required check.

    :returns: None; assertions in the body validate the regression.
    """
    result = run_script(
        "access_check.py",
        "--revision",
        TEST_REVISION,
        "--publication",
        "--json",
        fake_bin=fake_access_tools,
        env={
            "FAKE_GH_ADMIN": "true",
            "FAKE_GH_REQUIRED_CONTEXTS": "portable-required-check,extra-check",
        },
    )
    data = parse_json_output(result)

    assert result.returncode == 0
    assert data["surfaces"]["github"]["details"]["required_checks"] == [
        "extra-check",
        "portable-required-check",
    ]


def test_a_declared_check_is_matched_exactly_not_by_substring(
    fake_access_tools: Path,
    live_target_file: Path,
) -> None:
    """Reject a protection context that only extends the declared name.

    :param fake_access_tools: Directory of native command doubles.
    :param live_target_file: Target naming the exact required check.

    :returns: None; assertions in the body validate the regression.
    """
    result = run_script(
        "access_check.py",
        "--revision",
        TEST_REVISION,
        "--publication",
        "--json",
        fake_bin=fake_access_tools,
        env={
            "FAKE_GH_ADMIN": "true",
            "FAKE_GH_REQUIRED_CONTEXTS": "portable-required-check-extended",
        },
    )
    data = parse_json_output(result)

    assert result.returncode == 2
    assert data["surfaces"]["github"]["reason"].startswith("required_checks_incomplete")


def test_publication_blocks_when_the_target_declares_no_required_checks(
    fake_access_tools: Path,
    tmp_path: Path,
) -> None:
    """Refuse publication when the target omits required-check policy.

    :param fake_access_tools: Directory of native command doubles.
    :param tmp_path: Scratch kubeconfig and target without required checks.

    :returns: None; assertions in the body validate the regression.
    """
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    target = tmp_path / "undeclared.toml"
    target.write_text(
        (
            "[github]\n"
            'host = "github.example.test"\n'
            'repository = "unit/repo"\n'
            "\n"
            "[gitlab]\n"
            'url = "https://gitlab.example.test"\n'
            "\n"
            "[kubernetes]\n"
            'context = "unit-context"\n'
            'server = "https://api.cluster.example.test:6443"\n'
            f'kubeconfig = "{kubeconfig}"\n'
        ),
        encoding="utf-8",
    )

    result = run_script(
        "access_check.py",
        "--revision",
        TEST_REVISION,
        "--publication",
        "--json",
        fake_bin=fake_access_tools,
        env={
            "CI_SKILLS_TARGET": str(target),
            "FAKE_GH_ADMIN": "true",
            "FAKE_GH_REQUIRED_CONTEXTS": "portable-required-check",
        },
    )
    data = parse_json_output(result)

    assert result.returncode == 2
    assert data["surfaces"]["github"]["reason"] == "required_checks_not_declared"


def test_the_written_receipt_carries_no_path_from_this_host(
    fake_access_tools: Path,
    live_target_file: Path,
    tmp_path: Path,
) -> None:
    """Require portable written receipts to exclude absolute host paths.

    :param fake_access_tools: Directory of native command doubles.
    :param live_target_file: Selected target whose captured path must be redacted.
    :param tmp_path: Scratch destination for the committable receipt.

    :returns: None; assertions in the body validate the regression.
    """
    receipt_path = tmp_path / "written" / "receipt.json"

    result = run_script(
        "access_check.py",
        "--revision",
        TEST_REVISION,
        "--publication",
        "--receipt-out",
        receipt_path,
        "--json",
        fake_bin=fake_access_tools,
        env={
            "FAKE_GH_ADMIN": "true",
            "FAKE_GH_REQUIRED_CONTEXTS": "portable-required-check",
        },
    )
    data = parse_json_output(result)
    body = receipt_path.read_text(encoding="utf-8")
    written = json.loads(body)

    assert result.returncode == 0
    assert data["status"] == "PASS"
    # The captured form names the real path; the written one may not.
    assert data["target_file"] == str(live_target_file)
    assert str(tmp_path) not in body
    assert written["target_file"].startswith("path:")
    assert written["target_source"] == "env:CI_SKILLS_TARGET"
    # The structural invariant, which holds for a field under any home
    # directory rather than only one under this fixture's tmp_path.
    assert _absolute_path_values(written) == []


def _absolute_path_values(value: JSONValue, path: str = "") -> Sequence[str]:
    """Find absolute path strings anywhere in a nested receipt.

    :param value: Current scalar, mapping, or list under inspection.
    :param path: Dotted/indexed location of that value in the receipt.
    :returns: Locations and values of strings beginning with ``/``.
    """
    if isinstance(value, str):
        return [f"{path}={value}"] if value.startswith("/") else []
    if isinstance(value, Mapping):
        found: MutableSequence[str] = []
        for name, child in value.items():
            found.extend(_absolute_path_values(child, f"{path}.{name}"))
        return found
    if isinstance(value, Sequence) and not isinstance(value, str):
        found: MutableSequence[str] = []
        for index, child in enumerate(value):
            found.extend(_absolute_path_values(child, f"{path}[{index}]"))
        return found
    return []


def test_a_forgotten_required_argument_is_not_reported_as_an_access_failure(
    live_target_file: Path,
) -> None:
    """Report a missing job URL before attempting provider access.

    :param live_target_file: Valid target that must not mask the usage failure.

    :returns: None; assertions in the body validate the regression.
    """
    result = run_script(
        "gitlab_job.py",
        "--json",
        env={"PATH": "/nonexistent", "CI_SKILLS_TARGET": str(live_target_file)},
    )
    data = parse_json_output(result)

    assert result.returncode == 2
    assert data["status"] == "BLOCKED"
    assert data["errors"][0]["source"] == "arguments"
    assert "--job-url" in data["errors"][0]["reason"]
    assert data["kind"] == "gitlab_job"
