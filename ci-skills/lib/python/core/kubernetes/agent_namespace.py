"""Prove an Agent-owned namespace contains no foreign object before cascade."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from typing import Final

from lib.python.core.cli_api import APIError, RequestDeadline, response_decoder
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService
from lib.python.core.kubernetes.run_scope import run_owned, validate_namespace
from lib.python.core.runtime import error_class, execution_trace

_RESOURCE_TYPE: Final = re.compile(r"[a-z0-9][a-z0-9.-]{0,252}")
_MAX_TYPES: Final = 512
_BATCH_SIZE: Final = 24
_REQUIRED_TYPES: Final = frozenset(
    {"configmaps", "events", "pods", "secrets", "serviceaccounts"}
)
_DEPRECATED_DEPLOYMENT_CONFIG: Final = re.compile(
    r"Warning: apps\.openshift\.io/v1 DeploymentConfig is deprecated in "
    r"v[0-9]+\.[0-9]+\+, unavailable in v[0-9]+\.[0-9]+\+"
)
_DEPLOYMENT_CONFIG_TYPE: Final = "deploymentconfigs.apps.openshift.io"
_FOREIGN_OBJECT: Final = "agent_namespace_foreign_object"
_KUBE_CONTROLLER: Final = "kube-controller-manager"
_SERVICE_CA_OPERATOR: Final = "service-ca-operator"
_OPENSHIFT_CONTROLLER: Final = "openshift-controller-manager"
_PULL_ACCOUNT_CONTROLLER: Final = (
    "openshift.io/image-registry-pull-secrets_service-account-controller"
)
_PULL_SECRET_CONTROLLER: Final = (
    "openshift.io/image-registry-pull-secrets_image-pull-secret-controller"
)
_RBAC_GROUP: Final = "rbac.authorization.k8s.io"
_RBAC_API: Final = f"{_RBAC_GROUP}/v1"
_OPENSHIFT_RBAC_API: Final = "authorization.openshift.io/v1"
_SYSTEM_BINDINGS: Final = {
    "system:deployers": ("system:deployer", "deployer"),
    "system:image-builders": ("system:image-builder", "builder"),
    "system:image-pullers": ("system:image-puller", None),
}


def _require_namespace(
    service: KubernetesService,
    namespace: str,
    expected_uid: str,
    run_id: str,
    deadline: RequestDeadline,
) -> None:
    document = service.read("Namespace", namespace, "", timeout=deadline.remaining())
    metadata = document.get("metadata") if document is not None else None
    if (
        document is None
        or document.get("apiVersion") != "v1"
        or document.get("kind") != "Namespace"
        or not isinstance(metadata, Mapping)
        or metadata.get("name") != namespace
        or metadata.get("uid") != expected_uid
        or metadata.get("deletionTimestamp") is not None
        or not run_owned(document, run_id)
    ):
        raise KubernetesError("agent_namespace_ownership_mismatch")


def _discovered_types(
    service: KubernetesService, deadline: RequestDeadline
) -> tuple[str, ...]:
    result = service.command(
        "api-resources",
        "--namespaced=true",
        "--verbs=list",
        "--cached=false",
        "-o",
        "name",
        timeout=deadline.remaining(),
    )
    if result.returncode:
        raise KubernetesError("agent_namespace_discovery_" + error_class(result))
    resources = tuple(result.stdout.splitlines())
    if (
        result.stderr
        or len(resources) > _MAX_TYPES
        or any(_RESOURCE_TYPE.fullmatch(item) is None for item in resources)
        or len(set(resources)) != len(resources)
        or not _REQUIRED_TYPES.issubset(resources)
    ):
        raise KubernetesError("agent_namespace_discovery_incomplete")
    return resources


def _read_batch(
    service: KubernetesService,
    namespace: str,
    resources: tuple[str, ...],
    deadline: RequestDeadline,
) -> tuple[JSONObject, ...]:
    result = service.command(
        "get",
        ",".join(resources),
        "--namespace",
        namespace,
        "--show-managed-fields",
        "-o",
        "json",
        timeout=deadline.remaining(),
    )
    if result.returncode:
        raise KubernetesError("agent_namespace_inventory_" + error_class(result))
    if any(
        _DEPLOYMENT_CONFIG_TYPE not in resources
        or _DEPRECATED_DEPLOYMENT_CONFIG.fullmatch(line) is None
        for line in result.stderr.splitlines()
    ):
        raise KubernetesError("agent_namespace_inventory_incomplete")
    try:
        document = response_decoder().decode(result.stdout)
    except (ValueError, RecursionError):
        raise KubernetesError("agent_namespace_inventory_invalid") from None
    if not isinstance(document, Mapping):
        raise KubernetesError("agent_namespace_inventory_invalid")
    metadata = document.get("metadata")
    items = document.get("items")
    if (
        document.get("apiVersion") != "v1"
        or document.get("kind") != "List"
        or not isinstance(metadata, Mapping)
        or metadata.get("continue", "") != ""
        or not isinstance(items, list)
        or any(not isinstance(item, Mapping) for item in items)
    ):
        raise KubernetesError("agent_namespace_inventory_invalid")
    return tuple(items)


def _object_metadata(item: JSONObject, namespace: str) -> JSONObject:
    metadata = item.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("namespace") != namespace
        or not isinstance(metadata.get("name"), str)
        or not metadata["name"]
        or not isinstance(item.get("apiVersion"), str)
        or not item["apiVersion"]
        or not isinstance(item.get("kind"), str)
        or not item["kind"]
    ):
        raise KubernetesError("agent_namespace_inventory_invalid")
    return metadata


def _uid_valid(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and all(char.isprintable() and not char.isspace() for char in value)
    )


def _managed_by(metadata: JSONObject, required: str, *additional: str) -> bool:
    fields = metadata.get("managedFields")
    if not isinstance(fields, list) or not fields:
        return False
    managers: set[str] = set()
    for field in fields:
        manager = field.get("manager") if isinstance(field, Mapping) else None
        if not isinstance(manager, str):
            return False
        managers.add(manager)
    return required in managers and managers.issubset((required, *additional))


def _bootstrap_account(metadata: JSONObject) -> bool:
    name = metadata["name"]
    if name == "default":
        return _managed_by(metadata, _KUBE_CONTROLLER) or _managed_by(
            metadata, _PULL_ACCOUNT_CONTROLLER, _KUBE_CONTROLLER
        )
    return name in ("builder", "deployer") and _managed_by(
        metadata, _PULL_ACCOUNT_CONTROLLER, _OPENSHIFT_CONTROLLER
    )


def _bootstrap_pull_secret(
    item: JSONObject,
    metadata: JSONObject,
    accounts: Mapping[str, JSONObject],
) -> bool:
    if item.get("type") != "kubernetes.io/dockercfg" or not _managed_by(
        metadata, _PULL_SECRET_CONTROLLER, _PULL_ACCOUNT_CONTROLLER
    ):
        return False
    owners = metadata.get("ownerReferences")
    if not isinstance(owners, list) or len(owners) != 1:
        return False
    owner = owners[0]
    if (
        not isinstance(owner, Mapping)
        or owner.get("apiVersion") != "v1"
        or owner.get("kind") != "ServiceAccount"
        or not isinstance(owner.get("uid"), str)
    ):
        return False
    account = accounts.get(owner["uid"])
    if account is None or not isinstance(account.get("metadata"), Mapping):
        return False
    account_name = account["metadata"].get("name")
    secret_name = metadata["name"]
    if (
        not isinstance(secret_name, str)
        or account_name != owner.get("name")
        or not secret_name.startswith(f"{account_name}-dockercfg-")
    ):
        return False
    return all(
        isinstance(references := account.get(field), list)
        and {"name": secret_name} in references
        for field in ("secrets", "imagePullSecrets")
    )


def _bootstrap_role_binding(
    item: JSONObject, metadata: JSONObject, namespace: str
) -> bool:
    name = metadata["name"]
    if (
        not isinstance(name, str)
        or name not in _SYSTEM_BINDINGS
        or not _managed_by(metadata, _OPENSHIFT_CONTROLLER)
    ):
        return False
    role, account = _SYSTEM_BINDINGS[name]
    if item.get("roleRef") != {
        "apiGroup": _RBAC_GROUP,
        "kind": "ClusterRole",
        "name": role,
    }:
        return False
    subject = (
        {"kind": "ServiceAccount", "name": account, "namespace": namespace}
        if account
        else {
            "apiGroup": _RBAC_GROUP,
            "kind": "Group",
            "name": f"system:serviceaccounts:{namespace}",
        }
    )
    return item.get("subjects") == [subject]


def _bootstrap_olm_copy(metadata: JSONObject, namespace: str) -> bool:
    labels = metadata.get("labels")
    if (
        not isinstance(labels, Mapping)
        or labels.get("olm.managed") != "true"
        or not _managed_by(metadata, "olm")
    ):
        return False
    source = labels.get("olm.copiedFrom")
    if not isinstance(source, str) or source == namespace:
        return False
    try:
        validate_namespace(source)
    except ValueError:
        return False
    return True


def _bootstrap_identity(
    item: JSONObject, metadata: JSONObject, uid: str
) -> tuple[str, str, str, str]:
    return (str(item["apiVersion"]), str(item["kind"]), str(metadata["name"]), uid)


def _bootstrap_objects(
    items: tuple[tuple[JSONObject, JSONObject], ...], namespace: str
) -> tuple[set[tuple[str, str, str, str]], dict[str, str]]:
    accepted: set[tuple[str, str, str, str]] = set()
    accounts: dict[str, JSONObject] = {}
    bindings: dict[str, str] = {}
    for item, metadata in items:
        uid, name = metadata.get("uid"), metadata["name"]
        if not isinstance(uid, str) or not _uid_valid(uid):
            continue
        resource = (item["apiVersion"], item["kind"])
        if resource == ("v1", "ServiceAccount") and _bootstrap_account(metadata):
            accounts[uid] = item
            accepted.add(_bootstrap_identity(item, metadata, uid))
        elif resource == ("v1", "ConfigMap") and (
            (name == "kube-root-ca.crt" and _managed_by(metadata, _KUBE_CONTROLLER))
            or (
                name == "openshift-service-ca.crt"
                and _managed_by(metadata, _SERVICE_CA_OPERATOR, _KUBE_CONTROLLER)
            )
        ):
            accepted.add(_bootstrap_identity(item, metadata, uid))
        elif resource == (_RBAC_API, "RoleBinding") and _bootstrap_role_binding(
            item, metadata, namespace
        ):
            bindings[uid] = str(name)
            accepted.add(_bootstrap_identity(item, metadata, uid))
        elif resource == (
            "operators.coreos.com/v1alpha1",
            "ClusterServiceVersion",
        ) and _bootstrap_olm_copy(metadata, namespace):
            accepted.add(_bootstrap_identity(item, metadata, uid))
    for item, metadata in items:
        uid = metadata.get("uid")
        if (
            isinstance(uid, str)
            and _uid_valid(uid)
            and item["apiVersion"] == "v1"
            and item["kind"] == "Secret"
            and _bootstrap_pull_secret(item, metadata, accounts)
        ):
            accepted.add(_bootstrap_identity(item, metadata, uid))
    return accepted, bindings


def _verify_items(
    documents: tuple[JSONObject, ...],
    namespace: str,
    expected_uid: str,
    permitted_uids: frozenset[str],
) -> None:
    items = tuple((item, _object_metadata(item, namespace)) for item in documents)
    bootstrap, bootstrap_bindings = _bootstrap_objects(items, namespace)
    related_uids = (
        permitted_uids | {identity[3] for identity in bootstrap} | {expected_uid}
    )
    for item, metadata in items:
        uid = metadata.get("uid")
        if not isinstance(uid, str) or not _uid_valid(uid):
            raise KubernetesError(_FOREIGN_OBJECT)
        if uid in permitted_uids:
            continue
        resource = (item["apiVersion"], item["kind"])
        if resource == (_OPENSHIFT_RBAC_API, "RoleBinding"):
            if bootstrap_bindings.get(uid) == metadata["name"]:
                continue
        elif _bootstrap_identity(item, metadata, uid) in bootstrap:
            continue
        if resource in (("v1", "Event"), ("events.k8s.io/v1", "Event")):
            ref = item.get("regarding" if resource[0] != "v1" else "involvedObject")
            target_uid = ref.get("uid") if isinstance(ref, Mapping) else None
            if isinstance(target_uid, str) and target_uid in related_uids:
                continue
        raise KubernetesError(_FOREIGN_OBJECT)


def verify_agent_namespace_cleanup(
    kubernetes: KubernetesService,
    namespace: str,
    expected_uid: str,
    run_id: str,
    permitted_uids: frozenset[str],
    *,
    timeout: float,
) -> None:
    """Verify an Agent namespace and inventory immediately before its deletion.

    :param kubernetes: Bound Kubernetes authority used for the native reads.
    :param namespace: Exact owned namespace selected by the cleanup plan.
    :param expected_uid: Namespace UID retained by the confirmed ownership record.
    :param run_id: Expected Kubernetes run ownership identity.
    :param permitted_uids: Exact durable object and descendant UIDs retained by the run.
    :param timeout: Total seconds allowed for native discovery and inventory.
    :raises KubernetesError: Missing authority, changed identity, incomplete proof,
        or any unowned object in the namespace.
    """
    if not _uid_valid(expected_uid) or not all(map(_uid_valid, permitted_uids)):
        raise KubernetesError("agent_namespace_expected_uid_invalid")
    if not math.isfinite(timeout) or timeout <= 0:
        raise KubernetesError("agent_namespace_inventory_timeout")
    deadline = RequestDeadline(time.monotonic() + timeout)
    try:
        validate_namespace(namespace)
        # Inventory can contain arbitrary native objects, including Secrets.
        with execution_trace(None):
            _require_namespace(kubernetes, namespace, expected_uid, run_id, deadline)
            resources = _discovered_types(kubernetes, deadline)
            documents = tuple(
                item
                for start in range(0, len(resources), _BATCH_SIZE)
                for item in _read_batch(
                    kubernetes,
                    namespace,
                    resources[start : start + _BATCH_SIZE],
                    deadline,
                )
            )
            _verify_items(documents, namespace, expected_uid, permitted_uids)
            _require_namespace(kubernetes, namespace, expected_uid, run_id, deadline)
        deadline.remaining()
    except APIError:
        raise KubernetesError("agent_namespace_inventory_timeout") from None
    except (ValueError, OSError):
        raise KubernetesError("agent_namespace_inventory_unavailable") from None
