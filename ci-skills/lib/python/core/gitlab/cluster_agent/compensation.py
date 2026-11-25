"""Reconcile a failed Agent registration before any Helm installation starts."""

from __future__ import annotations

from lib.python.core.cli_api import RequestDeadline
from lib.python.core.gitlab.cluster_agent.provider import (
    AgentProvider,
    AgentRegistration,
    AgentToken,
)
from lib.python.core.gitlab.cluster_agent.recovery import (
    CREDENTIAL,
    CREDENTIAL_ID,
    CREDENTIAL_NAME,
    CREDENTIAL_RESOURCE,
    CREDENTIAL_RESOURCE_UID,
    AgentRecoveryStore,
)
from lib.python.core.gitlab.cluster_agent.secret import AgentSecretStore
from lib.python.core.gitlab.cluster_agent.selection import ConnectSelection
from lib.python.core.gitlab.gitlab_session import GitLabService
from lib.python.core.json_types import (
    JSONObject,
    json_integer,
    json_object,
    json_string,
)
from lib.python.core.kubernetes.agent_namespace import verify_agent_namespace_cleanup
from lib.python.core.kubernetes.gitlab_agent import read_agent_cluster_scope
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService
from lib.python.core.runtime import execution_trace


def _tracked_provider_id(
    document: JSONObject, created: JSONObject, field: str, owner: str
) -> int | None:
    value = document[field]
    if value is None and created[owner] is not True:
        return None
    if created[owner] is True:
        identifier = json_integer(value)
        if identifier > 0:
            return identifier
    raise KubernetesError("agent_compensation_untracked_identity")


def _registration_objects(
    provider: AgentProvider,
    selection: ConnectSelection,
    document: JSONObject,
    deadline: RequestDeadline,
) -> tuple[AgentRegistration | None, AgentToken | None]:
    created = json_object(document["created"])
    agent_id = _tracked_provider_id(document, created, "agent_id", "agent")
    token_id = _tracked_provider_id(document, created, CREDENTIAL_ID, CREDENTIAL)
    logical = deadline.call(provider.find_agent, selection.agent_name)
    if agent_id is None:
        if logical is not None or token_id is not None:
            raise KubernetesError("agent_compensation_untracked_identity")
        return None, None
    expected = AgentRegistration(agent_id, selection.agent_name, selection.project.id)
    native = deadline.call(provider.read_agent, agent_id)
    if logical != native or (native is not None and native != expected):
        raise KubernetesError("agent_compensation_registration_changed")
    token = None
    if token_id is not None:
        desired = AgentToken(token_id, json_string(document[CREDENTIAL_NAME]), agent_id)
        token = deadline.call(provider.read_token, agent_id, token_id)
        if token is not None and (token != desired or native is None):
            raise KubernetesError("agent_compensation_token_changed")
    if native is not None and deadline.call(provider.list_tokens, agent_id) != (
        (token,) if token is not None else ()
    ):
        raise KubernetesError("agent_compensation_untracked_identity")
    return native, token


def compensate_registration(
    gitlab: GitLabService,
    kubernetes: KubernetesService,
    selection: ConnectSelection,
    store: AgentRecoveryStore,
    *,
    journal_uid: str,
    unconfirmed: JSONObject | None = None,
    deadline: RequestDeadline,
) -> None:
    """Remove only run-owned registration resources proven by the fresh journal.

    Call only after this invocation created the given journal and registration
    failed before Helm began. Unknown writes must be reconciled from the journal
    and native reads before removal is authorized.

    :param gitlab: Selected project-bound provider service.
    :param kubernetes: Cluster used by the registration attempt.
    :param selection: Confirmed Agent names and scope.
    :param store: Recovery store from the same registration attempt.
    :param journal_uid: Exact UID returned by the successful journal creation.
    :param unconfirmed: Proposed update after a failed journal read-back, if known.
    :param deadline: Bounded time remaining for compensation.
    :raises KubernetesError: Any identity is untracked, replaced or unconfirmed.
    """  # noqa: DOC503 - Provider, deadline and object readers propagate errors.
    record = deadline.call(store.read)
    if record is None or record.uid != journal_uid or not journal_uid:
        raise KubernetesError("agent_compensation_journal_changed")
    document = record.document
    created = json_object(document["created"])
    if (
        document["state"] != "PENDING"
        or (unconfirmed is not None and document != unconfirmed)
        or created["namespace"] is not selection.scope.owns_namespace
        or created["release"] is True
        or document["release_revision"] is not None
        or document["release_storage_name"] is not None
        or document["release_storage_uid"] is not None
    ):
        raise KubernetesError("agent_compensation_journal_changed")
    if gitlab.target_id != selection.project.id:
        raise KubernetesError("agent_compensation_project_changed")
    provider = AgentProvider(gitlab)
    if deadline.call(provider.project) != selection.project:
        raise KubernetesError("agent_compensation_project_changed")

    agent, token = _registration_objects(provider, selection, document, deadline)
    secret = AgentSecretStore(kubernetes, selection)
    expected_secret_uid = document[CREDENTIAL_RESOURCE_UID]
    secret_uid = deadline.call(secret.read)
    if secret_uid is not None and (
        secret_uid != expected_secret_uid or created[CREDENTIAL_RESOURCE] is not True
    ):
        raise KubernetesError("agent_compensation_credential_changed")
    if deadline.call(store.read) != record:
        raise KubernetesError("agent_compensation_journal_changed")

    if token is not None:
        deadline.call(provider.delete_token, token.agent_id, token)
    if agent is not None:
        deadline.call(provider.delete_agent, agent)
    if deadline.call(provider.find_agent, selection.agent_name) is not None:
        raise KubernetesError("agent_compensation_registration_changed")

    scope = selection.scope
    cluster_uid = json_string(document["cluster_uid"])
    namespace_uid = json_string(document["namespace_uid"])
    if (
        deadline.call(read_agent_cluster_scope, kubernetes, scope.namespace)
        != (cluster_uid, namespace_uid)
        or deadline.call(store.read) != record
    ):
        raise KubernetesError("agent_compensation_journal_changed")
    if secret_uid is not None:
        with execution_trace(None):
            deadline.call(
                kubernetes.delete,
                "Secret",
                secret.name,
                scope.namespace,
                expected_uid=secret_uid,
            )
    if deadline.call(secret.read) is not None:
        raise KubernetesError("agent_compensation_credential_changed")

    if scope.owns_namespace:
        verify_agent_namespace_cleanup(
            kubernetes,
            scope.namespace,
            namespace_uid,
            scope.run_id,
            frozenset((record.uid,)),
            timeout=deadline.remaining(),
        )
        deadline.call(
            kubernetes.delete,
            "Namespace",
            scope.namespace,
            "",
            expected_uid=namespace_uid,
        )
    else:
        deadline.call(
            kubernetes.delete,
            "ConfigMap",
            store.name,
            scope.namespace,
            expected_uid=record.uid,
        )
        if deadline.call(read_agent_cluster_scope, kubernetes, scope.namespace) != (
            cluster_uid,
            namespace_uid,
        ):
            raise KubernetesError("agent_compensation_namespace_changed")
