"""CLI tests for publication-mode access receipts."""

from __future__ import annotations

import pytest
from conftest import install_executable, parse_json_output, run_script

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
            emit({"required_status_checks": {"contexts": []}})
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
    if kargs == ["config", "view", "-o", "json"]:
        emit({
            "contexts": [{"name": "unit-context", "context": {"cluster": "cluster-a"}}],
            "clusters": [{
                "name": "cluster-a",
                "cluster": {"server": "https://api.cluster.example.test:6443"},
            }],
        })
    if kargs == ["get", "--raw=/version"]:
        emit({"major": "1", "minor": "32"})
    if kargs == ["auth", "whoami", "-o", "json"]:
        emit({"status": {"userInfo": {"username": "unit-admin"}}})
    if kargs[:2] == ["auth", "can-i"]:
        print("yes")
        raise SystemExit(0)
    if kargs == ["get", "daemonsets", "--all-namespaces", "-o", "json"]:
        emit({"items": [{"metadata": {"namespace": "kube-system", "name": "cilium"}}]})
    raise SystemExit(98)

raise SystemExit(127)
"""


@pytest.fixture
def fake_access_tools(fake_bin):
    """Install fake native CLIs used by publication-mode tests."""
    for tool in ("gh", "glab", "kubectl"):
        install_executable(fake_bin, tool, FAKE_ACCESS_TOOLS)
    return fake_bin


def test_access_check_publication_pass_reports_admin_receipt(
    fake_access_tools,
    target_file,
):
    """Publication mode records repository administration as observed evidence."""
    result = run_script(
        "access_check.py",
        "--target",
        target_file,
        "--publication",
        "--json",
        fake_bin=fake_access_tools,
        env={"FAKE_GH_ADMIN": "true"},
    )
    data = parse_json_output(result)

    assert result.returncode == 0
    assert data["status"] == "PASS"
    assert data["publication"] is True
    assert "repository_admin" in data["surfaces"]["github"]["observed_capability"]
    assert "protection_read" in data["surfaces"]["github"]["observed_capability"]


def test_access_check_publication_denies_non_admin_identity(
    fake_access_tools,
    target_file,
):
    """Publication mode blocks when GitHub repository administration is absent."""
    result = run_script(
        "access_check.py",
        "--target",
        target_file,
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
