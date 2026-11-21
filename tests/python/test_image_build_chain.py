"""Focused orchestration contracts for the CI13 image-build chain.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
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
CHAIN_MODEL = import_script_module("lib.python.core.kubernetes.image_build_chain.model")
EXECUTOR = import_script_module("lib.python.core.executor")
GITLAB_ACTIONS = import_script_module("lib.python.core.gitlab.gitlab_actions")
MANAGER_ACTION = import_script_module("lib.python.core.gitlab.runner_manager.action")
PODMAN = import_script_module("lib.python.core.kubernetes.podman_probe")
READINESS = import_script_module("lib.python.core.kubernetes.image_build_readiness")
RUN_SCOPE = import_script_module("lib.python.core.kubernetes.run_scope")
SCOPES = import_script_module("lib.python.core.runner_scope")
IDENTITY = import_script_module("lib.python.core.runner_identity")
TARGET = import_script_module("lib.python.core.target")
STATUS = import_script_module("lib.python.sys.status")
REGISTRY = import_script_module("lib.python.core.harbor.registry_mirror")
SCHEMA = import_script_module("lib.python.core.schema")
VERIFICATION = import_script_module(
    "lib.python.core.kubernetes.image_build_chain.verification"
)
WORKFLOW = import_script_module("lib.python.core.kubernetes.image_build_chain.workflow")
ENDPOINTS = import_script_module("lib.python.core.endpoints")

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
PROJECT_ID: Final = 42
PROJECT_PATH: Final = "team/build"
NAMESPACE_KIND: Final = "Namespace"
CLUSTER_NAMESPACE: Final = ""
RUNNER_ID = 73
DEFAULT_CONSUMER: Final = "ci"
SAMPLE_NAME: Final = "sample_project"
SAMPLE_PROJECT: Final = f"team/{SAMPLE_NAME}"
OVERRIDE_NAME: Final = "selected_project"
LEGACY_BUILD_NAMESPACE: Final = "toolbox-builds"
LEGACY_BUILD_APP: Final = "toolbox-build"
LEGACY_TOOLBOX_TAG: Final = "curated"
REFERENCE_REPOSITORY: Final = "reference-toolbox"
REFERENCE_TAG: Final = "stable"
REFERENCE_TOOLBOX: Final = f"{REFERENCE_REPOSITORY}:{REFERENCE_TAG}"
HARBOR_HOST: Final = "harbor.example.test"
HARBOR_PROJECT: Final = "toolbox"
HARBOR_ORIGIN: Final = f"https://{HARBOR_HOST}"
TOOLBOX_DIGEST: Final = runner_manager.IMAGE.rsplit("@", 1)[-1]
TOOLBOX_IMAGE: Final = runner_manager.IMAGE
CHAIN_REPORT_SCHEMA: Final = "results/image-build-readiness.schema.json"


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

        :param *args: Native Kubernetes observation requested by a real callback.
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
    project_name: str | None = DEFAULT_CONSUMER,
) -> Namespace:
    """Parse a runner-delivery request without consumer job inputs.

    :param group: Select a group runner.
    :param apply: Select confirmed mutation mode.
    :param confirmation: Digest supplied to confirmed apply.
    :param timeout: Optional caller-selected operation bound in seconds.
    :param project_name: Consumer naming override to pass to the parser when set.
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
            *scope,
            *(["--project-name", project_name] if project_name is not None else []),
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
        active_surfaces=(
            ENDPOINTS.GITLAB_AUTHORITY,
            ENDPOINTS.KUBERNETES_AUTHORITY,
            ENDPOINTS.HARBOR_AUTHORITY,
        ),
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
            url=HARBOR_ORIGIN,
            host=HARBOR_HOST,
            project=HARBOR_PROJECT,
            reference_toolbox=REFERENCE_TOOLBOX,
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


def _mock_chain_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return the same credential-free configured target for both chain actions.

    :param monkeypatch: Redirect only target and credential resolution.
    """
    for module in (CHAIN_MODEL, MANAGER_ACTION):
        monkeypatch.setattr(module, "resolve_target", lambda *_a, **_k: _target())
        monkeypatch.setattr(module, "bind_sources", lambda target, **_k: target)


def _prepared_action(
    *,
    group: bool = False,
    apply: bool = False,
    confirmation: Optional[str] = None,
) -> ImageBuildChainAction:
    """Prepare exact runner and reference-toolbox defaults without provider access.

    :param group: Select the group-runner case.
    :param apply: Select apply mode for confirmation tests.
    :param confirmation: Apply confirmation supplied by the caller.
    :returns: Chain action ready for planning and callback tests.
    """
    action = CHAIN.ImageBuildChainAction(
        _args(group=group, apply=apply, confirmation=confirmation)
    )
    mode = PODMAN.ProbeMode.APPLY if apply else PODMAN.ProbeMode.LIVE_PLAN
    with pytest.MonkeyPatch.context() as monkeypatch:
        _mock_chain_targets(monkeypatch)
        action.prepare(mode)
    return action


class ChainHarbor(REGISTRY.HarborService):
    """Record native service calls below the real digest and publication callbacks."""

    def __init__(self, staging: Path, journal: list[str]) -> None:
        """Bind the configured fixture registry and start with no destination tags.

        :param staging: Test directory for the non-live registry credential path.
        :param journal: Ordered executor stage observations.
        """
        super().__init__(
            _target().harbor,
            REGISTRY.RegistryCredential("unit", "fixture", "fixture"),
            staging / "auth.json",
            30,
        )
        self.journal = journal
        self.tags: dict[str, str] = {}
        self.copies: list[tuple[str, str]] = []
        self.reads: list[tuple[str, str | None]] = []

    def inspect(self, image: str) -> str:
        """Observe the curated source tag or its immutable digest.

        :param image: Registry reference submitted by the chain.
        :returns: Configured test artifact digest.
        """
        assert image in {
            self.image(REFERENCE_REPOSITORY, REFERENCE_TAG),
            self.image(REFERENCE_REPOSITORY, TOOLBOX_DIGEST),
        }
        return TOOLBOX_DIGEST

    def digest(self, repository: str, reference: str) -> str | None:
        """Independently read the currently published tag.

        :param repository: Destination repository selected by the callback.
        :param reference: Destination tag selected by the callback.
        :returns: Published digest or absence.
        """
        destination = self.image(repository, reference)
        observed = self.tags.get(destination)
        self.reads.append((destination, observed))
        return observed

    def copy(self, source: str, destination: str) -> str:
        """Publish the observed source artifact under a distinct destination.

        :param source: Digest-pinned source selected by live confirmation.
        :param destination: Fully qualified mutable destination tag.
        :returns: Digest copied by the registry transport.
        """
        self.journal.append("harbor-push")
        self.copies.append((source, destination))
        digest = self.inspect(source)
        self.tags[destination] = digest
        return digest

    def delete_tag(self, repository: str, tag: str) -> bool:
        """Remove the tag created by a failed fixture execution.

        :param repository: Destination repository selected by recovery.
        :param tag: Tag selected for rollback.
        :returns: Whether the tag was present before removal.
        """
        return self.tags.pop(self.image(repository, tag), None) is not None


def _mock_chain_auth(
    action: ImageBuildChainAction, monkeypatch: pytest.MonkeyPatch, journal: list[str]
) -> None:
    """Provide auth callbacks for already bound in-memory provider services.

    :param action: Chain carrying the seeded Kubernetes, GitLab, and Harbor providers.
    :param monkeypatch: Replace only auth callbacks at their workflow entry points.
    :param journal: Ordered observations for later mutation assertions.
    """
    for component, stage in (
        ("K8SAuth", "kubernetes-auth"),
        ("ScopedGitLabAuth", "runner-auth"),
        ("HarborAuth", "harbor-auth"),
    ):
        monkeypatch.setattr(
            WORKFLOW,
            component,
            lambda *_a, stage=stage, **_k: JournalCallback(action, journal, stage),
        )


LEGACY_SPEC_BASELINE: Final = json.dumps(
    {
        "apiVersion": "ci-skills/v1",
        "kind": "ToolboxSpec",
        "metadata": {"name": SAMPLE_NAME},
        "spec": {
            "baseImage": "reference.example.test/unused:base",
            "image": {"repository": SAMPLE_NAME, "tag": LEGACY_TOOLBOX_TAG},
            "build": {"namespace": LEGACY_BUILD_NAMESPACE, "app": LEGACY_BUILD_APP},
            "dependencies": [],
            "requiredCommands": ["gitlab-runner", "podman", "git"],
        },
    }
)


def _sample_chain_plan(
    args: Namespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    status: str,
) -> tuple[JSONObject, ChainCluster, ChainHarbor]:
    """Capture a schema-checked offline or live plan using real chain callbacks.

    :param args: Parsed planner inputs.
    :param monkeypatch: Replace only external access boundaries.
    :param tmp_path: Test directory holding the dummy registry authfile path.
    :param capsys: Capture report emitted by the chain command.
    :param status: Expected planner status.
    :returns: Report with the in-memory Kubernetes and Harbor transports.
    """
    chain = CHAIN.ImageBuildChain(args)
    _mock_chain_targets(monkeypatch)
    stages: list[str] = []
    _mock_chain_auth(chain.action, monkeypatch, stages)
    state = chain.action.state
    cluster = ChainCluster()
    harbor = ChainHarbor(tmp_path, stages)
    state.kubernetes = cluster
    state.harbor = harbor
    state.access = _verified_project_access(SAMPLE_PROJECT)
    state.gitlab = SimpleNamespace(session=object(), access=state.access)
    code = chain.execute()
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == status
    assert code == STATUS.exit_code(status)
    SCHEMA.specification_validator(CHAIN_REPORT_SCHEMA).validate(report)
    assert harbor.copies == []
    assert cluster.applied == cluster.deleted == []
    if status == STATUS.DRY_RUN:
        assert stages == []
        assert harbor.reads == []
    return report, cluster, harbor


@pytest.mark.parametrize(
    ("project", "override", "legacy_spec"),
    (
        (SAMPLE_PROJECT, None, False),
        (SAMPLE_PROJECT, OVERRIDE_NAME, False),
        (str(PROJECT_ID), None, False),
        (SAMPLE_PROJECT, None, True),
    ),
    ids=(
        "repository-fallback",
        "explicit-project-name",
        "numeric-readback",
        "legacy-spec",
    ),
)
def test_chain_plans_one_consumer_name_without_rebuilding_the_reference(
    project: str,
    override: str | None,
    legacy_spec: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Use one resolved name for GitLab and Harbor with DNS-safe Kubernetes defaults.

    :param project: Provider project path or numeric selector.
    :param override: Explicit consumer name when supplied.
    :param legacy_spec: Supply fixed destination tag and legacy build metadata.
    :param monkeypatch: Isolate all provider configuration.
    :param tmp_path: Temporary location of the explicit ToolboxSpec.
    :param capsys: Capture the command's structured reports.
    """
    args = _args(project_name=override)
    args.project = project
    args.namespace = CALLER_NAMESPACE if legacy_spec else None
    spec_path = tmp_path / "toolbox.json"
    if legacy_spec:
        spec_path.write_text(LEGACY_SPEC_BASELINE, encoding="utf-8")
        args.toolbox_spec = str(spec_path)
        args.runner_label = [SAMPLE_NAME, "podman"]

    if project.isdecimal():
        offline_args = deepcopy(args)
        offline_args.live_plan = False
        offline_args.dry_run = True
        pending, _, _ = _sample_chain_plan(
            offline_args, monkeypatch, tmp_path, capsys, STATUS.DRY_RUN
        )
        assert pending["confirmable"] is False
        assert pending["plan"]["consumer_name"] is None
        assert pending["plan"]["toolbox"]["repository"] is None
        assert pending["plan"]["runner"]["run_scope"] is None

    result, _, harbor = _sample_chain_plan(
        args, monkeypatch, tmp_path, capsys, STATUS.PLANNED
    )
    assert result["confirmable"] is True
    plan = result["plan"]
    expected = override or SAMPLE_NAME
    toolbox, runner = plan["toolbox"], plan["runner"]
    assert plan["consumer_name"] == expected
    assert plan["naming_conflicts"] == []
    assert runner["reference"] == project
    assert runner["repository"] == SAMPLE_PROJECT
    assert runner["description"] == toolbox["repository"] == expected
    assert toolbox["mode"] == "copy"
    assert toolbox["origin"] == HARBOR_ORIGIN
    assert toolbox["project"] == HARBOR_PROJECT
    assert toolbox["source_image"] == harbor.image(REFERENCE_REPOSITORY, TOOLBOX_DIGEST)
    assert runner["name"].startswith(expected.replace("_", "-") + "-")
    if legacy_spec:
        assert toolbox["tag"] == LEGACY_TOOLBOX_TAG
        assert toolbox["namespace"] == LEGACY_BUILD_NAMESPACE
        assert toolbox["app"] == LEGACY_BUILD_APP
        assert runner["labels"] == [expected, "podman"]
        assert runner["run_scope"]["namespace"] == CALLER_NAMESPACE
        assert runner["scope_observations"][0]["uid"] == CALLER_NAMESPACE_UID
        assert spec_path.read_text(encoding="utf-8") == LEGACY_SPEC_BASELINE
    else:
        assert toolbox["tag"] == expected
        assert runner["labels"] == [expected]
        assert runner["run_scope"]["namespace"].startswith(
            expected.replace("_", "-") + "-"
        )


@pytest.mark.parametrize(
    "field", ("runner.description", "runner.labels", "toolbox.repository")
)
def test_chain_live_plan_reports_explicit_naming_conflict_without_mutation(
    field: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Vary one explicit name from the immutable valid planning baseline.

    :param field: Sole naming field changed in this case.
    :param monkeypatch: Isolate registry and cluster provider access.
    :param tmp_path: Directory holding the isolated ToolboxSpec copy.
    :param capsys: Capture complete blocked plan evidence.
    """
    args = _args(project_name=None)
    args.project = SAMPLE_PROJECT
    args.runner_description = SAMPLE_NAME
    args.runner_label = [SAMPLE_NAME]
    spec = json.loads(LEGACY_SPEC_BASELINE)
    conflicting = "other-name"
    explicit: str | list[str] = conflicting
    if field == "runner.description":
        args.runner_description = conflicting
    elif field == "runner.labels":
        explicit = [conflicting]
        args.runner_label = explicit
    else:
        spec["spec"]["image"]["repository"] = conflicting
    spec_path = tmp_path / "toolbox.json"
    spec_before = json.dumps(spec)
    spec_path.write_text(spec_before, encoding="utf-8")
    args.toolbox_spec = str(spec_path)

    result, _, harbor = _sample_chain_plan(
        args, monkeypatch, tmp_path, capsys, STATUS.BLOCKED
    )

    assert result["confirmable"] is False
    assert result["plan"]["naming_conflicts"] == [
        {"field": field, "explicit": explicit, "expected": SAMPLE_NAME}
    ]
    assert any(
        "consumer_naming_conflict" in error["reason"] for error in result["errors"]
    )
    assert harbor.reads == []
    assert spec_path.read_text(encoding="utf-8") == spec_before


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
    confirmation: str, tmp_path: Path
) -> None:
    """Stop the shared executor before publication or manager writes.

    :param confirmation: Missing or stale confirmation supplied by the caller.
    :param tmp_path: Test directory for the registry authfile stub.
    :returns: None; the mutation callback remains unentered after confirmation fails.
    """
    action = _prepared_action(apply=True)
    action.args.confirm_plan = confirmation
    action.state.selected_node = NODE
    action.state.kubernetes = ChainCluster()
    mutations: list[str] = []
    action.state.harbor = ChainHarbor(tmp_path, mutations)
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
    state.gitlab = SimpleNamespace(access=_verified_project_access())
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
        target_id=PROJECT_ID,
        access=_verified_project_access(),
    )
    monkeypatch.setattr(
        runner_manager.GITLAB_API, "_lock_root", lambda: tmp_path / "locks"
    )
    stages: list[str] = []
    cleanup: list[str] = []
    state.harbor = ChainHarbor(tmp_path, stages)
    _mock_chain_auth(action, monkeypatch, stages)
    _mock_chain_targets(monkeypatch)
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
    # The read-only plan pins the source before the apply workflow can publish.
    source = WORKFLOW.ConfirmWorkflow(action.toolbox)
    source.bind(state)
    source.run()
    planner = CALLBACKS.ResolveChainPlan(action)
    planner.bind(state)
    planner.run()
    action.mode = PODMAN.ProbeMode.APPLY
    action.args.confirm_plan = state.chain_plan_digest

    def manager(current: ImageBuildChainState) -> None:
        """Stand in for a verified native GitLab runner creation.

        :param current: Shared state receiving runner identity and one-time token.
        """
        current.created_runner_id = RUNNER_ID
        current.runner_create_plan = replace(
            _runner_plan(), body={FIELD_DESCRIPTION: action.args.runner_description}
        )
        current.token_directory = tmp_path / uuid4().hex
        current.token_directory.mkdir()
        current.token_path = current.token_directory / uuid4().hex
        current.token_path.write_text("glrt-unit-only\n", encoding="utf-8")
        current.token_path.chmod(0o600)

    def grant_scc(current: ImageBuildChainState) -> None:
        """Retain the existing SCC fixture for the real manager action.

        :param current: Shared state with the selected manager and run scope.
        """
        binding = runner_manager.SCC_ACCESS.role_binding_document(
            current.manager_action.scc_access(), current.run_scope
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
    for component, stage, transition in (
        ("ObserveGitLabRunner", "runner-observe", None),
        ("GitLabRunnerRuntime", "runner-runtime", None),
        ("CreateRunnerRecord", STAGE_MANAGER, manager),
        ("EnsureSCCAccess", STAGE_SCC, grant_scc),
    ):
        monkeypatch.setattr(
            WORKFLOW,
            component,
            lambda *_a, stage=stage, transition=transition, **_k: JournalCallback(
                action, stages, stage, transition
            ),
        )
    for name in (
        "EnsureNamespace",
        "HarborBuild",
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
    assert stages.index("harbor-auth") < stages.index("harbor-push")
    assert stages.index("harbor-push") < stages.index("manager")
    assert stages.index("manager") < stages.index("manager-deploy")
    assert cleanup == ([] if outcome == "success" else ["manager", "scc", "runner"])
    assert state.chain_cleanup["status"] == STATUS.PASS
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
        assert state.manager_action.args.image == TOOLBOX_IMAGE
        deployment = next(
            value
            for (kind, _namespace, _name), value in kubernetes.objects.items()
            if kind == "deployment"
        )
        containers = deployment["spec"]["template"]["spec"]["containers"]
        assert {container["image"] for container in containers} == {TOOLBOX_IMAGE}
        assert {pod["image_id"] for pod in state.deployment_result["pods"]} == {
            TOOLBOX_IMAGE
        }
        harbor = state.harbor
        source = harbor.image(REFERENCE_REPOSITORY, TOOLBOX_DIGEST)
        destination = harbor.image(DEFAULT_CONSUMER, DEFAULT_CONSUMER)
        assert harbor.copies == [(source, destination)]
        assert (destination, TOOLBOX_DIGEST) in harbor.reads
        assert all(
            kind not in {"build", "buildconfig"}
            for kind, _namespace, _name in kubernetes.objects
        )
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
        assert record["action"] == STATUS.APPLIED
        assert record["id"] == RUNNER_ID
        assert record["verified"] is True
        assert record["after"]["global_get_status"] == 404
        assert record["after"]["scoped_absent"] is True
    assert api.calls == expected_calls


def _verified_project_access(project: str = PROJECT_PATH) -> JSONObject:
    """Reuse the native project receipt shape for chain/manager identity binding.

    :param project: Verified provider project path for this fixture.
    :returns: Fresh verified target mapping matching the selected numeric project.
    """
    return {
        IDENTITY.ACCESS_STATUS: STATUS.PASS,
        IDENTITY.ACCESS_TARGET: {
            **runner_lifecycle.VERIFIED_TARGET,
            IDENTITY.TARGET_ID: PROJECT_ID,
            IDENTITY.TARGET_PATH: project,
        },
    }


def test_numeric_project_chain_prepares_inner_manager_from_verified_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retain outer verified identity when the inner manager has no explicit namespace.

    :param tmp_path: Test-owned private runner-token destination.
    :param monkeypatch: Replace target and credential resolution at existing boundaries.
    :returns: None after the real inner manager prepares the same deterministic scope.
    """
    action = _prepared_action(apply=True)
    action.args.project = str(PROJECT_ID)
    action.args.project_name = None
    action.args.namespace = None
    action.gitlab_selection = (IDENTITY.TARGET_PROJECT, str(PROJECT_ID))
    action.repository_identity = IDENTITY.RepositoryIdentity(
        None, IDENTITY.RepositorySource.UNKNOWN
    )
    action.bind_repository(_verified_project_access())
    expected_scope = action.manager_scope
    state = action.state
    state.gitlab = SimpleNamespace(access=_verified_project_access())
    state.selected_node = NODE
    state.published_image = TOOLBOX_IMAGE
    state.created_runner_id = RUNNER_ID
    state.token_path = tmp_path / "numeric-project-runner-token"
    state.token_path.write_text("glrt-unit-only\n", encoding="utf-8")
    state.token_path.chmod(0o600)
    monkeypatch.setattr(MANAGER_ACTION, "resolve_target", lambda *_a, **_k: _target())
    monkeypatch.setattr(MANAGER_ACTION, "bind_sources", lambda target, **_k: target)
    callback = CALLBACKS.PrepareRunnerManager(action)
    callback.bind(state)

    result = callback.run()

    manager = state.manager_action
    assert result["verified"] is True
    assert manager.args.project == str(PROJECT_ID)
    assert manager.args.namespace is None
    assert manager.repository_identity.reference == PROJECT_PATH
    assert manager.repository_identity == action.repository_identity
    assert manager.candidate_scope == expected_scope
    assert manager.resolved_description == action.resolved_description
    assert manager.args.tag == action.args.runner_label
