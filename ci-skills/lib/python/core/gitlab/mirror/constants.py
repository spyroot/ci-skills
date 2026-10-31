"""Own the immutable mirror command contract and failure classifications.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from enum import StrEnum
from typing import Final

from lib.python.core.git.mirroring import MirrorPolicy

KIND: Final = "gitlab_mirror"
COMMAND: Final = "gitlab_mirror.py"
OPERATION: Final = "sync"
PASSWORD_AUTH: Final = "password"
POLL_SECONDS: Final = 2
DEFAULT_POLICY: Final = MirrorPolicy(True, False)
DESTINATION_PATTERN: Final = r"^[^/\s]+/[^/\s]+$"
CLONE_URL_TEMPLATE: Final = "https://{host}/{repository}.git"


class Field(StrEnum):
    """Name normalized inputs, confirmation fields and bounded evidence.

    .. attribute :: OPERATION

        Native token ``operation``.

    .. attribute :: PROJECT

        Native token ``project``.

    .. attribute :: DESTINATION

        Native token ``destination``.

    .. attribute :: CREATE_IF_ABSENT

        Native token ``create_if_absent``.

    .. attribute :: INTENT

        Native token ``intent``.

    .. attribute :: SOURCE

        Native token ``source``.

    .. attribute :: SOURCE_REFS

        Native token ``source_refs``.

    .. attribute :: DESTINATION_REFS

        Native token ``destination_refs``.

    .. attribute :: MIRROR

        Native token ``mirror``.

    .. attribute :: POLICY

        Native token ``policy``.

    .. attribute :: FUNCTIONAL_DIGEST

        Native token ``functional_digest``.

    .. attribute :: CREDENTIAL_DIGEST

        Native token ``credential_digest``.

    .. attribute :: COUNT

        Native token ``count``.

    .. attribute :: SHA256

        Native token ``sha256``.

    .. attribute :: ID

        Native token ``id``.

    .. attribute :: URL

        Native token ``url``.

    .. attribute :: ENABLED

        Native token ``enabled``.

    .. attribute :: AUTH_METHOD

        Native token ``auth_method``.

    .. attribute :: CALLBACK

        Native token ``callback``.

    .. attribute :: ACTION

        Native token ``action``.

    .. attribute :: VERIFIED

        Native token ``verified``.

    .. attribute :: VERIFIED_SOURCE_REF_COUNT

        Native token ``verified_source_ref_count``.

    .. attribute :: CREATED

        Native token ``created``.

    .. attribute :: REPOSITORY

        Native token ``repository``.

    .. attribute :: PLAN_DIGEST

        Native token ``plan_digest``.

    .. attribute :: MUTATED

        Native token ``mutated``.

    .. attribute :: RECORDS

        Native token ``records``.

    .. attribute :: ERRORS

        Native token ``errors``.

    .. attribute :: STATUS

        Native token ``status``.

    .. attribute :: CLEANUP

        Native token ``cleanup``.

    .. attribute :: FAILURES

        Native token ``failures``.

    .. attribute :: SKILL

        Native token ``skill``.

    .. attribute :: REVISION

        Native token ``revision``.

    .. attribute :: VALUE

        Native token ``value``.

    .. attribute :: TESTED_REVISION

        Native token ``tested_revision``.

    .. attribute :: REASON

        Native token ``reason``.
    """

    OPERATION = "operation"
    PROJECT = "project"
    DESTINATION = "destination"
    CREATE_IF_ABSENT = "create_if_absent"
    INTENT = "intent"
    SOURCE = "source"
    SOURCE_REFS = "source_refs"
    DESTINATION_REFS = "destination_refs"
    MIRROR = "mirror"
    POLICY = "policy"
    FUNCTIONAL_DIGEST = "functional_digest"
    CREDENTIAL_DIGEST = "credential_digest"
    COUNT = "count"
    SHA256 = "sha256"
    ID = "id"
    URL = "url"
    ENABLED = "enabled"
    AUTH_METHOD = "auth_method"
    CALLBACK = "callback"
    ACTION = "action"
    VERIFIED = "verified"
    VERIFIED_SOURCE_REF_COUNT = "verified_source_ref_count"
    CREATED = "created"
    REPOSITORY = "repository"
    PLAN_DIGEST = "plan_digest"
    MUTATED = "mutated"
    RECORDS = "records"
    ERRORS = "errors"
    STATUS = "status"
    CLEANUP = "cleanup"
    FAILURES = "failures"
    SKILL = "skill"
    REVISION = "revision"
    VALUE = "value"
    TESTED_REVISION = "tested_revision"
    REASON = "reason"


class Outcome(StrEnum):
    """Distinguish planned observations, actual writes and verified no-op.

    .. attribute :: PLANNED

        Native token ``PLANNED``.

    .. attribute :: APPLIED

        Native token ``APPLIED``.

    .. attribute :: NO_OP

        Native token ``NO_OP``.
    """

    PLANNED = "PLANNED"
    APPLIED = "APPLIED"
    NO_OP = "NO_OP"


class Failure(StrEnum):
    """Classify mirror failures without echoing provider payloads or credentials.

    .. attribute :: INVALID_INTENT

        Native token ``mirror_intent_invalid``.

    .. attribute :: SOURCE_MISSING

        Native token ``mirror_source_missing``.

    .. attribute :: DESTINATION_MISSING

        Native token ``mirror_destination_missing``.

    .. attribute :: AMBIGUOUS

        Native token ``mirror_destination_ambiguous``.

    .. attribute :: STALE_PLAN

        Native token ``mirror_plan_mismatch``.

    .. attribute :: UNCONFIRMED

        Native token ``mirror_live_plan_confirmation_required``.

    .. attribute :: NATIVE_FAILURE

        Native token ``mirror_native_update_failed``.

    .. attribute :: REF_MISMATCH

        Native token ``mirror_destination_refs_unverified``.

    .. attribute :: SOURCE_CHANGED

        Native token ``mirror_source_changed_during_sync``.

    .. attribute :: TIMEOUT

        Native token ``mirror_verification_timeout``.

    .. attribute :: INVALID_TIMEOUT

        Native token ``mirror_timeout_invalid``.
    """

    INVALID_INTENT = "mirror_intent_invalid"
    SOURCE_MISSING = "mirror_source_missing"
    DESTINATION_MISSING = "mirror_destination_missing"
    AMBIGUOUS = "mirror_destination_ambiguous"
    STALE_PLAN = "mirror_plan_mismatch"
    UNCONFIRMED = "mirror_live_plan_confirmation_required"
    NATIVE_FAILURE = "mirror_native_update_failed"
    REF_MISMATCH = "mirror_destination_refs_unverified"
    SOURCE_CHANGED = "mirror_source_changed_during_sync"
    TIMEOUT = "mirror_verification_timeout"
    INVALID_TIMEOUT = "mirror_timeout_invalid"
