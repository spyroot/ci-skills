"""Render discovery answers for a terminal without expanding anything further.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from collections.abc import Sequence

from lib.python.core.json_types import JSONObject, json_object, json_objects


def read_lines(entries: Sequence[JSONObject], title: str) -> Sequence[str]:
    """Render what can be read at this level, each with its next call.

    :param entries: Read entries already present in the answer.
    :param title: Heading for the list.
    :returns: Indented human text, never fetched content.
    """
    if not entries:
        return []
    width = max(len(str(item["name"])) for item in entries) + 2
    lines = [title]
    for item in entries:
        lines.append(f"  {item['name']!s:<{width}}{item['summary']}")
        lines.append(f"  {'':<{width}}reference.py {item['pointer']}")
    lines.append("")
    return lines


def node_lines(data: JSONObject) -> Sequence[str]:
    """Render one node: what the agent can run, then what it can read.

    :param data: One proposition node.
    :returns: Human text for the node.
    """
    lines: list[str] = []
    tools = list(json_object(data["tools"]))
    if tools:
        lines.append("Run")
        lines.extend(f"  {script}  ({script} --help)" for script in tools)
        lines.append("")
    lines.extend(read_lines(json_objects(data["reference"]), "Read"))
    return lines


def human(data: JSONObject) -> str:
    """Draw the human view from the same bounded machine answer.

    :param data: Root, node or reference-section answer.
    :returns: Human output with the continuation invocation when one exists.
    """
    lines: list[str] = []
    if "error" in data:
        error = json_object(data["error"])
        lines.append(f"{error['reason']}: {error['input']}")
    if data.get("kind") == "reference_section":
        lines.extend(
            (
                f"{data['document']}#{data['anchor']}  pinned {data['pinned']}",
                "",
                str(data["text"]).rstrip("\n"),
                "",
            )
        )
        lines.extend(read_lines(json_objects(data["reference"]), "Subsections"))
    elif "tools" in data:
        if "query" in data:
            lines.append(f'Sections containing "{data["query"]}"')
            lines.append("")
        lines.extend(node_lines(data))
    else:
        for name, value in data.items():
            if name == "schema_version":
                continue
            lines.append(name)
            lines.extend(
                f"  {line}" if line else "" for line in node_lines(json_object(value))
            )
    if data.get("continuation"):
        lines.append(f"More: reference.py {data['continuation']}")
    return "\n".join(lines).rstrip("\n") + "\n"
