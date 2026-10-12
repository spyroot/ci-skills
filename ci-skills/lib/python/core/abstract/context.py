"""A concrete shared state for command-driven workflows.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

import sys
from abc import ABC
from argparse import Namespace
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Self

from lib.python.core.abstract.skill_state import SkillState
from lib.python.core.json_types import JSONValue
from lib.python.core.logging import log_event


@dataclass
class ExecutionContext(SkillState, ABC):
    """Add command arguments and a JSON report to SkillState.

    Inherited invocation fields are documented by SkillState. Provider-specific
    states declare their required provider clients as concrete named fields.

    :param args: Parsed command arguments used to construct the workflow.
    :param report: Mutable command report retained for final rendering.

    .. attribute :: args
        :type: Namespace

        Parsed command arguments used to construct the workflow.

    .. attribute :: report
        :type: MutableMapping[str, JSONValue]

        Command report retained for final rendering.
    """

    args: Namespace = field(default_factory=Namespace)
    report: MutableMapping[str, JSONValue] = field(default_factory=dict)

    @classmethod
    def from_args(cls, args: Namespace) -> Self:
        """Retain the workflow's parsed inputs.

        :param args: Command inputs, including the shared logging options.
        :returns: A context with an empty state-owned result.
        """
        return cls(args=args)

    def trace(self, event: str, details: Mapping[str, object]) -> None:
        """Send debug events through this invocation's shared logging controls.

        :param event: Lifecycle or provider transport event.
        :param details: Event metadata redacted by the shared logger.
        """
        if getattr(self.args, "log_level", "info") != "debug":
            return
        try:
            log_event(
                self.args,
                getattr(self.args, "kind", "workflow"),
                event,
                str(details.get("status", "OBSERVED")),
                level="debug",
                details=details,
            )
        except OSError:
            # A diagnostic destination must not interrupt resource cleanup.
            sys.stderr.write("workflow trace_output_unavailable\n")
