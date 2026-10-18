"""Bounded records and pointers for progressive catalogue discovery.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from collections.abc import MutableMapping, Sequence
from typing import Final

from lib.python.core.json_types import JSONObject, JSONValue, json_object, json_objects
from lib.python.core.report import render_value

ENTRYPOINT: Final = "bin/reference.py"
MAX_BYTES: Final = 4096
MAX_CHOICES: Final = 12
MAX_TEXT_BYTES: Final = 8192
MAX_PATH: Final = 8
MAX_QUERY: Final = 100


class NavigationError(ValueError):
    """Carry the existing reference schema's error reason and rejected input."""

    def __init__(self, reason: str, value: str) -> None:
        """Retain a machine-readable lookup failure.

        :param reason: Error reason from the reference schema.
        :param value: Rejected path segment, reference, anchor or page.
        """
        super().__init__(reason)
        self.reason = reason
        self.value = value


def pointer(action: str, args: Sequence[str]) -> JSONObject:
    """Build an offline navigator invocation.

    :param action: Expansion, description or section-read action.
    :param args: Selected verb and path, without output-mode flags.
    :returns: One complete invocation with explicit machine output.
    """
    return {"action": action, "entrypoint": ENTRYPOINT, "args": [*args, "--json"]}


def choice(
    name: str, summary: str, args: Sequence[str], action: str = "expand"
) -> JSONObject:
    """Offer one selected next step.

    :param name: Stable identifier at this level.
    :param summary: Catalogue-owned short description.
    :param args: Navigator arguments for the selected step.
    :param action: Pointer action, defaulting to expansion.
    :returns: A choice using the existing version-one envelope.
    """
    return {
        "id": name,
        "kind": "execute" if action == "describe" else action,
        "summary": summary,
        "next": pointer(action, args),
    }


def document(
    path: Sequence[str], summary: str, choices: Sequence[JSONObject]
) -> MutableMapping[str, JSONValue]:
    """Create an unpaged navigation answer.

    :param path: Resolved canonical node path.
    :param summary: Current level's description.
    :param choices: Immediate offerings, preserving catalogue order.
    :returns: Mutable record ready for bounded pagination.
    """
    return {
        "kind": "reference_next",
        "schema_version": "1.0",
        "path": list(path),
        "summary": summary,
        "choices": list(choices),
        "continuation": None,
    }


def split_inputs(
    data: JSONObject, choices: Sequence[JSONObject], continuation: JSONObject
) -> Sequence[JSONObject]:
    """Split large input descriptions while retaining every required argument.

    :param data: Shared level metadata counted in each response budget.
    :param choices: Complete offerings before pagination.
    :param continuation: Largest continuation pointer for the candidate pages.
    :returns: Offerings with long input lists divided across equivalent pointers.
    :raises NavigationError: If a single input or fixed invocation exceeds the budget.
    """
    result: list[JSONObject] = []
    for item in choices:
        step = json_object(item["next"]) if item.get("next") else {}
        inputs = json_objects(step.get("inputs", []))
        if not inputs:
            result.append(item)
            continue
        selected: list[JSONObject] = []
        for field in inputs:
            candidate = {**item, "next": {**step, "inputs": [*selected, field]}}
            envelope = {**data, "choices": [candidate], "continuation": continuation}
            if (
                len(
                    render_value(envelope, "json", compact=True, ordered=True)
                    .rstrip("\n")
                    .encode()
                )
                > MAX_BYTES
            ):
                if not selected:
                    raise NavigationError(
                        "page_out_of_range", "one input exceeds the response budget"
                    )
                result.append({**item, "next": {**step, "inputs": selected}})
                selected = []
            selected.append(field)
        result.append({**item, "next": {**step, "inputs": selected}})
    return result


def paginate(data: JSONObject, page: int, args: Sequence[str]) -> JSONObject:
    """Page choices by both serialized bytes and count without dropping records.

    :param data: Complete level, with choices not yet paginated.
    :param page: One-based page number.
    :param args: Invocation arguments retained by continuation pointers.
    :returns: One bounded page with an exact next invocation when needed.
    :raises NavigationError: If the page does not exist or one record exceeds the budget.
    """
    original = json_objects(data["choices"])
    continuation = pointer(
        "expand", [*args, "--page", str(max(2, len(original) * MAX_CHOICES))]
    )
    choices = split_inputs(data, original, continuation)
    pages: list[JSONObject] = []
    offset = 0
    while offset < len(choices) or not pages:
        selected: list[JSONObject] = []
        answer = dict(data)
        answer["choices"] = selected
        while offset + len(selected) < len(choices) and len(selected) < MAX_CHOICES:
            item = choices[offset + len(selected)]
            candidate = {**answer, "choices": [*selected, item]}
            remaining = offset + len(selected) + 1 < len(choices)
            candidate["continuation"] = (
                pointer("expand", [*args, "--page", str(len(pages) + 2)])
                if remaining
                else None
            )
            if (
                len(
                    render_value(candidate, "json", compact=True, ordered=True)
                    .rstrip("\n")
                    .encode()
                )
                > MAX_BYTES
            ):
                break
            selected.append(item)
            answer = candidate
        if not selected and choices:
            raise NavigationError(
                "page_out_of_range", "one catalogue choice exceeds the response budget"
            )
        pages.append(answer)
        offset += len(selected)
    if page < 1 or page > len(pages):
        raise NavigationError("page_out_of_range", str(page))
    return pages[page - 1]
