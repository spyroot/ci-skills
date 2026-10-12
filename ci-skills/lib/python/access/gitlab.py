"""Gitlab access and native command binding.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping, MutableSequence
from datetime import UTC, datetime
from typing import Optional, Union
from urllib.parse import quote, urlsplit

from lib.python.access.shared import Surface, _blocked, _json, _token_file
from lib.python.access.sources import Sources
from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.catalog import GITLAB_VARIABLES
from lib.python.core.gitlab.gitlab_api import GlabAPIClient
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    copy_json,
)
from lib.python.core.runtime import error_class, run_command
from lib.python.core.target import GitLabOperationTarget, Target
from lib.python.sys.status import BLOCKED, PASS


def glab_argv(
    target: Union[Target, GitLabOperationTarget], endpoint: str
) -> MutableSequence[str]:
    """Build a read-only glab request for the selected GitLab host.

    :param target: Runtime target or operation target containing GitLab settings.
    :param endpoint: Provider-relative API endpoint to request.
    :returns: Argument list for ``glab api`` with hostname and explicit GET method.
    :raises ValueError: If the target does not select a GitLab authority.
    """
    if target.gitlab is None:
        raise ValueError("gitlab_target_required")
    return [
        "glab",
        "api",
        "--hostname",
        target.gitlab.host,
        "--method",
        "GET",
        endpoint,
    ]


def gitlab_env(target: Target) -> Optional[Mapping[str, Optional[str]]]:
    """Select credential overrides for a GitLab child process.

    :param target: Runtime target with GitLab settings and optional bound sources.
    :returns: Bound overrides including the GitLab host, a token-file override,
        or ``None`` when the native CLI should use its existing authentication.
    :raises TargetError: If an explicitly selected token file is invalid.
    :raises ValueError: If the target does not select a GitLab authority.
    """  # noqa: DOC502 - Target and credential selectors propagate validation failures.
    selected = target.require_gitlab()
    if isinstance(target.sources, Sources):
        return {
            **target.sources.require(SkillPluginType.GITLAB).environment,
            "GITLAB_HOST": selected.host,
        }
    return _token_file(selected.token_file, GITLAB_VARIABLES[0])


def gitlab_access(target: Target) -> Surface:
    """Verify instance-admin identity and runner-list access on GitLab.

    :param target: Runtime target containing the selected GitLab host.
    :returns: PASS surface with the verified username, or BLOCKED surface when
        authentication, host identity, admin status, or runner access fails.
    :raises ValueError: If the target does not select a GitLab authority.
    :raises OSError: If a native authentication or API process cannot be started.
    """  # noqa: DOC503 - Response validation is caught; native I/O can escape.
    selected = target.require_gitlab()
    host = selected.host
    observed: MutableSequence[str] = []
    source = (
        target.sources.require(SkillPluginType.GITLAB).reference
        if isinstance(target.sources, Sources)
        else None
    )
    try:
        credential = gitlab_env(target)
    except ValueError as exc:
        return _blocked(
            "gitlab",
            selected.url,
            observed,
            str(exc),
            "Provide a readable, nonempty GitLab token file outside the skill.",
            source,
        )
    if source and source.startswith("file:"):
        observed.append("token_file_read")
    auth = run_command(["glab", "auth", "status", "--hostname", host], env=credential)
    if auth.returncode:
        return _blocked(
            "gitlab",
            selected.url,
            observed,
            error_class(auth),
            "Authenticate glab for this exact GitLab FQDN.",
            source,
        )
    observed.append("authenticated_host")
    try:
        user = _json(run_command(glab_argv(target, "user"), env=credential))
        if not isinstance(user, dict) or user.get("is_admin") is not True:
            raise ValueError("instance_admin_required")
        web_url = user.get("web_url")
        if not isinstance(web_url, str) or urlsplit(web_url).netloc.lower() != host:
            raise ValueError("host_mismatch")
        identity = user.get("username")
        if not isinstance(identity, str) or not identity:
            raise ValueError("missing_identity")
        observed.append("instance_admin_identity")
        runners = _json(
            run_command(glab_argv(target, "runners/all?per_page=1"), env=credential)
        )
        if not isinstance(runners, list):
            raise TypeError("runner_api_invalid_response")
    except (ValueError, TypeError) as exc:
        return _blocked(
            "gitlab",
            selected.url,
            observed,
            str(exc),
            "Use an instance administrator credential with runner API access on the selected host.",
            source,
        )
    observed.append("instance_runner_api_read")
    return Surface(
        "gitlab",
        PASS,
        identity,
        selected.url,
        observed,
        None,
        credential_source=source,
    )


def _gitlab_positive_id(value: object) -> bool:
    """Check the provider identifier boundary without accepting booleans.

    :param value: Untrusted ID field from a decoded GitLab response.
    :returns: Whether the value is a positive integer distinct from bool.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _gitlab_origin_matches(value: object, origin: str) -> bool:
    """Compare a provider resource URL with the selected GitLab origin.

    :param value: Untrusted web_url field from the provider response.
    :param origin: Already selected HTTPS GitLab origin.
    :returns: Whether scheme and authority match and URL user information is absent.
    :raises ValueError: If the supplied URL cannot be parsed.
    """  # noqa: DOC502 - urlsplit rejects malformed bracketed hosts.
    if not isinstance(value, str):
        return False
    observed = urlsplit(value)
    expected = urlsplit(origin)
    return (
        observed.scheme == expected.scheme
        and observed.netloc.lower() == expected.netloc.lower()
        and observed.username is None
        and observed.password is None
    )


def _gitlab_safe_reason(error: Exception) -> str:
    """Select a classified error name without exposing response content.

    :param error: Failure raised during the bound access probes.
    :returns: Validated lower-case reason code, or provider_request_failed when the
        exception contains only unsafe or unclassified detail.
    """
    reason = getattr(error, "reason", None)
    if reason is None and isinstance(error, ValueError):
        reason = str(error)
    if isinstance(reason, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason):
        return reason
    return "provider_request_failed"


def check_gitlab_operation_access(
    session: BoundGitLabSession, api_client: Optional[GlabAPIClient] = None
) -> JSONObject:
    """Verify one bound GitLab identity and its selected project or group.

    :param session: Selected host, target reference, provenance, and private child
        environment reused for both access reads.
    :param api_client: Injectable native GitLab client; omission selects the shared
        GlabAPIClient implementation.
    :returns: Versioned access receipt with verified numeric identity and target on
        PASS, or BLOCKED with classified errors and the observations made before
        failure. Credentials and response bodies are excluded from the receipt.

    Provider and identity-validation failures are caught and recorded in errors;
    they do not escape as exceptions from this access check.
    """  # noqa: DOC501, DOC503 - validation exceptions are converted to BLOCKED evidence here.
    observations: MutableSequence[JSONValue] = []
    errors: MutableSequence[JSONValue] = []
    receipt: MutableMapping[str, JSONValue] = {
        "schema_version": "1.0",
        "kind": "gitlab_access",
        "status": BLOCKED,
        "captured_at": datetime.now(UTC).isoformat(),
        "execution_host": session.execution_host,
        "origin": session.origin,
        "target_source": session.target_source,
        "target_file": str(session.target_file),
        "credential_source": session.credential_source,
        "credential_digest": session.credential_digest,
        "skill": copy_json(session.skill),
        "identity": None,
        "target": None,
        "observed_capability": observations,
        "errors": errors,
    }
    source = "user"
    try:
        if api_client is None:
            api_client = GlabAPIClient()
        user = api_client.get_json(session, "user")
        if (
            not isinstance(user, dict)
            or not _gitlab_positive_id(user.get("id"))
            or not isinstance(user.get("username"), str)
            or not user["username"].strip()
        ):
            raise ValueError("user_identity_invalid")
        if not _gitlab_origin_matches(user.get("web_url"), session.origin):
            raise ValueError("user_host_mismatch")
        receipt["identity"] = {
            "id": user["id"],
            "username": user["username"],
            "web_url": user["web_url"],
        }
        observations.append("identity_read")

        source = "target"
        endpoint_kind = "projects" if session.target_kind == "project" else "groups"
        if session.target_kind not in {"project", "group"}:
            raise ValueError("target_kind_invalid")
        reference = session.target_reference
        endpoint = f"{endpoint_kind}/{quote(reference, safe='')}"
        item = api_client.get_json(session, endpoint)
        if not isinstance(item, dict) or not _gitlab_positive_id(item.get("id")):
            raise ValueError("target_response_invalid")
        if not _gitlab_origin_matches(item.get("web_url"), session.origin):
            raise ValueError("target_host_mismatch")
        path_field = (
            "path_with_namespace" if session.target_kind == "project" else "full_path"
        )
        full_path = item.get(path_field)
        if not isinstance(full_path, str) or not full_path:
            raise ValueError("target_path_missing")
        if reference.isdecimal():
            if item["id"] != int(reference):
                raise ValueError("target_id_mismatch")
        elif full_path != reference:
            raise ValueError("target_path_mismatch")
        receipt["target"] = {
            "kind": session.target_kind,
            "id": item["id"],
            "full_path": full_path,
            "web_url": item["web_url"],
        }
        observations.append("target_read")
        receipt["status"] = PASS
    except Exception as exc:  # noqa: BLE001 - keep every provider failure structured
        errors.append({"source": source, "reason": _gitlab_safe_reason(exc)})
    return receipt
