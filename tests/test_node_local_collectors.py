"""Deterministic tests for node-local Cilium and Ceph collectors."""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import import_script_module


def _modules():
    return (
        import_script_module("core.node_local"),
        import_script_module("core.runtime"),
    )


def _agent_container(container_id: str = "0123456789ab") -> dict[str, Any]:
    return {
        "id": container_id,
        "metadata": {"name": "cilium-agent"},
        "labels": {
            "io.kubernetes.pod.name": "cilium-agent-abc",
            "io.kubernetes.pod.namespace": "kube-system",
        },
        "state": "CONTAINER_RUNNING",
    }


def _crictl_payload(containers: list[dict[str, Any]]) -> str:
    return json.dumps({"containers": containers})


def _journal_line(message: str, *, timestamp: str = "1760000000000000") -> str:
    return json.dumps(
        {
            "__REALTIME_TIMESTAMP": timestamp,
            "_HOSTNAME": "node-a",
            "PRIORITY": "3",
            "MESSAGE": message,
        }
    )


def test_cilium_node_collects_one_agent_and_runs_both_json_execs(monkeypatch):
    """Exactly one cilium-agent container is exec'd for status and health JSON."""
    node_local, runtime = _modules()
    calls: list[tuple[str, ...]] = []
    agent_id = "0123456789abcdef"

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        calls.append(command)
        if command[:5] == ("sudo", "-n", "crictl", "ps", "--output"):
            return runtime.CommandResult(
                command,
                0,
                _crictl_payload([_agent_container(agent_id)]),
                "",
            )
        if command[:6] == ("sudo", "-n", "crictl", "exec", "--sync", "--timeout"):
            if "cilium-dbg" in command:
                return runtime.CommandResult(
                    command,
                    0,
                    json.dumps({"cilium": {"state": "Ok", "msg": "unit"}}),
                    "",
                )
            if "cilium-health" in command:
                return runtime.CommandResult(
                    command,
                    0,
                    json.dumps({"local": {"name": "node-a"}, "nodes": []}),
                    "",
                )
        return runtime.CommandResult(command, 99, "", "unexpected command")

    monkeypatch.setattr(node_local, "run_command", fake_run, raising=False)

    result = node_local.collect_cilium_node()

    assert result["kind"] == "cilium_node"
    assert result["status"] == "PASS"
    assert result["summary"] == {"record_count": 1, "error_count": 0}
    row = result["records"][0]
    assert row["container_id"] == agent_id
    assert row["daemon"]["cilium"]["state"] == "Ok"
    assert row["health"]["local"]["name"] == "node-a"
    assert ("sudo", "-n", "crictl", "ps", "--output", "json") in calls
    assert any(
        "cilium-dbg" in command
        and command[-4:] == ("status", "--verbose", "--output", "json")
        for command in calls
    )
    assert any(
        "cilium-health" in command
        and command[-4:] == ("status", "--verbose", "--output", "json")
        for command in calls
    )


@pytest.mark.parametrize(
    ("stdout", "expected_reason"),
    (
        (_crictl_payload([]), "agent_not_running"),
        (
            _crictl_payload(
                [_agent_container("0123456789ab"), _agent_container("abcdefabcdef")]
            ),
            "multiple_running_agents",
        ),
        (json.dumps({"items": []}), "crictl:invalid_container_response"),
        ("not-json", "crictl_ps:invalid_json"),
    ),
)
def test_cilium_node_blocks_on_agent_discovery_errors(
    monkeypatch,
    stdout,
    expected_reason,
):
    """Missing, ambiguous, or malformed crictl evidence blocks before exec."""
    node_local, runtime = _modules()
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        calls.append(command)
        return runtime.CommandResult(command, 0, stdout, "")

    monkeypatch.setattr(node_local, "run_command", fake_run, raising=False)

    result = node_local.collect_cilium_node()

    assert result["kind"] == "cilium_node"
    assert result["status"] == "BLOCKED"
    assert result["records"] == []
    assert any(error["reason"] == expected_reason for error in result["errors"])
    assert not any("exec" in command for command in calls)


def test_cilium_node_reports_structured_exec_failure(monkeypatch):
    """A failed Cilium JSON exec becomes a structured machine-readable error."""
    node_local, runtime = _modules()
    agent_id = "0123456789abcdef"

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        if command[:5] == ("sudo", "-n", "crictl", "ps", "--output"):
            return runtime.CommandResult(
                command,
                0,
                _crictl_payload([_agent_container(agent_id)]),
                "",
            )
        if "cilium-dbg" in command:
            return runtime.CommandResult(command, 1, "", "permission denied")
        if "cilium-health" in command:
            return runtime.CommandResult(
                command,
                0,
                json.dumps({"local": {"name": "node-a"}, "nodes": []}),
                "",
            )
        return runtime.CommandResult(command, 99, "", "unexpected command")

    monkeypatch.setattr(node_local, "run_command", fake_run, raising=False)

    result = node_local.collect_cilium_node()

    assert result["status"] == "PARTIAL"
    assert result["records"][0]["status"] == "UNKNOWN"
    assert result["errors"] == [{"source": "daemon", "reason": "daemon:authorization"}]
    assert result["summary"] == {"record_count": 1, "error_count": 1}


@pytest.mark.parametrize(
    ("daemon", "health", "bad_source"),
    (
        ({"junk": True}, {"local": {"name": "node-a"}}, "daemon"),
        ({"cilium": {"state": "Ok"}}, {"local": {}}, "health"),
    ),
)
def test_cilium_node_rejects_meaningless_json(monkeypatch, daemon, health, bad_source):
    node_local, runtime = _modules()

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        if "ps" in command:
            value = {"containers": [_agent_container()]}
        else:
            value = daemon if "cilium-dbg" in command else health
        return runtime.CommandResult(command, 0, json.dumps(value), "")

    monkeypatch.setattr(node_local, "run_command", fake_run)
    result = node_local.collect_cilium_node()
    assert result["status"] == "PARTIAL"
    assert result["errors"] == [
        {"source": bad_source, "reason": f"{bad_source}:invalid_response"}
    ]


def test_cilium_node_no_agent_lists_all_cilium_containers(monkeypatch):
    node_local, runtime = _modules()
    calls = []

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        calls.append(command)
        containers = (
            [
                _agent_container(),
                {
                    "id": "abcdefabcdef",
                    "metadata": {"name": "cilium-config"},
                    "state": "EXITED",
                },
                {"id": "eeeeeeeeeeee", "metadata": {"name": "unrelated"}},
            ]
            if "-a" in command
            else []
        )
        return runtime.CommandResult(command, 0, _crictl_payload(containers), "")

    monkeypatch.setattr(node_local, "run_command", fake_run)
    result = node_local.collect_cilium_node()
    assert result["status"] == "BLOCKED"
    assert [item["name"] for item in result["cilium_containers"]] == [
        "cilium-agent",
        "cilium-config",
    ]
    assert (
        "sudo",
        "-n",
        "crictl",
        "ps",
        "-a",
        "--name",
        "cilium",
        "--output",
        "json",
    ) in calls


def test_ceph_kernel_reads_three_minute_journal_window_and_filters_ceph(
    monkeypatch,
):
    """Journal JSON lines are filtered to Ceph evidence inside the requested window."""
    node_local, runtime = _modules()
    calls: list[tuple[str, ...]] = []
    stdout = "\n".join(
        (
            _journal_line("eth0: link up"),
            _journal_line("libceph: mon0 socket closed"),
            _journal_line("rbd0: blk_update_request: I/O error"),
        )
    )

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        calls.append(command)
        return runtime.CommandResult(command, 0, stdout, "")

    monkeypatch.setattr(node_local, "run_command_tail", fake_run, raising=False)

    result = node_local.collect_ceph_kernel()

    assert result["kind"] == "ceph_kernel"
    assert result["status"] == "PASS"
    assert result["summary"] == {"record_count": 2, "error_count": 0}
    assert [row["message"] for row in result["records"]] == [
        "libceph: mon0 socket closed",
        "rbd0: blk_update_request: I/O error",
    ]
    command = calls[0]
    assert command[:4] == ("sudo", "-n", "journalctl", "-k")
    assert "--output=json" in command
    assert "--grep=libceph|rbd|ceph" in command
    assert any(part.startswith("--since") for part in command)
    assert "3" in " ".join(command)
    assert "--lines=200" in command


def test_ceph_kernel_marks_bounded_result_partial(monkeypatch):
    node_local, runtime = _modules()
    stdout = "\n".join(_journal_line(f"ceph: event {number}") for number in range(200))

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        return runtime.CommandResult(command, 0, stdout, "")

    monkeypatch.setattr(node_local, "run_command_tail", fake_run)
    result = node_local.collect_ceph_kernel()
    assert result["status"] == "PARTIAL"
    assert result["truncated"] is True
    assert result["limits"] == {"lines": 200, "bytes": 65536}


def test_ceph_kernel_empty_grep_is_a_complete_empty_read(monkeypatch):
    node_local, runtime = _modules()

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        return runtime.CommandResult(command, 1, "-- No entries --", "")

    monkeypatch.setattr(node_local, "run_command_tail", fake_run)
    result = node_local.collect_ceph_kernel()
    assert result["status"] == "PASS"
    assert result["records"] == []
    assert result["truncated"] is False


def test_ceph_kernel_classifies_errors_and_recommends_actions(monkeypatch):
    """Ceph-related kernel lines carry stable category and action fields."""
    node_local, runtime = _modules()
    stdout = "\n".join(
        (
            _journal_line("libceph: mon0 connection refused"),
            _journal_line("rbd0: blk_update_request: I/O error"),
            _journal_line("ceph: auth ticket denied"),
        )
    )

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        return runtime.CommandResult(command, 0, stdout, "")

    monkeypatch.setattr(node_local, "run_command_tail", fake_run, raising=False)

    result = node_local.collect_ceph_kernel()

    assert [(row["classification"], row["action"]) for row in result["records"]] == [
        ("connectivity", "inspect_ceph_monitor_network"),
        ("io_error", "inspect_rbd_and_ceph_health"),
        ("authentication", "inspect_ceph_client_auth"),
    ]
    assert result["actions"] == [
        "inspect_ceph_client_auth",
        "inspect_ceph_monitor_network",
        "inspect_rbd_and_ceph_health",
    ]


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "expected_reason", "expected_status"),
    (
        (1, "", "permission denied", "authorization", "BLOCKED"),
        (0, "{not-json}\n", "", "invalid_journal_event:JSONDecodeError", "PARTIAL"),
    ),
)
def test_ceph_kernel_blocks_on_denied_or_malformed_journal_response(
    monkeypatch,
    returncode,
    stdout,
    stderr,
    expected_reason,
    expected_status,
):
    """Denied or malformed journal evidence must not become an empty PASS."""
    node_local, runtime = _modules()

    def fake_run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        return runtime.CommandResult(command, returncode, stdout, stderr)

    monkeypatch.setattr(node_local, "run_command_tail", fake_run, raising=False)

    result = node_local.collect_ceph_kernel()

    assert result["kind"] == "ceph_kernel"
    assert result["status"] == expected_status
    assert result["records"] == []
    assert result["errors"] == [{"source": "journalctl", "reason": expected_reason}]


@pytest.mark.parametrize(
    ("message", "priority", "expected"),
    (
        (
            "client.1 blocklisted by osd",
            4,
            ("client_blocklisted", "inspect_ceph_client_blocklist"),
        ),
        (
            "ceph: harmless periodic map update",
            6,
            ("observation", None),
        ),
        (
            "ceph: unexpected critical event",
            3,
            ("unclassified_error", "review_ceph_kernel_event"),
        ),
    ),
)
def test_ceph_kernel_classification_fallbacks(message, priority, expected):
    """Classifier fallbacks separate action-worthy errors from observations."""
    node_local, _runtime = _modules()

    assert node_local.classify_ceph_message(message, priority) == expected
