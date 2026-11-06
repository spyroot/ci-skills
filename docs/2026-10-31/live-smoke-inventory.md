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
| Runtime selection | Target resolver and named profiles | Host/profile isolation | Implemented in PR #129 |
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
mirror. No queue record, scratch file, log or session transcript records its creation; it was created 16 minutes
after the last PR #138 commit (`12ddee8`), which suggests an unrecorded #138 run (inference, not a record). Identity
was checked at `2026-11-06T01:33:33Z`; it was present. A delete from the receipt-renewal session was refused by
that session's permission check and sent no request. By `2026-11-06T01:35:52Z` both `repos/spyroot/test-mirror-push`
and `repositories/1442655376` returned 404, and a second read at `01:36:27Z` returned `RESOURCE_NOT_FOUND` for both.
Who deleted it is not recorded. The team asked for it to be restored so every agent can use it.

Open conflict for the next renewal: a restored `spyroot/test-mirror-push` makes the next bounded qualification stop
with `destination_present`. This inventory says "The disposable mirror destination is expected absent only after its
recorded cleanup"; the user says "READ RESTORE SO ALL AGNET CAN USE YOU DELETE REPO". It stays open until the user
decides. Evidence is kept outside the repository with the run
log of issue #133.

### Mirror qualification read-back — 2026-11-06

Candidate `931b188` (installed copy digest `78c36437`) ran the one bounded qualification for issue #133 with
`tools/qualify_mirror.py --apply`. It created private GitHub repository `1447750315` and GitLab mirror `17` for
retained source `261`, `hott/test-mirror-push`; both seed refs (`main`, `smoke-seed` at `aafe5d50`) matched, a wrong
confirmation was BLOCKED without mutation, and the confirmed repeat was NO_OP. Cleanup deleted mirror `17` and
repository `1447750315` (HTTP 204 each), read both absent by name and by ID, and repeated once with no further
DELETE (`2026-11-06T02:40:25Z`). An independent read at `02:40:34Z` returned `RESOURCE_NOT_FOUND` by name and by ID
and no remote mirror on `261`. Selected receipts are in `tests/acceptance/candidate/`; the `bcf7d59` receipts moved
to `tests/acceptance/historical/pre-gitlab-private-public/`.

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
