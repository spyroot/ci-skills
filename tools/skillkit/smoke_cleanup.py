"""Name the keys and kinds of the smoke cleanup records that live acceptance verifies.

`tools/skillkit/mirror_qualification.py` writes the ``gitlab_mirror_smoke_cleanup``
record as ``smoke_cleanup`` of the mirror NO_OP receipt. A runner creation receipt
carries the ``gitlab_runner_smoke_cleanup`` record under the same key.
`tools/check_live_acceptance.py` verifies both records; its
``_smoke_cleanup_matches_execution`` compares the keys below with the receipt that
carries the record and reads that receipt's skill identity through ``skill`` and
``revision``. Both records hold the pre-delete state under ``before``, the delete
calls under ``delete`` and the absence read-back under ``after``.

This module imports nothing from ``lib``: `tools/skillkit/mirror_qualification.py`
imports it before ``installed_bindings`` puts the installed copy's ``lib`` package
first on ``sys.path``.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from typing import Final

# Key of the cleanup record in the receipt of the run that created the objects.
SMOKE_CLEANUP_FIELD: Final = "smoke_cleanup"
# Keys that a cleanup record and its receipt both carry.
KIND_FIELD: Final = "kind"
STATUS_FIELD: Final = "status"
CAPTURED_AT_FIELD: Final = "captured_at"
EXECUTION_HOST_FIELD: Final = "execution_host"
TESTED_REVISION_FIELD: Final = "tested_revision"
# Package digest of the skill that ran; the live acceptance result uses the same key.
SKILL_DIGEST_FIELD: Final = "skill_digest"
# Receipt keys of the skill identity and of the revision record inside it.
SKILL_FIELD: Final = "skill"
REVISION_FIELD: Final = "revision"
# Keys of the pre-delete state, the delete calls and the absence read-back in both
# cleanup records.
BEFORE_FIELD: Final = "before"
DELETE_FIELD: Final = "delete"
AFTER_FIELD: Final = "after"

# Values of KIND_FIELD in the two cleanup records.
MIRROR_CLEANUP_KIND: Final = "gitlab_mirror_smoke_cleanup"
RUNNER_CLEANUP_KIND: Final = "gitlab_runner_smoke_cleanup"
