"""Focused unit contracts for the GitLab runner-manager workflow.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
import signal
from argparse import Namespace
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Final, Optional
from unittest.mock import Mock
from uuid import uuid4

import pytest
from conftest import import_script_module
from test_tool_pod import MemoryCluster

ACTION = import_script_module("lib.python.core.gitlab.runner_manager.action")
CALLBACKS = import_script_module("lib.python.core.gitlab.runner_manager.callbacks")
K8S_RUNNER = import_script_module("lib.python.core.kubernetes.runner")
RESOURCES = import_script_module("lib.python.core.kubernetes.resources")
RUN_SCOPE = import_script_module("lib.python.core.kubernetes.run_scope")
SCC_ACCESS = import_script_module("lib.python.core.kubernetes.scc_access")
TARGET = import_script_module("lib.python.core.target")
TOKEN = import_script_module("lib.python.core.gitlab.runner_manager.token")
WORKLOAD = import_script_module("lib.python.core.gitlab.runner_manager.workload")
SCRIPT = import_script_module("gitlab_runner_manager")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")
GITLAB_API = import_script_module("lib.python.core.gitlab.gitlab_api")
RUNTIME = import_script_module("lib.python.core.runtime")
SCHEMA = import_script_module("lib.python.core.schema")
STATUS = import_script_module("lib.python.sys.status")

PRIVATE_TOKEN = "glrt-private-unit-value"
CHANGED_PRIVATE_TOKEN = "glrt-changed-unit"
TOKEN_DIGEST = "sha256:" + hashlib.sha256(PRIVATE_TOKEN.encode()).hexdigest()
RUN_ID = "manager-unit-run"
NAMESPACE = "manager-unit-namespace"
NAMESPACE_UID: Final = "manager-unit-namespace-uid"
OBSERVED_PROTECTION: Final = "not_protected"
TAG_ARGUMENT: Final = "--tag"
APPLY_ARGUMENT: Final = "--apply"
CONFIRM_ARGUMENT: Final = "--confirm-plan"
FIELD_PLAN_DIGEST: Final = "plan_digest"
FIELD_STATUS: Final = "status"
FIELD_RECORDS: Final = "records"
FIELD_CALLBACK: Final = "callback"
FIELD_TAGS: Final = "tag_list"
EXPECTED_TAGS_KEYWORD: Final = "expected_tags"
RUNNER_READ_ATTRIBUTE: Final = "read_runner_record"
RUNNER_ID = 73
RUNNER_DESCRIPTION = "unit-manager-runner"
RUNNER_LABELS = ("ci13", "unit")
IMAGE = "harbor.example.test/toolbox/ci@sha256:" + "a" * 64


def _args(
    action: str = "deploy",
    *,
    apply: bool = False,
    dry_run: bool = False,
) -> Namespace:
    """Build complete manager arguments without invoking a public parser.

    :param action: Manager ``deploy`` or ``delete`` action.
    :param apply: Whether mutation callbacks should be composed.
    :param dry_run: Whether preparation must defer credential reads.
    :returns: Namespace containing the manager fields used by production code.
    """
    return Namespace(
        action=action,
        apply=apply,
        binding=None,
        confirm_plan="unit-confirmation" if apply else None,
        description=RUNNER_DESCRIPTION,
        dry_run=dry_run,
        group=None,
        image=IMAGE if action == "deploy" else None,
        kind="gitlab_runner_manager",
        tag=list(RUNNER_LABELS),
        namespace=NAMESPACE,
        node="worker-ci13" if action == "deploy" else None,
        project="team/project",
        repository="toolbox/ci" if action == "deploy" else None,
        revision="b" * 40,
        run_id=RUN_ID,
        runner_id=RUNNER_ID,
        target="/unit/target.toml",
        timeout=30,
        token_file="/unit/runner.token" if action == "deploy" else None,
    )


def _target() -> TARGET.Target:
    """Build one finite target for offline callback composition.

    :returns: GitLab, Kubernetes, and Harbor target identity with no credentials.
    """
    return TARGET.Target(
        github=None,
        gitlab=TARGET.GitLabTarget(
            url="https://gitlab.example.test",
            host="gitlab.example.test",
            project="team/project",
        ),
        kubernetes=TARGET.KubernetesTarget(
            context="unit-context",
            server="https://api.cluster.example.test:6443",
            kubeconfig=None,
        ),
        active_surfaces=("gitlab", "kubernetes", "harbor"),
        tested_revision="b" * 40,
        source_file=Path("/unit/target.toml"),
        source_kind="unit-target",
        skill={"digest": "c" * 64},
        harbor=TARGET.HarborTarget(
            url="https://harbor.example.test",
            host="harbor.example.test",
            project="toolbox",
        ),
    )


def _runner_record(*, online: bool) -> CALLBACKS.RunnerRecord:
    """Build one validated exact runner read-back.

    :param online: Provider online flag and associated status/contact values.
    :returns: Public runner identity accepted by manager evidence helpers.
    """
    return {
        "id": RUNNER_ID,
        "description": RUNNER_DESCRIPTION,
        "runner_type": "project_type",
        "tag_list": list(RUNNER_LABELS),
        "status": "online" if online else "offline",
        "access_level": OBSERVED_PROTECTION,
        "online": online,
        "paused": False,
        "contacted_at": "2026-10-18T00:00:00Z" if online else None,
        "version": "18.0.0",
        "platform": "linux",
        "architecture": "amd64",
    }


def _action(
    action: str = "deploy", *, apply: bool = False
) -> ACTION.GitLabRunnerManagerAction:
    """Build a prepared-enough manager action for isolated callback tests.

    :param action: Manager ``deploy`` or ``delete`` action.
    :param apply: Whether the public workflow should include mutations.
    :returns: Manager action with exact target, scope, runtime, and token selection.
    """
    selected = ACTION.GitLabRunnerManagerAction(_args(action, apply=apply))
    selected.target = _target()
    selected.gitlab_selection = ACTION.select_gitlab_reference(
        TARGET.GitLabOperationTarget(
            selected.target.gitlab, selected.target.source_file
        ),
        project=selected.args.project,
        group=selected.args.group,
    )
    selected.explicit_tags = tuple(selected.args.tag)
    selected.resolved_description = ACTION.ResolvedName(
        selected.args.description, ACTION.NameSource.EXPLICIT
    )
    selected.candidate_scope = RUN_SCOPE.RunScope(RUN_ID, NAMESPACE, False)
    if action == "deploy":
        selected.podman_runtime = ACTION.PodmanRuntimeSettings(
            node_name=selected.args.node
        )
    selected.runner_token = (
        TOKEN.BoundRunnerToken(Path("/unit/runner.token"), TOKEN_DIGEST, PRIVATE_TOKEN)
        if action == "deploy"
        else TOKEN.NoRunnerToken()
    )
    return selected


def _bind_gitlab(
    action: ACTION.GitLabRunnerManagerAction,
) -> ACTION.GitLabRunnerManagerState:
    """Bind finite GitLab service and scope state without provider access.

    :param action: Manager action whose state receives exact provider identity.
    :returns: The action's shared manager state.
    """
    state = action.context
    session = SimpleNamespace(
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/project",
        credential_digest="sha256:" + "d" * 64,
    )
    state.gitlab = SimpleNamespace(api=object(), session=session, target_id=42)
    state.configured_session = session
    state.run_scope = action.candidate_scope
    return state


def test_token_binding_hashes_shared_reader_value_without_exposing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep private token bytes out of documents, equality, and representations.

    :param monkeypatch: Pytest patch helper for the shared token reader.
    :returns: None; assertions validate the credential boundary.
    """
    path = Path("/unit/runner.token")
    monkeypatch.setattr(TOKEN, "read_token_file", lambda selected: PRIVATE_TOKEN)

    bound = TOKEN.read_runner_token(path)

    assert bound.path == path
    assert bound.digest == TOKEN_DIGEST
    assert bound == TOKEN.BoundRunnerToken(path, TOKEN_DIGEST, "another-private-value")
    assert bound.document() == {
        "required": True,
        "source": f"file:{path}",
        "digest": TOKEN_DIGEST,
        "state": "bound",
    }
    assert PRIVATE_TOKEN not in repr(bound)
    assert PRIVATE_TOKEN not in json.dumps(bound.document())


def test_offline_prepare_defers_provider_and_runner_token_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prepare an offline plan without binding providers or reading the token.

    :param monkeypatch: Pytest patch helper for provider and token boundaries.
    :returns: None; assertions prove offline preparation is noncredentialed.
    """
    action = ACTION.GitLabRunnerManagerAction(_args(dry_run=True))
    calls: list[tuple[bool, tuple[str, ...]]] = []

    def resolve_target(
        target: str,
        binding: Optional[str],
        *,
        dry_run: bool,
        required_surfaces: Sequence[str],
    ) -> TARGET.Target:
        """Record offline target selection without touching a provider.

        :param target: Explicit target path selected by the action.
        :param binding: Optional project binding path.
        :param dry_run: Expected offline selection mode.
        :param required_surfaces: Provider declarations required by deploy.
        :returns: Finite unit target.
        """
        assert target == "/unit/target.toml"
        assert binding is None
        calls.append((dry_run, tuple(required_surfaces)))
        return _target()

    def unexpected(*args: object, **kwargs: object) -> None:
        """Fail if offline preparation crosses a credential boundary.

        :param *args: Unexpected positional arguments.
        :param **kwargs: Unexpected keyword arguments.
        :raises AssertionError: Always; the boundary must remain unused.
        """
        raise AssertionError("offline preparation read a credential")

    monkeypatch.setattr(action, "_validate", lambda: None)
    monkeypatch.setattr(ACTION, "resolve_target", resolve_target)
    monkeypatch.setattr(ACTION, "bind_sources", unexpected)
    monkeypatch.setattr(ACTION, "read_runner_token", unexpected)

    action.prepare()

    assert calls == [(True, ("gitlab", "kubernetes", "harbor"))]
    assert isinstance(action.runner_token, TOKEN.DeferredRunnerToken)
    assert action.runner_token.path == Path("/unit/runner.token")
    assert action.candidate_scope == RUN_SCOPE.RunScope(RUN_ID, NAMESPACE, False)


def test_manager_workload_binds_scope_node_resources_and_ownership() -> None:
    """Render exact run, node, Podman resources, and ownership into the plan.

    :returns: None; assertions validate manager-specific shared runner inputs.
    """
    action = _action()
    document = action.workload().document(action.candidate_scope)

    assert document == {
        "runner": {
            "namespace": NAMESPACE,
            "name": action.candidate_scope.name(ACTION.MANAGER_ROLE),
            "source_root": "",
            "labels": list(RUNNER_LABELS),
            "timeout": 30,
            "ownership_id": RUN_ID,
        },
        "security_constraint": "privileged",
        "podman": {
            "node": "worker-ci13",
            "service_account": "default",
            "privileged": True,
            "automount_service_account_token": False,
            "resources": {
                "cpu_request": "250m",
                "cpu_limit": "1",
                "memory_request": "256Mi",
                "memory_limit": "1Gi",
                "storage_size": "8Gi",
            },
            "storage": {
                "graph_root": "/var/lib/containers",
                "run_root": "/run/containers",
            },
        },
    }


def test_fingerprint_binds_exact_target_runner_image_scope_and_token_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bind every mutation-sensitive input while excluding private token bytes.

    :param monkeypatch: Pytest patch helper for capturing the canonical document.
    :returns: None; assertions validate confirmation fingerprint inputs.
    """
    action = _action()
    state = _bind_gitlab(action)
    state.gitlab_runner = _runner_record(online=False)
    captured: list[Mapping[str, object]] = []

    def capture(document: Mapping[str, object]) -> str:
        """Capture one finite fingerprint document.

        :param document: Manager plan document supplied to the shared digest helper.
        :returns: Stable sentinel digest.
        """
        captured.append(document)
        return "sha256:captured-plan"

    monkeypatch.setattr(ACTION, "plan_digest", capture)

    assert action.fingerprint() == "sha256:captured-plan"
    assert len(captured) == 1
    plan = captured[0]
    assert plan["operation"] == "deploy"
    assert plan["image"] == IMAGE
    assert plan["run_scope"] == action.candidate_scope.as_dict()
    assert plan["protected"] is None
    assert plan["registration"] == action.runner_token.document()
    assert plan["gitlab"] == {
        "origin": "https://gitlab.example.test",
        "session": {
            "target_kind": "project",
            "target_reference": "team/project",
            "credential_digest": "sha256:" + "d" * 64,
        },
        "runner": {
            key: state.gitlab_runner[key]
            for key in ("id", "description", "runner_type", "tag_list")
        },
    }
    assert plan["workload"] == action.workload().document(action.candidate_scope)
    assert PRIVATE_TOKEN not in json.dumps(plan)


def test_observe_reads_exact_runner_scope_identity_and_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass the exact runner ID, scope, description, and tags to GitLab read-back.

    :param monkeypatch: Pytest patch helper for the exact runner reader.
    :returns: None; assertions validate provider lookup identity and evidence.
    """
    action = _action()
    state = _bind_gitlab(action)
    calls: list[tuple[object, ...]] = []

    def read_runner_record(
        api: object,
        session: object,
        target_kind: str,
        target_id: int,
        runner_id: int,
        *,
        description: str,
        expected_tags: Sequence[str],
        expected_protection: Optional[str] = None,
    ) -> CALLBACKS.RunnerRecord:
        """Record the manager's exact GitLab lookup.

        :param api: Bound API client identity.
        :param session: Bound private session identity.
        :param target_kind: Expected project scope.
        :param target_id: Expected numeric project ID.
        :param runner_id: Expected exact runner ID.
        :param description: Expected runner description.
        :param expected_tags: Expected complete tag identity.
        :param expected_protection: Omitted protection preserves the provider value.
        :returns: Verified offline runner record.
        """
        calls.append(
            (
                api,
                session,
                target_kind,
                target_id,
                runner_id,
                description,
                tuple(expected_tags),
                expected_protection,
            )
        )
        return _runner_record(online=False)

    monkeypatch.setattr(CALLBACKS, "read_runner_record", read_runner_record)
    callback = CALLBACKS.ObserveGitLabRunner(action)
    callback.bind(state)

    result = callback.run()

    assert calls == [
        (
            state.gitlab.api,
            state.gitlab.session,
            "project",
            42,
            RUNNER_ID,
            RUNNER_DESCRIPTION,
            RUNNER_LABELS,
            None,
        )
    ]
    assert result == {
        "verified": True,
        "run_id": RUN_ID,
        "scope": action.candidate_scope.as_dict(),
        **ACTION.runner_evidence(_runner_record(online=False)),
    }


@pytest.mark.parametrize("binding_owner", [None, "external-run", RUN_ID])
def test_online_verification_retains_only_verified_manager_owned_resources(
    monkeypatch: pytest.MonkeyPatch,
    binding_owner: Optional[str],
) -> None:
    """Retain long-lived resources only after exact online manager verification.

    :param monkeypatch: Pytest patch helper for the exact GitLab read-back.
    :param binding_owner: Missing, external, or this run's SCC binding owner.
    :returns: None; assertions validate verified retention boundaries.
    """
    action = _action()
    state = _bind_gitlab(action)
    scope = RUN_SCOPE.RunScope(RUN_ID, NAMESPACE, True)
    state.run_scope = scope
    runner = action.workload().runner(scope)
    state.deployment_result = {
        "verified": True,
        "namespace": NAMESPACE,
        "name": runner.name,
        "image": IMAGE,
    }
    binding = SCC_ACCESS.role_binding_document(action.scc_access(), scope)
    binding_name = str(binding["metadata"]["name"])

    class Kubernetes:
        """Return one optional SCC binding without mutation facilities."""

        def read(self, kind: str, name: str, namespace: str) -> Optional[dict]:
            """Read the deterministic SCC binding selected by verification.

            :param kind: Expected RoleBinding kind.
            :param name: Expected deterministic binding name.
            :param namespace: Expected manager namespace.
            :returns: Missing, externally owned, or run-owned binding document.
            """
            assert (kind, name, namespace) == (
                "RoleBinding",
                binding_name,
                NAMESPACE,
            )
            if binding_owner is None:
                return None
            return {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": {
                    "name": binding_name,
                    "namespace": NAMESPACE,
                    "labels": {
                        RESOURCES.MANAGER_LABEL: RESOURCES.MANAGER_VALUE,
                        RESOURCES.OPERATION_LABEL: binding_owner,
                    },
                },
            }

    state.kubernetes = Kubernetes()
    monkeypatch.setattr(
        CALLBACKS,
        "read_runner_record",
        lambda *args, **kwargs: _runner_record(online=True),
    )
    callback = CALLBACKS.VerifyGitLabRunnerOnline(action)
    callback.bind(state)

    result = callback.run()

    expected = {
        RUN_SCOPE.resource_identity("Namespace", NAMESPACE, ""),
        RUN_SCOPE.resource_identity("Deployment", runner.name, NAMESPACE),
        RUN_SCOPE.resource_identity(
            "Secret", runner.name + K8S_RUNNER.REGISTRATION_SECRET_SUFFIX, NAMESPACE
        ),
        RUN_SCOPE.resource_identity(
            "Secret", runner.name + K8S_RUNNER.REGISTRY_SECRET_SUFFIX, NAMESPACE
        ),
    }
    if binding_owner == RUN_ID:
        expected.add(
            RUN_SCOPE.resource_identity("RoleBinding", binding_name, NAMESPACE)
        )
    assert state.retained_resources == expected
    assert result["workload"] == state.deployment_result
    assert result["online"] is True
    assert result["status"] == "online"
    assert result["contacted_at"]


def test_failed_online_verification_leaves_all_resources_cleanup_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not retain manager resources when online read-back times out.

    :param monkeypatch: Pytest patch helper for provider time and read-back.
    :returns: None; assertions validate failure cleanup eligibility.
    """
    action = _action()
    action.args.timeout = 1
    state = _bind_gitlab(action)
    times = iter((0.0, 2.0))
    monkeypatch.setattr(
        CALLBACKS,
        "read_runner_record",
        lambda *args, **kwargs: _runner_record(online=False),
    )
    monkeypatch.setattr(CALLBACKS.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        CALLBACKS.time,
        "sleep",
        lambda seconds: pytest.fail(f"unexpected sleep: {seconds}"),
    )
    callback = CALLBACKS.VerifyGitLabRunnerOnline(action)
    callback.bind(state)

    with pytest.raises(CALLBACKS.ActionError, match="runner_online_readback_timeout"):
        callback.run()

    assert state.retained_resources == set()


def test_delete_verification_preserves_runner_and_reports_current_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report the preserved GitLab record without inferring an offline state.

    :param monkeypatch: Pytest patch helper for the exact GitLab read-back.
    :returns: None; assertions validate preservation and observed status evidence.
    """
    action = _action("delete", apply=True)
    state = _bind_gitlab(action)
    observed = _runner_record(online=True)
    monkeypatch.setattr(
        CALLBACKS,
        "read_runner_record",
        lambda *args, **kwargs: observed,
    )
    callback = CALLBACKS.VerifyGitLabRunnerPreserved(action)
    callback.bind(state)

    result = callback.run()

    assert state.gitlab_runner == observed
    assert result == {
        "verified": True,
        "run_id": RUN_ID,
        "runner_preserved": True,
        "current_status": ACTION.runner_evidence(observed),
    }


def test_entered_manager_workload_delegates_failure_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delegate failed manager recovery to the entered shared runner callback.

    :param monkeypatch: Pytest patch helper for the shared callback boundary.
    :returns: None; assertions validate failure cleanup delegation.
    """
    action = _action()
    state = _bind_gitlab(action)
    events: list[str] = []

    class Child:
        """Record manager delegation without repeating shared runner behavior."""

        def __init__(self, runner: object) -> None:
            """Retain the derived shared runner action.

            :param runner: Run-scoped manager workload inputs.
            """
            self.action = runner

        def bind(self, selected: object) -> None:
            """Retain the shared workflow state.

            :param selected: Manager state supplied by the wrapper.
            :returns: None after binding the state.
            """
            self.state = selected

        def run(self) -> dict[str, bool]:
            """Record entry into the shared runner operation.

            :returns: Finite entered-state evidence.
            """
            events.append("run")
            return {"entered": True}

        def cleanup(self) -> dict[str, bool]:
            """Require failed state and record delegated recovery.

            :returns: Finite cleanup evidence.
            """
            assert self.state.failed is True
            events.append("cleanup")
            return {"recovered": True}

    monkeypatch.setattr(WORKLOAD.DeployManagerRunner, "child_type", Child)
    callback = WORKLOAD.DeployManagerRunner(action.workload())
    callback.bind(state)

    assert callback.run() == {"entered": True}
    state.failed = True
    assert callback.cleanup() == {"recovered": True}
    assert callback.child.action.ownership_id == RUN_ID
    assert events == ["run", "cleanup"]


def test_delete_targets_only_run_owned_manager_identities() -> None:
    """Delete the exact manager resources through the run ownership boundary.

    :returns: None; assertions validate resource names and absence-verifying calls.
    """
    action = _action("delete", apply=True)
    state = _bind_gitlab(action)
    runner = action.workload().runner(state.run_scope)
    names = (
        ("Deployment", runner.name),
        ("Secret", runner.name + K8S_RUNNER.REGISTRATION_SECRET_SUFFIX),
        ("Secret", runner.name + K8S_RUNNER.REGISTRY_SECRET_SUFFIX),
    )

    class Kubernetes:
        """Model a Kubernetes service whose delete verifies object absence."""

        def __init__(self) -> None:
            """Create exact run-owned objects and an empty mutation journal."""
            self.objects = {
                key: {
                    "apiVersion": "v1",
                    "kind": key[0],
                    "metadata": {
                        "name": key[1],
                        "namespace": NAMESPACE,
                        "labels": {
                            RESOURCES.MANAGER_LABEL: RESOURCES.MANAGER_VALUE,
                            RESOURCES.OPERATION_LABEL: RUN_ID,
                        },
                    },
                }
                for key in names
            }
            self.deleted: list[tuple[str, str, str]] = []

        def read(self, kind: str, name: str, namespace: str) -> Optional[dict]:
            """Return exact resource state before and after deletion.

            :param kind: Native resource kind.
            :param name: Exact manager resource name.
            :param namespace: Exact manager namespace.
            :returns: Current object or ``None`` after verified deletion.
            """
            assert namespace == NAMESPACE
            return self.objects.get((kind, name))

        def delete(self, kind: str, name: str, namespace: str) -> None:
            """Delete one object and independently assert its absence.

            :param kind: Native resource kind.
            :param name: Exact manager resource name.
            :param namespace: Exact manager namespace.
            :returns: None after absence read-back.
            """
            assert self.objects.pop((kind, name), None) is not None
            self.deleted.append((kind, name, namespace))
            assert self.read(kind, name, namespace) is None

    state.kubernetes = Kubernetes()
    callback = WORKLOAD.DeleteManagerRunner(action.workload())
    callback.bind(state)

    result = callback.run()

    assert callback.child.action.ownership_id == RUN_ID
    assert state.kubernetes.deleted == [(kind, name, NAMESPACE) for kind, name in names]
    assert result == {
        "action": "APPLIED",
        "verified": True,
        "namespace": NAMESPACE,
        "name": runner.name,
    }


def test_remove_scc_access_preserves_preexisting_external_binding() -> None:
    """Leave an unowned SCC RoleBinding untouched during manager deletion.

    :returns: None; assertions validate external permission preservation.
    """
    action = _action("delete", apply=True)
    state = _bind_gitlab(action)
    scope = state.run_scope
    document = SCC_ACCESS.role_binding_document(action.scc_access(), scope)
    binding_name = str(document["metadata"]["name"])
    external = {
        **document,
        "metadata": {
            **document["metadata"],
            "labels": {
                RESOURCES.MANAGER_LABEL: RESOURCES.MANAGER_VALUE,
                RESOURCES.OPERATION_LABEL: "external-run",
            },
        },
    }

    class Kubernetes:
        """Expose one external binding and reject every mutation attempt."""

        def read(self, kind: str, name: str, namespace: str) -> dict:
            """Return the preexisting binding selected by cleanup.

            :param kind: Expected RoleBinding kind.
            :param name: Expected deterministic binding name.
            :param namespace: Expected manager namespace.
            :returns: Externally owned binding.
            """
            assert (kind, name, namespace) == (
                "RoleBinding",
                binding_name,
                NAMESPACE,
            )
            return external

        def delete(self, kind: str, name: str, namespace: str) -> None:
            """Fail if cleanup attempts to delete the external binding.

            :param kind: Unexpected resource kind.
            :param name: Unexpected resource name.
            :param namespace: Unexpected resource namespace.
            :raises AssertionError: Always; external grants must be preserved.
            """
            raise AssertionError(f"deleted external {kind}/{namespace}/{name}")

    state.kubernetes = Kubernetes()
    callback = SCC_ACCESS.RemoveSCCAccess(action.scc_access())
    callback.bind(state)

    result = callback.run()

    assert result == {
        "action": "NO_OP",
        "verified": True,
        "resource": {
            "kind": "RoleBinding",
            "name": binding_name,
            "namespace": NAMESPACE,
            "owned": False,
            "preserved": True,
        },
    }


def test_delete_scope_preserves_caller_namespace_without_provider_mutation() -> None:
    """Select and preserve a caller-owned namespace during manager deletion.

    :returns: None; assertions validate namespace ownership and cleanup evidence.
    """
    action = _action("delete", apply=True)
    state = action.context
    scope = RUN_SCOPE.RunScope(RUN_ID, NAMESPACE, False)
    state.found_scopes = (scope,)
    state.resolved_run_id = RUN_ID

    class Kubernetes:
        """Read only the selected namespace and potential existing manager."""

        def read(self, kind: str, name: str, namespace: str) -> Optional[dict]:
            """Supply independent namespace evidence and absence of a manager.

            :param kind: Selected Namespace or candidate Deployment kind.
            :param name: Exact selected namespace or existing manager name.
            :param namespace: Cluster scope or selected manager namespace.
            :returns: Borrowed namespace with a UID, or an absent Deployment.
            :raises AssertionError: An unrelated object is read.
            """
            if kind == "Deployment":
                assert (name, namespace) == (
                    scope.name(ACTION.MANAGER_ROLE),
                    NAMESPACE,
                )
                return None
            assert (kind, name, namespace) == ("Namespace", NAMESPACE, "")
            return {
                "kind": "Namespace",
                "metadata": {"name": NAMESPACE, "uid": NAMESPACE_UID, "labels": {}},
            }

        def delete(self, kind: str, name: str, namespace: str) -> None:
            """Fail if caller-owned namespace cleanup performs a deletion.

            :param kind: Unexpected native kind.
            :param name: Unexpected object name.
            :param namespace: Unexpected namespace.
            :raises AssertionError: Always; caller namespaces are preserved.
            """
            raise AssertionError(f"unexpected delete {kind}/{namespace}/{name}")

    state.kubernetes = Kubernetes()
    select = WORKLOAD.SelectResolvedManagerScope(action.workload())
    select.bind(state)
    selected = select.run()
    remove = RUN_SCOPE.RemoveScope(RUN_SCOPE.ResolveRunScopeAction(RUN_ID))
    remove.bind(state)

    result = remove.run()

    assert selected == {"verified": True, **scope.as_dict()}
    assert state.run_scope == scope
    assert result == {
        "action": "NO_OP",
        "verified": True,
        "run_id": RUN_ID,
        "resources": [],
        "preserved_scopes": [scope.as_dict()],
    }


@pytest.mark.parametrize(
    ("action_name", "apply", "expected"),
    [
        (
            "deploy",
            False,
            (
                "K8SAuth",
                "GitLabManagerAuth",
                "HarborAuth",
                "GuardGitLabRunnerManager",
                "ResolveRunScopes",
                "SelectManagerPlanScope",
                "ObserveGitLabRunner",
                "ConfirmWorkflow",
            ),
        ),
        (
            "deploy",
            True,
            (
                "K8SAuth",
                "GitLabManagerAuth",
                "HarborAuth",
                "GuardGitLabRunnerManager",
                "ResolveRunScopes",
                "SelectManagerPlanScope",
                "ObserveGitLabRunner",
                "ConfirmWorkflow",
                "OpenManagerRunScope",
                "EnsureSCCAccess",
                "PullManagerImage",
                "GitLabRunnerRuntime",
                "DeployManagerRunner",
                "VerifyGitLabRunnerOnline",
            ),
        ),
        (
            "delete",
            True,
            (
                "K8SAuth",
                "GitLabManagerAuth",
                "GuardGitLabRunnerManager",
                "ResolveRunScopes",
                "SelectResolvedManagerScope",
                "ObserveGitLabRunner",
                "ConfirmWorkflow",
                "DeleteManagerRunner",
                "VerifyGitLabRunnerPreserved",
                "RemoveSCCAccess",
                "RemoveScope",
                "ObserveManagerNamespace",
            ),
        ),
    ],
)
def test_workflow_confirms_exact_plan_before_any_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    action_name: str,
    apply: bool,
    expected: Sequence[str],
) -> None:
    """Keep confirmation before mutation and deletion outside GitLab registration.

    :param monkeypatch: Fixture binding the existing offline provider doubles.
    :param tmp_path: Isolated runner-token and lock directory for the harness.
    :param action_name: Manager deploy or delete action.
    :param apply: Whether mutation callbacks are requested.
    :param expected: Exact callback order for the selected mode.
    :returns: None; assertions validate workflow ordering and provider boundaries.
    """
    _manager_cli_harness(monkeypatch, tmp_path)
    action = _action(action_name, apply=apply)
    executor = SCRIPT.workflow(action)
    callbacks = tuple(type(callback).__name__ for callback in executor.callbacks)

    assert callbacks == tuple(expected)
    assert callbacks.index("ConfirmWorkflow") < len(callbacks)
    mutating = {
        "OpenManagerRunScope",
        "EnsureSCCAccess",
        "PullManagerImage",
        "GitLabRunnerRuntime",
        "DeployManagerRunner",
        "DeleteManagerRunner",
        "RemoveSCCAccess",
        "RemoveScope",
    }
    assert not any(
        name in mutating for name in callbacks[: callbacks.index("ConfirmWorkflow")]
    )
    if action_name == "delete" and apply:
        assert all("GitLabRunnerDelete" not in name for name in callbacks)
        verify = next(
            callback
            for callback in executor.callbacks
            if isinstance(callback, CALLBACKS.VerifyGitLabRunnerPreserved)
        )
        assert verify.workflow is action


class ManagerCluster(MemoryCluster):
    """Extend the existing in-memory Kubernetes transport for manager workloads."""

    def __init__(self) -> None:
        super().__init__()
        self.target = replace(_target(), skill=None)
        self.runner_online = False
        self.fault = ""
        self.guard_held = False
        self.guard_checks: list[bool] = []
        self.events: list[str] = []
        self.runner_reads = 0
        self.observed_runner_tags = RUNNER_LABELS
        self.token_path: Path
        self.caller_namespace = ""

    def apply(self, document: dict) -> dict:
        self.guard_checks.append(self.guard_held)
        self.events.append("apply:" + document["kind"].casefold())
        return super().apply(document)

    def delete(
        self,
        kind: str,
        name: str,
        namespace: str,
        *,
        expected_uid: Optional[str] = None,
    ) -> None:
        self.guard_checks.append(self.guard_held)
        self.events.append("delete:" + kind.casefold())
        if self.fault == "cleanup" and kind.casefold() == "job":
            raise KUBERNETES.KubernetesError("unit_cleanup_denied")
        super().delete(kind, name, namespace, expected_uid=expected_uid)
        if kind.casefold() == "deployment":
            self.runner_online = False

    def command(self, *args: str, openshift: bool = False) -> RUNTIME.CommandResult:
        assert args == ("get", "--raw=/apis/security.openshift.io/v1")
        return RUNTIME.CommandResult(args, 1, "", "404 Not Found")

    def json(self, *args: str) -> dict:
        self.guard_checks.append(self.guard_held)
        if args[0] == "get":
            kind = {
                "pods": "pod",
                "namespaces": "namespace",
                "configmaps": "configmap",
                "deployments": "deployment",
                "rolebindings": "rolebinding",
            }[args[1]]
            selector = dict(
                item.split("=", 1) for item in args[args.index("-l") + 1].split(",")
            )
            self.events.append("lookup:" + kind)
            return {
                "items": [
                    deepcopy(document)
                    for (resource_kind, _, _), document in self.objects.items()
                    if resource_kind == kind
                    and all(
                        document["metadata"].get("labels", {}).get(key) == value
                        for key, value in selector.items()
                    )
                ]
            }
        assert args[:4] == ("-n", args[1], "get", "pods")
        namespace = args[1]
        selector = args[args.index("-l") + 1]
        label, name = selector.split("=", 1)
        kind = "job" if label == "job-name" else "deployment"
        workload = self.read(kind, name, namespace)
        assert workload is not None
        pod = deepcopy(workload["spec"]["template"])
        pod["metadata"]["name"] = name + "-pod"
        pod["spec"]["nodeName"] = "worker-ci13"
        pod["status"] = {
            "containerStatuses": [
                {
                    "name": "runner",
                    "imageID": IMAGE if self.fault != "image" else "sha256:" + "0" * 64,
                    "state": {"terminated": {"exitCode": 0}}
                    if kind == "job"
                    else {"running": {"startedAt": "2026-10-18T00:00:00Z"}},
                }
            ]
        }
        return {"items": [pod]}

    def wait_job(self, name: str, namespace: str) -> None:
        assert self.read("job", name, namespace) is not None
        self.events.append("wait:job")
        if self.fault == "token":
            self.token_path.write_text(CHANGED_PRIVATE_TOKEN, encoding="utf-8")
        elif self.fault in {"error", "timeout", "cleanup"}:
            reason = (
                "timeout"
                if self.fault == "timeout"
                else "unit_command_failed token=" + PRIVATE_TOKEN
            )
            raise KUBERNETES.KubernetesError(reason)
        elif self.fault in {"term", "int"}:
            signal.raise_signal(
                signal.SIGTERM if self.fault == "term" else signal.SIGINT
            )

    def wait_rollout(self, kind: str, name: str, namespace: str) -> None:
        assert kind == "deployment"
        assert self.read(kind, name, namespace) is not None
        self.runner_online = True


def _manager_cli_harness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> ManagerCluster:
    """Bind finite provider transports below the real parser and callback lifecycle."""
    cluster = ManagerCluster()
    cluster.token_path = tmp_path / "runner.token"
    cluster.token_path.write_text(PRIVATE_TOKEN, encoding="utf-8")
    cluster.token_path.chmod(0o600)
    session = SimpleNamespace(
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/project",
        credential_digest="sha256:" + "d" * 64,
    )

    def k8s_config(callback: KUBERNETES.K8SAuth) -> dict:
        callback.state.kubernetes_target = cluster.target
        return {}

    def k8s_auth(callback: KUBERNETES.K8SAuth) -> dict:
        callback.state.kubernetes = cluster
        callback.state.access_evidence["kubernetes"] = {"identity": "unit-user"}
        return {"identity": "unit-user"}

    def gitlab_config(callback: CALLBACKS.GitLabManagerAuth) -> dict:
        callback.state.configured_session = session
        return {}

    def gitlab_auth(callback: CALLBACKS.GitLabManagerAuth) -> dict:
        callback.state.gitlab = SimpleNamespace(
            api=object(), session=session, target_id=42
        )
        callback.state.access_evidence["gitlab"] = {"identity": "unit-user"}
        return {"identity": "unit-user"}

    def harbor_config(callback: SCRIPT.HarborAuth) -> dict:
        callback.state.harbor_target = cluster.target.harbor
        callback.state.harbor_credential = object()
        return {}

    def harbor_auth(callback: SCRIPT.HarborAuth) -> dict:
        callback.state.harbor = SimpleNamespace(dockerconfig=b'{"auths":{}}')
        callback.state.access_evidence["harbor"] = {"identity": "unit-user"}
        return {"identity": "unit-user"}

    def runner_read(
        api: object,
        bound: object,
        target_kind: str,
        target_id: int,
        runner_id: int,
        *,
        description: str,
        expected_tags: Optional[Sequence[str]],
        expected_protection: Optional[str] = None,
    ) -> dict:
        assert (bound, target_kind, target_id, runner_id, description) == (
            session,
            "project",
            42,
            RUNNER_ID,
            RUNNER_DESCRIPTION,
        )
        if expected_tags is not None:
            assert tuple(expected_tags) == tuple(cluster.observed_runner_tags)
        assert expected_protection is None
        cluster.runner_reads += 1
        record = _runner_record(online=cluster.runner_online)
        record[FIELD_TAGS] = list(cluster.observed_runner_tags)
        return record

    @contextmanager
    def guard(origin: str, runner_id: int) -> Iterator[None]:
        assert not cluster.guard_held
        with GITLAB_API.runner_update_guard(origin, runner_id):
            cluster.guard_held = True
            cluster.events.append("guard:acquire")
            try:
                yield
            finally:
                cluster.events.append("guard:release")
                cluster.guard_held = False

    monkeypatch.setattr(
        ACTION, "resolve_target", lambda *_args, **_kwargs: cluster.target
    )
    monkeypatch.setattr(ACTION, "bind_sources", lambda target, **_kwargs: target)
    monkeypatch.setattr(KUBERNETES.K8SAuth, "read_config", k8s_config)
    monkeypatch.setattr(KUBERNETES.K8SAuth, "run", k8s_auth)
    monkeypatch.setattr(CALLBACKS.GitLabManagerAuth, "read_config", gitlab_config)
    monkeypatch.setattr(CALLBACKS.GitLabManagerAuth, "run", gitlab_auth)
    monkeypatch.setattr(SCRIPT.HarborAuth, "read_config", harbor_config)
    monkeypatch.setattr(SCRIPT.HarborAuth, "run", harbor_auth)
    monkeypatch.setattr(CALLBACKS, "read_runner_record", runner_read)
    monkeypatch.setattr(CALLBACKS, "runner_update_guard", guard, raising=False)
    monkeypatch.setattr(GITLAB_API, "_lock_root", lambda: tmp_path / "locks")
    return cluster


def _manager_cli_arguments(
    cluster: ManagerCluster,
    *,
    action: str = "deploy",
    run_id: Optional[str] = None,
    flags: Sequence[str] = (),
    tag_option: str = "--tag",
) -> list[str]:
    """Build public arguments without bypassing command preparation or confirmation."""
    args = [
        action,
        "--runner-id",
        str(RUNNER_ID),
        "--description",
        RUNNER_DESCRIPTION,
        "--project",
        "team/project",
        "--binding",
        "/unit/target.toml",
        "--json",
        tag_option,
        RUNNER_LABELS[0],
        tag_option,
        RUNNER_LABELS[1],
        "--timeout",
        "30",
    ]
    if action == "deploy":
        args.extend(
            (
                "--image",
                IMAGE,
                "--node",
                "worker-ci13",
                "--token-file",
                str(cluster.token_path),
            )
        )
    if run_id is not None:
        args.extend(("--run-id", run_id))
    if cluster.caller_namespace:
        args.extend(("--namespace", cluster.caller_namespace))
    args.extend(flags)
    return args


def _manager_cli_report(
    cluster: ManagerCluster,
    capsys: pytest.CaptureFixture[str],
    *,
    action: str = "deploy",
    run_id: Optional[str] = None,
    flags: Sequence[str] = (),
    tag_option: str = "--tag",
) -> tuple[int, dict]:
    """Decode and validate the actual command report using the installed result schema."""
    code = SCRIPT.main(
        _manager_cli_arguments(
            cluster, action=action, run_id=run_id, flags=flags, tag_option=tag_option
        )
    )
    captured = capsys.readouterr()
    assert PRIVATE_TOKEN not in captured.out + captured.err
    assert CHANGED_PRIVATE_TOKEN not in captured.out + captured.err
    result = json.loads(captured.out)
    SCHEMA.validate_specification(result, "results/toolbox-workflow.schema.json")
    return code, result


def _confirmed_manager(
    cluster: ManagerCluster,
    capsys: pytest.CaptureFixture[str],
    *,
    action: str = "deploy",
    run_id: Optional[str] = None,
) -> tuple[int, dict]:
    """Obtain the real live plan and confirm its exact fingerprint through main."""
    code, plan = _manager_cli_report(cluster, capsys, action=action, run_id=run_id)
    assert (code, plan["status"]) == (0, "PLANNED")
    return _manager_cli_report(
        cluster,
        capsys,
        action=action,
        run_id=run_id,
        flags=("--apply", "--confirm-plan", plan["plan_digest"]),
    )


@pytest.mark.parametrize("mode", ("offline", "live-plan", "unconfirmed", "mismatch"))
def test_manager_main_plans_and_confirmation_refusal_never_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    """Actual public plans and denied applies leave every provider object untouched."""
    cluster = _manager_cli_harness(monkeypatch, tmp_path)
    flags = {
        "offline": ("--dry-run",),
        "live-plan": (),
        "unconfirmed": ("--apply",),
        "mismatch": ("--apply", "--confirm-plan", "0" * 64),
    }[mode]
    code, result = _manager_cli_report(cluster, capsys, flags=flags)
    assert (
        result["status"]
        == {
            "offline": "DRY_RUN",
            "live-plan": "PLANNED",
            "unconfirmed": "BLOCKED",
            "mismatch": "BLOCKED",
        }[mode]
    )
    assert code == (2 if mode in {"unconfirmed", "mismatch"} else 0)
    assert cluster.applied == cluster.deleted == []
    assert cluster.objects == {}
    assert cluster.runner_reads == (0 if mode == "offline" else 1)
    assert not cluster.guard_held


def test_manager_label_alias_plans_the_same_tags_as_tag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep --label working: it plans the same tags and fingerprint as --tag.

    Callers written before --tag matched gitlab_runner.py create still pass --label.
    """
    cluster = _manager_cli_harness(monkeypatch, tmp_path)
    tag_code, tag_plan = _manager_cli_report(cluster, capsys, tag_option="--tag")
    label_code, label_plan = _manager_cli_report(cluster, capsys, tag_option="--label")

    assert (tag_code, tag_plan["status"]) == (0, "PLANNED")
    assert (label_code, label_plan["status"]) == (0, "PLANNED")
    assert label_plan["plan_digest"] == tag_plan["plan_digest"]
    assert cluster.applied == cluster.deleted == []


@pytest.mark.parametrize("empty_tags", (False, True))
def test_manager_deploy_without_tags_preserves_observed_provider_tags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    empty_tags: bool,
) -> None:
    """Preserve omitted native tags through planning and confirmed manager rollout.

    :param monkeypatch: Bind the existing isolated provider lifecycle harness.
    :param tmp_path: Test-owned token and lock directory.
    :param capsys: Capture the real command's machine-readable report.
    :param empty_tags: Whether native runner read-back contains no tags.
    """
    cluster = _manager_cli_harness(monkeypatch, tmp_path)
    runner_read = Mock(wraps=CALLBACKS.read_runner_record)
    monkeypatch.setattr(CALLBACKS, RUNNER_READ_ATTRIBUTE, runner_read)
    observed_tags = () if empty_tags else tuple(uuid4().hex for _ in RUNNER_LABELS)
    cluster.observed_runner_tags = observed_tags
    selected_run_id = uuid4().hex
    original_fingerprint = ACTION.GitLabRunnerManagerAction.fingerprint
    resolved_tags: list[tuple[str, ...]] = []
    observed_actions: list[ACTION.GitLabRunnerManagerAction] = []

    def fingerprint(action: ACTION.GitLabRunnerManagerAction) -> str:
        """Retain the real post-observation labels before normal confirmation hashing.

        :param action: Prepared action after independent provider tag observation.
        :returns: Original confirmation digest without replacing its computation.
        """
        resolved_tags.append(action.labels)
        observed_actions.append(action)
        return original_fingerprint(action)

    monkeypatch.setattr(
        ACTION.GitLabRunnerManagerAction, original_fingerprint.__name__, fingerprint
    )
    arguments = [
        value
        for value in _manager_cli_arguments(cluster, run_id=selected_run_id)
        if value not in {TAG_ARGUMENT, *RUNNER_LABELS}
    ]

    code = SCRIPT.main(arguments)

    result = json.loads(capsys.readouterr().out)
    assert code == STATUS.exit_code(STATUS.PLANNED)
    assert result[FIELD_STATUS] == STATUS.PLANNED
    observations = [
        item
        for item in result[FIELD_RECORDS]
        if item.get(FIELD_CALLBACK) == CALLBACKS.ObserveGitLabRunner.__name__
    ]
    assert [item[FIELD_TAGS] for item in observations] == [list(observed_tags)]
    assert resolved_tags == [observed_tags]
    assert cluster.runner_reads == len(observations)
    assert cluster.applied == cluster.deleted == []

    code = SCRIPT.main(
        [*arguments, APPLY_ARGUMENT, CONFIRM_ARGUMENT, result[FIELD_PLAN_DIGEST]]
    )
    applied = json.loads(capsys.readouterr().out)
    assert code == STATUS.exit_code(STATUS.PASS)
    assert applied[FIELD_STATUS] == STATUS.PASS
    online = [
        item
        for item in applied[FIELD_RECORDS]
        if item.get(FIELD_CALLBACK) == CALLBACKS.VerifyGitLabRunnerOnline.__name__
    ]
    assert [item[FIELD_TAGS] for item in online] == [list(observed_tags)]
    assert runner_read.call_args.kwargs[EXPECTED_TAGS_KEYWORD] == observed_tags
    assert resolved_tags == [observed_tags] * len((result, applied))
    assert all(action.labels == observed_tags for action in observed_actions)
    assert cluster.applied
    provider_callbacks = {
        CALLBACKS.ObserveGitLabRunner.__name__,
        CALLBACKS.VerifyGitLabRunnerOnline.__name__,
    }
    assert cluster.runner_reads == len(
        [
            item
            for report in (result, applied)
            for item in report[FIELD_RECORDS]
            if item.get(FIELD_CALLBACK) in provider_callbacks
        ]
    )


def test_manager_parser_declares_tag_with_label_alias() -> None:
    """Parse repeated --tag and --label into one ordered tag list."""
    parsed = SCRIPT.build_parser().parse_args(
        ["deploy", "--tag", "first", "--label", "second", "--tag", "third"]
    )

    assert parsed.tag == ["first", "second", "third"]
    assert not hasattr(parsed, "label")


@pytest.mark.parametrize("run_id", (None, "explicit-manager-run"))
def test_manager_main_repeat_keeps_identity_plan_and_deployment_no_op(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_id: Optional[str],
) -> None:
    """Omitted or explicit identities reuse the observed manager on a second apply."""
    cluster = _manager_cli_harness(monkeypatch, tmp_path)
    code, first = _confirmed_manager(cluster, capsys, run_id=run_id)
    assert (code, first["status"]) == (0, "PASS")
    first_deploy = next(
        item for item in first["records"] if item["callback"] == "DeployManagerRunner"
    )
    first_scope = next(
        item for item in first["records"] if item["callback"] == "OpenManagerRunScope"
    )
    if run_id is not None:
        assert first_scope["run_id"] == run_id
    before = deepcopy(cluster.objects)
    cluster.applied.clear()
    code, repeated = _confirmed_manager(cluster, capsys, run_id=run_id)
    assert (code, repeated["status"]) == (0, "PASS")
    repeat_deploy = next(
        item
        for item in repeated["records"]
        if item["callback"] == "DeployManagerRunner"
    )
    repeat_scope = next(
        item
        for item in repeated["records"]
        if item["callback"] == "OpenManagerRunScope"
    )
    assert repeated["plan_digest"] == first["plan_digest"]
    assert repeat_deploy["action"] == "NO_OP"
    assert repeat_deploy["namespace"] == first_deploy["namespace"]
    assert repeat_scope["run_id"] == first_scope["run_id"]
    assert not any(
        kind in {"deployment", "namespace"} for kind, _, _ in cluster.applied
    )
    assert cluster.objects == before
    assert cluster.guard_checks and all(cluster.guard_checks)
    assert cluster.events[-1] == "guard:release"
    assert not cluster.guard_held


@pytest.mark.parametrize("fault", ("error", "timeout", "token", "image", "cleanup"))
def test_manager_main_failures_report_blocked_and_clean_only_owned_resources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    fault: str,
) -> None:
    """Native failures and changed token input produce schema-valid blocked evidence."""
    cluster = _manager_cli_harness(monkeypatch, tmp_path)
    cluster.fault = fault
    code, result = _confirmed_manager(cluster, capsys)
    assert (code, result["status"]) == (2, "BLOCKED")
    assert result["errors"]
    assert result["cleanup"]["status"] == ("BLOCKED" if fault == "cleanup" else "PASS")
    if fault == "error":
        assert "[REDACTED]" in result["errors"][0]["reason"]
    elif fault == "token":
        assert result["errors"][0]["reason"] == "runner_token_changed_rerun_plan"
        assert not any(kind == "deployment" for kind, _, _ in cluster.applied)
    elif fault == "image":
        assert result["errors"][0]["reason"] == "running_image_readback_missing"
    if fault != "cleanup":
        assert cluster.objects == {}
    else:
        assert result["cleanup"]["failures"]
        assert any(
            "unit_cleanup_denied" in item["reason"]
            for item in result["cleanup"]["failures"]
        )
    assert cluster.guard_checks and all(cluster.guard_checks)
    assert cluster.events[-1] == "guard:release"
    assert not cluster.guard_held


@pytest.mark.parametrize("fault", ("term", "int"))
def test_manager_main_signal_cleans_resources_releases_guard_and_restores_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    fault: str,
) -> None:
    """Real signal handling unwinds the entered callback chain before propagating exit."""
    cluster = _manager_cli_harness(monkeypatch, tmp_path)
    code, plan = _manager_cli_report(cluster, capsys)
    assert code == 0
    selected = signal.SIGTERM if fault == "term" else signal.SIGINT
    previous = signal.getsignal(selected)
    cluster.fault = fault
    with pytest.raises(SystemExit) as caught:
        SCRIPT.main(
            _manager_cli_arguments(
                cluster, flags=("--apply", "--confirm-plan", plan["plan_digest"])
            )
        )
    assert caught.value.code == 128 + selected
    assert signal.getsignal(selected) == previous
    assert cluster.objects == {}
    assert cluster.deleted
    assert cluster.guard_checks and all(cluster.guard_checks)
    assert cluster.events[-1] == "guard:release"
    assert not cluster.guard_held
    assert PRIVATE_TOKEN not in capsys.readouterr().out


def test_manager_main_delete_preserves_runner_caller_namespace_and_unowned_objects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Confirmed delete removes owned workload while leaving caller storage and GitLab intact."""
    cluster = _manager_cli_harness(monkeypatch, tmp_path)
    cluster.caller_namespace = NAMESPACE
    cluster.objects[("namespace", "", NAMESPACE)] = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": NAMESPACE, "uid": "caller"},
    }
    unrelated = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "caller-data", "namespace": NAMESPACE, "uid": "unowned"},
        "data": {"important": "preserve"},
    }
    cluster.objects[("configmap", NAMESPACE, "caller-data")] = unrelated
    code, deployed = _confirmed_manager(cluster, capsys, run_id=RUN_ID)
    assert code == 0
    code, removed = _confirmed_manager(cluster, capsys, action="delete", run_id=RUN_ID)
    assert (code, removed["status"]) == (0, "PASS")
    preserved = next(
        item
        for item in removed["records"]
        if item["callback"] == "VerifyGitLabRunnerPreserved"
    )
    assert preserved["runner_preserved"] is True
    assert preserved["current_status"]["id"] == RUNNER_ID
    assert cluster.objects[("configmap", NAMESPACE, "caller-data")] == unrelated
    assert ("namespace", "", NAMESPACE) in cluster.objects
    assert {kind for kind, _, _ in cluster.objects} == {"namespace", "configmap"}
    assert not any(kind == "namespace" for kind, _, _ in cluster.deleted)
    assert cluster.runner_reads >= 4
    assert removed["cleanup"] == {"status": "PASS", "failures": []}
    assert deployed["plan_digest"] != removed["plan_digest"]
