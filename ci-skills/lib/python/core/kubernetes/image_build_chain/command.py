"""Command boundary for the CI13 image-build chain.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
from collections.abc import MutableMapping, MutableSequence

from lib.python.core.gitlab.gitlab_actions import ActionError
from lib.python.core.gitlab.gitlab_api import GitLabAPIError
from lib.python.core.json_types import JSONObject, JSONValue, copy_json, is_json_value
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.podman_probe import (
    PROBE_KIND,
    ProbeMode,
    resolve_probe_mode,
)
from lib.python.core.report import emit_result, plan_digest, report
from lib.python.core.runtime import sanitize
from lib.python.core.target import TargetError
from lib.python.core.toolbox import ToolboxError
from lib.python.sys.status import BLOCKED, DRY_RUN, PASS, PLANNED

from .model import CHAIN_OPERATION, ImageBuildChainAction
from .workflow import workflow


class ImageBuildChain:
    """Prepare, execute, and emit one linked image-build chain result."""

    def __init__(self, args: argparse.Namespace):
        """Retain parsed inputs and create the one chain action.

        :param args: Shared and chain-specific command arguments.
        """
        self.args = args
        self.action = ImageBuildChainAction(args)

    def _output(self) -> MutableMapping[str, JSONValue]:
        """Create the existing report envelope for the selected target.

        :returns: Mutable chain result with target and operation identity.
        """
        target = getattr(self.action, "target", None)
        kubernetes = target.kubernetes if target is not None else None
        selected = (
            f"{kubernetes.context}@{kubernetes.server}" if kubernetes else "kubernetes"
        )
        output = report(PROBE_KIND, selected, {}, [], [])
        output["operation"] = CHAIN_OPERATION
        if target is not None:
            output["tested_revision"] = target.tested_revision
            output["target_file"] = str(target.source_file or "")
            output["target_source"] = target.source_kind or ""
            if target.skill is not None:
                output["skill"] = copy_json(target.skill)
        return output

    def _records(self) -> list[JSONObject]:
        """Render finite callback results from the one shared state.

        :returns: Callback-named result mappings in workflow order.
        :raises TypeError: A callback result falls outside the JSON model.
        """
        records: list[JSONObject] = []
        for callback, value in self.action.state.results.items():
            if not value:
                continue
            if not is_json_value(value):
                raise TypeError("workflow_record_requires_json")
            records.append({"callback": type(callback).__name__, **dict(value)})
        return records

    def execute(self) -> int:
        """Run the selected chain mode and emit one stable linked result.

        :returns: Canonical exit status derived from the final report.
        :raises OSError: Requested output or receipt writing fails.
        """  # noqa: DOC502 - shared result emission propagates output failures.
        errors: MutableSequence[JSONObject] = []
        output = self._output()
        try:
            mode = resolve_probe_mode(self.args)
            self.action.prepare(mode)
            output = self._output()
            if mode == ProbeMode.DRY_RUN:
                plan = self.action.plan(self.args.node or "auto", self.action.pipeline)
                output.update(
                    status=DRY_RUN,
                    plan=plan,
                    plan_digest=plan_digest(plan),
                    confirmable=False,
                    access={},
                )
            else:
                workflow(self.action).run()
                state = self.action.state
                successful = mode == ProbeMode.LIVE_PLAN or (
                    hasattr(state, "job_readback")
                    and state.job_readback.get("verified") is True
                    and state.chain_cleanup.get("status") == PASS
                )
                output.update(
                    status=(PLANNED if mode == ProbeMode.LIVE_PLAN else PASS)
                    if successful
                    else BLOCKED,
                    plan=state.chain_plan,
                    plan_digest=state.chain_plan_digest,
                    confirmable=True,
                    selected_node=state.selected_node,
                    access=copy_json(state.access_evidence),
                )
                if mode == ProbeMode.APPLY and state.chain_cleanup:
                    output["cleanup"] = copy_json(state.chain_cleanup)
                if mode == ProbeMode.APPLY and not successful:
                    errors.append(
                        {
                            "source": CHAIN_OPERATION,
                            "reason": "chain_evidence_incomplete",
                        }
                    )
        except (
            ActionError,
            GitLabAPIError,
            KubernetesError,
            OSError,
            RuntimeError,
            TargetError,
            ToolboxError,
            TypeError,
            ValueError,
        ) as exc:
            output = self._output()
            output["status"] = BLOCKED
            errors.append(
                {"source": CHAIN_OPERATION, "reason": sanitize(str(exc), 240)}
            )
            state = self.action.state
            if hasattr(state, "chain_plan"):
                output["plan"] = state.chain_plan
                output["plan_digest"] = state.chain_plan_digest
                output["confirmable"] = True
            if hasattr(state, "selected_node"):
                output["selected_node"] = state.selected_node
            if state.chain_cleanup:
                output["cleanup"] = copy_json(state.chain_cleanup)
            output["access"] = copy_json(state.access_evidence)
        records = self._records()
        output["records"] = records
        output["errors"] = list(errors)
        output["summary"] = {
            "record_count": len(records),
            "error_count": len(errors),
        }
        return emit_result(output, self.args)
