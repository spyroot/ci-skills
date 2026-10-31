"""Validate cross-field replication invariants in installed mirror receipts.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from collections.abc import Sequence
from typing import Final

from lib.python.core.gitlab.mirror.constants import Field
from lib.python.core.json_types import (
    JSONObject,
    json_integer,
    json_object,
    json_objects,
)
from lib.python.core.schema import validate_specification

RESULT_SCHEMA: Final = "results/gitlab-mirror.schema.json"
REFS_UNVERIFIED: Final = "mirror_refs_unverified"


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
    return (
        ()
        if all(check_ref_evidence(record) for record in records)
        else (REFS_UNVERIFIED,)
    )
