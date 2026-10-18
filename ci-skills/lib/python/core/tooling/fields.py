"""Read typed fields from validated dependency and consumer specifications.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Optional, TypeVar

from lib.python.core.json_types import JSONObject, JSONValue, json_string, json_strings
from lib.python.core.schema import SpecificationError

FieldT = TypeVar("FieldT")


class SpecificationFields:
    """Narrow schema fields while retaining their specification location.

    :ivar document: Read-only JSON object supplied by the caller.
    :ivar path: Schema location used in value-free validation failures.
    """

    def __init__(self, document: JSONObject, path: str):
        """Bind the object and its known schema location without copying it.

        :param document: Decoded object whose fields are being converted to a model.
        :param path: Schema path naming the object, without caller-supplied values.
        """
        self.document = document
        self.path = path

    def read(self, name: str, reader: Callable[[JSONValue], FieldT]) -> FieldT:
        """Read one required field through a shared concrete JSON shape reader.

        :param name: Declared schema field to read from the bound object.
        :param reader: Shared JSON reader returning the field's concrete type.
        :returns: Field value narrowed by the selected reader.
        :raises SpecificationError: The field is absent or its JSON shape is invalid.
        """
        try:
            return reader(self.document[name])
        except (KeyError, TypeError) as exc:
            raise SpecificationError(
                f"invalid_specification:{self.path}/{name}:type"
            ) from exc

    def optional_text(self, name: str) -> Optional[str]:
        """Read a text field that is absent for unselected installation routes.

        :param name: Declared optional text field.
        :returns: Original text, or None when the field is absent or JSON null.
        :raises SpecificationError: A present nonnull value is not text.
        """  # noqa: DOC502 - field validation propagates from read
        return None if self.document.get(name) is None else self.read(name, json_string)

    def optional_strings(self, name: str) -> Sequence[str]:
        """Read an optional ordered string array with an empty absence default.

        :param name: Declared optional array field.
        :returns: Declared strings, or an empty sequence when the field is absent.
        :raises SpecificationError: A present value is not an array of strings.
        """  # noqa: DOC502 - field validation propagates from read
        return () if name not in self.document else self.read(name, json_strings)
