"""Installed skill entrypoint smoke tests without source-tree PYTHONPATH."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import REPO_ROOT, install_executable, load_module

SKILL_ROOT = REPO_ROOT / "skills" / "k8s-admin-diagnostics"

ENTRYPOINT_CASES = (
    ("access_check.py", (), "access_check"),
    (
        "gitlab_job.py",
        ("--job-url", "https://gitlab.example.test/unit/repo/-/jobs/123"),
        "gitlab_job",
    ),
    ("storage_report.py", (), "storage_report"),
    (
        "event_trace.py",
        ("--from", "2026-10-01T10:00:00Z", "--to", "2026-10-01T10:10:00Z"),
        "event_trace",
    ),
    ("cilium_status.py", (), "cilium_status"),
)


FAKE_NATIVE_TOOLS = """#!/usr/bin/env python3
import json
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
        if endpoint == "repos/unit/repo":
            emit({"full_name": "unit/repo"})
    raise SystemExit(98)

if tool == "glab":
    if args[:2] == ["auth", "status"]:
        raise SystemExit(0)
    endpoint = args[-1]
    if endpoint == "user":
        emit({
            "username": "unit-gl",
            "is_admin": True,
            "web_url": "https://gitlab.example.test/unit-gl",
        })
    if endpoint == "runners/all?per_page=1":
        emit([{"id": 1}])
    if endpoint == "projects/unit%2Frepo/jobs/123":
        emit({
            "id": 123,
            "name": "selected-job",
            "status": "success",
            "pipeline": {"id": 77},
            "runner": {"id": 9},
            "created_at": "2026-10-01T10:00:00Z",
        })
    if endpoint == "projects/unit%2Frepo/pipelines/77":
        emit({"id": 77, "status": "success"})
    if endpoint == "runners/9":
        emit({"id": 9, "description": "runner-a"})
    if endpoint == "projects/unit%2Frepo/jobs/123/trace":
        print("selected trace line")
        raise SystemExit(0)
    raise SystemExit(98)

if tool == "kubectl":
    if "--context" not in args:
        raise SystemExit(98)
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
            "pods": [
                {
                    "metadata": {
                        "namespace": "app",
                        "name": "selected-pod",
                        "uid": "pod-a",
                        "labels": {"app": "selected"},
                    },
                    "spec": {
                        "nodeName": "worker-a",
                        "volumes": [{
                            "persistentVolumeClaim": {"claimName": "claim-selected"}
                        }],
                    },
                    "status": {"phase": "Running"},
                },
                {
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
                },
            ],
            "persistentvolumeclaims": [{
                "metadata": {"namespace": "app", "name": "claim-selected"},
                "spec": {
                    "volumeName": "pv-selected",
                    "storageClassName": "fast",
                    "accessModes": ["ReadWriteOnce"],
                },
                "status": {"phase": "Bound", "capacity": {"storage": "10Gi"}},
            }],
            "persistentvolumes": [{
                "metadata": {"name": "pv-selected"},
                "spec": {"storageClassName": "fast", "csi": {"driver": "csi.fast"}},
                "status": {"phase": "Bound"},
            }],
            "storageclasses": [{"metadata": {"name": "fast"}, "provisioner": "unit"}],
            "csidrivers": [{"metadata": {"name": "csi.fast"}}],
            "csinodes": [],
            "volumeattachments": [{
                "metadata": {"name": "attach-selected"},
                "spec": {
                    "source": {"persistentVolumeName": "pv-selected"},
                    "nodeName": "worker-a",
                },
                "status": {"attached": True},
            }],
            "deployments": [{
                "metadata": {"namespace": "kube-system", "name": "cilium-operator"},
                "status": {"readyReplicas": 1},
            }],
            "statefulsets": [],
            "daemonsets": [{
                "metadata": {"namespace": "kube-system", "name": "cilium"},
                "spec": {"selector": {"matchLabels": {"k8s-app": "cilium"}}},
                "status": {"desiredNumberScheduled": 1, "numberReady": 1},
            }],
            "replicasets": [],
            "events": [{
                "metadata": {
                    "namespace": "app",
                    "name": "event-selected",
                    "creationTimestamp": "2026-10-01T10:05:00Z",
                },
                "involvedObject": {
                    "kind": "Pod",
                    "namespace": "app",
                    "name": "selected-pod",
                    "uid": "pod-a",
                },
                "lastTimestamp": "2026-10-01T10:05:00Z",
                "reason": "Started",
                "message": "selected event",
                "type": "Normal",
            }],
            "events.events.k8s.io": [],
            "ciliumnodes.cilium.io": [{
                "metadata": {"name": "worker-a"},
                "status": {"ipam": {}},
            }],
        }
        emit({"items": resources.get(resource, [])})
    raise SystemExit(98)

raise SystemExit(127)
"""


def _write_target(tmp_path: Path, kubeconfig: Path) -> Path:
    target = tmp_path / "target.toml"
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
    return target


@pytest.mark.parametrize(
    ("mode", "loader"), (("--json", json.loads), ("--yaml", yaml.safe_load))
)
@pytest.mark.parametrize(("script_name", "extra_args", "kind"), ENTRYPOINT_CASES)
def test_installed_entrypoints_run_from_unrelated_cwd_without_source_pythonpath(
    tmp_path,
    fake_bin,
    mode,
    loader,
    script_name,
    extra_args,
    kind,
):
    """Every installed script renders normal machine output outside the repo."""
    installed = tmp_path / "installed" / "k8s-admin-diagnostics"
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    installer = load_module(
        "skill_installer", REPO_ROOT / "tools" / "install_k8s_admin_diagnostics.py"
    )
    installation = installer.install(SKILL_ROOT, installed.parent, dry_run=False)
    assert installation["status"] == "PASS"
    assert installation["algorithm"] == "sha256-tree-v1"
    assert installation["revision"]["verified"] is True
    for tool in ("gh", "glab", "kubectl"):
        install_executable(fake_bin, tool, FAKE_NATIVE_TOOLS)
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    target = _write_target(tmp_path, kubeconfig)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "LC_ALL": "C",
            "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            str(installed / "scripts" / script_name),
            "--target",
            str(target),
            "--revision",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            mode,
            *extra_args,
        ],
        check=False,
        capture_output=True,
        cwd=unrelated,
        env=env,
        text=True,
        timeout=10,
    )
    data: dict[str, Any] = loader(result.stdout)

    assert result.returncode == 0
    assert data["kind"] == kind
    assert data["status"] == "PASS"
    assert str(REPO_ROOT) not in result.stdout
