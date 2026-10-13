"""The result mapping owned by a skill's shared state.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from collections.abc import Mapping
from typing import TypeAlias

SkillResult: TypeAlias = Mapping[str, object]


def require_result(value: object) -> SkillResult:
    """Require the shared callback result mapping contract.

    :param value: Value returned by a configuration or lifecycle hook.
    :returns: The exact supplied mapping, including an empty mapping.
    :raises TypeError: If value is not a mapping.
    """
    if not isinstance(value, Mapping):
        raise TypeError("callback hooks must return a result mapping")
    return value
