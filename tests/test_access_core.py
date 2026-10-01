"""Offline tests for the cross-surface access gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import import_script_module


def _load_target(path: Path) -> Any:
    """Load the nonsecret target used by access tests."""
    return import_script_module("core.target").load_target(path)


def _access_modules() -> tuple[Any, Any]:
    """Load access and runtime modules through the script package path."""
    return import_script_module("core.access"), import_script_module("core.runtime")


def _write_token_target(tmp_path: Path, token_file: Path) -> Path:
    """Write one target file that points at a GitLab token file."""
    path = tmp_path / "target-with-token.toml"
    path.write_text(
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
    return path


def _fake_access_runner(runtime: Any, scenario: str) -> tuple[Any, list[tuple[str, ...]]]:
    """Return a fake command runner and a call journal for one scenario."""
    calls: list[tuple[str, ...]] = []

    def completed(argv: tuple[str, ...], code: int = 0, stdout: str = "", stderr: str = ""):
        return runtime.CommandResult(argv, code, stdout, stderr)

    def kube_args(argv: tuple[str, ...]) -> list[str]:
        context_index = argv.index("--context")
        return list(argv[context_index + 2 :])

    def run(argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        calls.append(command)
        tool = command[0]

        if tool == "gh":
            if command[1:3] == ("auth", "status"):
                if scenario == "github_auth_failed":
                    return completed(command, 1, stderr="not logged in")
                return completed(command)
            if command[1:3] == ("api", "--hostname") and command[-1] == "user":
                if scenario == "github_api_failed":
                    return completed(command, 1, stderr="HTTP 401 unauthorized")
                return completed(command, stdout=json.dumps({"login": "unit-gh"}))
            if command[1:3] == ("api", "--hostname") and command[-1] == "repos/unit/repo":
                return completed(command, stdout=json.dumps({"full_name": "unit/repo"}))
            return completed(command, 99, stderr="unexpected gh call")

        if tool == "glab":
            if command[1:3] == ("auth", "status"):
                if scenario == "gitlab_auth_wrong_host":
                    return completed(command, 1, stderr="not logged in to selected host")
                return completed(command)
            if command[-1] == "user":
                web_host = (
                    "other.example.test"
                    if scenario == "gitlab_identity_wrong_host"
                    else "gitlab.example.test"
                )
                return completed(
                    command,
                    stdout=json.dumps(
                        {
                            "username": "unit-gl",
                            "is_admin": scenario != "gitlab_non_admin",
                            "web_url": f"https://{web_host}/unit-gl",
                        }
                    ),
                )
            if command[-1] == "runners/all?per_page=1":
                if scenario == "gitlab_runner_denied":
                    return completed(command, 1, stderr="HTTP 403 forbidden")
                return completed(command, stdout=json.dumps([{"id": 1}]))
            return completed(command, 99, stderr="unexpected glab call")

        if tool == "kubectl":
            args = kube_args(command)
            if args == ["config", "view", "-o", "json"]:
                server = (
                    "https://api.other.example.test:6443"
                    if scenario == "k8s_wrong_server"
                    else "https://api.cluster.example.test:6443"
                )
                cluster = {"server": server}
                if scenario == "k8s_tls_skip_verify":
                    cluster["insecure-skip-tls-verify"] = True
                return completed(
                    command,
                    stdout=json.dumps(
                        {
                            "contexts": [
                                {
                                    "name": "unit-context",
                                    "context": {"cluster": "cluster-a"},
                                }
                            ],
                            "clusters": [{"name": "cluster-a", "cluster": cluster}],
                        }
                    ),
                )
            if args == ["get", "--raw=/version"]:
                return completed(command, stdout=json.dumps({"major": "1", "minor": "32"}))
            if args == ["auth", "whoami", "-o", "json"]:
                return completed(
                    command,
                    stdout=json.dumps(
                        {"status": {"userInfo": {"username": "unit-admin"}}}
                    ),
                )
            if args[:2] == ["auth", "can-i"]:
                if scenario == "k8s_not_admin" and args[2:4] == ["*", "*"]:
                    return completed(command, 1, stderr="no")
                if scenario == "k8s_exec_denied" and "pods/exec" in args:
                    return completed(command, 1, stderr="no")
                return completed(command, stdout="yes\n")
            if args == ["get", "daemonsets", "--all-namespaces", "-o", "json"]:
                return completed(
                    command,
                    stdout=json.dumps(
                        {
                            "items": [
                                {
                                    "metadata": {
                                        "namespace": "kube-system",
                                        "name": "cilium",
                                    }
                                }
                            ]
                        }
                    ),
                )
            return completed(command, 99, stderr="unexpected kubectl call")

        return completed(command, 127, stderr="command unavailable")

    return run, calls


def test_check_access_passes_when_all_three_authorities_are_admin(monkeypatch, target_file):
    """The gate reports PASS only after GitHub, GitLab, and Kubernetes pass."""
    access, runtime = _access_modules()
    runner, calls = _fake_access_runner(runtime, "success")
    monkeypatch.setattr(access, "run_command", runner)

    report = access.check_access(_load_target(target_file))

    assert report["status"] == "PASS"
    assert set(report["surfaces"]) == {"github", "gitlab", "kubernetes"}
    assert report["surfaces"]["github"]["identity"] == "unit-gh"
    assert report["surfaces"]["gitlab"]["observed_capability"] == [
        "authenticated_host",
        "instance_admin_identity",
        "instance_runner_api_read",
    ]
    assert "cluster_wildcard" in report["surfaces"]["kubernetes"]["observed_capability"]
    assert "cilium_pods_exec" in report["surfaces"]["kubernetes"]["observed_capability"]
    assert any(call[:4] == ("glab", "api", "--hostname", "gitlab.example.test") for call in calls)
    assert any(call[:3] == ("gh", "api", "--hostname") for call in calls)


@pytest.mark.parametrize(
    ("scenario", "surface", "reason"),
    (
        ("github_auth_failed", "github", "authentication"),
        ("github_api_failed", "github", "authentication"),
        ("gitlab_auth_wrong_host", "gitlab", "authentication"),
        ("gitlab_non_admin", "gitlab", "instance_admin_required"),
        ("gitlab_identity_wrong_host", "gitlab", "host_mismatch"),
        ("gitlab_runner_denied", "gitlab", "authorization"),
        ("k8s_wrong_server", "kubernetes", "api_server_mismatch"),
        ("k8s_tls_skip_verify", "kubernetes", "tls_verification_disabled"),
        ("k8s_not_admin", "kubernetes", "cluster_wildcard_denied"),
        ("k8s_exec_denied", "kubernetes", "pods_exec_denied"),
    ),
)
def test_check_access_blocks_each_required_surface(
    monkeypatch,
    target_file,
    scenario,
    surface,
    reason,
):
    """Each authority failure keeps the whole live collection gate blocked."""
    access, runtime = _access_modules()
    runner, _calls = _fake_access_runner(runtime, scenario)
    monkeypatch.setattr(access, "run_command", runner)

    report = access.check_access(_load_target(target_file))

    assert report["status"] == "BLOCKED"
    assert report["surfaces"][surface]["status"] == "BLOCKED"
    assert report["surfaces"][surface]["reason"] == reason
    assert report["surfaces"][surface]["next_step"]


def test_dry_run_access_lists_probes_without_calling_live_tools(monkeypatch, target_file):
    """Dry run explains intended probes and cannot accidentally become a pass."""
    access, _runtime = _access_modules()

    def fail_if_called(argv, **_kwargs):
        pytest.fail(f"dry-run called a live tool: {argv}")

    monkeypatch.setattr(access, "run_command", fail_if_called)

    report = access.dry_run_access(_load_target(target_file))

    assert report["status"] == "DRY_RUN"
    assert report["surfaces"]["github"]["probes"] == [
        "gh auth status",
        "gh api user",
        "gh api repository",
    ]
    assert "GET /user is_admin" in report["surfaces"]["gitlab"]["probes"]
    assert "pods/exec" in report["surfaces"]["kubernetes"]["probes"]


def test_gitlab_access_uses_token_file_only_as_subprocess_environment(monkeypatch, tmp_path):
    """GitLab token-file contents are passed in memory and never enter reports."""
    access, runtime = _access_modules()
    token = "unit-token-value"
    token_file = tmp_path / ".config" / "ci-skills" / "gitlab.example.test.token"
    token_file.parent.mkdir(parents=True)
    token_file.write_text(token + "\n", encoding="utf-8")
    target = _load_target(_write_token_target(tmp_path, token_file))
    observed_envs: list[dict[str, str] | None] = []

    def fake_run(argv, **kwargs):
        command = tuple(str(part) for part in argv)
        observed_envs.append(kwargs.get("env"))
        assert token not in command
        if command[:4] == ("glab", "auth", "status", "--hostname"):
            return runtime.CommandResult(command, 0, "", "")
        if command[-1] == "user":
            return runtime.CommandResult(
                command,
                0,
                json.dumps(
                    {
                        "username": "unit-gl",
                        "is_admin": True,
                        "web_url": "https://gitlab.example.test/unit-gl",
                    }
                ),
                "",
            )
        if command[-1] == "runners/all?per_page=1":
            return runtime.CommandResult(command, 0, json.dumps([{"id": 1}]), "")
        return runtime.CommandResult(command, 99, "", "unexpected call")

    monkeypatch.setattr(access, "run_command", fake_run)

    surface = access.gitlab_access(target)

    assert surface.status == "PASS"
    assert observed_envs
    assert all(env and env.get("GITLAB_TOKEN") == token for env in observed_envs)
    assert token not in json.dumps(surface.as_dict())
    assert str(token_file) not in json.dumps(surface.as_dict())


def test_gitlab_access_without_token_file_uses_glab_host_profile(monkeypatch, target_file):
    """Omitting token_file preserves glab's selected host profile behavior."""
    access, runtime = _access_modules()
    observed_envs: list[dict[str, str] | None] = []

    def fake_run(argv, **kwargs):
        command = tuple(str(part) for part in argv)
        observed_envs.append(kwargs.get("env"))
        if command[:4] == ("glab", "auth", "status", "--hostname"):
            return runtime.CommandResult(command, 0, "", "")
        if command[-1] == "user":
            return runtime.CommandResult(
                command,
                0,
                json.dumps(
                    {
                        "username": "unit-gl",
                        "is_admin": True,
                        "web_url": "https://gitlab.example.test/unit-gl",
                    }
                ),
                "",
            )
        if command[-1] == "runners/all?per_page=1":
            return runtime.CommandResult(command, 0, json.dumps([{"id": 1}]), "")
        return runtime.CommandResult(command, 99, "", "unexpected call")

    monkeypatch.setattr(access, "run_command", fake_run)

    surface = access.gitlab_access(_load_target(target_file))

    assert surface.status == "PASS"
    assert all(not env or "GITLAB_TOKEN" not in env for env in observed_envs)


@pytest.mark.parametrize(
    ("token_body", "expected_reason"),
    (
        (None, "credential_file_unavailable"),
        ("", "credential_file_invalid"),
        ("   \n", "credential_file_invalid"),
    ),
)
def test_gitlab_access_blocks_missing_or_empty_token_file(
    monkeypatch,
    tmp_path,
    token_body,
    expected_reason,
):
    """A configured token_file must exist and contain one nonempty token."""
    access, _runtime = _access_modules()
    token_file = tmp_path / ".config" / "ci-skills" / "gitlab.example.test.token"
    token_file.parent.mkdir(parents=True)
    if token_body is not None:
        token_file.write_text(token_body, encoding="utf-8")
    target = _load_target(_write_token_target(tmp_path, token_file))
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(tuple(str(part) for part in argv))
        pytest.fail("missing or empty token_file must block before glab runs")

    monkeypatch.setattr(access, "run_command", fake_run)

    surface = access.gitlab_access(target)

    assert surface.status == "BLOCKED"
    assert surface.reason == expected_reason
    assert surface.identity is None
    assert calls == []


def test_kubectl_argv_binds_context_and_optional_kubeconfig(tmp_path, target_file):
    """Every Kubernetes probe stays pinned to the selected context and file."""
    access, _runtime = _access_modules()
    target_mod = import_script_module("core.target")
    base = _load_target(target_file)
    target = target_mod.Target(
        github=base.github,
        gitlab=base.gitlab,
        kubernetes=target_mod.KubernetesTarget(
            context="chosen-context",
            server=base.kubernetes.server,
            kubeconfig=tmp_path / "kubeconfig",
        ),
    )

    argv = access.kubectl_argv(target, "auth", "can-i", "*", "*")

    assert argv == [
        "kubectl",
        "--kubeconfig",
        str(tmp_path / "kubeconfig"),
        "--context",
        "chosen-context",
        "auth",
        "can-i",
        "*",
        "*",
    ]
