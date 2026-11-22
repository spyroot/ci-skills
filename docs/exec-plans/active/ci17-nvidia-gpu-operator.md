# NVIDIA GPU Operator atomic `Nvidia*` callbacks and chain combos on OpenShift

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and
`Outcomes & Retrospective` must be kept up to date as work proceeds. It is maintained in accordance with
[docs/PLANS.md](../../PLANS.md) and delivers phase [CI17][phase]. Source baseline: `1ad150e`.

## Purpose / Big Picture

[CI16][ci16-exec] delivers generic OLM operator enablement (`ocp_operator.py`) using the NVIDIA Network Operator as its
initial consumer. OpenShift clusters running GPU workloads also require NVIDIA GPU Operator (`gpu-operator-certified`)
lifecycle automation grounded in the official [NVIDIA GPU Operator on OpenShift documentation][nvidia-ocp-docs],
including [Cleanup][nvidia-ocp-cleanup], and OpenShift-native semantics (`MachineSet`, `MachineConfig`,
`MachineConfigPool`, CRI-O/CDI, cluster monitoring, Driver Toolkit, and MOK signing keys).

This plan partitions NVIDIA GPU Operator operations into **atomic, `Nvidia*`-prefixed callbacks first** — each a
reusable `K8SCallback` owning one official NVIDIA OpenShift procedure — and then composes **chain combos** from those
callbacks plus CI16's OLM callbacks via `WorkflowExecutor`:

- `NvidiaPrerequisite` — NFD check, private registry `docker-registry` image pull Secret (via `[registry.<name>]`),
  and optional NLS `licensingConfig` Secret (`licensing-config`).
- `NvidiaClusterPolicy` — creates or patches `ClusterPolicy` (`gpu-cluster-policy`), wiring driver `repository`,
  `image`, `version`, `imagePullSecrets`, and `licensingConfig` without hand-editing raw JSON manifests.
- `NvidiaDriverPool` — creates or patches per-pool `NVIDIADriver` CRs when `useNvidiaDriverCRD: true`.
- `NvidiaHealthCheck` — out-of-the-box sanity check verifying every expected GPU Operator DaemonSet, validator Pod
  (`nvidia-cuda-validator`, `nvidia-device-plugin-validator`, `nvidia-operator-validator`), `ClusterPolicy` state,
  driver drift, allocatable GPUs, and `MachineConfigPool` health.
- `NvidiaCudaUpgrade(node, driver)` — cluster-wide (`node="*"`) or node/pool-scoped CUDA/driver upgrade.
- `NvidiaContainerToolkit` — `ClusterPolicy.spec.toolkit` + `spec.cdi` aligned with OpenShift CRI-O and
  `MachineConfigPool`.
- `NvidiaEnableFeatureDiscovery` — GPU Feature Discovery (`ClusterPolicy` `/spec/gfd/enabled` + NFD prerequisite).
- `NvidiaEnableMonitoring` — labels `ns/<namespace> openshift.io/cluster-monitoring=true` and enables DCGM Exporter
  `ServiceMonitor` / OpenShift Console monitoring integration.
- `NvidiaTimeSlicing` — creates/updates multi-profile time-slicing `ConfigMap` (for example `small`: 2, `medium`: 5,
  `large`: 10, `renameByDefault: false`, `failRequestsGreaterThanOne: false`) and binds `spec.devicePlugin.config`.
- `NvidiaMachineSetLabel` — patches OpenShift `MachineSet` template labels in `openshift-machine-api`
  (`/spec/template/spec/metadata/labels/<key>`) so autoscaled GPU nodes inherit sharing or driver-track labels.
- `NvidiaMachineConfig` — OpenShift `MachineConfig` / `MachineConfigPool` alignment (MOK key, IOMMU, CRI-O/CDI).
- `NvidiaUpgradePolicy` — `upgradePolicy` and per-node skip / `upgrade-required` recovery labels.
- `NvidiaCleanup` — ordered OpenShift teardown per `clean-up.html` (`--delete-crds` explicit opt-in).

Example golden-path commands after delivery:

```bash
# Out-of-the-box NvidiaHealthCheck (all GPU Operator Pods, DaemonSets, CRD, MCO, drift)
nvidia_gpu_operator.py health-check --json

# Atomic NvidiaEnableFeatureDiscovery (CRD-verified /spec/gfd/enabled)
nvidia_gpu_operator.py enable-gfd --enabled true --live-plan

# Atomic NvidiaTimeSlicing + NvidiaMachineSetLabel
nvidia_gpu_operator.py time-slicing --config-name time-slicing-config \
  --profile small:2 --profile medium:5 --profile large:10 \
  --machineset worker-gpu-nvidia-t4 --machineset-profile small --live-plan

# Atomic NvidiaCudaUpgrade(node="*", driver="580.159.03") or scoped to one pool/node
nvidia_gpu_operator.py cuda-upgrade --node "*" --nvidia-driver <name> \
  --driver 580.159.03 --min-cuda 12.8 --live-plan
nvidia_gpu_operator.py cuda-upgrade --node "*" --nvidia-driver <name> \
  --driver 580.159.03 --min-cuda 12.8 --apply --confirm-plan <sha256>

# Atomic NvidiaCleanup (ordered OpenShift teardown per NVIDIA clean-up.html)
nvidia_gpu_operator.py cleanup --scope operator --live-plan
```

## Progress

- [x] (DEC-CI17-01 to DEC-CI17-16) Design decisions grounded in official NVIDIA GPU Operator OpenShift docs and live
      read-only OpenShift cluster observations.
- [x] (CI17-P1) Phase document, this ExecPlan and the plan index row are written.
- [ ] (CI17-M1) Operator-approved result schema (`nvidia-gpu-operator.schema.json`), shared discovery/preflight/wait
      helpers, `NvidiaHealthCheck`, unit tests and live read-only proof.
- [ ] (CI17-M2) `NvidiaPrerequisite`, `NvidiaClusterPolicy`, `NvidiaDriverPool`, `NvidiaEnableMonitoring` with unit
      and live `--live-plan` proofs.
- [ ] (CI17-M3) `NvidiaEnableFeatureDiscovery`, `NvidiaContainerToolkit`, `NvidiaMachineConfig`, `NvidiaTimeSlicing`,
      `NvidiaMachineSetLabel` with unit and live `--live-plan` proofs.
- [ ] (CI17-M4) `NvidiaCudaUpgrade(node, driver)`, `NvidiaUpgradePolicy`, `NvidiaCleanup` with unit and live proofs.
- [ ] (CI17-M5) Chain combos, skill references, golden-path live evidence and renewed skill-digest receipts.

## Surprises & Discoveries

These observations come from read-only reads of the operator's OpenShift cluster on `2026-11-22`:

- Observation: `gpu-operator-certified` is installed on a non-default channel while a newer default channel exists.
  Evidence: Subscription `gpu-operator-certified` in `nvidia-gpu-operator` tracks channel `v26.3` from
  `certified-operators` with CSV `gpu-operator-certified.v26.3.3` (`Succeeded`); PackageManifest default channel is
  `v26.7` (`gpu-operator-certified.v26.7.3`), supporting `OwnNamespace` and `SingleNamespace`. `v26.3` ships
  `ClusterPolicy` and `NVIDIADriver` in `alm-examples`; `v26.7` also ships `GPUCluster`. Effect: DEC-CI17-02.
- Observation: `ClusterPolicy` CRD uses `/spec/gfd/enabled`, not `/spec/gfd/enable`.
  Evidence: Live `crd/clusterpolicies.nvidia.com` OpenAPI v3 schema lists `spec.gfd` keys as
  `args, enabled, env, hostNetwork, image, imagePullPolicy, imagePullSecrets, repository, resources, version`;
  `spec.toolkit` keys include `enabled, image, installDir, repository, version`; `spec.cdi` keys are
  `default, enabled, nriPluginEnabled`. Older doc snippets showing `/spec/gfd/enable` fail schema validation on v1.
  Effect: DEC-CI17-09, DEC-CI17-10.
- Observation: OpenShift `MachineConfig` already carries NVIDIA host configuration on this cluster.
  Evidence: `machineconfig.machineconfiguration.openshift.io/99-worker-nvidia-mok-key` is active alongside
  `ClusterPolicy` `spec.operator.use_ocp_driver_toolkit: true`, `spec.operator.runtimeClass: nvidia`, and
  `spec.cdi.enabled: true`. Host MCO state and GPU Operator CRD state must move hand-in-hand. Effect: DEC-CI17-08.
- Observation: `ClusterPolicy` (`gpu-cluster-policy`, `status.state: ready`) sets `spec.driver.useNvidiaDriverCRD: true`
  and delegates driver management to four per-pool `NVIDIADriver` (`nvidia.com/v1alpha1`) CRs.
  Evidence: Active `NVIDIADriver` CRs target distinct node pools via `nodeSelector` with mixed repositories, images,
  precompiled flags, and driver versions (three pools on `nvcr.io/nvidia/driver` with `usePrecompiled: false` across
  `570.211.01` and `580.159.03`, and one pool on a custom precompiled driver image at `570.158.01` with
  `usePrecompiled: true`). Effect: DEC-CI17-04, DEC-CI17-05.
- Observation: Real GPU nodes exhibit desired-vs-observed driver drift and active upgrade-controller states/skip flags.
  Evidence: Nodes span `NVIDIA-L4`, `NVIDIA-H100-NVL`, and `NVIDIA-H200-NVL` (all reporting
  `nvidia.com/cuda.runtime-version.full=12.8`). One worker pool node ran `570.211.01` while its governing
  `NVIDIADriver` CR desired `580.159.03`; two edge nodes carried `nvidia.com/gpu-driver-upgrade.skip=true` alongside
  `nvidia.com/gpu-driver-upgrade-state=upgrade-failed` and `upgrade-required`. Effect: DEC-CI17-06, DEC-CI17-07.
- Observation: `spec.driver.useOpenKernelModules` is deprecated upstream in favor of `spec.driver.kernelModuleType`
  (`auto`, `open`, `proprietary`), which the live `ClusterPolicy` and `NVIDIADriver` CRs already use.
  Effect: DEC-CI17-05.

## Decision Log

- Decision DEC-CI17-01: Prefix every NVIDIA GPU Operator atomic `K8SCallback` with `Nvidia*` (`NvidiaPrerequisite`,
  `NvidiaClusterPolicy`, `NvidiaDriverPool`, `NvidiaHealthCheck`, `NvidiaCudaUpgrade`, `NvidiaContainerToolkit`,
  `NvidiaEnableFeatureDiscovery`, `NvidiaEnableMonitoring`, `NvidiaTimeSlicing`, `NvidiaMachineSetLabel`,
  `NvidiaMachineConfig`, `NvidiaUpgradePolicy`, `NvidiaCleanup`), backed by shared helpers (`discovery.py`,
  `preflight.py`, `waits.py`) and composed into chain combos in `chains.py`.
  Status: ACCEPTED
  Rationale: Clear, uniform abstraction where every NVIDIA OpenShift doc procedure maps 1:1 to a reusable callback.
- Decision DEC-CI17-02: Do not duplicate OLM installation or removal code. Reuse CI16's `olm_callbacks` and
  `olm_resources` for `nfd` (`openshift-nfd`) and `gpu-operator-certified` (`nvidia-gpu-operator`).
  Status: ACCEPTED
  Rationale: One canonical OLM owner per repository standards.
- Decision DEC-CI17-03: `NvidiaCudaUpgrade(node, driver)` accepts `node="*"` (cluster-wide) or a specific node name /
  selector, plus target `driver` version tag (and optional `repository`, `image`, `kernel_module_type`,
  `use_precompiled`, `min_cuda`). Host CUDA capability is controlled by the driver container version and verified
  post-rollout via `nvidia.com/cuda.driver-version.full` and `nvidia.com/cuda.runtime-version.full`.
  Status: ACCEPTED
  Rationale: Single coherent callback for cluster-wide and node/pool CUDA upgrades.
- Decision DEC-CI17-04: Fail closed when `ClusterPolicy.spec.driver.useNvidiaDriverCRD` is `true` and
  `NvidiaCudaUpgrade` targets a driver update without `--nvidia-driver <name>` (or an unambiguous pool match for the
  requested `node`). Emit `BLOCKED` with `need: nvidia_driver_crd_required`.
  Status: ACCEPTED
  Rationale: Patching `ClusterPolicy.spec.driver.version` while `useNvidiaDriverCRD: true` is active does not upgrade
  `NVIDIADriver`-managed node pools.
- Decision DEC-CI17-05: `NvidiaClusterPolicy` and `NvidiaDriverPool` support `repository`, `image`, `version`,
  `imagePullSecrets`, `licensingConfig` (`secretName`, `nlsEnabled`), `kernelModuleType` (`auto|open|proprietary`),
  and `usePrecompiled`, wiring secrets created or verified by `NvidiaPrerequisite`.
  Status: ACCEPTED
  Rationale: Eliminates manual editing of large `clusterpolicy.json` files for private registries and NLS licensing.
- Decision DEC-CI17-06: `NvidiaHealthCheck` performs out-of-the-box OpenShift installation verification matching
  `oc get pods,daemonset -n nvidia-gpu-operator`: verifies every expected DaemonSet (`gpu-feature-discovery`,
  `nvidia-container-toolkit-daemonset`, `nvidia-dcgm`, `nvidia-dcgm-exporter`, `nvidia-device-plugin-daemonset`,
  `nvidia-driver-daemonset-*`, `nvidia-mig-manager`, `nvidia-node-status-exporter`, `nvidia-operator-validator`),
  validator Pods (`nvidia-cuda-validator`, `nvidia-device-plugin-validator` `Completed`, `nvidia-operator-validator`
  `Running`), `gpu-operator` controller Pod, `ClusterPolicy.status.state=ready`, `NVIDIADriver` drift, allocatable
  `nvidia.com/gpu`, and `MachineConfigPool` health.
  Status: ACCEPTED
  Rationale: Gives agents a single deterministic pass/fail sanity check out of the box.
- Decision DEC-CI17-07: `NvidiaCudaUpgrade` blocks by default if any targeted non-skipped node is already in
  `nvidia.com/gpu-driver-upgrade-state=upgrade-failed`, emitting `need: node_upgrade_failed`. Nodes labeled
  `nvidia.com/gpu-driver-upgrade.skip=true` are excluded from the wait set and reported explicitly.
  Status: ACCEPTED
  Rationale: Prevents cascading upgrades over already-broken nodes while respecting operator-pinned skip nodes.
- Decision DEC-CI17-08: `NvidiaContainerToolkit` and `NvidiaMachineConfig` treat `ClusterPolicy` CRD state and
  OpenShift `MachineConfig`/`MachineConfigPool` semantics as a coupled contract (`spec.toolkit`, `spec.cdi`,
  `spec.operator.runtimeClass=nvidia`, `use_ocp_driver_toolkit`, `RuntimeClass/nvidia`, and MCP `Updated=True`,
  `Updating=False`, `Degraded=False`).
  Status: ACCEPTED
  Rationale: On OpenShift, CRI-O runtime hooks, CDI, MOK signing keys, and kernel args are governed by MCO.
- Decision DEC-CI17-09: `NvidiaEnableFeatureDiscovery` verifies NFD (`NodeFeatureDiscovery` + `pci-10de.present=true`),
  patches `ClusterPolicy` `/spec/gfd/enabled` (verified against the live CRD OpenAPI schema), and reads back GFD
  DaemonSet readiness plus `nvidia.com/gpu.product` and `nvidia.com/cuda.*` node labels.
  Status: ACCEPTED
  Rationale: Aligns NVIDIA's "Enabling GPU Feature Discovery" procedure with live CRD v1 schema enforcement.
- Decision DEC-CI17-10: Every mutating callback defaults to `ActionMode.LIVE_PLAN` (read-only cluster read,
  Kubernetes `--dry-run=server` against the live CRD schema, exact before/after diff, `plan_digest`), writes only on
  `--apply --confirm-plan <sha256>`, and executes independent post-write read-back.
  Status: ACCEPTED
  Rationale: Matches CI16 and repository safety contracts; driver upgrades and `MachineConfig` changes can drain or
  reboot OpenShift nodes.
- Decision DEC-CI17-11: Every Kubernetes API read/write uses qualified resource names:
  `clusterpolicies.nvidia.com`, `nvidiadrivers.nvidia.com`, `nodefeaturediscoveries.nfd.openshift.io`,
  `machinesets.machine.openshift.io`, `machineconfigs.machineconfiguration.openshift.io`,
  `machineconfigpools.machineconfiguration.openshift.io`, `runtimeclasses.node.k8s.io`, plus CI16's qualified OLM
  resources.
  Status: ACCEPTED
  Rationale: Avoids bare-kind API-group ambiguity (same rule as DEC-CI16-13).
- Decision DEC-CI17-12: `NvidiaCleanup` follows NVIDIA's official OpenShift [Cleanup][nvidia-ocp-cleanup] order:
  delete `NVIDIADriver` CRs → delete `ClusterPolicy` (wait for operand DaemonSets to terminate) → remove OLM
  `Subscription` and `CSV` (reusing CI16 `remove`) → optional `OperatorGroup`/namespace cleanup → optional CRD deletion
  (`clusterpolicies.nvidia.com`, `nvidiadrivers.nvidia.com`) **only** when `--delete-crds` is explicitly passed and
  confirmed in `plan_digest`. Independent read-back verifies absence at the requested scope.
  Status: ACCEPTED
  Rationale: Matches NVIDIA OpenShift cleanup semantics while preventing accidental cluster-wide CRD loss.
- Decision DEC-CI17-13: `NvidiaTimeSlicing` creates or updates a multi-profile time-slicing `ConfigMap` (supporting
  NVIDIA's documented `replicas`, `renameByDefault`, and `failRequestsGreaterThanOne` fields — e.g., `small=2`,
  `medium=5`, `large=10`), binds `ClusterPolicy.spec.devicePlugin.config`, and composes with `NvidiaMachineSetLabel`
  to patch `machinesets.machine.openshift.io` in `openshift-machine-api` at
  `/spec/template/spec/metadata/labels/nvidia.com~1device-plugin.config` (or targets specific Nodes directly).
  Status: ACCEPTED
  Rationale: Ensures autoscaled OpenShift GPU MachineSets inherit the time-slicing profile automatically.
- Decision DEC-CI17-14: `NvidiaPrerequisite` creates private registry `docker-registry` pull Secrets strictly inside
  the GPU Operator namespace from CI16 `[registry.<name>]` target credential files (never CLI password flags or
  cluster-wide pull secret edits), and creates NLS `licensingConfig` Secrets from a caller-supplied license file path.
  Status: ACCEPTED
  Rationale: Reuses CI16's credential contract (DEC-CI16-18) with zero secret leaks.
- Decision DEC-CI17-15: `NvidiaEnableMonitoring` applies `openshift.io/cluster-monitoring=true` on the GPU Operator
  namespace, ensures `ClusterPolicy.spec.dcgmExporter.serviceMonitor.enabled: true`, and verifies the label and
  `dcgm-exporter` ServiceMonitor read-back.
  Status: ACCEPTED
  Rationale: Grounds OpenShift cluster monitoring / Console GPU metrics in a single atomic callback.
- Decision DEC-CI17-16: Result JSON contract (`ci-skills/schemas/results/nvidia-gpu-operator.schema.json`) and
  `tools.json` updates are proposed and approved explicitly by the operator in `CI17-M1` before any JSON or contract
  file is written.
  Status: ACCEPTED
  Rationale: Complies strictly with the repository's contract and JSON approval lock.

## Context and Orientation

Code lives under `ci-skills/lib/python/core/kubernetes/nvidia_gpu/` with a thin CLI entrypoint at
`ci-skills/bin/nvidia_gpu_operator.py`. Callbacks inherit `K8SCallback` (`core/kubernetes/kubernetes.py`), share
`KubernetesService`, `K8SAuth`, and `WorkflowExecutor` (`core/executor.py`), and hold zero cluster-specific values.

## Atomic `Nvidia*` Callback Specifications

All 13 atomic callbacks live in `callbacks.py` and delegate low-level reads, server-side dry-runs, confirmation, and
waits to shared modules (`discovery.py`, `preflight.py`, `waits.py`):

1. `NvidiaPrerequisite`: Checks NFD readiness; creates/verifies `docker-registry` image pull Secret (from
   `[registry.<name>]`) and optional NLS `licensing-config` Secret in `nvidia-gpu-operator`.
2. `NvidiaClusterPolicy`: Creates or patches `ClusterPolicy` (`gpu-cluster-policy`), wiring driver `repository`,
   `image`, `version`, `imagePullSecrets`, `licensingConfig` (`secretName`, `nlsEnabled`), `kernelModuleType`,
   `usePrecompiled`, and `useNvidiaDriverCRD`.
3. `NvidiaDriverPool`: Creates or patches named `NVIDIADriver` CRs (`nodeSelector`, `repository`, `image`, `version`,
   `imagePullSecrets`, `kernelModuleType`, `usePrecompiled`).
4. `NvidiaHealthCheck(node="*")`: Out-of-the-box sanity check verifying `gpu-operator` controller Pod, all expected
   DaemonSets (`DESIRED == CURRENT == READY == UP-TO-DATE == AVAILABLE`, allowing `0/0` where hardware is absent such
   as `nvidia-mig-manager` on non-MIG GPUs), validator Pods (`nvidia-cuda-validator`, `nvidia-device-plugin-validator`
   `Completed`; `nvidia-operator-validator` `Running`), `ClusterPolicy.status.state=ready`, `NVIDIADriver` desired vs
   observed `driver_drift`, allocatable `nvidia.com/gpu`, and `MachineConfigPool` health.
5. `NvidiaCudaUpgrade(node, driver)`: Cluster (`node="*"`) or node/pool CUDA/driver upgrade. Resolves `ClusterPolicy`
   vs `NVIDIADriver`, preflights `upgrade-failed`/`skip`, dry-runs and applies patch, tracks
   `nvidia.com/gpu-driver-upgrade-state` through `upgrade-done`, and verifies `nvidia.com/cuda.{driver,runtime}-*`.
6. `NvidiaContainerToolkit`: Aligns `ClusterPolicy.spec.toolkit.*`, `spec.cdi.*`, `spec.operator.runtimeClass=nvidia`,
   `use_ocp_driver_toolkit`, `RuntimeClass/nvidia`, and worker `MachineConfigPool` state.
7. `NvidiaEnableFeatureDiscovery`: Verifies NFD, patches `/spec/gfd/enabled` (CRD-verified), waits for GFD DaemonSet,
   and verifies `nvidia.com/gpu.product` and `nvidia.com/cuda.*` labels.
8. `NvidiaEnableMonitoring`: Labels `ns/<namespace> openshift.io/cluster-monitoring=true` and enables
   `ClusterPolicy.spec.dcgmExporter.serviceMonitor.enabled: true`.
9. `NvidiaTimeSlicing`: Creates/updates a multi-profile time-slicing `ConfigMap` (`replicas`, `renameByDefault`,
   `failRequestsGreaterThanOne`, e.g., `small=2`, `medium=5`, `large=10`) and binds
   `ClusterPolicy.spec.devicePlugin.config.{name,default}`.
10. `NvidiaMachineSetLabel`: Patches `machinesets.machine.openshift.io` in `openshift-machine-api` under
    `/spec/template/spec/metadata/labels` for validated NVIDIA labels (`nvidia.com/device-plugin.config`,
    `nvidia.com/mig.config`, `nvidia.com/gpu.deploy.driver`, `nvidia.com/gpu-driver-upgrade.skip`, or declared
    `NVIDIADriver` selector keys).
11. `NvidiaMachineConfig`: Plans/verifies NVIDIA OpenShift `MachineConfig` (MOK signing key, IOMMU kernel args,
    CRI-O/CDI) and waits for `MachineConfigPool` convergence (`Updated=True`, `Updating=False`, `Degraded=False`).
12. `NvidiaUpgradePolicy`: Patches `upgradePolicy` (`autoUpgrade`, `maxParallelUpgrades`, `maxUnavailable`, `drain`)
    and manages per-node `gpu-driver-upgrade.skip` and `gpu-driver-upgrade-state=upgrade-required` recovery labels.
13. `NvidiaCleanup`: Ordered teardown per NVIDIA OpenShift `clean-up.html` (`NVIDIADriver` → `ClusterPolicy` → CI16
    OLM remove → optional namespace → explicit `--delete-crds` opt-in) with absence read-back.

### Chain Composition (`chains.py`)

Chain combos compose strictly from `Nvidia*` callbacks and CI16 OLM callbacks via `WorkflowExecutor`:

- `install-gpu-operator`: `[K8SAuth, *ci16_nfd, NvidiaPrerequisite, *ci16_gpu_operator, NvidiaClusterPolicy,
  NvidiaEnableMonitoring, NvidiaContainerToolkit, NvidiaEnableFeatureDiscovery, NvidiaHealthCheck]`
- `upgrade-cuda`: `[K8SAuth, NvidiaHealthCheck, *(optional NvidiaUpgradePolicy), NvidiaCudaUpgrade(node, driver),
  NvidiaHealthCheck]`
- `enable-time-slicing`: `[K8SAuth, NvidiaHealthCheck, NvidiaTimeSlicing, *(optional NvidiaMachineSetLabel),
  NvidiaHealthCheck]`
- `align-toolkit-ocp`: `[K8SAuth, NvidiaHealthCheck, NvidiaMachineConfig, NvidiaContainerToolkit, NvidiaHealthCheck]`
- `recover-failed-upgrade`: `[K8SAuth, NvidiaHealthCheck, NvidiaUpgradePolicy(retry_failed=True),
  NvidiaCudaUpgrade(wait_only=True), NvidiaHealthCheck]`
- `cleanup-gpu-operator`: `[K8SAuth, NvidiaHealthCheck, NvidiaCleanup, NvidiaHealthCheck]`

## Verification Gates

| Gate ID | Scope | Proof Required |
| --- | --- | --- |
| `G-CI17-01` | Unit & contracts | Unit tests per `Nvidia*` callback, CRD guard, MCO/MachineSet, `NO_OP`, drift |
| `G-CI17-02` | Live health | Installed `NvidiaHealthCheck` returns `PASS` with Pods, DaemonSets, CRD, MCO, drift |
| `G-CI17-03` | Live `--live-plan` | Server-side dry-run + `plan_digest` with `mutated: false` for mutating atomics |
| `G-CI17-04` | Atomic write/restore | Authorized reversible atomic write verified by read-back and restored cleanly |
| `G-CI17-05` | Chain combo | Installed combo verified end-to-end with renewed candidate acceptance receipts |

## Interfaces and Dependencies

No new third-party Python dependency is introduced. All cluster operations go through `KubernetesService` (`kubectl`)
and CI16's `olm_callbacks`/`olm_resources`.

Planned module layout under `ci-skills/lib/python/core/kubernetes/nvidia_gpu/`:

- `models.py`: Qualified resource constants (`CLUSTER_POLICIES`, `NVIDIA_DRIVERS`, `NODE_FEATURE_DISCOVERIES`,
  `MACHINE_SETS`, `MACHINE_CONFIGS`, `MACHINE_CONFIG_POOLS`, `RUNTIME_CLASSES`), `GPUOperatorAction`,
  `GPUOperatorState`, `DriverPoolInfo`, `GPUNodeInfo`, `DaemonSetHealth`, `GPUNeed`, and `GPU_OPERATOR_MODE_POLICY`.
- `discovery.py`: Shared reads for OLM, `ClusterPolicy`, `NVIDIADriver`, `MachineSet`, MCO/MCP, GPU nodes, DaemonSets,
  and validator Pods.
- `preflight.py`: Shared contract validation, `--dry-run=server` diffing, and `plan_digest` confirmation.
- `waits.py`: Shared polling for `ClusterPolicy`/`NVIDIADriver` readiness, `gpu-driver-upgrade-state`,
  `MachineConfigPool` convergence, DaemonSet rollouts, and CUDA/validator read-back.
- `callbacks.py`: The 13 `Nvidia*` atomic callbacks.
- `chains.py`: Callback-list builders for standalone atomic verbs and composed chain combos.
- `cli.py`: `build_parser`, `workflow`, `NvidiaGPUOperator`, `main`, invoked by `ci-skills/bin/nvidia_gpu_operator.py`.

## Outcomes & Retrospective

Not yet implemented. Updated at the completion of each milestone (`CI17-M1` through `CI17-M5`).

[phase]: ../../phases/CI17-NVIDIA-GPU-OPERATOR.md
[ci16-exec]: ci16-olm-operator.md
[nvidia-ocp-docs]: https://docs.nvidia.com/datacenter/cloud-native/openshift/latest/index.html
[nvidia-ocp-cleanup]: https://docs.nvidia.com/datacenter/cloud-native/openshift/latest/clean-up.html
