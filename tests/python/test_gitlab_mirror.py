"""Verify Git mirror ref evidence and private native authentication boundaries.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from http import HTTPMethod, HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Final, Optional
from unittest.mock import Mock, call, create_autospec

import pytest
from conftest import import_script_module

REFS: Final = import_script_module("lib.python.core.git.refs")
REMOTE: Final = import_script_module("lib.python.core.git.remote")
API: Final = import_script_module("lib.python.core.cli_api")
RUNTIME: Final = import_script_module("lib.python.core.runtime")
MIRRORING: Final = import_script_module("lib.python.core.git.mirroring")
WORKFLOW: Final = import_script_module("lib.python.core.gitlab.mirror.workflow")
EVIDENCE: Final = import_script_module("lib.python.core.gitlab.mirror.evidence")
FIELD: Final = import_script_module("lib.python.core.gitlab.mirror.constants").Field
CLI: Final = import_script_module("lib.python.core.gitlab.mirror.cli")
ENTRYPOINT: Final = import_script_module("gitlab_mirror")
SCHEMA: Final = import_script_module("lib.python.core.schema")
TARGET: Final = import_script_module("lib.python.core.target")
JSON_TYPES: Final = import_script_module("lib.python.core.json_types")
GITLAB_API: Final = import_script_module("lib.python.core.gitlab.gitlab_api")
GITLAB_SESSION: Final = import_script_module("lib.python.core.gitlab.gitlab_session")
MIRROR_PROVIDER: Final = import_script_module("lib.python.core.gitlab.mirror.provider")
GITHUB: Final = import_script_module("lib.python.core.github.github")
GITHUB_SESSION: Final = import_script_module("lib.python.core.github.session")
GITHUB_REPOSITORY: Final = import_script_module("lib.python.core.github.repository")
PROVENANCE: Final = import_script_module("lib.python.core.provenance")

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue

OID_LENGTH: Final[int] = 40
MAIN_OID: Final[str] = "1" * OID_LENGTH
TAG_OID: Final[str] = "2" * OID_LENGTH
OTHER_OID: Final[str] = "3" * OID_LENGTH
MAIN_REF: Final[str] = "refs/heads/main"
TAG_REF: Final[str] = "refs/tags/v1.0"
EXTRA_REF: Final[str] = "refs/heads/destination-only"
MAIN_RECORD: Final[str] = f"{MAIN_OID}\t{MAIN_REF}\n"
TAG_RECORD: Final[str] = f"{TAG_OID}\t{TAG_REF}\n"
EXTRA_RECORD: Final[str] = f"{OTHER_OID}\t{EXTRA_REF}\n"
REMOTE_OUTPUT: Final[str] = TAG_RECORD + MAIN_RECORD
CANONICAL_OUTPUT: Final[str] = MAIN_RECORD + TAG_RECORD
PEELED_RECORD: Final[str] = f"{OTHER_OID}\t{TAG_REF}^{{}}\n"
MALFORMED_RECORD: Final[str] = f"not-a-git-object\t{MAIN_REF}\n"
SOURCE_REF_COUNT: Final[int] = 2
ONE_MATCH: Final[int] = 1
NATIVE_SUCCESS: Final[int] = 0
NATIVE_FAILURE: Final[int] = 1
TIMEOUT_SECONDS: Final[int] = 7
REMOTE_URL: Final[str] = "https://git.example.test/unit/repository.git"
USERNAME: Final[str] = "unit-user"
TOKEN: Final[str] = "unit-private-token-marker"
AUTHORIZATION: Final[str] = (
    "Authorization: Basic " + base64.b64encode(f"{USERNAME}:{TOKEN}".encode()).decode()
)
AMBIENT_CONFIG_KEY: Final[str] = "GIT_CONFIG_VALUE_0"
AMBIENT_CONFIG: Final[str] = "ambient-config-marker"
PROMPT_ENVIRONMENT_KEY: Final[str] = "GIT_TERMINAL_PROMPT"
PROMPT_DISABLED: Final[str] = "0"
EXPECTED_GIT_ARGV: Final[tuple[str, ...]] = (
    "git",
    "ls-remote",
    "--refs",
    "--heads",
    "--tags",
    REMOTE_URL,
)
SOURCE_ID: Final[int] = 42
DESTINATION_ID: Final[int] = 17
MIRROR_ID: Final[int] = 9
REPLACEMENT_MIRROR_ID: Final[int] = 10
SOURCE_HOST: Final[str] = "gitlab.example.test"
DESTINATION_HOST: Final[str] = "github.example.test"
SOURCE_PATH: Final[str] = "unit/source"
DESTINATION_OWNER: Final[str] = "unit"
DESTINATION_NAME: Final[str] = "destination"
DESTINATION_PATH: Final[str] = f"{DESTINATION_OWNER}/{DESTINATION_NAME}"
SOURCE_CLONE_URL: Final[str] = f"https://{SOURCE_HOST}/{SOURCE_PATH}.git"
DESTINATION_CLONE_URL: Final[str] = f"https://{DESTINATION_HOST}/{DESTINATION_PATH}.git"
DIGEST_LENGTH: Final[int] = 64
FUNCTIONAL_DIGEST: Final[str] = "a" * DIGEST_LENGTH
CREDENTIAL_DIGEST: Final[str] = "b" * DIGEST_LENGTH
SUCCESS_TIMESTAMP: Final[str] = "2026-01-02T03:04:05Z"
ADVANCED_SUCCESS_TIMESTAMP: Final[str] = "2026-01-02T03:04:06Z"
POLL_OBSERVATION_COUNT: Final[int] = 2
PASSWORD_AUTH: Final[str] = "password"
APPLIED_ACTION: Final[str] = "APPLIED"
NO_OP_ACTION: Final[str] = "NO_OP"
STALE_PLAN_REASON: Final[str] = "mirror_plan_mismatch"
FIXED_CLOCK_SECONDS: Final[float] = 0.0
SYNC_OPERATION: Final[str] = "sync"
PROJECT_OPTION: Final[str] = "--project"
DESTINATION_OPTION: Final[str] = "--destination"
DRY_RUN_OPTION: Final[str] = "--dry-run"
OUTPUT_DIRECTORY_OPTION: Final[str] = "--output-dir"
LOG_FILE_OPTION: Final[str] = "--log-file"
OUTPUT_DIRECTORY_NAME: Final[str] = "mirror-output"
LOG_FILE_NAME: Final[str] = "mirror.log"
JSON_OPTION: Final[str] = "--json"
CONFIRM_OPTION: Final[str] = "--confirm-plan"
COMMAND_FAILURE: Final[int] = 2
DRY_RUN_STATUS: Final[str] = "DRY_RUN"
PLANNED_STATUS: Final[str] = "PLANNED"
PARTIAL_STATUS: Final[str] = "PARTIAL"
BLOCKED_STATUS: Final[str] = "BLOCKED"
PROJECT_SCOPE: Final[str] = "project"
TARGET_ENVIRONMENT: Final[str] = "CI_SKILLS_TARGET"
FIXTURE_HOST: Final[str] = "fixture-host"
FIXTURE_CREDENTIAL_SOURCE: Final[str] = "fixture-environment"
GITLAB_TOKEN_VARIABLE: Final[str] = "GITLAB_TOKEN"
GITHUB_TOKEN_VARIABLE: Final[str] = "GH_TOKEN"
METHOD_OPTION: Final[str] = "--method"
INPUT_OPTION: Final[str] = "--input"
REPORT_FILES_FIELD: Final[str] = "report_files"
JSON_REPORT_FIELD: Final[str] = "json"
BODY_ENCODING: Final[str] = "utf-8"
NO_CONTENT_RESPONSE: Final[str] = "HTTP/2 204 No Content\r\n\r\n"
EMPTY_OK_RESPONSE: Final[str] = "HTTP/2 200 OK\r\n\r\n"
NONEMPTY_RESPONSE_BODY: Final[str] = "{}"
EXPECTED_USER_OWNER: Final[str] = "User"
INVALID_NO_CONTENT_REASON: Final[str] = "invalid_no_content_response"
DESTINATION_CHANGED_REASON: Final[str] = "mirror_destination_changed"
UNCONFIRMED_REASON: Final[str] = "mirror_live_plan_confirmation_required"
TRANSPORT_REASON: Final[str] = "transport"
ALTERNATE_DESTINATION_URL: Final[str] = (
    f"https://{DESTINATION_HOST}/unit/replaced-destination.git"
)
EXPECTED_SYNC_ENDPOINT: Final[str] = (
    f"projects/{SOURCE_ID}/remote_mirrors/{MIRROR_ID}/sync"
)
EXPECTED_MIRRORS_ENDPOINT: Final[str] = f"projects/{SOURCE_ID}/remote_mirrors"
EXPECTED_MIRROR_ENDPOINT: Final[str] = f"{EXPECTED_MIRRORS_ENDPOINT}/{MIRROR_ID}"
EXPECTED_REPLACEMENT_ENDPOINT: Final[str] = (
    f"{EXPECTED_MIRRORS_ENDPOINT}/{REPLACEMENT_MIRROR_ID}"
)
EXPECTED_GITHUB_REPOSITORY_ENDPOINT: Final[str] = f"repos/{DESTINATION_PATH}"
EXPECTED_GITHUB_OWNER_ENDPOINT: Final[str] = f"users/{DESTINATION_OWNER}"
EXPECTED_GITHUB_CREATE_ENDPOINT: Final[str] = "user/repos"
EXPECTED_SECRET_URL: Final[str] = (
    f"https://{USERNAME}:{TOKEN}@{DESTINATION_HOST}/{DESTINATION_PATH}.git"
)
COMMAND_SELECTION: Final[tuple[str, ...]] = (
    SYNC_OPERATION,
    PROJECT_OPTION,
    SOURCE_PATH,
    DESTINATION_OPTION,
    DESTINATION_PATH,
)
SOURCE_REPOSITORY: Final = MIRRORING.GitRepository(
    SOURCE_ID, SOURCE_HOST, SOURCE_PATH, SOURCE_CLONE_URL
)
DESTINATION_REPOSITORY: Final = MIRRORING.GitRepository(
    DESTINATION_ID, DESTINATION_HOST, DESTINATION_PATH, DESTINATION_CLONE_URL
)
DESIRED_POLICY: Final = MIRRORING.MirrorPolicy(True, False)
FINISHED_MIRROR: Final = MIRRORING.PushMirror(
    MIRROR_ID,
    DESTINATION_CLONE_URL,
    True,
    DESIRED_POLICY,
    PASSWORD_AUTH,
    MIRRORING.MirrorStatus.FINISHED,
    SUCCESS_TIMESTAMP,
    False,
)


def test_ref_digest_ignores_order_and_peeled_annotated_tag_objects() -> None:
    """Fingerprint exact named objects independently of native response ordering.

    :returns: None; assertions compare canonical evidence and annotated-tag identity.
    """
    output = REMOTE_OUTPUT + PEELED_RECORD
    snapshot = REFS.RefSnapshot.parse(output)

    assert snapshot.refs == (
        REFS.GitRef(MAIN_REF, MAIN_OID),
        REFS.GitRef(TAG_REF, TAG_OID),
    )
    assert snapshot.digest == hashlib.sha256(CANONICAL_OUTPUT.encode()).hexdigest()
    assert snapshot.digest == REFS.RefSnapshot.parse(CANONICAL_OUTPUT).digest


@pytest.mark.parametrize(
    "output",
    (
        MALFORMED_RECORD,
        MAIN_RECORD + MAIN_RECORD,
    ),
    ids=("malformed-object", "duplicate-name"),
)
def test_ref_parser_rejects_ambiguous_or_malformed_remote_evidence(output: str) -> None:
    """Refuse invalid object identity and duplicate names before comparing mirrors.

    :param output: Malformed or repeated native ref observation.
    :returns: None; the shared API error proves the response failed closed.
    """
    with pytest.raises(API.APIError):
        REFS.RefSnapshot.parse(output)


def test_ref_comparison_preserves_extra_refs_and_requires_source_objects() -> None:
    """Preserve destination-only refs while rejecting changed or missing source refs.

    :returns: None; assertions distinguish complete replication from partial matches.
    """
    source = REFS.RefSnapshot.parse(REMOTE_OUTPUT)
    complete = REFS.RefSnapshot.parse(REMOTE_OUTPUT + EXTRA_RECORD)
    changed = REFS.RefSnapshot.parse(
        MAIN_RECORD + f"{OTHER_OID}\t{TAG_REF}\n" + EXTRA_RECORD
    )
    missing = REFS.RefSnapshot.parse(MAIN_RECORD + EXTRA_RECORD)

    assert complete.matches(source)
    assert complete.matching_count(source) == SOURCE_REF_COUNT
    for incomplete in (changed, missing):
        assert not incomplete.matches(source)
        assert incomplete.matching_count(source) == ONE_MATCH


@pytest.mark.parametrize(
    "returncode", (NATIVE_SUCCESS, NATIVE_FAILURE), ids=("success", "failure")
)
def test_remote_reader_scopes_credentials_to_child_environment_and_redacts_errors(
    returncode: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep credentials out of native arguments, process state, and failure reports.

    :param returncode: Successful or failed native Git exit status.
    :param monkeypatch: Preserve a recognizable ambient Git configuration value.
    :returns: None; the runner double verifies native execution without network I/O.
    """
    monkeypatch.setenv(AMBIENT_CONFIG_KEY, AMBIENT_CONFIG)
    calls: list[tuple[str, ...]] = []

    def command(
        argv: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, Optional[str]],
        max_stdout_bytes: int = API.MAX_RESPONSE_BYTES,
    ) -> RUNTIME.CommandResult:
        """Inspect the actual command boundary and inject private provider stderr.

        :param argv: Native ref-read arguments, which must remain credential-free.
        :param timeout: Positive time limit forwarded by the remote reader.
        :param env: Child-only authentication and native Git configuration.
        :param max_stdout_bytes: Shared bounded-runner response limit.
        :returns: Scripted Git output with a deliberately private failure message.
        """
        calls.append(tuple(argv))
        assert tuple(argv) == EXPECTED_GIT_ARGV
        assert timeout == TIMEOUT_SECONDS
        assert AUTHORIZATION in env.values()
        assert env[PROMPT_ENVIRONMENT_KEY] == PROMPT_DISABLED
        assert TOKEN not in repr(argv)
        assert AUTHORIZATION not in repr(argv)
        return RUNTIME.CommandResult(
            tuple(argv), returncode, REMOTE_OUTPUT, f"{TOKEN} {AUTHORIZATION}"
        )

    reader = REMOTE.GitRemoteReader(TIMEOUT_SECONDS, runner=command)
    if returncode == NATIVE_SUCCESS:
        assert reader.read(REMOTE_URL, USERNAME, TOKEN).matches(
            REFS.RefSnapshot.parse(REMOTE_OUTPUT)
        )
    else:
        with pytest.raises(API.APIError) as failure:
            reader.read(REMOTE_URL, USERNAME, TOKEN)
        assert TOKEN not in str(failure.value)
        assert AUTHORIZATION not in str(failure.value)

    assert calls == [EXPECTED_GIT_ARGV]
    assert os.environ[AMBIENT_CONFIG_KEY] == AMBIENT_CONFIG


def _engine(
    mirror: Optional[MIRRORING.PushMirror], destination_refs: REFS.RefSnapshot
) -> tuple[WORKFLOW.MirrorEngine, Mock, Mock]:
    """Bind the real workflow to signature-checked offline provider doubles.

    :param mirror: Existing native mirror, absent when configuration must be created.
    :param destination_refs: Independent destination observation before a native sync.
    :returns: Engine and replaceable source/destination service doubles.
    """
    source = create_autospec(MIRRORING.PushMirrorSource, instance=True)
    destination = create_autospec(MIRRORING.MirrorDestination, instance=True)
    source.repository.return_value = SOURCE_REPOSITORY
    source.refs.return_value = REFS.RefSnapshot.parse(REMOTE_OUTPUT)
    source.mirrors.return_value = () if mirror is None else (mirror,)
    source.mirror.return_value = FINISHED_MIRROR
    source.configure.return_value = FINISHED_MIRROR
    destination.repository.return_value = DESTINATION_REPOSITORY
    destination.refs.return_value = destination_refs
    engine = WORKFLOW.MirrorEngine(
        source=source,
        destination=destination,
        destination_url=DESTINATION_CLONE_URL,
        policy=DESIRED_POLICY,
        username=USERNAME,
        token=TOKEN,
        timeout=TIMEOUT_SECONDS,
        clock=lambda: FIXED_CLOCK_SECONDS,
        sleep=lambda _seconds: pytest.fail("completed fixture unexpectedly polled"),
    )
    return engine, source, destination


def test_apply_reobserves_source_and_refuses_stale_confirmation_without_writes() -> (
    None
):
    """Reject an approved plan after the source changes, before configuring a mirror.

    :returns: None; provider write assertions prove stale confirmation is non-mutating.
    """
    engine, source, destination = _engine(
        FINISHED_MIRROR, REFS.RefSnapshot.parse(REMOTE_OUTPUT)
    )
    intent = WORKFLOW.MirrorIntent(SOURCE_PATH, DESTINATION_PATH)
    plan = engine.plan(intent, FUNCTIONAL_DIGEST, CREDENTIAL_DIGEST)
    source.refs.return_value = REFS.RefSnapshot.parse(
        f"{OTHER_OID}\t{MAIN_REF}\n" + TAG_RECORD
    )

    with pytest.raises(API.APIError, match=STALE_PLAN_REASON):
        engine.apply(plan, confirmation=plan.digest)

    source.configure.assert_not_called()
    source.sync.assert_not_called()
    destination.create.assert_not_called()
    assert engine.mutated is False


def test_apply_returns_verified_no_op_without_configuration_or_sync_writes() -> None:
    """Leave a successful, policy-matching mirror unchanged when source refs match.

    :returns: None; independent ref evidence explains the no-op result.
    """
    snapshot = REFS.RefSnapshot.parse(REMOTE_OUTPUT)
    engine, source, destination = _engine(FINISHED_MIRROR, snapshot)
    plan = engine.plan(
        WORKFLOW.MirrorIntent(SOURCE_PATH, DESTINATION_PATH),
        FUNCTIONAL_DIGEST,
        CREDENTIAL_DIGEST,
    )

    result = engine.apply(plan, confirmation=plan.digest)

    assert result.action.value == NO_OP_ACTION
    assert result.source_refs == snapshot
    assert result.destination_refs == snapshot
    assert result.created_repository is False
    assert result.created_mirror is False
    assert engine.mutated is False
    source.configure.assert_not_called()
    source.sync.assert_not_called()
    destination.create.assert_not_called()
    source.refs.assert_called_with(SOURCE_REPOSITORY)
    destination.refs.assert_called_with(DESTINATION_REPOSITORY)


def test_apply_configures_syncs_and_independently_verifies_destination_objects() -> (
    None
):
    """Report applied only after native success and a fresh destination ref read.

    :returns: None; the destination changes only when the fake native sync executes.
    """
    snapshot = REFS.RefSnapshot.parse(REMOTE_OUTPUT)
    engine, source, destination = _engine(None, REFS.RefSnapshot(()))

    def synchronized(mirror_id: int) -> None:
        """Expose replicated refs only after syncing the selected native mirror.

        :param mirror_id: Provider ID whose replication was explicitly requested.
        :returns: None; later destination reads observe the simulated replication.
        """
        assert mirror_id == MIRROR_ID
        destination.refs.return_value = snapshot

    source.sync.side_effect = synchronized
    plan = engine.plan(
        WORKFLOW.MirrorIntent(SOURCE_PATH, DESTINATION_PATH),
        FUNCTIONAL_DIGEST,
        CREDENTIAL_DIGEST,
    )

    result = engine.apply(plan, confirmation=plan.digest)

    assert result.action.value == APPLIED_ACTION
    assert result.source_refs == snapshot
    assert result.destination_refs == snapshot
    assert result.created_repository is False
    assert result.created_mirror is True
    assert engine.mutated is True
    source.configure.assert_called_once_with(
        DESTINATION_REPOSITORY, DESIRED_POLICY, USERNAME, TOKEN, None
    )
    source.sync.assert_called_once_with(MIRROR_ID)
    source.mirror.assert_called_once_with(MIRROR_ID)
    destination.create.assert_not_called()


def test_sync_waits_for_new_native_success_even_when_destination_refs_match() -> None:
    """Reject an old finished status as evidence for a newly requested mirror sync.

    :returns: None; a poll wait separates historical success from the fresh result.
    """
    snapshot = REFS.RefSnapshot.parse(REMOTE_OUTPUT)
    engine, source, destination = _engine(FINISHED_MIRROR, REFS.RefSnapshot(()))
    advanced = replace(
        FINISHED_MIRROR, last_successful_update_at=ADVANCED_SUCCESS_TIMESTAMP
    )
    source.mirror.side_effect = (FINISHED_MIRROR, advanced)
    source.sync.side_effect = lambda _mirror_id: destination.refs.configure_mock(
        return_value=snapshot
    )
    poll_wait = Mock()
    engine.sleep = poll_wait
    plan = engine.plan(
        WORKFLOW.MirrorIntent(SOURCE_PATH, DESTINATION_PATH),
        FUNCTIONAL_DIGEST,
        CREDENTIAL_DIGEST,
    )

    result = engine.apply(plan, confirmation=plan.digest)

    assert result.action.value == APPLIED_ACTION
    assert result.mirror.last_successful_update_at == ADVANCED_SUCCESS_TIMESTAMP
    assert result.destination_refs == snapshot
    assert source.mirror.call_count == POLL_OBSERVATION_COUNT
    poll_wait.assert_called_once()
    source.sync.assert_called_once_with(MIRROR_ID)
    source.configure.assert_not_called()
    destination.create.assert_not_called()


@pytest.mark.parametrize(
    ("verified_count", "destination_count", "destination_digest", "expected"),
    (
        (SOURCE_REF_COUNT, SOURCE_REF_COUNT, FUNCTIONAL_DIGEST, True),
        (ONE_MATCH, SOURCE_REF_COUNT, FUNCTIONAL_DIGEST, False),
        (SOURCE_REF_COUNT, SOURCE_REF_COUNT, CREDENTIAL_DIGEST, False),
        (SOURCE_REF_COUNT, SOURCE_REF_COUNT + ONE_MATCH, CREDENTIAL_DIGEST, True),
    ),
    ids=("matching-proof", "forged-count", "conflicting-digests", "extra-refs"),
)
def test_ref_evidence_rejects_contradictions_and_permits_destination_only_refs(
    verified_count: int,
    destination_count: int,
    destination_digest: str,
    expected: bool,
) -> None:
    """Validate cross-field replication proof beyond the result schema's types.

    :param verified_count: Reported source refs independently matched at destination.
    :param destination_count: Total destination refs, including destination-only refs.
    :param destination_digest: Reported fingerprint of all destination refs.
    :param expected: Whether counts and fingerprints form consistent evidence.
    :returns: None; assertions enforce the shared evidence contract.
    """
    record: JSONObject = {
        FIELD.SOURCE_REFS: {
            FIELD.COUNT: SOURCE_REF_COUNT,
            FIELD.SHA256: FUNCTIONAL_DIGEST,
        },
        FIELD.DESTINATION_REFS: {
            FIELD.COUNT: destination_count,
            FIELD.SHA256: destination_digest,
        },
        FIELD.VERIFIED_SOURCE_REF_COUNT: verified_count,
        FIELD.REPLACEMENT_REQUIRED: False,
        FIELD.REPLACED_MIRROR_ID: None,
    }

    assert EVIDENCE.check_ref_evidence(record) is expected
    assert EVIDENCE.check_replacement_evidence(record)


def test_mirror_parser_composes_mode_controls_and_shared_output_options(
    tmp_path: Path,
) -> None:
    """Construct the installed parser without re-registering shared persistence flags.

    :param tmp_path: Temporary paths accepted as output arguments without file writes.
    :returns: None; two parses verify dry-run and live-plan persistence controls.
    """
    assert ENTRYPOINT.build_parser is CLI.build_parser
    parser = ENTRYPOINT.build_parser()
    selection = [
        SYNC_OPERATION,
        PROJECT_OPTION,
        SOURCE_PATH,
        DESTINATION_OPTION,
        DESTINATION_PATH,
    ]

    dry_run = parser.parse_args([*selection, DRY_RUN_OPTION])
    assert dry_run.action == SYNC_OPERATION
    assert dry_run.project == SOURCE_PATH
    assert dry_run.destination == DESTINATION_PATH
    assert dry_run.dry_run is True
    assert dry_run.apply is False
    assert dry_run.live_plan is False

    output_directory = tmp_path / OUTPUT_DIRECTORY_NAME
    log_file = tmp_path / LOG_FILE_NAME
    live_plan = parser.parse_args(
        [
            *selection,
            CLI.LIVE_PLAN_OPTION,
            OUTPUT_DIRECTORY_OPTION,
            str(output_directory),
            LOG_FILE_OPTION,
            str(log_file),
        ]
    )
    assert live_plan.live_plan is True
    assert live_plan.dry_run is False
    assert live_plan.output_dir == str(output_directory)
    assert live_plan.log_file == str(log_file)


@dataclass(frozen=True)
class _Exchange:
    """Describe one expected native API request and its controlled response.

    :param method: Exact native HTTP method.
    :param endpoint: Exact provider-relative API route.
    :param response: Complete native stdout, including headers when requested.
    :param body: Expected private JSON body, absent for a body-free operation.
    :param returncode: Native process status selected by the fixture.
    """

    method: str
    endpoint: str
    response: str
    body: Optional[JSONObject] = None
    returncode: int = NATIVE_SUCCESS


class _NativeRequests:
    """Run real API adapters over a finite native-command script."""

    def __init__(self, exchanges: Sequence[_Exchange]) -> None:
        """Retain expected native requests without exposing a real provider.

        :param exchanges: Ordered requests and native responses to consume.
        """
        self.exchanges = iter(exchanges)
        self.calls: list[tuple[str, str]] = []
        self.body_paths: list[Path] = []

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, Optional[str]],
        max_stdout_bytes: int = API.MAX_RESPONSE_BYTES,
    ) -> RUNTIME.CommandResult:
        """Check the native request and read its actual private body file.

        :param argv: Real gh or glab API arguments from the shared transport.
        :param timeout: Bounded native request budget.
        :param env: Private selected credential environment.
        :param max_stdout_bytes: Shared native output bound.
        :returns: Next scripted native process result.
        :raises StopIteration: An unexpected extra request exceeds the script.
        """
        exchange = next(self.exchanges)
        assert argv[argv.index(METHOD_OPTION) + ONE_MATCH] == exchange.method
        assert exchange.endpoint in argv
        assert timeout > FIXED_CLOCK_SECONDS
        assert TOKEN not in repr(argv)
        self.calls.append((exchange.method, exchange.endpoint))
        if exchange.body is None:
            assert INPUT_OPTION not in argv
        else:
            body_path = Path(argv[argv.index(INPUT_OPTION) + ONE_MATCH])
            self.body_paths.append(body_path)
            assert (
                json.loads(body_path.read_text(encoding=BODY_ENCODING)) == exchange.body
            )
        return RUNTIME.CommandResult(
            tuple(argv), exchange.returncode, exchange.response, ""
        )


def _response(payload: JSONValue, status: HTTPStatus = HTTPStatus.OK) -> str:
    """Build a controlled native HTTP envelope for the real API decoder.

    :param payload: Provider-owned JSON value returned by the fixture.
    :param status: Exact HTTP response status.
    :returns: Native header/body output accepted by the shared CLI API transport.
    """
    return f"HTTP/2 {status.value} {status.phrase}\r\n\r\n{json.dumps(payload)}"


def _native_mirror_payload(mirror: MIRRORING.PushMirror) -> JSONObject:
    """Supply native provider fields, including intentionally private response text.

    :param mirror: Known mirror state whose native identity and policy are observed.
    :returns: Native response with private URL and error text for decoder verification.
    """
    payload = dict(mirror.evidence())
    private_error = payload.pop(MIRRORING.MirrorField.LAST_ERROR)
    payload[MIRRORING.MirrorField.URL] = EXPECTED_SECRET_URL
    payload[MIRROR_PROVIDER.SourceField.LAST_ERROR] = (
        TOKEN if private_error is True else None
    )
    return payload


def _native_source(
    requests: _NativeRequests, target_file: Path
) -> MIRROR_PROVIDER.GitLabPushMirrorService:
    """Construct the actual GitLab service and native API transport offline.

    :param requests: Scripted process boundary consumed by the actual GlabAPIClient.
    :param target_file: Test-owned nonsecret target reference.
    :returns: Concrete mirror provider with a signature-checked unused Git reader.
    """
    skill = PROVENANCE.SkillIdentity(
        algorithm=PROVENANCE.DIGEST_ALGORITHM,
        digest=FUNCTIONAL_DIGEST,
        file_count=ONE_MATCH,
        functional_digest=FUNCTIONAL_DIGEST,
        revision=PROVENANCE.Revision(value=None, source=None, verified=False),
        consuming_project=PROVENANCE.ConsumerRevision(commit=None, source=None),
    )
    session = GITLAB_SESSION.BoundGitLabSession(
        origin=f"https://{SOURCE_HOST}",
        host=SOURCE_HOST,
        target_kind=PROJECT_SCOPE,
        target_reference=SOURCE_PATH,
        target_source=FIXTURE_CREDENTIAL_SOURCE,
        target_file=target_file,
        credential_source=FIXTURE_CREDENTIAL_SOURCE,
        credential_digest=CREDENTIAL_DIGEST,
        execution_host=FIXTURE_HOST,
        skill=skill,
        environment={GITLAB_TOKEN_VARIABLE: TOKEN},
    )
    api = GITLAB_API.GlabAPIClient(command=requests, timeout=TIMEOUT_SECONDS)
    service = GITLAB_SESSION.GitLabService(api, session, SOURCE_ID)
    reader = create_autospec(REMOTE.GitRemoteReader, instance=True)
    return MIRROR_PROVIDER.GitLabPushMirrorService(service, reader)


def _selected_target(target_file: Path) -> TARGET.Target:
    """Provide explicit provider selection without credential resolution.

    :param target_file: Test-owned source reference required by the command boundary.
    :returns: Real target value selecting only the two mirror providers.
    """
    return TARGET.Target(
        github=TARGET.GitHubTarget(DESTINATION_HOST, DESTINATION_PATH),
        gitlab=TARGET.GitLabTarget(
            f"https://{SOURCE_HOST}", SOURCE_HOST, project=SOURCE_PATH
        ),
        kubernetes=None,
        active_surfaces=CLI.SURFACES,
        source_file=target_file,
    )


@pytest.mark.parametrize("mode_options", ((), (DRY_RUN_OPTION,)))
def test_command_offline_emits_valid_result_without_workflow_or_authentication(
    target_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode_options: tuple[str, ...],
) -> None:
    """Default and explicit offline modes emit their actual result without live access.

    :param target_file: Existing test fixture declaring provider authorities.
    :param monkeypatch: Select the fixture and observe workflow/authentication boundaries.
    :param capsys: Capture the real machine report emitted by MirrorCommand.
    :param mode_options: Default mode or explicit dry-run selection.
    :returns: None; the actual result is checked against its installed schema.
    """
    monkeypatch.setenv(TARGET_ENVIRONMENT, str(target_file))
    workflow = Mock(side_effect=AssertionError("offline mode invoked workflow"))
    authentication = Mock(side_effect=AssertionError("offline mode authenticated"))
    monkeypatch.setattr(CLI, "workflow", workflow)
    monkeypatch.setattr(CLI, "bind_gitlab_session", authentication)
    monkeypatch.setattr(CLI, "GitHubIdentityAuth", authentication)

    code = CLI.main([*COMMAND_SELECTION, *mode_options, JSON_OPTION])
    result = JSON_TYPES.json_object(
        API.response_decoder().decode(capsys.readouterr().out)
    )

    SCHEMA.validate_specification(result, EVIDENCE.RESULT_SCHEMA)
    assert code == NATIVE_SUCCESS
    assert result[FIELD.STATUS] == DRY_RUN_STATUS
    assert result[FIELD.MUTATED] is False
    workflow.assert_not_called()
    authentication.assert_not_called()


def test_command_refuses_unconfirmed_apply_before_target_or_authentication(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reject an unsafe apply before resolving a target or binding credentials.

    :param monkeypatch: Observe boundaries that must remain uncalled on refusal.
    :param capsys: Capture the actual blocked command report.
    :returns: None; the refused result remains valid under the installed schema.
    """
    unavailable = Mock(
        side_effect=AssertionError("unconfirmed apply reached live setup")
    )
    monkeypatch.setattr(CLI, "resolve_target", unavailable)
    monkeypatch.setattr(CLI, "workflow", unavailable)
    monkeypatch.setattr(CLI, "bind_gitlab_session", unavailable)

    code = CLI.main([*COMMAND_SELECTION, CLI.APPLY_OPTION, JSON_OPTION])
    result = JSON_TYPES.json_object(
        API.response_decoder().decode(capsys.readouterr().out)
    )

    SCHEMA.validate_specification(result, EVIDENCE.RESULT_SCHEMA)
    assert code == COMMAND_FAILURE
    assert result[FIELD.STATUS] == BLOCKED_STATUS
    errors = JSON_TYPES.json_objects(result[FIELD.ERRORS])
    assert [error[FIELD.REASON] for error in errors] == [UNCONFIRMED_REASON]
    unavailable.assert_not_called()


@pytest.mark.parametrize("uncertain", (False, True), ids=("planned", "uncertain-write"))
def test_command_persists_schema_valid_plan_and_partial_resource_ownership(
    tmp_path: Path,
    target_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    uncertain: bool,
) -> None:
    """Persist actual reports and retain created identities after an uncertain write.

    :param tmp_path: Test-owned destination for paired report files.
    :param target_file: Nonsecret source reference for command preparation.
    :param monkeypatch: Replace target binding and provider execution, retaining emitters.
    :param capsys: Capture the emitted report for independent schema validation.
    :param uncertain: Whether the fake executor fails after creating intended resources.
    :returns: None; both emitted and saved reports satisfy the actual result schema.
    """
    monkeypatch.setattr(
        CLI, "resolve_target", Mock(return_value=_selected_target(target_file))
    )
    snapshot = REFS.RefSnapshot.parse(REMOTE_OUTPUT)
    empty = REFS.RefSnapshot(())
    intent = WORKFLOW.MirrorIntent(SOURCE_PATH, DESTINATION_PATH, create_if_absent=True)
    plan = WORKFLOW.MirrorPlan(
        intent,
        WORKFLOW.MirrorObservation(SOURCE_REPOSITORY, None, None, snapshot, empty),
        DESIRED_POLICY,
        FUNCTIONAL_DIGEST,
        CREDENTIAL_DIGEST,
    )
    engine, _source, _destination = _engine(None, empty)

    def workflow(action: CLI.MirrorAction, state: CLI.MirrorState) -> Mock:
        """Supply planned evidence and simulate one uncertain post-creation failure.

        :param action: Real prepared mirror action, including execution deadline.
        :param state: Real command-owned state receiving controlled provider results.
        :returns: Minimal executor double; output handling remains real.
        """

        def run() -> None:
            """Publish a real plan value and preserve observed write ownership.

            :returns: None after publishing a plan for live-plan mode.
            :raises API.APIError: The selected apply outcome remains uncertain.
            """
            state.plan = plan
            state.engine = engine
            state.record = {
                FIELD.CALLBACK: CLI.KIND,
                FIELD.ACTION: PLANNED_STATUS,
                FIELD.VERIFIED: False,
                FIELD.SOURCE: SOURCE_REPOSITORY.evidence(),
                FIELD.DESTINATION: {
                    FIELD.ID: None,
                    MIRRORING.MirrorField.HOST: DESTINATION_HOST,
                    MIRRORING.MirrorField.PATH: DESTINATION_PATH,
                },
                FIELD.MIRROR: None,
                FIELD.SOURCE_REFS: CLI.ref_evidence(snapshot),
                FIELD.DESTINATION_REFS: CLI.ref_evidence(empty),
                FIELD.VERIFIED_SOURCE_REF_COUNT: NATIVE_SUCCESS,
                FIELD.CREATED: {FIELD.REPOSITORY: False, FIELD.MIRROR: False},
                FIELD.REPLACEMENT_REQUIRED: False,
                FIELD.REPLACED_MIRROR_ID: None,
            }
            if uncertain:
                engine.mutated = None
                engine.created_repository = True
                engine.created_mirror = True
                engine.destination_repository = DESTINATION_REPOSITORY
                engine.current_mirror = FINISHED_MIRROR
                raise API.APIError(TRANSPORT_REASON)

        executor = Mock(spec=CLI.WorkflowExecutor)
        executor.run.side_effect = run
        return executor

    monkeypatch.setattr(CLI, "workflow", workflow)
    output_directory = tmp_path / OUTPUT_DIRECTORY_NAME
    modes = (
        [CLI.APPLY_OPTION, CONFIRM_OPTION, plan.digest]
        if uncertain
        else [CLI.LIVE_PLAN_OPTION]
    )
    code = CLI.main(
        [
            *COMMAND_SELECTION,
            CLI.CREATE_OPTION,
            *modes,
            OUTPUT_DIRECTORY_OPTION,
            str(output_directory),
            JSON_OPTION,
        ]
    )
    result = JSON_TYPES.json_object(
        API.response_decoder().decode(capsys.readouterr().out)
    )
    files = JSON_TYPES.json_object(result[REPORT_FILES_FIELD])
    json_report = Path(JSON_TYPES.json_string(files[JSON_REPORT_FIELD]))
    saved = JSON_TYPES.json_object(
        API.response_decoder().decode(json_report.read_text(encoding=BODY_ENCODING))
    )

    SCHEMA.validate_specification(result, EVIDENCE.RESULT_SCHEMA)
    SCHEMA.validate_specification(saved, EVIDENCE.RESULT_SCHEMA)
    assert saved == result
    assert code == (COMMAND_FAILURE if uncertain else NATIVE_SUCCESS)
    assert result[FIELD.STATUS] == (PARTIAL_STATUS if uncertain else PLANNED_STATUS)
    assert result[FIELD.MUTATED] is (None if uncertain else False)
    records = JSON_TYPES.json_objects(result[FIELD.RECORDS])
    (record,) = records
    created = JSON_TYPES.json_object(record[FIELD.CREATED])
    assert created[FIELD.REPOSITORY] is uncertain
    assert created[FIELD.MIRROR] is uncertain
    assert record[FIELD.VERIFIED] is False
    if uncertain:
        assert (
            JSON_TYPES.json_object(record[FIELD.DESTINATION])[FIELD.ID]
            == DESTINATION_ID
        )
        assert JSON_TYPES.json_object(record[FIELD.MIRROR])[FIELD.ID] == MIRROR_ID


@pytest.mark.parametrize(
    ("response", "accepted"),
    (
        (NO_CONTENT_RESPONSE, True),
        (EMPTY_OK_RESPONSE, False),
        (NO_CONTENT_RESPONSE + NONEMPTY_RESPONSE_BODY, False),
    ),
    ids=("native-204", "empty-200", "204-with-body"),
)
def test_native_mirror_sync_requires_exact_http_204(
    target_file: Path, response: str, accepted: bool
) -> None:
    """Run native sync through the actual provider and shared no-content transport.

    :param target_file: Nonsecret bound-session reference owned by the fixture.
    :param response: Native HTTP envelope consumed by the real API decoder.
    :param accepted: Whether the native status and body satisfy the sync contract.
    :returns: None; malformed success envelopes are rejected without retries.
    """
    requests = _NativeRequests(
        (_Exchange(HTTPMethod.POST, EXPECTED_SYNC_ENDPOINT, response),)
    )
    source = _native_source(requests, target_file)

    if accepted:
        assert source.sync(MIRROR_ID) is None
    else:
        with pytest.raises(API.APIError, match=INVALID_NO_CONTENT_REASON):
            source.sync(MIRROR_ID)

    assert requests.calls == [(HTTPMethod.POST, EXPECTED_SYNC_ENDPOINT)]
    assert requests.body_paths == []


def test_github_private_creation_uses_independent_repository_and_visibility_reads() -> (
    None
):
    """Create only the selected private destination and trust subsequent provider reads.

    :returns: None; POST response identity cannot substitute for independent read-back.
    """
    fields = GITHUB_REPOSITORY.RepositoryField
    repository: JSONObject = {
        fields.ID: DESTINATION_ID,
        fields.FULL_NAME: DESTINATION_PATH,
        fields.CLONE_URL: DESTINATION_CLONE_URL,
        fields.PRIVATE: True,
    }
    requests = _NativeRequests(
        (
            _Exchange(
                HTTPMethod.GET,
                EXPECTED_GITHUB_REPOSITORY_ENDPOINT,
                _response({}, HTTPStatus.NOT_FOUND),
                returncode=NATIVE_FAILURE,
            ),
            _Exchange(
                HTTPMethod.GET,
                EXPECTED_GITHUB_OWNER_ENDPOINT,
                _response(
                    {fields.LOGIN: DESTINATION_OWNER, fields.TYPE: EXPECTED_USER_OWNER}
                ),
            ),
            _Exchange(
                HTTPMethod.POST,
                EXPECTED_GITHUB_CREATE_ENDPOINT,
                _response({fields.ID: SOURCE_ID}, HTTPStatus.CREATED),
                body={
                    fields.NAME: DESTINATION_NAME,
                    fields.PRIVATE: True,
                    fields.AUTO_INIT: False,
                },
            ),
            _Exchange(
                HTTPMethod.GET,
                EXPECTED_GITHUB_REPOSITORY_ENDPOINT,
                _response(repository),
            ),
            _Exchange(
                HTTPMethod.GET,
                EXPECTED_GITHUB_REPOSITORY_ENDPOINT,
                _response(repository),
            ),
        )
    )
    session = GITHUB_SESSION.GitHubIdentitySession(
        DESTINATION_HOST,
        {GITHUB_TOKEN_VARIABLE: TOKEN},
        GITHUB.GitHubAPIClient(command=requests, timeout=TIMEOUT_SECONDS),
        DESTINATION_OWNER,
        FIXTURE_CREDENTIAL_SOURCE,
    )
    reader = create_autospec(REMOTE.GitRemoteReader, instance=True)
    reader.read.return_value = REFS.RefSnapshot.parse(REMOTE_OUTPUT)
    destination = GITHUB_REPOSITORY.GitHubRepositoryService(session, reader)

    assert destination.repository(DESTINATION_PATH) is None
    observed = destination.create(DESTINATION_PATH)

    assert observed == DESTINATION_REPOSITORY
    assert destination.refs(observed).refs == REFS.RefSnapshot.parse(REMOTE_OUTPUT).refs
    reader.read.assert_called_once_with(DESTINATION_CLONE_URL, DESTINATION_OWNER, TOKEN)
    assert requests.calls[-POLL_OBSERVATION_COUNT:] == [
        (HTTPMethod.GET, EXPECTED_GITHUB_REPOSITORY_ENDPOINT),
        (HTTPMethod.GET, EXPECTED_GITHUB_REPOSITORY_ENDPOINT),
    ]
    assert all(not path.exists() for path in requests.body_paths)


def test_native_mirror_creation_decodes_private_state_and_reads_configuration_back(
    tmp_path: Path, target_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create a native mirror through private JSON transport and redact read-back.

    :param tmp_path: Own the real shared create guard's lock directory.
    :param target_file: Test-bound GitLab session reference.
    :param monkeypatch: Redirect only the existing shared guard's filesystem root.
    :returns: None; actual provider decode and independent configuration reads are used.
    """
    fields = MIRRORING.MirrorField
    native = _native_mirror_payload(replace(FINISHED_MIRROR, last_error_present=True))
    body: JSONObject = {
        fields.URL: EXPECTED_SECRET_URL,
        fields.AUTH_METHOD: PASSWORD_AUTH,
        fields.ENABLED: True,
        fields.KEEP_DIVERGENT: True,
        fields.ONLY_PROTECTED: False,
    }
    requests = _NativeRequests(
        (
            _Exchange(HTTPMethod.GET, EXPECTED_MIRRORS_ENDPOINT, json.dumps([])),
            _Exchange(
                HTTPMethod.POST,
                EXPECTED_MIRRORS_ENDPOINT,
                _response({fields.ID: MIRROR_ID}, HTTPStatus.CREATED),
                body=body,
            ),
            _Exchange(HTTPMethod.GET, EXPECTED_MIRROR_ENDPOINT, _response(native)),
        )
    )
    monkeypatch.setattr(GITLAB_API, "_lock_root", lambda: tmp_path / PROJECT_SCOPE)
    source = _native_source(requests, target_file)

    mirror = source.configure(
        DESTINATION_REPOSITORY, DESIRED_POLICY, USERNAME, TOKEN, None
    )

    assert mirror.id == MIRROR_ID
    assert mirror.url == DESTINATION_CLONE_URL
    assert mirror.policy == DESIRED_POLICY
    assert mirror.last_error_present is True
    assert TOKEN not in json.dumps(mirror.evidence())
    assert all(not path.exists() for path in requests.body_paths)


def test_engine_rejects_same_mirror_id_redirected_to_a_different_destination() -> None:
    """Refuse changed native destination identity even when expected refs now match.

    :returns: None; matching object IDs cannot hide a different remote mirror URL.
    """
    snapshot = REFS.RefSnapshot.parse(REMOTE_OUTPUT)
    engine, source, destination = _engine(FINISHED_MIRROR, REFS.RefSnapshot(()))
    source.sync.side_effect = lambda _mirror_id: destination.refs.configure_mock(
        return_value=snapshot
    )
    source.mirror.return_value = replace(
        FINISHED_MIRROR,
        url=ALTERNATE_DESTINATION_URL,
        last_successful_update_at=ADVANCED_SUCCESS_TIMESTAMP,
    )
    plan = engine.plan(
        WORKFLOW.MirrorIntent(SOURCE_PATH, DESTINATION_PATH),
        FUNCTIONAL_DIGEST,
        CREDENTIAL_DIGEST,
    )

    with pytest.raises(API.APIError, match=DESTINATION_CHANGED_REASON):
        engine.apply(plan, confirmation=plan.digest)

    assert destination.refs.return_value == snapshot
    source.sync.assert_called_once_with(MIRROR_ID)
    assert engine.mutated is True


def test_provider_read_exhausting_deadline_prevents_followup_reads_or_success() -> None:
    """Stop verification when a provider consumes the remaining shared time budget.

    :returns: None; no ref read follows the overdue native mirror response.
    """
    snapshot = REFS.RefSnapshot.parse(REMOTE_OUTPUT)
    engine, source, destination = _engine(FINISHED_MIRROR, REFS.RefSnapshot(()))
    clock = Mock(return_value=FIXED_CLOCK_SECONDS)
    engine.deadline = API.RequestDeadline(TIMEOUT_SECONDS, clock)
    source.sync.side_effect = lambda _mirror_id: destination.refs.configure_mock(
        return_value=snapshot
    )

    def exhausted(mirror_id: int) -> MIRRORING.PushMirror:
        """Consume the operation budget while returning otherwise successful state.

        :param mirror_id: Exact native mirror selected for verification.
        :returns: A fresh success response whose completion exceeded the deadline.
        """
        assert mirror_id == MIRROR_ID
        clock.return_value = float(TIMEOUT_SECONDS)
        return replace(
            FINISHED_MIRROR, last_successful_update_at=ADVANCED_SUCCESS_TIMESTAMP
        )

    source.mirror.side_effect = exhausted
    plan = engine.plan(
        WORKFLOW.MirrorIntent(SOURCE_PATH, DESTINATION_PATH),
        FUNCTIONAL_DIGEST,
        CREDENTIAL_DIGEST,
    )
    source.refs.reset_mock()
    destination.refs.reset_mock()

    with pytest.raises(API.APIError, match=API.REQUEST_DEADLINE_EXCEEDED):
        engine.apply(plan, confirmation=plan.digest)

    source.mirror.assert_called_once_with(MIRROR_ID)
    source.refs.assert_called_once_with(SOURCE_REPOSITORY)
    destination.refs.assert_called_once_with(DESTINATION_REPOSITORY)
    source.sync.assert_called_once_with(MIRROR_ID)


def test_failed_mirror_replacement_precedes_sync_and_next_execution_is_no_op() -> None:
    """Replace failed credentials despite unchanged policy, then preserve healthy state.

    :returns: None; exact call ordering and replacement evidence identify the old/new IDs.
    """
    snapshot = REFS.RefSnapshot.parse(REMOTE_OUTPUT)
    failed = replace(
        FINISHED_MIRROR,
        update_status=MIRRORING.MirrorStatus.FAILED,
        last_error_present=True,
    )
    replacement = replace(
        FINISHED_MIRROR,
        id=REPLACEMENT_MIRROR_ID,
        last_successful_update_at=ADVANCED_SUCCESS_TIMESTAMP,
    )
    engine, source, destination = _engine(failed, snapshot)
    source.configure.return_value = replacement
    source.mirror.return_value = replacement
    writes = Mock()
    writes.attach_mock(source.configure, "configure")
    writes.attach_mock(source.sync, "sync")
    intent = WORKFLOW.MirrorIntent(SOURCE_PATH, DESTINATION_PATH)
    plan = engine.plan(intent, FUNCTIONAL_DIGEST, CREDENTIAL_DIGEST)

    assert plan.replacement_required is True
    assert plan.replaced_mirror_id == MIRROR_ID
    applied = engine.apply(plan, confirmation=plan.digest)

    assert writes.mock_calls == [
        call.configure(DESTINATION_REPOSITORY, DESIRED_POLICY, USERNAME, TOKEN, failed),
        call.sync(REPLACEMENT_MIRROR_ID),
    ]
    assert applied.action.value == APPLIED_ACTION
    assert applied.created_mirror is True
    proof = dict(applied.evidence(DESTINATION_REPOSITORY))
    assert proof[FIELD.REPLACEMENT_REQUIRED] is True
    assert proof[FIELD.REPLACED_MIRROR_ID] == MIRROR_ID
    assert EVIDENCE.check_replacement_evidence(proof)
    proof[FIELD.MIRROR] = failed.evidence()
    assert not EVIDENCE.check_replacement_evidence(proof)

    source.mirrors.return_value = (replacement,)
    source.configure.reset_mock()
    source.sync.reset_mock()
    fresh = engine.plan(intent, FUNCTIONAL_DIGEST, CREDENTIAL_DIGEST)
    assert fresh.replacement_required is False
    assert fresh.replaced_mirror_id is None

    repeated = engine.apply(fresh, confirmation=fresh.digest)

    assert repeated.action.value == NO_OP_ACTION
    assert engine.mutated is False
    source.configure.assert_not_called()
    source.sync.assert_not_called()
    destination.create.assert_not_called()


def test_native_failed_mirror_replacement_deletes_only_selected_id_and_rebinds_token(
    tmp_path: Path, target_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delete the confirmed failed mirror, verify absence, then create with current auth.

    :param tmp_path: Test-owned directory for the shared replacement/create guard.
    :param target_file: Nonsecret selected GitLab session reference.
    :param monkeypatch: Redirect the existing guard's filesystem root.
    :returns: None; real native transport proves selected deletion and new-ID read-back.
    """
    failed = replace(
        FINISHED_MIRROR,
        update_status=MIRRORING.MirrorStatus.FAILED,
        last_error_present=True,
    )
    replacement = replace(
        FINISHED_MIRROR,
        id=REPLACEMENT_MIRROR_ID,
        last_successful_update_at=ADVANCED_SUCCESS_TIMESTAMP,
    )
    fields = MIRRORING.MirrorField
    body: JSONObject = {
        fields.URL: EXPECTED_SECRET_URL,
        fields.AUTH_METHOD: PASSWORD_AUTH,
        fields.ENABLED: True,
        fields.KEEP_DIVERGENT: True,
        fields.ONLY_PROTECTED: False,
    }
    exchanges = (
        _Exchange(
            HTTPMethod.GET,
            EXPECTED_MIRROR_ENDPOINT,
            _response(_native_mirror_payload(failed)),
        ),
        _Exchange(HTTPMethod.DELETE, EXPECTED_MIRROR_ENDPOINT, NO_CONTENT_RESPONSE),
        _Exchange(
            HTTPMethod.GET,
            EXPECTED_MIRROR_ENDPOINT,
            _response({}, HTTPStatus.NOT_FOUND),
            returncode=NATIVE_FAILURE,
        ),
        _Exchange(
            HTTPMethod.POST,
            EXPECTED_MIRRORS_ENDPOINT,
            _response({fields.ID: REPLACEMENT_MIRROR_ID}, HTTPStatus.CREATED),
            body=body,
        ),
        _Exchange(
            HTTPMethod.GET,
            EXPECTED_REPLACEMENT_ENDPOINT,
            _response(_native_mirror_payload(replacement)),
        ),
    )
    requests = _NativeRequests(exchanges)
    monkeypatch.setattr(GITLAB_API, "_lock_root", lambda: tmp_path / PROJECT_SCOPE)
    source = _native_source(requests, target_file)

    observed = source.configure(
        DESTINATION_REPOSITORY, DESIRED_POLICY, USERNAME, TOKEN, failed
    )

    assert observed == replacement
    assert requests.calls == [
        (exchange.method, exchange.endpoint) for exchange in exchanges
    ]
    assert all(not path.exists() for path in requests.body_paths)
