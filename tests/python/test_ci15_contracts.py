"""Focused repository identity, runner input, and scope evidence contracts.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import subprocess
from argparse import Namespace
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Final
from unittest.mock import Mock
from uuid import uuid4

import pytest
import test_gitlab_runner_lifecycle as runner_tests
import test_gitlab_runner_manager as manager_tests
import test_image_build_chain as chain_tests
from conftest import REPO_ROOT, SCRIPT_ROOT, import_script_module

CLI_API = import_script_module("lib.python.core.cli_api")
IDENTITY = import_script_module("lib.python.core.runner_identity")
JSON_TYPES = import_script_module("lib.python.core.json_types")
OPTIONS = import_script_module("lib.python.core.runner_options")
SCHEMA = import_script_module("lib.python.core.schema")
MANAGER = import_script_module("gitlab_runner_manager")
READINESS = import_script_module("image_build_readiness")
RUN_SCOPE = import_script_module("lib.python.core.kubernetes.run_scope")
RUNNER = import_script_module("lib.python.core.runner")
TARGET = import_script_module("lib.python.core.target")
WORKFLOWS = import_script_module("lib.python.core.toolbox_actions")
STATUS = import_script_module("lib.python.sys.status")
SCOPES = import_script_module("lib.python.core.runner_scope")
K8S_RUNNER = import_script_module("lib.python.core.kubernetes.runner")

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject

INSTALLATION_NAME: Final = SCRIPT_ROOT.parent.name
COMMAND_TIMEOUT: Final = 30
FIELD_STATUS: Final = "status"
FIELD_TARGET: Final = "target"
FIELD_KIND: Final = "kind"
FIELD_ID: Final = "id"
FIELD_PATH: Final = "full_path"
PROJECT_KIND: Final = runner_tests.RUNNERS.PROJECT_KIND
GROUP_KIND: Final = "group"
GITHUB_KIND: Final = "create_selfhosted_runner"
CREATE_ACTION: Final = runner_tests.RUNNERS.RUNNER_CREATE
HOST_SUFFIX: Final = ".invalid"
EMPTY_TEXT: Final = ""
NAME_UNDERSCORE: Final = "_"
NAME_PUNCTUATION: Final = ".!"
NAME_SEPARATOR: Final = "-"
REPOSITORY_SEPARATOR: Final = "/"
FIELD_NAMESPACE: Final = "namespace"
FIELD_RUNNER: Final = "runner"
FIELD_NAME: Final = "name"
FIELD_DESCRIPTION: Final = runner_tests.RUNNERS.RUNNER_DESCRIPTION_FIELD
FIELD_TAGS: Final = runner_tests.RUNNERS.RUNNER_TAGS_FIELD
FIELD_PLAN: Final = "plan"
FIELD_PLAN_DIGEST: Final = "plan_digest"
FIELD_CONFIRMATION_READY: Final = "confirmation_ready"
FIELD_MUTATED: Final = "mutated"
FIELD_ERRORS: Final = "errors"
FIELD_REASON: Final = "reason"
PROJECT_ARGUMENT: Final = "--project"
TOKEN_OUT_ARGUMENT: Final = "--token-out"
LIVE_PLAN_ARGUMENT: Final = "--live-plan"
APPLY_ARGUMENT: Final = "--apply"
CONFIRM_ARGUMENT: Final = "--confirm-plan"
JSON_ARGUMENT: Final = "--json"
CONFIRMATION_CHANGED: Final = "confirm_plan_mismatch_rerun_live_plan"
NORMALIZE_UPPERCASE: Final = "uppercase"
NORMALIZE_PUNCTUATION: Final = "punctuation"
NORMALIZE_LENGTH: Final = "length"
INVALID_NAME_EMPTY: Final = "empty"
INVALID_NAME_EXPLICIT: Final = "explicit"
NAME_ARGUMENT: Final = "--name"
DESCRIPTION_ARGUMENT: Final = "--description"
TAG_ARGUMENT: Final = "--tag"
TRANSPORT_NATIVE: Final = "native"
TRANSPORT_HTTPS: Final = "https"
TRANSPORT_SSH: Final = "ssh"
GIT_ORIGIN_FORMAT: Final = "https://{host}/{repository}.git"
GIT_AUTH_ORIGIN_FORMAT: Final = "https://{username}:{secret}@{host}/{repository}.git"
GIT_SSH_ORIGIN_FORMAT: Final = "git@{host}:{repository}.git"
TAG_SHAPE_EMPTY: Final = "empty"
TAG_SHAPE_BLANK: Final = "blank"
TAG_SHAPE_COMMA: Final = "comma"
TAG_SHAPE_DUPLICATE: Final = "duplicate"
TAG_BLANK: Final = "  "
TAG_COMMA: Final = ","
MALFORMED_PROTECTION: Final = "yes"
MANAGER_COMMAND: Final = "manager"
CHAIN_COMMAND: Final = "chain"
FIELD_METADATA: Final = "metadata"
JOB_KIND: Final = "Job"
SECRET_KIND: Final = "Secret"
NAMESPACE_KIND: Final = "Namespace"
EMPTY_DOCKER_CONFIG: Final = b"{}"
FIELD_UID: Final = "uid"
FIELD_RETENTION: Final = "retention"
FIELD_CLEANUP: Final = "cleanup"
FIELD_REMAINING: Final = "remaining"
FIELD_EXTRA: Final = "extra"
FIELD_STATE: Final = "state"
FIELD_OPERATION: Final = "operation"
FIELD_CALLBACK: Final = "callback"
FIELD_VERIFIED: Final = "verified"
FIELD_IDENTITY: Final = "identity"
FIELD_REPOSITORY: Final = "repository"
FIELD_SOURCE: Final = "source"
FIELD_RUNNER_NAME: Final = "runner_name"
FIELD_NAME_SOURCE: Final = "name_source"
FIELD_RUNNER_DESCRIPTION: Final = "runner_description"
FIELD_DESCRIPTION_SOURCE: Final = "description_source"
FIELD_SCOPES: Final = "scopes"
FIELD_SCHEMA_VERSION: Final = "schema_version"
FIELD_CAPTURED_AT: Final = "captured_at"
FIELD_FILTERS: Final = "filters"
FIELD_RECORDS: Final = "records"
FIELD_SUMMARY: Final = "summary"
FIELD_RECORD_COUNT: Final = "record_count"
FIELD_ERROR_COUNT: Final = "error_count"
SCHEMA_VERSION: Final = "1.0"
GIT_SUCCESS: Final = 0
GIT_MISSING: Final = 1
USAGE_ERROR: Final = 2
SINGLE_RECORD_COUNT: Final = 1
NO_ERRORS: Final = 0
PARSER_CASES: Final = (
    ("manager", "deploy", "--label", "tag", "description", DESCRIPTION_ARGUMENT),
    (
        "chain",
        "chain",
        "--runner-label",
        "runner_label",
        "runner_description",
        "--runner-description",
    ),
)
SCOPE_SCHEMA: Final = "results/runner-scope.schema.json"
RUNNER_REPORT_SCHEMA: Final = "results/toolbox-workflow.schema.json"
SCOPE_FIXTURES: Final = REPO_ROOT / "tests" / "fixtures" / "runner-scope"
REPORT_FIXTURE: Final = "valid-runner-report.json"
VALID_FIXTURE_PREFIX: Final = "valid-"
SCOPE_FIXTURE_NAMES: Final = (
    "valid-borrowed.json",
    "valid-planned.json",
    REPORT_FIXTURE,
    "invalid-missing-uid.json",
    "invalid-namespace-type.json",
    "invalid-planned-uid.json",
    "invalid-presence-without-uid.json",
    "invalid-unknown-field.json",
)
RESULT_SCHEMAS: Final = (
    ("results/toolbox-workflow.schema.json", "gitlab_runner_manager", "deploy"),
    ("results/image-build-readiness.schema.json", "image_build_readiness", "chain"),
)
CAPTURED_AT: Final = "2026-01-01T00:00:00Z"
PLAN_DIGEST: Final = "a" * 64


@pytest.mark.parametrize("fixture", SCOPE_FIXTURE_NAMES)
def test_checked_in_scope_fixtures_match_their_declared_contract(fixture: str) -> None:
    """Validate the approved examples themselves through strict installed decoding.

    :param fixture: Required checked-in scope or complete runner-result example.
    """
    document = JSON_TYPES.json_object(
        CLI_API.response_decoder().decode(
            (SCOPE_FIXTURES / fixture).read_text(encoding="utf-8")
        )
    )
    schema = RUNNER_REPORT_SCHEMA if fixture == REPORT_FIXTURE else SCOPE_SCHEMA
    if fixture.startswith(VALID_FIXTURE_PREFIX):
        SCHEMA.validate_specification(document, schema)
    else:
        with pytest.raises(SCHEMA.SpecificationError):
            SCHEMA.validate_specification(document, schema)


@pytest.mark.parametrize(
    "transport", (TRANSPORT_NATIVE, TRANSPORT_HTTPS, TRANSPORT_SSH)
)
def test_selected_repository_precedes_invoking_git(
    monkeypatch: pytest.MonkeyPatch, repository_inputs: Namespace, transport: str
) -> None:
    """Derive the selected consumer name without consulting another checkout.

    :param monkeypatch: Replace the native command boundary.
    :param repository_inputs: Repository identity generated once by setup.
    :param transport: Native provider path or supported Git remote syntax.
    """
    selected = repository_inputs
    references = {
        TRANSPORT_NATIVE: selected.repository,
        TRANSPORT_HTTPS: GIT_ORIGIN_FORMAT.format(
            host=selected.host, repository=selected.repository
        ),
        TRANSPORT_SSH: GIT_SSH_ORIGIN_FORMAT.format(
            host=selected.host, repository=selected.repository
        ),
    }
    native = Mock()
    monkeypatch.setattr(subprocess, subprocess.run.__name__, native)
    repository = IDENTITY.resolve_repository(references[transport])
    assert repository.name == selected.name
    assert repository.source == IDENTITY.RepositorySource.PROVIDER
    native.assert_not_called()


def test_invoking_git_origin_supplies_name_without_exposing_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repository_inputs: Namespace
) -> None:
    """Read the invoking checkout rather than the installed skill's directory.

    :param tmp_path: Caller-selected checkout directory.
    :param monkeypatch: Replace Git execution with a credentialed remote fixture.
    :param repository_inputs: Generated identity and credentials retained by setup.
    """
    selected = repository_inputs
    native = Mock(
        return_value=subprocess.CompletedProcess(
            (),
            GIT_SUCCESS,
            stdout=GIT_AUTH_ORIGIN_FORMAT.format(
                username=selected.username,
                secret=selected.secret,
                host=selected.host,
                repository=selected.repository,
            ),
            stderr=EMPTY_TEXT,
        )
    )
    monkeypatch.setattr(subprocess, subprocess.run.__name__, native)
    repository = IDENTITY.resolve_repository(None, directory=tmp_path)
    assert repository.name == selected.name
    assert repository.source == IDENTITY.RepositorySource.GIT
    assert selected.secret not in repr(repository)
    assert selected.secret not in repository.reference
    (arguments,) = native.call_args.args
    assert str(tmp_path) in arguments


@pytest.fixture
def repository_inputs(tmp_path: Path) -> Namespace:
    """Generate one consumer identity set for planning, read-back and cleanup.

    :param tmp_path: Test-owned root for generated filesystem paths.
    :returns: Names and native identity generated once for this test invocation.
    """
    name = uuid4().hex
    long_name = NAME_SEPARATOR.join((name, name))
    return Namespace(
        long_name=long_name,
        long_repository=REPOSITORY_SEPARATOR.join((uuid4().hex, long_name)),
        alternate_run_id=uuid4().hex,
        name=name,
        host=uuid4().hex + HOST_SUFFIX,
        repository=REPOSITORY_SEPARATOR.join((uuid4().hex, name)),
        renamed_repository=REPOSITORY_SEPARATOR.join((uuid4().hex, uuid4().hex)),
        project_id=uuid4().int,
        alternate_project_id=uuid4().int,
        run_id=uuid4().hex,
        group=uuid4().hex,
        override=uuid4().hex,
        username=uuid4().hex,
        secret=uuid4().hex,
        namespace_uid=uuid4().hex,
        namespace=uuid4().hex,
        build_namespace=uuid4().hex,
        labels=(uuid4().hex, uuid4().hex),
        runner_root=str(tmp_path / uuid4().hex),
    )


@pytest.mark.parametrize("argument", (NAME_ARGUMENT, DESCRIPTION_ARGUMENT))
def test_numeric_project_stays_pending_until_verified_provider_readback(
    monkeypatch: pytest.MonkeyPatch, repository_inputs: Namespace, argument: str
) -> None:
    """Defer numeric-name defaults without substituting an unrelated checkout.

    :param monkeypatch: Detect any invoking-repository lookup or provider subprocess.
    :param repository_inputs: Generated identity shared by intent and read-back.
    :param argument: Existing name or description option accepted for an override.
    """
    selected = repository_inputs
    native = Mock()
    monkeypatch.setattr(subprocess, subprocess.run.__name__, native)
    reference = str(selected.project_id)
    repository = IDENTITY.resolve_repository(reference)
    assert repository.reference is None
    assert repository.source == IDENTITY.RepositorySource.UNKNOWN
    pending = IDENTITY.resolve_name(None, repository, argument=argument, pending=True)
    assert pending == IDENTITY.ResolvedName(EMPTY_TEXT, IDENTITY.NameSource.UNKNOWN)
    assert OPTIONS.runner_tags((), repository) == ()
    override = IDENTITY.resolve_name(selected.override, repository, argument=argument)
    assert override == IDENTITY.ResolvedName(
        selected.override, IDENTITY.NameSource.EXPLICIT
    )
    verified = IDENTITY.RepositoryIdentity.from_access(
        _project_access(selected), reference
    )
    resolved = IDENTITY.resolve_name(None, verified, argument=argument)
    assert verified.reference == selected.repository
    assert resolved == IDENTITY.ResolvedName(
        selected.name, IDENTITY.NameSource.REPOSITORY
    )
    assert OPTIONS.runner_tags((), verified) == (selected.name,)
    native.assert_not_called()


def _project_access(selected: Namespace) -> JSONObject:
    """Build the retained shared authentication receipt for the selected project.

    :param selected: Identity generated once by the test's setup fixture.
    :returns: Successful project access evidence with verified numeric ID and path.
    """
    return {
        FIELD_STATUS: STATUS.PASS,
        FIELD_TARGET: {
            FIELD_KIND: PROJECT_KIND,
            FIELD_ID: selected.project_id,
            FIELD_PATH: selected.repository,
        },
    }


@pytest.mark.parametrize("field", (FIELD_STATUS, FIELD_KIND, FIELD_ID, FIELD_PATH))
def test_numeric_project_rejects_failed_or_mismatched_access_evidence(
    repository_inputs: Namespace, field: str
) -> None:
    """Require authenticated project-name evidence before resolving opaque IDs.

    :param repository_inputs: Generated selected and alternative project identities.
    :param field: Receipt status or native target field made inconsistent.
    """
    selected = repository_inputs
    access = _project_access(selected)
    mismatches = {
        FIELD_STATUS: STATUS.BLOCKED,
        FIELD_KIND: GROUP_KIND,
        FIELD_ID: selected.alternate_project_id,
        FIELD_PATH: str(selected.project_id),
    }
    if field == FIELD_STATUS:
        access[field] = mismatches[field]
    else:
        access[FIELD_TARGET][field] = mismatches[field]
    with pytest.raises(ValueError, match=IDENTITY.INVALID_REFERENCE):
        IDENTITY.RepositoryIdentity.from_access(access, str(selected.project_id))


def test_numeric_manager_resolves_pending_offline_intent_from_existing_auth_receipt(
    monkeypatch: pytest.MonkeyPatch, repository_inputs: Namespace
) -> None:
    """Bind numeric-ID defaults after real manager authentication, before scope mutation.

    :param monkeypatch: Isolate target selection and existing provider-access boundaries.
    :param repository_inputs: Generated project identity shared by plan and read-back.
    """
    selected = repository_inputs
    args = manager_tests._args(dry_run=True)
    args.project = str(selected.project_id)
    args.description = None
    args.tag = []
    args.namespace = None
    args.run_id = selected.run_id
    action = manager_tests.ACTION.GitLabRunnerManagerAction(args)
    monkeypatch.setattr(
        manager_tests.ACTION,
        manager_tests.ACTION.resolve_target.__name__,
        Mock(return_value=manager_tests._target()),
    )
    action.prepare()
    assert action.repository_identity.source == IDENTITY.RepositorySource.UNKNOWN
    assert action.resolved_description.source == IDENTITY.NameSource.UNKNOWN
    pending_scope, pending_workload = action.scope_plan()
    assert pending_scope[FIELD_NAMESPACE] is None
    assert pending_workload[FIELD_RUNNER][FIELD_NAME] is None
    assert action.labels == ()

    session = SimpleNamespace()
    action.context.configured_session = session
    access = _project_access(selected)
    reader = Mock(return_value=access)
    api = object()
    monkeypatch.setattr(
        manager_tests.CALLBACKS,
        manager_tests.CALLBACKS.GlabAPIClient.__name__,
        Mock(return_value=api),
    )
    monkeypatch.setattr(
        manager_tests.CALLBACKS,
        manager_tests.CALLBACKS.check_gitlab_operation_access.__name__,
        reader,
    )
    callback = manager_tests.CALLBACKS.GitLabManagerAuth(action)
    callback.bind(action.context)
    assert callback.run() == access

    reader.assert_called_once_with(session, api_client=api)
    assert action.repository_identity.reference == selected.repository
    assert action.description == selected.name
    assert action.resolved_description.source == IDENTITY.NameSource.REPOSITORY
    assert action.labels == (selected.name,)
    candidate = action.candidate_scope
    assert candidate is not None
    assert candidate.namespace.startswith(selected.name + NAME_SEPARATOR)
    resolved_scope, _ = action.scope_plan()
    assert resolved_scope[FIELD_NAMESPACE] == candidate.namespace
    assert action.workload().runner(candidate).namespace == candidate.namespace
    assert action.context.gitlab.access == access


@pytest.mark.parametrize("scope", (PROJECT_KIND, GROUP_KIND))
def test_manager_uses_effective_target_defaults_without_invoking_git_fallback(
    monkeypatch: pytest.MonkeyPatch,
    repository_inputs: Namespace,
    scope: str,
) -> None:
    """Select the target's project or group when argv omits both selectors.

    :param monkeypatch: Isolate target configuration and detect unintended Git reads.
    :param repository_inputs: Generated group or project identity and explicit group inputs.
    :param scope: Target-selected project or group operation context.
    """
    selected = repository_inputs
    args = manager_tests._args(dry_run=True)
    args.project = None
    args.group = None
    args.run_id = selected.run_id
    args.namespace = selected.namespace if scope == GROUP_KIND else None
    args.description = selected.override if scope == GROUP_KIND else None
    args.tag = list(selected.labels) if scope == GROUP_KIND else []
    configured = manager_tests._target()
    target = replace(
        configured,
        gitlab=replace(
            configured.gitlab,
            project=selected.repository if scope == PROJECT_KIND else None,
            group=selected.group if scope == GROUP_KIND else None,
        ),
    )
    native = Mock()
    monkeypatch.setattr(subprocess, subprocess.run.__name__, native)
    monkeypatch.setattr(
        manager_tests.ACTION,
        manager_tests.ACTION.resolve_target.__name__,
        Mock(return_value=target),
    )
    action = manager_tests.ACTION.GitLabRunnerManagerAction(args)
    action.prepare()
    if scope == PROJECT_KIND:
        assert action.repository_identity.reference == selected.repository
        assert action.description == selected.name
        assert action.labels == (selected.name,)
        assert action.candidate_scope.namespace.startswith(
            selected.name + NAME_SEPARATOR
        )
    else:
        assert action.repository_identity.source == IDENTITY.RepositorySource.UNKNOWN
        assert action.description == selected.override
        assert action.labels == selected.labels
        assert action.candidate_scope.namespace == selected.namespace
        from_access = Mock()
        monkeypatch.setattr(
            IDENTITY.RepositoryIdentity,
            IDENTITY.RepositoryIdentity.from_access.__name__,
            from_access,
        )
        action.bind_repository({})
        from_access.assert_not_called()
    native.assert_not_called()


def test_numeric_runner_create_live_plan_refuses_renamed_repository_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    repository_inputs: Namespace,
) -> None:
    """Resolve numeric create defaults read-only and bind apply to that exact identity.

    :param tmp_path: Test-owned directory for the unused one-time token sink.
    :param monkeypatch: Isolate existing target, credential and provider-read boundaries.
    :param capsys: Capture the actual workflow's planned and refused reports.
    :param repository_inputs: Generated identity reused across plan and stale apply.
    """
    selected = repository_inputs
    token_path = tmp_path / selected.override
    target = manager_tests._target()
    operation_target = TARGET.GitLabOperationTarget(target.gitlab, target.source_file)
    arguments = [
        CREATE_ACTION,
        PROJECT_ARGUMENT,
        str(selected.project_id),
        TOKEN_OUT_ARGUMENT,
        str(token_path),
        JSON_ARGUMENT,
    ]
    workflow_module = runner_tests.GITLAB_ACTION
    monkeypatch.setattr(
        workflow_module,
        workflow_module.resolve_gitlab_target.__name__,
        Mock(return_value=(operation_target, target.source_kind)),
    )
    workflow = workflow_module.GitLabWorkflow.from_args(
        runner_tests.RUNNER.build_parser().parse_args(arguments + [LIVE_PLAN_ARGUMENT])
    )
    workflow.prepare()
    assert runner_tests.RUNNERS.pending_runner_identity(workflow.state.plan)
    unresolved_digest = workflow.state.plan.digest
    session = runner_tests._bound_session(workflow.state.plan)
    access = _project_access(selected)
    renamed_access = _project_access(selected)
    renamed_access[FIELD_TARGET][FIELD_PATH] = selected.renamed_repository
    reader = Mock(side_effect=(access, renamed_access))
    api = Mock(spec=runner_tests.API.GlabAPIClient)
    monkeypatch.setattr(
        workflow_module,
        workflow_module.bind_gitlab_session.__name__,
        Mock(return_value=session),
    )
    monkeypatch.setattr(
        workflow_module, workflow_module.GlabAPIClient.__name__, Mock(return_value=api)
    )
    monkeypatch.setattr(
        workflow_module,
        workflow_module.check_gitlab_operation_access.__name__,
        reader,
    )
    assert workflow.execute() == STATUS.exit_code(STATUS.PLANNED)
    planned = JSON_TYPES.json_object(
        CLI_API.response_decoder().decode(capsys.readouterr().out)
    )
    assert planned[FIELD_STATUS] == STATUS.PLANNED
    assert planned[FIELD_PLAN][FIELD_CONFIRMATION_READY] is True
    assert planned[FIELD_PLAN_DIGEST] == workflow.state.plan.digest
    assert workflow.state.plan.digest != unresolved_digest
    assert workflow.state.plan.body[FIELD_DESCRIPTION] == selected.name
    assert workflow.state.plan.body[FIELD_TAGS] == selected.name
    assert not token_path.exists()
    assert api.mock_calls == []

    applying = workflow_module.GitLabWorkflow.from_args(
        runner_tests.RUNNER.build_parser().parse_args(
            arguments + [APPLY_ARGUMENT, CONFIRM_ARGUMENT, workflow.state.plan.digest]
        )
    )
    assert applying.execute() == STATUS.exit_code(STATUS.BLOCKED)
    refused = JSON_TYPES.json_object(
        CLI_API.response_decoder().decode(capsys.readouterr().out)
    )
    assert refused[FIELD_STATUS] == STATUS.BLOCKED
    assert refused[FIELD_MUTATED] is False
    assert any(
        item[FIELD_REASON] == CONFIRMATION_CHANGED for item in refused[FIELD_ERRORS]
    )
    assert reader.call_count == len((workflow, applying))
    assert not token_path.exists()
    assert api.mock_calls == []


def test_project_path_readback_cannot_select_another_repository(
    repository_inputs: Namespace,
) -> None:
    """Keep provider-name read-back bound to its original nonnumeric selector.

    :param repository_inputs: Generated selected and alternative repository paths.
    """
    selected = repository_inputs
    with pytest.raises(ValueError, match=IDENTITY.INVALID_REFERENCE):
        IDENTITY.RepositoryIdentity.from_access(
            _project_access(selected),
            selected.repository.replace(selected.name, selected.override),
        )


@pytest.mark.parametrize("override", (False, True))
def test_github_workload_uses_selected_consumer_defaults_and_preserves_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository_inputs: Namespace,
    override: bool,
) -> None:
    """Derive names, namespace and labels from the selected consumer repository.

    :param tmp_path: Directory containing the separately named skill installation.
    :param monkeypatch: Detect accidental identity lookup through the installation.
    :param repository_inputs: Generated consumer identity and independent overrides.
    :param override: Supply explicit object inputs instead of repository defaults.
    """
    selected = repository_inputs
    installation = tmp_path / INSTALLATION_NAME
    installation.mkdir()
    monkeypatch.chdir(installation)
    native = Mock()
    monkeypatch.setattr(subprocess, subprocess.run.__name__, native)
    action = WORKFLOWS.WorkflowAction(
        Namespace(
            action=CREATE_ACTION,
            kind=GITHUB_KIND,
            name=selected.override if override else None,
            namespace=selected.namespace if override else None,
            label=list(selected.labels) if override else [],
            runner_root=selected.runner_root,
            timeout=COMMAND_TIMEOUT,
        ),
        spec=SimpleNamespace(app=INSTALLATION_NAME, namespace=selected.build_namespace),
    )
    action.target = SimpleNamespace(
        github=TARGET.GitHubTarget(selected.host, selected.repository)
    )
    action._workload()
    assert action.runner.name == (selected.override if override else selected.name)
    namespace = action.runner.namespace
    if override:
        assert namespace == selected.namespace
    else:
        assert namespace.startswith(selected.name + NAME_SEPARATOR)
        assert RUN_SCOPE.validate_namespace(namespace) == namespace
    action._workload()
    assert action.runner.namespace == namespace
    assert action.runner.labels == (selected.labels if override else (selected.name,))
    assert action.resolved_runner_name.source == (
        IDENTITY.NameSource.EXPLICIT if override else IDENTITY.NameSource.REPOSITORY
    )
    assert action.runner.namespace != selected.build_namespace
    assert action.runner.namespace != INSTALLATION_NAME
    native.assert_not_called()


@pytest.mark.parametrize(
    "normalization", (NORMALIZE_UPPERCASE, NORMALIZE_PUNCTUATION, NORMALIZE_LENGTH)
)
def test_repository_workload_names_are_normalized_with_suffix_capacity(
    repository_inputs: Namespace, normalization: str
) -> None:
    """Fit native names, ownership labels and registration Secrets before planning.

    :param repository_inputs: Generated repository leaf used by each normalization case.
    :param normalization: Uppercase/underscore, punctuation, or resource length boundary.
    """
    name = repository_inputs.name
    cases = {
        NORMALIZE_UPPERCASE: (
            NAME_UNDERSCORE.join((name.upper(), name)),
            NAME_SEPARATOR.join((name, name)),
        ),
        NORMALIZE_PUNCTUATION: (
            NAME_PUNCTUATION.join((name, name)),
            NAME_SEPARATOR.join((name, name)),
        ),
        NORMALIZE_LENGTH: (
            name.upper() * RUN_SCOPE.DNS_LABEL_LIMIT,
            (name * RUN_SCOPE.DNS_LABEL_LIMIT)[: OPTIONS.RUNNER_NAME_LIMIT],
        ),
    }
    repository_name, expected = cases[normalization]
    expected = expected[: OPTIONS.RUNNER_NAME_LIMIT]
    resolved = OPTIONS.kubernetes_runner_name(
        IDENTITY.ResolvedName(repository_name, IDENTITY.NameSource.REPOSITORY)
    )
    assert resolved.value == expected
    assert resolved.source == IDENTITY.NameSource.REPOSITORY
    assert RUN_SCOPE.validate_namespace(resolved.value) == expected
    assert (
        len(resolved.value + RUNNER.REGISTRATION_SECRET_SUFFIX)
        <= RUN_SCOPE.DNS_LABEL_LIMIT
    )


@pytest.mark.parametrize("consumer", (MANAGER_COMMAND, CHAIN_COMMAND))
def test_long_repository_runner_keeps_bounded_names_and_scope_identity_through_cleanup(
    monkeypatch: pytest.MonkeyPatch, repository_inputs: Namespace, consumer: str
) -> None:
    """Reserve native suffix capacity without dropping the run uniqueness token.

    :param monkeypatch: Isolate manager target selection at its existing boundary.
    :param repository_inputs: Long repository and scope identity generated once by setup.
    :param consumer: Direct manager or chain-generated workload naming path.
    """
    selected = repository_inputs
    if consumer == MANAGER_COMMAND:
        args = manager_tests._args(dry_run=True)
        args.project = selected.long_repository
        args.description = None
        args.tag = []
        args.namespace = None
        args.run_id = selected.run_id
        manager = manager_tests.ACTION.GitLabRunnerManagerAction(args)
        monkeypatch.setattr(
            manager_tests.ACTION,
            manager_tests.ACTION.resolve_target.__name__,
            Mock(return_value=manager_tests._target()),
        )
        manager.prepare()
        scope = manager.candidate_scope
        planned_name = manager.workload().plan_document()[FIELD_RUNNER][FIELD_NAME]
    else:
        chain = chain_tests._prepared_action()
        chain.gitlab_selection = (PROJECT_KIND, selected.long_repository)
        chain.repository_identity = IDENTITY.resolve_repository(
            selected.long_repository
        )
        chain.resolved_description = IDENTITY.resolve_name(
            None, chain.repository_identity, argument=DESCRIPTION_ARGUMENT
        )
        chain.args.runner_description = chain.resolved_description.value
        chain.args.runner_label = list(selected.labels)
        chain.args.namespace = None
        chain._prepare_scope()
        scope = chain.manager_scope
        planned_name = chain.plan(chain.args.node)[FIELD_RUNNER][FIELD_NAME]
        manager = manager_tests._action()
        manager.context = chain.state
        manager.candidate_scope = scope
        manager.repository_identity = chain.repository_identity
        manager.resolved_runner_name = chain.resolved_runner_name
    assert scope is not None
    runner = manager.workload().runner(scope)
    namespace = scope.namespace
    _, _, token = scope.name(
        manager_tests.ACTION.MANAGER_ROLE, prefix=selected.long_name
    ).rpartition(NAME_SEPARATOR)
    assert len(token) == RUN_SCOPE.RESOURCE_TOKEN_LENGTH
    assert runner.name == planned_name
    assert runner.name.endswith(NAME_SEPARATOR + token)
    assert len(runner.name) <= OPTIONS.RUNNER_NAME_LIMIT
    assert runner.name != replace(scope, run_id=selected.alternate_run_id).bounded_name(
        manager_tests.ACTION.MANAGER_ROLE,
        prefix=selected.long_name,
        limit=OPTIONS.RUNNER_NAME_LIMIT,
    )

    cluster = manager_tests.ManagerCluster()
    native_namespace = scope.cluster_resources(cluster).managed(
        RUN_SCOPE.namespace_document(scope)
    )
    native_namespace[FIELD_METADATA][FIELD_UID] = selected.namespace_uid
    namespace_key = (NAMESPACE_KIND.casefold(), EMPTY_TEXT, namespace)
    cluster.objects[namespace_key] = native_namespace
    state = manager.context
    state.kubernetes = cluster
    state.harbor = SimpleNamespace(dockerconfig=EMPTY_DOCKER_CONFIG)
    state.published_image = manager_tests.IMAGE
    pull = K8S_RUNNER.PullImage(runner)
    pull.bind(state)
    assert pull.run()[FIELD_VERIFIED] is True
    job_name = runner.name + RUNNER.PULL_JOB_SUFFIX
    secret_name = job_name + RUNNER.REGISTRY_SECRET_SUFFIX
    job_key = (JOB_KIND.casefold(), namespace, job_name)
    secret_key = (SECRET_KIND.casefold(), namespace, secret_name)
    assert len(job_name) <= RUN_SCOPE.DNS_LABEL_LIMIT
    assert (
        len(runner.name + RUNNER.REGISTRATION_SECRET_SUFFIX)
        <= RUN_SCOPE.DNS_LABEL_LIMIT
    )
    assert len(secret_name) <= manager_tests.SCC_ACCESS.KUBERNETES_NAME_LIMIT
    assert cluster.objects[job_key][FIELD_METADATA][FIELD_NAME] == job_name
    assert cluster.objects[secret_key][FIELD_METADATA][FIELD_NAME] == secret_name
    runtime = RUNNER.RunnerRuntime((), (), (), (), {})
    deployed = K8S_RUNNER.DeployRunner(runner)._document(runtime, manager_tests.IMAGE)
    assert deployed[FIELD_METADATA][FIELD_NAME] == planned_name
    pull.cleanup()
    assert set(cluster.deleted) == {job_key, secret_key}
    assert cluster.objects == {namespace_key: native_namespace}
    assert scope.namespace == namespace
    assert manager.workload().runner(scope).name == planned_name


@pytest.mark.parametrize("case", (INVALID_NAME_EMPTY, INVALID_NAME_EXPLICIT))
def test_kubernetes_name_rejects_empty_normalization_and_invalid_explicit_override(
    repository_inputs: Namespace, case: str
) -> None:
    """Refuse unusable identity instead of silently replacing an explicit name.

    :param repository_inputs: Generated leaf name for an invalid explicit override.
    :param case: Empty derived token or invalid explicit name.
    """
    invalid = {
        INVALID_NAME_EMPTY: (NAME_UNDERSCORE, IDENTITY.NameSource.REPOSITORY),
        INVALID_NAME_EXPLICIT: (
            NAME_UNDERSCORE.join((repository_inputs.name, repository_inputs.name)),
            IDENTITY.NameSource.EXPLICIT,
        ),
    }
    value, source = invalid[case]
    with pytest.raises(ValueError):
        OPTIONS.kubernetes_runner_name(IDENTITY.ResolvedName(value, source))


def test_missing_git_origin_never_guesses_from_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Require an explicit name when the invoking checkout has no origin.

    :param tmp_path: Existing directory whose basename must not become a default.
    :param monkeypatch: Supply Git's missing-origin exit result.
    """
    native = Mock(
        return_value=subprocess.CompletedProcess(
            (), GIT_MISSING, stdout=EMPTY_TEXT, stderr=EMPTY_TEXT
        )
    )
    monkeypatch.setattr(subprocess, subprocess.run.__name__, native)
    repository = IDENTITY.resolve_repository(None, directory=tmp_path)
    assert repository.reference is None
    assert repository.source == IDENTITY.RepositorySource.UNKNOWN
    with pytest.raises(ValueError, match=NAME_ARGUMENT):
        IDENTITY.resolve_name(None, repository, argument=NAME_ARGUMENT)


def test_repository_default_and_explicit_override_have_distinct_sources(
    repository_inputs: Namespace,
) -> None:
    """Preserve caller naming while deriving omitted names from the consumer.

    :param repository_inputs: Generated repository and explicit override selection.
    """
    selected = repository_inputs
    repository = IDENTITY.resolve_repository(selected.repository)
    default = IDENTITY.resolve_name(None, repository, argument=NAME_ARGUMENT)
    explicit = IDENTITY.resolve_name(
        selected.override, repository, argument=NAME_ARGUMENT
    )
    assert default == IDENTITY.ResolvedName(
        selected.name, IDENTITY.NameSource.REPOSITORY
    )
    assert explicit == IDENTITY.ResolvedName(
        selected.override, IDENTITY.NameSource.EXPLICIT
    )


@pytest.mark.parametrize(
    ("requested", "expected"),
    (
        (None, None),
        (OPTIONS.PROTECTED_INPUT, OPTIONS.RunnerProtection.PROTECTED),
        (OPTIONS.UNPROTECTED_INPUT, OPTIONS.RunnerProtection.UNPROTECTED),
    ),
)
def test_protection_preserves_omission_and_maps_explicit_choice(
    requested: str | None, expected: str | None
) -> None:
    """Keep provider-default protection distinct from explicitly unprotected.

    :param requested: Omitted selection or a declared boolean token.
    :param expected: Stable provider representation or omission.
    """
    assert OPTIONS.parse_protected(requested) == expected


def test_malformed_protection_is_rejected_before_provider_use() -> None:
    """Reject an undeclared token rather than treating it as false."""
    with pytest.raises(ValueError):
        OPTIONS.parse_protected(MALFORMED_PROTECTION)


@pytest.mark.parametrize(
    "shape", (TAG_SHAPE_EMPTY, TAG_SHAPE_BLANK, TAG_SHAPE_COMMA, TAG_SHAPE_DUPLICATE)
)
def test_tags_reject_blank_comma_and_duplicate_inputs(
    repository_inputs: Namespace, shape: str
) -> None:
    """Reject ambiguous tags without silently splitting or deduplicating.

    :param repository_inputs: Tags generated once by the setup fixture.
    :param shape: Empty, whitespace, joined or repeated input shape.
    """
    labels = repository_inputs.labels
    first, _ = labels
    invalid = {
        TAG_SHAPE_EMPTY: (EMPTY_TEXT,),
        TAG_SHAPE_BLANK: (TAG_BLANK,),
        TAG_SHAPE_COMMA: (TAG_COMMA.join(labels),),
        TAG_SHAPE_DUPLICATE: (first, first),
    }
    with pytest.raises(ValueError):
        OPTIONS.validate_runner_tags(invalid[shape])


@pytest.mark.parametrize(
    (
        "command",
        "action",
        "legacy",
        "tag_attribute",
        "description_attribute",
        "description_alias",
    ),
    PARSER_CASES,
)
def test_runner_aliases_append_in_order_and_refuse_conflicting_descriptions(
    repository_inputs: Namespace,
    command: str,
    action: str,
    legacy: str,
    tag_attribute: str,
    description_attribute: str,
    description_alias: str,
) -> None:
    """Exercise real parsers while retaining their existing destination fields.

    :param repository_inputs: Generated tags and independent description selections.
    :param command: Manager or chain parser selection.
    :param action: Existing command verb.
    :param legacy: Retained provider-specific tag alias.
    :param tag_attribute: Existing parsed tag destination.
    :param description_attribute: Existing parsed description destination.
    :param description_alias: Singular alias or repetition used to test conflicts.
    """
    selected = repository_inputs
    parser = (MANAGER if command == MANAGER_COMMAND else READINESS).build_parser()
    first, second = selected.labels
    args = parser.parse_args(
        [
            action,
            TAG_ARGUMENT,
            first,
            legacy,
            second,
            DESCRIPTION_ARGUMENT,
            selected.override,
        ]
    )
    assert OPTIONS.validate_runner_tags(getattr(args, tag_attribute)) == selected.labels
    assert getattr(args, description_attribute) == selected.override
    with pytest.raises(SystemExit) as error:
        parser.parse_args(
            [
                action,
                DESCRIPTION_ARGUMENT,
                selected.override,
                description_alias,
                selected.name,
            ]
        )
    assert error.value.code == USAGE_ERROR


def _scope(selected: Namespace) -> JSONObject:
    """Build independently observed borrowed scope evidence from setup identity.

    :param selected: Generated namespace and UID retained for this test lifecycle.
    :returns: Shared typed scope with explicit preservation evidence.
    """
    return SCOPES.ScopeObservation(
        SCOPES.ScopePurpose.RUNNER,
        selected.namespace,
        SCOPES.ScopeSource.EXPLICIT,
        uid=selected.namespace_uid,
        ownership=SCOPES.ScopeOwnership.BORROWED,
        state=SCOPES.ScopeState.PRESENT,
        retention=SCOPES.ScopeRetention.PRESERVED,
    ).document()


@pytest.mark.parametrize(
    "field", (FIELD_UID, FIELD_NAMESPACE, FIELD_EXTRA, FIELD_RETENTION, FIELD_CLEANUP)
)
def test_scope_schema_rejects_incomplete_or_inconsistent_observation(
    repository_inputs: Namespace, field: str
) -> None:
    """Enforce presence evidence, object shape, types and borrowed retention.

    :param repository_inputs: Generated namespace identity used by schema evidence.
    :param field: Scope field replaced or added to a valid fixture.
    """
    selected = repository_inputs
    scope = _scope(selected)
    invalid = {
        FIELD_UID: None,
        FIELD_NAMESPACE: selected.project_id,
        FIELD_EXTRA: True,
        FIELD_RETENTION: SCOPES.ScopeRetention.RETAINED,
        FIELD_CLEANUP: {
            FIELD_STATUS: STATUS.PASS,
            FIELD_REMAINING: [selected.namespace],
        },
    }
    SCHEMA.validate_specification(scope, SCOPE_SCHEMA)
    scope[field] = invalid[field]
    with pytest.raises(SCHEMA.SpecificationError):
        SCHEMA.validate_specification(scope, SCOPE_SCHEMA)


def test_planned_scope_cannot_fabricate_uid_or_successful_cleanup(
    repository_inputs: Namespace,
) -> None:
    """Distinguish an unobserved candidate from an existing namespace.

    :param repository_inputs: Generated UID unavailable to the planned observation.
    """
    selected = repository_inputs
    scope = _scope(selected)
    scope[FIELD_STATE] = SCOPES.ScopeState.PLANNED
    scope[FIELD_UID] = None
    SCHEMA.validate_specification(scope, SCOPE_SCHEMA)
    scope[FIELD_UID] = selected.namespace_uid
    with pytest.raises(SCHEMA.SpecificationError):
        SCHEMA.validate_specification(scope, SCOPE_SCHEMA)
    scope[FIELD_UID] = None
    scope[FIELD_CLEANUP] = {FIELD_STATUS: STATUS.PASS, FIELD_REMAINING: []}
    with pytest.raises(SCHEMA.SpecificationError):
        SCHEMA.validate_specification(scope, SCOPE_SCHEMA)


@pytest.mark.parametrize(("schema", "kind", "operation"), RESULT_SCHEMAS)
def test_workflow_results_enforce_the_shared_scope_record(
    repository_inputs: Namespace, schema: str, kind: str, operation: str
) -> None:
    """Validate both existing result envelopes through their local shared ref.

    :param repository_inputs: Generated identity retained by the complete result.
    :param schema: Installed result schema consuming the shared scope definition.
    :param kind: Existing result kind accepted by that envelope.
    :param operation: Existing manager or chain verb.
    """
    selected = repository_inputs
    scope = _scope(selected)
    record: JSONObject = {
        FIELD_CALLBACK: SCOPES.SCOPE_CALLBACK,
        FIELD_VERIFIED: True,
        FIELD_IDENTITY: {
            FIELD_REPOSITORY: selected.repository,
            FIELD_SOURCE: IDENTITY.RepositorySource.PROVIDER,
            FIELD_RUNNER_NAME: selected.name,
            FIELD_NAME_SOURCE: IDENTITY.NameSource.REPOSITORY,
            FIELD_RUNNER_DESCRIPTION: selected.name,
            FIELD_DESCRIPTION_SOURCE: IDENTITY.NameSource.REPOSITORY,
        },
        FIELD_SCOPES: [scope],
    }
    report: JSONObject = {
        FIELD_SCHEMA_VERSION: SCHEMA_VERSION,
        FIELD_KIND: kind,
        FIELD_OPERATION: operation,
        FIELD_CAPTURED_AT: CAPTURED_AT,
        FIELD_TARGET: selected.repository,
        FIELD_FILTERS: {},
        FIELD_RECORDS: [record],
        FIELD_ERRORS: [],
        FIELD_STATUS: STATUS.BLOCKED,
        FIELD_SUMMARY: {
            FIELD_RECORD_COUNT: SINGLE_RECORD_COUNT,
            FIELD_ERROR_COUNT: NO_ERRORS,
        },
        FIELD_PLAN_DIGEST: PLAN_DIGEST,
    }
    SCHEMA.validate_specification(report, schema)
    del scope[FIELD_UID]
    with pytest.raises(SCHEMA.SpecificationError):
        SCHEMA.validate_specification(report, schema)
