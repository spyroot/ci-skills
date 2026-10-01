# Kubernetes admin diagnostics skill

`k8s-admin-diagnostics` collects read-only GitHub, GitLab CI, Kubernetes
storage, event, and Cilium evidence. Its commands use `gh`, `glab`, and
`kubectl`. They print human summaries by default and support JSON and YAML.

## Start here

1. On the intended execution host, provide Python 3.11 or newer with PyYAML,
   plus `gh`, `glab`, `kubectl`, and `oc`. Use the host's project Python
   environment. Configure credentials through the selected files, named
   environment variables, or CLI credential stores; do not put credential
   values in the target TOML.
2. Check out the merged repository and run the installer from its root:

   ```sh
   git clone https://github.com/spyroot/ci-skills.git
   cd ci-skills
   python3 -m venv .venv
   . .venv/bin/activate
   python -m pip install 'PyYAML>=6,<7'
   python tools/install_k8s_admin_diagnostics.py --dry-run --json
   python tools/install_k8s_admin_diagnostics.py --json
   ```

   The installer requires a clean skill subtree and reports its verified
   revision and digest. It installs into
   `$CODEX_HOME/skills/k8s-admin-diagnostics` or, when `CODEX_HOME` is unset,
   `~/.codex/skills/k8s-admin-diagnostics`. Use `--skills-dir PATH` for another
   destination. An existing destination blocks replacement; remove or move it
   deliberately before an upgrade.
3. In the consuming project, create `./.ci-skills/target.toml` with the exact
   GitHub repository, GitLab origin, Kubernetes context, API server, and
   kubeconfig source. A target can declare one `kubeconfig` or an explicit
   `kubeconfigs` candidate list. Use the complete nonsecret example in
   [project-binding.md](skills/k8s-admin-diagnostics/references/project-binding.md).
   `~/.ci-skills/target.toml` is the user fallback. An explicit `--target PATH`
   or `CI_SKILLS_TARGET=PATH` takes precedence over both. A project that
   generates kubeconfig with its own helper can declare an explicit binding
   there; the skill does not assume a helper name.
4. From the consuming project, inspect the plan, then run the live access gate
   on that same execution host:

   ```sh
   skills_dir="${CODEX_HOME:-$HOME/.codex}/skills"
   diagnostics="$skills_dir/k8s-admin-diagnostics/scripts/access_check.py"
   python "$diagnostics" --dry-run --json
   python "$diagnostics" --json
   ```

   If installation used `--skills-dir PATH`, set `skills_dir` to that path.

   The live receipt must report `status: PASS`, the selected target source,
   and individual GitHub, GitLab, and Kubernetes results. Add `--publication`
   after declaring the repository's actual required checks in the target.
   Use `--receipt-out PATH` when a sanitized, portable receipt is needed.

The target file may be project-local but must contain no credential values.
Keep tokens, kubeconfigs, and private keys out of tracked files. The commands
never log in, grant roles, or change the active context. See
[access.md](skills/k8s-admin-diagnostics/references/access.md) for effective
credential selection and live check details.

Codex can also install the merged skill directly from GitHub using
`https://github.com/spyroot/ci-skills/tree/main/skills/k8s-admin-diagnostics`.
The skill becomes available in the next Codex turn; installing it provides no
credentials or API access.

## Commands

The API commands accept `--binding PATH` or `--target PATH`, `--revision SHA`,
`--json`, `--yaml`, `--dry-run`, and `--help`. Pass a full source commit SHA with
`--revision`
when the installed copy has no Git metadata. Reports also accept
`--output-dir PATH` to write paired JSON and text files. Without that option,
no report file is written.

- `access_check.py` checks the three selected authorities and runs the
  storage, event, and Cilium collector reads. `--publication` also requires
  repository admin permission and read-back of required branch checks.
  `--job-url URL` also checks that job, pipeline, runner, and trace.
- `gitlab_job.py --job-url URL` reads a selected job, pipeline, runner, and
  bounded trace. It accepts `--search TEXT`.
- `storage_report.py` correlates PVCs, standalone PVs, Pods, attachments,
  and controllers. Filters: `--namespace NAME|all`, `--node NAME`,
  `--storage-class NAME`, `--phase Pending|Bound|Lost|Released|Failed|all`,
  and `--search TEXT`.
- `event_trace.py` reads both Kubernetes event APIs and accepts `--from`,
  `--to`, `--namespace`, `--kind`, `--object`, `--reason`, and `--search`.
  Its default window is the previous hour.
- `cilium_status.py` reads Cilium resources and executes non-TTY health on
  ready agents. It accepts `--namespace NAME|auto`, `--node`, and `--search`.
- `ceph_cluster.py --namespace NAME` reads Ceph status, OSD tree, inactive PGs,
  and OSD/monitor Pods through `oc` on the same pinned target. `--operator`
  and `--conf` select the operator deployment and in-Pod Ceph config;
  `--node`, `--ready all|true|false`, and `--condition TYPE=STATUS` filter
  the Pod view.

For example, on the selected OpenShift execution host:

```sh
python ~/.codex/skills/k8s-admin-diagnostics/scripts/ceph_cluster.py \
  --namespace openshift-storage --node worker-a --ready false --json
```

The JSON report includes `status`, `condition`, `health`, `actions`, and Pod
records. A failed query produces `PARTIAL` with an error, never an empty
successful result. Use `--condition PodScheduled=False` to inspect a specific
Pod condition.

The node-local commands run on the selected Linux node with noninteractive
`sudo -n`. They require local `crictl` or `journalctl`, and do not require
the API target file:

- `cilium_node.py --json` reads the local running `cilium-agent` container
  through CRI, then collects `cilium-dbg status --verbose --output json` and
  `cilium-health status --verbose --output json` concurrently. If no agent is
  running, it records the stopped Cilium containers.
- `ceph_kernel.py --json` reads the previous three minutes of kernel journal
  entries matching `libceph|rbd|ceph`. Each record has a UTC timestamp,
  priority, classification, and machine-readable recommended action. It
  performs no recovery action. The window is the previous three minutes, with
  bounded output; a limit hit is reported as `PARTIAL`.

The kernel classifier emits action codes for observed blocklisting, auth
failure, connectivity timeout, and I/O errors. Other priority 0–3 entries
receive `review_ceph_kernel_event`; informational entries have no action.
The action code is a prompt for investigation, not a claimed root cause.

Both accept `--yaml`, `--dry-run`, and `--help`. Exit code 0 means the node
read succeeded or a dry run was requested; code 2 means incomplete evidence.
These node reads supplement the three-surface API receipt; they do not
replace it.

The three-surface base access gate runs before every API collector;
`access_check.py` runs the expanded bundle. Exit code 0 means `PASS` or a
marked `DRY_RUN`; code 2 means `BLOCKED` or `PARTIAL`. See
[access.md](skills/k8s-admin-diagnostics/references/access.md) for evidence
profiles, health interpretation, and the live receipt contract.

## Validation

The `validate` workflow checks workflow/YAML and Markdown syntax, diff
hygiene, secrets, Ruff lint and format, package behavior, and mocked denial
paths. Its package smoke runs every installed entrypoint from outside the
source tree. Mocked CI is code evidence; the live access receipt must come
from each intended execution host. Use the exact target and tested revision
there, and retain the sanitized receipt only after all required checks pass.
