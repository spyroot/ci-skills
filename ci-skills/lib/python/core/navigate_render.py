"""Render progressive answers without expanding any additional reference.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

import shlex
from collections.abc import Sequence
from pathlib import PurePosixPath

from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    json_object,
    json_objects,
    json_strings,
)


def invocation(record: JSONObject) -> str:
    """Render a pointer as a copyable human invocation.

    :param record: Command pointer from the current answer.
    :returns: Shell-quoted command omitting the machine-output flag.
    """
    args = [
        PurePosixPath(str(record["entrypoint"])).name,
        *(item for item in json_strings(record["args"]) if item != "--json"),
    ]
    return shlex.join(args)


def choice_lines(choices: Sequence[JSONObject]) -> Sequence[str]:
    """Render only the choices and input hints already present in an answer.

    :param choices: Current page of choices.
    :returns: Indented human text, never fetched content.
    """
    lines: list[str] = []
    width = max((len(str(item["id"])) for item in choices), default=7) + 2
    labels = {
        "expand": "Expand",
        "describe": "Describe",
        "execute": "Run",
        "read": "Read",
        "retrieve": "Retrieve",
    }
    for item in choices:
        lines.append(f"  {item['id']!s:<{width}}{item['summary']}")
        indent = " " * (width + 2)
        if item.get("available") is False:
            lines.append(f"{indent}Unavailable")
        else:
            step = json_object(item["next"])
            lines.append(f"{indent}{labels[str(step['action'])]}: {invocation(step)}")
            for field in json_objects(step.get("inputs", [])):
                label = "Needs" if field["required"] else "Optional"
                lines.append(
                    f"{indent}{label}: --{field['name']}  {field.get('summary', '')}".rstrip()
                )
        if "when" in item:
            lines.append(f"{indent}{item['when']}")
        lines.append("")
    return lines


def value_text(value: JSONValue) -> str:
    """Format one already-selected capability fact.

    :param value: Boolean, scalar or ordered string list.
    :returns: Human representation with explicit empty-list meaning.
    """
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(json_strings(value)) or "none"
    return str(value)


def human(data: JSONObject) -> str:
    """Draw the CI09 human view from the same bounded machine answer.

    :param data: Reference-next or reference-section answer.
    :returns: Human output with expandable sections and continuation invocation.
    """
    lines: list[str] = []
    if "error" in data:
        error = json_object(data["error"])
        lines.append(f"{error['reason']}: {error['input']}")
    is_section = data["kind"] == "reference_section"
    if is_section:
        lines.extend(
            (
                f"{data['reference']}#{data['anchor']}  pinned {data['pinned']}",
                "",
                str(data["text"]),
            )
        )
    else:
        lines.extend((str(data["summary"]), ""))
    if "capability" in data:
        facts = json_object(data["capability"])
        for name in (
            "command",
            "kind",
            "requires",
            "tools",
            "mutates",
            "side_effects",
            "returns",
            "read_back",
        ):
            value = value_text(facts[name])
            if name == "command" and facts.get("verb"):
                value += " " + str(facts["verb"])
            lines.append(f"  {name:<14}{value}")
        lines.append("")
    groups: dict[str, list[JSONObject]] = {}
    for item in json_objects(data["choices"]):
        name, kind = item["id"], item["kind"]
        group = (
            "Subsections"
            if is_section
            else "Commands / combos"
            if kind == "execute" or name in ("run", "options")
            else "References"
            if kind == "read" or name == "read"
            else "Results"
            if kind == "retrieve"
            else "Subresources"
            if data.get("path")
            else "Available groups"
        )
        groups.setdefault(group, []).append(item)
    for name in (
        "Available groups",
        "Subresources",
        "Commands / combos",
        "References",
        "Results",
        "Subsections",
    ):
        if name in groups:
            lines.append(name)
            lines.extend(choice_lines(groups[name]))
    if data.get("continuation"):
        lines.append("More: " + invocation(json_object(data["continuation"])))
    return "\n".join(lines) + "\n"
