"""Domain-semantic atomic ``Nvidia*`` callbacks for OpenShift (CI17-M1).

``WorkflowExecutor`` runs every callback's ``cleanup()`` hook after both
success and failure. These callbacks create no temporary invocation-owned
objects, so ``cleanup()`` performs no lifecycle teardown (DEC-CI17-21).

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from lib.python.core.action_modes import ActionMode
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import K8SCallback
from lib.python.core.kubernetes.nvidia_gpu.discovery import collect_health_snapshot
from lib.python.core.kubernetes.nvidia_gpu.models import GPUOperatorAction
from lib.python.core.kubernetes.nvidia_gpu.preflight import (
    apply_pause_plan,
    build_pause_plan,
    pause_plan_evidence,
    verify_pause_confirmation,
)
from lib.python.core.report import plan_digest


class NvidiaHealthCheck(K8SCallback[GPUOperatorAction]):
    """Read-only NVIDIA GPU Operator health check across OpenShift surfaces."""

    def run(self) -> JSONObject:
        """Collect cluster policy, DRA, MCO/MCP, workload, and node health evidence.

        :returns: Structured health summary with verified read-back flag.
        """
        snapshot, needs = collect_health_snapshot(
            self.kubernetes,
            namespace=self.action.namespace,
            node_filter=self.action.node,
        )
        self.state.health = snapshot
        if needs:
            self.state.needs.extend(needs)
        return {
            "allocation_mode": snapshot["allocation_mode"],
            "node_count": len(snapshot["nodes"]),
            "driver_pool_count": len(snapshot["nvidia_drivers"]),
            "daemonset_count": len(snapshot["daemonsets"]),
            "finding_count": len(snapshot["findings"]),
            "findings": list(snapshot["findings"]),
            "needs": [
                {"need": item.need, "evidence": item.evidence, "supply": item.supply}
                for item in needs
            ],
            "verified": True,
        }


class NvidiaPauseOperator(K8SCallback[GPUOperatorAction]):
    """Symmetric atomic pause and resume across node, policy, and OpenShift MCP."""

    def run(self) -> JSONObject:
        """Plan with server dry-run, or apply on matching confirmation digest.

        :returns: Plan evidence in ``LIVE_PLAN`` mode, or read-back evidence in
            ``APPLY`` mode.
        """
        plan, needs = build_pause_plan(self.action, self.kubernetes)
        if needs:
            self.state.needs.extend(needs)
            return {
                "action": self.action.operation,
                "scope": self.action.scope,
                "needs": [
                    {
                        "need": item.need,
                        "evidence": item.evidence,
                        "supply": item.supply,
                    }
                    for item in needs
                ],
                "verified": False,
            }
        assert plan is not None
        self.state.pause_plan = plan
        evidence = pause_plan_evidence(plan)
        digest = plan_digest(evidence)
        mode = getattr(self.action.args, "_resolved_mode", ActionMode.LIVE_PLAN)
        if mode != ActionMode.APPLY:
            return {
                "action": "NO_OP" if plan.no_op else "PLANNED",
                "plan": evidence,
                "plan_digest": digest,
                "server_dry_run_ok": plan.server_dry_run_ok,
                "verified": True,
            }
        verified_digest = verify_pause_confirmation(plan, self.action.args.confirm_plan)
        applied = apply_pause_plan(plan, self.kubernetes)
        self.state.mutated = bool(applied.get("mutated", False))
        return {
            **applied,
            "scope": plan.scope,
            "plan_digest": verified_digest,
        }
