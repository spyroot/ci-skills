"""Offline regressions for isolated, caller-selected GitLab profiles.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final, Optional

import pytest
from conftest import import_script_module

TARGET = import_script_module("lib.python.core.target")
BINDING = import_script_module("lib.python.core.project_binding")
API_READ = import_script_module("lib.python.core.api_read")
READ_CLI = import_script_module("lib.python.core.read_cli")
GITLAB_ACTION = import_script_module("lib.python.core.gitlab.gitlab_action")
ISSUE = import_script_module("gitlab_issue")

NAMED_TABLE: Final[str] = (
    "[gitlab.com]\n"
    'url = "https://public.example.test"\n'
    'project = "consumer/public"\n'
    'token_file = "profiles/public.token"\n'
    "runner_id = 22\n"
)
BASELINE_TARGET: Final[str] = (
    "[github]\n"
    'host = "github.example.test"\n'
    'repository = "consumer/repo"\n'
    "[gitlab]\n"
    'url = "https://default.example.test"\n'
    'project = "consumer/default"\n'
    'token_file = "default.token"\n'
    "runner_id = 11\n"
    + NAMED_TABLE
    + NAMED_TABLE.replace("[gitlab.com]", "[gitlab.peer]")
    + "[kubernetes]\n"
    'context = "unit-context"\n'
    'server = "https://api.cluster.example.test:6443"\n'
)


@pytest.fixture
def profile_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create the shared baseline and isolate ambient target selection.

    :param tmp_path: Directory containing only synthetic target and token files.
    :param monkeypatch: Fixture isolating home, cwd, and target selectors.
    :returns: Target path with distinct default and named GitLab settings.
    """
    directory = tmp_path / "configuration"
    (directory / "profiles").mkdir(parents=True)
    for name in ("default.token", "profiles/public.token"):
        (directory / name).write_text("unit-fixture-token\n", encoding="utf-8")
    path = directory / "target.toml"
    path.write_text(BASELINE_TARGET, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(home)
    for variable in (BINDING.TARGET_ENV, BINDING.BINDING_ENV, BINDING.SMOKE_ENV):
        monkeypatch.delenv(variable, raising=False)
    return path


@pytest.mark.parametrize("loader", ("load_target", "load_gitlab_target"))
@pytest.mark.parametrize(
    ("profile", "host", "project", "token", "runner"),
    (
        (None, "default.example.test", "consumer/default", "default.token", 11),
        ("com", "public.example.test", "consumer/public", "profiles/public.token", 22),
    ),
    ids=("legacy-default", "explicit-profile"),
)
def test_loaders_select_the_same_profile_without_changing_the_file(
    profile_target: Path,
    loader: str,
    profile: Optional[str],
    host: str,
    project: str,
    token: str,
    runner: int,
) -> None:
    """Preserve defaults and select complete named settings through both loaders.

    :param profile_target: Baseline whose credentials are target-relative.
    :param loader: Public target loader exercised by the case.
    :param profile: Named table, or None for the legacy flat table.
    :param host: Expected GitLab authority.
    :param project: Expected consumer project.
    :param token: Expected target-relative credential path.
    :param runner: Expected runner ID.
    :returns: None; assertions verify selection and unchanged input bytes.
    """
    selected = getattr(TARGET, loader)(profile_target, gitlab_profile=profile).gitlab

    assert selected.url == f"https://{host}"
    assert selected.host == host
    assert selected.project == project
    assert selected.runner_id == runner
    assert selected.group is None
    assert selected.profile == profile
    assert selected.token_file == (profile_target.parent / token).resolve()
    assert profile_target.read_text(encoding="utf-8") == BASELINE_TARGET


@pytest.mark.parametrize("name", ("release.v1", "smoke candidate"))
def test_quoted_profile_names_remain_exact_selectors(
    profile_target: Path, name: str
) -> None:
    """Accept TOML quoted immediate-child names without rewriting their identity.

    :param profile_target: Baseline whose selected named table is renamed.
    :param name: Exact quoted TOML key selected by the caller.
    :returns: None; assertions verify the exact key and unambiguous provenance.
    """
    profile_target.write_text(
        BASELINE_TARGET.replace("[gitlab.com]", f"[gitlab.{json.dumps(name)}]", 1),
        encoding="utf-8",
    )

    selected = TARGET.load_gitlab_target(profile_target, gitlab_profile=name)

    assert selected.gitlab.profile == name
    assert selected.gitlab.host == "public.example.test"
    assert selected.gitlab.selection_source("target") == (
        f"target;argv:--gitlab-profile={json.dumps(name)}"
    )


@pytest.mark.parametrize("loader", ("load_target", "load_gitlab_target"))
def test_selected_profile_does_not_inherit_optional_defaults(
    profile_target: Path, loader: str
) -> None:
    """Allow native credentials without borrowing default scopes or files.

    :param profile_target: Target retaining a populated default GitLab table.
    :param loader: Public loader selecting a profile containing only its own URL.
    :returns: None; assertions prove absent optional inputs remain absent.
    """
    profile_target.write_text(
        BASELINE_TARGET.replace(
            NAMED_TABLE, '[gitlab.com]\nurl = "https://public.example.test"\n', 1
        ),
        encoding="utf-8",
    )

    selected = getattr(TARGET, loader)(profile_target, gitlab_profile="com").gitlab

    assert selected.host == "public.example.test"
    assert selected.project is None
    assert selected.group is None
    assert selected.runner_id is None
    assert selected.token_file is None


@pytest.mark.parametrize("loader", ("load_target", "load_gitlab_target"))
def test_named_selection_does_not_require_flat_default_settings(
    profile_target: Path, loader: str
) -> None:
    """Select a named profile without a configured legacy default URL.

    :param profile_target: Baseline whose flat GitLab settings are removed.
    :param loader: Public loader selecting the remaining named profile.
    :returns: None; assertions verify selection needs no default profile.
    """
    default = BASELINE_TARGET.split("[gitlab]\n", 1)[1].split("[gitlab.com]", 1)[0]
    profile_target.write_text(BASELINE_TARGET.replace(default, "", 1), encoding="utf-8")

    selected = getattr(TARGET, loader)(profile_target, gitlab_profile="com").gitlab

    assert selected.host == "public.example.test"
    assert selected.profile == "com"


@pytest.mark.parametrize("loader", ("load_target", "load_gitlab_target"))
@pytest.mark.parametrize(
    ("before", "after", "profile"),
    (
        ("", "", "missing"),
        ("", "", ""),
        ("", "", " "),
        ("", "", "url"),
        ('url = "https://public.example.test"\n', "", "com"),
        ('url = "https://public.example.test"', 'url = ""', "com"),
        (
            'url = "https://public.example.test"',
            'url = "https://public.example.test/consumer"',
            "com",
        ),
        ('project = "consumer/public"', 'project = "/consumer/public"', "com"),
        ('token_file = "profiles/public.token"', 'token_file = ""', "com"),
        ("runner_id = 22", "runner_id = 0", "com"),
        ("[gitlab.com]\n", '[gitlab.com]\nunknown = "fixture"\n', "com"),
        (NAMED_TABLE, 'com = "not-a-table"\n', "com"),
        ('url = "https://default.example.test"\n', "", None),
    ),
    ids=(
        "unknown-selection",
        "empty-selection",
        "blank-selection",
        "reserved-selection-name",
        "missing-selected-url",
        "empty-selected-url",
        "selected-url-is-not-origin",
        "invalid-selected-project",
        "empty-selected-token-path",
        "invalid-selected-runner",
        "unknown-selected-field",
        "profile-is-not-table",
        "omitted-selection-needs-default-url",
    ),
)
def test_invalid_profile_selection_never_falls_back_to_the_default(
    profile_target: Path,
    loader: str,
    before: str,
    after: str,
    profile: Optional[str],
) -> None:
    """Reject one broken invariant in an isolated immutable-baseline copy.

    :param profile_target: Fresh baseline copy for this rejection case.
    :param loader: Public loader whose fail-closed selection is checked.
    :param before: Single fragment to replace, or empty for selector cases.
    :param after: Replacement introducing the selected invalid invariant.
    :param profile: Selection, or None to exercise a missing default URL.
    :returns: None; assertions verify rejection rather than substitution.
    """
    body = BASELINE_TARGET.replace(before, after, 1) if before else BASELINE_TARGET
    profile_target.write_text(body, encoding="utf-8")

    with pytest.raises(TARGET.TargetError):
        getattr(TARGET, loader)(profile_target, gitlab_profile=profile)

    assert profile_target.read_text(encoding="utf-8") == body


@pytest.mark.parametrize("loader", ("load_target", "load_gitlab_target"))
def test_unselected_credential_path_is_not_validated(
    profile_target: Path, loader: str
) -> None:
    """Ignore an invalid default credential path when a named profile is selected.

    :param profile_target: Baseline with valid named and default credentials.
    :param loader: Public loader selecting the named profile only.
    :returns: None; assertions verify selection uses only its own credential path.
    """
    profile_target.write_text(
        BASELINE_TARGET.replace('token_file = "default.token"', 'token_file = ""', 1),
        encoding="utf-8",
    )

    selected = getattr(TARGET, loader)(profile_target, gitlab_profile="com").gitlab

    assert (
        selected.token_file
        == (profile_target.parent / "profiles/public.token").resolve()
    )


@pytest.mark.parametrize("selection", ("explicit", "environment", "binding"))
def test_profile_selection_preserves_target_and_binding_precedence(
    profile_target: Path, selection: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Carry named selection through the chosen file without another-tier fallback.

    :param profile_target: Target providing the named table for each selector case.
    :param selection: Explicit target, environment target, or binding tier.
    :param monkeypatch: Fixture setting competing environment selectors.
    :returns: None; assertions preserve file selection and binding provenance.
    """
    explicit = None
    binding = None
    source = "argv:--target"
    if selection == "explicit":
        explicit = str(profile_target)
        monkeypatch.setenv(BINDING.TARGET_ENV, str(profile_target.parent / "missing"))
    elif selection == "environment":
        source = f"env:{BINDING.TARGET_ENV}"
        monkeypatch.setenv(BINDING.TARGET_ENV, str(profile_target))
    else:
        path = profile_target.parent / "binding.toml"
        path.write_text(
            'schema_version = "1.0"\ntarget = "target.toml"\n'
            '[kubernetes]\nsources = [{kind = "file", path = "missing-kubeconfig"}]\n',
            encoding="utf-8",
        )
        binding = str(path)
        source = "binding"
        monkeypatch.setenv(BINDING.TARGET_ENV, str(profile_target.parent / "missing"))

    selected = BINDING.resolve_target(
        explicit, binding, required_surfaces=("gitlab",), gitlab_profile="com"
    )

    assert selected.source_file == profile_target.resolve()
    assert selected.source_kind == source
    assert selected.gitlab.profile == "com"
    assert selected.gitlab.project == "consumer/public"


def test_same_host_profiles_have_distinct_confirmation_digests(
    profile_target: Path,
) -> None:
    """Reject confirmation replay across profiles with identical provider inputs.

    :param profile_target: Target whose two names select identical GitLab settings.
    :returns: None; assertions verify profile provenance binds real action plans.
    """
    workflows = []
    for profile in ("com", "peer"):
        args = ISSUE.build_parser().parse_args(
            ["open-bug", "--title", "fixture", "--gitlab-profile", profile]
        )
        args.target = str(profile_target)
        workflow = GITLAB_ACTION.GitLabWorkflow.from_args(args)
        workflow.prepare()
        workflows.append(workflow)
    first, second = (workflow.state.plan for workflow in workflows)

    assert (first.origin, first.target_reference, first.target_file, first.body) == (
        second.origin,
        second.target_reference,
        second.target_file,
        second.body,
    )
    assert first.digest != second.digest
    assert 'argv:--gitlab-profile="com"' in first.target_source
    assert 'argv:--gitlab-profile="peer"' in second.target_source
    args = ISSUE.build_parser().parse_args(
        [
            "open-bug",
            "--title",
            "fixture",
            "--gitlab-profile",
            "peer",
            "--apply",
            "--confirm-plan",
            first.digest,
        ]
    )
    args.target = str(profile_target)
    with pytest.raises(GITLAB_ACTION.ActionError, match="confirm_plan_mismatch"):
        GITLAB_ACTION.GitLabWorkflow.from_args(args).prepare()


@pytest.mark.parametrize("operation", ("check", "get"))
def test_api_cli_resolves_profile_host_and_credential_together(
    profile_target: Path, operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the named authority in both parsed API operation adapters.

    :param profile_target: Target with distinct default and named authorities.
    :param operation: Identity check or provider-relative GET operation.
    :param monkeypatch: Fixture selecting the profile-bearing target file.
    :returns: None; assertions verify parsed actions select matching credentials.
    """
    monkeypatch.setenv(BINDING.TARGET_ENV, str(profile_target))
    argv = [operation, "--provider", "gitlab"]
    if operation == "get":
        argv.extend(["--endpoint", "projects?membership=true"])
    args = READ_CLI.build_api_parser().parse_args([*argv, "--gitlab-profile", "com"])
    callbacks = READ_CLI._api_callbacks(args, API_READ.ApiOutput.JSON)
    action = callbacks.callbacks[0].action

    action.validate()
    selected = API_READ.ApiProviderSelection.from_action(action)

    assert action.gitlab_profile == "com"
    assert selected.host == "public.example.test"
    assert (
        selected.token_file
        == (profile_target.parent / "profiles/public.token").resolve()
    )


@pytest.mark.parametrize("violation", ("github-provider", "conflicting-host"))
def test_api_profile_conflicts_reject_before_credential_binding(
    profile_target: Path, violation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse one invalid request invariant before native authentication.

    :param profile_target: Target supplying a valid named GitLab profile.
    :param violation: Provider or host invariant changed from a valid request.
    :param monkeypatch: Fixture selecting the target and trapping credential reads.
    :returns: None; assertions verify rejection without reading a credential.
    """
    monkeypatch.setenv(BINDING.TARGET_ENV, str(profile_target))
    monkeypatch.setattr(
        API_READ,
        "_token_source",
        lambda *args, **kwargs: pytest.fail("credential read for rejected profile"),
    )
    argv = ["check", "--provider", "gitlab", "--gitlab-profile", "com"]
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
