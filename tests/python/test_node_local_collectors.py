"""Focused parser tests for bounded reads from a selected existing Pod."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from tests.python.conftest import import_script_module

NODE = import_script_module("core.node_local")
RUNTIME = import_script_module("core.runtime")
REPORT = import_script_module("core.report")


@dataclass
class FakeReader:
    daemon: str = '{"cilium":{"state":"Ok"}}'
    health: str = '{"local":{"name":"node-a"},"nodes":[]}'
    journal: str = ""
    daemon_code: int = 0
    journal_code: int = 0
    journal_error: str = ""
    access_code: int = 0
    calls: list[tuple[str, ...]] = field(default_factory=list)
    name: str = "cilium-agent-one"
    uid: str = "pod-uid-one"

    def evidence(self):
        return {"node": "node-a", "pod": self.name, "pod_uid": self.uid}

    def command(self, *args):
        return ["kubectl", "exec", self.name, "--", *args]

    def run(self, *args, **_kwargs):
        self.calls.append(args)
        value = self.daemon if args[0] == "cilium-dbg" else self.health
        code = self.daemon_code if args[0] == "cilium-dbg" else 0
        stderr = "permission denied" if code else ""
        return RUNTIME.CommandResult(tuple(self.command(*args)), code, value, stderr)

    def run_tail(self, *args, **_kwargs):
        self.calls.append(args)
        if "--list-boots" in args:
            return RUNTIME.CommandResult(
                tuple(self.command(*args)), self.access_code, "-1 boot", ""
            )
        return RUNTIME.CommandResult(
            tuple(self.command(*args)),
            self.journal_code,
            self.journal,
            self.journal_error,
        )


def _line(message: str) -> str:
    return json.dumps(
        {
            "__REALTIME_TIMESTAMP": "1760000000000000",
            "PRIORITY": "3",
            "MESSAGE": message,
        }
    )


def test_cilium_node_executes_both_commands_and_summarizes_bounded_evidence():
    reader = FakeReader()
    result = NODE.collect_cilium_node(reader)

    assert result["status"] == "PASS"
    assert result["summary"] == {"record_count": 1, "error_count": 0}
    assert {call[0] for call in reader.calls} == {"cilium-dbg", "cilium-health"}
    assert result["records"][0]["pod_uid"] == "pod-uid-one"
    assert result["records"][0]["daemon"] == {"components": {"cilium": "Ok"}}
    assert result["records"][0]["health"] == {
        "local_node": "node-a",
        "peer_count": 0,
    }
    assert "nodes" not in result["records"][0]["health"]


def test_cilium_unhealthy_state_is_a_diagnostic_result_not_failed_collection():
    reader = FakeReader(
        daemon=json.dumps({"cilium": {"state": "Failure", "msg": "not ready"}}),
        health=json.dumps(
            {
                "local": {"name": "node-a"},
                "nodes": [
                    {"name": "node-b", "endpoint": {"icmp": {"status": "timeout"}}}
                ],
            }
        ),
    )
    result = NODE.collect_cilium_node(reader)

    assert result["status"] == "PASS"
    assert result["records"][0]["status"] == "PASS"
    assert result["errors"] == []
    assert [finding["component"] for finding in result["records"][0]["findings"]] == [
        "cilium",
        "endpoint",
    ]
    assert "findings:" in REPORT.human(result)
    assert "inspect_cilium_peer_connectivity" in REPORT.human(result)


@pytest.mark.parametrize("daemon", ["not-json", "{}", "null"])
def test_cilium_invalid_status_never_becomes_empty_pass(daemon):
    result = NODE.collect_cilium_node(FakeReader(daemon=daemon))
    assert result["status"] == "PARTIAL"
    assert result["records"][0]["status"] == "UNKNOWN"
    assert result["errors"][0]["source"] == "daemon"


def test_cilium_exec_denial_is_structured():
    result = NODE.collect_cilium_node(FakeReader(daemon_code=1))
    assert result["status"] == "PARTIAL"
    assert result["errors"] == [{"source": "daemon", "reason": "daemon:authorization"}]


def test_ceph_kernel_uses_declared_journal_directory_and_classifies_messages():
    reader = FakeReader(
        journal="\n".join(
            [
                _line("eth0: link up"),
                _line("libceph: mon0 connection refused"),
                _line("rbd0: blk_update_request: I/O error"),
            ]
        )
    )
    result = NODE.collect_ceph_kernel(reader, "/host-journal")

    assert result["status"] == "PASS"
    assert [row["classification"] for row in result["records"]] == [
        "connectivity",
        "io_error",
    ]
    assert "--directory=/host-journal" in reader.calls[1]
    assert "--grep=libceph|rbd|ceph" in reader.calls[1]
    assert result["actions"] == [
        "inspect_ceph_monitor_network",
        "inspect_rbd_and_ceph_health",
    ]


@pytest.mark.parametrize(
    ("code", "journal", "status"),
    [(1, "-- No entries --", "PASS"), (0, "{broken-json}", "PARTIAL")],
)
def test_ceph_empty_or_invalid_journal_is_classified(code, journal, status):
    result = NODE.collect_ceph_kernel(
        FakeReader(journal_code=code, journal=journal), "/host-journal"
    )
    assert result["status"] == status
    assert result["records"] == []


def test_ceph_kubectl_empty_match_wrapper_is_pass_after_access_read():
    result = NODE.collect_ceph_kernel(
        FakeReader(
            journal_code=1,
            journal_error="command terminated with exit code 1\n",
        ),
        "/host-journal",
    )
    assert result["status"] == "PASS"
    assert result["records"] == []


def test_ceph_unreadable_journal_blocks_before_grep():
    reader = FakeReader(access_code=1)
    result = NODE.collect_ceph_kernel(reader, "/host-journal")
    assert result["status"] == "BLOCKED"
    assert result["errors"][0]["source"] == "journal_access"
    assert len(reader.calls) == 1


def test_ceph_bounded_window_is_partial_when_limit_is_hit():
    reader = FakeReader(journal="\n".join(_line("ceph: event") for _ in range(200)))
    result = NODE.collect_ceph_kernel(reader, "/host-journal")
    assert result["status"] == "PARTIAL"
    assert result["truncated"] is True
    assert result["limits"] == {"lines": 200, "bytes": 65536}


@pytest.mark.parametrize(
    ("message", "priority", "expected"),
    [
        (
            "client blocklisted by osd",
            4,
            ("client_blocklisted", "inspect_ceph_client_blocklist"),
        ),
        ("ceph harmless update", 6, ("observation", None)),
        ("ceph critical event", 3, ("unclassified_error", "review_ceph_kernel_event")),
    ],
)
def test_ceph_classification_fallbacks(message, priority, expected):
    assert NODE.classify_ceph_message(message, priority) == expected
