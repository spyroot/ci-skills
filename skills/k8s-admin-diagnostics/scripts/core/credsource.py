"""Resolve the effective credential source for each selected authority.

A saved CLI login does not prove its stored credential was used: `GH_TOKEN`,
`GH_ENTERPRISE_TOKEN` and `GITLAB_TOKEN` override a host profile or keyring
entry, and a `KUBECONFIG` list overrides the default kubeconfig. This module
answers which source actually authenticates, which sources are present but
shadowed, and for Kubernetes which file supplied the context and user entry
and what authentication mechanism that entry uses.

It records references only -- an absolute path, a named environment variable,
or a credential-store reference. It never reads, returns or logs a secret
value, and the Kubernetes resolution goes through `kubectl config view`, whose
output is redacted by the client, rather than reading a kubeconfig directly.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runtime import run_command
from .target import Target

ENVIRONMENT = "environment"
TOKEN_FILE = "token_file"
CLI_PROFILE = "cli_profile"
KUBECONFIG = "kubeconfig"
UNRESOLVED = "unresolved"

# gh and glab both prefer an environment token over a stored profile, so the
# ordering here mirrors the client's own precedence rather than our preference.
GITHUB_ENV_DEFAULT = ("GH_TOKEN", "GITHUB_TOKEN")
GITHUB_ENV_ENTERPRISE = ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
GITLAB_ENV = ("GITLAB_TOKEN", "GITLAB_ACCESS_TOKEN", "OAUTH_TOKEN")

KUBE_MECHANISMS = (
    ("token", "bearer_token"),
    ("tokenFile", "bearer_token_file"),
    ("client-certificate-data", "client_certificate"),
    ("client-certificate", "client_certificate_file"),
    ("exec", "exec_plugin"),
    ("auth-provider", "auth_provider"),
    ("username", "basic_auth"),
)


class CredentialSourceError(ValueError):
    """An authority has no resolvable effective credential source."""


@dataclass(frozen=True)
class CredentialSource:
    """Where the credential that authenticates one surface actually comes from."""

    surface: str
    kind: str
    reference: str
    considered: list[str] = field(default_factory=list)
    shadowed: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "kind": self.kind,
            "reference": self.reference,
            "considered": list(self.considered),
            "shadowed": list(self.shadowed),
            "detail": dict(self.detail),
        }


def _present_env(names: tuple[str, ...], environ: dict[str, str]) -> list[str]:
    """Name every environment variable that carries a nonempty value."""
    return [name for name in names if (environ.get(name) or "").strip()]


def _github_env_names(host: str) -> tuple[str, ...]:
    return GITHUB_ENV_DEFAULT if host == "github.com" else GITHUB_ENV_ENTERPRISE


def resolve_github_source(target: Target, environ: dict[str, str] | None = None) -> CredentialSource:
    """Resolve which credential authenticates the selected GitHub host."""
    environ = os.environ if environ is None else environ
    host = target.github.host
    env_names = _github_env_names(host)
    present = _present_env(env_names, environ)
    considered = ["target:github.token_file", *(f"env:{name}" for name in env_names), f"gh_profile:{host}"]

    if target.github.token_file is not None:
        # The file is injected as the first environment name, so it wins over
        # both an ambient variable and the stored profile.
        return CredentialSource(
            surface="github", kind=TOKEN_FILE, reference=str(target.github.token_file),
            considered=considered,
            shadowed=[f"env:{name}" for name in present] + [f"gh_profile:{host}"],
            detail={"injected_as": env_names[0], "host": host},
        )
    if present:
        return CredentialSource(
            surface="github", kind=ENVIRONMENT, reference=f"env:{present[0]}",
            considered=considered,
            shadowed=[f"env:{name}" for name in present[1:]] + [f"gh_profile:{host}"],
            detail={"host": host},
        )
    return CredentialSource(
        surface="github", kind=CLI_PROFILE, reference=f"gh_profile:{host}",
        considered=considered, shadowed=[], detail={"host": host},
    )


def resolve_gitlab_source(target: Target, environ: dict[str, str] | None = None) -> CredentialSource:
    """Resolve which credential authenticates the selected GitLab FQDN."""
    environ = os.environ if environ is None else environ
    host = target.gitlab.host
    present = _present_env(GITLAB_ENV, environ)
    considered = ["target:gitlab.token_file", *(f"env:{name}" for name in GITLAB_ENV), f"glab_profile:{host}"]

    if target.gitlab.token_file is not None:
        return CredentialSource(
            surface="gitlab", kind=TOKEN_FILE, reference=str(target.gitlab.token_file),
            considered=considered,
            shadowed=[f"env:{name}" for name in present] + [f"glab_profile:{host}"],
            detail={"injected_as": GITLAB_ENV[0], "host": host},
        )
    if present:
        return CredentialSource(
            surface="gitlab", kind=ENVIRONMENT, reference=f"env:{present[0]}",
            considered=considered,
            shadowed=[f"env:{name}" for name in present[1:]] + [f"glab_profile:{host}"],
            detail={"host": host},
        )
    return CredentialSource(
        surface="gitlab", kind=CLI_PROFILE, reference=f"glab_profile:{host}",
        considered=considered, shadowed=[], detail={"host": host},
    )


def kubeconfig_search_path(target: Target, environ: dict[str, str] | None = None) -> list[Path]:
    """List the kubeconfig files kubectl will read, in the order it reads them."""
    environ = os.environ if environ is None else environ
    if target.kubernetes.kubeconfig is not None:
        return [target.kubernetes.kubeconfig]
    configured = (environ.get("KUBECONFIG") or "").strip()
    if configured:
        return [Path(part).expanduser() for part in configured.split(os.pathsep) if part.strip()]
    return [Path("~/.kube/config").expanduser()]


def _config_names(path: Path, context: str) -> dict[str, Any]:
    """Ask kubectl which context, user and cluster names one file defines."""
    result = run_command(["kubectl", "--kubeconfig", str(path), "config", "view", "-o", "json"])
    if result.returncode:
        return {}
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    contexts = {item.get("name") for item in data.get("contexts") or [] if isinstance(item, dict)}
    users = {item.get("name") for item in data.get("users") or [] if isinstance(item, dict)}
    clusters = {item.get("name") for item in data.get("clusters") or [] if isinstance(item, dict)}
    selected = next(
        (item for item in data.get("contexts") or []
         if isinstance(item, dict) and item.get("name") == context), None,
    )
    return {
        "contexts": contexts, "users": users, "clusters": clusters,
        "selected": (selected or {}).get("context") or {},
    }


def _user_entry(path: str, user_name: str) -> dict[str, Any]:
    """Return one redacted user entry from the file that defines it."""
    result = run_command(["kubectl", "--kubeconfig", path, "config", "view", "-o", "json"])
    if result.returncode:
        return {}
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    entry = next(
        (item.get("user") for item in data.get("users") or []
         if isinstance(item, dict) and item.get("name") == user_name), None,
    )
    return entry if isinstance(entry, dict) else {}


def _mechanism(user_entry: dict[str, Any]) -> str:
    """Name the authentication mechanism from redacted key presence alone."""
    for key, mechanism in KUBE_MECHANISMS:
        if key in user_entry:
            return mechanism
    return "unknown"


def resolve_kubernetes_source(
    target: Target,
    environ: dict[str, str] | None = None,
) -> CredentialSource:
    """Resolve the kubeconfig files, context, user entry, server and mechanism.

    Raises CredentialSourceError when no readable file supplies the context, so
    an unresolved credential blocks instead of falling through to a default.
    """
    context = target.kubernetes.context
    files = kubeconfig_search_path(target, environ)
    considered = [str(path) for path in files]
    readable: list[str] = []
    context_file: str | None = None
    user_file: str | None = None
    user_name: str | None = None
    cluster_name: str | None = None

    for path in files:
        if not path.is_file():
            continue
        readable.append(str(path))
        names = _config_names(path, context)
        if not names:
            continue
        if context_file is None and context in names["contexts"]:
            context_file = str(path)
            selected = names["selected"]
            user_name = selected.get("user")
            cluster_name = selected.get("cluster")
        if user_file is None and user_name and user_name in names["users"]:
            user_file = str(path)

    if context_file is None:
        raise CredentialSourceError(f"kubeconfig_context_unresolved:{context}")
    if user_name and user_file is None:
        raise CredentialSourceError(f"kubeconfig_user_unresolved:{user_name}")

    mechanism = _mechanism(_user_entry(user_file, user_name)) if user_file and user_name else "unknown"

    return CredentialSource(
        surface="kubernetes", kind=KUBECONFIG, reference=context_file,
        considered=considered,
        shadowed=[path for path in readable if path != context_file],
        detail={
            "search_path": considered,
            "readable": readable,
            "context": context,
            "context_from": context_file,
            "user_entry": user_name,
            "user_from": user_file,
            "cluster_entry": cluster_name,
            "server": target.kubernetes.server,
            "authentication_mechanism": mechanism,
            "selected_by": (
                "target.kubernetes.kubeconfig" if target.kubernetes.kubeconfig is not None
                else ("env:KUBECONFIG" if (environ or os.environ).get("KUBECONFIG") else "kubectl_default")
            ),
        },
    )


def resolve_sources(
    target: Target,
    environ: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve all three surfaces, reporting an unresolved one rather than raising."""
    resolved: dict[str, dict[str, Any]] = {
        "github": resolve_github_source(target, environ).as_dict(),
        "gitlab": resolve_gitlab_source(target, environ).as_dict(),
    }
    try:
        resolved["kubernetes"] = resolve_kubernetes_source(target, environ).as_dict()
    except CredentialSourceError as exc:
        resolved["kubernetes"] = CredentialSource(
            surface="kubernetes", kind=UNRESOLVED, reference=str(exc),
            considered=[str(path) for path in kubeconfig_search_path(target, environ)],
            detail={"context": target.kubernetes.context, "server": target.kubernetes.server},
        ).as_dict()
    return resolved
