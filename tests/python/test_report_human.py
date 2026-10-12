"""Human report rendering tests for nested diagnostic evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.python.conftest import import_script_module

REPORT = import_script_module("lib.python.core.report")


@pytest.mark.parametrize(
    ("data", "expected"),
    (
        (
            {
                "kind": "k8s_verify_mtu_consistency",
                "status": "PASS",
                "target": "unit-context",
                "plan_digest": "unit-plan",
                "planned_nodes": ["worker-a"],
                "records": [
                    {
                        "node": "worker-a",
                        "interface": "ens3",
                        "pci_device": "0000:00:03.0",
                        "mtu": 1500,
                        "oper_state": "up",
                        "ipv4_addresses": ["192.0.2.10"],
                    }
                ],
                "findings": [{"code": "mtu_mismatch", "mtu_values": [1500, 9000]}],
                "errors": [],
                "cleanup": {"status": "PASS"},
            },
            (
                "Physical NIC MTU: PASS\n"
                "Target: unit-context\n"
                "Plan: unit-plan\n"
                "Nodes: 1 selected\n"
                "Selection: PCI Ethernet interfaces with an IPv4 address\n"
                "Apply creates temporary oc debug Pods and verifies cleanup.\n"
                "NODE                      INTERFACE     PCI DEVICE       MTU   STATE   IPv4\n"
                "worker-a                 ens3          0000:00:03.0      1500 up      192.0.2.10\n"
                "Finding: mtu_mismatch MTUs=[1500, 9000]\n"
                "Cleanup: PASS\n"
            ),
        ),
        (
            {
                "kind": "access_check",
                "status": "PASS",
                "surfaces": {
                    "gitlab": {
                        "status": "PASS",
                        "target": "https://gitlab.example.test",
                        "reason": "authenticated",
                        "next_step": "run operation",
                    }
                },
            },
            (
                "Access: PASS\n"
                "  gitlab: PASS https://gitlab.example.test\n"
                "    reason: authenticated\n"
                "    next: run operation\n"
            ),
        ),
        (
            {
                "kind": "gitlab_runner",
                "status": "PASS",
                "target": "unit/repo",
                "operation": "list",
                "plan_digest": "unit-plan",
                "related_job": {"id": 123, "status": "success", "runner_id": 9},
                "plan": {"requested_tags": ["linux", "unit"]},
                "records": [
                    {
                        "id": 9,
                        "status": "online",
                        "online": True,
                        "access_level": "ref_protected",
                        "is_shared": False,
                        "tag_list": ["linux", "unit"],
                        "projects": ["unit/repo"],
                        "before_tags": ["legacy"],
                        "after_tags": ["linux", "unit"],
                    }
                ],
                "errors": [],
            },
            (
                "gitlab_runner: PASS\n"
                "Target: unit/repo\n"
                "Records: 1\n"
                "Operation: list\n"
                "Plan: unit-plan\n"
                "Related job: id=123 status=success runner_id=9\n"
                "Requested tags: linux,unit\n"
                "  runner_id=9  status=online  online=True  access=ref_protected  shared=False  tags=linux,unit\n"
                "    projects=['unit/repo']\n"
                "    before_tags=legacy\n"
                "    after_tags=linux,unit\n"
            ),
        ),
        (
            {
                "kind": "gitlab_job",
                "status": "PASS",
                "target": "unit/repo",
                "operation": "list",
                "records": [
                    {
                        "id": 321,
                        "name": "unit-test",
                        "stage": "test",
                        "status": "failed",
                        "ref": "main",
                        "failure_reason": "script_failure",
                        "stuck": True,
                    }
                ],
                "errors": [],
                "truncated": False,
            },
            (
                "gitlab_job: PASS\n"
                "Target: unit/repo\n"
                "Records: 1\n"
                "  id=321  name=unit-test  stage=test  status=failed  ref=main\n"
                "    failure_reason=script_failure\n"
                "    stuck=true\n"
                "Truncated: false\n"
            ),
        ),
        (
            {
                "kind": "gitlab_pipeline",
                "status": "PASS",
                "target": "unit/repo",
                "records": [
                    {
                        "name": "pipeline-77",
                        "status": "running",
                        "progress": {"terminal": 1, "total": 2},
                        "stages": [{"name": "test", "terminal": 1, "total": 2}],
                    }
                ],
                "errors": [],
            },
            (
                "gitlab_pipeline: PASS\n"
                "Target: unit/repo\n"
                "Records: 1\n"
                "  name=pipeline-77  status=running\n"
                "    jobs: 1/2 terminal\n"
                "    stage=test terminal=1/2\n"
            ),
        ),
        (
            {
                "kind": "ceph_cluster",
                "status": "PASS",
                "target": "unit-context",
                "records": [{"name": "osd.0", "status": "up"}],
                "errors": [],
                "health": "HEALTH_OK",
                "inactive_pgs": [],
                "osds": [{"status": "down"}, {"status": "up"}],
                "osd_tree": [
                    {
                        "name": "default",
                        "type": "root",
                        "id": -1,
                        "children": [
                            {
                                "name": "osd.0",
                                "type": "osd",
                                "id": 0,
                                "status": "up",
                                "children": [],
                            }
                        ],
                    }
                ],
                "actions": ["inspect osd.0"],
            },
            (
                "ceph_cluster: PASS\n"
                "Target: unit-context\n"
                "Records: 1\n"
                "  name=osd.0  status=up\n"
                "Ceph health: HEALTH_OK\n"
                "Inactive PGs: 0\n"
                "OSDs down: 1\n"
                "Ceph hierarchy:\n"
                "  default (root, id=-1)\n"
                "    osd.0 (osd, id=0, status=up)\n"
                "Action: inspect osd.0\n"
            ),
        ),
    ),
)
def test_human_output_is_byte_stable_for_specialized_reports(
    data: dict[str, object],
    expected: str,
) -> None:
    """Specialized report branches preserve their established terminal bytes."""
    assert REPORT.human(data) == expected


def _access_report() -> dict[str, object]:
    """Return one minimal access result reused by emit persistence checks."""
    return {
        "kind": "access_check",
        "status": "PASS",
        "surfaces": {"github": {"status": "PASS", "target": "unit/repo"}},
    }


def test_emit_accepts_omitted_and_none_output_directories() -> None:
    """Omitting persistence produces the same human report as an explicit None."""
    expected = "Access: PASS\n  github: PASS unit/repo\n"

    assert REPORT.emit(_access_report(), "human") == expected
    assert REPORT.emit(_access_report(), "human", None) == expected


def test_emit_persists_paired_files_only_for_an_explicit_directory(
    tmp_path: Path,
) -> None:
    """A requested output directory receives the rendered JSON and human pair."""
    destination = tmp_path / "reports"
    rendered = REPORT.emit(_access_report(), "human", str(destination))
    run_directories = list(destination.iterdir())

    assert len(run_directories) == 1
    run_directory = run_directories[0]
    json_path = run_directory / "access_check.json"
    text_path = run_directory / "access_check.txt"
    expected = (
        "Access: PASS\n"
        "  github: PASS unit/repo\n"
        f"Report JSON: {json_path}\n"
        f"Report text: {text_path}\n"
    )
    assert rendered == expected
    assert json.loads(json_path.read_text(encoding="utf-8"))["report_files"] == {
        "json": str(json_path),
        "text": str(text_path),
    }
    assert text_path.read_text(encoding="utf-8") == expected


def test_human_storage_report_includes_nested_attachments_and_controllers():
    """Storage summaries show the matching attachment and controller context."""
    report = import_script_module("lib.python.core.report")
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
    report = import_script_module("lib.python.core.report")
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
    report = import_script_module("lib.python.core.report")
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
