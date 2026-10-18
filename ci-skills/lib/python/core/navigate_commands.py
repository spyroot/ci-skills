"""Derive executable offerings from the existing command catalogue.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from lib.python.core import catalog
from lib.python.core.json_types import (
    JSONObject,
    copy_json_object,
    json_object,
    json_objects,
    json_string,
    json_strings,
)
from lib.python.core.navigate_common import NavigationError, choice, document


def commands() -> Mapping[str, JSONObject]:
    """Read every installed operation from its canonical catalogue.

    :returns: Command definitions, excluding discovery itself.
    """
    return {
        name: copy_json_object(entry)
        for name, entry in {
            **catalog.COMMANDS,
            **catalog.NODE_LOCAL_COMMANDS,
            **catalog.READ_COMMANDS,
        }.items()
    }


def command_path(script: str) -> tuple[str, ...]:
    """Resolve explicit placement or the command's existing provider family.

    :param script: Registered command key.
    :returns: Resource path; unplaced additions remain discoverable by filename.
    """
    if script in catalog.NAV_PLACEMENT:
        return catalog.NAV_PLACEMENT[script]
    providers = catalog.command_authorities(script)
    if not providers:
        return ()
    provider = "k8s" if providers[0] == "kubernetes" else providers[0]
    return provider, PurePosixPath(script).stem


def operations(scripts: Sequence[str]) -> Mapping[str, tuple[str, str]]:
    """Resolve installed operation identifiers without advertising planned verbs.

    :param scripts: Commands offered at this graph node.
    :returns: Operation identifiers mapped to command and actual optional verb.
    :raises ValueError: If two commands at one node claim the same identifier.
    """
    result: dict[str, tuple[str, str]] = {}
    entries = commands()
    for script in scripts:
        verbs = json_object(entries[script].get("subcommands", {}))
        for verb in verbs or {"": {}}:
            name = verb or PurePosixPath(script).stem
            if name in result:
                raise ValueError(f"duplicate navigation capability: {name}")
            result[name] = script, verb
    return result


def effective(script: str, verb: str) -> JSONObject:
    """Overlay declared verb facts on their shared command facts.

    :param script: Existing command key.
    :param verb: Actual verb, or empty for a command without verbs.
    :returns: Independent merged contract with command and tier options.
    """
    entry = commands()[script]
    specific = json_object(json_object(entry.get("subcommands", {})).get(verb, {}))
    options = dict(json_object(specific.get("options", entry.get("options", {}))))
    for tier in json_strings(entry.get("capabilities", ())):
        options = {**catalog.CAPABILITY_OPTIONS[tier], **options}
    return {**entry, **specific, "options": options}


def count_summary(count: int, planned: int = 0) -> str:
    """Describe available and unavailable operation counts.

    :param count: Number of callable capabilities.
    :param planned: Number of explicitly planned capabilities.
    :returns: CI09's compact level summary.
    """
    noun = "capability" if count == 1 else "capabilities"
    suffix = f"; {planned} planned." if planned else "."
    return f"{count} {noun} to run{suffix}"


def planned(scripts: Sequence[str]) -> Mapping[str, str]:
    """Read planned verbs while suppressing operations that have since shipped.

    :param scripts: Command keys at the selected node.
    :returns: Unavailable identifiers and their owning phase.
    """
    available = operations(scripts)
    return {
        verb: phase
        for script in scripts
        for verb, phase in catalog.PLANNED_VERBS.get(script, {}).items()
        if verb not in available
    }


def run_choices(path: Sequence[str], scripts: Sequence[str]) -> Sequence[JSONObject]:
    """Offer actual operations followed by explicitly declared future operations.

    :param path: Canonical path ending in run.
    :param scripts: Command keys at this node.
    :returns: Description pointers or unavailable records without executable pointers.
    """
    result = [
        choice(
            name,
            json_string(effective(script, verb)["purpose"]),
            ["next", *path, name],
            "describe",
        )
        for name, (script, verb) in operations(scripts).items()
    ]
    result.extend(
        {
            "id": name,
            "kind": "execute",
            "summary": f"Unavailable: planned in {phase}.",
            "available": False,
            "next": None,
        }
        for name, phase in planned(scripts).items()
    )
    return result


def execute_pointer(script: str, verb: str, entry: JSONObject) -> JSONObject:
    """Describe a safe initial invocation and its selected inputs.

    :param script: Installed command key.
    :param verb: Actual operation name, or empty.
    :param entry: Effective contract already resolved for this command and verb.
    :returns: Executable pointer to the normal default operation or mutation plan.
    """
    options = json_object(entry["options"])
    required = json_strings(entry.get("required_options", ()))
    optional = [
        name
        for name in options
        if name not in required
        and name not in catalog.UNIVERSAL_OPTIONS
        and name not in ("--apply", "--confirm-plan")
    ]
    args = [verb] if verb else []
    for name in required:
        args.extend((name, f"<{name.removeprefix('--')}>"))
    full_contract = (
        catalog.describe_node(script)
        if script in catalog.NODE_LOCAL_COMMANDS
        else catalog.describe(script)
    )
    if "--json" in json_object(full_contract["options"]):
        args.append("--json")
    names = [*required, *optional]
    return {
        "action": "execute",
        "entrypoint": f"bin/{PurePosixPath(script).name}",
        "args": args,
        "inputs": [
            {
                "name": name.removeprefix("--"),
                "required": name in required,
                **({"summary": options[name]} if name in options else {}),
            }
            for name in names
        ],
    }


def capability(
    path: Sequence[str], script: str, verb: str, optional: bool
) -> JSONObject:
    """Describe one selected capability, with an expansion for its optional inputs.

    :param path: Resolved invocation path.
    :param script: Installed command key.
    :param verb: Actual optional subcommand.
    :param optional: Whether the caller selected the options level.
    :returns: Capability metadata, executable pointer and optional-input expansion.
    """
    entry = effective(script, verb)
    action = dict(execute_pointer(script, verb, entry))
    all_inputs = json_objects(action["inputs"])
    required_inputs = [item for item in all_inputs if item["required"]]
    count = len(all_inputs) - len(required_inputs)
    if not optional:
        action["inputs"] = required_inputs
    options_summary = (
        f"{count} optional input; append it as --name value."
        if count == 1
        else f"{count} optional inputs; append each as --name value."
    )
    summary = json_string(entry["purpose"])
    choices: list[JSONObject] = [
        {
            "id": verb or PurePosixPath(script).stem,
            "kind": "execute",
            "summary": summary,
            "next": action,
        }
    ]
    if count and not optional:
        choices.append(choice("options", options_summary, ["next", *path, "options"]))
    result = document(path, options_summary if optional else summary, choices)
    if not optional:
        mutates = entry.get("mutates", not entry.get("read_only", True))
        returns = json_string(entry["returns"])
        result["capability"] = {
            "command": action["entrypoint"],
            **({"verb": verb} if verb else {}),
            "kind": entry.get("kind", entry.get("report_kind", "")),
            "requires": entry.get("requires", entry.get("requires_authorities", [])),
            "tools": entry.get("required_tools", []),
            "mutates": mutates,
            "side_effects": entry.get("side_effects", "none"),
            "returns": returns,
            "read_back": entry.get(
                "read_back",
                returns if mutates else "read-only; the result is the read-back.",
            ),
        }
    return result


def run_document(
    path: Sequence[str], scripts: Sequence[str], tail: Sequence[str]
) -> JSONObject:
    """Resolve a run listing, one capability or its optional inputs.

    :param path: Resource path ending in run.
    :param scripts: Command keys placed at that resource.
    :param tail: Optional capability and options segments.
    :returns: The selected discovery level.
    :raises NavigationError: If a requested operation or suffix is unavailable.
    """
    available = operations(scripts)
    if not tail:
        return document(
            path,
            count_summary(len(available), len(planned(scripts))),
            run_choices(path, scripts),
        )
    if (
        tail[0] not in available
        or len(tail) > 2
        or (len(tail) == 2 and tail[1] != "options")
    ):
        raise NavigationError("path_unknown", " ".join(tail))
    script, verb = available[tail[0]]
    return capability([*path, *tail], script, verb, len(tail) == 2)
