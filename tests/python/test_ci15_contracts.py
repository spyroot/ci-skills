"""Focused repository identity, runner input, and scope evidence contracts.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Final
from unittest.mock import Mock

import pytest
from conftest import REPO_ROOT, import_script_module

CLI_API = import_script_module("lib.python.core.cli_api")
IDENTITY = import_script_module("lib.python.core.runner_identity")
JSON_TYPES = import_script_module("lib.python.core.json_types")
OPTIONS = import_script_module("lib.python.core.runner_options")
SCHEMA = import_script_module("lib.python.core.schema")
MANAGER = import_script_module("gitlab_runner_manager")
READINESS = import_script_module("image_build_readiness")

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue

REPOSITORY: Final = "team/nested/consumer-tools"
REPOSITORY_NAME: Final = "consumer-tools"
PROVIDER_REFERENCES: Final = (
    REPOSITORY,
    f"https://git.example.test/{REPOSITORY}.git",
    f"git@git.example.test:{REPOSITORY}.git",
)
NUMERIC_PROJECT: Final = "42"
REMOTE_SECRET: Final = "unit-remote-credential"
CREDENTIALED_REMOTE: Final = (
    f"https://unit:{REMOTE_SECRET}@git.example.test/{REPOSITORY}.git"
)
OVERRIDE: Final = "caller-selected-runner"
NAME_ARGUMENT: Final = "--name"
DESCRIPTION_ARGUMENT: Final = "--description"
TAGS: Final = ("linux", "arm64")
INVALID_TAGS: Final = (("",), ("  ",), ("linux,arm64",), ("linux", "linux"))
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
NAMESPACE: Final = "consumer-runner"
NAMESPACE_UID: Final = "unit-namespace-uid"
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


@pytest.mark.parametrize("reference", PROVIDER_REFERENCES)
def test_selected_repository_precedes_invoking_git(
    monkeypatch: pytest.MonkeyPatch, reference: str
) -> None:
    """Derive the selected consumer name without consulting another checkout.

    :param monkeypatch: Replace the native command boundary.
    :param reference: Native provider path or supported Git remote syntax.
    """
    native = Mock()
    monkeypatch.setattr(subprocess, "run", native)
    repository = IDENTITY.resolve_repository(reference)
    assert repository.name == REPOSITORY_NAME
    assert repository.source.value == "provider"
    native.assert_not_called()


def test_invoking_git_origin_supplies_name_without_exposing_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read the invoking checkout rather than the installed skill's directory.

    :param tmp_path: Caller-selected checkout directory.
    :param monkeypatch: Replace Git execution with a credentialed remote fixture.
    """
    native = Mock(
        return_value=subprocess.CompletedProcess(
            (), 0, stdout=CREDENTIALED_REMOTE, stderr=""
        )
    )
    monkeypatch.setattr(subprocess, "run", native)
    repository = IDENTITY.resolve_repository(None, directory=tmp_path)
    assert repository.name == REPOSITORY_NAME
    assert repository.source.value == "git"
    assert REMOTE_SECRET not in repr(repository)
    assert REMOTE_SECRET not in repository.reference
    assert str(tmp_path) in native.call_args.args[0]


@pytest.mark.parametrize("argument", (NAME_ARGUMENT, DESCRIPTION_ARGUMENT))
def test_numeric_project_requires_explicit_name_without_unrelated_git_fallback(
    monkeypatch: pytest.MonkeyPatch, argument: str
) -> None:
    """Keep an opaque provider ID distinct from a known repository name.

    :param monkeypatch: Detect any unrelated invoking-repository lookup.
    :param argument: Caller-visible name input required when identity is unknown.
    """
    native = Mock()
    monkeypatch.setattr(subprocess, "run", native)
    repository = IDENTITY.resolve_repository(NUMERIC_PROJECT)
    assert repository.reference is None
    assert repository.source.value == "unknown"
    assert repository.name is None
    with pytest.raises(ValueError, match=argument):
        IDENTITY.resolve_name(None, repository, argument=argument)
    resolved = IDENTITY.resolve_name(OVERRIDE, repository, argument=argument)
    assert (resolved.value, resolved.source.value) == (OVERRIDE, "explicit")
    native.assert_not_called()


def test_missing_git_origin_never_guesses_from_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Require an explicit name when the invoking checkout has no origin.

    :param tmp_path: Existing directory whose basename must not become a default.
    :param monkeypatch: Supply Git's missing-origin exit result.
    """
    native = Mock(return_value=subprocess.CompletedProcess((), 1, stdout="", stderr=""))
    monkeypatch.setattr(subprocess, "run", native)
    repository = IDENTITY.resolve_repository(None, directory=tmp_path)
    assert repository.reference is None
    assert repository.source.value == "unknown"
    with pytest.raises(ValueError, match=NAME_ARGUMENT):
        IDENTITY.resolve_name(None, repository, argument=NAME_ARGUMENT)


def test_repository_default_and_explicit_override_have_distinct_sources() -> None:
    """Preserve caller naming while deriving omitted names from the consumer."""
    repository = IDENTITY.resolve_repository(REPOSITORY)
    default = IDENTITY.resolve_name(None, repository, argument=NAME_ARGUMENT)
    explicit = IDENTITY.resolve_name(OVERRIDE, repository, argument=NAME_ARGUMENT)
    assert (default.value, default.source.value) == (REPOSITORY_NAME, "repository")
    assert (explicit.value, explicit.source.value) == (OVERRIDE, "explicit")


@pytest.mark.parametrize(
    ("requested", "expected"),
    ((None, None), ("true", "ref_protected"), ("false", "not_protected")),
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
        OPTIONS.parse_protected("yes")


@pytest.mark.parametrize("tags", INVALID_TAGS)
def test_tags_reject_blank_comma_and_duplicate_inputs(tags: tuple[str, ...]) -> None:
    """Reject ambiguous tags without silently splitting or deduplicating.

    :param tags: One invalid combined tag sequence.
    """
    with pytest.raises(ValueError):
        OPTIONS.validate_runner_tags(tags)


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
    command: str,
    action: str,
    legacy: str,
    tag_attribute: str,
    description_attribute: str,
    description_alias: str,
) -> None:
    """Exercise real parsers while retaining their existing destination fields.

    :param command: Manager or chain parser selection.
    :param action: Existing command verb.
    :param legacy: Retained provider-specific tag alias.
    :param tag_attribute: Existing parsed tag destination.
    :param description_attribute: Existing parsed description destination.
    :param description_alias: Singular alias or repetition used to test conflicts.
    """
    parser = (MANAGER if command == "manager" else READINESS).build_parser()
    args = parser.parse_args(
        [action, "--tag", TAGS[0], legacy, TAGS[1], DESCRIPTION_ARGUMENT, OVERRIDE]
    )
    assert OPTIONS.validate_runner_tags(getattr(args, tag_attribute)) == TAGS
    assert getattr(args, description_attribute) == OVERRIDE
    with pytest.raises(SystemExit) as error:
        parser.parse_args(
            [
                action,
                DESCRIPTION_ARGUMENT,
                OVERRIDE,
                description_alias,
                REPOSITORY_NAME,
            ]
        )
    assert error.value.code == 2


def _scope() -> JSONObject:
    """Build one independently observed borrowed namespace fixture.

    :returns: Scope with a UID and explicit preservation evidence.
    """
    return {
        "purpose": "runner",
        "namespace": NAMESPACE,
        "uid": NAMESPACE_UID,
        "source": "explicit",
        "ownership": "borrowed",
        "state": "present",
        "retention": "preserved",
        "cleanup": {"status": "NOT_REQUIRED", "remaining": []},
    }


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("uid", None),
        ("namespace", 42),
        ("extra", True),
        ("retention", "retained"),
        ("cleanup", {"status": "PASS", "remaining": [NAMESPACE]}),
    ),
)
def test_scope_schema_rejects_incomplete_or_inconsistent_observation(
    field: str, value: JSONValue
) -> None:
    """Enforce presence evidence, object shape, types and borrowed retention.

    :param field: Scope field replaced or added to a valid fixture.
    :param value: Invalid value for that field.
    """
    scope = _scope()
    SCHEMA.validate_specification(scope, SCOPE_SCHEMA)
    scope[field] = value
    with pytest.raises(SCHEMA.SpecificationError):
        SCHEMA.validate_specification(scope, SCOPE_SCHEMA)


def test_planned_scope_cannot_fabricate_uid_or_successful_cleanup() -> None:
    """Distinguish an unobserved candidate from an existing namespace."""
    scope = _scope()
    scope.update(state="planned", uid=None)
    SCHEMA.validate_specification(scope, SCOPE_SCHEMA)
    scope["uid"] = NAMESPACE_UID
    with pytest.raises(SCHEMA.SpecificationError):
        SCHEMA.validate_specification(scope, SCOPE_SCHEMA)
    scope["uid"] = None
    scope["cleanup"] = {"status": "PASS", "remaining": []}
    with pytest.raises(SCHEMA.SpecificationError):
        SCHEMA.validate_specification(scope, SCOPE_SCHEMA)


@pytest.mark.parametrize(("schema", "kind", "operation"), RESULT_SCHEMAS)
def test_workflow_results_enforce_the_shared_scope_record(
    schema: str, kind: str, operation: str
) -> None:
    """Validate both existing result envelopes through their local shared ref.

    :param schema: Installed result schema consuming the shared scope definition.
    :param kind: Existing result kind accepted by that envelope.
    :param operation: Existing manager or chain verb.
    """
    scope = _scope()
    record: JSONObject = {
        "callback": "RunnerScopes",
        "verified": True,
        "identity": {
            "repository": REPOSITORY,
            "source": "provider",
            "runner_name": REPOSITORY_NAME,
            "name_source": "repository",
            "runner_description": REPOSITORY_NAME,
            "description_source": "repository",
        },
        "scopes": [scope],
    }
    report: JSONObject = {
        "schema_version": "1.0",
        "kind": kind,
        "operation": operation,
        "captured_at": CAPTURED_AT,
        "target": "unit-target",
        "filters": {},
        "records": [record],
        "errors": [],
        "status": "BLOCKED",
        "summary": {"record_count": 1, "error_count": 0},
        "plan_digest": PLAN_DIGEST,
    }
    SCHEMA.validate_specification(report, schema)
    del scope["uid"]
    with pytest.raises(SCHEMA.SpecificationError):
        SCHEMA.validate_specification(report, schema)
