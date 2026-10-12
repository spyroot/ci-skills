"""JSON value contracts shared by provider response boundaries.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import math
from collections.abc import Mapping, MutableMapping, Sequence
from typing import TypeAlias, TypeGuard, Union

JSONScalar: TypeAlias = Union[str, int, float, bool, None]
JSONValue: TypeAlias = Union[
    JSONScalar, Mapping[str, "JSONValue"], Sequence["JSONValue"]
]
JSONObject: TypeAlias = Mapping[str, JSONValue]
JSONDocument: TypeAlias = Union[JSONObject, Sequence[JSONValue]]


def json_string(value: JSONValue) -> str:
    """Require text without coercing numbers, booleans, or missing values.

    :param value: Decoded JSON field whose schema requires a string.
    :returns: The original string, including an empty string when supplied.
    :raises TypeError: If the field is not a string.
    """
    if not isinstance(value, str):
        raise TypeError("JSON string required")
    return value


def json_integer(value: JSONValue) -> int:
    """Require an integer without accepting Python's boolean subtype.

    :param value: Decoded JSON field whose schema requires an integer.
    :returns: The original integer without truncation or string conversion.
    :raises TypeError: If the field is a boolean or is not an integer.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("JSON integer required")
    return value


def json_object(value: JSONValue) -> JSONObject:
    """Require an object before reading fields from a JSON value.

    :param value: Already decoded JSON field whose object shape is required.
    :returns: The same object through a read-only mapping interface.
    :raises TypeError: If the field is an array, scalar, or JSON null.
    """
    if not isinstance(value, Mapping):
        raise TypeError("JSON object required")
    return value


def json_array(value: JSONValue) -> Sequence[JSONValue]:
    """Require an ordered JSON array without granting mutation rights.

    :param value: Already decoded JSON field whose array shape is required.
    :returns: The same array through a read-only sequence interface.
    :raises TypeError: If the field is an object, scalar, or JSON null.
    """
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError("JSON array required")
    return value


def json_objects(value: JSONValue) -> Sequence[JSONObject]:
    """Require an array containing only JSON objects.

    :param value: Decoded field containing provider or report records.
    :returns: Read-only sequence retaining the original object mappings.
    :raises TypeError: If the field is not an array or an element is not an object.
    """  # noqa: DOC502 - json_array and json_object validate nested shapes.
    return tuple(json_object(item) for item in json_array(value))


def json_strings(value: JSONValue) -> Sequence[str]:
    """Require an array containing only JSON strings.

    :param value: Decoded field containing labels, names, or other text values.
    :returns: Read-only sequence containing the validated strings.
    :raises TypeError: If the field is not an array or an element is not a string.
    """
    result = []
    for item in json_array(value):
        if not isinstance(item, str):
            raise TypeError("JSON string required")
        result.append(item)
    return tuple(result)


def copy_json_object(document: JSONObject) -> MutableMapping[str, JSONValue]:
    """Create an operation-owned mutable copy of a JSON object.

    :param document: Read-only source mapping whose nested containers may be immutable.
    :returns: Independent mutable mapping with copied nested objects and arrays.
    :raises TypeError: If a nested value contains bytes instead of a JSON value.
    :raises RecursionError: If the input contains a container cycle or excessive nesting.
    """  # noqa: DOC502 - Nested traversal propagates depth and value errors.
    return {key: copy_json(value) for key, value in document.items()}


def copy_json(value: JSONValue) -> JSONValue:
    """Copy JSON containers while preserving their scalar values.

    :param value: JSON scalar, mapping, or ordered array to copy.
    :returns: Equal JSON value using new dictionaries and lists for every container.
    :raises TypeError: If a bytes value is supplied in place of a JSON array or string.
    :raises RecursionError: If the input contains a container cycle or excessive nesting.
    """  # noqa: DOC503 - Recursive container copying can exceed nesting limits.
    if isinstance(value, (bytes, bytearray)):
        raise TypeError("bytes are not JSON arrays or strings")
    if isinstance(value, Mapping):
        return copy_json_object(value)
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [copy_json(item) for item in value]
    return value


def is_json_value(value: object) -> TypeGuard[JSONValue]:
    """Narrow a decoded value to the finite JSON data model.

    :param value: Decoded data or a caller-supplied read-only JSON container.
    :returns: Whether the complete value contains only string-keyed objects,
        non-byte sequences, strings, finite numbers, booleans, and JSON null.
        Immutable mappings and tuples are accepted without granting mutation rights.
    :raises RecursionError: If a non-JSON parser supplies cyclic or excessively
        deep containers.
    """  # noqa: DOC502 - Nested traversal propagates depth and value errors.
    if value is None or isinstance(value, (str, int, bool)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(
            isinstance(key, str) and is_json_value(item) for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return all(is_json_value(item) for item in value)
    return False
