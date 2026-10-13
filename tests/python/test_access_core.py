"""Offline tests for the cross-surface access gate.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, MutableSequence, Sequence
from os import PathLike
from pathlib import Path
from types import ModuleType
from typing import IO, TYPE_CHECKING, Optional, Protocol, TypedDict, Union, Unpack

import pytest
from conftest import import_script_module

TEST_REVISION = "a" * 40


if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject
    from lib.python.core.runtime import CommandResult
    from lib.python.core.target import Target


class _CommandOptions(TypedDict, total=False):
    """Keyword options accepted by command-runner fixture doubles.

    .. attribute :: timeout
        :type: int

        Maximum command runtime in seconds.

    .. attribute :: max_bytes
        :type: int

        Maximum stdout bytes retained by tail runners.

    .. attribute :: max_lines
        :type: int

        Maximum stdout lines retained by tail runners.

    .. attribute :: env
        :type: Optional[Mapping[str, Optional[str]]]

        Child environment overrides passed to the runtime helper.

    .. attribute :: cwd
        :type: Optional[Union[str, Path]]

        Optional child working directory for tail runners.
    """

    timeout: int
    max_bytes: int
    max_lines: int
    env: Optional[Mapping[str, Optional[str]]]
    cwd: Optional[Union[str, Path]]


class _CommandRunner(Protocol):
    """Callable shape used by access command-runner doubles."""

    def __call__(
        self, argv: Sequence[str], **kwargs: Unpack[_CommandOptions]
    ) -> CommandResult:
        """Run one native command in a fixture.

        :param argv: Native command vector requested by the access gate.
        :param **kwargs: Keyword arguments forwarded to the fake command boundary.
        :returns: Synthetic bounded command result.
        """


class _TokenPath(PathLike[str]):
    """Expose a scratch token file through the path-like protocol."""

    def __init__(self, path: Path):
        """Retain the concrete token path used by ``__fspath__``.

        :param path: Scratch token file created by the test.
        """
        self.path = path

    def __fspath__(self) -> str:
        """Return the token filename as text.

        :returns: String path accepted by ``Path``.
        """
        return str(self.path)


def _load_target(
    path: Path, *, required_surfaces: Optional[tuple[str, ...]] = None
) -> Target:
    """Load the nonsecret target with optional surface filtering.

    :param path: Fixture target TOML path.
    :param required_surfaces: Provider surfaces to retain during loading.
    :returns: Parsed target used by access checks.
    """
    if required_surfaces is None:
        return import_script_module("lib.python.core.target").load_target(path)
    return import_script_module("lib.python.core.target").load_target(
        path, required_surfaces=required_surfaces
    )


def _access_modules() -> tuple[ModuleType, ModuleType]:
    """Import access and bounded command runtime through the skill package.

    :returns: Access module and runtime module under test.
    """
    return import_script_module("lib.python.access.access"), import_script_module(
        "lib.python.core.runtime"
    )


def _provider_modules() -> tuple[ModuleType, ModuleType, ModuleType]:
    """Load provider-owned command boundaries used by aggregate access checks.

    :returns: GitHub, GitLab, and Kubernetes provider access modules.
    """
    return (
        import_script_module("lib.python.access.github"),
        import_script_module("lib.python.access.gitlab"),
        import_script_module("lib.python.access.kubernetes"),
    )


def _patch_provider_commands(
    monkeypatch: pytest.MonkeyPatch, runner: _CommandRunner
) -> None:
    """Replace the native command boundary in every provider access module.

    :param monkeypatch: Pytest fixture applying the temporary replacements.
    :param runner: Synthetic command boundary shared by all selected providers.
    :returns: None; each provider module receives the same command runner.
    """
    for provider in _provider_modules():
        monkeypatch.setattr(provider, "run_command", runner)


def _bind_target(path: Path) -> Target:
    """Bind a fixture target to credential sources with a test revision.

    :param path: Target TOML file to load and bind.
    :returns: Target carrying selected credential source references.
    """
    credentials = import_script_module("lib.python.access.credentials")
    return credentials.bind_sources(_load_target(path), revision=TEST_REVISION)


def _clear_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove ambient GitHub and GitLab tokens before precedence assertions.

    :param monkeypatch: Pytest fixture deleting known token variables.

    :returns: None; the helper records behavior through its side effects.
    """
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


@pytest.mark.parametrize("selector", ("string", "path", "pathlike"))
def test_read_token_file_accepts_declared_path_protocols(
    tmp_path: Path, selector: str
) -> None:
    """Read synthetic tokens from string, Path, and custom PathLike inputs.

    :param tmp_path: Scratch directory containing the token file.
    :param selector: Path representation passed to ``read_token_file``.

    :returns: None; assertions in the body validate the regression.
    """
    sources = import_script_module("lib.python.access.sources")
    token = tmp_path / "provider.token"
    token.write_text("unit-token\n", encoding="utf-8")
    if selector == "string":
        selected: Union[str, Path, _TokenPath] = str(token)
    elif selector == "path":
        selected = token
    else:
        selected = _TokenPath(token)

    assert sources.read_token_file(selected) == "unit-token"


def test_read_token_file_rejects_none_before_filesystem_access() -> None:
    """Reject a missing path without consulting any live credential location.
    :returns: None; assertions in the body validate the regression.
    """
    sources = import_script_module("lib.python.access.sources")

    with pytest.raises(TypeError):
        sources.read_token_file(None)


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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, host: str, selected: str
) -> None:
    """Select the host-class token file and clear competing ambient tokens.

    :param monkeypatch: Pytest fixture setting all ambient GitHub tokens.
    :param tmp_path: Directory receiving the selected synthetic token file.
    :param host: GitHub host whose cloud or Enterprise class is tested.
    :param selected: Child variable expected to carry the file token.

    :returns: None; assertions in the body validate the regression.
    """
    catalog = import_script_module("lib.python.core.catalog")
    sources = import_script_module("lib.python.access.sources")
    runtime = import_script_module("lib.python.core.runtime")
    token_file = tmp_path / "github.token"
    token_file.write_text("selected-file-token\n", encoding="utf-8")
    for name in catalog.GITHUB_TOKEN_VARIABLES:
        monkeypatch.setenv(name, f"ambient-{name}")

    source = sources._token_source(
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


def test_ghe_cloud_uses_gh_token_over_lower_priority_and_server_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prefer GH_TOKEN for ghe.com while clearing lower-priority values.

    :param monkeypatch: Pytest fixture setting competing GitHub token variables.

    :returns: None; assertions in the body validate the regression.
    """
    catalog = import_script_module("lib.python.core.catalog")
    sources = import_script_module("lib.python.access.sources")
    runtime = import_script_module("lib.python.core.runtime")
    monkeypatch.setenv("GH_TOKEN", "selected-cloud-token")
    monkeypatch.setenv("GITHUB_TOKEN", "lower-priority-token")
    monkeypatch.setenv("GH_ENTERPRISE_TOKEN", "unrelated-server-token")

    source = sources._token_source(
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
    """Write a target selecting an explicit GitLab token file.

    :param tmp_path: Directory receiving the target TOML file.
    :param token_file: Token path recorded in the GitLab section.
    :returns: Path to the written target.
    """
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
    github_token_file: Optional[Path] = None,
    gitlab_token_file: Optional[Path] = None,
    kubeconfig: Optional[Path] = None,
) -> Path:
    """Write all three target surfaces with selected source paths.

    :param tmp_path: Directory receiving the target TOML file.
    :param github_token_file: Explicit GitHub token path, if selected.
    :param gitlab_token_file: Explicit GitLab token path, if selected.
    :param kubeconfig: Explicit Kubernetes config path, if selected.
    :returns: Path to the written target.
    """
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
    runtime: ModuleType, scenario: str
) -> tuple[_CommandRunner, MutableSequence[tuple[str, ...]]]:
    """Build a command double for access-gate success and failure scenarios.

    :param runtime: Module providing the command-result type.
    :param scenario: Provider or Kubernetes failure variant to simulate.
    :returns: Runner callable and journal of exact native argv calls.
    """
    calls: MutableSequence[tuple[str, ...]] = []

    def completed(
        argv: tuple[str, ...], code: int = 0, stdout: str = "", stderr: str = ""
    ) -> CommandResult:
        """Construct a native command result for the simulated probe.

        :param argv: Exact command vector returned in the result.
        :param code: Simulated process exit code.
        :param stdout: Simulated provider response body.
        :param stderr: Simulated provider diagnostic.
        :returns: Runtime command-result record.
        """
        return runtime.CommandResult(argv, code, stdout, stderr)

    def kube_args(argv: tuple[str, ...]) -> Sequence[str]:
        """Strip pinned context options from a simulated kubectl call.

        :param argv: Full kubectl command with ``--context``.
        :returns: Kubernetes subcommand arguments after the context value.
        """
        context_index = argv.index("--context")
        return list(argv[context_index + 2 :])

    def run(argv: Sequence[str], **_kwargs: Unpack[_CommandOptions]) -> CommandResult:
        """Answer expected gh, glab, and kubectl probes for one scenario.

        :param argv: Native command requested by the access gate.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Simulated command result, including failures for unknown calls.
        """
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
                if scenario == "k8s_cilium_missing":
                    return completed(command, stdout=json.dumps({"items": []}))
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


def test_kubernetes_collectors_only_require_their_own_live_access(
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Let Ceph collect without Cilium while gating Cilium on its own exec.

    :param monkeypatch: Pytest fixture replacing target resolution and commands.
    :param target_file: Fixture TOML with all three target surfaces.
    :param capsys: Pytest fixture capturing structured CLI output.

    :returns: None; assertions in the body validate the regression.
    """
    _access, runtime = _access_modules()
    cli = import_script_module("lib.python.core.cli")
    runner, calls = _fake_access_runner(runtime, "k8s_cilium_missing")
    _patch_provider_commands(monkeypatch, runner)
    monkeypatch.setattr(
        cli,
        "resolve_project_target",
        lambda _target, _binding, *, dry_run, required_surfaces: _load_target(
            target_file, required_surfaces=required_surfaces
        ),
    )
    monkeypatch.setattr(cli, "bind_sources", lambda target, *, revision: target)

    args = argparse.Namespace(
        target=str(target_file),
        binding=None,
        json=True,
        yaml=False,
        human=False,
        dry_run=False,
        revision=None,
        output_dir=None,
        describe=False,
        publication=False,
        receipt_out=None,
        namespace="rook-ceph",
    )

    def collect_ceph_cluster(_target: Target, _args: argparse.Namespace) -> JSONObject:
        """Return a Ceph PASS record after Kubernetes-only access succeeds.

        :param _target: Resolved target accepted by the collector double.
        :param _args: Parsed command arguments accepted by the collector double.
        :returns: Minimal passing Ceph report.
        """
        return {"kind": "ceph_cluster", "status": "PASS", "records": [], "errors": []}

    assert cli.execute(args, collect_ceph_cluster) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"
    assert not any("daemonsets" in call for call in calls)

    def collect_cilium(_target: Target, _args: argparse.Namespace) -> None:
        """Expose any Cilium collection before its access gate passes.

        :param _target: Resolved target accepted by the collector double.
        :param _args: Parsed command arguments accepted by the collector double.

        :returns: None; the helper records behavior through its side effects.
        """
        pytest.fail("Cilium collection must wait for its live access gate")

    assert cli.execute(args, collect_cilium) == 2
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["surfaces"]["kubernetes"]["reason"] == (
        "cilium_namespace_not_unique"
    )
    assert any("daemonsets" in call for call in calls)


def test_check_access_passes_when_all_three_authorities_are_admin(
    monkeypatch: pytest.MonkeyPatch, target_file: Path
) -> None:
    """Require observed admin capabilities across GitHub, GitLab, and cluster.

    :param monkeypatch: Pytest fixture installing the success command runner.
    :param target_file: Fixture target selecting all three surfaces.

    :returns: None; assertions in the body validate the regression.
    """
    access, runtime = _access_modules()
    runner, calls = _fake_access_runner(runtime, "success")
    _patch_provider_commands(monkeypatch, runner)

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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Require PASS provenance and effective nonsecret credential sources.

    :param monkeypatch: Pytest fixture clearing token env and stubbing commands.
    :param tmp_path: Scratch target and kubeconfig path used by the receipt.

    :returns: None; assertions in the body validate the regression.
    """
    access, runtime = _access_modules()
    _clear_token_env(monkeypatch)
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    runner, _calls = _fake_access_runner(runtime, "success")
    _patch_provider_commands(monkeypatch, runner)

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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Name effective token variables in receipts without leaking values.

    :param monkeypatch: Pytest fixture selecting ambient token variables.
    :param tmp_path: Scratch target and kubeconfig path.

    :returns: None; assertions in the body validate the regression.
    """
    access, runtime = _access_modules()
    _clear_token_env(monkeypatch)
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    token = "ambient-token-value"
    monkeypatch.setenv("GH_ENTERPRISE_TOKEN", token)
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", token)
    runner, _calls = _fake_access_runner(runtime, "success")
    _patch_provider_commands(monkeypatch, runner)

    report = access.check_access(
        _bind_target(_write_full_target(tmp_path, kubeconfig=kubeconfig)),
    )

    assert report["status"] == "PASS"
    assert report["credential_sources"]["github"] == "env:GH_ENTERPRISE_TOKEN"
    assert report["credential_sources"]["gitlab"] == "env:GITLAB_ACCESS_TOKEN"
    assert token not in json.dumps(report)


def test_check_access_prefers_explicit_token_files_over_ambient_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Prefer selected token files and hide both file and ambient values.

    :param monkeypatch: Pytest fixture selecting tokens and fake commands.
    :param tmp_path: Scratch token files, kubeconfig, and target.

    :returns: None; assertions in the body validate the regression.
    """
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
    observed_envs: MutableSequence[Optional[Mapping[str, str]]] = []
    runner, _calls = _fake_access_runner(runtime, "success")

    def fake_run(
        argv: Sequence[str],
        **kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Capture child environments before delegating to the access double.

        :param argv: Provider command requested by the access gate.
        :param **kwargs: Keyword arguments forwarded to the fake command boundary.
        :returns: Scenario runner's simulated command result.

        """
        observed_envs.append(kwargs.get("env"))
        return runner(argv, **kwargs)

    _patch_provider_commands(monkeypatch, fake_run)
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
        ("token_file_with_embedded_token", "tokenFile"),
        ("client_certificate", "client-certificate"),
        ("exec", "exec-provider"),
    ),
)
def test_check_access_records_kubernetes_auth_mechanism_without_secret_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mechanism: str,
    expected_type: str,
) -> None:
    """Report effective kubeconfig auth type without serializing secrets.

    :param monkeypatch: Pytest fixture replacing kubectl and token variables.
    :param tmp_path: Scratch kubeconfig, token, certificate, and key files.
    :param mechanism: Embedded, file, certificate, or exec auth variant.
    :param expected_type: Public mechanism label expected in the receipt.

    :returns: None; assertions in the body validate the regression.
    """
    access, runtime = _access_modules()
    _clear_token_env(monkeypatch)
    kubeconfig = tmp_path / f"{mechanism}.kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    secret_values: MutableSequence[str] = []
    if mechanism == "embedded_token":
        user_auth = {"token": "embedded-secret-token"}
        secret_values.append("embedded-secret-token")
    elif mechanism in ("token_file", "token_file_with_embedded_token"):
        token_file = tmp_path / "admin.token"
        token_file.write_text("token-file-secret\n", encoding="utf-8")
        user_auth = {"tokenFile": str(token_file)}
        secret_values.append("token-file-secret")
        if mechanism == "token_file_with_embedded_token":
            user_auth["token"] = "stale-embedded-secret"
            secret_values.append("stale-embedded-secret")
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

    def fake_run(
        argv: Sequence[str],
        **kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Replace kubeconfig read-back with the selected auth variant.

        :param argv: Native command requested by the access gate.
        :param **kwargs: Keyword arguments forwarded to the fake command boundary.
        :returns: Fixture kubeconfig JSON or another simulated probe result.

        """
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

    _patch_provider_commands(monkeypatch, fake_run)
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
    if mechanism == "token_file_with_embedded_token":
        assert details["auth_mechanism"]["source"] == str(token_file.resolve())
    serialized = json.dumps(report)
    for value in secret_values:
        assert value not in serialized


def test_declared_token_file_blocks_embedded_token_fallback_when_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail closed when tokenFile is unreadable despite a stale inline token.

    :param monkeypatch: Pytest fixture denying reads of the token file.
    :param tmp_path: Scratch target, kubeconfig, and selected token path.

    :returns: None; assertions in the body validate the regression.
    """
    _access, _runtime = _access_modules()
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    token_file = tmp_path / "admin.token"
    token_file.write_text("secret\n", encoding="utf-8")
    target = _bind_target(_write_full_target(tmp_path, kubeconfig=kubeconfig))
    original_open = Path.open

    def denied_open(
        path: Path,
        *args: str,
        **kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> IO[str]:
        """Deny the selected token file while preserving other Path.open calls.

        :param path: Path being opened by the access reader.
        :param *args: Positional arguments forwarded to the helper under test.
        :param **kwargs: Keyword arguments forwarded to the fake command boundary.
        :returns: Original file handle for paths other than the selected token.
        :raises PermissionError: The selected token file is opened.

        """
        if path == token_file:
            raise PermissionError("unit denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied_open)
    with pytest.raises(ValueError, match="token_file_unavailable"):
        import_script_module("lib.python.access.kubernetes")._auth_mechanism(
            {"tokenFile": str(token_file), "token": "stale-secret"}, target
        )


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
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
    scenario: str,
    surface: str,
    reason: str,
) -> None:
    """Require each provider or cluster failure to block the aggregate gate.

    :param monkeypatch: Pytest fixture installing the selected scenario runner.
    :param target_file: Fixture target selecting all access surfaces.
    :param scenario: Native command behavior under test.
    :param surface: Report surface expected to be blocked.
    :param reason: Expected machine-readable blocking reason.

    :returns: None; assertions in the body validate the regression.
    """
    access, runtime = _access_modules()
    runner, _calls = _fake_access_runner(runtime, scenario)
    _patch_provider_commands(monkeypatch, runner)

    report = access.check_access(_load_target(target_file))

    assert report["status"] == "BLOCKED"
    assert report["surfaces"][surface]["status"] == "BLOCKED"
    assert report["surfaces"][surface]["reason"] == reason
    assert report["surfaces"][surface]["next_step"]


def test_dry_run_access_lists_probes_without_calling_live_tools(
    monkeypatch: pytest.MonkeyPatch, target_file: Path
) -> None:
    """List planned probes without allowing native command execution.

    :param monkeypatch: Pytest fixture replacing all native commands with failure.
    :param target_file: Fixture target selecting the planned surfaces.

    :returns: None; assertions in the body validate the regression.
    """
    access, _runtime = _access_modules()

    def fail_if_called(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> None:
        """Abort if a dry-run access report executes any native command.

        :param argv: Native command that would violate the dry-run boundary.

        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: None; the helper records behavior through its side effects.
        """
        pytest.fail(f"dry-run called a live tool: {argv}")

    _patch_provider_commands(monkeypatch, fail_if_called)
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep GitLab file token only in the child environment and out of output.

    :param monkeypatch: Pytest fixture selecting token source and fake commands.
    :param tmp_path: Scratch token file and target.

    :returns: None; assertions in the body validate the regression.
    """
    _access, runtime = _access_modules()
    _clear_token_env(monkeypatch)
    token = "unit-token-value"
    token_file = tmp_path / ".config" / "ci-skills" / "gitlab.example.test.token"
    token_file.parent.mkdir(parents=True)
    token_file.write_text(token + "\n", encoding="utf-8")
    monkeypatch.setenv("GITLAB_TOKEN", "ambient-token-value")
    target = _load_target(_write_token_target(tmp_path, token_file))
    observed_envs: MutableSequence[Optional[Mapping[str, str]]] = []

    def fake_run(
        argv: Sequence[str],
        **kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> runtime.CommandResult:
        """Answer GitLab access probes while recording child environments.

        :param argv: glab command requested by the access gate.
        :param **kwargs: Keyword arguments forwarded to the fake command boundary.
        :returns: Synthetic identity or runner API command result.

        """
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

    gitlab = import_script_module("lib.python.access.gitlab")
    monkeypatch.setattr(gitlab, "run_command", fake_run)

    surface = gitlab.gitlab_access(target)

    assert surface.status == "PASS"
    assert observed_envs
    assert all(env and env.get("GITLAB_TOKEN") == token for env in observed_envs)
    assert token not in json.dumps(surface.as_dict())


def test_gitlab_access_without_token_file_uses_glab_host_profile(
    monkeypatch: pytest.MonkeyPatch, target_file: Path
) -> None:
    """Use the selected glab profile when the target has no token file.

    :param monkeypatch: Pytest fixture clearing tokens and replacing commands.
    :param target_file: Fixture target without an explicit GitLab token file.

    :returns: None; assertions in the body validate the regression.
    """
    _access, runtime = _access_modules()
    _clear_token_env(monkeypatch)
    observed_envs: MutableSequence[Optional[Mapping[str, str]]] = []

    def fake_run(
        argv: Sequence[str],
        **kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> runtime.CommandResult:
        """Answer GitLab probes and expose any injected token environment.

        :param argv: glab command requested by the access gate.
        :param **kwargs: Keyword arguments forwarded to the fake command boundary.
        :returns: Synthetic identity or runner API command result.

        """
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

    gitlab = import_script_module("lib.python.access.gitlab")
    monkeypatch.setattr(gitlab, "run_command", fake_run)

    surface = gitlab.gitlab_access(_load_target(target_file))

    assert surface.status == "PASS"
    assert all(not env or "GITLAB_TOKEN" not in env for env in observed_envs)


@pytest.mark.parametrize(
    "payload",
    (
        '{"username":"robot$unit","username":"robot$other","password":"secret"}',
        '{"username":"robot$unit","password":"secret","unused":NaN}',
    ),
)
def test_harbor_credential_file_rejects_ambiguous_or_nonfinite_json(
    tmp_path: Path, payload: str
) -> None:
    """Reject invalid JSON before a Harbor credential can be selected.

    :param tmp_path: Scratch directory receiving the selected credential file.
    :param payload: Duplicate-key or nonfinite JSON credential input.

    :returns: None; assertions in the body validate the regression.
    """
    credentials = import_script_module("lib.python.access.harbor_credentials")
    target_module = import_script_module("lib.python.core.target")
    credential_file = tmp_path / "harbor.credentials.json"
    credential_file.write_text(payload, encoding="utf-8")
    target = target_module.HarborTarget(
        url="https://harbor.example.test",
        host="harbor.example.test",
        project="unit",
        credential_file=credential_file,
    )

    with pytest.raises(target_module.TargetError, match="credential_file_invalid"):
        credentials.resolve_harbor_credential(target)


@pytest.mark.parametrize(
    ("token_body", "expected_reason"),
    (
        (None, "credential_file_unavailable"),
        ("", "credential_file_invalid"),
        ("   \n", "credential_file_invalid"),
    ),
)
def test_gitlab_access_blocks_missing_or_empty_token_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    token_body: Optional[str],
    expected_reason: str,
) -> None:
    """Block absent or invalid selected token files before running glab.

    :param monkeypatch: Pytest fixture clearing tokens and rejecting commands.
    :param tmp_path: Scratch target and optional token file.
    :param token_body: Token-file text or absent-file case.
    :param expected_reason: Blocking reason expected from source validation.

    :returns: None; assertions in the body validate the regression.
    """
    _access, _runtime = _access_modules()
    _clear_token_env(monkeypatch)
    token_file = tmp_path / ".config" / "ci-skills" / "gitlab.example.test.token"
    token_file.parent.mkdir(parents=True)
    if token_body is not None:
        token_file.write_text(token_body, encoding="utf-8")
    target = _load_target(_write_token_target(tmp_path, token_file))
    calls = []

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> None:
        """Record and reject any glab call after invalid source selection.

        :param argv: Unexpected native command issued by the access gate.

        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: None; the helper records behavior through its side effects.
        """
        calls.append(tuple(str(part) for part in argv))
        pytest.fail("missing or empty token_file must block before glab runs")

    gitlab = import_script_module("lib.python.access.gitlab")
    monkeypatch.setattr(gitlab, "run_command", fake_run)

    surface = gitlab.gitlab_access(target)

    assert surface.status == "BLOCKED"
    assert surface.reason == expected_reason
    assert surface.identity is None
    assert calls == []


def test_kubectl_argv_binds_context_and_optional_kubeconfig(
    tmp_path: Path, target_file: Path
) -> None:
    """Require every Kubernetes command to carry selected context and file.

    :param tmp_path: Scratch path named as the target kubeconfig.
    :param target_file: Fixture target supplying other provider authorities.

    :returns: None; assertions in the body validate the regression.
    """
    _access, _runtime = _access_modules()
    target_mod = import_script_module("lib.python.core.target")
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

    kubernetes = import_script_module("lib.python.access.kubernetes")
    argv = kubernetes.kubectl_argv(target, "auth", "can-i", "*", "*")

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
