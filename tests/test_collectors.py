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


def _write_gitlab_token_target(tmp_path: Path, token_file: Path) -> Path:
    """Write a target file with a GitLab token-file pointer."""
    path = tmp_path / "target-with-token.toml"
    path.write_text(
        (
            "[github]\n"
            'host = "github.example.test"\n'
            'repository = "unit/repo"\n'
            "\n"
            "[gitlab]\n"
            'url = "https://gitlab.example.test"\n'
            f'token_file = "{token_file}"\n'
            "\n"
            "[kubernetes]\n"
            'context = "unit-context"\n'
            'server = "https://api.cluster.example.test:6443"\n'
        ),
        encoding="utf-8",
    )
    return path


def _command_result(runtime: Any, argv: tuple[str, ...], payload: Any, code: int = 0):
    """Create a command result with JSON stdout unless a string is supplied."""
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return runtime.CommandResult(argv, code, stdout, "")


def _empty_storage_payloads() -> dict[str, Any]:
    """Return valid empty Kubernetes list responses for every storage resource."""
    return {
        "nodes": {"items": []},
        "pods": {"items": []},
        "persistentvolumeclaims": {"items": []},
        "persistentvolumes": {"items": []},
        "storageclasses": {"items": []},
        "csidrivers": {"items": []},
        "csinodes": {"items": []},
        "volumeattachments": {"items": []},
        "deployments": {"items": []},
        "statefulsets": {"items": []},
        "daemonsets": {"items": []},
        "replicasets": {"items": []},
    }


def test_storage_report_records_malformed_list_envelopes_as_errors(
    monkeypatch,
    target_file,
):
    """Malformed Kubernetes list envelopes must not become an empty PASS."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    payloads = _empty_storage_payloads()
    payloads.update(
        {
            "persistentvolumeclaims": None,
            "persistentvolumes": {},
            "volumeattachments": {"items": {"name": "not-a-list"}},
        }
    )

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        return _command_result(runtime, command, payloads[resource])

    monkeypatch.setattr(collect, "run_command", fake_run)
    args = SimpleNamespace(
        namespace="all",
        node=None,
        storage_class=None,
        phase="all",
        search=None,
    )

    result = collect.collect_storage(_target(target_file), args)

    assert result["status"] == "PARTIAL"
    assert result["summary"]["record_count"] == 0
    reasons = {error["source"]: error["reason"] for error in result["errors"]}
    assert reasons["pvcs"] == "invalid_list_response"
    assert reasons["pvs"] == "invalid_list_response"
    assert reasons["attachments"] == "invalid_list_response"


@pytest.mark.parametrize(
    ("phase", "expected_volume"),
    (("Released", "pv-released"), ("Failed", "pv-failed")),
)
def test_storage_report_includes_orphaned_pvs_for_phase_filters(
    monkeypatch,
    target_file,
    phase,
    expected_volume,
):
    """Standalone PVs without remaining PVCs are still first-class records."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    payloads = _empty_storage_payloads()
    payloads["persistentvolumes"] = {
        "items": [
            {
                "metadata": {"name": "pv-released"},
                "spec": {
                    "storageClassName": "slow",
                    "claimRef": {"namespace": "old", "name": "gone"},
                },
                "status": {"phase": "Released"},
            },
            {
                "metadata": {"name": "pv-failed"},
                "spec": {"storageClassName": "broken"},
                "status": {"phase": "Failed"},
            },
        ]
    }

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        return _command_result(runtime, command, payloads[resource])

    monkeypatch.setattr(collect, "run_command", fake_run)
    args = SimpleNamespace(
        namespace="all",
        node=None,
        storage_class=None,
        phase=phase,
        search=None,
    )

    result = collect.collect_storage(_target(target_file), args)

    assert result["status"] == "PASS"
    assert [row["volume"] for row in result["records"]] == [expected_volume]
    row = result["records"][0]
    assert row["name"] == expected_volume
    assert row["namespace"] is None
    assert row["phase"] == phase
    assert row["pv_phase"] == phase


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
                "metadata": {
                    "namespace": "app",
                    "name": "selected-pod",
                    "uid": "pod-a",
                },
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
                    "volumes": [
                        {"persistentVolumeClaim": {"claimName": "claim-other"}}
                    ],
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
                "spec": {
                    "source": {"persistentVolumeName": "pv-selected"},
                    "nodeName": "worker-a",
                },
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
                "metadata": {
                    "namespace": "jobs",
                    "name": "runner-pod.later",
                    "creationTimestamp": "2026-10-01T10:05:00Z",
                },
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
                "metadata": {
                    "namespace": "jobs",
                    "name": "runner-pod.earlier",
                    "creationTimestamp": "2026-10-01T10:02:00Z",
                },
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
                "metadata": {
                    "namespace": "jobs",
                    "name": "runner-pod.pulled",
                    "creationTimestamp": "2026-10-01T10:04:00Z",
                },
                "involvedObject": {
                    "kind": "Pod",
                    "namespace": "jobs",
                    "name": "runner-pod",
                },
                "lastTimestamp": "2026-10-01T10:04:00Z",
                "reason": "Pulled",
                "message": "selected but wrong reason",
            },
        ],
        "events.events.k8s.io": [
            {
                "metadata": {
                    "namespace": "other",
                    "name": "runner-pod.other",
                    "creationTimestamp": "2026-10-01T10:03:00Z",
                },
                "regarding": {
                    "kind": "Pod",
                    "namespace": "other",
                    "name": "runner-pod",
                },
                "eventTime": "2026-10-01T10:03:00Z",
                "reason": "FailedScheduling",
                "note": "selected but wrong namespace",
            }
        ],
    }
    events["events.events.k8s.io"] = events["events"] + events["events.events.k8s.io"]
    calls: list[str] = []

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        calls.append(resource)
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
    assert calls == ["events.events.k8s.io"]


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


def test_event_trace_uses_series_last_observed_time_inside_window(
    monkeypatch,
    target_file,
):
    """A repeated event stays visible when its series update is in the window."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    events = {
        "events": [],
        "events.events.k8s.io": [
            {
                "metadata": {
                    "namespace": "jobs",
                    "name": "runner-pod.abc",
                    "creationTimestamp": "2026-10-01T09:00:00Z",
                },
                "regarding": {
                    "kind": "Pod",
                    "namespace": "jobs",
                    "name": "runner-pod",
                    "uid": "pod-series",
                },
                "eventTime": "2026-10-01T09:00:00Z",
                "series": {
                    "lastObservedTime": "2026-10-01T10:05:00Z",
                    "count": 7,
                },
                "reason": "FailedScheduling",
                "note": "selected series update",
                "type": "Warning",
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
        search="series",
    )

    result = collect.collect_events(_target(target_file), args)

    assert result["status"] == "PASS"
    assert result["summary"]["record_count"] == 1
    row = result["records"][0]
    assert row["timestamp"] == "2026-10-01T10:05:00+00:00"
    assert row["first_timestamp"] == "2026-10-01T09:00:00+00:00"
    assert row["last_timestamp"] == "2026-10-01T10:05:00+00:00"
    assert row["count"] == 7


def test_event_trace_prefers_deprecated_count_before_core_count(
    monkeypatch,
    target_file,
):
    """Event count preserves events.k8s.io deprecatedCount before generic count."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    event = {
        "metadata": {
            "namespace": "jobs",
            "name": "runner-pod.counts",
            "creationTimestamp": "2026-10-01T10:01:00Z",
        },
        "regarding": {
            "kind": "Pod",
            "namespace": "jobs",
            "name": "runner-pod",
            "uid": "pod-counts",
        },
        "eventTime": "2026-10-01T10:02:00Z",
        "deprecatedCount": 5,
        "count": 2,
        "reason": "FailedScheduling",
        "note": "selected deprecated count",
        "type": "Warning",
    }

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        assert resource == "events.events.k8s.io"
        return _command_result(runtime, command, {"items": [event]})

    monkeypatch.setattr(collect, "run_command", fake_run)
    args = SimpleNamespace(
        from_time="2026-10-01T10:00:00Z",
        to_time="2026-10-01T10:10:00Z",
        namespace="jobs",
        kind="Pod",
        object="runner-pod",
        reason="Failed",
        search="deprecated count",
    )

    result = collect.collect_events(_target(target_file), args)

    assert result["status"] == "PASS"
    assert result["records"][0]["count"] == 5


def test_event_trace_reports_malformed_event_items_as_partial(
    monkeypatch,
    target_file,
):
    """Malformed event list items must be recorded as evidence errors."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    events = {
        "events": [
            {
                "metadata": {"namespace": "jobs"},
                "reason": "FailedScheduling",
                "message": "missing involved object and timestamps",
            }
        ],
        "events.events.k8s.io": [
            {
                "metadata": {"namespace": "jobs"},
                "regarding": {"kind": "Pod"},
                "note": "missing object name and event time",
            }
        ],
    }

    calls: list[str] = []

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        calls.append(resource)
        return _command_result(runtime, command, {"items": events[resource]})

    monkeypatch.setattr(collect, "run_command", fake_run)
    args = SimpleNamespace(
        from_time="2026-10-01T10:00:00Z",
        to_time="2026-10-01T10:10:00Z",
        namespace="jobs",
        kind="Pod",
        object="runner-pod",
        reason=None,
        search=None,
    )

    result = collect.collect_events(_target(target_file), args)

    assert result["status"] == "PARTIAL"
    assert result["records"] == []
    reasons = {error["source"]: error["reason"] for error in result["errors"]}
    assert reasons == {"events_v1": "invalid_list_response"}
    assert calls == ["events.events.k8s.io"]


@pytest.mark.parametrize(
    "invalid_field",
    (
        {"series": ["unexpected"]},
        {"series": []},
        {"regarding": "unexpected"},
        {"eventTime": 123},
        {"reason": ["unexpected"]},
        {"note": {"unexpected": True}},
    ),
)
def test_event_trace_reports_malformed_nested_fields_as_partial(
    monkeypatch,
    target_file,
    invalid_field,
):
    """Bad nested Event evidence must not escape the structured report."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    event = {
        "metadata": {"name": "runner-pod.abc", "namespace": "jobs"},
        "regarding": {"kind": "Pod", "name": "runner-pod", "namespace": "jobs"},
        "eventTime": "2026-10-01T10:02:00Z",
        "reason": "FailedScheduling",
        "note": "scheduling failure",
    }
    event.update(invalid_field)

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        assert command[command.index("get") + 1] == "events.events.k8s.io"
        return _command_result(runtime, command, {"items": [event]})

    monkeypatch.setattr(collect, "run_command", fake_run)
    args = SimpleNamespace(
        from_time="2026-10-01T10:00:00Z",
        to_time="2026-10-01T10:10:00Z",
        namespace="all",
        kind=None,
        object=None,
        reason=None,
        search=None,
    )

    result = collect.collect_events(_target(target_file), args)

    assert result["status"] == "PARTIAL"
    assert result["records"] == []
    assert result["errors"] == [
        {"source": "events_v1", "reason": "invalid_event_record"}
    ]


@pytest.mark.parametrize(
    ("stderr", "expected_reason"),
    (
        ("InternalError: apiserver failed while listing events", "command_failed"),
        ("Error from server (TooManyRequests): rate limited", "command_failed"),
    ),
)
def test_event_trace_does_not_fallback_on_preferred_api_server_failure(
    monkeypatch,
    target_file,
    stderr,
    expected_reason,
):
    """Generic preferred API failures and throttling are not compatibility gaps."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    calls: list[str] = []

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        calls.append(resource)
        if resource != "events.events.k8s.io":
            raise AssertionError(f"unexpected fallback read: {resource}")
        return runtime.CommandResult(command, 1, "", stderr)

    monkeypatch.setattr(collect, "run_command", fake_run)
    args = SimpleNamespace(
        from_time="2026-10-01T10:00:00Z",
        to_time="2026-10-01T10:10:00Z",
        namespace="all",
        kind=None,
        object=None,
        reason=None,
        search=None,
    )

    result = collect.collect_events(_target(target_file), args)

    assert result["status"] == "PARTIAL"
    assert result["records"] == []
    assert result["errors"] == [{"source": "events_v1", "reason": expected_reason}]
    assert calls == ["events.events.k8s.io"]


def test_event_trace_falls_back_and_records_compatibility_reason(
    monkeypatch,
    target_file,
):
    """Exact preferred resource absence may use the compatible core API once."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    calls: list[str] = []
    core_event = {
        "metadata": {
            "namespace": "jobs",
            "name": "runner-pod.fallback",
            "creationTimestamp": "2026-10-01T10:05:00Z",
        },
        "involvedObject": {
            "kind": "Pod",
            "namespace": "jobs",
            "name": "runner-pod",
            "uid": "pod-fallback",
        },
        "lastTimestamp": "2026-10-01T10:05:00Z",
        "count": 4,
        "reason": "FailedScheduling",
        "message": "read from core fallback",
        "type": "Warning",
    }

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        calls.append(resource)
        if resource == "events.events.k8s.io":
            return runtime.CommandResult(
                command,
                1,
                "",
                'the server does not have a resource type "events.events.k8s.io"',
            )
        return _command_result(runtime, command, {"items": [core_event]})

    monkeypatch.setattr(collect, "run_command", fake_run)
    args = SimpleNamespace(
        from_time="2026-10-01T10:00:00Z",
        to_time="2026-10-01T10:10:00Z",
        namespace="jobs",
        kind="Pod",
        object="runner-pod",
        reason="Failed",
        search="fallback",
    )

    result = collect.collect_events(_target(target_file), args)

    assert result["status"] == "PARTIAL"
    assert result["errors"] == [
        {"source": "events_v1", "reason": "compatibility_fallback"}
    ]
    assert result["records"][0]["source"] == "core_events"
    assert result["records"][0]["count"] == 4
    assert calls == ["events.events.k8s.io", "events"]


def test_event_trace_does_not_hide_preferred_api_access_denial(
    monkeypatch,
    target_file,
):
    """Authentication and authorization failures block without substitution."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    calls: list[str] = []

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        calls.append(command[command.index("get") + 1])
        return runtime.CommandResult(command, 1, "", "Forbidden")

    monkeypatch.setattr(collect, "run_command", fake_run)
    args = SimpleNamespace(
        from_time="2026-10-01T10:00:00Z",
        to_time="2026-10-01T10:10:00Z",
        namespace="all",
        kind=None,
        object=None,
        reason=None,
        search=None,
    )

    result = collect.collect_events(_target(target_file), args)

    assert result["status"] == "PARTIAL"
    assert result["errors"] == [{"source": "events_v1", "reason": "authorization"}]
    assert calls == ["events.events.k8s.io"]


def test_event_trace_reports_both_errors_when_fallback_is_malformed(
    monkeypatch,
    target_file,
):
    """An unavailable preferred API and malformed fallback both remain visible."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    calls: list[str] = []

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        calls.append(resource)
        if resource == "events.events.k8s.io":
            return runtime.CommandResult(command, 1, "", "resource type unavailable")
        return _command_result(runtime, command, {})

    monkeypatch.setattr(collect, "run_command", fake_run)
    args = SimpleNamespace(
        from_time="2026-10-01T10:00:00Z",
        to_time="2026-10-01T10:10:00Z",
        namespace="all",
        kind=None,
        object=None,
        reason=None,
        search=None,
    )

    result = collect.collect_events(_target(target_file), args)

    assert result["status"] == "PARTIAL"
    assert result["errors"] == [
        {"source": "events_v1", "reason": "command_failed"},
        {"source": "core_events", "reason": "invalid_list_response"},
    ]
    assert calls == ["events.events.k8s.io", "events"]


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
                "spec": {"selector": {"matchLabels": {"k8s-app": "cilium"}}},
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
        "ciliumnodes.cilium.io": [
            {"metadata": {"name": "worker-a"}, "status": {"ipam": {}}}
        ],
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
    assert {"source": "cilium-not-ready", "reason": "agent_not_ready"} in result[
        "errors"
    ]
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


def test_cilium_status_uses_daemonset_selector_for_agent_pods(
    monkeypatch,
    target_file,
):
    """Agent discovery follows the DaemonSet selector, not a fixed label."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    resources = {
        "daemonsets": [
            {
                "metadata": {"namespace": "networking", "name": "cilium"},
                "spec": {
                    "selector": {
                        "matchLabels": {"app.kubernetes.io/name": "cilium-agent"}
                    }
                },
                "status": {"desiredNumberScheduled": 1, "numberReady": 1},
            }
        ],
        "pods": [
            {
                "metadata": {
                    "namespace": "networking",
                    "name": "cilium-selected",
                    "uid": "pod-selected",
                    "labels": {"app.kubernetes.io/name": "cilium-agent"},
                },
                "spec": {"nodeName": "worker-a"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            }
        ],
        "deployments": [],
        "ciliumnodes.cilium.io": [],
    }

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        if "exec" in command:
            return runtime.CommandResult(
                command,
                0,
                json.dumps({"local": {"host-primary-address": "10.0.0.10"}}),
                "",
            )
        resource = command[command.index("get") + 1]
        return _command_result(runtime, command, {"items": resources[resource]})

    monkeypatch.setattr(collect, "run_command", fake_run)
    args = SimpleNamespace(namespace="networking", node=None, search=None)

    result = collect.collect_cilium(_target(target_file), args)

    assert result["status"] == "PASS"
    assert result["summary"]["record_count"] == 1
    assert result["records"][0]["name"] == "cilium-selected"
    assert result["records"][0]["status"] == "PASS"


def test_cilium_status_reports_partial_for_explicit_namespace_with_zero_pods(
    monkeypatch,
    target_file,
):
    """An explicit namespace with no agent Pods is a diagnostic error."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    resources = {
        "daemonsets": [
            {
                "metadata": {"namespace": "networking", "name": "cilium"},
                "spec": {"selector": {"matchLabels": {"app": "cilium"}}},
                "status": {"desiredNumberScheduled": 1, "numberReady": 1},
            }
        ],
        "pods": [
            {
                "metadata": {
                    "namespace": "networking",
                    "name": "cilium-one",
                    "labels": {"app": "cilium"},
                },
                "spec": {"nodeName": "worker-a"},
                "status": {"phase": "Running"},
            }
        ],
        "deployments": [],
        "ciliumnodes.cilium.io": [],
    }

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        return _command_result(runtime, command, {"items": resources[resource]})

    monkeypatch.setattr(collect, "run_command", fake_run)
    args = SimpleNamespace(namespace="missing", node=None, search=None)

    result = collect.collect_cilium(_target(target_file), args)

    assert result["status"] == "PARTIAL"
    assert result["summary"]["record_count"] == 1
    assert {"source": "daemonsets", "reason": "cilium_daemonset_missing"} in result[
        "errors"
    ]
    assert {"source": "pods", "reason": "cilium_agent_pods_missing"} in result["errors"]
    placeholder = result["records"][0]
    assert placeholder["status"] == "UNKNOWN"
    assert placeholder["reason"] == "no_matching_agent_pods"


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
    tmp_path,
):
    """Job collection keeps only a bounded sanitized trace tail."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    token = "unit-token-value"
    token_file = tmp_path / ".config" / "ci-skills" / "gitlab.example.test.token"
    token_file.parent.mkdir(parents=True)
    token_file.write_text(token + "\n", encoding="utf-8")
    trace_calls = []
    run_envs: list[dict[str, str] | None] = []

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        run_envs.append(_kwargs.get("env"))
        assert token not in command
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
            return _command_result(
                runtime, command, {"id": 9, "description": "runner-a"}
            )
        return runtime.CommandResult(command, 1, "", "unexpected endpoint")

    def fake_tail(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        assert token not in command
        trace_calls.append((command, _kwargs))
        trace = "\n".join([f"line {index}" for index in range(250)])
        return runtime.CommandResult(command, 0, trace + "\npassword=topsecret\n", "")

    monkeypatch.setattr(collect, "run_command", fake_run)
    monkeypatch.setattr(collect, "run_command_tail", fake_tail)
    args = SimpleNamespace(
        job_url="https://gitlab.example.test/unit/repo/-/jobs/123",
        search="selected",
    )

    result = collect.collect_gitlab_job(
        _target(_write_gitlab_token_target(tmp_path, token_file)), args
    )

    assert result["status"] == "PASS"
    assert result["summary"]["record_count"] == 1
    row = result["records"][0]
    assert row["job_id"] == 123
    assert row["pipeline"] == {"id": 77, "status": "failed"}
    assert row["runner"] == {"id": 9, "description": "runner-a"}
    assert "topsecret" not in row["trace_tail"]
    assert "password=[REDACTED]" in row["trace_tail"]
    assert row["trace_tail"].splitlines()[0] == "line 51"
    assert run_envs
    assert all(env and env.get("GITLAB_TOKEN") == token for env in run_envs)
    assert len(trace_calls) == 1
    assert trace_calls[0][0][-1] == "projects/unit%2Frepo/jobs/123/trace"
    assert trace_calls[0][1]["env"]["GITLAB_TOKEN"] == token
    assert token not in json.dumps(result)
    assert str(token_file) not in json.dumps(result)


def test_gitlab_job_records_malformed_nested_api_responses(
    monkeypatch,
    target_file,
):
    """Pipeline and runner detail responses must be schema-checked."""
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
                    "pipeline": {"id": 77},
                    "runner": {"id": 9},
                },
            )
        if endpoint == "projects/unit%2Frepo/pipelines/77":
            return _command_result(runtime, command, [])
        if endpoint == "runners/9":
            return _command_result(runtime, command, {"items": []})
        return runtime.CommandResult(command, 1, "", "unexpected endpoint")

    def fake_tail(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        return runtime.CommandResult(command, 0, "selected trace line\n", "")

    monkeypatch.setattr(collect, "run_command", fake_run)
    monkeypatch.setattr(collect, "run_command_tail", fake_tail)
    args = SimpleNamespace(
        job_url="https://gitlab.example.test/unit/repo/-/jobs/123",
        search="selected",
    )

    result = collect.collect_gitlab_job(_target(target_file), args)

    assert result["status"] == "PARTIAL"
    reasons = {error["source"]: error["reason"] for error in result["errors"]}
    assert reasons["pipeline"] == "invalid_response"
    assert reasons["runner"] == "invalid_response"
    row = result["records"][0]
    assert row["pipeline"] is None
    assert row["runner"] is None


def test_gitlab_job_uses_effective_gitlab_env_token_for_all_reads(
    monkeypatch,
    tmp_path,
):
    """Ambient GitLab tokens must be resolved and reused for every job read."""
    collect = import_script_module("core.collect")
    credentials = import_script_module("core.credentials")
    runtime = import_script_module("core.runtime")
    token = "env-gitlab-token-value"
    for name in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "GITLAB_TOKEN",
        "GITLAB_ACCESS_TOKEN",
        "OAUTH_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITLAB_TOKEN", token)
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    target_path = tmp_path / "target.toml"
    target_path.write_text(
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
    target = credentials.bind_sources(_target(target_path), revision="a" * 40)
    observed_envs: list[dict[str, str] | None] = []

    def require_env(kwargs: dict[str, Any]) -> None:
        env = kwargs.get("env")
        observed_envs.append(env)
        assert env and env.get("GITLAB_TOKEN") == token

    def fake_run(argv, **kwargs):
        command = tuple(str(part) for part in argv)
        require_env(kwargs)
        endpoint = command[-1]
        if endpoint == "projects/unit%2Frepo/jobs/123":
            return _command_result(
                runtime,
                command,
                {
                    "id": 123,
                    "name": "selected-job",
                    "status": "failed",
                    "pipeline": {"id": 77},
                    "runner": {"id": 9},
                },
            )
        if endpoint == "projects/unit%2Frepo/pipelines/77":
            return _command_result(runtime, command, {"id": 77, "status": "failed"})
        if endpoint == "runners/9":
            return _command_result(
                runtime, command, {"id": 9, "description": "runner-a"}
            )
        return runtime.CommandResult(command, 1, "", "unexpected endpoint")

    def fake_tail(argv, **kwargs):
        command = tuple(str(part) for part in argv)
        require_env(kwargs)
        return runtime.CommandResult(command, 0, "selected trace line\n", "")

    monkeypatch.setattr(collect, "run_command", fake_run)
    monkeypatch.setattr(collect, "run_command_tail", fake_tail)
    args = SimpleNamespace(
        job_url="https://gitlab.example.test/unit/repo/-/jobs/123",
        search="selected",
    )

    result = collect.collect_gitlab_job(target, args)

    assert result["status"] == "PASS"
    assert observed_envs
    assert token not in json.dumps(result)


def test_a_kubernetes_report_identifies_the_api_server_not_only_the_context(
    monkeypatch, target_file
):
    """A context name alone does not say which cluster was read."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    monkeypatch.setattr(
        runtime,
        "run_command",
        lambda argv, **_k: runtime.CommandResult(
            tuple(argv), 0, json.dumps({"items": []}), ""
        ),
    )

    report = collect.collect_storage(
        _target(target_file),
        SimpleNamespace(
            namespace="all", node=None, storage_class=None, phase="all", search=None
        ),
    )

    assert report["target"] == ("unit-context -> https://api.cluster.example.test:6443")


def test_access_evidence_carries_what_a_reader_needs_to_check_the_run():
    """A collector report used to keep none of this."""
    access = import_script_module("core.access")
    gate = {
        "status": "PASS",
        "profile": "base_access_check",
        "captured_at": "2026-06-01T00:00:00+00:00",
        "execution_host": "unit-host",
        "skill": {"digest": "d" * 64, "revision": {"value": None}},
        "consuming_project": {"commit": None, "source": None},
        "credential_sources": {"github": "env:GH_TOKEN"},
        "targets": {"kubernetes": {"server": "https://api.unit.test:6443"}},
        "surfaces": {
            "github": {"identity": "unit-gh"},
            "kubernetes": {"identity": "unit-admin"},
        },
    }

    evidence = access.access_evidence(gate)

    assert evidence["profile"] == "base_access_check"
    assert evidence["execution_host"] == "unit-host"
    assert evidence["identities"] == {"github": "unit-gh", "kubernetes": "unit-admin"}
    assert evidence["credential_sources"] == {"github": "env:GH_TOKEN"}
    assert evidence["targets"]["kubernetes"]["server"] == "https://api.unit.test:6443"
    assert evidence["skill"]["digest"] == "d" * 64
    assert len(evidence["receipt_sha256"]) == 64


def test_the_receipt_digest_identifies_the_gate_it_came_from():
    """Two different gates must not share a correlator."""
    access = import_script_module("core.access")
    first = access.access_evidence({"status": "PASS", "execution_host": "a"})
    again = access.access_evidence({"status": "PASS", "execution_host": "a"})
    other = access.access_evidence({"status": "PASS", "execution_host": "b"})

    assert first["receipt_sha256"] == again["receipt_sha256"]
    assert first["receipt_sha256"] != other["receipt_sha256"]
