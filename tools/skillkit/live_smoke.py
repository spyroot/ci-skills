"""Deterministic lifecycle for live smoke fixtures across GitHub and GitLab.

The operator's live smoke allocations live in ``docs/2026-10-31/live-smoke-inventory.md``.
This module is the fixture manager those scenarios share: it creates, renames and
removes only the run-owned disposable repositories and projects, and it records a
JSON ledger so ``cleanup`` removes exactly what ``apply`` created. Nothing here
adopts a pre-existing object: every delete re-reads the object by path and refuses
to continue when the identity differs from the recorded one.

The five lifecycle verbs the operator specified are module functions:

- ``create_fine_grained`` — PAT-manage mode: bind the filed fine-grained PAT,
  report its identity, kind and (for classic tokens) scopes, and prove reachability.
  GitHub exposes no API to create or edit PATs, so creation stays a one-time web
  step; this verb verifies what was filed. The token is never reportable.
- ``attach_fine_grained_to_repo`` — PAT-manage mode: verify the filed PAT reaches
  one repository. A fine-grained PAT with all-repository access needs no attach.
- ``detach_fine_grained_to_repo`` — PAT-manage mode: documented no-op; a PAT has
  no per-repository attachment to remove.
- ``create_test_gitlab_repo`` — create one disposable project under the smoke
  group on the selected GitLab, with read-back.
- ``create_test_github_repo`` — create one disposable repository for the selected
  owner, with read-back.

Naming is deterministic: ``smoke-<phase>-<case>-<run_id>`` on both systems. ``plan``
needs no credentials and mutates nothing. Deletion is identity-guarded and verified
absent by path and by native ID, then repeated once; GitLab project removal is
asynchronous (HTTP 202) and is polled to absence.

This module imports nothing from ``lib``: it stays importable before any installed
copy puts its ``lib`` package first on ``sys.path``, which is what lets the
wrapper and the tests bind either side.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Final, Optional, TypeAlias

from skillkit.mirror_qualification import QualificationError
from skillkit.mirror_qualification import _write as _write_ledger

SCHEMA_VERSION: Final = 1
LEDGER_KIND: Final = "ci_skills_live_smoke_ledger"
LEDGER_STATUS_OPEN: Final = "open"
LEDGER_STATUS_CLOSED: Final = "closed"

GITHUB_SYSTEM: Final = "github"
GITLAB_SYSTEM: Final = "gitlab"
REPOSITORY_KIND: Final = "repository"
PROJECT_KIND: Final = "project"
GROUP_KIND: Final = "group"

# Credential read order for a fixture run: an explicit token file wins over the
# ambient environment, and the environment wins over the filed default. That is
# the deterministic order a deliberate smoke run wants; it differs from the skill
# binding order, which must stay target-driven.
GITHUB_TOKEN_VARIABLES: Final = ("GH_TOKEN", "GITHUB_TOKEN")
GITLAB_TOKEN_VARIABLES: Final = ("GITLAB_TOKEN", "GITLAB_PRIVATE_TOKEN")
DEFAULT_GITHUB_TOKEN_FILE: Final = Path(
    "~/.ci-skills/github.com.token.env"
).expanduser()
DEFAULT_GITLAB_TOKEN_FILE: Final = Path(
    "~/.ci-skills/gitlab.dcloud.run.token.env"
).expanduser()

GITHUB_API_HOST: Final = "api.github.com"
DEFAULT_GITHUB_OWNER: Final = "spyroot"
DEFAULT_GITLAB_GROUP: Final = "ci-skills-smoke"
NAME_PATTERN: Final = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
NAME_TEMPLATE: Final = "smoke-{phase}-{case}-{run_id}"
PROJECT_DELETE_POLL_SECONDS: Final = 30.0
PROJECT_DELETE_POLL_INTERVAL: Final = 1.0

JSONValue: TypeAlias = (
    None | bool | int | float | str | Sequence["JSONValue"] | Mapping[str, "JSONValue"]
)
JSONObject: TypeAlias = MutableMapping[str, JSONValue]
CreationObserver: TypeAlias = Callable[[JSONObject], None]

TOKEN_KIND_FINE_PREFIX: Final = "github_pat_"
TOKEN_KIND_OAUTH_PREFIX: Final = "gho_"
TOKEN_KIND_CLASSIC_PREFIX: Final = "ghp_"
TOKEN_KIND_FINE_GRAINED: Final = "fine_grained"
TOKEN_KIND_OAUTH: Final = "oauth"
TOKEN_KIND_CLASSIC_PAT: Final = "classic_pat"
TOKEN_KIND_UNKNOWN: Final = "unknown"


class SmokeError(Exception):
    """One failed smoke lifecycle step with a reportable reason."""

    def __init__(self, reason: str, detail: str = "") -> None:
        """Carry the machine reason and any public detail.

        :param reason: Stable snake-case identifier for receipts and tests.
        :param detail: Human-readable context safe to print.
        """
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def utc_now() -> str:
    """Return the current UTC time in ISO 8601 for ledger timestamps.

    :returns: Timezone-aware timestamp string.
    """
    return datetime.now(UTC).isoformat()


def read_token_file(path: Path) -> str:
    """Read one raw token or ``KEY=value`` pair without reporting the value.

    :param path: Token file the operator filed.
    :returns: Stripped token text.
    :raises SmokeError: If the file is missing, empty or holds more than one pair.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SmokeError("token_file_unreadable", str(path)) from exc
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise SmokeError("token_file_empty", str(path))
    if len(lines) != 1:
        raise SmokeError("token_file_multiline", str(path))
    line = lines[0]
    if "=" in line:
        _, _, value = line.partition("=")
        token = value.strip().strip('"').strip("'")
    else:
        token = line
    if not token:
        raise SmokeError("token_file_empty", str(path))
    if any(char.isspace() for char in token):
        raise SmokeError("token_file_whitespace", str(path))
    return token


def resolve_token(
    explicit_file: Optional[Path],
    variable_names: Sequence[str],
    default_file: Path,
    environment: Mapping[str, str],
    purpose: str,
) -> tuple[str, str]:
    """Resolve one credential in fixture order: file, environment, default file.

    :param explicit_file: Token file named by the caller; wins when given.
    :param variable_names: Environment variables accepted for this system.
    :param default_file: Filed default consulted last.
    :param environment: Environment mapping, injected for tests.
    :param purpose: System name used in the unresolved error.
    :returns: Token and a reportable source reference; the token is never printed.
    :raises SmokeError: If no source yields a usable token.
    """
    if explicit_file is not None:
        return read_token_file(explicit_file), f"file:{explicit_file.resolve()}"
    for name in variable_names:
        value = environment.get(name)
        if value is not None:
            if not value:
                raise SmokeError(f"{name}_empty")
            return value, f"env:{name}"
    if default_file.is_file():
        return read_token_file(default_file), f"file:{default_file.resolve()}"
    raise SmokeError(f"{purpose}_token_unresolved")


def sanitize_segment(value: str, label: str) -> str:
    """Normalize one name segment and require the smoke name grammar.

    :param value: Raw phase, case or run identifier.
    :param label: Segment name used in the error.
    :returns: Lowercased segment with underscores folded to dashes.
    :raises SmokeError: If the segment is empty or outside the grammar.
    """
    segment = value.strip().lower().replace("_", "-")
    if not NAME_PATTERN.fullmatch(segment):
        raise SmokeError(
            "name_segment_invalid",
            f"{label}={value!r} must match {NAME_PATTERN.pattern}",
        )
    return segment


def fixture_name(phase: str, case: str, run_id: str) -> str:
    """Build the deterministic fixture name shared by GitHub and GitLab.

    :param phase: Phase bucket, e.g. ``ci14``.
    :param case: Smoke case, e.g. ``mirror``.
    :param run_id: Run identifier the caller chose for this fixture set.
    :returns: The smoke- name both systems use.
    """
    return NAME_TEMPLATE.format(
        phase=sanitize_segment(phase, "phase"),
        case=sanitize_segment(case, "case"),
        run_id=sanitize_segment(run_id, "run_id"),
    )


def _urlopen(request: urllib.request.Request, timeout_seconds: float) -> object:
    """Open one request; the single seam tests replace with fakes.

    :param request: Prepared urllib request.
    :param timeout_seconds: Per-request timeout.
    :returns: The provider response object.
    """
    return urllib.request.urlopen(request, timeout=timeout_seconds)


@dataclass(frozen=True)
class ApiResponse:
    """One REST response with the headers the smoke evidence needs."""

    status: int
    body: JSONValue
    headers: Mapping[str, str]


class RestClient:
    """Minimal JSON REST transport with the error mapping smoke steps share."""

    def __init__(
        self,
        base_url: str,
        headers: Mapping[str, str],
        timeout_seconds: float = 30.0,
    ) -> None:
        """Pin the base URL, default headers and timeout.

        :param base_url: Scheme and authority, without a trailing slash.
        :param headers: Headers sent on every request, including authorization.
        :param timeout_seconds: Per-request timeout.
        """
        self._base_url = base_url.rstrip("/")
        self._headers = dict(headers)
        self._timeout = timeout_seconds

    @property
    def base_url(self) -> str:
        """Return the bound URL without its trailing slash."""
        return self._base_url

    def request(
        self,
        method: str,
        path: str,
        body: Optional[JSONObject] = None,
        expected: Sequence[int] = (HTTPStatus.OK,),
    ) -> ApiResponse:
        """Send one JSON request and map failures to stable smoke reasons.

        :param method: HTTP method name.
        :param path: Absolute path beginning with ``/``.
        :param body: Optional JSON object sent as the request body.
        :param expected: Status codes treated as success.
        :returns: Response with lowercased header names.
        :raises SmokeError: On transport failure, JSON decode failure or an
            unexpected status, with the provider message when present.
        """
        url = f"{self._base_url}{path}"
        data: Optional[bytes] = None
        headers = dict(self._headers)
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with _urlopen(request, self._timeout) as response:
                payload = response.read()
                status = response.status
                response_headers = {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            status = exc.code
            response_headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
        except urllib.error.URLError as exc:
            raise SmokeError(
                "transport_failed", f"{method} {path}: {exc.reason}"
            ) from exc
        parsed: JSONValue = None
        if payload:
            try:
                parsed = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise SmokeError("response_not_json", f"{method} {path}") from exc
        if status not in expected:
            message = ""
            if isinstance(parsed, Mapping):
                raw = parsed.get("message")
                if isinstance(raw, str):
                    message = raw
            raise SmokeError(
                f"unexpected_status_{status}",
                f"{method} {path}: {message or 'no provider message'}",
            )
        return ApiResponse(status, parsed, response_headers)


def encode_path(value: str) -> str:
    """URL-encode one path segment or slash-joined path for provider routes.

    :param value: Raw path, e.g. ``hott/test`` or a project name.
    :returns: Encoded path with slashes preserved.
    """
    return urllib.parse.quote(value, safe="/")


def _record_identifier(record: JSONValue) -> int:
    """Extract the numeric native ID a provider record must carry.

    :param record: Provider object decoded from JSON.
    :returns: Positive integer ID.
    :raises SmokeError: If the ID is missing or not a positive integer.
    """
    if not isinstance(record, Mapping):
        raise SmokeError("record_not_object")
    raw = record.get("id")
    if type(raw) is not int or raw <= 0:
        raise SmokeError("record_id_invalid", str(raw))
    return raw


class GitHubSmoke:
    """Identity-guarded GitHub repository lifecycle for smoke fixtures."""

    def __init__(
        self,
        client: RestClient,
        owner: str,
        credential_source: str = "unknown",
        token_kind: str = TOKEN_KIND_UNKNOWN,
    ) -> None:
        """Pin the authenticated client, owner and credential provenance.

        :param client: REST client already carrying the GitHub credential.
        :param owner: Repository owner the fixtures are created under.
        :param credential_source: Reportable reference from token resolution.
        :param token_kind: Classified token kind; the token value stays private.
        """
        self._client = client
        self._owner = owner
        self._credential_source = credential_source
        self._token_kind = token_kind
        self._identity: Optional[JSONObject] = None
        self._scopes: tuple[str, ...] = ()

    def identity(self) -> JSONObject:
        """Read and cache the authenticated GitHub user.

        :returns: The ``/user`` record; never holds credential material.
        """
        if self._identity is None:
            response = self._client.request("GET", "/user")
            if not isinstance(response.body, MutableMapping):
                raise SmokeError("identity_not_object")
            self._identity = dict(response.body)
            raw = response.headers.get("x-oauth-scopes", "")
            self._scopes = tuple(
                scope.strip() for scope in raw.split(",") if scope.strip()
            )
        return self._identity

    def scopes(self) -> tuple[str, ...]:
        """Report the classic token scopes, empty for fine-grained tokens.

        :returns: Sorted unique scope names from the ``/user`` response header.
        """
        self.identity()
        return tuple(sorted(set(self._scopes)))

    def login(self) -> str:
        """Report the authenticated account login.

        :returns: Login of the token owner; the token itself stays private.
        """
        record = self.identity()
        login = record.get("login")
        if not isinstance(login, str) or not login:
            raise SmokeError("identity_login_missing")
        return login

    def repository(self, path: str) -> Optional[JSONObject]:
        """Read one repository by ``owner/name``.

        :param path: Full repository path.
        :returns: Repository record, or ``None`` when GitHub answers 404.
        """
        encoded = encode_path(path)
        try:
            response = self._client.request("GET", f"/repos/{encoded}")
        except SmokeError as exc:
            if exc.reason == f"unexpected_status_{HTTPStatus.NOT_FOUND}":
                return None
            raise
        if not isinstance(response.body, MutableMapping):
            raise SmokeError("repository_not_object", path)
        return dict(response.body)

    def repository_present_by_id(self, repository_id: int) -> bool:
        """Read repository existence by native ID, independent of the name.

        :param repository_id: Numeric GitHub repository ID.
        :returns: Whether the ID still resolves.
        """
        try:
            self._client.request("GET", f"/repositories/{repository_id}")
        except SmokeError as exc:
            if exc.reason == f"unexpected_status_{HTTPStatus.NOT_FOUND}":
                return False
            raise
        return True

    def create_repository(
        self,
        name: str,
        private: bool = True,
        *,
        on_created: Optional[CreationObserver] = None,
    ) -> JSONObject:
        """Create one disposable repository and read it back.

        :param name: Repository name for the authenticated owner.
        :param private: Whether the fixture stays private.
        :param on_created: Record the create response before the read-back.
        :returns: The created repository record with its native ID.
        :raises SmokeError: With ``permission_missing`` when the token cannot
            create repositories (fine-grained PAT without Administration write).
        """
        try:
            response = self._client.request(
                "POST",
                "/user/repos",
                {"name": name, "private": private, "auto_init": False},
                expected=(HTTPStatus.CREATED,),
            )
        except SmokeError as exc:
            if exc.reason == f"unexpected_status_{HTTPStatus.FORBIDDEN}":
                raise SmokeError(
                    "permission_missing",
                    "token cannot create repositories; grant Administration: write",
                ) from exc
            if exc.reason == f"unexpected_status_{HTTPStatus.UNPROCESSABLE_ENTITY}":
                raise SmokeError("repository_already_present", name) from exc
            raise
        if not isinstance(response.body, MutableMapping):
            raise SmokeError("repository_not_object", name)
        created = dict(response.body)
        if on_created is not None:
            on_created(created)
        readback = self.repository(f"{self._owner}/{name}")
        if readback is None or readback.get("id") != created.get("id"):
            raise SmokeError("repository_readback_mismatch", name)
        return created

    def rename_repository(self, path: str, new_name: str) -> JSONObject:
        """Rename one repository, preserving its native ID.

        :param path: Current full repository path.
        :param new_name: New repository name segment.
        :returns: The renamed repository record.
        """
        encoded = encode_path(path)
        response = self._client.request(
            "PATCH", f"/repos/{encoded}", {"name": new_name}
        )
        if not isinstance(response.body, MutableMapping):
            raise SmokeError("repository_not_object", path)
        return dict(response.body)

    def delete_repository(self, path: str, expected_id: int) -> JSONObject:
        """Delete one repository after re-reading and matching its exact ID.

        The repository is re-read by path; a missing object or any ID mismatch
        aborts with ``identity_changed`` and deletes nothing. After the delete
        the absence is verified by path and by native ID, then repeated once.

        :param path: Full repository path recorded in the ledger.
        :param expected_id: Native ID the run created.
        :returns: Cleanup evidence with statuses for the ledger.
        """
        current = self.repository(path)
        if current is None:
            raise SmokeError("repository_absent_before_delete", path)
        if current.get("id") != expected_id:
            raise SmokeError(
                "identity_changed", f"{path} id {current.get('id')} != {expected_id}"
            )
        self._client.request(
            "DELETE", f"/repos/{encode_path(path)}", expected=(HTTPStatus.NO_CONTENT,)
        )
        after_path = self.repository(path) is None
        after_id = not self.repository_present_by_id(expected_id)
        repeat_path = self.repository(path) is None
        repeat_id = not self.repository_present_by_id(expected_id)
        if not (after_path and after_id and repeat_path and repeat_id):
            raise SmokeError(
                "repository_absence_unproven",
                f"path={after_path and repeat_path} id={after_id and repeat_id}",
            )
        evidence: JSONObject = {
            "path": path,
            "repository_id": expected_id,
            "http_status": int(HTTPStatus.NO_CONTENT),
            "absent_by_path": after_path and repeat_path,
            "absent_by_id": after_id and repeat_id,
            "mutated": True,
        }
        return evidence


class GitLabSmoke:
    """Identity-guarded GitLab group and project lifecycle for smoke fixtures."""

    def __init__(self, client: RestClient) -> None:
        """Pin the authenticated client for one GitLab base URL.

        :param client: REST client already carrying the GitLab credential.
        """
        self._client = client
        self._server = self._validated_server(client.base_url)

    @staticmethod
    def _validated_server(url: str) -> str:
        """Validate the URL before storing it as a ledger authority.

        :param url: Configured GitLab base URL, including any relative root.
        :returns: The exact credential-free base URL used for requests.
        :raises SmokeError: The URL is invalid or contains private components.
        """
        try:
            parsed = urllib.parse.urlsplit(url)
            valid = (
                parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not parsed.fragment
                and not parsed.query
            )
        except ValueError:
            valid = False
        if not valid:
            raise SmokeError("gitlab_server_invalid")
        return url

    @property
    def server(self) -> str:
        """Return the GitLab server this client is bound to."""
        return self._server

    def identity(self) -> JSONObject:
        """Read the authenticated GitLab user.

        :returns: The ``/user`` record; never holds credential material.
        """
        response = self._client.request("GET", "/api/v4/user")
        if not isinstance(response.body, MutableMapping):
            raise SmokeError("identity_not_object")
        return dict(response.body)

    def group(self, full_path: str) -> Optional[JSONObject]:
        """Read one group by its full path.

        :param full_path: Group path, e.g. ``ci-skills-smoke``.
        :returns: Group record, or ``None`` when absent.
        """
        try:
            response = self._client.request(
                "GET", f"/api/v4/groups/{encode_path(full_path)}"
            )
        except SmokeError as exc:
            if exc.reason == f"unexpected_status_{HTTPStatus.NOT_FOUND}":
                return None
            raise
        if not isinstance(response.body, MutableMapping):
            raise SmokeError("group_not_object", full_path)
        return dict(response.body)

    def ensure_group(self, name: str, full_path: str) -> tuple[JSONObject, bool]:
        """Return the smoke group, creating it only when absent.

        :param name: Group display name.
        :param full_path: Group path to create or read.
        :returns: Group record and whether this call created it.
        """
        existing = self.group(full_path)
        if existing is not None:
            return existing, False
        try:
            response = self._client.request(
                "POST",
                "/api/v4/groups",
                {"name": name, "path": full_path, "visibility": "private"},
                expected=(HTTPStatus.CREATED,),
            )
        except SmokeError as exc:
            if exc.reason == f"unexpected_status_{HTTPStatus.FORBIDDEN}":
                raise SmokeError(
                    "permission_missing",
                    f"token cannot create group {full_path}; grant group creation "
                    "or pass --group under an existing namespace",
                ) from exc
            raise
        if not isinstance(response.body, MutableMapping):
            raise SmokeError("group_not_object", full_path)
        return dict(response.body), True

    def project(self, full_path: str) -> Optional[JSONObject]:
        """Read one project by its full path.

        :param full_path: Project path with namespace.
        :returns: Project record, or ``None`` when absent.
        """
        try:
            response = self._client.request(
                "GET", f"/api/v4/projects/{encode_path(full_path)}"
            )
        except SmokeError as exc:
            if exc.reason == f"unexpected_status_{HTTPStatus.NOT_FOUND}":
                return None
            raise
        if not isinstance(response.body, MutableMapping):
            raise SmokeError("project_not_object", full_path)
        return dict(response.body)

    def create_project(
        self,
        group_id: int,
        name: str,
        *,
        on_created: Optional[CreationObserver] = None,
    ) -> JSONObject:
        """Create one disposable project inside the smoke group and read it back.

        :param group_id: Native group ID the project is created under.
        :param name: Project name; GitLab derives the path from it.
        :param on_created: Record the create response before the read-back.
        :returns: The created project record with its native ID.
        """
        try:
            response = self._client.request(
                "POST",
                "/api/v4/projects",
                {
                    "name": name,
                    "namespace_id": group_id,
                    "visibility": "private",
                    "initialize": False,
                },
                expected=(HTTPStatus.CREATED,),
            )
        except SmokeError as exc:
            if exc.reason == f"unexpected_status_{HTTPStatus.FORBIDDEN}":
                raise SmokeError(
                    "permission_missing",
                    "token cannot create projects in the smoke group",
                ) from exc
            raise
        if not isinstance(response.body, MutableMapping):
            raise SmokeError("project_not_object", name)
        created = dict(response.body)
        if on_created is not None:
            on_created(created)
        path_with_namespace = created.get("path_with_namespace")
        if not isinstance(path_with_namespace, str):
            raise SmokeError("project_path_missing", name)
        readback = self.project(path_with_namespace)
        if readback is None or readback.get("id") != created.get("id"):
            raise SmokeError("project_readback_mismatch", name)
        return created

    def rename_project(self, project_id: int, new_name: str) -> JSONObject:
        """Rename one project, preserving its native ID.

        :param project_id: Native project ID to update.
        :param new_name: New project display name.
        :returns: The updated project record.
        """
        response = self._client.request(
            "PUT", f"/api/v4/projects/{project_id}", {"name": new_name}
        )
        if not isinstance(response.body, MutableMapping):
            raise SmokeError("project_not_object", str(project_id))
        return dict(response.body)

    def delete_project(self, full_path: str, expected_id: int) -> JSONObject:
        """Delete one project after re-reading and matching its exact ID.

        The project is re-read by path; a missing object or any ID mismatch aborts
        with ``identity_changed`` and deletes nothing. GitLab answers project
        deletion with HTTP 202 and removes asynchronously, so absence is polled
        to 404, then the absence is verified once more by ID.

        :param full_path: Full project path recorded in the ledger.
        :param expected_id: Native ID the run created.
        :returns: Cleanup evidence with statuses for the ledger.
        """
        current = self.project(full_path)
        if current is None:
            raise SmokeError("project_absent_before_delete", full_path)
        if current.get("id") != expected_id:
            raise SmokeError(
                "identity_changed",
                f"{full_path} id {current.get('id')} != {expected_id}",
            )
        self._client.request(
            "DELETE",
            f"/api/v4/projects/{expected_id}",
            expected=(HTTPStatus.ACCEPTED,),
        )
        deadline = time.monotonic() + PROJECT_DELETE_POLL_SECONDS
        absent = False
        while time.monotonic() < deadline:
            if self.project(full_path) is None:
                absent = True
                break
            time.sleep(PROJECT_DELETE_POLL_INTERVAL)
        repeat = self.project(full_path) is None
        if not (absent and repeat):
            raise SmokeError("project_absence_unproven", full_path)
        evidence: JSONObject = {
            "path": full_path,
            "project_id": expected_id,
            "http_status": int(HTTPStatus.ACCEPTED),
            "absent_by_path": absent and repeat,
            "mutated": True,
        }
        return evidence


def classify_github_token(token: str) -> str:
    """Classify one GitHub token from its prefix, never reporting the value.

    :param token: Resolved credential; only its scheme prefix is inspected.
    :returns: One of the ``TOKEN_KIND_*`` labels.
    """
    if token.startswith(TOKEN_KIND_FINE_PREFIX):
        return TOKEN_KIND_FINE_GRAINED
    if token.startswith(TOKEN_KIND_OAUTH_PREFIX):
        return TOKEN_KIND_OAUTH
    if token.startswith(TOKEN_KIND_CLASSIC_PREFIX):
        return TOKEN_KIND_CLASSIC_PAT
    return TOKEN_KIND_UNKNOWN


def create_fine_grained(github: GitHubSmoke) -> JSONObject:
    """Bind and verify the filed fine-grained PAT without exposing it.

    PAT-manage mode: GitHub offers no API to create or edit PATs, so the operator
    creates the token on the web and files it; this verb proves what was filed is
    usable. Classic tokens additionally report their OAuth scopes; fine-grained
    tokens report their kind only, because GitHub publishes no scope header for
    them and their permissions are not API-readable.

    :param github: Client carrying the filed credential.
    :returns: Credential report: login, source, token kind and classic scopes.
    """
    report: JSONObject = {
        "login": github.login(),
        "credential_source": github._credential_source,
        "token_kind": github._token_kind,
        "classic_scopes": list(github.scopes()),
        "reachable": True,
    }
    return report


def attach_fine_grained_to_repo(github: GitHubSmoke, path: str) -> JSONObject:
    """Verify the filed PAT reaches one repository (PAT-manage attach).

    A fine-grained PAT with all-repository access needs no per-repository
    attachment, so this verb verifies reachability instead of mutating access.

    :param github: Client carrying the filed credential.
    :param path: Full repository path to check.
    :returns: Attachment report with presence and the repository ID when found.
    """
    repository = github.repository(path)
    report: JSONObject = {
        "path": path,
        "present": repository is not None,
        "repository_id": repository.get("id") if repository else None,
        "operation": "verify",
        "mutated": False,
    }
    return report


def detach_fine_grained_to_repo(github: GitHubSmoke, path: str) -> JSONObject:
    """Document the PAT-manage no-op detach.

    Personal access tokens have no per-repository attachment to remove; only the
    GitHub App mode this tool may grow into would implement a real detach.

    :param github: Client carrying the filed credential.
    :param path: Full repository path the caller asked to detach from.
    :returns: No-op report naming the reason.
    """
    _ = github
    return {
        "path": path,
        "operation": "not_applicable_pat_mode",
        "mutated": False,
    }


def create_test_github_repo(
    github: GitHubSmoke,
    owner: str,
    name: str,
    *,
    on_created: Optional[CreationObserver] = None,
) -> JSONObject:
    """Create the disposable GitHub repository for one smoke run.

    :param github: Client carrying the GitHub credential.
    :param owner: Repository owner, recorded for the ledger.
    :param name: Deterministic fixture name.
    :param on_created: Persist the ownership record before provider read-back.
    :returns: Created object record for the ledger.
    """

    def ownership(created: JSONObject) -> JSONObject:
        return {
            "system": GITHUB_SYSTEM,
            "kind": REPOSITORY_KIND,
            "id": _record_identifier(created),
            "name": name,
            "path": f"{owner}/{name}",
            "created": True,
        }

    def observe(created: JSONObject) -> None:
        if on_created is not None:
            on_created(ownership(created))

    return ownership(github.create_repository(name, on_created=observe))


def create_test_gitlab_repo(
    gitlab: GitLabSmoke,
    group_full_path: str,
    name: str,
    *,
    on_created: Optional[CreationObserver] = None,
    on_group_created: Optional[CreationObserver] = None,
) -> tuple[JSONObject, JSONObject]:
    """Create the disposable GitLab project for one smoke run.

    :param gitlab: Client carrying the GitLab credential.
    :param group_full_path: Smoke group the project lives under.
    :param name: Deterministic fixture name.
    :param on_created: Persist the project ownership record before read-back.
    :param on_group_created: Persist a newly created infrastructure group's ID.
    :returns: Created object record and the group record (created or existing).
    """
    group, group_created = gitlab.ensure_group(group_full_path, group_full_path)
    group_id = _record_identifier(group)
    if group_created and on_group_created is not None:
        on_group_created({"id": group_id, "path": group_full_path})

    def ownership(created: JSONObject) -> JSONObject:
        path = created.get("path_with_namespace")
        return {
            "system": GITLAB_SYSTEM,
            "kind": PROJECT_KIND,
            "id": _record_identifier(created),
            "name": name,
            "path": path if isinstance(path, str) else f"{group_full_path}/{name}",
            "created": True,
            "group_id": group_id,
            "group_path": group_full_path,
            "group_created": group_created,
        }

    def observe(created: JSONObject) -> None:
        if on_created is not None:
            on_created(ownership(created))

    return ownership(gitlab.create_project(group_id, name, on_created=observe)), group


@dataclass
class SmokeLedger:
    """JSON ledger binding one apply run to the objects it created."""

    run_id: str
    phase: str
    case: str
    name: str
    objects: MutableMapping[str, JSONObject] = field(default_factory=dict)
    status: str = LEDGER_STATUS_OPEN
    created_at: str = field(default_factory=utc_now)
    closed_at: Optional[str] = None
    cleanup: MutableMapping[str, JSONObject] = field(default_factory=dict)
    gitlab_server: Optional[str] = None
    gitlab_group: Optional[JSONObject] = None

    def to_json(self) -> JSONObject:
        """Serialize the ledger in its on-disk shape.

        :returns: JSON object matching the smoke ledger shape.
        """
        document: JSONObject = {
            "schema_version": SCHEMA_VERSION,
            "kind": LEDGER_KIND,
            "run_id": self.run_id,
            "phase": self.phase,
            "case": self.case,
            "name": self.name,
            "status": self.status,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
            "objects": dict(self.objects),
            "cleanup": dict(self.cleanup),
            "gitlab_server": self.gitlab_server,
            "gitlab_group": self.gitlab_group,
        }
        return document

    @classmethod
    def from_json(cls, document: Mapping[str, JSONValue]) -> SmokeLedger:
        """Parse one ledger document written by :meth:`to_json`.

        :param document: Decoded ledger JSON.
        :returns: The ledger value object.
        :raises SmokeError: If a required field is missing or mistyped.
        """
        try:
            run_id = str(document["run_id"])
            phase = str(document["phase"])
            case = str(document["case"])
            name = str(document["name"])
            status = str(document["status"])
            created_at = str(document["created_at"])
            raw_objects = document["objects"]
        except (KeyError, TypeError) as exc:
            raise SmokeError("ledger_invalid", str(exc)) from exc
        if not isinstance(raw_objects, Mapping):
            raise SmokeError("ledger_invalid", "objects")
        objects: MutableMapping[str, JSONObject] = {}
        for key, value in raw_objects.items():
            if not isinstance(value, MutableMapping):
                raise SmokeError("ledger_invalid", f"objects.{key}")
            objects[str(key)] = dict(value)
        server = document.get("gitlab_server")
        group = document.get("gitlab_group")
        if server is not None and not isinstance(server, str):
            raise SmokeError("ledger_invalid", "gitlab_server")
        if group is not None and not isinstance(group, Mapping):
            raise SmokeError("ledger_invalid", "gitlab_group")
        ledger = cls(
            run_id=run_id,
            phase=phase,
            case=case,
            name=name,
            objects=objects,
            status=status,
            created_at=created_at,
            gitlab_server=server,
            gitlab_group=dict(group) if group is not None else None,
        )
        closed = document.get("closed_at")
        ledger.closed_at = str(closed) if closed is not None else None
        raw_cleanup = document.get("cleanup")
        if isinstance(raw_cleanup, Mapping):
            for key, value in raw_cleanup.items():
                if isinstance(value, MutableMapping):
                    ledger.cleanup[str(key)] = dict(value)
        return ledger

    @staticmethod
    def path_for(state_dir: Path, run_id: str) -> Path:
        """Return the ledger file for one run identifier.

        :param state_dir: Directory that holds smoke ledgers.
        :param run_id: Run identifier the ledger belongs to.
        :returns: Deterministic ledger path.
        """
        return state_dir / f"{sanitize_segment(run_id, 'run_id')}.json"

    def write(self, state_dir: Path) -> Path:
        """Replace the ledger after syncing its new contents to disk.

        :param state_dir: Directory that holds smoke ledgers.
        :returns: The ledger path written.
        :raises SmokeError: The ledger cannot be saved.
        """
        path = self.path_for(state_dir, self.run_id)
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            _write_ledger(path, self.to_json())
        except (OSError, QualificationError) as exc:
            raise SmokeError("ledger_write_failed", str(path)) from exc
        return path

    @classmethod
    def read(cls, state_dir: Path, run_id: str) -> SmokeLedger:
        """Read one ledger, requiring it to exist.

        :param state_dir: Directory that holds smoke ledgers.
        :param run_id: Run identifier to read.
        :returns: The parsed ledger.
        :raises SmokeError: If the ledger is missing or malformed.
        """
        path = cls.path_for(state_dir, run_id)
        if not path.is_file():
            raise SmokeError("ledger_missing", str(path))
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SmokeError("ledger_unreadable", str(path)) from exc
        if not isinstance(document, Mapping):
            raise SmokeError("ledger_invalid", "root")
        return cls.from_json(document)


class LiveSmoke:
    """Plan, create and remove one deterministic fixture set for a smoke case.

    One instance binds both provider lifecycles for one run. ``plan`` is pure and
    needs no reachable provider. ``apply`` enforces the inventory stop rule: if
    either fixture name is already present on its provider, nothing is created
    and the run aborts. ``cleanup`` removes exactly the ledger objects after
    re-checking their native IDs, and repeats the absence read-back.
    """

    def __init__(
        self,
        github: GitHubSmoke,
        gitlab: GitLabSmoke,
        owner: str,
        group_path: str,
    ) -> None:
        """Pin both provider lifecycles and the allocation identities.

        :param github: GitHub lifecycle bound to the smoke owner.
        :param gitlab: GitLab lifecycle bound to the smoke group.
        :param owner: GitHub owner the repositories are created under.
        :param group_path: GitLab group path the projects are created under.
        """
        self._github = github
        self._gitlab = gitlab
        self._owner = owner
        self._group_path = group_path

    def plan(self, phase: str, case: str, run_id: str) -> JSONObject:
        """Describe the fixture set without touching either provider.

        :param phase: Phase bucket, e.g. ``ci14``.
        :param case: Smoke case, e.g. ``mirror``.
        :param run_id: Run identifier; empty prints the naming template.
        :returns: Plan document with the deterministic names and object kinds.
        """
        name = (
            fixture_name(phase, case, run_id)
            if run_id
            else NAME_TEMPLATE.format(
                phase=sanitize_segment(phase, "phase"),
                case=sanitize_segment(case, "case"),
                run_id="<run_id>",
            )
        )
        plan: JSONObject = {
            "schema_version": SCHEMA_VERSION,
            "kind": "ci_skills_live_smoke_plan",
            "name": name,
            "phase": sanitize_segment(phase, "phase"),
            "case": sanitize_segment(case, "case"),
            "run_id": run_id or "<run_id>",
            "mutated": False,
            "objects": [
                {
                    "system": GITHUB_SYSTEM,
                    "kind": REPOSITORY_KIND,
                    "path": f"{self._owner}/{name}",
                },
                {
                    "system": GITLAB_SYSTEM,
                    "kind": PROJECT_KIND,
                    "path": f"{self._group_path}/{name}",
                },
            ],
            "group": {
                "system": GITLAB_SYSTEM,
                "kind": GROUP_KIND,
                "path": self._group_path,
                "created_by_apply": "when absent",
            },
        }
        return plan

    def apply(self, phase: str, case: str, run_id: str, state_dir: Path) -> SmokeLedger:
        """Create the fixture set, enforcing stop-if-present on both providers.

        :param phase: Phase bucket for the fixture name.
        :param case: Smoke case for the fixture name.
        :param run_id: Run identifier for the fixture name and the ledger.
        :param state_dir: Directory that holds smoke ledgers.
        :returns: The open ledger recording both created objects.
        :raises SmokeError: If a ledger for ``run_id`` already exists, if either
            fixture is already present, or if a provider step fails.
        """
        name = fixture_name(phase, case, run_id)
        if SmokeLedger.path_for(state_dir, run_id).is_file():
            raise SmokeError("run_already_open", run_id)
        github_path = f"{self._owner}/{name}"
        gitlab_path = f"{self._group_path}/{name}"
        if self._github.repository(github_path) is not None:
            raise SmokeError("fixture_already_present", github_path)
        if self._gitlab.project(gitlab_path) is not None:
            raise SmokeError("fixture_already_present", gitlab_path)
        ledger = SmokeLedger(
            run_id=run_id,
            phase=sanitize_segment(phase, "phase"),
            case=sanitize_segment(case, "case"),
            name=name,
            gitlab_server=self._gitlab.server,
        )
        ledger.write(state_dir)

        def record_fixture(record: JSONObject) -> None:
            system = str(record["system"])
            ledger.objects[system] = record
            ledger.write(state_dir)

        def record_group(record: JSONObject) -> None:
            ledger.gitlab_group = record
            ledger.write(state_dir)

        create_test_github_repo(
            self._github, self._owner, name, on_created=record_fixture
        )
        create_test_gitlab_repo(
            self._gitlab,
            self._group_path,
            name,
            on_created=record_fixture,
            on_group_created=record_group,
        )
        return ledger

    def cleanup(
        self, state_dir: Path, run_id: str, reconcile: bool = False
    ) -> SmokeLedger:
        """Delete the ledger objects with identity guards and absence read-back.

        Objects are deleted in ledger key order. Each delete re-reads the object
        and refuses a changed ID. With ``reconcile``, an object that is already
        absent is recorded as cleaned instead of aborting, which finishes a
        cleanup that a previous run left partial.

        :param state_dir: Directory that holds smoke ledgers.
        :param run_id: Run identifier whose ledger is closed.
        :param reconcile: Tolerate objects already absent instead of aborting.
        :returns: The closed ledger with per-object cleanup evidence.
        :raises SmokeError: If the ledger is missing, already closed, an object
            identity changed, or an absence read-back fails.
        """
        ledger = SmokeLedger.read(state_dir, run_id)
        if ledger.status != LEDGER_STATUS_OPEN:
            raise SmokeError("run_already_closed", run_id)
        if ledger.gitlab_server != self._gitlab.server:
            raise SmokeError(
                "gitlab_server_mismatch", "select the original GitLab server"
            )
        for key in sorted(ledger.objects):
            record = ledger.objects[key]
            system = record.get("system")
            path = record.get("path")
            raw_id = record.get("id")
            if (
                system not in (GITHUB_SYSTEM, GITLAB_SYSTEM)
                or not isinstance(path, str)
                or type(raw_id) is not int
            ):
                raise SmokeError("ledger_invalid", f"objects.{key}")
            try:
                if system == GITHUB_SYSTEM:
                    evidence = self._github.delete_repository(path, raw_id)
                else:
                    evidence = self._gitlab.delete_project(path, raw_id)
            except SmokeError as exc:
                if reconcile and exc.reason in (
                    "repository_absent_before_delete",
                    "project_absent_before_delete",
                ):
                    evidence = {"path": path, "already_absent": True, "mutated": False}
                else:
                    ledger.write(state_dir)
                    raise
            ledger.cleanup[key] = evidence
        ledger.status = LEDGER_STATUS_CLOSED
        ledger.closed_at = utc_now()
        ledger.write(state_dir)
        return ledger
