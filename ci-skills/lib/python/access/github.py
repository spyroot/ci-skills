"""Github access and native command binding.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping, MutableSequence, Sequence
from typing import Optional

from lib.python.access.shared import Surface, _blocked, _json, _token_file
from lib.python.access.sources import Sources
from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.catalog import github_variables
from lib.python.core.json_types import (
    json_object,
    json_objects,
    json_string,
    json_strings,
)
from lib.python.core.runtime import CommandResult, error_class, run_command
from lib.python.core.target import Target
from lib.python.sys.status import PASS


def github_env(target: Target) -> Optional[Mapping[str, Optional[str]]]:
    """Select credential overrides for a GitHub child process.

    :param target: Runtime target with GitHub settings and optional bound sources.
    :returns: Bound environment overrides, a selected token-file override, or
        ``None`` when the native CLI should use its existing authentication.
    :raises TargetError: If an explicitly selected token file is invalid.
    :raises ValueError: If the target does not select a GitHub authority.
    """  # noqa: DOC502 - Target and credential selectors propagate validation failures.
    selected = target.require_github()
    if isinstance(target.sources, Sources):
        return target.sources.require(SkillPluginType.GITHUB).environment
    return _token_file(selected.token_file, github_variables(selected.host)[0])


def github_access(target: Target) -> Surface:
    """Verify GitHub authentication and access to the selected repository.

    :param target: Runtime target containing the GitHub host and repository.
    :returns: PASS surface with verified login and repository access, or BLOCKED
        surface with the failed capability and a suggested recovery action.
    :raises ValueError: If the target does not select a GitHub authority.
    :raises OSError: If a native authentication or API process cannot be started.
    """  # noqa: DOC503 - Response validation is caught; native I/O can escape.
    selected = target.require_github()
    host = selected.host
    repo = selected.repository
    observed: MutableSequence[str] = []
    source = (
        target.sources.require(SkillPluginType.GITHUB).reference
        if isinstance(target.sources, Sources)
        else None
    )
    try:
        credential = github_env(target)
    except ValueError as exc:
        return _blocked(
            "github",
            f"{host}/{repo}",
            observed,
            str(exc),
            "Provide a readable, nonempty GitHub token file outside the skill.",
            source,
        )
    if source and source.startswith("file:"):
        observed.append("token_file_read")
    auth = run_command(["gh", "auth", "status", "--hostname", host], env=credential)
    if auth.returncode:
        return _blocked(
            "github",
            f"{host}/{repo}",
            observed,
            error_class(auth),
            "Authenticate gh for the selected host.",
            source,
        )
    observed.append("authenticated_host")
    try:
        user = _json(
            run_command(["gh", "api", "--hostname", host, "user"], env=credential)
        )
        repository = _json(
            run_command(
                ["gh", "api", "--hostname", host, f"repos/{repo}"], env=credential
            )
        )
        if not isinstance(user, dict) or not isinstance(repository, dict):
            raise TypeError("invalid_response")
        if json_string(repository.get("full_name", "")).lower() != repo.lower():
            raise ValueError("repository_mismatch")
        login = user.get("login")
        if not isinstance(login, str) or not login:
            raise ValueError("missing_identity")
    except (ValueError, TypeError) as exc:
        return _blocked(
            "github",
            f"{host}/{repo}",
            observed,
            str(exc),
            "Check gh API authentication and repository access for this host.",
            source,
        )
    observed += ["identity_read", "repository_read"]
    return Surface(
        "github",
        PASS,
        login,
        f"{host}/{repo}",
        observed,
        None,
        credential_source=source,
    )


def _required_check_names(result: CommandResult) -> Sequence[str]:
    """Read declared status-check names from a validated branch-protection response.

    :param result: Completed native API read for the selected branch protection.
    :returns: Sorted nonempty check names declared by contexts or check records.
    :raises ValueError: If the command failed, JSON is invalid, or no names are declared.
    :raises KeyError: If the protection object lacks required_status_checks.
    :raises TypeError: If protection fields are not their declared object or array shapes.
    """  # noqa: DOC503 - shared decoding and field readers validate the provider response.
    protection = json_object(_json(result))
    required = json_object(protection["required_status_checks"])
    contexts = json_strings(required.get("contexts", []))
    checks = json_objects(required.get("checks", []))
    names = sorted(
        {name for name in contexts if name}
        | {
            json_string(check["context"])
            for check in checks
            if isinstance(check.get("context"), str) and check["context"]
        }
    )
    if not names:
        raise ValueError("required_checks_missing")
    return names


def _publication_result(
    base: Surface, required_checks: Sequence[str], names: Sequence[str]
) -> Surface:
    """Compare declared publication checks with the observed branch protection.

    :param base: Successful repository access probe whose identity is retained.
    :param required_checks: Check names required by the selected target configuration.
    :param names: Nonempty validated check names observed in branch protection.
    :returns: PASS with verified check names when every declaration is present;
        otherwise BLOCKED naming absent declarations or missing required checks.
    """
    expected = set(required_checks)
    if not expected:
        return _blocked(
            "github",
            base.target,
            base.observed_capability,
            "required_checks_not_declared",
            "Declare github.required_checks in the target file.",
            base.credential_source,
        )
    missing = sorted(expected - set(names))
    if missing:
        return _blocked(
            "github",
            base.target,
            base.observed_capability,
            "required_checks_incomplete:" + ",".join(missing),
            "Require every declared status check on the protected branch.",
            base.credential_source,
        )
    return Surface(
        "github",
        PASS,
        base.identity,
        base.target,
        [
            *base.observed_capability,
            "repository_admin",
            "protection_read",
            "required_checks_read",
        ],
        None,
        credential_source=base.credential_source,
        details={"required_checks": names},
    )


def github_publication_access(target: Target) -> Surface:
    """Verify repository administration and declared required checks.

    :param target: Runtime target containing GitHub repository and required checks.
    :returns: PASS surface when repository access, administration, and all declared
        checks are verified; otherwise a BLOCKED surface with recovery guidance.
    :raises TargetError: If a configured credential file becomes invalid after
        the initial access probe and before the publication probes.
    :raises OSError: If a publication probe process cannot be started.
    :raises ValueError: If the target does not select a GitHub authority.
    """  # noqa: DOC502 - Target, credential, and native process helpers propagate failures.
    selected = target.require_github()
    base = github_access(target)
    if base.status != PASS:
        return base
    result = run_command(
        [
            "gh",
            "api",
            "--hostname",
            selected.host,
            f"repos/{selected.repository}/branches/main/protection",
        ],
        env=github_env(target),
    )
    info = run_command(
        [
            "gh",
            "api",
            "--hostname",
            selected.host,
            f"repos/{selected.repository}",
            "--jq",
            ".permissions.admin",
        ],
        env=github_env(target),
    )
    if info.returncode or info.stdout.strip().lower() != "true":
        return _blocked(
            "github",
            base.target,
            base.observed_capability,
            "admin_required",
            "Use a repository administrator identity to configure and read back the required check.",
            base.credential_source,
        )
    if result.returncode:
        return _blocked(
            "github",
            base.target,
            base.observed_capability,
            error_class(result),
            "Read back main branch protection and required status checks.",
            base.credential_source,
        )
    try:
        names = _required_check_names(result)
    except (KeyError, TypeError, ValueError):
        return _blocked(
            "github",
            base.target,
            base.observed_capability,
            "required_checks_missing",
            "Configure and read back at least one required main-branch status check.",
            base.credential_source,
        )
    return _publication_result(base, selected.required_checks, names)
