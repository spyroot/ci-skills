"""Focused unit contracts for the GitLab runner-manager workflow.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
from argparse import Namespace
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest
from conftest import import_script_module

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

PRIVATE_TOKEN = "glrt-private-unit-value"
TOKEN_DIGEST = "sha256:" + hashlib.sha256(PRIVATE_TOKEN.encode()).hexdigest()
RUN_ID = "manager-unit-run"
NAMESPACE = "manager-unit-namespace"
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
        label=list(RUNNER_LABELS),
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
    :returns: Manager action with exact target, scope, and token selection.
    """
    selected = ACTION.GitLabRunnerManagerAction(_args(action, apply=apply))
    selected.target = _target()
    selected.candidate_scope = RUN_SCOPE.RunScope(RUN_ID, NAMESPACE, False)
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
    assert plan["registration"] == action.runner_token.document()
    assert plan["gitlab"] == {
        "origin": "https://gitlab.example.test",
        "session": {
            "target_kind": "project",
            "target_reference": "team/project",
            "credential_digest": "sha256:" + "d" * 64,
        },
        "runner": ACTION.runner_evidence(state.gitlab_runner),
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
    ) -> CALLBACKS.RunnerRecord:
        """Record the manager's exact GitLab lookup.

        :param api: Bound API client identity.
        :param session: Bound private session identity.
        :param target_kind: Expected project scope.
        :param target_id: Expected numeric project ID.
        :param runner_id: Expected exact runner ID.
        :param description: Expected runner description.
        :param expected_tags: Expected complete tag identity.
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
        """Reject provider operations for the caller-owned namespace."""

        def read(self, kind: str, name: str, namespace: str) -> None:
            """Fail if caller-owned namespace cleanup performs a read.

            :param kind: Unexpected native kind.
            :param name: Unexpected object name.
            :param namespace: Unexpected namespace.
            :raises AssertionError: Always; no read is needed for preservation.
            """
            raise AssertionError(f"unexpected read {kind}/{namespace}/{name}")

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
                "ResolveRunScopes",
                "SelectResolvedManagerScope",
                "ObserveGitLabRunner",
                "ConfirmWorkflow",
                "DeleteManagerRunner",
                "VerifyGitLabRunnerPreserved",
                "RemoveSCCAccess",
                "RemoveScope",
            ),
        ),
    ],
)
def test_workflow_confirms_exact_plan_before_any_mutation(
    action_name: str,
    apply: bool,
    expected: Sequence[str],
) -> None:
    """Keep confirmation before mutation and deletion outside GitLab registration.

    :param action_name: Manager deploy or delete action.
    :param apply: Whether mutation callbacks are requested.
    :param expected: Exact callback order for the selected mode.
    :returns: None; assertions validate workflow ordering and provider boundaries.
    """
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
