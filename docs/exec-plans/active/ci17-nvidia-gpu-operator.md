# NVIDIA GPU Operator atomic `Nvidia*` callbacks and staged chain combos on OpenShift

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and
`Outcomes & Retrospective` must be kept up to date as work proceeds. It is maintained in accordance with
[docs/PLANS.md](../../PLANS.md) and delivers phase [CI17][phase]. Source baseline: `1ad150e`.

## Purpose / Big Picture

[CI16][ci16-exec] delivers generic OLM operator enablement (`ocp_operator.py`) using the NVIDIA Network Operator as its
initial consumer. Any OpenShift cluster running GPU workloads also requires NVIDIA GPU Operator
(`gpu-operator-certified`) lifecycle automation grounded in the official
[NVIDIA GPU Operator on OpenShift documentation][nvidia-ocp-docs] — including [Install][nvidia-ocp-install],
[DRA (`GPUCluster`)][nvidia-ocp-dra], [Precompiled Drivers][nvidia-ocp-precompiled],
[GPU Monitoring Dashboard][nvidia-ocp-dashboard], [Driver Upgrades][nvidia-driver-upgrades], and
[Cleanup][nvidia-ocp-cleanup] — plus OpenShift-specific operational realities: Secure Boot / MOK `MachineConfig`
coordination, pausing/resuming complex operator or `MachineConfigPool` reconciliation, non-standard precompiled driver
health checks, `MachineSet` label propagation, and OpenShift Console dashboard integration. This design is public and
cluster-neutral for any OpenShift user; zero cluster-specific values are hardcoded.

This plan builds **16 atomic, `Nvidia*`-prefixed callbacks first**, then composes **staged confirmed chain combos**
from those callbacks plus CI16's OLM callbacks via `WorkflowExecutor`:

- `NvidiaHealthCheck` — out-of-the-box sanity check covering standard and non-standard/precompiled drivers, Secure Boot
  / MOK `MachineConfig`, `MachineConfigPool` state, all DaemonSets and validator Pods, DRA `DeviceClass` /
  `ResourceSlice` coverage, paused/skipped nodes, and desired-vs-observed driver drift.
- `NvidiaPauseOperator` — symmetric pause/resume at node scope (`nvidia.com/gpu.deploy.operands=false`,
  `gpu.deploy.driver=false`, per-operand labels, `gpu-driver-upgrade.skip=true`), policy scope
  (`upgradePolicy.autoUpgrade: false`), or OpenShift MCO scope (`MachineConfigPool.spec.paused: true`).
- `NvidiaPrerequisite` — NFD readiness check, private registry `docker-registry` pull Secret (from CI16
  `[registry.<name>]`), and optional NLS `licensingConfig` Secret.
- `NvidiaClusterPolicy` — Device Plugin `ClusterPolicy` create/patch (mutually exclusive with `GPUCluster`).
- `NvidiaDraCluster` — DRA `GPUCluster` create/patch + `gpu.nvidia.com` `DeviceClass` / `ResourceSlice` verification
  (mutually exclusive with `ClusterPolicy`; GPU Operator `v26.7+`, OpenShift `4.21+`, validated `4.22`).
- `NvidiaDriverPool` — per-pool `NVIDIADriver` CRs (stock DTK or custom/non-standard precompiled images).
- `NvidiaCudaUpgrade(node, driver)` — fleet-aware driver/CUDA upgrade; resolves `node="*"` to full fleet inventory and
  applies **one explicitly selected governing driver CR per confirmed apply** (no automatic rollback).
- `NvidiaContainerToolkit` — `spec.toolkit` + `spec.cdi` + `runtimeClass: nvidia` aligned with OCP CRI-O & MCP.
- `NvidiaMachineConfig` — separately confirmed OpenShift `MachineConfig` (MOK/Secure Boot, IOMMU, CRI-O/CDI) proving
  rendered MCP convergence before dependent GPU work.
- `NvidiaEnableFeatureDiscovery` — NFD prerequisite + CRD-verified `/spec/gfd/enabled` patch + label read-back.
- `NvidiaEnableMonitoring` — `openshift.io/cluster-monitoring=true` namespace label + DCGM `ServiceMonitor`.
- `NvidiaEnableConsole` — OpenShift Console GPU monitoring dashboard integration (separate from metrics collection).
- `NvidiaTimeSlicing` — multi-profile time-slicing `ConfigMap` (`small=2`, `medium=5`, `large=10`, `renameByDefault`,
  `failRequestsGreaterThanOne`) + `spec.devicePlugin.config` binding.
- `NvidiaMachineSetLabel` — patches `machinesets.machine.openshift.io` in `openshift-machine-api` template labels.
- `NvidiaUpgradePolicy` — `upgradePolicy` (`autoUpgrade`, `maxParallelUpgrades`, `drain`) + separately confirmed
  failed-node retry (`nvidia.com/gpu-driver-upgrade-state=upgrade-required`).
- `NvidiaCleanup` — mode-specific OpenShift teardown (`clean-up.html` for Device Plugin; `dra-gpu-ocp.html#uninstall`
  for DRA; `--delete-crds` explicit opt-in only).

MIG profile mutation workflows (`mig-ocp.html`) and MPS are explicitly deferred as low priority; `NvidiaHealthCheck`
still reports observed MIG labels read-only.

Example golden-path commands after delivery:

```bash
# Out-of-the-box NvidiaHealthCheck (standard + non-standard driver, MOK/MCP, DRA, drift)
nvidia_gpu_operator.py health-check --json

# Pause a Secure Boot node or MCP before non-standard driver / MOK maintenance
nvidia_gpu_operator.py pause --node <node> --scope upgrade-skip --live-plan
nvidia_gpu_operator.py pause --mcp worker --live-plan

# Atomic NvidiaEnableFeatureDiscovery (CRD-verified /spec/gfd/enabled)
nvidia_gpu_operator.py enable-gfd --enabled true --live-plan

# Atomic NvidiaTimeSlicing + NvidiaMachineSetLabel
nvidia_gpu_operator.py time-slicing --config-name time-slicing-config \
  --profile small:2 --profile medium:5 --profile large:10 \
  --machineset <machineset-name> --machineset-profile small --live-plan

# Atomic NvidiaCudaUpgrade (resolves fleet; 1 governing driver CR per confirmed apply)
nvidia_gpu_operator.py cuda-upgrade --node "*" --nvidia-driver <cr-name> \
  --driver 580.159.03 --min-cuda 12.8 --live-plan
nvidia_gpu_operator.py cuda-upgrade --node "*" --nvidia-driver <cr-name> \
  --driver 580.159.03 --min-cuda 12.8 --apply --confirm-plan <sha256>

# Mode-specific NvidiaCleanup (Device Plugin or DRA)
nvidia_gpu_operator.py cleanup --scope operator --live-plan
```

## Progress

- [x] (DEC-CI17-01 to DEC-CI17-22) Design decisions grounded in official NVIDIA GPU Operator OpenShift docs, live
      read-only OpenShift observations, multi-model brainstorm, and operator priority decisions.
- [x] (CI17-P1) Phase document, this ExecPlan and the plan index row are written.
- [ ] (CI17-M1) Operator-approved result schema (`nvidia-gpu-operator.schema.json`), shared discovery/preflight/wait
      helpers, `NvidiaHealthCheck`, and `NvidiaPauseOperator` with unit and live proofs.
- [ ] (CI17-M2) `NvidiaPrerequisite`, `NvidiaClusterPolicy`, `NvidiaDraCluster`, and `NvidiaDriverPool` (aligned with
      CI16-M3 shared creation path; pre-provisioned operations runnable independently if CI16-M3 slips).
- [ ] (CI17-M3) `NvidiaEnableFeatureDiscovery`, `NvidiaEnableMonitoring`, `NvidiaEnableConsole`,
      `NvidiaContainerToolkit`, `NvidiaMachineConfig`, `NvidiaTimeSlicing`, and `NvidiaMachineSetLabel`.
- [ ] (CI17-M4) `NvidiaCudaUpgrade` (one governing CR per apply), `NvidiaUpgradePolicy`, and mode-specific
      `NvidiaCleanup` (tied to CI16-M4 for full operator teardown).
- [ ] (CI17-M5) Staged chain combos, `ci-skills/references/nvidia-gpu-operator.md`, live evidence and renewed receipts.

## Surprises & Discoveries

These observations come from read-only reads of an OpenShift cluster on `2026-11-22` and a multi-model brainstorm:

- Observation: `gpu-operator-certified` can run on a non-default channel while a newer channel introduces `GPUCluster`.
  Evidence: Subscription `gpu-operator-certified` tracked channel `v26.3` (`gpu-operator-certified.v26.3.3`) while
  PackageManifest default channel was `v26.7` (`v26.7.3`), which adds `GPUCluster` alongside `ClusterPolicy` and
  `NVIDIADriver` in `alm-examples`. Effect: DEC-CI17-02, DEC-CI17-16.
- Observation: `ClusterPolicy` CRD uses `/spec/gfd/enabled`, not `/spec/gfd/enable`.
  Evidence: Live `crd/clusterpolicies.nvidia.com` OpenAPI v3 schema lists `spec.gfd.enabled`, `spec.toolkit.*`,
  `spec.cdi.{default,enabled,nriPluginEnabled}`, and `spec.driver.kernelModuleType`. Older doc snippets showing
  `/spec/gfd/enable` fail schema validation on v1. Effect: DEC-CI17-09, DEC-CI17-10.
- Observation: OpenShift clusters with Secure Boot or custom/out-of-tree precompiled drivers require coordinated
  `MachineConfig` (MOK keys), `NVIDIADriver` CRs, and explicit pause/skip controls.
  Evidence: Live cluster held `machineconfig/99-worker-nvidia-mok-key`, `ClusterPolicy` with
  `useNvidiaDriverCRD: true`, four `NVIDIADriver` CRs mixing `nvcr.io/nvidia/driver` (`usePrecompiled: false`) and a
  custom precompiled driver image (`usePrecompiled: true`), active desired-vs-observed driver drift, and nodes carrying
  `nvidia.com/gpu-driver-upgrade.skip=true` alongside `upgrade-failed` and `upgrade-required`. Effect: DEC-CI17-04,
  DEC-CI17-06, DEC-CI17-19, DEC-CI17-20.
- Observation: `WorkflowExecutor` runs every callback's `cleanup()` hook after *both* success and failure
  (`ci-skills/lib/python/core/abstract/executor.py:139`).
  Evidence: Verified from repository source during multi-model brainstorm. Effect: DEC-CI17-21.

## Decision Log

- Decision DEC-CI17-01: Prefix every NVIDIA GPU Operator atomic `K8SCallback` with `Nvidia*` (16 callbacks total),
  backed by shared helpers (`discovery.py`, `preflight.py`, `waits.py`) and composed into staged chain combos in
  `chains.py`.
  Status: ACCEPTED
  Rationale: Clear, uniform abstraction where every NVIDIA OpenShift procedure maps 1:1 to a reusable callback.
- Decision DEC-CI17-02: Do not duplicate OLM installation or removal code. Reuse CI16's `olm_callbacks` and
  `olm_resources` for `nfd` (`openshift-nfd`) and `gpu-operator-certified` (`nvidia-gpu-operator`).
  Status: ACCEPTED
  Rationale: One canonical OLM owner per repository standards.
- Decision DEC-CI17-03: `NvidiaCudaUpgrade(node, driver)` accepts `node="*"` (entire fleet) or a specific node /
  selector, resolves every governing driver resource (surfacing overlaps, unmatched nodes, and pool-wide blast radius),
  and limits each confirmed apply in `CI17-M4` to **one explicitly selected governing driver CR** (`ClusterPolicy` or
  named `NVIDIADriver`). No automatic driver rollback: reverting a driver spec triggers a second disruptive rollout, so
  recovery is always a separately confirmed operator action.
  Status: ACCEPTED
  Rationale: Prevents uncoordinated multi-pool disruption while still giving full fleet visibility before confirmation.
- Decision DEC-CI17-04: Fail closed when `ClusterPolicy.spec.driver.useNvidiaDriverCRD` is `true` and
  `NvidiaCudaUpgrade` targets a driver update without `--nvidia-driver <name>` (or an unambiguous single pool match).
  Emit `BLOCKED` with `need: nvidia_driver_crd_required`.
  Status: ACCEPTED
  Rationale: Patching `ClusterPolicy.spec.driver.version` while `useNvidiaDriverCRD: true` is active does not upgrade
  `NVIDIADriver`-managed node pools.
- Decision DEC-CI17-05: `NvidiaClusterPolicy` and `NvidiaDriverPool` support `repository`, `image`, `version`,
  `imagePullSecrets`, `licensingConfig` (`secretName`, `nlsEnabled`), `kernelModuleType` (`auto|open|proprietary`),
  `kernelModuleConfig`, and `usePrecompiled`, wiring secrets from `NvidiaPrerequisite`.
  Status: ACCEPTED
  Rationale: Supports both stock DTK builds (`nvcr.io/nvidia/driver`) and custom/non-standard precompiled images.
- Decision DEC-CI17-06: `NvidiaHealthCheck` performs out-of-the-box OpenShift verification for both standard and
  non-standard setups: `gpu-operator` controller Pod, all expected DaemonSets (`DESIRED == CURRENT == READY ==
  UP-TO-DATE == AVAILABLE`, allowing `0/0` where hardware is absent), validator Pods (`nvidia-cuda-validator`,
  `nvidia-device-plugin-validator` `Completed`; `nvidia-operator-validator` `Running`), `ClusterPolicy` or `GPUCluster`
  state, DRA controller/kubelet plugin + `gpu.nvidia.com` `DeviceClass` + `ResourceSlice` coverage (in DRA mode),
  non-standard driver signals (custom image/repository, `usePrecompiled` vs `use_ocp_driver_toolkit`, RHCOS kernel /
  `OSTREE_VERSION` alignment, `DriverNotReady` / Secure Boot MOK errors), paused/skipped nodes, `driver_drift`,
  allocatable `nvidia.com/gpu`, and `MachineConfigPool` (`Paused`, `Updating`, `Degraded`) state.
  Status: ACCEPTED
  Rationale: Captures the real failure modes of heterogeneous, Secure Boot, and precompiled OpenShift GPU fleets.
- Decision DEC-CI17-07: `NvidiaCudaUpgrade` blocks by default on unskipped `upgrade-failed` nodes, emitting
  `need: node_upgrade_failed`. By contrast, `recover-failed-upgrade` does **not** run a self-blocking healthy precheck:
  it reports the failed state first, then applies a separately confirmed retry once the underlying cause is resolved.
  Status: ACCEPTED
  Rationale: Avoids blocking the recovery entry point while still protecting routine upgrades.
- Decision DEC-CI17-08: `NvidiaContainerToolkit` and `NvidiaMachineConfig` treat `ClusterPolicy` CRD state and
  OpenShift `MachineConfig`/`MachineConfigPool` semantics as a coupled contract. `NvidiaMachineConfig` changes are
  always confirmed separately, and rendered MCP convergence (`Updated=True`, `Updating=False`, `Degraded=False`) is
  proven before dependent GPU work runs.
  Status: ACCEPTED
  Rationale: On OpenShift, `MachineConfig` changes trigger rolling node reboots and must not race driver rollouts.
- Decision DEC-CI17-09: `NvidiaEnableFeatureDiscovery` verifies NFD (`NodeFeatureDiscovery` + `pci-10de.present=true`),
  patches `ClusterPolicy` `/spec/gfd/enabled` (verified against live CRD OpenAPI schema), and reads back GFD DaemonSet
  readiness plus `nvidia.com/gpu.product` and `nvidia.com/cuda.*` labels.
  Status: ACCEPTED
  Rationale: Aligns NVIDIA's GFD procedure with live CRD v1 schema enforcement.
- Decision DEC-CI17-10: Use **staged confirmed plans** (Option A). Every mutating stage defaults to
  `ActionMode.LIVE_PLAN`, runs `--dry-run=server`, hashes target identity + resolved release/mode + ordered actions +
  UIDs/expected absence + affected selectors/nodes + before-values + proposed changes into `plan_digest`, recomputes
  preconditions before apply (blocking on drifted target, changed node set, or degraded MCP), uses guarded patches on
  existing objects, and runs independent post-write read-back.
  Status: ACCEPTED
  Rationale: Fits `WorkflowExecutor`'s sequential model and guarantees every confirmation covers server-validatable API
  writes.
- Decision DEC-CI17-11: Every Kubernetes API read/write uses qualified resource names:
  `clusterpolicies.nvidia.com`, `gpuclusters.nvidia.com`, `nvidiadrivers.nvidia.com`,
  `nodefeaturediscoveries.nfd.openshift.io`, `machinesets.machine.openshift.io`,
  `machineconfigs.machineconfiguration.openshift.io`, `machineconfigpools.machineconfiguration.openshift.io`,
  `runtimeclasses.node.k8s.io`, `deviceclasses.resource.k8s.io`, `resourceslices.resource.k8s.io`, plus CI16's
  qualified OLM resources.
  Status: ACCEPTED
  Rationale: Avoids bare-kind API-group ambiguity (same rule as DEC-CI16-13).
- Decision DEC-CI17-12: `NvidiaCleanup` uses mode-specific teardown sequences grounded in NVIDIA's official OpenShift
  docs:
  - Device Plugin ([Cleanup][nvidia-ocp-cleanup]): remove OLM Subscription/CSV (via CI16-M4), then explicitly confirmed
    `ClusterPolicy` CRD deletion (`--delete-crds` opt-in).
  - DRA ([DRA Uninstall][nvidia-ocp-dra]): confirm GPU workloads and `ResourceClaim`s are cleared → delete
    `NVIDIADriver` and `GPUCluster` CRs → remove OLM Subscription/CSV (via CI16-M4) → explicit `--delete-crds` opt-in.
  CI17 full-teardown acceptance owns its own live read-back (since CI16 plans mock-only package-removal proof).
  Status: ACCEPTED
  Rationale: Matches NVIDIA's documented teardown order per mode and prevents orphaned claims or accidental CRD loss.
- Decision DEC-CI17-13: `NvidiaTimeSlicing` creates/updates a multi-profile time-slicing `ConfigMap` (`replicas`,
  `renameByDefault`, `failRequestsGreaterThanOne`, e.g., `small=2`, `medium=5`, `large=10`), binds
  `ClusterPolicy.spec.devicePlugin.config`, and composes with `NvidiaMachineSetLabel` to patch
  `machinesets.machine.openshift.io` in `openshift-machine-api` at
  `/spec/template/spec/metadata/labels/nvidia.com~1device-plugin.config` (keeping existing Node relabeling explicit).
  Status: ACCEPTED
  Rationale: Separates future autoscaled MachineSet defaults from live existing Node changes.
- Decision DEC-CI17-14: `NvidiaPrerequisite` creates private registry `docker-registry` pull Secrets strictly inside
  the GPU Operator namespace from CI16 `[registry.<name>]` target credential files (never CLI passwords or cluster-wide
  pull secret edits), and creates NLS `licensingConfig` Secrets from a caller-supplied license file path.
  Status: ACCEPTED
  Rationale: Reuses CI16's credential contract (DEC-CI16-18) with zero secret leaks.
- Decision DEC-CI17-15: Keep metrics collection (`NvidiaEnableMonitoring`: `openshift.io/cluster-monitoring=true` +
  DCGM `ServiceMonitor`) separate from OpenShift Console dashboard enablement (`NvidiaEnableConsole`, grounded in
  [GPU Monitoring Dashboard][nvidia-ocp-dashboard]).
  Status: ACCEPTED
  Rationale: Clusters often enable Prometheus scraping independently of Web Console dashboard configuration.
- Decision DEC-CI17-16: Support both Device Plugin mode (`NvidiaClusterPolicy`) and DRA mode (`NvidiaDraCluster` using
  `GPUCluster` + `NVIDIADriver` + `gpu.nvidia.com` `DeviceClass`, per [DRA][nvidia-ocp-dra]). Enforce mutual exclusion
  in preflight (`need: gpu_allocation_mode_conflict`).
  Status: ACCEPTED
  Rationale: NVIDIA GPU Operator `v26.7+` forbids running `ClusterPolicy` and `GPUCluster` concurrently.
- Decision DEC-CI17-17: Pair every `Nvidia*` callback with its official NVIDIA OpenShift documentation URL in
  `ci-skills/references/nvidia-gpu-operator.md` (registered in `reference.py`). Zero site-specific defaults.
  Status: ACCEPTED
  Rationale: `ci-skills` is a public, cluster-neutral skill for any OpenShift user on the internet.
- Decision DEC-CI17-18: Defer MIG profile mutation workflows (`mig-ocp.html`), MPS tuning, and disconnected mirror
  creation to a follow-up phase; CI17 consumes existing cluster mirror configuration and reports MIG labels read-only.
  Status: ACCEPTED
  Rationale: Operator prioritized OpenShift special cases (Secure Boot, MCO, pause/resume, Console, non-standard driver
  health) over MIG/MPS mutations.
- Decision DEC-CI17-19: Deliver `NvidiaPauseOperator` in `CI17-M1` alongside `NvidiaHealthCheck` to provide symmetric,
  confirmed pause and resume across three documented OpenShift surfaces: node operands/upgrades
  (`nvidia.com/gpu.deploy.operands=false`, `gpu.deploy.driver=false`, per-operand labels,
  `gpu-driver-upgrade.skip=true`), policy Upgrade Controller (`upgradePolicy.autoUpgrade: false`), and OpenShift
  `MachineConfigPool` (`spec.paused: true`).
  Status: ACCEPTED
  Rationale: Operators managing Secure Boot or custom/non-standard drivers need safe pause/resume during multi-step
  OpenShift maintenance.
- Decision DEC-CI17-20: Preflight Driver Toolkit (`use_ocp_driver_toolkit`) vs precompiled (`usePrecompiled`) driver
  compatibility against target node RHCOS kernel / `OSTREE_VERSION` before applying `NvidiaCudaUpgrade` or
  `NvidiaDriverPool`, warning or blocking with explicit `needs` when a Secure Boot or precompiled node risks an
  unmatched kernel image.
  Status: ACCEPTED
  Rationale: Directly addresses NVIDIA's OpenShift precompiled-driver and Secure Boot recovery warning.
- Decision DEC-CI17-21: Reserve `K8SCallback.cleanup()` strictly for temporary invocation-owned artifacts; never put
  operator or CR lifecycle teardown inside `K8SCallback.cleanup()`.
  Status: ACCEPTED
  Rationale: `WorkflowExecutor` runs `cleanup()` after both success and failure (`executor.py:139`).
- Decision DEC-CI17-22: Result JSON contract (`ci-skills/schemas/results/nvidia-gpu-operator.schema.json`) and
  `tools.json` updates are proposed and approved explicitly by the operator in `CI17-M1` before any JSON or contract
  file is written.
  Status: ACCEPTED
  Rationale: Complies strictly with the repository's contract and JSON approval lock.

## Context and Orientation

Code lives under `ci-skills/lib/python/core/kubernetes/nvidia_gpu/` with a thin CLI entrypoint at
`ci-skills/bin/nvidia_gpu_operator.py`. Callbacks inherit `K8SCallback` (`core/kubernetes/kubernetes.py`), share
`KubernetesService`, `K8SAuth`, `ResourceSet`, and `WorkflowExecutor` (`core/executor.py`), and hold zero
cluster-specific values.

## Atomic `Nvidia*` Callback Specifications

All 16 atomic callbacks live in `callbacks.py` and delegate low-level reads, server-side dry-runs, confirmation, and
waits to shared modules (`discovery.py`, `preflight.py`, `waits.py`):

1. `NvidiaHealthCheck(node="*")` ([Install][nvidia-ocp-install], [DRA][nvidia-ocp-dra],
   [Precompiled][nvidia-ocp-precompiled]): Out-of-the-box sanity check verifying `gpu-operator` controller Pod, all
   expected DaemonSets (`DESIRED == CURRENT == READY == UP-TO-DATE == AVAILABLE`, allowing `0/0` where hardware is
   absent), validator Pods (`nvidia-cuda-validator`, `nvidia-device-plugin-validator` `Completed`;
   `nvidia-operator-validator` `Running`), `ClusterPolicy` or `GPUCluster` state, DRA controller/kubelet plugin +
   `gpu.nvidia.com` `DeviceClass` + `ResourceSlice` node coverage (DRA mode), non-standard driver signals (custom
   image/repo, `usePrecompiled` vs DTK, RHCOS kernel/`OSTREE_VERSION` alignment, `DriverNotReady`/Secure Boot signing
   errors), paused/skipped nodes, `driver_drift`, allocatable `nvidia.com/gpu`, and `MachineConfigPool` state.
2. `NvidiaPauseOperator(action="pause|resume", ...)` ([Docs][nvidia-ocp-docs], [Upgrades][nvidia-driver-upgrades]):
   Symmetric pause/resume at node scope (`gpu.deploy.operands`, `gpu.deploy.driver`, per-operand labels,
   `gpu-driver-upgrade.skip`), policy scope (`upgradePolicy.autoUpgrade`), or OpenShift MCO scope
   (`MachineConfigPool.spec.paused`), with independent read-back.
3. `NvidiaPrerequisite` ([Install][nvidia-ocp-install]): Checks NFD readiness; creates/verifies `docker-registry`
   image pull Secret (from `[registry.<name>]`) and optional NLS `licensing-config` Secret in `nvidia-gpu-operator`.
4. `NvidiaClusterPolicy` ([Install][nvidia-ocp-install]): Creates or patches `ClusterPolicy` (`gpu-cluster-policy`,
   blocking if `GPUCluster` exists), wiring driver `repository`, `image`, `version`, `imagePullSecrets`,
   `licensingConfig`, `kernelModuleType`, `usePrecompiled`, and `useNvidiaDriverCRD`.
5. `NvidiaDraCluster` ([DRA][nvidia-ocp-dra]): Creates or patches `GPUCluster` (`gpuclusters.nvidia.com`, blocking if
   `ClusterPolicy` exists), verifies `gpu.nvidia.com` `DeviceClass` and `ResourceSlice` publication on OpenShift
   `4.21+` (validated `4.22`) / GPU Operator `v26.7+`.
6. `NvidiaDriverPool` ([Install][nvidia-ocp-install], [DRA][nvidia-ocp-dra], [Precompiled][nvidia-ocp-precompiled]):
   Creates or patches named `NVIDIADriver` CRs (`nodeSelector`, `repository`, `image`, `version`, `imagePullSecrets`,
   `kernelModuleType`, `kernelModuleConfig`, `usePrecompiled`), preflighting RHCOS kernel/DTK compatibility.
7. `NvidiaCudaUpgrade(node, driver)` ([Upgrades][nvidia-driver-upgrades], [Precompiled][nvidia-ocp-precompiled]):
   Resolves `node="*"` or node/selector to full fleet inventory (reporting overlaps, unmatched nodes, and pool blast
   radius), applies **one explicitly selected governing driver CR per confirmed apply**, tracks
   `nvidia.com/gpu-driver-upgrade-state` through `upgrade-done` (failing fast on `upgrade-failed`, respecting
   `skip=true`), and verifies `nvidia.com/cuda.{driver,runtime}-*` and validator readiness.
8. `NvidiaContainerToolkit` ([Docs][nvidia-ocp-docs]): Aligns `ClusterPolicy.spec.toolkit.*`, `spec.cdi.*`,
   `spec.operator.runtimeClass=nvidia`, `use_ocp_driver_toolkit`, `RuntimeClass/nvidia`, and worker
   `MachineConfigPool` state.
9. `NvidiaMachineConfig` ([Docs][nvidia-ocp-docs], [Precompiled][nvidia-ocp-precompiled]): Separately confirmed
   OpenShift `MachineConfig` callback (MOK/Secure Boot key, IOMMU kernel args, CRI-O/CDI) proving rendered
   `MachineConfigPool` convergence (`Updated=True`, `Updating=False`, `Degraded=False`) before dependent GPU work.
10. `NvidiaEnableFeatureDiscovery` ([Install][nvidia-ocp-install]): Verifies NFD, patches `/spec/gfd/enabled`
    (CRD-verified), waits for GFD DaemonSet, and verifies `nvidia.com/gpu.product` and `nvidia.com/cuda.*` labels.
11. `NvidiaEnableMonitoring` ([Install][nvidia-ocp-install]): Labels `ns/<namespace> openshift.io/cluster-monitoring=true`
    and enables `ClusterPolicy.spec.dcgmExporter.serviceMonitor.enabled: true`.
12. `NvidiaEnableConsole` ([Dashboard][nvidia-ocp-dashboard]): Enables/verifies OpenShift Web Console NVIDIA DCGM
    Exporter GPU monitoring dashboard integration (separate from `NvidiaEnableMonitoring`).
13. `NvidiaTimeSlicing` ([Docs][nvidia-ocp-docs]): Creates/updates a multi-profile time-slicing `ConfigMap`
    (`replicas`, `renameByDefault`, `failRequestsGreaterThanOne`, e.g., `small=2`, `medium=5`, `large=10`) and binds
    `ClusterPolicy.spec.devicePlugin.config.{name,default}`.
14. `NvidiaMachineSetLabel` ([Docs][nvidia-ocp-docs]): Patches `machinesets.machine.openshift.io` in
    `openshift-machine-api` under `/spec/template/spec/metadata/labels` for validated NVIDIA labels
    (`nvidia.com/device-plugin.config`, `nvidia.com/gpu.deploy.driver`, `nvidia.com/gpu-driver-upgrade.skip`, or
    declared `NVIDIADriver` selector keys).
15. `NvidiaUpgradePolicy` ([Upgrades][nvidia-driver-upgrades]): Patches `upgradePolicy` (`autoUpgrade`,
    `maxParallelUpgrades`, `maxUnavailable`, `drain`) and applies separately confirmed failed-node retry
    (`nvidia.com/gpu-driver-upgrade-state=upgrade-required`).
16. `NvidiaCleanup` ([Cleanup][nvidia-ocp-cleanup], [DRA Uninstall][nvidia-ocp-dra]): Mode-specific teardown
    (Device Plugin: OLM remove → confirmed CRD delete; DRA: clear workloads/`ResourceClaim`s → delete `NVIDIADriver` &
    `GPUCluster` → OLM remove → confirmed CRD delete) with absence read-back.

### Staged Chain Composition (`chains.py`)

Chain combos compose strictly from `Nvidia*` callbacks and CI16 OLM callbacks via `WorkflowExecutor` using staged
confirmations (every stage server-validated before apply):

- `install-gpu-operator`: `[K8SAuth, *ci16_nfd, NvidiaPrerequisite, *ci16_gpu_operator, NvidiaClusterPolicy,
  NvidiaEnableMonitoring, NvidiaEnableConsole, NvidiaContainerToolkit, NvidiaEnableFeatureDiscovery, NvidiaHealthCheck]`
- `install-gpu-dra`: `[K8SAuth, *ci16_nfd, NvidiaPrerequisite, *ci16_gpu_operator(v26.7+), NvidiaDraCluster,
  NvidiaDriverPool, NvidiaEnableMonitoring, NvidiaHealthCheck]`
- `upgrade-cuda`: `[K8SAuth, NvidiaHealthCheck, *(optional NvidiaPauseOperator / NvidiaUpgradePolicy),
  NvidiaCudaUpgrade(node, driver), NvidiaHealthCheck]`
- `secure-boot-driver-prep`: `[K8SAuth, NvidiaHealthCheck, NvidiaPauseOperator(pause), NvidiaMachineConfig(mok),
  NvidiaDriverPool(precompiled), NvidiaPauseOperator(resume), NvidiaHealthCheck]`
- `enable-time-slicing`: `[K8SAuth, NvidiaHealthCheck, NvidiaTimeSlicing, *(optional NvidiaMachineSetLabel),
  NvidiaHealthCheck]`
- `recover-failed-upgrade`: `[K8SAuth, NvidiaHealthCheck(report_only), NvidiaPauseOperator(resume_skip),
  NvidiaUpgradePolicy(retry_failed), NvidiaCudaUpgrade(wait_only), NvidiaHealthCheck]`
- `cleanup-gpu-operator`: `[K8SAuth, NvidiaHealthCheck, NvidiaCleanup, NvidiaHealthCheck]`

## Verification Gates

| Gate ID | Scope | Proof Required |
| --- | --- | --- |
| `G-CI17-01` | Unit & contracts | Unit tests per `Nvidia*` callback, CRD/DRA/MCO, pause/resume, `NO_OP`, drift |
| `G-CI17-02` | Live health | Installed `NvidiaHealthCheck` returns `PASS` (Pods, DaemonSets, non-std, MOK/MCP) |
| `G-CI17-03` | Live `--live-plan` | Server-side dry-run + `plan_digest` with `mutated: false` for mutating atomics |
| `G-CI17-04` | Atomic write/restore | Authorized reversible write (incl. pause/resume) verified and restored |
| `G-CI17-05` | Combo & teardown | Installed combo + live full-teardown read-back with renewed acceptance receipts |

## Interfaces and Dependencies

No new third-party Python dependency is introduced. All cluster operations go through `KubernetesService` (`kubectl`)
and CI16's `olm_callbacks`/`olm_resources`. Fresh operand creation ties to `CI16-M3` (pre-provisioned operations remain
runnable independently if `CI16-M3` slips); full operator teardown ties to `CI16-M4`.

Planned module layout under `ci-skills/lib/python/core/kubernetes/nvidia_gpu/`:

- `models.py`: Qualified resource constants (`CLUSTER_POLICIES`, `GPU_CLUSTERS`, `NVIDIA_DRIVERS`,
  `NODE_FEATURE_DISCOVERIES`, `MACHINE_SETS`, `MACHINE_CONFIGS`, `MACHINE_CONFIG_POOLS`, `RUNTIME_CLASSES`,
  `DEVICE_CLASSES`, `RESOURCE_SLICES`), `GPUOperatorAction`, `GPUOperatorState`, `DriverPoolInfo`, `GPUNodeInfo`,
  `DaemonSetHealth`, `GPUNeed`, and `GPU_OPERATOR_MODE_POLICY`.
- `discovery.py`: Shared reads for OLM, `ClusterPolicy`, `GPUCluster`, `NVIDIADriver`, `DeviceClass`, `ResourceSlice`,
  `MachineSet`, MCO/MCP, GPU nodes (including RHCOS kernel/`OSTREE_VERSION`), DaemonSets, and validator Pods.
- `preflight.py`: Shared contract validation, mutual-exclusion and DTK/precompiled checks, `--dry-run=server` diffing,
  and guarded `plan_digest` confirmation.
- `waits.py`: Shared polling for `ClusterPolicy`/`GPUCluster`/`NVIDIADriver` readiness, `gpu-driver-upgrade-state`,
  `MachineConfigPool` convergence, DaemonSet rollouts, and CUDA/DRA/validator read-back.
- `callbacks.py`: The 16 `Nvidia*` atomic callbacks (with `cleanup()` strictly limited to temporary artifacts).
- `chains.py`: Staged callback-list builders for standalone atomic verbs and composed chain combos.
- `cli.py`: `build_parser`, `workflow`, `NvidiaGPUOperator`, `main`, invoked by `ci-skills/bin/nvidia_gpu_operator.py`.

## Outcomes & Retrospective

Not yet implemented. Updated at the completion of each milestone (`CI17-M1` through `CI17-M5`).

[phase]: ../../phases/CI17-NVIDIA-GPU-OPERATOR.md
[ci16-exec]: ci16-olm-operator.md
[nvidia-ocp-docs]: https://docs.nvidia.com/datacenter/cloud-native/openshift/latest/index.html
[nvidia-ocp-install]: https://docs.nvidia.com/datacenter/cloud-native/openshift/latest/install-gpu-ocp.html
[nvidia-ocp-dra]: https://docs.nvidia.com/datacenter/cloud-native/openshift/latest/dra-gpu-ocp.html
[nvidia-ocp-precompiled]: https://docs.nvidia.com/datacenter/cloud-native/openshift/latest/gpu-operator-with-precompiled-drivers.html
[nvidia-ocp-dashboard]: https://docs.nvidia.com/datacenter/cloud-native/openshift/latest/enable-gpu-monitoring-dashboard.html
[nvidia-driver-upgrades]: https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-driver-upgrades.html
[nvidia-ocp-cleanup]: https://docs.nvidia.com/datacenter/cloud-native/openshift/latest/clean-up.html
