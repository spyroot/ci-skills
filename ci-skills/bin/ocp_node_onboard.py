#!/usr/bin/env python3
"""Build and verify an OpenShift node ISO at caller-selected cluster storage.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

import _bootstrap  # noqa: F401
from lib.python.core.kubernetes.ocp_onboard import build_parser, main

__all__ = ["build_parser", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
