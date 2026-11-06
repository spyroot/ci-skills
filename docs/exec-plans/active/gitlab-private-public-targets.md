# Select the `gitlab_private` or `gitlab_public` GitLab instance with explicit Kubernetes and Harbor routing

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and
`Outcomes & Retrospective` must be kept up to date as work proceeds. It is maintained in accordance with
[docs/PLANS.md](../../PLANS.md).

## Purpose / Big Picture

After this change, one target file can declare two GitLab instances side by side, under the table keys
`gitlab_private` and `gitlab_public`, without the TOML nesting trap reported in
[issue #133](https://github.com/spyroot/ci-skills/issues/133). The caller chooses one with a single flag,
`--gitlab private` or `--gitlab public`, on every GitLab command. The target file itself answers, before any provider
request, three questions:

- which GitLab is used when the flag is omitted;
- which Kubernetes cluster pairs with each GitLab;
- which Harbor registry pairs with each GitLab.

The split exists because a person's own GitLab and GitLab SaaS offer different capabilities, so a caller chooses, per
command, which instance it runs against. Each instance is paired with its own Kubernetes and Harbor, or with none,
through `[gitlab_routing]`, and the `routing` object in every result shows which instance ran.

GitLab action, `access_check`, `gitlab_mirror`, `gitlab_runner_manager` and `image_build_readiness chain` results,
offline plans included, report that answer in one `routing` object (DEC-15), and the human view prints it on one line.
A result produced before the target file loads, such as a selection failure, has no `routing`. To see it work, run
a GitLab dry-run with and without `--gitlab public` and compare the `routing` object and `origin`.

## Progress

- [x] (DEC-01–DEC-19) Design accepted. DEC-03, DEC-05, DEC-08, DEC-10 and DEC-17 were in CONFLICT with recorded
  answers; each now follows the answer it quotes (GPP-10). DEC-14 and DEC-16 stay PROPOSED.
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
  each install: the renamed target without `[gitlab_routing]` gives `BLOCKED gitlab_routing_required` (behavior at
  `e5b40eb`; GPP-10 made the table optional); the issue #133
  shape (`[gitlab]` + `[gitlab.com]`) gives `DRY_RUN` on `[gitlab]` with one `gitlab_target_deprecated` event, and
  `--gitlab-profile com` reads `[gitlab.com]`. After `[gitlab_routing]` was added to the user target (backup beside
  it), no option reads `[gitlab_private]` with `routing.selected_by` `default`, and `--gitlab public` reads
  `[gitlab_public]` with `argv:--gitlab=public`, no deprecation event. Evidence files:
  `.internal/log/bug-133/after-*.json` in the primary checkout.
- [ ] (GPP-07b) Both installed copies are upgraded again at the final functional head and the GPP-M4 commands rerun.
  The `e5b40eb` installs are stale: `f474eb6` and later commits change result output and table selection.
- [ ] (GPP-08) The candidate receipts are renewed and `validate` is green on the PR head. The earlier holds are
  cleared: the target pins `kubernetes.kubeconfigs` and `kubernetes.context` for the cluster `authority.sh` names, so
  no command reads the current kube context; the mirror qualification runs from the tracked
  `tools/qualify_mirror.py` (GPP-10) instead of the scratch pair. PR #141 merges first; draft PR #138 rebases
  afterwards.
- [x] (GPP-09) Legacy removal is tracked as TD-11 (`docs/exec-plans/tech-debt-tracker.md`).
- [x] (GPP-11) Live AFTER evidence of the capability, from an isolated installed copy at `c76723a` (digest
  `870a657e`), 2026-11-06T01:57:28Z to 02:00:43Z, with no ambient GitLab token, against the user target:
  `--gitlab private` read `hott/test` (123) on gitlab.dcloud.run and `--gitlab public` read `spyroot/test-self-runner`
  (88575800) on gitlab.com; the issue #133 shape blocked on `fb8b5ac` ("gitlab contains unsupported fields: com") and
  is DRY_RUN on the candidate, from dcloud by default and from gitlab.com with `--gitlab public`; `gitlab_runner_manager.py
  deploy --gitlab public --dry-run` is BLOCKED `target is missing required authority tables: harbor, kubernetes` and
  `--gitlab private` routes to `kubernetes` + `harbor`; a wrong confirmation is BLOCKED with `mutated` false;
  `gitlab_issue.py open-bug` APPLIED with verified read-back on both instances (dcloud `hott/test` issue iid 38, id 523;
  GitLab.com `spyroot/test-self-runner` issue iid 1, id 209352261) after an empty before-read, and the confirmed repeat
  is NO_OP. These are not the gate receipts of GPP-08. Evidence: `.internal/log/bug-133/smoke-claude-c76723a/` in the
  primary checkout (`SUMMARY.md` lists each command and file).
- [x] (GPP-10) The recorded answers replace the CONFLICT statuses (DEC-03, DEC-05, DEC-08, DEC-10, DEC-17) and the
  keys rule is DEC-19. `[gitlab_routing]` and its `default` are optional, `GITLAB_DEFAULT_INSTANCE` is
  `gitlab_private`, legacy `[gitlab.com]` answers `gitlab_public` by the key `GITLAB_LEGACY_PUBLIC_KEY`, and
  `[gitlab]` may sit beside `[gitlab_public]` and `[gitlab_routing]`. The mirror qualification producer is the
  tracked `tools/qualify_mirror.py` over `tools/skillkit/mirror_qualification.py`, with
  `tests/python/test_mirror_qualification.py`.

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
  `gitlab_mirror_smoke_cleanup`. Until GPP-10 no tracked tool produced that record; the producer was the scratch
  pair `qualify-executed.py` and `mirror-owned-cleanup.py` under `.internal/scratch/codex/ci14-ci15-closure/`.
  Evidence: `tools/check_live_acceptance.py` `_mirror_smoke_cleanup_verified`. `tools/qualify_mirror.py` now runs
  the same native calls and its test checks the record with that function.

## Decision Log

- Decision DEC-01: GitLab stays one internal authority, `gitlab`. A target declares up to two instance tables for it.
  Status: ACCEPTED
  Rationale: The authority name is used in command requirements, access protocol, live-acceptance surfaces and two
  contract schemas' enums. Keeping it leaves all of them unchanged. Two separate authorities would roughly triple the
  files touched and add enum values to `schemas/command-contract.schema.json` and `schemas/skill-manifest.schema.json`,
  a MINOR change under `docs/phases/CI07-SCHEMA.md`, in every consumer of those enums.
  Governing Ref: issue #133; `docs/phases/CI07-SCHEMA.md` MAJOR/MINOR rules; team answers "remeber gitlab saas allow
  you do more" and "that way we need that split".
- Decision DEC-02: A single `[gitlab_routing]` table declares the default GitLab and the Kubernetes and Harbor tables
  that pair with each GitLab.
  Status: ACCEPTED
  Rationale: One place answers the default and both pairings before any call. GitLab, Kubernetes and Harbor tables
  keep only endpoint, access and value fields. A row is the person's own declaration; no key, host or code rule
  infers one. Example: the template and the user target declare `gitlab_public = {}`, so `gitlab_runner_manager.py
  deploy --gitlab public` returns BLOCKED `target is missing required authority tables: harbor, kubernetes` before any
  provider call, and its `--dry-run` shows the same result. A task the selected instance itself refuses, such as a
  permission or feature limit on GitLab SaaS, comes back as a structured BLOCKED built from that instance's answer.
  Governing Ref: issue #133 Expected section; recorded answers "One routing table" and "so we need elegant solution so
  on call side , prior any call back even called it should answer what is default ? for gitlab private what harbor ?
  what k8s ? so propose show me toml" (queue record `handoff.operatorAnswers`); team answers "remeber gitlab saas
  allow you do more", "that way we need that split" and "2 some task in public gitlab you can not do , i.e user for
  example indicate his public github and private harbor < make no sense".
- Decision DEC-03: The CLI selector is `--gitlab private|public`. Its choices and its table mapping come from the
  contract constants. Omitting it selects `[gitlab_routing].default`; when the table or its `default` is absent, it
  selects `GITLAB_DEFAULT_INSTANCE`, which is `gitlab_private`. An explicit `default` naming a declared instance is
  still allowed.
  Status: ACCEPTED
  Rationale: One option name keeps one meaning across commands, as `docs/phases/CI02-CLI.md` requires. The fallback
  is one constant in `core/endpoints.py`.
  Governing Ref: `docs/phases/CI02-CLI.md` interface rules; recorded answer "Private by default".
- Decision DEC-04: Results carry a top-level `routing` object. It is not named `profile`.
  Status: ACCEPTED
  Rationale: `profile` already means the diagnostic run mode in `core/cli.py` and `core/access/shared.py`.
  Governing Ref: `core/cli.py` gate profile field.
- Decision DEC-05: The legacy shape (`[gitlab]`, `[gitlab.NAME]`, `--gitlab-profile`) is accepted for one release,
  alone or beside the new tables. Every load of a target holding `[gitlab]`, and every `--gitlab-profile`, writes one
  deprecation event. Only these combinations are rejected:
  - `[gitlab]` fields with `[gitlab_private]`: `gitlab_private_declared_twice`, because both answer `gitlab_private`;
  - `--gitlab` with `--gitlab-profile`: `gitlab_selector_conflict`.
  `[gitlab]` + `[gitlab_public]` is allowed: `[gitlab]` answers `gitlab_private`, `[gitlab_public]` answers
  `gitlab_public`. `[gitlab_routing]` may sit beside the legacy tables.
  Status: ACCEPTED
  Rationale: Existing user-level targets keep working while their owners migrate, one table at a time. TD-11 tracks
  the removal.
  Governing Ref: recorded answer "Accept both one release"; the reviewed legacy-shape table ("`[gitlab]` +
  `[gitlab_public]` | allowed"); `docs/PLANS.md` parallel-implementation guidance.
- Decision DEC-06: Authority table names use `_`, never `.`. A dot appears only where nesting is real, as in
  `[kubernetes.node_diagnostics]`.
  Status: ACCEPTED
  Rationale: This prevents the nesting trap of issue #133. A table nested in the selected instance table, such as
  `[gitlab_public.com]`, blocks with a diagnostic that names the nested table. `[gitlab.com]` is the legacy table of
  DEC-10.
  Governing Ref: Surprises entry on TOML nesting; recorded answer "use [gitlab_private] use [gitlab_public] design cli
  so caller can choose".
- Decision DEC-07: Every new name is defined once, as a constant in `ci-skills/lib/python/core/endpoints.py`. The
  catalog, parser, help text, result fields, tests and docs derive from those constants or are checked against them.
  Status: ACCEPTED
  Rationale: Repository rule: define constant strings once. The authority names, the selector values, the routing
  result keys and values, and the selection and routing reason tokens are `Final` constants in `core/endpoints.py`;
  `core/target.py` imports them. Tests import the same constants and build every target from them through one
  builder in `tests/python/conftest.py`; this replaces the earlier review note that literal table names in tests are
  allowed. `tools/skillkit/portability_gate.py` rejects a copy of these values as a string literal in any selected
  Python source outside the defining module (rule `contract_literal`).
  Governing Ref: pinned Standards `docs/agents/contracts/software-design.md` (revision `5bd31f1`), hardcoded-value
  rule; recorded answer "this name need define in entier code once as const"; team answers "it need define in 1 place
  in code no test should hardcode this value no code should do this it const and final and it must define read code
  follow same standard do no bypass my interface I go mental" and "do not brake abstraction".
- Decision DEC-08: `project`, `group` and `runner_id` stay optional in each GitLab instance table, exactly as in the
  flat table today. When `project` is absent the caller passes `--project`; `project = ""` stays rejected. Examples
  and the template omit `project`, so the caller always names the project.
  Status: ACCEPTED
  Rationale: The answer asks for the current behavior, which is unchanged: `project` may be left out, and the caller
  then names the project. A user-level target serves many projects, so no default project is set.
  Governing Ref: recorded answer "project default should \"\" because call need provide otherwise what project is
  this ? and I think that the current behaviour it can be empty"; current `core/target.py` `_optional_reference`
  behavior.
- Decision DEC-09: Adding `routing` to the result schemas is a MINOR change. `schema_version` moves from `1.0` to `1.1`,
  and schemas that pin `1.0` accept both versions.
  Status: ACCEPTED
  Rationale: An added optional field keeps old consumers working, per `docs/phases/CI07-SCHEMA.md`. Historical
  receipts remain valid.
  Governing Ref: `docs/phases/CI07-SCHEMA.md` versioning rules.
- Decision DEC-10: In the legacy shape, `[gitlab]` fields answer `gitlab_private` and `[gitlab.com]` answers
  `gitlab_public` when `[gitlab_public]` is absent. On the issue #133 shape, `--gitlab private` reads `[gitlab]` and
  `--gitlab public` reads `[gitlab.com]`; `routing.gitlab` names the table read (`gitlab`, `gitlab.com`). The match is
  by the table key `com`, the constant `GITLAB_LEGACY_PUBLIC_KEY` in `core/endpoints.py`; no URL is inspected.
  Without `[gitlab_routing]`, legacy routes report `kubernetes` and `harbor` as `null`. A command that requires them
  reads `[kubernetes]` and `[harbor]` directly only when the caller gives no `--gitlab` (no selector, or
  `--gitlab-profile`), which is how these invocations behaved before this change; with `--gitlab private|public` it
  blocks with the missing-tables reason, so `--gitlab public` never pairs GitLab SaaS with the person's cluster by
  default (DEC-02). With `[gitlab_routing]`, the row of the instance pairs them. The deprecation event's `table`
  names the legacy table.
  Status: ACCEPTED
  Rationale: The answer names both legacy tables. The reviewed plan's line "No silent mapping of `[gitlab.com]` to
  public" is replaced by this answer: the mapping is one named constant, applies only when `gitlab_public` is
  selected, and the load writes the deprecation event.
  Governing Ref: recorded answer "remeber gitlab mean private gitlab.com public"; DEC-05; DEC-19.
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
  - Reachability: whether the routed cluster can open a connection to the selected GitLab's KAS address is an
    infrastructure fact. The planned command checks it live, read only, before any registration, and returns BLOCKED
    naming the missing egress. A cluster with no outbound path can attach only to a self-managed GitLab that can
    reach it (receptive agent, Ultimate), never to GitLab.com. Facts and sources:
    `ci-skills/references/gitlab-agent.md`. Not implemented in #141.
  Conflict, recorded and not resolved: `docs/phases/CI14-GITLAB-CONNECTIONS.md:76` says "No new target key is
  authorized by this phase." DEC-14 adds no target key. A later change that adds a GitLab Agent key to
  `[gitlab_routing]`, for example `gitlab_routing.<instance>.agent`, falls under that line and needs approval.
  Governing Ref: [CI14-M2](ci14-gitlab-connections.md#ci14-m2-resolve-d2-and-deliver-the-agent-workflow);
  `docs/phases/CI14-GITLAB-CONNECTIONS.md:66-67,76`; docs.gitlab.com `user/clusters/agent/install`,
  `user/clusters/agent/ci_cd_workflow`, `api/cluster_agents`; team answer "same I think in saas you can not attac
  private hosted k8s. (I might wrong ) or it need different semantic or need something from k8s < ie from infra I
  don't know , read context7 read reference , our skill suppose to give this without reading online entire point of
  reference <".
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
- Decision DEC-17: `[gitlab_routing]` is optional. A GitLab-only target needs only an instance table. A command that
  requires Kubernetes or Harbor needs the selected instance's row and blocks without it (DEC-18), except the legacy
  shape without `[gitlab_routing]` (DEC-10).
  Status: ACCEPTED
  Rationale: The table answers the pairing; without it, the default is `GITLAB_DEFAULT_INSTANCE` (DEC-03), so a
  GitLab-only command has nothing else to read from it. Combined commands stay fail-closed.
  Governing Ref: team answer to review finding F33, relayed with the section C answers; DEC-02; DEC-18.
- Decision DEC-18: A command that requires a routed authority reads the table the selected `[gitlab_routing]` row
  names. A row that omits it, or a target without `[gitlab_routing]`, blocks with the existing `target is missing
  required authority tables: <names>`. Optional authorities, and legacy routes on a target without `[gitlab_routing]`
  when the caller gives no `--gitlab` (DEC-10), read the authority's own table.
  Status: ACCEPTED
  Rationale: Issue #133 requires fail-closed selection, and an omitted pairing is the only case where the row carries
  information while one table per authority exists. No new reason token.
  Governing Ref: issue #133 Expected section; review of `a9eddd5` (findings F01, F4).
- Decision DEC-19: `gitlab_private`, `gitlab_public` and the legacy key `com` are table keys. They mean only what the
  code assigns: which table `--gitlab` reads (`GITLAB_SELECTORS`), the fallback when nothing names an instance
  (`GITLAB_DEFAULT_INSTANCE`), the legacy table that answers `gitlab_public` (`GITLAB_LEGACY_PUBLIC_KEY`), and the
  `[gitlab_routing]` row of each instance. The GitLab host is the selected table's `url`. No code derives visibility,
  trust or a host from a key, or a key from a URL.
  Status: ACCEPTED
  Rationale: One meaning per name, defined in code; a reader of a target or a result learns the host from `url` and
  `origin`, not from the key.
  Governing Ref: team answer "remember it just a key it has meaning if we define meaning in coe"; DEC-07.

## Outcomes & Retrospective

- Delivered on PR #141: instance tables, the optional `[gitlab_routing]`, `--gitlab`, route-enforced Kubernetes and
  Harbor selection, the `routing` object in five command families (DEC-15), the legacy path with its deprecation
  event, the tracked mirror qualification tool, and the documents.
- Not delivered yet: candidate receipts (GPP-08) and the second install upgrade (GPP-07b). DEC-14 and DEC-16 are
  PROPOSED.
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

`gitlab_private`, `gitlab_public` and the legacy key `com` are keys (DEC-19). The code assigns them exactly this:

- `gitlab_private`: the table `--gitlab private` reads; the instance read when neither `--gitlab` nor
  `[gitlab_routing].default` names one (`GITLAB_DEFAULT_INSTANCE`); its `[gitlab_routing]` row.
- `gitlab_public`: the table `--gitlab public` reads; its `[gitlab_routing]` row.
- `[gitlab]` fields: answer `gitlab_private` for one release.
- `com` under `[gitlab]`: answers `gitlab_public` for one release when `[gitlab_public]` is absent
  (`GITLAB_LEGACY_PUBLIC_KEY`).

The host comes from the selected table's `url`. Neither key says whether that GitLab is self-managed, on gitlab.com,
reachable from the internet, or trusted.

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
(`--live-plan`, `--apply`, `--confirm-plan`) and `--describe`. The full sequence diagram, the value-source table, the
table of when an attach is possible and the project YAML live in `ci-skills/references/gitlab-agent.md`, which ships
with the skill so an AI agent reads them offline.

A `[gitlab_routing]` row is the person's declaration, and some pairings make no sense. GitLab SaaS hosted runners
cannot pull from a Harbor behind the person's DMZ, so the template and the user target keep `gitlab_public = {}`.
A command that needs Kubernetes or Harbor with `--gitlab public` then returns BLOCKED with the missing-tables reason
before any provider call. A GitLab.com project can still use the person's lab cluster through a GitLab Agent or a
self-managed runner in that cluster when the cluster has egress to GitLab.com; the person fills the row for that case.
Team answer: "2 some task in public gitlab you can not do , i.e user for example indicate his public github and
private harbor < make no sense".

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
`NO_OP` receipts and the `gitlab_mirror_smoke_cleanup` record come from one bounded qualification, the one the smoke
inventory permits when candidate evidence requires renewal:

    python tools/qualify_mirror.py --skill ~/.claude/skills/ci-skills --work-dir .internal/log/bug-133/mirror \
        --project hott/test-mirror-push --destination spyroot/test-mirror-push --source-id 261 \
        --seed-ref branches:main:<sha> --seed-ref tags:smoke-seed:<sha> --run-id <id>

Run it from the directory whose target the receipts must name (the same `./.ci-skills/target.toml` or
`CI_SKILLS_TARGET` resolution applies to both halves). The tool binds the installed lib first, then runs the installed
`gitlab_mirror.py sync`: `--live-plan`, an in-process live plan whose `target_source` must match, a seed-ref
baseline, a wrong-digest rejection, `--apply --confirm-plan <digest> --receipt-out <file>` for `APPLIED`, the same for
`NO_OP`. It then deletes only the run-created mirror and GitHub repository, each after an ID match, proves both absent,
repeats cleanup once, and writes `mirror-noop-with-cleanup-receipt.json`. It stops before any write if the
destination or a mirror already exists, the source ID or a seed ref differs, or the two target sources differ.
`--plan-only` stops after the rejection and writes no ledger. `--reconcile` reads a pending ledger and removes only
the IDs it records as created. Then run
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
    GITLAB_ROUTED_AUTHORITIES: Final = (KUBERNETES_AUTHORITY, HARBOR_AUTHORITY)
    GITLAB_DEFAULT_INSTANCE: Final = GITLAB_PRIVATE_TABLE
    GITLAB_LEGACY_PUBLIC_KEY: Final = "com"
    GITHUB_AUTHORITY, HARBOR_AUTHORITY, KUBERNETES_AUTHORITY          # authority table names
    GITLAB_PRIVATE_SELECTOR: Final = "private"; GITLAB_PUBLIC_SELECTOR: Final = "public"
    GITLAB_TARGET_OPTION: Final = "--gitlab"                          # imported by core/catalog.py
    ROUTING_RESULT_KEY: Final = "routing"; ROUTING_SELECTED_BY_KEY: Final = "selected_by"
    ROUTING_SELECTED_BY_DEFAULT, ROUTING_SELECTED_BY_LEGACY, ROUTING_ARGV_PREFIX
    GITLAB_INSTANCE_REQUIRED: Final = "{table}_target_required"
    GITLAB_ROUTING_PAIRING_UNKNOWN: Final = "{routing}_{authority}_unknown"
    GITLAB_SELECTOR_CONFLICT, ..., GITLAB_ROUTING_ROW_MISSING, GITLAB_DEPRECATION_EVENT   # reason tokens

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
        def parse(cls, data: Mapping[str, object], instances: Mapping[str, str]) -> Self: ...
        def select(self, selection: GitLabSelection) -> GitLabRoute: ...
        def route_for(self, table: str) -> Optional[GitLabRoute]: ...

`instances` maps each declared instance to the table that answers it (`_declared_instances` in `core/target.py`):
`[gitlab_private]` or `[gitlab]` fields to `gitlab_private`, `[gitlab_public]` or `[gitlab.com]` to `gitlab_public`.

Invariants enforced by `GitLabRouting.parse`:

- `[gitlab_routing]` is optional; without it the default is `GITLAB_DEFAULT_INSTANCE` and no route pairs a table.
- `default` is optional; when present it names a declared instance.
- With the table, each declared instance has exactly one row, and no row names an undeclared instance.
- Row keys are only `kubernetes` and `harbor`, and each value names a declared table of that authority.

`[gitlab]` fields beside `[gitlab_private]` are rejected before parsing with `gitlab_private_declared_twice`.

New reason tokens:

- `gitlab_private_declared_twice`
- `gitlab_routing_default_unknown`
- `gitlab_routing_row_missing`
- `gitlab_routing_row_unknown`
- `gitlab_routing_kubernetes_unknown`
- `gitlab_routing_harbor_unknown`
- `gitlab_private_target_required`
- `gitlab_public_target_required`
- `gitlab_selector_conflict` (both `--gitlab` and `--gitlab-profile`)
- `gitlab_selector_unknown` (a library caller passes a selector outside `GITLAB_SELECTORS`)

Each is a constant in `core/target.py`; the two families come from `GitLabRouting.instance_required(table)` and
`GitLabRouting.pairing_unknown(authority)`. A table nested in the selected instance table blocks with
`NESTED_TABLE_DIAGNOSTIC`, for example `[gitlab_public.com] is table com nested in [gitlab_public]: TOML reads a dot
in a table name as nesting; an instance table holds no nested table; name each GitLab [gitlab_private] or
[gitlab_public]`.

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
`tools/skillkit/portability_gate.py` (headers from `TargetContract.tables`; `[gitlab_routing]` values are not hosts;
rule `contract_literal`: a GitLab instance or routing table name, a selection or routing reason token, the
deprecation event or `--gitlab` as a string literal in any selected Python source outside `core/endpoints.py`, which
defines all of them (`catalog.GITLAB_TARGET_OPTION` is imported from there); docstrings are exempt;
`schemas/ci_skills_portability.schema.json` gains the rule and
the selector `contract`)
and `tools/check_live_acceptance.py` (`RECEIPT_SCHEMA_VERSIONS` from the catalog constants).

INTRODUCED `tools/skillkit/mirror_qualification.py` and its thin adapter `tools/qualify_mirror.py`, with
`tests/python/test_mirror_qualification.py`. `MirrorQualification.run()` drives the installed `gitlab_mirror.py`
through a `CommandRunner` and checks and deletes run-owned objects through a `MirrorProvider`; the tests replace both.
Fixture identity (`--project`, `--destination`, `--source-id`, `--seed-ref`) is input, never a constant.

MODIFIED `ci-skills/lib/python/core/catalog.py`:

- `GITLAB_TARGET_OPTION`, imported from `core/endpoints.py`, with `choices=tuple(GITLAB_SELECTORS)`;
- `GITLAB_PROFILE_OPTION` stays as the deprecated alias;
- `supports_gitlab_profile` becomes `supports_gitlab_selection` and gates both options;
- the help text states the default comes from `[gitlab_routing]`, else `gitlab_private`.

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

- `target.toml.template`: the new shape, with no `project` default and `gitlab_public = {}`.
- `ci-skills/references/access.md`: the section "GitLab private and public targets", including the `_` rule.
- INTRODUCED `ci-skills/references/gitlab-agent.md`: GitLab Agent and KAS connectivity facts with sources, the
  planned attach flow, and when a routing row makes sense; indexed as "GitLab Agent connectivity" in
  `core/catalog.py` and listed under `gitlab_runner_manager.py`.
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
    gitlab_public  = {}

    [gitlab_private]
    url = "https://gitlab.example.test"
    token_file = "gitlab-private.token"

    [gitlab_public]
    url = "https://gitlab.second.example.test"
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
- The same target without `[gitlab_routing]` returns `DRY_RUN` from `[gitlab_private]`; a runner deploy on it blocks
  with `target is missing required authority tables: harbor, kubernetes`.
- A legacy `[gitlab]` plus `[gitlab.com]` target, the shape of issue #133, returns `DRY_RUN` with `routing.gitlab`
  `gitlab`, `selected_by` `legacy`, and one deprecation warning on standard error. With `--gitlab public` it returns
  `routing.gitlab` `gitlab.com` and `selected_by` `argv:--gitlab=public`.
- `[gitlab]` fields beside `[gitlab_private]` return `BLOCKED` with `gitlab_private_declared_twice`.

A combined command reads the cluster and registry the route pairs. From the directory holding the target, the token
files it names and a runner token file `runner.token`:

    CI_SKILLS_TARGET=$PWD/target.toml python ci-skills/bin/gitlab_runner_manager.py deploy --dry-run --json \
        --gitlab private --project team/app --node worker-a --token-file runner.token --runner-id 73 \
        --description d --tag t --image harbor.example.test/example/runner@sha256:<64 hex digits>

Expected: `"status": "DRY_RUN"`, `"schema_version": "1.1"` and

    "routing": {"gitlab": "gitlab_private", "harbor": "harbor", "kubernetes": "kubernetes",
                "selected_by": "argv:--gitlab=private"}

With `--gitlab public` and the row `gitlab_public = {}`, the same command returns `BLOCKED` with `target is missing
required authority tables: harbor, kubernetes` and no `routing`, before any provider call. `access_check.py` reads
Kubernetes as an optional authority, from its own table:

    CI_SKILLS_TARGET=$PWD/target.toml python ci-skills/bin/access_check.py --dry-run --json --gitlab public

Expected: `"routing": {"gitlab": "gitlab_public", "harbor": null, "kubernetes": null, "selected_by":
"argv:--gitlab=public"}` and `"surfaces"` targets `{"gitlab": "https://gitlab.second.example.test", "kubernetes":
"admin-context -> https://api.cluster.example.test:6443"}`. Observed on the working tree on 2026-11-06 with these
inputs.

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

    {"status": "DRY_RUN", "phase": "OFFLINE_PLAN", "origin": "<[gitlab] url>",
     "target_source": "env:CI_SKILLS_TARGET;argv:--project"}

AFTER, the same shape and command on an installed copy at `c76723a` (GPP-11): `DRY_RUN`, `origin`
`https://gitlab.dcloud.run`, `routing` `{"gitlab": "gitlab", "kubernetes": null, "harbor": null, "selected_by":
"legacy"}` and one `gitlab_target_deprecated` event; with `--gitlab public`, `origin` `https://gitlab.com` and
`selected_by` `argv:--gitlab=public`.

Live scenarios per instance (GPP-11):

| Scenario | Instance (`--gitlab`) | Fixture, native ID | Result |
| --- | --- | --- | --- |
| project read | private | dcloud `hott/test` 123 | read from gitlab.dcloud.run |
| project read | public | GitLab.com `spyroot/test-self-runner` 88575800 | read from gitlab.com |
| issue #133 shape | no option / public | same two projects | DRY_RUN from each host |
| runner deploy, empty public row | public | user target | BLOCKED, no provider call |
| runner deploy, paired row | private | user target | DRY_RUN, routed to `kubernetes` + `harbor` |
| `open-bug` apply and repeat | private | `hott/test` issue 523 | APPLIED, then NO_OP |
| `open-bug` apply and repeat | public | `spyroot/test-self-runner` issue 209352261 | APPLIED, then NO_OP |

## Interfaces and Dependencies

No new library is used; `tomllib` from the Python 3.11 standard library parses targets.

In `ci-skills/lib/python/core/target.py`, the following must exist at the end:

    def load_target(path, *, required_surfaces=..., optional_surfaces=(), gitlab: Optional[str] = None,
                    gitlab_profile: Optional[str] = None) -> Target
    class GitLabSelection: from_args(args) -> Self; requested; selected_by; validate()
    class GitLabRoute: legacy; table_for(authority) -> Optional[str]; as_dict(); selection_source(source)
    class GitLabRouting: default; routes; parse(data, instances) -> Self; select(selection) -> GitLabRoute;
        route_for(table) -> Optional[GitLabRoute]

In `ci-skills/lib/python/core/endpoints.py`, the constants listed in Concrete Steps, `TargetContract.authority_of`,
`TargetContract.tables` and `TargetContract.routing_table() -> Optional[str]`. In `core/logging.py`,
`bind_log_settings`, `unbind_log_settings` and `bound_log_settings`. In `core/schema.py`, `specification_validator`.

Revision notes: initial version, derived from issue #133 and decisions DEC-01 to DEC-09. Then DEC-10 to DEC-13 during
implementation; milestones, the `bless` row and DEC-14 after the plan review; DEC-15 to DEC-18, the CONFLICT statuses
and runnable GPP-M4/GPP-M5 commands after the review of `a9eddd5`. Then GPP-10: the recorded answers replace the
CONFLICT statuses of DEC-03, DEC-05, DEC-08, DEC-10 and DEC-17; DEC-19 states that `gitlab_private`, `gitlab_public` and
`com` are keys; the mirror producer is tracked in `tools/qualify_mirror.py`.
