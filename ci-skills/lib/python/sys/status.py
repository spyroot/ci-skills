"""Shared diagnostic status tokens and command exit semantics.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

PASS = "PASS"
BLOCKED = "BLOCKED"
PARTIAL = "PARTIAL"
DRY_RUN = "DRY_RUN"
PLANNED = "PLANNED"
UNKNOWN = "UNKNOWN"
APPLIED = "APPLIED"
NO_OP = "NO_OP"
VERIFIED = "VERIFIED"


def exit_code(status: str) -> int:
    """Map a diagnostic status to its process exit code.

    :param status: Shared status token reported by a command.
    :returns: Zero for ``PASS``, ``DRY_RUN``, or ``PLANNED``; two otherwise.
    """
    return 0 if status in (PASS, DRY_RUN, PLANNED) else 2


# Which access gate an invocation actually passed. A collector passes the base
# gate; only access_check.py runs the expanded bundle. Named here with the other
# cross-boundary tokens so a typo in one producer cannot go unnoticed.
PROFILE_BASE = "base_access_check"
PROFILE_FULL = "full_live_access_check"
PROFILE_DRY_RUN = "dry_run"
