"""Register a fresh Agent and durably record each native ownership transition."""

from __future__ import annotations

from collections.abc import Mapping

from lib.python.core.cli_api import APIError, RequestDeadline
from lib.python.core.gitlab.cluster_agent.compensation import compensate_registration
from lib.python.core.gitlab.cluster_agent.provider import AgentProvider
from lib.python.core.gitlab.cluster_agent.recovery import (
    CREDENTIAL,
    CREDENTIAL_ID,
    CREDENTIAL_NAME,
    CREDENTIAL_RESOURCE,
    CREDENTIAL_RESOURCE_NAME,
    CREDENTIAL_RESOURCE_UID,
    PREEXISTING_CREDENTIAL_IDS,
    AgentRecoveryStore,
    RecoveryRecord,
)
from lib.python.core.gitlab.cluster_agent.secret import AgentSecretStore
from lib.python.core.gitlab.cluster_agent.selection import ConnectSelection
from lib.python.core.gitlab.gitlab_api import GitLabAPIError
from lib.python.core.gitlab.gitlab_session import GitLabService
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    copy_json_object,
    json_object,
    json_objects,
    json_string,
)
from lib.python.core.kubernetes.gitlab_agent import read_agent_cluster_scope
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService

_AGENT_ID = "agent_id"
_RECORD_UID = "ownership_record_uid"


class RegistrationPersistenceError(KubernetesError):
    """Preserve known native observations when their recovery write is unconfirmed."""

    def __init__(self, unconfirmed: JSONObject) -> None:
        """Keep the proposed update available for reconciliation only.

        :param unconfirmed: Nonsecret observations that were not durably confirmed.
        """
        self.unconfirmed = unconfirmed
        super().__init__("agent_registration_persistence_unconfirmed")


def observed_resource(
    kind: str,
    name: str,
    namespace: str,
    uid: str,
    *,
    created: bool,
    api_version: str = "v1",
) -> JSONObject:
    """Record a native object identity in the Agent recovery schema.

    :param kind: Observed Kubernetes kind.
    :param name: Exact object name.
    :param namespace: Object namespace, or empty for cluster scope.
    :param uid: Original immutable UID from native read-back.
    :param created: Whether this run created the object.
    :param api_version: Kubernetes API version from the observed resource.
    :returns: Nonsecret recovery observation using the verified native identity.
    """
    return {
        "api_version": api_version,
        "kind": kind,
        "name": name,
        "namespace": namespace,
        "uid": uid,
        "state": "PRESENT",
        "created": created,
    }


def _pending_input(
    pending: JSONObject,
    selection: ConnectSelection,
    secret_name: str,
) -> tuple[JSONObject, tuple[str, str], str]:
    try:
        cluster_uid = json_string(pending.get("cluster_uid"))
        namespace_uid = json_string(pending.get("namespace_uid"))
        token_name = json_string(pending.get(CREDENTIAL_NAME))
        created = json_object(pending.get("created"))
        resources = json_objects(pending.get("resources"))
    except TypeError:
        raise KubernetesError("agent_registration_pending_invalid") from None
    scope = selection.scope
    namespace = observed_resource(
        "Namespace", scope.namespace, "", namespace_uid, created=scope.owns_namespace
    )
    if (
        not cluster_uid
        or not namespace_uid
        or not token_name.strip()
        or pending.get(CREDENTIAL_RESOURCE_NAME) != secret_name
        or pending.get(PREEXISTING_CREDENTIAL_IDS) != []
        or created.get("namespace") is not scope.owns_namespace
        or (resources and resources != (namespace,))
    ):
        raise KubernetesError("agent_registration_pending_invalid")
    payload = copy_json_object(pending)
    payload["resources"] = [namespace]
    return payload, (cluster_uid, namespace_uid), token_name


def persist_observation(
    store: AgentRecoveryStore,
    previous: RecoveryRecord,
    updates: Mapping[str, JSONValue],
    creation: str,
    *resources: JSONObject,
) -> RecoveryRecord:
    """Persist a confirmed write, retaining its observed identity on failure.

    :param store: Recovery store bound to the selected run.
    :param previous: Latest independently observed recovery record.
    :param updates: Nonsecret native identity fields confirmed for this stage.
    :param creation: Ownership field made true by the confirmed native write.
    :param *resources: Newly confirmed resources to append to the record.
    :returns: Recovery record after guarded replacement and independent read-back.
    :raises RegistrationPersistenceError: Durable ownership update is unconfirmed.
    """
    result = copy_json_object(previous.document)
    result.update(updates)
    result[_RECORD_UID] = previous.uid
    result["created"] = {**json_object(result.get("created")), creation: True}
    if resources:
        result["resources"] = [*json_objects(result.get("resources")), *resources]
    try:
        return store.replace(previous, result)
    except (KubernetesError, OSError):
        raise RegistrationPersistenceError(result) from None


def register_fresh_agent(
    gitlab: GitLabService,
    kubernetes: KubernetesService,
    selection: ConnectSelection,
    pending: JSONObject,
    *,
    deadline: RequestDeadline | None = None,
) -> RecoveryRecord:
    """Confirm each native identity before dispatching the next registration write.

    The caller has confirmed the plan and chosen the already observed namespace.
    This operation never adopts an existing registration or retries a write.

    :param gitlab: Access-verified service for the selected configuration project.
    :param kubernetes: Credential-bound Kubernetes runtime for the chosen scope.
    :param selection: Confirmed project, run, namespace, release, and Agent name.
    :param pending: Schema-valid fresh ownership document with unknown native IDs.
    :param deadline: Lifecycle budget for compensation after journal creation.
    :returns: Latest independently confirmed PENDING record with credential UID.
    :raises GitLabAPIError: The selected project, Agent, or credential cannot be verified.
    :raises KubernetesError: Scope, preflight absence, or durable persistence fails.
    :raises APIError: A bound provider deadline or request fails.
    :raises OSError: Native I/O cannot complete.
    :raises ValueError: Selected native inputs fail validation.
    """
    if gitlab.target_id != selection.project.id:
        raise GitLabAPIError("agent_registration_project_mismatch")
    provider = AgentProvider(gitlab)
    if provider.project() != selection.project:
        raise GitLabAPIError("agent_registration_project_mismatch")

    secret = AgentSecretStore(kubernetes, selection)
    payload, (cluster_uid, namespace_uid), token_name = _pending_input(
        pending, selection, secret.name
    )
    store = AgentRecoveryStore(
        kubernetes, selection, gitlab.session.origin, cluster_uid, namespace_uid
    )
    if provider.find_agent(selection.agent_name) is not None:
        raise GitLabAPIError("agent_registration_already_present")
    if read_agent_cluster_scope(kubernetes, selection.scope.namespace) != (
        cluster_uid,
        namespace_uid,
    ):
        raise KubernetesError("agent_registration_scope_mismatch")
    if secret.read() is not None:
        raise KubernetesError("agent_registration_credential_already_present")
    record = store.create_pending(payload)

    try:
        return _register_from_journal(
            provider,
            kubernetes,
            selection,
            store,
            secret,
            record,
            (cluster_uid, namespace_uid),
            token_name,
        )
    except (APIError, KubernetesError, OSError, ValueError) as failure:
        if deadline is not None:
            proposed = (
                failure.unconfirmed
                if isinstance(failure, RegistrationPersistenceError)
                else None
            )
            try:
                compensate_registration(
                    gitlab,
                    kubernetes,
                    selection,
                    store,
                    journal_uid=record.uid,
                    unconfirmed=proposed,
                    deadline=deadline,
                )
            except (APIError, KubernetesError, OSError, TypeError, ValueError):
                raise KubernetesError(
                    "agent_registration_compensation_unverified"
                ) from None
        raise


def _register_from_journal(
    provider: AgentProvider,
    kubernetes: KubernetesService,
    selection: ConnectSelection,
    store: AgentRecoveryStore,
    secret: AgentSecretStore,
    record: RecoveryRecord,
    scope_uids: tuple[str, str],
    token_name: str,
) -> RecoveryRecord:
    agent = provider.create_agent(selection.agent_name)
    record = persist_observation(
        store,
        record,
        {_AGENT_ID: agent.id},
        "agent",
        observed_resource(
            "ConfigMap", store.name, selection.scope.namespace, record.uid, created=True
        ),
    )
    if provider.read_agent(agent.id) != agent or provider.list_tokens(agent.id):
        raise GitLabAPIError("agent_registration_readback_mismatch")

    credential = provider.create_token(agent.id, token_name)
    identity = credential.identity
    record = persist_observation(
        store, record, {CREDENTIAL_ID: identity.id}, CREDENTIAL
    )
    if provider.read_token(agent.id, identity.id) != identity or (
        provider.list_tokens(agent.id) != (identity,)
    ):
        raise GitLabAPIError("agent_registration_credential_readback_mismatch")
    if read_agent_cluster_scope(kubernetes, selection.scope.namespace) != scope_uids:
        raise KubernetesError("agent_registration_scope_mismatch")

    secret_uid = secret.create(credential)
    return persist_observation(
        store,
        record,
        {CREDENTIAL_RESOURCE_UID: secret_uid},
        CREDENTIAL_RESOURCE,
        observed_resource(
            "Secret",
            secret.name,
            selection.scope.namespace,
            secret_uid,
            created=True,
        ),
    )
