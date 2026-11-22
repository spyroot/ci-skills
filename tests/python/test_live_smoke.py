"""Regression coverage for the deterministic live smoke fixture lifecycle.

The module under test is stdlib-only and is loaded by path; transports are
faked by replacing the module's ``_urlopen`` seam, so no test reaches a
provider. The five operator-specified lifecycle verbs, the ledger round-trip,
the stop-if-present apply guard and the identity-guarded cleanup are the
behaviors pinned here.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from http import HTTPStatus
from pathlib import Path
from typing import Self

import pytest
from conftest import REPO_ROOT, load_module

live_smoke = load_module(
    "ci_skills_test_live_smoke_skillkit",
    REPO_ROOT / "tools" / "skillkit" / "live_smoke.py",
)

GITHUB_HOST = "api.github.test"
GITLAB_HOST = "gitlab.dcloud.test"


class FakeHTTPError(urllib.error.HTTPError):
    """HTTPError stand-in carrying code, body and headers."""

    def __init__(self, code: int, body: bytes, headers: dict[str, str]) -> None:
        """Pin the response facts urlopen would expose."""
        super().__init__(
            url="",
            code=code,
            msg=str(code),
            hdrs=headers,
            fp=io.BytesIO(body),
        )


class FakeResponse:
    """urlopen response stand-in for the success path."""

    def __init__(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        """Pin status, body and headers."""
        self.status = status
        self._body = body
        self.headers = headers

    def read(self) -> bytes:
        """Return the recorded response body."""
        return self._body

    def __enter__(self) -> Self:
        """Enter the response context."""
        return self

    def __exit__(self, *args: object) -> None:
        """Leave the response context."""


class FakeGitHub:
    """In-memory GitHub API answering the routes the smoke lifecycle uses."""

    def __init__(
        self, login: str = "spyroot", scopes: str = "repo, delete_repo"
    ) -> None:
        """Start with one authenticated user and no repositories."""
        self.login = login
        self.scopes = scopes
        self.user: dict[str, object] = {"login": login, "id": 11797329}
        self.repositories: dict[str, dict[str, object]] = {}
        self.by_id: dict[int, str] = {}
        self.next_id = 90000001

    def __call__(self, request: object, timeout_seconds: float = 0) -> FakeResponse:
        """Route one urllib request by method and path."""
        url = request.full_url
        method = request.get_method()
        path = urllib.parse.urlsplit(url).path
        body = (
            json.loads(request.data.decode("utf-8"))
            if getattr(request, "data", None)
            else None
        )
        if path == "/user" and method == "GET":
            return self._json(HTTPStatus.OK, self.user, {"X-OAuth-Scopes": self.scopes})
        if path == "/user/repos" and method == "POST":
            name = str(body["name"])
            full = f"{self.login}/{name}"
            if full in self.repositories:
                return self._error(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "name already exists on this account",
                )
            record = {
                "id": self.next_id,
                "name": name,
                "full_name": full,
                "private": bool(body.get("private", True)),
            }
            self.next_id += 1
            self.repositories[full] = record
            self.by_id[int(record["id"])] = full
            return self._json(HTTPStatus.CREATED, record)
        if path.startswith("/repos/") and method == "GET":
            full = path[len("/repos/") :]
            record = self.repositories.get(full)
            if record is None:
                return self._error(HTTPStatus.NOT_FOUND, "Not Found")
            return self._json(HTTPStatus.OK, record)
        if path.startswith("/repos/") and method == "PATCH":
            full = path[len("/repos/") :]
            record = self.repositories.get(full)
            if record is None:
                return self._error(HTTPStatus.NOT_FOUND, "Not Found")
            new_name = str(body["name"])
            new_full = f"{self.login}/{new_name}"
            del self.repositories[full]
            record["name"] = new_name
            record["full_name"] = new_full
            self.repositories[new_full] = record
            self.by_id[int(record["id"])] = new_full
            return self._json(HTTPStatus.OK, record)
        if path.startswith("/repos/") and method == "DELETE":
            full = path[len("/repos/") :]
            record = self.repositories.get(full)
            if record is None:
                return self._error(HTTPStatus.NOT_FOUND, "Not Found")
            del self.repositories[full]
            self.by_id.pop(int(record["id"]), None)
            return self._json(HTTPStatus.NO_CONTENT, None)
        if path.startswith("/repositories/") and method == "GET":
            repo_id = int(path[len("/repositories/") :])
            full = self.by_id.get(repo_id)
            if full is None or full not in self.repositories:
                return self._error(HTTPStatus.NOT_FOUND, "Not Found")
            return self._json(HTTPStatus.OK, self.repositories[full])
        return self._error(HTTPStatus.NOT_FOUND, f"unrouted {method} {path}")

    def _json(
        self, status: int, payload: object, headers: dict[str, str] | None = None
    ) -> FakeResponse:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        return FakeResponse(status, body, headers or {})

    def _error(self, status: int, message: str) -> FakeHTTPError:
        return FakeHTTPError(
            int(status), json.dumps({"message": message}).encode("utf-8"), {}
        )


class FakeGitLab:
    """In-memory GitLab API answering the routes the smoke lifecycle uses."""

    def __init__(self, login: str = "spyroot") -> None:
        """Start with one authenticated user and no groups or projects."""
        self.user: dict[str, object] = {"username": login, "id": 7}
        self.groups: dict[str, dict[str, object]] = {}
        self.projects: dict[str, dict[str, object]] = {}
        self.next_group_id = 5001
        self.next_project_id = 6001

    def __call__(self, request: object, timeout_seconds: float = 0) -> FakeResponse:
        """Route one urllib request by method and path."""
        url = request.full_url
        method = request.get_method()
        path = urllib.parse.urlsplit(url).path
        body = (
            json.loads(request.data.decode("utf-8"))
            if getattr(request, "data", None)
            else None
        )
        if path == "/api/v4/user" and method == "GET":
            return self._json(HTTPStatus.OK, self.user)
        if path == "/api/v4/groups" and method == "POST":
            name = str(body["path"])
            if name in self.groups:
                return self._error(HTTPStatus.BAD_REQUEST, "has already been taken")
            record = {"id": self.next_group_id, "full_path": name}
            self.next_group_id += 1
            self.groups[name] = record
            return self._json(HTTPStatus.CREATED, record)
        if path.startswith("/api/v4/groups/") and method == "GET":
            name = urllib.parse.unquote(path[len("/api/v4/groups/") :])
            record = self.groups.get(name)
            if record is None:
                return self._error(HTTPStatus.NOT_FOUND, "404 Group Not Found")
            return self._json(HTTPStatus.OK, record)
        if path == "/api/v4/projects" and method == "POST":
            group = self._find_group(int(body["namespace_id"]))
            if group is None:
                return self._error(HTTPStatus.NOT_FOUND, "404 Group Not Found")
            full = f"{group['full_path']}/{body['name']}"
            if full in self.projects:
                return self._error(HTTPStatus.BAD_REQUEST, "has already been taken")
            record = {
                "id": self.next_project_id,
                "name": body["name"],
                "path_with_namespace": full,
            }
            self.next_project_id += 1
            self.projects[full] = record
            return self._json(HTTPStatus.CREATED, record)
        if path.startswith("/api/v4/projects/") and method == "GET":
            key = urllib.parse.unquote(path[len("/api/v4/projects/") :])
            record = self.projects.get(key)
            if record is None:
                return self._error(HTTPStatus.NOT_FOUND, "404 Project Not Found")
            return self._json(HTTPStatus.OK, record)
        if path.startswith("/api/v4/projects/") and method == "PUT":
            project_id = int(path[len("/api/v4/projects/") :])
            record = self._find_project(project_id)
            if record is None:
                return self._error(HTTPStatus.NOT_FOUND, "404 Project Not Found")
            record["name"] = body["name"]
            return self._json(HTTPStatus.OK, record)
        if path.startswith("/api/v4/projects/") and method == "DELETE":
            project_id = int(path[len("/api/v4/projects/") :])
            record = self._find_project(project_id)
            if record is None:
                return self._error(HTTPStatus.NOT_FOUND, "404 Project Not Found")
            del self.projects[str(record["path_with_namespace"])]
            return self._json(HTTPStatus.ACCEPTED, None)
        return self._error(HTTPStatus.NOT_FOUND, f"unrouted {method} {path}")

    def _find_group(self, group_id: int) -> dict[str, object] | None:
        for record in self.groups.values():
            if record["id"] == group_id:
                return record
        return None

    def _find_project(self, project_id: int) -> dict[str, object] | None:
        for record in self.projects.values():
            if record["id"] == project_id:
                return record
        return None

    def _json(self, status: int, payload: object) -> FakeResponse:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        return FakeResponse(int(status), body, {})

    def _error(self, status: int, message: str) -> FakeHTTPError:
        return FakeHTTPError(
            int(status), json.dumps({"message": message}).encode("utf-8"), {}
        )


@pytest.fixture
def smoke(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[live_smoke.LiveSmoke, FakeGitHub, FakeGitLab]:
    """Bind one smoke session over both fakes through the module seam."""
    github_fake = FakeGitHub()
    gitlab_fake = FakeGitLab()

    def router(request: object, timeout_seconds: float) -> object:
        host = urllib.parse.urlsplit(request.full_url).netloc
        if host == GITHUB_HOST:
            return github_fake(request, timeout_seconds)
        if host == GITLAB_HOST:
            return gitlab_fake(request, timeout_seconds)
        raise AssertionError(f"unexpected host {host}")

    monkeypatch.setattr(live_smoke, "_urlopen", router)
    session = live_smoke.LiveSmoke(
        live_smoke.GitHubSmoke(
            live_smoke.RestClient(f"https://{GITHUB_HOST}", {}),
            "spyroot",
            credential_source="file:/token",
            token_kind=live_smoke.TOKEN_KIND_FINE_GRAINED,
        ),
        live_smoke.GitLabSmoke(live_smoke.RestClient(f"https://{GITLAB_HOST}", {})),
        "spyroot",
        "ci-skills-smoke",
    )
    return session, github_fake, gitlab_fake


def test_fixture_name_is_deterministic() -> None:
    """Same inputs give the same shared name; bad segments raise."""
    assert live_smoke.fixture_name("ci14", "mirror", "r1") == "smoke-ci14-mirror-r1"
    assert live_smoke.fixture_name("CI14", "mirror", "r1") == "smoke-ci14-mirror-r1"
    with pytest.raises(live_smoke.SmokeError) as excinfo:
        live_smoke.fixture_name("bad_phase!", "mirror", "r1")
    assert excinfo.value.reason == "name_segment_invalid"


def test_plan_mutates_nothing(
    smoke: tuple[live_smoke.LiveSmoke, FakeGitHub, FakeGitLab],
) -> None:
    """Plan output names both fixtures and never touches providers."""
    session, github_fake, gitlab_fake = smoke
    plan = session.plan("ci14", "mirror", "run1")
    assert plan["mutated"] is False
    assert plan["name"] == "smoke-ci14-mirror-run1"
    paths = [obj["path"] for obj in plan["objects"]]
    assert paths == [
        "spyroot/smoke-ci14-mirror-run1",
        "ci-skills-smoke/smoke-ci14-mirror-run1",
    ]
    assert github_fake.repositories == {}
    assert gitlab_fake.projects == {}


def test_apply_creates_ledger_and_fixtures(
    smoke: tuple[live_smoke.LiveSmoke, FakeGitHub, FakeGitLab], tmp_path: Path
) -> None:
    """Apply creates both fixtures, records IDs, and writes the ledger."""
    session, github_fake, gitlab_fake = smoke
    ledger = session.apply("ci14", "mirror", "run1", tmp_path)
    assert ledger.status == live_smoke.LEDGER_STATUS_OPEN
    github_record = ledger.objects["github"]
    gitlab_record = ledger.objects["gitlab"]
    assert github_record["path"] == "spyroot/smoke-ci14-mirror-run1"
    assert gitlab_record["path"] == "ci-skills-smoke/smoke-ci14-mirror-run1"
    assert github_fake.repositories[github_record["path"]]["id"] == github_record["id"]
    assert gitlab_fake.projects[gitlab_record["path"]]["id"] == gitlab_record["id"]
    assert (tmp_path / "run1.json").is_file()
    parsed = json.loads((tmp_path / "run1.json").read_text(encoding="utf-8"))
    assert parsed["kind"] == live_smoke.LEDGER_KIND
    assert parsed["status"] == live_smoke.LEDGER_STATUS_OPEN


def test_apply_stops_when_fixture_already_present(
    smoke: tuple[live_smoke.LiveSmoke, FakeGitHub, FakeGitLab], tmp_path: Path
) -> None:
    """The inventory stop rule aborts apply before creating anything."""
    session, github_fake, _ = smoke
    github_fake.repositories["spyroot/smoke-ci14-mirror-run1"] = {
        "id": 1,
        "name": "smoke-ci14-mirror-run1",
        "full_name": "spyroot/smoke-ci14-mirror-run1",
    }
    with pytest.raises(live_smoke.SmokeError) as excinfo:
        session.apply("ci14", "mirror", "run1", tmp_path)
    assert excinfo.value.reason == "fixture_already_present"
    assert not (tmp_path / "run1.json").exists()


def test_apply_refuses_second_open_run(
    smoke: tuple[live_smoke.LiveSmoke, FakeGitHub, FakeGitLab], tmp_path: Path
) -> None:
    """A ledger for the run ID blocks a second apply."""
    session, _, _ = smoke
    session.apply("ci14", "mirror", "run1", tmp_path)
    with pytest.raises(live_smoke.SmokeError) as excinfo:
        session.apply("ci14", "mirror", "run1", tmp_path)
    assert excinfo.value.reason == "run_already_open"


def test_cleanup_deletes_and_verifies_absence(
    smoke: tuple[live_smoke.LiveSmoke, FakeGitHub, FakeGitLab], tmp_path: Path
) -> None:
    """Cleanup removes both fixtures, proves absence, and closes the ledger."""
    session, github_fake, gitlab_fake = smoke
    session.apply("ci14", "mirror", "run1", tmp_path)
    closed = session.cleanup(tmp_path, "run1")
    assert closed.status == live_smoke.LEDGER_STATUS_CLOSED
    assert closed.closed_at is not None
    github_evidence = closed.cleanup["github"]
    assert github_evidence["http_status"] == int(HTTPStatus.NO_CONTENT)
    assert github_evidence["absent_by_path"] is True
    assert github_evidence["absent_by_id"] is True
    gitlab_evidence = closed.cleanup["gitlab"]
    assert gitlab_evidence["http_status"] == int(HTTPStatus.ACCEPTED)
    assert gitlab_evidence["absent_by_path"] is True
    assert github_fake.repositories == {}
    assert gitlab_fake.projects == {}
    with pytest.raises(live_smoke.SmokeError) as excinfo:
        session.cleanup(tmp_path, "run1")
    assert excinfo.value.reason == "run_already_closed"


def test_cleanup_refuses_changed_identity(
    smoke: tuple[live_smoke.LiveSmoke, FakeGitHub, FakeGitLab], tmp_path: Path
) -> None:
    """A squatting replacement at the same path is never deleted."""
    session, github_fake, _ = smoke
    session.apply("ci14", "mirror", "run1", tmp_path)
    squatter = 42424242
    github_fake.repositories["spyroot/smoke-ci14-mirror-run1"] = {
        "id": squatter,
        "name": "smoke-ci14-mirror-run1",
        "full_name": "spyroot/smoke-ci14-mirror-run1",
    }
    with pytest.raises(live_smoke.SmokeError) as excinfo:
        session.cleanup(tmp_path, "run1")
    assert excinfo.value.reason == "identity_changed"
    assert github_fake.repositories["spyroot/smoke-ci14-mirror-run1"]["id"] == squatter


def test_cleanup_reconcile_finishes_partial(
    smoke: tuple[live_smoke.LiveSmoke, FakeGitHub, FakeGitLab], tmp_path: Path
) -> None:
    """Reconcile records an already-absent fixture instead of aborting."""
    session, github_fake, _ = smoke
    session.apply("ci14", "mirror", "run1", tmp_path)
    del github_fake.repositories["spyroot/smoke-ci14-mirror-run1"]
    closed = session.cleanup(tmp_path, "run1", reconcile=True)
    assert closed.cleanup["github"]["already_absent"] is True
    assert closed.cleanup["github"]["mutated"] is False
    assert closed.cleanup["gitlab"]["mutated"] is True
    assert closed.status == live_smoke.LEDGER_STATUS_CLOSED


def test_create_fine_grained_reports_without_token(
    smoke: tuple[live_smoke.LiveSmoke, FakeGitHub, FakeGitLab],
) -> None:
    """The credential report carries provenance and never the token value."""
    session, _, _ = smoke
    github = session._github
    report = live_smoke.create_fine_grained(github)
    assert report["login"] == "spyroot"
    assert report["credential_source"] == "file:/token"
    assert report["token_kind"] == live_smoke.TOKEN_KIND_FINE_GRAINED
    assert report["reachable"] is True
    assert "ghp_" not in json.dumps(report)


def test_attach_and_detach_verbs(
    smoke: tuple[live_smoke.LiveSmoke, FakeGitHub, FakeGitLab],
) -> None:
    """Attach verifies reachability; detach documents the PAT-mode no-op."""
    session, github_fake, _ = smoke
    github = session._github
    missing = live_smoke.attach_fine_grained_to_repo(github, "spyroot/none")
    assert missing["present"] is False
    assert missing["mutated"] is False
    github_fake.repositories["spyroot/exists"] = {
        "id": 77,
        "name": "exists",
        "full_name": "spyroot/exists",
    }
    present = live_smoke.attach_fine_grained_to_repo(github, "spyroot/exists")
    assert present["present"] is True
    assert present["repository_id"] == 77
    detached = live_smoke.detach_fine_grained_to_repo(github, "spyroot/exists")
    assert detached["operation"] == "not_applicable_pat_mode"
    assert detached["mutated"] is False


def test_resolve_token_prefers_explicit_file(tmp_path: Path) -> None:
    """Explicit file beats environment; environment beats the filed default."""
    token_file = tmp_path / "github.com.token.env"
    token_file.write_text("ghp_test_value_1234567890abcdef1234\n", encoding="utf-8")
    environment = {"GH_TOKEN": "ghp_env_value_1234567890abcdef1234"}
    token, source = live_smoke.resolve_token(
        token_file,
        live_smoke.GITHUB_TOKEN_VARIABLES,
        live_smoke.DEFAULT_GITHUB_TOKEN_FILE,
        environment,
        "github",
    )
    assert token == "ghp_test_value_1234567890abcdef1234"
    assert source.startswith("file:")
    token, source = live_smoke.resolve_token(
        None,
        live_smoke.GITHUB_TOKEN_VARIABLES,
        live_smoke.DEFAULT_GITHUB_TOKEN_FILE,
        environment,
        "github",
    )
    assert token == "ghp_env_value_1234567890abcdef1234"
    assert source == "env:GH_TOKEN"
    with pytest.raises(live_smoke.SmokeError) as excinfo:
        live_smoke.resolve_token(
            None,
            live_smoke.GITHUB_TOKEN_VARIABLES,
            tmp_path / "absent.env",
            {},
            "github",
        )
    assert excinfo.value.reason == "github_token_unresolved"


def test_read_token_file_rejects_bad_shapes(tmp_path: Path) -> None:
    """Token files must hold exactly one raw token or one KEY=value pair."""
    multiline = tmp_path / "multi.env"
    multiline.write_text("a=1\nb=2\n", encoding="utf-8")
    with pytest.raises(live_smoke.SmokeError) as excinfo:
        live_smoke.read_token_file(multiline)
    assert excinfo.value.reason == "token_file_multiline"
    empty = tmp_path / "empty.env"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(live_smoke.SmokeError) as excinfo:
        live_smoke.read_token_file(empty)
    assert excinfo.value.reason == "token_file_empty"
    paired = tmp_path / "paired.env"
    paired.write_text("GITHUB_TOKEN=ghp_pair_1234567890abcdef1234\n", encoding="utf-8")
    assert live_smoke.read_token_file(paired) == "ghp_pair_1234567890abcdef1234"


def test_classify_github_token_prefixes() -> None:
    """Token kind classification reads only the scheme prefix."""
    assert (
        live_smoke.classify_github_token("github_pat_abc")
        == live_smoke.TOKEN_KIND_FINE_GRAINED
    )
    assert live_smoke.classify_github_token("gho_abc") == live_smoke.TOKEN_KIND_OAUTH
    assert (
        live_smoke.classify_github_token("ghp_abc") == live_smoke.TOKEN_KIND_CLASSIC_PAT
    )
    assert live_smoke.classify_github_token("zzz") == live_smoke.TOKEN_KIND_UNKNOWN


def test_github_permission_error_names_administration(
    smoke: tuple[live_smoke.LiveSmoke, FakeGitHub, FakeGitLab],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 403 on repo creation maps to the stable permission reason."""
    session, _, _ = smoke
    github = session._github

    def forbidden(request: object, timeout_seconds: float) -> FakeHTTPError:
        return FakeHTTPError(
            int(HTTPStatus.FORBIDDEN),
            json.dumps({"message": "Resource not accessible"}).encode("utf-8"),
            {},
        )

    monkeypatch.setattr(live_smoke, "_urlopen", forbidden)
    with pytest.raises(live_smoke.SmokeError) as excinfo:
        github.create_repository("smoke-ci14-mirror-x")
    assert excinfo.value.reason == "permission_missing"


def test_rename_preserves_identity(
    smoke: tuple[live_smoke.LiveSmoke, FakeGitHub, FakeGitLab],
) -> None:
    """Renames move the name, keep the ID, and stay deletable by ledger path."""
    session, github_fake, _ = smoke
    github = session._github
    created = github.create_repository("smoke-ci14-mirror-rn")
    repository_id = int(created["id"])
    renamed = github.rename_repository(
        "spyroot/smoke-ci14-mirror-rn", "smoke-ci14-mirror-rn2"
    )
    assert int(renamed["id"]) == repository_id
    assert "spyroot/smoke-ci14-mirror-rn2" in github_fake.repositories
    evidence = github.delete_repository("spyroot/smoke-ci14-mirror-rn2", repository_id)
    assert evidence["absent_by_id"] is True


def test_ledger_round_trip(tmp_path: Path) -> None:
    """A written ledger parses back with identical content."""
    ledger = live_smoke.SmokeLedger(
        run_id="run9", phase="ci15", case="fleet", name="smoke-ci15-fleet-run9"
    )
    ledger.objects["github"] = {
        "system": "github",
        "kind": "repository",
        "id": 123,
        "name": ledger.name,
        "path": f"spyroot/{ledger.name}",
        "created": True,
    }
    path = ledger.write(tmp_path)
    parsed = live_smoke.SmokeLedger.read(tmp_path, "run9")
    assert parsed.to_json() == ledger.to_json()
    assert path.name == "run9.json"
    with pytest.raises(live_smoke.SmokeError) as excinfo:
        live_smoke.SmokeLedger.read(tmp_path, "missing")
    assert excinfo.value.reason == "ledger_missing"
