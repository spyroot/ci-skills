# NOT_IMPLEMENTED: CI13 — Prove a cluster can build images from a GitLab job, starting with a podman probe on one node

- grounding: GROUNDED
- verification: UNVERIFIED
- planningStage: PROPOSED

## Authority

CI13 is the approved Podman/readiness delivery, separate from the native OCP delivery in
[CI12](CI12-NODE-ONBOARDING.md). CI12.0 owns the reusable tool preparation and run scope; this phase consumes
that foundation. Specification approval does not claim implementation or successful live verification.
This document plans from:

- the owner's direction of 2026-10-15, recorded in [`docs/combos.md`](../combos.md) chain 11 ("Can this cluster
  build images from a GitLab job?"): a user who installed ci-skills asks their agent to prepare CI so the project's
  container tests, docker builds included, run in GitLab jobs on their cluster; the agent proves in one go that
  podman can run containers on a node there, then that a real job can build and push an image;
- the owner's directions of 2026-10-15 on run-scoped names and agent-chosen locations, and on the probe's result
  being the Pod's terminated exit code with every marker line a row, recorded with their dates in the
  [ExecPlan's Decision Log](../exec-plans/active/ci13-image-build-readiness.md#decision-log); the catalogue does
  not yet carry the first, so the Decision Log is the cited authority for `R-CI13-005`;
- the project motivation in [`README.md`](../../README.md#motivation): one combo instead of many hand-rolled
  calls, less context, the same result in one go;
- the six-row evidence model in [`docs/gitlab-toolbelt-plan.md`](../gitlab-toolbelt-plan.md) block 7, which
  defines how a chain is proven and names the runner registration and test job that chain 11's later steps build
  on, and the owner's rule that only a live-tested head merges;
- the [contribution guide](../contribution_guide.md), sections 2 (one atomic callback), 3 (compose a thin
  workflow; for a domain other than the toolbox, reuse that domain's action adapter) and 4 (declare the command in
  `core/catalog.py`, pair a result schema, regenerate `tools.json`), which every step below is written against;
- the shared command contract every installed command exposes through `--describe`: the shared options
  (`--binding`, `--json`, `--yaml`, `--human`, `--dry-run`, `--revision`, `--log-*`, `--run-id`, `--output-dir`),
  the status values and the exit codes, as `ci-skills/lib/python/core/catalog.py` declares them, and the MTU
  command's precedent of a confirmed plan that creates temporary privileged Pods and verifies their cleanup;
- the merged [`CI12-NODE-ONBOARDING`](CI12-NODE-ONBOARDING.md#phase-ci120--reusable-tool-pod-pr-ci12-0)
  delivery specification, whose CI12.0 owns the shared run scope and reusable tool preparation;
- the live-acceptance gate as it exists: the policy
  [`inventory/ci/live-acceptance.json`](../../inventory/ci/live-acceptance.json) and the checker
  [`tools/check_live_acceptance.py`](../../tools/check_live_acceptance.py); how this command's evidence enters that
  gate is an open question for the owner, recorded below, not a decision of this document;
- the shared standards pinned by `standards-binding.yaml` at revision `56a579c`. The architecture and plan formats
  this document follows are taken from standards revision `cae52b2`, the `main` the owner named on 2026-10-15; the
  selected format does not add a validation gate absent from the pinned binding.

<!-- architecture-map:v1 -->
    requirements:
      - id: R-CI13-001
        authority: [docs/combos.md, README.md]
        phaseId: CI13
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: The node is the agent's argument or, without one, a Ready worker the combo picks and names in the result; the plan lists the Job the apply would create (node, images by digest, limits, the name pattern) and its digest binds the caller's inputs and the resolved target.
        owner: ci-skills/lib/python/core/kubernetes/image_build_probe.py
        preserve: [The target and credential resolver, the result envelope, the exit-code contract]
        prIds: [PR-CI13-1]
        gateIds: [G-CI13-STATIC, G-CI13-LIVE]
        evidence: []
      - id: R-CI13-002
        authority: [docs/combos.md]
        phaseId: CI13
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: One-shot privileged Job pinned to the selected node runs podman from an image pinned by digest with overlay storage on a size-limited emptyDir and the cgroupfs manager, no service-account token mounted, bounded requests, limits, deadline and TTL; inside it one inner container pinned by digest runs, writes a file read back through a bind mount, and a second inner container exits 7 so the exit code is seen to propagate; the probe removes both and lists what remains.
        owner: ci-skills/lib/python/core/kubernetes/image_build_probe.py
        preserve: [The temporary Job pattern of PullImage; a tag without a digest is refused]
        prIds: [PR-CI13-1]
        gateIds: [G-CI13-STATIC, G-CI13-LIVE]
        evidence: []
      - id: R-CI13-003
        authority: [docs/combos.md, docs/exec-plans/active/ci13-image-build-readiness.md]
        phaseId: CI13
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: Every marker line of the Job log becomes an evidence row (host kernel, podman version, storage driver and cgroup version, backing filesystem, inner run, bind-mount read-back, exit propagation, remaining containers); a missing marker is a row of its own, never a silent gap; the probe's own result is the Pod's terminated exit code read from the API, not a line it printed.
        owner: ci-skills/lib/python/core/kubernetes/image_build_probe.py
        preserve: [Rows are read from the log and the Pod status through the bound Kubernetes service, never from a shell the agent runs]
        prIds: [PR-CI13-1]
        gateIds: [G-CI13-STATIC, G-CI13-LIVE]
        evidence: []
      - id: R-CI13-004
        authority: [docs/combos.md]
        phaseId: CI13
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: On OpenShift, the installed command reads whether the run namespace's service account can use the privileged constraint before creating the Job; existing permission is a verified no-op, otherwise the command creates one run-owned namespaced RoleBinding to the exact OpenShift SCC ClusterRole, reads permission back, and removes only that binding during cleanup. A missing selected SCC or generated ClusterRole, or a denied create, delete or bind preflight, blocks before Job creation. Only absence of the OpenShift SCC API is not applicable.
        owner: ci-skills/lib/python/core/kubernetes/scc_access.py
        preserve: [Never alter or remove preexisting authorization; create no ServiceAccount; grant no cluster-wide or alternate runtime permission]
        prIds: [PR-CI13-1]
        gateIds: [G-CI13-STATIC, G-CI13-LIVE]
        evidence: []
      - id: R-CI13-005
        authority: [docs/exec-plans/active/ci13-image-build-readiness.md, docs/phases/CI12-NODE-ONBOARDING.md]
        phaseId: CI13
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: Every temporary name (namespace, ConfigMap, Job, labels) derives from one random run identifier generated after confirmation; the namespace, script ConfigMap and Job are removed on every exit, success, failure and interrupt, with absence read-back; when the agent names an evidence directory the Job log and the parsed rows persist there, otherwise nothing persists beyond the result.
        owner: ci-skills/lib/python/core/kubernetes/run_scope.py
        preserve: [ResourceSet ownership semantics and its operation label; the executor's reverse cleanup and recoverable signals; the shared --run-id option's existing meaning]
        prIds: [PR-CI13-1]
        gateIds: [G-CI13-STATIC, G-CI13-LIVE]
        evidence: []
      - id: R-CI13-006
        authority: [docs/combos.md, README.md, docs/contribution_guide.md]
        phaseId: CI13
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: One thin main composes the callbacks through a Kubernetes action adapter over WorkflowExecutor, declares the command in the catalogue source with a paired result schema and regenerates tools.json, routes in SKILL.md, and renders the shared report, so the agent invokes it like every other installed command and reads the same envelope.
        owner: ci-skills/bin/image_build_readiness.py
        preserve: [The shared options, status values and exit codes; --describe and --help work without credentials; the existing gate's required operations and freshness policy]
        prIds: [PR-CI13-1, PR-CI13-2]
        gateIds: [G-CI13-STATIC, G-CI13-LIVE, G-CI13-GATE]
        evidence: []
      - id: R-CI13-007
        authority: [docs/combos.md, docs/gitlab-toolbelt-plan.md]
        phaseId: CI13
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: After a passing probe, the chain continues on the same node with a toolbox-backed runner carrying the same privileged podman setup, a job on the test project that builds an image with podman and pushes it to Harbor, and the read-back of the pushed digest and of the job naming the runner, composed from chain 1 and block 7 once they exist.
        owner: ci-skills/lib/python/core/kubernetes/image_build_chain.py
        preserve: [Chain 1's runner manager and harbor_push read-back are composed, not reimplemented]
        prIds: [PR-CI13-2]
        gateIds: [G-CI13-STATIC, G-CI13-LIVE]
        evidence: []
    pullRequests:
      - id: PR-CI13-1
        requirementIds: [R-CI13-001, R-CI13-002, R-CI13-003, R-CI13-004, R-CI13-005, R-CI13-006]
        dependsOn: [PR-CI12-0]
        waveId: W-CI13-1
        owns: [ci-skills/lib/python/core/kubernetes/scc_access.py, ci-skills/lib/python/core/kubernetes/image_build_probe.py, ci-skills/lib/python/core/image_build_action.py, ci-skills/lib/python/core/action_modes.py, ci-skills/lib/python/core/gitlab/gitlab_action.py, ci-skills/lib/python/core/catalog.py, ci-skills/bin/image_build_readiness.py, ci-skills/schemas/results/image-build-readiness.schema.json, ci-skills/tools.json, ci-skills/SKILL.md, tests/python/test_run_scope.py, tests/python/test_action_modes.py, tests/python/test_image_build_probe.py, .github/workflows/validate.yml, lib/bash/automation/bless.bash]
      - id: PR-CI13-2
        requirementIds: [R-CI13-006, R-CI13-007]
        dependsOn: [PR-CI13-1]
        waveId: W-CI13-2
        owns: [ci-skills/lib/python/core/kubernetes/image_build_chain.py, ci-skills/lib/python/core/image_build_action.py, ci-skills/lib/python/core/catalog.py, ci-skills/bin/image_build_readiness.py, ci-skills/schemas/results/image-build-readiness.schema.json, ci-skills/tools.json, ci-skills/SKILL.md, tests/python/test_image_build_chain.py]
    waves:
      - id: W-CI13-1
        prIds: [PR-CI13-1]
        after: [W-CI12-0]
        gateIds: [G-CI13-STATIC, G-CI13-LIVE, G-CI13-GATE]
      - id: W-CI13-2
        prIds: [PR-CI13-2]
        after: [W-CI13-1]
        gateIds: [G-CI13-STATIC, G-CI13-LIVE, G-CI13-GATE]
    gates:
      - id: G-CI13-STATIC
        requirementIds: [R-CI13-001, R-CI13-002, R-CI13-003, R-CI13-004, R-CI13-005, R-CI13-006, R-CI13-007]
        command: ./bless.sh --all locally; the validate workflow on the pull request head (static gate, docstrings, manifests and schemas including the catalogue render check, unit tests, installer fixtures, endpoint gate, aggregated required result)
        environment: GitHub Actions on the pull request head; ruleset "protected" (id 25130553) on main requires the ci-skills/required status with the strict up-to-date policy
        enforcement: Existing
        evidence: [Exact head SHA, validate run URL, every job green, ci-skills/required SUCCESS, the new result schema listed in the metaschema step per the existing pairing rule, tools.json regenerated from the catalogue source]
      - id: G-CI13-LIVE
        requirementIds: [R-CI13-001, R-CI13-002, R-CI13-003, R-CI13-004, R-CI13-005, R-CI13-006, R-CI13-007]
        command: The invocations under "Invocation and the result the agent reads", run from the installed candidate against the live cluster, each with --receipt-out
        environment: The user's live smoke setup against the live cluster; the installed command must reuse or prepare its own run-scoped privileged permission
        enforcement: Proposed; the live run precedes the merge and its receipts are attached to the pull request; whether and how those receipts are committed into the gate is open question 2, because the checker today accepts only its declared GitLab kinds and rejects any other receipt in a declared directory
        evidence: [One receipt per verb and result action, the permission source and optional RoleBinding UID, the marker rows with the Pod's terminated exit code, the chosen node, the run identifier, absence read-back of every run-owned resource, the functional checksum of the installed candidate]
      - id: G-CI13-GATE
        requirementIds: [R-CI13-006]
        command: python tools/check_live_acceptance.py --root . --json
        environment: The live_acceptance step of the validate job, on the committed receipts
        enforcement: Existing for the current operation set and unchanged by this phase until the owner answers open question 2; until then no receipt of this command is committed under a declared receipt directory
        evidence: [Checker PASS on the pull request head with the existing receipt set unchanged, or, after the owner's answer, with the new receipts accepted]
<!-- /architecture-map:v1 -->

## Consumer

The consumer is an agent, a Codex or Claude session, acting for a user who installed ci-skills and asked for help
preparing CI so their project's container tests and image builds run in GitLab jobs on their cluster. The agent
calls the installed command with a node, or lets the combo pick one, and reads the shared result envelope: one
row per thing the probe proved on that node, the probe's exit code from the Pod, and either `PASS` or a named
authorization or runtime failure. The exact invocations and the JSON the agent reads are under "Invocation and the
result the agent reads". The downstream consumer is chain 11's later steps: a runner with the same setup on that
node, a job that builds and pushes, and the digest read-back.

The motivations, in the contract's categories: the capability it adds (an answer to "can this cluster build
images from a job?" in one command, where today the agent would discover each failure inside a pipeline, one
retry at a time); the necessity it addresses (container builds in CI need a privileged podman that works on the
node, and the four things that break it, storage driver, cgroup manager, bind mounts and exit propagation, are
exactly what the probe checks); and the limitation in established code it resolves (the skill runs temporary
Jobs and privileged debug Pods but nothing runs podman or reads a Job's log into rows). Each traces to chain 11
of the catalogue and the README motivation.

## Motivation

A user wants their project's docker tests to run in GitLab jobs on their own cluster. Today they register a
runner, push a pipeline, watch the build job fail on overlay storage, fix it, watch it fail on cgroups, fix it,
watch a bind mount read back empty, and only then learn that the exit code of a failing container never reached
the job. Each round trip costs a pipeline and the agent's context. After this phase the agent runs one probe on
the chosen node and gets every one of those answers as a row, with the Pod's exit code as the verdict, and a
named reason when the cluster refuses privileged Pods. The observable outcome for the user: a node proven able to
run podman builds before any runner or pipeline is touched, and later, the same chain carried through a real job
and a pushed digest.

## Diagram

    agent ─ image_build_readiness.py probe [--node N] ──► ReadinessWorkflow (prepare → callbacks → execute)
                                                              │ mode: --dry-run | --live-plan | --apply --confirm-plan
              ReadinessState(DeliveryState), bound once; prepare() compares the digest, then state.run_scope = RunScope.new()
                                                              │
      K8SAuth ─► ConfirmReadinessOperation ─► SelectNode ─► OpenRunScope ─► RunProbeJob ─► ReadProbeResult ─► ParseMarkers
                     │ target re-verified       │ state.node    │ namespace      │ Job; waits   │ state.log,       │ state.rows,
                                                                                 │ to completion│ state.exit_code  │ state.verdict
      cleanup, reverse: Job and script ConfigMap (RunProbeJob), namespace (OpenRunScope) on every exit, with absence
  read-back
                                                              │
                                      shared report ─► JSON result, sanitized receipt (schema image-build-readiness)

    (PR-CI13-2) agent ─ image_build_readiness.py chain --node N ──► probe rows ─► chain 1 runner with the same setup
                                                                   ─► job on the test project builds and pushes ─► digest read-back

Data flow runs left to right through named state fields; control flow is the executor's `collect → run → update`
per callback and reverse `cleanup` for every entered callback on success and on failure, inside the executor's
recoverable-signal guard so an interrupt still reaches cleanup; construction dependency is the callback order the
adapter declares. Nothing of the probe survives the run except the result, the receipt and, when the agent names
an evidence directory, the log and the rows.

## Implementation Plan

### Current State and Intended Outcome

NOT_IMPLEMENTED: the probe and the chain. Inspected at main `352128d` (documentation only for this phase). The
relevant tree, with the files this phase adds marked NEW:

    ci-skills/
      bin/
        image_build_readiness.py                 NEW  thin main: probe (PR-CI13-1), chain (PR-CI13-2)
        k8s_verify_mtu_consistency.py                 precedent: confirmed plan, temporary privileged Pods, cleanup read-back
      lib/python/core/
        abstract/                                     callback lifecycle, executor with recoverable signals, state, action
        executor.py                                   WorkflowExecutor(context, Callbacks([...]))
        delivery_state.py                             DeliveryState, ProviderAuthAction
        catalog.py                                    the catalogue source; tools.json is rendered from it
        cli.py                                        shared parser; --output-dir means paired report output
        gitlab/gitlab_action.py                       GitLabWorkflow: the domain action adapter this phase mirrors; consumes the shared mode validator
        action_modes.py                          NEW  one shared mode validator (dry-run, live-plan, apply, receipt rules), extracted from three copies
        kubernetes/kubernetes.py                      K8SCallback, K8SAuth, KubernetesService (command, json, read, apply, delete, wait_job)
        kubernetes/resources.py                       ResourceSet, OPERATION_LABEL
        kubernetes/runner.py                          PullImage: temporary Job with cleanup on every exit
        kubernetes/mtu_consistency.py                 privileged debug Pods, marker-based cleanup, interrupt handling
        kubernetes/run_scope.py                       RunScope over ResourceSet; OpenRunScope (delivered by CI12.0)
        kubernetes/image_build_probe.py          NEW  SelectNode, RunProbeJob, ReadProbeResult, ParseMarkers
        kubernetes/image_build_chain.py          NEW  PR-CI13-2: composition over chain 1 and block 7
        image_build_action.py                    NEW  ReadinessState, ReadinessAction, ConfirmReadinessOperation, ReadinessWorkflow
      schemas/
        results/image-build-readiness.schema.json NEW
      lib/python/sys/status.py                        PASS, DRY_RUN, PLANNED, PARTIAL, BLOCKED, APPLIED, NO_OP
    tools/check_live_acceptance.py                    required operation set, GATE_SURFACES, the GitLab receipt validator
    tools/render_manifest.py                          renders tools.json; --check fails on a stale copy
    inventory/ci/live-acceptance.json                 declared receipt directories, freshness
    tests/acceptance/receipts/                        committed sanitized receipts, all bound to one functional checksum

CI topology, from `.github/workflows/validate.yml`, existing elements plain and the entries this phase adds
marked `+`:

    pull request head (exact SHA)
      ├─ validate ────────────────┐ static gate → docstrings → manifests and schemas (render check of tools.json;
      │                           │ metaschema list + image-build-readiness result schema)
      │                           │ → unit tests (tests/python; + test_run_scope, + test_image_build_probe)
      │                           │ → offline acceptance tests → live_acceptance: checker over the committed receipts (unchanged)
      ├─ gate-ci-skills-endpoints ┤ endpoint declarations, unchanged
      ├─ test-install ────────────┤ installer fixtures and toolchain schema, unchanged
      │        each job writes its evidence file (artifact) ▼
      └─ required-result ─────────┴─► aggregates the three evidence files → publishes the final ci-skills/required status
                                          │ ruleset "protected": ci-skills/required SUCCESS on the up-to-date head, else no merge

The three jobs run in parallel and share nothing but the head; `required-result` consumes their evidence
artifacts and publishes the final required status (validate publishes a pending one at its start), so a red step
in any job stops advancement there. The local pre-commit gate `./bless.sh` runs the same whitespace, JSON, schema,
markdown, shell, Python and endpoint checks on the working tree; its schema selector matches only `schemas/*.json`
today, so the existing pairing rule for the new schema needs the selector to include `ci-skills/schemas/`, which is
open question 3 because it also changes what the gate checks for the existing result schemas.

Nothing in the skill runs podman, reads a Job's log into rows, or picks a node. The skill does have: the temporary
Job with cleanup on every exit (`PullImage`), the privileged debug Pods of the MTU check with marker-based cleanup
and interrupt handling, the Kubernetes service's `command`, `json`, `read`, `apply`, `delete` and `wait_job`
calls (the Job log and the Pod status are read through `command` and `json`; there is no dedicated log call), and
the GitLab adapter's mode and report pattern. The command is declared in `core/catalog.py`, the single source from
which `tools.json`, `--help` and `--describe` are rendered.

The gate today accepts a receipt only when its kind, operation and result action are in the checker's required
operation set, its functional checksum equals the candidate's, it passes the GitLab receipt validator and it
binds to exactly one setup context built from a `gitlab_access` receipt; any other receipt in a declared
directory turns the gate red, and every committed receipt turns red once anything under `ci-skills/bin`, `lib`
or `schemas` changes until the README rerun procedure refreshes it; a setup context is the host, origin, identity
and target the access receipt recorded, which every operation receipt must match. Those are facts about the
existing gate; what this phase does about them is open question 2. One more fact shapes the adapter: the mode
rules (dry-run against apply, live-plan, and that `--receipt-out` needs a live run) exist today in three copies,
in the toolbox actions, the GitLab adapter and the MTU check.

Intended outcome: seven requirements, two pull requests, two waves, one command with the proposed verbs `probe`
and, later, `chain`, sharing the mode flags of the GitLab actions (`--dry-run`, `--live-plan`, `--apply
--confirm-plan`); the probe's callbacks reusable by the chain and the run scope shared with CI12. The command
surface is open question 1 until the owner approves it; the invocations below show the proposal exactly.

### Design and Decomposition

One concrete state. `ReadinessState(DeliveryState)` adds the named fields the diagram shows: `node`, `run_scope`,
`job`, `log`, `exit_code`, `rows`, `verdict`. Every callback reads earlier fields and writes its own; the fields
are the only hand-off.

One action. `ReadinessAction(SkillAction)` is a frozen dataclass with `node` (optional; otherwise the combo picks a
Ready worker and names it), `probe_image` and `inner_image` (digests; the shipped defaults are pinned in the pull
request and a tag without a digest is refused), `timeout` (the Job's active deadline), `evidence_dir` (optional;
where the Job log and the rows persist), the resource bounds with defaults, and the shared mode flags. Every
environment specific arrives here as an argument or through the target resolver; none is hardcoded.

One adapter. `ReadinessWorkflow` in `core/image_build_action.py` mirrors `GitLabWorkflow`: `prepare()` validates
the mode, resolves the Kubernetes target (offline for `--dry-run`), builds the plan and its digest from the
caller's inputs and the resolved target (node, both image digests, timeout, bounds, context and server), and for
an apply compares `--confirm-plan` with that digest before any callback runs; only then does it generate the run
identifier (`RunScope.new()`), so the identifier is never part of the digest and a dry run's digest is reproduced
by the apply. The modes are explicit per callback sequence: a live plan runs `K8SAuth` and `SelectNode` only and
returns `PLANNED` with the node read back, no confirm callback and no mutation; an apply runs `K8SAuth`, then
`ConfirmReadinessOperation` (re-verifies the target against the plan, as `ConfirmGitLabOperation` does), then
`SelectNode` and the probe sequence; `--receipt-out` is accepted on an apply, which is the probe's live run, and
rejected with the shared reason on a dry run. The mode rules are not copied a fourth time: `prepare()` calls one
shared validator, `core/action_modes.py`, extracted from the three existing copies with a per-adapter read and
receipt policy, and the GitLab adapter switches to it in the same pull request (the toolbox and MTU copies follow
as their own change). `execute()` runs the executor and renders the shared report; the result validates against
`schemas/results/image-build-readiness.schema.json`. The toolbox adapter `WorkflowAction` is not reused, for the
reason the contribution guide states: it requires a toolbox catalogue kind and parses a toolbox specification.

Callbacks, each a `K8SCallback[ReadinessAction]` unless stated, each owning its hooks only, with their module:

- `SelectNode` (`core/kubernetes/image_build_probe.py`): `run` reads the named node, or lists the nodes and picks
  the first Ready worker by name order, and writes `state.node`; `update` records the choice and the node's
  kernel and OS image as read from the API, so the probe's kernel row can be compared with the API's view.
- `OpenRunScope` (`K8SCallback[RunScopeAction]`, `core/kubernetes/run_scope.py`, as CI12 specifies it): `run`
  applies the Namespace through the scope's cluster-scoped set; `cleanup` removes it on every exit with absence
  read-back. No ServiceAccount is created: the probe uses the namespace's default account.
- `EnsureSCCAccess` (`K8SCallback[SCCAccessAction]`, `core/kubernetes/scc_access.py`): `run` reads
  the selected OpenShift SCC ClusterRole and service account's permission on the bound target. The action accepts
  those provider names without a consumer-specific allowlist; the Podman consumer selects `privileged` and
  `default`. Existing
  permission returns `NO_OP`. If the SCC and role exist and permission is absent, it preflights create/delete of one
  namespaced RoleBinding plus bind of that exact ClusterRole, applies the run-owned RoleBinding through
  `ResourceSet`, then reads both the object and resulting permission back. `cleanup` removes only a binding this
  callback created, with absence read-back. Only a target whose API discovery lacks the OpenShift SCC API returns
  the verified `not_applicable` `NO_OP`. On OpenShift, a missing selected SCC or generated ClusterRole blocks.
- `RunProbeJob` (`core/kubernetes/image_build_probe.py`): `collect` renders a script ConfigMap and Job from the
  action: one privileged container from the probe image, pinned to `state.node`, `backoffLimit` 0, the deadline,
  a TTL, no service-account token, the storage and evidence emptyDirs with size limits, the bounds, and the
  probe script as the container command, which runs podman with the overlay driver and the cgroupfs manager,
  runs the inner container with limits, writes and reads back through the bind mount, runs the second inner
  container that exits 7, removes both and lists what remains, printing one marker line per step; `run` applies
  the Job through the scope's namespaced set after its read-only script ConfigMap and waits for it within the
  deadline, classifying an admission rejection of the privileged Pod as `scc_required` with the namespace's
  service account named; `update` reads the Job back and writes `state.job`; `cleanup` deletes the Job then its
  ConfigMap on every exit with absence read-back, using the existing `ResourceSet` ownership journal.
- `ReadProbeResult` (`core/kubernetes/image_build_probe.py`): `run` reads the Job's Pod through the service's
  `json` call and takes the container's terminated exit code as `state.exit_code`, then reads the log through the
  service's `command` call into `state.log`; when the agent named an evidence directory, `update` writes the log
  there.
- `ParseMarkers` (`LocalCallback`, `core/kubernetes/image_build_probe.py`): `run` parses `state.log` into
  `state.rows`, one per marker: host kernel, podman version, storage driver with graph root and cgroup version,
  backing filesystem, inner run, bind-mount read-back, exit propagation (expected 7, observed), remaining
  containers; a marker that is absent becomes a row with `observed: missing`; `update` writes `state.verdict`:
  `PASS` when the exit code is 0 and no row is missing or failed, otherwise `PARTIAL` with the failed rows, and
  writes the rows beside the log when an evidence directory was named.

Reuse: `K8SAuth` first; `ResourceSet` for everything created; the temporary-Job pattern of `PullImage`; the
executor's recoverable-signal guard and reverse cleanup, which is what the MTU check's interrupt handling
guarantees by hand; the statuses from `ci-skills/lib/python/sys/status.py`; the existing report renderer and the
portable receipt writer in `core/portable.py`; the catalogue declaration and render tool.

Lifetime, decided as mechanism: nothing of the probe is kept. The namespace, script ConfigMap and Job are owned
by the run identifier and removed by their creators' cleanup hooks on success, on failure and on interrupt, each
with absence read-back; a cleanup hook that raises is rendered as `BLOCKED` with `cleanup.status` `BLOCKED` and
the run identifier, and deleting by the operation label is the manual recovery. A new run never changes or
deletes another run's objects, because `ResourceSet` refuses to modify an existing object whose operation label
differs.

Concrete statuses, from the shared validator: `--dry-run` returns `DRY_RUN` in phase `OFFLINE_PLAN` with the plan
digest and no API call, and rejects `--receipt-out`; `--live-plan` returns `PLANNED` in phase `LIVE_PLAN` after
`K8SAuth` reads the identity back and `SelectNode` reads the node, with no confirm callback; `--apply --confirm-plan
<digest>` returns `PASS` when the Pod exited 0 and every row is present and passed, `PARTIAL` when the Pod ran but a
row is missing or failed, and `BLOCKED` with a classified reason (`scc_required`, `job_deadline_exceeded`,
`node_not_ready`, `image_not_pinned`) when the probe could not run; the exit code is 0 for `PASS`, `DRY_RUN` and
`PLANNED`, and 2 for `PARTIAL` and `BLOCKED`.

#### Invocation and the result the agent reads

The agent invokes the command the way SKILL.md routes every installed command, from the skill root, and reads the
shared envelope the result schema declares; no new output format. The option names below are the proposal under
open question 1. The top-level keys are the existing envelope keys; the rows inside `records` and `readback` are
the paired `image-build-readiness` schema's to declare, sketched here. Values in angle brackets are placeholders,
never examples.

    conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/image_build_readiness.py" --describe

returns the command contract as every installed command does: `kind: command_contract`, `command`, `purpose`,
`options` with one line each, `status_values`, `exit_codes` (`0` for `PASS`, `DRY_RUN` and `PLANNED`; `2` for
`BLOCKED` and `PARTIAL`), `requires_authorities: [kubernetes]`, `mutates: true`, `report_kind:
image_build_readiness`, `side_effects`. The verb is a positional argument, shown in the `--help` usage line as
`{probe}` (and `{probe,chain}` after PR-CI13-2) the way the GitLab runner command shows its actions.

    conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/image_build_readiness.py" probe --node <NODE> --dry-run --json

    {
      "schema_version": "1.0", "kind": "image_build_readiness", "status": "DRY_RUN", "phase": "OFFLINE_PLAN",
      "operation": "probe", "mutated": false,
      "plan": { "node": "<NODE>", "probe_image": "<registry>/<image>@sha256:<...>", "inner_image": "<registry>/<image>@sha256:<...>",
                "timeout": <seconds>, "bounds": { "cpu": "<...>", "memory": "<...>", "storage": "<...>" },
                "target": "<context> -> <server>", "names": "<pattern derived from the run identifier>",
                "objects": ["Namespace", "ConfigMap", "Job"] },
      "plan_digest": "sha256:<digest>",
      "summary": "<one line>", "errors": [], "captured_at": "<time>"
    }

    conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/image_build_readiness.py" probe --node <NODE> \
      --apply --confirm-plan <digest> --json --receipt-out <PATH>

    {
      "schema_version": "1.0", "kind": "image_build_readiness", "status": "PASS", "phase": "APPLY",
      "operation": "probe", "result_action": "APPLIED", "mutated": true,
      "plan_digest": "sha256:<digest>", "verified_target": { "context": "<context>", "server": "<server>" },
      "identity": { "<the identity K8SAuth read back>": "..." },
      "records": [ { "step": "SelectNode", "verified": true, "node": "<NODE>", "chosen": false,
                     "api_kernel": "<version>", "os_image": "<name>" },
                   { "step": "RunProbeJob", "action": "APPLIED", "verified": true, "namespace": "<name>", "job": "<name>",
                     "pod": "<name>" },
                   { "step": "ReadProbeResult", "verified": true, "exit_code": 0, "log_lines": <count> },
                   { "step": "ParseMarkers", "verified": true, "verdict": "PASS",
                     "rows": [ { "row": "host_kernel",        "observed": "<version>",          "ok": true },
                               { "row": "podman_version",     "observed": "<version>",          "ok": true },
                               { "row": "storage",            "driver": "overlay", "graph_root": "<path>", "cgroup_version": "<v>", "ok": true },
                               { "row": "backing_filesystem", "observed": "<fs>",               "ok": true },
                               { "row": "inner_run",          "observed": "<marker>",           "ok": true },
                               { "row": "bind_mount_readback","observed": "<content>",          "ok": true },
                               { "row": "exit_propagation",   "expected": 7, "observed": 7,     "ok": true },
                               { "row": "remaining_containers","observed": [],                  "ok": true } ] } ],
      "readback": { "verified": true, "run_id": "<run identifier>", "node": "<NODE>", "exit_code": 0,
                    "namespace_absent": true, "job_absent": true },
      "cleanup": { "status": "PASS" },
      "summary": "<one line>", "errors": [], "captured_at": "<time>"
    }

A row that fails or is missing turns the verdict to `PARTIAL` with `ok: false` or `observed: "missing"` on that row
and exit code 2. When the selected identity cannot prepare the missing OpenShift permission, the command blocks
before Job creation, for example:

    {
      "schema_version": "1.0", "kind": "image_build_readiness", "status": "BLOCKED", "phase": "APPLY",
      "operation": "probe", "mutated": true,
      "errors": [ { "source": "EnsureSCCAccess", "reason": "scc_cluster_role_bind_denied" } ],
      "readback": { "verified": true, "run_id": "<run identifier>", "namespace_absent": true, "job_absent": true },
      "cleanup": { "status": "PASS" },
      "summary": "<one line>", "captured_at": "<time>"
    }

The agent reports that missing authorization to the user; no external grant can stand in for capability proof.
Every verb also accepts `--live-plan` (`status: PLANNED`, identity and node read back, no mutation),
`--yaml`, `--human`, `--output-dir` for paired reports, `--evidence-dir` for the persisted log and rows, and the
shared diagnostic options.

#### When to Decompose

Two slices, because they have independent reasons to change and independent proofs: the probe (PR-CI13-1) is
provable with nothing but the cluster, one node and the constraint; the chain (PR-CI13-2) depends on chain 1's
runner manager and block 7's registration and test job, which are their own deliveries, and on the test project
and Harbor. Each is one pull request with its own live proof.

#### When to Introduce Abstraction

[CI12.0](CI12-NODE-ONBOARDING.md#pr-order) delivers `RunScope` over `ResourceSet` and `OpenRunScope`.
CI13 consumes that shared implementation and its tool preparation callbacks where needed; it does not create
another run scope, downloader or installer. The marker parser is a pure function over the
log with a table of expected markers, not an abstraction: one consumer, one shape, tested against fixture logs.
The checked probe scripts live in `ci-skills/lib/bash/kubernetes/`. A read-only ConfigMap mounts them with the
shared Bash runtime; the Job invokes the script file directly. The plan digest binds all script bytes as well as
the image digests, and the run's existing `ResourceSet` removes the Job then its ConfigMap before the namespace.

### Open Decisions and Scope Boundary

The phase identity and shared-foundation delivery order are settled by the operator-approved CI12/CI13
specifications. Missing optional roadmap files do not create another approval requirement. The architecture
format is documented above; the existing pinned checks remain authoritative.

Open questions for the owner. Each is a requirement, a user-facing contract or a gate behaviour that only the
owner decides; the document records the options and the finding that raised each, and decides none of them:

1. The command surface: the command name `image_build_readiness.py`, the verb `probe` (and `chain` later), and
   the options `--node`, `--probe-image`, `--inner-image`, `--timeout`, `--evidence-dir` and the resource bounds,
   as "Invocation and the result the agent reads" shows them. The shipped default digests for the probe image and
   the inner image are pinned in the pull request and listed in its body for approval.
2. How this command's live evidence enters the gate. Facts: the checker accepts only its declared GitLab kinds
   and rejects any other receipt in a declared directory; it fails on a missing required operation; every
   committed receipt turns red when anything under `ci-skills/bin`, `lib` or `schemas` changes, so each of these
   pull requests triggers the README rerun of the whole set. Options: extend the checker with a validator, a
   kubernetes setup context and required operations for this kind; a separate receipt directory with its own
   freshness policy; or evidence attached to the pull request only, with no committed receipt. Until answered,
   the pull requests attach their live evidence and commit no receipt under a declared directory.
3. The local gate's schema selector matches only `schemas/*.json`, so the existing pairing rule cannot cover the
   new result schema without widening it to `ci-skills/schemas/`, which also starts checking the existing result
   schemas locally.
4. Resolved by the operator: the installed command performs every prerequisite needed by the probe. On OpenShift
   it reuses an existing `privileged` permission or creates one run-owned namespaced RoleBinding to
   `system:openshift:scc:privileged`, verifies it on the same target, and removes only that binding. External test
   setup is diagnostic evidence only and cannot make the capability pass. The selected identity therefore needs
   read access to the exact SCC ClusterRole and ServiceAccount authorization; when permission is absent it also
   needs create/delete RoleBinding and bind rights for that exact ClusterRole in the selected namespace.
5. Resolved delivery order: CI12.0 delivers `core/kubernetes/run_scope.py` and reusable tool preparation.
   CI13 consumes them. The shared mode validator `core/action_modes.py` serves both adapters; reuse the existing
   implementation if already delivered rather than introduce a duplicate.
6. Chain 11's later steps (runner with the same setup, build-and-push job, digest read-back) depend on chain 1's
   runner manager and block 7's registration and test job, which are not delivered; PR-CI13-2 waits for them and
   its verb, `chain`, is a proposal.

Decided as mechanism inside the library, recorded so no step hides a choice:

- The Pod's terminated exit code, read from the API, is the probe's result; the marker lines fill the rows; a
  missing marker is a row, never a silent gap.
- Node selection without an argument: the first Ready worker by name order, named in the result, so two runs on
  the same cluster pick the same node unless it changes.
- The probe runs under the namespace's default service account with no token mounted. Existing SCC permission is
  preserved. When the exact OpenShift SCC ClusterRole exists and permission is absent, one run-owned namespaced
  RoleBinding is created, read back and removed; no ServiceAccount or cluster-wide binding is created.
- The Job log is read through the service's `command` call and the Pod status through `json`, because the service
  has no dedicated log call; adding one is not needed for one consumer.
- Image references are digests; a tag without a digest is refused before any plan is produced.
- Nothing persists beyond the result unless the agent names an evidence directory; the namespace and the Job are
  removed on every exit.

The boundary is the two slices above. The runner manager and block 7 stay their own deliveries.

## PR Order

PR-CI13-1 (wave W-CI13-1) consumes the merged CI12.0 foundation and delivers the node selection,
the probe Job and its script, the result and log read-back, the marker parser, the adapter and the thin main with
the `probe` verb, the catalogue declaration with its regenerated `tools.json` and result schema, the schema entry
in the validate workflow per the existing pairing rule, and the routing; its live proof is the probe run from
the installed candidate on one node of the live cluster with every row present and the Pod exited 0, and the
namespace, RoleBinding and Job absent afterwards. A permission preflight failure is a blocked capability and is
not accepted as live proof. PR-CI13-2 (wave W-CI13-2) depends on it and on chain 1 and block 7, and delivers the
`chain` verb that composes the probe with the runner, the build-and-push job and the digest read-back; its live
proof needs the test project and Harbor. Per the owner's rule, each pull request is live-tested on its own head
from the
installed candidate before it merges. Live proof is bound to the functional checksum; wording-only docs or
test changes do not invalidate unchanged executable behavior. What each pull request commits
into the gate follows the owner's answer to open question 2; until then the evidence is attached to the pull
request. Each pull request leaves the installed command coherent under the existing gates, with the
unimplemented verb absent from the catalogue rather than stubbed.

## Gates

The traceability map in `Authority` is the canonical map. `G-CI13-STATIC` is the existing validate workflow and
the local pre-commit gate; its enforcement is the main ruleset's required `ci-skills/required` status with the
strict up-to-date policy, read back from the repository's branch rules on 2026-10-15. `G-CI13-LIVE` is the live
run of each slice from the installed candidate on the user's live smoke setup, with one receipt per verb and
result action; it is proposed, its evidence is attached to the pull request, and whether its receipts enter the
committed gate is open question 2. `G-CI13-GATE` is the existing live-acceptance step, unchanged by this phase
until that question is answered.

Per-requirement gaps today, all of the same kind: no module, no unit test and no live run exists for any of
`R-CI13-001` to `R-CI13-007`, so every row records `UNVERIFIED`. The specific evidence each needs: `R-CI13-001`
a node selection test (named node, chosen worker, no Ready worker) and a digest test (same inputs and target
reproduce it, another target does not); `R-CI13-002` a Job document test (privileged, pinned node, no token,
emptyDir limits, bounds, deadline, TTL, digests only, tag refused) and the live Job's read-back; `R-CI13-003` a
parser test against fixture logs (all markers, one missing, exit propagation 7 and not 7, remaining containers
non-empty) and the live rows with the Pod's exit code; `R-CI13-004` a classification test on an admission
rejection message, existing-permission no-op preservation, temporary RoleBinding read-back and cleanup, and denial
before Job creation; `R-CI13-005` a scope test (names
derive from the identifier, removal deletes exactly the owned set) and the live absence read-back, including an
interrupted run; `R-CI13-006` the catalogue consistency test, the render check and `--describe` without
credentials; `R-CI13-007` nothing until chain 1 and block 7 exist.

## Live Smoke Evidence

Planned, not observed. `probe`: the command run from the installed candidate against the live cluster on one node
the agent names; evidence is the result's rows with the Pod's terminated exit code, the chosen node, the run
identifier, the permission source, and the namespace, run-owned RoleBinding and Job absent afterwards. External
authorization setup is not capability proof. A second run interrupted during the Job proves cleanup on interrupt
by the same absence read-back. Each receipt is sanitized with the portable writer and carries the functional
checksum of the installed candidate; where it is committed is open question 2. The chain's evidence
(runner, job, digest) waits for PR-CI13-2.

## End-to-End Test

The consumer journey: the agent names a node (or not), runs `probe`, and reads back every row passed with the
Pod exited 0 and nothing left in the cluster carrying the run identifier; later, `chain` carries the same node
through a runner, a build-and-push job and a pushed digest. The proposed test is the probe's live receipt bound to
the functional checksum, then, after PR-CI13-2, the chain's receipts linked by the same run identifier; its
acceptance gate is `G-CI13-LIVE`. No execution is recorded yet.
