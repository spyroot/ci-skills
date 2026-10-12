"""Resolve an explicit per-project diagnostic target and kubeconfig selector.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tomllib
from collections.abc import Mapping, MutableSequence, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Optional, TypeAlias, Union

from lib.python.core.catalog import DEFAULT_AUTHORITIES, PROJECT_DIR, TARGET_FILENAME
from lib.python.core.runtime import error_class, run_command_tail
from lib.python.core.target import Target, TargetError, load_target

BINDING_ENV = "K8S_ADMIN_DIAGNOSTICS_BINDING"
TARGET_ENV = "CI_SKILLS_TARGET"
SMOKE_ENV: Final = "ci-skills-smoke"
SMOKE_TARGET_FILENAME: Final = "ci-skills-smoke.toml"
DEFAULT_TARGET = Path(PROJECT_DIR) / TARGET_FILENAME
ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def target_candidates() -> Sequence[tuple[str, Path]]:
    """List target file locations in configured, project, then user order.

    :returns: ``(source, path)`` pairs; the project and user filenames become
        ``ci-skills-smoke.toml`` when ``ci-skills-smoke`` is set.
    """
    candidates: MutableSequence[tuple[str, Path]] = []
    configured = (os.environ.get(TARGET_ENV) or "").strip()
    if configured:
        candidates.append((f"env:{TARGET_ENV}", Path(configured).expanduser()))
    filename = SMOKE_TARGET_FILENAME if os.getenv(SMOKE_ENV) else TARGET_FILENAME
    relative = Path(PROJECT_DIR) / filename
    candidates.append(("project", Path.cwd() / relative))
    candidates.append(("user", Path.home() / relative))
    return candidates


def resolve_target_file(explicit: Optional[str]) -> tuple[Path, str]:
    """Select one target file without merging other locations.

    :param explicit: Selected file, or ``None`` for environment, project, then user
        resolution. A present ``CI_SKILLS_TARGET`` must name an existing file.
    :returns: Selected path and its argv, environment, project, or user source label.
    :raises TargetError: The selected explicit or environment path is empty or
        missing, or no default target file exists.
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
        f"~/{DEFAULT_TARGET} or set {TARGET_ENV}. "
        f"Searched: {searched}"
    )


def _path(value: object, base: Path) -> Path:
    """Resolve a binding path against its containing directory.

    :param value: Nonempty path string from the binding or command output.
    :param base: Directory used for a relative path.
    :returns: Canonical absolute path after home expansion.
    :raises TargetError: The supplied value is not a nonempty string.
    """
    if not isinstance(value, str) or not value.strip():
        raise TargetError("binding_path_invalid")
    selected = Path(value).expanduser()
    return (selected if selected.is_absolute() else base / selected).resolve()


@dataclass(frozen=True)
class SelectedSource:
    """Identify a selected kubeconfig path before file validation.

    :param path: Resolved candidate path, checked by the binding loader before use.
    :param reference: Nonsecret source identifier used in the access receipt.

    .. attribute :: path
        :type: Path

        Candidate selected by one file, environment, or command source.

    .. attribute :: reference
        :type: str

        Nonsecret provenance of this selected candidate.
    """

    path: Path
    reference: str


@dataclass(frozen=True)
class MissingSource:
    """Record an absent candidate so the next declared source can be considered.

    :param reference: Missing file or unset environment source identifier.

    .. attribute :: reference
        :type: str

        Source that supplied no candidate; it grants no access evidence.
    """

    reference: str


@dataclass(frozen=True)
class DeferredSource:
    """Record a selected command that a dry-run must not execute.

    :param reference: Validated command identity without its output or credentials.

    .. attribute :: reference
        :type: str

        Command source retained in the plan without selecting a fallback.
    """

    reference: str


SourceSelection: TypeAlias = Union[SelectedSource, MissingSource, DeferredSource]


def _file_source(
    source: Mapping[str, object], base: Path
) -> Union[SelectedSource, MissingSource]:
    """Resolve one declared file source without treating absence as failure.

    :param source: File source with exactly kind and path fields.
    :param base: Binding directory used to resolve a relative path.
    :returns: Selected path when present, otherwise an explicit missing source.
    :raises TargetError: If the declaration has extra fields or an invalid path.
    :raises OSError: If filesystem path inspection cannot complete.
    """  # noqa: DOC503 - path resolution and filesystem inspection propagate errors.
    if set(source) != {"kind", "path"}:
        raise TargetError("binding_file_source_invalid")
    path = _path(source["path"], base)
    reference = f"file:{path}"
    return (
        SelectedSource(path, reference) if path.exists() else MissingSource(reference)
    )


def _environment_source(
    source: Mapping[str, object], base: Path
) -> Union[SelectedSource, MissingSource]:
    """Resolve one absolute kubeconfig path from its declared environment variable.

    :param source: Environment source with exactly kind and name fields.
    :param base: Binding directory supplied to common path validation.
    :returns: Missing source for an unset variable, otherwise the selected absolute path.
    :raises TargetError: If the declaration, variable name, or absolute path is invalid.
    :raises OSError: If path resolution cannot complete.
    """  # noqa: DOC503 - the shared path resolver may raise OSError.
    if set(source) != {"kind", "name"}:
        raise TargetError("binding_environment_source_invalid")
    name = source["name"]
    if not isinstance(name, str) or not ENVIRONMENT_NAME.fullmatch(name):
        raise TargetError("binding_environment_name_invalid")
    value = os.environ.get(name)
    if value is None:
        return MissingSource(f"env:{name}")
    path = _path(value, base)
    if not path.is_absolute() or not Path(value).expanduser().is_absolute():
        raise TargetError("binding_environment_path_not_absolute")
    return SelectedSource(path, f"env:{name} -> file:{path}")


def _command_source(
    source: Mapping[str, object], base: Path, dry_run: bool
) -> Union[SelectedSource, DeferredSource]:
    """Resolve the path printed by one bounded command, or explicitly defer the read.

    :param source: Command source with exactly kind and argv fields.
    :param base: Binding directory for executable resolution and command execution.
    :param dry_run: Whether to retain the validated command without executing it.
    :returns: Deferred command provenance in a dry-run, otherwise its selected path.
    :raises TargetError: If argv is invalid, its executable is unavailable, the command
        fails, or stdout does not contain exactly one bounded absolute path.
    :raises OSError: If resolving or starting the selected executable fails.
    """  # noqa: DOC503 - path resolution and the native runner propagate OSError.
    if set(source) != {"kind", "argv"}:
        raise TargetError("binding_command_source_invalid")
    argv = source["argv"]
    if not isinstance(argv, list) or not argv:
        raise TargetError("binding_command_invalid")
    command: MutableSequence[str] = []
    for part in argv:
        if not isinstance(part, str) or not part:
            raise TargetError("binding_command_invalid")
        command.append(part)
    if "/" in command[0]:
        command[0] = str(_path(command[0], base))
    executable = shutil.which(command[0])
    if executable is None:
        raise TargetError("binding_command_unavailable")
    command[0] = str(Path(executable).resolve())
    digest = hashlib.sha256(
        json.dumps(command, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    reference = f"command:{command[0]}#argv-sha256:{digest}"
    if dry_run:
        return DeferredSource(reference)
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
    selected = path.resolve()
    return SelectedSource(selected, f"{reference} -> file:{selected}")


def _source_path(
    source: Mapping[str, object], base: Path, *, dry_run: bool = False
) -> SourceSelection:
    """Resolve one source into a selected, absent, or deliberately deferred result.

    :param source: Parsed TOML source with its kind-specific fields.
    :param base: Binding directory used to resolve relative paths.
    :param dry_run: Suppress execution of command sources while retaining their identity.
    :returns: SelectedSource, MissingSource, or DeferredSource; absence and deferred
        execution are distinct outcomes and never share a null path sentinel.
    :raises TargetError: If the kind, fields, path, executable, or command output is invalid.
    :raises OSError: If path inspection or native process startup fails.
    """  # noqa: DOC503 - source-specific resolvers propagate documented failures.
    kind = source.get("kind")
    if kind == "file":
        return _file_source(source, base)
    if kind == "environment":
        return _environment_source(source, base)
    if kind == "command":
        return _command_source(source, base, dry_run)
    raise TargetError("binding_source_kind_invalid")


def load_project_binding(
    path: Union[str, Path],
    *,
    dry_run: bool = False,
    required_surfaces: tuple[str, ...] = DEFAULT_AUTHORITIES,
) -> Target:
    """Load a binding and select its first available kubeconfig source.

    :param path: Binding TOML path, expanded and resolved before reading.
    :param dry_run: Record a command source without executing it when true.
    :param required_surfaces: Authority tables required from the target named by
        the binding; Kubernetes source selection is skipped if absent.
    :returns: Target with binding provenance and the selected Kubernetes source
        when Kubernetes is required.
    :raises TargetError: The binding, target, or source is invalid, conflicting,
        unavailable, or unreadable, or no declared source is usable.
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
        selection = _source_path(source, binding.parent, dry_run=dry_run)
        if isinstance(selection, MissingSource):
            continue
        reference = selection.reference
        if isinstance(selection, DeferredSource):
            return replace(
                target,
                kubernetes_source_reference=f"binding:{binding}#{index + 1}:{reference}",
                target_reference=f"binding:{binding}",
                source_file=target_path,
                source_kind="binding",
            )
        selected = selection.path
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
    explicit_target: Optional[str],
    explicit_binding: Optional[str],
    *,
    dry_run: bool = False,
    required_surfaces: tuple[str, ...] = DEFAULT_AUTHORITIES,
) -> Target:
    """Resolve one target or binding using the caller and environment selectors.

    :param explicit_target: Caller-selected target path, or ``None`` for default
        resolution; internal callers may inject this path.
    :param explicit_binding: Caller-selected binding path, or ``None``.
    :param dry_run: Suppress execution of a binding command source when true.
    :param required_surfaces: Authority tables the selected target must provide.
    :returns: Validated target with its selection source and binding provenance.
    :raises TargetError: Selectors conflict or the selected target, binding, or
        required authority cannot be loaded.
    """
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
