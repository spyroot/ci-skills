"""Keep this GitHub-only project's required validation route singular."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Final

import yaml
from validation_cli import ValidationArgumentParser, add_options, emit

REMOVED_ENTRYPOINTS: Final[frozenset[str]] = frozenset(
    {
        "scripts/check.sh",
        "lib/ci/check.bash",
        "tests/check.bats",
    }
)
APPROVED_WORKFLOW: Final[str] = ".github/workflows/validate.yml"
ALWAYS: Final[str] = "${{ always() }}"
STANDARDS_CHECKOUT_IF: Final[str] = (
    "always() && steps.standards_pin.outcome == 'success' && "
    "steps.standards_access.outcome == 'success'"
)


# Summary: inspect the approved GitHub route; Arguments: repository root
# Environment inputs: Git index and workflow file; Stdout: none; Stderr: none
# Exit classes: problem list or unreadable input; Side effects: read-only
# Idempotency: same tree gives same result; Cleanup: Git child process exits
def check(root: Path) -> list[str]:
    """Check the one approved GitHub validation route and removed entrypoints.

    :param root: Checkout containing the workflow and tracked paths.
    :returns: Exact route and required-result policy failures.
    :raises OSError: The workflow cannot be read.
    :raises subprocess.CalledProcessError: Git cannot list tracked paths.
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
    steps = job.get("steps", [])
    if any(step.get("continue-on-error") for step in steps):
        problems.append("step_may_bypass_required_result")
    for index, step in enumerate(steps):
        condition = step.get("if")
        if index == 0 and step.get("uses") == "actions/checkout@v4":
            if condition:
                problems.append("initial_checkout_may_be_skipped")
            continue
        if step.get("with", {}).get("repository") == "spyroot/standards":
            if re.sub(r"\s+", " ", str(condition).strip()) != STANDARDS_CHECKOUT_IF:
                problems.append("standards_checkout_guard_invalid")
            continue
        if condition != ALWAYS:
            problems.append("required_step_may_be_skipped")
    return sorted(problems)


# Summary: report route status; Arguments: CLI options from argv
# Environment inputs: selected repository; Stdout: status JSON; Stderr: safe log
# Exit classes: 0 pass, 2 policy failure; Side effects: optional log append
# Idempotency: repeated checks agree; Cleanup: log handle closes
def main() -> int:
    """Publish delivery route status through shared validation output.

    :returns: Zero on pass or two for a route policy failure.
    """
    parser = ValidationArgumentParser(description=__doc__)
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
