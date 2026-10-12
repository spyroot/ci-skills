"""Load and validate the schemas shipped with the installed skill.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from lib.python.core.paths import SKILL_ROOT

SCHEMA_DIRECTORY: Final = SKILL_ROOT / "schemas"
TOOLBOX_SCHEMA: Final = "toolbox.schema.json"


class SpecificationError(ValueError):
    """A supplied document does not satisfy its installed schema."""


def load_schema(name: str) -> dict[str, object]:
    """Read a schema by its package-relative filename.

    :param name: Filename declared by the consuming operation.
    :returns: The installed JSON Schema object.
    :raises OSError: If the installed schema cannot be read.
    :raises ValueError: If the installed JSON is malformed.
    :raises TypeError: If the installed schema is not an object.
    """
    value = json.loads((SCHEMA_DIRECTORY / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("schema_object_required")
    return value


def validate_specification(document: Mapping[str, object], name: str) -> None:
    """Validate a document against installed schemas without network retrieval.

    :param document: Caller-supplied specification to validate before mutation.
    :param name: Filename of its authoritative installed schema.
    :raises SpecificationError: If a field violates the schema; the diagnostic
        identifies its path and rule without exposing the supplied value.
    :raises OSError: If a required installed schema is unavailable.
    :raises ValueError: If an installed schema document is malformed.
    """
    resources = []
    for path in sorted(SCHEMA_DIRECTORY.glob("*.schema.json")):
        schema = load_schema(path.name)
        Draft202012Validator.check_schema(schema)
        resources.append((str(schema["$id"]), Resource.from_contents(schema)))
    registry = Registry().with_resources(resources)
    validator = Draft202012Validator(load_schema(name), registry=registry)
    try:
        validator.validate(document)
    except ValidationError as exc:
        location = "/".join(str(part) for part in exc.absolute_path) or "/"
        raise SpecificationError(
            f"invalid_specification:{location}:{exc.validator}"
        ) from None
