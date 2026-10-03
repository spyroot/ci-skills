"""Offline tests for the cross-surface access gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import import_script_module

TEST_REVISION = "a" * 40


def _load_target(path: Path) -> Any:
    """Load the nonsecret target used by access tests."""
    return import_script_module("core.target").load_target(path)


def _access_modules() -> tuple[Any, Any]:
    """Load access and runtime modules through the script package path."""
    return import_script_module("core.access"), import_script_module("core.runtime")


def _bind_target(path: Path) -> Any:
    """Load a target and freeze its credential sources with a test SHA."""
    credentials = import_script_module("core.credentials")
    return credentials.bind_sources(_load_target(path), revision=TEST_REVISION)


def _clear_token_env(monkeypatch) -> None:
    """Remove ambient token variables so source precedence tests are explicit."""
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


@pytest.mark.parametrize(
    ("host", "selected"),
    (
        ("github.com", "GH_TOKEN"),
        ("tenant.ghe.com", "GH_TOKEN"),
        ("github.example.test", "GH_ENTERPRISE_TOKEN"),
        ("tenant.ghe.com.example.test", "GH_ENTERPRISE_TOKEN"),
    ),
)
def test_github_file_token_uses_gh_host_class_and_clears_ambient_tokens(
    monkeypatch, tmp_path, host, selected
):
    """The child gh process must use the selected file, not an ambient token."""
    catalog = import_script_module("core.catalog")
    credentials = import_script_module("core.credentials")
    runtime = import_script_module("core.runtime")
    token_file = tmp_path / "github.token"
    token_file.write_text("selected-file-token\n", encoding="utf-8")
    for name in catalog.GITHUB_TOKEN_VARIABLES:
        monkeypatch.setenv(name, f"ambient-{name}")

    source = credentials._token_source(
        token_file,
        catalog.github_variables(host),
        "unused-store",
        clear_names=catalog.GITHUB_TOKEN_VARIABLES,
    )
    effective = runtime._environment(source.environment)

    assert source.reference == f"file:{token_file.resolve()}"
    assert effective[selected] == "selected-file-token"
    assert all(
        name not in effective
        for name in catalog.GITHUB_TOKEN_VARIABLES
        if name != selected
    )


def test_ghe_cloud_uses_gh_token_over_lower_priority_and_server_tokens(monkeypatch):
    catalog = import_script_module("core.catalog")
    credentials = import_script_module("core.credentials")
    runtime = import_script_module("core.runtime")
    monkeypatch.setenv("GH_TOKEN", "selected-cloud-token")
    monkeypatch.setenv("GITHUB_TOKEN", "lower-priority-token")
    monkeypatch.setenv("GH_ENTERPRISE_TOKEN", "unrelated-server-token")

    source = credentials._token_source(
        None,
        catalog.github_variables("tenant.ghe.com"),
        "unused-store",
        clear_names=catalog.GITHUB_TOKEN_VARIABLES,
    )
    effective = runtime._environment(source.environment)

    assert source.reference == "env:GH_TOKEN"
    assert effective["GH_TOKEN"] == "selected-cloud-token"
    assert "GITHUB_TOKEN" not in effective
    assert "GH_ENTERPRISE_TOKEN" not in effective


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


def _write_full_target(
    tmp_path: Path,
    *,
    github_token_file: Path | None = None,
    gitlab_token_file: Path | None = None,
    kubeconfig: Path | None = None,
) -> Path:
    """Write a target file with optional explicit credential source paths."""
    path = tmp_path / "target.toml"
    github_token = f'token_file = "{github_token_file}"\n' if github_token_file else ""
    gitlab_token = f'token_file = "{gitlab_token_file}"\n' if gitlab_token_file else ""
    kubeconfig_line = f'kubeconfig = "{kubeconfig}"\n' if kubeconfig else ""
    path.write_text(
        (
            "[github]\n"
            'host = "github.example.test"\n'
            'repository = "unit/repo"\n'
            f"{github_token}"
            "\n"
            "[gitlab]\n"
            'url = "https://gitlab.example.test"\n'
            f"{gitlab_token}"
            "\n"
            "[kubernetes]\n"
            'context = "unit-context"\n'
            'server = "https://api.cluster.example.test:6443"\n'
            f"{kubeconfig_line}"
        ),
        encoding="utf-8",
    )
    return path


def _fake_access_runner(
    runtime: Any, scenario: str
) -> tuple[Any, list[tuple[str, ...]]]:
    """Return a fake command runner and a call journal for one scenario."""
    calls: list[tuple[str, ...]] = []

    def completed(
        argv: tuple[str, ...], code: int = 0, stdout: str = "", stderr: str = ""
    ):
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
            if (
                command[1:3] == ("api", "--hostname")
                and command[-1] == "repos/unit/repo"
            ):
                return completed(command, stdout=json.dumps({"full_name": "unit/repo"}))
            return completed(command, 99, stderr="unexpected gh call")

        if tool == "glab":
            if command[1:3] == ("auth", "status"):
                if scenario == "gitlab_auth_wrong_host":
                    return completed(
                        command, 1, stderr="not logged in to selected host"
                    )
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
            if args in (
                ["config", "view", "-o", "json"],
                ["config", "view", "--raw", "-o", "json"],
            ):
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
                                    "context": {
                                        "cluster": "cluster-a",
                                        "user": "admin-user",
                                    },
                                }
                            ],
                            "clusters": [{"name": "cluster-a", "cluster": cluster}],
                            "users": [
                                {
                                    "name": "admin-user",
                                    "user": {
                                        "exec": {
                                            "command": "unit-auth",
                                            "args": ["token"],
                                        }
                                    },
                                }
                            ],
                        }
                    ),
                )
            if args == ["get", "--raw=/version"]:
                return completed(
                    command, stdout=json.dumps({"major": "1", "minor": "32"})
                )
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
                                    },
                                    "spec": {
                                        "selector": {
                                            "matchLabels": {"k8s-app": "cilium"}
                                        }
                                    },
                                }
                            ]
                        }
                    ),
                )
            if "get" in args and "pods" in args:
                return completed(
                    command,
                    stdout=json.dumps(
                        {
                            "items": [
                                {
                                    "metadata": {
                                        "namespace": "kube-system",
                                        "name": "cilium-operator-ready",
                                        "labels": {"name": "cilium-operator"},
                                    },
                                    "status": {
                                        "conditions": [
                                            {"type": "Ready", "status": "True"}
                                        ]
                                    },
                                },
                                {
                                    "metadata": {
                                        "namespace": "kube-system",
                                        "name": "cilium-ready",
                                        "labels": {"k8s-app": "cilium"},
                                    },
                                    "status": {
                                        "conditions": [
                                            {"type": "Ready", "status": "True"}
                                        ]
                                    },
                                },
                            ]
                        }
                    ),
                )
            if "exec" in args:
                if scenario == "k8s_health_failed":
                    return completed(command, 1, stderr="connection refused")
                return completed(
                    command,
                    stdout=json.dumps({"local": {"name": "unit-node"}, "nodes": []}),
                )
            return completed(command, 99, stderr="unexpected kubectl call")

        return completed(command, 127, stderr="command unavailable")

    return run, calls


def test_check_access_passes_when_all_three_authorities_are_admin(
    monkeypatch, target_file
):
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
    assert (
        "cilium_health_exec" in report["surfaces"]["kubernetes"]["observed_capability"]
    )
    assert any(
        call[:4] == ("glab", "api", "--hostname", "gitlab.example.test")
        for call in calls
    )
    assert any(call[:3] == ("gh", "api", "--hostname") for call in calls)
    assert any("exec" in call and "cilium-health" in call for call in calls)


def test_check_access_receipt_records_metadata_and_resolved_sources(
    monkeypatch,
    tmp_path,
):
    """A PASS receipt identifies the execution host, revision, and sources."""
    access, runtime = _access_modules()
    _clear_token_env(monkeypatch)
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    runner, _calls = _fake_access_runner(runtime, "success")
    monkeypatch.setattr(access, "run_command", runner)

    report = access.check_access(
        _bind_target(_write_full_target(tmp_path, kubeconfig=kubeconfig)),
    )

    assert report["status"] == "PASS"
    assert report["execution_host"]
    assert report["captured_at"]
    # Running inside this checkout, the revision comes from Git and is marked
    # verified; the operator's --revision is only a claim and must not win.
    assert report["skill"]["algorithm"] == "sha256-tree-v1"
    assert len(report["skill"]["digest"]) == 64
    assert report["skill"]["file_count"] > 0
    assert report["skill"]["revision"]["source"] == "git_head"
    assert report["tested_revision"] != TEST_REVISION
    # The invoking project's commit is recorded separately, never as the
    # skill's revision. It is set under CI and absent on a workstation, so the
    # assertion is on the SEPARATION, not on the field being empty;
    # test_provenance.py pins the values with an explicit environment.
    consuming = report["consuming_project"]
    assert set(consuming) == {"commit", "source"}
    assert consuming["source"] is None or consuming["source"].startswith("env:")
    assert report["skill"]["revision"]["source"] == "git_head"
    assert report["targets"] == {
        "github": "github.example.test/unit/repo",
        "gitlab": "https://gitlab.example.test",
        "kubernetes": {
            "context": "unit-context",
            "server": "https://api.cluster.example.test:6443",
        },
    }
    assert report["credential_sources"]["github"] == (
        "gh-credential-store:github.example.test"
    )
    assert report["credential_sources"]["gitlab"] == (
        "glab-credential-store:gitlab.example.test"
    )
    assert report["credential_sources"]["kubernetes"] == f"file:{kubeconfig.resolve()}"
    kubernetes = report["surfaces"]["kubernetes"]
    assert kubernetes["credential_source"] == f"file:{kubeconfig.resolve()}"
    assert kubernetes["details"]["kubeconfig_files"] == [str(kubeconfig.resolve())]
    assert kubernetes["details"]["context"] == "unit-context"
    assert kubernetes["details"]["user_entry"] == "admin-user"
    assert kubernetes["details"]["api_server"] == (
        "https://api.cluster.example.test:6443"
    )
    assert kubernetes["details"]["auth_mechanism"] == {
        "type": "exec-provider",
        "source": "unit-auth",
    }


def test_check_access_resolves_ambient_token_environment_sources(
    monkeypatch,
    tmp_path,
):
    """Environment tokens must be named as the effective credential source."""
    access, runtime = _access_modules()
    _clear_token_env(monkeypatch)
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    token = "ambient-token-value"
    monkeypatch.setenv("GH_ENTERPRISE_TOKEN", token)
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", token)
    runner, _calls = _fake_access_runner(runtime, "success")
    monkeypatch.setattr(access, "run_command", runner)

    report = access.check_access(
        _bind_target(_write_full_target(tmp_path, kubeconfig=kubeconfig)),
    )

    assert report["status"] == "PASS"
    assert report["credential_sources"]["github"] == "env:GH_ENTERPRISE_TOKEN"
    assert report["credential_sources"]["gitlab"] == "env:GITLAB_ACCESS_TOKEN"
    assert token not in json.dumps(report)


def test_check_access_prefers_explicit_token_files_over_ambient_env(
    monkeypatch,
    tmp_path,
):
    """Explicit token files are the selected source even when env vars exist."""
    access, runtime = _access_modules()
    _clear_token_env(monkeypatch)
    github_token = tmp_path / "github.token"
    gitlab_token = tmp_path / "gitlab.token"
    kubeconfig = tmp_path / "kubeconfig"
    github_token.write_text("github-file-token\n", encoding="utf-8")
    gitlab_token.write_text("gitlab-file-token\n", encoding="utf-8")
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    monkeypatch.setenv("GH_ENTERPRISE_TOKEN", "ambient-github-token")
    monkeypatch.setenv("GITLAB_TOKEN", "ambient-gitlab-token")
    observed_envs: list[dict[str, str] | None] = []
    runner, _calls = _fake_access_runner(runtime, "success")

    def fake_run(argv, **kwargs):
        observed_envs.append(kwargs.get("env"))
        return runner(argv, **kwargs)

    monkeypatch.setattr(access, "run_command", fake_run)
    target = _bind_target(
        _write_full_target(
            tmp_path,
            github_token_file=github_token,
            gitlab_token_file=gitlab_token,
            kubeconfig=kubeconfig,
        )
    )

    report = access.check_access(target)

    assert report["status"] == "PASS"
    assert report["credential_sources"]["github"] == f"file:{github_token.resolve()}"
    assert report["credential_sources"]["gitlab"] == f"file:{gitlab_token.resolve()}"
    gh_envs = [env for env in observed_envs if env and "GH_ENTERPRISE_TOKEN" in env]
    gl_envs = [env for env in observed_envs if env and "GITLAB_TOKEN" in env]
    assert gh_envs and all(
        env["GH_ENTERPRISE_TOKEN"] == "github-file-token" for env in gh_envs
    )
    assert gl_envs and all(
        env["GITLAB_TOKEN"] == "gitlab-file-token" for env in gl_envs
    )
    assert "ambient-github-token" not in json.dumps(report)
    assert "ambient-gitlab-token" not in json.dumps(report)
    assert "github-file-token" not in json.dumps(report)
    assert "gitlab-file-token" not in json.dumps(report)


@pytest.mark.parametrize(
    ("mechanism", "expected_type"),
    (
        ("embedded_token", "embedded-token"),
        ("token_file", "tokenFile"),
        ("client_certificate", "client-certificate"),
        ("exec", "exec-provider"),
    ),
)
def test_check_access_records_kubernetes_auth_mechanism_without_secret_values(
    monkeypatch,
    tmp_path,
    mechanism,
    expected_type,
):
    """Kubernetes source read-back names the real auth mechanism only."""
    access, runtime = _access_modules()
    _clear_token_env(monkeypatch)
    kubeconfig = tmp_path / f"{mechanism}.kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    secret_values: list[str] = []
    if mechanism == "embedded_token":
        user_auth = {"token": "embedded-secret-token"}
        secret_values.append("embedded-secret-token")
    elif mechanism == "token_file":
        token_file = tmp_path / "admin.token"
        token_file.write_text("token-file-secret\n", encoding="utf-8")
        user_auth = {"tokenFile": str(token_file)}
        secret_values.append("token-file-secret")
    elif mechanism == "client_certificate":
        cert = tmp_path / "admin.crt"
        key = tmp_path / "admin.key"
        cert.write_text("certificate-data\n", encoding="utf-8")
        key.write_text("private-key-secret\n", encoding="utf-8")
        user_auth = {"client-certificate": str(cert), "client-key": str(key)}
        secret_values.append("private-key-secret")
    else:
        user_auth = {"exec": {"command": "unit-auth", "args": ["token"]}}

    runner, _calls = _fake_access_runner(runtime, "success")

    def fake_run(argv, **kwargs):
        command = tuple(str(part) for part in argv)
        if command[0] == "kubectl":
            context_index = command.index("--context")
            args = list(command[context_index + 2 :])
            if args in (
                ["config", "view", "-o", "json"],
                ["config", "view", "--raw", "-o", "json"],
            ):
                return runtime.CommandResult(
                    command,
                    0,
                    json.dumps(
                        {
                            "contexts": [
                                {
                                    "name": "unit-context",
                                    "context": {
                                        "cluster": "cluster-a",
                                        "user": "admin-user",
                                    },
                                }
                            ],
                            "clusters": [
                                {
                                    "name": "cluster-a",
                                    "cluster": {
                                        "server": (
                                            "https://api.cluster.example.test:6443"
                                        )
                                    },
                                }
                            ],
                            "users": [{"name": "admin-user", "user": user_auth}],
                        }
                    ),
                    "",
                )
        return runner(argv, **kwargs)

    monkeypatch.setattr(access, "run_command", fake_run)
    report = access.check_access(
        _bind_target(_write_full_target(tmp_path, kubeconfig=kubeconfig)),
    )

    assert report["status"] == "PASS"
    assert report["credential_sources"]["kubernetes"] == f"file:{kubeconfig.resolve()}"
    details = report["surfaces"]["kubernetes"]["details"]
    assert details["kubeconfig_files"] == [str(kubeconfig.resolve())]
    assert details["context"] == "unit-context"
    assert details["user_entry"] == "admin-user"
    assert details["api_server"] == "https://api.cluster.example.test:6443"
    assert details["auth_mechanism"]["type"] == expected_type
    serialized = json.dumps(report)
    for value in secret_values:
        assert value not in serialized


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
        ("k8s_health_failed", "kubernetes", "cilium_health_exec_failed"),
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


def test_dry_run_access_lists_probes_without_calling_live_tools(
    monkeypatch, target_file
):
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


def test_gitlab_access_uses_token_file_only_as_subprocess_environment(
    monkeypatch, tmp_path
):
    """GitLab token-file contents override ambient env and never enter reports."""
    access, runtime = _access_modules()
    _clear_token_env(monkeypatch)
    token = "unit-token-value"
    token_file = tmp_path / ".config" / "ci-skills" / "gitlab.example.test.token"
    token_file.parent.mkdir(parents=True)
    token_file.write_text(token + "\n", encoding="utf-8")
    monkeypatch.setenv("GITLAB_TOKEN", "ambient-token-value")
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


def test_gitlab_access_without_token_file_uses_glab_host_profile(
    monkeypatch, target_file
):
    """Omitting token_file preserves glab's selected host profile behavior."""
    access, runtime = _access_modules()
    _clear_token_env(monkeypatch)
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
    _clear_token_env(monkeypatch)
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
