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
from functools import cache
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
MIRROR_APPLIED_FILE: Final[str] = "mirror-applied.json"
MIRROR_NO_OP_FILE: Final[str] = "mirror-noop.json"
BEFORE_FIELD: Final[str] = "before"
OBSERVATION_FIELDS: Final[tuple[str, ...]] = (
    "source",
    "destination",
    "mirror",
    "source_refs",
    "destination_refs",
)
PROFILE_NAME: Final[str] = "public"
ALTERNATE_PROFILE_NAME: Final[str] = "alternate"
PROFILE_OPTION: Final[str] = "--gitlab-profile"
MINIMUM_POLL_SECONDS: Final[int] = 30
POLL_BUDGET_SECONDS: Final[int] = 3 * MINIMUM_POLL_SECONDS
PAIR_ACTIONS_REASON: Final[str] = "mirror_pair_actions_unverified"
PAIR_IDENTITY_REASON: Final[str] = "mirror_pair_identity_mismatch"
PAIR_REFS_REASON: Final[str] = "mirror_pair_refs_unverified"
PAIR_MUTATION_REASON: Final[str] = "mirror_pair_mutation_unverified"
PAIR_CONTINUITY_REASON: Final[str] = "mirror_pair_continuity_unverified"
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
CREATION_ID_BASELINE: Final[tuple[tuple[str, int], ...]] = (
    ("post", DESTINATION_ID),
    ("first-read", DESTINATION_ID),
    ("visibility-read", DESTINATION_ID),
    ("final-read", DESTINATION_ID),
)
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


@cache
def _mirror_pair_baseline() -> str:
    """Run one offline lifecycle and freeze both real evidence envelopes as JSON.

    :returns: Immutable APPLIED and NO_OP receipts sharing independently read state.
    """
    snapshot = REFS.RefSnapshot.parse(REMOTE_OUTPUT)
    engine, source, destination = _engine(None, REFS.RefSnapshot(()))
    destination.repository.return_value = None

    def created(path: str) -> MIRRORING.GitRepository:
        """Expose only the explicitly created destination to later provider reads.

        :param path: Consumer repository selected by the confirmed plan.
        :returns: Created destination with its actual provider identity.
        """
        assert path == DESTINATION_PATH
        destination.repository.return_value = DESTINATION_REPOSITORY
        return DESTINATION_REPOSITORY

    destination.create.side_effect = created
    source.sync.side_effect = lambda _mirror_id: destination.refs.configure_mock(
        return_value=snapshot
    )
    intent = WORKFLOW.MirrorIntent(SOURCE_PATH, DESTINATION_PATH, True)
    plan = engine.plan(intent, FUNCTIONAL_DIGEST, CREDENTIAL_DIGEST)
    applied = engine.apply(plan, confirmation=plan.digest)
    assert engine.mutated is True
    source.mirrors.return_value = (FINISHED_MIRROR,)
    fresh = engine.plan(intent, FUNCTIONAL_DIGEST, CREDENTIAL_DIGEST)
    noop = engine.apply(fresh, confirmation=fresh.digest)
    assert engine.mutated is False
    source.configure.assert_called_once()
    source.sync.assert_called_once_with(MIRROR_ID)
    destination.create.assert_called_once_with(DESTINATION_PATH)

    receipts: dict[str, JSONObject] = {}
    for name, execution, timestamp in (
        (MIRROR_APPLIED_FILE, applied, SUCCESS_TIMESTAMP),
        (MIRROR_NO_OP_FILE, noop, ADVANCED_SUCCESS_TIMESTAMP),
    ):
        receipt = CLI.report(
            CLI.KIND,
            f"https://{SOURCE_HOST}",
            intent.evidence(),
            [execution.evidence(DESTINATION_REPOSITORY)],
            [],
        )
        receipt.update(
            operation=SYNC_OPERATION,
            plan_digest=execution.plan.digest,
            mutated=execution.action.value == APPLIED_ACTION,
            captured_at=timestamp,
            cleanup={"status": "PASS", "failures": []},
            skill=PROVENANCE.SkillIdentity(
                algorithm=PROVENANCE.DIGEST_ALGORITHM,
                digest=FUNCTIONAL_DIGEST,
                file_count=ONE_MATCH,
                functional_digest=FUNCTIONAL_DIGEST,
                revision=PROVENANCE.Revision(
                    value=MAIN_OID, source=FIXTURE_CREDENTIAL_SOURCE, verified=True
                ),
                consuming_project=PROVENANCE.ConsumerRevision(commit=None, source=None),
            ),
            tested_revision=MAIN_OID,
            execution_host=FIXTURE_HOST,
            target_source=FIXTURE_CREDENTIAL_SOURCE,
        )
        receipts[name] = receipt
    return json.dumps(receipts, sort_keys=True)


def test_mirror_pair_preserves_absent_before_and_accepts_fresh_plan_digest() -> None:
    """Keep creation history truthful while accepting a confirmed repeat no-op.

    :returns: None; schema and pair checks validate real engine evidence together.
    """
    receipts = json.loads(_mirror_pair_baseline())
    applied = receipts[MIRROR_APPLIED_FILE]
    noop = receipts[MIRROR_NO_OP_FILE]
    applied_record = applied[FIELD.RECORDS][0]
    noop_record = noop[FIELD.RECORDS][0]

    assert applied_record[BEFORE_FIELD][FIELD.DESTINATION] == {
        "host": DESTINATION_HOST,
        "path": DESTINATION_PATH,
        "id": None,
    }
    assert applied_record[BEFORE_FIELD][FIELD.MIRROR] is None
    assert applied_record[FIELD.CREATED] == {"repository": True, "mirror": True}
    assert noop_record[FIELD.CREATED] == {"repository": False, "mirror": False}
    assert noop_record[BEFORE_FIELD] == {
        key: applied_record[key] for key in OBSERVATION_FIELDS
    }
    assert applied[FIELD.PLAN_DIGEST] != noop[FIELD.PLAN_DIGEST]
    assert not EVIDENCE.check_mirror_receipt(applied)
    assert not EVIDENCE.check_mirror_receipt(noop)
    assert not EVIDENCE.check_mirror_pair(applied, noop)


@pytest.mark.parametrize(
    ("name", "path", "replacement", "reason"),
    (
        (
            MIRROR_NO_OP_FILE,
            ("records", 0, "action"),
            APPLIED_ACTION,
            PAIR_ACTIONS_REASON,
        ),
        (
            MIRROR_NO_OP_FILE,
            ("skill", "digest"),
            CREDENTIAL_DIGEST,
            PAIR_IDENTITY_REASON,
        ),
        (
            MIRROR_NO_OP_FILE,
            ("skill", "functional_digest"),
            CREDENTIAL_DIGEST,
            PAIR_IDENTITY_REASON,
        ),
        (MIRROR_NO_OP_FILE, ("tested_revision",), OTHER_OID, PAIR_IDENTITY_REASON),
        (
            MIRROR_NO_OP_FILE,
            ("target_source",),
            f"{FIXTURE_CREDENTIAL_SOURCE};argv:{PROFILE_OPTION}={json.dumps(PROFILE_NAME)}",
            PAIR_IDENTITY_REASON,
        ),
        (
            MIRROR_NO_OP_FILE,
            ("skill", "revision", "value"),
            OTHER_OID,
            PAIR_IDENTITY_REASON,
        ),
        (
            MIRROR_NO_OP_FILE,
            ("records", 0, "source", "host"),
            DESTINATION_HOST,
            PAIR_IDENTITY_REASON,
        ),
        (
            MIRROR_NO_OP_FILE,
            ("records", 0, "source", "path"),
            DESTINATION_PATH,
            PAIR_IDENTITY_REASON,
        ),
        (
            MIRROR_NO_OP_FILE,
            ("records", 0, "destination", "host"),
            SOURCE_HOST,
            PAIR_IDENTITY_REASON,
        ),
        (
            MIRROR_NO_OP_FILE,
            ("records", 0, "destination", "path"),
            SOURCE_PATH,
            PAIR_IDENTITY_REASON,
        ),
        (
            MIRROR_NO_OP_FILE,
            ("records", 0, "source", "id"),
            SOURCE_ID + ONE_MATCH,
            PAIR_IDENTITY_REASON,
        ),
        (
            MIRROR_NO_OP_FILE,
            ("records", 0, "destination", "id"),
            DESTINATION_ID + ONE_MATCH,
            PAIR_IDENTITY_REASON,
        ),
        (
            MIRROR_NO_OP_FILE,
            ("records", 0, "mirror", "id"),
            REPLACEMENT_MIRROR_ID,
            PAIR_IDENTITY_REASON,
        ),
        (
            MIRROR_APPLIED_FILE,
            ("records", 0, "source_refs", "count"),
            0,
            PAIR_REFS_REASON,
        ),
        (
            MIRROR_APPLIED_FILE,
            ("records", 0, "verified_source_ref_count"),
            ONE_MATCH,
            PAIR_REFS_REASON,
        ),
        (MIRROR_APPLIED_FILE, ("mutated",), False, PAIR_MUTATION_REASON),
        (MIRROR_NO_OP_FILE, ("mutated",), True, PAIR_MUTATION_REASON),
        (
            MIRROR_APPLIED_FILE,
            ("records", 0, "created", "repository"),
            False,
            PAIR_MUTATION_REASON,
        ),
        (
            MIRROR_APPLIED_FILE,
            ("records", 0, "before", "destination", "id"),
            DESTINATION_ID,
            PAIR_MUTATION_REASON,
        ),
        (
            MIRROR_APPLIED_FILE,
            ("records", 0, "created", "mirror"),
            False,
            PAIR_MUTATION_REASON,
        ),
        (
            MIRROR_NO_OP_FILE,
            ("records", 0, "created", "mirror"),
            True,
            PAIR_MUTATION_REASON,
        ),
        (
            MIRROR_NO_OP_FILE,
            ("records", 0, "before", "destination_refs", "sha256"),
            CREDENTIAL_DIGEST,
            PAIR_CONTINUITY_REASON,
        ),
        (
            MIRROR_NO_OP_FILE,
            ("records", 0, "before", "mirror", "id"),
            REPLACEMENT_MIRROR_ID,
            PAIR_CONTINUITY_REASON,
        ),
    ),
    ids=(
        "wrong-repeat-action",
        "installed-tree",
        "functional-inputs",
        "tested-revision",
        "target-source",
        "installed-revision",
        "source-host",
        "source-path",
        "destination-host",
        "destination-path",
        "source-id",
        "destination-id",
        "native-mirror-id",
        "empty-source",
        "incomplete-refs",
        "applied-no-mutation",
        "noop-mutated",
        "repository-ownership",
        "fabricated-before-id",
        "mirror-ownership",
        "noop-creation",
        "before-refs",
        "before-native-id",
    ),
)
def test_mirror_pair_rejects_one_changed_evidence_invariant(
    name: str, path: tuple[str | int, ...], replacement: JSONValue, reason: str
) -> None:
    """Reject isolated contradictions in copies of one immutable lifecycle pair.

    :param name: Receipt whose single invariant is changed.
    :param path: Exact envelope or observation leaf changed for this case.
    :param replacement: Schema-valid value contradicting the paired evidence.
    :param reason: Stable pair diagnostic required for the changed invariant.
    :returns: None; assertions preserve the baseline and identify the rejection.
    """
    baseline = _mirror_pair_baseline()
    receipts = json.loads(baseline)
    selected = receipts[name]
    for key in path[:-1]:
        selected = selected[key]
    selected[path[-1]] = replacement

    failures = EVIDENCE.check_mirror_pair(
        receipts[MIRROR_APPLIED_FILE], receipts[MIRROR_NO_OP_FILE]
    )

    assert reason in failures
    assert _mirror_pair_baseline() == baseline


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


def test_sync_polling_uses_thirty_second_waits_and_one_absolute_deadline() -> None:
    """Pace unchanged native state without exceeding the caller's total time budget.

    :returns: None; fake time proves finite polling without a real sleep or API call.
    """
    snapshot = REFS.RefSnapshot.parse(REMOTE_OUTPUT)
    engine, source, destination = _engine(FINISHED_MIRROR, REFS.RefSnapshot(()))
    clock = Mock(return_value=FIXED_CLOCK_SECONDS)
    waits: list[float] = []

    def advanced(seconds: float) -> None:
        """Advance only fixture time for the scheduled bounded native poll wait.

        :param seconds: Requested delay before the next native observation.
        :returns: None; the shared absolute deadline observes elapsed fixture time.
        """
        waits.append(seconds)
        clock.return_value += seconds

    engine.deadline = API.RequestDeadline(POLL_BUDGET_SECONDS, clock)
    engine.sleep = advanced
    source.sync.side_effect = lambda _mirror_id: destination.refs.configure_mock(
        return_value=snapshot
    )
    plan = engine.plan(
        WORKFLOW.MirrorIntent(SOURCE_PATH, DESTINATION_PATH),
        FUNCTIONAL_DIGEST,
        CREDENTIAL_DIGEST,
    )

    with pytest.raises(API.APIError, match=API.REQUEST_DEADLINE_EXCEEDED):
        engine.apply(plan, confirmation=plan.digest)

    assert waits == [MINIMUM_POLL_SECONDS] * (
        POLL_BUDGET_SECONDS // MINIMUM_POLL_SECONDS
    )
    assert sum(waits) == POLL_BUDGET_SECONDS
    assert source.mirror.call_count == len(waits)
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


def test_command_forwards_named_gitlab_profile_without_binding_credentials(
    target_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Select a caller-named source authority through the actual mirror parser.

    :param target_file: Existing synthetic provider fixture extended by one profile.
    :param monkeypatch: Observe real resolution and trap offline authentication.
    :returns: None; the selected action carries the exact named profile and source.
    """
    with target_file.open("a", encoding=BODY_ENCODING) as stream:
        stream.write(
            f'\n[gitlab.{PROFILE_NAME}]\nurl="https://public.{SOURCE_HOST}"\n'
            f'project="{SOURCE_PATH}"\n'
        )
    monkeypatch.setenv(TARGET_ENVIRONMENT, str(target_file))
    resolution = Mock(wraps=CLI.resolve_target)
    authentication = Mock(side_effect=AssertionError("offline mode authenticated"))
    monkeypatch.setattr(CLI, "resolve_target", resolution)
    monkeypatch.setattr(CLI, "bind_gitlab_session", authentication)
    args = CLI.build_parser().parse_args(
        [*COMMAND_SELECTION, PROFILE_OPTION, PROFILE_NAME, DRY_RUN_OPTION]
    )

    action = CLI.MirrorCommand(args).prepare()

    assert action.target.require_gitlab().profile == PROFILE_NAME
    assert action.target.require_gitlab().host == f"public.{SOURCE_HOST}"
    assert action.intent.project == SOURCE_PATH
    assert resolution.call_args.kwargs["gitlab_profile"] == PROFILE_NAME
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
                FIELD.SOURCE_REFS: WORKFLOW.ref_evidence(snapshot),
                FIELD.DESTINATION_REFS: WORKFLOW.ref_evidence(empty),
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


@pytest.mark.parametrize(
    "changed",
    (None, "post", "first-read", "visibility-read", "final-read"),
    ids=(
        "matching-identities",
        "post-id",
        "first-read-id",
        "visibility-read-id",
        "final-read-id",
    ),
)
def test_github_private_creation_uses_independent_repository_and_visibility_reads(
    changed: Optional[str],
    target_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bind creation and final certification to the exact native POST identity.

    :param changed: One native identity changed in a copy of the immutable baseline.
    :param target_file: Existing nonsecret selected-target fixture.
    :param monkeypatch: Replace only target resolution and the outer workflow adapter.
    :param capsys: Capture the actual success or partial command report.
    :returns: None; mismatches preserve only the actual created ID as unverified evidence.
    """
    identities = dict(CREATION_ID_BASELINE)
    if changed is not None:
        identities[changed] = SOURCE_ID
    fields = GITHUB_REPOSITORY.RepositoryField
    repository: JSONObject = {
        fields.ID: DESTINATION_ID,
        fields.FULL_NAME: DESTINATION_PATH,
        fields.CLONE_URL: DESTINATION_CLONE_URL,
        fields.PRIVATE: True,
    }
    exchanges = (
        *(
            _Exchange(
                HTTPMethod.GET,
                EXPECTED_GITHUB_REPOSITORY_ENDPOINT,
                _response({}, HTTPStatus.NOT_FOUND),
                returncode=NATIVE_FAILURE,
            )
            for _ in range(POLL_OBSERVATION_COUNT)
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
            _response({fields.ID: identities["post"]}, HTTPStatus.CREATED),
            body={
                fields.NAME: DESTINATION_NAME,
                fields.PRIVATE: True,
                fields.AUTO_INIT: False,
            },
        ),
        *(
            _Exchange(
                HTTPMethod.GET,
                EXPECTED_GITHUB_REPOSITORY_ENDPOINT,
                _response({**repository, fields.ID: identities[stage]}),
            )
            for stage in ("first-read", "visibility-read", "final-read")
        ),
    )
    requests = _NativeRequests(exchanges)
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
    engine, source, _ = _engine(None, REFS.RefSnapshot(()))
    engine.destination = destination
    intent = WORKFLOW.MirrorIntent(SOURCE_PATH, DESTINATION_PATH, True)
    plan = engine.plan(intent, FUNCTIONAL_DIGEST, CREDENTIAL_DIGEST)
    baseline_record = json.loads(_mirror_pair_baseline())[MIRROR_APPLIED_FILE][
        FIELD.RECORDS
    ][0]
    bound_session = replace(
        _native_source(Mock(), target_file).service.session,
        skill=json.loads(_mirror_pair_baseline())[MIRROR_APPLIED_FILE][FIELD.SKILL],
    )

    def workflow(action: CLI.MirrorAction, state: CLI.MirrorState) -> Mock:
        """Run the real apply callback with native HTTP and typed source fixtures.

        :param action: Actual confirmed command inputs.
        :param state: Actual command-owned plan, record, and mutation state.
        :returns: Adapter whose execution retains the real callback and reporting path.
        """

        def run() -> None:
            """Apply once and let the command classify native identity failures.

            :returns: None after successful independently verified application.
            :raises API.APIError: A native identity does not match the created object.
            """
            state.session = bound_session
            state.engine = engine
            state.plan = plan
            state.record = {
                **baseline_record,
                **plan.observation.evidence(DESTINATION_HOST, DESTINATION_PATH),
                FIELD.ACTION: PLANNED_STATUS,
                FIELD.VERIFIED: False,
                FIELD.CREATED: {FIELD.REPOSITORY: False, FIELD.MIRROR: False},
                FIELD.VERIFIED_SOURCE_REF_COUNT: NATIVE_SUCCESS,
            }
            callback = CLI.ApplyMirror(action)
            callback.bind(state)
            callback.run()

        executor = Mock(spec=CLI.WorkflowExecutor)
        executor.run.side_effect = run
        return executor

    monkeypatch.setattr(
        CLI, "resolve_target", Mock(return_value=_selected_target(target_file))
    )
    monkeypatch.setattr(CLI, "workflow", workflow)
    code = CLI.main(
        [
            *COMMAND_SELECTION,
            CLI.CREATE_OPTION,
            CLI.APPLY_OPTION,
            CONFIRM_OPTION,
            plan.digest,
            JSON_OPTION,
        ]
    )
    result = json.loads(capsys.readouterr().out)
    SCHEMA.validate_specification(result, EVIDENCE.RESULT_SCHEMA)
    (record,) = result[FIELD.RECORDS]
    assert record[FIELD.DESTINATION][FIELD.ID] == identities["post"]
    assert record[FIELD.CREATED][FIELD.REPOSITORY] is True
    assert dict(CREATION_ID_BASELINE) == {
        stage: DESTINATION_ID for stage, _ in CREATION_ID_BASELINE
    }
    if changed is None:
        assert (code, result[FIELD.STATUS]) == (NATIVE_SUCCESS, "PASS")
        assert record[FIELD.VERIFIED] is True
        assert record[FIELD.CREATED][FIELD.MIRROR] is True
        reader.read.assert_called_once_with(
            DESTINATION_CLONE_URL, DESTINATION_OWNER, TOKEN
        )
        assert requests.calls == [
            (exchange.method, exchange.endpoint) for exchange in exchanges
        ]
    else:
        assert (code, result[FIELD.STATUS]) == (COMMAND_FAILURE, PARTIAL_STATUS)
        assert record[FIELD.VERIFIED] is False
        assert record[FIELD.VERIFIED_SOURCE_REF_COUNT] == NATIVE_SUCCESS
        assert result[FIELD.ERRORS]
        assert destination.created_repository_id == identities["post"]
        if changed != "final-read":
            source.configure.assert_not_called()
            source.sync.assert_not_called()
            reader.read.assert_not_called()
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
    assert proof[BEFORE_FIELD][FIELD.MIRROR][FIELD.ID] == MIRROR_ID
    assert proof[FIELD.MIRROR][FIELD.ID] == REPLACEMENT_MIRROR_ID
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
    repeated_proof = repeated.evidence(DESTINATION_REPOSITORY)
    applied_proof = applied.evidence(DESTINATION_REPOSITORY)
    assert repeated_proof[BEFORE_FIELD] == {
        key: applied_proof[key] for key in OBSERVATION_FIELDS
    }
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


def test_same_host_profile_alias_binds_provenance_and_confirmation_digest(
    target_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prevent profile substitution when host, project, refs, and credentials agree.

    :param target_file: Existing synthetic source reference reused by both aliases.
    :param monkeypatch: Replace only authentication I/O and provider construction.
    :returns: None; real callbacks retain selection and produce distinct confirmations.
    """
    selected = _selected_target(target_file)
    source_service = _native_source(Mock(), target_file).service
    skill = json.loads(_mirror_pair_baseline())[MIRROR_APPLIED_FILE][FIELD.SKILL]
    base_session = replace(source_service.session, skill=skill)
    engine, _, _ = _engine(FINISHED_MIRROR, REFS.RefSnapshot.parse(REMOTE_OUTPUT))
    monkeypatch.setattr(CLI, "MirrorEngine", Mock(return_value=engine))

    def bound(
        target: TARGET.GitLabOperationTarget,
        *,
        target_kind: str,
        target_reference: str,
        target_source: str,
        revision: Optional[str] = None,
    ) -> GITLAB_SESSION.BoundGitLabSession:
        """Retain caller provenance without reading a credential or provider.

        :param target: Exact source snapshot handed to the existing binder.
        :param target_kind: Selected project scope.
        :param target_reference: Consumer source project.
        :param target_source: Resolution provenance supplied by the auth callback.
        :param revision: Optional installed revision assertion.
        :returns: Same session identity with only its received target source changed.
        """
        assert target.gitlab.host == SOURCE_HOST
        assert target_kind == PROJECT_SCOPE
        assert target_reference == SOURCE_PATH
        assert revision is None
        return replace(base_session, target_source=target_source)

    monkeypatch.setattr(CLI, "bind_gitlab_session", bound)
    args = CLI.build_parser().parse_args([*COMMAND_SELECTION, CLI.LIVE_PLAN_OPTION])
    plans: list[WORKFLOW.MirrorPlan] = []
    sources: list[str] = []
    for name in (PROFILE_NAME, ALTERNATE_PROFILE_NAME):
        target = replace(
            selected, gitlab=replace(selected.require_gitlab(), profile=name)
        )
        action = CLI.MirrorAction(
            target,
            WORKFLOW.MirrorIntent(SOURCE_PATH, DESTINATION_PATH),
            CLI.ActionMode.LIVE_PLAN,
            TIMEOUT_SECONDS,
            None,
            None,
            API.RequestDeadline(TIMEOUT_SECONDS, lambda: FIXED_CLOCK_SECONDS),
        )
        command = CLI.MirrorCommand(args)
        authentication = CLI.MirrorSourceAuth(action)
        authentication.bind(command.state)
        authentication.read_config()
        assert command.state.session is not None
        expected_source = target.selection_source(CLI.EnvelopeField.TARGET.value)
        assert command.state.session.target_source == expected_source
        command.state.gitlab = source_service
        command.state.github_identity = GITHUB_SESSION.GitHubIdentitySession(
            DESTINATION_HOST,
            {GITHUB_TOKEN_VARIABLE: TOKEN},
            create_autospec(API.CLIAPIClient, instance=True),
            USERNAME,
            FIXTURE_CREDENTIAL_SOURCE,
        )
        observation = CLI.ObserveMirror(action)
        observation.bind(command.state)
        observation.run()
        assert command.state.plan is not None
        plans.append(command.state.plan)
        output = command.output(action, PLANNED_STATUS)
        SCHEMA.validate_specification(output, EVIDENCE.RESULT_SCHEMA)
        assert output["target_source"] == expected_source
        sources.append(command.state.session.target_source)

    assert sources[0] != sources[1]
    assert plans[0].observation == plans[1].observation
    assert plans[0].functional_digest == plans[1].functional_digest
    assert plans[0].credential_digest != plans[1].credential_digest
    assert plans[0].digest != plans[1].digest
