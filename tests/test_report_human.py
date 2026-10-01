"""Human report rendering tests for nested diagnostic evidence."""

from __future__ import annotations

from conftest import import_script_module


def test_human_storage_report_includes_nested_attachments_and_controllers():
    """Storage summaries show the matching attachment and controller context."""
    report = import_script_module("core.report")
    data = {
        "kind": "storage_report",
        "status": "PASS",
        "target": "unit-context",
        "records": [
            {
                "namespace": "app",
                "name": "claim-selected",
                "attachments": [
                    {"name": "attach-selected", "node": "worker-a", "attached": True}
                ],
                "controllers": [{"name": "deploy-selected", "desired": 2, "ready": 1}],
            }
        ],
        "errors": [],
    }

    text = report.human(data)

    assert "attach-selected" in text
    assert "worker-a" in text
    assert "deploy-selected" in text


def test_human_gitlab_report_includes_matching_trace_tail():
    """CI job summaries include the trace line that matched the search."""
    report = import_script_module("core.report")
    data = {
        "kind": "gitlab_job",
        "status": "PASS",
        "target": "https://gitlab.example.test",
        "records": [
            {"job_id": 123, "name": "selected-job", "trace_tail": "selected trace line"}
        ],
        "errors": [],
    }

    text = report.human(data)

    assert "selected-job" in text
    assert "selected trace line" in text


def test_human_cilium_report_includes_health_payload_summary():
    """Cilium summaries include nested health data from the agent command."""
    report = import_script_module("core.report")
    data = {
        "kind": "cilium_status",
        "status": "PASS",
        "target": "unit-context",
        "records": [
            {
                "namespace": "kube-system",
                "name": "cilium-ready",
                "node": "worker-a",
                "status": "PASS",
                "health": {"connectivity": {"status": "reachable"}},
            }
        ],
        "errors": [],
    }

    text = report.human(data)

    assert "cilium-ready" in text
    assert "worker-a" in text
    assert "reachable" in text
