# Deliver the node onboarding combo, slice by slice

This ExecPlan is maintained in accordance with the shared standards' execution plan format (`docs/PLANS.md` at
standards revision `cae52b2`). Its Progress, Surprises & Discoveries, Decision Log and Outcomes & Retrospective
sections are kept current as work proceeds. The phase document is
[`docs/phases/CI12-NODE-ONBOARDING.md`](../../phases/CI12-NODE-ONBOARDING.md); this plan follows its three
ordered deliveries. All paths are relative to the repository root. CI12 owns the invocation and destination
contracts, concrete examples, implementation ownership and acceptance requirements.

## Purpose / Big Picture

A user first prepares reusable tools in a ci-skills-owned Pod, then builds and verifies a native OpenShift node
ISO at the caller-selected in-cluster destination, then boots the host and runs the join step. The installed
commands return location and evidence; the ISO is not copied to the agent computer. Each delivery is usable and
proven independently before the next one consumes it.

## Progress

- [x] CI12.0 / PR-CI12-0: deliver and prove the reusable tool Pod, shared download/install operations,
      same-Pod consumption, present-tool `NO_OP` and owned-resource cleanup.
- [x] CI12.1 / PR-CI12-1: consume merged CI12.0; deliver native ISO build/verify/remove with both native input
      routes, exact destination read-back, embedded-configuration inspection and cleanup.
- [x] CI12.2 / PR-CI12-2: deliver join with GitHub mocked CSR/join coverage, the sole live-test exception.

## Surprises & Discoveries

None yet. Record here anything the cluster, the release tooling or the install media does differently from the
phase document, with the observation that showed it.

## Decision Log

Historical decisions from 2026-10-15 are preserved below. The
[CI12 delivery revision](../../phases/CI12-NODE-ONBOARDING.md#authority) supersedes their custom machine schema,
pool preflight, workstation ISO fetch, local verification, one-Pod build-and-serve design and two-PR order.
The current milestones below follow CI12.0, CI12.1 and CI12.2.

- 2026-10-15, the owner's direction: every temporary name (namespace, ServiceAccount, binding, Secret, ConfigMap,
  Pod, Service, labels) is derived from a random run identifier generated per run and removed in cleanup; the
  ISO directory and the node are the calling agent's arguments; no environment specific is written into the
  repository. This entry is the cited authority for `R-CI12-004` until the catalogue carries it.
- 2026-10-15, the owner's direction: a combo is proven by one live run from the installed candidate whose evidence
  rows are bound to one functional checksum; mocked runs do not count as proof; only the live-tested head merges.
- 2026-10-15, the owner's direction: requirements, the user-facing command surface, install or usage steps and
  gate behaviour are the owner's to approve; a review finding on any of them becomes an open question in the
  phase document, not a decision. The spec shows how the agent invokes the command and the JSON it reads.
- 2026-10-15, the owner's direction: CI12 includes checking that the generated ISO carries the specification it
  was built from, as the last step of `build` and as a standalone `verify` verb, and the joined node's hostname
  and role are compared with the specification on `join`. The live-test report follows the existing pattern:
  every live verb runs from the installed candidate with `--receipt-out`, the receipt is the report, and its rows are
  the evidence linked by the run identifier.
- 2026-10-15, the owner's direction: the certificate approval and the node join cannot be tested live, because
  they need a real host to join; that is the only part of CI12 not proven live. CSR handling and join are mocked
  in GitHub tests with a simulated two-request sequence; no live pending-request read is required.
- 2026-10-15, mechanism, from the fourth review: the join live plan is a defined mode, `K8SAuth` plus a read of the
  pending requests by selector, with `--receipt-out` accepted on it as this adapter's policy; the mode rules are one
  shared validator in `core/action_modes.py`, not a copy of the GitLab adapter's; `build`'s generated identifier
  is outside its digest and the caller-supplied `--run-id` of `join` and `remove` is inside theirs; the JSON the
  agent reads is the existing envelope exactly (`summary` an object of counts, `errors` entries of `source` and
  `reason`, `plan_digest` bare hex); on drift the ISO check completes with `PARTIAL` and the scope is kept; an
  interrupt is converted at the adapter boundary into a rendered `BLOCKED` result; the existing receipt set is
  refreshed on each pull request head; how the embedded configuration is extracted locally is open question 7.
- 2026-10-15, the owner's follow-up recorded in the queue: build from the agent-supplied specification; inspect the
  generated ISO's embedded configuration with the supported tooling for the selected image and compare the
  declared hostname, network and hardware address, install device and the other supported fields with the
  embedded values, since existence, size and checksum alone prove nothing; expected values come from the runtime
  specification, never from the bench; verify serving, fetch and cleanup live; keep live ISO proof distinct from
  the mocked approval and join, which is the only live exception; use the existing GitHub test and check path,
  with no new gate and no separate plan. Mechanism from the same note: the approval loop lives in one bounded
  `run`, since the executor runs each hook once; a repeated `join` takes an already-Ready path to `NO_OP`; only
  the generated build identifier is outside the plan digest; a documentation or test-only head change does not
  invalidate functional proof.
- 2026-10-15: the ISO is built inside the cluster, because a workstation build cannot reach the host's network and
  streams gigabytes over a slow tunnel.
- 2026-10-15: the certificate step approves by a selector (the declared hostname and the two signers) and ends on
  the joiner's issued serving request and a Ready node, with a deadline; "nothing pending" is not the stop
  condition.
- 2026-10-15: the live-ISO route with a hand-built Ignition document is out of scope.
- 2026-10-15, mechanism from the first conformance review: the scope is owned by the run identifier and lives from
  the build to the end of `join` or `remove`; the toolbox adapter `WorkflowAction` and `EnsureNamespace` are not
  reused; an OpenShift adapter mirrors the GitLab one; the callbacks live in the modules CI11 already names as
  owners; post-join CI preparation leaves this plan for lack of recorded authority.
- 2026-10-15, mechanism from the second review: the run identifier is generated after confirmation and is not
  part of the plan digest, which binds the caller's inputs and the resolved target; the pull secret is copied into
  the run namespace as a Secret and never written to disk by this combo; the command is declared in
  `core/catalog.py` and `tools.json` regenerated; the comparison baseline is the cluster's machine pool as CI11
  defines it, with no reference file; `RemoveScope` is delivered by milestone 1.
- 2026-10-15, mechanism from the third review: the keep-or-remove decision rests on `state.serving_kept`, recorded
  by `VerifyNodeImage.cleanup`, the last callback of `build`, which runs first in reverse order, because the
  executor also sets `state.failed` when a cleanup hook raises; the
  joiner is the first pending client certificate request matching the declared hostname, because the Node object
  exists only after that request is approved; one Pod builds and serves, as CI11's source does, so the ISO never
  crosses workloads, and the ISO reaches the ISO directory by a fetch over the served URL because the Kubernetes
  service has no file-copy call; `join` takes the same specification as `build` so the hostname has a source.

## Outcomes & Retrospective

All three deliveries are merged; [delivery evidence](../../phases/CI12-NODE-ONBOARDING.md#live-smoke-evidence)
links live preparation, ISO and storage results and mocked CSR/join acceptance.
Unobserved native fields remain explicit.

## Context and Orientation

The installed skill lives under `ci-skills/`: commands in `ci-skills/bin/`, the library in
`ci-skills/lib/python/core/`, schemas in `ci-skills/schemas/`. The
[contribution guide](../../contribution_guide.md) owns callback composition, shared state, command declaration
and reports. [CI12's reuse boundary](../../phases/CI12-NODE-ONBOARDING.md#existing-code-and-reuse-boundary)
names the existing implementations this delivery consumes.

## Plan of Work

### Milestone 0: reusable tool Pod (CI12.0 / PR-CI12-0)

Deliver the installed `prepare_tool_pod.py prepare/remove` capability and shared `DownloadArtifact` /
`InstallArtifact` operations together, following
[CI12.0](../../phases/CI12-NODE-ONBOARDING.md#phase-ci120--reusable-tool-pod-pr-ci12-0).
Prove actual download/install, a different callback consuming the same Pod, present-tool `NO_OP`, and removal
with live read-back. No ISO build is required for this milestone.

### Milestone 1: native ISO build, verify and remove (CI12.1 / PR-CI12-1)

Start from merged CI12.0 and its live evidence. Follow
[CI12.1](../../phases/CI12-NODE-ONBOARDING.md#phase-ci121--native-ocp-iso-combo-pr-ci12-1) for native input
pass-through, destination checking, build, inspection and cleanup. Prove the generated ISO exists at the exact
caller-selected in-cluster destination and contains the expected configuration. Exercise both native input
routes from the installed candidate; no custom native schema or workstation ISO fetch belongs to this milestone.

### Milestone 2: join from a booted host to a Ready node (CI12.2 / PR-CI12-2)

Follow [CI12.2](../../phases/CI12-NODE-ONBOARDING.md#phase-ci122--monitor-and-join-pr-ci12-2) after the user boots
the host. Deliver the bounded hostname/two-signer approval sequence, Ready-node read-back, already-Ready `NO_OP`
and owned-scope removal. CSR handling and join use GitHub mocked acceptance, including missing reverse DNS,
unrelated requests and expiry; no live CSR read or approval is required for acceptance.

## Concrete Steps

1. Follow the proposed preparation invocation in
   [CI12.0](../../phases/CI12-NODE-ONBOARDING.md#phase-ci120--reusable-tool-pod-pr-ci12-0), using the caller's
   tool inputs and the digest returned by its planning path. Install the candidate and collect its live proof
   before advancing to CI12.1.
2. Follow CI12.1's [native input examples](../../phases/CI12-NODE-ONBOARDING.md#caller-input-and-schema-boundary)
   and [native procedure](../../phases/CI12-NODE-ONBOARDING.md#native-procedure-and-callback-composition).
   Use the caller's actual destination and native inputs; read the artifact back there, inspect it in the cluster,
   then exercise verify/remove.
3. Run CI12.2's mocked CSR/join acceptance on GitHub and attach evidence marked as mocked.

## Validation and Acceptance

Follow [CI12 Gates](../../phases/CI12-NODE-ONBOARDING.md#gates),
[Live Smoke Evidence](../../phases/CI12-NODE-ONBOARDING.md#live-smoke-evidence) and
[End-to-End Test](../../phases/CI12-NODE-ONBOARDING.md#end-to-end-test). Reuse the existing GitHub checks and
portable report writer; do not add a gate or treat mocked preparation/ISO results as live evidence.

## Idempotence and Recovery

Resource ownership, retention and removal follow the corresponding CI12.0, CI12.1 and CI12.2 sections above.
Cleanup releases owned temporary resources and preserves caller-owned storage. The generated ISO remains at its
requested cluster destination; removal does not imply deleting a joined node.

## Artifacts and Notes

Use the evidence described in [CI12 Live Smoke Evidence](../../phases/CI12-NODE-ONBOARDING.md#live-smoke-evidence):
installed functional identity, linked operation/read-back records and cleanup, with CSR/join rows marked mocked.
Receipt placement follows [CI12 Gates](../../phases/CI12-NODE-ONBOARDING.md#gates).

## Interfaces and Dependencies

CI12.0 owns the reusable tool Pod and dependency inputs; CI12.1 owns native input selection, cluster destination
and artifact references; CI12.2 owns join selection. Use the interfaces and file ownership declared in the
[CI12 implementation plan](../../phases/CI12-NODE-ONBOARDING.md#implementation-plan), including the shared run
scope. The planned shared scope interface supports generated or caller-selected namespaces as CI12.0 specifies:

`RunScope.new(*, namespace: Optional[str] = None, run_id: Optional[str] = None) -> RunScope` constructs a
candidate without provider access. `RunScope.resolve(service, run_id) -> RunScope` requires one live scope;
`RunScope.find_all(service, run_id) -> tuple[RunScope, ...]` returns observed scopes, including an empty tuple.
Each scope exposes `run_id`, `namespace`, `owns_namespace`, `labels`, `name(kind)`, namespaced
`resources(service)` and `cluster_resources(service)`.

`RunScopeAction(SkillAction)` carries a concrete `scope` for `OpenRunScope`. `ResolveRunScopeAction(SkillAction)`
carries a `run_id` for `ResolveRunScopes` and `RemoveScope`; removal therefore needs no fabricated namespace
before the API lookup. These Kubernetes callbacks share `RunScopeState`, which retains the lookup identity,
observed scopes and retained resource identities. CI12.0 exposes no unused role input and creates no RBAC;
the ISO consumer's authorization belongs to CI12.1.

Preparation requires one unambiguous live scope. After creating a generated namespace, opening reads the labels
back before staging resources; an ambiguous result fails and cleanup removes only that invocation's created
namespace. This check is not an atomic reservation. Explicit removal processes the scopes found for the selected
run identifier, rechecks ownership labels before deletion and preserves caller-selected namespaces. Empty lookup
is verified `NO_OP`.
