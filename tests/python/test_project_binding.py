"""Offline tests for project target selection and kubeconfig binding.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, MutableSequence, Sequence
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Optional, TypedDict, Union, Unpack

import pytest
from conftest import import_script_module

if TYPE_CHECKING:
    from lib.python.core.runtime import CommandResult
    from lib.python.core.target import Target


class _CommandTailOptions(TypedDict, total=False):
    """Keyword options accepted by ``run_command_tail`` fixture doubles.

    .. attribute :: timeout
        :type: int

        Maximum command runtime in seconds.

    .. attribute :: max_bytes
        :type: int

        Maximum stdout bytes retained by the tail helper.

    .. attribute :: max_lines
        :type: int

        Maximum stdout lines retained by the tail helper.

    .. attribute :: env
        :type: Optional[Mapping[str, Optional[str]]]

        Child environment overrides; None values remove ambient variables.

    .. attribute :: cwd
        :type: Optional[Union[str, Path]]

        Child working directory; None retains the parent directory.
    """

    timeout: int
    max_bytes: int
    max_lines: int
    env: Optional[Mapping[str, Optional[str]]]
    cwd: Optional[Union[str, Path]]


def _toml_string(value: Union[str, Path]) -> str:
    """Encode a fixture path or name as a TOML basic string.

    :param value: Synthetic path or identifier to quote.
    :returns: JSON-escaped text valid as a TOML basic string.
    """
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
    """Write a target with explicit nonsecret provider authorities.

    :param directory: Destination directory for the target file.
    :param filename: Target file basename in that directory.
    :param github_host: GitHub host recorded in the target.
    :param repository: GitHub repository path recorded in the target.
    :param gitlab_url: GitLab base URL recorded in the target.
    :param context: Kubernetes context pinned by the target.
    :param server: Kubernetes API URL pinned by the target.
    :returns: Path to the written target TOML file.
    """
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
    """Write parseable TOML with a deliberately invalid target shape.

    :param path: Destination for the invalid target.
    :returns: Path to the written fixture file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('schema_version = "not-a-target"\n', encoding="utf-8")
    return path


def _write_kubeconfig(path: Path) -> Path:
    """Write a nonempty kubeconfig placeholder for source selection tests.

    :param path: Destination file whose parent directories are created.
    :returns: Path to the written placeholder.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    return path


def _file_source(path: Union[str, Path]) -> str:
    """Render a file-backed Kubernetes source table for a binding.

    :param path: Kubeconfig path encoded in the source entry.
    :returns: TOML table text for a file source.
    """
    return f'[[kubernetes.sources]]\nkind = "file"\npath = {_toml_string(path)}\n'


def _environment_source(name: str) -> str:
    """Render an environment-backed Kubernetes source table.

    :param name: Variable name containing a kubeconfig path.
    :returns: TOML table text for an environment source.
    """
    return (
        f'[[kubernetes.sources]]\nkind = "environment"\nname = {_toml_string(name)}\n'
    )


def _command_source(*argv: str) -> str:
    """Render a command-backed Kubernetes source table.

    :param *argv: Command argument vector supplied to the helper under test.
    :returns: TOML table text for a command source.

    """
    return (
        "[[kubernetes.sources]]\n"
        'kind = "command"\n'
        f"argv = [{', '.join(_toml_string(part) for part in argv)}]\n"
    )


def _argv_digest(*argv: Union[str, Path]) -> str:
    """Hash the exact command argument vector used in source provenance.

    :param *argv: Command argument vector supplied to the helper under test.
    :returns: SHA-256 hex digest of compact JSON-encoded argv.

    """
    return hashlib.sha256(
        json.dumps([str(part) for part in argv], separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _write_binding(
    path: Path,
    *,
    target: Union[str, Path],
    sources: str,
) -> Path:
    """Write a versioned project binding with selected source entries.

    :param path: Destination binding TOML file.
    :param target: Target path recorded in the binding.
    :param sources: Rendered Kubernetes source tables.
    :returns: Path to the written binding.
    """
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


def _project_binding() -> ModuleType:
    """Load the project binding module from the skill script tree.

    :returns: Imported module under test.
    """
    return import_script_module("lib.python.core.project_binding")


def _access() -> ModuleType:
    """Load access receipt helpers from the skill script tree.

    :returns: Imported access module under test.
    """
    return import_script_module("lib.python.access.access")


def _clear_target_selectors(
    project_binding: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remove target and binding selectors before precedence checks.

    :param project_binding: Module supplying the two selector variable names.
    :param monkeypatch: Pytest environment fixture removing those variables.

    :returns: None; the helper records behavior through its side effects.
    """
    monkeypatch.delenv(project_binding.TARGET_ENV, raising=False)
    monkeypatch.delenv(project_binding.BINDING_ENV, raising=False)


def _reported_target_selection(target: Target) -> str:
    """Read the target-selection source from a dry-run access receipt.

    :param target: Resolved target passed to the access planner.
    :returns: Machine-readable selection reference in the receipt.
    """
    return _access().dry_run_access(target)["target_selection"]


def test_explicit_target_has_highest_precedence_and_reports_cli_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep explicit target selection ahead of environment and default files.

    :param tmp_path: Scratch directory containing target choices and kubeconfig.
    :param monkeypatch: Fixture controlling environment selectors and home lookup.

    :returns: None; assertions in the body validate the regression.
    """
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
    """Require the environment target tier to outrank project and user files.

    :param tmp_path: Scratch targets for the three selection tiers.
    :param monkeypatch: Pytest fixture setting selector, cwd, and HOME.

    :returns: None; assertions in the body validate the regression.
    """
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
    """Fail closed when the selected environment target path is absent.

    :param tmp_path: Scratch absent env path and valid project target.
    :param monkeypatch: Pytest fixture setting cwd and target selector.

    :returns: None; assertions in the body validate the regression.
    """
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
    """Require the cwd project target to outrank the home target.

    :param tmp_path: Scratch project and home target files.
    :param monkeypatch: Pytest fixture setting cwd and HOME.

    :returns: None; assertions in the body validate the regression.
    """
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
    """Select the home target only when earlier target tiers are absent.

    :param tmp_path: Scratch home target and project without a target.
    :param monkeypatch: Pytest fixture setting cwd and HOME.

    :returns: None; assertions in the body validate the regression.
    """
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
    """Reject simultaneous environment target and binding selectors.

    :param tmp_path: Scratch target, kubeconfig, and binding files.
    :param monkeypatch: Pytest fixture setting both selector variables.

    :returns: None; assertions in the body validate the regression.
    """
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
    """Fail closed on an invalid environment or project target.

    :param tmp_path: Scratch invalid higher tier and valid user fallback.
    :param monkeypatch: Pytest fixture selecting cwd, HOME, and optional env file.
    :param invalid_tier: Higher-priority tier deliberately made invalid.

    :returns: None; assertions in the body validate the regression.
    """
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

    with pytest.raises(
        project_binding.TargetError,
        match="^target contains unsupported tables: schema_version$",
    ):
        project_binding.resolve_target(None, None)

    assert user_target.exists()


def test_explicit_binding_arg_resolves_exact_target_context_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolve a selected binding and report its exact target/source provenance.

    :param tmp_path: Scratch target, relative kubeconfig, and binding.
    :param monkeypatch: Pytest fixture clearing ambient selectors.

    :returns: None; assertions in the body validate the regression.
    """
    project_binding = _project_binding()
    cli = import_script_module("lib.python.core.cli")
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
    """Reject a binding that conflicts with target-owned kubeconfig paths.

    :param tmp_path: Scratch target and binding with competing sources.

    :returns: None; assertions in the body validate the regression.
    """
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
    """Select the first present file after absent file and env sources.

    :param tmp_path: Scratch binding and final valid kubeconfig.
    :param monkeypatch: Pytest fixture removing the intermediate env source.

    :returns: None; assertions in the body validate the regression.
    """
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
    """Reject a present empty kubeconfig before trying later valid sources.

    :param tmp_path: Scratch empty and valid kubeconfigs plus ordered binding.

    :returns: None; assertions in the body validate the regression.
    """
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
    """Require bounded command resolution and exact argv provenance.

    :param tmp_path: Scratch binding, generated kubeconfig, and command path.
    :param monkeypatch: Pytest fixture replacing command lookup and execution.

    :returns: None; assertions in the body validate the regression.
    """
    project_binding = _project_binding()
    runtime = import_script_module("lib.python.core.runtime")
    target = _write_target(tmp_path)
    kubeconfig = _write_kubeconfig(tmp_path / "generated" / "kubeconfig")
    executable = tmp_path / "bin" / "unit-kubeconfig-source"
    executable.parent.mkdir()
    which_calls: MutableSequence[str] = []
    binding = _write_binding(
        tmp_path / "binding.toml",
        target=target.name,
        sources=_command_source("unit-kubeconfig-source", "--project", "alpha"),
    )
    calls: MutableSequence[tuple[tuple[str, ...], _CommandTailOptions]] = []

    def fake_which(command: str) -> Optional[str]:
        """Record executable lookup and return its absolute fixture path.

        :param command: Program named by the binding source.
        :returns: Path to the fake executable.
        """
        which_calls.append(command)
        return str(executable)

    def fake_run_command_tail(
        argv: Sequence[str], **kwargs: Unpack[_CommandTailOptions]
    ) -> CommandResult:
        """Return one generated kubeconfig path from the command source.

        :param argv: Absolute executable and arguments being invoked.
        :param **kwargs: Keyword arguments forwarded to the fake command boundary.
        :returns: Successful result containing the kubeconfig path.
        """
        calls.append((tuple(argv), dict(kwargs)))
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
    """Require source provenance digests to cover differing arguments.

    :param tmp_path: Scratch bindings with alpha and beta command arguments.
    :param monkeypatch: Pytest fixture resolving the fake executable.

    :returns: None; assertions in the body validate the regression.
    """
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
    """Report a command source in dry-run without executing its program.

    :param tmp_path: Scratch binding and unexecuted source command path.
    :param monkeypatch: Pytest fixture replacing lookup and execution.

    :returns: None; assertions in the body validate the regression.
    """
    project_binding = _project_binding()
    target = _write_target(tmp_path)
    executable = tmp_path / "scripts" / "project-cluster"
    binding = _write_binding(
        tmp_path / "binding.toml",
        target=target.name,
        sources=_command_source("./scripts/project-cluster"),
    )
    run_calls: MutableSequence[tuple[str, ...]] = []

    def fake_which(command: str) -> Optional[str]:
        """Confirm lookup uses the binding-relative executable path.

        :param command: Absolute program path requested by source resolution.
        :returns: Fixture executable path.
        """
        assert command == str(executable.resolve())
        return str(executable)

    def unexpected_command(
        argv: Sequence[str], **_kwargs: Unpack[_CommandTailOptions]
    ) -> CommandResult:
        """Expose any accidental command execution in dry-run mode.

        :param argv: Command vector that must not be executed in dry-run mode.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: This function never returns because command execution is a failure.
        :raises AssertionError: Dry-run invokes the command source.
        """
        run_calls.append(tuple(argv))
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


def test_dry_run_deferred_command_stops_after_missing_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep a dry-run command source distinct from absent earlier sources.

    :param tmp_path: Scratch binding, deferred command, and fallback file.
    :param monkeypatch: Fixture replacing command lookup and execution.

    :returns: None; assertions in the body validate the regression.
    """
    project_binding = _project_binding()
    target = _write_target(tmp_path)
    executable = tmp_path / "scripts" / "project-cluster"
    fallback = _write_kubeconfig(tmp_path / "fallback" / "kubeconfig")
    binding = _write_binding(
        tmp_path / "binding.toml",
        target=target.name,
        sources=(
            _file_source("missing-file")
            + _command_source("./scripts/project-cluster", "--project", "unit")
            + _file_source("fallback/kubeconfig")
        ),
    )

    def fake_which(command: str) -> Optional[str]:
        """Resolve the deferred command without permitting fallback selection.

        :param command: Command path looked up by the resolver.
        :returns: Fixture executable path.
        """
        assert command == str(executable.resolve())
        return str(executable)

    def unexpected_command(
        argv: Sequence[str], **_kwargs: Unpack[_CommandTailOptions]
    ) -> CommandResult:
        """Expose command execution in dry-run source selection.

        :param argv: Command vector that must remain deferred in dry-run mode.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: This function never returns because command execution is a failure.
        :raises AssertionError: A dry-run command source is executed.
        """
        raise AssertionError("dry run executed deferred kubeconfig source")

    monkeypatch.setattr(project_binding.shutil, "which", fake_which)
    monkeypatch.setattr(project_binding, "run_command_tail", unexpected_command)

    resolved = project_binding.load_project_binding(binding, dry_run=True)

    digest = _argv_digest(executable.resolve(), "--project", "unit")
    assert fallback.exists()
    assert resolved.kubernetes.kubeconfig is None
    assert resolved.kubernetes_source_reference == (
        f"binding:{binding.resolve()}#2:"
        f"command:{executable.resolve()}#argv-sha256:{digest}"
    )


def test_missing_declared_target_and_ambient_kubeconfig_do_not_select_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject ambient kubeconfig files as a substitute for a target.

    :param tmp_path: Scratch kubeconfigs without any declared target.
    :param monkeypatch: Pytest fixture setting cwd, HOME, and KUBECONFIG.

    :returns: None; assertions in the body validate the regression.
    """
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
    """Resolve ambient kubeconfig paths for an explicitly selected target.

    :param tmp_path: Scratch target and ordered kubeconfig source files.
    :param monkeypatch: Fixture selecting KUBECONFIG and isolating the home directory.

    :returns: None; assertions in the body validate the regression.
    """
    project_binding = _project_binding()
    credentials = import_script_module("lib.python.access.kubernetes_credentials")
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
    """Use the user's kubectl default when target and environment omit files.

    :param tmp_path: Scratch target and home kubeconfig.
    :param monkeypatch: Pytest fixture setting HOME and clearing KUBECONFIG.

    :returns: None; assertions in the body validate the regression.
    """
    project_binding = _project_binding()
    credentials = import_script_module("lib.python.access.kubernetes_credentials")
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
