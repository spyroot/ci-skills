#!/usr/bin/env python3
"""Plan, apply, and verify an exact GitLab bug issue."""

from core.gitlab_actions import run_action_cli

if __name__ == "__main__":
    raise SystemExit(run_action_cli("gitlab_issue"))
