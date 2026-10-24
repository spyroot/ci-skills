"""Load extensible provider declarations for one callback workflow.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TypeAlias

from lib.python.core.json_types import JSONObject, copy_json_object, is_json_value
from lib.python.core.paths import SKILL_ROOT

Spec: TypeAlias = Mapping[str, JSONObject]


def require_spec(value: object) -> Spec:
    """Copy a provider declaration without interpreting provider-specific fields.

    :param value: Caller-supplied or decoded JSON object with provider object values.
    :returns: Independent provider mapping, preserving extensible keys and fields.
    :raises TypeError: The declaration is not a JSON object of JSON objects.
    """
    if not isinstance(value, Mapping) or not is_json_value(value):
        raise TypeError("ci_skills.spec.json must contain a JSON object")
    result: dict[str, JSONObject] = {}
    for key, section in value.items():
        if not isinstance(section, Mapping):
            raise TypeError(f"specification section {key!r} must be a JSON object")
        result[key] = copy_json_object(section)
    return result


def load_spec(path: Path | None = None) -> Spec:
    """Read the supplied file or the required specification installed with the skill.

    :param path: Explicit declaration file; otherwise use the installed skill root.
    :returns: Provider declarations validated only for their JSON object shape.
    :raises OSError: The selected specification file is missing or unreadable.
    :raises json.JSONDecodeError: The selected file is not valid JSON.
    :raises TypeError: The file does not declare an object of provider objects.
    """  # noqa: DOC502 - file reading, JSON parsing, and require_spec validate input.
    source = path if path is not None else SKILL_ROOT / "ci_skills.spec.json"
    with source.open(encoding="utf-8") as handle:
        return require_spec(json.load(handle))
