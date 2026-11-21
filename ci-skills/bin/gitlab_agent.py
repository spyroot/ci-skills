#!/usr/bin/env python3
"""Connect a GitLab project to Kubernetes through its Agent."""

import _bootstrap  # noqa: F401
from lib.python.core.gitlab.cluster_agent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
