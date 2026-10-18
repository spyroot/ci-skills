---
name: ci-skills
description: Use repeatable GitLab CI and Kubernetes/OpenShift tools for jobs, milestones, issues, runners, storage, events, Cilium, Ceph, node MTUs, API reads, and exact-commit build plans.
metadata:
  manifest: tools.json
  default_output: json when stdout is not a terminal
  read_only: false
---

# CI Skills

The directory containing this file is the installed skill root. Keep the
working directory at the calling project root so its `.ci-skills/target.toml`
can be selected. All command paths below are relative to the **installed skill
root**, not the working directory. Set `CI_SKILLS_ROOT` to that directory and
invoke its Python entrypoints with Python 3.11 or newer. Prepare that interpreter
with PyYAML and `jsonschema`, which installs `referencing`; the repository
`requirements.txt` supplies these dependencies. The copy installer does not
create a Python environment or install Python packages. For a default Codex
installation using the prepared `ci-skills` conda environment:

```bash
CI_SKILLS_ROOT="${CODEX_HOME:-$HOME/.codex}/skills/ci-skills"
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/reference.py" next --json
```

This also applies to the extensionless `bin/ci-api` and `bin/ci-binary-build`.
Use these commands for supported operations instead of rebuilding them with
one-off CLI parsing. The selected command's `--describe` lists its native
`required_tools`. GitLab uses `glab`,
GitHub uses `gh`, Kubernetes uses `kubectl`, OpenShift builds and MTU checks
also use `oc`, and registry image inspection/publication uses `skopeo`.
Installing the skill does not install all of those native clients.

Diagnose a Kubernetes-backed CI, storage, or network symptom, or act on an
explicit GitLab request. GitLab operation commands default to an offline
dry-run and write only with `--apply` and the printed plan fingerprint.
`k8s_verify_mtu_consistency.py --apply` creates temporary OpenShift debug Pods
and verifies cleanup; its default invocation reads the selected node inventory
and returns a plan.

## 1. Choose the command for the symptom

Start at `bin/reference.py next` and expand only the selected capability or reference.
Known commands remain directly callable; `reference.py [node]` keeps the compact
proposition interface. Use `next PATH run ID options` for optional inputs and
`get REF ANCHOR` for one reference section. Target-backed commands
resolve only their declared authority: GitLab for a job, or Kubernetes for
storage, events, Cilium, and Ceph. Diagnostic reports use `access` to report the
effective source and identity. A Kubernetes-only target needs only `[kubernetes]`; a GitLab-only
target needs only `[gitlab]`. The selected target file never inherits missing
tables from a lower tier. For a selected GitLab operation, its command also
reads back GitLab identity and target before applying a change.

For one receipt proving all three authorities, run:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/access_check.py" --json
```

Add `--publication` when repository administration and required-check read-back
are part of the requested proof. Add `--ceph-namespace NAME` when the receipt
must also prove the Ceph collector in that selected namespace. Ceph, storage,
events, and kernel diagnostics require base Kubernetes access; Cilium discovery
and health exec are checked only for Cilium commands and the full receipt.

Do not go looking for credentials. The selected command resolves them and
tells you what it used. For the full access receipt:

- `PASS` — `credential_sources` names the effective source per required
  authority and `surfaces.<name>.identity` names the identity read back.
- `BLOCKED` — `surfaces.<name>.reason` names what failed and `next_step` what
  to do. Report that; do not try other credentials.

Access is resolved from declared locations, first match wins, and the match is
reported. The chain per authority is in `tools.json` under `access_protocol`,
and [references/access.md](references/access.md) is the full contract.
The installed `gh` environment contract uses `GH_TOKEN`/`GITHUB_TOKEN` for
`github.com` and `*.ghe.com`, and enterprise token variables for other GitHub
Enterprise Server hosts. A selected token file clears ambient GitHub tokens.
For Kubernetes, the target may declare `kubernetes.kubeconfig` or the ordered
`kubernetes.kubeconfigs` path. Otherwise resolution uses `KUBECONFIG`, then
`~/.kube/config`. Every route must match the target's context and API server;
the skill does not substitute an ambient current context. For a project-specific
kubeconfig resolver, use the [project binding](references/project-binding.md)
and its declared sources.

Provisioning access is not this skill's job. If a kubeconfig has to be fetched
or minted first, that belongs to the calling project's own instructions.

## 2. Read the command manifest

`tools.json` beside this file is the machine-readable contract for API and
node commands: their purpose, when to use them, required authorities or
execution surface, options, and symptom routing. `<command> --describe` prints
one command's contract as JSON and needs no credentials. Read its `required_tools` and
`requires_authorities` before choosing a live operation.

Routing, in short:

| You need | Command |
| --- | --- |
| Toolbox-backed GitHub runner lifecycle | `bin/create_selfhosted_runner.py` |
| Build a schema-declared toolbox | `bin/toolbox.py` |
| Build an OpenShift ISO or join its node | [bin/ocp_node_onboard.py](references/ocp-node-image.md) |
| Publish or verify a Harbor toolbox | `bin/harbor_push.py`, `bin/harbor_pull.py` |
| proof of access across all three authorities | `access_check.py` |
| a named CI job's own facts | `gitlab_job.py --job-url URL` |
| pipeline read, confirmed start, or bounded watch | `gitlab_pipeline.py get\|start\|watch` |
| why a volume or claim is stuck | `storage_report.py` |
| what the cluster said during an interval | `event_trace.py --last 15m` |
| connectivity, or CNI health per node | `cilium_status.py` |
| read back GitLab target and identity | `gitlab_access.py check` |
| create, update, or adjust milestone dates | `gitlab_milestone.py` |
| open a bug issue | `gitlab_issue.py open-bug` |
| create or update a wiki page | `gitlab_wiki.py` |
| inspect, filter, create, assign, tag, or delete a runner record | `gitlab_runner.py` |
| Ceph hierarchy and Pods | `ceph_cluster.py --namespace NAME` |
| physical PCI NIC MTU mismatch across nodes | `k8s_verify_mtu_consistency.py` |
| Cilium daemon and health on a selected node | `cilium_node.py` |
| Ceph or RBD kernel messages on that node | `ceph_kernel.py` |
| a bounded GitHub or GitLab API response | `bin/ci-api get --provider PROVIDER --endpoint PATH` |
| validate an exact-commit OpenShift build plan | `bin/ci-binary-build` |

## 3. One target and output interface

Use `<command> --describe` for its exact options. Diagnostic commands share
`--json`, `--yaml`, `--human`, `--dry-run`, and `--revision`, and accept
`--binding` for a project-selected target and kubeconfig source. Otherwise
they use the target-resolution chain in [references/access.md](references/access.md). Use native
filters such as `--namespace`, `--node`, or `--last` where the command declares
them.

The defaults and output contracts for `ci-api` and `ci-binary-build` are
described in section 9.

For a GitLab operation, select the exact project or group in the target file or
with `--project`/`--group`. Run the action without `--apply` to get a
machine-readable dry-run plan and its `plan_digest`. Only an explicitly
requested write uses `--apply --confirm-plan DIGEST`; the command reads the
resource before changing it and verifies it afterward. `--token-out PATH` is
required for both the plan and apply when creating a runner record because
GitLab returns its token once and the destination is bound into the plan;
the token never appears in a report. Runner registration and online readiness
are separate from creating its record.

`gitlab_runner.py tag --runner-id ID --tag TAG` adds a tag to an existing runner record. Repeat `--tag` to add several.
Plan first, then apply with the printed digest; the result reads back the tags before and after the change. Tagging a
record does not register or start its executor.

For a failed job in a pipeline, use `gitlab_runner.py get --job-id JOB_ID --project PROJECT --json`. It resolves the
job's runner ID and returns a compact job-to-runner relation with tags, online state, protection level, sharing, and
project IDs. `get --runner-id ID` reads a known runner directly. Use `list --project PROJECT --filter protected` to
narrow a project view; repeated filters combine. `get` and `list` are live reads unless `--dry-run` is supplied; use
`--receipt-out PATH` to save their portable live evidence.
`delete --runner-id ID --project PROJECT --live-plan` reads the exact runner identity and affected project IDs and
prints an apply-ready digest. Apply with that `--confirm-plan` digest; a changed identity or assignment set refuses the
delete. Deletion verifies a global 404 and absence from the selected project or group listing.

For a group runner assignment, the offline plan has no project list and cannot
authorize apply. Run `gitlab_runner.py assign --group GROUP --runner-id ID
--live-plan` to read the exact project IDs and obtain an apply-ready digest.
Apply re-reads that set and blocks if it changed before any assignment.

Target selection and credential precedence are defined in [references/access.md](references/access.md).
Keep the consumer project as the working directory; project configuration takes precedence over the user target.
Use [the binding reference](references/project-binding.md) only for a project-supplied kubeconfig resolver.
A terminal gets a human summary; a pipe or file gets versioned JSON unless an output flag selects another mode.

Prefer a native filter over a shell pipeline — `--namespace`, `--node`,
`--reason`, `--search`, `--last` — because a pipeline discards the identity and
timing you will need to correlate. `--last 15m` beats computing an RFC3339
pair; pass the job's own interval when correlating a job.

## 4. Read the status honestly

- `PASS` — every selected authority and live check passed.
- `PARTIAL` with `access_proven: true` — the read worked and a component is
  unhealthy. `records[].findings` names explicit Cilium daemon, peer, or
  endpoint failures with an inspection action. Say which component.
- `BLOCKED` — see `blocking_live_checks` and the surface `reason`.
- `DRY_RUN` — a plan, never proof of NIC MTUs. The MTU planner reads the
  authenticated node inventory; other collectors do not contact their APIs.
- `PLANNED` — a live, read-only group assignment plan with bound project IDs.
- `UNKNOWN` — a per-item reading could not be taken. Preserve it; do not
  coerce it to a failure or a pass.
- For GitLab writes, `PASS` means an applied change or verified no-op with
  independent read-back. A returned API ID alone is not acceptance evidence.
- An event read returning zero records with zero errors means the events aged
  out of the cluster, not that the read failed. Say "unverified, evidence
  expired".

For these diagnostic and operation reports, exit 0 is `PASS`, `DRY_RUN`, or
`PLANNED`; exit 2 is `BLOCKED` or `PARTIAL`. The two read tools retain the
exit classes in section 9.

## 5. Correlate, then state your confidence

Correlate by job time, Pod UID, node, claim and event reason. Mark each
conclusion **confirmed** by direct read-back, **correlated** by matching time
and identity, or **unverified** when evidence is missing or expired.

Diagnostic and GitLab operation reports carry the gate that authorized them:
`access.profile`, the identities, the credential sources, the execution host,
and `skill.digest`.
That digest is identical across every report from one installed copy, and each
carries a `receipt_sha256` tying it to its gate — cross-check and cite them,
and several reports become one audit trail.

`skill.revision.verified` is true for an installed copy only when
`INSTALLATION.json`, written by the repository installer during its staged
copy, records a clean source revision and the current installed digest still
matches that record. Otherwise a supplied commit SHA remains an unverified
claim. The repository acceptance checker independently resolves that revision
and compares its skill tree and digest.

## 6. Persisting evidence

Nothing is written unless you ask. Where declared, `--output-dir PATH` writes
paired JSON and text. For an artifact that will be kept or committed, use `access_check.py
--receipt-out PATH` instead: only that form digests absolute host paths, and
the captured form names credential locations under someone's home directory.
Review any artifact before sharing — event messages and job traces can carry
sensitive text.

## 7. Read evidence on a selected node

Declare `[kubernetes.node_diagnostics]` and an existing Pod route in the
selected `.ci-skills/target.toml` (see the template). Run
`bin/cilium_node.py --json` to execute `cilium-dbg` and `cilium-health`
inside that agent Pod without a TTY. Run `bin/ceph_kernel.py --json` to
classify recent host journal messages through a selected existing Pod whose
host journal mount is read back first. Both accept `--binding` and otherwise
use the shared target-resolution chain, pinned kubeconfig/context/server and Kubernetes access gate
as the cluster collectors;
neither creates a Pod or repairs a node. Both accept `--search TEXT`;
`ceph_kernel.py` also accepts `--classification NAME`.

## 8. Verify physical uplink MTUs

On OpenShift, for Ceph connectivity, RBD timeout, or cross-node network
symptoms, check physical MTU consistency before searching node interfaces by
hand. This command requires `oc` as well as `kubectl`, and blocks if the
selected API is not OpenShift. Run
`bin/k8s_verify_mtu_consistency.py --json` in the selected project. It
resolves the same exact Kubernetes target, verifies access, reads the node
inventory, and returns `plan_digest` without creating a Pod. Then run
`bin/k8s_verify_mtu_consistency.py --apply --confirm-plan SHA256 --json`
using that digest. `--node NAME` restricts both calls to one existing node.

The apply uses `oc debug node/NAME` to read `ip -d -j addr show` through
`chroot /host`. It selects PCI Ethernet interfaces regardless of IP address,
compares their MTUs, and emits one versioned JSON report or a human table.
The `ipv4_addresses` field is empty when a selected interface has no IPv4 address.
The command creates temporary debug Pods in the selected context's namespace,
tags them for this run, deletes any survivors, and reads back their absence.
An incomplete read or cleanup is `BLOCKED` or `PARTIAL`; a real MTU mismatch is
`PARTIAL` with `physical_mtu_mismatch` and an inspection action. It does not
change host interfaces or repair networking.

## 9. Read APIs and validate binary build plans

`bin/ci-api` performs a bounded GET against a caller-selected GitHub or
GitLab API endpoint through native `gh` or `glab`. Select the provider and
relative endpoint; host and token references resolve from the selected
`target.toml`. `--host` and `--token-file` provide explicit overrides.
`check --provider PROVIDER` reads authenticated identity. For `get`, use
`--field` for a dotted JSON field, `--decode-base64` for encoded file content,
`--lines START:END` for a bounded text slice, and `--paginate` for array pages.
Its output is the selected provider value, not an access-receipt envelope.

`bin/ci-binary-build` uses Git and PyYAML to validate the caller's BuildConfig,
source repository, full commit, and commit-label key. It checks the Dockerfile
at that commit and returns a fingerprinted plan. It does not resolve a cluster
target, execute a build, or publish an image.

Both are Python entrypoints over shared callbacks. They default to JSON and
support `--yaml`, `--help`, and catalogue-driven `--describe`. API reads also
support `--output text`. Exit 0 means success; 64 means invalid arguments,
65 invalid data, 66 missing input, and 69 a blocked operation. `--dry-run`
on `ci-api` describes the request without contacting the provider; the binary
planner always validates local inputs and never executes the build.

For a Ceph network or RBD timeout, start with `ceph_cluster.py` and
`event_trace.py`, then run the MTU command when physical network consistency
matters. For Cilium, run `cilium_status.py` first and `cilium_node.py` for an
affected node. `tools.json` describes every command and its filters.

## Toolbox and runner workflows

`bin/reference.py harbor` points to the installed `schemas/toolbox.schema.json`. Read that schema before producing a
specification. `target.toml` supplies the Harbor endpoint/project and either its existing combined `credential_file`
or paired `username_file` and `password_file`. Paths resolve beside the selected target file; commands do not accept
credential values as arguments. GitHub and Kubernetes retain their existing access resolution.

A toolbox specification supplies build inputs and the commands its generated doctor must find. This minimal runner
example assumes a base image that already contains a working GitHub Actions runner, `bash` and `gh`. Replace the example
image, Harbor repository/tag and build namespace/app with your selected values, then save it as `toolbox.json`:

```json
{
  "apiVersion": "ci-skills/v1",
  "kind": "ToolboxSpec",
  "metadata": {"name": "runner-toolbox"},
  "spec": {
    "baseImage": "registry.example.com/team/runner-base:version",
    "image": {"repository": "runner-toolbox", "tag": "candidate"},
    "build": {"namespace": "ci-tools", "app": "runner-toolbox"},
    "dependencies": [],
    "requiredCommands": ["bash", "gh"]
  }
}
```

The example image is caller-supplied, not a bundled fixture. `dependencies` is empty because that image already contains
its runtime. To install additional packages or archives, use the dependency definitions linked by
[schemas/toolbox.schema.json](schemas/toolbox.schema.json); archive/download inputs include their declared checksum.
The runner's `--runner-root` must name the image directory containing its working `config.sh` and `run.sh`, with that
runtime's libraries installed. This directory is copied into the runner's writable runtime during registration.
Host `gh` and the image's `gh` are separate prerequisites.

From the consumer project, plan the runner using your specification and image's actual runtime directory:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/create_selfhosted_runner.py" create \
  --spec ./toolbox.json --name example-runner --runner-root /opt/actions-runner \
  --label ci-skills --json
```

A successful live plan returns `status: PLANNED` and `plan_digest`; it reads provider identities and resolves image
digests without building or deploying. Repeat the same inputs with `--apply --confirm-plan DIGEST` using that digest.
Successful apply returns `status: PASS`, verified callback records including the online GitHub registration, and
`cleanup.status: PASS` for temporary resources. Missing configuration, tools, runtime or read-back produces a blocked
result; a schema-valid specification alone does not prove its selected image works. To check the input shape without
provider calls, add `--dry-run`; its `DRY_RUN` result does not authorize apply.

The independent build uses the same file:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/toolbox.py" create --spec ./toolbox.json --json
```

Use `--help` for the selected operation. Planning reads current provider identity and resolves image references;
`--dry-run` stays offline. Mutation requires `--apply --confirm-plan` with the exact returned digest. Runner deletion
removes its workload, registration and owned Secrets. Toolbox deletion is a separate explicit operation; it removes
the selected build record and image tags; shared image content remains in Harbor. GitHub Actions supplies no
provisioning prerequisite.
