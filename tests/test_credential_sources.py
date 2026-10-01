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


def test_github_profile_is_effective_when_no_environment_token(tmp_path):
    """With no environment token the stored gh profile is the effective source."""
    credsource, _ = _modules()
    source = credsource.resolve_github_source(_target(_write_target(tmp_path)), {})

    assert source.kind == credsource.CLI_PROFILE
    assert source.reference == "gh_profile:github.example.test"
    assert source.shadowed == []


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
