"""The result mapping owned by a skill's shared state.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from collections.abc import Mapping
from typing import TypeAlias

SkillResult: TypeAlias = Mapping[str, object]
