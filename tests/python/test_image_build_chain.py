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
from typing import TYPE_CHECKING, Optional

import pytest
from conftest import import_script_module

ABSTRACT_CALLBACK = import_script_module("lib.python.core.abstract.callback")
ABSTRACT_CALLBACKS = import_script_module("lib.python.core.abstract.callbacks")
CALLBACKS = import_script_module(
    "lib.python.core.kubernetes.image_build_chain.callbacks"
)
CHAIN = import_script_module("lib.python.core.kubernetes.image_build_chain")
EXECUTOR = import_script_module("lib.python.core.executor")
GITLAB_ACTIONS = import_script_module("lib.python.core.gitlab.gitlab_actions")
GITLAB_JOBS = import_script_module("lib.python.core.gitlab.gitlab_jobs")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")
MANAGER_ACTION = import_script_module("lib.python.core.gitlab.runner_manager.action")
PODMAN = import_script_module("lib.python.core.kubernetes.podman_probe")
READINESS = import_script_module("lib.python.core.kubernetes.image_build_readiness")
RUN_SCOPE = import_script_module("lib.python.core.kubernetes.run_scope")
TARGET = import_script_module("lib.python.core.target")
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
RUNNER_ID = 73
PIPELINE_ID = 91
TOOLBOX_IMAGE = "harbor.example.test/toolbox/ci@sha256:" + "a" * 64
BUILD_DIGEST = "sha256:" + "b" * 64


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
    """Parse one complete project- or group-runner chain request.

    :param group: Select a group runner and a separate pipeline project.
    :param apply: Select confirmed mutation mode.
    :param confirmation: Digest supplied to confirmed apply.
    :param timeout: Optional caller-selected operation bound in seconds.
    :returns: Public parser namespace with explicit chain inputs.
    """
    scope = (
        ["--group", "team", "--pipeline-project", "team/build"]
        if group
        else ["--project", "team/build"]
    )
    mode = (
        ["--apply", "--confirm-plan", confirmation or "sha256:unit"]
        if apply
        else ["--live-plan"]
    )
    return READINESS.build_parser().parse_args(
        [
            "chain",
            "--target",
            "/unit/target.toml",
            "--toolbox-spec",
            "/unit/toolbox.toml",
            *scope,
            "--ref",
            "main",
            "--expected-sha",
            "c" * 40,
            "--job",
            "build-image",
            "--job-digest-artifact",
            "reports/image-digest.txt",
            "--runner-description",
            "ci13-unit-runner",
            "--runner-label",
            "ci13",
            "--runner-label",
            "podman",
            "--build-repository",
            "team/application",
            "--build-tag",
            "unit",
            "--namespace",
            CALLER_NAMESPACE,
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


def _pipeline_plan(project: str = "team/build") -> ActionPlan:
    """Build one exact-ref pipeline plan for chain planning.

    :param project: Project that owns the selected build pipeline.
    :returns: Existing GitLab action plan with an exact commit.
    """
    return GITLAB_ACTIONS.ActionPlan(
        kind="gitlab_pipeline",
        operation="start",
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference=project,
        target_file="/unit/target.toml",
        target_source="fixture",
        body={"ref": "main", "resolved_sha": "c" * 40, "variables": []},
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
    action.probe = SimpleNamespace(
        plan=lambda node: {
            "node": node,
            "run_scope": {
                "namespace": CALLER_NAMESPACE,
                "service_account": "default",
            },
            "podman": {
                "privileged": True,
                "cpu_request": action.args.cpu_request,
                "cpu_limit": action.args.cpu_limit,
                "memory_request": action.args.memory_request,
                "memory_limit": action.args.memory_limit,
                "storage_size": action.args.storage_size,
            },
        }
    )
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
    )
    action.pipeline = _pipeline_plan()
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
    action.state.runner_auth_plan = _pipeline_plan()
    action.state.pipeline_plan = _pipeline_plan()
    action.toolbox = TOOLBOX_ACTIONS.WorkflowAction(
        action.args, spec=action.toolbox.spec, context=action.state
    )
    action.probe = PODMAN.PodmanProbeAction(
        target=action.target,
        node=NODE,
        probe_image=action.args.probe_image,
        inner_image=action.args.inner_image,
        timeout=action.args.timeout,
        resources=PODMAN.probe_resources_from_args(action.args),
        mode=action.mode,
        confirm_plan=None,
        namespace=CALLER_NAMESPACE,
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
    ("group", "runner_scope", "runner_reference", "pipeline_project"),
    (
        (False, "project", "team/build", "team/build"),
        (True, "group", "team", "team/build"),
    ),
    ids=("project-runner", "group-runner-separate-project"),
)
def test_chain_plan_separates_runner_scope_from_pipeline_project(
    group: bool,
    runner_scope: str,
    runner_reference: str,
    pipeline_project: str,
) -> None:
    """Bind project and group runners without losing the job project identity.

    :param group: Whether the runner belongs to the group scope.
    :param runner_scope: Expected runner scope in the chain plan.
    :param runner_reference: Expected project or group reference.
    :param pipeline_project: Expected project that executes the build job.
    :returns: None; assertions validate distinct target roles.
    """
    action = _prepared_action(group=group)

    plan = action.plan(NODE, _pipeline_plan(pipeline_project))

    assert plan["runner"]["scope"] == runner_scope
    assert plan["runner"]["reference"] == runner_reference
    assert plan["pipeline"]["project"] == pipeline_project
    assert plan["pipeline"]["plan"]["target_reference"] == pipeline_project


@pytest.mark.parametrize(
    "confirmation", ("", "sha256:stale"), ids=("absent", "mismatched")
)
def test_chain_confirmation_mismatch_prevents_every_mutating_stage(
    confirmation: str,
) -> None:
    """Stop the shared executor before probe, build, manager, or pipeline writes.

    :param confirmation: Missing or stale confirmation supplied by the caller.
    :returns: None; the mutation callback remains unentered after confirmation fails.
    """
    action = _prepared_action(apply=True)
    action.args.confirm_plan = confirmation
    action.state.selected_node = NODE
    action.state.pipeline_plan = _pipeline_plan()
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
def test_pipeline_start_uses_execution_scope_without_changing_confirmation(
    outer_run_id: Optional[str],
) -> None:
    """Reuse a pipeline only within one concrete chain execution scope.

    :param outer_run_id: Omitted or caller-supplied identity in the stable template.
    :returns: None; child digests distinguish runs while confirmation stays stable.
    """
    action = _prepared_action(apply=True)
    action.args.run_id = outer_run_id
    template = _pipeline_plan()
    template = replace(template, body={**template.body, "run_id": outer_run_id})
    template_body = deepcopy(template.body)
    state = action.state
    state.pipeline_plan = template
    state.chain_plan = action.plan(NODE, template)
    state.chain_plan_digest = action.fingerprint(NODE, template)
    action.args.confirm_plan = state.chain_plan_digest
    confirmed_plan = deepcopy(state.chain_plan)
    confirmed_digest = state.chain_plan_digest
    state.gitlab = SimpleNamespace(session=object())
    child_plans: list[ActionPlan] = []

    for run_id in (RUN_ID, RUN_ID, f"{RUN_ID}-next"):
        state.run_scope = RUN_SCOPE.RunScope(run_id, CALLER_NAMESPACE, False)
        callback = CALLBACKS.StartBuildPipeline(action)
        callback.bind(state)

        assert callback.collect() == {}

        assert callback.child is not None
        assert callback.child.action is state.plan
        assert state.plan.body == {**template_body, "run_id": run_id}
        child_plans.append(state.plan)

    first, retry, next_run = child_plans
    assert first.digest == retry.digest
    assert first.digest != next_run.digest
    assert state.pipeline_plan is template
    assert template.body == template_body
    assert state.chain_plan == confirmed_plan
    assert state.chain_plan_digest == confirmed_digest
    assert action.args.confirm_plan == confirmed_digest
    assert action.fingerprint(NODE, template) == confirmed_digest


def test_manager_reuses_probe_node_scope_account_and_resource_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Carry the proven Podman placement unchanged into manager preparation.

    :param tmp_path: Directory holding the ephemeral one-time runner token.
    :param monkeypatch: Fixture replacing only target and credential boundaries.
    :returns: None; manager workload fields match the probe-side selections.
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
        "run_id": RUN_ID,
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


def _job(runner_id: int = RUNNER_ID) -> JSONObject:
    """Build one successful job bound to the chain pipeline and runner.

    :param runner_id: Runner identity reported by GitLab for the selected job.
    :returns: Complete bounded job summary accepted by the shared job reader.
    """
    return {
        "id": 501,
        "name": "build-image",
        "stage": "build",
        "status": "success",
        "stuck": False,
        "failure_reason": None,
        "created_at": "2026-10-18T00:00:00Z",
        "started_at": "2026-10-18T00:00:01Z",
        "finished_at": "2026-10-18T00:00:03Z",
        "duration": 2.0,
        "runner": {"id": runner_id, "description": "ci13-unit-runner"},
        "pipeline_id": PIPELINE_ID,
        "ref": "main",
        "web_url": "https://gitlab.example.test/team/build/-/jobs/501",
    }


class ArtifactAPI:
    """Return one exact digest artifact emitted by the selected build job."""

    def __init__(self, digest: str) -> None:
        """Retain the artifact value and initialize an endpoint journal.

        :param digest: Digest text returned from the bounded artifact endpoint.
        """
        self.digest = digest
        self.calls: list[str] = []

    def get_text(self, _session: object, endpoint: str) -> str:
        """Read the single job artifact and record its exact endpoint.

        :param _session: Bound GitLab session retained only for interface parity.
        :param endpoint: Job artifact endpoint selected by production code.
        :returns: Configured artifact digest with its normal terminal newline.
        """
        self.calls.append(endpoint)
        return self.digest + "\n"


class HarborReadback:
    """Return one independently observed Harbor tag digest."""

    def __init__(self, digest: Optional[str]) -> None:
        """Retain the tag read-back selected for this test.

        :param digest: Harbor digest or missing observation.
        """
        self.observed_digest = digest

    def digest(self, repository: str, tag: str) -> Optional[str]:
        """Return the configured digest for the exact repository and tag.

        :param repository: Expected job publication repository.
        :param tag: Expected caller-selected publication tag.
        :returns: Configured immutable digest or ``None``.
        """
        assert (repository, tag) == ("team/application", "unit")
        return self.observed_digest

    def image(self, repository: str, digest: str) -> str:
        """Render the digest-pinned image from verified Harbor identity.

        :param repository: Verified repository name.
        :param digest: Verified canonical digest.
        :returns: Deterministic fixture image reference.
        """
        return f"harbor.example.test/{repository}@{digest}"


def _bind_verification_state(
    action: ImageBuildChainAction,
    *,
    artifact_digest: str = BUILD_DIGEST,
    harbor_digest: Optional[str] = BUILD_DIGEST,
) -> ArtifactAPI:
    """Bind finite job-artifact and Harbor read-back services to chain state.

    :param action: Prepared chain action whose one state receives the services.
    :param artifact_digest: Digest emitted by the exact selected job.
    :param harbor_digest: Digest independently read from the selected Harbor tag.
    :returns: Artifact API double for endpoint assertions.
    """
    api = ArtifactAPI(artifact_digest)
    action.state.pipeline_id = PIPELINE_ID
    action.state.created_runner_id = RUNNER_ID
    action.state.pipeline_gitlab = SimpleNamespace(
        session=object(), target_id=42, api=api
    )
    action.state.build_harbor = HarborReadback(harbor_digest)
    return api


@pytest.mark.parametrize(
    ("runner_id", "artifact_digest", "harbor_digest", "expected"),
    (
        (99, BUILD_DIGEST, BUILD_DIGEST, "build_job_runner_mismatch"),
        (RUNNER_ID, "", BUILD_DIGEST, "build_job_digest_artifact_invalid"),
        (
            RUNNER_ID,
            BUILD_DIGEST,
            "sha256:" + "f" * 64,
            "build_job_digest_mismatch",
        ),
        (RUNNER_ID, BUILD_DIGEST, "latest", "pushed_harbor_digest_invalid"),
    ),
    ids=(
        "wrong-runner",
        "missing-job-digest",
        "digest-mismatch",
        "malformed-harbor-digest",
    ),
)
def test_job_and_digest_readback_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    runner_id: int,
    artifact_digest: str,
    harbor_digest: Optional[str],
    expected: str,
) -> None:
    """Reject any break in the job-runner-artifact-Harbor identity chain.

    :param monkeypatch: Fixture replacing the bounded GitLab job listing.
    :param runner_id: Runner identity attached to the selected job.
    :param artifact_digest: Digest emitted by the selected job artifact.
    :param harbor_digest: Digest independently read from Harbor.
    :param expected: Stable failure classification expected from verification.
    :returns: None; verification never retains a passing read-back.
    """
    action = _prepared_action(apply=True)
    api = _bind_verification_state(
        action,
        artifact_digest=artifact_digest,
        harbor_digest=harbor_digest,
    )
    monkeypatch.setattr(
        VERIFICATION,
        "list_jobs",
        lambda *_a, **_k: GITLAB_JOBS.JobListResult(
            records=[_job(runner_id)], errors=(), truncated=False
        ),
    )
    callback = VERIFICATION.VerifyBuildJob(action)
    callback.bind(action.state)

    with pytest.raises((GITLAB_ACTIONS.ActionError, ValueError), match=expected):
        callback.run()

    expected_artifact_calls = (
        []
        if runner_id != RUNNER_ID
        else ["projects/42/jobs/501/artifacts/reports/image-digest.txt"]
    )
    assert api.calls == expected_artifact_calls
    assert not hasattr(action.state, "job_readback")


class CallerScopeKubernetes:
    """Reject any attempt to read or remove a caller-owned namespace."""

    def __init__(self, target: TARGET.KubernetesTarget) -> None:
        """Retain target identity and initialize a mutation journal.

        :param target: Bound Kubernetes identity required by shared callbacks.
        """
        self.target = target
        self.calls: list[tuple[str, str, str]] = []

    def read(self, kind: str, name: str, namespace: str) -> None:
        """Fail if cleanup touches a caller-owned namespace.

        :param kind: Unexpected native resource kind.
        :param name: Unexpected resource name.
        :param namespace: Unexpected namespace argument.
        :raises AssertionError: Always; caller scope must be preserved directly.
        """
        self.calls.append((kind, name, namespace))
        raise AssertionError("caller_owned_namespace_was_touched")


def _lifecycle_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str = "digest-mismatch",
    cleanup_failure: bool = False,
) -> tuple[
    ImageBuildChainAction, WorkflowExecutor, list[str], list[str], CallerScopeKubernetes
]:
    """Compose real verification and cleanup around deterministic provider doubles.

    :param tmp_path: Directory receiving the ephemeral runner-token sink.
    :param monkeypatch: Fixture replacing provider mutations at existing boundaries.
    :param outcome: Success, digest mismatch, timeout, SIGINT, or SIGTERM outcome.
    :param cleanup_failure: Make manager removal fail while later cleanup continues.
    :returns: Action, real executor, stage journal, cleanup journal, and fake Kubernetes.
    """
    action = _prepared_action(apply=True)
    state = action.state
    scope = RUN_SCOPE.RunScope(RUN_ID, CALLER_NAMESPACE, False)
    kubernetes = CallerScopeKubernetes(action.target.kubernetes)
    state.kubernetes = kubernetes
    state.resolved_run_id = RUN_ID
    state.found_scopes = (scope,)
    state.selected_node = NODE
    state.pipeline_plan = _pipeline_plan()
    action.args.confirm_plan = action.fingerprint(NODE, state.pipeline_plan)
    stages: list[str] = []
    cleanup: list[str] = []

    def probe(current: ImageBuildChainState) -> None:
        """Retain the probe-selected node and caller-owned run scope."""
        current.selected_node = NODE
        current.run_scope = scope
        current.probe_record = {"verified": True, "node": NODE}

    def toolbox(current: ImageBuildChainState) -> None:
        """Retain the digest-pinned toolbox image published for the manager."""
        assert current.selected_node == NODE
        assert current.run_scope is scope
        current.published_image = TOOLBOX_IMAGE

    def manager(current: ImageBuildChainState) -> None:
        """Retain chain-owned manager, runner, and private token identities."""
        assert current.published_image == TOOLBOX_IMAGE
        current.manager_action = SimpleNamespace(scc_access=lambda: action)
        current.created_runner_id = RUNNER_ID
        current.runner_create_plan = _pipeline_plan()
        current.token_directory = tmp_path / "runner-token"
        current.token_directory.mkdir()
        current.token_path = current.token_directory / "token"
        current.token_path.write_text("private-unit-value\n", encoding="utf-8")

    def pipeline(current: ImageBuildChainState) -> None:
        """Retain exact pipeline services with a mismatching Harbor digest."""
        assert current.created_runner_id == RUNNER_ID
        _bind_verification_state(
            action,
            artifact_digest=BUILD_DIGEST,
            harbor_digest=("sha256:" + "f" * 64)
            if outcome == "digest-mismatch"
            else BUILD_DIGEST,
        )
        assert current is action.state
        if outcome == "timeout":
            raise TimeoutError("pipeline_watch_timeout token=private-unit-value")
        if outcome in ("SIGINT", "SIGTERM"):
            signal.raise_signal(getattr(signal, outcome))

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
        """Record verified deletion through the existing runner-cleanup seam."""
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
    monkeypatch.setattr(
        VERIFICATION,
        "list_jobs",
        lambda *_a, **_k: GITLAB_JOBS.JobListResult(
            records=[_job()], errors=(), truncated=False
        ),
    )
    callbacks = ABSTRACT_CALLBACKS.Callbacks(
        [
            VERIFICATION.CleanupChain(action),
            CALLBACKS.ResolveChainPlan(action),
            JournalCallback(action, stages, "probe", probe),
            JournalCallback(action, stages, "toolbox", toolbox),
            JournalCallback(action, stages, "manager", manager),
            JournalCallback(action, stages, "pipeline", pipeline),
            VERIFICATION.VerifyBuildJob(action),
        ]
    )
    executor = EXECUTOR.WorkflowExecutor(state, callbacks)
    assert all(callback.state is state for callback in executor.callbacks)

    return action, executor, stages, cleanup, kubernetes


@pytest.mark.parametrize(
    "outcome", ("success", "digest-mismatch", "timeout", "SIGINT", "SIGTERM")
)
def test_chain_cleanup_runs_on_success_failure_timeout_and_signals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """Remove owned resources and preserve caller scope on every execution exit.

    :param tmp_path: Directory receiving the private runner-token sink.
    :param monkeypatch: Fixture replacing external provider operations.
    :param outcome: Final verification success, provider failure, timeout, or signal.
    :returns: None after checking cleanup side effects and restored signal handlers.
    """
    action, executor, stages, cleanup, kubernetes = _lifecycle_executor(
        tmp_path, monkeypatch, outcome
    )
    previous = {
        number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)
    }
    if outcome == "success":
        executor.run()
        assert action.state.job_readback["verified"] is True
    elif outcome in ("SIGINT", "SIGTERM"):
        with pytest.raises(SystemExit) as interrupted:
            executor.run()
        assert interrupted.value.code == 128 + getattr(signal, outcome)
    else:
        expected = (
            "build_job_digest_mismatch"
            if outcome == "digest-mismatch"
            else "pipeline_watch_timeout"
        )
        with pytest.raises((GITLAB_ACTIONS.ActionError, TimeoutError), match=expected):
            executor.run()
    state = action.state
    assert stages == ["probe", "toolbox", "manager", "pipeline"]
    assert cleanup == ["manager", "scc", "runner"]
    assert state.chain_cleanup["status"] == "PASS"
    scope_cleanup = next(
        item
        for item in state.chain_cleanup["resources"]
        if item.get("callback") == "RemoveScope"
    )
    assert scope_cleanup["preserved_scopes"] == [state.run_scope.as_dict()]
    assert kubernetes.calls == []
    assert not state.token_path.exists()
    assert not state.token_directory.exists()
    assert {number: signal.getsignal(number) for number in previous} == previous
