"""A concrete shared state for command-driven workflows.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from abc import ABC
from argparse import Namespace
from dataclasses import dataclass, field
from typing import Self

from .skill_state import SkillState


@dataclass
class ExecutionContext(SkillState, ABC):
    """Add parsed arguments to the inherited result and record sequence.

    Provider workflows add concrete service fields in their own state classes.
    This common context contains no provider clients.
    """

    args: Namespace = field(default_factory=Namespace)

    @classmethod
    def from_args(cls, args: Namespace) -> Self:
        """Retain the workflow's parsed inputs.

        :param args: Command inputs, including the shared logging options.
        :returns: A context with an empty state-owned result.
        """
        return cls(args=args)
