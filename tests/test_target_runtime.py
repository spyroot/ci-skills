"""Focused tests for target parsing and command runtime helpers."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest
from conftest import SCRIPT_ROOT, import_script_module


def _clear_credential_env(monkeypatch):
    for name in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "GITLAB_TOKEN",
        "GITLAB_ACCESS_TOKEN",
        "OAUTH_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


def test_load_target_accepts_exact_nonsecret_authorities(target_file):
    """A target file supplies all live authorities without secret material."""
    target = import_script_module("core.target").load_target(target_file)

    assert target.github.host == "github.example.test"
    assert target.github.repository == "unit/repo"
    assert target.gitlab.url == "https://gitlab.example.test"
    assert target.gitlab.host == "gitlab.example.test"
    assert target.kubernetes.context == "unit-context"
    assert target.kubernetes.server == "https://api.cluster.example.test:6443"
    assert target.kubernetes.kubeconfig is None


def test_load_target_accepts_optional_gitlab_token_file_path(tmp_path):
    """The target may point at a host-local token file without storing a token."""
    token_file = tmp_path / ".config" / "ci-skills" / "gitlab.example.test.token"
    token_file.parent.mkdir(parents=True)
    token_file.write_text("unit-token-value\n", encoding="utf-8")
    target_path = tmp_path / "target.toml"
    target_path.write_text(
        (
            "[github]\n"
            'host = "github.example.test"\n'
            'repository = "unit/repo"\n'
            "\n"
            "[gitlab]\n"
            'url = "https://gitlab.example.test"\n'
            f'token_file = "{token_file}"\n'
            "\n"
            "[kubernetes]\n"
            'context = "unit-context"\n'
            'server = "https://api.cluster.example.test:6443"\n'
        ),
        encoding="utf-8",
    )

    target = import_script_module("core.target").load_target(target_path)

    assert target.gitlab.token_file == token_file
    assert not hasattr(target.gitlab, "token")


def test_bind_sources_rejects_env_kubeconfig_inside_skill(
    monkeypatch,
    target_file,
):
    """An ambient KUBECONFIG cannot point back into the installed skill tree."""
    target_mod = import_script_module("core.target")
    credentials = import_script_module("core.credentials")
    _clear_credential_env(monkeypatch)
    monkeypatch.setenv("KUBECONFIG", str(SCRIPT_ROOT / "inside.kubeconfig"))

    with pytest.raises(
        target_mod.TargetError,
        match="kubeconfig must be stored outside",
    ):
        credentials.bind_sources(target_mod.load_target(target_file), revision="a" * 40)


def test_bind_sources_rejects_default_kubeconfig_inside_skill(
    monkeypatch,
    target_file,
):
    """The kubectl default path must obey the same containment rule."""
    target_mod = import_script_module("core.target")
    credentials = import_script_module("core.credentials")
    _clear_credential_env(monkeypatch)
    monkeypatch.delenv("KUBECONFIG", raising=False)
    monkeypatch.setenv("HOME", str(SCRIPT_ROOT))

    with pytest.raises(
        target_mod.TargetError,
        match="kubeconfig must be stored outside",
    ):
        credentials.bind_sources(target_mod.load_target(target_file), revision="a" * 40)


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
        (
            (
                "[github]\n"
                'host = "github.example.test"\n'
                'repository = "unit/repo"\n'
                "\n"
                "[gitlab]\n"
                'url = "https://gitlab.example.test"\n'
                'token_file = "inline-token-value"\n'
                "\n"
                "[kubernetes]\n"
                'context = "unit-context"\n'
                'server = "https://api.cluster.example.test:6443"\n'
            ),
            "token_file",
        ),
    ),
)
def test_load_target_rejects_ambiguous_or_secret_authorities(
    tmp_path, content, message
):
    """Bad target files fail before any live command can use the wrong surface."""
    path = tmp_path / "target.toml"
    path.write_text(content, encoding="utf-8")
    target_mod = import_script_module("core.target")

    with pytest.raises(target_mod.TargetError, match=message):
        target_mod.load_target(path)


def test_runtime_sanitizes_secret_like_output_and_bounds_text():
    """External command output is redacted before it enters diagnostics."""
    runtime = import_script_module("core.runtime")
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


def test_runtime_sanitizes_env_json_url_jwt_and_control_sequences():
    """Trace redaction covers token shapes emitted by CI tools and JSON logs."""
    runtime = import_script_module("core.runtime")
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1bml0In0.signature"
    text = (
        "CI_JOB_TOKEN=unit-ci-token\n"
        "GITLAB_TOKEN=unit-gl-token\n"
        'json={"password":"unit-json-password","token":"unit-json-token"}\n'
        f"callback=https://unit-user:unit-url-secret@gitlab.example.test/path\n"
        f"raw={jwt}\n"
        "\x1b[31mred\x1b[0m\n"
    )

    sanitized = runtime.sanitize(text, limit=1000)

    for secret in (
        "unit-ci-token",
        "unit-gl-token",
        "unit-json-password",
        "unit-json-token",
        "unit-url-secret",
        jwt,
        "\x1b",
    ):
        assert secret not in sanitized
    assert sanitized.count("[REDACTED]") >= 5


def test_run_command_uses_argument_vector_and_noninteractive_environment(monkeypatch):
    """Command execution disables prompts and avoids shell expansion."""
    runtime = import_script_module("core.runtime")
    captured = {}

    def fake_run(argv, **kwargs):
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


def test_run_command_tail_keeps_only_bounded_stdout_tail():
    """Trace collection keeps recent lines without retaining full command output."""
    runtime = import_script_module("core.runtime")

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


def test_run_command_tail_rejects_unbounded_limits():
    """Tail limits must stay positive so a trace cannot request full output."""
    runtime = import_script_module("core.runtime")

    with pytest.raises(ValueError, match="tail limits"):
        runtime.run_command_tail(["tool"], max_lines=0)


def test_run_command_tail_times_out_after_child_closes_pipes_then_lingers():
    """A child that closes output pipes must still be reaped by the deadline."""
    runtime = import_script_module("core.runtime")
    start = time.monotonic()

    result = runtime.run_command_tail(
        [
            sys.executable,
            "-c",
            ("import os, time\nos.close(1)\nos.close(2)\ntime.sleep(2)\n"),
        ],
        timeout=0.2,
        max_lines=5,
        max_bytes=1000,
    )
    elapsed = time.monotonic() - start

    assert result.returncode == 124
    assert elapsed < 1.0


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
def test_error_class_maps_command_failures(returncode, stderr, expected):
    """Gate output classifies common external failures for safe next steps."""
    runtime = import_script_module("core.runtime")
    result = runtime.CommandResult(("tool",), returncode, "", stderr)

    assert runtime.error_class(result) == expected
