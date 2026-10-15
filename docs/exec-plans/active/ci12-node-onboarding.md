# Deliver the node onboarding combo, slice by slice

This ExecPlan is maintained in accordance with the shared standards' execution plan format. Its Progress,
Surprises & Discoveries, Decision Log and Outcomes & Retrospective sections are kept current as work proceeds.
The phase document is [`docs/phases/CI12-NODE-ONBOARDING.md`](../../phases/CI12-NODE-ONBOARDING.md); this plan
turns its three pull requests into milestones a reader can carry out and observe.

## Purpose / Big Picture

After this work a user who installed ci-skills can ask their agent to onboard a host into their OpenShift cluster
by passing one machine specification and an output directory. The agent runs one installed command per slice.
The person observes an ISO appear in the output directory with its digest in the result, the host boot from it,
the node read back Ready with the declared role, and finally the node prepared for CI jobs. Every step reports its
own plan, apply and read-back, and every slice leaves a sanitized receipt bound to the installed runtime's
checksum.

## Progress

- [ ] Milestone 1: run scope, specification loader, boot-image and pull-secret reads, ISO build and serving, the
      `plan` and `build` verbs, unit tests, catalogue and routing; live receipt with the ISO read back.
- [ ] Milestone 2: joiner wait, certificate approval, node state, serving removal, the `join` verb, unit tests;
      live receipt with the node Ready.
- [ ] Milestone 3: CI preparation and its verification, the `prepare` verb, unit tests; live receipt.
- [ ] Phase identity `CI12` approved by the project owner; the missing authority files recorded as a blocker.

## Surprises & Discoveries

None yet. Record here anything the cluster, the release tooling or the install media does differently from the
assumptions in the phase document, with the observation that showed it.

## Decision Log

- 2026-10-15, from the owner's direction: every temporary name is derived from a random run identifier and
  removed in cleanup; the output directory and the node are the calling agent's arguments; no environment
  specific is written into the repository.
- 2026-10-15: the ISO is built inside the cluster, because a workstation build cannot reach the host's network and
  streams gigabytes over a slow tunnel.
- 2026-10-15: the certificate step approves by planned name and signer and ends on the joiner's issued serving
  request and a Ready node, with a deadline; "nothing pending" is not the stop condition.
- 2026-10-15: the live-ISO route with a hand-built Ignition document is out of scope.

## Outcomes & Retrospective

To be written when a milestone completes: compare the observed receipts with the purpose, state the gaps, and
record what the next combo should copy or avoid.

## Context and Orientation

The installed skill lives under `ci-skills/`: commands in `bin/`, the library in `lib/python/core/`, schemas in
`schemas/`. A combo is a thin main that composes atomic callbacks through `WorkflowExecutor(context,
Callbacks([...]))`; each callback subclasses a provider base (`K8SCallback` for Kubernetes) and implements only the
hooks it needs from `collect`, `run`, `update` and `cleanup`; the executor runs the first three in order for each
callback, stops on an incomplete result, and runs cleanup in reverse for every entered callback. State is one
object the executor binds once; callbacks hand values to each other only through its named fields. A mutating
command defaults to an offline plan whose digest the apply must confirm, re-resolves target and credential before
writing, and reads the result back; the shared `APPLIED` and `NO_OP` values describe the outcome. The functional
checksum over `bin/`, `lib/` and `schemas/` binds every live receipt to the runtime that produced it. The
contribution guide, `docs/contribution_guide.md`, is the authority for all of this.

Unfamiliar terms: a machine specification is the YAML the agent passes, describing the host's identity, operating
system, cluster membership, network and storage, plus the first NIC's hardware address, the install device, the
hostname and the interface used for the build; a reference specification is the same shape captured from a working
node and used as the comparison baseline; the boot image is the installer image the cluster publishes for its own
version; a joiner is a node object that appears when a booted host first contacts the cluster.

## Plan of Work

Milestone 1 adds `lib/python/core/kubernetes/run_scope.py` (run identifier, derived names and labels, owned
`ResourceSet`, cleanup with absence read-back), `lib/python/core/ocp/machine_spec.py` with
`schemas/machine_spec.schema.json` (loader, validation, reference comparison), `lib/python/core/ocp/boot_image.py`
(boot-image stream read, pull-secret reference), `lib/python/core/ocp/node_image.py` (in-cluster build Job,
output-directory copy, read-back, serving Pod and Service), the state `OnboardState`, the action `OnboardAction`,
the thin main `bin/ocp_node_onboard.py` with the `plan` and `build` verbs, the catalogue entry, SKILL.md routing,
and `tests/python/test_ocp_node_onboard.py`. Milestone 2 adds `lib/python/core/ocp/join_requests.py` and
`lib/python/core/ocp/join.py`, the `join` verb and `tests/python/test_ocp_join.py`. Milestone 3 adds
`lib/python/core/ocp/prepare_ci.py`, the `prepare` verb and `tests/python/test_ocp_prepare_ci.py`. Each milestone
edits only its owned files plus the thin main, the catalogue and the routing.

## Concrete Steps

Work in a worktree cut from main. For milestone 1, implement the modules in the order the diagram lists them so
each callback's state field exists before its consumer; run `./bless.sh` from the repository root after each
module and expect every line to end in `ok`; run `python ci-skills/bin/ocp_node_onboard.py --describe` and expect
the command contract with the `plan` and `build` verbs and no credentials read; run `python
ci-skills/bin/ocp_node_onboard.py plan --spec SPEC --output-dir DIR --json` and expect `DRY_RUN` with the plan
digest and no API call; push and read the validate workflow back green on the exact head; install the candidate
with `./install.sh --skills-dir ~/.claude/skills --dry-run --json` then `--apply --confirm-install <fingerprint>`;
run `build` from the installed copy against the live cluster with `--receipt-out
tests/acceptance/receipts/ocp-node-build-applied.json` and expect `PASS`, an ISO in the output directory whose
digest and size appear in the result, and a run-scoped namespace that no longer exists afterwards; run `python
tools/check_live_acceptance.py --root . --json` and expect the receipt accepted once the policy and the checker's
operation set list it. Milestones 2 and 3 follow the same sequence with their verbs, with the host booted by the
user between `build` and `join`.

## Validation and Acceptance

Milestone 1 is accepted when the unit tests cover the loader (valid, invalid, drift), the run scope (names derive
from the identifier, cleanup removes exactly the owned set), the build callback (timeout, failed Job, missing
output directory each block with a classified reason) and the serving read-back; when the validate workflow is
green on the exact head; and when the live receipt shows `PASS` with the ISO digest read back from the output
directory and the namespace absent after cleanup. Milestone 2 is accepted when the approval loop is tested against
a simulated two-request sequence (client, then serving after approval) and a foreign signer, and the live receipt
shows the joiner, the approved requests by name and signer, and the node Ready with the declared role. Milestone 3
is accepted when the declared labels and taints read back and the existing read-only views report the node. A
green build alone demonstrates nothing; the receipts do.

## Idempotence and Recovery

Every run generates a new identifier, so repeated runs do not collide. `plan` is read-only. `build` is repeatable:
an existing ISO with the same name in the output directory blocks unless the agent passes the overwrite flag; the
run-scoped namespace, Job, Pod and Service are owned by a `ResourceSet` and removed in reverse order on success,
on failure and on interrupt, with absence read-back; the pull-secret file is removed on exit. `join` approves only
planned names and signers and is `NO_OP` when the node is already Ready; it never deletes a node. `prepare`
compares desired and observed labels and taints before writing and is `NO_OP` when they match. The ISO and the
token sink are never removed by cleanup. If a milestone's live run leaves a resource behind, the recorded run
identifier selects it for manual removal; nothing else carries that identifier.

## Artifacts and Notes

Evidence for each milestone is its sanitized receipt under `tests/acceptance/receipts/`, the validate run URL on
the exact head, and the checker's accepted list. Keep the receipt excerpts in the pull request body to the rows
that prove the outcome: ISO digest and size, joiner name, approved request names, node condition.

## Interfaces and Dependencies

`OnboardAction(SkillAction)`: `spec_path`, `output_dir`, `node`, `build_timeout`, `join_timeout`,
`approve_timeout`, `probe_image_digest`, `apply`, `confirm_plan`, `overwrite`. `OnboardState(DeliveryState)`:
the fields named in the phase diagram. `RunScope`: `RunScope.new() -> RunScope`, `scope.name(kind) -> str`,
`scope.labels -> Mapping[str, str]`, `scope.resources -> ResourceSet`. Callbacks: `ReadMachineSpec`,
`ReadBootImage`, `ResolvePullSecret`, `BuildNodeImage`, `ServeNodeImage`, `WaitForJoiner`, `ApproveJoinRequests`,
`ReadNodeState`, `RemoveServing`, `PrepareForCI`, `VerifyPrepared`, each `K8SCallback[OnboardAction]` except the
local specification reader. Dependencies: the existing `K8SAuth`, `EnsureNamespace`, `ResourceSet`, the temporary
Job pattern, the report renderer, the portable receipt writer and the provenance checksum; the release's node-image
tooling inside the build Job; no new third-party dependency.
