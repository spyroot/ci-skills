"""Select the pinned official gitlab-runner chart for the selected GitLab instance.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Optional
from urllib.parse import urlsplit

CHART_NAME: Final = "gitlab-runner"
DEFAULT_CHART_REPOSITORY: Final = "https://charts.gitlab.io"
CHART_APP_VERSION_KEYS: Final = ("appVersion", "appversion")
CHART_DIGEST_PREFIX: Final = "sha256:"
_VERSION_PREFIX: Final = re.compile(r"^(\d+)\.(\d+)")
_VERSION_PARTS: Final = re.compile(r"(\d+)")
_DIGEST: Final = re.compile(r"[a-f0-9]{64}")


class ChartSelectionError(ValueError):
    """One chart selection input is missing or has no supported chart."""


@dataclass(frozen=True)
class ChartSelection:
    """One resolved chart version bound to the selected GitLab instance.

    :param chart_version: Exact chart version selected for install.
    :param app_version: Chart ``appVersion`` paired with the selection.
    :param gitlab_version: Raw ``/api/v4/version`` value read from the instance.
    :param requested: Caller override, or absence when selection derived it.
    """

    chart_version: str
    app_version: str
    gitlab_version: str
    requested: Optional[str]


@dataclass(frozen=True)
class ChartArchive:
    """Pinned archive location for one selected chart version.

    :param url: Approved HTTPS archive URL served by the chart repository.
    :param digest: Archive SHA-256 digest as lowercase hexadecimal.
    """

    url: str
    digest: str


@dataclass(frozen=True)
class ResolvedRunnerChart:
    """One chart selection bound to its pinned archive for install.

    :param selection: Version rule outcome bound to the instance version.
    :param url: Approved HTTPS archive URL served by the chart repository.
    :param digest: Archive SHA-256 digest as lowercase hexadecimal.
    """

    selection: ChartSelection
    url: str
    digest: str


def _minor(version: str) -> tuple[int, int]:
    """Return the ``MAJOR.MINOR`` pair of one dotted version.

    :param version: Dotted version, optionally suffixed (for example ``-ee``).
    :returns: Numeric ``(major, minor)`` pair.
    :raises ChartSelectionError: The version has no leading ``MAJOR.MINOR``.
    """
    matched = _VERSION_PREFIX.match(version.strip())
    if matched is None:
        raise ChartSelectionError("gitlab_version_unreadable")
    return int(matched.group(1)), int(matched.group(2))


def _rank(version: str) -> tuple[int, ...]:
    """Rank one dotted version by its numeric components.

    :param version: Chart or application version string.
    :returns: Numeric components in order for tuple comparison.
    """
    return tuple(int(part) for part in _VERSION_PARTS.findall(version))


def _entry_app_version(entry: Mapping[str, object]) -> Optional[str]:
    """Read one chart index entry application version.

    :param entry: One parsed chart index entry.
    :returns: Application version, or absence when the entry omits it.
    """
    for key in CHART_APP_VERSION_KEYS:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def select_chart_version(
    gitlab_version: str,
    entries: Sequence[Mapping[str, object]],
    requested: Optional[str] = None,
) -> ChartSelection:
    """Select the highest official chart matching the selected instance.

    A caller override wins after it exists in the index. Otherwise the highest
    chart whose ``appVersion`` shares the instance ``MAJOR.MINOR`` wins; when no
    chart matches the exact minor, the highest lower minor in the same major
    wins, per the GitLab runner version rule.

    :param gitlab_version: Raw ``/api/v4/version`` value of the instance.
    :param entries: Parsed ``gitlab-runner`` entries of the chart index.
    :param requested: Optional exact chart version selected by the caller.
    :returns: Bound chart and application versions with provenance.
    :raises ChartSelectionError: The version is unreadable, or no chart entry
        satisfies the requested or derived selection.
    """
    major, minor = _minor(gitlab_version)
    available: list[tuple[str, str]] = []
    for entry in entries:
        version = entry.get("version")
        app_version = _entry_app_version(entry)
        if isinstance(version, str) and version.strip() and app_version is not None:
            available.append((version.strip(), app_version))
    if not available:
        raise ChartSelectionError("chart_index_empty")
    if requested is not None:
        selected = requested.strip()
        for version, app_version in available:
            if version == selected:
                return ChartSelection(selected, app_version, gitlab_version, selected)
        raise ChartSelectionError("chart_version_not_found")
    matching = [
        (version, app_version)
        for version, app_version in available
        if _safe_minor(app_version) == (major, minor)
    ]
    if not matching:
        minors: set[tuple[int, int]] = set()
        for _, app_version in available:
            try:
                candidate_minor = _minor(app_version)
            except ChartSelectionError:
                continue
            if candidate_minor[0] == major and candidate_minor[1] < minor:
                minors.add(candidate_minor)
        for candidate_minor in sorted(minors, reverse=True):
            matching = [
                (version, app_version)
                for version, app_version in available
                if _safe_minor(app_version) == candidate_minor
            ]
            if matching:
                break
    if not matching:
        raise ChartSelectionError("chart_version_unavailable")
    version, app_version = max(matching, key=lambda pair: _rank(pair[0]))
    return ChartSelection(version, app_version, gitlab_version, None)


def _safe_minor(app_version: str) -> Optional[tuple[int, int]]:
    """Return the ``MAJOR.MINOR`` pair of one version when readable.

    :param app_version: Chart ``appVersion`` value.
    :returns: Numeric ``(major, minor)`` pair, or absence when unreadable.
    """
    try:
        return _minor(app_version)
    except ChartSelectionError:
        return None


def _archive_url(entry: Mapping[str, object], repository: str) -> Optional[str]:
    """Read one HTTPS archive URL served by the selected repository.

    :param entry: One parsed chart index entry.
    :param repository: Approved chart repository origin.
    :returns: First exact archive URL, or absence when none is served there.
    :raises ChartSelectionError: The entry URL list is malformed.
    """
    urls = entry.get("urls")
    if not isinstance(urls, list) or any(not isinstance(url, str) for url in urls):
        raise ChartSelectionError("chart_archive_urls_invalid")
    try:
        authority = urlsplit(repository).hostname or ""
    except ValueError:
        raise ChartSelectionError("chart_repository_invalid") from None
    for url in urls:
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if (
            parsed.scheme == "https"
            and parsed.hostname == authority
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        ):
            return url
    return None


def chart_archive(
    entries: Sequence[Mapping[str, object]],
    selection: ChartSelection,
    repository: str,
) -> ChartArchive:
    """Locate the pinned archive of one selected chart version.

    :param entries: Parsed ``gitlab-runner`` entries of the chart index.
    :param selection: Exact chart version selected for install.
    :param repository: Approved chart repository origin.
    :returns: Archive URL and digest verified against the selected version.
    :raises ChartSelectionError: The selected entry, URL or digest is missing.
    """
    for entry in entries:
        version = entry.get("version")
        if not isinstance(version, str) or version.strip() != selection.chart_version:
            continue
        url = _archive_url(entry, repository)
        if url is None:
            raise ChartSelectionError("chart_archive_url_unavailable")
        digest = entry.get("digest")
        if not isinstance(digest, str):
            raise ChartSelectionError("chart_archive_digest_invalid")
        expected = digest.strip()
        if not expected.startswith(CHART_DIGEST_PREFIX):
            raise ChartSelectionError("chart_archive_digest_invalid")
        hexdigest = expected[len(CHART_DIGEST_PREFIX) :]
        if _DIGEST.fullmatch(hexdigest) is None:
            raise ChartSelectionError("chart_archive_digest_invalid")
        return ChartArchive(url, hexdigest)
    raise ChartSelectionError("chart_archive_entry_missing")
