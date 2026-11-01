"""Validate cross-field replication invariants in installed mirror receipts.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from collections.abc import Sequence
from typing import Final

from lib.python.core.git.mirroring import MirrorField
from lib.python.core.gitlab.mirror.constants import Field, Outcome
from lib.python.core.json_types import (
    JSONObject,
    json_integer,
    json_object,
    json_objects,
)
from lib.python.core.schema import validate_specification
from lib.python.sys.status import PASS

RESULT_SCHEMA: Final = "results/gitlab-mirror.schema.json"
REFS_UNVERIFIED: Final = "mirror_refs_unverified"
REPLACEMENT_UNVERIFIED: Final = "mirror_replacement_unverified"
PAIR_ACTIONS_UNVERIFIED: Final = "mirror_pair_actions_unverified"
PAIR_IDENTITY_MISMATCH: Final = "mirror_pair_identity_mismatch"
PAIR_REFS_UNVERIFIED: Final = "mirror_pair_refs_unverified"
PAIR_MUTATION_UNVERIFIED: Final = "mirror_pair_mutation_unverified"
PAIR_CONTINUITY_UNVERIFIED: Final = "mirror_pair_continuity_unverified"
IDENTITY_FIELDS: Final = ("algorithm", "digest", "file_count", Field.FUNCTIONAL_DIGEST)


def check_ref_evidence(record: JSONObject) -> bool:
    """Compare receipt counts and fingerprints beyond static schema constraints.

    :param record: One schema-validated, independently observed mirror result.
    :returns: Whether every source ref is accounted for without contradictory digests.
    :raises TypeError: A ref observation is not an object or its count is not an integer.
    """  # noqa: DOC502 - Shared readers and transport propagate the documented failures.
    source = json_object(record.get(Field.SOURCE_REFS))
    destination = json_object(record.get(Field.DESTINATION_REFS))
    source_count = json_integer(source.get(Field.COUNT))
    destination_count = json_integer(destination.get(Field.COUNT))
    verified_count = json_integer(record.get(Field.VERIFIED_SOURCE_REF_COUNT))
    return (
        source_count == verified_count
        and destination_count >= source_count
        and (
            destination_count > source_count
            or source.get(Field.SHA256) == destination.get(Field.SHA256)
        )
    )


def check_replacement_evidence(record: JSONObject) -> bool:
    """Require actual new configuration identity when a receipt claims replacement.

    :param record: One schema-validated verified mirror result.
    :returns: Whether selected old/new identities and creation ownership agree.
    :raises TypeError: Required mirror or ownership evidence has an invalid shape.
    """  # noqa: DOC502 - Shared JSON readers enforce the documented shapes.
    old_id = record.get(Field.REPLACED_MIRROR_ID)
    if record.get(Field.REPLACEMENT_REQUIRED) is not True:
        return old_id is None
    mirror = json_object(record.get(Field.MIRROR))
    created = json_object(record.get(Field.CREATED))
    return (
        json_integer(old_id) != json_integer(mirror.get(Field.ID))
        and created.get(Field.MIRROR) is True
    )


def check_mirror_receipt(receipt: JSONObject) -> Sequence[str]:
    """Apply the installed result schema and nonexpressible ref-count invariants.

    :param receipt: Installed candidate mirror result selected for acceptance.
    :returns: Empty findings for consistent evidence, otherwise a stable failure token.
    :raises SpecificationError: The receipt violates its installed result schema.
    :raises OSError: The installed schema cannot be read.
    :raises TypeError: The schema-valid result contains invalid ref evidence.
    """  # noqa: DOC502 - Shared readers and transport propagate the documented failures.
    validate_specification(receipt, RESULT_SCHEMA)
    records = json_objects(receipt.get(Field.RECORDS))
    failures: list[str] = []
    if not all(check_ref_evidence(record) for record in records):
        failures.append(REFS_UNVERIFIED)
    if not all(check_replacement_evidence(record) for record in records):
        failures.append(REPLACEMENT_UNVERIFIED)
    return failures


def _same_candidate(applied: JSONObject, noop: JSONObject) -> bool:
    """Compare measured installed bytes and the revision actually exercised.

    :param applied: Schema-validated successful mutation receipt.
    :param noop: Schema-validated successful repeat receipt.
    :returns: Whether both receipts bind the same installed and tested candidate.
    """
    first = json_object(applied.get(Field.SKILL))
    second = json_object(noop.get(Field.SKILL))
    revision = json_object(first.get(Field.REVISION)).get(Field.VALUE)
    return (
        all(
            first.get(key) is not None and first.get(key) == second.get(key)
            for key in IDENTITY_FIELDS
        )
        and revision == json_object(second.get(Field.REVISION)).get(Field.VALUE)
        and revision == applied.get(Field.TESTED_REVISION)
        and revision == noop.get(Field.TESTED_REVISION)
        and applied.get(Field.TARGET_SOURCE) == noop.get(Field.TARGET_SOURCE)
    )


def _same_resources(first: JSONObject, second: JSONObject) -> bool:
    """Compare native repository identities and the selected mirror ID.

    :param first: Schema-validated observation or verified record.
    :param second: Schema-validated observation or verified record.
    :returns: Whether both identify the same native source, destination and mirror.
    """
    first_mirror = first.get(Field.MIRROR)
    second_mirror = second.get(Field.MIRROR)
    return (
        first.get(Field.SOURCE) == second.get(Field.SOURCE)
        and first.get(Field.DESTINATION) == second.get(Field.DESTINATION)
        and first_mirror is not None
        and second_mirror is not None
        and json_object(first_mirror).get(Field.ID)
        == json_object(second_mirror).get(Field.ID)
    )


def _same_observation(first: JSONObject, second: JSONObject) -> bool:
    """Connect complete refs while tolerating native background timestamp advances.

    :param first: Schema-validated observation or verified record.
    :param second: Schema-validated observation or verified record.
    :returns: Whether stable resource and ref evidence is unchanged.
    """
    return _same_resources(first, second) and all(
        first.get(key) == second.get(key)
        for key in (Field.SOURCE_REFS, Field.DESTINATION_REFS)
    )


def _consistent_creation(record: JSONObject) -> bool:
    """Link resource creation and replacement to the fresh before observation.

    :param record: Schema-validated successful mirror record.
    :returns: Whether ownership flags and old/new native IDs agree.
    """
    before = json_object(record.get(Field.BEFORE))
    destination = json_object(record.get(Field.DESTINATION))
    previous_destination = json_object(before.get(Field.DESTINATION))
    created = json_object(record.get(Field.CREATED))
    previous_mirror = before.get(Field.MIRROR)
    mirror = json_object(record.get(Field.MIRROR))
    replacement = record.get(Field.REPLACEMENT_REQUIRED) is True
    absent_destination = previous_destination.get(Field.ID) is None
    if created.get(Field.REPOSITORY) is not absent_destination:
        return False
    if not all(
        previous_destination.get(key) == destination.get(key)
        for key in (MirrorField.HOST, MirrorField.PATH)
    ):
        return False
    if not absent_destination and previous_destination != destination:
        return False
    if created.get(Field.MIRROR) is not (previous_mirror is None or replacement):
        return False
    if previous_mirror is None:
        return not replacement
    old_id = json_object(previous_mirror).get(Field.ID)
    return (
        old_id == record.get(Field.REPLACED_MIRROR_ID)
        and old_id != mirror.get(Field.ID)
        if replacement
        else old_id == mirror.get(Field.ID)
    )


def check_mirror_pair(applied: JSONObject, noop: JSONObject) -> Sequence[str]:
    """Qualify a connected mutation and repeat no-op using their native evidence.

    Schema validation and JSON readers propagate their existing errors. Common
    freshness, provenance, redaction and cleanup checks remain with the caller.

    :param applied: Full installed-candidate receipt for the actual mutation.
    :param noop: Full receipt for the subsequent independently observed no-op.
    :returns: Empty findings for a qualified pair, otherwise stable failure tokens.
    """
    failures = [*check_mirror_receipt(applied), *check_mirror_receipt(noop)]
    applied_records = json_objects(applied.get(Field.RECORDS))
    noop_records = json_objects(noop.get(Field.RECORDS))
    if (
        applied.get(Field.STATUS) != PASS
        or noop.get(Field.STATUS) != PASS
        or len(applied_records) != 1
        or len(noop_records) != 1
    ):
        return [*dict.fromkeys(failures), PAIR_ACTIONS_UNVERIFIED]
    first, second = applied_records[0], noop_records[0]
    if (
        first.get(Field.ACTION) != Outcome.APPLIED
        or second.get(Field.ACTION) != Outcome.NO_OP
    ):
        failures.append(PAIR_ACTIONS_UNVERIFIED)
    if not _same_candidate(applied, noop) or not _same_resources(first, second):
        failures.append(PAIR_IDENTITY_MISMATCH)
    if not all(
        check_ref_evidence(record)
        and json_integer(json_object(record.get(Field.SOURCE_REFS)).get(Field.COUNT))
        > 0
        for record in (first, second)
    ):
        failures.append(PAIR_REFS_UNVERIFIED)
    if (
        applied.get(Field.MUTATED) is not True
        or noop.get(Field.MUTATED) is not False
        or not all(_consistent_creation(record) for record in (first, second))
        or any(json_object(second.get(Field.CREATED)).values())
        or second.get(Field.REPLACEMENT_REQUIRED) is True
    ):
        failures.append(PAIR_MUTATION_UNVERIFIED)
    first_before = json_object(first.get(Field.BEFORE))
    second_before = json_object(second.get(Field.BEFORE))
    if (
        not _same_observation(first, second_before)
        or not _same_observation(second_before, second)
        or second_before.get(Field.MIRROR) != second.get(Field.MIRROR)
        or any(
            first_before.get(key) != first.get(key)
            for key in (Field.SOURCE, Field.SOURCE_REFS)
        )
    ):
        failures.append(PAIR_CONTINUITY_UNVERIFIED)
    return list(dict.fromkeys(failures))
