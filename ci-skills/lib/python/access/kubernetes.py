"""Kubernetes access and native command binding.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from pathlib import Path
from typing import Optional

from lib.python.access.kubernetes_credentials import assert_kubeconfig_unchanged
from lib.python.access.shared import Surface, _blocked, _json
from lib.python.access.sources import Sources
from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    json_object,
    json_string,
)
from lib.python.core.kubernetes.cilium import (
    HEALTH_COMMAND,
    agent_selector,
    ready_agent_pods,
    valid_health,
)
from lib.python.core.runtime import run_command
from lib.python.core.target import Target
from lib.python.sys.status import PASS


def kubectl_argv(target: Target, *args: str) -> MutableSequence[str]:
    """Build a kubectl command bound to the selected Kubernetes target.

    :param target: ``core.target.Target`` containing the Kubernetes context and
        optional bound kubeconfig sources.
    :param *args: kubectl subcommand and arguments appended after target options.
    :returns: Argument list beginning with ``kubectl``, followed by a kubeconfig
        option when applicable, ``--context`` and its value, then caller arguments.
    :raises ValueError: If the target does not select a Kubernetes authority.
    :raises TargetError: If a previously pinned kubeconfig changed or is unreadable.
    """  # noqa: DOC502 - Target and credential selectors propagate validation failures.
    selected = target.require_kubernetes()
    assert_kubeconfig_unchanged(target.sources)
    command = ["kubectl"]
    sources = target.sources if isinstance(target.sources, Sources) else None
    if sources and len(sources.kubeconfig_files) == 1:
        command += ["--kubeconfig", str(sources.kubeconfig_files[0])]
    elif selected.kubeconfig:
        command += ["--kubeconfig", str(selected.kubeconfig)]
    command += ["--context", selected.context, *args]
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


def kubernetes_env(target: Target) -> Optional[Mapping[str, Optional[str]]]:
    """Use the kubeconfig environment frozen during source binding.

    :param target: Runtime target with optional bound credential sources.
    :returns: Bound Kubernetes environment overrides, or ``None`` when unbound.
    :raises ValueError: Bound sources omit the required Kubernetes credential.
    """  # noqa: DOC502 - Sources.require validates the selected credential.
    if isinstance(target.sources, Sources):
        return target.sources.require(SkillPluginType.KUBERNETES).environment
    return None


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
        certificate = _credential_path(json_string(user["client-certificate"]), target)
        key = _credential_path(json_string(user["client-key"]), target)
        if not certificate.is_file() or not key.is_file():
            raise ValueError("client_certificate_file_unavailable")
        options.append(
            {"type": "client-certificate", "source": f"{certificate} | {key}"}
        )
    executable = user.get("exec")
    if isinstance(executable, Mapping) and executable.get("command"):
        options.append({"type": "exec-provider", "source": str(executable["command"])})
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
    pod_name = json_string(json_object(ready[0].get("metadata") or {}).get("name"))
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
        """  # noqa: DOC502 - Target.require_kubernetes rejects an absent provider.
        self.target = target
        self.kubernetes = target.require_kubernetes()
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
        """Verify the selected context's API server using its kubeconfig settings.

        :raises ValueError: If configuration cannot identify the selected server
            or bound credentials are unresolved.
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
        target.sources.require(SkillPluginType.KUBERNETES).reference
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
