"""Combine selected provider access results.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from datetime import UTC, datetime
from functools import partial

from lib.python.access.github import github_access, github_publication_access
from lib.python.access.gitlab import gitlab_access
from lib.python.access.kubernetes import kubernetes_access
from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.abstract.spec import load_spec
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    copy_json,
)
from lib.python.core.target import Target
from lib.python.sys.status import BLOCKED, DRY_RUN, PASS


def _selected_targets(target: Target) -> MutableMapping[str, JSONValue]:
    """Describe only the providers selected by this invocation.

    :param target: Runtime settings with independently optional provider sections.
    :returns: Configured endpoint metadata for the active providers.
    :raises TargetError: A selected provider section is absent.
    """  # noqa: DOC502 - Required provider selectors validate optional sections.
    selected: MutableMapping[str, JSONValue] = {}
    if "github" in target.active_surfaces:
        github = target.require_github()
        selected["github"] = f"{github.host}/{github.repository}"
    if "gitlab" in target.active_surfaces:
        selected["gitlab"] = target.require_gitlab().url
    if "kubernetes" in target.active_surfaces:
        kubernetes = target.require_kubernetes()
        selected["kubernetes"] = {
            "context": kubernetes.context,
            "server": kubernetes.server,
        }
    return selected


def _bound_metadata(target: Target) -> JSONObject:
    """Report frozen source selections without exposing credentials.

    :param target: Selected runtime target, optionally bound to credential sources.
    :returns: Invocation provenance, or an empty mapping before source binding.
    :raises ValueError: A selected provider or its bound source is missing.
    """  # noqa: DOC502 - Source and target selectors validate configured providers.
    sources = target.sources
    if sources is None:
        return {}
    references = {
        name: sources.require(SkillPluginType(name)).reference
        for name in target.active_surfaces
    }
    return {
        "execution_host": sources.execution_host,
        "captured_at": datetime.now(UTC).isoformat(),
        "target_file": str(target.source_file) if target.source_file else None,
        "target_source": target.source_kind,
        "tested_revision": target.tested_revision,
        "skill": copy_json(target.skill),
        "consuming_project": copy_json(target.skill["consuming_project"])
        if target.skill
        else None,
        "credential_sources": references,
        "target_selection": target.target_reference,
        "targets": _selected_targets(target),
    }


def check_access(
    target: Target,
    *,
    publication: bool = False,
    cilium: bool = True,
    state: DeliveryState | None = None,
) -> MutableMapping[str, JSONValue]:
    """Probe active authorities and combine their observed access results.

    :param target: Runtime target declaring active authorities and their sources.
    :param publication: Whether GitHub requires administration and required-check probes.
    :param cilium: Whether Kubernetes requires the Cilium health exec probe.
    :param state: Shared declaration and runtime; omitted creates one invocation state.
    :returns: Access receipt with per-surface results; overall PASS requires every
        selected surface to pass, otherwise the receipt reports BLOCKED.
    :raises ValueError: If active surfaces are invalid, publication lacks GitHub,
        or a required bound credential source is absent.
    """
    if state is None:
        state = DeliveryState(spec=load_spec())
    elif state.spec is None:
        state.spec = load_spec()
    checks = {
        "github": github_publication_access if publication else github_access,
        "gitlab": gitlab_access,
        "kubernetes": partial(kubernetes_access, cilium=cilium),
    }
    _selected = target.active_surfaces

    if not _selected or any(name not in checks for name in _selected):
        raise ValueError("required_target_surfaces_invalid")

    if publication and "github" not in _selected:
        raise ValueError("publication_requires_github")
    surfaces = [checks[name](target, state=state) for name in _selected]

    receipt: MutableMapping[str, JSONValue] = {
        "schema_version": "1.0",
        "kind": "access_check",
        "status": PASS if all(item.status == PASS for item in surfaces) else BLOCKED,
        "publication": publication,
        "surfaces": {item.name: item.as_dict() for item in surfaces},
    }
    receipt.update(_bound_metadata(target))
    return receipt


def dry_run_access(
    target: Target, *, publication: bool = False, cilium: bool = True
) -> JSONObject:
    """Describe access probes without contacting the selected authorities.

    :param target: Runtime target declaring active authorities and their settings.
    :param publication: Whether to include GitHub publication checks.
    :param cilium: Whether to include Cilium health checks.
    :returns: DRY_RUN access receipt listing selected targets and planned probes.
    :raises ValueError: Publication lacks GitHub or an active provider is unconfigured.
    """
    selected = target.active_surfaces
    if publication and "github" not in selected:
        raise ValueError("publication_requires_github")
    probes = {
        "github": [
            "gh auth status",
            "gh api user",
            "gh api repository",
            *(["repository admin"] if publication else []),
        ],
        "gitlab": ["glab auth status", "GET /user is_admin", "GET /runners/all"],
        "kubernetes": [
            "context server",
            "TLS API version",
            "auth whoami",
            "cluster wildcard",
            "clusterrolebinding admin",
            *(
                ["Cilium namespace", "pods/exec", "non-TTY Cilium health"]
                if cilium
                else []
            ),
        ],
    }
    surfaces = {
        name: {
            "target": (
                f"{value['context']} -> {value['server']}"
                if isinstance(value, Mapping)
                else value
            ),
            "probes": probes[name],
        }
        for name, value in _selected_targets(target).items()
    }
    return {
        "schema_version": "1.0",
        "kind": "access_check",
        "status": DRY_RUN,
        "publication": publication,
        "target_selection": target.target_reference,
        "surfaces": surfaces,
    }
