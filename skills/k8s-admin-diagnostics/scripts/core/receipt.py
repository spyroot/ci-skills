"""Build the single machine-readable access receipt.

The receipt is the acceptance evidence for one execution host: it names the
host, when the checks ran, the revision under test, the effective credential
source for each authority, the explicitly selected targets, the identities
read back, and the result of every individual live check.

It carries sanitized read-back evidence only. Token values, private keys and
raw credential-bearing kubeconfig contents are never placed in it; a
credential appears as a reference -- an absolute path, a named environment
variable, or a credential-store reference -- and never as a value.
"""

from __future__ import annotations

import platform
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runtime import run_command, sanitize
from .status import BLOCKED, PASS
from .target import Target

SCHEMA_VERSION = "1.0"
KIND = "access_receipt"


def observed_at() -> str:
    """Return the check time as an explicit UTC RFC3339 stamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def execution_host() -> dict[str, Any]:
    """Identify the computer or runner the checks actually ran on."""
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = "unknown"
    return {
        "hostname": hostname,
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }


def tested_revision(root: Path | None = None) -> dict[str, Any]:
    """Describe the skill revision under test, or say it is not a checkout."""
    base = Path(__file__).resolve().parents[3] if root is None else root
    head = run_command(["git", "-C", str(base), "rev-parse", "HEAD"])
    if head.returncode:
        return {"repository": str(base), "commit": None, "dirty": None,
                "detail": "not_a_git_checkout"}
    status = run_command(["git", "-C", str(base), "status", "--porcelain"])
    branch = run_command(["git", "-C", str(base), "rev-parse", "--abbrev-ref", "HEAD"])
    return {
        "repository": str(base),
        "commit": head.stdout.strip() or None,
        "branch": branch.stdout.strip() or None if not branch.returncode else None,
        "dirty": bool(status.stdout.strip()) if not status.returncode else None,
    }


def targets(target: Target) -> dict[str, Any]:
    """Restate the explicitly selected authorities, with no credential paths."""
    return {
        "github": {"host": target.github.host, "repository": target.github.repository},
        "gitlab": {"url": target.gitlab.url, "host": target.gitlab.host},
        "kubernetes": {"context": target.kubernetes.context, "server": target.kubernetes.server},
    }


def live_check(
    name: str,
    surface: str,
    status: str,
    *,
    detail: str | None = None,
    evidence: Any = None,
) -> dict[str, Any]:
    """Record one individual live check and its sanitized read-back."""
    return {
        "name": name,
        "surface": surface,
        "status": status,
        "detail": detail,
        "evidence": sanitize(evidence, 400) if isinstance(evidence, str) else evidence,
    }


def build(
    target: Target,
    *,
    sources: dict[str, Any],
    surfaces: dict[str, Any],
    checks: list[dict[str, Any]],
    publication: bool = False,
) -> dict[str, Any]:
    """Assemble the receipt and decide PASS from resolution and live results.

    PASS requires every surface to pass, every required credential source to be
    resolved, and every recorded live check to pass. Anything else blocks: an
    unresolved source is as disqualifying as a failed probe, so a receipt can
    never report PASS on the strength of a login-status message alone.
    """
    unresolved = sorted(
        name for name, source in sources.items()
        if not isinstance(source, dict) or source.get("kind") in (None, "unresolved")
    )
    failed_checks = sorted({item["name"] for item in checks if item.get("status") != PASS})
    blocked_surfaces = sorted(
        name for name, surface in surfaces.items()
        if not isinstance(surface, dict) or surface.get("status") != PASS
    )
    status = PASS if not (unresolved or failed_checks or blocked_surfaces) else BLOCKED
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "status": status,
        "publication": publication,
        "observed_at": observed_at(),
        "execution_host": execution_host(),
        "tested_revision": tested_revision(),
        "targets": targets(target),
        "credential_sources": sources,
        "identities": {
            name: (surface.get("identity") if isinstance(surface, dict) else None)
            for name, surface in surfaces.items()
        },
        "surfaces": surfaces,
        "live_checks": checks,
        "blocking": {
            "unresolved_credential_sources": unresolved,
            "failed_live_checks": failed_checks,
            "blocked_surfaces": blocked_surfaces,
        },
    }
