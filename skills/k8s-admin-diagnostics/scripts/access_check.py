#!/usr/bin/env python3
"""Verify explicit GitHub, GitLab, and Kubernetes administrator access."""

from core.cli import execute, parser


def main() -> int:
    cli = parser(
        "Check every selected authority; dry run never counts as access.",
        output_dir=False,
    )
    cli.add_argument(
        "--publication",
        action="store_true",
        help="also require GitHub repository administration before configuring checks",
    )
    cli.add_argument(
        "--job-url",
        metavar="URL",
        help="also verify one exact GitLab job, pipeline, runner, and trace",
    )
    cli.add_argument(
        "--receipt-out",
        metavar="PATH",
        help="also write the committable receipt, with host paths digested",
    )
    args = cli.parse_args()
    return execute(args, live_checks=True)


if __name__ == "__main__":
    raise SystemExit(main())
