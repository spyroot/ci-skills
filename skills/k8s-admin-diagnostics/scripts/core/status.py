"""Shared diagnostic status tokens and command exit semantics."""

PASS = "PASS"
BLOCKED = "BLOCKED"
PARTIAL = "PARTIAL"
DRY_RUN = "DRY_RUN"
UNKNOWN = "UNKNOWN"


def exit_code(status: str) -> int:
    """Success means a complete live pass or an explicitly labeled dry run."""
    return 0 if status in (PASS, DRY_RUN) else 2
