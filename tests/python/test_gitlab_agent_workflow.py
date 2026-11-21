"""Exercise the Agent operation dispatcher with isolated provider responses."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock

import pytest
from conftest import import_script_module
from test_gitlab_agent_installation import SELECTION_CASE
from test_gitlab_agent_plan import CLUSTER_UID, IMAGE_DIGEST, KAS, NAMESPACE_UID

WORKFLOW = import_script_module("lib.python.core.gitlab.cluster_agent.workflow")
OCI = import_script_module("lib.python.core.oci")
HELM = import_script_module("lib.python.core.helm.native")

RUN = SELECTION_CASE.scope.run_id
DIGEST = "a" * 64
JOB_URL = "https://gitlab.example.test/consumer/project/-/jobs/4"
BASELINE = MappingProxyType(
    {
        "operation": "connect",
        "project": SELECTION_CASE.project.path,
        "project_name": "consumer-override",
        "agent_name": None,
        "run_id": RUN,
        "namespace": None,
        "job_url": None,
    }
)
READ = WORKFLOW.ActionMode.READ
PLAN = WORKFLOW.ActionMode.LIVE_PLAN
APPLY = WORKFLOW.ActionMode.APPLY


@pytest.mark.parametrize(
    ("operation", "mode", "retained", "branch", "action"),
    (
        ("connect", PLAN, False, "prepare_fresh_connect", WORKFLOW.PLANNED),
        ("connect", APPLY, False, "confirm_and_connect_fresh", WORKFLOW.APPLIED),
        ("connect", APPLY, True, "observe_repeat_connect", WORKFLOW.NO_OP),
        ("delete", PLAN, True, "plan_tracked_delete", WORKFLOW.PLANNED),
        ("delete", APPLY, True, "delete_tracked_agent", WORKFLOW.APPLIED),
        ("delete", APPLY, False, "observe_repeat_delete", WORKFLOW.NO_OP),
        ("verify", READ, True, "verify_ci_access", WORKFLOW.VERIFIED),
        ("verify", READ, False, None, "agent_verify_journal_absent"),
    ),
)
def test_agent_workflow_routes_exact_operation(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    mode: WORKFLOW.ActionMode,
    retained: bool,
    branch: str | None,
    action: str,
) -> None:
    """Select one approved path without using a mocked action as native evidence."""
    intent = dict(BASELINE)
    intent["operation"] = operation
    if operation == "delete":
        intent["agent_name"] = SELECTION_CASE.agent_name
    if operation == "verify":
        intent["job_url"] = JOB_URL
    case = SELECTION_CASE
    gitlab = SimpleNamespace(
        session=SimpleNamespace(target_reference=intent["project"])
    )
    kubernetes = Mock()
    authorities = WORKFLOW.AgentAuthorities(
        gitlab, Mock(), kubernetes, case.project, KAS, CLUSTER_UID
    )
    record = WORKFLOW.RecoveryRecord(
        "journal", "4", {"agent_id": 9, "namespace_uid": NAMESPACE_UID}
    )
    connectivity = WORKFLOW.AgentConnectivity(record, ())
    chart = HELM.VerifiedChart(Path("fixture-agent.tgz"), WORKFLOW.CHART_SHA256)
    image = OCI.ImageManifestIdentity(IMAGE_DIGEST, frozenset((IMAGE_DIGEST,)))
    lifecycle = {
        "prepare_fresh_connect": Mock(
            return_value=WORKFLOW.FreshConnectPlan(None, DIGEST)
        ),
        "confirm_and_connect_fresh": Mock(return_value=connectivity),
        "observe_repeat_connect": Mock(return_value=(DIGEST, connectivity)),
        "plan_tracked_delete": Mock(return_value=DIGEST),
        "delete_tracked_agent": Mock(),
        "observe_repeat_delete": Mock(return_value=(DIGEST, Mock())),
        "verify_ci_access": Mock(),
    }
    for name, mock in lifecycle.items():
        monkeypatch.setattr(WORKFLOW, name, mock)
    locate = Mock(return_value=(case, record) if retained else None)
    inspect = Mock(return_value=image)
    pull = Mock(return_value=nullcontext(chart))
    monkeypatch.setattr(WORKFLOW, "locate_agent_run", locate)
    monkeypatch.setattr(WORKFLOW, "inspect_image_identity", inspect)
    monkeypatch.setattr(WORKFLOW, "pull_verified_chart", pull)
    deadline = WORKFLOW.RequestDeadline(30, monotonic=lambda: 0)
    producers = {"definition_sha256": WORKFLOW.ProducerBlob(".gitlab-ci.yml", DIGEST)}
    options = {
        "deadline": deadline,
        "confirmation": DIGEST if mode == APPLY else None,
        "producers": producers if mode == READ else None,
    }
    if branch is None:
        with pytest.raises(WORKFLOW.KubernetesError, match=action):
            WORKFLOW.run_agent_workflow(authorities, intent, mode, **options)
        assert all(mock.call_count == 0 for mock in lifecycle.values())
        inspect.assert_not_called()
        return
    outcome = WORKFLOW.run_agent_workflow(authorities, intent, mode, **options)
    assert outcome.action == action
    assert outcome.before == (record if retained else None)
    assert outcome.digest == (None if mode == READ else DIGEST)
    if retained:
        assert outcome.selection == case
    elif operation == "connect":
        assert outcome.selection.agent_name == "consumer-override"
    selected = {name for name, mock in lifecycle.items() if mock.call_count}
    assert selected == (
        {branch, "observe_repeat_connect"} if mode == READ else {branch}
    )
    if operation == "delete":
        inspect.assert_not_called()
        pull.assert_not_called()
    else:
        inspect.assert_called_once()
        pull.assert_called_once()
    if mode == READ:
        _, url, evidence = lifecycle[branch].call_args.args
        assert url == JOB_URL
        assert evidence.agent.id == 9
        assert evidence.run_id == RUN
        assert evidence.namespace_uid == NAMESPACE_UID
        assert evidence.producers == producers
    if operation == "delete" and mode == APPLY:
        assert lifecycle[branch].call_args.kwargs["confirmation"] == DIGEST
