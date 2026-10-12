"""Deterministic collector tests with mocked native command output.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, MutableMapping, MutableSequence, Sequence
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Optional, Union

import pytest
from conftest import import_script_module

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue
    from lib.python.core.runtime import CommandResult
    from lib.python.core.target import Target


def _target(path: Path) -> Target:
    """Load the neutral test target.

    :param path: Target TOML file supplied by a pytest fixture or helper.
    :returns: Parsed target object used by the collector APIs under test.
    """
    return import_script_module("lib.python.core.target").load_target(path)


def _write_gitlab_token_target(tmp_path: Path, token_file: Path) -> Path:
    """Write a target file with a GitLab token-file pointer.

    :param tmp_path: Pytest scratch directory for the generated target file.
    :param token_file: Existing file path named by the target's GitLab stanza.
    :returns: Path to the generated target TOML file.
    """
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


def _command_result(
    runtime: ModuleType, argv: tuple[str, ...], payload: JSONValue, code: int = 0
) -> CommandResult:
    """Create a command result with JSON stdout unless a string is supplied.

    :param runtime: Imported ``core.runtime`` module that owns ``CommandResult``.
    :param argv: Native command vector being simulated.
    :param payload: Raw stdout string or JSON-serializable decoded payload.
    :param code: Process exit code to place in the command result.
    :returns: ``CommandResult`` instance compatible with collector helpers.
    """
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return runtime.CommandResult(argv, code, stdout, "")


def _patch_json_commands(
    monkeypatch: pytest.MonkeyPatch, runner: Callable[..., CommandResult]
) -> None:
    """Replace the shared native JSON command boundary for a test.

    :param monkeypatch: Pytest fixture applying the temporary replacement.
    :param runner: Synthetic command runner returning the fixture response.
    :returns: None; the shared collection module receives the test runner.
    """
    collection = import_script_module("lib.python.core.collection")
    monkeypatch.setattr(collection, "run_command", runner)


def _patch_cilium_commands(
    monkeypatch: pytest.MonkeyPatch,
    cilium: ModuleType,
    runner: Callable[..., CommandResult],
) -> None:
    """Replace Cilium list and direct health command boundaries for a test.

    :param monkeypatch: Pytest fixture applying the temporary replacements.
    :param cilium: Cilium collector module whose health execution is exercised.
    :param runner: Synthetic command runner serving list and health responses.
    :returns: None; shared list and Cilium health boundaries use the test runner.
    """
    _patch_json_commands(monkeypatch, runner)
    monkeypatch.setattr(cilium, "run_command", runner)


def _patch_gitlab_job_commands(
    monkeypatch: pytest.MonkeyPatch,
    job_read: ModuleType,
    runner: Callable[..., CommandResult],
    tail_runner: Callable[..., CommandResult],
) -> None:
    """Replace GitLab job JSON and trace-tail boundaries for a test.

    :param monkeypatch: Pytest fixture applying the temporary replacements.
    :param job_read: GitLab job collector whose direct trace tail is exercised.
    :param runner: Synthetic JSON command runner for job metadata reads.
    :param tail_runner: Synthetic bounded trace-tail command runner.
    :returns: None; metadata and trace reads use only the fixture runners.
    """
    _patch_json_commands(monkeypatch, runner)
    monkeypatch.setattr(job_read, "run_command_tail", tail_runner)


def _empty_storage_payloads() -> MutableMapping[str, JSONValue]:
    """Return valid empty Kubernetes list responses for every storage resource.

    :returns: Mutable resource-name mapping with empty Kubernetes List envelopes.
    """
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
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
) -> None:
    """Malformed Kubernetes list envelopes must not become an empty PASS.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.storage")
    runtime = import_script_module("lib.python.core.runtime")
    payloads = _empty_storage_payloads()
    payloads.update(
        {
            "persistentvolumeclaims": None,
            "persistentvolumes": {},
            "volumeattachments": {"items": {"name": "not-a-list"}},
        }
    )

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return malformed or valid list envelopes by requested resource.

        :param argv: Native Kubernetes command vector generated by the collector.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        return _command_result(runtime, command, payloads[resource])

    _patch_json_commands(monkeypatch, fake_run)
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


def test_storage_report_records_malformed_pv_and_pvc_fields_as_errors(
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
) -> None:
    """Malformed nested API fields produce source errors instead of exceptions.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.storage")
    runtime = import_script_module("lib.python.core.runtime")
    payloads = _empty_storage_payloads()
    payloads["persistentvolumeclaims"] = {
        "items": [
            {
                "metadata": {"namespace": "app", "name": "bad-pvc"},
                "spec": "not-a-mapping",
            }
        ]
    }
    payloads["persistentvolumes"] = {
        "items": [
            {
                "metadata": {"name": "bad-pv"},
                "status": "not-a-mapping",
            }
        ]
    }

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return PV and PVC item-shape fixtures by requested resource.

        :param argv: Native Kubernetes command vector generated by the collector.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        return _command_result(runtime, command, payloads[resource])

    _patch_json_commands(monkeypatch, fake_run)
    args = SimpleNamespace(
        namespace="all", node=None, storage_class=None, phase="all", search=None
    )

    result = collect.collect_storage(_target(target_file), args)

    assert result["status"] == "PARTIAL"
    assert result["records"] == []
    reasons = {error["source"]: error["reason"] for error in result["errors"]}
    assert reasons["pvcs"] == "invalid_item_response"
    assert reasons["pvs"] == "invalid_item_response"


@pytest.mark.parametrize(
    ("phase", "expected_volume"),
    (("Released", "pv-released"), ("Failed", "pv-failed")),
)
def test_storage_report_includes_orphaned_pvs_for_phase_filters(
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
    phase: str,
    expected_volume: str,
) -> None:
    """Standalone PVs without remaining PVCs are still first-class records.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.
    :param phase: PersistentVolume phase used to filter the report.
    :param expected_volume: Volume expected to remain after phase filtering.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.storage")
    runtime = import_script_module("lib.python.core.runtime")
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

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return orphaned PersistentVolume fixtures by requested resource.

        :param argv: Native Kubernetes command vector generated by the collector.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected resource envelope.

        """
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        return _command_result(runtime, command, payloads[resource])

    _patch_json_commands(monkeypatch, fake_run)
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
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
) -> None:
    """Storage collection returns only matching PVCs with correlated consumers.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.storage")
    runtime = import_script_module("lib.python.core.runtime")
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

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return the storage inventory fixture by requested resource.

        :param argv: Native Kubernetes command vector generated by the collector.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected resource envelope.

        """
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        return _command_result(runtime, command, {"items": inventory[resource]})

    _patch_json_commands(monkeypatch, fake_run)
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


def test_storage_controller_lookup_uses_kind_namespace_and_daemonset_counters(
    monkeypatch: pytest.MonkeyPatch, target_file: Path
) -> None:
    """Same-named controllers remain distinct and use their API replica fields.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.storage")
    runtime = import_script_module("lib.python.core.runtime")
    payloads = _empty_storage_payloads()
    payloads["persistentvolumeclaims"] = {
        "items": [
            {
                "metadata": {"namespace": "app", "name": "claim"},
                "spec": {"volumeName": "pv"},
                "status": {"phase": "Bound"},
            }
        ]
    }
    payloads["persistentvolumes"] = {
        "items": [
            {
                "metadata": {"name": "pv"},
                "spec": {},
                "status": {"phase": "Bound"},
            }
        ]
    }
    payloads["pods"] = {
        "items": [
            {
                "metadata": {
                    "namespace": "app",
                    "name": f"pod-{kind.lower()}",
                    "ownerReferences": [{"kind": kind, "name": owner}],
                },
                "spec": {
                    "volumes": [{"persistentVolumeClaim": {"claimName": "claim"}}]
                },
            }
            for kind, owner in (
                ("ReplicaSet", "shared-rs"),
                ("StatefulSet", "shared"),
                ("DaemonSet", "shared"),
            )
        ]
    }
    payloads["replicasets"] = {
        "items": [
            {
                "metadata": {
                    "namespace": "app",
                    "name": "shared-rs",
                    "ownerReferences": [{"kind": "Deployment", "name": "shared"}],
                }
            }
        ]
    }
    payloads["deployments"] = {
        "items": [
            {
                "metadata": {"namespace": "app", "name": "shared"},
                "spec": {"replicas": 3},
                "status": {"readyReplicas": 2},
            },
            {
                "metadata": {"namespace": "other", "name": "shared"},
                "spec": {"replicas": 99},
                "status": {"readyReplicas": 99},
            },
        ]
    }
    payloads["statefulsets"] = {
        "items": [
            {
                "metadata": {"namespace": "app", "name": "shared"},
                "spec": {"replicas": 2},
                "status": {"readyReplicas": 1},
            }
        ]
    }
    payloads["daemonsets"] = {
        "items": [
            {
                "metadata": {"namespace": "app", "name": "shared"},
                "status": {"desiredNumberScheduled": 4, "numberReady": 3},
            }
        ]
    }

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return controller and storage fixtures by requested resource.

        :param argv: Native Kubernetes command vector generated by the collector.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected resource envelope.

        """
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        return _command_result(runtime, command, payloads[resource])

    _patch_json_commands(monkeypatch, fake_run)
    result = collect.collect_storage(
        _target(target_file),
        SimpleNamespace(
            namespace="app", node=None, storage_class=None, phase="all", search=None
        ),
    )

    assert result["status"] == "PASS"
    assert result["records"][0]["controllers"] == [
        {"kind": "DaemonSet", "name": "shared", "desired": 4, "ready": 3},
        {"kind": "Deployment", "name": "shared", "desired": 3, "ready": 2},
        {"kind": "StatefulSet", "name": "shared", "desired": 2, "ready": 1},
    ]


def test_event_trace_filters_reason_object_search_and_sorts_by_source_time(
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
) -> None:
    """Event collection keeps the requested object window in timestamp order.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.events")
    runtime = import_script_module("lib.python.core.runtime")
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
    calls: MutableSequence[str] = []

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        calls.append(resource)
        return _command_result(runtime, command, {"items": events[resource]})

    _patch_json_commands(monkeypatch, fake_run)
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


def test_event_trace_rejects_naive_and_reversed_time_bounds(target_file: Path) -> None:
    """Time bounds must be unambiguous before Kubernetes data is read.

    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.events")
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
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
) -> None:
    """A repeated event stays visible when its series update is in the window.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.events")
    runtime = import_script_module("lib.python.core.runtime")
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

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        return _command_result(runtime, command, {"items": events[resource]})

    _patch_json_commands(monkeypatch, fake_run)
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


def test_event_trace_keeps_first_observation_in_window_after_later_repeat(
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
) -> None:
    """An Event first seen in the window remains visible after a later repeat.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.events")
    runtime = import_script_module("lib.python.core.runtime")

    def repeated(name: str, first: str) -> MutableMapping[str, JSONValue]:
        """Build a repeated Kubernetes Event fixture.

        :param name: Event name used in the generated repeated-event fixture.
        :param first: First observation timestamp used in the generated event fixture.
        :returns: Kubernetes Event fixture with a series update after first observation.
        """
        return {
            "metadata": {"namespace": "jobs", "name": name},
            "regarding": {"name": "runner-pod", "namespace": "jobs", "uid": name},
            "eventTime": first,
            "series": {"lastObservedTime": "2026-10-01T10:15:00Z", "count": 3},
            "reason": "FailedScheduling",
            "note": "repeated scheduling failure",
        }

    events = [
        repeated("inside", "2026-10-01T10:05:00Z"),
        repeated("before", "2026-10-01T09:55:00Z"),
    ]

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
        command = tuple(str(part) for part in argv)
        assert command[command.index("get") + 1] == "events.events.k8s.io"
        return _command_result(runtime, command, {"items": events})

    _patch_json_commands(monkeypatch, fake_run)
    args = SimpleNamespace(
        from_time="2026-10-01T10:00:00Z",
        to_time="2026-10-01T10:10:00Z",
        namespace="jobs",
        kind=None,
        object=None,
        reason=None,
        search=None,
    )

    result = collect.collect_events(_target(target_file), args)

    assert result["status"] == "PASS"
    assert len(result["records"]) == 1
    row = result["records"][0]
    assert row["object_uid"] == "inside"
    assert row["timestamp"] == "2026-10-01T10:05:00+00:00"
    assert row["first_timestamp"] == "2026-10-01T10:05:00+00:00"
    assert row["last_timestamp"] == "2026-10-01T10:15:00+00:00"
    assert row["count"] == 3


def test_event_trace_prefers_deprecated_count_before_core_count(
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
) -> None:
    """Event count preserves events.k8s.io deprecatedCount before generic count.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.events")
    runtime = import_script_module("lib.python.core.runtime")
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

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        assert resource == "events.events.k8s.io"
        return _command_result(runtime, command, {"items": [event]})

    _patch_json_commands(monkeypatch, fake_run)
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
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
) -> None:
    """Malformed event list items must be recorded as evidence errors.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.events")
    runtime = import_script_module("lib.python.core.runtime")
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

    calls: MutableSequence[str] = []

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        calls.append(resource)
        return _command_result(runtime, command, {"items": events[resource]})

    _patch_json_commands(monkeypatch, fake_run)
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
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
    invalid_field: JSONObject,
) -> None:
    """Bad nested Event evidence must not escape the structured report.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.
    :param invalid_field: Malformed nested Event field supplied by the parametrized case.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.events")
    runtime = import_script_module("lib.python.core.runtime")
    event = {
        "metadata": {"name": "runner-pod.abc", "namespace": "jobs"},
        "regarding": {"kind": "Pod", "name": "runner-pod", "namespace": "jobs"},
        "eventTime": "2026-10-01T10:02:00Z",
        "reason": "FailedScheduling",
        "note": "scheduling failure",
    }
    event.update(invalid_field)

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
        command = tuple(str(part) for part in argv)
        assert command[command.index("get") + 1] == "events.events.k8s.io"
        return _command_result(runtime, command, {"items": [event]})

    _patch_json_commands(monkeypatch, fake_run)
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


def test_event_trace_accepts_reference_without_optional_kind(
    monkeypatch: pytest.MonkeyPatch, target_file: Path
) -> None:
    """A live Event API reference can have a name and UID but no kind.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.events")
    runtime = import_script_module("lib.python.core.runtime")
    event = {
        "metadata": {"namespace": "jobs", "name": "runner-pod.abc"},
        "regarding": {"namespace": "jobs", "name": "runner-pod", "uid": "pod-1"},
        "eventTime": "2026-10-01T10:02:00Z",
        "reason": "Updated",
        "note": "status changed",
    }

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
        command = tuple(str(part) for part in argv)
        return _command_result(runtime, command, {"items": [event]})

    _patch_json_commands(monkeypatch, fake_run)
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

    assert result["status"] == "PASS"
    assert result["errors"] == []
    assert result["records"][0]["kind"] is None
    assert result["records"][0]["name"] == "runner-pod"


@pytest.mark.parametrize(
    ("stderr", "expected_reason"),
    (
        ("InternalError: apiserver failed while listing events", "command_failed"),
        ("Error from server (TooManyRequests): rate limited", "command_failed"),
    ),
)
def test_event_trace_does_not_fallback_on_preferred_api_server_failure(
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
    stderr: str,
    expected_reason: str,
) -> None:
    """Generic preferred API failures and throttling are not compatibility gaps.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.
    :param stderr: Provider stderr fixture used to classify the failed command.
    :param expected_reason: Collector error reason expected for the stderr fixture.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.events")
    runtime = import_script_module("lib.python.core.runtime")
    calls: MutableSequence[str] = []

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.
        :raises AssertionError: The collector attempts a fallback after a provider failure.

        """
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        calls.append(resource)
        if resource != "events.events.k8s.io":
            raise AssertionError(f"unexpected fallback read: {resource}")
        return runtime.CommandResult(command, 1, "", stderr)

    _patch_json_commands(monkeypatch, fake_run)
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
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
) -> None:
    """Exact preferred resource absence may use the compatible core API once.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.events")
    runtime = import_script_module("lib.python.core.runtime")
    calls: MutableSequence[str] = []
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

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
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

    _patch_json_commands(monkeypatch, fake_run)
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
    assert result["compatibility_fallback"] == {
        "preferred_api": "events.k8s.io/v1",
        "reason": "resource_unavailable",
    }
    assert result["records"][0]["source"] == "core_events"
    assert result["records"][0]["count"] == 4
    assert calls == ["events.events.k8s.io", "events"]


def test_event_trace_does_not_hide_preferred_api_access_denial(
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
) -> None:
    """Authentication and authorization failures block without substitution.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.events")
    runtime = import_script_module("lib.python.core.runtime")
    calls: MutableSequence[str] = []

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
        command = tuple(str(part) for part in argv)
        calls.append(command[command.index("get") + 1])
        return runtime.CommandResult(command, 1, "", "Forbidden")

    _patch_json_commands(monkeypatch, fake_run)
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
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
) -> None:
    """An unavailable preferred API and malformed fallback both remain visible.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.events")
    runtime = import_script_module("lib.python.core.runtime")
    calls: MutableSequence[str] = []

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
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
        return _command_result(runtime, command, {})

    _patch_json_commands(monkeypatch, fake_run)
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
        {"source": "events_v1", "reason": "resource_unavailable"},
        {"source": "core_events", "reason": "invalid_list_response"},
    ]
    assert calls == ["events.events.k8s.io", "events"]


def test_cilium_status_records_unknown_health_and_uses_non_tty_exec(
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
) -> None:
    """Agent health stays read-only and non-interactive, even on exec failure.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.cilium_status")
    runtime = import_script_module("lib.python.core.runtime")
    exec_calls: MutableSequence[tuple[str, ...]] = []
    resources = {
        "daemonsets": [
            {
                "metadata": {"namespace": "kube-system", "name": "cilium"},
                "spec": {"selector": {"matchLabels": {"k8s-app": "cilium"}}},
                "status": {"desiredNumberScheduled": 2, "numberReady": 1},
            },
            {
                "metadata": {"namespace": "other", "name": "cilium-operator"},
                "spec": {"selector": {"matchLabels": {"app": "operator"}}},
            },
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

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
        command = tuple(str(part) for part in argv)
        if "exec" in command:
            exec_calls.append(command)
            return runtime.CommandResult(command, 1, "", "connection refused")
        resource = command[command.index("get") + 1]
        return _command_result(runtime, command, {"items": resources[resource]})

    _patch_cilium_commands(monkeypatch, collect, fake_run)
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
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
) -> None:
    """Agent discovery follows the DaemonSet selector, not a fixed label.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.cilium_status")
    runtime = import_script_module("lib.python.core.runtime")
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

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
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

    _patch_cilium_commands(monkeypatch, collect, fake_run)
    args = SimpleNamespace(namespace="networking", node=None, search=None)

    result = collect.collect_cilium(_target(target_file), args)

    assert result["status"] == "PASS"
    assert result["summary"]["record_count"] == 1
    assert result["records"][0]["name"] == "cilium-selected"
    assert result["records"][0]["status"] == "PASS"


def test_cilium_status_exposes_failed_peer_but_proves_successful_read(
    monkeypatch: pytest.MonkeyPatch, target_file: Path
) -> None:
    """A failed endpoint probe is a finding, while its exec still proves access.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.cilium_status")
    live = import_script_module("lib.python.core.live")
    runtime = import_script_module("lib.python.core.runtime")
    resources = {
        "daemonsets": [
            {
                "metadata": {"namespace": "networking", "name": "cilium"},
                "spec": {"selector": {"matchLabels": {"app": "cilium"}}},
            }
        ],
        "pods": [
            {
                "metadata": {
                    "namespace": "networking",
                    "name": "cilium-agent-a",
                    "labels": {"app": "cilium"},
                },
                "spec": {"nodeName": "node-a"},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }
        ],
        "deployments": [],
        "ciliumnodes.cilium.io": [],
    }
    health = {
        "local": {"name": "node-a"},
        "nodes": [{"name": "node-b", "endpoint": {"http": {"status": "timeout"}}}],
    }

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
        command = tuple(str(part) for part in argv)
        if "exec" in command:
            return _command_result(runtime, command, health)
        resource = command[command.index("get") + 1]
        return _command_result(runtime, command, {"items": resources[resource]})

    _patch_cilium_commands(monkeypatch, collect, fake_run)
    args = SimpleNamespace(namespace="networking", node=None, search=None)

    result = collect.collect_cilium(_target(target_file), args)

    assert result["status"] == "PARTIAL"
    assert result["errors"] == [
        {"source": "cilium-agent-a", "reason": "component_unhealthy"}
    ]
    row = result["records"][0]
    assert row["status"] == "PASS"
    assert row["exit_status"] == 0
    assert row["health"] == health
    assert row["findings"][0]["path"] == "peer.endpoint.http.status"
    assert row["findings"][0]["message"] == "timeout"
    assert live._access_proven("cilium_status", live._evidence(result)) is True


def test_cilium_status_reports_partial_for_explicit_namespace_with_zero_pods(
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
) -> None:
    """An explicit namespace with no agent Pods is a diagnostic error.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.cilium_status")
    runtime = import_script_module("lib.python.core.runtime")
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

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
        command = tuple(str(part) for part in argv)
        resource = command[command.index("get") + 1]
        return _command_result(runtime, command, {"items": resources[resource]})

    _patch_cilium_commands(monkeypatch, collect, fake_run)
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


def test_gitlab_job_rejects_job_url_from_different_fqdn(target_file: Path) -> None:
    """A job URL from another host cannot reuse the selected target access.

    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.gitlab.job_read")
    args = SimpleNamespace(
        job_url="https://other.example.test/unit/repo/-/jobs/123",
        search=None,
    )

    with pytest.raises(ValueError, match="exact selected GitLab FQDN"):
        collect.collect_gitlab_job(_target(target_file), args)


def test_gitlab_job_collects_metadata_and_bounded_sanitized_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Job collection keeps only a bounded sanitized trace tail.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param tmp_path: Pytest scratch directory used for generated target or token files.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.gitlab.job_read")
    runtime = import_script_module("lib.python.core.runtime")
    token = "unit-token-value"
    token_file = tmp_path / ".config" / "ci-skills" / "gitlab.example.test.token"
    token_file.parent.mkdir(parents=True)
    token_file.write_text(token + "\n", encoding="utf-8")
    trace_calls = []
    run_envs: MutableSequence[Optional[Mapping[str, str]]] = []

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
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

    def fake_tail(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return trace-tail fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying a bounded trace-tail fixture.

        """
        command = tuple(str(part) for part in argv)
        assert token not in command
        trace_calls.append((command, _kwargs))
        trace = "\n".join([f"line {index}" for index in range(250)])
        return runtime.CommandResult(command, 0, trace + "\npassword=topsecret\n", "")

    _patch_gitlab_job_commands(monkeypatch, collect, fake_run, fake_tail)
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
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
) -> None:
    """Pipeline and runner detail responses must be schema-checked.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.gitlab.job_read")
    runtime = import_script_module("lib.python.core.runtime")

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying the selected fixture payload.

        """
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

    def fake_tail(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return trace-tail fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Command result carrying a bounded trace-tail fixture.

        """
        command = tuple(str(part) for part in argv)
        return runtime.CommandResult(command, 0, "selected trace line\n", "")

    _patch_gitlab_job_commands(monkeypatch, collect, fake_run, fake_tail)
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Ambient GitLab tokens must be resolved and reused for every job read.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param tmp_path: Pytest scratch directory used for generated target or token files.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.gitlab.job_read")
    credentials = import_script_module("lib.python.access.credentials")
    runtime = import_script_module("lib.python.core.runtime")
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
    observed_envs: MutableSequence[Optional[Mapping[str, str]]] = []

    def require_env(kwargs: Mapping[str, JSONValue]) -> None:
        """Assert that a command call carries the expected GitLab token.

        :param kwargs: Command options supplied by the collector and inspected by the fixture.
        :returns: None after verifying the expected GitLab token environment.
        """
        env = kwargs.get("env")
        observed_envs.append(env)
        assert env and env.get("GITLAB_TOKEN") == token

    def fake_run(
        argv: Sequence[str],
        **kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return command fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **kwargs: Keyword arguments forwarded to the fake command boundary.
        :returns: Command result carrying the selected fixture payload.

        """
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

    def fake_tail(
        argv: Sequence[str],
        **kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return trace-tail fixture data for the collector call under test.

        :param argv: Native command vector generated by the collector under test.
        :param **kwargs: Keyword arguments forwarded to the fake command boundary.
        :returns: Command result carrying a bounded trace-tail fixture.

        """
        command = tuple(str(part) for part in argv)
        require_env(kwargs)
        return runtime.CommandResult(command, 0, "selected trace line\n", "")

    _patch_gitlab_job_commands(monkeypatch, collect, fake_run, fake_tail)
    args = SimpleNamespace(
        job_url="https://gitlab.example.test/unit/repo/-/jobs/123",
        search="selected",
    )

    result = collect.collect_gitlab_job(target, args)

    assert result["status"] == "PASS"
    assert observed_envs
    assert token not in json.dumps(result)


def test_a_kubernetes_report_identifies_the_api_server_not_only_the_context(
    monkeypatch: pytest.MonkeyPatch, target_file: Path
) -> None:
    """A context name alone does not say which cluster was read.

    :param monkeypatch: Pytest fixture used to replace native command execution.
    :param target_file: Neutral target TOML fixture consumed by the target loader.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.storage")
    runtime = import_script_module("lib.python.core.runtime")
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


def test_access_evidence_carries_what_a_reader_needs_to_check_the_run() -> None:
    """A collector report used to keep none of this.
    :returns: None; assertions in the body validate the regression.
    """
    access = import_script_module("lib.python.access.shared")
    gate = {
        "status": "PASS",
        "profile": "base_access_check",
        "captured_at": "2026-06-01T00:00:00+00:00",
        "execution_host": "unit-host",
        "skill": {"digest": "d" * 64, "revision": {"value": None}},
        "consuming_project": {"commit": None, "source": None},
        "credential_sources": {"github": "env:GH_TOKEN"},
        "target_file": "/unit/home/.ci-skills/target.toml",
        "target_source": "user",
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
    # A collector report must name its own target source, like the gate does.
    assert evidence["target_file"] == "/unit/home/.ci-skills/target.toml"
    assert evidence["target_source"] == "user"


def test_the_receipt_digest_identifies_the_gate_it_came_from() -> None:
    """Two different gates must not share a correlator.
    :returns: None; assertions in the body validate the regression.
    """
    access = import_script_module("lib.python.access.shared")
    first = access.access_evidence({"status": "PASS", "execution_host": "a"})
    again = access.access_evidence({"status": "PASS", "execution_host": "a"})
    other = access.access_evidence({"status": "PASS", "execution_host": "b"})

    assert first["receipt_sha256"] == again["receipt_sha256"]
    assert first["receipt_sha256"] != other["receipt_sha256"]


@pytest.mark.parametrize(
    ("value", "expected_seconds"),
    (
        ("5m", 300),
        ("90s", 90),
        ("2h", 7200),
        ("7d", 604800),
        (" 5m ", 300),
    ),
)
def test_parse_window_reads_a_relative_duration(
    value: JSONValue, expected_seconds: int
) -> None:
    """`--last 15m` is the form an operator asks for, so it must parse exactly.

    :param value: Duration expression supplied by the parametrized case.
    :param expected_seconds: Expected parsed duration in seconds.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.time_window")

    assert collect.parse_window(value).total_seconds() == expected_seconds


@pytest.mark.parametrize(
    "value", ("1M", "2H", "0m", "-5m", "5", "m", "", "5 m", "5min")
)
def test_parse_window_refuses_anything_it_cannot_mean_exactly(value: JSONValue) -> None:
    """`1M` most likely means a month; reading it as a minute would be silent.

        The refusals matter more than the accepts here: a misread window returns
        evidence from the wrong interval, which correlates against the wrong job.

    :param value: Duration expression supplied by the parametrized case.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.time_window")

    with pytest.raises(ValueError, match="--last"):
        collect.parse_window(value)


@pytest.mark.parametrize(
    ("last", "from_time", "to_time"),
    (
        ("5m", "2026-06-01T00:00:00+00:00", None),
        ("5m", None, "2026-06-01T01:00:00+00:00"),
        ("", "2026-06-01T00:00:00+00:00", None),
    ),
)
def test_event_trace_refuses_a_relative_window_combined_with_bounds(
    target_file: Path, last: str, from_time: Optional[str], to_time: Optional[str]
) -> None:
    """Two ways of naming one interval have no defined precedence.

        The empty row is deliberate: `--last ""` was supplied, so it is an input
        error. Treating it as absent silently returned the default hour.

    :param target_file: Neutral target TOML fixture consumed by the target loader.
    :param last: Relative event window supplied by the parametrized case.
    :param from_time: Absolute lower time bound supplied by the parametrized case.
    :param to_time: Absolute upper time bound supplied by the parametrized case.

    :returns: None; assertions in the body validate the regression.
    """
    collect = import_script_module("lib.python.core.kubernetes.events")
    args = SimpleNamespace(
        last=last,
        from_time=from_time,
        to_time=to_time,
        namespace="all",
        kind=None,
        object=None,
        reason=None,
        search=None,
    )

    with pytest.raises(ValueError, match="--last"):
        collect.collect_events(_target(target_file), args)
