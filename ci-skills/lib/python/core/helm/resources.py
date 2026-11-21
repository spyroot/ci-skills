"""Read exact native Kubernetes identities from rendered Helm manifests."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import yaml

from lib.python.core.binary_build import BuildConfigLoader
from lib.python.core.cli_api import MAX_RESPONSE_BYTES
from lib.python.core.helm.native import HelmError
from lib.python.core.kubernetes.run_scope import validate_namespace

_MAX_DOCUMENTS: Final = 256
_RESOURCE_NAME: Final = re.compile(r"[A-Za-z0-9_.:-]{1,253}")
_HOOK_ANNOTATION: Final = "helm.sh/hook"


@dataclass(frozen=True)
class RenderedResource:
    """Identify one object to reconcile before Helm creates it.

    .. attribute :: api_version
        :type: str

        Exact native API version, including the group if present.

    .. attribute :: kind
        :type: str

        Exact native kind with its original casing.

    .. attribute :: name
        :type: str

        Exact native object name.

    .. attribute :: namespace
        :type: str

        Exact selected namespace, or empty for cluster-scoped resources.
    """

    api_version: str
    kind: str
    name: str
    namespace: str


class _ManifestLoader(BuildConfigLoader):
    """Reject ambiguous mappings, YAML aliases and explicit null documents."""

    def compose_node(
        self, parent: yaml.nodes.Node | None, index: int | yaml.nodes.Node | None
    ) -> yaml.nodes.Node:
        """Reject aliases while composing native Helm YAML.

        :param parent: Optional surrounding YAML node.
        :param index: Mapping key node or sequence position.
        :returns: A composed node with no alias.
        :raises HelmError: The render contains an unsupported alias.
        """
        if self.check_event(yaml.events.AliasEvent):
            raise HelmError("helm_render_yaml_alias_unsupported")
        return super().compose_node(parent, index)

    def construct_document(self, node: yaml.nodes.Node) -> object:
        """Permit structural empty documents and require object content otherwise.

        :param node: Root of one composed document.
        :returns: Constructed mapping or None for an empty document.
        :raises HelmError: The document contains a scalar or list.
        """
        if (
            isinstance(node, yaml.nodes.ScalarNode)
            and node.tag == "tag:yaml.org,2002:null"
            and node.value == ""
        ):
            return None
        if not isinstance(node, yaml.nodes.MappingNode):
            raise HelmError("helm_render_document_invalid")
        return super().construct_document(node)

    def construct_mapping(
        self, node: yaml.nodes.MappingNode, deep: bool = False
    ) -> dict[str, object]:
        """Read a mapping with unique string keys.

        :param node: Composed YAML mapping.
        :param deep: Whether to construct child objects immediately.
        :returns: Constructed mapping with no duplicate keys.
        :raises HelmError: A key is unsupported or repeated.
        """
        seen: set[str] = set()
        for key_node, _ in node.value:
            if key_node.tag != "tag:yaml.org,2002:str":
                raise HelmError("helm_render_mapping_key_invalid")
            key = self.construct_object(key_node)
            if not isinstance(key, str) or key in seen:
                raise HelmError("helm_render_mapping_key_invalid")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _resource(
    document: Mapping[str, object],
    namespace: str,
    accepted: Mapping[tuple[str, str], bool],
) -> RenderedResource:
    api_version = document.get("apiVersion")
    kind = document.get("kind")
    if (
        not isinstance(api_version, str)
        or not api_version
        or not isinstance(kind, str)
        or not kind
        or (api_version, kind) not in accepted
    ):
        raise HelmError("helm_render_resource_type_unsupported")
    namespaced = accepted[(api_version, kind)]
    if type(namespaced) is not bool:
        raise HelmError("helm_render_resource_scope_invalid")
    metadata = document.get("metadata")
    if not isinstance(metadata, Mapping):
        raise HelmError("helm_render_metadata_invalid")
    if "generateName" in metadata:
        raise HelmError("helm_render_generate_name_unsupported")
    name = metadata.get("name")
    if (
        not isinstance(name, str)
        or not _RESOURCE_NAME.fullmatch(name)
        or name in {".", ".."}
    ):
        raise HelmError("helm_render_resource_name_invalid")
    annotations = metadata.get("annotations", {})
    if not isinstance(annotations, Mapping) or any(
        not isinstance(value, str) for value in annotations.values()
    ):
        raise HelmError("helm_render_annotations_invalid")
    if any(
        key == _HOOK_ANNOTATION or key.startswith(_HOOK_ANNOTATION + "-")
        for key in annotations
    ):
        raise HelmError("helm_render_hook_unsupported")
    rendered_namespace = metadata.get("namespace", "")
    expected_namespace = namespace if namespaced else ""
    if not isinstance(rendered_namespace, str) or rendered_namespace not in {
        "",
        expected_namespace,
    }:
        raise HelmError("helm_render_resource_namespace_invalid")
    return RenderedResource(api_version, kind, name, expected_namespace)


def rendered_resources(
    manifest: str,
    *,
    namespace: str,
    accepted: Mapping[tuple[str, str], bool],
) -> tuple[RenderedResource, ...]:
    """Read unique create identities in the order Helm rendered them.

    :param manifest: Exact native Helm multi-document YAML within its output cap.
    :param namespace: Selected namespace for namespaced objects with no namespace.
    :param accepted: Exact API version and kind pairs mapped to namespaced scope.
    :returns: Ordered, nonempty tuple of validated create identities.
    :raises HelmError: Invalid namespace, YAML, scope, resource identity or limits.
    """
    try:
        validate_namespace(namespace)
    except ValueError:
        raise HelmError("helm_render_namespace_invalid") from None
    try:
        if len(manifest.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise HelmError("helm_render_too_large")
    except UnicodeEncodeError:
        raise HelmError("helm_render_yaml_invalid") from None

    identities: list[RenderedResource] = []
    seen: set[tuple[str, str, str, str]] = set()
    try:
        for number, document in enumerate(
            yaml.load_all(manifest, Loader=_ManifestLoader), start=1
        ):
            if number > _MAX_DOCUMENTS:
                raise HelmError("helm_render_too_many_documents")
            if document is None:
                continue
            if not isinstance(document, Mapping):
                raise HelmError("helm_render_document_invalid")
            identity = _resource(document, namespace, accepted)
            # Multiple versions of one group's kind address the same object.
            group = identity.api_version.rpartition("/")[0]
            key = (group, identity.kind, identity.name, identity.namespace)
            if key in seen:
                raise HelmError("helm_render_duplicate_resource")
            seen.add(key)
            identities.append(identity)
    except (yaml.YAMLError, RecursionError, TypeError, ValueError, OverflowError):
        raise HelmError("helm_render_yaml_invalid") from None
    if not identities:
        raise HelmError("helm_render_empty")
    return tuple(identities)
