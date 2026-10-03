"""Offline tests for project target selection and kubeconfig binding."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import import_script_module


def _toml_string(value: str | Path) -> str:
    """Return one TOML basic string for a synthetic path or name."""
    return json.dumps(str(value))


def _write_target(
    directory: Path,
    *,
    filename: str = "target.toml",
    github_host: str = "github.example.test",
    repository: str = "unit/repo",
    gitlab_url: str = "https://gitlab.example.test",
    context: str = "unit-context",
    server: str = "https://api.cluster.example.test:6443",
) -> Path:
    """Write a target file with exact nonsecret authorities."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(
        (
            "[github]\n"
            f"host = {_toml_string(github_host)}\n"
            f"repository = {_toml_string(repository)}\n"
            "\n"
            "[gitlab]\n"
            f"url = {_toml_string(gitlab_url)}\n"
            "\n"
            "[kubernetes]\n"
            f"context = {_toml_string(context)}\n"
            f"server = {_toml_string(server)}\n"
        ),
        encoding="utf-8",
    )
    return path


def _write_invalid_target(path: Path) -> Path:
    """Write a syntactically valid TOML file with an invalid target shape."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('schema_version = "not-a-target"\n', encoding="utf-8")
    return path


def _write_kubeconfig(path: Path) -> Path:
    """Write a nonempty synthetic kubeconfig placeholder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    return path


def _file_source(path: str | Path) -> str:
    """Return one file source table for a project binding."""
    return f'[[kubernetes.sources]]\nkind = "file"\npath = {_toml_string(path)}\n'


def _environment_source(name: str) -> str:
    """Return one environment source table for a project binding."""
    return (
        f'[[kubernetes.sources]]\nkind = "environment"\nname = {_toml_string(name)}\n'
    )


def _command_source(*argv: str) -> str:
    """Return one command source table for a project binding."""
    return (
        "[[kubernetes.sources]]\n"
        'kind = "command"\n'
        f"argv = [{', '.join(_toml_string(part) for part in argv)}]\n"
    )


def _argv_digest(*argv: str | Path) -> str:
    """Return the command provenance digest used by project bindings."""
    return hashlib.sha256(
        json.dumps([str(part) for part in argv], separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _write_binding(
    path: Path,
    *,
    target: str | Path,
    sources: str,
) -> Path:
    """Write one schema-versioned project binding file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            'schema_version = "1.0"\n'
            f"target = {_toml_string(target)}\n"
            "\n"
            "[kubernetes]\n"
            f"{sources}"
        ),
        encoding="utf-8",
    )
    return path


def _project_binding() -> Any:
    """Import the module under test from the skill script tree."""
    return import_script_module("core.project_binding")


def _access() -> Any:
    """Import access helpers that render target_selection receipts."""
    return import_script_module("core.access")


def _clear_target_selectors(
    project_binding: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remove selector env vars so each test declares its selection inputs."""
    monkeypatch.delenv(project_binding.TARGET_ENV, raising=False)
    monkeypatch.delenv(project_binding.BINDING_ENV, raising=False)


def _reported_target_selection(target: Any) -> str:
    """Return the machine-readable target_selection a dry-run receipt reports."""
    return _access().dry_run_access(target)["target_selection"]


def test_explicit_target_has_highest_precedence_and_reports_cli_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tier 1, --target, wins over env, project, and user targets."""
    project_binding = _project_binding()
    explicit = _write_target(tmp_path / "explicit", context="tier-1")
    env_target = _write_target(tmp_path / "env", context="tier-2")
    project = tmp_path / "project"
    project_target = _write_target(
        project / ".ci-skills", filename="target.toml", context="tier-3"
    )
    home_target = _write_target(
        tmp_path / "home" / ".ci-skills", filename="target.toml", context="tier-4"
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv(project_binding.TARGET_ENV, str(env_target))
    monkeypatch.setenv("HOME", str(home_target.parents[1]))

    resolved = project_binding.resolve_target(str(explicit), None)

    assert project_target.exists()
    assert resolved.kubernetes.context == "tier-1"
    assert resolved.target_reference == f"cli:{explicit.resolve()}"
    assert _reported_target_selection(resolved) == f"cli:{explicit.resolve()}"


def test_ci_skills_target_precedes_project_and_user_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tier 2, CI_SKILLS_TARGET, wins over cwd and home defaults."""
    project_binding = _project_binding()
    _clear_target_selectors(project_binding, monkeypatch)
    env_target = _write_target(tmp_path / "env", context="tier-2")
    project = tmp_path / "project"
    _write_target(project / ".ci-skills", filename="target.toml", context="tier-3")
    _write_target(
        tmp_path / "home" / ".ci-skills", filename="target.toml", context="tier-4"
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv(project_binding.TARGET_ENV, str(env_target))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    resolved = project_binding.resolve_target(None, None)

    expected = f"env:{project_binding.TARGET_ENV} -> file:{env_target.resolve()}"
    assert resolved.kubernetes.context == "tier-2"
    assert resolved.target_reference == expected
    assert _reported_target_selection(resolved) == expected


def test_declared_environment_target_missing_does_not_use_project_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_binding = _project_binding()
    _clear_target_selectors(project_binding, monkeypatch)
    project = tmp_path / "project"
    _write_target(project / ".ci-skills", filename="target.toml")
    monkeypatch.chdir(project)
    monkeypatch.setenv(project_binding.TARGET_ENV, str(tmp_path / "missing.toml"))

    with pytest.raises(project_binding.TargetError, match="target_file_missing"):
        project_binding.resolve_target(None, None)


def test_project_target_precedes_user_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tier 3, ./.ci-skills/target.toml, wins over the home target."""
    project_binding = _project_binding()
    _clear_target_selectors(project_binding, monkeypatch)
    project = tmp_path / "project"
    project_target = _write_target(
        project / ".ci-skills", filename="target.toml", context="tier-3"
    )
    _write_target(
        tmp_path / "home" / ".ci-skills", filename="target.toml", context="tier-4"
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    resolved = project_binding.resolve_target(None, None)

    expected = f"project:{project_target.resolve()}"
    assert resolved.kubernetes.context == "tier-3"
    assert resolved.target_reference == expected
    assert _reported_target_selection(resolved) == expected


def test_user_target_is_last_declared_target_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tier 4, ~/.ci-skills/target.toml, is used only when higher tiers are absent."""
    project_binding = _project_binding()
    _clear_target_selectors(project_binding, monkeypatch)
    project = tmp_path / "project-without-target"
    project.mkdir()
    home_target = _write_target(
        tmp_path / "home" / ".ci-skills", filename="target.toml", context="tier-4"
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    resolved = project_binding.resolve_target(None, None)

    expected = f"user:{home_target.resolve()}"
    assert resolved.kubernetes.context == "tier-4"
    assert resolved.target_reference == expected
    assert _reported_target_selection(resolved) == expected


def test_environment_selector_conflict_blocks_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two env selectors cannot silently race or choose one side."""
    project_binding = _project_binding()
    target = _write_target(tmp_path / "target")
    binding = _write_binding(
        tmp_path / "binding.toml",
        target=target,
        sources=_file_source(_write_kubeconfig(tmp_path / "kubeconfig")),
    )
    monkeypatch.setenv(project_binding.TARGET_ENV, str(target))
    monkeypatch.setenv(project_binding.BINDING_ENV, str(binding))

    with pytest.raises(
        project_binding.TargetError, match="environment_selector_conflict"
    ):
        project_binding.resolve_target(None, None)


@pytest.mark.parametrize("invalid_tier", ("environment", "project"))
def test_invalid_higher_tier_blocks_lower_target_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_tier: str
) -> None:
    """A present but invalid higher tier fails closed instead of falling back."""
    project_binding = _project_binding()
    _clear_target_selectors(project_binding, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    user_target = _write_target(
        tmp_path / "home" / ".ci-skills", filename="target.toml", context="tier-4"
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    if invalid_tier == "environment":
        invalid = _write_invalid_target(tmp_path / "invalid-env-target.toml")
        _write_target(project / ".ci-skills", filename="target.toml", context="tier-3")
        monkeypatch.setenv(project_binding.TARGET_ENV, str(invalid))
    else:
        _write_invalid_target(project / ".ci-skills" / "target.toml")

    with pytest.raises(project_binding.TargetError, match="target must contain"):
        project_binding.resolve_target(None, None)

    assert user_target.exists()


def test_explicit_binding_arg_resolves_exact_target_context_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit --binding path selects the exact target and source receipt."""
    project_binding = _project_binding()
    cli = import_script_module("core.cli")
    _clear_target_selectors(project_binding, monkeypatch)
    target = _write_target(
        tmp_path,
        github_host="github.enterprise.example.test",
        repository="team/project",
        gitlab_url="https://gitlab.enterprise.example.test",
        context="production-admin",
        server="https://api.production.example.test:6443",
    )
    kubeconfig = _write_kubeconfig(tmp_path / "runtime" / "kubeconfig")
    binding = _write_binding(
        tmp_path / "project-binding.toml",
        target=target.name,
        sources=_file_source("runtime/kubeconfig"),
    )
    args = cli.parser("unit").parse_args(["--binding", str(binding)])

    resolved = project_binding.resolve_target(args.target, args.binding)

    expected_selection = f"binding:{binding.resolve()}"
    assert resolved.github.host == "github.enterprise.example.test"
    assert resolved.github.repository == "team/project"
    assert resolved.gitlab.url == "https://gitlab.enterprise.example.test"
    assert resolved.kubernetes.context == "production-admin"
    assert resolved.kubernetes.server == "https://api.production.example.test:6443"
    assert resolved.kubernetes.kubeconfig == kubeconfig.resolve()
    assert resolved.target_reference == expected_selection
    assert _reported_target_selection(resolved) == expected_selection
    assert resolved.kubernetes_source_reference == (
        f"binding:{binding.resolve()}#1:file:{kubeconfig.resolve()}"
    )


def test_binding_rejects_target_with_declared_kubeconfig_path(tmp_path: Path) -> None:
    """A binding must not mix its selected source with a target search path."""
    project_binding = _project_binding()
    target = _write_target(tmp_path)
    target.write_text(
        target.read_text(encoding="utf-8")
        + f"kubeconfigs = [{_toml_string(tmp_path / 'declared.yaml')}]\n",
        encoding="utf-8",
    )
    binding = _write_binding(
        tmp_path / "binding.toml",
        target=target.name,
        sources=_file_source(_write_kubeconfig(tmp_path / "binding.yaml")),
    )

    with pytest.raises(
        project_binding.TargetError, match="binding_target_kubeconfig_conflict"
    ):
        project_binding.load_project_binding(binding)


def test_binding_sources_fall_through_only_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordered sources advance past absent entries and select the first present one."""
    project_binding = _project_binding()
    monkeypatch.delenv("UNIT_ABSENT_KUBECONFIG", raising=False)
    target = _write_target(tmp_path)
    present_kubeconfig = _write_kubeconfig(tmp_path / "present" / "kubeconfig")
    binding = _write_binding(
        tmp_path / "binding.toml",
        target=target.name,
        sources=(
            _file_source("missing-file")
            + _environment_source("UNIT_ABSENT_KUBECONFIG")
            + _file_source("present/kubeconfig")
        ),
    )

    resolved = project_binding.load_project_binding(binding)

    assert resolved.kubernetes.kubeconfig == present_kubeconfig.resolve()
    assert resolved.target_reference == f"binding:{binding.resolve()}"
    assert resolved.kubernetes_source_reference == (
        f"binding:{binding.resolve()}#3:file:{present_kubeconfig.resolve()}"
    )


def test_present_invalid_source_blocks_later_fallback(tmp_path: Path) -> None:
    """A present-but-invalid source fails closed instead of trying another source."""
    project_binding = _project_binding()
    target = _write_target(tmp_path)
    empty_kubeconfig = tmp_path / "empty-kubeconfig"
    empty_kubeconfig.touch()
    _write_kubeconfig(tmp_path / "later" / "valid-kubeconfig")
    binding = _write_binding(
        tmp_path / "binding.toml",
        target=target.name,
        sources=(
            _file_source(empty_kubeconfig.name) + _file_source("later/valid-kubeconfig")
        ),
    )

    with pytest.raises(
        project_binding.TargetError, match="binding_kubeconfig_unavailable"
    ):
        project_binding.load_project_binding(binding)


def test_command_source_returns_absolute_path_and_records_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command source must run from the binding directory and return one path."""
    project_binding = _project_binding()
    runtime = import_script_module("core.runtime")
    target = _write_target(tmp_path)
    kubeconfig = _write_kubeconfig(tmp_path / "generated" / "kubeconfig")
    executable = tmp_path / "bin" / "unit-kubeconfig-source"
    executable.parent.mkdir()
    which_calls: list[str] = []
    binding = _write_binding(
        tmp_path / "binding.toml",
        target=target.name,
        sources=_command_source("unit-kubeconfig-source", "--project", "alpha"),
    )
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def fake_which(command: str) -> str | None:
        which_calls.append(command)
        return str(executable)

    def fake_run_command_tail(argv: list[str], **kwargs: Any) -> Any:
        calls.append((tuple(argv), kwargs))
        return runtime.CommandResult(tuple(argv), 0, f"{kubeconfig}\n", "")

    monkeypatch.setattr(project_binding.shutil, "which", fake_which)
    monkeypatch.setattr(project_binding, "run_command_tail", fake_run_command_tail)

    resolved = project_binding.load_project_binding(binding)

    digest = _argv_digest(executable.resolve(), "--project", "alpha")
    assert which_calls == ["unit-kubeconfig-source"]
    assert calls == [
        (
            (str(executable.resolve()), "--project", "alpha"),
            {"timeout": 30, "max_bytes": 4096, "max_lines": 2, "cwd": tmp_path},
        )
    ]
    assert resolved.kubernetes.kubeconfig == kubeconfig.resolve()
    assert resolved.target_reference == f"binding:{binding.resolve()}"
    assert resolved.kubernetes_source_reference == (
        f"binding:{binding.resolve()}#1:"
        f"command:{executable.resolve()}#argv-sha256:{digest}"
        f" -> file:{kubeconfig.resolve()}"
    )


def test_command_source_digest_changes_when_argv_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command source digest covers the full argument vector."""
    project_binding = _project_binding()
    target = _write_target(tmp_path)
    executable = tmp_path / "bin" / "unit-kubeconfig-source"
    executable.parent.mkdir()
    alpha = _write_binding(
        tmp_path / "alpha.toml",
        target=target.name,
        sources=_command_source("unit-kubeconfig-source", "--project", "alpha"),
    )
    beta = _write_binding(
        tmp_path / "beta.toml",
        target=target.name,
        sources=_command_source("unit-kubeconfig-source", "--project", "beta"),
    )

    monkeypatch.setattr(
        project_binding.shutil,
        "which",
        lambda command: (
            str(executable) if command == "unit-kubeconfig-source" else None
        ),
    )

    alpha_resolved = project_binding.load_project_binding(alpha, dry_run=True)
    beta_resolved = project_binding.load_project_binding(beta, dry_run=True)

    alpha_digest = _argv_digest(executable.resolve(), "--project", "alpha")
    beta_digest = _argv_digest(executable.resolve(), "--project", "beta")
    assert alpha_digest != beta_digest
    assert f"#argv-sha256:{alpha_digest}" in alpha_resolved.kubernetes_source_reference
    assert f"#argv-sha256:{beta_digest}" in beta_resolved.kubernetes_source_reference


def test_dry_run_never_executes_project_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dry-run reports the command source without executing it."""
    project_binding = _project_binding()
    target = _write_target(tmp_path)
    executable = tmp_path / "scripts" / "project-cluster"
    binding = _write_binding(
        tmp_path / "binding.toml",
        target=target.name,
        sources=_command_source("./scripts/project-cluster"),
    )
    run_calls: list[tuple[Any, ...]] = []

    def fake_which(command: str) -> str | None:
        assert command == str(executable.resolve())
        return str(executable)

    def unexpected_command(*_args: Any, **_kwargs: Any) -> Any:
        run_calls.append(_args)
        raise AssertionError("dry run executed credential resolver")

    monkeypatch.setattr(project_binding.shutil, "which", fake_which)
    monkeypatch.setattr(project_binding, "run_command_tail", unexpected_command)

    resolved = project_binding.load_project_binding(binding, dry_run=True)

    digest = _argv_digest(executable.resolve())
    assert run_calls == []
    assert resolved.kubernetes.kubeconfig is None
    assert resolved.target_reference == f"binding:{binding.resolve()}"
    assert resolved.kubernetes_source_reference == (
        f"binding:{binding.resolve()}#1:"
        f"command:{executable.resolve()}#argv-sha256:{digest}"
    )


def test_missing_declared_target_and_ambient_kubeconfig_do_not_select_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ambient kubeconfig files do not create an implicit fifth target tier."""
    project_binding = _project_binding()
    _clear_target_selectors(project_binding, monkeypatch)
    home = tmp_path / "home-without-ci-skills"
    implicit_home_config = _write_kubeconfig(home / ".kube" / "config")
    env_config = _write_kubeconfig(tmp_path / "env-kubeconfig")
    project = tmp_path / "project-without-target"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KUBECONFIG", str(env_config))

    with pytest.raises(project_binding.TargetError, match="target_missing"):
        project_binding.resolve_target(None, None)

    assert implicit_home_config.exists()
    assert env_config.exists()


def test_explicit_target_without_kubeconfig_uses_environment_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A target pins the context/server while KUBECONFIG supplies the files."""
    project_binding = _project_binding()
    credentials = import_script_module("core.credentials")
    target = _write_target(tmp_path)
    config = _write_kubeconfig(tmp_path / "global")
    monkeypatch.setenv("KUBECONFIG", str(config))

    source, paths = credentials._kubernetes_source(
        project_binding.resolve_target(str(target), None)
    )
    assert source.reference == "env:KUBECONFIG"
    assert paths == (config.resolve(),)


def test_target_without_kubeconfig_uses_user_kubectl_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_binding = _project_binding()
    credentials = import_script_module("core.credentials")
    target = _write_target(tmp_path)
    user_home = tmp_path / "user"
    config = _write_kubeconfig(user_home / ".kube" / "config")
    monkeypatch.delenv("KUBECONFIG", raising=False)
    monkeypatch.setenv("HOME", str(user_home))

    source, paths = credentials._kubernetes_source(
        project_binding.resolve_target(str(target), None)
    )
    assert source.reference == f"kubectl-default:{config.resolve()}"
    assert paths == (config.resolve(),)
