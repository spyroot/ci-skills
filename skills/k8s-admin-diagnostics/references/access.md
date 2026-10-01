# Per-computer access and evidence

The installed skill contains instructions and code. Each execution host
supplies a nonsecret target file and its own credentials. The target file
identifies one exact GitHub repository, GitLab origin, Kubernetes context, and
API server. Use `--target PATH`; no target location is assumed.

## Effective credential sources

- GitHub uses an explicit `github.token_file` from the target, then the
  effective `GH_TOKEN` or `GITHUB_TOKEN` environment variable on github.com.
  Enterprise hosts use `GH_ENTERPRISE_TOKEN` or
  `GITHUB_ENTERPRISE_TOKEN`. Otherwise, the selected host's `gh` credential
  store is used.
- GitLab uses an explicit `gitlab.token_file`, then the effective
  `GITLAB_TOKEN`, `GITLAB_ACCESS_TOKEN`, or `OAUTH_TOKEN` environment variable.
  Otherwise, the selected host's `glab` credential store is used.
- Kubernetes uses an explicit `kubernetes.kubeconfig`, `KUBECONFIG`, or the
  default kubeconfig. The selected context resolves the user and cluster.
  The user may use an embedded token, `tokenFile`, client certificate and
  key, or an exec provider. No separate token file is assumed.

An explicit missing or unreadable file blocks with no fallback to a different
credential. The gate selects sources once
and passes the same sources to every collector. It records source references,
not token values, private keys, or raw kubeconfig contents. Keep credentials
outside this repository and the installed skill.

Example nonsecret target:

```toml
[github]
host = "github.com"
repository = "owner/repository"
# token_file = "/home/operator/.config/ci-skills/github.token"

[gitlab]
url = "https://gitlab.example.com"
# token_file = "/home/operator/.config/ci-skills/gitlab.token"

[kubernetes]
context = "admin-context"
server = "https://api.cluster.example.com:6443"
# kubeconfig = "/home/operator/.kube/config"
```

## Mandatory live gate

Run `access_check.py --target PATH --json --publication` on each intended
execution host. Supply `--revision SHA` for an installed copy without Git
metadata, and `--job-url URL` when verifying a requested job. The receipt
identifies the execution host, time, revision, sources, targets, identities,
and individual results.

- GitHub: authenticate the selected host, read back the identity and exact
  repository. Publication mode also requires repository administration and
  read-back of nonempty required status checks on the protected main branch.
- GitLab: authenticate the exact origin, read back an identity with
  `is_admin: true` and matching host, and read the instance runner API.
  A selected job also requires the job, pipeline, runner, and trace reads.
- Kubernetes: match the context to the exact HTTPS API server with TLS
  verification, read back the effective identity, check administrator
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

The executed code is identified by a content digest of the skill tree, not by a
commit SHA: a SHA cannot be verified where it is claimed, and an installed copy
has no Git metadata at all. A revision is reported `verified` only when it came
from a clean subtree in the repository that tracks this skill; `--revision` is
recorded as a claim, and the invoking project's `CI_COMMIT_SHA` is kept in its
own `consuming_project` field so it can never be mistaken for the skill's.

Required status checks are compared against the set declared as
`github.required_checks` in the target file, by exact equality. Any nonempty
read-back used to pass, so protection requiring only an unrelated check
satisfied the gate; an absent declaration now blocks rather than accepting
anything. The declared set must be a subset of what protection requires, so
extra protection is not a failure.

Each kubeconfig is digested when sources are bound, and the digest is
re-checked before every Kubernetes command. Pinning the filename is not pinning
the target: the server is verified once, then each later command reopens that
mutable path. This is detect-and-block, not an atomic pin -- a file swapped
between the check and the command's own open is still possible -- and it closes
the case that actually happens, a login rewriting the kubeconfig mid-run.

The base gate runs before every collector. The expanded bundle runs in
`access_check.py`, and every report names which one it passed in
`access.profile`, so neither is implied for the other. A collector report also
carries `access`: the identities, credential sources, targets, execution host,
skill digest and a `receipt_sha256` of the gate that authorized it.

`--receipt-out PATH` writes the committable form, with every absolute host path
replaced by a digest token. That is what makes a real receipt publishable: the
captured form names credential locations under the operator's home directory.
`tools/check_live_acceptance.py` compares committed receipts against
`acceptance/expected.toml` and refuses one that is missing, stale, from an
undeclared executor or identity, aimed at different targets, missing a required
live check, carrying an unproven one, or produced by a different skill digest.
The validate workflow runs it unconditionally.

`PASS` requires all selected live checks. Missing, invalid, expired,
wrong-target, or unauthorized credentials block. A mock, file-existence
check, login-status message, dry run, or receipt from another host is not
live acceptance evidence.

Cluster-wide list reads carry their own bound, `collect.LIST_TIMEOUT_SECONDS`.
Measured on one target cluster, a whole-cluster event list was 7.7 MB and
about 20 seconds on its own with up to ten such reads running concurrently;
the default per-command bound reported that healthy cluster as a timeout, and
the gate then read the timeout as a denial.

## Automated gates

The `validate` workflow checks package layout, all installed entrypoints from
an unrelated working directory, workflow/YAML and Markdown syntax, diff
hygiene, secret scanning, Ruff lint and format, and mocked behavior. CI proves
code behavior at its tested commit. The per-host live receipt proves actual
access and resource reads on that host.
