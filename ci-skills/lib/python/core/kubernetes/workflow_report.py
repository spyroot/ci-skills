"""Shared report evidence for workflows consuming a prepared Kubernetes Pod.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
from typing import Optional

from lib.python.core.json_types import JSONObject, JSONValue, copy_json, json_object
from lib.python.core.kubernetes.tool_pod_models import ToolPodState
from lib.python.core.report import plan_digest, report
from lib.python.core.target import Target
from lib.python.sys.status import BLOCKED, PASS


def result_records(state: ToolPodState) -> Sequence[JSONObject]:
    """Select each callback's final run result once, excluding empty default hooks.

    :param state: Workflow state retaining per-callback results.
    :returns: Ordered JSON records with callback names for the execution trace.
    :raises TypeError: A callback's result contains a non-JSON value.
    """  # noqa: DOC502 - The common JSON conversion rejects unsupported values.
    records = [
        {"callback": type(callback).__name__, **json_object(copy_json(value))}
        for callback, value in state.results.items()
        if value
    ]
    records.extend(json_object(copy_json(value)) for value in state.cleanup_results)
    return records


def operation_evidence(state: ToolPodState) -> JSONObject:
    """Keep observed cleanup, retained identities and provider read-back together.

    :param state: Shared callback state, including partial observations after failure.
    :returns: Nonsecret evidence common to successful and failed command reports.
    :raises TypeError: Callback evidence contains a non-JSON value.
    """  # noqa: DOC502 - Shared JSON copying rejects unsupported values.
    retained = sorted(
        state.retained_resources,
        key=lambda item: (item.namespace, item.kind, item.name),
    )
    output: JSONObject = {
        "cleanup": {
            "status": BLOCKED if state.cleanup_failures else PASS,
            "failures": copy_json(state.cleanup_failures),
            "resources": copy_json(state.cleanup_resources),
            "retained": [item.as_dict() for item in retained],
        },
        "access": copy_json(state.access_evidence),
        "readback": copy_json(state.readback),
    }
    for field in ("pod", "run_id"):
        if field in state.readback:
            output[field] = copy_json(state.readback[field])
    return output


def workflow_result(
    kind: str,
    operation: str,
    state: ToolPodState,
    plan: JSONObject,
    status: str,
    errors: Sequence[JSONObject],
    target: Optional[Target] = None,
) -> MutableMapping[str, JSONValue]:
    """Build one Kubernetes workflow envelope from observed state and provenance.

    :param kind: Registered command result kind.
    :param operation: Caller-selected operation name.
    :param state: Shared callback state, including cleanup and retained resources.
    :param plan: Exact confirmation inputs, or empty before planning succeeded.
    :param status: Observed invocation status.
    :param errors: Sanitized failures, empty after successful execution.
    :param target: Resolved target when available; absent after early setup failure.
    :returns: One shared report with final callback records and operation evidence.
    :raises TypeError: Callback or provenance evidence contains non-JSON values.
    """  # noqa: DOC502 - shared JSON conversion rejects malformed evidence.
    selected = target.kubernetes if target is not None else None
    label = f"{selected.context}@{selected.server}" if selected else "kubernetes"
    output = report(kind, label, {}, result_records(state), errors)
    output.update(operation=operation, status=status, plan=plan)
    if target is not None:
        if plan:
            output["plan_digest"] = plan_digest(plan)
        output["tested_revision"] = target.tested_revision
        output["skill"] = copy_json(target.skill) if target.skill else {}
        output["target_source"] = target.source_kind or ""
        output["target_file"] = str(target.source_file) if target.source_file else ""
    output.update(operation_evidence(state))
    return output
