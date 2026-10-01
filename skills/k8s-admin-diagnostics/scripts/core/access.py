"""Read-only verification of the three explicitly selected live authorities."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .runtime import CommandResult, error_class, run_command
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

    def as_dict(self) -> dict[str, Any]:
        return vars(self)


def kubectl_argv(target: Target, *args: str) -> list[str]:
    """Bind every Kubernetes command to the supplied context and optional file."""
    command = ["kubectl"]
    if target.kubernetes.kubeconfig:
        command += ["--kubeconfig", str(target.kubernetes.kubeconfig)]
    command += ["--context", target.kubernetes.context, *args]
    return command


def glab_argv(target: Target, endpoint: str) -> list[str]:
    return ["glab", "api", "--hostname", target.gitlab.host, "--method", "GET", endpoint]


def _json(result: CommandResult) -> Any:
    if result.returncode:
        raise ValueError(error_class(result))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_json") from exc


def _blocked(name: str, target: str, observed: list[str], reason: str, next_step: str) -> Surface:
    return Surface(name, "BLOCKED", None, target, observed, next_step, reason)


def github_access(target: Target) -> Surface:
    host = target.github.host
    repo = target.github.repository
    observed: list[str] = []
    auth = run_command(["gh", "auth", "status", "--hostname", host])
    if auth.returncode:
        return _blocked("github", f"{host}/{repo}", observed, error_class(auth), "Authenticate gh for the selected host.")
    observed.append("authenticated_host")
    try:
        user = _json(run_command(["gh", "api", "--hostname", host, "user"]))
        repository = _json(run_command(["gh", "api", "--hostname", host, f"repos/{repo}"]))
        if not isinstance(user, dict) or not isinstance(repository, dict):
            raise TypeError("invalid_response")
        if repository.get("full_name", "").lower() != repo.lower():
            raise ValueError("repository_mismatch")
        login = user.get("login")
        if not isinstance(login, str) or not login:
            raise ValueError("missing_identity")
    except (ValueError, TypeError) as exc:
        return _blocked("github", f"{host}/{repo}", observed, str(exc), "Check gh API authentication and repository access for this host.")
    observed += ["identity_read", "repository_read"]
    return Surface("github", "PASS", login, f"{host}/{repo}", observed, None)


def github_publication_access(target: Target) -> Surface:
    """Extra check before changing required-check settings for publication."""
    base = github_access(target)
    if base.status != "PASS":
        return base
    result = run_command([
        "gh", "api", "--hostname", target.github.host,
        f"repos/{target.github.repository}/branches/main/protection",
    ])
    # Unprotected repositories return 404, so the repository permissions API is authoritative.
    info = run_command([
        "gh", "api", "--hostname", target.github.host,
        f"repos/{target.github.repository}", "--jq", ".permissions.admin",
    ])
    if info.returncode or info.stdout.strip().lower() != "true":
        return _blocked("github", base.target, base.observed_capability, "admin_required", "Use a repository administrator identity to configure and read back the required check.")
    capabilities = base.observed_capability + ["repository_admin"]
    if not result.returncode:
        capabilities.append("protection_read")
    return Surface("github", "PASS", base.identity, base.target, capabilities, None)


def gitlab_access(target: Target) -> Surface:
    host = target.gitlab.host
    observed: list[str] = []
    auth = run_command(["glab", "auth", "status", "--hostname", host])
    if auth.returncode:
        return _blocked("gitlab", target.gitlab.url, observed, error_class(auth), "Authenticate glab for this exact GitLab FQDN.")
    observed.append("authenticated_host")
    try:
        user = _json(run_command(glab_argv(target, "user")))
        if not isinstance(user, dict) or user.get("is_admin") is not True:
            raise ValueError("instance_admin_required")
        web_url = user.get("web_url")
        if not isinstance(web_url, str) or urlsplit(web_url).netloc.lower() != host:
            raise ValueError("host_mismatch")
        identity = user.get("username")
        if not isinstance(identity, str) or not identity:
            raise ValueError("missing_identity")
        observed.append("instance_admin_identity")
        runners = _json(run_command(glab_argv(target, "runners/all?per_page=1")))
        if not isinstance(runners, list):
            raise TypeError("runner_api_invalid_response")
    except (ValueError, TypeError) as exc:
        return _blocked("gitlab", target.gitlab.url, observed, str(exc), "Use an instance administrator credential with runner API access on the selected host.")
    observed.append("instance_runner_api_read")
    return Surface("gitlab", "PASS", identity, target.gitlab.url, observed, None)


def cilium_namespace(target: Target) -> str:
    data = _json(run_command(kubectl_argv(target, "get", "daemonsets", "--all-namespaces", "-o", "json")))
    items = data.get("items", []) if isinstance(data, dict) else []
    matches = sorted({
        item.get("metadata", {}).get("namespace")
        for item in items if isinstance(item, dict)
        and item.get("metadata", {}).get("name", "").startswith("cilium")
        and item.get("metadata", {}).get("namespace")
    })
    if len(matches) != 1:
        raise ValueError("cilium_namespace_not_unique")
    return matches[0]


def kubernetes_access(target: Target) -> Surface:
    name = target.kubernetes.context
    server = target.kubernetes.server
    observed: list[str] = []
    try:
        config = _json(run_command(kubectl_argv(target, "config", "view", "-o", "json")))
        if not isinstance(config, dict):
            raise TypeError("invalid_context_response")
        contexts = [item for item in config.get("contexts", [])
                    if item.get("name") == name]
        if len(contexts) != 1:
            raise ValueError("context_not_unique")
        cluster_name = contexts[0].get("context", {}).get("cluster")
        clusters = [item for item in config.get("clusters", [])
                    if item.get("name") == cluster_name]
        actual = clusters[0].get("cluster", {}).get("server") if len(clusters) == 1 else None
        if actual != server:
            raise ValueError("api_server_mismatch")
        if clusters[0].get("cluster", {}).get("insecure-skip-tls-verify") is True:
            raise ValueError("tls_verification_disabled")
        observed.append("context_server_match")
        _json(run_command(kubectl_argv(target, "get", "--raw=/version")))
        observed.append("tls_api_reachable")
        whoami = _json(run_command(kubectl_argv(target, "auth", "whoami", "-o", "json")))
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
            ("clusterrolebinding_create", ["auth", "can-i", "create", "clusterrolebindings.rbac.authorization.k8s.io", "-q"]),
            ("clusterrolebinding_update", ["auth", "can-i", "update", "clusterrolebindings.rbac.authorization.k8s.io", "-q"]),
            ("clusterrolebinding_delete", ["auth", "can-i", "delete", "clusterrolebindings.rbac.authorization.k8s.io", "-q"]),
            ("clusterrole_bind", ["auth", "can-i", "bind", "clusterroles.rbac.authorization.k8s.io", "-q"]),
        ]
        for label, args in checks:
            result = run_command(kubectl_argv(target, *args))
            if result.returncode:
                raise ValueError(label + "_denied")
            observed.append(label)
        namespace = cilium_namespace(target)
        observed.append("cilium_namespace:" + namespace)
        result = run_command(kubectl_argv(target, "auth", "can-i", "create", "pods/exec", "-n", namespace, "-q"))
        if result.returncode:
            raise ValueError("pods_exec_denied")
        observed.append("cilium_pods_exec")
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        return _blocked("kubernetes", f"{name} -> {server}", observed, str(exc), "Verify the exact context, API TLS, cluster admin RBAC, and Cilium exec permission.")
    return Surface("kubernetes", "PASS", identity, f"{name} -> {server}", observed, None)


def check_access(target: Target) -> dict[str, Any]:
    """Probe independent authorities concurrently, then fail closed on any denial."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(check, target) for check in (github_access, gitlab_access, kubernetes_access)]
        surfaces = [future.result() for future in futures]
    return {
        "schema_version": "1.0",
        "kind": "access_check",
        "status": "PASS" if all(item.status == "PASS" for item in surfaces) else "BLOCKED",
        "surfaces": {item.name: item.as_dict() for item in surfaces},
    }


def dry_run_access(target: Target) -> dict[str, Any]:
    return {
        "schema_version": "1.0", "kind": "access_check", "status": "DRY_RUN",
        "surfaces": {
            "github": {"target": f"{target.github.host}/{target.github.repository}", "probes": ["gh auth status", "gh api user", "gh api repository"]},
            "gitlab": {"target": target.gitlab.url, "probes": ["glab auth status", "GET /user is_admin", "GET /runners/all"]},
            "kubernetes": {"target": f"{target.kubernetes.context} -> {target.kubernetes.server}", "probes": ["context server", "TLS API version", "auth whoami", "cluster wildcard", "clusterrolebinding admin", "Cilium namespace", "pods/exec"]},
        },
    }
