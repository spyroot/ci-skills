"""Expand one catalogue node or read one selected local reference section.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from collections.abc import Mapping, Sequence

from lib.python.core import catalog, navigate_references
from lib.python.core.json_types import JSONObject, json_object, json_strings
from lib.python.core.navigate_commands import (
    command_path,
    commands,
    count_summary,
    operations,
    planned,
    run_document,
)
from lib.python.core.navigate_common import (
    MAX_PATH,
    MAX_QUERY,
    NavigationError,
    choice,
    document,
    error_document,
    paginate,
)


def nodes() -> Mapping[tuple[str, ...], str]:
    """Build occupied resource paths from catalogue placements.

    :returns: Declared nodes plus automatic placements for newly registered tools.
    """
    result: dict[tuple[str, ...], str] = {(): catalog.NAV_NODES[()]}
    for script, entry in commands().items():
        path = command_path(script)
        for depth in range(1, len(path) + 1):
            prefix = path[:depth]
            result[prefix] = catalog.NAV_NODES.get(
                prefix, str(entry["purpose"]) if prefix == path else prefix[-1]
            )
    return {
        **{
            path: summary
            for path, summary in catalog.NAV_NODES.items()
            if path in result
        },
        **{
            path: summary
            for path, summary in result.items()
            if path not in catalog.NAV_NODES
        },
    }


def resolve_path(path: Sequence[str]) -> tuple[tuple[str, ...], Sequence[str]]:
    """Resolve canonical graph paths, command filenames and direct resource names.

    :param path: Requested segments before a run or read suffix.
    :returns: Resolved resource and any run/read suffix.
    :raises NavigationError: If no unique resource path matches.
    """
    if not path:
        return (), ()
    known = nodes()
    entries = commands()
    if path[0] in entries:
        return command_path(path[0]), path[1:]
    if path[0] in ("run", "read"):
        return (), path
    normalized = (
        "gitlab"
        if path[0] == "glab"
        else "k8s"
        if path[0] == "kubernetes"
        else path[0],
        *path[1:],
    )
    if (normalized[0],) not in known:
        matches = [node for node in known if node and node[-1] == normalized[0]]
        if len(matches) != 1:
            raise NavigationError("path_unknown", normalized[0])
        normalized = (*matches[0], *normalized[1:])
    selected: tuple[str, ...] = ()
    for index, segment in enumerate(normalized):
        if segment in ("run", "read"):
            return selected, normalized[index:]
        selected = (*selected, segment)
        if selected not in known:
            raise NavigationError("path_unknown", segment)
    return selected, ()


def reference_names(path: Sequence[str], scripts: Sequence[str]) -> Sequence[str]:
    """Select task references through existing command proposition connections.

    :param path: Selected resource path; root can address any known reference.
    :param scripts: Commands at or below the selected node.
    :returns: Canonical readable reference identifiers, with no duplicate entries.
    """
    available = navigate_references.references()
    if not path:
        return tuple(available)
    entries = commands()
    connected = dict.fromkeys(scripts)
    for script in scripts:
        connected.update(
            dict.fromkeys(json_strings(entries[script].get("related", ())))
        )
    offering = catalog._proposition_offering(tuple(connected), entries)
    names = json_object(offering["reference"])
    return tuple(
        slug for name in names if (slug := navigate_references.slug(name)) in available
    )


def read_document(
    path: Sequence[str], names: Sequence[str], tail: Sequence[str], query: str
) -> JSONObject:
    """List relevant references or the own-text sections of one selected document.

    :param path: Canonical resource path ending in read.
    :param names: Reference ids connected to this node.
    :param tail: Optional selected reference id.
    :param query: Optional literal substring search over that document.
    :returns: Immediate references or section pointers, never full reference text.
    :raises NavigationError: If the selected reference or suffix cannot be resolved.
    """
    entries = navigate_references.references()
    if not tail:
        if query:
            raise NavigationError("reference_unknown", query)
        choices = [
            choice(name, entries[name]["summary"], ["next", *path, name])
            for name in names
        ]
        noun = "reference" if len(names) == 1 else "references"
        return document(path, f"{len(names)} {noun} with sections here.", choices)
    name, entry = navigate_references.resolve(tail[0])
    if len(tail) != 1 or name not in names:
        raise NavigationError("reference_unknown", tail[0])
    _, sections, _ = navigate_references.load(name)
    selected = [
        section
        for section in sections
        if not query or query.casefold() in section.text.casefold()
    ]
    choices = [
        navigate_references.section_choice(name, section, query) for section in selected
    ]
    noun = "section contains" if len(selected) == 1 else "sections contain"
    summary = f'{len(selected)} {noun} "{query}".' if query else entry["summary"]
    if query and len(summary) > MAX_QUERY:
        summary = f"{len(selected)} matching sections."
    result = document([*path, name], summary, choices)
    if query:
        result["query"] = query
    return result


def node_document(path: Sequence[str]) -> JSONObject:
    """Offer only immediate child resources and the node's run/read connections.

    :param path: Canonical resource path.
    :returns: One level in the catalogue-derived graph.
    """
    known = nodes()
    key = tuple(path)
    local: list[str] = []
    descendants: list[str] = []
    for script in commands():
        selected = command_path(script)
        if selected == key:
            local.append(script)
        if selected[: len(key)] == key:
            descendants.append(script)
    names = reference_names(path, descendants)
    choices = [
        choice(node[-1], summary, ["next", *node])
        for node, summary in known.items()
        if len(node) == len(key) + 1 and node[:-1] == key
    ]
    if local:
        choices.append(
            choice(
                "run",
                count_summary(len(operations(local)), len(planned(local))),
                ["next", *path, "run"],
            )
        )
    if names:
        noun = "reference" if len(names) == 1 else "references"
        choices.append(
            choice(
                "read",
                f"{len(names)} {noun} with sections here.",
                ["next", *path, "read"],
            )
        )
    return document(path, known[key], choices)


def next_document(
    path: Sequence[str], *, page: int = 1, search: str = ""
) -> JSONObject:
    """Expand a requested catalogue node with bounded output and recoverable errors.

    :param path: Resource, run/read and optional selection segments.
    :param page: One-based choice page.
    :param search: Literal query for one selected reference, never a network search.
    :returns: Reference-next answer, or the nearest valid level plus an error.
    """  # noqa: DOC501,DOC503 - Lookup errors are caught and returned in the envelope.
    parent: tuple[str, ...] = ()
    try:
        if len(path) > MAX_PATH or len(search) > MAX_QUERY:
            raise NavigationError("path_unknown", " ".join(path) or search)
        known = nodes()
        if path and (path[0],) not in known:
            matches = [node for node in known if node and node[-1] == path[0]]
            if len(matches) > 1:
                result = document(
                    [],
                    f"{path[0]}: which one?",
                    [
                        choice(".".join(node), known[node], ["next", *node, *path[1:]])
                        for node in matches
                    ],
                )
                return paginate(result, page, ["next", *path])
        parent, tail = resolve_path(path)
        local = [script for script in commands() if command_path(script) == parent]
        if not tail:
            result = node_document(parent)
        elif tail[0] == "run":
            if search:
                raise NavigationError("path_unknown", search)
            result = run_document([*parent, "run"], local, tail[1:])
        else:
            descendants = [
                script
                for script in commands()
                if command_path(script)[: len(parent)] == parent
            ]
            result = read_document(
                [*parent, "read"],
                reference_names(parent, descendants),
                tail[1:],
                search,
            )
        args = ["next", *json_strings(result["path"])]
        if search:
            args.extend(("--search", search))
        return paginate(result, page, args)
    except NavigationError as exc:
        known = nodes()
        for length in range(len(path), 0, -1):
            candidate = tuple(path[:length])
            if candidate in known:
                parent = candidate
                break
        return error_document(node_document(parent), exc, ["next", *parent])


def section_document(
    reference: str, anchor: str, *, page: int = 1, tier: str = ""
) -> JSONObject:
    """Read a selected section, reporting lookup failures through the same envelope.

    :param reference: Known reference id or filename stem.
    :param anchor: Selected heading slug.
    :param page: One-based section page.
    :param tier: Optional card or part selector.
    :returns: Own-text reference section or an actionable error answer.
    """
    try:
        return navigate_references.section_document(
            reference, anchor, page=page, tier=tier
        )
    except NavigationError as exc:
        return error_document(
            document([], "Choose a registered reference.", []), exc, ["next", "read"]
        )
