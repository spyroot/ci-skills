"""Offline regressions for GitLab instance selection and ``[gitlab_routing]``.

A target declares ``[gitlab_private]`` and ``[gitlab_public]`` plus one
``[gitlab_routing]`` table. ``--gitlab private|public`` selects an instance;
omission selects ``[gitlab_routing].default``. The legacy ``[gitlab]`` table and
its ``[gitlab.NAME]`` profiles stay readable for one release with a deprecation
event. Every row asserts exact text.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final, Optional

import pytest
from conftest import (
    import_script_module,
    parse_json_output,
    run_script,
)

TARGET = import_script_module("lib.python.core.target")
ENDPOINTS = import_script_module("lib.python.core.endpoints")
BINDING = import_script_module("lib.python.core.project_binding")
API_READ = import_script_module("lib.python.core.api_read")
READ_CLI = import_script_module("lib.python.core.read_cli")
GITLAB_ACTION = import_script_module("lib.python.core.gitlab.gitlab_action")
SCHEMA = import_script_module("lib.python.core.schema")
ISSUE = import_script_module("gitlab_issue")

PRIVATE_HOST: Final[str] = "private.example.test"
PUBLIC_HOST: Final[str] = "public.example.test"
PRIVATE_TOKEN_PATH: Final[str] = "private.token"
PUBLIC_TOKEN_PATH: Final[str] = "tokens/public.token"
PRIVATE_TOKEN_TEXT: Final[str] = "unit-private-token-value"
PUBLIC_TOKEN_TEXT: Final[str] = "unit-public-token-value"
TARGET_ENV_SOURCE: Final[str] = "env:CI_SKILLS_TARGET"
DEPRECATION_EVENT: Final[str] = "gitlab_target_deprecated"
ROUTING_SCHEMA: Final[str] = "results/gitlab-routing.schema.json"
ACTION_SCHEMA: Final[str] = "results/gitlab-action.schema.json"

ROUTING: Final[str] = (
    "[gitlab_routing]\n"
    'default = "gitlab_private"\n'
    'gitlab_private = { kubernetes = "kubernetes", harbor = "harbor" }\n'
    'gitlab_public = { kubernetes = "kubernetes", harbor = "harbor" }\n'
)
SINGLE_ROUTING: Final[str] = (
    '[gitlab_routing]\ndefault = "gitlab_private"\ngitlab_private = {}\n'
)
PRIVATE_TABLE: Final[str] = (
    "[gitlab_private]\n"
    f'url = "https://{PRIVATE_HOST}"\n'
    'project = "consumer/private"\n'
    f'token_file = "{PRIVATE_TOKEN_PATH}"\n'
    "runner_id = 11\n"
)
PUBLIC_TABLE: Final[str] = (
    "[gitlab_public]\n"
    f'url = "https://{PUBLIC_HOST}"\n'
    'project = "consumer/public"\n'
    f'token_file = "{PUBLIC_TOKEN_PATH}"\n'
    "runner_id = 22\n"
)
HARBOR_TABLE: Final[str] = (
    "[harbor]\n"
    'url = "https://harbor.example.test"\n'
    'project = "unit"\n'
    'credential_file = "harbor.credential"\n'
)
KUBERNETES_TABLE: Final[str] = (
    "[kubernetes]\n"
    'context = "unit-context"\n'
    'server = "https://api.cluster.example.test:6443"\n'
)
ROUTED_TARGET: Final[str] = (
    ROUTING + PRIVATE_TABLE + PUBLIC_TABLE + HARBOR_TABLE + KUBERNETES_TABLE
)
SINGLE_TARGET: Final[str] = SINGLE_ROUTING + PRIVATE_TABLE + KUBERNETES_TABLE
LEGACY_TABLE: Final[str] = PRIVATE_TABLE.replace("[gitlab_private]", "[gitlab]")
LEGACY_TARGET: Final[str] = LEGACY_TABLE + KUBERNETES_TABLE
# The shape reported in issue #133: TOML reads [gitlab.com] as a table "com"
# nested in [gitlab].
ISSUE_TARGET: Final[str] = LEGACY_TABLE + PUBLIC_TABLE.replace(
    "[gitlab_public]", "[gitlab.com]"
)
PUBLIC_DEFAULT_TARGET: Final[str] = ROUTED_TARGET.replace(
    'default = "gitlab_private"', 'default = "gitlab_public"', 1
)
PUBLIC_ONLY_TARGET: Final[str] = (
    '[gitlab_routing]\ndefault = "gitlab_public"\ngitlab_public = {}\n'
    + PUBLIC_TABLE
    + KUBERNETES_TABLE
)

Loader = Callable[..., object]
LOADERS: Final[Mapping[str, Loader]] = {
    "load_target": lambda path, **selection: (
        TARGET.load_target(path, required_surfaces=("gitlab",), **selection).gitlab
    ),
    "load_gitlab_target": lambda path, **selection: (
        TARGET.load_gitlab_target(path, **selection).gitlab
    ),
}


@pytest.fixture
def target_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create token files and isolate ambient target selection.

    :param tmp_path: Directory containing only synthetic target and token files.
    :param monkeypatch: Fixture isolating home, cwd, and target selectors.
    :returns: Path where each test writes its target body.
    """
    directory = tmp_path / "configuration"
    (directory / "tokens").mkdir(parents=True)
    (directory / PRIVATE_TOKEN_PATH).write_text(PRIVATE_TOKEN_TEXT, encoding="utf-8")
    (directory / PUBLIC_TOKEN_PATH).write_text(PUBLIC_TOKEN_TEXT, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(home)
    for variable in (BINDING.TARGET_ENV, BINDING.BINDING_ENV, BINDING.SMOKE_ENV):
        monkeypatch.delenv(variable, raising=False)
    return directory / "target.toml"


def _route(
    gitlab: str,
    kubernetes: Optional[str],
    harbor: Optional[str],
    selected_by: str,
) -> object:
    """Build the expected route through the production value type.

    :param gitlab: Table the loader read.
    :param kubernetes: Routed Kubernetes table, or None.
    :param harbor: Routed Harbor table, or None.
    :param selected_by: Selection provenance.
    :returns: ``GitLabRoute`` value compared with the loaded route.
    """
    return TARGET.GitLabRoute(
        gitlab=gitlab, kubernetes=kubernetes, harbor=harbor, selected_by=selected_by
    )


def test_selector_constants_are_declared_once_in_the_contract() -> None:
    """Map each CLI choice to exactly one instance table name.

    :returns: None; assertions pin the contract names every module imports.
    """
    assert dict(ENDPOINTS.GITLAB_SELECTORS) == {
        "private": "gitlab_private",
        "public": "gitlab_public",
    }
    assert ENDPOINTS.GITLAB_ROUTING_TABLE == "gitlab_routing"
    assert ENDPOINTS.GITLAB_ROUTING_DEFAULT_KEY == "default"
    assert ENDPOINTS.GITLAB_ROUTED_AUTHORITIES == ("kubernetes", "harbor")


@pytest.mark.parametrize("loader", tuple(LOADERS))
@pytest.mark.parametrize(
    ("body", "selection", "host", "project", "token", "runner", "route"),
    (
        (
            SINGLE_TARGET,
            {},
            PRIVATE_HOST,
            "consumer/private",
            PRIVATE_TOKEN_PATH,
            11,
            ("gitlab_private", None, None, "default"),
        ),
        (
            ROUTED_TARGET,
            {},
            PRIVATE_HOST,
            "consumer/private",
            PRIVATE_TOKEN_PATH,
            11,
            ("gitlab_private", "kubernetes", "harbor", "default"),
        ),
        (
            ROUTED_TARGET,
            {"gitlab": "public"},
            PUBLIC_HOST,
            "consumer/public",
            PUBLIC_TOKEN_PATH,
            22,
            ("gitlab_public", "kubernetes", "harbor", "argv:--gitlab=public"),
        ),
        (
            ROUTED_TARGET,
            {"gitlab": "private"},
            PRIVATE_HOST,
            "consumer/private",
            PRIVATE_TOKEN_PATH,
            11,
            ("gitlab_private", "kubernetes", "harbor", "argv:--gitlab=private"),
        ),
        (
            PUBLIC_DEFAULT_TARGET,
            {},
            PUBLIC_HOST,
            "consumer/public",
            PUBLIC_TOKEN_PATH,
            22,
            ("gitlab_public", "kubernetes", "harbor", "default"),
        ),
        (
            LEGACY_TARGET,
            {},
            PRIVATE_HOST,
            "consumer/private",
            PRIVATE_TOKEN_PATH,
            11,
            ("gitlab", None, None, "legacy"),
        ),
        (
            ISSUE_TARGET,
            {},
            PRIVATE_HOST,
            "consumer/private",
            PRIVATE_TOKEN_PATH,
            11,
            ("gitlab", None, None, "legacy"),
        ),
        (
            ISSUE_TARGET,
            {"gitlab_profile": "com"},
            PUBLIC_HOST,
            "consumer/public",
            PUBLIC_TOKEN_PATH,
            22,
            ("gitlab.com", None, None, 'argv:--gitlab-profile="com"'),
        ),
        (
            LEGACY_TARGET,
            {"gitlab": "private"},
            PRIVATE_HOST,
            "consumer/private",
            PRIVATE_TOKEN_PATH,
            11,
            ("gitlab", None, None, "argv:--gitlab=private"),
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
    ),
)
def test_selection_reads_one_instance_and_reports_its_route(
    target_path: Path,
    loader: str,
    body: str,
    selection: Mapping[str, str],
    host: str,
    project: str,
    token: str,
    runner: int,
    route: tuple[str, Optional[str], Optional[str], str],
) -> None:
    """Select the declared instance and report the table that was read.

    :param target_path: Path receiving the target body.
    :param loader: Public loader exercised by the case.
    :param body: Complete target TOML.
    :param selection: ``gitlab`` or ``gitlab_profile`` keyword, or none.
    :param host: Expected GitLab authority.
    :param project: Expected project from the selected table.
    :param token: Expected target-relative token path.
    :param runner: Expected runner ID from the selected table.
    :param route: Expected route fields in declaration order.
    :returns: None; assertions verify the selected values and unchanged input.
    """
    target_path.write_text(body, encoding="utf-8")

    selected = LOADERS[loader](target_path, **selection)

    assert selected.url == f"https://{host}"
    assert selected.host == host
    assert selected.project == project
    assert selected.group is None
    assert selected.runner_id == runner
    assert selected.token_file == (target_path.parent / token).resolve()
    assert selected.route == _route(*route)
    assert target_path.read_text(encoding="utf-8") == body


def test_target_routing_is_the_selected_gitlab_route(target_path: Path) -> None:
    """Expose the selected route on the combined target used by every command.

    :param target_path: Path receiving the routed target.
    :returns: None; assertions verify one route value for target and GitLab.
    """
    target_path.write_text(ROUTED_TARGET, encoding="utf-8")

    target = TARGET.load_target(
        target_path, required_surfaces=("gitlab", "kubernetes"), gitlab="public"
    )

    assert target.routing == _route(
        "gitlab_public", "kubernetes", "harbor", "argv:--gitlab=public"
    )
    assert target.routing is target.gitlab.route


@pytest.mark.parametrize(
    ("body", "selection", "expected"),
    (
        (ROUTED_TARGET, {}, TARGET_ENV_SOURCE),
        (
            ROUTED_TARGET,
            {"gitlab": "public"},
            f"{TARGET_ENV_SOURCE};argv:--gitlab=public",
        ),
        (LEGACY_TARGET, {}, TARGET_ENV_SOURCE),
        (
            ISSUE_TARGET,
            {"gitlab_profile": "com"},
            f'{TARGET_ENV_SOURCE};argv:--gitlab-profile="com"',
        ),
    ),
    ids=("default", "explicit-public", "legacy", "legacy-profile"),
)
def test_selection_source_names_only_caller_selectors(
    target_path: Path, body: str, selection: Mapping[str, str], expected: str
) -> None:
    """Bind an explicit selector into report and confirmation provenance.

    :param target_path: Path receiving the target body.
    :param body: Complete target TOML.
    :param selection: Caller selector keyword, or none.
    :param expected: Exact provenance string.
    :returns: None; assertions verify the provenance text.
    """
    target_path.write_text(body, encoding="utf-8")

    target = TARGET.load_target(target_path, required_surfaces=("gitlab",), **selection)

    assert target.selection_source(TARGET_ENV_SOURCE) == expected
    assert target.gitlab.selection_source(TARGET_ENV_SOURCE) == expected


@pytest.mark.parametrize("loader", tuple(LOADERS))
@pytest.mark.parametrize(
    ("body", "selection", "reason"),
    (
        (SINGLE_TARGET, {"gitlab": "public"}, "gitlab_public_target_required"),
        (PUBLIC_ONLY_TARGET, {"gitlab": "private"}, "gitlab_private_target_required"),
        (LEGACY_TARGET, {"gitlab": "public"}, "gitlab_public_target_required"),
        (PRIVATE_TABLE + KUBERNETES_TABLE, {}, "gitlab_routing_required"),
        (PUBLIC_TABLE + KUBERNETES_TABLE, {}, "gitlab_routing_required"),
        (
            ROUTED_TARGET.replace('"gitlab_private"\n', '"gitlab_other"\n', 1),
            {},
            "gitlab_routing_default_unknown",
        ),
        (
            SINGLE_TARGET.replace('"gitlab_private"\n', '"gitlab_public"\n', 1),
            {},
            "gitlab_routing_default_unknown",
        ),
        (
            ROUTED_TARGET.replace('default = "gitlab_private"\n', "", 1),
            {},
            "gitlab_routing_default_unknown",
        ),
        (
            ROUTED_TARGET.replace(
                'gitlab_public = { kubernetes = "kubernetes", harbor = "harbor" }\n',
                "",
                1,
            ),
            {},
            "gitlab_routing_row_missing",
        ),
        (
            SINGLE_TARGET.replace(
                "gitlab_private = {}\n", "gitlab_private = {}\ngitlab_public = {}\n", 1
            ),
            {},
            "gitlab_routing_row_unknown",
        ),
        (
            SINGLE_TARGET.replace(
                "gitlab_private = {}\n", "gitlab_private = {}\ngitlab_other = {}\n", 1
            ),
            {},
            "gitlab_routing_row_unknown",
        ),
        (
            ROUTED_TARGET.replace(
                '{ kubernetes = "kubernetes"', '{ kubernetes = "cluster_b"', 1
            ),
            {},
            "gitlab_routing_kubernetes_unknown",
        ),
        (
            ROUTED_TARGET.replace(KUBERNETES_TABLE, "", 1),
            {},
            "gitlab_routing_kubernetes_unknown",
        ),
        (
            ROUTED_TARGET.replace(HARBOR_TABLE, "", 1),
            {},
            "gitlab_routing_harbor_unknown",
        ),
        (
            SINGLE_TARGET.replace(
                "gitlab_private = {}", 'gitlab_private = { registry = "harbor" }', 1
            ),
            {},
            "gitlab_routing.gitlab_private contains unsupported fields: registry",
        ),
        (
            SINGLE_TARGET.replace(
                "gitlab_private = {}", 'gitlab_private = "kubernetes"', 1
            ),
            {},
            "gitlab_routing.gitlab_private must be a TOML table",
        ),
        (LEGACY_TABLE + ROUTED_TARGET, {}, "gitlab_target_shapes_mixed"),
        (LEGACY_TARGET + PUBLIC_TABLE, {}, "gitlab_target_shapes_mixed"),
        (LEGACY_TARGET + SINGLE_ROUTING, {}, "gitlab_target_shapes_mixed"),
        (
            ISSUE_TARGET,
            {"gitlab": "public", "gitlab_profile": "com"},
            "gitlab_selector_conflict",
        ),
        (
            ROUTED_TARGET,
            {"gitlab": "public", "gitlab_profile": "com"},
            "gitlab_selector_conflict",
        ),
        (ROUTED_TARGET, {"gitlab_profile": "com"}, "gitlab_profile_unknown"),
        (ISSUE_TARGET, {"gitlab_profile": "missing"}, "gitlab_profile_unknown"),
    ),
    ids=(
        "public-not-declared",
        "private-not-declared",
        "legacy-has-no-public",
        "private-without-routing",
        "public-without-routing",
        "default-names-unknown-table",
        "default-names-undeclared-instance",
        "default-absent",
        "declared-instance-without-row",
        "row-for-undeclared-instance",
        "row-for-unknown-instance",
        "row-names-unknown-kubernetes",
        "row-names-absent-kubernetes",
        "row-names-absent-harbor",
        "row-has-unknown-field",
        "row-is-not-table",
        "legacy-and-private",
        "legacy-and-public",
        "legacy-and-routing",
        "both-selectors-legacy",
        "both-selectors-routed",
        "profile-on-routed-target",
        "unknown-legacy-profile",
    ),
)
def test_invalid_selection_or_routing_fails_closed_with_exact_reason(
    target_path: Path,
    loader: str,
    body: str,
    selection: Mapping[str, str],
    reason: str,
) -> None:
    """Reject one broken selection or routing invariant without a fallback.

    :param target_path: Path receiving the invalid target.
    :param loader: Public loader whose fail-closed selection is checked.
    :param body: Target TOML breaking exactly one invariant.
    :param selection: Caller selector keywords, or none.
    :param reason: Exact diagnostic expected by the calling agent.
    :returns: None; assertions verify the reason and the unchanged input.
    """
    target_path.write_text(body, encoding="utf-8")

    with pytest.raises(TARGET.TargetError) as error:
        LOADERS[loader](target_path, **selection)

    assert str(error.value) == reason
    assert target_path.read_text(encoding="utf-8") == body


@pytest.mark.parametrize("loader", tuple(LOADERS))
@pytest.mark.parametrize(
    ("body", "before", "after", "selection", "message"),
    (
        (
            ROUTED_TARGET,
            f'url = "https://{PUBLIC_HOST}"\n',
            "",
            {"gitlab": "public"},
            "gitlab_public.url must be a nonempty string",
        ),
        (
            ROUTED_TARGET,
            f'url = "https://{PUBLIC_HOST}"\n',
            f'url = "https://{PUBLIC_HOST}/consumer"\n',
            {"gitlab": "public"},
            "gitlab_public.url must be an HTTPS origin with a full hostname",
        ),
        (
            ROUTED_TARGET,
            'project = "consumer/public"',
            'project = "/consumer/public"',
            {"gitlab": "public"},
            "gitlab_public.project must be a relative path or numeric ID",
        ),
        (
            ROUTED_TARGET,
            f'token_file = "{PUBLIC_TOKEN_PATH}"',
            'token_file = ""',
            {"gitlab": "public"},
            "gitlab_public.token_file must be a nonempty path when supplied",
        ),
        (
            ROUTED_TARGET,
            "runner_id = 22",
            "runner_id = 0",
            {"gitlab": "public"},
            "gitlab_public.runner_id must be a positive numeric ID",
        ),
        (
            ROUTED_TARGET,
            "[gitlab_public]\n",
            '[gitlab_public]\nunknown = "fixture"\n',
            {"gitlab": "public"},
            "gitlab_public contains unsupported fields: unknown",
        ),
        (
            ROUTED_TARGET,
            f'url = "https://{PRIVATE_HOST}"\n',
            "",
            {},
            "gitlab_private.url must be a nonempty string",
        ),
        (
            LEGACY_TARGET,
            f'url = "https://{PRIVATE_HOST}"\n',
            "",
            {},
            "gitlab.url must be a nonempty string",
        ),
        (
            ISSUE_TARGET,
            f'url = "https://{PUBLIC_HOST}"\n',
            "",
            {"gitlab_profile": "com"},
            "gitlab.com.url must be a nonempty string",
        ),
    ),
    ids=(
        "public-url-missing",
        "public-url-not-origin",
        "public-project-absolute",
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
    body: str,
    before: str,
    after: str,
    selection: Mapping[str, str],
    message: str,
) -> None:
    """Qualify a field error with the exact table the loader read.

    :param target_path: Path receiving the invalid target.
    :param loader: Public loader whose diagnostic is checked.
    :param body: Valid target TOML before one fragment is replaced.
    :param before: Fragment that occurs once in ``body``.
    :param after: Replacement breaking one field invariant.
    :param selection: Caller selector keywords, or none.
    :param message: Exact diagnostic expected by the calling agent.
    :returns: None; assertions verify the text and the absence of credentials.
    """
    assert body.count(before) == 1
    target_path.write_text(body.replace(before, after, 1), encoding="utf-8")

    with pytest.raises(TARGET.TargetError) as error:
        LOADERS[loader](target_path, **selection)

    text = str(error.value)
    assert text == message
    for secret in (PRIVATE_TOKEN_TEXT, PUBLIC_TOKEN_TEXT, str(target_path.parent)):
        assert secret not in text


@pytest.mark.parametrize("loader", tuple(LOADERS))
@pytest.mark.parametrize(
    ("before", "after", "selection", "host"),
    (
        (
            f'token_file = "{PRIVATE_TOKEN_PATH}"',
            'token_file = ""',
            {"gitlab": "public"},
            PUBLIC_HOST,
        ),
        (f'url = "https://{PUBLIC_HOST}"\n', "", {}, PRIVATE_HOST),
    ),
    ids=("invalid-private-while-public", "invalid-public-while-default"),
)
def test_unselected_instance_is_not_validated(
    target_path: Path,
    loader: str,
    before: str,
    after: str,
    selection: Mapping[str, str],
    host: str,
) -> None:
    """Validate only the instance the caller selected.

    :param target_path: Path receiving the target.
    :param loader: Public loader exercised by the case.
    :param before: Fragment of the unselected instance.
    :param after: Replacement making that instance invalid.
    :param selection: Caller selector keywords, or none.
    :param host: Expected selected authority.
    :returns: None; assertions verify the selected instance loads.
    """
    target_path.write_text(ROUTED_TARGET.replace(before, after, 1), encoding="utf-8")

    assert LOADERS[loader](target_path, **selection).host == host


@pytest.mark.parametrize("loader", tuple(LOADERS))
def test_selected_instance_does_not_inherit_the_other_instance(
    target_path: Path, loader: str
) -> None:
    """Keep optional fields absent when only the other instance declares them.

    :param target_path: Path receiving a public table holding only its URL.
    :param loader: Public loader selecting the public instance.
    :returns: None; assertions verify that absent fields stay absent.
    """
    target_path.write_text(
        ROUTED_TARGET.replace(
            PUBLIC_TABLE, f'[gitlab_public]\nurl = "https://{PUBLIC_HOST}"\n'
        ),
        encoding="utf-8",
    )

    selected = LOADERS[loader](target_path, gitlab="public")

    assert selected.host == PUBLIC_HOST
    assert (selected.project, selected.group, selected.runner_id) == (None, None, None)
    assert selected.token_file is None


def test_selector_for_a_command_without_gitlab_is_rejected(target_path: Path) -> None:
    """Refuse a GitLab selector when the command does not select GitLab.

    :param target_path: Path receiving the routed target.
    :returns: None; assertions verify the existing fail-closed reason.
    """
    target_path.write_text(ROUTED_TARGET, encoding="utf-8")

    with pytest.raises(TARGET.TargetError) as error:
        TARGET.load_target(
            target_path, required_surfaces=("kubernetes",), gitlab="public"
        )

    assert str(error.value) == "gitlab_profile_requires_gitlab_authority"


def test_route_as_dict_matches_the_routing_schema() -> None:
    """Render the result ``routing`` object and validate it against its schema.

    :returns: None; assertions verify the object, null fields, and rejections.
    """
    explicit = _route("gitlab_public", "kubernetes", "harbor", "argv:--gitlab=public")
    legacy = _route("gitlab", None, None, "legacy")

    assert explicit.as_dict() == {
        "gitlab": "gitlab_public",
        "kubernetes": "kubernetes",
        "harbor": "harbor",
        "selected_by": "argv:--gitlab=public",
    }
    assert legacy.as_dict() == {
        "gitlab": "gitlab",
        "kubernetes": None,
        "harbor": None,
        "selected_by": "legacy",
    }
    for document in (explicit.as_dict(), legacy.as_dict()):
        SCHEMA.validate_specification(document, ROUTING_SCHEMA)
    invalid = (
        {**explicit.as_dict(), "extra": "field"},
        {
            key: value
            for key, value in explicit.as_dict().items()
            if key != "selected_by"
        },
        {**explicit.as_dict(), "kubernetes": 1},
        {**explicit.as_dict(), "gitlab": ""},
    )
    for document in invalid:
        with pytest.raises(SCHEMA.SpecificationError):
            SCHEMA.validate_specification(document, ROUTING_SCHEMA)


@pytest.mark.parametrize("selection", ("explicit", "environment", "binding"))
def test_selection_preserves_target_and_binding_precedence(
    target_path: Path, selection: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Carry the selector through the chosen file without another-tier fallback.

    :param target_path: Path receiving the routed target.
    :param selection: Explicit target, environment target, or binding tier.
    :param monkeypatch: Fixture setting competing environment selectors.
    :returns: None; assertions verify file selection and selected instance.
    """
    target_path.write_text(ROUTED_TARGET, encoding="utf-8")
    explicit = None
    binding = None
    source = "argv:--target"
    if selection == "explicit":
        explicit = str(target_path)
        monkeypatch.setenv(BINDING.TARGET_ENV, str(target_path.parent / "missing"))
    elif selection == "environment":
        source = TARGET_ENV_SOURCE
        monkeypatch.setenv(BINDING.TARGET_ENV, str(target_path))
    else:
        path = target_path.parent / "binding.toml"
        path.write_text(
            'schema_version = "1.0"\ntarget = "target.toml"\n'
            '[kubernetes]\nsources = [{kind = "file", path = "missing-kubeconfig"}]\n',
            encoding="utf-8",
        )
        binding = str(path)
        source = "binding"
        monkeypatch.setenv(BINDING.TARGET_ENV, str(target_path.parent / "missing"))

    selected = BINDING.resolve_target(
        explicit,
        binding,
        required_surfaces=("gitlab",),
        gitlab_selection=TARGET.GitLabSelection(selector="public"),
    )

    assert selected.source_file == target_path.resolve()
    assert selected.source_kind == source
    assert selected.routing.gitlab == "gitlab_public"
    assert selected.gitlab.project == "consumer/public"


def test_identical_instances_have_distinct_confirmation_digests(
    target_path: Path,
) -> None:
    """Reject confirmation replay across instances with identical provider inputs.

    :param target_path: Target whose two instances hold identical settings.
    :returns: None; assertions verify the selector binds the action plan.
    """
    target_path.write_text(
        ROUTED_TARGET.replace(
            PUBLIC_TABLE, PRIVATE_TABLE.replace("[gitlab_private]", "[gitlab_public]")
        ),
        encoding="utf-8",
    )
    plans = []
    for selector in ("private", "public"):
        args = ISSUE.build_parser().parse_args(
            ["open-bug", "--title", "fixture", "--gitlab", selector]
        )
        args.target = str(target_path)
        workflow = GITLAB_ACTION.GitLabWorkflow.from_args(args)
        workflow.prepare()
        plans.append(workflow.state.plan)
    first, second = plans

    assert (first.origin, first.target_reference, first.target_file, first.body) == (
        second.origin,
        second.target_reference,
        second.target_file,
        second.body,
    )
    assert first.digest != second.digest
    assert ";argv:--gitlab=private;" in first.target_source
    assert ";argv:--gitlab=public;" in second.target_source
    args = ISSUE.build_parser().parse_args(
        [
            "open-bug",
            "--title",
            "fixture",
            "--gitlab",
            "public",
            "--apply",
            "--confirm-plan",
            first.digest,
        ]
    )
    args.target = str(target_path)
    with pytest.raises(GITLAB_ACTION.ActionError, match="confirm_plan_mismatch"):
        GITLAB_ACTION.GitLabWorkflow.from_args(args).prepare()


@pytest.mark.parametrize("operation", ("check", "get"))
def test_api_cli_resolves_selected_host_credential_and_route(
    target_path: Path, operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the selected instance in both parsed API operation adapters.

    :param target_path: Path receiving the routed target.
    :param operation: Identity check or provider-relative GET operation.
    :param monkeypatch: Fixture selecting the target file.
    :returns: None; assertions verify host, credential, and route agree.
    """
    target_path.write_text(ROUTED_TARGET, encoding="utf-8")
    monkeypatch.setenv(BINDING.TARGET_ENV, str(target_path))
    argv = [operation, "--provider", "gitlab"]
    if operation == "get":
        argv.extend(["--endpoint", "projects?membership=true"])
    args = READ_CLI.build_api_parser().parse_args([*argv, "--gitlab", "public"])
    callbacks = READ_CLI._api_callbacks(args, API_READ.ApiOutput.JSON)
    action = callbacks.callbacks[0].action

    action.validate()
    selected = API_READ.ApiProviderSelection.from_action(action)

    assert action.gitlab_selection == TARGET.GitLabSelection(selector="public")
    assert selected.host == PUBLIC_HOST
    assert selected.token_file == (target_path.parent / PUBLIC_TOKEN_PATH).resolve()
    assert selected.route == _route(
        "gitlab_public", "kubernetes", "harbor", "argv:--gitlab=public"
    )


@pytest.mark.parametrize("violation", ("github-provider", "conflicting-host"))
def test_api_selection_conflicts_reject_before_credential_binding(
    target_path: Path, violation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse one invalid request invariant before native authentication.

    :param target_path: Path receiving the routed target.
    :param violation: Provider or host invariant changed from a valid request.
    :param monkeypatch: Fixture selecting the target and trapping credential reads.
    :returns: None; assertions verify rejection without reading a credential.
    """
    target_path.write_text(ROUTED_TARGET, encoding="utf-8")
    monkeypatch.setenv(BINDING.TARGET_ENV, str(target_path))
    monkeypatch.setattr(
        API_READ,
        "_token_source",
        lambda *args, **kwargs: pytest.fail("credential read for rejected selection"),
    )
    argv = ["check", "--provider", "gitlab", "--gitlab", "public"]
    if violation == "github-provider":
        argv[2] = "github"
    else:
        argv.extend(["--host", "other.example.test"])
    args = READ_CLI.build_api_parser().parse_args(argv)

    with pytest.raises((API_READ.ReadToolError, TARGET.TargetError)):
        callbacks = READ_CLI._api_callbacks(args, API_READ.ApiOutput.JSON)
        action = callbacks.callbacks[0].action
        action.validate()
        API_READ.ApiProviderSelection.from_action(action)


OPEN_BUG: Final[tuple[str, ...]] = (
    "open-bug",
    "--project",
    "team/app",
    "--title",
    "t",
    "--json",
)


@pytest.mark.parametrize(
    ("body", "argv", "origin", "routing", "target_source", "deprecations"),
    (
        (
            ROUTED_TARGET,
            (),
            f"https://{PRIVATE_HOST}",
            ("gitlab_private", "kubernetes", "harbor", "default"),
            f"{TARGET_ENV_SOURCE};argv:--project",
            0,
        ),
        (
            ROUTED_TARGET,
            ("--gitlab", "public"),
            f"https://{PUBLIC_HOST}",
            ("gitlab_public", "kubernetes", "harbor", "argv:--gitlab=public"),
            f"{TARGET_ENV_SOURCE};argv:--gitlab=public;argv:--project",
            0,
        ),
        (
            ISSUE_TARGET,
            (),
            f"https://{PRIVATE_HOST}",
            ("gitlab", None, None, "legacy"),
            f"{TARGET_ENV_SOURCE};argv:--project",
            1,
        ),
        (
            ISSUE_TARGET,
            ("--gitlab-profile", "com"),
            f"https://{PUBLIC_HOST}",
            ("gitlab.com", None, None, 'argv:--gitlab-profile="com"'),
            f'{TARGET_ENV_SOURCE};argv:--gitlab-profile="com";argv:--project',
            1,
        ),
    ),
    ids=("routed-default", "routed-public", "legacy-issue-133", "legacy-profile"),
)
def test_open_bug_dry_run_reports_origin_and_routing(
    target_path: Path,
    body: str,
    argv: tuple[str, ...],
    origin: str,
    routing: tuple[str, Optional[str], Optional[str], str],
    target_source: str,
    deprecations: int,
) -> None:
    """Run the issue #133 command against a direct target and read its result.

    :param target_path: Path receiving the target named by ``CI_SKILLS_TARGET``.
    :param body: Complete target TOML.
    :param argv: Selector arguments appended to the issue command.
    :param origin: Expected GitLab origin.
    :param routing: Expected ``routing`` fields in declaration order.
    :param target_source: Expected selection provenance.
    :param deprecations: Expected count of deprecation events on stderr.
    :returns: None; assertions verify the DRY_RUN envelope and its schema.
    """
    target_path.write_text(body, encoding="utf-8")

    result = run_script(
        "gitlab_issue.py",
        *OPEN_BUG,
        *argv,
        env={BINDING.TARGET_ENV: str(target_path)},
    )
    data = parse_json_output(result)

    assert result.returncode == 0, result.stderr
    assert data["status"] == "DRY_RUN"
    assert data["phase"] == "OFFLINE_PLAN"
    assert data["schema_version"] == "1.1"
    assert data["origin"] == origin
    assert data["routing"] == _route(*routing).as_dict()
    assert data["target_source"] == target_source
    assert result.stderr.count(DEPRECATION_EVENT) == deprecations
    for secret in (PRIVATE_TOKEN_TEXT, PUBLIC_TOKEN_TEXT):
        assert secret not in result.stdout + result.stderr
    SCHEMA.validate_specification(data, ACTION_SCHEMA)


@pytest.mark.parametrize(
    ("body", "argv", "reason"),
    (
        (SINGLE_TARGET, ("--gitlab", "public"), "gitlab_public_target_required"),
        (LEGACY_TARGET + PUBLIC_TABLE, (), "gitlab_target_shapes_mixed"),
    ),
    ids=("public-not-declared", "mixed-shapes"),
)
def test_open_bug_blocks_on_an_unusable_selection(
    target_path: Path, body: str, argv: tuple[str, ...], reason: str
) -> None:
    """Block before any provider call when the selection cannot be satisfied.

    :param target_path: Path receiving the target named by ``CI_SKILLS_TARGET``.
    :param body: Complete target TOML.
    :param argv: Selector arguments appended to the issue command.
    :param reason: Exact reason expected in the BLOCKED result.
    :returns: None; assertions verify the BLOCKED envelope.
    """
    target_path.write_text(body, encoding="utf-8")

    result = run_script(
        "gitlab_issue.py",
        *OPEN_BUG,
        *argv,
        env={BINDING.TARGET_ENV: str(target_path)},
    )
    data = parse_json_output(result)

    assert result.returncode == 2
    assert data["status"] == "BLOCKED"
    assert [error["reason"] for error in data["errors"]] == [reason]


def test_open_bug_rejects_an_unknown_selector_choice(target_path: Path) -> None:
    """Accept only the selector choices declared by the contract.

    :param target_path: Path receiving the routed target.
    :returns: None; assertions verify the argument failure before target loading.
    """
    target_path.write_text(ROUTED_TARGET, encoding="utf-8")

    result = run_script(
        "gitlab_issue.py",
        *OPEN_BUG,
        "--gitlab",
        "other",
        env={BINDING.TARGET_ENV: str(target_path)},
    )
    data = parse_json_output(result)

    assert result.returncode == 2
    assert data["status"] == "BLOCKED"
    assert data["errors"][0]["source"] == "arguments"
    assert "routing" not in data


def test_open_bug_human_view_prints_the_routing_line(target_path: Path) -> None:
    """Show the selected GitLab and its pairing in the human summary.

    :param target_path: Path receiving the routed target.
    :returns: None; assertions verify the exact human routing line.
    """
    target_path.write_text(ROUTED_TARGET, encoding="utf-8")

    result = run_script(
        "gitlab_issue.py",
        *OPEN_BUG[:-1],
        "--human",
        "--gitlab",
        "public",
        env={BINDING.TARGET_ENV: str(target_path)},
    )

    assert result.returncode == 0, result.stderr
    assert (
        "Routing: gitlab=gitlab_public kubernetes=kubernetes harbor=harbor "
        "(argv:--gitlab=public)"
    ) in result.stdout.splitlines()
