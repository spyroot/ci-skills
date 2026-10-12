"""Required operation inputs for concrete skill callbacks.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from abc import ABC
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.python.core.target import Target
from typing import TypeVar


class SkillAction(ABC):
    """Base for concrete read, authentication, and mutation inputs."""


ActionT_co = TypeVar("ActionT_co", bound=SkillAction, covariant=True)


@dataclass(frozen=True)
class AuthAction(SkillAction):
    """Select an existing runtime target for a provider authentication operation."""

    target: Target
