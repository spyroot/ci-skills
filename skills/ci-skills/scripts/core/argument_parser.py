"""Shared structured argument errors for agent-facing diagnostics."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import datetime, timezone

from .report import emit
from .status import BLOCKED


class StructuredParser(argparse.ArgumentParser):
    """Render invalid arguments in the requested machine format."""

    requested: list[str]

    # Summary: remember requested format before argparse validates options
    # Arguments: optional argv and namespace; Environment inputs: sys.argv fallback
    # Stdout: none; Stderr: none; Exit classes: namespace or parser exit
    # Side effects: stores requested argv; Idempotency: depends on argv
    # Cleanup: none
    def parse_args(self, args=None, namespace=None):
        self.requested = list(sys.argv[1:] if args is None else args)
        return super().parse_args(args, namespace)

    # Summary: emit structured invalid-argument response and exit with status two
    # Arguments: argparse message; Environment inputs: requested output format
    # Stdout: structured error; Stderr: none; Exit classes: SystemExit(2)
    # Side effects: writes stdout; Idempotency: timestamp and host vary
    # Cleanup: none
    def error(self, message: str) -> None:
        data = {
            "schema_version": "1.0",
            "kind": self.prog.removesuffix(".py"),
            "status": BLOCKED,
            "captured_at": datetime.now(timezone.utc).isoformat(),
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
