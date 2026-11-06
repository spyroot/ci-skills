"""Offline regressions for GitLab instance selection and the routing table.

A target declares a private and a public GitLab instance table and an optional
routing table. ``--gitlab private|public`` selects an instance; omission reads
the routing default, else the private instance. The legacy GitLab table and its
named profiles stay readable for one release with a deprecation event. Every
target is composed by the conftest builder from the contract constants, or is
a copy of the shipped template. Command tests observe what fake ``glab``,
``kubectl`` and ``helm`` received.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import copy
import json
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Optional
from urllib.parse import urlsplit

import pytest
from conftest import (
    FIXTURE_GITHUB,
    FIXTURE_GITLAB,
    FIXTURE_HARBOR,
    FIXTURE_KUBERNETES,
    TARGET_TEMPLATE,
    TomlValue,
    copy_template_target,
    install_executable,
    legacy_profile,
    paired,
    parse_json_output,
    read_journal,
    routing_table,
    run_script,
    write_target,
)
from lib.python.core.catalog import (
    GITLAB_PROFILE_OPTION,
    GITLAB_TARGET_OPTION,
    GITLAB_VARIABLES,
    ROUTED_RESULT_SCHEMA_VERSION,
)
from lib.python.core.endpoints import (
    GITHUB_AUTHORITY,
    GITLAB_AUTHORITY,
    GITLAB_DEFAULT_INSTANCE,
    GITLAB_DEPRECATION_EVENT,
    GITLAB_INSTANCE_REQUIRED,
    GITLAB_LEGACY_PUBLIC_KEY,
    GITLAB_PRIVATE_DECLARED_TWICE,
    GITLAB_PRIVATE_SELECTOR,
    GITLAB_PRIVATE_TABLE,
    GITLAB_PROFILE_UNKNOWN,
    GITLAB_PUBLIC_SELECTOR,
    GITLAB_PUBLIC_TABLE,
    GITLAB_ROUTED_AUTHORITIES,
    GITLAB_ROUTING_DEFAULT_KEY,
    GITLAB_ROUTING_DEFAULT_UNKNOWN,
    GITLAB_ROUTING_PAIRING_UNKNOWN,
    GITLAB_ROUTING_ROW_MISSING,
    GITLAB_ROUTING_ROW_UNKNOWN,
    GITLAB_ROUTING_TABLE,
    GITLAB_SELECTION_REQUIRES_GITLAB,
    GITLAB_SELECTOR_CONFLICT,
    GITLAB_SELECTOR_UNKNOWN,
    HARBOR_AUTHORITY,
    KUBERNETES_AUTHORITY,
    ROUTING_ARGV_PREFIX,
    ROUTING_RESULT_KEY,
    ROUTING_SELECTED_BY_DEFAULT,
    ROUTING_SELECTED_BY_KEY,
    ROUTING_SELECTED_BY_LEGACY,
)
from lib.python.core.project_binding import (
    BINDING_ENV,
    SMOKE_ENV,
    TARGET_ENV,
    resolve_target,
)
from lib.python.core.schema import specification_validator
from lib.python.core.target import (
    GITLAB_INSTANCE_ADVICE,
    MISSING_AUTHORITY_TABLES,
    NESTED_TABLE_DIAGNOSTIC,
    GitLabSelection,
    TargetError,
    load_gitlab_target,
    load_target,
)
from test_access_publication_cli import FAKE_ACCESS_TOOLS, TEST_REVISION


@dataclass(frozen=True)
class Instance:
    """Fixture values of one GitLab instance table.

    :param host: Hostname of the instance URL.
    :param project: Project path declared by the table.
    :param token_path: Token file path relative to the target file.
    :param token_text: Token file content.
    :param runner: Runner ID declared by the table.
    """

    host: str
    project: str
    token_path: str
    token_text: str
    runner: int

    @property
    def url(self) -> str:
        """Return the HTTPS origin written to the table.

        :returns: Origin built from the host.
        """
        return f"https://{self.host}"

    def fields(self) -> dict[str, TomlValue]:
        """Compose the instance table fields.

        :returns: New mapping of URL, project, token file and runner ID.
        """
        return {
            "url": self.url,
            "project": self.project,
            "token_file": self.token_path,
            "runner_id": self.runner,
        }


PRIVATE: Final = Instance(
    "private.example.test", "consumer/private", "private.token", "unit-private", 11
)
PUBLIC: Final = Instance(
    "public.example.test",
    "consumer/public",
    "tokens/public.token",
    "unit-public",
    22,
)
ROUTED: Final = GITLAB_ROUTED_AUTHORITIES
LEGACY_PUBLIC: Final = legacy_profile(GITLAB_LEGACY_PUBLIC_KEY)
NESTED_CHILD: Final = "foo"
UNKNOWN_INSTANCE: Final = "unknown_instance"
NO_SELECTION: Final = GitLabSelection()
SELECT_PRIVATE: Final = GitLabSelection(selector=GITLAB_PRIVATE_SELECTOR)
SELECT_PUBLIC: Final = GitLabSelection(selector=GITLAB_PUBLIC_SELECTOR)
SELECT_PROFILE: Final = GitLabSelection(profile=GITLAB_LEGACY_PUBLIC_KEY)
ARGV_PRIVATE: Final = (
    f"{ROUTING_ARGV_PREFIX}{GITLAB_TARGET_OPTION}={GITLAB_PRIVATE_SELECTOR}"
)
ARGV_PUBLIC: Final = (
    f"{ROUTING_ARGV_PREFIX}{GITLAB_TARGET_OPTION}={GITLAB_PUBLIC_SELECTOR}"
)
ARGV_PROFILE: Final = (
    f'{ROUTING_ARGV_PREFIX}{GITLAB_PROFILE_OPTION}="{GITLAB_LEGACY_PUBLIC_KEY}"'
)
TARGET_ENV_SOURCE: Final = f"env:{TARGET_ENV}"
MISSING_ROUTED: Final = MISSING_AUTHORITY_TABLES + ", ".join(sorted(ROUTED))
ACTION_SCHEMA: Final = "results/gitlab-action.schema.json"
WORKFLOW_SCHEMA: Final = "results/toolbox-workflow.schema.json"
TEMPLATE: Final = tomllib.loads(TARGET_TEMPLATE.read_text(encoding="utf-8"))
TEMPLATE_DEFAULT: Final = TEMPLATE[GITLAB_ROUTING_TABLE][GITLAB_ROUTING_DEFAULT_KEY]
PROVIDER_TOOLS: Final = ("gh", "glab", "kubectl", "oc", "helm")
TOKEN_VARIABLE: Final = GITLAB_VARIABLES[0]
OPEN_BUG: Final = ("open-bug", "--project", "team/app", "--title", "t", "--json")
# The fake GitLab project a runner deploy plans against.
DEPLOY_PROJECT: Final = ("--project", "team/app")
# Removes the entry at a path in `edited`.
REMOVED: Final = object()
# Journals every call with the token the caller exported, then answers any
# `glab api` request with an identity on the host it was asked for.
FAKE_PROVIDER: Final = f"""#!{sys.executable}
import json, os, pathlib, sys
tool = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
token = os.environ.get(os.environ["FAKE_TOKEN_VARIABLE"])
with open(os.environ["FAKE_JOURNAL"], "a", encoding="utf-8") as journal:
    journal.write(json.dumps({{"tool": tool, "argv": args, "token": token}}) + "\\n")
if tool == "glab" and args[:1] == ["api"]:
    host = args[args.index("--hostname") + 1]
    print(json.dumps({{"id": 1, "username": "unit", "web_url": "https://" + host + "/unit"}}))
    raise SystemExit(0)
raise SystemExit(98)
"""


def routed_target() -> dict[str, TomlValue]:
    """Compose both instances, each routed to Kubernetes and Harbor.

    :returns: Routing default private, both instance tables, Harbor, Kubernetes.
    """
    return {
        GITLAB_ROUTING_TABLE: routing_table(
            {
                GITLAB_PRIVATE_TABLE: paired(*ROUTED),
                GITLAB_PUBLIC_TABLE: paired(*ROUTED),
            },
            default=GITLAB_PRIVATE_TABLE,
        ),
        GITLAB_PRIVATE_TABLE: PRIVATE.fields(),
        GITLAB_PUBLIC_TABLE: PUBLIC.fields(),
        HARBOR_AUTHORITY: dict(FIXTURE_HARBOR),
        KUBERNETES_AUTHORITY: dict(FIXTURE_KUBERNETES),
    }


def single_target() -> dict[str, TomlValue]:
    """Compose the private instance alone with an empty routing row.

    :returns: Routing default private, the private table, Kubernetes.
    """
    return {
        GITLAB_ROUTING_TABLE: routing_table(
            {GITLAB_PRIVATE_TABLE: paired()}, default=GITLAB_PRIVATE_TABLE
        ),
        GITLAB_PRIVATE_TABLE: PRIVATE.fields(),
        KUBERNETES_AUTHORITY: dict(FIXTURE_KUBERNETES),
    }


def legacy_target() -> dict[str, TomlValue]:
    """Compose the legacy GitLab table holding the private fields.

    :returns: Legacy GitLab table and Kubernetes.
    """
    return {
        GITLAB_AUTHORITY: PRIVATE.fields(),
        KUBERNETES_AUTHORITY: dict(FIXTURE_KUBERNETES),
    }


def issue_target() -> dict[str, TomlValue]:
    """Compose the issue #133 shape: legacy fields and the legacy public profile.

    :returns: Legacy GitLab table and its public-key child table.
    """
    return {GITLAB_AUTHORITY: PRIVATE.fields(), LEGACY_PUBLIC: PUBLIC.fields()}


def public_only_target() -> dict[str, TomlValue]:
    """Compose the public instance alone, named as the routing default.

    :returns: Routing default public, the public table, Kubernetes.
    """
    return {
        GITLAB_ROUTING_TABLE: routing_table(
            {GITLAB_PUBLIC_TABLE: paired()}, default=GITLAB_PUBLIC_TABLE
        ),
        GITLAB_PUBLIC_TABLE: PUBLIC.fields(),
        KUBERNETES_AUTHORITY: dict(FIXTURE_KUBERNETES),
    }


def edited(
    tables: Mapping[str, TomlValue], *changes: tuple[tuple[str, ...], object]
) -> dict[str, TomlValue]:
    """Copy a composed target and set or remove one entry per change.

    :param tables: Composed target entries.
    :param *changes: Key path and new value; ``REMOVED`` deletes the entry.
    :returns: Changed copy; ``tables`` is unchanged.
    """
    result = copy.deepcopy(dict(tables))
    for path, value in changes:
        parent = result
        for key in path[:-1]:
            parent = parent[key]
        if value is REMOVED:
            del parent[path[-1]]
        else:
            parent[path[-1]] = value
    return result


def _routing(
    gitlab: str, kubernetes: Optional[str], harbor: Optional[str], selected: str
) -> dict[str, Optional[str]]:
    """Compose an expected result routing object from fixture values.

    :param gitlab: GitLab table the command read.
    :param kubernetes: Routed Kubernetes table, or None.
    :param harbor: Routed Harbor table, or None.
    :param selected: Selection provenance.
    :returns: Routing object keyed by the contract names.
    """
    return {
        GITLAB_AUTHORITY: gitlab,
        KUBERNETES_AUTHORITY: kubernetes,
        HARBOR_AUTHORITY: harbor,
        ROUTING_SELECTED_BY_KEY: selected,
    }


def _template_routing(instance: str, selected: str) -> dict[str, Optional[str]]:
    """Compose the routing object the shipped template declares for one instance.

    :param instance: Instance table whose template routing row is read.
    :param selected: Selection provenance.
    :returns: Routing object built from the template's own row.
    """
    row = TEMPLATE[GITLAB_ROUTING_TABLE][instance]
    return _routing(
        instance, row.get(KUBERNETES_AUTHORITY), row.get(HARBOR_AUTHORITY), selected
    )


def _selection_kwargs(selection: GitLabSelection) -> dict[str, Optional[str]]:
    """Pass a caller selection to the public loaders.

    :param selection: Caller's instance or legacy profile choice.
    :returns: Loader keyword arguments.
    """
    return {"gitlab": selection.selector, "gitlab_profile": selection.profile}


def _selection_argv(selection: GitLabSelection) -> tuple[str, ...]:
    """Render a caller selection as command options.

    :param selection: Caller's instance or legacy profile choice.
    :returns: Option and value pairs; empty for no selection.
    """
    argv: tuple[str, ...] = ()
    if selection.selector is not None:
        argv += (GITLAB_TARGET_OPTION, selection.selector)
    if selection.profile is not None:
        argv += (GITLAB_PROFILE_OPTION, selection.profile)
    return argv


Loader = Callable[[Path, GitLabSelection], object]
LOADERS: Final[Mapping[str, Loader]] = {
    "load_target": lambda path, selection: (
        load_target(
            path, required_surfaces=(GITLAB_AUTHORITY,), **_selection_kwargs(selection)
        ).gitlab
    ),
    "load_gitlab_target": lambda path, selection: (
        load_gitlab_target(path, **_selection_kwargs(selection)).gitlab
    ),
}


@pytest.fixture
def target_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create both instance token files and isolate ambient target selection.

    :param tmp_path: Directory containing only synthetic target and token files.
    :param monkeypatch: Fixture isolating home, cwd, and target selectors.
    :returns: Path where each test writes its target.
    """
    directory = tmp_path / "configuration"
    for instance in (PRIVATE, PUBLIC):
        token = directory / instance.token_path
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text(instance.token_text, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(home)
    for variable in (TARGET_ENV, BINDING_ENV, SMOKE_ENV):
        monkeypatch.delenv(variable, raising=False)
    return directory / "target.toml"


@pytest.fixture
def template_path(target_path: Path) -> Path:
    """Copy the shipped template, with the files it references, to the target path.

    :param target_path: Isolated target path whose directory receives the copy.
    :returns: Path of the copied template.
    """
    return copy_template_target(target_path.parent)


@pytest.fixture
def providers(fake_bin: Path, call_journal: Path) -> Mapping[str, str]:
    """Install journaling fake provider commands.

    :param fake_bin: Directory prepended to the child PATH.
    :param call_journal: Journal receiving one record per provider call.
    :returns: Child environment naming the journal and the token variable.
    """
    for tool in PROVIDER_TOOLS:
        install_executable(fake_bin, tool, FAKE_PROVIDER)
    return {"FAKE_JOURNAL": str(call_journal), "FAKE_TOKEN_VARIABLE": TOKEN_VARIABLE}


def _hostnames(calls: Sequence[Mapping[str, object]]) -> list[str]:
    """Read the host each journaled ``glab`` call was given.

    :param calls: Journal records of the fake providers.
    :returns: ``--hostname`` value of every ``glab`` call, in call order.
    """
    return [
        call["argv"][call["argv"].index("--hostname") + 1]
        for call in calls
        if call["tool"] == "glab"
    ]


# --- load_target and load_gitlab_target --------------------------------------


@pytest.mark.parametrize("loader", tuple(LOADERS))
@pytest.mark.parametrize(
    ("tables", "selection", "instance", "routing"),
    (
        (
            single_target(),
            NO_SELECTION,
            PRIVATE,
            _routing(GITLAB_PRIVATE_TABLE, None, None, ROUTING_SELECTED_BY_DEFAULT),
        ),
        (
            routed_target(),
            NO_SELECTION,
            PRIVATE,
            _routing(
                GITLAB_PRIVATE_TABLE,
                KUBERNETES_AUTHORITY,
                HARBOR_AUTHORITY,
                ROUTING_SELECTED_BY_DEFAULT,
            ),
        ),
        (
            routed_target(),
            SELECT_PUBLIC,
            PUBLIC,
            _routing(
                GITLAB_PUBLIC_TABLE, KUBERNETES_AUTHORITY, HARBOR_AUTHORITY, ARGV_PUBLIC
            ),
        ),
        (
            routed_target(),
            SELECT_PRIVATE,
            PRIVATE,
            _routing(
                GITLAB_PRIVATE_TABLE,
                KUBERNETES_AUTHORITY,
                HARBOR_AUTHORITY,
                ARGV_PRIVATE,
            ),
        ),
        (
            edited(
                routed_target(),
                (
                    (GITLAB_ROUTING_TABLE, GITLAB_ROUTING_DEFAULT_KEY),
                    GITLAB_PUBLIC_TABLE,
                ),
            ),
            NO_SELECTION,
            PUBLIC,
            _routing(
                GITLAB_PUBLIC_TABLE,
                KUBERNETES_AUTHORITY,
                HARBOR_AUTHORITY,
                ROUTING_SELECTED_BY_DEFAULT,
            ),
        ),
        (
            legacy_target(),
            NO_SELECTION,
            PRIVATE,
            _routing(GITLAB_AUTHORITY, None, None, ROUTING_SELECTED_BY_LEGACY),
        ),
        (
            issue_target(),
            NO_SELECTION,
            PRIVATE,
            _routing(GITLAB_AUTHORITY, None, None, ROUTING_SELECTED_BY_LEGACY),
        ),
        (
            issue_target(),
            SELECT_PROFILE,
            PUBLIC,
            _routing(LEGACY_PUBLIC, None, None, ARGV_PROFILE),
        ),
        (
            legacy_target(),
            SELECT_PRIVATE,
            PRIVATE,
            _routing(GITLAB_AUTHORITY, None, None, ARGV_PRIVATE),
        ),
        (
            issue_target(),
            SELECT_PUBLIC,
            PUBLIC,
            _routing(LEGACY_PUBLIC, None, None, ARGV_PUBLIC),
        ),
        (
            edited(
                routed_target(),
                ((GITLAB_ROUTING_TABLE, GITLAB_ROUTING_DEFAULT_KEY), REMOVED),
            ),
            NO_SELECTION,
            PRIVATE,
            _routing(
                GITLAB_PRIVATE_TABLE,
                KUBERNETES_AUTHORITY,
                HARBOR_AUTHORITY,
                ROUTING_SELECTED_BY_DEFAULT,
            ),
        ),
        (
            {
                GITLAB_PRIVATE_TABLE: PRIVATE.fields(),
                KUBERNETES_AUTHORITY: dict(FIXTURE_KUBERNETES),
            },
            NO_SELECTION,
            PRIVATE,
            _routing(GITLAB_PRIVATE_TABLE, None, None, ROUTING_SELECTED_BY_DEFAULT),
        ),
        (
            {GITLAB_AUTHORITY: PRIVATE.fields(), GITLAB_PUBLIC_TABLE: PUBLIC.fields()},
            NO_SELECTION,
            PRIVATE,
            _routing(GITLAB_AUTHORITY, None, None, ROUTING_SELECTED_BY_LEGACY),
        ),
        (
            {GITLAB_AUTHORITY: PRIVATE.fields(), GITLAB_PUBLIC_TABLE: PUBLIC.fields()},
            SELECT_PUBLIC,
            PUBLIC,
            _routing(GITLAB_PUBLIC_TABLE, None, None, ARGV_PUBLIC),
        ),
        (
            {
                **legacy_target(),
                GITLAB_ROUTING_TABLE: routing_table(
                    {GITLAB_PRIVATE_TABLE: paired()}, default=GITLAB_PRIVATE_TABLE
                ),
            },
            NO_SELECTION,
            PRIVATE,
            _routing(GITLAB_AUTHORITY, None, None, ROUTING_SELECTED_BY_LEGACY),
        ),
        (
            {**routed_target(), LEGACY_PUBLIC: {"url": PUBLIC.url}},
            NO_SELECTION,
            PRIVATE,
            _routing(
                GITLAB_PRIVATE_TABLE,
                KUBERNETES_AUTHORITY,
                HARBOR_AUTHORITY,
                ROUTING_SELECTED_BY_DEFAULT,
            ),
        ),
    ),
    ids=(
        "single-private",
        "private-and-public-default",
        "explicit-public",
        "explicit-private",
        "routing-default-public",
        "legacy-flat",
        "legacy-issue-133-shape",
        "legacy-profile",
        "legacy-flat-is-private",
        "legacy-com-is-public",
        "routing-default-is-optional",
        "routing-table-is-optional",
        "legacy-private-beside-public-instance",
        "public-instance-beside-legacy-private",
        "legacy-private-with-routing-row",
        "legacy-com-beside-instances-is-unread",
    ),
)
def test_selection_reads_one_instance_and_reports_its_route(
    target_path: Path,
    loader: str,
    tables: Mapping[str, TomlValue],
    selection: GitLabSelection,
    instance: Instance,
    routing: Mapping[str, Optional[str]],
) -> None:
    """Select the declared instance and report the table that was read.

    :param target_path: Path receiving the target.
    :param loader: Public loader exercised by the case.
    :param tables: Composed target.
    :param selection: Caller's instance or profile choice, or none.
    :param instance: Fixture values of the table that must be read.
    :param routing: Expected routing object.
    :returns: None; assertions compare the loaded values with the fixture's.
    """
    write_target(target_path, tables)

    selected = LOADERS[loader](target_path, selection)

    assert selected.url == instance.url
    assert selected.host == instance.host
    assert selected.project == instance.project
    assert selected.group is None
    assert selected.runner_id == instance.runner
    assert selected.token_file == (target_path.parent / instance.token_path).resolve()
    assert selected.route.as_dict() == routing


@pytest.mark.parametrize("loader", tuple(LOADERS))
@pytest.mark.parametrize(
    ("tables", "selection", "reason"),
    (
        (
            single_target(),
            SELECT_PUBLIC,
            GITLAB_INSTANCE_REQUIRED.format(table=GITLAB_PUBLIC_TABLE),
        ),
        (
            public_only_target(),
            SELECT_PRIVATE,
            GITLAB_INSTANCE_REQUIRED.format(table=GITLAB_PRIVATE_TABLE),
        ),
        (
            legacy_target(),
            SELECT_PUBLIC,
            GITLAB_INSTANCE_REQUIRED.format(table=GITLAB_PUBLIC_TABLE),
        ),
        (
            {
                GITLAB_PUBLIC_TABLE: PUBLIC.fields(),
                KUBERNETES_AUTHORITY: dict(FIXTURE_KUBERNETES),
            },
            NO_SELECTION,
            GITLAB_INSTANCE_REQUIRED.format(table=GITLAB_DEFAULT_INSTANCE),
        ),
        (
            edited(
                public_only_target(),
                ((GITLAB_ROUTING_TABLE, GITLAB_ROUTING_DEFAULT_KEY), REMOVED),
            ),
            NO_SELECTION,
            GITLAB_INSTANCE_REQUIRED.format(table=GITLAB_DEFAULT_INSTANCE),
        ),
        (
            edited(
                routed_target(),
                ((GITLAB_ROUTING_TABLE, GITLAB_ROUTING_DEFAULT_KEY), UNKNOWN_INSTANCE),
            ),
            NO_SELECTION,
            GITLAB_ROUTING_DEFAULT_UNKNOWN,
        ),
        (
            edited(
                single_target(),
                (
                    (GITLAB_ROUTING_TABLE, GITLAB_ROUTING_DEFAULT_KEY),
                    GITLAB_PUBLIC_TABLE,
                ),
            ),
            NO_SELECTION,
            GITLAB_ROUTING_DEFAULT_UNKNOWN,
        ),
        (
            edited(
                routed_target(), ((GITLAB_ROUTING_TABLE, GITLAB_PUBLIC_TABLE), REMOVED)
            ),
            NO_SELECTION,
            GITLAB_ROUTING_ROW_MISSING,
        ),
        (
            edited(single_target(), ((GITLAB_ROUTING_TABLE, GITLAB_PUBLIC_TABLE), {})),
            NO_SELECTION,
            GITLAB_ROUTING_ROW_UNKNOWN,
        ),
        (
            edited(single_target(), ((GITLAB_ROUTING_TABLE, UNKNOWN_INSTANCE), {})),
            NO_SELECTION,
            GITLAB_ROUTING_ROW_UNKNOWN,
        ),
        (
            edited(
                routed_target(),
                (
                    (GITLAB_ROUTING_TABLE, GITLAB_PRIVATE_TABLE, KUBERNETES_AUTHORITY),
                    UNKNOWN_INSTANCE,
                ),
            ),
            NO_SELECTION,
            GITLAB_ROUTING_PAIRING_UNKNOWN.format(
                routing=GITLAB_ROUTING_TABLE, authority=KUBERNETES_AUTHORITY
            ),
        ),
        (
            edited(routed_target(), ((KUBERNETES_AUTHORITY,), REMOVED)),
            NO_SELECTION,
            GITLAB_ROUTING_PAIRING_UNKNOWN.format(
                routing=GITLAB_ROUTING_TABLE, authority=KUBERNETES_AUTHORITY
            ),
        ),
        (
            edited(routed_target(), ((HARBOR_AUTHORITY,), REMOVED)),
            NO_SELECTION,
            GITLAB_ROUTING_PAIRING_UNKNOWN.format(
                routing=GITLAB_ROUTING_TABLE, authority=HARBOR_AUTHORITY
            ),
        ),
        (
            edited(
                single_target(),
                (
                    (GITLAB_ROUTING_TABLE, GITLAB_PRIVATE_TABLE),
                    {"registry": HARBOR_AUTHORITY},
                ),
            ),
            NO_SELECTION,
            (
                f"{GITLAB_ROUTING_TABLE}.{GITLAB_PRIVATE_TABLE} contains unsupported "
                "fields: registry"
            ),
        ),
        (
            edited(
                single_target(),
                ((GITLAB_ROUTING_TABLE, GITLAB_PRIVATE_TABLE), KUBERNETES_AUTHORITY),
            ),
            NO_SELECTION,
            f"{GITLAB_ROUTING_TABLE}.{GITLAB_PRIVATE_TABLE} must be a TOML table",
        ),
        (
            {GITLAB_AUTHORITY: PRIVATE.fields(), **routed_target()},
            NO_SELECTION,
            GITLAB_PRIVATE_DECLARED_TWICE,
        ),
        (
            issue_target(),
            GitLabSelection(GITLAB_PUBLIC_SELECTOR, GITLAB_LEGACY_PUBLIC_KEY),
            GITLAB_SELECTOR_CONFLICT,
        ),
        (
            routed_target(),
            GitLabSelection(GITLAB_PUBLIC_SELECTOR, GITLAB_LEGACY_PUBLIC_KEY),
            GITLAB_SELECTOR_CONFLICT,
        ),
        (routed_target(), SELECT_PROFILE, GITLAB_PROFILE_UNKNOWN),
        (
            issue_target(),
            GitLabSelection(profile=UNKNOWN_INSTANCE),
            GITLAB_PROFILE_UNKNOWN,
        ),
        (
            edited(
                routed_target(),
                (
                    (GITLAB_ROUTING_TABLE, GITLAB_PRIVATE_TABLE, HARBOR_AUTHORITY),
                    KUBERNETES_AUTHORITY,
                ),
            ),
            NO_SELECTION,
            GITLAB_ROUTING_PAIRING_UNKNOWN.format(
                routing=GITLAB_ROUTING_TABLE, authority=HARBOR_AUTHORITY
            ),
        ),
        (
            edited(
                routed_target(),
                (
                    (GITLAB_ROUTING_TABLE, GITLAB_PRIVATE_TABLE, KUBERNETES_AUTHORITY),
                    5,
                ),
            ),
            NO_SELECTION,
            GITLAB_ROUTING_PAIRING_UNKNOWN.format(
                routing=GITLAB_ROUTING_TABLE, authority=KUBERNETES_AUTHORITY
            ),
        ),
        (
            edited(routed_target(), ((GITLAB_ROUTING_TABLE,), UNKNOWN_INSTANCE)),
            NO_SELECTION,
            f"{GITLAB_ROUTING_TABLE} must be a TOML table",
        ),
        (
            routed_target(),
            GitLabSelection(selector=UNKNOWN_INSTANCE),
            GITLAB_SELECTOR_UNKNOWN,
        ),
        (
            {
                GITLAB_ROUTING_TABLE: routing_table(
                    {GITLAB_PRIVATE_TABLE: paired()}, default=GITLAB_PRIVATE_TABLE
                ),
                KUBERNETES_AUTHORITY: dict(FIXTURE_KUBERNETES),
            },
            NO_SELECTION,
            MISSING_AUTHORITY_TABLES + GITLAB_AUTHORITY,
        ),
        (
            {
                **routed_target(),
                f"{GITLAB_PUBLIC_TABLE}.{NESTED_CHILD}": {"url": PUBLIC.url},
            },
            SELECT_PUBLIC,
            NESTED_TABLE_DIAGNOSTIC.format(
                path=f"{GITLAB_PUBLIC_TABLE}.{NESTED_CHILD}",
                child=NESTED_CHILD,
                parent=GITLAB_PUBLIC_TABLE,
                advice=GITLAB_INSTANCE_ADVICE,
            ),
        ),
        (
            {
                GITLAB_PRIVATE_TABLE: PRIVATE.fields(),
                f"{GITLAB_PUBLIC_TABLE}.{NESTED_CHILD}": {"url": PUBLIC.url},
            },
            SELECT_PUBLIC,
            NESTED_TABLE_DIAGNOSTIC.format(
                path=f"{GITLAB_PUBLIC_TABLE}.{NESTED_CHILD}",
                child=NESTED_CHILD,
                parent=GITLAB_PUBLIC_TABLE,
                advice=GITLAB_INSTANCE_ADVICE,
            ),
        ),
    ),
    ids=(
        "public-not-declared",
        "private-not-declared",
        "legacy-has-no-public",
        "public-only-without-routing",
        "public-only-routing-without-default",
        "default-names-unknown-table",
        "default-names-undeclared-instance",
        "declared-instance-without-row",
        "row-for-undeclared-instance",
        "row-for-unknown-instance",
        "row-names-unknown-kubernetes",
        "row-names-absent-kubernetes",
        "row-names-absent-harbor",
        "row-has-unknown-field",
        "row-is-not-table",
        "legacy-and-private-declare-private-twice",
        "both-selectors-legacy",
        "both-selectors-routed",
        "profile-on-routed-target",
        "unknown-legacy-profile",
        "row-harbor-names-kubernetes-table",
        "row-kubernetes-not-a-string",
        "routing-is-not-a-table",
        "library-selector-outside-contract",
        "routing-without-instance-table",
        "dotted-header-under-instance",
        "selected-instance-holds-only-a-nested-table",
    ),
)
def test_invalid_selection_or_routing_fails_closed_with_exact_reason(
    target_path: Path,
    loader: str,
    tables: Mapping[str, TomlValue],
    selection: GitLabSelection,
    reason: str,
) -> None:
    """Reject one broken selection or routing invariant without a fallback.

    :param target_path: Path receiving the invalid target.
    :param loader: Public loader whose fail-closed selection is checked.
    :param tables: Composed target breaking exactly one invariant.
    :param selection: Caller's instance or profile choice, or none.
    :param reason: Exact diagnostic expected by the calling agent.
    :returns: None; assertions verify the reason and the unchanged input.
    """
    body = write_target(target_path, tables).read_text(encoding="utf-8")

    with pytest.raises(TargetError) as error:
        LOADERS[loader](target_path, selection)

    assert str(error.value) == reason
    assert target_path.read_text(encoding="utf-8") == body


@pytest.mark.parametrize("loader", tuple(LOADERS))
@pytest.mark.parametrize(
    ("tables", "selection", "message"),
    (
        (
            edited(routed_target(), ((GITLAB_PUBLIC_TABLE, "url"), REMOVED)),
            SELECT_PUBLIC,
            f"{GITLAB_PUBLIC_TABLE}.url must be a nonempty string",
        ),
        (
            edited(
                routed_target(),
                ((GITLAB_PUBLIC_TABLE, "url"), f"{PUBLIC.url}/consumer"),
            ),
            SELECT_PUBLIC,
            f"{GITLAB_PUBLIC_TABLE}.url must be an HTTPS origin with a full hostname",
        ),
        (
            edited(
                routed_target(),
                ((GITLAB_PUBLIC_TABLE, "project"), f"/{PUBLIC.project}"),
            ),
            SELECT_PUBLIC,
            f"{GITLAB_PUBLIC_TABLE}.project must be a relative path or numeric ID",
        ),
        (
            edited(routed_target(), ((GITLAB_PUBLIC_TABLE, "project"), "")),
            SELECT_PUBLIC,
            f"{GITLAB_PUBLIC_TABLE}.project must be a relative path or numeric ID",
        ),
        (
            edited(routed_target(), ((GITLAB_PRIVATE_TABLE, "project"), "")),
            NO_SELECTION,
            f"{GITLAB_PRIVATE_TABLE}.project must be a relative path or numeric ID",
        ),
        (
            edited(routed_target(), ((GITLAB_PUBLIC_TABLE, "token_file"), "")),
            SELECT_PUBLIC,
            f"{GITLAB_PUBLIC_TABLE}.token_file must be a nonempty path when supplied",
        ),
        (
            edited(routed_target(), ((GITLAB_PUBLIC_TABLE, "runner_id"), 0)),
            SELECT_PUBLIC,
            f"{GITLAB_PUBLIC_TABLE}.runner_id must be a positive numeric ID",
        ),
        (
            edited(routed_target(), ((GITLAB_PUBLIC_TABLE, "unknown"), "fixture")),
            SELECT_PUBLIC,
            f"{GITLAB_PUBLIC_TABLE} contains unsupported fields: unknown",
        ),
        (
            edited(routed_target(), ((GITLAB_PRIVATE_TABLE, "url"), REMOVED)),
            NO_SELECTION,
            f"{GITLAB_PRIVATE_TABLE}.url must be a nonempty string",
        ),
        (
            edited(legacy_target(), ((GITLAB_AUTHORITY, "url"), REMOVED)),
            NO_SELECTION,
            f"{GITLAB_AUTHORITY}.url must be a nonempty string",
        ),
        (
            edited(issue_target(), ((LEGACY_PUBLIC, "url"), REMOVED)),
            SELECT_PROFILE,
            f"{LEGACY_PUBLIC}.url must be a nonempty string",
        ),
    ),
    ids=(
        "public-url-missing",
        "public-url-not-origin",
        "public-project-absolute",
        "public-project-empty",
        "private-project-empty",
        "public-token-path-empty",
        "public-runner-zero",
        "public-unknown-field",
        "private-url-missing",
        "legacy-url-missing",
        "legacy-profile-url-missing",
    ),
)
def test_field_errors_name_the_table_read_and_never_the_credential(
    target_path: Path,
    loader: str,
    tables: Mapping[str, TomlValue],
    selection: GitLabSelection,
    message: str,
) -> None:
    """Qualify a field error with the exact table the loader read.

    :param target_path: Path receiving the invalid target.
    :param loader: Public loader whose diagnostic is checked.
    :param tables: Composed target with one invalid field.
    :param selection: Caller's instance or profile choice, or none.
    :param message: Exact diagnostic expected by the calling agent.
    :returns: None; assertions verify the text and the absence of credentials.
    """
    write_target(target_path, tables)

    with pytest.raises(TargetError) as error:
        LOADERS[loader](target_path, selection)

    text = str(error.value)
    assert text == message
    for secret in (PRIVATE.token_text, PUBLIC.token_text, str(target_path.parent)):
        assert secret not in text


@pytest.mark.parametrize("loader", tuple(LOADERS))
@pytest.mark.parametrize(
    ("tables", "selection", "instance"),
    (
        (
            edited(routed_target(), ((GITLAB_PRIVATE_TABLE, "token_file"), "")),
            SELECT_PUBLIC,
            PUBLIC,
        ),
        (
            edited(routed_target(), ((GITLAB_PUBLIC_TABLE, "url"), REMOVED)),
            NO_SELECTION,
            PRIVATE,
        ),
        (
            {
                GITLAB_PRIVATE_TABLE: PRIVATE.fields(),
                f"{GITLAB_PUBLIC_TABLE}.{NESTED_CHILD}": {"url": PUBLIC.url},
            },
            NO_SELECTION,
            PRIVATE,
        ),
    ),
    ids=(
        "invalid-private-while-public",
        "invalid-public-while-default",
        "nested-table-in-unselected-public",
    ),
)
def test_unselected_instance_is_not_validated(
    target_path: Path,
    loader: str,
    tables: Mapping[str, TomlValue],
    selection: GitLabSelection,
    instance: Instance,
) -> None:
    """Validate only the instance the caller selected.

    :param target_path: Path receiving the target.
    :param loader: Public loader exercised by the case.
    :param tables: Composed target whose other instance is invalid.
    :param selection: Caller's instance choice, or none.
    :param instance: Fixture values of the instance that must load.
    :returns: None; assertions verify the selected instance loads.
    """
    write_target(target_path, tables)

    assert LOADERS[loader](target_path, selection).host == instance.host


@pytest.mark.parametrize("loader", tuple(LOADERS))
def test_selected_instance_does_not_inherit_the_other_instance(
    target_path: Path, loader: str
) -> None:
    """Keep optional fields absent when only the other instance declares them.

    :param target_path: Path receiving a public table holding only its URL.
    :param loader: Public loader selecting the public instance.
    :returns: None; assertions verify that absent fields stay absent.
    """
    write_target(
        target_path,
        edited(routed_target(), ((GITLAB_PUBLIC_TABLE,), {"url": PUBLIC.url})),
    )

    selected = LOADERS[loader](target_path, SELECT_PUBLIC)

    assert selected.host == PUBLIC.host
    assert (selected.project, selected.group, selected.runner_id) == (None, None, None)
    assert selected.token_file is None


def test_selector_for_a_command_without_gitlab_is_rejected(target_path: Path) -> None:
    """Refuse a GitLab selector when the command does not select GitLab.

    :param target_path: Path receiving the routed target.
    :returns: None; assertions verify the fail-closed reason.
    """
    write_target(target_path, routed_target())

    with pytest.raises(TargetError) as error:
        load_target(
            target_path,
            required_surfaces=(KUBERNETES_AUTHORITY,),
            **_selection_kwargs(SELECT_PUBLIC),
        )

    assert str(error.value) == GITLAB_SELECTION_REQUIRES_GITLAB


@pytest.mark.parametrize("selection", ("explicit", "environment", "binding"))
def test_selection_preserves_target_and_binding_precedence(
    target_path: Path, selection: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Carry the selector through the chosen file without another-tier fallback.

    :param target_path: Path receiving the routed target.
    :param selection: Explicit target, environment target, or binding tier.
    :param monkeypatch: Fixture setting competing environment selectors.
    :returns: None; assertions verify file selection and the public instance.
    """
    write_target(target_path, routed_target())
    explicit = None
    binding = None
    source = "argv:--target"
    if selection == "explicit":
        explicit = str(target_path)
        monkeypatch.setenv(TARGET_ENV, str(target_path.parent / "missing"))
    elif selection == "environment":
        source = TARGET_ENV_SOURCE
        monkeypatch.setenv(TARGET_ENV, str(target_path))
    else:
        binding = str(
            write_target(
                target_path.parent / "binding.toml",
                {
                    "schema_version": "1.0",
                    "target": target_path.name,
                    KUBERNETES_AUTHORITY: {
                        "sources": [{"kind": "file", "path": "missing-kubeconfig"}]
                    },
                },
            )
        )
        source = "binding"
        monkeypatch.setenv(TARGET_ENV, str(target_path.parent / "missing"))

    selected = resolve_target(
        explicit,
        binding,
        required_surfaces=(GITLAB_AUTHORITY,),
        gitlab_selection=SELECT_PUBLIC,
    )

    assert selected.source_file == target_path.resolve()
    assert selected.source_kind == source
    assert selected.gitlab.host == PUBLIC.host
    assert selected.gitlab.project == PUBLIC.project
    assert (
        selected.gitlab.token_file == (target_path.parent / PUBLIC.token_path).resolve()
    )


# --- authorities a combined command reads through the selected route ---------


@pytest.mark.parametrize(
    ("selection", "required", "optional", "reason", "routing"),
    (
        (
            SELECT_PUBLIC,
            (GITLAB_AUTHORITY, KUBERNETES_AUTHORITY, HARBOR_AUTHORITY),
            (),
            MISSING_ROUTED,
            None,
        ),
        (
            SELECT_PUBLIC,
            (GITLAB_AUTHORITY, KUBERNETES_AUTHORITY),
            (),
            MISSING_AUTHORITY_TABLES + KUBERNETES_AUTHORITY,
            None,
        ),
        (
            SELECT_PUBLIC,
            (GITLAB_AUTHORITY,),
            (KUBERNETES_AUTHORITY, HARBOR_AUTHORITY),
            None,
            _routing(GITLAB_PUBLIC_TABLE, None, None, ARGV_PUBLIC),
        ),
        (
            SELECT_PRIVATE,
            (GITLAB_AUTHORITY, KUBERNETES_AUTHORITY, HARBOR_AUTHORITY),
            (),
            None,
            _routing(
                GITLAB_PRIVATE_TABLE,
                KUBERNETES_AUTHORITY,
                HARBOR_AUTHORITY,
                ARGV_PRIVATE,
            ),
        ),
    ),
    ids=(
        "public-runner-deploy-surfaces",
        "public-runner-delete-surfaces",
        "public-optional-surfaces",
        "private-runner-deploy-surfaces",
    ),
)
def test_required_routed_authority_reads_the_route_pairing(
    target_path: Path,
    selection: GitLabSelection,
    required: tuple[str, ...],
    optional: tuple[str, ...],
    reason: Optional[str],
    routing: Optional[Mapping[str, Optional[str]]],
) -> None:
    """Read a required Kubernetes or Harbor table only through the selected row.

    The public row pairs nothing and the private row pairs both. An optional
    authority reads its own table whatever the row says.

    :param target_path: Path receiving the routed target.
    :param selection: Caller's instance choice.
    :param required: Authorities the command requires.
    :param optional: Authorities read only when configured.
    :param reason: Exact diagnostic, or None when the load succeeds.
    :param routing: Expected routing object when the load succeeds.
    :returns: None; assertions compare loaded tables with the fixture's.
    """
    write_target(
        target_path,
        edited(routed_target(), ((GITLAB_ROUTING_TABLE, GITLAB_PUBLIC_TABLE), {})),
    )

    def load() -> object:
        """Load the target with the case's authorities and selection.

        :returns: Loaded target.
        """
        return load_target(
            target_path,
            required_surfaces=required,
            optional_surfaces=optional,
            **_selection_kwargs(selection),
        )

    if reason is not None:
        with pytest.raises(TargetError) as error:
            load()
        assert str(error.value) == reason
        return
    target = load()
    assert target.routing.as_dict() == routing
    assert target.kubernetes.context == FIXTURE_KUBERNETES["context"]
    assert target.kubernetes.server == FIXTURE_KUBERNETES["server"]
    assert target.harbor.url == FIXTURE_HARBOR["url"]
    assert target.harbor.project == FIXTURE_HARBOR["project"]


@pytest.mark.parametrize(
    ("tables", "selection", "reason", "routing"),
    (
        (
            {
                **issue_target(),
                HARBOR_AUTHORITY: dict(FIXTURE_HARBOR),
                KUBERNETES_AUTHORITY: dict(FIXTURE_KUBERNETES),
            },
            NO_SELECTION,
            None,
            _routing(GITLAB_AUTHORITY, None, None, ROUTING_SELECTED_BY_LEGACY),
        ),
        (
            {
                **issue_target(),
                HARBOR_AUTHORITY: dict(FIXTURE_HARBOR),
                KUBERNETES_AUTHORITY: dict(FIXTURE_KUBERNETES),
            },
            SELECT_PROFILE,
            None,
            _routing(LEGACY_PUBLIC, None, None, ARGV_PROFILE),
        ),
        (
            {
                **issue_target(),
                HARBOR_AUTHORITY: dict(FIXTURE_HARBOR),
                KUBERNETES_AUTHORITY: dict(FIXTURE_KUBERNETES),
            },
            SELECT_PRIVATE,
            MISSING_ROUTED,
            None,
        ),
        (
            {
                **issue_target(),
                HARBOR_AUTHORITY: dict(FIXTURE_HARBOR),
                KUBERNETES_AUTHORITY: dict(FIXTURE_KUBERNETES),
            },
            SELECT_PUBLIC,
            MISSING_ROUTED,
            None,
        ),
        (
            {
                **issue_target(),
                GITLAB_ROUTING_TABLE: routing_table(
                    {
                        GITLAB_PRIVATE_TABLE: paired(),
                        GITLAB_PUBLIC_TABLE: paired(*ROUTED),
                    }
                ),
                HARBOR_AUTHORITY: dict(FIXTURE_HARBOR),
                KUBERNETES_AUTHORITY: dict(FIXTURE_KUBERNETES),
            },
            SELECT_PROFILE,
            None,
            _routing(
                LEGACY_PUBLIC, KUBERNETES_AUTHORITY, HARBOR_AUTHORITY, ARGV_PROFILE
            ),
        ),
        (
            {**routed_target(), legacy_profile(UNKNOWN_INSTANCE): PUBLIC.fields()},
            GitLabSelection(profile=UNKNOWN_INSTANCE),
            MISSING_ROUTED,
            None,
        ),
        (
            {**routed_target(), LEGACY_PUBLIC: PUBLIC.fields()},
            SELECT_PROFILE,
            MISSING_ROUTED,
            None,
        ),
        (
            {
                GITLAB_PRIVATE_TABLE: PRIVATE.fields(),
                HARBOR_AUTHORITY: dict(FIXTURE_HARBOR),
                KUBERNETES_AUTHORITY: dict(FIXTURE_KUBERNETES),
            },
            NO_SELECTION,
            MISSING_ROUTED,
            None,
        ),
        (
            {
                **legacy_target(),
                HARBOR_AUTHORITY: dict(FIXTURE_HARBOR),
                GITLAB_ROUTING_TABLE: routing_table(
                    {GITLAB_PRIVATE_TABLE: paired(KUBERNETES_AUTHORITY)}
                ),
            },
            NO_SELECTION,
            MISSING_AUTHORITY_TABLES + HARBOR_AUTHORITY,
            None,
        ),
    ),
    ids=(
        "legacy-unrouted-no-selector-reads-tables",
        "legacy-unrouted-profile-reads-tables",
        "legacy-unrouted-private-selector-blocks",
        "legacy-unrouted-public-selector-blocks",
        "legacy-profile-reads-its-routing-row",
        "routed-target-profile-answering-no-instance",
        "routed-target-legacy-com-beside-public-instance",
        "instance-without-routing",
        "legacy-row-omits-harbor",
    ),
)
def test_combined_command_reads_the_routed_tables_or_blocks(
    target_path: Path,
    tables: Mapping[str, TomlValue],
    selection: GitLabSelection,
    reason: Optional[str],
    routing: Optional[Mapping[str, Optional[str]]],
) -> None:
    """Load GitLab, Kubernetes and Harbor as a runner deploy requires them.

    A legacy target without a routing table reads Kubernetes and Harbor directly
    only when no ``--gitlab`` selector was given. A legacy profile reads the
    row of the instance it answers; a profile that answers no instance pairs
    nothing.

    :param target_path: Path receiving the target.
    :param tables: Composed target with GitLab, Kubernetes and Harbor tables.
    :param selection: Caller's instance or profile choice, or none.
    :param reason: Exact diagnostic, or None when the target loads.
    :param routing: Expected routing object when the target loads.
    :returns: None; assertions compare loaded tables with the fixture's.
    """
    write_target(target_path, tables)

    def load() -> object:
        """Load the target as a combined command does.

        :returns: Loaded target.
        """
        return load_target(
            target_path,
            required_surfaces=(
                GITLAB_AUTHORITY,
                KUBERNETES_AUTHORITY,
                HARBOR_AUTHORITY,
            ),
            **_selection_kwargs(selection),
        )

    if reason is not None:
        with pytest.raises(TargetError) as error:
            load()
        assert str(error.value) == reason
        return
    target = load()
    assert target.routing.as_dict() == routing
    assert target.kubernetes.context == FIXTURE_KUBERNETES["context"]
    assert target.harbor.url == FIXTURE_HARBOR["url"]


# --- commands: what the fake providers received ------------------------------


@pytest.mark.parametrize("operation", ("check", "get"))
@pytest.mark.parametrize(
    ("tables", "selection", "instance", "deprecations"),
    (
        (routed_target(), NO_SELECTION, PRIVATE, 0),
        (routed_target(), SELECT_PUBLIC, PUBLIC, 0),
        (routed_target(), SELECT_PRIVATE, PRIVATE, 0),
        (issue_target(), NO_SELECTION, PRIVATE, 1),
        (issue_target(), SELECT_PUBLIC, PUBLIC, 1),
        (issue_target(), SELECT_PROFILE, PUBLIC, 1),
    ),
    ids=(
        "routed-default",
        "routed-public",
        "routed-private",
        "issue-133-default",
        "issue-133-public-selector",
        "issue-133-profile",
    ),
)
def test_api_command_sends_the_selected_host_and_token(
    target_path: Path,
    providers: Mapping[str, str],
    fake_bin: Path,
    call_journal: Path,
    operation: str,
    tables: Mapping[str, TomlValue],
    selection: GitLabSelection,
    instance: Instance,
    deprecations: int,
) -> None:
    """Call ``glab`` on the selected instance's host with that instance's token.

    :param target_path: Path receiving the target named by the environment.
    :param providers: Child environment of the journaling fake providers.
    :param fake_bin: Directory holding the fake providers.
    :param call_journal: Journal of provider calls.
    :param operation: Identity check or provider-relative GET.
    :param tables: Composed target.
    :param selection: Caller's instance or profile choice, or none.
    :param instance: Fixture values of the instance the call must reach.
    :param deprecations: Expected deprecation events on stderr.
    :returns: None; assertions compare the received host and token with the fixture.
    """
    write_target(target_path, tables)
    endpoint = ("--endpoint", "projects/1") if operation == "get" else ()

    result = run_script(
        "ci-api",
        operation,
        "--provider",
        GITLAB_AUTHORITY,
        *endpoint,
        *_selection_argv(selection),
        fake_bin=fake_bin,
        env={**providers, TARGET_ENV: str(target_path)},
    )
    calls = read_journal(call_journal)

    assert result.returncode == 0, result.stderr
    assert calls
    assert _hostnames(calls) == [instance.host] * len(calls)
    assert {call["token"] for call in calls} == {instance.token_text}
    assert result.stderr.count(GITLAB_DEPRECATION_EVENT) == deprecations


def test_api_command_on_the_template_reads_the_public_instance(
    template_path: Path,
    providers: Mapping[str, str],
    fake_bin: Path,
    call_journal: Path,
) -> None:
    """Reach the shipped template's public URL with the token file it names.

    :param template_path: Copy of the shipped template beside its files.
    :param providers: Child environment of the journaling fake providers.
    :param fake_bin: Directory holding the fake providers.
    :param call_journal: Journal of provider calls.
    :returns: None; assertions compare the call with the template's own values.
    """
    target = template_path
    public = TEMPLATE[GITLAB_PUBLIC_TABLE]
    token = (template_path.parent / public["token_file"]).read_text(encoding="utf-8")

    result = run_script(
        "ci-api",
        "check",
        "--provider",
        GITLAB_AUTHORITY,
        *_selection_argv(SELECT_PUBLIC),
        fake_bin=fake_bin,
        env={**providers, TARGET_ENV: str(target)},
    )
    calls = read_journal(call_journal)

    assert result.returncode == 0, result.stderr
    assert calls
    assert _hostnames(calls) == [urlsplit(public["url"]).netloc] * len(calls)
    assert {call["token"] for call in calls} == {token}
    assert GITLAB_DEPRECATION_EVENT not in result.stderr


@pytest.mark.parametrize(
    "argv",
    (
        ("--provider", GITHUB_AUTHORITY, *_selection_argv(SELECT_PUBLIC)),
        (
            "--provider",
            GITLAB_AUTHORITY,
            *_selection_argv(SELECT_PUBLIC),
            "--host",
            "other.example.test",
        ),
    ),
    ids=("github-provider", "conflicting-host"),
)
def test_api_selection_conflict_rejects_before_any_provider_call(
    target_path: Path,
    providers: Mapping[str, str],
    fake_bin: Path,
    call_journal: Path,
    argv: tuple[str, ...],
) -> None:
    """Refuse an invalid provider or host for the selected instance.

    The command's reasons for these two refusals are not contract constants,
    so the test asserts the refusal and the provider boundary only.

    :param target_path: Path receiving the routed target with GitHub.
    :param providers: Child environment of the journaling fake providers.
    :param fake_bin: Directory holding the fake providers.
    :param call_journal: Journal that must stay empty.
    :param argv: Provider, selector and host options of the case.
    :returns: None; assertions verify the refusal and zero provider calls.
    """
    write_target(
        target_path, {GITHUB_AUTHORITY: dict(FIXTURE_GITHUB), **routed_target()}
    )

    result = run_script(
        "ci-api",
        "check",
        *argv,
        fake_bin=fake_bin,
        env={**providers, TARGET_ENV: str(target_path)},
    )

    assert result.returncode != 0
    assert read_journal(call_journal) == []


@pytest.mark.parametrize(
    ("tables", "selection", "instance", "routing", "source", "deprecations"),
    (
        (
            routed_target(),
            NO_SELECTION,
            PRIVATE,
            _routing(
                GITLAB_PRIVATE_TABLE,
                KUBERNETES_AUTHORITY,
                HARBOR_AUTHORITY,
                ROUTING_SELECTED_BY_DEFAULT,
            ),
            f"{TARGET_ENV_SOURCE};argv:--project",
            0,
        ),
        (
            routed_target(),
            SELECT_PUBLIC,
            PUBLIC,
            _routing(
                GITLAB_PUBLIC_TABLE, KUBERNETES_AUTHORITY, HARBOR_AUTHORITY, ARGV_PUBLIC
            ),
            f"{TARGET_ENV_SOURCE};{ARGV_PUBLIC};argv:--project",
            0,
        ),
        (
            issue_target(),
            NO_SELECTION,
            PRIVATE,
            _routing(GITLAB_AUTHORITY, None, None, ROUTING_SELECTED_BY_LEGACY),
            f"{TARGET_ENV_SOURCE};argv:--project",
            1,
        ),
        (
            issue_target(),
            SELECT_PROFILE,
            PUBLIC,
            _routing(LEGACY_PUBLIC, None, None, ARGV_PROFILE),
            f"{TARGET_ENV_SOURCE};{ARGV_PROFILE};argv:--project",
            1,
        ),
        (
            issue_target(),
            SELECT_PUBLIC,
            PUBLIC,
            _routing(LEGACY_PUBLIC, None, None, ARGV_PUBLIC),
            f"{TARGET_ENV_SOURCE};{ARGV_PUBLIC};argv:--project",
            1,
        ),
    ),
    ids=(
        "routed-default",
        "routed-public",
        "legacy-issue-133",
        "legacy-profile",
        "legacy-issue-133-public-selector",
    ),
)
def test_open_bug_dry_run_reports_origin_and_routing(
    target_path: Path,
    providers: Mapping[str, str],
    fake_bin: Path,
    call_journal: Path,
    tables: Mapping[str, TomlValue],
    selection: GitLabSelection,
    instance: Instance,
    routing: Mapping[str, Optional[str]],
    source: str,
    deprecations: int,
) -> None:
    """Run the issue #133 command offline and read its result.

    :param target_path: Path receiving the target named by the environment.
    :param providers: Child environment of the journaling fake providers.
    :param fake_bin: Directory holding the fake providers.
    :param call_journal: Journal that must stay empty.
    :param tables: Composed target.
    :param selection: Caller's instance or profile choice, or none.
    :param instance: Fixture values of the instance the plan must name.
    :param routing: Expected routing object.
    :param source: Expected selection provenance.
    :param deprecations: Expected deprecation events on stderr.
    :returns: None; assertions verify the DRY_RUN result and its schema.
    """
    write_target(target_path, tables)

    result = run_script(
        "gitlab_issue.py",
        *OPEN_BUG,
        *_selection_argv(selection),
        fake_bin=fake_bin,
        env={**providers, TARGET_ENV: str(target_path)},
    )
    data = parse_json_output(result)

    assert result.returncode == 0, result.stderr
    assert data["status"] == "DRY_RUN"
    assert data["schema_version"] == ROUTED_RESULT_SCHEMA_VERSION
    assert data["origin"] == instance.url
    assert data[ROUTING_RESULT_KEY] == routing
    assert data["target_source"] == source
    assert result.stderr.count(GITLAB_DEPRECATION_EVENT) == deprecations
    for secret in (PRIVATE.token_text, PUBLIC.token_text):
        assert secret not in result.stdout + result.stderr
    specification_validator(ACTION_SCHEMA).validate(data)
    assert read_journal(call_journal) == []


def test_open_bug_on_the_template_reads_the_routing_default(
    template_path: Path,
    providers: Mapping[str, str],
    fake_bin: Path,
    call_journal: Path,
) -> None:
    """Plan against the shipped template's default instance.

    :param template_path: Copy of the shipped template beside its files.
    :param providers: Child environment of the journaling fake providers.
    :param fake_bin: Directory holding the fake providers.
    :param call_journal: Journal that must stay empty.
    :returns: None; assertions compare the plan with the template's own values.
    """
    target = template_path

    result = run_script(
        "gitlab_issue.py",
        *OPEN_BUG,
        fake_bin=fake_bin,
        env={**providers, TARGET_ENV: str(target)},
    )
    data = parse_json_output(result)

    assert result.returncode == 0, result.stderr
    assert data["status"] == "DRY_RUN"
    assert data["origin"] == TEMPLATE[TEMPLATE_DEFAULT]["url"]
    assert data[ROUTING_RESULT_KEY] == _template_routing(
        TEMPLATE_DEFAULT, ROUTING_SELECTED_BY_DEFAULT
    )
    specification_validator(ACTION_SCHEMA).validate(data)
    assert read_journal(call_journal) == []


@pytest.mark.parametrize(
    ("tables", "selection", "reason"),
    (
        (
            single_target(),
            SELECT_PUBLIC,
            GITLAB_INSTANCE_REQUIRED.format(table=GITLAB_PUBLIC_TABLE),
        ),
        (
            {GITLAB_AUTHORITY: PRIVATE.fields(), **single_target()},
            NO_SELECTION,
            GITLAB_PRIVATE_DECLARED_TWICE,
        ),
        (
            edited(routed_target(), ((GITLAB_PRIVATE_TABLE, "project"), "")),
            NO_SELECTION,
            f"{GITLAB_PRIVATE_TABLE}.project must be a relative path or numeric ID",
        ),
        (
            edited(routed_target(), ((GITLAB_PUBLIC_TABLE, "project"), "")),
            SELECT_PUBLIC,
            f"{GITLAB_PUBLIC_TABLE}.project must be a relative path or numeric ID",
        ),
    ),
    ids=(
        "public-not-declared",
        "private-declared-twice",
        "private-project-empty",
        "public-project-empty",
    ),
)
def test_open_bug_blocks_on_an_unusable_selection(
    target_path: Path,
    providers: Mapping[str, str],
    fake_bin: Path,
    call_journal: Path,
    tables: Mapping[str, TomlValue],
    selection: GitLabSelection,
    reason: str,
) -> None:
    """Block before any provider call when the selection cannot be satisfied.

    :param target_path: Path receiving the target named by the environment.
    :param providers: Child environment of the journaling fake providers.
    :param fake_bin: Directory holding the fake providers.
    :param call_journal: Journal that must stay empty.
    :param tables: Composed target.
    :param selection: Caller's instance choice, or none.
    :param reason: Exact reason expected in the BLOCKED result.
    :returns: None; assertions verify the BLOCKED result and zero provider calls.
    """
    write_target(target_path, tables)

    result = run_script(
        "gitlab_issue.py",
        *OPEN_BUG,
        *_selection_argv(selection),
        fake_bin=fake_bin,
        env={**providers, TARGET_ENV: str(target_path)},
    )
    data = parse_json_output(result)

    assert result.returncode == 2
    assert data["status"] == "BLOCKED"
    assert [error["reason"] for error in data["errors"]] == [reason]
    assert ROUTING_RESULT_KEY not in data
    assert read_journal(call_journal) == []


def test_open_bug_rejects_an_unknown_selector_choice(target_path: Path) -> None:
    """Accept only the selector choices declared by the contract.

    :param target_path: Path receiving the routed target.
    :returns: None; assertions verify the argument failure before target loading.
    """
    write_target(target_path, routed_target())

    result = run_script(
        "gitlab_issue.py",
        *OPEN_BUG,
        GITLAB_TARGET_OPTION,
        UNKNOWN_INSTANCE,
        env={TARGET_ENV: str(target_path)},
    )
    data = parse_json_output(result)

    assert result.returncode == 2
    assert data["status"] == "BLOCKED"
    assert data["errors"][0]["source"] == "arguments"
    assert ROUTING_RESULT_KEY not in data


@pytest.mark.parametrize(
    ("tables", "selection", "line"),
    (
        (
            routed_target(),
            SELECT_PUBLIC,
            (
                f"Routing: {GITLAB_AUTHORITY}={GITLAB_PUBLIC_TABLE} "
                f"{KUBERNETES_AUTHORITY}={KUBERNETES_AUTHORITY} "
                f"{HARBOR_AUTHORITY}={HARBOR_AUTHORITY} ({ARGV_PUBLIC})"
            ),
        ),
        (
            issue_target(),
            NO_SELECTION,
            (
                f"Routing: {GITLAB_AUTHORITY}={GITLAB_AUTHORITY} "
                f"{KUBERNETES_AUTHORITY}=none {HARBOR_AUTHORITY}=none "
                f"({ROUTING_SELECTED_BY_LEGACY})"
            ),
        ),
    ),
    ids=("routed-public", "legacy-unrouted"),
)
def test_open_bug_human_view_prints_the_routing_line(
    target_path: Path,
    tables: Mapping[str, TomlValue],
    selection: GitLabSelection,
    line: str,
) -> None:
    """Print one routing line directly under the status line of the human view.

    :param target_path: Path receiving the target.
    :param tables: Composed target.
    :param selection: Caller's instance choice, or none.
    :param line: Exact routing line; an unrouted table prints ``none``.
    :returns: None; assertions verify the line text and its position.
    """
    write_target(target_path, tables)

    result = run_script(
        "gitlab_issue.py",
        *OPEN_BUG[:-1],
        "--human",
        *_selection_argv(selection),
        env={TARGET_ENV: str(target_path)},
    )
    lines = result.stdout.splitlines()

    assert result.returncode == 0, result.stderr
    assert lines[1] == line
    assert lines.count(line) == 1


def test_identical_instances_have_distinct_confirmation_digests(
    target_path: Path, providers: Mapping[str, str], fake_bin: Path, call_journal: Path
) -> None:
    """Reject a confirmation digest planned for the other instance.

    Both instances hold identical fields, so only the selector differs.

    :param target_path: Target whose two instances hold identical settings.
    :param providers: Child environment of the journaling fake providers.
    :param fake_bin: Directory holding the fake providers.
    :param call_journal: Journal that must stay empty.
    :returns: None; assertions verify the selector binds the plan digest.
    """
    write_target(
        target_path, edited(routed_target(), ((GITLAB_PUBLIC_TABLE,), PRIVATE.fields()))
    )
    environment = {**providers, TARGET_ENV: str(target_path)}
    plans = [
        parse_json_output(
            run_script(
                "gitlab_issue.py",
                *OPEN_BUG,
                *_selection_argv(selection),
                fake_bin=fake_bin,
                env=environment,
            )
        )
        for selection in (SELECT_PRIVATE, SELECT_PUBLIC)
    ]
    first, second = plans

    applied = run_script(
        "gitlab_issue.py",
        *OPEN_BUG,
        *_selection_argv(SELECT_PUBLIC),
        "--apply",
        "--confirm-plan",
        first["plan_digest"],
        fake_bin=fake_bin,
        env=environment,
    )
    blocked = parse_json_output(applied)

    assert (
        first["origin"],
        first["target"],
        first["target_file"],
        first["plan"]["body_sha256"],
    ) == (
        second["origin"],
        second["target"],
        second["target_file"],
        second["plan"]["body_sha256"],
    )
    assert first["plan_digest"] != second["plan_digest"]
    assert applied.returncode == 2
    assert blocked["status"] == "BLOCKED"
    assert [error["reason"] for error in blocked["errors"]] == [
        "confirm_plan_mismatch_rerun_dry_run"
    ]
    assert read_journal(call_journal) == []


@pytest.mark.parametrize(
    ("selection", "expected"),
    (
        (
            NO_SELECTION,
            _template_routing(TEMPLATE_DEFAULT, ROUTING_SELECTED_BY_DEFAULT),
        ),
        (SELECT_PUBLIC, _template_routing(GITLAB_PUBLIC_TABLE, ARGV_PUBLIC)),
    ),
    ids=("default", "public"),
)
def test_access_check_dry_run_on_the_template_reports_routing(
    template_path: Path,
    providers: Mapping[str, str],
    fake_bin: Path,
    call_journal: Path,
    selection: GitLabSelection,
    expected: Mapping[str, Optional[str]],
) -> None:
    """Report the shipped template's route in the access result.

    :param template_path: Copy of the shipped template beside its files.
    :param providers: Child environment of the journaling fake providers.
    :param fake_bin: Directory holding the fake providers.
    :param call_journal: Journal that must stay empty.
    :param selection: Caller's instance choice, or none.
    :param expected: Routing object built from the template's own row.
    :returns: None; assertions compare the routing object with the template.
    """
    target = template_path

    result = run_script(
        "access_check.py",
        "--dry-run",
        "--json",
        *_selection_argv(selection),
        fake_bin=fake_bin,
        env={**providers, TARGET_ENV: str(target)},
    )
    data = parse_json_output(result)

    assert result.returncode == 0, result.stderr
    assert data["status"] == "DRY_RUN"
    assert data["schema_version"] == ROUTED_RESULT_SCHEMA_VERSION
    assert data[ROUTING_RESULT_KEY] == expected
    assert read_journal(call_journal) == []


@pytest.mark.parametrize(
    ("selection", "reason"),
    (
        (SELECT_PRIVATE, None),
        (SELECT_PUBLIC, MISSING_AUTHORITY_TABLES + KUBERNETES_AUTHORITY),
    ),
    ids=("private-row-pairs-kubernetes", "public-row-pairs-nothing"),
)
def test_access_check_requiring_kubernetes_uses_the_routed_cluster(
    target_path: Path,
    fake_bin: Path,
    call_journal: Path,
    selection: GitLabSelection,
    reason: Optional[str],
) -> None:
    """Pass the routed Kubernetes context and kubeconfig to ``kubectl``.

    ``--ceph-namespace`` makes Kubernetes required, so the selected row decides
    whether the command reaches the cluster at all.

    :param target_path: Path receiving the target; its directory holds the kubeconfig.
    :param fake_bin: Directory receiving the fake providers.
    :param call_journal: Journal of provider calls.
    :param selection: Caller's instance choice.
    :param reason: Missing-authority reason expected for an unpaired row, or None.
    :returns: None; assertions compare provider arguments with the fixture's.
    """
    for tool in ("gh", "glab", "kubectl"):
        install_executable(fake_bin, tool, FAKE_ACCESS_TOOLS)
    kubeconfig = target_path.parent / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    kubernetes = {**FIXTURE_KUBERNETES, "kubeconfig": str(kubeconfig)}
    target = write_target(
        target_path,
        {
            GITHUB_AUTHORITY: dict(FIXTURE_GITHUB),
            GITLAB_ROUTING_TABLE: routing_table(
                {
                    GITLAB_PRIVATE_TABLE: paired(KUBERNETES_AUTHORITY),
                    GITLAB_PUBLIC_TABLE: paired(),
                },
                default=GITLAB_PRIVATE_TABLE,
            ),
            GITLAB_PRIVATE_TABLE: dict(FIXTURE_GITLAB),
            GITLAB_PUBLIC_TABLE: {"url": PUBLIC.url},
            KUBERNETES_AUTHORITY: kubernetes,
        },
    )

    result = run_script(
        "access_check.py",
        "--ceph-namespace",
        "unit-ceph",
        "--revision",
        TEST_REVISION,
        "--json",
        *_selection_argv(selection),
        fake_bin=fake_bin,
        env={"FAKE_ACCESS_CALLS": str(call_journal), TARGET_ENV: str(target)},
    )
    calls = read_journal(call_journal)

    if reason is not None:
        data = parse_json_output(result)
        assert result.returncode == 2
        assert data["errors"][0]["reason"] == reason
        assert calls == []
        return
    kubectl = [call["args"] for call in calls if call["tool"] == "kubectl"]
    glab = [call["args"] for call in calls if call["tool"] == "glab"]
    assert kubectl and glab
    for argv in kubectl:
        assert argv[argv.index("--context") + 1] == kubernetes["context"]
        assert argv[argv.index("--kubeconfig") + 1] == str(kubeconfig.resolve())
    for argv in glab:
        assert (
            argv[argv.index("--hostname") + 1] == urlsplit(FIXTURE_GITLAB["url"]).netloc
        )


def _runner_deploy(
    target: Path,
    harbor: Mapping[str, TomlValue],
    fake_bin: Path,
    environment: Mapping[str, str],
    *argv: str,
) -> tuple[int, dict[str, object]]:
    """Run ``gitlab_runner_manager.py deploy`` against one target.

    :param target: Target file named by the environment.
    :param harbor: Harbor table the target declares; its host names the image.
    :param fake_bin: Directory holding the fake providers.
    :param environment: Child environment of the fake providers.
    :param *argv: Mode and selector options of the case.
    :returns: Exit status and decoded JSON result.
    """
    token = target.parent / "runner.token"
    token.write_text("unit-runner-token\n", encoding="utf-8")
    registry = urlsplit(harbor["url"]).netloc
    result = run_script(
        "gitlab_runner_manager.py",
        "deploy",
        "--json",
        *DEPLOY_PROJECT,
        "--image",
        f"{registry}/toolbox/ci@sha256:{'a' * 64}",
        "--node",
        "worker-a",
        "--token-file",
        token,
        "--runner-id",
        "73",
        *argv,
        fake_bin=fake_bin,
        env={**environment, TARGET_ENV: str(target)},
    )
    return result.returncode, parse_json_output(result)


@pytest.mark.parametrize(
    ("selection", "expected"),
    (
        (
            NO_SELECTION,
            _template_routing(TEMPLATE_DEFAULT, ROUTING_SELECTED_BY_DEFAULT),
        ),
        (SELECT_PRIVATE, _template_routing(GITLAB_PRIVATE_TABLE, ARGV_PRIVATE)),
    ),
    ids=("default", "private"),
)
def test_runner_deploy_plan_on_the_template_reports_the_paired_route(
    template_path: Path,
    providers: Mapping[str, str],
    fake_bin: Path,
    call_journal: Path,
    selection: GitLabSelection,
    expected: Mapping[str, Optional[str]],
) -> None:
    """Plan a runner deploy into the cluster and registry the template row pairs.

    :param template_path: Copy of the shipped template beside its files.
    :param providers: Child environment of the journaling fake providers.
    :param fake_bin: Directory holding the fake providers.
    :param call_journal: Journal that must stay empty.
    :param selection: Caller's instance choice, or none.
    :param expected: Routing object built from the template's own row.
    :returns: None; assertions verify the routing object and the schema.
    """
    target = template_path

    code, data = _runner_deploy(
        target,
        TEMPLATE[HARBOR_AUTHORITY],
        fake_bin,
        providers,
        "--dry-run",
        *_selection_argv(selection),
    )

    assert code == 0, data
    assert data["status"] == "DRY_RUN"
    assert data["schema_version"] == ROUTED_RESULT_SCHEMA_VERSION
    assert data[ROUTING_RESULT_KEY] == expected
    specification_validator(WORKFLOW_SCHEMA).validate(data)
    assert read_journal(call_journal) == []


def test_template_public_instance_cannot_deploy_a_runner_into_private_tables(
    template_path: Path,
    providers: Mapping[str, str],
    fake_bin: Path,
    call_journal: Path,
) -> None:
    """Block a public-GitLab runner deploy that would use the private cluster.

    The shipped template's public row pairs no Kubernetes or Harbor table, so
    ``--gitlab public`` must not reach the cluster and registry declared for
    the private GitLab. The live plan and the dry run block alike.

    :param template_path: Copy of the shipped template beside its files.
    :param providers: Child environment of the journaling fake providers.
    :param fake_bin: Directory holding the fake providers.
    :param call_journal: Journal that must stay empty.
    :returns: None; assertions verify both modes block before any provider call.
    """
    target = template_path
    row = TEMPLATE[GITLAB_ROUTING_TABLE][GITLAB_PUBLIC_TABLE]
    reason = MISSING_AUTHORITY_TABLES + ", ".join(sorted(set(ROUTED) - set(row)))
    selector = _selection_argv(SELECT_PUBLIC)

    harbor = TEMPLATE[HARBOR_AUTHORITY]
    live_code, live = _runner_deploy(target, harbor, fake_bin, providers, *selector)
    dry_code, dry = _runner_deploy(
        target, harbor, fake_bin, providers, "--dry-run", *selector
    )

    for code, data in ((live_code, live), (dry_code, dry)):
        assert code == 2
        assert data["status"] == "BLOCKED"
        assert [error["reason"] for error in data["errors"]] == [reason]
        assert ROUTING_RESULT_KEY not in data
    assert live["errors"] == dry["errors"]
    assert read_journal(call_journal) == []


@pytest.mark.parametrize(
    ("row", "reason"),
    ((paired(*ROUTED), None), (paired(), MISSING_ROUTED)),
    ids=("paired", "unpaired"),
)
def test_runner_deploy_plan_uses_the_selected_route(
    target_path: Path,
    providers: Mapping[str, str],
    fake_bin: Path,
    call_journal: Path,
    row: Mapping[str, TomlValue],
    reason: Optional[str],
) -> None:
    """Plan a runner deploy only into the cluster and registry the route pairs.

    :param target_path: Path receiving the routed target.
    :param providers: Child environment of the journaling fake providers.
    :param fake_bin: Directory holding the fake providers.
    :param call_journal: Journal that must stay empty.
    :param row: Public routing row.
    :param reason: Expected reason for the unpaired row, otherwise None.
    :returns: None; assertions verify the plan, the routing object and the schema.
    """
    write_target(
        target_path,
        edited(routed_target(), ((GITLAB_ROUTING_TABLE, GITLAB_PUBLIC_TABLE), row)),
    )

    code, data = _runner_deploy(
        target_path,
        FIXTURE_HARBOR,
        fake_bin,
        providers,
        "--dry-run",
        *_selection_argv(SELECT_PUBLIC),
    )

    assert read_journal(call_journal) == []
    if reason is not None:
        assert code == 2
        assert data["status"] == "BLOCKED"
        assert [error["reason"] for error in data["errors"]] == [reason]
        assert ROUTING_RESULT_KEY not in data
        return
    assert code == 0, data
    assert data["status"] == "DRY_RUN"
    assert data[ROUTING_RESULT_KEY] == _routing(
        GITLAB_PUBLIC_TABLE, KUBERNETES_AUTHORITY, HARBOR_AUTHORITY, ARGV_PUBLIC
    )
    specification_validator(WORKFLOW_SCHEMA).validate(data)


def test_dry_run_deprecation_event_does_not_write_the_log_file(
    target_path: Path,
) -> None:
    """Follow the command's dry-run rule: diagnostics on stderr, no log file.

    :param target_path: Path receiving the issue #133 legacy shape.
    :returns: None; assertions verify stderr carries the event and no file exists.
    """
    write_target(target_path, issue_target())
    log_file = target_path.parent / "ci-api.log"

    result = run_script(
        "ci-api",
        "check",
        "--provider",
        GITLAB_AUTHORITY,
        "--dry-run",
        "--json",
        "--log-file",
        log_file,
        env={TARGET_ENV: str(target_path)},
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr.count(GITLAB_DEPRECATION_EVENT) == 1
    assert not log_file.exists()


def test_legacy_table_beside_public_instance_reports_the_legacy_table(
    target_path: Path,
) -> None:
    """Name the deprecated table, not the selected instance, in the one event.

    :param target_path: Path receiving the legacy table beside the public instance.
    :returns: None; assertions verify one event whose table is the legacy table.
    """
    write_target(
        target_path,
        {GITLAB_AUTHORITY: PRIVATE.fields(), GITLAB_PUBLIC_TABLE: PUBLIC.fields()},
    )

    result = run_script(
        "gitlab_issue.py",
        *OPEN_BUG,
        *_selection_argv(SELECT_PUBLIC),
        "--log-format",
        "json",
        "--run-id",
        "unit-run",
        env={TARGET_ENV: str(target_path)},
    )
    events = [
        json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")
    ]
    deprecations = [
        event for event in events if event["event"] == GITLAB_DEPRECATION_EVENT
    ]

    assert result.returncode == 0, result.stderr
    assert len(deprecations) == 1
    assert deprecations[0]["details"]["table"] == GITLAB_AUTHORITY
