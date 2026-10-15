# Combos: chains worth packing

A combo is one installed command that walks a chain of atomic operations and returns one result with its own
read-back evidence. The agent runs the combo instead of hand-rolling the `glab`, `kubectl`, or `jq` calls behind it,
and keeps its context for the task. This catalogue lists chains worth packing, in the format
`step → step → step`, with the goal, the status of each step, and the evidence that proves the chain. It is an
exploration list: an entry marked proposed is an idea to evaluate, not a commitment.

Status words: **exists** means an installed command with a live receipt in
[`tests/acceptance/evidence`](../tests/acceptance/evidence/README.md) (case numbers in brackets); **planned** means
specified in [CI11](phases/CI11-TOOLS.md) or the [GitLab tool belt plan](gitlab-toolbelt-plan.md); **missing**
means no command yet. [CI11 "Combos"](phases/CI11-TOOLS.md#combos) owns the delivery entries; this list points at
them where they exist and adds chains they do not name yet. A chain is proven only by one live run whose rows link,
each consuming the previous row's output, bound to one functional checksum; see block 7 of the tool belt plan for
the six-row model.

## 1. Runner proven by a test job

`build toolbox → push to Harbor → create/configure runner → execute test job → verify results → cleanup`

Goal: a runner the agent created runs a real job, and the job read-back names that runner.

GitHub today: the first three steps are already one installed combo, `create_selfhosted_runner.py create` (build
the toolbox, push it, deploy the runner, register it), exists and the runner comes online [9]; the standalone
`toolbox.py` [35, 37] and `harbor_push.py` [31] exist for the parts. Execute test job, missing (the skill has no
workflow dispatch) → verify results, missing → `create_selfhosted_runner.py delete`, exists [10].

GitLab today: `toolbox.py` and `harbor_push.py` as above → `gitlab_runner.py create`, exists, record only [23],
with the runner manager deployment planned (block 7) → `gitlab_pipeline.py start` and `watch`, planned (CI11) →
`gitlab_job.py get`, exists [16], but naming the created runner is unproven: case 16 is PARTIAL because that job
never reached a runner, and that read-back is block 7's proof → `gitlab_runner.py delete`, exists [24], with
deployment removal planned.

## 2. OpenShift ignition for a new machine (proposed)

`read machine spec → use pull secret → read spec for ignition → build ignition → verify`

Goal: produce the Ignition document a new node or VM needs from the cluster's own sources, so the agent never
assembles it by hand from several `oc` reads.

- Read machine spec: the target pool's machine objects and their role, image, and network settings.
- Use pull secret: resolve the pull secret the cluster uses for images; record its name and digest, never its
  value. The skill already handles pull secrets as `dockerconfigjson` Secrets in
  [`core/kubernetes/runner.py`](../ci-skills/lib/python/core/kubernetes/runner.py).
- Read spec for ignition: the pool's rendered machine configuration and the Ignition version it declares.
- Build ignition: merge the pointer configuration, the pull secret reference, and the declared files and units
  into one Ignition document, written to a caller-selected path.
- Verify: the document validates against the Ignition version it declares, every reference resolves, and the
  result records the document's SHA-256 and size.

Status: proposed as a chain; its read steps are planned in CI11 (`ocp_machine.py` for machine objects and
machine configuration, `harbor_pull_secret.py` for the pull secret). Nothing in the skill reads machine
configurations or writes Ignition today. Evidence rows: machine spec read, pull secret resolved, ignition source
read, ignition built, verify passed.

## 3. Failed pipeline, root cause in one go

`select pipeline → watch until settled → logs of failed jobs → job get → open bug with pointers`

Goal: from a pipeline ID to an issue that names the failed jobs, their runners, and trace excerpts, without the
agent paging through the pipeline by hand.

Steps: `gitlab_pipeline.py --pipeline-id ID` (the bare read), exists [21] → `watch` and `logs`, planned (CI11) →
`gitlab_job.py get`, exists [16] → `gitlab_issue.py open-bug`, exists [14, 15]. CI11 plans this pack as
`ci_failure_trace.py`. Evidence: pipeline ID and settled status, failed job IDs with trace tails, the issue IID
read back, NO_OP on a repeat.

## 4. Milestone close-out

`milestone get → adjust dates → close → read back`

Goal: close a milestone with its dates settled, in one call that is a NO_OP when already done.

Steps: milestone get, planned (CI11 `gitlab_milestone.py list`) → `adjust-time`, exists [18] → `update --state
closed`, implemented, no live receipt yet (the update receipt [20] changed title and description) → read back.
Missing: a read of the open issues still attached to the milestone, so the combo can refuse or report before
closing. Evidence: milestone ID, dates, and `closed` in the read-back; NO_OP on a repeat.

## 5. Cluster health in one go

`access check → Ceph → Cilium → storage → events → one report`

Goal: one read-only document with a status per surface, instead of five commands and a hand-written summary.

Steps: `access_check.py`, `ceph_cluster.py`, `cilium_status.py`, `storage_report.py`, `event_trace.py`, all exist
[1, 5, 8, 34, 12]. Each is already a combo over several `kubectl` reads. The pack is planned in CI11 as
`cluster_health.py`, which also covers MTU and controllers; its one report carries a status per component, bound
to one digest.

## 6. Node triage

`cilium node → Ceph kernel journal → MTU verify`

Goal: one node name in, three findings out. Steps: `cilium_node.py`, `ceph_kernel.py`,
`k8s_verify_mtu_consistency.py`, all exist [7, 6, 32]. Gap: only the MTU check takes `--node`; the other two read
their node from the target's `kubernetes.node_diagnostics`, so per-node selection is the piece to add.

## 7. Image promotion

`pull approved digest → push copy → read back digest`

Goal: promote one image by digest between Harbor projects or tags with both read-backs in one result. Steps:
`harbor_pull.py pull` and `harbor_push.py push`, exist [30, 31].

## 8. Runner lifecycle

`create → assign → tag → get → delete`

All steps exist [23, 22, 27, 25, 24]. The gap is the runner going online, which is chain 1.

## 9. Toolbox refresh

`toolbox update → push to Harbor → runner update to the new image`

For GitHub this is already one installed command, `create_selfhosted_runner.py update`, which rebuilds, pushes and
redeploys, exists [11]; the standalone `toolbox.py update` [37] and `harbor_push.py push` [31] exist for the parts.
The open gap is the GitLab runner update, which is part of chain 1.

## 10. GPU state before the rabbit hole (proposed)

`nodes with GPUs → GPU load per node → NVIDIA operator state → one compact view`

Goal: the agent sees the GPU estate in one compact document before it starts debugging a scheduling or driver
problem, instead of walking nodes, Pods, and the NVIDIA operator's objects by hand.

- Nodes with GPUs: every node that advertises `nvidia.com/gpu`, with capacity, allocatable, the GPU product and
  driver labels, and the node's readiness and taints.
- GPU load per node: utilization and memory per GPU, read without a TTY from the exporter or driver Pod on each
  node in the same way the Cilium checks read `cilium-health`; a node whose Pod cannot answer is reported as that
  node's state, not as a failure of the whole read.
- NVIDIA operator state: the cluster policy's readiness, each operator component (driver, toolkit, device plugin,
  exporter, feature discovery) as a DaemonSet with desired versus ready counts, and the versions they run.
- One compact view: nodes down the side, columns for GPUs, load, driver, and findings; findings name the usual
  causes first, such as a GPU node without a device-plugin Pod, a Pod pending on `nvidia.com/gpu`, or a driver
  version that differs across nodes.

Status: proposed. Nothing in the skill reads GPU nodes or the NVIDIA operator today. Reuse: DaemonSet discovery
through the owner's selector and non-TTY exec from the Cilium checks, Pod records from `ceph_cluster.py`, and
events from `event_trace.py`. Evidence rows: node inventory read, load read per node, operator state read,
findings listed, all bound to one digest.

## How to add one

Design the evidence rows first and name which existing command supplies each row. Then write the combo as a small
main that composes atomic callbacks into an ordered chain, with state as the only hand-off between them, following
the [contribution guide](contribution_guide.md). Prove it with a live smoke on real systems from the installed
candidate, commit the sanitized receipts, and keep the chain's functional checksum bound to them. If no existing
command supplies a row, that row is the new capability to build, and it is marked planned here until its receipt
exists.
