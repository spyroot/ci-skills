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
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import NoReturn, Optional, TypeVar, Union, overload

from lib.python.core.json_types import JSONObject
from lib.python.core.report import emit
from lib.python.sys.status import BLOCKED

NamespaceT = TypeVar("NamespaceT")


class TrackedArgumentParser(argparse.ArgumentParser):
    """Retain original arguments for a concrete parser's structured errors.

    .. attribute :: requested

        Original arguments retained as an immutable sequence for output selection.
    """

    requested: Sequence[str] = ()

    @overload
    def parse_args(
        self, args: Optional[Iterable[str]] = None, namespace: None = None
    ) -> argparse.Namespace: ...

    @overload
    def parse_args(
        self, args: Optional[Iterable[str]], namespace: NamespaceT
    ) -> NamespaceT: ...

    @overload
    def parse_args(self, *, namespace: NamespaceT) -> NamespaceT: ...

    def parse_args(
        self,
        args: Optional[Iterable[str]] = None,
        namespace: Optional[NamespaceT] = None,
    ) -> Union[argparse.Namespace, NamespaceT]:
        """Retain the original arguments and preserve argparse's namespace contract.

        :param args: Arguments to parse, or None for process arguments.
        :param namespace: Existing namespace to populate, or None for a new namespace.
        :returns: The supplied namespace instance, or a new argparse.Namespace.
        :raises SystemExit: Help was requested or arguments are invalid.
        """  # noqa: DOC502 - argparse handles help and invalid inputs by exiting.
        self.requested = tuple(sys.argv[1:] if args is None else args)
        return super().parse_args(self.requested, namespace)


class StructuredParser(TrackedArgumentParser):
    """Render invalid arguments in the requested machine output format.

    .. attribute :: requested
        :type: Sequence[str]

        Raw option sequence retained before parsing for mode selection.
    """

    requested: Sequence[str]

    def error(self, message: str) -> NoReturn:
        """Emit a bounded invalid-arguments report in the requested output mode.

        :param message: Parser diagnostic from ``argparse``; it is not copied into
            the report to avoid echoing supplied values.
        :raises SystemExit: Always exits with status two after emitting the report.
        """
        data: JSONObject = {
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
