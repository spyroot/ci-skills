#!/usr/bin/env python3
"""Inspect and manage NVIDIA GPU Operator resources on OpenShift (CI17).

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

import _bootstrap  # noqa: F401
from lib.python.core.kubernetes.nvidia_gpu.cli import build_parser, main

__all__ = ["build_parser", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
