#!/usr/bin/env python3
"""Verify explicit GitHub, GitLab, and Kubernetes administrator access."""
from core.cli import execute, parser


def main() -> int:
    # The receipt is this host's acceptance evidence, so it must be persistable
    # in the same way a collector report is.
    cli = parser("Check every selected authority; dry run never counts as access.")
    cli.add_argument("--publication", action="store_true", help="also require GitHub repository administration before configuring checks")
    args = cli.parse_args()
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
