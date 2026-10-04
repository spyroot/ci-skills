"""Tests that a verified Kubernetes target stays the one that gets read.

Pinning the kubeconfig FILENAME is not pinning the target. The gate verifies
the API server once, then every later command reopens that same mutable path
and re-resolves the context by name, so a file rewritten in between redirects
the commands with no second comparison.

The refusal has to BLOCK. Raising it inside the per-resource read path would be
caught there and degraded into a PARTIAL report with resources silently
missing, which reads as a healthy-but-empty cluster.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.python.conftest import import_script_module

ORIGINAL = "apiVersion: v1\nkind: Config\n"
REWRITTEN = "apiVersion: v1\nkind: Config\nchanged: true\n"


def _modules():
    return (
        import_script_module("core.credentials"),
        import_script_module("core.access"),
        import_script_module("core.target"),
    )


def _target(tmp_path: Path, kubeconfig: Path):
    credentials, _access, target_module = _modules()
    path = tmp_path / "target.toml"
    path.write_text(
        "[github]\n"
        'host = "github.example.test"\n'
        'repository = "unit/repo"\n'
        "\n"
        "[gitlab]\n"
        'url = "https://gitlab.example.test"\n'
        "\n"
        "[kubernetes]\n"
        'context = "unit-context"\n'
        'server = "https://api.cluster.example.test:6443"\n'
        f'kubeconfig = "{kubeconfig}"\n',
        encoding="utf-8",
    )
    return credentials.bind_sources(target_module.load_target(path), revision="a" * 40)


@pytest.fixture
def kubeconfig(tmp_path: Path) -> Path:
    path = tmp_path / "kubeconfig"
    path.write_text(ORIGINAL, encoding="utf-8")
    return path


def test_binding_records_a_digest_for_every_kubeconfig(tmp_path, kubeconfig):
    """The digest is what makes "same verified target" checkable."""
    credentials, _access, _target_module = _modules()
    bound = _target(tmp_path, kubeconfig)

    assert len(bound.sources.kubeconfig_digests) == len(bound.sources.kubeconfig_files)
    assert all(len(digest) == 64 for digest in bound.sources.kubeconfig_digests)
    credentials.assert_kubeconfig_unchanged(bound.sources)


def test_an_untouched_kubeconfig_does_not_block(tmp_path, kubeconfig):
    """Reading the file must not look like changing it."""
    credentials, access, _target_module = _modules()
    bound = _target(tmp_path, kubeconfig)

    kubeconfig.read_bytes()

    credentials.assert_kubeconfig_unchanged(bound.sources)
    assert "--kubeconfig" in access.kubectl_argv(bound, "get", "nodes")


def test_a_kubeconfig_rewritten_after_verification_blocks(tmp_path, kubeconfig):
    """The window between verification and collection must not stay open."""
    credentials, _access, _target_module = _modules()
    bound = _target(tmp_path, kubeconfig)

    kubeconfig.write_text(REWRITTEN, encoding="utf-8")

    with pytest.raises(
        credentials.TargetError, match="kubeconfig_changed_during_invocation"
    ):
        credentials.assert_kubeconfig_unchanged(bound.sources)


def test_every_kubernetes_command_rechecks_before_it_is_built(tmp_path, kubeconfig):
    """The check belongs at command construction, not once at gate time."""
    _credentials, access, _target_module = _modules()
    bound = _target(tmp_path, kubeconfig)
    assert access.kubectl_argv(bound, "get", "nodes")

    kubeconfig.write_text(REWRITTEN, encoding="utf-8")

    with pytest.raises(Exception, match="kubeconfig_changed_during_invocation"):
        access.kubectl_argv(bound, "get", "pods")


def test_a_changed_kubeconfig_blocks_the_collector_rather_than_degrading_it(
    tmp_path, kubeconfig, monkeypatch
):
    """A PARTIAL report with missing resources reads as an empty cluster."""
    _credentials, _access, _target_module = _modules()
    collect = import_script_module("core.collect")
    bound = _target(tmp_path, kubeconfig)

    kubeconfig.write_text(REWRITTEN, encoding="utf-8")

    with pytest.raises(Exception, match="kubeconfig_changed_during_invocation"):
        collect.collect_storage(
            bound,
            SimpleNamespace(
                namespace="all",
                node=None,
                storage_class=None,
                phase="all",
                search=None,
            ),
        )


def test_an_unbound_target_is_not_checked(tmp_path, kubeconfig):
    """A dry run never bound sources, so there is nothing to compare."""
    credentials, _access, target_module = _modules()
    path = tmp_path / "target.toml"
    path.write_text(
        "[github]\n"
        'host = "github.example.test"\n'
        'repository = "unit/repo"\n'
        "\n"
        "[gitlab]\n"
        'url = "https://gitlab.example.test"\n'
        "\n"
        "[kubernetes]\n"
        'context = "unit-context"\n'
        'server = "https://api.cluster.example.test:6443"\n'
        f'kubeconfig = "{kubeconfig}"\n',
        encoding="utf-8",
    )
    unbound = target_module.load_target(path)

    credentials.assert_kubeconfig_unchanged(unbound.sources)


def _target_with_search_path(tmp_path: Path, entries: str):
    """Write a target declaring `kubernetes.kubeconfigs` verbatim."""
    _credentials, _access, target_module = _modules()
    path = tmp_path / "target.toml"
    path.write_text(
        "[github]\n"
        'host = "github.example.test"\n'
        'repository = "unit/repo"\n'
        "\n"
        "[gitlab]\n"
        'url = "https://gitlab.example.test"\n'
        "\n"
        "[kubernetes]\n"
        'context = "unit-context"\n'
        'server = "https://api.cluster.example.test:6443"\n'
        f"kubeconfigs = {entries}\n",
        encoding="utf-8",
    )
    return target_module, path


def test_a_declared_search_path_is_read_in_order(tmp_path):
    """Two files, kubectl's own order, with no environment variable involved."""
    overlay = tmp_path / "overlay"
    overlay.write_text(ORIGINAL, encoding="utf-8")
    real = tmp_path / "real"
    real.write_text(REWRITTEN, encoding="utf-8")
    target_module, path = _target_with_search_path(tmp_path, f'["{overlay}", "{real}"]')

    target = target_module.load_target(path)

    assert target.kubernetes.kubeconfigs == (overlay, real)
    assert target.kubernetes.kubeconfig is None


def test_the_declared_search_path_beats_the_ambient_kubeconfig(tmp_path, monkeypatch):
    """Otherwise a cold run aims at whatever the shell happens to export.

    This is the guard `references/access.md` tells operators to rely on: with
    nothing declared, resolution ends at the default kubeconfig, which is
    usually a different cluster.
    """
    credentials, _access, _target_module = _modules()
    declared = tmp_path / "declared"
    declared.write_text(ORIGINAL, encoding="utf-8")
    ambient = tmp_path / "ambient"
    ambient.write_text(REWRITTEN, encoding="utf-8")
    monkeypatch.setenv("KUBECONFIG", str(ambient))
    target_module, path = _target_with_search_path(tmp_path, f'["{declared}"]')

    bound = credentials.bind_sources(target_module.load_target(path), revision="a" * 40)

    assert bound.sources.kubernetes.reference == "target:kubernetes.kubeconfigs"
    assert bound.sources.kubeconfig_files == (declared,)
    assert ambient not in bound.sources.kubeconfig_files


@pytest.mark.parametrize(
    ("entries", "message"),
    (
        ('"/not/a/list"', "kubeconfigs"),
        ("[]", "kubeconfigs"),
        ('[""]', "kubeconfigs"),
        ('["relative/kubeconfig"]', "kubeconfigs"),
    ),
)
def test_a_malformed_search_path_is_refused(tmp_path, entries, message):
    """Each rejection branch must block rather than silently drop an entry."""
    target_module, path = _target_with_search_path(tmp_path, entries)

    with pytest.raises(target_module.TargetError, match=message):
        target_module.load_target(path)


def test_a_duplicated_search_path_entry_is_refused(tmp_path):
    """A repeated file makes the digest set ambiguous about what was read."""
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text(ORIGINAL, encoding="utf-8")
    target_module, path = _target_with_search_path(
        tmp_path, f'["{kubeconfig}", "{kubeconfig}"]'
    )

    with pytest.raises(target_module.TargetError, match="kubeconfigs"):
        target_module.load_target(path)


def test_declaring_both_kubeconfig_forms_is_refused(tmp_path):
    """Two declarations of the same thing have no defined precedence."""
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text(ORIGINAL, encoding="utf-8")
    _credentials, _access, target_module = _modules()
    path = tmp_path / "target.toml"
    path.write_text(
        "[github]\n"
        'host = "github.example.test"\n'
        'repository = "unit/repo"\n'
        "\n"
        "[gitlab]\n"
        'url = "https://gitlab.example.test"\n'
        "\n"
        "[kubernetes]\n"
        'context = "unit-context"\n'
        'server = "https://api.cluster.example.test:6443"\n'
        f'kubeconfig = "{kubeconfig}"\n'
        f'kubeconfigs = ["{kubeconfig}"]\n',
        encoding="utf-8",
    )

    with pytest.raises(target_module.TargetError, match="not both"):
        target_module.load_target(path)
