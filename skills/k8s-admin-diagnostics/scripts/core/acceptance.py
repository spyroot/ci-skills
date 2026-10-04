"""Validate a sanitized live receipt against an independent selected target."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .credentials import skill_digest
from .live import _access_proven
from .status import PARTIAL, PASS


def validate_live_receipt(
    receipt: Any,
    expected: Any,
    *,
    revision: str,
    github_repository: str,
    skill_root: Path,
    now: datetime | None = None,
) -> list[str]:
    """Return stable failure codes without printing private receipt contents."""
    failures: list[str] = []
    if not isinstance(receipt, dict) or not isinstance(expected, dict):
        return ["invalid_receipt_or_target"]
    now = now or datetime.now(timezone.utc)
    if (
        receipt.get("schema_version") != "1.0"
        or receipt.get("kind") != "access_check"
        or receipt.get("status") != PASS
        or receipt.get("blocking_live_checks")
    ):
        failures.append("gate_not_passed")
    if receipt.get("publication") is not True:
        failures.append("publication_not_checked")
    if receipt.get("tested_revision") != revision:
        failures.append("revision_mismatch")
    if receipt.get("skill_sha256") != skill_digest(skill_root):
        failures.append("skill_content_mismatch")
    try:
        captured_at = datetime.fromisoformat(
            receipt["captured_at"].replace("Z", "+00:00")
        )
        if captured_at.tzinfo is None or not now - timedelta(
            hours=24
        ) <= captured_at <= now + timedelta(minutes=5):
            failures.append("receipt_stale")
    except (KeyError, AttributeError, ValueError, TypeError):
        failures.append("receipt_time_invalid")
    if receipt.get("execution_host") != expected.get(
        "execution_host"
    ) or not expected.get("execution_host"):
        failures.append("execution_host_mismatch")

    targets = receipt.get("targets") or {}
    if not isinstance(targets, dict):
        targets = {}
    github_target = f"{expected.get('github_host', '')}/{github_repository}"
    if targets.get("github") != github_target:
        failures.append("github_target_mismatch")
    if targets.get("gitlab") != expected.get("gitlab_url") or not expected.get(
        "gitlab_url"
    ):
        failures.append("gitlab_target_mismatch")
    kubernetes = targets.get("kubernetes") or {}
    if (
        not isinstance(kubernetes, dict)
        or kubernetes.get("context") != expected.get("kubernetes_context")
        or kubernetes.get("server") != expected.get("kubernetes_server")
        or not expected.get("kubernetes_context")
        or not expected.get("kubernetes_server")
    ):
        failures.append("kubernetes_target_mismatch")

    sources = receipt.get("credential_sources") or {}
    surfaces = receipt.get("surfaces") or {}
    if not isinstance(sources, dict) or not isinstance(surfaces, dict):
        return failures + ["credential_or_surface_evidence_missing"]
    for name in ("github", "gitlab", "kubernetes"):
        source = sources.get(name)
        surface = surfaces.get(name)
        if not isinstance(source, str) or not source or not isinstance(surface, dict):
            failures.append(f"{name}_evidence_missing")
            continue
        if (
            source.startswith(("file:", "kubectl-default:"))
            and not Path(source.partition(":")[2]).is_absolute()
        ):
            failures.append(f"{name}_source_not_absolute")
        elif not source.startswith(
            (
                "file:",
                "env:",
                "gh-credential-store:",
                "glab-credential-store:",
                "kubectl-default:",
            )
        ):
            failures.append(f"{name}_source_unresolved")
        if (
            surface.get("credential_source") != source
            or surface.get("status") != PASS
            or not surface.get("identity")
        ):
            failures.append(f"{name}_identity_or_access_missing")
        expected_target = {
            "github": github_target,
            "gitlab": expected.get("gitlab_url"),
            "kubernetes": f"{expected.get('kubernetes_context')} -> {expected.get('kubernetes_server')}",
        }[name]
        if surface.get("target") != expected_target:
            failures.append(f"{name}_surface_target_mismatch")
    github = surfaces.get("github") or {}
    required_checks = expected.get("required_checks")
    details = github.get("details") or {} if isinstance(github, dict) else {}
    if (
        not isinstance(required_checks, list)
        or not required_checks
        or not all(isinstance(item, str) and item for item in required_checks)
    ):
        failures.append("expected_checks_missing")
    elif not isinstance(details, dict) or not set(required_checks) <= set(
        details.get("required_checks") or []
    ):
        failures.append("required_checks_mismatch")
    kubernetes_surface = surfaces.get("kubernetes") or {}
    kube_details = (
        kubernetes_surface.get("details") or {}
        if isinstance(kubernetes_surface, dict)
        else {}
    )
    if not isinstance(kube_details, dict) or not all(
        kube_details.get(item)
        for item in (
            "context",
            "user_entry",
            "api_server",
            "kubeconfig_files",
            "auth_mechanism",
        )
    ):
        failures.append("kubernetes_mechanism_missing")
    else:
        files = kube_details["kubeconfig_files"]
        if (
            kube_details["context"] != expected.get("kubernetes_context")
            or kube_details["api_server"] != expected.get("kubernetes_server")
            or not isinstance(files, list)
            or not files
            or not all(
                isinstance(path, str) and Path(path).is_absolute() for path in files
            )
            or not isinstance(kube_details["auth_mechanism"], dict)
            or not kube_details["auth_mechanism"].get("type")
        ):
            failures.append("kubernetes_mechanism_mismatch")

    job_url = expected.get("job_url")
    if (
        not isinstance(job_url, str)
        or not job_url
        or receipt.get("requested_job_url") != job_url
    ):
        failures.append("job_target_mismatch")
    checks = receipt.get("live_checks") or {}
    if not isinstance(checks, dict):
        checks = {}
    for name in ("storage_report", "event_trace", "cilium_status", "gitlab_job"):
        check = checks.get(name)
        if not isinstance(check, dict):
            failures.append(f"{name}_missing")
            continue
        digest = check.get("readback_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            failures.append(f"{name}_readback_missing")
        if check.get("access_proven") is not True or not _access_proven(name, check):
            failures.append(f"{name}_access_unproven")
        if check.get("status") not in (PASS, PARTIAL):
            failures.append(f"{name}_failed")
        if check.get("status") == PASS and (
            check.get("error_count") != 0 or check.get("errors")
        ):
            failures.append(f"{name}_inconsistent_result")
        if (
            name == "cilium_status"
            and (check.get("agent_health") or {}).get("passed", 0) < 1
        ):
            failures.append("cilium_health_readback_missing")
    job = checks.get("gitlab_job") or {}
    job_readback = job.get("job_readback") or {} if isinstance(job, dict) else {}
    if (
        not isinstance(job_readback, dict)
        or not all(
            job_readback.get(key) for key in ("job_id", "pipeline_id", "runner_id")
        )
        or not isinstance(job_readback.get("trace_line_count"), int)
    ):
        failures.append("job_readback_missing")
    return failures


def validate_external_status(
    payload: Any,
    *,
    revision: str,
    repository: str,
    trusted_actor: str,
    now: datetime | None = None,
) -> list[str]:
    """Require a fresh exact-head result from the configured trusted verifier."""
    if not trusted_actor:
        return ["trusted_actor_unconfigured"]
    if not isinstance(payload, dict):
        return ["status_response_invalid"]
    observed_repository = payload.get("repository")
    if (
        payload.get("sha") != revision
        or not isinstance(observed_repository, dict)
        or observed_repository.get("full_name") != repository
    ):
        return ["status_target_mismatch"]
    statuses = payload.get("statuses")
    if not isinstance(statuses, list):
        return ["status_list_invalid"]
    status = next(
        (
            item
            for item in statuses
            if isinstance(item, dict) and item.get("context") == "gal19/live-receipt"
        ),
        None,
    )
    if status is None:
        return ["live_receipt_status_missing"]
    creator = status.get("creator")
    if (
        status.get("state") != "success"
        or not isinstance(creator, dict)
        or creator.get("login") != trusted_actor
    ):
        return ["live_receipt_status_untrusted_or_failed"]
    try:
        updated = datetime.fromisoformat(status["updated_at"].replace("Z", "+00:00"))
        now = now or datetime.now(timezone.utc)
        if updated.tzinfo is None or not now - timedelta(
            hours=24
        ) <= updated <= now + timedelta(minutes=5):
            return ["live_receipt_status_stale"]
    except (KeyError, AttributeError, ValueError, TypeError):
        return ["live_receipt_status_time_invalid"]
    return []
