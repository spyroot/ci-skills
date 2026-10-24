# Per-computer access and evidence

The installed skill contains instructions and code. Each execution host
supplies a nonsecret target file and its own credentials. The target file
identifies the authorities its commands use. A Kubernetes collector needs its
exact context and API server; a GitLab job needs its exact origin. The access
receipt checks only configured GitHub, GitLab and Kubernetes providers; a
configured GitHub check needs its repository. A project can select a target
and its kubeconfig through the [project binding](project-binding.md).
`gitlab_job.py` can use a GitLab-only target; its job URL selects the exact
project for identity and project access read-back before job, pipeline, runner,
and trace reads.

## Provider identity and required roles

The installed `ci_skills.spec.json` declares the expected provider roles:
Kubernetes `cluster_role: cluster-admin`; GitLab and Harbor `role: admin`;
GitHub `role: admin` on the selected repository. GitHub repository Admin does
not establish every permission of a fine-grained token; native API denials
still fail the requested operation.

The shared executor loads this file unless its library caller supplies `Spec`.
Provider authentication establishes identity and actual role once per invocation.
Every callback first calls `whoami()`; later callbacks check the retained runtime
against the declaration without another identity request. An unset role or a
mismatch fails before the operation. A fresh invocation starts with unset roles.
`--help` and `--describe` do not authenticate.

The installer rejects a missing declaration. The existing required installation
check exercises absence, exact restoration, and successful installed loading.

## Where the target file comes from

Without a project binding, three declared places are checked in order, and the winner is reported back in
every report as `target_file` and `target_source`:

| # | Scope | Where | Source name |
| --- | --- | --- | --- |
| 1 | one environment | `$CI_SKILLS_TARGET` | `env:CI_SKILLS_TARGET` |
| 2 | one project | `./.ci-skills/target.toml` | `project` |
| 3 | one user | `~/.ci-skills/target.toml` | `user` |

One cluster means filling in tier 3 once; many clusters mean tier 1 or tier 2,
one target per cluster. A set `CI_SKILLS_TARGET` must name an existing file;
an empty value or missing file blocks without falling back to another target.
The nonsecret example below shows the target shape;
include only the providers required by your command. Every provider section is
optional: a target that declares only `[kubernetes]` serves every command whose
only declared authority is Kubernetes, and a command that needs an undeclared
provider blocks and names the authority tables it requires. A live smoke, or live test, reproduces the
installed user experience when a user installs the skill in their agentic
setup: the installed commands run against real systems with exactly such a
user-written target. `core.catalog.TARGET_PROTOCOL`
defines target selection, also published in the installed `tools.json`.

`ci-api` uses these three tiers. `ci-binary-build` validates explicit local inputs without selecting a
target. For smoke testing, `env ci-skills-smoke=1 <command>` changes only the
basename in the project and user tiers to `ci-skills-smoke.toml`. Directory
precedence and relative credential resolution stay unchanged; those tiers do
not fall back to their ordinary `target.toml` while the switch is set.

`--binding PATH` selects a [project-specific kubeconfig resolver](project-binding.md) before environment
selection. Without that option, `K8S_ADMIN_DIAGNOSTICS_BINDING` selects a binding. Setting both
`K8S_ADMIN_DIAGNOSTICS_BINDING` and `CI_SKILLS_TARGET` is an error. An explicit
or environment-selected target that is absent blocks without trying a lower tier.
The selected file never inherits missing provider sections from another tier.

The `target_selection` receipt field also records the selected route:
`binding:`, `env:CI_SKILLS_TARGET -> file:`, `project:` or `user:` with its
resolved path. Portable receipts replace absolute paths with digest tokens.

This skill resolves; it does not provision. It creates no target file, mints no
token and fetches no kubeconfig. Putting them there is the user's job, or
an explicit step in the calling project's own instructions.

## Effective credential sources

- GitHub uses an explicit `github.token_file` from the target, then the
  effective `GH_TOKEN` or `GITHUB_TOKEN` environment variable on `github.com`
  or a subdomain of `ghe.com`. GitHub Enterprise Server hosts use
  `GH_ENTERPRISE_TOKEN` or
  `GITHUB_ENTERPRISE_TOKEN`. Otherwise, the selected host's `gh` credential
  store is used. An explicit token file clears other ambient GitHub tokens.
- GitLab uses an explicit `gitlab.token_file`, then the effective
  `GITLAB_TOKEN`, `GITLAB_ACCESS_TOKEN`, or `OAUTH_TOKEN` environment variable.
  Otherwise, the selected host's `glab` credential store is used.
- Kubernetes uses the target's explicit `kubernetes.kubeconfigs` ordered,
  combined path, its `kubernetes.kubeconfig` single file, or the kubeconfig
  selected by an explicit project binding. If none is declared, it uses
  `KUBECONFIG`, then `~/.kube/config`. Use `kubeconfigs` when the context and
  credential live in separate files, in kubectl's path order. The selected
  context and API server are checked against those files.
  The user may use an embedded token, `tokenFile`, client certificate and
  key, or an exec provider. No separate token file is assumed.

An explicit missing or unreadable file blocks with no fallback to a different
credential. Each collector selects only its declared authority, checks that
authority, and uses the same resolved source for its data reads. It records
source references, not token values, private keys, or raw kubeconfig contents.
Keep credentials outside this repository and the installed skill.

Relative `github.token_file` and `gitlab.token_file` paths resolve beside the
selected `target.toml`, independently of the command's working directory.
Absolute paths and paths starting with `~` remain supported.

Example nonsecret target:

```toml
[github]
host = "github.com"
repository = "owner/repository"
# token_file = "/home/user/.ci-skills/credentials/github.token"

[gitlab]
url = "https://gitlab.example.com"
# token_file = "/home/user/.ci-skills/credentials/gitlab.token"

[kubernetes]
context = "admin-context"
server = "https://api.cluster.example.com:6443"
# kubeconfig = "/home/user/.kube/config"
```

## Package and functional identity

The shared [`core/provenance.py`](../lib/python/core/provenance.py) supplies two
checksums to live receipts that carry a `skill` identity:

- `skill.digest` identifies the installer-checked skill file tree, including
  documentation. The installer uses it to verify copied files. Its own record,
  symlinks, bytecode caches, and platform metadata are excluded.
- `skill.functional_digest` identifies files under `bin/`, `lib/`, and `schemas/`,
  plus `ci_skills.spec.json`. Changing a declared role changes this checksum.
  Live reports and the GitHub acceptance checker use the same calculation over
  sorted relative paths, executable bits, and file contents. Documentation,
  tests, and bytecode caches are excluded. Matching checksums bind the captured
  live results to the candidate's unchanged runtime files; they do not establish
  that an operation passed without its result and read-back evidence.

A revision is reported `verified` when it came from a clean tracked skill
subtree, or when the installer's `INSTALLATION.json` records that clean source
revision and its package checksum still matches the installed bytes.
`--revision` remains a claim. The invoking project's `CI_COMMIT_SHA` is recorded
separately in `consuming_project`. The repository acceptance checker validates
the recorded revision fields and compares the receipt's functional checksum with
its own calculation for the candidate. The original commit need not remain in
the candidate's Git history. A documentation or test edit can change
the package checksum without invalidating live proof of unchanged runtime files.

## Configured-provider live receipt

Run `access_check.py --json` to check the configured GitHub, GitLab and Kubernetes
providers in the selected target. At least one supported provider is needed.
Absent providers require no credentials or tools; malformed selected tables still fail.
Harbor access remains part of the selected Harbor operation.
`--publication`, `--job-url` and `--ceph-namespace` additionally require GitHub, GitLab
and Kubernetes respectively, before any API calls. Supply `--revision SHA` for an installed copy without Git
metadata, and `--job-url URL` when verifying a requested job. The receipt
identifies the execution host, time, revision, sources, targets, identities,
and individual results.

- GitHub: authenticate the selected host, read back the identity and exact
  repository and its declared role. Publication mode additionally requires
  read-back of nonempty required status checks on the protected main branch.
- GitLab: authenticate the exact origin, read back an identity with
  `is_admin: true` and matching host, and read the instance runner API.
  A selected job also requires the job, pipeline, runner, and trace reads.
- Kubernetes: match the context to the exact HTTPS API server using the selected
  kubeconfig settings, read back the effective identity, check administrator
  permissions, and perform the storage and event resource reads. Cilium
  diagnostics require a real non-TTY `cilium-health` command on a ready agent.

The gate proves access, not cluster health -- this skill exists to be run ON a
degraded cluster. A denial (`authentication`, `authorization`, `missing_tool`)
always blocks. Any other read failure blocks a single-shot collector; Cilium is
read per agent, so one agent failing while others answer is that agent's state,
and the capability counts as proven when at least one real agent returned a
health response that carries `local` or `nodes`. Parseable JSON is not a health
response. Each live check reports `access_proven` beside its own `status`, and
the receipt lists `blocking_live_checks`.

Agents are discovered through the `cilium` DaemonSet's own selector, resolved
from the one cluster-wide listing that also resolves the namespace. A name
prefix would also match `cilium-operator-*` and `cilium-envoy-*`, which carry
no health endpoint.

Publication mode compares required status checks with `github.required_checks`
in the selected target file. Every declared name must appear in the branch
protection read-back; additional protected checks are allowed. An absent
declaration or missing required name blocks that publication check.

Each kubeconfig is digested when sources are bound, and the digest is
re-checked before every Kubernetes command. Pinning the filename is not pinning
the target: the server is verified once, then each later command reopens that
mutable path. This is detect-and-block, not an atomic pin -- a file swapped
between the check and the command's own open is still possible -- and it closes
the case that actually happens, a login rewriting the kubeconfig mid-run.

The command-specific base gate runs before every collector. Cilium discovery
and non-TTY health exec extend that gate only for Cilium commands and the full
`access_check.py` receipt. The configured-provider bundle runs in
`access_check.py`, and every report names which gate it passed in
`access.profile`. A
collector report also carries `access`: the identities, credential sources,
targets, execution host,
skill digest and a `receipt_sha256` correlating it to the gate that authorized
it. An `access_check.py` receipt carries `profile` at the top level instead,
since it IS the gate rather than a report authorized by one.

`--receipt-out PATH` writes the committable form, with every absolute host path
replaced by a digest token. That is what makes a real receipt publishable: the
captured form names credential locations under the user's home directory.
Invoke `access_check.py --ceph-namespace NAME` when the requested live proof
includes Ceph reads in that selected namespace. Capture live evidence from the
installed candidate on the intended execution host; the repository's static
and mocked GitHub checks do not run against these private runtime targets.

`PASS` requires all selected live checks. Missing, invalid, expired,
wrong-target, or unauthorized credentials block. A mock, file-existence
check, login-status message, dry run, or receipt from another host is not
live acceptance evidence.

Cluster-wide list reads carry their own bound, `core.collection.LIST_TIMEOUT_SECONDS`, defined in
[`collection.py`](../lib/python/core/collection.py).

## Automated gates

The `validate` workflow checks package layout, all installed entrypoints from
an unrelated working directory, workflow/YAML and Markdown syntax, diff
hygiene, secret scanning, Ruff lint and format, and mocked behavior. It also
evaluates the committed live receipts against the candidate's functional
checksum, the required operation set, and the freshness window declared in the
repository's `inventory/ci/live-acceptance.json`, on every run and on the
declared schedule. CI proves code behavior at its tested commit. The per-host
live receipt proves actual access and resource reads on that host.
