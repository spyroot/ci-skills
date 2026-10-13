"""Required operation inputs for concrete skill callbacks.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from lib.python.core.target import Target


class SkillAction(ABC):
    """Base for concrete read, authentication, and mutation inputs."""


ActionT_co = TypeVar("ActionT_co", bound=SkillAction, covariant=True)


@dataclass(frozen=True)
class AuthAction(SkillAction):
    """Select the existing runtime target for one authentication operation.

    :param target: Validated authority and credential-source selection.

    .. attribute :: target
        :type: Target

        Runtime authority and credential-source selection.
    """

    target: Target
