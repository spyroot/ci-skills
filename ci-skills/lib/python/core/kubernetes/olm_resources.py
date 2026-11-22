"""Apply-chain callbacks for the OLM operator command (CI16).

``ResolveShippedResource`` selects the resource document the package ships as
its example (or loads the caller's ``--spec``) and is the read-only resolve
step of the apply chain; the writers land with CI16-M3. ``alm_examples`` decodes
the ``alm-examples`` annotation of a CSV or PackageManifest (DEC-CI16-03).

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json

from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import K8SCallback
from lib.python.core.kubernetes.olm_models import (
    ALM_EXAMPLES_ANNOTATION,
    CLUSTERSERVICEVERSION_RESOURCE,
    OLMAction,
)


def alm_examples(provider_object: JSONObject) -> list[JSONObject]:
    """Decode the shipped example resources of one CSV or PackageManifest.

    :param provider_object: Object carrying the ``alm-examples`` annotation.
    :returns: Decoded example documents; empty when none are shipped.
    """
    annotations = provider_object.get("metadata", {}).get("annotations", {})
    raw = annotations.get(ALM_EXAMPLES_ANNOTATION)
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        decoded = json.loads(raw)
    except ValueError:
        return []
    return [item for item in decoded if isinstance(item, dict)]


class ResolveShippedResource(K8SCallback[OLMAction]):
    """Resolve the custom resource document for the apply step (CI16-M1 reads)."""

    def run(self) -> JSONObject:
        """Select the shipped example for ``--kind`` or load the ``--spec`` file.

        :returns: Resolution evidence with the selected document summary.
        :raises ValueError: Neither or both document selectors were passed.
        """
        args = self.action.args
        shipped = bool(getattr(args, "shipped_default", False))
        spec_path = getattr(args, "spec", None)
        if shipped == bool(spec_path):
            raise ValueError("operator_document_selection_invalid")
        if spec_path is not None:
            with open(spec_path, encoding="utf-8") as handle:
                document = json.load(handle)
        else:
            plan = self.state.plan
            if plan is None or not plan.installed_csv:
                raise ValueError("operator_csv_unavailable")
            csv = self.kubernetes.read(
                CLUSTERSERVICEVERSION_RESOURCE, plan.installed_csv, plan.namespace
            )
            examples = alm_examples(csv) if csv is not None else []
            document = next(
                (item for item in examples if item.get("kind") == args.resource_kind),
                None,
            )
            if document is None:
                raise ValueError(f"operator_shipped_example_missing: {args.resource_kind}")
        self.state.shipped_document = document
        metadata = document.get("metadata", {})
        return {
            "kind": document.get("kind"),
            "name": metadata.get("name"),
            "api_version": document.get("apiVersion"),
            "source": "spec" if spec_path is not None else "shipped_default",
        }
