"""Read and validate Kubernetes resource lists.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from lib.python.access.kubernetes import kubectl_argv, kubernetes_env
from lib.python.core.collection import CommandReadError, _read_json
from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.target import Target


def _items(value: JSONValue) -> Sequence[JSONObject]:
    """Validate a Kubernetes List envelope before consuming its items.

    :param value: Decoded API response expected to contain an ``items`` list.
    :returns: Item objects with named metadata and object-shaped spec/status.
    :raises TypeError: The envelope or any item's required shape is malformed.
    """
    items = value.get("items") if isinstance(value, Mapping) else None
    if not isinstance(items, list):
        raise TypeError("invalid_list_response")
    records: MutableSequence[JSONObject] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise TypeError("invalid_list_response")
        metadata = item.get("metadata")
        if (
            not isinstance(metadata, Mapping)
            or not isinstance(metadata.get("name"), str)
            or not metadata["name"]
        ):
            raise TypeError("invalid_list_response")
        if any(
            key in item and not isinstance(item[key], Mapping)
            for key in ("spec", "status")
        ):
            raise TypeError("invalid_item_response")
        records.append(item)
    return records


def _meta(item: JSONObject) -> JSONObject:
    """Read a Kubernetes item's metadata or an empty mapping.

    :param item: Decoded resource object.
    :returns: Item metadata when present, otherwise an empty mapping.
    """
    metadata = item.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _batch(
    target: Target, resources: Mapping[str, tuple[str, bool]]
) -> tuple[Mapping[str, Sequence[JSONObject]], MutableSequence[Mapping[str, str]]]:
    """Read several Kubernetes resource lists concurrently on one target.

    :param target: Bound Kubernetes target used for every native command.
    :param resources: Report key mapped to resource name and all-namespaces flag.
    :returns: Validated item lists keyed by report name and per-resource error
        records; a failed list contributes an empty list.
    :raises TargetError: Bound kubeconfig changed while building queries.
    :raises ValueError: If the target has no Kubernetes authority.
    :raises OSError: If a native resource process cannot be started.
    """  # noqa: DOC502 - Target/query helpers and worker reads propagate failures.
    data: MutableMapping[str, Sequence[JSONObject]] = {}
    errors: MutableSequence[Mapping[str, str]] = []
    if not resources:
        return data, errors

    with ThreadPoolExecutor(max_workers=min(10, len(resources))) as pool:
        futures = {
            pool.submit(
                _read_json,
                kubectl_argv(
                    target,
                    "get",
                    resource,
                    *(["-A"] if namespaced else []),
                    "-o",
                    "json",
                ),
                env=kubernetes_env(target),
            ): key
            for key, (resource, namespaced) in resources.items()
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                data[key] = _items(future.result())
            except (CommandReadError, TypeError) as exc:
                data[key] = []
                errors.append({"source": key, "reason": str(exc)})

    return data, errors
