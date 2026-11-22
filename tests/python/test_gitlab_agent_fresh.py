"""Check the confirmed Agent handoff before any registration write."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from conftest import import_script_module
from test_gitlab_agent_installation import SELECTION_CASE
from test_gitlab_agent_plan import CLUSTER_UID, IMAGE_DIGEST, KAS, NAMESPACE_UID

FRESH = import_script_module("lib.python.core.gitlab.cluster_agent.fresh")
SCOPE = import_script_module("lib.python.core.kubernetes.run_scope")
RESOURCES = import_script_module("lib.python.core.kubernetes.resources")
SELECTION = import_script_module("lib.python.core.gitlab.cluster_agent.selection")

CONFIRMATION = "a" * 64
NAMESPACE = SELECTION_CASE.scope.namespace
NATIVE_NAMESPACE = json.dumps(
    {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": NAMESPACE,
            "uid": NAMESPACE_UID,
            "labels": {
                **SELECTION_CASE.scope.labels,
                RESOURCES.MANAGER_LABEL: RESOURCES.MANAGER_VALUE,
                RESOURCES.OPERATION_LABEL: SELECTION_CASE.scope.run_id,
            },
        },
    }
)


@pytest.mark.parametrize(
    ("owned", "change", "reason", "write_count"),
    (
        (False, None, None, 0),
        (True, None, None, 1),
        (True, "stale", "confirm_plan_mismatch_rerun_live_plan", 0),
        (False, "borrowed_uid", "agent_plan_namespace_changed", 0),
        (True, "cluster_uid", "agent_plan_cluster_changed", 0),
        (True, "occupied", "agent_namespace_create_unconfirmed", 1),
        (True, "replacement_uid", "agent_namespace_create_identity_mismatch", 1),
        (True, "wrong_labels", "agent_namespace_create_identity_mismatch", 1),
    ),
)
def test_confirmed_fresh_connect_handoff(
    monkeypatch: pytest.MonkeyPatch,
    owned: bool,
    change: str | None,
    reason: str | None,
    write_count: int,
) -> None:
    """Mutate a single observation on a copy of the native response fixture."""
    selected = replace(
        SELECTION_CASE, scope=replace(SELECTION_CASE.scope, owns_namespace=owned)
    )
    namespace_result = SimpleNamespace(returncode=0, stdout=NATIVE_NAMESPACE)
    observed = json.loads(NATIVE_NAMESPACE)
    cluster_uid = CLUSTER_UID
    borrowed_uid = NAMESPACE_UID
    confirmation = CONFIRMATION
    if change == "stale":
        confirmation = "b" * 64
    elif change == "borrowed_uid":
        borrowed_uid = "replacement"
    elif change == "cluster_uid":
        cluster_uid = "replacement"
    elif change == "occupied":
        namespace_result.returncode = 1
    elif change == "replacement_uid":
        observed["metadata"]["uid"] = "replacement"
    elif change == "wrong_labels":
        observed["metadata"]["labels"][SCOPE.RUN_ID_LABEL] = "other-run"

    kubernetes = Mock()
    kubernetes.command.return_value = namespace_result
    kubernetes.read.return_value = observed
    gitlab = SimpleNamespace(
        session=SimpleNamespace(origin="https://gitlab.example.test")
    )
    authorities = FRESH.AgentAuthorities(
        gitlab, Mock(), kubernetes, selected.project, KAS, CLUSTER_UID
    )
    plan = FRESH.FreshConnectPlan(None if owned else NAMESPACE_UID, CONFIRMATION)
    prepare = Mock(return_value=plan)
    connect = Mock()
    monkeypatch.setattr(FRESH, "prepare_fresh_connect", prepare)
    monkeypatch.setattr(FRESH, "connect_fresh_agent", connect)
    monkeypatch.setattr(FRESH, "read_agent_cluster_uid", Mock(return_value=cluster_uid))
    monkeypatch.setattr(
        FRESH,
        "read_agent_cluster_scope",
        Mock(return_value=(CLUSTER_UID, borrowed_uid)),
    )
    chart = FRESH.VerifiedChart(Path("fixture.tgz"), SELECTION.CHART_SHA256)
    image = FRESH.ImageManifestIdentity(IMAGE_DIGEST, frozenset((IMAGE_DIGEST,)))
    deadline = FRESH.RequestDeadline(30, monotonic=lambda: 0)
    if reason is None:
        assert (
            FRESH.confirm_and_connect_fresh(
                authorities,
                selected,
                chart,
                image,
                confirmation=confirmation,
                deadline=deadline,
            )
            is connect.return_value
        )
        pending = connect.call_args.args[3]
        assert pending["state"] == "PENDING"
        assert pending["namespace_uid"] == NAMESPACE_UID
        assert pending["image_digest"] == IMAGE_DIGEST
        assert pending["created"]["namespace"] is owned
        assert pending["resources"][0]["created"] is owned
        assert pending["agent_id"] is pending["registration_credential_id"] is None
        assert (
            pending["registration_credential_name"]
            == pending["credential_resource_name"]
        )
    else:
        with pytest.raises((FRESH.KubernetesError, ValueError), match=reason):
            FRESH.confirm_and_connect_fresh(
                authorities,
                selected,
                chart,
                image,
                confirmation=confirmation,
                deadline=deadline,
            )
        connect.assert_not_called()
    prepare.assert_called_once_with(
        authorities, selected, chart, image, deadline=deadline
    )
    assert kubernetes.command.call_count == write_count
    if write_count:
        assert kubernetes.command.call_args.args == (
            "create",
            "--raw",
            "/api/v1/namespaces",
            "-f",
            "-",
        )
        posted = json.loads(kubernetes.command.call_args.kwargs["input_data"])
        assert posted["metadata"]["name"] == NAMESPACE
        assert posted["metadata"]["labels"][SCOPE.RUN_ID_LABEL] == selected.scope.run_id
    kubernetes.apply.assert_not_called()
