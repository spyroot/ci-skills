#!/usr/bin/env python3
"""Plan, apply, and verify GitLab runner assignment or creation."""

from core.gitlab_actions import run_action_cli

if __name__ == "__main__":
    raise SystemExit(run_action_cli("gitlab_runner"))
