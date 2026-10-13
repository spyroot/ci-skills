"""Resolve and pin Kubernetes credential sources.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import MutableSequence, Sequence
from pathlib import Path
from typing import Optional

from lib.python.access.sources import (
    CredentialSource,
    Sources,
)
from lib.python.core.paths import SKILL_ROOT
from lib.python.core.target import (
    Target,
    TargetError,
    assert_external_path,
)


def _kubernetes_source(target: Target) -> tuple[CredentialSource, tuple[Path, ...]]:
    """Resolve and verify the configured kubeconfig search path.

    :param target: Runtime target containing Kubernetes configuration.
    :returns: Credential-source record exporting ``KUBECONFIG`` and its ordered
        tuple of resolved paths.
    :raises TargetError: If Kubernetes is absent or a selected kubeconfig is empty,
        unreadable, missing, or located inside the installed skill.
    """
    if target.kubernetes is None:
        raise TargetError("kubernetes_target_missing")
    if target.kubernetes.kubeconfigs:
        # A declared search path needs no environment export, so a cold run
        # cannot silently fall through to a different cluster's default
        # kubeconfig.
        paths = tuple(path.resolve() for path in target.kubernetes.kubeconfigs)
        reference = "target:kubernetes.kubeconfigs"
    elif target.kubernetes.kubeconfig is not None:
        paths = (target.kubernetes.kubeconfig.resolve(),)
        reference = target.kubernetes_source_reference or f"file:{paths[0]}"
    elif "KUBECONFIG" in os.environ:
        parts = os.environ["KUBECONFIG"].split(os.pathsep)
        if any(not part for part in parts):
            raise TargetError("kubeconfig_source_empty")
        paths = tuple(Path(part).expanduser().resolve() for part in parts)
        reference = "env:KUBECONFIG"
    else:
        paths = ((Path.home() / ".kube" / "config").resolve(),)
        reference = f"kubectl-default:{paths[0]}"
    if not paths:
        raise TargetError("kubeconfig_source_empty")
    for path in paths:
        assert_external_path(path, SKILL_ROOT, "kubeconfig")
        try:
            if not path.is_file() or not path.stat().st_size:
                raise TargetError(f"kubeconfig_unavailable:{path}")
            with path.open("rb") as handle:
                handle.read(1)
        except OSError as exc:
            raise TargetError(f"kubeconfig_unreadable:{path}") from exc
    paths = tuple(path.resolve() for path in paths)
    return CredentialSource(
        reference, {"KUBECONFIG": os.pathsep.join(str(path) for path in paths)}
    ), paths


def kubeconfig_digests(paths: Sequence[Path]) -> tuple[str, ...]:
    """Hash kubeconfig contents in their configured search order.

    :param paths: Ordered kubeconfig paths to read.
    :returns: Tuple of hexadecimal SHA-256 digests in the same order.
    :raises TargetError: If any selected kubeconfig cannot be read.
    """
    digests: MutableSequence[str] = []
    for path in paths:
        try:
            digests.append(hashlib.sha256(path.read_bytes()).hexdigest())
        except OSError as exc:
            raise TargetError(f"kubeconfig_unreadable:{path}") from exc
    return tuple(digests)


def assert_kubeconfig_unchanged(sources: Optional[Sources]) -> None:
    """Reject kubeconfig changes after source binding.

    :param sources: Bound source record; None means the target has not been bound.
        A record with no digests has no pinned files to check.
    :raises TargetError: If a pinned file is unreadable or its digest changed.

    The check detects changes before command construction; it does not make
    subsequent file access atomic.
    """
    if sources is None or not sources.kubeconfig_digests:
        return
    current = kubeconfig_digests(sources.kubeconfig_files)
    if current != sources.kubeconfig_digests:
        raise TargetError("kubeconfig_changed_during_invocation")
