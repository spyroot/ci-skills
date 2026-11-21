"""Persist an Agent run's native recovery record with guarded writes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

from lib.python.core.cli_api import RequestDeadline, response_decoder
from lib.python.core.gitlab.cluster_agent.authority import AgentAuthorities
from lib.python.core.gitlab.cluster_agent.selection import (
    ConnectSelection,
    release_name,
)
from lib.python.core.json_types import (
    JSONObject,
    copy_json_object,
    json_object,
    json_objects,
    json_string,
)
from lib.python.core.kubernetes.gitlab_agent import read_agent_cluster_scope
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.kubernetes.resources import (
    MANAGER_LABEL,
    MANAGER_VALUE,
    OPERATION_LABEL,
)
from lib.python.core.kubernetes.run_scope import RUN_ID_LABEL, RunScope, validate_run_id
from lib.python.core.runtime import execution_trace
from lib.python.core.schema import SpecificationError, validate_specification

_OWNERSHIP_SCHEMA: Final = "gitlab-agent-ownership.schema.json"
_DATA_KEY: Final = "ownership.json"
_KIND: Final = "ConfigMap"
_PENDING: Final = "PENDING"
_RECORD_UID: Final = "ownership_record_uid"

CREDENTIAL_ID: Final = "registration_credential_id"
CREDENTIAL_NAME: Final = "registration_credential_name"
PREEXISTING_CREDENTIAL_IDS: Final = "preexisting_registration_credential_ids"
CREDENTIAL_RESOURCE_NAME: Final = "credential_resource_name"
CREDENTIAL_RESOURCE_UID: Final = "credential_resource_uid"
CREDENTIAL: Final = "registration_credential"
CREDENTIAL_RESOURCE: Final = "credential_resource"
# Resource and token states written into ownership rows and result records.
PRESENT: Final = "PRESENT"
ABSENT: Final = "ABSENT"
# The token is known from the ownership record only; this invocation did not read it natively.
RECORDED: Final = "RECORDED"

_NATIVE_IDS: Final = (
    "agent_id",
    CREDENTIAL_ID,
    CREDENTIAL_RESOURCE_UID,
    "release_revision",
    "release_storage_name",
    "release_storage_uid",
)


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    """One schema-validated document and its native write preconditions.

    .. attribute :: uid
        :type: str

        Immutable Kubernetes ConfigMap UID observed from the API.

    .. attribute :: resource_version
        :type: str

        Opaque Kubernetes version used for the next conditional replacement.

    .. attribute :: document
        :type: JSONObject

        Native record payload, treated as the observed snapshot.
    """

    uid: str
    resource_version: str
    document: JSONObject


def agent_recovery_name(selection: ConnectSelection) -> str:
    """Derive the same exact resource name for creation and logical absence.

    :param selection: Verified consumer Agent name and run scope.
    :returns: Stable DNS-safe resource name including the run suffix.
    """
    return selection.scope.name("agent-recovery", prefix=selection.agent_name)


def _journal_document(raw: JSONObject) -> JSONObject:
    data = KubernetesObject(raw).object("data")
    if set(data.document) != {_DATA_KEY} or "binaryData" in raw:
        raise KubernetesError("agent_recovery_native_invalid")
    return json_object(response_decoder().decode(data.required_text(_DATA_KEY)))


def _validate_schema(document: JSONObject) -> None:
    try:
        validate_specification(document, _OWNERSHIP_SCHEMA)
    except SpecificationError:
        raise KubernetesError("agent_recovery_document_invalid") from None


class AgentRecoveryStore:
    """Manage the unique nonsecret ConfigMap in the selected Agent scope."""

    def __init__(
        self,
        service: KubernetesService,
        selection: ConnectSelection,
        origin: str,
        cluster_uid: str,
        namespace_uid: str,
    ) -> None:
        """Bind the store to live, independently selected provider identities.

        :param service: Credential- and context-bound Kubernetes service.
        :param selection: Native GitLab project and Agent run details.
        :param origin: Canonical read-back origin of the selected GitLab project.
        :param cluster_uid: Native kube-system namespace UID.
        :param namespace_uid: Native UID of the selected namespace.
        """
        self._service = service
        self._selection = selection
        self._scope_uids = (cluster_uid, namespace_uid)
        scope = selection.scope
        self.name = agent_recovery_name(selection)
        self.labels = {
            **scope.labels,
            MANAGER_LABEL: MANAGER_VALUE,
            OPERATION_LABEL: scope.run_id,
        }
        self._identity = {
            "run_id": scope.run_id,
            "origin": origin.rstrip("/"),
            "project_id": selection.project.id,
            "project_path": selection.project.path,
            "agent_name": selection.agent_name,
            "cluster_uid": cluster_uid,
            "namespace": scope.namespace,
            "namespace_uid": namespace_uid,
            "ownership_record_name": self.name,
            "release": selection.release,
        }

    def _verify_scope(self) -> None:
        namespace = self._selection.scope.namespace
        if read_agent_cluster_scope(self._service, namespace) != self._scope_uids:
            raise KubernetesError("agent_recovery_scope_mismatch")

    @staticmethod
    def _initial(document: JSONObject) -> bool:
        created = json_object(document["created"])
        return (
            document["state"] == _PENDING
            and document[_RECORD_UID] is None
            and all(document[key] is None for key in _NATIVE_IDS)
            and created["ownership_record"] is True
            and not any(
                created[key] is True
                for key in ("agent", CREDENTIAL, CREDENTIAL_RESOURCE, "release")
            )
        )

    def _validate_document(self, document: JSONObject) -> None:
        _validate_schema(document)
        if any(document[key] != value for key, value in self._identity.items()):
            raise KubernetesError("agent_recovery_scope_mismatch")

    def _observe(self, raw: JSONObject) -> RecoveryRecord:
        try:
            obj = KubernetesObject(raw)
            metadata = obj.metadata
            labels = metadata.object("labels").document
            if (
                obj.text("apiVersion") != "v1"
                or obj.text("kind") != _KIND
                or metadata.text("name") != self.name
                or metadata.text("namespace") != self._selection.scope.namespace
                or metadata.text("deletionTimestamp") is not None
                or any(labels.get(key) != value for key, value in self.labels.items())
            ):
                raise KubernetesError("agent_recovery_native_invalid")
            uid = metadata.required_text("uid")
            version = metadata.required_text("resourceVersion")
            document = _journal_document(raw)
        except (TypeError, ValueError, RecursionError):
            raise KubernetesError("agent_recovery_native_invalid") from None
        self._validate_document(document)
        if document[_RECORD_UID] != uid and not self._initial(document):
            raise KubernetesError("agent_recovery_resource_uid_mismatch")
        return RecoveryRecord(uid, version, document)

    def read(self) -> RecoveryRecord | None:
        """Read the exact recovery ConfigMap and reject an inconsistent identity.

        :returns: The validated native record, or None for verified API absence.
        :raises KubernetesError: The response, scope, or record cannot be verified.
        """  # noqa: DOC502 - Native read and identity checks propagate errors.
        raw = self._service.read(_KIND, self.name, self._selection.scope.namespace)
        if raw is None:
            return None
        observed = self._observe(raw)
        self._verify_scope()
        return observed

    def create_pending(self, document: JSONObject) -> RecoveryRecord:
        """Create the first marker atomically before making any provider writes.

        :param document: Initial validated ownership document with unknown native IDs.
        :returns: Successful native response confirmed by independent read-back.
        :raises KubernetesError: The document, write, or independent read is invalid.
        """
        self._validate_document(document)
        if not self._initial(document):
            raise KubernetesError("agent_recovery_pending_invalid")
        return self._write(document, previous=None)

    def replace(self, previous: RecoveryRecord, document: JSONObject) -> RecoveryRecord:
        """Persist an update only while the exact observed UID and version match.

        :param previous: Previously verified native record and write preconditions.
        :param document: Next ownership record with the exact native ConfigMap UID.
        :returns: Updated version independently confirmed after the native write.
        :raises KubernetesError: Scope, retained identity, or native write is invalid.
        """
        self._validate_document(previous.document)
        self._validate_document(document)
        if (
            not previous.uid
            or not previous.resource_version
            or document[_RECORD_UID] != previous.uid
            or any(
                previous.document[key] is not None
                and previous.document[key] != document[key]
                for key in _NATIVE_IDS
            )
        ):
            raise KubernetesError("agent_recovery_resource_uid_mismatch")
        return self._write(document, previous=previous)

    def _write(
        self, document: JSONObject, *, previous: RecoveryRecord | None
    ) -> RecoveryRecord:
        self._verify_scope()
        namespace = self._selection.scope.namespace
        metadata = {"namespace": namespace, "name": self.name, "labels": self.labels}
        uri = f"/api/v1/namespaces/{namespace}/configmaps"
        verb = "create"
        if previous is not None:
            metadata.update(uid=previous.uid, resourceVersion=previous.resource_version)
            uri = f"{uri}/{self.name}"
            verb = "replace"
        body = {
            "apiVersion": "v1",
            "kind": _KIND,
            "metadata": metadata,
            "data": {
                _DATA_KEY: json.dumps(copy_json_object(document), allow_nan=False)
            },
        }
        result = self._service.command(
            verb, "--raw", uri, "-f", "-", input_data=json.dumps(body).encode("utf-8")
        )
        if result.returncode:
            raise KubernetesError("agent_recovery_write_unconfirmed")
        try:
            returned = self._observe(
                json_object(response_decoder().decode(result.stdout))
            )
        except (TypeError, ValueError, RecursionError):
            raise KubernetesError("agent_recovery_native_invalid") from None
        if previous is not None and (
            returned.uid != previous.uid
            or returned.resource_version == previous.resource_version
        ):
            raise KubernetesError("agent_recovery_readback_mismatch")
        if returned.document != document or self.read() != returned:
            raise KubernetesError("agent_recovery_readback_mismatch")
        return returned


def locate_agent_run(
    authorities: AgentAuthorities,
    run_id: str,
    *,
    agent_name: str | None,
    namespace: str | None,
    deadline: RequestDeadline,
) -> tuple[ConnectSelection, RecoveryRecord] | None:
    """Find one exact Agent journal and verify its original native scope.

    :param authorities: Independently verified GitLab project and cluster.
    :param run_id: Original run identifier returned by the Agent operation.
    :param agent_name: Optional exact name supplied by the caller.
    :param namespace: Optional exact namespace supplied by the caller.
    :param deadline: Absolute operation timeout shared with other native reads.
    :returns: Recorded selection and verified journal, or None after an empty list.
    :raises ValueError: The caller's run identifier is invalid.
    :raises KubernetesError: The journal is ambiguous, malformed, or replaced.
    """  # noqa: DOC503 - Native reads and shared deadline propagate their errors.
    run = validate_run_id(run_id)
    selector = ",".join(
        (
            f"{MANAGER_LABEL}={MANAGER_VALUE}",
            f"{OPERATION_LABEL}={run}",
            f"{RUN_ID_LABEL}={run}",
        )
    )
    kubernetes = authorities.kubernetes
    with execution_trace(None):
        listing = kubernetes.json(
            "get",
            "configmaps",
            "--all-namespaces",
            "--chunk-size=0",
            "-l",
            selector,
            "-o",
            "json",
            timeout=deadline.remaining(),
        )
    try:
        envelope = json_object(listing)
        if (
            envelope.get("apiVersion") != "v1"
            or envelope.get("kind") not in ("List", "ConfigMapList")
            or json_object(envelope.get("metadata", {})).get("continue")
        ):
            raise KubernetesError("agent_recovery_inventory_unverified")
        items = json_objects(envelope.get("items"))
        if len(items) > 1:
            raise KubernetesError("agent_recovery_inventory_ambiguous")
        if not items:
            return None
        original = items[0]
        document = _journal_document(original)
        _validate_schema(document)
        name = json_string(document["agent_name"])
        selected_namespace = json_string(document["namespace"])
        owns_namespace = json_object(document["created"])["namespace"]
        if type(owns_namespace) is not bool:
            raise KubernetesError("agent_recovery_scope_mismatch")
        scope = RunScope(run, selected_namespace, owns_namespace)
        selection = ConnectSelection(
            authorities.project, name, scope, release_name(scope, name)
        )
        namespace_uid = json_string(document["namespace_uid"])
    except (TypeError, ValueError, RecursionError):
        raise KubernetesError("agent_recovery_inventory_unverified") from None
    if (agent_name is not None and agent_name != name) or (
        namespace is not None and namespace != selected_namespace
    ):
        raise KubernetesError("agent_recovery_scope_mismatch")

    store = AgentRecoveryStore(
        kubernetes,
        selection,
        authorities.gitlab.session.origin,
        authorities.cluster_uid,
        namespace_uid,
    )
    planned = store._observe(original)
    with execution_trace(None):
        observed = deadline.call(store.read)
    if observed is None or observed != planned:
        raise KubernetesError("agent_recovery_readback_mismatch")
    return selection, observed
