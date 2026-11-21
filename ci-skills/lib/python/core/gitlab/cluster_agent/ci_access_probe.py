"""Write the selected project's Agent CI proof artifact.

The consumer repository holds a byte copy of this file at its root as
``ci_access_probe.py``, beside ``ci_access_proof.py``; the staged
``.gitlab-ci.yml`` (from ``ci_access_job.yml``) runs it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from ci_access_proof import ProofError, collect_ci_access


def main(argv: Sequence[str] | None = None) -> int:
    """Collect proof using the byte-identical library staged beside this adapter.

    :param argv: Native Agent ID returned by the confirmed connect operation.
    :returns: Zero when the selected context and UIDs were read, otherwise one.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "agent_id", type=int, help="Native ID from verified Agent connect"
    )
    args = parser.parse_args(argv)
    probe = Path(__file__).resolve()
    try:
        result = collect_ci_access(
            os.environ,
            agent_id=args.agent_id,
            definition=Path(".gitlab-ci.yml"),
            probe=probe,
            library=probe.with_name("ci_access_proof.py"),
        )
        with Path("agent-access.json").open("x", encoding="utf-8") as output:
            json.dump(result, output, indent=2)
            output.write("\n")
    except ProofError as exc:
        print(f"Agent CI proof unavailable: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("Agent CI proof artifact could not be written.", file=sys.stderr)
        return 1
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
