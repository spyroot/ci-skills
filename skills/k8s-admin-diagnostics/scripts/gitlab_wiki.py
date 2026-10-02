#!/usr/bin/env python3
"""Plan, apply, and verify a selected GitLab wiki page."""

from core.gitlab_actions import action_parser, run_action_cli


def build_parser():
    """Return this command's declared parser for callers and contract checks."""
    return action_parser("gitlab_wiki")


if __name__ == "__main__":
    raise SystemExit(run_action_cli("gitlab_wiki"))
