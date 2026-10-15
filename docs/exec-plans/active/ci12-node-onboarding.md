# Deliver the node onboarding combo, slice by slice

This ExecPlan is maintained in accordance with the shared standards' execution plan format (`docs/PLANS.md` at
standards revision `cae52b2`). Its Progress, Surprises & Discoveries, Decision Log and Outcomes & Retrospective
sections are kept current as work proceeds. The phase document is
[`docs/phases/CI12-NODE-ONBOARDING.md`](../../phases/CI12-NODE-ONBOARDING.md); this plan turns its two pull
requests into milestones a reader can carry out and observe. All paths are relative to the repository root.

## Purpose / Big Picture

After this work a user who installed ci-skills can ask their agent to onboard a host into their OpenShift cluster
by passing one machine specification and an output directory. The agent runs `build`, the user boots the host
from the served ISO, and the agent runs `join` with the run identifier `build` returned. The person observes an
ISO appear in the output directory with its digest in the result, the host boot from it, and the node read back
Ready with the declared role, with nothing left in the cluster carrying the run identifier. Every step reports its
own plan, apply and read-back, and every verb leaves a sanitized receipt bound to the installed runtime's checksum.

## Progress

- [ ] Milestone 1, code: run scope, specification loader and schema, boot-image and pull-secret reads, ISO build
      and serving, the adapter, the thin main with the `build` verb, result schema, catalogue entry, routing.
- [ ] Milestone 1, gates: unit tests; the result schema listed in the validate metaschema step and in
      `lib/bash/automation/bless.bash`; the checker's required set extended with the `build` operation.
- [ ] Milestone 1, proof: live `build` receipt from the installed candidate with the ISO read back.
- [ ] Milestone 2, code: joiner wait, certificate approval, node state, serving removal, the `join` verb.
- [ ] Milestone 2, gates: unit tests; the checker's required set extended with the `join` operations.
- [ ] Milestone 2, proof: live `join` receipts (applied and no-op) carrying the build's run identifier.
- [ ] Phase identity `CI12` approved by the project owner.
- [ ] The missing `.coordination` authority files created or named by the owner (blocker in the phase document).
- [ ] The standards pin moved to a revision carrying the architecture contract (blocker in the phase document).

## Surprises & Discoveries

None yet. Record here anything the cluster, the release tooling or the install media does differently from the
assumptions in the phase document, with the observation that showed it.

## Decision Log

- 2026-10-15, the owner's direction: every temporary name (namespace, Job, Pod, Service, labels) is derived from
  a random run identifier generated per run and removed in cleanup; the output directory and the node are the
  calling agent's arguments; no environment specific is written into the repository. This entry is the cited
  authority for `R-CI12-004` until the catalogue carries it.
- 2026-10-15, the owner's direction: a combo is proven by one live run from the installed candidate whose evidence
  rows are bound to one functional checksum; mocked runs do not count as proof.
- 2026-10-15: the ISO is built inside the cluster, because a workstation build cannot reach the host's network and
  streams gigabytes over a slow tunnel.
- 2026-10-15: the certificate step approves by planned name and signer and ends on the joiner's issued serving
  request and a Ready node, with a deadline; "nothing pending" is not the stop condition.
- 2026-10-15: the live-ISO route with a hand-built Ignition document is out of scope.
- 2026-10-15, from the first conformance review: the serving resources are owned by the run identifier and live
  from a successful `build` to the end of `join`; `build` returns the identifier and `join` takes it as `--run-id`.
- 2026-10-15, from the same review: the toolbox adapter `WorkflowAction` and `EnsureNamespace` are not reused; an
  OpenShift adapter mirrors the GitLab one and the namespace is a `ResourceSet`-owned document.
- 2026-10-15, from the same review: the callbacks live in the modules CI11 already names as owners
  (`core/ocp_machines.py`, `core/ocp_iso.py`, `core/ocp_csr.py`), so each path has one owner.
- 2026-10-15, from the same review: post-join CI preparation leaves this plan; it has no recorded authority and
  becomes its own proposal when the owner records it.

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
applies only with `--apply --confirm-plan <digest>`; it re-resolves target and credential before writing and reads
the result back; the shared `APPLIED` and `NO_OP` values describe the outcome. The functional checksum over
`ci-skills/bin/`, `ci-skills/lib/` and `ci-skills/schemas/` binds every live receipt to the runtime that produced
it. The contribution guide, `docs/contribution_guide.md`, is the authority for all of this; the GitLab adapter in
`ci-skills/lib/python/core/gitlab/gitlab_action.py` is the pattern the OpenShift adapter copies.

Terms used below: a machine specification is the YAML the agent passes, describing the host's identity, operating
system, cluster membership, network and storage, plus the first NIC's hardware address, the install device, the
hostname and the interface used for the build; a reference specification is the same shape captured from a working
node and passed as the comparison baseline; the boot image is the installer image the cluster publishes for its
own version; a joiner is a node object that appears when a booted host first contacts the cluster; the run
identifier is the random value `build` generates, from which every temporary name derives and which `join`
receives; a receipt is the sanitized JSON the portable writer produces, accepted by the live-acceptance checker only
when its kind, operation and result action are in the checker's required set and its checksum matches the
candidate.

## Plan of Work

Milestone 1 adds `ci-skills/lib/python/core/kubernetes/run_scope.py` (run identifier, deterministic names and
labels, private directory, `ResourceSet` bound to the operation label, reconstruction from an identifier),
`ci-skills/lib/python/core/ocp_machines.py` with `ci-skills/schemas/machine-spec.schema.json` (loader, validation,
reference comparison), `ci-skills/lib/python/core/ocp_iso.py` (boot-image stream read, pull-secret reference,
in-cluster build Job, output-directory copy, read-back, serving Pod and Service, serving removal),
`ci-skills/lib/python/core/ocp_onboard.py` (`OnboardState`, `OnboardAction`, `OnboardWorkflow`), the thin main
`ci-skills/bin/ocp_node_onboard.py` with the `build` verb, `ci-skills/schemas/results/ocp-node-onboard.schema.json`,
the catalogue entry in `ci-skills/tools.json`, the routing in `ci-skills/SKILL.md`, the checker extension in
`tools/check_live_acceptance.py` (required operations and a kubernetes setup context) with its tests in
`tests/python/test_live_acceptance.py`, the unit tests
`tests/python/test_run_scope.py` and `tests/python/test_ocp_node_onboard.py`, the metaschema entry in
`.github/workflows/validate.yml` paired with `lib/bash/automation/bless.bash`, and the receipt
`tests/acceptance/receipts/ocp-node-build-applied.json`. Milestone 2 adds `ci-skills/lib/python/core/ocp_csr.py`
and `ci-skills/lib/python/core/ocp_join.py`, the `join` verb in the adapter and the thin main, the checker
extension for the `join` operations, `tests/python/test_ocp_join.py`, and the receipts
`tests/acceptance/receipts/ocp-node-join-applied.json` and `ocp-node-join-no_op.json`. Each milestone edits only
the files its pull request owns in the phase document's map. The two milestones, each with its goal, work,
result and proof:

### Milestone 1: `build`, from specification to served ISO

Goal: an agent passes one specification and an output directory and gets an ISO on disk, served in the cluster,
with the run identifier to hand on. Work: the modules, schema, adapter, thin main, catalogue, routing, tests and
checker extension listed under Plan of Work. Result: `ocp_node_onboard.py build` exists in the installed catalogue
with `--dry-run`, `--live-plan` and `--apply --confirm-plan`; a successful apply leaves the ISO in the output
directory and the serving Pod, Service and namespace in the cluster, all carrying the run identifier; a failed
apply leaves only the ISO when the copy completed. Proof: the unit tests named under Validation and Acceptance, the
validate workflow green on the exact head, and the live receipt whose rows show the ISO digest and size read back
and whose `run_id` the next milestone consumes.

### Milestone 2: `join`, from booted host to Ready node

Goal: after the user boots the host from the served ISO, the agent passes the run identifier and gets a Ready node
and an empty scope. Work: the joiner wait, the approval loop, the node read-back, the serving removal, the verb,
tests and checker extension. Result: `ocp_node_onboard.py join --run-id <id>` approves exactly the joiner's planned
requests, reads the node Ready, removes the serving resources and the namespace with absence read-back, and is
`NO_OP` when the node is already Ready and nothing is pending. Proof: the approval-loop tests, the validate
workflow green on the exact head, and two live receipts (applied, then no-op on rerun) carrying the build's run
identifier, with the namespace absent afterwards.

## Concrete Steps

`SPEC`, `DIR`, `<digest>`, `<fingerprint>` and `<id>` below stand for the agent's own arguments and for values the
previous step returned; by project rule no example value is written here. Work in a worktree cut from main. For
milestone 1, implement the modules in the order the phase diagram lists
them so each callback's state field exists before its consumer. After each module run `./bless.sh --all` from the
repository root and expect every line to end in `ok`. Run `python ci-skills/bin/ocp_node_onboard.py --describe`
and expect the command contract with the `build` verb and no credentials read. Run `python
ci-skills/bin/ocp_node_onboard.py build --spec SPEC --output-dir DIR --dry-run --json` and expect `DRY_RUN` in
phase `OFFLINE_PLAN` with the plan digest and no API call; run the same with `--live-plan` and expect `PLANNED` in
phase `LIVE_PLAN` with the identity read back. Push and read the validate workflow back green on the exact head.
Install the candidate with `./install.sh --skills-dir ~/.claude/skills --dry-run --json` then `--apply
--confirm-install <fingerprint>`. From the installed copy run `build --apply --confirm-plan <digest> --receipt-out
tests/acceptance/receipts/ocp-node-build-applied.json` against the live cluster and expect `PASS`, an ISO in the
output directory whose digest and size appear in the result, the run identifier in the result, and the serving
URL read back. Run `python tools/check_live_acceptance.py --root . --json` and expect the receipt in the accepted
list with no `receipt_not_declared` problem, which requires the checker extension from the same branch. Commit
the receipt, push, and expect the validate workflow green on that head; that head is the one that merges.

Milestone 2 follows the same sequence with `join --run-id <id>`: the user boots the host between `build` and
`join`; the first live run expects `PASS` with `APPLIED` rows for the approved requests and the node Ready; a
rerun expects `NO_OP` and the namespace absent; both receipts are committed before the checker is run.

## Validation and Acceptance

Milestone 1 is accepted when `tests/python/test_run_scope.py` shows names derive from the identifier, `from_id`
reproduces them, and removal deletes exactly the owned set; when `tests/python/test_ocp_node_onboard.py` shows the
loader accepting a valid specification, rejecting an invalid one, reporting drift against a reference and `NO_OP`
without one, the build callback blocking with a classified reason on timeout, on a failed Job and on a missing
output directory, the serving read-back, and the keep-on-success and remove-on-failure branches of the serving
cleanup; when `tests/python/test_live_acceptance.py` shows the `build` operation accepted when its receipt binds to
the kubernetes context, an unknown OpenShift operation still `receipt_not_declared`, and a receipt without a
matching context `setup_context_missing_or_ambiguous`; when the validate workflow is green on the exact head; and
when the live
receipt shows `PASS` with the ISO digest read back from the output directory. Milestone 2 is accepted when the
approval loop is tested against a simulated two-request sequence (client, then serving after approval), a foreign
signer, and an expired deadline; when the live receipts show the joiner, the approved requests by name and signer,
the node Ready with the declared role, and the namespace absent. A green build alone demonstrates nothing; the
receipts do.

## Idempotence and Recovery

Every `build` generates a new identifier, so repeated builds do not collide; an existing ISO with the same name in
the output directory blocks unless the agent passes `--overwrite`. Resources owned by the work: the run-scoped
namespace, the build Job, the pull-secret file, the serving Pod and Service, and the ISO file. The Job and the
pull-secret file are removed on success, on failure and on interrupt, with absence read-back; the serving and the
namespace are removed on failure and kept on success for `join`; the ISO and the output directory are never
removed. `join` approves only planned names and signers, is `NO_OP` when the node is already Ready, and never
deletes a node. Rollback: `build` changes no persistent cluster state beyond the scope, so rolling it back is
`join`'s removal step or, if `join` never runs, deleting everything carrying the recorded identifier; a certificate
approval cannot be undone, so the approved request names and signers are recorded in the receipt as the audit
trail and the approval loop never widens its selection. If a run leaves a resource behind, the recorded run
identifier selects it for removal; nothing else carries that identifier.

## Artifacts and Notes

Evidence for each milestone is its sanitized receipt under `tests/acceptance/receipts/`, the validate run URL on
the exact head, and the checker's accepted list. Keep the receipt excerpts in the pull request body to the rows
that prove the outcome: ISO digest and size, run identifier, joiner name, approved request names, node condition.

## Interfaces and Dependencies

`OnboardAction(SkillAction)`: `spec_path`, `reference_path` (optional), `output_dir`, `run_id` (required by
`join`), `node` (optional), `build_timeout`, `join_timeout`, `approve_timeout`, `overwrite`, and the shared mode
flags `dry_run`, `live_plan`, `apply`, `confirm_plan`. `OnboardState(DeliveryState)`: `spec`, `reference`,
`run_scope`, `boot_image`, `pull_secret_ref`, `iso`, `served_url`, `joiner`, `approved`, `node`. `RunScope`:
`RunScope.new() -> RunScope`, `RunScope.from_id(run_id: str) -> RunScope`, `scope.run_id`, `scope.namespace`,
`scope.labels -> Mapping[str, str]`, `scope.name(kind: str) -> str`, `scope.private_dir -> Path`,
`scope.resources(service) -> ResourceSet`. `OnboardWorkflow`: `prepare()`, `callbacks() -> Callbacks[OnboardState]`,
`execute() -> int`, mirroring `GitLabWorkflow`. Callbacks: `ReadMachineSpec`, `ReadBootImage`, `ResolvePullSecret`,
`BuildNodeImage`, `ServeNodeImage`, `WaitForJoiner`, `ApproveJoinRequests`, `ReadNodeState`, `RemoveServing`, each
`K8SCallback[OnboardAction]` except the local specification reader. Dependencies: the existing `K8SAuth`,
`ResourceSet`, the temporary Job pattern, the report renderer, the portable receipt writer and the provenance
checksum; the release's node-image tooling inside the build Job; no new third-party dependency.
