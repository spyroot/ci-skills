"""Offline evidence for GitLab-only target, source, and exact-target access.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, MutableMapping, MutableSequence
from pathlib import Path
from typing import TYPE_CHECKING, Final, Optional, Union

import pytest
from conftest import import_script_module

ACCESS = import_script_module("lib.python.access.gitlab")
API = import_script_module("lib.python.core.gitlab.gitlab_api")
CLI = import_script_module("lib.python.core.gitlab.cli")
GITLAB_CREDENTIALS = import_script_module("lib.python.access.gitlab_credentials")
GITLAB_SESSION = import_script_module("lib.python.core.gitlab.gitlab_session")
TARGET = import_script_module("lib.python.core.target")
SCRIPT = import_script_module("gitlab_access")
PROJECT_BINDING = import_script_module("lib.python.core.project_binding")

REVISION: Final = "a" * 40


if TYPE_CHECKING:
    from lib.python.core.gitlab.gitlab_api import GlabAPIClient
    from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
    from lib.python.core.json_types import JSONObject, JSONValue


def _target(
    tmp_path: Path,
    *,
    filename: str = "selected.toml",
    url: str = "https://gitlab.example.test",
    token_file: Optional[Union[str, Path]] = None,
    body: str = "",
) -> Path:
    """Write a GitLab target with selected project and credential fields.

    :param tmp_path: Directory receiving the target TOML file.
    :param filename: Target file basename.
    :param url: GitLab origin written to the target.
    :param token_file: Optional token path written to the GitLab section.
    :param body: Additional GitLab fields or deliberately invalid tables.
    :returns: Path to the written target file.
    """
    path = tmp_path / filename
    token = f'token_file = "{token_file}"\n' if token_file else ""
    path.write_text(
        f'[gitlab]\nurl = "{url}"\n{token}{body}',
        encoding="utf-8",
    )
    return path


def _identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace skill identity lookup with nonsecret digest/revision evidence.

    :param monkeypatch: Pytest fixture replacing the identity resolver.

    :returns: None; the helper records behavior through its side effects.
    """
    monkeypatch.setattr(
        GITLAB_CREDENTIALS,
        "resolve_skill_identity",
        lambda _revision: {
            "digest": {"algorithm": "sha256-tree-v1", "value": "a" * 64},
            "revision": {"value": "b" * 40, "verified": False},
        },
    )


def _clear_gitlab_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove ambient GitLab token candidates before precedence checks.

    :param monkeypatch: Pytest fixture deleting provider token variables.

    :returns: None; the helper records behavior through its side effects.
    """
    for name in ("GITLAB_TOKEN", "GITLAB_ACCESS_TOKEN", "OAUTH_TOKEN", "CI_JOB_TOKEN"):
        monkeypatch.delenv(name, raising=False)


class FakeAPI:
    """Return scripted GitLab identity and target reads for access checks."""

    def __init__(self, responses: Mapping[str, JSONValue]):
        """Set exact endpoint responses and start a session/endpoint journal.

        :param responses: Relative API paths mapped to fixture JSON results.
        """
        self.responses = responses
        self.calls: MutableSequence[tuple[BoundGitLabSession, str]] = []

    def get_json(self, session: BoundGitLabSession, endpoint: str) -> JSONValue:
        """Record a selected-session read and return its endpoint fixture.

        :param session: Bound GitLab session whose identity is later asserted.
        :param endpoint: Exact user or target path requested by the access gate.
        :returns: JSON fixture for that endpoint.
        """
        self.calls.append((session, endpoint))
        return self.responses[endpoint]


def test_shared_setup_resolves_binds_and_reads_access_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reuse one target and bound session through resolution, bind, and read.

    :param monkeypatch: Pytest fixture replacing each shared setup operation.

    :returns: None; assertions in the body validate the regression.
    """
    target = TARGET.GitLabOperationTarget(
        TARGET.GitLabTarget(
            url="https://gitlab.example.test",
            host="gitlab.example.test",
            project="team/repo",
        ),
        Path("/selected/target.toml"),
    )
    session = GITLAB_SESSION.BoundGitLabSession(
        origin="https://gitlab.example.test",
        host="gitlab.example.test",
        target_kind="project",
        target_reference="team/repo",
        target_source="project",
        target_file=Path("/selected/target.toml"),
        credential_source="env:GITLAB_TOKEN",
        credential_digest="sha256:unit",
        execution_host="unit-host",
        skill={"digest": "unit", "revision": {"value": REVISION, "verified": True}},
        environment={},
    )
    api = API.GlabAPIClient()
    receipt: JSONObject = {
        "status": "PASS",
        "identity": {"id": 17, "username": "operator"},
        "target": {"id": 42, "full_path": "team/repo"},
    }
    observed: MutableSequence[tuple[str, str]] = []

    def resolve(
        explicit_target: Optional[str],
        explicit_binding: Optional[str],
        *,
        dry_run: bool,
    ) -> tuple[TARGET.GitLabOperationTarget, str]:
        """Record exact target/binding selection for the shared setup.

        :param explicit_target: Caller-selected target file argument.
        :param explicit_binding: Caller-selected binding file argument.
        :param dry_run: Whether setup requested a non-live target resolution.
        :returns: Fixture target and project source reference.
        """
        observed.append(("resolve", f"{explicit_target}:{explicit_binding}:{dry_run}"))
        return target, "project"

    def select(
        selected: TARGET.GitLabOperationTarget, *, project: str, group: Optional[str]
    ) -> tuple[str, str]:
        """Record project/group selection from the resolved target.

        :param selected: Target passed from resolution to scope selection.
        :param project: Explicit project reference.
        :param group: Explicit group reference, absent in this case.
        :returns: Selected project kind and exact reference.
        """
        observed.append(("select", f"{selected is target}:{project}:{group}"))
        return "project", "team/repo"

    def bind(
        selected: TARGET.GitLabOperationTarget,
        *,
        target_kind: str,
        target_reference: str,
        target_source: str,
        revision: Optional[str],
    ) -> BoundGitLabSession:
        """Record selected scope and return the fixture session.

        :param selected: Target passed to the GitLab binder.
        :param target_kind: Selected GitLab target kind.
        :param target_reference: Exact selected project or group reference.
        :param target_source: Nonsecret source of the selected target.
        :param revision: Installed skill revision supplied by the caller.
        :returns: Bound fixture session reused by the access read.
        """
        observed.append(
            (
                "bind",
                (
                    f"{selected is target}:{target_kind}:{target_reference}:"
                    f"{target_source}:{revision}"
                ),
            )
        )
        return session

    def check(
        bound_session: BoundGitLabSession, *, api_client: GlabAPIClient
    ) -> JSONObject:
        """Record the access read against the bound session and API client.

        :param bound_session: Session returned by the fake binder.
        :param api_client: Injected API client passed by the caller.
        :returns: Passing identity and selected-target receipt.
        """
        observed.append(("access", f"{bound_session is session}:{api_client is api}"))
        return receipt

    monkeypatch.setattr(CLI, "resolve_gitlab_target", resolve)
    monkeypatch.setattr(CLI, "select_gitlab_reference", select, raising=False)
    monkeypatch.setattr(CLI, "bind_gitlab_session", bind)
    monkeypatch.setattr(CLI, "check_gitlab_operation_access", check)

    actual_session, actual_receipt = CLI.setup_gitlab_operation(
        "selected.toml",
        "binding.toml",
        project="team/repo",
        group=None,
        revision=REVISION,
        api_client=api,
    )

    assert actual_session is session
    assert actual_receipt is receipt
    assert actual_receipt["identity"] == {"id": 17, "username": "operator"}
    assert observed == [
        ("resolve", "selected.toml:binding.toml:False"),
        ("select", "True:team/repo:None"),
        ("bind", f"True:project:team/repo:project:{REVISION}"),
        ("access", "True:True"),
    ]


def test_live_entrypoint_uses_shared_setup_and_emits_its_receipt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Delegate the live access CLI to shared setup without rewriting receipt.

    :param monkeypatch: Pytest fixture replacing shared setup.
    :param capsys: Pytest fixture capturing JSON CLI output.

    :returns: None; assertions in the body validate the regression.
    """
    session = GITLAB_SESSION.BoundGitLabSession(
        origin="https://gitlab.example.test",
        host="gitlab.example.test",
        target_kind="project",
        target_reference="team/repo",
        target_source="env:CI_SKILLS_TARGET",
        target_file=Path("/selected/target.toml"),
        credential_source="env:GITLAB_TOKEN",
        credential_digest="sha256:unit",
        execution_host="unit-host",
        skill={"digest": "unit", "revision": {"value": REVISION, "verified": True}},
        environment={},
    )
    receipt: JSONObject = {
        "status": "PASS",
        "identity": {"id": 17, "username": "operator"},
        "target": {"id": 42, "full_path": "team/repo"},
        "errors": [],
    }
    observed: MutableSequence[
        tuple[Optional[str], Optional[str], Mapping[str, Optional[str]]]
    ] = []

    def setup(
        explicit_target: Optional[str],
        explicit_binding: Optional[str],
        *,
        project: Optional[str],
        group: Optional[str],
        revision: Optional[str],
    ) -> tuple[BoundGitLabSession, JSONObject]:
        """Record CLI selection options and return a passing receipt.

        :param explicit_target: Target path argument passed by the command.
        :param explicit_binding: Binding path argument passed by the command.
        :param project: Project override supplied by the command.
        :param group: Group override supplied by the command.
        :param revision: Installed skill revision supplied by the command.
        :returns: Bound fixture session and original access receipt.
        """
        observed.append(
            (
                explicit_target,
                explicit_binding,
                {"project": project, "group": group, "revision": revision},
            )
        )
        return session, receipt

    monkeypatch.setenv("CI_SKILLS_TARGET", "selected.toml")
    monkeypatch.setattr(SCRIPT, "setup_gitlab_operation", setup)

    result = SCRIPT.main(
        [
            "check",
            "--project",
            "team/repo",
            "--revision",
            REVISION,
            "--json",
        ]
    )
    data = json.loads(capsys.readouterr().out)

    assert result == 0
    assert data == receipt
    assert observed == [
        (
            None,
            None,
            {"project": "team/repo", "group": None, "revision": REVISION},
        )
    ]


def _bound_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, reference: str = "team/repo"
) -> BoundGitLabSession:
    """Bind a project target to a selected synthetic token source.

    :param tmp_path: Directory receiving the GitLab target.
    :param monkeypatch: Pytest fixture selecting the environment token.
    :param reference: Project path written to the target and bound session.
    :returns: GitLab session pinned to that project and source.
    """
    _identity(monkeypatch)
    _clear_gitlab_env(monkeypatch)
    monkeypatch.setenv("GITLAB_TOKEN", "selected-token")
    target = TARGET.load_gitlab_target(
        _target(tmp_path, body=f'project = "{reference}"\n')
    )
    kind, selected = TARGET.select_gitlab_reference(target)
    return GITLAB_CREDENTIALS.bind_gitlab_session(
        target,
        target_kind=kind,
        target_reference=selected,
        target_source="env:CI_SKILLS_TARGET",
    )


def test_gitlab_only_target_does_not_weaken_full_diagnostic_target(
    tmp_path: Path,
) -> None:
    """Accept GitLab-only loading while keeping full diagnostic target strict.

    :param tmp_path: Directory receiving a GitLab-only target.

    :returns: None; assertions in the body validate the regression.
    """
    path = _target(tmp_path, body='project = "team/repo"\n')
    selected = TARGET.load_gitlab_target(path)
    assert selected.gitlab.project == "team/repo"
    with pytest.raises(
        TARGET.TargetError,
        match="^target is missing required authority tables: github, kubernetes$",
    ):
        TARGET.load_target(path)


def test_full_target_can_supply_project_without_changing_diagnostic_gate(
    tmp_path: Path,
) -> None:
    """Read a project from a complete target through both target loaders.

    :param tmp_path: Directory receiving a three-surface target.

    :returns: None; assertions in the body validate the regression.
    """
    path = tmp_path / "full.toml"
    path.write_text(
        '[github]\nhost = "github.example.test"\nrepository = "unit/repo"\n'
        '[gitlab]\nurl = "https://gitlab.example.test"\nproject = "team/repo"\n'
        '[kubernetes]\ncontext = "unit"\n'
        'server = "https://api.cluster.example.test:6443"\n',
        encoding="utf-8",
    )
    assert TARGET.load_target(path).gitlab.project == "team/repo"
    assert TARGET.load_gitlab_target(path).gitlab.project == "team/repo"


def test_gitlab_access_ignores_unavailable_unrelated_kubernetes_settings(
    tmp_path: Path,
) -> None:
    """Keep GitLab-only access independent of invalid Kubernetes paths.

    :param tmp_path: Directory receiving a mixed target with missing kubeconfig.

    :returns: None; assertions in the body validate the regression.
    """
    path = tmp_path / "mixed.toml"
    path.write_text(
        '[github]\nhost = "github.example.test"\nrepository = "unit/repo"\n'
        '[gitlab]\nurl = "https://gitlab.example.test"\nproject = "team/repo"\n'
        '[kubernetes]\ncontext = "unavailable-here"\n'
        'server = "https://api.cluster.example.test:6443"\n'
        'kubeconfigs = ["missing-on-this-host"]\n',
        encoding="utf-8",
    )
    with pytest.raises(TARGET.TargetError, match="absolute paths"):
        TARGET.load_target(path)
    selected = TARGET.load_gitlab_target(path)
    assert selected.gitlab.project == "team/repo"
    assert selected.source_file == path.resolve()


def test_gitlab_access_rejects_unknown_top_level_table(tmp_path: Path) -> None:
    """Reject undeclared target tables even in GitLab-only mode.

    :param tmp_path: Directory receiving the malformed target.

    :returns: None; assertions in the body validate the regression.
    """
    path = _target(tmp_path, body='project = "team/repo"\n[other]\nvalue = "x"\n')
    with pytest.raises(
        TARGET.TargetError, match="^target contains unsupported tables: other$"
    ):
        TARGET.load_gitlab_target(path)


def test_explicit_project_overrides_a_selected_default_group(tmp_path: Path) -> None:
    """Prefer an explicit project argument to a default group in target.

    :param tmp_path: Directory receiving a group-default target.

    :returns: None; assertions in the body validate the regression.
    """
    target = TARGET.load_gitlab_target(_target(tmp_path, body='group = "team"\n'))

    assert TARGET.select_gitlab_reference(target, project="team/repo") == (
        "project",
        "team/repo",
    )


def test_relative_file_source_overrides_ambient_tokens_and_reads_exact_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolve a relative token file and reuse it for exact project access.

    :param tmp_path: Scratch target, file token, and unrelated cwd.
    :param monkeypatch: Pytest fixture setting ambient tokens and cwd.

    :returns: None; assertions in the body validate the regression.
    """
    target_parent = tmp_path / "operator-config"
    target_parent.mkdir()
    token_file = target_parent / "token"
    token_file.write_text("file-token\n", encoding="utf-8")
    path = _target(target_parent, token_file="token", body='project = "team/repo"\n')
    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)
    _identity(monkeypatch)
    monkeypatch.setenv("GITLAB_TOKEN", "ambient-token")
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "other-token")
    monkeypatch.setenv("OAUTH_TOKEN", "oauth-token")
    monkeypatch.setenv("CI_JOB_TOKEN", "job-token")
    target = TARGET.load_gitlab_target(path)
    kind, reference = TARGET.select_gitlab_reference(target)
    session = GITLAB_CREDENTIALS.bind_gitlab_session(
        target,
        target_kind=kind,
        target_reference=reference,
        target_source="env:CI_SKILLS_TARGET",
    )
    assert session.credential_source == f"file:{token_file.resolve()}"
    assert session.credential_digest == (
        "sha256:" + hashlib.sha256(b"file-token").hexdigest()
    )
    assert session.environment["GITLAB_TOKEN"] == "file-token"
    assert session.environment["GITLAB_ACCESS_TOKEN"] is None
    assert session.environment["OAUTH_TOKEN"] is None
    assert session.environment["CI_JOB_TOKEN"] is None
    assert "file-token" not in repr(session)
    api = FakeAPI(
        {
            "user": {
                "is_admin": True,
                "id": 17,
                "username": "operator",
                "web_url": "https://gitlab.example.test/operator",
            },
            "projects/team%2Frepo": {
                "id": 42,
                "path_with_namespace": "team/repo",
                "web_url": "https://gitlab.example.test/team/repo",
            },
        }
    )
    receipt = ACCESS.check_gitlab_operation_access(session, api_client=api)
    assert receipt["status"] == "PASS"
    assert receipt["identity"]["id"] == 17
    assert receipt["target"]["id"] == 42
    assert [endpoint for _, endpoint in api.calls] == [
        "user",
        "projects/team%2Frepo",
    ]
    assert all(bound is session for bound, _ in api.calls)
    assert "file-token" not in json.dumps(receipt)


@pytest.mark.parametrize(
    ("project", "reason"),
    [
        (
            {
                "id": 42,
                "path_with_namespace": "other/repo",
                "web_url": "https://gitlab.example.test/other/repo",
            },
            "target_path_mismatch",
        ),
        (
            {
                "id": 42,
                "path_with_namespace": "team/repo",
                "web_url": "https://elsewhere.example.test/team/repo",
            },
            "target_host_mismatch",
        ),
    ],
)
def test_wrong_project_or_host_blocks_with_sanitized_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, project: JSONObject, reason: str
) -> None:
    """Block a project path or origin mismatch without leaking token data.

    :param tmp_path: Directory receiving the selected project target.
    :param monkeypatch: Pytest fixture selecting the bound token.
    :param project: Provider project record with mismatched path or host.
    :param reason: Expected structured target mismatch reason.

    :returns: None; assertions in the body validate the regression.
    """
    session = _bound_project(tmp_path, monkeypatch)
    api = FakeAPI(
        {
            "user": {
                "is_admin": True,
                "id": 17,
                "username": "operator",
                "web_url": "https://gitlab.example.test/operator",
            },
            "projects/team%2Frepo": project,
        }
    )
    receipt = ACCESS.check_gitlab_operation_access(session, api_client=api)
    assert receipt["status"] == "BLOCKED"
    assert receipt["target"] is None
    assert receipt["errors"] == [{"source": "target", "reason": reason}]
    assert "selected-token" not in json.dumps(receipt)


@pytest.mark.parametrize("is_admin", (True, False))
def test_gitlab_identity_is_observed_once_and_role_precedes_target_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, is_admin: bool
) -> None:
    """Reuse one native identity and reject insufficient access before target work.

    :param tmp_path: Isolated target and credential fixture directory.
    :param monkeypatch: Fixture selecting only the synthetic configured credential.
    :param is_admin: Native current-user administrator flag returned by GitLab.
    :returns: None; assertions prove identity request counts and operation ordering.
    """
    session = _bound_project(tmp_path, monkeypatch)
    api = FakeAPI(
        {
            "user": {
                "id": 17,
                "username": "operator",
                "is_admin": is_admin,
                "web_url": "https://gitlab.example.test/operator",
            },
            "projects/team%2Frepo": {
                "id": 42,
                "path_with_namespace": "team/repo",
                "web_url": "https://gitlab.example.test/team/repo",
            },
        }
    )

    first = ACCESS.check_gitlab_operation_access(session, api_client=api)
    second = ACCESS.check_gitlab_operation_access(session, api_client=api)

    assert first["status"] == second["status"] == ("PASS" if is_admin else "BLOCKED")
    if not is_admin:
        assert (
            first["errors"]
            == second["errors"]
            == [{"source": "user", "reason": "gitlab_required_role_missing"}]
        )
    endpoints = [endpoint for _, endpoint in api.calls]
    assert endpoints.count("user") == 1
    assert endpoints.count("projects/team%2Frepo") == (2 if is_admin else 0)
    assert session.invocation is not None
    assert session.invocation.gitlab_runtime.role == ("admin" if is_admin else "user")


def test_selected_missing_file_never_falls_back_to_ambient_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed on a selected missing token file despite ambient token.

    :param tmp_path: Directory holding the target and absent token path.
    :param monkeypatch: Pytest fixture setting a lower-priority env token.

    :returns: None; assertions in the body validate the regression.
    """
    _identity(monkeypatch)
    monkeypatch.setenv("GITLAB_TOKEN", "ambient-token")
    target = TARGET.load_gitlab_target(
        _target(
            tmp_path, token_file=tmp_path / "missing", body='project = "team/repo"\n'
        )
    )
    with pytest.raises(TARGET.TargetError, match="credential_file_unavailable"):
        GITLAB_CREDENTIALS.bind_gitlab_session(
            target,
            target_kind="project",
            target_reference="team/repo",
            target_source="env:CI_SKILLS_TARGET",
        )


def test_environment_source_is_pinned_by_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Choose GITLAB_TOKEN before lower-priority ambient candidates.

    :param tmp_path: Directory receiving a project target.
    :param monkeypatch: Pytest fixture setting competing token variables.

    :returns: None; assertions in the body validate the regression.
    """
    _identity(monkeypatch)
    monkeypatch.setenv("GITLAB_TOKEN", "first-token")
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "second-token")
    target = TARGET.load_gitlab_target(
        _target(tmp_path, body='project = "team/repo"\n')
    )
    session = GITLAB_CREDENTIALS.bind_gitlab_session(
        target,
        target_kind="project",
        target_reference="team/repo",
        target_source="env:CI_SKILLS_TARGET",
    )
    assert session.credential_source == "env:GITLAB_TOKEN"
    assert session.environment["GITLAB_TOKEN"] == "first-token"
    assert session.environment["GITLAB_ACCESS_TOKEN"] is None


def test_bound_session_records_effective_source_and_exact_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Record selected group, host, target path, and effective token source.

    :param tmp_path: Directory receiving a group target.
    :param monkeypatch: Pytest fixture selecting GITLAB_ACCESS_TOKEN.

    :returns: None; assertions in the body validate the regression.
    """
    _identity(monkeypatch)
    _clear_gitlab_env(monkeypatch)
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "selected-token")
    target_file = _target(tmp_path, body='group = "platform/team"\n')
    target = TARGET.load_gitlab_target(target_file)
    kind, reference = TARGET.select_gitlab_reference(target)
    session = GITLAB_CREDENTIALS.bind_gitlab_session(
        target,
        target_kind=kind,
        target_reference=reference,
        target_source="env:CI_SKILLS_TARGET;target:gitlab.group",
    )
    assert session.credential_source == "env:GITLAB_ACCESS_TOKEN"
    assert session.origin == "https://gitlab.example.test"
    assert session.host == "gitlab.example.test"
    assert session.target_kind == "group"
    assert session.target_reference == "platform/team"
    assert session.target_source == "env:CI_SKILLS_TARGET;target:gitlab.group"
    assert session.target_file == target_file.resolve()
    assert session.environment["GITLAB_TOKEN"] == "selected-token"
    assert session.environment["GITLAB_ACCESS_TOKEN"] is None
    assert session.environment["CI_JOB_TOKEN"] is None
    assert session.environment["GITLAB_HOST"] == "gitlab.example.test"
    assert session.environment["GLAB_NO_PROMPT"] == "1"
    assert "selected-token" not in repr(session)


def test_group_id_readback_requires_the_selected_numeric_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Block a group read-back whose numeric ID differs from selection.

    :param tmp_path: Directory receiving the group-ID target.
    :param monkeypatch: Pytest fixture selecting the bound token.

    :returns: None; assertions in the body validate the regression.
    """
    _identity(monkeypatch)
    _clear_gitlab_env(monkeypatch)
    monkeypatch.setenv("GITLAB_TOKEN", "selected-token")
    target = TARGET.load_gitlab_target(_target(tmp_path, body="group = 71\n"))
    kind, reference = TARGET.select_gitlab_reference(target)
    session = GITLAB_CREDENTIALS.bind_gitlab_session(
        target,
        target_kind=kind,
        target_reference=reference,
        target_source="env:CI_SKILLS_TARGET",
    )
    api = FakeAPI(
        {
            "user": {
                "is_admin": True,
                "id": 17,
                "username": "operator",
                "web_url": "https://gitlab.example.test/operator",
            },
            "groups/71": {
                "id": 72,
                "full_path": "team",
                "web_url": "https://gitlab.example.test/team",
            },
        }
    )
    receipt = ACCESS.check_gitlab_operation_access(session, api_client=api)
    assert receipt["status"] == "BLOCKED"
    assert receipt["errors"] == [{"source": "target", "reason": "target_id_mismatch"}]


def test_malformed_user_response_blocks_with_structured_safe_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Block malformed user identity before making target claims.

    :param tmp_path: Directory receiving the selected project target.
    :param monkeypatch: Pytest fixture selecting the bound token.

    :returns: None; assertions in the body validate the regression.
    """
    session = _bound_project(tmp_path, monkeypatch)
    receipt = ACCESS.check_gitlab_operation_access(
        session,
        api_client=FakeAPI(
            {
                "user": [],
            }
        ),
    )
    assert receipt["status"] == "BLOCKED"
    assert receipt["identity"] is None
    assert receipt["target"] is None
    assert receipt["observed_capability"] == []
    assert receipt["errors"] == [{"source": "user", "reason": "user_identity_invalid"}]
    assert "selected-token" not in json.dumps(receipt)


def test_malformed_target_response_blocks_after_identity_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retain verified identity but block malformed project read-back.

    :param tmp_path: Directory receiving the selected project target.
    :param monkeypatch: Pytest fixture selecting the bound token.

    :returns: None; assertions in the body validate the regression.
    """
    session = _bound_project(tmp_path, monkeypatch)
    receipt = ACCESS.check_gitlab_operation_access(
        session,
        api_client=FakeAPI(
            {
                "user": {
                    "is_admin": True,
                    "id": 17,
                    "username": "operator",
                    "web_url": "https://gitlab.example.test/operator",
                },
                "projects/team%2Frepo": {"path_with_namespace": "team/repo"},
            }
        ),
    )
    assert receipt["status"] == "BLOCKED"
    assert receipt["identity"]["username"] == "operator"
    assert receipt["target"] is None
    assert receipt["observed_capability"] == ["identity_read"]
    assert receipt["errors"] == [
        {"source": "target", "reason": "target_response_invalid"}
    ]
    assert "selected-token" not in json.dumps(receipt)


def test_cli_dry_run_never_binds_credentials_or_calls_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Publish GitLab access intent without binding credentials or API calls.

    :param tmp_path: Directory receiving the project target.
    :param monkeypatch: Pytest fixture rejecting shared live setup.
    :param capsys: Pytest fixture capturing dry-run JSON output.

    :returns: None; assertions in the body validate the regression.
    """
    path = _target(tmp_path, body='project = "team/repo"\n')
    monkeypatch.setenv("CI_SKILLS_TARGET", str(path))
    monkeypatch.setattr(
        SCRIPT,
        "setup_gitlab_operation",
        lambda *_args, **_kwargs: pytest.fail("dry-run called shared live setup"),
    )
    result = SCRIPT.main(["check", "--dry-run", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert result == 0
    assert data["status"] == "DRY_RUN"
    assert data["target"] == {"kind": "project", "reference": "team/repo"}
    assert data["credential_source"] is None


def _smoke_target_layout(tmp_path: Path) -> MutableMapping[str, JSONValue]:
    """Create distinct project and home normal/smoke target files.

    :param tmp_path: Scratch root for selected and fallback target tiers.
    :returns: Layout paths and expected identity/target API records.
    """
    project = tmp_path / "project"
    project_target_dir = project / ".ci-skills"
    project_target_dir.mkdir(parents=True)
    selected_token = project_target_dir / "selected.token"
    selected_token.write_text("selected-token\n", encoding="utf-8")
    selected = _target(
        project_target_dir,
        filename="target.toml",
        token_file="selected.token",
        body='project = "selected/repo"\n',
    )
    _target(
        project_target_dir,
        filename=PROJECT_BINDING.SMOKE_TARGET_FILENAME,
        token_file="missing.token",
        body='project = "smoke/repo"\n',
    )
    home_target_dir = tmp_path / "home" / ".ci-skills"
    home_target_dir.mkdir(parents=True)
    _target(
        home_target_dir,
        filename="target.toml",
        url="https://user.example.test",
        body='project = "user/default"\n',
    )
    _target(
        home_target_dir,
        filename=PROJECT_BINDING.SMOKE_TARGET_FILENAME,
        url="https://user-selected.example.test",
        body='project = "user/selected"\n',
    )
    return {
        "project": project,
        "home": tmp_path / "home",
        "selected": selected,
        "selected_token": selected_token,
        "identity": {
            "id": 17,
            "username": "operator",
            "is_admin": True,
            "web_url": "https://gitlab.example.test/operator",
        },
        "target": {
            "id": 42,
            "path_with_namespace": "selected/repo",
            "web_url": "https://gitlab.example.test/selected/repo",
        },
    }


@pytest.mark.parametrize("smoke", (False, True), ids=("default", "smoke"))
def test_smoke_environment_switches_filename_without_fallback_or_provider_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    smoke: bool,
) -> None:
    """Select project target or smoke filename without fallback on failure.

    :param tmp_path: Scratch project/home normal and smoke target files.
    :param monkeypatch: Pytest fixture setting cwd, HOME, and smoke switch.
    :param capsys: Pytest fixture capturing structured access output.
    :param smoke: Whether the smoke target filename is selected.

    :returns: None; assertions in the body validate the regression.
    """
    layout = _smoke_target_layout(tmp_path)
    _identity(monkeypatch)
    monkeypatch.chdir(layout["project"])
    monkeypatch.setenv("HOME", str(layout["home"]))
    monkeypatch.delenv("CI_SKILLS_TARGET", raising=False)
    monkeypatch.setenv("GITLAB_TOKEN", "ambient-token")
    if smoke:
        monkeypatch.setenv(PROJECT_BINDING.SMOKE_ENV, "1")
        monkeypatch.setattr(
            CLI,
            "check_gitlab_operation_access",
            lambda *_args, **_kwargs: pytest.fail(
                "selected credential failure called provider"
            ),
        )
    else:
        monkeypatch.delenv(PROJECT_BINDING.SMOKE_ENV, raising=False)
        responses = {
            "user": layout["identity"],
            "projects/selected%2Frepo": layout["target"],
        }
        api = FakeAPI(responses)
        monkeypatch.setattr(
            CLI,
            "check_gitlab_operation_access",
            lambda session, *, api_client: ACCESS.check_gitlab_operation_access(
                session, api_client=api_client or api
            ),
        )

    result = SCRIPT.main(["check", "--json"])
    data = json.loads(capsys.readouterr().out)

    if smoke:
        assert result == 2
        assert data["status"] == "BLOCKED"
        assert data["errors"] == [
            {"source": "credential", "reason": "credential_file_unavailable"}
        ]
    else:
        assert result == 0
        assert data["status"] == "PASS"
        assert data["target_source"] == "project"
        assert data["target_file"] == str(layout["selected"].resolve())
        assert data["identity"]["id"] == layout["identity"]["id"]
        assert data["identity"]["username"] == layout["identity"]["username"]
        assert data["target"]["id"] == layout["target"]["id"]
        assert data["target"]["full_path"] == layout["target"]["path_with_namespace"]
        assert data["target"]["web_url"] == layout["target"]["web_url"]
        assert data["credential_source"] == f"file:{layout['selected_token'].resolve()}"
        assert [endpoint for _session, endpoint in api.calls] == [
            "user",
            "projects/selected%2Frepo",
        ]


def test_access_check_uses_user_gitlab_target_without_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Select the home GitLab target when project and env tiers are absent.

    :param tmp_path: Scratch home target and unrelated project cwd.
    :param monkeypatch: Pytest fixture setting HOME and clearing selector.
    :param capsys: Pytest fixture capturing dry-run output.

    :returns: None; assertions in the body validate the regression.
    """
    home = tmp_path / "home"
    selected = home / ".ci-skills" / "target.toml"
    selected.parent.mkdir(parents=True)
    selected.write_text(
        '[gitlab]\nurl = "https://gitlab.example.test"\nproject = "unit/repo"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CI_SKILLS_TARGET", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        SCRIPT,
        "setup_gitlab_operation",
        lambda *_args, **_kwargs: pytest.fail("dry-run called shared live setup"),
    )

    result = SCRIPT.main(["check", "--dry-run", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert result == 0
    assert data["target_source"] == "user"
    assert data["target"] == {"kind": "project", "reference": "unit/repo"}
    assert data["credential_source"] is None


def test_live_receipt_is_portable_redacted_and_parseable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Write a portable GitLab access receipt without local path/token leaks.

    :param tmp_path: Scratch target, file token, and receipt destination.
    :param monkeypatch: Pytest fixture selecting token and API client double.
    :param capsys: Pytest fixture capturing the displayed live receipt.

    :returns: None; assertions in the body validate the regression.
    """
    token_file = tmp_path / "credential"
    token_file.write_text("file-token\n", encoding="utf-8")
    target_file = _target(
        tmp_path, token_file=token_file, body='project = "team/repo"\n'
    )
    receipt_file = tmp_path / "receipt.json"
    _identity(monkeypatch)
    _clear_gitlab_env(monkeypatch)
    api = FakeAPI(
        {
            "user": {
                "is_admin": True,
                "id": 17,
                "username": "operator",
                "web_url": "https://gitlab.example.test/operator",
            },
            "projects/team%2Frepo": {
                "id": 42,
                "path_with_namespace": "team/repo",
                "web_url": "https://gitlab.example.test/team/repo",
            },
        }
    )
    monkeypatch.setattr(
        CLI,
        "check_gitlab_operation_access",
        lambda session, *, api_client: ACCESS.check_gitlab_operation_access(
            session, api_client=api_client or api
        ),
    )
    monkeypatch.setenv("CI_SKILLS_TARGET", str(target_file))
    result = SCRIPT.main(
        [
            "check",
            "--receipt-out",
            str(receipt_file),
            "--json",
        ]
    )
    displayed = json.loads(capsys.readouterr().out)
    portable_receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert result == 0
    assert displayed["status"] == "PASS"
    assert portable_receipt["status"] == "PASS"
    assert portable_receipt["target"]["id"] == 42
    assert portable_receipt["target_file"].startswith("path:")
    assert portable_receipt["credential_source"].startswith("file:path:")
    assert "file-token" not in receipt_file.read_text(encoding="utf-8")
    assert str(tmp_path) not in receipt_file.read_text(encoding="utf-8")
    assert not list(tmp_path.glob(".receipt.json.*.partial"))
