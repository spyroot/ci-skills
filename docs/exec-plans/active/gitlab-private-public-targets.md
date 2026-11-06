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

GitLab action, `access_check`, `gitlab_mirror`, `gitlab_runner_manager` and `image_build_readiness chain` results,
offline plans included, report that answer in one `routing` object (DEC-15), and the human view prints it on one line.
A result produced before the target file loads, such as a selection failure, has no `routing`. To see it work, run
a GitLab dry-run with and without `--gitlab public` and compare the `routing` object and `origin`.

## Progress

- [x] (DEC-01–DEC-13) Design accepted, except DEC-03, DEC-05, DEC-08, DEC-10 and DEC-17, which are in CONFLICT with
  recorded answers. DEC-14 and DEC-16 are PROPOSED.
- [x] (GPP-01) This ExecPlan and its index row are committed.
- [x] (GPP-02) Table-driven regression tests are committed and fail on the base revision. Tests:
  `tests/python/test_target_profiles.py` (selection, routing, field errors, CLI rows), `conftest.py` `target_file` in
  the new shape, and rows in `test_endpoints.py`, `test_catalog.py`, `test_report_human.py`, `test_target_runtime.py`,
  `test_gitlab_mirror.py` and `test_gitlab_actions.py` (commits `66eee42`, `2ed6503`). Evidence: run
  [42127530173](https://github.com/spyroot/ci-skills/actions/runs/42127530173) on `2ed6503`: job `validate` 197
  failed and 7 errors; job `gate-ci-skills-endpoints` 4 failed, all in `test_endpoints.py`. `validate` new rows:
  `test_target_profiles.py` 105, `test_gitlab_mirror.py` 4, `test_catalog.py` 2, `test_report_human.py` 2,
  `test_gitlab_actions.py` 2. `validate` fixture cascade from the routed `target_file`:
  `test_collectors.py` 34, `test_access_core.py` 15, `test_target_runtime.py` 13, `test_node_local_cli.py` 9,
  `test_node_pod.py` 7 errors, `test_cli_contract.py` 5, `test_status_cli.py` 4, `test_live_gate.py` 2.
- [x] (GPP-03) The contract names are defined once in `core/endpoints.py`; `[gitlab_routing]` is parsed and validated
  (`a9eddd5`).
- [x] (GPP-04) The `--gitlab` selector joins `--gitlab-profile` on every GitLab command; the legacy path warns
  (`a9eddd5`). Evidence: run [42130263348](https://github.com/spyroot/ci-skills/actions/runs/42130263348) on
  `e5b40eb`: `gate-ci-skills-endpoints`, `test-install` and the `validate` pytest step pass.
- [x] (GPP-05) `routing` is in the GitLab action, `access_check` and `gitlab_mirror` JSON and in every human view
  that receives it; the action, runner and mirror result schemas and the live-acceptance checker accept 1.1
  (`a9eddd5`, `e5b40eb`, `f474eb6`). The review-batch commit after `e38a4a9` reads Kubernetes and Harbor through the
  selected route (DEC-18), adds `routing` to `gitlab_runner_manager` and `image_build_readiness chain` results, and
  derives the accepted receipt versions from the catalog constants. Scope limits are DEC-15 and TD-12.
- [x] (GPP-06) The template, references, README and `tools.json` are updated (`a9eddd5`, `e5b40eb`).
- [x] (GPP-07a) Both installed copies are upgraded, with AFTER evidence against the selected target. Both installs
  read back `e5b40eb` (`INSTALLATION.json`, verified). `gitlab_issue.py open-bug --project hott/test --json` from
  each install: the renamed target without `[gitlab_routing]` gives `BLOCKED gitlab_routing_required`; the issue #133
  shape (`[gitlab]` + `[gitlab.com]`) gives `DRY_RUN` on `[gitlab]` with one `gitlab_target_deprecated` event, and
  `--gitlab-profile com` reads `[gitlab.com]`. After `[gitlab_routing]` was added to the user target (backup beside
  it), no option reads `[gitlab_private]` with `routing.selected_by` `default`, and `--gitlab public` reads
  `[gitlab_public]` with `argv:--gitlab=public`, no deprecation event. Evidence files:
  `.internal/log/bug-133/after-*.json` in the primary checkout.
- [ ] (GPP-07b) Both installed copies are upgraded again at the final PR head and the GPP-M4 commands rerun. The
  `e5b40eb` installs are stale: `f474eb6` and later commits change result output and table selection. Held until the
  decisions listed under GPP-08 are made.
- [ ] (GPP-08) The candidate receipts are renewed and `validate` is green on the PR head. Held. Open decisions:
  DEC-14 is PROPOSED; DEC-03, DEC-05, DEC-08, DEC-10 and DEC-17 are in CONFLICT; the current kube context
  (`home-lab-k8s`) is not the cluster `authority.sh` names for the smoke GitLab (`api-ww-cai-cisco-live-dcloud-local`);
  the mirror qualification script that writes the allocation record pins another candidate SHA and worktree and has
  been used twice, the `.internal/INDEX.yaml` `promoteAfterUses` limit, while `scratchPolicy.mutationAllowed` is
  `false`. PR #138 also changes `ci-skills/lib`; reviewers recommend merging it first, rebasing this branch, and
  capturing one receipt set on the final head.
- [x] (GPP-09) Legacy removal is tracked as TD-11 (`docs/exec-plans/tech-debt-tracker.md`).

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
- Observation: live acceptance pinned receipt `schema_version` to `1.0`.
  Evidence: `tools/check_live_acceptance.py:583` reported `schema_version_unexpected` otherwise. Per DEC-09 the
  checker now accepts `RECEIPT_SCHEMA_VERSIONS` (`1.0`, `1.1`), so historical receipts stay valid and `access_check`
  and `gitlab_mirror` results emit `1.1` with `routing`, as GitLab action results do. The checker builds that set
  from `catalog.SCHEMA_VERSION` and `catalog.ROUTED_RESULT_SCHEMA_VERSION`.
- Observation: no check runs the project-neutrality scan.
  Evidence: `lib/bash/automation/bless.bash:19` sets `BLESS_CHECKS='whitespace json schemas yaml markdown bash_syntax
  shell shfmt actionlint gitleaks python endpoints portability source_cycle runtime_sync'`, with no neutrality entry,
  and no file under `.github/workflows/`, the `Makefile` or `.githooks/` mentions `check_project_neutrality.py`.
  `python tools/check_project_neutrality.py --root . --json` reports `FAIL` on this branch for the tracked
  `.coordination/priorties.md`, which is on `main` since `67b683e`. Out of scope here.
- Observation: the endpoint gate's `undocumented` rule compared raw template paths with declared paths, so
  `[gitlab_private]` did not document `gitlab.url`.
  Evidence: `make bless` reported `undocumented: gitlab.url`. `tools/skillkit/endpoint_gate.py` now folds template
  paths through `TargetContract.canonical_path` for that rule, as `_introduced` already did. The portability gate
  scans `TargetContract.tables` headers and does not classify `[gitlab_routing]` values as hosts.
- Observation: the first implementation reported the route but did not use it. `[gitlab_routing]` named the paired
  Kubernetes and Harbor tables, yet `load_target` read `[kubernetes]` and `[harbor]` by fixed name, so a runner deploy
  with `--gitlab public` and `gitlab_public = {}` would have planned into `[kubernetes]`.
  Evidence: review of `a9eddd5`; `core/target.py` `load_target`. DEC-18 records the fix.
- Observation: an ambient `GITLAB_TOKEN` is not bound to one instance.
  Evidence: `ci-skills/lib/python/access/sources.py` picks the first set environment variable for the selected host,
  and `access/gitlab_credentials.py` exports it as `GITLAB_TOKEN`. With no `token_file`, the token goes to whichever
  instance `--gitlab` selects. The template now declares `token_file` in `[gitlab_public]`; DEC-16 proposes a rule.
- Observation: `gitlab_runner_manager.py --dry-run` prints `routing` but not the cluster context or registry URL;
  those are bound into `plan_digest`. `access_check.py --dry-run` prints the selected targets per surface.
  Evidence: the GPP-M4 and Validation commands below.
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
  files touched and add enum values to `schemas/command-contract.schema.json` and `schemas/skill-manifest.schema.json`,
  a MINOR change under `docs/phases/CI07-SCHEMA.md`, in every consumer of those enums.
  Governing Ref: issue #133; `docs/phases/CI07-SCHEMA.md` MAJOR/MINOR rules.
- Decision DEC-02: A single `[gitlab_routing]` table declares the default GitLab and the Kubernetes and Harbor tables
  that pair with each GitLab.
  Status: ACCEPTED
  Rationale: One place answers the default and both pairings before any call. GitLab, Kubernetes and Harbor tables
  keep only endpoint, access and value fields.
  Governing Ref: issue #133 Expected section; recorded answers "One routing table" and "so we need elegant solution so
  on call side , prior any call back even called it should answer what is default ? for gitlab private what harbor ?
  what k8s ? so propose show me toml" (queue record `handoff.operatorAnswers`).
- Decision DEC-03: The CLI selector is `--gitlab private|public`. Its choices and its table mapping come from the
  contract constants. Omitting it selects `[gitlab_routing].default`.
  Status: CONFLICT - awaiting decision
  Rationale: One option name keeps one meaning across commands, as `docs/phases/CI02-CLI.md` requires.
  Conflict: the recorded answer is "Private by default". The code requires `[gitlab_routing].default` and accepts
  `default = "gitlab_public"` without a warning, so omission reads whichever instance the target names.
  Governing Ref: `docs/phases/CI02-CLI.md` interface rules; recorded answer "Private by default".
- Decision DEC-04: Results carry a top-level `routing` object. It is not named `profile`.
  Status: ACCEPTED
  Rationale: `profile` already means the diagnostic run mode in `core/cli.py` and `core/access/shared.py`.
  Governing Ref: `core/cli.py` gate profile field.
- Decision DEC-05: The legacy shape (`[gitlab]`, `[gitlab.NAME]`, `--gitlab-profile`) is accepted for one release.
  Every use writes a deprecation warning. A target that mixes the legacy and new shapes is rejected.
  Status: CONFLICT - awaiting decision
  Rationale: Existing user-level targets keep working while their owners migrate. Mixing shapes would make the
  default ambiguous. TD-11 tracks the removal.
  Conflict: the recorded answer is "Accept both one release". The code accepts each shape alone but rejects a target
  holding both with `gitlab_target_shapes_mixed`.
  Governing Ref: `docs/PLANS.md` parallel-implementation guidance; recorded answer "Accept both one release".
- Decision DEC-06: Authority table names use `_`, never `.`. A dot appears only where nesting is real, as in
  `[kubernetes.node_diagnostics]`.
  Status: ACCEPTED
  Rationale: This prevents the nesting trap of issue #133. A dotted header beside the instance tables, such as
  `[gitlab.com]` or `[gitlab_public.com]`, blocks with a diagnostic that names the nested table.
  Governing Ref: Surprises entry on TOML nesting; recorded answer "use [gitlab_private] use [gitlab_public] design cli
  so caller can choose".
- Decision DEC-07: Every new name is defined once, as a constant in `ci-skills/lib/python/core/endpoints.py`. The
  catalog, parser, help text, result fields, tests and docs derive from those constants or are checked against them.
  Status: ACCEPTED
  Rationale: Repository rule: define constant strings once. Reason tokens are constants in `core/target.py`.
  Governing Ref: pinned Standards `docs/agents/contracts/software-design.md` (revision `5bd31f1`), hardcoded-value
  rule; recorded answer "this name need define in entier code once as const".
- Decision DEC-08: `project`, `group` and `runner_id` stay optional in each GitLab instance table, exactly as in the
  flat table today. When `project` is absent the caller passes `--project`; `project = ""` stays rejected. Examples
  and the template omit `project`, so the caller always names the project.
  Status: CONFLICT - awaiting decision
  Rationale: This preserves current behavior and avoids a hidden default project. It deliberately differs from the
  shell-script guidance to use a known repository default: a user-level target serves many projects.
  Conflict: the recorded answer is "project default should \"\" because call need provide otherwise what project is
  this ? and I think that the current behaviour it can be empty". The code rejects `project = ""`.
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
  Status: CONFLICT - awaiting decision
  Rationale: The recorded answer says `gitlab` means the private GitLab. A legacy target has no pairing to report.
  Conflict: the same answer is "remeber gitlab mean private gitlab.com public". On the issue #133 shape
  (`[gitlab]` + `[gitlab.com]`), `--gitlab public` returns `gitlab_public_target_required` instead of reading
  `[gitlab.com]`.
  Governing Ref: recorded answer "remeber gitlab mean private gitlab.com public"; DEC-05.
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
  error, written through `core/logging.py:log_event` with the options the command parser bound
  (`bind_log_settings`). It appears at `--log-level warning` or below, and a dry run or `--describe` does not write it
  to `--log-file`, the same rule as `core/read_cli.py`.
  Status: ACCEPTED
  Rationale: No new reason token where an existing diagnostic already names the problem; one logging facility.
  Governing Ref: `core/target.py` `_table`; `core/logging.py`; `core/read_cli.py` persist rule.

- Decision DEC-14: The GitLab Agent for Kubernetes uses the same `[gitlab_routing]` row as runners. No target key is
  added.
  Status: PROPOSED
  Rationale: One routing authority. Per GitLab instance:
  - GitLab Agent and KAS identity: a GitLab Agent belongs to the selected instance. It is registered in a
    configuration project of that instance (`--project`). Its agentk installation runs in the cluster that
    `[gitlab_routing].<instance>.kubernetes` names and connects to that instance's KAS, whose address is discovered
    from the selected GitLab (CI14-M2: "Discover ... KAS prerequisites"). GitLab Agents of the private and public
    instance can share one cluster; each has its own agent name and installation and reaches the cluster only
    through its own instance's KAS.
  - Runners on Kubernetes: `gitlab_runner_manager.py` and `image_build_readiness.py chain` resolve `gitlab`,
    `kubernetes` and `harbor` with the same `--gitlab` selection. The runner registers with the selected instance,
    runs in the routed cluster and pulls from the routed Harbor (DEC-18). It reaches GitLab with its own runner
    token, not through a GitLab Agent. A GitLab Agent and a runner of the same instance land in the same cluster
    because they read the same row.
  - What the target carries: `[gitlab_routing]` holds table names only. The GitLab Agent name, the configuration
    project and the authorized projects come from the request (`--project`, an agent-name option, a repeatable
    CI-access project option); CI14 D3 has not settled those flags.
  - What results carry: a GitLab Agent result would add the context `<configuration project path>:<agent-name>` to
    the `routing` object. That adds an optional field to `gitlab-runner-tag.schema.json#/$defs/routing` and needs
    approval together with CI14 D3. This change adds nothing for the GitLab Agent.
  - How CI14 reads routing: `gitlab_agent.py` passes `GitLabSelection.from_args` to the shared resolver and reads
    `Target.routing.kubernetes`. It adds no cluster selector of its own, so CI14 does not get a second routing
    authority.
  Conflict, recorded and not resolved: `docs/phases/CI14-GITLAB-CONNECTIONS.md:76` says "No new target key is
  authorized by this phase." DEC-14 adds no target key. A later change that adds a GitLab Agent key to
  `[gitlab_routing]`, for example `gitlab_routing.<instance>.agent`, falls under that line and needs approval.
  Governing Ref: [CI14-M2](ci14-gitlab-connections.md#ci14-m2-resolve-d2-and-deliver-the-agent-workflow);
  `docs/phases/CI14-GITLAB-CONNECTIONS.md:66-67,76`; docs.gitlab.com `user/clusters/agent/install`,
  `user/clusters/agent/ci_cd_workflow`, `api/cluster_agents`.
- Decision DEC-15: `routing` is carried by GitLab action results (`gitlab-action`, `gitlab-runner-tag`, `-read`,
  `-delete` schemas), `access_check`, `gitlab_mirror`, `gitlab_runner_manager` (`toolbox-workflow` schema) and
  `image_build_readiness chain`. `gitlab_job`, `gitlab_job list`, `gitlab_pipeline` get and `gitlab_pipeline watch`
  results do not carry it in this change; TD-12 tracks them.
  Status: ACCEPTED
  Rationale: These are the results whose commands combine GitLab with Kubernetes or Harbor, or whose receipts live
  acceptance requires. The job and pipeline readers build their envelopes separately and read one GitLab only.
  Governing Ref: review of `a9eddd5` (finding F05); DEC-09.
- Decision DEC-16: When both instance tables are declared, each declares its own `token_file`, so an ambient
  `GITLAB_TOKEN` cannot reach the other instance.
  Status: PROPOSED
  Rationale: Today the environment token goes to whichever instance is selected when that instance has no
  `token_file` (Surprises). A binding rule is a credential-scope change and needs approval; this change only documents
  the behavior and declares `token_file` in the template.
  Governing Ref: pinned Standards `docs/agents/contracts/policy-authority-contract.md` (credential scope needs a
  named authority); Surprises entry on `GITLAB_TOKEN`.
- Decision DEC-17: `[gitlab_routing]` is required whenever an instance table exists, including a GitLab-only target.
  Status: CONFLICT - awaiting decision
  Rationale: One place answers the default before any call (DEC-02).
  Conflict: no recorded answer covers a GitLab-only target; "One routing table" settles the shape, not whether the
  table is mandatory.
  Governing Ref: DEC-02; queue record `handoff.operatorAnswers`.
- Decision DEC-18: A command that requires a routed authority reads the table the selected `[gitlab_routing]` row
  names. A row that omits it blocks with the existing `target is missing required authority tables: <names>`. Optional
  authorities and legacy routes read the authority's own table.
  Status: ACCEPTED
  Rationale: Issue #133 requires fail-closed selection, and an omitted pairing is the only case where the row carries
  information while one table per authority exists. No new reason token.
  Governing Ref: issue #133 Expected section; review of `a9eddd5` (findings F01, F4).

## Outcomes & Retrospective

- Delivered on PR #141: instance tables, `[gitlab_routing]`, `--gitlab`, route-enforced Kubernetes and Harbor
  selection, the `routing` object in five command families (DEC-15), the deprecation path, and the documents.
- Not delivered: candidate receipts (GPP-08) and the second install upgrade (GPP-07b); five decisions are in
  CONFLICT and two are PROPOSED.
- Lesson: the first implementation reported the pairing without using it; a test that asserts behavior for an
  omitted pairing would have caught it before review.

## Context and Orientation

A target file is a TOML file that tells every ci-skills command which external systems to use. TOML is a
configuration format of `[table]` headers followed by `key = value` lines. A command resolves the file in this order:
`--binding`, `CI_SKILLS_TARGET` (or `K8S_ADMIN_DIAGNOSTICS_BINDING`), `./.ci-skills/target.toml`, then
`~/.ci-skills/target.toml`. Each top-level table describes one
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
`core/gitlab/gitlab_actions.py` (`_result`), `ci-skills/lib/python/access/access.py`, `core/api_read.py` and
`core/report.py`, which
renders JSON, YAML and the human view. Result schemas live in `ci-skills/schemas/results/`.

Three commands combine GitLab with other authorities:

- `ci-skills/bin/gitlab_runner_manager.py` deploys a runner on Kubernetes from a Harbor image and registers it in
  GitLab.
- `ci-skills/bin/image_build_readiness.py chain` does the same through a build chain.
- `ci-skills/bin/access_check.py` checks every configured authority.

These commands need to know which Kubernetes cluster and which Harbor registry belong to the selected GitLab. That is
the routing this plan adds; DEC-18 makes the runner manager and the chain read their tables through it.

A person with their own GitLab (self-managed or gitlab.com) and their own Kubernetes cluster (home lab, staging,
production) installs CI Skills. They ask their AI agent to attach one of their GitLab projects to one of their
clusters. The AI agent finds the command in the installed catalog, gets a plan, shows it, and on confirmation applies
it; CI Skills makes the GitLab and Kubernetes calls and returns one bounded result. The ci-skills repository is never
attached to a cluster: the project and the cluster are the person's, named in the person's own
`~/.ci-skills/target.toml` and in the request.

    Person with their own GitLab and their own Kubernetes cluster
      |  "attach my project group/app to my lab cluster"
      v
    AI agent (Claude, Codex, ...) with CI Skills installed
      |  1. gitlab_agent.py --describe                 flags, modes, result schema; no credentials
      |  2. gitlab_agent.py attach --gitlab private --project group/app --agent-name lab --live-plan
      |                                                reads only; returns the plan and its digest
      |  3. shows the plan to the person; the person confirms
      |  4. same command with --apply --confirm-plan <digest>
      v
    CI Skills (installed copy) reads the person's ~/.ci-skills/target.toml
      [gitlab_routing]  --gitlab private -> [gitlab_private] + its kubernetes table
      |-- their GitLab   register agent "lab" in group/app; .gitlab/agents/lab/config.yaml (ci_access)
      |-- their cluster  install agentk (gitlab/gitlab-agent chart, token, KAS address read from GitLab)
      `-- read back      agent connected; CI jobs in group/app get context group/app:lab
      v
    One bounded JSON result -> AI agent -> person
      PASS | BLOCKED <reason and missing prerequisite> | PARTIAL <what exists, what is missing>

The command name `gitlab_agent.py` and its flags are PROPOSED (CI14 D3); the modes follow the existing mirror pattern
(`--live-plan`, `--apply`, `--confirm-plan`) and `--describe`.

The GitLab Agent for Kubernetes is the other way a GitLab project reaches a cluster, besides a runner. It is planned
in [CI14-M2](ci14-gitlab-connections.md#ci14-m2-resolve-d2-and-deliver-the-agent-workflow) and not delivered. A
GitLab Agent is registered in one GitLab project, its configuration project, through the cluster agents API
(`POST /projects/:id/cluster_agents`, as relayed from the GitLab API documentation). Its configuration file is
`.gitlab/agents/<agent-name>/config.yaml` in that project. The `ci_access.projects` and `ci_access.groups` lists in
that file name the projects whose CI jobs may use the GitLab Agent, up to 500 each; they must share the configuration
project's top-level group unless instance-level authorization is enabled. The cluster side, agentk, is installed with
the `gitlab/gitlab-agent` Helm chart, given `config.token` and `config.kasAddress`; KAS is the GitLab service agentk
connects to. A CI job in an authorized project then finds one kubeconfig context per shared GitLab Agent, named
`<path/to/configuration/project>:<agent-name>`. Sources: docs.gitlab.com `user/clusters/agent/install`,
`user/clusters/agent/ci_cd_workflow` and `api/cluster_agents`.

With CI Skills, an AI agent attaches the person's cluster to the person's project like this. The caller names the
GitLab with `--gitlab`, the project with `--project`, and the GitLab Agent with an agent-name option.
`[gitlab_routing]` names the cluster, by the `kubernetes` value of the selected instance's row; agentk is installed
there and connects to the selected GitLab's KAS, whose address is read from that GitLab at run time. The projects
allowed to use the GitLab Agent come from the request, never from a default. DEC-14 records this proposal; the flag
names are not settled.

Live acceptance is a CI check that compares committed receipts with the code under review. A receipt is a JSON result
a real host produced. The check is `tools/check_live_acceptance.py`, with policy in
`inventory/ci/live-acceptance.json`. It fingerprints `ci-skills/bin`, `ci-skills/lib` and `ci-skills/schemas`. Any
change there invalidates the receipts in `tests/acceptance/candidate/` until fresh ones are captured from an installed
copy.

## Plan of Work

### GPP-M1: plan and failing regression tests (GPP-01, GPP-02)

Commit this ExecPlan with its row in `docs/plans/index.md`, then the regression tests alone, so CI shows them failing
against the unchanged code. The shared fixture `target_file` in `tests/python/conftest.py` moves to the routed shape.
`tests/python/test_target_profiles.py` becomes the selection, routing and field-error table and runs
`gitlab_issue.py open-bug --project team/app --title t --json` against direct targets. `test_endpoints.py`,
`test_catalog.py`, `test_report_human.py` and `test_gitlab_actions.py` gain rows for the contract, the selector, the
human `Routing:` line and JSON Lines diagnostics. After this milestone the expected behavior exists as executable rows.
Run `make bless`, push, and read the PR's `validate` run. Acceptance: the run fails on the new rows and on the fixture
cascade, and on nothing else.

### GPP-M2: contract, parser and selector (GPP-03, GPP-04)

Add the constants, the instance tables and the routing table to `core/endpoints.py`; add `GitLabSelection`,
`GitLabRoute` and `GitLabRouting` to `core/target.py`; carry `gitlab_selection` through the call chain; add
`--gitlab` beside the deprecated `--gitlab-profile` in `core/catalog.py` and the shared parser. After this milestone
every GitLab command reads `[gitlab_private]` or `[gitlab_public]`, rejects an invalid routing table with one exact
reason, and reads the legacy shape with one `gitlab_target_deprecated` event. Run `ruff check .`,
`ruff format --check .`, `python tools/render_manifest.py --check` and `make bless`, then push. Acceptance: the
selection, routing and field-error rows of `test_target_profiles.py` pass in `validate`.

### GPP-M3: routing output, schemas and documents (GPP-05, GPP-06)

Read required Kubernetes and Harbor tables through the selected route (DEC-18). Emit `routing` and `schema_version`
`1.1` from GitLab action, `access_check`, `gitlab_mirror`, `gitlab_runner_manager` and `image_build_readiness chain`
results; print the
`Routing:` line in the human view; define the routing object once in `gitlab-runner-tag.schema.json#/$defs/routing`;
let the live-acceptance checker accept receipt versions `1.0` and `1.1`; update the template, `references/access.md`,
README, SKILL.md and `tools.json`. After this milestone a caller sees which GitLab, cluster and registry a command
used. Run `python gates/gate-ci-skills-endpoints.py --base <merge-base> --json` and `make bless`, then push.
Acceptance: `gate-ci-skills-endpoints` and the pytest step of `validate` pass on the PR head.

### GPP-M4: installed copies and AFTER evidence (GPP-07a, GPP-07b)

Upgrade both installed copies from the clean worktree at the PR head, run the BEFORE commands again from each
installed copy, add `[gitlab_routing]` to the user target after a backup, and run them once more. GPP-07a did this at
`e5b40eb`; GPP-07b repeats it at the final head. After this milestone both installs read the user target. Run from
the worktree, with the conda Python, for each scope, `claude-user` (`~/.claude/skills/ci-skills`) and `codex-home`
(`~/.codex/skills/ci-skills`):

    python tools/install_ci_skills.py --scope claude-user --upgrade --dry-run --json
    python tools/install_ci_skills.py --scope claude-user --upgrade --apply --confirm-upgrade <fingerprint> \
        --timeout 600s --json
    jq -r .revision.value ~/.claude/skills/ci-skills/INSTALLATION.json

Then, from a directory outside the repository, for each install:

    CI_SKILLS_TARGET=~/.ci-skills/target.toml python ~/.claude/skills/ci-skills/bin/gitlab_issue.py open-bug \
        --project hott/test --title "issue 133 after" --json
    CI_SKILLS_TARGET=~/.ci-skills/target.toml python ~/.claude/skills/ci-skills/bin/gitlab_issue.py open-bug \
        --project hott/test --title "issue 133 after" --gitlab public --json

Expected: `INSTALLATION.json` reads back the head SHA. Without the option, `status` `DRY_RUN`, `origin` from
`[gitlab_private]` and `routing` `{"gitlab": "gitlab_private", "kubernetes": "kubernetes", "harbor": "harbor",
"selected_by": "default"}`. With `--gitlab public`, `origin` from `[gitlab_public]` and `routing` `{"gitlab":
"gitlab_public", "kubernetes": null, "harbor": null, "selected_by": "argv:--gitlab=public"}` for the row
`gitlab_public = {}`. On the issue #133 shape the result is `DRY_RUN` with one `gitlab_target_deprecated` line on
standard error. Save each output under `.internal/log/bug-133/`.

### GPP-M5: candidate receipts and green validate (GPP-08)

Capture fresh candidate receipts from the installed final candidate and move the superseded ones to
`tests/acceptance/historical/pre-gitlab-private-public/`. Preconditions: the GPP-08 decisions are made and GPP-07b is
done. Commands, from the installed copy, with the kube context `authority.sh` names:

    CI_SKILLS_TARGET=~/.ci-skills/target.toml python ~/.claude/skills/ci-skills/bin/access_check.py --json \
        --publication --job-url https://gitlab.dcloud.run/hott/test/-/jobs/59277 --ceph-namespace openshift-storage \
        --receipt-out tests/acceptance/candidate/access.json

The flags are those recorded in the current `tests/acceptance/candidate/access.json`. The mirror `APPLIED` and
`NO_OP` receipts come from `gitlab_mirror.py sync --project hott/test-mirror-push --destination
spyroot/test-mirror-push --create-if-absent`: `--live-plan`, then `--apply --confirm-plan <digest> --receipt-out
<file>`, twice. The `NO_OP` receipt needs a `gitlab_mirror_smoke_cleanup` record. Its only producer is the scratch
pair `.internal/scratch/codex/ci14-ci15-closure/pr127-corrected/mirror/qualify-executed.py` (writes
`mirror-live-allocation.json`) and `mirror-owned-cleanup.py` (deletes the run-created mirror and GitHub repository and
writes the record); running them again needs the decision listed under GPP-08. Then run
`python tools/check_live_acceptance.py --root . --no-aggregate --receipts tests/acceptance/candidate --json`.
Acceptance: the checker reports `PASS`, and `ci-skills/required` and the `live-acceptance` deployment are green on the
PR head.

### GPP-M6: legacy removal tracked (GPP-09)

Record TD-11 in `docs/exec-plans/tech-debt-tracker.md`: remove `named_profiles`, `--gitlab-profile` and the `[gitlab]`
read in the next release. Acceptance: the TD-11 row links to DEC-05.

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
`gitlab`. `TargetContract.routing_table() -> Optional[str]` returns `GITLAB_ROUTING_TABLE`, or `None` for a contract
built without a routing table. `TargetContract.tables` lists every top-level table a target may declare.

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
        def select(self, selection: GitLabSelection) -> GitLabRoute: ...

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
- `gitlab_selector_unknown` (a library caller passes a selector outside `GITLAB_SELECTORS`)

Each is a constant in `core/target.py`; the two families come from `GitLabRouting.instance_required(table)` and
`GitLabRouting.pairing_unknown(authority)`. A dotted header beside the instance tables blocks with
`NESTED_TABLE_DIAGNOSTIC`, for example `[gitlab.com] is table com nested in [gitlab]: TOML reads a dot in a table name
as nesting; name a GitLab [gitlab_private] or [gitlab_public] and add [gitlab_routing]`.

`load_target` gains `gitlab: Optional[str]`, holding a `GITLAB_SELECTORS` key, beside the legacy `gitlab_profile`.
`Target` gains `routing: Optional[GitLabRoute]`. Field errors name the table read, for example
`gitlab_public.url must be a nonempty string`.

REFACTOR the call chain. Rename the `gitlab_profile` keyword to a selection value and pass it through every caller:

- `core/cli.py`, `core/read_cli.py`, `core/api_read.py`;
- `core/project_binding.py`, `core/gitlab/cli.py`;
- `core/gitlab/gitlab_action.py`, `core/gitlab/gitlab_pipeline_start.py`;
- `core/gitlab/mirror/cli.py`;
- `core/gitlab/runner_manager/action.py`, `core/gitlab/runner_manager/callbacks.py`;
- `core/kubernetes/image_build_chain/model.py`, `core/kubernetes/image_build_readiness.py`;
- `ci-skills/bin/gitlab_access.py`, `ci-skills/bin/gitlab_pipeline.py`.

Pre-verification is the existing suite on the base revision. Post-verification is the same suite plus the new tables.
Combined commands (`gitlab_runner_manager.py`, `image_build_readiness.py chain`) read Kubernetes and Harbor through
the selected route (DEC-18). `image_build_readiness.py probe` still offers the selector and rejects it at load
(DEC-12).

MODIFIED `ci-skills/lib/python/core/logging.py` and `core/argument_parser.py`: the parser binds its namespace with
`bind_log_settings`, which returns a token for `unbind_log_settings`; `bound_log_settings` serves the target loader.
`tests/python/conftest.py` resets the binding around every test.

MODIFIED `ci-skills/lib/python/core/schema.py`: `specification_validator(name)` returns the registry-backed validator
that `validate_specification` uses, so tests resolve sibling `$ref`s locally.

MODIFIED `tools/skillkit/endpoint_gate.py` (the `undocumented` rule folds instance paths through `canonical_path`),
`tools/skillkit/portability_gate.py` (headers from `TargetContract.tables`; `[gitlab_routing]` values are not hosts)
and `tools/check_live_acceptance.py` (`RECEIPT_SCHEMA_VERSIONS` from the catalog constants).

MODIFIED `ci-skills/lib/python/core/catalog.py`:

- `GITLAB_TARGET_OPTION: Final = "--gitlab"` with `choices=tuple(GITLAB_SELECTORS)`;
- `GITLAB_PROFILE_OPTION` stays as the deprecated alias;
- `supports_gitlab_profile` becomes `supports_gitlab_selection` and gates both options;
- the help text states the default comes from `[gitlab_routing]`.

MODIFIED result envelopes in `core/gitlab/gitlab_actions.py`, `ci-skills/lib/python/access/access.py`,
`core/gitlab/mirror/cli.py`, `core/toolbox_actions.py` (runner manager) and
`core/kubernetes/image_build_chain/command.py`. They emit:

    "routing": {"gitlab": "gitlab_public", "kubernetes": "kubernetes", "harbor": "harbor",
                "selected_by": "argv:--gitlab=public"}

MODIFIED `core/report.py` human view. It adds the line:

    Routing: gitlab=gitlab_public kubernetes=kubernetes harbor=harbor (argv:--gitlab=public)

INTRODUCED `gitlab-runner-tag.schema.json#/$defs/routing`, Draft 2020-12, the one definition: required keys
`gitlab` (string), `kubernetes` (string or null), `harbor` (string or null) and `selected_by` (string), with
`additionalProperties: false`. `ci-skills/schemas/results/gitlab-routing.schema.json` is a `$ref` to it.

MODIFIED: seven result schemas gain an optional `routing` property and accept `schema_version` `1.0` and `1.1`:
`gitlab-action`, `gitlab-runner-tag`, `gitlab-runner-read`, `gitlab-runner-delete`, `gitlab-mirror`,
`toolbox-workflow` and `image-build-readiness`. `.github/workflows/validate.yml` adds `gitlab-routing.schema.json` to
its existing metaschema check.

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
| `GITLAB_TOKEN` | `secret` | Optional | Caller environment | Used only without `token_file`, for the selected GitLab |
| `GITLAB_ACCESS_TOKEN` | `secret` | Optional | Caller environment | Same rule as `GITLAB_TOKEN` |
| `OAUTH_TOKEN` | `secret` | Optional | Caller environment | Same rule as `GITLAB_TOKEN` |

CI pipelines and jobs relied on (no new job is introduced):

| Pipeline / Job Name | Type / Trigger | Stage / Order | File Path | Required Inputs & Artifacts |
| --- | --- | --- | --- | --- |
| `validate` | PR, push, schedule, dispatch | 1 | `.github/workflows/validate.yml` | Lint, schemas, pytest, receipts |
| `gate-ci-skills-endpoints` | Same | parallel | `.github/workflows/validate.yml` | Template and skill Markdown |
| `test-install` | Same | parallel | `.github/workflows/validate.yml` | Installed copy, outside the source |
| `required-result` | Same | last | `.github/workflows/validate.yml` | Results of the jobs above |

Verification gates relied on (all existing):

| Gate ID | Gate Type / Class | Pass Condition | Fail-Closed Condition | Producer / Owner |
| --- | --- | --- | --- | --- |
| `pytest` | unit | Suite and new selection tables pass | Any failure | `validate` job |
| `live-acceptance` | receipt | Receipts match the digest | Mismatch or missing operation | `check_live_acceptance.py` |
| `endpoints` | contract docs | Paths documented and paired | Undocumented or unpaired path | `endpoint_gate.py` |
| `portability` | static scan | No fixed deployment selector | Any hard-coded selector | `portability_gate.py` |
| `bless` | pre-commit | Staged files pass `BLESS_CHECKS` (`bless.bash:19`) | Any finding | `bless.sh` |

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

    [kubernetes]
    context = "admin-context"
    server = "https://api.cluster.example.test:6443"

    [harbor]
    url = "https://harbor.example.test"
    project = "example"
    credential_file = "harbor.credentials"

Expected results:

- `gitlab_issue.py open-bug --project team/app --title t --json` returns `DRY_RUN`, `origin` from `[gitlab_private]`,
  and `routing.selected_by` `default`.
- Adding `--gitlab public` returns `origin` from `[gitlab_public]` and `routing.gitlab` `gitlab_public`.
- `--gitlab public` with no `[gitlab_public]` returns `BLOCKED` with `gitlab_public_target_required`.
- A legacy `[gitlab]` plus `[gitlab.com]` target, the shape of issue #133, returns `DRY_RUN` with `routing.gitlab`
  `gitlab`, `selected_by` `legacy`, and one deprecation warning on standard error.

A combined command reads the cluster and registry the route pairs. From the directory holding the target and a token
file `runner.token`:

    CI_SKILLS_TARGET=$PWD/target.toml python ci-skills/bin/gitlab_runner_manager.py deploy --dry-run --json \
        --gitlab public --project team/app --node worker-a --token-file runner.token --runner-id 73 \
        --image harbor.example.test/example/runner@sha256:<64 hex digits>

Expected: `"status": "DRY_RUN"`, `"schema_version": "1.1"` and

    "routing": {"gitlab": "gitlab_public", "kubernetes": "kubernetes", "harbor": "harbor",
                "selected_by": "argv:--gitlab=public"}

With `gitlab_public = {}` the same command returns `BLOCKED` with `target is missing required authority tables:
harbor, kubernetes`. The cluster and registry endpoints appear in the access dry run:

    CI_SKILLS_TARGET=$PWD/target.toml python ci-skills/bin/access_check.py --dry-run --json --gitlab public

Expected: `"routing"` as above and `"surfaces"` targets `{"gitlab": "https://gitlab.public.example.test",
"kubernetes": "admin-context -> https://api.cluster.example.test:6443"}`.

## Idempotence and Recovery

Dry-runs and offline plans make no provider request and can be repeated. Installer upgrades follow the installer's
own plan, confirm and recover path: after a failed upgrade, run `install_ci_skills.py --scope <scope> --recover
--apply --confirm-recover`, then repeat the `--upgrade` dry run and apply. Receipt
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
    class GitLabSelection: from_args(args) -> Self; requested; selected_by; validate()
    class GitLabRoute: legacy; table_for(authority) -> Optional[str]; as_dict(); selection_source(source)
    class GitLabRouting: parse(data) -> Self; select(selection) -> GitLabRoute

In `ci-skills/lib/python/core/endpoints.py`, the constants listed in Concrete Steps, `TargetContract.authority_of`,
`TargetContract.tables` and `TargetContract.routing_table() -> Optional[str]`. In `core/logging.py`,
`bind_log_settings`, `unbind_log_settings` and `bound_log_settings`. In `core/schema.py`, `specification_validator`.

Revision notes: initial version, derived from issue #133 and decisions DEC-01 to DEC-09. Then DEC-10 to DEC-13 during
implementation; milestones, the `bless` row and DEC-14 after the plan review; DEC-15 to DEC-18, the CONFLICT statuses
and runnable GPP-M4/GPP-M5 commands after the review of `a9eddd5`.
