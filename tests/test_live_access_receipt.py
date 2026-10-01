"""Tests for the live-access gate and its receipt.

These pin the acceptance rule: a receipt passes only when every credential
source resolved and every live check succeeded, the Cilium check runs the real
non-TTY health command rather than an `auth can-i` answer, and no token value
ever reaches the receipt.
"""

from __future__ import annotations

import json

import pytest
from conftest import import_script_module


def _modules():
    return (
        import_script_module("core.liveaccess"),
        import_script_module("core.receipt"),
        import_script_module("core.runtime"),
        import_script_module("core.status"),
    )


def _target(path):
    return import_script_module("core.target").load_target(path)


def _surfaces(status):
    return {
        name: {"status": status, "identity": f"unit-{name}", "target": name,
               "observed_capability": []}
        for name in ("github", "gitlab", "kubernetes")
    }


def _sources():
    return {
        "github": {"kind": "cli_profile", "reference": "gh_profile:github.example.test"},
        "gitlab": {"kind": "token_file", "reference": "/unit/gitlab.token"},
        "kubernetes": {"kind": "kubeconfig", "reference": "/unit/kubeconfig"},
    }


def test_receipt_identifies_host_revision_sources_and_results(target_file):
    """One receipt carries host, time, revision, sources, identities and checks."""
    liveaccess, receipt, _runtime, status = _modules()
    built = receipt.build(
        _target(target_file), sources=_sources(), surfaces=_surfaces(status.PASS),
        checks=[receipt.live_check("kubernetes_read:nodes", "kubernetes", status.PASS)],
    )

    assert built["kind"] == "access_receipt"
    assert built["status"] == status.PASS
    assert built["observed_at"].endswith("Z")
    assert built["execution_host"]["hostname"]
    assert set(built["credential_sources"]) == {"github", "gitlab", "kubernetes"}
    assert built["identities"]["kubernetes"] == "unit-kubernetes"
    assert built["targets"]["github"]["repository"] == "unit/repo"
    assert "nodes" in json.dumps(built["live_checks"])
    assert liveaccess.HEALTH_COMMAND == ("cilium-health", "status", "-o", "json")


def test_receipt_blocks_on_an_unresolved_credential_source(target_file):
    """An unresolved source blocks even when every live check passed."""
    _liveaccess, receipt, _runtime, status = _modules()
    sources = _sources()
    sources["kubernetes"] = {"kind": "unresolved", "reference": "kubeconfig_context_unresolved"}

    built = receipt.build(
        _target(target_file), sources=sources, surfaces=_surfaces(status.PASS), checks=[],
    )

    assert built["status"] == status.BLOCKED
    assert built["blocking"]["unresolved_credential_sources"] == ["kubernetes"]


def test_receipt_blocks_on_a_failed_live_check(target_file):
    """A single failed live check blocks regardless of surface status."""
    _liveaccess, receipt, _runtime, status = _modules()
    built = receipt.build(
        _target(target_file), sources=_sources(), surfaces=_surfaces(status.PASS),
        checks=[
            receipt.live_check("kubernetes_read:pvs", "kubernetes", status.PASS),
            receipt.live_check("cilium_health_exec", "kubernetes", status.BLOCKED,
                               detail="no_ready_agent_answered_health"),
        ],
    )

    assert built["status"] == status.BLOCKED
    assert built["blocking"]["failed_live_checks"] == ["cilium_health_exec"]


def test_receipt_never_carries_a_credential_value(target_file):
    """Evidence is sanitized, so an echoed secret cannot land in the receipt."""
    _liveaccess, receipt, _runtime, status = _modules()
    built = receipt.build(
        _target(target_file), sources=_sources(), surfaces=_surfaces(status.PASS),
        checks=[receipt.live_check(
            "gitlab_trace_access", "gitlab", status.PASS,
            evidence="Authorization: Bearer super-secret-value",
        )],
    )

    body = json.dumps(built)
    assert "super-secret-value" not in body
    assert "[REDACTED]" in body


def test_collector_reads_cover_every_resource_the_collectors_use():
    """The gate reads the union of the three Kubernetes collectors' resources."""
    liveaccess, _receipt, _runtime, _status = _modules()
    served = {name for _r, _n, collectors in liveaccess.COLLECTOR_READS.values()
              for name in collectors}

    assert served == {"storage", "events", "cilium"}
    resources = {resource for resource, _n, _c in liveaccess.COLLECTOR_READS.values()}
    for required in ("nodes", "persistentvolumeclaims", "persistentvolumes",
                     "storageclasses", "volumeattachments", "events",
                     "events.events.k8s.io", "ciliumnodes.cilium.io"):
        assert required in resources


def test_cilium_health_exec_tries_every_ready_agent(target_file, monkeypatch):
    """One unhealthy agent does not block while another answers."""
    liveaccess, _receipt, runtime, status = _modules()
    pods = {"items": [
        {"metadata": {"name": "cilium-aaa"},
         "status": {"conditions": [{"type": "Ready", "status": "True"}]}},
        {"metadata": {"name": "cilium-bbb"},
         "status": {"conditions": [{"type": "Ready", "status": "True"}]}},
    ]}
    attempted: list[str] = []

    def fake_run(argv, **_kwargs):
        argv = list(argv)
        pod = argv[argv.index("exec") + 1]
        attempted.append(pod)
        if pod == "cilium-aaa":
            return runtime.CommandResult(tuple(argv), 1, "", "error: timed out")
        return runtime.CommandResult(
            tuple(argv), 0,
            json.dumps({"local": {"name": "unit-node"}, "nodes": [{"name": "peer"}]}), "",
        )

    monkeypatch.setattr(liveaccess, "cilium_namespace", lambda _target: "cilium")
    monkeypatch.setattr(liveaccess, "read_json", lambda *_a, **_k: (pods, None))
    monkeypatch.setattr(liveaccess, "run_command", fake_run)

    check = liveaccess.cilium_health_exec(_target(target_file))

    assert check["status"] == status.PASS
    assert attempted == ["cilium-aaa", "cilium-bbb"]
    assert check["evidence"]["pod"] == "cilium-bbb"
    assert check["evidence"]["agents_attempted"] == 2
    assert check["evidence"]["command"] == ["cilium-health", "status", "-o", "json"]


def test_cilium_health_exec_blocks_when_no_agent_answers(target_file, monkeypatch):
    """Permission to exec is not acceptance evidence when the command fails."""
    liveaccess, _receipt, runtime, status = _modules()
    pods = {"items": [{"metadata": {"name": "cilium-aaa"},
                       "status": {"conditions": [{"type": "Ready", "status": "True"}]}}]}

    monkeypatch.setattr(liveaccess, "cilium_namespace", lambda _target: "cilium")
    monkeypatch.setattr(liveaccess, "read_json", lambda *_a, **_k: (pods, None))
    monkeypatch.setattr(
        liveaccess, "run_command",
        lambda argv, **_k: runtime.CommandResult(tuple(argv), 0, "not json", ""),
    )

    check = liveaccess.cilium_health_exec(_target(target_file))

    assert check["status"] == status.BLOCKED
    assert check["detail"] == "no_ready_agent_answered_health"
    assert check["evidence"]["attempts"] == [{"pod": "cilium-aaa", "reason": "invalid_health_json"}]


def test_cilium_health_exec_blocks_when_no_agent_is_ready(target_file, monkeypatch):
    """A cluster with no ready agent cannot produce collector evidence either."""
    liveaccess, _receipt, _runtime, status = _modules()
    pods = {"items": [{"metadata": {"name": "cilium-aaa"},
                       "status": {"conditions": [{"type": "Ready", "status": "False"}]}}]}

    monkeypatch.setattr(liveaccess, "cilium_namespace", lambda _target: "cilium")
    monkeypatch.setattr(liveaccess, "read_json", lambda *_a, **_k: (pods, None))

    check = liveaccess.cilium_health_exec(_target(target_file))

    assert check["status"] == status.BLOCKED
    assert check["detail"] == "no_ready_cilium_agent"


@pytest.mark.parametrize(
    ("job_url", "reason"),
    (
        ("https://other.example.test/unit/repo/-/jobs/1", "job_url_wrong_host"),
        ("http://gitlab.example.test/unit/repo/-/jobs/1", "job_url_wrong_host"),
        ("https://gitlab.example.test/unit/repo/-/jobs/abc", "job_url_malformed"),
        ("https://gitlab.example.test/-/jobs/1", "job_url_malformed"),
    ),
)
def test_job_access_rejects_a_url_outside_the_selected_host(target_file, job_url, reason):
    """Job proof is bound to the exact selected GitLab FQDN and job path."""
    liveaccess, _receipt, _runtime, status = _modules()

    checks = liveaccess.gitlab_job_access(_target(target_file), job_url)

    assert len(checks) == 1
    assert checks[0]["status"] == status.BLOCKED
    assert checks[0]["detail"] == reason


def test_job_access_proves_job_pipeline_runner_and_trace(target_file, monkeypatch):
    """Job diagnostics require the job, its pipeline, its runner and its trace."""
    liveaccess, _receipt, runtime, status = _modules()
    responses = {
        "jobs/7": {"id": 7, "name": "unit", "status": "failed",
                   "failure_reason": "runner_system_failure",
                   "pipeline": {"id": 11}, "runner": {"id": 13}},
        "pipelines/11": {"id": 11, "ref": "main", "sha": "a" * 40, "status": "failed"},
        "runners/13": {"id": 13, "description": "unit-runner", "online": True,
                       "tag_list": ["unit"]},
    }

    def fake_read(argv, env=None):
        endpoint = list(argv)[-1]
        for suffix, value in responses.items():
            if endpoint.endswith(suffix):
                return value, None
        return None, "not_found"

    monkeypatch.setattr(liveaccess, "read_json", fake_read)
    monkeypatch.setattr(
        liveaccess, "run_command_tail",
        lambda argv, **_k: runtime.CommandResult(tuple(argv), 0, "line one\nline two", ""),
    )

    checks = liveaccess.gitlab_job_access(
        _target(target_file), "https://gitlab.example.test/unit/repo/-/jobs/7",
    )
    by_name = {item["name"]: item for item in checks}

    assert set(by_name) == {"gitlab_job_access", "gitlab_pipeline_access",
                            "gitlab_runner_access", "gitlab_trace_access"}
    assert all(item["status"] == status.PASS for item in checks)
    assert by_name["gitlab_job_access"]["evidence"]["failure_reason"] == "runner_system_failure"
    assert by_name["gitlab_runner_access"]["evidence"]["id"] == 13
    assert by_name["gitlab_trace_access"]["evidence"]["line_count"] == 2


def test_job_access_blocks_when_the_job_references_no_runner(target_file, monkeypatch):
    """A job with no runner cannot prove runner access, so that check blocks."""
    liveaccess, _receipt, runtime, status = _modules()

    monkeypatch.setattr(
        liveaccess, "read_json",
        lambda argv, env=None: (
            ({"id": 7, "pipeline": {"id": 11}}, None) if str(argv[-1]).endswith("jobs/7")
            else ({"id": 11}, None)
        ),
    )
    monkeypatch.setattr(
        liveaccess, "run_command_tail",
        lambda argv, **_k: runtime.CommandResult(tuple(argv), 0, "", ""),
    )

    checks = liveaccess.gitlab_job_access(
        _target(target_file), "https://gitlab.example.test/unit/repo/-/jobs/7",
    )
    by_name = {item["name"]: item for item in checks}

    assert by_name["gitlab_runner_access"]["status"] == status.BLOCKED
    assert by_name["gitlab_runner_access"]["detail"] == "not_referenced_by_job"


def test_verify_skips_deeper_reads_when_a_surface_is_blocked(target_file, monkeypatch):
    """A denied surface stops its own deeper reads instead of repeating the denial."""
    liveaccess, _receipt, _runtime, status = _modules()
    surfaces = _surfaces(status.PASS)
    surfaces["kubernetes"]["status"] = status.BLOCKED
    surfaces["kubernetes"]["reason"] = "authentication"
    called: list[str] = []

    monkeypatch.setattr(liveaccess, "check_access",
                        lambda _target, publication=False: {"surfaces": surfaces})
    monkeypatch.setattr(liveaccess, "resolve_sources", lambda _target, _environ: _sources())
    monkeypatch.setattr(liveaccess, "kubernetes_live_reads",
                        lambda _target: called.append("reads") or [])
    monkeypatch.setattr(liveaccess, "cilium_health_exec",
                        lambda _target: called.append("exec") or {})

    built = liveaccess.verify(_target(target_file), environ={})

    assert called == []
    assert built["status"] == status.BLOCKED
    assert built["blocking"]["blocked_surfaces"] == ["kubernetes"]
