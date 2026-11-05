# Select a private or public GitLab with explicit Kubernetes and Harbor routing

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and
`Outcomes & Retrospective` must be kept up to date as work proceeds. It is maintained in accordance with
[docs/PLANS.md](../../PLANS.md).

## Purpose / Big Picture

After this change, one target file can declare a private GitLab and a public GitLab side by side, without the TOML
nesting trap reported in [issue #133](https://github.com/spyroot/ci-skills/issues/133). The caller chooses one with a
single flag, `--gitlab private` or `--gitlab public`, on every GitLab command. The target file itself answers, before
any provider request, three questions:

- which GitLab is used when the flag is omitted;
- which Kubernetes cluster pairs with each GitLab;
- which Harbor registry pairs with each GitLab.

Every result, including an offline plan and a `BLOCKED` result, reports that answer in one `routing` object, and the
human view prints it on one line. To see it work, run a GitLab dry-run with and without `--gitlab public` and compare
the `routing` object and `origin`.

## Progress

- [x] (DEC-01–DEC-09) Design accepted.
- [x] (GPP-01) This ExecPlan and its index row are committed.
- [x] (GPP-02) Table-driven regression tests are committed and fail on the base revision. Tests:
  `tests/python/test_target_profiles.py` (selection, routing, field errors, CLI rows), `conftest.py` `target_file` in
  the new shape, and rows in `test_endpoints.py`, `test_catalog.py`, `test_report_human.py`, `test_target_runtime.py`,
  `test_gitlab_mirror.py` and `test_gitlab_actions.py` (commits `66eee42`, `2ed6503`). Evidence: run
  [42127530173](https://github.com/spyroot/ci-skills/actions/runs/42127530173) on `2ed6503`: 197 failed, 7 errors.
  New rows: `test_target_profiles.py` 105, `test_endpoints.py` 4, `test_gitlab_mirror.py` 4, `test_catalog.py` 2,
  `test_report_human.py` 2, `test_gitlab_actions.py` 2. Fixture cascade from the routed `target_file`:
  `test_collectors.py` 34, `test_access_core.py` 15, `test_target_runtime.py` 13, `test_node_local_cli.py` 9,
  `test_node_pod.py` 7 errors, `test_cli_contract.py` 5, `test_status_cli.py` 4, `test_live_gate.py` 2.
- [x] (GPP-03) The contract names are defined once in `core/endpoints.py`; `[gitlab_routing]` is parsed and validated.
- [x] (GPP-04) The `--gitlab` selector joins `--gitlab-profile` on every GitLab command; the legacy path warns.
- [x] (GPP-05) `routing` is in the GitLab action JSON and every human view that receives it; the action and runner
  result schemas accept 1.1. Scope limits are in Surprises.
- [x] (GPP-06) The template, references, README and `tools.json` are updated.
- [ ] (GPP-07) Both installed copies are upgraded, with AFTER evidence against the selected target.
- [ ] (GPP-08) The candidate receipts are renewed and `validate` is green on the PR head.
- [ ] (GPP-09) Legacy removal is tracked as TD-11.

## Surprises & Discoveries

- Observation: the reported failure reproduced only on a stale installed copy. The source parse was already fixed by
  PR #129 (`690812b`).
  Evidence: an install at `fb8b5ac` returned `BLOCKED gitlab contains unsupported fields: com`. An install at
  `925b898` returned `DRY_RUN` for the same target and command.
- Observation: TOML treats a dot in a table header as nesting.
  Evidence: `tomllib` parses `[gitlab.com]` as `{"gitlab": {"com": {...}}}`, `["gitlab.com"]` as
  `{"gitlab.com": {...}}`, and `[gitlab_public]` as `{"gitlab_public": {...}}`.
- Observation: on the base revision the selected profile appears only inside the `target_source` string. No
  structured field names it; the human view prints neither the origin nor the selection; `ci-api` output drops it.
  Evidence: `target_source` was `env:CI_SKILLS_TARGET;argv:--gitlab-profile="com";argv:--project`. The human view in
  `core/report.py` prints kind, status, target and record count only.
- Observation: `image_build_readiness.py probe` accepts `--gitlab-profile` but rejects it at load, because `probe`
  requires only `kubernetes`.
  Evidence: `core/target.py` raises `gitlab_profile_requires_gitlab_authority`.
- Observation: live acceptance requires receipt `schema_version` `1.0`.
  Evidence: `tools/check_live_acceptance.py:583` reports `schema_version_unexpected` otherwise. The `access_check` and
  `gitlab_mirror` results are the required receipts, so they stay `1.0` and carry no `routing` in this change; moving
  them needs a change to that gate first. GitLab action results (`gitlab-action`, `gitlab-runner-tag`, `-read`,
  `-delete` schemas) emit `1.1` with `routing`. Job list, pipeline watch, runner manager and image-build chain results
  do not carry `routing` yet.
- Observation: the endpoint gate's `undocumented` rule compared raw template paths with declared paths, so
  `[gitlab_private]` did not document `gitlab.url`.
  Evidence: `make bless` reported `undocumented: gitlab.url`. `tools/skillkit/endpoint_gate.py` now folds template
  paths through `TargetContract.canonical_path` for that rule, as `_introduced` already did. The portability gate
  scans `TargetContract.tables` headers and does not classify `[gitlab_routing]` values as hosts.
- Observation: `ci-api` prints fixed wire-format objects (`PlanAPICheck`, `PlanAPI`, `ApiIdentitySelector`).
  Evidence: `core/api_read.py`. `ci-api` takes host and token file from the selected instance and keeps the route on
  `ApiProviderSelection.route`; its printed output is unchanged.
- Observation: the mirror NO_OP candidate receipt must carry a `smoke_cleanup` record of kind
  `gitlab_mirror_smoke_cleanup`. No tracked tool produces that record.
  Evidence: `tools/check_live_acceptance.py` `_mirror_smoke_cleanup_verified`.

## Decision Log

- Decision DEC-01: GitLab stays one internal authority, `gitlab`. A target declares up to two instance tables for it.
  Status: ACCEPTED
  Rationale: The authority name is used in command requirements, access protocol, live-acceptance surfaces and two
  contract schemas' enums. Keeping it leaves all of them unchanged. Two separate authorities would roughly triple the
  files touched and force new major versions of `schemas/command-contract.schema.json` and
  `schemas/skill-manifest.schema.json`.
  Governing Ref: issue #133; `docs/phases/CI07-SCHEMA.md` MAJOR/MINOR rules.
- Decision DEC-02: A single `[gitlab_routing]` table declares the default GitLab and the Kubernetes and Harbor tables
  that pair with each GitLab.
  Status: ACCEPTED
  Rationale: One place answers the default and both pairings before any call. GitLab, Kubernetes and Harbor tables
  keep only endpoint, access and value fields.
  Governing Ref: issue #133 Expected section.
- Decision DEC-03: The CLI selector is `--gitlab private|public`. Its choices and its table mapping come from the
  contract constants. Omitting it selects `[gitlab_routing].default`.
  Status: ACCEPTED
  Rationale: One option name keeps one meaning across commands, as `docs/phases/CI02-CLI.md` requires.
  Governing Ref: `docs/phases/CI02-CLI.md` interface rules.
- Decision DEC-04: Results carry a top-level `routing` object. It is not named `profile`.
  Status: ACCEPTED
  Rationale: `profile` already means the diagnostic run mode in `core/cli.py` and `core/access/shared.py`.
  Governing Ref: `core/cli.py` gate profile field.
- Decision DEC-05: The legacy shape (`[gitlab]`, `[gitlab.NAME]`, `--gitlab-profile`) is accepted for one release.
  Every use writes a deprecation warning. A target that mixes the legacy and new shapes is rejected.
  Status: ACCEPTED
  Rationale: Existing user-level targets keep working while their owners migrate. Mixing shapes would make the
  default ambiguous. TD-11 tracks the removal.
  Governing Ref: `docs/PLANS.md` parallel-implementation guidance.
- Decision DEC-06: Authority table names use `_`, never `.`. A dot appears only where nesting is real, as in
  `[kubernetes.node_diagnostics]`.
  Status: ACCEPTED
  Rationale: This prevents the nesting trap of issue #133.
  Governing Ref: Surprises entry on TOML nesting.
- Decision DEC-07: Every new name is defined once, as a constant in `ci-skills/lib/python/core/endpoints.py`. The
  catalog, parser, help text, result fields, tests and docs derive from those constants or are checked against them.
  Status: ACCEPTED
  Rationale: Repository rule: define constant strings once.
  Governing Ref: repository agent rules on constants.
- Decision DEC-08: `project`, `group` and `runner_id` stay optional in each GitLab instance table, exactly as in the
  flat table today. When `project` is absent the caller passes `--project`; `project = ""` stays rejected. Examples
  and the template omit `project`, so the caller always names the project.
  Status: ACCEPTED
  Rationale: This preserves current behavior and avoids a hidden default project. It deliberately differs from the
  shell-script guidance to use a known repository default: a user-level target serves many projects.
  Governing Ref: issue #133 acceptance; current `core/target.py` `_optional_reference` behavior.
- Decision DEC-09: Adding `routing` to the result schemas is a MINOR change. `schema_version` moves from `1.0` to `1.1`,
  and schemas that pin `1.0` accept both versions.
  Status: ACCEPTED
  Rationale: An added optional field keeps old consumers working, per `docs/phases/CI07-SCHEMA.md`. Historical
  receipts remain valid.
  Governing Ref: `docs/phases/CI07-SCHEMA.md` versioning rules.
- Decision DEC-10: In the legacy shape, `[gitlab]` is the private instance. `--gitlab private` reads it with
  `selected_by` `argv:--gitlab=private`; `--gitlab public` returns `gitlab_public_target_required`. Legacy routes report
  `kubernetes` and `harbor` as `null`, because the legacy shape has no `[gitlab_routing]`.
  Status: ACCEPTED
  Rationale: The operator stated that `gitlab` means the private GitLab. A legacy target has no pairing to report.
  Governing Ref: operator requirement 1, 2026-11-05; DEC-05.
- Decision DEC-11: The call chain carries one `GitLabSelection` value (`selector` from `--gitlab`, `profile` from
  `--gitlab-profile`) under the keyword `gitlab_selection`. `load_target` and `load_gitlab_target` keep the two string
  keywords of the Interfaces section. `GitLabTarget.route` replaces `GitLabTarget.profile`; `Target.routing` returns it.
  Status: ACCEPTED
  Rationale: One keyword through the chain instead of two; the loaders keep the interface this plan declares.
  Governing Ref: Concrete Steps, call-chain refactor.
- Decision DEC-12: `image_build_readiness.py` keeps one parser for `probe` and `chain`, so `probe` still accepts the
  selector option and rejects it at load with the existing `gitlab_profile_requires_gitlab_authority`. The same reason
  covers `--gitlab` on any command whose selected authorities exclude GitLab.
  Status: ACCEPTED
  Rationale: Removing the option from `probe` alone needs subparsers, a CLI change outside issue #133. Rejection before
  any provider call keeps the fail-closed behavior.
  Governing Ref: current `core/target.py` behavior.
- Decision DEC-13: Unknown keys in a routing row reuse the existing table diagnostics, for example
  `gitlab_routing.gitlab_private contains unsupported fields: registry` and
  `gitlab_routing.gitlab_private must be a TOML table`. A legacy profile error names the dotted table, for example
  `gitlab.com.url must be a nonempty string`. The deprecation is one `gitlab_target_deprecated` event on standard
  error, written through `core/logging.py:log_event`.
  Status: ACCEPTED
  Rationale: No new reason token where an existing diagnostic already names the problem; one logging facility.
  Governing Ref: `core/target.py` `_table`; `core/logging.py`.

## Outcomes & Retrospective

Not started.

## Context and Orientation

A target file is a TOML file that tells every ci-skills command which external systems to use. TOML is a
configuration format of `[table]` headers followed by `key = value` lines. Callers select the file with `--target`,
`CI_SKILLS_TARGET`, a project binding, or the default `~/.ci-skills/target.toml`. Each top-level table describes one
authority, meaning one external system the command may contact: `github`, `gitlab`, `harbor` or `kubernetes`. The
parser is `ci-skills/lib/python/core/target.py`, function `load_target`. The allowed tables and fields are declared
once in `ci-skills/lib/python/core/endpoints.py` as `TARGET_CONTRACT`. `ci-skills/lib/python/core/catalog.py` derives
the authority list `AUTHORITIES` from that contract and declares which authorities each command requires.

On the base revision, the GitLab authority is a flat `[gitlab]` table plus optional named profiles `[gitlab.NAME]`,
selected with `--gitlab-profile NAME`. Profiles were added in PR #129. The selection reaches the provider through:

- `core/cli.py` (parser and diagnostic target);
- `core/gitlab/cli.py` (`resolve_gitlab_target`);
- `core/project_binding.py` (`resolve_target`);
- `core/api_read.py` (`ci-api`).

The selected values become a `GitLabTarget` in `core/target.py`. Results are written by
`core/gitlab/gitlab_actions.py` (`_result`), `core/access/access.py`, `core/api_read.py` and `core/report.py`, which
renders JSON, YAML and the human view. Result schemas live in `ci-skills/schemas/results/`.

Three commands combine GitLab with other authorities:

- `ci-skills/bin/gitlab_runner_manager.py` deploys a runner on Kubernetes from a Harbor image and registers it in
  GitLab.
- `ci-skills/bin/image_build_readiness.py chain` does the same through a build chain.
- `ci-skills/bin/access_check.py` checks every configured authority.

These commands need to know which Kubernetes cluster and which Harbor registry belong to the selected GitLab. That is
the routing this plan adds.

Live acceptance is a CI check that compares committed receipts with the code under review. A receipt is a JSON result
a real host produced. The check is `tools/check_live_acceptance.py`, with policy in
`inventory/ci/live-acceptance.json`. It fingerprints `ci-skills/bin`, `ci-skills/lib` and `ci-skills/schemas`. Any
change there invalidates the receipts in `tests/acceptance/candidate/` until fresh ones are captured from an installed
copy.

## Plan of Work

First we commit this ExecPlan and its row in `docs/plans/index.md`.

Second, we commit the regression tests, so CI shows them failing against the unchanged code:

- the shared fixture `target_file` in `tests/python/conftest.py` moves to the new shape;
- `tests/python/test_target_profiles.py` becomes the selection and routing table;
- a direct-target CLI table runs `gitlab_issue.py open-bug --project ... --json`.

Third, we implement the change. We add the constants and the `Table` instance and routing declaration to
`core/endpoints.py`. We add a routing model to `core/target.py` and teach `_select_surfaces` to fold instance tables
into the `gitlab` authority. We replace `gitlab_profile` with a selection value along the call chain. We add `routing`
to results and to the human view, update the schemas, regenerate `ci-skills/tools.json`, and update the template and
references.

Fourth, we install the candidate into both skill locations and run the AFTER commands. Fifth, we capture fresh
candidate receipts and update the live-smoke inventory.

## Concrete Steps

All commands run from the repository root of the working checkout with the project conda environment's Python 3.11.

INTRODUCED `ci-skills/lib/python/core/endpoints.py` constants. Each name exists only here; every other module imports
it.

    GITLAB_AUTHORITY: Final = "gitlab"
    GITLAB_PRIVATE_TABLE: Final = "gitlab_private"
    GITLAB_PUBLIC_TABLE: Final = "gitlab_public"
    GITLAB_ROUTING_TABLE: Final = "gitlab_routing"
    GITLAB_ROUTING_DEFAULT_KEY: Final = "default"
    GITLAB_SELECTORS: Final = MappingProxyType({"private": GITLAB_PRIVATE_TABLE, "public": GITLAB_PUBLIC_TABLE})
    GITLAB_ROUTED_AUTHORITIES: Final = ("kubernetes", "harbor")

MODIFIED `TARGET_CONTRACT` in the same file. `Table(GITLAB_AUTHORITY, ...)` keeps its fields (`url`, `project`,
`group`, `token_file`, `runner_id`) and gains `instances=tuple(GITLAB_SELECTORS.values())`. Its
`named_profiles=True` stays only for the legacy release.

INTRODUCED: `TargetContract.authority_of(table) -> Optional[str]` maps `gitlab_private` and `gitlab_public` to
`gitlab`. `TargetContract.routing_table() -> str` returns `GITLAB_ROUTING_TABLE`.

MODIFIED `canonical_path`: it folds `gitlab_private.X` and `gitlab_public.X` into `gitlab.X`, so
`tools/skillkit/endpoint_gate.py` and `tools/skillkit/portability_gate.py` see the same declared paths.

INTRODUCED in `ci-skills/lib/python/core/target.py`:

    @dataclass(frozen=True, slots=True)
    class GitLabRoute:
        gitlab: str                 # table read: "gitlab_private", "gitlab_public", or legacy "gitlab" / "gitlab.NAME"
        kubernetes: Optional[str]   # table name, e.g. "kubernetes", or None when not routed
        harbor: Optional[str]       # table name, e.g. "harbor", or None when not routed
        selected_by: str            # "default", "argv:--gitlab=public", "legacy", or "argv:--gitlab-profile=NAME"

    @dataclass(frozen=True, slots=True)
    class GitLabRouting:
        default: str
        routes: Mapping[str, GitLabRoute]
        @classmethod
        def parse(cls, data: Mapping[str, object]) -> Self: ...
        def select(self, selector: Optional[str]) -> GitLabRoute: ...

Invariants enforced by `GitLabRouting.parse`:

- `[gitlab_routing]` must exist when `[gitlab_private]` or `[gitlab_public]` exists.
- `default` names a declared GitLab instance table.
- Each declared instance table has exactly one row.
- A row names no undeclared instance table.
- Row keys are only `kubernetes` and `harbor`, and each value names a declared table of that authority.
- A target with both legacy `[gitlab]` and any new table is rejected with `gitlab_target_shapes_mixed`.

New reason tokens:

- `gitlab_routing_required`
- `gitlab_routing_default_unknown`
- `gitlab_routing_row_missing`
- `gitlab_routing_row_unknown`
- `gitlab_routing_kubernetes_unknown`
- `gitlab_routing_harbor_unknown`
- `gitlab_private_target_required`
- `gitlab_public_target_required`
- `gitlab_target_shapes_mixed`
- `gitlab_selector_conflict` (both `--gitlab` and `--gitlab-profile`)

`load_target` gains `gitlab: Optional[str]`, holding a `GITLAB_SELECTORS` key, beside the legacy `gitlab_profile`.
`Target` gains `routing: Optional[GitLabRoute]`. Field errors name the table read, for example
`gitlab_public.url must be a nonempty string`.

REFACTOR the call chain. Rename the `gitlab_profile` keyword to a selection value and pass it through every caller:

- `core/cli.py`, `core/read_cli.py`, `core/api_read.py`;
- `core/project_binding.py`, `core/gitlab/cli.py`;
- `core/gitlab/gitlab_action.py`, `core/gitlab/gitlab_pipeline_start.py`;
- `core/gitlab/mirror/cli.py`;
- `core/gitlab/runner_manager/action.py`, `core/gitlab/runner_manager/callbacks.py`;
- `core/image_build_chain/model.py`, `core/image_build_readiness.py`;
- `ci-skills/bin/gitlab_access.py`, `ci-skills/bin/gitlab_pipeline.py`.

Pre-verification is the existing suite on the base revision. Post-verification is the same suite plus the new tables.
Combined commands (`gitlab_runner_manager.py`, `image_build_readiness.py chain`) read Kubernetes and Harbor through
`routing`. `image_build_readiness.py probe` no longer offers the selector.

MODIFIED `ci-skills/lib/python/core/catalog.py`:

- `GITLAB_TARGET_OPTION: Final = "--gitlab"` with `choices=tuple(GITLAB_SELECTORS)`;
- `GITLAB_PROFILE_OPTION` stays as the deprecated alias;
- `supports_gitlab_profile` becomes `supports_gitlab_selection` and gates both options;
- the help text states the default comes from `[gitlab_routing]`.

MODIFIED result envelopes in `core/gitlab/gitlab_actions.py`, `core/access/access.py`, `core/api_read.py` and the
runner manager. They emit:

    "routing": {"gitlab": "gitlab_public", "kubernetes": "kubernetes", "harbor": "harbor",
                "selected_by": "argv:--gitlab=public"}

MODIFIED `core/report.py` human view. It adds the line:

    Routing: gitlab=gitlab_public kubernetes=kubernetes harbor=harbor (argv:--gitlab=public)

INTRODUCED `ci-skills/schemas/results/gitlab-routing.schema.json`, Draft 2020-12. It has required keys `gitlab`
(string), `kubernetes` (string or null), `harbor` (string or null) and `selected_by` (string), with
`additionalProperties: false`.

MODIFIED: the ten result schemas that declare `target_source` gain an optional `routing` property (`$ref` to the new
file). Their `schema_version` accepts `1.0` and `1.1`; producers emit `1.1`.

MODIFIED docs:

- `target.toml.template`: the new shape, with no `project` default.
- `ci-skills/references/access.md`: the section "GitLab private and public targets", including the `_` rule.
- `README.md`, `ci-skills/SKILL.md`, `ci-skills/references/gitlab-pipeline.md`.
- `docs/2026-10-31/live-smoke-inventory.md`, `docs/exec-plans/active/ci15-runner-workflows.md`.
- `docs/phases/CI11-TOOLS.md`, `docs/phases/CI09-REFERENCE.md`: one pointer line each.
- `docs/exec-plans/tech-debt-tracker.md`: row TD-11.

Regenerate `ci-skills/tools.json` with:

    python tools/render_manifest.py

Variables (no new variable is introduced):

| Variable Name | Type | Default / Required | Source / Scope | Description & Invariants |
| --- | --- | --- | --- | --- |
| `CI_SKILLS_TARGET` | `path` | Optional | Caller environment | Selects the target file; unchanged precedence |
| `GITLAB_TOKEN` | `secret` | Optional | Caller environment | Only without `token_file`; not shared across tables |
| `GITLAB_ACCESS_TOKEN` | `secret` | Optional | Caller environment | Same rule as `GITLAB_TOKEN` |
| `OAUTH_TOKEN` | `secret` | Optional | Caller environment | Same rule as `GITLAB_TOKEN` |

CI pipelines and jobs relied on (no new job is introduced):

| Pipeline / Job Name | Type / Trigger | Stage / Order | File Path | Required Inputs & Artifacts |
| --- | --- | --- | --- | --- |
| `validate` | PR, push to main | 1 | `.github/workflows/validate.yml` | Lint, schemas, pytest, live acceptance |
| `gate-ci-skills-endpoints` | PR | parallel | `.github/workflows/validate.yml` | Template and skill Markdown |
| `test-install` | PR | parallel | `.github/workflows/validate.yml` | Installed copy, outside the source |
| `required-result` | PR | last | `.github/workflows/validate.yml` | Results of the jobs above |

Verification gates relied on (all existing):

| Gate ID | Gate Type / Class | Pass Condition | Fail-Closed Condition | Producer / Owner |
| --- | --- | --- | --- | --- |
| `pytest` | unit | Suite and new selection tables pass | Any failure | `validate` job |
| `live-acceptance` | receipt | Receipts match the digest | Mismatch or missing operation | `check_live_acceptance.py` |
| `endpoints` | contract docs | Paths documented and paired | Undocumented or unpaired path | `endpoint_gate.py` |
| `portability` | static scan | No fixed deployment selector | Any hard-coded selector | `portability_gate.py` |
| `bless` | pre-commit | Staged files pass format, lint and neutrality | Any finding | `bless.sh` |

## Validation and Acceptance

Run `python tools/check_project_neutrality.py --root . --json`, `ruff check .`, `ruff format --check .` and
`python tools/render_manifest.py --check` locally. Tests run in CI. The new selection tests in
`tests/python/test_target_profiles.py` fail on the base revision and pass after the change.

Acceptance is the installed behavior against a target of this shape:

    [gitlab_routing]
    default = "gitlab_private"
    gitlab_private = { kubernetes = "kubernetes", harbor = "harbor" }
    gitlab_public  = { kubernetes = "kubernetes", harbor = "harbor" }

    [gitlab_private]
    url = "https://gitlab.example.test"
    token_file = "gitlab-private.token"

    [gitlab_public]
    url = "https://gitlab.public.example.test"
    token_file = "gitlab-public.token"

Expected results:

- `gitlab_issue.py open-bug --project team/app --title t --json` returns `DRY_RUN`, `origin` from `[gitlab_private]`,
  and `routing.selected_by` `default`.
- Adding `--gitlab public` returns `origin` from `[gitlab_public]` and `routing.gitlab` `gitlab_public`.
- `--gitlab public` with no `[gitlab_public]` returns `BLOCKED` with `gitlab_public_target_required`.
- A legacy `[gitlab]` plus `[gitlab.com]` target, the shape of issue #133, returns `DRY_RUN` with `routing.gitlab`
  `gitlab`, `selected_by` `legacy`, and one deprecation warning on standard error.

## Idempotence and Recovery

Dry-runs and offline plans make no provider request and can be repeated. Installer upgrades follow the installer's
own plan, confirm and recover path, so a failed upgrade is resumed with `install_ci_skills.py --recover`. Receipt
renewal moves superseded receipts to `tests/acceptance/historical/pre-gitlab-private-public/` and never deletes them.
The mirror qualification creates only a run-created destination and mirror, and removes only those IDs.

## Artifacts and Notes

BEFORE, the issue #133 target and command on an install at `fb8b5ac`:

    {"status": "BLOCKED", "errors": [{"reason": "gitlab contains unsupported fields: com", "source": "gitlab_action"}]}

BEFORE, the same on an install at `925b898`:

    {"status": "DRY_RUN", "phase": "OFFLINE_PLAN", "origin": "<private GitLab origin>",
     "target_source": "env:CI_SKILLS_TARGET;argv:--project"}

## Interfaces and Dependencies

No new library is used; `tomllib` from the Python 3.11 standard library parses targets.

In `ci-skills/lib/python/core/target.py`, the following must exist at the end:

    def load_target(path, *, required_surfaces=..., optional_surfaces=(), gitlab: Optional[str] = None,
                    gitlab_profile: Optional[str] = None) -> Target
    class GitLabRouting: parse(data) -> Self; select(selector) -> GitLabRoute

In `ci-skills/lib/python/core/endpoints.py`, the constants listed in Concrete Steps and
`TargetContract.authority_of`.

Revision note: initial version, derived from issue #133 and the accepted decisions DEC-01 to DEC-09.
