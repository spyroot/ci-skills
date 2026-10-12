"""Parse shared relative windows and timezone-aware timestamps.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

DEFAULT_WINDOW = timedelta(hours=1)


RELATIVE_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


RELATIVE_PATTERN = re.compile(r"\A(\d+)([smhd])\Z")


def parse_window(value: str) -> timedelta:
    """Parse a positive relative event duration.

    :param value: Count plus one lower-case unit ``s``, ``m``, ``h``, or ``d``.
    :returns: Corresponding positive ``timedelta``.
    :raises ValueError: The expression is empty, malformed, or nonpositive.
    :raises OverflowError: If the requested duration exceeds timedelta limits.
    """  # noqa: DOC503 - Duration and datetime arithmetic can overflow.
    # Not lowercased: `1M` most likely means one month, which this does not
    # support, so it must be refused rather than read as one minute.
    match = RELATIVE_PATTERN.match(value.strip())
    if not match:
        raise ValueError(
            "--last must be a count and unit, one of s, m, h, d (for example 5m)"
        )
    amount = int(match.group(1))
    if amount <= 0:
        raise ValueError("--last must be a positive duration")
    return timedelta(**{RELATIVE_UNITS[match.group(2)]: amount})


def _timestamp(value: str) -> datetime:
    """Normalize one timezone-aware ISO timestamp to UTC.

    :param value: ISO timestamp, optionally using ``Z`` for UTC.
    :returns: Timezone-aware UTC datetime.
    :raises ValueError: The timestamp is invalid or lacks a timezone.
    """
    candidate = value.replace("Z", "+00:00")
    stamp = datetime.fromisoformat(candidate)
    if stamp.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return stamp.astimezone(UTC)
