"""Required operation inputs for concrete skill callbacks.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from abc import ABC
from typing import TypeVar


class SkillAction(ABC):
    """Base for concrete read, authentication, and mutation inputs."""


ActionT_co = TypeVar("ActionT_co", bound=SkillAction, covariant=True)
