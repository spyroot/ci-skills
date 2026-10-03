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
    (
        "ceph_cluster.py",
        ("--namespace", "rook-ceph", "--node", "worker-a", "--ready", "true"),
        "ceph_cluster",
    ),
)

NODE_ENTRYPOINT_CASES = (
    ("cilium_node.py", "cilium_node"),
    ("ceph_kernel.py", "ceph_kernel"),
)

FAKE_NODE_SUDO = """#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]


def emit(value):
    print(json.dumps(value))
    raise SystemExit(0)


if args[:2] == ["-n", "crictl"]:
    cargs = args[2:]
    if cargs == ["ps", "--output", "json"]:
        emit({
            "containers": [{
                "id": "0123456789abcdef",
                "metadata": {"name": "cilium-agent"},
                "state": "CONTAINER_RUNNING",
            }],
        })
    if cargs[:5] == ["exec", "--sync", "--timeout", "30", "0123456789abcdef"]:
        command = cargs[5:]
        if command == ["cilium-dbg", "status", "--verbose", "--output", "json"]:
            emit({"cilium": {"state": "Ok"}})
        if command == ["cilium-health", "status", "--verbose", "--output", "json"]:
            emit({"local": {"name": "node-a"}, "nodes": []})
    raise SystemExit(98)

if args[:2] == ["-n", "journalctl"]:
    jargs = args[2:]
    if "-k" in jargs and "--output=json" in jargs:
        print(json.dumps({
            "__REALTIME_TIMESTAMP": "1760000000000000",
            "MESSAGE": "libceph: mon0 connection refused",
            "PRIORITY": "3",
        }))
        raise SystemExit(0)
    raise SystemExit(98)

raise SystemExit(127)
"""


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

if tool == "oc":
    if "--context" not in args or "-n" not in args:
        raise SystemExit(98)
    if "exec" in args:
        if "-s" in args:
            emit({"health": {"status": "HEALTH_OK"}})
        if "osd" in args and "tree" in args:
            emit({"nodes": [{"type": "osd", "id": 0, "name": "osd.0", "status": "up"}]})
        if "pg" in args and "dump_stuck" in args:
            emit({"pg_stats": []})
    if "get" in args and "pods" in args:
        emit({
            "items": [{
                "metadata": {
                    "namespace": "rook-ceph",
                    "name": "rook-ceph-osd-0",
                    "labels": {"app": "rook-ceph-osd", "ceph-osd-id": "0"},
                },
                "spec": {"nodeName": "worker-a"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            }],
        })
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


def _toml_string(value: str | Path) -> str:
    """Return one TOML basic string for a synthetic path or name."""
    return json.dumps(str(value))


def _write_kubeconfig_for_target(
    path: Path,
    *,
    context: str = "unit-context",
    server: str = "https://api.cluster.example.test:6443",
    cluster: str = "cluster-a",
) -> Path:
    """Write a kubeconfig that target.py can match by context and server."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "apiVersion: v1\n"
            "kind: Config\n"
            "clusters:\n"
            f"- name: {cluster}\n"
            "  cluster:\n"
            f"    server: {server}\n"
            "contexts:\n"
            f"- name: {context}\n"
            "  context:\n"
            f"    cluster: {cluster}\n"
            "    user: admin-user\n"
            "users:\n"
            "- name: admin-user\n"
            "  user: {}\n"
        ),
        encoding="utf-8",
    )
    return path


def _write_target_with_kubeconfigs(
    tmp_path: Path, kubeconfigs: tuple[Path, ...]
) -> Path:
    """Write a target with an ordered combined kubeconfig path."""
    target = tmp_path / "target-kubeconfigs.toml"
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
            "kubeconfigs = ["
            + ", ".join(_toml_string(path) for path in kubeconfigs)
            + "]\n"
        ),
        encoding="utf-8",
    )
    return target


def _install_skill(tmp_path: Path) -> Path:
    installed = tmp_path / "installed" / "k8s-admin-diagnostics"
    installer = load_module(
        "skill_installer", REPO_ROOT / "tools" / "install_k8s_admin_diagnostics.py"
    )
    installation = installer.install(SKILL_ROOT, installed.parent, dry_run=False)
    assert installation["status"] == "PASS"
    assert installation["algorithm"] == "sha256-tree-v1"
    assert installation["revision"]["verified"] is True
    return installed


def _run_installed_node_script(
    installed: Path,
    script_name: str,
    args: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run an installed node script while forcing the Linux-only branch."""
    wrapper = (
        "import pathlib, runpy, sys\n"
        "script = pathlib.Path(sys.argv[1])\n"
        "sys.path.insert(0, str(script.parent))\n"
        "sys.platform = 'linux'\n"
        "sys.argv = [str(script), *sys.argv[2:]]\n"
        "runpy.run_path(str(script), run_name='__main__')\n"
    )
    return subprocess.run(
        [
            sys.executable,
            "-c",
            wrapper,
            str(installed / "scripts" / script_name),
            *args,
        ],
        check=False,
        capture_output=True,
        cwd=cwd,
        env=env,
        text=True,
        timeout=10,
    )


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
    installed = _install_skill(tmp_path)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    for tool in ("gh", "glab", "kubectl", "oc"):
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


def test_installed_api_entrypoint_uses_plural_kubeconfigs_from_unrelated_cwd(
    tmp_path,
    fake_bin,
):
    """The installed API gate uses the declared combined kubeconfig path."""
    installed = _install_skill(tmp_path)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    for tool in ("gh", "glab", "kubectl", "oc"):
        install_executable(fake_bin, tool, FAKE_NATIVE_TOOLS)
    matching = _write_kubeconfig_for_target(tmp_path / "matching.yaml")
    unrelated_kubeconfig = _write_kubeconfig_for_target(
        tmp_path / "unrelated.yaml",
        context="other-context",
        server="https://api.other.example.test:6443",
        cluster="other-cluster",
    )
    target = _write_target_with_kubeconfigs(tmp_path, (matching, unrelated_kubeconfig))
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
            str(installed / "scripts" / "access_check.py"),
            "--target",
            str(target),
            "--revision",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "--json",
        ],
        check=False,
        capture_output=True,
        cwd=unrelated,
        env=env,
        text=True,
        timeout=10,
    )
    data = json.loads(result.stdout)

    assert result.returncode == 0
    assert data["kind"] == "access_check"
    assert data["status"] == "PASS"
    assert data["credential_sources"]["kubernetes"] == (
        "target:kubernetes.kubeconfigs"
    )
    assert str(REPO_ROOT) not in result.stdout


@pytest.mark.parametrize(
    ("mode", "loader"), (("--json", json.loads), ("--yaml", yaml.safe_load))
)
@pytest.mark.parametrize(
    ("script_name", "kind"),
    (("access_check.py", "access_check"), ("ceph_cluster.py", "ceph_cluster")),
)
def test_installed_api_and_ceph_invalid_args_are_structured(
    tmp_path,
    mode,
    loader,
    script_name,
    kind,
):
    """Shared StructuredParser failures emit machine-readable stdout."""
    installed = _install_skill(tmp_path)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update({"HOME": str(tmp_path / "home"), "LC_ALL": "C"})

    result = subprocess.run(
        [
            sys.executable,
            str(installed / "scripts" / script_name),
            "--definitely-invalid",
            mode,
        ],
        check=False,
        capture_output=True,
        cwd=unrelated,
        env=env,
        text=True,
        timeout=10,
    )
    data: dict[str, Any] = loader(result.stdout)

    assert result.returncode == 2
    assert result.stderr == ""
    assert data["kind"] == kind
    assert data["status"] == "BLOCKED"
    assert data["errors"] == [{"source": "arguments", "reason": "invalid_arguments"}]


@pytest.mark.parametrize(
    ("mode", "loader"), (("--json", json.loads), ("--yaml", yaml.safe_load))
)
@pytest.mark.parametrize(("script_name", "kind"), NODE_ENTRYPOINT_CASES)
def test_installed_node_entrypoints_dry_run_from_unrelated_cwd(
    tmp_path,
    mode,
    loader,
    script_name,
    kind,
):
    """Node-local entrypoints smoke without target, revision, or source PYTHONPATH."""
    installed = _install_skill(tmp_path)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update({"HOME": str(tmp_path / "home"), "LC_ALL": "C"})

    result = subprocess.run(
        [
            sys.executable,
            str(installed / "scripts" / script_name),
            "--dry-run",
            mode,
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
    assert data["status"] == "DRY_RUN"
    assert data["probes"]
    assert str(REPO_ROOT) not in result.stdout


@pytest.mark.parametrize(
    ("mode", "loader"), (("--json", json.loads), ("--yaml", yaml.safe_load))
)
@pytest.mark.parametrize(("script_name", "kind"), NODE_ENTRYPOINT_CASES)
def test_installed_node_entrypoints_normal_smoke_with_fake_native_tools(
    tmp_path,
    fake_bin,
    mode,
    loader,
    script_name,
    kind,
):
    """Installed node entrypoints run normally with fake sudo/native tools."""
    installed = _install_skill(tmp_path)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    install_executable(fake_bin, "sudo", FAKE_NODE_SUDO)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "LC_ALL": "C",
            "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
        }
    )

    result = _run_installed_node_script(
        installed,
        script_name,
        (mode,),
        cwd=unrelated,
        env=env,
    )
    data: dict[str, Any] = loader(result.stdout)

    assert result.returncode == 0
    assert data["kind"] == kind
    assert data["status"] == "PASS"
    assert data["summary"]["record_count"] == 1
    assert str(REPO_ROOT) not in result.stdout


@pytest.mark.parametrize(("script_name", "kind"), NODE_ENTRYPOINT_CASES)
def test_installed_node_entrypoints_report_missing_native_dependency(
    tmp_path,
    script_name,
    kind,
):
    """Missing sudo/native access is a structured BLOCKED report."""
    installed = _install_skill(tmp_path)
    unrelated = tmp_path / "unrelated"
    empty_path = tmp_path / "empty-bin"
    unrelated.mkdir()
    empty_path.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "LC_ALL": "C",
            "PATH": str(empty_path),
        }
    )

    result = _run_installed_node_script(
        installed,
        script_name,
        ("--json",),
        cwd=unrelated,
        env=env,
    )
    data = json.loads(result.stdout)

    assert result.returncode == 2
    assert data["kind"] == kind
    assert data["status"] == "BLOCKED"
    assert data["errors"]
    assert "missing_tool" in json.dumps(data["errors"])


@pytest.mark.parametrize(
    ("mode", "loader"), (("--json", json.loads), ("--yaml", yaml.safe_load))
)
@pytest.mark.parametrize(("script_name", "kind"), NODE_ENTRYPOINT_CASES)
def test_installed_node_entrypoints_invalid_args_are_structured(
    tmp_path,
    mode,
    loader,
    script_name,
    kind,
):
    """Parser failures emit structured stdout and exit 2."""
    installed = _install_skill(tmp_path)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update({"HOME": str(tmp_path / "home"), "LC_ALL": "C"})

    result = subprocess.run(
        [
            sys.executable,
            str(installed / "scripts" / script_name),
            "--target",
            "target.toml",
            mode,
        ],
        check=False,
        capture_output=True,
        cwd=unrelated,
        env=env,
        text=True,
        timeout=10,
    )
    data: dict[str, Any] = loader(result.stdout)

    assert result.returncode == 2
    assert result.stderr == ""
    assert data["kind"] == kind
    assert data["status"] == "BLOCKED"
    assert data["errors"] == [{"source": "arguments", "reason": "invalid_arguments"}]
