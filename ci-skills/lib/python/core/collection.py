"""Read native JSON responses and apply shared record filters.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Optional

from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.runtime import error_class, run_command

LIST_TIMEOUT_SECONDS = 120


class CommandReadError(ValueError):
    """A native read failed before a valid JSON response was available.

    :param reason: Stable classification of the native failure or invalid JSON.
    """


def _read_json(
    command: Sequence[str],
    env: Optional[Mapping[str, Optional[str]]] = None,
    timeout: int = LIST_TIMEOUT_SECONDS,
) -> JSONValue:
    """Read one native command response through the shared JSON decoder.

    :param command: Executable and arguments passed without a shell.
    :param env: Child environment overrides; absent overrides inherit the current
        environment, and a null mapping value removes that variable.
    :param timeout: Maximum command runtime in seconds.
    :returns: Validated JSON value, including an empty object or JSON null exactly
        when the command returned that value. Callers validate their resource shape.
    :raises CommandReadError: If the command fails or stdout is malformed,
        nonfinite, or too deeply nested to decode safely.
    :raises OSError: If process creation fails for a reason other than a missing
        executable, such as insufficient execute permission.
    """  # noqa: DOC503 - Native process creation can propagate OSError.
    result = run_command(command, env=env, timeout=timeout)
    if result.returncode:
        raise CommandReadError(error_class(result))
    try:
        return response_decoder().decode(result.stdout)
    except (ValueError, RecursionError) as exc:
        raise CommandReadError("invalid_json") from exc


def _search(value: JSONObject, needle: Optional[str]) -> bool:
    """Apply an optional case-insensitive search over a report record.

    :param value: Record to serialize for text search.
    :param needle: Search text, or ``None``/empty text to accept every record.
    :returns: Whether the serialized record contains the supplied text.
    :raises TypeError: If supplied record values cannot be serialized as JSON.
    :raises ValueError: If a caller supplies a circular container.
    """  # noqa: DOC502 - json.dumps rejects cycles and unsupported values.
    return (
        not needle
        or needle.casefold() in json.dumps(value, ensure_ascii=False).casefold()
    )
