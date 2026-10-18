"""Apply and read back exact Kubernetes resources owned by an operation.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping, MutableSequence, Sequence
from dataclasses import dataclass, field
from typing import Final, Optional

from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    copy_json,
    copy_json_object,
)
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.sys.status import APPLIED, NO_OP

MANAGER_LABEL: Final = "app.kubernetes.io/managed-by"
MANAGER_VALUE: Final = "ci-skills"
OPERATION_LABEL: Final = "ci-skills.spyroot.dev/operation"
PROVIDER_ANNOTATIONS: Final = frozenset(
    {
        "deployment.kubernetes.io/revision",
        "kubectl.kubernetes.io/last-applied-configuration",
    }
)
RESTORABLE_METADATA: Final = frozenset(
    {"annotations", "finalizers", "labels", "name", "namespace", "ownerReferences"}
)


POD_OMITTED_FALSE_PATHS: Final = frozenset(
    {
        ("spec", "containers", "[]", "volumeMounts", "[]", "readOnly"),
        ("spec", "volumes", "[]", "persistentVolumeClaim", "readOnly"),
    }
)


def _omitted_field_matches(
    value: JSONValue, path: tuple[str, ...], native_pod: bool
) -> bool:
    """Recognize only empty lists and the observed native Pod boolean defaults.

    :param value: Requested value absent from the provider's response.
    :param path: Field location, with sequence entries represented by ``[]``.
    :param native_pod: Whether the root request is a native v1 Pod.
    :returns: Whether omission preserves this field's requested native value.
    """
    return value == [] or (
        native_pod and value is False and path in POD_OMITTED_FALSE_PATHS
    )


def _matches(
    expected: JSONValue,
    observed: JSONValue,
    path: tuple[str, ...],
    native_pod: bool,
) -> bool:
    """Compare requested fields while retaining their native resource location.

    :param expected: Requested JSON value at the current field location.
    :param observed: Provider-returned JSON value at that location.
    :param path: Field location, with sequence entries represented by ``[]``.
    :param native_pod: Whether the root request is a native v1 Pod.
    :returns: Whether requested values and documented omissions match.
    """
    if isinstance(expected, Mapping):
        return isinstance(observed, Mapping) and all(
            _matches(value, observed[key], (*path, key), native_pod)
            if key in observed
            else _omitted_field_matches(value, (*path, key), native_pod)
            for key, value in expected.items()
        )
    if isinstance(expected, Sequence) and not isinstance(expected, str):
        return (
            isinstance(observed, Sequence)
            and not isinstance(observed, str)
            and len(expected) == len(observed)
            and all(
                _matches(left, right, (*path, "[]"), native_pod)
                for left, right in zip(expected, observed, strict=True)
            )
        )
    return expected == observed


def matches(expected: JSONValue, observed: JSONValue) -> bool:
    """Compare requested fields with native independent read-back.

    Native v1 Pod mount and PVC ``readOnly`` fields default to false when omitted.
    Other requested booleans remain mandatory; an explicit different value fails.

    :param expected: Exact requested values owned by this operation.
    :param observed: Native independent read-back.
    :returns: Whether requested fields match, including recognized native defaults.
    """
    native_pod = (
        isinstance(expected, Mapping)
        and expected.get("apiVersion") == "v1"
        and expected.get("kind") == "Pod"
    )
    return _matches(expected, observed, (), native_pod)


def restorable(document: JSONObject) -> MutableMapping[str, JSONValue]:
    """Remove provider-owned state from one native object snapshot.

    :param document: Independent native read-back of an operation-owned object.
    :returns: Deep-copied desired state accepted by the native apply operation.
    :raises KubernetesError: If the object has no applicable identity metadata.
    :raises RecursionError: If native JSON containers contain a cycle or excessive nesting.
    """  # noqa: DOC503 - Recursive JSON copying propagates nesting errors.
    metadata = document.get("metadata")
    if not isinstance(metadata, Mapping) or not metadata.get("name"):
        raise KubernetesError("resource_snapshot_identity_missing")
    desired = {
        key: copy_json(value)
        for key, value in document.items()
        if key != "status" and key != "metadata"
    }
    metadata_copy: MutableMapping[str, JSONValue] = {
        key: copy_json(value)
        for key, value in metadata.items()
        if key in RESTORABLE_METADATA
    }
    desired["metadata"] = metadata_copy
    annotations = metadata_copy.get("annotations")
    if isinstance(annotations, MutableMapping):
        for key in PROVIDER_ANNOTATIONS:
            annotations.pop(key, None)
    return desired


@dataclass
class ResourceSet:
    """Track one operation's Kubernetes resources for read-back and rollback.

    :param service: Bound Kubernetes service used for apply and read operations.
    :param namespace: Namespace of this operation's resources.
    :param operation: Ownership label value assigned to generated resources.
    :param created: Resources created by this operation for failure cleanup.
    :param evidence: Applied or no-op read-back records accumulated by the tracker.
    :ivar _mutations: Prior resource snapshots retained for rollback.
    :ivar _tracked: Resource keys already recorded in the mutation journal.

    .. attribute :: service
        :type: KubernetesService

        Bound Kubernetes service used for apply and read operations.

    .. attribute :: namespace
        :type: str

        Namespace of this operation's resources.

    .. attribute :: operation
        :type: str

        Ownership label value assigned to generated resources.

    .. attribute :: created
        :type: MutableSequence[tuple[str, str]]

        Resources created by this operation for failure cleanup.

    .. attribute :: evidence
        :type: MutableSequence[JSONObject]

        Applied or no-op read-back records accumulated by the tracker.
    """

    service: KubernetesService
    namespace: str
    operation: str
    created: MutableSequence[tuple[str, str]] = field(default_factory=list)
    evidence: MutableSequence[JSONObject] = field(default_factory=list)
    _mutations: MutableSequence[tuple[str, str, Optional[JSONObject]]] = field(
        default_factory=list, init=False, repr=False
    )
    _tracked: set[tuple[str, str]] = field(default_factory=set, init=False, repr=False)

    def managed(
        self, document: MutableMapping[str, JSONValue]
    ) -> MutableMapping[str, JSONValue]:
        """Mark one generated object with its operation ownership.

        :param document: Generated Kubernetes object to label in place.
        :returns: The same object with shared ownership labels.
        :raises KubernetesError: If metadata or labels are present but are not objects.
        """
        metadata = document.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise KubernetesError("resource_metadata_invalid")
        labels = metadata.get("labels", {})
        if not isinstance(labels, Mapping):
            raise KubernetesError("resource_labels_invalid")
        owned_metadata = dict(metadata)
        if self.namespace:
            owned_metadata.setdefault("namespace", self.namespace)
        owned_metadata["labels"] = {
            **labels,
            MANAGER_LABEL: MANAGER_VALUE,
            OPERATION_LABEL: self.operation,
        }
        document["metadata"] = owned_metadata
        return document

    def owns(self, document: JSONObject) -> bool:
        """Check the operation labels before changing an existing resource.

        :param document: Native resource read-back.
        :returns: Whether this named operation manages the resource.
        :raises KubernetesError: If metadata or ownership labels are malformed.
        """
        try:
            labels = KubernetesObject(document).metadata.object("labels").document
        except TypeError as exc:
            raise KubernetesError("resource_labels_invalid") from exc
        return (
            labels.get(MANAGER_LABEL) == MANAGER_VALUE
            and labels.get(OPERATION_LABEL) == self.operation
        )

    def apply(self, document: JSONObject) -> JSONObject:
        """Apply changed desired fields and prove their independent read-back.

        :param document: Fully selected native Kubernetes resource, copied before
            ownership labels are added; the caller's mapping is not mutated.
        :returns: Safe resource identity and applied/no-op status.
        :raises KubernetesError: If identity or ownership fields are malformed, an
            unrelated resource conflicts, provider operations fail, or read-back differs.
        :raises TargetError: If the bound kubeconfig changes before a provider command.
        :raises OSError: If a provider process cannot start, its output cannot be read,
            or its private manifest cannot be created, permissioned, written, or removed.
        :raises TypeError: If desired containers contain bytes instead of JSON values.
        :raises RecursionError: If desired JSON containers contain a cycle or excessive nesting.
        """  # noqa: DOC503 - JSON copying, target binding, and provider I/O propagate errors.
        desired = self.managed(copy_json_object(document))
        try:
            selected = KubernetesObject(desired)
            kind, name = selected.required_text("kind"), selected.name
        except TypeError as exc:
            raise KubernetesError("resource_identity_invalid") from exc
        current = self.service.read(kind, name, self.namespace)
        if current is not None and not self.owns(current):
            raise KubernetesError("resource_not_owned_by_operation")
        changed = current is None or not matches(desired, current)
        if changed:
            key = (kind.casefold(), name)
            if key not in self._tracked:
                self._mutations.append(
                    (kind, name, None if current is None else restorable(current))
                )
                self._tracked.add(key)
            if current is None and (kind, name) not in self.created:
                # Track before dispatch so ambiguous native apply also gets read-back cleanup.
                self.created.append((kind, name))
            self.service.apply(desired)
        observed = self.service.read(kind, name, self.namespace)
        if observed is None or not matches(desired, observed):
            raise KubernetesError("resource_readback_mismatch")
        try:
            uid = KubernetesObject(observed).metadata.required_text("uid")
        except TypeError as exc:
            raise KubernetesError("resource_readback_identity_missing") from exc
        evidence = {
            "kind": kind,
            "name": name,
            "namespace": self.namespace,
            "uid": uid,
            "action": APPLIED if changed else NO_OP,
        }
        self.evidence.append(evidence)
        return evidence

    def delete(self, kind: str, name: str) -> bool:
        """Delete only the selected operation-owned object and verify absence.

        :param kind: Native resource kind.
        :param name: Exact resource name.
        :returns: Whether deletion was necessary.
        :raises KubernetesError: If ownership or provider deletion/read-back fails.
        :raises TargetError: If the bound kubeconfig changes before a provider command.
        :raises OSError: If a provider process cannot start or its output cannot be read
            during deletion or read-back.
        """  # noqa: DOC503 - Provider commands propagate target binding and I/O errors.
        current = self.service.read(kind, name, self.namespace)
        if current is None:
            return False
        if not self.owns(current):
            raise KubernetesError("resource_not_owned_by_operation")
        self.service.delete(kind, name, self.namespace)
        return True

    def cleanup_created(self) -> None:
        """Delete newly created objects in reverse order, preserving prior resources.

        :raises KubernetesError: If any owned resource cannot be removed.
        """  # noqa: DOC502 - cleanup_owned propagates unverified deletion failures.
        self.cleanup_owned(reversed(self.created))
        created = {(kind.casefold(), name) for kind, name in self.created}
        self._mutations = [
            item
            for item in self._mutations
            if (item[0].casefold(), item[1]) not in created
        ]
        self._tracked.difference_update(created)
        self.created.clear()

    def recover(self) -> None:
        """Restore prior objects and remove new objects in reverse mutation order.

        Every prior object is independently read back after native apply. Cleanup
        continues across failures so one broken object does not strand later ones.

        :raises KubernetesError: If any prior state cannot be restored or removed.
        """
        failures: MutableSequence[str] = []
        for kind, name, previous in reversed(self._mutations):
            try:
                if previous is None:
                    self.delete(kind, name)
                    continue
                current = self.service.read(kind, name, self.namespace)
                if current is not None and not self.owns(current):
                    raise KubernetesError("resource_not_owned_by_operation")
                self.service.apply(previous)
                observed = self.service.read(kind, name, self.namespace)
                if not matches(previous, observed):
                    raise KubernetesError("resource_recovery_readback_mismatch")
            except Exception as exc:  # noqa: BLE001 - attempt every recovery
                failures.append(f"{kind}/{name}:{exc}")
        if failures:
            raise KubernetesError("resource_recovery_incomplete:" + ",".join(failures))
        self.created.clear()
        self._mutations.clear()
        self._tracked.clear()

    def cleanup_owned(self, resources: Iterable[tuple[str, str]]) -> None:
        """Delete selected operation-owned resources while attempting every item.

        :param resources: Resource kind and exact name pairs in deletion order.
        :raises KubernetesError: If any owned resource cannot be removed.
        """
        failures = []
        for kind, name in resources:
            try:
                self.delete(kind, name)
            except Exception as exc:  # noqa: BLE001 - attempt every owned cleanup
                failures.append(str(exc))
        if failures:
            raise KubernetesError("resource_cleanup_incomplete:" + ",".join(failures))
