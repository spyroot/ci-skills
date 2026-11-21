"""Deliver an Agent's one-time credential into its run-owned Secret."""

from __future__ import annotations

import json
from typing import Final

from lib.python.core.gitlab.cluster_agent.provider import CreatedAgentToken
from lib.python.core.gitlab.cluster_agent.selection import ConnectSelection
from lib.python.core.json_types import json_object
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService
from lib.python.core.kubernetes.resources import (
    MANAGER_LABEL,
    MANAGER_VALUE,
    OPERATION_LABEL,
    ResourceSet,
)
from lib.python.core.kubernetes.run_scope import RUN_ID_LABEL
from lib.python.core.runtime import execution_trace

# Secret data key that holds the Agent token; chart config.secretName reads it.
TOKEN_KEY: Final = "token"
_SECRET_KIND: Final = "Secret"
_SECRET_TYPE: Final = "Opaque"
_NATIVE_FIELDS: Final = (
    "{{.apiVersion}}",
    "{{.kind}}",
    "{{.metadata.namespace}}",
    "{{.metadata.name}}",
    "{{.metadata.uid}}",
    f'{{{{index .metadata.labels "{MANAGER_LABEL}"}}}}',
    f'{{{{index .metadata.labels "{OPERATION_LABEL}"}}}}',
    f'{{{{index .metadata.labels "{RUN_ID_LABEL}"}}}}',
    "{{.type}}",
    "{{.immutable}}",
    "{{if not .metadata.deletionTimestamp}}live{{end}}",
    (
        "{{if .data}}"
        f'{{{{if and (eq (len .data) 1) (index .data "{TOKEN_KEY}")}}}}'
        f"{TOKEN_KEY}{{{{end}}}}{{{{end}}}}"
    ),
)
_OUTPUT: Final = "go-template=" + r'{{"\n"}}'.join((*_NATIVE_FIELDS, ""))


def agent_secret_name(selection: ConnectSelection) -> str:
    """Derive the same exact resource name for creation and logical absence.

    :param selection: Verified consumer Agent name and run scope.
    :returns: Stable DNS-safe resource name including the run suffix.
    """
    return selection.scope.name("agent-token", prefix=selection.agent_name)


class AgentSecretStore:
    """Create the exact Agent credential and read back only safe metadata."""

    def __init__(self, service: KubernetesService, selection: ConnectSelection) -> None:
        """Bind the credential name and labels to the selected Agent scope.

        :param service: Kubernetes transport bound to the approved target.
        :param selection: Exact Agent name and Kubernetes namespace and run.
        """
        self._service = service
        scope = selection.scope
        self._namespace = scope.namespace
        self._run_id = scope.run_id
        self.name = agent_secret_name(selection)
        owned = ResourceSet(service, self._namespace, self._run_id).managed(
            {"metadata": {"name": self.name, "labels": dict(scope.labels)}}
        )
        self._metadata = json_object(owned["metadata"])
        self.labels = json_object(self._metadata["labels"])

    def _uid(self, output: str) -> str:
        fields = output.splitlines()
        identity = ["v1", _SECRET_KIND, self._namespace, self.name]
        ownership = [
            MANAGER_VALUE,
            self._run_id,
            self._run_id,
            _SECRET_TYPE,
            "true",
            "live",
            TOKEN_KEY,
        ]
        if (
            len(fields) != len(_NATIVE_FIELDS)
            or fields[:4] != identity
            or not fields[4].strip()
            or fields[5:] != ownership
        ):
            raise KubernetesError("agent_secret_identity_mismatch")
        return fields[4]

    def read(self) -> str | None:
        """Read the exact Secret without returning its value in command output.

        :returns: Verified Secret UID, or None when Kubernetes reports absence.
        :raises KubernetesError: A read failed or native ownership is inconsistent.
        """
        # Native projection failures can include the source Secret.
        with execution_trace(None):
            result = self._service.command(
                "-n",
                self._namespace,
                "get",
                f"secret/{self.name}",
                "--ignore-not-found",
                "-o",
                _OUTPUT,
            )
        if result.returncode:
            raise KubernetesError("agent_secret_read_unconfirmed")
        return self._uid(result.stdout) if result.stdout else None

    def create(self, credential: CreatedAgentToken) -> str:
        """Consume the token once and create the immutable Secret atomically.

        :param credential: One-time GitLab token whose native ID is already recorded.
        :returns: UID from independently confirmed Kubernetes read-back.
        :raises KubernetesError: Creation, identity or independent read-back failed.
        :raises GitLabAPIError: The token has no available one-time credential.
        """  # noqa: DOC503 - The one-time accessor may reject consumed credentials.
        document = {
            "apiVersion": "v1",
            "kind": _SECRET_KIND,
            "type": _SECRET_TYPE,
            "immutable": True,
            "metadata": self._metadata,
            "stringData": {TOKEN_KEY: credential.take_secret()},
        }
        # Native projection failures can include the source Secret.
        with execution_trace(None):
            result = self._service.command(
                "create",
                "-f",
                "-",
                "-o",
                _OUTPUT,
                input_data=json.dumps(document).encode("utf-8"),
            )
        if result.returncode:
            raise KubernetesError("agent_secret_write_unconfirmed")
        uid = self._uid(result.stdout)
        if self.read() != uid:
            raise KubernetesError("agent_secret_readback_mismatch")
        return uid
