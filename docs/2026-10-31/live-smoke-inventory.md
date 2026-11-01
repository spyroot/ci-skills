# Operator-allocated live smoke assets

This is the shared inventory for live smoke and behavioral tests across phases. The operator approved these
allocations on 2026-10-31. All four authorities—Harbor, GitLab, GitHub and Kubernetes—are resolved at runtime
from the selected `target.toml`; the default location is `~/.ci-skills/target.toml`.

Allocation is permission to use the listed assets within the rules below. It is not evidence that a planned
repository exists or that a capability has passed. Update this inventory after verified allocation, recording
native IDs and the verification date before another phase reuses the asset.

## Reusable pairs

The name in the first column must match on GitHub and GitLab. `dcloud` means the operator-selected `[gitlab]`
authority. The public-service row uses named profile `[gitlab.com]`, selected with
`--gitlab-profile com`. Provider URLs and credentials remain runtime inputs.

| Pair / purpose | GitHub allocation | GitLab allocation | State |
| --- | --- | --- | --- |
| `test-self-runner` / self-hosted runners | `spyroot/test-self-runner` | dcloud `hott/test-self-runner` | Planned |
| `test-k8s-agent` / project Agent access | `spyroot/test-k8s-agent` | dcloud `hott/test-k8s-agent` | Planned |
| `test-mirror-push` / GitLab → GitHub | `spyroot/test-mirror-push` | dcloud `hott/test-mirror-push` | Planned |
| `test-self-runner` / public GitLab | Reuse runner repository above | GitLab.com `spyroot/test-self-runner` | Planned |

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
Missing repositories, changed IDs or unexpected names must stop preparation, not trigger silent recreation.
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
| GitLab | `[gitlab]` or explicitly selected named profile | Verify host, identity, project ID and permissions |
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
