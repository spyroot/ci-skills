"""Offline evidence for GitLab-only target, source, and exact-target access."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.python.conftest import import_script_module

ACCESS = import_script_module("core.access")
CREDENTIALS = import_script_module("core.credentials")
TARGET = import_script_module("core.target")
SCRIPT = import_script_module("gitlab_access")
PROJECT_BINDING = import_script_module("core.project_binding")


def _target(
    tmp_path: Path,
    *,
    filename: str = "selected.toml",
    url: str = "https://gitlab.example.test",
    token_file: str | Path | None = None,
    body: str = "",
) -> Path:
    path = tmp_path / filename
    token = f'token_file = "{token_file}"\n' if token_file else ""
    path.write_text(
        f'[gitlab]\nurl = "{url}"\n{token}{body}',
        encoding="utf-8",
    )
    return path


def _identity(monkeypatch) -> None:
    monkeypatch.setattr(
        CREDENTIALS,
        "resolve_skill_identity",
        lambda _revision: {
            "digest": {"algorithm": "sha256-tree-v1", "value": "a" * 64},
            "revision": {"value": "b" * 40, "verified": False},
        },
    )


def _clear_gitlab_env(monkeypatch) -> None:
    for name in ("GITLAB_TOKEN", "GITLAB_ACCESS_TOKEN", "OAUTH_TOKEN", "CI_JOB_TOKEN"):
        monkeypatch.delenv(name, raising=False)


class FakeAPI:
    def __init__(self, responses: dict[str, object]):
        self.responses = responses
        self.calls: list[tuple[object, str]] = []

    def get_json(self, session, endpoint: str):
        self.calls.append((session, endpoint))
        return self.responses[endpoint]


def _bound_project(tmp_path, monkeypatch, *, reference: str = "team/repo"):
    _identity(monkeypatch)
    _clear_gitlab_env(monkeypatch)
    monkeypatch.setenv("GITLAB_TOKEN", "selected-token")
    target = TARGET.load_gitlab_target(
        _target(tmp_path, body=f'project = "{reference}"\n')
    )
    kind, selected = TARGET.select_gitlab_reference(target)
    return CREDENTIALS.bind_gitlab_session(
        target,
        target_kind=kind,
        target_reference=selected,
        target_source="argv:--target",
    )


def test_gitlab_only_target_does_not_weaken_full_diagnostic_target(tmp_path):
    path = _target(tmp_path, body='project = "team/repo"\n')
    selected = TARGET.load_gitlab_target(path)
    assert selected.gitlab.project == "team/repo"
    with pytest.raises(TARGET.TargetError, match="selected authority tables"):
        TARGET.load_target(path)


def test_full_target_can_supply_project_without_changing_diagnostic_gate(tmp_path):
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


def test_gitlab_access_ignores_unavailable_unrelated_kubernetes_settings(tmp_path):
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


def test_gitlab_access_rejects_unknown_top_level_table(tmp_path):
    path = _target(tmp_path, body='project = "team/repo"\n[other]\nvalue = "x"\n')
    with pytest.raises(TARGET.TargetError, match="without extra tables"):
        TARGET.load_gitlab_target(path)


def test_explicit_project_overrides_a_selected_default_group(tmp_path):
    target = TARGET.load_gitlab_target(_target(tmp_path, body='group = "team"\n'))

    assert TARGET.select_gitlab_reference(target, project="team/repo") == (
        "project",
        "team/repo",
    )


def test_relative_file_source_overrides_ambient_tokens_and_reads_exact_project(
    tmp_path, monkeypatch
):
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
    session = CREDENTIALS.bind_gitlab_session(
        target,
        target_kind=kind,
        target_reference=reference,
        target_source="argv:--target",
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
    tmp_path, monkeypatch, project, reason
):
    session = _bound_project(tmp_path, monkeypatch)
    api = FakeAPI(
        {
            "user": {
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


def test_selected_missing_file_never_falls_back_to_ambient_token(tmp_path, monkeypatch):
    _identity(monkeypatch)
    monkeypatch.setenv("GITLAB_TOKEN", "ambient-token")
    target = TARGET.load_gitlab_target(
        _target(
            tmp_path, token_file=tmp_path / "missing", body='project = "team/repo"\n'
        )
    )
    with pytest.raises(TARGET.TargetError, match="credential_file_unavailable"):
        CREDENTIALS.bind_gitlab_session(
            target,
            target_kind="project",
            target_reference="team/repo",
            target_source="argv:--target",
        )


def test_environment_source_is_pinned_by_precedence(tmp_path, monkeypatch):
    _identity(monkeypatch)
    monkeypatch.setenv("GITLAB_TOKEN", "first-token")
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "second-token")
    target = TARGET.load_gitlab_target(
        _target(tmp_path, body='project = "team/repo"\n')
    )
    session = CREDENTIALS.bind_gitlab_session(
        target,
        target_kind="project",
        target_reference="team/repo",
        target_source="argv:--target",
    )
    assert session.credential_source == "env:GITLAB_TOKEN"
    assert session.environment["GITLAB_TOKEN"] == "first-token"
    assert session.environment["GITLAB_ACCESS_TOKEN"] is None


def test_bound_session_records_effective_source_and_exact_target(tmp_path, monkeypatch):
    _identity(monkeypatch)
    _clear_gitlab_env(monkeypatch)
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "selected-token")
    target_file = _target(tmp_path, body='group = "platform/team"\n')
    target = TARGET.load_gitlab_target(target_file)
    kind, reference = TARGET.select_gitlab_reference(target)
    session = CREDENTIALS.bind_gitlab_session(
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


def test_group_id_readback_requires_the_selected_numeric_id(tmp_path, monkeypatch):
    _identity(monkeypatch)
    _clear_gitlab_env(monkeypatch)
    monkeypatch.setenv("GITLAB_TOKEN", "selected-token")
    target = TARGET.load_gitlab_target(_target(tmp_path, body="group = 71\n"))
    kind, reference = TARGET.select_gitlab_reference(target)
    session = CREDENTIALS.bind_gitlab_session(
        target,
        target_kind=kind,
        target_reference=reference,
        target_source="argv:--target",
    )
    api = FakeAPI(
        {
            "user": {
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
    tmp_path, monkeypatch
):
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


def test_malformed_target_response_blocks_after_identity_read(tmp_path, monkeypatch):
    session = _bound_project(tmp_path, monkeypatch)
    receipt = ACCESS.check_gitlab_operation_access(
        session,
        api_client=FakeAPI(
            {
                "user": {
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
    tmp_path, monkeypatch, capsys
):
    path = _target(tmp_path, body='project = "team/repo"\n')
    monkeypatch.setattr(
        SCRIPT,
        "bind_gitlab_session",
        lambda *_args, **_kwargs: pytest.fail("dry-run bound credentials"),
    )
    result = SCRIPT.main(["check", "--target", str(path), "--dry-run", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert result == 0
    assert data["status"] == "DRY_RUN"
    assert data["target"] == {"kind": "project", "reference": "team/repo"}
    assert data["credential_source"] is None


def _smoke_target_layout(tmp_path: Path) -> dict[str, object]:
    """Build one project/home layout for both normal and smoke selections."""
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
    tmp_path, monkeypatch, capsys, smoke
):
    """The same project chooses target.toml or smoke.toml only by smoke mode."""
    layout = _smoke_target_layout(tmp_path)
    _identity(monkeypatch)
    monkeypatch.chdir(layout["project"])
    monkeypatch.setenv("HOME", str(layout["home"]))
    monkeypatch.delenv("CI_SKILLS_TARGET", raising=False)
    monkeypatch.setenv("GITLAB_TOKEN", "ambient-token")
    if smoke:
        monkeypatch.setenv(PROJECT_BINDING.SMOKE_ENV, "1")
        monkeypatch.setattr(
            SCRIPT,
            "check_gitlab_operation_access",
            lambda _session: pytest.fail("selected credential failure called provider"),
        )
    else:
        monkeypatch.delenv(PROJECT_BINDING.SMOKE_ENV, raising=False)
        responses = {
            "user": layout["identity"],
            "projects/selected%2Frepo": layout["target"],
        }
        api = FakeAPI(responses)
        monkeypatch.setattr(
            SCRIPT,
            "check_gitlab_operation_access",
            lambda session: ACCESS.check_gitlab_operation_access(
                session, api_client=api
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
    tmp_path, monkeypatch, capsys
):
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
        "bind_gitlab_session",
        lambda *_args, **_kwargs: pytest.fail("dry run bound credentials"),
    )

    result = SCRIPT.main(["check", "--dry-run", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert result == 0
    assert data["target_source"] == "user"
    assert data["target"] == {"kind": "project", "reference": "unit/repo"}
    assert data["credential_source"] is None


def test_live_receipt_is_portable_redacted_and_parseable(tmp_path, monkeypatch, capsys):
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
        SCRIPT,
        "check_gitlab_operation_access",
        lambda session: ACCESS.check_gitlab_operation_access(session, api_client=api),
    )
    result = SCRIPT.main(
        [
            "check",
            "--target",
            str(target_file),
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
