"""Github access and native command binding.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping, MutableSequence, Sequence
from typing import Optional

from lib.python.access.shared import Surface, _blocked, _json, _token_file
from lib.python.access.sources import Sources, _token_source
from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.abstract.skill_state import SkillState
from lib.python.core.abstract.spec import load_spec
from lib.python.core.catalog import GITHUB_TOKEN_VARIABLES, github_variables
from lib.python.core.cli_api import APIError, ReadSession
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.github.github import (
    GitHubAPIClient,
    establish_github_identity,
    pin_github_environment,
    require_github_runtime,
)
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


def _github_session(target: Target, state: SkillState) -> ReadSession:
    """Reuse configured credentials or pin one standalone diagnostic session.

    :param target: Caller-selected GitHub authority and credential source.
    :param state: Shared invocation, optionally already carrying a delivery session.
    :returns: Private session bound to this state and the selected repository.
    :raises ValueError: The target or an existing provider binding is invalid.
    :raises OSError: The native credential store cannot be read.
    """  # noqa: DOC502,DOC503 - Credential selectors and native-store reads propagate failures.
    selected = target.require_github()
    if isinstance(state, DeliveryState) and hasattr(state, "github_session"):
        if state.github_target != selected:
            raise ValueError("github_configuration_binding_mismatch")
        return state.github_session
    source = (
        target.sources.require(SkillPluginType.GITHUB)
        if isinstance(target.sources, Sources)
        else _token_source(
            selected.token_file,
            github_variables(selected.host),
            f"gh-credential-store:{selected.host}",
            clear_names=GITHUB_TOKEN_VARIABLES,
        )
    )
    session = ReadSession(
        selected.host,
        pin_github_environment(selected.host, source.reference, source.environment, 25),
        source.reference,
        invocation=state,
        repository=selected.repository,
    )
    if isinstance(state, DeliveryState):
        state.github_target = selected
        state.github_session = session
    return session


def _github_access(target: Target, state: SkillState, session: ReadSession) -> Surface:
    """Produce the existing access surface from shared identity initialization.

    :param target: Selected GitHub authority and repository.
    :param state: Shared declaration and write-once provider observation.
    :param session: Credential-bound session reused by publication read-back.
    :returns: Existing PASS/BLOCKED surface shape with no extra token-permission claim.
    :raises ValueError: The target does not select GitHub.
    :raises OSError: A native authentication or provider command cannot start.
    """  # noqa: DOC502,DOC503 - Native transport and target readers propagate failures.
    selected = target.require_github()
    host, repo = selected.host, selected.repository
    source = session.credential_source
    observed: MutableSequence[str] = []
    if source.startswith("file:"):
        observed.append("token_file_read")
    if state.github_runtime.role is None:
        auth = run_command(
            ["gh", "auth", "status", "--hostname", host], env=session.environment
        )
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
        identity = establish_github_identity(state, session, repo, GitHubAPIClient())
    except (APIError, ValueError, TypeError) as exc:
        return _blocked(
            "github",
            f"{host}/{repo}",
            observed,
            str(exc),
            "Check the selected repository role and GitHub token access for this host.",
            source,
        )
    observed += ["identity_read", "repository_read"]
    return Surface(
        "github",
        PASS,
        identity.identity,
        f"{host}/{repo}",
        observed,
        None,
        credential_source=source,
    )


def github_access(target: Target, *, state: SkillState | None = None) -> Surface:
    """Verify GitHub authentication and access to the selected repository.

    :param target: Runtime target containing the GitHub host and repository.
    :param state: Shared invocation; a standalone call loads the installed declaration.
    :returns: PASS surface with verified login and repository access, or BLOCKED
        surface with the failed capability and a suggested recovery action.
    :raises ValueError: If the target does not select a GitHub authority.
    :raises OSError: If a native authentication or API process cannot be started.
    """  # noqa: DOC502,DOC503 - Response validation is caught; target and native I/O errors propagate.
    selected = target.require_github()
    host = selected.host
    repo = selected.repository
    invocation = state if state is not None else SkillState()
    if invocation.spec is None:
        invocation.spec = load_spec()
    try:
        session = _github_session(target, invocation)
    except ValueError as exc:
        return _blocked(
            "github",
            f"{host}/{repo}",
            [],
            str(exc),
            "Provide the configured GitHub credential and matching repository binding.",
        )
    return _github_access(target, invocation, session)


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


def github_publication_access(
    target: Target, *, state: SkillState | None = None
) -> Surface:
    """Verify repository administration and declared required checks.

    :param target: Runtime target containing GitHub repository and required checks.
    :param state: Shared invocation whose GitHub identity is established at most once.
    :returns: PASS surface when repository access, administration, and all declared
        checks are verified; otherwise a BLOCKED surface with recovery guidance.
    :raises TargetError: If a configured credential file becomes invalid after
        the initial access probe and before the publication probes.
    :raises OSError: If a publication probe process cannot be started.
    :raises ValueError: If the target does not select a GitHub authority.
    """  # noqa: DOC502 - Target, credential, and native process helpers propagate failures.
    selected = target.require_github()
    invocation = state if state is not None else SkillState()
    if invocation.spec is None:
        invocation.spec = load_spec()
    try:
        session = _github_session(target, invocation)
    except ValueError as exc:
        return _blocked(
            "github",
            f"{selected.host}/{selected.repository}",
            [],
            str(exc),
            "Provide the configured GitHub credential and matching repository binding.",
        )
    base = _github_access(target, invocation, session)
    if base.status != PASS:
        return base
    identity = require_github_runtime(invocation, session, selected.repository)
    if identity.role != "admin":
        return _blocked(
            "github",
            base.target,
            base.observed_capability,
            "admin_required",
            "Use a repository administrator identity to configure and read back the required check.",
            base.credential_source,
        )
    result = run_command(
        [
            "gh",
            "api",
            "--hostname",
            selected.host,
            f"repos/{selected.repository}/branches/main/protection",
        ],
        env=session.environment,
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
