"""Offline tests for explicit kubernetes.kubeconfigs target selection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import import_script_module


def _toml_string(value: str | Path) -> str:
    """Return one TOML basic string for a synthetic path."""
    return json.dumps(str(value))


def _write_kubeconfig(
    path: Path,
    *,
    context: str = "unit-context",
    server: str = "https://api.cluster.example.test:6443",
    cluster: str = "unit-cluster",
) -> Path:
    """Write a minimal kubeconfig that maps one context to one API server."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "apiVersion: v1\n"
            "kind: Config\n"
            "clusters:\n"
            f"- name: {cluster}\n"
            "  cluster:\n"
            f"    server: {server}\n"
            "contexts:\n"
            f"- name: {context}\n"
            "  context:\n"
            f"    cluster: {cluster}\n"
            "    user: unit-user\n"
            "users:\n"
            "- name: unit-user\n"
            "  user: {}\n"
        ),
        encoding="utf-8",
    )
    return path


def _write_target(
    path: Path,
    *,
    context: str = "unit-context",
    server: str = "https://api.cluster.example.test:6443",
    kubeconfigs: list[Path] | None = None,
    kubeconfig: Path | None = None,
) -> Path:
    """Write a target with a singular or list kubeconfig selector."""
    path.parent.mkdir(parents=True, exist_ok=True)
    kubeconfig_line = f"kubeconfig = {_toml_string(kubeconfig)}\n" if kubeconfig else ""
    kubeconfigs_line = ""
    if kubeconfigs is not None:
        kubeconfigs_line = (
            "kubeconfigs = ["
            + ", ".join(_toml_string(item) for item in kubeconfigs)
            + "]\n"
        )
    path.write_text(
        (
            "[github]\n"
            'host = "github.example.test"\n'
            'repository = "unit/repo"\n'
            "\n"
            "[gitlab]\n"
            'url = "https://gitlab.example.test"\n'
            "\n"
            "[kubernetes]\n"
            f"context = {_toml_string(context)}\n"
            f"server = {_toml_string(server)}\n"
            f"{kubeconfig_line}"
            f"{kubeconfigs_line}"
        ),
        encoding="utf-8",
    )
    return path


def _target() -> Any:
    """Import the target parser under test."""
    return import_script_module("core.target")


def test_kubeconfigs_selects_one_context_server_match_from_two_files(
    tmp_path: Path,
) -> None:
    """The target selects the sole file that matches both context and server."""
    nonmatching = _write_kubeconfig(
        tmp_path / "first.yaml", server="https://api.other.example.test:6443"
    )
    matching = _write_kubeconfig(tmp_path / "second.yaml")
    target_path = _write_target(
        tmp_path / "target.toml", kubeconfigs=[nonmatching, matching]
    )

    loaded = _target().load_target(target_path)

    assert loaded.kubernetes.context == "unit-context"
    assert loaded.kubernetes.server == "https://api.cluster.example.test:6443"
    assert loaded.kubernetes.kubeconfig == matching.resolve()
    assert loaded.kubernetes_source_reference == (
        f"kubeconfig-list:2 -> file:{matching.resolve()}"
    )


@pytest.mark.parametrize("matching_count", (0, 2))
def test_kubeconfigs_require_exactly_one_context_server_match(
    tmp_path: Path, matching_count: int
) -> None:
    """Zero or multiple matching kubeconfigs block target resolution."""
    if matching_count == 0:
        first = _write_kubeconfig(
            tmp_path / "first.yaml", server="https://api.one.example.test:6443"
        )
        second = _write_kubeconfig(
            tmp_path / "second.yaml", server="https://api.two.example.test:6443"
        )
    else:
        first = _write_kubeconfig(tmp_path / "first.yaml")
        second = _write_kubeconfig(tmp_path / "second.yaml")
    target_path = _write_target(tmp_path / "target.toml", kubeconfigs=[first, second])

    with pytest.raises(_target().TargetError, match="kubeconfig_target_not_unique"):
        _target().load_target(target_path)


def test_malformed_kubeconfig_candidate_blocks_without_substitution(
    tmp_path: Path,
) -> None:
    """An invalid earlier candidate blocks instead of selecting a later match."""
    malformed = tmp_path / "malformed.yaml"
    malformed.write_bytes(b"\xff\xfe\x00not utf-8")
    matching = _write_kubeconfig(tmp_path / "matching.yaml")
    target_path = _write_target(
        tmp_path / "target.toml", kubeconfigs=[malformed, matching]
    )

    with pytest.raises(_target().TargetError, match="kubeconfig_candidate_invalid"):
        _target().load_target(target_path)


def test_unavailable_kubeconfig_candidate_blocks_without_substitution(
    tmp_path: Path,
) -> None:
    """An unreadable-style candidate blocks instead of selecting a later match."""
    directory_candidate = tmp_path / "not-a-file.yaml"
    directory_candidate.mkdir()
    matching = _write_kubeconfig(tmp_path / "matching.yaml")
    target_path = _write_target(
        tmp_path / "target.toml", kubeconfigs=[directory_candidate, matching]
    )

    with pytest.raises(_target().TargetError, match="kubeconfig_candidate_unavailable"):
        _target().load_target(target_path)


def test_kubeconfigs_conflict_with_singular_kubeconfig(tmp_path: Path) -> None:
    """A target may not declare both singular and list kubeconfig sources."""
    singular = _write_kubeconfig(tmp_path / "singular.yaml")
    listed = _write_kubeconfig(tmp_path / "listed.yaml")
    target_path = _write_target(
        tmp_path / "target.toml", kubeconfig=singular, kubeconfigs=[listed]
    )

    with pytest.raises(_target().TargetError, match="kubeconfig_source_conflict"):
        _target().load_target(target_path)


def test_kubeconfig_list_source_provenance_reaches_credential_binding(
    tmp_path: Path,
) -> None:
    """Credential binding preserves the selected list index and file path."""
    credentials = import_script_module("core.credentials")
    nonmatching = _write_kubeconfig(tmp_path / "first.yaml", context="other-context")
    matching = _write_kubeconfig(tmp_path / "second.yaml")
    target_path = _write_target(
        tmp_path / "target.toml", kubeconfigs=[nonmatching, matching]
    )

    loaded = _target().load_target(target_path)
    source, files = credentials._kubernetes_source(loaded)

    assert files == (matching.resolve(),)
    assert source.reference == f"kubeconfig-list:2 -> file:{matching.resolve()}"
    assert source.environment == {"KUBECONFIG": str(matching.resolve())}
