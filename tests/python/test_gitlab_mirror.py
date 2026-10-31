"""Verify Git mirror ref evidence and private native authentication boundaries.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
import hashlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Final, Optional
from unittest.mock import Mock, create_autospec

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

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject

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
SOURCE_HOST: Final[str] = "gitlab.example.test"
DESTINATION_HOST: Final[str] = "github.example.test"
SOURCE_PATH: Final[str] = "unit/source"
DESTINATION_PATH: Final[str] = "unit/destination"
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
    }

    assert EVIDENCE.check_ref_evidence(record) is expected


def test_mirror_parser_composes_mode_controls_and_shared_output_options(
    tmp_path: Path,
) -> None:
    """Construct the installed parser without re-registering shared persistence flags.

    :param tmp_path: Temporary paths accepted as output arguments without file writes.
    :returns: None; two parses verify dry-run and live-plan persistence controls.
    """
    parser = CLI.build_parser()
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
