"""Human report rendering tests for nested diagnostic evidence."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

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


def test_machine_report_emission_sanitizes_nested_secret_values():
    """JSON/YAML report output has a final redaction boundary."""
    report = import_script_module("core.report")
    data = {
        "kind": "gitlab_job",
        "status": "PASS",
        "target": "https://gitlab.example.test",
        "records": [
            {
                "trace_tail": (
                    "CI_JOB_TOKEN=unit-ci-token\n"
                    '{"password":"unit-json-password","token":"unit-json-token"}'
                )
            }
        ],
        "errors": [],
        "summary": {"record_count": 1, "error_count": 0},
    }

    rendered = report.emit(data, "json")
    parsed = json.loads(rendered)

    assert parsed["kind"] == "gitlab_job"
    for secret in ("unit-ci-token", "unit-json-password", "unit-json-token"):
        assert secret not in rendered
    assert "[REDACTED]" in rendered


def test_emit_publishes_unique_paired_output_directories_concurrently(tmp_path):
    """Concurrent report writes keep each JSON and human report paired."""
    report = import_script_module("core.report")
    output_dir = tmp_path / "reports"

    def publish(record_name: str) -> tuple[str, str]:
        rendered = report.emit(
            {
                "kind": "storage_report",
                "status": "PASS",
                "target": "unit-context",
                "filters": {"search": None},
                "records": [{"namespace": "app", "name": record_name}],
                "errors": [],
                "summary": {"record_count": 1, "error_count": 0},
            },
            "json",
            str(output_dir),
        )
        payload = json.loads(rendered)
        return record_name, payload["run_id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(publish, ("claim-a", "claim-b")))

    assert len({run_id for _name, run_id in runs}) == 2
    for record_name, run_id in runs:
        published = output_dir / f"storage_report-{run_id}"
        machine = published / "report.json"
        human = published / "report.txt"
        assert machine.is_file()
        assert human.is_file()
        parsed = json.loads(machine.read_text(encoding="utf-8"))
        assert parsed["run_id"] == run_id
        assert parsed["records"] == [{"namespace": "app", "name": record_name}]
        assert record_name in human.read_text(encoding="utf-8")
