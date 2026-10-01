"""Tests for effective credential-source resolution.

A saved CLI login does not prove its stored credential was used. These tests
pin the rule that an environment token shadows a host profile, that a selected
token file shadows both, and that an unresolvable kubeconfig context blocks
instead of falling through to a default.
"""

from __future__ import annotations

import json

import pytest
from conftest import import_script_module


def _modules():
    return import_script_module("core.credsource"), import_script_module("core.runtime")


def _target(path):
    return import_script_module("core.target").load_target(path)


def _write_target(tmp_path, *, github_token=None, gitlab_token=None, kubeconfig=None):
    lines = [
        "[github]", 'host = "github.example.test"', 'repository = "unit/repo"',
    ]
    if github_token:
        lines.append(f'token_file = "{github_token}"')
    lines += ["", "[gitlab]", 'url = "https://gitlab.example.test"']
    if gitlab_token:
        lines.append(f'token_file = "{gitlab_token}"')
    lines += ["", "[kubernetes]", 'context = "unit-context"',
              'server = "https://api.cluster.example.test:6443"']
    if kubeconfig:
        lines.append(f'kubeconfig = "{kubeconfig}"')
    path = tmp_path / "target.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_github_profile_resolves_to_the_store_the_client_names(tmp_path, monkeypatch):
    """With no environment token the source is the store gh itself reports."""
    credsource, runtime = _modules()
    monkeypatch.setattr(
        credsource, "run_command",
        lambda argv, **_k: runtime.CommandResult(
            tuple(argv), 0,
            "github.example.test\n  Logged in to github.example.test account u (keyring)\n", "",
        ),
    )
    source = credsource.resolve_github_source(_target(_write_target(tmp_path)), {})

    assert source.kind == credsource.CREDENTIAL_STORE
    assert source.reference == "gh:keyring:github.example.test"
    assert source.shadowed == []


def test_github_is_unresolved_when_the_client_holds_no_credential(tmp_path, monkeypatch):
    """No environment token and no stored credential is unresolved, not a default."""
    credsource, runtime = _modules()
    monkeypatch.setattr(
        credsource, "run_command",
        lambda argv, **_k: runtime.CommandResult(tuple(argv), 1, "", "not logged in"),
    )
    target = _target(_write_target(tmp_path))

    with pytest.raises(credsource.CredentialSourceError):
        credsource.resolve_github_source(target, {})
    assert credsource.resolve_sources(target, {})["github"]["kind"] == credsource.UNRESOLVED


@pytest.mark.parametrize(
    ("host", "expected"),
    (
        ("github.com", ("GH_TOKEN", "GITHUB_TOKEN")),
        ("tenant.ghe.com", ("GH_TOKEN", "GITHUB_TOKEN")),
        ("GitHub.Com", ("GH_TOKEN", "GITHUB_TOKEN")),
        ("ghe.example.test", ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")),
    ),
)
def test_github_env_names_follow_the_clients_host_classes(host, expected):
    """gh honours GH_TOKEN for github.com and any ghe.com subdomain only."""
    credsource, _ = _modules()

    assert credsource.github_env_names(host) == expected


def test_a_ghe_com_token_file_is_injected_into_the_variable_gh_honours(tmp_path):
    """The injected variable must match the host class, or gh ignores the file."""
    credsource, _ = _modules()
    access = import_script_module("core.access")
    token = tmp_path / "github.token"
    token.write_text("unit\n", encoding="utf-8")
    path = tmp_path / "target.toml"
    path.write_text(
        '[github]\nhost = "tenant.ghe.com"\nrepository = "unit/repo"\n'
        f'token_file = "{token}"\n\n'
        '[gitlab]\nurl = "https://gitlab.example.test"\n\n'
        '[kubernetes]\ncontext = "unit-context"\n'
        'server = "https://api.cluster.example.test:6443"\n',
        encoding="utf-8",
    )
    target = _target(path)

    source = credsource.resolve_github_source(target, {})

    assert source.detail["injected_as"] == "GH_TOKEN"
    assert set(access.github_env(target)) == {"GH_TOKEN"}


@pytest.mark.parametrize("variable", ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"))
def test_github_environment_token_shadows_the_stored_profile(tmp_path, variable):
    """An ambient environment token authenticates, not the keyring entry."""
    credsource, _ = _modules()
    source = credsource.resolve_github_source(
        _target(_write_target(tmp_path)), {variable: "value-not-read"},
    )

    assert source.kind == credsource.ENVIRONMENT
    assert source.reference == f"env:{variable}"
    assert "gh_profile:github.example.test" in source.shadowed
    assert "value-not-read" not in json.dumps(source.as_dict())


def test_github_token_file_shadows_environment_and_profile(tmp_path):
    """A selected token file wins because the gate injects it into the child."""
    credsource, _ = _modules()
    token = tmp_path / "github.token"
    token.write_text("unit\n", encoding="utf-8")
    source = credsource.resolve_github_source(
        _target(_write_target(tmp_path, github_token=token)),
        {"GH_ENTERPRISE_TOKEN": "ambient"},
    )

    assert source.kind == credsource.TOKEN_FILE
    assert source.reference == str(token.resolve())
    assert source.shadowed == ["env:GH_ENTERPRISE_TOKEN", "gh_profile:github.example.test"]
    assert source.detail["injected_as"] == "GH_ENTERPRISE_TOKEN"


def test_gitlab_environment_token_shadows_the_stored_profile(tmp_path):
    """GITLAB_TOKEN overrides a glab host profile, so the receipt must say so."""
    credsource, _ = _modules()
    source = credsource.resolve_gitlab_source(
        _target(_write_target(tmp_path)), {"GITLAB_TOKEN": "ambient"},
    )

    assert source.kind == credsource.ENVIRONMENT
    assert source.reference == "env:GITLAB_TOKEN"
    assert "glab_profile:gitlab.example.test" in source.shadowed


def test_kubeconfig_search_path_follows_the_environment_list(tmp_path):
    """Without an explicit path the KUBECONFIG list is the search order."""
    credsource, _ = _modules()
    target = _target(_write_target(tmp_path))
    first, second = tmp_path / "a.yaml", tmp_path / "b.yaml"

    resolved = credsource.kubeconfig_search_path(
        target, {"KUBECONFIG": f"{first}:{second}"},
    )

    assert [str(item) for item in resolved] == [str(first), str(second)]


def test_kubeconfig_default_is_used_when_nothing_is_selected(tmp_path):
    """An empty environment resolves to the single kubectl default file."""
    credsource, _ = _modules()

    resolved = credsource.kubeconfig_search_path(_target(_write_target(tmp_path)), {})

    assert len(resolved) == 1
    assert resolved[0].name == "config"


def test_kubernetes_source_records_context_user_and_mechanism(tmp_path, monkeypatch):
    """Resolution names the file, context, user entry and auth mechanism."""
    credsource, runtime = _modules()
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")

    def fake_run(argv, **_kwargs):
        return runtime.CommandResult(tuple(argv), 0, json.dumps({
            "contexts": [{"name": "unit-context",
                          "context": {"cluster": "cluster-a", "user": "unit-user"}}],
            "clusters": [{"name": "cluster-a", "cluster": {"server": "https://x:6443"}}],
            "users": [{"name": "unit-user", "user": {"exec": {"command": "plugin"}}}],
        }), "")

    monkeypatch.setattr(credsource, "run_command", fake_run)
    source = credsource.resolve_kubernetes_source(
        _target(_write_target(tmp_path, kubeconfig=kubeconfig)), {},
    )

    assert source.kind == credsource.KUBECONFIG
    assert source.reference == str(kubeconfig.resolve())
    assert source.detail["user_entry"] == "unit-user"
    assert source.detail["authentication_mechanism"] == "exec_plugin"
    assert source.detail["selected_by"] == "target.kubernetes.kubeconfig"


def test_context_and_user_are_attributed_across_merged_files(tmp_path, monkeypatch):
    """kubectl merges its search path, so a split context and user must resolve."""
    credsource, runtime = _modules()
    first, second = tmp_path / "a.yaml", tmp_path / "b.yaml"
    for path in (first, second):
        path.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    payloads = {
        str(first): {"contexts": [], "clusters": [],
                     "users": [{"name": "unit-user", "user": {"token": "REDACTED"}}]},
        str(second): {"contexts": [{"name": "unit-context",
                                    "context": {"cluster": "cluster-a", "user": "unit-user"}}],
                      "clusters": [{"name": "cluster-a", "cluster": {"server": "https://x:6443"}}],
                      "users": []},
    }

    def fake_run(argv, **_kwargs):
        argv = list(argv)
        body = payloads[argv[argv.index("--kubeconfig") + 1]]
        return runtime.CommandResult(tuple(argv), 0, json.dumps(body), "")

    monkeypatch.setattr(credsource, "run_command", fake_run)
    source = credsource.resolve_kubernetes_source(
        _target(_write_target(tmp_path)), {"KUBECONFIG": f"{first}:{second}"},
    )

    assert source.detail["context_from"] == str(second)
    assert source.detail["user_from"] == str(first)
    assert source.detail["authentication_mechanism"] == "bearer_token"
    assert source.detail["merged_contributors"] == [str(first)]
    assert source.shadowed == []


def test_unresolvable_context_blocks_instead_of_defaulting(tmp_path, monkeypatch):
    """A context no readable file defines is unresolved, never a silent default."""
    credsource, runtime = _modules()
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")

    monkeypatch.setattr(
        credsource, "run_command",
        lambda argv, **_kwargs: runtime.CommandResult(
            tuple(argv), 0, json.dumps({"contexts": [], "clusters": [], "users": []}), "",
        ),
    )
    target = _target(_write_target(tmp_path, kubeconfig=kubeconfig))

    with pytest.raises(credsource.CredentialSourceError) as raised:
        credsource.resolve_kubernetes_source(target, {})
    assert "kubeconfig_context_unresolved" in str(raised.value)

    resolved = credsource.resolve_sources(target, {})
    assert resolved["kubernetes"]["kind"] == credsource.UNRESOLVED


def test_the_profile_probe_blanks_the_token_variables_it_would_shadow(tmp_path, monkeypatch):
    """An ambient token must not make an empty stored profile look populated."""
    credsource, runtime = _modules()
    seen: dict[str, dict[str, str] | None] = {}

    def fake_run(argv, *, env=None, **_kwargs):
        seen["env"] = env
        return runtime.CommandResult(tuple(argv), 0, "Logged in to h account u (keyring)", "")

    monkeypatch.setattr(credsource, "run_command", fake_run)
    target = _target(_write_target(tmp_path))

    credsource.resolve_github_source(target, {})
    assert seen["env"] == {"GH_ENTERPRISE_TOKEN": "", "GITHUB_ENTERPRISE_TOKEN": ""}

    credsource.resolve_gitlab_source(target, {})
    assert seen["env"] == {name: "" for name in credsource.GITLAB_ENV}


@pytest.mark.parametrize(
    ("line", "expected"),
    (
        ("  Logged in to github.com account u (keyring)", "keyring"),
        ("  Logged in to github.com account u (secure-storage)", "secure_storage"),
        ("  Logged in to github.com account u (insecure-storage)", "insecure_storage"),
        ("  Logged in to github.com account u (oauth_token)", None),
        ("  Token found in operating system keyring", "keyring"),
        # glab prints this on every success; it must not be read as the store.
        ("  Git operations for gitlab.example.test configured to use https protocol.", None),
    ),
)
def test_the_store_is_read_only_from_the_login_line(line, expected):
    """Store detection is anchored, so unrelated boilerplate cannot match."""
    credsource, _ = _modules()

    assert credsource._named_store(line) == expected


def test_an_unrecognised_store_is_a_profile_not_a_guess(tmp_path, monkeypatch):
    """Exit 0 with no recognisable store resolves, but is not classified."""
    credsource, runtime = _modules()
    monkeypatch.setattr(
        credsource, "run_command",
        lambda argv, **_k: runtime.CommandResult(
            tuple(argv), 0, "Logged in to github.example.test account u", "",
        ),
    )
    source = credsource.resolve_github_source(_target(_write_target(tmp_path)), {})

    assert source.kind == credsource.CLI_PROFILE
    assert source.reference == "gh_profile:github.example.test"


@pytest.mark.parametrize(
    ("environ", "expected"),
    (
        ({}, "kubectl_default"),
        ({"KUBECONFIG": "   "}, "kubectl_default"),
        ({"KUBECONFIG": "/unit/a.yaml"}, "env:KUBECONFIG"),
    ),
)
def test_selected_by_reports_what_actually_chose_the_search_path(environ, expected, tmp_path):
    """A whitespace-only KUBECONFIG is not a selection, and an explicit
    environ is not overridden by the ambient one."""
    credsource, _ = _modules()

    assert credsource._selected_by(_target(_write_target(tmp_path)), environ) == expected


def test_an_unresolved_kubernetes_source_still_names_the_files_searched(tmp_path, monkeypatch):
    """The one failure this field diagnoses must say where it looked."""
    credsource, runtime = _modules()
    first = tmp_path / "a.yaml"
    first.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    monkeypatch.setattr(
        credsource, "run_command",
        lambda argv, **_k: runtime.CommandResult(
            tuple(argv), 0, json.dumps({"contexts": [], "clusters": [], "users": []}), "",
        ),
    )

    resolved = credsource.resolve_sources(
        _target(_write_target(tmp_path)), {"KUBECONFIG": str(first)},
    )

    assert resolved["kubernetes"]["kind"] == credsource.UNRESOLVED
    assert resolved["kubernetes"]["considered"] == [str(first)]
