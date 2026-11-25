"""Focused unit contracts for the kubernetes-executor chart selection.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import random
import string

import pytest
from conftest import import_script_module

CHART = import_script_module("lib.python.core.gitlab.runner_manager.chart_selection")


def _entry(version: str, app_version: str) -> dict[str, str]:
    """Build one chart index entry."""
    return {"version": version, "appVersion": app_version}


def _series(major: int, minor: int, count: int = 3) -> list[dict[str, str]]:
    """Build ``count`` descending chart entries for one application minor."""
    return [
        _entry(f"{major}.{minor}.{_patch}", f"{major}.{minor}.0")
        for _patch in range(count, 0, -1)
    ]


def test_omission_selects_highest_matching_minor() -> None:
    entries = [*_series(18, 3), *_series(18, 4), *_series(18, 5)]
    selected = CHART.select_chart_version("18.5.2-ee", entries)
    assert selected.chart_version == "18.5.3"
    assert selected.app_version == "18.5.0"
    assert selected.gitlab_version == "18.5.2-ee"
    assert selected.requested is None


def test_override_wins_when_present() -> None:
    entries = [*_series(18, 4), *_series(18, 5)]
    selected = CHART.select_chart_version("18.5.1", entries, requested="18.4.2")
    assert selected.chart_version == "18.4.2"
    assert selected.requested == "18.4.2"


def test_override_must_exist_in_index() -> None:
    entries = _series(18, 5)
    with pytest.raises(CHART.ChartSelectionError, match="chart_version_not_found"):
        CHART.select_chart_version("18.5.1", entries, requested="18.4.9")


def test_falls_back_to_highest_lower_minor_in_same_major() -> None:
    entries = [*_series(18, 2), *_series(18, 4)]
    selected = CHART.select_chart_version("18.5.0", entries)
    assert selected.chart_version == "18.4.3"
    assert selected.app_version == "18.4.0"


def test_lower_major_is_never_selected() -> None:
    entries = [*_series(17, 9), *_series(18, 0)]
    selected = CHART.select_chart_version("18.1.0", entries)
    assert selected.chart_version == "18.0.3"
    with pytest.raises(CHART.ChartSelectionError, match="chart_version_unavailable"):
        CHART.select_chart_version("18.1.0", _series(17, 9))


def test_higher_minor_is_never_selected() -> None:
    entries = [*_series(18, 6), *_series(18, 5)]
    selected = CHART.select_chart_version("18.5.0", entries)
    assert selected.chart_version == "18.5.3"


def test_unreadable_instance_version_rejected() -> None:
    with pytest.raises(CHART.ChartSelectionError, match="gitlab_version_unreadable"):
        CHART.select_chart_version("ee-only", _series(18, 5))


def test_empty_index_rejected() -> None:
    with pytest.raises(CHART.ChartSelectionError, match="chart_index_empty"):
        CHART.select_chart_version("18.5.1", [])


def test_entries_without_app_version_are_skipped() -> None:
    entries = [{"version": "18.5.1"}, *_series(18, 5)]
    selected = CHART.select_chart_version("18.5.1", entries)
    assert selected.chart_version == "18.5.3"


def test_lowercase_appversion_key_accepted() -> None:
    entries = [{"version": "18.5.1", "appversion": "18.5.1"}]
    selected = CHART.select_chart_version("18.5.1", entries)
    assert selected.chart_version == "18.5.1"


def test_randomized_matching_minor_selection() -> None:
    """Any matching-minor set selects the highest patch regardless of order."""
    rng = random.Random(20261125)
    for _ in range(20):
        major = rng.randint(10, 20)
        minor = rng.randint(0, 12)
        patch_count = rng.randint(1, 6)
        suffix = "".join(
            rng.choice(string.ascii_lowercase) for _ in range(rng.randint(0, 4))
        )
        versions = list(range(1, patch_count + 1))
        rng.shuffle(versions)
        entries = [
            _entry(f"{major}.{minor}.{patch}", f"{major}.{minor}.0")
            for patch in versions
        ]
        selected = CHART.select_chart_version(
            f"{major}.{minor}.{rng.randint(0, 9)}{suffix}", entries
        )
        assert selected.chart_version == f"{major}.{minor}.{patch_count}"
