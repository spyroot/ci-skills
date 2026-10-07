"""Resolve an explicit per-project diagnostic target and kubeconfig selector."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import tomllib

from .catalog import AUTHORITIES, PROJECT_DIR, TARGET_FILENAME
from .runtime import error_class, run_command_tail
from .target import Target, TargetError, load_target

BINDING_ENV = "K8S_ADMIN_DIAGNOSTICS_BINDING"
TARGET_ENV = "CI_SKILLS_TARGET"
DEFAULT_TARGET = Path(PROJECT_DIR) / TARGET_FILENAME
ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def target_candidates() -> list[tuple[str, Path]]:
    """Return the declared target files in their precedence order.
    :return:
    """
    candidates: list[tuple[str, Path]] = []
    configured = (os.environ.get(TARGET_ENV) or "").strip()
    if configured:
        candidates.append((f"env:{TARGET_ENV}", Path(configured).expanduser()))
    candidates.append(("project", Path.cwd() / DEFAULT_TARGET))
    candidates.append(("user", Path.home() / DEFAULT_TARGET))
    return candidates


def resolve_target_file(explicit: str | None) -> tuple[Path, str]:
    """

    :param explicit:
    :return:
    """
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise TargetError(f"target_file_missing:{path}")
        return path, "argv:--target"
    if TARGET_ENV in os.environ:
        configured = os.environ[TARGET_ENV].strip()
        if not configured:
            raise TargetError(f"target_environment_empty:{TARGET_ENV}")
        path = Path(configured).expanduser()
        if not path.is_file():
            raise TargetError(f"target_file_missing:{path}")
        return path, f"env:{TARGET_ENV}"
    for source, path in target_candidates():
        if path.is_file():
            return path, source
    searched = ", ".join(str(path) for _source, path in target_candidates())
    raise TargetError(
        "target_missing: no_target_file. Copy target.toml.template to "
        f"~/{DEFAULT_TARGET} or supply --target or {TARGET_ENV}. "
        f"Searched: {searched}"
    )


def _path(value: object, base: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise TargetError("binding_path_invalid")
    selected = Path(value).expanduser()
    return (selected if selected.is_absolute() else base / selected).resolve()


def _source_path(
    source: dict[str, Any], base: Path, *, dry_run: bool = False
) -> tuple[Path | None, str]:
    """

    :param source:
    :param base:
    :param dry_run:
    :return:
    """
    kind = source.get("kind")
    if kind == "file":
        if set(source) != {"kind", "path"}:
            raise TargetError("binding_file_source_invalid")
        path = _path(source["path"], base)
        return (path if path.exists() else None), f"file:{path}"
    if kind == "environment":
        if set(source) != {"kind", "name"}:
            raise TargetError("binding_environment_source_invalid")
        name = source["name"]
        if not isinstance(name, str) or not ENVIRONMENT_NAME.fullmatch(name):
            raise TargetError("binding_environment_name_invalid")
        value = os.environ.get(name)
        if value is None:
            return None, f"env:{name}"
        path = _path(value, base)
        if not path.is_absolute() or not Path(value).expanduser().is_absolute():
            raise TargetError("binding_environment_path_not_absolute")
        return path, f"env:{name} -> file:{path}"
    if kind == "command":
        if set(source) != {"kind", "argv"}:
            raise TargetError("binding_command_source_invalid")
        argv = source["argv"]
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(part, str) or not part for part in argv)
        ):
            raise TargetError("binding_command_invalid")

        command = argv.copy()

        if "/" in command[0]:
            command[0] = str(_path(command[0], base))
        executable = shutil.which(command[0])

        if executable is None:
            raise TargetError("binding_command_unavailable")
        command[0] = str(Path(executable).resolve())

        digest = hashlib.sha256(
            json.dumps(command, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        provenance = f"command:{command[0]}#argv-sha256:{digest}"
        if dry_run:
            return None, provenance
        result = run_command_tail(
            command, timeout=30, max_bytes=4096, max_lines=2, cwd=base
        )

        if result.returncode:
            raise TargetError(f"binding_command_{error_class(result)}")
        lines = result.stdout.splitlines()
        if len(lines) != 1 or len(lines[0].encode("utf-8")) >= 4096:
            raise TargetError("binding_command_output_invalid")
        path = Path(lines[0]).expanduser()
        if not path.is_absolute():
            raise TargetError("binding_command_path_not_absolute")
        return path.resolve(), f"{provenance} -> file:{path.resolve()}"
    raise TargetError("binding_source_kind_invalid")


def load_project_binding(
    path: str | Path,
    *,
    dry_run: bool = False,
    required_surfaces: tuple[str, ...] = AUTHORITIES,
) -> Target:
    """Use only declared sources; advance solely when one is absent.
    :param path:
    :param dry_run:
    :param required_surfaces:
    :return:
    """
    binding = Path(path).expanduser().resolve()
    try:
        with binding.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise TargetError(f"binding_unavailable_or_invalid:{binding}") from exc
    if (
        set(data) != {"schema_version", "target", "kubernetes"}
        or data.get("schema_version") != "1.0"
    ):
        raise TargetError("binding_shape_invalid")
    kube = data["kubernetes"]
    if not isinstance(kube, dict) or set(kube) != {"sources"}:
        raise TargetError("binding_kubernetes_invalid")
    sources = kube["sources"]
    if not isinstance(sources, list) or not sources:
        raise TargetError("binding_sources_missing")
    target_path = _path(data["target"], binding.parent)
    target = load_target(target_path, required_surfaces=required_surfaces)
    if "kubernetes" not in required_surfaces:
        return replace(
            target,
            target_reference=f"binding:{binding}",
            source_file=target_path,
            source_kind="binding",
        )
    if target.kubernetes.kubeconfig is not None or target.kubernetes.kubeconfigs:
        raise TargetError("binding_target_kubeconfig_conflict")
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise TargetError("binding_source_invalid")
        selected, reference = _source_path(source, binding.parent, dry_run=dry_run)
        if dry_run and source["kind"] == "command":
            return replace(
                target,
                kubernetes_source_reference=f"binding:{binding}#{index + 1}:{reference}",
                target_reference=f"binding:{binding}",
                source_file=target_path,
                source_kind="binding",
            )
        if selected is None:
            continue
        try:
            if not selected.is_file() or not selected.stat().st_size:
                raise TargetError("binding_kubeconfig_unavailable")
            with selected.open("rb") as handle:
                handle.read(1)
        except OSError as exc:
            raise TargetError("binding_kubeconfig_unreadable") from exc
        return replace(
            target,
            kubernetes=replace(target.kubernetes, kubeconfig=selected),
            kubernetes_source_reference=f"binding:{binding}#{index + 1}:{reference}",
            target_reference=f"binding:{binding}",
            source_file=target_path,
            source_kind="binding",
        )
    raise TargetError("binding_sources_absent")


def resolve_target(
    explicit_target: str | None,
    explicit_binding: str | None,
    *,
    dry_run: bool = False,
    required_surfaces: tuple[str, ...] = AUTHORITIES,
) -> Target:
    """Resolve explicit, environment, project, then user target without crossover."""
    if explicit_target and explicit_binding:
        raise TargetError("target_binding_conflict")
    if explicit_target:
        selected, source = resolve_target_file(explicit_target)
        return replace(
            load_target(selected, required_surfaces=required_surfaces),
            target_reference=f"cli:{selected.resolve()}",
            source_file=selected,
            source_kind=source,
        )
    if explicit_binding:
        return load_project_binding(
            explicit_binding,
            dry_run=dry_run,
            required_surfaces=required_surfaces,
        )
    if TARGET_ENV in os.environ and BINDING_ENV in os.environ:
        raise TargetError("environment_selector_conflict")
    if BINDING_ENV in os.environ:
        return load_project_binding(
            os.environ[BINDING_ENV],
            dry_run=dry_run,
            required_surfaces=required_surfaces,
        )
    selected, source = resolve_target_file(None)
    reference = (
        f"env:{TARGET_ENV} -> file:{selected.resolve()}"
        if source == f"env:{TARGET_ENV}"
        else f"{source}:{selected.resolve()}"
    )
    return replace(
        load_target(selected, required_surfaces=required_surfaces),
        target_reference=reference,
        source_file=selected,
        source_kind=source,
    )
