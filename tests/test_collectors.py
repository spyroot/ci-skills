"""Deterministic collector tests with mocked native command output."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import import_script_module


def _target(path: Path) -> Any:
    """Load the neutral test target."""
    return import_script_module("core.target").load_target(path)


def _command_result(runtime: Any, argv: tuple[str, ...], payload: Any, code: int = 0):
    """Create a command result with JSON stdout unless a string is supplied."""
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return runtime.CommandResult(argv, code, stdout, "")


def test_storage_report_filters_and_correlates_claim_pod_volume_and_node(
    monkeypatch,
    target_file,
):
    """Storage collection returns only matching PVCs with correlated consumers."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    inventory = {
        "nodes": [{"metadata": {"name": "worker-a"}}],
        "pods": [
            {
                "metadata": {"namespace": "app", "name": "selected-pod", "uid": "pod-a"},
                "spec": {
                    "nodeName": "worker-a",
                    "volumes": [
                        {"persistentVolumeClaim": {"claimName": "claim-selected"}}
                    ],
                },
                "status": {"phase": "Pending"},
            },
            {
                "metadata": {"namespace": "app", "name": "other-pod", "uid": "pod-b"},
                "spec": {
                    "nodeName": "worker-b",
                    "volumes": [{"persistentVolumeClaim": {"claimName": "claim-other"}}],
                },
                "status": {"phase": "Running"},
            },
        ],
        "persistentvolumeclaims": [
            {
                "metadata": {"namespace": "app", "name": "claim-selected"},
                "spec": {
                    "volumeName": "pv-selected",
                    "storageClassName": "fast",
                    "accessModes": ["ReadWriteOnce"],
                },
                "status": {"phase": "Bound", "capacity": {"storage": "10Gi"}},
            },
            {
                "metadata": {"namespace": "app", "name": "claim-other"},
                "spec": {"volumeName": "pv-other", "storageClassName": "slow"},
                "status": {"phase": "Pending"},
            },
        ],
        "persistentvolumes": [
            {
                "metadata": {"name": "pv-selected"},
                "spec": {"storageClassName": "fast", "csi": {"driver": "csi.fast"}},
                "status": {"phase": "Bound"},
            },
            {
                "metadata": {"name": "pv-other"},
                "spec": {"storageClassName": "slow"},
                "status": {"phase": "Pending"},
            },
        ],
        "storageclasses": [],
        "csidrivers": [],
        "csinodes": [],
        "volumeattachments": [
            {
                "metadata": {"name": "attach-selected"},
                "spec": {"source": {"persistentVolumeName": "pv-selected"}, "nodeName": "worker-a"},
                "status": {"attached": True},
            }
        ],
        "deployments": [],
        "statefulsets": [],
        "daemonsets": [],
        "replicasets": [],
    }

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        return _command_result(runtime, command, {"items": inventory[resource]})

    monkeypatch.setattr(collect, "run_command", fake_run)
    args = SimpleNamespace(
        namespace="app",
        node="worker-a",
        storage_class="fast",
        phase="Bound",
        search="selected",
    )

    result = collect.collect_storage(_target(target_file), args)

    assert result["status"] == "PASS"
    assert result["summary"]["record_count"] == 1
    row = result["records"][0]
    assert row["name"] == "claim-selected"
    assert row["nodes"] == ["worker-a"]
    assert row["attachments"] == [
        {"name": "attach-selected", "node": "worker-a", "attached": True}
    ]
    assert row["pods"][0]["pod_uid"] == "pod-a"
    assert row["csi_driver"] == "csi.fast"


def test_event_trace_filters_reason_object_search_and_sorts_by_source_time(
    monkeypatch,
    target_file,
):
    """Event collection keeps the requested object window in timestamp order."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    events = {
        "events": [
            {
                "metadata": {"namespace": "jobs", "creationTimestamp": "2026-10-01T10:05:00Z"},
                "involvedObject": {
                    "kind": "Pod",
                    "namespace": "jobs",
                    "name": "runner-pod",
                    "uid": "pod-1",
                },
                "lastTimestamp": "2026-10-01T10:05:00Z",
                "reason": "FailedScheduling",
                "message": "selected later",
                "type": "Warning",
            },
            {
                "metadata": {"namespace": "jobs", "creationTimestamp": "2026-10-01T10:02:00Z"},
                "involvedObject": {
                    "kind": "Pod",
                    "namespace": "jobs",
                    "name": "runner-pod",
                    "uid": "pod-1",
                },
                "lastTimestamp": "2026-10-01T10:02:00Z",
                "reason": "FailedScheduling",
                "message": "selected earlier",
                "type": "Warning",
            },
            {
                "metadata": {"namespace": "jobs", "creationTimestamp": "2026-10-01T10:04:00Z"},
                "involvedObject": {"kind": "Pod", "namespace": "jobs", "name": "runner-pod"},
                "lastTimestamp": "2026-10-01T10:04:00Z",
                "reason": "Pulled",
                "message": "selected but wrong reason",
            },
        ],
        "events.events.k8s.io": [
            {
                "metadata": {"namespace": "other", "creationTimestamp": "2026-10-01T10:03:00Z"},
                "regarding": {"kind": "Pod", "namespace": "other", "name": "runner-pod"},
                "eventTime": "2026-10-01T10:03:00Z",
                "reason": "FailedScheduling",
                "note": "selected but wrong namespace",
            }
        ],
    }

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        return _command_result(runtime, command, {"items": events[resource]})

    monkeypatch.setattr(collect, "run_command", fake_run)
    args = SimpleNamespace(
        from_time="2026-10-01T10:00:00Z",
        to_time="2026-10-01T10:10:00Z",
        namespace="jobs",
        kind="Pod",
        object="runner-pod",
        reason="Failed",
        search="selected",
    )

    result = collect.collect_events(_target(target_file), args)

    assert result["status"] == "PASS"
    assert [row["message"] for row in result["records"]] == [
        "selected earlier",
        "selected later",
    ]
    assert [row["timestamp"] for row in result["records"]] == sorted(
        row["timestamp"] for row in result["records"]
    )


def test_event_trace_rejects_naive_and_reversed_time_bounds(target_file):
    """Time bounds must be unambiguous before Kubernetes data is read."""
    collect = import_script_module("core.collect")
    target = _target(target_file)
    base = {
        "namespace": "all",
        "kind": None,
        "object": None,
        "reason": None,
        "search": None,
    }

    with pytest.raises(ValueError, match="timezone"):
        collect.collect_events(
            target,
            SimpleNamespace(
                **base,
                from_time="2026-10-01T10:00:00",
                to_time="2026-10-01T10:10:00Z",
            ),
        )
    with pytest.raises(ValueError, match="--from"):
        collect.collect_events(
            target,
            SimpleNamespace(
                **base,
                from_time="2026-10-01T10:10:00Z",
                to_time="2026-10-01T10:00:00Z",
            ),
        )


def test_cilium_status_records_unknown_health_and_uses_non_tty_exec(
    monkeypatch,
    target_file,
):
    """Agent health stays read-only and non-interactive, even on exec failure."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    exec_calls: list[tuple[str, ...]] = []
    resources = {
        "daemonsets": [
            {
                "metadata": {"namespace": "kube-system", "name": "cilium"},
                "status": {"desiredNumberScheduled": 2, "numberReady": 1},
            }
        ],
        "pods": [
            {
                "metadata": {
                    "namespace": "kube-system",
                    "name": "cilium-ready",
                    "uid": "pod-ready",
                    "labels": {"k8s-app": "cilium"},
                },
                "spec": {"nodeName": "worker-a"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            },
            {
                "metadata": {
                    "namespace": "kube-system",
                    "name": "cilium-not-ready",
                    "uid": "pod-wait",
                    "labels": {"k8s-app": "cilium"},
                },
                "spec": {"nodeName": "worker-b"},
                "status": {
                    "phase": "Pending",
                    "conditions": [{"type": "Ready", "status": "False"}],
                },
            },
        ],
        "deployments": [
            {
                "metadata": {"namespace": "kube-system", "name": "cilium-operator"},
                "status": {"readyReplicas": 1},
            }
        ],
        "ciliumnodes.cilium.io": [{"metadata": {"name": "worker-a"}, "status": {"ipam": {}}}],
    }

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        if "exec" in command:
            exec_calls.append(command)
            return runtime.CommandResult(command, 1, "", "connection refused")
        resource = command[command.index("get") + 1]
        return _command_result(runtime, command, {"items": resources[resource]})

    monkeypatch.setattr(collect, "run_command", fake_run)
    args = SimpleNamespace(namespace="auto", node=None, search=None)

    result = collect.collect_cilium(_target(target_file), args)

    assert result["status"] == "PARTIAL"
    rows = {row["name"]: row for row in result["records"]}
    assert rows["cilium-ready"]["status"] == "UNKNOWN"
    assert rows["cilium-ready"]["exit_status"] == 1
    assert rows["cilium-ready"]["reason"] == "transport"
    assert rows["cilium-not-ready"]["status"] == "UNKNOWN"
    assert rows["cilium-not-ready"]["exit_status"] is None
    assert {"source": "cilium-ready", "reason": "transport"} in result["errors"]
    assert {"source": "cilium-not-ready", "reason": "agent_not_ready"} in result["errors"]
    assert len(exec_calls) == 1
    exec_call = exec_calls[0]
    assert "-t" not in exec_call
    assert "-i" not in exec_call
    assert "-ti" not in exec_call
    assert "-it" not in exec_call
    separator = exec_call.index("--")
    assert exec_call[separator + 1 :] == (
        "cilium-health",
        "status",
        "-o",
        "json",
    )


def test_gitlab_job_rejects_job_url_from_different_fqdn(target_file):
    """A job URL from another host cannot reuse the selected target access."""
    collect = import_script_module("core.collect")
    args = SimpleNamespace(
        job_url="https://other.example.test/unit/repo/-/jobs/123",
        search=None,
    )

    with pytest.raises(ValueError, match="exact selected GitLab FQDN"):
        collect.collect_gitlab_job(_target(target_file), args)


def test_gitlab_job_collects_metadata_and_bounded_sanitized_trace(
    monkeypatch,
    target_file,
):
    """Job collection keeps only a bounded sanitized trace tail."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        endpoint = command[-1]
        if endpoint == "projects/unit%2Frepo/jobs/123":
            return _command_result(
                runtime,
                command,
                {
                    "id": 123,
                    "name": "selected-job",
                    "status": "failed",
                    "failure_reason": "runner_system_failure",
                    "pipeline": {"id": 77},
                    "runner": {"id": 9},
                    "created_at": "2026-10-01T10:00:00Z",
                },
            )
        if endpoint == "projects/unit%2Frepo/pipelines/77":
            return _command_result(runtime, command, {"id": 77, "status": "failed"})
        if endpoint == "runners/9":
            return _command_result(runtime, command, {"id": 9, "description": "runner-a"})
        if endpoint == "projects/unit%2Frepo/jobs/123/trace":
            trace = "\n".join([f"line {index}" for index in range(250)])
            return runtime.CommandResult(
                command,
                0,
                trace + "\npassword=topsecret\n",
                "",
            )
        return runtime.CommandResult(command, 1, "", "unexpected endpoint")

    monkeypatch.setattr(collect, "run_command", fake_run)
    args = SimpleNamespace(
        job_url="https://gitlab.example.test/unit/repo/-/jobs/123",
        search="selected",
    )

    result = collect.collect_gitlab_job(_target(target_file), args)

    assert result["status"] == "PASS"
    assert result["summary"]["record_count"] == 1
    row = result["records"][0]
    assert row["job_id"] == 123
    assert row["pipeline"] == {"id": 77, "status": "failed"}
    assert row["runner"] == {"id": 9, "description": "runner-a"}
    assert "topsecret" not in row["trace_tail"]
    assert "password=[REDACTED]" in row["trace_tail"]
    assert row["trace_tail"].splitlines()[0] == "line 51"
