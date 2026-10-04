"""A diagnostic uses only the authorities declared for its command."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from tests.python.conftest import import_script_module


def _target(tmp_path, body: str):
    path = tmp_path / "target.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _args(path, *, dry_run=False, namespace=None, job_url=None):
    return SimpleNamespace(
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


def _unexpected(*_args, **_kwargs):
    raise AssertionError("unselected authority was contacted")


def test_ceph_collector_uses_only_kubernetes_authority(tmp_path, monkeypatch, capsys):
    """A project with no GitHub or GitLab target can read Ceph through Kubernetes."""
    cli = import_script_module("core.cli")
    access = import_script_module("core.access")
    credentials = import_script_module("core.credentials")
    path = _target(
        tmp_path,
        '[kubernetes]\ncontext = "unit"\n'
        'server = "https://api.cluster.example.test:6443"\n',
    )
    monkeypatch.setattr(access, "github_access", _unexpected)
    monkeypatch.setattr(access, "gitlab_access", _unexpected)
    monkeypatch.setattr(credentials, "_token_source", _unexpected)
    monkeypatch.setattr(
        credentials,
        "_kubernetes_source",
        lambda _target: (credentials.CredentialSource("file:/unit/kubeconfig", {}), ()),
    )
    monkeypatch.setattr(
        credentials,
        "resolve_skill_identity",
        lambda _revision: {"revision": {"value": "a" * 40}},
    )
    monkeypatch.setattr(
        access,
        "kubernetes_access",
        lambda _target, *, cilium=False: access.Surface(
            "kubernetes", "PASS", "unit-admin", "unit -> api", ["live-read"], None
        ),
    )
    seen = []

    def collect_ceph_cluster(target, _args):
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


def test_gitlab_job_does_not_require_cluster_or_github(tmp_path, monkeypatch, capsys):
    """A GitLab-only target does not resolve a kubeconfig or GitHub token."""
    cli = import_script_module("core.cli")
    access = import_script_module("core.access")
    credentials = import_script_module("core.credentials")
    path = _target(tmp_path, '[gitlab]\nurl = "https://gitlab.example.test"\n')
    monkeypatch.setattr(access, "github_access", _unexpected)
    monkeypatch.setattr(access, "kubernetes_access", _unexpected)
    monkeypatch.setattr(credentials, "_kubernetes_source", _unexpected)
    monkeypatch.setattr(
        credentials,
        "_token_source",
        lambda *_args, **_kwargs: credentials.CredentialSource("env:GITLAB_TOKEN", {}),
    )
    monkeypatch.setattr(
        credentials,
        "resolve_skill_identity",
        lambda _revision: {"revision": {"value": "a" * 40}},
    )
    monkeypatch.setattr(
        access,
        "gitlab_access",
        lambda _target: access.Surface(
            "gitlab", "PASS", "unit-admin", "gitlab.example.test", ["live-read"], None
        ),
    )

    def collect_gitlab_job(target, _args):
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


def test_selected_target_malformed_blocks_without_fallback(tmp_path, capsys):
    """A bad selected Kubernetes origin cannot fall back to another target."""
    cli = import_script_module("core.cli")
    path = _target(
        tmp_path,
        '[kubernetes]\ncontext = "unit"\n'
        'server = "http://api.cluster.example.test:6443"\n',
    )

    def collect_ceph_cluster(_target, _args):
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


def test_full_access_check_still_requires_all_three_targets(tmp_path):
    """The three-surface receipt cannot pass from a Kubernetes-only target."""
    target = import_script_module("core.target")
    path = _target(
        tmp_path,
        '[kubernetes]\ncontext = "unit"\n'
        'server = "https://api.cluster.example.test:6443"\n',
    )
    with pytest.raises(target.TargetError, match="target must contain"):
        target.load_target(path)
