"""Shared structured argument errors for agent-facing diagnostics.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Optional

from lib.python.core.report import emit
from lib.python.sys.status import BLOCKED


class StructuredParser(argparse.ArgumentParser):
    """Render invalid arguments in the requested machine output format.

    .. attribute :: requested
        :type: Sequence[str]

        Raw option sequence retained before parsing for mode selection.
    """

    requested: Sequence[str]

    def parse_args(
        self,
        args: Optional[Sequence[str]] = None,
        namespace: Optional[argparse.Namespace] = None,
    ) -> argparse.Namespace:
        """Remember requested options before parsing for structured errors.

        :param args: Explicit argument sequence, or ``None`` for process arguments.
        :param namespace: Optional namespace to populate with parsed values.
        :returns: Parsed argument namespace.
        :raises SystemExit: ``argparse`` rejects input or handles help.
        """  # noqa: DOC502 - ArgumentParser.parse_args raises SystemExit for help or invalid input.
        self.requested = tuple(sys.argv[1:] if args is None else args)
        return super().parse_args(args, namespace)

    def error(self, message: str) -> None:
        """Emit a bounded invalid-arguments report in the requested output mode.

        :param message: Parser diagnostic from ``argparse``; it is not copied into
            the report to avoid echoing supplied values.
        :raises SystemExit: Always exits with status two after emitting the report.
        """
        data = {
            "schema_version": "1.0",
            "kind": self.prog.removesuffix(".py"),
            "status": BLOCKED,
            "captured_at": datetime.now(UTC).isoformat(),
            "execution_host": socket.getfqdn(),
            "records": [],
            "errors": [{"source": "arguments", "reason": "invalid_arguments"}],
            "summary": {"record_count": 0, "error_count": 1},
            "safe_next_step": "Check --help and correct the arguments.",
        }
        mode = (
            "json"
            if "--json" in self.requested
            else "yaml"
            if "--yaml" in self.requested
            else "human"
        )
        try:
            print(emit(data, mode), end="")
        except RuntimeError:
            print(json.dumps(data, sort_keys=True))
        raise SystemExit(2)
