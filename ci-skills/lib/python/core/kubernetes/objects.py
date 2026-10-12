"""Validated field access for native Kubernetes JSON observations.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Optional

from lib.python.core.json_types import JSONObject, json_objects


@dataclass(frozen=True)
class KubernetesObject:
    """Read a Kubernetes object without allowing malformed fields into joins.

    :param document: JSON object already decoded by the bounded transport.

    .. attribute :: document
        :type: JSONObject

        JSON object already decoded by the bounded transport.
    """

    document: JSONObject

    def object(self, key: str) -> KubernetesObject:
        """Read an optional nested object.

        :param key: Native field name in this object.
        :returns: View of the nested object; an absent field produces an empty view.
        :raises TypeError: If a present field is not an object.
        """
        value = self.document.get(key, {})
        if not isinstance(value, Mapping):
            raise TypeError("invalid_item_response")
        return KubernetesObject(value)

    def objects(self, key: str) -> Sequence[KubernetesObject]:
        """Read an optional array of nested objects without requiring mutation.

        :param key: Native field name in this object.
        :returns: Object views in provider order, or an empty list when absent.
        :raises TypeError: If the field is not an array or contains non-object items.
        """
        try:
            values = json_objects(self.document.get(key, []))
        except TypeError as exc:
            raise TypeError("invalid_item_response") from exc
        return [KubernetesObject(item) for item in values]

    def text(self, key: str) -> Optional[str]:
        """Read an optional string without coercing provider data.

        :param key: Native string field name.
        :returns: The string value, or None when the field is absent or JSON null.
        :raises TypeError: If a present non-null value is not a string.
        """
        value = self.document.get(key)
        if value is not None and not isinstance(value, str):
            raise TypeError("invalid_item_response")
        return value

    def required_text(self, key: str) -> str:
        """Read a nonempty string used as a resource relationship key.

        :param key: Required native string field name.
        :returns: Nonempty provider string suitable for a resource key.
        :raises TypeError: If the field is absent, empty, or not a string.
        """
        value = self.text(key)
        if not value:
            raise TypeError("invalid_item_response")
        return value

    def replica_counts(self, kind: str) -> JSONObject:
        """Read desired and ready workload counts from their native fields.

        :param kind: Native workload kind, such as DaemonSet or Deployment.
        :returns: Mapping with ``desired`` and ``ready`` observations. DaemonSets
            use status scheduling counters; other replica controllers use
            spec.replicas and status.readyReplicas. An absent counter remains
            JSON null rather than being reported as an observed zero.
        :raises TypeError: If status, or a required replica-controller spec,
            is present but is not an object.
        """  # noqa: DOC502 - Nested object readers validate native field shapes.
        status = self.object("status").document
        if kind == "DaemonSet":
            return {
                "desired": status.get("desiredNumberScheduled"),
                "ready": status.get("numberReady"),
            }
        return {
            "desired": self.object("spec").document.get("replicas"),
            "ready": status.get("readyReplicas"),
        }

    @property
    def metadata(self) -> KubernetesObject:
        """Read resource metadata.

        :returns: Metadata object view, empty when the field is absent.
        :raises TypeError: If metadata is present and not an object.
        """  # noqa: DOC502 - Object field readers validate required shapes.
        return self.object("metadata")

    @property
    def name(self) -> str:
        """Read the resource name used by storage indexes.

        :returns: Nonempty metadata.name.
        :raises TypeError: If metadata or its name is missing or malformed.
        """  # noqa: DOC502 - Object field readers validate required shapes.
        return self.metadata.required_text("name")

    @property
    def namespace(self) -> str:
        """Read the namespace of a namespaced resource.

        :returns: Nonempty metadata.namespace.
        :raises TypeError: If called for a resource without a valid namespace.
        """  # noqa: DOC502 - Object field readers validate required shapes.
        return self.metadata.required_text("namespace")
