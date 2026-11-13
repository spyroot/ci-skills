"""Consumer naming propagation, identity preservation, and confirmation contracts.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from argparse import Namespace
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Final
from unittest.mock import Mock

import pytest
import test_callbacks as callback_tests
import test_ci15_contracts as contract_tests
import test_gitlab_runner_lifecycle as runner_tests
import test_gitlab_runner_manager as manager_tests
import test_image_build_chain as chain_tests
import test_podman_readiness as probe_tests
from conftest import import_script_module
from test_ci15_contracts import (
    repository_inputs as repository_inputs,  # noqa: PLC0414 - pytest fixture import
)
from test_tool_pod import MemoryCluster

GITHUB = import_script_module("create_selfhosted_runner")
CONSUMERS: Final = contract_tests.PROJECT_NAME_CONSUMERS
PROJECT_NAME_ARGUMENT: Final = contract_tests.PROJECT_NAME_ARGUMENT
OVERRIDE: Final = CONSUMERS[0][1]
ALTERNATE_OVERRIDE: Final = CONSUMERS[1][1]
DEPLOY: Final = "deploy"
PROBE: Final = "probe"
RUNNER_KIND: Final = runner_tests.RUNNERS.RUNNER_KIND
FIELD_PROJECT_NAME: Final = "project_name"
PARSER_CASES: Final = (
    (GITHUB, contract_tests.CREATE_ACTION),
    (contract_tests.MANAGER, DEPLOY),
    (runner_tests.RUNNER, contract_tests.CREATE_ACTION),
    (contract_tests.READINESS, PROBE),
    (contract_tests.READINESS, contract_tests.CHAIN_COMMAND),
)


@pytest.mark.parametrize(("command", "verb"), PARSER_CASES)
@pytest.mark.parametrize("conflicting", (False, True), ids=("equal", "conflicting"))
def test_project_name_all_parsers_share_singular_option_semantics(
    command: object, verb: str, conflicting: bool
) -> None:
    """Every producer accepts equal repeats and rejects a conflicting naming base.

    :param command: Existing public command module supplying its real parser.
    :param verb: Selected command operation.
    :param conflicting: Sole mutation: the value of the second option occurrence.
    """
    baseline = (verb, PROJECT_NAME_ARGUMENT, OVERRIDE, PROJECT_NAME_ARGUMENT, OVERRIDE)
    arguments = [*baseline]
    if conflicting:
        arguments[-1] = ALTERNATE_OVERRIDE
        with pytest.raises(SystemExit) as rejected:
            command.build_parser().parse_args(arguments)
        assert rejected.value.code == 2
    else:
        assert command.build_parser().parse_args(arguments).project_name == OVERRIDE
    assert baseline[-1] == OVERRIDE


@pytest.mark.parametrize(("reference", "project_name"), CONSUMERS)
@pytest.mark.parametrize("explicit", (False, True), ids=("defaults", "exact-fields"))
def test_project_name_manager_producer_preserves_provider_and_explicit_fields(
    monkeypatch: pytest.MonkeyPatch,
    repository_inputs: Namespace,
    reference: str,
    project_name: str,
    explicit: bool,
) -> None:
    """One consumer base reaches manager names without changing its GitLab target.

    :param monkeypatch: Bind the existing offline target resolver.
    :param repository_inputs: Existing explicit field and operation identity fixture.
    :param reference: Actual GitLab project selected by the caller.
    :param project_name: Consumer-specific naming base.
    :param explicit: Whether exact namespace, description and tags must survive.
    """
    selected = repository_inputs
    args = manager_tests._args(dry_run=True)
    args.project = reference
    args.project_name = project_name
    args.description = selected.override if explicit else None
    args.tag = list(selected.labels) if explicit else []
    args.namespace = selected.namespace if explicit else None
    args.run_id = selected.run_id
    monkeypatch.setattr(
        manager_tests.ACTION,
        manager_tests.ACTION.resolve_target.__name__,
        Mock(return_value=manager_tests._target()),
    )
    action = manager_tests.ACTION.GitLabRunnerManagerAction(args)

    action.prepare()

    assert action.repository_identity.reference == reference
    assert action.description == (selected.override if explicit else project_name)
    assert action.labels == (selected.labels if explicit else (project_name,))
    scope = action.candidate_scope
    assert scope is not None
    assert action.workload().runner(scope).name.startswith(project_name + "-")
    if explicit:
        assert scope.namespace == selected.namespace
    else:
        assert scope.namespace.startswith(project_name + "-")


@pytest.mark.parametrize(("reference", "project_name"), CONSUMERS)
@pytest.mark.parametrize("explicit", (False, True), ids=("defaults", "exact-fields"))
def test_project_name_gitlab_runner_creation_and_confirmation_keep_real_target(
    tmp_path: Path,
    repository_inputs: Namespace,
    reference: str,
    project_name: str,
    explicit: bool,
) -> None:
    """Create fields use the consumer base and hidden override changes invalidate plans.

    :param tmp_path: Test-owned path for the unused one-time token destination.
    :param repository_inputs: Existing explicit description and tag fixture.
    :param reference: Actual provider project which the override cannot replace.
    :param project_name: Consumer-specific naming base supplied through the real parser.
    :param explicit: Whether description and tags suppress generated defaults.
    """
    selected = repository_inputs
    target = manager_tests._target()
    operation_target = contract_tests.TARGET.GitLabOperationTarget(
        target.gitlab, target.source_file
    )
    baseline = (
        contract_tests.CREATE_ACTION,
        contract_tests.RUNNER_TYPE_ARGUMENT,
        contract_tests.PROJECT_KIND,
        contract_tests.PROJECT_ARGUMENT,
        reference,
        contract_tests.TOKEN_OUT_ARGUMENT,
        str(tmp_path / selected.override),
        PROJECT_NAME_ARGUMENT,
        project_name,
    )
    arguments = [*baseline]
    if explicit:
        arguments.extend((contract_tests.DESCRIPTION_ARGUMENT, selected.override))
        for tag in selected.labels:
            arguments.extend((contract_tests.TAG_ARGUMENT, tag))
    parsed = runner_tests.RUNNER.build_parser().parse_args(arguments)
    plan = runner_tests.ACTION.make_plan(RUNNER_KIND, parsed, operation_target, "unit")

    assert plan.target_reference == reference
    assert plan.body[contract_tests.FIELD_DESCRIPTION] == (
        selected.override if explicit else project_name
    )
    assert plan.body[contract_tests.FIELD_TAGS] == ",".join(
        selected.labels if explicit else (project_name,)
    )
    parsed.project_name = project_name + "-changed"
    changed = runner_tests.ACTION.make_plan(
        RUNNER_KIND, parsed, operation_target, "unit"
    )
    assert changed.digest != plan.digest
    assert baseline[-1] == project_name
    assert list(tmp_path.iterdir()) == []


def test_project_name_github_confirmation_binds_override_behind_exact_fields(
    repository_inputs: Namespace,
) -> None:
    """Changing the naming base invalidates confirmation even with identical resources.

    :param repository_inputs: Existing explicit name, namespace and tags fixture.
    """
    selected = repository_inputs
    action = contract_tests.WORKFLOWS.WorkflowAction(
        Namespace(
            action=contract_tests.CREATE_ACTION,
            kind=contract_tests.GITHUB_KIND,
            name=selected.override,
            namespace=selected.namespace,
            label=list(selected.labels),
            runner_root=selected.runner_root,
            timeout=contract_tests.COMMAND_TIMEOUT,
            project_name=OVERRIDE,
        )
    )
    action.target = replace(
        manager_tests._target(),
        github=contract_tests.TARGET.GitHubTarget(selected.host, selected.repository),
    )
    action._workload()
    runner = action.runner
    digest = action.fingerprint()

    action.args.project_name = ALTERNATE_OVERRIDE
    action._workload()

    assert action.runner == runner
    assert action.fingerprint() != digest
    assert action.repository_identity.reference == selected.repository


@pytest.mark.parametrize(("reference", "project_name"), CONSUMERS)
def test_project_name_chain_forwards_naming_to_existing_runner_and_manager_adapters(
    tmp_path: Path, reference: str, project_name: str
) -> None:
    """One outer naming choice reaches both producers while explicit toolbox fields survive.

    :param tmp_path: Unused test-owned path for the runner token adapter.
    :param reference: Actual provider project retained through both adapters.
    :param project_name: Outer consumer naming base.
    """
    action = chain_tests._prepared_action()
    action.gitlab_selection = (contract_tests.PROJECT_KIND, reference)
    action.repository_identity = contract_tests.IDENTITY.resolve_repository(reference)
    action.args.project = reference
    action.args.project_name = project_name
    action.args.namespace = None
    specification = vars(action.toolbox.spec).copy()
    action._prepare_scope()
    state = action.state
    state.created_runner_id = manager_tests.RUNNER_ID
    state.published_image = manager_tests.IMAGE
    state.selected_node = chain_tests.NODE
    state.token_path = tmp_path / manager_tests.RUN_ID

    runner = chain_tests.CALLBACKS._runner_args(action, state.token_path)
    manager = chain_tests.CALLBACKS._manager_args(action, state)

    assert runner.project_name == manager.project_name == project_name
    assert runner.project == manager.project == reference
    assert action.manager_scope.namespace.startswith(project_name + "-")
    assert action.resolved_runner_name.value.startswith(project_name + "-")
    assert vars(action.toolbox.spec) == specification
    assert action.repository_identity.reference == reference
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "previous_project_name",
    (None, probe_tests.RUN_SCOPE.RUN_SCOPE_PREFIX, ALTERNATE_OVERRIDE),
)
def test_project_name_manager_reuses_native_identity_after_override_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    previous_project_name: str | None,
) -> None:
    """Repository, skill-era and prior consumer names retain native names and UIDs.

    :param monkeypatch: Bind the existing full manager command lifecycle harness.
    :param tmp_path: Existing harness token and lock directory.
    :param capsys: Capture real schema-validated plan/apply command reports.
    :param previous_project_name: Previous repository default or selected naming scheme.
    """
    cluster = manager_tests._manager_cli_harness(monkeypatch, tmp_path)
    observed = None
    for project_name in (previous_project_name, OVERRIDE):
        naming = () if project_name is None else (PROJECT_NAME_ARGUMENT, project_name)
        code, plan = manager_tests._manager_cli_report(
            cluster, capsys, run_id=manager_tests.RUN_ID, flags=naming
        )
        assert code == manager_tests.STATUS.exit_code(manager_tests.STATUS.PLANNED)
        code, result = manager_tests._manager_cli_report(
            cluster,
            capsys,
            run_id=manager_tests.RUN_ID,
            flags=(
                *naming,
                manager_tests.APPLY_ARGUMENT,
                manager_tests.CONFIRM_ARGUMENT,
                plan[manager_tests.FIELD_PLAN_DIGEST],
            ),
        )
        assert code == manager_tests.STATUS.exit_code(manager_tests.STATUS.PASS)
        if observed is None:
            observed = deepcopy(cluster.objects)
            cluster.applied.clear()
            cluster.deleted.clear()
        else:
            assert cluster.objects == observed
            assert not any(
                kind in {"deployment", "namespace"} for kind, _, _ in cluster.applied
            )
            assert not set(cluster.deleted).intersection(observed)
            deployment = next(
                record
                for record in result[manager_tests.FIELD_RECORDS]
                if record[manager_tests.FIELD_CALLBACK]
                == manager_tests.WORKLOAD.DeployManagerRunner.__name__
            )
            assert deployment["action"] == manager_tests.STATUS.NO_OP
    assert not cluster.guard_held


@pytest.mark.parametrize(
    "previous_prefix", (None, contract_tests.PROJECT_KIND, ALTERNATE_OVERRIDE)
)
def test_project_name_scc_preserves_verified_binding_then_cleans_its_native_name(
    tmp_path: Path, previous_prefix: str | None
) -> None:
    """A naming change neither duplicates a verified grant nor loses its cleanup identity.

    :param tmp_path: Existing Kubernetes-only target fixture location.
    :param previous_prefix: Legacy, repository or prior consumer support-name prefix.
    """
    service = probe_tests.FakeKubernetes(
        probe_tests._target(tmp_path), scc_available=True
    )
    scope = probe_tests.RUN_SCOPE.RunScope.new(
        namespace=manager_tests.NAMESPACE, run_id=manager_tests.RUN_ID
    )
    scope.cluster_resources(service).apply(
        probe_tests.RUN_SCOPE.namespace_document(replace(scope, owns_namespace=True))
    )
    previous = replace(
        probe_tests._action(tmp_path).scc_access(), name_prefix=previous_prefix
    )
    document = probe_tests.SCC_ACCESS.role_binding_document(previous, scope)
    resources = scope.resources(service)
    resources.apply(document)
    native_name = document["metadata"]["name"]
    protected = deepcopy(service.resources)
    service.mutations.clear()
    selected = replace(previous, name_prefix=OVERRIDE)
    state = probe_tests.PODMAN_CALLBACKS.PodmanProbeState()
    state.kubernetes = service
    state.run_scope = scope
    ensure = probe_tests.SCC_ACCESS.EnsureSCCAccess(selected)
    ensure.bind(state)

    ensure.run()

    assert (
        probe_tests.SCC_ACCESS.resolve_role_binding_name(selected, scope, service)
        == native_name
    )
    assert service.resources == protected
    assert service.mutations == service.deletions == []
    remove = probe_tests.SCC_ACCESS.RemoveSCCAccess(selected)
    remove.bind(state)
    remove.run()
    assert service.deletions == [("RoleBinding", native_name, scope.namespace)]
    assert service.mutations == []


@pytest.mark.parametrize("project_name", (None, OVERRIDE, ALTERNATE_OVERRIDE))
def test_project_name_probe_uses_only_kubernetes_and_cleans_consumer_resources(
    tmp_path: Path, project_name: str | None
) -> None:
    """Probe resources use the caller base without adding GitLab or GitHub prerequisites.

    :param tmp_path: Existing Kubernetes-only target fixture location.
    :param project_name: Explicit consumer base or omitted unknown-identity legacy case.
    """
    planned = replace(probe_tests._action(tmp_path), project_name=project_name)
    action = replace(
        planned,
        mode=probe_tests.PODMAN.ProbeMode.APPLY,
        confirm_plan=planned.fingerprint("worker-a"),
    )
    service = probe_tests.FakeKubernetes(action.target, scc_available=True)
    protected = deepcopy(service.resources)
    executor = probe_tests._executor(action, service)

    executor.run()

    assert action.target.gitlab is action.target.github is action.target.harbor is None
    expected = project_name or probe_tests.RUN_SCOPE.RUN_SCOPE_PREFIX
    assert service.mutations
    assert all(name.startswith(expected + "-") for _, name, _ in service.mutations)
    assert service.resources == protected
    assert probe_tests._reported_apply_status(executor.state) == probe_tests.STATUS.PASS


@pytest.mark.parametrize("invalid", ("", "  ", "\n"))
def test_project_name_probe_rejects_blank_even_with_explicit_namespace(
    tmp_path: Path, invalid: str
) -> None:
    """A selected namespace cannot suppress validation of the supplied naming base.

    :param tmp_path: Existing Kubernetes-only action fixture location.
    :param invalid: Sole mutation to a valid immutable probe naming baseline.
    """
    baseline = MappingProxyType(
        {FIELD_PROJECT_NAME: OVERRIDE, "namespace": "caller-probe-namespace"}
    )
    changed = dict(baseline)
    changed[FIELD_PROJECT_NAME] = invalid
    with pytest.raises(ValueError, match=PROJECT_NAME_ARGUMENT):
        replace(probe_tests._action(tmp_path), **changed)
    assert baseline[FIELD_PROJECT_NAME] == OVERRIDE


@pytest.mark.parametrize(
    "changed",
    (None, "ownership", "fingerprint", "image", "digest-format", "registry-digest"),
)
def test_project_name_build_reuse_keeps_every_original_proof_requirement(
    changed: str | None,
) -> None:
    """Renaming support must not turn a partially verified BuildConfig into image reuse.

    :param changed: Sole invariant mutated from the immutable valid native build baseline.
    """
    toolbox = callback_tests.TOOLBOX
    spec = chain_tests._prepared_action().toolbox.spec
    digest = "sha256:" + "a" * 64
    lookup = Mock(return_value=digest)
    action = toolbox.BuildAction(
        spec=spec,
        resolved_base_image=spec.base_image,
        candidate_repository="registry.example.test/consumer/toolbox",
        candidate_reference="consumer-build-candidate",
        dockerconfig=b'{"auths":{"registry.example.test":{}}}',
        digest_lookup=lookup,
    )
    cluster = MemoryCluster()
    baseline = json.dumps(
        callback_tests.RESOURCES.ResourceSet(
            cluster, spec.namespace, toolbox.MANAGED_VALUE
        ).managed(
            {
                "apiVersion": toolbox.BUILD_API_VERSION,
                "kind": "BuildConfig",
                "metadata": {
                    "name": spec.app,
                    "namespace": spec.namespace,
                    "uid": "consumer-buildconfig-uid",
                    "annotations": {
                        toolbox.FINGERPRINT_ANNOTATION: action.fingerprint,
                        toolbox.IMAGE_ANNOTATION: action.image,
                        toolbox.DIGEST_ANNOTATION: digest,
                    },
                },
            }
        ),
        sort_keys=True,
    )
    observed = json.loads(baseline)
    metadata = observed["metadata"]
    annotations = metadata["annotations"]
    if changed == "ownership":
        metadata["labels"][callback_tests.RESOURCES.OPERATION_LABEL] = "other-build"
    elif changed == "fingerprint":
        annotations[toolbox.FINGERPRINT_ANNOTATION] = "b" * 64
    elif changed == "image":
        annotations[toolbox.IMAGE_ANNOTATION] = action.image + "-other"
    elif changed == "digest-format":
        annotations[toolbox.DIGEST_ANNOTATION] = "sha256:invalid"
    elif changed == "registry-digest":
        lookup.return_value = "sha256:" + "c" * 64
    cluster.objects[("buildconfig", spec.namespace, spec.app)] = observed
    protected = deepcopy(cluster.objects)
    callback = toolbox.BuildToolbox(action)
    state = callback_tests.DELIVERY_STATE.DeliveryState()
    state.kubernetes = cluster
    callback.bind(state)

    if changed == "ownership":
        with pytest.raises(
            toolbox.ToolboxError, match="buildconfig_ownership_conflict"
        ):
            callback._reusable()
    else:
        result = callback._reusable()
        assert bool(result) is (changed is None)
        if result:
            assert result["reused"] is result["verified"] is True
            assert result["digest"] == digest
    assert cluster.objects == protected
    assert cluster.applied == cluster.deleted == []
