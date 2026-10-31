#!/usr/bin/env python3
"""Synchronize a selected GitLab repository through the shared mirror workflow.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from typing import Final

import _bootstrap  # noqa: F401
from lib.python.core.gitlab.mirror.cli import build_parser, main

__all__: Final = ("build_parser", "main")

if __name__ == "__main__":
    raise SystemExit(main())
