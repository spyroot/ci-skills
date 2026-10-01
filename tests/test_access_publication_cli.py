"""CLI tests for publication-mode access receipts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import install_executable, parse_json_output, run_script

TEST_REVISION = "a" * 40

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
def fake_access_tools(fake_bin):
    """Install fake native CLIs used by publication-mode tests."""
    for tool in ("gh", "glab", "kubectl"):
        install_executable(fake_bin, tool, FAKE_ACCESS_TOOLS)
    return fake_bin


@pytest.fixture
def live_target_file(tmp_path: Path) -> Path:
    """Provide a target file with a concrete kubeconfig source for live gates."""
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
    fake_access_tools,
    live_target_file,
):
    """Publication mode requires every check the target declares."""
    result = run_script(
        "access_check.py",
        "--target",
        live_target_file,
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
    fake_access_tools,
    live_target_file,
):
    """Publication mode blocks when GitHub repository administration is absent."""
    result = run_script(
        "access_check.py",
        "--target",
        live_target_file,
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
    fake_access_tools,
    live_target_file,
):
    """Publication mode cannot pass if branch protection cannot be read back."""
    result = run_script(
        "access_check.py",
        "--target",
        live_target_file,
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
    fake_access_tools,
    live_target_file,
):
    """Publication mode requires at least one required branch protection check."""
    result = run_script(
        "access_check.py",
        "--target",
        live_target_file,
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


def test_publication_blocks_when_only_an_unrelated_check_is_required(
    fake_access_tools,
    live_target_file,
):
    """The defect: any nonempty set used to pass, so the declared one was moot."""
    result = run_script(
        "access_check.py",
        "--target",
        live_target_file,
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
    fake_access_tools,
    live_target_file,
):
    """Declared is a subset: more protection than asked for is not a failure."""
    result = run_script(
        "access_check.py",
        "--target",
        live_target_file,
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
    fake_access_tools,
    live_target_file,
):
    """`invalidate-cache` must never satisfy a declared `validate`."""
    result = run_script(
        "access_check.py",
        "--target",
        live_target_file,
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
    fake_access_tools,
    tmp_path,
):
    """An absent declaration must refuse, not fall back to accepting anything."""
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
        "--target",
        target,
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

    assert result.returncode == 2
    assert data["surfaces"]["github"]["reason"] == "required_checks_not_declared"


def test_the_written_receipt_carries_no_path_from_this_host(
    fake_access_tools,
    live_target_file,
    tmp_path,
):
    """Every path-bearing field must be declared, not only the ones we remember.

    `target_file` was added to the receipt without being added to
    `PATH_VALUE_FIELDS`, so the committable form carried an absolute home
    directory path. Asserting the absence of the host's own prefix catches the
    next field too, rather than the one field already fixed.
    """
    receipt_path = tmp_path / "written" / "receipt.json"

    result = run_script(
        "access_check.py",
        "--target",
        live_target_file,
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
    assert written["target_source"] == "argv:--target"
