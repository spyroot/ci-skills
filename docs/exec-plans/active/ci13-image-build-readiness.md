# Prove Podman readiness and deliver a toolbox-backed GitLab runner

This ExecPlan is maintained in accordance with the shared standards' execution plan format (`docs/PLANS.md` at
standards revision `cae52b2`). Its Progress, Surprises & Discoveries, Decision Log and Outcomes & Retrospective
sections are kept current as work proceeds. The phase document is
[`docs/phases/CI13-IMAGE-BUILD-READINESS.md`](../../phases/CI13-IMAGE-BUILD-READINESS.md); this plan turns its two
pull requests into milestones a reader can carry out and observe. All paths are relative to the repository root.
The command surface and gate wiring remain open questions in the phase document. The operator resolved the
privileged-constraint behavior: the installed command must establish its own run-scoped prerequisite, so an
external cluster grant cannot make an incomplete capability pass.

## Purpose / Big Picture

After this work a user who installed ci-skills can ask their agent whether their cluster can build container
images from a GitLab job. The agent runs one probe on a node, and the person observes one row per thing the probe
proved there (kernel, podman version, storage driver and cgroup version, backing filesystem, an inner container
that ran, a bind mount read back, a failing exit code that propagated, nothing left behind), the Pod's exit code as
the verdict, and nothing left in the cluster afterwards; when the cluster refuses privileged Pods, the person reads
the exact missing authorization instead of a failed pipeline. The separate chain builds and publishes its runner
toolbox and configures/starts a runner using it. Online/runtime proof and a representative consumer job are
external live acceptance.

## Progress

- [x] Milestone 1, code: the shared mode validator, reuse of CI12.0's run scope, node selection, the probe Job and
      its script, the result and log read-back, the marker parser, the adapter, the thin main with the `probe`
      verb, the catalogue declaration with regenerated `tools.json`, result schema, routing.
- [x] Milestone 1, gates: unit tests; the new schema listed in the validate metaschema step per the existing
      pairing rule (the local selector is open question 3).
- [x] Standalone probe and owned SCC cleanup passed in [PR81](https://github.com/spyroot/ci-skills/pull/81).
- [ ] Milestone 1, proof: live `probe` from the installed candidate on one node with every row present and the
      Pod exited 0, plus an interrupted run with the same absence read-back, attached to the pull request. The
      command must reuse or prepare and clean its own SCC permission; an external grant is not accepted as proof.
- [x] CI13-R1: Milestone 2 code is present at `ff1fdc8`: the `chain` verb composes toolbox build, Harbor
      publication and runner configuration through `core/kubernetes/image_build_chain/workflow.py`.
- [ ] Milestone 2, proof: existing chain build/publication/deployment results and external consumer proof.
- [x] Open question 1 (command surface and default digests) answered by the owner.
- [x] Open question 2 (gate wiring for this command's receipts) answered by the owner.
- [x] Open question 4 answered: reuse existing permission or create, verify and clean one run-owned RoleBinding.
- [x] Phase identity `CI13` approved; CI12.0 owns the shared foundation (see the phase's Authority and PR Order).
      The selected architecture format adds no gate outside the pinned binding.

## Surprises & Discoveries

CI13-R1: the phase's unmerged chain label predates the implementation now present in
`ci-skills/lib/python/core/kubernetes/image_build_chain/workflow.py`, its command/model/schema and
`tests/python/test_image_build_chain.py`. The current workflow ends at `DeployManagerRunner`. Older combined-chain
receipts include superseded probe/consumer callbacks and do not prescribe the current composition.
The [probe evidence](../../../tests/acceptance/evidence/podman-readiness-final/README.md) explicitly records that
its interruption attempt sent no signal, so interruption proof remains open.

## Decision Log

- DEC-CI13-R1: record the chain code as delivered at `ff1fdc8` while retaining current-chain and interruption
  acceptance as open. Preserve the original external-consumer boundary and the unchanged phase requirements.

- 2026-10-15, the owner's direction: "we need to see can we do podman", for a caller who needs to run docker in
  Kubernetes through GitLab; a useful combo. Recorded as chain 11 of the catalogue.
- 2026-10-15, the owner's direction: every temporary name (namespace, Job, labels) is derived from a random run
  identifier generated per run and removed in cleanup; the evidence directory is the calling agent's argument; no
  environment specific is written into the repository. This entry is the cited authority for `R-CI13-005` until
  the catalogue carries it.
- 2026-10-15, the owner's direction: a combo is proven by one live run from the installed candidate whose evidence
  rows are bound to one functional checksum; mocked runs do not count as proof; only the live-tested head merges.
- 2026-10-15, the owner's direction: requirements, the user-facing command surface, install or usage steps and
  gate behaviour are the owner's to approve; a review finding on any of them becomes an open question in the
  phase document, not a decision. The spec shows how the agent invokes the command and the JSON it reads.
- 2026-10-15, the owner's direction: this combo is specified as its own phase, CI13, separate from CI12.
- 2026-10-18, the owner's direction: live proof must use only the installed command; external OpenShift setup is
  diagnostic evidence and cannot make an incomplete capability pass. The command reuses existing SCC permission
  or creates, verifies and cleans one run-owned RoleBinding to the exact SCC ClusterRole.
- 2026-10-19, the owner's correction: the production combo is build toolbox, push it to Harbor and configure a
  runner using it. The runner remains available after success. Probe and consumer-job execution are external
  acceptance; the combo ends at the existing runner deployment without a separate verification stage, pipeline
  execution, required job results or automatic runner teardown.
- 2026-10-15, mechanism: the Pod's terminated exit code read from the API is the probe's result; every marker line
  is a row and a missing marker is a row; a tag without a digest is refused; node selection without an argument
  is the first Ready worker by name order, named in the result.
- 2026-10-15, mechanism, from the CI12 reviews: the run identifier is generated after confirmation and is not part
  of the plan digest, which binds the caller's inputs and the resolved target; the live plan runs the reads only,
  with no confirm callback; the mode rules are one shared validator in `core/action_modes.py` rather than a fourth
  copy, and the GitLab adapter switches to it in the same pull request.

## Outcomes & Retrospective

PR81 merged the standalone probe with [live proof](../../phases/CI13-IMAGE-BUILD-READINESS.md#live-smoke-evidence).
Interruption proof remains unestablished. Milestone 2's toolbox-backed runner chain is implemented at `ff1fdc8`;
current-chain/external-consumer acceptance remains open. Historical standalone runner-manager proof explicitly
does not establish the combined CI13 command. The plan remains active.

## Context and Orientation

The installed skill lives under `ci-skills/`: commands in `ci-skills/bin/`, the library in
`ci-skills/lib/python/core/`, schemas in `ci-skills/schemas/`. A combo is a thin main that composes atomic
callbacks through `WorkflowExecutor(context, Callbacks([...]))`; each callback subclasses a provider base
(`K8SCallback` for Kubernetes) and implements only the hooks it needs from `collect`, `run`, `update` and
`cleanup`; the executor runs the first three in order for each callback, stops on an incomplete result, and runs
cleanup in reverse for every entered callback on success and on failure, inside a recoverable-signal guard so an
interrupt still reaches cleanup. State is one object the executor binds once; callbacks hand values to each other
only through its named fields. A mutating verb returns an offline plan with `--dry-run`, a live plan with
`--live-plan`, and applies only with `--apply --confirm-plan <digest>`, where the digest is computed from the
caller's inputs and the resolved target so the apply reproduces it; it re-verifies the target before writing and
reads the result back. The command is declared in `ci-skills/lib/python/core/catalog.py`, from which
`ci-skills/tools.json`, `--help` and `--describe` are rendered; the validate workflow fails on a stale
`tools.json`. The functional checksum over `ci-skills/bin/`, `ci-skills/lib/` and `ci-skills/schemas/` binds every
live receipt to the runtime that produced it. The contribution guide, `docs/contribution_guide.md`, is the
authority for all of this; the GitLab adapter in `ci-skills/lib/python/core/gitlab/gitlab_action.py` is the
pattern the Kubernetes adapter mirrors, and the temporary Job of `PullImage` in
`ci-skills/lib/python/core/kubernetes/runner.py` is the pattern the probe Job follows.

Terms used below: the probe is the one-shot privileged Job that runs podman on the selected node; the probe image
is the podman image it runs, pinned by digest; the inner image is the small image the probe runs inside podman,
pinned by digest; a marker line is one line the probe script prints per step, which the combo parses into a row; the
run identifier is the random value the apply generates after confirmation, from which the namespace, the Job name
and the labels derive; the envelope is the shared JSON result every installed command prints, declared by the
paired result schema; a receipt is the sanitized copy of it the portable writer produces with `--receipt-out`; the
evidence directory is the optional place the agent names for the Job log and the rows to persist; the installed
candidate is the skill installed from the pull request head as the README's live smoke setup describes; the
constraint is the OpenShift `privileged` security context constraint. The installed command reads whether the
run-scoped service account can use it and creates a temporary RoleBinding only when that permission is absent.

## Plan of Work

CI13-R1 reconciliation: the implementation scope below is delivered code, retained to explain the original
milestones. The unchecked proof items in Progress are the remaining work; do not implement these callbacks again.

Milestone 1 adds `ci-skills/lib/python/core/action_modes.py` (the shared mode validator with its per-adapter
read and receipt policy, consumed by `ci-skills/lib/python/core/gitlab/gitlab_action.py` in the same change),
then consumes `ci-skills/lib/python/core/kubernetes/run_scope.py` delivered by CI12.0 and adds
`ci-skills/lib/python/core/kubernetes/image_build_probe.py` (`SelectNode`, `RunProbeJob` with
the probe script, `ReadProbeResult`, `ParseMarkers`), `ci-skills/lib/python/core/image_build_action.py`
(`ReadinessState`, `ReadinessAction`, `ConfirmReadinessOperation`, `ReadinessWorkflow`), the thin main
`ci-skills/bin/image_build_readiness.py` with the `probe` verb, the declaration in
`ci-skills/lib/python/core/catalog.py` with `ci-skills/tools.json` regenerated,
`ci-skills/schemas/results/image-build-readiness.schema.json`, the routing in `ci-skills/SKILL.md`, the unit tests
`tests/python/test_action_modes.py`, `tests/python/test_run_scope.py` and `tests/python/test_image_build_probe.py`,
and the metaschema entry in `.github/workflows/validate.yml` paired in `lib/bash/automation/bless.bash` as open
question 3 allows. Milestone 2 adds `ci-skills/lib/python/core/kubernetes/image_build_chain/`, the `chain` verb
in the adapter, the thin main and the catalogue declaration, the chain's rows in the result schema, and
`tests/python/test_image_build_chain.py`, reusing the toolbox, Harbor and runner manager callbacks. Each milestone
edits only the files its pull request owns in the phase document's map; any receipt committed into the gate follows the
owner's answer to open question 2 and is not part of either milestone until then. The two milestones, each with
its goal, work, result and proof:

### Milestone 1: `probe`, one node proven able to run podman builds

Goal: an agent names a node, or lets the combo pick one, and reads back every row the probe proved there with the
Pod's exit code as the verdict, or the exact missing authorization, with nothing left in the cluster. Work: the
modules, schema, adapter, thin main, catalogue declaration, routing and tests listed under Plan of Work, then the
live run. Result: `image_build_readiness.py probe` exists in the installed catalogue with `--dry-run`,
`--live-plan` and `--apply --confirm-plan`, invoked and read as the phase document's "Invocation and the result
the agent reads" shows; a successful apply returns `PASS` with eight rows and the Pod's exit code 0 and leaves no
namespace and no Job; existing SCC permission is a no-op, missing permission creates and cleans one run-owned
RoleBinding, and a denied authorization preflight blocks before Job creation; an interrupted apply leaves nothing.
Proof: the unit tests named under Validation and Acceptance, the
validate workflow green on the exact head, and the live `probe` result on one node of the live cluster with every
row present and the Pod exited 0, plus a second run interrupted during the Job with the same absence read-back,
both attached to the pull request as receipts. A blocked authorization result does not stand in for execution.
Reuse proof when the functional checksum is unchanged.

### Milestone 2: `chain`, build and publish a toolbox and configure its runner

Goal: the agent supplies the toolbox specification and runner scope and deploys a runner using the
published toolbox on the selected worker. Work: compose the existing toolbox, Harbor and runner manager
callbacks, the verb, result-schema rows and tests, then installed evidence. Result:
`image_build_readiness.py chain --node <NODE>` builds the toolbox, publishes it to Harbor and configures/starts
the runner, ending at the existing `DeployManagerRunner` and returning the existing operation results.
The runner, manager, required SCC binding and runtime namespace remain available after success; removal is an
explicit lifecycle action.
Proof: the composition tests, the validate workflow green on the exact head, and the installed chain result.
Online/runtime proof, consumer-job execution and disposal of test resources belong to external acceptance through
installed-skill invocations by the consumer agent, not the product combo.

## Concrete Steps

`<NODE>`, `<digest>`, `<RECEIPT>` and `<EVIDENCE>` below stand for the agent's own arguments and for values the
previous step returned; by project rule no example value is written here. Work in a worktree cut from main.
The shared validator, adapters and chain modules already exist. Run the current static and focused checks through
the existing validation route, including `python tools/render_manifest.py --check`, and expect exit 0. Then use
the installed candidate for the outstanding proof rather than recreating the implementation.
Run `python ci-skills/bin/image_build_readiness.py --describe` and expect the command contract with the
`probe` verb and no credentials read. Run `python ci-skills/bin/image_build_readiness.py probe --node <NODE>
--dry-run --json` and expect `DRY_RUN` in phase `OFFLINE_PLAN` with the plan digest, both image digests, the name
pattern and no API call; run the same with `--live-plan` and expect `PLANNED` in phase `LIVE_PLAN` with the
identity and the node read back and the same digest. Push and read the validate workflow back green on the exact
head. Install the candidate as the README's live smoke setup describes and run the remaining steps from the
installed copy against the live cluster: `probe --node <NODE> --apply --confirm-plan <digest> --json
--receipt-out <RECEIPT> --evidence-dir <EVIDENCE>` and expect `PASS` with eight rows each `ok`, `exit_code` 0,
the run identifier under `readback`, `namespace_absent` and `job_absent` true, and the log and the rows under
`<EVIDENCE>`. Expect the permission callback to report `existing` or `temporary_role_binding`; for a temporary
binding, expect exact UID read-back and absence during cleanup. If permission preparation is denied, expect
`BLOCKED` before Job creation and record the exact native authorization result under Surprises & Discoveries;
that result is diagnostic, not capability proof. Then run the apply again and interrupt it while
the Job runs; expect `BLOCKED` with the interrupt reason and `namespace_absent` and `job_absent` true. Attach the
results to the pull request; push the head they came from and expect the validate workflow green on it; that
head is the one that merges. The report of every live step is its receipt, produced the same way as every other
live run in this repository: `--receipt-out` on the installed command, sanitized by the portable writer, bound to
the functional checksum, with the rows that prove the outcome quoted in the pull request body.

Milestone 2 uses the [chain reference](../../../ci-skills/references/image-build-chain.md) for its required
toolbox and runner inputs, live plan and confirmed apply. It returns the existing build, publication and deployment
results; no separate verification stage, pipeline or consumer job is executed by the combo.

## Validation and Acceptance

Milestone 1 is accepted when `tests/python/test_action_modes.py` shows the shared validator reproducing every rule
the three copies enforce today (apply against dry-run, live-plan, `--receipt-out` needing a live run) with the
GitLab adapter's own cases still passing; when `tests/python/test_run_scope.py` shows names derive from the
identifier and removal deletes exactly the owned set; when `tests/python/test_image_build_probe.py` shows node
selection (a named node, the first Ready worker by name order, no Ready worker blocking), the Job document
(privileged, pinned node, no token, both emptyDirs with limits, the bounds, deadline, TTL, both images as digests,
a tag refused before any plan), a dry-run digest confirming a later apply with the same inputs and target and
rejecting another target, the parser against fixture logs (all markers, one missing marker as a row, exit
propagation 7 and not 7, remaining containers non-empty), the verdict (`PASS` only with exit 0 and every row
`ok`, `PARTIAL` otherwise), existing SCC permission as a preserved no-op, temporary RoleBinding read-back and
cleanup, and authorization denial before Job creation; when the catalogue consistency test and the render check
pass; when the validate workflow is green on the exact head; and when the live results attached to the pull
request show the rows with the Pod's exit code and nothing left carrying the identifier, including after the
interrupted run. Milestone 2 is accepted when the composition tests pass, the existing build/publication/deployment
results are captured, and external consumer proof shows that the retained runner uses the published toolbox.
Online/runtime proof, consumer-job execution and test-resource disposal are external acceptance steps.

## Idempotence and Recovery

Every apply generates a new identifier, so repeated probes do not collide and two probes can run on two nodes at
once. Resources owned by the work: the run-scoped namespace, optional run-owned SCC RoleBinding, read-only script
ConfigMap and Job, with its Pod and emptyDirs. The confirmed plan binds the script bytes. These resources are
removed on success, on failure and on interrupt by their creators' cleanup hooks, with absence read-back; the
evidence directory, when named, is never removed. The probe changes nothing on the node: podman's storage lives on
the Pod's emptyDir and leaves with the Pod, and the two inner containers are removed by the script before it
exits. Recovery: if a cleanup hook fails, the result is `BLOCKED` with the run identifier and the objects still
carry the operation label, so deleting by that label is the manual recovery; nothing else carries the identifier.
Rollback: the probe creates no persistent cluster state, so there is nothing to roll back. Preexisting SCC
permission is never touched; only the RoleBinding created by this run is removed.

## Artifacts and Notes

Evidence for each milestone is the live results attached to the pull request (sanitized receipts written with
`--receipt-out`), the Job log and the rows under the evidence directory when the agent named one, the validate run
URL on the exact head, and, once open question 2 is answered, whatever the owner decides is committed into the
gate. The pull request body carries the probe's rows in the six-row style, one row per marker, each pointing at
its receipt and all linked by the run identifier and the functional checksum, plus the absence read-back row and
the interrupted run's row; the pull request lists the two default image digests for approval under open
question 1. Keep the excerpts to the rows that prove the outcome: the chosen node, the eight rows, the Pod's exit
code, the run identifier, the absence read-back.

## Interfaces and Dependencies

`ReadinessAction(SkillAction)`: `node` (optional), `probe_image`, `inner_image` (digests), `timeout`,
`evidence_dir` (optional), the resource bounds (`cpu`, `memory`, `storage`, with defaults), and the shared mode
flags `dry_run`, `live_plan`, `apply`, `confirm_plan`. `ReadinessState(DeliveryState)`: `node`, `run_scope`,
`job`, `log`, `exit_code`, `rows`, `verdict`. CI12.0 owns `RunScope` and `OpenRunScope`; their interface is
recorded in [CI12's ExecPlan](../completed/ci12-node-onboarding.md#interfaces-and-dependencies).
The shared validator: `resolve_mode(args, policy) -> Mode` in `core/action_modes.py`, where `policy` names the
adapter's read operations and its receipt rule, returning one of `describe`, `offline`, `live-plan`, `read`,
`apply`, and raising the shared reasons. `ReadinessWorkflow`: `prepare()`, `callbacks() ->
Callbacks[ReadinessState]`, `execute() -> int`, mirroring `GitLabWorkflow`; `ConfirmReadinessOperation`, mirroring
`ConfirmGitLabOperation`. Callbacks: `SelectNode`, `OpenRunScope`, `RunProbeJob`, `ReadProbeResult`,
`ParseMarkers` (local), each `K8SCallback[ReadinessAction]` except the scope callback and the local parser.
Dependencies: the existing `K8SAuth`, `ResourceSet`, the temporary Job pattern, the service's `command`, `json`,
`apply`, `delete` and `wait_job` calls, the report renderer, the portable receipt writer and the provenance
checksum; the podman image and the inner image by digest; no new third-party dependency on the agent's machine.

Revision DEC-CI13-R1: reconciled chain source delivery against `ff1fdc8`, retained original proof requirements
and removed obsolete instructions to implement already-delivered modules. Only moved-document paths in the phase are repaired.
