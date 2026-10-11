"""Apply and read back exact Kubernetes resources owned by an operation.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Final

from .kubernetes import KubernetesError, KubernetesService
from .status import APPLIED, NO_OP

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


def matches(expected: Any, observed: Any) -> bool:
    """Compare requested fields while allowing provider-defaulted object keys.

    :param expected: Exact requested values owned by this operation.
    :param observed: Native independent read-back.
    :returns: Whether every requested field equals its observed value.
    """
    if isinstance(expected, Mapping):
        # Native Kubernetes APIs may omit empty optional lists in JSON read-back.
        return isinstance(observed, Mapping) and all(
            matches(value, observed[key]) if key in observed else value == []
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(observed, list)
            and len(expected) == len(observed)
            and all(
                matches(left, right)
                for left, right in zip(expected, observed, strict=True)
            )
        )
    return expected == observed


def restorable(document: Mapping[str, Any]) -> dict[str, Any]:
    """Remove provider-owned state from one native object snapshot.

    :param document: Independent native read-back of an operation-owned object.
    :returns: Deep-copied desired state accepted by the native apply operation.
    :raises KubernetesError: If the object has no applicable identity metadata.
    """
    metadata = document.get("metadata")
    if not isinstance(metadata, Mapping) or not metadata.get("name"):
        raise KubernetesError("resource_snapshot_identity_missing")
    desired = {
        key: deepcopy(value)
        for key, value in document.items()
        if key != "status" and key != "metadata"
    }
    desired["metadata"] = {
        key: deepcopy(value)
        for key, value in metadata.items()
        if key in RESTORABLE_METADATA
    }
    annotations = desired["metadata"].get("annotations")
    if isinstance(annotations, dict):
        for key in PROVIDER_ANNOTATIONS:
            annotations.pop(key, None)
    return desired


@dataclass
class ResourceSet:
    """Track resources created by one atom for read-back and failure cleanup."""

    service: KubernetesService
    namespace: str
    operation: str
    created: list[tuple[str, str]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    _mutations: list[tuple[str, str, dict[str, Any] | None]] = field(
        default_factory=list, init=False, repr=False
    )
    _tracked: set[tuple[str, str]] = field(default_factory=set, init=False, repr=False)

    def managed(self, document: dict[str, Any]) -> dict[str, Any]:
        """Mark one generated object with its operation ownership.

        :param document: Generated Kubernetes object.
        :returns: The same object with shared ownership labels.
        """
        metadata = document.setdefault("metadata", {})
        if self.namespace:
            metadata.setdefault("namespace", self.namespace)
        metadata.setdefault("labels", {}).update(
            {MANAGER_LABEL: MANAGER_VALUE, OPERATION_LABEL: self.operation}
        )
        return document

    def owns(self, document: dict[str, Any]) -> bool:
        """Check the operation labels before changing an existing resource.

        :param document: Native resource read-back.
        :returns: Whether this named operation manages the resource.
        """
        labels = document.get("metadata", {}).get("labels", {})
        return (
            labels.get(MANAGER_LABEL) == MANAGER_VALUE
            and labels.get(OPERATION_LABEL) == self.operation
        )

    def apply(self, document: dict[str, Any]) -> dict[str, Any]:
        """Apply changed desired fields and prove their independent read-back.

        :param document: Fully selected native Kubernetes resource.
        :returns: Safe resource identity and applied/no-op status.
        :raises KubernetesError: If an unrelated resource conflicts or read-back differs.
        """
        desired = self.managed(document)
        kind, name = desired["kind"], desired["metadata"]["name"]
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
        if not matches(desired, observed):
            raise KubernetesError("resource_readback_mismatch")
        evidence = {
            "kind": kind,
            "name": name,
            "namespace": self.namespace,
            "uid": observed["metadata"]["uid"],
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
        """
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
        """
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
        failures: list[str] = []
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
