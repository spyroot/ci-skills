#!/usr/bin/env python3
"""Plan or run a disposable nested Podman runtime readiness probe.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

import _bootstrap  # noqa: F401
from lib.python.core.kubernetes.image_build_readiness import build_parser, main

__all__ = ["build_parser", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
