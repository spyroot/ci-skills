# Deliver the node onboarding combo, slice by slice

This ExecPlan is maintained in accordance with the shared standards' execution plan format (`docs/PLANS.md` at
standards revision `cae52b2`). Its Progress, Surprises & Discoveries, Decision Log and Outcomes & Retrospective
sections are kept current as work proceeds. The phase document is
[`docs/phases/CI12-NODE-ONBOARDING.md`](../../phases/CI12-NODE-ONBOARDING.md); this plan turns its two pull
requests into milestones a reader can carry out and observe. All paths are relative to the repository root.

## Purpose / Big Picture

After this work a user who installed ci-skills can ask their agent to onboard a host into their OpenShift cluster
by passing one machine specification and an ISO directory. The agent runs `build`, the user boots the host from
the served ISO, and the agent runs `join` with the run identifier `build` returned; `remove` with the same
identifier clears everything the build left if the join never happens. The person observes an ISO appear in the
ISO directory with its digest in the result, the host boot from it, and the node read back Ready with the
declared role, with nothing left in the cluster carrying the run identifier. Every step reports its own plan,
apply and read-back, and every verb leaves a sanitized receipt bound to the installed runtime's checksum.

## Progress

- [ ] Milestone 1, code: run scope with its opening callback, specification loader and schema, pool comparison,
      boot-image read, pull-secret copy, ISO build and serving, serving removal, the adapter, the thin main with
      the `build` and `remove` verbs, the catalogue declaration with regenerated `tools.json`, result schema,
      routing.
- [ ] Milestone 1, gates: unit tests; the new schemas listed in the validate metaschema step and selected by
      `lib/bash/automation/bless.bash`; the checker's validator, context and required set extended for the
      OpenShift kind with the `build` operation; the README rerun list extended.
- [ ] Milestone 1, proof: live `build` receipt from the installed candidate with the ISO read back, plus every
      other committed receipt refreshed at the same checksum.
- [ ] Milestone 2, code: joiner wait, certificate approval, node state, the `join` verb, the result schema extended
      for the join fields.
- [ ] Milestone 2, gates: unit tests; the checker's required set extended with the `join` operations.
- [ ] Milestone 2, proof: live `join` receipts (applied, then no-op) carrying the build's run identifier, plus every
      other committed receipt refreshed at the same checksum.
- [ ] Phase identity `CI12` approved by the project owner.
- [ ] The missing `.coordination` authority files created or named by the owner (blocker in the phase document).
- [ ] The standards pin moved to a revision carrying the architecture contract (blocker in the phase document).

## Surprises & Discoveries

None yet. Record here anything the cluster, the release tooling or the install media does differently from the
assumptions in the phase document, with the observation that showed it; the first expected entry is which
workload, if any, needs cluster API access and the least role that works.

## Decision Log

- 2026-10-15, the owner's direction: every temporary name (namespace, ServiceAccount, binding, Secret, Job, Pod,
  Service, labels) is derived from a random run identifier generated per run and removed in cleanup; the ISO
  directory and the node are the calling agent's arguments; no environment specific is written into the
  repository. This entry is the cited authority for `R-CI12-004` until the catalogue carries it.
- 2026-10-15, the owner's direction: a combo is proven by one live run from the installed candidate whose evidence
  rows are bound to one functional checksum; mocked runs do not count as proof.
- 2026-10-15: the ISO is built inside the cluster, because a workstation build cannot reach the host's network and
  streams gigabytes over a slow tunnel.
- 2026-10-15: the certificate step approves by a selector (joiner identity and the two signers) and ends on the
  joiner's issued serving request and a Ready node, with a deadline; "nothing pending" is not the stop condition.
- 2026-10-15: the live-ISO route with a hand-built Ignition document is out of scope.
- 2026-10-15, from the first conformance review: the serving resources are owned by the run identifier and live
  from a successful `build` to the end of `join`; `build` returns the identifier and `join` takes it as `--run-id`.
- 2026-10-15, from the same review: the toolbox adapter `WorkflowAction` and `EnsureNamespace` are not reused; an
  OpenShift adapter mirrors the GitLab one and the namespace is a `ResourceSet`-owned document.
- 2026-10-15, from the same review: the callbacks live in the modules CI11 already names as owners
  (`core/ocp_machines.py`, `core/ocp_iso.py`, `core/ocp_csr.py`), so each path has one owner.
- 2026-10-15, from the same review: post-join CI preparation leaves this plan; it has no recorded authority and
  becomes its own proposal when the owner records it.
- 2026-10-15, from the second conformance review: the run identifier is generated after confirmation and is not
  part of the build's plan digest, so a dry run's digest is reproduced by the apply; the ISO destination is
  `--iso-dir`, since the shared `--output-dir` means paired report output and rejects a dry run; the pull secret is
  copied into the run namespace as a Secret and never written to disk by this combo; a `remove` verb keyed by the
  identifier is the recovery path; the command is declared in `core/catalog.py` and `tools.json` regenerated; each
  pull request refreshes every committed receipt because it changes the functional checksum; the comparison
  baseline is the cluster's machine pool as CI11 defines it, with no reference file; `join` `NO_OP` is the
  required repeatable receipt and `join` `APPLIED` is captured once; `RemoveServing` is delivered by milestone 1.

## Outcomes & Retrospective

To be written when a milestone completes: compare the observed receipts with the purpose, state the gaps, and
record what the next combo should copy or avoid.

## Context and Orientation

The installed skill lives under `ci-skills/`: commands in `ci-skills/bin/`, the library in
`ci-skills/lib/python/core/`, schemas in `ci-skills/schemas/`. A combo is a thin main that composes atomic
callbacks through `WorkflowExecutor(context, Callbacks([...]))`; each callback subclasses a provider base
(`K8SCallback` for Kubernetes) and implements only the hooks it needs from `collect`, `run`, `update` and
`cleanup`; the executor runs the first three in order for each callback, stops on an incomplete result, and runs
cleanup in reverse for every entered callback on success and on failure, setting `state.failed` first when the
run failed. State is one object the executor binds once; callbacks hand values to each other only through its
named fields. A mutating verb returns an offline plan with `--dry-run`, a live plan with `--live-plan`, and
applies only with `--apply --confirm-plan <digest>`, where the digest is computed from the caller's inputs so the
apply reproduces it; it re-resolves target and credential before writing and reads the result back; the shared
`APPLIED` and `NO_OP` values describe the outcome. The command is declared in `ci-skills/lib/python/core/catalog.py`,
from which `ci-skills/tools.json`, `--help` and `--describe` are rendered; the validate workflow fails on a stale
`tools.json`. The functional checksum over `ci-skills/bin/`, `ci-skills/lib/` and `ci-skills/schemas/` binds every
live receipt to the runtime that produced it, so any change under those paths invalidates every committed receipt
until it is refreshed. The contribution guide, `docs/contribution_guide.md`, is the authority for all of this; the
GitLab adapter in `ci-skills/lib/python/core/gitlab/gitlab_action.py` is the pattern the OpenShift adapter copies.

Terms used below: a machine specification is the YAML the agent passes, describing the host's identity, operating
system, cluster membership, network and storage, plus the first NIC's hardware address, the install device, the
hostname and the interface used for the build; the machine pool is the set of the cluster's machine objects the
specification targets, read and compared as CI11's `ocp_machine.py --node-spec` defines; the boot image is the
installer image the cluster publishes for its own version; a joiner is a node object that appears when a booted
host first contacts the cluster; the run identifier is the random value `build` generates after confirmation,
from which every temporary name derives and which `join` and `remove` receive; a receipt is the sanitized JSON
the portable writer produces, accepted by the live-acceptance checker only when its kind, operation and result
action are in the checker's required set, its checksum matches the candidate, it passes the validator for its kind
and it binds to exactly one setup context; a refresh is the README procedure (`README.md#live-acceptance-reruns`)
that re-captures every committed receipt from the installed candidate after a runtime change.

## Plan of Work

Milestone 1 adds `ci-skills/lib/python/core/kubernetes/run_scope.py` (run identifier, deterministic names and
labels, the namespaced and the cluster-scoped `ResourceSet` bound to the operation label, reconstruction from an
identifier, and the `OpenRunScope` callback that applies the namespace, the ServiceAccount and the binding),
`ci-skills/lib/python/core/ocp_machines.py` with `ci-skills/schemas/machine-spec.schema.json` (loader, validation,
pool read and comparison), `ci-skills/lib/python/core/ocp_iso.py` (boot-image stream read, pull-secret copy,
in-cluster build Job, ISO-directory copy, read-back, serving Pod and NodePort Service, serving removal),
`ci-skills/lib/python/core/ocp_onboard.py` (`OnboardState`, `OnboardAction`, `OnboardWorkflow`), the thin main
`ci-skills/bin/ocp_node_onboard.py` with the `build` and `remove` verbs, the declaration in
`ci-skills/lib/python/core/catalog.py` with `ci-skills/tools.json` regenerated,
`ci-skills/schemas/results/ocp-node-onboard.schema.json`, the routing in `ci-skills/SKILL.md`, the checker
extension in `tools/check_live_acceptance.py` (a validator for the OpenShift receipt kind, a kubernetes setup
context from the access-check receipt's kubernetes surface, a match rule, and `build` `APPLIED` in the required
set) with its tests in `tests/python/test_live_acceptance.py`, the unit tests `tests/python/test_run_scope.py` and
`tests/python/test_ocp_node_onboard.py`, the metaschema entries in `.github/workflows/validate.yml` paired with a
widened selector in `lib/bash/automation/bless.bash`, the OpenShift operations in the README rerun list, and the
receipt `tests/acceptance/receipts/ocp-node-build-applied.json` together with every other committed receipt
refreshed. Milestone 2 adds `ci-skills/lib/python/core/ocp_csr.py` and `ci-skills/lib/python/core/ocp_join.py`,
the `join` verb in the adapter, the thin main and the catalogue declaration, the join fields in the result
schema, the checker's `join` operations (`NO_OP` required, `APPLIED` accepted) with the applied-and-no-op pairing
rule, `tests/python/test_ocp_join.py`, the README rerun list entry, and the receipts
`tests/acceptance/receipts/ocp-node-join-applied.json` and `ocp-node-join-no_op.json` together with every other
committed receipt refreshed. Each milestone edits only the files its pull request owns in the phase document's
map, plus the refreshed receipts under the declared receipt directories and the historical directory, which
every runtime-changing pull request rewrites by the README procedure and which the map does not list per file.
The two milestones, each with its goal, work, result and proof:

### Milestone 1: `build` and `remove`, from specification to served ISO

Goal: an agent passes one specification and an ISO directory and gets an ISO on disk, served in the cluster, with
the run identifier to hand on, and can clear the scope with that identifier. Work: the modules, schemas, adapter,
thin main, catalogue declaration, routing, tests, gate pairing and checker extension listed under Plan of Work,
then the live run and the receipt refresh. Result: `ocp_node_onboard.py build` and `remove` exist in the installed
catalogue with `--dry-run`, `--live-plan` and `--apply --confirm-plan`; a successful build apply leaves the ISO in
the ISO directory and the serving Pod, Service, ServiceAccount, any binding and the namespace in the cluster, all
carrying the run identifier, with the Secret and the Job already gone; a failed apply leaves only the ISO when the copy
completed; `remove --run-id` leaves nothing carrying the identifier. Proof: the unit tests named under Validation
and Acceptance, the validate workflow green on the exact head, and the live receipt whose rows show the ISO digest
and size read back and whose `run_id` the next milestone consumes, with the checker accepting the whole refreshed
set.

### Milestone 2: `join`, from booted host to Ready node

Goal: after the user boots the host from the served ISO, the agent passes the run identifier and gets a Ready node
and an empty scope. Work: the joiner wait, the approval loop, the node read-back, the verb, the result-schema
extension, tests and checker extension, then the live run and the receipt refresh. Result:
`ocp_node_onboard.py join --run-id <id>` approves exactly the requests its selector matches, reads the node
Ready, removes the serving resources and the namespace with absence read-back, and is `NO_OP` when the node is
already Ready and nothing is pending. Proof: the approval-loop tests, the validate workflow green on the exact
head, and two live receipts (applied, then no-op on rerun) carrying the build's run identifier, with the namespace
absent afterwards and the checker accepting the whole refreshed set.

## Concrete Steps

`SPEC`, `DIR`, `<digest>`, `<fingerprint>` and `<id>` below stand for the agent's own arguments and for values the
previous step returned; by project rule no example value is written here. Work in a worktree cut from main. For
milestone 1, implement the modules in the order the phase diagram lists them so each callback's state field
exists before its consumer. After each module run `./bless.sh --all` from the repository root and expect every
line to end in `ok`; after the catalogue declaration run the render tool and expect `tools.json` regenerated and
`python tools/render_manifest.py --check` to exit 0. Run `python ci-skills/bin/ocp_node_onboard.py --describe` and
expect the command contract with the `build` and `remove` verbs and no credentials read. Run `python
ci-skills/bin/ocp_node_onboard.py build --spec SPEC --iso-dir DIR --dry-run --json` and expect `DRY_RUN` in phase
`OFFLINE_PLAN` with the plan digest, the name pattern and no API call; run the same with `--live-plan` and expect
`PLANNED` in phase `LIVE_PLAN` with the identity read back and the same digest. Push and read the validate workflow
back green on the exact head. Install the candidate with `./install.sh --skills-dir ~/.claude/skills --dry-run
--json` then `--apply --confirm-install <fingerprint>`. From the installed copy run `build --spec SPEC --iso-dir
DIR --apply --confirm-plan <digest> --receipt-out tests/acceptance/receipts/ocp-node-build-applied.json` against
the live cluster and expect `PASS`, an ISO in the ISO directory whose digest and size appear in the result, the run
identifier in the result, the serving URL read back, and the Secret and the Job absent. Run
`remove --run-id <id> --apply --confirm-plan <digest>` once against a build you do not intend to join and expect
`PASS` with the namespace absent. Then run the README rerun procedure on this head from the installed candidate
so every other committed receipt carries this checksum, run `python tools/check_live_acceptance.py --root . --json`
and expect `PASS` with the build receipt in the accepted list and no `receipt_not_declared`, `kind_unexpected` or
`functional_digest_mismatch` problem, which requires the checker extension from the same branch. Commit the
refreshed set, push, and expect the validate workflow green on that head; that head is the one that merges.

Milestone 2 follows the same sequence with `join --run-id <id>`: the user boots the host between `build` and
`join`; the first live run expects `PASS` with `APPLIED` rows for the approved requests and the node Ready; a
rerun expects `NO_OP` and the namespace absent; both receipts and the refreshed set are committed before the
checker is run.

## Validation and Acceptance

Milestone 1 is accepted when `tests/python/test_run_scope.py` shows names derive from the identifier, `from_id`
reproduces them, the namespaced and cluster-scoped sets carry the operation label, and removal deletes exactly the
owned set including the cluster-scoped objects; when `tests/python/test_ocp_node_onboard.py` shows the loader
accepting a valid specification and rejecting an invalid one, the pool comparison reporting match and drift per
field, a dry-run digest confirming a later apply with the same inputs, a dry run with `--iso-dir` accepted and one
with `--output-dir` still rejected, the build callback blocking with a classified reason on timeout, on a failed
Job and on a missing ISO directory, the serving read-back, and the keep-on-success and remove-on-failure branches
of the serving and namespace cleanups; when `tests/python/test_live_acceptance.py` shows an OpenShift `build`
receipt accepted when it passes the validator and binds to the kubernetes context, an unknown OpenShift operation
still `receipt_not_declared`, a receipt missing a validator field `kind_unexpected` or the field's own problem, and
a receipt without a matching context `setup_context_missing_or_ambiguous`; when the catalogue consistency test and
the render check pass; when the validate workflow is green on the exact head; and when the live receipt shows
`PASS` with the ISO digest read back from the ISO directory and the checker accepts the refreshed set. Milestone 2
is accepted when the approval loop is tested against a simulated two-request sequence (client, then serving after
approval), a foreign signer and an expired deadline; when the pairing rule accepts `join` `NO_OP` alone and `NO_OP`
with `APPLIED`; when the live receipts show the joiner, the approved requests by name and signer, the node Ready
with the declared role, and the namespace absent. A green build alone demonstrates nothing; the receipts do.

## Idempotence and Recovery

Every `build` apply generates a new identifier, so repeated builds do not collide; an existing ISO with the same
name in the ISO directory blocks unless the agent passes `--overwrite`. Resources owned by the work: the
run-scoped namespace, the ServiceAccount, the ClusterRoleBinding, the pull-secret Secret, the build Job, the
serving Pod and NodePort Service, and the ISO file. The Secret and the Job are removed on success, on failure
and on interrupt, with absence read-back; the serving, the ServiceAccount, the binding and the namespace are
removed on failure and kept on success for `join`; the ISO and the ISO directory are never removed. `join`
approves only what its selector matches, is `NO_OP` when the node is already Ready, and never deletes a node.
Recovery: `remove --run-id <id>` deletes everything carrying the identifier, cluster-scoped objects included,
without waiting for a joiner; it is the path for an abandoned build, for a `join` that failed before its last
step, and for the one edge where a cleanup hook fails after the serving was kept (the result is then `BLOCKED`
with the identifier). Rollback: `build` changes no persistent cluster state beyond the scope, so `remove` is its
rollback; a certificate approval cannot be undone and a joined node is persistent cluster state this combo never
removes, so the rollback of `join` is the user's decision through the cluster's own node deletion, and the
approved request names and signers in the receipt are the audit trail. Nothing else carries the identifier.

## Artifacts and Notes

Evidence for each milestone is its sanitized receipts under `tests/acceptance/receipts/`, the refreshed receipts
beside them, the validate run URL on the exact head, and the checker's accepted list. Keep the receipt excerpts in
the pull request body to the rows that prove the outcome: ISO digest and size, run identifier, joiner name,
approved request names, node condition, and the role the first live run pinned for the build Job.

## Interfaces and Dependencies

`OnboardAction(SkillAction)`: `spec_path`, `iso_dir`, `run_id` (required by `join` and `remove`), `node`
(optional), `build_timeout`, `join_timeout`, `approve_timeout`, `overwrite`, and the shared mode flags `dry_run`,
`live_plan`, `apply`, `confirm_plan`. `OnboardState(DeliveryState)`: `spec`, `pool`, `run_scope`, `boot_image`,
`pull_secret_ref`, `iso`, `served_url`, `joiner`, `approved`, `node`. `RunScope`: `RunScope.new() -> RunScope`,
`RunScope.from_id(run_id: str) -> RunScope`, `scope.run_id`, `scope.namespace`, `scope.labels -> Mapping[str, str]`,
`scope.name(kind: str) -> str`, `scope.resources(service) -> ResourceSet` (namespaced),
`scope.cluster_resources(service) -> ResourceSet` (cluster-scoped); `RunScopeAction(SkillAction)`: `scope`,
`role` (optional); `OpenRunScope`, a `K8SCallback[RunScopeAction]` composable by any chain.
`OnboardWorkflow`: `prepare()`, `callbacks() -> Callbacks[OnboardState]`, `execute() -> int`, mirroring
`GitLabWorkflow`. Callbacks: `LoadMachineSpec` (local), `ReadMachinePool`, `ReadBootImage`, `OpenRunScope`,
`ResolvePullSecret`, `BuildNodeImage`, `ServeNodeImage`, `WaitForJoiner`, `ApproveJoinRequests`, `ReadNodeState`,
`RemoveServing`, each `K8SCallback[OnboardAction]` except the local specification loader and the scope opener.
Dependencies: the
existing `K8SAuth`, `ResourceSet`, the temporary Job and Secret-copy pattern, the catalogue source and render
tool, the report renderer, the portable receipt writer and the provenance checksum; the release's node-image
tooling inside the build Job; no new third-party dependency.
