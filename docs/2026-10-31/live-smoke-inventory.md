# Operator-allocated live smoke assets

This is the shared inventory for live smoke and behavioral tests across phases. The operator approved these
allocations on 2026-10-31. All four authorities—Harbor, GitLab, GitHub and Kubernetes—are resolved at runtime
from the selected `target.toml`; the default location is `~/.ci-skills/target.toml`.

Allocation is permission to use the listed assets within the rules below. It is not evidence that a planned
repository exists or that a capability has passed. Update this inventory after verified allocation, recording
native IDs and the verification date before another phase reuses the asset.

## Reusable pairs

The name in the first column must match on GitHub and GitLab. `dcloud` is the GitLab whose `url` the selected
target holds in `[gitlab_private]`, read with `--gitlab private` or no option. The GitLab.com row is read through
`[gitlab_public]` with `--gitlab public`; its `url` in the selected target names GitLab.com. The table keys carry no
other meaning. Provider URLs and credentials remain runtime inputs.

| Pair / purpose | GitHub allocation | GitLab allocation | State |
| --- | --- | --- | --- |
| `test-self-runner` / self-hosted runners | `spyroot/test-self-runner` | dcloud `hott/test-self-runner` | Allocated |
| `test-k8s-agent` / project Agent access | `spyroot/test-k8s-agent` | dcloud `hott/test-k8s-agent` | Planned |
| `test-mirror-push` / GitLab → GitHub | `spyroot/test-mirror-push` | dcloud `hott/test-mirror-push` | Qualified |
| `test-self-runner` / GitLab.com | Runner repository above | GitLab.com `spyroot/test-self-runner` | Allocated |

Prepare each pair only when its scenario is ready:

- Rename `spyroot/test_project_creation_macos_clean` to `spyroot/test-self-runner`.
  Preserve GitHub repository ID `1344617680` and its history.
- Rename `spyroot/builder_phase1_live_linux_20260824` to `spyroot/test-k8s-agent`.
  Preserve GitHub node ID `R_kgDOUCnwrQ` and record its numeric repository ID during preflight.
- Create the three private dcloud projects and the empty private GitLab.com project listed above.
- Create the private GitHub `test-mirror-push` repository through CI-14's create-if-absent scenario,
  verify ref synchronization and subsequent reuse/no-op behavior, then remove only that run-created repository ID.
  The operator approved this disposable destination on 2026-11-01; retain its GitLab source. Remove the run-created
  GitLab mirror configuration first, verify both objects absent, and repeat cleanup once without another deletion.
  Stop if either object was already present or its identity differs. This permits one bounded qualification when
  candidate evidence requires renewal; it does not authorize automatic reruns or another repository name.

The rename candidates were inspected during planning on 2026-10-31: README-only content, no registered
runners, workflows or webhooks. Recheck those facts immediately before renaming. If their usage or identity
has changed, stop that allocation and reconcile it; do not choose another repository automatically.

After allocation, names and purposes are locked across phases. Record both the old and new name for a rename.
Missing retained repositories, changed IDs or unexpected names must stop preparation, not trigger silent recreation.
The disposable mirror destination is expected absent only after its recorded cleanup.
Reserve `test-mirror-pull` for later work; do not allocate it for CI-14/CI-15 closure.

## Protected existing assets

These assets remain reusable in their existing roles. Different GitHub and GitLab names do not establish a mirror pair.
IDs recorded during planning or earlier receipts must be read back before use; they are not fresh live evidence.

| Asset | Recorded identity / role | Rule |
| --- | --- | --- |
| GitHub `spyroot/ci-skills-selfhosted-smoke-20261011` | Repository `1414527179`; runner smoke | Preserve; no rename |
| dcloud `hott/test` | Project `123`; existing general GitLab test bench | Preserve; no rename |
| dcloud `hott/ci-skills-runner-proof` | Historical project `124`; runner assignment | Preserve; verify before reuse |
| GitHub `spyroot/test_project_creation_linux` | Historical repository `1344490288`; access fixture | Preserve |
| GitHub `spyroot/test_project_creation_macos` | Existing project-preflight workflow | Preserve |
| GitHub `spyroot/pr-coordinator-dispatch-smoke` | Existing cross-project dispatch fixture | Preserve |
| GitHub `spyroot/ci-skills` | Delivery repository | Normal PR CI only; no consumer smoke mutations |
| dcloud `hott/isovalent` | Historical diagnostic target | Preserve; not disposable |

Preserve all unallocated pre-existing repositories. General repository cleanup is a separate task.

## Supporting authorities

| Authority | Runtime input | Reuse and ownership |
| --- | --- | --- |
| Harbor | `[harbor]` | Resolve immutable digests; preserve borrowed images and repositories |
| GitLab | `[gitlab_private]`/`[gitlab_public]`, `--gitlab` | Verify host, identity, project ID and permissions |
| GitHub | `[github]` and existing authentication | Verify repository ID, runner routing and possible charges |
| Kubernetes | `[kubernetes]` | Verify context/server; preserve borrowed namespaces, controllers and credentials |

Known retained Harbor assets include `hott/ci13-runner-toolbox` and `hott/go-golangci-toolbox`.
`hott/ci13-chain-proof` appears in historical evidence. Verify current state before reuse; never infer
that an image or namespace is disposable from its name. Build each required toolbox variant once and reuse
its immutable digest across applicable scenarios.

## Run budget and cleanup

All listed allocations are authorized for live smoke and behavioral testing with these constraints:

- One active run per fixture. Record candidate identity, scenario, created native IDs/UIDs and dispatch IDs.
- No scheduled smoke, automatic full reruns, repeated repository allocation or unrequested matrix expansion.
- Inspect potentially billable GitHub Actions routing before dispatch. Do not introduce paid capacity,
  upgrades, caches or artifact storage without separate approval.
- ARC qualification uses `minRunners=0`, `maxRunners=1` and exactly two sequential jobs. Each job has a
  two-minute limit; observation stops after 20 minutes.
- Poll sequentially, normally at least 30 seconds apart. Back off unchanged state, honor rate-limit headers
  and bound each scenario's reads and deadline.
- Reconcile uncertain writes before retrying. A rerun must identify the failed scenario and the changed cause.
- Collect evidence first. Remove only disposable resources created and owned by that run, in dependency order.
  Match native IDs and Kubernetes UIDs with ownership records; never delete by name prefix alone.
- Preserve reusable repositories and all borrowed resources. Verify disposable-resource absence independently,
  then repeat cleanup once to prove idempotency.
- Failed or interrupted cleanup blocks another allocation until recorded leftovers are reconciled.

## Delivery coverage

| Requirement | Implementation / asset owner | Gate | Observed status |
| --- | --- | --- | --- |
| Stable paired names | Reusable pairs above | ID/name preflight and inventory read-back | Allocations planned |
| Preserve working fixtures | Protected assets above | Before/after identity read-back | Required |
| Runtime selection | Instance tables, `--gitlab` | Host/instance isolation | PR #141; legacy read until TD-11 |
| Bounded execution | Dispatcher and job limits | Dispatch IDs, limits and pacing | Approved; execution unclaimed |
| Owned cleanup | Resource lifecycle handling | ID/UID match, absence and repeated cleanup | Required for every run |

## Mirror qualification read-back — 2026-11-01

Candidate `bcf7d59` created private GitHub repository `1441633602` and GitLab mirror `15` for retained GitLab project
`261`, `hott/test-mirror-push`. Both branch and tag refs matched; a confirmed repeat returned NO_OP without mutation.
Cleanup removed only those two run-created IDs, proved both absent by independent native reads, and repeated once
without another deletion. The source and its `main`/`smoke-seed` refs remain present; source CI stays disabled.
No consumer Actions job or GitLab pipeline was dispatched. Original receipts are selected in
`tests/acceptance/candidate/`; the NO_OP copy includes separately captured cleanup observations.

### Unrecorded destination leftover — 2026-11-06

GitHub `spyroot/test-mirror-push` existed as repository `1442655376`, private, created `2026-11-02T07:40:01Z`, holding
branch `main` and tag `smoke-seed` at `aafe5d50bbe4a1884b7b4f86668651e9ddbd5222`; GitLab project `261` had no remote
mirror. No tracked record names who created it; it was created 16 minutes after the last PR #138 commit
(`12ddee8`), which suggests an unrecorded #138 run (inference, not a record). Identity was checked at
`2026-11-06T01:33:33Z`; it was present. A delete attempt during receipt renewal sent no request. By
`2026-11-06T01:35:52Z` both `repos/spyroot/test-mirror-push` and `repositories/1442655376` returned 404, and a second
read at `01:36:27Z` returned `RESOURCE_NOT_FOUND` for both. Who deleted it is not recorded.

### Mirror qualification read-back — 2026-11-06

Candidate `931b188` (installed copy digest `78c36437`) ran the one bounded qualification for issue #133 with
`tools/qualify_mirror.py --apply`. It created private GitHub repository `1447750315` and GitLab mirror `17` for
retained source `261`, `hott/test-mirror-push`; both seed refs (`main`, `smoke-seed` at `aafe5d50`) matched, a wrong
confirmation was BLOCKED without mutation, and the confirmed repeat was NO_OP. Cleanup deleted mirror `17` and
repository `1447750315` (HTTP 204 each), read both absent by name and by ID, and repeated once with no further
DELETE (`2026-11-06T02:40:25Z`). An independent read at `02:40:34Z` returned `RESOURCE_NOT_FOUND` by name and by ID
and no remote mirror on `261`. Selected receipts are in `tests/acceptance/candidate/`; the `bcf7d59` receipts moved
to `tests/acceptance/historical/pre-gitlab-private-public/`.

After that cleanup, we restored `spyroot/test-mirror-push`, the repository the selected target's `[github]` table
names, at the team's request for shared use: repository `1447753892`, private, created `2026-11-06T02:43:05Z`, with
`main` and tag `smoke-seed` at `aafe5d50bbe4a1884b7b4f86668651e9ddbd5222` pushed from GitLab `261`. Both refs were
read back at `02:43:56Z`. The access receipt in `tests/acceptance/candidate/access.json` was captured with it present.

The restored `spyroot/test-mirror-push` (`1447898875`) blocked the next qualification with `destination_present`.
The operator resolved this conflict on 2026-11-11: retire that exact ID and keep the same-name GitHub destination
disposable. The retained GitHub access fixture is `spyroot/test-self-runner` (`1344617680`). Preserve GitLab source
`261`; the mirror destination stays absent after recorded cleanup, with no restoration or automatic rerun.

### Mirror qualification read-back — 2026-11-06, PR #142 head `fa7063c`

At the team's request we deleted the restored repository `1447753892` so the bounded qualification could run on the
head of the follow-up pull request #142. A read at `2026-11-06T06:05:43Z` showed it private, created
`2026-11-06T02:43:05Z`, with `main` and `smoke-seed` at `aafe5d50`; `DELETE repos/spyroot/test-mirror-push` returned
204; at `06:05:59Z` both `repos/spyroot/test-mirror-push` and `repositories/1447753892` returned 404, and a second
read at `06:06:18Z` returned `RESOURCE_NOT_FOUND` by name and by ID with no remote mirror on `261`.

Candidate `fa7063c` (installed copy digest `63bdb745`) then ran `tools/qualify_mirror.py --apply`, run
`bug133-mirror-20261106-fa7063c`. It created private GitHub repository `1447896555` and GitLab mirror `18` for
retained source `261`; both seed refs matched; a wrong confirmation returned BLOCKED `mirror_plan_mismatch` without
mutation; the confirmed repeat was NO_OP. Cleanup deleted mirror `18` and repository `1447896555` (HTTP 204 each),
read both absent by name and by ID, and repeated once with no further DELETE (`2026-11-06T06:10:00Z`). An independent
read at `06:10:07Z` returned `RESOURCE_NOT_FOUND` by name and by ID and no remote mirror on `261`. The `931b188`
receipts moved to `tests/acceptance/historical/pre-gitlab-private-public-followup/`.

After that cleanup, we restored `spyroot/test-mirror-push` again at the team's request for shared use: repository
`1447898875`, private, created `2026-11-06T06:10:37Z`, with `main` and tag `smoke-seed` at `aafe5d50` pushed from
GitLab `261`. Both refs read back at `06:11:10Z` and again at `06:11:31Z`. The `fa7063c` install then captured the
access receipt with the repository present (PASS on GitHub, GitLab and Kubernetes and on five live checks), and
`tools/check_live_acceptance.py --root . --no-aggregate --receipts tests/acceptance/candidate` returned PASS with
functional digest `5e988fb7` and `problems: []`.

The earlier `6f9f1da` qualification remains unchanged under `tests/acceptance/historical/pre-mirror-owned-id/`.
The `1f2b6f5` qualification remains in the merged CI-14 history. The prior `ae47632` originals remain unchanged under
`tests/acceptance/historical/pre-ci15-build-retention/`. The renewed `bcf7d59` run verifies the corrected runner
candidate with the same retained source and creation-ID checks; it does not allocate another retained repository.

## GitLab.com runner project allocation — 2026-11-06

GitLab.com `spyroot/test-self-runner` is project `88575800`, owner namespace `spyroot` (user `38452341`), private and
empty. It was absent at `2026-11-06T01:19:57Z` (`ci-api get --gitlab public`, `RESOURCE_NOT_FOUND`), created at
`01:20:18Z` with `POST /projects` (`visibility=private`, no README), and read back at `01:20:26Z` through the same
installed `ci-api --gitlab public`. It is retained across phases; only run-owned resources in it are disposable.

## Issue #133 smoke objects — 2026-11-06

The issue #133 live smoke created one issue per GitLab instance with `gitlab_issue.py open-bug`, read each back
independently, and repeated the confirmed apply as NO_OP. Both issues are retained as smoke records:

- dcloud `hott/test` (project `123`): issue iid `38`, id `523`, opened `2026-11-06T02:00:09Z`.
- GitLab.com `spyroot/test-self-runner` (project `88575800`): issue iid `1`, id `209352261`, opened
  `2026-11-06T02:00:15Z`.

## Runner baseline support allocation

The runner smoke's runtime ToolboxSpec defines build namespace and app `test-self-runner-toolbox`, and Harbor
repository `test-self-runner-toolbox` under the runtime-selected project. Build this one image with checksum-pinned
GitHub runner/CLI archives and runtime root `/opt/actions-runner`; retain its namespace, BuildConfig and immutable
image for later phases. Reuse the existing GitLab shell-runner toolbox. Each provider's single consumer job has a
two-minute limit and manual-only routing; configuration pushes must not execute a job.

The GitHub rename retained repository ID `1344617680` and its existing history. The private dcloud project is `262`.
These repositories are retained across phases; only run-owned runner registrations and workloads are disposable.

The retained support namespace UID is `7ae80f69-fa57-4cc4-8be7-c80ac05ccbec`; its BuildConfig UID is
`7e695883-88eb-4ffd-af61-b81856556a84`. The verified image is
`sha256:567b6f13bb9d658bf8a917b395f728515c428c1d799a39a84900dc2c4a6c34b1`.
The namespace was allocated by this smoke run for retention across phases. Reuse this image digest; the initial
image lacked libicu and is not qualified. The one corrected build passed after exact-image dependency reproduction.

### Mirror qualification read-back — 2026-11-11, cleanup-repeat candidate `8230363`

After fresh identity/ref read-back, the operator-authorized retirement removed repository `1447898875`; independent
name/ID absence and a delayed repeat passed. One installed-candidate qualification then created repository
`1453643750` and GitLab mirror `20`, verified both seed refs, rejected a wrong confirmation and returned NO_OP on
repeat. Exact-owned cleanup removed both new IDs, independently proved absence and repeated without another delete.
GitLab source `261`, its refs and disabled CI were preserved. No consumer Actions job or GitLab pipeline ran.
The selected access receipt uses retained `spyroot/test-self-runner`; prior candidate receipts are preserved under
`tests/acceptance/historical/pre-ci15-cleanup-repeat/`.

### Issue #151 merge request description smoke — 2026-11-21, candidate `a46f295` (superseded)

Superseded by the `ff5aa48` run below: `a46f295` was rebased onto `main` `989e4a8` and is not on the branch. The
installed candidate `a46f295` (digest `a4b58db7`) ran `gitlab_mr.py create --description-file` on dcloud `hott/test`
(project `123`), run `issue151-20261121225221`. `gitlab_repository.py commit` created run-owned branches
`ci-skills/issue151-20261121225221` (commit `0a2f900d`) and `ci-skills/issue151-20261121225221-empty` (commit
`09a24981`) from the default branch, with `[skip ci]` commit messages. Push pipelines `15313` and `15316` read
`skipped`; merge-request pipeline `15318` for `!5` read `skipped` before cleanup and returned 404 after `!5` was
deleted. No merge-request pipeline was read for `!4` before its deletion. MR `!4` (id `1451`) was created from a
270-byte Markdown file. An independent read returned the same text minus its terminal newline (269 bytes). The repeated
apply returned NO_OP, and an apply confirmed with the earlier digest after a file edit returned BLOCKED
`confirm_plan_mismatch_rerun_dry_run` without a write. MR `!5` (id `1452`) was created from an empty file, and GitLab
read back `""`. Cleanup re-read each identity, then deleted MRs `!4` and `!5` and both branches (HTTP 204 each). At
`2026-11-21T23:13:46Z` an independent read returned `RESOURCE_NOT_FOUND` for both MRs and showed neither branch. A
repeated cleanup sent no DELETE. Open MRs `!1`–`!3` and the other branches were unchanged.

### Mirror qualification read-back — 2026-11-21, issue #151 candidate `a46f295` (superseded)

Superseded by the `ff5aa48` qualification below; the `a46f295` receipts were not kept because that commit is not on the
branch. GitHub `spyroot/test-mirror-push` was absent and source `261` had no remote mirror, with `main` and `smoke-seed`
at `aafe5d50`, at `2026-11-21T23:14:54Z`. One installed-candidate qualification, run `issue151-mirror-20261121-a46f295`,
created repository `1467035373` and GitLab mirror `27`. It verified both seed refs, rejected a wrong confirmation and
returned NO_OP on repeat. Cleanup reported `PASS_INDEPENDENT_ABSENCE_AND_REPEAT`; at `2026-11-21T23:19:05Z` GitHub
returned 404 by name and by ID and source `261` had no remote mirror. The access receipt was captured from the same
install.

### Issue #151 merge request description smoke — 2026-11-22, candidate `ff5aa48`

The installed candidate `ff5aa48` (digest `d50e7afb`), rebased onto `main` `989e4a8`, ran
`gitlab_mr.py create --description-file` on dcloud `hott/test` (project `123`), run `issue151-20261122004650`.
`gitlab_repository.py commit` created run-owned branches `ci-skills/issue151-20261122004650` (commit `f98ebed8`) and
`ci-skills/issue151-20261122004650-empty` (commit `693bd236`) from the default branch, with `[skip ci]` commit
messages. For `f98ebed8`, push pipeline `15633` and merge-request pipeline `15645` read `skipped` before cleanup. For
`693bd236`, two reads before cleanup returned no pipeline. The plan listed `description` in `fields` and its output
held no description text. MR `!6` (id `1471`) was created from a 270-byte Markdown file. An independent read returned
the same text minus its terminal newline (269 bytes). The repeated apply returned NO_OP, and an apply confirmed with
the earlier digest after a file edit returned BLOCKED `confirm_plan_mismatch_rerun_dry_run` without a write. MR `!7`
(id `1476`) was created from an empty file, and GitLab read back `""`. Cleanup re-read each identity, then deleted MRs
`!6` and `!7` and both branches (HTTP 204 each). At `2026-11-22T00:49:01Z` an independent read returned
`RESOURCE_NOT_FOUND` for both MRs and showed neither run branch. A repeated cleanup sent no DELETE. Open MRs `!1`–`!3`
were unchanged.

### Mirror qualification read-back — 2026-11-22, issue #151 candidate `ff5aa48`

One installed-candidate qualification, run `issue151-mirror-20261122-ff5aa48`, created repository `1467093241` and
GitLab mirror `29` from source `261` with `main` and `smoke-seed` at `aafe5d50`. The applied sync read back verified, a
wrong confirmation returned BLOCKED `mirror_plan_mismatch`, and the repeat returned NO_OP. Cleanup reported
`PASS_INDEPENDENT_ABSENCE_AND_REPEAT`; at `2026-11-22T01:10:21Z` GitHub returned 404 by ID and by name, source `261`
listed no remote mirror, and mirror `29` returned 404. The access receipt was captured from the same install at
`2026-11-22T01:08:32Z`. Two earlier `access_check` runs from this install, at `00:58Z`, returned BLOCKED only because
`event_trace` timed out reading `events_v1`; the `a46f295` install passed at `01:01Z`, and both installs passed back to
back at `01:04Z` and `01:06Z`, with `events.py`, `live.py` and `cli.py` unchanged between them. The receipts superseded
here are `main`'s `e9c6608` set, preserved under `tests/acceptance/historical/pre-issue151-mr-description/`.

### Mirror qualification read-back — 2026-11-22, issue #149 candidate `5db056f`

Candidate `5db056f` is the issue #149 branch merged with the #158 head `2d39da3`. One installed-candidate
qualification, run `pr157-mirror-20261122-5db056f`, created repository `1467484939` and GitLab mirror `33` from source
`261` with `main` and `smoke-seed` at `aafe5d50`. The applied sync read back verified, a wrong confirmation returned
BLOCKED `mirror_plan_mismatch`, and the repeat returned NO_OP. Cleanup reported `PASS_INDEPENDENT_ABSENCE_AND_REPEAT`;
at `2026-11-22T11:26:17Z` GitHub returned 404 by ID and by name, mirror `33` returned 404, and source `261` listed no
remote mirror. The access receipt was captured from the same install at `2026-11-22T11:23:08Z`. The receipts
superseded here are the #158 `ff5aa48` set, preserved under `tests/acceptance/historical/pre-issue149-ci-api-selection/`,
and this branch's earlier `857070e` set (repository `1467450060`, mirror `32`), preserved under
`tests/acceptance/historical/pre-issue149-158-integration/`.

### Mirror qualification read-back — 2026-11-22, guarded image adoption candidate `e0c93c6`

Candidate `e0c93c6` is the guarded image adoption branch merged with `main` `00a32b4` and the issue #149 head
`9ce2d7b`. One installed-candidate qualification, run `pr147-mirror-20261122-e0c93c6`, created repository
`1467491120` and GitLab mirror `34` from source `261` with `main` and `smoke-seed` at `aafe5d50`. The applied sync read
back verified, a wrong confirmation returned BLOCKED `mirror_plan_mismatch`, and the repeat returned NO_OP. Cleanup
reported `PASS_INDEPENDENT_ABSENCE_AND_REPEAT`; at `2026-11-22T11:33:46Z` GitHub returned 404 by ID and by name, mirror
`34` returned 404, and source `261` listed no remote mirror. The access receipt was captured from the same install at
`2026-11-22T11:29:18Z`. The issue #149 `5db056f` set it supersedes is preserved under
`tests/acceptance/historical/pre-guarded-image-adoption-at-5db056f/`.

### Mirror qualification read-back — 2026-11-22, runner cleanup confirmation candidate `77f2565`

Candidate `77f2565` is the runner cleanup confirmation branch merged with `main` `df6994f` and the guarded image
adoption head `30f11bc`. One installed-candidate qualification, run `pr148-mirror-20261122-77f2565`, created
repository `1467496859` and GitLab mirror `35` from source `261` with `main` and `smoke-seed` at `aafe5d50`. The
applied sync read back verified, a wrong confirmation returned BLOCKED `mirror_plan_mismatch`, and the repeat returned
NO_OP. Cleanup reported `PASS_INDEPENDENT_ABSENCE_AND_REPEAT`; at `2026-11-22T11:40:54Z` GitHub returned 404 by ID and
by name, mirror `35` returned 404, and source `261` listed no remote mirror. The access receipt was captured from the
same install at `2026-11-22T11:36:36Z`. The `e0c93c6` set it supersedes is preserved under
`tests/acceptance/historical/pre-runner-cleanup-confirmation/`.
