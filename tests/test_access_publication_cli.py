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

CONFIG = {
    "contexts": [{
        "name": "unit-context",
        "context": {"cluster": "cluster-a", "user": "unit-user"},
    }],
    "clusters": [{
        "name": "cluster-a",
        "cluster": {"server": "https://api.cluster.example.test:6443"},
    }],
    "users": [{"name": "unit-user", "user": {"token": "REDACTED"}}],
}

AGENT = "cilium-unit0"
AGENT_LABELS = {"k8s-app": "cilium"}
READY = {"conditions": [{"type": "Ready", "status": "True"}]}
CILIUM_DS = {
    "metadata": {"namespace": "kube-system", "name": "cilium"},
    "spec": {"selector": {"matchLabels": AGENT_LABELS}},
}
AGENT_POD = {
    "metadata": {"name": AGENT, "namespace": "kube-system", "uid": "uid-0",
                 "labels": AGENT_LABELS},
    "spec": {"nodeName": "unit-node"},
    "status": dict(READY, phase="Running"),
}
# A prefix sibling that is NOT an agent: the gate must not exec into it.
OPERATOR_POD = {
    "metadata": {"name": "cilium-operator-unit", "namespace": "kube-system",
                 "labels": {"name": "cilium-operator"}},
    "status": dict(READY, phase="Running"),
}

if tool == "kubectl":
    # The credential-source resolution asks one file what it defines, with no
    # --context, before any context-bound call is made.
    if "--context" not in args:
        if args[2:] == ["config", "view", "-o", "json"]:
            emit(CONFIG)
        raise SystemExit(98)
    context_index = args.index("--context")
    kargs = args[context_index + 2:]
    if kargs == ["config", "view", "-o", "json"]:
        emit(CONFIG)
    if kargs == ["get", "--raw=/version"]:
        emit({"major": "1", "minor": "32"})
    if kargs == ["auth", "whoami", "-o", "json"]:
        emit({"status": {"userInfo": {"username": "unit-admin"}}})
    if kargs[:2] == ["auth", "can-i"]:
        print("yes")
        raise SystemExit(0)
    if kargs == ["get", "daemonsets", "--all-namespaces", "-o", "json"]:
        emit({"items": [{"metadata": {"namespace": "kube-system", "name": "cilium"}}]})
    if kargs == ["get", "daemonsets", "-A", "-o", "json"]:
        emit({"items": [CILIUM_DS]})
    if kargs == ["get", "pods", "-A", "-o", "json"]:
        emit({"items": [AGENT_POD, OPERATOR_POD]})
    if kargs == ["-n", "kube-system", "exec", AGENT, "--",
                 "cilium-health", "status", "-o", "json"]:
        emit({"local": {"name": "unit-node"}, "nodes": [{"name": "unit-node"}]})
    # Named explicitly so an unexpected resource or a missing -A fails loudly
    # instead of being absorbed by a catch-all.
    if kargs[0] == "get" and kargs[-2:] == ["-o", "json"]:
        resource = kargs[1]
        namespaced = kargs[2:3] == ["-A"]
        if (resource, namespaced) in EXPECTED_READS:
            emit({"items": []})
        print(f"unexpected read: {resource} namespaced={namespaced}", file=sys.stderr)
        raise SystemExit(97)
    raise SystemExit(98)

raise SystemExit(127)
"""


def _expected_reads() -> str:
    """Render the collectors' declared reads into the fake tool's source."""
    from conftest import import_script_module

    reads = import_script_module("core.reads")
    pairs = sorted(
        {(resource, namespaced)
         for resources in reads.COLLECTOR_SETS.values()
         for resource, namespaced in resources.values()}
    )
    return "EXPECTED_READS = " + repr(set(pairs)) + "\n"


@pytest.fixture
def fake_access_tools(fake_bin):
    """Install fake native CLIs used by publication-mode tests."""
    body = FAKE_ACCESS_TOOLS.replace("AGENT = ", _expected_reads() + "AGENT = ", 1)
    for tool in ("gh", "glab", "kubectl"):
        install_executable(fake_bin, tool, body)
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
    assert data["kind"] == "access_receipt"
    assert set(data["credential_sources"]) == {"github", "gitlab", "kubernetes"}
    assert data["credential_sources"]["kubernetes"]["detail"]["context"] == "unit-context"
    assert data["credential_sources"]["kubernetes"]["detail"]["user_entry"] == "unit-user"
    names = {check["name"]: check for check in data["live_checks"]}
    assert names["cilium_health_exec"]["status"] == "PASS"
    assert names["cilium_health_exec"]["evidence"]["pod"] == "cilium-unit0"
    assert names["cilium_health_exec"]["evidence"]["agents_ready"] == 1
    assert "kubernetes_read:persistentvolumeclaims" in names
    assert data["execution_host"]["hostname"]


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
