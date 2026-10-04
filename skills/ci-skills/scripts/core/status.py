"""Shared diagnostic status tokens and command exit semantics."""

PASS = "PASS"
BLOCKED = "BLOCKED"
PARTIAL = "PARTIAL"
DRY_RUN = "DRY_RUN"
PLANNED = "PLANNED"
UNKNOWN = "UNKNOWN"


def exit_code(status: str) -> int:
    """Success means a complete live pass or an explicitly labeled plan."""
    return 0 if status in (PASS, DRY_RUN, PLANNED) else 2


# Which access gate an invocation actually passed. A collector passes the base
# gate; only access_check.py runs the expanded bundle. Named here with the other
# cross-boundary tokens so a typo in one producer cannot go unnoticed.
PROFILE_BASE = "base_access_check"
PROFILE_FULL = "full_live_access_check"
PROFILE_DRY_RUN = "dry_run"
