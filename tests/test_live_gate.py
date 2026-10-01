"""Tests for the live-check half of the access receipt.

Two rules are pinned here. A failure to READ blocks the gate. A successful read
that reports an unhealthy component does not, because this skill exists to be
run on a degraded cluster — but the capability it proves must still have been
demonstrated at least once.
"""

from __future__ import annotations

import pytest
from conftest import import_script_module


def _live():
    return import_script_module("core.live"), import_script_module("core.status")


def _cilium(status, errors, passed, total=3, records=3):
    return {
        "status": status,
        "record_count": records,
        "error_count": len(errors),
        "errors": errors,
        "readback_sha256": "0" * 64,
        "agent_health": {"passed": passed, "total": total},
        "ciliumnode_count": total,
    }


def test_a_read_failure_blocks_the_gate():
    """Authentication, authorization, transport and timeout are access failures."""
    live, status = _live()

    for reason in (
        "authentication",
        "authorization",
        "transport",
        "timeout",
        "invalid_json",
        "missing_tool",
    ):
        evidence = {
            "status": status.PARTIAL,
            "record_count": 0,
            "error_count": 1,
            "errors": [{"source": "core_events", "reason": reason}],
        }
        assert live._access_proven("event_trace", evidence) is False, reason


def test_an_unhealthy_component_does_not_block_a_proven_read():
    """A down agent is cluster state, not a denial, once health was read once."""
    live, status = _live()
    evidence = _cilium(
        status.PARTIAL,
        [
            {"source": "cilium-a", "reason": "agent_not_ready"},
            {"source": "cilium-b", "reason": "agent_not_ready"},
        ],
        passed=1,
    )

    assert live._access_proven("cilium_status", evidence) is True


def test_an_unhealthy_component_blocks_when_nothing_proved_the_capability():
    """If no agent answered health, the exec capability is unproven."""
    live, status = _live()
    evidence = _cilium(
        status.PARTIAL,
        [{"source": "cilium-a", "reason": "agent_not_ready"}],
        passed=0,
    )

    assert live._access_proven("cilium_status", evidence) is False


def test_a_mixed_error_set_blocks():
    """One read failure alongside a health state still blocks."""
    live, status = _live()
    evidence = _cilium(
        status.PARTIAL,
        [
            {"source": "cilium-a", "reason": "agent_not_ready"},
            {"source": "pods", "reason": "authorization"},
        ],
        passed=2,
    )

    assert live._access_proven("cilium_status", evidence) is False


@pytest.mark.parametrize("state", ("BLOCKED", "UNKNOWN"))
def test_only_pass_or_partial_can_prove_access(state):
    """A collector that did not complete cannot prove anything."""
    live, _status = _live()

    assert (
        live._access_proven("storage_report", {"status": state, "errors": []}) is False
    )


def test_a_partial_with_no_errors_blocks():
    """PARTIAL without a recorded reason is unexplained, so it is not accepted."""
    live, status = _live()

    assert (
        live._access_proven(
            "storage_report",
            {"status": status.PARTIAL, "errors": [], "record_count": 5},
        )
        is False
    )


def test_the_receipt_records_which_checks_blocked(monkeypatch):
    """The receipt names the blocking checks rather than only a status."""
    live, status = _live()
    results = {
        "storage_report": {
            "kind": "storage_report",
            "status": status.PASS,
            "records": [{}],
            "errors": [],
            "inventory": {},
        },
        "event_trace": {
            "kind": "event_trace",
            "status": status.PARTIAL,
            "records": [],
            "errors": [{"source": "core_events", "reason": "timeout"}],
        },
        "cilium_status": {
            "kind": "cilium_status",
            "status": status.PARTIAL,
            "records": [{"status": status.PASS}],
            "errors": [{"source": "cilium-a", "reason": "agent_not_ready"}],
            "ciliumnodes": [],
        },
    }
    monkeypatch.setattr(live, "collect_storage", lambda *_a: results["storage_report"])
    monkeypatch.setattr(live, "collect_events", lambda *_a: results["event_trace"])
    monkeypatch.setattr(live, "collect_cilium", lambda *_a: results["cilium_status"])

    receipt = live.collect_live_checks(object(), object(), {})

    assert receipt["status"] == status.BLOCKED
    assert receipt["blocking_live_checks"] == ["event_trace"]
    assert receipt["live_checks"]["cilium_status"]["access_proven"] is True
    assert receipt["live_checks"]["event_trace"]["access_proven"] is False
    assert receipt["live_checks"]["storage_report"]["access_proven"] is True


def test_cluster_wide_list_reads_use_a_bound_sized_for_a_real_cluster(monkeypatch):
    """A slow but healthy list must not be reported as a timeout."""
    collect = import_script_module("core.collect")
    runtime = import_script_module("core.runtime")
    seen: list[int] = []

    def fake_run(argv, *, env=None, timeout=None):
        seen.append(timeout)
        return runtime.CommandResult(tuple(argv), 0, "{}", "")

    monkeypatch.setattr(collect, "run_command", fake_run)
    collect._read_json(["kubectl", "get", "events", "-A", "-o", "json"])

    assert collect.LIST_TIMEOUT_SECONDS > 25
    assert seen == [collect.LIST_TIMEOUT_SECONDS]
