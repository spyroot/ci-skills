#!/usr/bin/env python3
"""Verify explicit GitHub, GitLab, and Kubernetes administrator access."""
from core.cli import execute, parser


def main() -> int:
    args = parser("Check every selected authority; dry run never counts as access.", output_dir=False).parse_args()
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
