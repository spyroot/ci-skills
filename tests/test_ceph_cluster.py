"""Deterministic tests for selected Rook Ceph cluster evidence collection."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import import_script_module


def _target(tmp_path):
    target_mod = import_script_module("core.target")
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    return target_mod.Target(
        github=target_mod.GitHubTarget(
            host="github.example.test",
            repository="unit/repo",
        ),
        gitlab=target_mod.GitLabTarget(
            url="https://gitlab.example.test",
            host="gitlab.example.test",
        ),
        kubernetes=target_mod.KubernetesTarget(
            context="unit-context",
            server="https://api.cluster.example.test:6443",
            kubeconfig=kubeconfig,
        ),
    )


def _args(**overrides: Any) -> SimpleNamespace:
    values = {
        "namespace": "rook-ceph",
        "operator": "rook-ceph-operator",
        "conf": None,
        "node": None,
        "ready": "all",
        "condition": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _command_source(command: tuple[str, ...]) -> str:
    if "get" in command and command[command.index("get") + 1] == "pods":
        return "pods"
    if "exec" in command and "-s" in command:
        return "status"
    if "exec" in command and "osd" in command and "tree" in command:
        return "osd_tree"
    if "exec" in command and "pg" in command and "dump_stuck" in command:
        return "inactive_pgs"
    raise AssertionError(f"unexpected command: {command}")


def _result(runtime, command: tuple[str, ...], payload: Any, returncode: int = 0):
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    stderr = "forbidden" if returncode else ""
    return runtime.CommandResult(command, returncode, stdout, stderr)


def _pod(
    name: str,
    *,
    role: str = "rook-ceph-osd",
    node: str = "worker-a",
    ready: str = "True",
    scheduled: str = "True",
) -> dict[str, Any]:
    return {
        "metadata": {
            "namespace": "rook-ceph",
            "name": name,
            "labels": {"app": role, "ceph-osd-id": "0"},
        },
        "spec": {"nodeName": node},
        "status": {
            "phase": "Running",
            "conditions": [
                {"type": "Ready", "status": ready},
                {"type": "PodScheduled", "status": scheduled},
            ],
        },
    }


def test_ceph_cluster_uses_exact_target_bound_oc_commands_and_shapes(
    monkeypatch,
    tmp_path,
):
    """Every Ceph query is bound to the selected kubeconfig, context, namespace."""
    ceph_cluster = import_script_module("core.ceph_cluster")
    runtime = import_script_module("core.runtime")
    target = _target(tmp_path)
    kubeconfig = str(target.kubernetes.kubeconfig)
    responses = {
        "status": {"health": {"status": "HEALTH_WARN"}},
        "osd_tree": {
            "nodes": [
                {"type": "host", "id": -1, "name": "worker-a"},
                {"type": "osd", "id": 0, "name": "osd.0", "status": "up"},
                {"type": "osd", "id": 1, "name": "osd.1", "status": "down"},
            ]
        },
        "inactive_pgs": {"pg_stats": [{"pgid": "1.2", "state": "stale+inactive"}]},
        "pods": {"items": [_pod("rook-ceph-osd-0"), _pod("rook-ceph-mon-a")]},
    }
    calls: list[tuple[str, ...]] = []
    kwargs_seen: list[dict[str, Any]] = []

    def fake_run_tail(argv, **kwargs):
        command = tuple(str(part) for part in argv)
        calls.append(command)
        kwargs_seen.append(kwargs)
        return _result(runtime, command, responses[_command_source(command)])

    monkeypatch.setattr(ceph_cluster, "run_command_tail", fake_run_tail)

    result = ceph_cluster.collect_ceph_cluster(target, _args())

    prefix = (
        "oc",
        "--kubeconfig",
        kubeconfig,
        "--context",
        "unit-context",
        "-n",
        "rook-ceph",
    )
    ceph = (
        *prefix,
        "exec",
        "deploy/rook-ceph-operator",
        "--",
        "ceph",
        "--conf=/var/lib/rook/rook-ceph/rook-ceph.config",
    )
    expected = {
        "status": (*ceph, "-s", "--format=json"),
        "osd_tree": (*ceph, "osd", "tree", "--format=json"),
        "inactive_pgs": (*ceph, "pg", "dump_stuck", "inactive", "--format=json"),
        "pods": (
            *prefix,
            "get",
            "pods",
            "-l",
            "app in (rook-ceph-osd,rook-ceph-mon)",
            "-o",
            "json",
        ),
    }

    assert set(calls) == set(expected.values())
    assert result["queries"] == {key: list(value) for key, value in expected.items()}
    assert all(item["timeout"] == 40 for item in kwargs_seen)
    assert result["kind"] == "ceph_cluster"
    assert result["target"] == "unit-context -> https://api.cluster.example.test:6443"
    assert result["status"] == "PASS"
    assert result["health"] == "HEALTH_WARN"
    assert result["osds"] == [
        {"id": 0, "name": "osd.0", "status": "up"},
        {"id": 1, "name": "osd.1", "status": "down"},
    ]
    assert result["inactive_pgs"] == [{"pgid": "1.2", "state": "stale+inactive"}]
    assert result["pod_count"] == 2
    assert [record["name"] for record in result["records"]] == [
        "rook-ceph-osd-0",
        "rook-ceph-mon-a",
    ]
    assert result["actions"] == [
        "inspect_ceph_health_detail",
        "inspect_down_osd_pods_and_nodes",
        "inspect_inactive_pg_peering",
    ]
    assert result["condition"] == "ATTENTION"


def test_ceph_cluster_filters_pods_by_node_ready_and_condition(
    monkeypatch,
    tmp_path,
):
    """Pod rows honor the node, Ready, and condition filters together."""
    ceph_cluster = import_script_module("core.ceph_cluster")
    runtime = import_script_module("core.runtime")
    target = _target(tmp_path)
    responses = {
        "status": {"health": {"status": "HEALTH_OK"}},
        "osd_tree": {"nodes": []},
        # Ceph's live dump_stuck envelope uses stuck_pg_stats even when empty.
        "inactive_pgs": {"pg_ready": True, "stuck_pg_stats": []},
        "pods": {
            "items": [
                _pod("ready-on-worker-a", node="worker-a", ready="True"),
                _pod("unready-on-worker-b", node="worker-b", ready="False"),
                _pod(
                    "selected-unready",
                    node="worker-a",
                    ready="False",
                    scheduled="False",
                ),
            ]
        },
    }

    def fake_run_tail(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        return _result(runtime, command, responses[_command_source(command)])

    monkeypatch.setattr(ceph_cluster, "run_command_tail", fake_run_tail)

    result = ceph_cluster.collect_ceph_cluster(
        target,
        _args(node="worker-a", ready="false", condition="PodScheduled=False"),
    )

    assert result["status"] == "PASS"
    assert result["pod_count"] == 3
    assert [record["name"] for record in result["records"]] == ["selected-unready"]
    assert result["records"][0]["node"] == "worker-a"
    assert result["records"][0]["ready"] is False
    assert result["records"][0]["conditions"]["PodScheduled"] == "False"
    assert result["actions"] == ["inspect_unready_ceph_pods"]


def test_ceph_cluster_parses_live_dump_stuck_envelope():
    """Ceph pg dump_stuck returns stuck_pg_stats rather than pg_stats."""
    ceph_cluster = import_script_module("core.ceph_cluster")

    assert ceph_cluster._inactive_pgs(
        {
            "pg_ready": True,
            "stuck_pg_stats": [{"pgid": "1.2", "state": "inactive"}],
        }
    ) == [{"pgid": "1.2", "state": "inactive"}]
    with pytest.raises(TypeError, match="inactive_pgs:invalid_response"):
        ceph_cluster._inactive_pgs({"pg_ready": True, "stuck_pg_stats": None})


@pytest.mark.parametrize(
    ("bad_source", "payload", "returncode", "expected"),
    (
        (
            "osd_tree",
            {"nodes": {"not": "a-list"}},
            0,
            {"source": "osd_tree", "reason": "osd_tree:invalid_response"},
        ),
        (
            "pods",
            "",
            1,
            {"source": "pods", "reason": "pods:authorization"},
        ),
    ),
)
def test_ceph_cluster_records_malformed_response_and_denied_command(
    monkeypatch,
    tmp_path,
    bad_source,
    payload,
    returncode,
    expected,
):
    """Malformed JSON shapes and denied commands become structured errors."""
    ceph_cluster = import_script_module("core.ceph_cluster")
    runtime = import_script_module("core.runtime")
    target = _target(tmp_path)
    responses = {
        "status": {"health": {"status": "HEALTH_OK"}},
        "osd_tree": {"nodes": []},
        "inactive_pgs": {"pg_stats": []},
        "pods": {"items": []},
    }
    responses[bad_source] = payload

    def fake_run_tail(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        source = _command_source(command)
        return _result(
            runtime,
            command,
            responses[source],
            returncode if source == bad_source else 0,
        )

    monkeypatch.setattr(ceph_cluster, "run_command_tail", fake_run_tail)

    result = ceph_cluster.collect_ceph_cluster(target, _args())

    assert result["status"] == "PARTIAL"
    assert expected in result["errors"]
    assert result["summary"]["error_count"] == 1
    assert result["safe_next_step"] == (
        "Review failed Ceph queries on the selected target."
    )
