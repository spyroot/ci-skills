"""Operation names and resource or token states shared by the GitLab Agent modules.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from typing import Final

from lib.python.core.kubernetes.kinds import LEASE_KIND, REPLICA_SET_KIND

# Operation names accepted by gitlab_agent.py and recorded in results and receipts.
CONNECT: Final = "connect"
VERIFY: Final = "verify"
DELETE: Final = "delete"

# State of an owned resource row or of the Agent token.
PRESENT: Final = "PRESENT"
ABSENT: Final = "ABSENT"
# Only the ownership record names the token; this invocation did not read it natively.
RECORDED: Final = "RECORDED"

# Most agentk Pods, and so ReplicaSets, one run connects and records.
AGENT_POD_LIMIT: Final = 10

# Objects the Agent workload created after install. Connect records them; delete
# removes the ones still present and accepts the ones already gone, and its
# evidence may show them ABSENT before the delete wrote anything.
AGENT_RUNTIME_KINDS: Final = frozenset((REPLICA_SET_KIND, LEASE_KIND))
