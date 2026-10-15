# Deliver the node onboarding combo, slice by slice

This ExecPlan is maintained in accordance with the shared standards' execution plan format (`docs/PLANS.md` at
standards revision `cae52b2`). Its Progress, Surprises & Discoveries, Decision Log and Outcomes & Retrospective
sections are kept current as work proceeds. The phase document is
[`docs/phases/CI12-NODE-ONBOARDING.md`](../../phases/CI12-NODE-ONBOARDING.md); this plan turns its two pull
requests into milestones a reader can carry out and observe. All paths are relative to the repository root. The
command surface and the gate wiring are open questions for the owner in the phase document; this plan uses the
proposed surface so the steps are concrete, and nothing here decides those questions.

## Purpose / Big Picture

After this work a user who installed ci-skills can ask their agent to onboard a host into their OpenShift cluster by
passing one machine specification and an ISO directory. The agent runs `build`, the user boots the host from the
served ISO, and the agent runs `join` with the run identifier `build` returned; `remove` with the same identifier
clears everything the build left if the join never happens; `verify` checks any ISO against any specification without
a cluster. The person observes an ISO appear in the ISO directory with its digest and its field-by-field match against
the specification in the result, the host boot from it, and the node read back Ready with the declared hostname and
role, with nothing left in the cluster carrying the run identifier. Every step reports its own plan, apply and
read-back in the shared envelope, and every verb can write a sanitized receipt bound to the installed runtime's
checksum.

## Progress

- [ ] Milestone 1, code: run scope with its two callbacks, specification loader and schema, pool comparison,
      boot-image read, pull-secret copy, ISO build and serving in one Pod, the ISO fetch and its check against the
      specification, the adapter, the thin main with the `build`, `remove` and `verify` verbs, the catalogue
      declaration with regenerated `tools.json`, result schema, routing.
- [ ] Milestone 1, gates: unit tests; the new schemas listed in the validate metaschema step per the existing
      pairing rule (the local selector is open question 4).
- [ ] Milestone 1, proof: live `build`, `verify` and `remove` from the installed candidate with the ISO read back
      and matched against the specification, attached to the pull request.
- [ ] Milestone 2, code: joiner wait, certificate approval, node state compared with the specification, the
      `join` verb, the result schema extended for the join fields.
- [ ] Milestone 2, gates: unit tests.
- [ ] Milestone 2, proof: live `join --live-plan` over the pending requests carrying the build's run identifier,
      attached to the pull request; the approval and the join mocked in the unit tests.
- [ ] Open question 1 (command surface) answered by the owner.
- [ ] Open question 2 (gate wiring for this command's receipts) answered by the owner; the committed receipts, if
      any, follow that answer.
- [ ] Phase identity `CI12` and its authority named by the owner (blocker in the phase document).
- [ ] The standards pin moved to a revision carrying the architecture contract (blocker in the phase document).

## Surprises & Discoveries

None yet. Record here anything the cluster, the release tooling or the install media does differently from the
assumptions in the phase document, with the observation that showed it; the first expected entry is which
workload, if any, needs cluster API access and the least role that works.

## Decision Log

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
  every verb runs from the installed candidate with `--receipt-out`, the receipt is the report, and its rows are
  the evidence linked by the run identifier.
- 2026-10-15, the owner's direction: the certificate approval and the node join cannot be tested live, because
  they need a real host to join; that is the only part of CI12 not proven live. The test reads the pending
  requests live (`join --live-plan`) and mocks the approval and the join with a simulated two-request sequence.
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

To be written when a milestone completes: compare the observed results with the purpose, state the gaps, and
record what the next combo should copy or avoid.

## Context and Orientation

The installed skill lives under `ci-skills/`: commands in `ci-skills/bin/`, the library in
`ci-skills/lib/python/core/`, schemas in `ci-skills/schemas/`. A combo is a thin main that composes atomic
callbacks through `WorkflowExecutor(context, Callbacks([...]))`; each callback subclasses a provider base
(`K8SCallback` for Kubernetes) and implements only the hooks it needs from `collect`, `run`, `update` and
`cleanup`; the executor runs the first three in order for each callback, stops on an incomplete result, and runs
cleanup in reverse for every entered callback on success and on failure, setting `state.failed` before cleanup
when the run failed and again when a cleanup hook raises. State is one object the executor binds once; callbacks
hand values to each other only through its named fields. A mutating verb returns an offline plan with
`--dry-run`, a live plan with `--live-plan`, and applies only with `--apply --confirm-plan <digest>`, where the
digest is computed from the caller's inputs and the resolved target so the apply reproduces it; it re-verifies
the target before writing and reads the result back; the shared `APPLIED` and `NO_OP` values describe the
outcome. The command is declared in `ci-skills/lib/python/core/catalog.py`, from which `ci-skills/tools.json`,
`--help` and `--describe` are rendered; the validate workflow fails on a stale `tools.json`. The functional
checksum over `ci-skills/bin/`, `ci-skills/lib/` and `ci-skills/schemas/` binds every live receipt to the runtime
that produced it. The contribution guide, `docs/contribution_guide.md`, is the authority for all of this; the
GitLab adapter in `ci-skills/lib/python/core/gitlab/gitlab_action.py` is the pattern the OpenShift adapter copies.

Terms used below: a machine specification is the YAML the agent passes, describing the host's identity, operating
system, cluster membership, network and storage, plus the first NIC's hardware address, the install device, the
hostname and the interface used for the build; the machine pool is the set of the cluster's machine objects the
specification targets, read and compared as CI11's `ocp_machine.py --node-spec` defines; the boot image is the
installer image the cluster publishes for its own version; a joiner is the first pending client certificate
request a booted host makes with the bootstrap credential, matched by the declared hostname; the run identifier
is the random value `build` generates after confirmation, from which every temporary name derives and which
`join` and `remove` receive; the envelope is the shared JSON result every installed command prints, declared by
the paired result schema; a receipt is the
sanitized copy of it the portable writer produces with `--receipt-out`; the installed candidate is the skill
installed from the pull request head as the README's live smoke setup describes.

## Plan of Work

Milestone 1 adds `ci-skills/lib/python/core/kubernetes/run_scope.py` (run identifier, deterministic names and
labels, the namespaced and the cluster-scoped `ResourceSet` bound to the operation label, reconstruction from an
identifier, and the `OpenRunScope` and `RemoveScope` callbacks), `ci-skills/lib/python/core/ocp_machines.py` with
`ci-skills/schemas/machine-spec.schema.json` (loader, validation, pool read and comparison),
`ci-skills/lib/python/core/ocp_iso.py` (boot-image stream read, pull-secret copy, the build-and-serve Pod with its
ConfigMap, the NodePort Service, the ISO fetch into the ISO directory with read-back, the check of the embedded
node configuration against the specification),
`ci-skills/lib/python/core/ocp_onboard.py` (`OnboardState`, `OnboardAction`, `ConfirmOnboardOperation`,
`OnboardWorkflow`), the thin main
`ci-skills/bin/ocp_node_onboard.py` with the `build`, `remove` and `verify` verbs, the declaration in
`ci-skills/lib/python/core/catalog.py` with `ci-skills/tools.json` regenerated,
`ci-skills/schemas/results/ocp-node-onboard.schema.json`, the routing in `ci-skills/SKILL.md`, the unit tests
`tests/python/test_run_scope.py` and `tests/python/test_ocp_node_onboard.py`, and the metaschema entries in
`.github/workflows/validate.yml` paired in `lib/bash/automation/bless.bash` as open question 4 allows. Milestone 2
adds `ci-skills/lib/python/core/ocp_csr.py` and `ci-skills/lib/python/core/ocp_join.py`, the `join` verb in the
adapter, the thin main and the catalogue declaration, the join fields in the result schema, and
`tests/python/test_ocp_join.py`. Each milestone edits only the files its pull request owns in the phase
document's map; any receipt committed into the gate follows the owner's answer to open question 2 and is not
part of either milestone until then. The two milestones, each with its goal, work, result and proof:

### Milestone 1: `build`, `verify` and `remove`, from specification to a checked, served ISO

Goal: an agent passes one specification and an ISO directory and gets an ISO on disk that carries that specification,
served in the cluster, with the run identifier to hand on, and can check any ISO or clear the scope with that
identifier. Work: the modules, schemas, adapter, thin main, catalogue declaration, routing and tests listed under Plan
of Work, then the live run. Result: `ocp_node_onboard.py build` and `remove` exist in the installed catalogue with
`--dry-run`, `--live-plan` and `--apply --confirm-plan`, and `verify` as a read-only verb, invoked and read as the
phase document's "Invocation and the result the agent reads" shows; a successful build ends with the per-field match
of the ISO against the specification; a successful build apply leaves the ISO in the ISO directory and the whole scope
(namespace, ServiceAccount, any binding, Secret, ConfigMap, the build-and-serve Pod, the Service) in the cluster, all
carrying the run identifier; an apply that fails before `serving_kept` is recorded leaves only the ISO, if the fetch
completed; `remove --run-id` leaves nothing carrying the identifier. Proof: the unit tests named under Validation and
Acceptance, the validate workflow green on the exact head, and the live `build` result whose rows show the ISO digest
and size read back, every declared field matching the specification, and whose `run_id` the next milestone consumes, a
live `verify` of that ISO, and a live `remove` whose read-back shows nothing left, all attached to the pull request as
receipts.

### Milestone 2: `join`, from booted host to Ready node

Goal: after the user boots the host from the served ISO, the agent passes the run identifier and the same
specification and gets a Ready node and an empty scope. Work: the joiner wait, the approval loop, the node read-back,
the verb, the result-schema extension and tests, then the read-only live plan. Result: `ocp_node_onboard.py join
--run-id <id> --spec <SPEC>` approves exactly the requests its selector matches, reads the node Ready with the
hostname and role the specification declares, removes the whole scope with absence read-back, and is `NO_OP` when the
node is already Ready and nothing is pending. Proof: the approval-loop tests with a simulated two-request sequence
(the approval and the join are mocked by the owner's decision, since they need a real host), the validate workflow
green on the exact head, and the live `join --live-plan` result over the pending requests carrying the build's run
identifier, attached to the pull request.

## Concrete Steps

`<SPEC>`, `<DIR>`, `<ISO>`, `<RECEIPT>`, `<digest>` and `<id>` below stand for the agent's own arguments and for
values the previous step returned; by project rule no example value is written here. Work in a worktree cut from main.
For milestone 1, implement the modules in the order the phase diagram lists them so each callback's state field exists
before its consumer. After each module run `./bless.sh --all` from the repository root and expect every line to end in
`ok`; after the catalogue declaration run the render tool and expect `tools.json` regenerated and `python
tools/render_manifest.py --check` to exit 0. Run `python ci-skills/bin/ocp_node_onboard.py --describe` and expect the
command contract with the `build`, `remove` and `verify` verbs and no credentials read. Run `python
ci-skills/bin/ocp_node_onboard.py build --spec <SPEC> --iso-dir <DIR> --dry-run --json` and expect `DRY_RUN` in phase
`OFFLINE_PLAN` with the plan digest, the name pattern and no API call; run the same with `--live-plan` and expect
`PLANNED` in phase `LIVE_PLAN` with the identity read back and the same digest. Push and read the validate workflow
back green on the exact head. Install the candidate as the README's live smoke setup describes and run the remaining
steps from the installed copy against the live cluster: `build --spec <SPEC> --iso-dir <DIR> --apply --confirm-plan
<digest> --json --receipt-out <RECEIPT>` and expect `PASS`, `result_action` `APPLIED`, an ISO in the ISO directory,
fetched over the served URL, whose digest and size appear in the result, every declared field matching the
specification in the `VerifyNodeImage` row, the run identifier under `readback` and the serving URL read back; then
`verify --iso <ISO> --spec <SPEC> --json --receipt-out <RECEIPT>` on that ISO and expect `PASS` with the same match
rows; then, for a build you do not intend to join, `remove --run-id <id> --dry-run --json` for its digest and `remove
--run-id <id> --apply --confirm-plan <digest> --json --receipt-out <RECEIPT>` and expect `PASS` with every object
absent. Then run the README rerun procedure on this head from the installed candidate so every existing committed
receipt carries this checksum, run `python tools/check_live_acceptance.py --root . --json` and expect `PASS` with no
`functional_digest_mismatch`. Attach the three results to the pull request, commit the refreshed set, push the head
they came from and expect the validate workflow green on it; that head is the one that merges. The report of every
live step is its receipt, produced the same way as every other live run in this repository: `--receipt-out` on the
installed command, sanitized by the portable writer, bound to the functional checksum, with the rows that prove the
outcome quoted in the pull request body.

Milestone 2 follows the same sequence up to the live run, which is read-only by the owner's decision: `join
--run-id <id> --spec <SPEC> --live-plan --json --receipt-out <RECEIPT>` from the installed candidate expects
`PLANNED` with the pending requests listed, the selector shown and no approval; the `APPLIED` and `NO_OP` paths
are exercised only by the unit tests with a simulated two-request sequence, because the approval and the join
need a real host. The plan result is attached to the pull request with the mocked rows marked as mocked.

## Validation and Acceptance

Milestone 1 is accepted when `tests/python/test_run_scope.py` shows names derive from the identifier, `from_id`
reproduces them, the namespaced and cluster-scoped sets carry the operation label, and removal deletes exactly the
owned set including the cluster-scoped objects; when `tests/python/test_ocp_node_onboard.py` shows the loader
accepting a valid specification and rejecting an invalid one, the pool comparison reporting match and drift per field,
a dry-run digest confirming a later apply with the same inputs and target and rejecting another target, a `remove`
digest for one run identifier rejecting an apply for another, `tests/python/test_action_modes.py` showing the shared
validator reproducing every existing rule under one canonical reason each with the GitLab adapter's cases still
passing and this adapter accepting `--receipt-out` on a live plan, a workflow-level interrupt test asserting a
rendered `BLOCKED` result with the read-back fields, a schema test of the sketched documents against the paired
schema, a dry run with `--iso-dir` accepted and one with `--output-dir` still rejected, the build callback blocking
with a classified reason on timeout and on a failed init container, the fetch blocking on a missing ISO directory, an
existing ISO without `--overwrite` and an unreachable served URL, the serving read-back, the keep-on-success branch
(`serving_kept` set, every object kept) and the remove-on-failure branch (each creator removes its object), the
cleanup-failure case after a successful serve (scope kept, `BLOCKED`), and the ISO check against a fixture built from
a known configuration, reporting a changed hostname and a changed hardware address as drift and `PARTIAL` with the
scope kept; when the catalogue consistency test and the render check pass; when the validate workflow is green on the
exact head; and when the live `build`, `verify` and `remove` results attached to the pull request show the ISO digest
read back, every field matching the specification, and nothing left carrying the identifier. Milestone 2 is accepted
when the join live plan is tested (accepted with `--receipt-out`, no confirm and no approval, `PLANNED` with the
pending-request rows and the run identifier), when the approval loop is tested inside one bounded `run` against a
simulated two-request sequence (client, then serving after approval, then issued and Ready, with the final read-back),
a foreign signer, an expired deadline, the no-Node-before-client-approval ordering, and the already-Ready path that
makes a repeated `join` `NO_OP`, all on the existing GitHub test path; when those mocked runs show the joiner request,
the approved requests by name and signer, the node Ready with the declared hostname and role, and nothing carrying the
identifier; and when the live `join --live-plan` result shows the pending requests read from the cluster. The approval
and the join are the one part of this plan proven by mock, by the owner's decision. A green build alone demonstrates
nothing; the live results and the mocked approval tests do.

## Idempotence and Recovery

Every `build` apply generates a new identifier (whether a caller may pass one is open question 1), so repeated
builds do not collide; an
existing ISO with the same name in the ISO directory blocks unless the agent passes `--overwrite`. Resources owned
by the work: the run-scoped namespace, the ServiceAccount, any ClusterRoleBinding, the pull-secret Secret, the
node-configuration ConfigMap, the build-and-serve Pod, the NodePort Service, and the ISO file. On a failed build
each callback removes what it created, with absence read-back; on a successful build everything is kept for
`join`; the ISO and the ISO directory are never removed. `join` approves only what its selector matches, is
`NO_OP` when the node is already Ready, and never deletes a node. Recovery: `remove --run-id <id>` deletes
everything carrying the identifier, cluster-scoped objects included, without waiting for a joiner; it is the path
for an abandoned build, for a `join` that failed before its last step, and for the case where a cleanup hook
fails after the serving was kept (the scope stays intact and the result is `BLOCKED` with the identifier).
Rollback: `build` changes no persistent cluster state beyond the scope, so `remove` is its rollback; a
certificate approval cannot be undone and a joined node is persistent cluster state this combo never removes, so
the rollback of `join` is the user's decision through the cluster's own node deletion, and the approved request
names and signers in the result are the audit trail. Nothing else carries the identifier.

## Artifacts and Notes

Evidence for each milestone is the live results attached to the pull request (sanitized receipts written with
`--receipt-out`), the validate run URL on the exact head, and, once open question 2 is answered, whatever the owner
decides is committed into the gate. The pull request body carries the chain's evidence rows in the six-row style, one
row per live step, each pointing at its receipt and all linked by the run identifier and the functional checksum: read
the specification and the pool, build the ISO, check the ISO against the specification, serve it, read the pending
requests live, approve the join (mocked), the node Ready with the declared hostname and role (mocked), remove the
scope; the two mocked rows say so and point at their tests instead of a receipt. Keep the excerpts to the rows that
prove the outcome: ISO digest and size, the per-field match, run identifier, joiner request, approved request names,
node condition, and the role the first live run pinned for the scope.

## Interfaces and Dependencies

`OnboardAction(SkillAction)`: `spec_path`, `iso_dir`, `iso_path` (for `verify`), `run_id` (generated by `build`,
required by `join` and `remove`; a caller value on `build` is open question 1), `node` (optional),
`build_timeout`, `join_timeout`, `approve_timeout`, `overwrite`, and the shared mode flags `dry_run`,
`live_plan`, `apply`, `confirm_plan`. `OnboardState(DeliveryState)`: `spec`, `pool`, `run_scope`, `boot_image`,
`pull_secret_ref`, `iso`, `served_url`, `iso_check`, `serving_kept`, `joiner`, `approved`, `node`.
`RunScope`: `RunScope.new() -> RunScope`, `RunScope.from_id(run_id: str) -> RunScope`, `scope.run_id`,
`scope.namespace`, `scope.labels -> Mapping[str, str]`, `scope.name(kind: str) -> str`,
`scope.resources(service) -> ResourceSet` (namespaced), `scope.cluster_resources(service) -> ResourceSet`
(cluster-scoped); `RunScopeAction(SkillAction)`: `scope`, `role` (optional); `OpenRunScope` and `RemoveScope`,
each a `K8SCallback[RunScopeAction]` composable by any chain. `OnboardWorkflow`: `prepare()`, `callbacks() ->
Callbacks[OnboardState]`, `execute() -> int`, mirroring `GitLabWorkflow`; `ConfirmOnboardOperation`, mirroring
`ConfirmGitLabOperation`. Callbacks: `LoadMachineSpec` (local), `ReadMachinePool`, `ReadBootImage`,
`OpenRunScope`, `ResolvePullSecret`, `BuildNodeImage`, `ServeNodeImage`, `FetchNodeImage`, `VerifyNodeImage`
(local), `WaitForJoiner`, `ApproveJoinRequests`, `ReadNodeState`, `RemoveScope`, each `K8SCallback[OnboardAction]`
except the two local callbacks and the two scope callbacks. Dependencies: the existing `K8SAuth`, `ResourceSet`, the
Secret-copy pattern, the catalogue
source and render tool, the report renderer, the portable receipt writer and the provenance checksum; the
release's node-image tooling inside the build container; no new third-party dependency.
