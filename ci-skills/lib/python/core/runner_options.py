"""Validate shared runner inputs once before plan construction or mutation.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from lib.python.core.kubernetes.run_scope import (
    DNS_LABEL_LIMIT,
    dns_token,
    validate_namespace,
)
from lib.python.core.runner import REGISTRATION_SECRET_SUFFIX
from lib.python.core.runner_identity import NameSource, RepositoryIdentity, ResolvedName

RUNNER_NAME_LIMIT: Final = DNS_LABEL_LIMIT - len(REGISTRATION_SECRET_SUFFIX)
RUNNER_NAME_INVALID: Final = "runner_name_invalid"

PROTECTED_INPUT: Final = "true"
UNPROTECTED_INPUT: Final = "false"
PROTECTION_INVALID: Final = "protected_must_be_true_or_false"
TAG_EMPTY: Final = "tag_required"
TAG_COMMA: Final = "tag_must_not_contain_comma"
TAG_DUPLICATE: Final = "duplicate_tag"
PROTECTION_READBACK_MISMATCH: Final = "runner_protection_readback_mismatch"
SINGULAR_CONFLICT: Final = "conflicting values for "


class RunnerProtection(StrEnum):
    """Native GitLab runner access levels; unrelated to untagged jobs."""

    PROTECTED = "ref_protected"
    UNPROTECTED = "not_protected"


PROTECTION_VALUES: Final = MappingProxyType(
    {
        PROTECTED_INPUT: RunnerProtection.PROTECTED,
        UNPROTECTED_INPUT: RunnerProtection.UNPROTECTED,
    }
)


def parse_protected(value: str | None) -> RunnerProtection | None:
    """Map one strict CLI selection without treating omission as false.

    :param value: Literal ``true`` or ``false``, or absence.
    :returns: Native provider access level, or absence for preservation/default.
    :raises ValueError: The value is not one supported literal.
    """
    if value is None:
        return None
    try:
        return PROTECTION_VALUES[value]
    except (KeyError, TypeError) as exc:
        raise ValueError(PROTECTION_INVALID) from exc


def validate_runner_tags(values: Sequence[str]) -> tuple[str, ...]:
    """Validate combined canonical and legacy tag inputs without deduplication.

    :param values: Tags in their original argument order.
    :returns: Validated nonblank tags in the same order.
    :raises ValueError: A tag is blank, contains a comma, or repeats another tag.
    """
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(TAG_EMPTY)
    tags = tuple(value.strip() for value in values)
    if any("," in value for value in tags):
        raise ValueError(TAG_COMMA)
    if len(set(tags)) != len(tags):
        raise ValueError(TAG_DUPLICATE)
    return tags


class SingleValue(argparse.Action):
    """Allow equal repeats and reject conflicting singular argument aliases."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        """Retain one canonical value shared by every accepted alias.

        :param parser: Parser used for a safe usage error.
        :param namespace: Parsed arguments receiving the canonical destination.
        :param values: Value selected by this occurrence.
        :param option_string: Accepted canonical name or alias used by this occurrence.
        :returns: None after setting the canonical value.
        """
        current = getattr(namespace, self.dest, None)
        if current is not None and current != values:
            parser.error(SINGULAR_CONFLICT + self.option_strings[0])
        setattr(namespace, self.dest, values)


def runner_tags(
    values: Sequence[str], repository: RepositoryIdentity
) -> tuple[str, ...]:
    """Apply repository tags only when the caller omitted every explicit tag.

    :param values: Ordered explicit tags or labels.
    :param repository: Selected consumer repository, possibly pending authentication.
    :returns: Validated explicit values, the repository leaf, or pending empty intent.
    :raises ValueError: Supplied tag values are malformed.
    """
    return validate_runner_tags(
        values or ((repository.name,) if repository.name else ())
    )


def kubernetes_runner_name(name: ResolvedName) -> ResolvedName:
    """Normalize derived names and reject invalid explicit Kubernetes identities.

    :param name: Explicit or repository-derived workload identity.
    :returns: Bounded identity safe for ownership labels and suffixed Secrets.
    :raises ValueError: An explicit name is invalid, or normalization yields no token.
    """
    if name.source == NameSource.EXPLICIT:
        validate_namespace(name.value)
        if len(name.value) > RUNNER_NAME_LIMIT:
            raise ValueError(RUNNER_NAME_INVALID)
        return name
    return ResolvedName(dns_token(name.value, limit=RUNNER_NAME_LIMIT), name.source)
