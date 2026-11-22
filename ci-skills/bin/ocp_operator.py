#!/usr/bin/env python3
"""Enable an OLM operator on OpenShift and apply its shipped resources.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

import _bootstrap  # noqa: F401
from lib.python.core.kubernetes.olm_cli import build_parser, main

__all__ = ["build_parser", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
