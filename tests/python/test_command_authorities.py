"""A diagnostic uses only the authorities declared for its command.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from unittest.mock import Mock

import pytest
from conftest import import_script_module

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject
    from lib.python.core.target import Target


def _target(tmp_path: Path, body: str) -> Path:
    """Write one authority-only TOML target for a selection test.

    :param tmp_path: Pytest directory that owns the target file.
    :param body: Exact TOML tables under test.
    :returns: Written target path.
    """
    path = tmp_path / "target.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _args(
    path: Path,
    *,
    dry_run: bool = False,
    namespace: Optional[str] = None,
    job_url: Optional[str] = None,
) -> Namespace:
    """Construct CLI arguments for a selected authority operation.

    :param path: Target TOML path injected into the argument namespace.
    :param dry_run: Whether to plan without live reads.
    :param namespace: Optional Ceph namespace selector.
    :param job_url: Optional GitLab job URL selector.
    :returns: Namespace with common output/target settings and operation fields.
    """
    return Namespace(
        target=str(path),
        binding=None,
        json=True,
        yaml=False,
        human=False,
        describe=False,
        dry_run=dry_run,
        output_dir=None,
        revision=None,
        publication=False,
        receipt_out=None,
        namespace=namespace,
        job_url=job_url,
    )


def _unexpected() -> Mock:
    """Create a fail-fast double for an authority excluded by the command.

    :returns: Mock raising AssertionError whenever the replaced boundary is called.
    """
    return Mock(side_effect=AssertionError("unselected authority was contacted"))


def test_ceph_collector_uses_only_kubernetes_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Require Ceph execution to bind only Kubernetes credentials and access.

    :param tmp_path: Directory for a Kubernetes-only target.
    :param monkeypatch: Fixture replacing unrelated providers and native reads.
    :param capsys: Fixture capturing the CLI's JSON access receipt.

    :returns: None; assertions in the body validate the regression.
    """
    cli = import_script_module("lib.python.core.cli")
    access = import_script_module("lib.python.access.access")
    shared = import_script_module("lib.python.access.shared")
    credentials = import_script_module("lib.python.access.credentials")
    sources = import_script_module("lib.python.access.sources")
    path = _target(
        tmp_path,
        '[kubernetes]\ncontext = "unit"\n'
        'server = "https://api.cluster.example.test:6443"\n',
    )
    monkeypatch.setattr(access, "github_access", _unexpected())
    monkeypatch.setattr(access, "gitlab_access", _unexpected())
    monkeypatch.setattr(credentials, "_token_source", _unexpected())
    monkeypatch.setattr(
        credentials,
        "_kubernetes_source",
        lambda _target: (sources.CredentialSource("file:/unit/kubeconfig", {}), ()),
    )
    monkeypatch.setattr(
        credentials,
        "resolve_skill_identity",
        lambda _revision: {"revision": {"value": "a" * 40}},
    )
    monkeypatch.setattr(
        access,
        "kubernetes_access",
        lambda _target, *, cilium=False: shared.Surface(
            "kubernetes", "PASS", "unit-admin", "unit -> api", ["live-read"], None
        ),
    )
    seen = []

    def collect_ceph_cluster(target: Target, _args: Namespace) -> JSONObject:
        """Capture the target passed to a fake Ceph collector.

        :param target: Bound target whose active surfaces are asserted by the test.
        :param _args: Collector options unused by this fake.
        :returns: Minimal successful Ceph report.
        """
        seen.append(target)
        return {"kind": "ceph_cluster", "status": "PASS", "records": [], "errors": []}

    assert cli.execute(_args(path, namespace="storage"), collect_ceph_cluster) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["access"]["credential_sources"] == {
        "kubernetes": "file:/unit/kubeconfig"
    }
    assert data["access"]["identities"] == {"kubernetes": "unit-admin"}
    assert len(seen) == 1
    assert seen[0].active_surfaces == ("kubernetes",)
    assert seen[0].github is None and seen[0].gitlab is None


def test_gitlab_job_does_not_require_cluster_or_github(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Require GitLab job execution to avoid Kubernetes and GitHub sources.

    :param tmp_path: Directory for a GitLab-only target.
    :param monkeypatch: Fixture replacing provider access and source resolution.
    :param capsys: Fixture capturing the CLI's JSON access receipt.

    :returns: None; assertions in the body validate the regression.
    """
    cli = import_script_module("lib.python.core.cli")
    access = import_script_module("lib.python.access.access")
    shared = import_script_module("lib.python.access.shared")
    credentials = import_script_module("lib.python.access.credentials")
    sources = import_script_module("lib.python.access.sources")
    path = _target(tmp_path, '[gitlab]\nurl = "https://gitlab.example.test"\n')
    monkeypatch.setattr(access, "github_access", _unexpected())
    monkeypatch.setattr(access, "kubernetes_access", _unexpected())
    monkeypatch.setattr(credentials, "_kubernetes_source", _unexpected())
    monkeypatch.setattr(
        credentials,
        "_token_source",
        lambda *_args, **_kwargs: sources.CredentialSource("env:GITLAB_TOKEN", {}),
    )
    monkeypatch.setattr(
        credentials,
        "resolve_skill_identity",
        lambda _revision: {"revision": {"value": "a" * 40}},
    )
    monkeypatch.setattr(
        access,
        "gitlab_access",
        lambda _target: shared.Surface(
            "gitlab", "PASS", "unit-admin", "gitlab.example.test", ["live-read"], None
        ),
    )

    def collect_gitlab_job(target: Target, _args: Namespace) -> JSONObject:
        """Assert non-GitLab authorities are absent from the collector target.

        :param target: Bound target selected for the GitLab job.
        :param _args: Collector options unused by this fake.
        :returns: Minimal successful GitLab job report.
        """
        assert target.github is None and target.kubernetes is None
        return {"kind": "gitlab_job", "status": "PASS", "records": [], "errors": []}

    assert (
        cli.execute(
            _args(path, job_url="https://gitlab.example.test/unit/repo/-/jobs/1"),
            collect_gitlab_job,
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["access"]["credential_sources"] == {"gitlab": "env:GITLAB_TOKEN"}
    assert data["access"]["identities"] == {"gitlab": "unit-admin"}


def test_selected_target_malformed_blocks_without_fallback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Require an invalid selected cluster URL to block before collection.

    :param tmp_path: Directory for a target with an HTTP cluster origin.
    :param capsys: Fixture capturing the blocked JSON report.

    :returns: None; assertions in the body validate the regression.
    """
    cli = import_script_module("lib.python.core.cli")
    path = _target(
        tmp_path,
        '[kubernetes]\ncontext = "unit"\n'
        'server = "http://api.cluster.example.test:6443"\n',
    )

    def collect_ceph_cluster(_target: Target, _args: Namespace) -> None:
        """Fail if the Ceph collector runs after target validation fails.

        :param _target: Invalid target, unused because this fake must never run.
        :param _args: Collector options, unused because this fake must never run.
        :returns: None; the helper records behavior through its side effects.
        :raises AssertionError: Always if invoked.

        """
        raise AssertionError("collector ran after malformed target")

    assert (
        cli.execute(
            _args(path, dry_run=True, namespace="storage"), collect_ceph_cluster
        )
        == 2
    )
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "BLOCKED"
    assert data["errors"][0]["source"] == "target"
    assert "kubernetes.server" in data["errors"][0]["reason"]


def test_full_access_check_still_requires_all_three_targets(tmp_path: Path) -> None:
    """Keep the full access receipt dependent on all declared authorities.

    :param tmp_path: Directory for a Kubernetes-only target that must be rejected.

    :returns: None; assertions in the body validate the regression.
    """
    target = import_script_module("lib.python.core.target")
    path = _target(
        tmp_path,
        '[kubernetes]\ncontext = "unit"\n'
        'server = "https://api.cluster.example.test:6443"\n',
    )
    with pytest.raises(target.TargetError, match="target must contain"):
        target.load_target(path)
