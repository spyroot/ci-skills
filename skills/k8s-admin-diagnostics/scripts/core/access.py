"""Read-only verification of the three explicitly selected live authorities."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .cilium import (
    HEALTH_COMMAND,
    agent_selector,
    ready_agent_pods,
    valid_health,
)
from .credentials import Sources
from .runtime import CommandResult, error_class, run_command
from .status import BLOCKED, DRY_RUN, PASS
from .target import Target


@dataclass(frozen=True)
class Surface:
    name: str
    status: str
    identity: str | None
    target: str
    observed_capability: list[str]
    next_step: str | None
    reason: str | None = None
    credential_source: str | None = None
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return vars(self)


def kubectl_argv(target: Target, *args: str) -> list[str]:
    """Bind every Kubernetes command to the supplied context and optional file."""
    command = ["kubectl"]
    sources = target.sources if isinstance(target.sources, Sources) else None
    if sources and len(sources.kubeconfig_files) == 1:
        command += ["--kubeconfig", str(sources.kubeconfig_files[0])]
    elif target.kubernetes.kubeconfig:
        command += ["--kubeconfig", str(target.kubernetes.kubeconfig)]
    command += ["--context", target.kubernetes.context, *args]
    return command


def glab_argv(target: Target, endpoint: str) -> list[str]:
    return [
        "glab",
        "api",
        "--hostname",
        target.gitlab.host,
        "--method",
        "GET",
        endpoint,
    ]


def _token_file(path: Path | None, variable: str) -> dict[str, str] | None:
    """Load one selected host token into a child environment, never a report."""
    if path is None:
        return None
    try:
        if not path.is_file() or path.stat().st_size > 4096:
            raise ValueError("credential_file_unavailable")
        token = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError("credential_file_unavailable") from exc
    if not token or any(character.isspace() for character in token):
        raise ValueError("credential_file_invalid")
    return {variable: token}


def github_env(target: Target) -> dict[str, str | None] | None:
    if isinstance(target.sources, Sources):
        return target.sources.github.environment
    variable = (
        "GH_TOKEN" if target.github.host == "github.com" else "GH_ENTERPRISE_TOKEN"
    )
    return _token_file(target.github.token_file, variable)


def gitlab_env(target: Target) -> dict[str, str | None] | None:
    if isinstance(target.sources, Sources):
        return {**target.sources.gitlab.environment, "GITLAB_HOST": target.gitlab.host}
    return _token_file(target.gitlab.token_file, "GITLAB_TOKEN")


def kubernetes_env(target: Target) -> dict[str, str | None] | None:
    """Use the kubeconfig source frozen before the live access gate."""
    if isinstance(target.sources, Sources):
        return target.sources.kubernetes.environment
    return None


def _json(result: CommandResult) -> Any:
    if result.returncode:
        raise ValueError(error_class(result))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_json") from exc


def _blocked(
    name: str,
    target: str,
    observed: list[str],
    reason: str,
    next_step: str,
    credential_source: str | None = None,
) -> Surface:
    return Surface(
        name, BLOCKED, None, target, observed, next_step, reason, credential_source
    )


def github_access(target: Target) -> Surface:
    host = target.github.host
    repo = target.github.repository
    observed: list[str] = []
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
        if repository.get("full_name", "").lower() != repo.lower():
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


def github_publication_access(target: Target) -> Surface:
    """Extra check before changing required-check settings for publication."""
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
        protection = json.loads(result.stdout)
        required = protection["required_status_checks"]
        contexts = required.get("contexts", [])
        checks = required.get("checks", [])
        names = sorted(
            {name for name in contexts if isinstance(name, str) and name}
            | {
                check.get("context")
                for check in checks
                if isinstance(check, dict)
                and isinstance(check.get("context"), str)
                and check.get("context")
            }
        )
        if not names:
            raise ValueError("required_checks_missing")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return _blocked(
            "github",
            base.target,
            base.observed_capability,
            "required_checks_missing",
            "Configure and read back at least one required main-branch status check.",
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


def gitlab_access(target: Target) -> Surface:
    host = target.gitlab.host
    observed: list[str] = []
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


def cilium_discovery(target: Target) -> tuple[str, dict[str, Any]]:
    """Resolve the agent namespace and its DaemonSet selector in ONE read.

    The selector comes from the same listing that resolves the namespace; a
    second namespaced read would issue an extra API call and give the preflight
    its own discovery path, which is how it came to disagree with the collector.
    """
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
    """Resolve a credential path against its sole kubeconfig source."""
    selected = Path(value).expanduser()
    if not selected.is_absolute():
        sources = target.sources if isinstance(target.sources, Sources) else None
        if sources is None or len(sources.kubeconfig_files) != 1:
            raise ValueError("relative_kubeconfig_credential_unresolved")
        selected = sources.kubeconfig_files[0].parent / selected
    return selected.resolve()


def _auth_mechanism(user: dict[str, Any], target: Target) -> dict[str, str]:
    """Identify the selected kubeconfig user's configured auth mechanism."""
    options: list[dict[str, str]] = []
    if user.get("tokenFile"):
        selected = _credential_path(user["tokenFile"], target)
        if not selected.is_file() or not selected.stat().st_size:
            raise ValueError("token_file_unavailable")
        options.append({"type": "tokenFile", "source": str(selected)})
    if user.get("token"):
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


def kubernetes_access(target: Target) -> Surface:
    name = target.kubernetes.context
    server = target.kubernetes.server
    observed: list[str] = []
    source = (
        target.sources.kubernetes.reference
        if isinstance(target.sources, Sources)
        else None
    )
    details: dict[str, Any] = {}
    try:
        if target.kubernetes.kubeconfig:
            path = target.kubernetes.kubeconfig
            if not path.is_file():
                raise ValueError("kubeconfig_unavailable")
            try:
                with path.open("rb") as handle:
                    if not handle.read(1):
                        raise ValueError("kubeconfig_empty")
            except OSError as exc:
                raise ValueError("kubeconfig_unreadable") from exc
            observed.append("kubeconfig_read")
        config = _json(
            run_command(
                kubectl_argv(target, "config", "view", "--raw", "-o", "json"),
                env=kubernetes_env(target),
            )
        )
        if not isinstance(config, dict):
            raise TypeError("invalid_context_response")
        contexts = [
            item for item in config.get("contexts", []) if item.get("name") == name
        ]
        if len(contexts) != 1:
            raise ValueError("context_not_unique")
        cluster_name = contexts[0].get("context", {}).get("cluster")
        user_name = contexts[0].get("context", {}).get("user")
        clusters = [
            item
            for item in config.get("clusters", [])
            if item.get("name") == cluster_name
        ]
        actual = (
            clusters[0].get("cluster", {}).get("server") if len(clusters) == 1 else None
        )
        if actual != server:
            raise ValueError("api_server_mismatch")
        if clusters[0].get("cluster", {}).get("insecure-skip-tls-verify") is True:
            raise ValueError("tls_verification_disabled")
        if isinstance(target.sources, Sources):
            users = [
                item
                for item in config.get("users", [])
                if item.get("name") == user_name
            ]
            if len(users) != 1 or not isinstance(users[0].get("user"), dict):
                raise ValueError("kubeconfig_user_unresolved")
            details = {
                "context": name,
                "user_entry": user_name,
                "api_server": server,
                "kubeconfig_files": [
                    str(path) for path in target.sources.kubeconfig_files
                ],
                "auth_mechanism": _auth_mechanism(users[0]["user"], target),
            }
        observed.append("context_server_match")
        _json(
            run_command(
                kubectl_argv(target, "get", "--raw=/version"),
                env=kubernetes_env(target),
            )
        )
        observed.append("tls_api_reachable")
        whoami = _json(
            run_command(
                kubectl_argv(target, "auth", "whoami", "-o", "json"),
                env=kubernetes_env(target),
            )
        )
        if not isinstance(whoami, dict):
            raise TypeError("invalid_identity_response")
        identity = whoami.get("status", {}).get("userInfo", {}).get("username")
        if not identity:
            identity = whoami.get("userInfo", {}).get("username")
        if not isinstance(identity, str) or not identity:
            raise ValueError("missing_identity")
        observed.append("effective_identity")
        checks = [
            ("cluster_wildcard", ["auth", "can-i", "*", "*", "--all-namespaces", "-q"]),
            (
                "clusterrolebinding_create",
                [
                    "auth",
                    "can-i",
                    "create",
                    "clusterrolebindings.rbac.authorization.k8s.io",
                    "-q",
                ],
            ),
            (
                "clusterrolebinding_update",
                [
                    "auth",
                    "can-i",
                    "update",
                    "clusterrolebindings.rbac.authorization.k8s.io",
                    "-q",
                ],
            ),
            (
                "clusterrolebinding_delete",
                [
                    "auth",
                    "can-i",
                    "delete",
                    "clusterrolebindings.rbac.authorization.k8s.io",
                    "-q",
                ],
            ),
            (
                "clusterrole_bind",
                [
                    "auth",
                    "can-i",
                    "bind",
                    "clusterroles.rbac.authorization.k8s.io",
                    "-q",
                ],
            ),
        ]
        for label, args in checks:
            result = run_command(
                kubectl_argv(target, *args), env=kubernetes_env(target)
            )
            if result.returncode:
                raise ValueError(label + "_denied")
            observed.append(label)
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
        # Agents come from the DaemonSet's own selector, the same discovery the
        # collector uses. A name prefix also matches cilium-operator-* and
        # cilium-envoy-*, which carry no health endpoint.
        ready = ready_agent_pods(pods["items"], agent_pod_selector, namespace)
        if not ready:
            raise ValueError("cilium_ready_pod_missing")
        pod_name = (ready[0].get("metadata") or {}).get("name")
        health_result = run_command(
            kubectl_argv(
                target,
                "-n",
                namespace,
                "exec",
                pod_name,
                "--",
                *HEALTH_COMMAND,
            ),
            timeout=30,
            env=kubernetes_env(target),
        )
        if health_result.returncode:
            raise ValueError("cilium_health_exec_failed")
        health = _json(health_result)
        # Parseable JSON is not a health response.
        if not valid_health(health):
            raise TypeError("invalid_cilium_health_response")
        observed.append("cilium_health_exec")
        details["cilium_health_pod"] = pod_name
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
            f"{name} -> {server}",
            observed,
            str(exc),
            "Verify the exact context, API TLS, cluster admin RBAC, and Cilium exec permission.",
            source,
        )
    return Surface(
        "kubernetes",
        PASS,
        identity,
        f"{name} -> {server}",
        observed,
        None,
        credential_source=source,
        details=details,
    )


def check_access(target: Target, *, publication: bool = False) -> dict[str, Any]:
    """Probe independent authorities concurrently, then fail closed on any denial."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        github_check = github_publication_access if publication else github_access
        futures = [
            pool.submit(check, target)
            for check in (github_check, gitlab_access, kubernetes_access)
        ]
        surfaces = [future.result() for future in futures]
    receipt = {
        "schema_version": "1.0",
        "kind": "access_check",
        "status": PASS if all(item.status == PASS for item in surfaces) else BLOCKED,
        "publication": publication,
        "surfaces": {item.name: item.as_dict() for item in surfaces},
    }
    if isinstance(target.sources, Sources):
        receipt.update(
            {
                "execution_host": target.sources.execution_host,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "tested_revision": target.tested_revision,
                "skill": target.skill,
                "consuming_project": (target.skill or {}).get("consuming_project"),
                "credential_sources": {
                    "github": target.sources.github.reference,
                    "gitlab": target.sources.gitlab.reference,
                    "kubernetes": target.sources.kubernetes.reference,
                },
                "targets": {
                    "github": f"{target.github.host}/{target.github.repository}",
                    "gitlab": target.gitlab.url,
                    "kubernetes": {
                        "context": target.kubernetes.context,
                        "server": target.kubernetes.server,
                    },
                },
            }
        )
    return receipt


def dry_run_access(target: Target, *, publication: bool = False) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": "access_check",
        "status": DRY_RUN,
        "publication": publication,
        "surfaces": {
            "github": {
                "target": f"{target.github.host}/{target.github.repository}",
                "probes": [
                    "gh auth status",
                    "gh api user",
                    "gh api repository",
                    *(["repository admin"] if publication else []),
                ],
            },
            "gitlab": {
                "target": target.gitlab.url,
                "probes": [
                    "glab auth status",
                    "GET /user is_admin",
                    "GET /runners/all",
                ],
            },
            "kubernetes": {
                "target": f"{target.kubernetes.context} -> {target.kubernetes.server}",
                "probes": [
                    "context server",
                    "TLS API version",
                    "auth whoami",
                    "cluster wildcard",
                    "clusterrolebinding admin",
                    "Cilium namespace",
                    "pods/exec",
                ],
            },
        },
    }
