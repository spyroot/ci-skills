#!/usr/bin/env python3
"""Keep this GitHub-only project's required validation route singular."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import yaml
from validation_cli import add_options, emit


REMOVED_ENTRYPOINTS = {
    "scripts/check.sh",
    "lib/ci/check.bash",
    "tests/check.bats",
}
APPROVED_WORKFLOW = ".github/workflows/validate.yml"


def check(root: Path) -> list[str]:
    """Inspect tracked route paths; root arg, no env, output, side effects or cleanup.

    Returns named failures, raises for unreadable inputs; repeated reads agree.
    """
    found = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "-z"],
        capture_output=True,
        check=True,
    )
    tracked = {path.decode("utf-8") for path in found.stdout.split(b"\0") if path}
    problems = [
        f"unapproved_entrypoint:{path}"
        for path in sorted(REMOVED_ENTRYPOINTS)
        if path in tracked or (root / path).exists()
    ]
    workflows = {
        path
        for path in tracked
        if path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))
    }
    workflows.update(
        str(path.relative_to(root))
        for pattern in ("*.yml", "*.yaml")
        for path in (root / ".github" / "workflows").glob(pattern)
    )
    if workflows != {APPROVED_WORKFLOW}:
        problems.append("workflow_route_unapproved")
    workflow = yaml.safe_load((root / APPROVED_WORKFLOW).read_text(encoding="utf-8"))
    if not isinstance(workflow, dict):
        return sorted(problems + ["workflow_invalid"])
    triggers = workflow.get("on", workflow.get(True, {}))
    push = triggers.get("push") if isinstance(triggers, dict) else None
    if (
        not isinstance(triggers, dict)
        or "pull_request" not in triggers
        or not isinstance(push, dict)
        or "main" not in push.get("branches", [])
    ):
        problems.append("required_trigger_missing")
    if workflow.get("name") != "validate" or set(workflow.get("jobs", {})) != {
        "validate"
    }:
        problems.append("required_check_unapproved")
    job = workflow.get("jobs", {}).get("validate", {})
    if job.get("continue-on-error"):
        problems.append("required_result_may_be_bypassed")
    if job.get("if"):
        problems.append("required_job_may_be_skipped")
    if any(step.get("continue-on-error") for step in job.get("steps", [])):
        problems.append("step_may_bypass_required_result")
    if any(step.get("if") for step in job.get("steps", [])):
        problems.append("required_step_may_be_skipped")
    return sorted(problems)


def main() -> int:
    """Print status JSON; argv input, stdout result, no mutation or cleanup."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    add_options(parser)
    args = parser.parse_args()
    try:
        problems = check(args.root.resolve())
    except (OSError, ValueError, TypeError, subprocess.CalledProcessError) as exc:
        problems = [f"delivery_policy_unreadable:{type(exc).__name__}"]
    emit(args, {"status": "PASS" if not problems else "BLOCKED", "problems": problems})
    return 0 if not problems else 2


if __name__ == "__main__":
    raise SystemExit(main())
