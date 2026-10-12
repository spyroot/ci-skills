"""Mutable invocation state shared by every callback in one executor.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import pickle
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import ClassVar, Self, TypeVar

from .skill_result import SkillResult


@dataclass
class SkillState:
    """Own the result and common invocation data.

    Concrete states add typed fields, such as ``x: int = 0``. Callbacks retain
    this same mutable instance; ordinary field assignments are visible to all.
    """

    result: SkillResult = field(default_factory=dict)
    records: Sequence[SkillResult] = field(default_factory=list)
    failed: bool = False
    name: str = ""
    debug: bool = False
    verbose: bool = False
    environment: dict[str, str] = field(default_factory=dict)
    json_encoder: ClassVar[type[json.JSONEncoder]] = json.JSONEncoder

    def state_dict(self) -> SkillResult:
        """Return the common, reportable state values.

        Concrete states override this method to include their reportable fields.

        :returns: A dictionary of invocation metadata and the current result.
        """
        return {
            "name": self.name,
            "debug": self.debug,
            "verbose": self.verbose,
            "environment": dict(self.environment),
            "result": dict(self.result),
            "records": [dict(record) for record in self.records],
        }

    def __str__(self) -> str:
        """Render the common invocation settings.

        :returns: A human-readable summary of this state.
        """
        return f"name: {self.name}\ndebug: {self.debug}\nverbose: {self.verbose}"

    def to_json(self) -> str:
        """Serialize the reportable state values.

        :returns: JSON containing the values exposed by state_dict.
        :raises TypeError: If a value is unsupported by json_encoder.
        """
        return json.dumps(self.state_dict(), cls=self.json_encoder, indent=4)

    @classmethod
    def load_state_from_file(cls, file_path: str) -> Self:
        """Restore a concrete state from a caller-selected trusted pickle file.

        :param file_path: File previously written by save_state_to_file.
        :returns: The restored concrete state.
        :raises OSError: If the file cannot be read.
        :raises pickle.UnpicklingError: If the file is not valid pickle data.
        :raises TypeError: If the stored object has another state type.
        """
        with open(file_path, "rb") as handle:
            state = pickle.load(handle)
        if not isinstance(state, cls):
            raise TypeError(f"Expected {cls.__name__}, got {type(state).__name__}.")
        return state

    def save_state_to_file(self, file_path: str) -> None:
        """Serialize this state to the caller's explicit file.

        :param file_path: Destination for the serialized concrete state.
        :raises OSError: If the destination cannot be written.
        :raises pickle.PicklingError: If this state cannot be serialized.
        """
        with open(file_path, "wb") as handle:
            pickle.dump(self, handle)


StateT = TypeVar("StateT", bound=SkillState)
