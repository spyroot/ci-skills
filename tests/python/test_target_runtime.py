"""Focused tests for target parsing and command runtime helpers.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypeAlias, Union

import pytest
from conftest import REPO_ROOT, import_script_module

HarborTargetCase: TypeAlias = tuple[
    Path, tuple[Path, ...], Mapping[Path, tuple[bytes, int]]
]


@pytest.fixture(params=("combined", "pair"))
def harbor_target_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> HarborTargetCase:
    """Create combined or paired Harbor credential files beside a target.

    :param tmp_path: Scratch target, credential, and unrelated cwd directories.
    :param monkeypatch: Pytest fixture moving cwd away from the target.
    :param request: Parametrized fixture case selecting combined or paired form.
    :returns: Target path, selected credential paths, and original file state.
    """
    target_parent = tmp_path / "operator-config"
    target_parent.mkdir()
    combined = target_parent / "harbor.credential"
    username = target_parent / "harbor.username"
    password = target_parent / "harbor.password"
    combined.write_text("unit-robot:unit-secret\n", encoding="utf-8")
    username.write_text("unit-robot\n", encoding="utf-8")
    password.write_text("unit-secret\n", encoding="utf-8")
    selected = (combined,)
    declaration = 'credential_file = "harbor.credential"\n'
    if request.param == "pair":
        selected = (username, password)
        declaration = (
            'username_file = "harbor.username"\npassword_file = "harbor.password"\n'
        )
    before = {path: (path.read_bytes(), path.stat().st_mode) for path in selected}
    target_path = target_parent / "target.toml"
    target_path.write_text(
        (
            "[harbor]\n"
            'url = "https://harbor.example.test"\n'
            'project = "unit"\n'
            f"{declaration}"
        ),
        encoding="utf-8",
    )
    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)
    return target_path, selected, before


def test_load_target_accepts_exact_nonsecret_authorities(target_file: Path) -> None:
    """Read all declared provider hosts and cluster context without inline secrets.

    :param target_file: Fixture target with GitHub, GitLab, and Kubernetes sections.

    :returns: None; assertions in the body validate the regression.
    """
    target = import_script_module("lib.python.core.target").load_target(target_file)

    assert target.github.host == "github.example.test"
    assert target.github.repository == "unit/repo"
    assert target.gitlab.url == "https://gitlab.example.test"
    assert target.gitlab.host == "gitlab.example.test"
    assert target.kubernetes.context == "unit-context"
    assert target.kubernetes.server == "https://api.cluster.example.test:6443"
    assert target.kubernetes.kubeconfig is None


@pytest.mark.parametrize(
    ("body", "reason"),
    (
        ("", "target is missing required authority tables: gitlab"),
        ("[githb]\n", "target contains unsupported tables: githb"),
        (
            '[gitlab]\nurl = "https://gitlab.example.test"\n[extra]\n',
            "target contains unsupported tables: extra",
        ),
    ),
)
def test_load_target_distinguishes_missing_from_unsupported_authorities(
    tmp_path: Path, body: str, reason: str
) -> None:
    """Name the actual missing or unsupported table without conflating them.

    :param tmp_path: Scratch directory containing the malformed target.
    :param body: TOML with a missing required table or an unsupported table.
    :param reason: Exact corrective diagnostic expected by the calling agent.
    :returns: None; assertions verify the public target-loading error.
    """
    target_mod = import_script_module("lib.python.core.target")
    target_path = tmp_path / "target.toml"
    target_path.write_text(body, encoding="utf-8")

    with pytest.raises(target_mod.TargetError) as error:
        target_mod.load_target(target_path, required_surfaces=("gitlab",))

    assert str(error.value) == reason


@pytest.mark.parametrize(
    ("table", "key"),
    (
        ("github", "host"),
        ("github", "repository"),
        ("gitlab", "url"),
        ("harbor", "url"),
        ("harbor", "project"),
        ("kubernetes", "context"),
        ("kubernetes", "server"),
        ("kubernetes.node_diagnostics", "node"),
        ("kubernetes.node_diagnostics.cilium", "namespace"),
        ("kubernetes.node_diagnostics.journal", "directory"),
    ),
)
def test_load_target_qualifies_required_string_diagnostics(
    target_file: Path, table: str, key: str
) -> None:
    """Identify a missing string by its provider and complete nested table path.

    :param target_file: Existing target fixture with valid provider settings.
    :param table: Provider or nested diagnostic table whose required field is removed.
    :param key: Required field missing from that table.
    :returns: None; assertions identify the exact field the agent must supply.
    """
    target_mod = import_script_module("lib.python.core.target")
    body = target_file.read_text(encoding="utf-8") + (
        '\n[harbor]\nurl = "https://harbor.example.test"\nproject = "unit"\n'
        'credential_file = "harbor.credential"\n'
        '\n[kubernetes.node_diagnostics]\nnode = "worker-a"\n'
        '\n[kubernetes.node_diagnostics.cilium]\nnamespace = "cilium"\n'
        'selector = "app=cilium"\ncontainer = "agent"\n'
        '\n[kubernetes.node_diagnostics.journal]\nnamespace = "diagnostics"\n'
        'selector = "app=journal"\ncontainer = "reader"\n'
        'directory = "/host-journal"\nhost_path = "/var/log/journal"\n'
    )
    current_table = ""
    lines = []
    for line in body.splitlines():
        if line.startswith("["):
            current_table = line[1:-1]
        if current_table != table or not line.startswith(f"{key} ="):
            lines.append(line)
    target_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(target_mod.TargetError) as error:
        target_mod.load_target(target_file, required_surfaces=(table.split(".")[0],))

    assert str(error.value) == f"{table}.{key} must be a nonempty string"


@pytest.mark.parametrize("declaration", ("relative", "absolute", "tilde"))
def test_load_target_resolves_token_files_from_declared_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declaration: str
) -> None:
    """Resolve relative, absolute, and tilde token paths independent of cwd.

    :param tmp_path: Scratch target, token files, home, and unrelated cwd.
    :param monkeypatch: Pytest fixture setting HOME and cwd when needed.
    :param declaration: Path form written into the target.

    :returns: None; assertions in the body validate the regression.
    """
    target_parent = tmp_path / "operator-config"
    target_parent.mkdir()
    github_token_file = target_parent / "github.token"
    gitlab_token_file = target_parent / "gitlab.token"
    github_token_file.write_text("github-unit-token\n", encoding="utf-8")
    gitlab_token_file.write_text("gitlab-unit-token\n", encoding="utf-8")
    github_declaration = str(github_token_file)
    gitlab_declaration = str(gitlab_token_file)
    if declaration == "relative":
        github_declaration = github_token_file.name
        gitlab_declaration = gitlab_token_file.name
    elif declaration == "tilde":
        home = tmp_path / "home"
        home.mkdir()
        github_token_file = home / github_token_file.name
        gitlab_token_file = home / gitlab_token_file.name
        github_token_file.write_text("github-unit-token\n", encoding="utf-8")
        gitlab_token_file.write_text("gitlab-unit-token\n", encoding="utf-8")
        monkeypatch.setenv("HOME", str(home))
        github_declaration = f"~/{github_token_file.name}"
        gitlab_declaration = f"~/{gitlab_token_file.name}"
    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)
    target_path = target_parent / "target.toml"
    target_path.write_text(
        (
            "[github]\n"
            'host = "github.example.test"\n'
            'repository = "unit/repo"\n'
            f'token_file = "{github_declaration}"\n'
            "\n"
            "[gitlab]\n"
            'url = "https://gitlab.example.test"\n'
            f'token_file = "{gitlab_declaration}"\n'
            "\n"
            "[kubernetes]\n"
            'context = "unit-context"\n'
            'server = "https://api.cluster.example.test:6443"\n'
        ),
        encoding="utf-8",
    )

    target = import_script_module("lib.python.core.target").load_target(target_path)

    assert target.github.token_file == github_token_file.resolve()
    assert target.gitlab.token_file == gitlab_token_file.resolve()
    assert not hasattr(target.gitlab, "token")


def test_harbor_credentials_resolve_beside_target_without_altering_files(
    harbor_target_case: HarborTargetCase,
) -> None:
    """Read combined or paired Harbor files without changing their bytes/mode.

    :param harbor_target_case: Target path, selected paths, and original file state.

    :returns: None; assertions in the body validate the regression.
    """
    target_path, selected, before = harbor_target_case
    target_mod = import_script_module("lib.python.core.target")
    credentials = import_script_module("lib.python.access.harbor_credentials")

    target = target_mod.load_target(target_path, required_surfaces=("harbor",))
    credential = credentials.resolve_harbor_credential(target.harbor)

    assert credential.username == "unit-robot"
    assert credential.password == "unit-secret"
    assert credential.source == " -> ".join(
        f"file:{path.resolve()}" for path in selected
    )
    assert {
        path: (path.read_bytes(), path.stat().st_mode) for path in selected
    } == before


def test_load_target_rejects_relative_token_file_resolved_inside_installed_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject a target-relative token path inside the installed skill.

    :param tmp_path: Scratch installed-skill and target directories.
    :param monkeypatch: Pytest fixture selecting the synthetic skill root.

    :returns: None; assertions in the body validate the regression.
    """
    target_mod = import_script_module("lib.python.core.target")
    skill_root = tmp_path / "installed-skill"
    skill_root.mkdir()
    (skill_root / "credential").write_text("unit-token\n", encoding="utf-8")
    target_parent = tmp_path / "operator-config"
    target_parent.mkdir()
    target_path = target_parent / "target.toml"
    target_path.write_text(
        (
            "[github]\n"
            'host = "github.example.test"\n'
            'repository = "unit/repo"\n'
            'token_file = "../installed-skill/credential"\n'
            "\n"
            "[gitlab]\n"
            'url = "https://gitlab.example.test"\n'
            "\n"
            "[kubernetes]\n"
            'context = "unit-context"\n'
            'server = "https://api.cluster.example.test:6443"\n'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(target_mod, "SKILL_ROOT", skill_root)

    with pytest.raises(target_mod.TargetError, match="stored outside"):
        target_mod.load_target(target_path)


def test_skill_root_is_the_folder_that_holds_skill_md() -> None:
    """core.paths declares the skill root once; the boundary tests below rely on it.
    :returns: None; assertions in the body validate the regression.
    """
    skill_root = import_script_module("lib.python.core.paths").SKILL_ROOT

    assert skill_root == REPO_ROOT / "ci-skills"
    assert (skill_root / "SKILL.md").is_file()


def test_ambient_kubeconfig_cannot_point_into_installed_skill(
    monkeypatch: pytest.MonkeyPatch, target_file: Path
) -> None:
    """Reject KUBECONFIG that points inside the installed skill tree.

    :param monkeypatch: Pytest fixture setting the ambient kubeconfig path.
    :param target_file: Fixture target selecting cluster context/server.

    :returns: None; assertions in the body validate the regression.
    """
    target_mod = import_script_module("lib.python.core.target")
    credentials = import_script_module("lib.python.access.kubernetes_credentials")
    skill_root = import_script_module("lib.python.core.paths").SKILL_ROOT
    monkeypatch.setenv("KUBECONFIG", str(skill_root / "inside.kubeconfig"))

    with pytest.raises(target_mod.TargetError, match="stored outside"):
        credentials._kubernetes_source(target_mod.load_target(target_file))


def test_default_kubeconfig_cannot_point_into_installed_skill(
    monkeypatch: pytest.MonkeyPatch, target_file: Path
) -> None:
    """Reject kubectl's home default when it resolves inside installed skill.

    :param monkeypatch: Pytest fixture setting HOME and clearing KUBECONFIG.
    :param target_file: Fixture target selecting cluster context/server.

    :returns: None; assertions in the body validate the regression.
    """
    target_mod = import_script_module("lib.python.core.target")
    credentials = import_script_module("lib.python.access.kubernetes_credentials")
    skill_root = import_script_module("lib.python.core.paths").SKILL_ROOT
    monkeypatch.delenv("KUBECONFIG", raising=False)
    monkeypatch.setenv("HOME", str(skill_root))

    with pytest.raises(target_mod.TargetError, match="stored outside"):
        credentials._kubernetes_source(target_mod.load_target(target_file))


@pytest.mark.parametrize(
    ("content", "message"),
    (
        (
            (
                "[github]\n"
                'host = "github.example.test"\n'
                'repository = "unit/repo"\n'
                'token = "secret-value"\n'
                "\n"
                "[gitlab]\n"
                'url = "https://gitlab.example.test"\n'
                "\n"
                "[kubernetes]\n"
                'context = "unit-context"\n'
                'server = "https://api.cluster.example.test:6443"\n'
            ),
            "unsupported fields",
        ),
        (
            (
                "[github]\n"
                'host = "github"\n'
                'repository = "unit/repo"\n'
                "\n"
                "[gitlab]\n"
                'url = "https://gitlab.example.test"\n'
                "\n"
                "[kubernetes]\n"
                'context = "unit-context"\n'
                'server = "https://api.cluster.example.test:6443"\n'
            ),
            "full hostname",
        ),
        (
            (
                "[github]\n"
                'host = "github.example.test"\n'
                'repository = "unit/repo"\n'
                "\n"
                "[gitlab]\n"
                'url = "https://["\n'
                "\n"
                "[kubernetes]\n"
                'context = "unit-context"\n'
                'server = "https://api.cluster.example.test:6443"\n'
            ),
            "invalid authority",
        ),
        (
            (
                "[github]\n"
                'host = "github.example.test"\n'
                'repository = "unit/repo"\n'
                "\n"
                "[gitlab]\n"
                'url = "https://token@gitlab.example.test"\n'
                "\n"
                "[kubernetes]\n"
                'context = "unit-context"\n'
                'server = "https://api.cluster.example.test:6443"\n'
            ),
            "HTTPS origin",
        ),
        (
            (
                "[github]\n"
                'host = "github.example.test"\n'
                'repository = "unit/repo"\n'
                "\n"
                "[gitlab]\n"
                'url = "https://gitlab.example.test"\n'
                'token = "inline-token-value"\n'
                "\n"
                "[kubernetes]\n"
                'context = "unit-context"\n'
                'server = "https://api.cluster.example.test:6443"\n'
            ),
            "unsupported fields",
        ),
    ),
)
def test_load_target_rejects_ambiguous_or_secret_authorities(
    tmp_path: Path, content: str, message: str
) -> None:
    """Reject inline secrets and malformed host/origin authorities.

    :param tmp_path: Directory receiving the invalid target TOML.
    :param content: Target text for the selected failure case.
    :param message: Expected validation error fragment.

    :returns: None; assertions in the body validate the regression.
    """
    path = tmp_path / "target.toml"
    path.write_text(content, encoding="utf-8")
    target_mod = import_script_module("lib.python.core.target")

    with pytest.raises(target_mod.TargetError, match=message):
        target_mod.load_target(path)


def test_runtime_sanitizes_secret_like_output_and_bounds_text() -> None:
    """External command output is redacted before it enters diagnostics.
    :returns: None; assertions in the body validate the regression.
    """
    runtime = import_script_module("lib.python.core.runtime")
    private_key = "-----BEGIN PRIVATE KEY-----\nabc123\n-----END PRIVATE KEY-----"
    text = (
        "Authorization: Bearer abc.def\n"
        "api_key=abc123\n"
        "password: hidden\n"
        f"{private_key}\n"
        "x" * 200
    )

    sanitized = runtime.sanitize(text, limit=120)

    assert "abc.def" not in sanitized
    assert "abc123" not in sanitized
    assert "hidden" not in sanitized
    assert "[REDACTED]" in sanitized
    assert len(sanitized) == 120


def test_run_command_uses_argument_vector_and_noninteractive_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass argv literally and disable prompts without a shell.

    :param monkeypatch: Pytest fixture replacing subprocess execution.

    :returns: None; assertions in the body validate the regression.
    """
    runtime = import_script_module("lib.python.core.runtime")
    captured = {}

    def fake_run(
        argv: Sequence[str],
        **kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> subprocess.CompletedProcess[str]:
        """Capture subprocess argv/options and return a successful process.

        :param argv: Literal command vector passed by the runtime.
        :param **kwargs: Keyword arguments forwarded to the fake command boundary.
        :returns: Completed process carrying the fixture success output.

        """
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)

    result = runtime.run_command(["tool", "literal $(not-run)"])

    assert result.returncode == 0
    assert captured["argv"] == ("tool", "literal $(not-run)")
    assert captured["kwargs"].get("shell") is not True
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
    assert captured["kwargs"]["env"]["GH_PROMPT_DISABLED"] == "1"
    assert captured["kwargs"]["env"]["GLAB_NO_PROMPT"] == "1"


def test_run_command_tail_keeps_only_bounded_stdout_tail() -> None:
    """Trace collection keeps recent lines without retaining full command output.
    :returns: None; assertions in the body validate the regression.
    """
    runtime = import_script_module("lib.python.core.runtime")

    result = runtime.run_command_tail(
        [
            sys.executable,
            "-c",
            "for index in range(12): print(f'line {index}')",
        ],
        max_lines=5,
        max_bytes=1000,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "line 7",
        "line 8",
        "line 9",
        "line 10",
        "line 11",
    ]


def test_run_command_tail_rejects_unbounded_limits() -> None:
    """Tail limits must stay positive so a trace cannot request full output.
    :returns: None; assertions in the body validate the regression.
    """
    runtime = import_script_module("lib.python.core.runtime")

    with pytest.raises(ValueError, match="tail limits"):
        runtime.run_command_tail(["tool"], max_lines=0)


def test_run_command_tail_times_out_after_child_closes_pipes_then_lingers() -> None:
    """A child that closes output pipes must still be reaped by the deadline.
    :returns: None; assertions in the body validate the regression.
    """
    runtime = import_script_module("lib.python.core.runtime")
    start = time.monotonic()

    result = runtime.run_command_tail(
        [
            sys.executable,
            "-c",
            "import os, time\nos.close(1)\nos.close(2)\ntime.sleep(2)\n",
        ],
        timeout=0.2,
        max_lines=5,
        max_bytes=1000,
    )

    assert result.returncode == 124
    assert time.monotonic() - start < 1.0


@pytest.mark.parametrize(
    ("returncode", "stderr", "expected"),
    (
        (127, "", "missing_tool"),
        (124, "", "timeout"),
        (1, "HTTP 401 unauthorized", "authentication"),
        (1, "HTTP 403 forbidden", "authorization"),
        (1, "could not resolve host", "transport"),
        (1, "other failure", "command_failed"),
    ),
)
def test_error_class_maps_command_failures(
    returncode: int, stderr: str, expected: str
) -> None:
    """Map native exit and stderr patterns to stable failure classes.

    :param returncode: Process status for the selected failure case.
    :param stderr: Diagnostic text used by the classifier.
    :param expected: Machine-readable failure class asserted by the test.

    :returns: None; assertions in the body validate the regression.
    """
    runtime = import_script_module("lib.python.core.runtime")
    result = runtime.CommandResult(("tool",), returncode, "", stderr)

    assert runtime.error_class(result) == expected
