"""Represent complete branch and tag observations independently of providers.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final, Self

from lib.python.core.cli_api import APIError

REF_PREFIXES: Final = ("refs/heads/", "refs/tags/")
PEELED_SUFFIX: Final = "^{}"
OID_LENGTHS: Final = frozenset((40, 64))
REF_FIELD_COUNT: Final = 2
FIELD_SEPARATOR: Final = "\t"
RECORD_SEPARATOR: Final = "\n"
INVALID_REFS: Final = "git_ref_response_invalid"
DUPLICATE_REF: Final = "git_ref_duplicate"
OID_PATTERN: Final = re.compile(r"^[0-9a-f]+$")


@dataclass(frozen=True, slots=True)
class GitRef:
    """Bind one full branch/tag name to its exact Git object ID.

    :param name: Full ref name including its heads or tags namespace.
    :param oid: Lowercase SHA-1 or SHA-256 Git object identifier.

    .. attribute :: name
        :type: str

        Full ref name including its heads or tags namespace.

    .. attribute :: oid
        :type: str

        Lowercase SHA-1 or SHA-256 Git object identifier.
    """

    name: str
    oid: str


@dataclass(frozen=True, slots=True)
class RefSnapshot:
    """Retain a complete, provider-independent set of branch and tag refs.

    :param refs: Observed ref/object pairs; digest ordering is canonical.

    .. attribute :: refs
        :type: tuple[GitRef, ...]

        Observed ref/object pairs; digest ordering is canonical.
    """

    refs: tuple[GitRef, ...]

    @classmethod
    def parse(cls, output: str) -> Self:
        """Decode native ls-remote output without conflating annotated tags.

        :param output: Complete stdout from a successful native Git ref read.
        :returns: Sorted refs, excluding redundant peeled-tag observations.
        :raises APIError: A line, object ID, ref namespace or duplicate is invalid.
        """
        observed: dict[str, GitRef] = {}
        for line in output.splitlines():
            if not line:
                continue
            fields = line.split(FIELD_SEPARATOR)
            if len(fields) != REF_FIELD_COUNT:
                raise APIError(INVALID_REFS)
            oid, name = fields
            if (
                len(oid) not in OID_LENGTHS
                or OID_PATTERN.fullmatch(oid) is None
                or not name.startswith(REF_PREFIXES)
                or any(character.isspace() for character in name)
            ):
                raise APIError(INVALID_REFS)
            if name.endswith(PEELED_SUFFIX):
                continue
            if name in observed:
                raise APIError(DUPLICATE_REF)
            observed[name] = GitRef(name, oid)
        return cls(tuple(observed[name] for name in sorted(observed)))

    @property
    def digest(self) -> str:
        """Fingerprint full ref names and actual tag or commit object IDs.

        :returns: SHA-256 over sorted native object-ID/ref-name records.
        """
        content = "".join(
            f"{ref.oid}{FIELD_SEPARATOR}{ref.name}{RECORD_SEPARATOR}"
            for ref in sorted(self.refs, key=lambda item: item.name)
        )
        return hashlib.sha256(content.encode()).hexdigest()

    def matching_count(self, source: RefSnapshot) -> int:
        """Count exact source refs observed independently at this destination.

        :param source: Required upstream ref/object observations.
        :returns: Number of source refs whose complete name and object ID match.
        """
        destination = {ref.name: ref.oid for ref in self.refs}
        return sum(destination.get(ref.name) == ref.oid for ref in source.refs)

    def matches(self, source: RefSnapshot) -> bool:
        """Verify every source ref while permitting destination-only refs.

        :param source: Required upstream ref/object observations.
        :returns: Whether all required upstream objects are present unchanged.
        """
        return self.matching_count(source) == len(source.refs)
