"""Consumer file commits and MR milestone changes use native GitLab read-back.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Final, Optional
from urllib.parse import urlencode

import pytest
from conftest import import_script_module
from lib.python.core.project_binding import TARGET_ENV
from test_gitlab_actions import FakeAPI, _list, _plan, _tag_output

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject

SCHEMA = import_script_module("lib.python.core.schema")
API = import_script_module("lib.python.core.gitlab.gitlab_api")
ACTIONS = import_script_module("lib.python.core.gitlab.gitlab_actions")
GITLAB_ACTION = import_script_module("lib.python.core.gitlab.gitlab_action")
REPOSITORY = import_script_module("lib.python.core.gitlab.gitlab_repository")
MERGE_REQUESTS = import_script_module("lib.python.core.gitlab.gitlab_merge_requests")
REPOSITORY_COMMAND = import_script_module("gitlab_repository")
MR_COMMAND = import_script_module("gitlab_mr")
STATUS = import_script_module("lib.python.sys.status")

# Multi-line Markdown MR description a caller keeps in a file (issue #151).
MR_DESCRIPTION: Final = (
    "## Summary\n\nAdd `consumer` files.\n\n- step one\n- step two\n"
)
MR_SOURCE_BRANCH: Final = "consumer"
MR_TARGET_BRANCH: Final = "main"
MR_TITLE: Final = "Consumer files"
MR_CREATE: Final = (
    "create",
    "--source-branch",
    MR_SOURCE_BRANCH,
    "--target-branch",
    MR_TARGET_BRANCH,
    "--title",
    MR_TITLE,
)
# Project path ``_plan`` binds and the access-verified numeric ID the tests pass to apply.
MR_PROJECT: Final = "team/repo"
MR_TARGET_ID: Final = 42
MR_ENDPOINT: Final = f"projects/{MR_TARGET_ID}/merge_requests"
MR_BRANCH_QUERY: Final = urlencode(
    {
        "state": "opened",
        "source_branch": MR_SOURCE_BRANCH,
        "target_branch": MR_TARGET_BRANCH,
    }
)
MR_DESCRIPTION_FILE_NAME: Final = "description.md"


@pytest.fixture(autouse=True)
def isolated_create_guards(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep real shared create guards private to this deterministic test.

    :param monkeypatch: Fixture selecting the existing lock directory resolver.
    :param tmp_path: Unique temporary directory owned by this test invocation.
    """
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")


def _file_record(path: str, content: bytes, sha: str) -> JSONObject:
    """Represent GitLab's native file response at one immutable commit.

    :param path: Exact repository-relative filename.
    :param content: File bytes independently returned by the provider.
    :param sha: Commit selected by the read request.
    :returns: Native base64 file response with immutable commit identity.
    """
    return {
        "file_path": path,
        "commit_id": sha,
        "content": base64.b64encode(content).decode("ascii"),
        "encoding": "base64",
    }


@pytest.mark.parametrize("matches", (True, False))
def test_commit_preserves_native_bytes_and_requires_matching_readback(
    tmp_path: Path, matches: bool
) -> None:
    """Commit text and binary content once; retry only matching content as NO_OP.

    :param tmp_path: Directory for the caller's native actions JSON.
    :param matches: Whether independent file reads return the requested bytes.
    """
    text = "package main\n\n// preserve UTF-8: café\n"
    binary = b"\x00\xff\r\n"
    actions = [
        {"action": "create", "file_path": "main.go", "content": text},
        {
            "action": "create",
            "file_path": "asset.bin",
            "encoding": "base64",
            "content": base64.b64encode(binary).decode("ascii"),
        },
    ]
    source = tmp_path / "actions.json"
    source.write_text(json.dumps(actions), encoding="utf-8")
    args = REPOSITORY_COMMAND.build_parser().parse_args(
        [
            "commit",
            "--branch",
            "consumer",
            "--commit-message",
            "Consumer files",
            "--actions-file",
            str(source),
        ]
    )
    prepared = REPOSITORY.prepare(args)
    assert prepared.body["actions"] == actions
    plan = _plan("gitlab_repository", "commit", prepared.body)
    sha = "b" * 40
    base = "projects/42/repository"
    branch = {"name": "consumer", "commit": {"id": sha}}
    reads = 2 if matches else 1
    api = FakeAPI(
        {
            ("GET", base + "/branches/consumer"): [
                API.GitLabAPIError("provider_404"),
                API.GitLabAPIError("provider_404"),
                *([branch] * 3 if matches else []),
            ],
            ("POST", base + "/commits"): [{"id": sha}],
            ("GET", base + "/commits/" + sha): [
                {"id": sha, "message": "Consumer files\n"}
            ]
            * reads,
            ("GET", base + "/files/main.go?ref=" + sha): [
                _file_record("main.go", text.encode() if matches else b"wrong", sha)
            ]
            * reads,
            ("GET", base + "/files/asset.bin?ref=" + sha): [
                _file_record("asset.bin", binary, sha)
            ]
            * reads,
        }
    )

    result = REPOSITORY.apply(api, object(), plan, 42)

    assert result["verified"] is matches
    writes = [call for call in api.calls if call[0] != "GET"]
    assert writes == [("POST", base + "/commits", prepared.body)]
    if matches:
        assert result["files"] == [
            {
                "file_path": "main.go",
                "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
            },
            {
                "file_path": "asset.bin",
                "content_sha256": hashlib.sha256(binary).hexdigest(),
            },
        ]
        repeated = REPOSITORY.apply(api, object(), plan, 42)
        assert repeated["action"] == "NO_OP"
        assert repeated["commit_sha"] == sha
        assert repeated["verified"] is True
        assert [call for call in api.calls if call[0] != "GET"] == writes
    else:
        assert result["mutated"] is True
        assert result["errors"][0]["reason"] == "repository_commit_readback_unverified"


def _mr_record(milestone_id: int) -> JSONObject:
    """Return one exact project MR with a native nested milestone identity.

    :param milestone_id: Global milestone ID returned by the provider.
    :returns: Complete MR identity used by collection and independent detail reads.
    """
    return {
        "id": 81,
        "iid": 7,
        "title": MR_TITLE,
        "state": "opened",
        "source_project_id": MR_TARGET_ID,
        "target_project_id": MR_TARGET_ID,
        "source_branch": MR_SOURCE_BRANCH,
        "target_branch": MR_TARGET_BRANCH,
        "milestone": {"id": milestone_id},
    }


def test_mr_create_reads_exact_iid_and_repeated_creation_has_no_post() -> None:
    """Create the requested branch MR and reuse only its verified milestone."""
    args = MR_COMMAND.build_parser().parse_args([*MR_CREATE, "--milestone-id", "113"])
    prepared = MERGE_REQUESTS.prepare(args)
    plan = _plan("gitlab_mr", "create", prepared.body)
    record = _mr_record(113)
    api = FakeAPI(
        {
            ("GET", _list(MR_ENDPOINT + "?" + MR_BRANCH_QUERY)): [[], [record]],
            ("POST", MR_ENDPOINT): [{"id": 81, "iid": 7}],
            ("GET", MR_ENDPOINT + "/7"): [record, record],
        }
    )

    created = MERGE_REQUESTS.apply(api, object(), plan, MR_TARGET_ID)
    repeated = MERGE_REQUESTS.apply(api, object(), plan, MR_TARGET_ID)

    assert created == {
        "action": STATUS.APPLIED,
        "id": 81,
        "iid": 7,
        "title": MR_TITLE,
        "milestone_id": 113,
        "verified": True,
    }
    assert repeated == {**created, "action": STATUS.NO_OP}
    assert [call for call in api.calls if call[0] != "GET"] == [
        ("POST", MR_ENDPOINT, prepared.body)
    ]


@pytest.mark.parametrize("matches", (True, False))
def test_mr_milestone_assignment_reads_back_before_success(matches: bool) -> None:
    """Assign only the milestone and reject stale independent milestone evidence.

    :param matches: Whether the post-write detail read contains the requested ID.
    """
    args = MR_COMMAND.build_parser().parse_args(
        ["update", "--mr-iid", "7", "--milestone-id", "113"]
    )
    prepared = MERGE_REQUESTS.prepare(args)
    plan = _plan("gitlab_mr", "update", prepared.body, resource_id=prepared.resource_id)
    endpoint = MR_ENDPOINT + "/7"
    old = _mr_record(112)
    updated = _mr_record(113) if matches else old
    api = FakeAPI(
        {
            ("GET", endpoint): [old, updated, *([updated] if matches else [])],
            ("PUT", endpoint): [{"id": 81}],
        }
    )

    result = MERGE_REQUESTS.apply(api, object(), plan, MR_TARGET_ID)

    assert result["verified"] is matches
    assert [call for call in api.calls if call[0] != "GET"] == [
        ("PUT", endpoint, {"milestone_id": 113})
    ]
    if matches:
        repeated = MERGE_REQUESTS.apply(api, object(), plan, MR_TARGET_ID)
        assert repeated["action"] == STATUS.NO_OP
        assert repeated["milestone_id"] == 113
        assert [call[0] for call in api.calls].count("PUT") == 1
    else:
        assert result["mutated"] is True
        assert result["errors"][0]["reason"] == "mr_readback_mismatch"


def _described_record(description: Optional[str]) -> JSONObject:
    """Return the exact project MR with the description GitLab reads back.

    :param description: Provider description text, or ``None`` when the MR has none.
    :returns: MR record from ``_mr_record`` with its native description field.
    """
    return {**_mr_record(113), ACTIONS.DESCRIPTION_FIELD: description}


def _description_file(directory: Path, text: str) -> Path:
    """Write a caller's MR description file.

    :param directory: Test-owned directory receiving the file.
    :param text: Exact UTF-8 file content.
    :returns: Path passed to ``--description-file``.
    """
    path = directory / MR_DESCRIPTION_FILE_NAME
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "described", (True, False), ids=("description-file", "no-description-file")
)
def test_mr_create_posts_description_file_text_and_reads_it_back(
    tmp_path: Path, described: bool
) -> None:
    """Send the file text as the MR description and verify it before success.

    GitLab removes the terminal newline from a stored description, so the
    read-back and the repeated create compare the text without it. Without the
    option the POST carries no description field.

    :param tmp_path: Directory holding the caller's description file.
    :param described: Whether the caller passes ``--description-file``.
    """
    source = _description_file(tmp_path, MR_DESCRIPTION)
    selected = ("--description-file", str(source)) if described else ()
    args = MR_COMMAND.build_parser().parse_args([*MR_CREATE, *selected])
    plan = _plan("gitlab_mr", "create", MERGE_REQUESTS.prepare(args).body)
    record = _described_record(MR_DESCRIPTION.rstrip("\n") if described else None)
    api = FakeAPI(
        {
            ("GET", _list(MR_ENDPOINT + "?" + MR_BRANCH_QUERY)): [[], [record]],
            ("POST", MR_ENDPOINT): [{"id": 81, "iid": 7}],
            ("GET", MR_ENDPOINT + "/7"): [record, record],
        }
    )

    created = MERGE_REQUESTS.apply(api, object(), plan, MR_TARGET_ID)
    repeated = MERGE_REQUESTS.apply(api, object(), plan, MR_TARGET_ID)

    posts = [body for method, _endpoint, body in api.calls if method == "POST"]
    assert len(posts) == 1
    if described:
        assert posts[0][ACTIONS.DESCRIPTION_FIELD] == MR_DESCRIPTION
    else:
        assert ACTIONS.DESCRIPTION_FIELD not in posts[0]
    assert (created["action"], created["verified"]) == (STATUS.APPLIED, True)
    assert (repeated["action"], repeated["verified"]) == (STATUS.NO_OP, True)


def test_mr_create_reports_unverified_write_when_description_readback_differs(
    tmp_path: Path,
) -> None:
    """Keep a created MR unverified when GitLab stores a different description.

    :param tmp_path: Directory holding the caller's description file.
    """
    source = _description_file(tmp_path, MR_DESCRIPTION)
    args = MR_COMMAND.build_parser().parse_args(
        [*MR_CREATE, "--description-file", str(source)]
    )
    plan = _plan("gitlab_mr", "create", MERGE_REQUESTS.prepare(args).body)
    changed = MR_DESCRIPTION.replace("step two", "step changed")
    api = FakeAPI(
        {
            ("GET", _list(MR_ENDPOINT + "?" + MR_BRANCH_QUERY)): [[]],
            ("POST", MR_ENDPOINT): [{"id": 81, "iid": 7}],
            ("GET", MR_ENDPOINT + "/7"): [_described_record(changed)],
        }
    )

    result = MERGE_REQUESTS.apply(api, object(), plan, MR_TARGET_ID)

    assert result["verified"] is False
    assert result["mutated"] is True
    assert result["errors"] == [
        {"project_id": MR_TARGET_ID, "reason": "mr_readback_mismatch"}
    ]


def test_mr_create_refuses_open_mr_with_another_description_without_post(
    tmp_path: Path,
) -> None:
    """Refuse to reuse the branches' open MR when its description differs.

    This happens when a caller edits the description file and repeats create
    for branches that already have an open MR; ``update`` sets only milestones.

    :param tmp_path: Directory holding the caller's description file.
    """
    source = _description_file(tmp_path, MR_DESCRIPTION)
    args = MR_COMMAND.build_parser().parse_args(
        [*MR_CREATE, "--description-file", str(source)]
    )
    plan = _plan("gitlab_mr", "create", MERGE_REQUESTS.prepare(args).body)
    existing = _described_record("Earlier description")
    api = FakeAPI(
        {
            ("GET", _list(MR_ENDPOINT + "?" + MR_BRANCH_QUERY)): [[existing]],
            ("GET", MR_ENDPOINT + "/7"): [existing],
        }
    )

    with pytest.raises(
        MERGE_REQUESTS.ActionError, match="existing_mr_differs_use_update"
    ):
        MERGE_REQUESTS.apply(api, object(), plan, MR_TARGET_ID)

    assert [method for method, _endpoint, _body in api.calls] == ["GET", "GET"]


def test_mr_update_rejects_description_file(tmp_path: Path) -> None:
    """Keep update milestone-only instead of ignoring a selected description file.

    :param tmp_path: Directory holding the caller's description file.
    """
    source = _description_file(tmp_path, MR_DESCRIPTION)
    args = MR_COMMAND.build_parser().parse_args(
        [
            "update",
            "--mr-iid",
            "7",
            "--milestone-id",
            "113",
            "--description-file",
            str(source),
        ]
    )

    with pytest.raises(
        MERGE_REQUESTS.ActionError, match="mr_update_accepts_milestone_only"
    ):
        MERGE_REQUESTS.prepare(args)


@pytest.fixture
def offline_mr_target(target_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Select the shared fixture target and fail on any provider access.

    :param target_file: Shared nonsecret fixture target from ``conftest``.
    :param monkeypatch: Fixture selecting the target and replacing provider entry points.
    """
    monkeypatch.setenv(TARGET_ENV, str(target_file))

    def provider_access(*_args: object, **_kwargs: object) -> None:
        """Fail the test when the command reaches GitLab.

        :param *_args: Ignored positional arguments of the replaced entry point.
        :param **_kwargs: Ignored keyword arguments of the replaced entry point.
        """
        pytest.fail("gitlab_mr.py reached GitLab before its plan was confirmed")

    monkeypatch.setattr(GITLAB_ACTION, "bind_gitlab_session", provider_access)
    monkeypatch.setattr(GITLAB_ACTION, "GlabAPIClient", provider_access)


@pytest.mark.usefixtures("offline_mr_target")
def test_mr_plan_binds_description_bytes_and_edited_file_refuses_apply(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bind the description file bytes into the plan without printing them.

    A caller who edits the file after the dry run gets a new digest, and an
    apply confirmed with the earlier digest stops before any GitLab access.

    :param tmp_path: Directory holding the caller's description file.
    :param capsys: Fixture capturing the plan and refusal reports.
    """
    source = _description_file(tmp_path, MR_DESCRIPTION)
    argv = [*MR_CREATE, "--project", MR_PROJECT, "--description-file", str(source)]

    planned_code = MR_COMMAND.main([*argv, "--json"])
    planned_out = capsys.readouterr().out
    source.write_text(MR_DESCRIPTION + "- step three\n", encoding="utf-8")
    edited_code = MR_COMMAND.main([*argv, "--json"])
    edited = json.loads(capsys.readouterr().out)
    planned = json.loads(planned_out)
    refused_code = MR_COMMAND.main(
        [*argv, "--apply", "--confirm-plan", planned["plan_digest"], "--json"]
    )
    refused = json.loads(capsys.readouterr().out)

    assert planned_code == edited_code == STATUS.exit_code(STATUS.DRY_RUN)
    assert ACTIONS.DESCRIPTION_FIELD in planned["plan"]["fields"]
    assert [
        line for line in MR_DESCRIPTION.splitlines() if line and line in planned_out
    ] == []
    assert planned["plan"]["body_sha256"] != edited["plan"]["body_sha256"]
    assert planned["plan_digest"] != edited["plan_digest"]
    assert refused_code == STATUS.exit_code(STATUS.BLOCKED)
    assert [error["reason"] for error in refused["errors"]] == [
        "confirm_plan_mismatch_rerun_dry_run"
    ]


@pytest.mark.usefixtures("offline_mr_target")
@pytest.mark.parametrize(
    ("content", "status", "reasons"),
    (
        (None, STATUS.BLOCKED, ["description_file_unreadable"]),
        (b"\xff\xfe invalid", STATUS.BLOCKED, ["description_file_unreadable"]),
        (b"a" * ACTIONS.ACTION_FILE_MAX_BYTES, STATUS.DRY_RUN, []),
        (
            b"a" * (ACTIONS.ACTION_FILE_MAX_BYTES + 1),
            STATUS.BLOCKED,
            ["description_file_too_large"],
        ),
    ),
    ids=("missing", "not-utf-8", "at-limit", "over-limit"),
)
def test_mr_create_plans_only_a_usable_description_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    content: Optional[bytes],
    status: str,
    reasons: list[str],
) -> None:
    """Plan a description file up to the size limit and block an unusable one.

    A wrong path, a file saved in another encoding, or an oversized generated
    file each stop before any GitLab access; a file of exactly the limit plans.

    :param tmp_path: Directory holding the caller's description file.
    :param capsys: Fixture capturing the plan or blocked report.
    :param content: File bytes, or ``None`` to leave the path absent.
    :param status: Expected report status.
    :param reasons: Expected error reasons, empty for a plan.
    """
    source = tmp_path / MR_DESCRIPTION_FILE_NAME
    if content is not None:
        source.write_bytes(content)

    code = MR_COMMAND.main(
        [
            *MR_CREATE,
            "--project",
            MR_PROJECT,
            "--description-file",
            str(source),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert code == STATUS.exit_code(status)
    assert report["status"] == status
    assert [error["reason"] for error in report["errors"]] == reasons


@pytest.mark.parametrize("kind", ("gitlab_repository", "gitlab_merge_request"))
def test_action_schema_requires_each_delivered_evidence_field(kind: str) -> None:
    """Reject PASS records and read-back that omit the new operation's evidence.

    :param kind: Repository commit or MR operation whose successful output is checked.
    """
    if kind == "gitlab_repository":
        operation = "commit"
        record = {
            "action": "APPLIED",
            "verified": True,
            "mutated": True,
            "branch": "consumer",
            "commit_sha": "b" * 40,
            "files": [{"file_path": "main.go", "content_sha256": "c" * 64}],
        }
        fields = ("files", "file_path", "content_sha256")
    else:
        operation = "create"
        record = {
            "action": "APPLIED",
            "verified": True,
            "mutated": True,
            "id": 81,
            "iid": 7,
            "title": "Consumer files",
            "milestone_id": 113,
        }
        fields = ("id", "iid", "title", "milestone_id")
    result = _tag_output(_plan(kind, operation, {}), record)
    SCHEMA.validate_specification(result, "results/gitlab-action.schema.json")
    if kind == "gitlab_merge_request":
        unassigned = deepcopy(result)
        unassigned["records"][0]["milestone_id"] = None
        unassigned["readback"]["milestone_id"] = None
        SCHEMA.validate_specification(unassigned, "results/gitlab-action.schema.json")

    for section in ("records", "readback"):
        for field in fields:
            invalid = deepcopy(result)
            evidence = (
                invalid["records"][0] if section == "records" else invalid["readback"]
            )
            if kind == "gitlab_repository" and field != "files":
                del evidence["files"][0][field]
            else:
                del evidence[field]
            with pytest.raises(SCHEMA.SpecificationError):
                SCHEMA.validate_specification(
                    invalid, "results/gitlab-action.schema.json"
                )
