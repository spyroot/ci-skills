"""Focused orchestration contracts for the CI13 image-build chain.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import signal
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Final, Optional
from uuid import uuid4

import pytest
import test_gitlab_runner_lifecycle as runner_lifecycle
import test_gitlab_runner_manager as runner_manager
from conftest import import_script_module
from test_podman_readiness import _node

ABSTRACT_CALLBACK = import_script_module("lib.python.core.abstract.callback")
ABSTRACT_CALLBACKS = import_script_module("lib.python.core.abstract.callbacks")
CALLBACKS = import_script_module(
    "lib.python.core.kubernetes.image_build_chain.callbacks"
)
CHAIN = import_script_module("lib.python.core.kubernetes.image_build_chain")
EXECUTOR = import_script_module("lib.python.core.executor")
GITLAB_ACTIONS = import_script_module("lib.python.core.gitlab.gitlab_actions")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")
MANAGER_ACTION = import_script_module("lib.python.core.gitlab.runner_manager.action")
PODMAN = import_script_module("lib.python.core.kubernetes.podman_probe")
READINESS = import_script_module("lib.python.core.kubernetes.image_build_readiness")
RUN_SCOPE = import_script_module("lib.python.core.kubernetes.run_scope")
RUNNER = import_script_module("lib.python.core.runner")
SCOPES = import_script_module("lib.python.core.runner_scope")
IDENTITY = import_script_module("lib.python.core.runner_identity")
TARGET = import_script_module("lib.python.core.target")
STATUS = import_script_module("lib.python.sys.status")
TOOLBOX_ACTIONS = import_script_module("lib.python.core.toolbox_actions")
VERIFICATION = import_script_module(
    "lib.python.core.kubernetes.image_build_chain.verification"
)
WORKFLOW = import_script_module("lib.python.core.kubernetes.image_build_chain.workflow")

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Callable, MutableSequence

    from lib.python.core.abstract.skill_action import SkillAction
    from lib.python.core.executor import WorkflowExecutor
    from lib.python.core.gitlab.gitlab_actions import ActionPlan
    from lib.python.core.json_types import JSONObject
    from lib.python.core.kubernetes.image_build_chain import (
        ImageBuildChainAction,
        ImageBuildChainState,
    )


RUN_ID = "ci13-unit-run"
NODE = "worker-ci13"
CALLER_NAMESPACE = "caller-builds"
CALLER_NAMESPACE_UID: Final = "caller-builds-namespace-uid"
DRIFT_UNCHANGED: Final = "unchanged"
DRIFT_UID: Final = "uid"
DRIFT_OWNERSHIP: Final = "ownership"
DRIFT_NAME: Final = "name"
MANAGER_DRIFT_CASES: Final = (DRIFT_UNCHANGED, DRIFT_UID, DRIFT_OWNERSHIP, DRIFT_NAME)
OUTCOME_SUCCESS: Final = "success"
STAGE_MANAGER: Final = "manager"
STAGE_MANAGER_DEPLOY: Final = "manager-deploy"
STAGE_SCC: Final = "scc"
STAGE_RUNNER: Final = "runner"
FIELD_STATUS: Final = "status"
FIELD_METADATA: Final = "metadata"
FIELD_UID: Final = "uid"
FIELD_DESCRIPTION: Final = "description"
FIELD_KIND: Final = "kind"
FIELD_NAME: Final = "name"
FIELD_LABELS: Final = "labels"
NAMESPACE_KIND: Final = "Namespace"
CLUSTER_NAMESPACE: Final = ""
RUNNER_ID = 73
TOOLBOX_IMAGE = "harbor.example.test/toolbox/ci@sha256:" + "a" * 64


def _read_nodes(*args: str) -> JSONObject:
    """Return the existing Ready-worker fixture for the exact node-list read.

    :param args: Kubernetes node-list command issued by chain planning.
    :returns: Native NodeList containing the requested Ready worker.
    """
    assert args == ("get", "nodes", "-o", "json")
    return {"items": [_node(NODE)]}


class ChainCluster(runner_manager.ManagerCluster):
    """Reuse manager storage with Ready-worker and borrowed-namespace observations."""

    def __init__(self) -> None:
        """Seed the caller's existing namespace with its observed immutable UID."""
        super().__init__()
        self.objects[("namespace", "", CALLER_NAMESPACE)] = {
            "kind": "Namespace",
            "metadata": {
                "name": CALLER_NAMESPACE,
                "uid": CALLER_NAMESPACE_UID,
                "labels": {},
            },
        }

    def json(self, *args: str) -> JSONObject:
        """Read nodes or delegate exact ownership lookups to the shared transport.

        :param args: Native Kubernetes observation requested by a real callback.
        :returns: Ready NodeList or the shared manager's stored resource list.
        """
        if args == ("get", "nodes", "-o", "json"):
            return _read_nodes(*args)
        return super().json(*args)


class JournalCallback(ABSTRACT_CALLBACK.LocalCallback):
    """Record one mocked provider boundary against the executor-owned state."""

    def __init__(
        self,
        action: SkillAction,
        journal: MutableSequence[str],
        name: str,
        transition: Optional[Callable[[ImageBuildChainState], None]] = None,
        failure: Optional[str] = None,
    ) -> None:
        """Retain one boundary transition or deterministic failure.

        :param action: Parent chain action accepted by the callback contract.
        :param journal: Ordered provider-boundary call journal.
        :param name: Stable stage name appended on execution.
        :param transition: Optional shared-state mutation for this boundary.
        :param failure: Optional error raised after recording the stage.
        """
        super().__init__(action)
        self.journal = journal
        self.name = name
        self.transition = transition
        self.failure = failure

    def run(self) -> JSONObject:
        """Apply the scripted state transition or fail at this boundary.

        :returns: Verified stage evidence when no failure was requested.
        :raises RuntimeError: The fixture selected a provider failure.
        """
        self.journal.append(self.name)
        if self.transition is not None:
            self.transition(self.state)
        if self.failure is not None:
            raise RuntimeError(self.failure)
        return {"verified": True, "stage": self.name}


def _args(
    *,
    group: bool = False,
    apply: bool = False,
    confirmation: Optional[str] = None,
    timeout: Optional[int] = None,
) -> Namespace:
    """Parse a runner-delivery request without consumer job inputs.

    :param group: Select a group runner.
    :param apply: Select confirmed mutation mode.
    :param confirmation: Digest supplied to confirmed apply.
    :param timeout: Optional caller-selected operation bound in seconds.
    :returns: Public parser namespace with explicit chain inputs.
    """
    scope = ["--group", "team"] if group else ["--project", "team/build"]
    mode = (
        ["--apply", "--confirm-plan", confirmation or "sha256:unit"]
        if apply
        else ["--live-plan"]
    )
    return READINESS.build_parser().parse_args(
        [
            "chain",
            "--binding",
            "/unit/target.toml",
            "--toolbox-spec",
            "/unit/toolbox.toml",
            *scope,
            "--runner-description",
            "ci13-unit-runner",
            "--runner-label",
            "ci13",
            "--runner-label",
            "podman",
            "--namespace",
            CALLER_NAMESPACE,
            "--node",
            NODE,
            "--cpu-request",
            "333m",
            "--cpu-limit",
            "2",
            "--memory-request",
            "384Mi",
            "--memory-limit",
            "2Gi",
            "--storage-size",
            "12Gi",
            *(["--timeout", str(timeout)] if timeout is not None else []),
            *mode,
            "--json",
        ]
    )


def _target() -> TARGET.Target:
    """Build finite GitLab, Kubernetes, and Harbor target identity.

    :returns: Credential-free target used only at mocked provider boundaries.
    """
    return TARGET.Target(
        github=None,
        gitlab=TARGET.GitLabTarget(
            url="https://gitlab.example.test",
            host="gitlab.example.test",
            project="team/build",
        ),
        kubernetes=TARGET.KubernetesTarget(
            context="unit-context",
            server="https://api.cluster.example.test:6443",
            kubeconfig=None,
        ),
        active_surfaces=("gitlab", "kubernetes", "harbor"),
        tested_revision="c" * 40,
        source_file=Path("/unit/target.toml"),
        source_kind="fixture",
        skill={
            "algorithm": "sha256-tree-v1",
            "digest": "d" * 64,
            "file_count": 10,
            "revision": {"value": "c" * 40, "source": "fixture", "verified": True},
            "consuming_project": {"commit": "c" * 40, "source": "fixture"},
        },
        harbor=TARGET.HarborTarget(
            url="https://harbor.example.test",
            host="harbor.example.test",
            project="toolbox",
        ),
    )


def _runner_plan() -> ActionPlan:
    """Build the existing runner-scope plan without a consumer pipeline.

    :returns: Existing GitLab runner plan used by cleanup context coverage.
    """
    return GITLAB_ACTIONS.ActionPlan(
        kind="gitlab_runner",
        operation="create",
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/build",
        target_file="/unit/target.toml",
        target_source="fixture",
        body={"description": "ci13-unit-runner"},
        revision="c" * 40,
    )


def _prepared_action(
    *,
    group: bool = False,
    apply: bool = False,
    confirmation: Optional[str] = None,
) -> ImageBuildChainAction:
    """Construct a chain action with component plans but no provider access.

    :param group: Select the group-runner case.
    :param apply: Select apply mode for confirmation tests.
    :param confirmation: Apply confirmation supplied by the caller.
    :returns: Chain action ready for pure planning and callback tests.
    """
    action = CHAIN.ImageBuildChainAction(
        _args(group=group, apply=apply, confirmation=confirmation)
    )
    action.target = _target()
    action.target_source = "fixture"
    action.gitlab_target = TARGET.GitLabOperationTarget(
        action.target.gitlab, action.target.source_file
    )
    action.gitlab_selection = TARGET.select_gitlab_reference(
        action.gitlab_target, project=action.args.project, group=action.args.group
    )
    action.repository_identity = (
        IDENTITY.RepositoryIdentity(None, IDENTITY.RepositorySource.UNKNOWN)
        if group
        else IDENTITY.resolve_repository(action.args.project)
    )
    action.resolved_description = IDENTITY.ResolvedName(
        action.args.runner_description, IDENTITY.NameSource.EXPLICIT
    )
    action.manager_scope = RUN_SCOPE.RunScope(RUN_ID, CALLER_NAMESPACE, False)
    action.toolbox = SimpleNamespace(
        resolved_base=None,
        repository="toolbox/ci",
        spec=SimpleNamespace(
            fingerprint="sha256:toolbox-spec",
            base_image="registry.example.test/base@sha256:" + "e" * 64,
            image_repository="toolbox/ci",
            image_tag="unit",
            namespace=CALLER_NAMESPACE,
            app="ci13-toolbox",
        ),
        runner=RUNNER.RunnerAction(
            namespace=CALLER_NAMESPACE,
            name="ci13-toolbox",
            source_root="",
            labels=(),
            timeout=action.args.timeout,
        ),
    )
    action.mode = PODMAN.ProbeMode.APPLY if apply else PODMAN.ProbeMode.LIVE_PLAN
    action.state.args = action.args
    return action


def test_chain_toolbox_build_can_complete_after_command_default_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Honor the chain timeout when a toolbox Build outlasts the command default.

    :param monkeypatch: Fixture isolating configuration, provider reads, and time.
    :returns: None; the real shared service observes a complete Build at 35 seconds.
    """
    action = _prepared_action()
    action.args = _args(timeout=300)
    action.state.args = action.args
    action.state.runner_auth_plan = action._runner_auth_plan()
    action.toolbox = TOOLBOX_ACTIONS.WorkflowAction(
        action.args, spec=action.toolbox.spec, context=action.state
    )
    monkeypatch.setattr(
        WORKFLOW, "WorkflowExecutor", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    callbacks = CHAIN.workflow(action).callbacks
    authentication = next(
        callback for callback in callbacks if isinstance(callback, KUBERNETES.K8SAuth)
    )
    authentication.bind(action.state)
    action.state.kubernetes_target = action.target
    monkeypatch.setattr(
        KUBERNETES.KubernetesService, "verify", lambda _service: "unit-builder"
    )
    authentication.run()
    service = action.state.kubernetes
    elapsed = [0.0]
    completed = {"status": {"phase": "Complete"}}

    def advance(seconds: float) -> None:
        """Advance the fake clock without waiting.

        :param seconds: Poll interval selected by the real Build waiter.
        :returns: None after advancing simulated elapsed time.
        """
        elapsed[0] += seconds

    def read_build(kind: str, name: str, namespace: str) -> JSONObject:
        """Keep the selected Build running until its 35-second completion.

        :param kind: Native resource kind requested by the waiter.
        :param name: Exact toolbox Build selected by the caller.
        :param namespace: Caller-owned Build namespace.
        :returns: Running or complete provider read-back at the fake clock time.
        """
        assert (kind, name, namespace) == ("build", "toolbox-1", CALLER_NAMESPACE)
        return completed if elapsed[0] >= 35 else {"status": {"phase": "Running"}}

    monkeypatch.setattr(
        KUBERNETES,
        "time",
        SimpleNamespace(monotonic=lambda: elapsed[0], sleep=advance),
    )
    monkeypatch.setattr(service, "read", read_build)

    assert service.wait_build("toolbox-1", CALLER_NAMESPACE) is completed
    assert elapsed[0] == 35


@pytest.mark.parametrize(
    ("group", "runner_scope", "runner_reference"),
    (
        (False, "project", "team/build"),
        (True, "group", "team"),
    ),
    ids=("project-runner", "group-runner"),
)
def test_chain_plan_requires_only_runner_delivery_inputs(
    group: bool,
    runner_scope: str,
    runner_reference: str,
) -> None:
    """Plan project or group runner delivery without consumer job inputs.

    :param group: Whether the runner belongs to the group scope.
    :param runner_scope: Expected runner scope in the chain plan.
    :param runner_reference: Expected project or group reference.
    :returns: None after checking runner scope and absence of test operations.
    """
    action = _prepared_action(group=group)
    action._validate_chain_inputs()

    plan = action.plan(NODE)

    assert plan["runner"]["scope"] == runner_scope
    assert plan["runner"]["reference"] == runner_reference
    assert "pipeline" not in plan
    assert "job" not in plan
    assert "probe" not in plan


@pytest.mark.parametrize(
    "confirmation", ("", "sha256:stale"), ids=("absent", "mismatched")
)
def test_chain_confirmation_mismatch_prevents_every_mutating_stage(
    confirmation: str,
) -> None:
    """Stop the shared executor before build, publication, or manager writes.

    :param confirmation: Missing or stale confirmation supplied by the caller.
    :returns: None; the mutation callback remains unentered after confirmation fails.
    """
    action = _prepared_action(apply=True)
    action.args.confirm_plan = confirmation
    action.state.selected_node = NODE
    action.state.kubernetes = ChainCluster()
    mutations: list[str] = []
    callbacks = ABSTRACT_CALLBACKS.Callbacks(
        [
            CALLBACKS.ResolveChainPlan(action),
            JournalCallback(action, mutations, "provider-mutation"),
        ]
    )
    executor = EXECUTOR.WorkflowExecutor(action.state, callbacks)

    with pytest.raises(ValueError, match="confirm_plan_mismatch_rerun_live_plan"):
        executor.run()

    assert mutations == []
    assert not hasattr(action.state, "run_scope")
    assert not hasattr(action.state, "created_runner_id")


@pytest.mark.parametrize("outer_run_id", (None, "caller-chain-run"))
def test_delivery_scope_does_not_change_confirmed_plan(
    outer_run_id: Optional[str],
) -> None:
    """Keep confirmation stable while delivery acquires its concrete run scope.

    :param outer_run_id: Omitted or caller-selected execution identity.
    :returns: None after comparing the public plan before and after scope creation.
    """
    action = _prepared_action(apply=True)
    action.args.run_id = outer_run_id
    confirmed_plan = deepcopy(action.plan(NODE))
    confirmed_digest = action.fingerprint(NODE)
    action.args.confirm_plan = confirmed_digest

    for run_id in (RUN_ID, RUN_ID, f"{RUN_ID}-next"):
        action.state.run_scope = RUN_SCOPE.RunScope(run_id, CALLER_NAMESPACE, False)
        assert action.plan(NODE) == confirmed_plan
        assert action.fingerprint(NODE) == confirmed_digest

    assert action.args.confirm_plan == confirmed_digest


def test_manager_reuses_selected_node_scope_account_and_resource_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Carry selected placement and resource requests into manager preparation.

    :param tmp_path: Directory holding the ephemeral one-time runner token.
    :param monkeypatch: Fixture replacing only target and credential boundaries.
    :returns: None; manager workload fields match the caller's selections.
    """
    action = _prepared_action(apply=True)
    state = action.state
    state.selected_node = NODE
    state.published_image = TOOLBOX_IMAGE
    state.created_runner_id = RUNNER_ID
    state.run_scope = RUN_SCOPE.RunScope(RUN_ID, CALLER_NAMESPACE, False)
    state.token_path = tmp_path / "runner-token"
    state.token_path.write_text("glrt-unit-only\n", encoding="utf-8")
    state.token_path.chmod(0o600)
    monkeypatch.setattr(MANAGER_ACTION, "resolve_target", lambda *_a, **_k: _target())
    monkeypatch.setattr(MANAGER_ACTION, "bind_sources", lambda target, **_k: target)
    callback = CALLBACKS.PrepareRunnerManager(action)
    callback.bind(state)

    result = callback.run()

    workload = state.manager_action.workload().document(state.run_scope)
    assert result == {
        "verified": True,
        "runner_id": RUNNER_ID,
        "node": NODE,
        "image": TOOLBOX_IMAGE,
    }
    assert workload["runner"]["namespace"] == CALLER_NAMESPACE
    assert workload["runner"]["ownership_id"] == RUN_ID
    assert workload["security_constraint"] == "privileged"
    assert workload["podman"] == {
        "node": NODE,
        "service_account": "default",
        "privileged": True,
        "automount_service_account_token": False,
        "resources": {
            "cpu_request": "333m",
            "cpu_limit": "2",
            "memory_request": "384Mi",
            "memory_limit": "2Gi",
            "storage_size": "12Gi",
        },
        "storage": {
            "graph_root": "/var/lib/containers",
            "run_root": "/run/containers",
        },
    }


def _lifecycle_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str = "failed-manager",
    cleanup_failure: bool = False,
    owns_namespace: bool = False,
) -> tuple[
    ImageBuildChainAction,
    WorkflowExecutor,
    list[str],
    list[str],
    runner_manager.ManagerCluster,
]:
    """Use the real chain and manager deployment with existing memory transports.

    :param tmp_path: Directory receiving the ephemeral runner-token sink.
    :param monkeypatch: Fixture replacing provider mutations at existing boundaries.
    :param outcome: Success, manager failure, timeout, SIGINT, or SIGTERM outcome.
    :param cleanup_failure: Make manager removal fail while later cleanup continues.
    :param owns_namespace: Reuse a namespace owned by this delivery scope.
    :returns: Action, real executor, stage journal, cleanup journal, and fake Kubernetes.
    """
    action = _prepared_action(apply=True)
    selected_namespace = uuid4().hex
    selected_namespace_uid = uuid4().hex
    selected_run_id = uuid4().hex
    action.args.namespace = selected_namespace
    action.args.runner_description = uuid4().hex
    action.args.runner_label = [uuid4().hex, uuid4().hex]
    action.resolved_description = IDENTITY.ResolvedName(
        action.args.runner_description, IDENTITY.NameSource.EXPLICIT
    )
    action.toolbox.spec.namespace = selected_namespace
    action.toolbox.runner = replace(
        action.toolbox.runner, namespace=selected_namespace, name=uuid4().hex
    )
    state = action.state
    kubernetes = ChainCluster()
    state.kubernetes = kubernetes
    state.selected_node = NODE
    state.run_scope = RUN_SCOPE.RunScope(
        selected_run_id, selected_namespace, owns_namespace
    )
    if owns_namespace:
        state.found_scopes = (state.run_scope,)
        state.resolved_run_id = selected_run_id
    namespace: JSONObject = {
        FIELD_KIND: NAMESPACE_KIND,
        FIELD_METADATA: {FIELD_NAME: selected_namespace, FIELD_LABELS: {}},
    }
    if owns_namespace:
        namespace = state.run_scope.cluster_resources(kubernetes).managed(
            RUN_SCOPE.namespace_document(state.run_scope)
        )
    namespace[FIELD_METADATA][FIELD_UID] = selected_namespace_uid
    kubernetes.objects[
        (NAMESPACE_KIND.casefold(), CLUSTER_NAMESPACE, selected_namespace)
    ] = namespace
    state.gitlab = SimpleNamespace(
        session=SimpleNamespace(
            origin="https://gitlab.example.test", target_kind="project"
        ),
        api=object(),
        target_id=42,
    )
    monkeypatch.setattr(
        runner_manager.GITLAB_API, "_lock_root", lambda: tmp_path / "locks"
    )
    state.harbor = SimpleNamespace(dockerconfig=b'{"auths":{}}')
    state.runner_runtime = runner_manager.CALLBACKS.RunnerRuntime(
        init_command=("/bin/true",),
        command=("gitlab-runner",),
        environment=(),
        readiness_command=(),
        credential={"token": "private-unit-value"},
    )
    state.runner_auth_plan = action._runner_auth_plan()
    action.manager_scope = state.run_scope
    action.mode = PODMAN.ProbeMode.LIVE_PLAN
    planner = CALLBACKS.ResolveChainPlan(action)
    planner.bind(state)
    planner.run()
    action.mode = PODMAN.ProbeMode.APPLY
    action.args.confirm_plan = state.chain_plan_digest
    stages: list[str] = []
    cleanup: list[str] = []

    def toolbox(current: ImageBuildChainState) -> None:
        """Check selected inputs at the toolbox provider boundary.

        :param current: Shared state retaining selected placement and scope.
        """
        assert current.selected_node == NODE
        assert current.run_scope.namespace == selected_namespace

    def publish(current: ImageBuildChainState) -> None:
        """Retain the immutable image from the Harbor provider boundary.

        :param current: Shared state receiving the publication output.
        """
        current.published_image = TOOLBOX_IMAGE

    def manager(current: ImageBuildChainState) -> None:
        """Retain manager inputs and the provider-created runner identity.

        :param current: Shared state receiving runner and manager inputs.
        """
        assert current.published_image == TOOLBOX_IMAGE
        current.created_runner_id = RUNNER_ID
        current.runner_create_plan = replace(
            _runner_plan(), body={FIELD_DESCRIPTION: action.args.runner_description}
        )
        current.token_directory = tmp_path / uuid4().hex
        current.token_directory.mkdir()
        current.token_path = current.token_directory / uuid4().hex
        current.token_path.write_text("private-unit-value\n", encoding="utf-8")
        selected = runner_manager._action(apply=True)
        selected.context = current
        selected.candidate_scope = current.run_scope
        selected.repository_identity = action.repository_identity
        selected.resolved_runner_name = action.resolved_runner_name
        selected.args.description = action.args.runner_description
        selected.args.tag = list(action.args.runner_label)
        selected.explicit_tags = tuple(selected.args.tag)
        selected.args.image = TOOLBOX_IMAGE
        selected.args.run_id = current.run_scope.run_id
        selected.args.namespace = current.run_scope.namespace
        current.manager_action = selected
        binding = runner_manager.SCC_ACCESS.role_binding_document(
            selected.scc_access(), current.run_scope
        )
        kubernetes.objects[
            (
                "rolebinding",
                selected_namespace,
                str(binding[FIELD_METADATA][FIELD_NAME]),
            )
        ] = binding

    rollout = kubernetes.wait_rollout

    def deploy(kind: str, name: str, namespace: str) -> None:
        """Return actual memory rollout or a bounded injected provider failure.

        :param kind: Deployment selected by shared runner code.
        :param name: Exact run-owned manager name.
        :param namespace: Selected delivery namespace.
        """
        stages.append("manager-deploy")
        if outcome == "failed-manager":
            raise GITLAB_ACTIONS.ActionError("manager_rollout_failed")
        if outcome == "timeout":
            raise TimeoutError("manager_rollout_timeout token=private-unit-value")
        if outcome in ("SIGINT", "SIGTERM"):
            signal.raise_signal(getattr(signal, outcome))
        rollout(kind, name, namespace)

    def cleanup_callback(_selection: object, resource: str) -> JournalCallback:
        """Build one mocked existing cleanup operation.

        :param _selection: Existing manager or SCC deletion input.
        :param resource: Resource category recorded on deletion.
        :returns: Local callback that records a verified cleanup attempt.
        """
        return JournalCallback(
            action,
            cleanup,
            resource,
            failure="manager_remove_failed token=private-unit-value"
            if cleanup_failure and resource == "manager"
            else None,
        )

    def delete_runner(
        _state: ImageBuildChainState,
        records: MutableSequence[JSONObject],
        _failures: MutableSequence[JSONObject],
    ) -> None:
        """Record deletion through the existing runner-cleanup boundary.

        :param _state: Shared state retained by cleanup.
        :param records: Destination for successful cleanup evidence.
        :param _failures: Destination for cleanup failures, unused in this double.
        """
        cleanup.append("runner")
        records.append(
            {"callback": "DeleteRunner", "resource": "runner", "verified": True}
        )

    monkeypatch.setattr(
        VERIFICATION,
        "DeleteManagerRunner",
        lambda selection: cleanup_callback(selection, "manager"),
    )
    monkeypatch.setattr(
        VERIFICATION,
        "RemoveSCCAccess",
        lambda selection: cleanup_callback(selection, "scc"),
    )
    monkeypatch.setattr(VERIFICATION, "workload_action", lambda _manager: action)
    monkeypatch.setattr(VERIFICATION, "_delete_runner_record", delete_runner)
    monkeypatch.setattr(kubernetes, "wait_rollout", deploy)
    for name in (
        "K8SAuth",
        "HarborAuth",
        "ConfirmWorkflow",
        "EnsureNamespace",
        "ObserveGitLabRunner",
        "EnsureSCCAccess",
        "GitLabRunnerRuntime",
    ):
        monkeypatch.setattr(
            WORKFLOW,
            name,
            lambda *_a, name=name, **_k: JournalCallback(action, stages, name),
        )
    transitions = {
        "ScopedGitLabAuth": ("runner-auth", None),
        "HarborBuild": ("toolbox", toolbox),
        "HarborPush": ("harbor-push", publish),
        "CreateRunnerRecord": ("manager", manager),
        "PrepareRunnerManager": ("manager-prepare", None),
    }
    for name, (stage, transition) in transitions.items():
        monkeypatch.setattr(
            WORKFLOW,
            name,
            lambda *_a, stage=stage, transition=transition, **_k: JournalCallback(
                action, stages, stage, transition
            ),
        )
    for name in (
        "StartBuildPipeline",
        "WatchBuildPipeline",
        "ResolvePipelineStart",
        "RunPodmanProbe",
        "PullManagerImage",
        "VerifyGitLabRunnerOnline",
    ):
        monkeypatch.setattr(
            WORKFLOW,
            name,
            lambda *_a, **_k: pytest.fail("delivery entered a test job or pipeline"),
            raising=False,
        )
    executor = WORKFLOW.workflow(action)
    assert all(callback.state is state for callback in executor.callbacks)

    return action, executor, stages, cleanup, kubernetes


@pytest.mark.parametrize(
    ("outcome", "owns_namespace"),
    (
        ("success", False),
        ("success", True),
        ("failed-manager", False),
        ("timeout", False),
        ("SIGINT", False),
        ("SIGTERM", False),
    ),
)
def test_chain_retains_delivery_and_cleans_failures_timeouts_and_signals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    owns_namespace: bool,
) -> None:
    """Keep the deployed runner on success and clean failed delivery attempts.

    :param tmp_path: Directory receiving the private runner-token sink.
    :param monkeypatch: Fixture replacing external provider operations.
    :param outcome: Deployment success, provider failure, timeout, or signal.
    :param owns_namespace: Whether successful delivery owns its retained namespace.
    :returns: None after checking cleanup side effects and restored signal handlers.
    """
    action, executor, stages, cleanup, kubernetes = _lifecycle_executor(
        tmp_path, monkeypatch, outcome, owns_namespace=owns_namespace
    )
    previous = {
        number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)
    }
    if outcome == "success":
        executor.run()
    elif outcome in ("SIGINT", "SIGTERM"):
        with pytest.raises(SystemExit) as interrupted:
            executor.run()
        assert interrupted.value.code == 128 + getattr(signal, outcome)
    else:
        expected = (
            "manager_rollout_failed"
            if outcome == "failed-manager"
            else "manager_rollout_timeout"
        )
        with pytest.raises((GITLAB_ACTIONS.ActionError, TimeoutError), match=expected):
            executor.run()
    state = action.state
    assert stages.count("runner-auth") == 1
    assert stages.index("toolbox") < stages.index("harbor-push")
    assert stages.index("harbor-push") < stages.index("manager")
    assert stages.index("manager") < stages.index("manager-deploy")
    assert cleanup == ([] if outcome == "success" else ["manager", "scc", "runner"])
    assert state.chain_cleanup["status"] == "PASS"
    if outcome == "success":
        assert state.created_runner_id == RUNNER_ID
        assert state.deployment_result["verified"] is True
        assert state.deployment_result["image"] == TOOLBOX_IMAGE
        assert state.deployment_result["pods"]
        assert state.published_image == TOOLBOX_IMAGE
        assert {kind for kind, _namespace, _name in kubernetes.objects} >= {
            "deployment",
            "secret",
            "rolebinding",
            "namespace",
        }
        assert kubernetes.deleted == []
        assert state.runner_gitlab is state.gitlab
        assert state.toolbox_harbor is state.harbor
    else:
        scope_cleanup = next(
            item
            for item in state.chain_cleanup["resources"]
            if item.get("callback") == "RemoveScope"
        )
        assert scope_cleanup["preserved_scopes"] == [state.run_scope.as_dict()]
    assert (
        kubernetes.read(NAMESPACE_KIND, state.run_scope.namespace, CLUSTER_NAMESPACE)
        is not None
    )
    assert not state.token_path.exists()
    assert not state.token_directory.exists()
    assert {number: signal.getsignal(number) for number in previous} == previous


@pytest.mark.parametrize("drift", MANAGER_DRIFT_CASES)
def test_chain_manager_confirmation_rejects_drift_before_deploy_and_cleans_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    """Bind the final manager guard to the confirmed plan after provider creation.

    :param tmp_path: Private token and resource-lock directory for the lifecycle.
    :param monkeypatch: Isolate provider calls and inject drift at the final guard.
    :param drift: Stable input or namespace UID, ownership, or workload name drift.
    """
    action, executor, stages, cleanup, kubernetes = _lifecycle_executor(
        tmp_path, monkeypatch, OUTCOME_SUCCESS
    )
    original_guard = CALLBACKS.ConfirmChainManager.run
    selected_namespace = action.manager_scope.namespace
    original_namespace = kubernetes.read(
        NAMESPACE_KIND, selected_namespace, CLUSTER_NAMESPACE
    )
    assert original_namespace is not None
    selected_uid = original_namespace[FIELD_METADATA][FIELD_UID]
    replacement_uid = uuid4().hex
    changed_manager_name = uuid4().hex
    guard_entries: list[str] = []

    def observe_drift(callback: CALLBACKS.ConfirmChainManager) -> JSONObject:
        """Change one bound value at the final observation boundary.

        :param callback: Real guard retaining the shared chain execution state.
        :returns: Original guard result for the unchanged confirmed selection.
        """
        state = callback.state
        guard_entries.append(drift)
        key, observation = next(
            (key, value)
            for key, value in state.runner_scopes.items()
            if value.purpose == SCOPES.ScopePurpose.MANAGER
        )
        if drift == DRIFT_UID:
            namespace = kubernetes.objects[
                (NAMESPACE_KIND.casefold(), CLUSTER_NAMESPACE, selected_namespace)
            ]
            namespace[FIELD_METADATA][FIELD_UID] = replacement_uid
            state.runner_scopes[key] = replace(observation, uid=replacement_uid)
        elif drift == DRIFT_OWNERSHIP:
            state.runner_scopes[key] = replace(
                observation,
                ownership=SCOPES.ScopeOwnership.OPERATION_OWNED,
                retention=SCOPES.ScopeRetention.RETAINED,
            )
        elif drift == DRIFT_NAME:
            state.manager_action.resolved_runner_name = IDENTITY.ResolvedName(
                changed_manager_name, IDENTITY.NameSource.PROVIDER
            )
        return original_guard(callback)

    monkeypatch.setattr(
        CALLBACKS.ConfirmChainManager, original_guard.__name__, observe_drift
    )
    if drift == DRIFT_UNCHANGED:
        executor.run()
        assert stages.count(STAGE_MANAGER_DEPLOY) == len(guard_entries)
        assert cleanup == []
    else:
        with pytest.raises(ValueError, match=CALLBACKS.CHAIN_MANAGER_CHANGED):
            executor.run()
        assert STAGE_MANAGER in stages
        assert STAGE_MANAGER_DEPLOY not in stages
        assert kubernetes.applied == []
        assert cleanup == [STAGE_MANAGER, STAGE_SCC, STAGE_RUNNER]
    assert guard_entries == [drift]
    assert action.state.chain_cleanup[FIELD_STATUS] == STATUS.PASS
    assert kubernetes.deleted == []
    namespace = kubernetes.read(NAMESPACE_KIND, selected_namespace, CLUSTER_NAMESPACE)
    assert namespace is not None
    assert namespace[FIELD_METADATA][FIELD_UID] == (
        replacement_uid if drift == DRIFT_UID else selected_uid
    )
    assert not action.state.token_path.exists()
    assert not action.state.token_directory.exists()


@pytest.mark.parametrize(
    "delete_failure", (False, True), ids=("deleted", "delete-failed")
)
def test_chain_runner_cleanup_uses_delete_plan_and_restores_execution_context(
    delete_failure: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real cleanup deletes only its runner and restores the caller's active context.

    :param delete_failure: Whether the provider rejects the DELETE request.
    :param tmp_path: Isolated resource-lock directory used by the real callback.
    :param monkeypatch: Fixture selecting only the temporary lock directory.
    """
    monkeypatch.setattr(runner_lifecycle.API, "_lock_root", lambda: tmp_path / "locks")
    state = _prepared_action(apply=True).state
    state.runner_create_plan = runner_lifecycle._plan(
        "create", {"description": "owned-runner"}
    )
    state.created_runner_id = RUNNER_ID
    original_plan = _runner_plan()
    original_provider = SimpleNamespace(session=object())
    state.plan = original_plan
    state.gitlab = original_provider
    state.configured_session = original_provider.session
    endpoint = f"runners/{RUNNER_ID}"
    scope = runner_lifecycle._list()
    details = [runner_lifecycle._runner(RUNNER_ID), runner_lifecycle._runner(RUNNER_ID)]
    memberships = [[{"id": RUNNER_ID}], [{"id": RUNNER_ID}]]
    if not delete_failure:
        details.append(runner_lifecycle.API.GitLabAPIError("provider_404"))
        memberships.append([])
    api = runner_lifecycle.FakeAPI(
        {
            ("GET", endpoint): details,
            ("GET", scope): memberships,
            ("DELETE", endpoint): [runner_lifecycle.API.GitLabAPIError("provider_403")]
            if delete_failure
            else [{}],
        }
    )
    state.runner_gitlab = SimpleNamespace(api=api, session=object(), target_id=42)
    records: list[JSONObject] = []
    failures: list[JSONObject] = []

    VERIFICATION._delete_runner_record(state, records, failures)

    assert state.plan is original_plan
    assert state.gitlab is original_provider
    assert state.configured_session is original_provider.session
    expected_calls = [
        ("GET", endpoint),
        ("GET", scope),
        ("GET", endpoint),
        ("GET", scope),
        ("DELETE", endpoint),
    ]
    if delete_failure:
        assert records == []
        assert failures == [{"callback": "DeleteRunner", "reason": "provider_403"}]
    else:
        expected_calls.extend((("GET", endpoint), ("GET", scope)))
        assert failures == []
        assert len(records) == 1
        record = records[0]
        assert record["callback"] == "DeleteRunner"
        assert record["action"] == "APPLIED"
        assert record["id"] == RUNNER_ID
        assert record["verified"] is True
        assert record["after"]["global_get_status"] == 404
        assert record["after"]["scoped_absent"] is True
    assert api.calls == expected_calls
