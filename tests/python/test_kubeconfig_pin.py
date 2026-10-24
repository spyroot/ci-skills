"""Tests that a verified Kubernetes target stays the one that gets read.

Pinning the kubeconfig FILENAME is not pinning the target. The gate verifies
the API server once, then every later command reopens that same mutable path
and re-resolves the context by name, so a file rewritten in between redirects
the commands with no second comparison.

The refusal has to BLOCK. Raising it inside the per-resource read path would be
caught there and degraded into a PARTIAL report with resources silently
missing, which reads as a healthy-but-empty cluster.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.python.core.target import Target

import pytest
from conftest import authenticated_kubernetes_service, import_script_module

ORIGINAL = "apiVersion: v1\nkind: Config\n"
REWRITTEN = "apiVersion: v1\nkind: Config\nchanged: true\n"


def _modules() -> tuple[ModuleType, ModuleType, ModuleType]:
    """Load credential, access, and target modules used in pinning checks.

    :returns: Credential binder, access helper, and target parser modules.
    """
    return (
        import_script_module("lib.python.access.credentials"),
        import_script_module("lib.python.access.kubernetes"),
        import_script_module("lib.python.core.target"),
    )


def _kubernetes_credentials() -> ModuleType:
    """Load the Kubernetes credential module that owns kubeconfig pinning.

    :returns: Credential source and digest verification module.
    """
    return import_script_module("lib.python.access.kubernetes_credentials")


def _target(tmp_path: Path, kubeconfig: Path) -> Target:
    """Bind a three-surface target to one selected kubeconfig file.

    :param tmp_path: Directory receiving the target TOML file.
    :param kubeconfig: File path recorded in the target and digested on bind.
    :returns: Bound target with a pinned kubeconfig digest.
    """
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
    """Write the original kubeconfig bytes for pinning tests.

    :param tmp_path: Scratch directory receiving the config file.
    :returns: Path to the written kubeconfig.
    """
    path = tmp_path / "kubeconfig"
    path.write_text(ORIGINAL, encoding="utf-8")
    return path


def test_binding_records_a_digest_for_every_kubeconfig(
    tmp_path: Path, kubeconfig: Path
) -> None:
    """Record one digest per selected kubeconfig and accept unchanged bytes.

    :param tmp_path: Directory receiving the target TOML file.
    :param kubeconfig: Fixture kubeconfig whose bytes are pinned.

    :returns: None; assertions in the body validate the regression.
    """
    _credentials, _access, _target_module = _modules()
    bound = _target(tmp_path, kubeconfig)

    assert len(bound.sources.kubeconfig_digests) == len(bound.sources.kubeconfig_files)
    assert all(len(digest) == 64 for digest in bound.sources.kubeconfig_digests)
    _kubernetes_credentials().assert_kubeconfig_unchanged(bound.sources)


def test_an_untouched_kubeconfig_does_not_block(
    tmp_path: Path, kubeconfig: Path
) -> None:
    """Allow read-only access to a pinned kubeconfig before command build.

    :param tmp_path: Directory receiving the target TOML file.
    :param kubeconfig: Fixture kubeconfig read without modification.

    :returns: None; assertions in the body validate the regression.
    """
    _credentials, access, _target_module = _modules()
    bound = _target(tmp_path, kubeconfig)

    kubeconfig.read_bytes()

    _kubernetes_credentials().assert_kubeconfig_unchanged(bound.sources)
    assert "--kubeconfig" in access.kubectl_argv(bound, "get", "nodes")


def test_a_kubeconfig_rewritten_after_verification_blocks(
    tmp_path: Path, kubeconfig: Path
) -> None:
    """Block if kubeconfig bytes change after source binding.

    :param tmp_path: Directory receiving the target TOML file.
    :param kubeconfig: Fixture config rewritten after digest capture.

    :returns: None; assertions in the body validate the regression.
    """
    _credentials, _access, target_module = _modules()
    bound = _target(tmp_path, kubeconfig)

    kubeconfig.write_text(REWRITTEN, encoding="utf-8")

    with pytest.raises(
        target_module.TargetError, match="kubeconfig_changed_during_invocation"
    ):
        _kubernetes_credentials().assert_kubeconfig_unchanged(bound.sources)


def test_every_kubernetes_command_rechecks_before_it_is_built(
    tmp_path: Path, kubeconfig: Path
) -> None:
    """Check pinned bytes again for each subsequent kubectl command.

    :param tmp_path: Directory receiving the target TOML file.
    :param kubeconfig: Fixture config changed between two command builds.

    :returns: None; assertions in the body validate the regression.
    """
    _credentials, access, _target_module = _modules()
    bound = _target(tmp_path, kubeconfig)
    assert access.kubectl_argv(bound, "get", "nodes")

    kubeconfig.write_text(REWRITTEN, encoding="utf-8")

    with pytest.raises(Exception, match="kubeconfig_changed_during_invocation"):
        access.kubectl_argv(bound, "get", "pods")


def test_a_changed_kubeconfig_blocks_the_collector_rather_than_degrading_it(
    tmp_path: Path, kubeconfig: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raise on kubeconfig drift before storage collection can appear partial.

    :param tmp_path: Directory receiving the target TOML file.
    :param kubeconfig: Fixture config rewritten after binding.
    :param monkeypatch: Pytest fixture accepted by this test but not used.

    :returns: None; assertions in the body validate the regression.
    """
    _credentials, _access, _target_module = _modules()
    collect = import_script_module("lib.python.core.kubernetes.storage")
    bound = _target(tmp_path, kubeconfig)
    service = authenticated_kubernetes_service(bound)

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
            state=service.state,
        )


def test_an_unbound_target_is_not_checked(tmp_path: Path, kubeconfig: Path) -> None:
    """Skip digest comparison for a target that never bound sources.

    :param tmp_path: Directory receiving an unbound target file.
    :param kubeconfig: Fixture path named in that target.

    :returns: None; assertions in the body validate the regression.
    """
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
        f'kubeconfig = "{kubeconfig}"\n',
        encoding="utf-8",
    )
    unbound = target_module.load_target(path)

    _kubernetes_credentials().assert_kubeconfig_unchanged(unbound.sources)


def _target_with_search_path(tmp_path: Path, entries: str) -> tuple[ModuleType, Path]:
    """Write a target whose plural kubeconfig declaration is supplied verbatim.

    :param tmp_path: Directory receiving the target TOML file.
    :param entries: TOML value written as ``kubeconfigs``.
    :returns: Target parser module and path to the written file.
    """
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


def test_a_declared_search_path_is_read_in_order(tmp_path: Path) -> None:
    """Preserve a two-file kubeconfig search path in declared order.

    :param tmp_path: Directory receiving both configs and the target file.

    :returns: None; assertions in the body validate the regression.
    """
    overlay = tmp_path / "overlay"
    overlay.write_text(ORIGINAL, encoding="utf-8")
    real = tmp_path / "real"
    real.write_text(REWRITTEN, encoding="utf-8")
    target_module, path = _target_with_search_path(tmp_path, f'["{overlay}", "{real}"]')

    target = target_module.load_target(path)

    assert target.kubernetes.kubeconfigs == (overlay, real)
    assert target.kubernetes.kubeconfig is None


def test_the_declared_search_path_beats_the_ambient_kubeconfig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prefer target-declared kubeconfigs to ambient KUBECONFIG.

    :param tmp_path: Directory receiving declared/ambient configs and target.
    :param monkeypatch: Pytest fixture selecting the ambient config.

    :returns: None; assertions in the body validate the regression.
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
def test_a_malformed_search_path_is_refused(
    tmp_path: Path, entries: str, message: str
) -> None:
    """Reject scalar, empty, blank, or relative plural kubeconfig paths.

    :param tmp_path: Directory receiving the malformed target.
    :param entries: Invalid TOML value used for ``kubeconfigs``.
    :param message: Expected target validation error fragment.

    :returns: None; assertions in the body validate the regression.
    """
    target_module, path = _target_with_search_path(tmp_path, entries)

    with pytest.raises(target_module.TargetError, match=message):
        target_module.load_target(path)


def test_a_duplicated_search_path_entry_is_refused(tmp_path: Path) -> None:
    """Reject duplicate kubeconfig files in one declared search path.

    :param tmp_path: Directory receiving the config and target.

    :returns: None; assertions in the body validate the regression.
    """
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text(ORIGINAL, encoding="utf-8")
    target_module, path = _target_with_search_path(
        tmp_path, f'["{kubeconfig}", "{kubeconfig}"]'
    )

    with pytest.raises(target_module.TargetError, match="kubeconfigs"):
        target_module.load_target(path)


def test_declaring_both_kubeconfig_forms_is_refused(tmp_path: Path) -> None:
    """Reject simultaneous singular and plural kubeconfig declarations.

    :param tmp_path: Directory receiving the config and conflicting target.

    :returns: None; assertions in the body validate the regression.
    """
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
