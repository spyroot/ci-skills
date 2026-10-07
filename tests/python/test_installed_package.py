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
from tests.python.conftest import REPO_ROOT, install_executable, load_module

SKILL_ROOT = REPO_ROOT / "ci-skills"

ENTRYPOINT_CASES = (
    ("access_check.py", (), "access_check", "PASS"),
    (
        "gitlab_job.py",
        ("--job-url", "https://gitlab.example.test/unit/repo/-/jobs/123"),
        "gitlab_job",
        "PASS",
    ),
    (
        "gitlab_pipeline.py",
        ("--project", "unit/repo", "--pipeline-id", "77"),
        "gitlab_pipeline",
        "PASS",
    ),
    ("storage_report.py", (), "storage_report", "PASS"),
    (
        "event_trace.py",
        ("--from", "2026-10-01T10:00:00Z", "--to", "2026-10-01T10:10:00Z"),
        "event_trace",
        "PASS",
    ),
    ("cilium_status.py", (), "cilium_status", "PASS"),
    ("gitlab_access.py", ("check", "--project", "unit/repo"), "gitlab_access", "PASS"),
    (
        "gitlab_milestone.py",
        ("create", "--project", "unit/repo", "--title", "unit-milestone"),
        "gitlab_milestone",
        "DRY_RUN",
    ),
    (
        "gitlab_issue.py",
        ("open-bug", "--project", "unit/repo", "--title", "unit-bug"),
        "gitlab_issue",
        "DRY_RUN",
    ),
    (
        "gitlab_wiki.py",
        (
            "create",
            "--project",
            "unit/repo",
            "--title",
            "unit-page",
            "--content-file",
            "@CONTENT@",
        ),
        "gitlab_wiki",
        "DRY_RUN",
    ),
    (
        "gitlab_runner.py",
        ("assign", "--project", "unit/repo", "--runner-id", "9"),
        "gitlab_runner",
        "DRY_RUN",
    ),
    (
        "ceph_cluster.py",
        ("--namespace", "rook-ceph", "--node", "worker-a", "--ready", "true"),
        "ceph_cluster",
        "PASS",
    ),
)

NODE_ENTRYPOINT_CASES = (
    ("cilium_node.py", "cilium_node"),
    ("ceph_kernel.py", "ceph_kernel"),
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
    if args[:3] == ["config", "get", "token"] and args[-2:] == [
        "--host", "gitlab.example.test"
    ]:
        print("unit-token")
        raise SystemExit(0)
    endpoint = (
        args[1]
        if len(args) > 1 and args[0] == "api" and not args[1].startswith("--")
        else args[-1]
    )
    if endpoint == "user":
        emit({
            "id": 24,
            "username": "unit-gl",
            "is_admin": True,
            "web_url": "https://gitlab.example.test/unit-gl",
        })
    if endpoint == "projects/unit%2Frepo":
        emit({
            "id": 12,
            "path_with_namespace": "unit/repo",
            "web_url": "https://gitlab.example.test/unit/repo",
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
    if endpoint == "projects/12/pipelines/77":
        emit({
            "id": 77,
            "project_id": 12,
            "status": "success",
            "ref": "main",
            "sha": "a" * 40,
        })
    if endpoint == "projects/12/pipelines/77/jobs?per_page=100&page=1":
        emit([{
            "id": 123,
            "name": "selected-job",
            "stage": "test",
            "status": "success",
            "pipeline": {"id": 77},
        }])
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
    if kargs == ["config", "view", "--minify", "-o", "jsonpath={..namespace}"]:
        print("default")
        raise SystemExit(0)
    if kargs == ["-n", "default", "get", "pods", "-o", "json"]:
        emit({"items": []})
    if kargs == ["get", "--raw=/apis/config.openshift.io/v1"]:
        emit({
            "kind": "APIResourceList",
            "groupVersion": "config.openshift.io/v1",
            "resources": [],
        })
    if kargs[:2] in (["-n", "cilium"], ["-n", "diagnostics"]):
        namespace = kargs[1]
        journal = namespace == "diagnostics"
        container = "journal-reader" if journal else "cilium-agent"
        pod = {
            "metadata": {
                "namespace": namespace,
                "name": "selected-node-pod",
                "uid": "selected-node-pod-uid",
            },
            "spec": {
                "nodeName": "worker-a",
                "containers": [{"name": container}],
            },
            "status": {
                "phase": "Running",
                "containerStatuses": [{
                    "name": container,
                    "state": {"running": {"startedAt": "now"}},
                }],
            },
        }
        if journal:
            pod["spec"]["containers"][0]["volumeMounts"] = [
                {"name": "journal", "mountPath": "/host-journal"}
            ]
            pod["spec"]["volumes"] = [
                {"name": "journal", "hostPath": {"path": "/var/log/journal"}}
            ]
        if "get" in kargs and "pods" in kargs:
            emit({"items": [pod]})
        if "get" in kargs and "pod" in kargs:
            emit(pod)
        if "exec" in kargs:
            if "-t" in kargs or "-i" in kargs:
                raise SystemExit(97)
            command = kargs[kargs.index("--") + 1:]
            if command[:1] == ["cilium-dbg"]:
                emit({"cilium": {"state": "Ok"}})
            if command[:1] == ["cilium-health"]:
                emit({"local": {"name": "worker-a"}, "nodes": []})
            if "--list-boots" in command:
                print("-1 selected-boot")
                raise SystemExit(0)
            if command[:2] == ["journalctl", "-k"]:
                print(json.dumps({
                    "__REALTIME_TIMESTAMP": "1760000000000000",
                    "MESSAGE": "libceph: mon0 connection refused",
                    "PRIORITY": "3",
                }))
                raise SystemExit(0)
        raise SystemExit(98)
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
    if "--context" not in args:
        raise SystemExit(98)
    if "debug" in args:
        if "--to-namespace=default" not in args or "-t" in args or "-i" in args:
            raise SystemExit(97)
        emit([{
            "ifname": "eno1",
            "mtu": 9000,
            "operstate": "UP",
            "link_type": "ether",
            "parentbus": "pci",
            "parentdev": "0000:0a:00.0",
            "addr_info": [{"family": "inet", "local": "192.0.2.10"}],
        }])
    if "-n" not in args:
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
            "\n[kubernetes.node_diagnostics]\n"
            'node = "worker-a"\n'
            "\n[kubernetes.node_diagnostics.cilium]\n"
            'namespace = "cilium"\nselector = "app=cilium"\n'
            'container = "cilium-agent"\n'
            "\n[kubernetes.node_diagnostics.journal]\n"
            'namespace = "diagnostics"\nselector = "app=journal"\n'
            'container = "journal-reader"\n'
            'directory = "/host-journal"\nhost_path = "/var/log/journal"\n'
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
    installed = tmp_path / "installed" / "ci-skills"
    installer = load_module(
        "skill_installer", REPO_ROOT / "tools" / "install_ci_skills.py"
    )
    installation = installer.install(SKILL_ROOT, installed.parent, dry_run=False)
    assert installation["status"] == "PASS"
    assert installation["algorithm"] == "sha256-tree-v1"
    assert installation["revision"]["verified"] is True
    return installed


def test_installed_bash_entrypoints_run_from_unrelated_directory(tmp_path: Path):
    """Copied Bash commands resolve their own libraries, not checkout libraries."""
    installed = _install_skill(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    install_executable(
        fake_bin, "gh", "#!/bin/sh\nprintf '%s\\n' '{\"enabled\":false}'\n"
    )
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    api = subprocess.run(
        [
            str(installed / "bin" / "ci-api"),
            "get",
            "--provider",
            "github",
            "--endpoint",
            "repos/unit/repo",
            "--field",
            "enabled",
            "--output",
            "json",
            "--log-level",
            "error",
        ],
        cwd=elsewhere,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert api.returncode == 0, api.stderr
    assert json.loads(api.stdout) is False

    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "Dockerfile"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    spec = tmp_path / "buildconfig.yaml"
    spec.write_text(
        "apiVersion: build.openshift.io/v1\n"
        "kind: BuildConfig\n"
        "metadata:\n  name: fixture-build\n  namespace: fixture-ns\n"
        f"  labels:\n    example.invalid/source-commit: {commit}\n"
        "spec:\n  source:\n    type: Binary\n    binary: {}\n"
        "  strategy:\n    type: Docker\n    dockerStrategy:\n"
        "      dockerfilePath: Dockerfile\n"
        "  output:\n    to:\n      kind: DockerImage\n"
        "      name: registry.example.invalid/demo/image:candidate\n"
        "  triggers: []\n",
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            str(installed / "bin" / "ci-binary-build"),
            "--spec",
            str(spec),
            "--source-repo",
            str(source),
            "--source-commit",
            commit,
            "--commit-label",
            "example.invalid/source-commit",
            "--dry-run",
        ],
        cwd=elsewhere,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    assert json.loads(build.stdout)["mode"] == "dry-run"


def _run_installed_node_script(
    installed: Path,
    script_name: str,
    args: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run an installed node script directly from an unrelated directory."""
    return subprocess.run(
        [sys.executable, str(installed / "bin" / script_name), *args],
        check=False,
        capture_output=True,
        cwd=cwd,
        env=env,
        text=True,
        timeout=10,
    )


def _assert_sanitized_failure_log(stderr: str, kind: str) -> None:
    """Installed API commands log one sanitized diagnostic line on stderr."""
    assert stderr
    assert "BLOCKED:" not in stderr
    assert "usage:" not in stderr.lower()
    assert "--definitely-invalid" not in stderr
    assert " ERROR " in stderr
    assert kind in stderr
    assert " failure " in stderr
    assert "result=BLOCKED" in stderr


@pytest.mark.parametrize(
    ("mode", "loader"), (("--json", json.loads), ("--yaml", yaml.safe_load))
)
@pytest.mark.parametrize(
    ("script_name", "extra_args", "kind", "expected_status"), ENTRYPOINT_CASES
)
def test_installed_entrypoints_run_from_unrelated_cwd_without_source_pythonpath(
    tmp_path,
    fake_bin,
    mode,
    loader,
    script_name,
    extra_args,
    kind,
    expected_status,
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
    content_file = tmp_path / "content.md"
    content_file.write_text("unit page\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    for name in ("GITLAB_TOKEN", "GITLAB_ACCESS_TOKEN", "OAUTH_TOKEN", "CI_JOB_TOKEN"):
        env.pop(name, None)
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
            str(installed / "bin" / script_name),
            "--target",
            str(target),
            "--revision",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            mode,
            *(
                str(content_file) if item == "@CONTENT@" else item
                for item in extra_args
            ),
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
    assert data["status"] == expected_status
    assert str(REPO_ROOT) not in result.stdout

    failure = subprocess.run(
        [
            sys.executable,
            str(installed / "bin" / script_name),
            "--target",
            str(tmp_path / "missing-target.toml"),
            mode,
            *(
                str(content_file) if item == "@CONTENT@" else item
                for item in extra_args
            ),
        ],
        check=False,
        capture_output=True,
        cwd=unrelated,
        env=env,
        text=True,
        timeout=10,
    )
    blocked = loader(failure.stdout)
    assert failure.returncode == 2
    assert blocked["kind"] == kind
    assert blocked["status"] == "BLOCKED"
    assert blocked["errors"]
    assert str(REPO_ROOT) not in failure.stdout


@pytest.mark.parametrize(
    ("mode", "loader"), (("--json", json.loads), ("--yaml", yaml.safe_load))
)
def test_installed_mtu_entrypoint_plans_and_applies_from_unrelated_cwd(
    tmp_path, fake_bin, mode, loader
):
    """The new installed command needs no source PYTHONPATH or ambient context."""
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
    script = installed / "bin" / "k8s_verify_mtu_consistency.py"
    base = [sys.executable, str(script), "--target", str(target), mode]
    plan = subprocess.run(
        base,
        check=False,
        capture_output=True,
        cwd=unrelated,
        env=env,
        text=True,
        timeout=10,
    )
    planned = loader(plan.stdout)
    assert plan.returncode == 0
    assert planned["status"] == "DRY_RUN"

    applied = subprocess.run(
        [*base, "--apply", "--confirm-plan", planned["plan_digest"]],
        check=False,
        capture_output=True,
        cwd=unrelated,
        env=env,
        text=True,
        timeout=10,
    )
    data = loader(applied.stdout)
    assert applied.returncode == 0
    assert data["status"] == "PASS"
    assert data["cleanup"]["verified"] is True
    assert data["summary"]["consistent"] is True
    assert data["records"][0]["interface"] == "eno1"
    assert str(REPO_ROOT) not in applied.stdout


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
            str(installed / "bin" / "access_check.py"),
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
    assert data["credential_sources"]["kubernetes"] == ("target:kubernetes.kubeconfigs")
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
            str(installed / "bin" / script_name),
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
    _assert_sanitized_failure_log(result.stderr, kind)
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
    """Installed node commands plan their declared route without source PYTHONPATH."""
    installed = _install_skill(tmp_path)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update({"HOME": str(tmp_path / "home"), "LC_ALL": "C"})
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    target = _write_target(tmp_path, kubeconfig)

    result = subprocess.run(
        [
            sys.executable,
            str(installed / "bin" / script_name),
            "--target",
            str(target),
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
    """Installed node entrypoints read existing Pods with fake native CLIs."""
    installed = _install_skill(tmp_path)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
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

    result = _run_installed_node_script(
        installed,
        script_name,
        (
            "--target",
            str(target),
            "--revision",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            mode,
        ),
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
    """Missing native CLIs block before any selected Pod exec."""
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
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    target = _write_target(tmp_path, kubeconfig)

    result = _run_installed_node_script(
        installed,
        script_name,
        (
            "--target",
            str(target),
            "--revision",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "--json",
        ),
        cwd=unrelated,
        env=env,
    )
    data = json.loads(result.stdout)

    assert result.returncode == 2
    assert data["kind"] == kind
    assert data["status"] == "BLOCKED"
    assert data["errors"]
    assert "missing_tool" in json.dumps(data["access_failures"])


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
            str(installed / "bin" / script_name),
            "--unknown",
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
