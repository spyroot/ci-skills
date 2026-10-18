"""Bounded documents, entries and errors for proposition discovery.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from collections.abc import MutableMapping, Sequence
from typing import Final

from lib.python.core.json_types import JSONObject, JSONValue, json_objects
from lib.python.core.report import render_value
from lib.python.core.runtime import sanitize

SCHEMA_VERSION: Final = "3.0"
MAX_BYTES: Final = 4096
MAX_ENTRIES: Final = 12
MAX_TEXT_BYTES: Final = 8192
MAX_QUERY: Final = 100


class NavigationError(ValueError):
    """Carry the reference schema's error reason and the rejected input."""

    def __init__(self, reason: str, value: str) -> None:
        """Retain a machine-readable lookup failure.

        :param reason: Error reason from the reference schema.
        :param value: Rejected node, reference, anchor, page or query.
        """
        super().__init__(reason)
        self.reason = reason
        self.value = value


def entry(name: str, summary: str, pointer: str) -> JSONObject:
    """Offer one thing the agent can read, with the call that reads it.

    :param name: Display name of the reference or section.
    :param summary: Catalogue-owned or heading-derived short description.
    :param pointer: The next ``reference.py`` call, without output-mode flags.
    :returns: One read entry of a proposition node.
    """
    return {"name": name, "summary": summary, "pointer": pointer}


def node(
    tools: Sequence[str], entries: Sequence[JSONObject]
) -> MutableMapping[str, JSONValue]:
    """Create one answer: what the agent can run and what it can read.

    :param tools: Installed command filenames offered at this node.
    :param entries: Ordered read entries offered at this node.
    :returns: Versioned node without a continuation.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "tools": {script: {} for script in tools},
        "reference": list(entries),
    }


def serialized_size(value: JSONValue) -> int:
    """Count compact UTF-8 JSON bytes, including the final newline.

    :param value: JSON value in the command's machine output format.
    :returns: Serialized byte count used for response budgets.
    """
    return len(render_value(value, "json", compact=True, ordered=True).encode())


def _error_input(value: str, budget: int) -> str:
    """Fit a redacted diagnostic echo into its remaining serialized byte budget.

    :param value: Rejected caller input, unrestricted by this formatter.
    :param budget: Bytes available for the serialized string, including its newline.
    :returns: Complete redacted input or a prefix with an explicit ellipsis.
    """
    value = sanitize(value, len(value)) or "<empty>"
    if serialized_size(value) <= budget:
        return value
    low, high = 0, min(len(value), budget)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = value[:middle] + ("..." if middle < len(value) else "")
        if serialized_size(candidate) <= budget:
            low = middle
        else:
            high = middle - 1
    return value[:low] + ("..." if low < len(value) else "")


def error_document(error: NavigationError) -> JSONObject:
    """Return a bounded empty node that carries the lookup failure.

    :param error: Original failure reason and rejected caller value.
    :returns: Schema-shaped error answer with bounded input.
    """
    base = {**node((), ()), "error": {"reason": error.reason, "input": "..."}}
    budget = MAX_BYTES - serialized_size(base) + serialized_size("...")
    return {
        **node((), ()),
        "error": {"reason": error.reason, "input": _error_input(error.value, budget)},
    }


def paginate(data: JSONObject, page: int, call: str) -> JSONObject:
    """Page read entries by bytes and count without dropping any of them.

    :param data: Complete answer whose ``reference`` entries are not yet paged.
    :param page: One-based page number.
    :param call: The exact call that produced ``data``, for the continuation.
    :returns: One bounded page whose continuation names the next call, or null.
    :raises NavigationError: If the page does not exist or one entry exceeds the budget.
    """
    entries = json_objects(data["reference"])
    pages: list[JSONObject] = []
    offset = 0
    while offset < len(entries) or not pages:
        selected: list[JSONObject] = []
        answer: JSONObject = {**data, "reference": [], "continuation": None}
        while offset + len(selected) < len(entries) and len(selected) < MAX_ENTRIES:
            item = entries[offset + len(selected)]
            remaining = offset + len(selected) + 1 < len(entries)
            candidate: JSONObject = {
                **data,
                "reference": [*selected, item],
                "continuation": f"{call} --page {len(pages) + 2}"
                if remaining
                else None,
            }
            if serialized_size(candidate) > MAX_BYTES:
                break
            selected.append(item)
            answer = candidate
        if (not selected and entries) or serialized_size(answer) > MAX_BYTES:
            raise NavigationError(
                "page_out_of_range", "one entry exceeds the response budget"
            )
        pages.append(answer)
        offset += len(selected)
    if page < 1 or page > len(pages):
        raise NavigationError("page_out_of_range", str(page))
    return pages[page - 1]
