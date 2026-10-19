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
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import pytest
from conftest import import_script_module
from test_gitlab_actions import FakeAPI, _list, _plan, _tag_output

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject

SCHEMA = import_script_module("lib.python.core.schema")
API = import_script_module("lib.python.core.gitlab.gitlab_api")
REPOSITORY = import_script_module("lib.python.core.gitlab.gitlab_repository")
MERGE_REQUESTS = import_script_module("lib.python.core.gitlab.gitlab_merge_requests")
REPOSITORY_COMMAND = import_script_module("gitlab_repository")
MR_COMMAND = import_script_module("gitlab_mr")


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
        "title": "Consumer files",
        "state": "opened",
        "source_project_id": 42,
        "target_project_id": 42,
        "source_branch": "consumer",
        "target_branch": "main",
        "milestone": {"id": milestone_id},
    }


def test_mr_create_reads_exact_iid_and_repeated_creation_has_no_post() -> None:
    """Create the requested branch MR and reuse only its verified milestone."""
    args = MR_COMMAND.build_parser().parse_args(
        [
            "create",
            "--source-branch",
            "consumer",
            "--target-branch",
            "main",
            "--title",
            "Consumer files",
            "--milestone-id",
            "113",
        ]
    )
    prepared = MERGE_REQUESTS.prepare(args)
    plan = _plan("gitlab_mr", "create", prepared.body)
    base = "projects/42/merge_requests"
    query = urlencode(
        {"state": "opened", "source_branch": "consumer", "target_branch": "main"}
    )
    record = _mr_record(113)
    api = FakeAPI(
        {
            ("GET", _list(base + "?" + query)): [[], [record]],
            ("POST", base): [{"id": 81, "iid": 7}],
            ("GET", base + "/7"): [record, record],
        }
    )

    created = MERGE_REQUESTS.apply(api, object(), plan, 42)
    repeated = MERGE_REQUESTS.apply(api, object(), plan, 42)

    assert created == {
        "action": "APPLIED",
        "id": 81,
        "iid": 7,
        "title": "Consumer files",
        "milestone_id": 113,
        "verified": True,
    }
    assert repeated == {**created, "action": "NO_OP"}
    assert [call for call in api.calls if call[0] != "GET"] == [
        ("POST", base, prepared.body)
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
    endpoint = "projects/42/merge_requests/7"
    old = _mr_record(112)
    updated = _mr_record(113) if matches else old
    api = FakeAPI(
        {
            ("GET", endpoint): [old, updated, *([updated] if matches else [])],
            ("PUT", endpoint): [{"id": 81}],
        }
    )

    result = MERGE_REQUESTS.apply(api, object(), plan, 42)

    assert result["verified"] is matches
    assert [call for call in api.calls if call[0] != "GET"] == [
        ("PUT", endpoint, {"milestone_id": 113})
    ]
    if matches:
        repeated = MERGE_REQUESTS.apply(api, object(), plan, 42)
        assert repeated["action"] == "NO_OP"
        assert repeated["milestone_id"] == 113
        assert [call[0] for call in api.calls].count("PUT") == 1
    else:
        assert result["mutated"] is True
        assert result["errors"][0]["reason"] == "mr_readback_mismatch"


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
