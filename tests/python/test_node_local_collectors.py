"""Focused parser tests for bounded reads from a selected existing Pod.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import MutableSequence, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject
    from lib.python.core.runtime import CommandResult

import pytest
from conftest import import_script_module

NODE = import_script_module("lib.python.core.kubernetes.node_local")
RUNTIME = import_script_module("lib.python.core.runtime")
REPORT = import_script_module("lib.python.core.report")


@dataclass
class FakeReader:
    """Supply selected Pod evidence and bounded command results to collectors.

    :param daemon: JSON text returned by ``cilium-dbg status``.
    :param health: JSON text returned by ``cilium-health status``.
    :param journal: Journal text returned by bounded tail reads.
    :param daemon_code: Exit code returned by daemon status reads.
    :param journal_code: Exit code returned by journal tail reads.
    :param journal_error: Stderr returned by journal tail reads.
    :param access_code: Exit code returned by the boot-list access probe.
    :param calls: Captured command argument tuples in call order.
    :param name: Selected Pod name included in command wrappers.
    :param uid: Selected Pod UID included in evidence.

    .. attribute :: daemon
        :type: str

        JSON text returned by cilium-dbg status.

    .. attribute :: health
        :type: str

        JSON text returned by cilium-health status.

    .. attribute :: journal
        :type: str

        Journal text returned by bounded tail reads.

    .. attribute :: daemon_code
        :type: int

        Exit code returned by daemon status reads.

    .. attribute :: journal_code
        :type: int

        Exit code returned by journal tail reads.

    .. attribute :: journal_error
        :type: str

        Stderr returned by journal tail reads.

    .. attribute :: access_code
        :type: int

        Exit code returned by the boot-list access probe.

    .. attribute :: calls
        :type: MutableSequence[tuple[str, ...]]

        Captured command argument tuples in call order.

    .. attribute :: name
        :type: str

        Selected Pod name included in command wrappers.

    .. attribute :: uid
        :type: str

        Selected Pod UID included in evidence.
    """

    daemon: str = '{"cilium":{"state":"Ok"}}'
    health: str = '{"local":{"name":"node-a"},"nodes":[]}'
    journal: str = ""
    daemon_code: int = 0
    journal_code: int = 0
    journal_error: str = ""
    access_code: int = 0
    calls: MutableSequence[tuple[str, ...]] = field(default_factory=list)
    name: str = "cilium-agent-one"
    uid: str = "pod-uid-one"

    def evidence(self) -> JSONObject:
        """Expose the selected existing Pod and node identity.

        :returns: Node, Pod name, and Pod UID for collector provenance.
        """
        return {"node": "node-a", "pod": self.name, "pod_uid": self.uid}

    def command(self, *args: str) -> Sequence[str]:
        """Wrap a node-local tool in a selected Pod exec command.

        :param *args: Positional arguments forwarded to the helper under test.
        :returns: kubectl exec argv for this fake reader.

        """
        return ["kubectl", "exec", self.name, "--", *args]

    def run(
        self,
        *args: str,
        timeout: int = 35,
    ) -> CommandResult:
        """Return daemon or health response for a selected Pod command.

        :param *args: Positional arguments forwarded to the helper under test.
        :param timeout: Accepted native read deadline; no process runs in this fake.
        :returns: Native command result with selected fixture text/status.

        """
        self.calls.append(args)
        value = self.daemon if args[0] == "cilium-dbg" else self.health
        code = self.daemon_code if args[0] == "cilium-dbg" else 0
        stderr = "permission denied" if code else ""
        return RUNTIME.CommandResult(tuple(self.command(*args)), code, value, stderr)

    def run_tail(
        self,
        *args: str,
        timeout: int,
        max_bytes: int,
        max_lines: int,
    ) -> CommandResult:
        """Return boot-list or bounded journal response for the Pod.

        :param *args: Positional arguments forwarded to the helper under test.
        :param timeout: Accepted native read deadline; no process runs in this fake.
        :param max_bytes: Requested output byte limit; fixture content is already bounded.
        :param max_lines: Requested output line limit; fixture content is already bounded.
        :returns: Native command result with boot or journal text.

        """
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
    """Render one timestamped priority-three journal JSON line.

    :param message: Kernel log message classified by Ceph collector.
    :returns: JSON text for a single journal entry.
    """
    return json.dumps(
        {
            "__REALTIME_TIMESTAMP": "1760000000000000",
            "PRIORITY": "3",
            "MESSAGE": message,
        }
    )


def test_cilium_node_executes_both_commands_and_summarizes_bounded_evidence() -> None:
    """Read daemon and health from one selected Pod and keep compact evidence.
    :returns: None; assertions in the body validate the regression.
    """
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


def test_cilium_unhealthy_state_is_a_diagnostic_result_not_failed_collection() -> None:
    """Keep reachable Cilium failure findings in a passing collection.
    :returns: None; assertions in the body validate the regression.
    """
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
def test_cilium_invalid_status_never_becomes_empty_pass(daemon: str) -> None:
    """Keep malformed daemon output partial rather than an empty PASS.

    :param daemon: Invalid JSON or structurally empty daemon response.

    :returns: None; assertions in the body validate the regression.
    """
    result = NODE.collect_cilium_node(FakeReader(daemon=daemon))
    assert result["status"] == "PARTIAL"
    assert result["records"][0]["status"] == "UNKNOWN"
    assert result["errors"][0]["source"] == "daemon"


def test_cilium_exec_denial_is_structured() -> None:
    """Classify denied Cilium Pod exec as partial authorization evidence.
    :returns: None; assertions in the body validate the regression.
    """
    result = NODE.collect_cilium_node(FakeReader(daemon_code=1))
    assert result["status"] == "PARTIAL"
    assert result["errors"] == [{"source": "daemon", "reason": "daemon:authorization"}]


def test_ceph_kernel_uses_declared_journal_directory_and_classifies_messages() -> None:
    """Read the selected journal directory and classify Ceph connectivity/I/O.
    :returns: None; assertions in the body validate the regression.
    """
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
def test_ceph_empty_or_invalid_journal_is_classified(
    code: int, journal: str, status: str
) -> None:
    """Distinguish no matching journal entries from malformed JSON.

    :param code: Native journalctl exit status for this case.
    :param journal: Empty-match text or malformed entry text.
    :param status: Expected PASS or PARTIAL collector status.

    :returns: None; assertions in the body validate the regression.
    """
    result = NODE.collect_ceph_kernel(
        FakeReader(journal_code=code, journal=journal), "/host-journal"
    )
    assert result["status"] == status
    assert result["records"] == []


def test_ceph_kubectl_empty_match_wrapper_is_pass_after_access_read() -> None:
    """Treat kubectl's empty journal match wrapper as a proven empty read.
    :returns: None; assertions in the body validate the regression.
    """
    result = NODE.collect_ceph_kernel(
        FakeReader(
            journal_code=1,
            journal_error="command terminated with exit code 1\n",
        ),
        "/host-journal",
    )
    assert result["status"] == "PASS"
    assert result["records"] == []


def test_ceph_unreadable_journal_blocks_before_grep() -> None:
    """Block an unreadable journal directory before running the grep probe.
    :returns: None; assertions in the body validate the regression.
    """
    reader = FakeReader(access_code=1)
    result = NODE.collect_ceph_kernel(reader, "/host-journal")
    assert result["status"] == "BLOCKED"
    assert result["errors"][0]["source"] == "journal_access"
    assert len(reader.calls) == 1


def test_ceph_bounded_window_is_partial_when_limit_is_hit() -> None:
    """Mark a 200-line Ceph journal window partial when its cap is reached.
    :returns: None; assertions in the body validate the regression.
    """
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
def test_ceph_classification_fallbacks(
    message: str, priority: int, expected: tuple[str, Optional[str]]
) -> None:
    """Classify blocklist, harmless, and unknown critical Ceph messages.

    :param message: Kernel message passed to the classifier.
    :param priority: Journal severity for fallback classification.
    :param expected: Expected finding code and suggested action pair.

    :returns: None; assertions in the body validate the regression.
    """
    assert NODE.classify_ceph_message(message, priority) == expected
