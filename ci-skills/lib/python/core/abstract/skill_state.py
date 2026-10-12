"""Mutable invocation state shared by every callback in one executor.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import pickle
from collections.abc import MutableMapping, MutableSequence
from dataclasses import dataclass, field
from typing import ClassVar, Self, TypedDict, TypeVar

from lib.python.core.abstract.skill_result import SkillResult


class CleanupFailure(TypedDict):
    """Report one callback cleanup failure without retaining exception objects.

    :param callback: Concrete callback class whose cleanup hook failed.
    :param reason: Redacted failure text safe for the workflow report.

    .. attribute :: callback
        :type: str

        Concrete callback class whose cleanup hook failed.

    .. attribute :: reason
        :type: str

        Redacted failure text safe for the workflow report.
    """

    callback: str
    reason: str


@dataclass
class SkillState:
    """Own shared invocation data, hook history, and per-callback results.

    Concrete states add domain fields. Every callback retains this same mutable
    instance, so later callbacks observe assignments made by earlier callbacks.

    :param result: Last successful hook result; empty before execution.
    :param records: Successful hook results in execution order, including cleanup.
    :param results: Each bound callback's most recent run result, initially empty.
    :param cleanup_failures: Callback names and sanitized reasons from failed cleanup hooks.
    :param failed: Whether the current run failed or an operation remained incomplete.
    :param name: Caller-supplied workflow name for reporting.
    :param debug: Caller-selected diagnostic detail setting.
    :param verbose: Caller-selected verbose output setting.
    :param environment: Caller-supplied invocation environment values.

    .. attribute :: result
        :type: SkillResult

        Last successful hook result; empty before execution.

    .. attribute :: records
        :type: MutableSequence[SkillResult]

        Successful hook results in execution order, including cleanup.

    .. attribute :: results
        :type: MutableMapping[object, SkillResult]

        Each bound callback's most recent run result, initially empty.

    .. attribute :: cleanup_failures
        :type: MutableSequence[CleanupFailure]

        Callback names and sanitized reasons from failed cleanup hooks.

    .. attribute :: failed
        :type: bool

        Whether the current run failed or an operation remained incomplete.

    .. attribute :: name
        :type: str

        Caller-supplied workflow name for reporting.

    .. attribute :: debug
        :type: bool

        Caller-selected diagnostic detail setting.

    .. attribute :: verbose
        :type: bool

        Caller-selected verbose output setting.

    .. attribute :: environment
        :type: MutableMapping[str, str]

        Caller-supplied invocation environment values.

    .. attribute :: json_encoder
        :type: ClassVar[type[json.JSONEncoder]]

        Encoder used by to_json for the reportable state values.
    """

    result: SkillResult = field(default_factory=dict)
    records: MutableSequence[SkillResult] = field(default_factory=list)
    results: MutableMapping[object, SkillResult] = field(
        default_factory=dict, repr=False
    )
    cleanup_failures: MutableSequence[CleanupFailure] = field(default_factory=list)
    failed: bool = False
    name: str = ""
    debug: bool = False
    verbose: bool = False
    environment: MutableMapping[str, str] = field(default_factory=dict)
    json_encoder: ClassVar[type[json.JSONEncoder]] = json.JSONEncoder

    def state_dict(self) -> SkillResult:
        """Select the common reportable values without serializing provider services.

        Concrete state subclasses override this method to expose additional fields.
        Per-callback keys, failure objects, and cleanup failures are not included here.

        :returns: Mapping containing name, debug/verbose flags, environment, current
            result, and ordered hook records.
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
        """Encode the reportable state selected by state_dict.

        :returns: Indented JSON text containing the selected state values.
        :raises TypeError: A selected value is unsupported by json_encoder.
        :raises ValueError: Selected values contain a circular reference.
        """  # noqa: DOC502 - json.dumps reports unsupported or circular values.
        return json.dumps(self.state_dict(), cls=self.json_encoder, indent=4)

    @classmethod
    def load_state_from_file(cls, file_path: str) -> Self:
        """Restore a concrete state from a caller-selected trusted pickle file.

        :param file_path: Path previously written by save_state_to_file.
        :returns: Deserialized instance of this concrete state class.
        :raises OSError: The file cannot be opened or read.
        :raises EOFError: The file ends before a pickle value can be read.
        :raises pickle.UnpicklingError: The file contains invalid pickle data.
        :raises ImportError: A class required by the stored value cannot be imported.
        :raises AttributeError: A class or attribute referenced by the pickle is unavailable.
        :raises TypeError: The stored object has a different state type.
        """  # noqa: DOC503 - file access and pickle.load propagate decode failures.
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
        """  # noqa: DOC502 - file access and pickle.dump propagate write failures.
        with open(file_path, "wb") as handle:
            pickle.dump(self, handle)


StateT = TypeVar("StateT", bound=SkillState)
