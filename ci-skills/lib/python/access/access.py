"""Read-only verification of the three explicitly selected live authorities.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from typing import Optional, Union
from urllib.parse import quote, urlsplit

from lib.python.access.credentials import (
    Sources,
    assert_kubeconfig_unchanged,
    read_token_file,
)
from lib.python.core.catalog import GITLAB_VARIABLES, github_variables
from lib.python.core.cli_api import response_decoder
from lib.python.core.gitlab.gitlab_api import GlabAPIClient
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    json_object,
    json_objects,
    json_string,
    json_strings,
)
from lib.python.core.kubernetes.cilium import (
    HEALTH_COMMAND,
    agent_selector,
    ready_agent_pods,
    valid_health,
)
from lib.python.core.runtime import CommandResult, error_class, run_command
from lib.python.core.target import GitLabOperationTarget, Target
from lib.python.sys.status import BLOCKED, DRY_RUN, PASS


@dataclass(frozen=True)
class Surface:
    """Observed access state for one selected authority.

    :param name: Authority identifier used in the access report.
    :param status: PASS or BLOCKED result of the access probes.
    :param identity: Authenticated identity, or ``None`` when unverified.
    :param target: Selected authority or resource identifier.
    :param observed_capability: Capabilities verified before the result was returned.
    :param next_step: Suggested recovery action, or ``None`` on success.
    :param reason: Classified failure, or ``None`` on success.
    :param credential_source: Reportable credential origin, when bound.
    :param details: Additional verified access facts, when available.

    .. attribute :: name
        :type: str

        Authority identifier used in the access report.

    .. attribute :: status
        :type: str

        PASS or BLOCKED result of the access probes.

    .. attribute :: identity
        :type: Optional[str]

        Authenticated identity, or ``None`` when unverified.

    .. attribute :: target
        :type: str

        Selected authority or resource identifier.

    .. attribute :: observed_capability
        :type: Sequence[str]

        Capabilities verified before the result was returned.

    .. attribute :: next_step
        :type: Optional[str]

        Suggested recovery action, or ``None`` on success.

    .. attribute :: reason
        :type: Optional[str]

        Classified failure, or ``None`` on success.

    .. attribute :: credential_source
        :type: Optional[str]

        Reportable credential origin, when bound.

    .. attribute :: details
        :type: Optional[JSONObject]

        Additional verified access facts, when available.
    """

    name: str
    status: str
    identity: Optional[str]
    target: str
    observed_capability: Sequence[str]
    next_step: Optional[str]
    reason: Optional[str] = None
    credential_source: Optional[str] = None
    details: Optional[JSONObject] = None

    def as_dict(self) -> JSONObject:
        """Expose the access result as report fields.

        :returns: Mapping of this surface's fields and their current values.
        """
        return {
            "name": self.name,
            "status": self.status,
            "identity": self.identity,
            "target": self.target,
            "observed_capability": self.observed_capability,
            "next_step": self.next_step,
            "reason": self.reason,
            "credential_source": self.credential_source,
            "details": self.details,
        }


def kubectl_argv(target: Target, *args: str) -> MutableSequence[str]:
    """Build a kubectl command bound to the selected Kubernetes target.

    :param target: ``core.target.Target`` containing the Kubernetes context and
        optional bound kubeconfig sources.
    :param *args: kubectl subcommand and arguments appended after target options.
    :returns: Argument list beginning with ``kubectl``, followed by a kubeconfig
        option when applicable, ``--context`` and its value, then caller arguments.
    :raises ValueError: If the target does not select a Kubernetes authority.
    :raises TargetError: If a previously pinned kubeconfig changed or is unreadable.
    """  # noqa: DOC503 - Credential helpers propagate TargetError.
    if target.kubernetes is None:
        raise ValueError("kubernetes_target_required")
    assert_kubeconfig_unchanged(target.sources)
    command = ["kubectl"]
    sources = target.sources if isinstance(target.sources, Sources) else None
    if sources and len(sources.kubeconfig_files) == 1:
        command += ["--kubeconfig", str(sources.kubeconfig_files[0])]
    elif target.kubernetes.kubeconfig:
        command += ["--kubeconfig", str(target.kubernetes.kubeconfig)]
    command += ["--context", target.kubernetes.context, *args]
    return command


def oc_argv(target: Target, *args: str) -> MutableSequence[str]:
    """Build an OpenShift command using the same Kubernetes target binding.

    :param target: ``core.target.Target`` containing context and kubeconfig sources.
    :param *args: oc subcommand and arguments appended after target options.
    :returns: Argument list beginning with ``oc``, optional kubeconfig selection,
        the selected context, and caller arguments.
    :raises ValueError: If the target does not select a Kubernetes authority.
    :raises TargetError: If a previously pinned kubeconfig changed or is unreadable.
    """  # noqa: DOC502 - kubectl_argv validates target and kubeconfig.
    return ["oc", *kubectl_argv(target, *args)[1:]]


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


def _token_file(
    path: Optional[Union[str, PathLike[str]]], variable: str
) -> Optional[Mapping[str, str]]:
    """Create a child-process credential override when a file is selected.

    :param path: Selected token filename, or ``None`` to leave native CLI
        authentication unchanged.
    :param variable: Environment variable under which the token is supplied.
    :returns: Mapping from the variable name to the token, or ``None`` when
        no credential file is configured.
    :raises TargetError: If the selected file cannot provide a valid token.
    :raises TypeError: If a nonempty selection is not a text path-like value.
    """  # noqa: DOC502 - read_token_file validates and reads the path.
    return None if path is None else {variable: read_token_file(path)}


def github_env(target: Target) -> Optional[Mapping[str, Optional[str]]]:
    """Select credential overrides for a GitHub child process.

    :param target: Runtime target with GitHub settings and optional bound sources.
    :returns: Bound environment overrides, a selected token-file override, or
        ``None`` when the native CLI should use its existing authentication.
    :raises TargetError: If an explicitly selected token file is invalid.
    :raises ValueError: If the target does not select a GitHub authority.
    """  # noqa: DOC503 - Credential helpers propagate TargetError.
    if target.github is None:
        raise ValueError("github_target_required")
    if isinstance(target.sources, Sources):
        return target.sources.github.environment
    return _token_file(
        target.github.token_file, github_variables(target.github.host)[0]
    )


def gitlab_env(target: Target) -> Optional[Mapping[str, Optional[str]]]:
    """Select credential overrides for a GitLab child process.

    :param target: Runtime target with GitLab settings and optional bound sources.
    :returns: Bound overrides including the GitLab host, a token-file override,
        or ``None`` when the native CLI should use its existing authentication.
    :raises TargetError: If an explicitly selected token file is invalid.
    :raises ValueError: If the target does not select a GitLab authority.
    """  # noqa: DOC503 - Credential helpers propagate TargetError.
    if target.gitlab is None:
        raise ValueError("gitlab_target_required")
    if isinstance(target.sources, Sources):
        return {**target.sources.gitlab.environment, "GITLAB_HOST": target.gitlab.host}
    return _token_file(target.gitlab.token_file, GITLAB_VARIABLES[0])


def kubernetes_env(target: Target) -> Optional[Mapping[str, Optional[str]]]:
    """Use the kubeconfig environment frozen during source binding.

    :param target: Runtime target with optional bound credential sources.
    :returns: Bound Kubernetes environment overrides, or ``None`` when unbound.
    """
    if isinstance(target.sources, Sources):
        return target.sources.kubernetes.environment
    return None


def _json(result: CommandResult) -> JSONValue:
    """Decode successful native-command output for an access probe.

    :param result: Command exit status, stdout, and stderr.
    :returns: JSON value decoded from stdout.
    :raises ValueError: If the command failed or stdout is not valid JSON.
    """
    if result.returncode:
        raise ValueError(error_class(result))
    try:
        return response_decoder().decode(result.stdout)
    except (ValueError, RecursionError) as exc:
        raise ValueError("invalid_json") from exc


def _blocked(
    name: str,
    target: str,
    observed: Sequence[str],
    reason: str,
    next_step: str,
    credential_source: Optional[str] = None,
) -> Surface:
    """Create a failed access result with the observations already collected.

    :param name: Authority identifier used in the report.
    :param target: Selected authority or resource identifier.
    :param observed: Capabilities verified before the failure.
    :param reason: Classified failure reason.
    :param next_step: Suggested recovery action for the operator.
    :param credential_source: Reportable credential origin, when available.
    :returns: BLOCKED surface result without an authenticated identity.
    """
    return Surface(
        name, BLOCKED, None, target, observed, next_step, reason, credential_source
    )


def github_access(target: Target) -> Surface:
    """Verify GitHub authentication and access to the selected repository.

    :param target: Runtime target containing the GitHub host and repository.
    :returns: PASS surface with verified login and repository access, or BLOCKED
        surface with the failed capability and a suggested recovery action.
    :raises ValueError: If the target does not select a GitHub authority.
    :raises OSError: If a native authentication or API process cannot be started.
    """  # noqa: DOC503 - Response validation is caught; native I/O can escape.
    if target.github is None:
        raise ValueError("github_target_required")
    host = target.github.host
    repo = target.github.repository
    observed: MutableSequence[str] = []
    source = (
        target.sources.github.reference if isinstance(target.sources, Sources) else None
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
        base.observed_capability
        + ["repository_admin", "protection_read", "required_checks_read"],
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
    """  # noqa: DOC503 - Credential and process helpers propagate failures.
    if target.github is None:
        raise ValueError("github_target_required")
    base = github_access(target)
    if base.status != PASS:
        return base
    result = run_command(
        [
            "gh",
            "api",
            "--hostname",
            target.github.host,
            f"repos/{target.github.repository}/branches/main/protection",
        ],
        env=github_env(target),
    )
    info = run_command(
        [
            "gh",
            "api",
            "--hostname",
            target.github.host,
            f"repos/{target.github.repository}",
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
    return _publication_result(base, target.github.required_checks, names)


def gitlab_access(target: Target) -> Surface:
    """Verify instance-admin identity and runner-list access on GitLab.

    :param target: Runtime target containing the selected GitLab host.
    :returns: PASS surface with the verified username, or BLOCKED surface when
        authentication, host identity, admin status, or runner access fails.
    :raises ValueError: If the target does not select a GitLab authority.
    :raises OSError: If a native authentication or API process cannot be started.
    """  # noqa: DOC503 - Response validation is caught; native I/O can escape.
    if target.gitlab is None:
        raise ValueError("gitlab_target_required")
    host = target.gitlab.host
    observed: MutableSequence[str] = []
    source = (
        target.sources.gitlab.reference if isinstance(target.sources, Sources) else None
    )
    try:
        credential = gitlab_env(target)
    except ValueError as exc:
        return _blocked(
            "gitlab",
            target.gitlab.url,
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
            target.gitlab.url,
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
            target.gitlab.url,
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
        target.gitlab.url,
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
        "skill": session.skill,
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


def cilium_discovery(target: Target) -> tuple[str, JSONObject]:
    """Resolve a unique Cilium namespace and agent selector in one read.

    :param target: Runtime target selecting the Kubernetes authority.
    :returns: Cilium namespace and the matching DaemonSet's label selector.
    :raises TypeError: If the API result is not a DaemonSet list.
    :raises ValueError: If the read fails, namespace is ambiguous, or selector is absent.
    :raises TargetError: If a pinned kubeconfig changed or cannot be read.
    :raises OSError: If the native DaemonSet query cannot be started.
    """  # noqa: DOC503 - Kubeconfig validation and native reads can escape.
    data = _json(
        run_command(
            kubectl_argv(target, "get", "daemonsets", "--all-namespaces", "-o", "json"),
            env=kubernetes_env(target),
        )
    )
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise TypeError("invalid_daemonset_list")
    items = data["items"]
    matches = sorted(
        {
            item.get("metadata", {}).get("namespace")
            for item in items
            if isinstance(item, dict)
            and item.get("metadata", {}).get("name") == "cilium"
            and item.get("metadata", {}).get("namespace")
        }
    )
    if len(matches) != 1:
        raise ValueError("cilium_namespace_not_unique")
    namespace = matches[0]
    selector = agent_selector(items, namespace)
    if selector is None:
        raise ValueError("cilium_agent_selector_missing")
    return namespace, selector


def _credential_path(value: str, target: Target) -> Path:
    """Resolve a kubeconfig credential path against its source file.

    :param value: Absolute credential path or path relative to its kubeconfig.
    :param target: Runtime target containing the bound kubeconfig sources.
    :returns: Expanded absolute credential path.
    :raises ValueError: If a relative path has no unique bound kubeconfig source.
    """
    selected = Path(value).expanduser()
    if not selected.is_absolute():
        sources = target.sources if isinstance(target.sources, Sources) else None
        if sources is None or len(sources.kubeconfig_files) != 1:
            raise ValueError("relative_kubeconfig_credential_unresolved")
        selected = sources.kubeconfig_files[0].parent / selected
    return selected.resolve()


def _auth_mechanism(user: JSONObject, target: Target) -> Mapping[str, str]:
    """Identify the selected kubeconfig user's single authentication mechanism.

    :param user: Selected kubeconfig user mapping.
    :param target: Runtime target used to resolve external credential paths.
    :returns: Mapping describing the mechanism type and its nonsecret source.
    :raises ValueError: If credential files are unavailable or no unique mechanism
        can be determined.
    """
    options: MutableSequence[Mapping[str, str]] = []
    if "tokenFile" in user:
        token_file = user["tokenFile"]
        if not isinstance(token_file, str) or not token_file:
            raise ValueError("token_file_unavailable")
        selected = _credential_path(token_file, target)
        if not selected.is_file():
            raise ValueError("token_file_unavailable")
        try:
            with selected.open("rb") as token_handle:
                if not token_handle.read(1):
                    raise ValueError("token_file_unavailable")
        except OSError as exc:
            raise ValueError("token_file_unavailable") from exc
        options.append({"type": "tokenFile", "source": str(selected)})
    elif user.get("token"):
        options.append(
            {"type": "embedded-token", "source": "selected kubeconfig user entry"}
        )
    if user.get("client-certificate-data") and user.get("client-key-data"):
        options.append(
            {
                "type": "embedded-client-certificate",
                "source": "selected kubeconfig user entry",
            }
        )
    if user.get("client-certificate") and user.get("client-key"):
        certificate = _credential_path(user["client-certificate"], target)
        key = _credential_path(user["client-key"], target)
        if not certificate.is_file() or not key.is_file():
            raise ValueError("client_certificate_file_unavailable")
        options.append(
            {"type": "client-certificate", "source": f"{certificate} | {key}"}
        )
    if isinstance(user.get("exec"), dict) and user["exec"].get("command"):
        options.append(
            {"type": "exec-provider", "source": str(user["exec"]["command"])}
        )
    if len(options) != 1:
        raise ValueError("kubeconfig_auth_mechanism_unresolved")
    return options[0]


def _cilium_access(target: Target) -> tuple[Sequence[str], JSONObject]:
    """Verify permission and read health from a ready Cilium agent.

    :param target: Runtime target selecting the Kubernetes authority.
    :returns: Verified capability names and the pod used for the health read.
    :raises ValueError: If discovery, exec permission, readiness, or the read fails.
    :raises TypeError: If pod-list or health responses have an invalid shape.
    :raises TargetError: If a pinned kubeconfig changed or cannot be read.
    :raises OSError: If a native permission, Pod, or health process cannot be started.
    """  # noqa: DOC503 - Kubeconfig validation and native reads can escape.
    observed: MutableSequence[str] = []
    namespace, agent_pod_selector = cilium_discovery(target)
    observed.append("cilium_namespace:" + namespace)
    result = run_command(
        kubectl_argv(
            target, "auth", "can-i", "create", "pods/exec", "-n", namespace, "-q"
        ),
        env=kubernetes_env(target),
    )
    if result.returncode:
        raise ValueError("pods_exec_denied")

    observed.append("cilium_pods_exec")
    pods = _json(
        run_command(
            kubectl_argv(target, "-n", namespace, "get", "pods", "-o", "json"),
            env=kubernetes_env(target),
        )
    )
    if not isinstance(pods, dict) or not isinstance(pods.get("items"), list):
        raise TypeError("invalid_cilium_pod_list")

    ready = ready_agent_pods(pods["items"], agent_pod_selector, namespace)
    if not ready:
        raise ValueError("cilium_ready_pod_missing")
    pod_name = (ready[0].get("metadata") or {}).get("name")
    health_result = run_command(
        kubectl_argv(target, "-n", namespace, "exec", pod_name, "--", *HEALTH_COMMAND),
        timeout=30,
        env=kubernetes_env(target),
    )
    if health_result.returncode:
        raise ValueError("cilium_health_exec_failed")
    if not valid_health(_json(health_result)):
        raise TypeError("invalid_cilium_health_response")
    observed.append("cilium_health_exec")
    return observed, {"cilium_health_pod": pod_name}


class KubernetesAccessProbe:
    """Collect Kubernetes access evidence through the selected native context."""

    def __init__(self, target: Target):
        """Keep the selected target and observations for one access attempt.

        :param target: Runtime target with Kubernetes settings and bound sources.
        :raises ValueError: If the target has no Kubernetes authority.
        """
        if target.kubernetes is None:
            raise ValueError("kubernetes_target_required")
        self.target = target
        self.kubernetes = target.kubernetes
        self.observed: MutableSequence[str] = []
        self.details: MutableMapping[str, JSONValue] = {}

    def read(self, *arguments: str) -> JSONValue:
        """Read JSON through the already selected kubectl context.

        :param *arguments: Native kubectl arguments after the context selection.
        :returns: Validated JSON value returned by the native command.
        :raises ValueError: If the command fails or returns invalid JSON.
        :raises TargetError: If a pinned kubeconfig changed or is unreadable.
        :raises OSError: If the native process cannot be started.
        """  # noqa: DOC502 - JSON, kubeconfig, and process helpers can fail.
        return _json(
            run_command(
                kubectl_argv(self.target, *arguments),
                env=kubernetes_env(self.target),
            )
        )

    def check_file(self) -> None:
        """Verify the explicitly selected kubeconfig file is readable and nonempty.

        :raises ValueError: If a selected file is missing, empty, or unreadable.
        """
        path = self.kubernetes.kubeconfig
        if path is None:
            return
        if not path.is_file():
            raise ValueError("kubeconfig_unavailable")
        try:
            with path.open("rb") as handle:
                if not handle.read(1):
                    raise ValueError("kubeconfig_empty")
        except OSError as exc:
            raise ValueError("kubeconfig_unreadable") from exc
        self.observed.append("kubeconfig_read")

    @staticmethod
    def entry(config: JSONObject, collection: str, name: str, field: str) -> JSONObject:
        """Select one named mapping from a kubectl kubeconfig response.

        :param config: Validated kubeconfig JSON object.
        :param collection: Native collection key, such as ``contexts``.
        :param name: Exact entry name referenced by the selected context.
        :param field: Nested mapping containing that entry's configuration.
        :returns: Configuration mapping of the uniquely named entry.
        :raises ValueError: If the entry is missing or duplicated.
        :raises TypeError: If the collection or selected entry has an invalid shape.
        """
        entries = config.get(collection)
        if not isinstance(entries, list):
            raise TypeError("invalid_context_response")
        matches = [
            item
            for item in entries
            if isinstance(item, Mapping) and item.get("name") == name
        ]
        reason = (
            "context_not_unique"
            if collection == "contexts"
            else "kubeconfig_entry_unresolved"
        )
        if len(matches) != 1:
            raise ValueError(reason)
        selected = matches[0].get(field)
        if not isinstance(selected, Mapping):
            raise TypeError("invalid_context_response")
        return selected

    def check_context(self) -> None:
        """Verify the selected context's API server and TLS configuration.

        :raises ValueError: If configuration cannot identify the selected server,
            TLS verification is disabled, or bound credentials are unresolved.
        :raises TypeError: If kubectl configuration or its entries have an invalid shape.
        :raises TargetError: If pinned kubeconfig files changed or are unreadable.
        :raises OSError: If the native process cannot be started.
        """  # noqa: DOC503 - Kubeconfig validation and native reads can escape.
        config = self.read("config", "view", "--raw", "-o", "json")
        if not isinstance(config, Mapping):
            raise TypeError("invalid_context_response")
        context = self.entry(config, "contexts", self.kubernetes.context, "context")
        cluster_name = context.get("cluster")
        if not isinstance(cluster_name, str) or not cluster_name:
            raise ValueError("api_server_mismatch")
        cluster = self.entry(config, "clusters", cluster_name, "cluster")
        if cluster.get("server") != self.kubernetes.server:
            raise ValueError("api_server_mismatch")
        if cluster.get("insecure-skip-tls-verify") is True:
            raise ValueError("tls_verification_disabled")
        if isinstance(self.target.sources, Sources):
            self.describe_credentials(config, context)
        self.observed.append("context_server_match")

    def describe_credentials(self, config: JSONObject, context: JSONObject) -> None:
        """Record the resolved kubeconfig authentication mechanism without secrets.

        :param config: Native kubeconfig object containing user entries.
        :param context: Selected context referring to one user entry.
        :raises ValueError: If the user reference or its mechanism is unresolved.
        :raises TypeError: If user entries or the bound credential source are invalid.
        """
        user_name = context.get("user")
        if not isinstance(user_name, str) or not user_name:
            raise ValueError("kubeconfig_user_unresolved")
        user = self.entry(config, "users", user_name, "user")
        sources = self.target.sources
        if not isinstance(sources, Sources):
            raise TypeError("required_credential_source_missing")
        self.details.update(
            {
                "context": self.kubernetes.context,
                "user_entry": user_name,
                "api_server": self.kubernetes.server,
                "kubeconfig_files": [str(path) for path in sources.kubeconfig_files],
                "auth_mechanism": _auth_mechanism(user, self.target),
            }
        )

    def identity(self) -> str:
        """Verify TLS access and read the effective Kubernetes username.

        :returns: Nonempty username returned by the selected API server.
        :raises ValueError: If API access fails or no username is reported.
        :raises TypeError: If the identity response is not a JSON object.
        :raises TargetError: If pinned kubeconfig files changed or are unreadable.
        :raises OSError: If the native process cannot be started.
        """  # noqa: DOC503 - Kubeconfig validation and native reads can escape.
        self.read("get", "--raw=/version")
        self.observed.append("tls_api_reachable")
        whoami = self.read("auth", "whoami", "-o", "json")
        if not isinstance(whoami, Mapping):
            raise TypeError("invalid_identity_response")
        status = whoami.get("status", {})
        user = status.get("userInfo", {}) if isinstance(status, Mapping) else {}
        identity = user.get("username") if isinstance(user, Mapping) else None
        if not identity:
            user = whoami.get("userInfo", {})
            identity = user.get("username") if isinstance(user, Mapping) else None
        if not isinstance(identity, str) or not identity:
            raise ValueError("missing_identity")
        self.observed.append("effective_identity")
        return identity

    def check_permissions(self) -> None:
        """Verify the diagnostic gate's existing cluster administration permissions.

        :raises ValueError: If any required authorization probe is denied.
        :raises TargetError: If pinned kubeconfig files changed or are unreadable.
        :raises OSError: If the native process cannot be started.
        """  # noqa: DOC503 - Kubeconfig validation and native reads can escape.
        checks = (
            ("cluster_wildcard", ("*", "*", "--all-namespaces")),
            (
                "clusterrolebinding_create",
                ("create", "clusterrolebindings.rbac.authorization.k8s.io"),
            ),
            (
                "clusterrolebinding_update",
                ("update", "clusterrolebindings.rbac.authorization.k8s.io"),
            ),
            (
                "clusterrolebinding_delete",
                ("delete", "clusterrolebindings.rbac.authorization.k8s.io"),
            ),
            ("clusterrole_bind", ("bind", "clusterroles.rbac.authorization.k8s.io")),
        )
        for label, arguments in checks:
            result = run_command(
                kubectl_argv(self.target, "auth", "can-i", *arguments, "-q"),
                env=kubernetes_env(self.target),
            )
            if result.returncode:
                raise ValueError(label + "_denied")
            self.observed.append(label)


def kubernetes_access(target: Target, *, cilium: bool = False) -> Surface:
    """Verify cluster identity, TLS access, and the required permissions.

    :param target: Runtime target containing context, expected server, and sources.
    :param cilium: Whether to additionally verify the Cilium health exec path.
    :returns: PASS surface with verified identity and capabilities, or a BLOCKED
        surface with observations made before the failed probe and recovery guidance.
    :raises ValueError: If the caller did not select a Kubernetes authority.
    """  # noqa: DOC502 - Probe construction rejects a missing authority.
    probe = KubernetesAccessProbe(target)
    selected = f"{probe.kubernetes.context} -> {probe.kubernetes.server}"
    source = (
        target.sources.kubernetes.reference
        if isinstance(target.sources, Sources)
        else None
    )
    try:
        probe.check_file()
        probe.check_context()
        identity = probe.identity()
        probe.check_permissions()
        if cilium:
            observed, details = _cilium_access(target)
            probe.observed.extend(observed)
            probe.details.update(details)
    except (
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        OSError,
        AttributeError,
    ) as exc:
        return _blocked(
            "kubernetes",
            selected,
            probe.observed,
            str(exc),
            "Verify the exact context, API TLS, cluster admin RBAC"
            + (", and Cilium exec permission." if cilium else "."),
            source,
        )
    return Surface(
        "kubernetes",
        PASS,
        identity,
        selected,
        probe.observed,
        None,
        credential_source=source,
        details=probe.details,
    )


def access_evidence(gate: JSONObject) -> JSONObject:
    """Extract the access facts attached to a collector report.

    :param gate: Access-gate receipt produced for the current invocation.
    :returns: Mapping of status, identities, selected targets, credential-source
        references, provenance, and a SHA-256 correlator of the supplied receipt.
    :raises TypeError: If the surfaces field or one of its entries is not an object.

    The correlator hashes the in-memory receipt, not a separately redacted file.
    """
    body = json.dumps(gate, sort_keys=True, default=str).encode("utf-8")
    surfaces = gate.get("surfaces") or {}
    if not isinstance(surfaces, Mapping) or any(
        not isinstance(surface, Mapping) for surface in surfaces.values()
    ):
        raise TypeError("invalid_access_surfaces")
    return {
        "profile": gate.get("profile"),
        "status": gate.get("status"),
        "captured_at": gate.get("captured_at"),
        "execution_host": gate.get("execution_host"),
        # Which declared location the target came from. Without it a collector
        # report names the authorities but not what selected them, so a reader
        # cannot tell a project target from the user default -- and the
        # protocol's promise is that every report names its own source.
        "target_file": gate.get("target_file"),
        "target_source": gate.get("target_source"),
        "skill": gate.get("skill"),
        "consuming_project": gate.get("consuming_project"),
        "credential_sources": gate.get("credential_sources"),
        "target_selection": gate.get("target_selection"),
        "targets": gate.get("targets"),
        "identities": {
            name: surface.get("identity")
            for name, surface in surfaces.items()
            if isinstance(surface, Mapping)
        },
        "receipt_sha256": hashlib.sha256(body).hexdigest(),
    }


def check_access(
    target: Target, *, publication: bool = False, cilium: bool = True
) -> JSONObject:
    """Probe active authorities and combine their observed access results.

    :param target: Runtime target declaring active authorities and their sources.
    :param publication: Whether GitHub requires administration and required-check probes.
    :param cilium: Whether Kubernetes requires the Cilium health exec probe.
    :returns: Access receipt with per-surface results; overall PASS requires every
        selected surface to pass, otherwise the receipt reports BLOCKED.
    :raises ValueError: If active surfaces are invalid, publication lacks GitHub,
        or a required bound credential source is absent.
    """
    checks = {
        "github": github_publication_access if publication else github_access,
        "gitlab": gitlab_access,
        "kubernetes": lambda _selected: kubernetes_access(_selected, cilium=cilium),
    }
    _selected = target.active_surfaces

    if not _selected or any(name not in checks for name in _selected):
        raise ValueError("required_target_surfaces_invalid")

    if publication and "github" not in _selected:
        raise ValueError("publication_requires_github")
    with ThreadPoolExecutor(max_workers=len(_selected)) as pool:
        futures = [pool.submit(checks[name], target) for name in _selected]
        surfaces = [future.result() for future in futures]

    receipt = {
        "schema_version": "1.0",
        "kind": "access_check",
        "status": PASS if all(item.status == PASS for item in surfaces) else BLOCKED,
        "publication": publication,
        "surfaces": {item.name: item.as_dict() for item in surfaces},
    }
    if isinstance(target.sources, Sources):
        source_by_name = {
            "github": target.sources.github,
            "gitlab": target.sources.gitlab,
            "kubernetes": target.sources.kubernetes,
        }
        if any(source_by_name[name] is None for name in _selected):
            raise ValueError("required_credential_source_missing")
        targets = {}
        if "github" in _selected:
            targets["github"] = f"{target.github.host}/{target.github.repository}"
        if "gitlab" in _selected:
            targets["gitlab"] = target.gitlab.url
        if "kubernetes" in _selected:
            targets["kubernetes"] = {
                "context": target.kubernetes.context,
                "server": target.kubernetes.server,
            }

        receipt.update(
            {
                "execution_host": target.sources.execution_host,
                "captured_at": datetime.now(UTC).isoformat(),
                "target_file": str(target.source_file) if target.source_file else None,
                "target_source": target.source_kind,
                "tested_revision": target.tested_revision,
                "skill": target.skill,
                "consuming_project": (target.skill or {}).get("consuming_project"),
                "credential_sources": {
                    name: source_by_name[name].reference for name in _selected
                },
                "target_selection": target.target_reference,
                "targets": targets,
            }
        )
    return receipt


def dry_run_access(
    target: Target, *, publication: bool = False, cilium: bool = True
) -> JSONObject:
    """Describe access probes without contacting the selected authorities.

    :param target: Runtime target declaring active authorities and their settings.
    :param publication: Whether to include GitHub publication checks.
    :param cilium: Whether to include Cilium health checks.
    :returns: DRY_RUN access receipt listing selected targets and planned probes.
    :raises ValueError: If publication checks are requested without GitHub.
    """
    selected = target.active_surfaces
    if publication and "github" not in selected:
        raise ValueError("publication_requires_github")
    surfaces = {}
    if "github" in selected:
        surfaces["github"] = {
            "target": f"{target.github.host}/{target.github.repository}",
            "probes": [
                "gh auth status",
                "gh api user",
                "gh api repository",
                *(["repository admin"] if publication else []),
            ],
        }
    if "gitlab" in selected:
        surfaces["gitlab"] = {
            "target": target.gitlab.url,
            "probes": [
                "glab auth status",
                "GET /user is_admin",
                "GET /runners/all",
            ],
        }
    if "kubernetes" in selected:
        surfaces["kubernetes"] = {
            "target": f"{target.kubernetes.context} -> {target.kubernetes.server}",
            "probes": [
                "context server",
                "TLS API version",
                "auth whoami",
                "cluster wildcard",
                "clusterrolebinding admin",
                *(
                    ["Cilium namespace", "pods/exec", "non-TTY Cilium health"]
                    if cilium
                    else []
                ),
            ],
        }
    return {
        "schema_version": "1.0",
        "kind": "access_check",
        "status": DRY_RUN,
        "publication": publication,
        "target_selection": target.target_reference,
        "surfaces": surfaces,
    }
