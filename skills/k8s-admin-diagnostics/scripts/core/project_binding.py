"""Resolve an explicit per-project diagnostic target and kubeconfig selector."""

from __future__ import annotations

import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import tomllib

from .runtime import error_class, run_command_tail
from .target import Target, TargetError, load_target

BINDING_ENV = "K8S_ADMIN_DIAGNOSTICS_BINDING"
TARGET_ENV = "CI_SKILLS_TARGET"
DEFAULT_TARGET = Path(".ci-skills/target.toml")
ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _path(value: object, base: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise TargetError("binding_path_invalid")
    selected = Path(value).expanduser()
    return (selected if selected.is_absolute() else base / selected).resolve()


def _source_path(
    source: dict[str, Any], base: Path, *, dry_run: bool = False
) -> tuple[Path | None, str]:
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
        if dry_run:
            return None, f"command:{command[0]}"
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
        return path.resolve(), f"command:{command[0]} -> file:{path.resolve()}"
    raise TargetError("binding_source_kind_invalid")


def load_project_binding(path: str | Path, *, dry_run: bool = False) -> Target:
    """Use only declared sources; advance solely when one is absent."""
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
    target = load_target(_path(data["target"], binding.parent))
    if target.kubernetes.kubeconfig is not None:
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
        )
    raise TargetError("binding_sources_absent")


def resolve_target(
    explicit_target: str | None, explicit_binding: str | None, *, dry_run: bool = False
) -> Target:
    """Resolve explicit, environment, project, then user target without crossover."""
    if explicit_target and explicit_binding:
        raise TargetError("target_binding_conflict")
    if explicit_target:
        selected = Path(explicit_target).expanduser().resolve()
        return replace(load_target(selected), target_reference=f"cli:{selected}")
    if explicit_binding:
        return load_project_binding(explicit_binding, dry_run=dry_run)
    if TARGET_ENV in os.environ and BINDING_ENV in os.environ:
        raise TargetError("environment_selector_conflict")
    if TARGET_ENV in os.environ:
        selected = _path(os.environ[TARGET_ENV], Path.cwd())
        return replace(
            load_target(selected),
            target_reference=f"env:{TARGET_ENV} -> file:{selected}",
        )
    if BINDING_ENV in os.environ:
        return load_project_binding(os.environ[BINDING_ENV], dry_run=dry_run)
    project = Path.cwd() / DEFAULT_TARGET
    if project.exists():
        selected = project.resolve()
        return replace(load_target(selected), target_reference=f"project:{selected}")
    user = Path.home() / DEFAULT_TARGET
    if user.exists():
        selected = user.resolve()
        return replace(load_target(selected), target_reference=f"user:{selected}")
    raise TargetError("target_missing")
