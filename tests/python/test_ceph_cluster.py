"""Deterministic tests for selected Rook Ceph cluster evidence collection.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Union

import pytest
from conftest import authenticated_kubernetes_service, import_script_module

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONValue
    from lib.python.core.runtime import CommandResult
    from lib.python.core.target import Target


def _target(tmp_path: Path) -> Target:
    """Build a full fixture target with a selected kubeconfig path.

    :param tmp_path: Directory receiving the synthetic kubeconfig.
    :returns: Three-surface target pinned to the fixture cluster.
    """
    target_mod = import_script_module("lib.python.core.target")
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


def _args(**overrides: JSONValue) -> SimpleNamespace:
    """Build Ceph collector options with selected filter overrides.

    :param **overrides: Field overrides applied to the default test namespace.
    :returns: Namespace-shaped arguments passed to the collector.

    """
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
    """Classify a selected Ceph native command for response dispatch.

    :param command: Exact oc command issued by the collector.
    :returns: Status, OSD tree, inactive PG, or Pod fixture key.
    :raises AssertionError: Command is outside the expected four probes.
    """
    if "get" in command and command[command.index("get") + 1] == "pods":
        return "pods"
    if "exec" in command and "-s" in command:
        return "status"
    if "exec" in command and "osd" in command and "tree" in command:
        return "osd_tree"
    if "exec" in command and "pg" in command and "dump_stuck" in command:
        return "inactive_pgs"
    raise AssertionError(f"unexpected command: {command}")


def _result(
    runtime: ModuleType,
    command: tuple[str, ...],
    payload: JSONValue,
    returncode: int = 0,
) -> CommandResult:
    """Build a native Ceph command result from text or JSON payload.

    :param runtime: Module providing the command-result class.
    :param command: Native argv recorded in the result.
    :param payload: Raw text or JSON-serializable provider response.
    :param returncode: Simulated process status, including denied cases.
    :returns: Command result with response body and safe failure text.
    """
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
) -> MutableMapping[str, JSONValue]:
    """Build a Rook Pod with selectable node and condition states.

    :param name: Pod name exposed in collector records.
    :param role: Rook app label used by Pod filtering.
    :param node: Kubernetes node hosting the Pod.
    :param ready: Ready condition status string.
    :param scheduled: PodScheduled condition status string.
    :returns: Minimal Kubernetes Pod mapping.
    """
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


def test_ceph_health_checks_preserve_exact_findings_and_reject_malformed_detail() -> (
    None
):
    """Keep exact health-check findings and reject wrong detail envelopes.
    :returns: None; assertions in the body validate the regression.
    """
    ceph_cluster = import_script_module("lib.python.core.kubernetes.ceph_cluster")
    health = {
        "status": "HEALTH_WARN",
        "checks": {
            "OSD_DOWN": {
                "severity": "HEALTH_WARN",
                "summary": {"message": "one OSD is down"},
                "detail": [{"message": "osd.1 is down"}],
            }
        },
    }

    status, findings = ceph_cluster._health(health)
    assert status == "HEALTH_WARN"
    assert findings == [
        {
            "code": "OSD_DOWN",
            "severity": "HEALTH_WARN",
            "summary": "one OSD is down",
            "detail": ["osd.1 is down"],
        }
    ]

    health["checks"]["OSD_DOWN"]["detail"] = {"message": "invalid"}
    with pytest.raises(TypeError, match="status:invalid_health_detail"):
        ceph_cluster._health(health)


def test_ceph_cluster_uses_exact_target_bound_oc_commands_and_shapes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pin four Ceph probes to target context/file and preserve tree evidence.

    :param monkeypatch: Pytest fixture replacing bounded native commands.
    :param tmp_path: Directory receiving the target kubeconfig.

    :returns: None; assertions in the body validate the regression.
    """
    ceph_cluster = import_script_module("lib.python.core.kubernetes.ceph_cluster")
    runtime = import_script_module("lib.python.core.runtime")
    service = authenticated_kubernetes_service(_target(tmp_path))
    target = service.target
    kubeconfig = str(target.kubernetes.kubeconfig)
    responses = {
        "status": {"health": {"status": "HEALTH_WARN"}},
        "osd_tree": {
            "nodes": [
                {"type": "root", "id": -1, "name": "default", "children": [-2]},
                {"type": "rack", "id": -2, "name": "rack-a", "children": [-3]},
                {"type": "host", "id": -3, "name": "worker-a", "children": [0, 1]},
                {"type": "osd", "id": 0, "name": "osd.0", "status": "up"},
                {"type": "osd", "id": 1, "name": "osd.1", "status": "down"},
            ]
        },
        "inactive_pgs": {"pg_stats": [{"pgid": "1.2", "state": "stale+inactive"}]},
        "pods": {"items": [_pod("rook-ceph-osd-0"), _pod("rook-ceph-mon-a")]},
    }
    calls: MutableSequence[tuple[str, ...]] = []
    kwargs_seen: MutableSequence[Mapping[str, JSONValue]] = []

    def fake_run_tail(
        argv: Sequence[str],
        **kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Record bounded oc argv and return the matching Ceph fixture.

        :param argv: Selected oc command issued by the collector.
        :param **kwargs: Keyword arguments forwarded to the fake command boundary.
        :returns: Simulated native response for the command class.

        """
        command = tuple(str(part) for part in argv)
        calls.append(command)
        kwargs_seen.append(kwargs)
        return _result(runtime, command, responses[_command_source(command)])

    monkeypatch.setattr(ceph_cluster, "run_command_tail", fake_run_tail)

    result = ceph_cluster.collect_ceph_cluster(target, _args(), state=service.state)

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
    assert result["osd_tree"] == [
        {
            "id": -1,
            "name": "default",
            "type": "root",
            "children": [
                {
                    "id": -2,
                    "name": "rack-a",
                    "type": "rack",
                    "children": [
                        {
                            "id": -3,
                            "name": "worker-a",
                            "type": "host",
                            "children": [
                                {
                                    "id": 0,
                                    "name": "osd.0",
                                    "type": "osd",
                                    "status": "up",
                                    "children": [],
                                },
                                {
                                    "id": 1,
                                    "name": "osd.1",
                                    "type": "osd",
                                    "status": "down",
                                    "children": [],
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    rendered = import_script_module("lib.python.core.report").human(result)
    assert rendered.index("default (root") < rendered.index("rack-a (rack")
    assert rendered.index("rack-a (rack") < rendered.index("worker-a (host")
    assert rendered.index("worker-a (host") < rendered.index("osd.1 (osd")
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


def test_ceph_cluster_discovers_default_operator_by_name_without_metadata_label(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Discover the default Rook Deployment by its exact namespace and name.

    :param monkeypatch: Pytest fixture replacing the bounded deployment read.
    :param tmp_path: Directory receiving the selected kubeconfig.
    :returns: None; assertions prove omitted operator discovery is label-independent.
    """
    ceph_cluster = import_script_module("lib.python.core.kubernetes.ceph_cluster")
    kubernetes = import_script_module("lib.python.core.kubernetes.kubernetes")
    runtime = import_script_module("lib.python.core.runtime")
    service = authenticated_kubernetes_service(_target(tmp_path))
    target = service.target
    namespace = "openshift-storage"
    operator = "rook-ceph-operator"
    calls: MutableSequence[tuple[str, ...]] = []

    def fake_run_bounded(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return the live-shaped default operator Deployment.

        :param argv: Native discovery command issued by the Ceph reader.
        :param **_kwargs: Unused bounded-command options.
        :returns: Deployment list with no metadata ``app`` label.
        """
        command = tuple(str(part) for part in argv)
        calls.append(command)
        return _result(
            runtime,
            command,
            {
                "items": [
                    {
                        "metadata": {
                            "namespace": namespace,
                            "name": operator,
                            "labels": {"app.kubernetes.io/name": "rook-ceph"},
                        },
                        "spec": {"selector": {"matchLabels": {"app": operator}}},
                        "status": {"readyReplicas": 1},
                    }
                ]
            },
        )

    monkeypatch.setattr(kubernetes, "run_command_bounded", fake_run_bounded)

    reader = ceph_cluster.CephClusterRead(
        target,
        _args(namespace=namespace, operator=None),
        state=service.state,
    )

    assert calls == [
        (
            "kubectl",
            "--kubeconfig",
            str(target.kubernetes.kubeconfig),
            "--context",
            "unit-context",
            "-n",
            namespace,
            "get",
            "deployments",
            "--field-selector",
            f"metadata.name={operator}",
            "-o",
            "json",
        )
    ]
    status = reader.commands["status"]
    assert status[status.index("exec") + 1] == f"deploy/{operator}"
    assert f"--conf=/var/lib/rook/{namespace}/{namespace}.config" in status


def test_ceph_cluster_filters_pods_by_node_ready_and_condition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Combine node, Ready, and PodScheduled filters in Ceph rows.

    :param monkeypatch: Pytest fixture replacing bounded native commands.
    :param tmp_path: Directory receiving the target kubeconfig.

    :returns: None; assertions in the body validate the regression.
    """
    ceph_cluster = import_script_module("lib.python.core.kubernetes.ceph_cluster")
    runtime = import_script_module("lib.python.core.runtime")
    service = authenticated_kubernetes_service(_target(tmp_path))
    target = service.target
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

    def fake_run_tail(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return Ceph probe fixtures including mixed-readiness Pods.

        :param argv: Selected oc command issued by the collector.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Simulated native response for the command class.

        """
        command = tuple(str(part) for part in argv)
        return _result(runtime, command, responses[_command_source(command)])

    monkeypatch.setattr(ceph_cluster, "run_command_tail", fake_run_tail)

    result = ceph_cluster.collect_ceph_cluster(
        target,
        _args(node="worker-a", ready="false", condition="PodScheduled=False"),
        state=service.state,
    )

    assert result["status"] == "PASS"
    assert result["pod_count"] == 3
    assert [record["name"] for record in result["records"]] == ["selected-unready"]
    assert result["records"][0]["node"] == "worker-a"
    assert result["records"][0]["ready"] is False
    assert result["records"][0]["conditions"]["PodScheduled"] == "False"
    assert result["actions"] == ["inspect_unready_ceph_pods"]


def test_ceph_cluster_parses_live_dump_stuck_envelope() -> None:
    """Ceph pg dump_stuck returns stuck_pg_stats rather than pg_stats.
    :returns: None; assertions in the body validate the regression.
    """
    ceph_cluster = import_script_module("lib.python.core.kubernetes.ceph_cluster")

    assert ceph_cluster._inactive_pgs(
        {
            "pg_ready": True,
            "stuck_pg_stats": [{"pgid": "1.2", "state": "inactive"}],
        }
    ) == [{"pgid": "1.2", "state": "inactive"}]
    with pytest.raises(TypeError, match="inactive_pgs:invalid_response"):
        ceph_cluster._inactive_pgs({"pg_ready": True, "stuck_pg_stats": None})


@pytest.mark.parametrize(
    "nodes",
    (
        [{"type": "root", "id": -1, "name": "default", "children": [9]}],
        [
            {"type": "root", "id": -1, "name": "default"},
            {"type": "rack", "id": -1, "name": "rack-a"},
        ],
        [
            {"type": "root", "id": -1, "name": "default", "children": [-2]},
            {"type": "rack", "id": -2, "name": "rack-a", "children": [-1]},
        ],
    ),
)
def test_ceph_tree_rejects_missing_duplicate_or_cyclic_hierarchy(
    nodes: Sequence[str],
) -> None:
    """Reject OSD trees with missing children, duplicate IDs, or cycles.

    :param nodes: Invalid Ceph tree node sequence under test.

    :returns: None; assertions in the body validate the regression.
    """
    ceph_cluster = import_script_module("lib.python.core.kubernetes.ceph_cluster")
    with pytest.raises(TypeError, match="osd_tree:"):
        ceph_cluster._osd_tree({"nodes": nodes})


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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bad_source: str,
    payload: JSONValue,
    returncode: int,
    expected: Mapping[str, str],
) -> None:
    """Render invalid Ceph shapes or denied Pod query as partial evidence.

    :param monkeypatch: Pytest fixture replacing bounded native commands.
    :param tmp_path: Directory receiving the target kubeconfig.
    :param bad_source: Probe whose response or exit status is invalid.
    :param payload: Malformed JSON shape or denied-command output.
    :param returncode: Native status for the selected bad probe.
    :param expected: Structured source/reason pair expected in errors.

    :returns: None; assertions in the body validate the regression.
    """
    ceph_cluster = import_script_module("lib.python.core.kubernetes.ceph_cluster")
    runtime = import_script_module("lib.python.core.runtime")
    service = authenticated_kubernetes_service(_target(tmp_path))
    target = service.target
    responses = {
        "status": {"health": {"status": "HEALTH_OK"}},
        "osd_tree": {"nodes": []},
        "inactive_pgs": {"pg_stats": []},
        "pods": {"items": []},
    }
    responses[bad_source] = payload

    def fake_run_tail(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return one bad Ceph probe while preserving other responses.

        :param argv: Selected oc command issued by the collector.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Native result with selected payload and failure status.

        """
        command = tuple(str(part) for part in argv)
        source = _command_source(command)
        return _result(
            runtime,
            command,
            responses[source],
            returncode if source == bad_source else 0,
        )

    monkeypatch.setattr(ceph_cluster, "run_command_tail", fake_run_tail)

    result = ceph_cluster.collect_ceph_cluster(target, _args(), state=service.state)

    assert result["status"] == "PARTIAL"
    assert expected in result["errors"]
    assert result["summary"]["error_count"] == 1
    assert result["safe_next_step"] == (
        "Review failed Ceph queries on the selected target."
    )
