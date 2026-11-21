#!/usr/bin/env python3
"""Connect a GitLab project to Kubernetes through its Agent."""

from __future__ import annotations

from typing import Final

import _bootstrap  # noqa: F401
from lib.python.core.gitlab.cluster_agent.cli import build_parser, main

__all__: Final = ("build_parser", "main")

if __name__ == "__main__":
    raise SystemExit(main())
